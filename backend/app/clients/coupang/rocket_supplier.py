# rocket_supplier.py — 쿠팡 로켓배송(1P) supplier.coupang.com 순수 파서 SA (트랙 rocket-1p S2)
#
# 런타임 경계(D-1): supplier는 Akamai 봇방어 → 백엔드 requests 직접 호출 금지.
#   Mac 헤드풀 CDP 페처(tools/, S3)가 수집 → raw push → 이 SA가 정규화(파싱). HTTP 없음.
# 단일 책임(원칙18-1): 원시 응답(발주 list JSON / 정산 DOM rows) → 정규화 레코드 dict[].
#   적재(upsert)는 services/coupang/rocket_supplier_sync.py(Harness)가 담당.
# 방어적 파싱(D-13): 키/컬럼 누락·스키마 드리프트에도 죽지 않고 가능한 만큼 정규화.
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════
# 값 변환 헬퍼 (방어적)
# ════════════════════════════════════════════════
def _to_int(v: Any) -> int:
    """'510,819' / 510819 / None → int. 콤마 제거, 실패 시 0."""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "")
    if s in ("", "-"):
        return 0
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def _to_dec(v: Any) -> Decimal:
    """'561,900' / Decimal / None → Decimal. 콤마 제거, 실패 시 0."""
    if v is None:
        return Decimal(0)
    if isinstance(v, Decimal):
        return v
    s = str(v).strip().replace(",", "")
    if s in ("", "-"):
        return Decimal(0)
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _to_date(v: Any) -> date | None:
    """'2026-06-16' / '-' / '' → date|None."""
    if not v:
        return None
    s = str(v).strip()
    if s in ("", "-"):
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _to_dt_utc_naive(v: Any) -> datetime | None:
    """ISO('2026-06-17T06:34:32.000+00:00') → naive UTC datetime.

    소스는 UTC(+00:00). tz 비교 회피 위해 naive UTC로 저장(KST 환산은 +9h, S4에서).
    """
    if not v:
        return None
    s = str(v).strip()
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)  # UTC 기준 naive로 통일
    return dt


# ════════════════════════════════════════════════
# ① + ② 발주/납품 list API 파서
# ════════════════════════════════════════════════
def extract_page_meta(payload: dict) -> dict:
    """list API 응답 envelope에서 페이지네이션 메타 추출(페처 루프 종료 판단용).

    envelope: {success, message, body:{body:[...], currentPage, lastPageNumber, totalRecordSize, pageSize}}.
    반환: {current_page, last_page_number, total_record_size, page_size}.
    """
    outer = (payload or {}).get("body") or {}
    return {
        "current_page": _to_int(outer.get("currentPage")),
        "last_page_number": _to_int(outer.get("lastPageNumber")),
        "total_record_size": _to_int(outer.get("totalRecordSize")),
        "page_size": _to_int(outer.get("pageSize")),
    }


def parse_purchase_order_list(payload: dict) -> list[dict]:
    """발주 list API 한 페이지 응답 → 정규화 PO 레코드[].

    payload = 한 페이지 raw JSON({success, body:{body:[{PO},...]}}). body.body 이중 중첩 주의(ref 20).
    purchaseOrderSeq 없는 row는 스킵(방어). 금액=gross(D-9 §6-1③).
    """
    outer = (payload or {}).get("body") or {}
    pos = outer.get("body") or []
    if not isinstance(pos, list):
        log.warning("rocket PO list: body.body가 list 아님(type=%s)", type(pos).__name__)
        return []

    records: list[dict] = []
    for po in pos:
        if not isinstance(po, dict):
            continue
        seq = po.get("purchaseOrderSeq")
        if seq is None:
            continue
        vp_list = po.get("vendorPaymentList") or []
        payment_seqs = [
            vp.get("vendorPaymentInfoSeq")
            for vp in vp_list
            if isinstance(vp, dict) and vp.get("vendorPaymentInfoSeq") is not None
        ]
        records.append({
            "purchase_order_seq": int(seq),
            "vendor_id": str(po.get("vendorId") or ""),
            "sum_of_order_amount": _to_int(po.get("sumOfOrderAmount")),
            "sum_of_receiving_amount": _to_int(po.get("sumOfReceivingAmount")),
            "sum_of_vendor_confirmed_amount": _to_int(po.get("sumOfVendorConfirmedAmount")),
            "order_qty": _to_int(po.get("sumOfOrderQty")),
            "receiving_qty": _to_int(po.get("sumOfReceivingQty")),
            "vendor_confirmed_qty": _to_int(po.get("sumOfVendorConfirmedQty")),
            "purchase_order_status": (po.get("purchaseOrderStatus") or None),
            "purchase_order_status_description": (po.get("purchaseOrderStatusDescription") or None),
            "purchase_type": (po.get("purchaseType") or None),
            "center_code": (po.get("centerCode") or None),
            "center_name": (po.get("centerName") or None),
            "first_sku_name": (po.get("firstSkuName") or None),
            "sku_count": _to_int(po.get("skuCount")),
            "po_created_at": _to_dt_utc_naive(po.get("createdAt")),
            "expected_delivery_date": _to_dt_utc_naive(po.get("expectedDeliveryDate")),
            "vendor_payment_seqs": payment_seqs,
        })
    return records


# ════════════════════════════════════════════════
# ③ 정산(매입 정산) DOM 파서 — 헤더명 기반 동적 매핑(D-13)
# ════════════════════════════════════════════════
# 정산 테이블 헤더명 → 정규화 키 (ref 20 §4 실측, 헤더 위치 변동 대비 이름 기반).
_SETTLE_COL = {
    "계산서번호": "invoice_seq",
    "작성일자": "issue_date",
    "지급일자": "payment_date",
    "과세유형": "tax_type",
    "정산유형": "settlement_type",
    "발행유형": "bill_issue_type",
    "공급가액": "supply_amount",
    "부가가치세": "vat",
    "지급예정금액": "payment_amount",
    "세금계산서 확정일": "tax_invoice_confirmed_date",
    "1차 지급액": "first_payment_amount",
    "2차 지급액": "second_payment_amount",
}


def parse_settlement_rows(rows: list[list[str]]) -> list[dict]:
    """정산 DOM 테이블 rows(헤더 포함) → 정규화 계산서 레코드[].

    rows[0] = 헤더(컬럼명), 이후 = 데이터. 헤더명으로 컬럼 인덱스 매핑(위치 변동 방어, D-13).
    공급가액+부가가치세=지급예정금액(gross) 관계는 S4 reconcile에서 검산.
    """
    if not rows or len(rows) < 2:
        return []
    header = [str(c).strip() for c in rows[0]]
    # 헤더명 → 컬럼 인덱스
    idx: dict[str, int] = {}
    for col_name, key in _SETTLE_COL.items():
        if col_name in header:
            idx[key] = header.index(col_name)
    if "invoice_seq" not in idx:
        log.warning("rocket 정산 파서: '계산서번호' 헤더 없음 → 파싱 중단. header=%s", header[:6])
        return []

    def cell(row: list, key: str) -> Any:
        i = idx.get(key)
        if i is None or i >= len(row):
            return None
        return row[i]

    records: list[dict] = []
    for row in rows[1:]:
        if not isinstance(row, (list, tuple)) or not row:
            continue
        inv = cell(row, "invoice_seq")
        inv_i = _to_int(inv)
        if inv_i <= 0:
            continue  # 헤더 반복/합계행/빈행 방어
        records.append({
            "invoice_seq": inv_i,
            "supply_amount": _to_dec(cell(row, "supply_amount")),
            "vat": _to_dec(cell(row, "vat")),
            "payment_amount": _to_dec(cell(row, "payment_amount")),
            "issue_date": _to_date(cell(row, "issue_date")),
            "payment_date": _to_date(cell(row, "payment_date")),
            "tax_invoice_confirmed_date": _to_date(cell(row, "tax_invoice_confirmed_date")),
            "settlement_type": (str(cell(row, "settlement_type")).strip() or None) if cell(row, "settlement_type") else None,
            "bill_issue_type": (str(cell(row, "bill_issue_type")).strip() or None) if cell(row, "bill_issue_type") else None,
            "tax_type": (str(cell(row, "tax_type")).strip() or None) if cell(row, "tax_type") else None,
            "first_payment_amount": _to_dec(cell(row, "first_payment_amount")),
            "second_payment_amount": _to_dec(cell(row, "second_payment_amount")),
        })
    return records
