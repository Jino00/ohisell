# naver_ops.py — 네이버 스마트스토어 운영 패널 (매출/이익 집계)
# GET /api/naver/ops/sales-summary
# 이익 = 매출 − commission_amount(PG수수료) − 원가 − 광고비 − shipping_cost
# 광고비: ad_costs(source LIKE 'naver%') — product_id NULL이므로 요약 카드 총합만, 상품별 미배분
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AdCost, Channel, Order, ProductChannelMapping, ProductMaster
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED
from app.utils.kst import kst_today

router = APIRouter(prefix="/api/naver/ops", tags=["naver-ops"])

_NAVER_CHANNEL_ID = 6
_Q2 = Decimal("0.01")
_Z  = Decimal("0")


def _date_range(days: int) -> tuple[date, date]:
    today = kst_today()
    if days == 0:
        return today, today
    if days == 1:
        d = today - timedelta(days=1)
        return d, d
    return today - timedelta(days=days - 1), today


def _f(v) -> Decimal:
    if v is None:
        return _Z
    return v if isinstance(v, Decimal) else Decimal(str(v))


@router.get("/sales-summary")
def sales_summary(
    days: int = Query(default=7, ge=0, le=90),
    db: Session = Depends(get_db),
):
    """네이버 스마트스토어 매출 현황 — 기간별 집계.

    반환: summary(합계) + by_product(상품명별).
    광고비는 ad_costs(naver_sa:*) 기간 총합 — 상품별 배분 없음(product_id NULL).
    """
    dfrom, dto = _date_range(days)
    start = datetime.combine(dfrom, time.min)
    end   = datetime.combine(dto,   time.max)

    # ── 1. 주문 집계 (platform_product_id별) ─────────────────────────
    order_rows = (
        db.query(
            Order.platform_product_id,
            func.max(Order.platform_product_name),
            func.sum(Order.selling_price * Order.quantity),
            func.sum(Order.quantity),
            func.sum(Order.commission_amount),
            func.sum(Order.shipping_cost),
        )
        .filter(
            Order.channel_id == _NAVER_CHANNEL_ID,
            Order.platform_product_id != "",
            Order.platform_product_id.isnot(None),
            Order.status.notin_(tuple(REVENUE_EXCLUDED)),
            Order.order_date >= start,
            Order.order_date <= end,
        )
        .group_by(Order.platform_product_id)
        .all()
    )

    # ── 2. 광고비 (기간 총합, 상품별 배분 없음) ──────────────────────
    ad_ref_date: str | None = None
    ad_dfrom, ad_dto = dfrom, dto
    if days == 0:
        latest_ad = (
            db.query(func.max(AdCost.ad_date))
            .filter(AdCost.channel_id == _NAVER_CHANNEL_ID)
            .scalar()
        )
        if latest_ad:
            ad_dfrom = ad_dto = latest_ad
            ad_ref_date = str(latest_ad)

    total_ad_spend = _f(
        db.query(func.sum(AdCost.ad_spend))
        .filter(
            AdCost.channel_id == _NAVER_CHANNEL_ID,
            AdCost.ad_date >= ad_dfrom,
            AdCost.ad_date <= ad_dto,
        )
        .scalar()
    )

    # ── 3. 원가 조회 (product_channel_mapping → product_master) ───────
    all_pids = {str(r[0]) for r in order_rows if r[0]}
    cost_rows = (
        db.query(ProductChannelMapping.channel_product_id, ProductMaster.cost_price, ProductMaster.id)
        .join(ProductMaster, ProductChannelMapping.product_id == ProductMaster.id)
        .filter(
            ProductChannelMapping.channel_id == _NAVER_CHANNEL_ID,
            ProductChannelMapping.is_active.is_(True),
            ProductChannelMapping.channel_product_id.in_(list(all_pids)),
        )
        .all()
    ) if all_pids else []
    cost_candidates: dict[str, list] = {}
    for cpid, cp, pid in cost_rows:
        cost_candidates.setdefault(str(cpid), []).append((cp, pid))
    cost_map: dict[str, Decimal] = {}
    for pid, cands in cost_candidates.items():
        costed = [(cp, p) for cp, p in cands if cp and cp > 0]
        chosen = min(costed or cands, key=lambda x: x[1])[0]
        if chosen:
            cost_map[pid] = _f(chosen)

    # ── 4. 상품별 집계 ────────────────────────────────────────────────
    by_product = []
    total_rev = total_fee = total_cost = total_ship = _Z

    for pid, pname, rev, qty, commission, shipping in order_rows:
        rev       = _f(rev)
        qty_      = int(qty or 0)
        fee       = _f(commission)
        ship      = _f(shipping)
        unit_cost = cost_map.get(str(pid), _Z)
        cost      = unit_cost * qty_
        profit    = rev - fee - cost - ship

        total_rev  += rev
        total_fee  += fee
        total_cost += cost
        total_ship += ship

        by_product.append({
            "product_name":  pname or str(pid),
            "platform_id":   str(pid),
            "revenue":       str(rev.quantize(_Q2)),
            "fee":           str(fee.quantize(_Q2)),
            "cost":          str(cost.quantize(_Q2)),
            "shipping":      str(ship.quantize(_Q2)),
            "profit":        str(profit.quantize(_Q2)),
            "profit_rate":   str((profit / rev * 100).quantize(_Q2)) if rev else None,
        })

    by_product.sort(key=lambda x: -Decimal(x["revenue"]))

    # ── 5. 요약 (광고비 차감해서 전체 이익 계산) ─────────────────────
    total_profit = total_rev - total_fee - total_cost - total_ad_spend - total_ship
    profit_rate  = (total_profit / total_rev * 100).quantize(_Q2) if total_rev else None

    return {
        "period":       {"from": str(dfrom), "to": str(dto)},
        "ad_ref_date":  ad_ref_date,
        "summary": {
            "revenue":      str(total_rev.quantize(_Q2)),
            "fee":          str(total_fee.quantize(_Q2)),
            "cost":         str(total_cost.quantize(_Q2)),
            "ad_spend":     str(total_ad_spend.quantize(_Q2)),
            "shipping":     str(total_ship.quantize(_Q2)),
            "profit":       str(total_profit.quantize(_Q2)),
            "profit_rate":  str(profit_rate) if profit_rate is not None else None,
        },
        "by_product": by_product,
    }
