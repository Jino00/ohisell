r"""발주서 PDF 폴더 → 발주 원장 적재 + 품목명 사전 동기화 (계약 §4 S1 · 체인 `발주예측` n=5).

## 왜 «두 조각»인가 (되돌릴 수 없는 설계 제약)

발주서 PDF 원본은 Jino의 Google Drive 동기화 폴더에 있고 **prod 서버는 그 폴더를 못 본다.**
그래서 이 모듈은 PDF를 읽는 쪽과 DB에 쓰는 쪽을 갈라 둔다:

    build_payload(root)              ← Mac에서 (pypdf·Drive 필요, DB 무접촉)
        ↓  JSON 한 덩어리
    ingest_payload(session, payload) ← prod에서 (DB만 필요, pypdf·Drive 불요)

    ingest_orders(session, root)     = 위 둘을 한 번에 (로컬·테스트용)

갈라 두면 ①prod에 pypdf·Drive 의존이 안 생기고 ②페이로드가 **근거 보존물**이 된다(무엇을 심었는지
파일로 남는다) ③재적재가 PDF 재파싱 없이 된다. 이건 게으름이 아니라 원천의 위치가 정한 것이다 —
쿠팡 수집에서 반복 실증된 「Mac은 꺼지면 멈춘다」 제약을 여기서도 그대로 진다.

실행 스크립트는 `backend/scripts/otao_po_export.py`(Mac)·`otao_po_import.py`(prod)다.

## 멱등성의 키는 `serial`이 아니라 **파일 내용 해시**다

같은 `serial`이 여러 파일로 존재하는 것이 **정상**이다(실측: PDF 121개 → 발주서 95건 →
고유 발주번호 **66**, 28개 번호가 파일 둘 이상). 그래서 `serial` unique를 걸면 개정 이력이
통째로 사라진다. 전부 담고 `is_authoritative`로 가른다(D-INV-3).

## 정본 판정 (D-INV-3) — 순위는 이 순서이고, 진 이유를 행에 적어 둔다

    ① ECOUNT 사본        `source_kind='ecount'`
    ② `Revise` 표기 파일
    ③ 그 외에는 파일 mtime이 늦은 것

★**ECOUNT 사본 판별은 파일명 규칙이다** — 실측 26개가 전부 `^[0-9A-Z]{15}\.PDF$`(해시형)이고
2025 폴더 14 + 2026년 폴더 12로 정확히 나뉜다. 사람이 만들어 보낸 원본은 `OHI_Order Sheet_…`
꼴이라 겹치지 않는다.

⚠️**③의 mtime은 근거가 약하다** — ECOUNT 사본 26개는 «내려받은 시각»이 전부 같은 날로 찍혀
있어 mtime이 문서의 나이를 뜻하지 않는다. 그래서 mtime은 **①②로 갈리지 않을 때만** 쓰고,
그렇게 갈린 건은 리포트의 `tie_broken_by_mtime`에 실어 **사람이 볼 수 있게** 남긴다.
정본이 아닌 행에는 `supersede_reason`으로 「무엇에 졌는가」를 적는다(근거 보존).

## 수량이 빈 라인은 **0으로 채우지 않는다**

계약 §2-8이 「데이터 없음 ≠ 0」을 못 박았고 파서도 그래서 `qty=None`·`blank_qty=True`로
돌려준다. 모델 `quantity`가 NOT NULL이라 그 라인은 **원장에 넣지 않고** 리포트의
`blank_qty_lines`에 좌표(serial·code·품목명)와 함께 싣는다.

★스키마를 고쳐 nullable로 만들지 않은 이유는 **비례**다: 전수 1,206라인 중 빈 수량은
**1건**(`20250702-1` / `PGAPIP17`)뿐이라, 컬럼 하나를 「모름」 표현이 가능한 형태로 바꾸는
비용이 그 1건이 주는 값보다 크다. 대신 **조용히 사라지지는 않는다** — 리포트가 항상 그것을
말하고, 건수가 늘면 그때 스키마를 바꾼다.

## 사전 동기화는 사람이 확정한 것을 덮지 않는다

`match_kind='manual'` 행은 Jino가 손으로 정한 것이라 재적재가 갈아엎지 않는다(D-INV-2 규칙 2가
「상품 지식」이라 코드가 못 푸는 자리가 실제로 있다). 나머지는 발주서에서 다시 만든다.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ImportInvoiceLine,
    OtaoItemNameMap,
    OtaoPurchaseOrder,
    OtaoPurchaseOrderLine,
)

from .name_map import build_dictionary, resolve
from .parser import parse_order_pdf

# ECOUNT에서 내려받은 사본의 파일명 규칙 (실측 26/26). 사람이 만든 원본은 `OHI_Order Sheet_…`.
ECOUNT_FILENAME = re.compile(r"^[0-9A-Z]{15}\.PDF$", re.I)
REVISE = re.compile(r"revise", re.I)
# `20260812-1` → 2026-08-12. 발주일이 발주번호 앞 8자리에 박혀 있다.
SERIAL_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})-")


@dataclass
class IngestReport:
    files_scanned: int = 0
    purchase_orders: int = 0  # 발주서로 판정된 파일 수
    non_purchase_orders: int = 0  # Packing List·Invoice 등
    inserted: int = 0
    unchanged: int = 0  # 같은 해시가 이미 있다 (멱등)
    moved: int = 0  # 내용은 같은데 경로가 바뀌었다
    lines_inserted: int = 0
    serials: int = 0
    authoritative: int = 0
    superseded: int = 0
    # ★조용히 사라지면 안 되는 것들 — 전부 좌표와 함께 싣는다.
    blank_qty_lines: list[dict] = field(default_factory=list)
    dropped_lines: list[dict] = field(default_factory=list)
    qty_mismatch: list[dict] = field(default_factory=list)
    bad_serial_dates: list[str] = field(default_factory=list)
    tie_broken_by_mtime: list[str] = field(default_factory=list)
    # 사전 동기화 결과
    map_total: int = 0
    map_resolved: int = 0
    map_manual_kept: int = 0
    map_unresolved: list[str] = field(default_factory=list)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _order_date(serial: str) -> date | None:
    """발주번호 앞 8자리 = 발주일. 형식이 아니면 **지어내지 않고** None을 준다."""
    m = SERIAL_DATE.match(serial)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _source_kind(filename: str) -> str:
    return "ecount" if ECOUNT_FILENAME.match(filename) else "local"


PAYLOAD_VERSION = 1


def build_payload(root: str) -> dict:
    """★PDF 쪽 (Mac). 폴더를 훑어 **DB 없이** 심을 재료를 만든다. pypdf·Drive가 여기만 필요하다.

    파일 하나당 `{rel, sha256, mtime, source_kind, parsed}`. 비발주서(Packing List·Invoice)는
    `parsed=None`으로 **세되 버리지 않는다** — 121 중 26이 그것이고, 그 수가 리포트에서
    사라지면 「폴더에 뭐가 있었나」를 되짚을 수 없다.
    """
    files: list[dict] = []
    for dirpath, _dirs, names in os.walk(root):
        for fname in sorted(names):
            if not fname.lower().endswith(".pdf"):
                continue
            path = os.path.join(dirpath, fname)
            files.append({
                "rel": os.path.relpath(path, root),
                "sha256": _sha256(path),
                "mtime": os.path.getmtime(path),
                "source_kind": _source_kind(fname),
                "parsed": parse_order_pdf(path),
            })
    return {"version": PAYLOAD_VERSION, "root": root, "files": files}


def ingest_payload(
    session: Session, payload: dict, *, report: IngestReport | None = None
) -> IngestReport:
    """★DB 쪽 (prod). 페이로드를 원장에 넣고 정본을 판정한다. **커밋은 호출자 몫이다.**

    같은 페이로드를 여러 번 먹여도 결과가 같다(`content_sha256` unique). 파일이 폴더 안에서
    옮겨졌으면 경로만 갱신하고 라인은 다시 만들지 않는다 — 내용이 같으니 라인도 같다.
    """
    rep = report or IngestReport()
    if payload.get("version") != PAYLOAD_VERSION:
        # 조용히 먹지 않는다 — 형식이 바뀐 페이로드를 옛 코드가 먹으면 원장이 반쯤 채워진다.
        raise ValueError(
            f"페이로드 version={payload.get('version')} — 이 코드는 {PAYLOAD_VERSION}만 먹는다"
        )

    existing: dict[str, OtaoPurchaseOrder] = {
        po.content_sha256: po for po in session.scalars(select(OtaoPurchaseOrder)).all()
    }
    mtimes: dict[int, float] = {}

    for f in payload["files"]:
        rep.files_scanned += 1
        parsed = f["parsed"]
        if parsed is None:
            rep.non_purchase_orders += 1
            continue
        rep.purchase_orders += 1

        rel, sha, serial = f["rel"], f["sha256"], parsed["serial"]

        if parsed["header_qty"] is not None and parsed["header_qty"] != parsed["line_qty_sum"]:
            # 검산 실패를 **삼키지 않는다** — 파싱이 틀렸거나 문서가 특이하다는 신호다.
            rep.qty_mismatch.append({
                "serial": serial,
                "file": rel,
                "header_qty": parsed["header_qty"],
                "line_qty_sum": parsed["line_qty_sum"],
            })

        po = existing.get(sha)
        if po is not None:
            if po.source_file != rel:
                po.source_file = rel
                rep.moved += 1
            else:
                rep.unchanged += 1
            mtimes[po.id] = f["mtime"]
            continue

        od = _order_date(serial)
        if od is None:
            rep.bad_serial_dates.append(f"{serial} ({rel})")

        po = OtaoPurchaseOrder(
            serial=serial,
            order_date=od,
            source_kind=f["source_kind"],
            source_file=rel,
            content_sha256=sha,
            is_authoritative=False,  # 아래 `_mark_authoritative`가 정한다
            header_qty=parsed["header_qty"],
            total_amount=parsed["header_amount"],
            currency=next((line["currency"] for line in parsed["lines"] if line["currency"]), None),
        )
        session.add(po)
        session.flush()
        existing[sha] = po
        mtimes[po.id] = f["mtime"]
        rep.inserted += 1

        seq = 0
        for line in parsed["lines"]:
            if line["qty"] is None:
                # 0으로 채우지 않는다(계약 §2-8). 대신 좌표와 함께 리포트에 싣는다.
                rep.blank_qty_lines.append({
                    "serial": serial,
                    "file": rel,
                    "code": line["code"],
                    "name": line["name_en"] or line["name_ko"],
                })
                continue
            seq += 1
            session.add(
                OtaoPurchaseOrderLine(
                    order_id=po.id,
                    seq=seq,
                    product_code=line["code"],
                    name_ko=line["name_ko"] or None,
                    name_en=line["name_en"],
                    quantity=line["qty"],
                    currency=line["currency"],
                    unit_price=line["unit_price"],
                    amount=line["amount"],
                )
            )
            rep.lines_inserted += 1

        for d in parsed["dropped"]:
            rep.dropped_lines.append({"serial": serial, "file": rel, **d})

    session.flush()
    _mark_authoritative(session, mtimes, rep)
    return rep


def ingest_orders(session: Session, root: str, *, report: IngestReport | None = None) -> IngestReport:
    """폴더 → 원장 한 번에. **로컬·테스트용**이다(prod는 페이로드 경유 — 모듈 docstring)."""
    return ingest_payload(session, build_payload(root), report=report)


def _mark_authoritative(session: Session, mtimes: dict[int, float], rep: IngestReport) -> None:
    """serial마다 정본 하나를 고른다 (D-INV-3). 나머지엔 «진 이유»를 적는다."""
    by_serial: dict[str, list[OtaoPurchaseOrder]] = {}
    for po in session.scalars(select(OtaoPurchaseOrder)).all():
        by_serial.setdefault(po.serial, []).append(po)

    rep.serials = len(by_serial)
    for serial, group in by_serial.items():
        def rank(po: OtaoPurchaseOrder) -> tuple:
            return (
                1 if po.source_kind == "ecount" else 0,
                1 if REVISE.search(po.source_file) else 0,
                mtimes.get(po.id, 0.0),
            )

        ranked = sorted(group, key=rank, reverse=True)
        winner = ranked[0]
        if len(group) > 1:
            runner = ranked[1]
            if rank(winner)[:2] == rank(runner)[:2]:
                # ①②로 안 갈리고 mtime만으로 정해졌다 — mtime은 문서의 나이가 아니다(모듈
                # docstring). 사람이 볼 수 있게 남긴다.
                rep.tie_broken_by_mtime.append(serial)

        for po in group:
            po.is_authoritative = po is winner
            if po is winner:
                po.supersede_reason = None
                rep.authoritative += 1
            else:
                why = (
                    "ECOUNT 사본이 정본"
                    if winner.source_kind == "ecount" and po.source_kind != "ecount"
                    else "Revise 판본이 정본"
                    if REVISE.search(winner.source_file) and not REVISE.search(po.source_file)
                    else "같은 발주번호의 더 늦은 파일이 정본"
                )
                po.supersede_reason = f"{why}: {winner.source_file}"
                rep.superseded += 1
    session.flush()


def sync_name_map(session: Session, *, report: IngestReport | None = None) -> IngestReport:
    """정본 발주서 라인으로 사전을 만들고 통관 원장 품목명을 대조해 저장한다.

    ★사람이 확정한 행(`match_kind='manual'`)은 **덮지 않는다.** 규칙 2(공용 표기 ≡ 단일 표기)는
    상품 지식이라 코드가 못 풀고, 그 자리를 사람이 메우면 그게 정본이기 때문이다.
    """
    rep = report or IngestReport()

    po_lines = [
        {"code": code, "name_en": name_en, "serial": serial}
        for code, name_en, serial in session.execute(
            select(
                OtaoPurchaseOrderLine.product_code,
                OtaoPurchaseOrderLine.name_en,
                OtaoPurchaseOrder.serial,
            )
            .join(OtaoPurchaseOrder, OtaoPurchaseOrderLine.order_id == OtaoPurchaseOrder.id)
            .where(OtaoPurchaseOrder.is_authoritative.is_(True))
        )
    ]
    dictionary = build_dictionary(po_lines)

    raw_names = sorted(
        {
            n
            for (n,) in session.execute(
                select(ImportInvoiceLine.item_name)
                .where(ImportInvoiceLine.line_type == "product")
                .distinct()
            )
            if n
        }
    )
    rep.map_total = len(raw_names)

    rows = {m.raw_name: m for m in session.scalars(select(OtaoItemNameMap)).all()}
    for entry in resolve(raw_names, dictionary):
        row = rows.get(entry.raw_name)
        if row is not None and row.match_kind == "manual":
            rep.map_manual_kept += 1
            if row.product_code:
                rep.map_resolved += 1
            continue
        if row is None:
            row = OtaoItemNameMap(raw_name=entry.raw_name)
            session.add(row)
            rows[entry.raw_name] = row
        row.product_code = entry.product_code
        row.match_kind = entry.match_kind
        row.evidence = entry.evidence
        row.note = entry.note
        if entry.product_code:
            rep.map_resolved += 1
        else:
            rep.map_unresolved.append(entry.raw_name)

    session.flush()
    return rep
