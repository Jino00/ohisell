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


# ── RG 정산 자동 다운로드 (S4-P2) ──
def test_rg_settlement_upload_requires_token(client):
    """회계(net_profit 소스) 변경 엔드포인트 → 토큰 없으면 401(파일 파싱 전 차단)."""
    r = client.post(
        "/api/coupang/ops/rg/settlement/upload-xlsx",
        files={"file": ("A01564720-WAREHOUSING_SHIPPING-ko-x.xlsx", b"dummy",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 401


def test_rg_settlement_upload_roundtrip_offloads_to_threadpool(client):
    """업로드 성공 경로 HTTP 왕복 — 파싱·적재 결과가 응답에 실려 오는지.

    ★이 테스트가 필요한 이유(2026-07-17): 엔드포인트가 `async def`라 동기 ingest가 이벤트 루프를
    막았고(단일 워커 = 그동안 모든 API 정지), 수정으로 run_in_threadpool 오프로드를 넣었다.
    오프로드는 **DB 세션을 워커 스레드에서 쓰게 만든다** → SQLite `check_same_thread` 설정이
    틀리면 여기서만 터진다(파서 단위 테스트로는 절대 안 잡힘).
    prod 엔진도 connect_args={"check_same_thread": False}라 동일 조건(app/database.py).
    """
    from tests.test_rg_settlement_sync import _build_xlsx, _WH_ROWS

    content = _build_xlsx([("입출고비", "입출고비", _WH_ROWS)])
    r = client.post(
        "/api/coupang/ops/rg/settlement/upload-xlsx",
        params={"account_key": "COUPANG_WING1"},   # 파일명 vendor_id 없음 → 명시 account_key 사용
        files={"file": ("WAREHOUSING_SHIPPING-ko-x.xlsx", content,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers={"X-Ingest-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["upserted"] == 2               # 고유 옵션 2개
    assert body["account_key"] == "COUPANG_WING1"


def test_rg_settlement_upload_corrupt_file_returns_422(client):
    """손상 파일 → 500 아닌 422(WingReadError 매핑). 스레드풀 오프로드 후에도 예외가 전파되는지 확인."""
    r = client.post(
        "/api/coupang/ops/rg/settlement/upload-xlsx",
        params={"account_key": "COUPANG_WING1"},
        files={"file": ("WAREHOUSING_SHIPPING-ko-x.xlsx", b"not-a-real-xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers={"X-Ingest-Token": _TOKEN},
    )
    assert r.status_code == 422, r.text


def test_rg_settlement_refresh_trigger_claim_flow(client):
    """request-refresh(무토큰 UI) → status requested=True → claim(토큰) → requested=False. (vendor-summary 미러)"""
    base = "/api/coupang/ops/wing/rg-settlement"
    assert client.get(f"{base}/refresh-status").json()["requested"] is False
    assert client.post(f"{base}/request-refresh").status_code == 200
    assert client.get(f"{base}/refresh-status").json()["requested"] is True
    assert client.post(f"{base}/refresh-claim").status_code == 401          # 토큰 필요
    claimed = client.post(f"{base}/refresh-claim", headers={"X-Ingest-Token": _TOKEN})
    assert claimed.status_code == 200 and claimed.json()["claimed"] is True
    assert client.get(f"{base}/refresh-status").json()["requested"] is False
