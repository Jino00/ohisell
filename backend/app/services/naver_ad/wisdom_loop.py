# wisdom_loop.py — wisdom_harness (D-NAO-54 P3, docs/PLAN_naver-ad-diary-wisdom.md §P3)
# 역할: candidate_sa(수확)→judge_sa(승격판정)→writer_sa(지혜+보고)→retention_sa(망각)를 하나의
#   일 1회 체인으로 조립. SA간 직접 호출 금지(원칙18) — 이 Harness가 유일한 정보 유통 허브.
#   reflection_loop의 stage_status 단계 격리 패턴을 그대로 따른다(한 단계 실패해도 나머지 계속).
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.services.naver_ad.wisdom_apply import propose_param_changes
from app.services.naver_ad.wisdom_candidates import harvest_candidates
from app.services.naver_ad.wisdom_judge import judge_ripe_candidates
from app.services.naver_ad.wisdom_retention import apply_retention
from app.services.naver_ad.wisdom_writer import write_wisdom
from app.utils.kst import kst_now

log = logging.getLogger(__name__)


def _stage(result: dict, key: str, fn, db: Session, now: datetime) -> None:
    """한 단계 실행 + 격리 — 실패해도 예외를 밖으로 던지지 않고 stage_status에만 기록.

    fn은 호출 시점에 모듈 전역에서 조회된다(테스트 몽키패치가 유효하도록 — reflection_loop 전례).
    """
    try:
        result[key] = fn(db, now=now)
        result["stage_status"][key] = "ok"
    except Exception as e:  # noqa: BLE001 — 한 단계 실패가 나머지를 막지 않음
        db.rollback()  # 실패 트랜잭션 정리 — 공유 세션 오염 방지(reflection_loop 전례)
        log.exception("wisdom_loop %s 단계 실패: %s", key, e)
        result["stage_status"][key] = "failed"
        result[key] = {"error": str(e)}


def run_daily_wisdom(db: Session, *, now: datetime | None = None) -> dict:
    """08:45 엔트리 — 수확→판정→기록→망각→적용 5단계 단계격리(한 단계 실패가 나머지를 막지 않음).

    now: as-of 관통 파라미터(미지정 시 kst_now) — 백테스트/catch-up에서 호출자 시각 존중.
    ⑤apply(propose_param_changes): 갓 기록된 지혜 중 param_suggestion 보유분을 결정 전용
    param_change 제안으로 낸다(writer 뒤 = 이번 회차 승격분도 같은 날 반영, 멱등이라 재실행 안전).
    """
    now = now or kst_now()
    result: dict = {"stage_status": {}}
    _stage(result, "harvest", harvest_candidates, db, now)
    _stage(result, "judge", judge_ripe_candidates, db, now)
    _stage(result, "writer", write_wisdom, db, now)
    _stage(result, "retention", apply_retention, db, now)
    _stage(result, "apply", propose_param_changes, db, now)
    return result
