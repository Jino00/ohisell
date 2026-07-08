# test_naver_estimate_calibrator.py — 듀얼모드 스프린트 Phase 6 estimate_calibrator 단위테스트
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverEntity, NaverLearningState
from app.services.naver_ad import estimate_calibrator

AS_OF = date(2026, 7, 8)


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


def _seed_keyword(db, kw_id, name, bid_amt, daily_clk, days=15, as_of=AS_OF):
    db.add(NaverEntity(
        entity_type="keyword", entity_id=kw_id, campaign_id="cmp1", campaign_type="WEB_SITE",
        name=name, status="on", bid_amt=bid_amt,
    ))
    for i in range(days):
        db.add(NaverAdDaily(
            ad_date=as_of - timedelta(days=i), campaign_id="cmp1", campaign_type="WEB_SITE",
            adgroup_id="grp1", keyword_id=kw_id, imp=100, clk=daily_clk, cost=1000, rank_sum=100,
        ))


def test_candidate_keywords_excludes_below_click_threshold(db):
    _seed_keyword(db, "nkw-1", "키워드1", 100, daily_clk=1)  # 15일 총 15클릭 < LOW_CLICK_THRESHOLD*15면 통과? 실측단위 확인
    db.commit()
    out = estimate_calibrator._candidate_keywords(db, AS_OF - timedelta(days=14), AS_OF)
    # 총 클릭 15 >= LOW_CLICK_THRESHOLD(10) 이므로 포함돼야 함
    assert len(out) == 1
    assert out[0]["keyword_id"] == "nkw-1"


def test_candidate_keywords_excludes_thin_sample(db):
    _seed_keyword(db, "nkw-2", "키워드2", 100, daily_clk=0)  # 15일 합산 클릭 0 < LOW_CLICK_THRESHOLD
    db.commit()
    out = estimate_calibrator._candidate_keywords(db, AS_OF - timedelta(days=14), AS_OF)
    assert out == []


def test_measure_estimate_bias_computes_ratio(db, monkeypatch):
    _seed_keyword(db, "nkw-1", "키워드1", 100, daily_clk=10)  # 일평균 클릭=10
    db.commit()

    monkeypatch.setattr(
        estimate_calibrator.fetcher, "estimate_performance",
        lambda items: [{"keyword": it["keyword"], "bid": it["bid"], "clicks": 5} for it in items],
    )
    result = estimate_calibrator.measure_estimate_bias(db, as_of=AS_OF)
    assert result["sample_n"] == 1
    assert result["ratio"] == 2.0  # 실측10 / 예측5 = 2배(네이버가 과소예측)


def test_measure_estimate_bias_no_candidates(db):
    result = estimate_calibrator.measure_estimate_bias(db, as_of=AS_OF)
    assert result["sample_n"] == 0
    assert result["ratio"] is None


def test_measure_estimate_bias_estimate_call_fails(db, monkeypatch):
    _seed_keyword(db, "nkw-1", "키워드1", 100, daily_clk=10)
    db.commit()

    def _boom(items):
        raise RuntimeError("network down")

    monkeypatch.setattr(estimate_calibrator.fetcher, "estimate_performance", _boom)
    result = estimate_calibrator.measure_estimate_bias(db, as_of=AS_OF)
    assert result["sample_n"] == 0
    assert "estimate 호출 실패" in result["skipped_reason"]


def test_run_daily_writes_learning_state(db, monkeypatch):
    _seed_keyword(db, "nkw-1", "키워드1", 100, daily_clk=10)
    db.commit()
    monkeypatch.setattr(
        estimate_calibrator.fetcher, "estimate_performance",
        lambda items: [{"keyword": it["keyword"], "bid": it["bid"], "clicks": 5} for it in items],
    )
    estimate_calibrator.run_daily(db, as_of=AS_OF)

    row = db.query(NaverLearningState).filter(NaverLearningState.metric == "estimate_bias").first()
    assert row is not None
    assert row.scope == "keyword_type"
    assert row.scope_key == "WEB_SITE"
    assert float(row.current_value) == 2.0
    assert row.sample_n == 1


def test_run_daily_keeps_previous_state_when_no_signal(db):
    db.add(NaverLearningState(
        scope="keyword_type", scope_key="WEB_SITE", metric="estimate_bias",
        current_value=1.5, sample_n=50, confidence=1,
    ))
    db.commit()

    estimate_calibrator.run_daily(db, as_of=AS_OF)  # 후보 없음 → 스킵

    row = db.query(NaverLearningState).filter(NaverLearningState.metric == "estimate_bias").first()
    assert float(row.current_value) == 1.5  # 기존값 보존
    assert row.sample_n == 50
