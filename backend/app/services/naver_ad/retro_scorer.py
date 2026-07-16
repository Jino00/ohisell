# retro_scorer.py — retro_scorer SA (D-NAO-45, PLAN_naver-ad-retro-scoring §4-C2)
# 역할: naver_retro_signal에 스냅샷된 신호를 D+3/D+7 사후창 실적(naver_ad_daily 상세)으로
#   방향 채점(correct/gray/wrong/no_spend)한다. retro_snapshotter가 남긴 asof 시점 고정
#   렌즈(cf_asof/bep_asof/target_asof)를 그대로 판정 기준으로 쓴다(채점 재현성 — 나중에
#   계정 BEP·보정계수가 바뀌어도 이 판정은 변하지 않음).
#   읽기 + 본인 테이블 쓰기만(원칙18-1) — 외부 API 호출 없음.
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverRetroSignal
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_now


def _post_agg(db: Session, grain: str, target_id: str, date_from: date, date_to: date) -> tuple[int, int]:
    """사후창 (cost, conv) 합계 — 상세행(≠BACKFILL_SENTINEL_ADGROUP)만 grain 컬럼으로 필터."""
    col = NaverAdDaily.adgroup_id if grain == "adgroup" else NaverAdDaily.keyword_id
    row = db.query(
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0)
        + sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
    ).filter(
        NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        col == target_id,
    ).one()
    return int(row[0]), int(row[1])


def _judge(
    direction: str, cost_post: int, conv_post: int, cf_asof: float, bep_asof: float, target_asof: float,
) -> tuple[str, float | None, int]:
    """PLAN §4-C2 판정 규약(ref 31 §1-a 동일). bleed = round(cost_post − conv_post×cf/bep)
    (bep_asof=계정 순수 손익분기) — no_spend를 포함해 전 케이스 균일 계산(no_spend는
    cost_post=conv_post=0이라 자연히 0)."""
    bleed = round(cost_post - conv_post * cf_asof / bep_asof)
    if cost_post == 0:
        return "no_spend", None, bleed

    roas_c = (conv_post / cost_post) * cf_asof
    if direction == "down":
        if roas_c < bep_asof:
            verdict = "correct"
        elif roas_c < target_asof:
            verdict = "gray"
        else:
            verdict = "wrong"
    elif direction == "pause":
        if conv_post == 0:
            verdict = "correct"
        elif roas_c < bep_asof:
            verdict = "gray"
        else:
            verdict = "wrong"
    else:  # up
        if roas_c >= target_asof:
            verdict = "correct"
        elif roas_c >= bep_asof:
            verdict = "gray"
        else:
            verdict = "wrong"
    return verdict, roas_c, bleed


def _score_window(db: Session, today: date, *, horizon: int, wait_days: int) -> int:
    """horizon(3 또는 7)일 사후창 채점. asof_date <= today-wait_days AND verdict_d{horizon}
    IS NULL 전부(밀린 것 포함 catch-up) — 사후창 = asof+1..asof+horizon."""
    verdict_col = getattr(NaverRetroSignal, f"verdict_d{horizon}")
    rows = db.query(NaverRetroSignal).filter(
        NaverRetroSignal.asof_date <= today - timedelta(days=wait_days),
        verdict_col.is_(None),
    ).all()

    for row in rows:
        post_from = row.asof_date + timedelta(days=1)
        post_to = row.asof_date + timedelta(days=horizon)
        cost_post, conv_post = _post_agg(db, row.grain, row.target_id, post_from, post_to)
        verdict, roas_c, bleed = _judge(
            row.direction, cost_post, conv_post, row.cf_asof, row.bep_asof, row.target_asof,
        )
        setattr(row, f"verdict_d{horizon}", verdict)
        setattr(row, f"scored_d{horizon}_at", kst_now())
        setattr(row, f"cost_post{horizon}", cost_post)
        setattr(row, f"conv_post{horizon}", conv_post)
        setattr(row, f"roas_c_post{horizon}", roas_c)
        setattr(row, f"bleed_post{horizon}", bleed)

    return len(rows)


def score_due(db: Session, today: date) -> dict:
    """d3: asof_date <= today-4 AND verdict_d3 IS NULL(사후창 asof+1..asof+3) /
    d7: asof_date <= today-8 AND verdict_d7 IS NULL(사후창 asof+1..asof+7).
    같은 행 이중 채점 금지는 verdict IS NULL 조건이 가드(재실행해도 채점된 행은 재조회 대상에서 빠짐)."""
    scored_d3 = _score_window(db, today, horizon=3, wait_days=4)
    scored_d7 = _score_window(db, today, horizon=7, wait_days=8)
    db.commit()
    return {"scored_d3": scored_d3, "scored_d7": scored_d7}
