# test_completeness_curve.py — D-NAO-44 completeness_curve SA 단위 테스트
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverHourlySnapshot
from app.services.naver_ad import completeness_curve
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

TODAY = date(2026, 7, 16)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(completeness_curve, "kst_today", lambda: TODAY)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _sentinel(db, campaign_id, ad_date, cost):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type="WEB_SITE",
        adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="",
        imp=max(cost // 10, 1), clk=max(cost // 500, 1), cost=cost, rank_sum=0,
    ))


def _snapshot(db, campaign_id, ad_date, hour, cost):
    db.add(NaverHourlySnapshot(
        snapshot_at=datetime(ad_date.year, ad_date.month, ad_date.day, hour, 5),
        ad_date=ad_date, snapshot_hour=hour, campaign_id=campaign_id,
        campaign_type="WEB_SITE", cost=cost, clk=max(cost // 500, 0), imp=max(cost // 10, 0),
    ))


def test_build_curve_computes_hourly_median_ratio(db):
    d1, d2 = TODAY - timedelta(days=1), TODAY - timedelta(days=2)
    for d in (d1, d2):
        _sentinel(db, "cmp-a", d, 100_000)
        _snapshot(db, "cmp-a", d, 12, 28_000)
    db.commit()

    curve = completeness_curve.build_curve(db, min_daily_cost=50_000)
    assert 12 in curve
    assert curve[12]["completeness"] == Decimal("0.28")
    assert curve[12]["n"] == 2


def test_build_curve_excludes_today(db):
    """오늘(=kst_today())은 아직 확정치가 아니므로 finals에서 제외돼야 함."""
    _sentinel(db, "cmp-a", TODAY, 100_000)
    _snapshot(db, "cmp-a", TODAY, 12, 28_000)
    db.commit()
    curve = completeness_curve.build_curve(db, min_daily_cost=50_000)
    assert curve == {}


def test_build_curve_below_min_daily_cost_excluded(db):
    d1 = TODAY - timedelta(days=1)
    _sentinel(db, "cmp-a", d1, 10_000)  # min_daily_cost=50000 미만
    _snapshot(db, "cmp-a", d1, 12, 3_000)
    db.commit()
    curve = completeness_curve.build_curve(db, min_daily_cost=50_000)
    assert curve == {}


def test_build_curve_sentinel_plus_detail_no_double_count(db):
    """회귀(필수): sentinel+상세행 공존 시 전행 SUM이면 분모가 2배가 되어 비율이 왜곡됨
    — 반드시 sentinel(BACKFILL_SENTINEL_ADGROUP) 행만 확정치로 집계해야 한다."""
    d1 = TODAY - timedelta(days=1)
    _sentinel(db, "cmp-a", d1, 100_000)
    db.add(NaverAdDaily(
        ad_date=d1, campaign_id="cmp-a", campaign_type="WEB_SITE",
        adgroup_id="grp-1", keyword_id="kw-1",
        imp=5000, clk=50, cost=100_000, rank_sum=0,
    ))
    _snapshot(db, "cmp-a", d1, 12, 50_000)
    db.commit()

    curve = completeness_curve.build_curve(db, min_daily_cost=50_000)
    assert curve[12]["completeness"] == Decimal("0.5"), (
        "sentinel(100,000)만 분모여야 함 — 상세행까지 합산되면 200,000이 되어 0.25로 왜곡됨(2배 버그)"
    )


def test_build_curve_campaign_equal_weighting_not_cost_weighted(db):
    """캠페인-일별 비율의 median — 캠페인 규모(비용 크기)와 무관하게 동등 가중이어야 함."""
    d1 = TODAY - timedelta(days=1)
    _sentinel(db, "cmp-big", d1, 1_000_000)
    _snapshot(db, "cmp-big", d1, 12, 100_000)  # ratio 0.10
    _sentinel(db, "cmp-small", d1, 60_000)
    _snapshot(db, "cmp-small", d1, 12, 30_000)  # ratio 0.50
    db.commit()

    curve = completeness_curve.build_curve(db, min_daily_cost=50_000)
    # 합산비였다면 130,000/1,060,000 ≈ 0.1226(큰 캠페인이 지배) — median(0.10, 0.50)=0.30이어야 함
    assert curve[12]["completeness"] == Decimal("0.30")


def test_build_curve_respects_lookback_days(db):
    d_old = TODAY - timedelta(days=20)
    _sentinel(db, "cmp-a", d_old, 100_000)
    _snapshot(db, "cmp-a", d_old, 12, 28_000)
    db.commit()
    curve = completeness_curve.build_curve(db, lookback_days=14, min_daily_cost=50_000)
    assert curve == {}


def test_build_curve_no_data_returns_empty_dict(db):
    assert completeness_curve.build_curve(db) == {}


def test_projection_factor_none_when_hour_missing():
    curve: dict[int, dict] = {}
    assert completeness_curve.projection_factor(curve, 9) is None


def test_projection_factor_none_when_samples_below_min():
    curve = {12: {"completeness": Decimal("0.28"), "n": 3}}
    assert completeness_curve.projection_factor(curve, 12, min_samples=5) is None


def test_projection_factor_none_when_completeness_below_min():
    curve = {8: {"completeness": Decimal("0.05"), "n": 10}}
    assert completeness_curve.projection_factor(curve, 8, min_completeness=Decimal("0.10")) is None


def test_projection_factor_returns_inverse_completeness():
    curve = {18: {"completeness": Decimal("0.648"), "n": 10}}
    factor = completeness_curve.projection_factor(curve, 18)
    assert factor == Decimal(1) / Decimal("0.648")


def test_projection_factor_boundary_min_samples_inclusive():
    curve = {12: {"completeness": Decimal("0.28"), "n": 5}}
    assert completeness_curve.projection_factor(curve, 12, min_samples=5) is not None


def test_projection_factor_boundary_min_completeness_inclusive():
    curve = {9: {"completeness": Decimal("0.10"), "n": 10}}
    assert completeness_curve.projection_factor(curve, 9, min_completeness=Decimal("0.10")) is not None
