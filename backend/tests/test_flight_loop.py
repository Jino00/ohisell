# test_flight_loop.py — X2 T3 flight_loop Harness 단위 테스트
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily, NaverCampaignSettings, NaverChangeLog, NaverEntity,
    NaverForecastDaily, NaverHourlyPatternHistory, NaverHourlySnapshot,
    NaverProductBep, Order,
)
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.flight_loop import run_flight_loop


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


def _setup_campaign(db, campaign_id="cmp-test", daily_budget=100000):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer="ours"))
    db.add(NaverEntity(
        entity_type="campaign", entity_id=campaign_id, campaign_id=campaign_id,
        campaign_type="WEB_SITE", name="테스트캠페인", status="on",
    ))
    db.add(NaverForecastDaily(
        target_date=date(2026, 7, 11), grain="campaign", scope_key=campaign_id,
        pred_clk=100, pred_cost=50000, pred_conv_amt=200000,
    ))
    db.add(NaverHourlySnapshot(
        snapshot_at=datetime(2026, 7, 11, 10, 0), ad_date=date(2026, 7, 11),
        snapshot_hour=10, campaign_id=campaign_id, campaign_type="WEB_SITE",
        cost=20000, clk=40, imp=4000, daily_budget=daily_budget,
    ))
    for h in range(24):
        db.add(NaverHourlyPatternHistory(weekday=4, hour=h, clk_sum=10, cost_sum=2000, sample_days=4))
    db.add(NaverAdDaily(
        ad_date=date(2026, 7, 10), campaign_id=campaign_id, adgroup_id="grp-1",
        keyword_id="kw-1", campaign_type="WEB_SITE",
        clk=50, cost=25000, imp=5000, conv_direct_amt=100000, conv_indirect_amt=50000,
    ))
    db.commit()


def test_flight_loop_no_ours_campaigns(db):
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    assert result["campaigns_processed"] == 0


def test_flight_loop_processes_ours_campaign(db):
    _setup_campaign(db)
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10, dry_run=True)
    assert result["campaigns_processed"] == 1
    assert result["dry_run"] is True
    d = result["decisions"][0]
    assert "alpha" in d
    assert d["dry_run"] is True
    assert d["campaign_id"] == "cmp-test"


def test_flight_loop_records_change_log(db):
    _setup_campaign(db)
    run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    logs = db.query(NaverChangeLog).filter(
        NaverChangeLog.action == "flight_pacing",
        NaverChangeLog.campaign_id == "cmp-test",
    ).all()
    assert len(logs) == 1
    assert logs[0].dry_run is True
    assert "α=" in logs[0].rationale


def test_flight_loop_skips_campaign_without_forecast(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-no-forecast", optimizer="ours"))
    db.commit()
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    assert result["decisions"][0].get("skipped") == "forecast 없음"


def test_flight_loop_alpha_within_bounds(db):
    _setup_campaign(db)
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    d = result["decisions"][0]
    from app.services.naver_ad.pacing_controller import ALPHA_MIN, ALPHA_MAX
    assert ALPHA_MIN <= d["alpha"] <= ALPHA_MAX


def test_flight_loop_tight_budget_reduces_alpha(db):
    _setup_campaign(db, daily_budget=25000)
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    d = result["decisions"][0]
    assert d["alpha"] <= 1.0
    assert d["daily_budget"] == 25000
    assert d["remaining_budget"] == 5000


def test_flight_loop_multiple_campaigns(db):
    _setup_campaign(db, campaign_id="cmp-a")
    db.add(NaverCampaignSettings(campaign_id="cmp-b", optimizer="ours"))
    db.add(NaverForecastDaily(
        target_date=date(2026, 7, 11), grain="campaign", scope_key="cmp-b",
        pred_clk=50, pred_cost=30000, pred_conv_amt=80000,
    ))
    db.add(NaverHourlySnapshot(
        snapshot_at=datetime(2026, 7, 11, 10, 0), ad_date=date(2026, 7, 11),
        snapshot_hour=10, campaign_id="cmp-b", campaign_type="WEB_SITE",
        cost=10000, clk=20, imp=2000, daily_budget=50000,
    ))
    db.commit()
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    assert result["campaigns_processed"] == 2


def test_flight_loop_uncapped_budget_not_treated_as_exhausted(db):
    """P2-1 regression: dailyBudget=0 means uncapped, not exhausted."""
    _setup_campaign(db, daily_budget=0)
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    d = result["decisions"][0]
    assert d["daily_budget"] is None
    assert d["remaining_budget"] is None
    assert d["binding_constraint"] != "budget", (
        "uncapped campaign must never be budget-bound"
    )


def test_flight_loop_total_vs_remaining_budget_comparison(db):
    """P2-2 regression: controller gets daily_budget (total), not remaining."""
    _setup_campaign(db, daily_budget=100000)
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    d = result["decisions"][0]
    assert d["daily_budget"] == 100000
    assert d["remaining_budget"] == 80000
    assert d["binding_constraint"] != "budget" or d["alpha"] >= 0.9, (
        "100k budget with 20k spent should not aggressively bind"
    )


def test_flight_loop_error_in_one_campaign_doesnt_block_others(db):
    _setup_campaign(db, campaign_id="cmp-good")
    db.add(NaverCampaignSettings(campaign_id="cmp-bad", optimizer="ours"))
    db.commit()
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    assert result["campaigns_processed"] == 2
    good = [d for d in result["decisions"] if d["campaign_id"] == "cmp-good"]
    bad = [d for d in result["decisions"] if d["campaign_id"] == "cmp-bad"]
    assert "alpha" in good[0]
    assert "skipped" in bad[0] or "error" in bad[0]
