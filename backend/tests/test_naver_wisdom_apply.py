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
from app.services.naver_ad import expert_briefing_builder, wisdom_apply, wisdom_loop

NOW = datetime(2026, 7, 20, 8, 45)  # KST
_SUGGESTION = {"param": "17E 스텝 클램프 상한", "direction": "up", "note": "휴일 저속 관찰"}


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
    """param_suggestion 있는 지혜 → param_change 제안 1건. 실행 payload 전부 None(실행 불가 형태)·
    비정보성(proposal_type=param_change). 멱등 추적 컬럼(param_proposal_id)이 새겨진다."""
    cand = _candidate(db, param_suggestion=_SUGGESTION)
    entry = _entry(db, cand)
    db.commit()

    res = wisdom_apply.propose_param_changes(db, now=NOW)
    assert res["proposals_created"] == 1 and res["skipped_no_suggestion"] == 0 and res["errors"] == 0

    prop = db.query(NaverProposal).filter_by(proposal_type="param_change").one()
    assert prop.status == "pending"
    assert prop.campaign_id == "cmp1"
    assert prop.target_type == "account" and prop.target_id == ""
    # 실행 payload 전부 미설정 — 실행 불가 형태(D-NAO-54 금지선)
    assert prop.target_bid is None and prop.target_lock is None and prop.target_budget is None
    # rationale에 지혜 원칙 + param_suggestion 내용 + 승률 근거가 담긴다
    assert "휴일엔 bid_up이 좋았다" in prop.rationale
    assert "17E 스텝 클램프 상한" in prop.rationale
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


def _seed_param_change(rdb) -> NaverProposal:
    p = NaverProposal(
        proposal_type="param_change", target_type="account", target_id="",
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


def test_param_change_approve_records_no_execute(client, rdb, monkeypatch):
    """결정 전용 승인: approve→approved + approval_source=console, change_log 0행,
    harness.execute 미호출."""
    from app.routers import naver_ad as router_mod

    called = {"execute": 0}
    monkeypatch.setattr(
        router_mod.naver_execution_harness, "execute",
        lambda *a, **k: called.__setitem__("execute", called["execute"] + 1),
    )
    p = _seed_param_change(rdb)
    resp = client.post(f"/api/naver/ad/proposals/{p.id}/status", json={"status": "approved"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["approval_source"] == "console"
    assert body["decision_only"] is True
    assert called["execute"] == 0  # 승인은 execute를 부르지 않는다
    assert rdb.query(NaverChangeLog).count() == 0  # 광고 API 쓰기 이력 없음


def test_param_change_execute_endpoint_blocks(client, rdb):
    """만약 콘솔이 실수로 /execute를 쳐도 결정 전용은 409로 차단(실행 대상 아님)."""
    p = _seed_param_change(rdb)
    client.post(f"/api/naver/ad/proposals/{p.id}/status", json={"status": "approved"})
    resp = client.post(f"/api/naver/ad/proposals/{p.id}/execute")
    assert resp.status_code == 409
    assert rdb.query(NaverChangeLog).count() == 0


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
