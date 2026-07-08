# naver_ad.py — 네이버 SA 광고 리포트 라우터 (P1/P2-S2/P2-S3, track_naver-ad-optimization)
# GET /api/naver/ad/report            — 광고 리포트(KPI·3열 ROAS·드릴다운·시계열), ad_report Harness 경유.
# GET /api/naver/ad/bep               — 상품별 BEP 목록(단순 read, CRUD 직접).
# GET /api/naver/ad/diagnosis         — 진단 보드(출혈/승자/확장버킷/쇼핑BEP/제외후보/3단분류/악순환),
#   diagnosis Harness 경유(P2-S2). D-NAO-15/D-3: 전부 읽기 전용 — 제안·쓰기 없음.
# GET /api/naver/ad/proposals         — 제안 카드 목록(P2-S3, naver_proposals 단순 read).
#   각 행에 expert_verdict(최근 완료(status=ok) run의 평결 요약) 조인(E1a T6, 배지용).
# GET/PUT /api/naver/ad/campaign-settings — optimizer/mode/override 조회·설정(P2-S3, 전환 시
#   naver_change_log에 경량 기록). 광고 API 쓰기는 아님 — 우리 시스템 내부 설정만(D-NAO-13).
# GET /api/naver/ad/expert-reviews    — 전문가(Ava) 평결 목록(E1a T6, naver_expert_review 단순
#   read). proposal_id=NULL 행은 하루 총평.
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    NaverCampaignSettings,
    NaverChangeLog,
    NaverExpertReview,
    NaverExpertReviewRun,
    NaverProductBep,
    NaverProposal,
)
from app.services.naver_ad import metrics_aggregator
from app.services.naver_ad.ad_report import build_report
from app.services.naver_ad.diagnosis import build_diagnosis
from app.utils.kst import kst_today

router = APIRouter(prefix="/api/naver/ad", tags=["naver-ad"])

_VALID_GRAINS = metrics_aggregator.GRAINS + ("hour",)
_MAX_RANGE_DAYS = 180  # 과도한 범위 방지(리포트는 최근 위주)
_MAX_DIAGNOSIS_RANGE_DAYS = 30  # 진단 창은 최근 위주(다기간 비교는 harness 내부에서 30일 고정 사용)


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
_PROPOSAL_STATUSES = {"pending", "approved", "rejected", "expired"}
_MAX_PROPOSAL_RANGE_DAYS = 90


def _serialize_proposal(p: NaverProposal, verdict: NaverExpertReview | None) -> dict:
    return {
        "id": p.id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "proposal_type": p.proposal_type,
        "target_type": p.target_type,
        "target_id": p.target_id,
        "campaign_id": p.campaign_id,
        "rationale": p.rationale,
        "expected_effect": p.expected_effect,
        "status": p.status,
        "slack_ts": p.slack_ts,
        "executed_change_log_id": p.executed_change_log_id,
        "expert_verdict": _serialize_expert_verdict_summary(verdict) if verdict else None,
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
    행은 하루 총평(E1a T6)."""
    q = db.query(NaverExpertReview)
    if as_of is not None:
        q = q.filter(NaverExpertReview.as_of == as_of)
    if proposal_id is not None:
        q = q.filter(NaverExpertReview.proposal_id == proposal_id)
    rows = q.order_by(NaverExpertReview.id.desc()).limit(limit).all()
    return {"rows": [_serialize_expert_review(r) for r in rows]}


_VALID_OPTIMIZERS = {"none", "ours", "mop"}
_VALID_MODES = {"growth", "recovery", "launch", "defense"}


class CampaignSettingsIn(BaseModel):
    campaign_id: str
    optimizer: str
    mode: str | None = None
    target_roas_override: float | None = None
    memo: str | None = None


def _serialize_settings(s: NaverCampaignSettings) -> dict:
    return {
        "campaign_id": s.campaign_id,
        "optimizer": s.optimizer,
        "mode": s.mode,
        "target_roas_override": _num(s.target_roas_override),
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
