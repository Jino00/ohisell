# test_no_ad_cost_refresh_cron.py — 자동 트리거 제거 회귀 가드(D: 순수 on-demand).
#   request_ad_cost_refresh 크론이 defaults·job map·함수에서 완전히 빠졌는지 고정.
import inspect

from app.services import scheduler_service as ss


def test_request_ad_cost_refresh_not_in_defaults():
    src = inspect.getsource(ss._ensure_default_states)
    assert "request_ad_cost_refresh" not in src


def test_request_ad_cost_refresh_not_in_job_map():
    assert ss.job_func_for("request_ad_cost_refresh") is None


def test_request_ad_cost_refresh_func_removed():
    assert not hasattr(ss, "request_ad_cost_refresh_job")
