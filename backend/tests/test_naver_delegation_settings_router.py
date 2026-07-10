# test_naver_delegation_settings_router.py — X1a T5 HTTP 라운드트립(TestClient)
# GET/PUT /api/naver/ad/settings/expert-delegation — E2 위임 스위치 콘솔 설정(D-NAO-25).
# 원칙22(라우터 레이어는 SA 단위테스트로 못 잡음) — 실제 HTTP 왕복으로 검증.
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverAccountSettings, NaverChangeLog


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


def test_get_default_returns_empty_delegated_and_delegable_list(client):
    resp = client.get("/api/naver/ad/settings/expert-delegation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["delegated_types"] == []
    # X1b T4로 정지·재개·입찰 개방 — delegable도 자동 확장(sorted, 예산 제외 D-NAO-34)
    assert body["delegable_types"] == [
        "bid_down", "bid_up", "growth_bid_up", "negative_keyword", "pause", "resume",
    ]


def test_put_valid_type_saves_and_records_change_log(client, db):
    resp = client.put("/api/naver/ad/settings/expert-delegation",
                       json={"delegated_types": ["negative_keyword"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["delegated_types"] == ["negative_keyword"]

    row = db.query(NaverAccountSettings).filter(
        NaverAccountSettings.key == "expert_delegated_types"
    ).first()
    assert row is not None
    assert json.loads(row.value_json) == ["negative_keyword"]

    logs = db.query(NaverChangeLog).filter(NaverChangeLog.action == "update_expert_delegation").all()
    assert len(logs) == 1
    assert json.loads(logs[0].before_value) == []
    assert json.loads(logs[0].after_value) == ["negative_keyword"]
    assert logs[0].dry_run is False


def test_put_no_change_log_when_value_unchanged(client, db):
    client.put("/api/naver/ad/settings/expert-delegation", json={"delegated_types": ["negative_keyword"]})
    client.put("/api/naver/ad/settings/expert-delegation", json={"delegated_types": ["negative_keyword"]})

    logs = db.query(NaverChangeLog).filter(NaverChangeLog.action == "update_expert_delegation").all()
    assert len(logs) == 1  # 두 번째 PUT은 값이 동일 — 감사 기록 추가 없음


def test_put_disallowed_type_returns_400(client, db):
    """X1b T4로 bid_up이 delegable해졌으므로, 여전히 스코프 밖인 budget_up(D-NAO-34)으로 검증."""
    resp = client.put("/api/naver/ad/settings/expert-delegation",
                       json={"delegated_types": ["budget_up"]})
    assert resp.status_code == 400
    assert db.query(NaverAccountSettings).count() == 0


def test_put_duplicate_type_returns_400(client, db):
    resp = client.put("/api/naver/ad/settings/expert-delegation",
                       json={"delegated_types": ["negative_keyword", "negative_keyword"]})
    assert resp.status_code == 400
    assert db.query(NaverAccountSettings).count() == 0


def test_put_empty_list_clears_delegation(client, db):
    client.put("/api/naver/ad/settings/expert-delegation", json={"delegated_types": ["negative_keyword"]})
    resp = client.put("/api/naver/ad/settings/expert-delegation", json={"delegated_types": []})
    assert resp.status_code == 200
    assert resp.json()["delegated_types"] == []


def test_put_missing_body_field_422(client):
    resp = client.put("/api/naver/ad/settings/expert-delegation", json={})
    assert resp.status_code == 422


def test_get_after_put_reflects_saved_state(client):
    client.put("/api/naver/ad/settings/expert-delegation", json={"delegated_types": ["negative_keyword"]})
    resp = client.get("/api/naver/ad/settings/expert-delegation")
    assert resp.json()["delegated_types"] == ["negative_keyword"]
