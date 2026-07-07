# test_naver_ad_diagnosis_router.py — GET /api/naver/ad/diagnosis HTTP 라운드트립 (TestClient)
# 목적: SA/harness 단위 테스트(test_naver_ad_diagnosis.py)는 라우터 코드를 안 거치므로
#   라우터 레이어 자체의 버그(예: 정의 안 된 상수 참조로 인한 500)를 못 잡는다 — 2026-07-07
#   prod 배포 중 실제로 이런 500을 겪은 뒤 추가(원칙22: 라이브 증거로만 "됐다" 판단).
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


@pytest.fixture
def client():
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
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_diagnosis_endpoint_returns_200_with_defaults(client):
    resp = client.get("/api/naver/ad/diagnosis")
    assert resp.status_code == 200
    body = resp.json()
    assert "window" in body
    assert "correction_factor" in body


def test_diagnosis_endpoint_returns_200_with_explicit_range(client):
    resp = client.get("/api/naver/ad/diagnosis", params={
        "date_from": "2026-06-23", "date_to": "2026-07-07",
    })
    assert resp.status_code == 200


def test_diagnosis_endpoint_rejects_range_over_max(client):
    resp = client.get("/api/naver/ad/diagnosis", params={
        "date_from": "2026-01-01", "date_to": "2026-07-07",
    })
    assert resp.status_code == 400


def test_diagnosis_endpoint_rejects_inverted_range(client):
    resp = client.get("/api/naver/ad/diagnosis", params={
        "date_from": "2026-07-07", "date_to": "2026-06-23",
    })
    assert resp.status_code == 400
