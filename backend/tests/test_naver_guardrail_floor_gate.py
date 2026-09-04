# test_naver_guardrail_floor_gate.py — D-NAO-286 표본 하한 게이트
# 계약: docs/contracts/CONTRACT_sample_floor_gate.md (북극성 M2 S2-ⓑ)
# 실 API 0 · 실쓰기 0 · 순수 판정기 + 로컬 SQLite 집계만.
#
# ★이 파일이 지키는 문장: **「표본이 없으면 아무 판단도 하지 않는다」**
#   — 「올리지 마라」가 아니다. 증액만 막으면 브레이크만 남아 D-NAO-85형 표류가 된다(북극성 §7).
from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdCreativeDaily, NaverAdDaily
from app.services.naver_ad import auto_operator, bid_step_types
from app.services.naver_ad import guardrail_gate as gate

NOW = datetime(2026, 9, 4, 12, 20, 0)
TODAY = date(2026, 9, 4)
WINDOW_FROM, WINDOW_TO = auto_operator._settlement_window(TODAY)  # [D-8, D-2]
CAMPAIGN = "cmp-shop"
ADGROUP = "grp-1"
AD = "nad-1"


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


def _bid(proposal_type="bid_up", target_bid=210):
    return {"proposal_type": proposal_type, "target_bid": target_bid, "target_lock": None}


def _ctx(**over):
    """게이트가 «배선된» 컨텍스트 — 두 원료 키가 실려 있다."""
    base = {
        "current_bid": 190, "current_budget": None,
        "roas_corrected": 250.0, "target_roas": 150.0,
        "cost_today": 10_000, "daily_budget": 500_000, "unconverted_spend": 0,
        "last_change_at": None, "changes_today_count": 0,
        "auto_exec": True, "floor_exempt": False,
        "campaign_weekly_conv": 100, "target_weekly_conv": 100,
    }
    base.update(over)
    return base


# ══════════════ A. 적용 경계 — 누구에게 걸리고 누구에게 안 걸리나 ══════════════

def test_컨텍스트에_원료_키가_아예_없으면_통과한다():
    """미배선 호출부·기존 테스트의 행위를 바꾸지 않는다(_param fail-to-current와 같은 결).

    ★이 줄이 없으면 배포 순간 컨텍스트를 안 채우는 모든 경로가 전건 차단된다.
    """
    ctx = _ctx()
    del ctx["campaign_weekly_conv"]
    del ctx["target_weekly_conv"]
    assert gate._check_data_floor(ctx, "bid_up") is None


def test_사람_승인_경로에는_안_걸린다():
    """auto_exec=False = 콘솔 Confirm. 사람은 표본이 얇은 것을 알고도 누를 수 있다(계약 §3)."""
    assert gate._check_data_floor(_ctx(auto_exec=False, campaign_weekly_conv=0, target_weekly_conv=0), "bid_up") is None


def test_되돌림_손실고삐_레인은_면제된다():
    """표본 하한이 막으려는 것은 「표본 없이 «새로» 판단하는 것」이지 「이전 판단의 취소」가 아니다.

    막으면 «올려놓고 못 내리는» 상태가 되어 게이트가 손해를 만든다(계약 §3 금지선).
    """
    assert gate._check_data_floor(
        _ctx(floor_exempt=True, campaign_weekly_conv=0, target_weekly_conv=0), "bid_up"
    ) is None


# ══════════════ B. 「모름」과 「적음」은 다르다 ══════════════

@pytest.mark.parametrize("key", ["campaign_weekly_conv", "target_weekly_conv"])
def test_키는_있는데_값이_None이면_차단한다(key):
    """★「측정 안 함」(키 부재)과 「측정했는데 모름」(None)은 다르다.

    후자를 통과로 읽는 것이 이 저장소가 반복해 밟은 「모름=괜찮음」이다(교훈 #123).
    """
    reason = gate._check_data_floor(_ctx(**{key: None}), "bid_up")
    assert reason is not None
    assert "측정 불가" in reason
    assert "모름은 통과가 아니다" in reason


def test_하한_미달이면_사유에_실제_수가_박힌다():
    """사유가 「하한 미달」로만 끝나면 다음 사람이 «얼마나» 모자란지 모른다."""
    reason = gate._check_data_floor(_ctx(target_weekly_conv=2), "bid_up")
    assert reason is not None
    assert "대상 정착창 전환 2건 < 하한 15건" in reason


def test_캠페인_하한이_대상보다_먼저_판정된다():
    """둘 다 미달이면 위층(캠페인)을 사유로 낸다 — 좁은 사유가 넓은 사유를 가리지 않게."""
    reason = gate._check_data_floor(_ctx(campaign_weekly_conv=3, target_weekly_conv=2), "bid_up")
    assert reason is not None and "캠페인 정착창 전환 3건" in reason


@pytest.mark.parametrize("conv,blocked", [(14, True), (15, False), (16, False)])
def test_경계는_정확히_15에서_열린다(conv, blocked):
    """`ref 33` [6] 원문 주 15전환 — 15는 «통과»다(미만만 막는다)."""
    reason = gate._check_data_floor(_ctx(campaign_weekly_conv=conv, target_weekly_conv=conv), "bid_up")
    assert (reason is not None) is blocked


# ══════════════ C. ★양방향 — 이 계약의 핵심 문장 ══════════════

@pytest.mark.parametrize("proposal_type", ["bid_up", "bid_down"])
def test_증액도_감액도_똑같이_막힌다(proposal_type):
    """★증액만 막으면 브레이크만 남는다 — D-NAO-85 실측(ROAS +7% · 매출 −52%)의 모양이다.

    `gate.check` 전체를 태워 «입찰 검사보다 먼저» 표본 하한이 뜨는 것까지 확인한다.
    """
    target_bid = 210 if proposal_type == "bid_up" else 170
    reason = gate.check(_bid(proposal_type, target_bid), _ctx(target_weekly_conv=2), now=NOW)
    assert reason is not None
    assert reason.startswith("표본 하한")


def test_표본이_충분하면_종전_판정으로_넘어간다():
    """게이트는 «가로채기»이지 «대체»가 아니다 — 통과하면 기존 입찰 검사가 그대로 돈다."""
    assert gate.check(_bid(), _ctx(), now=NOW) is None


def test_SPECS_값이_코드_상수를_이긴다():
    """화면에서 하한을 내리면 그 값으로 판정해야 한다 — 안 그러면 승인 카드가 거짓말한다."""
    ctx = _ctx(campaign_weekly_conv=9, target_weekly_conv=9,
               guardrail_params={"min_weekly_conv_campaign": 8, "min_weekly_conv_target": 8})
    assert gate._check_data_floor(ctx, "bid_up") is None          # 8 하한이면 9는 통과
    assert gate._check_data_floor(_ctx(campaign_weekly_conv=9, target_weekly_conv=9), "bid_up") is not None  # 코드 상수 15면 차단


# ══════════════ D. 소재(ad) grain 집계 — 게이트의 원료 ══════════════

def _creative_row(db, *, ad_id=AD, clk=10, cost=1000, cnt_d=3, cnt_i=2, day=None):
    db.add(NaverAdCreativeDaily(
        ad_date=day or WINDOW_FROM, ad_id=ad_id, campaign_id=CAMPAIGN,
        campaign_type="SHOPPING", adgroup_id=ADGROUP,
        imp=100, clk=clk, cost=cost, rank_sum=0,
        conv_direct_cnt=cnt_d, conv_indirect_cnt=cnt_i,
        conv_direct_amt=5000, conv_indirect_amt=1000,
    ))
    db.commit()


def test_ad_grain은_소재_원장을_읽는다(db):
    """★종전엔 target_type='ad'가 else로 떨어져 `NaverAdDaily.adgroup_id == <소재id>`를
    조회했고 그 조합은 **원리적으로 0행**이었다 — 정착창 검증·CPC 급등 DOWN·손실 고삐가
    셋 다 죽어 있던 원인이다(2026-09-04 실측: 최근 30일 실집행 15건이 전부 ad grain)."""
    _creative_row(db)
    agg = auto_operator._settlement_agg(db, "ad", AD, WINDOW_FROM, WINDOW_TO)
    assert agg["clk"] == 10
    assert agg["cost"] == 1000
    assert agg["conv_cnt"] == 5          # 3 + 2
    assert agg["conv_amt"] == 6000
    assert agg["grain_fallback"] is False


def test_ad_grain은_adgroup_원장을_보지_않는다(db):
    """회귀 방어 — 소재 id와 «같은 문자열»의 adgroup_id 행이 있어도 ad 집계에 섞이면 안 된다.

    종전 결함이 정확히 이 혼선이었다(소재 id를 adgroup_id 컬럼에서 찾았다).
    """
    db.add(NaverAdDaily(
        ad_date=WINDOW_FROM, campaign_id=CAMPAIGN, campaign_type="SHOPPING",
        adgroup_id=AD, keyword_id=None, imp=9999, clk=9999, cost=9999,
        conv_direct_cnt=99, conv_indirect_cnt=0, conv_direct_amt=0, conv_indirect_amt=0,
    ))
    db.commit()
    agg = auto_operator._settlement_agg(db, "ad", AD, WINDOW_FROM, WINDOW_TO)
    assert agg == {"clk": 0, "cost": 0, "conv_amt": 0, "conv_cnt": 0, "grain_fallback": False}


def test_창_밖_소재_행은_안_섞인다(db):
    _creative_row(db, clk=10)
    _creative_row(db, clk=777, day=date(2026, 9, 3))     # 정착창(D-2=09-02) 밖
    agg = auto_operator._settlement_agg(db, "ad", AD, WINDOW_FROM, WINDOW_TO)
    assert agg["clk"] == 10


def test_adgroup_grain은_종전_그대로이고_conv_cnt만_는다(db):
    db.add(NaverAdDaily(
        ad_date=WINDOW_FROM, campaign_id=CAMPAIGN, campaign_type="SHOPPING",
        adgroup_id=ADGROUP, keyword_id=None, imp=100, clk=7, cost=700,
        conv_direct_cnt=4, conv_indirect_cnt=1, conv_direct_amt=100, conv_indirect_amt=200,
    ))
    db.commit()
    agg = auto_operator._settlement_agg(db, "adgroup", ADGROUP, WINDOW_FROM, WINDOW_TO)
    assert agg["clk"] == 7 and agg["cost"] == 700 and agg["conv_amt"] == 300
    assert agg["conv_cnt"] == 5
    assert agg["grain_fallback"] is False


def test_미지원_grain은_자백한다(db):
    """«조용히 adgroup으로 흡수»되던 것을 플래그로 드러낸다 — 다음 grain이 또 새지 않게."""
    agg = auto_operator._settlement_agg(db, "search_term", "검색어", WINDOW_FROM, WINDOW_TO)
    assert agg["grain_fallback"] is True


# ══════════════ E. settlement_conv_counts — 원료 조립 ══════════════

def test_conv_counts는_캠페인과_대상을_따로_센다(db):
    _creative_row(db, cnt_d=3, cnt_i=2)                       # 소재 5건
    db.add(NaverAdDaily(
        ad_date=WINDOW_FROM, campaign_id=CAMPAIGN, campaign_type="SHOPPING",
        adgroup_id=ADGROUP, keyword_id=None, imp=1, clk=1, cost=1,
        conv_direct_cnt=20, conv_indirect_cnt=0, conv_direct_amt=0, conv_indirect_amt=0,
    ))
    db.commit()
    camp, tgt = auto_operator.settlement_conv_counts(
        db, target_type="ad", target_id=AD, campaign_id=CAMPAIGN, today=TODAY)
    assert camp == 20                                        # 캠페인은 일별 원장에서
    assert tgt == 5                                          # 대상은 소재 원장에서


def test_conv_counts는_미지원_grain에_None을_준다(db):
    """None = «측정 불가» — 게이트가 차단으로 읽는다. 0(«측정했고 0건»)과 뭉개지 않는다."""
    camp, tgt = auto_operator.settlement_conv_counts(
        db, target_type="search_term", target_id="검색어", campaign_id=CAMPAIGN, today=TODAY)
    assert camp == 0        # 캠페인은 잴 수 있다 — 측정했고 0건
    assert tgt is None      # 대상은 못 잰다


def test_conv_counts의_미지원_grain_판정은_자백_플래그_하나로만_한다(db):
    """★자기 변이 M9 생존이 만든 테스트 — 「grain_fallback을 무시해도 초록」이었다.

    원인: 호출부가 `target_type in ("ad","adgroup","keyword")`로 미리 걸러서 그 셋은
    fallback이 원리적으로 False였다 ⇒ **뒤 가드가 도달 불가한 죽은 코드**. 어휘를 두 곳에
    적으면 갈라진다. 이 테스트는 «미지원 grain이 남의 그룹 숫자를 들고 오지 않는다»를
    자백 플래그 «하나»로만 지킨다 — 플래그를 무시하면 여기가 빨개진다.
    """
    # 'search_term'은 adgroup 필터로 폴백해 «남의» 그룹 행을 긁는다 — 그걸 대상 표본으로
    # 쓰면 안 된다. 같은 문자열의 adgroup 행을 일부러 심어 그 위험을 재현한다.
    db.add(NaverAdDaily(
        ad_date=WINDOW_FROM, campaign_id=CAMPAIGN, campaign_type="SHOPPING",
        adgroup_id="검색어", keyword_id=None, imp=1, clk=1, cost=1,
        conv_direct_cnt=99, conv_indirect_cnt=0, conv_direct_amt=0, conv_indirect_amt=0,
    ))
    db.commit()
    _, tgt = auto_operator.settlement_conv_counts(
        db, target_type="search_term", target_id="검색어", campaign_id=CAMPAIGN, today=TODAY)
    assert tgt is None, "미지원 grain은 «측정 불가»여야 한다 — 99건을 대상 표본으로 읽으면 안 된다"


def test_conv_counts는_캠페인_id가_없으면_None을_준다(db):
    camp, _ = auto_operator.settlement_conv_counts(
        db, target_type="ad", target_id=AD, campaign_id=None, today=TODAY)
    assert camp is None


# ══════════════ F. 면제 표식 ══════════════

def test_손실방어_표식은_왕복하고_표시에선_지워진다():
    e = bid_step_types.encode_loss_defense("순위 고삐 본문")
    assert bid_step_types.is_loss_defense(e) is True
    assert bid_step_types.is_loss_defense("순위 고삐 본문") is False
    assert bid_step_types.strip_base_bid_marker(e) == "순위 고삐 본문"


def test_손실방어_표식은_멱등이고_중복이면_안_믿는다():
    """base_bid 디코드와 같은 엄격 모드 — 표식을 못 믿으면 게이트를 그냥 적용한다(보수)."""
    e = bid_step_types.encode_loss_defense("본문")
    assert bid_step_types.encode_loss_defense(e) == e          # 멱등
    assert bid_step_types.is_loss_defense(e + "\n[[loss_defense=1]]") is False


# ══════════════ G. ★게이트 대상 인구조사 — 어느 레인이 걸리고 어느 레인이 면제인가 ══════════════

def test_게이트_대상은_성과_판단_레인_5종뿐이다():
    """키가 늘거나 줄면 이 줄이 빨개진다 — 「누가 이 브레이크를 맞는가」가 조용히 안 바뀌도록.

    ★탐색·콜드가 여기 들어오면 **닫힌 고리**가 된다(입찰→노출→클릭→전환 길이 막혀 하한을
    영원히 못 넘는다). Jino 결정 2026-09-04 「탐색·콜드는 면제」.
    """
    assert set(gate._FLOOR_GATED_TYPES) == {
        "bid_up", "bid_down", "bid_up_rank", "bid_up_servo", "growth_bid_up",
    }
    assert set(gate._BID_TYPES - gate._FLOOR_GATED_TYPES) == {"bid_up_explore", "bid_up_cold"}


@pytest.mark.parametrize("proposal_type", ["bid_up_explore", "bid_up_cold"])
def test_탐색과_콜드는_표본이_0이어도_통과한다(proposal_type):
    """그 둘은 «표본을 만들러 가는» 레인이다 — 자체 상한(경제성 ceiling·30% 캡·쿨다운)이 따로 있다."""
    ctx = _ctx(campaign_weekly_conv=0, target_weekly_conv=0)
    assert gate._check_data_floor(ctx, proposal_type) is None
    assert gate.check(_bid(proposal_type, 210), ctx, now=NOW) is None


@pytest.mark.parametrize("proposal_type", ["bid_up", "bid_down", "bid_up_rank", "bid_up_servo", "growth_bid_up"])
def test_성과_판단_레인은_전부_막힌다(proposal_type):
    """면제가 «면제 아닌 것»까지 새지 않는지 — 대상 5종 전수."""
    reason = gate._check_data_floor(_ctx(target_weekly_conv=0), proposal_type)
    assert reason is not None and reason.startswith("표본 하한")
