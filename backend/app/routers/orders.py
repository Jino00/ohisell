# routers/orders.py — 주문 조회 API
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func
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
    """
    query = db.query(Order)

    if channel_id:
        query = query.filter(Order.channel_id == channel_id)
    if date_from:
        query = query.filter(Order.order_date >= datetime.fromisoformat(date_from))
    if date_to:
        dt = datetime.fromisoformat(date_to)
        query = query.filter(Order.order_date < dt + timedelta(days=1))

    orders = query.all()
    if not orders:
        return ProfitSummary()

    # 채널별 수수료율 조회
    channel_map = {c.id: c for c in db.query(Channel).all()}
    product_map = {p.id: p for p in db.query(ProductMaster).all()}

    total_revenue = Decimal("0")
    total_cost = Decimal("0")
    total_commission = Decimal("0")
    total_shipping = Decimal("0")

    for o in orders:
        revenue = o.selling_price * o.quantity
        total_revenue += revenue

        # 원가 (매핑된 상품이 있는 경우)
        if o.product_id and o.product_id in product_map:
            total_cost += product_map[o.product_id].cost_price * o.quantity

        # 수수료
        ch = channel_map.get(o.channel_id)
        if ch:
            total_commission += revenue * ch.commission_rate / Decimal("100")

        # 배송비 (None이면 0)
        if o.shipping_cost:
            total_shipping += o.shipping_cost

    # VAT = 매출의 10/110
    total_vat = total_revenue * Decimal("10") / Decimal("110")

    # 순이익 (광고비는 별도 API에서 조회, 여기서는 0)
    net_profit = total_revenue - total_cost - total_commission - total_shipping - total_vat

    return ProfitSummary(
        total_revenue=total_revenue,
        total_cost=total_cost,
        total_commission=total_commission,
        total_ad_spend=Decimal("0"),  # ad_costs 라우터에서 별도 제공
        total_shipping=total_shipping,
        total_vat=total_vat,
        net_profit=net_profit,
        order_count=len(orders),
    )
