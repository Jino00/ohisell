"""표준원가 산술 — 순수 SA (계약 A′ §2-6, D-CPP-53).

이 모듈은 **DB도 IO도 모른다.** 입력은 값 객체, 출력은 값 객체다.
`import_cost/allocator.py`와 같은 형태다 — 왜냐하면 산술이 «감시자가 감시 대상보다 낡는»
형태로 두 벌 생기면 안 되기 때문이다(ref 54 §9 실증). 라우터·파서·테스트가 이 한 벌을 쓴다.

## 무엇을 계산하나

표준원가 = **Σ(구성 라인의 수량 × 단가)**. 그게 전부다 — 배부도 환율도 여기 없다.
그것들은 이미 계약 B 원장에서 끝나 **단가 하나**로 들어온다(계약 §5-2 데이터 흐름).

정본 대조값(계약 §7 합격 3): 「지문방지필름 TPU 3매」(일반 `bar`)
  필름 600×3=1800 · 부착안내문 30 · 부자재(밀대외) 22 · 알콜솜 2EA 60 · 비닐(9*18) 8
  · 비닐(12*22+4) 13 · 패키지 98 · 폼텍 스티커 6 · 부착 지그 100
  ⇒ ex_vat **2,137** · inc_vat **2,350.70** (부자재 9종)

## ★미확정은 0이 아니다 (계약 §2-7)

단가가 하나라도 미확정이면 **표준원가는 «없음»(None)**이고 0이 아니다. 왜냐하면 0=미입력
혼동이 기존 스키마의 결함이고(`cost_price` NOT NULL default 0), 새 층에서 재생산할 이유가
없기 때문이다. 부분합을 «표준원가»라고 부르지 않는다 — 부분합은 `partial_ex_vat`로 따로
내주되 그 이름이 그것이 부분임을 말한다.

## 부가세 두 값

라인마다 `unit_price_inc_vat`가 오면 **그 값을 쓴다**(원장 파생분은 계약 B가 이미 확정 저장해
둔 값이다). 없으면 `ex × 1.1`로 유도하고 그 라인에 `inc_derived=True`를 남긴다 — 유도했다는
사실이 화면까지 가야 하기 때문이다(D-NAO-150 규약, allocator와 같은 승수).

⚠️ 이 ×1.1은 «실제로 낸 부가세»가 아니다 — allocator docstring의 같은 경고가 그대로 적용된다.

## 합계는 라인 합이다 (어긋날 수 없게)

라인별로 2자리 반올림한 뒤 **그 라인 값들을 더해** 총액을 만든다. 총액을 따로 반올림하지
않는다 — 그래야 화면의 라인 합과 총액이 원 단위로 항상 일치한다(교훈 #327: 「원장 기준으로
세다가 118,890원 증발」이 정확히 이 자리에서 났다).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Literal, Optional

VAT_MULTIPLIER = Decimal("1.1")  # D-NAO-150 규약 — 실제 세액이 아니다(모듈 docstring)

_ZERO = Decimal("0")
_CENT = Decimal("0.01")

# 단가를 못 쓰는 사유. `ledger_check`의 6값을 그대로 계승하고 **하나만 더한다** — 새 어휘는
# 화면이 설명할 사유를 늘리므로 함부로 만들지 않는다. 그런데 아래 하나는 만들 수밖에 없었다
# (적대 리뷰 1R P1-1).
#
# ★왜 `unconfirmed`로 접으면 안 되나 (실측 2026-08-23):
#   원장의 `unconfirmed`는 «수입건 확정이 풀려 원장이 단가를 지웠다» = **값이 없다.**
#   「종이 아직 승인 안 됐다」는 **값이 있는데 못 쓰는 것**이다. 둘을 한 단어로 부르면 화면이
#   「단가 미확정」이라 말하면서 실재하는 178.78원을 「—」로 감춘다. 그러면 Jino는
#   **이미 있는 단가를 입력하러 간다.** 사유가 틀리면 사람이 틀린 일을 한다.
PriceStatus = Literal[
    "ok", "manual", "missing", "unconfirmed", "changed", "item_mismatch",
    "material_unapproved",
]

#: 종이 승인되지 않아 못 쓰는 상태 — **단가는 실재한다.** 화면은 그 값을 보여주되 합계엔 안 넣는다.
STATUS_MATERIAL_UNAPPROVED = "material_unapproved"

#: 이 상태의 단가는 계산에 쓰지 않는다. `ok`·`manual`만 쓴다.
UNUSABLE_STATUSES: frozenset[str] = frozenset(
    {"missing", "unconfirmed", "changed", "item_mismatch", STATUS_MATERIAL_UNAPPROVED}
)

#: 사유별 «사람이 읽고 움직일 수 있는» 문장. ★상태 이름만으로는 사람이 무엇을 해야 할지 모르고,
#: 움직일 수 없는 자백은 자백이 아니라 장식이다.
UNUSABLE_REASON_TEXT: dict[str, str] = {
    "missing": "단가 없음",
    "unconfirmed": "수입건 확정 해제 — 원장이 단가를 지웠다",
    "changed": "원장이 재확정돼 값이 달라졌다 — 부자재 탭에서 「갱신」",
    "item_mismatch": "연결된 원장 라인이 다른 품목이 됐다 — 해제 후 재연결",
    STATUS_MATERIAL_UNAPPROVED: "부자재 종 미승인 — 단가는 있다, 부자재 탭에서 「승인」",
}


def unusable_summary(labels_by_status: dict[str, list[str]]) -> str:
    """사유별로 묶어 한 문장으로. 「미확정 N건」처럼 뭉치지 않는다 — 처분이 사유마다 다르다."""

    parts: list[str] = []
    for status, labels in sorted(labels_by_status.items()):
        text = UNUSABLE_REASON_TEXT.get(status, status)
        shown = ", ".join(labels[:3])
        more = f" 외 {len(labels) - 3}건" if len(labels) > 3 else ""
        parts.append(f"{text} ({len(labels)}건: {shown}{more})")
    return " · ".join(parts)


def _round(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


# ──────────────────────────────────────────────
# 값 객체
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class RecipeLineInput:
    """구성 한 줄 + 그 시점의 단가.

    단가 조회(원장 파생·수동)는 **호출자가 이미 끝낸 상태로** 넘긴다 — 이 모듈은 DB를 모른다.
    """

    label: str                                   # 화면에 보일 이름(부자재명)
    quantity: Decimal
    unit_price_ex_vat: Optional[Decimal] = None
    unit_price_inc_vat: Optional[Decimal] = None
    price_status: PriceStatus = "missing"
    material_id: Optional[int] = None
    ledger_item_name: Optional[str] = None
    price_source: Optional[str] = None           # ledger / manual — 근거 표시용
    price_note: Optional[str] = None


@dataclass(frozen=True)
class StandardCostLine:
    """계산된 한 줄 — «계산되는 방법이 나오는» 화면의 원료(계약 §7 합격 4)."""

    label: str
    quantity: Decimal
    unit_price_ex_vat: Optional[Decimal]
    unit_price_inc_vat: Optional[Decimal]
    amount_ex_vat: Optional[Decimal]
    amount_inc_vat: Optional[Decimal]
    price_status: PriceStatus
    inc_derived: bool = False
    price_source: Optional[str] = None
    price_note: Optional[str] = None
    material_id: Optional[int] = None
    ledger_item_name: Optional[str] = None

    @property
    def usable(self) -> bool:
        return self.amount_ex_vat is not None


@dataclass(frozen=True)
class StandardCostResult:
    """레시피 1건의 표준원가.

    `computable=False`면 `std_cost_*`는 **None**이다 — 0이 아니다(§2-7).
    """

    computable: bool
    std_cost_ex_vat: Optional[Decimal]
    std_cost_inc_vat: Optional[Decimal]
    lines: tuple[StandardCostLine, ...]
    unresolved: tuple[str, ...]          # 단가를 못 쓴 라인 라벨
    reason: Optional[str]                # 계산 못 한 사유(사람이 읽는 문장)
    partial_ex_vat: Decimal              # 쓸 수 있는 라인만의 합 — «부분»임을 이름이 말한다
    partial_inc_vat: Decimal

    @property
    def line_count(self) -> int:
        return len(self.lines)


class StandardCostError(ValueError):
    """입력 자체가 성립하지 않는 경우 — 미확정 단가는 여기 해당하지 않는다."""


# ──────────────────────────────────────────────
# 계산
# ──────────────────────────────────────────────
def _resolve_line(line: RecipeLineInput) -> StandardCostLine:
    if line.quantity is None:
        raise StandardCostError(f"수량이 없다: {line.label}")
    if line.quantity < _ZERO:
        raise StandardCostError(f"수량이 음수다: {line.label}={line.quantity}")

    unusable = (
        line.price_status in UNUSABLE_STATUSES
        or line.unit_price_ex_vat is None
    )
    if unusable:
        return StandardCostLine(
            label=line.label,
            quantity=line.quantity,
            unit_price_ex_vat=line.unit_price_ex_vat,
            unit_price_inc_vat=line.unit_price_inc_vat,
            amount_ex_vat=None,
            amount_inc_vat=None,
            price_status=line.price_status,
            price_source=line.price_source,
            price_note=line.price_note,
            material_id=line.material_id,
            ledger_item_name=line.ledger_item_name,
        )

    ex = line.unit_price_ex_vat
    assert ex is not None  # 위 분기가 보장한다
    inc_derived = line.unit_price_inc_vat is None
    inc = ex * VAT_MULTIPLIER if inc_derived else line.unit_price_inc_vat
    assert inc is not None

    return StandardCostLine(
        label=line.label,
        quantity=line.quantity,
        unit_price_ex_vat=ex,
        unit_price_inc_vat=_round(inc),
        amount_ex_vat=_round(ex * line.quantity),
        amount_inc_vat=_round(inc * line.quantity),
        price_status=line.price_status,
        inc_derived=inc_derived,
        price_source=line.price_source,
        price_note=line.price_note,
        material_id=line.material_id,
        ledger_item_name=line.ledger_item_name,
    )


def compute_standard_cost(lines: Iterable[RecipeLineInput]) -> StandardCostResult:
    """구성 라인 → 표준원가.

    ★라인이 **하나도 없으면** 계산 불가다 — 「0원짜리 제품」이 아니라 「구성이 비었다」이기
    때문이다. 빈 Σ를 0으로 접는 것이 §2-7이 금지하는 바로 그 혼동이다.
    """

    resolved = tuple(_resolve_line(line) for line in lines)

    if not resolved:
        return StandardCostResult(
            computable=False,
            std_cost_ex_vat=None,
            std_cost_inc_vat=None,
            lines=(),
            unresolved=(),
            reason="구성 라인이 없다 — 레시피가 비어 있다",
            partial_ex_vat=_ZERO,
            partial_inc_vat=_ZERO,
        )

    partial_ex = sum((ln.amount_ex_vat for ln in resolved if ln.usable), _ZERO)
    partial_inc = sum((ln.amount_inc_vat for ln in resolved if ln.usable), _ZERO)
    unresolved = tuple(ln.label for ln in resolved if not ln.usable)

    if unresolved:
        # ★사유별로 나눠 말한다. 「미확정 N건」으로 뭉치면 처분이 서로 다른 것들이 한 문장에
        #   섞여, 사람이 무엇을 해야 하는지 알 수 없다(적대 리뷰 1R P1-1).
        by_status: dict[str, list[str]] = {}
        for ln in resolved:
            if not ln.usable:
                by_status.setdefault(ln.price_status, []).append(ln.label)
        return StandardCostResult(
            computable=False,
            std_cost_ex_vat=None,
            std_cost_inc_vat=None,
            lines=resolved,
            unresolved=unresolved,
            reason=unusable_summary(by_status),
            partial_ex_vat=partial_ex,
            partial_inc_vat=partial_inc,
        )

    return StandardCostResult(
        computable=True,
        std_cost_ex_vat=partial_ex,
        std_cost_inc_vat=partial_inc,
        lines=resolved,
        unresolved=(),
        reason=None,
        partial_ex_vat=partial_ex,
        partial_inc_vat=partial_inc,
    )


def breakdown_payload(result: StandardCostResult) -> list[dict]:
    """`cost_standard.breakdown`에 저장할 근거(JSON 직렬화 대상).

    계산 «시점»의 단가가 남아야 한다(계약 §5-2) — 나중에 원장이 바뀌어도 이 값은 그때 무엇을
    보고 계산했는지 말한다.
    """

    return [
        {
            "label": ln.label,
            "quantity": str(ln.quantity),
            "unit_price_ex_vat": None if ln.unit_price_ex_vat is None else str(ln.unit_price_ex_vat),
            "unit_price_inc_vat": None if ln.unit_price_inc_vat is None else str(ln.unit_price_inc_vat),
            "amount_ex_vat": None if ln.amount_ex_vat is None else str(ln.amount_ex_vat),
            "amount_inc_vat": None if ln.amount_inc_vat is None else str(ln.amount_inc_vat),
            "price_status": ln.price_status,
            "inc_derived": ln.inc_derived,
            "price_source": ln.price_source,
            "price_note": ln.price_note,
            "material_id": ln.material_id,
            "ledger_item_name": ln.ledger_item_name,
        }
        for ln in result.lines
    ]
