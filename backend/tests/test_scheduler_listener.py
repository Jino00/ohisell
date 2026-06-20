# test_scheduler_listener.py — 워치독 리스너 매핑(_apply_job_event) + 삼킴잡 re-raise 정렬 (S5b S3).
#   ① EVENT_JOB_EXECUTED/ERROR/MISSED → SchedulerState 필드 매핑(순수 mutation)
#   ② error/missed는 last_run_at(마지막 성공) 보존, traceback 2000자 절단
#   ③ 삼키던 잡(naver_settlement)이 catastrophic 실패 시 re-raise(거짓 ok 봉인)
from __future__ import annotations

import sys
from datetime import datetime

import pytest

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
)

from app.services.scheduler_service import _apply_job_event

NOW = datetime(2026, 6, 20, 12, 0, 0)
PRIOR_SUCCESS = datetime(2026, 6, 20, 6, 0, 0)


class _FakeState:
    def __init__(self, **kw):
        self.last_run_at = kw.get("last_run_at")
        self.last_status = kw.get("last_status")
        self.last_error = kw.get("last_error")
        self.last_status_at = kw.get("last_status_at")


# ── EXECUTED → ok + last_run_at 갱신 ───────────────────────────────────────
def test_executed_sets_ok_and_last_run_at():
    s = _FakeState(last_status="error", last_error="이전에러", last_run_at=PRIOR_SUCCESS)
    _apply_job_event(s, EVENT_JOB_EXECUTED, NOW)
    assert s.last_run_at == NOW
    assert s.last_status == "ok"
    assert s.last_error is None
    assert s.last_status_at == NOW


# ── ERROR → error, last_run_at(마지막 성공) 보존 ───────────────────────────
def test_error_preserves_last_run_at_and_records_traceback():
    s = _FakeState(last_status="ok", last_run_at=PRIOR_SUCCESS)
    _apply_job_event(s, EVENT_JOB_ERROR, NOW, traceback="Traceback ... boom")
    assert s.last_status == "error"
    assert s.last_error == "Traceback ... boom"
    assert s.last_status_at == NOW
    assert s.last_run_at == PRIOR_SUCCESS  # 마지막 성공 불변


def test_error_truncates_long_traceback_to_2000():
    s = _FakeState()
    _apply_job_event(s, EVENT_JOB_ERROR, NOW, traceback="x" * 5000)
    assert len(s.last_error) == 2000


def test_error_falls_back_to_exception_str_when_no_traceback():
    s = _FakeState()
    _apply_job_event(s, EVENT_JOB_ERROR, NOW, traceback=None, exception=ValueError("터짐"))
    assert "터짐" in s.last_error
    assert s.last_status == "error"


# ── MISSED → missed, last_run_at 보존 ──────────────────────────────────────
def test_missed_sets_missed_and_preserves_last_run_at():
    s = _FakeState(last_status="ok", last_run_at=PRIOR_SUCCESS)
    _apply_job_event(s, EVENT_JOB_MISSED, NOW)
    assert s.last_status == "missed"
    assert s.last_status_at == NOW
    assert s.last_run_at == PRIOR_SUCCESS


# ── 삼킴잡 re-raise 정렬: catastrophic 실패가 전파돼 EVENT_JOB_ERROR로 표면화 ──
@pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="로컬 3.9: app 전체 임포트 시 Pydantic 'X|None' 평가 실패(prod 3.10 정상). "
    "라이브 검증 §6.3(고의 raise→health failed)이 실증.",
)
def test_swallowing_job_now_reraises_on_failure(monkeypatch):
    # naver_settlement은 첫 단계에서 get_naver_config를 부른다 → 여기서 raise하면 DB 미접근.
    import app.config

    def _boom(*a, **k):
        raise RuntimeError("config 폭발")

    monkeypatch.setattr(app.config, "get_naver_config", _boom)

    from app.services.scheduler_service import sync_naver_settlement_job

    # 정렬 전(삼킴)이면 조용히 리턴했겠지만, S3 정렬 후엔 예외가 전파돼야 한다.
    with pytest.raises(RuntimeError, match="config 폭발"):
        sync_naver_settlement_job()
