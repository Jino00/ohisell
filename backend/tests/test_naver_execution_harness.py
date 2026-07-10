# test_naver_execution_harness.py — 듀얼모드 스프린트 Phase 5 naver_execution_harness 단위테스트
# + X1a T3 실쓰기 개방(add_negative_keyword) 테스트. writer는 전부 mock — 실제 HTTP 0.
from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverCampaignSettings, NaverChangeLog, NaverProposal
from app.services.naver_ad import naver_execution_harness as harness
from app.services.naver_ad import naver_sa_writer


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


def _proposal(db, proposal_type="bid_up", campaign_id="cmp1", status="approved",
              target_type="keyword", target_id="nkw-1", adgroup_id=None):
    p = NaverProposal(
        proposal_type=proposal_type, target_type=target_type, target_id=target_id,
        campaign_id=campaign_id, adgroup_id=adgroup_id,
        rationale="테스트 근거", expected_effect="테스트 예상효과",
        status=status,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _negative_proposal(db, adgroup_id="grp-1", **kw):
    return _proposal(db, proposal_type="negative_keyword", target_type="search_term",
                     target_id="무관검색어", adgroup_id=adgroup_id, **kw)


def _settings(db, campaign_id="cmp1", optimizer="ours"):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer=optimizer))
    db.commit()


# ── optimizer 하드체크(D-NAO-13) ──
def test_execute_blocked_when_optimizer_not_ours(db):
    p = _proposal(db)
    _settings(db, optimizer="none")
    with pytest.raises(harness.OptimizerGuardError):
        harness.execute(db, p.id)


def test_execute_blocked_when_optimizer_is_mop(db):
    p = _proposal(db)
    _settings(db, optimizer="mop")
    with pytest.raises(harness.OptimizerGuardError):
        harness.execute(db, p.id)


def test_execute_blocked_when_no_settings_row_defaults_to_none(db):
    p = _proposal(db)
    # NaverCampaignSettings 행 자체가 없음 — 기본값 'none' 취급, 실행 차단
    with pytest.raises(harness.OptimizerGuardError):
        harness.execute(db, p.id)


# ── 실행 불가 유형(정보성 제안) ──
@pytest.mark.parametrize("proposal_type", [
    "anomaly", "anomaly_freshness", "account_brief", "trigger_pacing", "trigger_cpc_spike",
])
def test_execute_rejects_informational_proposal_types(db, proposal_type):
    p = _proposal(db, proposal_type=proposal_type)
    _settings(db, optimizer="ours")
    with pytest.raises(harness.ActionNotExecutableError):
        harness.execute(db, p.id)


# ── dry-run 정상 경로 ──
@pytest.mark.parametrize("proposal_type,expected_action", [
    ("bid_up", "update_bid"),
    ("bid_down", "update_bid"),
    ("growth_bid_up", "update_bid"),
    ("negative_keyword", "add_negative_keyword"),
    ("budget_up", "update_budget"),
])
def test_execute_dry_run_records_change_log(db, proposal_type, expected_action):
    p = _proposal(db, proposal_type=proposal_type)
    _settings(db, optimizer="ours")

    log_entry = harness.execute(db, p.id)

    assert log_entry.dry_run is True
    assert log_entry.action == expected_action
    assert log_entry.campaign_id == "cmp1"
    assert log_entry.proposal_id == p.id
    assert log_entry.predicted_json == "테스트 예상효과"
    assert log_entry.verify_date == (log_entry.executed_at.date() + timedelta(days=14))

    db.refresh(p)
    assert p.executed_change_log_id == log_entry.id

    saved = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(saved) == 1


def test_execute_forces_dry_run_even_when_caller_requests_live(db):
    """OPEN_ACTIONS가 비어 있는 한 dry_run=False를 넘겨도 강제로 dry-run 처리된다(D-NAO-5)."""
    p = _proposal(db)
    _settings(db, optimizer="ours")
    log_entry = harness.execute(db, p.id, dry_run=False)
    assert log_entry.dry_run is True


def test_execute_raises_value_error_for_missing_proposal(db):
    with pytest.raises(ValueError):
        harness.execute(db, 999999)


# ── 승인 게이트(D-NAO-5) + 재실행 방지 ──
@pytest.mark.parametrize("status", ["pending", "rejected", "expired"])
def test_execute_blocked_when_not_approved(db, status):
    p = _proposal(db, status=status)
    _settings(db, optimizer="ours")
    with pytest.raises(harness.ProposalNotApprovedError):
        harness.execute(db, p.id)


def test_execute_blocked_on_second_call_already_executed(db):
    p = _proposal(db)
    _settings(db, optimizer="ours")
    harness.execute(db, p.id)
    with pytest.raises(harness.AlreadyExecutedError):
        harness.execute(db, p.id)

    saved = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(saved) == 1  # 재실행 차단 — change_log 중복 기록 없음


def test_open_actions_contains_only_negative_keyword_x1a(db):
    """X1a T3: D-NAO-16 개방 순서의 1단계(제외키워드)만 개방 — 정지·재개/입찰/예산은 스코프 밖.
    (구 test_open_actions_is_empty_this_sprint를 T3 계약에 따라 갱신 — 듀얼모드 스프린트의
    '항상 빈 집합' 불변식은 X 스프린트 T3에서 공식 해제됨.)"""
    assert harness.OPEN_ACTIONS == frozenset({"add_negative_keyword"})


# ── X1a T3: 실쓰기 개방(add_negative_keyword) ──


def _write_result(before=None, after=None, created_ids=None, response=None):
    return naver_sa_writer.WriteResult(
        action="add_restricted_keywords",
        before=before if before is not None else [],
        response=response,
        after=after if after is not None else [],
        created_ids=created_ids if created_ids is not None else [],
    )


def test_live_execute_negative_keyword_success_records_measured_change_log(db):
    p = _negative_proposal(db)
    _settings(db, optimizer="ours")
    after_rows = [{"nccAdgroupRestrictKwdId": "rkw-9", "keyword": "무관검색어"}]
    result = _write_result(before=[], after=after_rows, created_ids=["rkw-9"],
                           response=after_rows)

    with patch.object(harness.naver_sa_writer, "add_restricted_keywords",
                      return_value=result) as mock_write:
        log_entry = harness.execute(db, p.id, dry_run=False)

    mock_write.assert_called_once_with("grp-1", ["무관검색어"])
    assert log_entry.dry_run is False
    assert log_entry.outcome == "executed"
    assert json.loads(log_entry.before_value) == []
    after_payload = json.loads(log_entry.after_value)
    assert after_payload["after"] == after_rows
    assert after_payload["created_ids"] == ["rkw-9"]  # 원복 원료 — 반드시 저장
    assert log_entry.verify_date == (log_entry.executed_at.date() + timedelta(days=14))

    db.refresh(p)
    assert p.executed_change_log_id == log_entry.id
    assert p.status == "approved"  # 성공 시 status는 건드리지 않음(현행 유지)


def test_live_execute_missing_adgroup_id_raises_before_writer(db):
    p = _negative_proposal(db, adgroup_id=None)
    _settings(db, optimizer="ours")

    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_write.assert_not_called()
    # 사전 검증 실패는 실행 시도가 아님 — change_log 신규 행 0
    assert db.query(NaverChangeLog).count() == 0


@pytest.mark.parametrize("exc_cls", [
    naver_sa_writer.WriteVerificationError, naver_sa_writer.WriteError,
])
def test_live_execute_writer_failure_fail_closed(db, exc_cls):
    """실패 시: status='failed'(자동 재시도 차단) + change_log 전건 기록(D-NAO-12,
    outcome='failed') 커밋 후 원 예외 재전파. executed_change_log_id는 성공 전용."""
    p = _negative_proposal(db)
    _settings(db, optimizer="ours")

    with patch.object(harness.naver_sa_writer, "add_restricted_keywords",
                      side_effect=exc_cls("모의 실패")):
        with pytest.raises(exc_cls):
            harness.execute(db, p.id, dry_run=False)

    db.refresh(p)
    assert p.status == "failed"
    assert p.executed_change_log_id is None

    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert logs[0].outcome == "failed"
    assert logs[0].dry_run is False
    assert f"[실행 실패] {exc_cls.__name__}: 모의 실패" in logs[0].rationale


def test_live_execute_wrong_target_type_raises_before_writer(db):
    """격상 경로(_bid_proposal economic_ceiling<=0)의 negative_keyword는 target_type='keyword',
    target_id='nkw-…'(키워드 ID) — restricted-keywords는 검색어 텍스트를 등록하는 API라서
    그대로 쓰면 무의미한 문자열이 제외키워드로 등록된다. adgroup_id가 채워져 있어도 차단."""
    p = _proposal(db, proposal_type="negative_keyword", target_type="keyword",
                  target_id="nkw-1", adgroup_id="grp-1")
    _settings(db, optimizer="ours")

    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_write.assert_not_called()
    assert db.query(NaverChangeLog).count() == 0  # 사전 검증 실패 — change_log 미기록


def test_open_action_without_executor_blocked_by_write_not_opened(db, monkeypatch):
    """방벽: OPEN_ACTIONS에 실수로 추가돼도 _WRITE_EXECUTORS에 구현이 없으면 fail-closed."""
    p = _proposal(db)  # bid_up → update_bid
    _settings(db, optimizer="ours")
    monkeypatch.setattr(harness, "OPEN_ACTIONS",
                        frozenset({"add_negative_keyword", "update_bid"}))

    with pytest.raises(harness.WriteNotOpenedError):
        harness.execute(db, p.id, dry_run=False)


def test_opened_action_still_dry_run_by_default(db):
    """dry_run=True(기본)면 개방된 액션이어도 쓰기 없이 기존 dry-run 기록(동작 보존)."""
    p = _negative_proposal(db)
    _settings(db, optimizer="ours")

    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        log_entry = harness.execute(db, p.id)

    mock_write.assert_not_called()
    assert log_entry.dry_run is True
    assert log_entry.outcome is None  # dry-run은 기존대로 outcome 미기록
