# naver_ad.py — 네이버 SA 광고 리포트 라우터 (P1/P2-S2/P2-S3, track_naver-ad-optimization)
# GET /api/naver/ad/dashboard-overview — 대시보드 개요(엔진 5단계 라이브 증거 상태 +
#   optimizer_coverage), dashboard_overview SA 단순 read(대시보드 미니 스프린트 T1).
# GET /api/naver/ad/report            — 광고 리포트(KPI·3열 ROAS·드릴다운·시계열), ad_report Harness 경유.
# GET /api/naver/ad/bep               — 상품별 BEP 목록(단순 read, CRUD 직접).
# GET /api/naver/ad/diagnosis         — 진단 보드(출혈/승자/확장버킷/쇼핑BEP/제외후보/3단분류/악순환),
#   diagnosis Harness 경유(P2-S2). D-NAO-15/D-3: 전부 읽기 전용 — 제안·쓰기 없음.
# GET /api/naver/ad/proposals         — 제안 카드 목록(P2-S3, naver_proposals 단순 read).
#   각 행에 expert_verdict(최근 완료(status=ok) run의 평결 요약) 조인(E1a T6, 배지용) +
#   executable/not_executable_reason(X1a T4, naver_execution_harness.real_write_blocker).
# POST /api/naver/ad/proposals/{id}/status  — 상태 전이(승인/반려, X1a T4). D-NAO-5 사람 승인
#   게이트의 유일한 정당 경로 — pending→approved가 이 라우터를 거쳐야 harness.execute()가
#   실행을 허용한다.
# POST /api/naver/ad/proposals/{id}/execute — 실쓰기 실행(X1a T4). naver_execution_harness.
#   execute(dry_run=False) 호출 — 사람이 콘솔에서 승인한 제안만(approved) 실제 집행.
# GET/PUT /api/naver/ad/campaign-settings — optimizer/mode/override 조회·설정(P2-S3, 전환 시
#   naver_change_log에 경량 기록). 광고 API 쓰기는 아님 — 우리 시스템 내부 설정만(D-NAO-13).
# GET /api/naver/ad/expert-reviews    — 전문가(Ava) 평결 목록(E1a T6, naver_expert_review 단순
#   read). proposal_id=NULL 행은 하루 총평. 완료(status=ok) run만 조인(X1a T4, codex
#   아웃사이드 보이스 2026-07-10 합의 — 비-ok run의 child 평결 누출 방어).
# GET/PUT /api/naver/ad/settings/expert-delegation — E2 위임 스위치(X1a T5, D-NAO-25) 조회·
#   설정. Jino만 유형 단위로 명시 위임(자동승인+자동실행, delegation_gate 경유) — 전체 치환
#   저장 + naver_change_log 감사 기록(campaign-settings PUT 전례와 동일 패턴).
# GET /api/naver/ad/retro-scorecard   — 상설 소급 채점 성적표(D-NAO-45). naver_retro_signal
#   (보드별 d3/d7 방향 정밀도)·naver_retro_pacing_score(저속/과속 경보 채점) 단순 rollup
#   read. 정직 경계(ref 31): 방향 정확도 계기판이지 인과 성과 검증 아님(그건 카나리 몫).
# GET /api/naver/ad/change-log        — 변경 이력 조회(D-NAO-47). naver_change_log 단순 read.
#   include_dry_run 기본 False — 1층 "우리 조작 N회"는 실제 집행만 센다(D-47-h 정직성).
#   이 API는 읽기만 하고, 이력을 *채우는* 것은 entity_sync의 diff 밸브와 execution_harness다.
# GET /api/naver/ad/raw/keywords      — 등록 키워드 원자료(prod 91,005행), limit 상한 200 강제.
# GET /api/naver/ad/raw/search-terms  — 검색어 원자료(prod 114,285행), limit 상한 200 강제.
# GET /api/naver/ad/raw/hourly        — 시간당 스냅샷 + daily_budget·spend_ratio(스펙 §1-4의
#   "소진율 미노출" 해소). spend_ratio는 budget 없음/0이면 None(0 나눗셈 금지).
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    NaverAccountSettings,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverExpertReview,
    NaverExpertReviewRun,
    NaverHourlySnapshot,
    NaverLearningState,
    NaverProductBep,
    NaverProposal,
    NaverRetroPacingScore,
    NaverRetroSignal,
    NaverSearchTermDaily,
)
from app.services.naver_ad import dashboard_overview
from app.services.naver_ad import delegation_gate
from app.services.naver_ad import metrics_aggregator
from app.services.naver_ad import naver_execution_harness
from app.services.naver_ad import naver_sa_writer
from app.services.naver_ad import proposal_writer
from app.services.naver_ad.ad_report import build_report
from app.services.naver_ad.diagnosis import build_diagnosis
from app.utils.kst import kst_now, kst_today

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/naver/ad", tags=["naver-ad"])

_VALID_GRAINS = metrics_aggregator.GRAINS + ("hour",)
_MAX_RANGE_DAYS = 180  # 과도한 범위 방지(리포트는 최근 위주)
_MAX_DIAGNOSIS_RANGE_DAYS = 30  # 진단 창은 최근 위주(다기간 비교는 harness 내부에서 30일 고정 사용)


@router.get("/dashboard-overview")
def dashboard_overview_endpoint(db: Session = Depends(get_db)):
    """대시보드 개요 — 엔진 5단계(수집·예측·제안·전문가·학습) 라이브 증거 상태 +
    optimizer_coverage(최근 7일 비용을 ours/mop/none별 합산). 파라미터 없음(단순 read)."""
    return dashboard_overview.build(db)


@router.get("/report")
def report(
    date_from: date = Query(..., description="집계 시작일(YYYY-MM-DD)"),
    date_to: date = Query(..., description="집계 종료일(포함)"),
    grain: str = Query("date", description="date|campaign|adgroup|keyword|hour"),
    compare_from: date | None = Query(None, description="비교기간 시작일"),
    compare_to: date | None = Query(None, description="비교기간 종료일"),
    campaign_id: str | None = Query(None, description="특정 캠페인만 필터"),
    db: Session = Depends(get_db),
):
    """네이버 광고 리포트 — KPI 8칸 + 3열 ROAS + 드릴다운 + 일별 시계열."""
    if grain not in _VALID_GRAINS:
        raise HTTPException(400, f"grain은 {_VALID_GRAINS} 중 하나여야 합니다")
    if date_from > date_to:
        raise HTTPException(400, "date_from은 date_to보다 이후일 수 없습니다")
    if (date_to - date_from).days > _MAX_RANGE_DAYS:
        raise HTTPException(400, f"조회 범위는 최대 {_MAX_RANGE_DAYS}일입니다")
    if (compare_from is None) != (compare_to is None):
        raise HTTPException(400, "비교기간은 시작·종료를 함께 지정해야 합니다")
    if compare_from and compare_to and compare_from > compare_to:
        raise HTTPException(400, "compare_from은 compare_to보다 이후일 수 없습니다")

    return build_report(
        db, date_from, date_to,
        grain=grain,
        compare_from=compare_from, compare_to=compare_to,
        campaign_filter=campaign_id,
    )


_NAVER_CHANNEL_ID = 6
_BEP_SORTS = {
    "bep_roas": NaverProductBep.bep_roas,
    "target_roas": NaverProductBep.target_roas,
    "selling_price": NaverProductBep.selling_price,
    "contribution_margin": NaverProductBep.contribution_margin,
}


def _num(v) -> float | None:
    return float(v) if isinstance(v, Decimal) else v


def _serialize_bep(r: NaverProductBep) -> dict:
    return {
        "channel_product_id": r.channel_product_id,
        "product_master_id": r.product_master_id,
        "product_name": r.product_name,
        "selling_price": _num(r.selling_price),
        "cost_price": _num(r.cost_price),
        "commission_rate": _num(r.commission_rate),
        "logistics_cost": _num(r.logistics_cost),
        "contribution_margin": _num(r.contribution_margin),
        "bep_roas": _num(r.bep_roas),
        "aggressiveness": r.aggressiveness,
        "target_roas": _num(r.target_roas),
        "has_cost": r.has_cost,
    }


@router.get("/bep")
def bep_list(
    only_actionable: bool = Query(False, description="bep_roas 산출된 상품만"),
    sort: str = Query("bep_roas", description="bep_roas|target_roas|selling_price|contribution_margin"),
    desc: bool = Query(False, description="내림차순 여부"),
    limit: int = Query(1000, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    """네이버 상품별 BEP ROAS 목록 (단순 read)."""
    if sort not in _BEP_SORTS:
        raise HTTPException(400, f"sort는 {tuple(_BEP_SORTS)} 중 하나여야 합니다")
    q = db.query(NaverProductBep).filter(NaverProductBep.channel_id == _NAVER_CHANNEL_ID)
    if only_actionable:
        q = q.filter(NaverProductBep.bep_roas.isnot(None))
    col = _BEP_SORTS[sort]
    # NULL은 항상 뒤로(정렬 방향 무관) — actionable 우선 노출
    q = q.order_by(col.is_(None), col.desc() if desc else col.asc())
    rows = q.limit(limit).all()

    total = db.query(NaverProductBep).filter(
        NaverProductBep.channel_id == _NAVER_CHANNEL_ID
    ).count()
    actionable = db.query(NaverProductBep).filter(
        NaverProductBep.channel_id == _NAVER_CHANNEL_ID,
        NaverProductBep.bep_roas.isnot(None),
    ).count()
    return {
        "total": total,
        "actionable": actionable,
        "rows": [_serialize_bep(r) for r in rows],
    }


@router.get("/diagnosis")
def diagnosis(
    date_to: date = Query(None, description="진단 기준일(기본=오늘). 보드별 창은 harness가 자체 결정"),
    date_from: date = Query(None, description="키워드 보드(출혈·승자·확장버킷·쇼핑BEP) 창 시작일(기본=date_to-14, 15일 창)"),
    db: Session = Depends(get_db),
):
    """네이버 광고 진단 보드 — 출혈/굶는승자/확장버킷/쇼핑그룹BEP/제외후보/3단분류/악순환.

    D-NAO-21 보정계수(직전 30일 고정)는 harness 내부에서 항상 계산. date_from/date_to는
    출혈·승자·확장버킷·쇼핑BEP 보드의 실적 창만 조절(기본 15일 — 실측 베이스라인과 동일 창).
    """
    if date_to is None:
        date_to = kst_today()
    if date_from is None:
        date_from = date_to - timedelta(days=14)
    if date_from > date_to:
        raise HTTPException(400, "date_from은 date_to보다 이후일 수 없습니다")
    if (date_to - date_from).days > _MAX_DIAGNOSIS_RANGE_DAYS:
        raise HTTPException(400, f"조회 범위는 최대 {_MAX_DIAGNOSIS_RANGE_DAYS}일입니다")

    return build_diagnosis(db, date_from, date_to)


# ══════════════════════════════════════════════════════════════════
# P2-S3 — 제안 카드·캠페인 optimizer 설정 (관찰 모드, D-3: 읽기전용 제안만)
# ══════════════════════════════════════════════════════════════════
# failed/executing은 X1a T3에서 추가된 상태(harness의 클레임·재승인 재시도 경로) — GET
# /proposals의 status 필터도 이 값들을 받아야 콘솔에서 "실패한 제안" 등을 조회할 수 있다(T4).
_PROPOSAL_STATUSES = {"pending", "approved", "rejected", "expired", "failed", "executing"}
_MAX_PROPOSAL_RANGE_DAYS = 90


def _serialize_proposal(p: NaverProposal, verdict: NaverExpertReview | None) -> dict:
    blocker_reason = naver_execution_harness.real_write_blocker(p)
    return {
        "id": p.id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "proposal_type": p.proposal_type,
        "target_type": p.target_type,
        "target_id": p.target_id,
        "campaign_id": p.campaign_id,
        "adgroup_id": p.adgroup_id,
        # D-NAO-47: 실행 목표값 — 이게 없어서 "입찰 인상" 카드가 *얼마로* 올리는지
        # 화면에 안 나왔다(스펙 §1-6). pending 실행대상 5건이 전부 bid_up이라 바로 체감됨.
        "target_bid": p.target_bid,
        "target_lock": p.target_lock,
        "target_budget": p.target_budget,
        "budget_auto_eligible": p.budget_auto_eligible,
        # D-NAO-47: 정보성/실행형 구분을 백엔드가 준다 — 프론트가 유형 문자열을 하드코딩해
        # 재분류하면 백엔드에 유형이 추가될 때 조용히 드리프트한다.
        "informational": p.proposal_type in proposal_writer.INFORMATIONAL_PROPOSAL_TYPES,
        "rationale": p.rationale,
        "expected_effect": p.expected_effect,
        "status": p.status,
        "slack_ts": p.slack_ts,
        "executed_change_log_id": p.executed_change_log_id,
        "approval_source": p.approval_source,  # X1a T5: console(사람)/delegation(E2 위임 자동승인)
        "expert_verdict": _serialize_expert_verdict_summary(verdict) if verdict else None,
        # X1a T4: 콘솔 실행 버튼 활성화 여부 + 사유(naver_execution_harness.real_write_blocker,
        # 상태와 무관하게 판정 — pending이어도 "구조적으로 실행 가능한 제안인지"는 미리 보여줌).
        "executable": blocker_reason is None,
        "not_executable_reason": blocker_reason,
    }


def _serialize_expert_verdict_summary(v: NaverExpertReview) -> dict:
    """배지용 요약(전체 필드는 GET /expert-reviews에서)."""
    return {"verdict": v.verdict, "confidence": _num(v.confidence), "as_of": v.as_of.isoformat(), "run_id": v.run_id}


def _latest_ok_verdicts_by_proposal(db: Session, proposal_ids: list[int]) -> dict[int, NaverExpertReview]:
    """proposal_id별 가장 최근 완료(status=ok) run의 평결 1개(codex 아웃사이드 보이스: "라우터
    조인=as_of 최근 완료 run의 평결"). 제안이 여러 날 pending으로 남아 재검토됐을 수 있어
    run_id 기준 최신 것만 남긴다 — degraded/skipped/failed run은 애초에 조인 대상에서 제외."""
    if not proposal_ids:
        return {}
    rows = (
        db.query(NaverExpertReview)
        .join(NaverExpertReviewRun, NaverExpertReview.run_id == NaverExpertReviewRun.id)
        .filter(NaverExpertReviewRun.status == "ok", NaverExpertReview.proposal_id.in_(proposal_ids))
        .order_by(
            NaverExpertReview.proposal_id.asc(),
            NaverExpertReviewRun.as_of.desc(),  # codex 발견(P1): run.id만으론 "실제 최신"을 못 보장(백필 등)
            NaverExpertReviewRun.id.desc(),
            NaverExpertReview.id.desc(),
        )
        .all()
    )
    latest: dict[int, NaverExpertReview] = {}
    for r in rows:
        latest.setdefault(r.proposal_id, r)  # 정렬 덕분에 각 proposal_id의 첫 항목이 최신
    return latest


@router.get("/proposals")
def proposals(
    status: str | None = Query(None, description="pending|approved|rejected|expired"),
    date_from: date | None = Query(None, description="created_at 시작일(KST 달력일 경계)"),
    date_to: date | None = Query(None, description="created_at 종료일(포함)"),
    campaign_id: str | None = Query(None, description="특정 캠페인만 필터"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """제안 카드 목록(콘솔) — naver_proposals 단순 read, 최신순. expert_verdict는 최근 완료
    (status=ok) run의 평결 요약(배지용, E1a T6)."""
    if status is not None and status not in _PROPOSAL_STATUSES:
        raise HTTPException(400, f"status는 {sorted(_PROPOSAL_STATUSES)} 중 하나여야 합니다")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(400, "date_from은 date_to보다 이후일 수 없습니다")
    if date_from and date_to and (date_to - date_from).days > _MAX_PROPOSAL_RANGE_DAYS:
        raise HTTPException(400, f"조회 범위는 최대 {_MAX_PROPOSAL_RANGE_DAYS}일입니다")

    q = db.query(NaverProposal)
    if status:
        q = q.filter(NaverProposal.status == status)
    if campaign_id:
        q = q.filter(NaverProposal.campaign_id == campaign_id)
    if date_from:
        q = q.filter(NaverProposal.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.filter(NaverProposal.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    rows = q.order_by(NaverProposal.created_at.desc()).limit(limit).all()
    verdicts = _latest_ok_verdicts_by_proposal(db, [p.id for p in rows])
    return {"rows": [_serialize_proposal(p, verdicts.get(p.id)) for p in rows]}


# ══════════════════════════════════════════════════════════════════
# X1a T4 — 콘솔 승인/반려 + 실행 라우터
# ══════════════════════════════════════════════════════════════════
_VALID_STATUS_TARGETS = {"approved", "rejected"}

# 허용 전이(from, to). pending/failed에서만 approved로 갈 수 있다(D-NAO-5 사람 승인 게이트 —
# approved가 harness.execute()를 허용하는 유일한 상태). approved→rejected는 아직 실행 전
# (executed_change_log_id IS NULL)인 경우만 — 이미 실행된 건 반려로 되돌릴 수 없다.
# failed→{approved,rejected}는 T3 설계의 "재승인만 재시도 경로"(harness가 실쓰기 실패 시
# status='failed'로 종결 — 자동 재시도 없음, 사람이 콘솔에서 재승인해야만 재시도).
# executing/expired/rejected에서의 전이는 전부 금지(사람 조사 대상 — executing은 클레임
# 잔존=크래시로 쓰기 결과 불확실, expired/rejected는 이미 종결된 제안).
_ALLOWED_STATUS_TRANSITIONS = {
    ("pending", "approved"),
    ("pending", "rejected"),
    ("approved", "rejected"),
    ("failed", "approved"),
    ("failed", "rejected"),
}


class ProposalStatusIn(BaseModel):
    status: str  # "approved" | "rejected" — 그 외 값은 400


@router.post("/proposals/{proposal_id}/status")
def proposal_status_transition(
    proposal_id: int, body: ProposalStatusIn, db: Session = Depends(get_db),
):
    """제안 상태 전이(콘솔 승인/반려 버튼, X1a T4). D-NAO-5 "반자동 = Confirm 승인 후 실행"의
    유일한 정당 경로 — pending→approved가 이 라우터를 거쳐야만 harness.execute()가 실행을
    허용한다(harness의 ProposalNotApprovedError 게이트와 대칭).

    원자화: 검증한 현재 status를 WHERE 조건에 넣은 조건부 UPDATE로 동시성 처리
    (naver_execution_harness._execute_add_negative_keyword의 클레임 패턴과 동일 — codex R2
    P1). rowcount!=1이면 그 사이 상태가 바뀐 것 — 409 "새로고침 후 재시도". approved→rejected
    는 WHERE에 executed_change_log_id IS NULL도 포함해 이미 실행된 제안의 반려를 원자적으로
    막는다.
    """
    if body.status not in _VALID_STATUS_TARGETS:
        raise HTTPException(400, f"status는 {sorted(_VALID_STATUS_TARGETS)} 중 하나여야 합니다")

    proposal = db.get(NaverProposal, proposal_id)
    if proposal is None:
        raise HTTPException(404, "제안을 찾을 수 없습니다")

    current = proposal.status
    target = body.status
    if (current, target) not in _ALLOWED_STATUS_TRANSITIONS:
        raise HTTPException(409, f"허용되지 않는 상태 전이: {current} → {target}")

    q = db.query(NaverProposal).filter(
        NaverProposal.id == proposal_id,
        NaverProposal.status == current,
    )
    if (current, target) == ("approved", "rejected"):
        q = q.filter(NaverProposal.executed_change_log_id.is_(None))
    # target=='approved'인 전이는 사람이 이 콘솔 라우터를 직접 호출한 것 — approval_source=
    # 'console'로 감사 기록(X1a T5, delegation_gate의 'delegation'과 대칭). rejected 전이는
    # approval_source를 건드리지 않는다(이력 보존 — 반려됐던 승인의 출처도 남겨둔다).
    values = {"status": target}
    if target == "approved":
        values["approval_source"] = "console"
    rowcount = q.update(values, synchronize_session=False)
    db.commit()
    if rowcount != 1:
        raise HTTPException(409, "상태가 변경됨 — 새로고침 후 재시도")

    db.refresh(proposal)
    verdicts = _latest_ok_verdicts_by_proposal(db, [proposal.id])
    return _serialize_proposal(proposal, verdicts.get(proposal.id))


@router.post("/proposals/{proposal_id}/execute")
def proposal_execute(proposal_id: int, db: Session = Depends(get_db)):
    """제안 실쓰기 실행(콘솔 실행 버튼, X1a T4) — naver_execution_harness.execute(dry_run=False)
    호출. 사람이 위 /status 라우터로 승인(approved)한 제안만 실제 네이버 광고 API에 쓰기가
    가해진다(D-NAO-5).

    사전 차단(하네스 호출 전, 라우터 레이어): 액션 자체가 없는 정보성 제안이거나 아직
    미개방(D-NAO-16)인 액션은 여기서 409로 먼저 막는다 — harness에 그대로 넘기면
    OPEN_ACTIONS에 없는 액션에 대해 execute()의 dry_run 강제 로직이 dry-run change_log를
    만들고 proposal.executed_change_log_id를 박아버리는 함정이 있다(제안이 "소비"돼 버림 —
    T4 회귀 테스트로 못 박음). target_type/adgroup_id 구조 결함(예: negative_keyword인데
    target_type='keyword')은 여기서 가로채지 않는다 — harness 자체 가드
    (_execute_add_negative_keyword, MissingExecutionTargetError)로 흘려보내야 failed 종결
    +change_log 감사 기록이 정상적으로 남는다(422로 매핑, 아래 참조).
    """
    proposal = db.get(NaverProposal, proposal_id)
    if proposal is None:
        raise HTTPException(404, "제안을 찾을 수 없습니다")

    action = naver_execution_harness._ACTION_BY_PROPOSAL_TYPE.get(proposal.proposal_type)
    action_unavailable = (
        action is None
        or action not in naver_execution_harness.OPEN_ACTIONS
        or action not in naver_execution_harness._WRITE_EXECUTORS
    )
    if action_unavailable:
        reason = naver_execution_harness.real_write_blocker(proposal)
        raise HTTPException(409, reason)

    try:
        change_log = naver_execution_harness.execute(db, proposal_id, dry_run=False)
    except (
        naver_execution_harness.ProposalNotApprovedError,
        naver_execution_harness.AlreadyExecutedError,
        naver_execution_harness.OptimizerGuardError,
        naver_execution_harness.WriteNotOpenedError,
        naver_execution_harness.ActionNotExecutableError,
    ) as exc:
        raise HTTPException(409, str(exc))
    except naver_execution_harness.MissingExecutionTargetError as exc:
        raise HTTPException(
            422,
            f"{exc} — 제안은 harness가 이미 'failed'로 종결하고 change_log에 감사 기록을 "
            "남겼습니다(재승인해도 데이터가 바뀌지 않는 영구 결함).",
        )
    except (
        naver_sa_writer.WriteValidationError,
        naver_sa_writer.WriteError,
        naver_sa_writer.WriteVerificationError,
        requests.RequestException,
    ) as exc:
        log.error(
            "naver_ad execute 라우터: 실쓰기 실패 proposal_id=%s — %s: %s",
            proposal_id, type(exc).__name__, exc,
        )
        raise HTTPException(
            502,
            f"{type(exc).__name__}: {exc} — 제안은 failed로 종결됨(change_log 감사 기록 완료) "
            "— 재승인으로만 재시도 가능합니다.",
        )

    db.refresh(proposal)
    verdicts = _latest_ok_verdicts_by_proposal(db, [proposal.id])
    return {
        "change_log_id": change_log.id,
        "outcome": change_log.outcome,
        "before": _parse_json_or_raw(change_log.before_value),
        "after": _parse_json_or_raw(change_log.after_value),
        "proposal": _serialize_proposal(proposal, verdicts.get(proposal.id)),
    }


def _parse_json_or_raw(value: str | None):
    """change_log.before_value/after_value는 JSON 문자열로 저장된다(json.dumps) — 파싱해
    구조화된 값으로 응답한다. 파싱 실패 시 원문 문자열 그대로 반환(방어, 데이터 유실 방지)."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _serialize_expert_review(r: NaverExpertReview) -> dict:
    return {
        "id": r.id,
        "run_id": r.run_id,
        "as_of": r.as_of.isoformat() if r.as_of else None,
        "proposal_id": r.proposal_id,
        "verdict": r.verdict,
        "confidence": _num(r.confidence),
        "reasoning": r.reasoning,
        "checkable_prediction": r.checkable_prediction,
        "pred_target_type": r.pred_target_type,
        "pred_target_id": r.pred_target_id,
        "pred_metric": r.pred_metric,
        "pred_direction": r.pred_direction,
        "verify_date": r.verify_date.isoformat() if r.verify_date else None,
        "outcome": r.outcome,
        "source": r.source,
    }


@router.get("/expert-reviews")
def expert_reviews(
    as_of: date | None = Query(None, description="검토일(기본=전체)"),
    proposal_id: int | None = Query(None, description="특정 제안만 필터(NULL=하루 총평 행은 이 필터로 제외됨)"),
    limit: int = Query(500, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """전문가(Ava) 평결 목록(콘솔) — naver_expert_review 단순 read, 최신순. proposal_id=NULL
    행은 하루 총평(E1a T6). 완료(status=ok) run만 조인(X1a T4, codex 아웃사이드 보이스
    2026-07-10 합의) — degraded/skipped/failed run의 child 평결이 콘솔에 새는 것을 막는다
    (GET /proposals의 _latest_ok_verdicts_by_proposal과 동일한 status=ok 계약)."""
    q = (
        db.query(NaverExpertReview)
        .join(NaverExpertReviewRun, NaverExpertReview.run_id == NaverExpertReviewRun.id)
        .filter(NaverExpertReviewRun.status == "ok")
    )
    if as_of is not None:
        q = q.filter(NaverExpertReview.as_of == as_of)
    if proposal_id is not None:
        q = q.filter(NaverExpertReview.proposal_id == proposal_id)
    rows = q.order_by(NaverExpertReview.id.desc()).limit(limit).all()
    return {"rows": [_serialize_expert_review(r) for r in rows]}


# 표본이 이보다 적으면 정확도%를 헤드라인으로 노출하지 않는다(정직 라벨, codex 아웃사이드
# 보이스 반영 — "정확도%를 competence 신호로 헤드라인 금지"). 정확한 임계값은 스펙에 명시돼
# 있지 않아 보수적으로 20으로 시작(추정 금지 원칙상 실사용 데이터로 재검토 대상).
_SCOREBOARD_HONEST_THRESHOLD = 20


@router.get("/expert-scorecard")
def expert_scorecard(db: Session = Depends(get_db)):
    """전문가 예측 정확도 성적표(콘솔 "Ava의 검토" 패널) — naver_learning_state(scope=expert,
    metric=prediction_accuracy) 단순 read. 표본이 적으면 label에 "표본 축적 중" 류 안내를
    담아 정확도%가 competence 신호로 헤드라인되지 않게 한다(E1a T8)."""
    row = (
        db.query(NaverLearningState)
        .filter(
            NaverLearningState.scope == "expert",
            NaverLearningState.scope_key == "all",  # expert_ledger._SCOREBOARD_SCOPE_KEY 계약과 일치
            NaverLearningState.metric == "prediction_accuracy",
        )
        .first()
    )
    if row is None:
        return {"sample_n": 0, "accuracy": None, "label": "아직 채점된 예측이 없습니다"}
    sample_n = row.sample_n
    accuracy = _num(row.current_value)
    label = "표본 축적 중(참고용)" if sample_n < _SCOREBOARD_HONEST_THRESHOLD else None
    return {"sample_n": sample_n, "accuracy": accuracy, "label": label}


_VALID_OPTIMIZERS = {"none", "ours", "mop"}
_VALID_MODES = {"growth", "recovery", "launch", "defense"}


class CampaignSettingsIn(BaseModel):
    campaign_id: str
    optimizer: str
    mode: str | None = None
    target_roas_override: float | None = None
    gamma: float | None = None
    memo: str | None = None


def _serialize_settings(s: NaverCampaignSettings) -> dict:
    return {
        "campaign_id": s.campaign_id,
        "optimizer": s.optimizer,
        "mode": s.mode,
        "target_roas_override": _num(s.target_roas_override),
        "gamma": _num(s.gamma),
        "memo": s.memo,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


@router.get("/campaign-settings")
def campaign_settings_list(
    campaign_id: str | None = Query(None, description="특정 캠페인만 필터"),
    db: Session = Depends(get_db),
):
    """캠페인별 optimizer/mode/override 조회(최적화 콘솔 패널 로드)."""
    q = db.query(NaverCampaignSettings)
    if campaign_id:
        q = q.filter(NaverCampaignSettings.campaign_id == campaign_id)
    rows = q.order_by(NaverCampaignSettings.campaign_id).all()
    return {"rows": [_serialize_settings(r) for r in rows]}


@router.put("/campaign-settings")
def campaign_settings_put(body: CampaignSettingsIn, db: Session = Depends(get_db)):
    """optimizer/mode/override upsert. optimizer가 실제로 바뀌면 naver_change_log에 경량
    전환 기록(누가/언제는 API 호출 자체·changed_at 서버타임이 근거, 전후 값만 저장 — codex #16).
    이 엔드포인트는 우리 시스템 설정 테이블만 쓴다 — 네이버 광고 API에 쓰기 요청 없음(D-NAO-13).
    """
    if body.optimizer not in _VALID_OPTIMIZERS:
        raise HTTPException(400, f"optimizer는 {sorted(_VALID_OPTIMIZERS)} 중 하나여야 합니다")
    if body.mode is not None and body.mode not in _VALID_MODES:
        raise HTTPException(400, f"mode는 {sorted(_VALID_MODES)} 중 하나여야 합니다")

    settings = db.query(NaverCampaignSettings).filter(
        NaverCampaignSettings.campaign_id == body.campaign_id
    ).first()
    before_optimizer = settings.optimizer if settings else "none"

    if settings is None:
        settings = NaverCampaignSettings(campaign_id=body.campaign_id, optimizer=body.optimizer)
        db.add(settings)
    settings.optimizer = body.optimizer
    settings.mode = body.mode
    settings.target_roas_override = (
        Decimal(str(body.target_roas_override)) if body.target_roas_override is not None else None
    )
    settings.gamma = (
        Decimal(str(body.gamma)) if body.gamma is not None else None
    )
    settings.memo = body.memo

    if before_optimizer != body.optimizer:
        db.add(NaverChangeLog(
            entity_type="campaign", entity_id=body.campaign_id, campaign_id=body.campaign_id,
            action="optimizer_change",
            before_value=before_optimizer, after_value=body.optimizer,
            rationale="콘솔 PUT /campaign-settings",
        ))

    db.commit()
    db.refresh(settings)
    return _serialize_settings(settings)


# ══════════════════════════════════════════════════════════════════
# X1a T5 — E2 위임 스위치(D-NAO-25 부분 게이트) 콘솔 설정
# ══════════════════════════════════════════════════════════════════
_DELEGATION_KEY = "expert_delegated_types"


class ExpertDelegationIn(BaseModel):
    delegated_types: list[str]


def _delegation_response(db: Session) -> dict:
    return {
        "delegated_types": sorted(delegation_gate.get_delegated_types(db) & delegation_gate.delegable_types()),
        "delegable_types": sorted(delegation_gate.delegable_types()),
    }


@router.get("/settings/expert-delegation")
def expert_delegation_get(db: Session = Depends(get_db)):
    """E2 위임 스위치 조회(X1a T5) — 저장값 중 delegable_types()에 속한 것만 정제해 반환
    (미개방 유형이 저장돼 있어도 콘솔엔 유효한 것만 보여준다, delegation_gate와 동일 정제)."""
    return _delegation_response(db)


@router.put("/settings/expert-delegation")
def expert_delegation_put(body: ExpertDelegationIn, db: Session = Depends(get_db)):
    """E2 위임 스위치 설정(X1a T5, D-NAO-25) — Jino만 유형 단위로 명시 위임. 전체 치환 저장.
    delegable_types() 밖 유형이나 중복은 400(콘솔 오조작 방지) — 여기서 막아도 delegation_gate
    자체는 이중 방어로 저장값∩delegable만 쓰므로 안전하지만, 사람에게는 즉시 피드백이 낫다.
    변경 전후가 실제로 다르면 naver_change_log에 감사 기록(campaign-settings PUT 전례)."""
    if not isinstance(body.delegated_types, list):
        raise HTTPException(400, "delegated_types는 리스트여야 합니다")
    if len(set(body.delegated_types)) != len(body.delegated_types):
        raise HTTPException(400, "delegated_types에 중복된 유형이 있습니다")
    allowed = delegation_gate.delegable_types()
    invalid = set(body.delegated_types) - allowed
    if invalid:
        raise HTTPException(
            400, f"허용되지 않는 유형: {sorted(invalid)} — 위임 가능 유형은 {sorted(allowed)}뿐입니다",
        )

    before = sorted(delegation_gate.get_delegated_types(db) & allowed)
    after = sorted(set(body.delegated_types))

    row = db.query(NaverAccountSettings).filter(NaverAccountSettings.key == _DELEGATION_KEY).first()
    if row is None:
        row = NaverAccountSettings(key=_DELEGATION_KEY, value_json=json.dumps(after))
        db.add(row)
    else:
        row.value_json = json.dumps(after)

    if before != after:
        db.add(NaverChangeLog(
            entity_type="account", entity_id="", campaign_id="",
            action="update_expert_delegation",
            before_value=json.dumps(before, ensure_ascii=False),
            after_value=json.dumps(after, ensure_ascii=False),
            rationale="콘솔 PUT /settings/expert-delegation",
            dry_run=False, executed_at=kst_now(),
        ))

    db.commit()
    return _delegation_response(db)


_RETRO_VERDICTS = ("correct", "gray", "wrong", "no_spend")


def _retro_board_rollup(rows: list[NaverRetroSignal], horizon: int) -> dict:
    """단일 보드·단일 지평(d3/d7)의 rollup — PLAN §5: n, correct/gray/wrong/no_spend,
    precision_spenders(=correct/(correct+gray+wrong), no_spend 제외 — 지출 지속 타깃 기준),
    bleed_sum(down/pause & verdict=correct 행의 양수 bleed 합, ref 31 §1-c와 동일 산식)."""
    verdict_attr, bleed_attr = f"verdict_d{horizon}", f"bleed_post{horizon}"
    counts = dict.fromkeys(_RETRO_VERDICTS, 0)
    bleed_sum = 0
    for row in rows:
        verdict = getattr(row, verdict_attr)
        if verdict is None:  # 아직 채점 전(사후창 미도달) — rollup 대상 아님
            continue
        counts[verdict] = counts.get(verdict, 0) + 1
        if row.direction in ("down", "pause") and verdict == "correct":
            bleed_sum += max(0, getattr(row, bleed_attr) or 0)
    spenders = counts["correct"] + counts["gray"] + counts["wrong"]
    precision = round(counts["correct"] / spenders, 4) if spenders else None
    return {
        "n": spenders + counts["no_spend"],
        "correct": counts["correct"], "gray": counts["gray"],
        "wrong": counts["wrong"], "no_spend": counts["no_spend"],
        "precision_spenders": precision, "bleed_sum": bleed_sum,
    }


@router.get("/retro-scorecard")
def retro_scorecard(
    days: int = Query(28, ge=1, le=180, description="조회 창(일, asof_date/alert_date 기준)"),
    db: Session = Depends(get_db),
):
    """상설 소급 채점 성적표(D-NAO-45, PLAN_naver-ad-retro-scoring.md §5) — naver_retro_signal
    (진단 보드 as-of 스냅샷의 d3/d7 방향 정밀도)·naver_retro_pacing_score(trigger_pacing
    경보 채점) 단순 rollup read. 쓰기 없음.

    **정직 경계(ref 31 — 원칙22)**: 이것은 방향 정확도 계기판이지 인과 성과 검증이 아니다
    (인과 승격은 카나리 몫). unparsed pacing 경보는 alert_date가 없어(파싱 실패) 이 창
    필터에서 자연히 빠진다 — 별도 unparsed 총계는 이 엔드포인트 스코프 밖(PLAN §2 OUT)."""
    cutoff = kst_today() - timedelta(days=days)

    signal_rows = db.query(NaverRetroSignal).filter(NaverRetroSignal.asof_date >= cutoff).all()
    by_board: dict[str, list[NaverRetroSignal]] = {}
    for row in signal_rows:
        by_board.setdefault(row.board, []).append(row)
    boards = {
        board: {"d3": _retro_board_rollup(rows, 3), "d7": _retro_board_rollup(rows, 7)}
        for board, rows in by_board.items()
    }

    pacing_rows = (
        db.query(NaverRetroPacingScore)
        .filter(NaverRetroPacingScore.alert_date.isnot(None), NaverRetroPacingScore.alert_date >= cutoff)
        .all()
    )
    pacing: dict[str, dict[str, int]] = {}
    # D-NAO-47: kind×verdict별 final_ratio 평균. ★"저속 경보 769건 correct"는 '경보가
    # 맞았다'까지고, **"평균 최종 소진율 4.9%"라야 "하루가 끝나도 일예산의 4.9%만 썼다 =
    # 만성 저소진이 실재한다"는 증거**가 된다. D-NAO-45의 정정(trigger_pacing은 노이즈가
    # 아니다 → 접지 말고 롤업)의 핵심 숫자라 커맨드 센터 2층이 이걸 표시해야 한다.
    # 데이터는 naver_retro_pacing_score.final_ratio에 이미 있었는데 이 엔드포인트가 안 줬다.
    ratio_acc: dict[str, dict[str, list[float]]] = {}
    for row in pacing_rows:
        kind = row.kind or "unparsed"
        pacing.setdefault(kind, {}).setdefault(row.verdict, 0)
        pacing[kind][row.verdict] += 1
        ratio_acc.setdefault(kind, {}).setdefault(row.verdict, [])
        if row.final_ratio is not None:
            ratio_acc[kind][row.verdict].append(float(row.final_ratio))

    # ★전부 NULL(unparsed 등)이면 평균은 0이 아니라 **None**이다. 0으로 적으면
    # "소진율 0%"라는 거짓 사실이 된다(0과 '알 수 없음'은 다르다).
    pacing_final_ratio = {
        kind: {
            verdict: (round(sum(vals) / len(vals), 4) if vals else None)
            for verdict, vals in by_verdict.items()
        }
        for kind, by_verdict in ratio_acc.items()
    }

    return {
        "window_days": days,
        "boards": boards,
        "pacing": pacing,
        "pacing_final_ratio": pacing_final_ratio,
    }


# ══════════════════════════════════════════════════════════════════
# D-NAO-47 — 변경 이력 조회(change_log) · 커맨드 센터 1층 "우리 조작 N회"의 원천
# ══════════════════════════════════════════════════════════════════
_MAX_CHANGE_LOG_LIMIT = 500


def _loads_or_none(raw: str | None) -> dict | None:
    """change_log의 before/after_value 파싱 — 쓰레기가 들어있어도 500 대신 None.
    (이 테이블은 여러 writer가 각자 dumps 하므로 스키마 보장이 없다.)"""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


@router.get("/change-log")
def get_change_log(
    campaign_id: str | None = Query(None, description="캠페인 필터"),
    action: str | None = Query(None, description="update_bid/external_bid_change/set_user_lock 등"),
    actor: str = Query(
        "all",
        pattern="^(all|ours|external)$",
        description="ours=우리 실집행만 / external=외부 변경 감지만 / all=전부(기본)",
    ),
    days: int = Query(30, ge=1, le=365, description="changed_at 조회 창(KST 기준)"),
    include_dry_run: bool = Query(False, description="dry-run 기록 포함 여부(기본 제외)"),
    limit: int = Query(100, ge=1, le=_MAX_CHANGE_LOG_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """변경 이력 조회(D-NAO-47, 읽기 전용).

    ★`include_dry_run` 기본 False가 의도적이다: 1층 "우리 조작 N회"는 **실제 집행만** 세야
    한다. dry-run을 섞으면 아무것도 실행하지 않았는데 일한 것처럼 보인다(D-47-h 정직성 —
    0이면 0이라고 말하는 게 이 화면의 일).

    ★`actor`도 같은 이유다(codex[P2] 2026-07-17). change_log에는 세 부류가 섞여 있다:
      ① 우리 실집행(EXECUTION_ACTIONS) ② 외부 변경 **감지**(entity_sync가 기록 — MOP·사람이
      바꾼 걸 우리가 관측한 것) ③ 우리 시스템 내부 설정(optimizer_change 등, 광고 API 쓰기 아님).
    prod 실측(2026-07-17): dry_run=False 행 15건이 **전부 ②**이고 우리 실집행은 0건이다.
    필터 없이 total을 세면 1층이 **"우리 조작 15회"**라고 표시한다 — 정확히 반대의 거짓말.
    실행 액션 목록은 harness의 `_ACTION_BY_PROPOSAL_TYPE`에서 파생한다(하드코딩 금지 — 새
    제안 유형이 배선될 때 조용히 어긋난다).

    ⚠️ 이 API는 change_log를 **읽기만** 한다. 이력을 *채우는* 것은 entity_sync의 diff 밸브
    (D-NAO-47 T2)와 naver_execution_harness다.
    """
    since = kst_now() - timedelta(days=days)
    q = db.query(NaverChangeLog).filter(NaverChangeLog.changed_at >= since)
    if campaign_id:
        q = q.filter(NaverChangeLog.campaign_id == campaign_id)
    if action:
        q = q.filter(NaverChangeLog.action == action)
    if actor == "ours":
        q = q.filter(NaverChangeLog.action.in_(naver_execution_harness.EXECUTION_ACTIONS))
    elif actor == "external":
        q = q.filter(NaverChangeLog.action.in_(naver_execution_harness.EXTERNAL_DETECTION_ACTIONS))
    if not include_dry_run:
        q = q.filter(NaverChangeLog.dry_run.is_(False))

    total = q.count()
    rows = q.order_by(NaverChangeLog.changed_at.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "rows": [
            {
                "id": r.id,
                "changed_at": r.changed_at.isoformat() if r.changed_at else None,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "campaign_id": r.campaign_id,
                "action": r.action,
                "before": _loads_or_none(r.before_value),
                "after": _loads_or_none(r.after_value),
                "rationale": r.rationale,
                "outcome": r.outcome,
                "dry_run": r.dry_run,
                "proposal_id": r.proposal_id,
                "executed_at": r.executed_at.isoformat() if r.executed_at else None,
            }
            for r in rows
        ],
    }


# ══════════════════════════════════════════════════════════════════
# D-NAO-47 — 원자료 탐색(3층 ⑨). 수집은 풍부한데 API가 0건이라 볼 방법이 없었다(스펙 §1-4).
# ★limit 상한 200 고정: 키워드 91,005행 · 검색어 114,285행. §9 라이브에서 489행 무페이징이
#   스크롤 27,305px를 만든 전례가 있어 상한을 API가 강제한다(프론트 선의에 맡기지 않는다).
# ══════════════════════════════════════════════════════════════════
_MAX_RAW_LIMIT = 200


@router.get("/raw/keywords")
def get_raw_keywords(
    q: str | None = Query(None, description="키워드 텍스트 부분일치"),
    campaign_id: str | None = Query(None),
    status: str | None = Query(None, description="on/off"),
    include_deleted: bool = Query(False),
    limit: int = Query(50, ge=1, le=_MAX_RAW_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """등록 키워드 원자료(naver_entity의 keyword 행, prod 91,005행) 조회 — 읽기 전용."""
    query = db.query(NaverEntity).filter(NaverEntity.entity_type == "keyword")
    if not include_deleted:
        query = query.filter(NaverEntity.status != "deleted")
    if q:
        query = query.filter(NaverEntity.name.contains(q))
    if campaign_id:
        query = query.filter(NaverEntity.campaign_id == campaign_id)
    if status:
        query = query.filter(NaverEntity.status == status)

    total = query.count()
    rows = query.order_by(NaverEntity.name).offset(offset).limit(limit).all()
    return {
        "total": total,
        "rows": [
            {
                "entity_id": r.entity_id,
                "name": r.name,
                "parent_id": r.parent_id,
                "campaign_id": r.campaign_id,
                "campaign_type": r.campaign_type,
                "status": r.status,
                "bid_amt": r.bid_amt,
                "monthly_volume": r.monthly_volume,
                "competition": r.competition,
                "synced_at": r.synced_at.isoformat() if r.synced_at else None,
            }
            for r in rows
        ],
    }


@router.get("/raw/search-terms")
def get_raw_search_terms(
    q: str | None = Query(None, description="검색어 부분일치"),
    campaign_id: str | None = Query(None),
    days: int = Query(14, ge=1, le=365),
    limit: int = Query(50, ge=1, le=_MAX_RAW_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """검색어 원자료(prod 114,285행, 현재 shopping 소스만) 조회 — 읽기 전용."""
    since = kst_today() - timedelta(days=days)
    query = db.query(NaverSearchTermDaily).filter(NaverSearchTermDaily.ad_date >= since)
    if q:
        query = query.filter(NaverSearchTermDaily.search_term.contains(q))
    if campaign_id:
        query = query.filter(NaverSearchTermDaily.campaign_id == campaign_id)

    total = query.count()
    rows = (
        query.order_by(NaverSearchTermDaily.ad_date.desc(), NaverSearchTermDaily.cost.desc())
        .offset(offset).limit(limit).all()
    )
    return {
        "total": total,
        "rows": [
            {
                "ad_date": r.ad_date.isoformat() if r.ad_date else None,
                "campaign_id": r.campaign_id,
                "adgroup_id": r.adgroup_id,
                "search_term": r.search_term,
                "source": r.source,
                "imp": r.imp,
                "clk": r.clk,
                "cost": r.cost,
            }
            for r in rows
        ],
    }


@router.get("/raw/hourly")
def get_raw_hourly(
    campaign_id: str | None = Query(None),
    days: int = Query(3, ge=1, le=365),
    limit: int = Query(100, ge=1, le=_MAX_RAW_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """시간당 스냅샷 조회 — 읽기 전용.

    ★daily_budget·소진율(spend_ratio)이 화면에 없던 결함(스펙 §1-4)을 여기서 해소한다.
    spend_ratio는 daily_budget이 없거나 0이면 **None**이다 — '소진율 0%'가 아니라
    '알 수 없음'이다. 0으로 나누지 않는다.

    보존기간: D-NAO-46①로 7→365일 연장됨. days 상한 365는 그 상한과 맞춘 것.

    ⚠️ 컬럼명은 `snapshot_hour`다(`hour` 아님 — models.py:1553). 응답 키도 snapshot_hour로
    그대로 노출해 프론트↔DB 이름을 일치시킨다(번역 레이어를 만들지 않는다).
    """
    since = kst_today() - timedelta(days=days)
    query = db.query(NaverHourlySnapshot).filter(NaverHourlySnapshot.ad_date >= since)
    if campaign_id:
        query = query.filter(NaverHourlySnapshot.campaign_id == campaign_id)

    total = query.count()
    rows = (
        query.order_by(NaverHourlySnapshot.ad_date.desc(), NaverHourlySnapshot.snapshot_hour.desc())
        .offset(offset).limit(limit).all()
    )

    def _ratio(cost: int, budget: int | None) -> float | None:
        if not budget:  # None 또는 0
            return None
        return round(cost / budget, 4)

    return {
        "total": total,
        "rows": [
            {
                "ad_date": r.ad_date.isoformat() if r.ad_date else None,
                "snapshot_hour": r.snapshot_hour,
                "snapshot_at": r.snapshot_at.isoformat() if r.snapshot_at else None,
                "campaign_id": r.campaign_id,
                "campaign_type": r.campaign_type,
                "cost": r.cost,
                "clk": r.clk,
                "imp": r.imp,
                "daily_budget": r.daily_budget,
                "spend_ratio": _ratio(r.cost, r.daily_budget),
            }
            for r in rows
        ],
    }
