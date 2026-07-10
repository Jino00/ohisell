# expert_ledger.py — E1a SA3(단일 책임): 전문가 평결 저장(record) + 검증가능예측 채점
# (grade_due_predictions). PLAN §8.
#
# ⚠️C3 자문 경계 불변식: 이 SA는 naver_expert_review(_run)에만 쓴다. NaverProposal.status나
# 실행상태는 절대 건드리지 않는다(D-3 관찰모드, 전문가는 자문일 뿐) — 이 파일에는 NaverProposal
# 을 import조차 하지 않는다(경계를 코드 구조로도 강제).
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverExpertReview, NaverExpertReviewRun, NaverLearningState
from app.services.naver_ad.account_diagnosis import LOW_CLICK_THRESHOLD
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

log = logging.getLogger(__name__)

# 예측 검증 창 — D+7(NaverProposal.verify_date "D+7/14"의 짧은 쪽과 동일 관례). 채점 시
# before/after 각각 이만큼의 창으로 실적을 풀링한다(키워드 단위 일별 판단은 통계적으로
# 불가하다는 NaverAdDaily 모델 docstring 전례를 그대로 따름).
_PREDICTION_VERIFY_DAYS = 7
_GRADE_WINDOW_DAYS = 7

_SCOREBOARD_SCOPE = "expert"
_SCOREBOARD_METRIC = "prediction_accuracy"
_SCOREBOARD_SCOPE_KEY = "all"

_GRADEABLE_METRICS = ("clk", "cost", "conv_amt", "cpc")
_GRADEABLE_TARGET_TYPES = ("campaign", "adgroup", "keyword")


def _assert_session_clean_of_naver_proposal(db: Session) -> None:
    """C3 방어(codex review 발견): 이 모듈의 db.flush()/db.commit()은 세션 전체를 흘려보낸다 —
    같은 세션에 커밋 안 된 NaverProposal 변경이 있으면 그것까지 같이 커밋돼 자문 경계(D-3)를
    깰 수 있다. NaverProposal을 import하지 않고도(구조적 경계 유지) 테이블명으로만 검사한다."""
    for obj in set(db.new) | set(db.dirty) | set(db.deleted):
        if getattr(type(obj), "__tablename__", None) == "naver_proposals":
            raise RuntimeError(
                "C3 자문 경계 위반 위험: 세션에 커밋되지 않은 NaverProposal 변경이 있는 상태로 "
                "expert_ledger가 호출됨 — 먼저 커밋하거나 별도 세션을 쓰세요."
            )


def record(db: Session, as_of: date, review: dict) -> NaverExpertReviewRun:
    """review(ava_reviewer.review 반환값)를 run 원장+평결 child로 저장.

    멱등은 status='ok' run에 대해서만 — 같은 (as_of, briefing_hash)에 이미 status='ok' run이
    있으면 새로 쓰지 않고 그 run을 반환한다. degraded/failed run은 재시도를 막지 않는다
    (2026-07-10 prod 실증: 스키마 검증 실패로 평결이 비어있는 degraded run이 같은 날 성공한
    재시도를 삼켜 새 평결 43건이 조용히 버려짐 — 기존 degraded 행은 삭제·수정하지 않고
    새 run을 별도 행으로 원장에 누적한다).
    재실행(진짜 새 브리핑, 다른 briefing_hash)은 같은 날이어도 새 run(덮어쓰기 아님).
    """
    _assert_session_clean_of_naver_proposal(db)
    briefing_hash = review.get("briefing_hash")
    existing = (
        db.query(NaverExpertReviewRun)
        .filter(
            NaverExpertReviewRun.as_of == as_of,
            NaverExpertReviewRun.briefing_hash == briefing_hash,
            NaverExpertReviewRun.status == "ok",
        )
        .order_by(NaverExpertReviewRun.id.desc())
        .first()
    )
    if existing is not None:
        return existing

    run = NaverExpertReviewRun(
        as_of=as_of,
        model=review.get("model", ""),
        prompt_version=review.get("prompt_version", ""),
        briefing_hash=briefing_hash,
        raw=review.get("raw"),
        usage_json=json.dumps(review.get("usage") or {}, ensure_ascii=False),
        status=review.get("status", "ok"),
    )
    db.add(run)
    db.flush()  # run.id 확보(커밋 전 — 아래 verdict 행들이 FK로 참조)

    for v in review.get("verdicts", []):
        checkable = v.get("checkable_prediction")
        verify_date = as_of + timedelta(days=_PREDICTION_VERIFY_DAYS) if checkable else None
        outcome = "pending" if checkable else None
        db.add(NaverExpertReview(
            run_id=run.id, as_of=as_of, proposal_id=v["proposal_id"], verdict=v["verdict"],
            confidence=v.get("confidence"), reasoning=v.get("reasoning"),
            checkable_prediction=checkable, pred_target_type=v.get("pred_target_type"),
            pred_target_id=v.get("pred_target_id"), pred_metric=v.get("pred_metric"),
            pred_direction=v.get("pred_direction"), verify_date=verify_date, outcome=outcome,
            source="local",
        ))

    commentary = review.get("commentary")
    if commentary:
        db.add(NaverExpertReview(
            run_id=run.id, as_of=as_of, proposal_id=None, verdict="commentary",
            reasoning=commentary, source="local",
        ))

    db.commit()
    return run


def grade_due_predictions(db: Session, today: date) -> dict:
    """verify_date<=today & outcome=pending인 검증가능예측을 실측 대조해 채점 + 성적표 갱신.

    이미 채점된(outcome != pending) 행은 쿼리에서 제외되므로 재호출해도 재채점하지 않는다(멱등).
    """
    _assert_session_clean_of_naver_proposal(db)
    due = (
        db.query(NaverExpertReview)
        .filter(
            NaverExpertReview.outcome == "pending",
            NaverExpertReview.verify_date.isnot(None),
            NaverExpertReview.verify_date <= today,
        )
        .order_by(NaverExpertReview.id.asc())
        .all()
    )

    graded = {"correct": 0, "wrong": 0, "unverifiable": 0}
    for row in due:
        outcome = _grade_one(db, row)
        row.outcome = outcome
        graded[outcome] += 1

    db.flush()  # 세션 autoflush=False라 아래 집계 쿼리가 방금 채점한 outcome을 보려면 필요
    _upsert_scoreboard(db)  # 채점 결과와 같은 트랜잭션으로 한 번에 커밋(원자성, codex 발견 P1)
    db.commit()

    return {"graded": len(due), **graded}


def _grade_one(db: Session, row: NaverExpertReview) -> str:
    """예측(pred_metric/pred_direction)을 before/after 창 실측과 대조. 판단 불가는 unverifiable
    (정직 노트: prod 이력이 짧을수록 초기 다수가 unverifiable로 나오는 게 설계대로다)."""
    metric = row.pred_metric
    if metric not in _GRADEABLE_METRICS or row.pred_direction not in ("up", "down"):
        return "unverifiable"
    if row.pred_target_type not in _GRADEABLE_TARGET_TYPES or not row.pred_target_id:
        # codex 발견(P1): 미지의 target_type을 campaign으로 잘못 폴백시키면 오채점 위험.
        return "unverifiable"

    before_to = row.as_of - timedelta(days=1)
    before_from = before_to - timedelta(days=_GRADE_WINDOW_DAYS - 1)
    after_to = row.verify_date
    after_from = after_to - timedelta(days=_GRADE_WINDOW_DAYS - 1)

    if after_from <= before_to:
        # codex 발견(P2): _PREDICTION_VERIFY_DAYS/_GRADE_WINDOW_DAYS 관례를 벗어난 행(예: 수동
        # 구성, 상수 변경)은 창이 겹쳐 같은 실적을 이중계산할 수 있다 — 겹치면 판단 보류.
        return "unverifiable"

    before = _aggregate_metric(db, row.pred_target_type, row.pred_target_id, before_from, before_to)
    after = _aggregate_metric(db, row.pred_target_type, row.pred_target_id, after_from, after_to)

    # 클릭 볼륨이 너무 낮으면(통계적 판단 불가) unverifiable — cpc/conv_amt도 클릭모수 위에 서 있다.
    if before["clk"] < LOW_CLICK_THRESHOLD or after["clk"] < LOW_CLICK_THRESHOLD:
        return "unverifiable"

    before_val, after_val = before[metric], after[metric]
    if before_val is None or after_val is None:
        return "unverifiable"

    if before_val == after_val:
        # codex 발견(P1): 변동 없음을 "down"으로 단정하면 down 예측이 거짓으로 correct가 된다 —
        # 방향성 주장에 대해 무변동은 판단 근거가 없다는 뜻이라 unverifiable이 정직하다.
        return "unverifiable"

    actual_direction = "up" if after_val > before_val else "down"
    return "correct" if actual_direction == row.pred_direction else "wrong"


def _aggregate_metric(db: Session, target_type: str, target_id: str, date_from: date, date_to: date) -> dict:
    q = db.query(
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
    ).filter(
        NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    )
    if target_type == "keyword":
        q = q.filter(NaverAdDaily.keyword_id == target_id)
    elif target_type == "adgroup":
        q = q.filter(NaverAdDaily.adgroup_id == target_id)
    else:  # campaign
        q = q.filter(NaverAdDaily.campaign_id == target_id)

    clk, cost, direct, indirect = q.first()
    clk, cost = int(clk), int(cost)
    conv_amt = int(direct) + int(indirect)
    cpc = (Decimal(cost) / Decimal(clk)) if clk > 0 else None
    return {"clk": clk, "cost": cost, "conv_amt": conv_amt, "cpc": cpc}


def _upsert_scoreboard(db: Session) -> None:
    """naver_expert_review.outcome 전체에서 매번 정확히 재집계(ground truth) — codex 발견(P1):
    저장된 반올림 비율로 이전 correct 수를 역산하면 반복 갱신 시 표류할 수 있어, 대신 매번
    correct/wrong 행 수를 직접 세어 정확한 비율을 재계산한다(표 자체가 작아 비용 저렴)."""
    correct_n = db.query(NaverExpertReview).filter(NaverExpertReview.outcome == "correct").count()
    wrong_n = db.query(NaverExpertReview).filter(NaverExpertReview.outcome == "wrong").count()
    verifiable_n = correct_n + wrong_n
    if verifiable_n == 0:
        return  # 정직 경계: 채점 가능한 신호가 하나도 없으면 성적표를 건드리지 않는다.

    row = (
        db.query(NaverLearningState)
        .filter(
            NaverLearningState.scope == _SCOREBOARD_SCOPE,
            NaverLearningState.scope_key == _SCOREBOARD_SCOPE_KEY,
            NaverLearningState.metric == _SCOREBOARD_METRIC,
        )
        .first()
    )
    if row is None:
        row = NaverLearningState(
            scope=_SCOREBOARD_SCOPE, scope_key=_SCOREBOARD_SCOPE_KEY, metric=_SCOREBOARD_METRIC, sample_n=0,
        )
        db.add(row)

    row.sample_n = verifiable_n
    row.current_value = (Decimal(correct_n) / Decimal(verifiable_n)).quantize(Decimal("0.0001"))
