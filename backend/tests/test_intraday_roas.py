# test_intraday_roas.py — RL2(D-NAO-60) 시간당 추정 ROAS 신호 SA 단위 테스트
# 커버: estimated_intraday_roas(순수 계산)·estimated_intraday_profit(순수 계산)·
#   adgroup_unit_price(NaverAdgroupProduct×NaverProductBep 매출가중, campaign_target_resolver
#   재사용 경로). 픽스처 패턴은 tests/test_naver_ad_d_nao_57.py의 resolver ② 테스트를 따른다.
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdgroupProduct, NaverProductBep, Order
from app.services.naver_ad import intraday_roas


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


# ══════════════════════════════════════════════════════════════════
# estimated_intraday_roas — 순수 계산
# ══════════════════════════════════════════════════════════════════
def test_estimated_intraday_roas_normal():
    curve = [
        {"hour": "10", "imp": 100, "clk": 5, "cost": 5000, "conv_cnt": 1, "avg_rank": 2.0},
        {"hour": "11", "imp": 100, "clk": 5, "cost": 5000, "conv_cnt": 1, "avg_rank": 2.0},
    ]
    # Σconv_cnt=2, price=20000 → 매출 40000 / Σcost 10000 = roas 4.0
    roas = intraday_roas.estimated_intraday_roas(curve, Decimal("20000"))
    assert roas == Decimal("4.0000")


def test_estimated_intraday_roas_cost_zero_returns_none():
    curve = [{"hour": "10", "imp": 0, "clk": 0, "cost": 0, "conv_cnt": 0, "avg_rank": None}]
    assert intraday_roas.estimated_intraday_roas(curve, Decimal("20000")) is None


def test_estimated_intraday_roas_price_none_returns_none():
    curve = [{"hour": "10", "imp": 100, "clk": 5, "cost": 5000, "conv_cnt": 1, "avg_rank": 2.0}]
    assert intraday_roas.estimated_intraday_roas(curve, None) is None


def test_estimated_intraday_roas_defends_missing_conv_cnt_key():
    """구곡선(conv_cnt 키 없는 레거시 형태)도 방어적으로 0 취급."""
    curve = [{"hour": "10", "imp": 100, "clk": 5, "cost": 5000, "avg_rank": 2.0}]
    roas = intraday_roas.estimated_intraday_roas(curve, Decimal("20000"))
    assert roas == Decimal("0.0000")  # conv 0건 → 매출 0 / cost 5000


# ══════════════════════════════════════════════════════════════════
# estimated_intraday_profit — 순수 계산
# ══════════════════════════════════════════════════════════════════
def test_estimated_intraday_profit_normal():
    curve = [
        {"hour": "10", "imp": 100, "clk": 5, "cost": 5000, "conv_cnt": 1, "avg_rank": 2.0},
        {"hour": "11", "imp": 100, "clk": 5, "cost": 5000, "conv_cnt": 1, "avg_rank": 2.0},
    ]
    # Σconv_cnt=2 × margin 8000 = 16000 − Σcost 10000 = 6000
    profit = intraday_roas.estimated_intraday_profit(curve, Decimal("8000"))
    assert profit == Decimal("6000")


def test_estimated_intraday_profit_no_conversion_is_negative():
    curve = [{"hour": "10", "imp": 100, "clk": 5, "cost": 10000, "conv_cnt": 0, "avg_rank": 2.0}]
    profit = intraday_roas.estimated_intraday_profit(curve, Decimal("8000"))
    assert profit == Decimal("-10000")


def test_estimated_intraday_profit_margin_none_returns_none():
    curve = [{"hour": "10", "imp": 100, "clk": 5, "cost": 5000, "conv_cnt": 1, "avg_rank": 2.0}]
    assert intraday_roas.estimated_intraday_profit(curve, None) is None


def test_estimated_intraday_profit_defends_missing_conv_cnt_key():
    curve = [{"hour": "10", "imp": 100, "clk": 5, "cost": 5000, "avg_rank": 2.0}]
    profit = intraday_roas.estimated_intraday_profit(curve, Decimal("8000"))
    assert profit == Decimal("-5000")  # conv 0건 → 매출 0 − cost 5000


# ══════════════════════════════════════════════════════════════════
# adgroup_unit_price — NaverAdgroupProduct×NaverProductBep 매출가중
# ══════════════════════════════════════════════════════════════════
def test_adgroup_unit_price_weighted_average(db):
    db.add(NaverAdgroupProduct(adgroup_id="grp-1", campaign_id="cmp-shop", mall_product_id="p1"))
    db.add(NaverAdgroupProduct(adgroup_id="grp-1", campaign_id="cmp-shop", mall_product_id="p2"))
    db.add(NaverProductBep(
        channel_id=6, channel_product_id="p1", has_cost=True,
        selling_price=Decimal("15000"), contribution_margin=Decimal("6000"), bep_roas=Decimal("2.5"),
    ))
    db.add(NaverProductBep(
        channel_id=6, channel_product_id="p2", has_cost=True,
        selling_price=Decimal("25000"), contribution_margin=Decimal("10000"), bep_roas=Decimal("2.5"),
    ))
    db.add(Order(channel_id=6, platform_product_id="p1", selling_price=Decimal("3000"), quantity=1,
                 order_date=date(2026, 7, 1), order_number="o1"))
    db.add(Order(channel_id=6, platform_product_id="p2", selling_price=Decimal("1000"), quantity=1,
                 order_date=date(2026, 7, 1), order_number="o2"))
    db.commit()

    r = intraday_roas.adgroup_unit_price(db, "grp-1")
    assert r["source"] == "product_bep"
    # price: (15000*3000 + 25000*1000)/4000 = 17500
    assert r["price"] == Decimal("17500")
    # margin: (6000*3000 + 10000*1000)/4000 = 7000
    assert r["margin"] == Decimal("7000")
    # bep_roas: (2.5*3000 + 2.5*1000)/4000 = 2.5 (동일값이라 자명하지만 가중경로 검증)
    assert r["bep_roas"] == Decimal("2.5")


def test_adgroup_unit_price_unavailable_when_no_mapping(db):
    r = intraday_roas.adgroup_unit_price(db, "grp-none")
    assert r == {"price": None, "margin": None, "bep_roas": None, "source": "unavailable"}


def test_adgroup_unit_price_unavailable_when_no_cost(db):
    """매핑은 있으나 원가 미확인(has_cost=False) 상품뿐이면 unavailable (추정 금지)."""
    db.add(NaverAdgroupProduct(adgroup_id="grp-1", campaign_id="cmp-shop", mall_product_id="p1"))
    db.add(NaverProductBep(
        channel_id=6, channel_product_id="p1", has_cost=False,
        selling_price=Decimal("15000"), contribution_margin=Decimal("6000"), bep_roas=None,
    ))
    db.commit()
    r = intraday_roas.adgroup_unit_price(db, "grp-1")
    assert r["source"] == "unavailable"
    assert r["price"] is None
