# test_scheduler_naver_keyword_hourly.py — sweep_naver_keyword_hourly 크론 등록 확인 (D-NAO-46②)
# scheduler_service의 부수효과 없는 정적 등록 구조만 검증(실제 스케줄러 기동은 다른 테스트 영역).
from __future__ import annotations

import inspect

from app.services import scheduler_service


def test_job_function_exists_and_has_self_contained_session_pattern():
    assert hasattr(scheduler_service, "sweep_naver_keyword_hourly_job")
    src = inspect.getsource(scheduler_service.sweep_naver_keyword_hourly_job)
    assert "_get_own_db_session" in src
    assert "db.close()" in src


def test_registered_in_catchup_order_after_retro_scoring():
    order = scheduler_service._CATCHUP_ORDER
    assert "sweep_naver_keyword_hourly" in order
    assert order.index("sweep_naver_keyword_hourly") > order.index("run_naver_retro_scoring")


def test_default_cron_is_0910_kst():
    src = inspect.getsource(scheduler_service._ensure_default_states)
    assert '("sweep_naver_keyword_hourly", "10 9 * * *")' in src
