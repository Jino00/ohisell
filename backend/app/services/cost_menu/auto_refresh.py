"""단가 **자동 갱신** — 사람이 한 번 연결한 짝의 «반복»만 한다 (D-CPP-60 갈래② · §7-3·§7-4).

## 왜 생겼나 (실측 2026-08-26)

단가가 바뀌는 경로는 4개였고 **전부 사람이 눌러야 했다**(`link_ledger_line`·
`refresh_ledger_price`·`add_manual_price`·`adopt_excel_prices`). 스케줄러에 원가 메뉴 참조는
grep 0건. 그래서 새 수입 로트가 `confirmed`로 들어와도 사람이 「연결」을 누르기 전엔
표준원가가 옛 값에 머물렀다. 방치의 크기도 실측됐다 — `cleaning kit` 7/23 로트 178.78원 vs
8/18 로트 190.82원(+6.7%), 로트별 `fx_rate` 207.51~226.66(엑셀 고정 200 대비 +9%).

## 불변식 — 이 모듈이 절대 안 하는 것 (계약 §7-4 · §3 금지선)

★**사람 연결 1회 없이는 짝이 생기지 않는다.** 자동이 가져가는 것은 「연결」 버튼의 **반복
클릭**뿐이고, 판단(이 원장 라인이 이 부자재인가 · 신뢰할 값인가 · 새 종을 승인할까)은 전부
사람에 남는다. 구체적으로:

- **매칭 키는 발명하지 않는다** — 기존 `cost_material_price.linked_item_name`(사람이 연결할 때
  저장된 품목명)이 유일한 키다. 품명 «유사도»로 첫 연결을 만드는 것은 버린 대안이다:
  첫 매칭은 판단이고, 오연결 1건이 표준원가 여러 건에 조용히 전파된다.
- **`unconfirmed` 종을 승격하지 않는다** — 종 승인은 사람 몫이다.
- **새 종을 만들지 않는다.**
- 짝을 못 찾은 라인은 **큐로 올린다**(`outcome='queued'`) — 자동이 처리한 척하지 않는다.

## 침묵 금지 (계약 §2-6)

회전마다 `CostAutoRefreshRun` 한 행이 남는다 — **`updated=0`이어도 남는다.** 그 행이 매일
쌓이는 것이 「자동이 살아 있다」의 유일한 증거다. 아무것도 안 남기면 «돌았는데 바뀔 게
없었다»와 «죽었다»가 화면에서 똑같이 보인다.

## 실패

라인 하나가 실패해도 회전은 계속한다(`outcome='failed'` + 사유). 무한 재시도는 하지 않는다 —
다음 회전이 다시 시도하고, `MAX_ATTEMPTS`회 연속 실패한 라인은 큐에 고정돼 사람을 기다린다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session, selectinload

from app.models import (
    CostAutoRefreshEntry,
    CostAutoRefreshRun,
    CostMaterial,
    CostMaterialPrice,
    ImportInvoiceLine,
    ImportShipment,
)

log = logging.getLogger(__name__)

#: 같은 라인을 몇 회전까지 다시 시도하나. 넘으면 큐에 고정하고 사람을 기다린다(§3 무한 재시도 금지).
MAX_ATTEMPTS = 3

TRIGGER_EVENT = "event"
TRIGGER_CRON = "cron"
TRIGGER_MANUAL = "manual"

OUTCOME_LINKED = "linked"
OUTCOME_UNCHANGED = "unchanged"
OUTCOME_FAILED = "failed"
OUTCOME_QUEUED = "queued"


@dataclass
class RunResult:
    run_id: Optional[int]
    trigger: str
    checked: int = 0
    updated: int = 0
    failed: int = 0
    queued: int = 0
    entries: list[dict] = field(default_factory=list)


# ──────────────────────────────────────────────
# 매칭 키 — 사람이 만든 연결에서만 «유도»한다
# ──────────────────────────────────────────────
def known_pairs(db: Session) -> dict[str, int]:
    """`linked_item_name` → `material_id`. **사람이 연결한 것만** 담긴다.

    ★같은 품목명이 서로 다른 종에 연결돼 있으면 그 이름은 **키에서 뺀다.** 둘 중 하나를
    고르는 것이 곧 판단이고, 판단은 자동의 몫이 아니다 — 그런 라인은 큐로 간다.

    ★`source='ledger'`만 본다. `manual` 행에는 `linked_item_name`이 없다(원장 라인이 없으니
    당연하다) — 있어도 그건 사람이 원장을 보고 «연결»한 사실이 아니다.
    """

    rows = (
        db.query(CostMaterialPrice)
        .filter(
            CostMaterialPrice.source == "ledger",
            CostMaterialPrice.linked_item_name.isnot(None),
        )
        .all()
    )
    by_name: dict[str, set[int]] = {}
    for p in rows:
        name = (p.linked_item_name or "").strip()
        if not name:
            continue
        by_name.setdefault(name, set()).add(p.material_id)

    out: dict[str, int] = {}
    for name, ids in by_name.items():
        if len(ids) == 1:
            out[name] = next(iter(ids))
        else:
            # 모호하다 — 자동이 고르지 않는다. 이 이름의 라인은 큐로 떨어진다.
            log.info(
                "[cost-auto] 품목명 「%s」이 종 %s에 갈려 있다 — 자동 매칭에서 뺀다",
                name,
                sorted(ids),
            )
    return out


def _candidate_lines(db: Session, shipment_id: int | None = None):
    """자동이 «볼» 라인 = 확정 수입건의 부자재 라인 중 **아직 어느 종에도 안 붙은** 것.

    ★`shipment_id`가 오면 그 로트만(이벤트 방아쇠), 없으면 전건(크론 sweep).
    ★단가가 비어 있는 라인은 여기서 거른다 — 0으로 채우지 않는다(계약 §2-7 계승).
    """

    linked_ids = {
        row[0]
        for row in db.query(CostMaterialPrice.import_invoice_line_id)
        .filter(CostMaterialPrice.import_invoice_line_id.isnot(None))
        .all()
    }
    q = (
        db.query(ImportInvoiceLine)
        .options(selectinload(ImportInvoiceLine.shipment))
        .join(ImportShipment, ImportInvoiceLine.shipment_id == ImportShipment.id)
        .filter(
            ImportShipment.status == "confirmed",
            ImportInvoiceLine.line_type == "material",
            ImportInvoiceLine.unit_cost_ex_vat.isnot(None),
            ImportInvoiceLine.unit_cost_inc_vat.isnot(None),
        )
    )
    if shipment_id is not None:
        q = q.filter(ImportInvoiceLine.shipment_id == shipment_id)
    return [ln for ln in q.all() if ln.id not in linked_ids]


def _attempts(db: Session, line_id: int) -> int:
    """이 라인이 지금까지 몇 번 실패했나 — `MAX_ATTEMPTS` 판정의 근거."""

    return (
        db.query(CostAutoRefreshEntry)
        .filter(
            CostAutoRefreshEntry.import_invoice_line_id == line_id,
            CostAutoRefreshEntry.outcome == OUTCOME_FAILED,
        )
        .count()
    )


# ──────────────────────────────────────────────
# 회전
# ──────────────────────────────────────────────
def run(
    db: Session,
    *,
    trigger: str = TRIGGER_CRON,
    shipment_id: int | None = None,
) -> RunResult:
    """자동 갱신 1회전. **예외를 밖으로 던지지 않는다** — 회전 실패가 곧 방아쇠(로트 확정·크론)의
    실패가 되면 안 되기 때문이다. 대신 실패는 전부 행으로 남는다.

    ★커밋하지 않는다 — 호출자의 트랜잭션 경계를 존중한다(라우터·스케줄러가 커밋한다).
    """

    run_row = CostAutoRefreshRun(trigger=trigger, started_at=datetime.now())
    db.add(run_row)
    db.flush()

    result = RunResult(run_id=run_row.id, trigger=trigger)
    pairs = known_pairs(db)

    # 지연 임포트 — `materials`가 이 모듈을 모르는 편이 층 구조에 맞다(`_propagate` 선례).
    from app.services.cost_menu.materials import link_ledger_line

    touched_materials: set[int] = set()

    for line in _candidate_lines(db, shipment_id):
        result.checked += 1
        ship = line.shipment
        hbl = ship.hbl_no if ship is not None else None
        name = (line.item_name or "").strip()
        material_id = pairs.get(name)

        common = dict(
            run_id=run_row.id,
            import_invoice_line_id=line.id,
            hbl_no=hbl,
            item_name=line.item_name,
            new_price_ex_vat=line.unit_cost_ex_vat,
        )

        if material_id is None:
            # ★신규 짝 — 자동이 만들지 않는다(§7-4 불변식). 사람에게 올린다.
            db.add(
                CostAutoRefreshEntry(
                    outcome=OUTCOME_QUEUED,
                    message=(
                        f"「{name}」은 아직 어느 부자재에도 연결된 적이 없다 — "
                        "첫 연결은 사람이 한다(계약 §7-4)."
                    ),
                    **common,
                )
            )
            result.queued += 1
            continue

        if _attempts(db, line.id) >= MAX_ATTEMPTS:
            db.add(
                CostAutoRefreshEntry(
                    outcome=OUTCOME_QUEUED,
                    material_id=material_id,
                    message=(
                        f"자동 연결이 {MAX_ATTEMPTS}회 실패했다 — 큐에 고정하고 사람을 "
                        "기다린다(§3 무한 재시도 금지)."
                    ),
                    **common,
                )
            )
            result.queued += 1
            continue

        material = db.get(CostMaterial, material_id)
        try:
            price = link_ledger_line(
                db,
                material_id,
                line.id,
                note=f"자동 연결 — {trigger} 회전 #{run_row.id}",
            )
            db.add(
                CostAutoRefreshEntry(
                    outcome=OUTCOME_LINKED,
                    material_id=material_id,
                    material_name=material.name if material else None,
                    price_id=price.id,
                    **common,
                )
            )
            result.updated += 1
            touched_materials.add(material_id)
        except Exception as exc:  # noqa: BLE001 — 사유를 «행»으로 남기는 것이 이 except의 일이다
            # ★한 라인의 실패가 회전을 죽이지 않는다. 그러나 조용히 넘기지도 않는다.
            db.add(
                CostAutoRefreshEntry(
                    outcome=OUTCOME_FAILED,
                    material_id=material_id,
                    material_name=material.name if material else None,
                    message=f"{type(exc).__name__}: {exc}",
                    **common,
                )
            )
            result.failed += 1
            log.warning(
                "[cost-auto] 라인 %s 자동 연결 실패: %s", line.id, exc, exc_info=False
            )

    run_row.checked = result.checked
    run_row.updated = result.updated
    run_row.failed = result.failed
    run_row.queued = result.queued
    run_row.finished_at = datetime.now()
    db.flush()

    log.info(
        "[cost-auto] %s 회전 #%s — 검사 %s · 갱신 %s · 실패 %s · 대기 %s",
        trigger,
        run_row.id,
        result.checked,
        result.updated,
        result.failed,
        result.queued,
    )
    return result


# ──────────────────────────────────────────────
# 화면 재료
# ──────────────────────────────────────────────
def _dt(v) -> str | None:
    return None if v is None else v.isoformat()


def _d(v: Optional[Decimal]) -> str | None:
    return None if v is None else str(v)


def entry_payload(e: CostAutoRefreshEntry) -> dict:
    return {
        "id": e.id,
        "run_id": e.run_id,
        "outcome": e.outcome,
        "material_id": e.material_id,
        "material_name": e.material_name,
        "price_id": e.price_id,
        "import_invoice_line_id": e.import_invoice_line_id,
        "hbl_no": e.hbl_no,
        "item_name": e.item_name,
        "old_price_ex_vat": _d(e.old_price_ex_vat),
        "new_price_ex_vat": _d(e.new_price_ex_vat),
        "message": e.message,
        "created_at": _dt(e.created_at),
    }


def run_payload(r: CostAutoRefreshRun, entries: list[CostAutoRefreshEntry]) -> dict:
    return {
        "id": r.id,
        "trigger": r.trigger,
        "started_at": _dt(r.started_at),
        "finished_at": _dt(r.finished_at),
        "checked": r.checked,
        "updated": r.updated,
        "failed": r.failed,
        "queued": r.queued,
        "note": r.note,
        "entries": [entry_payload(e) for e in entries],
    }


def recent_runs(db: Session, limit: int = 20) -> list[dict]:
    """최근 회전 — **`updated=0`인 회전도 실린다**(합격 ④의 표면).

    화면이 「최근 검사: <시각> · 검사 N종 · 갱신 n건 · 실패 m건」을 그리는 재료다.
    이 목록이 비어 있으면 그건 「바뀔 게 없었다」가 아니라 **「한 번도 안 돌았다」**이고,
    화면은 그 둘을 구별해 말해야 한다.
    """

    runs = (
        db.query(CostAutoRefreshRun)
        .order_by(CostAutoRefreshRun.started_at.desc(), CostAutoRefreshRun.id.desc())
        .limit(limit)
        .all()
    )
    if not runs:
        return []
    ids = [r.id for r in runs]
    entries: dict[int, list[CostAutoRefreshEntry]] = {}
    for e in (
        db.query(CostAutoRefreshEntry)
        .filter(CostAutoRefreshEntry.run_id.in_(ids))
        .order_by(CostAutoRefreshEntry.id)
        .all()
    ):
        entries.setdefault(e.run_id, []).append(e)
    return [run_payload(r, entries.get(r.id, [])) for r in runs]


def pending_queue(db: Session) -> list[dict]:
    """「연결 대기」 큐 — 자동이 **안 건드리고 사람에게 올린** 라인들(합격 ⑤의 표면).

    ★최신 회전의 `queued` 항목만 낸다. 옛 회전의 대기 항목까지 합치면 이미 사람이 연결한
    라인이 계속 대기로 보인다 — 「할 일 목록」이 거짓말을 하면 아무도 안 본다.
    ★그리고 지금도 정말 미연결인지 **다시 확인한다** — 회전 이후 사람이 연결했을 수 있다.
    """

    latest = (
        db.query(CostAutoRefreshRun)
        .order_by(CostAutoRefreshRun.started_at.desc(), CostAutoRefreshRun.id.desc())
        .first()
    )
    if latest is None:
        return []
    rows = (
        db.query(CostAutoRefreshEntry)
        .filter(
            CostAutoRefreshEntry.run_id == latest.id,
            CostAutoRefreshEntry.outcome == OUTCOME_QUEUED,
        )
        .order_by(CostAutoRefreshEntry.id)
        .all()
    )
    if not rows:
        return []
    line_ids = [e.import_invoice_line_id for e in rows if e.import_invoice_line_id]
    still_linked = {
        row[0]
        for row in db.query(CostMaterialPrice.import_invoice_line_id)
        .filter(CostMaterialPrice.import_invoice_line_id.in_(line_ids))
        .all()
    }
    return [
        entry_payload(e)
        for e in rows
        if e.import_invoice_line_id not in still_linked
    ]
