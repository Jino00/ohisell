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
    lease = claim_fn(seed)["lease"]

    r = c.post(url, json={"error": "세션 만료", "kind": "login_required", "lease": lease},
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


_RG_COMPLETE = "/api/coupang/ops/wing/rg-settlement/refresh-complete"


def test_rg_refresh_complete_extinguishes_request(client):
    """RG run이 '받을 게 없어' 업로드 0건으로 정상 완주한 회차 — 요청은 여기서 소멸해야 한다.

    ★없으면: 성공 heartbeat(upload-xlsx)가 안 찍혀 요청이 남고, 창을 3번 더 띄운 뒤
    "재시도 3회 소진"이라는 거짓 실패로 끝난다.
    ★last_success_at(데이터 신선도 시계)은 건드리지 않는다 — 받은 게 없으니 데이터는 그대로.
    ★lease는 필수다(2026-08-03) — 아래 test_rg_refresh_complete_requires_lease 참조.
    """
    c, seed = client
    rg_settlement_sync.rg_request_refresh(seed)
    lease = rg_settlement_sync.rg_claim_refresh(seed)["lease"]

    assert c.post(_RG_COMPLETE).status_code == 401              # 토큰 필요(형제와 동일 규칙)
    assert c.post(_RG_COMPLETE, json={"lease": lease},
                  headers={"X-Ingest-Token": _TOKEN}).status_code == 200

    seed.expire_all()
    row = _row(seed, rg_settlement_sync._RG_STATE_ACCOUNT)
    assert row.refresh_requested_at is None
    assert row.last_success_at is None                          # 신선도 시계는 불변


def test_rg_refresh_complete_requires_lease(client):
    """★2026-08-03 codex 3R[P1]: lease 없는 완료 신호는 남의 요청을 지운다 — 거부해야 한다.

    재현(인터리빙): run A가 완료 POST를 지연시킨 채 끝나고 → 사용자가 다시 눌러 요청 B가
    생기고 → 늦은 A의 POST가 도착한다. lease가 없으면 '지금 유효한 임대'를 대조할 수단이
    없어 무조건 B의 요청을 지운다. 그러면 프론트(streamRefresh.ts의 `!requested → done`)가
    **시작도 안 한 B를 '완료'로 오보**한다.
    ★lease를 붙였는데 옛 임대인 경우(=stale)는 기존 가드가 이미 접는다 — 여기선 200 no-op.
    """
    c, seed = client
    acc = rg_settlement_sync._RG_STATE_ACCOUNT

    # ── run A: 임대까지 받은 뒤 요청이 소멸한 상태(reaper 또는 자기 완료로 이미 닫힘) ──
    rg_settlement_sync.rg_request_refresh(seed)
    lease_a = rg_settlement_sync.rg_claim_refresh(seed)["lease"]
    assert lease_a is not None
    row = _row(seed, acc)
    row.refresh_requested_at = None      # A의 요청은 이미 사라졌다
    row.claimed_at = None
    row.attempt_count = 0
    seed.commit()

    # ── 사용자가 다시 누름 → 요청 B 생성 + 데몬이 B를 임대 ──
    rg_settlement_sync.rg_request_refresh(seed)
    lease_b = rg_settlement_sync.rg_claim_refresh(seed)["lease"]
    assert lease_b is not None and lease_b != lease_a
    requested_b = _row(seed, acc).refresh_requested_at

    # ── 늦은 A의 완료 POST ──
    r = c.post(_RG_COMPLETE, headers={"X-Ingest-Token": _TOKEN})       # lease 없음
    assert r.status_code == 400                                        # 계약 위반 = 거부
    seed.expire_all()
    assert _row(seed, acc).refresh_requested_at == requested_b         # ★B의 요청 생존

    r = c.post(_RG_COMPLETE, json={"lease": lease_a},
               headers={"X-Ingest-Token": _TOKEN})                     # 옛 임대 = stale
    assert r.status_code == 200 and r.json()["ok"] is False
    seed.expire_all()
    assert _row(seed, acc).refresh_requested_at == requested_b         # ★여전히 생존

    # ── B 자신의 완료만이 B를 닫는다 ──
    assert c.post(_RG_COMPLETE, json={"lease": lease_b},
                  headers={"X-Ingest-Token": _TOKEN}).json()["ok"] is True
    seed.expire_all()
    assert _row(seed, acc).refresh_requested_at is None


@pytest.mark.parametrize("_, acc, request_fn, claim_fn, status_fn, error_fn, success_fn, url",
                         STREAMS, ids=IDS)
def test_claim_always_returns_lease(db, _, acc, request_fn, claim_fn, status_fn,
                                    error_fn, success_fn, url):
    """claimed=true면 lease도 반드시 온다 — 페처가 완료·실패 보고에 붙일 유일한 식별자다.

    ★없으면 무슨 일이 나나: 페처는 lease 없이 완료를 부르고 → 라우터가 400으로 거부하고 →
    요청이 임대된 채 TTL 20분을 묵히다 재시도되어 끝내 '재시도 3회 소진'이라는 거짓 실패로
    끝난다. 즉 lease 필수화의 안전성은 이 성질에 걸려 있다.
    """
    request_fn(db)
    claimed = claim_fn(db)
    assert claimed["claimed"] is True
    assert claimed["lease"] == _row(db, acc).claimed_at.isoformat()


@pytest.mark.parametrize("_, acc, request_fn, claim_fn, status_fn, error_fn, success_fn, url",
                         STREAMS, ids=IDS)
def test_router_without_kind_keeps_request_for_retry(client, _, acc, request_fn, claim_fn,
                                                     status_fn, error_fn, success_fn, url):
    """하위호환 — kind 없는 보고는 평범한 실패(=재시도 대상)로 다룬다.

    ★kind는 여전히 옵션이다(구버전 페처 하위호환). lease만 필수로 바뀌었다 —
    kind는 "어떤 실패인가"를 말할 뿐이지만 lease는 "누구의 회차인가"를 말하기 때문이다.
    """
    c, seed = client
    request_fn(seed)
    lease = claim_fn(seed)["lease"]

    r = c.post(url, json={"error": "browser closed", "lease": lease},
               headers={"X-Ingest-Token": _TOKEN})
    assert r.status_code == 200

    seed.expire_all()
    row = _row(seed, acc)
    assert row.refresh_requested_at is not None   # 요청 보존
    assert row.claimed_at is None                 # lease 반납 → 다음 폴에서 재claim


@pytest.mark.parametrize("_, acc, request_fn, claim_fn, status_fn, error_fn, success_fn, url",
                         STREAMS, ids=IDS)
def test_fetch_error_requires_lease(client, _, acc, request_fn, claim_fn, status_fn,
                                    error_fn, success_fn, url):
    """★2026-08-03 slice 2: lease 없는 실패 보고는 남의 요청을 죽인다 — 5스트림 전부 거부.

    완료 신호(refresh-complete)와 **같은 구멍**이다. report_failure는 lease가 있으면 stale
    가드를 돌지만 없으면 통째로 건너뛴다. 그리고 reason이 잡히는 실패
    (login_required/access_denied/mapping_broken/attempt>=MAX)는 **요청을 소멸시킨다** —
    즉 늦게 도착한 run A의 lease 없는 login_required 보고 하나가 사용자의 새 요청 B를 죽이고,
    프론트는 "새 실패 없이 요청만 사라졌다 = 정상 종료"로 읽어 시작도 안 한 B를 done으로 오보한다.

    ★거부해도 실패가 조용해지지 않는다: 보고가 거부된 회차는 "데몬이 보고 없이 죽었다"로
    퇴화하고, 그건 lease 계약이 이미 처리한다(TTL 만료 → reaper → last_error에
    "재시도 3회 소진 — 마지막 시도가 보고 없이 종료"). 가시성이 사라지는 게 아니라 늦어질 뿐이다.
    """
    c, seed = client

    # ── run A: 임대까지 받았고, 그 뒤 A의 요청은 이미 소멸한 상태 ──
    request_fn(seed)
    lease_a = claim_fn(seed)["lease"]
    assert lease_a is not None
    row = _row(seed, acc)
    row.refresh_requested_at = None
    row.claimed_at = None
    row.attempt_count = 0
    seed.commit()

    # ── 사용자가 다시 누름 → 요청 B → 데몬이 B를 임대 ──
    request_fn(seed)
    lease_b = claim_fn(seed)["lease"]
    assert lease_b is not None and lease_b != lease_a
    requested_b = _row(seed, acc).refresh_requested_at

    # ── 늦은 A의 lease 없는 실패 보고(가장 파괴적인 kind) ──
    r = c.post(url, json={"error": "세션 만료", "kind": "login_required"},
               headers={"X-Ingest-Token": _TOKEN})
    assert r.status_code == 400
    seed.expire_all()
    row = _row(seed, acc)
    assert row.refresh_requested_at == requested_b   # ★B의 요청 생존
    assert row.claimed_at is not None                # ★B의 임대도 반납되지 않음

    # ── 옛 임대를 실은 보고(stale)도 B를 건드리지 못한다(기존 가드) ──
    r = c.post(url, json={"error": "세션 만료", "kind": "login_required", "lease": lease_a},
               headers={"X-Ingest-Token": _TOKEN})
    assert r.status_code == 200
    seed.expire_all()
    assert _row(seed, acc).refresh_requested_at == requested_b

    # ── B 자신의 보고만이 B를 닫는다 ──
    r = c.post(url, json={"error": "세션 만료", "kind": "login_required", "lease": lease_b},
               headers={"X-Ingest-Token": _TOKEN})
    assert r.status_code == 200
    seed.expire_all()
    assert _row(seed, acc).refresh_requested_at is None
