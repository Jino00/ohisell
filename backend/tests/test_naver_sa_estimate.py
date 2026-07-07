# test_naver_sa_estimate.py — P2-S3 T1 estimate 함수(fetcher) 단위테스트
# 라이브 스펙 실측 근거: docs/references/23_naver_sa_estimate_recon.md
from __future__ import annotations

import pytest

from app.services import naver_sa_ad_fetcher as fetcher


class FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")


def test_estimate_average_position_bid_single_batch(monkeypatch):
    captured = []

    def fake_post(path, body):
        captured.append((path, body))
        return FakeResp(200, {"device": "MOBILE", "estimate": [
            {"keyword": "오하이", "position": 1, "nccKeywordId": "nkw-1", "bid": 920},
        ]})

    monkeypatch.setattr(fetcher, "_estimate_post", fake_post)

    out = fetcher.estimate_average_position_bid("MOBILE", [{"key": "nkw-1", "position": 1}])
    assert out == [{"keyword": "오하이", "position": 1, "nccKeywordId": "nkw-1", "bid": 920}]
    assert captured[0][0] == "/estimate/average-position-bid/id"
    assert captured[0][1] == {"device": "MOBILE", "items": [{"key": "nkw-1", "position": 1}]}


def test_estimate_average_position_bid_chunks_over_200(monkeypatch):
    """실측 상한 200/콜 — 250건 요청 시 2개 배치(200+50)로 자동 분할, 무언 truncation 없이 전량 처리."""
    calls = []

    def fake_post(path, body):
        calls.append(len(body["items"]))
        return FakeResp(200, {"estimate": [{"bid": 1}] * len(body["items"])})

    monkeypatch.setattr(fetcher, "_estimate_post", fake_post)

    items = [{"key": f"nkw-{i}", "position": 1} for i in range(250)]
    out = fetcher.estimate_average_position_bid("MOBILE", items)
    assert calls == [200, 50]
    assert len(out) == 250


def test_estimate_average_position_bid_empty_items_no_call(monkeypatch):
    def fake_post(path, body):
        raise AssertionError("빈 items는 호출하면 안 됨")

    monkeypatch.setattr(fetcher, "_estimate_post", fake_post)
    assert fetcher.estimate_average_position_bid("MOBILE", []) == []


def test_estimate_performance_uses_bulk_endpoint_with_keyword_text(monkeypatch):
    captured = []

    def fake_post(path, body):
        captured.append((path, body))
        return FakeResp(200, {"items": [
            {"keyword": "오하이", "bid": 190, "device": "MOBILE", "keywordplus": False,
             "clicks": 13, "impressions": 521, "cost": 1892},
        ]})

    monkeypatch.setattr(fetcher, "_estimate_post", fake_post)

    out = fetcher.estimate_performance([
        {"keyword": "오하이", "bid": 190, "keywordplus": False, "device": "MOBILE"},
    ])
    assert out[0]["clicks"] == 13
    assert "conv_cnt" not in out[0] and "conv_amt" not in out[0]  # 전환 아님 확인
    assert captured[0][0] == "/estimate/performance-bulk"


def test_estimate_performance_chunks_over_200(monkeypatch):
    calls = []

    def fake_post(path, body):
        calls.append(len(body["items"]))
        return FakeResp(200, {"items": [{"clicks": 1}] * len(body["items"])})

    monkeypatch.setattr(fetcher, "_estimate_post", fake_post)

    items = [{"keyword": f"kw{i}", "bid": 100, "keywordplus": False, "device": "MOBILE"} for i in range(400)]
    out = fetcher.estimate_performance(items)
    assert calls == [200, 200]
    assert len(out) == 400


def test_estimate_post_raises_without_credentials(monkeypatch):
    monkeypatch.delenv("NAVER_SA_ACCESS_LICENSE", raising=False)
    monkeypatch.delenv("NAVER_SA_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError):
        fetcher._estimate_post("/estimate/average-position-bid/id", {"device": "MOBILE", "items": []})
