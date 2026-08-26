"""발주 로스터 HTTP 경계 — 계약 §4 **S1**의 표면이 여기서 시작한다 (체인 `발주예측` n=5).

## 왜 서비스층 테스트로는 부족한가

교훈 #321: `response_model`이 선언 안 된 키를 **HTTP body에서 조용히 지워**, 서비스층 테스트
9건이 전부 초록인데 화면엔 배너가 통째로 안 뜨는 사고가 있었다. 이 응답은 「있으면 반드시 보여야
하는」 자백 필드가 많아 같은 함정의 표면이 넓다 — 그래서 이 파일은 **HTTP body를 단언한다.**

## 화면이 자백해야 하는 3가지가 body에 실려 있는가

계약이 요구하는 정직함은 숫자가 아니라 **결손의 표시**다:

    ① `window_start`            잔량은 통관 원장이 덮는 창 안의 발주분만 세었다
    ② `unmapped`                픽업 칸에 못 붙은 원장 품목명과 그 수량 (§2-9)
    ③ `notes` / `reserved<0`    음수는 창이 어긋났다는 신호이고 0으로 안 깎았다 (§2-8)

그리고 **`ledger_empty`** — 「적재를 안 돌렸다」와 「발주가 0이다」는 다른 상태다. 전자를 0으로
그리면 화면이 거짓말을 한다(n=4가 정확히 그 상태로 닫혔다: 코드는 있는데 원장이 비어 있었다).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    ImportInvoiceLine,
    ImportShipment,
    OtaoItemNameMap,
    OtaoPurchaseOrder,
    OtaoPurchaseOrderLine,
)


@pytest.fixture()
def env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # ★prod 세션과 같은 설정 — `autoflush=True`면 「방금 만든 행이 안 보이는」 결함을 못 잡는다.
    Testing = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        db = Testing()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with Testing() as s:
        yield TestClient(app), s
    app.dependency_overrides.pop(get_db, None)


def _seed(session, *, negative: bool = False) -> None:
    po = OtaoPurchaseOrder(
        serial="20260601-1",
        order_date=date(2026, 6, 1),
        source_kind="ecount",
        source_file="2026년/87261BS4UU81W5D.PDF",
        content_sha256="sha-1",
        is_authoritative=True,
    )
    old = OtaoPurchaseOrder(
        serial="20230426-1",
        order_date=date(2023, 4, 26),
        source_kind="local",
        source_file="2023년/OHI_Order Sheet_230426.pdf",
        content_sha256="sha-2",
        is_authoritative=True,
    )
    superseded = OtaoPurchaseOrder(
        serial="20260601-1",
        order_date=date(2026, 6, 1),
        source_kind="local",
        source_file="2026년/OHI_Order Sheet_260601.pdf",
        content_sha256="sha-3",
        is_authoritative=False,
        supersede_reason="ECOUNT 사본이 정본: 2026년/87261BS4UU81W5D.PDF",
    )
    session.add_all([po, old, superseded])
    session.flush()
    session.add_all([
        OtaoPurchaseOrderLine(
            order_id=po.id, seq=1, product_code="GAPIP15PR",
            name_en="Glass_iP15 pro", quantity=100 if negative else 500,
        ),
        OtaoPurchaseOrderLine(
            order_id=old.id, seq=1, product_code="GAPIP15PR",
            name_en="Glass_iP15 pro", quantity=4000,
        ),
        OtaoPurchaseOrderLine(
            order_id=superseded.id, seq=1, product_code="GAPIP15PR", quantity=9999,
        ),
    ])

    sh = ImportShipment(
        hbl_no="HBL1", declaration_date=date(2026, 1, 27), fx_rate=Decimal("200")
    )
    session.add(sh)
    session.flush()
    session.add_all([
        ImportInvoiceLine(
            shipment_id=sh.id, seq=1, item_name="Glass_iP15 pro",
            quantity=Decimal(300), unit_price_foreign=Decimal("1"), line_type="product",
        ),
        ImportInvoiceLine(
            shipment_id=sh.id, seq=2,
            item_name="For iPhone 15/16/14Pro Privacy Tempered Glass",
            quantity=Decimal(80), unit_price_foreign=Decimal("1"), line_type="product",
        ),
    ])
    session.add_all([
        OtaoItemNameMap(
            raw_name="Glass_iP15 pro", product_code="GAPIP15PR", match_kind="exact_en"
        ),
        OtaoItemNameMap(
            raw_name="For iPhone 15/16/14Pro Privacy Tempered Glass",
            product_code=None, match_kind="unmatched",
        ),
    ])
    session.commit()


def test_three_columns_reach_the_http_body(env):
    """★S1의 본체 — SKU별 **세 칸**이 HTTP 응답에 그대로 실린다.

    「계산이 된다」로는 합격이 아니다(계약 §6). 값이 body까지 오는지가 화면 직전의 마지막 마디다.
    """
    tc, s = env
    _seed(s)

    body = tc.get("/api/otao-po/roster").json()
    (row,) = body["rows"]
    assert row["product_code"] == "GAPIP15PR"
    assert (row["ordered"], row["picked"], row["reserved"]) == (500, 300, 200)
    assert body["totals"]["ordered"] == 500
    assert body["totals"]["picked"] == 300
    assert body["totals"]["reserved"] == 200


def test_body_never_carries_a_merged_single_number(env):
    """계약 §3-9 금지선 — 3분 표기를 합산한 파생 총계를 응답에 두지 않는다.

    합치는 순간 ②픽업 결정이 화면에서 사라진다. 「이 값을 화면이 안 그리면 된다」로 두면
    다음 세션이 그 필드를 보고 그린다 — 그래서 **없는 것이 규격**이다.
    """
    tc, s = env
    _seed(s)

    body = tc.get("/api/otao-po/roster").json()
    forbidden = {"total", "combined", "on_hand_plus_reserved", "grand_total"}
    assert not (forbidden & set(body["totals"])), body["totals"]
    assert not (forbidden & set(body["rows"][0]))


def test_window_start_is_declared(env):
    """자백 ① — 잔량이 어느 구간만 센 것인지 화면이 말할 수 있어야 한다."""
    tc, s = env
    _seed(s)

    body = tc.get("/api/otao-po/roster").json()
    assert body["window_start"] == "2026-01-27"
    assert body["rows"][0]["out_of_window_ordered"] == 4000
    assert any("2026-01-27" in n for n in body["notes"])


def test_unmapped_names_are_in_the_body_with_quantity(env):
    """자백 ② — 못 붙인 품목명이 **수량과 함께** 온다. 조용히 빼면 그만큼이 발주 누락이다."""
    tc, s = env
    _seed(s)

    body = tc.get("/api/otao-po/roster").json()
    assert body["unmapped"] == [
        {"item_name": "For iPhone 15/16/14Pro Privacy Tempered Glass", "quantity": 80}
    ]
    assert body["totals"]["unmapped_qty"] == 80


def test_negative_reserved_is_not_clamped_in_the_body(env):
    """자백 ③ — 음수를 0으로 깎지 않는다. 깎으면 「창이 어긋났다」는 신호가 사라진다."""
    tc, s = env
    _seed(s, negative=True)

    body = tc.get("/api/otao-po/roster").json()
    assert body["rows"][0]["reserved"] == -200
    assert any("음수" in n for n in body["notes"])


def test_empty_ledger_says_so_instead_of_showing_zeros(env):
    """★「적재를 안 돌렸다」와 「발주가 0이다」는 다른 상태다.

    n=4가 정확히 앞의 상태로 닫혔다 — 코드는 있는데 원장이 비어 화면에 0이 뜬다. 0을 보여 주면
    「발주가 없다」로 읽히므로 body가 먼저 그것을 말해야 한다.
    """
    tc, _ = env
    body = tc.get("/api/otao-po/roster").json()
    assert body["ledger_empty"] is True
    assert body["rows"] == []
    assert body["source"]["orders_total"] == 0


def test_source_reports_authoritative_split(env):
    """정본/대체 건수를 화면이 보여야 「왜 이 숫자인가」를 되짚을 수 있다(D-INV-3 근거 보존)."""
    tc, s = env
    _seed(s)

    src = tc.get("/api/otao-po/roster").json()["source"]
    assert (src["orders_total"], src["orders_authoritative"], src["orders_superseded"]) == (3, 2, 1)
    assert src["last_order_date"] == "2026-06-01"
    assert (src["name_map_total"], src["name_map_resolved"]) == (2, 1)


# ─────────────────────────────────────────────────────────────────────────────
# S3 — 판매 시계열의 HTTP 경계 (체인 `발주예측` n=6)
#
# 여기서 지키는 것은 숫자가 아니라 **자백 필드가 body까지 살아 오는가**다. 서비스층이 아무리
# 정직해도 `response_model`·직렬화에서 한 키가 지워지면 화면은 조용히 거짓말을 한다(교훈 #321).
# ─────────────────────────────────────────────────────────────────────────────


def _seed_sales(session) -> None:
    from datetime import datetime as _dt

    from app.models import Order, ProductMaster, SyncLog

    pm = ProductMaster(
        internal_sku="OHI-0001", product_name="지문방지 필름", cost_price=Decimal("1000")
    )
    session.add(pm)
    session.flush()
    today = date.today()
    session.add(
        Order(
            channel_id=6,
            product_id=pm.id,
            order_number="O-1",
            quantity=4,
            selling_price=Decimal("10000"),
            order_date=_dt(today.year, today.month, today.day, 10, 0, 0),
            status="delivered",
        )
    )
    session.add(
        Order(
            channel_id=6,
            product_id=None,  # 못 붙은 판매 — 조용히 사라지면 안 된다
            order_number="O-2",
            quantity=1,
            selling_price=Decimal("10000"),
            order_date=_dt(today.year, today.month, today.day, 11, 0, 0),
            status="delivered",
        )
    )
    session.add(
        SyncLog(
            channel_id=6,
            sync_type="orders",
            status="success",
            date_from=_dt(today.year, today.month, today.day),
            date_to=_dt(today.year, today.month, today.day),
            started_at=_dt(today.year, today.month, today.day, 23, 0, 0),
        )
    )
    session.flush()


def test_sales_body_carries_every_confession_field(env):
    """★자백 필드 5종이 **HTTP body에** 실려야 한다 — 하나라도 빠지면 화면이 못 그린다."""
    tc, s = env
    _seed_sales(s)

    body = tc.get("/api/otao-po/sales?days=7").json()
    # ★`dates`가 빠지면 화면이 「일별 추이 (undefined ~ undefined)」가 된다 — 값은 있는데
    #   «좌표»가 사라지는 모양이라 서비스층 테스트가 원리적으로 못 잡는다(적대 리뷰 2R R2-A).
    for key in ("window_start", "window_end", "dates", "channels", "rows", "daily",
                "unmapped", "order_axis", "notes"):
        assert key in body, f"body에 {key}가 없다"
    assert len(body["dates"]) == 7
    assert len(body["rows"][0]["series"]) == 7, "series가 날짜 축과 길이가 같아야 한다"
    naver = next(c for c in body["channels"] if c["key"] == "naver")
    # ★`quantity_ambiguous`가 빠지면 「매핑 모호」가 화면에서 **영구히 0**으로 그려진다.
    for key in ("mapping_rate", "missing_day_evidence", "days_collected_zero",
                "days_no_data", "quantity_excluded", "quantity_ambiguous",
                "bridge", "source_table"):
        assert key in naver, f"채널 body에 {key}가 없다"


def test_sales_body_shows_unmapped_and_mapping_rate(env):
    tc, s = env
    _seed_sales(s)

    body = tc.get("/api/otao-po/sales?days=7").json()
    naver = next(c for c in body["channels"] if c["key"] == "naver")
    assert (naver["quantity"], naver["quantity_mapped"]) == (5, 4)
    assert naver["mapping_rate"] == 80.0
    assert {"channel": "naver", "quantity": 1} in body["unmapped"]


def test_sales_body_marks_channels_without_missing_day_evidence(env):
    """근거가 없는 채널이 body에서 **false로 드러나야** 화면이 「구분 불가」를 그릴 수 있다."""
    tc, s = env
    _seed_sales(s)

    body = tc.get("/api/otao-po/sales?days=7").json()
    by_key = {c["key"]: c for c in body["channels"]}
    assert by_key["naver"]["missing_day_evidence"] is True
    assert by_key["cafe24"]["missing_day_evidence"] is True
    for key in ("wing3p_ofix", "wing3p_ohitech", "rg2p_ofix", "rg2p_ohitech", "rocket1p"):
        assert by_key[key]["missing_day_evidence"] is False
    assert any("구분할 근거가" in n for n in body["notes"])


def test_sales_body_confesses_the_missing_bridge_to_the_order_axis(env):
    """★이 자백이 body에서 사라지면 화면이 판매를 예약 잔량 옆에 놓아 «거짓 대비»가 된다."""
    tc, s = env
    _seed_sales(s)

    body = tc.get("/api/otao-po/sales?days=7").json()
    assert body["order_axis"]["overlap"] == 0
    assert body["order_axis"]["sales_axis_skus"] == 1
    assert any("다리가 아직 없다" in n for n in body["notes"])


# ─────────────────────────────────────────────────────────────────────────────
# S2 — 정산 창의 HTTP 경계 (체인 `발주예측` n=7)
#
# 서비스층 테스트(`test_otao_po_settlement.py`)가 숫자를 잠그고, 여기서는 **자백 필드가 body까지
# 살아 오는가**를 잠근다. 특히 `reconciled: null`은 «대조 불가»라는 상태 자체이므로, 직렬화에서
# 지워지거나 `false`로 바뀌면 화면이 **「대조했는데 틀렸다」로 거짓말**한다 — 서비스층만 보면
# 원리적으로 못 잡는 자리다(교훈 #321의 이 슬라이스판).
# ─────────────────────────────────────────────────────────────────────────────


def _seed_settlement(session) -> None:
    """창 둘에 걸치도록 심는다 — 07-23 픽업은 **8월 창**이다(20일부터 다음 창)."""
    jul = ImportShipment(
        hbl_no="SETR-JUL", declaration_date=date(2026, 7, 13), status="confirmed",
        currency="CNY", fx_rate=Decimal("209.88"),
    )
    aug = ImportShipment(
        hbl_no="SETR-AUG", declaration_date=date(2026, 7, 23), status="draft",
        currency="CNY", fx_rate=Decimal("209.88"),
    )
    session.add_all([jul, aug])
    session.flush()
    session.add_all([
        ImportInvoiceLine(
            shipment_id=jul.id, seq=1, item_name="Glass_a",
            quantity=Decimal(100), unit_price_foreign=Decimal(10), line_type="product",
        ),
        ImportInvoiceLine(
            shipment_id=aug.id, seq=1, item_name="Glass_b",
            quantity=Decimal(50), unit_price_foreign=Decimal(12), line_type="product",
        ),
        ImportInvoiceLine(
            shipment_id=aug.id, seq=2, item_name="cleaning kits",
            quantity=Decimal(2400), unit_price_foreign=Decimal("0.8"), line_type="material",
        ),
    ])
    session.commit()


def test_settlement_body_carries_every_confession_field(env):
    tc, s = env
    _seed_settlement(s)

    body = tc.get("/api/otao-po/settlement").json()
    for key in ("ledger_empty", "ledger_start", "ledger_end", "currency", "windows",
                "unassigned", "totals", "reconciliation", "notes"):
        assert key in body, f"body에 {key}가 없다"
    w = body["windows"][0]
    for key in ("key", "start", "end", "shipments", "lines",
                "product_quantity", "product_amount_cny",
                "material_quantity", "material_amount_cny",
                "other_quantity", "other_amount_cny", "total_amount_cny",
                "draft_shipment_ids", "boundary_shipment_ids",
                "payment_actual_cny", "difference_cny", "reconciled"):
        assert key in w, f"창 body에 {key}가 없다"


def test_settlement_body_splits_the_window_at_the_twentieth(env):
    """07-13은 7월 창, 07-23은 **8월 창**이다. 한 창으로 뭉치면 지급액 대조가 통째로 어긋난다."""
    tc, s = env
    _seed_settlement(s)

    body = tc.get("/api/otao-po/settlement").json()
    got = {w["key"]: w["total_amount_cny"] for w in body["windows"]}
    assert got == {"2026-07": 1000.0, "2026-08": 2520.0}


def test_settlement_body_keeps_reconciled_null_not_false(env):
    """★`null`(대조 불가)이 `false`(불일치)로 바뀌면 화면이 없는 사실을 말한다."""
    tc, s = env
    _seed_settlement(s)

    body = tc.get("/api/otao-po/settlement").json()
    assert all(w["reconciled"] is None for w in body["windows"])
    assert all(w["payment_actual_cny"] is None for w in body["windows"])
    assert body["reconciliation"]["source"] == "none"
    assert any("대조 불가" in n for n in body["notes"])


def test_settlement_body_never_merges_product_and_material(env):
    """부자재는 지급액엔 들어가고 S1 픽업 누계엔 안 들어간다 — 합치면 그 차이를 설명 못 한다."""
    tc, s = env
    _seed_settlement(s)

    aug = next(w for w in tc.get("/api/otao-po/settlement").json()["windows"]
               if w["key"] == "2026-08")
    assert aug["product_amount_cny"] == 600.0
    assert aug["material_amount_cny"] == 1920.0
    assert aug["total_amount_cny"] == 2520.0
    assert aug["material_quantity"] == 2400.0


def test_settlement_body_confesses_draft_and_boundary_shipments(env):
    tc, s = env
    _seed_settlement(s)

    aug = next(w for w in tc.get("/api/otao-po/settlement").json()["windows"]
               if w["key"] == "2026-08")
    assert len(aug["draft_shipment_ids"]) == 1, "draft가 body에서 사라지면 화면이 못 말한다"


def test_settlement_empty_ledger_says_so_instead_of_showing_zeros(env):
    tc, _ = env
    body = tc.get("/api/otao-po/settlement").json()
    assert body["ledger_empty"] is True
    assert body["windows"] == []
    assert any("픽업 0이 아니다" in n for n in body["notes"])
