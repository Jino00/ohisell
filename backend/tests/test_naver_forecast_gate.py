# test_naver_forecast_gate.py — 예측·전문가 스프린트 F1 forecast_gate(SA) 단위테스트
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverForecastModel
from app.services.naver_ad import forecast_gate
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


def _sentinel(db, ad_date, cost):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=CAMPAIGN, campaign_type="WEB_SITE",
        adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="",
        imp=cost, clk=max(cost // 100, 0), cost=cost, rank_sum=0,
        conv_direct_cnt=0, conv_indirect_cnt=0, conv_direct_amt=0, conv_indirect_amt=0,
    ))


def test_active_when_recent_activity_high(db):
    for i in range(1, 15):  # 14/14일 활동 → 100% ≥ 80%
        _sentinel(db, TODAY - timedelta(days=i), cost=1000)
    db.commit()

    result = forecast_gate.evaluate(db, grain="campaign", scope_key=CAMPAIGN, today=TODAY)
    assert result["gate_status"] == "active"
    assert result["active_days"] == 14


def test_fallback_when_recent_activity_low(db):
    for i in range(1, 6):  # 5/14일만 활동(~36%) < 80%
        _sentinel(db, TODAY - timedelta(days=i), cost=1000)
    db.commit()

    result = forecast_gate.evaluate(db, grain="campaign", scope_key=CAMPAIGN, today=TODAY)
    assert result["gate_status"] == "fallback"
    assert result["active_days"] == 5


def test_fallback_when_no_history(db):
    result = forecast_gate.evaluate(db, grain="campaign", scope_key=CAMPAIGN, today=TODAY)
    assert result["gate_status"] == "fallback"
    assert result["active_days"] == 0


def test_demoted_cooldown_not_overridden_by_high_activity(db):
    """scorer가 강등한 모델은 쿨다운이 남아있는 한 활동일이 충분해도 재평가로 되돌아오지 않는다."""
    for i in range(1, 15):
        _sentinel(db, TODAY - timedelta(days=i), cost=1000)
    db.add(NaverForecastModel(
        grain="campaign", scope_key=CAMPAIGN, gate_status="demoted",
        demoted_until=TODAY + timedelta(days=2), sample_days=14,
    ))
    db.commit()

    result = forecast_gate.evaluate(db, grain="campaign", scope_key=CAMPAIGN, today=TODAY)
    assert result["gate_status"] == "demoted"


def test_gate_reevaluates_after_cooldown_expires(db):
    """demoted_until이 지난 과거 날짜면 쿨다운이 끝난 것 — 활동일 기반으로 재평가한다."""
    for i in range(1, 15):
        _sentinel(db, TODAY - timedelta(days=i), cost=1000)
    db.add(NaverForecastModel(
        grain="campaign", scope_key=CAMPAIGN, gate_status="demoted",
        demoted_until=TODAY - timedelta(days=1), sample_days=14,
    ))
    db.commit()

    result = forecast_gate.evaluate(db, grain="campaign", scope_key=CAMPAIGN, today=TODAY)
    assert result["gate_status"] == "active"


def test_rejects_non_campaign_grain(db):
    with pytest.raises(ValueError):
        forecast_gate.evaluate(db, grain="keyword", scope_key="nkw-1", today=TODAY)
