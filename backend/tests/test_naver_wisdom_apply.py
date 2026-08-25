# test_naver_wisdom_apply.py — D-NAO-54 P4 소비층(wisdom_apply·라우터 결정 전용 승인·
#   briefing prefix 주입·wisdom_loop apply 단계) 단위·HTTP 테스트.
# ★검증 축: ①param_suggestion 있는 지혜→결정 전용 param_change 제안(payload None·비정보성)
#   ②없는 지혜→미생성 ③멱등 ④금지선(실행 매핑 부재) ⑤라우터 결정 전용 승인(approve→approved·
#   change_log 0·execute 미호출) ⑥기존 실행형 승인 흐름 불변(회귀) ⑦briefing prefix 주입/0건
#   생략 ⑧하니스 apply 단계.
from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverChangeLog, NaverProposal, OpsWisdomCandidate, OpsWisdomEntry
from app.services.naver_ad import expert_briefing_builder, guardrail_params, wisdom_apply, wisdom_loop

NOW = datetime(2026, 7, 20, 8, 45)  # KST
# ★D-NAO-248 §4-B(B7) 이후: param은 자유 텍스트가 아니라 guardrail_params.SPECS 화이트리스트
#   키만 허용되고, scope='unconditional'이어야 param_change 제안이 생성된다(코드 클램프,
#   wisdom_apply._classify_param_suggestion). 옛 _SUGGESTION(자유 텍스트 "17E 스텝 클램프
#   상한", scope 없음)은 이제 CONDITIONAL로 떨어져 제안을 만들지 않는다 — 아래 화이트리스트
#   케이스로 교체한다.
_SUGGESTION = {
    "param": "cooldown_hours", "scope": "unconditional",
    "direction": "up", "note": "휴일 저속 관찰",
}
# 옛 형태(자유 텍스트 param, scope 없음) — B7 게이트 회귀 테스트용으로 이름을 바꿔 보존한다.
_LEGACY_FREETEXT_SUGGESTION = {
    "param": "17E 스텝 클램프 상한", "direction": "up", "note": "휴일 저속 관찰",
}


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


@pytest.fixture
def client_and_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        d = TestingSession()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_db] = _override_get_db
    seed = TestingSession()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


@pytest.fixture
def client(client_and_session):
    return client_and_session[0]


@pytest.fixture
def rdb(client_and_session):
    return client_and_session[1]


def _candidate(db, *, param_suggestion=None, campaign_id="cmp1", action="bid_up",
               good=3, bad=1, signature="sig1"):
    verdict = {"verdict": "promote", "principle": "휴일엔 bid_up이 좋았다", "rationale": "3회 관찰"}
    if param_suggestion is not None:
        verdict["param_suggestion"] = param_suggestion
    c = OpsWisdomCandidate(
        signature=signature, campaign_id=campaign_id, action=action,
        env_bucket_json=json.dumps({"day_class": "weekday", "season": "summer", "iphone_window": "normal"}),
        observation="obs", occurrences=good + bad, good_count=good, bad_count=bad,
        first_seen_at=NOW, last_seen_at=NOW, source_entry_ids_json="[1]",
        status="promoted", judge_verdict_json=json.dumps(verdict),
    )
    db.add(c)
    db.flush()
    return c


def _entry(db, cand, *, status="active", promoted_at=NOW):
    e = OpsWisdomEntry(
        wisdom_text="휴일엔 bid_up이 좋았다", source_candidate_id=cand.id,
        judge_rationale="3회 관찰", status=status, promoted_at=promoted_at,
    )
    db.add(e)
    db.flush()
    return e


# ══════════════════════════ propose_param_changes (param_proposal_sa) ══════════════════════════


def test_param_suggestion_creates_decision_only_proposal(db):
    """param_suggestion 있는 지혜(scope=unconditional ∧ param∈SPECS) → param_change 제안 1건.
    실행 payload 전부 None(실행 불가 형태)·비정보성(proposal_type=param_change). 멱등 추적
    컬럼(param_proposal_id)이 새겨진다. ★B7-5: SPECS 키는 rationale 텍스트가 아니라
    target_type/target_id에 구조적으로 실린다(guardrail_params.TARGET_TYPE)."""
    cand = _candidate(db, param_suggestion=_SUGGESTION)
    entry = _entry(db, cand)
    db.commit()

    res = wisdom_apply.propose_param_changes(db, now=NOW)
    assert res["proposals_created"] == 1 and res["skipped_no_suggestion"] == 0 and res["errors"] == 0
    assert res["skipped_conditional"] == 0 and res["skipped_unmapped_param"] == 0

    prop = db.query(NaverProposal).filter_by(proposal_type="param_change").one()
    assert prop.status == "pending"
    assert prop.campaign_id == "cmp1"
    assert prop.target_type == guardrail_params.TARGET_TYPE and prop.target_id == "cooldown_hours"
    # 실행 payload 전부 미설정 — 실행 불가 형태(D-NAO-54 금지선)
    assert prop.target_bid is None and prop.target_lock is None and prop.target_budget is None
    # rationale에 지혜 원칙 + param_suggestion 내용 + 승률 근거가 담긴다
    assert "휴일엔 bid_up이 좋았다" in prop.rationale
    assert "cooldown_hours" in prop.rationale
    assert "win_rate" in prop.rationale
    assert "자동 적용 없음" in prop.expected_effect
    # 멱등 추적
    db.refresh(entry)
    assert entry.param_proposal_id == prop.id


def test_no_param_suggestion_creates_nothing(db):
    """param_suggestion 없는 지혜(대부분) → 제안 미생성(skipped_no_suggestion)."""
    cand = _candidate(db, param_suggestion=None)
    _entry(db, cand)
    db.commit()

    res = wisdom_apply.propose_param_changes(db, now=NOW)
    assert res["proposals_created"] == 0 and res["skipped_no_suggestion"] == 1
    assert db.query(NaverProposal).count() == 0


def test_empty_param_suggestion_is_ignored(db):
    """param/note가 전부 빈 param_suggestion(빈 dict 포함)은 제안 가치 없음 → 미생성."""
    cand = _candidate(db, param_suggestion={"direction": "review"})  # param/note 없음
    _entry(db, cand)
    db.commit()
    res = wisdom_apply.propose_param_changes(db, now=NOW)
    assert res["proposals_created"] == 0 and res["skipped_no_suggestion"] == 1


# ══════════════════════ B7 코드 클램프(fail-closed) ══════════════════════


def test_conditional_scope_creates_no_proposal(db):
    """★B7: scope가 없으면(옛 판사 응답 포함) CONDITIONAL로 떨어진다 — 제안 0건.
    격상(제안 생성) 방향으로는 절대 흔들리지 않는다(fail-closed)."""
    cand = _candidate(db, param_suggestion=_LEGACY_FREETEXT_SUGGESTION)  # scope 없음
    _entry(db, cand)
    db.commit()
    res = wisdom_apply.propose_param_changes(db, now=NOW)
    assert res["proposals_created"] == 0
    assert res["skipped_conditional"] == 1
    assert res["skipped_unmapped_param"] == 0
    assert db.query(NaverProposal).count() == 0


def test_explicit_conditional_scope_creates_no_proposal(db):
    """scope="conditional"을 명시해도(판사가 정직하게 판단한 정상 케이스) 제안 0건."""
    suggestion = {**_SUGGESTION, "scope": "conditional"}
    cand = _candidate(db, param_suggestion=suggestion)
    _entry(db, cand)
    db.commit()
    res = wisdom_apply.propose_param_changes(db, now=NOW)
    assert res["proposals_created"] == 0 and res["skipped_conditional"] == 1
    assert db.query(NaverProposal).count() == 0


def test_unmapped_param_creates_no_proposal(db):
    """★B7: scope=unconditional이어도 param이 SPECS 화이트리스트 밖이면 UNMAPPED — 제안 0건."""
    suggestion = {**_SUGGESTION, "param": "존재하지 않는 다이얼"}
    cand = _candidate(db, param_suggestion=suggestion)
    _entry(db, cand)
    db.commit()
    res = wisdom_apply.propose_param_changes(db, now=NOW)
    assert res["proposals_created"] == 0
    assert res["skipped_unmapped_param"] == 1
    assert res["skipped_conditional"] == 0
    assert db.query(NaverProposal).count() == 0


def test_unconditional_mapped_creates_exactly_one_proposal(db):
    """정상 케이스(scope=unconditional ∧ param∈SPECS) → 제안 정확히 1건(회귀 고정)."""
    cand = _candidate(db, param_suggestion=_SUGGESTION)
    _entry(db, cand)
    db.commit()
    res = wisdom_apply.propose_param_changes(db, now=NOW)
    assert res["proposals_created"] == 1
    assert res["skipped_conditional"] == 0 and res["skipped_unmapped_param"] == 0
    assert db.query(NaverProposal).filter_by(proposal_type="param_change").count() == 1


# ══════════════════════ B7-6 gate_summary — 카운터 표면화(0이어도 침묵 금지) ══════════════════════


def test_gate_summary_all_zero_when_no_wisdom(db):
    """지혜가 0건이면 넷 다 0 — 그래도 키는 항상 낸다(교훈 #318)."""
    out = wisdom_apply.gate_summary(db)
    assert out == {
        "unconditional_mapped": 0, "conditional_fallback": 0,
        "unmapped_param": 0, "no_suggestion": 0,
    }


def test_gate_summary_classifies_each_bucket(db):
    """네 버킷이 각각 올바르게 세어진다 — read-time 재현이라 propose_param_changes를
    실행하지 않아도(제안 생성 여부와 무관하게) 지혜 저장 상태만으로 집계된다."""
    c1 = _candidate(db, param_suggestion=_SUGGESTION, signature="s1")
    _entry(db, c1)
    c2 = _candidate(db, param_suggestion=_LEGACY_FREETEXT_SUGGESTION, signature="s2")
    _entry(db, c2)
    c3 = _candidate(db, param_suggestion={**_SUGGESTION, "param": "모르는 값"}, signature="s3")
    _entry(db, c3)
    c4 = _candidate(db, param_suggestion=None, signature="s4")
    _entry(db, c4)
    db.commit()

    out = wisdom_apply.gate_summary(db)
    assert out["unconditional_mapped"] == 1
    assert out["conditional_fallback"] == 1
    assert out["unmapped_param"] == 1
    assert out["no_suggestion"] == 1


def test_propose_is_idempotent(db):
    """두 번째 실행은 아무 것도 만들지 않는다(param_proposal_id 전용 추적)."""
    cand = _candidate(db, param_suggestion=_SUGGESTION)
    _entry(db, cand)
    db.commit()
    wisdom_apply.propose_param_changes(db, now=NOW)
    res2 = wisdom_apply.propose_param_changes(db, now=NOW)
    assert res2["proposals_created"] == 0
    assert db.query(NaverProposal).filter_by(proposal_type="param_change").count() == 1


def test_retired_entry_is_not_proposed(db):
    """비활성(retired) 지혜는 제안 대상이 아니다(active만)."""
    cand = _candidate(db, param_suggestion=_SUGGESTION)
    _entry(db, cand, status="retired")
    db.commit()
    res = wisdom_apply.propose_param_changes(db, now=NOW)
    assert res["active_entries"] == 0 and res["proposals_created"] == 0


def test_param_change_not_wired_to_execution():
    """★금지선: param_change는 실행 매핑·개방 액션·실행자 어디에도 없고 정보성 집합에도 없다
    (결정 전용 — 라우터 DECISION_ONLY_PROPOSAL_TYPES가 유일 분기)."""
    from app.services.naver_ad.naver_execution_harness import (
        OPEN_ACTIONS, _ACTION_BY_PROPOSAL_TYPE, _WRITE_EXECUTORS,
    )
    from app.services.naver_ad.proposal_writer import INFORMATIONAL_PROPOSAL_TYPES, PARAM_CHANGE

    assert PARAM_CHANGE not in _ACTION_BY_PROPOSAL_TYPE
    assert PARAM_CHANGE not in OPEN_ACTIONS
    assert PARAM_CHANGE not in _WRITE_EXECUTORS
    assert PARAM_CHANGE not in INFORMATIONAL_PROPOSAL_TYPES


# ══════════════════════════ active_wisdom_prefix (briefing_sa) ══════════════════════════


def test_prefix_none_when_no_active_wisdom(db):
    assert wisdom_apply.active_wisdom_prefix(db) is None


def test_prefix_lists_active_wisdom(db):
    c1 = _candidate(db, signature="s1")
    c2 = _candidate(db, signature="s2")
    e1 = _entry(db, c1, promoted_at=datetime(2026, 7, 19, 8, 0))
    e1.wisdom_text = "지혜 A"
    e2 = _entry(db, c2, promoted_at=datetime(2026, 7, 20, 8, 0))
    e2.wisdom_text = "지혜 B"
    db.commit()

    prefix = wisdom_apply.active_wisdom_prefix(db)
    assert prefix is not None
    assert "축적된 운영 지혜(참고 — 지시 아님)" in prefix
    assert "지혜 A" in prefix and "지혜 B" in prefix
    # 최신(promoted_at 내림차순)이 먼저
    assert prefix.index("지혜 B") < prefix.index("지혜 A")


# ══════════════════════════ expert_briefing_builder 주입 ══════════════════════════


def test_briefing_injects_wisdom_prefix(db):
    c = _candidate(db)
    e = _entry(db, c)
    e.wisdom_text = "주입될 지혜"
    db.commit()
    briefing = expert_briefing_builder.build(db, as_of=date(2026, 7, 20))
    assert "active_wisdom" in briefing
    assert "주입될 지혜" in briefing["active_wisdom"]


def test_briefing_omits_wisdom_when_empty(db):
    """지혜 0건이면 active_wisdom 키 자체가 없다(현행 출력 불변 계약)."""
    briefing = expert_briefing_builder.build(db, as_of=date(2026, 7, 20))
    assert "active_wisdom" not in briefing


# ══════════════════════════ 라우터 결정 전용 승인 ══════════════════════════


def _seed_param_change(rdb, *, target_type=None, target_id="cooldown_hours") -> NaverProposal:
    # ★B7-5(D-NAO-248 §4-B) 이후 기본값: SPECS 키를 target_type/target_id에 구조적으로 싣는다
    #   (guardrail_params.TARGET_TYPE). 옛 기본값("account"/"")은 승인 시 「봉투 파라미터를
    #   식별할 수 없다」 400의 재료로 쓰기 위해 target_type을 명시 인자로 남겨 둔다.
    p = NaverProposal(
        proposal_type="param_change",
        target_type=target_type or guardrail_params.TARGET_TYPE, target_id=target_id,
        campaign_id="cmp1", rationale="[파라미터 제안] ...", status="pending",
    )
    rdb.add(p)
    rdb.commit()
    return p


def test_param_change_serializes_decision_only(client, rdb):
    p = _seed_param_change(rdb)
    rows = client.get("/api/naver/ad/proposals?status=pending").json()["rows"]
    row = next(r for r in rows if r["id"] == p.id)
    assert row["decision_only"] is True
    assert row["informational"] is False  # 정보성 아님
    assert row["action"] is None  # 실행 매핑 없음
    assert row["executable"] is False  # 실행 대상 아님


# ══════════════════════ B1(D-NAO-248 §4-B) — 승인=적용 사슬 ══════════════════════


def test_param_change_approve_without_applied_value_returns_400_no_transition(client, rdb):
    """★B1: applied_value 없이 param_change를 승인하면 400 — 값은 사람이 정한다(코드가 값을
    발명하지 않는다). 상태 전이도 일어나지 않는다(pending 그대로, change_log도 0행)."""
    p = _seed_param_change(rdb)
    resp = client.post(f"/api/naver/ad/proposals/{p.id}/status", json={"status": "approved"})
    assert resp.status_code == 400
    assert "applied_value" in resp.text

    rdb.refresh(p)
    assert p.status == "pending"
    assert p.executed_change_log_id is None
    assert p.decided_at is None
    assert rdb.query(NaverChangeLog).count() == 0


def test_param_change_approve_with_applied_value_applies_and_records(client, rdb, monkeypatch):
    """★B1 핵심: 승인 + 유효 applied_value → KV 반영 + change_log 1행 + executed_change_log_id
    + decided_at/by/note 기입. harness.execute는 여전히 호출되지 않는다(param_change는 실행
    매핑이 없다 — 이건 D-NAO-54 금지선 그대로다)."""
    from app.routers import naver_ad as router_mod

    called = {"execute": 0}
    monkeypatch.setattr(
        router_mod.naver_execution_harness, "execute",
        lambda *a, **k: called.__setitem__("execute", called["execute"] + 1),
    )
    p = _seed_param_change(rdb, target_id="cooldown_hours")
    resp = client.post(
        f"/api/naver/ad/proposals/{p.id}/status",
        json={"status": "approved", "applied_value": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["approval_source"] == "console"
    assert body["decision_only"] is True
    assert body["executed_change_log_id"] is not None
    assert called["execute"] == 0  # 승인은 harness.execute를 부르지 않는다(실행 매핑 부재)

    rdb.refresh(p)
    assert p.status == "approved"
    assert p.decided_at is not None
    assert p.decided_by == "console"
    assert "cooldown_hours" in (p.decision_note or "")
    assert p.executed_change_log_id is not None

    logs = rdb.query(NaverChangeLog).all()
    assert len(logs) == 1
    assert logs[0].id == p.executed_change_log_id
    assert logs[0].action == "update_guardrail_params"
    assert logs[0].proposal_id == p.id
    assert '"cooldown_hours": "5"' in (logs[0].after_value or "")

    # 반영 확인 — describe()가 source='db'·값 5를 실제로 돌려준다(claimed≠wired 재발 방지).
    from app.services.naver_ad import guardrail_params as gp
    rows = {r["key"]: r for r in gp.describe(rdb)}
    assert rows["cooldown_hours"]["source"] == "db"
    assert rows["cooldown_hours"]["value"] == 5.0


def test_param_change_approve_does_not_wipe_the_other_params(client, rdb):
    """★B1 회귀 — 승인은 «그 키 하나»만 바꾼다. 사람이 설정해 둔 나머지를 지우면 안 된다.

    `apply_params`는 PUT 계약을 따라 **전체 치환**이다(넘긴 키만 남고 나머지는 코드 상수로
    복귀). 그런데 승인 경로는 제안이 지목한 키 하나만 넘긴다 — 그대로 치환하면 사람이 PUT으로
    설정해 둔 다른 키가 **조용히 코드 기본값으로 되돌아간다.** 되돌아간 흔적은 화면 source가
    'db'→'code'로 바뀌는 것뿐이라, 사람은 자기가 안 만진 값이 바뀐 걸 못 쫓는다.
    PUT의 전체 치환은 «사람이 화면 전체를 보고 저장»하는 맥락이라 정당하지만, 승인은
    «제안 한 건»의 맥락이라 같은 규칙을 쓰면 안 된다.
    """
    from app.models import NaverAccountSettings

    # 사람이 미리 두 키를 설정해 둔 상태(PUT 경로로 들어온 것과 같은 모양)
    rdb.add(NaverAccountSettings(
        key=guardrail_params.SETTINGS_KEY,
        value_json=json.dumps({"cooldown_hours": "3", "max_daily_auto_bid_downs": "5"}),
    ))
    rdb.commit()

    p = _seed_param_change(rdb, target_id="max_auto_up_multiple")
    resp = client.post(
        f"/api/naver/ad/proposals/{p.id}/status",
        json={"status": "approved", "applied_value": "2.5"},
    )
    assert resp.status_code == 200

    rows = {r["key"]: r for r in guardrail_params.describe(rdb)}
    # 승인한 키는 반영됐다
    assert rows["max_auto_up_multiple"]["source"] == "db"
    assert rows["max_auto_up_multiple"]["value"] == 2.5
    # ★손대지 않은 두 키는 사람이 정한 값 그대로여야 한다(코드 상수로 되돌아가면 실패)
    assert rows["cooldown_hours"]["source"] == "db", "승인이 남의 키를 코드 상수로 되돌렸다"
    assert rows["cooldown_hours"]["value"] == 3.0
    assert rows["max_daily_auto_bid_downs"]["source"] == "db"
    assert rows["max_daily_auto_bid_downs"]["value"] == 5.0


def test_param_change_approve_out_of_range_value_returns_400_and_no_transition(client, rdb):
    """★B2 봉투 불변 — 승인 경로로 들어온 봉투 밖 값도 400으로 거부된다(약화·우회 금지).
    실패(400) 시 상태 전이도 되돌린다: pending 그대로, change_log도 0행."""
    p = _seed_param_change(rdb, target_id="cooldown_hours")
    resp = client.post(
        f"/api/naver/ad/proposals/{p.id}/status",
        json={"status": "approved", "applied_value": 999},
    )
    assert resp.status_code == 400

    rdb.refresh(p)
    assert p.status == "pending"
    assert p.approval_source is None
    assert p.executed_change_log_id is None
    assert p.decided_at is None  # 실패 시 결정 메타도 기입되지 않는다(한 트랜잭션)
    assert rdb.query(NaverChangeLog).count() == 0
    from app.services.naver_ad import guardrail_params as gp
    assert {r["key"]: r["source"] for r in gp.describe(rdb)}["cooldown_hours"] == "code"


def test_param_change_approve_unresolvable_target_returns_400(client, rdb):
    """레거시/데이터 정합 결함 방어 — target_type이 guardrail_param이 아니거나 target_id가
    SPECS 밖이면(예: 옛 스키마의 "account"/"") 「식별 불가」 400. 상태 전이 없음."""
    p = _seed_param_change(rdb, target_type="account", target_id="")
    resp = client.post(
        f"/api/naver/ad/proposals/{p.id}/status",
        json={"status": "approved", "applied_value": 5},
    )
    assert resp.status_code == 400
    rdb.refresh(p)
    assert p.status == "pending"


def test_param_change_reject_records_decision_meta(client, rdb):
    """★B1 — 반려 시에도 decided_at/by/note를 기입한다(A7 표면이 반려 사유를 보여줄 수 있게).
    applied_value는 필요 없다(반려는 값을 안 쓴다)."""
    p = _seed_param_change(rdb)
    resp = client.post(f"/api/naver/ad/proposals/{p.id}/status", json={"status": "rejected"})
    assert resp.status_code == 200
    rdb.refresh(p)
    assert p.status == "rejected"
    assert p.decided_at is not None
    assert p.decided_by == "console"
    assert p.decision_note
    assert rdb.query(NaverChangeLog).count() == 0  # 반려는 파라미터를 건드리지 않는다


def test_param_change_execute_endpoint_blocks(client, rdb):
    """만약 콘솔이 실수로 /execute를 쳐도 결정 전용은 409로 차단(실행 대상 아님) — 승인 자체가
    KV를 반영하지만(B1), 광고 API 실쓰기 경로(execute)와는 여전히 무관하다."""
    p = _seed_param_change(rdb)
    client.post(f"/api/naver/ad/proposals/{p.id}/status",
                json={"status": "approved", "applied_value": 5})
    resp = client.post(f"/api/naver/ad/proposals/{p.id}/execute")
    assert resp.status_code == 409
    # KV 반영으로 생긴 change_log 1행은 있어도 되지만 실행(execute)이 만든 행은 0이어야 한다 —
    # 여기선 이미 승인 단계에서 1행이 생겼으므로 그 이상 늘지 않았는지로 "execute 무영향"을 본다.
    assert rdb.query(NaverChangeLog).count() == 1


def test_executable_approve_flow_unchanged(client, rdb, monkeypatch):
    """★회귀: 기존 실행형(bid_up) 승인 흐름은 1비트도 안 바뀐다 — /status는 여전히 execute를
    부르지 않고(실행은 별도 /execute), decision_only=False로 직렬화된다."""
    from app.routers import naver_ad as router_mod

    called = {"execute": 0}
    monkeypatch.setattr(
        router_mod.naver_execution_harness, "execute",
        lambda *a, **k: called.__setitem__("execute", called["execute"] + 1),
    )
    p = NaverProposal(
        proposal_type="bid_up", target_type="keyword", target_id="nkw-1",
        campaign_id="cmp1", adgroup_id="grp-1", target_bid=1000, status="pending",
    )
    rdb.add(p)
    rdb.commit()
    resp = client.post(f"/api/naver/ad/proposals/{p.id}/status", json={"status": "approved"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved" and body["approval_source"] == "console"
    assert body["decision_only"] is False
    assert body["executable"] is True  # update_bid 개방·target 정상
    assert called["execute"] == 0  # /status는 execute를 부르지 않는다(기존 계약)


# ══════════════════════════ wisdom_loop apply 단계 ══════════════════════════


def test_loop_apply_stage_creates_param_change(db):
    """run_daily_wisdom의 ⑤apply 단계가 param_suggestion 지혜를 param_change 제안으로 낸다."""
    cand = _candidate(db, param_suggestion=_SUGGESTION)
    _entry(db, cand)  # writer가 이미 만든 것으로 간주(멱등 skip) → apply만 작동
    db.commit()

    res = wisdom_loop.run_daily_wisdom(db, now=NOW)
    assert res["stage_status"]["apply"] == "ok"
    assert res["apply"]["proposals_created"] == 1
    assert db.query(NaverProposal).filter_by(proposal_type="param_change").count() == 1
