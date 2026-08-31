# expert_briefing_builder.py — E1a SA1(단일 책임): 전문가(Ava) 검토용 브리핑 조립.
# 결정적(같은 DB 상태·as_of → 같은 브리핑) — LLM 호출 없음, 페르소나도 안 넣는다(SA2 몫).
# 오늘 pending 제안 + 진단보드 요약 + forecast 예측vs실측 롤업 + 최근 trigger + 로컬 성적표를
# 읽기 전용으로만 조립한다(D-3 관찰모드, 어떤 상태도 변경하지 않음).
#
# X1a T6(D-NAO-37 ②): pending_proposals는 실행형 제안만 나열한다(전건 카드, ava_reviewer
# expected_ids의 근거) — 정보성 5종(anomaly/anomaly_freshness/account_brief/trigger_pacing/
# trigger_cpc_spike, proposal_writer.INFORMATIONAL_PROPOSAL_TYPES 참조)은 informational_pending
# 유형별 집계로 접어서 별도 키에 담는다(Ava는 실행형 전건 + 정보성 집계 총평 원료만 본다).
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from sqlalchemy import and_, func as sqlfunc, or_
from sqlalchemy.orm import Session

from app.models import NaverEntity, NaverForecastDaily, NaverLearningState, NaverProposal
from app.services.naver_ad import bid_step_types
from app.services.naver_ad.diagnosis import build_diagnosis
from app.services.naver_ad.proposal_scoreboard import METRIC as PROPOSAL_ACCURACY_METRIC
from app.services.naver_ad.proposal_writer import INFORMATIONAL_PROPOSAL_TYPES, PARAM_CHANGE
from app.services.naver_ad.wisdom_apply import active_wisdom_prefix
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
    informational_pending = _build_informational_pending(db)
    diagnosis_summary = _build_diagnosis_summary(db, as_of)
    forecast_rollup = _build_forecast_rollup(db)
    recent_triggers = _build_recent_triggers(db)
    scoreboard_summary = _build_scoreboard_summary(db)
    # D-NAO-54 P4(briefing_sa): 활성 지혜를 "참고(지시 아님)" 섹션으로 브리핑 앞부분에 주입한다
    # (wisdom_apply.active_wisdom_prefix — 하니스 성격의 builder가 SA를 호출, 원칙18 허용).
    # 지혜 0건이면 None → 키 자체를 넣지 않아 현행 출력 불변(0건 회귀 계약).
    wisdom_prefix = active_wisdom_prefix(db)

    briefing = {
        "as_of": as_of.isoformat(),
        "pending_proposals": proposals,
        "informational_pending": informational_pending,
        "diagnosis_summary": diagnosis_summary,
        "forecast_rollup": forecast_rollup,
        "recent_triggers": recent_triggers,
        "scoreboard_summary": scoreboard_summary,
        "truncated": {"pending_proposals_dropped_ids": dropped_ids} if dropped_ids else {},
    }
    # 앞부분 주입(0건이면 키 미추가 → 현행 출력 불변). ava_reviewer._build_prompt가 briefing
    # dict를 통째로 JSON 직렬화하므로 이 키가 프롬프트에 그대로 실린다.
    if wisdom_prefix is not None:
        return {"active_wisdom": wisdom_prefix, **briefing}
    return briefing


def _build_pending_proposals(db: Session) -> tuple[list[dict], list[int]]:
    """실행형 pending 제안 전건(D-NAO-37 ②) — 정보성 5종은 제외(_build_informational_pending
    이 유형별 집계로 별도 처리). ava_reviewer.review의 expected_ids가 이 목록의 id만 근거로
    삼는다 — 정보성은 자동으로 검토 대상에서 빠진다(의도된 효과)."""
    # P4 리뷰 P3-1: 결정 전용 param_change도 제외 — 지혜는 이미 active_wisdom prefix로
    # Ava에게 전달되므로 이중 검토이며, LLM 산출 rationale이 프롬프트에 재삽입되는
    # 주입면·비용만 늘린다(실행 결과도 없어 검토 실익 없음).
    # B4 GATE P2-2(D-NAO-65): 카나리 캠페인 전면 제외(아래 filter) — delegation_gate의
    # canary_confirm_only 게이트와 대칭. 함수 레벨 import(순환 리스크 회피, delegation_gate와
    # 동일 관례).
    # ★D-NAO-282: «제한»(Confirm-only) 의미의 집합이다 — 개방 allowlist가 아니다.
    from app.services.naver_ad.auto_operator import AD_BID_CONFIRM_ONLY_CAMPAIGNS

    rows = db.query(NaverProposal).filter(
        NaverProposal.status == "pending",
        NaverProposal.proposal_type.notin_(
            tuple(INFORMATIONAL_PROPOSAL_TYPES) + (PARAM_CHANGE,)
        ),
        # B3 GATE 2R P2-A(D-NAO-65): 소재-레벨(target_type='ad') 제안은 브리핑 제외 —
        # Confirm-only 카나리(D-NAO-5)라 위임 실행이 불가한 카드가 Ava 검토 대상
        # (expected_ids)에 실리면 혼란만 준다. 카나리 2단계 개방 시 delegation_gate의
        # ad 제외와 함께 해제.
        NaverProposal.target_type != "ad",
        # B4 GATE P2-2: 카나리 캠페인의 비-ad 제안(lever-resume의 resume 등)도 전부 제외 —
        # 카나리 기간 = 캠페인 전체 Confirm-only. 카나리 졸업(상수 제거) 시 자동 해제.
        NaverProposal.campaign_id.notin_(AD_BID_CONFIRM_ONLY_CAMPAIGNS),
    ).order_by(NaverProposal.id.asc()).all()

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
            # GATE R2 P2-1: rank-step TOCTOU 마커는 기계 원료 — 브리핑(사람/LLM)엔 제거.
            "expected_effect": bid_step_types.strip_base_bid_marker(r.expected_effect),
        }
        for r in rows
    ]
    return _apply_token_guard(proposals)


def _build_informational_pending(db: Session) -> list[dict]:
    """정보성 pending(D-NAO-37 ②)을 유형별로 집계 — 개별 카드 대신 Ava가 총평(commentary)
    원료로만 쓸 수 있게 건수·고유 캠페인 수만 제공한다. 0건인 유형은 미포함, proposal_type
    오름차순 정렬(결정적). 토큰가드 대상이 아니다(이미 집계라 유형 수만큼만 존재)."""
    rows = (
        db.query(
            NaverProposal.proposal_type,
            sqlfunc.count(NaverProposal.id),
            # 빈 campaign_id('' — account_brief 등 계정 단위)는 캠페인 수에서 제외(codex 지적):
            # nullif로 ''→NULL 변환하면 count(distinct)가 자연히 무시한다.
            sqlfunc.count(sqlfunc.distinct(sqlfunc.nullif(NaverProposal.campaign_id, ""))),
        )
        .filter(
            NaverProposal.status == "pending",
            NaverProposal.proposal_type.in_(INFORMATIONAL_PROPOSAL_TYPES),
        )
        .group_by(NaverProposal.proposal_type)
        .order_by(NaverProposal.proposal_type.asc())
        .all()
    )
    return [
        {"proposal_type": proposal_type, "count": int(count), "campaign_count": int(campaign_count)}
        for proposal_type, count, campaign_count in rows
    ]


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
