"""성과 화면 **첫 칸의 총이익**을 잰다 (설계서 122 §4-1, PAO 화면 목표 3단계).

## 왜 이 파일이 있나

§4-1이 총이익을 첫 칸으로 올리라고 한 이유는 장식이 아니다 — *"첫 칸이 ROAS면 화면이 그
표류를 다시 유도한다"*(D-NAO-85 실측: ROAS +7% · 매출 −52%). 그런데 이 화면의 `totals`엔
여태 총이익이 **아예 없었다**(`spend_today`·`campaigns_active_today`·`campaigns_total` 셋뿐).

여기서 재는 것은 산식보다 **합계 규칙**이다: BEP나 매출을 모르는 캠페인을 **0으로 세면**
그 0이 합계에 그대로 들어가 「이익이 없다」로 읽힌다. 모르는 것은 합계에서 빼고 **몇 개를
뺐는지 화면이 말해야** 한다. 그 규칙은 산술이 아니라 분기라, 분기를 지워도 숫자는 계속
그럴듯하게 나온다.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverEntity,
    NaverHourlySnapshot,
    NaverProductBep,
)
from app.services.naver_ad import perf_today_harness
from app.utils.kst import kst_now, kst_today

SHOPPING = "cmp-shopping-1"      # 상품 매핑 있음 ⇒ 매출 관측됨 ⇒ 총이익 계산됨
SHOPPING_2 = "cmp-shopping-2"    # ★두 번째 «아는» 캠페인 — 하나뿐이면 sum을 max로 바꿔도 초록이다
IDLE = "cmp-idle-1"              # 매핑 있으나 **오늘 광고비 0** ⇒ 총이익은 정의되지 않는다
# ★매핑 없음 ⇒ **매출**을 모른다(BEP는 계정 기본값으로 떨어져 값이 있다 — 실측으로 확인).
#   그래서 「모름」의 사유가 BEP가 아니라 매출인 것이 이 화면의 실제 모양이다.
POWERLINK = "cmp-powerlink-1"
PRODUCT_ID = "11730763642"
SHOPPING_COST = 41300
SHOPPING_2_COST = 10000


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    yield session
    session.close()


def _seed(db):
    db.add_all([
        NaverEntity(entity_type="campaign", entity_id=SHOPPING, campaign_id=SHOPPING,
                    campaign_type="SHOPPING", name="02. 아이폰_강화유리", status="on",
                    status_reason="ELIGIBLE"),
        NaverEntity(entity_type="campaign", entity_id=POWERLINK, campaign_id=POWERLINK,
                    campaign_type="WEB_SITE", name="P. 아이패드 파워링크", status="on",
                    status_reason="ELIGIBLE"),
        NaverEntity(entity_type="adgroup", entity_id="grp-1", campaign_id=SHOPPING,
                    parent_id=SHOPPING, campaign_type="SHOPPING", name="17프로", status="on"),
        NaverEntity(entity_type="campaign", entity_id=SHOPPING_2, campaign_id=SHOPPING_2,
                    campaign_type="SHOPPING", name="03. 갤럭시_강화유리", status="on",
                    status_reason="ELIGIBLE"),
        NaverEntity(entity_type="adgroup", entity_id="grp-2", campaign_id=SHOPPING_2,
                    parent_id=SHOPPING_2, campaign_type="SHOPPING", name="Z폴드", status="on"),
        NaverEntity(entity_type="campaign", entity_id=IDLE, campaign_id=IDLE,
                    campaign_type="SHOPPING", name="04. 멈춘 광고", status="on",
                    status_reason="ELIGIBLE"),
        NaverEntity(entity_type="adgroup", entity_id="grp-3", campaign_id=IDLE,
                    parent_id=IDLE, campaign_type="SHOPPING", name="정지", status="on"),
    ])
    db.add(NaverCampaignSettings(campaign_id=SHOPPING, optimizer="ours", auto_operate=True))
    db.add_all([
        NaverAdgroupProduct(adgroup_id="grp-1", campaign_id=SHOPPING,
                            mall_product_id=PRODUCT_ID, product_name="아이폰 17 Pro 강화유리",
                            ad_id="nad-1"),
        NaverAdgroupProduct(adgroup_id="grp-2", campaign_id=SHOPPING_2,
                            mall_product_id=PRODUCT_ID, product_name="Z폴드 강화유리",
                            ad_id="nad-2"),
        NaverAdgroupProduct(adgroup_id="grp-3", campaign_id=IDLE,
                            mall_product_id=PRODUCT_ID, product_name="정지 상품",
                            ad_id="nad-3"),
    ])
    db.add(NaverProductBep(channel_id=6, channel_product_id=PRODUCT_ID, product_name="강화유리",
                           selling_price=Decimal("12900"), cost_price=Decimal("3100"),
                           contribution_margin=Decimal("8740"), bep_roas=Decimal("1.4758"),
                           target_roas=Decimal("1.7200"), has_cost=True))
    now = kst_now()
    db.add_all([
        NaverHourlySnapshot(snapshot_at=now, ad_date=kst_today(), snapshot_hour=now.hour,
                            campaign_id=SHOPPING, campaign_type="SHOPPING",
                            cost=SHOPPING_COST, clk=20, imp=900, daily_budget=50000),
        NaverHourlySnapshot(snapshot_at=now, ad_date=kst_today(), snapshot_hour=now.hour,
                            campaign_id=SHOPPING_2, campaign_type="SHOPPING",
                            cost=SHOPPING_2_COST, clk=5, imp=200, daily_budget=30000),
        # ★오늘 한 푼도 안 썼다 — 그런데 프록시 매출은 «그 상품의 그날 전체 판매액»이라
        #   이 카드에도 붙는다. 더하면 헤드라인의 대부분이 «멈춰 있던 광고»에서 나온다.
        NaverHourlySnapshot(snapshot_at=now, ad_date=kst_today(), snapshot_hour=now.hour,
                            campaign_id=IDLE, campaign_type="SHOPPING",
                            cost=0, clk=0, imp=0, daily_budget=30000),
        NaverHourlySnapshot(snapshot_at=now, ad_date=kst_today(), snapshot_hour=now.hour,
                            campaign_id=POWERLINK, campaign_type="WEB_SITE",
                            cost=5000, clk=3, imp=100, daily_budget=None),
    ])
    db.commit()


def test_unknown_campaigns_are_excluded_not_counted_as_zero(db):
    """★모르는 캠페인을 0으로 세면 그 0이 합계에 들어가 「이익이 없다」로 읽힌다."""
    _seed(db)
    totals = perf_today_harness.build(db)["totals"]

    # 매출 0은 **관측된 0**이라 총이익이 계산된다(−광고비). 「모름」과 다르다.
    # ★아는 캠페인이 **둘**이어야 합계가 진짜 합계인지 잴 수 있다 — 하나뿐이면 sum을 max로
    #   바꿔도 초록이다(적대 리뷰 P2-5의 생존 변이 M9).
    assert totals["gross_profit_today"] == -(SHOPPING_COST + SHOPPING_2_COST)
    assert totals["gross_profit_known_campaigns"] == 2
    # 파워링크(매출 모름)와 멈춘 광고(오늘 집행 0) 둘이 빠지고, 빠진 사실이 수로 남는다.
    assert totals["gross_profit_unknown_campaigns"] == 2
    # 이 총이익이 **어느 매출 위에서** 잰 값인지 응답이 스스로 말한다.
    assert totals["gross_profit_basis"] == "오늘 추정"


def test_each_card_says_why_its_profit_is_unknown(db):
    """숫자를 지어내지 않는다 — 없으면 **왜 없는지**를 그 카드가 말한다."""
    _seed(db)
    cards = {c["campaign_id"]: c for c in perf_today_harness.build(db)["campaigns"]}

    assert cards[SHOPPING]["gross_profit_today"] == -SHOPPING_COST
    assert cards[SHOPPING]["gross_profit_unknown_reason"] is None
    assert cards[POWERLINK]["gross_profit_today"] is None
    # 사유가 **그 카드의 실제 결손**을 가리킨다 — BEP는 계정 기본값이 있어 값이 있고,
    # 없는 건 매출이다. 엉뚱한 것을 탓하면 다음 사람이 엉뚱한 곳을 고친다.
    assert "매출" in cards[POWERLINK]["gross_profit_unknown_reason"]


@pytest.mark.parametrize(
    "revenue, spend, bep, expected",
    [
        (147580, 41300, 1.4758, 100000 - 41300),  # 147,580 ÷ 1.4758 = 100,000
        (0, 41300, 1.4758, -41300),               # 관측된 0 — 「모름」이 아니다
    ],
)
def test_formula_is_revenue_over_bep_minus_spend(revenue, spend, bep, expected):
    """산식이 이 저장소의 다른 총이익 산출과 같은가 — 매출 ÷ BEP − 광고비."""
    value, reason = perf_today_harness._gross_profit_today(revenue, spend, bep)
    assert value == expected and reason is None


@pytest.mark.parametrize("revenue, bep", [(None, 1.4758), (100, None), (100, 0)])
def test_missing_inputs_never_become_zero(revenue, bep):
    """BEP나 매출이 없으면 **0이 아니라 None**이다 — 0은 「이익이 없다」는 사실 주장이다."""
    value, reason = perf_today_harness._gross_profit_today(revenue, 41300, bep)
    assert value is None and reason


def test_zero_spend_campaign_is_not_counted_as_profit(db):
    """★적대 리뷰 P1-1 — 오늘 한 푼도 안 쓴 광고의 **상품 매출**이 총이익에 들어가면
    헤드라인의 대부분이 「멈춰 있던 광고」에서 나온다(리뷰 재현: 308,341원 중 85%).

    같은 카드의 ROAS는 이미 `spend<=0`에서 계산을 **거부**한다 — 총이익만 값을 내면
    한 카드 안에서 자가 갈리고, 옆 칸 「돌아간 광고 N개」와 서로를 반증한다.
    """
    _seed(db)
    out = perf_today_harness.build(db)
    idle = {c["campaign_id"]: c for c in out["campaigns"]}[IDLE]

    assert idle["spend_today"] == 0
    assert idle["gross_profit_today"] is None
    assert "집행된 광고비가 없어" in idle["gross_profit_unknown_reason"]
    # 합계에도 안 들어간다 — 아는 캠페인 수가 「돌아간 광고」와 어긋나지 않는다.
    assert out["totals"]["gross_profit_known_campaigns"] <= out["totals"]["campaigns_active_today"]


def test_totals_confess_the_value_is_before_correction(db):
    """★적대 리뷰 P1-2 — 이 값은 **보정 전**이다.

    오늘치는 프록시 매출이라 보정계수를 곱하면 두 번 부풀지만, 과거 날짜의 입력은
    `conv_amt`라 그 계수의 정의역이다. 같은 저장소의 다른 총이익은 계수를 적용하므로
    값이 갈리고, 그 자는 **부호까지 바꾼 전례**가 있다(ref 93 §1 행 9).
    조용히 다르면 사용자는 같은 값이라고 믿는다.
    """
    _seed(db)
    note = perf_today_harness.build(db)["totals"]["gross_profit_lens_note"]
    assert "보정 전" in note and "다를 수 있습니다" in note


@pytest.mark.parametrize("spend", [0, -1])
def test_no_spend_means_no_profit(spend):
    """광고비가 없으면 이 광고의 총이익은 **정의되지 않는다**(0이 아니다)."""
    value, reason = perf_today_harness._gross_profit_today(387000, spend, 1.4758)
    assert value is None and "집행된 광고비가 없어" in reason
