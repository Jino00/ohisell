# 이 파일은 스케줄러 잡의 stale/실패 상태를 판정하는 순수 함수다 (S5b 워치독 SA②, I/O 없음).
# 입력으로 받은 잡 상태 스냅샷과 기준 시각(now)만으로 5-state를 산출한다. DB/네트워크 미접촉
# → 단위 테스트로 모든 경계를 검증 가능(원칙 22). Harness(scheduler_health, S4)가 SchedulerState를
# 읽어 이 함수에 주입하고, 결과를 /api/scheduler/health로 표면화한다.
from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Sequence, TypedDict, Union

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


# ── 데이터 나이 freshness (SA, 순수) ──────────────────────────────────────
# 잡·쿠키의 자기보고는 거짓말할 수 있다(2026-07-17 사고: RG 정산이 26일 침묵했는데 last_status='ok').
# 데이터 나이는 거짓말 못 한다 — '가장 최신 데이터가 며칠 전 것인가'는 파이프라인이 정말 살아있는지의
# 직접 증거다. 쿠키·잡 감시가 놓치는(또는 층1 경로 이관 후 쿠키 행 자체가 사라지는) 미지 침묵 경로까지
# 커버한다. cookies_stale과 달리 latest=None(한 번도 없음)도 즉시 비정상으로 시끄럽게 잡는다.
class DataSnapshot(TypedDict, total=False):
    name: str
    account_key: str
    latest: Optional[Union[datetime, date]]  # 최신 데이터 시각(SQLAlchemy Date면 date 객체)
    max_age_days: float
    impact: str  # 돈 영향 한글 라벨


class DataVerdict(TypedDict):
    name: str
    account_key: str
    state: str  # 'no_data' | 'stale' | 'check_failed'
    age_days: Optional[float]  # no_data면 None
    max_age_days: float
    impact: str
    reason: str


def evaluate_data_freshness(
    snapshots: Sequence[DataSnapshot], now: datetime
) -> list[DataVerdict]:
    """데이터 나이가 max_age_days를 넘겼거나(latest=stale) 아예 없는(no_data) 스냅샷만 반환(=비정상).

    정상(나이 ≤ max_age_days)이면 결과에서 제외. cookies_stale과 달리 latest=None도 즉시 비정상
    (no_data) — 파이프라인이 '한 번도 데이터를 못 만든' 것도 시끄럽게 잡는다(이번 사고 재발 방지).
    """
    out: list[DataVerdict] = []
    now_n = _to_naive(now)  # aware/naive 혼재 시 TypeError 방어(cookie_freshness와 동형).
    for s in snapshots:
        name = s.get("name", "?")
        account_key = s.get("account_key", "")
        max_age = float(s.get("max_age_days") or 0)
        impact = s.get("impact", "")

        # ★«이 규칙을 볼 수 없었다»는 «이상 없음»이 아니다 (적대 리뷰 P1-2, 2026-08-10).
        #   종전엔 Harness의 try/except가 **for 루프 전체**를 감싸 규칙 하나가 예외를 내면
        #   나머지까지 통째로 사라졌고(`data_stale: []`), 그건 정상과 구별되지 않았다.
        #   실제 도달 경로: 마이그레이션 전에 코드가 배포되면 새 테이블이 없어 RG 정산 감시까지
        #   함께 침묵한다 — 13일 침묵을 막으려는 변경이 같은 실패 모드를 새 트리거로 되살린다.
        #   이제 실패한 규칙만 `check_failed`로 여기 남는다. 별도 필드를 만들지 않는 이유:
        #   `data_stale`는 이미 healthy를 깨고 배너가 impact를 렌더한다 — 새 필드를 만들면
        #   표시 분기를 또 붙여야 하고, 그걸 빼먹는 것이 이 리포의 반복 실패다(교훈 #223).
        if s.get("error"):
            out.append({
                "name": name,
                "account_key": account_key,
                "state": "check_failed",
                "age_days": None,
                "max_age_days": max_age,
                "impact": impact or "이 파이프라인의 신선도를 확인할 수 없다",
                "reason": f"신선도 조회 실패: {str(s['error'])[:120]}",
            })
            continue

        latest_n = _to_naive(_date_to_datetime(s.get("latest")))

        if latest_n is None:
            out.append({
                "name": name,
                "account_key": account_key,
                "state": "no_data",
                "age_days": None,
                "max_age_days": max_age,
                "impact": impact,
                "reason": "데이터 없음(한 번도 수집된 적 없음)",
            })
            continue

        age_days = (now_n - latest_n).total_seconds() / 86400.0
        if age_days > max_age:
            out.append({
                "name": name,
                "account_key": account_key,
                "state": "stale",
                "age_days": age_days,
                "max_age_days": max_age,
                "impact": impact,
                "reason": f"최신 데이터 {age_days:.1f}일 전 (> {max_age:.0f}일)",
            })
    return out


# ── 디스크 여유 (SA, 순수) ────────────────────────────────────────────────
# 왜 잡·쿠키·데이터 나이 위에 또 디스크인가: 2026-08-03 03:32 KST에 디스크가 꽉 차(ENOSPC)
# **서버에서 쓰기가 필요한 모든 프로세스가 3시간 40분 마비**됐고, 05:20~07:00 예정 자동수집
# 12개가 통째로 유실됐다. 그때 세 감시선 중 어느 것도 사전에 울리지 않았다 —
#   · 잡 감시: 잡이 '실패'한 게 아니라 프로세스가 없어서 '발화 자체를 안 했다'
#   · 데이터 나이: 사후 지표라, 이미 유실된 뒤에야 뜬다(실제로 다음 날에야 stale로 떴다)
# 디스크는 **유일하게 사전에 보이는 신호**다. 꽉 차기 전에 울려야 의미가 있으므로 임계를
# 넉넉히 앞당긴다(85%). 잡·쿠키·데이터가 전부 '이미 죽은 뒤'를 보는 것과 대비된다.
#
# 임계 85% 근거: 이 서버의 일일 DB 백업이 원본 크기(~1.5GB)를 한 번에 쓴다. 97GB 디스크에서
# 15% = 14.5GB → 백업 1회분의 약 9배 여유. 하루 1GB 안팎으로 증가하던 실측 추세에서
# 대응까지 열흘 이상 확보된다(사고 당시엔 92%에서 시작해 하루 만에 터졌다 — 92%는 늦다).
DISK_WARN_PERCENT = 85.0


class DiskSnapshot(TypedDict, total=False):
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    warn_percent: float
    impact: str


class DiskVerdict(TypedDict):
    path: str
    state: str  # 'low'
    used_percent: float
    warn_percent: float
    free_bytes: int
    total_bytes: int
    impact: str
    reason: str


def evaluate_disk_space(snapshots: Sequence[DiskSnapshot]) -> list[DiskVerdict]:
    """사용률이 warn_percent를 넘긴 마운트만 반환(=비정상). 정상이면 결과에서 제외.

    total_bytes가 0/누락이면 판정 불가 → **조용히 건너뛴다**(헛알림 방지). 다른 SA들과 달리
    'no_data'를 만들지 않는 이유: 디스크 조회 실패는 파이프라인 침묵의 증거가 아니라 단순
    조회 실패이고, 이 감시선 자체가 보조선이라 시끄럽게 만들 이득이 없다.
    """
    out: list[DiskVerdict] = []
    for s in snapshots:
        total = int(s.get("total_bytes") or 0)
        if total <= 0:
            continue
        used = int(s.get("used_bytes") or 0)
        free = int(s.get("free_bytes") or 0)
        warn = float(s.get("warn_percent") or DISK_WARN_PERCENT)
        used_pct = used / total * 100.0
        if used_pct > warn:
            out.append({
                "path": s.get("path", "/"),
                "state": "low",
                "used_percent": used_pct,
                "warn_percent": warn,
                "free_bytes": free,
                "total_bytes": total,
                "impact": s.get("impact", ""),
                "reason": (
                    f"디스크 사용률 {used_pct:.1f}% (> {warn:.0f}%), "
                    f"여유 {free / 1073741824:.1f}GB"
                ),
            })
    return out


def _verdict(name: str, state: str, age: Optional[float], reason: str) -> JobVerdict:
    return {"job_name": name, "state": state, "age_sec": age, "reason": reason}


def _date_to_datetime(value: Optional[Union[datetime, date]]) -> Optional[datetime]:
    """SQLAlchemy Date 컬럼은 date 객체를 준다 → 자정 datetime으로 변환(naive 비교용).

    datetime은 그대로, date는 자정으로, None은 None. date/datetime 둘 다 방어적으로 받는다.
    (datetime이 date의 서브클래스이므로 datetime 판정을 먼저 한다.)
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return None


def _to_naive(dt: Optional[datetime]) -> Optional[datetime]:
    """tzinfo 있으면 제거(시스템 관례=naive KST). aware/naive 혼재 빼기 크래시 방어."""
    if dt is not None and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt
