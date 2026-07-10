# dashboard_overview.py — dashboard_overview SA (대시보드 미니 스프린트 T1, D-NAO ref 28 §6)
# 역할: 5단 파이프라인(수집07:30/예측07:50/제안08:00/전문가08:05/학습08:10)의 라이브 증거
#   기반 상태 + optimizer_coverage(카나리 확대 진행률)를 조립해 GET /dashboard-overview
#   응답 하나로 정리. 읽기 전용 — DB 쓰기 없음. 크론 스케줄이 아니라 각 단계의 실데이터
#   최신 타임스탬프로 status를 판정한다(원칙22: "됐다"는 라이브 증거로만).
#   SA간 직접 호출 금지(원칙18) — proposal_writer.INFORMATIONAL_PROPOSAL_TYPES(실행형/정보성
#   분류)·trigger_watch.KST_STAMPED_PROPOSAL_TYPES(시간대 소스 분류)만 단일 진실로 재사용
#   (재정의 금지 — 어긋나면 브리핑·대시보드가 서로 다른 숫자를 보여주는 사고로 이어진다).
#
# ── 타임스탬프 소스 실측표 (컬럼 × 작성자, codex 2R 지적 재조사 — 추정 금지·전량 grep 실측) ──
#   NaverAdDaily.synced_at          | ad_daily_ingest.py·campaign_backfill.py 둘 다
#                                    | kst_now() 명시 대입 → 이미 KST. 변환 금지.
#   NaverProposal.created_at        | proposal_writer.py(persist/account_brief_singleton)
#                                    |   → 명시 대입 없음, server_default=func.now() → UTC.
#                                    | trigger_watch.py(run_hourly, PACING/CPC 2종만)
#                                    |   → kst_now() 명시 대입 → 이미 KST.
#                                    | ⇒ 컬럼 내 소스 혼재. proposal_type이
#                                    |   trigger_watch.KST_STAMPED_PROPOSAL_TYPES에 속하면
#                                    |   변환 금지, 아니면 UTC→KST 변환(_normalize_proposal_
#                                    |   created_at 참조).
#   NaverForecastModel.updated_at   | forecast_model_builder.py: updated_at 명시 대입 없음,
#                                    | onupdate=func.now()에 맡김 → 항상 UTC. (trained_at
#                                    | 필드는 성공 경로에서만 kst_now() 대입되고 nullable이라
#                                    | 상태판정 소스로 부적합 — 미사용.) forecast_gate.py는
#                                    | 읽기 전용(쓰기 없음).
#   NaverExpertReviewRun.created_at | expert_ledger.py: 명시 대입 없음, server_default=
#                                    | func.now() → 항상 UTC.
#   NaverExpertReviewRun.as_of      | expert_ledger.py: 호출자(expert_desk.py)가
#                                    | kst_today() 계산해 전달 → 이미 KST 달력일(시각 아님,
#                                    | 변환 불필요) — 상태판정은 이 필드로만 한다.
#   NaverLearningState.updated_at   | conversion_maturity.py·estimate_calibrator.py·
#                                    | hourly_pattern.py·proposal_scoreboard.py·
#                                    | expert_ledger.py._upsert_scoreboard 5개 writer 전부
#                                    | 명시 대입 없음, onupdate=func.now() → 항상 UTC.
# ─────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    NaverAdDaily,
    NaverCampaignSettings,
    NaverExpertReview,
    NaverExpertReviewRun,
    NaverForecastModel,
    NaverLearningState,
    NaverProposal,
)
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.proposal_writer import INFORMATIONAL_PROPOSAL_TYPES
from app.services.naver_ad.trigger_watch import KST_STAMPED_PROPOSAL_TYPES
from app.utils.kst import kst_today

# 스테이지 상태 3분류 — 모듈 상수(테스트 용이, 오타 방지).
STATUS_OK = "ok"
STATUS_STALE = "stale"
STATUS_NONE = "none"

# 수집(07:30)은 D-1치를 채운다 — 최신 ad_date가 "어제"면 정상(오늘 크론이 아직 안 돌았어도
# 어제분은 이미 확정 적재돼 있어야 정직한 "ok").
INGEST_LAG_DAYS = 1

# optimizer_coverage 창 — D-1 확정 데이터 기준 최근 7일(오늘은 naver_ad_daily가 아직
# 미확정/미적재일 수 있어 제외, ad_report·diagnosis의 D-1 관례와 동일).
COVERAGE_WINDOW_DAYS = 7


def _utc_to_kst(ts: datetime | None) -> datetime | None:
    """검증된 UTC 소스 컬럼 전용 정규화(+9h) — 파일 상단 실측표에서 "항상 UTC"로 확인된
    컬럼(NaverForecastModel.updated_at, NaverExpertReviewRun.created_at,
    NaverLearningState.updated_at, NaverProposal.created_at 중 proposal_writer.py 발행분)
    에만 호출할 것.

    ⚠️ NaverAdDaily.synced_at과 NaverProposal.created_at 중 trigger_watch.py 발행분
    (KST_STAMPED_PROPOSAL_TYPES)은 이미 KST라 여기에 넣으면 안 된다(2026-07 codex 2R
    지적 — 과거엔 이 함수가 모든 증거에 일괄 적용돼 이 두 소스가 미래 시각으로 이중
    변환되고, 저녁 KST 제안이 다음 날로 밀려 카운트가 틀어지는 버그가 있었다).
    NaverExpertReviewRun.as_of·NaverAdDaily.ad_date는 시각이 아닌 달력일이라 애초에
    이 함수 대상이 아니다.
    """
    if ts is None:
        return None
    return ts + timedelta(hours=9)


def _stage(key: str, name: str, last_evidence_at: datetime | None, status: str, detail: str) -> dict:
    return {
        "key": key,
        "name": name,
        "last_evidence_at": last_evidence_at.isoformat() if last_evidence_at else None,
        "status": status,
        "detail": detail,
    }


def _ingest_stage(db: Session, today: date) -> dict:
    max_ad_date = db.query(func.max(NaverAdDaily.ad_date)).scalar()
    if max_ad_date is None:
        return _stage("ingest", "수집", None, STATUS_NONE, "적재된 데이터 없음")

    rows_count, last_evidence_at = db.query(
        func.count(NaverAdDaily.id), func.max(NaverAdDaily.synced_at)
    ).filter(NaverAdDaily.ad_date == max_ad_date).one()
    # synced_at은 ad_daily_ingest.py·campaign_backfill.py 둘 다 kst_now() 명시 대입(실측,
    # 파일 상단 표 참조) — 이미 KST라 변환하면 미래 시각으로 밀리는 이중 변환이 된다.

    # status는 ad_date(달력일, 보정 불필요)로만 판정 — synced_at은 표시용 증거일 뿐.
    status = STATUS_OK if max_ad_date >= today - timedelta(days=INGEST_LAG_DAYS) else STATUS_STALE
    detail = f"최신 D-1 적재: {max_ad_date.isoformat()} (rows {rows_count})"
    return _stage("ingest", "수집", last_evidence_at, status, detail)


def _forecast_stage(db: Session, today: date) -> dict:
    rows = db.query(NaverForecastModel.gate_status, NaverForecastModel.updated_at).all()
    if not rows:
        return _stage("forecast", "예측", None, STATUS_NONE, "예측 모델 없음")

    active = sum(1 for gate_status, _ in rows if gate_status == "active")
    fallback = sum(1 for gate_status, _ in rows if gate_status == "fallback")
    demoted = sum(1 for gate_status, _ in rows if gate_status == "demoted")
    # updated_at은 forecast_model_builder.py가 명시 대입 없이 onupdate=func.now()에
    # 맡기는 컬럼(실측, 파일 상단 표 참조) → 항상 UTC.
    timestamps = [_utc_to_kst(updated_at) for _, updated_at in rows if updated_at is not None]
    last_evidence_at = max(timestamps) if timestamps else None
    as_of = last_evidence_at.date() if last_evidence_at else None

    status = STATUS_OK if as_of == today else STATUS_STALE
    as_of_label = as_of.isoformat() if as_of else "없음"
    detail = f"활성 {active}개·폴백 {fallback}개·강등 {demoted}개(as_of {as_of_label})"
    return _stage("forecast", "예측", last_evidence_at, status, detail)


def _normalize_proposal_created_at(proposal_type: str, created_at: datetime) -> datetime:
    """NaverProposal.created_at은 컬럼 내에서 소스가 섞여 있다(파일 상단 실측표 참조) —
    trigger_watch.py가 만드는 KST_STAMPED_PROPOSAL_TYPES(PACING/CPC)만 이미 KST고,
    나머지(proposal_writer.py 발행분 전부, INFORMATIONAL_PROPOSAL_TYPES의 나머지 3종
    포함)는 UTC. proposal_type별로 다르게 정규화해야 한다 — INFORMATIONAL_PROPOSAL_TYPES
    (실행형/정보성 분류)로는 이 구분이 안 된다(둘은 서로 다른 축, 실측 확인).
    """
    if proposal_type in KST_STAMPED_PROPOSAL_TYPES:
        return created_at
    return _utc_to_kst(created_at)


def _latest_proposal_evidence(db: Session) -> datetime | None:
    """전체 기간 최신 증거 시각(오늘 생성분이 없을 때 stale/none 판정용) — 원시 컬럼에
    소스가 섞여 있어 func.max()를 그냥 못 쓴다(_normalize_proposal_created_at 참조).
    UTC 소스 그룹·KST 소스 그룹 각각 원시 최댓값을 구한 뒤 정규화해서 비교한다.
    """
    utc_max_raw = db.query(func.max(NaverProposal.created_at)).filter(
        NaverProposal.proposal_type.notin_(KST_STAMPED_PROPOSAL_TYPES)
    ).scalar()
    kst_max_raw = db.query(func.max(NaverProposal.created_at)).filter(
        NaverProposal.proposal_type.in_(KST_STAMPED_PROPOSAL_TYPES)
    ).scalar()
    candidates = [ts for ts in (_utc_to_kst(utc_max_raw), kst_max_raw) if ts is not None]
    return max(candidates) if candidates else None


def _proposal_stage(db: Session, today: date) -> dict:
    # created_at 소스가 UTC(proposal_writer.py)/KST(trigger_watch.py) 혼재라 KST 자정
    # 경계를 SQL만으로 정확히 못 자른다 — 하루 여유를 둔 넓은 창으로 DB에서 가져온 뒤
    # Python에서 타입별로 정규화(_normalize_proposal_created_at)한 후 정확히 today로
    # 재필터한다. window_start를 today-1 00:00으로 잡으면 UTC 소스(오늘 KST 새벽 = UTC
    # 전날 오후)·KST 소스(오늘 00:00~) 둘 다 여유 있게 포함한다.
    window_start = datetime.combine(today - timedelta(days=1), datetime.min.time())
    rows = db.query(NaverProposal.proposal_type, NaverProposal.created_at).filter(
        NaverProposal.created_at >= window_start
    ).all()
    normalized = [
        (proposal_type, _normalize_proposal_created_at(proposal_type, created_at))
        for proposal_type, created_at in rows
    ]
    today_rows = [(proposal_type, ts) for proposal_type, ts in normalized if ts.date() == today]

    if today_rows:
        total = len(today_rows)
        informational = sum(1 for proposal_type, _ in today_rows if proposal_type in INFORMATIONAL_PROPOSAL_TYPES)
        executable = total - informational
        last_evidence_at = max(ts for _, ts in today_rows)
        detail = f"오늘 생성 {total}건(실행형 {executable}·정보성 {informational})"
        return _stage("proposal", "제안", last_evidence_at, STATUS_OK, detail)

    latest = _latest_proposal_evidence(db)
    if latest is None:
        return _stage("proposal", "제안", None, STATUS_NONE, "생성된 제안 없음")

    detail = f"오늘 생성 0건(최신 생성일 {latest.date().isoformat()})"
    return _stage("proposal", "제안", latest, STATUS_STALE, detail)


def _expert_stage(db: Session, today: date) -> dict:
    # as_of는 kst_today()로 앱 코드가 직접 채우는 달력일이라 보정 불필요 — created_at은
    # expert_ledger.py가 명시 대입 없이 server_default=func.now()에 맡기는 컬럼(실측,
    # 파일 상단 표 참조) → 항상 UTC, 표시 시 _utc_to_kst 정규화.
    runs_today = db.query(NaverExpertReviewRun).filter(NaverExpertReviewRun.as_of == today).all()

    if runs_today:
        last_evidence_at = _utc_to_kst(max(run.created_at for run in runs_today))
        ok_run_ids = [run.id for run in runs_today if run.status == "ok"]
        if ok_run_ids:
            verdict_count = db.query(func.count(NaverExpertReview.id)).filter(
                NaverExpertReview.run_id.in_(ok_run_ids)
            ).scalar() or 0
            detail = f"오늘 run status=ok, 평결 {verdict_count}건"
            return _stage("expert", "전문가", last_evidence_at, STATUS_OK, detail)

        statuses = ",".join(sorted({run.status for run in runs_today}))
        detail = f"오늘 run status={statuses}(ok 없음)"
        return _stage("expert", "전문가", last_evidence_at, STATUS_STALE, detail)

    latest_as_of = db.query(func.max(NaverExpertReviewRun.as_of)).scalar()
    if latest_as_of is None:
        return _stage("expert", "전문가", None, STATUS_NONE, "run 없음")

    detail = f"오늘 run 없음(최신 as_of {latest_as_of.isoformat()})"
    return _stage("expert", "전문가", None, STATUS_NONE, detail)


def _learning_stage(db: Session, today: date) -> dict:
    """학습 스테이지 증거 = NaverLearningState.updated_at 최댓값.

    학습루프 1(proposal_scoreboard)·2(estimate_calibrator)·3(conversion_maturity)·
    5(hourly_pattern) 4개 SA + expert_ledger._upsert_scoreboard가 전부 최종적으로 이
    테이블에 upsert한다(learning_loops.py가 조합) — naver_conversion_maturity_snapshot·
    naver_hourly_pattern_history는 각 루프의 중간 원료 적립 테이블일 뿐 학습 "결과" 자체는
    아니라서, 이 공통 산출 지점인 NaverLearningState가 "학습이 오늘 실행됐는가"를 가장
    정직하게 반영하는 단일 신호다. 5개 writer 전부 updated_at 명시 대입 없이
    onupdate=func.now()에 맡긴다(실측, 파일 상단 표 참조) → 항상 UTC.
    """
    last_evidence_at_raw = db.query(func.max(NaverLearningState.updated_at)).scalar()
    last_evidence_at = _utc_to_kst(last_evidence_at_raw)
    if last_evidence_at is None:
        return _stage("learning", "학습", None, STATUS_NONE, "학습 파라미터 없음")

    count = db.query(func.count(NaverLearningState.id)).scalar() or 0
    status = STATUS_OK if last_evidence_at.date() == today else STATUS_STALE
    detail = f"파라미터 {count}건, 최근 갱신 {last_evidence_at.isoformat()}"
    return _stage("learning", "학습", last_evidence_at, status, detail)


def _optimizer_coverage(db: Session, today: date) -> dict:
    """최근 7일(D-1~D-7) 비용을 campaign_id→naver_campaign_settings.optimizer별 합산.

    오늘(D-0)은 naver_ad_daily가 아직 확정 적재 전일 수 있어 창에서 제외(ad_report·
    diagnosis의 D-1 확정치 기준 관례와 동일). 설정 행이 없는 캠페인은 'none' 취급 —
    naver_execution_harness._resolve_optimizer와 동일 시맨틱(단일 진실 유지).
    campaign_backfill이 심는 BACKFILL_SENTINEL_ADGROUP(180일 백필 그룹 롤업) 행은
    실단위 그룹 데이터가 아니라 커버리지 산정에 넣으면 이중집계 — proposal_pipeline·
    account_diagnosis 등 다른 SA와 동일하게 제외(단일 진실 관례).
    """
    date_to = today - timedelta(days=1)
    date_from = date_to - timedelta(days=COVERAGE_WINDOW_DAYS - 1)

    rows = db.query(
        NaverAdDaily.campaign_id, func.sum(NaverAdDaily.cost)
    ).filter(
        NaverAdDaily.ad_date >= date_from,
        NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    ).group_by(NaverAdDaily.campaign_id).all()

    optimizer_by_campaign = {
        row.campaign_id: row.optimizer for row in db.query(NaverCampaignSettings).all()
    }

    ours_cost = mop_cost = none_cost = 0
    for campaign_id, cost in rows:
        cost = cost or 0
        optimizer = optimizer_by_campaign.get(campaign_id, "none")
        if optimizer == "ours":
            ours_cost += cost
        elif optimizer == "mop":
            mop_cost += cost
        else:
            none_cost += cost

    total_cost = ours_cost + mop_cost + none_cost
    ours_ratio = round(ours_cost / total_cost, 4) if total_cost else 0.0

    return {
        "window_days": COVERAGE_WINDOW_DAYS,
        "ours_cost": ours_cost,
        "mop_cost": mop_cost,
        "none_cost": none_cost,
        "total_cost": total_cost,
        "ours_ratio": ours_ratio,
    }


def build(db: Session, *, today: date | None = None) -> dict:
    """대시보드 개요 — 엔진 파이프라인 5단 상태 + optimizer 커버리지. 읽기 전용."""
    today = today or kst_today()
    return {
        "engine_stages": [
            _ingest_stage(db, today),
            _forecast_stage(db, today),
            _proposal_stage(db, today),
            _expert_stage(db, today),
            _learning_stage(db, today),
        ],
        "optimizer_coverage": _optimizer_coverage(db, today),
    }
