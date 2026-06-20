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


def _verdict(name: str, state: str, age: Optional[float], reason: str) -> JobVerdict:
    return {"job_name": name, "state": state, "age_sec": age, "reason": reason}
