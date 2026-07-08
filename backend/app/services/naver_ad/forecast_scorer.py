# forecast_scorer.py — forecast_scorer SA (예측·전문가 스프린트 F1→F2a, D-NAO-24/26)
# 역할(SA 단일 책임): 어제(target_date) 예측을 오늘 도착한 실측(전날 07:50 forecast_engine이
#   예측을 만든 뒤 그날 하루가 지나면 /stats에 반영됨, grain별 소싱은 forecast_source에 위임
#   — 원칙18-6)과 대조해 MAPE를 채우고, 최근 성적이 계속 나쁜 모델을 자동 강등(demoted)한다.
#   강등은 demoted_until까지 쿨다운 — forecast_gate가 그 사이엔 재평가로 되돌리지 않는다.
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import NaverForecastDaily, NaverForecastModel
from app.services.naver_ad import forecast_source
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

_Q4 = Decimal("0.0001")
ROLLING_WINDOW = 7  # 최근 N건 스코어링된 예측으로 롤업 MAPE 산출
MIN_SCORED_SAMPLES = 3  # 강등 판단에 필요한 최소 표본(롤업 신뢰도 확보)
DEMOTE_MAPE_THRESHOLD = Decimal("0.50")  # 초기 상수 — F1 백테스트로 튜닝 대상(계획서 §6-2)
DEMOTE_COOLDOWN_DAYS = 3


def _mape(pred: int, actual: int) -> Decimal:
    """실측 0인데 예측도 0이면 완전 일치(0). 실측 0인데 예측>0이면 정의상 상한(1.0)으로 캡."""
    if actual == 0:
        return Decimal(0) if pred == 0 else Decimal(1)
    return (Decimal(abs(pred - actual)) / Decimal(actual)).quantize(_Q4)


def _rollup_mape(db: Session, *, grain: str, scope_key: str) -> dict | None:
    rows = db.query(NaverForecastDaily).filter(
        NaverForecastDaily.grain == grain, NaverForecastDaily.scope_key == scope_key,
        NaverForecastDaily.scored_at.isnot(None),
    ).order_by(NaverForecastDaily.target_date.desc()).limit(ROLLING_WINDOW).all()
    row_avgs = []
    for r in rows:
        vals = [v for v in (r.mape_clk, r.mape_cost, r.mape_conv_amt) if v is not None]
        if vals:
            row_avgs.append(sum(vals) / len(vals))
    if not row_avgs:
        return None
    return {"avg_mape": (sum(row_avgs) / len(row_avgs)).quantize(_Q4), "sample_n": len(row_avgs)}


def score_target_date(db: Session, *, target_date: date, today: date) -> dict:
    """target_date(보통 today-1)의 미채점 예측을 실측과 대조해 채점 + 강등 판정.

    실측이 아직 도착 안 했으면(campaign_backfill 재실행이 지연됐거나 API 결측) 해당 행은
    건너뛰고 다음 실행에서 재시도된다(scored_at이 NULL로 남아 자연스럽게 재시도 대상).
    """
    pending = db.query(NaverForecastDaily).filter(
        NaverForecastDaily.target_date == target_date,
        NaverForecastDaily.actual_clk.is_(None),
    ).all()

    scored_scopes: set[tuple[str, str]] = set()
    for row in pending:
        series = forecast_source.daily_series(
            db, grain=row.grain, scope_key=row.scope_key, date_from=target_date, date_to=target_date,
        )
        actual = series.get(target_date)
        if actual is None:
            continue
        row.actual_clk = actual["clk"]
        row.actual_cost = actual["cost"]
        row.actual_conv_amt = actual["conv_amt"]
        row.mape_clk = _mape(row.pred_clk, actual["clk"])
        row.mape_cost = _mape(row.pred_cost, actual["cost"])
        row.mape_conv_amt = _mape(row.pred_conv_amt, actual["conv_amt"])
        row.scored_at = kst_now()
        scored_scopes.add((row.grain, row.scope_key))
    db.commit()

    demoted: list[str] = []
    for grain, scope_key in scored_scopes:
        rollup = _rollup_mape(db, grain=grain, scope_key=scope_key)
        if rollup is None:
            continue
        model_row = db.query(NaverForecastModel).filter(
            NaverForecastModel.grain == grain, NaverForecastModel.scope_key == scope_key,
        ).first()
        if model_row is None:
            continue
        model_row.recent_mape = rollup["avg_mape"]
        if rollup["sample_n"] >= MIN_SCORED_SAMPLES and rollup["avg_mape"] > DEMOTE_MAPE_THRESHOLD:
            model_row.gate_status = "demoted"
            model_row.demoted_until = today + timedelta(days=DEMOTE_COOLDOWN_DAYS)
            demoted.append(scope_key)
    db.commit()

    log.info(
        "forecast_scorer: %s 채점 %d/%d건, 강등 %d개 (%s)",
        target_date, len(scored_scopes), len(pending), len(demoted), demoted,
    )
    return {
        "target_date": target_date.isoformat(), "scored": len(scored_scopes),
        "pending": len(pending), "unscored_pending": len(pending) - len(scored_scopes),
        "demoted": demoted,
    }


def run_daily(db: Session, *, today: date) -> dict:
    """어제(today-1) 예측을 오늘 채점 — forecast_engine이 매일 이 순서로 호출."""
    return score_target_date(db, target_date=today - timedelta(days=1), today=today)
