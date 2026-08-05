# 1P 발송(ASN) 쉽먼트 수집 — "우리가 보낸 양" (ref 45)
#
# 이 테스트가 지키는 것 여섯:
#   ① 표를 **헤더 이름**으로 고른다 — 상세 페이지가 id="shipmentTotalTable"을 두 표에 중복해서
#      쓰기 때문이다(라이브 실측). id/인덱스로 고르면 요약표를 영영 못 본다.
#   ② rowspan 이월 — 「박스」 셀은 첫 행에만 있다. 위치로 읽으면 발주번호 자리에 SKU가 들어간다.
#   ③ 셀 수가 헤더와 1 차이도 아니면 **행을 버린다** — 조용히 한 칸 밀린 채 적재하지 않는다.
#   ④ 상세 없는 목록만 받으면 총 입고 수량은 **건드리지 않는다**(모름 유지). 0으로 접으면 미수금을
#      지어낸다(ref 45 §12에서 실제로 5,763,290원 과대계상이 났던 계열).
#   ⑤ 재수집 멱등 — 쉽먼트 단위 snapshot replace로 중복이 안 쌓이고 줄어든 라인이 반영된다.
#   ⑥ 상태·타입은 화면 라벨이 아니라 data-* 속성이 정본이다.
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.clients.coupang import rocket_shipment as parser
from app.models import (
    Base,
    CoupangRocketShipment,
    CoupangRocketShipmentBox,
    CoupangRocketShipmentItem,
)
from app.services.coupang import rocket_shipment_sync as sync

VENDOR = "A01029796"

LIST_HEADERS = ["쉽먼트 번호", "쉽먼트 상태", "발주서", "송장 번호", "발송일",
                "입고예정일", "센터", "박스수", "총 납품 수량", ""]

# 라이브 실측(2026-08-05) 그대로의 상세 표 4개.
#   ★table[0]과 table[1]이 **같은 id**를 쓴다 — 이 중복이 이 파일의 ①번 계약이다.
DETAIL_TABLES = [
    {"id": "shipmentTotalTable", "cls": "scmTable productInfo", "headers": [],
     "rows": [["택배사", "한진택배", "쿠팡 물류센터", "시흥2", "출고지", "[18462] 경기도 화성시 동탄영천로 15"],
              ["발송일", "2026-08-04 16:14", "도착예정일", "2026-08-05", "발주서 입고예정일", "2026-08-05"]]},
    {"id": "shipmentTotalTable", "cls": "scmTable basic marginT",
     "headers": ["발주 종수", "SKU 종수", "박스수", "총 납품 수량", "총 입고 수량"],
     "rows": [["8", "11", "1", "29", "0"]]},
    {"id": "shipmentDetailTable", "cls": "scmTable basic marginT",
     "headers": ["박스", "발주번호", "SKU", "SKU 이름", "SKU 바코드", "납품수량", "입고수량"],
     "rows": [
         ["박스 #1 PBL0105694344", "138140185", "37350957", "오하이 지문방지 필름", "8809465525057", "1", "0"],
         ["137913136", "39017751", "오하이 강화유리 필름", "8809465524968", "1", "0"],
         ["138057077", "50344700", "오하이 EZ툴 사생활보호", "8800252590258", "2", "0"],
     ]},
    {"id": "parcelInvoiceTable", "cls": "scmTable basic marginT",
     "headers": ["박스", "상태", "송장 번호 #", "집하", "도착", "하차"],
     "rows": [["박스 #1 PBL0105694344", "하차 완료", "462975689153",
               "2026-08-04 18:53", "", "2026-08-05 16:35"]]},
]


def _entry(seq=49139959, status="CONFIRMED", detail=True, tables=None):
    e = {
        "headers": LIST_HEADERS,
        "cells": [str(seq), "발송 완료", "138140027 외 7건", "462975689153",
                  "2026-08-04 16:14", "2026-08-05", "시흥2", "1 박스", "29 개", "조회 Label"],
        "data": {"id": str(seq), "status": status, "type": "PARCEL"},
        "po_content": "138140027<br>138140185<br>137913136<br>138057077",
    }
    if detail:
        e["detail"] = {"tables": tables if tables is not None else DETAIL_TABLES}
    return e


@pytest.fixture()
def db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


# ── ① 표 선택은 헤더 이름으로 (id 중복 방어) ─────────────────────────
def test_summary_table_picked_by_header_not_id():
    """id가 같은 두 표 중 **요약표**를 고른다. 첫 표(정보표)를 집으면 수량이 통째로 없어진다."""
    parsed = parser.parse_shipment_detail(49139959, DETAIL_TABLES)
    assert parsed["summary"] == {
        "po_count": 8, "sku_count": 11, "box_count": 1,
        "total_shipped_qty": 29, "total_received_qty": 0,
    }


def test_table_order_change_does_not_break_parsing():
    """표 순서를 뒤집어도 같은 결과 — 위치 의존이 없다는 증거."""
    parsed = parser.parse_shipment_detail(49139959, list(reversed(DETAIL_TABLES)))
    assert parsed["summary"]["total_received_qty"] == 0
    assert len(parsed["lines"]) == 3
    assert len(parsed["boxes"]) == 1


# ── ② rowspan 이월 ────────────────────────────────────────────────
def test_rowspan_box_carried_forward_without_column_shift():
    """둘째 행부터 「박스」 셀이 없다. 이월하지 않으면 발주번호 자리에 SKU가 들어간다(ref 45 §10)."""
    lines = parser.parse_shipment_detail(49139959, DETAIL_TABLES)["lines"]
    assert [ln["purchase_order_seq"] for ln in lines] == [138140185, 137913136, 138057077]
    assert [ln["product_number"] for ln in lines] == ["37350957", "39017751", "50344700"]
    assert all(ln["box_label"] == "박스 #1 PBL0105694344" for ln in lines)
    assert [ln["shipped_qty"] for ln in lines] == [1, 1, 2]
    assert [ln["received_qty"] for ln in lines] == [0, 0, 0]


def test_second_box_starts_new_carry():
    """박스가 둘이면 두 번째 박스 셀이 나온 뒤부터 그 라벨로 이월된다."""
    tables = [dict(t) for t in DETAIL_TABLES]
    tables[2] = dict(tables[2])
    tables[2]["rows"] = [
        ["박스 #1 A", "138140185", "37350957", "이름", "880", "1", "0"],
        ["137913136", "39017751", "이름", "881", "1", "0"],
        ["박스 #2 B", "138057077", "50344700", "이름", "882", "2", "2"],
        ["138140027", "41670010", "이름", "883", "3", "3"],
    ]
    lines = parser.parse_shipment_detail(1, tables)["lines"]
    assert [ln["box_label"] for ln in lines] == ["박스 #1 A", "박스 #1 A", "박스 #2 B", "박스 #2 B"]


# ── ③ 셀 수가 안 맞으면 버린다 ────────────────────────────────────
def test_row_with_unexpected_cell_count_is_dropped_not_shifted():
    """5셀(헤더 7)짜리 행은 **버린다** — 밀어 넣으면 조용히 틀린 발주번호가 적재된다."""
    tables = [dict(t) for t in DETAIL_TABLES]
    tables[2] = dict(tables[2])
    tables[2]["rows"] = [
        ["박스 #1 A", "138140185", "37350957", "이름", "880", "1", "0"],
        ["쓰레기", "행", "짧음", "5칸", "뿐"],
        ["138057077", "50344700", "이름", "882", "2", "2"],
    ]
    lines = parser.parse_shipment_detail(1, tables)["lines"]
    assert [ln["purchase_order_seq"] for ln in lines] == [138140185, 138057077]


# ── ⑥ data-* 속성이 정본 ─────────────────────────────────────────
def test_list_uses_data_attributes_and_strips_units():
    recs = parser.parse_shipment_list([_entry(detail=False)])
    assert len(recs) == 1
    r = recs[0]
    assert r["shipment_seq"] == 49139959
    assert r["status_code"] == "CONFIRMED"      # 화면 라벨('발송 완료')이 아니라 속성
    assert r["status_label"] == "발송 완료"
    assert r["shipment_type"] == "PARCEL"
    assert r["box_count"] == 1                  # '1 박스'
    assert r["total_shipped_qty"] == 29         # '29 개'
    assert r["center_name"] == "시흥2"
    # 발주서 셀은 잘려 있고 전체는 popover에만 있다. '외 7건'의 7이 발주번호로 새면 안 된다.
    assert r["purchase_order_seqs"] == [138140027, 138140185, 137913136, 138057077]


def test_list_row_without_data_id_is_skipped():
    bad = _entry(detail=False)
    bad["data"] = {}
    assert parser.parse_shipment_list([bad]) == []


# ── ④ 상세 없으면 "모름"을 지킨다 ────────────────────────────────
def test_list_only_ingest_leaves_received_qty_unknown(db):
    sync.ingest_shipments(db, VENDOR, [_entry(detail=False)])
    row = db.query(CoupangRocketShipment).one()
    assert row.total_shipped_qty == 29
    assert row.total_received_qty is None       # ★0이 아니라 모름
    assert row.detail_synced_at is None
    assert db.query(CoupangRocketShipmentItem).count() == 0


def test_detail_ingest_fills_quantities_and_third_party_evidence(db):
    sync.ingest_shipments(db, VENDOR, [_entry()])
    row = db.query(CoupangRocketShipment).one()
    assert row.total_shipped_qty == 29
    assert row.total_received_qty == 0
    assert row.po_count == 8 and row.sku_count == 11
    assert row.carrier_name == "한진택배"        # 라벨/값 정보표에서
    assert row.center_name == "시흥2"
    assert db.query(CoupangRocketShipmentItem).count() == 3
    box = db.query(CoupangRocketShipmentBox).one()
    assert box.status_label == "하차 완료"
    assert box.unloaded_at is not None           # ★제3자 기록 = 청구 증거
    assert box.arrived_at is None                # 빈 셀은 None(0/오늘로 지어내지 않는다)


# ── ⑤ 멱등 ───────────────────────────────────────────────────────
def test_reingest_is_idempotent_and_reflects_removed_lines(db):
    sync.ingest_shipments(db, VENDOR, [_entry()])
    sync.ingest_shipments(db, VENDOR, [_entry()])
    assert db.query(CoupangRocketShipment).count() == 1
    assert db.query(CoupangRocketShipmentItem).count() == 3
    assert db.query(CoupangRocketShipmentBox).count() == 1

    # 쿠팡이 라인을 하나 지운 경우 — 누적되지 않고 줄어야 한다.
    shrunk = [dict(t) for t in DETAIL_TABLES]
    shrunk[2] = dict(shrunk[2])
    shrunk[2]["rows"] = DETAIL_TABLES[2]["rows"][:1]
    sync.ingest_shipments(db, VENDOR, [_entry(tables=shrunk)])
    assert db.query(CoupangRocketShipmentItem).count() == 1


def test_detail_absent_after_detail_present_does_not_wipe_quantities(db):
    """상세를 한 번 받은 뒤 목록만 다시 받아도 이미 아는 입고 수량을 지우지 않는다."""
    sync.ingest_shipments(db, VENDOR, [_entry()])
    sync.ingest_shipments(db, VENDOR, [_entry(detail=False)])
    row = db.query(CoupangRocketShipment).one()
    assert row.total_received_qty == 0
    assert db.query(CoupangRocketShipmentItem).count() == 3


# ── 부분 실패 허용 ────────────────────────────────────────────────
def test_missing_box_table_still_ingests_lines(db):
    """박스 추적 표가 없어도(트럭 쉽먼트 등) 라인은 적재한다 — 전부-아니면-전무 금지."""
    no_box = [t for t in DETAIL_TABLES if t["id"] != "parcelInvoiceTable"]
    sync.ingest_shipments(db, VENDOR, [_entry(tables=no_box)])
    assert db.query(CoupangRocketShipmentItem).count() == 3
    assert db.query(CoupangRocketShipmentBox).count() == 0


def test_missing_line_table_does_not_crash(db):
    only_summary = [t for t in DETAIL_TABLES if t["id"] != "shipmentDetailTable"]
    out = sync.ingest_shipments(db, VENDOR, [_entry(tables=only_summary)])
    assert out["lines"] == 0
    assert db.query(CoupangRocketShipment).one().total_received_qty == 0
