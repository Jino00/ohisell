# test_ad_cost_fetch_error_router.py — POST /api/coupang/ops/ad-cost/fetch-error (페처 실패 보고).
#   ★claim의 짝: 페처가 요청을 claim한 뒤 죽으면 플래그는 이미 clear라 흔적이 0이었다.
#   토큰 인증은 형제 refresh-claim과 동일 규칙(미설정·불일치 모두 401 — 서버 상태 비노출).
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import CoupangWingCookie
from app.services.coupang import ad_cost_sync

_URL = "/api/coupang/ops/ad-cost/fetch-error"
_TOKEN = "test-token-123"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AD_INGEST_TOKEN", _TOKEN)
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
    seed = TestingSession()
    seed.add(CoupangWingCookie(account_key=ad_cost_sync._ADS_ACCOUNT, cookie_blob="x", status="unknown"))
    seed.commit()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


def test_reports_error_with_valid_token(env):
    client, seed = env
    r = client.post(_URL, json={"error": "browser: Target page ... has been closed"},
                    headers={"X-Ingest-Token": _TOKEN})
    assert r.status_code == 200

    seed.expire_all()
    row = seed.query(CoupangWingCookie).first()
    assert "browser" in row.last_error
    assert row.last_error_at is not None
    assert row.status == "red"


def test_rejects_without_token(env):
    client, seed = env
    r = client.post(_URL, json={"error": "browser closed"})
    assert r.status_code == 401

    seed.expire_all()
    assert seed.query(CoupangWingCookie).first().last_error is None  # 인증 실패는 아무것도 안 쓴다


def test_rejects_wrong_token(env):
    client, _ = env
    r = client.post(_URL, json={"error": "x"}, headers={"X-Ingest-Token": "wrong"})
    assert r.status_code == 401


def test_missing_error_field_records_unknown(env):
    """페처가 메시지 없이 보고해도 '실패했다'는 사실 자체는 남는다(빈 문자열로 지워지지 않음)."""
    client, seed = env
    r = client.post(_URL, json={}, headers={"X-Ingest-Token": _TOKEN})
    assert r.status_code == 200

    seed.expire_all()
    row = seed.query(CoupangWingCookie).first()
    assert row.last_error == "unknown"
    assert row.last_error_at is not None
