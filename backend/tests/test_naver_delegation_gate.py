# test_naver_delegation_gate.py — X1a T5 E2 위임 스위치(D-NAO-25 부분 게이트) 단위테스트.
# delegation_gate.run_gate가 agree 평결 중 위임된 유형만 자동승인+실행하는지, 그 외에는
# 전부 무접촉(fail-closed)인지 검증. writer는 전부 mock — 실제 HTTP 0.
from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAccountSettings,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverExpertReview,
    NaverExpertReviewRun,
    NaverProposal,
)
from app.services.naver_ad import delegation_gate
from app.services.naver_ad import naver_sa_writer

AS_OF = date(2026, 7, 10)


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


def _proposal(db, proposal_type="negative_keyword", campaign_id="cmp-1", status="pending",
              target_type="search_term", target_id="검색어", adgroup_id="grp-1") -> NaverProposal:
    p = NaverProposal(
        proposal_type=proposal_type, target_type=target_type, target_id=target_id,
        campaign_id=campaign_id, adgroup_id=adgroup_id,
        rationale="테스트 근거", expected_effect="테스트 예상효과", status=status,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _settings(db, campaign_id="cmp-1", optimizer="ours"):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer=optimizer))
    db.commit()


def _delegate(db, types) -> None:
    db.add(NaverAccountSettings(key="expert_delegated_types", value_json=json.dumps(sorted(types))))
    db.commit()


def _run(db, status="ok") -> NaverExpertReviewRun:
    r = NaverExpertReviewRun(as_of=AS_OF, model="m", prompt_version="v1", status=status)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _review(db, run_id, proposal_id, verdict="agree") -> NaverExpertReview:
    r = NaverExpertReview(run_id=run_id, as_of=AS_OF, proposal_id=proposal_id, verdict=verdict)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _write_result(before=None, after=None, created_ids=None):
    return naver_sa_writer.WriteResult(
        action="add_restricted_keywords",
        before=before if before is not None else [],
        response=after,
        after=after if after is not None else [],
        created_ids=created_ids if created_ids is not None else [],
    )


def _patch_writer(**kw):
    return patch.object(delegation_gate.naver_execution_harness.naver_sa_writer, "add_restricted_keywords", **kw)


# ── get_delegated_types / delegable_types ──

def test_get_delegated_types_missing_row_returns_empty_set(db):
    assert delegation_gate.get_delegated_types(db) == set()


def test_get_delegated_types_parses_stored_json(db):
    _delegate(db, {"negative_keyword"})
    assert delegation_gate.get_delegated_types(db) == {"negative_keyword"}


def test_get_delegated_types_parse_failure_returns_empty_set_fail_closed(db):
    db.add(NaverAccountSettings(key="expert_delegated_types", value_json="not-json{"))
    db.commit()
    assert delegation_gate.get_delegated_types(db) == set()


def test_get_delegated_types_non_list_json_returns_empty_set_fail_closed(db):
    db.add(NaverAccountSettings(key="expert_delegated_types", value_json=json.dumps({"a": 1})))
    db.commit()
    assert delegation_gate.get_delegated_types(db) == set()


def test_delegable_types_tracks_open_actions_p3():
    """P3(D-NAO-42-f) 시점: OPEN_ACTIONS가 예산(update_budget)까지 확장됐으므로 delegable도
    자동으로 넓어진다(delegable_types() 자체 docstring: "OPEN_ACTIONS가 넓어지면 이 함수도
    자동으로 넓어짐" — 구 test_delegable_types_tracks_open_actions_x1b를 P3 계약에 맞춰
    갱신). 자동 발사 자체는 여전히 위임 스위치(D-NAO-5/25, Jino만)가 별도로 켜야 하지만,
    "위임 가능 유형" 집합 자체는 이미 넓어진다."""
    assert delegation_gate.delegable_types() == {
        "negative_keyword", "bid_up", "bid_down", "growth_bid_up", "pause", "resume",
        "budget_up", "budget_down",
    }


# ── run_gate: 핵심 게이트 로직 ──

def test_empty_delegated_types_returns_skipped(db):
    p = _proposal(db)
    _settings(db, optimizer="ours")
    run = _run(db)
    _review(db, run.id, p.id, verdict="agree")

    summary = delegation_gate.run_gate(db, run.id)

    assert summary["status"] == "skipped"
    db.refresh(p)
    assert p.status == "pending"
    assert p.approval_source is None


def test_on_type_agree_guardrails_pass_auto_approves_and_executes(db):
    p = _proposal(db)
    _settings(db, optimizer="ours")
    _delegate(db, {"negative_keyword"})
    run = _run(db)
    _review(db, run.id, p.id, verdict="agree")

    after_rows = [{"nccAdgroupRestrictKwdId": "rkw-9", "keyword": "검색어"}]
    result = _write_result(before=[], after=after_rows, created_ids=["rkw-9"])

    with _patch_writer(return_value=result) as mock_write:
        summary = delegation_gate.run_gate(db, run.id)

    mock_write.assert_called_once_with("grp-1", ["검색어"])
    assert summary["status"] == "ok"
    assert summary["delegated_types"] == ["negative_keyword"]
    assert summary["agree_count"] == 1
    assert summary["auto_approved"] == 1
    assert summary["executed"] == 1
    assert summary["failed"] == 0

    db.refresh(p)
    assert p.status == "approved"
    assert p.approval_source == "delegation"
    assert p.executed_change_log_id is not None

    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    # outcome은 D+14 채점 전엔 NULL이 정상(X1b T5 배선확인 — proposal_scoreboard가
    # outcome IS NULL로 미검증 실행 건을 찾는다). 성공 판별은 after_value로.
    assert logs[0].outcome is None
    assert logs[0].after_value is not None
    assert logs[0].dry_run is False


def test_off_type_agree_leaves_proposal_untouched(db):
    """완료기준③ 핵심: 위임 안 된 유형은 agree여도 무접촉."""
    off = _proposal(db, proposal_type="bid_up", target_type="keyword", target_id="nkw-1", adgroup_id=None)
    _settings(db, optimizer="ours")
    _delegate(db, {"negative_keyword"})  # bid_up은 위임 대상이 아님
    run = _run(db)
    _review(db, run.id, off.id, verdict="agree")

    with _patch_writer() as mock_write:
        summary = delegation_gate.run_gate(db, run.id)

    mock_write.assert_not_called()
    assert summary["auto_approved"] == 0
    assert summary["executed"] == 0
    assert summary["skipped"]["not_delegated"] == 1

    db.refresh(off)
    assert off.status == "pending"
    assert off.approval_source is None


@pytest.mark.parametrize("verdict", ["partial", "reject", "insufficient_evidence"])
def test_on_type_non_agree_verdict_untouched(db, verdict):
    p = _proposal(db)
    _settings(db, optimizer="ours")
    _delegate(db, {"negative_keyword"})
    run = _run(db)
    _review(db, run.id, p.id, verdict=verdict)

    with _patch_writer() as mock_write:
        summary = delegation_gate.run_gate(db, run.id)

    mock_write.assert_not_called()
    assert summary["auto_approved"] == 0
    assert summary["agree_count"] == 0  # verdict!='agree'는 애초에 조회 대상이 아님

    db.refresh(p)
    assert p.status == "pending"
    assert p.approval_source is None


def test_on_type_agree_but_missing_adgroup_id_blocked_untouched(db):
    p = _proposal(db, adgroup_id=None)
    _settings(db, optimizer="ours")
    _delegate(db, {"negative_keyword"})
    run = _run(db)
    _review(db, run.id, p.id, verdict="agree")

    with _patch_writer() as mock_write:
        summary = delegation_gate.run_gate(db, run.id)

    mock_write.assert_not_called()
    assert summary["auto_approved"] == 0
    assert summary["skipped"]["blocked"] == 1

    db.refresh(p)
    assert p.status == "pending"
    assert p.approval_source is None


def test_on_type_agree_but_wrong_target_type_blocked_untouched(db):
    p = _proposal(db, target_type="keyword", target_id="nkw-1")
    _settings(db, optimizer="ours")
    _delegate(db, {"negative_keyword"})
    run = _run(db)
    _review(db, run.id, p.id, verdict="agree")

    with _patch_writer() as mock_write:
        summary = delegation_gate.run_gate(db, run.id)

    mock_write.assert_not_called()
    assert summary["skipped"]["blocked"] == 1
    db.refresh(p)
    assert p.status == "pending"


def test_on_type_agree_but_status_failed_untouched_no_auto_retry(db):
    """failed→approved 재승인은 영구 사람 전용 — 자동 재시도 금지 불변."""
    p = _proposal(db, status="failed")
    _settings(db, optimizer="ours")
    _delegate(db, {"negative_keyword"})
    run = _run(db)
    _review(db, run.id, p.id, verdict="agree")

    with _patch_writer() as mock_write:
        summary = delegation_gate.run_gate(db, run.id)

    mock_write.assert_not_called()
    assert summary["skipped"]["not_pending"] == 1

    db.refresh(p)
    assert p.status == "failed"
    assert p.approval_source is None


def test_on_type_agree_but_optimizer_not_ours_untouched(db):
    p = _proposal(db)
    _settings(db, optimizer="none")
    _delegate(db, {"negative_keyword"})
    run = _run(db)
    _review(db, run.id, p.id, verdict="agree")

    with _patch_writer() as mock_write:
        summary = delegation_gate.run_gate(db, run.id)

    mock_write.assert_not_called()
    assert summary["skipped"]["optimizer"] == 1

    db.refresh(p)
    assert p.status == "pending"
    assert p.approval_source is None


def test_execute_exception_marks_that_proposal_failed_and_continues(db):
    p1 = _proposal(db, target_id="검색어1")
    p2 = _proposal(db, target_id="검색어2")
    _settings(db, optimizer="ours")
    _delegate(db, {"negative_keyword"})
    run = _run(db)
    _review(db, run.id, p1.id, verdict="agree")
    _review(db, run.id, p2.id, verdict="agree")

    ok_result = _write_result(
        before=[], after=[{"nccAdgroupRestrictKwdId": "rkw-2", "keyword": "검색어2"}], created_ids=["rkw-2"],
    )

    def fake_write(adgroup_id, keywords):
        if keywords == ["검색어1"]:
            raise naver_sa_writer.WriteError("모의 실패")
        return ok_result

    with _patch_writer(side_effect=fake_write) as mock_write:
        summary = delegation_gate.run_gate(db, run.id)

    assert mock_write.call_count == 2
    assert summary["auto_approved"] == 2
    assert summary["executed"] == 1
    assert summary["failed"] == 1

    db.refresh(p1)
    db.refresh(p2)
    assert p1.status == "failed"  # harness 경로 — change_log outcome='failed' 감사 기록 완료
    assert p1.approval_source == "delegation"  # 승인 출처는 이력 보존(반려와 동일 원칙)
    assert p2.status == "approved"
    assert p2.executed_change_log_id is not None


def test_rerun_same_run_id_does_not_double_execute(db):
    p = _proposal(db)
    _settings(db, optimizer="ours")
    _delegate(db, {"negative_keyword"})
    run = _run(db)
    _review(db, run.id, p.id, verdict="agree")

    after_rows = [{"nccAdgroupRestrictKwdId": "rkw-9", "keyword": "검색어"}]
    result = _write_result(before=[], after=after_rows, created_ids=["rkw-9"])

    with _patch_writer(return_value=result) as mock_write:
        first = delegation_gate.run_gate(db, run.id)
        second = delegation_gate.run_gate(db, run.id)

    assert mock_write.call_count == 1
    assert first["executed"] == 1
    assert second["executed"] == 0
    assert second["auto_approved"] == 0
    assert second["skipped"]["not_pending"] == 1


# ── run 자체 검증 (codex R2 — 게이트가 자동승인 경계이므로 내부에서도 fail-closed) ──

def test_degraded_run_skipped_fail_closed_inside_gate(db):
    """호출측(expert_desk)이 걸러주지 않아도 run_gate 자체가 degraded run을 거부해야 한다."""
    p = _proposal(db)
    _settings(db, optimizer="ours")
    _delegate(db, {"negative_keyword"})
    run = _run(db, status="degraded")
    _review(db, run.id, p.id, verdict="agree")

    with _patch_writer() as mock_write:
        summary = delegation_gate.run_gate(db, run.id)

    mock_write.assert_not_called()
    assert summary["status"] == "skipped"
    assert "fail-closed" in summary["reason"]
    db.refresh(p)
    assert p.status == "pending"
    assert p.approval_source is None


def test_missing_run_skipped_fail_closed(db):
    _delegate(db, {"negative_keyword"})
    summary = delegation_gate.run_gate(db, 99999)
    assert summary["status"] == "skipped"
    assert "fail-closed" in summary["reason"]


def test_claim_race_lost_skips_without_write(db):
    """_eligible 통과 후 조건부 UPDATE 사이에 상태가 바뀐 레이스(codex R2) — 클레임 실패 시
    writer 무호출·auto_approved 0·not_pending 카운트. _eligible을 강제로 통과시켜 재현."""
    p = _proposal(db, status="approved")  # 이미 pending이 아님 → 클레임은 반드시 실패
    _settings(db, optimizer="ours")
    _delegate(db, {"negative_keyword"})
    run = _run(db)
    _review(db, run.id, p.id, verdict="agree")

    with _patch_writer() as mock_write, patch.object(delegation_gate, "_eligible", return_value=True):
        summary = delegation_gate.run_gate(db, run.id)

    mock_write.assert_not_called()
    assert summary["auto_approved"] == 0
    assert summary["executed"] == 0
    assert summary["skipped"]["not_pending"] == 1
    db.refresh(p)
    assert p.status == "approved"
    assert p.approval_source is None
