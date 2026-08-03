# rg_fee_audit.py — RG 청구액 과오청구 감사 Harness (트랙 RG-Fee S8, D-17, 원칙 18-6 정보유통 허브)
# 단일 책임: 옵션별 (치수·실청구 배송/입출고비·판매수량)을 묶어 SA들로 이상치를 스크리닝한다.
# 흐름(정보 유통):
#   _load_dims(1회) ─────────┐
#   _load_fees(1회, 주기별)  ├─→ 옵션별 detect_fee_anomalies_by_period(SA3)
#   _load_qty_by_period(1회) ─┘      ←(주기당 SA3 단일판정 → SA1 분류·SA2 floor 호출)
#                                     → 플래그 + 근거수치 행 + 주기별 상세, summary 집계
# ★판정 단위 = 정산주기 1개(2026-08-03). 주기를 합쳐 나누면 주문 미수집 주기의 청구액이 다른
#   주기의 주문으로 나뉘어 단가가 정수배로 부푼다 — 오탐 4건의 원인(SA3 상단 실사고·LESSONS #92).
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
from app.services.coupang.rg_fee_anomaly import detect_fee_anomalies_by_period
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
) -> dict[str, dict[tuple[date, date], dict[str, float]]]:
    """옵션ID → {(정산주기 시작, 끝): {fee_type: 금액}}. 옵션 단위만. 기간은 매출인식일 기준.

    ★주기를 합치지 않고 그대로 내려보낸다(2026-08-03) — 판정 단위가 정산주기 1개이기 때문이다.
      주기를 미리 합치면 주문이 없는 주기의 청구액이 다른 주기의 주문으로 나뉘어 단가가
      정수배로 부풀고, 그게 '실측 vs 청구 불일치' 오탐 4건의 원인이었다(SA3 상단 실사고).
    """
    q = db.query(
        CoupangRgSettlementFee.vendor_item_id,
        CoupangRgSettlementFee.recognition_date_from,
        CoupangRgSettlementFee.recognition_date_to,
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
        CoupangRgSettlementFee.vendor_item_id,
        CoupangRgSettlementFee.recognition_date_from,
        CoupangRgSettlementFee.recognition_date_to,
        CoupangRgSettlementFee.fee_type,
    )
    out: dict[str, dict[tuple[date, date], dict[str, float]]] = {}
    for vii, rfrom, rto, ftype, total in q.all():
        out.setdefault(str(vii), {}).setdefault((rfrom, rto), {})[ftype] = float(total or 0)
    return out


def _load_coupang_sizes(db: Session) -> dict[str, str]:
    """옵션ID → 쿠팡 실측 사이즈 등급. 배치 1회 조회(원칙 18-8).

    쿠팡이 물류센터에서 측정한 값이 과금 기준 → anomaly 판단 최우선.
    없으면 호출자가 등록 치수 기반 분류로 폴백.
    """
    rows = db.query(CoupangProductSize.vendor_item_id, CoupangProductSize.size_type).all()
    return {str(r[0]): r[1] for r in rows}


def _load_qty_by_period(
    db: Session,
    account_key: str | None,
    periods_by_option: dict[str, list[tuple[date, date]]],
) -> dict[str, dict[tuple[date, date], dict[str, int]]]:
    """옵션ID → {(주기): {qty, orders}}. 주문을 **정산주기 안으로만** 버킷팅한다.

    배송비는 합포장 시 주문당 1회(codex P2) → 배송 정규화는 orders, 입출고(수량당)는 qty 사용.

    ★조회범위(date_from/date_to)로 주문을 다시 자르지 않는다(2026-08-03). 범위는 **주기를**
      고르고, 고른 주기가 **그 주기의 주문을** 고른다. 주문을 범위로 또 자르면 경계에 걸친
      주기(overlap 포함 규칙, codex P2-2)에서 분자는 주기 전체·분모는 범위 안 주문만 남아
      같은 커버리지 불일치가 경계에서 재발한다.
    ★배치 1회 조회 후 파이썬 버킷팅(원칙 18-8) — 옵션×주기마다 서브쿼리를 돌리지 않는다.
    """
    all_periods = [p for ps in periods_by_option.values() for p in ps]
    if not all_periods:
        return {}
    lo = min(p[0] for p in all_periods)
    hi = max(p[1] for p in all_periods)

    q = db.query(
        CoupangRgOrderItem.vendor_item_id,
        CoupangRgOrderItem.order_id,
        CoupangRgOrderItem.paid_at,
        CoupangRgOrderItem.sales_quantity,
    ).filter(
        CoupangRgOrderItem.paid_at.isnot(None),
        func.date(CoupangRgOrderItem.paid_at) >= lo,
        func.date(CoupangRgOrderItem.paid_at) <= hi,
    )
    if account_key:
        q = q.filter(CoupangRgOrderItem.account_key == account_key)

    # 주기당 주문ID 집합(중복 라인아이템을 1주문으로) + 수량 합.
    buckets: dict[str, dict[tuple[date, date], dict]] = {}
    for vii, order_id, paid_at, sales_qty in q.all():
        vii = str(vii)
        periods = periods_by_option.get(vii)
        if not periods:
            continue
        d = paid_at.date() if hasattr(paid_at, "date") else paid_at
        for p in periods:
            if p[0] <= d <= p[1]:
                b = buckets.setdefault(vii, {}).setdefault(p, {"order_ids": set(), "qty": 0})
                b["order_ids"].add(order_id)
                b["qty"] += int(sales_qty or 0)
                break  # 주기는 서로 겹치지 않는다 — 겹쳐도 이중계상은 하지 않는다.
    return {
        vii: {p: {"qty": b["qty"], "orders": len(b["order_ids"])} for p, b in per.items()}
        for vii, per in buckets.items()
    }


def _judged_units(period_detail: list[dict], periods_in: list[dict]) -> dict:
    """판정에 실제로 쓰인 주문수·수량. 판정 주기가 없으면(조기종료 포함) 전 주기 합으로 폴백."""
    judged = [r for r in period_detail if r.get("judged")]
    src = judged or periods_in
    return {
        "quantity": sum(r["quantity"] for r in src) or None,
        "order_count": sum(r["order_count"] for r in src) or None,
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
    # 주기별 판정(ⓒ) — 정산주기 목록을 먼저 뽑아 그 주기 안의 주문만 버킷팅한다.
    periods_by_option = {vii: sorted(by_period.keys()) for vii, by_period in fees.items()}
    qty = _load_qty_by_period(db, account_key, periods_by_option)
    # 쿠팡 실측 사이즈 배치 로드 — 과금 기준이므로 anomaly 판단 최우선(원칙 18-8)
    coupang_sizes = _load_coupang_sizes(db)

    items: list[dict] = []
    for vii, by_period in fees.items():
        d = dims.get(vii, {})
        per_option_qty = qty.get(vii, {})
        periods_in: list[dict] = []
        charged_delivery = charged_warehousing = None
        for p in periods_by_option[vii]:
            ft = by_period[p]
            dlv = ft.get("delivery")
            wh = ft.get("warehousing")
            if dlv is not None:
                charged_delivery = (charged_delivery or 0) + dlv
            if wh is not None:
                charged_warehousing = (charged_warehousing or 0) + wh
            oq = per_option_qty.get(p, {})
            periods_in.append({
                "date_from": p[0].isoformat(), "date_to": p[1].isoformat(),
                "delivery": dlv, "warehousing": wh,
                "order_count": oq.get("orders", 0), "quantity": oq.get("qty", 0),
            })
        # SA에는 date 원본이 아니라 표시용 문자열이 들어가도 무해(판정에 날짜를 쓰지 않는다).
        anomaly = detect_fee_anomalies_by_period(
            d.get("width_mm"), d.get("length_mm"), d.get("height_mm"), d.get("weight_g"),
            periods=periods_in,
            coupang_size_type=coupang_sizes.get(vii),
        )
        items.append({
            "vendor_item_id": vii,
            "product_name": d.get("product_name"),
            "item_name": d.get("item_name") or vii,
            "width_mm": d.get("width_mm"), "length_mm": d.get("length_mm"),
            "height_mm": d.get("height_mm"), "weight_g": d.get("weight_g"),
            # ★청구 총액(전 주기) — 단가의 분자가 아니다. judged_delivery가 분자다.
            #   charged ÷ order_count를 다시 하면 오탐 4건이 그대로 되살아난다.
            "charged_delivery": charged_delivery,
            "charged_warehousing": charged_warehousing,
            # 판정에 쓰인 주문/수량(판정 주기 합) — charged 전 주기의 주문수가 아니다.
            # period_detail이 빈 조기종료(missing_dims·oversize)에선 전 주기 합으로 폴백해
            # 검토자가 원자료를 볼 수 있게 한다(판정에는 쓰이지 않는다).
            **_judged_units(anomaly["period_detail"], periods_in),
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
        # 커버리지 표면화(ⓑ) — 이상치가 아니므로 flags에 넣지 않고 별도로 센다.
        #   coverage_partial: 일부 주기만 판정됨(단가가 부분 표본).
        #   coverage_none:    판정 주기 0개 → unit_unknown으로 강등(판정 보류).
        "coverage_partial": sum(
            1 for it in items
            if it.get("periods_judged") and it.get("periods_unmatched")),
        "coverage_none": sum(
            1 for it in items
            if it.get("periods_total") and not it.get("periods_judged")),
    }
    log.info(
        "RG 청구 감사 (%s): %d옵션 중 %d플래그(실측불일치%d·추정불일치%d·하한미달%d)"
        " · 커버리지 부분%d·없음%d",
        account_key or "ALL", summary["total_options"], summary["flagged"],
        summary["measured_vs_billed_mismatch"], summary["size_mismatch_high"],
        summary["below_floor"], summary["coverage_partial"], summary["coverage_none"],
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
            "달라지므로 플래그는 검토 신호이며 과오청구 확정이 아님(D-5). 청구 사이즈=물류센터 측정값. "
            "★단가는 **정산주기별**로 산출하며 주문이 대응되지 않은 주기는 판정에서 제외한다"
            "(periods_unmatched). charged_delivery(전 주기 청구총액)를 order_count로 나누지 말 것 — "
            "그 나눗셈이 2026-08-03에 규명된 오탐 4건의 원인이었다. 단가의 분자는 judged_delivery다. "
            "주기 안에서 일부 주문만 수집된 경우는 여전히 단가가 부풀 수 있다(한계, 단정 금지)."
        ),
    }
