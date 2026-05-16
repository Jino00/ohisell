# profit_calculator.py — 핵심 이익률 계산 엔진
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, func, text
from sqlalchemy.orm import Session

from app.models import Channel, Order, ProductChannelMapping, ProductMaster
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED

log = logging.getLogger(__name__)

ZERO = Decimal("0")


def _line_commission(ch: Channel | None, o: Order, revenue: Decimal) -> Decimal:
    """라인 수수료. cafe24는 동기화 시 산출된 commission_amount 사용, 그 외는 채널 정률."""
    if ch and ch.code == "CAFE24":
        return o.commission_amount if o.commission_amount is not None else ZERO
    rate = ch.commission_rate if ch else ZERO
    return revenue * rate / Decimal("100")


# ──────────────────────────────────────────────
# 내부 헬퍼
# ──────────────────────────────────────────────


def _get_option_id_map(db: Session) -> dict[str, int]:
    """product_channel_mapping에서 {channel_product_id: product_id} 딕셔너리"""
    rows = db.query(
        ProductChannelMapping.channel_product_id,
        ProductChannelMapping.product_id,
    ).filter(ProductChannelMapping.is_active.is_(True)).all()
    return {r[0]: r[1] for r in rows}


def _get_ad_spend_lookup(
    ad_db, date_from: date, date_to: date, option_ids: list[str]
) -> dict[tuple[str, str], Decimal]:
    """ad_data.db에서 option_id별/일별 광고비 조회 → {(option_id, date_str): spend}"""
    if ad_db is None or not option_ids:
        return {}

    try:
        # SQLite는 IN 절에 파라미터 바인딩이 까다로우므로 청크 처리
        lookup: dict[tuple[str, str], Decimal] = {}
        chunk_size = 500
        for i in range(0, len(option_ids), chunk_size):
            chunk = option_ids[i : i + chunk_size]
            placeholders = ", ".join(f":oid_{j}" for j in range(len(chunk)))
            params: dict = {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
            }
            for j, oid in enumerate(chunk):
                params[f"oid_{j}"] = oid

            sql = f"""
                SELECT option_id, date, SUM(ad_spend) as spend
                FROM daily_ad_data
                WHERE date >= :date_from AND date <= :date_to
                  AND option_id IN ({placeholders})
                GROUP BY option_id, date
            """
            result = ad_db.execute(text(sql), params)
            for row in result:
                m = row._mapping
                lookup[(str(m["option_id"]), str(m["date"]))] = Decimal(str(m["spend"]))

        return lookup
    except Exception as e:
        log.error("ad_data.db 광고비 조회 에러: %s", e)
        return {}


def _build_channel_maps(db: Session) -> tuple[dict[int, Channel], dict[int, ProductMaster]]:
    """채널/상품 맵 생성"""
    channels = {c.id: c for c in db.query(Channel).all()}
    products = {p.id: p for p in db.query(ProductMaster).all()}
    return channels, products


def _calc_line(
    revenue: Decimal,
    cost: Decimal,
    commission_rate: Decimal,
    shipping: Decimal,
    ad_spend: Decimal,
) -> dict:
    """단일 라인의 수수료/VAT/순이익 계산"""
    commission = revenue * commission_rate / Decimal("100")
    vat = revenue * Decimal("10") / Decimal("110")
    net_profit = revenue - cost - commission - shipping - ad_spend - vat
    return {
        "commission": commission,
        "vat": vat,
        "net_profit": net_profit,
    }


# ──────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────


def calculate_daily_trend(
    db: Session,
    ad_db,
    channel_id: int | None,
    date_from: date,
    date_to: date,
) -> list[dict]:
    """일별 매출/비용/순이익 추이"""
    query = db.query(Order).filter(
        and_(
            Order.order_date >= date_from.isoformat(),
            Order.order_date < (date_to.isoformat() + "T23:59:59"),
        )
    )
    if channel_id:
        query = query.filter(Order.channel_id == channel_id)

    orders = query.all()
    if not orders:
        return []

    channel_map, product_map = _build_channel_maps(db)
    option_map = _get_option_id_map(db)

    # 모든 option_id 수집하여 광고비 일괄 조회
    all_option_ids = list(set(o.platform_product_id for o in orders if o.platform_product_id))
    ad_lookup = _get_ad_spend_lookup(ad_db, date_from, date_to, all_option_ids)

    # 날짜별 집계
    daily: dict[str, dict] = {}
    for o in orders:
        ch = channel_map.get(o.channel_id)
        if ch and ch.channel_type == "consignment":
            continue
        if o.status in REVENUE_EXCLUDED:  # 취소/반품/입금전 매출 제외
            continue

        d = str(o.order_date.date()) if hasattr(o.order_date, "date") else str(o.order_date)[:10]
        if d not in daily:
            daily[d] = {
                "revenue": ZERO,
                "cost": ZERO,
                "commission": ZERO,
                "shipping": ZERO,
                "ad_spend": ZERO,
                "vat": ZERO,
                "order_count": 0,
            }

        bucket = daily[d]
        revenue = o.selling_price * o.quantity
        bucket["revenue"] += revenue
        bucket["order_count"] += 1

        # 원가
        if o.product_id and o.product_id in product_map:
            bucket["cost"] += product_map[o.product_id].cost_price * o.quantity

        # 수수료 (cafe24=산출액, 그 외=정률)
        bucket["commission"] += _line_commission(ch, o, revenue)

        # 배송비
        if o.shipping_cost:
            bucket["shipping"] += o.shipping_cost

        # VAT
        bucket["vat"] += revenue * Decimal("10") / Decimal("110")

    # 광고비 합산 (option_id 기반 → 날짜별 합산)
    for (oid, d_str), spend in ad_lookup.items():
        if d_str in daily:
            daily[d_str]["ad_spend"] += spend

    # 정렬 후 반환
    result = []
    for d in sorted(daily.keys()):
        b = daily[d]
        net = b["revenue"] - b["cost"] - b["commission"] - b["shipping"] - b["ad_spend"] - b["vat"]
        result.append({
            "date": d,
            "revenue": str(b["revenue"]),
            "cost": str(b["cost"]),
            "commission": str(b["commission"]),
            "ad_spend": str(b["ad_spend"]),
            "shipping": str(b["shipping"]),
            "vat": str(b["vat"]),
            "net_profit": str(net),
            "order_count": b["order_count"],
        })

    return result


def calculate_channel_summary(
    db: Session, ad_db, date_from: date, date_to: date
) -> list[dict]:
    """채널별 매출/이익 요약"""
    query = db.query(Order).filter(
        and_(
            Order.order_date >= date_from.isoformat(),
            Order.order_date < (date_to.isoformat() + "T23:59:59"),
        )
    )
    orders = query.all()
    if not orders:
        return []

    channel_map, product_map = _build_channel_maps(db)
    option_map = _get_option_id_map(db)
    all_option_ids = list(set(o.platform_product_id for o in orders if o.platform_product_id))
    ad_lookup = _get_ad_spend_lookup(ad_db, date_from, date_to, all_option_ids)

    # 채널별 집계
    by_channel: dict[int, dict] = {}
    for o in orders:
        ch = channel_map.get(o.channel_id)
        if ch and ch.channel_type == "consignment":
            continue
        if o.status in REVENUE_EXCLUDED:  # 취소/반품/입금전 매출 제외
            continue

        cid = o.channel_id
        if cid not in by_channel:
            by_channel[cid] = {
                "revenue": ZERO, "cost": ZERO, "commission": ZERO,
                "ad_spend": ZERO, "shipping": ZERO, "order_count": 0,
            }

        b = by_channel[cid]
        revenue = o.selling_price * o.quantity
        b["revenue"] += revenue
        b["order_count"] += 1

        if o.product_id and o.product_id in product_map:
            b["cost"] += product_map[o.product_id].cost_price * o.quantity

        b["commission"] += _line_commission(ch, o, revenue)
        # 배송비는 cafe24만 channel_summary에 반영 (기존 비-cafe24는 미포함 → 회귀 방지)
        if ch and ch.code == "CAFE24" and o.shipping_cost:
            b["shipping"] += o.shipping_cost

    # 광고비를 option_id → 주문의 채널로 직접 매핑 (bleeding 방지)
    # 1) option_id별 광고비 합산
    ad_by_option: dict[str, Decimal] = {}
    for (oid, _), spend in ad_lookup.items():
        ad_by_option[oid] = ad_by_option.get(oid, ZERO) + spend

    # 2) option_id → channel_id 매핑 (주문 데이터 기반)
    option_to_channel: dict[str, int] = {}
    for o in orders:
        if o.platform_product_id and o.platform_product_id not in option_to_channel:
            option_to_channel[o.platform_product_id] = o.channel_id

    # 3) 광고비를 해당 채널에 직접 할당
    for oid, spend in ad_by_option.items():
        cid = option_to_channel.get(oid)
        if cid and cid in by_channel:
            by_channel[cid]["ad_spend"] += spend

    result = []
    for cid, b in by_channel.items():
        ch = channel_map.get(cid)
        net = b["revenue"] - b["cost"] - b["commission"] - b["ad_spend"] - b["shipping"]
        rate_pct = (net / b["revenue"] * 100) if b["revenue"] > 0 else ZERO

        result.append({
            "channel_id": cid,
            "channel_name": ch.name if ch else "",
            "revenue": str(b["revenue"]),
            "cost": str(b["cost"]),
            "commission": str(b["commission"]),
            "ad_spend": str(b["ad_spend"]),
            "shipping": str(b["shipping"]),
            "net_profit": str(net),
            "profit_rate": str(rate_pct.quantize(Decimal("0.01")) if isinstance(rate_pct, Decimal) else "0.00"),
            "order_count": b["order_count"],
        })

    result.sort(key=lambda x: Decimal(x["revenue"]), reverse=True)
    return result


def calculate_product_profit(
    db: Session,
    ad_db,
    date_from: date,
    date_to: date,
    channel_id: int | None = None,
    sort_by: str = "revenue",
    limit: int = 20,
) -> list[dict]:
    """상품별 이익률 Top N"""
    query = db.query(Order).filter(
        and_(
            Order.order_date >= date_from.isoformat(),
            Order.order_date < (date_to.isoformat() + "T23:59:59"),
            Order.product_id.isnot(None),
        )
    )
    if channel_id:
        query = query.filter(Order.channel_id == channel_id)

    orders = query.all()
    if not orders:
        return []

    channel_map, product_map = _build_channel_maps(db)
    all_option_ids = list(set(o.platform_product_id for o in orders if o.platform_product_id))
    ad_lookup = _get_ad_spend_lookup(ad_db, date_from, date_to, all_option_ids)

    # option_id별 광고비 합산
    ad_by_option: dict[str, Decimal] = {}
    for (oid, _), spend in ad_lookup.items():
        ad_by_option[oid] = ad_by_option.get(oid, ZERO) + spend

    # 상품별 집계
    by_product: dict[int, dict] = {}
    for o in orders:
        ch = channel_map.get(o.channel_id)
        if ch and ch.channel_type == "consignment":
            continue
        if o.status in REVENUE_EXCLUDED:  # 취소/반품/입금전 매출 제외
            continue

        pid = o.product_id
        if pid not in by_product:
            by_product[pid] = {
                "revenue": ZERO, "cost": ZERO, "commission": ZERO,
                "ad_spend": ZERO, "shipping": ZERO, "quantity": 0,
            }

        b = by_product[pid]
        revenue = o.selling_price * o.quantity
        b["revenue"] += revenue
        b["quantity"] += o.quantity

        if pid in product_map:
            b["cost"] += product_map[pid].cost_price * o.quantity

        b["commission"] += _line_commission(ch, o, revenue)

        if o.shipping_cost:
            b["shipping"] += o.shipping_cost

        # 광고비는 아래에서 option_id → product_id 매핑으로 일괄 할당

    # option_id → product_id 매핑으로 광고비 직접 할당 (중복 방지)
    option_to_product: dict[str, int] = {}
    for o in orders:
        if o.platform_product_id and o.product_id and o.platform_product_id not in option_to_product:
            option_to_product[o.platform_product_id] = o.product_id

    for oid, spend in ad_by_option.items():
        pid = option_to_product.get(oid)
        if pid and pid in by_product:
            by_product[pid]["ad_spend"] += spend

    result = []
    for pid, b in by_product.items():
        p = product_map.get(pid)
        if not p:
            continue
        net = b["revenue"] - b["cost"] - b["commission"] - b["ad_spend"] - b["shipping"]
        rate_pct = (net / b["revenue"] * 100) if b["revenue"] > 0 else ZERO

        result.append({
            "product_id": pid,
            "product_name": p.product_name,
            "internal_sku": p.internal_sku,
            "revenue": str(b["revenue"]),
            "cost": str(b["cost"]),
            "commission": str(b["commission"]),
            "ad_spend": str(b["ad_spend"]),
            "shipping": str(b["shipping"]),
            "net_profit": str(net),
            "profit_rate": str(rate_pct.quantize(Decimal("0.01")) if isinstance(rate_pct, Decimal) else "0.00"),
            "quantity": b["quantity"],
        })

    # 정렬
    sort_key = sort_by if sort_by in ("revenue", "net_profit", "profit_rate") else "revenue"
    result.sort(key=lambda x: Decimal(x[sort_key]), reverse=True)
    return result[:limit]
