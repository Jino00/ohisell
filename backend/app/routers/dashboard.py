# routers/dashboard.py — 대시보드 API (트렌드, KPI, 채널/상품 분석)
from __future__ import annotations

import logging
import os
import threading
import time
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
from app.services.coupang.rocket_1p_channel_pnl import (
    BASIS_SETTLEMENT,
    compute_rocket_1p_daily_points,
    compute_rocket_1p_summary_row,
    normalize_basis,
    rocket_1p_channel,
)
from app.services.profit_calculator import (
    NET_SCOPE_FULL,
    NET_SCOPE_PARTIAL,
    calculate_channel_daily_trend,
    calculate_channel_summary,
    calculate_daily_trend,
    calculate_product_profit,
    get_channel_company_map,
    get_rg_total_by_account,
    group_summary_by_company,
    group_trend_by_company,
    net_contribution,
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


# ──────────────────────────────────────────────
# 조회 시 동기화 쿨다운 (2026-08-05, Jino 승인 "A로 해줘")
# ──────────────────────────────────────────────
# 문제: `/channel-breakdown`·`/trend-by-channel`은 조회할 때마다 외부 API로 주문 7일 +
#   광고비 7일을 재동기화한다. 라이브 실측 — 이 두 엔드포인트만 **63.7초**, 같은 창의
#   `kpi` 0.11초 · `product-ranking` 0.12초. 화면을 열 때마다, 기간을 바꿀 때마다,
#   매출 축 토글을 누를 때마다 1분씩 멈춘다.
# 이미 `auto_sync_orders` 크론이 매일 06:00에 돌고 있으므로 조회 시 동기화는 그 위에 얹힌
#   **중복**이다. 다만 크론만 믿으면 06:00 이후 주문이 다음날까지 안 보이므로 없애지 않고,
#   같은 창을 짧은 시간 안에 다시 볼 때만 건너뛴다.
# ★키를 (용도, 창)으로 잡는다: 기간을 바꾸면 그 창은 새로 동기화된다(다른 데이터를 보는데
#   앞 창의 쿨다운에 막히면 "왜 안 바뀌지"가 된다).
# ★check-and-set을 락으로 묶는다: 이 엔드포인트는 스레드풀에서 돌아 브라우저 탭 두 개가
#   동시에 열리면 **둘 다** 63초 동기화를 시작한다. 락이 그걸 하나로 접는다.
# ★"한 번도 안 함"을 0.0 같은 기본값으로 두지 않는다(키 부재로 표현한다) — 단조시계와
#   0.0을 비교했다가 재부팅 후 1시간 동안 조용히 죽은 사고가 있었다(Wing 폴 데몬, 2026-08-04).
_SYNC_COOLDOWN_SEC = int(os.getenv("DASHBOARD_SYNC_COOLDOWN_SEC") or "300")
_sync_lock = threading.Lock()
_last_sync: dict[str, float] = {}


def _sync_due(key: str) -> bool:
    """쿨다운이 지났으면 True(그리고 즉시 '지금 했음'으로 표시). 아니면 False.

    ★실패해도 표시한다: 이 경로의 동기화는 best-effort(내부에서 예외를 삼키고 로그만 남긴다)라
      성공 여부를 알 수 없다. 실패가 쿨다운을 잡아도 크론이 별도로 돌고 다음 창에서 다시 시도되므로
      데이터가 영구히 멈추지 않는다 — 대신 조회가 매번 1분 멈추는 것을 막는 쪽을 택했다.
    """
    now = time.monotonic()
    with _sync_lock:
        prev = _last_sync.get(key)
        if prev is not None and (now - prev) < _SYNC_COOLDOWN_SEC:
            return False
        _last_sync[key] = now
        return True


def _reset_sync_cooldown() -> None:
    """테스트용 — 프로세스 전역 상태를 비운다."""
    with _sync_lock:
        _last_sync.clear()


def _sync_orders_recent(db: Session) -> None:
    """대시보드 조회 시 API 채널 주문 최근 7일치 최신화 (실패해도 조회는 계속).

    쿨다운(_sync_due) 안이면 건너뛴다 — 위 섹션 주석 참조.
    """
    from app.services.sync_service import sync_channel_orders
    sync_to = kst_today()
    sync_from = sync_to - timedelta(days=6)
    if not _sync_due(f"orders:{sync_from}:{sync_to}"):
        log.debug("주문 최신화 건너뜀(쿨다운 %ds)", _SYNC_COOLDOWN_SEC)
        return
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
    if not _sync_due(f"adcost:{date_from}:{date_to}"):
        log.debug("광고비 최신화 건너뜀(쿨다운 %ds)", _SYNC_COOLDOWN_SEC)
        return
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
                    "fixed_cost": Decimal("0"),
                    "unmapped_revenue": Decimal("0"),
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
            g["fixed_cost"] += Decimal(point.get("fixed_cost", "0"))
            g["unmapped_revenue"] += Decimal(point.get("unmapped_revenue", "0"))
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
                "fixed_cost": str(g["fixed_cost"]),
                "unmapped_revenue": str(g["unmapped_revenue"]),
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
    rocket_basis: str = Query(
        BASIS_SETTLEMENT,
        description="로켓배송 1P 매출 축 — 요약표와 **같은 값**을 써야 카드와 표가 안 갈라진다.",
    ),
    db: Session = Depends(get_db),
):
    """핵심 KPI (매출, 순이익, 이익률, 전기 대비 변화율)

    ★D-22(2026-08-19)부터 요약표와 **같은 경로**(`_channel_rows`)를 쓴다. 종전엔 카드만
      `calculate_daily_trend`(=`orders` 축)를 돌아 로켓1P를 통째로 빼놨고, 그래서 표에
      로켓 광고비를 반영하는 순간 카드와 표가 다른 순이익을 말하게 된다.
      부수 효과 둘(의도한 것):
        · 판매 축을 고르면 카드 총매출에도 로켓1P 매출이 들어온다(표와 일치).
        · RG 수수료 차감이 표와 같은 규칙(채널 code 매칭분만)으로 통일된다 — 종전 카드는
          매칭되는 채널이 없는 account_key까지 빼서 표보다 더 차감했다.
    """
    df, dt = _default_dates(date_from, date_to, "daily")
    basis = normalize_basis(rocket_basis)
    _sync_orders_recent(db)
    _sync_ad_costs_for_period(db, df, dt)
    ad_db = _resolve_ad_db()

    try:
        ch = rocket_1p_channel(db)
        rocket_id = ch.id if ch else None

        cur = _kpi_totals(db, _channel_rows(db, ad_db, df, dt, basis), rocket_id)
        rev, net = cur["revenue"], cur["net_profit"]
        rate = (net / cur["basis_revenue"] * 100) if cur["basis_revenue"] > 0 else Decimal("0")

        # 이전 기간 (동일 길이)
        period_days = (dt - df).days
        prev_to = df - timedelta(days=1)
        prev_from = prev_to - timedelta(days=period_days)
        prv = _kpi_totals(db, _channel_rows(db, ad_db, prev_from, prev_to, basis), rocket_id)
        prev_rev, prev_net = prv["revenue"], prv["net_profit"]

        rev_change = float((rev - prev_rev) / prev_rev * 100) if prev_rev > 0 else None
        profit_change = float((net - prev_net) / prev_net * 100) if prev_net > 0 else None

        return DashboardKPI(
            total_revenue=str(rev),
            net_profit=str(net),
            profit_rate=str(rate.quantize(Decimal("0.01"))),
            order_count=cur["order_count"],
            revenue_change_pct=round(rev_change, 2) if rev_change is not None else None,
            profit_change_pct=round(profit_change, 2) if profit_change is not None else None,
            net_scope=NET_SCOPE_PARTIAL if cur["floor_ad"] > 0 else NET_SCOPE_FULL,
            net_floor_ad=str(cur["floor_ad"]),
        )
    finally:
        if ad_db is not None:
            try:
                ad_db.close()
            except Exception:
                pass


# ──────────────────────────────────────────────
# 로켓배송 1P 병합 (트랙: 쿠팡 손익 정합)
# ──────────────────────────────────────────────
# 1P는 `orders`에 행이 없어(매입 구조) 기존 엔진이 만들 수 없다 → 평행 엔진의 산출을 여기서 얹는다.
# ★**더하지 않고 갈아끼운다.** 그 채널엔 이미 사람이 넣던 레거시 두 갈래가 흘러들고 있다 —
#   수기 매출(`manual_revenue`, 2026-05-01~05-18 18일치)과 수기 광고비 XLSX(`ad_costs`, 03-17~05-18).
#   그냥 append하면 그 구간이 **두 번 계상된다**. 평행 엔진이 이 채널의 유일한 원천이 되고,
#   레거시 광고비는 엔진 안에서 날짜 단위로 흡수한다(_ad_spend_by_day — 겹치는 하루는 자동 우선).
def _merge_rocket_1p_summary(db: Session, rows: list[dict], df, dt, basis: str) -> list[dict]:
    ch = rocket_1p_channel(db)
    if ch is None:
        return rows
    out = [r for r in rows if r.get("channel_id") != ch.id]
    row = compute_rocket_1p_summary_row(db, df, dt, basis)
    if row is not None:
        out.append(row)
    return out


def _merge_rocket_1p_trend(db: Session, points: list[dict], df, dt, basis: str) -> list[dict]:
    ch = rocket_1p_channel(db)
    if ch is None:
        return points
    out = [p for p in points if p.get("channel_id") != ch.id]
    out.extend(compute_rocket_1p_daily_points(db, df, dt, basis))
    return out


def _channel_rows(db: Session, ad_db, df, dt, basis: str) -> list[dict]:
    """채널 단위 요약 행 1벌 — RG 정산 수수료 차감 + 로켓1P 병합까지 끝난 상태.

    ★KPI 카드와 요약표가 **이 함수 하나만** 쓰게 한 이유(D-22, 2026-08-19): 종전엔 카드가
      `calculate_daily_trend`(=`orders` 축)를 따로 돌아서 로켓1P를 통째로 못 태웠다. 표만
      고치면 카드 569,635원 vs 표 -28,253원처럼 **한 화면 안에서 두 숫자가 갈라진다.**
      경로가 둘이면 언젠가 반드시 갈라지므로, 갈라질 수 없게 경로를 하나로 만든다.
    """
    rows = calculate_channel_summary(db, ad_db, df, dt)

    # RG 정산 수수료 차감 (B안: account_key=채널code로 매핑)
    rg_by_account = get_rg_total_by_account(db, df, dt)
    if rg_by_account:
        ch_map = {ch.id: ch for ch in db.query(Channel).all()}
        for row in rows:
            ch = ch_map.get(row["channel_id"])
            if ch and ch.code in rg_by_account and row["net_profit"] is not None:
                row["net_profit"] = str(Decimal(row["net_profit"]) - rg_by_account[ch.code])

    return _merge_rocket_1p_summary(db, rows, df, dt, basis)


def _kpi_totals(db: Session, rows: list[dict], rocket_ch_id: int | None) -> dict:
    """채널 행 → KPI 카드 숫자. `group_summary_by_company`의 「전체」 행과 **같은 규칙**이다.

    ★order_count만 로켓1P를 뺀다: 1P의 그 칸은 주문 건수가 아니라 **판매수량**이라(주문 개념이
      없는 매입 구조) 더하면 「주문 건수」 카드가 축에 따라 뜻이 바뀐다.
    """
    rev = net = basis_rev = floor_ad = Decimal("0")
    orders = 0
    for r in rows:
        row_rev = Decimal(r["revenue"])
        rev += row_rev
        # ★요약표와 **같은 함수**로 손익을 센다 — 규칙이 두 벌이면 카드와 표가 갈라진다.
        r_net, r_basis, r_floor = net_contribution(r, row_rev)
        if r_net is not None:
            net += r_net
            basis_rev += r_basis
        floor_ad += r_floor
        if r["channel_id"] != rocket_ch_id:
            orders += r["order_count"]
    return {
        "revenue": rev,
        "net_profit": net,
        "basis_revenue": basis_rev,
        "floor_ad": floor_ad,
        "order_count": orders,
    }


@router.get("/channel-breakdown", response_model=list[GroupedSummaryRow])
def channel_breakdown(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    rocket_basis: str = Query(
        BASIS_SETTLEMENT,
        description="로켓배송 1P 매출 축: settlement(계산서=회계 정본·기본) | sales(판매분석×납품단가). "
                    "두 축은 택일이며 합산하지 않는다(같은 물건을 납품·판매 두 번 세는 이중계상).",
    ),
    db: Session = Depends(get_db),
):
    """회사 > leaf 계층 그룹 요약 (전체/회사소계/leaf)"""
    df, dt = _default_dates(date_from, date_to, "daily")
    _sync_orders_recent(db)
    _sync_ad_costs_for_period(db, df, dt)
    ad_db = _resolve_ad_db()

    try:
        rows = _channel_rows(db, ad_db, df, dt, normalize_basis(rocket_basis))
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
    rocket_basis: str = Query(
        BASIS_SETTLEMENT,
        description="로켓배송 1P 매출 축: settlement(계산서=회계 정본·기본) | sales(판매분석×납품단가). "
                    "두 축은 택일이며 합산하지 않는다(같은 물건을 납품·판매 두 번 세는 이중계상).",
    ),
    db: Session = Depends(get_db),
):
    """회사 leaf 그룹 단위 일자별 매출/광고비/순이익 추이"""
    df, dt = _default_dates(date_from, date_to, "daily")
    _sync_orders_recent(db)
    _sync_ad_costs_for_period(db, df, dt)
    ad_db = _resolve_ad_db()

    try:
        pts = calculate_channel_daily_trend(db, ad_db, df, dt)
        pts = _merge_rocket_1p_trend(db, pts, df, dt, normalize_basis(rocket_basis))
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
