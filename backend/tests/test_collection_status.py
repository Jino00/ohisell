# test_collection_status.py — 쿠팡 4스트림 수집 신선도 집계 SA 가드.
#   ★존재 이유: 자동 트리거 제거 후 '잊어버림→조용히 낡음'을 막는 유일한 안전장치가 이 상태다.
from datetime import datetime

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
