# actual_revenue.py — actual_revenue_sa (단일 책임: 네이버 실주문 매출 집계)
# 역할(SA): 기간의 네이버 채널 실주문 매출(Order.selling_price 합)을 반환.
#   naver_ops sales-summary(§주문 집계)와 동일 기준: 매출제외 상태 필터, selling_price=라인총액.
# 3열 ROAS 중 '실주문 대조'(D-NAO-7)용. 주문은 캠페인 미귀속 → 계정 총계 전용.
from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import Order
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED

_NAVER_CHANNEL_ID = 6


def naver_order_revenue(db: Session, date_from: date, date_to: date) -> dict:
    """네이버 채널 실주문 매출·건수 (order_date 기준, 매출제외 상태 제외).

    selling_price=totalPaymentAmount=라인총액(이미 ×수량, 2중계상 없음, naver_ops S2와 정합).
    반환: {revenue: int(원), order_count: int(주문번호 distinct)}.
    """
    start = datetime.combine(date_from, time.min)
    end = datetime.combine(date_to, time.max)

    base = db.query(Order).filter(
        Order.channel_id == _NAVER_CHANNEL_ID,
        Order.status.notin_(tuple(REVENUE_EXCLUDED)),
        Order.order_date >= start,
        Order.order_date <= end,
    )
    revenue = base.with_entities(
        sqlfunc.coalesce(sqlfunc.sum(Order.selling_price), 0)
    ).scalar()
    order_count = base.with_entities(
        sqlfunc.count(sqlfunc.distinct(Order.order_number))
    ).scalar()

    return {"revenue": int(revenue or 0), "order_count": int(order_count or 0)}
