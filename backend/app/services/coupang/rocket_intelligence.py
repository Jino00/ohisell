# rocket_intelligence.py — 쿠팡 로켓배송(1P) 종합조망 편입 Harness (트랙 rocket-1p S4, D-11/D-12)
#
# 목적(D-11): 1P를 종합조망(Command Center) 돈 축에 3P/RG와 나란히 올린다. 1P는 PO그레인
#   (purchase_order_seq, vendor_item_id 없음)이라 기존 compute_command_center의 옵션그레인
#   by_option 병합이 불가 → 별도 채널 블록으로 산출(additive·읽기전용, 3P/RG net_profit 불변).
#
# 단일 책임 SA 3종 + 결합(원칙18-2):
#   ① _agg_rocket_revenue : Σ sum_of_order_amount(gross), 발주일 KST(po_created_at+9h) 윈도우 (매출 D-3)
#   ② _agg_rocket_ad      : coupang_ad_report sell_type='Retail'(로켓배송) 광고비 합 (D-4, 계정단위)
#   ③ _agg_rocket_drift   : 발주(gross) vs 정산(payment_amount), vendor_payment_seqs→distinct invoice 조인 (D-5)
#
# D-12: PO 61%가 multi-SKU라 PO그레인 원가분해 불가 → net_profit는 cost 미반영(has_cost=False)으로
#   정직 표기(원칙22). 정확한 원가는 발주상세(per-SKU SSR) 수집 후속 스프린트(S4.5).
# D-3/D-7: 시스템은 사실·지표·드리프트만 — 전략 추천 없음.
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    CoupangAdReport,
    CoupangRocketPurchaseOrder,
    CoupangRocketSettlement,
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
# 결합 — 1P 채널 블록
# ──────────────────────────────────────────────
def compute_rocket_overview(db: Session, dfrom: date, dto: date,
                            vendor_id: str | None = None) -> dict:
    """로켓배송(1P) 돈 축 종합조망 블록. 매출(발주)·광고·순이익(원가 미반영)·발주↔정산 드리프트.

    Decimal은 그대로 반환(라우터에서 str 직렬화). 읽기전용 — 3P/RG 종합조망 값 불변(별도 블록, D-11).
    net_profit = 매출(발주) − 광고비. ★cost 미반영(has_cost=False, D-12): PO그레인 원가분해 불가
    (61% multi-SKU) → 정확한 원가는 발주상세 수집 후속(S4.5). 원칙22: 미검증 net_profit 확정값처럼 금지.
    """
    rev = _agg_rocket_revenue(db, dfrom, dto, vendor_id)
    ad_spend = _agg_rocket_ad(db, dfrom, dto, vendor_id)
    drift = _agg_rocket_drift(db, dfrom, dto, rev["order_amount"], vendor_id)

    # net_profit = 매출 − 광고 (cost 미반영, D-12). 원가가 들어오면 − cost 추가.
    net_profit = rev["order_amount"] - ad_spend

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
        "cost": _Z,
        "has_cost": False,                # D-12: 원가 미반영(발주상세 수집 후속)
        "net_profit": net_profit,         # = 매출 − 광고 (원가 빠짐)
        "net_profit_basis": (
            "net_profit = revenue(발주 gross, 발주일 KST) − ad_spend(Retail). "
            "★cost 미반영(has_cost=false, D-12): PO 61% multi-SKU로 PO그레인 원가분해 불가 → "
            "정확한 원가는 발주상세(per-SKU) 수집 후속(S4.5). 광고는 Retail 계정단위(D-4)."
        ),
        "drift": drift,
    }
