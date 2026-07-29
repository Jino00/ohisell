# test_naver_execution_harness.py — 듀얼모드 스프린트 Phase 5 naver_execution_harness 단위테스트
# + X1a T3 실쓰기 개방(add_negative_keyword) 테스트. writer는 전부 mock — 실제 HTTP 0.
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverCampaignSettings, NaverChangeLog, NaverEntity, NaverProposal
from app.services.naver_ad import naver_execution_harness as harness
from app.services.naver_ad import naver_sa_writer
from app.utils.kst import kst_now


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
    """액션이 OPEN_ACTIONS 밖이면 dry_run=False를 넘겨도 강제로 dry-run 처리된다(D-NAO-5).
    P3(D-NAO-42-f)로 update_budget까지 개방되며 매핑된 액션(add_negative_keyword/update_bid/
    set_user_lock/update_budget)이 전부 열렸다 — 게이트 자체의 동작(닫힌 액션이면 강제
    dry-run)을 검증하려면 OPEN_ACTIONS를 일시적으로 비워 시뮬레이션한다."""
    p = _proposal(db, proposal_type="bid_up")
    _settings(db, optimizer="ours")
    with patch.object(harness, "OPEN_ACTIONS", frozenset()):
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


def test_open_actions_contains_negative_keyword_bid_lock_and_budget_p3(db):
    """P3(D-NAO-42-f): D-NAO-16 개방 순서 4단계(제외키워드→정지·재개→입찰→예산) 전부 개방
    (D-NAO-34 금지선 개정). (구 test_open_actions_contains_negative_keyword_bid_and_lock_x1b를
    P3 계약에 따라 갱신.)"""
    assert harness.OPEN_ACTIONS == frozenset(
        {"add_negative_keyword", "update_bid", "set_user_lock", "update_budget", "exclude_search_term"}
    )


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

    status_during_write = []

    def fake_write(adgroup_id, keywords):
        # [codex P1] 클레임 우선: writer 호출 시점에 이미 내구 클레임(executing)이 커밋돼
        # 있어야 크래시/동시호출 시 approved 게이트가 재진입을 자연 차단한다.
        status_during_write.append(p.status)
        return result

    with patch.object(harness.naver_sa_writer, "add_restricted_keywords",
                      side_effect=fake_write) as mock_write:
        log_entry = harness.execute(db, p.id, dry_run=False)

    mock_write.assert_called_once_with("grp-1", ["무관검색어"])
    assert status_during_write == ["executing"]  # 쓰기 직전 클레임이 이미 확정돼 있었음
    assert log_entry.dry_run is False
    assert log_entry.outcome is None  # 채점 전 정상(X1b T5 배선확인) — 성공 판별은 after_value
    assert json.loads(log_entry.before_value) == []
    after_payload = json.loads(log_entry.after_value)
    assert after_payload["after"] == after_rows
    assert after_payload["created_ids"] == ["rkw-9"]  # 원복 원료 — 반드시 저장
    assert log_entry.verify_date == (log_entry.executed_at.date() + timedelta(days=14))

    db.refresh(p)
    assert p.executed_change_log_id == log_entry.id
    assert p.status == "approved"  # 성공 시 approved 복원 — "실행됨" 마커는 executed_change_log_id


def test_live_execute_missing_adgroup_id_raises_before_writer(db):
    """[codex P1] 사전 가드 실패도 운영자 관점에선 시도 — 전건 기록(D-NAO-12) + 영구 결함이라
    failed 종결(재승인해도 데이터가 안 바뀌는 제안의 재승인 루프 방지). writer는 미호출."""
    p = _negative_proposal(db, adgroup_id=None)
    _settings(db, optimizer="ours")

    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_write.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert logs[0].outcome == "failed"
    assert logs[0].dry_run is False
    assert "[실행 불가]" in logs[0].rationale


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
    db.refresh(p)
    assert p.status == "failed"  # [codex P1] 영구 결함 — failed 종결(재승인 루프 방지)
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert logs[0].outcome == "failed"
    assert "[실행 불가]" in logs[0].rationale


def test_live_execute_claim_race_lost_raises_already_executed(db):
    """[codex R2 P1] 클레임은 조건부 UPDATE로 원자화 — 두 실행자가 동시에 approved를 읽어도
    (T4 콘솔 라우터·X2 flight_loop 크론 다중 진입) UPDATE ... WHERE status='approved'는 한쪽만
    1행 성공. '게이트는 통과했지만 클레임 UPDATE가 0행'인 경합을 직접 재현: 사전 가드는
    통과하는 제안의 status를 다른 실행자가 선점한 값('executing')으로 바꿔놓고 executor를
    직접 호출한다."""
    p = _negative_proposal(db)
    _settings(db, optimizer="ours")
    # 다른 실행자의 선점 시뮬레이션 — approved 게이트 통과 이후·클레임 이전에 상태가 바뀐 상황
    p.status = "executing"
    db.commit()

    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        with pytest.raises(harness.AlreadyExecutedError):
            harness._execute_add_negative_keyword(db, p, kst_now())

    mock_write.assert_not_called()
    db.refresh(p)
    assert p.status == "executing"  # 선점자의 클레임을 훼손하지 않음
    assert db.query(NaverChangeLog).count() == 0  # 쓰기 시도 없음 — 기록도 없음


def test_open_action_without_executor_blocked_by_write_not_opened(db, monkeypatch):
    """방벽: OPEN_ACTIONS에 실수로 추가돼도 _WRITE_EXECUTORS에 구현이 없으면 fail-closed.
    P3(D-NAO-42-f)로 매핑된 액션(add_negative_keyword/update_bid/set_user_lock/
    update_budget)이 전부 구현됐으므로, 가상의 미구현 액션으로 이중 방벽 자체를 검증한다."""
    p = _proposal(db, proposal_type="budget_up")
    _settings(db, optimizer="ours")
    monkeypatch.setattr(
        harness, "_ACTION_BY_PROPOSAL_TYPE",
        {**harness._ACTION_BY_PROPOSAL_TYPE, "budget_up": "future_action"},
    )
    monkeypatch.setattr(harness, "OPEN_ACTIONS", frozenset({"future_action"}))

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


# ── X1b T4: update_bid 실쓰기 개방 ──────────────────────────────────────


def _keyword_write_result(before=None, after=None):
    return naver_sa_writer.WriteResult(
        action="update_keyword_bid",
        before=before if before is not None else {"bidAmt": 190, "userLock": False},
        response=after, after=after if after is not None else {"bidAmt": 210, "userLock": False},
        created_ids=[],
    )


def _complete_kw_up_ctx():
    """GATE P2-B(D-NAO-66 IU): keyword 증액도 S3 완전성 게이트 대상이 되어, keyword bid_up
    테스트의 mock 컨텍스트도 완전(BEP 원료·일예산)해야 실행 경로에 진입한다 — 가드를 약화하지
    않고 테스트 시드를 완전하게(adgroup S3 테스트의 complete_ctx와 동형)."""
    return {
        "current_bid": 190, "roas_corrected": 5.0, "target_roas": 2.0,
        "unconverted_spend": 0, "cost_today": 1000, "daily_budget": 50_000,
        "last_change_at": None, "changes_today_count": 0,
    }


def test_execute_update_bid_success(db):
    p = _proposal(db, proposal_type="bid_up")
    p.target_bid = 210
    db.commit()
    _settings(db, optimizer="ours")
    result = _keyword_write_result(before={"bidAmt": 190, "userLock": False}, after={"bidAmt": 210, "userLock": False})

    with patch.object(harness, "_build_guardrail_context", return_value=_complete_kw_up_ctx()), \
         patch.object(harness.guardrail_gate, "check", return_value=None) as mock_gate, \
         patch.object(harness.naver_sa_writer, "update_keyword_bid", return_value=result) as mock_write:
        log_entry = harness.execute(db, p.id, dry_run=False)

    mock_gate.assert_called_once()
    gate_call_args = mock_gate.call_args
    assert gate_call_args[0][0] == {"proposal_type": "bid_up", "target_bid": 210, "target_lock": None}
    mock_write.assert_called_once_with("nkw-1", 210)
    assert log_entry.outcome is None  # 채점 전 정상(X1b T5 배선확인) — 성공 판별은 after_value
    assert log_entry.action == "update_bid"
    assert json.loads(log_entry.before_value) == {"bidAmt": 190, "userLock": False}
    assert json.loads(log_entry.after_value) == {"bidAmt": 210, "userLock": False}

    db.refresh(p)
    assert p.executed_change_log_id == log_entry.id
    assert p.status == "approved"


def test_execute_update_bid_blocked_by_guardrail(db):
    p = _proposal(db, proposal_type="bid_up")
    p.target_bid = 300
    db.commit()
    _settings(db, optimizer="ours")

    with patch.object(harness, "_build_guardrail_context", return_value=_complete_kw_up_ctx()), \
         patch.object(harness.guardrail_gate, "check", return_value="변경폭 초과") as mock_gate, \
         patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_gate.assert_called_once()
    mock_write.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert logs[0].outcome == "failed"
    assert "가드레일 차단" in logs[0].rationale
    assert "변경폭 초과" in logs[0].rationale


def test_execute_update_bid_missing_target_bid_raises_before_gate(db):
    p = _proposal(db, proposal_type="bid_up")  # target_bid 미설정(None)
    _settings(db, optimizer="ours")

    with patch.object(harness, "_build_guardrail_context") as mock_ctx, \
         patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_ctx.assert_not_called()
    mock_write.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_execute_update_bid_wrong_target_type_raises(db):
    """target_type='campaign'은 여전히 미구현(정직 경계) — keyword·adgroup만 구현됨
    (adgroup은 X1b-S bid 확장, D-NAO-16 3단계 SHOPPING 대칭 확장으로 이제 유효 대상이라
    구 test는 target_type='campaign'으로 갱신)."""
    p = _proposal(db, proposal_type="bid_up", target_type="campaign", target_id="cmp1")
    p.target_bid = 210
    db.commit()
    _settings(db, optimizer="ours")

    with patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_kw, \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid") as mock_ag:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_kw.assert_not_called()
    mock_ag.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_execute_update_bid_writer_failure_records_failed(db):
    p = _proposal(db, proposal_type="bid_down")
    p.target_bid = 170
    db.commit()
    _settings(db, optimizer="ours")

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid",
                       side_effect=naver_sa_writer.WriteError("500")):
        with pytest.raises(naver_sa_writer.WriteError):
            harness.execute(db, p.id, dry_run=False)

    db.refresh(p)
    assert p.status == "failed"
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert logs[0].outcome == "failed"
    assert "[실행 실패]" in logs[0].rationale


# ── X1b-S bid 확장: update_bid target_type='adgroup'(SHOPPING) 대칭 확장 ──────
# D-NAO-16 3단계의 쇼핑 광고그룹 대칭 — shopping_group_bep 보드 제안(target_type='adgroup')이
# 이 경로로 실쓰기된다. bid_down이 주 사용처(shopping_group_bep는 적자그룹 감액이 핵심)지만
# writer 자체는 방향 무관(bid_up도 동일 함수) — update_keyword_bid와 동형 대칭 구조.


def _adgroup_write_result(before=None, after=None):
    return naver_sa_writer.WriteResult(
        action="update_adgroup_bid",
        before=before if before is not None else {"bidAmt": 1200, "systemBiddingType": "NONE"},
        response=after, after=after if after is not None else {"bidAmt": 1000, "systemBiddingType": "NONE"},
        created_ids=[],
    )


def test_execute_update_bid_adgroup_bid_down_success(db):
    """shopping_group_bep 대칭 확장 핵심 케이스 — target_type='adgroup' + bid_down이
    naver_sa_writer.update_adgroup_bid(update_keyword_bid 아님)로 디스패치돼야 한다."""
    p = _proposal(db, proposal_type="bid_down", target_type="adgroup", target_id="grp-shop-1")
    p.target_bid = 1000
    db.commit()
    _settings(db, optimizer="ours")
    result = _adgroup_write_result(
        before={"bidAmt": 1200, "systemBiddingType": "NONE"},
        after={"bidAmt": 1000, "systemBiddingType": "NONE"},
    )

    with patch.object(harness, "_build_guardrail_context", return_value={"current_bid": 1200}), \
         patch.object(harness.guardrail_gate, "check", return_value=None) as mock_gate, \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid", return_value=result) as mock_write, \
         patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_kw:
        log_entry = harness.execute(db, p.id, dry_run=False)

    mock_gate.assert_called_once()
    gate_call_args = mock_gate.call_args
    assert gate_call_args[0][0] == {"proposal_type": "bid_down", "target_bid": 1000, "target_lock": None}
    mock_write.assert_called_once_with("grp-shop-1", 1000)
    mock_kw.assert_not_called()
    assert log_entry.action == "update_bid"
    assert log_entry.entity_type == "adgroup"
    assert log_entry.entity_id == "grp-shop-1"
    assert log_entry.outcome is None  # 채점 전 정상 — 성공 판별은 after_value
    assert json.loads(log_entry.before_value) == {"bidAmt": 1200, "systemBiddingType": "NONE"}
    assert json.loads(log_entry.after_value) == {"bidAmt": 1000, "systemBiddingType": "NONE"}

    db.refresh(p)
    assert p.executed_change_log_id == log_entry.id
    assert p.status == "approved"


OTHER_UP_TYPES = ("bid_up", "growth_bid_up")


@pytest.mark.parametrize("ptype", OTHER_UP_TYPES)
def test_execute_update_bid_keyword_up_blocked_when_context_incomplete(db, ptype):
    """[GATE P2-B, D-NAO-66 IU] keyword 증액도 S3 완전성 게이트 대상 — BEP 원료(roas_corrected/
    target_roas)·일예산이 불완전하면 guardrail_gate 호출 전 fail-closed 차단(writer 미호출·
    failed). 종전엔 adgroup/ad만이었고 키워드는 핫셋 클릭 게이트와의 암묵적 커플링에 기댔다 —
    IU가 장중-단독 UP을 연 만큼 키워드도 명시적으로 보장한다."""
    p = _proposal(db, proposal_type=ptype)  # target_type='keyword'(기본), target_id='nkw-1'
    p.target_bid = 210
    db.commit()
    _settings(db, optimizer="ours")

    with patch.object(harness, "_build_guardrail_context",
                       return_value={"current_bid": 190, "roas_corrected": 3.0, "target_roas": None}), \
         patch.object(harness.guardrail_gate, "check") as mock_gate, \
         patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_kw:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_gate.assert_not_called()
    mock_kw.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert "keyword 증액 가드 컨텍스트 불완전" in logs[0].rationale


def test_execute_update_bid_keyword_down_exempt_from_completeness_gate(db):
    """[GATE P2-B 대칭] keyword bid_down(안전 방향)은 완전성 게이트 면제 — up 전용 검사라
    컨텍스트가 비어도 guardrail_gate로 정상 진행(기존 동작 보존, 과도차단 방지)."""
    p = _proposal(db, proposal_type="bid_down")
    p.target_bid = 170
    db.commit()
    _settings(db, optimizer="ours")
    result = _keyword_write_result(before={"bidAmt": 190, "userLock": False},
                                    after={"bidAmt": 170, "userLock": False})

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None) as mock_gate, \
         patch.object(harness.naver_sa_writer, "update_keyword_bid", return_value=result) as mock_kw:
        log_entry = harness.execute(db, p.id, dry_run=False)

    mock_gate.assert_called_once()
    mock_kw.assert_called_once_with("nkw-1", 170)
    assert log_entry.action == "update_bid"


@pytest.mark.parametrize("ptype", OTHER_UP_TYPES)
def test_execute_update_bid_adgroup_up_blocked_when_context_missing_roas(db, ptype):
    """[S3, D-NAO-43 확장] adgroup 증액은 컨텍스트에 roas_corrected/target_roas가 둘 다
    채워져야만 guardrail_gate로 넘어간다 — guardrail_gate._check_bid의 BEP 검사는 그 값이
    None이면 fail-open(검사 건너뜀)이므로, executor가 직접 fail-closed로 막는다(S2의
    blanket 차단을 대체). 컨텍스트가 미비하면(예: roas_corrected만 있고 target_roas 없음)
    guardrail_gate.check 자체를 호출하지 않고 즉시 차단·writer 미호출·failed."""
    p = _proposal(db, proposal_type=ptype, target_type="adgroup", target_id="grp-shop-up")
    p.target_bid = 2000
    db.commit()
    _settings(db, optimizer="ours")

    with patch.object(harness, "_build_guardrail_context",
                       return_value={"current_bid": 1000, "roas_corrected": 3.0, "target_roas": None}), \
         patch.object(harness.guardrail_gate, "check") as mock_gate, \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid") as mock_ag, \
         patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_kw:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_gate.assert_not_called()
    mock_ag.assert_not_called()
    mock_kw.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert "fail-closed" in logs[0].rationale


def test_execute_update_bid_adgroup_bid_up_blocked_when_roas_corrected_missing(db):
    """roas_corrected 쪽이 없는 경우도 동일하게 fail-closed(target_roas만 있는 경우)."""
    p = _proposal(db, proposal_type="bid_up", target_type="adgroup", target_id="grp-shop-up")
    p.target_bid = 2000
    db.commit()
    _settings(db, optimizer="ours")

    with patch.object(harness, "_build_guardrail_context",
                       return_value={"current_bid": 1000, "roas_corrected": None, "target_roas": 2.0}), \
         patch.object(harness.guardrail_gate, "check") as mock_gate, \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid") as mock_ag:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_gate.assert_not_called()
    mock_ag.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_execute_update_bid_adgroup_bid_up_success_when_context_complete(db):
    """[S3] 컨텍스트가 완전(roas_corrected·target_roas 둘 다 존재)하면 guardrail_gate로
    넘어가고, 통과 시 update_adgroup_bid로 정상 디스패치된다."""
    p = _proposal(db, proposal_type="bid_up", target_type="adgroup", target_id="grp-shop-up")
    p.target_bid = 1300
    db.commit()
    _settings(db, optimizer="ours")
    result = _adgroup_write_result(
        before={"bidAmt": 1200, "systemBiddingType": "NONE"},
        after={"bidAmt": 1300, "systemBiddingType": "NONE"},
    )
    complete_ctx = {
        "current_bid": 1200, "roas_corrected": 5.0, "target_roas": 2.0,
        "unconverted_spend": 0, "cost_today": 1000, "daily_budget": 50_000,
        "last_change_at": None, "changes_today_count": 0,
    }

    with patch.object(harness, "_build_guardrail_context", return_value=complete_ctx), \
         patch.object(harness.guardrail_gate, "check", return_value=None) as mock_gate, \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid", return_value=result) as mock_write, \
         patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_kw:
        log_entry = harness.execute(db, p.id, dry_run=False)

    mock_gate.assert_called_once()
    mock_write.assert_called_once_with("grp-shop-up", 1300)
    mock_kw.assert_not_called()
    assert log_entry.entity_type == "adgroup"
    assert log_entry.action == "update_bid"

    db.refresh(p)
    assert p.status == "approved"
    assert p.executed_change_log_id == log_entry.id


def test_execute_update_bid_adgroup_bid_up_blocked_when_daily_budget_missing(db):
    """[codex P1 S3] roas/target은 있어도 daily_budget이 None(스냅샷/재조회 실패)이면
    guardrail의 일예산 상한 검사가 fail-open이므로 executor가 fail-closed로 막는다."""
    p = _proposal(db, proposal_type="bid_up", target_type="adgroup", target_id="grp-shop-up")
    p.target_bid = 1300
    db.commit()
    _settings(db, optimizer="ours")
    ctx = {"current_bid": 1200, "roas_corrected": 5.0, "target_roas": 2.0,
           "unconverted_spend": 0, "cost_today": None, "daily_budget": None,
           "last_change_at": None, "changes_today_count": 0}

    with patch.object(harness, "_build_guardrail_context", return_value=ctx), \
         patch.object(harness.guardrail_gate, "check") as mock_gate, \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid") as mock_ag:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_gate.assert_not_called()
    mock_ag.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_execute_update_bid_adgroup_bid_up_blocked_when_capped_but_cost_today_missing(db):
    """[codex P1 S3] daily_budget>0(상한 있음)인데 cost_today가 None(오늘 소진 미확보)이면
    상한 검증 불가 → fail-closed 차단."""
    p = _proposal(db, proposal_type="bid_up", target_type="adgroup", target_id="grp-shop-up")
    p.target_bid = 1300
    db.commit()
    _settings(db, optimizer="ours")
    ctx = {"current_bid": 1200, "roas_corrected": 5.0, "target_roas": 2.0,
           "unconverted_spend": 0, "cost_today": None, "daily_budget": 50_000,
           "last_change_at": None, "changes_today_count": 0}

    with patch.object(harness, "_build_guardrail_context", return_value=ctx), \
         patch.object(harness.guardrail_gate, "check") as mock_gate, \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid") as mock_ag:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_gate.assert_not_called()
    mock_ag.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_execute_update_bid_adgroup_bid_up_allowed_when_uncapped_daily_budget_zero(db):
    """[codex P1 S3] daily_budget==0 = uncapped(무제한) → 상한 검사 자체가 없으므로
    cost_today가 None이어도 컨텍스트 완전으로 보고 guardrail_gate로 넘어간다(과도차단 방지)."""
    p = _proposal(db, proposal_type="bid_up", target_type="adgroup", target_id="grp-shop-up")
    p.target_bid = 1300
    db.commit()
    _settings(db, optimizer="ours")
    result = _adgroup_write_result(
        before={"bidAmt": 1200, "systemBiddingType": "NONE"},
        after={"bidAmt": 1300, "systemBiddingType": "NONE"},
    )
    ctx = {"current_bid": 1200, "roas_corrected": 5.0, "target_roas": 2.0,
           "unconverted_spend": 0, "cost_today": None, "daily_budget": 0,
           "last_change_at": None, "changes_today_count": 0}

    with patch.object(harness, "_build_guardrail_context", return_value=ctx), \
         patch.object(harness.guardrail_gate, "check", return_value=None) as mock_gate, \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid", return_value=result) as mock_write:
        log_entry = harness.execute(db, p.id, dry_run=False)

    mock_gate.assert_called_once()
    mock_write.assert_called_once_with("grp-shop-up", 1300)
    db.refresh(p)
    assert p.status == "approved"


def test_execute_update_bid_adgroup_bid_up_blocked_by_real_guardrail_bep(db):
    """[완료기준⑤] 컨텍스트는 완전하지만 보정ROAS가 목표 미달인 경우 — 실제
    guardrail_gate.check(mock 아님)가 BEP 미달로 차단해야 한다(D-NAO-1 안전선)."""
    p = _proposal(db, proposal_type="bid_up", target_type="adgroup", target_id="grp-shop-up")
    p.target_bid = 1300
    db.commit()
    _settings(db, optimizer="ours")
    incomplete_but_present_ctx = {
        "current_bid": 1200, "roas_corrected": 1.0, "target_roas": 2.0,  # BEP 미달
        "unconverted_spend": 0, "cost_today": 1000, "daily_budget": 50_000,
        "last_change_at": None, "changes_today_count": 0,
    }

    with patch.object(harness, "_build_guardrail_context", return_value=incomplete_but_present_ctx), \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_write.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert "BEP 미달" in logs[0].rationale


def test_real_write_blocker_adgroup_bid_up_executable_when_valid(db):
    """[S3] real_write_blocker는 구조 판정만 한다(target_type/target_bid) — 런타임 컨텍스트
    검증(roas_corrected/target_roas fail-closed)은 executor가 실행 시도 시점에 담당하므로
    콘솔 executable 표시에서는 더 이상 adgroup 증액을 블랑켓 차단하지 않는다."""
    for ptype in OTHER_UP_TYPES:
        p = _proposal(db, proposal_type=ptype, target_type="adgroup", target_id="grp-shop-up")
        p.target_bid = 2000
        db.commit()
        assert harness.real_write_blocker(p) is None


def test_execute_update_bid_adgroup_blocked_by_guardrail(db):
    p = _proposal(db, proposal_type="bid_down", target_type="adgroup", target_id="grp-shop-1")
    p.target_bid = 100  # -15% 초과라고 가정(가드가 차단하는 시나리오를 mock으로 표현)
    db.commit()
    _settings(db, optimizer="ours")

    with patch.object(harness, "_build_guardrail_context", return_value={"current_bid": 1200}), \
         patch.object(harness.guardrail_gate, "check", return_value="변경폭 초과") as mock_gate, \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_gate.assert_called_once()
    mock_write.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert logs[0].outcome == "failed"
    assert "가드레일 차단" in logs[0].rationale
    assert "변경폭 초과" in logs[0].rationale


def test_execute_update_bid_adgroup_writer_failure_records_failed(db):
    p = _proposal(db, proposal_type="bid_down", target_type="adgroup", target_id="grp-shop-1")
    p.target_bid = 1000
    db.commit()
    _settings(db, optimizer="ours")

    with patch.object(harness, "_build_guardrail_context", return_value={"current_bid": 1200}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid",
                       side_effect=naver_sa_writer.WriteError("500")):
        with pytest.raises(naver_sa_writer.WriteError):
            harness.execute(db, p.id, dry_run=False)

    db.refresh(p)
    assert p.status == "failed"
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert logs[0].outcome == "failed"
    assert "[실행 실패]" in logs[0].rationale


# ── X1b T4: set_user_lock 실쓰기 개방(pause/resume) ─────────────────────


def _lock_write_result(before_lock=False, after_lock=True):
    return naver_sa_writer.WriteResult(
        action="set_keyword_lock",
        before={"bidAmt": 190, "userLock": before_lock},
        response=None, after={"bidAmt": 190, "userLock": after_lock}, created_ids=[],
    )


def test_execute_set_user_lock_pause_success(db):
    p = _proposal(db, proposal_type="pause")
    p.target_lock = True
    db.commit()
    _settings(db, optimizer="ours")
    result = _lock_write_result(before_lock=False, after_lock=True)

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None) as mock_gate, \
         patch.object(harness.naver_sa_writer, "set_keyword_lock", return_value=result) as mock_write:
        log_entry = harness.execute(db, p.id, dry_run=False)

    gate_call_args = mock_gate.call_args
    assert gate_call_args[0][0] == {"proposal_type": "pause", "target_bid": None, "target_lock": True}
    mock_write.assert_called_once_with("nkw-1", True)
    assert log_entry.action == "set_user_lock"
    assert log_entry.outcome is None  # 채점 전 정상(X1b T5 배선확인) — 성공 판별은 after_value
    assert json.loads(log_entry.after_value)["userLock"] is True

    db.refresh(p)
    assert p.status == "approved"
    assert p.executed_change_log_id == log_entry.id


def test_execute_set_user_lock_resume_success(db):
    p = _proposal(db, proposal_type="resume")
    p.target_lock = False
    db.commit()
    _settings(db, optimizer="ours")
    result = _lock_write_result(before_lock=True, after_lock=False)

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "set_keyword_lock", return_value=result) as mock_write:
        log_entry = harness.execute(db, p.id, dry_run=False)

    mock_write.assert_called_once_with("nkw-1", False)
    assert json.loads(log_entry.after_value)["userLock"] is False


def test_execute_set_user_lock_blocked_by_guardrail(db):
    p = _proposal(db, proposal_type="pause")
    p.target_lock = True
    db.commit()
    _settings(db, optimizer="ours")

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value="쿨다운 중") as mock_gate, \
         patch.object(harness.naver_sa_writer, "set_keyword_lock") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_gate.assert_called_once()
    mock_write.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_execute_set_user_lock_missing_target_lock_raises(db):
    p = _proposal(db, proposal_type="pause")  # target_lock 미설정(None)
    _settings(db, optimizer="ours")

    with patch.object(harness.naver_sa_writer, "set_keyword_lock") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_write.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_execute_set_user_lock_wrong_target_type_raises(db):
    """X1b-S S1(D-NAO-43)로 adgroup은 이제 유효 대상이라(set_adgroup_lock) — 여전히
    미구현인 campaign으로 '잘못된 target_type' 케이스를 검증한다."""
    p = _proposal(db, proposal_type="pause", target_type="campaign", target_id="cmp-x")
    p.target_lock = True
    db.commit()
    _settings(db, optimizer="ours")

    with patch.object(harness.naver_sa_writer, "set_keyword_lock") as mock_kw, \
         patch.object(harness.naver_sa_writer, "set_adgroup_lock") as mock_ag:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_kw.assert_not_called()
    mock_ag.assert_not_called()


def test_execute_set_user_lock_writer_failure_records_failed(db):
    p = _proposal(db, proposal_type="resume")
    p.target_lock = False
    db.commit()
    _settings(db, optimizer="ours")

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "set_keyword_lock",
                       side_effect=naver_sa_writer.WriteError("500")):
        with pytest.raises(naver_sa_writer.WriteError):
            harness.execute(db, p.id, dry_run=False)

    db.refresh(p)
    assert p.status == "failed"


# ── X1b-S S1: set_user_lock adgroup 확장(쇼핑 스톱로스 정지·재개, D-NAO-43) ─────


def _adgroup_lock_write_result(before_lock=False, after_lock=True):
    return naver_sa_writer.WriteResult(
        action="set_adgroup_lock",
        before={"userLock": before_lock}, response=None,
        after={"userLock": after_lock}, created_ids=[],
    )


def test_execute_set_user_lock_pause_adgroup_success(db):
    p = _proposal(db, proposal_type="pause", target_type="adgroup", target_id="grp-shop-1")
    p.target_lock = True
    db.commit()
    _settings(db, optimizer="ours")
    result = _adgroup_lock_write_result(before_lock=False, after_lock=True)

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None) as mock_gate, \
         patch.object(harness.naver_sa_writer, "set_adgroup_lock", return_value=result) as mock_write, \
         patch.object(harness.naver_sa_writer, "set_keyword_lock") as mock_kw_write:
        log_entry = harness.execute(db, p.id, dry_run=False)

    gate_call_args = mock_gate.call_args
    assert gate_call_args[0][0] == {"proposal_type": "pause", "target_bid": None, "target_lock": True}
    mock_write.assert_called_once_with("grp-shop-1", True)
    mock_kw_write.assert_not_called()
    assert log_entry.action == "set_user_lock"
    assert log_entry.outcome is None
    assert json.loads(log_entry.after_value)["userLock"] is True

    db.refresh(p)
    assert p.status == "approved"
    assert p.executed_change_log_id == log_entry.id


def test_execute_set_user_lock_resume_adgroup_success(db):
    p = _proposal(db, proposal_type="resume", target_type="adgroup", target_id="grp-shop-1")
    p.target_lock = False
    db.commit()
    _settings(db, optimizer="ours")
    result = _adgroup_lock_write_result(before_lock=True, after_lock=False)

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "set_adgroup_lock", return_value=result) as mock_write:
        log_entry = harness.execute(db, p.id, dry_run=False)

    mock_write.assert_called_once_with("grp-shop-1", False)
    assert json.loads(log_entry.after_value)["userLock"] is False


def test_execute_set_user_lock_adgroup_blocked_by_guardrail(db):
    p = _proposal(db, proposal_type="pause", target_type="adgroup", target_id="grp-shop-1")
    p.target_lock = True
    db.commit()
    _settings(db, optimizer="ours")

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value="쿨다운 중") as mock_gate, \
         patch.object(harness.naver_sa_writer, "set_adgroup_lock") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_gate.assert_called_once()
    mock_write.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_execute_set_user_lock_adgroup_writer_failure_records_failed(db):
    p = _proposal(db, proposal_type="pause", target_type="adgroup", target_id="grp-shop-1")
    p.target_lock = True
    db.commit()
    _settings(db, optimizer="ours")

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "set_adgroup_lock",
                       side_effect=naver_sa_writer.WriteError("500")):
        with pytest.raises(naver_sa_writer.WriteError):
            harness.execute(db, p.id, dry_run=False)

    db.refresh(p)
    assert p.status == "failed"
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert logs[0].outcome == "failed"


def test_real_write_blocker_set_user_lock_adgroup_executable_when_valid(db):
    p = _proposal(db, proposal_type="pause", target_type="adgroup", target_id="grp-shop-1")
    p.target_lock = True
    db.commit()
    assert harness.real_write_blocker(p) is None


def test_real_write_blocker_set_user_lock_adgroup_missing_target_lock(db):
    p = _proposal(db, proposal_type="resume", target_type="adgroup", target_id="grp-shop-1")
    reason = harness.real_write_blocker(p)
    assert reason is not None and "target_lock" in reason


def test_real_write_blocker_set_user_lock_campaign_still_not_implemented(db):
    """캠페인 단위 userLock은 이 스프린트(X1b-S S1) 스코프 밖 — keyword/adgroup만 개방."""
    p = _proposal(db, proposal_type="pause", target_type="campaign", target_id="cmp1")
    p.target_lock = True
    db.commit()
    reason = harness.real_write_blocker(p)
    assert reason is not None and "campaign" in reason


# ── X1b T4: dry-run 회귀(신규 개방 액션도 dry_run=True 기본) ─────────────


def test_bid_up_dry_run_by_default_no_writer_call(db):
    p = _proposal(db, proposal_type="bid_up")
    p.target_bid = 210
    db.commit()
    _settings(db, optimizer="ours")

    with patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        log_entry = harness.execute(db, p.id)

    mock_write.assert_not_called()
    assert log_entry.dry_run is True


def test_pause_dry_run_by_default_no_writer_call(db):
    p = _proposal(db, proposal_type="pause")
    p.target_lock = True
    db.commit()
    _settings(db, optimizer="ours")

    with patch.object(harness.naver_sa_writer, "set_keyword_lock") as mock_write:
        log_entry = harness.execute(db, p.id)

    mock_write.assert_not_called()
    assert log_entry.dry_run is True


# ── X1b T4: MOP 충돌 감지(D-NAO-13) ──────────────────────────────────────


def test_execute_update_bid_detects_external_change_appends_warning(db):
    """우리 마지막 기록(after_value.bidAmt=210)과 방금 재조회한 라이브 before(bidAmt=250)가
    다르면 — 그 사이 외부(MOP 등)가 바꿨다는 신호. 실행 자체는 진행(경고만, 차단 아님)."""
    p = _proposal(db, proposal_type="bid_up")
    p.target_bid = 300
    db.commit()
    _settings(db, optimizer="ours")
    # 우리의 이전 성공 기록 — bidAmt=210으로 남아있음
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1", action="update_bid",
        dry_run=False, after_value=json.dumps({"bidAmt": 210, "userLock": False}),
        executed_at=kst_now(),
    ))
    db.commit()
    # 방금 재조회한 라이브 before는 250 — 우리가 마지막으로 기록한 210과 다름(외부 변경)
    result = _keyword_write_result(before={"bidAmt": 250, "userLock": False}, after={"bidAmt": 300, "userLock": False})

    with patch.object(harness, "_build_guardrail_context", return_value=_complete_kw_up_ctx()), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid", return_value=result):
        log_entry = harness.execute(db, p.id, dry_run=False)

    assert "외부 변경 감지" in log_entry.rationale
    assert "210" in log_entry.rationale and "250" in log_entry.rationale
    assert log_entry.outcome is None  # 채점 전 정상(D+14 배선) — 차단 아님, 경고만 부착하고 정상 진행


def test_execute_update_bid_no_warning_when_before_matches_our_last_record(db):
    p = _proposal(db, proposal_type="bid_up")
    p.target_bid = 300
    db.commit()
    _settings(db, optimizer="ours")
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1", action="update_bid",
        dry_run=False, after_value=json.dumps({"bidAmt": 210, "userLock": False}),
        executed_at=kst_now(),
    ))
    db.commit()
    # 재조회 before가 우리 마지막 기록(210)과 일치 — 외부 변경 없음
    result = _keyword_write_result(before={"bidAmt": 210, "userLock": False}, after={"bidAmt": 300, "userLock": False})

    with patch.object(harness, "_build_guardrail_context", return_value=_complete_kw_up_ctx()), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid", return_value=result):
        log_entry = harness.execute(db, p.id, dry_run=False)

    assert "외부 변경 감지" not in (log_entry.rationale or "")


def test_execute_update_bid_no_warning_when_no_prior_record(db):
    p = _proposal(db, proposal_type="bid_up")
    p.target_bid = 210
    db.commit()
    _settings(db, optimizer="ours")
    result = _keyword_write_result()

    with patch.object(harness, "_build_guardrail_context", return_value=_complete_kw_up_ctx()), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid", return_value=result):
        log_entry = harness.execute(db, p.id, dry_run=False)

    assert "외부 변경 감지" not in (log_entry.rationale or "")


# ── X1b T4: real_write_blocker 신규 구조 판정 ────────────────────────────


def test_real_write_blocker_update_bid_executable_when_valid(db):
    p = _proposal(db, proposal_type="bid_up")
    p.target_bid = 210
    db.commit()
    assert harness.real_write_blocker(p) is None


def test_real_write_blocker_update_bid_missing_target_bid(db):
    p = _proposal(db, proposal_type="bid_up")
    reason = harness.real_write_blocker(p)
    assert reason is not None and "target_bid" in reason


def test_real_write_blocker_update_bid_adgroup_executable_when_valid(db):
    """X1b-S bid 확장(D-NAO-16 3단계 SHOPPING 대칭) — target_type='adgroup' + target_bid
    존재면 이제 실행 가능(구 test_real_write_blocker_update_bid_wrong_target_type을
    이 계약에 따라 target_type='campaign'으로 갱신, 아래)."""
    p = _proposal(db, proposal_type="bid_down", target_type="adgroup", target_id="grp-shop-1")
    p.target_bid = 1000
    db.commit()
    assert harness.real_write_blocker(p) is None


def test_real_write_blocker_update_bid_wrong_target_type(db):
    p = _proposal(db, proposal_type="bid_up", target_type="campaign", target_id="cmp1")
    p.target_bid = 210
    reason = harness.real_write_blocker(p)
    assert reason is not None and "campaign" in reason


def test_real_write_blocker_set_user_lock_executable_when_valid(db):
    p = _proposal(db, proposal_type="pause")
    p.target_lock = True
    db.commit()
    assert harness.real_write_blocker(p) is None


def test_real_write_blocker_set_user_lock_missing_target_lock(db):
    p = _proposal(db, proposal_type="resume")
    reason = harness.real_write_blocker(p)
    assert reason is not None and "target_lock" in reason


def test_real_write_blocker_budget_up_executable_when_valid(db):
    """P3(D-NAO-42-f): update_budget 개방 — 구조가 유효(target_type='campaign' +
    target_budget 존재)하면 실행 가능(구 test_real_write_blocker_budget_up_still_not_open을
    P3 계약에 따라 갱신)."""
    p = _proposal(db, proposal_type="budget_up", target_type="campaign", target_id="cmp1")
    p.target_budget = 12_000
    db.commit()
    assert harness.real_write_blocker(p) is None


def test_real_write_blocker_budget_up_missing_target_budget(db):
    p = _proposal(db, proposal_type="budget_up", target_type="campaign", target_id="cmp1")
    reason = harness.real_write_blocker(p)
    assert reason is not None and "target_budget" in reason


def test_real_write_blocker_budget_up_wrong_target_type(db):
    p = _proposal(db, proposal_type="budget_up")  # 기본 target_type="keyword"
    p.target_budget = 12_000
    db.commit()
    reason = harness.real_write_blocker(p)
    assert reason is not None and "keyword" in reason


def test_real_write_blocker_budget_up_target_id_campaign_id_mismatch(db):
    """Fix 2(codex P1): target_id != campaign_id인 캠페인 예산 제안은 stale/malformed —
    구조(target_type='campaign' + target_budget 존재)만으로는 통과시키면 안 된다."""
    p = _proposal(db, proposal_type="budget_up", campaign_id="cmp-budget",
                  target_type="campaign", target_id="cmp-other")
    p.target_budget = 12_000
    db.commit()
    reason = harness.real_write_blocker(p)
    assert reason is not None
    assert "target_id" in reason and "campaign_id" in reason


# ── X1b T4: _build_guardrail_context ─────────────────────────────────────


def test_build_guardrail_context_non_keyword_target_all_none(db):
    # target_type='search_term'은 keyword/campaign/adgroup 어느 것도 아니므로 전부 None 유지
    p = _proposal(db, proposal_type="negative_keyword", target_type="search_term", target_id="검색어")
    ctx = harness._build_guardrail_context(db, p, kst_now())
    assert ctx == {
        "current_bid": None, "current_budget": None, "roas_corrected": None, "target_roas": None,
        "cost_today": None, "daily_budget": None, "unconverted_spend": None,
        "last_change_at": None, "changes_today_count": 0, "campaign_type": None,
        # D-NAO-121: target_type='ad'가 아니므로 출시창 순위 하한은 항상 None(하한 없음).
        "launch_floor_bid": None, "launch_target_rank": None,
    }


def test_build_guardrail_context_adgroup_target_bid_budget_fields_none_no_prior_changes(db):
    """X1b-S S1(D-NAO-43)+bid 확장: target_type='adgroup'은 current_budget/roas 등은
    adgroup_window_agg 미구현이라 여전히 None(정직 경계, S2/S3 스코프) — current_bid는
    이제 _get_adgroup 라이브 재조회를 시도한다(bid 확장). 재조회 실패는 fail-closed로
    None 유지(get_keyword 실패 패턴과 동형) — 변경 이력이 없으면 쿨다운 필드도 기본값."""
    p = _proposal(db, proposal_type="pause", target_type="adgroup", target_id="grp-shop-1")
    with patch.object(harness.naver_sa_writer, "_get_adgroup", side_effect=RuntimeError("network")):
        ctx = harness._build_guardrail_context(db, p, kst_now())
    assert ctx == {
        "current_bid": None, "current_budget": None, "roas_corrected": None, "target_roas": None,
        "cost_today": None, "daily_budget": None, "unconverted_spend": None,
        "last_change_at": None, "changes_today_count": 0, "campaign_type": None,
        # D-NAO-121: target_type='adgroup'(그룹 단위)이므로 출시창 순위 하한은 소재 전용 —
        # 항상 None(하한 없음).
        "launch_floor_bid": None, "launch_target_rank": None,
    }


def test_build_guardrail_context_adgroup_cooldown_fields_populated_from_prior_lock_changes(db):
    """X1b-S S1(D-NAO-43): adgroup 정지·재개도 쿨다운·일일상한(_check_cooldown_and_cap)이
    실효돼야 한다 — 이전엔 target_type='adgroup'이 통째로 None 처리돼 쿨다운이 사실상
    무력화됐다(fail-open이면 안전장치 우회). last_change_at/changes_today_count만 채우고
    bid/budget 필드는 여전히 None(_get_adgroup 재조회 실패를 mock — fail-closed)."""
    p = _proposal(db, proposal_type="pause", target_type="adgroup", target_id="grp-shop-1")
    now = kst_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    db.add(NaverChangeLog(
        entity_type="adgroup", entity_id="grp-shop-1", campaign_id="cmp1", action="set_user_lock",
        dry_run=False, after_value=json.dumps({"userLock": False}), changed_at=today_start + timedelta(hours=2),
    ))
    db.commit()

    with patch.object(harness.naver_sa_writer, "_get_adgroup", side_effect=RuntimeError("network")):
        ctx = harness._build_guardrail_context(db, p, now)
    assert ctx["last_change_at"] == today_start + timedelta(hours=2)
    assert ctx["changes_today_count"] == 1
    assert ctx["current_bid"] is None
    assert ctx["current_budget"] is None


def test_build_guardrail_context_adgroup_current_bid_from_live_reread(db):
    """X1b-S bid 확장(D-NAO-16 3단계 SHOPPING 대칭): target_type='adgroup'의 current_bid는
    naver_sa_writer._get_adgroup 라이브 재조회에서 채워진다(get_keyword의 adgroup 대칭)."""
    p = _proposal(db, proposal_type="bid_down", target_type="adgroup", target_id="grp-shop-1")
    with patch.object(harness.naver_sa_writer, "_get_adgroup",
                       return_value={"bidAmt": 1200, "systemBiddingType": "NONE"}) as mock_get:
        ctx = harness._build_guardrail_context(db, p, kst_now())
    mock_get.assert_called_once_with("grp-shop-1")
    assert ctx["current_bid"] == 1200
    # bid/budget 외 나머지는 adgroup_window_agg 미구현이라 여전히 None(정직 경계)
    assert ctx["current_budget"] is None
    assert ctx["roas_corrected"] is None
    assert ctx["target_roas"] is None


def test_build_guardrail_context_populates_campaign_type_from_entity(db):
    """VT4 P1-1: campaign 엔티티 행의 campaign_type이 context에 실린다(SHOPPING이면 guardrail이
    50원 하한 적용). 엔티티 부재면 None(보수 70 하한)."""
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp1", campaign_id="cmp1",
                       campaign_type="SHOPPING", status="on"))
    db.commit()
    p = _proposal(db, proposal_type="bid_down", target_type="adgroup", target_id="grp-shop-1")
    with patch.object(harness.naver_sa_writer, "_get_adgroup",
                       return_value={"bidAmt": 60, "systemBiddingType": "NONE"}):
        ctx = harness._build_guardrail_context(db, p, kst_now())
    assert ctx["campaign_type"] == "SHOPPING"


def test_build_guardrail_context_campaign_type_none_when_entity_absent(db):
    """campaign 엔티티 행 부재 → campaign_type None(보수 70 하한 유지, fail-closed)."""
    p = _proposal(db, proposal_type="bid_down", target_type="adgroup", target_id="grp-shop-1")
    with patch.object(harness.naver_sa_writer, "_get_adgroup",
                       return_value={"bidAmt": 1200, "systemBiddingType": "NONE"}):
        ctx = harness._build_guardrail_context(db, p, kst_now())
    assert ctx["campaign_type"] is None


def test_build_guardrail_context_adgroup_get_adgroup_failure_fail_closed(db):
    p = _proposal(db, proposal_type="bid_down", target_type="adgroup", target_id="grp-shop-1")
    with patch.object(harness.naver_sa_writer, "_get_adgroup", side_effect=RuntimeError("network")):
        ctx = harness._build_guardrail_context(db, p, kst_now())
    assert ctx["current_bid"] is None  # 재조회 실패 — 추정으로 채우지 않음


# ── X1b-S S3(D-NAO-43 확장): _build_guardrail_context adgroup UP 브랜치 ──────
# adgroup 증액(bid_up/growth_bid_up)만 roas_corrected/target_roas/unconverted_spend/
# cost_today/daily_budget을 채운다 — bid_down/pause/resume은 여전히 None(위 테스트들이 이미
# 그 회귀를 커버).


def test_build_guardrail_context_adgroup_bid_up_fills_roas_and_target_from_window_agg(db):
    from app.models import NaverAdDaily, Order
    p = _proposal(db, proposal_type="bid_up", target_type="adgroup", target_id="grp-shop-1")
    now = kst_now()
    as_of = now.date() - timedelta(days=1)
    db.add(NaverAdDaily(
        ad_date=as_of, campaign_id="cmp1", campaign_type="SHOPPING", adgroup_id="grp-shop-1",
        keyword_id="", imp=500, clk=25, cost=3000, rank_sum=1500,
        conv_direct_cnt=1, conv_indirect_cnt=0, conv_direct_amt=9000, conv_indirect_amt=0,
    ))
    # D-NAO-21 보정계수 = 실주문매출÷네이버convAmt — 9000/9000=1.0이 되도록 실주문도 시딩
    # (채널 실측: naver_order_revenue는 channel_id=6 고정, test_build_guardrail_context_
    # converted_window_zero_unconverted_spend와 동일 패턴).
    db.add(Order(
        channel_id=6, platform_product_id="cp-1", order_number="ORD-1",
        order_date=as_of, status="정상", selling_price=Decimal("9000"),
    ))
    db.commit()

    with patch.object(harness.naver_sa_writer, "_get_adgroup",
                       return_value={"bidAmt": 1200, "systemBiddingType": "NONE"}):
        ctx = harness._build_guardrail_context(db, p, now)

    assert ctx["current_bid"] == 1200
    assert ctx["roas_corrected"] == pytest.approx(3.0)  # 9000/3000 × 보정계수 1.0(9000/9000)
    assert ctx["unconverted_spend"] == 0  # 전환 존재 — 무전환 누적으로 안 잡힘


def test_build_guardrail_context_adgroup_bid_up_unconverted_spend_when_zero_conversion(db):
    from app.models import NaverAdDaily
    p = _proposal(db, proposal_type="bid_up", target_type="adgroup", target_id="grp-shop-1")
    now = kst_now()
    as_of = now.date() - timedelta(days=1)
    db.add(NaverAdDaily(
        ad_date=as_of, campaign_id="cmp1", campaign_type="SHOPPING", adgroup_id="grp-shop-1",
        keyword_id="", imp=500, clk=25, cost=3000, rank_sum=1500,
        conv_direct_cnt=0, conv_indirect_cnt=0, conv_direct_amt=0, conv_indirect_amt=0,
    ))
    db.commit()

    with patch.object(harness.naver_sa_writer, "_get_adgroup",
                       return_value={"bidAmt": 1200, "systemBiddingType": "NONE"}):
        ctx = harness._build_guardrail_context(db, p, now)

    assert ctx["unconverted_spend"] == 3000
    assert ctx["roas_corrected"] == 0.0


def test_build_guardrail_context_adgroup_growth_bid_up_also_fills(db):
    """growth_bid_up도 bid_up과 동일하게 up 취급된다(_BID_UP_TYPES와 동일 집합)."""
    from app.models import NaverAdDaily, Order
    p = _proposal(db, proposal_type="growth_bid_up", target_type="adgroup", target_id="grp-shop-1")
    now = kst_now()
    as_of = now.date() - timedelta(days=1)
    db.add(NaverAdDaily(
        ad_date=as_of, campaign_id="cmp1", campaign_type="SHOPPING", adgroup_id="grp-shop-1",
        keyword_id="", imp=500, clk=25, cost=3000, rank_sum=1500,
        conv_direct_cnt=1, conv_indirect_cnt=0, conv_direct_amt=9000, conv_indirect_amt=0,
    ))
    db.add(Order(
        channel_id=6, platform_product_id="cp-1", order_number="ORD-1",
        order_date=as_of, status="정상", selling_price=Decimal("9000"),
    ))
    db.commit()

    with patch.object(harness.naver_sa_writer, "_get_adgroup",
                       return_value={"bidAmt": 1200, "systemBiddingType": "NONE"}):
        ctx = harness._build_guardrail_context(db, p, now)

    assert ctx["roas_corrected"] == pytest.approx(3.0)


def test_build_guardrail_context_adgroup_bid_up_no_cost_leaves_roas_none(db):
    """window agg에 cost=0(실적 자체가 없음)이면 roas_corrected/unconverted_spend는
    None(추정 금지) — target_roas/cost_today/daily_budget은 그와 무관하게 채워진다."""
    p = _proposal(db, proposal_type="bid_up", target_type="adgroup", target_id="grp-shop-1")
    with patch.object(harness.naver_sa_writer, "_get_adgroup",
                       return_value={"bidAmt": 1200, "systemBiddingType": "NONE"}):
        ctx = harness._build_guardrail_context(db, p, kst_now())
    assert ctx["roas_corrected"] is None
    assert ctx["unconverted_spend"] is None


def test_build_guardrail_context_adgroup_bid_up_target_roas_from_campaign_override(db):
    from app.models import NaverCampaignSettings
    p = _proposal(db, proposal_type="bid_up", campaign_id="cmp-override",
                  target_type="adgroup", target_id="grp-shop-1")
    db.add(NaverCampaignSettings(campaign_id="cmp-override", target_roas_override=Decimal("4.5")))
    db.commit()

    with patch.object(harness.naver_sa_writer, "_get_adgroup",
                       return_value={"bidAmt": 1200, "systemBiddingType": "NONE"}):
        ctx = harness._build_guardrail_context(db, p, kst_now())

    assert ctx["target_roas"] == pytest.approx(4.5)


def test_build_guardrail_context_adgroup_bid_up_cost_today_and_daily_budget_from_snapshot(db):
    from app.models import NaverHourlySnapshot
    p = _proposal(db, proposal_type="bid_up", target_type="adgroup", target_id="grp-shop-1")
    now = kst_now()
    db.add(NaverHourlySnapshot(
        snapshot_at=now, ad_date=now.date(), snapshot_hour=5, campaign_id="cmp1",
        campaign_type="SHOPPING", cost=60_000, clk=30, imp=800, daily_budget=100_000,
    ))
    db.commit()

    with patch.object(harness.naver_sa_writer, "_get_adgroup",
                       return_value={"bidAmt": 1200, "systemBiddingType": "NONE"}):
        ctx = harness._build_guardrail_context(db, p, now)

    assert ctx["cost_today"] == 60_000
    assert ctx["daily_budget"] == 100_000


def test_build_guardrail_context_adgroup_bid_down_still_none_for_roas_fields(db):
    """회귀 방지 — bid_down(감액)은 여전히 up 전용 필드를 채우지 않는다(S2 계약 불변)."""
    from app.models import NaverAdDaily
    p = _proposal(db, proposal_type="bid_down", target_type="adgroup", target_id="grp-shop-1")
    now = kst_now()
    as_of = now.date() - timedelta(days=1)
    db.add(NaverAdDaily(
        ad_date=as_of, campaign_id="cmp1", campaign_type="SHOPPING", adgroup_id="grp-shop-1",
        keyword_id="", imp=500, clk=25, cost=8000, rank_sum=1500,
        conv_direct_cnt=1, conv_indirect_cnt=0, conv_direct_amt=40000, conv_indirect_amt=0,
    ))
    db.commit()

    with patch.object(harness.naver_sa_writer, "_get_adgroup",
                       return_value={"bidAmt": 1200, "systemBiddingType": "NONE"}):
        ctx = harness._build_guardrail_context(db, p, now)

    assert ctx["roas_corrected"] is None
    assert ctx["target_roas"] is None
    assert ctx["unconverted_spend"] is None
    assert ctx["cost_today"] is None
    assert ctx["daily_budget"] is None


def test_build_guardrail_context_current_bid_from_live_reread(db):
    p = _proposal(db, proposal_type="bid_up")
    with patch.object(harness.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": 250, "userLock": False}):
        ctx = harness._build_guardrail_context(db, p, kst_now())
    assert ctx["current_bid"] == 250


def test_build_guardrail_context_get_keyword_failure_fail_closed(db):
    p = _proposal(db, proposal_type="bid_up")
    with patch.object(harness.naver_sa_writer, "get_keyword", side_effect=RuntimeError("network")):
        ctx = harness._build_guardrail_context(db, p, kst_now())
    assert ctx["current_bid"] is None  # 재조회 실패 — 추정으로 채우지 않음


def test_build_guardrail_context_changes_today_count_and_last_change_at(db):
    p = _proposal(db, proposal_type="bid_up")
    now = kst_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # dry_run=False + after_value = 실제 성공한 쓰기(X1b T5 배선확인 — outcome은 D+14
    # 채점 전 NULL이 정상이라 더 이상 "executed" 마킹으로 판별하지 않는다).
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1", action="update_bid",
        dry_run=False, after_value=json.dumps({"bidAmt": 200}), changed_at=today_start + timedelta(hours=1),
    ))
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1", action="update_bid",
        dry_run=False, after_value=json.dumps({"bidAmt": 210}), changed_at=today_start + timedelta(hours=5),
    ))
    db.add(NaverChangeLog(  # 어제 — 오늘 카운트 제외 대상
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1", action="update_bid",
        dry_run=False, after_value=json.dumps({"bidAmt": 190}), changed_at=today_start - timedelta(hours=1),
    ))
    db.commit()

    with patch.object(harness.naver_sa_writer, "get_keyword", return_value={"bidAmt": 200}):
        ctx = harness._build_guardrail_context(db, p, now)

    assert ctx["changes_today_count"] == 2
    assert ctx["last_change_at"] == today_start + timedelta(hours=5)


def test_build_guardrail_context_no_prior_changes(db):
    p = _proposal(db, proposal_type="bid_up")
    with patch.object(harness.naver_sa_writer, "get_keyword", return_value={"bidAmt": 200}):
        ctx = harness._build_guardrail_context(db, p, kst_now())
    assert ctx["changes_today_count"] == 0
    assert ctx["last_change_at"] is None


def test_build_guardrail_context_excludes_dry_run_and_failed_from_cooldown(db):
    """[codex P2] dry-run(outcome=None)·실패(outcome='failed') 행은 네이버 상태를 바꾸지
    않았다 — 쿨다운·일일상한에 포함시키면 아무 일도 안 일어났는데 다음 실행이 차단된다."""
    p = _proposal(db, proposal_type="bid_up")
    now = kst_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    db.add(NaverChangeLog(  # dry-run — outcome 미기록
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1", action="update_bid",
        dry_run=True, changed_at=today_start + timedelta(hours=1),
    ))
    db.add(NaverChangeLog(  # 사전 가드 실패
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1", action="update_bid",
        outcome="failed", changed_at=today_start + timedelta(hours=2),
    ))
    db.commit()

    with patch.object(harness.naver_sa_writer, "get_keyword", return_value={"bidAmt": 200}):
        ctx = harness._build_guardrail_context(db, p, now)

    assert ctx["changes_today_count"] == 0
    assert ctx["last_change_at"] is None


def test_build_guardrail_context_only_counts_executed_among_mixed_outcomes(db):
    p = _proposal(db, proposal_type="bid_up")
    now = kst_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    db.add(NaverChangeLog(  # dry-run — 제외
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1", action="update_bid",
        dry_run=True, changed_at=today_start + timedelta(hours=1),
    ))
    db.add(NaverChangeLog(  # 실제 실행 — 포함(dry_run=False + after_value)
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1", action="update_bid",
        dry_run=False, after_value=json.dumps({"bidAmt": 210}), changed_at=today_start + timedelta(hours=3),
    ))
    db.add(NaverChangeLog(  # 실패(after_value 없음) — 제외
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1", action="update_bid",
        dry_run=False, outcome="failed", changed_at=today_start + timedelta(hours=5),
    ))
    db.commit()

    with patch.object(harness.naver_sa_writer, "get_keyword", return_value={"bidAmt": 200}):
        ctx = harness._build_guardrail_context(db, p, now)

    assert ctx["changes_today_count"] == 1
    assert ctx["last_change_at"] == today_start + timedelta(hours=3)  # 실패(5시)보다 앞선 실제 실행만


def test_build_guardrail_context_cost_today_and_daily_budget_from_hourly_snapshot(db):
    from app.models import NaverHourlySnapshot
    p = _proposal(db, proposal_type="bid_up")
    now = kst_now()
    db.add(NaverHourlySnapshot(
        snapshot_at=now, ad_date=now.date(), snapshot_hour=5, campaign_id="cmp1",
        campaign_type="WEB_SITE", cost=100_000, clk=50, imp=1000, daily_budget=500_000,
    ))
    db.add(NaverHourlySnapshot(  # 더 이른 시각 — 최신 아님
        snapshot_at=now, ad_date=now.date(), snapshot_hour=3, campaign_id="cmp1",
        campaign_type="WEB_SITE", cost=40_000, clk=20, imp=400, daily_budget=500_000,
    ))
    db.commit()

    with patch.object(harness.naver_sa_writer, "get_keyword", return_value={"bidAmt": 200}):
        ctx = harness._build_guardrail_context(db, p, now)

    assert ctx["cost_today"] == 100_000  # 최신(hour=5) 스냅샷만
    assert ctx["daily_budget"] == 500_000


def test_build_guardrail_context_unconverted_spend_from_window_agg(db):
    from app.models import NaverAdDaily
    p = _proposal(db, proposal_type="bid_up")
    now = kst_now()
    as_of = now.date() - timedelta(days=1)
    db.add(NaverAdDaily(
        ad_date=as_of, campaign_id="cmp1", campaign_type="WEB_SITE", adgroup_id="grp-1",
        keyword_id="nkw-1", imp=500, clk=25, cost=3000, rank_sum=1500,
        conv_direct_cnt=0, conv_indirect_cnt=0, conv_direct_amt=0, conv_indirect_amt=0,
    ))
    db.commit()

    with patch.object(harness.naver_sa_writer, "get_keyword", return_value={"bidAmt": 200}):
        ctx = harness._build_guardrail_context(db, p, now)

    assert ctx["unconverted_spend"] == 3000
    assert ctx["roas_corrected"] == 0.0


def test_build_guardrail_context_converted_window_zero_unconverted_spend(db):
    from app.models import NaverAdDaily, Order
    p = _proposal(db, proposal_type="bid_up")
    now = kst_now()
    as_of = now.date() - timedelta(days=1)
    db.add(NaverAdDaily(
        ad_date=as_of, campaign_id="cmp1", campaign_type="WEB_SITE", adgroup_id="grp-1",
        keyword_id="nkw-1", imp=500, clk=25, cost=3000, rank_sum=1500,
        conv_direct_cnt=1, conv_indirect_cnt=0, conv_direct_amt=9000, conv_indirect_amt=0,
    ))
    # D-NAO-21 보정계수 = 실주문매출÷네이버convAmt — 여기선 9000/9000=1.0이 되도록 실주문도 시딩
    # (채널 실측: naver_order_revenue는 channel_id=6 고정, actual_revenue.py 참조).
    db.add(Order(
        channel_id=6, platform_product_id="cp-1", order_number="ORD-1",
        order_date=as_of, status="정상", selling_price=Decimal("9000"),
    ))
    db.commit()

    with patch.object(harness.naver_sa_writer, "get_keyword", return_value={"bidAmt": 200}):
        ctx = harness._build_guardrail_context(db, p, now)

    assert ctx["unconverted_spend"] == 0  # 창 내 전환 존재 — 무전환 누적으로 안 잡힘
    assert ctx["roas_corrected"] == pytest.approx(3.0)  # 9000/3000 × 보정계수 1.0(9000/9000)


# ── P2 (D-NAO-42-f): _build_guardrail_context campaign 브랜치 ───────────


def test_build_guardrail_context_campaign_current_budget_from_live_reread(db):
    p = _proposal(db, proposal_type="budget_up", campaign_id="cmp-budget",
                  target_type="campaign", target_id="cmp-budget")
    with patch.object(harness.naver_sa_writer, "get_campaign",
                       return_value={"dailyBudget": 100_000, "useDailyBudget": True}):
        ctx = harness._build_guardrail_context(db, p, kst_now())
    assert ctx["current_budget"] == 100_000
    assert ctx["current_bid"] is None  # keyword 전용 필드는 campaign 브랜치에서 안 채움


def test_build_guardrail_context_campaign_get_campaign_failure_fail_closed(db):
    p = _proposal(db, proposal_type="budget_up", campaign_id="cmp-budget",
                  target_type="campaign", target_id="cmp-budget")
    with patch.object(harness.naver_sa_writer, "get_campaign", side_effect=RuntimeError("network")):
        ctx = harness._build_guardrail_context(db, p, kst_now())
    assert ctx["current_budget"] is None  # 재조회 실패 — 추정으로 채우지 않음


def test_build_guardrail_context_campaign_uses_campaign_id_not_target_id(db):
    """Fix 2(codex P1): get_campaign 재조회는 campaign_id로 해야 한다(target_id 아님) —
    가드 판정 대상과 실쓰기 대상이 항상 같은 캠페인이어야 한다(집계 agg는 이미 campaign_id
    를 쓰고 있었음, current_budget만 target_id를 써서 불일치할 여지가 있었다).
    target_id를 일부러 campaign_id와 다른 값으로 둬 어느 값이 실제로 쓰였는지 드러낸다."""
    p = _proposal(db, proposal_type="budget_up", campaign_id="cmp-budget",
                  target_type="campaign", target_id="cmp-budget-STALE")
    with patch.object(harness.naver_sa_writer, "get_campaign",
                       return_value={"dailyBudget": 77_000}) as mock_get:
        ctx = harness._build_guardrail_context(db, p, kst_now())
    mock_get.assert_called_once_with("cmp-budget")
    assert ctx["current_budget"] == 77_000


def test_build_guardrail_context_campaign_roas_and_unconverted_spend_from_campaign_window_agg(db):
    from app.models import NaverAdDaily
    p = _proposal(db, proposal_type="budget_up", campaign_id="cmp-budget",
                  target_type="campaign", target_id="cmp-budget")
    now = kst_now()
    as_of = now.date() - timedelta(days=1)
    # SHOPPING 타입 — campaign_window_agg는 WEB_SITE 필터가 없으므로 캠페인 grain 그대로 집계
    db.add(NaverAdDaily(
        ad_date=as_of, campaign_id="cmp-budget", campaign_type="SHOPPING", adgroup_id="grp-1",
        keyword_id="", imp=500, clk=25, cost=3000, rank_sum=1500,
        conv_direct_cnt=0, conv_indirect_cnt=0, conv_direct_amt=0, conv_indirect_amt=0,
    ))
    db.commit()

    with patch.object(harness.naver_sa_writer, "get_campaign", return_value={"dailyBudget": 100_000}):
        ctx = harness._build_guardrail_context(db, p, now)

    assert ctx["unconverted_spend"] == 3000  # 창 내 전환 0건 — 무전환 누적으로 잡힘
    assert ctx["roas_corrected"] == 0.0


def test_build_guardrail_context_campaign_change_log_uses_entity_type_campaign(db):
    p = _proposal(db, proposal_type="budget_up", campaign_id="cmp-budget",
                  target_type="campaign", target_id="cmp-budget")
    now = kst_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    db.add(NaverChangeLog(
        entity_type="campaign", entity_id="cmp-budget", campaign_id="cmp-budget",
        action="update_budget", dry_run=False,
        after_value=json.dumps({"dailyBudget": 100_000}), changed_at=today_start + timedelta(hours=2),
    ))
    # 다른 대상(entity_type='keyword')의 오늘 변경은 이 캠페인 컨텍스트에 섞이면 안 됨
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp-budget",
        action="update_bid", dry_run=False,
        after_value=json.dumps({"bidAmt": 200}), changed_at=today_start + timedelta(hours=3),
    ))
    db.commit()

    with patch.object(harness.naver_sa_writer, "get_campaign", return_value={"dailyBudget": 100_000}):
        ctx = harness._build_guardrail_context(db, p, now)

    assert ctx["changes_today_count"] == 1
    assert ctx["last_change_at"] == today_start + timedelta(hours=2)


def test_build_guardrail_context_campaign_target_roas_from_resolver(db):
    from app.models import NaverCampaignSettings
    p = _proposal(db, proposal_type="budget_up", campaign_id="cmp-budget",
                  target_type="campaign", target_id="cmp-budget")
    db.add(NaverCampaignSettings(campaign_id="cmp-budget", target_roas_override=Decimal("180.0")))
    db.commit()

    with patch.object(harness.naver_sa_writer, "get_campaign", return_value={"dailyBudget": 100_000}):
        ctx = harness._build_guardrail_context(db, p, kst_now())

    assert ctx["target_roas"] == pytest.approx(180.0)


def test_build_guardrail_context_campaign_cost_today_and_daily_budget_from_hourly_snapshot(db):
    from app.models import NaverHourlySnapshot
    p = _proposal(db, proposal_type="budget_up", campaign_id="cmp-budget",
                  target_type="campaign", target_id="cmp-budget")
    now = kst_now()
    db.add(NaverHourlySnapshot(
        snapshot_at=now, ad_date=now.date(), snapshot_hour=5, campaign_id="cmp-budget",
        campaign_type="SHOPPING", cost=80_000, clk=40, imp=900, daily_budget=100_000,
    ))
    db.commit()

    with patch.object(harness.naver_sa_writer, "get_campaign", return_value={"dailyBudget": 100_000}):
        ctx = harness._build_guardrail_context(db, p, now)

    assert ctx["cost_today"] == 80_000
    assert ctx["daily_budget"] == 100_000


# ── X1b T4: _detect_external_change ──────────────────────────────────────


def test_detect_external_change_no_prior_log_returns_none(db):
    p = _proposal(db, proposal_type="bid_up")
    reason = harness._detect_external_change(db, p, {"bidAmt": 200}, "bidAmt")
    assert reason is None


def test_detect_external_change_matching_value_returns_none(db):
    p = _proposal(db, proposal_type="bid_up")
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1", action="update_bid",
        dry_run=False, after_value=json.dumps({"bidAmt": 200}), executed_at=kst_now(),
    ))
    db.commit()
    reason = harness._detect_external_change(db, p, {"bidAmt": 200}, "bidAmt")
    assert reason is None


def test_detect_external_change_mismatch_returns_warning(db):
    p = _proposal(db, proposal_type="bid_up")
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1", action="update_bid",
        dry_run=False, after_value=json.dumps({"bidAmt": 200}), executed_at=kst_now(),
    ))
    db.commit()
    reason = harness._detect_external_change(db, p, {"bidAmt": 350}, "bidAmt")
    assert reason is not None
    assert "200" in reason and "350" in reason


def test_detect_external_change_unparseable_after_value_returns_none(db):
    p = _proposal(db, proposal_type="bid_up")
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1", action="update_bid",
        dry_run=False, after_value="not json", executed_at=kst_now(),
    ))
    db.commit()
    reason = harness._detect_external_change(db, p, {"bidAmt": 350}, "bidAmt")
    assert reason is None


# ── X1b T4 codex 2라운드(Claude 적대 리뷰, codex 한도 소진 대체): changed_at KST 명시 ──
# NaverChangeLog.changed_at은 server_default=func.now() — SQLite에서 이는 UTC를 반환한다
# (KST 아님, 9시간 차이 실측). guardrail_gate의 쿨다운(now - last_change_at)과
# _build_guardrail_context의 오늘 자정 경계 비교가 전부 kst_now() 기준이라, changed_at을
# 명시하지 않으면 그 비교가 항상 어긋난다(쿨다운은 항상 "충분히 지남"으로 오판정 —
# fail-open, D-NAO-19 안전장치 무력화). executed_at은 이미 명시(now)돼 있었으나 changed_at은
# 누락돼 있었다 — 전 change_log 생성 지점에 changed_at=now를 추가한다.


def test_execute_add_negative_keyword_success_sets_changed_at_to_kst_now(db):
    p = _negative_proposal(db)
    _settings(db, optimizer="ours")
    now = kst_now()
    result = _write_result(before=[], after=[{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "무관검색어"}],
                           created_ids=["rkw-1"])

    with patch.object(harness.naver_sa_writer, "add_restricted_keywords", return_value=result):
        log_entry = harness._execute_add_negative_keyword(db, p, now)

    assert log_entry.changed_at == now


def test_execute_update_bid_success_sets_changed_at_to_kst_now(db):
    p = _proposal(db, proposal_type="bid_up")
    p.target_bid = 210
    db.commit()
    _settings(db, optimizer="ours")
    now = kst_now()
    result = _keyword_write_result()

    with patch.object(harness, "_build_guardrail_context", return_value=_complete_kw_up_ctx()), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid", return_value=result):
        log_entry = harness._execute_update_bid(db, p, now)

    assert log_entry.changed_at == now


def test_execute_set_user_lock_success_sets_changed_at_to_kst_now(db):
    p = _proposal(db, proposal_type="pause")
    p.target_lock = True
    db.commit()
    _settings(db, optimizer="ours")
    now = kst_now()
    result = _lock_write_result(before_lock=False, after_lock=True)

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "set_keyword_lock", return_value=result):
        log_entry = harness._execute_set_user_lock(db, p, now)

    assert log_entry.changed_at == now


def test_guard_failure_sets_changed_at_to_kst_now(db):
    p = _proposal(db, proposal_type="bid_up")  # target_bid 미설정 — 구조 가드 실패 경로
    now = kst_now()

    with pytest.raises(harness.MissingExecutionTargetError):
        harness._execute_update_bid(db, p, now)

    log = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).one()
    assert log.changed_at == now


def test_writer_failure_sets_changed_at_to_kst_now(db):
    p = _proposal(db, proposal_type="bid_down")
    p.target_bid = 170
    db.commit()
    _settings(db, optimizer="ours")
    now = kst_now()

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid",
                       side_effect=naver_sa_writer.WriteError("500")):
        with pytest.raises(naver_sa_writer.WriteError):
            harness._execute_update_bid(db, p, now)

    log = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).one()
    assert log.changed_at == now


def test_dry_run_execute_sets_changed_at_to_kst_now(db):
    p = _negative_proposal(db)
    _settings(db, optimizer="ours")
    now = kst_now()

    log_entry = harness.execute(db, p.id, now=now)  # dry_run=True 기본

    assert log_entry.changed_at == now


# ── X1b T5: D+7/14 채점 배선 확인(proposal_scoreboard 통합) ──────────────
# T5는 "배선 확인"만 요구했으나, 확인 과정에서 X1a부터 있던 실제 결함을 발견했다(이 파일
# 상단 "changed_at KST 명시"·"outcome D+14 배선" 두 절 참조) — 이 테스트는 harness.execute()의
# 산출물이 proposal_scoreboard.run_daily()의 입력 계약과 실제로 맞물리는지 종단 검증한다.


def test_executed_change_log_is_picked_up_by_proposal_scoreboard_after_verify_date(db):
    """harness.execute()가 만든 change_log 행이 D+14 후 proposal_scoreboard.run_daily()에
    실제로 픽업되는지 종단 확인 — 이게 T5의 핵심 질문이었고, outcome="executed" 즉시기록
    (수정 전 상태)이었다면 이 테스트는 pending=0으로 영원히 실패했을 것이다."""
    from app.services.naver_ad import proposal_scoreboard

    p = _proposal(db, proposal_type="bid_up")
    p.target_bid = 210
    db.commit()
    _settings(db, optimizer="ours")

    executed_date = date(2026, 6, 20)
    executed_at = datetime.combine(executed_date, datetime.min.time())
    result = _keyword_write_result(before={"bidAmt": 190, "userLock": False}, after={"bidAmt": 210, "userLock": False})

    with patch.object(harness, "_build_guardrail_context", return_value=_complete_kw_up_ctx()), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid", return_value=result):
        log_entry = harness.execute(db, p.id, dry_run=False, now=executed_at)

    verify_date = log_entry.verify_date  # executed_date + 14일(VERIFY_DAYS)
    assert verify_date == executed_date + timedelta(days=14)

    # verify_date 이전엔 아직 채점 대상 아님(정직 경계 확인)
    result_before_due = proposal_scoreboard.run_daily(db, today=verify_date - timedelta(days=1))
    assert result_before_due["pending"] == 0

    # verify_date 이후엔 outcome=None + dry_run=False + verify_date 경과 조건에 걸려 픽업된다.
    # (실제 개선폭 판정에 필요한 naver_ad_daily는 없으므로 모수미달로 outcome=None 유지 —
    # 여기서 검증하려는 건 "픽업 자체가 되는가"이지 판정 로직 자체가 아니다.)
    result_after_due = proposal_scoreboard.run_daily(db, today=verify_date)
    assert result_after_due["pending"] == 1

    db.refresh(log_entry)
    assert log_entry.actual_json is not None  # 모수미달이어도 실측치 기록 시도는 됨(재시도 대상)


# ── P3(D-NAO-42-f): update_budget 실쓰기 개방(D-NAO-16 4단계) ────────────────


def _campaign_budget_write_result(before=None, after=None):
    return naver_sa_writer.WriteResult(
        action="update_campaign_budget",
        before=before if before is not None else
        {"dailyBudget": 10_000, "useDailyBudget": True, "sharedBudgetId": None},
        response=after,
        after=after if after is not None else
        {"dailyBudget": 12_000, "useDailyBudget": True, "sharedBudgetId": None},
        created_ids=[],
    )


def _budget_proposal_row(db, target_budget=12_000, campaign_id="cmp-budget", proposal_type="budget_up"):
    p = _proposal(db, proposal_type=proposal_type, campaign_id=campaign_id,
                  target_type="campaign", target_id=campaign_id)
    p.target_budget = target_budget
    db.commit()
    return p


def test_execute_update_budget_success(db):
    p = _budget_proposal_row(db, target_budget=12_000)
    _settings(db, campaign_id="cmp-budget", optimizer="ours")
    result = _campaign_budget_write_result(
        before={"dailyBudget": 10_000, "useDailyBudget": True, "sharedBudgetId": None},
        after={"dailyBudget": 12_000, "useDailyBudget": True, "sharedBudgetId": None},
    )

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None) as mock_gate, \
         patch.object(harness.naver_sa_writer, "update_campaign_budget", return_value=result) as mock_write:
        log_entry = harness.execute(db, p.id, dry_run=False)

    mock_gate.assert_called_once()
    gate_call_args = mock_gate.call_args
    assert gate_call_args[0][0] == {
        "proposal_type": "budget_up", "target_bid": None, "target_lock": None, "target_budget": 12_000,
    }
    mock_write.assert_called_once_with("cmp-budget", 12_000)
    assert log_entry.action == "update_budget"
    assert log_entry.outcome is None  # 채점 전 정상(D+14 배선) — 성공 판별은 after_value
    assert json.loads(log_entry.before_value) == {
        "dailyBudget": 10_000, "useDailyBudget": True, "sharedBudgetId": None,
    }
    assert json.loads(log_entry.after_value) == {
        "dailyBudget": 12_000, "useDailyBudget": True, "sharedBudgetId": None,
    }

    db.refresh(p)
    assert p.executed_change_log_id == log_entry.id
    assert p.status == "approved"


def test_execute_update_budget_blocked_by_guardrail(db):
    p = _budget_proposal_row(db, target_budget=999_999)
    _settings(db, campaign_id="cmp-budget", optimizer="ours")

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value="예산 증액폭 초과") as mock_gate, \
         patch.object(harness.naver_sa_writer, "update_campaign_budget") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_gate.assert_called_once()
    mock_write.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert logs[0].outcome == "failed"
    assert "가드레일 차단" in logs[0].rationale


def test_execute_update_budget_wrong_target_type_guard_failure(db):
    p = _proposal(db, proposal_type="budget_up", campaign_id="cmp-budget",
                  target_type="keyword", target_id="nkw-1")
    p.target_budget = 12_000
    db.commit()
    _settings(db, campaign_id="cmp-budget", optimizer="ours")

    with pytest.raises(harness.MissingExecutionTargetError):
        harness.execute(db, p.id, dry_run=False)

    db.refresh(p)
    assert p.status == "failed"
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert "실행 불가" in logs[0].rationale


def test_execute_update_budget_missing_target_budget_guard_failure(db):
    p = _proposal(db, proposal_type="budget_up", campaign_id="cmp-budget",
                  target_type="campaign", target_id="cmp-budget")
    _settings(db, campaign_id="cmp-budget", optimizer="ours")

    with pytest.raises(harness.MissingExecutionTargetError):
        harness.execute(db, p.id, dry_run=False)

    db.refresh(p)
    assert p.status == "failed"


def test_execute_update_budget_target_id_campaign_id_mismatch_guard_failure(db):
    """Fix 2(codex P1): target_id != campaign_id는 stale/malformed 제안 — 구조(target_type/
    target_budget)만 유효해도 fail-closed로 차단해야 한다(guardrail_gate.check까지 가면 안 됨)."""
    p = _proposal(db, proposal_type="budget_up", campaign_id="cmp-budget",
                  target_type="campaign", target_id="cmp-other")
    p.target_budget = 12_000
    db.commit()
    _settings(db, campaign_id="cmp-budget", optimizer="ours")

    with pytest.raises(harness.MissingExecutionTargetError):
        harness.execute(db, p.id, dry_run=False)

    db.refresh(p)
    assert p.status == "failed"
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert "실행 불가" in logs[0].rationale


def test_execute_update_budget_write_failure_marks_proposal_failed(db):
    p = _budget_proposal_row(db, target_budget=12_000)
    _settings(db, campaign_id="cmp-budget", optimizer="ours")

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_campaign_budget",
                       side_effect=naver_sa_writer.WriteError("500")):
        with pytest.raises(naver_sa_writer.WriteError):
            harness.execute(db, p.id, dry_run=False)

    db.refresh(p)
    assert p.status == "failed"
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    assert logs[0].outcome == "failed"


def test_execute_update_budget_detects_external_change_appends_warning(db):
    """우리 마지막 기록(after_value.dailyBudget=10,000)과 방금 재조회한 라이브 before
    (13,000)가 다르면 — 그 사이 외부(MOP 등)가 바꿨다는 신호(D-NAO-13). 실행 자체는
    진행(경고만, 차단 아님) — update_bid와 동일 계약."""
    p = _budget_proposal_row(db, target_budget=15_000)
    _settings(db, campaign_id="cmp-budget", optimizer="ours")
    db.add(NaverChangeLog(
        entity_type="campaign", entity_id="cmp-budget", campaign_id="cmp-budget",
        action="update_budget", dry_run=False,
        after_value=json.dumps({"dailyBudget": 10_000}), executed_at=kst_now(),
    ))
    db.commit()
    result = _campaign_budget_write_result(
        before={"dailyBudget": 13_000, "useDailyBudget": True, "sharedBudgetId": None},
        after={"dailyBudget": 15_000, "useDailyBudget": True, "sharedBudgetId": None},
    )

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_campaign_budget", return_value=result):
        log_entry = harness.execute(db, p.id, dry_run=False)

    assert "외부 변경 감지" in log_entry.rationale
    assert "10000" in log_entry.rationale and "13000" in log_entry.rationale
    assert log_entry.outcome is None  # 차단 아님, 경고만 부착
