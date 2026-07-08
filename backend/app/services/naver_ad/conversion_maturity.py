# conversion_maturity.py — conversion_maturity SA (듀얼모드 스프린트 Phase 6, D-NAO-14 학습루프3)
# 역할(SA): naver_ad_daily의 직접·간접 전환매출이 ad_date로부터 며칠(days_since) 지나야
#   "성숙"(더 이상 안 늘어남)하는지 실측 곡선 m(d)을 쌓는다. naver_ad_daily는 upsert라 이력이
#   안 남으므로(모델 docstring), 매일 이 SA가 관측시점 스냅샷을 naver_conversion_maturity_
#   snapshot에 별도 적립 — 오늘 하루 실행으로는 곡선이 안 나오고, MATURITY_DAYS일만큼 매일
#   쌓여야 첫 코호트가 성숙해 m(d) 산출이 가능해진다(정직 경계, 추정으로 대체하지 않음).
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverConversionMaturitySnapshot, NaverLearningState
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

_Q4 = Decimal("0.0001")
METRIC = "conv_delay"
# D-NAO-20-②가 "m(d)≥0.8"의 판정 대기일을 실측으로 대체하려는 목표 — 간접전환 지연 특성상
# 14일(기존 D+14 검증 관행)보다 넉넉히 잡아야 진짜 성숙점을 놓치지 않는다(과소평가 방지).
MATURITY_DAYS = 21
MIN_COHORTS_FOR_CURVE = 3  # 곡선 신뢰를 위한 최소 성숙 코호트 수(모수게이트, 자의적 상수 최소화 — 통계적 유의성 검정은 아님)


def _account_conv_amt_on(db: Session, ad_date: date) -> dict:
    """특정 ad_date의 계정 전체(전 campaign_type) 직접·간접 전환매출 합계(현재 시점 관측값)."""
    row = (
        db.query(
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
        )
        .filter(NaverAdDaily.ad_date == ad_date, NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP)
        .first()
    )
    direct, indirect = int(row[0]), int(row[1])
    return {"direct_amt": direct, "indirect_amt": indirect, "total_amt": direct + indirect}


def take_daily_snapshot(db: Session, *, today: date | None = None) -> dict:
    """오늘 시점에서 [today-MATURITY_DAYS, today] 창의 각 ad_date를 관측 — days_since=
    (today-ad_date)로 upsert. 같은 (ad_date, days_since)가 이미 있으면 갱신(같은 날 재실행
    멱등, hourly_snapshot 전례). 반환: {"rows_upserted": int, "today": iso}."""
    today = today or kst_today()
    upserted = 0
    for days_since in range(MATURITY_DAYS + 1):
        ad_date = today - timedelta(days=days_since)
        # naver_ad_daily에 이 ad_date 행 자체가 없으면(미수집/07:30 크론 아직 안 돎) 스킵 —
        # "진짜 전환 0원 확정"과 "아직 안 채워짐"을 구분 못 하면 곡선이 왜곡된다. days_since=0
        # (오늘)도 예외 없이 동일 규칙 적용(오늘 데이터가 아직 안 들어왔으면 관측 자체가 불가).
        existing_any = (
            db.query(sqlfunc.count(NaverAdDaily.id))
            .filter(NaverAdDaily.ad_date == ad_date).scalar() or 0
        )
        if existing_any == 0:
            continue
        amt = _account_conv_amt_on(db, ad_date)
        row = db.query(NaverConversionMaturitySnapshot).filter(
            NaverConversionMaturitySnapshot.ad_date == ad_date,
            NaverConversionMaturitySnapshot.days_since == days_since,
        ).first()
        if row is None:
            row = NaverConversionMaturitySnapshot(ad_date=ad_date, days_since=days_since)
            db.add(row)
        row.direct_amt = amt["direct_amt"]
        row.indirect_amt = amt["indirect_amt"]
        row.total_amt = amt["total_amt"]
        upserted += 1
    db.commit()
    return {"rows_upserted": upserted, "today": today.isoformat()}


def compute_curve(db: Session) -> dict:
    """성숙 코호트(ad_date에 days_since=MATURITY_DAYS 스냅샷이 있는 것)만으로 m(d) 평균곡선
    산출. 반환: {"curve": {days_since: m(d)}, "cohort_n": int, "skipped_reason": str|None}.
    성숙 코호트가 MIN_COHORTS_FOR_CURVE 미만이면 곡선 없음(정직 경계 — 부족한 데이터로
    허구의 곡선을 만들지 않는다).
    """
    mature_ad_dates = [
        r[0] for r in db.query(NaverConversionMaturitySnapshot.ad_date)
        .filter(NaverConversionMaturitySnapshot.days_since == MATURITY_DAYS).all()
    ]
    if len(mature_ad_dates) < MIN_COHORTS_FOR_CURVE:
        return {
            "curve": {}, "cohort_n": len(mature_ad_dates),
            "skipped_reason": f"성숙 코호트 {len(mature_ad_dates)}개 < 최소 {MIN_COHORTS_FOR_CURVE}개",
        }

    rows = db.query(NaverConversionMaturitySnapshot).filter(
        NaverConversionMaturitySnapshot.ad_date.in_(mature_ad_dates)
    ).all()
    by_cohort: dict[date, dict[int, int]] = {}
    for r in rows:
        by_cohort.setdefault(r.ad_date, {})[r.days_since] = r.total_amt

    ratios_by_day: dict[int, list[Decimal]] = {d: [] for d in range(MATURITY_DAYS + 1)}
    for ad_date, by_day in by_cohort.items():
        mature_amt = by_day.get(MATURITY_DAYS)
        if not mature_amt:
            continue  # 성숙 시점 매출이 0이면 비율 미정의(신규 저볼륨일 — 이 코호트는 곡선 계산에서 제외)
        for d, amt in by_day.items():
            ratios_by_day[d].append(Decimal(amt) / Decimal(mature_amt))

    curve = {
        d: float((sum(vals) / Decimal(len(vals))).quantize(_Q4))
        for d, vals in ratios_by_day.items() if vals
    }
    return {"curve": curve, "cohort_n": len(mature_ad_dates), "skipped_reason": None}


def _upsert_learning_state(db: Session, *, scope_key: str, value: Decimal, sample_n: int, confidence: Decimal) -> None:
    row = db.query(NaverLearningState).filter(
        NaverLearningState.scope == "global", NaverLearningState.scope_key == scope_key,
        NaverLearningState.metric == METRIC,
    ).first()
    if row is None:
        row = NaverLearningState(scope="global", scope_key=scope_key, metric=METRIC)
        db.add(row)
    row.current_value = value
    row.sample_n = sample_n
    row.confidence = confidence


def run_daily(db: Session, *, today: date | None = None) -> dict:
    """매일 실행 — ①오늘 시점 스냅샷 적립 ②성숙 코호트 충분하면 곡선 산출해
    NaverLearningState(scope=global, scope_key=f"day_{d}", metric=conv_delay)에 day별로 기록.
    곡선 없음(코호트 부족)이면 학습값을 건드리지 않는다(기존값 보존, estimate_calibrator와
    동일 원칙)."""
    snap_result = take_daily_snapshot(db, today=today)
    curve_result = compute_curve(db)
    if not curve_result["curve"]:
        log.info("conversion_maturity: 곡선 산출 스킵(%s) — 기존 학습값 유지", curve_result["skipped_reason"])
        return {**snap_result, **curve_result}

    confidence = min(Decimal(1), Decimal(curve_result["cohort_n"]) / Decimal(MIN_COHORTS_FOR_CURVE * 3))
    for d, m_d in curve_result["curve"].items():
        _upsert_learning_state(
            db, scope_key=f"day_{d}", value=Decimal(str(m_d)),
            sample_n=curve_result["cohort_n"], confidence=confidence.quantize(_Q4),
        )
    db.commit()
    log.info("conversion_maturity: 곡선 갱신 cohort_n=%d", curve_result["cohort_n"])
    return {**snap_result, **curve_result}
