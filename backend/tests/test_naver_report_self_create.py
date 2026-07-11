# test_naver_report_self_create.py — 네이버 stat-report 자체생성 전환 TDD (docs/PLAN_naver-report-self-create.md)
# 커버: create_stat_report(POST 바디·서명), _json_list(빈/공백 바디 graceful),
#   ensure_reports_built(dedup·pending재사용·생성+폴링·timeout·생성실패·자격증명없음),
#   fetch_ad_performance_daily가 list 전에 ensure_reports_built를 호출하는 배선.
from __future__ import annotations

from datetime import date

import pytest

from app.services import naver_sa_ad_fetcher as fetcher


class FakeResp:
    """requests.Response 흉내(status_code·text·json()·raise_for_status())."""

    def __init__(self, *, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


# ── create_stat_report ──
def test_create_stat_report_posts_body_and_signs_with_post_method(monkeypatch):
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "eA==")

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return FakeResp(status_code=200, text='{"reportJobId":123,"status":"REGIST"}',
                         json_data={"reportJobId": 123, "status": "REGIST"})

    monkeypatch.setattr(fetcher.requests, "post", fake_post)

    out = fetcher.create_stat_report("AD", date(2026, 7, 10))

    assert captured["url"] == fetcher.BASE_URL + "/stat-reports"
    assert captured["json"] == {"reportTp": "AD", "statDt": "20260710"}
    # 서명 문자열은 method=POST로 만들어져야 함(GET으로 서명하면 403 — 실측)
    assert "X-Signature" in captured["headers"]
    assert out == {"reportJobId": 123, "status": "REGIST"}


def test_create_expkeyword_report_is_thin_wrapper(monkeypatch):
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "eA==")

    calls = []

    def fake_create_stat_report(report_tp, stat_date):
        calls.append((report_tp, stat_date))
        return {"reportJobId": 999, "status": "REGIST"}

    monkeypatch.setattr(fetcher, "create_stat_report", fake_create_stat_report)

    out = fetcher.create_expkeyword_report(date(2026, 7, 10))
    assert calls == [("EXPKEYWORD", date(2026, 7, 10))]
    assert out == {"reportJobId": 999, "status": "REGIST"}


# ── _json_list ──
def test_json_list_empty_body_returns_empty_list():
    assert fetcher._json_list(FakeResp(status_code=200, text="")) == []


def test_json_list_whitespace_body_returns_empty_list():
    assert fetcher._json_list(FakeResp(status_code=200, text="   \n  ")) == []


def test_json_list_normal_body_parses_json():
    data = [{"reportTp": "AD", "status": "BUILT"}]
    resp = FakeResp(status_code=200, text='[{"reportTp": "AD", "status": "BUILT"}]', json_data=data)
    assert fetcher._json_list(resp) == data


def test_list_ad_reports_survives_empty_body(monkeypatch):
    """회귀 재현: /stat-reports가 200+빈바디를 반환해도 크래시하지 않고 빈 목록."""
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: FakeResp(status_code=200, text=""))
    out = fetcher.list_ad_reports(date(2026, 7, 10), date(2026, 7, 10))
    assert out == []


def test_list_reports_by_type_survives_empty_body(monkeypatch):
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: FakeResp(status_code=200, text=""))
    out = fetcher._list_reports_by_type("AD_CONVERSION", date(2026, 7, 10), date(2026, 7, 10))
    assert out == []


def test_list_report_jobs_survives_empty_body(monkeypatch):
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: FakeResp(status_code=200, text=""))
    out = fetcher.list_report_jobs("AD")
    assert out == []


# ── ensure_reports_built ──
def _no_sleep(monkeypatch):
    """폴링 테스트가 느려지지 않도록 time.sleep을 무력화."""
    monkeypatch.setattr(fetcher.time, "sleep", lambda s: None)


def test_ensure_reports_built_no_credentials_is_noop(monkeypatch):
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "")

    def boom(*a, **k):
        raise AssertionError("자격증명 없을 때는 list_report_jobs를 호출하면 안 된다")

    monkeypatch.setattr(fetcher, "list_report_jobs", boom)
    fetcher.ensure_reports_built("AD", date(2026, 7, 10), date(2026, 7, 10))  # 예외 없이 반환


def test_ensure_reports_built_creates_and_polls_until_built(monkeypatch):
    """(a) 미존재 → 생성 + 폴링 BUILT."""
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")
    _no_sleep(monkeypatch)

    monkeypatch.setattr(fetcher, "list_report_jobs", lambda tp: [])  # 기존 잡 없음

    created = {}

    def fake_create(report_tp, stat_date):
        created["report_tp"] = report_tp
        created["stat_date"] = stat_date
        return {"reportJobId": 555, "status": "REGIST"}

    monkeypatch.setattr(fetcher, "create_stat_report", fake_create)

    poll_statuses = iter(["REGIST", "RUNNING", "BUILT"])
    polled_ids = []

    def fake_get_report_job(job_id):
        polled_ids.append(job_id)
        return {"reportJobId": job_id, "status": next(poll_statuses)}

    monkeypatch.setattr(fetcher, "get_report_job", fake_get_report_job)

    fetcher.ensure_reports_built("AD", date(2026, 7, 10), date(2026, 7, 10), timeout=5.0, poll_interval=0.01)

    assert created == {"report_tp": "AD", "stat_date": date(2026, 7, 10)}
    assert polled_ids == [555, 555, 555]


def test_ensure_reports_built_skips_creation_when_already_built(monkeypatch):
    """(b) 기존 BUILT → 생성 안 함(dedup), 폴링도 안 함."""
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")

    monkeypatch.setattr(fetcher, "list_report_jobs", lambda tp: [
        {"reportTp": tp, "status": "BUILT", "reportJobId": 1, "statDt": "2026-07-10T00:00:00Z"},
    ])

    def boom_create(*a, **k):
        raise AssertionError("이미 BUILT인 날짜는 생성 호출하면 안 된다")

    def boom_poll(*a, **k):
        raise AssertionError("이미 BUILT인 날짜는 폴링 호출하면 안 된다")

    monkeypatch.setattr(fetcher, "create_stat_report", boom_create)
    monkeypatch.setattr(fetcher, "get_report_job", boom_poll)

    fetcher.ensure_reports_built("AD", date(2026, 7, 10), date(2026, 7, 10))


def test_ensure_reports_built_reuses_pending_job_without_recreating(monkeypatch):
    """(c) 기존 pending(REGIST/RUNNING) → 생성 안 하고 그 잡을 폴링."""
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")
    _no_sleep(monkeypatch)

    monkeypatch.setattr(fetcher, "list_report_jobs", lambda tp: [
        {"reportTp": tp, "status": "REGIST", "reportJobId": 777, "statDt": "2026-07-10T00:00:00Z"},
    ])

    def boom_create(*a, **k):
        raise AssertionError("pending 잡이 있으면 재생성하면 안 된다")

    monkeypatch.setattr(fetcher, "create_stat_report", boom_create)

    polled_ids = []
    poll_statuses = iter(["RUNNING", "BUILT"])

    def fake_get_report_job(job_id):
        polled_ids.append(job_id)
        return {"reportJobId": job_id, "status": next(poll_statuses)}

    monkeypatch.setattr(fetcher, "get_report_job", fake_get_report_job)

    fetcher.ensure_reports_built("AD", date(2026, 7, 10), date(2026, 7, 10), timeout=5.0, poll_interval=0.01)
    assert polled_ids == [777, 777]


def test_ensure_reports_built_recreates_after_terminal_error_job(monkeypatch):
    """(c') codex[P2] 회귀: 기존 잡이 터미널 ERROR면 그 id를 재사용하지 않고 새로 생성해
    복구한다. ERROR id를 재사용하면 _poll_until_built가 즉시 False → 영구 skip이었음."""
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")
    _no_sleep(monkeypatch)

    monkeypatch.setattr(fetcher, "list_report_jobs", lambda tp: [
        {"reportTp": tp, "status": "ERROR", "reportJobId": 111, "statDt": "2026-07-10T00:00:00Z"},
    ])

    created = {}

    def fake_create(report_tp, stat_date):
        created["called"] = True
        return {"reportJobId": 222, "status": "REGIST"}  # 새 잡

    monkeypatch.setattr(fetcher, "create_stat_report", fake_create)

    polled_ids = []
    poll_statuses = iter(["REGIST", "BUILT"])

    def fake_get_report_job(job_id):
        polled_ids.append(job_id)
        return {"reportJobId": job_id, "status": next(poll_statuses)}

    monkeypatch.setattr(fetcher, "get_report_job", fake_get_report_job)

    fetcher.ensure_reports_built("AD", date(2026, 7, 10), date(2026, 7, 10), timeout=5.0, poll_interval=0.01)

    assert created.get("called") is True       # 낡은 ERROR 잡 대신 새로 생성
    assert polled_ids == [222, 222]             # 낡은 111이 아니라 새 잡 222를 폴링


def test_ensure_reports_built_timeout_skips_without_raising(monkeypatch):
    """(d) 폴링이 timeout 도달 → 해당 날짜 skip, 예외 없음."""
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")
    _no_sleep(monkeypatch)

    monkeypatch.setattr(fetcher, "list_report_jobs", lambda tp: [])
    monkeypatch.setattr(fetcher, "create_stat_report", lambda tp, d: {"reportJobId": 1, "status": "REGIST"})
    # 영원히 RUNNING 상태만 반환 → timeout으로 탈출해야 함
    monkeypatch.setattr(fetcher, "get_report_job", lambda job_id: {"reportJobId": job_id, "status": "RUNNING"})

    # timeout을 아주 작게 주어 테스트가 빠르게 끝나도록
    fetcher.ensure_reports_built("AD", date(2026, 7, 10), date(2026, 7, 10), timeout=0.05, poll_interval=0.01)
    # 예외 없이 반환되면 통과


def test_ensure_reports_built_creation_failure_skips_only_that_date(monkeypatch):
    """(e) 생성 실패 → 그 날짜만 skip, 나머지 날짜는 계속 진행."""
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")
    _no_sleep(monkeypatch)

    monkeypatch.setattr(fetcher, "list_report_jobs", lambda tp: [])

    def fake_create(report_tp, stat_date):
        if stat_date == date(2026, 7, 10):
            raise Exception("500 서버 오류")
        return {"reportJobId": 42, "status": "REGIST"}

    monkeypatch.setattr(fetcher, "create_stat_report", fake_create)

    polled_ids = []

    def fake_get_report_job(job_id):
        polled_ids.append(job_id)
        return {"reportJobId": job_id, "status": "BUILT"}

    monkeypatch.setattr(fetcher, "get_report_job", fake_get_report_job)

    # 07-10(실패)~07-11(성공) 범위 — 07-10 실패해도 예외 없이 07-11까지 진행되어야 함
    fetcher.ensure_reports_built("AD", date(2026, 7, 10), date(2026, 7, 11), timeout=5.0, poll_interval=0.01)
    assert polled_ids == [42]  # 07-11만 생성·폴링됨


def test_ensure_reports_built_list_report_jobs_failure_continues(monkeypatch):
    """기존 잡 목록 조회 자체가 실패해도 예외 전파 없이 생성 시도로 계속 진행."""
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")
    _no_sleep(monkeypatch)

    def boom(tp):
        raise Exception("네트워크 오류")

    monkeypatch.setattr(fetcher, "list_report_jobs", boom)
    monkeypatch.setattr(fetcher, "create_stat_report", lambda tp, d: {"reportJobId": 9, "status": "REGIST"})
    monkeypatch.setattr(fetcher, "get_report_job", lambda job_id: {"reportJobId": job_id, "status": "BUILT"})

    fetcher.ensure_reports_built("AD", date(2026, 7, 10), date(2026, 7, 10))  # 예외 없이 반환


# ── 통합: fetch_ad_performance_daily가 list 전에 ensure_reports_built를 호출 ──
def test_fetch_ad_performance_daily_calls_ensure_before_list(monkeypatch):
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")

    call_order = []

    def fake_ensure(report_tp, date_from, date_to, **kwargs):
        call_order.append(("ensure", report_tp, date_from, date_to))

    def fake_list_ad_reports(date_from, date_to):
        call_order.append(("list", date_from, date_to))
        return []

    monkeypatch.setattr(fetcher, "ensure_reports_built", fake_ensure)
    monkeypatch.setattr(fetcher, "list_ad_reports", fake_list_ad_reports)

    d_from, d_to = date(2026, 7, 10), date(2026, 7, 11)
    out = fetcher.fetch_ad_performance_daily(d_from, d_to)

    assert out == []
    assert call_order == [("ensure", "AD", d_from, d_to), ("list", d_from, d_to)]


def test_fetch_search_term_daily_calls_ensure_with_report_tp(monkeypatch):
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")

    call_order = []

    def fake_ensure(report_tp, date_from, date_to, **kwargs):
        call_order.append(("ensure", report_tp))

    def fake_list(report_tp, date_from, date_to):
        call_order.append(("list", report_tp))
        return []

    monkeypatch.setattr(fetcher, "ensure_reports_built", fake_ensure)
    monkeypatch.setattr(fetcher, "_list_reports_by_type", fake_list)

    fetcher.fetch_search_term_daily("EXPKEYWORD", date(2026, 7, 10), date(2026, 7, 10))
    assert call_order == [("ensure", "EXPKEYWORD"), ("list", "EXPKEYWORD")]
