# routers/orders.py — 주문 조회 API
from __future__ import annotations

from app.utils.kst import kst_now, kst_today
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Channel, Order, ProductMaster
from app.schemas import OrderListResponse, OrderOut, ProfitSummary

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.get("", response_model=OrderListResponse)
def list_orders(
    channel_id: int | None = Query(None),
    status: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """주문 목록 조회 (필터 + 페이지네이션)"""
    query = db.query(Order)

    if channel_id:
        query = query.filter(Order.channel_id == channel_id)
    if status:
        query = query.filter(Order.status == status)
    if date_from:
        query = query.filter(Order.order_date >= datetime.fromisoformat(date_from))
    if date_to:
        dt = datetime.fromisoformat(date_to)
        query = query.filter(Order.order_date < dt + timedelta(days=1))
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            (Order.order_number.ilike(pattern))
            | (Order.platform_product_name.ilike(pattern))
        )

    total = query.count()
    orders = (
        query.order_by(Order.order_date.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    # 채널명, 상품명 조인
    channel_map = {c.id: c.name for c in db.query(Channel).all()}
    product_map = {p.id: p.product_name for p in db.query(ProductMaster).all()}

    items = []
    for o in orders:
        items.append(OrderOut(
            id=o.id,
            channel_id=o.channel_id,
            channel_name=channel_map.get(o.channel_id, ""),
            product_id=o.product_id,
            product_name=product_map.get(o.product_id) if o.product_id else None,
            order_number=o.order_number,
            platform_product_id=o.platform_product_id,
            platform_product_name=o.platform_product_name,
            quantity=o.quantity,
            selling_price=o.selling_price,
            shipping_cost=o.shipping_cost,
            order_date=o.order_date,
            status=o.status,
            created_at=o.created_at,
        ))

    return OrderListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/summary", response_model=ProfitSummary)
def profit_summary(
    channel_id: int | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """주문 기반 순이익 요약 (필터 적용)

    순이익 = 매출 - 원가 - 수수료 - 광고비 - 배송비 - VAT(10/110)
    profit_calculator를 사용하여 광고비까지 포함한 정확한 계산 수행
    """
    from app.database import get_ad_db
    from app.services.profit_calculator import calculate_daily_trend

    if date_from is None:
        df = (kst_now() - timedelta(days=365)).date()
    else:
        df = datetime.fromisoformat(date_from).date()

    if date_to is None:
        dt = kst_now().date()
    else:
        dt = datetime.fromisoformat(date_to).date()

    # ad_data.db 세션
    ad_db = None
    gen = get_ad_db()
    if gen is not None:
        try:
            ad_db = next(gen)
        except StopIteration:
            ad_db = None

    try:
        trend = calculate_daily_trend(db, ad_db, channel_id, df, dt)

        if not trend:
            return ProfitSummary()

        total_revenue = sum(Decimal(p["revenue"]) for p in trend)
        total_cost = sum(Decimal(p["cost"]) for p in trend)
        total_commission = sum(Decimal(p["commission"]) for p in trend)
        total_ad_spend = sum(Decimal(p["ad_spend"]) for p in trend)
        total_shipping = sum(Decimal(p["shipping"]) for p in trend)
        total_vat = sum(Decimal(p["vat"]) for p in trend)
        net_profit = sum(Decimal(p["net_profit"]) for p in trend)
        order_count = sum(p["order_count"] for p in trend)

        return ProfitSummary(
            total_revenue=total_revenue,
            total_cost=total_cost,
            total_commission=total_commission,
            total_ad_spend=total_ad_spend,
            total_shipping=total_shipping,
            total_vat=total_vat,
            net_profit=net_profit,
            order_count=order_count,
        )
    finally:
        if ad_db is not None:
            try:
                ad_db.close()
            except Exception:
                pass
