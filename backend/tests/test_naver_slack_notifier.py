# test_naver_slack_notifier.py — P2-S3 T4 slack_notifier 단위테스트
from __future__ import annotations

import pytest

from app.services.naver_ad import slack_notifier


class FakeResp:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text

    def json(self):
        if self._json_body is None:
            raise ValueError("no json")
        return self._json_body


PROPOSALS = [
    {"proposal_type": "bid_down", "target_id": "nkw-1"},
    {"proposal_type": "bid_down", "target_id": "nkw-2"},
    {"proposal_type": "negative_keyword", "target_id": "term-1"},
]


def test_notify_no_webhook_configured_is_noop(monkeypatch):
    monkeypatch.delenv("NAVER_SLACK_WEBHOOK_URL", raising=False)
    out = slack_notifier.notify(PROPOSALS)
    assert out == {"sent": False, "reason": "no_webhook", "slack_ts": None, "proposal_count": 3}


def test_notify_no_proposals_is_noop_even_with_webhook(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("호출되면 안 됨 — 제안 0건은 발송 스킵")
    monkeypatch.setattr(slack_notifier.requests, "post", fail)
    out = slack_notifier.notify([], webhook_url="https://hooks.example/xyz")
    assert out == {"sent": False, "reason": "no_proposals", "slack_ts": None, "proposal_count": 0}


def test_notify_success_no_ts_in_response_is_still_success(monkeypatch):
    """실측 성격: incoming webhook은 보통 'ok' 텍스트만 반환, JSON도 ts도 없음 — 그래도 sent=True."""
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResp(status_code=200, json_body=None, text="ok")

    monkeypatch.setattr(slack_notifier.requests, "post", fake_post)
    out = slack_notifier.notify(PROPOSALS, webhook_url="https://hooks.example/xyz")
    assert out["sent"] is True
    assert out["slack_ts"] is None  # best-effort — 없어도 실패 아님
    assert out["proposal_count"] == 3
    assert "bid_down: 2건" in captured["json"]["text"]
    assert "negative_keyword: 1건" in captured["json"]["text"]


def test_notify_success_with_ts_extracted(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        return FakeResp(status_code=200, json_body={"ok": True, "ts": "1234.5678"})

    monkeypatch.setattr(slack_notifier.requests, "post", fake_post)
    out = slack_notifier.notify(PROPOSALS, webhook_url="https://hooks.example/xyz")
    assert out["sent"] is True
    assert out["slack_ts"] == "1234.5678"


def test_notify_retries_then_gives_up_on_persistent_5xx(monkeypatch):
    calls = []
    sleeps = []

    def fake_post(url, json=None, timeout=None):
        calls.append(1)
        return FakeResp(status_code=500, text="server error")

    monkeypatch.setattr(slack_notifier.requests, "post", fake_post)
    monkeypatch.setattr(slack_notifier.time, "sleep", lambda s: sleeps.append(s))

    out = slack_notifier.notify(PROPOSALS, webhook_url="https://hooks.example/xyz")
    assert out["sent"] is False
    assert "500" in out["reason"]
    assert len(calls) == slack_notifier._MAX_RETRIES
    assert len(sleeps) == slack_notifier._MAX_RETRIES - 1  # 마지막 시도 후엔 sleep 안 함


def test_notify_retries_on_network_exception(monkeypatch):
    import requests as requests_module

    def fake_post(url, json=None, timeout=None):
        raise requests_module.ConnectionError("boom")

    monkeypatch.setattr(slack_notifier.requests, "post", fake_post)
    monkeypatch.setattr(slack_notifier.time, "sleep", lambda s: None)

    out = slack_notifier.notify(PROPOSALS, webhook_url="https://hooks.example/xyz")
    assert out["sent"] is False
    assert "boom" in out["reason"]


def test_notify_uses_env_webhook_when_arg_not_passed(monkeypatch):
    monkeypatch.setenv("NAVER_SLACK_WEBHOOK_URL", "https://hooks.example/from-env")
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        return FakeResp(status_code=200, json_body={"ok": True})

    monkeypatch.setattr(slack_notifier.requests, "post", fake_post)
    out = slack_notifier.notify(PROPOSALS)
    assert out["sent"] is True
    assert captured["url"] == "https://hooks.example/from-env"
