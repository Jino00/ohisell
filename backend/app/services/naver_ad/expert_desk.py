# expert_desk.py — E1a Harness: run_daily 4단계 격리(learning_loops.py식, PLAN §8) +
# X1a T5 stage5 delegation_gate 배선.
# stage1 grade_due_predictions → stage2 briefing_builder.build → stage2.5 빈제안 가드(A2,
# pending 0건이면 claude 미호출·stage3/4 skip) → stage3 ava_reviewer.review(주입경계) →
# stage4 expert_ledger.record → stage5 delegation_gate.run_gate(run.status=='ok'일 때만,
# D-NAO-25 — degraded run은 자동실행 금지 fail-closed). 각 단계 독립 try/except — 한 단계
# 실패가 나머지를 막지 않는다.
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.services.naver_ad import delegation_gate, expert_briefing_builder, expert_ledger
from app.services.naver_ad import ava_reviewer
from app.utils.kst import kst_today

log = logging.getLogger(__name__)


def run_daily(db: Session, *, today: date | None = None, invoke=None) -> dict:
    """일일 전문가 데스크 실행. invoke는 ava_reviewer.review로 전달되는 주입경계(테스트용
    가짜 — 프로덕션에서는 생략해 ava_reviewer 기본값인 실 claude 어댑터를 그대로 쓴다).

    호출측은 매일 새 세션(크론 전용)으로 db를 넘겨야 한다 — C3 런타임 가드(T4)가 세션에
    커밋 안 된 NaverProposal 변경을 발견하면 stage1/4이 실패로 기록되고(rollback으로 흡수),
    이 하네스 자체는 그 실패를 다른 단계 실패와 동일하게 취급해 계속 진행한다(의도된 동작 —
    가드가 실제로 작동했다는 신호일 뿐, 이 파일이 직접 침해한 게 아니다).
    """
    today = today or kst_today()
    result: dict = {"stage_status": {}, "errors": [], "grade": None}

    try:
        result["grade"] = expert_ledger.grade_due_predictions(db, today)
        result["stage_status"]["grade_due_predictions"] = "ok"
    except Exception as e:  # noqa: BLE001 — 한 단계 실패가 나머지를 막지 않음(learning_loops 전례)
        db.rollback()
        log.exception("expert_desk: grade_due_predictions 단계 실패: %s", e)
        result["stage_status"]["grade_due_predictions"] = "failed"
        result["errors"].append(f"grade_due_predictions: {e}")

    briefing = None
    try:
        briefing = expert_briefing_builder.build(db, today)
        result["stage_status"]["briefing_builder"] = "ok"
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("expert_desk: briefing_builder 단계 실패: %s", e)
        result["stage_status"]["briefing_builder"] = "failed"
        result["errors"].append(f"briefing_builder: {e}")

    if briefing is None:
        result["stage_status"]["ava_reviewer"] = "skipped"
        result["stage_status"]["expert_ledger"] = "skipped"
        result["stage_status"]["delegation_gate"] = "skipped"
        log.info("expert_desk run_daily: %s", result["stage_status"])
        return result

    if not briefing.get("pending_proposals"):
        # A2: 빈 제안 가드 — 검토할 게 없으면 claude 자체를 호출하지 않는다(비용·소음 방지).
        result["stage_status"]["ava_reviewer"] = "skipped"
        result["stage_status"]["expert_ledger"] = "skipped"
        result["stage_status"]["delegation_gate"] = "skipped"
        log.info("expert_desk: pending 제안 0건 — claude 미호출, stage3/4 skip")
        log.info("expert_desk run_daily: %s", result["stage_status"])
        return result

    review = None
    try:
        review_kwargs = {"invoke": invoke} if invoke is not None else {}
        review = ava_reviewer.review(briefing, **review_kwargs)
        result["stage_status"]["ava_reviewer"] = "ok"
    except Exception as e:  # noqa: BLE001
        log.exception("expert_desk: ava_reviewer 단계 실패: %s", e)
        result["stage_status"]["ava_reviewer"] = "failed"
        result["errors"].append(f"ava_reviewer: {e}")

    if review is None:
        result["stage_status"]["expert_ledger"] = "skipped"
        result["stage_status"]["delegation_gate"] = "skipped"
        log.info("expert_desk run_daily: %s", result["stage_status"])
        return result

    run = None
    try:
        run = expert_ledger.record(db, today, review)
        result["stage_status"]["expert_ledger"] = "ok"
        result["run_id"] = run.id
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("expert_desk: expert_ledger 단계 실패: %s", e)
        result["stage_status"]["expert_ledger"] = "failed"
        result["errors"].append(f"expert_ledger: {e}")

    # stage5(X1a T5): run.status=='ok'일 때만 위임 게이트 시도 — degraded run(예: 스키마
    # 검증 실패로 평결이 부실)은 자동실행 금지(fail-closed, D-NAO-25). run이 없으면(stage4
    # 실패) 당연히 skip.
    if run is not None and run.status == "ok":
        try:
            result["delegation"] = delegation_gate.run_gate(db, run.id)
            result["stage_status"]["delegation_gate"] = "ok"
        except Exception as e:  # noqa: BLE001
            db.rollback()
            log.exception("expert_desk: delegation_gate 단계 실패: %s", e)
            result["stage_status"]["delegation_gate"] = "failed"
            result["errors"].append(f"delegation_gate: {e}")
    else:
        result["stage_status"]["delegation_gate"] = "skipped"

    log.info("expert_desk run_daily: %s", result["stage_status"])
    return result
