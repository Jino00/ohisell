# test_naver_load_window.py — 적재 창 레지스트리 (D-NAO-193, ref 72 §4 채택 ①)
# 이 모듈은 순수 함수뿐이라 DB 없이 검증한다. 지키려는 것은 «판정 함수가 자기 입력의 적재 창을
# 모른 채 오늘을 읽는 일»이다(2026-08-18 probe_revert 실사고).
from __future__ import annotations

from datetime import date

import pytest

from app.services.naver_ad import load_window

TODAY = date(2026, 8, 18)


def test_naver_ad_daily_today_is_outside_window():
    """★사고의 모양 그대로 — naver_ad_daily는 D−1까지다(라이브 2026-08-18 MAX=08-17)."""
    assert load_window.max_loaded_date("naver_ad_daily", TODAY) == date(2026, 8, 17)
    assert load_window.is_loaded("naver_ad_daily", TODAY, TODAY) is False
    assert load_window.is_loaded("naver_ad_daily", date(2026, 8, 17), TODAY) is True


def test_require_loaded_raises_with_reader_and_window_in_message():
    with pytest.raises(load_window.LoadWindowError) as ei:
        load_window.require_loaded("naver_ad_daily", TODAY, TODAY, reader="probe_revert._x")
    msg = str(ei.value)
    assert "probe_revert._x" in msg      # 어디서 읽었는지
    assert "2026-08-17" in msg           # 실제 상한
    assert "행 없음" in msg               # «0이 아니라 행이 없다»는 오독 지점을 명시


def test_hourly_today_and_orders_cover_today():
    for table in ("naver_adgroup_hourly_today", "orders"):
        assert load_window.is_loaded(table, TODAY, TODAY) is True
        load_window.require_loaded(table, TODAY, TODAY, reader="t")  # 예외 없음


def test_unregistered_table_fails_closed():
    """미등재 테이블은 조용히 통과시키지 않는다 — 등재를 강제한다(fail-closed)."""
    with pytest.raises(load_window.LoadWindowError) as ei:
        load_window.require_loaded("naver_새테이블", TODAY, TODAY, reader="t")
    assert "미등재" in str(ei.value)


def test_registered_tables_are_documented_and_sorted():
    tables = load_window.registered_tables()
    assert tables == tuple(sorted(tables))
    assert "naver_ad_daily" in tables and "naver_adgroup_hourly_today" in tables
    for t in tables:
        assert load_window.load_lag_days(t) >= 0
