# rg_fee_audit.py — RG 청구액 과오청구 감사 Harness (트랙 RG-Fee S8, D-17, 원칙 18-6 정보유통 허브)
# 단일 책임: 옵션별 (치수·실청구 배송/입출고비·판매수량)을 묶어 SA들로 이상치를 스크리닝한다.
# 흐름(정보 유통):
#   _load_dims(1회) ─────────┐
#   _load_fees(1회, 주기별)  ├─→ 옵션별 detect_fee_anomalies_by_period(SA3)
#   _load_qty_by_period(1회) ─┘      ←(판정주기 집계 1회 판정 → SA1 분류·SA2 floor 호출)
#                                     → 플래그 + 근거수치 행 + 주기별 상세, summary 집계
# ★단가의 분모는 **주문이 대응된 정산주기**만 쓴다(2026-08-03). 미대응 주기까지 합쳐 나누면
#   그 주기 청구액이 다른 주기의 주문으로 나뉘어 단가가 정수배로 부푼다 — 오탐 4건의 원인
#   (SA3 상단 실사고·LESSONS #99). 단 판정을 주기 1개로 쪼개지는 않는다 — 주기 안에서도 주문이
#   부분 수집돼 그 설계는 오탐을 3→20건으로 늘렸다. 판정=판정주기 집계, 표시=주기별.
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
) -> dict[str, dict[tuple[date, date], dict]]:
    """옵션ID → {(정산주기): {"fees": {fee_type: 금액}, "billed": {fee_type: 청구근거}}}.

    옵션 단위만. 기간은 매출인식일 기준. billed = 정산서 기재값(S9: 사이즈·주문수·수량).

    ★주기를 합치지 않고 그대로 내려보낸다(2026-08-03) — 어느 주기에 주문이 대응됐는지를 SA가
      알아야 미대응 주기를 분모에서 뺄 수 있기 때문이다. 여기서 미리 합치면 주문이 없는 주기의
      청구액이 다른 주기의 주문으로 나뉘어 단가가 정수배로 부풀고, 그게 '실측 vs 청구 불일치'
      오탐 4건의 원인이었다(SA3 상단 실사고).
    """
    q = db.query(
        CoupangRgSettlementFee.vendor_item_id,
        CoupangRgSettlementFee.recognition_date_from,
        CoupangRgSettlementFee.recognition_date_to,
        CoupangRgSettlementFee.fee_type,
        func.sum(CoupangRgSettlementFee.amount),
        # ★S9: 정산서가 기재한 청구 근거. 같은 grain에 1행이라 max()는 그 값 자체다
        #   (group_by에 넣으면 NULL 행이 갈려 집계가 쪼개진다).
        func.max(CoupangRgSettlementFee.billed_size_type),
        func.max(CoupangRgSettlementFee.billed_order_count),
        func.max(CoupangRgSettlementFee.billed_quantity),
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
    out: dict[str, dict[tuple[date, date], dict]] = {}
    for vii, rfrom, rto, ftype, total, b_size, b_orders, b_qty in q.all():
        per = out.setdefault(str(vii), {}).setdefault(
            (rfrom, rto), {"fees": {}, "billed": {}}
        )
        per["fees"][ftype] = float(total or 0)
        # ★분모는 부과 단위별로 따로 온다: 배송비=주문당(billed_order_count),
        #   입출고비=수량당(billed_quantity). 그래서 fee_type별로 보관한다.
        per["billed"][ftype] = {
            "size_type": b_size,
            "order_count": int(b_orders) if b_orders else None,
            "quantity": int(b_qty) if b_qty else None,
        }
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
    # 정산주기 목록을 먼저 뽑아 그 주기 안의 주문만 버킷팅한다(주기 밖 주문을 분모에 섞지 않음).
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
        billed_sizes: set[str] = set()
        divisor_sources: set[str] = set()
        for p in periods_by_option[vii]:
            ft = by_period[p]["fees"]
            billed = by_period[p]["billed"]
            dlv = ft.get("delivery")
            wh = ft.get("warehousing")
            if dlv is not None:
                charged_delivery = (charged_delivery or 0) + dlv
            if wh is not None:
                charged_warehousing = (charged_warehousing or 0) + wh
            # ★S9: 분모는 정산서 기재값이 1순위. 같은 파일·같은 basis라 조인도 추론도 없다.
            #   NULL(구 행·계정 row·컬럼 없는 파일)이면 종전 주문테이블 버킷으로 폴백한다.
            b_dlv = billed.get("delivery") or {}
            b_wh = billed.get("warehousing") or {}
            oq = per_option_qty.get(p, {})
            orders = b_dlv.get("order_count")
            quantity = b_wh.get("quantity") or b_dlv.get("quantity")
            if orders is not None or quantity is not None:
                divisor_sources.add("settlement")
            if orders is None:
                orders = oq.get("orders", 0)
                divisor_sources.add("order_table")
            if quantity is None:
                quantity = oq.get("qty", 0)
                divisor_sources.add("order_table")
            for b in (b_dlv, b_wh):
                if b.get("size_type"):
                    billed_sizes.add(b["size_type"])
            periods_in.append({
                "date_from": p[0].isoformat(), "date_to": p[1].isoformat(),
                "delivery": dlv, "warehousing": wh,
                "order_count": orders, "quantity": quantity,
            })
        # ★사이즈도 정산서 기재값이 1순위(청구에 실제로 쓰인 등급). 없으면 물류센터 실측, 없으면 등록치수.
        #   한 옵션에서 등급이 갈리면 "어느 등급으로 청구됐나"에 답이 없으므로 폴백한다.
        billed_size = next(iter(billed_sizes)) if len(billed_sizes) == 1 else None
        measured_size = coupang_sizes.get(vii)
        # SA에는 date 원본이 아니라 표시용 문자열이 들어가도 무해(판정에 날짜를 쓰지 않는다).
        anomaly = detect_fee_anomalies_by_period(
            d.get("width_mm"), d.get("length_mm"), d.get("height_mm"), d.get("weight_g"),
            periods=periods_in,
            coupang_size_type=billed_size or measured_size,
            size_source=("settlement_billed" if billed_size
                         else ("coupang_measured" if measured_size else "registered_dims")),
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
            # ★S9 근거 노출: 정산서가 기재한 청구 사이즈 / 물류센터 실측 / 분모 출처.
            #   셋을 나란히 보여야 "무엇과 무엇이 어긋났는가"를 사람이 직접 읽을 수 있다.
            "billed_size_type": billed_size,
            "measured_size_type": measured_size,
            "divisor_source": "+".join(sorted(divisor_sources)) or None,
            # 정산서 사이즈와 물류센터 실측이 갈리면 그건 추론이 아니라 **기재값 간 불일치**다.
            "billed_vs_measured_size_diff": bool(
                billed_size and measured_size and billed_size != measured_size),
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
            # 최상단 = 정산서 기재 사이즈와 청구액의 모순(추론이 하나도 안 들어간 신호, S9).
            0 if "billed_size_vs_amount_mismatch" in f else 1,
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
        # ★S9 최강 신호: 정산서가 적은 청구 사이즈와 청구액이 서로 안 맞는다(추론 0).
        "billed_size_vs_amount_mismatch": sum(
            1 for it in items if "billed_size_vs_amount_mismatch" in it["flags"]),
        # 정산서 기재 사이즈 ≠ 물류센터 실측 사이즈 — 기재값 간 불일치(둘 다 쿠팡 값).
        "billed_vs_measured_size_diff": sum(
            1 for it in items if it.get("billed_vs_measured_size_diff")),
        # 분모를 **전부** 정산서에서 얻은 옵션 수(=추론 0으로 판정된 옵션). 커버리지 진전 지표.
        "divisor_from_settlement": sum(
            1 for it in items if it.get("divisor_source") == "settlement"),
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
        # 집계 판정은 깨끗한데 특정 주기만 이상해 보이는 옵션 수 — 집계가 희석했을 수 있는
        # 국소 신호를 숨기지 않기 위한 참고값(주기 분모 결손 탓인 경우가 많다).
        "clean_but_period_outlier": sum(
            1 for it in items if not it["flags"] and it.get("periods_flagged")),
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
            "★S9(2026-08-03): 청구 사이즈·주문수·수량은 정산서 기재값을 1순위로 쓴다"
            "(divisor_source·size_source 확인). 정산서 값이 있으면 추론이 개입하지 않으므로 "
            "billed_size_vs_amount_mismatch는 정산서 내부 모순을 뜻한다. "
            "정산서 값이 없는 구 행은 종전 경로(주문테이블 버킷·치수 분류)로 폴백한다. "
            "최소금액 기준 스크리닝(레퍼런스 17 §7). 정확 청구액은 카테고리·판매가·합포장에 따라 "
            "달라지므로 플래그는 검토 신호이며 과오청구 확정이 아님(D-5). 청구 사이즈=물류센터 측정값. "
            "★단가는 **주문이 대응된 정산주기**만으로 산출한다(미대응 주기는 판정 제외, "
            "periods_unmatched). 판정은 그 주기들을 집계해 1회 내린다 — 주기 안에서도 주문이 "
            "부분 수집되므로 주기 단위 판정은 오탐을 늘린다(periods_flagged는 참고값). charged_delivery(전 주기 청구총액)를 order_count로 나누지 말 것 — "
            "그 나눗셈이 2026-08-03에 규명된 오탐 4건의 원인이었다. 단가의 분자는 judged_delivery다. "
            "주기 안에서 일부 주문만 수집된 경우는 여전히 단가가 부풀 수 있다(한계, 단정 금지)."
        ),
    }
