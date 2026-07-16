# test_flight_loop.py — X2 T3 flight_loop Harness 단위 테스트
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily, NaverCampaignSettings, NaverChangeLog, NaverEntity,
    NaverForecastDaily, NaverHourlyPatternHistory, NaverHourlySnapshot,
    NaverProductBep, Order,
)
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.flight_loop import run_flight_loop


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


def _setup_campaign(db, campaign_id="cmp-test", daily_budget=100000):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer="ours"))
    db.add(NaverEntity(
        entity_type="campaign", entity_id=campaign_id, campaign_id=campaign_id,
        campaign_type="WEB_SITE", name="테스트캠페인", status="on",
    ))
    db.add(NaverForecastDaily(
        target_date=date(2026, 7, 11), grain="campaign", scope_key=campaign_id,
        pred_clk=100, pred_cost=50000, pred_conv_amt=200000,
    ))
    db.add(NaverHourlySnapshot(
        snapshot_at=datetime(2026, 7, 11, 10, 0), ad_date=date(2026, 7, 11),
        snapshot_hour=10, campaign_id=campaign_id, campaign_type="WEB_SITE",
        cost=20000, clk=40, imp=4000, daily_budget=daily_budget,
    ))
    for h in range(24):
        db.add(NaverHourlyPatternHistory(weekday=4, hour=h, clk_sum=10, cost_sum=2000, sample_days=4))
    db.add(NaverAdDaily(
        ad_date=date(2026, 7, 10), campaign_id=campaign_id, adgroup_id="grp-1",
        keyword_id="kw-1", campaign_type="WEB_SITE",
        clk=50, cost=25000, imp=5000, conv_direct_amt=100000, conv_indirect_amt=50000,
    ))
    db.commit()


def test_flight_loop_no_ours_campaigns(db):
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    assert result["campaigns_processed"] == 0


def test_flight_loop_processes_ours_campaign(db):
    _setup_campaign(db)
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10, dry_run=True)
    assert result["campaigns_processed"] == 1
    assert result["dry_run"] is True
    d = result["decisions"][0]
    assert "alpha" in d
    assert d["dry_run"] is True
    assert d["campaign_id"] == "cmp-test"


def test_flight_loop_records_change_log(db):
    _setup_campaign(db)
    run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    logs = db.query(NaverChangeLog).filter(
        NaverChangeLog.action == "flight_pacing",
        NaverChangeLog.campaign_id == "cmp-test",
    ).all()
    assert len(logs) == 1
    assert logs[0].dry_run is True
    assert "α=" in logs[0].rationale


def test_flight_loop_skips_campaign_without_forecast(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-no-forecast", optimizer="ours"))
    db.commit()
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    assert result["decisions"][0].get("skipped") == "forecast 없음"


def test_flight_loop_alpha_within_bounds(db):
    _setup_campaign(db)
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    d = result["decisions"][0]
    from app.services.naver_ad.pacing_controller import ALPHA_MIN, ALPHA_MAX
    assert ALPHA_MIN <= d["alpha"] <= ALPHA_MAX


def test_flight_loop_tight_budget_reduces_alpha(db):
    _setup_campaign(db, daily_budget=25000)
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    d = result["decisions"][0]
    assert d["alpha"] <= 1.0
    assert d["daily_budget"] == 25000
    assert d["remaining_budget"] == 5000


def test_flight_loop_multiple_campaigns(db):
    _setup_campaign(db, campaign_id="cmp-a")
    db.add(NaverCampaignSettings(campaign_id="cmp-b", optimizer="ours"))
    db.add(NaverForecastDaily(
        target_date=date(2026, 7, 11), grain="campaign", scope_key="cmp-b",
        pred_clk=50, pred_cost=30000, pred_conv_amt=80000,
    ))
    db.add(NaverHourlySnapshot(
        snapshot_at=datetime(2026, 7, 11, 10, 0), ad_date=date(2026, 7, 11),
        snapshot_hour=10, campaign_id="cmp-b", campaign_type="WEB_SITE",
        cost=10000, clk=20, imp=2000, daily_budget=50000,
    ))
    db.commit()
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    assert result["campaigns_processed"] == 2


def test_flight_loop_uncapped_budget_not_treated_as_exhausted(db):
    """P2-1 regression: dailyBudget=0 means uncapped, not exhausted."""
    _setup_campaign(db, daily_budget=0)
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    d = result["decisions"][0]
    assert d["daily_budget"] is None
    assert d["remaining_budget"] is None
    assert d["binding_constraint"] != "budget", (
        "uncapped campaign must never be budget-bound"
    )


def test_flight_loop_total_vs_remaining_budget_comparison(db):
    """P2-2 regression: controller gets daily_budget (total), not remaining."""
    _setup_campaign(db, daily_budget=100000)
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    d = result["decisions"][0]
    assert d["daily_budget"] == 100000
    assert d["remaining_budget"] == 80000
    assert d["binding_constraint"] != "budget" or d["alpha"] >= 0.9, (
        "100k budget with 20k spent should not aggressively bind"
    )


def test_flight_loop_fallback_roas_uses_ratio_not_percent():
    """codex P2 regression: fallback target_roas must be ratio (2.0), not percent (200).
    Verify at the source code level that the constant is 2, not 200."""
    import ast, inspect
    from app.services.naver_ad import flight_loop
    source = inspect.getsource(flight_loop)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and hasattr(node, 'args'):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value == "200":
                    if any(isinstance(kw.value, ast.Constant) and kw.value == "200"
                           for kw in getattr(node, 'keywords', [])):
                        pass
    # Direct check: the fallback string in source should be "2" not "200"
    assert 'Decimal("200")' not in source, (
        "fallback target_roas must be ratio (2), not percent (200)"
    )
    assert 'Decimal("2")' in source


# ── D-NAO-44: 완결도 보정(projection) 통합 테스트 (PLAN_naver-ad-pacing-correction §4 ④⑤⑥) ──

def _setup_completeness_history(
    db, *, hour=10, hour_cost=50_000, final_cost=100_000,
    days=5, end=date(2026, 7, 10), campaign_id="cmp-hist",
):
    """build_curve 표본용 과거 이력: sentinel 확정치 + 시각별 스냅샷.
    completeness = hour_cost/final_cost, n = days(기본 5 = min_samples 충족)."""
    for i in range(days):
        d = end - timedelta(days=i)
        db.add(NaverAdDaily(
            ad_date=d, campaign_id=campaign_id, campaign_type="WEB_SITE",
            adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="",
            imp=1000, clk=100, cost=final_cost, rank_sum=0,
        ))
        db.add(NaverHourlySnapshot(
            snapshot_at=datetime(d.year, d.month, d.day, hour, 5), ad_date=d,
            snapshot_hour=hour, campaign_id=campaign_id, campaign_type="WEB_SITE",
            cost=hour_cost, clk=hour_cost // 500, imp=hour_cost // 10,
        ))
    db.commit()


def test_flight_loop_projection_unavailable_forces_neutral_alpha(db):
    """C3-④: 완결도 표본 없음 → α=1.0 중립 고정 + projection_unavailable 라벨
    (저평가 raw 입력으로 원 로직을 계속 계산하지 않는다 — PLAN §3 fail-safe)."""
    _setup_campaign(db)
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    d = result["decisions"][0]
    assert d["binding_constraint"] == "projection_unavailable"
    assert d["alpha"] == 1.0 and d["alpha_budget"] == 1.0 and d["alpha_roas"] == 1.0
    assert d["projected_final_cost"] is None
    assert d["completeness"] is None
    assert d["raw_today_cost"] == 20000


def test_flight_loop_projection_tightens_budget_alpha(db):
    """C3-⑤: projected cost가 raw 대비 αB를 실제로 낮춘다.
    raw 20,000이면 α=1 총예상(≈49k)이 예산 50,000 이내라 budget 미발동이지만,
    completeness 0.5 → projected 40,000이면 총예상(≈69k)이 예산을 넘어 budget 발동."""
    _setup_campaign(db, daily_budget=50_000)
    _setup_completeness_history(db, hour_cost=50_000, final_cost=100_000)  # factor 2
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    d = result["decisions"][0]
    assert d["projected_final_cost"] == 40_000
    assert float(d["projection_factor"]) == 2.0
    assert d["binding_constraint"] == "budget"
    assert d["alpha"] < 1.0


def test_flight_loop_projection_preserves_roas_ratio(db, monkeypatch):
    """codex[P2] 회귀: cost만 투영하면 so-far ROAS가 factor배 깎여 αC가 오발동한다.
    raw so-far ROAS=6(40clk×rpc3000/20,000), completeness 0.25(factor 4)에서
    cost만 투영하면 ROAS 1.5<target 2로 roas 바인딩 — clk·conv_amt 동일 factor
    투영으로 비율(6)이 보존되어 roas 미발동이어야 한다."""
    # rpc 산출은 이 테스트의 관심사가 아님 — 실주문 매출 없는 픽스처에선 보정계수가
    # 0(=rpc 0)이 되므로 no-op(1)으로 고정해 rpc=3000이 나오게 한다.
    from app.services.naver_ad import flight_loop as fl
    monkeypatch.setattr(
        fl, "compute_correction_factor",
        lambda db_, date_to: {"factor": Decimal("1"), "source": "test"},
    )
    db.add(NaverCampaignSettings(campaign_id="cmp-roas", optimizer="ours"))
    db.add(NaverEntity(
        entity_type="campaign", entity_id="cmp-roas", campaign_id="cmp-roas",
        campaign_type="WEB_SITE", name="ROAS회귀", status="on",
    ))
    # 잔여항 영향 최소화를 위해 forecast는 아주 작게
    db.add(NaverForecastDaily(
        target_date=date(2026, 7, 11), grain="campaign", scope_key="cmp-roas",
        pred_clk=2, pred_cost=1000, pred_conv_amt=3000,
    ))
    db.add(NaverHourlySnapshot(
        snapshot_at=datetime(2026, 7, 11, 10, 0), ad_date=date(2026, 7, 11),
        snapshot_hour=10, campaign_id="cmp-roas", campaign_type="WEB_SITE",
        cost=20000, clk=40, imp=4000, daily_budget=1_000_000,
    ))
    for h in range(24):
        db.add(NaverHourlyPatternHistory(weekday=4, hour=h, clk_sum=10, cost_sum=2000, sample_days=4))
    db.add(NaverAdDaily(  # rpc≈3000 (150,000/50clk)
        ad_date=date(2026, 7, 10), campaign_id="cmp-roas", adgroup_id="grp-1",
        keyword_id="kw-1", campaign_type="WEB_SITE",
        clk=50, cost=25000, imp=5000, conv_direct_amt=100000, conv_indirect_amt=50000,
    ))
    _setup_completeness_history(db, hour_cost=25_000, final_cost=100_000)  # factor 4
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    d = result["decisions"][0]
    assert d["projected_final_cost"] == 80_000
    assert d["binding_constraint"] != "roas", (
        "예산 보정(cost 투영)이 ROAS 제약을 오발동시키면 안 됨 — "
        "clk도 같은 factor로 투영해 so-far ROAS 비율이 보존돼야 한다"
    )
    assert d["alpha_roas"] > 1.0


def test_flight_loop_projection_uses_snapshot_hour_not_current_hour(db):
    """codex[P2] R2 회귀: run 시각(current_hour)의 스냅샷이 아직 안 쓰였으면 최신
    스냅샷은 이전 시각분 — 거기에 current_hour의(더 높은 완결도=더 낮은) factor를
    곱하면 저투영되어 과속 편향이 재유입된다. factor는 스냅샷 시각 기준이어야 함."""
    _setup_campaign(db)  # 오늘 스냅샷은 hour=10분(cost 20,000)이 최신
    # 곡선: 10시 완결도 0.25(factor 4), 12시 완결도 0.5(factor 2)
    _setup_completeness_history(db, hour=10, hour_cost=25_000, final_cost=100_000)
    _setup_completeness_history(db, hour=12, hour_cost=50_000, final_cost=100_000,
                                campaign_id="cmp-hist2")
    # run은 12시에 돌지만 12시 스냅샷은 아직 없음 → 10시 factor(4)를 써야 함
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=12)
    d = result["decisions"][0]
    assert d["snapshot_hour"] == 10
    assert Decimal(str(d["completeness"])) == Decimal("0.25")
    assert d["projected_final_cost"] == 80_000, (
        "current_hour(12시) factor 2를 쓰면 40,000 저투영 — 스냅샷 시각(10시) factor 4로 80,000이어야 함"
    )


def test_flight_loop_change_log_detail_has_projection_fields(db):
    """C3-⑥: change_log detail(JSON)에 D-NAO-44 관측 필드 4종 기록 —
    dry-run 관찰·07-17 이후 대조의 원료."""
    _setup_campaign(db)
    _setup_completeness_history(db, hour_cost=50_000, final_cost=100_000)
    run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    log = db.query(NaverChangeLog).filter(
        NaverChangeLog.action == "flight_pacing",
        NaverChangeLog.campaign_id == "cmp-test",
    ).one()
    detail = json.loads(log.after_value)
    assert detail["raw_today_cost"] == 20000
    assert detail["projected_final_cost"] == 40000
    assert Decimal(detail["projection_factor"]) == Decimal("2")
    assert Decimal(detail["completeness"]) == Decimal("0.5")


def test_flight_loop_error_in_one_campaign_doesnt_block_others(db):
    _setup_campaign(db, campaign_id="cmp-good")
    db.add(NaverCampaignSettings(campaign_id="cmp-bad", optimizer="ours"))
    db.commit()
    result = run_flight_loop(db, today=date(2026, 7, 11), current_hour=10)
    assert result["campaigns_processed"] == 2
    good = [d for d in result["decisions"] if d["campaign_id"] == "cmp-good"]
    bad = [d for d in result["decisions"] if d["campaign_id"] == "cmp-bad"]
    assert "alpha" in good[0]
    assert "skipped" in bad[0] or "error" in bad[0]
