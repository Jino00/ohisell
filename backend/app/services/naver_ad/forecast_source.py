# forecast_source.py — forecast_source SA (예측·전문가 스프린트 F2a, D-NAO-26)
# 역할(SA 단일 책임): grain(campaign/adgroup/keyword)별 일별 clk/cost/conv_amt 시계열을 단일
#   창구로 소싱한다. campaign_backfill sentinel 행(campaign)과 P0 실단위 행(adgroup/keyword)이
#   서로 다른 데이터 출처라, forecast_gate/model_builder/scorer가 각자 재구현하지 않도록
#   원칙18-6(SA간 중복 라우팅 금지)에 따라 이 SA 하나로 집중한다.
#   account grain은 F2 스코프 밖(HANDOFF 확정, 신규 소스 미구현) — 지원 시도 시 ValueError.
from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session

from app.models import NaverAdDaily
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

SUPPORTED_GRAINS = ("campaign", "adgroup", "keyword")


def daily_series(db: Session, *, grain: str, scope_key: str, date_from: date, date_to: date) -> dict[date, dict]:
    """grain별 {date: {clk,cost,conv_amt}} 시계열.

    campaign: campaign_backfill sentinel 행(그 캠페인 전체 집계의 유일한 소스, 정직 경계).
    adgroup: 해당 adgroup_id의 P0 실단위 행(키워드별로 흩어진 행)을 날짜별로 합산.
    keyword: 해당 keyword_id의 P0 실단위 행 — WEB_SITE만 keyword_id가 실제값이라 자연히
      한정된다(SHOPPING/BRAND_SEARCH는 keyword_id='' sentinel, models.py NaverAdDaily 참조).
    """
    if grain == "campaign":
        rows = db.query(NaverAdDaily).filter(
            NaverAdDaily.campaign_id == scope_key,
            NaverAdDaily.adgroup_id == BACKFILL_SENTINEL_ADGROUP,
            NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
        ).all()
        return {
            r.ad_date: {"clk": r.clk, "cost": r.cost, "conv_amt": r.conv_direct_amt + r.conv_indirect_amt}
            for r in rows
        }

    if grain == "adgroup":
        rows = db.query(NaverAdDaily).filter(
            NaverAdDaily.adgroup_id == scope_key,
            NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
        ).all()
        series: dict[date, dict] = defaultdict(lambda: {"clk": 0, "cost": 0, "conv_amt": 0})
        for r in rows:
            entry = series[r.ad_date]
            entry["clk"] += r.clk
            entry["cost"] += r.cost
            entry["conv_amt"] += r.conv_direct_amt + r.conv_indirect_amt
        return dict(series)

    if grain == "keyword":
        rows = db.query(NaverAdDaily).filter(
            NaverAdDaily.keyword_id == scope_key,
            NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
        ).all()
        return {
            r.ad_date: {"clk": r.clk, "cost": r.cost, "conv_amt": r.conv_direct_amt + r.conv_indirect_amt}
            for r in rows
        }

    raise ValueError(f"F2는 campaign/adgroup/keyword grain만 지원: {grain}")


def active_days(db: Session, *, grain: str, scope_key: str, date_from: date, date_to: date) -> int:
    """cost>0인 날짜 수 — daily_series 재사용(원칙18-6, 소싱 로직 이중화 금지)."""
    series = daily_series(db, grain=grain, scope_key=scope_key, date_from=date_from, date_to=date_to)
    return sum(1 for v in series.values() if v["cost"] > 0)
