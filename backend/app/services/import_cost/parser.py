"""수입 통관 서류 3종(CI·PL·통관경비서) 파서 — 순수 SA (D-CPP-48).

이 모듈은 **DB도 IO도 FastAPI도 모른다.** 입력은 bytes/str, 출력은 값 객체다.
정본은 사람이 확인한 폼 데이터고 파싱은 채워주기 편의일 뿐이다.

⚠️ **이 모듈은 아직 «미배선»이다 — 어떤 라우터·프론트·테스트도 임포트하지 않는다**
(적대 리뷰 P2-2, 2026-08-22 실측: `grep -rn "import_cost.parser\|from .parser" ` 0건).
초판 docstring은 *"라우터가 `CustomsDocParseError`를 잡아 빈 폼을 연다"*고 적었는데
**그런 라우터 코드는 없다** — 사실이 아닌 배선을 주장하는 문서였다.
현재 계약 B(`docs/PLAN_import-cost-ledger.md`)의 화면은 **수기 폼만** 지원하고, 업로드는
원본 보관용이다. 이 파서를 붙이는 것은 다음 슬라이스이며, 그때 배선과 함께 테스트를 붙인다.
그전까지 이 파일은 «검증된 로직이 대기 중»이지 «도는 코드»가 아니다.

## .xls(BIFF8)가 아니라 .xlsx만 읽는 이유 (§3 판단)
이 환경엔 `xlrd`가 없고(설치 금지 — 실측: `python3 -c "import xlrd"` → ModuleNotFoundError),
`openpyxl`은 .xls를 못 읽는다. BIFF8을 직접 파싱하려면 ①OLE2 복합문서(CFB) 컨테이너에서
`Workbook` 스트림을 꺼내는 층(FAT/미니FAT 체인 추적)과 ②BIFF 레코드 스트림에서 SST(공유
문자열, CONTINUE 레코드로 8,224바이트 넘게 이어지는 경우 포함)·RK·MULRK·NUMBER·FORMULA
캐시값을 복원하는 층이 각각 따로 필요하다 — 이 둘을 최소로 짜도 250~350줄 규모가 되어
계약이 정한 200줄 상한을 넘긴다(150줄 넘으면 xlsx 전용으로 강등하라는 지시에 해당).
그래서 **업로드 경로를 xlsx로 강제**한다 — 사람이 엑셀에서 "다른 이름으로 저장 → xlsx"
한 번이면 되고, 파일을 안 읽는 것보다 훨씬 싸다. 시트명(`CI`/`PL`)은 원본과 동일하다고
가정한다(엑셀이 .xls→.xlsx 변환 시 시트명을 보존하는 것은 실측으로 확인했다 — 2026-08-22,
`Microsoft Excel.app`로 8/17 선적분 실파일 변환).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any

from openpyxl import load_workbook

from .allocator import CostLine, InvoiceLine

_ZERO = Decimal("0")
_EXCEL_EPOCH = datetime(1899, 12, 30)  # 엑셀 1900 날짜계 윤년버그를 흡수하는 표준 기준일


class CustomsDocParseError(ValueError):
    """서류 파싱 실패. 메시지에 **어느 시트·행에서 무엇을 못 읽었는지**를 담는다.

    이 예외를 잡는 쪽(라우터)은 폼을 빈 값으로라도 열어야 한다 — 파서 실패가
    시스템 실패가 되면 안 된다는 게 계약의 금지선이다.
    """


# ──────────────────────────────────────────────
# 값 객체
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class InvoiceParseResult:
    lines: list[InvoiceLine]
    order_nos: list[str | None]  # 라인별 forward-fill 결과. len == len(lines)
    invoice_no: str | None
    invoice_date: date | None
    declared_total: Decimal | None  # G30 — 검산용, 값을 고치지 않는다
    declared_qty: Decimal | None  # E30
    line_total_mismatches: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class PackingLine:
    """Packing List 한 줄. allocator/reconciler엔 이 값 객체가 없어 여기서 정의한다.

    ★박스를 공유하는 라인은 qty_per_carton·carton_count·gross_weight_kg·measure·cbm이
    그 박스의 첫 행에만 원본값을 갖고 나머지는 `None`이다(병합셀을 그대로 보존한 것 —
    forward-fill하지 않는다). 배분값은 `distribute_box_metrics`가 **별도로** 만든다.
    """

    seq: int
    carton_range: str | None
    item_name: str
    quantity: Decimal
    qty_per_carton: Decimal | None
    carton_count: Decimal | None
    gross_weight_kg: Decimal | None
    measure: str | None
    cbm: Decimal | None
    remark: str | None


@dataclass(frozen=True)
class PackingParseResult:
    lines: list[PackingLine]
    total_qty: Decimal | None
    total_gross_weight_kg: Decimal | None
    total_cbm: Decimal | None
    total_cartons: Decimal | None


@dataclass(frozen=True)
class ExpenseParseResult:
    cost_lines: list[CostLine]
    hbl_no: str | None
    declaration_no: str | None
    declaration_date: date | None
    eta: date | None
    shipper_name: str | None
    vessel: str | None
    fx_rate: Decimal | None
    declared_inv_value: Decimal | None
    currency: str | None
    customs_value_krw: Decimal | None
    carton_count: int | None
    gross_weight_kg: Decimal | None
    cbm: Decimal | None


# ──────────────────────────────────────────────
# 공용 변환 헬퍼 — 못 읽으면 None이지 추정값을 만들지 않는다
# ──────────────────────────────────────────────
def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        if not s:
            return None
        try:
            return Decimal(s)
        except InvalidOperation:
            return None
    return None


def _require_decimal(value: Any, ctx: str) -> Decimal:
    d = _to_decimal(value)
    if d is None:
        raise CustomsDocParseError(f"{ctx}: 숫자를 못 읽음(원본값={value!r})")
    return d


def _to_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _excel_date(value: Any) -> date | None:
    """엑셀 날짜 셀 → date. datetime(엑셀이 서식을 인식해 이미 변환한 경우)과
    순수 시리얼 숫자(변환기를 안 거친 xlsx) 둘 다 받는다 — 추정하지 않고 둘 다 실측 형태다."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        serial = float(value)
    except (TypeError, ValueError):
        return None
    return (_EXCEL_EPOCH + timedelta(days=serial)).date()


def _load_sheet(data: bytes, sheet_name: str):
    try:
        wb = load_workbook(BytesIO(data), data_only=True)
    except Exception as exc:  # openpyxl이 던지는 예외 종류가 다양해 포괄한다
        raise CustomsDocParseError(
            f"유효한 .xlsx가 아니다({exc}). .xls(BIFF8)는 이 파서가 못 읽는다 — "
            "업로드 전 엑셀에서 '다른 이름으로 저장 → xlsx(Excel 통합 문서)'로 변환해야 한다."
        ) from exc
    if sheet_name not in wb.sheetnames:
        raise CustomsDocParseError(f"시트 '{sheet_name}'이 없다 — 워크북 시트: {wb.sheetnames}")
    return wb[sheet_name]


# ──────────────────────────────────────────────
# §1 Commercial Invoice
# ──────────────────────────────────────────────
def parse_commercial_invoice(data: bytes) -> InvoiceParseResult:
    """CI 시트를 파싱한다. 헤더 행('Order No.')을 찾은 뒤 'Total'이 나올 때까지 읽는다.

    B열(Order No.)은 병합셀이라 데이터 라인마다 forward-fill한다. G열(Total)은
    quantity×unit_price 검산에만 쓰고, 어긋나도 값을 고치지 않고 리포트에 담는다.
    """
    ws = _load_sheet(data, "CI")

    header_row = None
    for r in range(1, 21):
        v = ws.cell(row=r, column=2).value
        if isinstance(v, str) and "order no" in v.strip().lower():
            header_row = r
            break
    if header_row is None:
        raise CustomsDocParseError("CI 시트: 헤더 행('Order No.')을 20행 안에서 못 찾음")

    invoice_no = _to_str(ws.cell(row=10, column=7).value)  # G10
    invoice_date = _excel_date(ws.cell(row=9, column=7).value)  # G9

    lines: list[InvoiceLine] = []
    order_nos: list[str | None] = []
    mismatches: list[dict] = []
    declared_total: Decimal | None = None
    declared_qty: Decimal | None = None

    seq = 0
    current_order: str | None = None
    max_row = header_row + 500
    row = header_row + 1
    while row <= max_row:
        raw_b = ws.cell(row=row, column=2).value
        if isinstance(raw_b, str) and "total" in raw_b.strip().lower():
            declared_qty = _to_decimal(ws.cell(row=row, column=5).value)
            declared_total = _to_decimal(ws.cell(row=row, column=7).value)
            break
        item_name = _to_str(ws.cell(row=row, column=4).value)
        qty_raw = ws.cell(row=row, column=5).value
        if item_name is None and qty_raw is None:
            row += 1
            continue  # 완전 공백 행 — 총계 행을 아직 못 만났으니 계속 본다
        if item_name is None:
            raise CustomsDocParseError(f"CI {row}행: 품명(D열)이 비었는데 수량은 있다.")
        if isinstance(raw_b, str) and raw_b.strip():
            current_order = raw_b.strip()
        seq += 1
        qty = _require_decimal(qty_raw, f"CI {row}행 수량(E열)")
        price = _require_decimal(ws.cell(row=row, column=6).value, f"CI {row}행 단가(F열)")
        lines.append(InvoiceLine(seq=seq, item_name=item_name, quantity=qty, unit_price_foreign=price))
        order_nos.append(current_order)
        declared_line_total = _to_decimal(ws.cell(row=row, column=7).value)
        if declared_line_total is not None:
            computed = qty * price
            if abs(computed - declared_line_total) > Decimal("0.01"):
                mismatches.append(
                    {"seq": seq, "item_name": item_name, "computed": str(computed), "declared": str(declared_line_total)}
                )
        row += 1
    else:
        raise CustomsDocParseError(f"CI 시트: {header_row + 1}~{max_row}행 안에서 총계 행('Total')을 못 찾음")

    return InvoiceParseResult(
        lines=lines,
        order_nos=order_nos,
        invoice_no=invoice_no,
        invoice_date=invoice_date,
        declared_total=declared_total,
        declared_qty=declared_qty,
        line_total_mismatches=mismatches,
    )


# ──────────────────────────────────────────────
# §2 Packing List
# ──────────────────────────────────────────────
def parse_packing_list(data: bytes) -> PackingParseResult:
    """PL 시트를 파싱한다. 헤더 행('Ctn No.')을 찾은 뒤 'TOTAL'이 나올 때까지 읽는다.

    A열(박스 번호)·E~J열(박스 단위 수치)은 박스 공유 시 첫 행에만 값이 있다 — 여기서는
    원본 그대로(forward-fill 없이) 보존하고, 배분은 `distribute_box_metrics`가 한다.
    """
    ws = _load_sheet(data, "PL")

    header_row = None
    for r in range(1, 21):
        v = ws.cell(row=r, column=1).value
        if isinstance(v, str) and "ctn no" in v.strip().lower():
            header_row = r
            break
    if header_row is None:
        raise CustomsDocParseError("PL 시트: 헤더 행('Ctn No.')을 20행 안에서 못 찾음")

    lines: list[PackingLine] = []
    total_qty = total_gw = total_cbm = total_cartons = None

    seq = 0
    max_row = header_row + 500
    row = header_row + 1
    while row <= max_row:
        raw_c = ws.cell(row=row, column=3).value  # C열 = 품명, 총계 행엔 'TOTAL:'
        if isinstance(raw_c, str) and "total" in raw_c.strip().lower():
            total_qty = _to_decimal(ws.cell(row=row, column=4).value)
            total_cartons = _to_decimal(ws.cell(row=row, column=6).value)
            total_gw = _to_decimal(ws.cell(row=row, column=8).value)
            total_cbm = _to_decimal(ws.cell(row=row, column=10).value)
            break
        item_name = _to_str(raw_c)
        qty_raw = ws.cell(row=row, column=4).value
        if item_name is None and qty_raw is None:
            row += 1
            continue
        if item_name is None:
            raise CustomsDocParseError(f"PL {row}행: 품명(C열)이 비었는데 수량은 있다.")
        seq += 1
        qty = _require_decimal(qty_raw, f"PL {row}행 Shipment Qty(D열)")
        lines.append(
            PackingLine(
                seq=seq,
                carton_range=_to_str(ws.cell(row=row, column=1).value),
                item_name=item_name,
                quantity=qty,
                qty_per_carton=_to_decimal(ws.cell(row=row, column=5).value),
                carton_count=_to_decimal(ws.cell(row=row, column=6).value),
                gross_weight_kg=_to_decimal(ws.cell(row=row, column=8).value),  # Total G.W(박스)
                measure=_to_str(ws.cell(row=row, column=9).value),
                cbm=_to_decimal(ws.cell(row=row, column=10).value),  # Total volume(박스)
                remark=_to_str(ws.cell(row=row, column=11).value),
            )
        )
        row += 1
    else:
        raise CustomsDocParseError(f"PL 시트: {header_row + 1}~{max_row}행 안에서 총계 행('TOTAL')을 못 찾음")

    return PackingParseResult(
        lines=lines,
        total_qty=total_qty,
        total_gross_weight_kg=total_gw,
        total_cbm=total_cbm,
        total_cartons=total_cartons,
    )


def distribute_box_metrics(lines: list[PackingLine]) -> list[PackingLine]:
    """박스 단위로만 있는 총중량·총부피를 그룹 내 라인에 **수량 비례**로 나눈다.

    그룹 시작 판정 = `gross_weight_kg`가 값을 가진 행(그 박스의 첫 행). `carton_range`로
    가르지 않는 이유는 continuation 행의 carton_range도 원본에서 비어 있어, 그걸로는
    첫 행 자체를 못 찾기 때문이다. 원본 리스트는 건드리지 않고 새 리스트를 반환한다
    (원본 보존과 파생값을 안 섞기 위해서 — 이 함수를 별도로 둔 이유).
    """
    if not lines:
        return []
    groups: list[list[int]] = []
    for i, ln in enumerate(lines):
        if ln.gross_weight_kg is not None or not groups:
            groups.append([i])
        else:
            groups[-1].append(i)

    out = list(lines)
    for idx_group in groups:
        head = lines[idx_group[0]]
        total_qty = sum((lines[i].quantity for i in idx_group), _ZERO)
        if total_qty <= _ZERO:
            continue  # 수량 0이면 비율을 못 만든다 — 원본(대개 전부 None) 그대로 둔다
        for i in idx_group:
            share = lines[i].quantity / total_qty
            new_weight = head.gross_weight_kg * share if head.gross_weight_kg is not None else None
            new_cbm = head.cbm * share if head.cbm is not None else None
            out[i] = replace(lines[i], gross_weight_kg=new_weight, cbm=new_cbm)
    return out


# ──────────────────────────────────────────────
# §4 통관경비서 (이미 추출된 텍스트)
# ──────────────────────────────────────────────
_COST_LINE_RE = re.compile(r"^(.+?)\s+([\d,]+(?:\.\d+)?)\s*/\s*([\d,]+(?:\.\d+)?)\s*$")


def _grab(text: str, pattern: str, flags: int = 0) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _grab_date(text: str, pattern: str) -> date | None:
    s = _grab(text, pattern)
    if s is None:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _grab_decimal(text: str, pattern: str) -> Decimal | None:
    s = _grab(text, pattern)
    return _to_decimal(s) if s is not None else None


def parse_customs_expense(text: str) -> ExpenseParseResult:
    """통관경비서에서 **이미 추출된 텍스트**를 파싱한다. PDF 추출 자체는 라우터가 한다.

    ★`is_costing` 판정은 「품명이 '부가세'로 시작하는가」 **하나뿐**이다. 이름 매칭 하나에
    의존한다는 걸 여기 명시한다 — 서류 양식이 바뀌면(예: '부가세액'으로 표기) 조용히 깨진다.
    그래서 정본은 이 파서가 아니라 사람이 확인한 폼 데이터다(계약 전문).
    ★소계/합계(`Sub Total`·`소계`·`합계`, 공백이 끼워진 'B/L Sub Total' 류 포함)는 반드시
    제외한다 — 넣으면 통관비가 이중 계상된다.
    """
    hbl_no = _grab(text, r"HBL\s*NO\s+(\S+)")
    declaration_no = _grab(text, r"신고번호\s+(\S+)")
    declaration_date = _grab_date(text, r"신고일자\s+(\d{4}-\d{2}-\d{2})")
    eta = _grab_date(text, r"ETA\s+(\d{4}-\d{2}-\d{2})")
    shipper_name = _grab(text, r"Shipper\s*NM\s+(.+?)\s*(?:·|\n|$)")
    vessel = _grab(text, r"선명\s*/\s*항차\s+(.+?)\s*(?:·|\n|$)")
    fx_rate = _grab_decimal(text, r"환율\s+([\d.]+)")
    customs_value_krw = _grab_decimal(text, r"과세금액\s*\(\s*₩\s*\)\s*([\d,]+)")
    gross_weight_kg = _grab_decimal(text, r"Gross\s+W\s*/\s*T\s+([\d.]+)")

    carton_m = re.search(r"수량\s+(\d+)\s*CARTONS\s*/\s*([\d.]+)", text, re.IGNORECASE)
    carton_count = int(carton_m.group(1)) if carton_m else None
    cbm = _to_decimal(carton_m.group(2)) if carton_m else None

    inv_m = re.search(r"INV\s*Value\s+([A-Z]{3})\s+([\d,.]+)", text)
    currency = inv_m.group(1) if inv_m else None
    declared_inv_value = _to_decimal(inv_m.group(2)) if inv_m else None

    cost_lines: list[CostLine] = []
    for raw_line in text.splitlines():
        m = _COST_LINE_RE.match(raw_line.strip())
        if not m:
            continue
        name = m.group(1).strip()
        compact = re.sub(r"\s+", "", name)
        if "subtotal" in compact.lower() or "소계" in compact or "합계" in compact:
            continue  # 소계·합계 행 — 데이터가 아니다
        supply = _to_decimal(m.group(2)) or _ZERO
        tax = _to_decimal(m.group(3)) or _ZERO
        is_costing = not compact.startswith("부가세")
        cost_lines.append(CostLine(item_name=name, supply_amount=supply, tax_amount=tax, is_costing=is_costing))

    if not cost_lines:
        raise CustomsDocParseError(
            "통관경비서 텍스트에서 비용 라인을 하나도 못 찾음 — "
            "'항목 금액 / 세액' 꼴의 줄이 있는지, 소계·합계만 있던 건 아닌지 확인해야 한다."
        )

    return ExpenseParseResult(
        cost_lines=cost_lines,
        hbl_no=hbl_no,
        declaration_no=declaration_no,
        declaration_date=declaration_date,
        eta=eta,
        shipper_name=shipper_name,
        vessel=vessel,
        fx_rate=fx_rate,
        declared_inv_value=declared_inv_value,
        currency=currency,
        customs_value_krw=customs_value_krw,
        carton_count=carton_count,
        gross_weight_kg=gross_weight_kg,
        cbm=cbm,
    )
