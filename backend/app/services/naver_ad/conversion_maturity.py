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


# 성숙 환산 배수 상한 — 곡선이 망가져(m(d)가 0에 가깝게) 상한을 폭주시키는 것을 막는 fail-safe.
# 3배 = 관측 시점에 최종 매출의 1/3만 보이는 경우까지 허용. 그보다 심한 지연은 곡선을 의심한다.
_MAX_MATURITY_MULTIPLIER = Decimal("3")


def load_curve(db: Session) -> dict[int, Decimal]:
    """m(d) 곡선 로드 — {days_since: 성숙(D+21) 대비 그날 시점 관측 비율}. 미산출이면 빈 dict.

    run_daily가 NaverLearningState(scope="global", scope_key="day_<d>", metric="conv_delay")에
    적립한 것을 그대로 읽는다. 코호트 부족으로 곡선이 없으면 빈 dict → 소비자는 보정 없이(배수 1.0)
    기존 동작을 유지한다(fail-safe — 없는 곡선을 추정으로 만들지 않는다)."""
    rows = (
        db.query(NaverLearningState.scope_key, NaverLearningState.current_value)
        .filter(
            NaverLearningState.scope == "global",
            NaverLearningState.metric == METRIC,
            NaverLearningState.current_value.isnot(None),
        )
        .all()
    )
    out: dict[int, Decimal] = {}
    for scope_key, value in rows:
        if not scope_key or not scope_key.startswith("day_"):
            continue
        try:
            out[int(scope_key[4:])] = Decimal(value)
        except (ValueError, TypeError):
            continue
    return out


def maturity_multiplier(curve: dict[int, Decimal], days_since: int) -> Decimal:
    """관측 매출 → **성숙 시점 추정 매출**로 환산하는 배수(= 1/m(d)).

    ★왜 필요한가: 전환은 ad_date로부터 며칠에 걸쳐 정착하므로, 최근 며칠의 conv_amt는 항상
    과소계상 상태다. 그 값을 그대로 쓰면 최근 성과가 실제보다 나쁘게 보인다. 수요가 평평할 때는
    이 편향이 작지만 **수요가 오르는 구간(신제품 런칭 등)에서는 가장 뜨거운 최근 며칠이 가장 덜
    집계된 상태로 들어와** 계통적 하향 편향이 된다 — 그리고 신규 상품은 **모든 데이터가 최근**이라
    편향을 100% 맞는다. 이 보정은 그래서 신규/런칭 상품에 가장 크게 작동한다(의도된 표적성).

    fail-safe 규칙(하나라도 걸리면 배수 1.0 = 보정 없음, 기존 동작):
      · 곡선 없음(코호트 부족)  · days_since ≥ MATURITY_DAYS(이미 성숙)
      · m(d)가 0 이하(정의 불가)  · m(d) ≥ 1(이미 다 찼거나 이상값)
    상한은 _MAX_MATURITY_MULTIPLIER — 망가진 곡선이 이익 상한을 폭주시키지 못하게 한다."""
    if days_since < 0 or days_since >= MATURITY_DAYS or not curve:
        return Decimal(1)
    m = curve.get(days_since)
    if m is None or m <= 0 or m >= 1:
        return Decimal(1)
    return min(Decimal(1) / m, _MAX_MATURITY_MULTIPLIER)


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
