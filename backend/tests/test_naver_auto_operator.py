# test_naver_auto_operator.py — auto_operator 일 레인 단위테스트 (D-NAO-49, A1)
# 시간당 레인 테스트는 다음 커밋(A2+A3)에서 이 파일에 추가된다.
# 실 API 호출 0 — naver_sa_writer(라이브 재조회)와 naver_execution_harness.execute(실행)는 mock.
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
