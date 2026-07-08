# test_naver_conversion_maturity.py — 듀얼모드 스프린트 Phase 6 conversion_maturity 단위테스트
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverConversionMaturitySnapshot, NaverLearningState
from app.services.naver_ad import conversion_maturity as cm

TODAY = date(2026, 7, 8)


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


def _ad_row(ad_date, direct_amt, indirect_amt):
    return NaverAdDaily(
        ad_date=ad_date, campaign_id="cmp1", campaign_type="WEB_SITE",
        adgroup_id="grp1", keyword_id="nkw-1", imp=10, clk=5, cost=1000,
        conv_direct_amt=direct_amt, conv_indirect_amt=indirect_amt,
    )


# ── take_daily_snapshot ──
def test_take_daily_snapshot_records_today_even_if_zero(db):
    db.add(_ad_row(TODAY, 0, 0))
    db.commit()
    result = cm.take_daily_snapshot(db, today=TODAY)
    row = db.query(NaverConversionMaturitySnapshot).filter(
        NaverConversionMaturitySnapshot.ad_date == TODAY, NaverConversionMaturitySnapshot.days_since == 0,
    ).first()
    assert row is not None
    assert row.total_amt == 0
    assert result["rows_upserted"] >= 1


def test_take_daily_snapshot_skips_dates_with_no_data_at_all(db):
    # naver_ad_daily에 해당 날짜 행 자체가 없음(아직 미수집) — 스킵돼야 함
    result = cm.take_daily_snapshot(db, today=TODAY)
    rows = db.query(NaverConversionMaturitySnapshot).all()
    # days_since=0(오늘)만 예외적으로 기록 시도하지만 그마저 행이 없으므로 전부 스킵
    assert len(rows) == 0
    assert result["rows_upserted"] == 0


def test_take_daily_snapshot_is_idempotent_same_day_rerun(db):
    db.add(_ad_row(TODAY, 1000, 500))
    db.commit()
    cm.take_daily_snapshot(db, today=TODAY)
    cm.take_daily_snapshot(db, today=TODAY)  # 재실행
    rows = db.query(NaverConversionMaturitySnapshot).filter(
        NaverConversionMaturitySnapshot.ad_date == TODAY,
    ).all()
    assert len(rows) == 1  # 중복 생성 없음(upsert)
    assert rows[0].total_amt == 1500


def test_take_daily_snapshot_captures_multiple_days_since_for_same_cohort(db):
    """같은 ad_date를 여러 날에 걸쳐 관측하면 서로 다른 days_since로 누적된다(곡선 재료)."""
    ad_date = TODAY - timedelta(days=5)
    db.add(_ad_row(ad_date, 1000, 200))
    db.commit()
    cm.take_daily_snapshot(db, today=ad_date + timedelta(days=5))  # days_since=5
    cm.take_daily_snapshot(db, today=ad_date + timedelta(days=10))  # days_since=10 (다음날 실행 시뮬)
    rows = db.query(NaverConversionMaturitySnapshot).filter(
        NaverConversionMaturitySnapshot.ad_date == ad_date,
    ).order_by(NaverConversionMaturitySnapshot.days_since).all()
    assert [r.days_since for r in rows] == [5, 10]


# ── compute_curve ──
def test_compute_curve_requires_minimum_cohorts(db):
    # 성숙 코호트(days_since=MATURITY_DAYS) 1개뿐 — MIN_COHORTS_FOR_CURVE(3) 미달
    ad_date = TODAY - timedelta(days=cm.MATURITY_DAYS)
    db.add(NaverConversionMaturitySnapshot(
        ad_date=ad_date, days_since=cm.MATURITY_DAYS, direct_amt=800, indirect_amt=200, total_amt=1000,
    ))
    db.commit()
    result = cm.compute_curve(db)
    assert result["curve"] == {}
    assert result["cohort_n"] == 1


def test_compute_curve_averages_ratio_across_mature_cohorts(db):
    for i in range(3):
        ad_date = TODAY - timedelta(days=cm.MATURITY_DAYS + i)  # 서로 다른 코호트 날짜
        db.add(NaverConversionMaturitySnapshot(
            ad_date=ad_date, days_since=0, direct_amt=400, indirect_amt=0, total_amt=400,
        ))
        db.add(NaverConversionMaturitySnapshot(
            ad_date=ad_date, days_since=cm.MATURITY_DAYS, direct_amt=800, indirect_amt=200, total_amt=1000,
        ))
    db.commit()
    result = cm.compute_curve(db)
    assert result["cohort_n"] == 3
    assert result["curve"][0] == 0.4  # 400/1000 — 모든 코호트 동일 비율
    assert result["curve"][cm.MATURITY_DAYS] == 1.0


def test_compute_curve_excludes_cohort_with_zero_mature_amount(db):
    for i in range(3):
        ad_date = TODAY - timedelta(days=cm.MATURITY_DAYS + i)
        mature_amt = 0 if i == 0 else 1000  # 첫 코호트만 성숙시점 매출 0(신규저볼륨) — 제외돼야 함
        db.add(NaverConversionMaturitySnapshot(
            ad_date=ad_date, days_since=0, direct_amt=400, indirect_amt=0, total_amt=400,
        ))
        db.add(NaverConversionMaturitySnapshot(
            ad_date=ad_date, days_since=cm.MATURITY_DAYS, direct_amt=mature_amt, indirect_amt=0, total_amt=mature_amt,
        ))
    db.commit()
    result = cm.compute_curve(db)
    assert result["curve"][0] == 0.4  # 유효한 2개 코호트만으로 계산(400/1000 동일)


# ── run_daily ──
def test_run_daily_keeps_previous_state_when_insufficient_cohorts(db):
    db.add(NaverLearningState(scope="global", scope_key="day_0", metric="conv_delay", current_value=0.3, sample_n=10, confidence=1))
    db.commit()
    cm.run_daily(db, today=TODAY)
    row = db.query(NaverLearningState).filter(NaverLearningState.scope_key == "day_0").first()
    assert float(row.current_value) == 0.3  # 기존값 보존


def test_run_daily_writes_curve_when_sufficient_cohorts(db):
    for i in range(3):
        ad_date = TODAY - timedelta(days=cm.MATURITY_DAYS + i)
        db.add(NaverConversionMaturitySnapshot(
            ad_date=ad_date, days_since=0, direct_amt=400, indirect_amt=0, total_amt=400,
        ))
        db.add(NaverConversionMaturitySnapshot(
            ad_date=ad_date, days_since=cm.MATURITY_DAYS, direct_amt=800, indirect_amt=200, total_amt=1000,
        ))
    db.commit()
    cm.run_daily(db, today=TODAY)
    row = db.query(NaverLearningState).filter(
        NaverLearningState.scope_key == "day_0", NaverLearningState.metric == "conv_delay",
    ).first()
    assert row is not None
    assert float(row.current_value) == 0.4
