# 오하이테크(1P 로켓배송) 광고센터 일별 광고비 ingest Harness.
# 트랙: docs/tracks/active/track_coupang-ohitech-ad.md (D-8/D-9/D-10).
#
# 역할(단일 책임): Mac CDP 페처가 report/SALES에서 뽑은 일별 광고비를
#   coupang_ad_report(sell_type='Retail', vendor_id=오하이테크 A코드)로 snapshot upsert.
#   → rocket_intelligence._agg_rocket_ad가 sell_type='Retail'로 자동 합산 → 1P 순이익 차감(D-4).
#
# 머니룰(D-10): ad_spend = ALL_DELIVERED_AD_COST(전체, 비-PA 포함). 3P/RG net_profit과 동일하게
#   실제 지불 총액을 차감(intelligence.py:763-783 비-PA 추가차감과 일관). report/SALES는 impressions/
#   clicks/orders/sales_qty를 주지 않으므로 0. conversion_revenue = AD_ATTRIBUTED_SALES.
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models import CoupangAdReport

log = logging.getLogger("ohitech_ad_sync")

_SELL_TYPE = "Retail"  # 1P 로켓배송 (rocket_intelligence.ROCKET_AD_SELL_TYPE와 일치)


def _to_date(v) -> date | None:
    """'YYYY-MM-DD' 또는 date → date. 실패 시 None(해당 행 스킵)."""
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _to_dec(v) -> Decimal:
    """방어적 Decimal 변환(None/빈값/잘못된 값 → 0)."""
    try:
        return Decimal(str(v if v is not None else 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def ingest_ohitech_ad_cost(db: Session, vendor_id: str, days: list[dict]) -> dict:
    """오하이테크 일별 광고비 → coupang_ad_report(Retail) per-day upsert(멱등).

    days[i] = {"date": "YYYY-MM-DD", "ad_spend": <전체 ALL_DELIVERED, D-10>,
               "conv_sales": <AD_ATTRIBUTED_SALES, optional>}.
    (report_date, sell_type='Retail', vendor_id) 키로 교체 — report/SALES는 확정 과거일만
    반환하므로 윈도우 밖 날짜는 건드리지 않는다(전체 삭제 금지, 다른 윈도우 백필 보존).
    """
    upserted = 0
    skipped = 0
    seen_dates: list[date] = []
    for d in days:
        if not isinstance(d, dict):
            skipped += 1
            continue
        ad_date = _to_date(d.get("date"))
        if ad_date is None:
            skipped += 1
            continue
        ad_spend = _to_dec(d.get("ad_spend"))       # 전체(D-10) — _agg_rocket_ad 차감값
        conv = _to_dec(d.get("conv_sales"))
        existing = (
            db.query(CoupangAdReport)
            .filter(
                CoupangAdReport.report_date == ad_date,
                CoupangAdReport.sell_type == _SELL_TYPE,
                CoupangAdReport.vendor_id == vendor_id,
            )
            .first()
        )
        if existing:
            existing.ad_spend = ad_spend
            existing.conversion_revenue = conv
        else:
            db.add(
                CoupangAdReport(
                    report_date=ad_date,
                    sell_type=_SELL_TYPE,
                    vendor_id=vendor_id,
                    impressions=0,
                    clicks=0,
                    ad_spend=ad_spend,
                    orders=0,
                    sales_qty=0,
                    conversion_revenue=conv,
                )
            )
        upserted += 1
        seen_dates.append(ad_date)

    db.commit()
    result = {
        "vendor_id": vendor_id,
        "sell_type": _SELL_TYPE,
        "upserted": upserted,
        "skipped": skipped,
        "date_from": min(seen_dates).isoformat() if seen_dates else None,
        "date_to": max(seen_dates).isoformat() if seen_dates else None,
    }
    log.info("오하이테크 광고비 적재 %s Retail %d건 (%s~%s, skip=%d)",
             vendor_id, upserted, result["date_from"], result["date_to"], skipped)
    return result
