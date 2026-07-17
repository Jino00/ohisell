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


def test_rg_settlement_upload_roundtrip_survives_cross_thread_session(client):
    """업로드 성공 경로 HTTP 왕복 — 파싱·적재 결과가 응답에 실려 오는지.

    ★이 테스트가 필요한 이유(2026-07-17): run_in_threadpool 오프로드는 **DB 세션을 워커
    스레드에서 쓰게 만든다** → SQLite `check_same_thread` 설정이 틀리면 여기서만 터진다
    (파서 단위 테스트로는 절대 안 잡힘). prod 엔진도 connect_args={"check_same_thread": False}
    라 동일 조건(app/database.py).

    ⚠️이 테스트는 "오프로드가 됐는지"는 검증하지 **않는다**(오프로드를 지워도 통과) —
    그건 아래 test_rg_settlement_upload_runs_ingest_off_the_event_loop 담당. (codex P2 반영)
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


def test_rg_settlement_upload_runs_ingest_off_the_event_loop(client, monkeypatch):
    """★사고 재발 방지의 핵심 가드: 동기 ingest가 이벤트 루프 위에서 돌면 실패한다.

    2026-07-17 사고의 절반은 파싱 O(n²)였고, 나머지 절반이 이것이다 — 업로드 엔드포인트가
    `async def`인데 동기 ingest를 그냥 호출해서 **이벤트 루프를 점유**했다. prod는
    uvicorn --workers 1이라 그동안 모든 API가 멈춘다(같은 시각 fetch-error POST 15초 타임아웃).

    판정 방법(타이밍·스레드 이름 대신 결정론적 신호): 이벤트 루프 스레드에서 돌면
    asyncio.get_running_loop()이 루프를 반환하고, 스레드풀 워커에서 돌면 RuntimeError가 난다.
    → 워커에서 돌았다 == 루프가 안 잡힌다.

    (codex P2: 기존 왕복 테스트는 run_in_threadpool을 지워도 통과해서 이 퇴행을 못 잡았다.)
    """
    import asyncio

    from app.services.coupang import rg_settlement_sync as _rs
    from tests.test_rg_settlement_sync import _build_xlsx, _WH_ROWS

    seen: dict[str, bool] = {}
    _real = _rs.ingest_settlement_xlsx

    def _spy(db, account_key, content):
        try:
            asyncio.get_running_loop()
            seen["on_event_loop"] = True      # 루프 스레드 = 오프로드 안 됨 = 퇴행
        except RuntimeError:
            seen["on_event_loop"] = False     # 워커 스레드 = 정상
        return _real(db, account_key, content)

    monkeypatch.setattr(_rs, "ingest_settlement_xlsx", _spy)

    r = client.post(
        "/api/coupang/ops/rg/settlement/upload-xlsx",
        params={"account_key": "COUPANG_WING1"},
        files={"file": ("WAREHOUSING_SHIPPING-ko-x.xlsx",
                        _build_xlsx([("입출고비", "입출고비", _WH_ROWS)]),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers={"X-Ingest-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text
    assert seen.get("on_event_loop") is False, (
        "동기 ingest가 이벤트 루프에서 실행됨 — run_in_threadpool 오프로드가 빠졌다."
        " 단일 워커(prod)에서 업로드 내내 모든 API가 멈춘다."
    )


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


# ── 층1: RG status/api 계정 단위 인제스트 (Mac 상주 브라우저, 쿠키 이관) ──
_STATUS = "/api/coupang/ops/wing/rg-settlement/ingest-status"


def test_rg_ingest_status_requires_token(client):
    """회계(net_profit 소스) 변경 → 토큰 없으면 401(파싱 전 차단)."""
    r = client.post(f"{_STATUS}?account_key=COUPANG_WING1", json={"settlementStatusReports": []})
    assert r.status_code == 401


def test_rg_ingest_status_bad_account_key_rejected(client):
    """RG_ACCOUNTS 아닌 account_key → 400."""
    r = client.post(f"{_STATUS}?account_key=TYPO",
                    headers={"X-Ingest-Token": _TOKEN}, json={"settlementStatusReports": []})
    assert r.status_code == 400


def test_rg_ingest_status_parse_error_returns_422(client):
    """스키마 드리프트 raw → WingReadError → 500 아닌 422."""
    r = client.post(f"{_STATUS}?account_key=COUPANG_WING1",
                    headers={"X-Ingest-Token": _TOKEN}, json={"unexpected_key": []})
    assert r.status_code == 422, r.text


def test_rg_ingest_status_roundtrip(client):
    """토큰 + 유효 raw → 200, 계정 단위 7행 적재 결과가 응답에 실려 온다."""
    from tests.test_rg_settlement_sync import _FIXTURE_SINGLE

    r = client.post(f"{_STATUS}?account_key=COUPANG_WING1",
                    headers={"X-Ingest-Token": _TOKEN}, json=_FIXTURE_SINGLE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"synced": 7, "account_key": "COUPANG_WING1", "status": "ok"}
