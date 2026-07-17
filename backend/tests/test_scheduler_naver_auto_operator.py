# test_scheduler_naver_auto_operator.py — auto_operator 일/시간당 크론 등록 확인 (D-NAO-49)
# scheduler_service의 부수효과 없는 정적 등록 구조만 검증(test_scheduler_naver_keyword_hourly.py
# 와 동일 패턴). 이 개발 환경에는 apscheduler가 설치돼 있지 않아(기존 세션에서도 동일 이슈)
# scheduler_service import 전에 sys.modules에 최소 스텁을 심는다 — 이미 설치된 환경(CI 등)
# 에서는 실제 apscheduler가 우선하도록 "이미 로드돼 있으면 건드리지 않음" 가드를 둔다.
from __future__ import annotations

import inspect
import sys
import types

if "apscheduler" not in sys.modules:
    _apscheduler = types.ModuleType("apscheduler")
    _events = types.ModuleType("apscheduler.events")
    _events.EVENT_JOB_EXECUTED = 1
    _events.EVENT_JOB_ERROR = 2
    _events.EVENT_JOB_MISSED = 4
    _schedulers = types.ModuleType("apscheduler.schedulers")
    _background = types.ModuleType("apscheduler.schedulers.background")

    class _StubBackgroundScheduler:
        def __init__(self, *a, **kw):
            pass

        def add_job(self, *a, **kw):
            pass

        def add_listener(self, *a, **kw):
            pass

        def start(self):
            pass

        def shutdown(self, *a, **kw):
            pass

    _background.BackgroundScheduler = _StubBackgroundScheduler
    _triggers = types.ModuleType("apscheduler.triggers")
    _cron = types.ModuleType("apscheduler.triggers.cron")

    class _StubCronTrigger:
        @classmethod
        def from_crontab(cls, *a, **kw):
            return cls()

    _cron.CronTrigger = _StubCronTrigger

    sys.modules["apscheduler"] = _apscheduler
    sys.modules["apscheduler.events"] = _events
    sys.modules["apscheduler.schedulers"] = _schedulers
    sys.modules["apscheduler.schedulers.background"] = _background
    sys.modules["apscheduler.triggers"] = _triggers
    sys.modules["apscheduler.triggers.cron"] = _cron

from app.services import scheduler_service  # noqa: E402


def test_daily_job_function_exists_and_has_self_contained_session_pattern():
    assert hasattr(scheduler_service, "run_naver_auto_operator_daily_job")
    src = inspect.getsource(scheduler_service.run_naver_auto_operator_daily_job)
    assert "_get_own_db_session" in src
    assert "db.close()" in src


def test_hourly_job_function_exists_and_has_self_contained_session_pattern():
    assert hasattr(scheduler_service, "run_naver_auto_operator_hourly_job")
    src = inspect.getsource(scheduler_service.run_naver_auto_operator_hourly_job)
    assert "_get_own_db_session" in src
    assert "db.close()" in src


def test_default_cron_daily_is_0850_kst():
    src = inspect.getsource(scheduler_service._ensure_default_states)
    assert '("run_naver_auto_operator_daily", "50 8 * * *")' in src


def test_default_cron_hourly_is_minute20_every_hour():
    src = inspect.getsource(scheduler_service._ensure_default_states)
    assert '("run_naver_auto_operator_hourly", "20 * * * *")' in src


def test_daily_registered_in_catchup_order_after_retro_scoring_before_sweep():
    order = scheduler_service._CATCHUP_ORDER
    assert "run_naver_auto_operator_daily" in order
    assert order.index("run_naver_auto_operator_daily") > order.index("run_naver_retro_scoring")
    assert order.index("run_naver_auto_operator_daily") < order.index("sweep_naver_keyword_hourly")


def test_hourly_not_registered_in_catchup_order():
    # 시간성 소멸 — catch-up 제외(PLAN §5)
    assert "run_naver_auto_operator_hourly" not in scheduler_service._CATCHUP_ORDER


def test_daily_wired_in_catchup_funcs_dict():
    src = inspect.getsource(scheduler_service._catch_up_morning_batch)
    assert '"run_naver_auto_operator_daily": run_naver_auto_operator_daily_job' in src


def test_start_scheduler_wires_both_job_names():
    src = inspect.getsource(scheduler_service.start_scheduler)
    assert 'state.job_name == "run_naver_auto_operator_daily"' in src
    assert "job_func = run_naver_auto_operator_daily_job" in src
    assert 'state.job_name == "run_naver_auto_operator_hourly"' in src
    assert "job_func = run_naver_auto_operator_hourly_job" in src
