# test_naver_hourly_pattern.py — 듀얼모드 스프린트 Phase 6 hourly_pattern 단위테스트
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverHourlyPatternHistory, NaverHourlySnapshot, NaverLearningState
from app.services.naver_ad import hourly_pattern

TODAY = date(2026, 7, 8)  # 수요일(weekday=2)
YESTERDAY = TODAY - timedelta(days=1)  # 화요일(weekday=1)


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


def _snap(campaign_id, hour, cost, clk, ad_date=YESTERDAY):
    return NaverHourlySnapshot(
        snapshot_at=datetime.combine(ad_date, datetime.min.time()).replace(hour=hour),
        ad_date=ad_date, snapshot_hour=hour, campaign_id=campaign_id,
        campaign_type="WEB_SITE", cost=cost, clk=clk, imp=0, daily_budget=None,
    )


# ── fold_yesterday ──
def test_fold_yesterday_accumulates_into_weekday_hour_cells(db):
    db.add(_snap("cmp1", 10, cost=1000, clk=10))
    db.add(_snap("cmp1", 14, cost=3000, clk=30))  # 누적치 — 14시 증분=2000/20
    db.commit()

    result = hourly_pattern.fold_yesterday(db, today=TODAY)
    assert result["weekday"] == 1  # 화요일
    assert result["hours_folded"] == 2

    row10 = db.query(NaverHourlyPatternHistory).filter(
        NaverHourlyPatternHistory.weekday == 1, NaverHourlyPatternHistory.hour == 10,
    ).first()
    assert row10.clk_sum == 10
    assert row10.sample_days == 1

    row14 = db.query(NaverHourlyPatternHistory).filter(
        NaverHourlyPatternHistory.weekday == 1, NaverHourlyPatternHistory.hour == 14,
    ).first()
    assert row14.clk_sum == 20  # 순증분(30-10)
    assert row14.cost_sum == 2000


def test_fold_yesterday_idempotent_on_rerun(db):
    db.add(_snap("cmp1", 10, cost=1000, clk=10))
    db.commit()
    hourly_pattern.fold_yesterday(db, today=TODAY)
    result2 = hourly_pattern.fold_yesterday(db, today=TODAY)
    assert result2["hours_folded"] == 0
    assert result2["already_folded"] is True

    row = db.query(NaverHourlyPatternHistory).filter(
        NaverHourlyPatternHistory.weekday == 1, NaverHourlyPatternHistory.hour == 10,
    ).first()
    assert row.sample_days == 1  # 재실행해도 중복 누적 없음


def test_fold_yesterday_accumulates_across_multiple_weeks(db):
    # 3주 전 화요일 + 2주 전 화요일 + 어제(화요일) — 모두 weekday=1로 같은 셀에 누적
    for weeks_ago in [3, 2, 0]:
        d = YESTERDAY - timedelta(weeks=weeks_ago)
        db.add(_snap("cmp1", 10, cost=1000, clk=10, ad_date=d))
    db.commit()

    for weeks_ago in [3, 2, 0]:
        d = YESTERDAY - timedelta(weeks=weeks_ago)
        hourly_pattern.fold_yesterday(db, today=d + timedelta(days=1))

    row = db.query(NaverHourlyPatternHistory).filter(
        NaverHourlyPatternHistory.weekday == 1, NaverHourlyPatternHistory.hour == 10,
    ).first()
    assert row.sample_days == 3
    assert row.clk_sum == 30


def test_fold_yesterday_no_data_returns_zero(db):
    result = hourly_pattern.fold_yesterday(db, today=TODAY)
    assert result["hours_folded"] == 0


# ── compute_bid_weight_recommendations ──
def test_compute_bid_weight_index_relative_to_day_average(db):
    # 화요일: 10시=클릭10(하루평균의 절반), 14시=클릭30(평균의 1.5배), 평균=20
    db.add(NaverHourlyPatternHistory(weekday=1, hour=10, clk_sum=10, cost_sum=0, sample_days=1))
    db.add(NaverHourlyPatternHistory(weekday=1, hour=14, clk_sum=30, cost_sum=0, sample_days=1))
    db.commit()
    out = hourly_pattern.compute_bid_weight_recommendations(db, weekday=1)
    by_hour = {r["hour"]: r["index"] for r in out}
    assert by_hour[10] == 50.0
    assert by_hour[14] == 150.0


def test_compute_bid_weight_excludes_unobserved_cells(db):
    db.add(NaverHourlyPatternHistory(weekday=1, hour=10, clk_sum=10, cost_sum=0, sample_days=1))
    db.add(NaverHourlyPatternHistory(weekday=1, hour=14, clk_sum=0, cost_sum=0, sample_days=0))  # 관측 안 됨
    db.commit()
    out = hourly_pattern.compute_bid_weight_recommendations(db, weekday=1)
    hours = {r["hour"] for r in out}
    assert 14 not in hours


def test_compute_bid_weight_empty_when_no_history(db):
    assert hourly_pattern.compute_bid_weight_recommendations(db) == []


# ── run_daily ──
def test_run_daily_writes_learning_state_per_cell(db):
    db.add(_snap("cmp1", 10, cost=1000, clk=10))
    db.add(_snap("cmp1", 14, cost=1000, clk=30))
    db.commit()
    result = hourly_pattern.run_daily(db, today=TODAY)
    assert result["cells_updated"] == 2

    row = db.query(NaverLearningState).filter(
        NaverLearningState.scope_key == "1_10", NaverLearningState.metric == "hour_weight",
    ).first()
    assert row is not None
    assert row.sample_n == 1
