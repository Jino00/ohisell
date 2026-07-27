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
from app.models import (
    CoupangRocketPurchaseOrder,
    CoupangRocketPurchaseOrderItem,
    CoupangRocketSettlement,
    CoupangWingCookie,
)
from app.utils.kst import kst_now

# 로켓배송 페처 heartbeat/refresh 상태 행 (Wing 페처 패턴 복제, vendor_summary_sync 참고).
# CoupangWingCookie 테이블을 재사용(가상 account_key).
_ROCKET_FETCHER_ACCOUNT = "COUPANG_ROCKET_FETCHER"
_STALE_HOURS = 26  # 이 시간 이상 push 없으면 stale (야간 off 오탐 방지)

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


# ════════════════════════════════════════════════
# 발주상세 per-SKU ingest (S4.5a, D-13) — snapshot replace
# ════════════════════════════════════════════════
def ingest_po_items(db: Session, purchase_order_seq: int, vendor_id: str, rows: list[list]) -> dict:
    """Mac 페처가 보낸 발주상세 Table[7] DOM rows → 파싱 → 해당 PO **snapshot replace**.

    rows: 발주상세 DOM(헤더·SKU·합계행 혼재). 위치 기반 파서가 SKU 행만 추출.
    vendor_id: 계정축(발주상세 DOM엔 거래처 없어 별도 주입, 정산 ingest와 동일).
    멱등: 같은 purchase_order_seq의 기존 라인아이템 전부 삭제 후 재삽입(SKU 제거 반영, 누적 방지).
    반환: {ingested(SKU 수), purchase_order_seq}.
    """
    recs = parser.parse_po_item_rows(rows)
    # snapshot replace: 이 PO의 기존 라인 삭제(타 PO 불변) → 재삽입.
    #   ORM 로드 후 delete + flush(동일 세션 재적재 시 identity-map 충돌 회피, per-PO N≤50 소량).
    existing = (
        db.query(CoupangRocketPurchaseOrderItem)
        .filter(CoupangRocketPurchaseOrderItem.purchase_order_seq == purchase_order_seq)
        .all()
    )
    for old in existing:
        db.delete(old)
    db.flush()
    now = kst_now()
    for rec in recs:
        db.add(CoupangRocketPurchaseOrderItem(
            purchase_order_seq=purchase_order_seq,
            vendor_id=vendor_id,
            line_no=rec["line_no"],
            product_number=rec["product_number"],
            barcode=rec["barcode"] or None,
            product_name=rec["product_name"],
            purchase_type=rec["purchase_type"],
            order_qty=rec["order_qty"],
            unit_purchase_price=rec["unit_purchase_price"],
            line_order_amount=rec["line_order_amount"],
            line_supply_amount=rec["line_supply_amount"],
            line_vat=rec["line_vat"],
            synced_at=now,
        ))
    db.commit()
    log.info("rocket PO 발주상세 ingest: po=%d vendor=%s skus=%d", purchase_order_seq, vendor_id, len(recs))
    return {"ingested": len(recs), "purchase_order_seq": purchase_order_seq}


# ════════════════════════════════════════════════
# 로켓 페처 heartbeat / 갱신 트리거 (Wing 패턴 복제)
# ════════════════════════════════════════════════
def _state_row(db: Session):
    return (
        db.query(CoupangWingCookie)
        .filter(CoupangWingCookie.account_key == _ROCKET_FETCHER_ACCOUNT)
        .first()
    )


def _ensure_state_row(db: Session):
    row = _state_row(db)
    if row is None:
        row = CoupangWingCookie(account_key=_ROCKET_FETCHER_ACCOUNT)
        db.add(row)
    return row


def rocket_fetcher_status(db: Session) -> dict:
    """로켓 페처 push 상태(민감값 없음). last_success_at=마지막 push, stale=push 끊김."""
    row = _state_row(db)
    if row is None:
        return {"account": _ROCKET_FETCHER_ACCOUNT, "status": "none",
                "last_success_at": None, "age_hours": None, "stale": False}
    age_hours = None
    stale = False
    if row.last_success_at:
        age_hours = max(0.0, round((kst_now() - row.last_success_at).total_seconds() / 3600, 1))
        stale = age_hours > _STALE_HOURS
    return {
        "account": _ROCKET_FETCHER_ACCOUNT,
        "status": row.status,
        "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        "last_error": row.last_error,
        "age_hours": age_hours,
        "stale": stale,
    }


def request_rocket_refresh(db: Session) -> dict:
    """UI '로켓 갱신' 버튼 → 갱신 요청 플래그 set. 페처가 다음 실행에서 소비."""
    row = _ensure_state_row(db)
    row.refresh_requested_at = kst_now()
    db.commit()
    return {"requested": True, "requested_at": row.refresh_requested_at.isoformat()}


def rocket_refresh_status(db: Session) -> dict:
    """갱신 요청/완료 상태. UI 폴링·페처 소비 공용."""
    row = _state_row(db)
    if row is None:
        return {"requested": False, "requested_at": None, "last_success_at": None,
                "status": "none", "last_error": None}
    return {
        "requested": row.refresh_requested_at is not None,
        "requested_at": row.refresh_requested_at.isoformat() if row.refresh_requested_at else None,
        "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        "status": row.status,
        "last_error": row.last_error,
    }


def claim_rocket_refresh(db: Session) -> dict:
    """로켓 페처가 갱신 요청을 '소비'(플래그 clear). 원자적 조건부 UPDATE."""
    from sqlalchemy import update

    res = db.execute(
        update(CoupangWingCookie)
        .where(CoupangWingCookie.account_key == _ROCKET_FETCHER_ACCOUNT)
        .where(CoupangWingCookie.refresh_requested_at.isnot(None))
        .values(refresh_requested_at=None)
    )
    db.commit()
    return {"claimed": (res.rowcount or 0) > 0}


def mark_rocket_fetch_success(db: Session) -> None:
    """페처 실행 완료 시 last_success_at 갱신."""
    row = _ensure_state_row(db)
    row.last_success_at = kst_now()
    row.status = "green"
    row.last_error = None
    db.commit()
