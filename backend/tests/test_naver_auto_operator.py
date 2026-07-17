# test_naver_auto_operator.py — auto_operator 일 레인 + 시간당 레인 단위테스트 (D-NAO-49)
# 실 API 호출 0 — naver_sa_writer(라이브 재조회)와 naver_execution_harness.execute(실행)는
# 대부분 mock. 단, "쿨다운/가드레일 차단" 통합 테스트 1건만 harness.execute를 실제로 태워
# guardrail_gate까지 관통하는 것을 증명한다(naver_sa_writer만 최하단에서 mock).
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverHourlySnapshot,
    NaverProposal,
    NaverRetroSignal,
)
from app.services.naver_ad import auto_operator

CAMPAIGN = "cmp-04"
TODAY = date(2026, 7, 20)
NOW = datetime(2026, 7, 20, 8, 50, 0)  # 일 레인 크론 시각(KST naive)
DAY_START_UTC = datetime.combine(TODAY, datetime.min.time()) - timedelta(hours=9)


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


def _settings(db, *, campaign_id=CAMPAIGN, auto_operate=True, optimizer="ours",
              target_roas_override=None):
    db.add(NaverCampaignSettings(
        campaign_id=campaign_id, auto_operate=auto_operate, optimizer=optimizer,
        target_roas_override=target_roas_override,
    ))
    db.commit()


def _proposal(db, *, proposal_type="bid_up", campaign_id=CAMPAIGN, target_type="keyword",
              target_id="nkw-1", target_bid=None, rationale="[bleeding_keywords] 보정ROAS=1.0 cost=100원 clk=15 — 시뮬 근거=x",
              status="pending", created_at=None, adgroup_id=None):
    p = NaverProposal(
        proposal_type=proposal_type, target_type=target_type, target_id=target_id,
        campaign_id=campaign_id, adgroup_id=adgroup_id, rationale=rationale,
        expected_effect="테스트 예상효과", status=status, target_bid=target_bid,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    ts = created_at if created_at is not None else DAY_START_UTC + timedelta(hours=1)
    db.query(NaverProposal).filter(NaverProposal.id == p.id).update({"created_at": ts})
    db.commit()
    db.refresh(p)
    return p


def _ad_row(db, *, campaign_id=CAMPAIGN, campaign_type="WEB_SITE", adgroup_id="grp-1",
            keyword_id="nkw-1", ad_date, imp=10, clk=5, cost=1000,
            conv_direct_amt=0, conv_indirect_amt=0):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id=keyword_id, imp=imp, clk=clk, cost=cost,
        conv_direct_amt=conv_direct_amt, conv_indirect_amt=conv_indirect_amt,
    ))
    db.commit()


def _retro_signal(db, *, asof_date, board, target_id, campaign_id=CAMPAIGN, grain="keyword",
                   direction="down"):
    db.add(NaverRetroSignal(
        created_at=NOW, asof_date=asof_date, board=board, direction=direction, grain=grain,
        target_id=target_id, campaign_id=campaign_id, cf_asof=1.0, bep_asof=1.0,
        target_asof=1.5, cost_asof=100,
    ))
    db.commit()


def _settlement_window():
    return auto_operator._settlement_window(TODAY)


# ══════════════════════════ 일 레인 ══════════════════════════

def test_daily_lane_reviews_nothing_when_no_auto_operate_campaign(db):
    _proposal(db)  # NaverCampaignSettings 행 자체 없음(auto_operate 기본 False 취급)
    result = auto_operator.run_daily_lane(db, now=NOW)
    assert result == {"reviewed": 0, "approved": 0, "executed": 0, "held": [], "failed": 0}


def test_daily_lane_ignores_non_auto_operate_campaign(db):
    _settings(db, campaign_id="cmp-other", auto_operate=False)
    _proposal(db, campaign_id="cmp-other", proposal_type="bid_down")
    result = auto_operator.run_daily_lane(db, now=NOW)
    assert result["reviewed"] == 0


def test_daily_lane_ignores_informational_proposal(db):
    _settings(db)
    _proposal(db, proposal_type="trigger_pacing", target_type="campaign", target_id=CAMPAIGN)
    result = auto_operator.run_daily_lane(db, now=NOW)
    assert result["reviewed"] == 0


def test_daily_lane_ignores_stale_pending_created_yesterday(db):
    _settings(db)
    _proposal(db, proposal_type="bid_down", created_at=DAY_START_UTC - timedelta(hours=1))
    result = auto_operator.run_daily_lane(db, now=NOW)
    assert result["reviewed"] == 0


def test_daily_lane_bid_down_always_approved(db):
    _settings(db)
    p = _proposal(db, proposal_type="bid_down", target_bid=900)
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_called_once_with(db, p.id, dry_run=False, now=NOW)
    assert result["reviewed"] == 1
    assert result["approved"] == 1
    assert result["held"] == []
    db.refresh(p)
    assert p.status == "approved"
    assert p.approval_source == "auto_operator"


def test_daily_lane_pause_held_when_recent_external_stop(db):
    _settings(db)
    p = _proposal(db, proposal_type="pause", target_type="keyword", target_id="nkw-pause")
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-pause", campaign_id=CAMPAIGN,
        action="external_status_change", dry_run=False,
        after_value=json.dumps({"userLock": True}), changed_at=NOW - timedelta(hours=1),
    ))
    db.commit()
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_not_called()
    assert result["approved"] == 0
    assert len(result["held"]) == 1
    assert result["held"][0]["id"] == p.id
    assert "D-NAO-40" in result["held"][0]["reason"]
    db.refresh(p)
    assert p.status == "pending"


def test_daily_lane_pause_approved_when_no_external_stop(db):
    _settings(db)
    p = _proposal(db, proposal_type="pause", target_type="keyword", target_id="nkw-pause2")
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_called_once_with(db, p.id, dry_run=False, now=NOW)
    assert result["approved"] == 1
    assert result["held"] == []


def _seed_bid_up_happy_path(db, *, target_id="nkw-1", current_bid=1000, target_bid=1100,
                             clk=15, exclude_bleeding=True, roas_row=True):
    _settings(db, target_roas_override=Decimal("2.0"))
    rationale = f"[bleeding_keywords] 보정ROAS=1.0 cost=100원 clk={clk} — 시뮬 근거=x, 추천입찰={target_bid}원"
    p = _proposal(db, proposal_type="bid_up", target_id=target_id, target_bid=target_bid,
                  rationale=rationale)
    window_from, window_to = _settlement_window()
    if roas_row:
        # roas_naver = conv_amt/cost = 3000/1000 = 3.0 >= target 2.0
        _ad_row(db, keyword_id=target_id, ad_date=window_from + timedelta(days=1),
                imp=50, clk=20, cost=1000, conv_direct_amt=3000)
    if exclude_bleeding:
        # 최신 소급채점은 있지만(latest_asof 확보) 이 target은 그 보드에 없음(안 걸림)
        _retro_signal(db, asof_date=TODAY - timedelta(days=1), board="bleeding_keywords",
                      target_id="nkw-other")
    return p, current_bid


def test_daily_lane_bid_up_all_4_conditions_met_approved_and_executed(db):
    p, current_bid = _seed_bid_up_happy_path(db)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": current_bid}), \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1")}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)

    mock_exec.assert_called_once_with(db, p.id, dry_run=False, now=NOW)
    assert result["reviewed"] == 1
    assert result["approved"] == 1
    assert result["held"] == []
    db.refresh(p)
    assert p.status == "approved"
    assert p.approval_source == "auto_operator"


def test_daily_lane_bid_up_condition1_fails_step_clamp_out_of_range(db):
    # target_bid=1500이 현재가 1000 대비 +50% — ±15% 상한 이탈
    p, current_bid = _seed_bid_up_happy_path(db, current_bid=1000, target_bid=1500)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": current_bid}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_not_called()
    assert result["approved"] == 0
    assert len(result["held"]) == 1
    assert "①" in result["held"][0]["reason"]
    db.refresh(p)
    assert p.status == "pending"  # harness에 안 넘어가 failed로 종결되지 않음


def test_daily_lane_bid_up_condition2_fails_click_below_10(db):
    p, current_bid = _seed_bid_up_happy_path(db, clk=3)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": current_bid}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_not_called()
    assert "②" in result["held"][0]["reason"]


def test_daily_lane_bid_up_condition3_fails_no_settlement_roas_evidence(db):
    p, current_bid = _seed_bid_up_happy_path(db, roas_row=False)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": current_bid}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_not_called()
    assert "③" in result["held"][0]["reason"]


def test_daily_lane_bid_up_condition4_fails_currently_bleeding(db):
    p, current_bid = _seed_bid_up_happy_path(db, exclude_bleeding=False, target_id="nkw-1")
    window_from, window_to = _settlement_window()
    _retro_signal(db, asof_date=TODAY - timedelta(days=1), board="bleeding_keywords",
                  target_id="nkw-1")
    with patch.object(auto_operator.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": current_bid}), \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1")}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_not_called()
    assert "④" in result["held"][0]["reason"]


# ══════════════════════════ 시간당 레인(A2+A3) ══════════════════════════

def _hour(h, *, imp, clk, cost, avg_rank=None):
    return {"hour": h, "imp": imp, "clk": clk, "cost": cost, "avg_rank": avg_rank}


def test_hourly_lane_hot_set_only_clicks_ge_10_and_imp_today(db):
    _settings(db)
    window_from, window_to = _settlement_window()
    # qualifies: clk=10 in settlement window
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-hot", campaign_id=CAMPAIGN, status="on"))
    _ad_row(db, keyword_id="nkw-hot", ad_date=window_from, clk=10, cost=100)
    # below threshold: clk=5
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-cold", campaign_id=CAMPAIGN, status="on"))
    _ad_row(db, keyword_id="nkw-cold", ad_date=window_from, clk=5, cost=100)
    db.commit()

    calls = []

    def fetch(target_id, stat_date):
        calls.append(target_id)
        if target_id == "nkw-hot":
            return [_hour(9, imp=0, clk=0, cost=0)]  # 당일 imp=0 → held(당일 imp 없음)
        raise AssertionError("hot-set 밖 대상이 조회됨")

    result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=fetch)
    assert calls == ["nkw-hot"]  # nkw-cold는 클릭 미달로 애초에 조회조차 안 됨
    assert result["reviewed"] == 1
    assert result["held"][0]["target_id"] == "nkw-hot"
    assert "imp" in result["held"][0]["reason"]


def test_hourly_lane_low_imp_bucket_held(db):
    _settings(db)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-lowimp", campaign_id=CAMPAIGN, status="on"))
    _ad_row(db, keyword_id="nkw-lowimp", ad_date=window_from, clk=10, cost=100)
    db.commit()

    curve = [_hour(6, imp=5, clk=1, cost=50, avg_rank=3.0), _hour(7, imp=10, clk=1, cost=50, avg_rank=3.0)]
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=NOW, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_not_called()
    assert "표본 부족" in result["held"][0]["reason"]


def test_hourly_lane_down_priority_when_rank_below_2_5(db):
    _settings(db)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-down", campaign_id=CAMPAIGN, status="on"))
    _ad_row(db, keyword_id="nkw-down", ad_date=window_from, clk=10, cost=100)
    db.commit()

    curve = [
        _hour(6, imp=15, clk=2, cost=100, avg_rank=2.0),
        _hour(7, imp=15, clk=2, cost=100, avg_rank=2.0),
        _hour(8, imp=15, clk=2, cost=100, avg_rank=2.0),
    ]
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=NOW, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_called_once()
    assert result["approved"] == 1
    proposal_id = mock_exec.call_args[0][1]
    saved = db.get(NaverProposal, proposal_id)
    assert saved.proposal_type == "bid_down"
    assert saved.approval_source == "auto_operator_hourly"
    assert saved.rationale.startswith("[시간당밴드]")
    assert saved.target_bid == 850  # 1000×0.85 → 10원 올림 클램프


def test_hourly_lane_down_on_cpc_spike(db):
    _settings(db)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-cpc", campaign_id=CAMPAIGN, status="on"))
    # baseline: cost=1000/clk=10 → CPC=100원, 급등 임계=200원(×2)
    _ad_row(db, keyword_id="nkw-cpc", ad_date=window_from, clk=10, cost=1000)
    db.commit()

    curve = [
        _hour(6, imp=15, clk=2, cost=500, avg_rank=3.0),
        _hour(7, imp=15, clk=2, cost=500, avg_rank=3.0),
        _hour(8, imp=10, clk=2, cost=500, avg_rank=3.0),
    ]  # today CPC = 1500/6 = 250원 > 200원
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=NOW, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_called_once()
    proposal_id = mock_exec.call_args[0][1]
    saved = db.get(NaverProposal, proposal_id)
    assert saved.proposal_type == "bid_down"
    assert "CPC급등" in saved.rationale


def test_hourly_lane_up_only_when_all_3_conditions_met(db):
    _settings(db, target_roas_override=Decimal("2.0"))
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-up", campaign_id=CAMPAIGN, status="on"))
    # roas_naver = 21000/7000 = 3.0 >= 2.0, baseline CPC = 7000/20 = 350원
    _ad_row(db, keyword_id="nkw-up", ad_date=window_from, clk=20, cost=7000, conv_direct_amt=21000)
    db.commit()

    up_curve = [
        _hour(10, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(11, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(12, imp=15, clk=2, cost=30, avg_rank=5.0),
    ]  # imp=45, weighted_rank=5.0>4.0, today CPC=100/6≈16.7(급등 아님), 오늘소진100 ≪ 일평균1000
    now_midday = datetime(2026, 7, 20, 12, 20, 0)  # 선형기대=740/1440≈0.514 vs 실제0.1 → 저속
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1")}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_midday, fetch_intraday=lambda tid, d: up_curve,
        )
    mock_exec.assert_called_once()
    proposal_id = mock_exec.call_args[0][1]
    saved = db.get(NaverProposal, proposal_id)
    assert saved.proposal_type == "bid_up"
    assert saved.target_bid == 1150  # 1000×1.15 → 10원 내림 클램프(이미 배수)


def test_hourly_lane_up_not_fired_when_roas_condition_missing(db):
    """rank>4.0·페이싱저속은 충족하지만 정착창 실적 자체가 없어 ROAS 검증 불가 → hold
    (3조건 동시 충족 요구 — 2개만 만족해도 up 아님)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-up2", campaign_id=CAMPAIGN, status="on"))
    window_from, window_to = _settlement_window()
    # 핫셋 자격만 채우는 별도 클릭 없이는애초에 hot-set에 안 들어가므로, clk>=10인 행을
    # 넣되 cost=0으로 만들어(collected 0 cost) roas 검증만 실패하게 한다.
    _ad_row(db, keyword_id="nkw-up2", ad_date=window_from, clk=10, cost=0)
    db.commit()

    curve = [
        _hour(10, imp=15, clk=1, cost=5, avg_rank=5.0),
        _hour(11, imp=15, clk=1, cost=5, avg_rank=5.0),
        _hour(12, imp=15, clk=1, cost=5, avg_rank=5.0),
    ]
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_midday, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_not_called()
    assert result["held"][0]["reason"] == "판정 조건 미충족(기본 hold)"


def test_hourly_lane_default_hold_when_rank_in_neutral_band(db):
    _settings(db)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-neutral", campaign_id=CAMPAIGN, status="on"))
    _ad_row(db, keyword_id="nkw-neutral", ad_date=window_from, clk=10, cost=1000)
    db.commit()

    curve = [
        _hour(6, imp=15, clk=1, cost=50, avg_rank=3.2),
        _hour(7, imp=15, clk=1, cost=50, avg_rank=3.2),
        _hour(8, imp=10, clk=1, cost=50, avg_rank=3.2),
    ]  # rank 2.5~4.0 사이(중립), today CPC=150/3=50원 < baseline100×2
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=NOW, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_not_called()
    assert result["held"][0]["reason"] == "판정 조건 미충족(기본 hold)"


def test_hourly_lane_spend_circuit_breaker_holds_entire_campaign(db):
    _settings(db)
    window_from, window_to = _settlement_window()
    # 직전 7일 일평균 = 700/7 = 100원
    for i in range(7):
        _ad_row(db, keyword_id="", adgroup_id="grp-x", campaign_type="SHOPPING",
                ad_date=window_to - timedelta(days=i), clk=1, cost=100)
    db.add(NaverHourlySnapshot(
        snapshot_at=NOW, ad_date=TODAY, snapshot_hour=8, campaign_id=CAMPAIGN,
        campaign_type="SHOPPING", cost=500, clk=10, imp=100,  # 500 > 100×3
    ))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-never", campaign_id=CAMPAIGN, status="on"))
    _ad_row(db, keyword_id="nkw-never", ad_date=window_from, clk=99, cost=100)
    db.commit()

    def fetch(tid, d):
        raise AssertionError("서킷브레이커가 걸리면 intraday 조회 자체가 없어야 함")

    result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=fetch)
    assert result["reviewed"] == 0
    assert len(result["held"]) == 1
    assert result["held"][0]["campaign_id"] == CAMPAIGN
    assert "서킷브레이커" in result["held"][0]["reason"]


def test_hourly_lane_intraday_fetch_failure_skips_group(db):
    _settings(db)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-fail", campaign_id=CAMPAIGN, status="on"))
    _ad_row(db, keyword_id="nkw-fail", ad_date=window_from, clk=10, cost=100)
    db.commit()

    def fetch(tid, d):
        raise RuntimeError("HTTP 400")

    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=fetch)
    mock_exec.assert_not_called()
    assert result["skipped"] == 1
    assert result["held"] == []


def test_clamp_step_15pct_and_10won_rounding():
    assert auto_operator._clamp_step(1234, "up") == 1410  # 1419.1 → floor10
    assert auto_operator._clamp_step(1234, "down") == 1050  # 1048.9 → ceil10
    assert auto_operator._clamp_step(1000, "up") == 1150
    assert auto_operator._clamp_step(1000, "down") == 850
    # 절대하한(70원)에서는 내릴 여지가 없어 스텝 자체가 무의미 — None(스텝 소실, proposal_writer의
    # 스텝 소실 skip 관례와 동일 원칙: 억지로 같은 값을 반환하지 않는다).
    assert auto_operator._clamp_step(70, "down") is None


# ── 통합 케이스: 쿨다운/가드레일 차단이 실행을 실제로 막는지(harness.execute 실호출) ──

def test_hourly_lane_execution_blocked_by_real_guardrail_cooldown(db):
    _settings(db)
    window_from, window_to = _settlement_window()
    target_id = "nkw-cd"
    db.add(NaverEntity(entity_type="keyword", entity_id=target_id, campaign_id=CAMPAIGN, status="on"))
    _ad_row(db, keyword_id=target_id, ad_date=window_from, clk=10, cost=100)
    # 1시간 전 우리 시스템이 이미 이 키워드를 변경 — 쿨다운 5시간 이내(guardrail_gate)
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id=target_id, campaign_id=CAMPAIGN,
        action="update_bid", dry_run=False,
        after_value=json.dumps({"bidAmt": 1000, "userLock": False}),
        changed_at=NOW - timedelta(hours=1),
    ))
    db.commit()

    curve = [
        _hour(6, imp=15, clk=2, cost=100, avg_rank=2.0),
        _hour(7, imp=15, clk=2, cost=100, avg_rank=2.0),
        _hour(8, imp=15, clk=2, cost=100, avg_rank=2.0),
    ]  # rank<2.5 → down 판정
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}):
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda tid, d: curve)

    assert result["approved"] == 1  # auto_operator는 승인까지는 함(harness가 최종 차단)
    assert result["executed"] == 0
    assert result["failed"] == 1

    proposals = db.query(NaverProposal).filter(NaverProposal.target_id == target_id).all()
    assert len(proposals) == 1
    assert proposals[0].status == "failed"  # guardrail_gate 차단 → harness._guard_failure

    logs = db.query(NaverChangeLog).filter(
        NaverChangeLog.entity_id == target_id, NaverChangeLog.action == "update_bid",
    ).order_by(NaverChangeLog.id.desc()).all()
    blocked = [l for l in logs if l.outcome == "failed"]
    assert len(blocked) == 1
    assert "가드레일 차단" in blocked[0].rationale
    assert "쿨다운" in blocked[0].rationale
