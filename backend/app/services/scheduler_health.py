# 이 파일은 스케줄러 워치독 Harness다 (S5b S4, 읽기 전용 — SA 정보 유통 허브, 원칙18).
# scheduler.running + 등록 누락 잡 산출 + SchedulerState 로드 + cron→interval 산출을 모아
# 순수 SA②(staleness_evaluator)에 주입하고, /api/scheduler/health가 표면화할 dict를 만든다.
# 머니로직 미접촉. DB/스케줄러 접근은 compute_scheduler_health에 한정하고, 판정 로직(build_health)과
# interval 산출(compute_interval_seconds)은 순수 함수로 분리해 단위 테스트한다(원칙22).
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional, Sequence

from apscheduler.triggers.cron import CronTrigger

from app.services.scheduler_watchdog import (
    STATE_DISABLED,
    STATE_FAILED,
    STATE_NEVER_SUCCEEDED,
    STATE_STALE,
    evaluate_cookie_freshness,
    evaluate_staleness,
)

log = logging.getLogger(__name__)

# 워치독 대상 allowlist (계획 §3, Jino 승인 critical-only). 서버측 필수 잡만.
# 제외(fail-soft/Mac·쿠키 의존): sync_coupang_rg_inbound, sync_coupang_rg_settlement,
#   auto_download_rg_settlement, sync_coupang_ad_cost, request_ad_cost_refresh, cafe24_token_refresh.
WATCHDOG_JOBS: tuple[str, ...] = (
    "auto_sync_orders",
    "auto_profit_calc",
    "sync_naver_settlement",
    "sync_naver_case_settlement",
    "sync_naver_sa_ad_costs",
    "sync_naver_ad_daily",
    "sync_meta_ad_costs",
    "sync_coupang_products",
    "sync_coupang_rg_sizes",
    "sync_coupang_rg_inventory",
    "sync_coupang_returns",
    "sync_coupang_settlement",
    "sync_coupang_rg_orders",
    "sync_coupang_coupons",
    "sync_coupang_cs",
)

# 에러 요약 최대 길이 — sanitized 한 줄(전체 traceback은 DB에만, codex #12 누출 방지).
_ERR_SUMMARY_MAX = 200

# 쿠키 freshness 감시 대상 — 돈에 직결되는 fail-soft 잡의 쿠키만(codex P2: 전체 감시 시 폐기/회전
# 쿠키가 영구 stale 노이즈). WING1/WING2=RG 정산(net_profit), ADS1=쿠팡 광고비(net_profit).
WATCHDOG_COOKIES: tuple[str, ...] = (
    "COUPANG_WING1",
    "COUPANG_WING2",
    "COUPANG_ADS1",
    # 1P 로켓 광고비(net_profit)·데일리 주기. 워치독 밖이라 만료가 조용히 묻혀 광고비가
    # 끊긴 사고 재발 방지 — prod 행 존재 실측(07-17).
    "COUPANG_OHITECH_AD",
)


def compute_interval_seconds(cron_expression: str) -> float:
    """cron 식의 기대 주기(초)를 CronTrigger 2회 발화 diff로 산출한다(신규 의존 0, 순수).

    파싱 실패/발화 불가 시 0.0 반환 → evaluator가 해당 잡을 stale/never_succeeded로 판정하지 않음
    (fail-safe: 주기 불명이면 헛알림보다 침묵, 계획 §9 failure-mode).

    ★가정(codex S4 [P2] #2): WATCHDOG_JOBS는 전부 '매일 고정시각' 또는 'N시간마다'(균등 주기)이고
    타임존 Asia/Seoul은 DST가 없으므로 첫 2회 발화 diff = 정확한 주기다. 만약 allowlist에 요일限定
    (예: 평일만)·월간·day-of-month 같은 불규칙 cron을 추가하면 이 산출이 '최소 gap'을 잡아 stale을
    과탐할 수 있다(누락은 아님) → 그때는 연속 N회 발화의 최대 gap을 쓰도록 재검토할 것.
    """
    try:
        trig = CronTrigger.from_crontab(cron_expression, timezone="Asia/Seoul")
    except Exception:
        log.warning("[워치독] cron 파싱 실패 — interval 0 처리: %r", cron_expression)
        return 0.0

    try:
        tz = getattr(trig, "timezone", None)
        base = datetime(2026, 1, 1, 0, 0, 0)
        if tz is not None and hasattr(tz, "localize"):  # pytz
            ref = tz.localize(base)
        elif tz is not None:  # zoneinfo/tzinfo
            ref = base.replace(tzinfo=tz)
        else:
            ref = base

        first = trig.get_next_fire_time(None, ref)
        if first is None:
            return 0.0
        # 첫 발화 직후를 now로 주면 그 다음 발화를 얻는다(같은 시각 재반환 회피).
        second = trig.get_next_fire_time(first, first + timedelta(microseconds=1))
        if second is None:
            return 0.0
        return (second - first).total_seconds()
    except Exception:
        log.warning("[워치독] interval 산출 실패 — 0 처리: %r", cron_expression)
        return 0.0


def _sanitize_error(last_error: Optional[str]) -> Optional[str]:
    """저장된 traceback에서 마지막 줄(예외 클래스+메시지)만 추출한다(누출 방지, codex #12).

    전체 traceback은 DB(last_error)에만 남기고, API 응답엔 한 줄 요약만 노출한다.
    """
    if not last_error:
        return None
    lines = [ln for ln in last_error.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    return lines[-1].strip()[:_ERR_SUMMARY_MAX]


def build_health(
    watched_jobs: Sequence[str],
    states: Iterable[Any],
    registered_job_names: set[str],
    scheduler_running: bool,
    now: datetime,
    cookies: Iterable[Any] = (),
) -> dict:
    """워치독 판정 코어(순수: DB/스케줄러 미접촉 — 인자로 받은 스냅샷만 사용).

    states: SchedulerState 유사 객체(.job_name/.is_enabled/.cron_expression/.last_run_at/
            .last_status/.last_status_at/.created_at/.last_error). registered_job_names:
            현재 APScheduler에 등록된 잡 id 집합. cookies: CoupangWingCookie 유사 객체
            (.account_key/.status/.last_success_at) — fail-soft 잡의 쿠키 만료를 직접 감시.
            반환 dict는 그대로 API 응답이 된다.
    """
    by_name = {getattr(s, "job_name", None): s for s in states}

    missing_jobs: list[str] = []
    snapshots: list[dict] = []
    err_by_name: dict[str, Optional[str]] = {}

    for name in watched_jobs:
        state = by_name.get(name)
        if state is None:
            # allowlist에 있으나 DB row 부재 → 등록·기록 자체가 없는 1급 결손.
            missing_jobs.append(name)
            continue

        is_enabled = bool(getattr(state, "is_enabled", True))
        # 스케줄러가 돌고 잡이 enabled인데 APScheduler 미등록 → start_scheduler 실패 등 1급 신호(codex #4).
        if scheduler_running and is_enabled and name not in registered_job_names:
            missing_jobs.append(name)

        err_by_name[name] = getattr(state, "last_error", None)
        snapshots.append(
            {
                "job_name": name,
                "is_enabled": is_enabled,
                "expected_interval_sec": compute_interval_seconds(
                    getattr(state, "cron_expression", "") or ""
                ),
                "last_run_at": getattr(state, "last_run_at", None),
                "last_status": getattr(state, "last_status", None),
                "last_status_at": getattr(state, "last_status_at", None),
                "created_at": getattr(state, "created_at", None),
            }
        )

    verdicts = evaluate_staleness(snapshots, now)

    failed: list[dict] = []
    stale: list[dict] = []
    never_succeeded: list[dict] = []
    disabled: list[dict] = []
    for v in verdicts:
        state_name = v["state"]
        if state_name == STATE_FAILED:
            entry = dict(v)
            entry["error_summary"] = _sanitize_error(err_by_name.get(v["job_name"]))
            failed.append(entry)
        elif state_name == STATE_STALE:
            stale.append(dict(v))
        elif state_name == STATE_NEVER_SUCCEEDED:
            never_succeeded.append(dict(v))
        elif state_name == STATE_DISABLED:
            disabled.append(dict(v))

    # 쿠키 freshness — fail-soft 잡(RG 정산·광고)이 쿠키 만료로 조용히 멈춘 걸 직접 잡는다.
    cookie_snaps = [
        {
            "account_key": getattr(c, "account_key", "?"),
            "status": getattr(c, "status", None),
            "last_success_at": getattr(c, "last_success_at", None),
        }
        for c in cookies
    ]
    cookies_stale = evaluate_cookie_freshness(cookie_snaps, now)

    # disabled는 정상(노이즈 제외) — healthy 판정에서 무시. 그 외 어떤 비정상이라도 healthy=False.
    healthy = (
        scheduler_running
        and not missing_jobs
        and not failed
        and not stale
        and not never_succeeded
        and not cookies_stale
    )

    return {
        "healthy": healthy,
        "scheduler_running": scheduler_running,
        "missing_jobs": missing_jobs,
        "failed": failed,
        "stale": stale,
        "never_succeeded": never_succeeded,
        "disabled": disabled,
        "cookies_stale": cookies_stale,
        "as_of": now.isoformat(),
    }


def compute_scheduler_health(db, scheduler, now: datetime) -> dict:
    """Harness 진입점(I/O 경계): DB에서 SchedulerState 로드 + 스케줄러 등록 잡 조회 후 build_health.

    읽기 전용. 머니로직 미접촉. 라우터(GET /api/scheduler/health)가 호출한다.
    """
    # 지연 임포트(순수 코어를 app 의존 없이 테스트하기 위함)
    from app.models import CoupangWingCookie, SchedulerState

    running = bool(getattr(scheduler, "running", False))
    registered: set[str] = set()
    if running:
        try:
            registered = {j.id for j in scheduler.get_jobs()}
        except Exception:
            log.exception("[워치독] get_jobs 조회 실패 — 등록 목록 빈 집합 처리")

    states = (
        db.query(SchedulerState)
        .filter(SchedulerState.job_name.in_(WATCHDOG_JOBS))
        .all()
    )
    cookies = (
        db.query(CoupangWingCookie)
        .filter(CoupangWingCookie.account_key.in_(WATCHDOG_COOKIES))
        .all()
    )
    return build_health(WATCHDOG_JOBS, states, registered, running, now, cookies=cookies)
