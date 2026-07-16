# retro_scoring_loop.py — retro_scoring_loop Harness (D-NAO-45, PLAN_naver-ad-retro-scoring §5-C4)
# 역할: retro_snapshotter(C1)·retro_scorer(C2)·retro_pacing_scorer(C3)를 조합해 08:30
#   일일 소급 채점 체인으로 조립. SA간 직접 호출 금지(원칙18) — 이 Harness가 유일한 정보
#   유통 허브. proposal_pipeline.run_daily의 stage_status 패턴을 그대로 따른다(각 단계
#   독립 try/except — 한 단계가 죽어도 나머지는 계속 진행, partial-failure 격리).
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.services.naver_ad import retro_pacing_scorer, retro_scorer, retro_snapshotter
from app.utils.kst import kst_today

log = logging.getLogger(__name__)


def run_daily_retro(db: Session, today: date | None = None) -> dict:
    """08:30 엔트리 — ①snapshot_signals(today-1) ②score_due(today) ③score_alerts(today-1).

    today: as-of 관통 파라미터(D-NAO-44 codex R1 버그 클래스 회피 — 벽시계 kst_today()를
    하드콜하면 리플레이/백테스트에서 호출자의 시뮬레이션 날짜가 무시된다) — 미지정 시
    kst_today().
    """
    today = today or kst_today()
    result: dict = {"stage_status": {}}

    try:
        result["snapshot"] = retro_snapshotter.snapshot_signals(db, today - timedelta(days=1))
        result["stage_status"]["snapshotter"] = "ok"
    except Exception as e:  # noqa: BLE001
        log.exception("retro_scoring_loop snapshotter 단계 실패: %s", e)
        result["stage_status"]["snapshotter"] = "failed"
        result["snapshot"] = {"error": str(e)}

    try:
        result["scored"] = retro_scorer.score_due(db, today)
        result["stage_status"]["scorer"] = "ok"
    except Exception as e:  # noqa: BLE001
        log.exception("retro_scoring_loop scorer 단계 실패: %s", e)
        result["stage_status"]["scorer"] = "failed"
        result["scored"] = {"error": str(e)}

    try:
        result["pacing"] = retro_pacing_scorer.score_alerts(db, today - timedelta(days=1))
        result["stage_status"]["pacing_scorer"] = "ok"
    except Exception as e:  # noqa: BLE001
        log.exception("retro_scoring_loop pacing_scorer 단계 실패: %s", e)
        result["stage_status"]["pacing_scorer"] = "failed"
        result["pacing"] = {"error": str(e)}

    return result


def backfill(db: Session, date_from: date, date_to: date, today: date | None = None) -> dict:
    """as-of date_from~date_to 순회 스냅샷 + 1회 catch-up 채점(idempotent라 안전, 배포 후
    1회 실행 — Jino 배포 단계).

    score_due/score_alerts 자체가 "밀린 것 전부" catch-up 쿼리라(PLAN §4-C2/C3) 매 as-of마다
    부를 필요가 없다 — 구간 전체 스냅샷 후 마지막에 한 번만 돌려 중복 스캔을 피한다.
    """
    today = today or kst_today()
    result: dict = {"days": [], "stage_status": {}}

    d = date_from
    while d <= date_to:
        try:
            snap = retro_snapshotter.snapshot_signals(db, d)
            result["days"].append({"asof": d.isoformat(), "snapshot": snap})
        except Exception as e:  # noqa: BLE001 — 하루 실패가 나머지 구간을 막으면 안 됨
            log.exception("retro_scoring_loop backfill snapshot 실패(asof=%s): %s", d, e)
            result["days"].append({"asof": d.isoformat(), "error": str(e)})
        d += timedelta(days=1)

    try:
        result["scored"] = retro_scorer.score_due(db, today)
        result["stage_status"]["scorer"] = "ok"
    except Exception as e:  # noqa: BLE001
        log.exception("retro_scoring_loop backfill scorer 실패: %s", e)
        result["stage_status"]["scorer"] = "failed"
        result["scored"] = {"error": str(e)}

    try:
        result["pacing"] = retro_pacing_scorer.score_alerts(db, date_to)
        result["stage_status"]["pacing_scorer"] = "ok"
    except Exception as e:  # noqa: BLE001
        log.exception("retro_scoring_loop backfill pacing_scorer 실패: %s", e)
        result["stage_status"]["pacing_scorer"] = "failed"
        result["pacing"] = {"error": str(e)}

    return result
