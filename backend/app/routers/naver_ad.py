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
# POST /api/naver/ad/proposals         — **사람 발의**(D-NAO-283, 계약 P2-ⓒ H2). pending 제안
#   1건 생성 — 승인이 아니다. 여는 유형은 bid_up·bid_down·제외 계열뿐이고(탐색·콜드·서보는
#   엔진 승인원 전용), 구조 검증은 실행기 real_write_blocker를 **재사용**한다(콘솔이 만들게
#   해주는데 실행기가 거부하는 죽은 카드 방지). 봉투 면제 신설 없음.
# GET /api/naver/ad/proposals/proposable-types — 발의 폼용. 열린 유형 + **엔진 전용 유형과
#   그 사유**를 둘 다 준다(조용한 실패 금지 — 「왜 없는지」를 화면이 말해야 한다).
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
# GET /api/naver/ad/modifications     — 「수정 사항」 화면. naver_change_log ∪ naver_agency_op을
#   날짜 구간으로 합쳐 시간순으로 준다(modification_feed 경유·읽기 전용). 주체는 데이터로
#   자동 판정(change_actor 5규칙)하고 기본값은 대행사 — 단 「외부 변경」으로 감지됐어도
#   **우리 실집행과 대조되면 되찾는다**(규칙 ⑤, 근거는 행의 actor_evidence). 날짜 귀속은 **실제 발생 시각 우선**
#   (agency_op.occurred_at) — 감지일로 잡으면 07-30 백필 36건이 07-30에 안 보인다.
# PUT /api/naver/ad/modifications/{source}/{source_id}/actor — 주체 정정. 원천 테이블은
#   건드리지 않고 naver_change_actor_override에만 쌓는다(탐지 산출물 ≠ 사람 주석).
# GET /api/naver/ad/raw/keywords      — 등록 키워드 원자료(prod 91,005행), limit 상한 200 강제.
# GET /api/naver/ad/raw/search-terms  — 검색어 원자료(prod 114,285행), limit 상한 200 강제.
# GET /api/naver/ad/raw/hourly        — 시간당 스냅샷 + daily_budget·spend_ratio(스펙 §1-4의
#   "소진율 미노출" 해소). spend_ratio는 budget 없음/0이면 None(0 나눗셈 금지).
# GET /api/naver/ad/performance/today — 광고 성과(사장님 뷰) ①오늘 한눈에 + ②오늘 시스템이
#   한 일(D-NAO-104 Phase 1). perf_today_harness 경유·읽기 전용. 응답 문자열은 D-NAO-103
#   표기 규칙(ID·내부 용어 금지, 문장)을 통과한 상태로 나간다 — 프론트는 조립하지 않는다.
# GET /api/naver/ad/performance/day       — 위의 날짜 일반화(D-NAO-105). date·campaign_id 선택.
# GET /api/naver/ad/performance/compare   — 기준일 vs 비교일 증감(D-NAO-105, 하루 대 하루).
# GET /api/naver/ad/performance/campaigns — 캠페인 선택기 목록(이름만, ID는 값으로만).
# GET /api/naver/ad/performance/campaign/{id} — ③캠페인 상세(일별 ROAS·기준선·그룹 배지).
# GET /api/naver/ad/performance/budget    — ④예산 소진 곡선·암전 구간·예산 변경 이력.
#   위 5개 전부 perf_today_harness / perf_campaign_harness 경유·**읽기 전용**(계획서 §0-1).
# GET /api/naver/ad/performance/bep-breakdown — ⑤BEP 구성(Phase 3). 상품별 판매가·수수료·원가·
#   물류비 → 공헌이익 → 이익 CPC 상한의 **근거 표**. perf_timeline_harness 경유·읽기 전용.
#   원가 미입력 상품은 추정치로 채우지 않고 "산출 불가"로 나간다(원칙22).
# GET /api/naver/ad/performance/timeline — ⑥개선 타임라인(Phase 3). 트랙 결정 카탈로그 ∪
#   라이브 구조 변경 + 이벤트별 전후 7일. **인과 주장 금지** — 겹친 변경을 전부 표기하고
#   사후 창이 안 찼으면 "관찰 중"으로 말한다(계획서 §3-3).
# GET /api/naver/ad/bm/agency-ops     — BM SA-2 조작 이벤트 온디맨드 드릴다운(D-NAO-79 ③).
# GET /api/naver/ad/bm/snapshot       — BM SA-1 구조 스냅샷 온디맨드 드릴다운(D-NAO-79 ③).
# GET /api/naver/ad/bm/benchmark      — BM SA-3 벤치마크 프라이어 현황 온디맨드 드릴다운
#   (D-NAO-79 ③). 3개 전부 단순 read — 예외 브리핑(주 UX)은 diary/vault/Slack이 맡고, 이
#   라우터는 "필요할 때 열어보는" 전체 리포트(§완료기준: 예외 브리핑=주 UX·이건 온디맨드).
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, tuple_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    NaverAccountSettings,
    NaverAdgroupScope,
    NaverAgencyOp,
    NaverBmBenchmark,
    NaverCampaignSettings,
    NaverChangeActorOverride,
    NaverChangeLog,
    NaverEntity,
    NaverEntitySnapshot,
    NaverExpertReview,
    NaverExpertReviewRun,
    NaverHourlySnapshot,
    NaverLearningState,
    NaverProductBep,
    NaverProposal,
    NaverRetroPacingScore,
    NaverRetroSignal,
    NaverSearchTermDaily,
    NaverSearchTermExclusion,
)
from app.services.naver_ad import adgroup_scope
from app.services.naver_ad import bid_step_types
from app.services.naver_ad import campaign_roster
from app.services.naver_ad import pao_scope_roster
from app.services.naver_ad import guardrail_params
from app.services.naver_ad import search_term_judge
from app.services.naver_ad import change_actor
from app.services.naver_ad import creative_scorecard
from app.services.naver_ad import wisdom_scorecard
from app.services.naver_ad import dashboard_overview
from app.services.naver_ad import delegation_gate
from app.services.naver_ad import exclusion_slot_usage, exclusion_survival, ignition_preflight
from app.services.naver_ad import search_term_exclusion_list
from app.services.naver_ad import search_term_execution
from app.services.naver_ad import search_term_scorecard
from app.services.naver_ad import search_term_ss_lane
from app.services.naver_ad import metrics_aggregator
from app.services.naver_ad import modification_feed
from app.services.naver_ad import naver_execution_harness
from app.services.naver_ad import naver_sa_writer
from app.services.naver_ad import perf_campaign_harness
from app.services.naver_ad import perf_ownership_bands
from app.services.naver_ad import perf_timeline_harness
from app.services.naver_ad import retro_rollup
from app.services.naver_ad import perf_today_harness
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
        # D-NAO-283: 이 행의 자가 «무엇으로» 만들어졌나. 실측/입력/모름이 화면에서 갈린다.
        "commission_basis": r.commission_basis,
        "price_basis": r.price_basis,
        "logistics_basis": r.logistics_basis,
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

# D-NAO-54 P4(결정 전용): 승인해도 harness.execute를 부르지 않는 유형 — 승인=결정 기록만
# (적용은 Jino가 콘솔/설정에서 수동, 금지선 "지혜→실행 직접 쓰기 금지"). 정보성(no-op·자동만료)
# 과도, 실행형(승인→실행)과도 다른 제3의 분기. proposal_writer.PARAM_CHANGE를 단일 진실로
# 삼는다(문자열 하드코딩 금지 — 유형이 늘면 이 집합에만 추가). ★기존 실행형/정보성 흐름은
# 1비트도 바뀌지 않는다: /status(승인)는 애초에 execute를 부르지 않고, param_change는 실행
# 매핑(_ACTION_BY_PROPOSAL_TYPE)이 없어 /execute·real_write_blocker가 자연히 차단한다.
# 이 상수는 프론트에 "결정 전용"임을 알리는 파생 필드(decision_only)의 단일 진실이다.
DECISION_ONLY_PROPOSAL_TYPES: frozenset[str] = frozenset({proposal_writer.PARAM_CHANGE})


def _serialize_proposal(
    p: NaverProposal, verdict: NaverExpertReview | None,
    ent_names: dict[tuple[str, str], str] | None = None,
    camp_names: dict[str, str] | None = None,
    db: Session | None = None,
) -> dict:
    # db를 주면 스코프(D-NAO-244)까지 판정한다 — 엔진 승인분이 스코프 밖이면 콘솔이
    # 「실행 가능」이라고 말하지 않는다(prod 실측 119건이 그렇게 표시되고 있었다).
    blocker_reason = naver_execution_harness.real_write_blocker(p, db)
    return {
        "id": p.id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "proposal_type": p.proposal_type,
        "target_type": p.target_type,
        "target_id": p.target_id,
        "campaign_id": p.campaign_id,
        "adgroup_id": p.adgroup_id,
        # 대상 사람 이름(D-NAO-54, Jino 2026-07-18) — 키워드ID nkw-… 로는 못 알아본다.
        # 맵이 없거나(단건 응답) 미해석이면 None → 프론트가 target_id로 폴백.
        "target_name": (ent_names or {}).get((p.target_type, p.target_id)),
        "campaign_name": (camp_names or {}).get(p.campaign_id),
        # D-NAO-47: 실행 목표값 — 이게 없어서 "입찰 인상" 카드가 *얼마로* 올리는지
        # 화면에 안 나왔다(스펙 §1-6). pending 실행대상 5건이 전부 bid_up이라 바로 체감됨.
        "target_bid": p.target_bid,
        "target_lock": p.target_lock,
        "target_budget": p.target_budget,
        "budget_auto_eligible": p.budget_auto_eligible,
        # D-NAO-47: 정보성/실행형 구분을 백엔드가 준다 — 프론트가 유형 문자열을 하드코딩해
        # 재분류하면 백엔드에 유형이 추가될 때 조용히 드리프트한다.
        "informational": p.proposal_type in proposal_writer.INFORMATIONAL_PROPOSAL_TYPES,
        # D-NAO-54 P4: 결정 전용 유형(param_change) — 승인해도 자동 적용 없음(콘솔 Confirm 문안·
        # 실행버튼 비노출을 프론트가 이 파생값으로 분기, informational/action 파생 패턴과 동일).
        "decision_only": p.proposal_type in DECISION_ONLY_PROPOSAL_TYPES,
        # 실행 액션(add_negative_keyword/update_bid/set_user_lock/update_budget) — 콘솔의
        # 실행 Confirm 문안이 이걸 기준으로 분기한다. 매핑은 harness가 단일 진실
        # (_ACTION_BY_PROPOSAL_TYPE) — 프론트가 유형 문자열로 액션을 재추론해 틀린 액션명을
        # 띄우던 결함을 막는다. 정보성 유형은 매핑에 없어 자연히 None.
        "action": naver_execution_harness._ACTION_BY_PROPOSAL_TYPE.get(p.proposal_type),
        "rationale": p.rationale,
        # GATE R2 P2-1: rank-step TOCTOU 마커([[servo_base_bid=N]])는 기계 원료 — 사람 화면엔 제거.
        "expected_effect": bid_step_types.strip_base_bid_marker(p.expected_effect),
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
    informational: bool | None = Query(
        None,
        description="true=정보성만 / false=실행형만 / 생략=전부. 실행형을 확실히 받으려면 false",
    ),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """제안 카드 목록(콘솔) — naver_proposals 단순 read, 최신순. expert_verdict는 최근 완료
    (status=ok) run의 평결 요약(배지용, E1a T6).

    ★`informational` 필터가 필요한 이유(D-NAO-47, 2026-07-17 prod 배포 검증 중 발견):
    이 목록은 `created_at DESC`인데 정보성 경보(trigger_pacing)가 실행형보다 훨씬 자주
    생성된다. prod 실측 당시 pending 107건 = trigger_pacing 102건(07-16) + bid_up 5건(07-15)
    이라 **limit=100이면 bid_up이 한 건도 안 나왔다**. 받은 페이지를 클라이언트에서
    `!informational`로 거르면 "지금 결정할 제안이 없습니다"가 렌더된다 — 5건이 사람 결정을
    기다리는데. limit을 올리는 건 임시방편이다(정보성이 더 쌓이면 다시 밀려난다).
    **실행형은 실행형으로 질의한다.** 분류 기준은 proposal_writer.INFORMATIONAL_PROPOSAL_TYPES
    (단일 진실 — 프론트가 유형 문자열로 재분류하면 드리프트한다)."""
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
    if informational is True:
        q = q.filter(NaverProposal.proposal_type.in_(proposal_writer.INFORMATIONAL_PROPOSAL_TYPES))
    elif informational is False:
        q = q.filter(NaverProposal.proposal_type.notin_(proposal_writer.INFORMATIONAL_PROPOSAL_TYPES))
    if date_from:
        q = q.filter(NaverProposal.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.filter(NaverProposal.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    # D-NAO-47: total은 limit과 무관한 전체 건수. 페이지 길이를 건수로 쓰면 limit에 따라
    # 달라지는 틀린 숫자가 된다(정보성 "N건 집계됨" 등). rows는 additive라 기존 소비자 불변.
    total = q.count()
    rows = q.order_by(NaverProposal.created_at.desc()).limit(limit).all()
    verdicts = _latest_ok_verdicts_by_proposal(db, [p.id for p in rows])
    # 대상 사람 이름 배치 해석(D-NAO-54, Jino 2026-07-18) — 키워드ID로는 못 알아본다.
    ent_names, camp_names = _batch_entity_names(
        db,
        {(p.target_type, p.target_id) for p in rows if p.target_type and p.target_id},
        {p.campaign_id for p in rows if p.campaign_id},
    )
    # 현재 실쓰기 개방된 액션 목록(배너 표시용) — 코드 배포로만 바뀌는 이중 방벽 교집합.
    # 하드코딩 라벨("현재 개방: 제외키워드")이 개방 순서 진행과 어긋나던 결함 재발 방지.
    return {
        "total": total,
        "open_actions": naver_execution_harness.open_executable_actions(),
        "rows": [_serialize_proposal(p, verdicts.get(p.id), ent_names, camp_names, db) for p in rows],
    }


# ══════════════════════════════════════════════════════════════════
# D-NAO-283 (계약 P2-ⓒ · H2) — 사람 발의 제안 입구
# ══════════════════════════════════════════════════════════════════
# ★이 입구가 «새로 만드는» 것은 제안 1건뿐이다. 그 뒤는 기존 경로 그대로다:
#   발의(pending) → 사람 승인(POST /proposals/{id}/status) → 실행(POST /proposals/{id}/execute)
#   → harness.execute()의 D-NAO-13 하드체크·가드레일 봉투.
# **봉투 면제를 신설하지 않는다**(계약 §3 P2·§5 금지선). 큰 폭이 필요하면 스텝을 나눈다.
#
# ★검증은 «실행기의 판정을 재사용»한다(교훈 #380 — 두 층이 각자 초록이던 그 자리).
#   필수 필드·구조 조건을 여기 다시 적으면 실행기와 갈라져 「콘솔은 만들게 해주는데
#   실행은 거부하는」 죽은 카드가 태어난다(prod 실측 133건이 그 모양이었다).
#   그래서 미저장(transient) NaverProposal을 만들어 real_write_blocker에 그대로 물어본다.


class ProposalCreateIn(BaseModel):
    """사람 발의 제안 1건. 필드 의미는 NaverProposal과 1:1 — 새 어휘를 만들지 않는다."""

    proposal_type: str
    target_type: str
    target_id: str
    campaign_id: str
    adgroup_id: str | None = None
    rationale: str                      # ★필수 — 근거 없는 발의는 학습 사슬에서 유령이 된다
    expected_effect: str | None = None
    target_bid: int | None = None       # bid_up/bid_down
    proposed_by: str | None = None      # 발의 주체 메모(옵션) — 미지정 시 "console"


@router.get("/proposals/proposable-types")
def proposal_proposable_types():
    """발의 폼이 그릴 유형 목록 — **열린 것과 엔진 전용을 «둘 다»** 준다(계약 §3 P2 ★v9).

    ★엔진 전용을 «빼서» 주지 않는 이유: 빼면 화면이 「그 유형이 없다」고만 말하고 사람은
    왜 없는지 모른다 — 계약이 명시적으로 금지한 조용한 실패다. 사유 문구도 백엔드가 낸다
    (프론트가 유형 문자열로 사유를 재추론하면 게이트가 바뀔 때 화면만 옛말을 한다 —
    `action`·`informational` 파생과 같은 관례).
    """
    proposable = naver_execution_harness.human_proposable_types()
    engine_only = [
        {"proposal_type": t, "reason": naver_execution_harness.human_proposal_blocker(t)}
        for t in sorted(naver_execution_harness._ACTION_BY_PROPOSAL_TYPE)
        if t not in proposable
    ]
    return {
        "proposable": [
            {
                "proposal_type": t,
                "action": naver_execution_harness._ACTION_BY_PROPOSAL_TYPE.get(t),
                "direction": bid_step_types.direction_of(t),
            }
            for t in proposable
        ],
        "engine_only": engine_only,
        "open_actions": naver_execution_harness.open_executable_actions(),
    }


@router.post("/proposals")
def proposal_create(body: ProposalCreateIn, db: Session = Depends(get_db)):
    """사람이 콘솔에서 제안을 발의한다(D-NAO-283 · 계약 P2-ⓒ H2).

    생성되는 것은 `status='pending'` 제안 1건이다 — **승인이 아니다.** `approval_source`는
    비워 둔다(그 컬럼의 뜻은 「승인 출처」이고, 승인은 아직 일어나지 않았다. 여기서 채우면
    감사상 「발의=승인」으로 읽힌다).

    거부 순서(전부 fail-closed):
    ① 유형이 사람 발의 대상이 아님 → 400 + 「이 유형은 엔진만 발의합니다 + 사유」
    ② 캠페인이 `optimizer='ours'`가 아님 → 409 (D-NAO-13. 실행 직전에도 재검증되지만,
       그때 거부하면 죽은 카드가 남으므로 입구에서 같은 답을 낸다 — 이중 방벽이지 우회 아님)
    ③ 실행기가 그 제안을 실행 못 할 구조 → 400 + **실행기가 낸 사유 그대로**
    """
    blocker = naver_execution_harness.human_proposal_blocker(body.proposal_type)
    if blocker is not None:
        raise HTTPException(400, blocker)

    optimizer = naver_execution_harness._resolve_optimizer(db, body.campaign_id)
    if optimizer != "ours":
        raise HTTPException(
            409,
            f"캠페인 {body.campaign_id}의 optimizer={optimizer!r} — 'ours'가 아닌 캠페인엔 "
            "쓰기 금지(D-NAO-13). 발의해도 실행 단계에서 거부되므로 입구에서 막는다.",
        )

    # ★미저장 제안으로 실행기에 먼저 물어본다 — 구조 판정을 여기 복제하지 않기 위함이다.
    #   (real_write_blocker는 순수 판정 함수이고 DB를 건드리지 않는다 — 그 계약에 기댄다.)
    # ★★`approval_source="console"`을 «판정용으로만» 실어 묻는다. 이 발의가 승인되면
    #   `/proposals/{id}/status`가 정확히 그 값을 박고, real_write_blocker의 스코프 판정
    #   (D-NAO-244)은 `approval_source is not None`일 때만 발동하기 때문이다. None으로 물으면
    #   스코프 밖 그룹이 입구를 통과하고, 승인 뒤 실행에서 ScopeGuardError로 죽는다 —
    #   그게 바로 이 입구가 만들지 않으려는 죽은 카드다. **묻는 값과 저장하는 값이 다르므로
    #   아래에서 반드시 되돌린다**(회귀 테스트가 저장된 행의 approval_source is None을 고정).
    proposed_by = (body.proposed_by or "console").strip() or "console"
    candidate = NaverProposal(
        proposal_type=body.proposal_type,
        target_type=body.target_type,
        target_id=body.target_id,
        campaign_id=body.campaign_id,
        adgroup_id=body.adgroup_id,
        rationale=f"[사람 발의: {proposed_by}] {body.rationale}",
        expected_effect=body.expected_effect,
        target_bid=body.target_bid,
        status="pending",
        approval_source="console",  # ← 판정용. 저장 직전 None으로 되돌린다(바로 아래).
    )
    structural = naver_execution_harness.real_write_blocker(candidate, db)
    if structural is not None:
        raise HTTPException(400, f"실행 불가 구조라 발의를 거부한다 — {structural}")

    # 발의는 승인이 아니다 — 「승인 출처」 컬럼은 비운 채로 저장한다(위 ★★).
    candidate.approval_source = None
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    log.info(
        "naver_ad 사람 발의: proposal_id=%s type=%s campaign=%s target=%s/%s by=%s",
        candidate.id, candidate.proposal_type, candidate.campaign_id,
        candidate.target_type, candidate.target_id, proposed_by,
    )
    return _serialize_proposal(candidate, None, db=db)


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
    # ★D-NAO-248 §4-B(B1) — param_change 승인 시 반영할 값. **사람이 정한다**: 코드가 값을
    # 발명하면 그게 새 상수 발명이다(금지선). param_change가 아닌 제안·반려에는 쓰이지 않는다.
    applied_value: Any | None = None
    decided_by: str | None = None      # 결정 주체(옵션) — 미지정 시 "console"
    decision_note: str | None = None   # 결정 근거 메모(옵션) — 미지정 시 자동 생성 문구


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

    ★D-NAO-248 §4-B(B1) — 「승인=적용」 사슬: `proposal_type=='param_change'`가 `approved`로
    가는 전이는 `guardrail_params.apply_params()`를 같은 트랜잭션으로 호출해 봉투 파라미터를
    실제로 반영한다. **여전히 자동 적용이 아니다** — 트리거는 이 라우터를 호출하는 사람의
    승인 행위이고, 반영될 값(`applied_value`)도 사람이 정한다(D-NAO-249 확정). 검증(값 존재·
    SPECS 키 식별)은 상태 전이 «전에» 끝내고, 상태 전이 자체가 실패(409)하거나 `apply_params`가
    실패(400, 봉투 밖 값 등)하면 **상태 전이도 되돌린다**(승인됐는데 반영 안 된 상태 방지) —
    이 함수 안에서 커밋을 한 번만 한다. `param_change`가 아닌 제안의 승인·반려 동작은
    1비트도 바뀌지 않는다(회귀 테스트로 고정).
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

    is_param_change = proposal.proposal_type == proposal_writer.PARAM_CHANGE

    # ★값 검증은 DB에 아무것도 쓰기 «전에» 끝낸다 — 가장 단순한 형태의 "실패 시 아무 전이도
    # 없다"(되돌릴 게 없으면 되돌릴 필요도 없다).
    spec_key: str | None = None
    if is_param_change and target == "approved":
        if body.applied_value is None:
            raise HTTPException(
                400,
                "param_change 승인은 applied_value가 필요합니다 — 적용할 값은 사람이 정합니다"
                "(코드가 값을 발명하지 않습니다).",
            )
        if proposal.target_type != guardrail_params.TARGET_TYPE:
            raise HTTPException(
                400,
                f"이 제안은 봉투 파라미터를 식별할 수 없습니다(target_type={proposal.target_type!r}"
                f", 기대값={guardrail_params.TARGET_TYPE!r})",
            )
        if proposal.target_id not in guardrail_params.SPECS:
            raise HTTPException(
                400,
                f"이 제안이 지목한 파라미터 키를 모릅니다: target_id={proposal.target_id!r} "
                f"(허용: {sorted(guardrail_params.SPECS)})",
            )
        spec_key = proposal.target_id

    q = db.query(NaverProposal).filter(
        NaverProposal.id == proposal_id,
        NaverProposal.status == current,
    )
    if (current, target) == ("approved", "rejected"):
        q = q.filter(NaverProposal.executed_change_log_id.is_(None))
    # target=='approved'인 전이는 사람이 이 콘솔 라우터를 직접 호출한 것 — approval_source=
    # 'console'로 감사 기록(X1a T5, delegation_gate의 'delegation'과 대칭). rejected 전이는
    # approval_source를 건드리지 않는다(이력 보존 — 반려됐던 승인의 출처도 남겨둔다).
    values: dict[str, Any] = {"status": target}
    if target == "approved":
        values["approval_source"] = "console"
    if is_param_change:
        # ★A7 표면(wisdom_scorecard._proposal_decision)이 「기록 없음(컬럼 신설 전)」 폴백 대신
        # 실제 결정 메타를 보게 한다. **param_change만** 채운다 — 다른 유형의 승인·반려는
        # 완전히 그대로(회귀 테스트로 고정, 이 필드들은 결과 JSON에 노출되지 않아 그 회귀에
        # 영향이 없다).
        now = kst_now()
        values["decided_at"] = now
        values["decided_by"] = body.decided_by or "console"
        if body.decision_note:
            values["decision_note"] = body.decision_note
        elif target == "approved":
            values["decision_note"] = f"승인 — {spec_key}={body.applied_value!r} 반영"
        else:
            values["decision_note"] = "반려"
    rowcount = q.update(values, synchronize_session=False)
    if rowcount != 1:
        db.rollback()
        raise HTTPException(409, "상태가 변경됨 — 새로고침 후 재시도")

    if spec_key is not None:  # is_param_change and target=='approved'이고 값 검증도 끝난 경우만
        try:
            result = guardrail_params.apply_params(
                db, {spec_key: body.applied_value},
                rationale=f"콘솔 승인 — 제안 #{proposal_id} 반영(D-NAO-248 §4-B)",
                proposal_id=proposal_id,
                # ★merge=True — 승인은 «제안 한 건»의 맥락이라 그 키만 바꾼다. PUT의 전체
                # 치환(사람이 화면 전체를 보고 저장하는 맥락)을 여기 쓰면 사람이 따로
                # 설정해 둔 다른 키가 조용히 코드 기본값으로 되돌아간다.
                merge=True,
            )
        except guardrail_params.InvalidGuardrailParams as e:
            db.rollback()
            raise HTTPException(400, str(e))
        if result["change_log_id"] is not None:
            # B0이 반환한 change_log 행 id를 심는다 — B4의 「제안→change_log」 조인이
            # 이 컬럼을 그대로 읽는다(wisdom_scorecard._change_rows_for).
            db.query(NaverProposal).filter(NaverProposal.id == proposal_id).update(
                {"executed_change_log_id": result["change_log_id"]}, synchronize_session=False,
            )
        # 무변화(이미 같은 값 재승인)면 change_log_id=None — executed_change_log_id도 그대로
        # 둔다. 「적용 시도는 됐지만 변경은 없었다」를 change_log 부재로 정직하게 나타낸다.

    db.commit()

    db.refresh(proposal)
    verdicts = _latest_ok_verdicts_by_proposal(db, [proposal.id])
    return _serialize_proposal(proposal, verdicts.get(proposal.id), db=db)


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
        "proposal": _serialize_proposal(proposal, verdicts.get(proposal.id), db=db),
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


@router.get("/wisdom-scorecard")
def wisdom_scorecard_get(
    wisdom_id: int | None = Query(None, description="특정 지혜 1건만 조회(미지정=전건)"),
    db: Session = Depends(get_db),
):
    """지혜 성적표(M3-a, 계약 PLAN_naver-m3-wisdom-scorecard.md §4-A① · §4-B⑥) —
    승격 지혜 id마다 «그 지혜가 낳은 제안 → 조치»를 잇고 총이익(outcome_profit)·GAVE·
    BEP 렌즈(bep_source)를 롤업한다. 쓰기 없음.

    **정직 경계**: 귀속 경로는 `param_proposal_id` 1:1 링크뿐이다 — 지혜를 자유 텍스트로
    브리핑에 주입하는 경로(`wisdom_apply.active_wisdom_prefix`)는 id를 남기지 않으므로
    이 롤업은 지혜 기여의 «하한»이다. 응답의 `attribution.limitation`이 그 사실을 실어 나른다.
    **표본 0을 «좋은 성적»으로 읽지 말 것** — 행마다 `has_evidence`·`evidence_gap`이 붙는다.

    ★`response_model`을 두지 않는다: 스키마가 키를 지워 판정면이 통째로 사라지는 사고가
    이 저장소에 반복됐다(교훈 #321 — schemas.py에 같은 경고 주석 4개). 관측은 HTTP body로.
    """
    return wisdom_scorecard.build(db, wisdom_id=wisdom_id)


_VALID_OPTIMIZERS = {"none", "ours", "mop"}
_VALID_MODES = {"growth", "recovery", "launch", "defense"}


class CampaignSettingsIn(BaseModel):
    """모드·공격성·override·memo 전용(D-NAO-48).

    ★`optimizer`를 **받지 않는다**(extra='forbid'로 422). 관리주체의 유일한 쓰기 경로는
    `PUT /campaign-settings/optimizer`다.
    왜 optional이 아니라 아예 거부인가(codex[P2] R2 — 내 하위호환 절충을 스스로 접음):
      ① 이 PUT이 optimizer를 쓰면 1층 스위치의 **확인창(원본 MOP가 자동으로 안 꺼진다는
         D-NAO-13 경고)을 우회**해 캠페인이 라이브 쓰기 대상이 된다.
      ② stale 편집 버퍼가 나중에 커밋되면 스위치로 끈 걸 'ours'로 되돌려 **아무도 의도하지
         않은 채 쓰기 게이트가 재무장**된다.
    optional로 두고 "안 보내면 된다"고 문서로 막는 건 약하다 — 문을 열어두면 언젠가 들어온다.
    돈이 걸린 게이트라 구조로 막는다(reason을 타입으로, EXECUTION_ACTIONS를 파생으로 막은 것과 같은 원칙).
    """

    model_config = {"extra": "forbid"}

    campaign_id: str
    mode: str | None = None
    target_roas_override: float | None = None
    gamma: float | None = None
    memo: str | None = None


def _serialize_settings(s: NaverCampaignSettings) -> dict:
    return {
        "campaign_id": s.campaign_id,
        "optimizer": s.optimizer,
        # ★H1(P2): `auto_operate`를 응답에 싣는다 — 종전엔 이 직렬화기가 optimizer만 실어서
        #   **캠페인 설정 API로는 킬스위치의 현재값을 아예 볼 수 없었다**(화면 배지는 별도
        #   로스터에서 읽는다). 끄고 켜는 손을 다는 마당에 「지금 켜져 있나」를 같은 응답에서
        #   못 읽으면, 누른 뒤 결과를 확인할 표면이 갈라진다. additive라 기존 소비처 무영향.
        "auto_operate": bool(s.auto_operate),
        "mode": s.mode,
        "target_roas_override": _num(s.target_roas_override),
        "gamma": _num(s.gamma),
        "memo": s.memo,
        # UI1(D-NAO-65): loss 대응 정책. NULL은 콘솔에서 기본값 'leash'(고삐)로 해석.
        "loss_policy": s.loss_policy,
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
    if body.mode is not None and body.mode not in _VALID_MODES:
        raise HTTPException(400, f"mode는 {sorted(_VALID_MODES)} 중 하나여야 합니다")

    settings = db.query(NaverCampaignSettings).filter(
        NaverCampaignSettings.campaign_id == body.campaign_id
    ).first()
    # ★optimizer는 건드리지 않는다(D-NAO-48). 신규 행은 'none'(자동화 안 함)에서 시작 —
    # 모드만 저장했는데 라이브 쓰기가 켜지면 안 된다.
    if settings is None:
        settings = NaverCampaignSettings(campaign_id=body.campaign_id, optimizer="none")
        db.add(settings)
    settings.mode = body.mode
    settings.target_roas_override = (
        Decimal(str(body.target_roas_override)) if body.target_roas_override is not None else None
    )
    settings.gamma = (
        Decimal(str(body.gamma)) if body.gamma is not None else None
    )
    settings.memo = body.memo

    db.commit()
    db.refresh(settings)
    return _serialize_settings(settings)


class OptimizerSwitchIn(BaseModel):
    campaign_id: str
    optimizer: str


@router.put("/campaign-settings/optimizer")
def campaign_optimizer_switch(body: OptimizerSwitchIn, db: Session = Depends(get_db)):
    """관리주체만 바꾼다(D-NAO-48). 1층 캠페인 스위치의 쓰기 경로.

    ★왜 PUT /campaign-settings를 쓰지 않는가: 그건 **전체 치환**이라
    (`settings.mode = body.mode`를 무조건 대입) optimizer만 보내면 mode·target_roas_override·
    gamma·memo가 전부 null로 날아간다. 콘솔은 memo를 되돌려보내 겨우 막고 있고
    (NaverAdOptimizationConsole.tsx:344 주석), **gamma는 api.ts에 파라미터조차 없어 콘솔이
    저장할 때마다 조용히 지워진다**(스펙 §1-6이 지적한 괴리의 실제 결과 — 현재 gamma가 전부
    NULL이라 안 보일 뿐). "필드 하나 바꾸는 동작"에 전체 치환을 쓰는 게 애초에 틀렸다.
    이 엔드포인트는 optimizer 외 필드를 **건드리지 않아** 그 실수를 구조적으로 불가능하게 한다.

    ⚠️ 이건 우리 시스템 내부 설정이지 광고 API 쓰기가 아니다(D-NAO-13). 실제 실행 게이트는
    naver_execution_harness의 optimizer=='ours' 하드체크(:912)이고 이중 방어는 불변이다.
    ⚠️ 'ours'로 바꿔도 **원본 MOP는 꺼지지 않는다** — 우리 프로그램은 MOP를 끌 수 없다(별도
    SaaS). Jino가 MOP 콘솔에서 직접 꺼야 하며, 안 끄면 두 옵티마이저가 같은 캠페인 입찰을
    두고 충돌한다. 그 경고는 프론트 확인창이 띄운다(D-48-b). 충돌이 실제로 나면
    entity_sync의 external_bid_change/external_status_change 감지로 드러난다(D-NAO-47 밸브).
    """
    if body.optimizer not in _VALID_OPTIMIZERS:
        raise HTTPException(422, f"optimizer는 {sorted(_VALID_OPTIMIZERS)} 중 하나여야 합니다")

    settings = db.query(NaverCampaignSettings).filter(
        NaverCampaignSettings.campaign_id == body.campaign_id
    ).first()
    before_optimizer = settings.optimizer if settings else "none"

    # ★켜기 선행 검사 (S6-b) — **쓰기 «전»에** 재고, 응답에 실어 보낸다. 차단하지 않는다:
    #   켜는 결정은 Jino의 것이고 여기서 막으면 새 게이트를 세우는 것이다(전역 §1).
    #   끄는 방향('none')엔 경고를 달지 않는다 — 닫는 데 안전 경고는 소음이다.
    preflight = (
        ignition_preflight.check(db, body.campaign_id)
        if body.optimizer != "none" else None
    )

    if settings is None:
        # 없던 행은 optimizer만 세팅 — mode 등 기본값을 임의로 지어내지 않는다.
        settings = NaverCampaignSettings(campaign_id=body.campaign_id, optimizer=body.optimizer)
        db.add(settings)
    else:
        settings.optimizer = body.optimizer  # ★다른 필드는 손대지 않는다

    if before_optimizer != body.optimizer:
        db.add(NaverChangeLog(
            entity_type="campaign", entity_id=body.campaign_id, campaign_id=body.campaign_id,
            action="optimizer_change",
            before_value=before_optimizer, after_value=body.optimizer,
            rationale="커맨드 센터 관리주체 스위치(D-NAO-48)",
            # ★changed_at 명시(D-NAO-54): 안 넘기면 models.py의 server_default=func.now()가
            #   먹어 **UTC**로 박힌다(실측 drift 9.0h). 이 테이블의 다른 모든 writer는
            #   kst_now()를 명시 전달하는데 이 라우터의 두 writer만 빠져 있었다 →
            #   00:00~09:00 KST 조작이 전날로 귀속됐다(D-NAO-54 날짜 창이 이걸 처음 가시화).
            #   memory: sqlite-server-default-now-is-utc — 같은 함정 세 번째.
            changed_at=kst_now(),
        ))

    db.commit()
    db.refresh(settings)
    out = _serialize_settings(settings)
    # ★키를 «항상» 싣지 않는다 — 켜는 요청에만 붙인다. 다만 붙일 땐 경고 0건이어도 붙여서
    #   「검사를 안 했다」와 「검사했는데 깨끗하다」가 같아 보이지 않게 한다(교훈 #123).
    if preflight is not None:
        out["ignition_preflight"] = preflight
    return out


class AutoOperateSwitchIn(BaseModel):
    """킬스위치(`auto_operate`)만 바꾼다 — optimizer 스위치와 동형.

    ★`optimizer`를 **받지 않는다**(extra='forbid'로 422). 두 스위치는 «층이 다르다»:
    optimizer = 「이 캠페인의 관리주체가 누구인가」, auto_operate = 「그 주체의 자동 레인이
    지금 도는가」. 한 요청으로 둘을 바꿀 수 있게 하면 «켜는 결정»과 «맡기는 결정»이 한
    클릭에 묶여, 어느 쪽을 의도했는지 감사 로그에서 갈라낼 수 없다.
    """

    model_config = {"extra": "forbid"}

    campaign_id: str
    auto_operate: bool


@router.put("/campaign-settings/auto-operate")
def campaign_auto_operate_switch(body: AutoOperateSwitchIn, db: Session = Depends(get_db)):
    """킬스위치 쓰기 경로(H1 · 계약 P2 첫째). **이 저장소 최초의 `auto_operate` 쓰기 API다.**

    ★왜 이제야 생기나: 종전엔 라우터에 쓰기가 **전무**했고(`ignition_preflight` 모듈 머리주석이
    2026-08-27 전수 확인으로 기록), 점화는 **prod DB 직접 UPDATE**였다. 그래서 ①감사 행이 앱
    코드 밖에서 생겨 `improvement_events`가 「writer가 없는데 prod엔 행이 있다」를 주석으로
    남겨야 했고 ②끄는 손이 사람 손에 없어, 제외 재개방이 **10일째 밀려도 아무도 못 열었다**
    (2026-08-31 실측: due 1건 — `next_review_at=2026-08-21`, 그 캠페인 `auto_operate=0`).

    ⚠️★**이 스위치는 optimizer 스위치보다 «무겁다» — 같은 문장으로 안심시키면 안 된다.**
    optimizer 스위치 독스트링은 *"우리 시스템 내부 설정이지 광고 API 쓰기가 아니다"*라고
    적을 수 있다. 실행 harness가 `optimizer=='ours'`를 하드체크(D-NAO-13, :2564)하기 때문이다.
    그런데 **제외 재개방 레인은 그 harness를 안 탄다** — `_open_exclusion`
    (`search_term_ss_lane.py`) 독스트링이 스스로 *"harness.execute()를 안 거치고
    naver_sa_writer를 직접 부르는 예외 경로"*라고 밝힌다. 그 경로의 게이트는 셋뿐이다:
    일일 복귀 캡 · `_auto_operate_now`(=이 플래그) · `blocked_by_scope`.
    ⇒ **`optimizer='none'`인 캠페인이라도 이 플래그를 켜면 다음 08:50 레인이 네이버에서
    제외키워드를 실제로 삭제한다.** 「내부 설정」이 아니라 **외부 쓰기의 방아쇠**다.
    그래서 켜는 요청엔 `ignition_preflight`를 반드시 실어 보낸다(경고 0건이어도 — 교훈 #123).

    ★차단하지 않는다(전역 §1 — 새 게이트를 세우지 않는다). 경고를 붙여 돌려줄 뿐이고,
    켜는 결정은 사람의 것이다. 끄는 방향엔 경고를 달지 않는다 — 닫는 데 안전 경고는 소음이다.
    """
    settings = (
        db.query(NaverCampaignSettings)
        .filter(NaverCampaignSettings.campaign_id == body.campaign_id)
        .first()
    )
    # ★행 부재의 뜻은 `_auto_operate_now`가 이미 정해 뒀다 — **행이 없으면 False**(fail-closed).
    #   그러니 before는 「모름」이 아니라 「꺼짐」이다. 여기서 다르게 읽으면 감사 로그의
    #   before_value가 엔진의 실제 판정과 어긋난다.
    before = bool(settings.auto_operate) if settings else False

    # ★켜기 «전»에 재고 응답에 싣는다(optimizer 스위치와 같은 관례·같은 판정기).
    # ⚠️적대 리뷰 P2 기록: 그래서 `out["ignition_preflight"]["auto_operate"]`는 **켜기 «전» 값**
    #   (=False)이고 같은 응답 최상위의 `out["auto_operate"]`는 **켠 «뒤» 값**(=True)이다. 한
    #   응답에 같은 이름 두 값이라 헷갈릴 수 있어 적어 둔다. 지금 프론트는 preflight의
    #   `warnings`·`safe_to_ignite`만 읽으므로 무해하고, 검사는 「켜기 전 상태에 대한 판정」이
    #   맞으므로 값을 사후로 바꾸지 않는다 — 바꾸면 「무엇을 보고 경고했나」가 어긋난다.
    preflight = ignition_preflight.check(db, body.campaign_id) if body.auto_operate else None

    if settings is None:
        # 없던 행은 이 플래그만 세팅 — optimizer·mode 기본값을 임의로 지어내지 않는다.
        # (모델 기본값 optimizer=None/'none'이 그대로 남아, 켜도 harness 경로는 안 열린다.
        #  단 위 ⚠️의 재개방 경로는 열린다 — 그 비대칭이 preflight 경고의 대상이다.)
        settings = NaverCampaignSettings(
            campaign_id=body.campaign_id, auto_operate=body.auto_operate
        )
        db.add(settings)
    else:
        settings.auto_operate = body.auto_operate  # ★다른 필드는 손대지 않는다

    if before != body.auto_operate:
        db.add(NaverChangeLog(
            entity_type="campaign", entity_id=body.campaign_id, campaign_id=body.campaign_id,
            action="auto_operate_change",
            # ★"1"/"0"이 아니라 "on"/"off"로 적는다 — 이 원장의 before/after는 사람이 읽는
            #   자리이고, optimizer_change가 'none'/'ours'라는 «말»을 적는 것과 결을 맞춘다.
            before_value="on" if before else "off",
            after_value="on" if body.auto_operate else "off",
            rationale="커맨드 센터 킬스위치(H1 · 계약 P2)",
            # ★changed_at 명시: 안 넘기면 server_default=func.now()가 먹어 **UTC**로 박힌다
            #   (memory: sqlite-server-default-now-is-utc — 이 라우터에서 같은 함정 네 번째).
            changed_at=kst_now(),
        ))

    db.commit()
    db.refresh(settings)
    out = _serialize_settings(settings)
    if preflight is not None:
        out["ignition_preflight"] = preflight
    return out


@router.get("/campaign-settings/ignition-preflight")
def campaign_ignition_preflight(
    campaign_id: str = Query(..., description="검사할 캠페인 id"),
    db: Session = Depends(get_db),
) -> dict:
    """켜기 선행 검사(읽기 전용·차단 0, S6-b).

    ★이 독스트링은 **2026-08-31에 반증됐다** — 「`auto_operate`를 켜는 API 경로는 존재하지
    않는다(점화는 직접 UPDATE다)」라고 적혀 있었는데, 같은 날 H1이 바로 위에
    `PUT /campaign-settings/auto-operate`를 만들었다. 사실을 갱신해 둔다: **켜는 경로는 있다.**
    그래도 이 창구는 남는다 — 켜기 «전에» 「지금 켜면 무엇이 열리는가」를 물어볼 수 있어야
    검사가 정작 켜는 순간에 쓰이고, 켜기 응답에도 같은 판정기가 실린다(한 사실, 한 판정기,
    세 표면). ★고친 이유: 낡은 정본은 다음 세션이 **없는 것을 다시 만들게** 한다."""
    return ignition_preflight.check(db, campaign_id)


# ── PAO 스코프 (D-NAO-244) — 「어떤 캠페인·광고그룹을 돌릴지 + 그 성과」 ──────────────
#
# Jino 원문 2026-08-24: *"ohisell에 PAO 메뉴를 만들어서 어떤 캠페인 - 광고그룹 을 돌릴지,
# 그 성과는 어떻게 나오는지 보여주는 대시보드를 같이 만들자"*
#
# ⚠️ 여기 쓰기는 **우리 시스템 내부 설정**이지 광고 API 쓰기가 아니다(campaign-settings와
#    같은 경계). 스코프 행을 넣어도 auto_operate가 꺼져 있으면 아무 일도 일어나지 않는다.


@router.get("/scope/roster")
def pao_scope_roster_get(
    campaign_id: str | None = None,
    days: int = pao_scope_roster.DEFAULT_WINDOW_DAYS,
    # ★날짜 구간(가산). 화면이 날짜를 직접 고를 수 있게 되면서 필요해졌다 — 서버가 `days`만
    #   받으면 «고른 날짜»와 «실제 조회 창»이 갈라지고, 사용자는 자기가 고른 구간을 봤다고
    #   믿는다. 안 주면 종전 `days` 경로 그대로다(기존 소비처 불변).
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: Session = Depends(get_db),
):
    """PAO 스코프 대시보드 — 캠페인 × 광고그룹 횡단 로스터(읽기 전용).

    각 광고그룹에 ①스코프(in_scope·역할·enabled) ②성과(광고비·클릭·전환·ROAS)
    ③**총이익**(D-NAO-59 목적함수)을 함께 싣는다. 총이익은 BEP를 해석 못 하면
    `gross_profit=null` + `profit_status='bep_unknown'` — **0원과 «모름»을 구분한다**
    (숫자를 지어내면 그 숫자가 그대로 판정에 쓰인다).
    """
    return pao_scope_roster.build_roster(
        db, campaign_id=campaign_id, days=days,
        date_from_in=date_from, date_to_in=date_to,
    )


class AdgroupScopeIn(BaseModel):
    """스코프 행 1개 upsert. optimizer 스위치와 동형 — 이 행만 건드리고 캠페인 설정은
    손대지 않는다(extra='forbid'로 오필드 422)."""

    model_config = {"extra": "forbid"}

    campaign_id: str
    adgroup_id: str
    role: str | None = None
    enabled: bool = True
    memo: str | None = None


@router.put("/scope/adgroup")
def pao_scope_adgroup_put(body: AdgroupScopeIn, db: Session = Depends(get_db)):
    """스코프 행 upsert — 「이 광고그룹을 엔진에 맡긴다/뺀다」.

    ★이 엔드포인트는 **엔진을 켜지 않는다.** 스코프는 캠페인 마스터(auto_operate) «아래»의
    축이라, 캠페인이 꺼져 있으면 행을 넣어도 실행은 0이다. 켜는 것은 별도 결정이다.

    ⚠️ 첫 행이 들어가는 순간 그 캠페인은 「일부 그룹만 맡긴 상태」가 되어 **캠페인 레벨
    액션(예산)이 hold**된다 — 예산은 광고그룹으로 귀속이 불가능해 열어두면 스코프 «밖»
    그룹의 노출까지 같이 움직이기 때문이다.

    ★H5(계약 P2)에서 일괄 경로가 생기면서 **upsert·감사 규칙을 `adgroup_scope`로 뽑았다** —
    단건과 일괄이 같은 `action`을 서로 다른 관례로 적으면 원장을 읽는 쪽이 두 규칙을
    재현해야 한다. 그 과정에서 종전 동작 두 가지가 바뀌었다(의도된 변경):
      ① `before_value`가 항상 `None`이던 것 → **실제 이전 상태**(행이 없었으면 그대로 None).
      ② 값이 그대로인 PUT도 감사 줄을 쓰던 것 → **바뀐 행만** 쓴다. 일괄에서 이게 없으면
         버튼 한 번에 no-op 수십 줄이 원장을 덮어 「무엇이 실제로 바뀌었나」가 사라진다.
    """
    if body.role is not None and body.role not in adgroup_scope.VALID_ROLES:
        raise HTTPException(
            422, f"role은 {sorted(adgroup_scope.VALID_ROLES)} 중 하나이거나 null이어야 합니다"
        )

    res = adgroup_scope.apply_scope_row(
        db, campaign_id=body.campaign_id, adgroup_id=body.adgroup_id,
        role=body.role, enabled=body.enabled, memo=body.memo,
    )
    if res["outcome"] != "unchanged":
        db.add(NaverChangeLog(
            entity_type="adgroup", entity_id=body.adgroup_id, campaign_id=body.campaign_id,
            action="adgroup_scope_change",
            before_value=res["before"], after_value=res["after"],
            rationale="PAO 스코프 설정(D-NAO-244)",
            # changed_at 명시 — 안 넘기면 server_default가 UTC로 박힌다(교훈: 같은 함정 세 번).
            changed_at=kst_now(),
        ))
    db.commit()
    return {
        "campaign_id": body.campaign_id, "adgroup_id": res["adgroup_id"],
        "role": res["role"], "enabled": res["enabled"], "memo": res["memo"],
        "outcome": res["outcome"],
    }


class CampaignScopeBulkIn(BaseModel):
    """스코프 캠페인 단위 일괄 지정(H5 · 계약 P2). `extra='forbid'` — 단건과 같은 규격.

    ★**대상 광고그룹을 «명시»로 받는다 — 「이 캠페인의 전부」로 받지 않는다.** 「전부」는
      부르는 시점마다 뜻이 달라지는 말이라(그룹은 늘고 줄고, 화면은 특정 창의 목록을
      보여준다), 사람이 화면에서 본 것과 서버가 실제로 손댄 것이 조용히 어긋날 수 있다.
      본 것을 그대로 보내면 그 창이 닫힌다.
    """

    model_config = {"extra": "forbid"}

    campaign_id: str = Field(min_length=1, max_length=50)
    # 상한 500 — 한 캠페인의 실제 그룹 수는 수십 규모다(prod 최대 58). 무제한이면 한 번의
    # 잘못된 호출이 원장을 통째로 덮는다(`ConsoleExclusionImportIn`의 상한과 같은 이유).
    # ★원소에도 길이 제약을 건다(적대 리뷰 P2-2): 컬럼이 `String(50)`인데 리스트 원소만
    #   무제약이면 빈 문자열 행이 생기거나(그 캠페인이 「일부만 맡긴 상태」로 뒤집힌다)
    #   PostgreSQL 전환 시 DataError→500이 된다. SQLite가 안 막는다고 없어도 되는 게 아니다.
    adgroup_ids: list[Annotated[str, Field(min_length=1, max_length=50)]] = Field(
        min_length=1, max_length=500
    )
    # ★★`role`·`memo`는 **안 보내면 「건드리지 마라」**다(적대 리뷰 P1-1). 명시 `null`은
    #   「지워라」로 남긴다. 둘을 같은 값으로 두면 「켜기/끄기」 버튼 한 번에 사람이 붙여 둔
    #   역할·메모가 N건 사라지는데, 그건 어떤 타입으로도 못 막는다 — 판별은
    #   pydantic의 `model_fields_set`(=요청 본문에 그 키가 있었나)으로 한다.
    role: str | None = None
    enabled: bool = True
    memo: str | None = None


@router.put("/scope/campaign")
def pao_scope_campaign_bulk_put(body: CampaignScopeBulkIn, db: Session = Depends(get_db)):
    """H5 — 캠페인의 여러 광고그룹을 한 번에 맡긴다/뺀다.

    ★이 엔드포인트도 **엔진을 켜지 않는다**(단건과 동일). 스코프는 `auto_operate` «아래»의
    축이라, 캠페인이 꺼져 있으면 행을 넣어도 실행은 0이다.

    ★**N건이 한 트랜잭션이다.** 부분 커밋이 되면 「58개 중 40개만 맡겨진」 상태가 남는데
    그건 사람이 의도한 적 없는 상태고, 화면은 그걸 「일괄 완료」로 읽는다.

    ★중복 `adgroup_ids`는 **422로 거부**한다 — 조용히 dedupe 하면 「보낸 수」와 「손댄 수」가
    달라지고, 그 차이는 응답의 카운트가 아니라 사람의 기대에서 어긋난다.

    ★★`role`·`memo`를 **안 보내면 보존한다**(적대 리뷰 P1-1). 「전부 끄기」는 `enabled`만
    바꾸는 동작이어야 하고, 화면의 확인 문구가 그렇게 약속한다.
    """
    if body.role is not None and body.role not in adgroup_scope.VALID_ROLES:
        raise HTTPException(
            422, f"role은 {sorted(adgroup_scope.VALID_ROLES)} 중 하나이거나 null이어야 합니다"
        )
    if len(set(body.adgroup_ids)) != len(body.adgroup_ids):
        raise HTTPException(422, "adgroup_ids에 중복이 있습니다")

    # 「안 보냄」과 「null로 보냄」을 가른다 — 전자는 보존(KEEP), 후자는 지우기.
    role_arg = body.role if "role" in body.model_fields_set else adgroup_scope.KEEP
    memo_arg = body.memo if "memo" in body.model_fields_set else adgroup_scope.KEEP

    now = kst_now()
    results = []
    for adgroup_id in body.adgroup_ids:
        res = adgroup_scope.apply_scope_row(
            db, campaign_id=body.campaign_id, adgroup_id=adgroup_id,
            role=role_arg, enabled=body.enabled, memo=memo_arg,
        )
        results.append(res)
        if res["outcome"] != "unchanged":
            db.add(NaverChangeLog(
                entity_type="adgroup", entity_id=adgroup_id, campaign_id=body.campaign_id,
                action="adgroup_scope_change",
                before_value=res["before"], after_value=res["after"],
                # ★일괄이었다는 사실을 원장에 남긴다 — 같은 초에 N줄이 선 이유를 나중에
                #   읽는 쪽이 「폭주」로 오해하지 않게(수정 사항 화면의 피드 재적용과 같은 결).
                rationale=f"PAO 스코프 일괄 지정(H5 · 계약 P2, {len(body.adgroup_ids)}건 중 1)",
                changed_at=now,
            ))
    db.commit()

    counts = {k: sum(1 for r in results if r["outcome"] == k)
              for k in ("created", "updated", "unchanged")}
    return {
        "campaign_id": body.campaign_id,
        "requested": len(body.adgroup_ids),
        # ★`changed`를 따로 준다 — 화면이 「N건 맡김」이라 말할 때 그 N이 «감사 줄이 선 수»와
        #   같아야 한다. requested를 그대로 쓰면 no-op까지 「했다」로 표시된다.
        "changed": counts["created"] + counts["updated"],
        "counts": counts,
        # ★`rows`(행별 outcome)를 **뺐다** — 적대 리뷰 MB-11이 「빈 배열로 치환해도 전건
        #   초록」임을 보였다. 아무도 안 읽고 아무 테스트도 안 지키는 필드는 응답 계약을
        #   넓히기만 하고 조용히 썩는다. 필요해지면 그때 소비처·테스트와 함께 되살린다.
    }


@router.delete("/scope/adgroup")
def pao_scope_adgroup_delete(
    campaign_id: str, adgroup_id: str, db: Session = Depends(get_db)
):
    """스코프 행 삭제 — 되돌리기 사다리의 한 칸(배포 불요).

    ★캠페인의 **마지막** 행을 지우면 그 캠페인은 「스코프 미설정」으로 돌아가 전 그룹이
    다시 대상이 된다(진리표 2행). 일부만 끄고 싶으면 삭제가 아니라 `enabled=false`다 —
    둘의 결과가 정반대이므로 화면·운영 문서가 이 차이를 분명히 말해야 한다.
    """
    deleted = db.query(NaverAdgroupScope).filter(
        NaverAdgroupScope.campaign_id == campaign_id,
        NaverAdgroupScope.adgroup_id == adgroup_id,
    ).delete()
    if deleted:
        db.add(NaverChangeLog(
            entity_type="adgroup", entity_id=adgroup_id, campaign_id=campaign_id,
            action="adgroup_scope_change",
            before_value="scoped", after_value="removed",
            rationale="PAO 스코프 해제(D-NAO-244)",
            changed_at=kst_now(),
        ))
    db.commit()
    remaining = len(adgroup_scope.scope_rows_for_campaigns(db, [campaign_id]).get(campaign_id, []))
    return {
        "deleted": bool(deleted),
        "remaining_rows": remaining,
        # 화면이 「전 그룹으로 돌아갔다」를 말할 수 있게 — 조용히 넓어지면 안 되는 변화다
        "campaign_now_unrestricted": remaining == 0,
    }


_VALID_LOSS_POLICIES = {"leash", "stoploss_pause"}


class LossPolicySwitchIn(BaseModel):
    """캠페인 loss 대응 정책 전용 스위치(D-NAO-65 UI1). optimizer 스위치와 동형 —
    이 필드만 바꾸고 나머지(optimizer/mode/…)는 손대지 않는다(extra='forbid'로 오필드 422)."""

    model_config = {"extra": "forbid"}

    campaign_id: str
    loss_policy: str


@router.put("/campaign-settings/loss-policy")
def campaign_loss_policy_switch(body: LossPolicySwitchIn, db: Session = Depends(get_db)):
    """캠페인별 loss 대응 정책만 바꾼다(D-NAO-65 UI1). 커맨드센터 loss 정책 스위치의 쓰기 경로.

    ★왜 전용 엔드포인트인가(PUT /campaign-settings·/optimizer와 같은 원칙, D-NAO-53 교훈):
    전체 치환 PUT을 쓰면 loss_policy만 보내려다 mode·override·gamma가 null로 날아간다. 좁은
    엔드포인트로 그 실수를 구조적으로 막는다 — 이 함수는 loss_policy 외 필드를 건드리지 않는다.

    ⚠️ §0 금지선: 정책 쓰기는 이 PUT 하나뿐이다. 위임(delegation_gate)·자동 레인·SA 어디서도
    loss_policy를 바꾸지 않는다. 이건 우리 시스템 내부 설정이지 광고 API 쓰기가 아니다.
    실제 행위 차이는 proposal_writer.build()가 이 값을 읽어 스톱로스 SA에 주입할 때 난다.

    ★NULL=leash 정규화(감사 정확성): 미설정(NULL)은 기본값 'leash'와 의미가 같다. 그래서
    before를 'leash'로 정규화해 비교한다 — NULL→'leash'는 실질 무변경이라 change_log를 남기지
    않고, NULL→'stoploss_pause'만 기록한다(optimizer 스위치의 before='none' 정규화와 동형)."""
    if body.loss_policy not in _VALID_LOSS_POLICIES:
        raise HTTPException(422, f"loss_policy는 {sorted(_VALID_LOSS_POLICIES)} 중 하나여야 합니다")

    settings = db.query(NaverCampaignSettings).filter(
        NaverCampaignSettings.campaign_id == body.campaign_id
    ).first()
    # NULL/미설정은 기본값 'leash'로 정규화(NULL=leash 불변식) — 실질 변경 여부 판정용.
    before = (settings.loss_policy if settings and settings.loss_policy else "leash")

    if settings is None:
        # 없던 행은 optimizer='none'(자동화 안 함)에서 시작 — 정책만 저장했다고 라이브 쓰기가
        # 켜지면 안 된다(PUT /campaign-settings 신규행 관례와 동일).
        settings = NaverCampaignSettings(
            campaign_id=body.campaign_id, optimizer="none", loss_policy=body.loss_policy)
        db.add(settings)
    else:
        settings.loss_policy = body.loss_policy  # ★다른 필드는 손대지 않는다

    if before != body.loss_policy:
        db.add(NaverChangeLog(
            entity_type="campaign", entity_id=body.campaign_id, campaign_id=body.campaign_id,
            action="set_loss_policy",
            before_value=before, after_value=body.loss_policy,
            rationale="커맨드 센터 loss 정책 스위치(D-NAO-65 UI1)",
            # changed_at 명시(D-NAO-54, sqlite-server-default-now-is-utc): 안 넘기면 UTC로 박힘.
            changed_at=kst_now(),
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
            # ★changed_at 명시(D-NAO-54) — executed_at만 kst_now()를 주고 changed_at을
            #   빠뜨려서 UTC로 박히고 있었다. 위 optimizer_change와 같은 결함.
            dry_run=False, executed_at=kst_now(), changed_at=kst_now(),
        ))

    db.commit()
    return _delegation_response(db)


@router.get("/settings/guardrail-params")
def guardrail_params_get(db: Session = Depends(get_db)):
    """봉투 현황판(D-NAO-172 P1) — 지금 무슨 값으로 돌고 있고 **그게 어디서 왔는지**.

    ★`source`가 이 화면의 존재 이유다. 값만 보이면 「DB를 고쳤는데 코드 상수가 이기고 있는」
    상태를 못 본다 — 이 리포가 반복해 데인 «기록됐다 ≠ 코드가 읽는다»의 봉투판이다.
    `rejected=true`는 DB에 값이 있는데 타입·범위 때문에 폴백된 것(조용히 무시하지 않는다).
    읽기 전용.
    """
    return {
        "params": guardrail_params.describe(db),
        "from_db_enabled": guardrail_params._PARAMS_FROM_DB,
        # B3 되돌림 절차 — 스위치의 존재·용법을 응답에 실어 화면이 자기 설명을 하게 한다.
        "from_db_help": (
            "from_db_enabled=false면 DB를 아예 읽지 않는다"
            "(사고 시 되돌림 스위치, guardrail_params._PARAMS_FROM_DB). "
            "★이 스위치가 끄는 것은 **DB 층뿐**이다(D-NAO-282 적대 리뷰 P1-1) — "
            "환경변수 폴백이 있는 항목(env 칸에 이름이 있는 키)은 내려도 **여전히 그 환경변수 "
            "값으로 돈다.** 각 항목의 실제 출처는 params[].source가 말한다(db/env/code): "
            "「되돌렸으니 전부 코드 기본값일 것」이라고 읽지 말고 그 칸을 볼 것. "
            "되돌리는 절차: ①즉시 원복 — 배포로 _PARAMS_FROM_DB=False로 바꾼다(DB 값은 지우지 "
            "않고 보존됨. env 폴백이 없는 키는 source='code'로 복귀) ②항목별 원복 — "
            "PUT /settings/guardrail-params에서 그 키를 넘기지 않거나(전체 치환이므로 키를 빼면 "
            "그 항목은 아래 층으로 복귀) DB의 naver_account_settings.guardrail_params 행을 삭제한다. "
            "③환경변수 항목까지 되돌리려면 서버 .env를 고치고 재시작해야 한다."
        ),
        "retro_freshness": guardrail_params_retro_freshness(db),
        # ★D-NAO-262(#14) — 창을 끝까지 늘렸을 때 그만큼의 재료가 있나. 값 옆에 같이 보여야 한다.
        "window_coverage": guardrail_params_window_coverage(db),
    }


def guardrail_params_retro_freshness(db: Session) -> dict:
    """소급채점 신선도 — 봉투 판단의 **입력**이 낡았는지.

    ★왜 봉투 화면에 붙나: 2026-07-16~30 실측에서 차단 3,863건 중 **2,138건(55%)**이
    「소급채점 stale」이었다. 봉투(86건·2.2%)보다 자릿수가 큰 병목인데 **상설 표면이 없어서**
    조용히 늙었다. 값 옆에 「이 판단의 입력이 며칠 전 것인가」가 같이 보여야 한다.
    기대치는 D−1(어제까지 채점) — `auto_operator`의 `expected_asof`와 같은 규약.
    """
    latest = db.query(func.max(NaverRetroSignal.asof_date)).scalar()
    today = kst_now().date()
    expected = today - timedelta(days=1)
    return {
        "latest_asof": latest.isoformat() if latest else None,
        "expected_asof": expected.isoformat(),
        "stale": latest is None or latest < expected,
        "lag_days": (expected - latest).days if latest else None,
    }


# 창 파라미터 ↔ 그 창이 먹는 원본 source. 창을 늘려도 재료가 없으면 값만 늘고 판정은 안 늘어난다.
_WINDOW_MATERIAL = (
    ("pl_window_days", "expkeyword", "파워링크 제외 판정"),
    # ★D-NAO-265 — 승격 완료. 보류 시절엔 판정기 상수 14를 상한으로 «미리» 재고 있었고(그
    #   자리표시자가 `None`이었다), 이제 봉투 상한(16)을 실제로 잰다.
    ("ss_window_days", "shopping", "쇼핑 제외 판정"),
)


def guardrail_params_window_coverage(db: Session) -> list[dict]:
    """창 파라미터의 **재료**가 봉투 상한을 덮는가 (D-NAO-262 · 계약 #14).

    ★왜 봉투 화면에 붙나 — `retro_freshness`와 같은 이유의 다른 축이다. 저건 「판단의 입력이
    낡았나」를 재고, 이건 「판단의 창을 **끝까지 늘렸을 때** 그만큼의 재료가 실제로 있나」를 잰다.
    `pl_window_days`의 봉투 상한은 90일인데, 그 창이 서려면 `naver_search_term_daily`에 90일치가
    결손 없이 있어야 한다. 계약 #14 *"검색어 원본 보존 16일→창 상한 이상으로"*가 이 조건이다.

    ★2026-08-27 실측이 계약의 전제를 정정했다(§4-B④ⓑ — 목표 유지, 구현만 사실에 맞춤):
      · 「보존 16일」은 **우리 DB 보존이 아니라 네이버 리포트 보관 기한**이다(ref 21 §10 —
        자동 생성 리포트 16일 보관). 우리 쪽엔 purge 자체가 없다.
      · 그래서 prod는 이미 shopping 53일·expkeyword 373일을 갖고 있고, **봉투 상한 90일 창의
        결손은 0일**이었다 ⇒ 「연장」은 할 것이 없다.
      · 다만 그 충족은 **purge가 «없어서» 생긴 우연**이고 아무도 안 재고 있었다. 수집이 며칠
        죽거나(2026-08-26 크론 결손 전례) 누가 purge를 넣으면 **값은 90인데 실제로 보는 건
        60일**이 된다 — 게이트는 조용히 느슨해지고 로그엔 아무것도 안 남는다.
      ⇒ #14의 실질은 「늘리기」가 아니라 **「늘려도 되는지 상시 보이게 하기」**다.

    read-only. 결손이 있어도 아무것도 막지 않는다 — 관측이지 게이트가 아니다.
    """
    out: list[dict] = []
    for key, source, label in _WINDOW_MATERIAL:
        # ★D-NAO-265 — 두 창 파라미터가 «둘 다» 승격돼 폴백 분기가 죽었다. 죽은 분기를 남기면
        #   다음 사람이 「아직 승격 안 된 축이 있나」로 읽는다.
        ceiling = int(guardrail_params.SPECS[key].hi)
        latest = db.query(func.max(NaverSearchTermDaily.ad_date)).filter(
            NaverSearchTermDaily.source == source).scalar()
        if latest is None:
            out.append({
                "param_key": key, "source": source, "label": label,
                "promoted": key is not None,
                "ceiling_days": ceiling, "latest": None,
                "missing_days": None, "covered": False,
                "note": "원본 0행 — 창을 못 세운다",
            })
            continue
        need_from = latest - timedelta(days=ceiling - 1)
        have = {
            d for (d,) in db.query(NaverSearchTermDaily.ad_date).filter(
                NaverSearchTermDaily.source == source,
                NaverSearchTermDaily.ad_date >= need_from,
                NaverSearchTermDaily.ad_date <= latest,
            ).distinct().all()
        }
        missing = ceiling - len(have)
        out.append({
            "param_key": key, "source": source, "label": label,
            # ★`promoted`와 `note`를 가른다 — 「봉투가 없다(승격 보류)」와 「재료가 없다」는
            #   직교하는 사실이고, 한 문자열에 담으면 하나가 다른 하나를 덮는다(테스트가 잡았다).
            "promoted": key is not None,
            "ceiling_days": ceiling,
            "latest": latest.isoformat(),
            "window_from": need_from.isoformat(),
            "missing_days": missing,
            "covered": missing == 0,
            "note": None,
        })
    return out


@router.put("/settings/guardrail-params")
def guardrail_params_put(body: dict, db: Session = Depends(get_db)):
    """봉투 파라미터 설정 — **사람 승인 채널**(D-NAO-172 P1).

    ★D-NAO-248 §4-B(B1) 이후: 이 PUT은 여전히 「풀기는 사람이 승인한다」의 한 경로이지만,
    **더 이상 유일한 경로가 아니다** — `POST /proposals/{id}/status`(승인 핸들러)도 param_change
    제안 승인 시 같은 `guardrail_params.apply_params()`를 호출해 반영한다. ★그렇다고 이게
    「자동 적용」인 것은 아니다 — 트리거는 두 경로 모두 **사람의 명시적 행위**(이 PUT 호출 자체,
    또는 콘솔에서 제안을 승인하는 클릭)이고, **적용될 값의 크기도 사람이 확정**한다(승인
    핸들러는 값을 발명하지 않고 요청 body의 `applied_value`를 그대로 쓴다) — D-NAO-249 확정.
    전체 치환 저장 — 넘긴 키만 남고 나머지는 코드 상수로 돌아간다(부분 병합은 「지금 무슨
    값인지」를 사람이 못 쫓는다).
    타입·범위 밖 값은 **400으로 즉시 거부**한다. 저장 후 조용히 폴백시키면 화면엔 코드 상수가
    뜨는데 사람은 자기가 바꾼 줄 안다.
    """
    try:
        guardrail_params.apply_params(
            db, body, rationale="콘솔 PUT /settings/guardrail-params (D-NAO-172)")
    except guardrail_params.InvalidGuardrailParams as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.commit()
    return guardrail_params_get(db)


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
    # D-NAO-267 (계약 §4-A T1 = ref 65 S2-ⓐ): 보드별 rollup에 **평시/주말/공휴일 분리 열**을
    # 나란히 싣는다. 기존 d3/d7 키는 **그대로 둔다** — 이 응답의 소비처(커맨드 센터·타임라인)가
    # 그 shape을 읽고 있어서, 갈아치우면 분리를 얻고 기존 표면을 잃는다(additive only).
    # ★`weekend_holiday`가 계약 §4-C S2-① 원문이 지목한 열 이름 그대로다.
    boards = {
        board: {"d3": retro_rollup.board_rollup(rows, 3),
                "d7": retro_rollup.board_rollup(rows, 7),
                "weekend_holiday": {
                    "d3": retro_rollup.day_class_rollup(rows, 3),
                    "d7": retro_rollup.day_class_rollup(rows, 7),
                }}
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
_MAX_CHANGE_LOG_SPAN_DAYS = 365  # `days`의 le=365와 같은 상한 — 캘린더로 우회되면 안 된다


def _change_log_window(
    date_from: date | None, date_to: date | None, days: int
) -> tuple[datetime, datetime | None]:
    """조회 창을 [since, until) 로 확정한다(KST). until=None이면 상한 없음(= 지금까지).

    ★두 표현이 공존하는 이유(D-NAO-54): `days`는 "지금부터 N일 전"이라 **닫힌 구간**을 못 만든다.
    화면 프리셋이 요구하는 '당일만'·'어제만'·'어제부터 7일'(당일 제외)은 끝점이 필요하다.
    `days`는 date_from/date_to가 없을 때만 쓰는 폴백으로 남긴다(구 프론트 번들 호환 —
    배포 순간에 옛 JS가 days=30을 보내도 창이 뒤바뀌지 않게).

    ★KST 경계다. `changed_at`은 **writer가 kst_now()를 명시 전달할 때만** KST다 — 모델의
    `server_default=func.now()`(models.py:1614)는 **UTC**라서 안 넘긴 writer는 9시간 어긋난다
    (memory: sqlite-server-default-now-is-utc — 이 저장소가 이미 두 번 당한 함정).
    2026-07-17 실측: harness·entity_sync는 전부 명시 전달하지만 **이 라우터 자신의 writer
    2개**(optimizer_change:691 · update_expert_delegation:755)가 빠뜨려 UTC로 박고 있었다
    (drift 9.0h 실측) → 같은 커밋에서 고쳤다. **이 불변식은 여전히 규율이지 구조가 아니다** —
    새 writer를 추가할 때 kst_now()를 명시하지 않으면 조용히 UTC가 된다. prod에 이미 박힌
    과거 UTC 행(optimizer_change 등)은 백필하지 않았으므로 actor=all 조회 시 남아 있다.
    """
    if (date_from is None) != (date_to is None):
        # 한쪽만 주면 나머지 끝을 days로 메우게 되는데, 그건 사용자가 고른 적 없는 구간이다.
        raise HTTPException(422, "date_from과 date_to는 함께 지정해야 합니다.")
    if date_from is None or date_to is None:
        return kst_now() - timedelta(days=days), None
    if date_from > date_to:
        # 캘린더에서 뒤집어 고를 수 있다. 빈 결과로 조용히 넘기면 "변경이 없다"로 읽힌다.
        raise HTTPException(422, "date_from은 date_to보다 늦을 수 없습니다.")
    if (date_to - date_from).days + 1 > _MAX_CHANGE_LOG_SPAN_DAYS:
        raise HTTPException(422, f"조회 구간은 최대 {_MAX_CHANGE_LOG_SPAN_DAYS}일입니다.")
    # date_to 당일을 통째로 포함한다(끝점 포함) — 안 그러면 '당일' 탭이 00:00만 보고 빈다.
    try:
        until = datetime.combine(date_to + timedelta(days=1), datetime.min.time())
    except OverflowError:
        # ★date(9999,12,31) + 1일 = OverflowError → 500. 위 검사를 전부 통과한 뒤 터진다
        #   (from==to면 span 1일이라 합법). `<input type="date">`에 max가 없어 연도 칸에
        #   9999를 타이핑하면 도달한다 — 다른 잘못된 입력은 전부 422인데 이것만 500이었다.
        raise HTTPException(422, "date_to가 표현 가능한 날짜 범위를 벗어났습니다.") from None
    return datetime.combine(date_from, datetime.min.time()), until



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


def _execution_state(row: NaverChangeLog) -> str | None:
    """우리 실집행 시도의 3-상태(D-NAO-54). 해당 없으면 None.

      "executed" — 광고가 실제로 바뀌었다(writer가 네이버 재조회 응답을 after_value에 실었다).
      "blocked"  — 사전 가드레일 거부. writer를 부르지도 않았으므로 **확실히 안 바뀌었다**.
      "unknown"  — writer 예외. PUT을 이미 보낸 뒤일 수 있어 **반영 여부를 모른다**.
      None       — 이 개념이 적용되지 않는 행(외부 감지·내부 설정 변경·dry-run).

    ★왜 3-상태인가(codex 계열 지적, 2026-07-17): 처음엔 `executed: bool` 하나였는데, 그러면
    WriteVerificationError 행이 false가 되어 화면이 "🚫 차단됨"이라고 **단언**한다. 그런데
    그 예외는 "bidAmt는 반영됐으나 useGroupBidAmt 미전환"에서도 뜬다(naver_sa_writer:341) —
    **네이버엔 우리 입찰가가 들어가 있는데** 안 바꿨다고 말하는 셈이다. 네트워크 타임아웃
    (PUT 성공·응답 유실)도 같다. 모름을 긍정 주장으로 바꾸는 건 원칙22 위반이고, harness도
    같은 상황에 "사람이 네이버 콘솔로 실제 반영 여부를 확인"하라고 못 박고 있다.

    ★None을 정확히 돌려주는 것도 계약이다: 이 필드는 **actor=ours 관점 전용**인데 응답 전역에
    나간다. `external_keyword_removed`는 after_value가 없고(entity_sync가 before에 싣는다)
    `optimizer_change`는 after_value가 있다 — bool 하나로는 각각 "차단됨"·"집행됨"이라는
    거짓이 된다. 우리 가드레일은 남의 조작에 걸리지도 않고, optimizer_change는 광고 API를
    건드린 적이 없다.

    판별이 outcome이 아니라 after_value인 것은 이 코드베이스의 규약이다(_load_our_bid_writes·
    _detect_external_change 동일) — outcome은 D+14 채점 전 NULL이고 채점 후 improved/declined로
    바뀌므로 "실행됨"의 영구 상태가 아니다.
    """
    if row.action not in naver_execution_harness.EXECUTION_ACTIONS or row.dry_run:
        return None
    if row.after_value is not None:
        return "executed"
    if row.outcome != "failed":
        return None  # 실행 중(executing)·미판정 등 — 지어내지 않는다
    rationale = row.rationale or ""
    # ★검사 순서가 안전 방향이다: WRITE_FAILURE를 **먼저** 본다. harness는 제안 rationale
    # 뒤에 접두사를 이어붙이므로(f"{proposal.rationale} {MARKER} {reason}"), 제안 원문에
    # "[실행 불가]"가 들어 있으면 쓰기 실패 행이 blocked로 오판된다 — 그게 정확히 P1-2의
    # 거짓말(반영됐을 수도 있는데 "확실히 안 바뀜"이라 단언)이다. 이 순서면 쓰기 실패 행은
    # 항상 unknown으로 떨어진다. 반대 방향 오판(가드 거부 → unknown)은 보수적이라 안전하다.
    # prod 실측(2026-07-17): 제안 1,023건 중 원문에 마커를 가진 건 0건 — 지금은 도달 불가지만
    # 문자열 검사에 기대는 이상 순서로 막아둔다.
    if naver_execution_harness.WRITE_FAILURE_MARKER in rationale:
        return "unknown"
    if naver_execution_harness.GUARD_BLOCK_MARKER in rationale:
        return "blocked"
    # 접두사가 없어 판별 불가 — "모름"으로 보수 판정한다(가드 거부라고 단정하려면
    # GUARD_BLOCK_MARKER라는 적극적 증거가 있어야 한다).
    return "unknown"


def _batch_entity_names(
    db: Session, ent_keys: set[tuple[str, str]], camp_ids: set[str]
) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    """(entity_type, entity_id)·campaign_id 집합 → 사람 이름 배치 해석
    (Jino 2026-07-18: "적혀있는 대상은 알아볼 수가 없어"). naver_entity.name에
    캠페인/그룹명·키워드 텍스트가 있다(prod 실측 100% 채워짐).

    ★쿼리 2개(엔티티·캠페인)면 충분하다 — 대상마다 조회(N+1)하지 않는다. 호출자가
    limit≤200을 강제하므로 IN 목록도 유한하다. 이름이 비었거나 매핑에 없으면 키가 빠지고,
    프론트가 원래 'type id'로 폴백한다(지어내지 않음). change_log·proposals가 공유한다.
    """
    ent_names: dict[tuple[str, str], str] = {}
    if ent_keys:
        for e in (
            db.query(NaverEntity.entity_type, NaverEntity.entity_id, NaverEntity.name)
            .filter(tuple_(NaverEntity.entity_type, NaverEntity.entity_id).in_(list(ent_keys)))
            .all()
        ):
            if e.name:
                ent_names[(e.entity_type, e.entity_id)] = e.name

    camp_names: dict[str, str] = {}
    if camp_ids:
        for c in (
            db.query(NaverEntity.entity_id, NaverEntity.name)
            .filter(NaverEntity.entity_type == "campaign", NaverEntity.entity_id.in_(list(camp_ids)))
            .all()
        ):
            if c.name:
                camp_names[c.entity_id] = c.name
    return ent_names, camp_names


def _resolve_entity_names(
    db: Session, rows: list[NaverChangeLog]
) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    """change_log 행들 → 사람 이름. __bulk__ 요약행은 실엔티티가 아니라 제외."""
    ent_keys = {
        (r.entity_type, r.entity_id)
        for r in rows
        if r.entity_type and r.entity_id and r.entity_id != "__bulk__"
    }
    camp_ids = {r.campaign_id for r in rows if r.campaign_id}
    return _batch_entity_names(db, ent_keys, camp_ids)


@router.get("/change-log")
def get_change_log(
    campaign_id: str | None = Query(None, description="캠페인 필터"),
    action: str | None = Query(None, description="update_bid/external_bid_change/set_user_lock 등"),
    actor: str = Query(
        "all",
        pattern="^(all|ours|external)$",
        description="ours=우리 실집행만 / external=외부 변경 감지만 / all=전부(기본)",
    ),
    days: int = Query(30, ge=1, le=365, description="changed_at 조회 창(KST). date_from/date_to의 폴백"),
    date_from: date | None = Query(None, description="조회 시작일(KST, 포함). date_to와 함께 지정"),
    date_to: date | None = Query(None, description="조회 종료일(KST, 포함). date_from과 함께 지정"),
    include_dry_run: bool = Query(False, description="dry-run 기록 포함 여부(기본 제외)"),
    include_blocked: bool = Query(
        False,
        description="actor=ours일 때 가드레일 차단·쓰기 실패 시도도 포함(기본 제외). executed로 구분",
    ),
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

    ★`include_blocked`(D-NAO-54)는 위 계약을 **깨지 않으려고** 옵트인이다. 기본값 False에서
    actor=ours의 total은 여전히 "실제로 광고를 바꾼 횟수"다. True를 주면 가드레일이 막은 시도
    (harness `_guard_failure`: before/after 없음 · outcome='failed')가 함께 오고, 각 행의
    `executed`로 구분한다. 화면에 이게 필요한 이유: 가드레일이 일한 것도 우리가 한 일이다
    (prod 실측 2026-07-17 — 입찰 시도 4건 중 2건이 차단됐는데 어느 화면에도 안 떴다).

    ⚠️ 이 API는 change_log를 **읽기만** 한다. 이력을 *채우는* 것은 entity_sync의 diff 밸브
    (D-NAO-47 T2)와 naver_execution_harness다.
    """
    if include_blocked and actor != "ours":
        # 뒷문은 아니다(actor=all은 애초에 무필터라 차단분이 이미 들어오고, external은
        # EXTERNAL_DETECTION_ACTIONS로 좁혀져 닿지 않는다). 다만 **조용히 무시**하면 호출자가
        # 켰다고 믿는다 — 이 코드베이스는 무성 실패를 시끄럽게 만드는 쪽을 택해왔다.
        raise HTTPException(422, "include_blocked는 actor=ours에서만 의미가 있습니다.")
    since, until = _change_log_window(date_from, date_to, days)
    q = db.query(NaverChangeLog).filter(NaverChangeLog.changed_at >= since)
    if until is not None:
        q = q.filter(NaverChangeLog.changed_at < until)
    if campaign_id:
        q = q.filter(NaverChangeLog.campaign_id == campaign_id)
    if action:
        q = q.filter(NaverChangeLog.action == action)
    if actor == "ours":
        # ★after_value 존재를 함께 요구한다(codex[P2] R2). harness는 가드 거부·쓰기 실패에도
        # 같은 action을 dry_run=False로 남긴다(`_guard_failure`는 writer를 부르지도 않는다).
        # 그 행을 세면 광고에 아무 변화가 없었는데 "우리 조작 1회"가 된다.
        # 판별 기준이 outcome이 아니라 after_value인 것은 이 코드베이스가 이미 정한 규약이다
        # (naver_execution_harness.py:372 주석 · _detect_external_change · _load_our_bid_writes):
        # outcome은 D+14 채점 전 NULL이고 채점 후엔 improved/declined로 바뀌므로 "실행됨"이라는
        # 영구 상태가 아니다. 실패·가드거부·dry-run은 before/after_value를 안 채운다.
        q = q.filter(NaverChangeLog.action.in_(naver_execution_harness.EXECUTION_ACTIONS))
        if include_blocked:
            # ★"실집행 또는 **명시적 실패**"다. after_value 요구를 그냥 없애지 않는 이유:
            # 그러면 after가 비어 있기만 하면(원인 불명·중간 상태 포함) 무엇이든 '차단됨'
            # 배지를 달게 된다. 지어내지 않는다 — harness가 실제로 남기는 두 모양만 받는다
            # (성공=after 채움 / 가드거부·쓰기실패=outcome 'failed').
            q = q.filter(
                or_(
                    NaverChangeLog.after_value.isnot(None),
                    NaverChangeLog.outcome == "failed",
                )
            )
        else:
            q = q.filter(NaverChangeLog.after_value.isnot(None))
    elif actor == "external":
        q = q.filter(NaverChangeLog.action.in_(naver_execution_harness.EXTERNAL_DETECTION_ACTIONS))
    if not include_dry_run:
        q = q.filter(NaverChangeLog.dry_run.is_(False))

    total = q.count()
    # ★분리 집계(D-NAO-54): total 하나만 주면 "우리가 한 일의 결과" 카드가 `총 32건`이라고
    #   쓰는데 그게 실집행 2 + 차단 30일 수 있다 — actor 필터가 막으려던 바로 그 거짓말이
    #   화면에서 되살아난다. 행마다 execution_state를 주면서 집계는 안 나누면 옵트인이라는
    #   방어가 화면에서 0원이다. executed_total은 include_blocked를 켠 경우에만 의미가 있다.
    # ★dry_run.is_(False)를 반드시 함께 건다: 이 count는 `execution_state == "executed"`인 행
    #   수와 **정확히 같아야** 한다(푸터가 그 배지들의 집계라고 주장하므로). _execution_state는
    #   dry-run이면 None을 주는데 여기서 dry_run을 안 보면 `include_dry_run=true` 조합에서
    #   푸터가 "집행 1건"이라 말하면서 정작 그 행엔 배지가 없다(실측). 시뮬을 집행으로 세는 건
    #   D-47-h가 금지하는 바로 그 거짓말이다 — 아무것도 안 했는데 일한 것처럼 보인다.
    executed_total = (
        q.filter(
            NaverChangeLog.after_value.isnot(None),
            NaverChangeLog.dry_run.is_(False),
        ).count()
        if actor == "ours" and include_blocked
        else None
    )
    rows = q.order_by(NaverChangeLog.changed_at.desc()).offset(offset).limit(limit).all()
    # 대상 사람 이름 해석(D-NAO-54, Jino 2026-07-18) — ID만으로는 못 알아본다.
    ent_names, camp_names = _resolve_entity_names(db, rows)

    return {
        "total": total,
        "executed_total": executed_total,
        "rows": [
            {
                "id": r.id,
                "changed_at": r.changed_at.isoformat() if r.changed_at else None,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "campaign_id": r.campaign_id,
                # 이름 없으면 키 자체를 안 넣는다(None) → 프론트가 'type id'로 폴백.
                "entity_name": ent_names.get((r.entity_type, r.entity_id)),
                "campaign_name": camp_names.get(r.campaign_id),
                "action": r.action,
                "before": _loads_or_none(r.before_value),
                "after": _loads_or_none(r.after_value),
                "rationale": r.rationale,
                "outcome": r.outcome,
                # executed(bool)가 아니라 3-상태다 — 왜인지는 _execution_state docstring 참조.
                "execution_state": _execution_state(r),
                "dry_run": r.dry_run,
                "proposal_id": r.proposal_id,
                "executed_at": r.executed_at.isoformat() if r.executed_at else None,
            }
            for r in rows
        ],
    }


# ══════════════════════════════════════════════════════════════════
# 「수정 사항」 화면 — 두 원천 합본 조회 + 주체 정정(읽기 화면. 네이버 API 쓰기 0)
#
# ★왜 새 엔드포인트인가(기존 확장이 아니라): `/change-log`는 **한 테이블 단순 read**라는
#   계약 위에 서 있고 그 계약을 6개 소비자가 쓴다(커맨드 센터 두 패널·성과 화면 등).
#   거기에 두 번째 테이블을 union으로 끼우면 `total`·`executed_total`·`execution_state`의
#   의미가 조용히 바뀐다 — agency_op 행에는 dry_run도 실행 3상태도 없다. 기존 계약을 깨는
#   대신 새 창구를 낸다(`/bm/agency-ops`도 그대로 둔다 — 그쪽은 단일 원천 원자료 열람이다).
# ★날짜 검증 규칙은 `_change_log_window`를 **그대로 재사용**한다. 프론트 `customRangeError()`가
#   그 세 규칙(빈값·뒤집힘·365일)+미래 차단과 1:1로 맞춰져 있어, 여기서 규칙이 갈라지면
#   화면이 막지 못한 입력에 백엔드 422 원문이 그대로 노출된다.
# ══════════════════════════════════════════════════════════════════
_MAX_MODIFICATION_LIMIT = 500


@router.get("/creatives")
def get_creatives(
    campaign_id: str | None = Query(None, description="캠페인 필터"),
    days: int = Query(7, ge=1, le=90, description="date_from/date_to의 폴백 창(KST)"),
    date_from: date | None = Query(None, description="조회 시작일(KST, 포함). date_to와 함께"),
    date_to: date | None = Query(None, description="조회 종료일(KST, 포함). date_from과 함께"),
    sort: str = Query("cost", pattern="^(cost|imp|clk)$", description="정렬 축(내림차순)"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """소재(광고)별 성과 — ROAS를 **BEP와 나란히** 준다 (D-NAO-140 S2, 읽기 전용).

    ★이 화면이 없으면 안 보이는 것: 캠페인 평균이 적자 소재를 가린다. 2026-08-03 실측으로
    캠페인 03의 ROAS는 2.07~3.26인데 그 안의 소재 하나는 0.61이었다(3일 10.4만원 써서 6.4만원).

    ★`verdict`는 3상태다(above/below/**null**). BEP를 모르면 판정하지 않는다 — 모르는 걸
    '미달'로 적으면 매핑 결손이 적자로 둔갑한다.
    ★기본 창이 7일인 이유: 소재당 전환이 하루 0~3건이라 하루치 ROAS는 노이즈가 신호보다 크다.
    """
    since, until = _change_log_window(date_from, date_to, days)
    # creative 테이블의 축은 DATE라 창을 날짜로 되돌린다(until은 배타적 → 하루 물린다).
    until_date = (until - modification_feed._ONE_MICRO).date() if until is not None else kst_today()
    return creative_scorecard.build(
        db,
        since=since.date(),
        until=until_date,
        campaign_id=campaign_id,
        sort=sort,
        limit=limit,
        offset=offset,
    )


@router.get("/modifications")
def get_modifications(
    campaign_id: str | None = Query(None, description="캠페인 필터"),
    actor: str | None = Query(
        None,
        pattern="^(ours|agency|jino)$",
        description="주체 필터(정정 반영 후 기준). 미지정=전체",
    ),
    source: str | None = Query(
        None, pattern="^(change_log|agency_op)$", description="원천 필터. 미지정=두 원천 합본"
    ),
    days: int = Query(30, ge=1, le=365, description="date_from/date_to의 폴백 창(KST)"),
    date_from: date | None = Query(None, description="조회 시작일(KST, 포함). date_to와 함께"),
    date_to: date | None = Query(None, description="조회 종료일(KST, 포함). date_from과 함께"),
    include_dry_run: bool = Query(False, description="dry-run 기록 포함(기본 제외)"),
    include_blocked: bool = Query(
        False, description="가드레일이 막아 **실제로는 안 바뀐** 시도도 포함(기본 제외)"
    ),
    include_feed_reapply: bool = Query(
        True,
        description="네이버 상품 피드 재적용으로 판별된 행 포함(D-NAO-139). 끄면 사람이 만진 것만 남는다",
    ),
    collapse_feed_reapply: bool = Query(
        True,
        description="같은 상품이 같은 초에 움직인 N줄을 1줄로 접는다(D-NAO-139). 정보는 feed_group_ids에 남는다",
    ),
    limit: int = Query(100, ge=1, le=_MAX_MODIFICATION_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """그날 광고에 일어난 수정 사항 전건(두 원천 합본, 읽기 전용).

    행마다: 발생 시각 + 그것이 실제 발생인지 감지 시각인지(`time_basis`) · 주체(자동 판정
    또는 정정) · 대상(이름 해석) · 무엇을 · 이전값→이후값(불명이면 명시적 null + 사유) ·
    원천 · 소급 백필 여부 · 정정 여부.

    ★기본 정렬은 **발생 시각 내림차순**이고, 그 시각은 agency_op의 경우 `occurred_at`을
    먼저 본다 — 백필 36건은 감지일이 08-03이지만 실제로는 07-30 일이라, 감지일로 잡으면
    07-30을 골랐을 때 한 건도 안 보인다(이 화면이 존재하는 이유가 바로 그 대조 작업이다).
    """
    since, until = _change_log_window(date_from, date_to, days)
    return modification_feed.build(
        db,
        since=since,
        until=until,
        campaign_id=campaign_id,
        actor=actor,
        source=source,
        include_dry_run=include_dry_run,
        include_blocked=include_blocked,
        include_feed_reapply=include_feed_reapply,
        collapse_feed_reapply=collapse_feed_reapply,
        limit=limit,
        offset=offset,
    )


class ModificationActorIn(BaseModel):
    """주체 정정 1건. actor=None이면 정정을 **지우고** 자동 판정으로 되돌린다.

    ★되돌리기를 넣는 이유: 잘못 누른 정정을 못 지우면 원천은 깨끗한데 화면이 영영 틀리게
    말한다 — 그건 원천을 안 건드린 보람이 없는 상태다(일방통행 문 금지)."""

    model_config = {"extra": "forbid"}

    actor: str | None = None
    note: str | None = None


@router.put("/modifications/{source}/{source_id}/actor")
def put_modification_actor(
    source: str,
    source_id: int,
    body: ModificationActorIn,
    db: Session = Depends(get_db),
) -> dict:
    """수정 1건의 주체를 사람이 정정한다 — **원천 테이블은 건드리지 않는다**.

    naver_change_actor_override에 (source, source_id) 유일키로 upsert 한다. 자동 판정은
    그대로 남아 있고(`actor_auto`), 화면은 정정된 값을 우선 보여준다. 이 경로에서
    naver_change_log·naver_agency_op에 대한 UPDATE는 한 줄도 없다(계약).

    ⚠️ 이 프로젝트는 앱 레벨 인증이 없고 IP 허용목록으로 보호된다 — 다른 쓰기 엔드포인트
    (campaign-settings 계열)와 같은 패턴을 따르며 여기서 새 인증 층을 만들지 않는다.
    """
    if source not in change_actor.SOURCES:
        raise HTTPException(422, f"source는 {list(change_actor.SOURCES)} 중 하나여야 합니다.")
    if body.actor is not None and body.actor not in change_actor.ACTORS:
        raise HTTPException(422, f"actor는 {list(change_actor.ACTORS)} 중 하나여야 합니다.")

    # 존재하지 않는 행에 정정을 달면 화면에 영영 안 보이는 유령 레코드가 된다 — 404로 막는다.
    model = NaverChangeLog if source == change_actor.SOURCE_CHANGE_LOG else NaverAgencyOp
    if db.query(model.id).filter(model.id == source_id).first() is None:
        raise HTTPException(404, f"{source} #{source_id} 행이 없습니다.")

    now = kst_now()  # ★server_default=func.now()는 UTC다(9시간 어긋남) — 명시 주입.
    row = (
        db.query(NaverChangeActorOverride)
        .filter(
            NaverChangeActorOverride.source == source,
            NaverChangeActorOverride.source_id == source_id,
        )
        .first()
    )

    if body.actor is None:
        if row is not None:
            db.delete(row)
            db.commit()
        return {"source": source, "source_id": source_id, "actor": None, "corrected": False}

    if row is None:
        row = NaverChangeActorOverride(
            source=source, source_id=source_id, actor=body.actor,
            note=body.note, created_at=now, updated_at=now,
        )
        db.add(row)
    else:
        row.actor = body.actor
        row.note = body.note
        row.updated_at = now
    db.commit()
    return {
        "source": source,
        "source_id": source_id,
        "actor": row.actor,
        "actor_label": change_actor.ACTOR_LABEL.get(row.actor, row.actor),
        "note": row.note,
        "corrected": True,
        "updated_at": row.updated_at.isoformat(),
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


# ══════════════════════════════════════════════════════════════════
# D-NAO-48 — 캠페인 명부(관리주체 스위치의 데이터 원천)
# ★이 API가 생기기 전엔 화면에 캠페인 **이름이 없었다**(report가 안 줌) → 내부 ID
#   `cmp-a001-02-000000008492582`가 그대로 노출. MOP UX 리뷰에서 "베끼면 안 되는 것"으로
#   꼽은 항목을 우리가 하고 있었다. 이름은 naver_entity에 있다.
# 읽기 전용 — 관리주체 변경은 기존 PUT /campaign-settings가 유일 경로이고, 실행 게이트는
#   naver_execution_harness의 optimizer=='ours' 하드체크다(D-NAO-13, 이중 방어 불변).
# ══════════════════════════════════════════════════════════════════


@router.get("/campaigns")
def campaigns_roster(
    days: int = Query(30, ge=1, le=180, description="성과 집계 창(D-1 확정치 기준)"),
    campaign_type: str | None = Query(None, description="WEB_SITE/SHOPPING/BRAND_SEARCH"),
    optimizer: str | None = Query(None, pattern="^(none|ours|mop)$", description="관리주체 필터"),
    db: Session = Depends(get_db),
) -> dict:
    """캠페인 명부 — 이름·광고종류·상태 + 최근 N일 성과 + 관리주체(D-NAO-48).

    광고비 0인 캠페인도 포함한다(정지·신규 인계 대상에도 관리주체를 지정할 수 있어야
    카나리를 확대한다). roas_naver는 광고비 0이면 None — 'ROAS 0배'가 아니라 '알 수 없음'.
    """
    rows = campaign_roster.build(db, days=days)
    if campaign_type:
        rows = [r for r in rows if r["campaign_type"] == campaign_type]
    if optimizer:
        rows = [r for r in rows if r["optimizer"] == optimizer]
    return {"total": len(rows), "rows": rows}


# ══════════════════════════════════════════════════════════════════
# BM(벤치마크) 학습 레이어 온디맨드 드릴다운 (Phase 5, D-NAO-78·79 ③)
# ★주 UX는 예외 브리핑(diary/vault+Slack, bm_briefing.py) — 이 3개는 "초기 2~3주 전체검증
#   +이후 필요할 때 열어보는" 온디맨드 전체 리포트다(§완료기준 불변 — 상설 배너 아님).
# 전부 단순 read — 쓰기 없음, 실행 손(naver_execution_harness/naver_sa_writer) 무관.
# ══════════════════════════════════════════════════════════════════
_MAX_BM_LIMIT = 200


def _bm_value_or_none(raw: str | None) -> dict | list | None:
    """naver_bm_benchmark.value_json 파싱 — bench_kind별로 dict([min,p50,max]류)·list(검증
    키워드셋)가 섞여 있어 _loads_or_none(dict 전용)을 그대로 못 쓴다. 쓰레기여도 500 대신 None."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


@router.get("/bm/agency-ops")
def get_bm_agency_ops(
    date_param: date | None = Query(None, alias="date", description="조회 기준일(KST). 미지정시 오늘"),
    days: int = Query(1, ge=1, le=365, description="date 기준 최근 N일(포함, KST)"),
    campaign_id: str | None = Query(None),
    is_exception: bool | None = Query(None, description="true=예외만/false=비예외만, 미지정=전체"),
    limit: int = Query(100, ge=1, le=_MAX_BM_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """BM SA-2 조작 이벤트 드릴다운(naver_agency_op 단순 read). 예외 브리핑(주 UX)의 원자료를
    필요할 때 전건 열람하는 온디맨드 창구(D-NAO-79 ③)."""
    end = date_param or kst_today()
    start = end - timedelta(days=days - 1)
    q = db.query(NaverAgencyOp).filter(NaverAgencyOp.op_date >= start, NaverAgencyOp.op_date <= end)
    if campaign_id:
        q = q.filter(NaverAgencyOp.campaign_id == campaign_id)
    if is_exception is not None:
        q = q.filter(NaverAgencyOp.is_exception.is_(is_exception))
    total = q.count()
    rows = (
        q.order_by(NaverAgencyOp.op_date.desc(), NaverAgencyOp.detected_at.desc())
        .offset(offset).limit(limit).all()
    )
    camp_ids = {r.campaign_id for r in rows if r.campaign_id}
    _, camp_names = _batch_entity_names(db, set(), camp_ids)
    return {
        "date_from": start.isoformat(), "date_to": end.isoformat(), "total": total,
        "rows": [
            {
                "id": r.id,
                "op_date": r.op_date.isoformat() if hasattr(r.op_date, "isoformat") else str(r.op_date),
                "detected_at": r.detected_at.isoformat() if r.detected_at else None,
                # D-NAO-127: 실제로 손댄 시각(ad grain은 editTm으로 확정).
                # ★D-NAO-146: campaign/adgroup grain도 이제 네이버 editTm으로 채워진다 — 단
                # 창((직전 관측, 이번 관측]) 밖이거나 자식 롤업 op(키워드·제외키워드 수 증감)면
                # 여전히 None이다. None은 "시각 불명"이지 "변경이 없었다"가 아니다.
                # detected_at(감지 시각)과 섞으면 "언제"에 거짓으로 답하게 된다.
                "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
                "entity_type": r.entity_type, "entity_id": r.entity_id,
                "campaign_id": r.campaign_id, "campaign_name": camp_names.get(r.campaign_id),
                "optimizer": r.optimizer, "op_type": r.op_type,
                "before_value": r.before_value, "after_value": r.after_value,
                "magnitude": r.magnitude, "is_exception": r.is_exception,
            }
            for r in rows
        ],
    }


@router.get("/bm/snapshot")
def get_bm_snapshot(
    date_param: date | None = Query(None, alias="date", description="스냅샷 날짜(KST). 미지정시 오늘"),
    entity_type: str | None = Query(None, pattern="^(campaign|adgroup)$"),
    campaign_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=_MAX_BM_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """BM SA-1 구조 스냅샷 드릴다운(naver_entity_snapshot 단순 read). 요약(유형×optimizer별
    건수, 필터 무관 당일 전체 기준) + 페이징 원자료(D-NAO-79 ③)."""
    d = date_param or kst_today()
    day_rows = db.query(NaverEntitySnapshot).filter(NaverEntitySnapshot.snapshot_date == d).all()
    summary: dict[str, dict[str, int]] = {}
    for row in day_rows:
        bucket = summary.setdefault(row.entity_type, {})
        opt = row.optimizer or "none"
        bucket[opt] = bucket.get(opt, 0) + 1

    q = db.query(NaverEntitySnapshot).filter(NaverEntitySnapshot.snapshot_date == d)
    if entity_type:
        q = q.filter(NaverEntitySnapshot.entity_type == entity_type)
    if campaign_id:
        q = q.filter(NaverEntitySnapshot.campaign_id == campaign_id)
    total = q.count()
    rows = (
        q.order_by(NaverEntitySnapshot.entity_type, NaverEntitySnapshot.entity_id)
        .offset(offset).limit(limit).all()
    )
    return {
        "snapshot_date": d.isoformat(), "total": total, "summary_by_type_optimizer": summary,
        "rows": [
            {
                "entity_type": r.entity_type, "entity_id": r.entity_id, "parent_id": r.parent_id,
                "campaign_id": r.campaign_id, "campaign_type": r.campaign_type,
                "optimizer": r.optimizer, "name": r.name, "status": r.status,
                "daily_budget": r.daily_budget, "bid_amt": r.bid_amt,
                "extended_search": r.extended_search, "keyword_count": r.keyword_count,
                "keyword_avg_bid": r.keyword_avg_bid, "negative_kw_count": r.negative_kw_count,
                "ad_count": r.ad_count,
            }
            for r in rows
        ],
    }


@router.get("/bm/benchmark")
def get_bm_benchmark(
    bench_kind: str | None = Query(None, description="keyword_verified/bid_band/group_structure"),
    bench_key: str | None = Query(None, description="campaign_type 버킷(WEB_SITE 등)"),
    db: Session = Depends(get_db),
) -> dict:
    """BM SA-3 벤치마크 프라이어 현황 드릴다운(naver_bm_benchmark 단순 read). 주간 요약(diary)
    의 원자료를 최신 상태 그대로 열람(D-NAO-79 ③). 행 수가 campaign_type 버킷 단위라 페이징
    불필요(무필터 시 전량, prod 기준 수십 행 규모)."""
    q = db.query(NaverBmBenchmark)
    if bench_kind:
        q = q.filter(NaverBmBenchmark.bench_kind == bench_kind)
    if bench_key:
        q = q.filter(NaverBmBenchmark.bench_key == bench_key)
    rows = q.order_by(NaverBmBenchmark.bench_kind, NaverBmBenchmark.bench_key).all()
    return {
        "total": len(rows),
        "rows": [
            {
                "bench_kind": r.bench_kind, "bench_key": r.bench_key,
                "value": _bm_value_or_none(r.value_json),
                "sample_n": r.sample_n, "confidence": r.confidence,
                "computed_at": r.computed_at.isoformat() if r.computed_at else None,
            }
            for r in rows
        ],
    }


# ══════════════════════════════════════════════════════════════════
# 파워링크 검색어 자동 제외 in-out 재심사 드릴다운 (스프린트 PX4, §4 3,
#   docs/PLAN_naver-ad-powerlink-autoexclude.md). 주 UX는 예외 브리핑(diary/Slack,
#   search_term_px_briefing.py) — 이 1종은 상태기계 전 행을 열람하는 온디맨드 창구다.
# 단순 read — 쓰기 없음, 실행 손(naver_execution_harness/naver_sa_writer) 무관.
# ══════════════════════════════════════════════════════════════════


@router.get("/search-term/exclusions")
def get_search_term_exclusions(
    # void = 무효화된 행. 조회할 수 있어야 «지운 것»이 어디로 갔는지 확인 가능하다(사후 가시성).
    status: str | None = Query(None, pattern="^(excluded|probation|restored|void)$"),
    campaign_id: str | None = Query(None),
    # ★광고그룹 필터(가산). 슬롯 화면이 「이 그룹에 실제로 뭐가 걸려 있나」를 열어 보려면
    #   캠페인이 아니라 **그룹** 단위로 좁혀야 한다 — 한 캠페인에 그룹이 수십 개라
    #   campaign_id로 내리면 `limit` 안에서 그 그룹 몫이 잘려 「없다」로 보인다
    #   (같은 모양의 병이 `exclude_console_import`에서 이미 한 번 났다 — 위 주석).
    adgroup_id: str | None = Query(None),
    # ★적대 리뷰 1R P1-1 상환. 재개방 패널은 «우리가 건 제외»만 봐야 하는데, 그 필터를 화면에서
    #   걸면 **`limit` 뒤에** 걸린다. 이 원장은 3,990행 중 **3,987행이 `console_import`**라
    #   (2026-08-31 실측) 한 페이지가 편입분으로 다 차고 정작 열 수 있는 due 행이 응답에서 빠진다.
    #   그러면 화면엔 「우리가 건 검색어 제외가 없습니다」가 뜬다 — **이 손이 고치려던 병(「배지는
    #   있는데 누를 것이 없다」)을 정상 문구로 위장해 되살리는 것**이다. 그래서 SQL로 내린다.
    #   기본값 False: 이 창구는 드릴다운도 겸하고 「지운 것이 어디로 갔는지」는 보여야 한다.
    exclude_console_import: bool = Query(False),
    limit: int = Query(100, ge=1, le=_MAX_BM_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """파워링크 검색어 자동 제외 in-out 상태기계 드릴다운(naver_search_term_exclusion 단순
    read, PX4 §4 3). 요약(status별 건수, 필터 무관 전체 기준) + 오늘(KST) 제외/복귀 건수
    (last_transition_at 기준) + 페이징 원자료."""
    today = kst_today()
    today_start = datetime.combine(today, datetime.min.time())
    tomorrow_start = today_start + timedelta(days=1)

    # 상태별 건수는 GROUP BY 집계로(P3-2) — restored 영구 보존이라 단조 증가 테이블, 전 행 로드 회피.
    summary: dict[str, int] = {
        st: cnt
        for st, cnt in db.query(
            NaverSearchTermExclusion.status,
            func.count(NaverSearchTermExclusion.id),
        ).group_by(NaverSearchTermExclusion.status).all()
    }

    def _today_count(target_status: str) -> int:
        """오늘 «우리가 전이시킨» 건수 — ★콘솔 편입분은 뺀다(D-NAO-177).

        편입분의 `last_transition_at`은 편입 시각(=오늘)이라 그냥 세면 43건을 부은 날
        「오늘 43건을 잘랐다」가 된다. 그건 D-NAO-176의 1번 금지선(편입분이 오늘 실행한
        조치로 보이면 안 된다)이 일기에 대해 막은 것과 **같은 거짓 표상이 다른 문으로
        나가는** 것이다. 편입 자체는 편입 API 응답과 목록의 `source`로 보인다.
        """
        return db.query(NaverSearchTermExclusion).filter(
            NaverSearchTermExclusion.status == target_status,
            NaverSearchTermExclusion.last_transition_at >= today_start,
            NaverSearchTermExclusion.last_transition_at < tomorrow_start,
            search_term_execution.not_console_import(),
        ).count()

    q = db.query(NaverSearchTermExclusion)
    if status:
        q = q.filter(NaverSearchTermExclusion.status == status)
    if campaign_id:
        q = q.filter(NaverSearchTermExclusion.campaign_id == campaign_id)
    if adgroup_id:
        q = q.filter(NaverSearchTermExclusion.adgroup_id == adgroup_id)
    if exclude_console_import:
        # ★`limit` «전»에 건다 — 이 한 줄의 위치가 P1-1의 전부다(위 파라미터 주석).
        q = q.filter(search_term_execution.not_console_import())
    total = q.count()
    rows = (
        q.order_by(NaverSearchTermExclusion.last_transition_at.desc())
        .offset(offset).limit(limit).all()
    )
    camp_ids = {r.campaign_id for r in rows if r.campaign_id}
    _, camp_names = _batch_entity_names(db, set(), camp_ids)
    # ★적대 리뷰 1R P2-2 상환(N+1 축소): 일일 복귀 캡은 **행에 안 딸린 값**인데 행마다 다시 세고
    #   있었다(50행 요청에 SQL 207회 실측). 한 번 세서 게이트에 넘긴다 — 판정은 그대로다.
    returns_today = search_term_ss_lane.count_returns_today(db, kst_now())

    return {
        "total": total,
        "summary_by_status": summary,
        "today_excluded": _today_count("excluded"),
        "today_opened": _today_count("probation"),
        "today_restored": _today_count("restored"),
        "rows": [
            {
                "id": r.id, "campaign_id": r.campaign_id,
                "campaign_name": camp_names.get(r.campaign_id),
                "adgroup_id": r.adgroup_id, "search_term": r.search_term,
                "restrict_kwd_id": r.restrict_kwd_id, "status": r.status, "cycle": r.cycle,
                "excluded_at": r.excluded_at.isoformat() if r.excluded_at else None,
                # ★행의 출처와 «콘솔이 알려준 실제 제외 시각»(D-NAO-177). 둘 다 화면이 읽는다 —
                #   `source=console_import`면 `excluded_at`은 편입 시각이므로 그대로 보여 주면
                #   「오늘 자른 것」으로 읽힌다. NULL인 console_excluded_at은 「모른다」이고,
                #   화면이 그 상태를 «모름»으로 그려야 추정이 안 생긴다(교훈 #283 — 세는 것을
                #   화면까지 잇는다).
                "source": r.source,
                "console_excluded_at": (
                    r.console_excluded_at.isoformat() if r.console_excluded_at else None
                ),
                "last_transition_at": r.last_transition_at.isoformat() if r.last_transition_at else None,
                "next_review_at": r.next_review_at.isoformat() if r.next_review_at else None,
                "probation_until": r.probation_until.isoformat() if r.probation_until else None,
                "cost_at_exclusion": r.cost_at_exclusion,
                # ★재개방 버튼의 «비활성 사유»(계약 P2 넷째의 손). 값의 출처는 실행 경로와 **같은
                #   함수**다(`reopen_gate`) — 화면이 사유를 자기 말로 다시 계산하면 「무엇이 막았나」와
                #   「무엇이라 말했나」가 갈라진다. `check_live=False`라 라이브 광고그룹 유형 GET은
                #   건너뛴다(목록 100행에 API를 100번 때리지 않는다). 그래서 이 값은 **힌트**이고
                #   권위는 실행 시점에 있다 — 방향이 fail-closed라 안전하다(화면이 「열림」이라 해도
                #   실행이 다시 막지만, 그 반대는 없다).
                "reopen_block_reason": _reopen_block_reason_of(db, r, returns_today),
            }
            for r in rows
        ],
    }


_CONSOLE_IMPORT_REASON = "콘솔 편입분 — 우리가 건 제외가 아니라 재개방 대상이 아님"


def _reopen_block_reason_of(
    db: Session, row: NaverSearchTermExclusion, returns_today: int | None = None,
) -> str | None:
    """행 1건의 재개방 차단 사유(사람이 읽는 문구) — 없으면 None(=지금 열 수 있음, DB 기준).

    ★`console_import`는 게이트를 «묻기 전에» 걸러낸다. 의미상으로도 맞고(계약 §5 금지선 — 우리가
      걸지 않은 제외는 우리가 풀지 않는다) 비용상으로도 그렇다: 2026-08-31 실측으로 제외 3,990행
      중 **3,987행이 console_import**라, 안 거르면 목록 한 장이 쓸모없는 게이트 판정을 100번 돈다.
    """
    if row.source == "console_import":
        return _CONSOLE_IMPORT_REASON
    gate = search_term_ss_lane.reopen_gate(
        db, row, kst_now(), check_live=False, returns_today=returns_today,
    )
    return None if gate.reason is None else search_term_ss_lane.REOPEN_BLOCK_MESSAGES[gate.reason]


@router.post("/search-term/exclusions/{row_id}/reopen")
def reopen_search_term_exclusion(row_id: int, db: Session = Depends(get_db)) -> dict:
    """제외 1건을 **사람이** 지금 재개방한다(계약 P2 넷째의 «손»).

    ★**왜 이 손이 필요했나**: 유형별 dispatch(D-NAO-271 — 파워링크=id 기반 / 쇼핑=키워드 기반)는
    이미 구현돼 **자동 레인 안에서만** 돈다. 그런데 그 레인은 `auto_operate=1`인 캠페인만 훑으므로,
    스위치가 꺼진 캠페인의 제외는 `next_review_at`이 지나도 **아무도 못 연다** — 2026-08-31 실측:
    due 1건이 `next_review_at=2026-08-21`로 **10일째** 밀려 있었고 그 캠페인은 `auto_operate=0`이다.
    화면엔 「재개방 대기」 **배지만** 있었지 누를 것이 없었다(기능은 있는데 손이 없다).

    ★**게이트를 우회하지 않는다 — 이 손은 얇다.** 재개방 판정·실쓰기는 전부 자동 레인과 같은
    `search_term_ss_lane._open_exclusion`이 한다(캡·킬스위치·스코프·소속·유형·클레임·change_log·
    일기까지 그대로). 우회 경로 신설은 계약 §5 금지선이다 — **재개방도 네이버 실쓰기이고, 사람이
    눌렀다는 사실은 게이트 면제 사유가 아니다.** 그래서 `auto_operate`가 꺼져 있으면 이 창구도
    거부한다(계약 §5 처분 ⓐ: 「재개방하려면 그 캠페인의 auto_operate를 켠다」).

    ★**조용한 no-op을 만들지 않는다**: 못 열었으면 200에 `ok=false`와 **사유**를 실어 돌려준다.
    실패를 200으로 삼키는 게 아니라, 「막혔다」는 정상 응답이고 사유가 본문이다 — 화면이 그 문장을
    그대로 보여 준다. 못 찾은 행만 404다.
    """
    row = db.get(NaverSearchTermExclusion, row_id)
    if row is None:
        raise HTTPException(404, f"제외 상태 행 {row_id}을 찾을 수 없습니다")
    # ★`console_import` 행은 대상이 아니다(계약 §5 금지선 — 우리가 걸지 않은 제외는 우리가 풀지
    #   않는다). 게이트 함수가 아니라 여기서 막는 이유: 자동 레인은 애초에 이 행들을 후보로
    #   집지 않아 `reopen_gate`에 도달조차 안 한다 — 없는 검사를 게이트에 넣으면 「레인이 하는 일」과
    #   「게이트가 아는 것」이 갈라진다. 손에만 있는 제약이라 손에 적는다.
    if row.source == "console_import":
        return {
            "ok": False, "id": row.id, "status": row.status,
            "reason": _CONSOLE_IMPORT_REASON, "reason_code": "console_import",
        }
    gate = search_term_ss_lane.reopen_gate(db, row, kst_now())
    if gate.reason is not None:
        return {
            "ok": False, "id": row.id, "status": row.status,
            "reason": search_term_ss_lane.REOPEN_BLOCK_MESSAGES[gate.reason],
            "reason_code": gate.reason,
        }
    now = kst_now()
    opened = search_term_ss_lane.open_exclusion_now(db, row, now)
    db.refresh(row)
    return {
        "ok": opened, "id": row.id, "status": row.status,
        "reason": None if opened else "네이버 쓰기 실패 — change_log에 사유 기록(상태 유지·재시도 가능)",
        "probation_until": row.probation_until.isoformat() if row.probation_until else None,
    }


# ══════════════════════════════════════════════════════════════════
# 검색어 제외 «후보 리스트» + 조치 생존 감시 (D-NAO-173 P1,
#   docs/PLAN_search-term-exclusion-list.md). 둘 다 **읽기 전용**이다 —
#   이 스프린트에서 시스템은 리스트만 만들고 제외 실행은 Jino가 콘솔에서 한다(PLAN §3 금지선).
#   여기에 쓰기 엔드포인트를 추가하지 말 것.
# ══════════════════════════════════════════════════════════════════


@router.get("/search-term/exclusion-list")
def get_search_term_exclusion_list(
    days: int = Query(search_term_exclusion_list.WINDOW_DAYS, ge=7, le=90),
    campaign_id: str | None = Query(None),
    round_cap: int = Query(search_term_exclusion_list.DEFAULT_ROUND_CAP, ge=1, le=500),
    # ★기본값 None = 「안 넘기면 DB(SPECS `ss_min_click`)가 이긴다」(D-NAO-265). 옛 코드는
    #   `Query(MIN_CLICK)`이라 **import 시점 상수 10이 항상 실려** 승인 카드에서 값을 내려도
    #   이 API만 옛 값으로 돌았다. 명시 조회는 그대로 존중(what-if). 유효값은 응답 `gates`에.
    min_click: int | None = Query(None, ge=0, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """연속 ROAS × 상품별 BEP로 뽑은 제외 후보 리스트(읽기 전용, PLAN §4 P1-②).

    후보에서 빠진 것은 전부 buckets로 세어 나온다(조용한 절단 없음). 캠페인·광고그룹 이름은
    여기서 붙인다 — SA는 순수하게 두고 표시용 이름 해석은 라우터 몫(기존 관례)."""
    result = search_term_exclusion_list.build_exclusion_list(
        db, window_days=days, campaign_id=campaign_id, round_cap=round_cap, min_click=min_click,
    )

    camp_ids = {c["campaign_id"] for c in result["candidates"] if c["campaign_id"]}
    ent_keys = {("adgroup", c["adgroup_id"]) for c in result["candidates"] if c["adgroup_id"]}
    ent_names, camp_names = _batch_entity_names(db, ent_keys, camp_ids)
    for c in result["candidates"]:
        c["campaign_name"] = camp_names.get(c["campaign_id"])
        c["adgroup_name"] = ent_names.get(("adgroup", c["adgroup_id"]))
    return result


class SearchTermExecutionIn(BaseModel):
    """사람이 콘솔에서 실행한 제외 1건의 보고(D-NAO-173 P2-①).

    ★값 검증의 정본은 여기가 아니라 `search_term_execution._require`다. 화면 말고 자동 발견
      경로도 같은 문을 지나야 하므로 SA에 두고, 여기서는 «어차피 거부될 것»을 일찍 끊을 뿐이다
      (min_length=1). 두 곳에 서로 다른 규칙을 쓰면 어느 쪽이 정본인지 곧 갈린다.
    """

    campaign_id: str = Field(min_length=1, max_length=50)
    adgroup_id: str = Field(min_length=1, max_length=50)
    search_term: str = Field(min_length=1, max_length=300)
    rationale: str = Field(min_length=1, max_length=1000)


class SearchTermVoidIn(BaseModel):
    """장부 행 무효화 요청 — 사유는 필수다(왜 지웠는지 없는 삭제는 감사 불가)."""

    reason: str = Field(min_length=1, max_length=200)


@router.post("/search-term/executions")
def post_search_term_execution(
    payload: SearchTermExecutionIn, db: Session = Depends(get_db)
) -> dict:
    """**사람이 이미 실행한** 제외를 원장 + 운영일기에 등록한다(D-NAO-173 P2-①).

    ★이 라우터의 「쓰기 금지」 주석과 충돌하지 않는다: 금지선은 «자동 제외 **실행** 금지»이고,
      이 경로는 네이버에 아무것도 쓰지 않는다 — 사람이 한 일을 우리 기록에 남길 뿐이다.
      기록이 없으면 diary→outcome→wisdom 사슬의 입력이 0이라 **열 번을 잘라도 아무것도
      배우지 않는다.** 실행과 기록을 같은 것으로 취급하면 그 상태가 금지선의 이름으로 굳는다.
    """
    try:
        return search_term_execution.record_execution(
            db,
            campaign_id=payload.campaign_id,
            adgroup_id=payload.adgroup_id,
            search_term=payload.search_term,
            rationale=payload.rationale,
        )
    except search_term_execution.ExclusionInputError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.delete("/search-term/executions/{exclusion_id}")
def delete_search_term_execution(
    exclusion_id: int, payload: SearchTermVoidIn, db: Session = Depends(get_db)
) -> dict:
    """잘못 들어온 장부 행을 무효화한다(원장 status=void + 짝인 일기 행 중화).

    ★하드 삭제가 아니다 — 행은 감사용으로 남고 모든 소비자(성적표·생존 감시 배너·SS레인·자동
      발견)에서 빠진다. **되돌릴 수 있으므로** 승인 게이트가 아니라 자동 진행 대상이고, 대신
      사유를 필수로 받아 근거를 보존한다(전역 §1: 사후 가시성·정정 경로·근거 보존).

    ⚠️이미 학습에 반영된 몫은 되돌리지 못한다 — 반환값 `wisdom_may_have_counted`로 표면화한다.
    """
    try:
        return search_term_execution.void_execution(
            db, exclusion_id=exclusion_id, reason=payload.reason
        )
    except search_term_execution.ExclusionInputError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


class ConsoleExclusionRow(BaseModel):
    """콘솔에 이미 걸려 있는 제외 1건(편입용)."""

    campaign_id: str = Field(min_length=1, max_length=50)
    adgroup_id: str = Field(min_length=1, max_length=50)
    search_term: str = Field(min_length=1, max_length=300)
    restrict_kwd_id: str | None = Field(default=None, max_length=50)
    note: str | None = Field(default=None, max_length=200)
    # ★콘솔 「제외 검색어」 탭의 등록시각(D-NAO-177). **모르면 생략한다** — 여기를 비우면
    #   `console_excluded_at`이 NULL로 남아 「모른다」가 그대로 보존된다.
    #   타입을 datetime이 아니라 str로 받는 이유: 콘솔 실물 표기가 `2026.08.11 22:26`이라
    #   pydantic의 datetime 파서가 통째로 거부한다. 형식 판정은 SA(search_term_execution)가
    #   정본이고 라우터는 그 예외를 옮기기만 한다(이 리포의 검증 정본 규칙).
    #   ★이름이 `excluded_at`이 아닌 이유(적대 리뷰 P2): GET 응답의 `excluded_at`은 **장부가
    #   이 행을 세운 시각**이라 뜻이 다르다. 같은 이름이면 GET 결과를 그대로 되먹였을 때
    #   「모른다」가 편입 시각으로 굳는다 — 이름으로 원천 차단한다.
    console_excluded_at: str | None = Field(default=None, max_length=40)


class ConsoleExclusionImportIn(BaseModel):
    """일괄 편입 요청. 상한 200건 — 콘솔 실물이 약 45건이라 그 4배면 충분하고,
    무제한이면 한 번의 붙여넣기 실수가 원장을 통째로 덮는다."""

    rows: list[ConsoleExclusionRow] = Field(min_length=1, max_length=200)


@router.post("/search-term/executions/import")
def post_import_console_exclusions(
    payload: ConsoleExclusionImportIn, db: Session = Depends(get_db)
) -> dict:
    """콘솔에 **이미 걸려 있는** 제외를 장부에 일괄 편입한다(원장 전용 — 일기 0건, D-NAO-176).

    ★단건 POST(`/executions`)와 다른 점이 이 라우트의 전부다: 그쪽은 **방금 실행한** 조치를
      학습 사슬에 태우려고 일기를 쓴다. 여기 들어오는 것은 **시점을 모르는 과거 조치**라
      일기를 쓰면 「오늘 실행된 조치 N건」이라는 거짓 표본이 생기고, 13일 만의 진짜 표본
      1건을 그 안에 익사시킨다. 그래서 검증만 공유하고 일기 경로는 공유하지 않는다.

    성적표는 이 행들을 **판정하지 않는다**(실행 시점을 모르면 전후 창을 못 자른다) —
    대신 `imported_unjudgeable_count`로 세어 낸다. 조치 생존 감시에는 포함된다.

    거부는 **행 단위**다 — 200건 중 1건이 오타라고 199건이 죽으면 사람이 전체를 다시 붙여넣는다.
    """
    return search_term_execution.import_console_exclusions(
        db, rows=[r.model_dump() for r in payload.rows]
    )


@router.post("/search-term/executions/detect")
def post_detect_search_term_executions(
    campaign_id: str | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """라이브 제외키워드를 읽어 **원장에 없는 제외를 스스로 발견**해 등록한다(읽기+원장 쓰기).

    사람의 보고에 의존하지 않는 경로다 — 보고를 잊으면 그 조치는 영원히 시스템 밖이고, 이
    리포는 이미 «보고에 없던 변경»에 한 번 당했다(대행사 되돌림 2건 중 1건은 change_log에
    행조차 없었다). 네이버에 쓰지 않는다."""
    adgroup_ids = None
    if campaign_id:
        adgroup_ids = [
            r[0] for r in db.query(NaverSearchTermDaily.adgroup_id)
            .filter(
                NaverSearchTermDaily.campaign_id == campaign_id,
                NaverSearchTermDaily.adgroup_id != "",
            ).distinct().all()
        ]
    return search_term_execution.detect_new_exclusions(db, adgroup_ids=adgroup_ids)


@router.get("/search-term/exclusion-scorecard")
def get_search_term_exclusion_scorecard(
    window_days: int = Query(search_term_scorecard.WINDOW_DAYS, ge=3, le=60),
    db: Session = Depends(get_db),
) -> dict:
    """실행된 제외의 전후 대조표(읽기 전용, D-NAO-173 P2-②).

    직접 효과는 검색어 grain(그 검색어가 실제로 멈췄나), 부작용은 캠페인 grain(전환매출·총이익이
    유지됐나)으로 나눠 본다 — 한 층만 보면 각각 다른 방식으로 속는다."""
    result = search_term_scorecard.build_scorecard(db, window_days=window_days)
    camp_ids = {i["campaign_id"] for i in result["items"] if i["campaign_id"]}
    ent_keys = {("adgroup", i["adgroup_id"]) for i in result["items"] if i["adgroup_id"]}
    ent_names, camp_names = _batch_entity_names(db, ent_keys, camp_ids)
    for i in result["items"]:
        i["campaign_name"] = camp_names.get(i["campaign_id"])
        i["adgroup_name"] = ent_names.get(("adgroup", i["adgroup_id"]))
    return result


@router.get("/search-term/exclusion-survival")
def get_search_term_exclusion_survival(db: Session = Depends(get_db)) -> dict:
    """조치 생존 감시 요약(읽기 전용, PLAN §4 P1-①) — DB에 적힌 마지막 대조 결과.

    라이브 재조회는 하루 1회 잡(verify_search_term_exclusions, 08:25 KST)이 한다. 이 창구는
    그 결과를 읽기만 한다 — 화면을 열 때마다 네이버 API를 부르면 감시가 외부 지연에 묶인다."""
    return exclusion_survival.survival_summary(db)


@router.get("/search-term/exclusion-slots")
def get_search_term_exclusion_slots(db: Session = Depends(get_db)) -> dict:
    """제외 슬롯 사용률·소진 예상일(읽기 전용, S6-a · ref 66 §5).

    「우리가 건 제외가 아직 걸려 있나」(위 창구)와 **반대 방향의 질문**이다: 조치는 멀쩡한데
    **더 걸 칸이 남았나**. 그룹당 70칸(네이버 제약)이고 70/70이면 그 그룹의 음의 레버가
    소멸한다 — 파이프라인도 값도 정상이라 다른 어떤 감시에도 안 잡힌다.

    라이브 count는 일일 타겟 스윕(`sync_naver_adgroup_targets`, 09:35 KST)이 적재한다.
    이 창구는 그것을 읽기만 한다 — 화면을 열 때마다 네이버를 부르면 1,013콜이 튄다."""
    return exclusion_slot_usage.slot_usage(db)


# ══════════════════════════════════════════════════════════════════
# 광고 성과(사장님 뷰) — D-NAO-104 Phase 1 (docs/PLAN_naver-ad-performance-view.md §4-ⓐ)
# ★읽기 전용 페이지 전용 API다. 조작(관리주체 스위치·승인·예산 변경)은 커맨드 센터와
#   최적화 콘솔이 계속 담당한다 — 여기에 쓰기 엔드포인트를 추가하지 말 것(계획서 §0-1).
# ★응답 문자열은 전부 D-NAO-103 규칙을 통과한 것이다(ID·내부 용어 없음, 문장). 프론트는
#   문장을 조립하지 않고 그대로 렌더한다 — 표기 규칙이 두 벌이 되면 갈라진다.
# ══════════════════════════════════════════════════════════════════


@router.get("/performance/today")
def performance_today(db: Session = Depends(get_db)) -> dict:
    """오늘 한눈에(캠페인 카드) + 오늘 시스템이 한 일(한글 문장). 파라미터 없음(오늘 고정).

    당일 숫자의 원천은 시간별 스냅샷(비용·노출·클릭)과 스마트스토어 실주문(매출 프록시)이다
    — naver_ad_daily는 그날 확정 적재 전이라 쓰지 않는다(계획서 §4 창 관례).

    ★`roas_today_proxy`는 **상한 프록시**다(그 상품의 전체 판매액 / 광고비). 상품 매핑이 없는
    지면(파워링크·브랜드검색)은 배분이 원리적으로 불가능해 **null**로 나간다 — 0으로 채우면
    "성과가 바닥"이라는 거짓 단언이 된다(원칙22). 프론트는 null을 '—'로 렌더한다.
    """
    return perf_today_harness.build(db)


# 과거 조회 상한. 시간별 스냅샷 보관이 365일이고 naver_ad_daily도 그 언저리라, 더 뒤로 가면
# "데이터가 없다"를 "성과가 0이다"로 읽게 만드는 빈 화면만 나온다.
_MAX_PERFORMANCE_LOOKBACK_DAYS = 365


def _validate_performance_date(day: date, *, field: str) -> date:
    """미래·너무 먼 과거를 막는다. 미래 날짜를 허용하면 '오늘 고정' 분기가 조용히 미래를
    오늘로 취급해 프록시 숫자를 미래 날짜에 붙인다."""
    today = kst_today()
    if day > today:
        raise HTTPException(400, f"{field}는 오늘 이후일 수 없습니다")
    if (today - day).days > _MAX_PERFORMANCE_LOOKBACK_DAYS:
        raise HTTPException(400, f"{field}는 최근 {_MAX_PERFORMANCE_LOOKBACK_DAYS}일 이내여야 합니다")
    return day


@router.get("/performance/day")
def performance_day(
    date_: date | None = Query(None, alias="date", description="조회 날짜(YYYY-MM-DD, 기본 오늘)"),
    campaign_id: str | None = Query(None, description="특정 광고만(선택기용)"),
    db: Session = Depends(get_db),
) -> dict:
    """선택한 날짜의 ①한눈에 + ②그날 시스템이 한 일(D-NAO-105).

    ★날짜에 따라 숫자의 **출처가 다르다**: 오늘은 실주문 상한 프록시, 과거는 네이버 확정
    전환매출이다. 응답의 `source`/`source_label`/`roas_label`이 그것을 말한다 — 프론트는 그
    라벨을 그대로 쓴다(표기 규칙이 두 벌이 되면 갈라진다).
    """
    day = _validate_performance_date(date_, field="date") if date_ else None
    return perf_today_harness.build(db, day=day, campaign_id=campaign_id)


@router.get("/performance/compare")
def performance_compare(
    base: date = Query(..., description="기준일(YYYY-MM-DD)"),
    against: date = Query(..., description="비교일(YYYY-MM-DD)"),
    campaign_id: str | None = Query(None, description="특정 광고만"),
    db: Session = Depends(get_db),
) -> dict:
    """기준일 vs 비교일 — 캠페인별·합계 지출/노출/클릭/매출/ROAS 증감(절대+%) (D-NAO-105).

    하루 대 하루만 비교한다(기간 범위 비교는 후속 슬라이스 — 계획서 승계 큐).
    """
    _validate_performance_date(base, field="base")
    _validate_performance_date(against, field="against")
    if base == against:
        raise HTTPException(400, "기준일과 비교일이 같습니다")
    return perf_today_harness.compare(db, base=base, against=against, campaign_id=campaign_id)


@router.get("/performance/campaigns")
def performance_campaigns(db: Session = Depends(get_db)) -> dict:
    """캠페인 선택기 목록 — 이름·광고종류·관리주체만(D-NAO-105).

    ★화면에는 이름만 뜬다(D-NAO-103①). `campaign_id`는 select의 value로만 쓰이고 사람이
    읽는 자리에는 절대 나가지 않는다.
    """
    return perf_today_harness.campaign_options(db)


@router.get("/performance/ownership-bands")
def performance_ownership_bands(
    days: int = Query(30, ge=1, le=_MAX_PERFORMANCE_LOOKBACK_DAYS, description="최근 N일"),
    db: Session = Depends(get_db),
) -> dict:
    """관할 밴드 — 전체 / PAO가 돌린 광고 / 안 돌린 광고 (+ 전환일·모름).

    Jino 2026-08-29: *"전체/PAO가 돌리는광고/PAO가 돌리지 않는광고/ 이렇게 나눠줄 수 있어?"*

    ★밴드는 **그 날짜의 당시 관할**로 판정한다(방법 B) — 지금 관할을 과거에 소급하면
    「지금 맡은 것들의 과거 성과」라는 다른 질문에 답하게 된다. 실측(2026-08-29): 현재
    스코프는 그날 00:25에 생겼는데 그걸 30일에 투영하면 2,170,514원으로 뜬다. 당시 관할로는
    같은 창이 **0원**이다.

    ★오늘치는 안 들어간다 — `naver_ad_daily`가 D-1 확정 적재라 오늘 행이 없고, 오늘 카드가
    쓰는 시간별 스냅샷엔 광고그룹 축이 아예 없다. `window.truncated`가 그 사실을 말한다.
    """
    return perf_ownership_bands.recent(db, days)


@router.get("/performance/ownership-campaigns")
def performance_ownership_campaigns(
    date_: date | None = Query(None, alias="date", description="판정 기준일(기본 최신 확정일)"),
    db: Session = Depends(get_db),
) -> dict:
    """캠페인별 관할 — 목록 밴드 필터용. **시점 판정**이라 기준일을 같이 돌려준다.

    한 캠페인 안에서 일부 그룹만 PAO일 수 있어(Jino: *"광고그룹만도 가져올 수 있잖아"*)
    `pao_adgroups/adgroups`와 `partial` 플래그를 같이 낸다.
    """
    day = _validate_performance_date(date_, field="date") if date_ else None
    return perf_ownership_bands.campaign_bands(db, as_of=day)


@router.get("/performance/campaign/{campaign_id}")
def performance_campaign(
    campaign_id: str,
    days: int = Query(30, ge=1, le=perf_campaign_harness.MAX_SERIES_DAYS,
                      description="일별 추이 창(기본 30일, D-0 제외)"),
    db: Session = Depends(get_db),
) -> dict:
    """③캠페인 상세 — 일별 ROAS 추이(BEP선·목표선 포함) + 그룹별 상태 배지(D-NAO-105).

    series의 ROAS는 **네이버 확정 기준**(직+간접 전환매출 ÷ 광고비)이고 D-0은 제외한다 —
    카드의 당일 프록시와 정의가 달라 같은 선에 그리면 안 된다(계획서 §4 창 관례).
    """
    try:
        return perf_campaign_harness.build_campaign(db, campaign_id, days=days)
    except perf_campaign_harness.CampaignNotFound:
        raise HTTPException(404, "그런 광고를 찾을 수 없습니다")


@router.get("/performance/budget")
def performance_budget(
    date_: date | None = Query(None, alias="date", description="조회 날짜(기본 오늘)"),
    campaign_id: str | None = Query(None, description="특정 광고만"),
    db: Session = Depends(get_db),
) -> dict:
    """④예산 — 시간별 누적 소진 곡선 + 예산 도달로 멈춘 구간(암전) + 그날 예산 변경 이력.

    ★`budget_changes`가 빈 배열인 것은 **정상**이다(BP 레인 미배포 또는 그날 변경 없음) —
    에러가 아니라 "이날은 예산을 자동으로 바꾼 기록이 없습니다"로 말한다(계획서 §4-ⓒ).
    """
    day = _validate_performance_date(date_, field="date") if date_ else None
    return perf_campaign_harness.build_budget(db, day=day, campaign_id=campaign_id)


@router.get("/performance/bep-breakdown")
def performance_bep_breakdown(
    campaign_id: str | None = Query(None, description="특정 광고의 상품만(선택기용)"),
    only_actionable: bool = Query(True, description="광고에 연결된 상품만(false=네이버 전 상품)"),
    db: Session = Depends(get_db),
) -> dict:
    """⑤BEP 구성 — "이 상품은 클릭당 얼마까지 써야 남나"의 **근거 표**(Phase 3, 계획서 §4-ⓓ).

    판매가 − 수수료 − 원가 − 물류비 = 세전 잔액, 거기서 부가세(÷1.1)를 걷어낸 것이 공헌이익이고,
    손익분기 ROAS = 판매가 ÷ 공헌이익이다. 화면에서 뺄셈이 맞도록 VAT 단계를 응답에 명시한다.

    ★새 산식을 만들지 않는다 — 전부 매일 저장되는 `naver_product_bep` 스냅샷 값을 되짚어
    보여줄 뿐이다. 원가가 없는 상품은 **추정치로 채우지 않고** 산출 불가 사유를 문장으로 낸다.
    """
    return perf_timeline_harness.build_bep_breakdown(
        db, campaign_id=campaign_id, only_actionable=only_actionable
    )


@router.get("/performance/timeline")
def performance_timeline(
    days: int = Query(perf_timeline_harness.DEFAULT_TIMELINE_DAYS, ge=1,
                      le=perf_timeline_harness.MAX_TIMELINE_DAYS,
                      description="조회 창(일, 기본 90)"),
    campaign_id: str | None = Query(None, description="특정 광고만(계정 전체 변경은 항상 포함)"),
    db: Session = Depends(get_db),
) -> dict:
    """⑥개선 타임라인 — "우리가 뭘 바꿨고, 그 전후 7일은 어땠나"(Phase 3, 계획서 §4-ⓔ).

    ★인과를 주장하지 않는다(계획서 §3-3 · 원칙22). 이 시스템은 변경이 거의 매일 나와 전후
    7일 창이 서로 겹친다 — 겹친 다른 변경을 `confounded_with`로 **전부** 표기하고, 사후
    7일이 아직 안 지난 이벤트는 "관찰 중 (N/7일)"로 말한다. "개선됐습니다"라고 쓰지 않는다.

    ★카탈로그(`docs/naver_ad_improvement_events.json`)가 prod에 없어도 500이 아니다 —
    `catalog_available:false`로 말하고 라이브 설정 변경만 낸다(계획서 §6 Phase3 완료기준 5).
    """
    return perf_timeline_harness.build_timeline(db, days=days, campaign_id=campaign_id)
