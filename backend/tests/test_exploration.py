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


# ══════════════════════ ladder_judgment (D-NAO-71: 사이클 판정 4분기) ══════════════════════

def test_ladder_start_when_no_prior_step():
    verdict, reason = exploration.ladder_judgment(None, {"clk": 0}, ceiling=1600, current_bid=800)
    assert verdict == "start" and "첫 탐색" in reason


def test_ladder_stop_observe_when_click_arrived():
    verdict, reason = exploration.ladder_judgment(
        {"bid": 1040}, {"clk": 3, "cost": 2400}, ceiling=1600, current_bid=1040)
    assert verdict == "stop_observe" and "인계" in reason


def test_ladder_step_up_when_no_click_below_ceiling():
    verdict, _ = exploration.ladder_judgment(
        {"bid": 1040}, {"clk": 0, "cost": 0}, ceiling=1600, current_bid=1040)
    assert verdict == "step_up"


def test_ladder_capped_when_no_click_at_ceiling():
    verdict, reason = exploration.ladder_judgment(
        {"bid": 1600}, {"clk": 0, "cost": 0}, ceiling=1600, current_bid=1600)
    assert verdict == "capped" and "상한" in reason


def test_ladder_capped_when_above_ceiling():
    verdict, _ = exploration.ladder_judgment(
        {"bid": 1700}, {"clk": 0, "cost": 0}, ceiling=1600, current_bid=1700)
    assert verdict == "capped"


# ══════════════════════ 상수 ══════════════════════

def test_constants_values():
    from decimal import Decimal
    # D-NAO-71: 런당 캡 삭제 — 상수 부재를 고정
    assert not hasattr(exploration, "_EXPLORATION_RUN_CAP")
    assert exploration._EXPLORATION_STEP_PCT == Decimal("0.30")  # D-NAO-71: 30%
    assert exploration._EXPLORATION_CEILING_MULT == Decimal("2.0")
    assert exploration._EXPLORATION_COOLDOWN_HOURS == 2


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
