# retro_scorer.py — retro_scorer SA (D-NAO-45, PLAN_naver-ad-retro-scoring §4-C2)
# 역할: naver_retro_signal에 스냅샷된 신호를 D+3/D+7 사후창 실적(naver_ad_daily 상세)으로
#   방향 채점(correct/gray/wrong/no_spend)한다. retro_snapshotter가 남긴 asof 시점 고정
#   렌즈(cf_asof/bep_asof/target_asof)를 그대로 판정 기준으로 쓴다(채점 재현성 — 나중에
#   계정 BEP·보정계수가 바뀌어도 이 판정은 변하지 않음).
#   읽기 + 본인 테이블 쓰기만(원칙18-1) — 외부 API 호출 없음.
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import (
    NaverAdDaily,
    NaverCampaignSettings,
    NaverLearningState,
    NaverRetroSignal,
)
from app.services.naver_ad import gave_score
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# D-NAO-72: GAVE 롤업 학습상태 좌표. scope=retro_board(진단 보드=제안 유형), metric별 D+3/D+7.
_GAVE_ROLLUP_SCOPE = "retro_board"


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


def _gamma_for(db: Session, campaign_id: str, cache: dict[str, Decimal]) -> Decimal:
    """캠페인 공격성 다이얼 γ(naver_campaign_settings.gamma, D-NAO-2) — 없거나 NULL이면
    gave_score.DEFAULT_GAMMA(=1). 루프 내 캠페인 반복 조회를 캐시로 절약."""
    if campaign_id in cache:
        return cache[campaign_id]
    row = db.query(NaverCampaignSettings.gamma).filter(
        NaverCampaignSettings.campaign_id == campaign_id,
    ).first()
    gamma = row[0] if row is not None and row[0] is not None else gave_score.DEFAULT_GAMMA
    cache[campaign_id] = gamma
    return gamma


def _score_window(db: Session, today: date, *, horizon: int, wait_days: int) -> int:
    """horizon(3 또는 7)일 사후창 채점. asof_date <= today-wait_days AND verdict_d{horizon}
    IS NULL 전부(밀린 것 포함 catch-up) — 사후창 = asof+1..asof+horizon."""
    verdict_col = getattr(NaverRetroSignal, f"verdict_d{horizon}")
    rows = db.query(NaverRetroSignal).filter(
        NaverRetroSignal.asof_date <= today - timedelta(days=wait_days),
        verdict_col.is_(None),
    ).all()

    gamma_cache: dict[str, Decimal] = {}
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

        # D-NAO-72: GAVE 점수 병렬 기록(방향 채점 그대로 유지). ROAS 렌즈를 방향 판정의
        # roas_c와 동일하게 맞추려 매출에 cf_asof를 곱한다(roas = conv_post·cf/cost_post =
        # roas_c) → 같은 행에서 방향 판정과 GAVE가 같은 손익분기(bep_asof)를 본다(채점 재현성).
        # no_spend(cost_post=0)면 gave_score가 score=0 반환(방향 no_spend와 정합).
        gamma = _gamma_for(db, row.campaign_id, gamma_cache)
        scored = gave_score.compute_gave_score(
            revenue=Decimal(conv_post) * Decimal(str(row.cf_asof)),
            cost=cost_post,
            bep_roas=Decimal(str(row.bep_asof)),
            gamma=gamma,
        )
        setattr(row, f"gave_score_d{horizon}", float(scored["score"]))

    return len(rows)


def _rollup_gave_by_board(db: Session, horizon: int) -> dict:
    """제안 유형(board)별 GAVE 점수 합/평균 롤업 → NaverLearningState 상시 공개
    (proposal_scoreboard._rollup_accuracy 관례). 방향 정확도(기존)에 총이익 기여도(신규)를
    더해 "어떤 제안 유형이 총이익에 가장 기여하나"를 실측 — 목적함수 연결의 핵심(D-NAO-59).
    current_value=평균 GAVE(유형 간 비교 가능, Numeric(14,4) 안전), sample_n=채점 건수.
    """
    score_col = getattr(NaverRetroSignal, f"gave_score_d{horizon}")
    rows = db.query(NaverRetroSignal.board, score_col).filter(score_col.isnot(None)).all()

    by_board: dict[str, dict[str, float]] = {}
    for board, score in rows:
        stats = by_board.setdefault(board, {"n": 0, "sum": 0.0})
        stats["n"] += 1
        stats["sum"] += float(score)

    metric = f"gave_score_d{horizon}"
    result: dict[str, dict] = {}
    for board, stats in by_board.items():
        n = int(stats["n"])
        total = stats["sum"]
        avg = total / n if n else 0.0
        result[board] = {"n": n, "sum": total, "avg": avg}

        state = db.query(NaverLearningState).filter(
            NaverLearningState.scope == _GAVE_ROLLUP_SCOPE,
            NaverLearningState.scope_key == board,
            NaverLearningState.metric == metric,
        ).first()
        if state is None:
            state = NaverLearningState(scope=_GAVE_ROLLUP_SCOPE, scope_key=board, metric=metric)
            db.add(state)
        state.current_value = Decimal(str(round(avg, 4)))
        state.sample_n = n

    if result:
        top = max(result.items(), key=lambda kv: kv[1]["avg"])
        log.info("retro_scorer GAVE 롤업 d%d: boards=%d top=%s(avg=%.1f)",
                 horizon, len(result), top[0], top[1]["avg"])
    return result


def score_due(db: Session, today: date) -> dict:
    """d3: asof_date <= today-4 AND verdict_d3 IS NULL(사후창 asof+1..asof+3) /
    d7: asof_date <= today-8 AND verdict_d7 IS NULL(사후창 asof+1..asof+7).
    같은 행 이중 채점 금지는 verdict IS NULL 조건이 가드(재실행해도 채점된 행은 재조회 대상에서 빠짐)."""
    scored_d3 = _score_window(db, today, horizon=3, wait_days=4)
    scored_d7 = _score_window(db, today, horizon=7, wait_days=8)
    # D-NAO-72: 방향 채점 직후 GAVE 점수를 제안 유형별로 롤업(같은 크론·같은 트랜잭션).
    # flush로 방금 setattr한 gave_score_d{h}를 DB에 반영해야 롤업 쿼리가 본다(autoflush=False
    # 세션 대비 명시 — proposal_scoreboard는 commit 후 롤업하지만 여기선 단일 트랜잭션 유지).
    db.flush()
    gave_rollup = {"d3": _rollup_gave_by_board(db, 3), "d7": _rollup_gave_by_board(db, 7)}
    db.commit()
    return {"scored_d3": scored_d3, "scored_d7": scored_d7, "gave_rollup": gave_rollup}
