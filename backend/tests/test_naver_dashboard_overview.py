# test_naver_dashboard_overview.py — 대시보드 미니 스프린트 T1 단위·라우터 테스트
# 커버: engine_stages 5단계(수집/예측/제안/전문가/학습) ok/stale/none 경계 + optimizer_coverage
#   합산 보존. 계획서 `docs/PLAN_naver-ad-dashboard-mini.md` §1 T1.
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    NaverAdDaily,
    NaverCampaignSettings,
    NaverExpertReview,
    NaverExpertReviewRun,
    NaverForecastModel,
    NaverLearningState,
    NaverProposal,
)
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.dashboard_overview import build
from app.services.naver_ad.proposal_writer import INFORMATIONAL_PROPOSAL_TYPES
from app.services.naver_ad.trigger_watch import PROPOSAL_TYPE_PACING

TODAY = date(2026, 7, 10)


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


def _stage(result: dict, key: str) -> dict:
    for s in result["engine_stages"]:
        if s["key"] == key:
            return s
    raise AssertionError(f"stage {key} not found")


def _ad_daily_row(db, ad_date, campaign_id="cmp1", cost=1000, synced_at=None):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type="WEB_SITE",
        adgroup_id="grp1", keyword_id="nkw-1", imp=10, clk=1, cost=cost, rank_sum=10,
        synced_at=synced_at or datetime.combine(ad_date, datetime.min.time()),
    ))


# ── engine_stages: 공통 구조 ──
def test_returns_five_stages_in_order(db):
    result = build(db, today=TODAY)
    keys = [s["key"] for s in result["engine_stages"]]
    assert keys == ["ingest", "forecast", "proposal", "expert", "learning"]
    for s in result["engine_stages"]:
        assert set(s.keys()) == {"key", "name", "last_evidence_at", "status", "detail"}
        assert s["status"] in ("ok", "stale", "none")


# ── ingest ──
def test_ingest_none_when_no_rows(db):
    result = build(db, today=TODAY)
    assert _stage(result, "ingest")["status"] == "none"


def test_ingest_ok_when_yesterday_loaded(db):
    _ad_daily_row(db, TODAY - timedelta(days=1))
    db.commit()
    result = build(db, today=TODAY)
    stage = _stage(result, "ingest")
    assert stage["status"] == "ok"
    assert stage["last_evidence_at"] is not None
    assert (TODAY - timedelta(days=1)).isoformat() in stage["detail"]


def test_ingest_stale_when_three_days_old(db):
    _ad_daily_row(db, TODAY - timedelta(days=3))
    db.commit()
    result = build(db, today=TODAY)
    assert _stage(result, "ingest")["status"] == "stale"


def test_ingest_last_evidence_at_not_double_converted(db):
    # 원칙22 회귀(codex 2R 지적): synced_at은 ad_daily_ingest.py·campaign_backfill.py
    # 둘 다 kst_now() 명시 대입 — server_default가 아니라 이미 KST(실측, prod 코드 확인).
    # 과거 버그: 여기에 +9h를 또 적용해 크론 07:50 증거가 16:50으로 미래 시각 밀림.
    # 변환 없이 그대로 노출되는지 확인.
    _ad_daily_row(db, TODAY - timedelta(days=1), synced_at=datetime(2026, 7, 10, 7, 50))
    db.commit()
    result = build(db, today=TODAY)
    stage = _stage(result, "ingest")
    assert stage["last_evidence_at"] == "2026-07-10T07:50:00"


# ── forecast ──
def test_forecast_none_when_no_models(db):
    result = build(db, today=TODAY)
    assert _stage(result, "forecast")["status"] == "none"


def test_forecast_ok_when_updated_today(db):
    db.add(NaverForecastModel(
        grain="campaign", scope_key="cmp1", gate_status="active", sample_days=14,
        updated_at=datetime.combine(TODAY, datetime.min.time()),
    ))
    db.add(NaverForecastModel(
        grain="campaign", scope_key="cmp2", gate_status="fallback", sample_days=2,
        updated_at=datetime.combine(TODAY, datetime.min.time()),
    ))
    db.commit()
    result = build(db, today=TODAY)
    stage = _stage(result, "forecast")
    assert stage["status"] == "ok"
    assert "1" in stage["detail"]  # active 1개


def test_forecast_stale_when_updated_before_today(db):
    db.add(NaverForecastModel(
        grain="campaign", scope_key="cmp1", gate_status="active", sample_days=14,
        updated_at=datetime.combine(TODAY - timedelta(days=2), datetime.min.time()),
    ))
    db.commit()
    result = build(db, today=TODAY)
    assert _stage(result, "forecast")["status"] == "stale"


def test_forecast_ok_when_utc_timestamp_crosses_midnight_into_kst_today(db):
    # 원칙22 회귀: updated_at은 server_default(onupdate)=func.now()라 UTC — 크론
    # 07:50 KST(=UTC 전날 22:50) 증거가 stale/none으로 오판되지 않는지 확인.
    db.add(NaverForecastModel(
        grain="campaign", scope_key="cmp1", gate_status="active", sample_days=14,
        updated_at=datetime(2026, 7, 9, 22, 50),
    ))
    db.commit()
    result = build(db, today=TODAY)
    assert _stage(result, "forecast")["status"] == "ok"


# ── proposal (실행형/정보성 분리) ──
def test_proposal_none_when_never_created(db):
    result = build(db, today=TODAY)
    assert _stage(result, "proposal")["status"] == "none"


def test_proposal_ok_splits_executable_and_informational(db):
    informational_type = next(iter(INFORMATIONAL_PROPOSAL_TYPES))
    today_start = datetime.combine(TODAY, datetime.min.time())
    db.add(NaverProposal(
        proposal_type="negative_keyword", target_type="keyword", target_id="nkw-1",
        campaign_id="cmp1", status="pending", created_at=today_start + timedelta(hours=8),
    ))
    db.add(NaverProposal(
        proposal_type="negative_keyword", target_type="keyword", target_id="nkw-2",
        campaign_id="cmp1", status="pending", created_at=today_start + timedelta(hours=8, minutes=1),
    ))
    db.add(NaverProposal(
        proposal_type=informational_type, target_type="campaign", target_id="cmp1",
        campaign_id="cmp1", status="pending", created_at=today_start + timedelta(hours=8, minutes=2),
    ))
    db.commit()
    result = build(db, today=TODAY)
    stage = _stage(result, "proposal")
    assert stage["status"] == "ok"
    assert "3" in stage["detail"]
    assert "2" in stage["detail"]  # 실행형 2건
    assert "1" in stage["detail"]  # 정보성 1건


def test_proposal_stale_when_only_past_exists(db):
    db.add(NaverProposal(
        proposal_type="negative_keyword", target_type="keyword", target_id="nkw-1",
        campaign_id="cmp1", status="pending",
        created_at=datetime.combine(TODAY - timedelta(days=2), datetime.min.time()),
    ))
    db.commit()
    result = build(db, today=TODAY)
    stage = _stage(result, "proposal")
    assert stage["status"] == "stale"


def test_proposal_ok_when_utc_timestamp_crosses_midnight_into_kst_today(db):
    # 원칙22 회귀: created_at은 server_default=func.now()라 UTC — 크론 08:00 KST 부근
    # 증거(=UTC 전날 22:50 근사)가 stale/none으로 오판되지 않는지 확인.
    db.add(NaverProposal(
        proposal_type="negative_keyword", target_type="keyword", target_id="nkw-1",
        campaign_id="cmp1", status="pending", created_at=datetime(2026, 7, 9, 22, 50),
    ))
    db.commit()
    result = build(db, today=TODAY)
    stage = _stage(result, "proposal")
    assert stage["status"] == "ok"
    assert stage["last_evidence_at"] == "2026-07-10T07:50:00"


def test_proposal_ok_for_trigger_watch_type_not_pushed_to_tomorrow(db):
    # 원칙22 회귀(codex 2R 지적, 이번 수정의 핵심 버그): trigger_watch.py가 만드는
    # PACING/CPC 유형은 created_at=kst_now()로 이미 KST 명시 스탬프된다(server_default가
    # 아니다) — 과거 버그: 블랭킷 +9h를 적용하면 저녁 20:00 KST 제안이 다음날 05:00로
    # 밀려 "오늘" 카운트에서 빠지고 status가 stale/none으로 오판됐다.
    db.add(NaverProposal(
        proposal_type=PROPOSAL_TYPE_PACING, target_type="campaign", target_id="cmp1",
        campaign_id="cmp1", status="pending",
        created_at=datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=20),
    ))
    db.commit()
    result = build(db, today=TODAY)
    stage = _stage(result, "proposal")
    assert stage["status"] == "ok"
    assert stage["last_evidence_at"] == f"{TODAY.isoformat()}T20:00:00"
    assert "1" in stage["detail"]  # 정보성 1건


# ── expert (오늘 ok / 오늘 degraded만 / 오늘 없음 3분기) ──
def test_expert_none_when_no_run_today(db):
    db.add(NaverExpertReviewRun(
        as_of=TODAY - timedelta(days=1), model="opus", prompt_version="v1", status="ok",
    ))
    db.commit()
    result = build(db, today=TODAY)
    stage = _stage(result, "expert")
    assert stage["status"] == "none"
    assert (TODAY - timedelta(days=1)).isoformat() in stage["detail"]


def test_expert_ok_when_today_run_ok_with_verdicts(db):
    run = NaverExpertReviewRun(as_of=TODAY, model="opus", prompt_version="v1", status="ok")
    db.add(run)
    db.commit()
    db.add(NaverExpertReview(
        run_id=run.id, as_of=TODAY, proposal_id=None,
        verdict="commentary", reasoning="총평", source="local",
    ))
    db.add(NaverExpertReview(
        run_id=run.id, as_of=TODAY, proposal_id=None,
        verdict="agree", reasoning="근거", source="local",
    ))
    db.commit()
    result = build(db, today=TODAY)
    stage = _stage(result, "expert")
    assert stage["status"] == "ok"
    assert "2" in stage["detail"]


def test_expert_stale_when_today_run_degraded_only(db):
    db.add(NaverExpertReviewRun(as_of=TODAY, model="opus", prompt_version="v1", status="degraded"))
    db.commit()
    result = build(db, today=TODAY)
    assert _stage(result, "expert")["status"] == "stale"


def test_expert_last_evidence_at_normalized_to_kst(db):
    # as_of는 kst_today()로 앱 코드가 직접 채우는 달력일이라 보정 대상이 아니지만(그대로
    # TODAY 사용), created_at은 server_default=func.now()라 UTC — 표시값 정규화 회귀 확인.
    db.add(NaverExpertReviewRun(
        as_of=TODAY, model="opus", prompt_version="v1", status="ok",
        created_at=datetime(2026, 7, 9, 22, 50),
    ))
    db.commit()
    result = build(db, today=TODAY)
    stage = _stage(result, "expert")
    assert stage["status"] == "ok"
    assert stage["last_evidence_at"] == "2026-07-10T07:50:00"


# ── learning ──
def test_learning_none_when_no_state(db):
    result = build(db, today=TODAY)
    assert _stage(result, "learning")["status"] == "none"


def test_learning_ok_when_updated_today(db):
    db.add(NaverLearningState(
        scope="action_type", scope_key="negative_keyword", metric="proposal_accuracy",
        sample_n=5, current_value=0.8, updated_at=datetime.combine(TODAY, datetime.min.time()),
    ))
    db.commit()
    result = build(db, today=TODAY)
    assert _stage(result, "learning")["status"] == "ok"


def test_learning_stale_when_updated_before_today(db):
    db.add(NaverLearningState(
        scope="global", scope_key="conv_delay", metric="conv_delay",
        sample_n=5, current_value=3, updated_at=datetime.combine(TODAY - timedelta(days=5), datetime.min.time()),
    ))
    db.commit()
    result = build(db, today=TODAY)
    assert _stage(result, "learning")["status"] == "stale"


def test_learning_ok_when_utc_timestamp_crosses_midnight_into_kst_today(db):
    # 원칙22 회귀: updated_at은 server_default(onupdate)=func.now()라 UTC — 크론
    # 08:10 KST 부근 증거가 stale/none으로 오판되지 않는지 확인.
    db.add(NaverLearningState(
        scope="global", scope_key="conv_delay", metric="conv_delay",
        sample_n=5, current_value=3, updated_at=datetime(2026, 7, 9, 22, 50),
    ))
    db.commit()
    result = build(db, today=TODAY)
    assert _stage(result, "learning")["status"] == "ok"


# ── optimizer_coverage ──
def test_coverage_sums_to_total_and_buckets_by_optimizer(db):
    d1 = TODAY - timedelta(days=1)
    d2 = TODAY - timedelta(days=2)
    _ad_daily_row(db, d1, campaign_id="cmp_ours", cost=1000)
    _ad_daily_row(db, d2, campaign_id="cmp_ours", cost=500)
    _ad_daily_row(db, d1, campaign_id="cmp_mop", cost=2000)
    _ad_daily_row(db, d1, campaign_id="cmp_unset", cost=300)  # 설정 없음 → none
    db.add(NaverCampaignSettings(campaign_id="cmp_ours", optimizer="ours"))
    db.add(NaverCampaignSettings(campaign_id="cmp_mop", optimizer="mop"))
    db.commit()

    result = build(db, today=TODAY)
    cov = result["optimizer_coverage"]
    assert cov["window_days"] == 7
    assert cov["ours_cost"] == 1500
    assert cov["mop_cost"] == 2000
    assert cov["none_cost"] == 300
    assert cov["total_cost"] == cov["ours_cost"] + cov["mop_cost"] + cov["none_cost"]
    assert cov["total_cost"] == 3800
    assert cov["ours_ratio"] == pytest.approx(1500 / 3800, abs=1e-4)


def test_coverage_excludes_today_and_outside_window(db):
    # 오늘 데이터는 아직 미확정(D-1 기준 창) → 집계 제외
    _ad_daily_row(db, TODAY, campaign_id="cmp_ours", cost=999999)
    # 창 밖(8일 전) → 집계 제외
    _ad_daily_row(db, TODAY - timedelta(days=8), campaign_id="cmp_ours", cost=999999)
    _ad_daily_row(db, TODAY - timedelta(days=1), campaign_id="cmp_ours", cost=100)
    db.add(NaverCampaignSettings(campaign_id="cmp_ours", optimizer="ours"))
    db.commit()

    result = build(db, today=TODAY)
    cov = result["optimizer_coverage"]
    assert cov["total_cost"] == 100


def test_coverage_excludes_backfill_sentinel_rows(db):
    # campaign_backfill이 심는 sentinel(adgroup_id=__backfill__) 행은 실단위 그룹 데이터가
    # 아니라 실단위 P0 행과 겹치는 최근 창에서 이중집계된다 — 커버리지 집계에서 제외돼야 함.
    d1 = TODAY - timedelta(days=1)
    db.add(NaverAdDaily(
        ad_date=d1, campaign_id="cmp_ours", campaign_type="WEB_SITE",
        adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="", imp=0, clk=0, cost=999999, rank_sum=0,
    ))
    _ad_daily_row(db, d1, campaign_id="cmp_ours", cost=100)
    db.add(NaverCampaignSettings(campaign_id="cmp_ours", optimizer="ours"))
    db.commit()

    result = build(db, today=TODAY)
    cov = result["optimizer_coverage"]
    assert cov["total_cost"] == 100
    assert cov["ours_cost"] == 100


def test_coverage_ratio_zero_when_total_zero(db):
    result = build(db, today=TODAY)
    cov = result["optimizer_coverage"]
    assert cov["total_cost"] == 0
    assert cov["ours_ratio"] == 0.0


# ── 라우터 왕복 ──
@pytest.fixture
def client(db):
    def _override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_router_returns_200(client):
    resp = client.get("/api/naver/ad/dashboard-overview")
    assert resp.status_code == 200
    body = resp.json()
    assert "engine_stages" in body
    assert "optimizer_coverage" in body
    assert len(body["engine_stages"]) == 5
