# test_naver_trigger_watch.py — 듀얼모드 스프린트 Phase 4 trigger_watch(harness) 단위테스트
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverForecastDaily, NaverHourlyPatternHistory, NaverHourlySnapshot, NaverProposal
from app.services.naver_ad import trigger_watch
from app.utils.kst import kst_today

AD_DATE = kst_today()  # trigger_watch는 "오늘"만 대상(F2b 1-a④ 당일 가드) — 고정 과거일 쓰면 안 됨


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


def test_pacing_skips_non_today_ad_date(db):
    """F2b 1-a④: 마감 과거일에 돌리면 underpace가 대량 오발생하므로, 오늘이 아니면 아예 스킵."""
    past = AD_DATE - timedelta(days=1)
    db.add(_snap("cmp1", 18, cost=500, daily_budget=10000, ad_date=past))  # 정상이면 underpace 조건
    db.commit()
    out = trigger_watch.find_pacing_anomalies(db, past)
    assert out == []


# ── find_pacing_anomalies: 예측곡선 (F2b ⓒ, D-NAO-26) ──
def _forecast(campaign_id, pred_cost, target_date=AD_DATE):
    return NaverForecastDaily(
        target_date=target_date, grain="campaign", scope_key=campaign_id,
        pred_clk=0, pred_cost=pred_cost, pred_conv_amt=0,
    )


def test_pacing_uses_forecast_curve_when_available(db):
    """예측(pred_cost)과 hourly_pattern 지출분포가 모두 있으면 선형 대신 곡선 기대치를 쓴다.

    weekday(AD_DATE)의 0~10시 누적비율=25%(1000/4000). pred_cost=10000 → 기대소진=2500원,
    daily_budget=10000이니 expected_pace=25%. 실제소진=9000원(90%) → 배수=3.6배(overpace).
    선형모델이었다면 10시=41.7% 기대라 배수=2.16배로도 이미 overpace였겠지만, 이 테스트는
    곡선이 실제로 다른(더 낮은) expected_pace를 냈다는 걸 배수 차이로 간접 검증한다.
    """
    weekday = AD_DATE.weekday()
    db.add(NaverHourlyPatternHistory(weekday=weekday, hour=10, clk_sum=0, cost_sum=1000, sample_days=1))
    db.add(NaverHourlyPatternHistory(weekday=weekday, hour=14, clk_sum=0, cost_sum=3000, sample_days=1))
    db.add(_forecast("cmp1", pred_cost=10000))
    db.add(_snap("cmp1", 10, cost=9000, daily_budget=10000))
    db.commit()

    out = trigger_watch.find_pacing_anomalies(db, AD_DATE)
    assert len(out) == 1
    assert out[0]["expected_pace"] == pytest.approx(0.25)  # 곡선 기대치(선형이었다면 0.4167)


def test_pacing_falls_back_to_linear_without_forecast(db):
    """예측이 없으면(fallback/미가동) hourly_pattern이 있어도 기존 선형모델로 폴백."""
    weekday = AD_DATE.weekday()
    db.add(NaverHourlyPatternHistory(weekday=weekday, hour=10, clk_sum=0, cost_sum=1000, sample_days=1))
    db.add(_snap("cmp1", 10, cost=10000, daily_budget=10000))
    db.commit()

    out = trigger_watch.find_pacing_anomalies(db, AD_DATE)
    assert len(out) == 1
    assert out[0]["expected_pace"] == pytest.approx(10 / 24, abs=1e-3)  # 선형(10시=10/24)


def test_pacing_falls_back_to_linear_without_hourly_pattern(db):
    """예측은 있어도 hourly_pattern 데이터가 없으면(초기 상태) 곡선을 못 만들어 선형 폴백."""
    db.add(_forecast("cmp1", pred_cost=10000))
    db.add(_snap("cmp1", 10, cost=10000, daily_budget=10000))
    db.commit()

    out = trigger_watch.find_pacing_anomalies(db, AD_DATE)
    assert len(out) == 1
    assert out[0]["expected_pace"] == pytest.approx(10 / 24, abs=1e-3)


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
