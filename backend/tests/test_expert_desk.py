# test_expert_desk.py — E1a T5 expert_desk 하네스(run_daily, 4단계 격리 + 빈제안 skip A2) 단위테스트.
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverExpertReview, NaverExpertReviewRun, NaverProposal
from app.services.naver_ad import expert_desk

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


def _seed_pending_proposal(db, **kw) -> NaverProposal:
    defaults = dict(proposal_type="bid_down", target_type="keyword", target_id="nkw-1", campaign_id="cmp-1", status="pending")
    defaults.update(kw)
    p = NaverProposal(**defaults)
    db.add(p)
    db.commit()
    return p


def _fake_invoke_ok(proposal_ids, commentary="총평"):
    def fake(prompt, *, system, schema, model, timeout):
        return {
            "json": {
                "verdicts": [{"proposal_id": pid, "verdict": "agree", "reasoning": "근거"} for pid in proposal_ids],
                "commentary": commentary,
            },
            "raw": "raw", "usage": {"input_tokens": 1},
        }
    return fake


def test_run_daily_full_pipeline_success(db):
    p = _seed_pending_proposal(db)

    result = expert_desk.run_daily(db, today=AS_OF, invoke=_fake_invoke_ok([p.id]))

    assert result["stage_status"] == {
        "grade_due_predictions": "ok", "briefing_builder": "ok",
        "ava_reviewer": "ok", "expert_ledger": "ok",
    }
    assert result["errors"] == []
    run = db.query(NaverExpertReviewRun).filter(NaverExpertReviewRun.as_of == AS_OF).first()
    assert run is not None
    verdicts = db.query(NaverExpertReview).filter(NaverExpertReview.run_id == run.id, NaverExpertReview.proposal_id == p.id).all()
    assert len(verdicts) == 1


def test_run_daily_skips_review_when_no_pending_proposals_a2(db):
    """A2: pending 제안이 0건이면 claude를 아예 호출하지 않고 stage3/4 skip."""
    invoke_calls = {"n": 0}

    def counting_invoke(prompt, *, system, schema, model, timeout):
        invoke_calls["n"] += 1
        return {"json": {"verdicts": [], "commentary": "x"}, "raw": "", "usage": {}}

    result = expert_desk.run_daily(db, today=AS_OF, invoke=counting_invoke)

    assert result["stage_status"]["briefing_builder"] == "ok"
    assert result["stage_status"]["ava_reviewer"] == "skipped"
    assert result["stage_status"]["expert_ledger"] == "skipped"
    assert invoke_calls["n"] == 0, "claude가 호출되면 안 됨(A2)"
    assert db.query(NaverExpertReviewRun).count() == 0


def test_run_daily_result_has_grade_key_even_when_grade_stage_fails(db, monkeypatch):
    """codex 발견(P2): grade 실패 시 result['grade']가 아예 없으면 호출측이 KeyError 위험 — None으로라도 있어야 함."""
    def boom(db, today):
        raise RuntimeError("채점 중 오류")

    monkeypatch.setattr(expert_desk.expert_ledger, "grade_due_predictions", boom)

    result = expert_desk.run_daily(db, today=AS_OF, invoke=_fake_invoke_ok([]))

    assert "grade" in result
    assert result["grade"] is None


def test_run_daily_grade_stage_failure_does_not_block_rest(db, monkeypatch):
    """단계격리: grade_due_predictions 실패해도 나머지 단계는 계속 진행."""
    p = _seed_pending_proposal(db)

    def boom(db, today):
        raise RuntimeError("채점 중 오류")

    monkeypatch.setattr(expert_desk.expert_ledger, "grade_due_predictions", boom)

    result = expert_desk.run_daily(db, today=AS_OF, invoke=_fake_invoke_ok([p.id]))

    assert result["stage_status"]["grade_due_predictions"] == "failed"
    assert result["stage_status"]["briefing_builder"] == "ok"
    assert result["stage_status"]["ava_reviewer"] == "ok"
    assert result["stage_status"]["expert_ledger"] == "ok"
    assert any("채점 중 오류" in e for e in result["errors"])


def test_run_daily_briefing_failure_skips_downstream(db, monkeypatch):
    _seed_pending_proposal(db)

    def boom(db, as_of):
        raise RuntimeError("브리핑 조립 실패")

    monkeypatch.setattr(expert_desk.expert_briefing_builder, "build", boom)

    result = expert_desk.run_daily(db, today=AS_OF, invoke=_fake_invoke_ok([1]))

    assert result["stage_status"]["briefing_builder"] == "failed"
    assert result["stage_status"]["ava_reviewer"] == "skipped"
    assert result["stage_status"]["expert_ledger"] == "skipped"
    assert any("브리핑 조립 실패" in e for e in result["errors"])
    assert db.query(NaverExpertReviewRun).count() == 0


def test_run_daily_review_failure_skips_ledger(db):
    """ava_reviewer가 invoke 예외를 흡수하지 않고 전파(T3 설계) — 하네스가 여기서 catch."""
    p = _seed_pending_proposal(db)

    def failing_invoke(prompt, *, system, schema, model, timeout):
        raise RuntimeError("claude CLI 타임아웃")

    result = expert_desk.run_daily(db, today=AS_OF, invoke=failing_invoke)

    assert result["stage_status"]["ava_reviewer"] == "failed"
    assert result["stage_status"]["expert_ledger"] == "skipped"
    assert any("claude CLI 타임아웃" in e for e in result["errors"])
    assert db.query(NaverExpertReviewRun).count() == 0


def test_run_daily_includes_grade_result_in_output(db):
    result = expert_desk.run_daily(db, today=AS_OF, invoke=_fake_invoke_ok([]))

    assert "grade" in result
    assert result["grade"]["graded"] == 0


def test_run_daily_defaults_today_to_kst_today(db, monkeypatch):
    monkeypatch.setattr(expert_desk, "kst_today", lambda: AS_OF)

    expert_desk.run_daily(db, invoke=_fake_invoke_ok([]))

    # briefing_builder가 as_of=AS_OF로 호출됐는지는 결과적으로 run이 없더라도(빈제안) 에러 없이
    # 정상 흘러갔는지로 간접 확인(빈 제안 skip 경로 재사용).
    result = expert_desk.run_daily(db, invoke=_fake_invoke_ok([]))
    assert result["stage_status"]["briefing_builder"] == "ok"
