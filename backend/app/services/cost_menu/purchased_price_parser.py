"""매입 완제품 단가 시트 → 단가 «제안» 파싱 — 순수 SA (계약 D-CPP-63 S1).

`mapping_parser`·`recipe_parser`와 같은 규율이다: DB도 IO도 모르고, 판정하지 않고,
제안까지만 한다.

## 이 파일이 푸는 문제

매입 완제품(케이스·스트랩·렌즈·충전기 등 318 SKU)의 단가는 우리 시스템 어디에도 없다.
조립품은 「구성 × 부자재 단가」로 계산되지만 매입품은 **사서 파는 물건**이라 구성이 없다
(prod 실측: 매입품 초안 50건 전부 `cost_recipe_line` 0줄). 그 단가가 있는 유일한 곳이
원가 매핑 정본의 **`원가` 열**이다.

## ★★열을 «이름»으로 찾는다 — 위치로 읽지 않는다 (계약 §3 금지선)

같은 폴더에 사는 두 판의 **B열이 서로 다르다**(2026-08-28 실측):

    08-07판:  0 상품명(제품+옵션 통합) · 1 **원가** · 2~ 채널명/코드…
    08-22판:  0 상품명(제품만)        · 1 **옵션명** · 2~ 채널명/코드…   ← `원가` 열 자체가 없다

채널 코드 열들의 채움율은 두 판이 **동일**하다 — 즉 08-22판은 「원가가 빠진 판」이 아니라
상품명/옵션명을 분리한 **다른 목적의 판**이다. 위치로 읽으면 08-07의 `1956`(원가)을
옵션명으로, 08-22의 `아이폰16플러스`(옵션명)를 원가로 읽는다.

**이 사고는 가설이 아니다** — 계약 저술 중 실측 스크립트가 정확히 후자를 밟아 08-22판을
「원가 0행 빈 파일」로 오독했고, 그 거짓 위에 「정본은 08-07판」이라는 계약 문단이 섰다.
Jino의 *"무슨 정본이 8/7자야?"*가 잡았다. ⇒ `원가` 열이 **이름으로** 안 잡히면 **거부한다.**
「없는 열을 0으로 읽는 것」보다 「이 판에는 원가가 없다」고 말하는 편이 언제나 낫다.

## ★`1`원은 값이 아니라 공백이다 (계약 §2·§3)

파일이 「아직 단가를 모른다」를 `1`로 적어 둔 행이 실측 36행 있다(시스루 케이스 6색 28 ·
하이톡 스마트톡 6 · 필름 2). 이것을 단가로 적재하면 **「시스템이 추측한 단가 저장 금지」를
파일의 손을 빌려** 어기는 것이고, 화면에서 「없음」과 「1원」이 같은 얼굴이 된다.
⇒ 파서가 그 자리에서 갈라 `is_placeholder`로 실어 보낸다. 판정이 아니라 **분류**다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional, Sequence

#: 헤더에서 찾을 열 이름. 「이름으로 찾는다」가 이 모듈의 금지선이라 상수는 **라벨**이지
#: 위치가 아니다. 여러 표기를 허용하되, 못 찾으면 **폴백하지 않고 거부**한다.
NAME_LABELS: tuple[str, ...] = ("상품명",)
PRICE_LABELS: tuple[str, ...] = ("원가",)

#: 코드 열에서 제외할 라벨 — 「채널명」은 코드가 아니라 구분자이고, 「옵션명」은 다른 판의
#: B열이라 이 시트에 함께 있을 수도 있다.
_NON_CODE_LABELS: frozenset[str] = frozenset({"채널명", "옵션명"})

#: 이 값 «이하»는 단가가 아니라 자리표시자로 본다. 파일이 실제로 쓰는 값은 `1`이지만
#: `0`·음수도 단가일 수 없으므로 같은 자리에서 잡는다.
PLACEHOLDER_MAX = Decimal("1")


class PriceSheetError(ValueError):
    """이 시트로는 단가를 읽을 수 없다 — 원가 열이 없거나 헤더가 없다."""


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _find_column(header: Sequence[Any], labels: Sequence[str]) -> Optional[int]:
    """헤더에서 라벨과 «정확히» 일치하는 열의 인덱스.

    부분 일치를 쓰지 않는다 — 「원가」로 부분 일치를 걸면 「원가 매핑」·「원가율」 같은 열이
    함께 걸려 어느 것이 단가인지 조용히 갈린다.
    """

    for idx, raw in enumerate(header):
        if _norm(raw) in labels:
            return idx
    return None


def detect_columns(header: Sequence[Any]) -> tuple[int, int, tuple[int, ...]]:
    """헤더 행 → (상품명 열, 원가 열, 채널코드 열들).

    ★`원가` 열이 없으면 `PriceSheetError`다. 폴백 상수를 두지 않는 것이 이 함수의 요지다
    (모듈 docstring의 실사고 참조).
    """

    name_col = _find_column(header, NAME_LABELS)
    if name_col is None:
        raise PriceSheetError(
            "「상품명」 열을 헤더에서 찾지 못했다 — 이 시트는 원가 매핑 시트가 아니다."
        )
    price_col = _find_column(header, PRICE_LABELS)
    if price_col is None:
        raise PriceSheetError(
            "「원가」 열이 이 판에는 없다 — 08-22판처럼 상품명/옵션명만 있는 판일 수 있다. "
            "원가 열을 가진 판을 올려라(열 위치로 읽지 않는다)."
        )

    codes: list[int] = []
    for idx, raw in enumerate(header):
        if idx in (name_col, price_col):
            continue
        label = _norm(raw)
        if not label or label in _NON_CODE_LABELS:
            continue
        codes.append(idx)
    return name_col, price_col, tuple(codes)


def _to_decimal(value: Any) -> Optional[Decimal]:
    """엑셀 셀 → Decimal. 숫자가 아니면 None(=읽지 못했다)."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    text = _norm(value).replace(",", "").replace("₩", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


@dataclass(frozen=True)
class PriceRow:
    """단가 시트 한 행 = 판매 옵션 1건의 매입가 «제안»."""

    #: 파일의 상품명 원문. 08-07판에서는 제품명과 옵션명이 한 칸에 뭉쳐 있다
    #: (예: `…필름 2매, 아이폰16플러스`). 자르지 않고 **원문 그대로** 싣는다 —
    #: 자르는 규칙을 파서가 정하면 그것이 곧 판정이고, 이 모듈은 판정하지 않는다.
    product_name: str
    #: 자리표시자면 None이다. 「없음」과 「1원」을 여기서 이미 가른다.
    price: Optional[Decimal]
    #: 원문 값 — 자리표시자 판정의 근거를 화면이 보여줄 수 있어야 한다.
    raw_price: Optional[Decimal]
    is_placeholder: bool
    channel_codes: tuple[str, ...]
    row_number: int


@dataclass
class PriceParseResult:
    rows: list[PriceRow] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)
    name_col: int = -1
    price_col: int = -1
    #: 화면이 「어느 열을 읽었나」를 표시하기 위한 헤더 원문(계약 §3 금지선의 표면).
    name_label: str = ""
    price_label: str = ""

    @property
    def priced(self) -> list[PriceRow]:
        return [r for r in self.rows if r.price is not None]

    @property
    def placeholders(self) -> list[PriceRow]:
        return [r for r in self.rows if r.is_placeholder]

    # ★«한 클릭» 묶음(계약 §7 답 1)을 여기서 만들지 않는다 — 이 판의 `상품명`은 제품명과
    #   옵션명이 한 칸에 뭉쳐 있어(`…필름 2매, 아이폰16플러스`) 행마다 다른 이름이 되고,
    #   실측하면 묶음이 904개로 갈려 «한 클릭»이 성립하지 않는다(944행 → 904묶음).
    #   쉼표로 자르는 것도 답이 아니다: 제품명 자체에 쉼표가 있다(`오하이 빛반사, 지문방지…`).
    #   묶음의 올바른 키는 **레시피 × 단가**이고 레시피는 DB만 안다 ⇒ 서비스층의 일이다.
    #   판정하지 않는다는 이 모듈의 규율이 곧 그 경계다.


def parse_price_sheet(rows: Iterable[Sequence[Any]]) -> PriceParseResult:
    """「원가 매핑」 시트의 행 목록 → 단가 행. 첫 행은 헤더로 소비한다.

    ★`원가` 열이 없으면 `PriceSheetError`를 던진다 — 조용히 0건을 돌려주지 않는다.
    발견 0건과 「읽을 수 없었다」는 다른 사실이다(교훈 #123).
    """

    result = PriceParseResult()
    header_seen = False
    name_col = price_col = -1
    code_columns: tuple[int, ...] = ()

    for row_number, raw in enumerate(rows, start=1):
        row = list(raw)
        if not header_seen:
            name_col, price_col, code_columns = detect_columns(row)
            result.name_col, result.price_col = name_col, price_col
            result.name_label = _norm(row[name_col])
            result.price_label = _norm(row[price_col])
            header_seen = True
            continue

        product_name = _norm(row[name_col]) if len(row) > name_col else ""
        if not product_name:
            continue

        raw_price = _to_decimal(row[price_col]) if len(row) > price_col else None
        if raw_price is None:
            result.anomalies.append(f"행{row_number} {product_name}: 원가 칸이 비었거나 숫자가 아니다")
            is_placeholder, price = True, None
        elif raw_price <= PLACEHOLDER_MAX:
            is_placeholder, price = True, None
        else:
            is_placeholder, price = False, raw_price

        codes: list[str] = []
        for idx in code_columns:
            if idx >= len(row):
                continue
            code = _norm(row[idx])
            if code and code not in codes:
                codes.append(code)

        result.rows.append(
            PriceRow(
                product_name=product_name,
                price=price,
                raw_price=raw_price,
                is_placeholder=is_placeholder,
                channel_codes=tuple(codes),
                row_number=row_number,
            )
        )

    if not header_seen:
        raise PriceSheetError("시트가 비었다 — 헤더 행조차 없다.")
    return result
