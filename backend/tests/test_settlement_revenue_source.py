# test_settlement_revenue_source.py — 쿠팡 3P 정산 매출 SA (트랙 revenue-wing-truth S4, D-11~D-13).
# 인메모리 SQLite로 settlement_net_revenue_by_day / settled_order_ids_by_day 검증.
# 핵심 계약(머니룰):
#   ① net = Σ(SALE sale_amount) − Σ(REFUND sale_amount) — REFUND는 양수 미러라 빼야 net(라이브 확정)
#   ② 일자 그레인 = sale_date, NULL 제외
#   ③ 3P 기본 계정(WING1·WING2)만, account_keys로 추가 필터
#   ④ settled_order_ids = 그 날 정산 등장 주문번호 집합(SALE/REFUND 무관)
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import CoupangRevenueFee
from app.services.coupang.settlement_revenue_source import (
    settled_order_ids_by_day,
    settlement_net_revenue_by_day,
)

_Z = Decimal(0)
D = lambda x: Decimal(str(x))  # noqa: E731
WIN = (date(2026, 6, 1), date(2026, 6, 30))


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _fee(db, *, order_id, vid, account="COUPANG_WING1", sale_type="SALE",
         sale_date=date(2026, 6, 8), sale_amount=10000, fee=858):
    db.add(CoupangRevenueFee(
        order_id=order_id, vendor_item_id=vid, account_key=account,
        vendor_id="A01564720", sale_type=sale_type, sale_date=sale_date,
        sale_price=D(sale_amount), quantity=1, sale_amount=D(sale_amount),
        service_fee=D(fee), service_fee_vat=_Z, settlement_amount=D(sale_amount) - D(fee),
        coupang_discount_coupon=_Z, seller_discount_coupon=_Z,
        downloadable_coupon=_Z, courantee_fee=_Z,
    ))


def test_sale_only_day_sums_sale_amount(db):
    """SALE만 있는 날: net = Σ sale_amount."""
    _fee(db, order_id="O1", vid="v1", sale_amount=16900)
    _fee(db, order_id="O2", vid="v2", sale_amount=15900)
    db.commit()
    out = settlement_net_revenue_by_day(db, *WIN)
    assert out[("COUPANG_WING1", date(2026, 6, 8))] == D(32800)


def test_full_refund_nets_to_zero(db):
    """전액환불: SALE 179,000 + REFUND 179,000(양수 미러) → net 0 (라이브 실증 규약)."""
    _fee(db, order_id="O1", vid="v1", sale_type="SALE", sale_amount=179000)
    _fee(db, order_id="O1", vid="v1", sale_type="REFUND", sale_amount=179000)
    db.commit()
    out = settlement_net_revenue_by_day(db, *WIN)
    assert out.get(("COUPANG_WING1", date(2026, 6, 8)), _Z) == _Z


def test_partial_refund_nets_difference(db):
    """부분환불: SALE 20,000 − REFUND 8,000 = net 12,000."""
    _fee(db, order_id="O1", vid="v1", sale_type="SALE", sale_amount=20000)
    _fee(db, order_id="O1", vid="v1", sale_type="REFUND", sale_amount=8000)
    db.commit()
    out = settlement_net_revenue_by_day(db, *WIN)
    assert out[("COUPANG_WING1", date(2026, 6, 8))] == D(12000)


def test_grouped_by_day_and_account(db):
    """일자·계정별 분리 집계."""
    _fee(db, order_id="O1", vid="v1", sale_date=date(2026, 6, 7), sale_amount=10000)
    _fee(db, order_id="O2", vid="v2", sale_date=date(2026, 6, 8), sale_amount=20000)
    _fee(db, order_id="O3", vid="v3", account="COUPANG_WING2", sale_date=date(2026, 6, 8), sale_amount=5000)
    db.commit()
    out = settlement_net_revenue_by_day(db, *WIN)
    assert out[("COUPANG_WING1", date(2026, 6, 7))] == D(10000)
    assert out[("COUPANG_WING1", date(2026, 6, 8))] == D(20000)
    assert out[("COUPANG_WING2", date(2026, 6, 8))] == D(5000)


def test_account_filter(db):
    """account_keys 주면 해당 계정만."""
    _fee(db, order_id="O1", vid="v1", account="COUPANG_WING1", sale_amount=10000)
    _fee(db, order_id="O2", vid="v2", account="COUPANG_WING2", sale_amount=20000)
    db.commit()
    out = settlement_net_revenue_by_day(db, *WIN, account_keys=["COUPANG_WING1"])
    assert ("COUPANG_WING1", date(2026, 6, 8)) in out
    assert ("COUPANG_WING2", date(2026, 6, 8)) not in out


def test_default_excludes_non_3p_account(db):
    """기본(account_keys=None)은 3P(WING1·WING2)만 — 타 계정 제외."""
    _fee(db, order_id="O1", vid="v1", account="COUPANG_RG1", sale_amount=10000)
    db.commit()
    out = settlement_net_revenue_by_day(db, *WIN)
    assert out == {}


def test_null_sale_date_excluded(db):
    """sale_date NULL 행은 일자 그레인 부재로 제외."""
    _fee(db, order_id="O1", vid="v1", sale_date=None, sale_amount=10000)
    _fee(db, order_id="O2", vid="v2", sale_date=date(2026, 6, 8), sale_amount=5000)
    db.commit()
    out = settlement_net_revenue_by_day(db, *WIN)
    assert out == {("COUPANG_WING1", date(2026, 6, 8)): D(5000)}


def test_window_bounds(db):
    """윈도우 밖(sale_date) 제외."""
    _fee(db, order_id="O1", vid="v1", sale_date=date(2026, 5, 31), sale_amount=10000)
    _fee(db, order_id="O2", vid="v2", sale_date=date(2026, 6, 8), sale_amount=5000)
    db.commit()
    out = settlement_net_revenue_by_day(db, *WIN)
    assert list(out.keys()) == [("COUPANG_WING1", date(2026, 6, 8))]


def test_settled_order_ids_collects_set(db):
    """settled_order_ids: 그 날 정산 등장 주문번호 집합(SALE/REFUND 무관, distinct)."""
    _fee(db, order_id="O1", vid="v1", sale_type="SALE", sale_amount=10000)
    _fee(db, order_id="O1", vid="v1", sale_type="REFUND", sale_amount=10000)
    _fee(db, order_id="O2", vid="v2", sale_type="SALE", sale_amount=5000)
    db.commit()
    out = settled_order_ids_by_day(db, *WIN)
    assert out[("COUPANG_WING1", date(2026, 6, 8))] == {"O1", "O2"}
