# test_exploration.py — 저볼륨 그룹 탐색 UP 순수 SA 단위테스트 (스프린트 B-X BX1, D-NAO-70)
# 실 API 0·실쓰기 0(BX1은 순수 선정/판정만). DB 픽스처는 test_naver_auto_operator 관례를 따른다.
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverEntity
from app.services.naver_ad import auto_operator, exploration
from app.services.naver_ad import guardrail_gate
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

CAMPAIGN = "cmp-shop"
TODAY = date(2026, 7, 21)
NOW = datetime(2026, 7, 21, 8, 20, 0)  # 시간당 레인 크론 시각(KST naive)
WINDOW = auto_operator._settlement_window(TODAY)  # ([오늘-8, 오늘-2]) — 핫셋과 동일 창


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


def _campaign(db, *, campaign_id=CAMPAIGN, campaign_type="SHOPPING", status="on"):
    db.add(NaverEntity(entity_type="campaign", entity_id=campaign_id,
                       campaign_id=campaign_id, campaign_type=campaign_type, status=status))
    db.commit()


def _adgroup(db, *, adgroup_id, campaign_id=CAMPAIGN, campaign_type="SHOPPING", status="on"):
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
                       campaign_id=campaign_id, campaign_type=campaign_type, status=status))
    db.commit()


def _clicks(db, *, adgroup_id, clk, campaign_id=CAMPAIGN, campaign_type="SHOPPING",
            imp=100, cost=1000, ad_date=None):
    """정착창 안(오늘-5)의 naver_ad_daily 1행 — clk 표본을 심는다."""
    db.add(NaverAdDaily(
        ad_date=ad_date or (TODAY - timedelta(days=5)),
        campaign_id=campaign_id, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id="-", imp=imp, clk=clk, cost=cost,
        conv_direct_amt=0, conv_indirect_amt=0,
    ))
    db.commit()


# ══════════════════════ exploration_candidates ══════════════════════

def test_hotset_excluded_and_warmzone_cold_included(db):
    """핫셋(clk≥10) 제외 · 웜존(clk 9) 포함 · 콜드(clk 0·imp 0) 포함 = 핫셋 여집합 전부."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-hot")     # clk 10 = 핫셋 → 제외
    _adgroup(db, adgroup_id="ag-warm")    # clk 9  = 웜존 → 포함
    _adgroup(db, adgroup_id="ag-cold")    # 행 없음(clk 0·imp 0) → 포함
    _clicks(db, adgroup_id="ag-hot", clk=10)
    _clicks(db, adgroup_id="ag-warm", clk=9)
    # ag-cold: naver_ad_daily 행 자체를 심지 않음(노출0·클릭0 콜드)

    got = exploration.exploration_candidates(db, CAMPAIGN, *WINDOW)
    assert got == [("adgroup", "ag-cold"), ("adgroup", "ag-warm")]  # entity_id 오름차순
    # 상호배타: 핫셋은 여기 없어야
    assert ("adgroup", "ag-hot") not in got


def test_boundary_clk_exactly_10_is_hotset(db):
    """경계: clk==10은 핫셋(≥10) → 탐색 후보 아님(반전 게이트 정확)."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-10")
    _clicks(db, adgroup_id="ag-10", clk=10)
    assert exploration.exploration_candidates(db, CAMPAIGN, *WINDOW) == []


def test_status_off_and_deleted_excluded(db):
    """status off/deleted 그룹은 제외(활성 그룹만 — 병행 세션 deleted 필터 정합)."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-off", status="off")
    _adgroup(db, adgroup_id="ag-del", status="deleted")
    _adgroup(db, adgroup_id="ag-on")
    assert exploration.exploration_candidates(db, CAMPAIGN, *WINDOW) == [("adgroup", "ag-on")]


def test_campaign_off_returns_empty(db):
    """캠페인 엔티티 off/행 부재 → 체인 최상위 비활성(fail-closed)."""
    _campaign(db, status="off")
    _adgroup(db, adgroup_id="ag-1")
    assert exploration.exploration_candidates(db, CAMPAIGN, *WINDOW) == []
    # 캠페인 행 자체가 없는 경우도 동일
    assert exploration.exploration_candidates(db, "cmp-missing", *WINDOW) == []


def test_non_shopping_campaign_defensive_excluded(db):
    """방어적 타입 체크: WEB_SITE/BRAND_SEARCH/타입 미확보 캠페인 → [](fail-closed)."""
    for ctype in ("WEB_SITE", "BRAND_SEARCH", ""):
        db.add(NaverEntity(entity_type="campaign", entity_id="cmp-x", campaign_id="cmp-x",
                           campaign_type=ctype, status="on"))
        db.add(NaverEntity(entity_type="adgroup", entity_id="agx", parent_id="cmp-x",
                           campaign_id="cmp-x", campaign_type=ctype, status="on"))
        db.commit()
        assert exploration.exploration_candidates(db, "cmp-x", *WINDOW) == []
        db.query(NaverEntity).filter(NaverEntity.campaign_id == "cmp-x").delete()
        db.commit()


def test_settlement_clk_matches_auto_operator_agg(db):
    """정착창 clk 집계가 auto_operator._settlement_agg(adgroup grain)와 동일값·sentinel 제외 정합."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-1")
    _clicks(db, adgroup_id="ag-1", clk=3)
    # backfill sentinel 행은 집계에서 제외되어야(auto_operator 관례)
    db.add(NaverAdDaily(
        ad_date=TODAY - timedelta(days=5), campaign_id=CAMPAIGN, campaign_type="SHOPPING",
        adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="-", imp=999, clk=999, cost=0,
        conv_direct_amt=0, conv_indirect_amt=0,
    ))
    db.commit()
    ours = exploration._settlement_clk(db, "ag-1", *WINDOW)
    theirs = auto_operator._settlement_agg(db, "adgroup", "ag-1", *WINDOW)["clk"]
    assert ours == theirs == 3


# ══════════════════════ exploration_trigger (D-NAO-71: 쿨다운 2h 사이클) ══════════════════════

def test_trigger_fires_on_low_click_first_probe():
    """clk<10 · last_step_at=None(첫 탐색) → 발동."""
    fire, reason = exploration.exploration_trigger({"clk": 0, "cost": 0, "conv_amt": 0}, None, NOW)
    assert fire is True and "발동" in reason


def test_trigger_fires_on_imp0_group():
    """imp=0(노출0)이어도 clk<10이면 발동(rank/노출 강등 — 증거 구매 대상)."""
    fire, _ = exploration.exploration_trigger({"clk": 0, "cost": 0, "conv_amt": 0}, None, NOW)
    assert fire is True


def test_trigger_blocked_within_cooldown_2h():
    """마지막 스텝 후 2h 미경과 → 미발동(사이클 대기)."""
    last = NOW - timedelta(hours=1, minutes=30)  # 1.5h < 2h
    fire, reason = exploration.exploration_trigger({"clk": 0, "cost": 0, "conv_amt": 0}, last, NOW)
    assert fire is False and "쿨다운" in reason


def test_trigger_fires_after_cooldown_2h_elapsed():
    """마지막 스텝 후 2h 경과 → 발동(다음 사이클)."""
    last = NOW - timedelta(hours=2, minutes=1)  # 2.0h+ 경과
    fire, _ = exploration.exploration_trigger({"clk": 0, "cost": 0, "conv_amt": 0}, last, NOW)
    assert fire is True


def test_trigger_blocked_when_click_sample_sufficient():
    """clk≥10이면 쿨다운·last_step 무관하게 미발동(핫셋/정착 ROAS 경로)."""
    fire, reason = exploration.exploration_trigger({"clk": 12, "cost": 0, "conv_amt": 0}, None, NOW)
    assert fire is False and "표본 충분" in reason


def test_trigger_boundary_clk_10_blocked():
    fire, _ = exploration.exploration_trigger({"clk": 10, "cost": 0, "conv_amt": 0}, None, NOW)
    assert fire is False


# ══════════════════ ladder_judgment (BX1-rev: 순위 피드백 상태 기계) ══════════════════
# 밴드 = rank 2.5~4. rank>4=밴드 밖(climbing) / 2.5<rank≤4=밴드 안 / rank≤2.5=과열(진입 금지).
from decimal import Decimal

_BAND = exploration._EXPLORATION_TARGET_BAND  # (2.5, 4.0)


def test_ladder_start_when_no_prior_step():
    verdict, reason = exploration.ladder_judgment(None, {"clk": 0, "avg_rank": 6.0}, ceiling=1600, current_bid=800)
    assert verdict == "start" and "첫 탐색" in reason


def test_ladder_start_capped_when_ceiling_already_reached():
    """ⓐ 첫 스텝이지만 경제 증거 전무(ceiling=current) → capped(발동 불가, codex P1 fail-closed 폐루프)."""
    verdict, _ = exploration.ladder_judgment(None, {"clk": 0}, ceiling=800, current_bid=800)
    assert verdict == "capped"


def test_ladder_stop_observe_when_click_arrived():
    """① 이번 사이클 클릭 발생 → 상향 정지·정착 ROAS 관측 인계(hold, 영구 아님)."""
    verdict, reason = exploration.ladder_judgment(
        {"bid": 1040}, {"clk": 3, "cost": 2400, "avg_rank": 5.0}, ceiling=1600, current_bid=1040)
    assert verdict == "stop_observe" and "인계" in reason


def test_ladder_not_rank_when_top_band_no_click():
    """② 과열밴드(rank≤2.5)인데 클릭0 지속 → 순위 병리 아님 진단 종료(explored_not_rank)."""
    verdict, reason = exploration.ladder_judgment(
        {"bid": 1300, "rank": 3.0}, {"clk": 0, "avg_rank": 2.3}, ceiling=2000, current_bid=1300)
    assert verdict == "not_rank" and "진단 종료" in reason


def test_ladder_stop_observe_when_band_reached_no_click():
    """③ 밴드 도달(2.5<rank≤4)·무클릭 → 상향 정지·관측(과열밴드 진입 금지)."""
    verdict, reason = exploration.ladder_judgment(
        {"bid": 1200, "rank": 5.0}, {"clk": 0, "avg_rank": 3.5}, ceiling=2000, current_bid=1200)
    assert verdict == "stop_observe" and "밴드 도달" in reason


def test_ladder_step_up_when_below_band_no_click():
    """⑤ 밴드 밖(rank>4)·무클릭·상한 미도달·과거 클릭 이력 없음 → 정상 적응 스텝(step_up)."""
    verdict, _ = exploration.ladder_judgment(
        {"bid": 900, "rank": 7.0}, {"clk": 0, "avg_rank": 6.0}, ceiling=1600, current_bid=1000,
        recent_flow_clk=0, settled_clk=0)
    assert verdict == "step_up"


def test_ladder_step_up_when_imp0_below_ceiling():
    """imp=0(rank None)·무클릭·상한 미도달·이력 없음 → step_up(콜드 blind climbing)."""
    verdict, _ = exploration.ladder_judgment(
        {"bid": 900, "rank": None}, {"clk": 0, "avg_rank": None}, ceiling=1600, current_bid=1000)
    assert verdict == "step_up"


def test_ladder_reactivate_when_prior_click_but_24h_stagnant():
    """④ 밴드 밖·무클릭·상한 미도달·과거 클릭 이력(정착>0)·최근 24h 클릭0 정체 → 재가동."""
    verdict, reason = exploration.ladder_judgment(
        {"bid": 900, "rank": 4.5}, {"clk": 0, "avg_rank": 5.0}, ceiling=1600, current_bid=1000,
        recent_flow_clk=0, settled_clk=4)
    assert verdict == "reactivate" and "재가동" in reason


def test_ladder_hold_when_flow_alive():
    """④ 최근 24h 클릭 흐름 있음(recent_flow_clk>0) → stop_observe(흐름 살아있으면 이번 사이클
    무클릭이어도 상향 정지 — codex P1 자정 리셋 수정)."""
    verdict, reason = exploration.ladder_judgment(
        {"bid": 900, "rank": 4.5}, {"clk": 0, "avg_rank": 5.0}, ceiling=1600, current_bid=1000,
        recent_flow_clk=2, settled_clk=4)
    assert verdict == "stop_observe" and "흐름" in reason


def test_ladder_fail_toward_hold_when_flow_unavailable():
    """codex P1: 롤링 24h 흐름 확정 불가(flow_available=False) → stop_observe(fail-toward-hold),
    step_up/reactivate 대신 관측 유지(안전 방향)."""
    verdict, reason = exploration.ladder_judgment(
        {"bid": 900, "rank": 5.0}, {"clk": 0, "avg_rank": 6.0}, ceiling=1600, current_bid=1000,
        recent_flow_clk=0, settled_clk=4, flow_available=False)
    assert verdict == "stop_observe" and "fail-toward-hold" in reason


def test_ladder_reactivate_requires_flow_available():
    """대조: flow_available=True·흐름0 정체·과거 이력 → reactivate(정상 재가동)."""
    verdict, _ = exploration.ladder_judgment(
        {"bid": 900, "rank": 5.0}, {"clk": 0, "avg_rank": 6.0}, ceiling=1600, current_bid=1000,
        recent_flow_clk=0, settled_clk=4, flow_available=True)
    assert verdict == "reactivate"


def test_ladder_reactivate_via_last_probe_had_click():
    """④ 프레시 신호: last_probe.had_click(정착창엔 아직 없는 최근 클릭)로도 재가동."""
    verdict, _ = exploration.ladder_judgment(
        {"bid": 900, "rank": 4.5, "had_click": True}, {"clk": 0, "avg_rank": 5.0},
        ceiling=1600, current_bid=1000, recent_flow_clk=0, settled_clk=0)
    assert verdict == "reactivate"


def test_ladder_capped_when_below_band_at_ceiling():
    """④ 밴드 밖·무클릭·경제성 상한 도달 → capped."""
    verdict, reason = exploration.ladder_judgment(
        {"bid": 1600, "rank": 6.0}, {"clk": 0, "avg_rank": 5.5}, ceiling=1600, current_bid=1600)
    assert verdict == "capped" and "상한" in reason


# ══════════════════ adaptive_step (BX1-rev: 순위 목표형 적응 스텝) ══════════════════

def test_adaptive_step_conservative_when_imp0_blind():
    """imp=0(rank None) → 보수적 소폭(+10%). 800→880(floor 10)."""
    got = exploration.adaptive_step(800, None, _BAND, None, exploration._EXPLORATION_STEP_PCT)
    assert got == 880  # 800×1.10=880


def test_adaptive_step_conservative_first_step():
    """첫 스텝(last_step None)·밴드 밖 → 보수적 소폭(+10%). 1000→1100."""
    got = exploration.adaptive_step(1000, 7.0, _BAND, None, exploration._EXPLORATION_STEP_PCT)
    assert got == 1100


def test_adaptive_step_slope_responsive_targets_band():
    """기울기 有(직전 900→1000에 rank 8→6=개선 2/100원): 목표 rank4까지 필요 개선폭 2 →
    필요 증분 = (100/2)×2 = 100 → 1100. 밴드까지 '필요한 만큼만'(눈먼 30% 아님)."""
    last = {"bid": 900, "rank": 8.0}
    got = exploration.adaptive_step(1000, 6.0, _BAND, last, exploration._EXPLORATION_STEP_PCT)
    assert got == 1100  # 1000 + (100원/rank)×(6-4)=1000+100


def test_adaptive_step_shrinks_near_band():
    """밴드 접근 시 스텝 축소(오버슈트 과지불 방지): rank 4.5(개선폭 0.5만 필요), 기울기 100원/rank
    → 증분 = 100×0.5 = 50 → 1050(첫 스텝 10%=1100보다 작음)."""
    last = {"bid": 900, "rank": 5.5}  # 1000에서 5.5 관측? 기울기 (5.5-4.5)/(1000-900)=1/100
    got = exploration.adaptive_step(1000, 4.5, _BAND, last, exploration._EXPLORATION_STEP_PCT)
    assert got == 1050  # 1000 + (100원/rank개선1)×(4.5-4.0)=1000+50


def test_adaptive_step_escalates_when_unresponsive():
    """순위 무반응(직전 900→1000인데 rank 6→6 개선0) → 직전 Δ입찰(100)×점증(1.5)=150 → 1150."""
    last = {"bid": 900, "rank": 6.0}
    got = exploration.adaptive_step(1000, 6.0, _BAND, last, exploration._EXPLORATION_STEP_PCT)
    assert got == 1150  # 1000 + 100×1.5


def test_adaptive_step_capped_at_30pct():
    """적응 산정이 30% 상한을 넘으면 상한으로 클램프(1스텝 상한 = 30%, floor 10원)."""
    last = {"bid": 900, "rank": 20.0}  # 큰 개선폭 → 큰 증분 요구
    got = exploration.adaptive_step(1000, 18.0, _BAND, last, exploration._EXPLORATION_STEP_PCT)
    assert got == 1300  # min(산정, 1000×1.30=1300)


def test_adaptive_step_none_when_in_band():
    """이미 밴드 내(rank≤4) → None(상향 불필요 = 밴드 도달)."""
    assert exploration.adaptive_step(1000, 3.5, _BAND, None, exploration._EXPLORATION_STEP_PCT) is None


def test_adaptive_step_never_overshoots_below_2_5():
    """과열밴드 미진입: 목표는 밴드 하단(rank4)이므로 산정 증분이 rank<2.5로 오버슈트하지 않는다.
    기울기 가팔라도(1원/rank) 목표=rank4까지만 → 증분 = 1×(6-4)=2원 → floor 소실 → None(hold)."""
    last = {"bid": 999, "rank": 7.0}  # 기울기 (7-6)/(1000-999)=1/1 매우 가파름
    got = exploration.adaptive_step(1000, 6.0, _BAND, last, exploration._EXPLORATION_STEP_PCT)
    # 증분 = 1원/rank × (6-4)=2원 → 1002 → floor 10 → 1000 = 현재 이하 → None
    assert got is None


def test_adaptive_step_none_when_current_bid_invalid():
    assert exploration.adaptive_step(0, 6.0, _BAND, None, exploration._EXPLORATION_STEP_PCT) is None


def test_adaptive_step_floor_rounding_no_30pct_breach():
    """floor(//10*10) 반올림 — 30% 상한 미세 초과 방지(P2③). 815×1.30=1059.5 → cap floor 1050."""
    last = {"bid": 700, "rank": 30.0}
    got = exploration.adaptive_step(815, 25.0, _BAND, last, exploration._EXPLORATION_STEP_PCT)
    assert got == 1050 and got <= int(815 * 1.30)  # floor cap, ≤ 30%


# ══════════════════════ 상수 ══════════════════════

def test_constants_values():
    # D-NAO-71: 런당 캡 삭제 — 상수 부재를 고정
    assert not hasattr(exploration, "_EXPLORATION_RUN_CAP")
    assert exploration._EXPLORATION_STEP_PCT == Decimal("0.30")  # D-NAO-71: 30% 1스텝 상한
    assert exploration._EXPLORATION_CEILING_MULT == Decimal("2.0")
    assert exploration._EXPLORATION_COOLDOWN_HOURS == 2
    assert exploration._EXPLORATION_BAND_LOW == Decimal("2.5")
    assert exploration._EXPLORATION_BAND_HIGH == Decimal("4.0")
    assert exploration._EXPLORATION_FLOW_WINDOW_H == 24


def test_click_gate_consistent_with_hotset():
    """탐색 상호배타 게이트가 핫셋 게이트(auto_operator._MIN_CLICK_FOR_APPROVAL)와 동일값 —
    조용한 divergence 차단(로컬 복제 상수 정합 고정)."""
    assert exploration._MIN_CLICK_FOR_EXPLORATION == auto_operator._MIN_CLICK_FOR_APPROVAL


def test_cooldown_consistent_with_guardrail():
    """탐색 사이클 간격이 guardrail_gate._COOLDOWN_HOURS(D-NAO-19)와 동일값(가드3 정합)."""
    assert exploration._EXPLORATION_COOLDOWN_HOURS == guardrail_gate._COOLDOWN_HOURS


def test_step_pct_exceeds_guardrail_max_change():
    """D-NAO-71: 탐색 스텝 30% > guardrail_gate._MAX_CHANGE_PCT(15%) — ±15% 면제 타입으로
    발사돼야 함을 명시(면제 타입 등록·배선은 BX2)."""
    assert exploration._EXPLORATION_STEP_PCT > guardrail_gate._MAX_CHANGE_PCT


# ══════════════════ VT4 (D-NAO-82①): 플로어 스텝 함정 수정 ══════════════════
# 배경(07-22 prod 해부): 03 아이폰17프로 rank 6.39·bid 50·clk 0인데 탐색 발사 이력 0.
# 원인 = adaptive_step cap=floor(50×1.3/10)×10=60이나 _finalize_step이 60<70(구 하한)으로 None
# + 보수 10%(55)가 10원 내림에서 current(50)로 소실. 쇼핑 하한 50 + 최소 증분 보장으로 수정.

def test_vt4_floor_step_50_to_60_conservative_first_step():
    """★핵심 결함 수정: 50원 그룹 첫 스텝(밴드 밖·last_step None) — 보수 10%(55)가 내림에서
    소실되던 것을 최소 증분(+10)으로 승격 → 50→60(cap_bid=60 이내)."""
    got = exploration.adaptive_step(50, 6.39, _BAND, None, exploration._EXPLORATION_STEP_PCT)
    assert got == 60  # 50×1.1=55→floor 50=current→+10 승격→60(=cap floor(50×1.3)=60)


def test_vt4_floor_step_50_to_60_imp0_blind():
    """imp=0(rank None) 눈먼 구간의 50원 그룹도 플로어 트랩 수정으로 50→60."""
    got = exploration.adaptive_step(50, None, _BAND, None, exploration._EXPLORATION_STEP_PCT)
    assert got == 60


def test_vt4_min_increment_only_on_blind_paths():
    """가드: 최소 증분 승격은 **눈먼 구간(첫 스텝·imp0·기울기 부재)** 에서만. 실관측 기울기
    경로는 측정 소폭이 소실돼도 승격하지 않는다(강제 +10원의 과열밴드<2.5 오버슈트 방지)."""
    # 눈먼 경로: 50→60 승격.
    assert exploration.adaptive_step(50, 6.0, _BAND, None, exploration._EXPLORATION_STEP_PCT) == 60
    # 실관측 기울기 경로: last={bid:40, rank:10.0} → 기울기 (10-6)/(50-40)=0.4 rank/원 →
    # 증분=(10원/4rank)×(6-4)=5원 → 55 → floor 50 = current → None(승격 안 함).
    last = {"bid": 40, "rank": 10.0}
    assert exploration.adaptive_step(50, 6.0, _BAND, last, exploration._EXPLORATION_STEP_PCT) is None


def test_vt4_min_increment_none_when_cap_below_current_plus_10():
    """극단: 현 입찰이 상한(_MAX_BID) 근처라 cap_bid<current+10이면 승격 불가 → None 유지
    (상한이 스텝을 막는 정상 동작)."""
    got = exploration.adaptive_step(100_000, 6.0, _BAND, None, exploration._EXPLORATION_STEP_PCT)
    # cap_bid=min(floor(100000×1.3),100000)=100000=current → +10(100010)>cap → 승격 불가 → None
    assert got is None


def test_vt4_70plus_paths_no_regression():
    """70원↑ 실관측/보수 경로 회귀 없음: 하한 50 교체가 기존 70+ 결과를 바꾸지 않는다(스텝은
    항상 current 초과=최소 80이라 50/70 하한 어느 쪽도 통과)."""
    # 보수 첫 스텝 1000→1100(변화 없음)
    assert exploration.adaptive_step(1000, 7.0, _BAND, None, exploration._EXPLORATION_STEP_PCT) == 1100
    # 실관측 기울기 1000→1100(변화 없음)
    assert exploration.adaptive_step(1000, 6.0, _BAND, {"bid": 900, "rank": 8.0},
                                     exploration._EXPLORATION_STEP_PCT) == 1100


def test_vt4_heuristic_ceiling_50won_no_70_floor():
    """_heuristic_ceiling 70원 하한이 50원 입력을 막지 않음(raw=100→rounded=100≥70) — 고정."""
    assert exploration._heuristic_ceiling(50) == 100


def test_vt4_shopping_min_bid_constant():
    """쇼핑 하한 상수 신설·키워드 하한(70) 보존 고정."""
    assert exploration._EXPLORATION_MIN_BID_SHOPPING == 50
    assert exploration._EXPLORATION_MIN_BID == 70  # (보존) 키워드 grain 출처
