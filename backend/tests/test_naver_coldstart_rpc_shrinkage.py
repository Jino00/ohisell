# D-NAO-119 회귀 — 신제품 콜드스타트 RPC 수축(shrinkage). 실 API 호출 없음, 인메모리 sqlite.
#
# 배경(2026-07-29 라이브 실측): 갤럭시Z 폴드8울트라 grp-…070451363 이 9일·클릭 100·
# 직접전환 2건(33,600원)으로 RPC 336원 → 상한 160원을 받았다. 같은 캠페인 90일 RPC는
# 2,283원. 층 선택 게이트가 클릭 수에만 걸려 있어 전환 2건짜리 추정이 확정값이 됐다.
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    Base, NaverAdDaily, NaverAdgroupProduct, NaverLearningState, NaverProductBep,
)
from app.services.naver_ad import bid_ceiling_calculator as sa1
from app.services.naver_ad import conversion_maturity as cm

TODAY = date(2026, 7, 29)
CID = "cmp-a001-02-000000008425541"
GID = "grp-a001-02-000000070451363"   # 폴드8울트라
AD = "nad-a001-02-000000554205314"
PID = "13661073009"


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


def _seed_bep(db, *, selling="16800", cm_="8249.7", bep="2.0364"):
    """폴드8울트라 실측 BEP(2026-07-29 07:30 KST 산출분)."""
    db.add(NaverProductBep(
        channel_id=6, channel_product_id=PID, product_name="Z폴드8울트라 저반사 지문방지",
        selling_price=Decimal(selling), cost_price=Decimal("6090"),
        commission_rate=Decimal("0.0514"), logistics_cost=Decimal("772"),
        contribution_margin=Decimal(cm_), bep_roas=Decimal(bep), has_cost=True,
    ))
    db.add(NaverAdgroupProduct(
        adgroup_id=GID, campaign_id=CID, mall_product_id=PID,
        product_name="Z폴드8울트라 저반사 지문방지", ad_id=AD,
        ad_bid_amt=1000, use_group_bid_amt=False, ad_user_lock=False,
    ))


def _day(db, *, adgroup, days_ago, clk, amt, cnt, campaign=CID):
    db.add(NaverAdDaily(
        ad_date=TODAY - timedelta(days=days_ago), campaign_id=campaign, campaign_type="SHOPPING",
        adgroup_id=adgroup, keyword_id="", imp=clk * 40, clk=clk, cost=clk * 1000,
        conv_direct_amt=amt, conv_direct_cnt=cnt, conv_indirect_amt=0,
    ))


def _seed_live_shape(db):
    """라이브 형태 재현 — 자기 그룹은 얇고(클릭 100·전환 2건), 캠페인은 두껍다."""
    _seed_bep(db)
    _day(db, adgroup=GID, days_ago=1, clk=100, amt=33_600, cnt=2)          # 자기 층
    _day(db, adgroup="grp-sibling", days_ago=1, clk=10_835, amt=24_938_840, cnt=1_484)
    db.commit()


# ══════════════════════════════════════════════════════════════════
# 핵심 — 얇은 신제품이 상위 층 쪽으로 끌려 올라간다
# ══════════════════════════════════════════════════════════════════
def test_thin_new_product_is_pulled_toward_campaign_prior(db):
    """★회귀: 전환 2건짜리 자기 관측이 확정값이 되면 안 된다(구 동작 = 상한 160원)."""
    _seed_live_shape(db)
    out = sa1.compute_ceiling(db, AD, GID, CID, TODAY)

    own_only = Decimal("33600") / Decimal("100")          # 336원 — 구 동작
    assert out["rpc"] > own_only * 3, "수축이 안 걸렸다(자기 층 값에 머물렀다)"
    assert out["ceiling_cpc"] > 500, f"상한이 여전히 비현실적으로 낮다: {out['ceiling_cpc']}"
    # 상위 층 값을 그대로 베끼지도 않는다 — 자기 관측(336원)이 아래로 당긴다.
    prior_rpc = Decimal("24972440") / Decimal("10935")
    assert out["rpc"] < prior_rpc


def test_shrinkage_disabled_reproduces_the_live_bug(db):
    """수축을 끄면(K=0) 정확히 라이브가 겪은 160원이 재현된다 — 이 테스트가 원인을 고정한다."""
    _seed_live_shape(db)
    orig = sa1.SHRINKAGE_K_CONV
    sa1.SHRINKAGE_K_CONV = Decimal("0")
    try:
        out = sa1.compute_ceiling(db, AD, GID, CID, TODAY)
    finally:
        sa1.SHRINKAGE_K_CONV = orig
    assert out["ceiling_cpc"] == 160


# ══════════════════════════════════════════════════════════════════
# 클릭 증거는 버리지 않는다 (전환 건수 가중이 아니라 클릭 가중인 이유)
# ══════════════════════════════════════════════════════════════════
def test_many_clicks_zero_conversions_is_not_given_the_full_prior(db):
    """클릭 2,000·전환 0건이면 '안 팔린다'는 강한 증거다 — prior를 그대로 받으면 안 된다."""
    _seed_bep(db)
    _day(db, adgroup=GID, days_ago=1, clk=2_000, amt=0, cnt=0)
    _day(db, adgroup="grp-sibling", days_ago=1, clk=10_835, amt=24_938_840, cnt=1_484)
    db.commit()
    out = sa1.compute_ceiling(db, AD, GID, CID, TODAY)

    prior_rpc = Decimal("24938840") / Decimal("10835")
    assert out["rpc"] < prior_rpc / 5, "자기 층의 '전환 0' 증거가 무시됐다"
    assert out["rpc_source"] == "adgroup", "자기 층 표본이 압도적인데 상위 층이 지배로 표기됐다"


def test_own_data_wins_as_it_accumulates(db):
    """자기 데이터가 쌓일수록 자기 값으로 수렴한다(점진 이행 — D-NAO-119의 요구)."""
    _seed_bep(db)
    _day(db, adgroup="grp-sibling", days_ago=1, clk=10_835, amt=24_938_840, cnt=1_484)
    _day(db, adgroup=GID, days_ago=2, clk=100, amt=33_600, cnt=2)
    db.commit()
    thin = sa1.compute_ceiling(db, AD, GID, CID, TODAY)["rpc"]

    # 같은 CVR·같은 객단가로 자기 클릭만 20배 늘린다.
    _day(db, adgroup=GID, days_ago=3, clk=2_000, amt=672_000, cnt=40)
    db.commit()
    thick = sa1.compute_ceiling(db, AD, GID, CID, TODAY)["rpc"]

    own_rpc = Decimal("336")
    assert abs(thick - own_rpc) < abs(thin - own_rpc), "표본이 늘었는데 자기 값에 더 가까워지지 않았다"


# ══════════════════════════════════════════════════════════════════
# 신뢰도 라벨 — 계정 층 지배는 confident=False(하위 판정자의 0.7 할인 유지)
# ══════════════════════════════════════════════════════════════════
def test_account_dominant_prior_stays_not_confident(db):
    _seed_bep(db)
    _day(db, adgroup="grp-x", days_ago=1, clk=5_000, amt=9_000_000, cnt=540, campaign="cmp-other")
    db.commit()
    out = sa1.compute_ceiling(db, AD, GID, CID, TODAY)
    assert out["rpc_source"] == "account"
    assert out["confident"] is False


def test_campaign_prior_is_confident(db):
    _seed_live_shape(db)
    out = sa1.compute_ceiling(db, AD, GID, CID, TODAY)
    assert out["rpc_source"] in {"campaign", "adgroup"}
    assert out["confident"] is True


def test_no_prior_anywhere_falls_back_to_old_behaviour(db):
    """상위 층에 매출이 전혀 없으면 종전 사다리 그대로 — 없는 근거를 지어내지 않는다.

    ★테스트 버그 수정(구현 아님): 원래 시드는 clk=50이었는데, 이 값은 자기 행이 캠페인의
    전부라 캠페인 층 clk도 50 — MIN_CLK_CAMPAIGN(30) **이상**이라 prior가 "발견"돼 버려서
    실제로는 no-prior 경로를 타지 않았다(수축이 걸려 2000이 아니라 2012.28이 나와 실패).
    clk을 30 미만(그러나 MIN_CLK_ADGROUP=10 이상)으로 낮춰 캠페인·계정 층 모두 문턱 미달 →
    prior=None → 진짜 구 사다리(자기 층 단독) 경로를 태운다.
    """
    _seed_bep(db)
    _day(db, adgroup=GID, days_ago=1, clk=20, amt=40_000, cnt=3)
    db.commit()
    out = sa1.compute_ceiling(db, AD, GID, CID, TODAY)
    # 캠페인 층·계정 층 = 이 한 행뿐이라 둘 다 MIN_CLK 미달 → prior=None → 값은 자기 층 그대로.
    assert out["rpc"] == Decimal("40000") / Decimal("20")


def test_no_sample_at_all_still_yields_none(db):
    _seed_bep(db)
    db.commit()
    out = sa1.compute_ceiling(db, AD, GID, CID, TODAY)
    assert out["rpc_source"] == "none"
    assert out["ceiling_cpc"] == 0


# ══════════════════════════════════════════════════════════════════
# 가격 환산 — 상위 층은 판매가가 다른 상품의 혼합이다
# ══════════════════════════════════════════════════════════════════
def test_prior_is_rescaled_to_this_products_price(db):
    """이 상품이 상위 층 평균 주문단가보다 비싸면 prior가 위로 환산되어 상한을 끌어올린다.

    ★테스트 버그 수정(구현 아님, 2건):
    1) 원래 시드(grp-cheap amt=8,400,000 → 상위 층 평균단가 정확히 8,400원)는 캠페인 층 prior가
       **이 광고그룹 자신의 행도 포함**한다는 점(campaign_id 필터라 own도 그 안에 들어간다)을
       빠뜨려서, 실제 배율이 1.9959...로 클램프 상한(2.0) 바로 아래에 머물러 실패했다.
       amt를 낮춰(8,000,000) 상위 층 평균단가를 확실히 8,400원 밑으로 떨어뜨려 클램프를 확실히 넘긴다.
    2) "같은 단가면 배율 1.0" 비교를 서로 다른 두 데이터셋(cheap vs same)의 최종 rpc 대소 비교로
       하던 방식은, 클램프가 걸리면 "부분 보정(2배 상한)"이 "완전 보정(자연 배율)"보다 오히려
       작을 수 있어(클램프의 정의상 당연한 결과) 방향이 뒤집혀 깨지기 쉽다. 대신 같은 데이터셋
       안에서 "가격 환산을 적용한 값 vs 적용 안 했을 값(ratio=1)"을 직접 비교한다 — ratio>1이면
       _blend가 prior_rpc 항에서 단조증가이므로 항상 성립이 보장된다.
    """
    _seed_bep(db)
    _day(db, adgroup=GID, days_ago=1, clk=100, amt=33_600, cnt=2)
    _day(db, adgroup="grp-cheap", days_ago=1, clk=10_000, amt=8_000_000, cnt=1_000)
    db.commit()
    rpc_info = sa1.resolve_rpc(db, GID, CID, TODAY)
    out = sa1.ceiling_from(db, AD, rpc_info)
    assert out["price_ratio"] == sa1._PRICE_RATIO_MAX

    blend = rpc_info["blend"]
    rpc_without_ratio = sa1._blend(
        blend["own_revenue"], blend["own_clk"], blend["prior_rpc"], blend["prior_clicks"],
    )
    assert out["rpc"] > rpc_without_ratio, "가격 환산이 상한에 반영되지 않았다"

    # 같은 형태에서 상위 층 단가만 이 상품과 같게 두면 환산이 (거의) 사라진다(배율 ≈ 1.0).
    # ★bep_roas가 소수 4자리로 저장돼 price(=bep_roas×contribution_margin)가 16,800원과 정확히
    # 일치하지 않는다(16,799.68908원) — exact-equality 대신 근사로 검증한다(테스트 버그 수정).
    db.query(NaverAdDaily).filter(NaverAdDaily.adgroup_id == "grp-cheap").delete()
    _day(db, adgroup="grp-same", days_ago=1, clk=10_000, amt=16_800_000, cnt=1_000)
    db.commit()
    out2 = sa1.compute_ceiling(db, AD, GID, CID, TODAY)
    assert abs(out2["price_ratio"] - Decimal(1)) < Decimal("0.001")


def test_price_ratio_is_clamped_both_ways(db):
    """상위 층이 이 상품을 대표하지 못할 만큼 단가가 벌어지면 배율을 잘라낸다."""
    _seed_bep(db)
    _day(db, adgroup=GID, days_ago=1, clk=100, amt=33_600, cnt=2)
    _day(db, adgroup="grp-lux", days_ago=1, clk=10_000, amt=200_000_000, cnt=1_000)  # 단가 20만
    db.commit()
    out = sa1.compute_ceiling(db, AD, GID, CID, TODAY)
    assert out["price_ratio"] == sa1._PRICE_RATIO_MIN


# ══════════════════════════════════════════════════════════════════
# 정착 보정은 기본 OFF — 이 파일 배포가 보류 결정을 밀반입하지 않는다
# ══════════════════════════════════════════════════════════════════
def test_maturity_correction_is_off_by_default(db):
    """★회귀: 곡선이 살아 있어도 기본값에서는 RPC가 변하지 않는다(dd73a59 보류 유지)."""
    assert sa1.MATURITY_CORRECTION_ENABLED is False
    _seed_bep(db)
    db.add(NaverLearningState(scope="global", scope_key="day_1", metric=cm.METRIC,
                              current_value=Decimal("0.5"), sample_n=5, confidence=Decimal("1")))
    _day(db, adgroup=GID, days_ago=1, clk=100, amt=33_600, cnt=2)
    db.commit()
    clk, rpc = sa1._rpc_for(db, TODAY, adgroup_id=GID)
    assert clk == 100
    assert rpc == Decimal("336"), "보류된 정착 보정이 적용됐다(배포 시 밀반입)"


def test_maturity_correction_still_works_when_enabled(db):
    """스위치를 켜면 종전 배선이 그대로 동작한다 — 기능을 지운 게 아니라 게이트한 것."""
    _seed_bep(db)
    db.add(NaverLearningState(scope="global", scope_key="day_1", metric=cm.METRIC,
                              current_value=Decimal("0.5"), sample_n=5, confidence=Decimal("1")))
    _day(db, adgroup=GID, days_ago=1, clk=100, amt=33_600, cnt=2)
    db.commit()
    orig = sa1.MATURITY_CORRECTION_ENABLED
    sa1.MATURITY_CORRECTION_ENABLED = True
    try:
        clk, rpc = sa1._rpc_for(db, TODAY, adgroup_id=GID)
    finally:
        sa1.MATURITY_CORRECTION_ENABLED = orig
    assert rpc == Decimal("672")  # 33,600 × 2 ÷ 100


# ══════════════════════════════════════════════════════════════════
# 누출 방지 — 내부 원재료가 반환 dict로 새 나가지 않는다
# ══════════════════════════════════════════════════════════════════
def test_blend_internals_do_not_leak_into_ceiling_result(db):
    """blend는 Decimal 덩어리라 API 응답·로그로 새면 안 된다(ceiling_from이 떼어낸다)."""
    _seed_live_shape(db)
    rpc_info = sa1.resolve_rpc(db, GID, CID, TODAY)
    assert rpc_info["blend"] is not None
    out = sa1.ceiling_from(db, AD, rpc_info)
    assert "blend" not in out
