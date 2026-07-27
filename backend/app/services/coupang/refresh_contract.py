# refresh_contract.py — 쿠팡 브라우저 수집 5스트림 공용 "갱신 요청" 계약(SA, 단일 책임).
#   버튼 1회 = 성공하거나 3회 시도할 때까지 살아있는 요청. lease(임대) 방식으로 구현한다.
#
# ★왜 필요했나(2026-07-17 13:02 실사고, coupang_ops.py 주석 4곳):
#   기존 claim은 요청 플래그를 **즉시 소비(clear)**했다. 페처가 claim 직후 브라우저 에러로
#   죽으면 요청은 이미 사라져 아무도 다시 시도하지 않았다 — 버튼 1회 = 시도 1회, 실패하면 끝.
#   사람이 실패를 눈치채고 다시 눌러야 했고, 실패 자체도 last_error로만 보였다.
#
# ★계약(D-NAO PLAN_coupang-claim-retry-lease §0, Jino 확정 2026-07-27):
#   - 버튼 1회의 의도는 **재시도 3회까지 포함**한다.
#   - 단 **로그인 필요 실패는 재시도 제외** — 재시도해도 실패하고 창만 반복해서 뜬다.
#
# 상태 전이(state 행 = CoupangWingCookie[account_key], 스트림마다 account_key만 다름):
#   ① request  : refresh_requested_at=now (이미 pending이면 no-op — 시도 횟수를 되돌리지 않는다)
#   ② claim    : requested ∧ (claimed_at IS NULL ∨ now-claimed_at > TTL)
#                → claimed_at=now, attempt_count+=1 (원자적 UPDATE). ★요청 플래그는 지우지 않는다.
#   ③ success  : refresh_requested_at=NULL, claimed_at=NULL, attempt_count=0 (요청 소멸)
#   ④ failure  : lease 반납(claimed_at=NULL) → 다음 폴에서 자동 재claim(=재시도)
#                단 kind=login_required ∨ attempt_count>=3 → 요청 소멸 + last_error에 사유 명시
#   ⑤ TTL      : 데몬이 보고 없이 죽는 경우의 안전망(기본 20분 — 실측 근거는 _LEASE_TTL_MIN 주석)
#
# ⚠️시각은 전부 kst_now() naive KST. SQLite server_default=func.now()는 UTC라 섞으면 9시간
#   어긋난다(LESSONS: sqlite-server-default-now-is-utc) — 이 파일은 DB 함수 시각을 쓰지 않는다.
from __future__ import annotations

import logging
import os
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import CoupangWingCookie
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 재시도 상한 — 버튼 1회가 만드는 최대 시도 횟수(claim 성공 횟수 기준).
MAX_ATTEMPTS = 3

# 재시도 제외 사유(페처가 fetch-error 보고 시 kind로 전달). 로그인 필요는 재시도해도
# 실패하고 창만 반복해서 뜬다(§0 금지선) → 즉시 요청 소멸 + 사람에게 안내.
KIND_LOGIN_REQUIRED = "login_required"

# lease TTL(분) — "데몬이 보고도 못 하고 죽었다"고 간주하기까지의 시간.
# ★실측 근거(2026-07-27, Mac 페처 로그 ~/.ohisell_*_fetcher.log의 claim→활동종료 구간):
#     ad_cost   run=227회  median 22s  p90 76s  max 684s(11.4분, 2026-06-17 로그인 대기 포함)
#     wing      run=300회  median  5s  p90 69s  max 246s
#     rocket    run=  7회  median 74s  p90 189s max 189s
#   코드상 이론 상한도 같은 자리수다(로그인 대기 180s + 옵션보고서 생성 폴 300s + push).
#   → 실측 최장 684s의 약 1.75배인 20분을 기본값으로 둔다. 짧으면 아직 살아 일하는 페처
#     위로 2차 claim이 겹쳐 창이 두 번 뜬다(이중 기동). 길어도 손해는 "재시도가 늦어짐"뿐이다.
_LEASE_TTL_MIN = max(1, int(os.getenv("COUPANG_REFRESH_LEASE_TTL_MIN", "20")))


def lease_ttl_minutes() -> int:
    """현재 TTL(분). 테스트·상태표시가 상수 대신 이 함수를 쓰면 env 오버라이드까지 반영된다."""
    return _LEASE_TTL_MIN


def _row(db: Session, account_key: str) -> CoupangWingCookie | None:
    return (
        db.query(CoupangWingCookie)
        .filter(CoupangWingCookie.account_key == account_key)
        .first()
    )


def _ensure_row(db: Session, account_key: str) -> CoupangWingCookie:
    row = _row(db, account_key)
    if row is None:
        row = CoupangWingCookie(account_key=account_key)
        db.add(row)
        db.flush()
    return row


# ════════════════════════════════════════════════
# ① request — 버튼
# ════════════════════════════════════════════════
def request_refresh(db: Session, account_key: str) -> dict:
    """UI 갱신 버튼 → 요청 플래그 set. 이미 pending이면 no-op(요청 시각·시도 횟수 보존).

    ★이미 pending인데 덮어쓰면: 진행 중인 재시도의 attempt_count가 리셋돼 상한(3회)이
    무한정 늘어난다(연타=무한 재시도). 새 요청은 '요청이 없을 때'만 시작된다.
    """
    row = _ensure_row(db, account_key)
    if row.refresh_requested_at is not None:
        db.commit()
        return {
            "requested": True,
            "requested_at": row.refresh_requested_at.isoformat(),
            "already_pending": True,
            "attempt_count": int(row.attempt_count or 0),
        }
    row.refresh_requested_at = kst_now()
    row.claimed_at = None       # 지난 회차의 유령 lease가 남아 첫 claim을 막지 않게
    row.attempt_count = 0       # 새 버튼 = 새 예산(3회)
    db.commit()
    return {
        "requested": True,
        "requested_at": row.refresh_requested_at.isoformat(),
        "already_pending": False,
        "attempt_count": 0,
    }


# ════════════════════════════════════════════════
# ② claim — 페처가 작업 시작(lease 취득)
# ════════════════════════════════════════════════
def claim_refresh(db: Session, account_key: str) -> dict:
    """페처가 요청을 **임대**한다(플래그는 보존). 원자적 조건부 UPDATE.

    조건: 요청이 살아있고 ∧ (임대 없음 ∨ 임대 만료(TTL 초과)).
    데몬이 둘 이상 동시에 돌아도 rowcount=1인 한쪽만 claimed=True가 된다(기존 codex P2 성질 유지).
    반환 attempt=이번 시도가 몇 번째인지(1-based). claimed=False면 attempt는 현재 값 그대로.
    """
    now = kst_now()
    cutoff = now - timedelta(minutes=_LEASE_TTL_MIN)
    res = db.execute(
        update(CoupangWingCookie)
        .where(CoupangWingCookie.account_key == account_key)
        .where(CoupangWingCookie.refresh_requested_at.isnot(None))
        .where(
            (CoupangWingCookie.claimed_at.is_(None))
            | (CoupangWingCookie.claimed_at < cutoff)
        )
        .values(
            claimed_at=now,
            attempt_count=CoupangWingCookie.attempt_count + 1,
        )
    )
    db.commit()
    claimed = (res.rowcount or 0) > 0
    row = _row(db, account_key)
    if row is not None:
        db.refresh(row)
    attempt = int(row.attempt_count or 0) if row is not None else 0
    if claimed:
        log.info("refresh claim: %s attempt=%d/%d", account_key, attempt, MAX_ATTEMPTS)
    return {"claimed": claimed, "attempt": attempt, "max_attempts": MAX_ATTEMPTS}


# ════════════════════════════════════════════════
# ③ success — 여기서만 요청이 소멸한다
# ════════════════════════════════════════════════
def mark_success(db: Session, account_key: str) -> None:
    """수집 성공 → 요청 소멸 + lease 해제 + 시도 카운터 리셋.

    ★요청이 사라지는 정상 경로는 여기 하나뿐이다(claim은 이제 소비하지 않는다).
    commit은 호출자 쪽 heartbeat와 함께 일어나도 무해하도록 여기서도 한다.
    """
    row = _row(db, account_key)
    if row is None:
        return
    row.refresh_requested_at = None
    row.claimed_at = None
    row.attempt_count = 0
    db.commit()


# ════════════════════════════════════════════════
# ④ failure — lease 반납(=재시도) 또는 요청 소멸
# ════════════════════════════════════════════════
def report_failure(
    db: Session,
    account_key: str,
    error: str,
    kind: str | None = None,
) -> dict:
    """페처 run 실패 보고. 재시도 여부를 계약대로 판정한다.

    - kind="login_required" → 재시도 없음(창만 반복해서 뜸, §0 금지선). 요청 소멸.
    - attempt_count >= MAX_ATTEMPTS → 재시도 예산 소진. 요청 소멸.
    - 그 외 → claimed_at=None으로 lease만 반납 → 다음 폴에서 페처가 자동 재claim(=재시도).

    반환 {"retry": bool, "attempt": n, "reason": str|None}. reason은 소멸 사유(재시도면 None).
    ★last_error/last_error_at은 어느 경우든 기록한다 — 재시도 중에도 무슨 일이 있었는지 남긴다.
    ★status(쿠키 상태)는 건드리지 않는다(PR #30 codex 1R[P2]): red면 배너가 "쿠키 재설정"을
      시키는데 브라우저 크래시엔 헛수고다.
    """
    row = _row(db, account_key)
    if row is None:
        return {"retry": False, "attempt": 0, "reason": None}

    attempt = int(row.attempt_count or 0)
    reason: str | None = None
    if kind == KIND_LOGIN_REQUIRED:
        reason = "로그인 필요 — 재시도 안 함(로그인 후 다시 갱신을 눌러주세요)"
    elif attempt >= MAX_ATTEMPTS:
        reason = f"재시도 {MAX_ATTEMPTS}회 소진"

    message = f"{error} [{reason}]" if reason else error
    row.last_error = message[:300]  # 컬럼 한계 — 긴 스택트레이스로 보고 자체가 날아가면 안 된다
    row.last_error_at = kst_now()

    if reason is not None:
        row.refresh_requested_at = None   # 요청 소멸(더 이상 재시도하지 않는다)
        row.claimed_at = None
        # attempt_count는 남긴다(진단용). 다음 버튼(request_refresh)이 0으로 리셋한다.
        db.commit()
        log.warning("refresh 요청 소멸: %s attempt=%d 사유=%s", account_key, attempt, reason)
        return {"retry": False, "attempt": attempt, "reason": reason}

    row.claimed_at = None  # lease 반납 → 다음 폴에서 재claim
    db.commit()
    log.info("refresh 재시도 예약: %s attempt=%d/%d", account_key, attempt, MAX_ATTEMPTS)
    return {"retry": True, "attempt": attempt, "reason": None}


# ════════════════════════════════════════════════
# 상태 조회 보조 — 기존 refresh_status에 얹는 추가 필드(하위호환: 필드 추가만)
# ════════════════════════════════════════════════
def status_fields(row: CoupangWingCookie | None) -> dict:
    """refresh_status 응답에 덧붙일 계약 필드. 프론트는 무시해도 되고, 진단엔 필수.

    in_flight = 지금 페처가 임대 중(살아있는 lease). requested가 true여도 in_flight가
    false면 "재시도 대기 중"이다.
    """
    if row is None:
        return {"attempt_count": 0, "max_attempts": MAX_ATTEMPTS,
                "claimed_at": None, "in_flight": False}
    claimed_at = row.claimed_at
    in_flight = bool(
        claimed_at is not None
        and row.refresh_requested_at is not None
        and (kst_now() - claimed_at) <= timedelta(minutes=_LEASE_TTL_MIN)
    )
    return {
        "attempt_count": int(row.attempt_count or 0),
        "max_attempts": MAX_ATTEMPTS,
        "claimed_at": claimed_at.isoformat() if claimed_at else None,
        "in_flight": in_flight,
    }
