"""품목별 관세율 규칙 (D-CPP-57) — 순수 SA. DB도 IO도 모른다.

## 왜 규칙이 필요한가

D-CPP-50이 「관세는 «배부»가 아니라 «귀속»」이라고 정했다 — 품목마다 세율이 다르므로
금액 기준으로 일괄 배부하면 **무관세 품목이 관세를 떠안고 나머지 품목 원가가 과소 계상**된다.
그런데 우리가 가진 서류(자금정산서)에는 **총 관세 금액만 있고 품목별 세율이 없다.**

## 왜 이 값인가 — 추정이 아니라 «재현»이다

Jino 승인(2026-08-25). 근거는 ref 92 전수 실측(필름·유리 5.6% · 부자재 0%)이고,
이 규칙을 **7개 수입건에 독립적으로 대조**해 전건이 반올림 오차 안에서 맞았다:

    SETR2604010220  규칙 210,253 vs 실제 210,250  (+3)
    SETR2604240109  규칙 174,183 vs 실제 174,180  (+3)
    SETR2605100210  규칙 233,838 vs 실제 233,830  (+8)
    SETR2605170105  규칙 303,383 vs 실제 303,380  (+3)
    SETR2606140215  규칙 338,684 vs 실제 338,680  (+4)
    SETR2607120215  규칙 413,916 vs 실제 413,910  (+6)
    SETR2607220324  규칙  15,204 vs 실제  15,200  (+4)

★마지막 건이 특히 규칙을 증명한다 — 관세가 15,200원으로 유난히 작았는데, 그 화물은
**88.7%가 cleaning kits(무관세)**였고 규칙이 그 값을 그대로 재현했다. 「대충 5.6%」로는
안 나오는 숫자다.

## 그래서 이 모듈은 «검산 가능한 추론»만 한다

`verify_against_document`가 있는 이유가 그것이다 — 규칙을 적용하는 쪽은 **반드시 실제
관세와 대조**하고, 어긋나면 값을 채우지 말고 사람에게 넘긴다. 규칙이 언제까지 참인지
우리는 모른다(관세율은 바뀔 수 있고 새 품목군이 들어올 수 있다). 검산이 그 경계를 지킨다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

_ZERO = Decimal("0")

#: 무관세 부자재. **실측에서 이것 하나뿐이었다**(CI 16건 전수 어휘: Glass 149 · Privacy 51 ·
#: For iPhone…(문장형) 16 · 2.5D Clear Glass(삼성) 13 · cleaning kits 9).
#: 새 부자재가 들어오면 검산이 어긋나 «사람에게 넘어간다» — 조용히 틀리지 않는다.
_MATERIAL_RE = re.compile(r"cleaning\s*kits?", re.IGNORECASE)

#: 필름·유리 관세율. 품목 수준 고정값(ref 92 전수 실측).
RATE_PRODUCT = Decimal("0.056")
#: 부자재 관세율.
RATE_MATERIAL = _ZERO

#: 검산 허용 오차. 라인별 반올림이 쌓이는 폭이다 — 실측 7건에서 최대 +8원이었다.
#: 비율이 아니라 «절대액»으로 두는 이유: 소액 화물(관세 15,200원)에서 비율 기준은
#: 반올림 8원을 0.05%로 통과시키지만, 고액 화물에서 같은 비율은 200원을 통과시킨다.
VERIFY_TOLERANCE_KRW = Decimal("100")


def is_material(item_name: str) -> bool:
    """부자재(무관세)인가. 이름 매칭 하나에 의존한다 — 그래서 검산이 필수다."""
    return bool(_MATERIAL_RE.search(item_name or ""))


def duty_rate_for(item_name: str) -> Decimal:
    """품목명 → 관세율. **`None`을 돌려주지 않는다** — 이 함수는 «규칙»이고,
    규칙을 쓸지 말지는 검산 결과를 본 호출부가 정한다."""
    return RATE_MATERIAL if is_material(item_name) else RATE_PRODUCT


@dataclass(frozen=True)
class DutyVerification:
    """규칙 ↔ 서류 대조 결과."""

    computed_krw: Decimal
    document_krw: Decimal | None
    #: 과세표준(원). 라인별 안분의 분모다.
    customs_value_krw: Decimal | None
    #: 대조에 쓴 물품 라인 수. **0이면 통과가 아니다** — 아래 `ok` 참조.
    line_count: int = 0

    @property
    def diff(self) -> Decimal:
        return self.computed_krw - (self.document_krw or _ZERO)

    @property
    def ok(self) -> bool:
        """서류에 관세가 없으면 «대조 못 함»이지 «통과»가 아니다.

        ★`line_count == 0`도 실패다(테스트가 잡은 결함, 2026-08-25): CI를 못 붙인
        수입건은 계산값이 0원인데, 서류 관세가 작은 화물(예: 15,200원 중 일부)에서는
        `|0 − N| ≤ 허용오차`가 우연히 성립해 **「검산 통과」로 관세율이 채워진다.**
        아무것도 대조하지 않고 통과하는 것이 이 함수가 막아야 할 유일한 사고다.
        """
        if self.document_krw is None or self.customs_value_krw is None:
            return False
        if self.line_count <= 0:
            return False
        return abs(self.diff) <= VERIFY_TOLERANCE_KRW

    @property
    def reason(self) -> str:
        if self.customs_value_krw is None:
            return "과세금액을 못 읽어 라인별 안분을 할 수 없다 — 관세율을 채우지 않는다."
        if self.document_krw is None:
            return "정산서에 관세 라인이 없어 대조할 수 없다 — 관세율을 채우지 않는다."
        if self.line_count <= 0:
            return "대조할 물품 라인이 없다(CI 미첨부) — 관세율을 채우지 않는다."
        if self.ok:
            return f"규칙 {self.computed_krw:.0f} ↔ 서류 {self.document_krw} (차 {self.diff:+.0f}원)"
        return (
            f"규칙 {self.computed_krw:.0f} vs 서류 {self.document_krw} — "
            f"차 {self.diff:+.0f}원이 허용 오차 {VERIFY_TOLERANCE_KRW}원을 넘는다. "
            "새 품목군이 섞였거나 세율이 바뀌었을 수 있다 — 사람이 확인해야 한다."
        )


def verify_against_document(
    *,
    line_amounts_foreign: list[tuple[str, Decimal]],
    customs_value_krw: Decimal | None,
    document_duty_krw: Decimal | None,
) -> DutyVerification:
    """규칙대로 계산한 관세를 서류의 실제 관세와 대조한다.

    `line_amounts_foreign`은 `(품목명, 외화 금액)`이다. 과세표준(원)을 외화 금액 비율로
    안분해 라인별 원화 과세표준을 만들고 거기에 세율을 곱한다 — 관세는 원화 과세가격에
    부과되므로 «환산 후 안분»이 서류의 계산 순서와 같다.
    """
    total_foreign = sum((amt for _, amt in line_amounts_foreign), _ZERO)
    n = len(line_amounts_foreign)
    if customs_value_krw is None or total_foreign <= _ZERO:
        return DutyVerification(_ZERO, document_duty_krw, customs_value_krw, n)

    computed = _ZERO
    for name, amt in line_amounts_foreign:
        base_krw = customs_value_krw * amt / total_foreign
        computed += base_krw * duty_rate_for(name)
    return DutyVerification(computed, document_duty_krw, customs_value_krw, n)
