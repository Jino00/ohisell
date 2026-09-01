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

⚠️ `필름*매입`은 «`매입`으로 끝나는 열»보다 **먼저** 걸러 `derived`로 분류한다.

★정정(적대 리뷰 1R M10): 초판 주석은 이 순서를 *"이 파서에서 가장 깨지기 쉬운 자리"*라고 적었는데
**그건 사실이 아니었다.** 리뷰어가 순서를 뒤집는 변이를 넣었더니 결과가 그대로였다 — 라인 생성
루프가 `qty`와 `derived`를 **둘 다 건너뛰기** 때문에 등가 변이다. 그래도 분류는 옳게 유지한다:
「유도값」과 「수량」은 다른 것이고, 나중에 유도값을 검산에 쓰기 시작하면 그때 실제로 갈린다.
분류 자체는 `test_column_roles_are_classified` 가 못 박는다 — 주장을 주석이 아니라 테스트에 둔다.

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
        # ★실제 시트의 섹션 이름은 「기타」다(셀카봉·맥탭 카드케이스가 그 아래 있다).
        #   위 두 줄은 «품목 이름»을 섹션으로 착각해 넣은 것이고 실제로는 한 번도 안 걸렸다
        #   (2026-09-01 엑셀 원본 대조 — 남겨 두되 「기타」를 더한다).
        "기타",
    }
)

#: 이 섹션들은 조립형이 아니다 — **통관 원장**에서 단가가 직접 온다(계약 §5-1 ★매입품 결정).
IMPORTED_SECTIONS: frozenset[str] = frozenset({"오타오_강화유리필름"})

#: ★국내 매입 완제품 섹션 — 우리가 만들지 않고 **사 오는** 것들 (Jino 확정 2026-09-01 20:0x).
#:
#: 수입품과 다른 점은 **단가의 출처뿐**이다: 이쪽은 통관 원장이 아니라 **원가표의 「상품원가」**가
#: 정본이다. 초판은 이 섹션들을 `assembly`로 찍었고, 그래서 구성이 0줄인 「조립품」이 되어
#: 165개 케이스 SKU가 「정본 없음」에 갇혀 있었다(2026-09-01 실측).
#: 값의 뜻은 `materials.PURCHASED_KIND` 주석이 정본이다.
PURCHASED_SECTIONS: frozenset[str] = frozenset(
    {
        "오타오_기타 액세서리",   # 맥세이프 그립톡
        "케이스",                # 일미리·소다 포유 계열 6종
        "오픽스 맥세이프 거치대",
        "기타",                  # 셀카봉 · 맥탭 카드케이스
    }
)

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


# ──────────────────────────────────────────────
# cleaning kit 별칭 (D-CPP-58 층2) — 폼팩터를 «가르지 않는» 유일한 예외
# ──────────────────────────────────────────────
#: 원장 파생 종의 이름. **prod의 `cost_material.id=1`과 문자열이 완전히 같아야 한다** —
#: `_upsert_materials`가 이름으로 upsert하므로, 한 글자만 달라도 새 종이 하나 더 생긴다.
CLEANING_KIT_NAME = "cleaning kit"

#: 폼팩터·부위 **둘 다 None**이다. MaterialKey docstring이 「폼팩터가 다르면 다른 물건」이라
#: 적어 둔 것의 **명시적 예외**이고, 근거는 규칙이 아니라 사실이다 — Jino 2026-08-25 11:32:
#: *"기종별 부자재와 cleaning kit가 따로 있는데 이게 모두 같은 cleaning kit야"*.
#: 같은 물건이므로 규격이 갈리지 않고, 단가는 수입 로트 하나에서 나온다.
#: ★이 예외를 다른 라벨로 넓히지 마라 — 패키지(98/370/550)·부착 안내문(30/40/55)은 폼팩터마다
#: 값이 «실제로» 달라 접으면 원가가 틀어진다(계약 §0-F 실측).
CLEANING_KIT_KEY = MaterialKey(None, None, CLEANING_KIT_NAME)

#: 엑셀 원가표가 이 물건을 부르는 이름들. 실측 라벨은 「부자재 (밀대외)」 하나지만,
#: 시트마다 줄바꿈·공백이 다르게 들어온다(테스트 픽스처에 `"부자재\n(밀대외)"`가 실재).
_CLEANING_KIT_ALIASES = frozenset({"부자재(밀대외)"})


def is_cleaning_kit_label(label: str) -> bool:
    """엑셀 열 이름이 cleaning kit을 가리키는가.

    ★공백·줄바꿈을 **전부 지우고** 비교한다 — 「부자재 (밀대외)」·「부자재(밀대외)」·
    「부자재\\n(밀대외)」가 같은 열이기 때문이다. 이름 매칭이 공백 하나에 지면 층2가 조용히
    뚫리고, 그때 증상은 «다음 엑셀 업로드에 6종이 되살아난다»로 나타난다(계약 §2-4).
    """

    if not label:
        return False
    return "".join(str(label).split()) in _CLEANING_KIT_ALIASES


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
    """헤더 행인가 — 「제품원가」가 **C 또는 D**에 있으면 헤더다.

    ★초판은 D(index 3)만 봤다. 그런데 「오타오_기타 액세서리」 섹션은 헤더가 C에 있어
    (`C3 = 제품원가 (KRW)`) 섹션 자체가 안 잡혔고 그 아래 「맥세이프 그립톡」 5,240원이
    통째로 사라졌다(2026-09-01 엑셀 원본 대조에서 발견).
    """

    return any(
        len(row) > idx and _HEADER_MARK in _norm(row[idx]) for idx in (2, 3)
    )


#: 총액을 어느 열에서 읽는가 — 섹션 헤더마다 다르다(2026-09-01 엑셀 원본 실측).
#:
#:   필름 섹션    D 「제품원가 (+VAT)」 = 부자재·부가세 포함 총액        → 그대로 총액
#:   수입 섹션    D 「제품원가 (CNY/USD)」 · E 「(KRW)」 · I 「상품원가」  → **I가 최종**
#:   국내 케이스  E 「제품원가 (KRW)」 · I 「상품원가」(본체) · J 「부자재」 → **(I+J)×1.1**
#:   액세서리     C 「제품원가 (KRW)」만                                → 그대로 총액
#:
#: ★초판은 언제나 D를 읽었다. 그래서 수입·케이스 섹션에서 **외화 단가**(12.2·0.99·11·4.5)를
#:   원가로 저장했고, D가 빈 국내 케이스 6행은 «데이터 행이 아니다»로 판정돼 통째로 버려졌다.
#:   국내 케이스 총액을 (I+J)×1.1로 정한 것은 Jino 확정이다(2026-09-01 19:5x) —
#:   엑셀 E와 어긋나는 행(「일미리 케이스」 922 vs 1,014.2)은 **계산값이 맞다**고 답하셨다.
@dataclass(frozen=True)
class _TotalSpec:
    film_idx: Optional[int] = None      # 「제품원가 (+VAT)」
    foreign_idx: Optional[int] = None   # 「제품원가 (CNY/USD/US$)」
    krw_idx: Optional[int] = None       # 「제품원가 (KRW)」
    landed_idx: Optional[int] = None    # 「상품원가」
    parts_idx: Optional[int] = None     # 「부자재」

    @property
    def value_indices(self) -> tuple[int, ...]:
        return tuple(
            i
            for i in (
                self.film_idx,
                self.foreign_idx,
                self.krw_idx,
                self.landed_idx,
                self.parts_idx,
            )
            if i is not None
        )


_FOREIGN_MARKS = ("cny", "usd", "us$")


def _total_spec(header: Sequence[Any]) -> _TotalSpec:
    """헤더 행 → 총액을 읽을 열들. **라벨로 찾는다** — 열 «위치»를 박아 두지 않는다."""

    film = foreign = krw = landed = parts = None
    for idx in range(len(header)):
        label = _norm(header[idx])
        if not label:
            continue
        low = label.lower().replace(" ", "")
        if _HEADER_MARK in label:
            if "vat" in low:
                film = idx if film is None else film
            elif any(m in low for m in _FOREIGN_MARKS):
                foreign = idx if foreign is None else foreign
            elif "krw" in low:
                krw = idx if krw is None else krw
        elif label == "상품원가":
            landed = idx if landed is None else landed
        elif label == "부자재":
            parts = idx if parts is None else parts
    return _TotalSpec(film, foreign, krw, landed, parts)


def _row_total(
    row: Sequence[Any], spec: _TotalSpec
) -> tuple[Optional[Decimal], list[str]]:
    """행 → (총액, 이상 목록). **총액을 «지어내지» 않는다** — 못 정하면 None + 자백."""

    def at(idx: Optional[int]) -> Optional[Decimal]:
        if idx is None or idx >= len(row):
            return None
        return _dec(row[idx])

    if spec.film_idx is not None:
        return at(spec.film_idx), []

    foreign = at(spec.foreign_idx)
    krw = at(spec.krw_idx)
    landed = at(spec.landed_idx)
    parts = at(spec.parts_idx)

    if foreign is not None:
        # 수입형 — 최종은 「상품원가」다. 외화·환산가는 중간값이라 원가로 쓰면 안 된다.
        if landed is None:
            # 엑셀이 아직 환산·부대비를 안 계산한 행(예: 오타오_갤럭시 투명 강화유리 CNY 19.3).
            # 외화값을 «원가처럼» 싣지 않는다 — 그게 12.2원짜리 강화유리를 만든 원인이다.
            return None, [f"foreign_only_no_landed:{foreign}"]
        return landed, []

    if landed is not None and parts is not None:
        # 국내 매입형 — (본체 + 부자재) × 1.1. 엑셀 총액과 어긋나면 자백하고 계산값을 쓴다.
        computed = (landed + parts) * VAT_MULTIPLIER
        if krw is not None and abs(computed - krw) > TOTAL_TOLERANCE:
            return computed, [f"excel_total_mismatch:{computed:.2f}≠{krw}"]
        return computed, []

    if landed is not None:
        # 「상품원가」만 있는 행 — 부가세 포함 여부를 모른다. 값은 싣되 그 사실을 남긴다.
        return landed, ["landed_only_vat_unknown"]

    if krw is not None:
        return krw, []

    return None, []


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
    tspec = _TotalSpec()
    seen_keys: dict[MaterialKey, Decimal] = {}
    seen_items: dict[tuple[str, str], tuple] = {}

    # ★섹션 제목 행은 **구조**로 가른다 — 「품목」 헤더 바로 앞 행이 그 섹션의 제목이다.
    #
    #   값의 «유무»로 가르면 안 된다(2026-09-01 자기 테스트가 잡았다): 제목 행에도 숫자가
    #   있을 수 있고(`오타오_강화유리필름 | 환율 | 200`), 그때 «앞 섹션의 열 배치»로 읽히면
    #   200을 원가로 들고 데이터 행이 되어 버린다. 그러면 뒤따르는 「품목」 헤더가 섹션
    #   이름을 못 찾아 **그 섹션 전체가 앞 섹션 소속이 된다**(수입 강화유리 5건이 통째로
    #   케이스로 분류됐다). 반대로 「직전 행 = 제목」만으로 가르면 섹션 끝 상품 행이
    #   먹힌다(행 26 자가복원 EPU 바로 뒤가 플립 헤더다) — 그래서 **「품목」 헤더일 때만**이다.
    materialized = [list(raw) for raw in rows]
    title_rows: set[int] = set()
    for i, row in enumerate(materialized):
        for nxt in materialized[i + 1 :]:
            if not any(c is not None and _norm(c) for c in nxt):
                continue  # 빈 행은 건너뛴다
            if _is_header_row(nxt) and _norm(nxt[1] if len(nxt) > 1 else "") == _ITEM_HEADER:
                title_rows.add(i)
            break

    for row_number, row in enumerate(materialized, start=1):
        is_title = (row_number - 1) in title_rows
        col1 = _norm(row[1]) if len(row) > 1 else ""

        if _is_header_row(row):
            if col1 and col1 != _ITEM_HEADER:
                section = col1
            elif last_label:
                section = last_label
            if section and section not in result.sections_seen:
                result.sections_seen.append(section)
            specs = _classify_columns(row, start=4)
            tspec = _total_spec(row)
            continue

        # ★«데이터 행인가»와 «총액이 얼마인가»는 다른 질문이다(2026-09-01).
        #   초판은 둘을 D열 하나로 물어봐서, D가 빈 국내 케이스 6행을 «행이 아니다»로
        #   판정해 통째로 버렸다. 이제 스펙이 아는 «값 열» 중 하나라도 차 있으면 데이터 행이고,
        #   총액은 따로 정한다(못 정하면 None이고 그 사실이 이상으로 올라간다).
        has_value = any(
            idx < len(row) and _dec(row[idx]) is not None for idx in tspec.value_indices
        )
        if col1 and (is_title or not has_value):
            last_label = col1          # 섹션 제목(위 구조 판별) 또는 값 없는 이름 행
            continue

        if not col1 or not has_value or section is None:
            continue

        total, total_anomalies = _row_total(row, tspec)

        result.recipes.append(
            _build_recipe(
                section=section,
                item_name=col1,
                total=total,
                total_anomalies=total_anomalies,
                row=row,
                specs=specs,
                row_number=row_number,
                seen_keys=seen_keys,
                seen_items=seen_items,
                result=result,
            )
        )

    # ★종 목록은 «실제로 만들어진 라인»에서 뽑는다(적대 리뷰 P2-8). `seen_keys`는 접기 «전»
    #   키라, 거기서 뽑으면 밀대외 6종이 목록에 남고 cleaning kit은 빠진다 — 종 목록의
    #   «두 번째 진실»이 생긴다. 지금 소비처가 없어 무해하지만, 무해한 거짓말도 거짓말이다.
    seen_material_keys: dict[MaterialKey, None] = {}
    for draft in result.recipes:
        for ln in draft.lines:
            seen_material_keys.setdefault(ln.key, None)
    result.materials = list(seen_material_keys)
    return result


def _build_recipe(
    *,
    section: str,
    item_name: str,
    total: Optional[Decimal],
    row: list[Any],
    specs: list[_ColumnSpec],
    row_number: int,
    seen_keys: dict[MaterialKey, Decimal],
    seen_items: dict[tuple[str, str], tuple],
    result: ParseResult,
    total_anomalies: Sequence[str] = (),
) -> RecipeDraft:
    # ★총액 해석에서 나온 이상(외화만 있음·엑셀 총액 불일치…)을 여기서 «먼저» 싣는다 —
    #   나중에 붙이면 조립형 조기 반환 경로에서 통째로 사라진다.
    anomalies: list[str] = list(total_anomalies)

    form_factor = SECTION_FORM_FACTOR.get(section)
    if form_factor is None and section not in NO_FORM_FACTOR_SECTIONS:
        anomalies.append(f"unknown_section:{section}")
    if section in IMPORTED_SECTIONS:
        recipe_kind = "imported_goods"   # 통관 원장에서 단가가 온다
    elif section in PURCHASED_SECTIONS:
        recipe_kind = "purchased"        # 원가표의 「상품원가」가 단가다
    else:
        recipe_kind = "assembly"

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

        # ★별칭은 «신원이 정해지고 이상 검사가 끝난 뒤» 적용한다(D-CPP-58 층2).
        #   순서가 중요하다 — 접기 전에 검사해야 `price_conflict`가 원래 열 이름으로 뜬다.
        #
        # ★★`excel_ref_price`는 **그대로 22를 싣는다.** 접었다고 22를 버리면
        #   `computed_ex_vat`(구성 합 = 엑셀 「제품원가」인가)라는 **검산이 통째로 죽는다** —
        #   시트를 잘못 읽어도 아무도 모르게 된다. 22가 «종의 단가»로 새는 것은 여기가 아니라
        #   `recipes._upsert_materials`에서 막는다(그 자리가 종을 만드는 유일한 자리다).
        if not is_film and is_cleaning_kit_label(spec.label):
            key = CLEANING_KIT_KEY
            # ★★접기는 «두 열이 한 종이 되는» 일이라, 한 행에 접히는 열이 둘 있으면
            #   같은 종이 두 줄이 된다(적대 리뷰 P2-2 재현: 「부자재 (밀대외)」와
            #   「부자재(밀대외)」가 나란히 있는 시트). 계약 §0-D가 지목한 **조용한 이중
            #   계상**이 여기서 «매 업로드마다» 재생산되는 셈이다 — 제약 위반보다 나쁘다,
            #   에러가 안 나기 때문이다. 그래서 둘째 줄은 버리고 **이상으로 자백한다.**
            #   (버리는 쪽이 옳다: 접었다는 것은 같은 물건이라는 뜻이고, 같은 물건을 두 번
            #    세는 것이 결함이다. 수량 합산이 아니다 — kit은 제품당 1개다.)
            if any(ln.key == CLEANING_KIT_KEY for ln in lines):
                anomalies.append(f"duplicate_cleaning_kit:{spec.label}")
                continue

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
    elif computed is None or total is None:
        # ★`total is None`도 검산 불능이다 — 초판은 total이 항상 있다고 가정했는데,
        #   이제 「외화만 있고 상품원가가 없는」 행이 None을 들고 여기까지 온다.
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
