# rg_daily_pnl.py — RG(로켓그로스) «상품(옵션) 단위 일별 손익»의 조립 (CONTRACT_2p_own_screens §1-A-4).
#
# ★이 모듈은 «세 번째 손익 엔진»이 아니다(계약 §3 금지선). 새 계산을 하나도 안 만든다 — 전부
#   이미 있는 SA(`rg_net_revenue`·`rg_sales_date_fees`·`rg_channel_pnl`·`intelligence._agg_ads`)의
#   출력을 **옵션 단위로 재배열**만 한다. `rg_channel_pnl.compute_rg_summary_row`가 계정 하나를
#   한 줄로 접는 자리라면, 여기는 그 한 줄을 **상품 행 + 계정 공통 행**으로 편다.
#
# 왜 이 모듈이 필요한가 (Jino 원문, 계약 §0):
#   "어제 어떤 제품이 몇개가 팔리고 그 판매분의 정산공제, 원가, 세금, 기타비용등을 빼고 남는
#   이익이 있잖아. 다른 판매와 같이 2P도 그걸 보자는거지" — `product_pnl`은 **창 단위**만 내고
#   (직전 계약 §8-2 확정), 화면 A는 **일별** 상품 손익이 필요하다. 여기가 그 조립층이다.
#
# ★★핵심 제약 — 보존식(계약 §4 ⓓ):
#   Σ(옵션 행 net_profit, 아래 정의) + Σ(계정 공통 행) == `compute_rg_summary_row(...)`의 net_profit
#   원 단위로. 두 축(요약축 매출 vs 옵션축 매출/원가/광고비)이 다른 테이블이라는 것이 이 설계의
#   전부다 — 어긋난 창의 차액을 옵션 행에 우겨넣거나 조용히 버리면 보존식이 깨진다. 반드시
#   `account_common`의 자백 필드로 흡수한다. 그 유도는 아래 세 문단이 전부다:
#
#   ① 원가 — `net_cost()`가 이미 「원가를 아는 옵션만 원가를 낸다」(모르는 옵션은 0 기여)는
#      규칙으로 **옵션축 자기 안에서 닫혀 있다**. 옵션 행의 cost 합 == 계정 행의 cost. 갭 없음.
#   ② 판매수수료(sale_fee) — 계정 값은 `revenue_total(옵션축 전체) × rate`인데, `by_option`은
#      «단가를 아는 옵션만» 담아 물류비와 함께 skip한다. `by_option_detail`(2026-08-23 additive,
#      `rg_sales_date_fees.py`)이 이 문제를 푼다: 단가 없이도 sale_fee 몫만 채워서, 옵션축의
#      «매출이 있는 모든 vid»에 대해 sale_fee가 빠짐없이 잡힌다 → 옵션 행의 fee_sale_fee 합
#      == 계정 행의 sale_fee. 물류비 쪽은 여전히 단가를 아는 옵션만 채워지므로 그 합도 정확히
#      계정 행의 logistics와 같다. 기간비용(storage·반품)은 애초에 판매일에 안 붙는 개념이라
#      옵션 행에 없다 — 계정 공통 행(`period_fees`)으로만 존재한다(옵션 하나로 못 좁혀진다).
#   ③ 광고비 — `intelligence._agg_ads`가 «판매 없이 광고만 돈» 옵션도 낼 수 있다(옵션축
#      `net_revenue_by_option`엔 매출이 있는 vid만 있다). 그 옵션이 옵션 행 목록에서 빠지면
#      그 광고비만큼 옵션 합계가 계정 값(`split_wing_ad_spend(...)["rg"]`)보다 작아진다.
#      ⇒ 옵션 행 목록은 **(매출 있는 vid) ∪ (RG로 귀속된 광고비가 있는 vid)**의 합집합이다.
#   ④ 매출 — 화면이 싣는 매출은 **요약축**(`net_revenue_by_account`)인데 원가·수수료·광고비는
#      전부 **옵션축**에서 온다. 옵션축이 창을 다 못 덮으면(페처 롤링, 취소·환불 그레인 차이 등)
#      두 축의 매출 합이 갈라진다. 그 차액은 어느 옵션의 것도 아니므로 `revenue_axis_gap`으로
#      계정 공통 행에 둔다(0이 아닐 수 있다 — 숨기지 않는다).
#   ⑤ 수수료 축 자체가 원장(recognition_date)으로 물러선 창(`fee_trustworthy=False`)에서는
#      «그 창에 판 것에 그 창의 수수료가 얼마 붙는지»를 옵션 단위로 잴 근거가 없다(원장은
#      정산 인식일 통짜다). 그 경우 옵션 행엔 fee 분해를 아예 싣지 않고(None — 「이 값이 이
#      셋으로 이뤄졌다」는 거짓말을 안 한다, `rg_channel_pnl`과 같은 규율) 원장 수수료 **전액**을
#      `fee_axis_fallback_gap`으로 계정 공통 행에 둔다.
#
# ★코스트 게이트(원가 커버리지 미달)는 낮추지 않는다 — `cost_trustworthy=False`면 이 창은
#   옵션 행이든 계정 공통 행이든 **어떤 net_profit도 내지 않는다**(전부 None). 대시보드 RG 행이
#   그 창에서 `net_profit=None`을 내는 것과 완전히 같은 규율이다(계약 §3 금지선).
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.coupang.rg_channel_pnl import _COST_COVERAGE_MIN, _cycles_label, compute_rg_summary_row
from app.services.coupang.rg_net_revenue import (
    net_cost,
    net_revenue_by_account,
    net_revenue_by_option,
    option_axis_coverage,
    option_sell_route,
    split_wing_ad_spend,
)
from app.services.coupang.rg_sales_date_fees import FEE_COVERAGE_MIN, sales_date_fees

log = logging.getLogger(__name__)

ZERO = Decimal("0")


def rg_option_pnl(
    db: Session,
    account_key: str,
    date_from: date,
    date_to: date,
    cost_master: dict[str, dict],
    vendor_id: str,
) -> dict:
    """RG «상품(옵션) 단위 일별 손익» — 화면 A의 재료.

    반환:
      options            [{vendor_item_id, name, revenue, units_sold, order_count,
                            fee_logistics, fee_sale_fee, fee_total, cost, has_cost,
                            ad_spend, net_profit}, ...]
      account_common     {period_fees, payable_vat, revenue_axis_gap, ad_unallocated,
                           ad_unallocated_options, fee_axis_fallback_gap,
                           cost_unmapped_revenue, fee_unmapped_revenue}
      commission_axis · rate · rate_basis · rate_cycles · fee_coverage · cost_coverage ·
      option_axis_days · option_axis_complete · cost_trustworthy · fee_trustworthy ·
      reconciliation     — 전부 `rg_channel_pnl.compute_rg_summary_row`와 같은 자백 어휘.
      conservation       {options_net_sum, account_common_sum, computed_total_net,
                           reference_net, diff, ok} — §4 ⓓ 보존식 자기 대조.

    ★옵션 행의 `net_profit`은 **두 게이트를 모두 통과했을 때만** 낸다(`cost_trustworthy` AND
      `fee_trustworthy`). 수수료 축이 원장으로 물러선 창에서는 그 원장 수수료를 어느 옵션의
      몫으로 나눌 근거가 없으므로(정산 인식일 통짜) 옵션 행에 fee/net을 싣지 않는다 — 대신
      `conservation`(내부 자기 대조용)은 그 경우에도 fee=0으로 둔 부분합을 쓰고, 원장 수수료
      전액은 `account_common.fee_axis_fallback_gap`으로 흡수한다. **화면에 보이는 옵션 행의
      net_profit이 None인 것과, 보존식이 내부적으로 그 옵션의 (수수료 제외) 부분 손익을 아는
      것은 다른 것이다** — 전자는 「사용자에게 이 숫자를 진짜 최종 이익이라고 보여주지 않는다」는
      정직성이고, 후자는 「전체 합이 원 단위로 맞는지를 재는 자기 검산」이다. 후자를 못 하면
      보존식 자체가 판정불능이 된다.
    """
    ad_master = intelligence_agg_ads(db, date_from, date_to, vendor_id)
    route = option_sell_route(db, account_key)
    ad_spend_by_option: dict[str, Decimal] = {
        vid: (a.get("spend", ZERO) or ZERO)
        for vid, a in ad_master.items()
        if route.get(vid) == "RG"
    }

    # ── 매출: 두 축 ──
    rev_by_acc = net_revenue_by_account(db, date_from, date_to).get(account_key)
    revenue_summary = rev_by_acc["revenue"] if rev_by_acc else ZERO
    by_option_revenue = net_revenue_by_option(db, date_from, date_to, account_key)
    option_axis_revenue_sum = sum((o["revenue"] for o in by_option_revenue.values()), ZERO)
    revenue_axis_gap = revenue_summary - option_axis_revenue_sum

    # ── 원가 게이트 (rg_channel_pnl과 같은 규칙) ──
    cost_info = net_cost(db, date_from, date_to, account_key, cost_master)
    cov_days = option_axis_coverage(db, date_from, date_to, account_key)
    cost_trustworthy = (
        cost_info["coverage"] is not None
        and cost_info["coverage"] >= _COST_COVERAGE_MIN
        and cov_days["complete"]
    )

    # ── 수수료 축 게이트 (rg_channel_pnl과 같은 규칙, 같은 revenue_reference) ──
    fees = sales_date_fees(db, account_key, date_from, date_to, revenue_reference=revenue_summary)
    fee_trustworthy = (
        fees["rate"] is not None
        and fees["coverage"] is not None
        and fees["coverage"] >= FEE_COVERAGE_MIN
    )
    by_option_detail = fees["by_option_detail"]

    # ── 광고비 (계정 전체 — 옵션 합의 기준값) ──
    ad = split_wing_ad_spend(db, date_from, date_to, account_key, vendor_id)

    # ── 계정 공통 행: 수수료 축 ──
    if fee_trustworthy:
        period_fees = fees["period"]
        fee_axis_fallback_gap = ZERO
        commission_total = fees["total"]
    else:
        from app.services.profit_calculator import get_rg_total_by_account  # noqa: PLC0415

        ledger_commission = get_rg_total_by_account(db, date_from, date_to).get(account_key, ZERO)
        period_fees = ZERO  # 원장 축은 세 항으로 안 갈린다 — 전액이 아래 gap에 있다
        fee_axis_fallback_gap = ledger_commission
        commission_total = ledger_commission

    # ── 옵션 행 조립 ──
    vids = set(by_option_revenue.keys()) | set(ad_spend_by_option.keys())
    options: list[dict] = []
    options_net_sum = ZERO if cost_trustworthy else None
    cost_unmapped_revenue = ZERO
    fee_unmapped_revenue_options = ZERO  # 자백용 — fees["unmapped_revenue"]와 별개 표시

    for vid in sorted(vids):
        rev_info = by_option_revenue.get(vid, {"revenue": ZERO, "qty": 0, "order_count": 0, "name": None})
        revenue_i = rev_info["revenue"]
        qty_i = rev_info["qty"]
        ad_i = ad_spend_by_option.get(vid, ZERO)

        # 원가
        if cost_trustworthy:
            pm = cost_master.get(vid)
            unit_cost = pm.get("cost_price") if pm else None
            if unit_cost is not None and unit_cost > 0:
                cost_i = Decimal(str(unit_cost)) * Decimal(qty_i)
                has_cost = True
            else:
                cost_i = None
                has_cost = False
                if revenue_i != ZERO:
                    cost_unmapped_revenue += revenue_i
        else:
            cost_i, has_cost = None, False

        # 수수료 — 화면 표시용(게이트를 통과했을 때만 분해를 싣는다)
        if fee_trustworthy:
            detail = by_option_detail.get(vid)
            if detail is not None:
                fee_logi, fee_sale, fee_tot = detail["logistics"], detail["sale_fee"], detail["total"]
            elif revenue_i == ZERO and ad_i != ZERO:
                fee_logi, fee_sale, fee_tot = None, None, ZERO
            else:
                fee_logi, fee_sale, fee_tot = None, None, None
                if revenue_i != ZERO:
                    fee_unmapped_revenue_options += revenue_i
        else:
            fee_logi, fee_sale, fee_tot = None, None, None

        # 순이익 — 화면 표시: 두 게이트를 다 통과했을 때만 낸다.
        if cost_trustworthy and fee_trustworthy:
            net_i = revenue_i - (cost_i or ZERO) - (fee_tot or ZERO) - ad_i
        else:
            net_i = None

        # 보존식 내부 부분합 — cost_trustworthy가 통과했으면 fee_trustworthy 여부와 무관하게
        # (fee=0으로 두고) 계속 더한다. 원장 축 전액은 account_common.fee_axis_fallback_gap이
        # 별도로 흡수하므로 여기서 이중으로 빼면 안 된다.
        if cost_trustworthy:
            internal_fee = (fee_tot or ZERO) if fee_trustworthy else ZERO
            options_net_sum += revenue_i - (cost_i or ZERO) - internal_fee - ad_i

        options.append({
            "vendor_item_id": vid,
            "name": rev_info["name"],
            "revenue": str(revenue_i),
            "units_sold": qty_i,
            "order_count": rev_info["order_count"],
            "fee_logistics": None if fee_logi is None else str(fee_logi),
            "fee_sale_fee": None if fee_sale is None else str(fee_sale),
            "fee_total": None if fee_tot is None else str(fee_tot),
            "cost": None if cost_i is None else str(cost_i),
            "has_cost": has_cost,
            "ad_spend": str(ad_i),
            "net_profit": None if net_i is None else str(net_i),
        })

    # ── payable_vat — reference 행과 같은 4항으로 같은 함수를 부른다 ──
    from app.services.profit_calculator import payable_vat  # noqa: PLC0415

    if cost_trustworthy:
        vat = payable_vat(revenue_summary, cost_info["cost"], commission_total, ad["rg"])
    else:
        vat = None

    account_common = {
        "period_fees": str(period_fees),
        "payable_vat": None if vat is None else str(vat),
        "revenue_axis_gap": str(revenue_axis_gap),
        "ad_unallocated": str(ad["unallocated"]),
        "ad_unallocated_options": ad["opt_unknown"],
        "fee_axis_fallback_gap": str(fee_axis_fallback_gap),
        "cost_unmapped_revenue": str(cost_unmapped_revenue),
        "fee_unmapped_revenue": str(fees["unmapped_revenue"]),
    }

    # ── 계정 공통 행의 순이익 기여분 (보존식용) ──
    # revenue_axis_gap은 «화면 매출」에는 있는데 옵션 어디에도 없는 몫이라 그대로 더한다.
    # period_fees·fee_axis_fallback_gap·payable_vat는 비용이라 뺀다. ad_unallocated은
    # **대시보드 RG 행에 안 실리는 돈**이라(rg_channel_pnl 주석) 보존식에서 제외한다 — 표시만.
    if cost_trustworthy:
        account_common_sum = revenue_axis_gap - period_fees - (vat or ZERO) - fee_axis_fallback_gap
        computed_total_net = options_net_sum + account_common_sum
    else:
        account_common_sum = None
        computed_total_net = None

    # ── 정본 대조 — 반드시 실제 호출로 받는다(자체 재계산 금지, §4 ⓓ) ──
    reference_row = compute_rg_summary_row(db, account_key, date_from, date_to, cost_master, vendor_id, ad=ad)
    reference_net = None if reference_row is None else reference_row.get("net_profit")
    reference_net_dec = None if reference_net is None else Decimal(reference_net)

    diff, ok = _conservation_diff(computed_total_net, reference_net_dec)

    conservation = {
        "options_net_sum": None if options_net_sum is None else str(options_net_sum),
        "account_common_sum": None if account_common_sum is None else str(account_common_sum),
        "computed_total_net": None if computed_total_net is None else str(computed_total_net),
        "reference_net": None if reference_net_dec is None else str(reference_net_dec),
        "diff": None if diff is None else str(diff),
        "ok": ok,
    }

    return {
        "options": options,
        "account_common": account_common,
        "commission_axis": "sales_date" if fee_trustworthy else "recognition_date",
        "rate": None if fees["rate"] is None else str(fees["rate"]),
        "rate_basis": fees["rate_basis"],
        "rate_cycles": _cycles_label(fees["rate_cycles"]),
        "fee_coverage": None if fees["coverage"] is None else str(fees["coverage"]),
        "cost_coverage": None if cost_info["coverage"] is None else str(cost_info["coverage"]),
        "option_axis_days": f"{cov_days['days_covered']}/{cov_days['days_total']}",
        "option_axis_complete": cov_days["complete"],
        "cost_trustworthy": cost_trustworthy,
        "fee_trustworthy": fee_trustworthy,
        "reconciliation": fees["reconciliation"],
        "conservation": conservation,
    }


def _conservation_diff(
    computed_total_net: Decimal | None, reference_net: Decimal | None
) -> tuple[Decimal | None, bool | None]:
    """보존식 diff·ok — 순수 함수로 분리했다(직접 단위 테스트 가능하게).

    ★왜 분리했나: 정직한 조립 코드는 «항상» `computed_total_net == reference_net`을 낸다(그게
    이 모듈의 존재 이유다) — 그래서 조립 전체를 통해서만 이 함수를 테스트하면 「diff를 항상 0으로
    하드코딩한」 변이와 「진짜로 뺄셈한」 정상 코드가 **어느 정상 입력에서도 구별되지 않는다**
    (둘 다 0을 낸다). 이 함수를 분리해 두면 **일부러 어긋난 두 값**을 직접 넣어 「진짜 뺄셈인가」를
    독립적으로 검사할 수 있다(`test_rg_daily_pnl.py`의 `test_conservation_diff_helper_*`).
    """
    if computed_total_net is None or reference_net is None:
        return None, None
    diff = computed_total_net - reference_net
    return diff, diff == ZERO


def intelligence_agg_ads(db: Session, date_from: date, date_to: date, vendor_id: str) -> dict[str, dict]:
    """`intelligence._agg_ads`를 지연 임포트로 감싼 얇은 래퍼 — 순환 참조 방지.

    `rg_net_revenue.split_wing_ad_spend`가 이미 같은 지연 임포트를 쓰고 있다(이 모듈이 그
    관행을 그대로 따른다). 새 계산이 아니라 기존 함수 호출일 뿐이다.
    """
    from app.services.coupang.intelligence import _agg_ads  # noqa: PLC0415

    return _agg_ads(db, date_from, date_to, vendor_id)
