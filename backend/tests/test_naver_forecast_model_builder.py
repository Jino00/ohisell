# test_naver_forecast_model_builder.py — 예측·전문가 스프린트 F1 forecast_model_builder(SA) 단위테스트
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverForecastDaily, NaverForecastModel
from app.services.naver_ad import forecast_model_builder
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

TODAY = date(2026, 7, 8)
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


def _sentinel(db, ad_date, *, clk=100, cost=1000, conv_amt=0):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=CAMPAIGN, campaign_type="WEB_SITE",
        adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="",
        imp=clk * 10, clk=clk, cost=cost, rank_sum=clk * 3,
        conv_direct_cnt=0, conv_indirect_cnt=1 if conv_amt else 0,
        conv_direct_amt=0, conv_indirect_amt=conv_amt,
    ))


def _seed_flat_history(db, *, days=28, clk=100, cost=1000):
    for i in range(1, days + 1):
        _sentinel(db, TODAY - timedelta(days=i), clk=clk, cost=cost)
    db.commit()


def test_skips_forecast_when_gate_fallback(db):
    """활동일이 부족하면(게이트 fallback) 예측 행을 만들지 않는다 — 정직 경계."""
    result = forecast_model_builder.build_and_forecast(db, "campaign", CAMPAIGN, today=TODAY)

    assert result["gate_status"] == "fallback"
    assert result["forecast_created"] is False
    assert db.query(NaverForecastDaily).count() == 0
    model_row = db.query(NaverForecastModel).filter(NaverForecastModel.scope_key == CAMPAIGN).first()
    assert model_row is not None  # 게이트 판정 자체는 감사를 위해 항상 기록
    assert model_row.gate_status == "fallback"


def test_flat_series_forecasts_exact_level(db):
    """완전 정상(요일 편차 없는) 28일 이력이면 예측이 그 수준을 정확히 재현해야 한다."""
    _seed_flat_history(db, clk=100, cost=1000)

    result = forecast_model_builder.build_and_forecast(db, "campaign", CAMPAIGN, today=TODAY)

    assert result["gate_status"] == "active"
    assert result["forecast_created"] is True
    assert result["pred_clk"] == 100
    assert result["pred_cost"] == 1000
    assert result["pred_conv_amt"] == 0

    row = db.query(NaverForecastDaily).filter(
        NaverForecastDaily.target_date == TODAY, NaverForecastDaily.scope_key == CAMPAIGN,
    ).first()
    assert row is not None
    assert row.pred_clk == 100
    assert float(row.pred_cpc) == 10.0  # 1000/100


def test_trend_weights_recent_days_more_than_older(db):
    """지수감쇠 추세 — 최근일일수록 가중치가 커서, 예측이 단순평균보다 최근값에 더 가까워야 한다.

    백테스트 실증(모듈 docstring 참조)으로 요일 계절성은 제거하고 짧은 창(3일) 지수감쇠로
    전환했다 — 이 테스트는 그 감쇠 가중치가 실제로 최근일 우선순위를 반영하는지 검증한다.
    """
    for i in range(4, 15):  # 오래된 이력(4~14일 전)은 낮은 수준
        _sentinel(db, TODAY - timedelta(days=i), clk=50, cost=500)
    _sentinel(db, TODAY - timedelta(days=3), clk=50, cost=500)   # TREND_WINDOW_DAYS 창의 가장 오래된 날
    _sentinel(db, TODAY - timedelta(days=2), clk=100, cost=1000)
    _sentinel(db, TODAY - timedelta(days=1), clk=200, cost=2000)  # 가장 최근일 — 가장 큰 가중치
    db.commit()

    result = forecast_model_builder.build_and_forecast(db, "campaign", CAMPAIGN, today=TODAY)

    assert result["forecast_created"] is True
    simple_avg = (50 + 100 + 200) / 3  # 116.67 — 감쇠 없는 단순평균이었다면 이 값
    assert result["pred_clk"] > simple_avg  # 최근일(200) 가중치가 더 커서 단순평균보다 높아야 함
    assert result["pred_clk"] == 142  # 지수감쇠(decay=0.6) 정확 계산값(모듈 상수 기준)


def test_rerun_same_day_replaces_idempotently(db):
    _seed_flat_history(db, clk=100, cost=1000)

    forecast_model_builder.build_and_forecast(db, "campaign", CAMPAIGN, today=TODAY)
    forecast_model_builder.build_and_forecast(db, "campaign", CAMPAIGN, today=TODAY)

    assert db.query(NaverForecastDaily).filter(
        NaverForecastDaily.target_date == TODAY, NaverForecastDaily.scope_key == CAMPAIGN,
    ).count() == 1
    assert db.query(NaverForecastModel).filter(NaverForecastModel.scope_key == CAMPAIGN).count() == 1


def test_run_daily_iterates_multiple_scopes(db):
    for cid in ("cmp-1", "cmp-2"):
        for i in range(1, 15):
            db.add(NaverAdDaily(
                ad_date=TODAY - timedelta(days=i), campaign_id=cid, campaign_type="WEB_SITE",
                adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="",
                imp=500, clk=50, cost=500, rank_sum=0,
            ))
    db.commit()

    scopes = [("campaign", "cmp-1"), ("campaign", "cmp-2"), ("campaign", "cmp-missing")]
    result = forecast_model_builder.run_daily(db, scopes, today=TODAY)

    assert result["campaigns"] == 3
    assert result["forecasted"] == 2  # cmp-missing은 이력 없음 → fallback


def test_build_and_forecast_adgroup_grain_aggregates_keyword_rows(db):
    """F2a: adgroup grain은 forecast_source가 키워드별 행을 합산한 시계열로 예측해야 한다."""
    for i in range(1, 15):
        db.add(NaverAdDaily(
            ad_date=TODAY - timedelta(days=i), campaign_id=CAMPAIGN, campaign_type="WEB_SITE",
            adgroup_id="adg-1", keyword_id="nkw-1",
            imp=300, clk=30, cost=300, rank_sum=0,
        ))
        db.add(NaverAdDaily(
            ad_date=TODAY - timedelta(days=i), campaign_id=CAMPAIGN, campaign_type="WEB_SITE",
            adgroup_id="adg-1", keyword_id="nkw-2",
            imp=700, clk=70, cost=700, rank_sum=0,
        ))
    db.commit()

    result = forecast_model_builder.build_and_forecast(db, "adgroup", "adg-1", today=TODAY)

    assert result["gate_status"] == "active"
    assert result["forecast_created"] is True
    assert result["pred_clk"] == 100  # 30+70 합산 수준
    assert result["pred_cost"] == 1000

    row = db.query(NaverForecastDaily).filter(
        NaverForecastDaily.target_date == TODAY, NaverForecastDaily.grain == "adgroup",
        NaverForecastDaily.scope_key == "adg-1",
    ).first()
    assert row is not None


def test_build_and_forecast_keyword_grain_with_no_history_is_fallback(db):
    """F2a: keyword grain 이력이 아직 없으면(정직 경계) fallback으로 남는다."""
    result = forecast_model_builder.build_and_forecast(db, "keyword", "nkw-new", today=TODAY)

    assert result["gate_status"] == "fallback"
    assert result["forecast_created"] is False
