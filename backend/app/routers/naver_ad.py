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

import requests
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, tuple_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    NaverAccountSettings,
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
from app.services.naver_ad import bid_step_types
from app.services.naver_ad import campaign_roster
from app.services.naver_ad import guardrail_params
from app.services.naver_ad import change_actor
from app.services.naver_ad import creative_scorecard
from app.services.naver_ad import dashboard_overview
from app.services.naver_ad import delegation_gate
from app.services.naver_ad import metrics_aggregator
from app.services.naver_ad import modification_feed
from app.services.naver_ad import naver_execution_harness
from app.services.naver_ad import naver_sa_writer
from app.services.naver_ad import perf_campaign_harness
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
) -> dict:
    blocker_reason = naver_execution_harness.real_write_blocker(p)
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
        "rows": [_serialize_proposal(p, verdicts.get(p.id), ent_names, camp_names) for p in rows],
    }


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
    return _serialize_settings(settings)


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
        "retro_freshness": guardrail_params_retro_freshness(db),
        # 낙관적 락 토큰 — 저장할 때 `If-Match`로 되돌려 보낸다(아래 PUT 참조).
        "version": guardrail_params.state_version(db),
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


@router.put("/settings/guardrail-params")
def guardrail_params_put(
    body: dict,
    db: Session = Depends(get_db),
    if_match: str | None = Header(None, alias="If-Match"),
):
    """봉투 파라미터 설정 — **사람 승인 채널**(D-NAO-172 P1).

    ★이 PUT이 설계의 「풀기는 사람이 승인한다」를 물리적으로 구현한다. 시스템은 이 경로를
    호출하지 않는다(P3의 제안 생성기도 «제안»만 만들고 누르는 것은 사람이다).
    전체 치환 저장 — 넘긴 키만 남고 나머지는 코드 상수로 돌아간다(부분 병합은 «지금 무슨
    값인지»를 사람이 못 쫓는다).
    타입·범위 밖 값은 **400으로 즉시 거부**한다. 저장 후 조용히 폴백시키면 화면엔 코드 상수가
    뜨는데 사람은 자기가 바꾼 줄 안다.

    ★**낙관적 락 필수**(적대 리뷰 P2, 2026-08-10): `If-Match`에 GET이 준 `version`을 그대로
    되돌려 보내야 한다. 전체 치환이라 두 탭이 각자 스냅샷으로 저장하면 **먼저 저장한 쪽이
    조용히 사라진다** — 이 기능이 막으려던 사고 모양 그 자체다.
    헤더가 **없어도 거부**한다(400). 선택이면 옛 번들·수기 호출이 그대로 창을 열어 둔 채
    남는데, 이 경로는 호출자가 콘솔 하나뿐이라 필수로 만드는 비용이 없다.
    어긋나면 **409** — 쓰기를 거부하는 것은 새로고침으로 회복되지만, 남의 설정이 조용히
    사라지는 것은 회복되지 않는다(fail-closed가 맞는 방향).
    """
    if not isinstance(body, dict):
        raise HTTPException(400, "본문은 객체여야 합니다")
    if not if_match:
        raise HTTPException(
            400,
            "If-Match 헤더가 없습니다 — 화면을 새로고침한 뒤 다시 저장해 주세요"
            "(전체 치환이라 다른 탭의 설정을 덮어쓸 수 있어 버전 확인을 요구합니다)",
        )
    if if_match != guardrail_params.state_version(db):
        raise HTTPException(
            409,
            "다른 곳에서 봉투 파라미터가 이미 바뀌었습니다 — 화면을 새로고침해 지금 값을 확인한 뒤 "
            "다시 저장해 주세요(그대로 저장하면 그쪽 설정이 사라집니다)",
        )
    unknown = set(body) - set(guardrail_params.SPECS)
    if unknown:
        raise HTTPException(400, f"알 수 없는 파라미터: {sorted(unknown)}")
    cleaned: dict = {}
    for key, raw in body.items():
        spec = guardrail_params.SPECS[key]
        val = guardrail_params._coerce(spec, raw)
        if val is None:
            raise HTTPException(
                400,
                f"{key}={raw!r}는 허용 범위 [{spec.lo}, {spec.hi}] 밖이거나 타입이 맞지 않습니다",
            )
        cleaned[key] = str(val)

    before = {k: str(v) for k, v in guardrail_params.get_params(db).items()}
    row = db.query(NaverAccountSettings).filter(
        NaverAccountSettings.key == guardrail_params.SETTINGS_KEY).first()
    if row is None:
        row = NaverAccountSettings(key=guardrail_params.SETTINGS_KEY, value_json=json.dumps(cleaned))
        db.add(row)
    else:
        row.value_json = json.dumps(cleaned)
    db.flush()
    after = {k: str(v) for k, v in guardrail_params.get_params(db).items()}

    if before != after:
        now = kst_now()
        db.add(NaverChangeLog(
            entity_type="account", entity_id="", campaign_id="",
            action="update_guardrail_params",
            before_value=json.dumps(before, ensure_ascii=False),
            after_value=json.dumps(after, ensure_ascii=False),
            rationale="콘솔 PUT /settings/guardrail-params (D-NAO-172)",
            # changed_at·executed_at 둘 다 KST 명시 — B-1 가드(D-NAO-169)가 30분 초과 어긋남을 거부한다.
            dry_run=False, executed_at=now, changed_at=now,
        ))
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
    boards = {
        board: {"d3": retro_rollup.board_rollup(rows, 3),
                "d7": retro_rollup.board_rollup(rows, 7)}
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
    status: str | None = Query(None, pattern="^(excluded|probation|restored)$"),
    campaign_id: str | None = Query(None),
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
        return db.query(NaverSearchTermExclusion).filter(
            NaverSearchTermExclusion.status == target_status,
            NaverSearchTermExclusion.last_transition_at >= today_start,
            NaverSearchTermExclusion.last_transition_at < tomorrow_start,
        ).count()

    q = db.query(NaverSearchTermExclusion)
    if status:
        q = q.filter(NaverSearchTermExclusion.status == status)
    if campaign_id:
        q = q.filter(NaverSearchTermExclusion.campaign_id == campaign_id)
    total = q.count()
    rows = (
        q.order_by(NaverSearchTermExclusion.last_transition_at.desc())
        .offset(offset).limit(limit).all()
    )
    camp_ids = {r.campaign_id for r in rows if r.campaign_id}
    _, camp_names = _batch_entity_names(db, set(), camp_ids)

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
                "last_transition_at": r.last_transition_at.isoformat() if r.last_transition_at else None,
                "next_review_at": r.next_review_at.isoformat() if r.next_review_at else None,
                "probation_until": r.probation_until.isoformat() if r.probation_until else None,
                "cost_at_exclusion": r.cost_at_exclusion,
            }
            for r in rows
        ],
    }


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
