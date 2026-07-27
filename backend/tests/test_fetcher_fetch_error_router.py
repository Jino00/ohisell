# test_fetcher_fetch_error_router.py — 페처 4종의 실패 보고 엔드포인트.
#   ★claim의 짝: 페처가 요청을 claim한 뒤 죽으면 플래그는 이미 clear라 흔적이 0이었다.
#   성공은 ingest/fetch-success가 알리고(last_success_at), 실패는 이 엔드포인트가 알린다.
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
from app.services.coupang import (
    ohitech_ad_sync,
    rg_settlement_sync,
    rocket_supplier_sync,
    vendor_summary_sync,
)

_TOKEN = "test-token-123"

# (URL, account_key) — 4종. URL은 형제 fetch-success/refresh-claim 옆에 붙인다.
_ROUTES = [
    pytest.param("/api/coupang/ops/wing/vendor-summary/fetch-error",
                 vendor_summary_sync._VS_ACCOUNT, id="wing_vendor_summary"),
    pytest.param("/api/coupang/ops/wing/rg-settlement/fetch-error",
                 rg_settlement_sync._RG_STATE_ACCOUNT, id="rg_settlement"),
    pytest.param("/api/coupang/ops/rocket/ad-cost/fetch-error",
                 ohitech_ad_sync._OHITECH_AD_ACCOUNT, id="ohitech_ad"),
    pytest.param("/api/coupang/ops/rocket/fetch-error",
                 rocket_supplier_sync._ROCKET_ACCOUNT, id="rocket_supplier"),
]


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
    for p in _ROUTES:
        seed.add(CoupangWingCookie(account_key=p.values[1], status="unknown"))
    seed.commit()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


def _row(seed, account_key: str) -> CoupangWingCookie:
    seed.expire_all()
    return seed.query(CoupangWingCookie).filter_by(account_key=account_key).one()


@pytest.mark.parametrize("url,account", _ROUTES)
def test_reports_error_with_valid_token(env, url, account):
    client, seed = env
    r = client.post(url, json={"error": "browser: Target page ... has been closed"},
                    headers={"X-Ingest-Token": _TOKEN})
    assert r.status_code == 200

    row = _row(seed, account)
    assert "browser" in row.last_error
    assert row.last_error_at is not None
    assert row.status == "unknown"  # 쿠키 status는 안 건드림 — red면 배너가 헛된 재설정을 시킨다


@pytest.mark.parametrize("url,account", _ROUTES)
def test_rejects_without_token(env, url, account):
    client, seed = env
    r = client.post(url, json={"error": "browser closed"})
    assert r.status_code == 401
    assert _row(seed, account).last_error is None  # 인증 실패는 아무것도 안 쓴다


@pytest.mark.parametrize("url,account", _ROUTES)
def test_rejects_wrong_token(env, url, account):
    client, seed = env
    r = client.post(url, json={"error": "x"}, headers={"X-Ingest-Token": "wrong"})
    assert r.status_code == 401
    assert _row(seed, account).last_error is None


@pytest.mark.parametrize("url,account", _ROUTES)
def test_missing_error_field_records_unknown(env, url, account):
    """페처가 메시지 없이 보고해도 '실패했다'는 사실 자체는 남는다(빈 문자열로 지워지지 않음)."""
    client, seed = env
    r = client.post(url, json={}, headers={"X-Ingest-Token": _TOKEN})
    assert r.status_code == 200

    row = _row(seed, account)
    assert row.last_error == "unknown"
    assert row.last_error_at is not None


# ══════════════════════════════════════════════════════════════════════
# RG 정산 fetch-success — '업로드 0건 완주'(정산주기 없음) 전용 성공 신호 (codex R4 [P2])
#   정상 성공은 upload-xlsx(rg_mark_heartbeat)가 알린다. 업로드가 0건인 회차만 이 신호가
#   필요하다: 침묵하면 UI 215초 헛기다림('Mac 응답 없음'), fetch-error로 알리면 완주한
#   무작업이 ❌ 실패로 렌더된다(프론트는 last_error_at 변화를 전부 실패로 읽는다).
# ══════════════════════════════════════════════════════════════════════
_RG_SUCCESS_URL = "/api/coupang/ops/wing/rg-settlement/fetch-success"


def test_rg_fetch_success_moves_heartbeat(env):
    """토큰 있으면 200 + last_success_at 기록(=프론트 폴링이 ✅로 종료되는 유일 경로)."""
    client, seed = env
    r = client.post(_RG_SUCCESS_URL, headers={"X-Ingest-Token": _TOKEN})
    assert r.status_code == 200

    row = _row(seed, rg_settlement_sync._RG_STATE_ACCOUNT)
    assert row.last_success_at is not None
    assert row.status == "green"


def test_rg_fetch_success_clears_previous_error(env):
    """직전 실패 흔적을 지운다 — 안 지우면 성공/실패 판정 순서에 기대는 화면이 오래된 ❌를 남긴다."""
    client, seed = env
    assert client.post("/api/coupang/ops/wing/rg-settlement/fetch-error",
                       json={"error": "chrome crash"},
                       headers={"X-Ingest-Token": _TOKEN}).status_code == 200
    assert client.post(_RG_SUCCESS_URL, headers={"X-Ingest-Token": _TOKEN}).status_code == 200

    row = _row(seed, rg_settlement_sync._RG_STATE_ACCOUNT)
    assert row.last_error is None and row.last_error_at is None
    assert row.last_success_at is not None


def test_rg_fetch_success_rejects_without_token(env):
    client, seed = env
    r = client.post(_RG_SUCCESS_URL)
    assert r.status_code == 401
    assert _row(seed, rg_settlement_sync._RG_STATE_ACCOUNT).last_success_at is None


def test_rg_fetch_success_rejects_wrong_token(env):
    client, seed = env
    r = client.post(_RG_SUCCESS_URL, headers={"X-Ingest-Token": "wrong"})
    assert r.status_code == 401
    assert _row(seed, rg_settlement_sync._RG_STATE_ACCOUNT).last_success_at is None


def test_rg_fetch_success_is_per_account(env):
    """WING2 완주가 WING1을 '갱신 완료'로 위장하지 못한다(계정별 상태행 — 큐 격리와 같은 불변식)."""
    client, seed = env
    r = client.post(_RG_SUCCESS_URL, params={"account_key": "COUPANG_WING2"},
                    headers={"X-Ingest-Token": _TOKEN})
    assert r.status_code == 200

    seed.expire_all()
    w2 = seed.query(CoupangWingCookie).filter_by(
        account_key=rg_settlement_sync._RG_STATE_ACCOUNT_BY_ACCOUNT["COUPANG_WING2"]).one()
    assert w2.last_success_at is not None
    assert _row(seed, rg_settlement_sync._RG_STATE_ACCOUNT).last_success_at is None


def test_rg_fetch_success_rejects_unknown_account(env):
    """RG_ACCOUNTS 밖 account_key는 400 — 오타로 유령 상태행이 생기지 않게."""
    client, seed = env
    r = client.post(_RG_SUCCESS_URL, params={"account_key": "COUPANG_WING9"},
                    headers={"X-Ingest-Token": _TOKEN})
    assert r.status_code == 400
    assert _row(seed, rg_settlement_sync._RG_STATE_ACCOUNT).last_success_at is None
