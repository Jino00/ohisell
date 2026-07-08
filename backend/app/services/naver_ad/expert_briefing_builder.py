# expert_briefing_builder.py — E1a SA1(단일 책임): 전문가(Ava) 검토용 브리핑 조립.
# 결정적(같은 DB 상태·as_of → 같은 브리핑) — LLM 호출 없음, 페르소나도 안 넣는다(SA2 몫).
# 오늘 pending 제안 + 진단보드 요약 + forecast 예측vs실측 롤업 + 최근 trigger + 로컬 성적표를
# 읽기 전용으로만 조립한다(D-3 관찰모드, 어떤 상태도 변경하지 않음).
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models import NaverEntity, NaverForecastDaily, NaverLearningState, NaverProposal
from app.services.naver_ad.diagnosis import build_diagnosis
from app.services.naver_ad.proposal_scoreboard import METRIC as PROPOSAL_ACCURACY_METRIC
from app.services.naver_ad.trigger_watch import PROPOSAL_TYPE_CPC, PROPOSAL_TYPE_PACING
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

_DIAGNOSIS_WINDOW_DAYS = 14  # naver_ad.py 라우터 /diagnosis 기본 창(15일)과 동일
_FORECAST_ROLLUP_LIMIT = 20  # 최근 채점된 예측 롤업 개수 상한
_TRIGGER_RECENT_LIMIT = 20  # 최근 trigger 이벤트(NaverProposal trigger_* 타입) 개수 상한
_TRIGGER_PROPOSAL_TYPES = (PROPOSAL_TYPE_PACING, PROPOSAL_TYPE_CPC)

# 토큰가드: 이 코드베이스에 기존 토큰캡 전례가 없어 신규 도입(원칙19 "no silent cap").
# 문자수 근사(한글 1자≈1~2토큰, 여유있게 보수적으로 낮게 잡음) — 정밀 토크나이저는 아니다.
_MAX_PROPOSALS_CHARS = 20000


def build(db: Session, as_of: date | None = None) -> dict:
    """전문가 검토 브리핑 조립(SA1, 결정적). LLM 호출 없음 — 페르소나는 SA2(ava_reviewer) 몫."""
    as_of = as_of or kst_today()

    proposals, dropped_ids = _build_pending_proposals(db)
    diagnosis_summary = _build_diagnosis_summary(db, as_of)
    forecast_rollup = _build_forecast_rollup(db)
    recent_triggers = _build_recent_triggers(db)
    scoreboard_summary = _build_scoreboard_summary(db)

    return {
        "as_of": as_of.isoformat(),
        "pending_proposals": proposals,
        "diagnosis_summary": diagnosis_summary,
        "forecast_rollup": forecast_rollup,
        "recent_triggers": recent_triggers,
        "scoreboard_summary": scoreboard_summary,
        "truncated": {"pending_proposals_dropped_ids": dropped_ids} if dropped_ids else {},
    }


def _build_pending_proposals(db: Session) -> tuple[list[dict], list[int]]:
    rows = db.query(NaverProposal).filter(NaverProposal.status == "pending").order_by(NaverProposal.id.asc()).all()

    pairs = {(r.target_type, r.target_id) for r in rows if r.target_id}
    entity_names = _entity_names(db, pairs)

    proposals = [
        {
            "id": r.id,
            "proposal_type": r.proposal_type,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "target_name": entity_names.get((r.target_type, r.target_id)),
            "campaign_id": r.campaign_id,
            "rationale": r.rationale,
            "expected_effect": r.expected_effect,
        }
        for r in rows
    ]
    return _apply_token_guard(proposals)


def _entity_names(db: Session, pairs: set[tuple[str, str]]) -> dict[tuple[str, str], str]:
    if not pairs:
        return {}
    conds = [and_(NaverEntity.entity_type == t, NaverEntity.entity_id == i) for t, i in pairs]
    rows = db.query(NaverEntity).filter(or_(*conds)).all()
    return {(r.entity_type, r.entity_id): r.name for r in rows}


def _apply_token_guard(proposals: list[dict]) -> tuple[list[dict], list[int]]:
    """예산 초과 시 오래된(리스트 앞쪽, id 오름차순) 제안부터 절삭 + 로깅(no silent cap)."""
    kept = list(proposals)
    dropped_ids: list[int] = []
    while kept and len(json.dumps(kept, ensure_ascii=False, default=str)) > _MAX_PROPOSALS_CHARS:
        dropped_ids.append(kept.pop(0)["id"])

    if dropped_ids:
        log.warning(
            "expert_briefing_builder: 토큰가드 절삭 — pending_proposals %d건 제거(오래된 순, 예산 %d자 초과): ids=%s",
            len(dropped_ids), _MAX_PROPOSALS_CHARS, dropped_ids,
        )
    return kept, dropped_ids


def _build_diagnosis_summary(db: Session, as_of: date) -> dict:
    date_to = as_of
    date_from = date_to - timedelta(days=_DIAGNOSIS_WINDOW_DAYS)
    result = build_diagnosis(db, date_from, date_to)

    boards = result.get("boards")
    board_summary = None
    if boards is not None:
        board_summary = {k: ({"count": len(v)} if isinstance(v, list) else v) for k, v in boards.items()}

    summary = {
        "window": result["window"],
        "account_bep_roas": result.get("account_bep_roas"),
        "account_target_roas": result.get("account_target_roas"),
        "boards": board_summary,
    }
    if "error" in result:
        summary["error"] = result["error"]
    return summary


def _build_forecast_rollup(db: Session) -> list[dict]:
    rows = (
        db.query(NaverForecastDaily)
        .filter(NaverForecastDaily.scored_at.isnot(None))
        .order_by(NaverForecastDaily.target_date.desc(), NaverForecastDaily.id.desc())
        .limit(_FORECAST_ROLLUP_LIMIT)
        .all()
    )
    return [
        {
            "target_date": r.target_date.isoformat(),
            "grain": r.grain,
            "scope_key": r.scope_key,
            "pred_clk": r.pred_clk,
            "actual_clk": r.actual_clk,
            "pred_cost": r.pred_cost,
            "actual_cost": r.actual_cost,
            "mape_clk": float(r.mape_clk) if r.mape_clk is not None else None,
            "mape_cost": float(r.mape_cost) if r.mape_cost is not None else None,
        }
        for r in rows
    ]


def _build_recent_triggers(db: Session) -> list[dict]:
    rows = (
        db.query(NaverProposal)
        .filter(NaverProposal.proposal_type.in_(_TRIGGER_PROPOSAL_TYPES))
        .order_by(NaverProposal.created_at.desc(), NaverProposal.id.desc())
        .limit(_TRIGGER_RECENT_LIMIT)
        .all()
    )
    return [
        {
            "id": r.id,
            "proposal_type": r.proposal_type,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "campaign_id": r.campaign_id,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


def _build_scoreboard_summary(db: Session) -> list[dict]:
    rows = (
        db.query(NaverLearningState)
        .filter(NaverLearningState.scope == "action_type", NaverLearningState.metric == PROPOSAL_ACCURACY_METRIC)
        .order_by(NaverLearningState.scope_key.asc())
        .all()
    )
    return [
        {
            "action_type": r.scope_key,
            "sample_n": r.sample_n,
            "accuracy": float(r.current_value) if r.current_value is not None else None,
        }
        for r in rows
    ]
