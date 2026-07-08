# forecast_engine.py — forecast_engine Harness (예측·전문가 스프린트 F1→F2a, D-NAO-24/26)
# 역할: grain(campaign/adgroup/keyword, F2a로 확장) 예측 SA 3개(forecast_gate는 model_builder가
#   내부에서 호출)를 ①최근 창 재백필(캠페인 시계열 신선도 유지) ②forecast_model_builder(오늘
#   예측 생성) ③forecast_scorer(어제 예측 채점+자동강등) 순서로 조합한다. learning_loops.py와
#   동일 단계격리 원칙(원칙18-6) — 한 단계 실패가 나머지를 막지 않고 stage_status로
#   관측 가능하게 남긴다.
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import NaverEntity
from app.services.naver_ad import forecast_model_builder, forecast_scorer
from app.services.naver_ad.campaign_backfill import backfill_campaign_daily
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

# 매일 최근 N일만 재백필 — 대량 180일 재조회 없이 어제치까지 시계열을 최신 유지(idempotent 교체).
# LOOKBACK_DAYS(14)+여유를 커버해 게이트 판정이 항상 최신 데이터를 보게 한다.
REFRESH_WINDOW_DAYS = 16


def _active_scopes(db: Session) -> list[tuple[str, str]]:
    """status='on'인 campaign·adgroup·keyword 엔티티를 (grain, scope_key) 목록으로."""
    rows = db.query(NaverEntity.entity_type, NaverEntity.entity_id).filter(
        NaverEntity.entity_type.in_(("campaign", "adgroup", "keyword")), NaverEntity.status == "on",
    ).all()
    return [(r[0], r[1]) for r in rows]


def run_daily(db: Session, *, today: date | None = None) -> dict:
    """①시계열 재백필 ②오늘 예측 생성 ③어제 예측 채점. 각 단계 독립 try/except."""
    today = today or kst_today()
    result: dict = {"stage_status": {}, "errors": []}

    try:
        refresh_to = today - timedelta(days=1)
        refresh_from = refresh_to - timedelta(days=REFRESH_WINDOW_DAYS - 1)
        result["backfill_refresh"] = backfill_campaign_daily(db, refresh_from, refresh_to)
        result["stage_status"]["backfill_refresh"] = "ok"
    except Exception as e:  # noqa: BLE001 — 단계격리, 재백필 실패해도 기존 시계열로 아래 단계 진행
        db.rollback()
        log.exception("forecast_engine: backfill_refresh 단계 실패: %s", e)
        result["stage_status"]["backfill_refresh"] = "failed"
        result["errors"].append(f"backfill_refresh: {e}")

    try:
        scopes = _active_scopes(db)
        result["model_builder"] = forecast_model_builder.run_daily(db, scopes, today=today)
        result["stage_status"]["model_builder"] = "ok"
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("forecast_engine: model_builder 단계 실패: %s", e)
        result["stage_status"]["model_builder"] = "failed"
        result["errors"].append(f"model_builder: {e}")

    try:
        result["scorer"] = forecast_scorer.run_daily(db, today=today)
        result["stage_status"]["scorer"] = "ok"
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("forecast_engine: scorer 단계 실패: %s", e)
        result["stage_status"]["scorer"] = "failed"
        result["errors"].append(f"scorer: {e}")

    log.info("forecast_engine run_daily: %s", result["stage_status"])
    return result
