"""수입건 3중 검산 — 순수 SA (D-CPP-48).

「확정」 저장 전에 통과해야 하는 대조 셋이다. 통과 못 하면 저장을 막는다(계약 §3 금지선).

## 왜 셋인가
서류 3종이 서로를 검증할 수 있는 지점이 정확히 셋이다:

1. **수량 대조 (CI ↔ PL)** — 같은 화물을 두 서류가 각각 센다.
   ★**라인 분해가 서로 다르다.** 8/18 실건에서 CI는 `Glass_Ip16 Pro 350` 한 줄인데
   PL은 `7-9번 박스 300` + `10번 박스 50`으로 나뉜다. 그래서 **라인 대 라인이 아니라
   품목명으로 묶은 합계**로 대조한다.
2. **총액 대조 (CI ↔ 통관경비서)** — 경비서의 `INV Value`가 CI 총액과 같아야 한다.
   다르면 둘 중 하나가 이 선적의 것이 아니거나 라인을 빠뜨린 것이다.
3. **배부 대조 (배부 결과 ↔ 배부 대상)** — Σ배부 == pool. 최대잔여법이 산술로 보장하지만
   **그래도 잰다** — 보장이 깨지는 코드 변경이 나중에 들어올 수 있고, 그때 조용히 틀리는 것보다
   시끄럽게 막히는 게 낫다(교훈 #327: 배분 합계를 안 재서 118,890원이 증발했다).

## 판정 어휘
`ok` / `mismatch`(값이 어긋남) / `missing`(원료 자체가 없음)를 구분한다.
**`missing`을 `ok`로 접지 않는다** — 발견 0건과 실행 안 됨은 같은 숫자로 보인다(교훈 #123).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Literal

CheckStatus = Literal["ok", "mismatch", "missing"]

_ZERO = Decimal("0")

# 품목명 대조의 허용 오차 — 수량은 정수라 0이 맞다.
QTY_TOLERANCE = Decimal("0")
# 외화 총액은 소수 2자리까지 오므로 1전 오차를 허용한다.
AMOUNT_TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class CheckResult:
    key: str
    label: str
    status: CheckStatus
    expected: Decimal | None = None
    actual: Decimal | None = None
    detail: str = ""
    rows: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class ReconcileReport:
    checks: list[CheckResult]

    @property
    def passed(self) -> bool:
        """**전항 통과일 때만 참이다.** `missing`은 통과가 아니다."""
        return bool(self.checks) and all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


def _norm(name: str) -> str:
    """품목명 정규화 — 공백·대소문자만 접는다.

    그 이상(유사도 매칭 등)은 하지 않는다. 왜냐하면 «비슷하니 같은 것»은 추론이고,
    추론으로 검산을 통과시키면 검산이 아니기 때문이다. 표기가 다르면 사람이 고친다.
    """
    return " ".join(name.split()).casefold()


def _sum_by_item(rows: Iterable[tuple[str, Decimal]]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for name, qty in rows:
        out[_norm(name)] = out.get(_norm(name), _ZERO) + qty
    return out


def check_quantity(
    invoice_rows: Iterable[tuple[str, Decimal]],
    packing_rows: Iterable[tuple[str, Decimal]],
) -> CheckResult:
    """①CI 수량합 == PL 수량합 (품목명으로 묶어서)."""
    ci = _sum_by_item(invoice_rows)
    pl = _sum_by_item(packing_rows)
    if not ci or not pl:
        return CheckResult(
            key="quantity",
            label="CI 수량합 = PL 수량합",
            status="missing",
            detail="CI 또는 PL 라인이 비어 있다 — 대조 자체가 불가능하다.",
        )
    diffs = []
    for name in sorted(set(ci) | set(pl)):
        a, b = ci.get(name, _ZERO), pl.get(name, _ZERO)
        if abs(a - b) > QTY_TOLERANCE:
            diffs.append({"item": name, "ci": str(a), "pl": str(b), "diff": str(a - b)})
    ci_total = sum(ci.values(), _ZERO)
    pl_total = sum(pl.values(), _ZERO)
    return CheckResult(
        key="quantity",
        label="CI 수량합 = PL 수량합",
        status="ok" if not diffs else "mismatch",
        expected=ci_total,
        actual=pl_total,
        detail="" if not diffs else f"품목 {len(diffs)}건이 어긋난다.",
        rows=diffs,
    )


def check_invoice_total(
    invoice_total_foreign: Decimal | None,
    declared_inv_value: Decimal | None,
) -> CheckResult:
    """②CI 총액 == 통관경비서의 INV Value."""
    if invoice_total_foreign is None or declared_inv_value is None:
        return CheckResult(
            key="invoice_total",
            label="CI 총액 = 경비서 INV Value",
            status="missing",
            expected=declared_inv_value,
            actual=invoice_total_foreign,
            detail="CI 총액 또는 경비서 INV Value가 입력되지 않았다.",
        )
    ok = abs(invoice_total_foreign - declared_inv_value) <= AMOUNT_TOLERANCE
    return CheckResult(
        key="invoice_total",
        label="CI 총액 = 경비서 INV Value",
        status="ok" if ok else "mismatch",
        expected=declared_inv_value,
        actual=invoice_total_foreign,
        detail="" if ok else f"차이 {invoice_total_foreign - declared_inv_value}",
    )


def check_allocation(
    pool_krw: Decimal,
    allocated_total_krw: Decimal,
    *,
    allocation_ran: bool = True,
    has_costing_lines: bool = True,
) -> CheckResult:
    """③Σ배부액 == 배부대상 총액 (미배분 잔액 0).

    ★**`0 == 0`을 통과로 접지 않는다** (적대 리뷰 P1-1, 2026-08-22). 초판은 차이만 봤는데,
    통관경비서를 한 줄도 안 넣은 건이 `pool=0 · allocated=0`으로 «전항 통과»가 되어
    **통관비 661,620원이 통째로 빠진 단가가 「확정」으로 저장**됐다. 화면은 초록 「미배분 0원」을
    띄우므로 사람이 구분할 방법이 없었다.
    이 모듈이 스스로 선언한 규약(「발견 0건과 실행 안 됨은 같은 숫자로 보인다」, 교훈 #123)을
    나머지 두 검산은 지키고 이것만 안 지키고 있었다.

    - `allocation_ran=False`  → 배부가 예외로 못 돌았다 → `missing`
    - `has_costing_lines=False` → 배부할 원료(원가성 비용 라인)가 없다 → `missing`
    - 그 외 → 차이를 잰다
    """
    if not allocation_ran:
        return CheckResult(
            key="allocation",
            label="Σ배부액 = 배부대상 총액 (미배분 0)",
            status="missing",
            expected=pool_krw,
            actual=None,
            detail="배부가 실행되지 않았다 — 미배분 0원이 아니라 «잰 적 없음»이다.",
        )
    if not has_costing_lines:
        return CheckResult(
            key="allocation",
            label="Σ배부액 = 배부대상 총액 (미배분 0)",
            status="missing",
            expected=pool_krw,
            actual=allocated_total_krw,
            detail=(
                "원가성 비용 라인이 없다 — 통관경비서를 넣지 않았거나 전 라인이 «원가성 아님»이다. "
                "이 상태로 확정하면 통관비가 통째로 빠진 단가가 저장된다."
            ),
        )
    diff = pool_krw - allocated_total_krw
    return CheckResult(
        key="allocation",
        label="Σ배부액 = 배부대상 총액 (미배분 0)",
        status="ok" if diff == _ZERO else "mismatch",
        expected=pool_krw,
        actual=allocated_total_krw,
        detail="" if diff == _ZERO else f"미배분 잔액 {diff}원",
    )


def reconcile(
    invoice_rows: Iterable[tuple[str, Decimal]],
    packing_rows: Iterable[tuple[str, Decimal]],
    invoice_total_foreign: Decimal | None,
    declared_inv_value: Decimal | None,
    pool_krw: Decimal,
    allocated_total_krw: Decimal,
    *,
    allocation_ran: bool = True,
    has_costing_lines: bool = True,
) -> ReconcileReport:
    return ReconcileReport(
        checks=[
            check_quantity(invoice_rows, packing_rows),
            check_invoice_total(invoice_total_foreign, declared_inv_value),
            check_allocation(
                pool_krw,
                allocated_total_krw,
                allocation_ran=allocation_ran,
                has_costing_lines=has_costing_lines,
            ),
        ]
    )
