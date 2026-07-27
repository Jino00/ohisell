# test_refresh_lease_streams.py — 5개 스트림이 모두 같은 lease 계약을 쓴다(라우터 경계 포함).
#   ★스트림마다 코드가 복제돼 있던 것이 2026-07-17 실사고의 배경이다(claim 즉시 소비 4곳).
#   여기서는 "공용 SA로 모였는가"를 스트림별로 같은 시나리오로 훑는다 — 하나만 빠져도 잡힌다.
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
    ad_cost_sync,
    ohitech_ad_sync,
    refresh_contract as rc,
    rg_settlement_sync,
    rocket_supplier_sync,
    vendor_summary_sync,
)

_TOKEN = "test-token-123"


def _rg_success(db) -> None:
    """RG run 전체 성공 = 업로드 heartbeat + refresh-complete(요청 소멸)."""
    rg_settlement_sync.rg_mark_heartbeat(db)
    rc.mark_success(db, rg_settlement_sync._RG_STATE_ACCOUNT)

# (id, account_key, request, claim, status, error, success, fetch-error URL)
STREAMS = [
    (
        "vendor_summary", vendor_summary_sync._VS_ACCOUNT,
        vendor_summary_sync.request_refresh, vendor_summary_sync.claim_refresh,
        vendor_summary_sync.refresh_status, vendor_summary_sync.mark_fetch_error,
        vendor_summary_sync._mark_heartbeat,
        "/api/coupang/ops/wing/vendor-summary/fetch-error",
    ),
    (
        "rg_settlement", rg_settlement_sync._RG_STATE_ACCOUNT,
        rg_settlement_sync.rg_request_refresh, rg_settlement_sync.rg_claim_refresh,
        rg_settlement_sync.rg_refresh_status, rg_settlement_sync.rg_mark_fetch_error,
        # RG의 "성공"은 업로드 heartbeat + run 종료 신호(refresh-complete)의 합이다 —
        # 업로드 하나만으로 요청을 지우면 나머지 엑셀 실패를 재시도할 수 없다(codex 1R[P1]).
        _rg_success,
        "/api/coupang/ops/wing/rg-settlement/fetch-error",
    ),
    (
        "ad_cost", ad_cost_sync._ADS_ACCOUNT,
        ad_cost_sync.request_refresh, ad_cost_sync.claim_refresh,
        ad_cost_sync.refresh_status, ad_cost_sync.mark_fetch_error,
        lambda db: ad_cost_sync._mark_cookie(db, status="green", error=None, success=True),
        "/api/coupang/ops/ad-cost/fetch-error",
    ),
    (
        "ohitech_ad", ohitech_ad_sync._OHITECH_AD_ACCOUNT,
        ohitech_ad_sync.request_refresh, ohitech_ad_sync.claim_refresh,
        ohitech_ad_sync.refresh_status, ohitech_ad_sync.mark_fetch_error,
        ohitech_ad_sync.mark_fetch_success,
        "/api/coupang/ops/rocket/ad-cost/fetch-error",
    ),
    (
        "rocket_supplier", rocket_supplier_sync._ROCKET_ACCOUNT,
        rocket_supplier_sync.request_rocket_refresh, rocket_supplier_sync.claim_rocket_refresh,
        rocket_supplier_sync.rocket_refresh_status, rocket_supplier_sync.mark_rocket_fetch_error,
        rocket_supplier_sync.mark_rocket_fetch_success,
        "/api/coupang/ops/rocket/fetch-error",
    ),
]
IDS = [s[0] for s in STREAMS]


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    yield s
    s.close()


def _row(db, acc):
    return db.query(CoupangWingCookie).filter(CoupangWingCookie.account_key == acc).first()


@pytest.mark.parametrize("_, acc, request_fn, claim_fn, status_fn, error_fn, success_fn, url",
                         STREAMS, ids=IDS)
def test_claim_does_not_consume_request(db, _, acc, request_fn, claim_fn, status_fn,
                                        error_fn, success_fn, url):
    """★계약의 핵심 — claim 후에도 요청은 살아있다(예전엔 여기서 유실됐다)."""
    request_fn(db)
    assert claim_fn(db)["claimed"] is True
    assert status_fn(db)["requested"] is True
    assert _row(db, acc).claimed_at is not None


@pytest.mark.parametrize("_, acc, request_fn, claim_fn, status_fn, error_fn, success_fn, url",
                         STREAMS, ids=IDS)
def test_failure_then_retry_then_success(db, _, acc, request_fn, claim_fn, status_fn,
                                         error_fn, success_fn, url):
    """실패 → 재claim(재시도) → 성공 시 요청 소멸. 버튼 1회로 두 번 시도된다."""
    request_fn(db)
    claim_fn(db)
    error_fn(db, "browser closed")
    assert status_fn(db)["requested"] is True          # 재시도 대기(UI엔 여전히 진행 중)

    assert claim_fn(db)["claimed"] is True             # 2회차
    success_fn(db)
    assert status_fn(db)["requested"] is False         # 성공만이 요청을 소멸시킨다
    assert claim_fn(db)["claimed"] is False


@pytest.mark.parametrize("_, acc, request_fn, claim_fn, status_fn, error_fn, success_fn, url",
                         STREAMS, ids=IDS)
def test_three_failures_extinguish(db, _, acc, request_fn, claim_fn, status_fn,
                                   error_fn, success_fn, url):
    request_fn(db)
    for _i in range(rc.MAX_ATTEMPTS):
        assert claim_fn(db)["claimed"] is True
        error_fn(db, "browser closed")
    st = status_fn(db)
    assert st["requested"] is False
    assert "재시도" in (st["last_error"] or "")


@pytest.mark.parametrize("_, acc, request_fn, claim_fn, status_fn, error_fn, success_fn, url",
                         STREAMS, ids=IDS)
def test_login_required_is_not_retried(db, _, acc, request_fn, claim_fn, status_fn,
                                       error_fn, success_fn, url):
    """§0 금지선 — 로그인 필요는 창만 반복해서 뜨므로 재시도하지 않는다."""
    request_fn(db)
    claim_fn(db)
    error_fn(db, "세션 만료", kind=rc.KIND_LOGIN_REQUIRED)

    st = status_fn(db)
    assert st["requested"] is False
    assert "로그인 필요" in (st["last_error"] or "")
    assert claim_fn(db)["claimed"] is False


@pytest.mark.parametrize("_, acc, request_fn, claim_fn, status_fn, error_fn, success_fn, url",
                         STREAMS, ids=IDS)
def test_status_exposes_contract_fields(db, _, acc, request_fn, claim_fn, status_fn,
                                        error_fn, success_fn, url):
    """행이 없어도(초기) 계약 필드가 빠지지 않는다 — UI/진단이 KeyError로 깨지지 않도록."""
    empty = status_fn(db)
    assert empty["attempt_count"] == 0 and empty["in_flight"] is False

    request_fn(db)
    claim_fn(db)
    st = status_fn(db)
    assert st["attempt_count"] == 1 and st["in_flight"] is True


# ── 라우터 경계: fetch-error가 kind를 받아 계약에 전달하는가 ──
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AD_INGEST_TOKEN", _TOKEN)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    seed = TestingSession()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


@pytest.mark.parametrize("_, acc, request_fn, claim_fn, status_fn, error_fn, success_fn, url",
                         STREAMS, ids=IDS)
def test_router_passes_login_required_kind(client, _, acc, request_fn, claim_fn, status_fn,
                                           error_fn, success_fn, url):
    c, seed = client
    request_fn(seed)
    claim_fn(seed)

    r = c.post(url, json={"error": "세션 만료", "kind": "login_required"},
               headers={"X-Ingest-Token": _TOKEN})
    assert r.status_code == 200

    seed.expire_all()
    assert _row(seed, acc).refresh_requested_at is None   # 재시도 없이 소멸


def test_rg_upload_heartbeat_alone_keeps_request(client):
    """★codex 1R[P1]: RG는 (정산주기×리포트종류) 여러 엑셀을 올린다 — 첫 업로드가 요청을
    지우면 뒤 엑셀이 실패해도 재시도할 요청이 없어 정산이 반쪽으로 남는다.
    """
    _c, seed = client
    rg_settlement_sync.rg_request_refresh(seed)
    rg_settlement_sync.rg_claim_refresh(seed)
    rg_settlement_sync.rg_mark_heartbeat(seed)      # 첫 엑셀 업로드 성공

    row = _row(seed, rg_settlement_sync._RG_STATE_ACCOUNT)
    assert row.last_success_at is not None           # 신선도는 올라간다
    assert row.refresh_requested_at is not None      # 요청은 run이 끝날 때까지 살아있다

    # 뒤 엑셀이 실패하면 재시도가 가능해야 한다
    rg_settlement_sync.rg_mark_fetch_error(seed, "두 번째 리포트 다운로드 실패")
    assert rg_settlement_sync.rg_claim_refresh(seed)["claimed"] is True


def test_rg_refresh_complete_extinguishes_request(client):
    """RG run이 '받을 게 없어' 업로드 0건으로 정상 완주한 회차 — 요청은 여기서 소멸해야 한다.

    ★없으면: 성공 heartbeat(upload-xlsx)가 안 찍혀 요청이 남고, 창을 3번 더 띄운 뒤
    "재시도 3회 소진"이라는 거짓 실패로 끝난다.
    ★last_success_at(데이터 신선도 시계)은 건드리지 않는다 — 받은 게 없으니 데이터는 그대로.
    """
    c, seed = client
    url = "/api/coupang/ops/wing/rg-settlement/refresh-complete"
    rg_settlement_sync.rg_request_refresh(seed)
    rg_settlement_sync.rg_claim_refresh(seed)

    assert c.post(url).status_code == 401                      # 토큰 필요(형제와 동일 규칙)
    assert c.post(url, headers={"X-Ingest-Token": _TOKEN}).status_code == 200

    seed.expire_all()
    row = _row(seed, rg_settlement_sync._RG_STATE_ACCOUNT)
    assert row.refresh_requested_at is None
    assert row.last_success_at is None                          # 신선도 시계는 불변


@pytest.mark.parametrize("_, acc, request_fn, claim_fn, status_fn, error_fn, success_fn, url",
                         STREAMS, ids=IDS)
def test_router_without_kind_keeps_request_for_retry(client, _, acc, request_fn, claim_fn,
                                                     status_fn, error_fn, success_fn, url):
    """하위호환 — kind 없는 구버전 페처 보고는 평범한 실패(=재시도 대상)로 다룬다."""
    c, seed = client
    request_fn(seed)
    claim_fn(seed)

    r = c.post(url, json={"error": "browser closed"}, headers={"X-Ingest-Token": _TOKEN})
    assert r.status_code == 200

    seed.expire_all()
    row = _row(seed, acc)
    assert row.refresh_requested_at is not None   # 요청 보존
    assert row.claimed_at is None                 # lease 반납 → 다음 폴에서 재claim
