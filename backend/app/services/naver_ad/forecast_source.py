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

# ★conv_amt의 회계 기준은 grain마다 다르다(라벨 정정, 2026-07-28) — 표시하는 쪽이 기준을
# 명시하도록 이 SA가 라벨의 단일 진실 소스를 제공한다(원칙18-6, 소비처가 각자 문구를
# 지어내면 드리프트).
#   campaign = sentinel('__backfill__') 행 = /stats 소스라 conv_direct/indirect_amt에
#     구매+장바구니 등 **전 전환액션 합계**가 들어있다(액션 유형 분리 불가, models.py
#     NaverAdDaily docstring 참조). 따라서 이 grain의 conv_amt는 "구매 매출"이 아니라
#     그보다 과대다(03 캠페인 07-27 기준 +64% 실측).
#   adgroup/keyword = 상세 행 = conv_*가 구매만(장바구니는 cart_*로 분리) = 순수 구매 매출.
# ★값 자체는 보정하지 않는다: forecast_scorer의 예측·실측이 둘 다 같은 소스라 내부 일관성이
# 이미 성립한다 — 여기서 상세합으로 갈아끼우면 시계열 레벨이 시프트해 추세 모델과 소급 채점이
# 함께 깨진다. 예측은 입찰 산식(D-NAO-19)에 물려 있지 않으므로(rationale 병기 전용) 정정할
# 것은 계산이 아니라 라벨이다.
CONV_AMT_BASIS_LABEL = {
    "campaign": "전 전환액션=구매+장바구니",
    "adgroup": "구매",
    "keyword": "구매",
}


def daily_series(db: Session, *, grain: str, scope_key: str, date_from: date, date_to: date) -> dict[date, dict]:
    """grain별 {date: {clk,cost,conv_amt}} 시계열.

    campaign: campaign_backfill sentinel 행(그 캠페인 전체 집계의 유일한 소스, 정직 경계).
    adgroup: 해당 adgroup_id의 P0 실단위 행(키워드별로 흩어진 행)을 날짜별로 합산.
    keyword: 해당 keyword_id의 P0 실단위 행(여러 캠페인/그룹에 걸쳐 있어도 날짜별 합산) —
      WEB_SITE만 keyword_id가 실제값이라 자연히 한정된다(SHOPPING/BRAND_SEARCH는 keyword_id=''
      sentinel, models.py NaverAdDaily 참조).

    ★conv_amt의 의미는 grain마다 다르다(campaign=구매+장바구니 합, adgroup/keyword=구매만) —
    CONV_AMT_BASIS_LABEL 참조. 소비처는 이 값을 "매출"로 무기준 표기하지 말 것.

    scope_key=''는 어떤 grain에서도 유효한 스코프가 아니다(campaign_id는 항상 값이 있고,
    adgroup/keyword의 ''는 SHOPPING/BRAND_SEARCH 그룹단위 sentinel과 충돌한다 — codex review
    지적, F2a) — 명시적으로 거부한다.
    """
    if not scope_key:
        raise ValueError("scope_key는 빈 문자열일 수 없음(sentinel과 충돌)")

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
        series: dict[date, dict] = defaultdict(lambda: {"clk": 0, "cost": 0, "conv_amt": 0})
        for r in rows:
            entry = series[r.ad_date]
            entry["clk"] += r.clk
            entry["cost"] += r.cost
            entry["conv_amt"] += r.conv_direct_amt + r.conv_indirect_amt
        return dict(series)

    raise ValueError(f"F2는 campaign/adgroup/keyword grain만 지원: {grain}")


def active_days(db: Session, *, grain: str, scope_key: str, date_from: date, date_to: date) -> int:
    """cost>0인 날짜 수 — daily_series 재사용(원칙18-6, 소싱 로직 이중화 금지)."""
    series = daily_series(db, grain=grain, scope_key=scope_key, date_from=date_from, date_to=date_to)
    return sum(1 for v in series.values() if v["cost"] > 0)
