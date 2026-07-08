# test_naver_forecast_scorer.py — 예측·전문가 스프린트 F1 forecast_scorer(SA) 단위테스트
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverForecastDaily, NaverForecastModel
from app.services.naver_ad import forecast_scorer
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

TODAY = date(2026, 7, 8)
YESTERDAY = TODAY - timedelta(days=1)
CAMPAIGN = "cmp-1"


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


def _forecast_row(db, target_date, *, pred_clk=100, pred_cost=1000, pred_conv_amt=0, scope_key=CAMPAIGN):
    row = NaverForecastDaily(
        target_date=target_date, grain="campaign", scope_key=scope_key,
        pred_clk=pred_clk, pred_cost=pred_cost, pred_conv_amt=pred_conv_amt,
    )
    db.add(row)
    return row


def _actual(db, ad_date, *, clk, cost, conv_amt=0, campaign_id=CAMPAIGN):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type="WEB_SITE",
        adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="",
        imp=clk * 10, clk=clk, cost=cost, rank_sum=0,
        conv_direct_cnt=0, conv_indirect_cnt=1 if conv_amt else 0,
        conv_direct_amt=0, conv_indirect_amt=conv_amt,
    ))


def test_scores_pending_forecast_when_actual_arrives(db):
    _forecast_row(db, YESTERDAY, pred_clk=100, pred_cost=1000)
    _actual(db, YESTERDAY, clk=100, cost=1000)
    db.commit()

    result = forecast_scorer.score_target_date(db, target_date=YESTERDAY, today=TODAY)

    assert result["scored"] == 1
    assert result["unscored_pending"] == 0
    row = db.query(NaverForecastDaily).filter(NaverForecastDaily.target_date == YESTERDAY).first()
    assert row.actual_clk == 100
    assert row.mape_clk == Decimal("0.0000")
    assert row.scored_at is not None


def test_leaves_unscored_when_actual_missing(db):
    """실측이 아직 도착 안 했으면 채점을 미루고(재시도 대상), 강제로 0으로 채우지 않는다."""
    _forecast_row(db, YESTERDAY, pred_clk=100, pred_cost=1000)
    db.commit()

    result = forecast_scorer.score_target_date(db, target_date=YESTERDAY, today=TODAY)

    assert result["scored"] == 0
    assert result["unscored_pending"] == 1
    row = db.query(NaverForecastDaily).filter(NaverForecastDaily.target_date == YESTERDAY).first()
    assert row.actual_clk is None
    assert row.scored_at is None


def test_mape_perfect_when_actual_and_pred_both_zero(db):
    _forecast_row(db, YESTERDAY, pred_clk=0, pred_cost=0)
    _actual(db, YESTERDAY, clk=0, cost=0)
    db.commit()

    forecast_scorer.score_target_date(db, target_date=YESTERDAY, today=TODAY)

    row = db.query(NaverForecastDaily).first()
    assert row.mape_clk == Decimal("0")
    assert row.mape_cost == Decimal("0")


def test_mape_capped_when_actual_zero_but_predicted_nonzero(db):
    _forecast_row(db, YESTERDAY, pred_clk=50, pred_cost=500)
    _actual(db, YESTERDAY, clk=0, cost=0)
    db.commit()

    forecast_scorer.score_target_date(db, target_date=YESTERDAY, today=TODAY)

    row = db.query(NaverForecastDaily).first()
    assert row.mape_clk == Decimal("1")  # 0으로 나눌 수 없어 상한(100%)으로 캡


def test_demotes_model_after_consistently_bad_mape(db):
    """최소표본(3건) 이상 채점되고 롤업 MAPE가 임계값(0.5) 초과면 자동 강등."""
    model = NaverForecastModel(grain="campaign", scope_key=CAMPAIGN, gate_status="active", sample_days=14)
    db.add(model)
    # 이미 채점된 나쁜 이력 3건(직전 3일) — mape 계산 없이 직접 스코어 필드 채워 롤업 대상으로 준비
    for i in range(2, 5):
        row = _forecast_row(db, YESTERDAY - timedelta(days=i - 1), pred_clk=100, pred_cost=1000)
        row.actual_clk, row.actual_cost = 10, 100  # 실측이 훨씬 작음 → mape 큼
        row.mape_clk = Decimal("9.0000")
        row.mape_cost = Decimal("9.0000")
        row.mape_conv_amt = Decimal("0")
        row.scored_at = datetime.combine(YESTERDAY - timedelta(days=i - 1), datetime.min.time())
    db.commit()

    # 오늘 채점 대상(어제) — 이것도 예측이 크게 빗나감
    _forecast_row(db, YESTERDAY, pred_clk=100, pred_cost=1000)
    _actual(db, YESTERDAY, clk=10, cost=100)
    db.commit()

    result = forecast_scorer.score_target_date(db, target_date=YESTERDAY, today=TODAY)

    assert CAMPAIGN in result["demoted"]
    model_row = db.query(NaverForecastModel).filter(NaverForecastModel.scope_key == CAMPAIGN).first()
    assert model_row.gate_status == "demoted"
    assert model_row.demoted_until == TODAY + timedelta(days=forecast_scorer.DEMOTE_COOLDOWN_DAYS)
    assert model_row.recent_mape > forecast_scorer.DEMOTE_MAPE_THRESHOLD


def test_no_demotion_when_sample_below_minimum(db):
    """채점 표본이 최소치(3건) 미만이면 성적이 나빠도 아직 강등하지 않는다(성급한 판단 방지)."""
    model = NaverForecastModel(grain="campaign", scope_key=CAMPAIGN, gate_status="active", sample_days=14)
    db.add(model)
    _forecast_row(db, YESTERDAY, pred_clk=100, pred_cost=1000)
    _actual(db, YESTERDAY, clk=10, cost=100)
    db.commit()

    result = forecast_scorer.score_target_date(db, target_date=YESTERDAY, today=TODAY)

    assert result["demoted"] == []
    model_row = db.query(NaverForecastModel).filter(NaverForecastModel.scope_key == CAMPAIGN).first()
    assert model_row.gate_status == "active"


def test_run_daily_scores_yesterday(db):
    _forecast_row(db, YESTERDAY, pred_clk=100, pred_cost=1000)
    _actual(db, YESTERDAY, clk=100, cost=1000)
    db.commit()

    result = forecast_scorer.run_daily(db, today=TODAY)

    assert result["target_date"] == YESTERDAY.isoformat()
    assert result["scored"] == 1
