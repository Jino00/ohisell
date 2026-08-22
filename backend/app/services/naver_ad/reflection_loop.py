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
from app.services.naver_ad.reflection_health import record_run_status
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
        refl = build_reflection(db, now=now)
        result["reflection"] = refl
        # ★D-NAO-228: build_reflection은 fail-open이라 LLM 실패도 «예외 없이» {"error": ...}로
        #   돌아온다. 예외만 failed로 적던 초판은 성공·재료없음 skip·LLM 실패를 전부 'ok'로
        #   기록했고, 그래서 2026-07-18~08-22 결번 19일이 로그에서 안 보였다(계약 §3).
        #   반환값을 «판독»해야 로그가 사실을 말한다.
        if refl.get("error"):
            result["stage_status"]["daily_reflection"] = "failed"
            _record(db, "failed", detail=str(refl.get("error")), entries=refl.get("entries"), now=now)
        elif refl.get("skipped"):
            reason = str(refl["skipped"])
            result["stage_status"]["daily_reflection"] = f"skipped:{reason}"
            # already_exists는 catch-up 이중 방지라 결번이 아니다 — 그 날엔 산출물 행이 이미 있다.
            if reason != "already_exists":
                _record(db, "skipped_no_material", detail=reason, entries=0, now=now)
        else:
            result["stage_status"]["daily_reflection"] = "ok"
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("reflection_loop daily_reflection 단계 실패: %s", e)
        result["stage_status"]["daily_reflection"] = "failed"
        result["reflection"] = {"error": str(e)}
        _record(db, "failed", detail=str(e), entries=None, now=now)

    return result


def _record(db: Session, status: str, *, detail, entries, now) -> None:
    """상태 행 기록 — 이 기록의 실패가 잡을 죽이면 안 된다(반성은 관찰 전용, fail-open 계약)."""
    try:
        record_run_status(db, status, detail=detail, entries=entries, now=now)
    except Exception as e:  # noqa: BLE001
        log.warning("reflection_loop: 상태 행 기록 실패(fail-open): %s", e)
