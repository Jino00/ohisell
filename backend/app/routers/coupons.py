# routers/coupons.py — 쿠팡 쿠폰 운영 현황 조회 API (P5).
# 트랙 D-3: 사실/지표 정리만(전략 판단은 Jino). 회계축 아님 — 운영 현황 보조.
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CoupangCoupon, CoupangCouponBudget, CoupangCouponItem

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/coupons", tags=["coupons"])


def _s(v):
    """Decimal/datetime → str(JSON 직렬화). None 보존."""
    return str(v) if v is not None else None


@router.get("/coupang-coupons")
def coupang_coupons(
    coupon_kind: str | None = Query(None, description="INSTANT / DOWNLOAD"),
    status: str | None = Query(None, description="쿠폰 상태(STANDBY/APPLIED/...)"),
    account_key: str | None = Query(None),
    limit: int = Query(300, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """쿠팡 쿠폰 목록 (즉시할인+다운로드). 운영 현황만(D-3)."""
    q = db.query(CoupangCoupon)
    if coupon_kind:
        q = q.filter(CoupangCoupon.coupon_kind == coupon_kind)
    if status:
        q = q.filter(CoupangCoupon.status == status)
    if account_key:
        q = q.filter(CoupangCoupon.account_key == account_key)
    rows = q.order_by(CoupangCoupon.end_at.desc()).limit(limit).all()
    items = [
        {
            "coupon_id": r.coupon_id,
            "coupon_kind": r.coupon_kind,
            "account_key": r.account_key,
            "vendor_id": r.vendor_id,
            "contract_id": r.contract_id,
            "promotion_name": r.promotion_name,
            "status": r.status,
            "discount_type": r.discount_type,
            "discount": _s(r.discount),
            "max_discount_price": _s(r.max_discount_price),
            "start_at": _s(r.start_at),
            "end_at": _s(r.end_at),
            "wow_exclusive": r.wow_exclusive,
            "applied_option_count": r.applied_option_count,
            "usage_amount": _s(r.usage_amount),
        }
        for r in rows
    ]
    return {"count": len(items), "items": items}


@router.get("/coupang-coupon-items")
def coupang_coupon_items(
    vendor_item_id: str | None = Query(None, description="옵션ID(결합축 D-8)"),
    coupon_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """쿠팡 즉시할인쿠폰 아이템(옵션별 적용) 목록. vendor_item_id로 옵션별 쿠폰 적용 조회(D-8)."""
    q = db.query(CoupangCouponItem)
    if vendor_item_id:
        q = q.filter(CoupangCouponItem.vendor_item_id == vendor_item_id)
    if coupon_id:
        q = q.filter(CoupangCouponItem.coupon_id == coupon_id)
    if status:
        q = q.filter(CoupangCouponItem.status == status)
    rows = q.order_by(CoupangCouponItem.end_at.desc()).limit(limit).all()
    items = [
        {
            "coupon_item_id": r.coupon_item_id,
            "coupon_id": r.coupon_id,
            "vendor_item_id": r.vendor_item_id,
            "account_key": r.account_key,
            "status": r.status,
            "start_at": _s(r.start_at),
            "end_at": _s(r.end_at),
        }
        for r in rows
    ]
    return {"count": len(items), "items": items}


@router.get("/coupang-coupon-budgets")
def coupang_coupon_budgets(
    account_key: str | None = Query(None),
    target_month: str | None = Query(None, description="yyyy-MM, ''=계약메타"),
    limit: int = Query(300, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """쿠팡 쿠폰 예산/계약 현황. 분담율·월예산·사용액(운영 토대)."""
    q = db.query(CoupangCouponBudget)
    if account_key:
        q = q.filter(CoupangCouponBudget.account_key == account_key)
    if target_month is not None:
        q = q.filter(CoupangCouponBudget.target_month == target_month)
    rows = q.order_by(
        CoupangCouponBudget.contract_id, CoupangCouponBudget.target_month
    ).limit(limit).all()
    items = [
        {
            "contract_id": r.contract_id,
            "target_month": r.target_month,
            "account_key": r.account_key,
            "vendor_id": r.vendor_id,
            "vendor_share_ratio": _s(r.vendor_share_ratio),
            "total_budget_amount": _s(r.total_budget_amount),
            "used_budget_amount": _s(r.used_budget_amount),
            "seller_share_ratio": _s(r.seller_share_ratio),
            "coupang_share_ratio": _s(r.coupang_share_ratio),
            "contract_type": r.contract_type,
            "contract_start": _s(r.contract_start),
            "contract_end": _s(r.contract_end),
        }
        for r in rows
    ]
    return {"count": len(items), "items": items}
