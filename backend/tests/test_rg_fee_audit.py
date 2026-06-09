# test_rg_fee_audit.py — Harness build_fee_audit 통합 테스트 (트랙 RG-Fee S8, D-17)
# in-memory SQLite에 옵션·정산비용·주문수량을 심어 SA 가로지르는 감사 흐름·집계 검증.
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base,
    CoupangProductItem,
    CoupangRgOrderItem,
    CoupangRgSettlementFee,
)
from app.services.coupang.rg_fee_audit import build_fee_audit


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _add_item(db, vii, w, l, h, wt, name="옵션"):
    db.add(CoupangProductItem(
        vendor_item_id=vii, account_key="COUPANG_WING1", vendor_id="A01564720",
        seller_product_id="P1", seller_product_name="상품", item_name=name,
        sale_price=Decimal(10000), width_mm=w, length_mm=l, height_mm=h, weight_g=wt,
    ))


def _add_fee(db, vii, ftype, amount, dfrom=date(2026, 4, 1), dto=date(2026, 4, 5)):
    db.add(CoupangRgSettlementFee(
        account_key="COUPANG_WING1", vendor_item_id=vii, fee_type=ftype,
        amount=Decimal(amount), recognition_date_from=dfrom, recognition_date_to=dto,
    ))


def _add_order(db, vii, qty, oid="O1", paid=datetime(2026, 4, 2)):
    db.add(CoupangRgOrderItem(
        order_id=oid, vendor_item_id=vii, account_key="COUPANG_WING1",
        vendor_id="A01564720", sales_quantity=qty, paid_at=paid,
    ))


def test_normal_option_no_flag(db):
    # 극소형 폰케이스, 단가 배송 1900/입출고 1100, qty 1 → 정상
    _add_item(db, "100", 227, 126, 20, 137)
    _add_fee(db, "100", "delivery", 1900)
    _add_fee(db, "100", "warehousing", 1100)
    _add_order(db, "100", 1)
    db.commit()
    out = build_fee_audit(db, "COUPANG_WING1")
    assert out["summary"]["total_options"] == 1
    assert out["summary"]["flagged"] == 0
    it = out["items"][0]
    assert it["size_type"] == "극소형"
    assert it["per_unit_delivery"] == 1900


def test_size_mismatch_high_flagged(db):
    # 극소형 치수인데 배송비 19,750(qty 1) → size_mismatch_high
    _add_item(db, "200", 227, 126, 20, 137, name="아이폰17프로")
    _add_fee(db, "200", "delivery", 19750)
    _add_fee(db, "200", "warehousing", 1100)
    _add_order(db, "200", 1)
    db.commit()
    out = build_fee_audit(db, "COUPANG_WING1")
    assert out["summary"]["size_mismatch_high"] == 1
    # 플래그 항목이 최상단 정렬
    assert "size_mismatch_high" in out["items"][0]["flags"]


def test_unit_unknown_when_no_orders(db):
    # 정산비용은 있는데 주문수량 데이터 없음 → unit_unknown (과오청구 단정 금지)
    _add_item(db, "300", 227, 126, 20, 137)
    _add_fee(db, "300", "delivery", 1900)
    db.commit()
    out = build_fee_audit(db, "COUPANG_WING1")
    it = out["items"][0]
    assert "unit_unknown" in it["flags"]
    assert it["per_unit_delivery"] is None


def test_missing_dims_flagged(db):
    # 치수 없는 옵션(정산비용만) → missing_dims
    _add_fee(db, "400", "delivery", 1900)
    _add_order(db, "400", 1)
    db.commit()
    out = build_fee_audit(db, "COUPANG_WING1")
    it = out["items"][0]
    assert "missing_dims" in it["flags"]
    assert it["size_type"] is None


def test_account_grain_rows_excluded(db):
    # 계정 단위 row(vendor_item_id='')는 감사 제외 — 옵션 단위만
    _add_item(db, "500", 227, 126, 20, 137)
    _add_fee(db, "500", "delivery", 1900)
    _add_fee(db, "", "delivery", 100000)  # 계정 합계 row — 제외돼야
    _add_order(db, "500", 1)
    db.commit()
    out = build_fee_audit(db, "COUPANG_WING1")
    assert out["summary"]["total_options"] == 1
    assert all(it["vendor_item_id"] != "" for it in out["items"])


def test_date_range_overlap_includes_boundary_period(db):
    # codex P2-2 회귀: 정산주기(04-01~04-05)가 조회범위(04-02~04-10)에 일부 걸침 → 포함돼야.
    _add_item(db, "700", 227, 126, 20, 137)
    _add_fee(db, "700", "delivery", 1900, dfrom=date(2026, 4, 1), dto=date(2026, 4, 5))
    _add_order(db, "700", 1, paid=datetime(2026, 4, 3))
    db.commit()
    out = build_fee_audit(db, "COUPANG_WING1", date_from=date(2026, 4, 2), date_to=date(2026, 4, 10))
    # 포함관계 필터였다면 from(04-01)<date_from(04-02)이라 드롭됐을 것 → overlap이라 포함.
    assert out["summary"]["total_options"] == 1
    assert out["items"][0]["charged_delivery"] == 1900


def test_net_profit_untouched_readonly(db):
    # D-17: 읽기 전용. 감사 후 정산비용 row 금액 불변.
    _add_item(db, "600", 227, 126, 20, 137)
    _add_fee(db, "600", "delivery", 1900)
    _add_order(db, "600", 1)
    db.commit()
    build_fee_audit(db, "COUPANG_WING1")
    row = db.query(CoupangRgSettlementFee).filter_by(vendor_item_id="600").first()
    assert row.amount == Decimal(1900)
