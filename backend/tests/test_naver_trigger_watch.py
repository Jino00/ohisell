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


def _snap(campaign_id, hour, cost, clk=0, daily_budget=None, ad_date=AD_DATE, minute=0):
    return NaverHourlySnapshot(
        snapshot_at=datetime.combine(ad_date, datetime.min.time()).replace(hour=hour, minute=minute),
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


def test_pacing_accepts_explicit_today_to_avoid_midnight_race(db):
    """codex review(F2b): 자정 경계에서 kst_today()를 함수 내부에서 재계산하면 호출자가 이미
    확정한 '오늘'과 어긋날 레이스가 있다 — 명시적 today를 받으면 그 값을 신뢰해야 한다."""
    future = AD_DATE + timedelta(days=1)  # 실제 kst_today()와는 다르지만, 호출자가 today로 확정
    db.add(_snap("cmp1", 10, cost=10000, daily_budget=10000, ad_date=future))
    db.commit()
    out = trigger_watch.find_pacing_anomalies(db, future, today=future)
    assert len(out) == 1


# ── find_pacing_anomalies: 예측곡선 (F2b ⓒ, D-NAO-26) ──
def _forecast(campaign_id, pred_cost, target_date=AD_DATE):
    return NaverForecastDaily(
        target_date=target_date, grain="campaign", scope_key=campaign_id,
        pred_clk=0, pred_cost=pred_cost, pred_conv_amt=0,
    )


def test_pacing_uses_forecast_curve_when_available(db):
    """예측(pred_cost)과 hourly_pattern 지출분포가 모두 있으면 선형 대신 곡선 기대치를 쓴다.

    codex review(F2b): 곡선도 선형처럼 분 단위로 보간해야 한다(정각에 그 시간 몫을 전부
    반영하면 정밀도가 낮음) — 9시까지 누적비율=0%, 10시까지=25%(1000/4000). 10:30(반쯤
    지남) 스냅샷이면 기대비율=0% + (25%-0%)×0.5=12.5%. pred_cost=10000 → 기대소진=1250원,
    daily_budget=10000이니 expected_pace=12.5%.
    """
    weekday = AD_DATE.weekday()
    db.add(NaverHourlyPatternHistory(weekday=weekday, hour=10, clk_sum=0, cost_sum=1000, sample_days=1))
    db.add(NaverHourlyPatternHistory(weekday=weekday, hour=14, clk_sum=0, cost_sum=3000, sample_days=1))
    db.add(_forecast("cmp1", pred_cost=10000))
    db.add(_snap("cmp1", 10, cost=9000, daily_budget=10000, minute=30))
    db.commit()

    out = trigger_watch.find_pacing_anomalies(db, AD_DATE)
    assert len(out) == 1
    assert out[0]["expected_pace"] == pytest.approx(0.125)  # 곡선 보간 기대치(선형이었다면 0.4375)


def test_pacing_curve_at_start_of_hour_uses_prior_hour_cumulative_only(db):
    """정각(minute=0)이면 이번 시간 몫은 아직 하나도 안 지났으니 이전 시간까지 누적치만 써야 한다.

    hourly_pattern 관측이 14시 셀 하나뿐이면(그 이전 시간은 관측 0) 14:00 정각의 기대비율은
    0이어야 한다 — 보간 없이 그 시간 전체를 반영했다면(구현 전) 기대비율=100%로 계산돼
    실제 10%(1000/10000) 소진이 오히려 underpace(오후·저배수)로 오탐됐을 것.
    """
    weekday = AD_DATE.weekday()
    db.add(NaverHourlyPatternHistory(weekday=weekday, hour=14, clk_sum=0, cost_sum=1000, sample_days=1))
    db.add(_forecast("cmp1", pred_cost=10000))
    db.add(_snap("cmp1", 14, cost=1000, daily_budget=10000, minute=0))
    db.commit()

    out = trigger_watch.find_pacing_anomalies(db, AD_DATE)
    assert out == []  # expected_pace=0(보간) → 자정 직후와 동일하게 비교 불가(스킵)


def test_pacing_hourly_pattern_lookup_is_batched_not_n_plus_1(db, monkeypatch):
    """codex review(F2b): 캠페인이 여러 개여도 hourly_pattern 조회는 시간대별로 캐시돼야 한다."""
    weekday = AD_DATE.weekday()
    db.add(NaverHourlyPatternHistory(weekday=weekday, hour=10, clk_sum=0, cost_sum=1000, sample_days=1))
    db.add(NaverHourlyPatternHistory(weekday=weekday, hour=14, clk_sum=0, cost_sum=3000, sample_days=1))
    for cid in ("cmp1", "cmp2", "cmp3"):
        db.add(_forecast(cid, pred_cost=10000))
        db.add(_snap(cid, 10, cost=9000, daily_budget=10000, minute=30))
    db.commit()

    calls = {"count": 0}
    real_fn = trigger_watch.hourly_pattern.expected_cost_fraction

    def _counting(db_, *, weekday, hour):
        calls["count"] += 1
        return real_fn(db_, weekday=weekday, hour=hour)

    monkeypatch.setattr(trigger_watch.hourly_pattern, "expected_cost_fraction", _counting)

    trigger_watch.find_pacing_anomalies(db, AD_DATE)
    # 3개 캠페인이 전부 같은 (weekday, hour=10)이므로 hour 9·10 두 값만 캐시되면 충분(2회) —
    # 캐시 없이 캠페인마다 2회씩(hour, hour-1) 쿼리했다면 6회가 됐을 것.
    assert calls["count"] == 2


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
