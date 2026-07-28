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
import re

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Header, HTTPException, Path, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.clients.coupang.inbound import WingReadError
from app.config import get_coupang_config
from app.database import get_db
from app.models import Channel, CoupangAdCostDaily, CoupangAdOptionDaily, CoupangProductItem, CoupangRevenueFee, CoupangRgInbound, CoupangRgOrderItem, CoupangRgSettlementFee, CoupangRocketPromotion, Order, ProductChannelMapping, ProductMaster
from app.routers._coupang_write_http import handle_write as _handle_write
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED
from app.services.coupang import ad_cost_sync, coupon_write, refresh_contract, coupang_seller_cost as _seller_cost_harness, demand_classifier, in_transit_estimator, lead_time_estimator, ohitech_ad_sync, product_write, rg_fee_audit, rg_inbound_sync, rg_product_size_sync, rg_replenishment, rg_settlement_sync, replenishment_backtest, rocket_cost_map, rocket_promo_sync, rocket_supplier_sync, rg_cost_reader as _rg_reader, sales_velocity_estimator, vendor_summary_sync
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

# 회사 → RG 재고가 적재된 셀러 계정(account_key). RG 재고/입고는 셀러 WING 계정 단위로
# 동기화되므로(실측: CoupangRgInventory.account_key ∈ {COUPANG_WING1, COUPANG_WING2}),
# 발송관제 회사 필터는 그 회사의 Wing 계정으로 변환한다. _CHANNEL_META에서 파생(단일 진실원천).
_RG_WING_PAIRS = [(comp, code) for code, (comp, ch_type) in _CHANNEL_META.items() if ch_type == "Wing"]
_RG_ACCOUNT_BY_COMPANY: dict[str, str] = dict(_RG_WING_PAIRS)
# codex P2: 한 회사에 Wing 계정이 2개면 dict가 조용히 덮어써 매핑이 모호해짐 → 1대1 단언.
assert len(_RG_ACCOUNT_BY_COMPANY) == len(_RG_WING_PAIRS), "한 회사에 Wing 계정 2개 — RG 계정 매핑 모호"


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


def _rg_fulfillment_per_unit(
    db: Session, account_keys: list[str]
) -> tuple[dict[str, Decimal], Decimal]:
    """옵션별 RG 풀필먼트(배송+입출고) 건당 단가 추정.

    = Σ(정산 옵션단위 delivery+warehousing) / Σ(같은 정산 날짜범위 RG 판매수량).
    ★배경(라이브 확정): RG는 판매수수료(CATEGORY_TR 라이브=7.8%) 외에 풀필먼트비가 매출의
    ~16%인데, 운영 패널이 RG 배송비를 0으로 둬 통째로 누락 → RG 이익 과대. 옵션별 실측 단가로
    보강한다. 오늘 주문은 아직 미정산이라 과거 실측 단가를 적용(=추정, 0보다 정확).
    반환: ({vid: 건당단가}, 계정 평균 폴백 단가). 데이터 없으면 ({}, 0).
    """
    if not account_keys:
        return {}, _Z
    _ff_filter = (
        CoupangRgSettlementFee.account_key.in_(account_keys),
        CoupangRgSettlementFee.vendor_item_id.isnot(None),  # codex P2: null 명시 제외
        CoupangRgSettlementFee.vendor_item_id != "",
        CoupangRgSettlementFee.fee_type.in_(["delivery", "warehousing"]),
    )
    ff_rows = (
        db.query(CoupangRgSettlementFee.vendor_item_id, func.sum(CoupangRgSettlementFee.amount))
        .filter(*_ff_filter)
        .group_by(CoupangRgSettlementFee.vendor_item_id)
        .all()
    )
    ff_by_vid = {str(v): _f(a) for v, a in ff_rows if a}
    if not ff_by_vid:
        return {}, _Z
    d_from, d_to = (
        db.query(
            func.min(CoupangRgSettlementFee.recognition_date_from),
            func.max(CoupangRgSettlementFee.recognition_date_to),
        )
        .filter(*_ff_filter)
        .first()
    )
    qty_by_vid: dict[str, int] = {}
    if d_from and d_to:
        q_rows = (
            db.query(CoupangRgOrderItem.vendor_item_id, func.sum(CoupangRgOrderItem.sales_quantity))
            .filter(
                CoupangRgOrderItem.account_key.in_(account_keys),
                func.date(CoupangRgOrderItem.paid_at) >= d_from.isoformat(),
                func.date(CoupangRgOrderItem.paid_at) <= d_to.isoformat(),
            )
            .group_by(CoupangRgOrderItem.vendor_item_id)
            .all()
        )
        qty_by_vid = {str(v): int(q or 0) for v, q in q_rows}
    per_unit: dict[str, Decimal] = {}
    tot_ff, tot_qty = _Z, 0
    for vid, ff in ff_by_vid.items():
        q = qty_by_vid.get(vid, 0)
        if q > 0:
            per_unit[vid] = (ff / Decimal(q)).quantize(_Q2)
            tot_ff += ff
            tot_qty += q
    fallback = (tot_ff / Decimal(tot_qty)).quantize(_Q2) if tot_qty else _Z
    if ff_by_vid and tot_qty == 0:  # codex P2: 수수료는 있는데 수량 0 → 폴백 0으로 RG 풀필먼트 소실 경고
        log.warning("RG 풀필먼트 단가 산출 실패(정산 옵션 수수료는 있으나 RG 수량 0): %s", account_keys)
    return per_unit, fallback


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
            func.sum(Order.selling_price),  # selling_price=orderPrice=라인총액(이미 ×수량) — 2중계상 방지(S2)
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

    # ── 3c. RG 풀필먼트 건당 단가(배송+입출고 실측, 옵션별) ──────────────
    # RG는 판매수수료(7.8%) 외 풀필먼트비가 매출 ~16%인데 패널이 0으로 누락 → 실측 단가로 보강.
    rg_acct_keys = [c for c in target_codes if c in ("COUPANG_WING1", "COUPANG_WING2")]
    rg_ff_per_unit, rg_ff_fallback = _rg_fulfillment_per_unit(db, rg_acct_keys)

    # ── 3d. D-18 판매유형별 쿠팡 총비용 — 배치 로드 (N+1 방지) ─────────
    # service_fee_ratio: 3P 캐스케이드 step2용 (실측 없는 옵션의 옵션별 율)
    ratio_by_vid: dict[str, Decimal] = {}
    if order_nums:
        ratio_rows = (
            db.query(
                CoupangRevenueFee.vendor_item_id,
                func.max(CoupangRevenueFee.service_fee_ratio),
            )
            .filter(CoupangRevenueFee.order_id.in_(list(order_nums)))
            .group_by(CoupangRevenueFee.vendor_item_id)
            .all()
        )
        # service_fee_ratio는 DB에 퍼센트값(e.g. 7.80)으로 저장 → /100으로 소수 변환
        ratio_by_vid = {str(vid): (_f(r) / Decimal("100")) for vid, r in ratio_rows if r}

    # RG 정산 옵션 그레인 (vendor_item_id != "", VAT前 A-B) — 공유 리더
    settled_option_costs = _rg_reader.load_settled_option_costs(
        db, dfrom, dto, rg_acct_keys
    )
    # 계정 단위 보관료 (per-unit 배분 불가, coverage 표기용)
    account_storage_map = _rg_reader.load_account_storage(db, dfrom, dto, rg_acct_keys)
    # 닫힌 윈도우 RG광고 비율 (미정산 추정)
    rg_ad_rate_map = _rg_reader.load_rg_ad_rate(db, dfrom, dto, rg_acct_keys)
    # RG광고 이중계상 감시 (정상=0)
    from app.services.coupang.intelligence import _agg_rg_ad_overlap
    _rg_ad_overlap = _agg_rg_ad_overlap(db, dfrom, dto)

    # vid → account_key (pi_map 기반 배치)
    account_by_vid = {vid: d.get("account_key", "") for vid, d in pi_map.items()}

    # vid → ch_type (order_by_vid 기반, _ch_type은 step4에서 정의되므로 인라인)
    ch_type_by_vid: dict[str, str] = {
        vid: _CHANNEL_META.get(od["channel_code"], ("—", "Wing"))[1]
        for vid, od in order_by_vid.items()
    }

    # ── 3e. D-18 Harness 실행 (배치) ─────────────────────────────────
    seller_cost_by_vid = _seller_cost_harness.compute_batch(
        list(order_by_vid.keys()),
        ch_type_by_vid=ch_type_by_vid,
        revenue_by_vid={vid: _f(od["revenue"]) for vid, od in order_by_vid.items()},
        qty_by_vid={vid: od["qty"] for vid, od in order_by_vid.items()},
        account_by_vid=account_by_vid,
        actual_fee_by_vid=actual_fee_by_vid,
        ratio_by_vid=ratio_by_vid,
        settled_option=settled_option_costs,
        ff_per_unit=rg_ff_per_unit,
        ff_fallback=rg_ff_fallback,
        account_storage=account_storage_map,
        ad_rate=rg_ad_rate_map,
        overlap_rg_ad=_rg_ad_overlap,
    )

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
        # 수수료(D-18): 판매유형별 쿠팡 총비용 — Harness 우선, 없으면 legacy 7.8% 폴백
        sc = seller_cost_by_vid.get(vid)
        if sc is not None:
            e["fee"] += sc.seller_cost
        elif vid in actual_fee_by_vid:
            e["fee"] += actual_fee_by_vid[vid]
        else:
            e["fee"] += (rev * _DEFAULT_FEE_RATE).quantize(_Q2, rounding=ROUND_HALF_UP)
        # 원가 = cost_price × 수량
        unit_cost = cost_map.get(vid, _Z)
        e["cost"] += unit_cost * qty
        # 물류비: Wing=한진 1,900원/건. RG=풀필먼트는 fee(D-18)에 포함 → 0.
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

    # 광고비 폴백(날짜별): XLSX(CoupangAdOptionDaily)가 '없는 날짜'만 report/SALES
    # 확정값(coupang_ad_cost_daily)으로 보강. XLSX 있는 날은 그대로(상품별 표와 일관).
    # days=0(오늘=최신 XLSX 특수처리)은 제외. coupang_ad_cost_daily는 오픽스 단위→오픽스/ALL만.
    # ad_ref_date는 건드리지 않는다 — 비-null이면 프론트가 today-only 레이아웃으로 분기(codex P2).
    if days != 0 and company in ("ALL", "오픽스"):
        xlsx_dates = {
            r[0] for r in db.query(CoupangAdOptionDaily.report_date)
            .filter(
                CoupangAdOptionDaily.report_date >= ad_dfrom,
                CoupangAdOptionDaily.report_date <= ad_dto,
                *ad_filter,
            ).distinct().all()
        }
        for r in ad_cost_sync.get_ad_cost_range(db, dfrom, dto):
            if date.fromisoformat(r["date"]) in xlsx_dates:
                continue  # 그날은 XLSX 사용(상품별 분해 보존)
            total_spend += Decimal(str(r["day_cost"]))
            total_conv += Decimal(str(r.get("conv_sales") or 0))

    total_fee   = sum((v["fee"]      for v in merged.values()), _Z)
    total_cost  = sum((v["cost"]     for v in merged.values()), _Z)
    total_ship  = sum((v["shipping"] for v in merged.values()), _Z)
    total_profit = total_rev - total_fee - total_cost - total_spend - total_ship
    profit_rate  = (total_profit / total_rev * 100).quantize(_Q2) if total_rev else None

    # 오늘 실시간 광고비(일자 단위, coupang_ad_cost_daily=report/SALES, Mac 페처). days=0 전용.
    # 옵션별 XLSX(total_spend)는 익일만 존재하므로 '광고 현황' 카드는 오늘 총액을 메인으로 쓴다.
    # coupang_ad_cost_daily는 오픽스 광고계정 단위(L790 기존 폴백과 동일 전제) → 오픽스/ALL만 제공.
    # 오늘 전환매출·RoAS는 미확정(conv_sales는 익일 확정)이라 프론트가 '익일 확정'으로 표기.
    # ★stale 주의: ad_today는 Mac 페처가 마지막 fetch한 시점(synced_at)의 스냅샷이다.
    # 광고센터 '오늘 누적광고비'는 실시간으로 계속 쌓이므로, 마지막 갱신 이후 격차가 생긴다
    # (라이브 확인: 09:41 fetch 23,105 vs 16시대 누적 ~88,574). 프론트에 synced_at을 함께
    # 노출해 '실시간'으로 오인하지 않게 하고, '광고비 갱신' 버튼으로 재fetch하도록 안내한다.
    ad_today: Decimal | None = None
    ad_today_synced_at: str | None = None
    if days == 0 and company in ("ALL", "오픽스"):
        _today_rows = ad_cost_sync.get_ad_cost_range(db, dfrom, dto)
        ad_today = sum((Decimal(str(r["day_cost"])) for r in _today_rows), _Z)
        _synced = db.query(func.max(CoupangAdCostDaily.synced_at)).filter(
            CoupangAdCostDaily.cost_date == dfrom
        ).scalar()
        ad_today_synced_at = _synced.isoformat() if _synced else None

    # 광고비 제외 이익 — '오늘' 분리 레이아웃 카드용. days=0이면 광고비가 어제(최신 XLSX)
    # 종일치라, 오늘 매출에서 그걸 빼면 의미가 없다 → 광고비를 아예 제외한 이익을 따로 제공.
    profit_excl_ad = total_rev - total_fee - total_cost - total_ship
    profit_rate_excl_ad = (
        (profit_excl_ad / total_rev * 100).quantize(_Q2) if total_rev else None
    )

    # 원가·수수료 데이터 완성도(매출가중, 0~1). 오늘은 정산·매핑 지연으로 낮음(프론트 표기용).
    #   cost_coverage  : 원가 매핑(product_channel_mapping) 보유 매출 비율
    #   fee_actual_ratio: 실측 정산수수료(coupang_revenue_fee) 매칭 매출 비율(나머지는 7.8% 추정)
    rev_named = _Z
    rev_costed = _Z
    rev_fee_actual = _Z
    for _vid, _od in order_by_vid.items():
        if not _resolve_names(_vid):
            continue
        _rev = _od["revenue"]
        rev_named += _rev
        if _vid in cost_map:  # 매핑 보유 여부(키 존재). cost_map은 >0만 적재되나 의도 명시.
            rev_costed += _rev
        if _vid in actual_fee_by_vid:
            rev_fee_actual += _rev
    cost_coverage = float(rev_costed / rev_named) if rev_named else 1.0
    fee_actual_ratio = float(rev_fee_actual / rev_named) if rev_named else 1.0

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
            "profit_excl_ad": str(profit_excl_ad.quantize(_Q2)),
            "profit_rate_excl_ad": str(profit_rate_excl_ad) if profit_rate_excl_ad is not None else None,
            "cost_coverage": round(cost_coverage, 4),
            "fee_actual_ratio": round(fee_actual_ratio, 4),
            "ad_today": str(ad_today.quantize(_Q2)) if ad_today is not None else None,
            "ad_today_synced_at": ad_today_synced_at,
            "rg_fulfillment": "0.00",  # D-18 컷오버: 풀필먼트는 fee에 내장됨
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
    company: str | None = Query(None, description="회사 필터(오픽스/오하이테크/ALL). account_key보다 우선 — RG 재고 적재 계정으로 변환."),
    target_days: int = Query(7, ge=1, le=14, description="FC 목표 보관 일수(D-9: 1주일치, 기본 7)"),
):
    """현재고 보유 옵션 전체의 권장 발송일·수량(배치 역산) + status별 집계.

    속도·리드타임을 각 1회 산출해 옵션별 calc에 주입(원칙 18-8). 단순 조회가 아니라 3개 SA를
    가로지르는 오케스트레이션이므로 rg_replenishment Harness를 경유한다(원칙 18-7).
    company가 오면(ALL 제외) 그 회사의 RG 재고 적재 계정으로 변환해 account_key를 덮어쓴다."""
    if company and company not in ("ALL", "전체"):
        # codex P2 fail-closed: 미지의 회사명이면 매칭 계정 없음(__unknown__)→빈 결과.
        # 필터를 명시 요청했는데 전체를 노출하지 않는다(전체는 company 미지정/ALL일 때만).
        account_key = _RG_ACCOUNT_BY_COMPANY.get(company, "__unknown__")
    return rg_replenishment.build_replenishment_plan(db, account_key, target_days=target_days)


@router.get("/in-transit")
def get_in_transit(
    db: Session = Depends(get_db),
    account_key: str | None = Query(None, description="특정 셀러계정만(미지정=전체)"),
):
    """옵션별 발송중(in-transit) 수량 + 판매개시 예정일 조회(읽기전용, X5 freshness-gate 포함).

    D-13: 유효재고=현재고+발송중. X5: last_success_at <2일=fresh(차감), stale=차감 스킵+배지.
    검증 엔드포인트 — Wing 입고관리 화면 수치와 대조 가능(필름 100·버디 0 등)."""
    return in_transit_estimator.estimate_in_transit(db, account_key)


@router.get("/demand-class")
def get_demand_class(
    db: Session = Depends(get_db),
    account_key: str | None = Query(None, description="특정 셀러계정만(미지정=전체)"),
):
    """옵션별 수요 형태 분류(ADI/CV² 4분면) + X1 진단 버킷 조회(읽기전용, S8 D-14).

    smooth/erratic/intermittent/lumpy + unknown(표본<2). 버킷=zero_signal/sparse/active.
    summary.by_bucket로 "예측(S9)이 살릴 수 있는 옵션 모집단" 크기를 정직하게 측정(X1)."""
    return demand_classifier.classify_demand(db, account_key)


@router.get("/replenishment-backtest")
def get_replenishment_backtest(
    db: Session = Depends(get_db),
    account_key: str | None = Query(None, description="특정 셀러계정만(미지정=전체)"),
    train_min_days: int = Query(7, ge=1, le=30, description="cutoff별 최소 학습 달력일수"),
    protection_mode: str = Query("full", description="보호구간: full(p90리드+검토주기≈10일) / lead_only(p90리드≈3일, 얇은 데이터용)"),
    review_period: int = Query(7, ge=1, le=14, description="검토주기 R(D-16 cadence, 목표재고 target_days)"),
):
    """파는 옵션 발송추천 정책의 과거 적정성 walk-forward 백테스트(읽기전용, D-18 게이트②).

    과거 시점 D까지로 산정한 목표재고 S(라이브 compute_target_level 공유) vs D 이후 보호구간 실제수요 A.
    옵션별 fill-rate(품절 회피율)·평균 과잉재고일수·valid_windows. sold_30d 배제(과거 복원 불가, D-결정-B).
    데이터 얇으면 summary.indicative_only=true(원칙22 은폐 금지). 다중 SA 가로지르므로 Harness 경유(원칙18-7)."""
    return replenishment_backtest.run_backtest(
        db, account_key,
        train_min_days=train_min_days,
        protection_mode=protection_mode,
        review_period=review_period,
    )


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
                # S5a/D-15: all_cost = ALL_DELIVERED_AD_COST(전체). None(미제공)은 그대로 전달 →
                # ingest가 폴백+카운트(머니필드 가시성, codex P2-2). 구 페처는 키 없음 → None.
                _raw_all = d.get("all_cost")
                all_cost = int(_raw_all) if _raw_all is not None else None
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="ad_spend/conv_sales/all_cost는 정수여야 함")
            if ad_spend < 0 or conv_sales < 0 or (all_cost is not None and all_cost < 0):
                raise HTTPException(status_code=400, detail="값은 음수일 수 없음")
            days.append({"date": day_date, "ad_spend": ad_spend,
                         "all_cost": all_cost, "conv_sales": conv_sales})
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


# ════════════════════════════════════════════════
# 쿠팡 로켓배송(1P) supplier.coupang.com ingest (트랙 rocket-1p S2, D-9)
# ────────────────────────────────────────────────
# 런타임 경계(D-1): supplier는 Akamai 봇방어 → 백엔드 직접 fetch 불가.
#   Mac 헤드풀 CDP 페처(S3)가 수집 → 아래로 raw push(X-Ingest-Token=AD_INGEST_TOKEN 재사용).
# ════════════════════════════════════════════════
def _check_ingest_token(x_ingest_token: str | None) -> None:
    """X-Ingest-Token 상수시간 비교(ad-cost/ingest 패턴 재사용). 미설정·불일치 모두 401."""
    import secrets as _secrets

    expected = os.getenv("AD_INGEST_TOKEN", "").strip()
    if not expected or not x_ingest_token or not _secrets.compare_digest(x_ingest_token.strip(), expected):
        raise HTTPException(status_code=401, detail="unauthorized")


@router.post("/rocket/po/ingest")
def ingest_rocket_po(
    body: dict[str, Any] = Body(...),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """로켓배송(1P) 발주 list 페이지(raw JSON) push → 파싱 → snapshot upsert(멱등).

    body: {"pages": [<발주 list API 한 페이지 raw JSON>, ...]}. 페처가 page=1..lastPageNumber 수집.
    """
    _check_ingest_token(x_ingest_token)
    pages = body.get("pages")
    if not isinstance(pages, list) or not pages:
        raise HTTPException(status_code=400, detail="pages[] 필요")
    return rocket_supplier_sync.ingest_purchase_orders(db, pages)


@router.post("/rocket/settlement/ingest")
def ingest_rocket_settlement(
    body: dict[str, Any] = Body(...),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """로켓배송(1P) 정산 DOM rows(헤더 포함) push → 파싱 → snapshot upsert(멱등).

    body: {"vendor_id": "A01029796", "rows": [[헤더셀...], [데이터셀...], ...]}.
    vendor_id는 계정축(정산 row엔 거래처명만 있어 별도 주입).
    """
    _check_ingest_token(x_ingest_token)
    vendor_id = str(body.get("vendor_id") or "").strip()
    if not vendor_id:
        raise HTTPException(status_code=400, detail="vendor_id 필요")
    rows = body.get("rows")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=400, detail="rows[] 필요")
    return rocket_supplier_sync.ingest_settlements(db, vendor_id, rows)


@router.post("/rocket/ad-cost/ingest")
def ingest_rocket_ad_cost(
    body: dict[str, Any] = Body(...),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """오하이테크(1P 로켓배송) 일별 광고비 push → coupang_ad_report(Retail) upsert(멱등).

    body: {"vendor_id": "A01029796", "days": [{"date":"YYYY-MM-DD","ad_spend":<전체>,"conv_sales":<…>}, ...]}.
    ad_spend=ALL_DELIVERED_AD_COST(전체, D-10) → _agg_rocket_ad가 1P 순이익에서 차감. 트랙 D-8/D-9/D-10.
    """
    _check_ingest_token(x_ingest_token)
    vendor_id = str(body.get("vendor_id") or "").strip()
    if not vendor_id:
        raise HTTPException(status_code=400, detail="vendor_id 필요")
    days = body.get("days")
    if not isinstance(days, list) or not days:
        raise HTTPException(status_code=400, detail="days[] 필요")
    return ohitech_ad_sync.ingest_ohitech_ad_cost(db, vendor_id, days)


@router.post("/rocket/po-detail/ingest")
def ingest_rocket_po_detail(
    body: dict[str, Any] = Body(...),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """로켓배송(1P) 발주상세 per-SKU DOM rows(Table[7]) push → 파싱 → 해당 PO snapshot replace(S4.5a).

    body: {"purchase_order_seq": 134342890, "vendor_id": "A01029796", "rows": [[셀...], ...]}.
    rows = 발주상세 SSR Table[7] DOM(헤더·SKU·합계행 혼재). vendor_id는 계정축(DOM에 없어 주입).
    """
    _check_ingest_token(x_ingest_token)
    try:
        po_seq = int(body.get("purchase_order_seq"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="purchase_order_seq(정수) 필요")
    if po_seq <= 0:
        raise HTTPException(status_code=400, detail="purchase_order_seq(>0) 필요")
    vendor_id = str(body.get("vendor_id") or "").strip()
    if not vendor_id:
        raise HTTPException(status_code=400, detail="vendor_id 필요")
    rows = body.get("rows")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=400, detail="rows[] 필요")
    return rocket_supplier_sync.ingest_po_items(db, po_seq, vendor_id, rows)


# ════════════════════════════════════════════════
# 프로모션 손익 레이어 ingest (트랙 coupang-promo-pnl Phase 1, D-CPP-1~6)
# ────────────────────────────────────────────────
# body의 rows는 **우리 레코드 계약**(PLAN_coupang-promo-pnl-phase1.md §4)이지 쿠팡 원시 응답이
#   아니다. 2026-07-28 정찰에서 supplier 세션이 만료돼 원시 스키마를 특정하지 못했고, 모르는
#   스키마의 파서를 지어내지 않기로 했다(추측 금지). 페처가 raw→계약 매핑을 담당한다.
# ★회계축 불변: 여기로 들어온 값은 net_profit·종합조망에 결합되지 않는다(D-CPP-2/D-CPP-4).
# ════════════════════════════════════════════════
@router.post("/rocket/sales/ingest")
def ingest_rocket_sales(
    body: dict[str, Any] = Body(...),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """로켓배송(1P) 옵션×일 소비자 판매(판매분석) push → snapshot upsert(멱등).

    body: {"vendor_id":"A01029796", "source":"sales_analysis|excel",
           "rows":[{"option_id","date","sku_id","qty","revenue","visitors","conversion_rate","product_name"}, ...]}
    vendor_id는 계정축(판매분석 행에 없어 주입). ★revenue는 소비자 실현가 — 회계 매출 아님(D-CPP-2).
    """
    _check_ingest_token(x_ingest_token)
    vendor_id = str(body.get("vendor_id") or "").strip()
    # 길이도 본다: vendor_id는 String(20) 그레인 키다. SQLite는 초과를 통과시켜 **평행 계정축**을
    #   만들고, PostgreSQL에선 배치 전체가 죽는다(파서가 식별자에 적용하는 규칙과 같은 이유).
    if not vendor_id or len(vendor_id) > 20:
        raise HTTPException(status_code=400, detail="vendor_id 필요(<=20자)")
    rows = body.get("rows")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=400, detail="rows[] 필요")
    source = str(body.get("source") or "sales_analysis").strip() or "sales_analysis"
    out = rocket_promo_sync.ingest_rocket_sales(db, vendor_id, rows, source=source)
    # ★수집 통계를 prod 로그에 남긴다(적대적 리뷰 4R): 실패·미조회 날짜가 Mac 로컬 로그에만
    #   있으면 아무도 안 본다 — RG 26일 침묵이 정확히 그 형태였다(감지는 되는데 표면이 없음).
    #   페처가 안 보내는 구버전이면 빈 dict라 그냥 조용하다(하위호환).
    cs = body.get("collection_stats")
    if isinstance(cs, dict) and cs:
        out["collection_stats"] = cs
        if cs.get("days_failed") or cs.get("days_abandoned"):
            log.warning(
                "1P 판매 수집 부분 성공: vendor=%s 요청 %s일 → 수집 %s·범위밖 %s·실패 %s·미조회 %s "
                "(실패 날짜 %s) — 롤링 창이 다음 회차에 메우는지 확인할 것",
                vendor_id, cs.get("days_requested"), cs.get("days_collected"),
                cs.get("days_out_of_range"), cs.get("days_failed"), cs.get("days_abandoned"),
                ",".join(map(str, cs.get("failed_dates") or []))[:200],
            )
    return out


@router.post("/rocket/promotion/ingest")
def ingest_rocket_promotion(
    body: dict[str, Any] = Body(...),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """로켓배송(1P) 프로모션 신청(공급자허브) push → snapshot upsert(멱등, grain=request_id).

    body: {"vendor_id":"A01029796",
           "rows":[{"request_id","contract_id","promotion_name","promotion_type","status",
                    "start_at","end_at","share_ratio","discount_method","discount_value",
                    "budget_amount","settlement_date","applied_product_count","requested_at","raw"}, ...]}
    행사기간(start_at/end_at)은 **초 단위**를 보존한다. ★분담금 청구 방식은 미확정(D-CPP-4) —
    이 데이터는 사실 기록이며 어떤 비용 라인에도 자동 반영되지 않는다.
    """
    _check_ingest_token(x_ingest_token)
    vendor_id = str(body.get("vendor_id") or "").strip()
    if not vendor_id or len(vendor_id) > 20:   # String(20) 그레인 키 — 위 sales와 같은 이유
        raise HTTPException(status_code=400, detail="vendor_id 필요(<=20자)")
    rows = body.get("rows")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=400, detail="rows[] 필요")
    return rocket_promo_sync.ingest_rocket_promotions(db, vendor_id, rows)


@router.post("/coupon/used-amount/ingest")
def ingest_coupon_used_amount(
    body: dict[str, Any] = Body(...),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """쿠폰별 실사용 할인액(= 셀러 실부담, D-CPP-3 권위값) push → 기존 coupang_coupon 행 갱신.

    body: {"account_key":"COUPANG_WING1", "source":"wing_ui|wing_api|manual",
           "rows":[{"coupon_id":"94177420","used_amount":156000}, ...]}
    ★쿠팡 Open API(fms)에는 이 값이 없다(ref 06 §E 전수 대조) → coupon_sync가 아니라 이 경로로만
      들어온다. 없는 쿠폰은 행을 만들지 않고 not_found로 돌려준다(유령 행 방지).
    """
    _check_ingest_token(x_ingest_token)
    account_key = str(body.get("account_key") or "").strip()
    if not account_key or len(account_key) > 20:   # coupang_coupon.account_key = String(20)
        raise HTTPException(status_code=400, detail="account_key 필요(<=20자)")
    rows = body.get("rows")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=400, detail="rows[] 필요")
    source = str(body.get("source") or "wing_ui").strip() or "wing_ui"
    return rocket_promo_sync.ingest_coupon_used_amount(db, account_key, rows, source=source)


# 목적지 컬럼 Numeric(12,2)의 정수부 상한 — 파서의 _MAX_DISCOUNT와 같은 이유(유한하다고 담기는
#   것은 아니다: SQLite는 통과시켜 합계를 오염시키고 PostgreSQL은 commit을 죽인다).
_MAX_UNIT_DISCOUNT = Decimal(10) ** 10

# 대상 SKU 지정(Phase 2) 방어값. product_number는 String(30)이고, 한 프로모션의 적용상품은
#   실측 1~3개다(prod 7건 전수). 상한은 넉넉히 두되 무한 리스트는 막는다.
_MAX_TARGET_SKUS = 200
_MAX_SKU_LEN = 30


def _normalize_target_sku_ids(raw: Any) -> list[str] | None:
    """대상 SKU 리스트 정규화 — 공백 제거·중복 제거(순서 보존)·빈 값 제거.

    ★길이 초과 ID는 **자르지 않고 거부한다**(400): 잘린 ID는 '다른 ID'가 되어 영원히 엉뚱한
      판매 행에 붙는다(파서 `_sid`와 같은 이유). 손익이 통째로 틀리면서 대사되지 않는다.
    ★빈 리스트([])는 삭제(=미지정)로 취급해 None으로 저장한다 — '지정했는데 0개'라는 상태는
      의미가 없고, target_sku_missing 판정을 한 갈래로 유지한다.
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="target_sku_ids는 배열이어야 합니다(삭제는 null 또는 [])")
    if len(raw) > _MAX_TARGET_SKUS:
        raise HTTPException(status_code=400, detail=f"target_sku_ids 최대 {_MAX_TARGET_SKUS}개")
    out: list[str] = []
    for v in raw:
        if isinstance(v, bool) or not isinstance(v, (str, int)):
            raise HTTPException(status_code=400, detail=f"target_sku_ids 원소는 문자열이어야 합니다: {v!r}")
        s = str(v).strip()
        if not s:
            continue
        if len(s) > _MAX_SKU_LEN:
            raise HTTPException(
                status_code=400,
                detail=f"target_sku_ids 항목 길이 초과(<={_MAX_SKU_LEN}자): {s[:40]}",
            )
        if s not in out:
            out.append(s)
    return out or None


@router.patch("/rocket/promotion/{request_id}/unit-discount")
def patch_promotion_unit_discount(
    request_id: str = Path(...),
    body: dict[str, Any] = Body(...),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """프로모션 **수기 입력** 갱신 — 개당 할인액(D-CPP-7) + 대상 SKU(Phase 2). 기존 행만 갱신.

    body: {"unit_discount_amount": 3000, "target_sku_ids": ["62178970", "69411570"]}
      - 두 키는 **각각 선택**이다(둘 다 없으면 400). 보낸 키만 갱신한다 — 할인액만 고칠 때
        SKU가 지워지지 않는다(반대도 마찬가지).
      - `null`(또는 SKU는 `[]`)이면 그 값 삭제 = '모름'으로 되돌림(0원 할인·0개 지정과 구분).

    ★target_sku_ids가 왜 수기인가(2026-07-28 prod raw 실측): 프로모션 API 응답에 **적용 상품
      목록이 없다** — `detailCount`(적용상품 수)만 있다. 손익은 "이 창에서 어느 SKU가 팔렸나"를
      알아야 계산되는데, 이름 유사도·기간 겹침 같은 추정 매핑은 틀려도 대사되지 않는다(원칙22).

    ★왜 수기인가(2026-07-28 라이브 실측): 공급자허브 프로모션 목록/상세 API에 상품별·단위
      할인액 필드가 **없다**(discountBudget=총예산, supplierFundRate=분담%뿐). Jino 확정:
      "한 프로모션에 제품은 여러개가 들어갈 수 있지만 할인 가격은 모두 같은게 맞아" → 1칸이면 족하다.
    ★행을 만들지 않는다: 없는 request_id는 404. 수기 입력이 유령 프로모션을 만들면 그 값은
      어떤 수집으로도 대사되지 않는다(원칙22).
    ★페처는 이 칸을 쓰지 않는다 → 재수집(snapshot upsert)이 수기 값을 지우지 않는다.
    """
    _check_ingest_token(x_ingest_token)
    rid = str(request_id or "").strip()
    if not rid or len(rid) > 30:   # String(30) 그레인 키
        raise HTTPException(status_code=400, detail="request_id 필요(<=30자)")
    has_amount = "unit_discount_amount" in body
    has_skus = "target_sku_ids" in body
    if not has_amount and not has_skus:
        raise HTTPException(
            status_code=400,
            detail="unit_discount_amount 또는 target_sku_ids 필요(각각 삭제는 null)",
        )

    amount: Decimal | None = None
    if has_amount:
        raw = body.get("unit_discount_amount")
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            amount = None   # 명시적 삭제 = '모른다'로 되돌림(0원 할인과 구분)
        else:
            try:
                amount = Decimal(str(raw).replace(",", "").strip())
            except (ArithmeticError, ValueError):
                raise HTTPException(status_code=400, detail="unit_discount_amount 숫자 아님")
            if not amount.is_finite() or amount < 0 or amount >= _MAX_UNIT_DISCOUNT:
                # 음수 할인액은 존재하지 않는다 — 입력 사고를 조용히 저장하지 않는다.
                raise HTTPException(status_code=400, detail="unit_discount_amount 범위 오류(0 이상, 10^10 미만)")
            # ★목적지 컬럼 그레인(Numeric(12,2))으로 먼저 맞춘다: SQLite는 준 대로 담고
            #   PostgreSQL은 반올림해서 담아, 같은 입력이 DB에 따라 다르게 저장된다. 여기서
            #   접어 두면 응답으로 돌려주는 값이 실제 저장값과 항상 같다. (`-0`도 `0.00`으로 정규화)
            amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) + Decimal(0)

    skus: list[str] | None = None
    if has_skus:
        skus = _normalize_target_sku_ids(body.get("target_sku_ids"))

    row = (
        db.query(CoupangRocketPromotion)
        .filter(CoupangRocketPromotion.request_id == rid)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"프로모션 {rid} 없음(먼저 수집되어야 합니다)")
    if has_amount:
        row.unit_discount_amount = amount
    if has_skus:
        row.target_sku_ids = skus
    db.commit()
    log.info(
        "프로모션 수기 갱신: request_id=%s amount=%s(set=%s) target_skus=%s(set=%s)",
        rid, amount, has_amount, skus, has_skus,
    )
    return {
        "request_id": rid,
        "unit_discount_amount": (
            str(row.unit_discount_amount) if row.unit_discount_amount is not None else None
        ),
        "target_sku_ids": row.target_sku_ids,
        "promotion_name": row.promotion_name,
        "applied_product_count": row.applied_product_count,
    }


# ════════════════════════════════════════════════
# 로켓배송(1P) 원가 브리지 매핑 (트랙 rocket-1p S4.5b, D-13)
# ────────────────────────────────────────────────
# 사용자 확정 입력(프론트 S5) — 발주상세 상품번호 → product_master.internal_sku.
#   ingest 토큰 불필요(products/manual-revenue와 동일 사용자 CRUD). net_profit 결합은 S4.5c.
# ════════════════════════════════════════════════
@router.get("/rocket/cost-map/unmapped")
def rocket_cost_map_unmapped(
    vendor_id: str | None = Query(None, description="계정 필터(미설정=전체)"),
    limit: int = Query(200, ge=0, le=1000),
    suggest: bool = Query(True, description="이름유사도 제안 포함(느림). False=목록만."),
    db: Session = Depends(get_db),
):
    """발주상세에 있으나 원가 매핑 없는 상품번호 + 이름유사도 제안(상위 3).

    각 항목: product_number·대표명·바코드·총 발주수량·등장 PO 수·suggestions[internal_sku/score].
    """
    return rocket_cost_map.list_unmapped(db, vendor_id=vendor_id, limit=limit, suggest=suggest)


@router.get("/rocket/cost-map")
def rocket_cost_map_list(db: Session = Depends(get_db)):
    """확정/제외(ignored) 매핑 전체 목록 — internal_sku 현재 cost_price 조인."""
    return rocket_cost_map.list_mappings(db)


@router.post("/rocket/cost-map")
def rocket_cost_map_upsert(
    body: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    """상품번호 → internal_sku 매핑 확정(멱등 upsert).

    body: {"product_number","internal_sku"(confirmed시 필수),"status"(confirmed|ignored, 기본 confirmed),
           "match_method"(manual|suggested, 기본 manual),"note"}.
    'ignored' = 원가 제외(샘플/증정). 검증 실패(없는 internal_sku 등)는 422.
    """
    pn = str(body.get("product_number") or "").strip()
    if not pn:
        raise HTTPException(status_code=400, detail="product_number 필요")
    try:
        return rocket_cost_map.upsert_mapping(
            db,
            product_number=pn,
            internal_sku=body.get("internal_sku"),
            status=str(body.get("status") or "confirmed"),
            match_method=str(body.get("match_method") or "manual"),
            note=body.get("note"),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/rocket/cost-map/{product_number}")
def rocket_cost_map_delete(product_number: str, db: Session = Depends(get_db)):
    """매핑 삭제(상품번호를 다시 미매핑으로). 없으면 deleted=0."""
    return rocket_cost_map.delete_mapping(db, product_number)


# ════════════════════════════════════════════════
# 로켓배송(1P) 페처 갱신 트리거 (S5 UI 버튼, Wing 패턴 복제)
# ════════════════════════════════════════════════
@router.post("/rocket/request-refresh")
def rocket_request_refresh(db: Session = Depends(get_db)):
    """UI '로켓 갱신' 버튼 → 갱신 요청 플래그 set. 페처가 다음 실행에서 소비."""
    return rocket_supplier_sync.request_rocket_refresh(db)


@router.get("/rocket/refresh-status")
def rocket_refresh_status_endpoint(db: Session = Depends(get_db)):
    """갱신 요청/완료 상태 폴링용."""
    return rocket_supplier_sync.rocket_refresh_status(db)


@router.post("/rocket/refresh-claim")
def rocket_refresh_claim(
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """로켓 페처가 갱신 요청을 **임대**(lease). 토큰 인증(ingest와 동일 — 페처 전용 뮤테이션).

    2026-07-27부터 claim은 플래그를 지우지 않는다 — 성공/3회 소진/로그인 필요일 때만
    소멸한다(refresh_contract). 시그니처·응답 키(claimed)는 불변.
    """
    _check_ingest_token(x_ingest_token)
    return rocket_supplier_sync.claim_rocket_refresh(db)


@router.get("/collection-status")
def coupang_collection_status(db: Session = Depends(get_db)):
    """쿠팡 4개 브라우저 수집 스트림(ofix 판매/광고, ohitech 광고, 로켓 발주)의
    신선도·실패 상태 집계. 전역 신선도 배너 전용(60s 폴). 자동 트리거 제거 후
    '낡음/실패'를 가시화하는 유일 경로."""
    from app.services.coupang import collection_status as _cs

    return _cs.collection_status(db)


@router.post("/rocket/fetch-success")
def rocket_fetch_success(
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """로켓 페처 실행 완료 시 last_success_at 갱신(UI 폴링 완료 감지용). 토큰 인증."""
    _check_ingest_token(x_ingest_token)
    rocket_supplier_sync.mark_rocket_fetch_success(db)
    return {"ok": True}


@router.post("/rocket/fetch-error")
def rocket_fetch_error(
    body: dict[str, Any] = Body(...),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """로켓 페처 run 실패 보고 → last_error/last_error_at 기록. 토큰 인증(형제 fetch-success와 동일).

    ★claim의 짝: 페처가 요청을 claim한 뒤 브라우저 에러로 죽으면 플래그는 이미 clear라 아무
    흔적도 안 남았다 — UI는 실패인지 진행 중인지 알 수 없었다(215초 헛기다림).
    """
    _check_ingest_token(x_ingest_token)
    error = str(body.get("error") or "").strip() or "unknown"
    # kind(옵션, 하위호환): 구버전 페처는 안 보낸다 → None = 평범한 실패 = 재시도 대상.
    # "login_required"만 특별 취급(재시도 제외, PLAN §0 금지선).
    kind = str(body.get("kind") or "").strip() or None
    # lease(옵션, codex 1R[P1]): claim 응답의 임대 식별자. 지금 유효한 임대와 다르면 stale
    # 보고로 무시한다(20분 넘게 멈췄던 옛 시도가 남의 임대를 반납하는 것 차단). 없으면 기존 동작.
    lease = str(body.get("lease") or "").strip() or None
    rocket_supplier_sync.mark_rocket_fetch_error(db, error, kind=kind, lease=lease)
    return {"ok": True}


# ════════════════════════════════════════════════
# 오하이테크(1P 로켓배송) 광고비 페처 갱신 트리거 (S3 버튼-poll, adcost/rocket 패턴)
#   광고비는 Akamai로 prod 직접 fetch 불가(D-4) → Mac CDP 페처(poll 데몬)가 가져온다.
#   '광고비 갱신' 버튼 → request-refresh → 데몬이 다음 폴에서 claim → fetch·push → fetch-success.
# ════════════════════════════════════════════════
@router.post("/rocket/ad-cost/request-refresh")
def ohitech_ad_request_refresh(db: Session = Depends(get_db)):
    """UI '광고비 갱신' 버튼 → 갱신 요청 플래그 set. 페처가 다음 폴에서 소비."""
    return ohitech_ad_sync.request_refresh(db)


@router.get("/rocket/ad-cost/refresh-status")
def ohitech_ad_refresh_status(db: Session = Depends(get_db)):
    """갱신 요청/완료 상태. 대시보드(버튼 후 폴링)·Mac 페처(요청 확인) 공용."""
    return ohitech_ad_sync.refresh_status(db)


@router.post("/rocket/ad-cost/refresh-claim")
def ohitech_ad_refresh_claim(
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Mac 페처가 갱신 요청을 **임대**(lease)하고 작업 시작. 토큰 인증(ingest와 동일).

    claim은 플래그를 지우지 않는다(2026-07-27 lease 계약) — 응답의 lease를 실패 보고에
    되돌려주면 stale 보고가 걸러진다.
    """
    _check_ingest_token(x_ingest_token)
    return ohitech_ad_sync.claim_refresh(db)


@router.post("/rocket/ad-cost/fetch-success")
def ohitech_ad_fetch_success(
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """페처 run 완료 시 last_success_at 갱신(UI 폴링 완료 감지용). 토큰 인증."""
    _check_ingest_token(x_ingest_token)
    ohitech_ad_sync.mark_fetch_success(db)
    return {"ok": True}


@router.post("/rocket/ad-cost/fetch-error")
def ohitech_ad_fetch_error(
    body: dict[str, Any] = Body(...),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """오하이테크 광고 페처 run 실패 보고 → last_error/last_error_at 기록. 토큰 인증.

    ★claim의 짝: 페처가 요청을 claim한 뒤 브라우저 에러로 죽으면 플래그는 이미 clear라 아무
    흔적도 안 남았다 — UI는 실패인지 진행 중인지 알 수 없었다(215초 헛기다림).
    """
    _check_ingest_token(x_ingest_token)
    error = str(body.get("error") or "").strip() or "unknown"
    # kind(옵션, 하위호환): 구버전 페처는 안 보낸다 → None = 평범한 실패 = 재시도 대상.
    # "login_required"만 특별 취급(재시도 제외, PLAN §0 금지선).
    kind = str(body.get("kind") or "").strip() or None
    # lease(옵션, codex 1R[P1]): claim 응답의 임대 식별자. 지금 유효한 임대와 다르면 stale
    # 보고로 무시한다(20분 넘게 멈췄던 옛 시도가 남의 임대를 반납하는 것 차단). 없으면 기존 동작.
    lease = str(body.get("lease") or "").strip() or None
    ohitech_ad_sync.mark_fetch_error(db, error, kind=kind, lease=lease)
    return {"ok": True}


@router.post("/ad-cost/option-ingest")
async def ingest_ad_cost_option(
    request: Request,
    background_tasks: BackgroundTasks,
    x_ingest_token: str | None = Header(default=None),
    x_report_filename: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """로컬 페처 → 옵션×일별 광고비 XLSX(Billboard 보고서) 바이트 인제스트.

    advertising.coupang.com Billboard 보고서는 옵션ID×일별 광고비의 유일한 소스(레퍼런스 16).
    Akamai 차단으로 prod 직접 다운로드 불가 → Jino Mac 페처가 받아 바이트로 push.
    인증: X-Ingest-Token == AD_INGEST_TOKEN. 파일명은 X-Report-Filename 헤더(vendor_id 추출용).
    기존 수동 업로드와 동일 파서(ad_costs.ingest_coupang_ad_xlsx_content)를 재사용한다.
    """
    import secrets as _secrets

    from app.routers.ad_costs import ingest_coupang_ad_xlsx_content, _recalc_profit_background

    expected = os.getenv("AD_INGEST_TOKEN", "").strip()
    if not expected or not x_ingest_token or not _secrets.compare_digest(x_ingest_token.strip(), expected):
        raise HTTPException(status_code=401, detail="unauthorized")

    filename = (x_report_filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="X-Report-Filename 헤더 필요({vendor_id}_pa_daily_*.xlsx)")

    # 크기 상한(codex P2): 토큰 인증이지만 방어적으로 과대 본문 거부(메모리 폭주 차단).
    # 옵션 보고서 XLSX는 보통 ~150KB. Content-Length 선검사 + 스트림 누적 한도(헤더 없거나
    # 거짓이어도 초과 즉시 중단 — 전체를 메모리에 올리기 전에 차단).
    _MAX = 30 * 1024 * 1024
    clen = request.headers.get("content-length")
    if clen and clen.isdigit() and int(clen) > _MAX:
        raise HTTPException(status_code=413, detail="본문 과대(최대 30MB)")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX:
            raise HTTPException(status_code=413, detail="본문 과대(최대 30MB)")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise HTTPException(status_code=400, detail="빈 본문 — xlsx 바이트 필요")

    result, recalc_from, recalc_to = ingest_coupang_ad_xlsx_content(content, filename, db)
    if recalc_from and recalc_to:
        background_tasks.add_task(_recalc_profit_background, recalc_from, recalc_to)
    return result


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
    """Mac 페처가 갱신 요청을 **임대**(lease)하고 작업 시작. 토큰 인증(ingest와 동일).

    claim은 플래그를 지우지 않는다(2026-07-27 lease 계약) — 응답의 lease를 실패 보고에
    되돌려주면 stale 보고가 걸러진다.
    """
    import secrets as _secrets

    expected = os.getenv("AD_INGEST_TOKEN", "").strip()
    if not expected or not x_ingest_token or not _secrets.compare_digest(x_ingest_token.strip(), expected):
        raise HTTPException(status_code=401, detail="unauthorized")
    return ad_cost_sync.claim_refresh(db)


@router.post("/ad-cost/fetch-error")
def report_ad_cost_fetch_error(
    body: dict[str, Any] = Body(...),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Mac 페처 run 실패 보고 → last_error/last_error_at 기록. 토큰 인증(claim과 동일).

    ★claim의 짝(2026-07-17 13:02 실사고): 페처가 요청을 claim한 뒤 브라우저 에러로 죽으면
    플래그는 이미 clear라 아무 흔적도 안 남았다 — UI는 실패인지 진행 중인지 알 수 없었다.
    성공은 ingest가 알리고(last_success_at), 실패는 이 엔드포인트가 알린다.
    """
    _check_ingest_token(x_ingest_token)
    error = str(body.get("error") or "").strip() or "unknown"
    # kind(옵션, 하위호환): 구버전 페처는 안 보낸다 → None = 평범한 실패 = 재시도 대상.
    # "login_required"만 특별 취급(재시도 제외, PLAN §0 금지선).
    kind = str(body.get("kind") or "").strip() or None
    # lease(옵션, codex 1R[P1]): claim 응답의 임대 식별자. 지금 유효한 임대와 다르면 stale
    # 보고로 무시한다(20분 넘게 멈췄던 옛 시도가 남의 임대를 반납하는 것 차단). 없으면 기존 동작.
    lease = str(body.get("lease") or "").strip() or None
    ad_cost_sync.mark_fetch_error(db, error, kind=kind, lease=lease)
    return {"ok": True}


@router.get("/ad-cost")
def get_ad_cost(
    db: Session = Depends(get_db),
    start: date = Query(..., description="시작일 YYYY-MM-DD"),
    end: date = Query(..., description="종료일 YYYY-MM-DD"),
):
    """날짜 범위 광고비 조회 (일별 합산)."""
    return {"costs": ad_cost_sync.get_ad_cost_range(db, start, end)}


# ════════════════════════════════════════════════════════════════════
# Wing 판매분석 vendor-summary 인제스트/갱신 (Wing 세션 자동화 트랙 S2, D-4/D-5)
# ════════════════════════════════════════════════════════════════════
def _require_ingest_token(x_ingest_token: str | None) -> None:
    """X-Ingest-Token == AD_INGEST_TOKEN 상수시간 검증(광고 ingest와 동일 토큰·인프라). 실패=401."""
    import secrets as _secrets

    expected = os.getenv("AD_INGEST_TOKEN", "").strip()
    if not expected or not x_ingest_token or not _secrets.compare_digest(x_ingest_token.strip(), expected):
        raise HTTPException(status_code=401, detail="unauthorized")


_VS_TYPES = {"NORMAL", "RFM"}  # ref 18: NORMAL=3P / RFM=RG
# 허용 계정(codex P2): account=None 대조가 저장된 모든 계정 official을 합산하므로, 오타 account_key가
# "전체" 합계를 오염시키되 계정 필터 뷰엔 안 보이는 사각 방지. command-center 허용 집합과 동일.
_VS_ACCOUNTS = {"COUPANG_WING1", "COUPANG_WING2"}
# ingest 값 방어 상한 — 음수는 정상(환불 순액), 이 절대값을 넘으면 malformed로 간주.
_VS_GMV_ABS_MAX = 10_000_000_000  # 100억 원/일
_VS_UNITS_ABS_MAX = 1_000_000  # 100만 개/일


@router.post("/wing/vendor-summary/ingest")
def ingest_wing_vendor_summary(
    body: dict[str, Any] = Body(...),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Wing 페처(Jino Mac, residential IP) → 쿠팡 공식 판매분석 GMV 인제스트(D-5).

    cf_clearance 재생 불가로 prod 직접 fetch 금지 → Mac 페처가 브라우저측 fetch한 결과만 적재.
    인증: X-Ingest-Token == AD_INGEST_TOKEN. body {account_key, days:[{date, registration_type,
    gmv, units_sold, last_refresh?}]}. registration_type∈{NORMAL,RFM}(ref 18).
    """
    _require_ingest_token(x_ingest_token)

    account_key = str(body.get("account_key") or "").strip()
    if account_key not in _VS_ACCOUNTS:
        raise HTTPException(
            status_code=400,
            detail=f"account_key는 {', '.join(sorted(_VS_ACCOUNTS))} 중 하나여야 함",
        )
    days_raw = body.get("days")
    if not isinstance(days_raw, list) or not days_raw:
        raise HTTPException(status_code=400, detail="days[] 필요")

    rows: list[dict] = []
    for d in days_raw:
        if not isinstance(d, dict):
            raise HTTPException(status_code=400, detail="days[] 항목은 객체여야 함")
        try:
            day_date = date.fromisoformat(str(d.get("date")))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="days[].date(YYYY-MM-DD) 필요")
        rt = str(d.get("registration_type") or "")
        if rt not in _VS_TYPES:
            raise HTTPException(status_code=400, detail="registration_type은 NORMAL/RFM")
        try:
            gmv = int(d.get("gmv") or 0)
            units_sold = int(d.get("units_sold") or 0)
        except (TypeError, ValueError, OverflowError):  # OverflowError: json은 Infinity를 허용한다
            raise HTTPException(status_code=400, detail="gmv/units_sold는 정수여야 함")
        # ★음수 허용(2026-07-27 라이브 실측, 병행 세션 38d19c1과 합류): gmv=판매액-환불액(쿠팡
        # 공식 정의) → 환불이 판매를 초과하는 날은 정당하게 음수(WING1 07-02 -16,620·07-07
        # -12,900 실측). 원래 검증(e2c2560, S2 초기 구현)은 ad-cost ingest의 "비용은 항상
        # 0 이상" 패턴을 그대로 복제한 것으로, 그 가정을 뒷받침하는 근거가 애초에 없었다 —
        # 결과적으로 배치 전체가 400으로 거부되어 45일 자가치유 백필(PR #108)이 통째로 막혔다.
        # units_sold도 순액 의미론(환불 수량 차감)일 수 있어 동일 정책 적용 — 절대값 상한만으로
        # 파싱 깨짐(json Infinity 등)을 방어한다(일 GMV 실측 ~수백만 원 대비 수천 배 여유).
        if abs(gmv) > _VS_GMV_ABS_MAX or abs(units_sold) > _VS_UNITS_ABS_MAX:
            raise HTTPException(status_code=400, detail="gmv/units_sold 값이 비정상 범위")
        last_refresh = d.get("last_refresh")
        rows.append({
            "date": day_date, "registration_type": rt,
            "gmv": gmv, "units_sold": units_sold,
            "last_refresh": str(last_refresh) if last_refresh is not None else None,
        })
    return vendor_summary_sync.ingest_vendor_summary(db, account_key, rows)


# ── 버튼 큐 계정 파라미터 (2026-07-27, WING2 데몬 편입) ──
# 갱신 버튼 큐(판매분석·RG 정산)는 계정 차원이다. account_key 기본값=COUPANG_WING1이라
# 파라미터를 안 보내는 구버전 페처도 기존과 똑같이 동작한다(배포 순서 무관).
def _require_rg_account(account_key: str) -> str:
    """버튼 큐 account_key 검증(RG_ACCOUNTS). 밖이면 400 — 오타로 유령 상태행이 생기지 않게."""
    if account_key not in rg_settlement_sync.RG_ACCOUNTS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 account_key: {account_key}")
    return account_key


@router.post("/wing/vendor-summary/request-refresh")
def request_wing_vendor_summary_refresh(
    account_key: str = Query(default="COUPANG_WING1", description="COUPANG_WING1/2"),
    db: Session = Depends(get_db),
):
    """UI '판매분석 갱신' 버튼 → 갱신 요청 플래그 set. 해당 계정 데몬이 다음 폴링에서 소비.

    ★UI는 WING1만 요청한다(WING2 판매분석은 휴면) — 파라미터는 큐 차원을 맞추기 위한 것.
    """
    return vendor_summary_sync.request_refresh(db, _require_rg_account(account_key))


@router.get("/wing/vendor-summary/refresh-status")
def wing_vendor_summary_refresh_status(
    account_key: str = Query(default="COUPANG_WING1", description="COUPANG_WING1/2"),
    db: Session = Depends(get_db),
):
    """갱신 요청/완료 상태. UI(버튼 후 폴링)·Wing 페처(요청 확인) 공용."""
    return vendor_summary_sync.refresh_status(db, _require_rg_account(account_key))


@router.post("/wing/vendor-summary/refresh-claim")
def claim_wing_vendor_summary_refresh(
    account_key: str = Query(default="COUPANG_WING1", description="COUPANG_WING1/2"),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Wing 페처가 갱신 요청을 **임대**(lease). 토큰 인증(ingest와 동일).

    claim은 플래그를 지우지 않는다(2026-07-27 lease 계약).
    ★account_key 격리: WING2 데몬이 WING1의 버튼 요청을 훔쳐가지 못한다(claim 경쟁 차단).
    """
    _require_ingest_token(x_ingest_token)
    return vendor_summary_sync.claim_refresh(db, _require_rg_account(account_key))


@router.post("/wing/vendor-summary/fetch-error")
def wing_vendor_summary_fetch_error(
    body: dict[str, Any] = Body(...),
    account_key: str = Query(default="COUPANG_WING1", description="COUPANG_WING1/2"),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Wing 판매분석 페처 run 실패 보고 → last_error/last_error_at 기록. 토큰 인증(claim과 동일).

    ★claim의 짝: 페처가 요청을 claim한 뒤 브라우저 에러로 죽으면 플래그는 이미 clear라 아무
    흔적도 안 남았다 — UI는 실패인지 진행 중인지 알 수 없었다(215초 헛기다림).
    성공은 ingest가 알리고(last_success_at), 실패는 이 엔드포인트가 알린다.
    """
    _require_ingest_token(x_ingest_token)
    error = str(body.get("error") or "").strip() or "unknown"
    # kind(옵션, 하위호환): 구버전 페처는 안 보낸다 → None = 평범한 실패 = 재시도 대상.
    # "login_required"만 특별 취급(재시도 제외, PLAN §0 금지선).
    kind = str(body.get("kind") or "").strip() or None
    # lease(옵션, codex 1R[P1]): claim 응답의 임대 식별자. 지금 유효한 임대와 다르면 stale
    # 보고로 무시한다(20분 넘게 멈췄던 옛 시도가 남의 임대를 반납하는 것 차단). 없으면 기존 동작.
    lease = str(body.get("lease") or "").strip() or None
    vendor_summary_sync.mark_fetch_error(
        db, error, account_key=_require_rg_account(account_key), kind=kind, lease=lease)
    return {"ok": True}


# ── RG 정산 자동 다운로드 갱신 트리거 (Wing 세션 자동화 트랙 S4-P2, D-8) ──
# vendor-summary와 동일 메커니즘이되 상태행 분리(COUPANG_WING_RG/_RG2). 페처 데몬이 claim 후
# 정산 엑셀 브라우저측 다운로드 → /rg/settlement/upload-xlsx 로 push(기존 ingest 재사용).
@router.post("/wing/rg-settlement/request-refresh")
def request_wing_rg_settlement_refresh(
    account_key: str = Query(default="COUPANG_WING1", description="COUPANG_WING1/2"),
    db: Session = Depends(get_db),
):
    """UI 'RG 정산 갱신' 버튼/스케줄 → 갱신 요청 플래그 set. 해당 계정 데몬이 다음 폴링에서 소비."""
    return rg_settlement_sync.rg_request_refresh(db, _require_rg_account(account_key))


@router.get("/wing/rg-settlement/refresh-status")
def wing_rg_settlement_refresh_status(
    account_key: str = Query(default="COUPANG_WING1", description="COUPANG_WING1/2"),
    db: Session = Depends(get_db),
):
    """RG 정산 갱신 요청/완료 상태. UI(버튼 후 폴링)·Wing 페처(요청 확인) 공용."""
    return rg_settlement_sync.rg_refresh_status(db, _require_rg_account(account_key))


@router.post("/wing/rg-settlement/refresh-claim")
def claim_wing_rg_settlement_refresh(
    account_key: str = Query(default="COUPANG_WING1", description="COUPANG_WING1/2"),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Wing 페처가 RG 갱신 요청을 **임대**(lease). 토큰 인증(ingest와 동일).

    claim은 플래그를 지우지 않는다(2026-07-27 lease 계약) — RG는 run이 끝날 때
    refresh-complete로 요청을 소멸시킨다.
    ★account_key 격리: WING2 데몬이 WING1의 버튼 요청을 훔쳐가지 못한다(claim 경쟁 차단).
    """
    _require_ingest_token(x_ingest_token)
    return rg_settlement_sync.rg_claim_refresh(db, _require_rg_account(account_key))


@router.post("/wing/rg-settlement/fetch-error")
def wing_rg_settlement_fetch_error(
    body: dict[str, Any] = Body(...),
    account_key: str = Query(default="COUPANG_WING1", description="COUPANG_WING1/2"),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Wing RG 정산 페처 run 실패 보고 → last_error/last_error_at 기록. 토큰 인증(claim과 동일).

    ★claim의 짝: 페처가 요청을 claim한 뒤 브라우저 에러로 죽으면 플래그는 이미 clear라 아무
    흔적도 안 남았다 — UI는 실패인지 진행 중인지 알 수 없었다(215초 헛기다림).
    성공은 upload-xlsx(rg_mark_heartbeat)가 알리고, 실패는 이 엔드포인트가 알린다.
    """
    _require_ingest_token(x_ingest_token)
    error = str(body.get("error") or "").strip() or "unknown"
    # kind(옵션, 하위호환): 구버전 페처는 안 보낸다 → None = 평범한 실패 = 재시도 대상.
    # "login_required"만 특별 취급(재시도 제외, PLAN §0 금지선).
    kind = str(body.get("kind") or "").strip() or None
    # lease(옵션, codex 1R[P1]): claim 응답의 임대 식별자. 지금 유효한 임대와 다르면 stale
    # 보고로 무시한다(20분 넘게 멈췄던 옛 시도가 남의 임대를 반납하는 것 차단). 없으면 기존 동작.
    lease = str(body.get("lease") or "").strip() or None
    rg_settlement_sync.rg_mark_fetch_error(
        db, error, account_key=_require_rg_account(account_key), kind=kind, lease=lease)
    return {"ok": True}


@router.post("/wing/rg-settlement/refresh-complete")
def wing_rg_settlement_refresh_complete(
    body: dict[str, Any] = Body(default={}),
    account_key: str = Query(default="COUPANG_WING1", description="COUPANG_WING1/2"),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """RG run이 성공으로 끝났음을 알려 갱신 요청을 소멸시킨다(lease 계약의 '요청 완료' 신호).

    ★왜 별도 신호가 필요한가: RG의 성공 heartbeat는 엑셀 upload-xlsx에서만 찍힌다. 그런데
    "이번 주 정산주기가 없어 받을 게 없었다"처럼 **정상 완주했지만 업로드가 0건**인 회차가
    있다. lease 계약에선 요청이 성공 신호로만 소멸하므로, 이 신호가 없으면 그런 회차가
    재시도 3회(=창 3번)를 유발한 뒤 "재시도 3회 소진"이라는 거짓 실패로 끝난다.
    ★last_success_at(데이터 신선도 시계)은 건드리지 않는다 — 받은 게 없으면 데이터는 그대로다.
    이미 업로드가 요청을 소멸시킨 뒤라면 이 호출은 무해한 no-op.

    ★account_key(R3, 2026-07-27 버그 수정): 형제 엔드포인트(request/status/claim/fetch-error)는
    모두 계정별 상태행을 쓰는데 이 완료 신호만 WING1 행을 하드코딩하고 있었다 — WING2 run이
    완주하면 (a)자기 요청은 안 지워져 창 3번 뒤 거짓 '3회 소진'으로 끝나고 (b)신호는 WING1 행으로
    가서 lease 불일치로 버려졌다(mark_success가 stale로 접음). 즉 이 엔드포인트가 막으려고
    만들어진 바로 그 거짓 실패가 WING2에서 그대로 재현된다. 기본값 WING1로 하위호환 유지 —
    account_key를 안 보내는 구버전 페처는 지금과 똑같이 동작한다.
    """
    _require_ingest_token(x_ingest_token)
    # clear_error=True(codex 2R[P2]): 1회차가 실패하고 2회차가 "받을 게 없어" 정상 종료하면
    # last_error_at만 바뀐 채 요청이 사라진다 → UI가 성공한 회차를 실패로 읽는다. 실패 흔적을
    # 함께 지우되 last_success_at(데이터 신선도 시계)은 그대로 둔다.
    # lease(옵션): 내 임대에 대해서만 완료 처리(codex 3R[P2]). 20분 넘게 끌던 옛 run이
    # 임대를 넘긴 뒤 뒤늦게 완료를 외쳐 새 요청을 지우는 것을 막는다. 없으면 기존 동작.
    lease = str(body.get("lease") or "").strip() or None
    ok = refresh_contract.mark_success(
        db, rg_settlement_sync._rg_state_key(_require_rg_account(account_key)),
        clear_error=True, lease=lease)
    return {"ok": ok}


@router.get("/wing/rg-settlement/layer2-gaps")
def wing_rg_settlement_layer2_gaps(
    account_key: str = Query(default="COUPANG_WING1", description="COUPANG_WING1/2"),
    days: int = Query(default=rg_settlement_sync.LAYER2_GAP_DEFAULT_DAYS, ge=1, le=400,
                      description="조회 창(일) — 페처의 rg_status_days와 짝"),
    report_types: list[str] | None = Query(default=None, description="sellerReportType 반복 파라미터"),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """층2(옵션 엑셀) 결손 주기 조회 — **읽기 전용**(PLAN_rg-layer2-gap-driven-selfheal D1).

    Mac 페처가 층1 push 직후 호출해 "무엇이 비었는지"를 받아 결손 주기만 다운로드한다.
    이게 없으면 페처는 매 회차 최신 1주기만 받아 캐던스가 느려질 때마다 영구 공백이 생긴다.
    인증은 형제 페처용 엔드포인트(refresh-claim·ingest)와 동일한 X-Ingest-Token — 이 응답은
    페처 전용이고 UI는 쓰지 않는다.
    """
    _require_ingest_token(x_ingest_token)
    return rg_settlement_sync.layer2_gaps(
        db, _require_rg_account(account_key), days=days, report_types=report_types)


# ════════════════════════════════════════════════════════════════════
# RG 정산 옵션 단위 수집 (S6 — 종류별 엑셀 수동 업로드, D-2)
# ════════════════════════════════════════════════════════════════════
def _vendor_id_to_account_key(vendor_id: str) -> str | None:
    """파일명 vendor_id(예 A01564720) → account_key(COUPANG_WING1/2). env {ACK}_VENDOR_ID 비교."""
    for ak in rg_settlement_sync.RG_ACCOUNTS:
        cfg = get_coupang_config(ak)
        if cfg and cfg.vendor_id == vendor_id:
            return ak
    return None


@router.post("/rg/settlement/upload-xlsx")
async def upload_rg_settlement_xlsx(
    file: UploadFile = File(...),
    account_key: str | None = Query(default=None, description="미지정 시 파일명 vendor_id로 자동 매핑"),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """RG 종류별 정산 엑셀(WAREHOUSING_SHIPPING 등) 업로드 → 옵션 단위 적재(S6, D-2).

    파일명 형식: {vendor_id}-{REPORT_TYPE}-ko-{uuid}.xlsx (예 A01564720-WAREHOUSING_SHIPPING-ko-*.xlsx).
    account_key 미지정 시 파일명 vendor_id로 자동 매핑(COUPANG_WING1/2).
    옵션 cost=할인적용가(A-B) VAT前(§8-1). 검산: Σ상세==요약합계, 요약최종 vs status/api 계정 row.
    **인증(S4-P2)**: X-Ingest-Token 필수 — prod 회계(net_profit 소스) 변경 엔드포인트이므로
    무인증 외부 POST로 데이터 오염을 막는다(vendor-summary ingest와 동일 토큰). Mac 페처 자동 push·
    수동 업로드(curl/스크립트) 모두 헤더에 토큰 포함. (프론트에서는 호출하지 않음 — UI 무영향.)
    """
    _require_ingest_token(x_ingest_token)
    filename = file.filename or ""
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="xlsx 파일만 업로드 가능합니다.")
    _vid_match = re.match(r"(A\d+)-", filename)
    if account_key is None:
        if not _vid_match:
            raise HTTPException(
                status_code=400,
                detail="파일명에서 vendor_id를 찾을 수 없습니다. account_key를 지정하거나 "
                       "{vendor_id}-{REPORT_TYPE}-ko-*.xlsx 형식을 사용하세요.",
            )
        account_key = _vendor_id_to_account_key(_vid_match.group(1))
        if account_key is None:
            raise HTTPException(status_code=400, detail=f"vendor_id {_vid_match.group(1)}에 해당하는 계정 설정이 없습니다.")
    elif _vid_match is not None:
        # account_key 명시 + 파일명에 vendor_id 존재 → 일치 검증(codex P2: 계정 오배치 방지).
        # 미등록 vendor_id(mapped is None)도 reject — 둘 다 있는데 매핑 안 되면 오배치 위험.
        mapped = _vendor_id_to_account_key(_vid_match.group(1))
        if mapped != account_key:
            raise HTTPException(
                status_code=400,
                detail=f"지정한 account_key({account_key})와 파일명 vendor_id({_vid_match.group(1)}"
                       f"→{mapped or '미등록'})가 일치하지 않습니다.",
            )
    if account_key not in rg_settlement_sync.RG_ACCOUNTS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 account_key: {account_key}")

    content = await file.read()
    try:
        # ★run_in_threadpool 필수(2026-07-17 사고): 이 라우터 82개 엔드포인트 중 79개는 `def`라
        # FastAPI가 알아서 스레드풀로 보내지만, 업로드 2개만 `async def`(await file.read() 때문)여서
        # 동기 ingest가 **이벤트 루프 위에서** 돌았다. prod는 uvicorn --workers 1 단일 워커라
        # 그 시간 동안 모든 API가 멈춘다(같은 시각 fetch-error POST도 15초 타임아웃). 파싱이
        # 빨라진 지금도(0.06초) 파일이 커지면 재발하므로 구조로 막는다.
        result = await run_in_threadpool(
            rg_settlement_sync.ingest_settlement_xlsx, db, account_key, content
        )
    except WingReadError as e:
        raise HTTPException(status_code=422, detail=f"엑셀 파싱 실패: {e}")
    # S4-P2: 자동 다운로드(Mac 페처 push)·수동 업로드 모두 freshness 갱신(스케줄 중복방지·상태표시).
    # ★계정별 상태행에 기록(2026-07-27): WING2 push가 WING1 행을 갱신하면 오픽스가 실제론 안
    #   왔는데 "갱신 완료"로 보이고(UI 폴링이 남의 성공에 종료됨) 신선도 배너도 거짓말한다.
    rg_settlement_sync.rg_mark_heartbeat(db, account_key)
    return result


@router.post("/wing/rg-settlement/ingest-status")
def ingest_rg_settlement_status(
    payload: dict = Body(...),
    account_key: str = Query(..., description="COUPANG_WING1/2 — RG_ACCOUNTS 검증"),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Mac 상주 브라우저 페처 → status/api raw JSON 인제스트 → 계정 단위 정산 수수료 적재(층1).

    ★정적 쿠키 경로(sync_coupang_rg_settlement 크론, WING1/2 쿠키)를 이관한다: Mac 세션이 매일
    받던 status/api JSON을 그대로 push해 계정 row를 적재(새 인증·새 세션 없음). 회계(net_profit
    소스) 변경 엔드포인트이므로 upload-xlsx와 동일 방어 수위 — X-Ingest-Token 필수 +
    account_key ∈ RG_ACCOUNTS + body=raw JSON dict. 스키마 드리프트(WingReadError)는 422.
    ★rg_mark_heartbeat 호출 안 함: 그건 엑셀 push 캐던스 상태행 전용(계정별 COUPANG_WING_RG/_RG2)이다.
    이 경로의 신선도는 적재된 데이터 자체(data_stale 감시)로 검증한다. 프론트 호출 없음.
    """
    _require_ingest_token(x_ingest_token)
    if account_key not in rg_settlement_sync.RG_ACCOUNTS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 account_key: {account_key}")
    try:
        return rg_settlement_sync.ingest_status_payload(db, account_key, payload)
    except WingReadError as e:
        raise HTTPException(status_code=422, detail=f"status/api 파싱 실패: {e}")


# ════════════════════════════════════════════════════════════════════
# RG 정산 자동 다운로드 + 적재 (S6-auto — Wing 세션쿠키 필요)
# ════════════════════════════════════════════════════════════════════

@router.post("/rg/settlement/auto-download")
def auto_download_rg_settlement(
    account_key: str = Query(
        default=None,
        description="미지정 시 모든 RG 계정(COUPANG_WING1/2) 순차 실행",
    ),
    db: Session = Depends(get_db),
):
    """S6-auto: DB 정산 기간별 × 종류별 엑셀 자동 생성 요청·폴링·다운로드·적재.

    Wing 세션쿠키(CGSID_PARTNERADMINWEB 등)가 DB에 저장되어 있어야 동작.
    account_key 미지정 시 모든 RG 계정 순차 실행.
    반환: {results: [{account_key, requested, completed, ingested, errors, status}]}
    """
    try:
        if account_key:
            if account_key not in rg_settlement_sync.RG_ACCOUNTS:
                raise HTTPException(status_code=400, detail=f"지원하지 않는 account_key: {account_key}")
            cfg = get_coupang_config(account_key)
            if not cfg or not cfg.vendor_id:
                raise HTTPException(status_code=400, detail=f"{account_key} vendor_id 설정 없음")
            result = rg_settlement_sync.auto_download_and_ingest(db, account_key, cfg.vendor_id)
            return {"results": [result]}
        else:
            vendor_id_map: dict[str, str] = {}
            for ak in rg_settlement_sync.RG_ACCOUNTS:
                cfg = get_coupang_config(ak)
                if cfg and cfg.vendor_id:
                    vendor_id_map[ak] = cfg.vendor_id
            results = rg_settlement_sync.auto_download_all(db, vendor_id_map)
            return {"results": results}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("S6-auto 실행 오류")
        raise HTTPException(status_code=500, detail=f"S6-auto 오류: {e}")


@router.get("/rg/fee-audit")
def get_rg_fee_audit(
    db: Session = Depends(get_db),
    account_key: str | None = Query(None, description="특정 RG 계정만(미지정=전체)"),
    company: str | None = Query(None, description="회사 필터(오픽스/오하이테크/ALL) — account_key보다 우선"),
    date_from: date | None = Query(None, description="매출인식일 시작(미지정=전체)"),
    date_to: date | None = Query(None, description="매출인식일 종료(미지정=전체)"),
):
    """S8: RG 청구액 과오청구 감사 — 옵션별 치수→예상 사이즈 유형 vs 실청구 정합성 스크리닝.

    SA1(분류)·SA2(floor)·SA3(이상치)를 rg_fee_audit Harness가 가로질러 호출(원칙 18-7).
    읽기 전용·net_profit 불변(D-17). 플래그는 사람 검토 신호이지 과오청구 확정 아님(D-5).
    반환: {generated_at·account_key·기간·summary·items·disclaimer}.
    """
    if company and company not in ("ALL", "전체"):
        account_key = _RG_ACCOUNT_BY_COMPANY.get(company, "__unknown__")
    return rg_fee_audit.build_fee_audit(db, account_key, date_from=date_from, date_to=date_to)


@router.post("/rg/product-size/upload-xlsx")
async def upload_product_size_xlsx(
    file: UploadFile = File(...),
    source_group_key: str | None = Query(default=None, description="출처 정산주기(예 A01564720-2026-06-08-2026-06-14)"),
    x_ingest_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """쿠팡 PRODUCT_SIZE_COMPARISON XLSX 업로드 → coupang_product_size upsert.

    Wing 페처가 자동 push 하거나 수동 업로드 가능. 인증: X-Ingest-Token 필수.
    """
    _require_ingest_token(x_ingest_token)
    filename = file.filename or ""
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="xlsx 파일만 업로드 가능합니다.")
    content = await file.read()
    # 위 upload-xlsx와 동일 이유로 스레드풀 오프로드(`async def` + 동기 파싱 = 단일 워커 전체 정지).
    result = await run_in_threadpool(
        rg_product_size_sync.ingest_product_size, db, content, source_group_key=source_group_key
    )
    return result


@router.get("/rg/product-size")
def get_product_size(
    vendor_item_id: str | None = Query(None, description="특정 옵션ID 조회(미지정=전체)"),
    db: Session = Depends(get_db),
):
    """DB에 저장된 쿠팡 실측 사이즈 조회."""
    from app.models import CoupangProductSize
    q = db.query(CoupangProductSize)
    if vendor_item_id:
        q = q.filter(CoupangProductSize.vendor_item_id == vendor_item_id)
    rows = q.all()
    return [
        {
            "vendor_item_id": r.vendor_item_id,
            "size_type": r.size_type,
            "product_name": r.product_name,
            "option_name": r.option_name,
            "source_group_key": r.source_group_key,
            "synced_at": r.synced_at.isoformat() if r.synced_at else None,
        }
        for r in rows
    ]

