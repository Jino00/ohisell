"""매핑 정본 엑셀 → SKU 링크 «초안» 파싱 — 순수 SA (계약 A′ S2, §0-B).

`recipe_parser`와 같은 규율이다: DB도 IO도 모르고, 판정하지 않고, 제안까지만 한다.

## 이 파일이 푸는 문제

레시피의 키는 **「상품명 × 폼팩터」**인데(§0-B), 매핑 정본에는 **폼팩터 열이 없다.**
있는 것은 상품명 / 옵션명(기종) / 채널별 코드뿐이다. 그리고 §0-B가 못 박은 대로
**폼팩터는 상품명이 아니라 옵션(기종)이 정한다** — 한 상품명이 플립·폴드 옵션을 함께 담는
실례가 있기 때문이다.

⇒ 그래서 이 파서는 **옵션명에서 폼팩터를 «제안»**한다. 확정이 아니다.

## 왜 제안을 믿을 만한가 — 독립 신호 둘의 교차 실측 (2026-08-23)

옵션명 규칙은 `product_master.cost_price`(사람이 엑셀에서 옮긴 현재 값)와 **독립**이다.
표적 상품명 「오하이 빛반사, 지문방지 매트 필름 3매」(162 SKU)로 둘을 교차했더니:

    bar   108 SKU → cost_price 2350.7 × 108        ← 단일값. 순도 100%
    flip   27 SKU → 4483·3480.4·1412.4·4883·2558   ← 혼재(S3 몫)
    fold   30 SKU → 6089.6·2666·8017·7519.6        ← 혼재(S3 몫)

`bar` 버킷이 원가표 「지문방지필름 TPU 3매」의 제품원가(+VAT) **2,350.7**과 정확히 일치한다.
두 신호가 어긋나는 SKU는 **이상으로 올려** 사람이 처분한다 — 자동으로 고르지 않는다.

⚠️ 이 교차는 `cost_price`를 **읽기만** 한다(§3 금지선). 쓰지 않고, 원가로 채택하지도 않는다 —
「어느 레시피에 붙는가」를 제안하는 **신원 단서**로만 쓴다.

## 채널코드 → internal_sku

매핑 정본에 `internal_sku` 열은 없다. 도달 경로는 저장소 전역의 표준 조인이다(실측 확인):

    channel_product_id → product_channel_mapping.product_id → product_master.internal_sku

그 조인은 **DB를 아는 층**(라우터·서비스)의 일이라 이 모듈에 없다. 여기서는 채널코드를
모아 올려보내기만 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

#: 매핑 정본 「원가 매핑」 시트의 채널코드 열 인덱스 — 실측(2026-08-23, 헤더 21열·944 옵션 행).
#: 열 구성: 0 상품명 · 1 옵션명 · 이후 (채널명, 코드…) 반복.
#: 하드코딩이 아니라 **헤더에서 도출**하는 것이 원칙이므로 `detect_code_columns()`가 정본이고,
#: 이 상수는 그 함수가 아무것도 못 찾았을 때의 대비값이다.
FALLBACK_CODE_COLUMNS: tuple[int, ...] = (3, 4, 6, 8, 9, 11, 12, 14, 15, 17, 19)

PRODUCT_NAME_COL = 0
OPTION_NAME_COL = 1

#: 「채널명」 열은 코드가 아니다 — 건너뛴다.
_CHANNEL_LABEL = "채널명"

#: 옵션명 → 폼팩터 제안 규칙. **순서가 규칙의 일부다** — 「트라이폴드」가 「폴드」보다 먼저
#: 걸려야 하고, 그 순서가 뒤집히면 트라이폴드가 통째로 fold로 접힌다.
_OPTION_RULES: tuple[tuple[str, str], ...] = (
    (r"트라이", "trifold"),
    (r"플립", "flip"),
    (r"폴드", "fold"),
    (r"탭|태블릿|아이패드|패드|Tab|iPad", "tablet"),
)

#: 상품명에서만 읽히는 폼팩터 — 기종 축이 아예 없는 계열이다(옵션명이 「1세트」 같은 것뿐).
_PRODUCT_RULES: tuple[tuple[str, str], ...] = (
    (r"도어락|문캅스", "doorlock"),
    (r"버디필름", "buddy"),
)

#: 어느 규칙에도 안 걸릴 때의 폴백.
#:
#: ★**이 상수를 제거하지 않는다** (계약 D-CPP-61 §3 금지선). `bar`를 «내는» 양성 규칙이
#: `_OPTION_RULES`·`_PRODUCT_RULES` 어디에도 없어서 **prod `bar` 67건 전부가 이 폴백의
#: 산물**이다 — 없애거나 None을 반환하게 하면 정당한 bar 계열(승인 레시피 48·49 포함)까지
#: 그룹 키가 흔들려 재업로드 시 중복 레시피가 생긴다.
#:
#: ★대신 고치는 것은 **침묵**이다: 이 상수의 병은 존재가 아니라 「모른다」를 「bar다」로
#: 바꾸면서 그 사실을 어디에도 안 남긴 것이다. 그래서 ①비필름 46건이 필름 원가표와
#: 비교되고 ②수입 완제품 초안이 영영 조회되지 않는 버킷에 갇혔는데 어느 화면도 그것을
#: 말하지 않았다. `propose_form_factor_with_source`가 그 사실을 함께 돌려준다.
DEFAULT_FORM_FACTOR = "bar"

#: `propose_form_factor_with_source`의 두 번째 값 — 폼팩터를 «어떻게» 얻었나.
FORM_SOURCE_RULE = "rule"
FORM_SOURCE_FALLBACK = "fallback"


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def detect_code_columns(header: Sequence[Any]) -> tuple[int, ...]:
    """헤더 행 → 채널코드 열 인덱스.

    「채널명」·「상품명」·「옵션명」이 아니면서 이름이 있는 열을 코드 열로 본다.
    헤더가 바뀌어도 따라가기 위해서다 — 열 위치를 상수로 굳히면 정본이 한 칸 밀리는 순간
    조용히 틀린 코드를 읽는다(교훈: 파서 열밀림, 계약 B 2026-08-22 PR #329).
    """

    cols: list[int] = []
    for idx, raw in enumerate(header):
        if idx in (PRODUCT_NAME_COL, OPTION_NAME_COL):
            continue
        label = _norm(raw)
        if not label or label == _CHANNEL_LABEL:
            continue
        cols.append(idx)
    return tuple(cols) if cols else FALLBACK_CODE_COLUMNS


def propose_form_factor_with_source(
    product_name: str, option_name: str
) -> tuple[str, str]:
    """옵션명(1순위)·상품명(2순위) → **(폼팩터 제안, 그 값을 얻은 방법)**.

    ★확정이 아니다. 화면에서 사람이 승인하고, `cost_price` 교차와 어긋나면 이상으로 뜬다.

    ★두 번째 값이 이 함수의 요점이다 (계약 D-CPP-61 §4-Q2). `FORM_SOURCE_RULE`이면
    실제 규칙이 걸린 것이고, `FORM_SOURCE_FALLBACK`이면 **아무 규칙도 안 걸려 `bar`로
    단정한 것**이다. 호출부는 그 사실을 레시피에 실어 화면이 「폼팩터 추정」을 말하게 한다 —
    이 프로젝트의 상습병(추정 금지 — 모르면 모른다고 말한다)의 파서판이다.
    """

    option = _norm(option_name).replace(" ", "")
    for pattern, form in _OPTION_RULES:
        if re.search(pattern, option, re.IGNORECASE):
            return form, FORM_SOURCE_RULE

    product = _norm(product_name).replace(" ", "")
    for pattern, form in _PRODUCT_RULES:
        if re.search(pattern, product, re.IGNORECASE):
            return form, FORM_SOURCE_RULE

    # 상품명에도 기종 축이 걸릴 수 있다(옵션명이 비어 있는 행) — 옵션 규칙을 상품명에 한 번 더.
    for pattern, form in _OPTION_RULES:
        if re.search(pattern, product, re.IGNORECASE):
            return form, FORM_SOURCE_RULE

    return DEFAULT_FORM_FACTOR, FORM_SOURCE_FALLBACK


def propose_form_factor(product_name: str, option_name: str) -> str:
    """`propose_form_factor_with_source`의 폼팩터만 — 기존 호출부 호환."""

    return propose_form_factor_with_source(product_name, option_name)[0]


def form_source_for(form_factor: Optional[str]) -> Optional[str]:
    """저장된 note 없이 **이미 있는 레시피**의 폼팩터 출처를 파생한다.

    ★왜 파생이 가능한가 (적대 리뷰 1R P1-2): `_OPTION_RULES`·`_PRODUCT_RULES` 어디에도
    `bar`를 **내는** 규칙이 없다. 그러므로 `form_factor == DEFAULT_FORM_FACTOR`인 레시피는
    **필연적으로 폴백의 산물**이다 — prod bar 67건 전부가 그렇다. 이 파생이 없으면 합격 5가
    「Jino가 매핑 정본을 다시 올려야」 열리는데, 계약 §5는 항목 5를 **사람 단계 없이 판정
    가능**한 것으로 두었다.

    ★**자기 보호**: 누군가 나중에 폴백값을 «내는» 규칙을 추가하면 이 추론은 무너진다.
    그때는 `None`(출처 미상)을 돌려준다 — **틀린 단정보다 「모른다」가 낫다**는 것이
    애초에 이 필드를 만든 이유다.
    """

    if form_factor is None:
        return None
    if any(f == DEFAULT_FORM_FACTOR for _, f in _OPTION_RULES + _PRODUCT_RULES):
        return None
    return (
        FORM_SOURCE_FALLBACK if form_factor == DEFAULT_FORM_FACTOR else FORM_SOURCE_RULE
    )


@dataclass(frozen=True)
class OptionRow:
    """매핑 정본 한 행 = 옵션 1건."""

    product_name: str
    option_name: str
    channel_codes: tuple[str, ...]
    form_factor: str
    row_number: int
    #: 폼팩터를 «어떻게» 얻었나 — `rule`이면 규칙이 걸린 것, `fallback`이면 `bar`로 단정한 것.
    #: 기본값을 `rule`로 두지 않는다: 모르는 채로 만들어진 행이 「규칙이 걸렸다」고 말하면
    #: 이 필드를 만든 이유(침묵 제거)가 그대로 뒤집힌다.
    form_source: str = FORM_SOURCE_FALLBACK


@dataclass
class MappingParseResult:
    options: list[OptionRow] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)

    @property
    def product_names(self) -> list[str]:
        seen: dict[str, None] = {}
        for row in self.options:
            seen.setdefault(row.product_name, None)
        return list(seen)

    def groups(self) -> dict[tuple[str, str], list[OptionRow]]:
        """(상품명, 폼팩터) → 옵션 행들 — 이것이 레시피 1건의 «후보 묶음»이다."""

        out: dict[tuple[str, str], list[OptionRow]] = {}
        for row in self.options:
            out.setdefault((row.product_name, row.form_factor), []).append(row)
        return out


def parse_mapping_table(rows: Iterable[Sequence[Any]]) -> MappingParseResult:
    """「원가 매핑」 시트의 행 목록 → 옵션 행. 첫 행은 헤더로 소비한다."""

    result = MappingParseResult()
    code_columns: Optional[tuple[int, ...]] = None

    for row_number, raw in enumerate(rows, start=1):
        row = list(raw)
        if code_columns is None:
            code_columns = detect_code_columns(row)
            continue

        product_name = _norm(row[PRODUCT_NAME_COL]) if len(row) > PRODUCT_NAME_COL else ""
        if not product_name:
            continue
        option_name = _norm(row[OPTION_NAME_COL]) if len(row) > OPTION_NAME_COL else ""

        codes: list[str] = []
        for idx in code_columns:
            if idx >= len(row):
                continue
            code = _norm(row[idx])
            if code and code not in codes:
                codes.append(code)

        if not codes:
            result.anomalies.append(f"행{row_number} {product_name}/{option_name}: 채널코드 0건")

        form_factor, form_source = propose_form_factor_with_source(
            product_name, option_name
        )
        result.options.append(
            OptionRow(
                product_name=product_name,
                option_name=option_name,
                channel_codes=tuple(codes),
                form_factor=form_factor,
                row_number=row_number,
                form_source=form_source,
            )
        )

    return result
