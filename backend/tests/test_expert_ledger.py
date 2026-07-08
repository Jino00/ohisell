# test_expert_ledger.py — E1a T4 expert_ledger(SA3: record + grade_due_predictions) 단위테스트.
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily,
    NaverExpertReview,
    NaverExpertReviewRun,
    NaverLearningState,
    NaverProposal,
)
from app.services.naver_ad import expert_ledger
from app.services.naver_ad.account_diagnosis import LOW_CLICK_THRESHOLD

AS_OF = date(2026, 7, 9)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _review(*, verdicts=None, commentary="총평입니다", status="ok", briefing_hash="hash-1", model="opus"):
    return {
        "verdicts": verdicts if verdicts is not None else [
            {"proposal_id": 1, "verdict": "agree", "reasoning": "근거1"},
            {"proposal_id": 2, "verdict": "reject", "reasoning": "근거2"},
        ],
        "commentary": commentary,
        "raw": "raw-response-text",
        "usage": {"input_tokens": 100, "output_tokens": 20},
        "status": status,
        "error": None,
        "model": model,
        "prompt_version": "v1",
        "briefing_hash": briefing_hash,
    }


# ── record() ─────────────────────────────────────────────────────────────


def test_record_creates_run_and_verdict_rows(db):
    run = expert_ledger.record(db, AS_OF, _review())

    assert run.id is not None
    assert run.as_of == AS_OF
    assert run.model == "opus"
    assert run.status == "ok"

    verdicts = db.query(NaverExpertReview).filter(NaverExpertReview.run_id == run.id).all()
    proposal_verdicts = [v for v in verdicts if v.proposal_id is not None]
    assert {v.proposal_id for v in proposal_verdicts} == {1, 2}


def test_record_commentary_row_dedup_within_one_run(db):
    """A1: 총평 행은 run당 정확히 1개(run 원장 구조가 자연히 이를 보장)."""
    run = expert_ledger.record(db, AS_OF, _review(commentary="오늘 총평"))

    commentary_rows = db.query(NaverExpertReview).filter(
        NaverExpertReview.run_id == run.id, NaverExpertReview.proposal_id.is_(None),
    ).all()
    assert len(commentary_rows) == 1
    assert commentary_rows[0].verdict == "commentary"
    assert commentary_rows[0].reasoning == "오늘 총평"


def test_record_skips_commentary_row_when_commentary_empty(db):
    run = expert_ledger.record(db, AS_OF, _review(commentary=""))

    commentary_rows = db.query(NaverExpertReview).filter(
        NaverExpertReview.run_id == run.id, NaverExpertReview.proposal_id.is_(None),
    ).all()
    assert commentary_rows == []


def test_record_is_idempotent_for_same_as_of_and_briefing_hash(db):
    review = _review(briefing_hash="same-hash")

    run1 = expert_ledger.record(db, AS_OF, review)
    run2 = expert_ledger.record(db, AS_OF, review)

    assert run1.id == run2.id
    assert db.query(NaverExpertReviewRun).count() == 1
    assert db.query(NaverExpertReview).filter(NaverExpertReview.run_id == run1.id).count() == 3  # 평결2+총평1


def test_record_creates_new_run_for_different_briefing_hash_same_day(db):
    """재실행=새 run(덮어쓰기 아님) — briefing_hash가 다르면 같은 날이어도 새 run."""
    run1 = expert_ledger.record(db, AS_OF, _review(briefing_hash="hash-A"))
    run2 = expert_ledger.record(db, AS_OF, _review(briefing_hash="hash-B"))

    assert run1.id != run2.id
    assert db.query(NaverExpertReviewRun).filter(NaverExpertReviewRun.as_of == AS_OF).count() == 2


def test_record_sets_verify_date_and_pending_outcome_only_when_checkable_prediction_present(db):
    review = _review(verdicts=[
        {"proposal_id": 1, "verdict": "agree", "reasoning": "근거"},
        {
            "proposal_id": 2, "verdict": "reject", "reasoning": "근거",
            "checkable_prediction": "7일 내 CPC 상승", "pred_target_type": "keyword",
            "pred_target_id": "nkw-1", "pred_metric": "cpc", "pred_direction": "up",
        },
    ])
    run = expert_ledger.record(db, AS_OF, review)

    rows = {v.proposal_id: v for v in db.query(NaverExpertReview).filter(NaverExpertReview.run_id == run.id)}
    assert rows[1].verify_date is None
    assert rows[1].outcome is None
    assert rows[2].verify_date is not None and rows[2].verify_date > AS_OF
    assert rows[2].outcome == "pending"


def test_record_does_not_touch_naver_proposal_c3_boundary(db):
    """⚠️C3 자문 경계: expert_ledger는 NaverProposal.status·실행상태를 절대 건드리지 않는다."""
    p1 = NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-1", campaign_id="cmp-1", status="pending")
    p2 = NaverProposal(proposal_type="bid_up", target_type="keyword", target_id="nkw-2", campaign_id="cmp-1", status="pending")
    db.add_all([p1, p2])
    db.commit()
    before = {p.id: (p.status, p.executed_change_log_id) for p in db.query(NaverProposal).all()}

    expert_ledger.record(db, AS_OF, _review(verdicts=[
        {"proposal_id": p1.id, "verdict": "agree", "reasoning": "a"},
        {"proposal_id": p2.id, "verdict": "reject", "reasoning": "b"},
    ]))

    after = {p.id: (p.status, p.executed_change_log_id) for p in db.query(NaverProposal).all()}
    assert before == after


# ── grade_due_predictions() ─────────────────────────────────────────────


def _seed_run_and_pending_verdict(db, *, verify_date, pred_metric="clk", pred_direction="up", target_id="nkw-1", target_type="keyword", pred_as_of=AS_OF):
    run = NaverExpertReviewRun(as_of=pred_as_of, model="opus", prompt_version="v1", status="ok")
    db.add(run)
    db.commit()
    v = NaverExpertReview(
        run_id=run.id, as_of=pred_as_of, proposal_id=None, verdict="reject", reasoning="근거",
        checkable_prediction="pred", pred_target_type=target_type, pred_target_id=target_id,
        pred_metric=pred_metric, pred_direction=pred_direction, verify_date=verify_date,
        outcome="pending", source="local",
    )
    db.add(v)
    db.commit()
    return v


def _seed_ad_daily(db, target_id, ad_date, clk):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id="cmp-1", campaign_type="WEB_SITE",
        adgroup_id="adg-1", keyword_id=target_id, imp=clk * 10, clk=clk, cost=clk * 100,
    ))


def test_grade_leaves_not_yet_due_predictions_as_pending(db):
    v = _seed_run_and_pending_verdict(db, verify_date=AS_OF + timedelta(days=7))

    result = expert_ledger.grade_due_predictions(db, today=AS_OF)  # verify_date가 미래

    assert result["graded"] == 0
    db.refresh(v)
    assert v.outcome == "pending"


def test_grade_marks_unverifiable_when_click_volume_too_low(db):
    v = _seed_run_and_pending_verdict(db, verify_date=AS_OF, pred_metric="clk")
    # 클릭 볼륨을 LOW_CLICK_THRESHOLD 미만으로만 채움
    for i in range(3):
        _seed_ad_daily(db, "nkw-1", AS_OF - timedelta(days=1 + i), clk=1)
    db.commit()

    result = expert_ledger.grade_due_predictions(db, today=AS_OF)

    assert result["unverifiable"] == 1
    db.refresh(v)
    assert v.outcome == "unverifiable"


_PRED_AS_OF = AS_OF - timedelta(days=7)  # verify_date=AS_OF와 7일 간격 → before/after 창이 안 겹침


def test_grade_marks_correct_when_actual_direction_matches_prediction(db):
    v = _seed_run_and_pending_verdict(db, verify_date=AS_OF, pred_metric="clk", pred_direction="up", pred_as_of=_PRED_AS_OF)
    for i in range(7):  # before 창(pred_as_of 이전 7일): 낮은 클릭
        _seed_ad_daily(db, "nkw-1", _PRED_AS_OF - timedelta(days=1 + i), clk=LOW_CLICK_THRESHOLD * 2)
    for i in range(7):  # after 창(verify_date까지 7일): 높은 클릭(예측=up과 일치)
        _seed_ad_daily(db, "nkw-1", AS_OF - timedelta(days=i), clk=LOW_CLICK_THRESHOLD * 5)
    db.commit()

    result = expert_ledger.grade_due_predictions(db, today=AS_OF)

    assert result["correct"] == 1
    db.refresh(v)
    assert v.outcome == "correct"


def test_grade_marks_wrong_when_actual_direction_contradicts_prediction(db):
    v = _seed_run_and_pending_verdict(db, verify_date=AS_OF, pred_metric="clk", pred_direction="up", pred_as_of=_PRED_AS_OF)
    for i in range(7):
        _seed_ad_daily(db, "nkw-1", _PRED_AS_OF - timedelta(days=1 + i), clk=LOW_CLICK_THRESHOLD * 5)
    for i in range(7):  # 실제론 하락(예측=up과 불일치)
        _seed_ad_daily(db, "nkw-1", AS_OF - timedelta(days=i), clk=LOW_CLICK_THRESHOLD * 2)
    db.commit()

    result = expert_ledger.grade_due_predictions(db, today=AS_OF)

    assert result["wrong"] == 1
    db.refresh(v)
    assert v.outcome == "wrong"


def test_grade_is_idempotent_does_not_regrade_already_graded_rows(db):
    v = _seed_run_and_pending_verdict(db, verify_date=AS_OF, pred_metric="clk")
    for i in range(3):
        _seed_ad_daily(db, "nkw-1", AS_OF - timedelta(days=1 + i), clk=1)
    db.commit()

    first = expert_ledger.grade_due_predictions(db, today=AS_OF)
    second = expert_ledger.grade_due_predictions(db, today=AS_OF)

    assert first["graded"] == 1
    assert second["graded"] == 0, "이미 채점된(outcome != pending) 행은 재채점하지 않아야 함(멱등)"


def test_grade_does_not_touch_naver_proposal_c3_boundary(db):
    p = NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-1", campaign_id="cmp-1", status="pending")
    db.add(p)
    db.commit()
    before = (p.status, p.executed_change_log_id)

    _seed_run_and_pending_verdict(db, verify_date=AS_OF)
    for i in range(3):
        _seed_ad_daily(db, "nkw-1", AS_OF - timedelta(days=1 + i), clk=1)
    db.commit()
    expert_ledger.grade_due_predictions(db, today=AS_OF)

    db.refresh(p)
    assert (p.status, p.executed_change_log_id) == before


def test_grade_upserts_scoreboard_only_when_verifiable_outcomes_exist(db):
    _seed_run_and_pending_verdict(db, verify_date=AS_OF, pred_metric="clk", pred_direction="up", pred_as_of=_PRED_AS_OF)
    for i in range(7):
        _seed_ad_daily(db, "nkw-1", _PRED_AS_OF - timedelta(days=1 + i), clk=LOW_CLICK_THRESHOLD * 2)
    for i in range(7):
        _seed_ad_daily(db, "nkw-1", AS_OF - timedelta(days=i), clk=LOW_CLICK_THRESHOLD * 5)
    db.commit()

    expert_ledger.grade_due_predictions(db, today=AS_OF)

    row = db.query(NaverLearningState).filter(
        NaverLearningState.scope == "expert", NaverLearningState.metric == "prediction_accuracy",
    ).first()
    assert row is not None
    assert row.sample_n == 1
    assert float(row.current_value) == 1.0


def test_grade_scoreboard_unchanged_when_only_unverifiable(db):
    _seed_run_and_pending_verdict(db, verify_date=AS_OF, pred_metric="clk")
    for i in range(3):
        _seed_ad_daily(db, "nkw-1", AS_OF - timedelta(days=1 + i), clk=1)
    db.commit()

    expert_ledger.grade_due_predictions(db, today=AS_OF)

    row = db.query(NaverLearningState).filter(
        NaverLearningState.scope == "expert", NaverLearningState.metric == "prediction_accuracy",
    ).first()
    assert row is None, "unverifiable만 있을 땐 정직 경계상 성적표를 갱신하지 않아야 함"


# ── codex review 발견 사항 회귀 테스트 ──────────────────────────────────


def test_grade_flat_before_after_value_is_unverifiable_not_down(db):
    """codex 발견(P1): before==after(변동 없음)를 'down'으로 단정하면 down 예측이 거짓으로 correct가 됨."""
    v = _seed_run_and_pending_verdict(db, verify_date=AS_OF, pred_metric="clk", pred_direction="down", pred_as_of=_PRED_AS_OF)
    for i in range(7):
        _seed_ad_daily(db, "nkw-1", _PRED_AS_OF - timedelta(days=1 + i), clk=LOW_CLICK_THRESHOLD * 3)
    for i in range(7):  # after도 완전히 동일한 값 — 변동 없음
        _seed_ad_daily(db, "nkw-1", AS_OF - timedelta(days=i), clk=LOW_CLICK_THRESHOLD * 3)
    db.commit()

    result = expert_ledger.grade_due_predictions(db, today=AS_OF)

    assert result["unverifiable"] == 1
    db.refresh(v)
    assert v.outcome == "unverifiable"


def test_grade_unknown_pred_target_type_is_unverifiable(db):
    """codex 발견(P1): 미지의 pred_target_type이 campaign 분기로 새서 오채점될 수 있음."""
    v = _seed_run_and_pending_verdict(
        db, verify_date=AS_OF, pred_metric="clk", pred_direction="up",
        target_type="product", target_id="cmp-1", pred_as_of=_PRED_AS_OF,
    )
    for i in range(7):
        _seed_ad_daily(db, "nkw-1", _PRED_AS_OF - timedelta(days=1 + i), clk=LOW_CLICK_THRESHOLD * 2)
    for i in range(7):
        _seed_ad_daily(db, "nkw-1", AS_OF - timedelta(days=i), clk=LOW_CLICK_THRESHOLD * 5)
    db.commit()

    expert_ledger.grade_due_predictions(db, today=AS_OF)

    db.refresh(v)
    assert v.outcome == "unverifiable"


def test_grade_before_after_window_overlap_is_unverifiable(db):
    """codex 발견(P2): verify_date가 as_of와 너무 가까우면(D+7 관례를 안 지키는 수동 구성 행)
    before/after 창이 겹쳐 같은 실적을 이중계산할 수 있다 — 겹치면 unverifiable로 방어."""
    v = _seed_run_and_pending_verdict(db, verify_date=AS_OF, pred_metric="clk", pred_direction="up", pred_as_of=AS_OF)  # 간격 0일
    for i in range(7):
        _seed_ad_daily(db, "nkw-1", AS_OF - timedelta(days=1 + i), clk=LOW_CLICK_THRESHOLD * 5)
    db.commit()

    expert_ledger.grade_due_predictions(db, today=AS_OF)

    db.refresh(v)
    assert v.outcome == "unverifiable"


def test_scoreboard_is_exact_recompute_not_incremental_reconstruction(db):
    """codex 발견(P1): 저장된 반올림 비율로 prior_correct를 역산하면 표류 위험 — 매번 outcome
    테이블에서 정확히 재집계해야 한다."""
    # 기존 이력: correct 3건 wrong 1건이 이미 채점 완료 상태로 있다고 가정(과거 배치 결과).
    old_run = NaverExpertReviewRun(as_of=AS_OF - timedelta(days=30), model="opus", prompt_version="v1", status="ok")
    db.add(old_run)
    db.commit()
    for outcome in ("correct", "correct", "correct", "wrong"):
        db.add(NaverExpertReview(
            run_id=old_run.id, as_of=AS_OF - timedelta(days=30), proposal_id=None, verdict="reject",
            reasoning="근거", checkable_prediction="pred", pred_target_type="keyword", pred_target_id="nkw-old",
            pred_metric="clk", pred_direction="up", verify_date=AS_OF - timedelta(days=23), outcome=outcome,
            source="local",
        ))
    db.commit()

    # 이번 배치: correct 1건 신규 채점 → 누적 correct=4, wrong=1, sample_n=5, 정확한 비율=0.8
    _seed_run_and_pending_verdict(db, verify_date=AS_OF, pred_metric="clk", pred_direction="up", pred_as_of=_PRED_AS_OF)
    for i in range(7):
        _seed_ad_daily(db, "nkw-1", _PRED_AS_OF - timedelta(days=1 + i), clk=LOW_CLICK_THRESHOLD * 2)
    for i in range(7):
        _seed_ad_daily(db, "nkw-1", AS_OF - timedelta(days=i), clk=LOW_CLICK_THRESHOLD * 5)
    db.commit()

    expert_ledger.grade_due_predictions(db, today=AS_OF)

    row = db.query(NaverLearningState).filter(
        NaverLearningState.scope == "expert", NaverLearningState.metric == "prediction_accuracy",
    ).first()
    assert row.sample_n == 5
    assert float(row.current_value) == 0.8


def test_record_raises_when_session_has_uncommitted_naver_proposal_change(db):
    """C3 방어(codex 발견 P1): 커밋 안 된 NaverProposal 변경이 같은 세션에 있으면 이 모듈의
    commit()이 그것까지 같이 흘려보낼 수 있다 — 자문 경계를 런타임에서도 막는다."""
    from app.models import NaverProposal
    p = NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-1", campaign_id="cmp-1", status="pending")
    db.add(p)
    db.commit()
    p.status = "approved"  # 커밋 안 하고 dirty 상태로 방치

    with pytest.raises(RuntimeError, match="C3"):
        expert_ledger.record(db, AS_OF, _review())


def test_grade_due_predictions_raises_when_session_has_uncommitted_naver_proposal_change(db):
    from app.models import NaverProposal
    p = NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-1", campaign_id="cmp-1", status="pending")
    db.add(p)
    db.commit()
    p.status = "approved"

    with pytest.raises(RuntimeError, match="C3"):
        expert_ledger.grade_due_predictions(db, today=AS_OF)
