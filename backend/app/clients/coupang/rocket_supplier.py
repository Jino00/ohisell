# rocket_supplier.py — 쿠팡 로켓배송(1P) supplier.coupang.com 순수 파서 SA (트랙 rocket-1p S2)
#
# 런타임 경계(D-1): supplier는 Akamai 봇방어 → 백엔드 requests 직접 호출 금지.
#   Mac 헤드풀 CDP 페처(tools/, S3)가 수집 → raw push → 이 SA가 정규화(파싱). HTTP 없음.
# 단일 책임(원칙18-1): 원시 응답(발주 list JSON / 정산 DOM rows) → 정규화 레코드 dict[].
#   적재(upsert)는 services/coupang/rocket_supplier_sync.py(Harness)가 담당.
# 방어적 파싱(D-13): 키/컬럼 누락·스키마 드리프트에도 죽지 않고 가능한 만큼 정규화.
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

log = logging.getLogger(__name__)

# 셀 안 공백만 다른 '숫자/날짜 하나'를 복구하기 위한 패턴.
#   페처가 셀 텍스트를 뽑을 때 요소 경계마다 공백을 넣으므로(fetcher _CELL_HELPERS_JS),
#   쿠팡이 숫자를 인라인 요소로 쪼개 놓으면 "510 819"처럼 공백이 끼어들 수 있다.
#   콤마만 지우던 기존 로직은 이때 **조용히 0**을 만든다 → 공백을 지워도 숫자/날짜뿐이면 복구한다.
#   (숫자꼴이 아닌 문자열은 건드리지 않는다 — 쓰레기 입력을 몰래 뭉개지 않기 위해)
_NUMERIC_RE = re.compile(r"^-?[\d,]+(\.\d+)?$")
_DATEISH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _despace_numeric(s: str) -> str:
    """공백을 지우면 숫자꼴이 되는 문자열만 공백 제거(아니면 원본 유지)."""
    if not s or not re.search(r"\s", s):
        return s
    squashed = re.sub(r"\s+", "", s)
    return squashed if _NUMERIC_RE.match(squashed) else s


# ════════════════════════════════════════════════
# 값 변환 헬퍼 (방어적)
# ════════════════════════════════════════════════
def _to_int(v: Any) -> int:
    """'510,819' / 510819 / None → int. 콤마 제거, 실패 시 0."""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = _despace_numeric(str(v).strip()).replace(",", "")
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
    s = _despace_numeric(str(v).strip()).replace(",", "")
    if s in ("", "-"):
        return Decimal(0)
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _to_date(v: Any) -> date | None:
    """'2026-06-16' / '-' / '' → date|None. 공백이 끼어든 날짜('2026-06 -16')도 복구."""
    if not v:
        return None
    s = str(v).strip()
    if s in ("", "-"):
        return None
    if not _DATEISH_RE.match(s):
        squashed = re.sub(r"\s+", "", s)
        if _DATEISH_RE.match(squashed):
            s = squashed
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
    # 공백 소실 폴백(안전망): 근본 원인은 2026-07-28에 페처에서 고쳤다 —
    #   추출이 자손 텍스트노드를 명시적으로 공백 조인하므로(fetcher _CELL_HELPERS_JS) 쿠팡이
    #   HTML을 미니파이해도 <a> 사이 공백이 유지된다. 그래도 이 폴백은 남긴다:
    #   추출 경로가 또 바뀌거나(구버전 페처가 도는 Mac) 다른 입력이 들어와도 조용히 틀리지 않게.
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
    # 헤더 매칭은 **공백 무시**(등가 비교는 유지 — 부분일치로 넓히지 않는다).
    #   페처가 셀 텍스트를 요소 경계마다 공백으로 이어 붙이므로(fetcher _CELL_HELPERS_JS),
    #   쿠팡이 헤더를 인라인 요소로 쪼개면 '세금계산서확정일'↔'세금계산서 확정일'처럼
    #   공백만 다른 변형이 나올 수 있다. 그때 컬럼이 조용히 사라지지 않게 한다.
    norm_header = [re.sub(r"\s+", "", h) for h in header]
    # 헤더명 → 컬럼 인덱스
    idx: dict[str, int] = {}
    for col_name, key in _SETTLE_COL.items():
        norm_col = re.sub(r"\s+", "", col_name)
        if col_name != "" and norm_col not in norm_header:
            continue
        if col_name == "" and "" not in header:
            continue
        if col_name == "":
            # 빈 헤더('')는 링크 컬럼 = 문서상 항상 **마지막**(ref 20 §4 #16) → 뒤에서 찾는다.
            #   빈 헤더가 둘 이상이면 헤더만으로는 구분 불가 → 가정을 소리내어 밝히고 마지막을 택한다.
            if header.count("") > 1:
                log.warning("rocket 정산 파서: 빈 헤더 %d개 → 마지막을 링크 컬럼으로 가정. header=%s",
                            header.count(""), header)
            idx[key] = len(header) - 1 - header[::-1].index("")
        else:
            idx[key] = norm_header.index(norm_col)
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


# 공백 소실 폴백용 바코드 패턴: EAN/JAN(숫자 8~14) 또는 내부코드(영문 1자+숫자 8~14, 예 R237867070002).
#   뒤가 숫자·공백이 아닌 문자로 이어질 때만 = 숫자 런을 중간에서 자르지 않는다(ref20b §2 실측 2종).
_GLUED_BARCODE_RE = re.compile(r"^(\d{8,14}|[A-Za-z]\d{8,14})(?=[^\s\d])")
_BARCODE_ONLY_RE = re.compile(r"^(\d{8,14}|[A-Za-z]\d{8,14})$")


def _split_barcode_name(cell: Any, seen: set[str] | None = None) -> tuple[str, str | None]:
    """'바코드 상품명' 셀 → (barcode, product_name|None).

    정상 입력은 바코드와 상품명 사이에 공백이 있다. ★공백이 사라진 입력을 그냥 partition하면
    **바코드 컬럼에 상품명 조각까지** 들어간다(인덱스 걸린 컬럼) — 조용한 데이터 오염이다.
    ★상품명 자체에 공백이 있으므로("8809465525057오하이 지문방지 풀커버") '공백 유무'로는
      이 상황을 못 잡는다. 판단 기준은 **선두 토큰이 바코드꼴인가**다.
    근본 원인은 페처의 셀 추출이었고(2026-07-28 수정: fetcher _CELL_HELPERS_JS), 이건 그 뒤의 안전망:
      선두에 바코드 런(EAN 8~14자리 / 영문+숫자 내부코드, ref20b §2 실측 2종)이 붙어 있으면 떼어낸다.
      떼어낼 수 없으면 **추측하지 않고** 현행 동작(첫 공백 분리 / 전체를 barcode) + warning.
      상품명이 숫자로 시작하는 경우까지 갈라내려면 근거가 없다 — 소리내어 남기는 쪽을 택한다.
    seen: warning 중복 억제(발주상세 1건당 최대 80 SKU × PO 다수 → 종류당 1줄).
    """
    s = str(cell or "").strip()
    if not s:
        return "", None

    def warn_once(kind: str, msg: str, *args) -> None:
        if seen is not None and kind in seen:
            return
        if seen is not None:
            seen.add(kind)
        log.warning(msg, *args)

    # ① 공백 소실: 바코드 런 뒤에 숫자도 공백도 아닌 문자가 바로 붙어 있다(정상 셀은 여기 안 걸린다).
    m = _GLUED_BARCODE_RE.match(s)
    if m:
        warn_once("glued",
                  "rocket 발주상세 파서: 바코드/상품명 셀 공백 소실 → 선두 바코드로 분리(barcode=%s, 셀=%r)",
                  m.group(1), s[:60])
        return m.group(1), (s[m.end():].strip() or None)
    # ② 정상 경로: 첫 공백으로 분리(선두 토큰이 바코드꼴이 아니어도 현행 동작 유지 — 포맷 드리프트에
    #    대해 상품명을 버리지 않는 쪽이 안전하다).
    barcode, sep, name = s.partition(" ")
    if sep:
        return barcode.strip(), (name.strip() or None)
    # ③ 공백도 없고 바코드 런도 못 떼어냄 → 바코드만 있는 셀이면 정상, 아니면 소리내어 남긴다.
    if not _BARCODE_ONLY_RE.match(s):
        warn_once("nosplit",
                  "rocket 발주상세 파서: 바코드/상품명 분리 불가(공백·바코드꼴 없음) 셀=%r → 전체를 barcode로",
                  s[:60])
    return s, None


def parse_po_item_rows(rows: list[list[str]]) -> list[dict]:
    """발주상세 Table[7] DOM rows → per-SKU 라인아이템 레코드[].

    rows = JS DOMParser가 추출한 셀 텍스트 배열(헤더/연속/합계행 혼재). 위치 기반(헤더 병합셀).
    SKU 행 = len>=12 AND 순번·상품번호 모두 숫자. 바코드는 셀 첫 토큰(EAN 숫자 또는 R-내부코드).
    머니 검산(soft): 매입가×발주수량 ≠ 발주금액이면 warning(드롭 안 함 — 수집은 보존, 검증은 reconcile).
    """
    records: list[dict] = []
    split_warned: set[str] = set()  # 바코드 분리 이상 warning — 파싱 1회당 종류별 1줄
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 12:
            continue
        if not (_is_digits(row[0]) and _is_digits(row[1])):
            continue  # 헤더·합계·연속행 방어
        barcode, name = _split_barcode_name(row[_PO_ITEM_COL["barcode_name"]], split_warned)
        rec = {
            "line_no": _to_int(row[_PO_ITEM_COL["line_no"]]),
            "product_number": str(row[_PO_ITEM_COL["product_number"]] or "").strip(),
            "barcode": barcode,
            "product_name": name,
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
