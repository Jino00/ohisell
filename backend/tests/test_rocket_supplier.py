# test_rocket_supplier.py — 로켓배송(1P) 발주/정산 파서 + ingest 머니코드 fixture (트랙 rocket-1p S2)
# D-12 패턴: 라이브 API 호출 없음. fixture는 S1 정찰 실측(2026-06-17, ref 20) 기반.
#   파서(순수): 금액 gross·VAT 검산(공급+VAT=지급예정)·계산서 매핑·방어적 파싱.
#   ingest(인메모리 SQLite): snapshot upsert 멱등.
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import CoupangRocketPurchaseOrder, CoupangRocketSettlement
from app.clients.coupang import rocket_supplier as rs
from app.services.coupang import rocket_supplier_sync as sync


# ─── 실측 fixture (ref 20) ─────────────────────────────
# 발주 list 한 페이지 envelope (PO 134433322 = ref20 §3 실측, body.body 이중 중첩).
_PO_PAGE = {
    "success": True, "message": None,
    "body": {
        "currentPage": 1, "lastPageNumber": 3, "totalRecordSize": 120, "pageSize": 50,
        "body": [
            {
                "purchaseOrderSeq": 134433322,
                "vendorId": "A01029796", "vendorName": "주식회사 오하이테크",
                "purchaseType": "REORDER",
                "firstSkuName": "오하이 풀커버 강화유리 액정보호필름2개",
                "createdAt": "2026-06-17T06:34:32.000+00:00",
                "purchaseOrderStatus": "RP", "purchaseOrderStatusDescription": "거래처확인요청",
                "expectedDeliveryDate": "2026-06-23T15:00:00.000+00:00",
                "centerCode": "SEL1", "centerName": "서울",
                "sumOfVendorConfirmedQty": 2, "sumOfVendorConfirmedAmount": 21480,
                "sumOfReceivingQty": 0, "sumOfReceivingAmount": 0,
                "sumOfOrderQty": 2, "sumOfOrderAmount": 21480,
                "skuCount": 1,
                "vendorPaymentList": [],
            },
            {
                # 부분정산 사례(ref20 §6-1 ④): 1 PO ↔ 복수 계산서
                "purchaseOrderSeq": 134001752,
                "vendorId": "A01029796",
                "createdAt": "2026-06-10T01:00:00.000+00:00",
                "purchaseOrderStatus": "FI", "purchaseOrderStatusDescription": "입고완료",
                "sumOfOrderQty": 100, "sumOfOrderAmount": 2375980,
                "sumOfReceivingQty": 100, "sumOfReceivingAmount": 2375980,
                "skuCount": 3,
                "vendorPaymentList": [
                    {"vendorPaymentInfoSeq": 30003353, "documentStatus": "CONFIRMED"},
                    {"vendorPaymentInfoSeq": 30003354, "documentStatus": "CONFIRMED"},
                ],
            },
        ],
    },
}

# 정산 DOM rows (ref20 §4 + 20_rocket_1p_settlement_dom_sample.json 실측, 헤더+3행).
_SETTLE_HEADER = [
    "계산서번호", "거래처명", "작성일자", "지급일자", "과세유형", "발주유형", "정산유형",
    "발행유형", "공급가액", "부가가치세", "지급예정금액", "세금계산서 확정일",
    "1차 지급일", "1차 지급액", "2차 지급액", "",
]
_SETTLE_ROWS = [
    _SETTLE_HEADER,
    ["30025494", "주식회사 오하이테크", "2026-06-16", "2026-08-14", "과세", "일반", "입고",
     "역발행", "510,819", "51,081", "561,900", "-", "-", "0", "561900", "발주현황 입고상세내역"],
    ["30015106", "주식회사 오하이테크", "2026-06-15", "2026-08-14", "과세", "일반", "입고",
     "역발행", "204,437", "20,443", "224,880", "2026-06-16", "-", "0", "224880", "전송성공"],
    ["30003353", "주식회사 오하이테크", "2026-06-14", "2026-08-12", "과세", "일반", "입고",
     "역발행", "1,101,800", "110,180", "1,211,980", "2026-06-15", "-", "0", "1211980", "전송성공"],
]


# ═══ 값 변환 헬퍼 ═══
def test_to_int_comma():
    assert rs._to_int("510,819") == 510819

def test_to_int_dash_blank():
    assert rs._to_int("-") == 0
    assert rs._to_int("") == 0
    assert rs._to_int(None) == 0

def test_to_dec_comma():
    assert rs._to_dec("561,900") == Decimal("561900")

def test_to_date_dash():
    assert rs._to_date("-") is None
    assert rs._to_date("2026-06-16") == date(2026, 6, 16)

def test_to_dt_utc_naive():
    # +00:00 → naive UTC (KST 환산은 +9h, S4)
    assert rs._to_dt_utc_naive("2026-06-17T06:34:32.000+00:00") == datetime(2026, 6, 17, 6, 34, 32)


# ═══ 발주 list 파서 ═══
def test_extract_page_meta():
    m = rs.extract_page_meta(_PO_PAGE)
    assert m == {"current_page": 1, "last_page_number": 3, "total_record_size": 120, "page_size": 50}

def test_parse_po_fields():
    recs = rs.parse_purchase_order_list(_PO_PAGE)
    assert len(recs) == 2
    po = recs[0]
    assert po["purchase_order_seq"] == 134433322
    assert po["vendor_id"] == "A01029796"
    assert po["sum_of_order_amount"] == 21480  # D-3 매출(gross)
    assert po["sum_of_receiving_amount"] == 0   # 미입고(status RP)
    assert po["order_qty"] == 2
    assert po["purchase_order_status"] == "RP"
    assert po["center_code"] == "SEL1"
    assert po["po_created_at"] == datetime(2026, 6, 17, 6, 34, 32)
    assert po["vendor_payment_seqs"] == []

def test_parse_po_payment_mapping():
    # 부분정산: vendorPaymentList → 계산서번호 리스트(↔정산 드리프트 조인키, D-9 §6-1④)
    recs = rs.parse_purchase_order_list(_PO_PAGE)
    assert recs[1]["vendor_payment_seqs"] == [30003353, 30003354]

def test_parse_po_skips_seqless():
    payload = {"body": {"body": [{"vendorId": "X", "sumOfOrderAmount": 999}]}}
    assert rs.parse_purchase_order_list(payload) == []

def test_parse_po_body_not_list():
    assert rs.parse_purchase_order_list({"body": {"body": None}}) == []
    assert rs.parse_purchase_order_list({}) == []


# ═══ 정산 파서 (머니 검산) ═══
def test_parse_settlement_count_and_fields():
    recs = rs.parse_settlement_rows(_SETTLE_ROWS)
    assert len(recs) == 3
    r0 = recs[0]
    assert r0["invoice_seq"] == 30025494
    assert r0["supply_amount"] == Decimal("510819")  # net
    assert r0["vat"] == Decimal("51081")
    assert r0["payment_amount"] == Decimal("561900")  # gross
    assert r0["issue_date"] == date(2026, 6, 16)
    assert r0["payment_date"] == date(2026, 8, 14)
    assert r0["tax_invoice_confirmed_date"] is None  # "-"
    assert r0["settlement_type"] == "입고"
    assert r0["second_payment_amount"] == Decimal("561900")

def test_parse_settlement_gross_equals_net_plus_vat():
    # ★머니 검산(D-9 §6-1③): 지급예정금액 = 공급가액 + 부가가치세
    for r in rs.parse_settlement_rows(_SETTLE_ROWS):
        assert r["payment_amount"] == r["supply_amount"] + r["vat"]

def test_parse_settlement_header_order_independent():
    # 헤더명 기반 매핑(D-13): 컬럼 순서를 바꿔도 정확히 매핑
    reordered_header = ["지급예정금액", "계산서번호", "공급가액", "부가가치세", "작성일자"]
    rows = [reordered_header, ["561,900", "30025494", "510,819", "51,081", "2026-06-16"]]
    recs = rs.parse_settlement_rows(rows)
    assert len(recs) == 1
    assert recs[0]["invoice_seq"] == 30025494
    assert recs[0]["payment_amount"] == Decimal("561900")
    assert recs[0]["supply_amount"] == Decimal("510819")

def test_parse_settlement_header_only():
    assert rs.parse_settlement_rows([_SETTLE_HEADER]) == []

def test_parse_settlement_missing_invoice_header():
    assert rs.parse_settlement_rows([["거래처명", "공급가액"], ["X", "100"]]) == []


# ═══ ingest Harness (인메모리 SQLite) ═══
@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()

def test_ingest_po_and_idempotent(db):
    r1 = sync.ingest_purchase_orders(db, [_PO_PAGE])
    assert r1 == {"ingested": 2, "pages": 1}
    assert db.query(CoupangRocketPurchaseOrder).count() == 2
    po = db.query(CoupangRocketPurchaseOrder).filter_by(purchase_order_seq=134433322).one()
    assert po.sum_of_order_amount == 21480
    assert po.vendor_payment_seqs == []
    # 재수신 멱등: count 불변
    sync.ingest_purchase_orders(db, [_PO_PAGE])
    assert db.query(CoupangRocketPurchaseOrder).count() == 2

def test_ingest_po_updates_on_resync(db):
    sync.ingest_purchase_orders(db, [_PO_PAGE])
    # 같은 PO가 입고 완료로 갱신되어 재수신 → 확정치 교체
    updated = {"body": {"body": [{
        "purchaseOrderSeq": 134433322, "vendorId": "A01029796",
        "sumOfOrderAmount": 21480, "sumOfReceivingAmount": 21480,
        "sumOfReceivingQty": 2, "purchaseOrderStatus": "FI",
    }]}}
    sync.ingest_purchase_orders(db, [updated])
    po = db.query(CoupangRocketPurchaseOrder).filter_by(purchase_order_seq=134433322).one()
    assert po.sum_of_receiving_amount == 21480  # 0 → 21480 교체
    assert po.purchase_order_status == "FI"

def test_ingest_settlement(db):
    r = sync.ingest_settlements(db, "A01029796", _SETTLE_ROWS)
    assert r == {"ingested": 3}
    inv = db.query(CoupangRocketSettlement).filter_by(invoice_seq=30025494).one()
    assert inv.vendor_id == "A01029796"  # 계정축 주입
    assert inv.payment_amount == Decimal("561900")
    # 멱등
    sync.ingest_settlements(db, "A01029796", _SETTLE_ROWS)
    assert db.query(CoupangRocketSettlement).count() == 3
