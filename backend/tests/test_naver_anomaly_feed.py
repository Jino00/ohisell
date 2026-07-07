# test_naver_anomaly_feed.py — 듀얼모드 스프린트 Phase 3 anomaly_feed(SA) 단위테스트
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily
from app.services.naver_ad import anomaly_feed

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


def _row(db, ad_date, campaign_id, cost, adgroup_id="grp1", keyword_id="nkw-1"):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type="WEB_SITE",
        adgroup_id=adgroup_id, keyword_id=keyword_id, imp=10, clk=1, cost=cost, rank_sum=3,
    ))


# ── freshness_partial_load ──
def test_freshness_no_baseline_returns_unjudgeable(db):
    _row(db, AS_OF, "cmp1", 1000)  # as_of만 있고 baseline 창([as_of-7, as_of-1])엔 데이터 없음
    db.commit()
    out = anomaly_feed.freshness_partial_load(db, AS_OF)
    assert out["partial"] is False
    assert out["baseline_avg"] is None


def test_freshness_normal_ratio_not_partial(db):
    for i in range(7):
        for kw in range(10):
            _row(db, AS_OF - timedelta(days=i + 1), "cmp1", 100, keyword_id=f"nkw-{kw}")
    for kw in range(10):
        _row(db, AS_OF, "cmp1", 100, keyword_id=f"nkw-{kw}")
    db.commit()
    out = anomaly_feed.freshness_partial_load(db, AS_OF)
    assert out["partial"] is False
    assert out["as_of_count"] == 10
    assert out["baseline_avg"] == 10.0


def test_freshness_partial_load_detected(db):
    for i in range(7):
        for kw in range(10):
            _row(db, AS_OF - timedelta(days=i + 1), "cmp1", 100, keyword_id=f"nkw-{kw}")
    # 오늘은 절반도 안 쌓임(3/10)
    for kw in range(3):
        _row(db, AS_OF, "cmp1", 100, keyword_id=f"nkw-{kw}")
    db.commit()
    out = anomaly_feed.freshness_partial_load(db, AS_OF)
    assert out["partial"] is True
    assert out["as_of_count"] == 3


# ── spend_anomalies ──
def test_spend_spike_detected(db):
    _row(db, AS_OF - timedelta(days=1), "cmp1", 10000)
    _row(db, AS_OF, "cmp1", 50000)  # 5배
    db.commit()
    out = anomaly_feed.spend_anomalies(db, AS_OF)
    assert len(out) == 1
    assert out[0]["campaign_id"] == "cmp1"
    assert out[0]["kind"] == "spike"


def test_spend_drop_detected(db):
    _row(db, AS_OF - timedelta(days=1), "cmp1", 10000)
    _row(db, AS_OF, "cmp1", 1000)  # 1/10
    db.commit()
    out = anomaly_feed.spend_anomalies(db, AS_OF)
    assert out[0]["kind"] == "drop"


def test_spend_full_drop_to_zero_detected(db):
    """codex 지적: 캠페인이 완전히 중단(오늘 행 자체가 없음)돼도 어제 지출이 있었다면
    잡혀야 한다 — today_cost만 순회하면 today=0인 행 자체가 없어 놓친다."""
    _row(db, AS_OF - timedelta(days=1), "cmp1", 10000)
    # AS_OF에는 cmp1 행 자체가 없음(완전 중단).
    db.commit()
    out = anomaly_feed.spend_anomalies(db, AS_OF)
    assert len(out) == 1
    assert out[0]["campaign_id"] == "cmp1"
    assert out[0]["cost_today"] == 0
    assert out[0]["kind"] == "drop"


def test_spend_anomaly_includes_iso_date_labels_not_today_yesterday(db):
    """codex 지적: run_daily의 as_of는 확정치 어제일 수 있어 "오늘/어제" 라벨이 오해를
    살 수 있다 — 실제 ISO 날짜를 반환해야 한다."""
    _row(db, AS_OF - timedelta(days=1), "cmp1", 10000)
    _row(db, AS_OF, "cmp1", 50000)
    db.commit()
    out = anomaly_feed.spend_anomalies(db, AS_OF)
    assert out[0]["as_of"] == AS_OF.isoformat()
    assert out[0]["prior_date"] == (AS_OF - timedelta(days=1)).isoformat()


def test_spend_no_prior_data_excluded(db):
    """신규 집행 시작(전일 0)은 비율 미정의라 이상으로 취급하지 않는다."""
    _row(db, AS_OF, "cmp1", 50000)
    db.commit()
    out = anomaly_feed.spend_anomalies(db, AS_OF)
    assert out == []


def test_spend_within_normal_range_not_flagged(db):
    _row(db, AS_OF - timedelta(days=1), "cmp1", 10000)
    _row(db, AS_OF, "cmp1", 12000)  # 1.2배 — 정상 변동
    db.commit()
    out = anomaly_feed.spend_anomalies(db, AS_OF)
    assert out == []
