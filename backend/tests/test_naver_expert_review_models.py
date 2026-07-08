# test_naver_expert_review_models.py — E1a T1 naver_expert_review_run/naver_expert_review 모델 테스트
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NAVER_EXPERT_OUTCOMES,
    NAVER_EXPERT_RUN_STATUSES,
    NAVER_EXPERT_SOURCES,
    NAVER_EXPERT_VERDICTS,
    NaverExpertReview,
    NaverExpertReviewRun,
    NaverProposal,
)

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


def _seed_proposal(db) -> int:
    p = NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-1", campaign_id="cmp-1")
    db.add(p)
    db.commit()
    return p.id


def test_run_and_verdict_link_via_run_id(db):
    """verdict는 run_id로 run에 묶인다 — run 조회로 자신이 속한 verdict들을 찾을 수 있다."""
    run = NaverExpertReviewRun(as_of=AS_OF, model="opus", prompt_version="v1", status="ok")
    db.add(run)
    db.commit()

    proposal_id = _seed_proposal(db)
    verdict = NaverExpertReview(
        run_id=run.id, as_of=AS_OF, proposal_id=proposal_id,
        verdict="agree", reasoning="근거 충분", source="local",
    )
    db.add(verdict)
    db.commit()

    fetched = db.query(NaverExpertReview).filter(NaverExpertReview.run_id == run.id).all()
    assert len(fetched) == 1
    assert fetched[0].proposal_id == proposal_id
    assert fetched[0].verdict == "agree"


def test_verdict_with_null_proposal_id_is_daily_commentary(db):
    """proposal_id NULL = 하루 총평(run의 child라 여러 run에 걸쳐 NULL이 중복돼도 문제없음)."""
    run = NaverExpertReviewRun(as_of=AS_OF, model="opus", prompt_version="v1", status="ok")
    db.add(run)
    db.commit()

    commentary = NaverExpertReview(
        run_id=run.id, as_of=AS_OF, proposal_id=None,
        verdict="commentary", reasoning="오늘 총평", source="local",
    )
    db.add(commentary)
    db.commit()

    assert commentary.proposal_id is None


def test_rerun_same_day_creates_new_run_preserves_history(db):
    """재실행은 같은 as_of라도 새 run을 만든다 — 덮어쓰기가 아니라 이력 보존(codex 반영)."""
    run1 = NaverExpertReviewRun(as_of=AS_OF, model="opus", prompt_version="v1", status="ok")
    run2 = NaverExpertReviewRun(as_of=AS_OF, model="opus", prompt_version="v1", status="ok")
    db.add_all([run1, run2])
    db.commit()

    assert run1.id != run2.id
    same_day_runs = db.query(NaverExpertReviewRun).filter(NaverExpertReviewRun.as_of == AS_OF).all()
    assert len(same_day_runs) == 2


def test_checkable_prediction_allowed_on_reject_verdict(db):
    """기각(reject) 평결에도 검증 가능한 예측을 붙일 수 있다(억지 예측 방지용 선택 필드일 뿐)."""
    run = NaverExpertReviewRun(as_of=AS_OF, model="opus", prompt_version="v1", status="ok")
    db.add(run)
    db.commit()

    verdict = NaverExpertReview(
        run_id=run.id, as_of=AS_OF, proposal_id=None,
        verdict="reject", reasoning="근거 부족",
        checkable_prediction="7일 내 CPC 5% 이상 상승 예상",
        pred_target_type="keyword", pred_target_id="nkw-1",
        pred_metric="cpc", pred_direction="up",
        verify_date=date(2026, 7, 16), outcome="pending", source="local",
    )
    db.add(verdict)
    db.commit()

    fetched = db.query(NaverExpertReview).filter(NaverExpertReview.verdict == "reject").one()
    assert fetched.checkable_prediction is not None
    assert fetched.verify_date == date(2026, 7, 16)
    assert fetched.outcome == "pending"


def test_insufficient_evidence_is_a_valid_verdict_constant(db):
    """관찰모드 정직 평결(codex 반영): 판단 불가를 얼버무리지 않고 명시적으로 저장한다."""
    assert "insufficient_evidence" in NAVER_EXPERT_VERDICTS

    run = NaverExpertReviewRun(as_of=AS_OF, model="opus", prompt_version="v1", status="ok")
    db.add(run)
    db.commit()
    verdict = NaverExpertReview(run_id=run.id, as_of=AS_OF, verdict="insufficient_evidence", source="local")
    db.add(verdict)
    db.commit()

    assert db.query(NaverExpertReview).filter(NaverExpertReview.verdict == "insufficient_evidence").count() == 1


def test_module_constants_cover_all_columns():
    """verdict/outcome/source/run-status는 모듈 상수로 노출된다(C2 — 흩어진 매직 문자열 방지)."""
    assert set(NAVER_EXPERT_VERDICTS) == {"agree", "partial", "reject", "insufficient_evidence", "commentary"}
    assert set(NAVER_EXPERT_OUTCOMES) == {"correct", "wrong", "unverifiable", "pending"}
    assert set(NAVER_EXPERT_SOURCES) == {"local", "ava"}
    assert set(NAVER_EXPERT_RUN_STATUSES) == {"ok", "degraded", "skipped", "failed"}


def test_verdict_column_fits_longest_verdict_constant():
    """codex review 발견(P1): 'insufficient_evidence'는 21자라 String(20)엔 안 들어간다 —
    상수의 최대 길이가 실제 컬럼 폭보다 크면 안 된다(regression guard)."""
    col = NaverExpertReview.__table__.c["verdict"]
    longest = max(len(v) for v in NAVER_EXPERT_VERDICTS)
    assert col.type.length >= longest, f"verdict String({col.type.length}) too short for {longest}자 값"
