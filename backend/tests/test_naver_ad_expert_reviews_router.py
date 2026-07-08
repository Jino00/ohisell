# test_naver_ad_expert_reviews_router.py — E1a T6 HTTP 라운드트립(TestClient)
# GET /expert-reviews + GET /proposals의 verdict 조인. 원칙22(라우터 레이어는 SA/harness
# 단위테스트로 못 잡음) — 실제 HTTP 왕복으로 검증.
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverExpertReview, NaverExpertReviewRun, NaverProposal

AS_OF = date(2026, 7, 9)


@pytest.fixture
def client_and_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    session_for_seed = TestingSession()
    yield TestClient(app), session_for_seed
    session_for_seed.close()
    app.dependency_overrides.clear()


@pytest.fixture
def client(client_and_session):
    return client_and_session[0]


@pytest.fixture
def db(client_and_session):
    return client_and_session[1]


def _add_run(db, *, as_of=AS_OF, status="ok") -> NaverExpertReviewRun:
    run = NaverExpertReviewRun(as_of=as_of, model="opus", prompt_version="v1", status=status)
    db.add(run)
    db.commit()
    return run


def _add_proposal(db, **kw) -> NaverProposal:
    defaults = dict(proposal_type="bid_down", target_type="keyword", target_id="nkw-1", campaign_id="cmp-1", status="pending")
    defaults.update(kw)
    p = NaverProposal(**defaults)
    db.add(p)
    db.commit()
    return p


# ── GET /expert-reviews ──

def test_expert_reviews_endpoint_returns_200_empty(client):
    resp = client.get("/api/naver/ad/expert-reviews")
    assert resp.status_code == 200
    assert resp.json() == {"rows": []}


def test_expert_reviews_endpoint_returns_seeded_rows(client, db):
    run = _add_run(db)
    p = _add_proposal(db)
    db.add(NaverExpertReview(run_id=run.id, as_of=AS_OF, proposal_id=p.id, verdict="agree", reasoning="근거", source="local"))
    db.commit()

    resp = client.get("/api/naver/ad/expert-reviews")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["verdict"] == "agree"
    assert rows[0]["proposal_id"] == p.id
    assert rows[0]["run_id"] == run.id


def test_expert_reviews_endpoint_filters_by_as_of(client, db):
    run1 = _add_run(db, as_of=date(2026, 7, 8))
    run2 = _add_run(db, as_of=AS_OF)
    db.add(NaverExpertReview(run_id=run1.id, as_of=date(2026, 7, 8), proposal_id=None, verdict="commentary", reasoning="어제", source="local"))
    db.add(NaverExpertReview(run_id=run2.id, as_of=AS_OF, proposal_id=None, verdict="commentary", reasoning="오늘", source="local"))
    db.commit()

    resp = client.get("/api/naver/ad/expert-reviews", params={"as_of": AS_OF.isoformat()})
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["reasoning"] == "오늘"


def test_expert_reviews_endpoint_filters_by_proposal_id(client, db):
    run = _add_run(db)
    p1 = _add_proposal(db, target_id="nkw-1")
    p2 = _add_proposal(db, target_id="nkw-2")
    db.add(NaverExpertReview(run_id=run.id, as_of=AS_OF, proposal_id=p1.id, verdict="agree", reasoning="a", source="local"))
    db.add(NaverExpertReview(run_id=run.id, as_of=AS_OF, proposal_id=p2.id, verdict="reject", reasoning="b", source="local"))
    db.commit()

    resp = client.get("/api/naver/ad/expert-reviews", params={"proposal_id": p2.id})
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["proposal_id"] == p2.id
    assert rows[0]["verdict"] == "reject"


def test_expert_reviews_endpoint_includes_commentary_rows(client, db):
    """proposal_id=NULL 총평 행도 정상 조회(프론트 'Ava의 검토' 패널용)."""
    run = _add_run(db)
    db.add(NaverExpertReview(run_id=run.id, as_of=AS_OF, proposal_id=None, verdict="commentary", reasoning="오늘 총평", source="local"))
    db.commit()

    resp = client.get("/api/naver/ad/expert-reviews", params={"as_of": AS_OF.isoformat()})
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["proposal_id"] is None
    assert rows[0]["reasoning"] == "오늘 총평"


def test_expert_reviews_endpoint_rejects_invalid_as_of(client):
    resp = client.get("/api/naver/ad/expert-reviews", params={"as_of": "bogus-date"})
    assert resp.status_code == 422  # FastAPI Query(date) 자체 검증


def test_expert_reviews_endpoint_respects_limit_param(client, db):
    """codex review 발견(P2): /proposals처럼 limit을 노출해 하드캡 뒤로 숨기지 않는다."""
    run = _add_run(db)
    for i in range(5):
        db.add(NaverExpertReview(run_id=run.id, as_of=AS_OF, proposal_id=None, verdict="commentary", reasoning=f"총평{i}", source="local"))
    db.commit()

    resp = client.get("/api/naver/ad/expert-reviews", params={"limit": 2})
    assert len(resp.json()["rows"]) == 2


# ── GET /proposals — expert_verdict 조인 ──

def test_proposals_endpoint_expert_verdict_null_when_no_review(client, db):
    _add_proposal(db)
    resp = client.get("/api/naver/ad/proposals")
    rows = resp.json()["rows"]
    assert rows[0]["expert_verdict"] is None


def test_proposals_endpoint_includes_expert_verdict_from_ok_run(client, db):
    p = _add_proposal(db)
    run = _add_run(db, status="ok")
    db.add(NaverExpertReview(run_id=run.id, as_of=AS_OF, proposal_id=p.id, verdict="agree", confidence=0.9, reasoning="근거", source="local"))
    db.commit()

    resp = client.get("/api/naver/ad/proposals")
    rows = resp.json()["rows"]
    assert rows[0]["expert_verdict"]["verdict"] == "agree"
    assert rows[0]["expert_verdict"]["confidence"] == 0.9


def test_proposals_endpoint_expert_verdict_ignores_degraded_run(client, db):
    """degraded run엔 평결이 없거나 신뢰할 수 없다 — 완료(status=ok) run만 조인."""
    p = _add_proposal(db)
    degraded_run = _add_run(db, status="degraded")
    db.add(NaverExpertReview(run_id=degraded_run.id, as_of=AS_OF, proposal_id=p.id, verdict="agree", reasoning="degraded인데도 있으면 안 됨", source="local"))
    db.commit()

    resp = client.get("/api/naver/ad/proposals")
    rows = resp.json()["rows"]
    assert rows[0]["expert_verdict"] is None


def test_proposals_endpoint_expert_verdict_uses_most_recent_ok_run(client, db):
    """제안이 여러 날 pending으로 남아 여러 번 재검토됐으면 가장 최근 완료 run의 평결만."""
    p = _add_proposal(db)
    run1 = _add_run(db, as_of=date(2026, 7, 8), status="ok")
    run2 = _add_run(db, as_of=AS_OF, status="ok")
    db.add(NaverExpertReview(run_id=run1.id, as_of=date(2026, 7, 8), proposal_id=p.id, verdict="reject", reasoning="어제는 기각", source="local"))
    db.add(NaverExpertReview(run_id=run2.id, as_of=AS_OF, proposal_id=p.id, verdict="agree", reasoning="오늘은 동의", source="local"))
    db.commit()

    resp = client.get("/api/naver/ad/proposals")
    rows = resp.json()["rows"]
    assert rows[0]["expert_verdict"]["verdict"] == "agree"


def test_proposals_endpoint_expert_verdict_prefers_later_as_of_even_with_lower_run_id(client, db):
    """codex review 발견(P1): run.id 순서만 믿으면 안 됨 — as_of가 진짜 최신 판단 기준.
    (예: 백필/재처리로 run이 시간순과 다르게 insert돼도 as_of가 더 최근인 쪽을 골라야 함)."""
    p = _add_proposal(db)
    later_as_of_run = _add_run(db, as_of=AS_OF, status="ok")  # 먼저 insert(id 작음)됐지만 as_of는 더 최근
    earlier_as_of_run = _add_run(db, as_of=date(2026, 7, 8), status="ok")  # 나중 insert(id 큼)됐지만 as_of는 더 과거
    assert later_as_of_run.id < earlier_as_of_run.id

    db.add(NaverExpertReview(run_id=later_as_of_run.id, as_of=AS_OF, proposal_id=p.id, verdict="agree", reasoning="진짜 최신(오늘)", source="local"))
    db.add(NaverExpertReview(run_id=earlier_as_of_run.id, as_of=date(2026, 7, 8), proposal_id=p.id, verdict="reject", reasoning="id는 크지만 실제론 과거(어제)", source="local"))
    db.commit()

    resp = client.get("/api/naver/ad/proposals")
    rows = resp.json()["rows"]
    assert rows[0]["expert_verdict"]["verdict"] == "agree"
