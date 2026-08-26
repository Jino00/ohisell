"""발주서 폴더 → 원장 적재 + 사전 동기화 회귀 (계약 §4 S1 · 체인 `발주예측` n=5).

## 무엇을 재는가

`ingest.py`가 지켜야 하는 약속은 넷이고, 넷 다 **조용히 깨질 수 있는** 종류다:

1. **멱등** — 같은 폴더를 두 번 돌려도 원장이 두 배가 되지 않는다. 키는 `serial`이 아니라
   **파일 내용 해시**다(같은 serial이 여러 파일인 것이 정상 — 실측 66번호 ↔ 95파일).
2. **정본 판정(D-INV-3)** — ECOUNT 사본 > `Revise` > 늦은 파일. 틀리면 개정 전 수량이
   집계에 섞여 **발주 누계가 부풀려진다**(실측: `20260107-2` 5,700 vs 1,300).
3. **빈 수량을 0으로 채우지 않는다** — 계약 §2-8. 대신 리포트가 좌표와 함께 말한다.
   조용히 사라지면 그 라인은 아무도 다시 못 찾는다.
4. **사람이 확정한 매핑을 재적재가 덮지 않는다** — 규칙 2(공용 표기 ≡ 단일 표기)는 상품
   지식이라 코드가 못 푼다. 그 자리를 사람이 메우면 그게 정본이다.

## pypdf 없이 도는 이유

`ingest_orders`는 PDF I/O를 `parser.parse_order_pdf` 한 곳에만 두었다. 테스트는 그것만
monkeypatch하고 **파일은 진짜로 만든다** — 해시·mtime·경로 계산은 실제 파일시스템 동작이라
가짜로 바꾸면 멱등성 단언이 의미를 잃는다.
"""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    ImportInvoiceLine,
    ImportShipment,
    OtaoItemNameMap,
    OtaoPurchaseOrder,
    OtaoPurchaseOrderLine,
)
from app.services.otao_po import ingest as I


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Testing = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with Testing() as s:
        yield s


def _doc(serial: str, lines: list[dict], *, header_qty: int | None = None) -> dict:
    qty_sum = sum(line["qty"] for line in lines if line["qty"] is not None)
    return {
        "serial": serial,
        "header_qty": qty_sum if header_qty is None else header_qty,
        "header_amount": None,
        "lines": [
            {
                "serial": serial,
                "code": line["code"],
                "name_ko": line.get("name_ko", "강화유리"),
                "name_en": line.get("name_en"),
                "qty": line["qty"],
                "currency": "CNY",
                "unit_price": 1.0,
                "amount": line["qty"],
                "blank_qty": line["qty"] is None,
            }
            for line in lines
        ],
        "dropped": [],
        "line_qty_sum": qty_sum,
        "line_amount_sum": qty_sum,
        "path": None,
    }


@pytest.fixture()
def folder(tmp_path, monkeypatch):
    """`{상대경로: 파싱결과}`를 등록하면 그 경로에 진짜 파일이 생기고 파서가 그 값을 준다."""
    docs: dict[str, dict | None] = {}

    def put(rel: str, parsed: dict | None, *, content: str | None = None) -> str:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        # 내용이 같으면 해시가 같다 — 멱등성 단언이 여기에 걸린다.
        path.write_text(content if content is not None else rel, encoding="utf-8")
        docs[str(path)] = parsed
        return str(path)

    monkeypatch.setattr(I, "parse_order_pdf", lambda p: docs.get(p))
    return str(tmp_path), put


def test_ingest_is_idempotent_on_file_content(session, folder):
    """같은 폴더를 두 번 돌려도 원장이 두 배가 되지 않는다."""
    root, put = folder
    put("2026년/OHI_Order Sheet_260812.pdf", _doc("20260812-1", [{"code": "GAPIP15PR", "qty": 500}]))

    first = I.ingest_orders(session, root)
    second = I.ingest_orders(session, root)

    assert (first.inserted, first.lines_inserted) == (1, 1)
    assert (second.inserted, second.unchanged) == (0, 1)
    assert session.query(OtaoPurchaseOrder).count() == 1
    assert session.query(OtaoPurchaseOrderLine).count() == 1


def test_moved_file_updates_path_without_duplicating(session, folder):
    """폴더 안에서 파일이 옮겨졌을 뿐이면 경로만 갱신한다 — 내용이 같으니 라인도 같다."""
    root, put = folder
    put("OHI_Order Sheet_260812.pdf", _doc("20260812-1", [{"code": "GAPIP15PR", "qty": 500}]), content="same")
    I.ingest_orders(session, root)
    os.remove(os.path.join(root, "OHI_Order Sheet_260812.pdf"))
    put("2026년/OHI_Order Sheet_260812.pdf", _doc("20260812-1", [{"code": "GAPIP15PR", "qty": 500}]), content="same")

    rep = I.ingest_orders(session, root)
    assert (rep.inserted, rep.moved) == (0, 1)
    assert session.query(OtaoPurchaseOrder).count() == 1
    (po,) = session.scalars(select(OtaoPurchaseOrder)).all()
    assert po.source_file == os.path.join("2026년", "OHI_Order Sheet_260812.pdf")


def test_same_serial_two_files_both_kept_one_authoritative(session, folder):
    """개정 이력을 버리지 않는다 — 전부 담고 `is_authoritative`로 가른다(D-INV-3).

    버리면 「왜 이 숫자인가」를 나중에 아무도 못 되짚는다.
    """
    root, put = folder
    put("2026년/OHI_Order Sheet_260107.pdf", _doc("20260107-2", [{"code": "GAPIP15PR", "qty": 5700}]))
    # ECOUNT 사본(해시형 파일명 15자) — 실측 26/26이 이 꼴이다.
    put("2026년/87261BS4UU81W5D.PDF", _doc("20260107-2", [{"code": "GAPIP15PR", "qty": 1300}]))

    rep = I.ingest_orders(session, root)
    assert rep.serials == 1
    assert (rep.authoritative, rep.superseded) == (1, 1)
    assert session.query(OtaoPurchaseOrder).count() == 2

    (winner,) = session.scalars(
        select(OtaoPurchaseOrder).where(OtaoPurchaseOrder.is_authoritative.is_(True))
    ).all()
    assert winner.source_kind == "ecount"
    (loser,) = session.scalars(
        select(OtaoPurchaseOrder).where(OtaoPurchaseOrder.is_authoritative.is_(False))
    ).all()
    assert loser.supersede_reason and "ECOUNT" in loser.supersede_reason


def test_revise_wins_when_no_ecount_copy(session, folder):
    """2023~2024년분엔 ECOUNT 사본이 없다 — 그때는 `Revise` 표기가 정본이다(Jino 23:13)."""
    root, put = folder
    put("2024년/OHI Order Sheet_240405.PDF", _doc("20240405-1", [{"code": "GAPIP13", "qty": 100}]))
    put("2024년/OHI Order Sheet_240405_Revise.PDF", _doc("20240405-1", [{"code": "GAPIP13", "qty": 250}]))

    I.ingest_orders(session, root)
    (winner,) = session.scalars(
        select(OtaoPurchaseOrder).where(OtaoPurchaseOrder.is_authoritative.is_(True))
    ).all()
    assert "Revise" in winner.source_file


def test_mtime_tiebreak_is_surfaced_not_silent(session, folder):
    """①②로 안 갈리고 mtime만으로 정해졌으면 **그렇다고 말한다.**

    ECOUNT 사본은 「내려받은 시각」이 전부 같은 날이라 mtime이 문서의 나이가 아니다 —
    근거가 약한 판정을 조용히 확정하면 그게 곧 잘못된 정본이 된다.
    """
    root, put = folder
    a = put("2025/OHI_Order Sheet_250102.pdf", _doc("20250102-1", [{"code": "GAPIP13", "qty": 100}]))
    b = put("2025/OHI_Order Sheet_250102 (2).pdf", _doc("20250102-1", [{"code": "GAPIP13", "qty": 200}]))
    os.utime(a, (1_700_000_000, 1_700_000_000))
    os.utime(b, (1_800_000_000, 1_800_000_000))

    rep = I.ingest_orders(session, root)
    assert rep.tie_broken_by_mtime == ["20250102-1"]
    (winner,) = session.scalars(
        select(OtaoPurchaseOrder).where(OtaoPurchaseOrder.is_authoritative.is_(True))
    ).all()
    assert "(2)" in winner.source_file


def test_blank_quantity_line_is_reported_not_zero_filled(session, folder):
    """★계약 §2-8 — 「데이터 없음」을 0으로 바꾸지 않는다. 대신 좌표와 함께 말한다."""
    root, put = folder
    put(
        "2025/OHI_Order Sheet_250702.pdf",
        _doc("20250702-1", [
            {"code": "PGAPIP17", "qty": None, "name_en": "Privacy Glass_Ip17"},
            {"code": "GAPIP15PR", "qty": 300},
        ], header_qty=300),
    )

    rep = I.ingest_orders(session, root)
    assert rep.lines_inserted == 1
    assert len(rep.blank_qty_lines) == 1
    assert rep.blank_qty_lines[0]["code"] == "PGAPIP17"
    assert rep.blank_qty_lines[0]["serial"] == "20250702-1"
    # 0짜리 라인이 원장에 들어가 있지 않다 — 들어갔으면 「안 시켰다」로 읽힌다.
    assert session.query(OtaoPurchaseOrderLine).count() == 1


def test_quantity_check_failure_is_reported(session, folder):
    """헤더 검산 불일치는 삼키지 않는다 — 파싱이 틀렸다는 유일한 신호다."""
    root, put = folder
    put("2026년/OHI_Order Sheet_260812.pdf",
        _doc("20260812-1", [{"code": "GAPIP15PR", "qty": 500}], header_qty=999))

    rep = I.ingest_orders(session, root)
    assert len(rep.qty_mismatch) == 1
    assert rep.qty_mismatch[0]["header_qty"] == 999
    assert rep.qty_mismatch[0]["line_qty_sum"] == 500


def test_order_date_comes_from_serial_prefix(session, folder):
    """발주일은 발주번호 앞 8자리다. 형식이 아니면 **지어내지 않고** 리포트에 싣는다."""
    root, put = folder
    put("2026년/a.pdf", _doc("20260812-1", [{"code": "GAPIP15PR", "qty": 10}]))
    put("2026년/b.pdf", _doc("SO-WSOH-046", [{"code": "GAPIP16PR", "qty": 20}]))

    rep = I.ingest_orders(session, root)
    rows = {po.serial: po.order_date for po in session.scalars(select(OtaoPurchaseOrder)).all()}
    assert rows["20260812-1"] == date(2026, 8, 12)
    assert rows["SO-WSOH-046"] is None
    assert rep.bad_serial_dates == ["SO-WSOH-046 (2026년/b.pdf)"]


def test_non_purchase_order_pdfs_are_counted_not_ingested(session, folder):
    """121파일 중 26개는 Packing List·Invoice다 — 세되 원장에 넣지 않는다."""
    root, put = folder
    put("2026년/OHI_Order Sheet_260812.pdf", _doc("20260812-1", [{"code": "GAPIP15PR", "qty": 10}]))
    put("2026년/Packing list SO-WSOH-046.pdf", None)

    rep = I.ingest_orders(session, root)
    assert (rep.files_scanned, rep.purchase_orders, rep.non_purchase_orders) == (2, 1, 1)
    assert session.query(OtaoPurchaseOrder).count() == 1


def test_payload_survives_json_round_trip(session, folder):
    """★페이로드는 Mac → prod로 **JSON 파일 하나가 되어** 건너간다.

    직렬화가 안 되는 값이 섞이면 그 사실은 **prod에서만** 드러난다(로컬 `ingest_orders`는
    dict를 그대로 넘겨 성공한다). 그 갈라짐을 여기서 잡는다.
    """
    root, put = folder
    put("2026년/OHI_Order Sheet_260812.pdf",
        _doc("20260812-1", [{"code": "GAPIP15PR", "qty": 500, "name_en": "Glass_iP15 pro"}]))

    payload = json.loads(json.dumps(I.build_payload(root), ensure_ascii=False))
    rep = I.ingest_payload(session, payload)

    assert (rep.inserted, rep.lines_inserted) == (1, 1)
    (line,) = session.scalars(select(OtaoPurchaseOrderLine)).all()
    assert (line.product_code, line.quantity) == ("GAPIP15PR", 500)


def test_unknown_payload_version_is_refused(session):
    """형식이 바뀐 페이로드를 옛 코드가 조용히 먹으면 원장이 **반쯤** 채워진다."""
    with pytest.raises(ValueError, match="version"):
        I.ingest_payload(session, {"version": 999, "root": "/x", "files": []})


# ─────────────────────────────────────────────────────────────────────────────
# 사전 동기화
# ─────────────────────────────────────────────────────────────────────────────


def _customs(session, item_names: list[str]) -> None:
    sh = ImportShipment(hbl_no="HBL1", declaration_date=date(2026, 1, 27), fx_rate=Decimal("200"))
    session.add(sh)
    session.flush()
    for seq, name in enumerate(item_names, start=1):
        session.add(
            ImportInvoiceLine(
                shipment_id=sh.id,
                seq=seq,
                item_name=name,
                quantity=Decimal(100),
                unit_price_foreign=Decimal("1"),
                line_type="product",
            )
        )
    session.flush()


def test_sync_name_map_authoritative_label_wins_over_superseded(session, folder):
    """라벨이 «충돌»하면 정본이 이긴다 — D-INV-3의 우선순위는 라벨 축에서도 유지된다.

    (구판 이름은 `..._uses_only_authoritative_orders`였다. D-INV-5로 사전 모집단이 전체로
    넓어졌으므로 이 테스트가 지키는 것은 「정본만 읽는다」가 아니라 **「충돌 시 정본이 이긴다」**로
    바뀌었다. 넓히면서 이 잠금까지 같이 풀면 개정본이 고쳐 쓴 코드를 옛 코드가 조용히 이긴다.)
    """
    root, put = folder
    put("2026년/OHI_Order Sheet_260107.pdf",
        _doc("20260107-2", [{"code": "WRONGCODE", "qty": 5700, "name_en": "Glass_iP15 pro"}]))
    put("2026년/87261BS4UU81W5D.PDF",
        _doc("20260107-2", [{"code": "GAPIP15PR", "qty": 1300, "name_en": "Glass_iP15 pro"}]))
    I.ingest_orders(session, root)
    _customs(session, ["Glass_iP15 pro"])

    rep = I.sync_name_map(session)
    (row,) = session.scalars(select(OtaoItemNameMap)).all()
    assert row.product_code == "GAPIP15PR"
    # ambiguous로 떨어지지도 않는다 — 정본이 그 키를 «말했으므로» 보충층은 아예 안 본다.
    assert row.match_kind == "exact_en"
    assert (rep.map_total, rep.map_resolved) == (1, 1)


def test_sync_name_map_borrows_labels_when_authoritative_is_silent(session, folder):
    """★D-INV-5의 본체 — 정본이 «침묵»하는 키는 대체된 판본이 채운다.

    prod의 실제 모양이다: ECOUNT 사본(정본)은 B형이라 `영문상품명` 칸이 **아예 없고**(`name_en`
    NULL), 우리가 만들어 보낸 로컬 원본(대체됨)에만 영문명이 있다. 「정본만」으로 사전을 만들면
    이 자리가 통째로 비어 원장 품목명이 영원히 `unmatched`로 남는다 — prod 실측으로 그렇게
    잃고 있던 것이 **8종 5,600개**(커버리지 61.4% ↔ 87.2%).
    """
    root, put = folder
    put("2025/OHI_Order Sheet_251121.pdf",
        _doc("20251121-1", [{"code": "GAPIP17", "qty": 500, "name_en": "Glass_Ip17"}]))
    # ECOUNT 사본(15자 해시형 파일명) = 정본. 같은 코드를 들고 있으나 영문명 칸이 없다.
    put("2025/87267R2FJHK2TOO.PDF",
        _doc("20251121-1", [{"code": "GAPIP17", "qty": 500, "name_en": None}]))
    I.ingest_orders(session, root)
    _customs(session, ["Glass_Ip17"])

    # 전제 확인 — 정본이 실제로 영문명을 안 갖고 있어야 이 테스트가 무언가를 잰다.
    auth_names = session.scalars(
        select(OtaoPurchaseOrderLine.name_en)
        .join(OtaoPurchaseOrder, OtaoPurchaseOrderLine.order_id == OtaoPurchaseOrder.id)
        .where(OtaoPurchaseOrder.is_authoritative.is_(True))
    ).all()
    assert auth_names == [None]

    rep = I.sync_name_map(session)
    (row,) = session.scalars(select(OtaoItemNameMap)).all()
    assert row.product_code == "GAPIP17"
    assert rep.map_unresolved == []
    assert (rep.map_total, rep.map_resolved) == (1, 1)


def test_widening_the_dictionary_does_not_widen_the_order_totals(session, folder):
    """★두 축을 가르는 잠금 — 사전은 전체를 읽어도 **발주 누계는 정본만** 센다.

    섞이면 개정 전 판본의 수량이 발주 누계에 얹힌다(실측 `20260107-2` 5,700 vs 1,300).
    사전 모집단을 넓힌 이번 변경이 수량 축으로 새는지를 여기서 잡는다.
    """
    root, put = folder
    # 발주일은 원장 창(`_customs`의 2026-01-27) «안»으로 둔다 — 창 밖이면 수량이
    # `out_of_window_ordered`로 빠져 이 테스트가 재려던 자리를 안 밟는다.
    put("2026년/OHI_Order Sheet_260210.pdf",
        _doc("20260210-2", [{"code": "GAPIP15PR", "qty": 5700, "name_en": "Glass_iP15 pro"}]))
    put("2026년/87261BS4UU81W5D.PDF",
        _doc("20260210-2", [{"code": "GAPIP15PR", "qty": 1300, "name_en": None}]))
    I.ingest_orders(session, root)
    _customs(session, ["Glass_iP15 pro"])
    I.sync_name_map(session)

    from app.services.otao_po.roster import build_roster

    roster = build_roster(session)
    (row,) = roster.rows
    assert row.product_code == "GAPIP15PR"
    # 라벨은 대체된 판본에서 빌려 왔고(그래서 픽업 100개가 붙는다) 수량은 정본 1,300뿐이다.
    assert row.ordered == 1300
    assert row.picked == 100
    assert roster.unmapped == {}


def test_sync_name_map_applies_rule_3_and_surfaces_the_rest(session, folder):
    """규칙 3(`2ea`)은 코드가 집행하고, 규칙 2(공용 표기)는 **못 푼 채로 드러낸다.**"""
    root, put = folder
    put("2026년/a.pdf", _doc("20260812-1", [
        {"code": "PGAPIP16PR", "qty": 100, "name_en": "Privacy Glass_iP16 Pro"},
    ]))
    I.ingest_orders(session, root)
    _customs(session, [
        "Privacy Glass_iP16 Pro 2ea",
        "For iPhone 15/16/14Pro Privacy Tempered Glass",
    ])

    rep = I.sync_name_map(session)
    rows = {m.raw_name: m for m in session.scalars(select(OtaoItemNameMap)).all()}
    assert rows["Privacy Glass_iP16 Pro 2ea"].product_code == "PGAPIP16PR"
    assert rows["For iPhone 15/16/14Pro Privacy Tempered Glass"].product_code is None
    assert rep.map_unresolved == ["For iPhone 15/16/14Pro Privacy Tempered Glass"]


def test_sync_name_map_does_not_overwrite_manual_decisions(session, folder):
    """★사람이 확정한 매핑을 재적재가 갈아엎지 않는다 — 그 자리가 코드로는 안 풀리는 자리다."""
    root, put = folder
    put("2026년/a.pdf", _doc("20260812-1", [
        {"code": "GAPIP15PR", "qty": 100, "name_en": "Glass_iP15 pro"},
    ]))
    I.ingest_orders(session, root)
    _customs(session, ["For iPhone 15/16/14Pro Privacy Tempered Glass"])
    session.add(
        OtaoItemNameMap(
            raw_name="For iPhone 15/16/14Pro Privacy Tempered Glass",
            product_code="GAPIP15PR",
            match_kind="manual",
            note="Jino 2026-08-25 22:47 — 공용 표기",
        )
    )
    session.flush()

    rep = I.sync_name_map(session)
    (row,) = session.scalars(select(OtaoItemNameMap)).all()
    assert (row.match_kind, row.product_code) == ("manual", "GAPIP15PR")
    assert (rep.map_manual_kept, rep.map_resolved) == (1, 1)


def test_manual_row_without_code_still_counts_as_mapping_needed(session, folder):
    """★적대 리뷰 P2-7 — 사람이 「아직 모르겠다」로 남긴 행도 「매핑 필요」에 실린다.

    사람이 손댔다는 이유만으로 미해결 항목이 리포트에서 사라지면, 그게 계약 §2-9가 말하는
    «조용한 발주 누락»이다. 사람의 확정은 존중하되 **미확정은 여전히 미확정으로 센다.**
    """
    root, put = folder
    put("2026년/a.pdf", _doc("20260812-1", [
        {"code": "GAPIP15PR", "qty": 100, "name_en": "Glass_iP15 pro"},
    ]))
    I.ingest_orders(session, root)
    _customs(session, ["For iPhone 15/16/14Pro Privacy Tempered Glass"])
    session.add(
        OtaoItemNameMap(
            raw_name="For iPhone 15/16/14Pro Privacy Tempered Glass",
            product_code=None,  # 사람이 봤지만 아직 못 정했다
            match_kind="manual",
            note="Jino 검토 대기",
        )
    )
    session.flush()

    rep = I.sync_name_map(session)
    assert rep.map_manual_kept == 1
    assert rep.map_resolved == 0
    assert rep.map_unresolved == ["For iPhone 15/16/14Pro Privacy Tempered Glass"]
