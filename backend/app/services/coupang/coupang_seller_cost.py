# D-18 coupang_seller_cost Harness — 판매유형별 쿠팡 총비용 유통 허브 (원칙18-6)
#
# 역할: S1·S2 Sub-Agent 결과를 받아 옵션별 {seller_cost, components, basis}를 반환한다.
#   • 입력: 요청당 1회 배치 로드된 dict들 (N+1 금지, 원칙18-8)
#   • 출력: {vid: SellerCostResult}
#
# 라우터가 직접 SA를 호출하지 않는다 — 반드시 이 Harness를 거친다(원칙18-7).
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.coupang.commission_vat_resolver import resolve_2p, resolve_3p
from app.services.coupang.rg_cost_reader import compute_2p_cost

_Z = Decimal("0")
_Q2 = Decimal("0.01")


@dataclass(frozen=True)
class SellerCostResult:
    """옵션 1개의 쿠팡 총비용 분해."""
    seller_cost: Decimal       # 쿠팡 총비용 합계
    commission: Decimal        # 판매수수료+VAT
    fulfillment: Decimal       # 입출고+배송 (2P만)
    storage: Decimal           # 보관 (정산 시만, 미정산=0)
    rg_ad: Decimal             # RG광고 (2P만)
    commission_basis: str
    fulfillment_basis: str
    storage_basis: str
    rg_ad_basis: str
    storage_account_level: Decimal  # 미정산 보관 참고값(account-level)


def compute_batch(
    vids: list[str],
    *,
    ch_type_by_vid: dict[str, str],           # vid → "Wing" | "로켓그로스"
    revenue_by_vid: dict[str, Decimal],
    qty_by_vid: dict[str, int],
    account_by_vid: dict[str, str],           # vid → account_key (pi_map 기반)
    actual_fee_by_vid: dict[str, Decimal],    # 3P: service_fee+vat 실측
    ratio_by_vid: dict[str, Decimal],         # 3P: service_fee_ratio (VAT제외)
    settled_option: dict[str, dict],          # rg_cost_reader.load_settled_option_costs
    ff_per_unit: dict[str, Decimal],          # _rg_fulfillment_per_unit
    ff_fallback: Decimal,
    account_storage: dict[str, Decimal],      # rg_cost_reader.load_account_storage
    ad_rate: dict[str, Decimal],              # rg_cost_reader.load_rg_ad_rate
    overlap_rg_ad: Decimal = _Z,
) -> dict[str, SellerCostResult]:
    """옵션 목록에 대한 쿠팡 총비용을 판매유형별로 분해 반환.

    3P(Wing): S1 resolve_3p — commission만 (풀필먼트=한진, Harness 외부에서 처리)
    2P(RG):   S1+S2 resolve_2p + compute_2p_cost — commission+fulfillment+storage+rg_ad
    """
    result: dict[str, SellerCostResult] = {}
    for vid in vids:
        ch_type = ch_type_by_vid.get(vid, "Wing")
        revenue = revenue_by_vid.get(vid, _Z)
        qty = qty_by_vid.get(vid, 0)
        account_key = account_by_vid.get(vid)

        if ch_type == "Wing":
            # 3P: 판매수수료+VAT만 (한진 배송비는 라우터에서 별도 처리)
            r3p = resolve_3p(
                revenue,
                actual_fee=actual_fee_by_vid.get(vid),
                service_fee_ratio=ratio_by_vid.get(vid),
            )
            result[vid] = SellerCostResult(
                seller_cost=r3p.commission,
                commission=r3p.commission,
                fulfillment=_Z,
                storage=_Z,
                rg_ad=_Z,
                commission_basis=r3p.basis,
                fulfillment_basis="n/a",
                storage_basis="n/a",
                rg_ad_basis="n/a",
                storage_account_level=_Z,
            )

        elif ch_type == "로켓그로스":
            # 2P: S1+S2 결합
            r2p_comm = resolve_2p(
                revenue,
                rg_sale_fee_settled=settled_option.get(vid, {}).get("sale_fee"),
            )
            r2p = compute_2p_cost(
                revenue, qty,
                settled=settled_option.get(vid),
                commission_result=r2p_comm,
                ff_per_unit=ff_per_unit.get(vid, _Z),
                ff_fallback=ff_fallback,
                account_storage=account_storage.get(account_key, _Z) if account_key else _Z,
                ad_rate=ad_rate.get(account_key) if account_key else None,
                overlap_rg_ad=overlap_rg_ad,
            )
            result[vid] = SellerCostResult(
                seller_cost=r2p.total,
                commission=r2p.commission,
                fulfillment=r2p.fulfillment,
                storage=r2p.storage,
                rg_ad=r2p.rg_ad,
                commission_basis=r2p.commission_basis,
                fulfillment_basis=r2p.fulfillment_basis,
                storage_basis=r2p.storage_basis,
                rg_ad_basis=r2p.rg_ad_basis,
                storage_account_level=r2p.storage_account_level,
            )
        # 1P(로켓배송) 등 미분류: 결과 없음(라우터에서 fee_legacy 유지)

    return result
