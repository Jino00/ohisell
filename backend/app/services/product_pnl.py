# product_pnl.py — 상품 연관맵 S3(Harness 3): 통합 손익 조망 — 대조원장 우선(reconciliation-first)
# 트랙: docs/tracks/active/track_product-connection-map.md (D-7~D-11) / 계획: docs/PLAN_product-connection-map-s3.md
#
# 핵심(D-6): 옵션(SKU)별 손익표를 먼저 만들지 않는다. "돈이 사라지지 않음"을 증명하는
#   대조원장(reconciliation ledger)을 먼저 만든다. 채널·컴포넌트마다 보존 법칙
#     Σ(SKU 귀속) + Σ(잔차 버킷) == 기존 권위 엔진 계정 소계   (정확 성립, tolerance 아님)
#   이 성립해야만 SKU 행을 신뢰(원장 균형 후에만 노출). 불균형이면 조인/기준 버그 표면화.
#
# 재사용(D1): intelligence._agg_* / compute_command_center·compute_rocket_overview를 권위 기준으로
#   소비(재구축 안 함). SA는 vid/line을 internal_sku로 재귀속만 신규. 잔차 버킷은 독립 계산 →
#   보존 법칙이 tautology가 아니라 실제 조인 버그를 잡는다.
from __future__ import annotations

import logging
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    Channel, CoupangRocketPurchaseOrderItem, Order, ProductChannelMapping,
    ProductMaster, RocketProductCostMap,
)
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED
from app.services.coupang.intelligence import (
    _agg_orders, _agg_rg_orders, _agg_rg_settlement_fees, _resolve_account,
    compute_command_center,
)
from app.services.coupang.rocket_intelligence import (
    _kst_window_utc, compute_rocket_overview,
)
from app.services.profit_calculator import _line_commission, _line_revenue

log = logging.getLogger(__name__)

_Z = Decimal("0")
_WON = Decimal("0.01")  # 원 단위 잔차 판정 스케일(D5 회귀: 순환소수 나눗셈이 만드는 20+자리 소수 잡음 흡수용)
# RG 정산 옵션수수료 VAT gross-up 계수(D-9). 옵션 row=VAT前 할인적용가(A−B), 계정 row=VAT後.
# 한국 부가세 10% → ×1.1(rg_settlement_sync.py:301 "Σ상세(A−B)+요약세액==status/api VAT後",
# 필드명 totalTakeRateAmountWithVat 등 WithVat=VAT포함). 입출고/배송/보관만 옵션 row 존재,
# 판매수수료·ad_sales는 계정 전용 → gross-up 대상 아님(rg_vat_residual로 흡수).
_RG_VAT_GROSSUP = Decimal("1.1")


# ──────────────────────────────────────────────
# SA: 옵션ID(vendor_item_id) → internal_sku 다리 (쿠팡 활성 매핑, 결정적)
# ──────────────────────────────────────────────
def _resolve_vid_sku(db: Session) -> tuple[dict[str, str], set[str]]:
    """쿠팡(platform=coupang) is_active 매핑에서 {channel_product_id(vid): internal_sku}.

    ★충돌 vid는 SKU 귀속하지 않는다(codex T3 P1#1, D-7 no-auto-merge). 한 vid가 서로 다른
    internal_sku 여러 개에 매핑되면(무결성 결손) 임의 선택이 권위 엔진 _cost_master(cost>0 우선
    선택)와 달라 SKU별 돈 귀속이 틀어진다(총합 보존은 통과해도). 따라서 충돌 vid는 vid_sku에서
    제외 → _partition_by_sku가 미매핑 잔차로 보낸다(어느 SKU로도 안 셈, 사실만 표면화). 충돌
    목록은 conflicts로 별도 리포트. T2 발견: _cost_master는 internal_sku를 노출하지 않으므로
    여기서 product_channel_mapping을 직접 조회한다.
    """
    products = {p.id: p.internal_sku for p in db.query(ProductMaster).all()}
    rows = (
        db.query(ProductChannelMapping.channel_product_id, ProductChannelMapping.product_id)
        .join(Channel, ProductChannelMapping.channel_id == Channel.id)
        .filter(Channel.platform == "coupang", ProductChannelMapping.is_active.is_(True))
        .all()
    )
    candidates: dict[str, set[int]] = {}
    for vid, pid in rows:
        if products.get(pid):  # internal_sku 존재(빈값/None 매핑 제외)
            candidates.setdefault(str(vid), set()).add(pid)
    vid_sku: dict[str, str] = {}
    conflicts: set[str] = set()
    for vid, pids in candidates.items():
        skus = {products[pid] for pid in pids}
        if len(skus) > 1:
            conflicts.add(vid)  # 서로 다른 internal_sku 충돌 → SKU 귀속 안 함(잔차로, 사실 표면화)
            continue
        vid_sku[vid] = next(iter(skus))  # 단일 internal_sku(중복 매핑이어도 동일 SKU)
    return vid_sku, conflicts


# ──────────────────────────────────────────────
# SA-5: 대조 순수함수 — allocated + Σ잔차 == authoritative 검증(보존 법칙)
# ──────────────────────────────────────────────
def _reconcile_component(channel: str, component: str, authoritative_total: Decimal,
                         allocated_by_sku: dict[str, Decimal],
                         residuals: dict[str, Decimal]) -> dict:
    """한 채널·컴포넌트의 대조원장 행. 보존 법칙 성립 여부를 diff로 판정(원 단위=0).

    ★raw diff는 저장·표시용으로 그대로 둔다(투명성). 판정(conservation_ok)만 원 단위(0.01)로
    quantize 후 0과 비교한다 — return_deduction 등 나눗셈(단가=매출/수량)이 만드는 순환소수가
    합산 경로(SKU별 분해 vs 계정 전체)에 따라 소수점 20여 자리에서 흔적(예: 3E-21)을 남겨
    실제로는 완전히 균형인 컴포넌트가 conservation_ok=False로 오판되는 것을 막는다. 원 미만
    금액은 KRW에 존재하지 않으므로 이 반올림이 진짜 누락을 가리는 tolerance가 아니다(D5 취지 유지)."""
    allocated = sum(allocated_by_sku.values(), _Z)
    residual = sum(residuals.values(), _Z)
    diff = authoritative_total - (allocated + residual)
    return {
        "channel": channel,
        "component": component,
        "authoritative_total": authoritative_total,
        "allocated_to_sku": allocated,           # SKU 귀속 총합(보존 법칙 항)
        "allocated_by_sku": allocated_by_sku,    # internal_sku별 분해(3b SKU행 소스)
        "residuals": residuals,
        "conservation_diff": diff,        # 정상=0(±0.01 미만 순환소수 잔여 가능). ≠0이면 조인/기준 버그
        "conservation_ok": diff.quantize(_WON) == _Z,
    }


def _dec(v) -> Decimal:
    """None/숫자 → Decimal(0). 집계 폴백."""
    if v is None:
        return _Z
    return v if isinstance(v, Decimal) else Decimal(str(v))


# ──────────────────────────────────────────────
# SA-1/2 (1P): 발주상세 라인 → internal_sku 매출·원가 (rocket _rocket_cost 조인 미러)
# ──────────────────────────────────────────────
def _agg_rocket_by_sku(db: Session, dfrom: date, dto: date,
                       vendor_id: str | None) -> dict:
    """1P 발주상세를 internal_sku로 집계(SA). rocket_intelligence._rocket_cost와 동일 조인·윈도우.

    반환: alloc_rev(sku별 Σline_order_amount)·alloc_cost(sku별 Σqty×cost_price[confirmed+master])·
      unmapped_rev(매핑/마스터 없는 라인 매출)·detail_total(발주상세 라인 합).
    ★원가는 status='confirmed'+master 존재만 가산(엔진과 동일). 매출은 sku+master 있으면 귀속,
      없으면 unmapped_rev(confirmed인데 master 없는 경우도 엔진처럼 미해결로 처리).
    """
    from sqlalchemy import select

    from app.models import CoupangRocketPurchaseOrder
    start, end = _kst_window_utc(dfrom, dto)
    seq_subq = select(CoupangRocketPurchaseOrder.purchase_order_seq).where(
        CoupangRocketPurchaseOrder.po_created_at >= start,
        CoupangRocketPurchaseOrder.po_created_at <= end,
    )
    if vendor_id is not None:
        seq_subq = seq_subq.where(CoupangRocketPurchaseOrder.vendor_id == vendor_id)
    Item = CoupangRocketPurchaseOrderItem
    rows = (
        db.query(Item.order_qty, Item.line_order_amount, RocketProductCostMap.status,
                 RocketProductCostMap.internal_sku, ProductMaster.cost_price)
        .outerjoin(RocketProductCostMap, RocketProductCostMap.product_number == Item.product_number)
        .outerjoin(ProductMaster, ProductMaster.internal_sku == RocketProductCostMap.internal_sku)
        .filter(Item.purchase_order_seq.in_(seq_subq))
        .all()
    )
    alloc_rev: dict[str, Decimal] = {}
    alloc_cost: dict[str, Decimal] = {}
    unmapped_rev = _Z
    detail_total = _Z
    for qty, line_amt, status, sku, cost_price in rows:
        line_amt = _dec(line_amt)
        detail_total += line_amt
        if sku and cost_price is not None:  # 매핑+마스터 존재 → SKU 귀속
            alloc_rev[sku] = alloc_rev.get(sku, _Z) + line_amt
            if status == "confirmed":       # 원가는 confirmed만(엔진과 동일)
                alloc_cost[sku] = alloc_cost.get(sku, _Z) + _dec(cost_price) * int(qty or 0)
        else:
            unmapped_rev += line_amt
    return {"alloc_rev": alloc_rev, "alloc_cost": alloc_cost,
            "unmapped_rev": unmapped_rev, "detail_total": detail_total}


_MARKETPLACE_PLATFORMS = ("naver", "cafe24")

# ── 컴포넌트별 날짜 기준(D-10): 교차검증은 각 소스 엔진의 그 기준·그 창으로 대조한다 ──
# 소스마다 날짜축이 다르므로(3P 주문일 vs 1P 발주일 vs RG 정산 인식기간) 원장에 명시해 오인 방지.
_DATE_BASIS: dict[tuple[str, str], str] = {
    ("coupang_3p", "revenue"): "order_date (주문일, KST)",
    ("coupang_rg", "revenue"): "paid_at (RG 주문 결제일)",
    ("coupang_rg", "settlement_fee"): "recognition period overlap (정산 인식기간 중첩)",
    ("coupang", "commission"): "recognition_date (매출내역 인식일)",
    ("coupang", "ad"): "report_date (광고 리포트일)",
    ("coupang", "cost"): "order_date (매출 순수량 기준)",
    ("coupang", "return_deduction"): "return requested_at (반품 요청일)",
    ("coupang", "net_profit"): "mixed (매출=주문일, RG정산/비-PA=인식일 — net_profit_basis 참조)",
    ("coupang_1p", "revenue"): "po_created_at (발주일, KST)",
    ("coupang_1p", "cost"): "po_created_at (발주일, KST)",
    ("coupang_1p", "ad"): "report_date (Retail 광고 리포트일)",
    ("coupang_1p", "net_profit"): "po_created_at (발주일, KST) + 광고 report_date",
}


def _date_basis(channel: str, component: str) -> str:
    """컴포넌트 날짜 기준. 마켓플레이스(naver/cafe24)는 전부 order_date."""
    if channel in _MARKETPLACE_PLATFORMS:
        return "order_date (주문일)"
    return _DATE_BASIS.get((channel, component), "order_date")


# ──────────────────────────────────────────────
# SA-1/3 (네이버/cafe24): 라인 매출·수수료·원가 → internal_sku (권위 엔진과 동일 헬퍼 D4)
# ──────────────────────────────────────────────
def _agg_marketplace_by_sku(db: Session, dfrom: date, dto: date) -> dict:
    """네이버/cafe24 주문을 platform×internal_sku로 집계(SA). 광고/배송은 계정단위라 제외(원장 잔차).

    ★권위 엔진(profit_calculator.calculate_channel_summary)과 동일한 _line_revenue·_line_commission·
      원가(product_id→ProductMaster.cost_price×qty)·REVENUE_EXCLUDED·consignment 제외를 그대로 써서
      product_revenue/commission/cost가 구성상 일치한다(재구축 아님). product_id→internal_sku로 그룹,
      product_id 없으면 미매핑 잔차(codex #5: product_id는 product_master.id지 internal_sku 아님 — id로 조인).
    반환: {platform: {"alloc": {sku:{revenue,commission,cost}}, "unmapped": {...}, "total": {...}}}
    """
    start = datetime.combine(dfrom, time.min)
    end = datetime.combine(dto, time.max)
    rows = (
        db.query(Order, Channel, ProductMaster)
        .join(Channel, Order.channel_id == Channel.id)
        .outerjoin(ProductMaster, Order.product_id == ProductMaster.id)
        .filter(
            Channel.platform.in_(_MARKETPLACE_PLATFORMS),
            Channel.channel_type != "consignment",
            Order.status.notin_(tuple(REVENUE_EXCLUDED)),
            Order.order_date >= start,
            Order.order_date <= end,
        )
        .all()
    )
    out: dict[str, dict] = {}
    for o, ch, pm in rows:
        plat = out.setdefault(ch.platform, {"alloc": {}, "unmapped": _zero_triplet(),
                                            "total": _zero_triplet(), "cost_unknown": {}})
        product_rev = _line_revenue(ch, o)
        commission = _line_commission(ch, o, product_rev, None)  # 네이버/cafe24=commission_amount(D4)
        cost = (pm.cost_price * o.quantity) if pm is not None else _Z
        plat["total"]["product_revenue"] += product_rev
        plat["total"]["commission"] += commission
        plat["total"]["cost"] += cost
        if pm is not None:  # product_id → internal_sku 귀속
            b = plat["alloc"].setdefault(pm.internal_sku, _zero_triplet())
            b["product_revenue"] += product_rev
            b["commission"] += commission
            b["cost"] += cost
            # ★«원가를 모른다»와 «원가가 0원이다»를 구분한다(2026-08-06, naver_ops와 같은 규율).
            #   마스터는 붙었는데 `cost_price`가 0/NULL/음수면 그 라인의 원가는 **모르는 것**이지
            #   0원이 아니다. `ProductMaster.cost_price`가 `nullable=False, default=0`이라 미입력이
            #   조용히 0원 원가가 되고, 그 SKU의 순익이 원가만큼 과대로 나온다.
            #   ★금액은 한 톨도 바꾸지 않는다 — 여기서 cost를 빼면 `conservation_ok`(Σ귀속+잔차==
            #   총계)와 권위 엔진(profit_calculator) 정합이 동시에 깨진다. 우리가 모른다는 **사실만**
            #   따로 싣고, 화면이 그 SKU의 순익을 「—」로 비운다(합계는 그대로 계산·표시).
            if not pm.cost_price or pm.cost_price <= 0:
                cu = plat["cost_unknown"]
                cu[pm.internal_sku] = cu.get(pm.internal_sku, _Z) + product_rev
        else:               # product_id 없음/미매칭 → 미매핑 잔차
            plat["unmapped"]["product_revenue"] += product_rev
            plat["unmapped"]["commission"] += commission
            plat["unmapped"]["cost"] += cost
    return out


def _zero_triplet() -> dict[str, Decimal]:
    return {"product_revenue": _Z, "commission": _Z, "cost": _Z}


def _partition_by_sku(per_vid: dict[str, Decimal], vid_sku: dict[str, str],
                      ) -> tuple[dict[str, Decimal], Decimal]:
    """vid별 금액을 internal_sku 귀속분과 미매핑 잔차로 분할. 반환=(sku별 합, 미매핑 합)."""
    allocated: dict[str, Decimal] = {}
    unmapped = _Z
    for vid, amount in per_vid.items():
        sku = vid_sku.get(str(vid))
        if sku is None:
            unmapped += amount
        else:
            allocated[sku] = allocated.get(sku, _Z) + amount
    return allocated, unmapped


# ──────────────────────────────────────────────
# Harness 3a: 대조원장
# ──────────────────────────────────────────────
def compute_pnl_reconciliation(db: Session, dfrom: date, dto: date,
                               account: str | None = None) -> dict:
    """통합 손익 대조원장(3a). 채널·컴포넌트마다 보존 법칙을 검증한다.

    account(COUPANG_WING1 등) 주면 해당 계정만(계정 분리). None이면 전체(라우터 T6가 계정 필수화).
    권위 엔진(compute_command_center)을 대조 기준으로 소비하고, 잔차 버킷을 독립 계산해 균형 증명.
    """
    acc = _resolve_account(db, account)
    vid_sku, sku_conflicts = _resolve_vid_sku(db)
    cc = compute_command_center(db, dfrom, dto, account)
    cc_sum = cc["account"]["summary"]

    components: list[dict] = []

    # ── 쿠팡 3P 매출: revenue_3p = Σ _agg_orders(3P만) ──
    orders_3p = _agg_orders(db, dfrom, dto, acc["channel_ids"])
    rev_3p_by_vid = {vid: o["revenue"] for vid, o in orders_3p.items()}
    alloc_rev, unmapped_rev = _partition_by_sku(rev_3p_by_vid, vid_sku)
    components.append(_reconcile_component(
        "coupang_3p", "revenue", cc_sum["revenue_3p"], alloc_rev,
        {"unmapped_coupang": unmapped_rev},
    ))

    # ── 쿠팡 RG 매출: revenue_rg = Σ _agg_rg_orders ──
    rg_orders = _agg_rg_orders(db, dfrom, dto, acc["account_key"])
    rev_rg_by_vid = {vid: o["revenue"] for vid, o in rg_orders.items()}
    alloc_rg, unmapped_rg = _partition_by_sku(rev_rg_by_vid, vid_sku)
    components.append(_reconcile_component(
        "coupang_rg", "revenue", cc_sum["revenue_rg"], alloc_rg,
        {"unmapped_coupang": unmapped_rg},
    ))

    # ── 쿠팡 per-vid 컴포넌트: command_center by_option 행을 소비(권위 per-vid 값) ──
    # by_option[vid]는 3P+RG 병합 매출·3P 실측 수수료·옵션 광고·순수량 원가·반품차감을 담는다.
    # 각 값을 그대로 재귀속하므로 Σ == cc_sum[field]가 자명 성립(권위 값 소비, 재계산 아님).
    cc_by_option = cc["account"]["by_option"]
    for component, field, auth in [
        ("commission", "total_fee", cc_sum["total_fee"]),
        ("ad", "ad_spend", cc_sum["ad_spend"]),
        ("cost", "cost", cc_sum["cost"]),
        ("return_deduction", "return_deduction", cc_sum["return_deduction"]),
    ]:
        by_vid = {row["vendor_item_id"]: row[field] for row in cc_by_option}
        alloc, unmapped = _partition_by_sku(by_vid, vid_sku)
        components.append(_reconcile_component(
            "coupang", component, auth, alloc, {"unmapped_coupang": unmapped},
        ))

    # ── 쿠팡 net_profit: 옵션 순익(by_option) + 계정 단위 조정 버킷 ──
    # 권위 체인(command_center): final net_profit = Σby_option net_profit(=pre_nonpa)
    #   + settlement_revenue_adjustment − ad_nonpa_deducted − rg_settlement_total − seller_shipping_3p.
    # 옵션 순익은 SKU 귀속/미매핑으로 분할, 계정 조정 4종은 옵션 귀속 불가라 1급 잔차 버킷(부호 유지).
    np_by_vid = {row["vendor_item_id"]: row["net_profit"] for row in cc_by_option}
    alloc_np, unmapped_np = _partition_by_sku(np_by_vid, vid_sku)
    components.append(_reconcile_component(
        "coupang", "net_profit", cc_sum["net_profit"], alloc_np,
        {
            "unmapped_coupang": unmapped_np,
            "settlement_revenue_adjustment": cc_sum["settlement_revenue_adjustment"],
            "account_only_ad_nonpa": -cc_sum["ad_nonpa_deducted"],
            "rg_settlement_flip": -cc_sum["rg_settlement_total"],
            "seller_shipping_3p": -cc_sum["seller_shipping_3p"],
            # D-CPP-33에서 새로 계정 단위로 붙은 둘. 원장에 안 실으면 보존식이 깨진다
            # (실제로 test_whole_ledger_conservation이 이 누락을 잡았다).
            "shipping_income_3p": cc_sum["shipping_income_3p"],
            "payable_vat": -cc_sum["payable_vat"],
        },
    ))

    # ── 쿠팡 RG 정산수수료: 옵션수수료 VAT gross-up SKU 귀속 + 명시 잔차 2종 (T4, D-8/D-9) ──
    # 신규 지표: RG 옵션수수료(VAT前 A−B)를 SKU에 귀속(×1.1 gross-up)하되, 계정 RG 플립 총액(VAT後,
    # net_profit 실차감값)과 화해. ★옵션 row 있는 fee_type은 어느 것이든 SKU 귀속(codex T4 P1 —
    # 입출고뿐 아니라 판매수수료·반품·보관도 엑셀 옵션 row가 있으면 귀속됨. rg_settlement_sync 참조).
    # ★잔차를 2종으로 분리(codex T4 P2 — 단일 plug는 ×1.1 오류·과귀속을 숨김):
    #   rg_account_only_fees = 옵션 row 없는 fee_type(예: ad_sales)의 계정 VAT後 총액(정상 귀속불가분).
    #   rg_vat_grossup_gap   = 옵션 row 있는 fee_type의 (계정VAT後 − Σ옵션×1.1). 정상 ≈0(반올림).
    #                          ×1.1이 틀리거나 already-VAT row가 섞이면 여기서 터져 감사 가능.
    # 보존: Σ(옵션귀속)+rg_unmapped+rg_account_only_fees+rg_vat_grossup_gap == rg_total (정확).
    # net_profit 컴포넌트의 rg_settlement_flip(계정단위)은 불변 — 별도 신규 지표(D-8 화해).
    rg_opt = _agg_rg_settlement_fees(db, dfrom, dto, acc["account_key"], grain="option")
    rg_acct = _agg_rg_settlement_fees(db, dfrom, dto, acc["account_key"], grain="account")
    rg_fee_by_vid: dict[str, Decimal] = {}
    opt_ft_sum: dict[str, Decimal] = {}        # fee_type별 Σ옵션(VAT前 A−B)
    for acct_opts in rg_opt.values():          # {account_key: {vid: {fee_type:amt, "total":Σ}}}
        for vid, fees in acct_opts.items():
            rg_fee_by_vid[vid] = rg_fee_by_vid.get(vid, _Z) + fees["total"] * _RG_VAT_GROSSUP
            for ft, amt in fees.items():
                if ft != "total":
                    opt_ft_sum[ft] = opt_ft_sum.get(ft, _Z) + amt
    alloc_rgfee, unmapped_rgfee = _partition_by_sku(rg_fee_by_vid, vid_sku)
    acct_ft_sum: dict[str, Decimal] = {}       # fee_type별 Σ계정(VAT後)
    for acct_fees in rg_acct.values():          # {account_key: {fee_type:amt, "total":Σ}}
        for ft, amt in acct_fees.items():
            if ft != "total":
                acct_ft_sum[ft] = acct_ft_sum.get(ft, _Z) + amt
    rg_account_only = sum((amt for ft, amt in acct_ft_sum.items() if not opt_ft_sum.get(ft)), _Z)
    rg_vat_gap = sum((acct_ft_sum.get(ft, _Z) - opt_ft_sum[ft] * _RG_VAT_GROSSUP
                      for ft in opt_ft_sum), _Z)
    components.append(_reconcile_component(
        "coupang_rg", "settlement_fee", cc_sum["rg_settlement_total"], alloc_rgfee,
        {"rg_unmapped": unmapped_rgfee, "rg_account_only_fees": rg_account_only,
         "rg_vat_grossup_gap": rg_vat_gap},
    ))

    # ── 1P(로켓배송): 별도 엔진(compute_rocket_overview) + RocketProductCostMap 브리지 ──
    # 옵션그레인 by_option이 없어(PO그레인) 별도 채널 블록.
    # ★1P vendor 스코프(codex T3 P1#2): acc["vendor_id"]로 스코프한다. D-2 확정사실 — vendor_id는
    #   회사별로 WING·RG·ROCKET에 공유(오하이테크 A01029796 = WING2/RG2/ROCKET 동일). 따라서
    #   ohitech 계정(WING2/RG2/ROCKET/None)은 acc["vendor_id"]가 곧 1P vendor라 정확 스코프되고,
    #   ofix 계정(A01564720)은 로켓 PO가 없어 0(회귀 가드). 이 등가는 test_1p_included_for_ohitech_account로 고정.
    ro = compute_rocket_overview(db, dfrom, dto, acc["vendor_id"])
    rk = _agg_rocket_by_sku(db, dfrom, dto, acc["vendor_id"])
    pos_without_detail = ro["revenue"] - rk["detail_total"]  # 발주상세 미수집 PO 매출분
    components.append(_reconcile_component(
        "coupang_1p", "revenue", ro["revenue"], rk["alloc_rev"],
        {"unmapped_1p": rk["unmapped_rev"], "pos_without_detail": pos_without_detail},
    ))
    components.append(_reconcile_component(
        "coupang_1p", "cost", ro["cost"], rk["alloc_cost"], {},
    ))
    components.append(_reconcile_component(
        "coupang_1p", "ad", ro["ad_spend"], {},   # 1P 광고=Retail 계정단위(옵션 귀속 불가)
        {"account_only_ad": ro["ad_spend"]},
    ))
    alloc_np_1p = {sku: rev - rk["alloc_cost"].get(sku, _Z)
                   for sku, rev in rk["alloc_rev"].items()}
    components.append(_reconcile_component(
        "coupang_1p", "net_profit", ro["net_profit"], alloc_np_1p,
        {
            "unmapped_1p": rk["unmapped_rev"],
            "pos_without_detail": pos_without_detail,
            "account_only_ad": -ro["ad_spend"],
        },
    ))

    # ── 네이버/cafe24: 라인 헬퍼 재사용, product_id→internal_sku 그룹(광고·배송은 Phase2 잔차) ──
    # 계정 파라미터는 쿠팡 전용이라 마켓플레이스는 account 지정 시 제외(계정 인식 엔진 정합, D-11).
    # ★컴포넌트명은 product_revenue(codex T3 P1#3): 권위 엔진 calculate_channel_summary의 revenue는
    #   product_revenue+shipping_revenue인데 여기 authoritative는 _line_revenue(=product 매출)만이라
    #   'revenue'로 부르면 배송/수동매출과 대조가 어긋난다. 동일 헬퍼·필터라 product_revenue/commission/
    #   cost는 권위 엔진 소계와 구성상 일치. 배송매출·광고·수동매출은 T3 스코프 밖(T5/Phase2, 계획 §5).
    cost_unknown_by_sku: dict[str, Decimal] = {}   # SKU → 원가 미상 라인의 제품매출(표시 전용)
    if account is None:
        mkt = _agg_marketplace_by_sku(db, dfrom, dto)
        for plat, blk in sorted(mkt.items()):
            unmapped_key = f"unmapped_{plat}"
            for comp in ("product_revenue", "commission", "cost"):
                alloc = {sku: v[comp] for sku, v in blk["alloc"].items()}
                components.append(_reconcile_component(
                    plat, comp, blk["total"][comp], alloc,
                    {unmapped_key: blk["unmapped"][comp]},
                ))
            for sku, rev in blk["cost_unknown"].items():
                cost_unknown_by_sku[sku] = cost_unknown_by_sku.get(sku, _Z) + rev

    # 컴포넌트별 날짜기준 명시(D-10) — 교차검증 시 각 소스 기준으로 대조.
    for c in components:
        c["date_basis"] = _date_basis(c["channel"], c["component"])

    warnings = _ledger_warnings(db, dfrom, dto, acc["account_key"])
    conservation_ok = all(c["conservation_ok"] for c in components)
    # Harness 3b: SKU 행은 원장 균형 후에만 노출(불균형이면 SKU 손익 신뢰 불가, D-6). 단 summary의
    # reconciled_net_profit(권위 엔진 총액)은 SKU 귀속과 무관하게 유효하므로 불균형이어도 유지한다
    # (codex T6 P2 — 총액까지 0으로 지우면 대조 대상을 잃고 UI가 '총액 0' 오해). trustworthy로 신뢰도 표기.
    by_sku, summary = _build_sku_rows(components)
    summary["trustworthy"] = conservation_ok
    # ★원가 미상 표면화 — **금액은 이미 다 계산된 뒤**에 표식만 붙인다(합계·보존법칙 불변).
    #   그 SKU의 `net_profit_allocated_only`는 원가가 빠진 값이라 **과대**다. 화면은 이 행의
    #   순익을 「—」로 비우고(naver_ops와 같은 규율: 모르면 값 자체를 비운다), 합계는 그대로 낸다
    #   — 미상은 보통 전체의 몇 %라 그것 때문에 나머지를 가리면 화면이 쓸모없어진다.
    for r in by_sku:
        rev_unknown = cost_unknown_by_sku.get(r["internal_sku"])
        r["cost_known"] = rev_unknown is None
        if rev_unknown is not None:
            r["cost_unknown_revenue"] = rev_unknown
    # 키는 alloc에 들어간 SKU이므로 by_sku에 반드시 나타난다 — 별도 교집합이 필요 없다.
    # (account 지정 시엔 마켓플레이스가 스코프 밖이라 비어 있고, 그때 0은 «없음»이 아니라
    #  «이 계정 조회에선 안 봄»이다 — 화면 문구가 그렇게 말한다.)
    summary["cost_unknown_skus"] = len(cost_unknown_by_sku)
    summary["cost_unknown_revenue"] = sum(cost_unknown_by_sku.values(), _Z)
    summary["cost_unknown_scoped"] = account is None   # false면 미상 판정 자체를 안 한 것
    if not conservation_ok:
        by_sku = []  # SKU 행만 숨김. summary.reconciled_net_profit은 엔진 총액이라 유지.
    period = {"from": dfrom.isoformat(), "to": dto.isoformat()}
    if account is not None:
        period["account"] = account
    return {
        "period": period,
        "ledger": {
            "components": components,
            "conservation_ok": conservation_ok,
            "sku_conflicts": sorted(sku_conflicts),  # 옵션ID→복수 internal_sku(무결성 결손)
            "warnings": warnings,
        },
        "by_sku": by_sku,
        "summary": summary,
    }


# ──────────────────────────────────────────────
# Harness 3b: SKU 행(옵션 손익) — 원장 균형 후에만
# ──────────────────────────────────────────────
# net_profit_allocated_only에 들어가는 순익 기여 컴포넌트(부호). 계정조정(RG플립·비-PA·정산화·
# 판매자배송)은 SKU 귀속 불가라 제외 — account_adjustment_residual로만 표기(codex #14, 안분 금지).
_NET_CONTRIB: dict[tuple[str, str], int] = {
    ("coupang", "net_profit"): 1,
    ("coupang_1p", "net_profit"): 1,
    ("naver", "product_revenue"): 1, ("naver", "commission"): -1, ("naver", "cost"): -1,
    ("cafe24", "product_revenue"): 1, ("cafe24", "commission"): -1, ("cafe24", "cost"): -1,
}


def _build_sku_rows(components: list[dict]) -> tuple[list[dict], dict]:
    """대조원장 컴포넌트를 internal_sku 행으로 피벗(3b).

    각 SKU 행: 채널별 컴포넌트 분해 + net_profit_allocated_only(SKU 귀속 순익, 계정조정 제외).
    summary: reconciled_net_profit(계정 단위 엔진 총액=Σ net_profit 컴포넌트 authoritative) ·
      net_profit_allocated_total(Σ SKU 귀속 순익) · account_adjustment_residual(둘의 차 —
      미매핑+계정조정, 안분하지 않고 참고치로만, codex #14).
    """
    rows: dict[str, dict] = {}
    for c in components:
        ch, comp = c["channel"], c["component"]
        contrib = _NET_CONTRIB.get((ch, comp), 0)
        for sku, amt in c["allocated_by_sku"].items():
            row = rows.setdefault(sku, {"internal_sku": sku, "channels": {},
                                        "net_profit_allocated_only": _Z})
            row["channels"].setdefault(ch, {})[comp] = amt
            if contrib:
                row["net_profit_allocated_only"] += contrib * amt
    by_sku = sorted(rows.values(), key=lambda r: r["net_profit_allocated_only"], reverse=True)

    # 계정 단위 화해: reconciled = Σ net_profit 기여 컴포넌트 authoritative(엔진 총액, 계정조정 포함).
    reconciled = _Z
    for c in components:
        w = _NET_CONTRIB.get((c["channel"], c["component"]), 0)
        if w:
            reconciled += w * c["authoritative_total"]
    allocated_total = sum((r["net_profit_allocated_only"] for r in by_sku), _Z)
    summary = {
        "reconciled_net_profit": reconciled,        # 계정 단위에서만 의미(= 엔진 총액)
        "net_profit_allocated_total": allocated_total,
        "account_adjustment_residual": reconciled - allocated_total,  # 미매핑+계정조정(안분 안 함)
    }
    return by_sku, summary


def _ledger_warnings(db: Session, dfrom: date, dto: date,
                     account_key: str | None) -> list[dict]:
    """원장 경고. partial_period_settlement(D-10/codex #10): RG 정산 주기가 조회 윈도우에
    완전 포함되지 않으면(경계 걸침) 경고. 일할 배분은 하지 않고(임의 배분 금지) 사실만 표면화 —
    부분 주기 정산이 net_profit/settlement_fee를 과대/과소로 보이게 할 수 있음을 알린다."""
    from app.models import CoupangRgSettlementFee
    q = db.query(CoupangRgSettlementFee).filter(
        CoupangRgSettlementFee.recognition_date_from <= dto,
        CoupangRgSettlementFee.recognition_date_to >= dfrom,
        (CoupangRgSettlementFee.recognition_date_from < dfrom)
        | (CoupangRgSettlementFee.recognition_date_to > dto),
    )
    if account_key is not None:
        q = q.filter(CoupangRgSettlementFee.account_key == account_key)
    partials = q.all()
    warnings: list[dict] = []
    if partials:
        periods = sorted({(r.recognition_date_from.isoformat(),
                           r.recognition_date_to.isoformat()) for r in partials})
        warnings.append({
            "type": "partial_period_settlement",
            "detail": (
                "RG 정산 주기가 조회 윈도우 경계에 걸침(부분 포함). 일할 배분 안 함 — "
                "settlement_fee/net_profit이 부분 주기만큼 낙관/비관적일 수 있음(D-10)."
            ),
            "periods": periods,
        })
    return warnings
