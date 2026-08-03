# rocket_intelligence.py — 쿠팡 로켓배송(1P) 종합조망 편입 Harness (트랙 rocket-1p S4/S4.5c, D-11/D-12/D-13)
#
# 목적(D-11): 1P를 종합조망(Command Center) 돈 축에 3P/RG와 나란히 올린다. 1P는 PO그레인
#   (purchase_order_seq, vendor_item_id 없음)이라 기존 compute_command_center의 옵션그레인
#   by_option 병합이 불가 → 별도 채널 블록으로 산출(additive·읽기전용, 3P/RG net_profit 불변).
#
# 단일 책임 SA 4종 + 결합(원칙18-2):
#   ① _agg_rocket_revenue : Σ sum_of_order_amount(gross), 발주일 KST(po_created_at+9h) 윈도우 (매출 D-3)
#   ② _agg_rocket_ad      : coupang_ad_report sell_type='Retail'(로켓배송) 광고비 합 (D-4, 계정단위)
#   ③ _agg_rocket_drift   : 발주(gross) vs 정산(payment_amount), vendor_payment_seqs→distinct invoice 조인 (D-5)
#   ④ _rocket_cost        : Σ(발주상세 per-SKU order_qty × product_master.cost_price[매핑]) (원가 D-4/D-13, S4.5c)
#
# S4.5c(D-13): 발주상세 per-SKU(CoupangRocketPurchaseOrderItem) → RocketProductCostMap(상품번호→internal_sku)
#   → product_master.cost_price 조인으로 원가 산정. net_profit cost 반영(has_cost=True 전환, D-12 해소).
#   ★커버리지%로 미매핑/미수집 투명화(원칙22): 매핑 안 된 매출분은 원가 누락 → net_profit 과대. 부분 커버리지를
#   확정값처럼 쓰지 않도록 coverage_pct·미해결 금액을 함께 노출. 매핑 0건이면 S4와 동일(has_cost=False).
# D-3/D-7: 시스템은 사실·지표·드리프트만 — 전략 추천 없음.
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    CoupangAdOptionDaily,
    CoupangAdReport,
    CoupangRocketOptionSku,
    CoupangRocketPurchaseOrder,
    CoupangRocketPurchaseOrderItem,
    CoupangRocketSettlement,
    ProductMaster,
    RocketProductCostMap,
)

log = logging.getLogger(__name__)

_Z = Decimal("0")
_Q4 = Decimal("0.0001")
_KST = timedelta(hours=9)

# 광고 XLSX 판매방식 코드: Retail=로켓배송(1P). (intelligence.py L453 "Retail=로켓배송(ad_costs 확정)")
ROCKET_AD_SELL_TYPE = "Retail"


def _f(v) -> Decimal:
    """None/숫자 → Decimal. 집계(func.sum)가 None(행 없음)이면 0."""
    if v is None:
        return _Z
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _ratio4(num: Decimal, den: Decimal) -> "Decimal | None":
    """비율 = num/den(4자리 quantize). den 0이면 None(0 나눗셈 방지)."""
    if not den:
        return None
    return (num / den).quantize(_Q4)


def _kst_window_utc(dfrom: date, dto: date) -> tuple[datetime, datetime]:
    """KST 날짜 윈도우 [dfrom, dto] → UTC naive 경계. po_created_at은 UTC naive 저장(파서 _to_dt_utc_naive).

    발주일(KST) = (po_created_at_UTC + 9h).date(). 따라서 KST [dfrom, dto]에 드는 UTC 구간 =
    [dfrom 00:00 KST, dto 24:00 KST) = [dfrom 00:00 − 9h, dto 23:59:59.999999 − 9h] (UTC).
    """
    start = datetime.combine(dfrom, time.min) - _KST
    end = datetime.combine(dto, time.max) - _KST
    return start, end


# ──────────────────────────────────────────────
# ① 매출 (발주 gross, 발주일 KST 윈도우) — D-3
# ──────────────────────────────────────────────
def _agg_rocket_revenue(db: Session, dfrom: date, dto: date,
                        vendor_id: str | None = None) -> dict:
    """발주 PO를 발주일 KST 윈도우로 집계. 매출=Σ sum_of_order_amount(gross, VAT포함, D-9 §6-1③).

    receiving_amount(실입고)도 함께 반환 — 발주↔납품 운영 드리프트(D-9, 참고)용.
    po_created_at이 None인 PO는 발주일 미상이라 윈도우에 못 들어감(제외, no_date_count로 투명화).
    vendor_id 주면 해당 계정만(단일 1P 계정=오하이테크 A01029796). None이면 전체.
    """
    start, end = _kst_window_utc(dfrom, dto)
    q = (
        db.query(
            func.coalesce(func.sum(CoupangRocketPurchaseOrder.sum_of_order_amount), 0),
            func.coalesce(func.sum(CoupangRocketPurchaseOrder.sum_of_receiving_amount), 0),
            func.coalesce(func.sum(CoupangRocketPurchaseOrder.order_qty), 0),
            func.count(CoupangRocketPurchaseOrder.id),
        )
        .filter(
            CoupangRocketPurchaseOrder.po_created_at >= start,
            CoupangRocketPurchaseOrder.po_created_at <= end,
        )
    )
    if vendor_id is not None:
        q = q.filter(CoupangRocketPurchaseOrder.vendor_id == vendor_id)
    order_amt, recv_amt, qty, cnt = q.one()

    # 발주일 미상(po_created_at NULL) PO 수 — 윈도우에서 빠진 잠재 누락 투명화(D-3).
    nd_q = db.query(func.count(CoupangRocketPurchaseOrder.id)).filter(
        CoupangRocketPurchaseOrder.po_created_at.is_(None)
    )
    if vendor_id is not None:
        nd_q = nd_q.filter(CoupangRocketPurchaseOrder.vendor_id == vendor_id)
    no_date_count = int(nd_q.scalar() or 0)

    return {
        "order_amount": _f(order_amt),          # ★매출(D-3): 쿠팡이 발주한 gross 금액
        "receiving_amount": _f(recv_amt),       # 실입고(납품) gross — 발주↔납품 드리프트용
        "order_qty": int(qty or 0),
        "po_count": int(cnt or 0),
        "no_date_po_count": no_date_count,      # 발주일 미상(윈도우 제외)
    }


# ──────────────────────────────────────────────
# ② 광고비 (Retail=로켓배송, 계정단위) — D-4
# ──────────────────────────────────────────────
def _agg_rocket_ad(db: Session, dfrom: date, dto: date,
                   vendor_id: str | None = None) -> Decimal:
    """로켓배송(1P) 광고비 = coupang_ad_report sell_type='Retail' ad_spend 합(report_date 윈도우).

    D-4: 1P 광고는 옵션귀속 없이 계정단위 차감(3P/RG처럼 옵션 매칭 불필요). 현재 단일 1P 계정
    (오하이테크)이라 모든 Retail 행이 1P 로켓배송 광고 → vendor_id 미지정(None)이면 전체 Retail 합산.
    ★1P 광고 vendor_id는 라이브 미관측(로컬 Retail 0행)이라 추정하지 않고 sell_type로 식별(원칙: 추정 금지).
    여러 1P 계정이 생기면 vendor_id 파라미터로 분리.
    """
    q = db.query(func.coalesce(func.sum(CoupangAdReport.ad_spend), 0)).filter(
        CoupangAdReport.report_date >= dfrom,
        CoupangAdReport.report_date <= dto,
        CoupangAdReport.sell_type == ROCKET_AD_SELL_TYPE,
    )
    if vendor_id is not None:
        q = q.filter(CoupangAdReport.vendor_id == vendor_id)
    return _f(q.scalar())


def _rocket_option_names(db: Session, dfrom: date, dto: date,
                         vendor_id: str | None, option_ids: list[str]) -> dict[str, str]:
    """옵션ID → 상품명(가장 최근 report_date의 non-null).

    상품명은 개명될 수 있어 **최신 이름**을 쓴다(사람이 아는 이름). 화면에 실제로 나가는
    상위 N개만 조회한다 — 전 옵션을 끌어오면 표시 한 줄 때문에 창 전체를 읽게 된다.
    """
    if not option_ids:
        return {}
    q = (
        db.query(
            CoupangAdOptionDaily.ad_option_id,
            CoupangAdOptionDaily.report_date,
            CoupangAdOptionDaily.ad_product_name,
        )
        .filter(
            CoupangAdOptionDaily.report_date >= dfrom,
            CoupangAdOptionDaily.report_date <= dto,
            CoupangAdOptionDaily.sell_type == ROCKET_AD_SELL_TYPE,
            CoupangAdOptionDaily.ad_option_id.in_(option_ids),
            CoupangAdOptionDaily.ad_product_name.isnot(None),
        )
    )
    if vendor_id is not None:
        q = q.filter(CoupangAdOptionDaily.vendor_id == vendor_id)

    # 오래된 것부터 훑어 덮어쓰면 마지막에 최신 이름이 남는다.
    out: dict[str, str] = {}
    for opt_id, _rdate, name in q.order_by(CoupangAdOptionDaily.report_date.asc()).all():
        out[str(opt_id)] = name
    return out


def _rocket_ad_options(db: Session, dfrom: date, dto: date,
                       vendor_id: str | None, account_total: Decimal,
                       limit: int = 30) -> dict:
    """1P 광고비를 **상품(옵션) 단위**로 — 표시 전용(트랙 ohitech-ad D-12/D-13).

    ★순이익에는 쓰지 않는다. 차감 축은 계정 총액(`coupang_ad_report`, 전체 기준 D-10) 그대로다.
      이 값은 PA 기준 Billboard라 정의가 다르고, 2026-08-03 실측 기준 계정 총액과 0.02%
      어긋나는데 **원인이 미규명**이다. 원인을 모르는 채 차감 축을 갈아타면 어긋나도 못 본다.
      → 대신 `reconciliation`으로 그 차이를 **항상 드러낸다**. 차이가 벌어지면 화면에서 보인다.
    """
    q = (
        db.query(
            CoupangAdOptionDaily.ad_option_id,
            func.sum(CoupangAdOptionDaily.ad_spend).label("spend"),
            func.sum(CoupangAdOptionDaily.impressions),
            func.sum(CoupangAdOptionDaily.clicks),
            func.sum(CoupangAdOptionDaily.conversion_revenue),
        )
        .filter(
            CoupangAdOptionDaily.report_date >= dfrom,
            CoupangAdOptionDaily.report_date <= dto,
            CoupangAdOptionDaily.sell_type == ROCKET_AD_SELL_TYPE,
        )
    )
    if vendor_id is not None:
        q = q.filter(CoupangAdOptionDaily.vendor_id == vendor_id)
    rows = q.group_by(CoupangAdOptionDaily.ad_option_id).all()

    option_total = sum((_f(r[1]) for r in rows), Decimal("0"))
    diff = option_total - account_total
    top = sorted(rows, key=lambda r: _f(r[1]), reverse=True)[:limit]
    names = _rocket_option_names(db, dfrom, dto, vendor_id, [str(r[0]) for r in top])
    return {
        "options": [
            {
                "option_id": str(r[0]),
                # 상품명은 없을 수 있다(컬럼 추가 이전 적재분·빈 셀) → 프론트가 옵션ID로 폴백한다.
                "product_name": names.get(str(r[0])),
                "ad_spend": _f(r[1]),
                "impressions": int(r[2] or 0),
                "clicks": int(r[3] or 0),
                "conversion_revenue": _f(r[4]),
            }
            for r in top
        ],
        "option_count": len(rows),
        "shown": len(top),
        "reconciliation": {
            "option_sum": option_total,
            "account_total": account_total,     # 순이익에 실제로 쓰이는 값
            "diff": diff,
            "diff_pct": (diff / account_total * 100) if account_total else None,
            "basis": ("옵션 합계는 Billboard(PA 기준), 계정 총액은 report/SALES(전체 기준, D-10). "
                      "정의가 달라 완전히 같지 않다 — 차이가 커지면 수집이 어긋난 신호다."),
        },
    }


# ──────────────────────────────────────────────
# ②-b 상품(SKU)별 손익 — 표시 전용 (트랙 ohitech-ad D-16)
# ──────────────────────────────────────────────
def _rocket_sku_pnl(db: Session, dfrom: date, dto: date,
                    vendor_id: str | None, limit: int = 30) -> dict:
    """1P 손익을 **상품(SKU=상품번호) 단위**로. 매출·광고비·원가·순이익.

    ★왜 옵션이 아니라 SKU인가: 매출(발주 라인)도 원가(cost_price 매핑)도 원래 **상품번호 그레인**
      이고, 광고비만 옵션 그레인이다. 그런데 sku→option은 1:N이다(실측 최대 3, 활동 기간이 실제로
      겹쳐 기간 필터로 안 갈린다). 옵션 축으로 내리면 매출·원가를 **안분(추정)** 해야 하지만,
      SKU 축으로 올리면 광고비를 **더하기만** 하면 된다 — 추정이 들어가는 자리가 없다.

    ★순이익에는 쓰지 않는다(D-13 유지). 계정 net_profit의 광고 차감축은 `coupang_ad_report`
      계정 총액(비-PA 포함, D-10)인데 여기 광고비는 Billboard PA 기준이라 정의가 다르다.
      따라서 **SKU 순이익 합 ≠ 계정 순이익**이다 — 그 차이를 coverage로 항상 드러낸다.

    ★원가 미매핑 SKU는 net_profit을 **내지 않는다**(None). 원가 0으로 계산하면 과대 순이익이
      정상값과 섞여 구분이 안 된다(원칙22). 사유는 profit_basis로 행마다 말한다.
    """
    # ── ① 광고비: 옵션 그레인 → 브리지로 SKU에 합산(더하기, 추정 없음) ──
    ad_rows = (
        db.query(
            CoupangAdOptionDaily.ad_option_id,
            func.sum(CoupangAdOptionDaily.ad_spend),
            func.sum(CoupangAdOptionDaily.clicks),
            func.sum(CoupangAdOptionDaily.conversion_revenue),
        )
        .filter(
            CoupangAdOptionDaily.report_date >= dfrom,
            CoupangAdOptionDaily.report_date <= dto,
            CoupangAdOptionDaily.sell_type == ROCKET_AD_SELL_TYPE,
            *([CoupangAdOptionDaily.vendor_id == vendor_id] if vendor_id is not None else []),
        )
        .group_by(CoupangAdOptionDaily.ad_option_id)
        .all()
    )
    bridge = {
        str(o): str(s)
        for o, s in db.query(CoupangRocketOptionSku.option_id, CoupangRocketOptionSku.sku_id).all()
    }

    by_sku: dict[str, dict] = {}
    ad_total = _Z
    ad_unbridged = _Z          # 브리지 없는 옵션의 광고비 — 어느 SKU에도 안 붙는다(투명화)
    unbridged_options = 0
    for opt, spend, clicks, conv in ad_rows:
        spend = _f(spend)
        ad_total += spend
        sku = bridge.get(str(opt))
        if sku is None:
            ad_unbridged += spend
            unbridged_options += 1
            continue
        g = by_sku.setdefault(sku, {"ad_spend": _Z, "clicks": 0, "conv_revenue": _Z,
                                    "option_count": 0, "revenue": _Z, "cost": _Z,
                                    "qty": 0, "has_cost": False, "name": None})
        g["ad_spend"] += spend
        g["clicks"] += int(clicks or 0)
        g["conv_revenue"] += _f(conv)
        g["option_count"] += 1

    # ── ② 매출·원가: 발주상세 라인(상품번호 그레인, 발주일 KST 윈도우) ──
    start, end = _kst_window_utc(dfrom, dto)
    seq_subq = select(CoupangRocketPurchaseOrder.purchase_order_seq).where(
        CoupangRocketPurchaseOrder.po_created_at >= start,
        CoupangRocketPurchaseOrder.po_created_at <= end,
        *([CoupangRocketPurchaseOrder.vendor_id == vendor_id] if vendor_id is not None else []),
    )
    Item = CoupangRocketPurchaseOrderItem
    po_rows = (
        db.query(
            Item.product_number,
            Item.product_name,
            Item.order_qty,
            Item.line_order_amount,
            RocketProductCostMap.status,
            ProductMaster.cost_price,
        )
        .outerjoin(RocketProductCostMap, RocketProductCostMap.product_number == Item.product_number)
        .outerjoin(ProductMaster, ProductMaster.internal_sku == RocketProductCostMap.internal_sku)
        .filter(Item.purchase_order_seq.in_(seq_subq))
        .all()
    )
    for pnum, pname, qty, line_amt, status, cost_price in po_rows:
        sku = str(pnum)
        if sku not in by_sku:
            continue     # 광고를 안 돌린 상품 — 이 표는 광고 축 조망이다(광고비 0행을 만들지 않는다)
        g = by_sku[sku]
        g["revenue"] += _f(line_amt)
        g["qty"] += int(qty or 0)
        if g["name"] is None and pname:
            g["name"] = pname
        # 원가는 confirmed 매핑 + master 존재일 때만. ignored는 "원가 0으로 결정됨"이라 확정 취급.
        if status == "confirmed" and cost_price is not None:
            g["cost"] += _f(cost_price) * int(qty or 0)
            g["has_cost"] = True
        elif status == "ignored":
            g["has_cost"] = True

    # ── ③ 라벨: 발주상세 이름이 없으면 광고 XLSX 상품명으로 폴백 ──
    need_name = [s for s, g in by_sku.items() if not g["name"]]
    if need_name:
        opt_of_sku = {v: k for k, v in bridge.items() if v in set(need_name)}
        if opt_of_sku:
            for opt_id, nm in (
                db.query(CoupangAdOptionDaily.ad_option_id, CoupangAdOptionDaily.ad_product_name)
                .filter(
                    CoupangAdOptionDaily.ad_option_id.in_(list(opt_of_sku.values())),
                    CoupangAdOptionDaily.ad_product_name.isnot(None),
                )
                .order_by(CoupangAdOptionDaily.report_date.asc())
                .all()
            ):
                for sku, o in opt_of_sku.items():
                    if o == str(opt_id):
                        by_sku[sku]["name"] = nm

    # ── ④ 행 구성 + 커버리지(광고비 가중) ──
    ad_with_revenue = _Z
    ad_with_cost = _Z
    items = []
    for sku, g in by_sku.items():
        has_rev = g["revenue"] > 0
        if has_rev:
            ad_with_revenue += g["ad_spend"]
        if g["has_cost"]:
            ad_with_cost += g["ad_spend"]
        if g["has_cost"] and has_rev:
            net = g["revenue"] - g["ad_spend"] - g["cost"]
            basis = "매출(발주 gross)−광고비(PA)−원가(확정 매핑)"
        else:
            net = None
            basis = ("이 기간 발주 없음 — 1P 매출은 발주일 귀속이라 매출 0이다"
                     if not has_rev else "원가 매핑 미확정 — 0으로 계산하면 순이익이 과대해진다")
        items.append({
            "sku_id": sku,
            "product_name": g["name"],
            "option_count": g["option_count"],
            "ad_spend": g["ad_spend"],
            "clicks": g["clicks"],
            "conversion_revenue": g["conv_revenue"],
            "revenue": g["revenue"],
            "order_qty": g["qty"],
            "cost": g["cost"] if g["has_cost"] else None,
            "net_profit": net,
            "profit_basis": basis,
        })
    items.sort(key=lambda r: r["ad_spend"], reverse=True)
    top = items[:limit]

    def _pct(part: Decimal) -> "Decimal | None":
        return (part / ad_total * 100) if ad_total else None

    return {
        "skus": top,
        "sku_count": len(items),
        "shown": len(top),
        "coverage": {
            # 분모는 항상 이 기간 1P 광고비 전체 — 도달하지 못한 돈이 얼마인지가 요점이다.
            "ad_total": ad_total,
            "ad_bridged": ad_total - ad_unbridged,
            "ad_bridged_pct": _pct(ad_total - ad_unbridged),
            "ad_with_revenue": ad_with_revenue,
            "ad_with_revenue_pct": _pct(ad_with_revenue),
            "ad_with_cost": ad_with_cost,
            "ad_with_cost_pct": _pct(ad_with_cost),
            "ad_unbridged": ad_unbridged,
            "unbridged_option_count": unbridged_options,
            "note": (
                "브리지=옵션ID↔상품번호(판매분석 관측을 누적 보존, D-16). 매출=발주 라인 gross(발주일 KST), "
                "원가=발주수량×cost_price(확정 매핑). 광고비는 Billboard PA 기준이라 계정 총액(비-PA 포함)과 "
                "정의가 달라 **SKU 순이익 합 ≠ 계정 순이익**이다 — 이 표는 표시 전용이다. "
                "원가 미도달분은 순이익을 내지 않는다(0으로 계산하면 과대 순이익이 섞인다)."
            ),
        },
    }


# ──────────────────────────────────────────────
# ③ 발주↔정산 드리프트 (vendor_payment_seqs 조인) — D-5
# ──────────────────────────────────────────────
def _agg_rocket_drift(db: Session, dfrom: date, dto: date,
                      order_amount: Decimal, vendor_id: str | None = None) -> dict:
    """윈도우 발주 PO들에 매핑된 계산서(정산) 지급예정 합과 발주합의 드리프트.

    PO.vendor_payment_seqs(계산서번호 리스트, D-9 §6-1④) → distinct invoice_seq 집합 →
    Σ CoupangRocketSettlement.payment_amount(gross 지급예정). 1PO↔N계산서·1계산서↔N PO(부분정산)
    다중성은 **distinct invoice로 중복제거**해 한 계산서를 두 번 안 센다.
    drift_abs = 발주 − 정산(매핑). 양수 = 발주가 정산보다 큼(미정산·지연분).

    ★참고치(권위값 아님): ① 정산은 발주보다 지연(윈도우 발주의 정산이 아직 안 옴) ② 매핑 계산서가
    윈도우 밖 PO도 포함하면 정산합이 과대 — 둘 다 잔차로 남는다(note 명시). reconcile 패턴과 동일 철학.
    """
    start, end = _kst_window_utc(dfrom, dto)
    q = db.query(CoupangRocketPurchaseOrder.vendor_payment_seqs).filter(
        CoupangRocketPurchaseOrder.po_created_at >= start,
        CoupangRocketPurchaseOrder.po_created_at <= end,
    )
    if vendor_id is not None:
        q = q.filter(CoupangRocketPurchaseOrder.vendor_id == vendor_id)

    invoice_seqs: set[int] = set()
    po_with_mapping = 0
    for (seqs,) in q.all():
        if seqs:  # JSON list (또는 None/[])
            po_with_mapping += 1
            for s in seqs:
                try:
                    invoice_seqs.add(int(s))
                except (TypeError, ValueError):
                    continue

    settled = _Z
    if invoice_seqs:
        settled = _f(
            db.query(func.coalesce(func.sum(CoupangRocketSettlement.payment_amount), 0))
            .filter(CoupangRocketSettlement.invoice_seq.in_(invoice_seqs))
            .scalar()
        )

    drift_abs = order_amount - settled
    return {
        "order_amount": order_amount,
        "settled_amount": settled,
        "drift_abs": drift_abs,                       # 발주 − 정산(매핑). 양수=미정산·지연분
        "drift_pct": _ratio4(drift_abs, order_amount),  # 발주 대비 (None=발주 0)
        "mapped_po_count": po_with_mapping,
        "mapped_invoice_count": len(invoice_seqs),
        "note": (
            "발주(gross) vs 매핑 계산서 정산(payment_amount, distinct). 참고 드리프트(권위값 아님): "
            "정산은 발주보다 지연(윈도우 발주의 정산 미도래)·매핑 계산서가 윈도우 밖 PO 포함 시 정산 과대. "
            "사실·지표만(D-5/D-7)."
        ),
    }


# ──────────────────────────────────────────────
# ④ 원가 (발주상세 per-SKU × 매핑 cost_price) — D-4/D-13, S4.5c
# ──────────────────────────────────────────────
def _rocket_cost(db: Session, dfrom: date, dto: date,
                 vendor_id: str | None = None,
                 window_order_amount: Decimal | None = None,
                 window_po_count: int | None = None) -> dict:
    """발주일 KST 윈도우 PO들의 발주상세 per-SKU 원가 = Σ(order_qty × product_master.cost_price[매핑]).

    조인(원칙18-8): 발주상세 라인(CoupangRocketPurchaseOrderItem) → RocketProductCostMap(상품번호→internal_sku)
      → ProductMaster.cost_price. status='confirmed'+master 존재만 원가 가산. 'ignored'=원가 0(결정된 제외).
      매핑 없음/마스터 없음 = 미해결(원가 미상) → cost 누락 투명화.
    ★커버리지(원칙22): 매핑 안 됐거나 발주상세 미수집 PO는 원가가 빠져 net_profit 과대. 두 누락을 함께 노출:
      - resolved_amount = confirmed+ignored 라인 발주금액(원가 결정된 매출분)
      - coverage_pct = resolved_amount / window_order_amount(PO그레인 총 발주, 미수집 PO까지 분모)
      - pos_without_detail_count = 윈도우 PO 중 발주상세 미수집 수.
    window_order_amount/window_po_count: 매출 SA 출력 주입(중복질의 회피, 원칙18-8). 미주입 시 자체 질의(독립 호출 가능).
    """
    start, end = _kst_window_utc(dfrom, dto)

    # 윈도우 PO seq (서브쿼리 — .in_() 파라미터 한계 회피, 매출 SA와 동일 필터).
    seq_subq = select(CoupangRocketPurchaseOrder.purchase_order_seq).where(
        CoupangRocketPurchaseOrder.po_created_at >= start,
        CoupangRocketPurchaseOrder.po_created_at <= end,
    )
    if vendor_id is not None:
        seq_subq = seq_subq.where(CoupangRocketPurchaseOrder.vendor_id == vendor_id)

    # 분모(미주입 시 자체 질의 — 매출 SA와 동일 결과 보장).
    if window_order_amount is None:
        oq = db.query(func.coalesce(func.sum(CoupangRocketPurchaseOrder.sum_of_order_amount), 0)).filter(
            CoupangRocketPurchaseOrder.po_created_at >= start,
            CoupangRocketPurchaseOrder.po_created_at <= end,
        )
        if vendor_id is not None:
            oq = oq.filter(CoupangRocketPurchaseOrder.vendor_id == vendor_id)
        window_order_amount = _f(oq.scalar())
    if window_po_count is None:
        cq = db.query(func.count(CoupangRocketPurchaseOrder.id)).filter(
            CoupangRocketPurchaseOrder.po_created_at >= start,
            CoupangRocketPurchaseOrder.po_created_at <= end,
        )
        if vendor_id is not None:
            cq = cq.filter(CoupangRocketPurchaseOrder.vendor_id == vendor_id)
        window_po_count = int(cq.scalar() or 0)

    Item = CoupangRocketPurchaseOrderItem
    rows = (
        db.query(
            Item.purchase_order_seq,
            Item.order_qty,
            Item.line_order_amount,
            RocketProductCostMap.status,
            ProductMaster.cost_price,
        )
        .outerjoin(RocketProductCostMap, RocketProductCostMap.product_number == Item.product_number)
        .outerjoin(ProductMaster, ProductMaster.internal_sku == RocketProductCostMap.internal_sku)
        .filter(Item.purchase_order_seq.in_(seq_subq))
        .all()
    )

    cost = _Z
    confirmed_amt = _Z
    ignored_amt = _Z
    unmapped_amt = _Z
    confirmed_sku = ignored_sku = unmapped_sku = 0
    pos_with_detail: set[int] = set()
    for po_seq, qty, line_amt, status, cost_price in rows:
        pos_with_detail.add(po_seq)
        line_amt = _f(line_amt)
        if status == "confirmed" and cost_price is not None:
            cost += _f(cost_price) * int(qty or 0)
            confirmed_amt += line_amt
            confirmed_sku += 1
        elif status == "ignored":
            ignored_amt += line_amt  # 원가 0(결정된 제외) — 해결됨
            ignored_sku += 1
        else:
            # 매핑 없음, 또는 confirmed인데 master 사라짐(원가 미상) → 미해결.
            if status == "confirmed":
                log.warning("rocket cost: confirmed 매핑이나 product_master 없음(po=%s) — 미해결 처리", po_seq)
            unmapped_amt += line_amt
            unmapped_sku += 1

    resolved_amt = confirmed_amt + ignored_amt
    detail_amt = confirmed_amt + ignored_amt + unmapped_amt
    has_cost = (confirmed_sku + ignored_sku) > 0  # 원가 기준 1건이라도 결정됨(=S4 대비 전환)
    return {
        "cost": cost,                                          # ★net_profit 차감 원가(confirmed만)
        "has_cost": has_cost,
        "coverage_pct": _ratio4(resolved_amt, window_order_amount),  # 원가 결정 매출분/총발주(미수집 PO 분모 포함)
        "resolved_order_amount": resolved_amt,                 # confirmed+ignored 라인 발주금액
        "confirmed_order_amount": confirmed_amt,
        "ignored_order_amount": ignored_amt,
        "unmapped_order_amount": unmapped_amt,                 # 발주상세 있으나 매핑 없는 매출분
        "detail_order_amount": detail_amt,                     # 발주상세 수집된 라인 합(미수집 PO 제외)
        "window_order_amount": window_order_amount,            # 분모(PO그레인 총 발주)
        "confirmed_sku_count": confirmed_sku,
        "ignored_sku_count": ignored_sku,
        "unmapped_sku_count": unmapped_sku,
        "pos_with_detail_count": len(pos_with_detail),
        "pos_without_detail_count": max(0, window_po_count - len(pos_with_detail)),  # 발주상세 미수집 PO
        "note": (
            "원가 = Σ(발주상세 order_qty × product_master.cost_price[상품번호→internal_sku 매핑, D-13]). "
            "confirmed만 가산·ignored=원가0·미매핑/미수집은 원가 누락 → coverage_pct로 투명화(원칙22). "
            "커버리지<100%면 net_profit는 원가 과소반영(순이익 과대)일 수 있음."
        ),
    }


# ──────────────────────────────────────────────
# 결합 — 1P 채널 블록
# ──────────────────────────────────────────────
def compute_rocket_overview(db: Session, dfrom: date, dto: date,
                            vendor_id: str | None = None) -> dict:
    """로켓배송(1P) 돈 축 종합조망 블록. 매출(발주)·광고·원가·순이익·발주↔정산 드리프트.

    Decimal은 그대로 반환(라우터에서 str 직렬화). 읽기전용 — 3P/RG 종합조망 값 불변(별도 블록, D-11).
    net_profit = 매출(발주) − 광고비 − 원가(S4.5c, D-13). 원가 = Σ(발주상세 order_qty × cost_price[매핑]).
    ★커버리지(원칙22): 매핑/발주상세 미수집분은 원가 누락 → cost_coverage.coverage_pct로 투명화. 매핑 0건이면
    has_cost=False·cost=0(S4와 동일 동작 보존). 부분 커버리지의 net_profit를 확정값처럼 쓰지 않게 함께 노출.
    """
    rev = _agg_rocket_revenue(db, dfrom, dto, vendor_id)
    ad_spend = _agg_rocket_ad(db, dfrom, dto, vendor_id)
    drift = _agg_rocket_drift(db, dfrom, dto, rev["order_amount"], vendor_id)
    cost_block = _rocket_cost(
        db, dfrom, dto, vendor_id,
        window_order_amount=rev["order_amount"],  # 원칙18-8: 매출 SA 출력 주입(중복질의 회피)
        window_po_count=rev["po_count"],
    )

    # net_profit = 매출 − 광고 − 원가(confirmed). 매핑 0건이면 cost=0 → S4와 동일(매출−광고).
    cost = cost_block["cost"]
    has_cost = cost_block["has_cost"]
    net_profit = rev["order_amount"] - ad_spend - cost

    period: dict = {"from": dfrom.isoformat(), "to": dto.isoformat()}
    if vendor_id is not None:
        period["vendor_id"] = vendor_id

    return {
        "period": period,
        "channel": "COUPANG_ROCKET",      # D-6: 1P 채널 (오하이테크)
        "revenue": rev["order_amount"],   # ★매출 = 발주 gross (D-3)
        "receiving_amount": rev["receiving_amount"],
        "order_qty": rev["order_qty"],
        "po_count": rev["po_count"],
        "no_date_po_count": rev["no_date_po_count"],
        "ad_spend": ad_spend,
        # 상품(옵션)별 광고비 — 표시 전용. 순이익은 위 ad_spend(계정 총액)만 쓴다(D-13).
        "ad_options": _rocket_ad_options(db, dfrom, dto, vendor_id, ad_spend),
        "sku_pnl": _rocket_sku_pnl(db, dfrom, dto, vendor_id),   # 상품(SKU)별 손익 — 표시 전용(D-16)
        "cost": cost,                     # ★원가(confirmed 매핑, S4.5c/D-13)
        "has_cost": has_cost,             # 매핑 1건이라도 결정되면 True(D-12 해소)
        "net_profit": net_profit,         # = 매출 − 광고 − 원가
        "net_profit_basis": (
            "net_profit = revenue(발주 gross, 발주일 KST) − ad_spend(Retail 계정단위, D-4) − "
            "cost(발주상세 per-SKU × cost_price[매핑, D-13]). ★커버리지<100%면 미매핑/미수집분 원가 누락으로 "
            "net_profit 과대 가능 → cost_coverage.coverage_pct 참조(원칙22). 매핑 0건이면 has_cost=false(원가 미반영)."
        ),
        "cost_coverage": cost_block,      # 커버리지·미해결 금액·미수집 PO(투명화)
        "drift": drift,
    }
