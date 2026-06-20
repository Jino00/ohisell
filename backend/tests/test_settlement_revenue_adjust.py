# test_settlement_revenue_adjust.py — net_profit 매출기준 정산화 Harness (트랙 revenue-wing-truth S4).
# 순수 산식 + 인메모리 DB 시나리오. 핵심 머니룰(라인 그레인):
#   ① 정산==우리 → 보정 0 (무회귀)
#   ② 정산이 취소 잡음(refund net0), 우리 active → 음수 보정(net_profit 정확 감소)
#   ③ 성숙 라인 반품 → 정산 net 환불 반영, 라인 반품차감 되돌림 → 이중차감 0(codex P1#1)
#   ④ 부분 옵션 정산: 정산된 옵션만 스왑, 미정산 옵션 폴백(codex P1#2)
#   ⑤ 정산만 있고 active 아닌 라인(미동기) → 스왑 안 함
#   ⑥ 비-3P 계정 → 0 (D-13); account=None == ΣWING(등가성)
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Channel, CoupangRevenueFee, CoupangReturnItem, Order
from app.services.coupang.settlement_revenue_adjust import (
    compute_line_adjustment,
    settlement_revenue_adjustment,
)
from datetime import date

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
        receipt_id=f"R{onum}-{vid}", order_id=onum, vendor_item_id=vid, account_key=account,
        vendor_id=vendor, receipt_type="RETURN", cancel_count=qty, withdrawn=withdrawn,
        requested_at=datetime(requested.year, requested.month, requested.day, 12, 0),
    ))


ACC_WING1 = {"channel_ids": [1], "vendor_id": "A01564720", "account_key": "COUPANG_WING1"}
ACC_ALL = {"channel_ids": None, "vendor_id": None, "account_key": None}


# ── 순수 산식 ──
def test_compute_only_settled_active_lines():
    settle = {("O1", "v1"): D(100), ("O2", "v2"): D(50)}  # v2는 active 아님
    active = {("O1", "v1"): D(120), ("O3", "v3"): D(30)}  # v3는 미정산
    r = compute_line_adjustment(settle, active, {}, {"v1": D(120)})
    # O1/v1만 스왑: 100 − 120 = −20. O2/v2(정산만)·O3/v3(active만) 스킵.
    assert r["adjustment"] == D(-20)
    assert r["matured_lines"] == 1


def test_compute_return_addback():
    """성숙 라인 반품: our_net = active − unit×qty, 정산도 환불반영 → 0."""
    settle = {("O1", "v1"): D(10000)}        # SALE20000 − REFUND10000
    active = {("O1", "v1"): D(20000)}
    retq = {("O1", "v1"): 1}
    r = compute_line_adjustment(settle, active, retq, {"v1": D(10000)})  # 단가 10000
    assert r["adjustment"] == _Z  # 10000 − (20000 − 10000) = 0


# ── DB 시나리오 ──
def test_clean_no_change(db):
    """① 정산==우리, 반품 없음 → 0."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _order(db, cid=1, onum="O1", vid="v1", price=16900)
    _fee(db, onum="O1", vid="v1", amount=16900)
    db.commit()
    r = settlement_revenue_adjustment(db, *WIN, ACC_WING1, {"v1": D(16900)})
    assert r["adjustment"] == _Z
    assert r["matured_lines"] == 1


def test_settlement_caught_cancellation(db):
    """② 정산 전액환불(net0), 우리 active → −16900."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _order(db, cid=1, onum="O1", vid="v1", price=16900)
    _fee(db, onum="O1", vid="v1", sale_type="SALE", amount=16900)
    _fee(db, onum="O1", vid="v1", sale_type="REFUND", amount=16900)
    db.commit()
    r = settlement_revenue_adjustment(db, *WIN, ACC_WING1, {"v1": D(16900)})
    assert r["adjustment"] == D(-16900)


def test_matured_return_no_double_subtract(db):
    """③ 성숙 라인 반품: 보정 0(이중차감 없음)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _order(db, cid=1, onum="O1", vid="v1", price=20000, qty=2)  # 단가 10000
    _ret(db, onum="O1", vid="v1", qty=1)
    _fee(db, onum="O1", vid="v1", sale_type="SALE", amount=20000)
    _fee(db, onum="O1", vid="v1", sale_type="REFUND", amount=10000)
    db.commit()
    r = settlement_revenue_adjustment(db, *WIN, ACC_WING1, {"v1": D(10000)})
    assert r["adjustment"] == _Z


def test_partial_option_settlement(db):
    """④ 한 주문 옵션 2개 중 v1만 정산 → v1만 스왑(취소 −10000), v2 폴백(미정산)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _order(db, cid=1, onum="O1", vid="v1", price=10000)
    _order(db, cid=1, onum="O1", vid="v2", price=8000)  # 같은 주문 다른 옵션
    # v1 정산: 전액환불(취소). v2 미정산(아직 정산 안 됨).
    _fee(db, onum="O1", vid="v1", sale_type="SALE", amount=10000)
    _fee(db, onum="O1", vid="v1", sale_type="REFUND", amount=10000)
    db.commit()
    r = settlement_revenue_adjustment(db, *WIN, ACC_WING1, {"v1": D(10000), "v2": D(8000)})
    # v1만 스왑: net0 − 10000 = −10000. v2는 정산 없어 폴백(불변).
    assert r["adjustment"] == D(-10000)
    assert r["matured_lines"] == 1


def test_settled_not_active_skipped(db):
    """⑤ 정산만 있고 우리 주문엔 없음(미동기) → 스왑 안 함(0)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _fee(db, onum="OX", vid="vx", amount=9999)  # 우리 orders에 없음
    db.commit()
    r = settlement_revenue_adjustment(db, *WIN, ACC_WING1, {})
    assert r["adjustment"] == _Z
    assert r["matured_lines"] == 0


def test_non_3p_skipped(db):
    """⑥ 비-3P(RG) → 0."""
    _ch(db, 3, "COUPANG_RG1", "오픽스")
    _order(db, cid=3, onum="O1", vid="v1", price=16900)
    db.commit()
    acc_rg = {"channel_ids": [3], "vendor_id": "A01564720", "account_key": "COUPANG_RG1"}
    r = settlement_revenue_adjustment(db, *WIN, acc_rg, {"v1": D(16900)})
    assert r["adjustment"] == _Z
    assert "non-3p" in r["basis"]


def test_account_none_equals_sum_of_wings(db):
    """⑥ account=None == WING1 + WING2 (등가성)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _ch(db, 2, "COUPANG_WING2", "오하이테크")
    # WING1: 정산 취소 −16900
    _order(db, cid=1, onum="O1", vid="v1", price=16900)
    _fee(db, onum="O1", vid="v1", sale_type="SALE", amount=16900)
    _fee(db, onum="O1", vid="v1", sale_type="REFUND", amount=16900)
    # WING2: 깨끗(0)
    _order(db, cid=2, onum="O2", vid="v2", price=10000)
    _fee(db, onum="O2", vid="v2", account="COUPANG_WING2", vendor="A01029796", amount=10000)
    db.commit()
    upv = {"v1": D(16900), "v2": D(10000)}
    none_adj = settlement_revenue_adjustment(db, *WIN, ACC_ALL, upv)["adjustment"]
    w1 = settlement_revenue_adjustment(db, *WIN, ACC_WING1, upv)["adjustment"]
    w2 = settlement_revenue_adjustment(
        db, *WIN, {"channel_ids": [2], "vendor_id": "A01029796", "account_key": "COUPANG_WING2"}, upv
    )["adjustment"]
    assert none_adj == w1 + w2 == D(-16900)
