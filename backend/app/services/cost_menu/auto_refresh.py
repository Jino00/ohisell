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
from app.services.cost_menu import price_rule as PR

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
        # ★연결 «전»의 채택 단가 — 계약 §4-③의 `old→new`가 여기서만 만들어진다
        #   (적대 리뷰 1R P1-3: 초판은 이 값을 채우는 코드가 **아예 없어서** 필드는 DB·API·
        #   프론트 타입 어디에나 있는데 항상 `None`이었고, 화면 분기가 `old ? "old → new" : "new"`라
        #   **「178.78 → 190.82」가 원리적으로 절대 안 떴다.** 「불변이면 old=new 멱등」도
        #   원리적으로 불가능했다. 변이가 아니라 배포될 코드 자체의 결함이었다).
        old_ex_vat = None
        if material is not None:
            try:
                old_ex_vat = PR.choose_price(
                    list(material.prices), PR.read_rule(db)
                ).price
                old_ex_vat = old_ex_vat.unit_price_ex_vat if old_ex_vat else None
            except Exception:  # noqa: BLE001
                # 옛 값을 못 읽는 것이 «연결 자체»를 막지는 않는다 — 그 사유는 아래 링크
                # 시도가 같은 예외로 다시 만나 FAILED 행에 담긴다.
                old_ex_vat = None

        try:
            # ★★SAVEPOINT — 적대 리뷰 1R P1-2.
            #   초판은 `link_ledger_line`이 단가 행을 `flush`한 «뒤» `_propagate`(표준원가
            #   재계산)를 부르는데, `_propagate`에서 예외가 나면 **이미 flush된 단가 행이
            #   롤백되지 않았다.** `run()`은 예외를 밖으로 안 던지므로 호출자가 그대로 커밋했고,
            #   결과는 **「failed」로 기록된 라인의 단가가 실제로는 영구히 커밋되는 것**이었다.
            #   게다가 `_candidate_lines`가 «이미 링크된» 라인을 후보에서 빼므로 그 라인은
            #   다음 회전부터 재검사 대상에서도 빠져 — 재시도도 안 되고 큐에도 안 걸린다.
            #   표준원가는 낡은 채로 남는데 어느 표면도 그걸 말하지 않는다.
            #   ⇒ 라인 하나를 **원자 단위**로 만든다: 실패하면 그 라인의 쓰기가 통째로 사라져
            #   라인은 미연결로 남고, 다음 회전이 다시 시도하며, MAX_ATTEMPTS가 실제로 건다.
            with db.begin_nested():
                price = link_ledger_line(
                    db,
                    material_id,
                    line.id,
                    note=f"자동 연결 — {trigger} 회전 #{run_row.id}",
                )
                price_id = price.id
            db.add(
                CostAutoRefreshEntry(
                    outcome=OUTCOME_LINKED,
                    material_id=material_id,
                    material_name=material.name if material else None,
                    price_id=price_id,
                    old_price_ex_vat=old_ex_vat,
                    **common,
                )
            )
            result.updated += 1
            touched_materials.add(material_id)
        except Exception as exc:  # noqa: BLE001 — 사유를 «행»으로 남기는 것이 이 except의 일이다
            # ★한 라인의 실패가 회전을 죽이지 않는다. 그러나 조용히 넘기지도 않는다.
            #   savepoint가 이미 되감겼으므로 세션은 깨끗하고, 아래 기록 쓰기는 안전하다.
            db.add(
                CostAutoRefreshEntry(
                    outcome=OUTCOME_FAILED,
                    material_id=material_id,
                    material_name=material.name if material else None,
                    old_price_ex_vat=old_ex_vat,
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
    """「연결 대기」 큐 — **지금 사람을 기다리는 라인**(합격 ⑤의 표면).

    ★★**«회전 기록»이 아니라 «현재 상태»에서 만든다** (적대 리뷰 1R P1-1).

    초판은 «가장 최근 1회전»의 `queued` 항목만 냈다. 그런데 로트가 확정될 때마다
    `confirm_shipment`가 **그 로트 하나만 스캔하는 이벤트 회전**을 돌리고(§7-3에서 이벤트를
    «주» 방아쇠로 채택했으므로 정상 업무 흐름에서 매번 일어난다), 그 좁은 회전이 최신이 되는
    순간 큐 화면은 **그 로트에서 새로 발견된 것만** 보여줬다 — 여전히 미연결인 다른 로트의
    항목들이 화면에서 통째로 사라졌다. 「할 일 목록」이 조용히 짧아지는 결함이다.

    초판 docstring은 반대 방향 위험(«옛 대기 항목이 이미 연결됐는데도 계속 뜬다»)만 막고
    이 방향을 놓쳤다. ⇒ **양쪽을 한 번에 없애는 방법은 로그를 안 보는 것이다**: 큐는
    「지금 무엇이 미연결인가」라는 **상태 질문**이지 「지난 회전에 무엇을 봤나」가 아니다.

    사유는 회전 기록에서 «가져올 수 있으면» 가져오고(사람이 읽을 맥락), 없으면 지금 상태로
    다시 만든다 — 사유 없는 대기 항목은 화면에서 침묵과 같다(§2-6).
    """

    pairs = known_pairs(db)
    lines = _candidate_lines(db)  # 확정·부자재·단가 있음·미연결 = 지금 후보 전건
    if not lines:
        return []

    # 회전 기록의 사유를 라인별로 «최근 것»만 집어 온다(있으면 그 문장을 그대로 쓴다).
    line_ids = [ln.id for ln in lines]
    notes: dict[int, CostAutoRefreshEntry] = {}
    for e in (
        db.query(CostAutoRefreshEntry)
        .filter(
            CostAutoRefreshEntry.import_invoice_line_id.in_(line_ids),
            CostAutoRefreshEntry.outcome.in_((OUTCOME_QUEUED, OUTCOME_FAILED)),
        )
        .order_by(CostAutoRefreshEntry.id)
        .all()
    ):
        notes[e.import_invoice_line_id] = e  # 뒤에 오는 것이 최신

    out: list[dict] = []
    for ln in lines:
        name = (ln.item_name or "").strip()
        material_id = pairs.get(name)
        prev = notes.get(ln.id)
        ship = ln.shipment
        # ★옛 사유가 «지금»과 어긋나면 버린다 (적대 리뷰 2R P2). 사람이 그 사이 같은 이름을
        #   다른 라인에서 연결해 짝이 «생긴» 경우, 옛 사유는 아직 「연결된 적 없다」라고
        #   말한다 — 그 문장은 이제 거짓이고, 사람은 「내가 방금 연결했는데?」에서 멈춘다.
        #   판정 기준은 **그 사유가 겨눴던 종과 지금 종이 같은가**다. 그래서 MAX_ATTEMPTS로
        #   고정된 사유(종이 같다)는 그대로 보존된다 — 그건 여전히 참이고 가장 중요한 사유다.
        if prev is not None and prev.material_id != material_id:
            prev = None
        if prev is not None:
            payload = entry_payload(prev)
        else:
            # ★아직 어느 회전도 이 라인을 못 본 경우 — 「검사 안 함」이 아니라 「곧 검사될
            #   것」이다. 그래도 사람에겐 지금 보여야 한다(다음 회전까지 숨기면 그게 침묵이다).
            payload = {
                "id": None,
                "run_id": None,
                "outcome": OUTCOME_QUEUED,
                "material_id": material_id,
                "material_name": None,
                "price_id": None,
                "import_invoice_line_id": ln.id,
                "hbl_no": ship.hbl_no if ship is not None else None,
                "item_name": ln.item_name,
                "old_price_ex_vat": None,
                "new_price_ex_vat": _d(ln.unit_cost_ex_vat),
                "message": None,
                "created_at": None,
            }
        if not payload.get("message"):
            payload["message"] = (
                f"「{name}」은 아직 어느 부자재에도 연결된 적이 없다 — "
                "첫 연결은 사람이 한다(계약 §7-4)."
                if material_id is None
                else "자동 연결을 아직 시도하지 않았다 — 다음 회전이 잇는다."
            )
        # 좌표는 «지금»의 것으로 덮는다 — 옛 기록의 HBL이 남아 사람을 딴 데로 보내면 안 된다.
        payload["import_invoice_line_id"] = ln.id
        payload["hbl_no"] = ship.hbl_no if ship is not None else None
        payload["item_name"] = ln.item_name
        payload["material_id"] = material_id
        out.append(payload)
    return out
