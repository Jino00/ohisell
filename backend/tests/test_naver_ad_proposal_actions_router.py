# test_naver_ad_proposal_actions_router.py — X1a T4 HTTP 라운드트립(TestClient)
# POST /proposals/{id}/status(승인·반려), POST /proposals/{id}/execute(실쓰기), GET
# /proposals의 executable/not_executable_reason 직렬화. 원칙22(라우터 레이어는 SA/harness
# 단위테스트로 못 잡음, test_naver_ad_proposals_router.py 선례) — 실제 HTTP 왕복으로 검증.
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverCampaignSettings, NaverChangeLog, NaverProposal
from app.services.naver_ad import naver_execution_harness as harness
from app.services.naver_ad import naver_sa_writer


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


def _proposal(db, proposal_type="bid_down", campaign_id="cmp-1", status="pending",
              target_type="keyword", target_id="nkw-1", adgroup_id=None) -> NaverProposal:
    p = NaverProposal(
        proposal_type=proposal_type, target_type=target_type, target_id=target_id,
        campaign_id=campaign_id, adgroup_id=adgroup_id,
        rationale="테스트 근거", expected_effect="테스트 예상효과", status=status,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _negative_proposal(db, adgroup_id="grp-1", **kw) -> NaverProposal:
    return _proposal(db, proposal_type="negative_keyword", target_type="search_term",
                      target_id="무관검색어", adgroup_id=adgroup_id, **kw)


def _settings(db, campaign_id="cmp-1", optimizer="ours"):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer=optimizer))
    db.commit()


def _write_result(before=None, after=None, created_ids=None, response=None):
    return naver_sa_writer.WriteResult(
        action="add_restricted_keywords",
        before=before if before is not None else [],
        response=response,
        after=after if after is not None else [],
        created_ids=created_ids if created_ids is not None else [],
    )


# ══════════════════════════════════════════════════════════════════
# GET /proposals — executable/not_executable_reason 직렬화
# ══════════════════════════════════════════════════════════════════

def test_serializer_executable_true_for_open_negative_keyword_regardless_of_status(client, db):
    """negative_keyword+search_term+adgroup_id면 pending이든 approved든 "구조적으로 실행
    가능한 제안인지"는 상태와 무관하게 True(콘솔 미리보기 용도)."""
    _negative_proposal(db, status="pending")
    _negative_proposal(db, status="approved")

    resp = client.get("/api/naver/ad/proposals")
    rows = resp.json()["rows"]
    assert len(rows) == 2
    for row in rows:
        assert row["executable"] is True
        assert row["not_executable_reason"] is None
        assert row["adgroup_id"] == "grp-1"


def test_serializer_executable_false_for_informational_proposal(client, db):
    _proposal(db, proposal_type="anomaly")

    resp = client.get("/api/naver/ad/proposals")
    row = resp.json()["rows"][0]
    assert row["executable"] is False
    assert row["not_executable_reason"] is not None


def test_serializer_executable_false_for_unopened_bid_up(client, db):
    _proposal(db, proposal_type="bid_up", status="approved")

    resp = client.get("/api/naver/ad/proposals")
    row = resp.json()["rows"][0]
    assert row["executable"] is False
    assert "미개방" in row["not_executable_reason"]


def test_serializer_executable_false_for_negative_keyword_missing_adgroup_id(client, db):
    _proposal(db, proposal_type="negative_keyword", target_type="search_term",
              target_id="무관검색어", adgroup_id=None)

    resp = client.get("/api/naver/ad/proposals")
    row = resp.json()["rows"][0]
    assert row["executable"] is False
    assert "adgroup_id" in row["not_executable_reason"]


def test_serializer_executable_false_for_negative_keyword_wrong_target_type(client, db):
    _proposal(db, proposal_type="negative_keyword", target_type="keyword",
              target_id="nkw-1", adgroup_id="grp-1")

    resp = client.get("/api/naver/ad/proposals")
    row = resp.json()["rows"][0]
    assert row["executable"] is False
    assert "target_type" in row["not_executable_reason"]


def test_proposals_status_filter_accepts_failed_and_executing(client, db):
    """T4: _PROPOSAL_STATUSES 확장 — failed/executing도 콘솔에서 필터 조회 가능해야 한다."""
    _proposal(db, status="failed")
    _proposal(db, status="executing")

    resp_failed = client.get("/api/naver/ad/proposals", params={"status": "failed"})
    assert resp_failed.status_code == 200
    assert len(resp_failed.json()["rows"]) == 1

    resp_executing = client.get("/api/naver/ad/proposals", params={"status": "executing"})
    assert resp_executing.status_code == 200
    assert len(resp_executing.json()["rows"]) == 1


# ══════════════════════════════════════════════════════════════════
# POST /proposals/{id}/status — 상태 전이
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("current,target", [
    ("pending", "approved"),
    ("pending", "rejected"),
    ("approved", "rejected"),
    ("failed", "approved"),
    ("failed", "rejected"),
])
def test_status_transition_allowed(client, db, current, target):
    p = _proposal(db, status=current)

    resp = client.post(f"/api/naver/ad/proposals/{p.id}/status", json={"status": target})
    assert resp.status_code == 200
    assert resp.json()["status"] == target

    db.refresh(p)
    assert p.status == target


@pytest.mark.parametrize("current,target", [
    ("executing", "approved"),
    ("executing", "rejected"),
    ("expired", "approved"),
    ("rejected", "approved"),
])
def test_status_transition_forbidden(client, db, current, target):
    p = _proposal(db, status=current)

    resp = client.post(f"/api/naver/ad/proposals/{p.id}/status", json={"status": target})
    assert resp.status_code == 409

    db.refresh(p)
    assert p.status == current  # DB 무변화


def test_status_transition_already_executed_approved_cannot_be_rejected(client, db):
    """approved→rejected는 executed_change_log_id IS NULL인 경우만 — 이미 실행된 건 반려 불가."""
    p = _proposal(db, status="approved")
    p.executed_change_log_id = 999
    db.commit()

    resp = client.post(f"/api/naver/ad/proposals/{p.id}/status", json={"status": "rejected"})
    assert resp.status_code == 409

    db.refresh(p)
    assert p.status == "approved"


@pytest.mark.parametrize("current", ["pending", "failed"])
def test_status_transition_approval_records_console_source(client, db, current):
    """X1a T5: 콘솔 승인 경로는 approval_source='console'로 감사 기록 — delegation_gate의
    'delegation'과 대칭(누가 승인했는지 구분)."""
    p = _proposal(db, status=current)

    resp = client.post(f"/api/naver/ad/proposals/{p.id}/status", json={"status": "approved"})
    assert resp.status_code == 200
    assert resp.json()["approval_source"] == "console"

    db.refresh(p)
    assert p.approval_source == "console"


def test_status_transition_rejection_does_not_set_approval_source(client, db):
    p = _proposal(db, status="pending")
    resp = client.post(f"/api/naver/ad/proposals/{p.id}/status", json={"status": "rejected"})
    assert resp.status_code == 200
    assert resp.json()["approval_source"] is None
    db.refresh(p)
    assert p.approval_source is None


def test_status_transition_rejection_preserves_prior_approval_source(client, db):
    """반려는 approval_source를 지우지 않는다(이력 보존)."""
    p = _proposal(db, status="approved")
    p.approval_source = "delegation"
    db.commit()

    resp = client.post(f"/api/naver/ad/proposals/{p.id}/status", json={"status": "rejected"})
    assert resp.status_code == 200
    assert resp.json()["approval_source"] == "delegation"
    db.refresh(p)
    assert p.approval_source == "delegation"


def test_status_transition_rejects_invalid_status_value(client, db):
    p = _proposal(db, status="pending")
    resp = client.post(f"/api/naver/ad/proposals/{p.id}/status", json={"status": "bogus"})
    assert resp.status_code == 400


def test_status_transition_rejects_pending_as_target(client, db):
    """body.status는 approved|rejected만 허용 — pending은 그 외 값이라 400(section A 계약)."""
    p = _proposal(db, status="pending")
    resp = client.post(f"/api/naver/ad/proposals/{p.id}/status", json={"status": "pending"})
    assert resp.status_code == 400


def test_status_transition_missing_body_field_422(client, db):
    p = _proposal(db, status="pending")
    resp = client.post(f"/api/naver/ad/proposals/{p.id}/status", json={})
    assert resp.status_code == 422


def test_status_transition_missing_proposal_404(client):
    resp = client.post("/api/naver/ad/proposals/999999/status", json={"status": "approved"})
    assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════
# POST /proposals/{id}/execute
# ══════════════════════════════════════════════════════════════════

def test_execute_success_returns_before_after_and_updates_proposal(client, db):
    p = _negative_proposal(db, status="approved")
    _settings(db, optimizer="ours")
    after_rows = [{"nccAdgroupRestrictKwdId": "rkw-9", "keyword": "무관검색어"}]
    result = _write_result(before=[], after=after_rows, created_ids=["rkw-9"], response=after_rows)

    with patch.object(harness.naver_sa_writer, "add_restricted_keywords", return_value=result) as mock_write:
        resp = client.post(f"/api/naver/ad/proposals/{p.id}/execute")

    mock_write.assert_called_once_with("grp-1", ["무관검색어"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "executed"
    assert body["before"] == []
    assert body["after"]["after"] == after_rows
    assert body["after"]["created_ids"] == ["rkw-9"]
    assert body["proposal"]["status"] == "approved"  # 성공 시 approved 복원
    assert body["proposal"]["executed_change_log_id"] == body["change_log_id"]

    db.refresh(p)
    assert p.status == "approved"
    assert p.executed_change_log_id == body["change_log_id"]
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert logs[0].outcome == "executed"


def test_execute_pending_proposal_returns_409_and_db_untouched(client, db):
    p = _negative_proposal(db, status="pending")
    _settings(db, optimizer="ours")

    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        resp = client.post(f"/api/naver/ad/proposals/{p.id}/execute")

    mock_write.assert_not_called()
    assert resp.status_code == 409
    db.refresh(p)
    assert p.status == "pending"
    assert p.executed_change_log_id is None
    assert db.query(NaverChangeLog).count() == 0


def test_execute_unopened_action_returns_409_without_consuming_proposal(client, db):
    """핵심 회귀 테스트: bid_up(미개방 액션)을 harness에 그대로 넘기면 dry-run 경로가
    change_log를 만들고 executed_change_log_id를 박아 제안을 "소비"해버리는 함정이 있다
    (naver_execution_harness.execute의 effective_dry_run 강제 로직). 라우터의 사전 차단이
    이를 막아야 한다 — change_log 0건, status 그대로, executed_change_log_id None 확인."""
    p = _proposal(db, proposal_type="bid_up", status="approved")
    _settings(db, optimizer="ours")

    resp = client.post(f"/api/naver/ad/proposals/{p.id}/execute")

    assert resp.status_code == 409
    assert "미개방" in resp.json()["detail"]

    db.refresh(p)
    assert p.status == "approved"
    assert p.executed_change_log_id is None
    assert db.query(NaverChangeLog).count() == 0


def test_execute_writer_error_returns_502_and_marks_proposal_failed(client, db):
    p = _negative_proposal(db, status="approved")
    _settings(db, optimizer="ours")

    with patch.object(harness.naver_sa_writer, "add_restricted_keywords",
                       side_effect=naver_sa_writer.WriteError("모의 실패")):
        resp = client.post(f"/api/naver/ad/proposals/{p.id}/execute")

    assert resp.status_code == 502
    assert "WriteError" in resp.json()["detail"]

    db.refresh(p)
    assert p.status == "failed"
    assert p.executed_change_log_id is None
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert logs[0].outcome == "failed"


def test_execute_wrong_target_type_returns_422_and_marks_proposal_failed(client, db):
    """target_type='keyword'인 approved negative_keyword — harness 자체 가드
    (MissingExecutionTargetError)로 흘려보내 422+failed 감사 기록(라우터가 여기서
    선점하지 않는다 — 미개방 케이스와는 다른 경로, naver_ad.py execute 라우터 docstring 참조)."""
    p = _proposal(db, proposal_type="negative_keyword", target_type="keyword",
                  target_id="nkw-1", adgroup_id="grp-1", status="approved")
    _settings(db, optimizer="ours")

    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        resp = client.post(f"/api/naver/ad/proposals/{p.id}/execute")

    mock_write.assert_not_called()
    assert resp.status_code == 422

    db.refresh(p)
    assert p.status == "failed"
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert logs[0].outcome == "failed"


def test_execute_optimizer_not_ours_returns_409(client, db):
    p = _negative_proposal(db, status="approved")
    _settings(db, optimizer="none")

    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        resp = client.post(f"/api/naver/ad/proposals/{p.id}/execute")

    mock_write.assert_not_called()
    assert resp.status_code == 409

    db.refresh(p)
    assert p.status == "approved"
    assert p.executed_change_log_id is None


def test_execute_missing_proposal_404(client):
    resp = client.post("/api/naver/ad/proposals/999999/execute")
    assert resp.status_code == 404


def test_execute_already_executed_returns_409(client, db):
    p = _negative_proposal(db, status="approved")
    p.executed_change_log_id = 12345
    db.commit()
    _settings(db, optimizer="ours")

    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        resp = client.post(f"/api/naver/ad/proposals/{p.id}/execute")

    mock_write.assert_not_called()
    assert resp.status_code == 409
