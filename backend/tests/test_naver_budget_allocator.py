# test_naver_budget_allocator.py — 듀얼모드 스프린트 Phase 3 budget_allocator(SA) 단위테스트
from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverForecastDaily, NaverHourlySnapshot
from app.services.naver_ad import budget_allocator

AS_OF = date(2026, 7, 7)


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


def _snap(campaign_id, hour, cost, daily_budget, ad_date=AS_OF, campaign_type="WEB_SITE"):
    return NaverHourlySnapshot(
        snapshot_at=datetime.combine(ad_date, datetime.min.time()).replace(hour=hour),
        ad_date=ad_date, snapshot_hour=hour, campaign_id=campaign_id,
        campaign_type=campaign_type, cost=cost, clk=0, imp=0, daily_budget=daily_budget,
    )


def test_find_exhausted_uses_latest_hour_only(db):
    """같은 캠페인의 여러 시각 스냅샷 중 최신 시각(누적 최대)만 판정에 사용."""
    db.add(_snap("cmp1", 10, cost=5000, daily_budget=10000))
    db.add(_snap("cmp1", 14, cost=10000, daily_budget=10000))  # 최신 — 소진
    db.commit()
    out = budget_allocator.find_budget_exhausted_campaigns(db, AS_OF)
    assert len(out) == 1
    assert out[0]["campaign_id"] == "cmp1"
    assert out[0]["hour"] == 14


def test_excludes_campaign_below_budget(db):
    db.add(_snap("cmp1", 10, cost=5000, daily_budget=10000))
    db.commit()
    out = budget_allocator.find_budget_exhausted_campaigns(db, AS_OF)
    assert out == []


def test_excludes_campaign_without_daily_budget(db):
    db.add(_snap("cmp1", 10, cost=99999, daily_budget=None))
    db.commit()
    out = budget_allocator.find_budget_exhausted_campaigns(db, AS_OF)
    assert out == []


def test_sorted_by_cost_descending(db):
    db.add(_snap("cmp-small", 10, cost=1000, daily_budget=1000))
    db.add(_snap("cmp-big", 10, cost=50000, daily_budget=50000))
    db.commit()
    out = budget_allocator.find_budget_exhausted_campaigns(db, AS_OF)
    assert [c["campaign_id"] for c in out] == ["cmp-big", "cmp-small"]


def test_expansion_signals_require_both_exhaustion_and_growth_candidates(db):
    db.add(_snap("cmp-exhausted-no-growth", 10, cost=10000, daily_budget=10000))
    db.add(_snap("cmp-exhausted-with-growth", 10, cost=20000, daily_budget=20000))
    db.add(_snap("cmp-not-exhausted", 10, cost=100, daily_budget=10000))
    db.commit()

    growth_candidates = [
        {"campaign_id": "cmp-exhausted-with-growth", "gap": 300},
        {"campaign_id": "cmp-exhausted-with-growth", "gap": 200},
        {"campaign_id": "cmp-not-exhausted", "gap": 999},  # 예산 소진 아니므로 신호 아님
    ]
    out = budget_allocator.find_budget_expansion_signals(db, AS_OF, growth_candidates=growth_candidates)
    assert len(out) == 1
    assert out[0]["campaign_id"] == "cmp-exhausted-with-growth"
    assert out[0]["growth_candidate_count"] == 2
    assert out[0]["total_gap"] == 500


def test_expansion_signals_empty_when_no_exhausted_campaigns(db):
    db.commit()
    out = budget_allocator.find_budget_expansion_signals(db, AS_OF, growth_candidates=[{"campaign_id": "x", "gap": 1}])
    assert out == []


def _forecast(campaign_id, pred_cost, target_date=AS_OF):
    return NaverForecastDaily(
        target_date=target_date, grain="campaign", scope_key=campaign_id,
        pred_clk=0, pred_cost=pred_cost, pred_conv_amt=0,
    )


def test_pre_exhaustion_flags_not_yet_exhausted_campaign_with_high_forecast(db):
    """F2b ⓑ: 아직 소진 전이지만 오늘 예측 pred_cost가 daily_budget을 넘으면 사전경보."""
    db.add(_snap("cmp1", 10, cost=3000, daily_budget=10000))  # 아직 소진 아님
    db.add(_forecast("cmp1", pred_cost=12000))  # 오늘 예측은 예산 초과
    db.commit()

    out = budget_allocator.find_pre_exhaustion_signals(db, AS_OF)

    assert len(out) == 1
    assert out[0]["campaign_id"] == "cmp1"
    assert out[0]["pred_cost"] == 12000
    assert out[0]["pred_gap"] == 2000


def test_pre_exhaustion_excludes_already_exhausted_campaign(db):
    """이미 소진된 캠페인은 find_budget_exhausted_campaigns 대상이지 사전경보 대상이 아니다."""
    db.add(_snap("cmp1", 10, cost=10000, daily_budget=10000))
    db.add(_forecast("cmp1", pred_cost=15000))
    db.commit()

    out = budget_allocator.find_pre_exhaustion_signals(db, AS_OF)
    assert out == []


def test_pre_exhaustion_excludes_when_forecast_below_budget(db):
    db.add(_snap("cmp1", 10, cost=3000, daily_budget=10000))
    db.add(_forecast("cmp1", pred_cost=8000))  # 예측도 예산 이하
    db.commit()

    out = budget_allocator.find_pre_exhaustion_signals(db, AS_OF)
    assert out == []


def test_pre_exhaustion_excludes_campaign_without_forecast(db):
    """예측 모델이 아직 없거나(fallback) 미가동인 캠페인은 비교 불가라 제외."""
    db.add(_snap("cmp1", 10, cost=3000, daily_budget=10000))
    db.commit()

    out = budget_allocator.find_pre_exhaustion_signals(db, AS_OF)
    assert out == []


def test_pre_exhaustion_sorted_by_pred_gap_descending(db):
    db.add(_snap("cmp-small-gap", 10, cost=1000, daily_budget=10000))
    db.add(_forecast("cmp-small-gap", pred_cost=10500))
    db.add(_snap("cmp-big-gap", 10, cost=1000, daily_budget=10000))
    db.add(_forecast("cmp-big-gap", pred_cost=20000))
    db.commit()

    out = budget_allocator.find_pre_exhaustion_signals(db, AS_OF)
    assert [c["campaign_id"] for c in out] == ["cmp-big-gap", "cmp-small-gap"]
