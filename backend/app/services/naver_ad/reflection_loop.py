# reflection_loop.py — reflection_loop Harness (D-NAO-54 P2, docs/PLAN_naver-ad-diary-wisdom.md §P2)
# 역할: outcome_backfill_sa(C1)·daily_reflection_sa(C2)를 조합해 08:35 일일 해석 체인으로
#   조립. ①먼저 어제/그제/D-8 결과를 소급 기입(backfill) → ②그 최신 결과로 해석문 생성.
#   SA간 직접 호출 금지(원칙18) — 이 Harness가 유일한 정보 유통 허브. retro_scoring_loop의
#   stage_status 단계 격리 패턴을 그대로 따른다(한 단계 실패해도 나머지 계속).
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.services.naver_ad.diary_outcome import backfill_outcomes
from app.services.naver_ad.diary_reflection import build_reflection
from app.utils.kst import kst_now

log = logging.getLogger(__name__)


def run_daily_reflection(db: Session, *, now: datetime | None = None) -> dict:
    """08:35 엔트리 — ①backfill_outcomes ②build_reflection(단계 격리).

    now: as-of 관통 파라미터(미지정 시 kst_now) — 백테스트/catch-up에서 호출자 시각을 존중.
    """
    now = now or kst_now()
    result: dict = {"stage_status": {}}

    try:
        result["backfill"] = backfill_outcomes(db, now=now)
        result["stage_status"]["outcome_backfill"] = "ok"
    except Exception as e:  # noqa: BLE001 — 한 단계 실패가 나머지를 막지 않음
        db.rollback()  # 실패 트랜잭션 정리 — 공유 세션 오염 방지(retro_scoring_loop 전례)
        log.exception("reflection_loop outcome_backfill 단계 실패: %s", e)
        result["stage_status"]["outcome_backfill"] = "failed"
        result["backfill"] = {"error": str(e)}

    try:
        result["reflection"] = build_reflection(db, now=now)
        result["stage_status"]["daily_reflection"] = "ok"
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("reflection_loop daily_reflection 단계 실패: %s", e)
        result["stage_status"]["daily_reflection"] = "failed"
        result["reflection"] = {"error": str(e)}

    return result
