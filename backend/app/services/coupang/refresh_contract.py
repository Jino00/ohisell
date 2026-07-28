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
from datetime import datetime, timedelta

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

# ★access_denied(2026-07-28, 트랙 coupang-promo-pnl 적대적 리뷰 3R) — 구독/권한 만료처럼
#   **영구적인** 접근 차단. 재시도해도 40초 뒤에 되살아나지 않는다(판매분석 무료체험 종료
#   2026-08-20이 눈앞). login_required와 같은 이유로 재시도에서 뺀다: 안 그러면 갱신 버튼
#   1회가 Chrome을 3번 띄우고 매번 같은 곳에서 죽으며, 그 사이 발주/정산은 계속 성공한다.
#   ★단 실패로는 남는다(D-CPP-5 "조용한 skip 금지") — 요청만 소멸시키고 last_error에 사유를 적는다.
#   일시적/영구적을 가르는 이 저장소의 기존 계약과 같은 결(ingest_coupon_used_amount의
#   not_found=일시적 / wrong_kind=영구적 분리).
KIND_ACCESS_DENIED = "access_denied"

# ★mapping_broken(4R) — 수집기 매핑이 깨져 응답은 오는데 레코드가 0인 상태. access_denied와
#   **재시도 정책은 같지만(0회) 처방이 정반대**다: 이쪽은 결제가 아니라 코드를 고쳐야 한다.
#   한 kind로 뭉치면 운영자가 멀쩡한 구독을 갱신하며 코드 버그를 쫓는다.
KIND_MAPPING_BROKEN = "mapping_broken"

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
def _lease_of(row: CoupangWingCookie | None) -> str | None:
    """현재 임대 식별자(=claimed_at ISO). 페처가 claim 응답으로 받아 실패 보고에 되돌려준다."""
    if row is None or row.claimed_at is None:
        return None
    return row.claimed_at.isoformat()


def claim_refresh(db: Session, account_key: str, *,
                  settle_on_success_heartbeat: bool = True) -> dict:
    """페처가 요청을 **임대**한다(플래그는 보존). 원자적 조건부 UPDATE.

    조건: 요청이 살아있고 ∧ 시도 예산이 남았고(attempt_count < MAX) ∧ (임대 없음 ∨ 임대 만료).
    데몬이 둘 이상 동시에 돌아도 rowcount=1인 한쪽만 claimed=True가 된다(기존 codex P2 성질 유지).

    ★attempt_count 조건이 필요한 이유(codex 1R[P1]): 데몬이 **보고 없이 죽는** 경로에서는
    report_failure를 안 거치므로 TTL 만료만으로 계속 재claim된다 — 상한이 무력해지고 Chrome이
    영원히 반복해서 뜬다. 예산을 다 쓴 요청은 여기서 소멸시킨다(폴이 곧 reaper 역할).

    settle_on_success_heartbeat(codex 4R[P1]): "요청 이후 성공 heartbeat가 있고 임대가 만료된 채
    방치" = 자동 완료. **한 회차가 산출물 하나인 스트림에서만 참**이다. RG 정산처럼 한 회차가
    여러 엑셀을 올리는 스트림은 첫 엑셀 heartbeat를 완주로 오인하므로 False로 끈다(그 대신
    run 종료 신호 refresh-complete를 쓴다).

    반환 attempt=이번 시도가 몇 번째인지(1-based), lease=이번 임대 식별자(실패 보고에 되돌려줌).
    """
    now = kst_now()
    cutoff = now - timedelta(minutes=_LEASE_TTL_MIN)
    pre = _row(db, account_key)
    if settle_on_success_heartbeat and pre is not None and _settle_if_satisfied(db, pre, cutoff):
        return {"claimed": False, "attempt": int(pre.attempt_count or 0),
                "max_attempts": MAX_ATTEMPTS, "lease": None}
    res = db.execute(
        update(CoupangWingCookie)
        .where(CoupangWingCookie.account_key == account_key)
        .where(CoupangWingCookie.refresh_requested_at.isnot(None))
        .where(CoupangWingCookie.attempt_count < MAX_ATTEMPTS)
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
    elif row is not None:
        _reap_exhausted(db, row, now, cutoff)
    return {
        "claimed": claimed,
        "attempt": attempt,
        "max_attempts": MAX_ATTEMPTS,
        "lease": _lease_of(row) if claimed else None,
    }


def _settle_if_satisfied(db: Session, row: CoupangWingCookie, cutoff) -> bool:
    """"이미 데이터가 들어온" 요청을 창을 열기 전에 조용히 닫는다. 닫았으면 True.

    ★왜(codex 3R[P1]): 성공 신호를 못 보내는 페처(예: 백엔드만 먼저 배포돼 아직 refresh-complete를
    모르는 구버전, 또는 업로드까지 마치고 죽은 run)의 요청이 남아 재시도를 유발한다 — 데이터는
    이미 들어왔는데 창이 두세 번 더 뜨고 끝내 "3회 소진"이라는 거짓 실패로 끝난다.
    판정: 요청 이후에 성공 heartbeat(last_success_at)가 찍혔고 ∧ **임대가 만료된 채로 방치**됨.
    - 임대가 살아 있으면 손대지 않는다 — 여러 파일을 올리는 run의 중간 업로드를 완료로 오인 금지.
    - 임대가 **반납된**(claimed_at IS NULL) 경우도 손대지 않는다 — 그건 페처가 실패를 명시적으로
      보고해 재시도를 기다리는 상태다(부분 성공 후 뒷단 실패 = 재시도해야 정산이 완전해진다).
    즉 "아무도 20분간 아무 말이 없는데 데이터는 들어와 있다"일 때만 닫는다.
    """
    if row.refresh_requested_at is None or row.last_success_at is None:
        return False
    if row.claimed_at is None or row.claimed_at >= cutoff:
        return False  # 아직 일하는 중이거나, 실패 보고로 반납돼 재시도 대기 중
    if row.last_success_at < row.refresh_requested_at:
        return False  # 이번 요청보다 오래된 성공 — 무관
    # 조건부 UPDATE(codex 4R[P2]): 폴이 여럿이면 A가 닫은 뒤 사용자가 새 버튼을 눌렀는데
    # B의 낡은 행이 뒤늦게 커밋되며 그 새 요청을 지울 수 있다. 관측한 (요청시각·임대·시도횟수)와
    # 일치할 때만 쓴다.
    res = db.execute(
        update(CoupangWingCookie)
        .where(CoupangWingCookie.account_key == row.account_key)
        .where(CoupangWingCookie.refresh_requested_at == row.refresh_requested_at)
        .where(CoupangWingCookie.claimed_at == row.claimed_at)
        .where(CoupangWingCookie.attempt_count == row.attempt_count)
        .values(refresh_requested_at=None, claimed_at=None, attempt_count=0)
    )
    db.commit()
    if (res.rowcount or 0) == 0:
        return False
    log.info("refresh 요청 자동 완료(성공 heartbeat 확인): %s", row.account_key)
    return True


def _reap_exhausted(
    db: Session, row: CoupangWingCookie, now, cutoff
) -> None:
    """예산을 다 쓰고 마지막 시도가 보고 없이 사라진 요청을 소멸시킨다(claim 경로의 reaper).

    조건: 요청 살아있음 ∧ attempt_count>=MAX ∧ 임대가 없거나 만료됨(=일하는 중이 아님).
    이게 없으면 요청이 영원히 requested=true로 남아 UI가 "진행 중"을 무한히 표시한다.

    ★조건부 UPDATE로 쓴다(codex 3R[P2]): 폴이 여럿이면 A가 수확한 뒤 사용자가 새 버튼을 눌러
    새 요청이 생겼는데 B의 낡은 ORM 행이 뒤늦게 커밋되며 그 새 요청을 지울 수 있다.
    소진 당시의 (요청시각·시도횟수·임대)와 일치할 때만 쓴다.
    """
    if row.refresh_requested_at is None:
        return
    if int(row.attempt_count or 0) < MAX_ATTEMPTS:
        return
    if row.claimed_at is not None and row.claimed_at >= cutoff:
        return  # 마지막 시도가 아직 살아 일하는 중 — 기다린다
    stmt = (
        update(CoupangWingCookie)
        .where(CoupangWingCookie.account_key == row.account_key)
        .where(CoupangWingCookie.refresh_requested_at == row.refresh_requested_at)
        .where(CoupangWingCookie.attempt_count == row.attempt_count)
    )
    if row.claimed_at is None:
        stmt = stmt.where(CoupangWingCookie.claimed_at.is_(None))
    else:
        stmt = stmt.where(CoupangWingCookie.claimed_at == row.claimed_at)
    res = db.execute(stmt.values(
        refresh_requested_at=None,
        claimed_at=None,
        last_error=(f"재시도 {MAX_ATTEMPTS}회 소진 — 마지막 시도가 보고 없이 종료(응답 없음)")[:300],
        last_error_at=now,
    ))
    db.commit()
    if (res.rowcount or 0) > 0:
        log.warning("refresh 요청 소멸(reaper): %s attempt=%d", row.account_key, row.attempt_count)


# ════════════════════════════════════════════════
# ③ success — 여기서만 요청이 소멸한다
# ════════════════════════════════════════════════
def mark_success(db: Session, account_key: str, *, clear_error: bool = False,
                 lease: str | None = None) -> bool:
    """수집 성공 → 요청 소멸 + lease 해제 + 시도 카운터 리셋.

    ★요청이 사라지는 정상 경로는 여기 하나뿐이다(claim은 이제 소비하지 않는다).
    commit은 호출자 쪽 heartbeat와 함께 일어나도 무해하도록 여기서도 한다.

    clear_error(codex 2R[P2]): 지난 시도의 실패 흔적까지 지운다. 데이터 heartbeat를 동반하지
    않는 성공(=받을 게 없어 정상 종료한 회차)에서 필요하다 — 안 지우면 last_error_at만
    바뀐 상태로 요청이 사라져 UI가 성공한 회차를 '실패'로 읽는다. heartbeat를 찍는 경로는
    호출자가 이미 지우므로 기본값 False.
    """
    row = _row(db, account_key)
    if row is None:
        return False
    values: dict = {"refresh_requested_at": None, "claimed_at": None, "attempt_count": 0}
    if clear_error:
        values["last_error"] = None
        values["last_error_at"] = None

    if lease is None:
        # 레거시 경로(데이터 ingest가 알리는 성공) — 조건 없이 닫는다.
        for k, v in values.items():
            setattr(row, k, v)
        db.commit()
        return True

    # ★stale 완료 신호 무시(codex 3R[P2]) + 검사·쓰기 원자화(codex 5R[P2]):
    # 임대를 넘긴 옛 run이 뒤늦게 "끝났다"고 말하면 지금 일하는 run의 임대나 사용자의 새
    # 요청을 지운다. SELECT로 확인하고 따로 쓰면 그 사이에 임대가 바뀔 수 있으므로,
    # 관측한 claimed_at과 일치할 때만 쓰는 조건부 UPDATE로 처리하고 rowcount로 판정한다.
    try:
        lease_dt = datetime.fromisoformat(lease)
    except (TypeError, ValueError):
        log.info("완료 신호의 lease 형식 오류 — 무시: %s (%s)", account_key, lease)
        return False
    res = db.execute(
        update(CoupangWingCookie)
        .where(CoupangWingCookie.account_key == account_key)
        .where(CoupangWingCookie.claimed_at == lease_dt)
        .values(**values)
    )
    db.commit()
    if (res.rowcount or 0) == 0:
        log.info("stale 완료 신호 무시: %s (보고 lease=%s, 현재=%s)",
                 account_key, lease, _lease_of(_row(db, account_key)))
        return False
    return True


# ════════════════════════════════════════════════
# ④ failure — lease 반납(=재시도) 또는 요청 소멸
# ════════════════════════════════════════════════
def report_failure(
    db: Session,
    account_key: str,
    error: str,
    kind: str | None = None,
    lease: str | None = None,
) -> dict:
    """페처 run 실패 보고. 재시도 여부를 계약대로 판정한다.

    - kind="login_required" → 재시도 없음(창만 반복해서 뜸, §0 금지선). 요청 소멸.
    - kind="access_denied" → 재시도 없음(구독/권한 만료는 영구적). 요청 소멸.
    - kind="mapping_broken" → 재시도 없음(수집기 매핑 파손). 요청 소멸. 처방은 코드 수정.
    - attempt_count >= MAX_ATTEMPTS → 재시도 예산 소진. 요청 소멸.
    - 그 외 → claimed_at=None으로 lease만 반납 → 다음 폴에서 페처가 자동 재claim(=재시도).

    ★lease(옵션, codex 1R[P1]): claim 응답으로 받은 임대 식별자. 지금 유효한 임대와 다르면
    **stale 보고**로 보고 무시한다 — 20분 넘게 멈춰 있던 옛 시도가 뒤늦게 깨어나 (a)남의
    임대를 반납해 창을 두 번 띄우거나 (b)이미 성공한 요청 위에 실패 흔적을 남기는 것을 막는다.
    미전달(구버전 페처)이면 기존대로 동작(하위호환).

    반환 {"retry": bool, "attempt": n, "reason": str|None}. reason은 소멸 사유(재시도면 None).
    ★last_error/last_error_at은 어느 경우든 기록한다 — 재시도 중에도 무슨 일이 있었는지 남긴다.
    ★status(쿠키 상태)는 건드리지 않는다(PR #30 codex 1R[P2]): red면 배너가 "쿠키 재설정"을
      시키는데 브라우저 크래시엔 헛수고다.
    """
    row = _row(db, account_key)
    if row is None:
        return {"retry": False, "attempt": 0, "reason": None}

    lease_dt = None
    if lease is not None:
        try:
            lease_dt = datetime.fromisoformat(lease)
        except (TypeError, ValueError):
            lease_dt = None
        if lease_dt is None or _lease_of(row) != lease:
            log.info(
                "stale 실패 보고 무시: %s (보고 lease=%s, 현재=%s)",
                account_key, lease, _lease_of(row),
            )
            return {"retry": False, "attempt": int(row.attempt_count or 0),
                    "reason": None, "stale": True}

    attempt = int(row.attempt_count or 0)
    reason: str | None = None
    if kind == KIND_LOGIN_REQUIRED:
        reason = "로그인 필요 — 재시도 안 함(로그인 후 다시 갱신을 눌러주세요)"
    elif kind == KIND_ACCESS_DENIED:
        reason = "접근 권한/구독 만료 — 재시도 안 함(구독 상태를 확인한 뒤 다시 갱신을 눌러주세요)"
    elif kind == KIND_MAPPING_BROKEN:
        reason = "수집기 매핑 파손 — 재시도 안 함(구독 문제가 아니라 코드 수정이 필요합니다)"
    elif attempt >= MAX_ATTEMPTS:
        reason = f"재시도 {MAX_ATTEMPTS}회 소진"

    message = f"{error} [{reason}]" if reason else error
    values: dict = {
        # 컬럼 한계 — 긴 스택트레이스로 보고 자체가 날아가면 안 된다
        "last_error": message[:300],
        "last_error_at": kst_now(),
        "claimed_at": None,   # lease 반납 → 다음 폴에서 재claim(소멸 케이스에도 해제)
    }
    if reason is not None:
        values["refresh_requested_at"] = None   # 요청 소멸(더 이상 재시도하지 않는다)
        # attempt_count는 남긴다(진단용). 다음 버튼(request_refresh)이 0으로 리셋한다.

    # ★조건부 UPDATE로 전이한다(codex 2R[P2]): lease 확인(SELECT)과 쓰기 사이에 다른 폴이
    # 재claim했다면 rowcount=0으로 걸러진다. SELECT만 믿고 무조건 쓰면 중복·지연된 실패
    # 핸들러 둘이 각각 검사를 통과해 새 임대를 반납하고 시도 예산을 갉아먹을 수 있다.
    stmt = update(CoupangWingCookie).where(CoupangWingCookie.account_key == account_key)
    if lease_dt is not None:
        stmt = stmt.where(CoupangWingCookie.claimed_at == lease_dt)
    res = db.execute(stmt.values(**values))
    db.commit()
    if (res.rowcount or 0) == 0:
        log.info("stale 실패 보고 무시(경합): %s lease=%s", account_key, lease)
        return {"retry": False, "attempt": attempt, "reason": None, "stale": True}

    if reason is not None:
        log.warning("refresh 요청 소멸: %s attempt=%d 사유=%s", account_key, attempt, reason)
        return {"retry": False, "attempt": attempt, "reason": reason}
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
