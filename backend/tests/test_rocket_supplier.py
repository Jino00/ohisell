# test_rocket_supplier.py — 로켓배송(1P) 발주/정산 파서 + ingest 머니코드 fixture (트랙 rocket-1p S2)
# D-12 패턴: 라이브 API 호출 없음. fixture는 S1 정찰 실측(2026-06-17, ref 20) 기반.
#   파서(순수): 금액 gross·VAT 검산(공급+VAT=지급예정)·계산서 매핑·방어적 파싱.
#   ingest(인메모리 SQLite): snapshot upsert 멱등.
from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    CoupangRocketPurchaseOrder,
    CoupangRocketPurchaseOrderItem,
    CoupangRocketSettlement,
)
from app.clients.coupang import rocket_supplier as rs
from app.services.coupang import rocket_supplier_sync as sync


# 저장소 루트(backend/tests/ → ../..) — 원본 증거 파일(docs/references/data) 직접 파싱용.
_REPO_ROOT = Path(__file__).resolve().parents[2]


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
     "역발행", "204,437", "20,443", "224,880", "2026-06-16", "-", "0", "224880",
     "발주현황 입고상세내역 전송성공"],
    ["30003353", "주식회사 오하이테크", "2026-06-14", "2026-08-12", "과세", "일반", "입고",
     "역발행", "1,101,800", "110,180", "1,211,980", "2026-06-15", "-", "0", "1211980",
     "발주현황 입고상세내역 전송성공"],
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

def test_parse_settlement_header_whitespace_variants():
    # ★페처 셀 추출이 요소 경계마다 공백을 넣으므로(fetcher _CELL_HELPERS_JS) 쿠팡이 헤더를
    #   인라인 요소로 쪼개면 공백만 다른 변형이 온다 → 컬럼이 조용히 사라지면 안 된다.
    header = ["계 산 서 번 호", "세금계산서확정일", "1차지급액", "지급예정금액"]
    rows = [header, ["30025494", "2026-06-16", "0", "561,900"]]
    recs = rs.parse_settlement_rows(rows)
    assert len(recs) == 1
    assert recs[0]["invoice_seq"] == 30025494
    assert recs[0]["tax_invoice_confirmed_date"] == date(2026, 6, 16)  # 헤더 공백 변형에도 매핑
    assert recs[0]["payment_amount"] == Decimal("561900")


def test_settlement_money_survives_stray_space():
    # 숫자 셀에 공백이 끼어들어도(요소로 쪼개진 금액) 조용한 0이 되지 않는다
    rows = [["계산서번호", "공급가액", "작성일자"], ["30025494", "510 819", "2026-06 -16"]]
    recs = rs.parse_settlement_rows(rows)
    assert recs[0]["supply_amount"] == Decimal("510819")
    assert recs[0]["issue_date"] == date(2026, 6, 16)
    # 숫자꼴이 아닌 문자열은 건드리지 않는다(쓰레기를 몰래 뭉개지 않음)
    assert rs._to_dec("발주현황 입고상세내역") == Decimal(0)
    assert rs._to_int("510 819") == 510819


def test_parse_settlement_header_only():
    assert rs.parse_settlement_rows([_SETTLE_HEADER]) == []

def test_parse_settlement_missing_invoice_header():
    assert rs.parse_settlement_rows([["거래처명", "공급가액"], ["X", "100"]]) == []


# ═══ 전자세금계산서 전송상태(마지막 빈-헤더 링크 컬럼) ═══
def test_to_transmitted_variants():
    # 실측 2변형(DOM 샘플 10행 전수) + 방어 케이스
    assert rs._to_transmitted("발주현황 입고상세내역 전송성공") is True
    assert rs._to_transmitted("발주현황 입고상세내역") is False  # 상태 표기 없음 = 미전송
    assert rs._to_transmitted("전송성공") is True               # 버튼 라벨 없이 상태만 온 경우
    assert rs._to_transmitted("") is None                      # 빈 셀 = 판별 불가
    assert rs._to_transmitted(None) is None
    # 미관측 토큰은 True/False로 뭉개지 않고 None(D-13)
    assert rs._to_transmitted("발주현황 입고상세내역 전송실패") is None


def test_parse_settlement_transmitted_column():
    recs = rs.parse_settlement_rows(_SETTLE_ROWS)
    # 30025494 = 확정일 '-' + 전송성공 표기 없음 → False, 나머지 2건은 확정일 있음 + 전송성공 → True
    assert recs[0]["tax_invoice_transmitted"] is False
    assert recs[0]["tax_invoice_confirmed_date"] is None
    assert recs[1]["tax_invoice_transmitted"] is True
    assert recs[2]["tax_invoice_transmitted"] is True


def test_to_transmitted_button_label_drift_never_yields_false():
    # ★버튼 라벨이 드리프트해도 잔여를 잘라 먹지 않는다(토큰 정확 일치).
    #   부분문자열 replace였다면 잔여가 비어 False로 오판할 수 있는 자리.
    assert rs._to_transmitted("발주현황조회 입고상세내역 전송성공") is None  # 미관측 토큰 → None
    assert rs._to_transmitted("발주 현황 입고상세내역") is None            # 라벨 분리 → 판별 불가
    # 정상 라벨에서는 여전히 판정된다(회귀 방어)
    assert rs._to_transmitted("발주현황 입고상세내역 전송성공") is True


def test_to_transmitted_no_whitespace_between_links():
    # ★페처는 DOMParser 문서의 innerText(=textContent)로 셀을 뽑아 <a> 사이 공백을 보장받지 못한다.
    #   쿠팡 마크업이 미니파이되면 라벨+상태가 한 토큰으로 붙는다 → 그때도 전송성공은 살려낸다.
    assert rs._to_transmitted("발주현황입고상세내역전송성공") is True
    # 다만 붙어 있는데 상태 토큰이 없으면 False로 승격시키지 않는다(라벨 드리프트와 구분 불가)
    assert rs._to_transmitted("발주현황입고상세내역") is None


def test_to_transmitted_warns_once_per_token():
    # 미관측 토큰 warning은 파싱 1회당 토큰당 1줄(정산 최대 100p×50행 → 로그 폭주 방지)
    seen: set[str] = set()
    for _ in range(5):
        assert rs._to_transmitted("발주현황 입고상세내역 전송실패", seen) is None
    assert seen == {"전송실패"}


def test_parse_settlement_transmitted_missing_column(caplog):
    # 링크 컬럼(빈 헤더) 자체가 없는 DOM → None(다른 필드 파싱은 정상) + **소리내어** 경고
    rows = [["계산서번호", "공급가액"], ["30025494", "510,819"]]
    with caplog.at_level("WARNING"):
        recs = rs.parse_settlement_rows(rows)
    assert recs[0]["tax_invoice_transmitted"] is None
    assert recs[0]["supply_amount"] == Decimal("510819")
    assert any("링크 컬럼" in r.getMessage() for r in caplog.records)


def test_parse_settlement_transmitted_leading_blank_header():
    # 앞쪽에 빈 헤더가 끼어도 링크 컬럼(마지막)을 정확히 집는다
    header = [""] + _SETTLE_HEADER
    row = ["x"] + _SETTLE_ROWS[1]
    recs = rs.parse_settlement_rows([header, row])
    assert len(recs) == 1
    assert recs[0]["invoice_seq"] == 30025494
    assert recs[0]["tax_invoice_transmitted"] is False


def test_parse_settlement_ambiguous_blank_headers_warns(caplog):
    # 빈 헤더가 2개 이상이면 '마지막을 링크 컬럼으로 가정'했음을 소리내어 밝힌다
    header = _SETTLE_HEADER + [""]
    row = _SETTLE_ROWS[2] + ["발주현황 입고상세내역 전송성공"]
    with caplog.at_level("WARNING"):
        recs = rs.parse_settlement_rows([header, row])
    assert recs[0]["tax_invoice_transmitted"] is True
    assert any("빈 헤더" in r.getMessage() for r in caplog.records)


def test_parse_settlement_from_live_dom_sample():
    """★원본 증거 파일 직접 파싱(ref 20 §4): 10행 중 9행 전송성공 / 1행 미전송."""
    sample = _REPO_ROOT / "docs" / "references" / "data" / "20_rocket_1p_settlement_dom_sample.json"
    if not sample.exists():
        # skip이 아니라 fail: 이 파일은 git 추적 중이고, 파싱 규칙의 **유일한** 근거다.
        #   사라졌는데 스위트가 green이면 구현 베끼기 테스트만 남는다.
        pytest.fail(f"원본 DOM 샘플이 없다(git 추적 파일): {sample}")
    recs = rs.parse_settlement_rows(json.loads(sample.read_text(encoding="utf-8"))["rows"])
    assert len(recs) == 10
    flags = [r["tax_invoice_transmitted"] for r in recs]
    assert flags.count(True) == 9
    assert flags.count(False) == 1
    assert flags.count(None) == 0  # 전 행 판별 성공(미관측 토큰 없음)
    # 전송성공 ⟺ 세금계산서 확정일 존재(실측 상관, ref 20 §4)
    for r in recs:
        assert r["tax_invoice_transmitted"] is (r["tax_invoice_confirmed_date"] is not None)


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
    assert inv.tax_invoice_transmitted is False  # 전송성공 표기 없는 행
    assert db.query(CoupangRocketSettlement).filter_by(invoice_seq=30015106).one().tax_invoice_transmitted is True
    # 멱등
    sync.ingest_settlements(db, "A01029796", _SETTLE_ROWS)
    assert db.query(CoupangRocketSettlement).count() == 3


# ═══════════════════════════════════════════════════════════════════
# 발주상세 per-SKU (S4.5a, D-13) — 라이브 DOM 실측 fixture (ref 20b, PO 134342890)
# ═══════════════════════════════════════════════════════════════════
# Table[7] rows = JS DOMParser가 td/th innerText로 추출(병합셀로 행마다 셀 수 다름).
#   헤더 3행(7·2·10셀, th) + SKU 데이터행(13셀) + 연속행(5셀) + 합계행(8셀). per-SKU는 13셀행만.
#   금액 검산: 매입가(unit)×발주수량 = 발주금액(line, gross), 발주금액 = 공급가액(line) + 세액(line).
_PO_DETAIL_ROWS = [
    ["", "상품번호", "바코드", "매입유형", "수량", "금액", "제조(수입)일자 유통(소비)기한 생산년도"],
    ["공급가", "발주금액"],
    ["상품명", "면세여부", "발주수량", "업체납품가능수량", "매입가", "공급가액", "세액부가세", "매입가", "공급가액", "세액부가세"],
    ["1", "37350957", "8809465525057 오하이 지문방지 풀커버 저반사 매트 액정보호필름 전면 3매+내부 3매 +option+ 갤럭시 Z폴드5 ::",
     "일반매입 과세", "1", "1", "12,000", "10,909", "1,091", "12,000", "10,909", "1,091", ""],
    ["", "", "", "", ""],
    ["2", "50342949", "8800252590227 오하이 풀커버 강화유리 액정보호필름2개 + EZ툴 제공, 아이폰16프로",
     "일반매입 과세", "89", "89", "10,740", "9,764", "976", "955,860", "868,996", "86,864", ""],
    ["", "", "", "", ""],
    ["3", "63408012", "R237867070002 오하이 일미리 맥세이프 케이스 1mm 투명 아이폰 16프로",
     "직매입 과세", "2", "2", "10,080", "9,164", "916", "20,160", "18,328", "1,832", ""],
    ["", "", "", "", ""],
    ["4", "63427931", "R237867090004 오하이 일미리 맥세이프 케이스 1mm PC 하드 케이스 투명 아이폰 16플러스",
     "직매입 과세", "1", "1", "10,080", "9,164", "916", "10,080", "9,164", "916", ""],
    ["", "", "", "", ""],
    ["합계", "93", "93", "", "998,100", "907,397", "90,703", ""],
    ["", "", "", ""],
]


def test_parse_po_items_count_and_skips_headers_totals():
    # 13셀 SKU 데이터행만 추출(헤더 3행·연속 5셀행·합계 8셀행 제외)
    items = rs.parse_po_item_rows(_PO_DETAIL_ROWS)
    assert len(items) == 4


def test_parse_po_items_fields():
    items = rs.parse_po_item_rows(_PO_DETAIL_ROWS)
    it = items[1]  # SKU 50342949
    assert it["line_no"] == 2
    assert it["product_number"] == "50342949"
    assert it["barcode"] == "8800252590227"
    assert it["product_name"].startswith("오하이 풀커버 강화유리")
    assert it["purchase_type"] == "일반매입 과세"
    assert it["order_qty"] == 89
    assert it["vendor_confirmed_qty"] == 89  # 업체납품가능수량(인덱스 5)
    assert it["unit_purchase_price"] == Decimal("10740")  # 쿠팡→우리 매입 단가(gross)
    assert it["line_order_amount"] == Decimal("955860")    # line gross
    assert it["line_supply_amount"] == Decimal("868996")   # net
    assert it["line_vat"] == Decimal("86864")


def test_parse_po_items_money_checksums():
    # ★머니 검산: 매입가×발주수량=발주금액(line), 발주금액=공급가액+세액
    for it in rs.parse_po_item_rows(_PO_DETAIL_ROWS):
        assert it["unit_purchase_price"] * it["order_qty"] == it["line_order_amount"]
        assert it["line_order_amount"] == it["line_supply_amount"] + it["line_vat"]


def test_parse_po_items_barcode_internal_code():
    # 바코드가 EAN(숫자)가 아닌 내부코드(R-prefix)도 첫 토큰으로 분리
    items = rs.parse_po_item_rows(_PO_DETAIL_ROWS)
    assert items[2]["barcode"] == "R237867070002"
    assert items[2]["product_name"].startswith("오하이 일미리")


def test_parse_po_items_vendor_confirmed_qty_distinct_from_order_qty():
    # 발주수량(인덱스 4)과 업체납품가능수량(인덱스 5)이 다른 행 → 인덱스 혼동 방지
    partial = [
        ["1", "50342949", "8800252590227 오하이 풀커버", "일반매입 과세",
         "89", "40", "10,740", "9,764", "976", "955,860", "868,996", "86,864", ""],
    ]
    it = rs.parse_po_item_rows(partial)[0]
    assert it["order_qty"] == 89
    assert it["vendor_confirmed_qty"] == 40  # 부분 확인(발주분 일부만 납품 가능)


def test_parse_po_items_total_qty_matches_summary():
    # Σ발주수량 = 합계행(93)
    items = rs.parse_po_item_rows(_PO_DETAIL_ROWS)
    assert sum(it["order_qty"] for it in items) == 93


def test_parse_po_items_glued_barcode_name(caplog):
    # ★공백 소실 폴백: 셀 안 공백이 사라져도 바코드 컬럼(인덱스 걸림)에 상품명이 섞이지 않는다.
    #   근본 수정은 페처 쪽(_CELL_HELPERS_JS)이고 이건 그 뒤의 안전망 — 조용히 오염되지 않는지만 본다.
    glued = [
        ["1", "37350957", "8809465525057오하이 지문방지 풀커버", "일반매입 과세",
         "1", "1", "12,000", "10,909", "1,091", "12,000", "10,909", "1,091", ""],
        ["2", "63408012", "R237867070002오하이 일미리 맥세이프", "직매입 과세",
         "2", "2", "10,080", "9,164", "916", "20,160", "18,328", "1,832", ""],
    ]
    with caplog.at_level("WARNING"):
        items = rs.parse_po_item_rows(glued)
    assert [it["barcode"] for it in items] == ["8809465525057", "R237867070002"]
    assert items[0]["product_name"] == "오하이 지문방지 풀커버"
    assert items[1]["product_name"] == "오하이 일미리 맥세이프"
    # 소리내어 남긴다 — 다만 종류당 1줄(발주 다수 × SKU 80건 로그 폭주 방지)
    warned = [r.getMessage() for r in caplog.records if "공백 소실" in r.getMessage()]
    assert len(warned) == 1


def test_parse_po_items_unsplittable_barcode_warns(caplog):
    # 바코드꼴 선두 토큰이 없으면 **추측하지 않는다** — 현행 동작(전체를 barcode) + 경고
    row = [["1", "37350957", "ABC오하이지문방지", "일반매입 과세",
            "1", "1", "12,000", "10,909", "1,091", "12,000", "10,909", "1,091", ""]]
    with caplog.at_level("WARNING"):
        it = rs.parse_po_item_rows(row)[0]
    assert it["barcode"] == "ABC오하이지문방지"
    assert it["product_name"] is None
    assert any("분리 불가" in r.getMessage() for r in caplog.records)


def test_parse_po_items_barcode_only_cell_is_not_an_anomaly(caplog):
    # 상품명 없이 바코드만 있는 셀은 정상 입력 — 경고 없이 name=None
    row = [["1", "37350957", "8809465525057", "일반매입 과세",
            "1", "1", "12,000", "10,909", "1,091", "12,000", "10,909", "1,091", ""]]
    with caplog.at_level("WARNING"):
        it = rs.parse_po_item_rows(row)[0]
    assert (it["barcode"], it["product_name"]) == ("8809465525057", None)
    assert not [r for r in caplog.records if "바코드" in r.getMessage()]


def test_parse_po_items_empty_and_garbage():
    assert rs.parse_po_item_rows([]) == []
    assert rs.parse_po_item_rows([["합계", "93"], ["", ""]]) == []
    # 상품번호가 숫자 아닌 행은 스킵(헤더 방어)
    assert rs.parse_po_item_rows([["1", "ABC", "x", "y", "1", "1", "1", "1", "1", "1", "1", "1", ""]]) == []


# ═══════════════════════════════════════════════════════════════════
# 페처 DOM 추출 JS 자체의 계약 (tools/rocket_supplier_fetcher.py)
# ═══════════════════════════════════════════════════════════════════
# 위 fixture들은 "페처가 뽑아준 rows"를 입력으로 받는다 — 그 rows를 만드는 JS가 조용히 달라지면
# 파서 테스트는 전부 green인데 데이터가 오염된다(2026-07-28 리뷰 지적: DOMParser 문서는 렌더링되지
# 않아 innerText=textContent, textContent는 요소 사이에 공백을 넣지 않는다).
# → 실제 증거 HTML(ref20b)을 실제 브라우저에서 실제 추출 함수로 돌려 fixture와 대조한다.
def _fetcher_js() -> dict:
    """tools/rocket_supplier_fetcher.py의 JS 상수만 추출(모듈 import 부작용 없이 — 로깅·락 회피)."""
    import ast
    src = (_REPO_ROOT / "tools" / "rocket_supplier_fetcher.py").read_text(encoding="utf-8")
    want = {"_CELL_HELPERS_JS", "_FETCH_SETTLEMENT_JS", "_FETCH_PO_DETAIL_JS"}
    ns: dict = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in want for t in node.targets):
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<fetcher-js>", "exec"), ns)
    missing = want - set(ns)
    if missing:
        pytest.fail(f"페처에서 JS 상수를 찾지 못했다(이름이 바뀌었나?): {missing}")
    return ns


def test_fetcher_cell_extraction_never_relies_on_markup_whitespace():
    """★두 추출기가 공용 헬퍼(명시적 공백 조인)를 쓰고 innerText로 되돌아가지 않는지 소스로 고정.

    실동작(회귀 0 + 공백 독립성)은 브라우저가 필요해 이 스위트에서 검증하지 않는다
    (test_fetcher_button_only_chrome.py의 방침: 테스트는 브라우저를 띄우지 않는다).
      → 실증 하니스: `python3 tools/verify_rocket_dom_extract.py`
        (ref20b 원본/미니파이 HTML을 실제 Chromium에서 실제 추출 함수로 돌려 _PO_DETAIL_ROWS와 대조)
    여기서는 '조용히 옛 패턴으로 돌아가는 것'만 막는다.
    """
    js = _fetcher_js()
    for name in ("_FETCH_SETTLEMENT_JS", "_FETCH_PO_DETAIL_JS"):
        src = js[name]
        # innerText = DOMParser 문서에서 textContent로 떨어져 요소 사이 공백을 보장하지 못한다
        assert "innerText" not in src, f"{name}: innerText 회귀(요소 사이 공백 미보장)"
        assert "tableRows(" in src, f"{name}: 공용 셀 추출 헬퍼를 쓰지 않는다"
        assert "cellText" in src, f"{name}: 공용 셀 추출 헬퍼가 인라인되지 않았다"
        # 표 선택 토큰 매칭은 공백 무시로 — 정산은 noWs(c), 발주상세는 .map(noWs)
        assert re.search(r"noWs[(\)]|\(noWs\)", src), f"{name}: 표 선택 토큰 매칭이 공백에 민감하다"
    # <br>은 분리자가 아니어야 한다(헤더 '상품<br>번호'·'세액<br>부가세' 토큰 계약)
    assert "'BR'" in js["_CELL_HELPERS_JS"]


# ═══ 발주상세 ingest Harness (snapshot replace) ═══
def test_ingest_po_items_snapshot_replace(db):
    r = sync.ingest_po_items(db, 134342890, "A01029796", _PO_DETAIL_ROWS)
    assert r == {"ingested": 4, "purchase_order_seq": 134342890}
    assert db.query(CoupangRocketPurchaseOrderItem).count() == 4
    it = (
        db.query(CoupangRocketPurchaseOrderItem)
        .filter_by(purchase_order_seq=134342890, product_number="50342949")
        .one()
    )
    assert it.vendor_id == "A01029796"
    assert it.order_qty == 89
    assert it.vendor_confirmed_qty == 89  # 업체납품가능수량 적재
    assert it.line_order_amount == Decimal("955860")
    # 멱등: 같은 PO 재수신 시 snapshot replace(중복 누적 없이 4건 유지)
    sync.ingest_po_items(db, 134342890, "A01029796", _PO_DETAIL_ROWS)
    assert db.query(CoupangRocketPurchaseOrderItem).count() == 4


def test_ingest_po_items_replace_drops_removed_skus(db):
    sync.ingest_po_items(db, 134342890, "A01029796", _PO_DETAIL_ROWS)
    # SKU 1개만 남은 갱신본 재수신 → 이전 4건 삭제 후 1건만
    shrunk = [
        _PO_DETAIL_ROWS[0],
        ["1", "50342949", "8800252590227 오하이 풀커버", "일반매입 과세",
         "89", "89", "10,740", "9,764", "976", "955,860", "868,996", "86,864", ""],
    ]
    sync.ingest_po_items(db, 134342890, "A01029796", shrunk)
    assert db.query(CoupangRocketPurchaseOrderItem).count() == 1


def test_ingest_po_items_isolated_per_po(db):
    sync.ingest_po_items(db, 134342890, "A01029796", _PO_DETAIL_ROWS)
    sync.ingest_po_items(db, 999999999, "A01029796", _PO_DETAIL_ROWS)
    # 다른 PO 재수신은 자기 PO만 replace(타 PO 불변)
    sync.ingest_po_items(db, 999999999, "A01029796", _PO_DETAIL_ROWS)
    assert db.query(CoupangRocketPurchaseOrderItem).filter_by(purchase_order_seq=134342890).count() == 4
    assert db.query(CoupangRocketPurchaseOrderItem).filter_by(purchase_order_seq=999999999).count() == 4


# ═══════════════════════════════════════════════════════════════════
# 원가 브리지 매핑 (S4.5b, D-13) — RocketProductCostMap
# ═══════════════════════════════════════════════════════════════════
from decimal import Decimal as _D

from app.models import ProductMaster, RocketProductCostMap
from app.services.coupang import rocket_cost_map as cmap


def _seed_masters(db):
    db.add_all([
        ProductMaster(internal_sku="OHI-0001",
                      product_name="오하이 풀커버 강화유리 액정보호필름2개 아이폰16프로",
                      cost_price=_D("3200")),
        ProductMaster(internal_sku="OHI-0002",
                      product_name="오하이 일미리 맥세이프 케이스 1mm 투명 아이폰 16프로",
                      cost_price=_D("4100")),
        ProductMaster(internal_sku="OHI-0003",
                      product_name="전혀 다른 상품 무선 충전기",
                      cost_price=_D("9000")),
    ])
    db.commit()


# ─── 순수 SA: 이름 유사도 ───
def test_suggest_skus_ranks_by_name_similarity():
    cands = [
        {"internal_sku": "OHI-0001", "product_name": "오하이 풀커버 강화유리 액정보호필름2개 아이폰16프로", "cost_price": _D("3200")},
        {"internal_sku": "OHI-0003", "product_name": "전혀 다른 상품 무선 충전기", "cost_price": _D("9000")},
    ]
    out = cmap.suggest_skus("오하이 풀커버 강화유리 액정보호필름2개 + EZ툴 제공, 아이폰16프로", cands, top_n=2)
    assert out[0]["internal_sku"] == "OHI-0001"  # 가장 유사한 후보가 1위
    assert out[0]["score"] > out[1]["score"]
    assert 0.0 <= out[0]["score"] <= 1.0


def test_suggest_skus_empty_inputs():
    assert cmap.suggest_skus("", [{"internal_sku": "X", "product_name": "x"}]) == []
    assert cmap.suggest_skus("이름있음", []) == []


# ─── Harness: 미매핑 목록 ───
def test_list_unmapped_excludes_mapped_and_includes_suggestions(db):
    _seed_masters(db)
    sync.ingest_po_items(db, 134342890, "A01029796", _PO_DETAIL_ROWS)  # 4 SKU
    # 하나는 미리 확정 매핑 → 미매핑 목록에서 제외돼야
    cmap.upsert_mapping(db, "50342949", internal_sku="OHI-0001")

    res = cmap.list_unmapped(db, vendor_id="A01029796")
    pns = {it["product_number"] for it in res["items"]}
    assert "50342949" not in pns          # 확정된 건 제외
    assert res["total_unmapped"] == 3     # 4 - 1
    # 발주수량 큰 순 정렬 보장(50342949 제외 후 최다 = 37350957? qty 1; 모두 소량) — 집계 필드 검증
    top = res["items"][0]
    assert "total_order_qty" in top and "po_count" in top
    assert isinstance(top["suggestions"], list)


def test_suggestions_expose_how_many_products_already_share_that_cost(db):
    """★내부 SKU 이름이 기종을 지목해도 실제로는 **기종 공용 원가**일 수 있다.

    라이브 실측: `OHI-TGLASS-IP17PRO`("아이폰17 Pro")에 붙은 12개가 실제로는
    아이폰12·13·14·16이다. 납품단가가 전부 같아 원가 공유 자체는 일관되지만,
    **이름만 보면 다른 기종 원가를 붙이는 것으로 오해**한다 — 그리고 후보 제안이
    바로 그 SKU를 1순위로 내놓는다. 이름을 고치는 건 실물 지식이 필요하지만,
    "이미 N개가 이 원가를 쓰고 있다"를 보이는 것은 지금 할 수 있다.
    """
    _seed_masters(db)
    sync.ingest_po_items(db, 134342890, "A01029796", _PO_DETAIL_ROWS)
    # OHI-0001을 이미 두 상품번호가 공유하는 상태로 만든다.
    cmap.upsert_mapping(db, "50342949", internal_sku="OHI-0001")
    cmap.upsert_mapping(db, "99999999", internal_sku="OHI-0001")

    res = cmap.list_unmapped(db, vendor_id="A01029796")
    seen = {c["internal_sku"]: c["already_mapped_count"]
            for it in res["items"] for c in it["suggestions"]}
    assert seen.get("OHI-0001") == 2          # 이미 둘이 쓰는 중 = 공용 원가
    # 아직 아무도 안 쓰는 후보는 0 — «전용»과 «공용»이 구분된다.
    assert any(v == 0 for k, v in seen.items() if k != "OHI-0001")


def test_list_unmapped_vendor_filter_and_no_suggest(db):
    _seed_masters(db)
    sync.ingest_po_items(db, 134342890, "A01029796", _PO_DETAIL_ROWS)
    sync.ingest_po_items(db, 555, "OTHERVENDOR", _PO_DETAIL_ROWS)
    res = cmap.list_unmapped(db, vendor_id="A01029796", suggest=False)
    # vendor 필터: A01029796만 집계되지만 product_number는 동일(같은 fixture) → po_count는 자기 vendor PO만
    by_pn = {it["product_number"]: it for it in res["items"]}
    assert by_pn["50342949"]["po_count"] == 1  # OTHERVENDOR PO는 제외
    assert by_pn["50342949"]["suggestions"] == []  # suggest=False


# ─── Harness: 확정/검증 ───
def test_upsert_mapping_confirmed_validates_master(db):
    _seed_masters(db)
    sync.ingest_po_items(db, 134342890, "A01029796", _PO_DETAIL_ROWS)
    out = cmap.upsert_mapping(db, "50342949", internal_sku="OHI-0001")
    assert out["status"] == "confirmed"
    assert out["internal_sku"] == "OHI-0001"
    assert out["cost_price"] == _D("3200")
    # 라벨 캐시: 발주상세 product_name 자동 채움
    row = db.query(RocketProductCostMap).filter_by(product_number="50342949").one()
    assert row.product_name and "풀커버" in row.product_name
    # 멱등 upsert(중복 행 없음)
    cmap.upsert_mapping(db, "50342949", internal_sku="OHI-0002")
    assert db.query(RocketProductCostMap).filter_by(product_number="50342949").count() == 1
    assert db.query(RocketProductCostMap).filter_by(product_number="50342949").one().internal_sku == "OHI-0002"


def test_upsert_mapping_rejects_unknown_sku(db):
    with pytest.raises(ValueError):
        cmap.upsert_mapping(db, "50342949", internal_sku="OHI-9999")  # product_master에 없음


def test_upsert_mapping_confirmed_requires_sku(db):
    with pytest.raises(ValueError):
        cmap.upsert_mapping(db, "50342949", internal_sku=None, status="confirmed")


def test_upsert_mapping_ignored_excludes_from_unmapped(db):
    _seed_masters(db)
    sync.ingest_po_items(db, 134342890, "A01029796", _PO_DETAIL_ROWS)
    out = cmap.upsert_mapping(db, "63408012", status="ignored", note="샘플")
    assert out["status"] == "ignored"
    assert out["internal_sku"] is None  # 원가 제외
    res = cmap.list_unmapped(db, suggest=False)
    assert "63408012" not in {it["product_number"] for it in res["items"]}  # 재제안 방지


def test_upsert_mapping_invalid_status(db):
    with pytest.raises(ValueError):
        cmap.upsert_mapping(db, "50342949", internal_sku="OHI-0001", status="bogus")


# ─── Harness: 목록/삭제 ───
def test_list_mappings_joins_cost_price(db):
    _seed_masters(db)
    sync.ingest_po_items(db, 134342890, "A01029796", _PO_DETAIL_ROWS)
    cmap.upsert_mapping(db, "50342949", internal_sku="OHI-0001")
    res = cmap.list_mappings(db)
    assert res["count"] == 1
    m = res["mappings"][0]
    assert m["product_number"] == "50342949"
    assert m["cost_price"] == _D("3200")  # 현재 product_master cost_price 조인


def test_delete_mapping_restores_unmapped(db):
    _seed_masters(db)
    sync.ingest_po_items(db, 134342890, "A01029796", _PO_DETAIL_ROWS)
    cmap.upsert_mapping(db, "50342949", internal_sku="OHI-0001")
    assert cmap.delete_mapping(db, "50342949") == {"product_number": "50342949", "deleted": 1}
    res = cmap.list_unmapped(db, suggest=False)
    assert "50342949" in {it["product_number"] for it in res["items"]}  # 다시 미매핑
    # 없는 것 삭제 = deleted 0
    assert cmap.delete_mapping(db, "50342949")["deleted"] == 0
