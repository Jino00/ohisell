# rg_inbound_sync.py — RG 입고 동기화 Harness (트랙 RG-Replenishment D-1·D-5, S1)
# 흐름: 계정별 → 쿠키 로드·복호화(SA: crypto) → Wing inbound/search 순회(SA: inbound 클라)
#       → 옵션 그레인 파싱(발송→판매개시 리드타임) → coupang_rg_inbound upsert.
# 쿠키 CRUD(저장 암호화/상태)도 여기 집약 — 라우터는 이 Harness만 호출(원칙 18-7).
# fail-soft: 302/401(만료)=status red + 이번 변경 rollback(마지막 이력 유지). 성공=last_success_at 기록(만료 측정).
from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.clients.coupang import CoupangInboundClient
from app.clients.coupang.inbound import WingAuthError, WingReadError, parse_curl_cookies
from app.models import CoupangRgInbound, CoupangWingCookie
from app.utils.crypto import CookieCryptoError, decrypt_secret, encrypt_secret
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

RG_ACCOUNTS = ["COUPANG_WING1", "COUPANG_WING2"]
_KST = ZoneInfo("Asia/Seoul")

# 입고 생애주기 statusId (S0 실측) — 리드타임 = 3(발송) → 7(판매개시)
_STATUS_SHIPMENT_CREATED = 3
_STATUS_STOWING = 7


# ════════════════════════════════════════════════
# 파싱 헬퍼
# ════════════════════════════════════════════════
def _int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _parse_ms(value) -> datetime | None:
    """ms epoch(또는 ISO) → KST naive datetime. 입고 타임스탬프는 ms epoch(S0 실측)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():
        try:
            return datetime.fromtimestamp(int(s) / 1000, _KST).replace(tzinfo=None)
        except (ValueError, OverflowError, OSError):
            return None
    # ISO 방어
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(_KST).replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def _status_ts(history, status_id: int) -> datetime | None:
    """shipmentStatusHistory에서 해당 statusId의 updatedAt(ms) → datetime.

    history는 dict({"_N":{...}}) 또는 list 둘 다 방어(S0 기록은 dict 형태)."""
    if isinstance(history, dict):
        values = history.values()
    elif isinstance(history, list):
        values = history
    else:
        return None
    for v in values:
        # ★codex S1: statusId가 "3"/"7" 문자열로 와도 매칭되도록 _int 변환 비교(내부 API 흔들림 방어)
        if isinstance(v, dict) and _int(v.get("statusId")) == status_id:
            return _parse_ms(v.get("updatedAt"))
    return None


def _inbound_id(row: dict) -> str | None:
    """입고건 고유 식별자 추출(방어적 — 실응답 필드명 라이브 확정 전).

    후보 키 순회 → 없으면 vendorId+createdAt 합성(임시 grain). 둘 다 없으면 None(스킵)."""
    for k in ("id", "inboundId", "inboundShipmentId", "shipmentId", "inboundRequestId"):
        v = row.get(k)
        if v not in (None, ""):
            return str(v)
    vid, cat = row.get("vendorId"), row.get("createdAt")
    if vid and cat:
        return f"{vid}_{cat}"
    return None


def _upsert_inbound(db: Session, account_key: str, row: dict, sku: dict) -> bool:
    """입고 옵션 1건을 coupang_rg_inbound에 upsert ((account_key, inbound_id, vendor_item_id) 기준)."""
    inbound_id = _inbound_id(row)
    planned = sku.get("plannedSku") or {}
    vii = planned.get("vendorItemId")
    if not inbound_id or vii in (None, ""):
        return False
    inbound_id, vii = str(inbound_id), str(vii)

    history = row.get("shipmentStatusHistory")
    ship_at = _status_ts(history, _STATUS_SHIPMENT_CREATED)
    stow_at = _status_ts(history, _STATUS_STOWING)
    lead = None
    if ship_at and stow_at and stow_at >= ship_at:
        lead = Decimal(str(round((stow_at - ship_at).total_seconds() / 86400, 2)))

    db_row = (
        db.query(CoupangRgInbound)
        .filter(
            CoupangRgInbound.account_key == account_key,
            CoupangRgInbound.inbound_id == inbound_id,
            CoupangRgInbound.vendor_item_id == vii,
        )
        .first()
    )
    if db_row is None:
        db_row = CoupangRgInbound(
            account_key=account_key, inbound_id=inbound_id, vendor_item_id=vii
        )
        db.add(db_row)
    db_row.vendor_id = (str(row.get("vendorId")) if row.get("vendorId") else None) or None
    sid = planned.get("skuId")
    db_row.sku_id = str(sid)[:50] if sid not in (None, "") else None
    db_row.cached_sku_name = (planned.get("cachedSkuName") or "")[:300] or None
    db_row.requested_qty = _int(planned.get("requestedQty"))
    db_row.received_qty = _int(sku.get("receivedQty"))
    db_row.stowed_qty = _int(sku.get("stowedQty"))
    db_row.shipment_created_at = ship_at
    db_row.stowing_at = stow_at
    db_row.lead_time_days = lead
    db_row.inbound_created_at = _parse_ms(row.get("createdAt"))
    db_row.raw_json = json.dumps(
        {
            "vendorId": row.get("vendorId"),
            "createdAt": row.get("createdAt"),
            "shipmentStatusHistory": history,
            "sku": sku,
        },
        ensure_ascii=False,
    )[:20000]
    return True


# ════════════════════════════════════════════════
# 쿠키 CRUD (라우터가 이 Harness만 호출 — 원칙 18-7)
# ════════════════════════════════════════════════
def _cookie_row(db: Session, account_key: str) -> CoupangWingCookie | None:
    return (
        db.query(CoupangWingCookie)
        .filter(CoupangWingCookie.account_key == account_key)
        .first()
    )


def save_cookie(db: Session, account_key: str, curl_or_cookie: str) -> dict:
    """cURL(또는 쿠키 문자열)에서 쿠키·xsrf 추출 → Fernet 암호화 저장. 쿠키 값은 응답에 노출 금지.

    저장 직후 status=unknown(미검증). 다음 sync(또는 즉시 트리거)가 검증해 green/red 갱신.
    parse 실패 → ValueError, 암호화 키 없음 → CookieCryptoError (라우터가 400으로 매핑)."""
    if account_key not in RG_ACCOUNTS:
        raise ValueError(f"알 수 없는 계정: {account_key} (허용: {RG_ACCOUNTS})")
    cookie, xsrf = parse_curl_cookies(curl_or_cookie)  # ValueError 전파
    enc_cookie = encrypt_secret(cookie)  # CookieCryptoError 전파(키 없음)
    enc_xsrf = encrypt_secret(xsrf)
    row = _cookie_row(db, account_key)
    if row is None:
        row = CoupangWingCookie(account_key=account_key)
        db.add(row)
    row.cookie_blob = enc_cookie
    row.xsrf_token = enc_xsrf
    row.last_saved_at = kst_now()
    row.status = "unknown"
    row.last_error = None
    db.commit()
    return {"account": account_key, "saved": True, "cookie_len": len(cookie)}


def cookie_status(db: Session) -> list[dict]:
    """계정별 쿠키 설정·상태 요약 (민감값 미포함). UI 상태 표시용."""
    out: list[dict] = []
    for key in RG_ACCOUNTS:
        row = _cookie_row(db, key)
        if row is None:
            out.append({"account": key, "configured": False, "status": "none",
                        "last_saved_at": None, "last_success_at": None, "last_error": None})
            continue
        out.append({
            "account": key,
            "configured": bool(row.cookie_blob),
            "status": row.status,
            "last_saved_at": row.last_saved_at.isoformat() if row.last_saved_at else None,
            "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
            "last_error": row.last_error,
        })
    return out


# ════════════════════════════════════════════════
# 동기화 (Harness 본체)
# ════════════════════════════════════════════════
def _mark_cookie(db: Session, account_key: str, *, status: str | None, error: str | None,
                 success: bool = False) -> None:
    """rollback 후 cookie row를 재조회해 상태 갱신·commit (fail-soft 경로 공용)."""
    row = _cookie_row(db, account_key)
    if row is None:
        return
    if status is not None:
        row.status = status
    if error is not None:
        row.last_error = error[:300]
    if success:
        row.last_success_at = kst_now()
        row.last_error = None
    db.commit()


def sync_account_inbound(db: Session, account_key: str, *, paging_size: int = 50) -> dict:
    """한 계정의 Wing 입고를 동기화. 반환: 통계 dict. 인증 실패(만료)=fail-soft(이전 이력 유지)."""
    stats: dict = {"account": account_key, "inbounds": 0, "items": 0}
    row = _cookie_row(db, account_key)
    if row is None or not row.cookie_blob:
        stats["error"] = "cookie_missing"
        return stats

    try:
        cookie = decrypt_secret(row.cookie_blob)
        xsrf = decrypt_secret(row.xsrf_token or "")
    except CookieCryptoError as e:
        _mark_cookie(db, account_key, status="red", error=str(e))
        stats["error"] = "crypto_error"
        stats["detail"] = str(e)
        log.error("RG 입고 동기화 복호화 실패 %s: %s", account_key, e)
        return stats

    client = CoupangInboundClient(cookie, xsrf)
    try:
        for inbound in client.iter_inbound(paging_size=paging_size):
            if not isinstance(inbound, dict):
                continue
            stats["inbounds"] += 1
            sku_details = inbound.get("skuDetails")
            if not isinstance(sku_details, list):
                # ★codex S1: skuDetails non-list(dict/null/str)면 이 입고만 skip(fail-soft) — 다음 입고 계속
                log.warning("RG 입고 skuDetails non-list skip %s: %s", account_key, type(sku_details).__name__)
                continue
            for sku in sku_details:
                if isinstance(sku, dict) and _upsert_inbound(db, account_key, inbound, sku):
                    stats["items"] += 1
    except WingAuthError as e:
        db.rollback()  # 이번 sync 부분 변경 폐기 = 마지막 입고 이력 유지(fail-soft)
        _mark_cookie(db, account_key, status="red", error=str(e))
        # ★codex S1: rollback으로 DB 미반영 → 통계도 0으로 정정(처리된 것처럼 보이지 않게)
        stats.update({"error": "auth_expired", "detail": str(e), "items": 0, "rolled_back": True})
        log.warning("RG 입고 동기화 쿠키 만료 %s: %s", account_key, e)
        return stats
    except WingReadError as e:
        db.rollback()
        # ★codex S1: read_error도 status=red — 직전 green이 남아 'stale=정상'으로 위장되는 것 방지(원칙22).
        #   만료(auth)와 구분은 last_error 메시지로. job raise는 안 함(입고 fail-soft 설계 유지).
        _mark_cookie(db, account_key, status="red", error=str(e))
        stats.update({"error": "read_error", "detail": str(e), "items": 0, "rolled_back": True})
        log.error("RG 입고 동기화 읽기 실패 %s: %s", account_key, e)
        return stats

    db.commit()
    _mark_cookie(db, account_key, status="green", error=None, success=True)
    log.info("RG 입고 동기화 완료 %s: %s", account_key, stats)
    return stats


def sync_all_inbound(db: Session, *, paging_size: int = 50) -> list[dict]:
    """2개 셀러계정(WING1·WING2) Wing 입고 동기화. 쿠키 미설정 계정은 cookie_missing으로 스킵."""
    return [sync_account_inbound(db, key, paging_size=paging_size) for key in RG_ACCOUNTS]
