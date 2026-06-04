# routers/coupang_report.py — 쿠팡 광고 리포트 XLSX 업로드 + 기간 조회 API
from __future__ import annotations

import io
import logging
import re
from app.utils.kst import kst_now, kst_today
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CoupangAdReport

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ads/coupang", tags=["coupang-ad-report"])

# pa_daily_keyword XLSX 컬럼 인덱스
_COL_DATE = 0
_COL_SELL_TYPE = 2
_COL_IMPRESSIONS = 13
_COL_CLICKS = 14
_COL_AD_SPEND = 15
_COL_ORDERS = 17      # 총 주문수(1일)
_COL_SALES_QTY = 20   # 총 판매수량(1일)
_COL_CONV_REV = 23    # 총 전환매출액(1일)

_SELL_TYPE_LABEL = {"3P": "윙", "2P": "로켓그로스", "Retail": "로켓배송"}


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except Exception:
        return Decimal("0")


@router.post("/upload")
async def upload_coupang_ad_report(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """쿠팡 pa_daily_keyword XLSX 업로드 → coupang_ad_report 저장."""
    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="xlsx 파일만 업로드 가능합니다.")

    m = re.match(r"(A\d+)_", file.filename or "")
    if not m:
        raise HTTPException(status_code=400, detail="파일명에서 vendor_id를 찾을 수 없습니다. 형식: {vendor_id}_pa_daily_keyword_...")
    vendor_id = m.group(1)

    try:
        import openpyxl
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl 패키지가 설치되지 않았습니다.")

    content = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"XLSX 파일을 열 수 없습니다: {e}")

    ws = wb.active
    # (date, sell_type) → 집계 dict
    agg: dict[tuple[date, str], dict] = {}
    skipped = 0

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or len(row) <= _COL_CONV_REV:
            skipped += 1
            continue

        date_raw = row[_COL_DATE]
        sell_type = str(row[_COL_SELL_TYPE] or "").strip()
        if not date_raw or sell_type not in _SELL_TYPE_LABEL:
            skipped += 1
            continue

        try:
            date_str = str(int(date_raw))
            ad_date = date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
        except Exception:
            skipped += 1
            continue

        key = (ad_date, sell_type)
        if key not in agg:
            agg[key] = {"impressions": 0, "clicks": 0, "ad_spend": Decimal("0"),
                        "orders": 0, "sales_qty": 0, "conversion_revenue": Decimal("0")}

        g = agg[key]
        g["impressions"] += _int(row[_COL_IMPRESSIONS])
        g["clicks"] += _int(row[_COL_CLICKS])
        g["ad_spend"] += _dec(row[_COL_AD_SPEND])
        g["orders"] += _int(row[_COL_ORDERS])
        g["sales_qty"] += _int(row[_COL_SALES_QTY])
        g["conversion_revenue"] += _dec(row[_COL_CONV_REV])

    if not agg:
        raise HTTPException(status_code=422, detail="저장할 광고 데이터가 없습니다. 파일 형식을 확인하세요.")

    upserted = 0
    for (ad_date, sell_type), g in agg.items():
        existing = db.query(CoupangAdReport).filter(
            CoupangAdReport.report_date == ad_date,
            CoupangAdReport.sell_type == sell_type,
            CoupangAdReport.vendor_id == vendor_id,
        ).first()
        if existing:
            existing.impressions = g["impressions"]
            existing.clicks = g["clicks"]
            existing.ad_spend = g["ad_spend"]
            existing.orders = g["orders"]
            existing.sales_qty = g["sales_qty"]
            existing.conversion_revenue = g["conversion_revenue"]
        else:
            db.add(CoupangAdReport(
                report_date=ad_date,
                sell_type=sell_type,
                vendor_id=vendor_id,
                **g,
            ))
        upserted += 1

    db.commit()
    date_from = min(k[0] for k in agg)
    date_to = max(k[0] for k in agg)
    log.info("쿠팡 광고 리포트 %s 적재 (%s~%s) %d건", vendor_id, date_from, date_to, upserted)
    return {
        "vendor_id": vendor_id,
        "upserted": upserted,
        "skipped": skipped,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
    }


@router.get("/report")
def get_coupang_ad_report(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """기간별 쿠팡 광고 성과 요약 (전체 합계 + 판매방식별 분류)."""
    if date_to is None:
        date_to = kst_today()
    if date_from is None:
        date_from = date_to.replace(day=1)  # 이번 달 1일

    rows = db.execute(text("""
        SELECT sell_type,
               SUM(impressions)         AS impressions,
               SUM(clicks)              AS clicks,
               SUM(ad_spend)            AS ad_spend,
               SUM(orders)              AS orders,
               SUM(sales_qty)           AS sales_qty,
               SUM(conversion_revenue)  AS conversion_revenue
        FROM coupang_ad_report
        WHERE report_date >= :df AND report_date <= :dt
        GROUP BY sell_type
        ORDER BY sell_type
    """), {"df": date_from.isoformat(), "dt": date_to.isoformat()}).fetchall()

    items = []
    total = {"sell_type": "전체", "impressions": 0, "clicks": 0,
             "ad_spend": Decimal("0"), "orders": 0, "sales_qty": 0,
             "conversion_revenue": Decimal("0")}

    for r in rows:
        imp = r[1] or 0
        clk = r[2] or 0
        spend = Decimal(str(r[3] or 0))
        orders = r[4] or 0
        qty = r[5] or 0
        rev = Decimal(str(r[6] or 0))

        ctr = round(clk / imp * 100, 2) if imp > 0 else 0.0
        cvr = round(orders / clk * 100, 2) if clk > 0 else 0.0
        roas = round(float(rev / spend * 100), 1) if spend > 0 else 0.0

        items.append({
            "sell_type": _SELL_TYPE_LABEL.get(r[0], r[0]),
            "impressions": imp,
            "clicks": clk,
            "ad_spend": int(spend),
            "orders": orders,
            "sales_qty": qty,
            "conversion_revenue": int(rev),
            "ctr": ctr,
            "cvr": cvr,
            "roas": roas,
        })

        total["impressions"] += imp
        total["clicks"] += clk
        total["ad_spend"] += spend
        total["orders"] += orders
        total["sales_qty"] += qty
        total["conversion_revenue"] += rev

    t_imp = total["impressions"]
    t_clk = total["clicks"]
    t_spend = total["ad_spend"]
    t_rev = total["conversion_revenue"]
    total.update({
        "ad_spend": int(t_spend),
        "conversion_revenue": int(t_rev),
        "ctr": round(t_clk / t_imp * 100, 2) if t_imp > 0 else 0.0,
        "cvr": round(total["orders"] / t_clk * 100, 2) if t_clk > 0 else 0.0,
        "roas": round(float(t_rev / t_spend * 100), 1) if t_spend > 0 else 0.0,
    })

    return {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
            "total": total, "items": items}
