# routers/dashboard.py — 대시보드 API (트렌드, KPI, 채널/상품 분석)
from __future__ import annotations

import logging
from app.utils.kst import kst_now, kst_today
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_ad_db, get_db
from app.models import Channel
from app.routers.ad_costs import _extract_meta_keyword, _extract_naver_sa_keyword, _upsert_ad_cost

log = logging.getLogger(__name__)
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
    get_rg_total_by_account,
    group_summary_by_company,
    group_trend_by_company,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _default_dates(
    date_from: date | None, date_to: date | None, period: str
) -> tuple[date, date]:
    """기간 파라미터 기본값 설정"""
    if date_to is None:
        date_to = kst_today()
    if date_from is None:
        if period == "weekly":
            date_from = date_to - timedelta(weeks=12)
        elif period == "monthly":
            date_from = date_to - timedelta(days=365)
        else:
            date_from = date_to - timedelta(days=30)
    return date_from, date_to


def _sync_orders_recent(db: Session) -> None:
    """대시보드 조회 시 API 채널 주문 최근 7일치 최신화 (실패해도 조회는 계속)"""
    from app.services.sync_service import sync_channel_orders
    sync_to = kst_today()
    sync_from = sync_to - timedelta(days=6)
    channels = db.query(Channel).filter(
        Channel.api_type != "excel",
        Channel.code != "COUPANG_ROCKET",
    ).all()
    for ch in channels:
        try:
            result = sync_channel_orders(db, ch.id, sync_from, sync_to)
            if result.get("status") == "success":
                log.info("주문 최신화: %s (신규 %s)", ch.name, result.get("new_orders"))
        except Exception as e:
            log.warning("주문 최신화 실패 (%s, 조회는 계속): %s", ch.name, e)


def _sync_ad_costs_for_period(db: Session, date_from: date, date_to: date) -> None:
    """대시보드 조회 시 SA/Meta 광고비 최신화 (upsert — 실패해도 조회는 계속)
    API 호출 범위는 최근 7일로 제한 — 과거분은 스케줄러가 적재한 데이터를 그대로 사용.
    """
    sync_from = max(date_from, date_to - timedelta(days=6))
    date_from, date_to = sync_from, date_to
    # 네이버 SA
    try:
        from app.services.naver_sa_ad_fetcher import fetch_campaign_daily_spend as _fetch_sa
        naver_row = db.execute(text("SELECT id FROM channels WHERE code='NAVER' LIMIT 1")).fetchone()
        if naver_row:
            agg: dict[tuple[str, str], Decimal] = {}
            for c in _fetch_sa(date_from, date_to):
                kw = _extract_naver_sa_keyword(c["campaign_name"])
                key = (c["date"], f"naver_sa:{kw}")
                agg[key] = agg.get(key, Decimal("0")) + c["spend"]
            for (dt_str, source), spend in agg.items():
                _upsert_ad_cost(db, naver_row[0], date.fromisoformat(dt_str), spend, source)
            db.commit()
            log.info("네이버 SA 광고비 최신화 완료 (%s ~ %s, %d건)", date_from, date_to, len(agg))
    except Exception as e:
        log.warning("네이버 SA 광고비 최신화 실패 (조회는 계속): %s", e)
        db.rollback()

    # Meta
    try:
        from app.services.meta_ad_fetcher import fetch_campaign_daily_spend as _fetch_meta
        cafe24_row = db.execute(text("SELECT id FROM channels WHERE code='CAFE24' LIMIT 1")).fetchone()
        if cafe24_row:
            agg2: dict[tuple[str, str], Decimal] = {}
            for c in _fetch_meta(date_from, date_to):
                kw = _extract_meta_keyword(c["campaign_name"])
                key = (c["date"], f"meta:{kw}")
                agg2[key] = agg2.get(key, Decimal("0")) + c["spend"]
            for (dt_str, source), spend in agg2.items():
                _upsert_ad_cost(db, cafe24_row[0], date.fromisoformat(dt_str), spend, source)
            db.commit()
            log.info("Meta 광고비 최신화 완료 (%s ~ %s, %d건)", date_from, date_to, len(agg2))
    except Exception as e:
        log.warning("Meta 광고비 최신화 실패 (조회는 계속): %s", e)
        db.rollback()


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
                    "revenue": Decimal("0"),
                    "product_revenue": Decimal("0"),
                    "shipping_revenue": Decimal("0"),
                    "cost": Decimal("0"),
                    "commission": Decimal("0"), "ad_spend": Decimal("0"),
                    "shipping": Decimal("0"), "vat": Decimal("0"),
                    "net_profit": Decimal("0"), "order_count": 0,
                }

            g = grouped[key]
            g["revenue"] += Decimal(point["revenue"])
            g["product_revenue"] += Decimal(point.get("product_revenue", "0"))
            g["shipping_revenue"] += Decimal(point.get("shipping_revenue", "0"))
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
                "product_revenue": str(g["product_revenue"]),
                "shipping_revenue": str(g["shipping_revenue"]),
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

        # RG 정산 수수료 차감 (B안: KPI 기간 합산)
        rg_by_account = get_rg_total_by_account(db, df, dt)
        net -= sum(rg_by_account.values(), Decimal("0"))

        rate = (net / rev * 100) if rev > 0 else Decimal("0")

        # 이전 기간 (동일 길이)
        period_days = (dt - df).days
        prev_to = df - timedelta(days=1)
        prev_from = prev_to - timedelta(days=period_days)
        prev = calculate_daily_trend(db, ad_db, None, prev_from, prev_to)

        prev_rev = sum(Decimal(p["revenue"]) for p in prev)
        prev_net = sum(Decimal(p["net_profit"]) for p in prev)

        # 이전 기간 RG 차감
        rg_prev = get_rg_total_by_account(db, prev_from, prev_to)
        prev_net -= sum(rg_prev.values(), Decimal("0"))

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
    _sync_orders_recent(db)
    _sync_ad_costs_for_period(db, df, dt)
    ad_db = _resolve_ad_db()

    try:
        rows = calculate_channel_summary(db, ad_db, df, dt)

        # RG 정산 수수료 차감 (B안: account_key=채널code로 매핑)
        rg_by_account = get_rg_total_by_account(db, df, dt)
        if rg_by_account:
            ch_map = {ch.id: ch for ch in db.query(Channel).all()}
            for row in rows:
                ch = ch_map.get(row["channel_id"])
                if ch and ch.code in rg_by_account and row["net_profit"] is not None:
                    row["net_profit"] = str(Decimal(row["net_profit"]) - rg_by_account[ch.code])

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
    _sync_orders_recent(db)
    _sync_ad_costs_for_period(db, df, dt)
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
