# test_naver_ad_proposals_router.py — P2-S3 T7 HTTP 라운드트립(TestClient)
# GET /proposals, GET/PUT /campaign-settings. P2-S2 500 사고 재발방지 원칙(원칙22) —
# SA/harness 단위테스트는 라우터 코드를 안 거치므로 라우터 레이어 버그(미정의 상수 참조 등
# 500 유발)를 못 잡는다. 반드시 실제 HTTP 왕복으로 검증한다.
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverChangeLog, NaverProposal


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


# ── GET /proposals ──
def test_proposals_endpoint_returns_200_empty(client):
    resp = client.get("/api/naver/ad/proposals")
    assert resp.status_code == 200
    assert resp.json() == {"rows": []}


def test_proposals_endpoint_returns_seeded_rows(client, db):
    db.add(NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-1",
                          campaign_id="cmp-1", status="pending", rationale="테스트"))
    db.commit()

    resp = client.get("/api/naver/ad/proposals")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["proposal_type"] == "bid_down"
    assert rows[0]["target_id"] == "nkw-1"


def test_proposals_endpoint_filters_by_status(client, db):
    db.add(NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-1",
                          campaign_id="cmp-1", status="pending"))
    db.add(NaverProposal(proposal_type="bid_up", target_type="keyword", target_id="nkw-2",
                          campaign_id="cmp-1", status="approved"))
    db.commit()

    resp = client.get("/api/naver/ad/proposals", params={"status": "approved"})
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["target_id"] == "nkw-2"


def test_proposals_endpoint_rejects_invalid_status(client):
    resp = client.get("/api/naver/ad/proposals", params={"status": "bogus"})
    assert resp.status_code == 400


def test_proposals_endpoint_rejects_inverted_date_range(client):
    resp = client.get("/api/naver/ad/proposals", params={
        "date_from": "2026-07-07", "date_to": "2026-06-23",
    })
    assert resp.status_code == 400


def test_proposals_endpoint_filters_by_campaign_id(client, db):
    db.add(NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-1",
                          campaign_id="cmp-1", status="pending"))
    db.add(NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-2",
                          campaign_id="cmp-2", status="pending"))
    db.commit()

    resp = client.get("/api/naver/ad/proposals", params={"campaign_id": "cmp-2"})
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["campaign_id"] == "cmp-2"


# ── GET/PUT /campaign-settings ──
def test_campaign_settings_get_returns_200_empty(client):
    resp = client.get("/api/naver/ad/campaign-settings")
    assert resp.status_code == 200
    assert resp.json() == {"rows": []}


def test_campaign_settings_put_creates_new_row(client, db):
    resp = client.put("/api/naver/ad/campaign-settings", json={
        "campaign_id": "cmp-new", "optimizer": "ours", "mode": "growth",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign_id"] == "cmp-new"
    assert body["optimizer"] == "ours"
    assert body["mode"] == "growth"

    get_resp = client.get("/api/naver/ad/campaign-settings", params={"campaign_id": "cmp-new"})
    assert get_resp.json()["rows"][0]["optimizer"] == "ours"


def test_campaign_settings_put_logs_optimizer_transition(client, db):
    client.put("/api/naver/ad/campaign-settings", json={"campaign_id": "cmp-1", "optimizer": "none"})
    resp = client.put("/api/naver/ad/campaign-settings", json={"campaign_id": "cmp-1", "optimizer": "ours"})
    assert resp.status_code == 200

    logs = db.query(NaverChangeLog).filter(
        NaverChangeLog.campaign_id == "cmp-1", NaverChangeLog.action == "optimizer_change",
    ).all()
    assert len(logs) == 1
    assert logs[0].before_value == "none"
    assert logs[0].after_value == "ours"


def test_campaign_settings_put_no_log_when_optimizer_unchanged(client, db):
    client.put("/api/naver/ad/campaign-settings", json={"campaign_id": "cmp-1", "optimizer": "ours"})
    client.put("/api/naver/ad/campaign-settings", json={"campaign_id": "cmp-1", "optimizer": "ours", "memo": "메모만 변경"})

    logs = db.query(NaverChangeLog).filter(NaverChangeLog.campaign_id == "cmp-1").all()
    assert len(logs) == 1  # 최초 none→ours 전환 1건만, memo만 바뀐 두번째 호출은 로그 없음


def test_campaign_settings_put_rejects_invalid_optimizer(client):
    resp = client.put("/api/naver/ad/campaign-settings", json={"campaign_id": "cmp-1", "optimizer": "bogus"})
    assert resp.status_code == 400


def test_campaign_settings_put_rejects_invalid_mode(client):
    resp = client.put("/api/naver/ad/campaign-settings", json={
        "campaign_id": "cmp-1", "optimizer": "ours", "mode": "bogus",
    })
    assert resp.status_code == 400


def test_campaign_settings_put_target_roas_override(client):
    resp = client.put("/api/naver/ad/campaign-settings", json={
        "campaign_id": "cmp-1", "optimizer": "ours", "target_roas_override": 350.5,
    })
    assert resp.status_code == 200
    assert resp.json()["target_roas_override"] == 350.5
