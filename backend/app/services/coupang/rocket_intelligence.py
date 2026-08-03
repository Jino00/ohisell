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
    return {
        "options": [
            {
                "option_id": str(r[0]),
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
