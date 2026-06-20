# test_settlement_revenue_adjust.py — net_profit 매출기준 정산화 Harness (트랙 revenue-wing-truth S4).
# 순수 로직 + 인메모리 DB 시나리오. 핵심 머니룰:
#   ① 성숙일 정산==우리 → 보정 0 (무변화·무회귀)
#   ② 정산이 취소를 잡고 우리는 못 잡음 → 음수 보정(net_profit 정확 감소)
#   ③ 성숙일 반품 → 정산 net이 이미 환불 반영, 반품차감 되돌림으로 이중차감 0
#   ④ 미성숙일(active ⊄ settled) → 게이트로 보정 0(차이 있어도)
#   ⑤ 비-3P 계정 → 보정 0 (D-13)
#   ⑥ account=None → WING1+WING2 합산
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Channel, CoupangRevenueFee, CoupangReturnItem, Order
from app.services.coupang.settlement_revenue_adjust import (
    compute_settlement_adjustment,
    day_matured,
    settlement_revenue_adjustment,
)

_Z = Decimal(0)
D = lambda x: Decimal(str(x))  # noqa: E731
WIN = (date(2026, 6, 1), date(2026, 6, 30))
DAY = date(2026, 6, 8)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _ch(db, cid, code, company):
    db.add(Channel(id=cid, name=f"쿠팡_{code}", code=code, platform="coupang", company=company))


def _order(db, *, cid, onum, vid, price, qty=1, status="delivered", od=DAY):
    db.add(Order(
        channel_id=cid, order_number=onum, platform_product_id=vid,
        platform_order_line_id="", selling_price=D(price), quantity=qty,
        shipping_cost=_Z, order_date=datetime(od.year, od.month, od.day, 12, 0),
        status=status,
    ))


def _fee(db, *, onum, vid, account="COUPANG_WING1", vendor="A01564720",
         sale_type="SALE", amount, sd=DAY):
    db.add(CoupangRevenueFee(
        order_id=onum, vendor_item_id=vid, account_key=account, vendor_id=vendor,
        sale_type=sale_type, sale_date=sd, sale_price=D(amount), quantity=1,
        sale_amount=D(amount), service_fee=_Z, service_fee_vat=_Z,
        settlement_amount=D(amount), coupang_discount_coupon=_Z,
        seller_discount_coupon=_Z, downloadable_coupon=_Z, courantee_fee=_Z,
    ))


def _ret(db, *, onum, vid, account="COUPANG_WING1", vendor="A01564720",
         qty=1, requested=DAY, withdrawn=False):
    db.add(CoupangReturnItem(
        receipt_id=f"R{onum}", order_id=onum, vendor_item_id=vid, account_key=account,
        vendor_id=vendor, receipt_type="RETURN", cancel_count=qty, withdrawn=withdrawn,
        requested_at=datetime(requested.year, requested.month, requested.day, 12, 0),
    ))


ACC_WING1 = {"channel_ids": [1], "vendor_id": "A01564720", "account_key": "COUPANG_WING1"}
ACC_ALL = {"channel_ids": None, "vendor_id": None, "account_key": None}


# ── 순수 로직 ──
def test_day_matured_subset():
    assert day_matured({"A", "B"}, {"A", "B", "C"}) is True
    assert day_matured({"A", "B"}, {"A"}) is False
    assert day_matured(set(), {"A"}) is False  # 빈 active → 미성숙(매출0일 보정 안 함)


def test_compute_adjustment_only_matured():
    sett = {date(2026, 6, 7): D(100), date(2026, 6, 8): D(80)}
    ours = {date(2026, 6, 7): D(100), date(2026, 6, 8): D(95)}
    # 6/8만 성숙 → 80−95 = −15. 6/7은 미성숙이라 0 기여.
    assert compute_settlement_adjustment(sett, ours, {date(2026, 6, 8)}) == D(-15)
    # 둘 다 성숙 → (100−100)+(80−95) = −15
    assert compute_settlement_adjustment(sett, ours, set(sett)) == D(-15)
    # 성숙 없음 → 0
    assert compute_settlement_adjustment(sett, ours, set()) == _Z


# ── DB 시나리오 ──
def test_clean_matured_no_change(db):
    """① 정산==우리, 반품 없음 → 보정 0(무회귀)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _order(db, cid=1, onum="O1", vid="v1", price=16900)
    _fee(db, onum="O1", vid="v1", amount=16900)
    db.commit()
    r = settlement_revenue_adjustment(db, *WIN, ACC_WING1, {"v1": D(16900)})
    assert r["adjustment"] == _Z
    assert r["matured_days"] == 1


def test_settlement_caught_cancellation_we_missed(db):
    """② 정산은 전액환불 잡음(net0), 우리는 active로 셈 → 음수 보정 −16900."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _order(db, cid=1, onum="O1", vid="v1", price=16900)  # 우리: active(취소 미동기)
    _fee(db, onum="O1", vid="v1", sale_type="SALE", amount=16900)
    _fee(db, onum="O1", vid="v1", sale_type="REFUND", amount=16900)  # 정산: 환불됨
    db.commit()
    r = settlement_revenue_adjustment(db, *WIN, ACC_WING1, {"v1": D(16900)})
    assert r["adjustment"] == D(-16900)
    assert r["matured_days"] == 1


def test_matured_with_return_no_double_subtract(db):
    """③ 성숙일 반품: 우리 net=active−반품차감, 정산 net도 환불반영 → 보정 0(이중차감 없음)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _order(db, cid=1, onum="O1", vid="v1", price=20000, qty=2)  # 단가 10000
    _ret(db, onum="O1", vid="v1", qty=1)                         # 반품 1개 → 차감 10000
    _fee(db, onum="O1", vid="v1", sale_type="SALE", amount=20000)
    _fee(db, onum="O1", vid="v1", sale_type="REFUND", amount=10000)  # 부분환불 10000
    db.commit()
    # 우리 net = 20000 − 10000 = 10000; 정산 net = 20000 − 10000 = 10000 → 보정 0
    r = settlement_revenue_adjustment(db, *WIN, ACC_WING1, {"v1": D(10000)})
    assert r["adjustment"] == _Z


def test_unmatured_day_gated_zero(db):
    """④ active 주문이 정산에 없음(미성숙) → 게이트로 보정 0(차이 있어도)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _order(db, cid=1, onum="O1", vid="v1", price=16900)  # 정산 row 없음
    db.commit()
    r = settlement_revenue_adjustment(db, *WIN, ACC_WING1, {"v1": D(16900)})
    assert r["adjustment"] == _Z
    assert r["matured_days"] == 0


def test_non_3p_account_skipped(db):
    """⑤ 비-3P 계정(RG) → 보정 0(D-13)."""
    _ch(db, 3, "COUPANG_RG1", "오픽스")
    _order(db, cid=3, onum="O1", vid="v1", price=16900)
    db.commit()
    acc_rg = {"channel_ids": [3], "vendor_id": "A01564720", "account_key": "COUPANG_RG1"}
    r = settlement_revenue_adjustment(db, *WIN, acc_rg, {"v1": D(16900)})
    assert r["adjustment"] == _Z
    assert "non-3p" in r["basis"]


def test_account_none_aggregates_both_wings(db):
    """⑥ account=None: WING1(취소잡힘 −16900) + WING2(무변화 0) 합산 → −16900."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _ch(db, 2, "COUPANG_WING2", "오하이테크")
    # WING1: 정산이 취소 잡음
    _order(db, cid=1, onum="O1", vid="v1", price=16900)
    _fee(db, onum="O1", vid="v1", sale_type="SALE", amount=16900)
    _fee(db, onum="O1", vid="v1", sale_type="REFUND", amount=16900)
    # WING2: 깨끗
    _order(db, cid=2, onum="O2", vid="v2", price=10000)
    _fee(db, onum="O2", vid="v2", account="COUPANG_WING2", vendor="A01029796", amount=10000)
    db.commit()
    r = settlement_revenue_adjustment(db, *WIN, ACC_ALL, {"v1": D(16900), "v2": D(10000)})
    assert r["adjustment"] == D(-16900)
    assert r["matured_days"] == 2  # 계정×일자 단위(WING1 6/8 + WING2 6/8) — 등가성 위해 계정별 분리
