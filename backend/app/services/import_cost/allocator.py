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
    is_costing: bool = True      # 원가 대상인가 (부가세 라인은 False)
    # ★관세는 «배부»가 아니라 «귀속»이다 (D-CPP-50). 품목마다 세율이 다르기 때문이다 —
    #   실측 2건에서 cleaning kits(부자재)는 0%, 유리·필름은 5.6%였다.
    #   금액 기준으로 일괄 배부하면 무관세 품목이 관세를 떠안고 나머지 품목 원가가 과소 계상된다
    #   (7/22 건 실측: 유리 개당 −135원 = 원가의 5.0%).
    is_duty: bool = False

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
    # 품목별 관세율(0.056 = 5.6%). **None은 «모름»이지 0%가 아니다** — 하나라도 값이 있으면
    # 관세를 라인별로 귀속하고, 전부 None이면 종전대로 공통비에 섞어 배부한다(사유가 결과에 실린다).
    duty_rate: Decimal | None = None

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
    allocated_cost_krw: Decimal    # 배부받은 통관비 합계 = 공통비 + 관세 (원 단위 정수)
    unit_cost_ex_vat: Decimal      # (물품대 + 배부액) / 수량
    unit_cost_inc_vat: Decimal     # ex_vat × 1.1
    # 내역을 갈라 둔다 — 「이 품목이 관세를 얼마 물었나」가 화면에서 보여야 세율 입력이 검증된다.
    allocated_common_krw: Decimal = _ZERO
    allocated_duty_krw: Decimal = _ZERO

    @property
    def total_cost_ex_vat(self) -> Decimal:
        return self.goods_amount_krw + self.allocated_cost_krw


@dataclass(frozen=True)
class AllocationResult:
    lines: list[AllocatedLine]
    pool_krw: Decimal              # 배부 대상 총액(공통비 + 관세)
    allocated_total_krw: Decimal   # Σ배부액
    basis: AllocationBasis
    fx_rate: Decimal
    common_pool_krw: Decimal = _ZERO   # 운임·수수료 등 — basis로 배부
    duty_pool_krw: Decimal = _ZERO     # 관세 — 라인 세율로 귀속
    duty_mode: str = "blended"         # "by_rate"(라인별 세율) | "blended"(세율 미입력 → 공통비에 섞음)
    # 라인 세율로 «계산»한 관세 총액. 서류의 관세 총액(duty_pool_krw)과 대조하면 세율이 맞는지 보인다.
    duty_computed_krw: Decimal | None = None

    @property
    def unallocated_krw(self) -> Decimal:
        """미배분 잔액. **항상 0이어야 한다** — 0이 아니면 산술 결함이다."""
        return self.pool_krw - self.allocated_total_krw

    @property
    def duty_check_diff(self) -> Decimal | None:
        """서류 관세 − 라인 세율로 계산한 관세. 0에 가까울수록 세율이 맞다.

        **이 값이 크면 세율 입력이 틀렸다는 신호다.** 실측(2026-08-22) 두 건에서 오차는 4원과
        0.0001%p였다 — 맞는 세율이면 이 정도로 재현된다.
        """
        if self.duty_computed_krw is None:
            return None
        return self.duty_pool_krw - self.duty_computed_krw


class AllocationError(ValueError):
    """배부가 원리적으로 불가능할 때. 조용히 0을 반환하지 않는다."""


# ──────────────────────────────────────────────
# 배부
# ──────────────────────────────────────────────
def costing_pool(cost_lines: Iterable[CostLine]) -> Decimal:
    """배부 대상 총액 = 원가성 라인의 **공급가액** 합계(공통비 + 관세).

    세액(`tax_amount`)은 더하지 않는다 — 매입세액 공제 대상이라 원가가 아니다.
    """
    return sum((c.supply_amount for c in cost_lines if c.is_costing), _ZERO)


def duty_pool(cost_lines: Iterable[CostLine]) -> Decimal:
    """관세 총액 — 원가이지만 **배부가 아니라 귀속** 대상이다(D-CPP-50)."""
    return sum((c.supply_amount for c in cost_lines if c.is_costing and c.is_duty), _ZERO)


def common_pool(cost_lines: Iterable[CostLine]) -> Decimal:
    """공통비 총액 — 운임·통관수수료 등. `basis`로 배부한다."""
    return sum(
        (c.supply_amount for c in cost_lines if c.is_costing and not c.is_duty), _ZERO
    )


def _largest_remainder(pool: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """`pool`을 `weights` 비율로 원 단위로 나눈다. **Σ == pool을 산술로 보장**한다.

    가중치 합이 0이면 전액을 첫 칸에 둔다 — 돈을 없애지 않는 것이 이 함수의 계약이다.
    """
    n = len(weights)
    total_w = sum(weights, _ZERO)
    if pool == _ZERO:
        return [_ZERO] * n
    if total_w <= _ZERO:
        return [pool] + [_ZERO] * (n - 1)
    exact = [pool * w / total_w for w in weights]
    floors = [e.to_integral_value(rounding=ROUND_FLOOR) for e in exact]
    remainder = int(pool - sum(floors, _ZERO))
    order = sorted(
        range(n), key=lambda i: (exact[i] - floors[i], weights[i], -i), reverse=True
    )
    out = list(floors)
    for k in range(max(remainder, 0)):
        out[order[k % n]] += _ONE
    return out


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
    customs_value_krw: Decimal | None = None,
) -> AllocationResult:
    """통관비를 인보이스 라인에 배부한다.

    Σ배부액 == 배부대상 총액이 **산술로 보장**된다(최대잔여법).

    비용은 **두 몫**으로 갈린다(D-CPP-50):
    - **공통비**(운임·통관수수료 등) → `basis`로 배부
    - **관세** → 라인의 `duty_rate`로 **귀속**. 세율이 하나도 없으면 종전대로 공통비에 섞는다
      (`duty_mode="blended"`가 결과에 실려 «세율을 안 넣어서 섞였다»가 화면에 보인다).

    `customs_value_krw`는 서류의 과세금액이다. 관세 과세표준은 CIF라 물품대와 다르므로
    있으면 그걸 쓰고, 없으면 물품대 합으로 근사한다.
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

    # ── 관세를 «귀속»으로 가를지 결정한다 (D-CPP-50) ──
    duty_total = duty_pool(cost_lines).quantize(_ONE, ROUND_HALF_UP)
    has_rates = any(ln.duty_rate is not None for ln in invoice_lines)
    duty_mode = "by_rate" if (has_rates and duty_total > _ZERO) else "blended"

    if duty_mode == "by_rate":
        common_total = (pool - duty_total).quantize(_ONE, ROUND_HALF_UP)
        # 관세 과세표준: 서류의 과세금액이 있으면 그것을, 없으면 물품대 합을 쓴다.
        # (과세가격은 CIF라 물품대와 조금 다르다 — 있으면 서류 값이 정확하다.)
        amount_total = sum((ln.amount_foreign for ln in invoice_lines), _ZERO)
        base_total = customs_value_krw if customs_value_krw is not None else (
            amount_total * fx_rate
        )
        # 라인별 «계산 관세» = 과세표준 몫 × 라인 세율. 이게 관세 배부의 **가중치**가 된다.
        computed = [
            (base_total * (ln.amount_foreign / amount_total) * (ln.duty_rate or _ZERO))
            if amount_total > _ZERO
            else _ZERO
            for ln in invoice_lines
        ]
        duty_computed = sum(computed, _ZERO).quantize(_CENT, ROUND_HALF_UP)
        # ★서류의 관세 총액을 «계산값 비율»로 나눈다 — 세율이 맞으면 계산값과 거의 같고,
        #   틀려도 Σ == 서류 총액은 깨지지 않는다(돈이 새거나 생기지 않는다).
        duty_alloc = _largest_remainder(duty_total, computed)
        common_alloc = _largest_remainder(common_total, weights)
    else:
        common_total = pool
        duty_computed = None
        duty_alloc = [_ZERO] * len(invoice_lines)
        common_alloc = _largest_remainder(pool, weights)

    out: list[AllocatedLine] = []
    for ln, c, d in zip(invoice_lines, common_alloc, duty_alloc):
        a = c + d
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
                allocated_common_krw=c,
                allocated_duty_krw=d,
            )
        )
    alloc = [x.allocated_cost_krw for x in out]

    return AllocationResult(
        lines=out,
        pool_krw=pool,
        allocated_total_krw=sum(alloc, _ZERO),
        basis=basis,
        fx_rate=fx_rate,
        common_pool_krw=common_total,
        duty_pool_krw=duty_total if duty_mode == "by_rate" else _ZERO,
        duty_mode=duty_mode,
        duty_computed_krw=duty_computed,
    )
