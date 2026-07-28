# rocket_recon.py — 로켓배송(1P) 통합 대사(발주↔납품↔거래명세서↔계산서) 조회 Harness.
#
# 목적(Jino 원문): "상품별 발주내역, 납품 내역, 거래명세서 발행 내역, 계산서 발행내역을
#   한 화면에서 볼 수 있게" — 네 축이 서로 다른 그레인에 흩어져 있어 지금까지 대조가 불가능했다.
#     · 발주/납품/명세서 단계 = PO 그레인 (coupang_rocket_purchase_order)
#     · 상품(SKU)별 발주/납품가능 = 발주상세 그레인 (coupang_rocket_purchase_order_item)
#     · 계산서 = invoice 그레인 (coupang_rocket_settlement), PO.vendor_payment_seqs로 연결
#   이 모듈이 셋을 **상품(SKU) 기준**으로 접어서 한 테이블로 만든다. 읽기 전용 — 회계·수집 무변경.
#
# 단일 책임 SA(원칙18-1) 4종 + 결합 2:
#   ① _agg_by_status   : PO 그레인 상태별 발주/입고 수량·금액·드리프트 건수
#   ② _agg_invoices    : 윈도우 PO → vendor_payment_seqs → 계산서 확정/전송 상태 집계
#   ③ _sku_aggregate   : 발주상세 라인 → SKU별 발주·납품가능·(귀속 가능분)입고
#   ④ _po_lines_for_sku: 한 SKU가 속한 PO 목록 + 라인 + 연결 계산서 (행 확장용)
#   compute_rocket_recon / compute_rocket_recon_sku 가 결합(Harness).
#
# ★원칙22(0으로 접지 않기) — 이 화면의 핵심 정직성 3가지:
#   1) **SKU별 입고수량은 원천에 없다.** 입고수량(receiving_qty)은 PO 그레인에만 있고,
#      멀티SKU PO(전체의 64%)는 SKU로 쪼갤 근거가 없다. 그래서 입고는 **단일SKU PO에서만**
#      귀속하고, 나머지는 received_unattributable_po_count로 드러낸다. 안분(按分) 추정 금지.
#   2) **발주상세(SKU 그레인)는 수집 시작 시점 이후분만 있다.** 그 전 PO는 PO 그레인 수량만
#      존재 → SKU 테이블에 아예 안 나온다. summary.detail_coverage로 얼마가 빠졌는지 명시한다.
#   3) **미수집 = 0이 아니다.** 납품가능수량(vendor_confirmed_qty)이 NULL인 라인은 0으로 더하지
#      않고 confirmed_missing_lines로 센다. 계산서도 seq는 있는데 정산행이 없으면 found=false.
#
# ★드리프트의 의미는 상태에 따라 다르다: 입고 전 단계(발주확정·거래처확인요청)에서 발주≠입고는
#   당연하다(아직 안 들어옴). 진짜 신호는 **거래명세서확인(CI) 단계인데도 발주≠입고**인 건이다.
#   그래서 drift_po_count(전체)와 drift_po_count_settled_stage(CI)를 분리해서 낸다.
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models import (
    CoupangRocketPurchaseOrder,
    CoupangRocketPurchaseOrderItem,
    CoupangRocketSettlement,
)
from app.services.coupang.rocket_intelligence import _kst_window_utc

log = logging.getLogger(__name__)

_Z = Decimal("0")
_Q4 = Decimal("0.0001")
_KST = timedelta(hours=9)

# 입고가 끝난 단계 = 발주≠입고가 "진짜 신호"인 단계. 라이브 실측 상태 **4종**(2026-07-28,
#   coupang_rocket_purchase_order 651건 전수 — 입고율 = Σreceiving_qty/Σorder_qty):
#     RI 거래명세서확인요청  2건  입고율 0.993  ← 입고 끝남
#     CI 거래명세서확인    578건  입고율 0.910  ← 입고 끝남
#     PA 발주확정           58건  입고율 0.190  ← 입고 전/중
#     RP 거래처확인요청     13건  입고율 0.000  ← 입고 전
# ★CI와 RI를 "입고 완료"로 본다. RI는 거래명세서 확인을 **요청**한 단계라 이름만 앞서 보이지만,
#   실측 입고율이 CI보다 높다(0.993 > 0.910) — 물건은 이미 들어온 뒤다. RI를 빼면 그 단계의
#   발주≠입고가 "입고 전이라 당연한 불일치" 회색 처리로 묻힌다(실제 PO 134001752: 212→210,
#   납품가능수량도 210으로 확정된 진짜 미입고 2개). 추정이 아니라 전수 실측에 근거한 편입이다.
# ★여기 없는 새 상태 코드는 '진행 중'으로 분류된다(추정 금지) — 관측되면 입고율을 재고 후 추가한다.
SETTLED_STAGE_STATUSES = frozenset({"CI", "RI"})


def _f(v) -> Decimal:
    """None/숫자 → Decimal. 집계(func.sum)가 None(행 없음)이면 0."""
    if v is None:
        return _Z
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _ratio4(num: Decimal, den: Decimal) -> "Decimal | None":
    """비율 = num/den(4자리 quantize, 분수 0~1). den 0이면 None(0 나눗셈·가짜 0% 방지)."""
    if not den:
        return None
    return (num / den).quantize(_Q4)


def _kst_date_str(dt) -> "str | None":
    """UTC naive datetime → KST 날짜 문자열(YYYY-MM-DD). None이면 None(미상 유지)."""
    if dt is None:
        return None
    return (dt + _KST).date().isoformat()


def _window_po_query(db: Session, dfrom: date, dto: date, vendor_id: str | None):
    """발주일 KST 윈도우 PO 쿼리(공통 필터). rocket_intelligence와 같은 윈도우 계약을 재사용."""
    start, end = _kst_window_utc(dfrom, dto)
    q = db.query(CoupangRocketPurchaseOrder).filter(
        CoupangRocketPurchaseOrder.po_created_at >= start,
        CoupangRocketPurchaseOrder.po_created_at <= end,
    )
    if vendor_id is not None:
        q = q.filter(CoupangRocketPurchaseOrder.vendor_id == vendor_id)
    return q


# ──────────────────────────────────────────────
# ① 상태별 발주/입고 (PO 그레인)
# ──────────────────────────────────────────────
def _agg_by_status(db: Session, dfrom: date, dto: date, vendor_id: str | None) -> dict:
    """윈도우 PO를 진행 상태별로 집계 — 발주/입고 수량·금액 + 발주≠입고 건수.

    반환 totals는 화면 상단 요약 타일의 원천이다. drift_po_count_settled_stage(거래명세서확인
    단계인데 발주≠입고)가 이 화면의 핵심 값 — 입고 전 단계의 당연한 불일치와 섞이지 않는다.
    """
    PO = CoupangRocketPurchaseOrder
    start, end = _kst_window_utc(dfrom, dto)
    drift_expr = case((PO.order_qty != PO.receiving_qty, 1), else_=0)

    q = (
        db.query(
            PO.purchase_order_status,
            PO.purchase_order_status_description,
            func.count(PO.id),
            func.coalesce(func.sum(PO.order_qty), 0),
            func.coalesce(func.sum(PO.receiving_qty), 0),
            func.coalesce(func.sum(PO.sum_of_order_amount), 0),
            func.coalesce(func.sum(PO.sum_of_receiving_amount), 0),
            func.coalesce(func.sum(drift_expr), 0),
        )
        .filter(PO.po_created_at >= start, PO.po_created_at <= end)
        .group_by(PO.purchase_order_status, PO.purchase_order_status_description)
    )
    if vendor_id is not None:
        q = q.filter(PO.vendor_id == vendor_id)

    by_status: list[dict] = []
    t_po = t_oq = t_rq = t_drift = 0
    t_oa = t_ra = _Z
    settled_drift = 0
    settled_po = 0
    for status, desc, cnt, oq, rq, oa, ra, drift in q.all():
        is_settled_stage = status in SETTLED_STAGE_STATUSES
        by_status.append({
            "status": status,
            "status_description": desc,
            "is_settled_stage": is_settled_stage,  # True=입고 끝난 단계(드리프트가 진짜 신호)
            "po_count": int(cnt or 0),
            "order_qty": int(oq or 0),
            "received_qty": int(rq or 0),
            "order_amount": _f(oa),
            "receiving_amount": _f(ra),
            "drift_po_count": int(drift or 0),
        })
        t_po += int(cnt or 0)
        t_oq += int(oq or 0)
        t_rq += int(rq or 0)
        t_oa += _f(oa)
        t_ra += _f(ra)
        t_drift += int(drift or 0)
        if is_settled_stage:
            settled_drift += int(drift or 0)
            settled_po += int(cnt or 0)

    by_status.sort(key=lambda r: r["po_count"], reverse=True)

    # 발주일 미상(po_created_at NULL) PO — 어떤 윈도우에도 안 들어간다(누락 투명화).
    nd_q = db.query(func.count(PO.id)).filter(PO.po_created_at.is_(None))
    if vendor_id is not None:
        nd_q = nd_q.filter(PO.vendor_id == vendor_id)

    return {
        "po_count": t_po,
        "order_qty": t_oq,
        "received_qty": t_rq,
        "unreceived_qty": t_oq - t_rq,          # 양수 = 아직 안 들어온 수량(진행 중 포함)
        "order_amount": t_oa,
        "receiving_amount": t_ra,
        "unreceived_amount": t_oa - t_ra,
        "drift_po_count": t_drift,              # 전체 — 입고 전 단계의 당연한 불일치 포함
        "drift_po_count_settled_stage": settled_drift,  # ★핵심: 거래명세서확인인데 발주≠입고
        "settled_stage_po_count": settled_po,
        "no_date_po_count": int(nd_q.scalar() or 0),
        "by_status": by_status,
    }


# ──────────────────────────────────────────────
# ② 계산서 (PO.vendor_payment_seqs → settlement)
# ──────────────────────────────────────────────
def _collect_invoice_seqs(pos) -> tuple[dict[int, list[int]], int]:
    """PO 행들 → {purchase_order_seq: [invoice_seq…]} 와 계산서 미연결 PO 수.

    vendor_payment_seqs는 JSON 리스트(1PO↔N계산서). NULL/[] = 아직 계산서에 안 묶임.
    """
    mapping: dict[int, list[int]] = {}
    without = 0
    for po in pos:
        seqs: list[int] = []
        for s in (po.vendor_payment_seqs or []):
            try:
                seqs.append(int(s))
            except (TypeError, ValueError):
                log.warning("rocket_recon: 계산서번호 파싱 실패 po=%s raw=%r", po.purchase_order_seq, s)
        mapping[po.purchase_order_seq] = seqs
        if not seqs:
            without += 1
    return mapping, without


def _load_settlements(db: Session, invoice_seqs: set[int]) -> dict[int, CoupangRocketSettlement]:
    """계산서번호 집합 → {invoice_seq: 정산행}. 없는 번호는 키 자체가 없다(=미수집, 0 아님)."""
    if not invoice_seqs:
        return {}
    # 발주상세 조회와 같은 이유로 청크 분할 — 윈도우는 사용자 입력이라 상한이 없고,
    # 계산서번호는 PO당 ~1개라 긴 기간이면 IN 목록이 그대로 PO 수만큼 커진다(SQLite 변수 한계).
    seqs = sorted(invoice_seqs)
    out: dict[int, CoupangRocketSettlement] = {}
    for i in range(0, len(seqs), 500):
        rows = (
            db.query(CoupangRocketSettlement)
            .filter(CoupangRocketSettlement.invoice_seq.in_(seqs[i:i + 500]))
            .all()
        )
        out.update({r.invoice_seq: r for r in rows})
    return out


def _agg_invoices(mapping: dict[int, list[int]], po_without_invoice: int,
                  settlements: dict[int, CoupangRocketSettlement]) -> dict:
    """계산서 발행 상태 집계 — 확정일 미기재·전송 미표기·정산행 미수집을 각각 따로 센다.

    ★tax_invoice_transmitted가 False라고 '전송 실패'로 읽지 않는다(모델 docstring):
      확정 전에는 상태 텍스트가 없어 False로 파싱된다. 그래서 이름을 not_marked(미표기)로 둔다.
    """
    seqs: set[int] = set()
    for v in mapping.values():
        seqs.update(v)

    unconfirmed = 0   # 세금계산서 확정일 없음
    not_marked = 0    # 전송성공 미표기(False 또는 None) — '실패' 아님
    missing = 0       # 계산서번호는 있는데 정산행 미수집
    settled_amount = _Z
    for s in sorted(seqs):
        row = settlements.get(s)
        if row is None:
            missing += 1
            continue
        settled_amount += _f(row.payment_amount)
        if row.tax_invoice_confirmed_date is None:
            unconfirmed += 1
        if row.tax_invoice_transmitted is not True:
            not_marked += 1

    return {
        "po_without_invoice_count": po_without_invoice,   # 아직 계산서에 안 묶인 발주
        "mapped_invoice_count": len(seqs),
        "invoice_missing_row_count": missing,             # 번호는 있으나 정산 미수집(0 아님)
        "invoice_unconfirmed_count": unconfirmed,         # 세금계산서 확정일 없음
        "invoice_not_marked_transmitted_count": not_marked,
        "settled_amount": settled_amount,
        "note": (
            "계산서는 PO.vendor_payment_seqs로 연결(1PO↔N계산서·1계산서↔N PO). "
            "전송 '미표기'는 실패가 아니다 — 확정 전에는 상태 텍스트 자체가 없다. "
            "settled_amount는 윈도우 PO에 매핑된 distinct 계산서 지급예정 합(윈도우 밖 PO 몫 포함 가능)."
        ),
    }


# ──────────────────────────────────────────────
# ③ SKU 집계 (발주상세 라인 → 상품 기준)
# ──────────────────────────────────────────────
def _sku_aggregate(db: Session, po_by_seq: dict[int, CoupangRocketPurchaseOrder],
                   invoice_map: dict[int, list[int]],
                   settlements: dict[int, CoupangRocketSettlement]) -> tuple[list[dict], dict]:
    """윈도우 PO들의 발주상세 라인을 SKU(product_number)로 접는다.

    반환 (rows, coverage).
    수량 3종의 출처가 서로 다르다 — 섞지 않는다:
      · order_qty      = 라인 발주수량 합            (SKU 정확)
      · confirmed_qty  = 업체납품가능수량 합          (SKU 정확, 미수집 라인은 NULL로 제외)
      · received_qty   = **단일SKU PO의 PO 입고수량**만 (멀티SKU PO는 SKU로 못 쪼갠다 → 미귀속)
    drift_qty = 귀속 가능분 발주 − 귀속 가능분 입고. 귀속 가능 PO가 0이면 None(0 아님).

    파이썬 집계인 이유: 발주상세는 윈도우당 수백~수천 행(라이브 4개월 전체 1,207행)이라
    그룹 쿼리 여러 번보다 1회 스캔이 단순하고, 미귀속/미수집 분기를 SQL로 짜면 오히려 안 읽힌다.
    """
    seqs = list(po_by_seq.keys())
    lines: list[CoupangRocketPurchaseOrderItem] = []
    if seqs:
        # SQLite 변수 한계(999) 회피 — 청크로 나눠 조회.
        for i in range(0, len(seqs), 500):
            lines.extend(
                db.query(CoupangRocketPurchaseOrderItem)
                .filter(CoupangRocketPurchaseOrderItem.purchase_order_seq.in_(seqs[i:i + 500]))
                .all()
            )

    # PO별 수집된 라인 수 — 단일SKU 귀속 판정에 쓴다.
    lines_per_po: dict[int, int] = {}
    for ln in lines:
        lines_per_po[ln.purchase_order_seq] = lines_per_po.get(ln.purchase_order_seq, 0) + 1

    def _attributable(po_seq: int) -> bool:
        """이 PO의 입고수량을 SKU 하나에 통째로 귀속해도 되는가.

        PO가 선언한 sku_count==1 **이고** 실제 수집 라인도 1개일 때만 참. 둘 중 하나라도
        어긋나면(멀티SKU거나 라인 일부만 수집) 귀속하지 않는다 — 안분 추정 금지(원칙22).
        """
        po = po_by_seq.get(po_seq)
        return bool(po and po.sku_count == 1 and lines_per_po.get(po_seq) == 1)

    agg: dict[str, dict] = {}
    for ln in lines:
        po = po_by_seq.get(ln.purchase_order_seq)
        if po is None:
            continue  # 윈도우 밖(방어) — 상단 필터상 발생하지 않음
        r = agg.get(ln.product_number)
        if r is None:
            r = agg[ln.product_number] = {
                "product_number": ln.product_number,
                "product_name": None,
                "barcode": None,
                "po_seqs": set(),
                "order_qty": 0,
                "order_amount": _Z,
                "confirmed_qty": 0,
                "confirmed_missing_lines": 0,
                "line_count": 0,
                "_attr_po_seqs": set(),
                "_unattr_po_seqs": set(),
            }
        if r["product_name"] is None and ln.product_name:
            r["product_name"] = ln.product_name
        if r["barcode"] is None and ln.barcode:
            r["barcode"] = ln.barcode
        r["po_seqs"].add(ln.purchase_order_seq)
        r["line_count"] += 1
        r["order_qty"] += int(ln.order_qty or 0)
        r["order_amount"] += _f(ln.line_order_amount)
        if ln.vendor_confirmed_qty is None:
            r["confirmed_missing_lines"] += 1   # ★0으로 더하지 않는다
        else:
            r["confirmed_qty"] += int(ln.vendor_confirmed_qty)
        if _attributable(ln.purchase_order_seq):
            r["_attr_po_seqs"].add(ln.purchase_order_seq)
        else:
            r["_unattr_po_seqs"].add(ln.purchase_order_seq)

    rows: list[dict] = []
    for r in agg.values():
        attr_seqs = r["_attr_po_seqs"]
        received = sum(int(po_by_seq[s].receiving_qty or 0) for s in attr_seqs)
        attr_order = sum(int(po_by_seq[s].order_qty or 0) for s in attr_seqs)
        # ★단계별 드리프트 — 요약 타일의 drift_po_count / drift_po_count_settled_stage와 같은 가름.
        #   입고 전 단계(PA·RP) 발주는 입고 0이 정상이라 그 차이를 '드리프트'로 칠하면 오탐이 된다.
        #   진짜 신호는 입고가 끝난 단계(CI·RI)의 귀속분 발주≠입고뿐이다.
        settled_seqs = {s for s in attr_seqs
                        if po_by_seq[s].purchase_order_status in SETTLED_STAGE_STATUSES}
        settled_received = sum(int(po_by_seq[s].receiving_qty or 0) for s in settled_seqs)
        settled_order = sum(int(po_by_seq[s].order_qty or 0) for s in settled_seqs)
        # ★수량 순합은 상쇄된다 — 초과입고 PO와 미입고 PO가 섞이면 합이 0이 되어 "불일치 없음"으로
        #   보인다. 요약 타일은 **건수**를 세므로 그쪽엔 잡히고 여기만 사라지는 어긋남이 생긴다.
        #   그래서 건수도 함께 낸다(요약과 같은 단위).
        settled_drift_pos = sum(
            1 for s in settled_seqs
            if int(po_by_seq[s].order_qty or 0) != int(po_by_seq[s].receiving_qty or 0)
        )
        # 계산서 상태 — **이 SKU가 속한 PO들** 기준(윈도우 전체가 아니다).
        # ★행 배지의 합 ≠ summary.invoice 타일: 1계산서가 멀티SKU 발주에 걸리면 그 계산서가
        #   SKU마다 한 번씩 잡힌다(inv_seen은 SKU 안에서만 중복 제거). summary는 윈도우 전체
        #   distinct라 항상 더 작다. 같은 이름의 po_without_invoice_count도 분모가 다르다
        #   (여기=이 SKU가 속한 PO 중 / summary=윈도우 전체 PO 중). 화면 각주로도 고지한다.
        no_inv = unconf = not_marked = miss = 0
        inv_seen: set[int] = set()
        for s in r["po_seqs"]:
            inv = invoice_map.get(s) or []
            if not inv:
                no_inv += 1
            for iseq in inv:
                if iseq in inv_seen:
                    continue
                inv_seen.add(iseq)
                srow = settlements.get(iseq)
                if srow is None:
                    miss += 1
                    continue
                if srow.tax_invoice_confirmed_date is None:
                    unconf += 1
                if srow.tax_invoice_transmitted is not True:
                    not_marked += 1

        rows.append({
            "product_number": r["product_number"],
            "product_name": r["product_name"],
            "barcode": r["barcode"],
            "po_count": len(r["po_seqs"]),
            "line_count": r["line_count"],
            "order_qty": r["order_qty"],
            "order_amount": r["order_amount"],
            # 납품가능(업체확인) — 미수집 라인은 합에서 빠졌고 그 수를 함께 낸다.
            "confirmed_qty": r["confirmed_qty"],
            "confirmed_missing_lines": r["confirmed_missing_lines"],
            # 입고 — 단일SKU PO 귀속분만. 귀속 PO 0건이면 None(화면에서 "—").
            "received_qty": received if attr_seqs else None,
            "received_attributable_po_count": len(attr_seqs),
            "received_unattributable_po_count": len(r["_unattr_po_seqs"]),
            "attributable_order_qty": attr_order if attr_seqs else None,
            # 전체 귀속분 차이 — 입고 전 단계를 포함하므로 그 자체로는 '문제'가 아니다(참고값).
            "drift_qty": (attr_order - received) if attr_seqs else None,
            # ★진짜 신호 — 입고 완료 단계(CI·RI) 귀속분만. 해당 PO 0건이면 None(0 아님).
            "drift_qty_settled_stage": (settled_order - settled_received) if settled_seqs else None,
            # 수량이 상쇄돼 0이어도 건수가 >0이면 불일치는 실재한다(요약 타일과 같은 단위).
            "drift_po_count_settled_stage": settled_drift_pos,
            "settled_stage_attributable_po_count": len(settled_seqs),
            "invoice_count": len(inv_seen),
            "po_without_invoice_count": no_inv,
            "invoice_missing_row_count": miss,
            "invoice_unconfirmed_count": unconf,
            "invoice_not_marked_transmitted_count": not_marked,
        })

    rows.sort(key=lambda x: (x["order_amount"], x["order_qty"]), reverse=True)

    pos_with_detail = len(lines_per_po)
    coverage = {
        "pos_with_detail_count": pos_with_detail,
        "pos_without_detail_count": max(0, len(po_by_seq) - pos_with_detail),
        "po_coverage_pct": _ratio4(Decimal(pos_with_detail), Decimal(len(po_by_seq)))
        if po_by_seq else None,
        "line_count": len(lines),
        "sku_count": len(agg),
        "lines_missing_confirmed_qty": sum(1 for ln in lines if ln.vendor_confirmed_qty is None),
        "note": (
            "발주상세(SKU 그레인)는 수집 시작 이후 PO만 존재한다 — 그 전 발주는 PO 그레인 수량만 "
            "있어 아래 상품 테이블에 나오지 않는다(요약 타일은 PO 그레인이라 전부 포함). "
            "SKU별 입고수량은 원천에 없어 단일SKU PO에서만 귀속한다 — 멀티SKU PO는 미귀속으로 남긴다."
        ),
    }
    return rows, coverage


# ──────────────────────────────────────────────
# ④ 한 SKU의 PO 목록 (행 확장용)
# ──────────────────────────────────────────────
def _serialize_invoice(iseq: int, row: "CoupangRocketSettlement | None") -> dict:
    """계산서 1건 직렬화. 정산행 미수집이면 found=False + 값 전부 None(0으로 접지 않는다)."""
    if row is None:
        return {
            "invoice_seq": iseq,
            "found": False,
            "issue_date": None,
            "payment_date": None,
            "tax_invoice_confirmed_date": None,
            "tax_invoice_transmitted": None,
            "payment_amount": None,
            "supply_amount": None,
            "vat": None,
            "bill_issue_type": None,
            "settlement_type": None,
        }
    return {
        "invoice_seq": iseq,
        "found": True,
        "issue_date": row.issue_date.isoformat() if row.issue_date else None,
        "payment_date": row.payment_date.isoformat() if row.payment_date else None,
        "tax_invoice_confirmed_date": (
            row.tax_invoice_confirmed_date.isoformat() if row.tax_invoice_confirmed_date else None
        ),
        "tax_invoice_transmitted": row.tax_invoice_transmitted,  # True/False/None(미표기≠실패)
        "payment_amount": _f(row.payment_amount),
        "supply_amount": _f(row.supply_amount),
        "vat": _f(row.vat),
        "bill_issue_type": row.bill_issue_type,
        "settlement_type": row.settlement_type,
    }


def _po_lines_for_sku(db: Session, product_number: str,
                      po_by_seq: dict[int, CoupangRocketPurchaseOrder],
                      invoice_map: dict[int, list[int]],
                      settlements: dict[int, CoupangRocketSettlement]) -> list[dict]:
    """이 SKU가 들어간 윈도우 PO들 — 발주/입고(PO 그레인) + 이 SKU 라인 + 연결 계산서."""
    seqs = list(po_by_seq.keys())
    if not seqs:
        return []
    lines: list[CoupangRocketPurchaseOrderItem] = []
    for i in range(0, len(seqs), 500):
        lines.extend(
            db.query(CoupangRocketPurchaseOrderItem)
            .filter(
                CoupangRocketPurchaseOrderItem.product_number == product_number,
                CoupangRocketPurchaseOrderItem.purchase_order_seq.in_(seqs[i:i + 500]),
            )
            .all()
        )

    out: list[dict] = []
    for ln in lines:
        po = po_by_seq.get(ln.purchase_order_seq)
        if po is None:
            continue
        invoices = [_serialize_invoice(s, settlements.get(s))
                    for s in (invoice_map.get(po.purchase_order_seq) or [])]
        out.append({
            "product_name": ln.product_name,
            "purchase_order_seq": po.purchase_order_seq,
            "po_created_date": _kst_date_str(po.po_created_at),      # 발주일(KST)
            "expected_delivery_date": _kst_date_str(po.expected_delivery_date),
            "status": po.purchase_order_status,
            "status_description": po.purchase_order_status_description,  # 명세서 단계 포함
            "is_settled_stage": po.purchase_order_status in SETTLED_STAGE_STATUSES,
            "center_name": po.center_name,
            "purchase_type": po.purchase_type,
            "sku_count": po.sku_count,
            # PO 그레인(권위값) — 이 PO 전체의 발주/입고
            "po_order_qty": po.order_qty,
            "po_received_qty": po.receiving_qty,
            "po_order_amount": _f(po.sum_of_order_amount),
            "po_receiving_amount": _f(po.sum_of_receiving_amount),
            "po_drift_qty": (po.order_qty or 0) - (po.receiving_qty or 0),
            # 이 SKU 라인 — 입고는 SKU로 안 쪼개지므로 라인엔 없다(위 PO 값으로 읽는다)
            "line_order_qty": ln.order_qty,
            "line_confirmed_qty": ln.vendor_confirmed_qty,   # None=미수집(0 아님)
            "line_order_amount": _f(ln.line_order_amount),
            "unit_purchase_price": _f(ln.unit_purchase_price),
            "invoices": invoices,
        })
    out.sort(key=lambda r: (r["po_created_date"] or "", r["purchase_order_seq"]), reverse=True)
    return out


# ──────────────────────────────────────────────
# 결합 (Harness)
# ──────────────────────────────────────────────
def compute_rocket_recon(db: Session, dfrom: date, dto: date,
                         vendor_id: str | None = None,
                         drift_only: bool = False,
                         unconfirmed_only: bool = False) -> dict:
    """로켓 1P 통합 대사 — 요약(PO 그레인) + 상품(SKU) 테이블. 읽기 전용.

    요약 타일은 **PO 그레인**이라 윈도우 발주 전체를 담고, 상품 테이블은 **발주상세 그레인**이라
    발주상세가 수집된 PO만 담는다. 둘의 합계는 일부러 다르다 — detail_coverage가 그 차이다.
    필터(drift_only/unconfirmed_only)는 상품 테이블에만 적용된다(요약은 항상 전체 기준).
    """
    pos = _window_po_query(db, dfrom, dto, vendor_id).all()
    po_by_seq = {p.purchase_order_seq: p for p in pos}

    invoice_map, po_without_invoice = _collect_invoice_seqs(pos)
    all_invoice_seqs: set[int] = set()
    for v in invoice_map.values():
        all_invoice_seqs.update(v)
    settlements = _load_settlements(db, all_invoice_seqs)

    totals = _agg_by_status(db, dfrom, dto, vendor_id)
    invoices = _agg_invoices(invoice_map, po_without_invoice, settlements)
    sku_rows, coverage = _sku_aggregate(db, po_by_seq, invoice_map, settlements)

    total_sku_count = len(sku_rows)
    # ★드리프트 '미상'을 '없음'으로 접지 않는다(원칙22) — 귀속 가능 PO가 없거나 입고 완료 단계
    #   PO가 없는 SKU는 발주≠입고 여부를 **알 수 없다**. drift_only는 그런 SKU를 걸러내지만,
    #   몇 건이 그렇게 빠졌는지 세어서 응답에 싣는다(화면이 "나머지는 정상"으로 읽히지 않게).
    unknown_drift = sum(1 for r in sku_rows if r["drift_qty_settled_stage"] is None)
    if drift_only:
        # 수량 상쇄로 0이 된 SKU도 남긴다 — 건수가 있으면 불일치는 실재한다(R2-1).
        sku_rows = [
            r for r in sku_rows
            if r["drift_qty_settled_stage"] not in (None, 0) or r["drift_po_count_settled_stage"] > 0
        ]
    if unconfirmed_only:
        sku_rows = [
            r for r in sku_rows
            if r["po_without_invoice_count"] > 0
            or r["invoice_unconfirmed_count"] > 0
            or r["invoice_missing_row_count"] > 0
            or r["invoice_not_marked_transmitted_count"] > 0
        ]

    period: dict = {"from": dfrom.isoformat(), "to": dto.isoformat()}
    if vendor_id is not None:
        period["vendor_id"] = vendor_id

    return {
        "period": period,
        "channel": "COUPANG_ROCKET",
        "summary": {
            **totals,
            "invoice": invoices,
            "detail_coverage": coverage,
        },
        "filters": {
            "drift_only": drift_only,
            "unconfirmed_only": unconfirmed_only,
            "sku_count_total": total_sku_count,
            "sku_count_shown": len(sku_rows),
            # 발주≠입고를 판정할 근거 자체가 없는 SKU 수(0이 아니라 '모름') — drift_only가 제외한다.
            "sku_count_unknown_drift": unknown_drift,
        },
        "skus": sku_rows,
        "note": (
            "발주일(KST) 윈도우 기준 조회 전용. 요약=PO 그레인(전체 발주), 상품표=발주상세 그레인"
            "(수집된 PO만) — 차이는 detail_coverage 참조. SKU별 입고는 단일SKU PO 귀속분만이며 "
            "미귀속 PO 수를 함께 낸다(안분 추정 없음). 사실·지표만."
        ),
    }


def compute_rocket_recon_sku(db: Session, product_number: str, dfrom: date, dto: date,
                             vendor_id: str | None = None) -> dict:
    """한 SKU의 PO 목록(행 확장) — 발주·입고·명세서 단계·연결 계산서까지 한 줄에.

    윈도우/계정 필터는 목록 API와 동일 계약. PO가 0건이면 rows=[]로 오며, 그것은 "이 기간에
    이 상품 발주상세가 없다"는 뜻이지 발주가 없었다는 뜻이 아니다(발주상세 미수집 가능).
    """
    pos = _window_po_query(db, dfrom, dto, vendor_id).all()
    po_by_seq = {p.purchase_order_seq: p for p in pos}
    invoice_map, _ = _collect_invoice_seqs(pos)
    all_invoice_seqs: set[int] = set()
    for v in invoice_map.values():
        all_invoice_seqs.update(v)
    settlements = _load_settlements(db, all_invoice_seqs)

    rows = _po_lines_for_sku(db, product_number, po_by_seq, invoice_map, settlements)
    period: dict = {"from": dfrom.isoformat(), "to": dto.isoformat()}
    if vendor_id is not None:
        period["vendor_id"] = vendor_id
    return {
        "period": period,
        "product_number": product_number,
        "product_name": next((r["product_name"] for r in rows if r["product_name"]), None),
        "po_count": len(rows),
        "rows": rows,
        "note": (
            "입고수량은 PO 그레인 값이다(po_received_qty) — 멀티SKU PO면 이 SKU만의 입고가 아니다. "
            "line_confirmed_qty=None은 미수집(0 아님). 계산서 found=false는 번호만 있고 정산 미수집."
        ),
    }
