# intelligence.py — 쿠팡 종합 조망(Command Center) 결합 엔진 (트랙 P7, D-2/D-3)
# 목적: 옵션ID(vendor_item_id, 결합축 D-8)를 단일 키로 5개 소스를 각자 집계한 뒤 병합해
#       ① 회계(진짜 순이익) ② 광고(사실 정리) ③ 상품(판매현황) 3축을 파생한다.
# ★설계 핵심(스키마 검증으로 확정):
#   ① Fan-out 방지: 거대 단일 JOIN(N×M×K×J 곱) 금지 — 각 소스를 vendor_item_id로 따로
#      GROUP BY 집계한 뒤 dict merge (profit_calculator 패턴과 동일).
#   ② 날짜축이 소스마다 다름: 주문 order_date·광고 report_date·수수료 recognition_date·
#      반품 requested_at — 각자 자기 날짜로 기간 필터 후 옵션ID로 병합.
#   ③ orders는 전 채널 공용: platform='coupang' 채널만 필터(네이버·cafe24 제외).
# D-3: 시스템은 사실/지표 정리만 — 전략 추천 없음. 해석은 Jino 몫.
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    Channel,
    CoupangAdOptionDaily,
    CoupangProductItem,
    CoupangReturnItem,
    CoupangRevenueFee,
    CoupangRgSettlementFee,
    Order,
    ProductChannelMapping,
    ProductMaster,
)
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED

log = logging.getLogger(__name__)

_Z = Decimal("0")
_Q4 = Decimal("0.0001")  # 비율(ROAS·CTR·반품률) 표시 자리수 — JSON 정리


def _ratio(num: Decimal, den) -> "Decimal | None":
    """비율 = num/den. den 0이면 None. 결과는 4자리로 quantize(긴 소수 방지)."""
    if not den:
        return None
    return (num / Decimal(den)).quantize(_Q4)


def _f(v) -> Decimal:
    """None/숫자 → Decimal. 집계 결과(func.sum)가 None(행 없음)이면 0."""
    if v is None:
        return _Z
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


# ──────────────────────────────────────────────
# 소스별 기간 집계 (각 SA는 vendor_item_id 키 dict 반환 — 단독 GROUP BY, fan-out 없음)
# ──────────────────────────────────────────────
def _agg_orders(db: Session, dfrom: date, dto: date) -> dict[str, dict]:
    """쿠팡 채널 주문을 옵션ID별 집계. 매출=Σ(selling_price×quantity), 단가=매출/수량.

    codex[P2]: 취소/반품/입금전(REVENUE_EXCLUDED) 주문은 매출에서 제외 — 기존 profit_calculator와
    동일 기준. 안 거르면 ① 매출 부풀림 ② coupang_return_item에서 또 차감 = 이중차감.
    """
    start = datetime.combine(dfrom, time.min)
    end = datetime.combine(dto, time.max)
    rows = (
        db.query(
            Order.platform_product_id,
            func.sum(Order.selling_price * Order.quantity),
            func.sum(Order.quantity),
            func.count(Order.id),
            func.max(Order.platform_product_name),
        )
        .join(Channel, Order.channel_id == Channel.id)
        .filter(
            Channel.platform == "coupang",
            Order.platform_product_id != "",
            Order.status.notin_(tuple(REVENUE_EXCLUDED)),
            Order.order_date >= start,
            Order.order_date <= end,
        )
        .group_by(Order.platform_product_id)
        .all()
    )
    out: dict[str, dict] = {}
    for vid, revenue, qty, cnt, name in rows:
        rev = _f(revenue)
        q = int(qty or 0)
        out[str(vid)] = {
            "revenue": rev,
            "qty": q,
            "order_count": int(cnt or 0),
            "unit_price": (rev / q) if q else _Z,  # 반품 차감액 추정용 평균 단가
            "name": name,
        }
    return out


def _agg_ads(db: Session, dfrom: date, dto: date) -> dict[str, dict]:
    """광고 옵션 일별을 옵션ID별 집계. D-9: 비용·노출·클릭은 ad_option_id 귀속,
    매출·주문은 conv_option_id 귀속(간접전환 대비 분리). 같은 옵션이면 한 행에 합쳐짐."""
    out: dict[str, dict] = {}

    def _row(vid: str) -> dict:
        return out.setdefault(
            str(vid),
            {"spend": _Z, "impressions": 0, "clicks": 0,
             "conv_revenue": _Z, "ad_orders": 0, "ad_qty": 0},
        )

    cost_rows = (
        db.query(
            CoupangAdOptionDaily.ad_option_id,
            func.sum(CoupangAdOptionDaily.ad_spend),
            func.sum(CoupangAdOptionDaily.impressions),
            func.sum(CoupangAdOptionDaily.clicks),
        )
        .filter(CoupangAdOptionDaily.report_date >= dfrom,
                CoupangAdOptionDaily.report_date <= dto)
        .group_by(CoupangAdOptionDaily.ad_option_id)
        .all()
    )
    for vid, spend, imp, clk in cost_rows:
        b = _row(vid)
        b["spend"] += _f(spend)
        b["impressions"] += int(imp or 0)
        b["clicks"] += int(clk or 0)

    conv_rows = (
        db.query(
            CoupangAdOptionDaily.conv_option_id,
            func.sum(CoupangAdOptionDaily.conversion_revenue),
            func.sum(CoupangAdOptionDaily.orders),
            func.sum(CoupangAdOptionDaily.sales_qty),
        )
        .filter(CoupangAdOptionDaily.report_date >= dfrom,
                CoupangAdOptionDaily.report_date <= dto)
        .group_by(CoupangAdOptionDaily.conv_option_id)
        .all()
    )
    for vid, conv_rev, ords, qty in conv_rows:
        b = _row(vid)
        b["conv_revenue"] += _f(conv_rev)
        b["ad_orders"] += int(ords or 0)
        b["ad_qty"] += int(qty or 0)
    return out


def _agg_returns(db: Session, dfrom: date, dto: date) -> dict[str, dict]:
    """반품/취소를 옵션ID별 집계. withdrawn=False(철회 제외)만. 사실=반품 수량·건수."""
    start = datetime.combine(dfrom, time.min)
    end = datetime.combine(dto, time.max)
    rows = (
        db.query(
            CoupangReturnItem.vendor_item_id,
            func.sum(CoupangReturnItem.cancel_count),
            func.count(CoupangReturnItem.id),
            func.max(CoupangReturnItem.vendor_item_name),
        )
        .filter(
            CoupangReturnItem.withdrawn.is_(False),
            CoupangReturnItem.requested_at >= start,
            CoupangReturnItem.requested_at <= end,
        )
        .group_by(CoupangReturnItem.vendor_item_id)
        .all()
    )
    return {
        str(vid): {
            "return_qty": int(cancel or 0),
            "receipt_count": int(cnt or 0),
            "name": name,
        }
        for vid, cancel, cnt, name in rows
    }


def _agg_fees(db: Session, dfrom: date, dto: date) -> dict[str, dict]:
    """매출내역(실측수수료)을 옵션ID별 집계. recognition_date 기준. SALE/REFUND 모두 포함
    (service_fee·sale_amount는 REFUND가 음수로 저장 — 사실 그대로 합산, D-3)."""
    rows = (
        db.query(
            CoupangRevenueFee.vendor_item_id,
            func.sum(CoupangRevenueFee.service_fee),
            func.sum(CoupangRevenueFee.service_fee_vat),
            func.sum(CoupangRevenueFee.sale_amount),
            func.count(CoupangRevenueFee.id),
            func.max(CoupangRevenueFee.vendor_item_name),
        )
        .filter(CoupangRevenueFee.recognition_date >= dfrom,
                CoupangRevenueFee.recognition_date <= dto)
        .group_by(CoupangRevenueFee.vendor_item_id)
        .all()
    )
    # codex[P2]: 쿠팡 정산은 service_fee + service_fee_vat 둘 다 차감 → total_fee로 합산.
    return {
        str(vid): {
            "service_fee": _f(fee),
            "service_fee_vat": _f(vat),
            "total_fee": _f(fee) + _f(vat),
            "settled_sale_amount": _f(sale),
            "fee_rows": int(cnt or 0),
            "name": name,
        }
        for vid, fee, vat, sale, cnt, name in rows
    }


def _agg_rg_settlement_fees(db: Session, dfrom: date, dto: date) -> dict[str, dict]:
    """RG 정산 수수료를 account_key별·fee_type별로 집계. D-6/D-7: 대조(reconciliation) 뷰용.

    날짜 필터: recognition_date_from과 recognition_date_to가 [dfrom, dto]와 겹치는 행.
    겹침 조건 = recognition_date_from <= dto AND recognition_date_to >= dfrom.
    반환: {account_key: {fee_type: amount, ..., "total": Decimal}}
    """
    rows = (
        db.query(
            CoupangRgSettlementFee.account_key,
            CoupangRgSettlementFee.fee_type,
            func.sum(CoupangRgSettlementFee.amount),
        )
        .filter(
            CoupangRgSettlementFee.recognition_date_from <= dto,
            CoupangRgSettlementFee.recognition_date_to >= dfrom,
        )
        .group_by(
            CoupangRgSettlementFee.account_key,
            CoupangRgSettlementFee.fee_type,
        )
        .all()
    )
    result: dict[str, dict] = {}
    for account_key, fee_type, amount in rows:
        entry = result.setdefault(account_key, {"total": _Z})
        entry[fee_type] = _f(amount)
        entry["total"] = entry["total"] + _f(amount)
    return result


def _product_master(db: Session) -> dict[str, dict]:
    """상품 옵션 마스터 — 이름·가격·등록수수료율·재고·원가성 공급가. 조망 베이스."""
    out: dict[str, dict] = {}
    for p in db.query(CoupangProductItem).all():
        out[str(p.vendor_item_id)] = {
            "name": p.item_name or p.seller_product_name,
            "sale_price": _f(p.sale_price),
            "supply_price": p.supply_price,  # None 가능 — 원가 미설정
            "sale_agent_commission": p.sale_agent_commission,
            "stock": p.amount_in_stock if p.amount_in_stock is not None else p.max_buy_count,
            "on_sale": p.on_sale,
            "status_name": p.status_name,
            "brand": p.brand,
            "account_key": p.account_key,
            "vendor_id": p.vendor_id,
        }
    return out


def _cost_master(db: Session) -> dict[str, dict]:
    """옵션ID(vendor_item_id) → 내부 product_master(원가·정식 상품명) 다리. (트랙 D-12)

    원가는 coupang_product_item.supply_price(실거래 0.6% 커버)가 아니라 내부
    product_master.cost_price(89% 보유)에 product_channel_mapping(coupang, is_active)으로
    닿는다 — 실거래 66% 커버. profit_calculator._get_option_id_map과 **동일 경로**(is_active
    매핑)라 기존 회계엔진과 원가 원천이 일치한다. (라이브 진단: 옵션ID당 원가충돌 0건 확인)
    """
    products = {p.id: p for p in db.query(ProductMaster).all()}
    rows = (
        db.query(
            ProductChannelMapping.channel_product_id,
            ProductChannelMapping.product_id,
        )
        .join(Channel, ProductChannelMapping.channel_id == Channel.id)
        .filter(
            Channel.platform == "coupang",
            ProductChannelMapping.is_active.is_(True),
        )
        .all()
    )
    # 옵션ID별 후보 수집 — 같은 옵션ID가 WING+RG 두 채널에 매핑되거나(같은 product) 재지정
    # 이력으로 중복될 수 있다. 라이브 진단상 현재 원가충돌 0건이나, codex[P2] 견고성 지적
    # 수용: 첫 행 임의채택 금지하고 결정적으로 고른다.
    candidates: dict[str, list] = {}
    for cpid, pid in rows:
        pm = products.get(pid)
        if pm is not None:
            candidates.setdefault(str(cpid), []).append(pm)

    out: dict[str, dict] = {}
    for cpid, pms in candidates.items():
        # 원가>0 보유 행 우선(0/None이 유효원가 가리지 않게). 동률이면 product_id 최소(결정적).
        costed = [pm for pm in pms if pm.cost_price and pm.cost_price > 0]
        chosen = min(costed or pms, key=lambda pm: pm.id)
        distinct = {pm.cost_price for pm in costed}
        if len(distinct) > 1:  # 서로 다른 실원가 충돌 — 임의판단 금지, 사실 경고(D-3)
            log.warning(
                "옵션ID %s 활성 쿠팡매핑이 서로 다른 원가 %s 보유 — product_id 최소(%s) 선택",
                cpid, sorted(map(str, distinct)), chosen.id,
            )
        out[cpid] = {"cost_price": chosen.cost_price, "name": chosen.product_name}
    return out


# ──────────────────────────────────────────────
# 결합 엔진 — 5소스 병합 + 3축 파생
# ──────────────────────────────────────────────
def compute_command_center(db: Session, dfrom: date, dto: date) -> dict:
    """옵션ID 합집합을 키로 5소스를 병합해 3축(회계·광고·상품)을 파생.

    Decimal은 그대로 반환(라우터에서 str 직렬화). D-3: 사실/지표만, 추천 없음.
    순이익 원가는 내부 product_master.cost_price 우선(D-12), 없으면 coupang supply_price 폴백.
    둘 다 없으면 미반영(has_cost=False)으로 표기.
    """
    master = _product_master(db)
    cost_master = _cost_master(db)  # D-12: 내부 원가·정식상품명 다리
    orders = _agg_orders(db, dfrom, dto)
    ads = _agg_ads(db, dfrom, dto)
    returns = _agg_returns(db, dfrom, dto)
    fees = _agg_fees(db, dfrom, dto)
    rg_fees = _agg_rg_settlement_fees(db, dfrom, dto)  # D-6/D-7: 대조 뷰용

    all_vids = set(master) | set(orders) | set(ads) | set(returns) | set(fees)

    account_rows: list[dict] = []
    ad_rows: list[dict] = []
    product_rows: list[dict] = []

    for vid in all_vids:
        m = master.get(vid, {})
        cm = cost_master.get(vid, {})  # D-12: 내부 원가·정식상품명
        o = orders.get(vid, {})
        a = ads.get(vid, {})
        r = returns.get(vid, {})
        f = fees.get(vid, {})
        # 이름 폴백: 쿠팡옵션명 → 내부 정식상품명 → 주문 → 매출내역 → 반품 (화면 유지)
        name = (
            m.get("name") or cm.get("name") or o.get("name") or f.get("name")
            or r.get("name") or "(이름 미상)"
        )

        revenue = o.get("revenue", _Z)
        order_qty = o.get("qty", 0)
        unit_price = o.get("unit_price", _Z) or m.get("sale_price", _Z)
        return_qty = r.get("return_qty", 0)
        return_deduction = unit_price * return_qty  # 추정(평균단가×반품수량)
        service_fee = f.get("service_fee", _Z)
        service_fee_vat = f.get("service_fee_vat", _Z)
        total_fee = f.get("total_fee", _Z)  # 수수료+수수료VAT (쿠팡 실차감, codex[P2])
        ad_spend = a.get("spend", _Z)

        # 원가 — D-12: 내부 product_master.cost_price 우선, 없으면 coupang supply_price 폴백.
        # 단가는 순판매수량(주문−반품)에 적용. 0/None은 원가정보 없음으로 간주(미반영).
        internal_cost = cm.get("cost_price")
        supply = m.get("supply_price")
        net_qty = order_qty - return_qty
        if internal_cost is not None and internal_cost > 0:
            unit_cost, cost_source = _f(internal_cost), "internal"
        elif supply is not None and supply > 0:
            unit_cost, cost_source = _f(supply), "coupang_supply"
        else:
            unit_cost, cost_source = None, None
        if unit_cost is not None:
            cost = unit_cost * net_qty
            has_cost = True
        else:
            cost = _Z
            has_cost = False

        net_profit = revenue - return_deduction - total_fee - ad_spend - cost

        account_rows.append({
            "vendor_item_id": vid, "name": name,
            "revenue": revenue, "return_deduction": return_deduction,
            "service_fee": service_fee, "service_fee_vat": service_fee_vat,
            "total_fee": total_fee, "ad_spend": ad_spend,
            "cost": cost, "has_cost": has_cost, "cost_source": cost_source,
            "net_profit": net_profit,
        })

        clicks = a.get("clicks", 0)
        impressions = a.get("impressions", 0)
        conv_revenue = a.get("conv_revenue", _Z)
        ad_rows.append({
            "vendor_item_id": vid, "name": name,
            "ad_spend": ad_spend, "impressions": impressions, "clicks": clicks,
            "conv_revenue": conv_revenue,
            "roas": _ratio(conv_revenue, ad_spend),  # 사실(배수). 추천 아님
            "ctr": _ratio(Decimal(clicks), impressions),
        })

        product_rows.append({
            "vendor_item_id": vid, "name": name,
            "order_count": o.get("order_count", 0), "order_qty": order_qty,
            "return_qty": return_qty,
            "return_rate": _ratio(Decimal(return_qty), order_qty),
            "stock": m.get("stock"), "on_sale": m.get("on_sale"),
            "status_name": m.get("status_name"),
            "sale_price": m.get("sale_price", _Z),
            "in_master": vid in master,
        })

    # 정렬: 회계=순이익 desc, 광고=비용 desc, 상품=주문수 desc (결정론적, D-3)
    account_rows.sort(key=lambda x: x["net_profit"], reverse=True)
    ad_rows.sort(key=lambda x: x["ad_spend"], reverse=True)
    product_rows.sort(key=lambda x: x["order_count"], reverse=True)

    account_sum = {
        "revenue": sum((x["revenue"] for x in account_rows), _Z),
        "return_deduction": sum((x["return_deduction"] for x in account_rows), _Z),
        "service_fee": sum((x["service_fee"] for x in account_rows), _Z),
        "service_fee_vat": sum((x["service_fee_vat"] for x in account_rows), _Z),
        "total_fee": sum((x["total_fee"] for x in account_rows), _Z),
        "ad_spend": sum((x["ad_spend"] for x in account_rows), _Z),
        "cost": sum((x["cost"] for x in account_rows), _Z),
        "net_profit": sum((x["net_profit"] for x in account_rows), _Z),
        "cost_covered_options": sum(1 for x in account_rows if x["has_cost"]),
        "cost_internal_options": sum(1 for x in account_rows if x["cost_source"] == "internal"),
        "cost_supply_options": sum(1 for x in account_rows if x["cost_source"] == "coupang_supply"),
        "option_count": len(account_rows),
    }
    ad_spend_total = sum((x["ad_spend"] for x in ad_rows), _Z)
    ad_conv_total = sum((x["conv_revenue"] for x in ad_rows), _Z)
    ad_sum = {
        "ad_spend": ad_spend_total,
        "impressions": sum(x["impressions"] for x in ad_rows),
        "clicks": sum(x["clicks"] for x in ad_rows),
        "conv_revenue": ad_conv_total,
        "roas": _ratio(ad_conv_total, ad_spend_total),
    }
    product_sum = {
        "option_count": len(product_rows),
        "order_count": sum(x["order_count"] for x in product_rows),
        "order_qty": sum(x["order_qty"] for x in product_rows),
        "return_qty": sum(x["return_qty"] for x in product_rows),
    }

    # D-6/D-7: RG 정산 비용 대조(reconciliation) 뷰 — net_profit 불변, 독립 섹션.
    rg_total = sum((v["total"] for v in rg_fees.values()), _Z)
    rg_by_account = [
        {
            "account_key": ak,
            "total": v["total"],
            "sale_fee": v.get("sale_fee", _Z),
            "fulfillment": v.get("fulfillment", _Z),
            "storage": v.get("storage", _Z),
            "return_fee": v.get("return_shipping", _Z) + v.get("return_handling", _Z),
            "other": v.get("warehousing", _Z) + v.get("ad_sales", _Z),
        }
        for ak, v in sorted(rg_fees.items())
    ]
    rg_settlement = {
        "summary": {
            "total": rg_total,
            "has_data": len(rg_fees) > 0,
            "note": "RG 정산 비용(미반영) — Phase1 대조 지표. net_profit에 미포함(D-6).",
        },
        "by_account": rg_by_account,
    }

    return {
        "period": {"from": dfrom.isoformat(), "to": dto.isoformat()},
        "account": {"summary": account_sum, "by_option": account_rows},
        "ad": {"summary": ad_sum, "by_option": ad_rows},
        "product": {"summary": product_sum, "by_option": product_rows},
        "rg_settlement": rg_settlement,
    }
