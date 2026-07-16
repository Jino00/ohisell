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
    items = r.json()["items"]
    assert len(items) == 2
    assert items[0]["changed_at"] > items[1]["changed_at"]


def test_change_log_parses_before_after_json(client, db):
    _seed(db)
    items = client.get("/api/naver/ad/change-log").json()["items"]
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
    assert r.json()["items"][0]["before"] is None


def test_change_log_filters_by_campaign(client, db):
    _seed(db, campaign_id="cmp-1")
    _seed(db, campaign_id="cmp-2")
    items = client.get("/api/naver/ad/change-log?campaign_id=cmp-1").json()["items"]
    assert len(items) == 1
    assert items[0]["campaign_id"] == "cmp-1"


def test_change_log_filters_by_action(client, db):
    _seed(db, action="update_bid")
    _seed(db, action="external_bid_change")
    items = client.get("/api/naver/ad/change-log?action=external_bid_change").json()["items"]
    assert len(items) == 1
    assert items[0]["action"] == "external_bid_change"


def test_change_log_excludes_dry_run_by_default(client, db):
    """★기본값이 중요하다: 1층 '우리 조작 N회'는 실제 집행만 세야 한다.
    dry_run을 섞으면 아무것도 안 했는데 일한 것처럼 보인다(D-47-h 정직성)."""
    _seed(db, dry_run=True)
    _seed(db, dry_run=False)
    items = client.get("/api/naver/ad/change-log").json()["items"]
    assert len(items) == 1
    assert items[0]["dry_run"] is False

    all_items = client.get("/api/naver/ad/change-log?include_dry_run=true").json()["items"]
    assert len(all_items) == 2


def test_change_log_respects_days_window(client, db):
    _seed(db, days_ago=40)
    _seed(db, days_ago=2)
    items = client.get("/api/naver/ad/change-log?days=7").json()["items"]
    assert len(items) == 1


def test_change_log_limit_is_capped(client, db):
    r = client.get("/api/naver/ad/change-log?limit=99999")
    assert r.status_code == 422  # Query(le=500) 위반


def test_change_log_returns_total_for_pagination(client, db):
    for _ in range(3):
        _seed(db)
    body = client.get("/api/naver/ad/change-log?limit=2").json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


def test_change_log_empty_is_200_not_404(client):
    """★빈 상태는 에러가 아니다 — 1층이 '우리 조작 0회'를 정직하게 그려야 한다(D-47-h)."""
    body = client.get("/api/naver/ad/change-log").json()
    assert body == {"items": [], "total": 0}
