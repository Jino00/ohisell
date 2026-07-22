# test_scheduler_naver_auto_operator.py — auto_operator 일/시간당 크론 등록 확인 (D-NAO-49)
# scheduler_service의 부수효과 없는 정적 등록 구조만 검증(test_scheduler_naver_keyword_hourly.py
# 와 동일 패턴). apscheduler가 설치되지 않은 환경에서만 scheduler_service import 전에 sys.modules에
# 최소 스텁을 심는다 — 설치된 환경(CI 등)에서는 실제 apscheduler를 절대 대체하지 않는다.
# ★가드는 반드시 find_spec(설치 여부)로 판단할 것: "sys.modules에 없음"(아직 import 안 됨)으로
#   판단하면, apscheduler가 설치돼 있어도 이 테스트가 먼저 import될 때 스텁이 실제 패키지를
#   프로세스 전역으로 덮어써(테스트 격리 오염) test_scheduler_health의 CronTrigger가 스텁이 된다.
from __future__ import annotations

import importlib.util
import inspect
import sys
import types

if importlib.util.find_spec("apscheduler") is None:
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


def test_px4_briefing_wired_with_independent_fail_open_try():
    """PX4(§4, docs/PLAN_naver-ad-powerlink-autoexclude.md): 파워링크 예외 브리핑·대행사
    주간 브리핑이 08:50 일 레인에 배선되고, 자기만의 try/except·독립 세션(db3)으로 감싸여
    있는지 소스 검증(test_naver_searchterm_px4.py가 함수 자체의 유/무 분기·침묵을 커버,
    이 테스트는 "실패해도 일 레인을 못 죽인다"는 배선 계약만 확인 — apscheduler 미설치
    환경에서도 실제 job을 실행하지 않고 정적 검증한다)."""
    src = inspect.getsource(scheduler_service.run_naver_auto_operator_daily_job)
    assert "run_exclusion_exception_briefing" in src
    assert "run_agency_powerlink_weekly_briefing" in src
    # PX4 블록이 run_search_term_ss_lane 블록과 분리된 자기 try/except를 가진다(한쪽 실패가
    # 다른 쪽·일 레인 집행을 못 막게) — "PX4 브리핑 에러(fail-open)" 로그가 그 증거.
    assert "PX4 브리핑 에러(fail-open)" in src
    assert "db3.close()" in src


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
    # 등록 매핑은 job_func_for가 단일 진실(start_scheduler·toggle 라이브 등록 공용) —
    # 소스 형태 대신 매핑 자체를 검증.
    assert scheduler_service.job_func_for("run_naver_auto_operator_daily") \
        is scheduler_service.run_naver_auto_operator_daily_job
    assert scheduler_service.job_func_for("run_naver_auto_operator_hourly") \
        is scheduler_service.run_naver_auto_operator_hourly_job
    src = inspect.getsource(scheduler_service.start_scheduler)
    assert "job_func_for(state.job_name)" in src


def test_hourly_job_has_5min_misfire_grace_not_global_1hour():
    """codex 9R[P2]: hourly 레인이 전역 misfire_grace_time=3600을 상속하면 스케줄러 지연 시
    놓친 :20 실행이 최대 1시간 늦게 발화 — 케이던스 밖 실입찰. per-job 5분(:25 내 미발화 시
    폐기, 다음 정시가 재기회) 명시. 일 레인은 기존 정책 유지(전역 상속 — per-job 미지정)."""
    assert scheduler_service._AUTO_OPERATOR_HOURLY_MISFIRE_GRACE == 300
    # per-job 옵션도 job_kwargs_for가 단일 진실 — hourly만 5분, daily는 전역 상속(빈 dict).
    assert scheduler_service.job_kwargs_for("run_naver_auto_operator_hourly") == {
        "misfire_grace_time": 300
    }
    assert scheduler_service.job_kwargs_for("run_naver_auto_operator_daily") == {}
    src = inspect.getsource(scheduler_service.start_scheduler)
    assert "job_kwargs_for(state.job_name)" in src
