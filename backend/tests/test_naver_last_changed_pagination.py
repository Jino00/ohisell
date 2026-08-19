# test_naver_last_changed_pagination.py — D-NAO-202
#
# 결함(2026-08-19 실사고): 네이버 `last-changed-statuses`는 한 번에 300건까지만 주고 나머지를
# `data.more`(moreFrom·moreSequence)로 알린다. 세 호출부가 전부 `more`를 무시해, 하루 변경이
# 300건을 넘은 2026-08-18에 20:30 이후 23건(상품매출 356,100원)이 조용히 사라졌다.
# 그날 sync_log는 네 회차 전부 'success'였다 — 그래서 「완주 여부」까지 함께 검증한다.
from __future__ import annotations

from datetime import date

import pytest

from app.clients.naver import NaverClient


class _Cfg:
    client_id = "cid"
    client_secret = "csec"


def _client() -> NaverClient:
    return NaverClient(_Cfg(), access_token="tok")


def _page(items: list[dict], more: dict | None = None) -> dict:
    data: dict = {"lastChangeStatuses": items, "count": len(items)}
    if more:
        data["more"] = more
    return {"data": data}


def _stub(client: NaverClient, pages: list[dict | None]) -> list[dict]:
    """_request를 페이지 목록으로 대체하고, 실제로 보낸 params를 기록해 돌려준다."""
    sent: list[dict] = []
    seq = list(pages)

    def fake_request(method, path, params=None):
        sent.append(dict(params or {}))
        return seq.pop(0) if seq else None

    client._request = fake_request  # type: ignore[method-assign]
    return sent


# ── 1. more 커서를 실제로 따라간다 (결함의 본체) ───────────────────────────────
def test_sweep_follows_more_cursor():
    c = _client()
    sent = _stub(c, [
        _page([{"productOrderId": f"A{i}"} for i in range(300)],
              more={"moreFrom": "2026-08-18T20:30:13.000+09:00", "moreSequence": "2026081881324561"}),
        _page([{"productOrderId": f"B{i}"} for i in range(36)]),
    ])
    items, complete = c._sweep_last_changed(date(2026, 8, 18))

    assert len(items) == 336, "1페이지 300건에서 멈추면 이 결함이 그대로 재발한다"
    assert complete is True
    assert len(sent) == 2
    # 2페이지 요청 규약: lastChangedTo 유지 + lastChangedFrom=moreFrom + moreSequence
    assert sent[1]["lastChangedFrom"] == "2026-08-18T20:30:13.000+09:00"
    assert sent[1]["moreSequence"] == "2026081881324561"
    assert sent[1]["lastChangedTo"] == "2026-08-18T23:59:59.999+09:00"


# ── 2. more가 없으면 1회로 끝난다 (정상일에 호출을 늘리지 않는다) ───────────────
def test_sweep_single_page_when_no_more():
    c = _client()
    sent = _stub(c, [_page([{"productOrderId": "A1"}])])
    items, complete = c._sweep_last_changed(date(2026, 8, 17))
    assert (len(items), complete, len(sent)) == (1, True, 1)


# ── 3. 이어받기 실패는 «완주»가 아니다 (교훈 #123) ─────────────────────────────
def test_sweep_incomplete_when_continuation_fails():
    c = _client()
    _stub(c, [
        _page([{"productOrderId": "A1"}],
              more={"moreFrom": "2026-08-18T20:30:13.000+09:00", "moreSequence": "S1"}),
        None,   # 2페이지 조회 실패
    ])
    items, complete = c._sweep_last_changed(date(2026, 8, 18))
    assert len(items) == 1
    assert complete is False, "남은 걸 알고도 못 받았으면 미완주다"


def test_sweep_incomplete_when_first_page_fails():
    c = _client()
    _stub(c, [None])
    items, complete = c._sweep_last_changed(date(2026, 8, 18))
    assert (items, complete) == ([], False)


# ── 4. 커서 정체 = 무한 루프. 진행이 없으면 끊는다 ─────────────────────────────
def test_sweep_breaks_on_stalled_cursor():
    c = _client()
    same = {"moreFrom": "2026-08-18T20:30:13.000+09:00", "moreSequence": "SAME"}
    sent = _stub(c, [_page([{"productOrderId": f"A{i}"}], more=same) for i in range(10)])
    items, complete = c._sweep_last_changed(date(2026, 8, 18))
    assert complete is False
    assert len(sent) == 2, "같은 커서를 다시 받으면 즉시 끊어야 한다"
    assert len(items) == 2


def test_sweep_stops_at_page_cap():
    c = _client()
    pages = [
        _page([{"productOrderId": f"A{i}"}], more={"moreFrom": f"2026-08-18T00:00:{i:02d}.000+09:00",
                                                   "moreSequence": f"S{i}"})
        for i in range(NaverClient.LAST_CHANGED_MAX_PAGES + 5)
    ]
    sent = _stub(c, pages)
    _items, complete = c._sweep_last_changed(date(2026, 8, 18))
    assert complete is False
    assert len(sent) == NaverClient.LAST_CHANGED_MAX_PAGES


# ── 5. fetch_orders가 스윕 결과를 계약대로 노출한다 ────────────────────────────
def test_fetch_orders_flags_incomplete_sweep(monkeypatch):
    c = _client()
    calls: list[date] = []

    def fake_sweep(day: date):
        calls.append(day)
        # 8/18만 미완주
        return [{"productOrderId": f"PO-{day.isoformat()}"}], day != date(2026, 8, 18)

    monkeypatch.setattr(c, "_sweep_last_changed", fake_sweep)
    monkeypatch.setattr(c, "_request_post", lambda *a, **k: {"data": []})
    monkeypatch.setattr("app.clients.naver.time.sleep", lambda *_: None)

    c.fetch_orders(date(2026, 8, 17), date(2026, 8, 19))

    assert calls == [date(2026, 8, 17), date(2026, 8, 18), date(2026, 8, 19)]
    assert c.last_fetch_complete is False
    assert c.last_sweep_incomplete_days == ["2026-08-18"]


def test_fetch_orders_complete_sweep_sets_flag_true(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_sweep_last_changed", lambda day: ([{"productOrderId": "P1"}], True))
    monkeypatch.setattr(c, "_request_post", lambda *a, **k: {"data": []})
    monkeypatch.setattr("app.clients.naver.time.sleep", lambda *_: None)

    c.fetch_orders(date(2026, 8, 18), date(2026, 8, 18))
    assert c.last_fetch_complete is True
    assert c.last_sweep_incomplete_days == []


def test_fetch_orders_resets_flags_between_calls(monkeypatch):
    """이전 호출의 미완주가 다음 호출에 눌어붙으면 «영원한 부분수집»으로 오독된다."""
    c = _client()
    monkeypatch.setattr("app.clients.naver.time.sleep", lambda *_: None)
    monkeypatch.setattr(c, "_request_post", lambda *a, **k: {"data": []})

    monkeypatch.setattr(c, "_sweep_last_changed", lambda day: ([], False))
    c.fetch_orders(date(2026, 8, 18), date(2026, 8, 18))
    assert c.last_fetch_complete is False

    monkeypatch.setattr(c, "_sweep_last_changed", lambda day: ([{"productOrderId": "P1"}], True))
    c.fetch_orders(date(2026, 8, 18), date(2026, 8, 18))
    assert c.last_fetch_complete is True
    assert c.last_sweep_incomplete_days == []


# ── 6. 나머지 두 호출부도 같은 스윕을 쓴다 (한 파일 수리 ≠ 규율 채택) ──────────
@pytest.mark.parametrize("method", ["fetch_pending_orders", "fetch_claims"])
def test_other_call_sites_use_paginated_sweep(monkeypatch, method):
    c = _client()
    used: list[date] = []

    def fake_sweep(day: date):
        used.append(day)
        return [], True

    monkeypatch.setattr(c, "_sweep_last_changed", fake_sweep)
    monkeypatch.setattr(c, "_request_post", lambda *a, **k: {"data": []})
    monkeypatch.setattr("app.clients.naver.time.sleep", lambda *_: None)
    # 이 두 경로가 _request를 직접 쓰면 여기서 터진다 = 스윕을 안 쓴다는 증거
    monkeypatch.setattr(
        c, "_request",
        lambda *a, **k: pytest.fail(f"{method}가 _sweep_last_changed를 우회했다"),
    )

    getattr(c, method)(days=3)
    assert len(used) == 3


# ── 7. 2단계 상세조회 실패도 «부분 수집»이다 (적대 리뷰 P1, 2026-08-19) ────────
#     1단계가 완주해도 여기서 조용히 넘어가면 청크당 최대 300건이 사라지는데
#     last_fetch_complete는 True로 남아 「전건 받았다」고 거짓 주장한다 — 8/18과 같은 모양.
def test_detail_chunk_failure_marks_incomplete(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_sweep_last_changed",
                        lambda day: ([{"productOrderId": "P1"}], True))
    monkeypatch.setattr(c, "_request_post", lambda *a, **k: None)   # 상세조회 지속 실패
    monkeypatch.setattr("app.clients.naver.time.sleep", lambda *_: None)

    out = c.fetch_orders(date(2026, 8, 18), date(2026, 8, 18))

    assert out == []
    assert c.last_fetch_complete is False, \
        "1단계만 보고 «전건 받았다»고 하면 안 된다 — 상세가 통째로 빠졌다"
    assert c.last_sweep_incomplete_days, "sync_log까지 닿을 좌표가 있어야 한다"
    assert any(d.startswith("detail-chunk[") for d in c.last_sweep_incomplete_days), \
        "접두사는 sync_service가 날짜/청크를 가르는 계약이다 — 느슨하게 두면 분류가 샌다"


def test_detail_chunk_success_keeps_complete(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_sweep_last_changed",
                        lambda day: ([{"productOrderId": "P1"}], True))
    monkeypatch.setattr(c, "_request_post", lambda *a, **k: {"data": []})
    monkeypatch.setattr("app.clients.naver.time.sleep", lambda *_: None)
    c.fetch_orders(date(2026, 8, 18), date(2026, 8, 18))
    assert c.last_fetch_complete is True
    assert c.last_sweep_incomplete_days == []


# ── 8. more는 있는데 커서 키가 결측이면 완주가 아니다 (적대 리뷰 P2) ───────────
@pytest.mark.parametrize("more", [
    {"moreFrom": "2026-08-18T20:30:13.000+09:00"},          # moreSequence 없음
    {"moreSequence": "S1"},                                  # moreFrom 없음
    {"moreFrom": "2026-08-18T20:30:13.000+09:00", "moreSequence": 0},  # 숫자 0
])
def test_sweep_incomplete_when_cursor_keys_missing(more):
    c = _client()
    _stub(c, [_page([{"productOrderId": "A1"}], more=more)])
    items, complete = c._sweep_last_changed(date(2026, 8, 18))
    assert len(items) == 1
    assert complete is False, "API가 «더 있다»고 말했으면 완주라고 하면 안 된다"


# ── 9. 나머지 두 호출부의 미완주가 반환값까지 온다 (적대 리뷰 P2) ──────────────
@pytest.mark.parametrize("method", ["fetch_pending_orders", "fetch_claims"])
def test_other_call_sites_return_incomplete_days(monkeypatch, method):
    c = _client()
    monkeypatch.setattr(c, "_sweep_last_changed", lambda day: ([], False))
    monkeypatch.setattr(c, "_request_post", lambda *a, **k: {"data": []})
    monkeypatch.setattr("app.clients.naver.time.sleep", lambda *_: None)
    out = getattr(c, method)(days=2)
    assert out["incomplete_days"], "로그만 남기면 화면은 «처리할 게 없다»로 읽는다"
    assert len(out["incomplete_days"]) == 2


# ── 10. pending/claims도 «상세조회 실패»를 미완주로 올린다 (2R 리뷰 P1) ─────────
#      fetch_orders만 고치고 옆 둘을 두면, 새로 넣은 incomplete_days가 바로 그 자리에서
#      「완주」를 거짓 주장한다 — 교훈 one-file-fixed-is-not-a-rule-adopted.
@pytest.mark.parametrize("method", ["fetch_pending_orders", "fetch_claims"])
def test_other_call_sites_flag_detail_chunk_failure(monkeypatch, method):
    c = _client()
    monkeypatch.setattr(
        c, "_sweep_last_changed",
        lambda day: ([{"productOrderId": "P1", "claimStatus": "CANCEL_REQUEST"}], True),
    )
    monkeypatch.setattr(c, "_request_post", lambda *a, **k: None)   # 상세조회 지속 실패
    monkeypatch.setattr("app.clients.naver.time.sleep", lambda *_: None)

    out = getattr(c, method)(days=1)

    assert out["incomplete_days"], \
        "스윕은 완주했어도 상세가 통째로 빠졌으면 «완주»라고 하면 안 된다"
    assert any("detail-chunk" in d for d in out["incomplete_days"])


# ── 11. `detail-chunk` 접두사는 두 파일에 걸친 계약이다 (3R 리뷰 P2) ────────────
#      naver.py가 생산하고 sync_service.py가 startswith로 분류한다. 표식을 델타 직전
#      이름(`detail[`)으로 되돌리면 sync_service가 그걸 «날짜»로 읽어
#      "변경상태 스윕 미완주 1일: detail[0:300]"을 찍는다 — P2-3이 없애려던 오독의 부활.
#      한 파일만 보는 테스트로는 원리적으로 못 잡으므로 계약을 여기서 묶는다.
def test_detail_chunk_prefix_is_a_cross_file_contract(monkeypatch):
    from app.services.sync_service import _DETAIL_CHUNK_PREFIX

    c = _client()
    monkeypatch.setattr(c, "_sweep_last_changed", lambda day: ([{"productOrderId": "P1"}], True))
    monkeypatch.setattr(c, "_request_post", lambda *a, **k: None)
    monkeypatch.setattr("app.clients.naver.time.sleep", lambda *_: None)
    c.fetch_orders(date(2026, 8, 18), date(2026, 8, 18))

    produced = c.last_sweep_incomplete_days
    assert produced
    for mark in produced:
        assert mark.startswith(_DETAIL_CHUNK_PREFIX), (
            f"생산자({mark!r})와 분류자({_DETAIL_CHUNK_PREFIX!r})의 접두사가 갈라졌다"
        )
