# test_settlement_revenue_source.py — 쿠팡 3P 정산 매출 SA (트랙 revenue-wing-truth S4, D-11~D-13).
# 인메모리 SQLite로 settlement_net_by_line 검증. 핵심 계약(머니룰):
#   ① net = Σ(SALE) − Σ(REFUND) sale_amount — REFUND 양수 미러라 빼야 net(라이브 확정)
#   ② 그레인 = (order_id, vendor_item_id) 라인 (codex P1#2: 부분 옵션 정산 정확)
#   ③ 윈도우=sale_date, NULL 제외; 3P 기본 계정만, account_keys 필터
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import CoupangRevenueFee
from app.services.coupang.settlement_revenue_source import settlement_net_by_line

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


def test_sale_only_line(db):
    _fee(db, order_id="O1", vid="v1", sale_amount=16900)
    db.commit()
    out = settlement_net_by_line(db, *WIN)
    assert out[("O1", "v1")] == D(16900)


def test_full_refund_line_nets_zero(db):
    """전액환불: SALE 179,000 + REFUND 179,000(양수 미러) → net 0."""
    _fee(db, order_id="O1", vid="v1", sale_type="SALE", sale_amount=179000)
    _fee(db, order_id="O1", vid="v1", sale_type="REFUND", sale_amount=179000)
    db.commit()
    out = settlement_net_by_line(db, *WIN)
    assert out.get(("O1", "v1"), _Z) == _Z


def test_partial_refund_line(db):
    """부분환불: SALE 20,000 − REFUND 8,000 = 12,000."""
    _fee(db, order_id="O1", vid="v1", sale_type="SALE", sale_amount=20000)
    _fee(db, order_id="O1", vid="v1", sale_type="REFUND", sale_amount=8000)
    db.commit()
    out = settlement_net_by_line(db, *WIN)
    assert out[("O1", "v1")] == D(12000)


def test_line_grain_same_order_two_options(db):
    """같은 주문, 옵션 2개 → 라인별 분리(codex P1#2 그레인)."""
    _fee(db, order_id="O1", vid="v1", sale_amount=10000)
    _fee(db, order_id="O1", vid="v2", sale_amount=5000)
    db.commit()
    out = settlement_net_by_line(db, *WIN)
    assert out[("O1", "v1")] == D(10000)
    assert out[("O1", "v2")] == D(5000)


def test_account_filter_and_3p_default(db):
    _fee(db, order_id="O1", vid="v1", account="COUPANG_WING1", sale_amount=10000)
    _fee(db, order_id="O2", vid="v2", account="COUPANG_WING2", sale_amount=20000)
    _fee(db, order_id="O3", vid="v3", account="COUPANG_RG1", sale_amount=5000)
    db.commit()
    # 기본(None) = 3P만(WING1·WING2), RG 제외
    out = settlement_net_by_line(db, *WIN)
    assert ("O1", "v1") in out and ("O2", "v2") in out and ("O3", "v3") not in out
    # account_keys 필터
    only1 = settlement_net_by_line(db, *WIN, account_keys=["COUPANG_WING1"])
    assert ("O1", "v1") in only1 and ("O2", "v2") not in only1


def test_null_and_window_bounds(db):
    _fee(db, order_id="O1", vid="v1", sale_date=None, sale_amount=10000)        # NULL 제외
    _fee(db, order_id="O2", vid="v2", sale_date=date(2026, 5, 31), sale_amount=20000)  # 윈도우 밖
    _fee(db, order_id="O3", vid="v3", sale_date=date(2026, 6, 8), sale_amount=5000)
    db.commit()
    out = settlement_net_by_line(db, *WIN)
    assert list(out.keys()) == [("O3", "v3")]
