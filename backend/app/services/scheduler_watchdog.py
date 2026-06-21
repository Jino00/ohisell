# 이 파일은 스케줄러 잡의 stale/실패 상태를 판정하는 순수 함수다 (S5b 워치독 SA②, I/O 없음).
# 입력으로 받은 잡 상태 스냅샷과 기준 시각(now)만으로 5-state를 산출한다. DB/네트워크 미접촉
# → 단위 테스트로 모든 경계를 검증 가능(원칙 22). Harness(scheduler_health, S4)가 SchedulerState를
# 읽어 이 함수에 주입하고, 결과를 /api/scheduler/health로 표면화한다.
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence, TypedDict

# 5-state (계획서 §3 SA② 규칙, 우선순위 순). 'disabled'는 노이즈 제외, 나머지 중 'ok'만 정상.
STATE_OK = "ok"
STATE_DISABLED = "disabled"
STATE_FAILED = "failed"
STATE_NEVER_SUCCEEDED = "never_succeeded"
STATE_STALE = "stale"

# 마지막 성공 후 (기대 주기 × 이 배수)를 넘기면 stale. cron 잡의 1회 미스파이어는 관용,
# 연속 실패만 잡기 위한 여유.
STALE_MULTIPLIER = 1.5


class JobSnapshot(TypedDict, total=False):
    """워치독 평가 입력 — Harness가 SchedulerState에서 채워 주입."""

    job_name: str
    is_enabled: bool
    expected_interval_sec: float  # cron→CronTrigger 2회 발화 diff로 산출(Harness)
    last_run_at: Optional[datetime]  # 마지막 '성공' 시각(리스너가 EXECUTED에만 갱신)
    last_status: Optional[str]  # 'ok' | 'error' | 'missed' | None
    last_status_at: Optional[datetime]  # 마지막 상태 전이 시각(미사용·참고)
    created_at: Optional[datetime]  # 잡 등록 시각(never_succeeded 유예 판정용)


class JobVerdict(TypedDict):
    job_name: str
    state: str
    age_sec: Optional[float]  # now - last_run_at (없으면 None)
    reason: str


def evaluate_job(job: JobSnapshot, now: datetime) -> JobVerdict:
    """잡 1건의 상태를 판정한다. 우선순위: disabled > failed > never_succeeded > stale > ok."""
    name = job.get("job_name", "?")
    last_run = job.get("last_run_at")
    age = (now - last_run).total_seconds() if last_run is not None else None
    interval = float(job.get("expected_interval_sec") or 0)

    # 1) 비활성 잡 — 감시 대상 아님(노이즈 제외). 다른 상태보다 우선.
    if not job.get("is_enabled", True):
        return _verdict(name, STATE_DISABLED, age, "비활성 잡(감시 제외)")

    # 2) 마지막 상태가 에러/미스파이어 — 명시적 실패. stale보다 우선(원인이 더 구체적).
    status = job.get("last_status")
    if status in ("error", "missed"):
        return _verdict(name, STATE_FAILED, age, f"마지막 상태={status}")

    # 3) 한 번도 성공한 적 없음 — 단, 갓 등록된 잡의 첫 주기는 유예(헛알림 방지).
    if last_run is None:
        created = job.get("created_at")
        job_age = (now - created).total_seconds() if created is not None else None
        if created is not None and interval > 0 and job_age is not None and job_age > interval:
            return _verdict(
                name, STATE_NEVER_SUCCEEDED, None,
                f"성공 기록 없음(등록 {int(job_age)}s 전, 주기 {int(interval)}s 초과)",
            )
        # 등록 직후(유예) 또는 등록시각/주기 불명 → 보수적으로 ok(헛알림 방지).
        return _verdict(name, STATE_OK, None, "성공 기록 없음(첫 주기 유예 또는 정보 부족)")

    # 4) 마지막 성공이 기대 주기×배수를 넘김 — stale.
    if interval > 0 and age is not None and age > interval * STALE_MULTIPLIER:
        return _verdict(
            name, STATE_STALE, age,
            f"마지막 성공 {int(age)}s 전 (> {STALE_MULTIPLIER}×주기 {int(interval)}s)",
        )

    # 5) 정상.
    return _verdict(name, STATE_OK, age, "정상")


def evaluate_staleness(jobs: Sequence[JobSnapshot], now: datetime) -> list[JobVerdict]:
    """잡 스냅샷 목록을 일괄 판정한다."""
    return [evaluate_job(job, now) for job in jobs]


# ── 쿠키 freshness (SA, 순수) ─────────────────────────────────────────────
# fail-soft 잡(RG 정산·광고 등)은 쿠키 만료를 에러로 안 띄우고 조용히 넘어가 워치독이 'ok'로 오판한다
# (2026-06-10 RG 정산 11일 동결 사고). 그래서 잡 상태와 별개로 '쿠키가 며칠째 성공 못 했나'를 직접 본다.
COOKIE_STALE_DAYS = 3.0  # 마지막 성공 후 이 일수를 넘기면 stale. 세션쿠키 단명 깜빡임은 관용, 지속 실패만.


class CookieSnapshot(TypedDict, total=False):
    account_key: str
    status: Optional[str]  # green | red | unknown
    last_success_at: Optional[datetime]


class CookieVerdict(TypedDict):
    account_key: str
    state: str  # 'stale'
    age_days: Optional[float]
    status: Optional[str]
    reason: str


def evaluate_cookie_freshness(
    cookies: Sequence[CookieSnapshot], now: datetime, stale_days: float = COOKIE_STALE_DAYS
) -> list[CookieVerdict]:
    """마지막 성공이 stale_days를 넘긴 쿠키만 반환(=비정상). 한 번도 성공 못 한 쿠키는 제외.

    한 번도 성공 못 한 쿠키(last_success_at=None)는 미설정/미사용으로 보고 노이즈 제외 —
    '쓰던 게 멈춘 것'만 잡는다(allowlist 불필요, 자동 스코프). 현재 status(red/green)의
    단명 깜빡임에 의존하지 않고 '며칠째 성공 못 함'으로 지속 실패를 판정한다.
    """
    out: list[CookieVerdict] = []
    now_n = _to_naive(now)  # aware/naive 혼재 시 TypeError 방어(codex P1) — 워치독은 안 죽어야 함.
    for c in cookies:
        ls = _to_naive(c.get("last_success_at"))
        if ls is None:
            continue
        age_days = (now_n - ls).total_seconds() / 86400.0
        if age_days > stale_days:
            out.append({
                "account_key": c.get("account_key", "?"),
                "state": "stale",
                "age_days": age_days,
                "status": c.get("status"),
                "reason": f"마지막 성공 {age_days:.1f}일 전 (> {int(stale_days)}일)",
            })
    return out


def _verdict(name: str, state: str, age: Optional[float], reason: str) -> JobVerdict:
    return {"job_name": name, "state": state, "age_sec": age, "reason": reason}


def _to_naive(dt: Optional[datetime]) -> Optional[datetime]:
    """tzinfo 있으면 제거(시스템 관례=naive KST). aware/naive 혼재 빼기 크래시 방어."""
    if dt is not None and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt
