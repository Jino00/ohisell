# test_dashboard_sync_cooldown.py — 조회 시 동기화 쿨다운 (2026-08-05)
#
# 왜 있나: `/channel-breakdown`은 조회할 때마다 외부 API로 주문·광고비를 재동기화해
#   라이브에서 **63.7초**가 걸렸다(같은 창 kpi 0.11초·product-ranking 0.12초).
#   기간을 바꾸거나 매출 축 토글을 누를 때마다 1분씩 멈춘다.
from __future__ import annotations

import threading

import pytest

from app.routers import dashboard as dash


@pytest.fixture(autouse=True)
def _clean():
    dash._reset_sync_cooldown()
    yield
    dash._reset_sync_cooldown()


def test_first_call_syncs_and_second_is_skipped():
    assert dash._sync_due("orders:a:b") is True
    assert dash._sync_due("orders:a:b") is False


def test_different_windows_do_not_block_each_other(monkeypatch):
    """기간을 바꾸면 그 창은 새로 동기화된다 — 앞 창의 쿨다운에 막히면 '왜 안 바뀌지'가 된다."""
    assert dash._sync_due("orders:2026-08-01:2026-08-04") is True
    assert dash._sync_due("orders:2026-07-01:2026-07-31") is True   # 다른 창
    assert dash._sync_due("orders:2026-08-01:2026-08-04") is False  # 같은 창


def test_orders_and_adcost_are_tracked_separately():
    assert dash._sync_due("orders:x") is True
    assert dash._sync_due("adcost:x") is True


def test_cooldown_expires(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(dash.time, "monotonic", lambda: t[0])
    assert dash._sync_due("k") is True
    t[0] += dash._SYNC_COOLDOWN_SEC - 1
    assert dash._sync_due("k") is False
    t[0] += 2
    assert dash._sync_due("k") is True


def test_never_synced_is_not_confused_with_time_zero(monkeypatch):
    """★단조시계가 0에 가까운 순간(부팅 직후)에도 '한 번도 안 함'이 스킵으로 읽히면 안 된다.

    2026-08-04 Wing 폴 데몬이 정확히 이 형태로 재부팅 후 1시간 동안 조용히 죽었다
    (last=0.0 초기값을 monotonic과 비교). 여기선 키 부재로 표현해 그 함정을 피한다.
    """
    monkeypatch.setattr(dash.time, "monotonic", lambda: 0.0)
    assert dash._sync_due("fresh-boot") is True


def test_concurrent_callers_collapse_into_one_sync():
    """★브라우저 탭 두 개가 동시에 열리면 둘 다 63초 동기화를 시작하던 것을 락이 하나로 접는다."""
    results: list[bool] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(dash._sync_due("same-key"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == 1, f"동시 호출 중 {sum(results)}개가 통과했다(1이어야 한다)"
