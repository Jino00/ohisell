# D-18 판매유형별 판매수수료+VAT 해석기 (Sub-Agent, 순수함수)
#
# 역할: 옵션 1개의 판매수수료+VAT를 3P/2P 경로별 캐스케이드로 결정한다.
#   • 3P(Wing): 실측(service_fee+vat) ▸ 옵션율×1.1 ▸ 기본 7.8%×1.1
#   • 2P(RG):   RG정산 sale_fee(VAT前)×1.1 ▸ 기본 7.8%×1.1 추정
# 이중계상 방지: 2P는 coupang_revenue_fee 경로(3P)를 일절 사용하지 않는다.
# VAT 규칙: 판매수수료에만 ×1.1. 풀필먼트·보관·RG광고 정산액은 금지(이미 실청구).
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

_Z = Decimal("0")
_Q2 = Decimal("0.01")
_DEFAULT_RATE = Decimal("0.078")
_VAT_MULT = Decimal("1.1")

Basis3P = Literal["actual_3p", "option_rate_3p", "default_3p"]
Basis2P = Literal["rg_settled", "rg_estimated"]
Basis = Basis3P | Basis2P


@dataclass(frozen=True)
class CommissionResult:
    commission: Decimal  # 판매수수료+VAT (실청구 기준)
    basis: Basis


def resolve_3p(
    revenue: Decimal,
    *,
    actual_fee: Decimal | None = None,          # service_fee + service_fee_vat (VAT후)
    service_fee_ratio: Decimal | None = None,   # VAT제외율 (e.g. 0.078)
) -> CommissionResult:
    """3P(Wing) 판매수수료+VAT 결정.

    캐스케이드:
      1. actual_fee 있으면 그대로 (실측 — VAT 이미 포함)
      2. service_fee_ratio 있으면 revenue × ratio × 1.1
      3. 기본 7.8% × 1.1
    """
    if actual_fee is not None and actual_fee > _Z:
        return CommissionResult(actual_fee.quantize(_Q2, ROUND_HALF_UP), "actual_3p")
    rate = service_fee_ratio if service_fee_ratio is not None else _DEFAULT_RATE
    commission = (revenue * rate * _VAT_MULT).quantize(_Q2, ROUND_HALF_UP)
    basis: Basis3P = "option_rate_3p" if service_fee_ratio is not None else "default_3p"
    return CommissionResult(commission, basis)


def resolve_2p(
    revenue: Decimal,
    *,
    rg_sale_fee_settled: Decimal | None = None,  # RG정산 sale_fee 옵션행 (VAT前, A-B)
) -> CommissionResult:
    """2P(RG) 판매수수료+VAT 결정.

    캐스케이드:
      1. rg_sale_fee_settled 있으면 × 1.1 (옵션행은 VAT前)
      2. 기본 7.8% × 1.1 추정

    ★이중계상 방지: coupang_revenue_fee 경로(actual_fee, service_fee_ratio) 사용 금지.
    """
    if rg_sale_fee_settled is not None and rg_sale_fee_settled > _Z:
        commission = (rg_sale_fee_settled * _VAT_MULT).quantize(_Q2, ROUND_HALF_UP)
        return CommissionResult(commission, "rg_settled")
    commission = (revenue * _DEFAULT_RATE * _VAT_MULT).quantize(_Q2, ROUND_HALF_UP)
    return CommissionResult(commission, "rg_estimated")
