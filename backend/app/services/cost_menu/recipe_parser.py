"""원가 정본 엑셀 → 레시피 구성 «초안» 파싱 — 순수 SA (계약 A′ S2, D-CPP-53).

이 모듈은 DB도 IO도 모른다. 입력은 «시트의 행 목록»(값 튜플), 출력은 값 객체다.
엑셀 파일을 여는 것은 호출자(라우터)의 일이다 — 그래야 테스트가 파일 없이 돈다.

## ★이 파서가 «하지 않는» 것 (계약 §3 금지선)

**단가를 유입하지 않는다.** 파싱 대상은 **구성(부자재 목록·수량)뿐**이다. 엑셀의 숫자는
`excel_ref_price`에 **참고값**으로 실려 화면에 보이기만 하고, 저장되는 단가는 원장 파생이거나
Jino가 입력·승인한 값뿐이다. 그래서 이 모듈의 출력 타입 이름이 `...Draft`다 — 확정이 아니다.

**판정하지 않는다.** 이상(값 충돌·중복 품목·라벨 불일치)은 `anomalies`에 실어 올려보내고
승인 화면에서 사람이 처분한다(계약 §5-3). 자동으로 고르거나 버리지 않는다.

## 시트 구조 — 실측(2026-08-23, 『MD_원가 계산_Jino_260822_Claude.xlsx』 「제품 원가표」 129행)

두 가지 레이아웃이 섞여 있고 **둘 다 지원해야 한다**:

    (a) 섹션 제목 행 → 「품목」 헤더 행 → 데이터        예: 「모바일 필름-아이폰,갤럭시」(20~27행)
    (b) 섹션 제목이 곧 헤더 행                          예: 「모바일 필름-플립」(29~39행)

판별자는 **col3(4번째 칸)에 「제품원가」가 들어 있는 행 = 헤더 행**이다. 섹션 이름은 그 행의
col1이 「품목」이 아니면 그것이고, 「품목」이면 바로 위 비어 있지 않은 col1이다.

## 구성 열의 해석 — 3연칸(triple)과 낱칸

필름류는 **(`{부위}매입`, `{부위}필름`, `{부위}필름*매입`)** 3연칸으로 나온다:
- `매입` = **매수**(수량) · `필름` = 단가 · `필름*매입` = 둘의 곱(유도값 — 읽지 않는다)
- 부위는 빈 문자열(일반)·`내부`·`외부`·`후면`·`힌지`

나머지 열은 전부 낱칸이고 **수량 1**이다(부착 안내문·부자재(밀대외)·알콜솜 2EA·비닐·패키지·
폼텍 스티커·부착 지그·스퀴즈·하드보드지·자 등).

⚠️ `필름*매입`을 «`매입`으로 끝나는 열»보다 **먼저** 걸러야 한다 — 안 그러면 유도값이 수량으로
읽힌다. 이 순서가 이 파서에서 가장 깨지기 쉬운 자리다.

## 검산 — 왜 이 파서가 옳다고 말할 수 있나

행 22 「지문방지필름 TPU 3매」(일반 `bar`):
  필름 600×3=1800 + 30 + 22 + 60 + 8 + 13 + 98 + 6 + 100 = **2,137**  ⇒ ×1.1 = **2,350.70**
엑셀 col3 「제품원가(+VAT)」 = **2350.7000000000003** — 일치한다(부동소수 꼬리는 엑셀 것이다).
그래서 파서는 라인 합 × 1.1을 col3와 대조해 **어긋나면 이상으로 올린다**(`total_mismatch`) —
검산이 통과하도록 값을 맞추는 게 아니라, 안 맞으면 사람에게 보여준다.

## 폼팩터

섹션 이름 → 폼팩터는 **실측 도출 표**(`SECTION_FORM_FACTOR`)로만 정한다. 표에 없는 섹션은
`None`(폼팩터 없음)이고 `unknown_section` 이상이 붙는다 — 파서가 새 폼팩터 값을 만들어내지
않는다(계약 §5-1: 「새 값은 화면 승인 경로로만 늘어난다」).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

VAT_MULTIPLIER = Decimal("1.1")  # standard_cost와 같은 승수(D-NAO-150 규약)

_ZERO = Decimal("0")
#: 라인 합 × 1.1 과 엑셀 「제품원가(+VAT)」의 허용 오차. 엑셀 부동소수 꼬리(…0003)만 흡수한다.
TOTAL_TOLERANCE = Decimal("0.05")

#: 섹션 이름 → 폼팩터. **실측 도출**(계약 §5-1) — 발명 금지, 표에 없으면 None이다.
SECTION_FORM_FACTOR: dict[str, Optional[str]] = {
    "모바일 필름-아이폰,갤럭시": "bar",
    "모바일 필름-플립": "flip",
    "모바일 필름-폴드": "fold",
    "모바일 필름-트라이 폴드": "trifold",
    "태블릿 필름": "tablet",          # 3구간은 품목명 접미사가 가른다(S3 몫)
    "도어락필름": "doorlock",
    "버디필름": "buddy",
}

#: 폼팩터가 «없음»인 것이 정상인 섹션 — 수입 완제품·매입품(계약 §5-1). 이상으로 올리지 않는다.
NO_FORM_FACTOR_SECTIONS: frozenset[str] = frozenset(
    {
        "오타오_기타 액세서리",
        "오타오_강화유리필름",
        "케이스",
        "오픽스 맥세이프 거치대",
        "셀카봉",
        "카드케이스",
    }
)

#: 이 섹션들은 조립형이 아니다 — 원장/매입가에서 단가가 직접 온다(계약 §5-1 ★매입품 결정).
IMPORTED_SECTIONS: frozenset[str] = frozenset({"오타오_강화유리필름"})

#: ★구성 라인을 «뽑는» 섹션은 이것뿐이다 — 폼팩터가 있는 조립형.
#:
#: 왜 나누나: 수입 완제품·매입품 섹션의 열은 부자재가 아니라 **계수**다(제품원가(CNY)·환율·
#: 물류비·관세·부가세·상품원가). 그걸 낱칸 부자재로 읽으면 「관세 0.056원짜리 부자재」 같은
#: 헛것이 생기고 총액 검산이 통째로 어긋난다(실측 2026-08-23: 그렇게 읽었더니 강화유리
#: 6,097원≠12.2 꼴의 total_mismatch가 8건 났다 — 파서가 만든 가짜 이상이었다).
#: 이 섹션들의 단가는 원장 파생(계약 B)이거나 Jino 수동 입력이고 **S3 몫**이다.
ASSEMBLY_SECTIONS: frozenset[str] = frozenset(SECTION_FORM_FACTOR)

_HEADER_MARK = "제품원가"
_ITEM_HEADER = "품목"


def _norm(value: Any) -> str:
    """헤더·라벨 정규화 — 엑셀 헤더에는 줄바꿈이 들어 있다(예: `부착\\n안내문`)."""

    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _dec(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(round(float(value), 4)))
    text = _norm(value).replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except Exception:
        return None


# ──────────────────────────────────────────────
# 값 객체
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class MaterialKey:
    """부자재 «종»의 신원 — (폼팩터, 부위, 라벨).

    ★왜 폼팩터로 가르나(계약 §5-1 ★원단 결정): 같은 라벨이라도 폼팩터가 다르면 규격이 다른
    **다른 물건**이다(실측: 패키지가 일반 98 · 폴드 370 · 태블릿 550). 라벨 하나로 합치면 서로
    다른 물건의 단가가 한 종 아래 섞인다.

    ★필름은 **품목명까지** 신원에 넣는다 — 같은 `bar` 섹션 안에서도 필름 단가가 품목마다
    갈리기 때문이다(실측: 600·600·1650·1600·650·800). 이것이 「종을 쪼갠다」의 실제 근거다.
    """

    form_factor: Optional[str]
    part: Optional[str]          # 내부/외부/후면/힌지 — 낱칸이면 None
    label: str

    @property
    def display_name(self) -> str:
        bits = [b for b in (self.form_factor, self.part) if b]
        return f"{self.label} ({' · '.join(bits)})" if bits else self.label


@dataclass(frozen=True)
class RecipeLineDraft:
    """구성 한 줄의 초안. `excel_ref_price`는 **참고값**이지 단가가 아니다(§3 금지선)."""

    key: MaterialKey
    quantity: Decimal
    excel_ref_price: Optional[Decimal]
    is_film: bool = False
    source_column: str = ""

    @property
    def excel_ref_amount(self) -> Optional[Decimal]:
        if self.excel_ref_price is None:
            return None
        return self.excel_ref_price * self.quantity


@dataclass(frozen=True)
class RecipeDraft:
    """「섹션 × 품목」 1건의 구성 초안.

    상품명은 **아직 붙지 않는다** — 원가표는 «품목»으로 적혀 있고 상품명은 매핑 정본에 있다.
    둘을 잇는 것은 `matcher` 층이고 확정은 사람이다(계약 §2-2).
    """

    section: str
    item_name: str
    form_factor: Optional[str]
    recipe_kind: str                       # assembly / imported_goods
    lines: tuple[RecipeLineDraft, ...]
    excel_total_inc_vat: Optional[Decimal]  # 엑셀 「제품원가(+VAT)」 — 참고·대조용
    row_number: int
    anomalies: tuple[str, ...] = ()

    @property
    def computed_ex_vat(self) -> Optional[Decimal]:
        """엑셀 참고값으로 계산한 라인 합 — **대조용**이지 저장할 원가가 아니다."""

        total = _ZERO
        for line in self.lines:
            amount = line.excel_ref_amount
            if amount is None:
                return None
            total += amount
        return total


@dataclass
class ParseResult:
    recipes: list[RecipeDraft] = field(default_factory=list)
    materials: list[MaterialKey] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)
    sections_seen: list[str] = field(default_factory=list)

    @property
    def recipe_count(self) -> int:
        return len(self.recipes)


# ──────────────────────────────────────────────
# 열 해석
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class _ColumnSpec:
    index: int
    label: str
    role: str          # qty / price / derived / flat
    part: Optional[str]


def _classify_columns(header: Sequence[Any], start: int) -> list[_ColumnSpec]:
    """헤더 행 → 열 역할. ⚠️`필름*매입`(유도값)을 «매입»보다 먼저 걸러야 한다."""

    specs: list[_ColumnSpec] = []
    for idx in range(start, len(header)):
        label = _norm(header[idx])
        if not label:
            continue
        if label.endswith("필름*매입"):
            part = label[: -len("필름*매입")].strip() or None
            specs.append(_ColumnSpec(idx, label, "derived", part))
        elif label.endswith("매입"):
            part = label[: -len("매입")].strip() or None
            specs.append(_ColumnSpec(idx, label, "qty", part))
        elif label.endswith("필름"):
            part = label[: -len("필름")].strip() or None
            specs.append(_ColumnSpec(idx, label, "price", part))
        else:
            specs.append(_ColumnSpec(idx, label, "flat", None))
    return specs


def _is_header_row(row: Sequence[Any]) -> bool:
    return len(row) > 3 and _HEADER_MARK in _norm(row[3])


# ──────────────────────────────────────────────
# 파싱
# ──────────────────────────────────────────────
def parse_cost_table(rows: Iterable[Sequence[Any]]) -> ParseResult:
    """「제품 원가표」 시트의 행 목록 → 레시피 초안.

    `rows`는 `openpyxl`의 `iter_rows(values_only=True)` 결과 형태다(파일은 호출자가 연다).
    """

    result = ParseResult()
    section: Optional[str] = None
    last_label: Optional[str] = None
    specs: list[_ColumnSpec] = []
    seen_keys: dict[MaterialKey, Decimal] = {}
    seen_items: dict[tuple[str, str], tuple] = {}

    for row_number, raw in enumerate(rows, start=1):
        row = list(raw)
        col1 = _norm(row[1]) if len(row) > 1 else ""

        if _is_header_row(row):
            if col1 and col1 != _ITEM_HEADER:
                section = col1
            elif last_label:
                section = last_label
            if section and section not in result.sections_seen:
                result.sections_seen.append(section)
            specs = _classify_columns(row, start=4)
            continue

        total = _dec(row[3]) if len(row) > 3 else None
        if col1 and total is None:
            last_label = col1          # 섹션 제목 후보(데이터가 아닌 이름 행)
            continue

        if not col1 or total is None or section is None:
            continue

        result.recipes.append(
            _build_recipe(
                section=section,
                item_name=col1,
                total=total,
                row=row,
                specs=specs,
                row_number=row_number,
                seen_keys=seen_keys,
                seen_items=seen_items,
                result=result,
            )
        )

    result.materials = list(seen_keys.keys())
    return result


def _build_recipe(
    *,
    section: str,
    item_name: str,
    total: Decimal,
    row: list[Any],
    specs: list[_ColumnSpec],
    row_number: int,
    seen_keys: dict[MaterialKey, Decimal],
    seen_items: dict[tuple[str, str], tuple],
    result: ParseResult,
) -> RecipeDraft:
    anomalies: list[str] = []

    form_factor = SECTION_FORM_FACTOR.get(section)
    if form_factor is None and section not in NO_FORM_FACTOR_SECTIONS:
        anomalies.append(f"unknown_section:{section}")
    recipe_kind = "imported_goods" if section in IMPORTED_SECTIONS else "assembly"

    if section not in ASSEMBLY_SECTIONS:
        # 조립형이 아니다 — 열이 부자재가 아니라 계수다. 헤더만 만들고 라인은 비운다.
        # 「구성이 비었다」는 사실이 그대로 화면에 가고, 단가는 S3에서 원장·수동으로 붙는다.
        anomalies.append("needs_manual_lines")
        draft = RecipeDraft(
            section=section,
            item_name=item_name,
            form_factor=form_factor,
            recipe_kind=recipe_kind,
            lines=(),
            excel_total_inc_vat=total,
            row_number=row_number,
            anomalies=tuple(anomalies),
        )
        result.anomalies.append(
            f"{section}/{item_name}(행{row_number}): {', '.join(draft.anomalies)}"
        )
        return draft

    lines: list[RecipeLineDraft] = []
    qty_by_part: dict[Optional[str], Decimal] = {}
    for spec in specs:
        if spec.role == "qty":
            value = _dec(row[spec.index]) if spec.index < len(row) else None
            if value is not None:
                qty_by_part[spec.part] = value

    for spec in specs:
        if spec.role in ("qty", "derived"):
            continue
        value = _dec(row[spec.index]) if spec.index < len(row) else None

        if spec.role == "price":
            quantity = qty_by_part.get(spec.part, _ZERO)
            if quantity <= _ZERO:
                continue          # 그 부위를 안 쓰는 제품이다 — 0매를 라인으로 만들지 않는다
            key = MaterialKey(form_factor, spec.part, f"{item_name} · {spec.label}")
            is_film = True
        else:
            if value is None or value == _ZERO:
                continue          # 빈 칸·0 = 그 부자재를 안 쓴다(«단가 0»이 아니다)
            quantity = Decimal("1")
            key = MaterialKey(form_factor, None, spec.label)
            is_film = False

        prior = seen_keys.get(key)
        if prior is not None and value is not None and prior != value:
            anomalies.append(f"price_conflict:{spec.label}:{prior}≠{value}")
        if value is not None:
            seen_keys.setdefault(key, value)

        lines.append(
            RecipeLineDraft(
                key=key,
                quantity=quantity,
                excel_ref_price=value,
                is_film=is_film,
                source_column=spec.label,
            )
        )

    # ★같은 이름이 두 번 나오는 것과, 같은 이름인데 «구성이 다른» 것은 다른 사실이다.
    #   실측: 태블릿 「지문방지 PET 2매_기본」은 매입 2벌·1벌 두 섹션에 있고(1매·2매 제품 두 종,
    #   계약 §9-4에서 «이상 아님»으로 닫힘), 폴드 「지문방지_내부3매+외부3매」는 행 42와 105가
    #   내부 필름 1000≠700으로 갈린다(§9-9 이상① — Jino 답 대기). 앞은 정상, 뒤는 미결이다.
    #   둘을 한 이름으로 부르면 화면에서 처분이 갈리지 않으므로 라벨을 나눈다.
    signature = tuple((ln.key, ln.quantity, ln.excel_ref_price) for ln in lines)
    prior_signature = seen_items.get((section, item_name))
    if prior_signature is not None:
        anomalies.append(
            "duplicate_item" if prior_signature == signature else "variant_item"
        )
    else:
        seen_items[(section, item_name)] = signature

    # 총액 검산 — 검산이 통과하도록 값을 맞추지 않는다. 안 맞으면 사람에게 보여준다.
    computed: Optional[Decimal] = _ZERO
    for line in lines:
        amount = line.excel_ref_amount
        if amount is None:
            computed = None
            break
        computed += amount

    if not lines:
        anomalies.append("empty_lines")
    elif computed is None:
        anomalies.append("total_unverifiable")
    elif abs(computed * VAT_MULTIPLIER - total) > TOTAL_TOLERANCE:
        anomalies.append(f"total_mismatch:{computed * VAT_MULTIPLIER:.2f}≠{total}")

    draft = RecipeDraft(
        section=section,
        item_name=item_name,
        form_factor=form_factor,
        recipe_kind=recipe_kind,
        lines=tuple(lines),
        excel_total_inc_vat=total,
        row_number=row_number,
        anomalies=tuple(anomalies),
    )

    if draft.anomalies:
        result.anomalies.append(f"{section}/{item_name}(행{row_number}): {', '.join(draft.anomalies)}")
    return draft
