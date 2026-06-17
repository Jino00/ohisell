# rocket_supplier_sync.py — 쿠팡 로켓배송(1P) 발주/정산 ingest+store Harness (트랙 rocket-1p S2)
#
# 소스: supplier.coupang.com (ref 20, D-9). 발주+납품 = list JSON, 정산 = SSR DOM rows.
# 런타임 경계(D-1): Akamai 봇방어 → 백엔드 requests 직접 호출 금지.
#   Mac 헤드풀 CDP 페처(S3)가 수집 → raw push(아래 ingest) → 파서(clients/coupang/rocket_supplier.py)
#   정규화 → snapshot upsert. 이 Harness는 push 수신·저장·조회만(읽기전용·net_profit 불변).
#
# 단일 책임 조합(원칙18-2): 파서 SA 호출 → 모델 upsert. grain: 발주=purchase_order_seq,
#   정산=invoice_seq. 같은 seq 재수신 시 확정치 교체(per-row upsert, 멱등).
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.clients.coupang import rocket_supplier as parser
from app.models import CoupangRocketPurchaseOrder, CoupangRocketSettlement
from app.utils.kst import kst_now

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════
# ① + ② 발주/납품 ingest
# ════════════════════════════════════════════════
def _upsert_po(db: Session, rec: dict) -> None:
    row = (
        db.query(CoupangRocketPurchaseOrder)
        .filter(CoupangRocketPurchaseOrder.purchase_order_seq == rec["purchase_order_seq"])
        .first()
    )
    if row is None:
        row = CoupangRocketPurchaseOrder(purchase_order_seq=rec["purchase_order_seq"])
        db.add(row)
    row.vendor_id = rec["vendor_id"]
    row.sum_of_order_amount = rec["sum_of_order_amount"]
    row.sum_of_receiving_amount = rec["sum_of_receiving_amount"]
    row.sum_of_vendor_confirmed_amount = rec["sum_of_vendor_confirmed_amount"]
    row.order_qty = rec["order_qty"]
    row.receiving_qty = rec["receiving_qty"]
    row.vendor_confirmed_qty = rec["vendor_confirmed_qty"]
    row.purchase_order_status = rec["purchase_order_status"]
    row.purchase_order_status_description = rec["purchase_order_status_description"]
    row.purchase_type = rec["purchase_type"]
    row.center_code = rec["center_code"]
    row.center_name = rec["center_name"]
    row.first_sku_name = rec["first_sku_name"]
    row.sku_count = rec["sku_count"]
    row.po_created_at = rec["po_created_at"]
    row.expected_delivery_date = rec["expected_delivery_date"]
    row.vendor_payment_seqs = rec["vendor_payment_seqs"]
    row.synced_at = kst_now()


def ingest_purchase_orders(db: Session, pages: list[dict]) -> dict:
    """Mac 페처가 보낸 발주 list 페이지들(raw JSON) → 파싱 → snapshot upsert.

    pages: [{발주 list API 한 페이지 raw JSON}, ...] (page=1..lastPageNumber 루프 결과).
    멱등: 같은 purchase_order_seq 재수신 시 확정치로 교체.
    반환: {ingested(PO 수), pages(페이지 수)}.
    """
    n = 0
    for payload in pages or []:
        if not isinstance(payload, dict):
            continue
        for rec in parser.parse_purchase_order_list(payload):
            _upsert_po(db, rec)
            n += 1
    db.commit()
    log.info("rocket PO ingest: pages=%d records=%d", len(pages or []), n)
    return {"ingested": n, "pages": len(pages or [])}


# ════════════════════════════════════════════════
# ③ 정산 ingest
# ════════════════════════════════════════════════
def _upsert_settlement(db: Session, vendor_id: str, rec: dict) -> None:
    row = (
        db.query(CoupangRocketSettlement)
        .filter(CoupangRocketSettlement.invoice_seq == rec["invoice_seq"])
        .first()
    )
    if row is None:
        row = CoupangRocketSettlement(invoice_seq=rec["invoice_seq"])
        db.add(row)
    row.vendor_id = vendor_id
    row.supply_amount = rec["supply_amount"]
    row.vat = rec["vat"]
    row.payment_amount = rec["payment_amount"]
    row.issue_date = rec["issue_date"]
    row.payment_date = rec["payment_date"]
    row.tax_invoice_confirmed_date = rec["tax_invoice_confirmed_date"]
    row.settlement_type = rec["settlement_type"]
    row.bill_issue_type = rec["bill_issue_type"]
    row.tax_type = rec["tax_type"]
    row.first_payment_amount = rec["first_payment_amount"]
    row.second_payment_amount = rec["second_payment_amount"]
    row.synced_at = kst_now()


def ingest_settlements(db: Session, vendor_id: str, rows: list[list]) -> dict:
    """Mac 페처가 보낸 정산 DOM rows(헤더 포함) → 파싱 → snapshot upsert.

    rows: 정산 테이블 DOM(rows[0]=헤더). vendor_id는 계정축(정산 row엔 거래처명만 있어 별도 주입).
    멱등: 같은 invoice_seq 재수신 시 확정치로 교체.
    반환: {ingested(계산서 수)}.
    """
    recs = parser.parse_settlement_rows(rows)
    for rec in recs:
        _upsert_settlement(db, vendor_id, rec)
    db.commit()
    log.info("rocket settlement ingest: vendor=%s records=%d", vendor_id, len(recs))
    return {"ingested": len(recs)}
