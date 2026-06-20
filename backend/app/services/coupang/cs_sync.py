# cs_sync.py — 쿠팡 CS(고객문의) 동기화 Harness (P6).
# SA 조합: CoupangCsClient(#1 온라인문의 + #3 CS이관문의) → CoupangInquiry DB upsert.
# 소비자: POST /api/sync/coupang-cs + 스케줄러 06:05 KST.
# 원칙22: 라이브 증거(answered 상태·건수)만 보고. 추정 금지.
from __future__ import annotations

import logging
from app.utils.kst import kst_now, kst_today
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.clients.coupang._base import CoupangReadError
from app.clients.coupang.cs import CoupangCsClient
from app.config import CoupangAccountConfig, get_coupang_config
from app.models import CoupangInquiry

CS_ACCOUNTS = ["COUPANG_WING1", "COUPANG_WING2"]

log = logging.getLogger(__name__)

_INQUIRY_DAYS = 7  # 요청 일수(클라이언트가 end-start≤6일로 cap, cs._MAX_DAYS)

# partnerCounselingStatus 유효 enum — 라이브 실측(원칙22, 2026-06-20): 그 외 값
# (COMPLETE/ANSWER_COMPLETE/PROGRESS/ALL)은 쿠팡이 code=500 internal error 반환.
_VALID_CC_STATUSES = frozenset({"NO_ANSWER", "ANSWER", "TRANSFER", "NONE"})
# CS이관 동기화 조회 상태: 미답변(NO_ANSWER) + 답변완료(ANSWER) 갱신.
# ★과거 "COMPLETE"(무효)가 17일간 code=500 유발 → CS 동기화 부분 실패.
_CC_QUERY_STATUSES = ("NO_ANSWER", "ANSWER")


def _build_client(account: CoupangAccountConfig) -> CoupangCsClient:
    return CoupangCsClient(account)


def _account_key(account: CoupangAccountConfig) -> str:
    """config에 account_key 필드 없음 → vendor_id를 키로 사용."""
    return account.vendor_id


def _parse_dt(val: Any) -> datetime | None:
    """ISO 또는 타임스탬프 문자열을 datetime으로 변환."""
    if not val:
        return None
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(val / 1000, tz=timezone.utc).replace(tzinfo=None)
        except Exception:
            return None
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _upsert_inquiry(
    db: Session,
    *,
    account_key: str,
    vendor_id: str,
    inquiry_type: str,
    row: dict,
) -> None:
    """문의 dict → CoupangInquiry upsert."""
    inquiry_id = str(
        row.get("inquiryId") or row.get("onlineInquiryId") or row.get("counselingId") or ""
    )
    if not inquiry_id:
        return

    # 답변 여부 판정
    answered_type = str(row.get("answeredType") or row.get("partnerCounselingStatus") or "")
    answered = answered_type.upper() in ("ANSWERED", "ANSWER", "COMPLETE", "COMPLETED")

    # 상태 문자열
    status = (
        row.get("partnerCounselingStatus")
        or row.get("answeredType")
        or row.get("status")
        or None
    )

    stmt = (
        sqlite_insert(CoupangInquiry)
        .values(
            account_key=account_key,
            vendor_id=vendor_id,
            inquiry_type=inquiry_type,
            inquiry_id=inquiry_id,
            status=str(status)[:30] if status else None,
            title=str(row.get("title") or "")[:200] or None,
            content=str(row.get("content") or row.get("inquiryContent") or "")[:2000] or None,
            answered=answered,
            inquired_at=_parse_dt(row.get("inquiryDate") or row.get("createdAt")),
        )
        .on_conflict_do_update(
            index_elements=["account_key", "inquiry_type", "inquiry_id"],
            set_={
                "status": str(status)[:30] if status else None,
                "answered": answered,
                "synced_at": kst_now(),
            },
        )
    )
    db.execute(stmt)


def sync_cs(
    db: Session,
    account: CoupangAccountConfig,
    *,
    days: int = _INQUIRY_DAYS,
) -> dict:
    """계정 1개의 CS 문의 동기화.

    Returns:
        {account_key, online_total, cc_total, unanswered, api_failures, errors}
    """
    client = _build_client(account)
    account_key = _account_key(account)
    vendor_id = account.vendor_id

    online_total = 0
    cc_total = 0
    api_failures: list[str] = []

    # ── #1 상품별 고객문의 (온라인 Q&A) ─────────────────────────────────────────
    try:
        for row in client.iter_online_inquiries(answered_type="ALL", days=days):
            _upsert_inquiry(
                db,
                account_key=account_key,
                vendor_id=vendor_id,
                inquiry_type="online",
                row=row,
            )
            online_total += 1
        db.commit()
    except CoupangReadError as e:
        log.warning("[cs_sync] %s 온라인문의 읽기 실패: %s", account_key, e)
        api_failures.append(f"online:{e}")
        db.rollback()

    # ── #3 쿠팡 고객센터 문의 (CS이관) — NO_ANSWER 우선, 이후 ANSWER(답변완료)도 조회 ──────
    # codex[P2]: NO_ANSWER만 조회하면 이미 답변된 건이 DB에 unanswered=False로 남음(stale).
    # NO_ANSWER로 현재 미답변을 upsert하고, 추가로 답변완료 건(ANSWER)도 가져와 answered 갱신.
    for cs_status in _CC_QUERY_STATUSES:
        try:
            for row in client.iter_call_center_inquiries(status=cs_status, days=days):
                _upsert_inquiry(
                    db,
                    account_key=account_key,
                    vendor_id=vendor_id,
                    inquiry_type="call_center",
                    row=row,
                )
                if cs_status == "NO_ANSWER":
                    cc_total += 1
            db.commit()
        except CoupangReadError as e:
            log.warning("[cs_sync] %s CS문의 읽기 실패(status=%s): %s", account_key, cs_status, e)
            api_failures.append(f"call_center_{cs_status}:{e}")
            db.rollback()

    # 미답변 현황 집계
    unanswered = (
        db.query(CoupangInquiry)
        .filter(
            CoupangInquiry.account_key == account_key,
            CoupangInquiry.answered.is_(False),
        )
        .count()
    )

    return {
        "account_key": account_key,
        "online_total": online_total,
        "cc_total": cc_total,
        "unanswered": unanswered,
        "api_failures": len(api_failures),
        "errors": api_failures,
    }


def sync_all_cs(db: Session) -> dict:
    """전 계정(WING1·WING2) CS 문의 동기화.

    Returns:
        {accounts:[...per-account], total_online, total_cc, total_unanswered, total_api_failures}
    """
    account_cfgs = [get_coupang_config(k) for k in CS_ACCOUNTS]
    accounts = [a for a in account_cfgs if a is not None]
    results = []
    total_online = 0
    total_cc = 0
    total_unanswered = 0
    total_failures = 0

    for account in accounts:
        try:
            r = sync_cs(db, account)
            results.append(r)
            total_online += r["online_total"]
            total_cc += r["cc_total"]
            total_unanswered += r["unanswered"]
            total_failures += r["api_failures"]
        except Exception as e:
            key = _account_key(account)
            log.exception("[cs_sync] %s 동기화 예외: %s", key, e)
            results.append({"account_key": key, "error": str(e)})

    return {
        "accounts": results,
        "total_online": total_online,
        "total_cc": total_cc,
        "total_unanswered": total_unanswered,
        "total_api_failures": total_failures,
    }
