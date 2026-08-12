# search_term_scorecard.py — 검색어 grain 성적표 (D-NAO-173 P2-②,
#   docs/PLAN_search-term-exclusion-list.md §4-a·§5 P2)
#
# ## 왜 별도 층인가 (기존 diary_outcome이 있는데도)
# `diary_outcome`은 결과를 `naver_ad_daily`(캠페인·그룹 grain)에서 잰다. 그래서 우리 조치의
# **직접 효과**(그 검색어의 비용이 실제로 죽었나)를 원리적으로 못 본다 — 검색어 축이 그
# 테이블에 없기 때문이다. 두 층으로 나눠 본다:
#   · 직접 효과(여기)  — 자른 그 검색어가 실제로 멈췄나. `naver_search_term_daily`.
#   · 부작용(diary_outcome) — 캠페인 전환매출·ROAS가 유지됐나(볼륨 절멸이 났나).
# 한 층만 보면 각각 다른 방식으로 속는다: 직접 효과만 보면 «잘랐더니 매출이 같이 죽은 것»을
# 못 보고, 캠페인만 보면 «애초에 조치가 안 걸린 것»을 성과로 읽는다.
#
# ## 정직 규칙(이 파일의 존재 이유)
# ① **사후 창이 성숙 전이면 판정하지 않는다** — `pending`으로 표시한다. 「아직」과 「효과 없음」을
#    같은 칸에 넣으면 하루 뒤 스크린샷이 실패로 보인다.
# ② **일평균으로 비교한다** — 전후 창의 날짜 수가 다르면 총합 비교는 그 자체로 거짓말이다.
# ③ **자른 뒤에도 비용이 남으면 그것이 1급 결과다**(`still_spending`). 조치가 안 걸린 것을
#    조용히 «효과 미미»로 적지 않는다.
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverSearchTermDaily, NaverSearchTermExclusion
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.search_term_exclusion_list import (
    MATURITY_LAG_DAYS,
    _bep_for_adgroups,
)
from app.utils.kst import kst_now

# 전후 대조 창(일) — 기본 14일. 30일로 잡으면 첫 성적표를 한 달 뒤에나 볼 수 있고, 7일로 잡으면
# 요일 효과가 그대로 남는다(주 단위 2회전이 최소).
WINDOW_DAYS = 14
# 사후 창의 최소 관측일 — 이보다 짧으면 판정하지 않고 pending. 성숙도 컷오프(D+3)를 뺀 뒤의
# 실관측 일수 기준이다.
MIN_AFTER_DAYS = 3
# 「비용이 죽었다」의 문턱 — 일평균 비용이 전 대비 이 비율 미만이면 조치가 걸린 것으로 본다.
# 0이 아니라 10%인 이유: 제외 반영에 하루 정도 지연이 있고, 실행 당일 잔여 비용이 창에 섞인다.
_STOPPED_RATIO = 0.10


def _term_window(
    db: Session, adgroup_id: str, search_term: str, frm: date, to: date
) -> dict:
    row = (
        db.query(
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.clk), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.conv_purchase_cnt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.conv_purchase_amt), 0),
        )
        .filter(
            NaverSearchTermDaily.adgroup_id == adgroup_id,
            NaverSearchTermDaily.search_term == search_term,
            NaverSearchTermDaily.ad_date >= frm,
            NaverSearchTermDaily.ad_date <= to,
        )
        .first()
    )
    days = max((to - frm).days + 1, 1)
    cost, clk, conv, amt = (int(x or 0) for x in row)
    return {
        "from": frm.isoformat(), "to": to.isoformat(), "days": days,
        "cost": cost, "clk": clk, "conv_purchase_cnt": conv, "conv_purchase_amt": amt,
        "cost_per_day": round(cost / days, 1),
        "amt_per_day": round(amt / days, 1),
    }


def _campaign_window(db: Session, campaign_id: str, frm: date, to: date, bep) -> dict:
    """부작용 축 — 캠페인 전체가 어떻게 됐나. 총이익 기여는 리스트 생성기와 같은 정의
    (전환매출/BEP − 광고비)를 쓴다. BEP가 없으면 총이익은 None(추정하지 않는다)."""
    row = (
        db.query(
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
            sqlfunc.coalesce(
                sqlfunc.sum(NaverAdDaily.conv_direct_amt + NaverAdDaily.conv_indirect_amt), 0
            ),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        )
        .filter(
            NaverAdDaily.campaign_id == campaign_id,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
            NaverAdDaily.ad_date >= frm,
            NaverAdDaily.ad_date <= to,
        )
        .first()
    )
    days = max((to - frm).days + 1, 1)
    cost, amt, clk = (int(x or 0) for x in row)
    profit = None if not bep else int(Decimal(amt) / bep) - cost
    return {
        "from": frm.isoformat(), "to": to.isoformat(), "days": days,
        "cost": cost, "conv_amt": amt, "clk": clk,
        "cost_per_day": round(cost / days, 1),
        "conv_amt_per_day": round(amt / days, 1),
        "profit_contrib": profit,
        "profit_contrib_per_day": None if profit is None else round(profit / days, 1),
    }


def _verdict(before: dict, after: dict, after_days: int) -> tuple[str, str]:
    """(verdict, 한 줄 설명). 판정 불가는 판정하지 않는다."""
    if after_days < MIN_AFTER_DAYS:
        return "pending", (
            f"실행 후 성숙 관측일이 {after_days}일뿐이다(최소 {MIN_AFTER_DAYS}일) — 아직 판정하지 않는다."
        )
    if before["cost_per_day"] <= 0:
        return "no_baseline", "실행 전 창에 비용이 없어 비교 기준이 없다(판정 불가)."
    ratio = after["cost_per_day"] / before["cost_per_day"]
    if ratio <= _STOPPED_RATIO:
        return "stopped", (
            f"일평균 광고비가 {int(before['cost_per_day']):,}원 → {int(after['cost_per_day']):,}원"
            f"({ratio * 100:.1f}%)로 멈췄다 — 조치가 걸렸다."
        )
    return "still_spending", (
        f"일평균 광고비가 {int(before['cost_per_day']):,}원 → {int(after['cost_per_day']):,}원"
        f"({ratio * 100:.0f}%)으로 **아직 쓰고 있다** — 제외가 반영되지 않았을 수 있다."
    )


def build_scorecard(
    db: Session, *, now: datetime | None = None, window_days: int = WINDOW_DAYS
) -> dict:
    """실행된 제외 전건의 전후 대조표(순수·read-only).

    각 행: 실행일 · 검색어 · 전 창/후 창(검색어 grain) · 판정 · 회수한 일평균 광고비 ·
    같은 창의 캠페인 grain 대조(부작용). 합계에는 **판정된 것만** 넣는다(pending을 0으로 세면
    성과가 희석되고, 성과로 세면 부풀려진다).
    """
    now = now or kst_now()
    today = now.date()
    # 성숙도 컷오프 — 최근 며칠은 전환이 덜 잡혀 있어 사후 창에서 뺀다(리스트 생성기와 같은 규칙).
    mature_to = today - timedelta(days=MATURITY_LAG_DAYS)

    rows = (
        db.query(NaverSearchTermExclusion)
        .filter(NaverSearchTermExclusion.status == "excluded")
        .order_by(NaverSearchTermExclusion.excluded_at.desc())
        .all()
    )
    bep_map = _bep_for_adgroups(db, {r.adgroup_id for r in rows if r.adgroup_id})

    items: list[dict] = []
    for r in rows:
        if not r.excluded_at:
            continue
        ex_date = r.excluded_at.date()
        before = _term_window(
            db, r.adgroup_id, r.search_term, ex_date - timedelta(days=window_days), ex_date - timedelta(days=1)
        )
        after_to = min(mature_to, ex_date + timedelta(days=window_days))
        after_days = max((after_to - ex_date).days, 0)
        after = _term_window(db, r.adgroup_id, r.search_term, ex_date + timedelta(days=1), after_to) \
            if after_days > 0 else {
                "from": None, "to": None, "days": 0, "cost": 0, "clk": 0,
                "conv_purchase_cnt": 0, "conv_purchase_amt": 0, "cost_per_day": 0.0, "amt_per_day": 0.0,
            }

        verdict, why = _verdict(before, after, after_days)
        bep = (bep_map.get(r.adgroup_id) or {}).get("bep_roas")
        if verdict in ("stopped", "still_spending") and not bep:
            why += " (이 그룹은 BEP가 없어 회수액은 산출하지 않는다 — 같이 사라진 공헌이익을 뺄 수 없다)"
        # 회수액 = (전 일평균 비용 − 후 일평균 비용) × 사후 일수. 매출이 같이 줄었다면 그만큼
        # 공헌이익도 빠지므로 함께 뺀다 — 「비용만 줄었다」를 이익으로 읽지 않기 위해서다.
        saved = None
        if verdict in ("stopped", "still_spending") and bep:
            # ★BEP가 없으면 회수액을 내지 않는다(적대 리뷰 P1-2, fail-closed). 종전에는
            #   margin_lost를 0으로 두고 «비용 절감 전액»을 이익이라 신고했는데, 그건 이 파일
            #   상단 정직 규칙이 막겠다고 선언한 바로 그것이다 — 매출이 같이 죽은 컷이 초록으로
            #   집계된다. 같은 파일 _campaign_window가 `profit = None if not bep`이고, 리스트
            #   생성기도 BEP 없는 그룹을 bep_unknown으로 후보에서 뺀다. 판정층이 「모르면 안
            #   자른다」인데 채점층만 「모르면 전액이 이익」이면 확대 판단의 근거가 부풀려진다.
            cost_saved = (before["cost_per_day"] - after["cost_per_day"]) * after_days
            margin_lost = (before["amt_per_day"] - after["amt_per_day"]) / float(bep) * after_days
            saved = int(cost_saved - margin_lost)

        items.append({
            "exclusion_id": r.id,
            "campaign_id": r.campaign_id,
            "adgroup_id": r.adgroup_id,
            "search_term": r.search_term,
            "excluded_at": r.excluded_at.isoformat(),
            "cost_at_exclusion": r.cost_at_exclusion,
            "live_state": r.live_state,
            "applied_bep": None if not bep else round(float(bep), 4),
            "before": before,
            "after": after,
            "after_days": after_days,
            "verdict": verdict,
            "why": why,
            "profit_recovered": saved,
            "campaign": {
                "before": _campaign_window(
                    db, r.campaign_id, ex_date - timedelta(days=window_days), ex_date - timedelta(days=1), bep
                ),
                "after": _campaign_window(
                    db, r.campaign_id, ex_date + timedelta(days=1), after_to, bep
                ) if after_days > 0 else None,
            },
        })

    judged = [i for i in items if i["verdict"] in ("stopped", "still_spending")]
    return {
        "window_days": window_days,
        "maturity_lag_days": MATURITY_LAG_DAYS,
        "mature_through": mature_to.isoformat(),
        "total": len(items),
        "by_verdict": {
            v: sum(1 for i in items if i["verdict"] == v)
            for v in ("stopped", "still_spending", "pending", "no_baseline")
        },
        # ★판정된 것만 합산한다. pending을 0원으로 세면 성과가 희석되고, 빼고 세면 부풀려진다 —
        #   둘 다 피하려면 «몇 건이 아직 판정 밖인지»를 함께 내보내는 수밖에 없다.
        "profit_recovered_judged": sum(i["profit_recovered"] or 0 for i in judged),
        "judged_count": len(judged),
        # ★판정은 났지만 BEP가 없어 «회수액을 못 낸» 건수(적대 리뷰 P1-2). 위 합계는 이들을
        #   0원으로 세므로, 이 숫자 없이는 «회수액이 적다»와 «회수액을 못 잰다»가 구분되지
        #   않는다 — pending_count를 따로 내보내는 것과 같은 이유다.
        "profit_unknown_count": sum(1 for i in judged if i["profit_recovered"] is None),
        "pending_count": sum(1 for i in items if i["verdict"] == "pending"),
        "items": items,
        "as_of": now.isoformat(),
    }
