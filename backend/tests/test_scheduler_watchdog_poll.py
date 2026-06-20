# test_scheduler_watchdog_poll.py — Mac 워치독 폴러 순수 로직 (S5b S5).
#   ① _problem_keys: health 응답 → 문제 키 추출(healthy면 빈 목록)
#   ② _summarize: 집계 단일 알림 문자열
#   ③ _maybe_notify: 디바운스(같은 문제 억제)·해소 prune(재발 시 재알림)
# 알림(osascript)은 monkeypatch로 가로채 호출 횟수만 검증(원칙22, I/O 없이 경계 검증).
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

import scheduler_watchdog_poll as wp  # noqa: E402


# ── _problem_keys ──────────────────────────────────────────────────────────
def test_problem_keys_healthy_is_empty():
    h = {"healthy": True, "scheduler_running": True, "missing_jobs": [],
         "failed": [], "stale": [], "never_succeeded": []}
    assert wp._problem_keys(h) == []


def test_problem_keys_collects_all_buckets():
    h = {
        "scheduler_running": False,
        "missing_jobs": ["a"],
        "failed": [{"job_name": "b"}],
        "stale": [{"job_name": "c"}],
        "never_succeeded": [{"job_name": "d"}],
    }
    keys = set(wp._problem_keys(h))
    assert keys == {"scheduler_down", "missing:a", "failed:b", "stale:c", "never_succeeded:d"}


# ── _summarize ─────────────────────────────────────────────────────────────
def test_summarize_groups_by_kind():
    s = wp._summarize(["stale:c", "stale:c2", "failed:b", "scheduler_down"])
    assert "지연(stale): c, c2" in s
    assert "실패: b" in s
    assert "스케줄러 정지" in s


# ── _maybe_notify: 디바운스 + 집계 단일 알림 ───────────────────────────────
def test_maybe_notify_fires_once_then_debounces(monkeypatch):
    calls = []
    monkeypatch.setattr(wp, "_notify_mac", lambda *a, **k: calls.append(a))
    state = {"_debounce_s": 100, "problems": {}}
    keys = ["stale:x", "failed:y"]
    # 첫 호출: 단일 알림 1회(집계).
    wp._maybe_notify(keys, state, now=1000.0, title="t")
    assert len(calls) == 1
    # 같은 문제 디바운스 창 내 재호출: 알림 없음.
    wp._maybe_notify(keys, state, now=1050.0, title="t")
    assert len(calls) == 1
    # 디바운스 창 경과 후: 다시 1회.
    wp._maybe_notify(keys, state, now=1200.0, title="t")
    assert len(calls) == 2


def test_maybe_notify_prunes_resolved_then_realerts(monkeypatch):
    calls = []
    monkeypatch.setattr(wp, "_notify_mac", lambda *a, **k: calls.append(a))
    state = {"_debounce_s": 100, "problems": {}}
    wp._maybe_notify(["stale:x"], state, now=1000.0, title="t")
    assert len(calls) == 1
    # 문제 해소(목록에서 사라짐) → 상태에서 prune.
    wp._maybe_notify([], state, now=1010.0, title="t")
    assert "stale:x" not in state["problems"]
    # 디바운스 창 내라도 재발 시 즉시 재알림(prune됐으므로 last=0).
    wp._maybe_notify(["stale:x"], state, now=1020.0, title="t")
    assert len(calls) == 2


def test_maybe_notify_aggregates_multiple_into_single_call(monkeypatch):
    calls = []
    monkeypatch.setattr(wp, "_notify_mac", lambda *a, **k: calls.append(a))
    state = {"_debounce_s": 100, "problems": {}}
    wp._maybe_notify(["stale:x", "stale:y", "failed:z"], state, now=1.0, title="t")
    # 3개 문제 → 알림 1회(집계 단일 알림), 메시지에 3개 모두 포함.
    assert len(calls) == 1
    msg = calls[0][1]
    assert "x" in msg and "y" in msg and "z" in msg
