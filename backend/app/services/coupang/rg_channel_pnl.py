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
#     정산 수수료                                                   ← `profit_calculator.get_rg_total_by_account`
#   여기서 새로 만드는 것은 **행의 모양**뿐이다.
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
) -> dict | None:
    """RG 채널 요약 행 — `calculate_channel_summary` 출력과 **같은 모양**.

    매출 = 콘솔 net 요약축(RFM). gross 주문 원장이 아니다 — 그쪽은 취소·반품이 안 빠져
      오픽스 30일 기준 +11.8% 과대다(ref 89).
    원가 = 옵션축 net 수량 × 옵션 원가. 커버리지가 임계 미만이면 **순이익을 내지 않는다**
      (원가를 모르는 매출로 이익률을 내면 뜻 없는 비율이 나온다 — D-22와 같은 판단).
    수수료 = 정산 실측 `rg_total − ad_sales`(D-CPP-43). 금액표 되계산이 아니다.
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

    ad = split_wing_ad_spend(db, date_from, date_to, account_key, vendor_id)
    ad_spend = ad["rg"]

    if revenue == ZERO and ad_spend == ZERO:
        return None

    # 수수료 — 지연 임포트(순환 참조 방지: profit_calculator는 이 모듈을 모른다).
    from app.services.profit_calculator import (  # noqa: PLC0415
        get_rg_total_by_account,
        payable_vat,
    )

    commission = get_rg_total_by_account(db, date_from, date_to).get(account_key, ZERO)

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
        #   여기서 하한을 «만들지 않고» 집계층에 맡기는 것이 원칙이지만(net_contribution),
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
        "order_count": units,
        # ── 이 행이 «자기 신뢰도»를 스스로 말하는 칸 (로켓1P의 revenue_basis/cost_coverage와 같은 계열) ──
        "revenue_basis": "console_net",
        "cost_coverage": None if coverage is None else str(coverage.quantize(Decimal("0.0001"))),
        "option_axis_days": f"{cov_days['days_covered']}/{cov_days['days_total']}",
        # ★카탈로그에 없는 옵션에 쓰인 광고비 — **이 행에 안 실린 돈**이다(귀속 불가).
        #   그만큼 회사 광고비가 어느 채널 행에도 안 잡히므로 화면이 실토해야 한다.
        #   ★prod 실측(2026-08-22, WING1 08-05~08-20): **26옵션 · 57,787원 = 그 창 광고비의 4.2%.**
        #     0이 아니다 — 초판 주석은 「0이 정상」이라 썼는데 실측이 반증했다. 대부분이 상위 2개에
        #     몰려 있고(Z폴드8 필름 `95854992864` 52,395원 + `95854992863` 5,181원) 나머지 24개는
        #     0~101원이다. 신상품이 카탈로그 동기화보다 광고에 먼저 뜨는 것으로 보인다.
        #   ⇒ 이 값은 «있으면 이상»이 아니라 «상품 동기화가 광고를 못 따라간 양»으로 읽는다.
        "ad_unallocated": str(ad["unallocated"]),
        "ad_unallocated_options": ad["opt_unknown"],
    }
