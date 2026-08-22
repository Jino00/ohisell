"""수입 통관비 배부 — 순수 SA (D-CPP-48).

이 모듈은 **DB도 IO도 모른다.** 입력은 값 객체, 출력은 값 객체다.
왜냐하면 배부 산술이 «감시자가 감시 대상보다 낡는» 형태로 두 벌 생기면 안 되기 때문이다
(ref 54 §9 실증). 라우터·파서·테스트가 전부 이 한 벌을 임포트한다.

## 무엇을 배부하나
통관경비서의 비용 라인 중 **원가성(`is_costing=True`)인 것의 공급가액 합계**를 배부한다.
- 8/18 실건(SETR2608170216): B/L 소계 386,950 + 관세 249,670 + 통관수수료 25,000 = **661,620원**
- **부가세(511,230원)는 배부 대상이 아니다** — 매입세액으로 공제받으므로 원가가 아니라
  국가에 대한 채권이다. 단 그 값은 버리지 않고 비용 라인에 그대로 보존한다.
  ★「공제받는 매입세액은 취득원가에서 제외」는 **법령 조문으로 확인 안 됐다**
  (법인세법 시행령 §72에 「부가가치세」·「매입세액」·「관세」라는 단어가 없다 — 2026-08-22
  국가법령정보센터 원문 대조). 회계기준의 해석이고 세무사 확인 대기 중이다.
  그래서 «원가 정의»를 하나로 못 박지 않고 두 값을 다 저장한다(아래).

## 배부기준 (D-CPP-48 ①)
**금액(인보이스 가액) 기준**이 기본이다. 8/18 실건으로 4기준을 전부 계산해 고른 결과다:
금액·부피·중량은 0.3% 이내로 수렴하고, 수량 기준만 부자재(cleaning kits 2,400개 = 전체 수량의
59%)가 통관비의 59.3%를 흡수해 왜곡된다. 관세 자체가 가액 비례로 매겨지니 성격도 맞다.
`weight`·`volume`·`quantity`도 구현해 둔다 — 기준을 바꿔도 같은 코드가 돌아야 비교가 가능하다.

## 왜 최대잔여법(largest remainder)인가
원 단위로 반올림해 나눠 담으면 합이 총액과 안 맞는다. 그 잔액이 화면에서 «미배분»으로 보이면
사용자는 결함으로 읽고, 안 보이면 돈이 조용히 증발한다(교훈 #327 — 2026-08-19 광고비 배분
사고가 정확히 «원장 기준으로 세다가 282개 상품 118,890원 증발»이었다).
그래서 **Σ배부 == 배부대상 총액**을 산술로 보장한다 — 검산이 통과하도록 값을 맞추는 게 아니라,
애초에 어긋날 수 없게 나눈다.

## 부가세 두 값 (D-CPP-48 ②)
`unit_cost_ex_vat`(제외)와 `unit_cost_inc_vat`(포함)를 **계산 시점에 확정 저장**한다.
포함값은 `제외값 × 1.1`이다 — 왜냐하면 손익 엔진 전 축이 D-NAO-150(Jino 원문
*"Sellc에 들어있는 원가는 부가세 포함된 가격이야."*)으로 통일돼 있고 `payable_vat()`가
`×10/110`으로 매입세액을 역산하기 때문이다. 엔진의 규약과 같은 모양이어야 한다.

⚠️ **이 ×1.1은 «실제로 낸 부가세»가 아니다.** 실건에서 해상운임 등은 세액이 0이고,
실제 세액 합계(511,230 + 9,000 + 2,500 = 522,730원)를 같은 기준으로 배부하면 Ip17Pro 개당
3,186원이 되어 ×1.1의 3,201원과 15원(0.5%) 차이가 난다.
**실제 세액은 비용 라인(`tax_amount`)에 원본 그대로 남는다** — 회계 목적이면 그쪽을 봐야 한다.
여기서 ×1.1을 쓰는 이유는 «회계적 정확성»이 아니라 «기존 엔진 축과의 호환»이다.
`actual_vat_pool()`이 그 실제 세액 합계를 따로 내주므로 둘을 대조할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Iterable, Literal

AllocationBasis = Literal["amount", "weight", "volume", "quantity"]

ALLOCATION_BASES: tuple[AllocationBasis, ...] = ("amount", "weight", "volume", "quantity")

VAT_MULTIPLIER = Decimal("1.1")  # D-NAO-150 규약 — 실제 세액이 아니다(모듈 docstring 참조)

_ZERO = Decimal("0")
_ONE = Decimal("1")
_CENT = Decimal("0.01")


# ──────────────────────────────────────────────
# 값 객체
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class CostLine:
    """통관경비서의 비용 한 줄."""

    item_name: str
    supply_amount: Decimal       # 예상공급가액
    tax_amount: Decimal = _ZERO  # 예상세액
    is_costing: bool = True      # 배부 대상인가 (부가세 라인은 False)

    @property
    def total(self) -> Decimal:
        return self.supply_amount + self.tax_amount


@dataclass(frozen=True)
class InvoiceLine:
    """Commercial Invoice의 한 줄 = 배부를 받는 단위."""

    seq: int
    item_name: str
    quantity: Decimal
    unit_price_foreign: Decimal
    # 배부 기준 원료 — Packing List에서 온다. 없으면 그 기준으로는 배부 불가(예외를 던진다).
    gross_weight_kg: Decimal | None = None
    cbm: Decimal | None = None

    @property
    def amount_foreign(self) -> Decimal:
        return self.quantity * self.unit_price_foreign


@dataclass(frozen=True)
class AllocatedLine:
    """배부 결과 한 줄. 값은 전부 확정 저장 대상이다."""

    seq: int
    item_name: str
    quantity: Decimal
    goods_amount_krw: Decimal      # 물품대 (외화 × 신고환율)
    allocated_cost_krw: Decimal    # 배부받은 통관비 (원 단위 정수)
    unit_cost_ex_vat: Decimal      # (물품대 + 배부액) / 수량
    unit_cost_inc_vat: Decimal     # ex_vat × 1.1

    @property
    def total_cost_ex_vat(self) -> Decimal:
        return self.goods_amount_krw + self.allocated_cost_krw


@dataclass(frozen=True)
class AllocationResult:
    lines: list[AllocatedLine]
    pool_krw: Decimal              # 배부 대상 총액
    allocated_total_krw: Decimal   # Σ배부액
    basis: AllocationBasis
    fx_rate: Decimal

    @property
    def unallocated_krw(self) -> Decimal:
        """미배분 잔액. **항상 0이어야 한다** — 0이 아니면 산술 결함이다."""
        return self.pool_krw - self.allocated_total_krw


class AllocationError(ValueError):
    """배부가 원리적으로 불가능할 때. 조용히 0을 반환하지 않는다."""


# ──────────────────────────────────────────────
# 배부
# ──────────────────────────────────────────────
def costing_pool(cost_lines: Iterable[CostLine]) -> Decimal:
    """배부 대상 총액 = 원가성 라인의 **공급가액** 합계.

    세액(`tax_amount`)은 더하지 않는다 — 매입세액 공제 대상이라 원가가 아니다.
    """
    return sum((c.supply_amount for c in cost_lines if c.is_costing), _ZERO)


def actual_vat_pool(cost_lines: Iterable[CostLine]) -> Decimal:
    """실제 세액 합계 — 원가성 여부와 무관하게 전 라인의 세액 + 부가세 라인의 공급가액.

    ×1.1 규약과 대조하기 위한 값이다(모듈 docstring의 ⚠️ 참조). 배부에는 쓰이지 않는다.
    부가세 라인은 `is_costing=False`이고 그 «공급가액» 칸에 세액이 적혀 오므로 함께 센다.
    """
    total = _ZERO
    for c in cost_lines:
        total += c.tax_amount
        if not c.is_costing:
            total += c.supply_amount
    return total


def _basis_weights(lines: list[InvoiceLine], basis: AllocationBasis) -> list[Decimal]:
    if basis == "amount":
        return [ln.amount_foreign for ln in lines]
    if basis == "quantity":
        return [ln.quantity for ln in lines]
    if basis == "weight":
        raw: list[Decimal | None] = [ln.gross_weight_kg for ln in lines]
    elif basis == "volume":
        raw = [ln.cbm for ln in lines]
    else:
        raise AllocationError(f"알 수 없는 배부기준: {basis}")
    missing = [lines[i].item_name for i, v in enumerate(raw) if v is None]
    if missing:
        raise AllocationError(
            f"배부기준 '{basis}'에 필요한 값이 없는 라인이 있다: {missing}. "
            "Packing List 대조를 먼저 통과시켜야 한다."
        )
    return [v for v in raw if v is not None]


def allocate(
    invoice_lines: list[InvoiceLine],
    cost_lines: list[CostLine],
    fx_rate: Decimal,
    basis: AllocationBasis = "amount",
) -> AllocationResult:
    """통관비를 인보이스 라인에 배부한다.

    Σ배부액 == 배부대상 총액이 **산술로 보장**된다(최대잔여법).
    """
    if not invoice_lines:
        raise AllocationError("인보이스 라인이 없다 — 배부할 대상이 없다.")
    if fx_rate <= _ZERO:
        raise AllocationError(f"환율이 유효하지 않다: {fx_rate}")
    for ln in invoice_lines:
        if ln.quantity <= _ZERO:
            raise AllocationError(f"수량이 0 이하인 라인이 있다: {ln.item_name}")

    pool = costing_pool(cost_lines)
    if pool < _ZERO:
        raise AllocationError(f"배부 대상 총액이 음수다: {pool}")
    # 배부는 원 단위로 한다 — 소수 원이 남으면 «미배분 잔액»이 영원히 0이 안 된다.
    pool = pool.quantize(_ONE, ROUND_HALF_UP)

    weights = _basis_weights(invoice_lines, basis)
    if any(w < _ZERO for w in weights):
        raise AllocationError(f"배부기준 '{basis}'에 음수 값이 있다.")
    total_weight = sum(weights, _ZERO)
    if total_weight <= _ZERO:
        raise AllocationError(
            f"배부기준 '{basis}'의 합계가 0이다 — 비율을 만들 수 없다."
        )

    # 1차: 내림. 잔여는 소수부가 큰 순서로 1원씩 나눠 준다(최대잔여법).
    exact = [pool * w / total_weight for w in weights]
    floors = [e.to_integral_value(rounding=ROUND_FLOOR) for e in exact]
    remainder = int(pool - sum(floors, _ZERO))
    order = sorted(
        range(len(exact)),
        key=lambda i: (exact[i] - floors[i], weights[i], -i),
        reverse=True,
    )
    alloc = list(floors)
    for k in range(max(remainder, 0)):
        alloc[order[k % len(order)]] += _ONE

    out: list[AllocatedLine] = []
    for ln, a in zip(invoice_lines, alloc):
        goods = (ln.amount_foreign * fx_rate).quantize(_CENT, ROUND_HALF_UP)
        unit_ex = ((goods + a) / ln.quantity).quantize(_CENT, ROUND_HALF_UP)
        unit_inc = (unit_ex * VAT_MULTIPLIER).quantize(_CENT, ROUND_HALF_UP)
        out.append(
            AllocatedLine(
                seq=ln.seq,
                item_name=ln.item_name,
                quantity=ln.quantity,
                goods_amount_krw=goods,
                allocated_cost_krw=a,
                unit_cost_ex_vat=unit_ex,
                unit_cost_inc_vat=unit_inc,
            )
        )

    return AllocationResult(
        lines=out,
        pool_krw=pool,
        allocated_total_krw=sum(alloc, _ZERO),
        basis=basis,
        fx_rate=fx_rate,
    )
