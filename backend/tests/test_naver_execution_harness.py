# test_naver_execution_harness.py — 듀얼모드 스프린트 Phase 5 naver_execution_harness 단위테스트
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverCampaignSettings, NaverChangeLog, NaverProposal
from app.services.naver_ad import naver_execution_harness as harness


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


def _proposal(db, proposal_type="bid_up", campaign_id="cmp1", status="approved"):
    p = NaverProposal(
        proposal_type=proposal_type, target_type="keyword", target_id="nkw-1",
        campaign_id=campaign_id, rationale="테스트 근거", expected_effect="테스트 예상효과",
        status=status,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


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


def test_open_actions_is_empty_this_sprint(db):
    """계획서 §4-Phase5 불변 가드레일: 이번 스프린트는 실제 쓰기 개방 스코프 밖."""
    assert harness.OPEN_ACTIONS == frozenset()
