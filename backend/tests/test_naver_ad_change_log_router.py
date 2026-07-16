# test_naver_ad_change_log_router.py — D-NAO-47 T3: GET /api/naver/ad/change-log HTTP 왕복
# 원칙22: SA 단위테스트는 라우터를 안 거치므로 라우터 레이어 500을 못 잡는다(P2-S2 사고 전례).
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverChangeLog
from app.utils.kst import kst_now


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


def _seed(db, *, action="update_bid", campaign_id="cmp-1", dry_run=False, days_ago=0, outcome=None):
    row = NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id=campaign_id,
        action=action, dry_run=dry_run, changed_at=kst_now() - timedelta(days=days_ago),
        before_value=json.dumps({"bidAmt": 700}), after_value=json.dumps({"bidAmt": 900}),
        rationale="테스트 근거", outcome=outcome,
    )
    db.add(row)
    db.commit()
    return row


def test_change_log_returns_rows_newest_first(client, db):
    _seed(db, days_ago=3)
    _seed(db, days_ago=1)
    r = client.get("/api/naver/ad/change-log")
    assert r.status_code == 200
    items = r.json()["rows"]
    assert len(items) == 2
    assert items[0]["changed_at"] > items[1]["changed_at"]


def test_change_log_parses_before_after_json(client, db):
    _seed(db)
    items = client.get("/api/naver/ad/change-log").json()["rows"]
    assert items[0]["before"] == {"bidAmt": 700}
    assert items[0]["after"] == {"bidAmt": 900}
    assert items[0]["action"] == "update_bid"
    assert items[0]["rationale"] == "테스트 근거"


def test_change_log_survives_malformed_json_without_500(client, db):
    """★before_value에 쓰레기가 들어있어도 500이 아니라 null로 흘려보낸다(fail-safe)."""
    row = _seed(db)
    row.before_value = "{not json"
    db.commit()
    r = client.get("/api/naver/ad/change-log")
    assert r.status_code == 200
    assert r.json()["rows"][0]["before"] is None


def test_change_log_filters_by_campaign(client, db):
    _seed(db, campaign_id="cmp-1")
    _seed(db, campaign_id="cmp-2")
    items = client.get("/api/naver/ad/change-log?campaign_id=cmp-1").json()["rows"]
    assert len(items) == 1
    assert items[0]["campaign_id"] == "cmp-1"


def test_change_log_filters_by_action(client, db):
    _seed(db, action="update_bid")
    _seed(db, action="external_bid_change")
    items = client.get("/api/naver/ad/change-log?action=external_bid_change").json()["rows"]
    assert len(items) == 1
    assert items[0]["action"] == "external_bid_change"


def test_change_log_excludes_dry_run_by_default(client, db):
    """★기본값이 중요하다: 1층 '우리 조작 N회'는 실제 집행만 세야 한다.
    dry_run을 섞으면 아무것도 안 했는데 일한 것처럼 보인다(D-47-h 정직성)."""
    _seed(db, dry_run=True)
    _seed(db, dry_run=False)
    items = client.get("/api/naver/ad/change-log").json()["rows"]
    assert len(items) == 1
    assert items[0]["dry_run"] is False

    all_items = client.get("/api/naver/ad/change-log?include_dry_run=true").json()["rows"]
    assert len(all_items) == 2


def test_change_log_respects_days_window(client, db):
    _seed(db, days_ago=40)
    _seed(db, days_ago=2)
    items = client.get("/api/naver/ad/change-log?days=7").json()["rows"]
    assert len(items) == 1


def test_change_log_limit_is_capped(client, db):
    r = client.get("/api/naver/ad/change-log?limit=99999")
    assert r.status_code == 422  # Query(le=500) 위반


def test_change_log_returns_total_for_pagination(client, db):
    for _ in range(3):
        _seed(db)
    body = client.get("/api/naver/ad/change-log?limit=2").json()
    assert body["total"] == 3
    assert len(body["rows"]) == 2


def test_change_log_empty_is_200_not_404(client):
    """★빈 상태는 에러가 아니다 — 1층이 '우리 조작 0회'를 정직하게 그려야 한다(D-47-h)."""
    body = client.get("/api/naver/ad/change-log").json()
    assert body == {"rows": [], "total": 0}


# ── codex[P2] 2026-07-17: actor 필터 — "우리 조작"에 외부 감지를 섞으면 안 된다 ──
def test_actor_ours_excludes_external_detections(client, db):
    """★D-47-h의 핵심. prod 실측상 change_log의 dry_run=False 행 15건이 **전부**
    external_status_change(외부가 바꾼 걸 우리가 감지한 것)다. 필터 없이 total을 쓰면
    1층이 "우리 조작 15회"라고 표시한다 — 우리는 아무것도 실행하지 않았는데.
    0을 0이라고 말하는 게 이 화면의 존재 이유인데 정반대로 거짓말을 하게 된다."""
    _seed(db, action="external_status_change")
    _seed(db, action="external_bid_change")
    _seed(db, action="update_bid")  # 우리 실집행

    ours = client.get("/api/naver/ad/change-log?actor=ours").json()
    assert ours["total"] == 1
    assert ours["rows"][0]["action"] == "update_bid"


def test_actor_ours_is_zero_when_only_external_exists(client, db):
    """★prod 현재 상태 재현: 외부 감지만 있고 우리 실집행은 0."""
    for _ in range(15):
        _seed(db, action="external_status_change")
    body = client.get("/api/naver/ad/change-log?actor=ours").json()
    assert body["total"] == 0
    assert body["rows"] == []


def test_actor_external_returns_only_detections(client, db):
    _seed(db, action="external_bid_change")
    _seed(db, action="update_bid")
    body = client.get("/api/naver/ad/change-log?actor=external").json()
    assert body["total"] == 1
    assert body["rows"][0]["action"] == "external_bid_change"


def test_actor_excludes_settings_changes_from_ours(client, db):
    """optimizer_change·update_expert_delegation은 우리 시스템 내부 설정이지
    광고 실집행이 아니다(광고 API 쓰기 아님)."""
    _seed(db, action="optimizer_change")
    _seed(db, action="update_expert_delegation")
    assert client.get("/api/naver/ad/change-log?actor=ours").json()["total"] == 0


def test_actor_all_is_default(client, db):
    _seed(db, action="external_status_change")
    _seed(db, action="update_bid")
    assert client.get("/api/naver/ad/change-log").json()["total"] == 2
    assert client.get("/api/naver/ad/change-log?actor=all").json()["total"] == 2


def test_actor_rejects_unknown_value(client):
    assert client.get("/api/naver/ad/change-log?actor=bogus").status_code == 422


def test_execution_actions_derives_from_harness_mapping():
    """★드리프트 방지: 실행 액션 목록을 프론트나 라우터가 하드코딩하면 새 제안 유형이
    배선될 때 조용히 어긋난다. harness의 _ACTION_BY_PROPOSAL_TYPE이 단일 진실이다."""
    from app.services.naver_ad.naver_execution_harness import (
        EXECUTION_ACTIONS, _ACTION_BY_PROPOSAL_TYPE,
    )
    assert EXECUTION_ACTIONS == frozenset(_ACTION_BY_PROPOSAL_TYPE.values())
    assert "external_bid_change" not in EXECUTION_ACTIONS
    assert "external_status_change" not in EXECUTION_ACTIONS
    assert "optimizer_change" not in EXECUTION_ACTIONS


def test_actor_ours_excludes_failed_writes(client, db):
    """★codex[P2] R2: 실패한 쓰기는 '우리 조작'이 아니다. harness는 가드 거부·쓰기 실패에도
    같은 action(update_bid)을 dry_run=False로 남긴다(_guard_failure는 writer를 부르지도 않음).
    그걸 세면 광고에 아무 변화가 없었는데 "우리 조작 1회"가 된다.
    판별은 outcome이 아니라 **after_value 존재**다 — 이 코드베이스가 이미 정한 규약
    (naver_execution_harness.py:372 주석·_load_our_bid_writes와 동일)."""
    _seed(db, action="update_bid")  # 성공(before/after 채워짐)
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-fail", campaign_id="cmp-1",
        action="update_bid", dry_run=False, outcome="failed",
        changed_at=kst_now(), before_value=None, after_value=None,  # ← 실패라 안 채워짐
    ))
    db.commit()

    body = client.get("/api/naver/ad/change-log?actor=ours").json()
    assert body["total"] == 1
    assert body["rows"][0]["entity_id"] == "nkw-1"


def test_actor_ours_scoped_by_campaign(client, db):
    """★codex[P2] R2: D-47-c(N=1→N=여럿)를 위해 캠페인별 집계가 되어야 한다.
    캠페인이 늘면 A의 조작이 B 행에도 표시되면 안 된다."""
    _seed(db, action="update_bid", campaign_id="cmp-1")
    _seed(db, action="update_bid", campaign_id="cmp-2")
    _seed(db, action="update_bid", campaign_id="cmp-2")

    assert client.get("/api/naver/ad/change-log?actor=ours&campaign_id=cmp-1").json()["total"] == 1
    assert client.get("/api/naver/ad/change-log?actor=ours&campaign_id=cmp-2").json()["total"] == 2
