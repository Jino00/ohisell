# probe_signal.py — probe_signal SA (단일 책임: 탐침 성과 판정용 선행지표 score 산출)
"""탐침 되돌림 판정(CD3)에 쓸 선행지표 score + 보정 ROAS를 naver_ad_daily 창 집계로 산출.

D-NAO-58 CD3 / D-58-9: 탐침(한 등 상향)이 성적을 냈는지 판정하려면 즉시구매만으로는
부족하다 — 장바구니(관심 신호)는 지연 구매로 이어지므로, 상품별 장바구니→구매 전환율
(cart_conversion_rate SA 산출)로 가중한 선행지표 adjusted_score = 즉시구매 + 장바구니×전환율을
쓴다(D-58-1 정의). roas_corrected는 diary_outcome._window_metrics와 동일한 보정 ROAS 정의
((conv매출/cost)×보정계수, cost=0이면 None).

순수 계산 SA(쓰기 없음·실 API 없음). grain 필터는 auto_operator._settlement_agg /
diary_outcome._window_agg의 상세행 필터를 그대로 따른다(BACKFILL 센티넬 롤업 행 제외 →
상세행과 이중계상 방지, keyword grain은 WEB_SITE 한정).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP


def probe_signal_score(
    db: Session,
    *,
    grain: str,
    target_id: str,
    campaign_id: str,
    date_from: date,
    date_to: date,
    cart_rate: float,
    correction_factor_value: float,
) -> dict:
    """탐침 대상의 [date_from, date_to] 창 선행지표 score + 보정 ROAS.

    grain ∈ keyword/adgroup/campaign. keyword→keyword_id+campaign_type=WEB_SITE,
    adgroup→adgroup_id, campaign→campaign_id로 필터(auto_operator._settlement_agg 규약).
    반환: {immediate, carts, cart_rate, adjusted_score, cost, clk, conv_amt, roas_corrected}.
      immediate = Σ(conv_direct_cnt+conv_indirect_cnt)  # 즉시구매
      carts     = Σ(cart_direct_cnt+cart_indirect_cnt)  # 장바구니(관심)
      adjusted_score = immediate + carts × cart_rate    # D-58-1 선행지표
      roas_corrected = round((conv_amt/cost)×보정계수, 4) if cost>0 else None
    """
    q = db.query(
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_cnt), 0)
        + sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_cnt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cart_direct_cnt), 0)
        + sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cart_indirect_cnt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0)
        + sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
    ).filter(
        NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    )
    if grain == "keyword":
        q = q.filter(NaverAdDaily.keyword_id == target_id, NaverAdDaily.campaign_type == "WEB_SITE")
    elif grain == "adgroup":
        q = q.filter(NaverAdDaily.adgroup_id == target_id)
    else:  # campaign
        q = q.filter(NaverAdDaily.campaign_id == campaign_id)

    immediate, carts, cost, clk, conv_amt = q.one()
    immediate, carts, cost, clk, conv_amt = (
        int(immediate), int(carts), int(cost), int(clk), int(conv_amt)
    )
    adjusted_score = immediate + carts * cart_rate
    roas_corrected = round((conv_amt / cost) * correction_factor_value, 4) if cost > 0 else None
    return {
        "immediate": immediate,
        "carts": carts,
        "cart_rate": cart_rate,
        "adjusted_score": adjusted_score,
        "cost": cost,
        "clk": clk,
        "conv_amt": conv_amt,
        "roas_corrected": roas_corrected,
    }
