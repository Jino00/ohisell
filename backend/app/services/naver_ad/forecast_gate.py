# forecast_gate.py — forecast_gate SA (예측·전문가 스프린트 F1→F2a, D-NAO-24/26)
# 역할(SA 단일 책임): grain(campaign/adgroup/keyword, F2a로 확장)별 예측 모델 게이트 판정 —
#   최근 활동일 비율로 데이터 충분성(active/fallback)을 매일 재평가한다. MOP 게이트(14일
#   80% 운영)의 우리 번안 — 임계값은 초기 상수(F1 백테스트로 튜닝 대상, 계획서 §6-2).
#   forecast_scorer가 내린 강등(demoted)은 demoted_until 쿨다운이 남아 있는 동안 이 평가로
#   즉시 덮어쓰지 않는다(스코어러 판단의 유효기간 보장). 시계열 소싱은 forecast_source에 위임
#   (원칙18-6, grain별 소스 라우팅 중복 금지).
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import NaverForecastModel
from app.services.naver_ad import forecast_source

LOOKBACK_DAYS = 14  # MOP 14일 운영 이력 게이트의 우리 번안
MIN_ACTIVE_RATIO = Decimal("0.8")  # 초기 상수 — F1 백테스트로 튜닝(계획서 §6-2)


def evaluate(db: Session, *, grain: str, scope_key: str, today: date) -> dict:
    """최근 LOOKBACK_DAYS일의 활동일 비율로 게이트 상태를 산출.

    grain은 forecast_source.SUPPORTED_GRAINS(campaign/adgroup/keyword)만 지원.
    현재 demoted 쿨다운이 아직 유효하면(demoted_until >= today) 재평가하지 않고 'demoted' 유지.
    """
    if grain not in forecast_source.SUPPORTED_GRAINS:
        raise ValueError(f"F2는 campaign/adgroup/keyword grain만 지원: {grain}")

    existing = db.query(NaverForecastModel).filter(
        NaverForecastModel.grain == grain, NaverForecastModel.scope_key == scope_key,
    ).first()
    if existing and existing.gate_status == "demoted" and existing.demoted_until and existing.demoted_until >= today:
        return {
            "grain": grain, "scope_key": scope_key, "gate_status": "demoted",
            "active_days": existing.sample_days, "lookback_days": LOOKBACK_DAYS,
            "reason": f"scorer 강등 쿨다운 유지({existing.demoted_until}까지)",
        }

    date_from = today - timedelta(days=LOOKBACK_DAYS)
    date_to = today - timedelta(days=1)
    active_days = forecast_source.active_days(db, grain=grain, scope_key=scope_key, date_from=date_from, date_to=date_to)

    ratio = Decimal(active_days) / Decimal(LOOKBACK_DAYS)
    gate_status = "active" if ratio >= MIN_ACTIVE_RATIO else "fallback"
    return {
        "grain": grain, "scope_key": scope_key, "gate_status": gate_status,
        "active_days": active_days, "lookback_days": LOOKBACK_DAYS,
        "reason": f"활동일 {active_days}/{LOOKBACK_DAYS}일 (기준 {MIN_ACTIVE_RATIO})",
    }
