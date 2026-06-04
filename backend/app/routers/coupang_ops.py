# coupang_ops.py — 쿠팡 쓰기 라우터 (트랙 §5 Layer3 coupang_ops, D-16 쓰기 페이즈).
# W3a 상품 단순쓰기 9개(재고·가격·할인율기준가·판매중지/재개·자동생성옵션 활성/비활성).
# 전부 dry_run=true 기본 — 라이브 실행은 dry_run=false + confirm 토큰 필요(이중확인).
# 변경값은 query param(쿠팡 원 API가 path/query, body 없음 — 명세 02 §4). Harness가 dry_run 게이트.
# 트랙 D-8: 쿠팡 Open API는 서버 IP 화이트리스트 → 라이브 호출은 서버에서만(로컬 403).
from __future__ import annotations

import logging

from typing import Any

from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_coupang_config
from app.database import get_db
from app.models import Channel, CoupangAdOptionDaily, CoupangProductItem, CoupangRgOrderItem, Order
from app.routers._coupang_write_http import handle_write as _handle_write
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED
from app.services.coupang import coupon_write, product_write

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

_KST = ZoneInfo("Asia/Seoul")
_Q2 = Decimal("0.01")
_Z = Decimal("0")

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


def _kst_today() -> date:
    return datetime.now(_KST).date()


def _date_range(days: int) -> tuple[date, date]:
    today = _kst_today()
    if days == 1:
        d = today - timedelta(days=1)
        return d, d
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
    days: int = Query(default=7, ge=1, le=90),
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

    # paid_at은 UTC 저장 → KST 기준 날짜 범위를 UTC로 변환
    _UTC_OFFSET = timedelta(hours=9)
    rg_start = start - _UTC_OFFSET   # KST 자정 → UTC 전날 15:00
    rg_end = end - _UTC_OFFSET       # KST 23:59 → UTC 당일 14:59

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
    ad_rows = (
        db.query(
            CoupangAdOptionDaily.ad_option_id,
            CoupangAdOptionDaily.vendor_id,
            func.sum(CoupangAdOptionDaily.ad_spend),
            func.sum(CoupangAdOptionDaily.conversion_revenue),
        )
        .filter(
            CoupangAdOptionDaily.report_date >= dfrom,
            CoupangAdOptionDaily.report_date <= dto,
            *ad_filter,
        )
        .group_by(CoupangAdOptionDaily.ad_option_id, CoupangAdOptionDaily.vendor_id)
        .all()
    )
    ad_by_vid: dict[str, dict] = {
        str(vid): {"vendor_id": vendor_id, "spend": _f(spend), "conv_revenue": _f(conv)}
        for vid, vendor_id, spend, conv in ad_rows
    }

    # ── 3. 상품명 조회 (coupang_product_item) ─────────────────────────
    all_vids = set(order_by_vid) | set(ad_by_vid)
    pi_rows = (
        db.query(CoupangProductItem.vendor_item_id,
                 CoupangProductItem.seller_product_name,
                 CoupangProductItem.item_name,
                 CoupangProductItem.account_key)
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

    # ── 4. 상품명·채널타입 단위로 병합 ───────────────────────────────
    # key = (product_label, channel_type)
    merged: dict[tuple[str, str], dict] = {}

    def _resolve_name(vid: str) -> str | None:
        """옵션ID로 상품명을 찾는다. 못 찾으면 None 반환 (테이블에서 제외)."""
        pi = pi_map.get(vid, {})
        order_entry = order_by_vid.get(vid, {})
        return (
            pi.get("seller_name")
            or pi.get("item_name")
            or order_entry.get("order_name")  # platform_product_name 폴백
        )

    def _ch_type(ch_code: str) -> str:
        return _CHANNEL_META.get(ch_code, ("—", "Wing"))[1]

    for vid, od in order_by_vid.items():
        name = _resolve_name(vid)
        if not name:
            continue  # 이름 없는 행은 테이블에서 제외
        ch_code = od["channel_code"]
        pk = (name, _ch_type(ch_code))
        e = merged.setdefault(pk, {"revenue": _Z, "ad_spend": _Z, "conv_revenue": _Z})
        e["revenue"] += od["revenue"]

    for vid, ad in ad_by_vid.items():
        name = _resolve_name(vid)
        if not name:
            continue  # 이름 모르는 구버전 옵션은 테이블에서 제외
        pi = pi_map.get(vid, {})
        ch_code = pi.get("account_key") or _vendor_to_channel(ad.get("vendor_id", ""))
        pk = (name, _ch_type(ch_code))
        e = merged.setdefault(pk, {"revenue": _Z, "ad_spend": _Z, "conv_revenue": _Z})
        e["ad_spend"] += ad["spend"]
        e["conv_revenue"] += ad["conv_revenue"]

    # ── 5. 요약 합계: 원본 집계치 사용 (RG 포함, 이름 없는 행 제외해도 요약 정확) ──
    total_rev = sum((_f(od["revenue"]) for od in order_by_vid.values()), _Z)
    total_spend = sum((_f(ad["spend"]) for ad in ad_by_vid.values()), _Z)
    total_conv = sum((_f(ad["conv_revenue"]) for ad in ad_by_vid.values()), _Z)

    # ── 6. 최종 응답 ─────────────────────────────────────────────────
    by_product = [
        {
            "product_name": pk[0],
            "channel_type": pk[1],
            "revenue": str(v["revenue"].quantize(_Q2)),
            "ad_spend": str(v["ad_spend"].quantize(_Q2)),
            "conv_revenue": str(v["conv_revenue"].quantize(_Q2)),
            "roas": _roas(v["ad_spend"], v["conv_revenue"]),
        }
        for pk, v in sorted(merged.items(), key=lambda x: -x[1]["revenue"])
    ]

    return {
        "period": {"from": str(dfrom), "to": str(dto)},
        "summary": {
            "revenue": str(total_rev.quantize(_Q2)),
            "ad_spend": str(total_spend.quantize(_Q2)),
            "conv_revenue": str(total_conv.quantize(_Q2)),
            "roas": _roas(total_spend, total_conv),
        },
        "by_product": by_product,
    }
