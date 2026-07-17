# test_scheduler_health.py — 워치독 Harness 순수 코어 (S5b S4).
#   ① compute_interval_seconds: cron→기대 주기 산출(일배치 86400 / 2시간 7200 / 30분 1800 / 불량 0)
#   ② build_health: 버킷팅·missing_jobs(DB결손/미등록)·healthy 판정·에러 sanitize
# DB/스케줄러 미접촉(인자 주입) → 로컬 3.9에서도 검증 가능(원칙22, 계획서 §5).
from __future__ import annotations

from datetime import datetime, timedelta

from app.services.scheduler_health import (
    WATCHDOG_COOKIES,
    build_health,
    compute_interval_seconds,
    _sanitize_error,
)

NOW = datetime(2026, 6, 20, 12, 0, 0)


class _FakeCookie:
    def __init__(self, **kw):
        self.account_key = kw["account_key"]
        self.status = kw.get("status", "red")
        self.last_success_at = kw.get("last_success_at")


class _FakeState:
    """SchedulerState 유사 객체(duck-typed)."""

    def __init__(self, **kw):
        self.job_name = kw["job_name"]
        self.is_enabled = kw.get("is_enabled", True)
        self.cron_expression = kw.get("cron_expression", "0 6 * * *")
        self.last_run_at = kw.get("last_run_at", NOW - timedelta(minutes=5))
        self.last_status = kw.get("last_status", "ok")
        self.last_status_at = kw.get("last_status_at", NOW - timedelta(minutes=5))
        self.created_at = kw.get("created_at", NOW - timedelta(days=30))
        self.last_error = kw.get("last_error")


# ── compute_interval_seconds ───────────────────────────────────────────────
def test_interval_daily_cron():
    assert compute_interval_seconds("0 6 * * *") == 86400.0


def test_interval_two_hourly_cron():
    assert compute_interval_seconds("0 */2 * * *") == 7200.0


def test_interval_30min_cron():
    assert compute_interval_seconds("*/30 * * * *") == 1800.0


def test_interval_bad_cron_returns_zero():
    assert compute_interval_seconds("not a cron") == 0.0
    assert compute_interval_seconds("") == 0.0


# ── _sanitize_error: 마지막 줄(예외 클래스+메시지)만, 전체 traceback 미노출 ──
def test_sanitize_error_keeps_only_last_line():
    tb = (
        'Traceback (most recent call last):\n'
        '  File "x.py", line 10, in foo\n'
        '    raise RuntimeError("config 폭발")\n'
        'RuntimeError: config 폭발'
    )
    s = _sanitize_error(tb)
    assert s == "RuntimeError: config 폭발"
    assert "Traceback" not in s
    assert "File" not in s


def test_sanitize_error_none_and_empty():
    assert _sanitize_error(None) is None
    assert _sanitize_error("") is None
    assert _sanitize_error("   \n  ") is None


def test_sanitize_error_truncates_to_200():
    s = _sanitize_error("E: " + "x" * 5000)
    assert len(s) == 200


# ── 워치독 쿠키 allowlist: 1P 로켓 광고비 편입(만료가 조용히 묻히던 사고 방지) ──
def test_watchdog_cookies_includes_ohitech_ad():
    assert "COUPANG_OHITECH_AD" in WATCHDOG_COOKIES


# ── build_health: 전부 정상 → healthy True ─────────────────────────────────
def test_build_health_all_ok():
    states = [_FakeState(job_name="a"), _FakeState(job_name="b")]
    h = build_health(["a", "b"], states, {"a", "b"}, True, NOW)
    assert h["healthy"] is True
    assert h["scheduler_running"] is True
    assert h["missing_jobs"] == []
    assert h["failed"] == [] and h["stale"] == [] and h["never_succeeded"] == []
    assert h["as_of"] == NOW.isoformat()


# ── failed: last_status=error → failed 버킷 + sanitized 요약, healthy False ──
def test_build_health_failed_job_with_error_summary():
    states = [
        _FakeState(job_name="a"),
        _FakeState(
            job_name="b",
            last_status="error",
            last_error='Traceback ...\nValueError: 터짐',
        ),
    ]
    h = build_health(["a", "b"], states, {"a", "b"}, True, NOW)
    assert h["healthy"] is False
    assert len(h["failed"]) == 1
    f = h["failed"][0]
    assert f["job_name"] == "b"
    assert f["state"] == "failed"
    assert f["error_summary"] == "ValueError: 터짐"


# ── stale: 마지막 성공이 주기×1.5 초과 → stale, healthy False ──────────────
def test_build_health_stale_job():
    # 일배치(86400s)인데 마지막 성공 3일 전 → stale.
    states = [_FakeState(job_name="a", last_run_at=NOW - timedelta(days=3))]
    h = build_health(["a"], states, {"a"}, True, NOW)
    assert h["healthy"] is False
    assert len(h["stale"]) == 1
    assert h["stale"][0]["job_name"] == "a"


# ── missing: 스케줄러는 도는데 enabled 잡이 미등록 → missing_jobs, healthy False ──
def test_build_health_missing_registered_job():
    states = [_FakeState(job_name="a"), _FakeState(job_name="b")]
    # b가 APScheduler 미등록(start_scheduler 부분 실패 모사).
    h = build_health(["a", "b"], states, {"a"}, True, NOW)
    assert h["healthy"] is False
    assert h["missing_jobs"] == ["b"]


# ── missing: allowlist에 있으나 DB row 자체가 없음 → missing_jobs ──────────
def test_build_health_missing_db_state():
    states = [_FakeState(job_name="a")]
    h = build_health(["a", "b"], states, {"a", "b"}, True, NOW)
    assert "b" in h["missing_jobs"]
    assert h["healthy"] is False


# ── 스케줄러 정지 → healthy False, scheduler_running False (잡 누락으로 도배 안 함) ──
def test_build_health_scheduler_not_running():
    states = [_FakeState(job_name="a"), _FakeState(job_name="b")]
    # 정지 시 registered는 빈 집합이지만 missing 도배는 안 한다(running 가드).
    h = build_health(["a", "b"], states, set(), False, NOW)
    assert h["scheduler_running"] is False
    assert h["healthy"] is False
    assert h["missing_jobs"] == []


# ── disabled 잡은 정상 취급(노이즈 제외) → healthy 유지 ──────────────────────
def test_build_health_disabled_job_does_not_break_health():
    states = [
        _FakeState(job_name="a"),
        _FakeState(job_name="b", is_enabled=False, last_status="error"),
    ]
    h = build_health(["a", "b"], states, {"a"}, True, NOW)
    # b는 비활성 → disabled 버킷, missing/ failed 아님, healthy 유지.
    assert h["healthy"] is True
    assert len(h["disabled"]) == 1
    assert h["disabled"][0]["job_name"] == "b"
    assert h["failed"] == []
    assert h["missing_jobs"] == []


# ── never_succeeded: 성공 기록 없음 + 등록 후 주기 초과 → healthy False ─────
def test_build_health_never_succeeded():
    states = [
        _FakeState(
            job_name="a",
            last_run_at=None,
            last_status=None,
            created_at=NOW - timedelta(days=3),  # 주기(1일) 초과
        )
    ]
    h = build_health(["a"], states, {"a"}, True, NOW)
    assert h["healthy"] is False
    assert len(h["never_succeeded"]) == 1


# ── 쿠키 freshness 통합(build_health) ───────────────────────────────────────
def test_build_health_stale_cookie_breaks_health():
    from datetime import timedelta
    states = [_FakeState(job_name="a")]
    cookies = [
        _FakeCookie(account_key="COUPANG_WING1", last_success_at=NOW - timedelta(days=11)),  # stale
        _FakeCookie(account_key="COUPANG_WING_RG", last_success_at=NOW - timedelta(days=1)),  # ok
    ]
    h = build_health(["a"], states, {"a"}, True, NOW, cookies=cookies)
    assert h["healthy"] is False
    assert len(h["cookies_stale"]) == 1
    assert h["cookies_stale"][0]["account_key"] == "COUPANG_WING1"


def test_build_health_fresh_cookies_keep_health():
    from datetime import timedelta
    states = [_FakeState(job_name="a")]
    cookies = [_FakeCookie(account_key="X", last_success_at=NOW - timedelta(days=1))]
    h = build_health(["a"], states, {"a"}, True, NOW, cookies=cookies)
    assert h["healthy"] is True
    assert h["cookies_stale"] == []


def test_build_health_no_cookies_arg_defaults_empty():
    states = [_FakeState(job_name="a")]
    h = build_health(["a"], states, {"a"}, True, NOW)
    assert h["cookies_stale"] == []
    assert h["healthy"] is True


# ── 데이터 나이 통합(build_health) ─────────────────────────────────────────
def _data_snap(**kw):
    base = {
        "name": "rg_settlement_account_rows",
        "account_key": "COUPANG_WING1",
        "latest": NOW - timedelta(days=5),
        "max_age_days": 14.0,
        "impact": "RG 정산비용 누락 중",
    }
    base.update(kw)
    return base


def test_build_health_stale_data_breaks_health():
    states = [_FakeState(job_name="a")]
    snaps = [
        _data_snap(account_key="COUPANG_WING1", latest=NOW - timedelta(days=26)),  # stale
        _data_snap(account_key="COUPANG_WING2", latest=NOW - timedelta(days=5)),   # ok
    ]
    h = build_health(["a"], states, {"a"}, True, NOW, data_snapshots=snaps)
    assert h["healthy"] is False
    assert len(h["data_stale"]) == 1
    assert h["data_stale"][0]["account_key"] == "COUPANG_WING1"
    assert h["data_stale"][0]["state"] == "stale"


def test_build_health_no_data_breaks_health():
    states = [_FakeState(job_name="a")]
    snaps = [_data_snap(latest=None)]  # 한 번도 수집 안 됨
    h = build_health(["a"], states, {"a"}, True, NOW, data_snapshots=snaps)
    assert h["healthy"] is False
    assert h["data_stale"][0]["state"] == "no_data"


def test_build_health_fresh_data_keeps_health():
    states = [_FakeState(job_name="a")]
    snaps = [_data_snap(latest=NOW - timedelta(days=3))]
    h = build_health(["a"], states, {"a"}, True, NOW, data_snapshots=snaps)
    assert h["healthy"] is True
    assert h["data_stale"] == []


def test_build_health_no_data_snapshots_arg_defaults_empty():
    states = [_FakeState(job_name="a")]
    h = build_health(["a"], states, {"a"}, True, NOW)
    assert h["data_stale"] == []
    assert h["healthy"] is True


# ── 스키마 직렬화: response_model이 data_stale을 지우지 않고 응답에 실어야 함 ──
def test_scheduler_health_out_serializes_data_stale():
    # response_model(SchedulerHealthOut)에 필드가 없으면 API에서 조용히 사라진다 → 회귀 방지.
    from app.schemas import SchedulerHealthOut

    states = [_FakeState(job_name="a")]
    snaps = [
        {
            "name": "rg_settlement_account_rows",
            "account_key": "COUPANG_WING1",
            "latest": NOW - timedelta(days=26),
            "max_age_days": 14.0,
            "impact": "RG 정산비용(오픽스) 누락 중",
        }
    ]
    h = build_health(["a"], states, {"a"}, True, NOW, data_snapshots=snaps)
    out = SchedulerHealthOut(**h).model_dump()
    assert "data_stale" in out
    assert len(out["data_stale"]) == 1
    v = out["data_stale"][0]
    assert v["name"] == "rg_settlement_account_rows"
    assert v["account_key"] == "COUPANG_WING1"
    assert v["state"] == "stale"
    assert v["max_age_days"] == 14.0
    assert "누락" in v["impact"]


def test_scheduler_health_out_data_stale_defaults_empty():
    from app.schemas import SchedulerHealthOut

    states = [_FakeState(job_name="a")]
    h = build_health(["a"], states, {"a"}, True, NOW)
    out = SchedulerHealthOut(**h).model_dump()
    assert out["data_stale"] == []
