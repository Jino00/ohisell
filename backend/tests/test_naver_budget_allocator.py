# test_naver_budget_allocator.py — 듀얼모드 스프린트 Phase 3 budget_allocator(SA) 단위테스트
from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverHourlySnapshot
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
