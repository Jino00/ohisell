# test_collection_status.py — 쿠팡 4스트림 수집 신선도 집계 SA 가드.
#   ★존재 이유: 자동 트리거 제거 후 '잊어버림→조용히 낡음'을 막는 유일한 안전장치가 이 상태다.
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.services.coupang.collection_status import (
    CRIT_HOURS,
    WARN_HOURS,
    compute_stream_state,
)

NOW = datetime(2026, 7, 19, 12, 0, 0)  # naive KST


def _iso(y, mo, d, h=12, mi=0):
    return datetime(y, mo, d, h, mi, 0).isoformat()


def test_fresh_within_warn():
    s = compute_stream_state(last_success_at=_iso(2026, 7, 19, 6), last_error_at=None, requested=False, now_kst=NOW)
    assert s["state"] == "fresh"
    assert 5.9 < s["age_hours"] < 6.1


def test_warn_between_24_and_48():
    s = compute_stream_state(last_success_at=_iso(2026, 7, 18, 6), last_error_at=None, requested=False, now_kst=NOW)
    assert s["state"] == "warn"  # 30h


def test_critical_over_48():
    s = compute_stream_state(last_success_at=_iso(2026, 7, 16, 6), last_error_at=None, requested=False, now_kst=NOW)
    assert s["state"] == "critical"  # 78h


# ── needs_login (2026-08-22 W1, 계약 CONTRACT_collection_stability_s1) ────────────
# ★배경: 로그인이 끊긴 상태에서 버튼을 누르면 requested=true라 종전 판정은 「수집 중」이
#   됐다. 그런데 실제로는 **사람이 로그인하기 전까지 영원히 진행되지 않는다** —
#   「수집 중」으로 보이는 동안 아무도 Mac 앞으로 가지 않은 것이 2026-08-22 사고의 형태다.

def test_needs_login_beats_in_flight():
    """★in_flight보다 앞선다. 이 순서가 뒤집히면 사고가 그대로 재현된다."""
    s = compute_stream_state(
        last_success_at=_iso(2026, 7, 19, 6), last_error_at=_iso(2026, 7, 19, 10),
        requested=True, now_kst=NOW, last_error_kind="login_required")
    assert s["state"] == "needs_login"


def test_needs_login_survives_request_extinction():
    """★요청이 소멸(login_required는 재시도 없이 소멸)한 뒤에도 계정은 여전히 잠겨 있다.

    그게 이 사실을 «이벤트»가 아니라 «상태»로 만든 이유다 — 버튼 결과 문구로 한 번
    스쳐 지나가면, 화면을 그 순간에 보지 않은 사람에겐 존재하지 않는 사실이 된다.
    """
    s = compute_stream_state(
        last_success_at=_iso(2026, 7, 19, 6), last_error_at=_iso(2026, 7, 19, 10),
        requested=False, now_kst=NOW, last_error_kind="login_required")
    assert s["state"] == "needs_login"


def test_needs_login_clears_once_a_later_success_lands():
    """성공이 실패보다 나중이면 이미 회복된 것 — 낡은 kind가 배너를 붙들면 안 된다."""
    s = compute_stream_state(
        last_success_at=_iso(2026, 7, 19, 11), last_error_at=_iso(2026, 7, 19, 10),
        requested=False, now_kst=NOW, last_error_kind="login_required")
    assert s["state"] == "fresh"


def test_other_kinds_do_not_become_needs_login():
    """reaper가 붙이는 no_response는 처방이 다르다 — 「Mac을 보라」지 「로그인하라」가 아니다."""
    s = compute_stream_state(
        last_success_at=_iso(2026, 7, 19, 6), last_error_at=_iso(2026, 7, 19, 10),
        requested=False, now_kst=NOW, last_error_kind="no_response")
    assert s["state"] == "failed"


def test_per_stream_thresholds_keep_weekly_streams_out_of_permanent_red():
    """★RG 정산은 주 단위다 — 24/48시간 임계면 상시 빨강이 되고, 상시 빨강은 아무도 안 본다."""
    args = dict(last_success_at=_iso(2026, 7, 16, 6), last_error_at=None,
                requested=False, now_kst=NOW)   # 78h 경과
    assert compute_stream_state(**args)["state"] == "critical"          # 기본 임계
    assert compute_stream_state(**args, warn_hours=24 * 9,
                                crit_hours=24 * 16)["state"] == "fresh"  # RG 임계


def test_never_succeeded_is_critical():
    s = compute_stream_state(last_success_at=None, last_error_at=None, requested=False, now_kst=NOW)
    assert s["state"] == "critical"
    assert s["age_hours"] is None


def test_failed_when_error_newer_than_success():
    s = compute_stream_state(last_success_at=_iso(2026, 7, 19, 6), last_error_at=_iso(2026, 7, 19, 10),
                             requested=False, now_kst=NOW)
    assert s["state"] == "failed"


def test_success_newer_than_error_not_failed():
    s = compute_stream_state(last_success_at=_iso(2026, 7, 19, 10), last_error_at=_iso(2026, 7, 19, 6),
                             requested=False, now_kst=NOW)
    assert s["state"] == "fresh"


def test_in_flight_takes_precedence():
    s = compute_stream_state(last_success_at=_iso(2026, 7, 16, 6), last_error_at=_iso(2026, 7, 19, 11),
                             requested=True, now_kst=NOW)
    assert s["state"] == "in_flight"


def test_tzaware_iso_treated_as_kst_no_9h_drift():
    # tz-aware 입력이 와도 KST로 해석 → 9시간 오차 없음(SQLite UTC 함정 방어).
    s = compute_stream_state(last_success_at="2026-07-19T06:00:00+09:00", last_error_at=None,
                             requested=False, now_kst=NOW)
    assert 5.9 < s["age_hours"] < 6.1


def test_boundary_exactly_24h_is_warn():
    s = compute_stream_state(last_success_at=_iso(2026, 7, 18, 12), last_error_at=None, requested=False, now_kst=NOW)
    assert s["state"] == "warn"  # 정확히 24h → warn(>= 경계는 warn 쪽)


def test_constants():
    assert WARN_HOURS == 24
    assert CRIT_HOURS == 48


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


def test_collection_status_route_shape(client):
    r = client.get("/api/coupang/ops/collection-status")
    assert r.status_code == 200
    body = r.json()
    assert "streams" in body and "as_of" in body
    keys = {s["key"] for s in body["streams"]}
    # ★RG 2큐 편입(2026-08-22 W1) — 종전엔 4스트림이라 RG가 전역 배너에 아예 안 떴다.
    assert keys == {"ofix_sales", "ofix_ad", "ohitech_ad", "supplier_hub",
                    "rg_wing1", "rg_wing2"}
    for s in body["streams"]:
        assert s["state"] in {"fresh", "warn", "critical", "failed", "in_flight",
                              "unknown", "needs_login"}
        # kind는 «왜»를 기계가 읽는 자리 — 키 자체가 사라지면 배너가 처방을 못 고른다.
        assert "last_error_kind" in s


# ── 적대리뷰 P1 회귀 가드 (2026-08-07) ────────────────────────────────
# ★존재 이유: 이 엔드포인트는 «낡음»의 단일 표면이 됐다(빨강 광고쿠키 배너에서 stale 갈래를
#   여기로 흡수). 그런데 getter를 감싸는 try가 없어서 넷 중 하나만 던지면 500이었고,
#   프론트는 조회 실패를 fail-safe로 삼켜 배너를 **아예 안 띄운다** → 낡아도 전면 실명.
#   이 프로젝트엔 그 실패 이력이 있다(마이그레이션 순서 → OperationalError → 경로 통째 침묵).
def test_one_stream_failure_does_not_kill_the_endpoint(client, monkeypatch):
    from app.services.coupang import collection_status as cs

    def _boom(_db):
        raise RuntimeError("no such column: coupang_wing_cookie.some_new_col")

    # supplier_hub 하나만 터뜨린다.
    patched = [
        (k, lb, _boom if k == "supplier_hub" else g) for (k, lb, g) in cs._STREAMS
    ]
    monkeypatch.setattr(cs, "_STREAMS", patched)

    r = client.get("/api/coupang/ops/collection-status")
    assert r.status_code == 200, "한 스트림의 예외가 엔드포인트를 죽이면 안 된다"
    body = r.json()
    by_key = {s["key"]: s for s in body["streams"]}

    # 죽은 스트림은 'unknown'으로 **드러난다**(숨기거나 fresh로 접지 않는다).
    assert by_key["supplier_hub"]["state"] == "unknown"
    assert by_key["supplier_hub"]["state"] != "fresh"
    assert "상태 조회 실패" in by_key["supplier_hub"]["last_error"]
    assert by_key["supplier_hub"]["age_hours"] is None

    # 나머지는 정상 판정이 살아 있다 — 이게 이 가드의 핵심이다.
    assert len(body["streams"]) == len(cs._STREAMS)
    for k in ("ofix_sales", "ofix_ad", "ohitech_ad", "rg_wing1", "rg_wing2"):
        assert by_key[k]["state"] != "unknown"
