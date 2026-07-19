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


# ══════════════════════════════════════════════════════════════════
# ★독립 Opus 리뷰 권고(RL3 GATE PASS) — 총이익 등가: (est_roas < bep_roas) ⟺ (est_profit < 0)
# ══════════════════════════════════════════════════════════════════
# 단일상품(price=20000·margin=8000)이면 bep_roas = price/margin = 2.5(D-NAO-1 BEP 정의와 동일
# 관계식, campaign_target_resolver가 매출가중으로 산출하는 값을 픽스처에서 직접 명시).
# 대수적 증명(단위테스트 밖 참고): cost>0일 때
#   est_roas < bep_roas
#   ⟺ conv_cnt*price/cost < price/margin        (price로 양변 나눔, price>0)
#   ⟺ conv_cnt/cost < 1/margin
#   ⟺ conv_cnt*margin < cost                     (cost*margin>0을 양변에 곱함)
#   ⟺ conv_cnt*margin - cost < 0
#   ⟺ est_profit < 0
# 이 항등식이 RL3 고삐 트리거(est_roas<bep)가 곧 "추정 총이익 음수"를 뜻함을 보장한다
# (D-NAO-59 총이익 절대액 극대화 정합 — ROAS 신호로 손절해도 실제로는 총이익 신호와 동치).
_PRICE = Decimal("20000")
_MARGIN = Decimal("8000")
_BEP_ROAS = _PRICE / _MARGIN  # = 2.5


@pytest.mark.parametrize(
    "label,conv_cnt,cost",
    [
        ("이익(+) 소량전환·저비용", 3, 20000),  # roas=3.0>2.5·profit=+4000
        ("이익(+) 대량전환", 10, 70000),  # roas≈2.857>2.5·profit=+10000
        ("손익분기(=) 정확히 BEP", 1, 8000),  # roas=2.5==2.5·profit=0
        ("손실(-) 저전환", 1, 10000),  # roas=2.0<2.5·profit=-2000
        ("손실(-) 무전환", 0, 100),  # roas=0<2.5·profit=-100
        ("손실(-) 소량전환·고비용", 2, 30000),  # roas=1.333<2.5·profit=-14000
    ],
)
def test_estimated_roas_bep_and_profit_sign_agree(label, conv_cnt, cost):
    """단일 시간대 곡선 — (est_roas < bep_roas)와 (est_profit < 0)가 항상 일치한다."""
    curve = [{"hour": "10", "imp": 100, "clk": 5, "cost": cost, "conv_cnt": conv_cnt, "avg_rank": 2.0}]
    est_roas = intraday_roas.estimated_intraday_roas(curve, _PRICE)
    est_profit = intraday_roas.estimated_intraday_profit(curve, _MARGIN)
    assert est_roas is not None and est_profit is not None
    assert (est_roas < _BEP_ROAS) == (est_profit < 0), (
        f"{label}: est_roas={est_roas} bep_roas={_BEP_ROAS} est_profit={est_profit}"
    )


def test_estimated_roas_bep_and_profit_sign_agree_across_accumulated_hh24_curve():
    """RL3 실사용 형태 — 여러 시간대(hh24)에 걸쳐 누적된 곡선에서도 등가가 성립한다
    (estimated_intraday_roas/profit는 둘 다 Σconv_cnt·Σcost를 쓰므로 선형이라 자명하지만,
    RL3가 실제로 소비하는 다중행 곡선 형태로 별도 실증 — 단일행 테스트만으로는 합산 로직의
    회귀를 못 잡는다)."""
    curve = [
        {"hour": "9", "imp": 40, "clk": 3, "cost": 8000, "conv_cnt": 0, "avg_rank": 3.0},
        {"hour": "10", "imp": 40, "clk": 3, "cost": 7000, "conv_cnt": 1, "avg_rank": 3.0},
        {"hour": "11", "imp": 40, "clk": 3, "cost": 5000, "conv_cnt": 0, "avg_rank": 3.0},
    ]  # Σcost=20000·Σconv_cnt=1 → revenue=20000·roas=1.0<2.5 / profit=8000-20000=-12000<0
    est_roas = intraday_roas.estimated_intraday_roas(curve, _PRICE)
    est_profit = intraday_roas.estimated_intraday_profit(curve, _MARGIN)
    assert (est_roas < _BEP_ROAS) == (est_profit < 0)
    assert est_roas < _BEP_ROAS  # 이 곡선은 명시적으로 loss 케이스(고삐 발동 조건과 동형)
    assert est_profit < 0
