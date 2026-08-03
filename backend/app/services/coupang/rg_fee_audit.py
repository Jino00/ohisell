# rg_fee_audit.py — RG 청구액 과오청구 감사 Harness (트랙 RG-Fee S8, D-17, 원칙 18-6 정보유통 허브)
# 단일 책임: 옵션별 (치수·실청구 배송/입출고비·판매수량)을 묶어 SA들로 이상치를 스크리닝한다.
# 흐름(정보 유통):
#   _load_dims(1회) ─┐
#   _load_fees(1회)  ├─→ 옵션별 detect_fee_anomalies(SA3) ←(SA1 분류·SA2 floor 내부 호출)
#   _load_qty(1회)   ─┘     → 플래그 + 근거수치 행, summary 집계
# ★읽기 전용·net_profit 불변(D-17). 정확 청구액 판정 아님 — 사람 검토 신호(D-5).
# ★배치 효율(원칙 18-8): 치수·수량을 옵션ID로 1회씩 dict 적재 후 주입 → N×쿼리 방지.
# SA 직접 호출이 아니라 이 Harness를 라우터가 호출한다(원칙 18-7).
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    CoupangProductItem,
    CoupangProductSize,
    CoupangRgOrderItem,
    CoupangRgSettlementFee,
)
from app.services.coupang.rg_fee_anomaly import detect_fee_anomalies
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

# 감사 대상 비용 종류(사이즈 기반 청구). 보관·반품 등은 재고/이벤트 단위라 사이즈 감사 대상 아님.
_AUDITED_FEE_TYPES = ("delivery", "warehousing")


def _load_dims(db: Session, account_key: str | None) -> dict[str, dict]:
    """옵션ID → {치수·무게·상품명}. 1회 조회(원칙 18-8)."""
    q = db.query(
        CoupangProductItem.vendor_item_id,
        CoupangProductItem.width_mm,
        CoupangProductItem.length_mm,
        CoupangProductItem.height_mm,
        CoupangProductItem.weight_g,
        CoupangProductItem.seller_product_name,
        CoupangProductItem.item_name,
    )
    if account_key:
        q = q.filter(CoupangProductItem.account_key == account_key)
    out: dict[str, dict] = {}
    for r in q.all():
        out[str(r[0])] = {
            "width_mm": r[1], "length_mm": r[2], "height_mm": r[3], "weight_g": r[4],
            "product_name": r[5], "item_name": r[6],
        }
    return out


def _load_fees(
    db: Session, account_key: str | None, date_from: date | None, date_to: date | None
) -> dict[str, dict[str, float]]:
    """옵션ID → {fee_type: 합계금액}. 옵션 단위(vendor_item_id != '')만. 기간은 매출인식일 기준."""
    q = db.query(
        CoupangRgSettlementFee.vendor_item_id,
        CoupangRgSettlementFee.fee_type,
        func.sum(CoupangRgSettlementFee.amount),
    ).filter(
        CoupangRgSettlementFee.vendor_item_id != "",
        CoupangRgSettlementFee.fee_type.in_(_AUDITED_FEE_TYPES),
    )
    if account_key:
        q = q.filter(CoupangRgSettlementFee.account_key == account_key)
    # overlap 의미(codex P2): 정산주기가 조회범위에 일부라도 걸치면 포함(경계 row 누락 방지).
    if date_from:
        q = q.filter(CoupangRgSettlementFee.recognition_date_to >= date_from)
    if date_to:
        q = q.filter(CoupangRgSettlementFee.recognition_date_from <= date_to)
    q = q.group_by(
        CoupangRgSettlementFee.vendor_item_id, CoupangRgSettlementFee.fee_type
    )
    out: dict[str, dict[str, float]] = {}
    for vii, ftype, total in q.all():
        out.setdefault(str(vii), {})[ftype] = float(total or 0)
    return out


def _load_coupang_sizes(db: Session) -> dict[str, str]:
    """옵션ID → 쿠팡 실측 사이즈 등급. 배치 1회 조회(원칙 18-8).

    쿠팡이 물류센터에서 측정한 값이 과금 기준 → anomaly 판단 최우선.
    없으면 호출자가 등록 치수 기반 분류로 폴백.
    """
    rows = db.query(CoupangProductSize.vendor_item_id, CoupangProductSize.size_type).all()
    return {str(r[0]): r[1] for r in rows}


def _load_qty(
    db: Session, account_key: str | None, date_from: date | None, date_to: date | None
) -> dict[str, dict[str, int]]:
    """옵션ID → {qty: 판매수량 합, orders: 주문수(distinct order_id)}. paid_at 기준 필터.

    배송비는 합포장 시 주문당 1회(codex P2) → 배송 정규화는 orders, 입출고(수량당)는 qty 사용.
    """
    q = db.query(
        CoupangRgOrderItem.vendor_item_id,
        func.sum(CoupangRgOrderItem.sales_quantity),
        func.count(func.distinct(CoupangRgOrderItem.order_id)),
    )
    if account_key:
        q = q.filter(CoupangRgOrderItem.account_key == account_key)
    if date_from:
        q = q.filter(func.date(CoupangRgOrderItem.paid_at) >= date_from)
    if date_to:
        q = q.filter(func.date(CoupangRgOrderItem.paid_at) <= date_to)
    q = q.group_by(CoupangRgOrderItem.vendor_item_id)
    return {
        str(vii): {"qty": int(qty or 0), "orders": int(orders or 0)}
        for vii, qty, orders in q.all()
    }


def build_fee_audit(
    db: Session,
    account_key: str | None = None,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """옵션별 RG 청구액 이상치 감사. 반환: {generated_at·account_key·기간·summary·items}.

    옵션 단위 정산 비용(delivery/warehousing)이 있는 옵션만 감사. 치수·수량을 주입해 SA3 실행.
    읽기 전용·net_profit 불변(D-17). 플래그는 사람 검토 신호이지 과오청구 단정 아님(D-5).
    """
    dims = _load_dims(db, account_key)
    fees = _load_fees(db, account_key, date_from, date_to)
    qty = _load_qty(db, account_key, date_from, date_to)
    # 쿠팡 실측 사이즈 배치 로드 — 과금 기준이므로 anomaly 판단 최우선(원칙 18-8)
    coupang_sizes = _load_coupang_sizes(db)

    items: list[dict] = []
    for vii, fee_by_type in fees.items():
        d = dims.get(vii, {})
        delivery = fee_by_type.get("delivery")
        warehousing = fee_by_type.get("warehousing")
        oq = qty.get(vii, {})
        q = oq.get("qty")
        orders = oq.get("orders")
        anomaly = detect_fee_anomalies(
            d.get("width_mm"), d.get("length_mm"), d.get("height_mm"), d.get("weight_g"),
            delivery_amount=delivery, warehousing_amount=warehousing,
            quantity=q, order_count=orders,
            coupang_size_type=coupang_sizes.get(vii),
        )
        items.append({
            "vendor_item_id": vii,
            "product_name": d.get("product_name"),
            "item_name": d.get("item_name") or vii,
            "width_mm": d.get("width_mm"), "length_mm": d.get("length_mm"),
            "height_mm": d.get("height_mm"), "weight_g": d.get("weight_g"),
            "charged_delivery": delivery,
            "charged_warehousing": warehousing,
            "quantity": q,
            "order_count": orders,
            **anomaly,
        })

    # 플래그 있는 항목 먼저(검토 우선순위). 최상단은 measured_vs_billed_mismatch —
    #   실측값 자체와 청구가 어긋난 것이라 등록치수 기준 추정(size_mismatch_high)보다 강한 신호다.
    def _sort_key(it: dict) -> tuple:
        f = it["flags"]
        return (
            0 if "measured_vs_billed_mismatch" in f else 1,
            0 if "size_mismatch_high" in f else 1,
            0 if f else 1,
            -(it.get("per_unit_delivery") or 0),
        )

    items.sort(key=_sort_key)

    flagged = [it for it in items if it["flags"]]
    summary = {
        "total_options": len(items),
        "flagged": len(flagged),
        "size_mismatch_high": sum(1 for it in items if "size_mismatch_high" in it["flags"]),
        # 실측값 자체와 청구가 어긋난 건수 — 등록치수 기준 추정보다 강한 신호(2026-08-03 신설).
        "measured_vs_billed_mismatch": sum(
            1 for it in items if "measured_vs_billed_mismatch" in it["flags"]),
        "below_floor": sum(1 for it in items if "below_floor" in it["flags"]),
        "missing_dims": sum(1 for it in items if "missing_dims" in it["flags"]),
        "unit_unknown": sum(1 for it in items if "unit_unknown" in it["flags"]),
        "oversize": sum(1 for it in items if "oversize" in it["flags"]),
    }
    log.info(
        "RG 청구 감사 (%s): %d옵션 중 %d플래그(실측불일치%d·추정불일치%d·하한미달%d)",
        account_key or "ALL", summary["total_options"], summary["flagged"],
        summary["measured_vs_billed_mismatch"], summary["size_mismatch_high"],
        summary["below_floor"],
    )
    return {
        "generated_at": kst_today().isoformat(),
        "account_key": account_key,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "summary": summary,
        "items": items,
        "disclaimer": (
            "최소금액 기준 스크리닝(레퍼런스 17 §7). 정확 청구액은 카테고리·판매가·합포장에 따라 "
            "달라지므로 플래그는 검토 신호이며 과오청구 확정이 아님(D-5). 청구 사이즈=물류센터 측정값."
        ),
    }
