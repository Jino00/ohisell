# coupang_ops.py — 쿠팡 쓰기 라우터 (트랙 §5 Layer3 coupang_ops, D-16 쓰기 페이즈).
# W3a 상품 단순쓰기 9개(재고·가격·할인율기준가·판매중지/재개·자동생성옵션 활성/비활성).
# 전부 dry_run=true 기본 — 라이브 실행은 dry_run=false + confirm 토큰 필요(이중확인).
# 변경값은 query param(쿠팡 원 API가 path/query, body 없음 — 명세 02 §4). Harness가 dry_run 게이트.
# 트랙 D-8: 쿠팡 Open API는 서버 IP 화이트리스트 → 라이브 호출은 서버에서만(로컬 403).
from __future__ import annotations

from app.utils.kst import kst_now, kst_today
import logging

from typing import Any

from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

import os

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_coupang_config
from app.database import get_db
from app.models import Channel, CoupangAdOptionDaily, CoupangProductItem, CoupangRevenueFee, CoupangRgInbound, CoupangRgOrderItem, Order, ProductChannelMapping, ProductMaster
from app.routers._coupang_write_http import handle_write as _handle_write
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED
from app.services.coupang import ad_cost_sync, coupon_write, lead_time_estimator, product_write, rg_inbound_sync, rg_replenishment, sales_velocity_estimator
from app.utils.crypto import CookieCryptoError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/coupang/ops", tags=["coupang-ops"])

_DEFAULT_ACCOUNT = "COUPANG_WING2"  # 주 계정(Wing2). 옵션ID는 계정 귀속(D-8) → 정확한 계정 지정 필요.


# ════════════════════════════════════════════════════════════════════
# 운영 패널 — 상품 목록 조회 (DB, 쿠팡 API 호출 없음)
# ════════════════════════════════════════════════════════════════════

@router.get("/products/items")
def list_product_items(db: Session = Depends(get_db)):
    """운영 패널용 상품 목록. coupang_product_item DB에서 반환 (라이브 API 호출 없음)."""
    rows = db.query(CoupangProductItem).order_by(
        CoupangProductItem.account_key,
        CoupangProductItem.item_name,
    ).all()
    return [
        {
            "vendor_item_id": r.vendor_item_id,
            "item_name": r.item_name or "—",
            "seller_product_name": r.seller_product_name or "—",
            "account_key": r.account_key,
            "sale_price": str(r.sale_price) if r.sale_price is not None else None,
            "stock": r.amount_in_stock,
            "on_sale": r.on_sale,
            "status_name": r.status_name,
        }
        for r in rows
    ]


def _get_account(account_key: str = _DEFAULT_ACCOUNT):
    cfg = get_coupang_config(account_key)
    if not cfg:
        raise HTTPException(status_code=503, detail=f"계정 설정 없음: {account_key}")
    return cfg


# ════════════════════════════════════════════════════════════════════
# 옵션 단위 쓰기 (vendorItemId 대상)
# ════════════════════════════════════════════════════════════════════

@router.put("/products/items/{vendor_item_id}/quantity")
def update_item_quantity(
    vendor_item_id: int = Path(..., ge=1),
    quantity: int = Query(..., ge=0),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#14 아이템 재고수량 변경 — ⚠️쓰기. dry_run=true(기본)면 미리보기만."""
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: product_write.update_item_quantity(
            cfg, vendor_item_id, quantity, dry_run=dry_run, confirm=confirm
        )
    )


@router.put("/products/items/{vendor_item_id}/price")
def update_item_price(
    vendor_item_id: int = Path(..., ge=1),
    price: int = Query(..., ge=1),
    force_sale_price_update: bool = Query(default=False),
    ap_min_sale_price: int | None = Query(default=None, ge=1),
    ap_active: bool | None = Query(default=None),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#15 아이템 판매가격 변경 — ⚠️쓰기. dry_run 기본.

    force_sale_price_update=true면 변경비율 제한(기존가 -50%~+100%) 해제.
    ap_min_sale_price·ap_active는 자동가격조정(함께 전달, ap_min<price).
    """
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: product_write.update_item_price(
            cfg,
            vendor_item_id,
            price,
            force_sale_price_update=force_sale_price_update,
            ap_min_sale_price=ap_min_sale_price,
            ap_active=ap_active,
            dry_run=dry_run,
            confirm=confirm,
        )
    )


@router.put("/products/items/{vendor_item_id}/base-price")
def update_item_base_price(
    vendor_item_id: int = Path(..., ge=1),
    original_price: int = Query(..., ge=0),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#16 아이템 할인율 기준가격 변경 — ⚠️쓰기. dry_run 기본."""
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: product_write.update_item_base_price(
            cfg, vendor_item_id, original_price, dry_run=dry_run, confirm=confirm
        )
    )


@router.put("/products/items/{vendor_item_id}/sale/resume")
def resume_item_sale(
    vendor_item_id: int = Path(..., ge=1),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#17 아이템 판매 재개 — ⚠️쓰기. dry_run 기본."""
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: product_write.resume_item_sale(
            cfg, vendor_item_id, dry_run=dry_run, confirm=confirm
        )
    )


@router.put("/products/items/{vendor_item_id}/sale/stop")
def stop_item_sale(
    vendor_item_id: int = Path(..., ge=1),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#18 아이템 판매 중지 — ⚠️쓰기. dry_run 기본."""
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: product_write.stop_item_sale(
            cfg, vendor_item_id, dry_run=dry_run, confirm=confirm
        )
    )


@router.post("/products/items/{vendor_item_id}/auto-option/enable")
def enable_auto_option_item(
    vendor_item_id: int = Path(..., ge=1),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#19 자동생성옵션 활성화(옵션 단위) — ⚠️쓰기. dry_run 기본."""
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: product_write.enable_auto_option_item(
            cfg, vendor_item_id, dry_run=dry_run, confirm=confirm
        )
    )


@router.post("/products/items/{vendor_item_id}/auto-option/disable")
def disable_auto_option_item(
    vendor_item_id: int = Path(..., ge=1),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#21 자동생성옵션 비활성화(옵션 단위) — ⚠️쓰기. dry_run 기본."""
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: product_write.disable_auto_option_item(
            cfg, vendor_item_id, dry_run=dry_run, confirm=confirm
        )
    )


# ════════════════════════════════════════════════════════════════════
# 셀러 전체 단위 쓰기 (⚠️ 셀러의 모든 적격 상품에 영향)
# ════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════
# W3b 복잡 쓰기 (상품 생성·승인요청·수정) — body 있음. 삭제 영구 차단.
# body는 dict[str, Any](카테고리·옵션에 따라 필드가 달라져 Pydantic 모델 대신 자유형).
# Harness에서 필수키·타입 검증.
# ════════════════════════════════════════════════════════════════════

@router.post("/products")
def create_product(
    body: dict[str, Any] = Body(...),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#9 상품 생성 — ⚠️쓰기. dry_run=true(기본)면 body 요약 미리보기만.

    body 필수: vendorId, sellerProductName, items[]. requested=true로 자동승인 요청 가능.
    """
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: product_write.create_product(cfg, body, dry_run=dry_run, confirm=confirm)
    )


@router.put("/products/{seller_product_id}/approvals")
def request_approval(
    seller_product_id: int = Path(..., ge=1),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#10 상품 승인 요청 — ⚠️쓰기. '임시저장' 상태에서만 가능. body 없음."""
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: product_write.request_approval(
            cfg, seller_product_id, dry_run=dry_run, confirm=confirm
        )
    )


@router.put("/products")
def update_product(
    body: dict[str, Any] = Body(...),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#11 상품 수정 (승인필요) — ⚠️쓰기. dry_run=true(기본)면 미리보기만.

    body 필수: sellerProductId, vendorId, items[]. 승인완료 후 반영.
    """
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: product_write.update_product(cfg, body, dry_run=dry_run, confirm=confirm)
    )


@router.put("/products/{seller_product_id}/partial")
def update_product_partial(
    seller_product_id: int = Path(..., ge=1),
    body: dict[str, Any] = Body(...),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#12 상품 수정 (승인불필요) — ⚠️쓰기. 배송/반품지 정보만. 즉시 반영.

    body 필수: sellerProductId. 나머지 배송/반품 필드는 변경분만 전송 가능.
    """
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: product_write.update_product_partial(
            cfg, seller_product_id, body, dry_run=dry_run, confirm=confirm
        )
    )


@router.delete("/products/{seller_product_id}")
def delete_product(seller_product_id: int = Path(..., ge=1)):
    """#13 상품 삭제 — ⛔ 이 시스템에서 영구 차단(시스템 정책).

    삭제가 필요하면 Wing(coupang.com)에서 직접 수행하세요.
    """
    raise HTTPException(
        status_code=403,
        detail="상품 삭제는 이 시스템에서 허용하지 않습니다. Wing(coupang.com)에서 직접 수행하세요.",
    )


@router.post("/products/seller/auto-option/enable")
def enable_auto_option_all(
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#20 자동생성옵션 활성화(전체 단위) — ⚠️쓰기·셀러 전체. dry_run 기본."""
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: product_write.enable_auto_option_all(
            cfg, dry_run=dry_run, confirm=confirm
        )
    )


@router.post("/products/seller/auto-option/disable")
def disable_auto_option_all(
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#22 자동생성옵션 비활성화(전체 단위) — ⚠️쓰기·셀러 전체. dry_run 기본."""
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: product_write.disable_auto_option_all(
            cfg, dry_run=dry_run, confirm=confirm
        )
    )


# ════════════════════════════════════════════════════════════════════
# W4 쿠폰 쓰기 (다운로드쿠폰 3 + 즉시할인쿠폰 3)
# dry_run=true 기본. body는 dict[str,Any](쿠폰 구조가 게이트웨이별로 다름).
# ════════════════════════════════════════════════════════════════════

@router.post("/coupons/download")
def w4_create_download_coupon(
    body: dict[str, Any] = Body(...),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#7 다운로드쿠폰 생성 — ⚠️쓰기. body 필수: title, contractId, couponType='DOWNLOAD', startDate, endDate, userId, policies[]."""
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: coupon_write.create_download_coupon(cfg, body, dry_run=dry_run, confirm=confirm)
    )


@router.put("/coupons/download/items")
def w4_create_download_coupon_items(
    body: dict[str, Any] = Body(...),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#8 다운로드쿠폰 아이템 생성 — ⚠️쓰기. body 필수: couponItems[{couponId, userId, vendorItemIds[]}]."""
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: coupon_write.create_download_coupon_items(cfg, body, dry_run=dry_run, confirm=confirm)
    )


@router.post("/coupons/download/expire")
def w4_expire_download_coupon(
    body: dict[str, Any] = Body(...),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#9 다운로드쿠폰 파기 — ⚠️쓰기. body 필수: expireCouponList[{couponId, reason='expired', userId}]."""
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: coupon_write.expire_download_coupon(cfg, body, dry_run=dry_run, confirm=confirm)
    )


@router.post("/coupons/instant")
def w4_create_instant_coupon(
    body: dict[str, Any] = Body(...),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#12 즉시할인쿠폰 생성 — ⚠️쓰기. body 필수: contractId, name, maxDiscountPrice, discount, startAt, endAt, type."""
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: coupon_write.create_instant_coupon(cfg, body, dry_run=dry_run, confirm=confirm)
    )


@router.post("/coupons/instant/{coupon_id}/items")
def w4_create_instant_coupon_items(
    coupon_id: int = Path(..., ge=1),
    body: dict[str, Any] = Body(...),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#13 즉시할인쿠폰 아이템 생성 — ⚠️쓰기. body 필수: vendorItems[...] (옵션ID 배열, max 10,000)."""
    cfg = _get_account(account_key)
    vendor_items = body.get("vendorItems")
    return _handle_write(
        lambda: coupon_write.create_instant_coupon_items(
            cfg, coupon_id, vendor_items, dry_run=dry_run, confirm=confirm
        )
    )


@router.put("/coupons/instant/{coupon_id}/expire")
def w4_expire_instant_coupon(
    coupon_id: int = Path(..., ge=1),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#14 즉시할인쿠폰 파기 — ⚠️쓰기. body 없음. 비동기: requestedId로 상태 확인."""
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: coupon_write.expire_instant_coupon(cfg, coupon_id, dry_run=dry_run, confirm=confirm)
    )


# ════════════════════════════════════════════════════════════════════
# W5 RG 쓰기 (로켓그로스 상품 생성·수정)
# seller_api 동일 경로이나 items[].rocketGrowthItemData 포함 body.
# ════════════════════════════════════════════════════════════════════

@router.post("/rg/products")
def w5_create_rg_product(
    body: dict[str, Any] = Body(...),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#6 RG 상품 생성 — ⚠️쓰기. body 필수: vendorId, sellerProductName, items[](rocketGrowthItemData 포함)."""
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: product_write.create_rg_product(cfg, body, dry_run=dry_run, confirm=confirm)
    )


@router.put("/rg/products")
def w5_update_rg_product(
    body: dict[str, Any] = Body(...),
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """#7 RG 상품 수정 — ⚠️쓰기. body 필수: sellerProductId, vendorId, items[](rocketGrowthItemData 포함)."""
    cfg = _get_account(account_key)
    return _handle_write(
        lambda: product_write.update_rg_product(cfg, body, dry_run=dry_run, confirm=confirm)
    )


# ════════════════════════════════════════════════════════════════════
# 운영 패널 — 매출 현황 (회사별·기간별·채널타입별)
# ════════════════════════════════════════════════════════════════════

_Q2 = Decimal("0.01")
_Q4 = Decimal("0.0001")
_Z = Decimal("0")
_WING_DELIVERY_COST = Decimal("1900")   # 판매자배송(Wing) 한건당 배송비(한진)
_DEFAULT_FEE_RATE   = Decimal("0.078")  # 수수료율 기본값 7.8% (정산 실측 최빈값)

# 쿠팡 채널코드 → (company_short, channel_type)
_CHANNEL_META: dict[str, tuple[str, str]] = {
    "COUPANG_WING1":   ("오픽스",    "Wing"),
    "COUPANG_WING2":   ("오하이테크", "Wing"),
    "COUPANG_RG1":     ("오픽스",    "로켓그로스"),
    "COUPANG_RG2":     ("오하이테크", "로켓그로스"),
    "COUPANG_ROCKET":  ("오하이테크", "로켓배송"),
}


def _safe_cfg(code: str):
    try:
        return get_coupang_config(code)
    except Exception:
        return None


# vendor_id(A01...) → channel_code (첫 번째 매칭)
def _vendor_to_channel(vendor_id: str) -> str:
    for code in _CHANNEL_META:
        cfg = _safe_cfg(code)
        if cfg and cfg.vendor_id == vendor_id:
            return code
    return "COUPANG_WING2"




def _date_range(days: int) -> tuple[date, date]:
    today = kst_today()
    if days == 0:
        return today, today                      # 오늘
    if days == 1:
        d = today - timedelta(days=1)
        return d, d                              # 어제
    return today - timedelta(days=days - 1), today


def _f(v) -> Decimal:
    if v is None:
        return _Z
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _roas(spend: Decimal, conv: Decimal) -> str | None:
    if not spend:
        return None
    return str((conv / spend).quantize(_Q2, rounding=ROUND_HALF_UP))


@router.get("/sales-summary")
def sales_summary(
    company: str = Query(default="ALL", description="오픽스 | 오하이테크 | ALL"),
    days: int = Query(default=7, ge=0, le=90),
    db: Session = Depends(get_db),
):
    """쿠팡 채널 매출 현황 — 회사별·기간별·채널타입별 집계.

    반환: summary(합계) + by_product(상품명별, channel_type 포함).
    광고비·광고전환매출은 coupang_ad_option_daily + coupang_product_item 조인.
    """
    dfrom, dto = _date_range(days)
    start = datetime.combine(dfrom, time.min)
    end = datetime.combine(dto, time.max)

    # 대상 채널코드 결정
    target_codes = [
        code for code, (comp, _) in _CHANNEL_META.items()
        if company == "ALL" or comp == company
    ]
    if not target_codes:
        return {"summary": {}, "by_product": []}

    # paid_at은 KST naive datetime으로 저장(_parse_paid_at에서 KST 변환 후 tzinfo 제거)
    # → 날짜 범위(start/end)를 그대로 사용 (UTC 보정 불필요)
    rg_start = start   # KST 00:00:00
    rg_end = end       # KST 23:59:59

    # ── 1. 주문 집계 (상품명·채널코드별) ──────────────────────────────
    order_rows = (
        db.query(
            Order.platform_product_id,
            func.max(Order.platform_product_name),
            Channel.code,
            func.sum(Order.selling_price * Order.quantity),
            func.sum(Order.quantity),
        )
        .join(Channel, Order.channel_id == Channel.id)
        .filter(
            Channel.code.in_(target_codes),
            Order.platform_product_id != "",
            Order.status.notin_(tuple(REVENUE_EXCLUDED)),
            Order.order_date >= start,
            Order.order_date <= end,
        )
        .group_by(Order.platform_product_id, Channel.code)
        .all()
    )

    # vid → {channel_code, revenue, qty, name(from order)}
    order_by_vid: dict[str, dict] = {}
    for vid, oname, ch_code, rev, qty in order_rows:
        key = str(vid)
        entry = order_by_vid.setdefault(key, {
            "channel_code": ch_code, "revenue": _Z, "qty": 0, "order_name": oname
        })
        entry["revenue"] += _f(rev)
        entry["qty"] += int(qty or 0)

    # ── 1b. RG 주문 집계 (coupang_rg_order_item) ─────────────────────
    if target_codes:
        rg_rows = (
            db.query(
                CoupangRgOrderItem.vendor_item_id,
                func.max(CoupangRgOrderItem.product_name),
                CoupangRgOrderItem.account_key,
                func.sum(
                    CoupangRgOrderItem.unit_sales_price * CoupangRgOrderItem.sales_quantity
                ),
                func.sum(CoupangRgOrderItem.sales_quantity),
            )
            .filter(
                CoupangRgOrderItem.account_key.in_(target_codes),  # WING코드로 저장된 경우도 포함
                CoupangRgOrderItem.paid_at >= rg_start,            # UTC 보정
                CoupangRgOrderItem.paid_at <= rg_end,
            )
            .group_by(CoupangRgOrderItem.vendor_item_id, CoupangRgOrderItem.account_key)
            .all()
        )
        for vid, rg_name, acc_key, rev, qty in rg_rows:
            key = str(vid)
            # coupang_rg_order_item은 RG 전용 테이블 → 채널타입 강제 로켓그로스
            rg_ch = acc_key.replace("WING", "RG") if "RG" not in acc_key else acc_key
            entry = order_by_vid.setdefault(key, {
                "channel_code": rg_ch, "revenue": _Z, "qty": 0, "order_name": rg_name
            })
            entry["revenue"] += _f(rev)
            entry["qty"] += int(qty or 0)

    # ── 2. 광고 집계 (옵션ID별) ──────────────────────────────────────
    # vendor_id(A01564720 등) → target channel 코드 매핑으로 필터
    target_vendor_ids = {
        cfg.vendor_id
        for code in target_codes
        if (cfg := _safe_cfg(code)) is not None
    }
    ad_filter = (
        [CoupangAdOptionDaily.vendor_id.in_(list(target_vendor_ids))]
        if target_vendor_ids else []
    )

    # 오늘(days=0) 선택 시: 광고비 XLSX는 당일 업로드 전이므로 DB 최신 report_date 사용
    ad_dfrom, ad_dto = dfrom, dto
    ad_ref_date: str | None = None
    if days == 0:
        latest_ad = db.query(func.max(CoupangAdOptionDaily.report_date)).scalar()
        if latest_ad:
            ad_dfrom = ad_dto = latest_ad
            ad_ref_date = str(latest_ad)

    ad_rows = (
        db.query(
            CoupangAdOptionDaily.ad_option_id,
            CoupangAdOptionDaily.vendor_id,
            func.sum(CoupangAdOptionDaily.ad_spend),
            func.sum(CoupangAdOptionDaily.conversion_revenue),
        )
        .filter(
            CoupangAdOptionDaily.report_date >= ad_dfrom,
            CoupangAdOptionDaily.report_date <= ad_dto,
            *ad_filter,
        )
        .group_by(CoupangAdOptionDaily.ad_option_id, CoupangAdOptionDaily.vendor_id)
        .all()
    )
    ad_by_vid: dict[str, dict] = {
        str(vid): {"vendor_id": vendor_id, "spend": _f(spend), "conv_revenue": _f(conv)}
        for vid, vendor_id, spend, conv in ad_rows
    }

    # ── 3. 상품명·수수료율 조회 (coupang_product_item) ───────────────────
    all_vids = set(order_by_vid) | set(ad_by_vid)
    pi_rows = (
        db.query(CoupangProductItem.vendor_item_id,
                 CoupangProductItem.seller_product_name,
                 CoupangProductItem.item_name,
                 CoupangProductItem.account_key,
                 CoupangProductItem.sale_agent_commission)
        .filter(CoupangProductItem.vendor_item_id.in_(list(all_vids)))
        .all()
    ) if all_vids else []
    pi_map = {
        str(r.vendor_item_id): {
            "seller_name": r.seller_product_name,
            "item_name": r.item_name,
            "account_key": r.account_key,
        }
        for r in pi_rows
    }

    # ── 3a. 수수료 직접 매칭 (orders.order_number ↔ coupang_revenue_fee.order_id) ──
    # 기간 내 쿠팡 Wing 주문의 order_number 수집 (ORM으로 IN 처리)
    order_nums = [
        row[0] for row in (
            db.query(Order.order_number)
            .join(Channel, Order.channel_id == Channel.id)
            .filter(
                Channel.code.in_(target_codes),
                Order.order_date >= start,
                Order.order_date <= end,
            )
            .distinct()
            .all()
        )
    ] if target_codes else []

    # vendor_item_id별 실거래 수수료 합산 (order_number 매칭)
    actual_fee_by_vid: dict[str, Decimal] = {}
    if order_nums:
        fee_matched = (
            db.query(
                CoupangRevenueFee.vendor_item_id,
                func.sum(CoupangRevenueFee.service_fee + CoupangRevenueFee.service_fee_vat),
            )
            .filter(CoupangRevenueFee.order_id.in_(list(order_nums)))
            .group_by(CoupangRevenueFee.vendor_item_id)
            .all()
        )
        actual_fee_by_vid = {str(vid): _f(fee) for vid, fee in fee_matched}

    # 매칭 안 된 주문 — 기본율(최빈 수수료율 7.8%)로 추정 표시용 fee_rate_map 유지
    fee_rate_map: dict[str, Decimal] = {}  # 직접 매칭 없는 옵션 추정용

    # ── 3b. 원가 조회 (D-12: product_master via product_channel_mapping) ──
    cost_rows = (
        db.query(ProductChannelMapping.channel_product_id, ProductMaster.cost_price, ProductMaster.id)
        .join(ProductMaster, ProductChannelMapping.product_id == ProductMaster.id)
        .join(Channel, ProductChannelMapping.channel_id == Channel.id)
        .filter(
            Channel.platform == "coupang",
            ProductChannelMapping.is_active.is_(True),
            ProductChannelMapping.channel_product_id.in_(list(all_vids)),
        )
        .all()
    ) if all_vids else []
    # 원가>0 우선, 동률이면 product_id 최소 (D-12 결정적 선택)
    cost_candidates: dict[str, list] = {}
    for cpid, cp, pid in cost_rows:
        cost_candidates.setdefault(str(cpid), []).append((cp, pid))
    cost_map: dict[str, Decimal] = {}
    for vid, cands in cost_candidates.items():
        costed = [(cp, pid) for cp, pid in cands if cp and cp > 0]
        chosen_cost = min(costed or cands, key=lambda x: x[1])[0]
        if chosen_cost:
            cost_map[vid] = _f(chosen_cost)

    # ── 4. 상품명·채널타입 단위로 병합 ───────────────────────────────
    # key = (product_label, channel_type)
    merged: dict[tuple[str, str], dict] = {}

    def _resolve_names(vid: str) -> tuple[str, str] | None:
        """옵션ID → (상품명, 옵션명). 상품명 없으면 None (테이블에서 제외)."""
        pi = pi_map.get(vid, {})
        order_entry = order_by_vid.get(vid, {})
        product = pi.get("seller_name") or order_entry.get("order_name")
        option = pi.get("item_name") or ""
        return (product, option) if product else None

    def _ch_type(ch_code: str) -> str:
        return _CHANNEL_META.get(ch_code, ("—", "Wing"))[1]

    for vid, od in order_by_vid.items():
        names = _resolve_names(vid)
        if not names:
            continue
        product, option = names
        ch_code = od["channel_code"]
        ch_type = _ch_type(ch_code)
        pk = (product, option, ch_type)
        e = merged.setdefault(pk, {"revenue": _Z, "ad_spend": _Z, "conv_revenue": _Z,
                                    "fee": _Z, "cost": _Z, "shipping": _Z})
        rev = od["revenue"]
        qty = od["qty"]
        e["revenue"] += rev
        # 수수료: 실거래 정산 매칭 우선, 없으면 기본율(7.8%) 추정
        if vid in actual_fee_by_vid:
            e["fee"] += actual_fee_by_vid[vid]
        else:
            fee_rate = fee_rate_map.get(vid, _DEFAULT_FEE_RATE)
            e["fee"] += (rev * fee_rate).quantize(_Q2, rounding=ROUND_HALF_UP)
        # 원가 = cost_price × 수량
        unit_cost = cost_map.get(vid, _Z)
        e["cost"] += unit_cost * qty
        # 배송비 = Wing만 1,900원/건
        if ch_type == "Wing":
            e["shipping"] += _WING_DELIVERY_COST * qty

    for vid, ad in ad_by_vid.items():
        names = _resolve_names(vid)
        if not names:
            continue
        product, option = names
        pi = pi_map.get(vid, {})
        ch_code = pi.get("account_key") or _vendor_to_channel(ad.get("vendor_id", ""))
        pk = (product, option, _ch_type(ch_code))
        e = merged.setdefault(pk, {"revenue": _Z, "ad_spend": _Z, "conv_revenue": _Z,
                                    "fee": _Z, "cost": _Z, "shipping": _Z})
        e["ad_spend"] += ad["spend"]
        e["conv_revenue"] += ad["conv_revenue"]

    # ── 5. 요약 합계 ─────────────────────────────────────────────────
    total_rev   = sum((_f(od["revenue"]) for od in order_by_vid.values()), _Z)
    total_spend = sum((_f(ad["spend"]) for ad in ad_by_vid.values()), _Z)
    total_conv  = sum((_f(ad["conv_revenue"]) for ad in ad_by_vid.values()), _Z)

    # 광고비 폴백: XLSX(CoupangAdOptionDaily)가 해당 기간에 없으면(미업로드) →
    # report/SALES 확정값(coupang_ad_cost_daily)으로 카드 광고비·전환매출 대체.
    # 이 테이블은 오픽스(advertiser) 단위라 company가 오픽스/ALL일 때만 적용(오하이테크는 미수집).
    if total_spend == _Z and company in ("ALL", "오픽스"):
        fb_rows = ad_cost_sync.get_ad_cost_range(db, dfrom, dto)
        fb_spend = sum((Decimal(str(r["day_cost"])) for r in fb_rows), _Z)
        fb_conv = sum((Decimal(str(r.get("conv_sales") or 0)) for r in fb_rows), _Z)
        if fb_spend:
            total_spend = fb_spend
            total_conv = fb_conv
            # ad_ref_date는 건드리지 않는다 — 프론트가 비-null이면 'today-only/최신업로드'
            # 레이아웃으로 분기(CoupangOps.tsx). 폴백은 같은 기간 일반 카드에 투명 반영(codex P2).

    total_fee   = sum((v["fee"]      for v in merged.values()), _Z)
    total_cost  = sum((v["cost"]     for v in merged.values()), _Z)
    total_ship  = sum((v["shipping"] for v in merged.values()), _Z)
    total_profit = total_rev - total_fee - total_cost - total_spend - total_ship
    profit_rate  = (total_profit / total_rev * 100).quantize(_Q2) if total_rev else None

    def _profit(v: dict) -> Decimal:
        return v["revenue"] - v["fee"] - v["cost"] - v["ad_spend"] - v["shipping"]

    # ── 6. 최종 응답 ─────────────────────────────────────────────────
    by_product = []
    for pk, v in sorted(merged.items(), key=lambda x: -x[1]["revenue"]):
        profit = _profit(v)
        rev = v["revenue"]
        by_product.append({
            "product_name": pk[0],
            "option_name": pk[1],
            "channel_type": pk[2],
            "revenue":      str(v["revenue"].quantize(_Q2)),
            "fee":          str(v["fee"].quantize(_Q2)),
            "cost":         str(v["cost"].quantize(_Q2)),
            "ad_spend":     str(v["ad_spend"].quantize(_Q2)),
            "shipping":     str(v["shipping"].quantize(_Q2)),
            "profit":       str(profit.quantize(_Q2)),
            "profit_rate":  str((profit / rev * 100).quantize(_Q2)) if rev else None,
            "conv_revenue": str(v["conv_revenue"].quantize(_Q2)),
            "roas":         _roas(v["ad_spend"], v["conv_revenue"]),
        })

    return {
        "period": {"from": str(dfrom), "to": str(dto)},
        "ad_ref_date": ad_ref_date,
        "summary": {
            "revenue":      str(total_rev.quantize(_Q2)),
            "fee":          str(total_fee.quantize(_Q2)),
            "cost":         str(total_cost.quantize(_Q2)),
            "ad_spend":     str(total_spend.quantize(_Q2)),
            "shipping":     str(total_ship.quantize(_Q2)),
            "profit":       str(total_profit.quantize(_Q2)),
            "profit_rate":  str(profit_rate) if profit_rate is not None else None,
            "conv_revenue": str(total_conv.quantize(_Q2)),
            "roas":         _roas(total_spend, total_conv),
        },
        "by_product": by_product,
    }


# ════════════════════════════════════════════════
# RG 입고(inbound) — Wing 세션쿠키 (트랙 RG-Replenishment S1, D-1/D-5)
# 라우터는 rg_inbound_sync Harness만 호출(원칙 18-7). 쿠키 민감값은 응답에 노출 안 함.
# ════════════════════════════════════════════════
@router.post("/inbound/cookie")
def save_inbound_cookie(
    body: dict[str, Any] = Body(...), db: Session = Depends(get_db)
):
    """Wing 세션쿠키 저장 — body {account_key, curl}. cURL 통째 붙여넣기 → 쿠키·xsrf 추출·Fernet 암호화.

    account_key 미지정 시 COUPANG_WING1. parse 실패·암호화키 부재는 400. 저장 직후 status=unknown."""
    account_key = (body.get("account_key") or "COUPANG_WING1").strip()
    curl = body.get("curl") or body.get("cookie") or ""
    try:
        return rg_inbound_sync.save_cookie(db, account_key, curl)
    except (ValueError, CookieCryptoError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/inbound/cookie/status")
def inbound_cookie_status(db: Session = Depends(get_db)):
    """계정별 쿠키 설정·상태(🟢green/🔴red/unknown/none) + 마지막 저장·성공 시각(만료 측정)."""
    return {"accounts": rg_inbound_sync.cookie_status(db)}


@router.post("/inbound/sync")
def trigger_inbound_sync(
    account_key: str | None = Query(None), db: Session = Depends(get_db)
):
    """입고 동기화 즉시 실행(수동 트리거). account_key 지정 시 단일, 미지정 시 전 계정."""
    if account_key:
        results = [rg_inbound_sync.sync_account_inbound(db, account_key)]
    else:
        results = rg_inbound_sync.sync_all_inbound(db)
    return {"results": results}


@router.get("/inbound")
def list_inbound(
    db: Session = Depends(get_db), limit: int = Query(100, ge=1, le=500)
):
    """적재된 입고 이력 조회(확인/디버그용). 최신 판매개시(stowing) 순."""
    rows = (
        db.query(CoupangRgInbound)
        .order_by(CoupangRgInbound.stowing_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "count": len(rows),
        "items": [
            {
                "account_key": r.account_key,
                "inbound_id": r.inbound_id,
                "vendor_item_id": r.vendor_item_id,
                "sku_name": r.cached_sku_name,
                "requested_qty": r.requested_qty,
                "received_qty": r.received_qty,
                "stowed_qty": r.stowed_qty,
                "shipment_created_at": r.shipment_created_at.isoformat() if r.shipment_created_at else None,
                "stowing_at": r.stowing_at.isoformat() if r.stowing_at else None,
                "lead_time_days": float(r.lead_time_days) if r.lead_time_days is not None else None,
            }
            for r in rows
        ],
    }


# ──────────────────────────────────────────────
# RG 리드타임 추정 (트랙 RG-Replenishment S2) — 읽기 전용 SA 직접 호출(원칙 18-7 조회 예외)
# ──────────────────────────────────────────────
@router.get("/lead-times")
def get_lead_times(
    db: Session = Depends(get_db),
    account_key: str | None = Query(None, description="특정 셀러계정만(미지정=전체)"),
):
    """옵션별 발송→판매개시 리드타임 분포 + 글로벌 분포(검증/UI용).

    옵션 표본 부족 시 글로벌 폴백(source 필드로 구분). S4 replenishment_calc가 estimate_lead_time() 사용."""
    return lead_time_estimator.estimate_lead_times(db, account_key)


# ──────────────────────────────────────────────
# RG 일판매속도 추정 (트랙 RG-Replenishment S3, D-3·D-6) — 읽기 전용 SA 직접 호출(원칙 18-7 조회 예외)
# ──────────────────────────────────────────────
@router.get("/sales-velocity")
def get_sales_velocity(
    db: Session = Depends(get_db),
    account_key: str | None = Query(None, description="특정 셀러계정만(미지정=전체)"),
):
    """옵션별 일판매속도(평일/주말/휴일 구간) + 글로벌 요일계수(검증/UI용).

    base_rate = order_item(관측일 충분) → sold_30d/30 폴백. 요일계수는 신뢰도 게이트(표본 임계 넘으면
    자동 승격, 그 전엔 collecting·factor 1.0). S4 replenishment_calc가 estimate_sales_velocity() 사용."""
    return sales_velocity_estimator.estimate_sales_velocities(db, account_key)


# ──────────────────────────────────────────────
# RG 발송관제 계획 (트랙 RG-Replenishment S5) — Harness 경유(원칙 18-7, 3 SA 오케스트레이션)
# ──────────────────────────────────────────────
@router.get("/replenishment-plan")
def get_replenishment_plan(
    db: Session = Depends(get_db),
    account_key: str | None = Query(None, description="특정 셀러계정만(미지정=전체)"),
    target_days: int = Query(3, ge=1, le=14, description="FC 목표 보관 일수(D-2: 2~3일치, 기본 3)"),
):
    """현재고 보유 옵션 전체의 권장 발송일·수량(배치 역산) + status별 집계.

    속도·리드타임을 각 1회 산출해 옵션별 calc에 주입(원칙 18-8). 단순 조회가 아니라 3개 SA를
    가로지르는 오케스트레이션이므로 rg_replenishment Harness를 경유한다(원칙 18-7)."""
    return rg_replenishment.build_replenishment_plan(db, account_key, target_days=target_days)


# ════════════════════════════════════════════════
# 쿠팡 광고비 — advertising.coupang.com Wing 내부 API (S0 실증 2026-06-05)
# ════════════════════════════════════════════════
@router.post("/ad-cost/cookie")
def save_ad_cookie(
    body: dict[str, Any] = Body(...), db: Session = Depends(get_db)
):
    """advertising.coupang.com 세션쿠키 저장 — body {curl}. cURL 통째 붙여넣기 → 쿠키 추출·Fernet 암호화."""
    curl = body.get("curl") or body.get("cookie") or ""
    try:
        return ad_cost_sync.save_cookie(db, curl)
    except (ValueError, CookieCryptoError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/ad-cost/cookie/status")
def ad_cookie_status(db: Session = Depends(get_db)):
    """광고 쿠키 설정·상태(🟢green/🔴red/unknown/none)."""
    return ad_cost_sync.cookie_status(db)


@router.post("/ad-cost/sync")
def trigger_ad_cost_sync(db: Session = Depends(get_db)):
    """광고비 즉시 sync (수동 트리거)."""
    return ad_cost_sync.sync_ad_cost(db)


@router.post("/ad-cost/ingest")
def ingest_ad_cost(
    body: dict[str, Any] = Body(...),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """로컬 페처(Jino Mac, residential IP) → 광고비 숫자 인제스트.

    advertising.coupang.com은 Akamai가 데이터센터 IP 차단 → prod 직접 fetch 불가.
    인증: X-Ingest-Token 헤더 == 환경변수 AD_INGEST_TOKEN. body {date, vendors:[...]}.
    """
    import secrets as _secrets

    expected = os.getenv("AD_INGEST_TOKEN", "").strip()
    # 상수시간 비교(codex P2). 미설정·불일치 모두 동일한 401(서버 설정 상태 비노출).
    if not expected or not x_ingest_token or not _secrets.compare_digest(x_ingest_token.strip(), expected):
        raise HTTPException(status_code=401, detail="unauthorized")

    # 신규 경로: report/SALES 날짜별 확정값 — body {days:[{date,ad_spend,conv_sales}]}.
    days_raw = body.get("days")
    if isinstance(days_raw, list) and days_raw:
        days: list[dict] = []
        for d in days_raw:
            if not isinstance(d, dict):
                raise HTTPException(status_code=400, detail="days[] 항목은 객체여야 함")
            try:
                day_date = date.fromisoformat(str(d.get("date")))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="days[].date(YYYY-MM-DD) 필요")
            try:
                ad_spend = int(d.get("ad_spend") or 0)
                conv_sales = int(d.get("conv_sales") or 0)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="ad_spend/conv_sales는 정수여야 함")
            if ad_spend < 0 or conv_sales < 0:
                raise HTTPException(status_code=400, detail="값은 음수일 수 없음")
            days.append({"date": day_date, "ad_spend": ad_spend, "conv_sales": conv_sales})
        return ad_cost_sync.ingest_ad_cost_days(db, days)

    # 구 경로(back-compat): report/cost 단일일 vendors[].
    try:
        cost_date = date.fromisoformat(str(body.get("date")))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="date(YYYY-MM-DD) 또는 days[] 필요")
    raw = body.get("vendors")
    if not isinstance(raw, list) or not raw:
        raise HTTPException(status_code=400, detail="vendors[] 필요")
    # 검증: vendor_id 존재, 비용은 음수 아닌 정수(malformed → 400, codex P2)
    vendors: list[dict] = []
    for v in raw:
        if not isinstance(v, dict) or not str(v.get("vendor_id") or "").strip():
            raise HTTPException(status_code=400, detail="vendor_id 필요")
        try:
            day_cost = int(v.get("day_cost") or 0)
            month_cost = int(v.get("month_cost") or 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="비용은 정수여야 함")
        if day_cost < 0 or month_cost < 0:
            raise HTTPException(status_code=400, detail="비용은 음수일 수 없음")
        vendors.append({"vendor_id": str(v["vendor_id"]).strip(),
                        "day_cost": day_cost, "month_cost": month_cost})
    return ad_cost_sync.ingest_ad_cost(db, cost_date, vendors)


@router.post("/ad-cost/request-refresh")
def request_ad_cost_refresh(db: Session = Depends(get_db)):
    """대시보드 '광고비 갱신' 버튼 → 갱신 요청 플래그 set.

    광고비는 Akamai로 prod 직접 fetch 불가 → Jino Mac 페처가 가져온다. 이 플래그를 보고
    Mac 데몬이 다음 폴링에서 headful fetch를 1회 수행한다("볼 때만 클릭" 방식).
    """
    return ad_cost_sync.request_refresh(db)


@router.get("/ad-cost/refresh-status")
def ad_cost_refresh_status(db: Session = Depends(get_db)):
    """갱신 요청/완료 상태. 대시보드(버튼 후 폴링)·Mac 페처(요청 확인) 공용."""
    return ad_cost_sync.refresh_status(db)


@router.post("/ad-cost/refresh-claim")
def claim_ad_cost_refresh(
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Mac 페처가 갱신 요청을 소비(플래그 clear)하고 작업 시작. 토큰 인증(ingest와 동일)."""
    import secrets as _secrets

    expected = os.getenv("AD_INGEST_TOKEN", "").strip()
    if not expected or not x_ingest_token or not _secrets.compare_digest(x_ingest_token.strip(), expected):
        raise HTTPException(status_code=401, detail="unauthorized")
    return ad_cost_sync.claim_refresh(db)


@router.get("/ad-cost")
def get_ad_cost(
    db: Session = Depends(get_db),
    start: date = Query(..., description="시작일 YYYY-MM-DD"),
    end: date = Query(..., description="종료일 YYYY-MM-DD"),
):
    """날짜 범위 광고비 조회 (일별 합산)."""
    return {"costs": ad_cost_sync.get_ad_cost_range(db, start, end)}
