# test_revenue_canonical.py — 닫힌 과거일 매출 정본화 순수 계산 (트랙 revenue-wing-truth S2, D-1/D-9 A안).
# 머니룰 fixture(DB 없음). combine_canonical 순수함수만 검증 — 안분·윈도우분할·폴백·잔차.
from __future__ import annotations

from decimal import Decimal

from app.services.coupang.revenue_canonical import combine_canonical

_Z = Decimal(0)
D = lambda x: Decimal(str(x))  # noqa: E731


def test_3p_scale_down_apportions_by_ratio():
    """닫힌 3P: Wing(150) < 우리(200) → 0.75 안분. summary==Σby_option."""
    closed = {"A": {"p3": D(100), "rg": _Z}, "B": {"p3": D(100), "rg": _Z}}
    r = combine_canonical(closed, {}, {"gmv_3p": 150, "gmv_rg": 0})
    s = r["summary"]
    assert s["canonical_3p"] == D(150)
    assert s["canonical_total"] == D(150)
    assert s["factor_3p"] == D("0.75")
    assert r["by_option"]["A"] == D(75)
    assert r["by_option"]["B"] == D(75)
    assert s["apportion_residual"] == _Z
    assert s["wing_used"] is True


def test_rg_scale_down():
    """닫힌 RG: Wing(180) < 우리(200) → 0.9 안분, 3P 팩터와 독립."""
    closed = {"A": {"p3": _Z, "rg": D(200)}}
    r = combine_canonical(closed, {}, {"gmv_3p": 0, "gmv_rg": 180})
    assert r["summary"]["canonical_rg"] == D(180)
    assert r["summary"]["factor_rg"] == D("0.9")
    assert r["by_option"]["A"] == D(180)


def test_mixed_type_independent_factors():
    """같은 옵션이 3P·RG 양쪽 — 타입별 팩터 따로 적용."""
    closed = {"A": {"p3": D(100), "rg": D(100)}}
    r = combine_canonical(closed, {}, {"gmv_3p": 50, "gmv_rg": 200})
    # 3P 팩터 0.5, RG 팩터 2.0 → A = 50 + 200 = 250
    assert r["by_option"]["A"] == D(250)
    assert r["summary"]["canonical_3p"] == D(50)
    assert r["summary"]["canonical_rg"] == D(200)


def test_window_with_today_open_portion_orderbased():
    """닫힌부분=Wing, 당일부분=주문기반 그대로 가산."""
    closed = {"A": {"p3": D(100), "rg": _Z}}
    open_ = {"A": {"p3": D(50), "rg": _Z}}
    r = combine_canonical(closed, open_, {"gmv_3p": 80, "gmv_rg": 0})
    # 닫힌 80(Wing) + 당일 50(주문) = 130. 옵션 A = 100*0.8 + 50 = 130.
    assert r["summary"]["canonical_3p"] == D(130)
    assert r["summary"]["closed_3p"] == D(80)
    assert r["summary"]["open_3p"] == D(50)
    assert r["by_option"]["A"] == D(130)


def test_no_wing_falls_back_to_orderbased():
    """Wing 미적재(None) → 주문기반 폴백, 정본화 안 됨(wing_used=False)."""
    closed = {"A": {"p3": D(100), "rg": D(40)}}
    r = combine_canonical(closed, {}, None)
    assert r["summary"]["wing_used"] is False
    assert r["summary"]["canonical_3p"] == D(100)
    assert r["summary"]["canonical_rg"] == D(40)
    assert r["summary"]["factor_3p"] == D(1)
    assert r["by_option"]["A"] == D(140)


def test_open_only_no_closed_data():
    """닫힌 데이터 없음(당일만) — 전량 주문기반."""
    open_ = {"A": {"p3": D(30), "rg": _Z}}
    r = combine_canonical({}, open_, None)
    assert r["summary"]["canonical_total"] == D(30)
    assert r["by_option"]["A"] == D(30)


def test_residual_when_wing_exceeds_with_no_orders():
    """우리닫힌=0인데 Wing>0(주문 없는 매출) → 옵션 귀속 불가, 잔차로 가시화."""
    r = combine_canonical({}, {}, {"gmv_3p": 90, "gmv_rg": 0})
    assert r["summary"]["canonical_3p"] == D(90)
    assert r["summary"]["apportion_residual"] == D(90)  # by_option 비어 summary 전액 잔차
    assert sum(r["by_option"].values(), _Z) == _Z


def test_repeating_decimal_split_is_exact():
    """codex P1#2: 1을 3옵션에 배분(1/3 반복소수)해도 Σby_option==canonical 정확(최대잔여법)."""
    closed = {"A": {"p3": D(1), "rg": _Z}, "B": {"p3": D(1), "rg": _Z}, "C": {"p3": D(1), "rg": _Z}}
    r = combine_canonical(closed, {}, {"gmv_3p": 1, "gmv_rg": 0})
    assert r["summary"]["canonical_3p"] == D(1)
    assert sum(r["by_option"].values(), _Z) == D(1)        # 0.999... 아님
    assert r["summary"]["apportion_residual"] == _Z
    assert sorted(r["by_option"].values()) == [D(0), D(0), D(1)]  # 정수 won 배분


# ════════════════════════════════ DB 통합 (compute_canonical_revenue) ════════════════════════════════
from datetime import datetime, timedelta  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import Channel, CoupangProductItem, CoupangRgOrderItem, Order  # noqa: E402
from app.services.coupang import vendor_summary_sync as vss  # noqa: E402
from app.services.coupang.intelligence import compute_command_center  # noqa: E402
from app.services.coupang.revenue_canonical import compute_canonical_revenue  # noqa: E402
from app.utils.kst import kst_today  # noqa: E402


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _setup_opix(db):
    db.add(Channel(id=1, name="c1", code="COUPANG_WING1", platform="coupang", company="오픽스"))
    db.add(CoupangProductItem(vendor_item_id="V3P", account_key="COUPANG_WING1",
           vendor_id="A01564720", seller_product_id="SP1", item_name="opt3p", sale_price=_Z))
    db.add(CoupangProductItem(vendor_item_id="VRG", account_key="COUPANG_WING1",
           vendor_id="A01564720", seller_product_id="SP2", item_name="optrg", sale_price=_Z))


def test_db_closed_window_uses_wing_gmv(db):
    """단일 닫힌일: 우리 주문(3P 200k)보다 Wing(150k) 작음 → 정본=Wing, complete=True."""
    day = kst_today() - timedelta(days=3)
    od = datetime.combine(day, datetime.min.time()).replace(hour=12)
    _setup_opix(db)
    db.add(Order(channel_id=1, order_number="O1", platform_product_id="V3P",
           quantity=1, selling_price=D(200000), order_date=od, status="delivered"))
    vss.ingest_vendor_summary(db, "COUPANG_WING1", [
        {"date": day, "registration_type": "NORMAL", "gmv": 150000, "units_sold": 1}])
    db.commit()
    r = compute_canonical_revenue(db, day, day, "COUPANG_WING1")
    assert r["applicable"] is True
    assert r["summary"]["wing_used"] is True
    assert r["coverage"]["complete"] is True
    assert r["summary"]["canonical_3p"] == D(150000)   # Wing 정본(주문 200k 아님)
    assert r["summary"]["our_closed_3p"] == D(200000)
    assert r["by_option"]["V3P"] == D(150000)          # 단일 옵션 → 전액 안분


def test_db_window_with_today_splits(db):
    """닫힌부분=Wing, 당일부분=주문기반. 윈도우=어제~오늘."""
    today = kst_today()
    yest = today - timedelta(days=1)
    _setup_opix(db)
    # 어제 주문 300k (Wing 250k로 정본화), 오늘 주문 100k (그대로)
    db.add(Order(channel_id=1, order_number="OY", platform_product_id="V3P", quantity=1,
           selling_price=D(300000), order_date=datetime.combine(yest, datetime.min.time()).replace(hour=12), status="delivered"))
    db.add(Order(channel_id=1, order_number="OT", platform_product_id="V3P", quantity=1,
           selling_price=D(100000), order_date=datetime.combine(today, datetime.min.time()).replace(hour=10), status="delivered"))
    vss.ingest_vendor_summary(db, "COUPANG_WING1", [
        {"date": yest, "registration_type": "NORMAL", "gmv": 250000, "units_sold": 1}])
    db.commit()
    r = compute_canonical_revenue(db, yest, today, "COUPANG_WING1")
    assert r["closed_through"] == yest.isoformat()
    assert r["summary"]["closed_3p"] == D(250000)      # Wing
    assert r["summary"]["open_3p"] == D(100000)        # 오늘 주문
    assert r["summary"]["canonical_3p"] == D(350000)
    assert r["by_option"]["V3P"] == D(350000)          # 300k*(250/300) + 100k


def test_db_no_wing_falls_back(db):
    """Wing 미적재 닫힌일 → 주문기반 폴백(wing_used=False)."""
    day = kst_today() - timedelta(days=2)
    od = datetime.combine(day, datetime.min.time()).replace(hour=12)
    _setup_opix(db)
    db.add(Order(channel_id=1, order_number="O1", platform_product_id="V3P",
           quantity=1, selling_price=D(120000), order_date=od, status="delivered"))
    db.commit()
    r = compute_canonical_revenue(db, day, day, "COUPANG_WING1")
    assert r["summary"]["wing_used"] is False
    assert r["summary"]["canonical_3p"] == D(120000)   # 주문 그대로
    assert "폴백" in r["note"]


def test_db_future_window_no_today_leak(db):
    """codex P1#1: 미래 윈도우(내일~내일) 요청 시 오늘 주문이 새지 않음(open_start 클램프)."""
    today = kst_today()
    tomorrow = today + timedelta(days=1)
    _setup_opix(db)
    # 오늘 주문 99k — 미래 윈도우에 포함되면 안 됨
    db.add(Order(channel_id=1, order_number="OT", platform_product_id="V3P", quantity=1,
           selling_price=D(99000), order_date=datetime.combine(today, datetime.min.time()).replace(hour=10), status="delivered"))
    db.commit()
    r = compute_canonical_revenue(db, tomorrow, tomorrow, "COUPANG_WING1")
    assert r["applicable"] is False                  # 닫힌 과거일 없음
    assert r["summary"]["canonical_total"] == _Z     # 오늘 99k 누출 안 됨
    assert r["summary"]["open_3p"] == _Z


def test_db_partial_coverage_falls_back(db):
    """codex P2#3: Wing 부분 적재(6일 중 1일만) → 정본화 보류, 주문기반 폴백(과소계상 방지)."""
    today = kst_today()
    dto = today - timedelta(days=1)          # 어제
    dfrom = today - timedelta(days=6)        # 6일 윈도우(전부 닫힘)
    _setup_opix(db)
    for i, d in enumerate([dfrom + timedelta(days=k) for k in range(6)]):
        db.add(Order(channel_id=1, order_number=f"O{i}", platform_product_id="V3P", quantity=1,
               selling_price=D(100000), order_date=datetime.combine(d, datetime.min.time()).replace(hour=12), status="delivered"))
    # Wing은 dfrom 하루만 적재(부분)
    vss.ingest_vendor_summary(db, "COUPANG_WING1", [
        {"date": dfrom, "registration_type": "NORMAL", "gmv": 30000, "units_sold": 1}])
    db.commit()
    r = compute_canonical_revenue(db, dfrom, dto, "COUPANG_WING1")
    assert r["coverage"]["complete"] is False
    assert r["summary"]["wing_used"] is False            # 부분 → 정본화 안 함
    assert r["summary"]["canonical_3p"] == D(600000)     # 주문기반 폴백(30k로 과소계상 안 함)
    assert "부분 적재" in r["note"]


def test_db_read_only_net_profit_unchanged(db):
    """compute_canonical_revenue 호출 전후 net_profit 불변(A안 읽기전용)."""
    day = kst_today() - timedelta(days=3)
    od = datetime.combine(day, datetime.min.time()).replace(hour=12)
    _setup_opix(db)
    db.add(Order(channel_id=1, order_number="O1", platform_product_id="V3P",
           quantity=1, selling_price=D(200000), order_date=od, status="delivered"))
    vss.ingest_vendor_summary(db, "COUPANG_WING1", [
        {"date": day, "registration_type": "NORMAL", "gmv": 150000, "units_sold": 1}])
    db.commit()
    before = compute_command_center(db, day, day, "COUPANG_WING1")["account"]["summary"]["net_profit"]
    compute_canonical_revenue(db, day, day, "COUPANG_WING1")
    after = compute_command_center(db, day, day, "COUPANG_WING1")["account"]["summary"]["net_profit"]
    assert before == after
