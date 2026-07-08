# estimate_calibrator.py — estimate_calibrator SA (듀얼모드 스프린트 Phase 6, D-NAO-14 학습루프2)
# 역할(SA): 네이버 /estimate/performance-bulk의 예측클릭이 실측(naver_ad_daily) 대비 어느
#   방향·크기로 계통 편향돼 있는지 측정해 NaverLearningState(metric=estimate_bias)에 축적한다.
#   순수 함수형 흐름 — fetcher(estimate API)와 db read만, 광고 API 쓰기 없음(D-3).
#
#   비교 방법(추정 금지 — 실측 가능한 유일한 기준): 후보 키워드의 **현재 입찰가(bid_amt)**로
#   estimate_performance를 호출해 예측클릭을 받고, 같은 키워드의 naver_ad_daily 최근 실측
#   일평균 클릭과 비교한다. "현재 입찰가"를 쓰는 이유 — 이 시스템은 아직 실제 입찰을 바꾼 적이
#   없어(D-NAO-5 영구 게이트, naver_execution_harness가 항상 dry-run) 키워드의 실제 집행
#   조건(입찰가)이 안정적으로 유지돼 왔다는 게 유일하게 검증 가능한 전제다 — recommended_bid
#   (아직 적용 안 된 가상의 입찰가)로 비교하면 실측 자체가 다른 조건에서 나온 값이라 의미가 없다.
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverEntity, NaverLearningState
from app.services.naver_ad import growth_sweeper
from app.services.naver_ad.account_diagnosis import LOW_CLICK_THRESHOLD
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services import naver_sa_ad_fetcher as fetcher
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

_Q4 = Decimal("0.0001")
METRIC = "estimate_bias"
LOOKBACK_DAYS = 15  # 계정 리포트 기본 창(D-NAO 계획서 §2)과 동일 — 실측 일평균 클릭 산정 구간
SAMPLE_BUDGET = 200  # /estimate/performance-bulk 회당 상한(실측)과 동일 — API 예산 통제(growth_sweeper 전례)


def _candidate_keywords(db: Session, date_from: date, date_to: date) -> list[dict]:
    """전 활성 WEB_SITE 키워드 중 실측 클릭이 모수게이트(D-NAO-9)를 넘는 것만 후보로 산출.

    반환: [{"keyword_id","name","bid_amt","actual_avg_daily_clk"}], 클릭 내림차순
    (상위 SAMPLE_BUDGET만 estimate 호출 대상 — 전수 호출 금지, 회당 API 예산 통제).
    """
    rows = (
        db.query(
            NaverAdDaily.keyword_id,
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        )
        .filter(
            NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
            NaverAdDaily.campaign_type == growth_sweeper.WEB_SITE,
            NaverAdDaily.keyword_id != "",
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.keyword_id)
        .all()
    )
    n_days = (date_to - date_from).days + 1
    clk_by_kw = {kw: int(clk) for kw, clk in rows if int(clk) >= LOW_CLICK_THRESHOLD}
    if not clk_by_kw:
        return []

    entities = (
        db.query(NaverEntity.entity_id, NaverEntity.name, NaverEntity.bid_amt)
        .filter(
            NaverEntity.entity_type == "keyword", NaverEntity.status == "on",
            NaverEntity.entity_id.in_(clk_by_kw.keys()),
        )
        .all()
    )
    out = [
        {
            "keyword_id": kw_id, "name": name, "bid_amt": bid_amt,
            "actual_avg_daily_clk": Decimal(clk_by_kw[kw_id]) / Decimal(n_days),
        }
        for kw_id, name, bid_amt in entities
        if name and bid_amt is not None and bid_amt > 0
    ]
    out.sort(key=lambda c: c["actual_avg_daily_clk"], reverse=True)
    return out[:SAMPLE_BUDGET]


def measure_estimate_bias(db: Session, *, as_of: date | None = None) -> dict:
    """후보 키워드의 현재 입찰가 기준 예측클릭 vs 실측 일평균클릭 비율(actual/predicted)을
    측정해 campaign_type="WEB_SITE" 단일 버킷(현재 키워드 grain이 있는 유일한 유형)으로
    집계한다. 반환: {"sample_n": int, "ratio": float|None, "skipped_reason": str|None}.

    ratio=None(sample_n=0)은 후보 자체가 없거나(모수게이트 미달) estimate 호출이 전부
    실패한 경우 — 정직하게 신호 없음으로 반환(자의적 기본값 대체 없음).
    """
    as_of = as_of or kst_today()
    date_from = as_of - timedelta(days=LOOKBACK_DAYS - 1)  # LOOKBACK_DAYS일 inclusive 창(proposal_pipeline 관례)
    candidates = _candidate_keywords(db, date_from, as_of)
    if not candidates:
        return {"sample_n": 0, "ratio": None, "skipped_reason": "모수게이트 통과 후보 없음"}

    items = [
        {"keyword": c["name"], "bid": c["bid_amt"], "keywordplus": False, "device": "MOBILE"}
        for c in candidates
    ]
    try:
        results = fetcher.estimate_performance(items)
    except Exception as e:  # noqa: BLE001 — estimate 실패는 이번 회차 측정 스킵, 파이프라인 중단 아님
        log.warning("estimate_calibrator: estimate_performance 호출 실패: %s", e)
        return {"sample_n": 0, "ratio": None, "skipped_reason": f"estimate 호출 실패: {e}"}

    predicted_by_key = {(r["keyword"], r["bid"]): r.get("clicks") for r in results}
    ratios: list[Decimal] = []
    for c in candidates:
        predicted = predicted_by_key.get((c["name"], c["bid_amt"]))
        if predicted is None or predicted <= 0:
            continue
        ratios.append(c["actual_avg_daily_clk"] / Decimal(predicted))

    if not ratios:
        return {"sample_n": 0, "ratio": None, "skipped_reason": "예측클릭 응답 없음/0"}

    avg_ratio = (sum(ratios) / Decimal(len(ratios))).quantize(_Q4)
    return {"sample_n": len(ratios), "ratio": float(avg_ratio), "skipped_reason": None}


def _upsert_learning_state(
    db: Session, *, scope: str, scope_key: str, metric: str, value: Decimal, sample_n: int, confidence: Decimal,
) -> NaverLearningState:
    row = db.query(NaverLearningState).filter(
        NaverLearningState.scope == scope, NaverLearningState.scope_key == scope_key,
        NaverLearningState.metric == metric,
    ).first()
    if row is None:
        row = NaverLearningState(scope=scope, scope_key=scope_key, metric=metric)
        db.add(row)
    row.current_value = value
    row.sample_n = sample_n
    row.confidence = confidence
    return row


def run_daily(db: Session, *, as_of: date | None = None) -> dict:
    """매일 실행 — 측정 후 NaverLearningState(scope=keyword_type, scope_key=WEB_SITE,
    metric=estimate_bias)에 축적. 신호 없음(sample_n=0)이면 기존 학습값을 건드리지 않는다
    (부정확한 0/None으로 덮어써 이전에 쌓인 유효한 학습을 지우지 않음)."""
    result = measure_estimate_bias(db, as_of=as_of)
    if result["sample_n"] == 0:
        log.info("estimate_calibrator: 측정 스킵(%s) — 기존 학습값 유지", result["skipped_reason"])
        db.commit()
        return result

    confidence = min(Decimal(1), Decimal(result["sample_n"]) / Decimal(SAMPLE_BUDGET))
    _upsert_learning_state(
        db, scope="keyword_type", scope_key=growth_sweeper.WEB_SITE, metric=METRIC,
        value=Decimal(str(result["ratio"])), sample_n=result["sample_n"], confidence=confidence.quantize(_Q4),
    )
    db.commit()
    log.info("estimate_calibrator: WEB_SITE bias=%s (n=%d)", result["ratio"], result["sample_n"])
    return result
