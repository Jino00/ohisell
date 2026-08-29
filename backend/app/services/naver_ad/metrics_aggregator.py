# metrics_aggregator.py — metrics_aggregator_sa (단일 책임: naver_ad_daily grain별 집계)
# 역할(SA): 기간·grain·캠페인 필터를 받아 naver_ad_daily를 집계한 rows + 계정 총계 KPI 반환.
#   파생지표(CTR·CPC·avg_rank·ROAS네이버·ROAS직접)는 합산 후 Python에서 계산(정밀도·0분모 안전).
# 순수 쿼리 SA: naver_ad_daily만 읽음(다른 소스 모름). 3열 중 실주문 대조는 actual_revenue SA 소관.
# P2-S2에서 발견: campaign_backfill의 sentinel 행(adgroup_id='__backfill__')은 캠페인grain
#   전용(D-NAO-17) — 실단위 P0 행과 같은 날짜에 공존하면 이 SA의 합계가 이중계상된다.
#   실행 전(2026-07-07 기준 prod 0건)엔 무영향이지만 향후 backfill 실행 시 P1 리포트·P2
#   진단 모두가 이 SA를 거치므로 여기서 한 번에 제외한다.
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from datetime import date

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

# grain → 그룹 기준 컬럼 (hour grain은 hourly_pacing SA 소관, 여기서 제외)
_GRAIN_COLS = {
    "date": (NaverAdDaily.ad_date,),
    "campaign": (NaverAdDaily.campaign_id, NaverAdDaily.campaign_type),
    "adgroup": (NaverAdDaily.campaign_id, NaverAdDaily.adgroup_id),
    "keyword": (NaverAdDaily.campaign_id, NaverAdDaily.adgroup_id, NaverAdDaily.keyword_id),
    # 날짜×광고그룹 — 「그날 그 그룹을 누가 맡고 있었나」를 물으려면 날짜가 키에 남아야 한다
    # (ownership_timeline 밴드 판정의 입력. additive — 기존 grain 동작 불변).
    "date_adgroup": (NaverAdDaily.ad_date, NaverAdDaily.campaign_id, NaverAdDaily.adgroup_id),
}
GRAINS = tuple(_GRAIN_COLS.keys())

_Q2 = Decimal("0.01")
_Q4 = Decimal("0.0001")


def _ratio(num: int, den: int, q: Decimal) -> float | None:
    """num/den을 안전하게 계산. den=0이면 None(미정의)."""
    if not den:
        return None
    return float((Decimal(num) / Decimal(den)).quantize(q, ROUND_HALF_UP))


def _derive(raw: dict) -> dict:
    """합산 raw(imp/clk/cost/rank_sum/conv_*)에서 파생지표를 계산해 병합."""
    imp, clk, cost = raw["imp"], raw["clk"], raw["cost"]
    conv_cnt = raw["conv_direct_cnt"] + raw["conv_indirect_cnt"]
    conv_amt = raw["conv_direct_amt"] + raw["conv_indirect_amt"]
    return {
        **raw,
        "conv_cnt": conv_cnt,
        "conv_amt": conv_amt,
        "ctr": _ratio(clk, imp, _Q4),                 # 클릭률
        "cpc": _ratio(cost, clk, _Q2),                # 클릭당 비용(원)
        "avg_rank": _ratio(raw["rank_sum"], imp, _Q2),  # 평균 노출순위
        "roas_naver": _ratio(conv_amt, cost, _Q4),    # ROAS(네이버 직+간접, 배수)
        "roas_direct": _ratio(raw["conv_direct_amt"], cost, _Q4),  # ROAS(직접전환만)
    }


_SUM_COLS = (
    "imp", "clk", "cost", "rank_sum",
    "conv_direct_cnt", "conv_indirect_cnt", "conv_direct_amt", "conv_indirect_amt",
)


def _sum_exprs():
    return [sqlfunc.coalesce(sqlfunc.sum(getattr(NaverAdDaily, c)), 0) for c in _SUM_COLS]


def aggregate(
    db: Session,
    date_from: date,
    date_to: date,
    *,
    grain: str = "date",
    campaign_filter: str | None = None,
) -> dict:
    """naver_ad_daily를 grain별 집계 → {totals, rows}.

    totals = 계정 총계 KPI(파생 포함). rows = grain별 집계 행 리스트(키 필드 + 지표).
    grain은 date/campaign/adgroup/keyword(hour는 hourly_pacing SA). campaign_filter 주면 해당 캠페인만.
    """
    if grain not in _GRAIN_COLS:
        raise ValueError(f"지원하지 않는 grain: {grain} (허용: {GRAINS})")

    base = db.query(NaverAdDaily).filter(
        NaverAdDaily.ad_date >= date_from,
        NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    )
    if campaign_filter:
        base = base.filter(NaverAdDaily.campaign_id == campaign_filter)

    # ── 계정 총계 (단일 행) ──
    total_row = base.with_entities(*_sum_exprs()).one()
    totals_raw = {c: int(v or 0) for c, v in zip(_SUM_COLS, total_row)}
    totals = _derive(totals_raw)

    # ── grain별 rows ──
    group_cols = _GRAIN_COLS[grain]
    q = base.with_entities(*group_cols, *_sum_exprs()).group_by(*group_cols)
    ncols = len(group_cols)
    rows: list[dict] = []
    for r in q.all():
        keys = r[:ncols]
        sums = r[ncols:]
        raw = {c: int(v or 0) for c, v in zip(_SUM_COLS, sums)}
        row = _derive(raw)
        # 키 필드 병합(grain별 식별자)
        if grain == "date":
            row["ad_date"] = keys[0].isoformat() if hasattr(keys[0], "isoformat") else str(keys[0])
        elif grain == "campaign":
            row["campaign_id"], row["campaign_type"] = keys[0], keys[1]
        elif grain == "adgroup":
            row["campaign_id"], row["adgroup_id"] = keys[0], keys[1]
        elif grain == "date_adgroup":
            row["ad_date"] = keys[0].isoformat() if hasattr(keys[0], "isoformat") else str(keys[0])
            row["ad_date_obj"] = keys[0]
            row["campaign_id"], row["adgroup_id"] = keys[1], keys[2]
        else:  # keyword
            row["campaign_id"], row["adgroup_id"], row["keyword_id"] = keys[0], keys[1], keys[2]
        rows.append(row)

    # 비용 큰 순 정렬(날짜 grain은 날짜 오름차순 — 시계열)
    if grain == "date":
        rows.sort(key=lambda x: x["ad_date"])
    else:
        rows.sort(key=lambda x: x["cost"], reverse=True)

    return {"totals": totals, "rows": rows, "grain": grain}
