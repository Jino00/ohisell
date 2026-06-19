# D-18 S2 — RG(2P) 비용 공유 리더 + 미정산 추정 (Sub-Agent)
#
# 역할:
#   • 공유 리더: RG 정산 옵션 그레인(vendor_item_id != "") 배치 로드 → 패널·net_profit 단일 소스
#   • 미정산 추정: 풀필먼트 per-unit, 보관 account-level, RG광고 rate (basis 표기)
#
# 설계 원칙(plan-eng-review 반영):
#   ① 새 평행 합산 금지 — 기존 intelligence._agg_rg_settlement_fees 계정 행은 그대로 유지.
#      이 파일은 옵션 그레인(vendor_item_id != "")만 추가로 읽는 "공유 리더"다.
#   ② VAT: 옵션 행(VAT前 A-B)에만 × 1.1. 계정 행(VAT후)에는 금지.
#   ③ 보관: per-unit-per-option 배분 금지(재고보유시간 비례라 방법론상 부정확).
#      account-level 총액 + coverage 표기.
#   ④ RG광고(미정산): 닫힌 과거 윈도우 비율 × 현재 매출. _agg_rg_ad_overlap 제로체크.
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import CoupangRgOrderItem, CoupangRgSettlementFee

_Z = Decimal("0")
_Q2 = Decimal("0.01")
_VAT_MULT = Decimal("1.1")

StorageBasis = Literal["settled", "account_level", "excluded"]
FulfillBasis = Literal["settled", "per_unit_estimate"]
AdBasis = Literal["settled", "rate_estimate", "overlap_warning"]


# ── DB 공유 리더 (배치주입용, 요청당 1회) ───────────────────────────────

def load_settled_option_costs(
    db: Session,
    dfrom: date,
    dto: date,
    account_keys: list[str],
) -> dict[str, dict]:
    """RG 정산 옵션 그레인 배치 로드 (vendor_item_id != "", VAT前 A-B).

    단일 쿼리로 기간 내 모든 정산 옵션 비용을 fee_type별로 집계.
    반환: {vendor_item_id: {"sale_fee": D, "delivery": D, "warehousing": D,
                             "storage": D, "ad_sales": D, "return": D, "total_pre_vat": D}}
    """
    if not account_keys:
        return {}
    rows = (
        db.query(
            CoupangRgSettlementFee.vendor_item_id,
            CoupangRgSettlementFee.fee_type,
            func.sum(CoupangRgSettlementFee.amount),
        )
        .filter(
            CoupangRgSettlementFee.account_key.in_(account_keys),
            CoupangRgSettlementFee.recognition_date_from <= dto,
            CoupangRgSettlementFee.recognition_date_to >= dfrom,
            CoupangRgSettlementFee.vendor_item_id.isnot(None),
            CoupangRgSettlementFee.vendor_item_id != "",  # 옵션 행만 (계정 sentinel 제외)
        )
        .group_by(
            CoupangRgSettlementFee.vendor_item_id,
            CoupangRgSettlementFee.fee_type,
        )
        .all()
    )
    result: dict[str, dict] = {}
    for vid, fee_type, amount in rows:
        key = str(vid)
        entry = result.setdefault(key, {
            "sale_fee": _Z, "delivery": _Z, "warehousing": _Z,
            "storage": _Z, "ad_sales": _Z, "return": _Z, "total_pre_vat": _Z,
        })
        amt = _f(amount)
        _RG_RETURN = {"return_handling", "return_shipping"}
        if fee_type in _RG_RETURN:
            entry["return"] += amt
        elif fee_type in entry:
            entry[fee_type] = entry[fee_type] + amt
        entry["total_pre_vat"] += amt
    return result


def load_account_storage(
    db: Session,
    dfrom: date,
    dto: date,
    account_keys: list[str],
) -> dict[str, Decimal]:
    """계정 단위 보관료 총액 (account 행, VAT후).

    per-unit 배분 불가(재고보유시간 비례) → account-level 표기용.
    반환: {account_key: Decimal}
    """
    if not account_keys:
        return {}
    rows = (
        db.query(
            CoupangRgSettlementFee.account_key,
            func.sum(CoupangRgSettlementFee.amount),
        )
        .filter(
            CoupangRgSettlementFee.account_key.in_(account_keys),
            CoupangRgSettlementFee.recognition_date_from <= dto,
            CoupangRgSettlementFee.recognition_date_to >= dfrom,
            CoupangRgSettlementFee.vendor_item_id == "",  # 계정 행만
            CoupangRgSettlementFee.fee_type == "storage",
        )
        .group_by(CoupangRgSettlementFee.account_key)
        .all()
    )
    return {str(k): _f(v) for k, v in rows}


def load_rg_ad_rate(
    db: Session,
    dfrom: date,
    dto: date,
    account_keys: list[str],
) -> dict[str, Decimal]:
    """닫힌 과거 윈도우 RG광고비/GMV 비율 (미정산 추정용).

    분자: 계정 행(VAT후) ad_sales 정산 합계
    분모: 동기간 RG 주문 GMV
    반환: {account_key: rate}. 데이터 없으면 {}.
    """
    if not account_keys:
        return {}
    # 분자: 계정행 ad_sales
    ad_rows = (
        db.query(
            CoupangRgSettlementFee.account_key,
            func.sum(CoupangRgSettlementFee.amount),
        )
        .filter(
            CoupangRgSettlementFee.account_key.in_(account_keys),
            CoupangRgSettlementFee.recognition_date_from <= dto,
            CoupangRgSettlementFee.recognition_date_to >= dfrom,
            CoupangRgSettlementFee.vendor_item_id == "",
            CoupangRgSettlementFee.fee_type == "ad_sales",
        )
        .group_by(CoupangRgSettlementFee.account_key)
        .all()
    )
    ad_by_account = {str(k): _f(v) for k, v in ad_rows}
    if not ad_by_account:
        return {}
    # 분모: 동기간 RG GMV
    gmv_rows = (
        db.query(
            CoupangRgOrderItem.account_key,
            func.sum(
                CoupangRgOrderItem.unit_sales_price * CoupangRgOrderItem.sales_quantity
            ),
        )
        .filter(
            CoupangRgOrderItem.account_key.in_(account_keys),
            func.date(CoupangRgOrderItem.paid_at) >= dfrom.isoformat(),
            func.date(CoupangRgOrderItem.paid_at) <= dto.isoformat(),
        )
        .group_by(CoupangRgOrderItem.account_key)
        .all()
    )
    gmv_by_account = {str(k): _f(v) for k, v in gmv_rows}
    rates = {}
    for account_key, ad_amt in ad_by_account.items():
        gmv = gmv_by_account.get(account_key, _Z)
        if gmv > _Z:
            rates[account_key] = (ad_amt / gmv).quantize(Decimal("0.00001"), ROUND_HALF_UP)
    return rates


# ── 순수함수: 옵션 1개의 2P 비용 전체 분해 ────────────────────────────

@dataclass(frozen=True)
class Rg2pCost:
    """2P 옵션 비용 분해 결과."""
    commission: Decimal          # 판매수수료+VAT
    fulfillment: Decimal         # 입출고+배송 (정산 또는 추정)
    storage: Decimal             # 보관료 (정산 시만, 미정산=0)
    rg_ad: Decimal               # RG광고 (정산 또는 추정)
    total: Decimal               # 합계
    commission_basis: str        # from commission_vat_resolver
    fulfillment_basis: FulfillBasis
    storage_basis: StorageBasis
    rg_ad_basis: AdBasis
    storage_account_level: Decimal = field(default=_Z)  # 미정산 시 account-level 참고값


def compute_2p_cost(
    revenue: Decimal,
    qty: int,
    *,
    settled: dict | None = None,          # load_settled_option_costs 결과의 해당 vid 항목
    commission_result: "object | None" = None,  # CommissionResult from S1
    ff_per_unit: Decimal = _Z,            # _rg_fulfillment_per_unit 단가
    ff_fallback: Decimal = _Z,
    account_storage: Decimal = _Z,        # load_account_storage 해당 계정 값
    ad_rate: Decimal | None = None,       # load_rg_ad_rate 해당 계정 비율
    overlap_rg_ad: Decimal = _Z,          # _agg_rg_ad_overlap 반환값
) -> Rg2pCost:
    """2P 옵션 1개의 쿠팡 총비용 분해 (순수함수).

    settled가 있으면 정산 데이터 우선.
    미정산은 estimate로 대체, basis에 표기.
    """
    # ── 1. commission ────────────────────────────────────────────────
    if commission_result is not None:
        commission = commission_result.commission
        comm_basis = commission_result.basis
    else:
        # commission_vat_resolver 없이 직접 호출 시 default 추정
        from app.services.coupang.commission_vat_resolver import resolve_2p
        rg_sale_fee = settled.get("sale_fee") if settled else None
        r = resolve_2p(revenue, rg_sale_fee_settled=rg_sale_fee)
        commission = r.commission
        comm_basis = r.basis

    # ── 2. fulfillment (입출고+배송) ─────────────────────────────────
    if settled and (settled.get("delivery", _Z) > _Z or settled.get("warehousing", _Z) > _Z):
        # 정산 옵션 행은 VAT前(A-B) → × 1.1
        ff_pre_vat = settled.get("delivery", _Z) + settled.get("warehousing", _Z)
        fulfillment = (ff_pre_vat * _VAT_MULT).quantize(_Q2, ROUND_HALF_UP)
        ff_basis: FulfillBasis = "settled"
    else:
        unit = ff_per_unit if ff_per_unit > _Z else ff_fallback
        fulfillment = (unit * qty).quantize(_Q2, ROUND_HALF_UP)
        ff_basis = "per_unit_estimate"

    # ── 3. storage (보관) ────────────────────────────────────────────
    if settled and settled.get("storage", _Z) > _Z:
        # 옵션 행 VAT前 → × 1.1
        storage = (settled["storage"] * _VAT_MULT).quantize(_Q2, ROUND_HALF_UP)
        st_basis: StorageBasis = "settled"
        st_account_level = _Z
    else:
        # account-level만 있음 — 옵션 배분 금지(per-unit 불가)
        storage = _Z
        st_basis = "account_level" if account_storage > _Z else "excluded"
        st_account_level = account_storage

    # ── 4. RG광고 ────────────────────────────────────────────────────
    if settled and settled.get("ad_sales", _Z) > _Z:
        # 옵션 행 VAT前 → × 1.1
        rg_ad = (settled["ad_sales"] * _VAT_MULT).quantize(_Q2, ROUND_HALF_UP)
        ad_basis: AdBasis = "settled"
    elif ad_rate is not None and ad_rate > _Z:
        rg_ad = (revenue * ad_rate).quantize(_Q2, ROUND_HALF_UP)
        ad_basis = "overlap_warning" if overlap_rg_ad > _Z else "rate_estimate"
    else:
        rg_ad = _Z
        ad_basis = "rate_estimate"

    total = commission + fulfillment + storage + rg_ad
    return Rg2pCost(
        commission=commission,
        fulfillment=fulfillment,
        storage=storage,
        rg_ad=rg_ad,
        total=total,
        commission_basis=comm_basis,
        fulfillment_basis=ff_basis,
        storage_basis=st_basis,
        rg_ad_basis=ad_basis,
        storage_account_level=st_account_level,
    )


def _f(v) -> Decimal:
    if v is None:
        return _Z
    return v if isinstance(v, Decimal) else Decimal(str(v))
