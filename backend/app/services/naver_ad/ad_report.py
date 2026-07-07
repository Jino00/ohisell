# ad_report.py — ad_report_harness (SA 조합·3열 ROAS 계산 = 정보 유통 허브, 원칙18-6)
# 역할(Harness): metrics_aggregator(광고 집계) + actual_revenue(실주문) + hourly_pacing(시간대)을
#   조합해 광고 리포트 응답을 구성. 3열 ROAS(D-NAO-7)는 이 허브가 SA 출력을 받아 계산.
#   현재기간 + 비교기간(옵션)을 각각 집계해 KPI 델타 산출. SA간 직접호출 없음(허브 경유).
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.services.naver_ad import actual_revenue, hourly_pacing, metrics_aggregator

_Q4 = Decimal("0.0001")
_Q2 = Decimal("0.01")

# 비교기간 델타를 계산할 KPI 목록
_DELTA_KEYS = ("cost", "imp", "clk", "conv_cnt", "conv_amt", "roas_naver")


def _ratio(num: int, den: int) -> float | None:
    if not den:
        return None
    return float((Decimal(num) / Decimal(den)).quantize(_Q4, ROUND_HALF_UP))


def _roas_3col(kpis: dict, actual: dict) -> dict:
    """3열 ROAS(D-NAO-7) — 계정 총계 전용(주문 캠페인 미귀속).

    ①네이버(직+간접) ②직접전환만 ③실주문 대조(order_date 기준, 귀속아닌 현실 대조).
    공통 분모 = 광고비(cost). 네이버 convAmt 과대 여부를 실주문으로 검증하는 목적.
    """
    cost = kpis["cost"]
    naver_rev = kpis["conv_amt"]
    direct_rev = kpis["conv_direct_amt"]
    actual_rev = actual["revenue"]
    return {
        "cost": cost,
        "naver": {"revenue": naver_rev, "roas": _ratio(naver_rev, cost)},
        "direct": {"revenue": direct_rev, "roas": _ratio(direct_rev, cost)},
        "actual_order": {
            "revenue": actual_rev,
            "roas": _ratio(actual_rev, cost),
            "order_count": actual["order_count"],
            "note": "주문은 캠페인 미귀속 — 계정 총계 현실 대조(정확 귀속 아님)",
        },
    }


def _delta_pct(cur, prev) -> float | None:
    """(cur-prev)/prev × 100. prev가 0/None이면 None."""
    if cur is None or prev is None or not prev:
        return None
    return float((Decimal(str(cur)) - Decimal(str(prev))) / Decimal(str(prev)) * 100)


def build_report(
    db: Session,
    date_from: date,
    date_to: date,
    *,
    grain: str = "date",
    compare_from: date | None = None,
    compare_to: date | None = None,
    campaign_filter: str | None = None,
) -> dict:
    """네이버 광고 리포트 조립.

    반환: {period, grain, kpis, roas_3col, trend, rows, hourly_meta?, compare?}.
      kpis=계정 총계(8칸+). trend=일별 시계열(듀얼차트, grain 무관 항상 제공).
      rows=선택 grain 드릴다운(hour는 시간대 페이싱). compare=비교기간 KPI+델타(옵션).
    """
    # ── 현재 기간: 일별 집계(KPI 총계 + 시계열) ──
    date_agg = metrics_aggregator.aggregate(
        db, date_from, date_to, grain="date", campaign_filter=campaign_filter
    )
    kpis = date_agg["totals"]
    trend = date_agg["rows"]

    actual = actual_revenue.naver_order_revenue(db, date_from, date_to)
    roas_3col = _roas_3col(kpis, actual)

    # ── 선택 grain 드릴다운 rows ──
    hourly_meta = None
    if grain == "date":
        rows = trend
    elif grain == "hour":
        hp = hourly_pacing.hourly_rows(db, on_or_before=date_to, campaign_filter=campaign_filter)
        rows = hp["rows"]
        hourly_meta = {"ad_date": hp["ad_date"], "total_cost": hp["total_cost"], "clamped": hp["clamped"]}
    else:
        rows = metrics_aggregator.aggregate(
            db, date_from, date_to, grain=grain, campaign_filter=campaign_filter
        )["rows"]

    result = {
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "grain": grain,
        "kpis": kpis,
        "roas_3col": roas_3col,
        "trend": trend,
        "rows": rows,
    }
    if hourly_meta is not None:
        result["hourly_meta"] = hourly_meta

    # ── 비교 기간(옵션) ──
    if compare_from and compare_to:
        cmp_agg = metrics_aggregator.aggregate(
            db, compare_from, compare_to, grain="date", campaign_filter=campaign_filter
        )
        cmp_kpis = cmp_agg["totals"]
        cmp_actual = actual_revenue.naver_order_revenue(db, compare_from, compare_to)
        deltas = {k: _delta_pct(kpis.get(k), cmp_kpis.get(k)) for k in _DELTA_KEYS}
        result["compare"] = {
            "period": {"from": compare_from.isoformat(), "to": compare_to.isoformat()},
            "kpis": cmp_kpis,
            "roas_3col": _roas_3col(cmp_kpis, cmp_actual),
            "deltas_pct": deltas,
        }

    return result
