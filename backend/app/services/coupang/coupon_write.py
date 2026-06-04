# coupon_write.py — 쿠팡 쿠폰 쓰기 Harness (트랙 D-16, W4).
# W4 구현: 다운로드쿠폰 3(생성·아이템생성·파기) + 즉시할인쿠폰 3(생성·아이템생성·파기) = 6개.
# #1·#3 [도서]캐시백 = 오픽스 무관(D-7) → 영구 NotImplementedError.
# 핵심: dry_run 게이트는 _write_guard.guarded_write. SA는 dry_run 모름(원칙18-1).
# retry_transient=False (쿠폰 재생성·중복 적용 위험). 비동기 응답: requestedId 반환.
from __future__ import annotations

import logging

from app.clients.coupang._base import CoupangWriteValidationError
from app.clients.coupang.coupons import CoupangCouponClient
from app.config import CoupangAccountConfig
from app.services.coupang._write_guard import guarded_write

log = logging.getLogger(__name__)

_FMS_BASE = "/v2/providers/fms/apis/api"
_MP_BASE = "/v2/providers/marketplace_openapi/apis/api/v1"


def _client(account: CoupangAccountConfig) -> CoupangCouponClient:
    return CoupangCouponClient(account)


def _require_coupon_body(body: object, required_keys: tuple[str, ...], context: str) -> dict:
    """body가 dict이고 필수 키를 가졌는지 검증(dry-run에서도 적용)."""
    if not isinstance(body, dict):
        raise CoupangWriteValidationError(
            f"{context}: body는 dict여야 합니다 (받은 타입: {type(body).__name__})"
        )
    for k in required_keys:
        if k not in body:
            raise CoupangWriteValidationError(f"{context}: body 필수 필드 누락: '{k}'")
    return body


def _require_int_pos(value: object, field: str) -> int:
    """양의 정수 검증."""
    try:
        v = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as e:
        raise CoupangWriteValidationError(f"쿠폰 쓰기 정수 필드 오류: {field}={value!r}") from e
    if v <= 0:
        raise CoupangWriteValidationError(f"쿠폰 쓰기 {field} 범위 오류: {v} (양의 정수 필요)")
    return v


def _require_list(value: object, field: str) -> list:
    """list 타입 검증."""
    if not isinstance(value, list):
        raise CoupangWriteValidationError(
            f"쿠폰 쓰기 {field}: list 타입이어야 합니다 (받은 타입: {type(value).__name__})"
        )
    if not value:
        raise CoupangWriteValidationError(f"쿠폰 쓰기 {field}: 빈 리스트 불가")
    return value


# ════════════════════════════════════════════════════════════════════
# 다운로드쿠폰 (#7·#8·#9) — marketplace_openapi gateway
# ════════════════════════════════════════════════════════════════════

def create_download_coupon(
    account: CoupangAccountConfig,
    body: dict,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#7 다운로드쿠폰 생성 (POST, body 있음). dry_run 기본.

    body 필수: title, contractId, couponType(='DOWNLOAD'), startDate, endDate, userId, policies[].
    응답: requestResultStatus=SUCCESS + body.couponId.
    ⚠️ 생성 후 최소 1시간 이후부터 프론트 반영. 한 옵션당 쿠폰 1개만 가능.
    """
    _require_coupon_body(body, ("title", "contractId", "couponType", "startDate", "endDate", "userId", "policies"), "다운로드쿠폰 생성")
    client = _client(account)
    return guarded_write(
        operation=f"다운로드쿠폰 생성({body.get('title', '')!r})",
        method="POST",
        path=f"{_MP_BASE}/coupons",
        payload={
            "title": body.get("title"),
            "contractId": body.get("contractId"),
            "couponType": body.get("couponType"),
            "startDate": body.get("startDate"),
            "endDate": body.get("endDate"),
            "_note": "다운로드쿠폰 생성 body 요약",
        },
        sa_call=lambda: client.create_download_coupon(body=body),
        dry_run=dry_run,
        confirm=confirm,
    )


def create_download_coupon_items(
    account: CoupangAccountConfig,
    body: dict,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#8 다운로드쿠폰 아이템 생성 (PUT, body 있음). dry_run 기본.

    body 필수: couponItems[{couponId, userId, vendorItemIds[]}]. 옵션ID max 100개/호출.
    ⚠️ 아이템 추가 실패 시 해당 쿠폰은 파기됨(쿠팡 정책).
    """
    _require_coupon_body(body, ("couponItems",), "다운로드쿠폰 아이템 생성")
    items = _require_list(body.get("couponItems"), "couponItems")
    client = _client(account)
    return guarded_write(
        operation=f"다운로드쿠폰 아이템 생성(coupon_count={len(items)})",
        method="PUT",
        path=f"{_MP_BASE}/coupon-items",
        payload={
            "couponItems_count": len(items),
            "coupon_ids": [i.get("couponId") for i in items[:3]],
            "_note": "다운로드쿠폰 아이템 body 요약",
        },
        sa_call=lambda: client.create_download_coupon_items(body=body),
        dry_run=dry_run,
        confirm=confirm,
    )


def expire_download_coupon(
    account: CoupangAccountConfig,
    body: dict,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#9 다운로드쿠폰 파기 (POST, body 있음). dry_run 기본.

    body 필수: expireCouponList[{couponId, reason='expired', userId}].
    비동기: requestTransactionId로 상태 확인.
    """
    _require_coupon_body(body, ("expireCouponList",), "다운로드쿠폰 파기")
    expire_list = _require_list(body.get("expireCouponList"), "expireCouponList")
    client = _client(account)
    return guarded_write(
        operation=f"다운로드쿠폰 파기(count={len(expire_list)})",
        method="POST",
        path=f"{_MP_BASE}/coupons/expire",
        payload={
            "coupon_ids": [i.get("couponId") for i in expire_list],
            "_note": "다운로드쿠폰 파기 body 요약",
        },
        sa_call=lambda: client.expire_download_coupon(body=body),
        dry_run=dry_run,
        confirm=confirm,
    )


# ════════════════════════════════════════════════════════════════════
# 즉시할인쿠폰 (#12·#13·#14) — fms gateway
# ════════════════════════════════════════════════════════════════════

def create_instant_coupon(
    account: CoupangAccountConfig,
    body: dict,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#12 즉시할인쿠폰 생성 (POST, body 있음). dry_run 기본.

    body 필수: contractId, name, maxDiscountPrice, discount, startAt, endAt, type.
    비동기: data.content.requestedId → #21 요청상태 확인.
    ⚠️ 생성 후 쿠폰 적용 상품 삭제 불가(파기 후 재생성 필요).
    """
    _require_coupon_body(body, ("contractId", "name", "maxDiscountPrice", "discount", "startAt", "endAt", "type"), "즉시할인쿠폰 생성")
    client = _client(account)
    return guarded_write(
        operation=f"즉시할인쿠폰 생성({body.get('name', '')!r})",
        method="POST",
        path=f"{_FMS_BASE}/v2/vendors/{account.vendor_id}/coupon",
        payload={
            "name": body.get("name"),
            "contractId": body.get("contractId"),
            "type": body.get("type"),
            "startAt": body.get("startAt"),
            "endAt": body.get("endAt"),
            "_note": "즉시할인쿠폰 생성 body 요약",
        },
        sa_call=lambda: client.create_instant_coupon(body=body),
        dry_run=dry_run,
        confirm=confirm,
    )


def create_instant_coupon_items(
    account: CoupangAccountConfig,
    coupon_id: int,
    vendor_item_ids: list,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#13 즉시할인쿠폰 아이템 생성 (POST, body 있음). dry_run 기본.

    vendor_item_ids: 쿠폰 적용 옵션ID 배열(max 10,000개/호출). 비동기: requestedId.
    """
    cid = _require_int_pos(coupon_id, "couponId")
    items = _require_list(vendor_item_ids, "vendorItemIds")
    if len(items) > 10000:
        raise CoupangWriteValidationError(f"즉시할인쿠폰 아이템: 옵션ID max 10,000개, 입력={len(items)}")
    client = _client(account)
    return guarded_write(
        operation=f"즉시할인쿠폰 아이템 생성(coupon={cid}, items={len(items)})",
        method="POST",
        path=f"{_FMS_BASE}/v1/vendors/{account.vendor_id}/coupons/{cid}/items",
        payload={"couponId": cid, "vendor_item_count": len(items), "_note": "body 요약"},
        sa_call=lambda: client.create_instant_coupon_items(coupon_id=cid, vendor_item_ids=items),
        dry_run=dry_run,
        confirm=confirm,
    )


def expire_instant_coupon(
    account: CoupangAccountConfig,
    coupon_id: int,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#14 즉시할인쿠폰 파기 (PUT, body 없음, query action=expire). dry_run 기본.

    비동기: data.content.requestedId → #21 요청상태 확인.
    ⚠️ 진행 중 아이템 추가 요청이 있으면 파기 실패(success=false).
    """
    cid = _require_int_pos(coupon_id, "couponId")
    client = _client(account)
    return guarded_write(
        operation=f"즉시할인쿠폰 파기(coupon={cid})",
        method="PUT",
        path=f"{_FMS_BASE}/v1/vendors/{account.vendor_id}/coupons/{cid}",
        payload={"couponId": cid, "query": "action=expire", "_note": "no body"},
        sa_call=lambda: client.expire_instant_coupon(coupon_id=cid),
        dry_run=dry_run,
        confirm=confirm,
    )
