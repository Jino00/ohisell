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
    # ★마지막 링크 컬럼: 헤더명이 **빈 문자열**(ref 20 §4 표 #16 · DOM 샘플 실측).
    #   셀 = 상시 버튼 라벨('발주현황'·'입고상세내역') + 전자세금계산서 전송상태 텍스트.
    "": "transmit_cell",
}

# 링크 컬럼의 상시 버튼 라벨(전송상태 아님) — 제거 후 남는 토큰이 전송상태.
_SETTLE_LINK_BUTTONS = ("발주현황", "입고상세내역")
_TRANSMIT_SUCCESS = "전송성공"


def _to_transmitted(v: Any, seen: set[str] | None = None) -> bool | None:
    """정산 마지막(빈 헤더) 링크 셀 → 전자세금계산서 전송성공 여부(bool|None).

    실측 변형(20_rocket_1p_settlement_dom_sample.json 10행 전수):
      "발주현황 입고상세내역 전송성공" (9행 — 전부 세금계산서 확정일 있음) → True
      "발주현황 입고상세내역"          (1행 — 확정일 '-' = 미확정)        → False(전송성공 미표기)
    셀 부재(컬럼 없음/행 짧음)·빈 문자열 → None(판별 불가).
    잔여 토큰이 미관측 상태면 → None + warning(True/False로 뭉개지 않음, D-13).

    버튼 라벨은 **토큰 단위 정확 일치**로만 걸러낸다(부분문자열 replace 금지):
      replace는 라벨이 드리프트하면 상태 토큰을 잘라 먹고, 잔여가 비면 False로 **오판**한다.
      토큰 일치는 잔여를 원형 그대로 남겨 미관측이면 반드시 None으로 떨어진다.
    seen: 미관측 토큰 warning 중복 억제 집합(파싱 1회당 토큰당 1줄).
      정산은 최대 100페이지×50행 → 행마다 찍으면 수천 줄이 쏟아진다.
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    rest = " ".join(t for t in s.split() if t not in _SETTLE_LINK_BUTTONS)
    if not rest:
        return False  # 버튼만 = 전송성공 미표기
    if rest == _TRANSMIT_SUCCESS:
        return True
    # 공백 소실 폴백: 페처는 td.innerText로 셀을 뽑지만(fetcher :112·:142) DOMParser 문서는
    #   렌더링되지 않아 innerText가 textContent로 떨어진다 → 쿠팡이 <a> 사이를 공백 없이
    #   렌더하면 라벨+상태가 한 토큰으로 붙는다("발주현황입고상세내역전송성공").
    #   현재 공백은 쿠팡 마크업 들여쓰기 덕이라 미니파이 한 번이면 사라진다.
    #   이때만 부분문자열 제거로 재시도하되 **'전송성공' 정확 일치일 때만** 채택한다.
    #   잔여가 비어도 False로 승격시키지 않는다 — 라벨 드리프트가 상태 토큰을 잘라먹은 경우와
    #   구분할 수 없어서다(부분문자열 replace의 '조용한 False 오판'을 되살리지 않는다).
    squashed = rest
    for btn in _SETTLE_LINK_BUTTONS:
        squashed = squashed.replace(btn, "")
    if squashed.strip() == _TRANSMIT_SUCCESS:
        return True
    if seen is None or rest not in seen:
        if seen is not None:
            seen.add(rest)
        log.warning("rocket 정산 파서: 미관측 전송상태 토큰=%r (셀 원문=%r) → None", rest, s)
    return None


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
        if col_name not in header:
            continue
        if col_name == "":
            # 빈 헤더('')는 링크 컬럼 = 문서상 항상 **마지막**(ref 20 §4 #16) → 뒤에서 찾는다.
            #   빈 헤더가 둘 이상이면 헤더만으로는 구분 불가 → 가정을 소리내어 밝히고 마지막을 택한다.
            if header.count("") > 1:
                log.warning("rocket 정산 파서: 빈 헤더 %d개 → 마지막을 링크 컬럼으로 가정. header=%s",
                            header.count(""), header)
            idx[key] = len(header) - 1 - header[::-1].index("")
        else:
            idx[key] = header.index(col_name)
    if "invoice_seq" not in idx:
        log.warning("rocket 정산 파서: '계산서번호' 헤더 없음 → 파싱 중단. header=%s", header[:6])
        return []
    if "transmit_cell" not in idx:
        # 조용히 전 행 None이 되는 것을 막는다 — 이 커밋이 존재하는 이유가 '컬럼이 소리 없이 사라짐'이다.
        log.warning("rocket 정산 파서: 링크 컬럼(빈 헤더) 없음 → 전송상태 전 행 None. header=%s", header)

    def cell(row: list, key: str) -> Any:
        i = idx.get(key)
        if i is None or i >= len(row):
            return None
        return row[i]

    records: list[dict] = []
    unknown_transmit: set[str] = set()  # 미관측 전송상태 토큰 — 파싱 1회당 토큰당 warning 1줄
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
            "tax_invoice_transmitted": _to_transmitted(cell(row, "transmit_cell"), unknown_transmit),
        })
    return records


# ════════════════════════════════════════════════
# 발주상세 per-SKU 파서 (S4.5a, D-13) — 위치 기반(병합셀로 헤더명 매핑 불가)
# ════════════════════════════════════════════════
# 소스: GET /scm/purchase/order/get/{seq} SSR HTML의 Table[7](ref 20b §2 라이브 실측).
#   헤더 3행이 rowspan/colspan 병합 + 매입가/공급가액/세액 컬럼 2벌(단가/라인) → 헤더명 매핑 불가.
#   대신 SKU 데이터행(13셀)의 위치가 안정적(검산으로 확정): 순번·상품번호·바코드+상품명·매입유형·
#   발주수량·업체납품가능수량·매입가(단가)·공급가액(단가)·세액(단가)·발주금액(라인)·공급가액(라인)·세액(라인)·제조일자.
# SKU 행 식별: len>=12 AND row[0](순번)·row[1](상품번호) 모두 숫자.
#   → 헤더 3행(셀수 7·2·10 또는 첫셀 비숫자)·연속행(5셀)·합계행(첫셀 '합계')을 전부 배제.
_PO_ITEM_COL = {
    "line_no": 0,           # 순번
    "product_number": 1,    # 상품번호(1P 카탈로그 — S4.5b 브리지 키)
    "barcode_name": 2,      # "바코드 상품명"(첫 토큰=바코드)
    "purchase_type": 3,     # 매입유형(일반매입/직매입 + 과세)
    "order_qty": 4,         # 발주수량
    "vendor_confirmed_qty": 5,  # 업체납품가능수량(ref 20b §2) = PO그레인 sumOfVendorConfirmedQty의 per-SKU 판
    "unit_purchase_price": 6,   # 매입가(단가, gross=쿠팡 지급 단가)
    "line_order_amount": 9,     # 발주금액(라인, gross)
    "line_supply_amount": 10,   # 공급가액(라인, net)
    "line_vat": 11,             # 세액(라인, 부가세)
}


def _is_digits(s: Any) -> bool:
    t = str(s or "").strip().replace(",", "")
    return t.isdigit() and t != ""


def parse_po_item_rows(rows: list[list[str]]) -> list[dict]:
    """발주상세 Table[7] DOM rows → per-SKU 라인아이템 레코드[].

    rows = JS DOMParser가 추출한 셀 텍스트 배열(헤더/연속/합계행 혼재). 위치 기반(헤더 병합셀).
    SKU 행 = len>=12 AND 순번·상품번호 모두 숫자. 바코드는 셀 첫 토큰(EAN 숫자 또는 R-내부코드).
    머니 검산(soft): 매입가×발주수량 ≠ 발주금액이면 warning(드롭 안 함 — 수집은 보존, 검증은 reconcile).
    """
    records: list[dict] = []
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 12:
            continue
        if not (_is_digits(row[0]) and _is_digits(row[1])):
            continue  # 헤더·합계·연속행 방어
        barcode_name = str(row[_PO_ITEM_COL["barcode_name"]] or "").strip()
        barcode, _, name = barcode_name.partition(" ")
        rec = {
            "line_no": _to_int(row[_PO_ITEM_COL["line_no"]]),
            "product_number": str(row[_PO_ITEM_COL["product_number"]] or "").strip(),
            "barcode": barcode.strip(),
            "product_name": name.strip() or None,
            "purchase_type": str(row[_PO_ITEM_COL["purchase_type"]] or "").strip() or None,
            "order_qty": _to_int(row[_PO_ITEM_COL["order_qty"]]),
            "vendor_confirmed_qty": _to_int(row[_PO_ITEM_COL["vendor_confirmed_qty"]]),
            "unit_purchase_price": _to_dec(row[_PO_ITEM_COL["unit_purchase_price"]]),
            "line_order_amount": _to_dec(row[_PO_ITEM_COL["line_order_amount"]]),
            "line_supply_amount": _to_dec(row[_PO_ITEM_COL["line_supply_amount"]]),
            "line_vat": _to_dec(row[_PO_ITEM_COL["line_vat"]]),
        }
        expected = rec["unit_purchase_price"] * rec["order_qty"]
        if rec["order_qty"] > 0 and rec["unit_purchase_price"] > 0 and expected != rec["line_order_amount"]:
            log.warning(
                "rocket 발주상세 검산 불일치(상품번호=%s): 단가%s×수량%d=%s ≠ 발주금액%s",
                rec["product_number"], rec["unit_purchase_price"], rec["order_qty"],
                expected, rec["line_order_amount"],
            )
        records.append(rec)
    return records
