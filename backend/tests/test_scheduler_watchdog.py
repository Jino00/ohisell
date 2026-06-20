# test_scheduler_watchdog.py — 스케줄러 워치독 staleness_evaluator 순수함수 (S5b SA②).
# 5-state 규칙·우선순위·경계값을 I/O 없이 검증(원칙 22, 계획서 §5).
#   우선순위: disabled > failed > never_succeeded > stale > ok
from __future__ import annotations

from datetime import datetime, timedelta

from app.services.scheduler_watchdog import (
    STALE_MULTIPLIER,
    STATE_DISABLED,
    STATE_FAILED,
    STATE_NEVER_SUCCEEDED,
    STATE_OK,
    STATE_STALE,
    evaluate_job,
    evaluate_staleness,
)

NOW = datetime(2026, 6, 20, 12, 0, 0)
INTERVAL = 3600.0  # 1시간 주기
STALE_AFTER = INTERVAL * STALE_MULTIPLIER  # 5400s = 1.5h


def _job(**kw):
    base = {
        "job_name": "j",
        "is_enabled": True,
        "expected_interval_sec": INTERVAL,
        "last_run_at": NOW - timedelta(seconds=60),
        "last_status": "ok",
        "last_status_at": NOW - timedelta(seconds=60),
        "created_at": NOW - timedelta(days=30),
    }
    base.update(kw)
    return base


# ── ok ───────────────────────────────────────────────────────────────────
def test_recent_success_is_ok():
    v = evaluate_job(_job(last_run_at=NOW - timedelta(seconds=600)), NOW)
    assert v["state"] == STATE_OK
    assert v["age_sec"] == 600


# ── stale 경계 (interval×1.5) ──────────────────────────────────────────────
def test_just_before_stale_threshold_is_ok():
    v = evaluate_job(_job(last_run_at=NOW - timedelta(seconds=STALE_AFTER - 1)), NOW)
    assert v["state"] == STATE_OK


def test_just_after_stale_threshold_is_stale():
    v = evaluate_job(_job(last_run_at=NOW - timedelta(seconds=STALE_AFTER + 1)), NOW)
    assert v["state"] == STATE_STALE
    assert v["age_sec"] == STALE_AFTER + 1


def test_exactly_at_threshold_is_ok():
    # > 1.5×주기 일 때만 stale (경계는 관용).
    v = evaluate_job(_job(last_run_at=NOW - timedelta(seconds=STALE_AFTER)), NOW)
    assert v["state"] == STATE_OK


# ── failed (error/missed) — stale보다 우선 ─────────────────────────────────
def test_error_status_is_failed():
    # 오래돼서 stale 조건도 충족하지만 last_status='error'가 우선.
    v = evaluate_job(
        _job(last_status="error", last_run_at=NOW - timedelta(seconds=STALE_AFTER + 999)),
        NOW,
    )
    assert v["state"] == STATE_FAILED
    assert "error" in v["reason"]


def test_missed_status_is_failed():
    v = evaluate_job(_job(last_status="missed"), NOW)
    assert v["state"] == STATE_FAILED


# ── never_succeeded ────────────────────────────────────────────────────────
def test_never_run_and_old_job_is_never_succeeded():
    v = evaluate_job(
        _job(last_run_at=None, last_status=None, created_at=NOW - timedelta(days=1)),
        NOW,
    )
    assert v["state"] == STATE_NEVER_SUCCEEDED
    assert v["age_sec"] is None


def test_never_run_but_fresh_job_within_first_interval_is_ok():
    # 갓 등록(< 한 주기) + 미실행 → 유예(헛알림 방지).
    v = evaluate_job(
        _job(last_run_at=None, last_status=None, created_at=NOW - timedelta(seconds=INTERVAL / 2)),
        NOW,
    )
    assert v["state"] == STATE_OK


def test_never_run_with_unknown_created_at_is_ok_conservative():
    v = evaluate_job(_job(last_run_at=None, last_status=None, created_at=None), NOW)
    assert v["state"] == STATE_OK


def test_failed_overrides_never_succeeded():
    # 미실행+오래된 등록(never_succeeded 조건)이지만 last_status='error'가 우선(failed).
    v = evaluate_job(
        _job(last_run_at=None, last_status="error", created_at=NOW - timedelta(days=2)),
        NOW,
    )
    assert v["state"] == STATE_FAILED


def test_never_succeeded_exact_grace_boundary_is_ok():
    # job_age == interval 은 '초과' 아님 → 아직 ok(경계 관용, stale 경계와 동일 스타일).
    v = evaluate_job(
        _job(last_run_at=None, last_status=None, created_at=NOW - timedelta(seconds=INTERVAL)),
        NOW,
    )
    assert v["state"] == STATE_OK


def test_never_succeeded_just_past_grace_boundary():
    v = evaluate_job(
        _job(last_run_at=None, last_status=None, created_at=NOW - timedelta(seconds=INTERVAL + 1)),
        NOW,
    )
    assert v["state"] == STATE_NEVER_SUCCEEDED


# ── disabled — 최우선(다른 모든 상태 누름) ─────────────────────────────────
def test_disabled_is_disabled():
    v = evaluate_job(_job(is_enabled=False), NOW)
    assert v["state"] == STATE_DISABLED


def test_disabled_overrides_error():
    # 비활성 잡은 에러/오래됨이어도 disabled(노이즈 제외).
    v = evaluate_job(
        _job(is_enabled=False, last_status="error", last_run_at=NOW - timedelta(days=10)),
        NOW,
    )
    assert v["state"] == STATE_DISABLED


def test_disabled_overrides_never_succeeded():
    v = evaluate_job(
        _job(is_enabled=False, last_run_at=None, last_status=None, created_at=NOW - timedelta(days=5)),
        NOW,
    )
    assert v["state"] == STATE_DISABLED


# ── interval 불명(0) — stale 미판정(헛알림 방지) ───────────────────────────
def test_unknown_interval_skips_stale():
    v = evaluate_job(
        _job(expected_interval_sec=0, last_run_at=NOW - timedelta(days=10)),
        NOW,
    )
    assert v["state"] == STATE_OK


# ── 배치 ───────────────────────────────────────────────────────────────────
def test_evaluate_staleness_batch_preserves_order_and_classifies_each():
    jobs = [
        _job(job_name="a", last_run_at=NOW - timedelta(seconds=60)),  # ok
        _job(job_name="b", last_status="error"),  # failed
        _job(job_name="c", last_run_at=NOW - timedelta(seconds=STALE_AFTER + 10)),  # stale
        _job(job_name="d", is_enabled=False),  # disabled
    ]
    out = evaluate_staleness(jobs, NOW)
    assert [v["job_name"] for v in out] == ["a", "b", "c", "d"]
    assert [v["state"] for v in out] == [
        STATE_OK,
        STATE_FAILED,
        STATE_STALE,
        STATE_DISABLED,
    ]
