# test_naver_trigger_watch.py — 듀얼모드 스프린트 Phase 4 trigger_watch(harness) 단위테스트
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverHourlySnapshot, NaverProposal
from app.services.naver_ad import trigger_watch

AD_DATE = date(2026, 7, 8)


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


def _snap(campaign_id, hour, cost, clk=0, daily_budget=None, ad_date=AD_DATE):
    return NaverHourlySnapshot(
        snapshot_at=datetime.combine(ad_date, datetime.min.time()).replace(hour=hour),
        ad_date=ad_date, snapshot_hour=hour, campaign_id=campaign_id,
        campaign_type="WEB_SITE", cost=cost, clk=clk, imp=0, daily_budget=daily_budget,
    )


def _daily_row(ad_date, campaign_id, cost, clk):
    return NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type="WEB_SITE",
        adgroup_id="grp1", keyword_id="nkw-1", imp=100, clk=clk, cost=cost, rank_sum=3,
    )


# ── find_pacing_anomalies ──
def test_pacing_overpace_detected(db):
    # 10시 정각(=10/24 기대 페이스=41.7%)에 이미 100% 소진 → 배수 2.4배(≥2, overpace)
    db.add(_snap("cmp1", 10, cost=10000, daily_budget=10000))
    db.commit()
    out = trigger_watch.find_pacing_anomalies(db, AD_DATE)
    assert len(out) == 1
    assert out[0]["campaign_id"] == "cmp1"
    assert out[0]["kind"] == "overpace"


def test_pacing_underpace_requires_afternoon(db):
    # 오전 10시엔 underpace 판정 안 함(오탐 방지)
    db.add(_snap("cmp1", 10, cost=100, daily_budget=10000))
    db.commit()
    out = trigger_watch.find_pacing_anomalies(db, AD_DATE)
    assert out == []


def test_pacing_underpace_detected_in_afternoon(db):
    # 18시 정각(=18/24 기대 페이스=75.0%)에 5%만 소진 → underpace
    db.add(_snap("cmp1", 18, cost=500, daily_budget=10000))
    db.commit()
    out = trigger_watch.find_pacing_anomalies(db, AD_DATE)
    assert len(out) == 1
    assert out[0]["kind"] == "underpace"


def test_pacing_normal_not_flagged(db):
    # 12시 정각(=12/24 기대 페이스=50.0%)에 50% 소진 — 정상 범위(배수 1.0)
    db.add(_snap("cmp1", 12, cost=5000, daily_budget=10000))
    db.commit()
    out = trigger_watch.find_pacing_anomalies(db, AD_DATE)
    assert out == []


def test_pacing_excludes_campaign_without_daily_budget(db):
    db.add(_snap("cmp1", 10, cost=9000, daily_budget=None))
    db.commit()
    out = trigger_watch.find_pacing_anomalies(db, AD_DATE)
    assert out == []


# ── find_cpc_spikes ──
def test_cpc_spike_detected(db):
    # 베이스라인: 최근 7일 cost=700/clk=70 → CPC=10원. 이번시간 증분: cost=2000/clk=10 → CPC=200원(20배)
    for i in range(1, 8):
        db.add(_daily_row(AD_DATE - timedelta(days=i), "cmp1", cost=100, clk=10))
    db.add(_snap("cmp1", 10, cost=0, clk=0))  # 직전 시각(증분 기준점)
    db.add(_snap("cmp1", 11, cost=2000, clk=10))  # 최신 시각
    db.commit()
    out = trigger_watch.find_cpc_spikes(db, AD_DATE)
    assert len(out) == 1
    assert out[0]["campaign_id"] == "cmp1"
    assert out[0]["ratio"] >= 2


def test_cpc_spike_excludes_thin_click_sample(db):
    for i in range(1, 8):
        db.add(_daily_row(AD_DATE - timedelta(days=i), "cmp1", cost=100, clk=10))
    db.add(_snap("cmp1", 10, cost=0, clk=0))
    db.add(_snap("cmp1", 11, cost=1000, clk=2))  # 클릭 2 — 최소 클릭수(5) 미달
    db.commit()
    out = trigger_watch.find_cpc_spikes(db, AD_DATE)
    assert out == []


def test_cpc_normal_not_flagged(db):
    for i in range(1, 8):
        db.add(_daily_row(AD_DATE - timedelta(days=i), "cmp1", cost=100, clk=10))
    db.add(_snap("cmp1", 10, cost=0, clk=0))
    db.add(_snap("cmp1", 11, cost=120, clk=10))  # CPC=12원, 베이스라인10원 대비 1.2배 — 정상
    db.commit()
    out = trigger_watch.find_cpc_spikes(db, AD_DATE)
    assert out == []


def test_cpc_first_snapshot_of_day_treated_as_increment_from_zero(db):
    """codex 지적: hourly_pacing.py 관례와 동일하게, 당일 첫 기록은 0에서부터의 증분으로
    간주해야 한다 — 그렇지 않으면 하루의 첫 시간대가 항상 누락된다."""
    for i in range(1, 8):
        db.add(_daily_row(AD_DATE - timedelta(days=i), "cmp1", cost=100, clk=10))
    db.add(_snap("cmp1", 10, cost=2000, clk=10))  # 오늘 첫 스냅샷뿐 — 0에서부터 증분 2000/10
    db.commit()
    out = trigger_watch.find_cpc_spikes(db, AD_DATE)
    assert len(out) == 1
    assert out[0]["campaign_id"] == "cmp1"


def test_cpc_requires_baseline_data(db):
    # 베이스라인(최근 7일) 데이터 없음 — 비교 불가로 스킵
    db.add(_snap("cmp1", 10, cost=0, clk=0))
    db.add(_snap("cmp1", 11, cost=2000, clk=10))
    db.commit()
    out = trigger_watch.find_cpc_spikes(db, AD_DATE)
    assert out == []


# ── run_hourly (통합 + 쿨다운) ──
def test_run_hourly_generates_proposal_and_notifies(db, monkeypatch):
    db.add(_snap("cmp1", 10, cost=10000, daily_budget=10000))
    db.commit()

    sent = {}
    monkeypatch.setattr(
        "app.services.naver_ad.trigger_watch.slack_notifier.notify",
        lambda proposals: sent.setdefault("proposals", proposals) or {
            "sent": True, "reason": None, "slack_ts": None, "proposal_count": len(proposals),
        },
    )
    result = trigger_watch.run_hourly(db, ad_date=AD_DATE)
    assert result["generated"] == 1
    assert result["pacing_anomalies"] == 1
    saved = db.query(NaverProposal).filter(NaverProposal.proposal_type == "trigger_pacing").all()
    assert len(saved) == 1
    assert saved[0].campaign_id == "cmp1"
    assert sent["proposals"][0]["proposal_type"] == "trigger_pacing"


def test_run_hourly_respects_cooldown(db):
    db.add(_snap("cmp1", 10, cost=10000, daily_budget=10000))
    db.commit()

    first = trigger_watch.run_hourly(db, ad_date=AD_DATE)
    assert first["generated"] == 1

    # 같은 시각 재실행 — 쿨다운(5시간) 이내라 재생성되지 않아야 함
    second = trigger_watch.run_hourly(db, ad_date=AD_DATE)
    assert second["generated"] == 0
    assert second["cooled_down"] == 1

    saved = db.query(NaverProposal).filter(NaverProposal.proposal_type == "trigger_pacing").all()
    assert len(saved) == 1


def test_run_hourly_no_anomalies_no_slack_call(db, monkeypatch):
    called = {"count": 0}
    monkeypatch.setattr(
        "app.services.naver_ad.trigger_watch.slack_notifier.notify",
        lambda proposals: called.__setitem__("count", called["count"] + 1) or {"sent": False},
    )
    result = trigger_watch.run_hourly(db, ad_date=AD_DATE)
    assert result["generated"] == 0
    assert called["count"] == 0
