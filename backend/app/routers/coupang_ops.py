# coupang_ops.py — 쿠팡 쓰기 라우터 (트랙 §5 Layer3 coupang_ops, D-16 쓰기 페이즈).
# W3a 상품 단순쓰기 9개(재고·가격·할인율기준가·판매중지/재개·자동생성옵션 활성/비활성).
# 전부 dry_run=true 기본 — 라이브 실행은 dry_run=false + confirm 토큰 필요(이중확인).
# 변경값은 query param(쿠팡 원 API가 path/query, body 없음 — 명세 02 §4). Harness가 dry_run 게이트.
# 트랙 D-8: 쿠팡 Open API는 서버 IP 화이트리스트 → 라이브 호출은 서버에서만(로컬 403).
from __future__ import annotations

import logging

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.config import get_coupang_config
from app.database import get_db
from app.models import CoupangProductItem
from app.routers._coupang_write_http import handle_write as _handle_write
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
