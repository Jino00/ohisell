# routers/dashboard.py — 대시보드 API (트렌드, KPI, 채널/상품 분석)
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_ad_db, get_db
from app.schemas import (
    DashboardKPI,
    GroupedSummaryRow,
    GroupedTrendPoint,
    ProductProfitRow,
    TrendPoint,
)
from app.services.profit_calculator import (
    calculate_channel_daily_trend,
    calculate_channel_summary,
    calculate_daily_trend,
    calculate_product_profit,
    get_channel_company_map,
    group_summary_by_company,
    group_trend_by_company,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _default_dates(
    date_from: date | None, date_to: date | None, period: str
) -> tuple[date, date]:
    """기간 파라미터 기본값 설정"""
    if date_to is None:
        date_to = date.today()
    if date_from is None:
        if period == "weekly":
            date_from = date_to - timedelta(weeks=12)
        elif period == "monthly":
            date_from = date_to - timedelta(days=365)
        else:
            date_from = date_to - timedelta(days=30)
    return date_from, date_to


def _get_ad_session(ad_db_gen=Depends(get_ad_db)):
    """ad_db 제너레이터를 세션으로 변환 (None 허용)"""
    if ad_db_gen is None:
        return None
    return ad_db_gen


def _resolve_ad_db():
    """ad_data.db 세션을 안전하게 가져오기"""
    gen = get_ad_db()
    if gen is None:
        return None
    try:
        return next(gen)
    except StopIteration:
        return None


@router.get("/trend", response_model=list[TrendPoint])
def dashboard_trend(
    period: str = Query("daily", regex="^(daily|weekly|monthly)$"),
    channel_id: Optional[int] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """일별/주별/월별 매출-비용 추이"""
    df, dt = _default_dates(date_from, date_to, period)
    ad_db = _resolve_ad_db()

    try:
        daily_data = calculate_daily_trend(db, ad_db, channel_id, df, dt)

        if period == "daily":
            return daily_data

        # 주별/월별 그룹핑
        grouped: dict[str, dict] = {}
        for point in daily_data:
            d = point["date"]
            if period == "weekly":
                # ISO 주차 기준 그룹핑
                dt_obj = date.fromisoformat(d)
                iso = dt_obj.isocalendar()
                key = f"{iso[0]}-W{iso[1]:02d}"
            else:  # monthly
                key = d[:7]  # YYYY-MM

            if key not in grouped:
                grouped[key] = {
                    "revenue": Decimal("0"), "cost": Decimal("0"),
                    "commission": Decimal("0"), "ad_spend": Decimal("0"),
                    "shipping": Decimal("0"), "vat": Decimal("0"),
                    "net_profit": Decimal("0"), "order_count": 0,
                }

            g = grouped[key]
            g["revenue"] += Decimal(point["revenue"])
            g["cost"] += Decimal(point["cost"])
            g["commission"] += Decimal(point["commission"])
            g["ad_spend"] += Decimal(point["ad_spend"])
            g["shipping"] += Decimal(point["shipping"])
            g["vat"] += Decimal(point["vat"])
            g["net_profit"] += Decimal(point["net_profit"])
            g["order_count"] += point["order_count"]

        result = []
        for key in sorted(grouped.keys()):
            g = grouped[key]
            result.append({
                "date": key,
                "revenue": str(g["revenue"]),
                "cost": str(g["cost"]),
                "commission": str(g["commission"]),
                "ad_spend": str(g["ad_spend"]),
                "shipping": str(g["shipping"]),
                "vat": str(g["vat"]),
                "net_profit": str(g["net_profit"]),
                "order_count": g["order_count"],
            })
        return result
    finally:
        if ad_db is not None:
            try:
                ad_db.close()
            except Exception:
                pass


@router.get("/kpi", response_model=DashboardKPI)
def dashboard_kpi(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """핵심 KPI (매출, 순이익, 이익률, 전기 대비 변화율)"""
    df, dt = _default_dates(date_from, date_to, "daily")
    ad_db = _resolve_ad_db()

    try:
        current = calculate_daily_trend(db, ad_db, None, df, dt)

        # 현재 기간 합산
        rev = sum(Decimal(p["revenue"]) for p in current)
        net = sum(Decimal(p["net_profit"]) for p in current)
        orders = sum(p["order_count"] for p in current)
        rate = (net / rev * 100) if rev > 0 else Decimal("0")

        # 이전 기간 (동일 길이)
        period_days = (dt - df).days
        prev_to = df - timedelta(days=1)
        prev_from = prev_to - timedelta(days=period_days)
        prev = calculate_daily_trend(db, ad_db, None, prev_from, prev_to)

        prev_rev = sum(Decimal(p["revenue"]) for p in prev)
        prev_net = sum(Decimal(p["net_profit"]) for p in prev)

        rev_change = float((rev - prev_rev) / prev_rev * 100) if prev_rev > 0 else None
        profit_change = float((net - prev_net) / prev_net * 100) if prev_net > 0 else None

        return DashboardKPI(
            total_revenue=str(rev),
            net_profit=str(net),
            profit_rate=str(rate.quantize(Decimal("0.01"))),
            order_count=orders,
            revenue_change_pct=round(rev_change, 2) if rev_change is not None else None,
            profit_change_pct=round(profit_change, 2) if profit_change is not None else None,
        )
    finally:
        if ad_db is not None:
            try:
                ad_db.close()
            except Exception:
                pass


@router.get("/channel-breakdown", response_model=list[GroupedSummaryRow])
def channel_breakdown(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """회사 > leaf 계층 그룹 요약 (전체/회사소계/leaf)"""
    df, dt = _default_dates(date_from, date_to, "daily")
    ad_db = _resolve_ad_db()

    try:
        rows = calculate_channel_summary(db, ad_db, df, dt)
        return group_summary_by_company(rows, get_channel_company_map(db))
    finally:
        if ad_db is not None:
            try:
                ad_db.close()
            except Exception:
                pass


@router.get("/trend-by-channel", response_model=list[GroupedTrendPoint])
def trend_by_channel(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """회사 leaf 그룹 단위 일자별 매출/광고비/순이익 추이"""
    df, dt = _default_dates(date_from, date_to, "daily")
    ad_db = _resolve_ad_db()

    try:
        pts = calculate_channel_daily_trend(db, ad_db, df, dt)
        return group_trend_by_company(pts, get_channel_company_map(db))
    finally:
        if ad_db is not None:
            try:
                ad_db.close()
            except Exception:
                pass


@router.get("/product-ranking", response_model=list[ProductProfitRow])
def product_ranking(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    sort_by: str = Query("revenue"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """상품별 이익률 랭킹"""
    df, dt = _default_dates(date_from, date_to, "daily")
    ad_db = _resolve_ad_db()

    try:
        return calculate_product_profit(db, ad_db, df, dt, sort_by=sort_by, limit=limit)
    finally:
        if ad_db is not None:
            try:
                ad_db.close()
            except Exception:
                pass
