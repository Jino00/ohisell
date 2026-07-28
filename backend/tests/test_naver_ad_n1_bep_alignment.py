# test_naver_ad_n1_bep_alignment.py — N1 BEP 배송방식 인지형 정합 (D-NAO-99, ref 42)
# 커버: ① 적응형 최근 창 선택(사다리·nmin·전기간 폴백) ② 판매가·N배송 혼합비만 적응형,
#   평균수량·수취배송비는 넓은 창 유지 ③ product_commission SA(주문관리·기저 매출연동 분해,
#   N배송 프리미엄 1.5%p 제거·재부과, 표본<5 계정 폴백, 표본 없음 → 계정 단일 요율 폴백)
#   ④ calculate_bep 통합 손계산.
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Channel, NaverProductBep, NaverSettlementCase, Order, ProductChannelMapping, ProductMaster,
)
from app.services.naver_ad import bep_calculator, product_commission
from app.utils.kst import kst_today

AG = "ARRIVAL_GUARANTEE"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _order(db, pid, *, days_ago, price="10000", qty=1, nbaesong=False, collected=None, n=1):
    for i in range(n):
        db.add(Order(
            channel_id=6, platform_product_id=pid, order_number=f"o-{pid}-{days_ago}-{i}-{price}",
            quantity=qty, selling_price=Decimal(price),
            shipping_cost=Decimal(collected) if collected is not None else None,
            order_date=kst_today() - timedelta(days=days_ago),
            delivery_attribute_type=AG if nbaesong else "TODAY",
        ))


# ══════════════════════════════════════════════════════════════════
# ① 적응형 창 선택
# ══════════════════════════════════════════════════════════════════
def _rows(*specs):
    """(days_ago, nbaesong) 목록 → _adaptive_rows 입력 형태."""
    return sorted(
        [{"order_date": kst_today() - timedelta(days=d), "nbaesong": nb, "quantity": 1,
          "unit_price": Decimal("10000"), "collected": Decimal("0")} for d, nb in specs],
        key=lambda r: r["order_date"], reverse=True)


def test_adaptive_window_picks_shortest_window_meeting_nmin():
    rows = _rows(*[(1, False)] * 10, *[(50, False)] * 10)
    sub, window = bep_calculator._adaptive_rows(rows, 10)
    assert window == 7 and len(sub) == 10        # 7일 창이 이미 10건 → 최단 창 채택


def test_adaptive_window_falls_down_the_ladder():
    # 7d 3건 / 14d 누적 6건 / 30d 누적 12건 → nmin=10이면 30일 창
    rows = _rows(*[(2, False)] * 3, *[(10, False)] * 3, *[(25, False)] * 6)
    sub, window = bep_calculator._adaptive_rows(rows, 10)
    assert window == 30 and len(sub) == 12


def test_adaptive_window_falls_back_to_alltime_when_all_windows_thin():
    rows = _rows((3, False), (200, False))       # 어떤 창도 nmin 미달
    sub, window = bep_calculator._adaptive_rows(rows, 10)
    assert window is None and len(sub) == 2


def test_adaptive_window_nmin_changes_selection():
    rows = _rows(*[(1, False)] * 5, *[(20, False)] * 20)
    assert bep_calculator._adaptive_rows(rows, 5)[1] == 7    # 5건이면 7일 창으로 충분
    assert bep_calculator._adaptive_rows(rows, 10)[1] == 30  # 10건이면 30일 창까지 내려간다


def test_adaptive_nmin_default_is_ten():
    """Jino 승인 처분(ref 42 §8-2): nmin=10."""
    assert bep_calculator._ADAPTIVE_MIN_ORDERS == 10
    assert bep_calculator._ADAPTIVE_WINDOW_LADDER == (7, 14, 30, 60, 120)


# ══════════════════════════════════════════════════════════════════
# ② 판매가·물류비 — 레짐 변수만 최근 창
# ══════════════════════════════════════════════════════════════════
def test_unit_price_uses_recent_window_not_120d_median(db):
    # 인하 전 18,900 × 40건(90일 전) vs 인하 후 15,900 × 12건(3일 전).
    # 120일 median이면 18,900이 이기지만, 적응형 창(7d 미달 → 14d 12건)은 15,900을 본다.
    _order(db, "p1", days_ago=90, price="18900", n=40)
    _order(db, "p1", days_ago=10, price="15900", n=12)
    db.commit()
    assert bep_calculator._unit_prices(db)["p1"] == Decimal("15900")


def test_unit_price_keeps_wide_window_when_recent_sample_thin(db):
    # 최근 주문이 nmin 미달 → 전기간 폴백(잡음 방지). 3건뿐이라 median은 전체 기준.
    _order(db, "p1", days_ago=90, price="18900", n=40)
    _order(db, "p1", days_ago=2, price="15900", n=3)
    db.commit()
    assert bep_calculator._unit_prices(db)["p1"] == Decimal("18900")


def test_logistics_uses_recent_nbaesong_share(db):
    # 오래된 일반배송 40건 + 최근 N배송 12건 → 혼합비는 최근 창 기준 100%
    _order(db, "p1", days_ago=90, n=40)
    _order(db, "p1", days_ago=3, nbaesong=True, n=12)
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)["p1"]
    assert out["nb_share"] == Decimal("1")
    assert out["shipping"] == Decimal("3020")     # 1,900 + 1,120 × 1
    # ★평균 수량·수취 배송비는 넓은 창(52건 전부) — 혼합비만 최근 창이다
    assert out["orders"] == 52


def test_logistics_paid_is_linear_in_nbaesong_share(db):
    # 최근 창 10건 중 5건 N배송 → 지불 = 1,900 + 1,120 × 0.5 = 2,460
    _order(db, "p1", days_ago=3, nbaesong=True, n=5)
    _order(db, "p1", days_ago=4, nbaesong=False, n=5)
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)["p1"]
    assert out["nb_share"] == Decimal("0.5")
    assert out["shipping"] == Decimal("2460")
    assert out["logistics"] == Decimal("2460.00")


def test_logistics_wide_window_still_drives_qty_and_collected(db):
    # 넓은 창 안의 옛 주문(수량 3·수취 900)이 평균에 그대로 반영된다(창 단축 잡음 차단).
    _order(db, "p1", days_ago=100, qty=3, collected="900", n=1)
    _order(db, "p1", days_ago=1, qty=1, collected="0", n=1)
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)["p1"]
    assert out["avg_qty"] == Decimal("2")          # (3+1)/2
    assert out["collected"] == Decimal("450")      # (900+0)/2
    assert out["net_ship"] == Decimal("1450")      # 1,900 − 450
    assert out["logistics"] == Decimal("725.00")   # 1,450 / 2


# ══════════════════════════════════════════════════════════════════
# ③ product_commission SA
# ══════════════════════════════════════════════════════════════════
def _settled(db, pid, *, i, gross, mgmt_rate, interlock_rate, nbaesong=False, days_ago=10):
    g = Decimal(str(gross))
    oid = f"ord-{pid}-{i}"
    db.add(Order(channel_id=6, platform_product_id=pid, order_number=oid, quantity=1,
                 selling_price=g, order_date=kst_today() - timedelta(days=days_ago),
                 delivery_attribute_type=AG if nbaesong else "TODAY"))
    db.add(NaverSettlementCase(
        product_order_id=f"po-{pid}-{i}", order_id=oid, product_id=pid,
        product_order_type="PROD_ORDER", pay_settle_amount=g,
        total_pay_commission=-(g * Decimal(str(mgmt_rate))),
        selling_interlock_commission=-(g * Decimal(str(interlock_rate))),
        free_installment_commission=Decimal("0"),
    ))


def test_measure_decomposes_mgmt_and_base_interlock(db):
    # 비 N배송 10건: 주문관리 2.724% · 매출연동 3.0%
    for i in range(10):
        _settled(db, "p1", i=i, gross=10000, mgmt_rate="0.02724", interlock_rate="0.03")
    db.commit()
    t = product_commission.measure(db)
    assert t.available
    comp = t.products["p1"]
    assert comp.rows == 10
    assert comp.mgmt_rate == Decimal("0.02724")
    assert comp.base_interlock_rate == Decimal("0.03")
    rate, basis = t.rate_for("p1")
    assert basis == "delivery_case"
    assert rate == Decimal("0.05724")


def test_measure_strips_nbaesong_premium_from_base_interlock(db):
    # N배송 10건: 매출연동 4.5% = 기저 3.0% + 프리미엄 1.5%p → 기저만 3.0%로 환산돼야 한다
    for i in range(10):
        _settled(db, "p1", i=i, gross=10000, mgmt_rate="0.02724", interlock_rate="0.045",
                 nbaesong=True)
    db.commit()
    t = product_commission.measure(db)
    assert t.products["p1"].base_interlock_rate == Decimal("0.03")
    # 혼합비 0(일반배송 기준) → 프리미엄 미부과
    assert t.rate_for("p1")[0] == Decimal("0.05724")
    # 혼합비 100% → 프리미엄 전액 재부과 = 실측 그대로
    assert t.rate_for("p1", Decimal("1"))[0] == Decimal("0.07224")
    # 혼합비 절반 → 선형
    assert t.rate_for("p1", Decimal("0.5"))[0] == Decimal("0.06474")


def test_measure_falls_back_to_account_when_product_sample_thin(db):
    for i in range(10):  # 계정 표본을 만드는 다른 상품
        _settled(db, "p1", i=i, gross=10000, mgmt_rate="0.02724", interlock_rate="0.01")
    for i in range(3):   # 표본 3건(<5) → 계정 폴백
        _settled(db, "p2", i=i, gross=10000, mgmt_rate="0.02724", interlock_rate="0.03")
    db.commit()
    t = product_commission.measure(db)
    rate, basis = t.rate_for("p2")
    assert basis == "delivery_acct"
    acct_rate, _ = t.rate_for(None)
    assert rate == acct_rate
    # 미지의 상품도 같은 계정 폴백
    assert t.rate_for("p-unknown")[0] == acct_rate


def test_measure_unavailable_without_settlement(db):
    t = product_commission.measure(db)
    assert not t.available


def test_measure_ignores_unmatched_settlement_rows(db):
    # orders와 조인되지 않는 정산 행은 상품 귀속이 불가 → 실측 표본에 들어가지 않는다
    db.add(NaverSettlementCase(product_order_id="po-x", order_id="no-such-order", product_id="p9",
                               product_order_type="PROD_ORDER", pay_settle_amount=Decimal("10000"),
                               total_pay_commission=Decimal("-272"),
                               selling_interlock_commission=Decimal("-300"),
                               free_installment_commission=Decimal("0")))
    db.commit()
    assert not product_commission.measure(db).available


def test_measure_rejects_implausible_rate(db):
    for i in range(10):  # 수수료 40% — 데이터 이상
        _settled(db, "p1", i=i, gross=10000, mgmt_rate="0.20", interlock_rate="0.20")
    db.commit()
    assert not product_commission.measure(db).available


# ══════════════════════════════════════════════════════════════════
# ④ calculate_bep 통합
# ══════════════════════════════════════════════════════════════════
def test_calculate_bep_delivery_aware_hand_computed(db):
    """손계산 하드코딩 대조(거울 방지).

    입력: 최근 10건 전부 N배송·판매가 20,000·수량 1·수취 0 / 원가 5,000
          정산 실측 10건: 주문관리 2.724% · 매출연동 4.5%(N배송) → 기저 3.0%
    손계산: 혼합비 1.0 → 수수료율 = 0.02724 + 0.03 + 0.015 = 0.07224
            물류비 = 1,900 + 1,120×1 = 3,020
            20,000×0.07224 = 1,444.8 → 20,000−1,444.8−5,000−3,020 = 10,535.2
            공헌이익 = 10,535.2 / 1.1 = 9,577.4545… → 9,577.45(2자리)
            bep = 20,000 / 9,577.45 = 2.08823… → 2.0882(4자리)
    """
    pm = ProductMaster(internal_sku="SKU-N1", product_name="N1", cost_price=Decimal("5000"))
    db.add(pm)
    db.flush()
    db.add(ProductChannelMapping(product_id=pm.id, channel_id=6, channel_product_id="p1",
                                 channel_product_name="N1", selling_price=Decimal("0"),
                                 is_active=True))
    for i in range(10):
        _settled(db, "p1", i=i, gross=20000, mgmt_rate="0.02724", interlock_rate="0.045",
                 nbaesong=True, days_ago=3)
    db.commit()

    res = bep_calculator.calculate_bep(db)
    assert res["commission_basis"] == "delivery_acct"   # 대표값은 계정 실측(배송 중립)
    assert res["product_rate_rows"] == 1
    row = db.query(NaverProductBep).filter(NaverProductBep.channel_product_id == "p1").one()
    assert row.commission_basis == "delivery_case"
    assert row.commission_rate == Decimal("0.0722")     # 0.07224 → 4자리 반올림
    assert row.logistics_cost == Decimal("3020.00")
    assert row.selling_price == Decimal("20000")
    expected_cm = ((Decimal("20000") - Decimal("20000") * Decimal("0.07224")
                    - Decimal("5000") - Decimal("3020")) / Decimal("1.1")).quantize(Decimal("0.01"))
    assert row.contribution_margin == expected_cm
    assert row.bep_roas == Decimal("2.0882")


def test_calculate_bep_falls_back_to_account_rate_without_settlement(db):
    """정산 표본이 없으면 종전 계정 단일 요율 경로 그대로(회귀 0)."""
    db.add(Channel(id=6, name="네이버", code="NAVER", platform="naver",
                   commission_rate=Decimal("5.0")))
    pm = ProductMaster(internal_sku="SKU-F", product_name="F", cost_price=Decimal("5000"))
    db.add(pm)
    db.flush()
    db.add(ProductChannelMapping(product_id=pm.id, channel_id=6, channel_product_id="p1",
                                 channel_product_name="F", selling_price=Decimal("0"),
                                 is_active=True))
    _order(db, "p1", days_ago=1, price="10000")
    db.commit()
    res = bep_calculator.calculate_bep(db)
    assert res["commission_basis"] == "blended"
    assert res["product_rate_rows"] == 0
    row = db.query(NaverProductBep).filter(NaverProductBep.channel_product_id == "p1").one()
    assert row.commission_rate == Decimal("0.0500")
    assert row.logistics_cost == Decimal("1900.00")
