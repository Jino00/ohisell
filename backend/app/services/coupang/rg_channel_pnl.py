# rg_channel_pnl.py — 로켓그로스(RG) 채널을 대시보드 요약에 올린다 (D-CPP-47).
#
# 왜 별도 모듈인가: `rg_net_revenue`는 **데이터 접근층**(net 매출·원가·커버리지·광고 분해)이고,
#   여기는 그것들을 «화면 행 한 줄»로 조립하는 층이다. 같은 분업을 로켓1P가 이미 쓰고 있다
#   (`rocket_1p_channel_pnl`). 층을 섞으면 조회 함수가 화면 사정을 알게 되고, 그때부터
#   같은 조회를 쓰는 다른 화면이 이 행의 규칙에 끌려간다.
#
# ★이 모듈은 «세 번째 손익 엔진»이 아니다(금지선). 공식·부품을 전부 기존 것에서 가져온다:
#     순이익 = 매출 − 원가 − 수수료 − 광고비 − payable_vat(...)   ← `calculate_channel_summary`와 동일
#     하한 판정(net=None인데 광고비가 있으면 −광고비)               ← 집계층 `net_contribution`(D-22)
#     원가 다리                                                     ← `intelligence._cost_master`
#     정산 수수료                                                   ← `rg_sales_date_fees.sales_date_fees`
#   여기서 새로 만드는 것은 **행의 모양**뿐이다.
#
# ★2026-08-22 계약 CONTRACT_rg_sales_date_axis: 정산 수수료의 출처를
#   `profit_calculator.get_rg_total_by_account`(정산 인식일 축, 주간 통짜)에서
#   `rg_sales_date_fees.sales_date_fees`(판매일 축)로 **교체**했다. 종전엔 한 주기를 덮는
#   어느 하루를 물어도 같은 값이 나와서(08-17~21 다섯 날 전부 153,058원) 그날 매출의 81.8%가
#   빠졌고 순이익 부호가 뒤집혔다. 그 함수는 폐기가 아니라 **원장 권위값**으로 남아 있고,
#   보존식 대조(`sales_date_fees(...)["reconciliation"]`)가 그것과 계속 맞춰 본다.
from __future__ import annotations

import logging
import os
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.coupang.rg_net_revenue import (
    net_cost,
    net_revenue_by_account,
    option_axis_coverage,
    rg_channel_for_account,
    split_wing_ad_spend,
)
from app.services.coupang.rg_sales_date_fees import FEE_COVERAGE_MIN, sales_date_fees

log = logging.getLogger(__name__)

ZERO = Decimal("0")

#: 원가 커버리지 하한 — 이 아래면 순이익을 «내지 않는다»(None + 광고비 하한).
#: 로켓1P(`ROCKET_1P_COST_COVERAGE_MIN`, 기본 0.95)와 **같은 계열·같은 기본값**으로 둔다.
#: ref 89 실측 커버리지는 99.44%라 통상은 넉넉히 넘는다 — 이 게이트는 옵션축 백필이 안 닿은
#: 구간이 창에 섞여 커버리지가 무너질 때를 위한 것이다.
_COST_COVERAGE_MIN = Decimal(os.getenv("RG_COST_COVERAGE_MIN") or "0.95")


def compute_rg_summary_row(
    db: Session,
    account_key: str,
    date_from: date,
    date_to: date,
    cost_master: dict[str, dict],
    vendor_id: str,
    ad: dict | None = None,
) -> dict | None:
    """RG 채널 요약 행 — `calculate_channel_summary` 출력과 **같은 모양**.

    매출 = 콘솔 net 요약축(RFM). gross 주문 원장이 아니다 — 그쪽은 취소·반품이 안 빠져
      오픽스 30일 기준 +11.8% 과대다(ref 89).
    원가 = 옵션축 net 수량 × 옵션 원가. 커버리지가 임계 미만이면 **순이익을 내지 않는다**
      (원가를 모르는 매출로 이익률을 내면 뜻 없는 비율이 나온다 — D-22와 같은 판단).
    수수료 = **판매일 축**(`rg_sales_date_fees`). 그날 판 수량×단가 + 그날 매출×실측 요율
      + 계정 기간비용(보관비·반품비) 일할. 요율을 못 재거나 단가 커버리지가 얇으면
      **원장 축(정산 인식일)으로 물러서고 행이 그 사실을 말한다**(`commission_axis`).
    광고비 = 실판매 경로로 «측정 귀속»된 몫만(라벨 아님). 미배분은 이 행에 안 실린다.

    매출·광고비가 둘 다 0이면 None — 빈 행을 화면에 만들지 않는다(로켓1P와 같은 규율).
    """
    ch = rg_channel_for_account(db, account_key)
    if ch is None:
        log.warning("account %s에 대응하는 RG 채널이 없다 — RG 행을 만들지 않는다", account_key)
        return None

    rev_by_acc = net_revenue_by_account(db, date_from, date_to).get(account_key)
    revenue = rev_by_acc["revenue"] if rev_by_acc else ZERO
    units = rev_by_acc["units"] if rev_by_acc else 0

    # ★호출부가 이미 구했으면 주입받는다 — `option_sell_route`가 쿼리 5종을 도는데, 호출부가
    #   3P 행 갱신용으로 한 번 부르고 여기서 또 부르면 계정마다 그게 2배가 된다(적대 리뷰 P2).
    if ad is None:
        ad = split_wing_ad_spend(db, date_from, date_to, account_key, vendor_id)
    ad_spend = ad["rg"]

    if revenue == ZERO and ad_spend == ZERO:
        return None

    # 수수료 — 지연 임포트(순환 참조 방지: profit_calculator는 이 모듈을 모른다).
    from app.services.profit_calculator import (  # noqa: PLC0415
        get_rg_total_by_account,
        payable_vat,
    )

    # ★정산공제는 «그 창에 판 것»에 붙는다(계약 CONTRACT_rg_sales_date_axis).
    #   종전 `get_rg_total_by_account`는 정산 인식일 창 겹침이라 한 주기를 덮는 어느 하루를
    #   물어도 같은 값을 줬다 — 그 함수는 폐기가 아니라 **원장 권위값**으로 남아 있고
    #   아래 `reconciliation`이 그것과 계속 맞춰 본다(§4 ⓒ).
    fees = sales_date_fees(db, account_key, date_from, date_to)
    fee_coverage = fees["coverage"]

    cost_info = net_cost(db, date_from, date_to, account_key, cost_master)
    coverage = cost_info["coverage"]
    cov_days = option_axis_coverage(db, date_from, date_to, account_key)

    # ★순이익을 낼 수 있는가 — 두 조건을 **둘 다** 본다.
    #   ① 원가 커버리지(매출 기준)가 임계 이상인가
    #   ② 옵션축이 창 «전체»를 덮는가
    #   ②가 따로 필요한 이유: 커버리지는 «옵션축이 있는 날들 안에서»의 비율이라, 창의 절반이
    #   통째로 비어 있어도 100%가 나온다. 그 경우 원가는 절반만 세고 매출은 요약축에서 전부
    #   세므로 **순이익이 위로 부푼다** — 조용히 틀리는 가장 나쁜 모양이다.
    cost_trustworthy = (
        coverage is not None
        and coverage >= _COST_COVERAGE_MIN
        and cov_days["complete"]
    )

    # ★판매일 축을 «낼 수 있는가»(계약 §4 ⓓⓔ).
    #   ① 판매수수료 «요율»을 못 재면(완결 주기가 없거나 그 주기 매출이 0) 그 항이 통째로 빠진다.
    #      3P처럼 기본 요율로 폴백하지 않는 이유는 RG엔 그런 근거값이 없기 때문이다(계약 §8-4).
    #   ② 물류비 «단가»를 아는 매출 비율이 임계 미만이면 물류비가 과소다 — 순이익이 위로 부푼다.
    #   ⇒ 둘 중 하나라도 안 되면 **판매일 축을 내지 않고 원장 축으로 물러선다.**
    #
    # ★왜 「순이익을 안 낸다」가 아니라 「축을 물린다」인가 (2026-08-22 라이브 실측이 정했다):
    #   `billed_quantity`는 07-27 이후 주기에만 채워져 있다. 그래서 **08-21 하루는 커버리지
    #   100%인데 30일 창은 91.9%**다(단가 미상 매출 420,250원). 앞의 규칙대로면 대시보드
    #   기본 창에서 RG 순이익이 통째로 「—」가 되는데, 그건 종전보다 **덜 아는 화면**이다 —
    #   원장 축은 «모름»이 아니라 창이 넓을수록 정확해지는 다른 축의 실측값이기 때문이다.
    #   대신 **어느 축인지 행이 말한다**(`commission_axis` → 화면에 ⚠️「정산 인식일 축」).
    #   ★종합조망(`intelligence`)도 **같은 규칙**이다 — 규칙이 갈리면 두 화면이 같은 계정을
    #     다른 금액으로 뺀다(D-CPP-47이 고쳤던 바로 그 병).
    #   ★이 결손은 과도기적이다: 옛 주기가 창에서 빠져나가면 커버리지가 저절로 100%로 간다.
    fee_trustworthy = (
        fees["rate"] is not None
        and fee_coverage is not None
        and fee_coverage >= FEE_COVERAGE_MIN
    )
    commission = (
        fees["total"]
        if fee_trustworthy
        else get_rg_total_by_account(db, date_from, date_to).get(account_key, ZERO)
    )

    if cost_trustworthy:
        cost = cost_info["cost"]
        net = revenue - cost - commission - ad_spend - payable_vat(
            revenue, cost, commission, ad_spend
        )
        scope, net_basis = "full", revenue
        rate = (
            str((net / revenue * Decimal("100")).quantize(Decimal("0.01")))
            if revenue > 0
            else None
        )
    else:
        # 원가를 못 믿는다 → 순이익 없음. 단 **광고비는 확정 비용**이라 하한을 낸다(D-22).
        #   여기서 하한을 «만들지 않고» 집계층에 맡기는 것이 원칙이지만(`net_contribution`),
        #   `cost`를 0으로 실어 보내면 화면이 「원가 0」으로 읽으므로 0이 아니라 미상을 싣는다.
        cost = ZERO
        net = None
        scope, net_basis, rate = None, ZERO, None

    return {
        "channel_id": ch.id,
        "channel_name": ch.name or "",
        "revenue": str(revenue),
        # RG 매출은 콘솔 GMV(소비자 결제액)라 배송비 수취분이 따로 없다 — 전액 상품매출.
        "product_revenue": str(revenue),
        "shipping_revenue": "0",
        "cost": str(cost),
        "commission": str(commission),
        "ad_spend": str(ad_spend),
        "shipping": "0",   # 판매자 배송비 없음(쿠팡 물류) — 그 비용은 정산 풀필먼트에 들어 있다
        "fixed_cost": "0",
        "net_profit": None if net is None else str(net),
        "net_scope": scope,
        "net_basis_revenue": str(net_basis),
        "unmapped_revenue": str(cost_info["unmapped_revenue"]),
        "profit_rate": rate,
        # ★「주문 건수」다 — **판매수량이 아니다**(적대 리뷰 1R P1-1).
        #   초판은 여기 요약축 `units_sold`를 넣었는데, 이 칸을 소비하는 두 집계층이
        #   (`_kpi_totals`·`group_summary_by_company`) 전 채널 값을 그냥 더하므로 「주문 건수」
        #   카드와 회사 소계가 부풀었다. 로켓1P가 **정확히 같은 이유로** `_kpi_totals`에서
        #   제외돼 있는데(그 칸이 판매수량이라서), RG를 세 번째 예외로 추가하는 대신
        #   **칸의 뜻을 지키는 쪽**으로 고쳤다 — 옵션축이 실제 net 주문 수를 준다.
        #
        # ★★그런데 그 축은 **창을 다 안 덮을 수 있다**(2R NEW P1). `net_profit`·`cost`는
        #   `cost_trustworthy` 게이트를 통과해야 값을 내는데 이 칸만 게이트를 안 거쳐서,
        #   덮은 날짜만 부분 합산한 값이 **완전한 숫자처럼** 나갔다. 옵션축이 아예 없으면
        #   정확히 0이 되어 「미상」이 「주문 0건」으로 읽힌다 — WING2는 옵션축이 07-27부터라
        #   기본 30일 창에서 **상시 재현**되는 조건이었다.
        #   ⇒ 같은 게이트를 씌운다. 부분 커버리지면 0을 낸다.
        #
        # ⚠️ 남는 한계(의도적, 자백): 0으로 내면 그 창의 RG 주문이 「주문 건수」 합계에서
        #   **빠진다**(과소). 종전 동작은 판매수량이 섞여 **과대**였다. 둘 다 완벽하지 않지만
        #   과소가 안전한 방향이고 — 무엇보다 이 행은 `option_axis_days`("16/16")를 같이 실어
        #   보내므로 **읽는 쪽이 부분치임을 알 수 있다.** 「모르는데 아는 척」만은 피한다.
        #   완전한 해법은 `order_count`를 Optional로 바꿔 집계에서 «제외»하는 것인데, 그건
        #   스키마·두 집계층·프론트를 함께 건드려야 해서 이번 범위 밖이다(트랙 「안함」 참조).
        "order_count": cost_info["net_orders"] if cost_trustworthy else 0,
        # 판매수량은 뜻이 다른 별도 칸으로 낸다(요약축 기준 — 창 전체를 덮는다).
        "units_sold": units,
        # ── 이 행이 «자기 신뢰도»를 스스로 말하는 칸 (로켓1P의 revenue_basis/cost_coverage와 같은 계열) ──
        "revenue_basis": "console_net",
        "cost_coverage": None if coverage is None else str(coverage.quantize(Decimal("0.0001"))),
        "option_axis_days": f"{cov_days['days_covered']}/{cov_days['days_total']}",
        # ★카탈로그에 없는 옵션에 쓰인 광고비 — **이 행에 안 실린 돈**이다(귀속 불가).
        #   그만큼 회사 광고비가 어느 채널 행에도 안 잡히므로 화면이 실토해야 한다.
        #   ★prod 라이브 실측(2026-08-22 15:1x, WING1 08-05~08-20): **0원**(옵션 14개는 전부 지출 0).
        #     즉 그 창의 광고비는 **한 푼도 빠짐없이** 3P/RG로 귀속된다.
        #   ⚠️이 주석은 두 번 틀렸다가 라이브가 고쳤다:
        #     ①초판 「0이 정상(모든 상품은 카탈로그에 있으므로)」 — 카탈로그만 보면 틀리다.
        #     ②2판 「26옵션 57,787원(4.2%)」 — 그건 내가 **카탈로그만으로 3P를 판정한 임시 쿼리**의
        #       산물이고, 실제 코드는 `카탈로그 ∪ orders ∪ 옵션축 NORMAL`을 쓴다. 그 57,787원은
        #       미배분이 아니라 **3P였다**(Z폴드8 `95854992864`: 카탈로그 0행인데 orders 8건·
        #       옵션축 NORMAL 11행). 배포된 코드가 내 임시 쿼리보다 정확했다.
        #   ⇒ 「카탈로그가 우주」라는 말은 **부정확하다**. 실측: WING1 카탈로그 413행인데 실제 3P
        #     판매 옵션 88개 중 **67개(76%)가 카탈로그에 없다**(최종 동기화 08-20 20:35이라
        #     시간 문제도 아니다). 카탈로그는 «참고»이고 **판매 원장이 실질 우주**다.
        #   ⇒ 이 값이 0이 아니면 = 카탈로그에도 판매 원장에도 없는 옵션에 돈이 쓰였다는 뜻이다.
        "ad_unallocated": str(ad["unallocated"]),
        "ad_unallocated_options": ad["opt_unknown"],
        # ── 정산공제가 «어느 축이고 무엇을 근거로 하는가» (계약 §4 ⓒⓓⓔ) ──
        # ★이 칸들이 없으면 실측 요율과 「못 잼」이 화면에서 **같은 얼굴**을 한다.
        #   3P가 이미 `basis="default_rate"`로 같은 일을 한다(`option_fee_rate.py:97`).
        "commission_axis": "sales_date" if fee_trustworthy else "recognition_date",
        "commission_basis": fees["rate_basis"],
        "commission_rate": None if fees["rate"] is None else str(
            (fees["rate"] * Decimal("100")).quantize(Decimal("0.0001"))
        ),
        "commission_rate_cycles": _cycles_label(fees["rate_cycles"]),
        # 세 항의 축이 서로 다르다 — 물류비=수량×단가, 수수료=매출×요율, 기간비용=일할(판매일 아님)
        # ★원장 축으로 물러선 창에선 **분해를 싣지 않는다**(None) — 합이 `commission`과 안 맞는
        #   분해는 화면에서 「이 값이 저 셋으로 이뤄졌다」는 거짓말이 된다.
        "commission_logistics": str(fees["logistics"]) if fee_trustworthy else None,
        "commission_sale_fee": str(fees["sale_fee"]) if fee_trustworthy else None,
        "commission_period": str(fees["period"]) if fee_trustworthy else None,
        # 단가를 아는 매출의 비율 · 모르는 몫(0으로 안 채운 자백)
        "fee_coverage": None if fee_coverage is None else str(
            fee_coverage.quantize(Decimal("0.0001"))
        ),
        "fee_unmapped_revenue": str(fees["unmapped_revenue"]),
        # ★장부 총액 보존 — 최근 완결 주기에서 이 방식의 합 vs 원장 실청구액.
        #   **차이를 숨겨 0으로 만들지 않는다**(계약 §4 ⓒ 원문).
        **_reconcile_fields(fees["reconciliation"]),
    }


def _cycles_label(cycles: list) -> str | None:
    """요율을 잰 완결 주기 범위 — "2026-08-03~2026-08-16" 꼴. 없으면 None."""
    if not cycles:
        return None
    starts = [c[0] for c in cycles]
    ends = [c[1] for c in cycles]
    return f"{min(starts)}~{max(ends)}"


def _reconcile_fields(rec: dict | None) -> dict:
    """보존식 대조를 «평평한 문자열 칸»으로 — 화면 스키마(`GroupedSummaryRow`)가 평면이다.

    못 쟀으면(완결 주기가 아예 없으면) 전부 None이다 — 0이 아니다. 0은 「맞았다」로 읽힌다.
    """
    if rec is None:
        return {
            "settlement_reconcile_cycle": None,
            "settlement_reconcile_computed": None,
            "settlement_reconcile_actual": None,
            "settlement_reconcile_diff": None,
            "settlement_reconcile_pct": None,
        }
    return {
        "settlement_reconcile_cycle": f"{rec['cycle_from']}~{rec['cycle_to']}",
        "settlement_reconcile_computed": str(rec["computed"]),
        "settlement_reconcile_actual": str(rec["actual"]),
        "settlement_reconcile_diff": str(rec["diff"]),
        "settlement_reconcile_pct": None if rec["diff_pct"] is None else str(
            rec["diff_pct"].quantize(Decimal("0.01"))
        ),
    }
