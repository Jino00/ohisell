# test_vendor_summary_http.py — Wing 세션 자동화 S2 라우터 HTTP 라운드트립 (TestClient, 격리 인메모리 DB)
# 토큰 인증·검증·ingest 저장·reconcile read-back·refresh 트리거를 HTTP 레이어로 검증.
# 스케줄러 lifespan은 TestClient를 context manager로 쓰지 않아 시작 안 됨(부작용 차단).
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.utils.kst import kst_today

_TOKEN = "rt-test-token-123"


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setenv("AD_INGEST_TOKEN", _TOKEN)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _yesterday() -> str:
    return (kst_today() - timedelta(days=1)).isoformat()


def test_ingest_requires_token(client):
    y = _yesterday()
    r = client.post("/api/coupang/ops/wing/vendor-summary/ingest", json={
        "account_key": "COUPANG_WING1",
        "days": [{"date": y, "registration_type": "NORMAL", "gmv": 1693230, "units_sold": 10}],
    })
    assert r.status_code == 401


def test_ingest_bad_account_key_rejected(client):
    y = _yesterday()
    r = client.post("/api/coupang/ops/wing/vendor-summary/ingest",
                    headers={"X-Ingest-Token": _TOKEN}, json={
                        "account_key": "TYPO",
                        "days": [{"date": y, "registration_type": "NORMAL", "gmv": 1, "units_sold": 1}],
                    })
    assert r.status_code == 400


def test_ingest_then_reconcile_roundtrip(client):
    """토큰 ingest → reconcile read-back: official GMV가 그대로 조회되고 드리프트 계산됨(우리 매출 0)."""
    y = _yesterday()
    r = client.post("/api/coupang/ops/wing/vendor-summary/ingest",
                    headers={"X-Ingest-Token": _TOKEN}, json={
                        "account_key": "COUPANG_WING1",
                        "days": [
                            {"date": y, "registration_type": "NORMAL", "gmv": 1693230, "units_sold": 10},
                            {"date": y, "registration_type": "RFM", "gmv": 1786500, "units_sold": 5},
                        ],
                    })
    assert r.status_code == 200, r.text
    assert r.json()["ingested"] == 2

    rr = client.get(f"/api/overview/revenue-reconcile?from={y}&to={y}&account=COUPANG_WING1")
    assert rr.status_code == 200, rr.text
    body = rr.json()
    assert body["has_official"] is True
    assert body["coverage"]["complete"] is True          # 단일 닫힌일·full 적재
    assert body["official"]["gmv_3p"] == 1693230
    assert body["official"]["gmv_rg"] == 1786500
    # Decimal은 문자열로 직렬화(_jsonify). 우리 매출 0 → 드리프트 -100%.
    assert body["ours"]["revenue_3p"] == "0"
    assert body["drift"]["pct_3p"] == "-100"


def test_reconcile_today_only_no_closed_days(client):
    today = kst_today().isoformat()
    rr = client.get(f"/api/overview/revenue-reconcile?from={today}&to={today}&account=COUPANG_WING1")
    assert rr.status_code == 200
    assert rr.json()["has_closed_days"] is False


def test_refresh_trigger_claim_flow(client):
    """request-refresh(무토큰 UI) → refresh-status requested=True → claim(토큰) → requested=False."""
    assert client.get("/api/coupang/ops/wing/vendor-summary/refresh-status").json()["requested"] is False
    assert client.post("/api/coupang/ops/wing/vendor-summary/request-refresh").status_code == 200
    assert client.get("/api/coupang/ops/wing/vendor-summary/refresh-status").json()["requested"] is True
    # claim은 토큰 필요
    assert client.post("/api/coupang/ops/wing/vendor-summary/refresh-claim").status_code == 401
    claimed = client.post("/api/coupang/ops/wing/vendor-summary/refresh-claim",
                          headers={"X-Ingest-Token": _TOKEN})
    assert claimed.status_code == 200 and claimed.json()["claimed"] is True
    assert client.get("/api/coupang/ops/wing/vendor-summary/refresh-status").json()["requested"] is False
