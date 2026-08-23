# test_naver_retro_scoring_loop.py — D-NAO-45 retro_scoring_loop Harness 단위 테스트
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverProductBep, NaverRetroPacingScore, NaverRetroSignal
from app.services.naver_ad import diagnosis, retro_scoring_loop

TODAY = date(2026, 7, 20)


@pytest.fixture
def db(monkeypatch):
    def _fixed_cf(db, date_to):
        return {"factor": Decimal("1"), "factor_low": Decimal("1"), "factor_high": Decimal("1"), "factor_point": Decimal("1"), "source": "test-fixed", "window_revenue": 0, "window_conv_amt": 0}

    monkeypatch.setattr(diagnosis, "correction_factor", _fixed_cf)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _bep(db):
    db.add(NaverProductBep(
        channel_id=6, channel_product_id="cp-1", product_name="테스트상품",
        selling_price=Decimal("10000"), cost_price=Decimal("5000"),
        commission_rate=Decimal("0.05"), logistics_cost=Decimal("1000"),
        contribution_margin=Decimal("3000"), bep_roas=Decimal("2.0"),
        aggressiveness="standard", target_roas=Decimal("4.0"), has_cost=True,
    ))


def _row(db, ad_date, campaign_id, campaign_type, adgroup_id, keyword_id, imp, clk, cost, direct=0):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id=keyword_id,
        imp=imp, clk=clk, cost=cost, rank_sum=0,
        conv_direct_amt=direct, conv_indirect_amt=0,
    ))


# ── 단계 오케스트레이션 ──
def test_run_daily_retro_calls_stages_with_correct_asof(db, monkeypatch):
    calls: dict = {}

    def fake_snapshot(db, asof):
        calls["snapshot_asof"] = asof
        return {"snapshotted": 0}

    def fake_score_due(db, today):
        calls["score_today"] = today
        return {"scored_d3": 0, "scored_d7": 0}

    def fake_score_alerts(db, upto):
        calls["pacing_upto"] = upto
        return {"scored": 0, "unparsed": 0, "skipped": 0}

    monkeypatch.setattr(retro_scoring_loop.retro_snapshotter, "snapshot_signals", fake_snapshot)
    monkeypatch.setattr(retro_scoring_loop.retro_scorer, "score_due", fake_score_due)
    monkeypatch.setattr(retro_scoring_loop.retro_pacing_scorer, "score_alerts", fake_score_alerts)

    result = retro_scoring_loop.run_daily_retro(db, today=TODAY)

    assert calls["snapshot_asof"] == TODAY - timedelta(days=1)
    assert calls["score_today"] == TODAY
    assert calls["pacing_upto"] == TODAY - timedelta(days=1)
    assert result["stage_status"] == {"snapshotter": "ok", "scorer": "ok", "pacing_scorer": "ok"}


def test_run_daily_retro_defaults_today_to_kst_today(db, monkeypatch):
    monkeypatch.setattr(retro_scoring_loop, "kst_today", lambda: TODAY)
    calls: dict = {}
    monkeypatch.setattr(retro_scoring_loop.retro_snapshotter, "snapshot_signals",
                         lambda db, asof: calls.setdefault("asof", asof) or {"snapshotted": 0})
    monkeypatch.setattr(retro_scoring_loop.retro_scorer, "score_due",
                         lambda db, today: {"scored_d3": 0, "scored_d7": 0})
    monkeypatch.setattr(retro_scoring_loop.retro_pacing_scorer, "score_alerts",
                         lambda db, upto: {"scored": 0, "unparsed": 0, "skipped": 0})

    retro_scoring_loop.run_daily_retro(db)
    assert calls["asof"] == TODAY - timedelta(days=1)


# ── 단계 독립 격리(partial-failure) ──
def test_run_daily_retro_isolates_snapshotter_failure(db, monkeypatch):
    def boom(db, asof):
        raise RuntimeError("diagnosis 폭발")

    monkeypatch.setattr(retro_scoring_loop.retro_snapshotter, "snapshot_signals", boom)
    monkeypatch.setattr(retro_scoring_loop.retro_scorer, "score_due",
                         lambda db, today: {"scored_d3": 1, "scored_d7": 0})
    monkeypatch.setattr(retro_scoring_loop.retro_pacing_scorer, "score_alerts",
                         lambda db, upto: {"scored": 2, "unparsed": 0, "skipped": 0})

    result = retro_scoring_loop.run_daily_retro(db, today=TODAY)
    assert result["stage_status"]["snapshotter"] == "failed"
    assert result["stage_status"]["scorer"] == "ok"
    assert result["stage_status"]["pacing_scorer"] == "ok"
    assert result["scored"]["scored_d3"] == 1
    assert result["pacing"]["scored"] == 2


def test_run_daily_retro_isolates_scorer_failure(db, monkeypatch):
    monkeypatch.setattr(retro_scoring_loop.retro_snapshotter, "snapshot_signals",
                         lambda db, asof: {"snapshotted": 3})

    def boom(db, today):
        raise RuntimeError("scorer 폭발")

    monkeypatch.setattr(retro_scoring_loop.retro_scorer, "score_due", boom)
    monkeypatch.setattr(retro_scoring_loop.retro_pacing_scorer, "score_alerts",
                         lambda db, upto: {"scored": 2, "unparsed": 0, "skipped": 0})

    result = retro_scoring_loop.run_daily_retro(db, today=TODAY)
    assert result["stage_status"]["snapshotter"] == "ok"
    assert result["stage_status"]["scorer"] == "failed"
    assert result["stage_status"]["pacing_scorer"] == "ok"
    assert result["snapshot"]["snapshotted"] == 3
    assert result["pacing"]["scored"] == 2


def test_run_daily_retro_isolates_pacing_scorer_failure(db, monkeypatch):
    monkeypatch.setattr(retro_scoring_loop.retro_snapshotter, "snapshot_signals",
                         lambda db, asof: {"snapshotted": 3})
    monkeypatch.setattr(retro_scoring_loop.retro_scorer, "score_due",
                         lambda db, today: {"scored_d3": 1, "scored_d7": 0})

    def boom(db, upto):
        raise RuntimeError("pacing 폭발")

    monkeypatch.setattr(retro_scoring_loop.retro_pacing_scorer, "score_alerts", boom)

    result = retro_scoring_loop.run_daily_retro(db, today=TODAY)
    assert result["stage_status"]["snapshotter"] == "ok"
    assert result["stage_status"]["scorer"] == "ok"
    assert result["stage_status"]["pacing_scorer"] == "failed"
    assert result["snapshot"]["snapshotted"] == 3
    assert result["scored"]["scored_d3"] == 1


# ── backfill ──
def test_backfill_snapshots_each_day_in_range(db, monkeypatch):
    seen: list[date] = []
    monkeypatch.setattr(retro_scoring_loop.retro_snapshotter, "snapshot_signals",
                         lambda db, asof: seen.append(asof) or {"snapshotted": 0})
    monkeypatch.setattr(retro_scoring_loop.retro_scorer, "score_due",
                         lambda db, today: {"scored_d3": 0, "scored_d7": 0})
    monkeypatch.setattr(retro_scoring_loop.retro_pacing_scorer, "score_alerts",
                         lambda db, upto: {"scored": 0, "unparsed": 0, "skipped": 0})

    date_from, date_to = date(2026, 7, 8), date(2026, 7, 10)
    retro_scoring_loop.backfill(db, date_from, date_to, today=TODAY)
    assert seen == [date(2026, 7, 8), date(2026, 7, 9), date(2026, 7, 10)]


def test_backfill_is_idempotent_on_rerun(db):
    """real SA 조합(monkeypatch 없음) — 같은 구간 재실행해도 스냅샷 중복·재채점 없음."""
    _bep(db)
    d1 = date(2026, 7, 8)
    _row(db, d1, "cmp1", "WEB_SITE", "grp1", "nkw-1", 1000, 50, 10000, direct=1000)  # bleeding(down)
    db.commit()

    first = retro_scoring_loop.backfill(db, d1, d1, today=TODAY)
    assert first["days"][0]["snapshot"]["snapshotted"] == 1
    n_after_first = db.query(NaverRetroSignal).count()

    second = retro_scoring_loop.backfill(db, d1, d1, today=TODAY)
    assert second["days"][0]["snapshot"]["snapshotted"] == 0
    assert db.query(NaverRetroSignal).count() == n_after_first


def test_backfill_isolates_single_day_snapshot_failure(db, monkeypatch):
    def flaky(db, asof):
        if asof == date(2026, 7, 9):
            raise RuntimeError("하루만 폭발")
        return {"snapshotted": 0}

    monkeypatch.setattr(retro_scoring_loop.retro_snapshotter, "snapshot_signals", flaky)
    monkeypatch.setattr(retro_scoring_loop.retro_scorer, "score_due",
                         lambda db, today: {"scored_d3": 0, "scored_d7": 0})
    monkeypatch.setattr(retro_scoring_loop.retro_pacing_scorer, "score_alerts",
                         lambda db, upto: {"scored": 0, "unparsed": 0, "skipped": 0})

    result = retro_scoring_loop.backfill(db, date(2026, 7, 8), date(2026, 7, 10), today=TODAY)
    assert "error" in result["days"][1]
    assert "snapshot" in result["days"][0]
    assert "snapshot" in result["days"][2]


def test_run_daily_retro_rolls_back_poisoned_session_before_next_stage(db, monkeypatch):
    """codex[P2] 회귀: 단계가 DB 예외(IntegrityError 등)로 죽으면 세션이 failed 트랜잭션
    상태로 남는다 — 핸들러가 rollback하지 않으면 다음 단계가 PendingRollbackError로 연쇄
    실패해 격리 설계가 무력화된다. 진짜 DB 오염으로 재현(문자열 예외 아님)."""
    from app.services.naver_ad import retro_snapshotter

    def poison_snapshot(db_, asof):
        row = dict(
            asof_date=asof, board="bleeding_keywords", direction="down", grain="keyword",
            target_id="nkw-dup", campaign_id="cmp1",
            cf_asof=1.0, bep_asof=2.0, target_asof=4.0, cost_asof=100,
        )
        db_.add(NaverRetroSignal(**row))
        db_.flush()
        db_.add(NaverRetroSignal(**row))  # (asof,board,target) UNIQUE 위반
        db_.flush()  # IntegrityError → 세션 failed 상태로 전이

    monkeypatch.setattr(retro_snapshotter, "snapshot_signals", poison_snapshot)
    result = retro_scoring_loop.run_daily_retro(db, today=TODAY)

    assert result["stage_status"]["snapshotter"] == "failed"
    # rollback 덕에 이후 단계는 PendingRollbackError 없이 정상 완주해야 한다
    assert result["stage_status"]["scorer"] == "ok"
    assert result["stage_status"]["pacing_scorer"] == "ok"
