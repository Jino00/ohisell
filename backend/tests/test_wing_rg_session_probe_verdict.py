# test_wing_rg_session_probe_verdict.py — RG 세션 프로브가 "로그아웃"과 "판정 불가"를 구분하는가.
#
# ★존재 이유(2026-08-03 라이브 실측): 프로브 실패가 bool 한 비트라 로그아웃·비200·깨진 JSON·
#   evaluate 예외가 전부 '세션 만료'로 뭉개졌다. 층2 루프가 그걸 곧장 중단 사유로 써서, 멀쩡한
#   세션에서 남은 주기가 통째로 버려지고 "로그인 필요"라는 **거짓 사유**가 기록됐다.
#     - 3/3 재현: 매번 다운로드 성공 6~7초 뒤 실패
#     - 대조군 성립: 같은 세션·2분 간격에 대상 2개면 중단, 1개면 성공
#     - rider(PRODUCT_SIZE_COMPARISON)가 항상 슬롯 #1 → 진짜 정산 결손은 구조적으로 항상 #2 이하
#       → 이 오판 하나가 결손 충전을 영구히 막았다(회차당 사실상 1주기만 처리)
#
# 여기서 고정하는 것:
#   ① 로그인 신호(401·403·kccontext·signin·200+HTML)만 AUTH
#   ② 그 외 실패(429·500·깨진 JSON·예외·예상 형태 아님)는 UNKNOWN — 만료로 승격하지 않는다
#   ③ 502 오류 페이지처럼 status가 200이 아닌 HTML을 로그아웃으로 오인하지 않는다
#   ④ 층2 루프 중단 판정(_rg_loop_should_abort)은 AUTH일 때만 True
#   ⑤ 1차 실패·2차 성공은 OK(일시 실패를 만료로 굳히지 않는다)
#   ⑥ 실패 사유가 사람이 읽을 수 있게 남는다 — 종전엔 사후 원인 규명이 불가능했다
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"


def _ensure_playwright_stub() -> None:
    if "playwright.sync_api" in sys.modules:
        return
    pkg = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")

    def _stub_sync_playwright(*_a, **_k):
        raise RuntimeError("playwright stub — 테스트에서 브라우저 사용 금지")

    sync_api.sync_playwright = _stub_sync_playwright
    pkg.sync_api = sync_api
    sys.modules.setdefault("playwright", pkg)
    sys.modules["playwright.sync_api"] = sync_api


@pytest.fixture()
def fetcher(tmp_path_factory):
    """import 시점에 로그파일을 만들므로 HOME을 tmp로 격리한다."""
    _ensure_playwright_stub()
    home = tmp_path_factory.mktemp("home")
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        spec = importlib.util.spec_from_file_location(
            "_tool_wing_rg_probe_verdict", TOOLS / "wing_browser_fetcher.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home


# ★프로브 엔드포인트는 2026-08-03에 status/api → download-list/api로 옮겼다(라이브 실측:
#   status/api 1/32 성공 vs download-list 8/8). 그래서 OK 응답의 형태는 **항목 배열**이다
#   (라이브 캡처 'JSON list n=5'). status/api의 dict를 그대로 두면 이 테스트가 옛 계약을
#   고정해 버린다.
_OK_BODY = '[{"requestTime": "1754190000000", "downloadStatus": "COMPLETED"}]'


class _PageStub:
    """evaluate가 미리 정한 {status, body}를 순서대로 돌려준다(또는 예외를 던진다)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def evaluate(self, _js, _args):
        self.calls += 1
        r = self._responses[min(self.calls - 1, len(self._responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


# ── ① 정상 ───────────────────────────────────────────────────────────────
def test_probe_ok(fetcher):
    v, why = fetcher._rg_session_probe(_PageStub([{"status": 200, "body": _OK_BODY}]))
    assert v == fetcher._PROBE_OK, why


# ── ② 로그인 신호만 AUTH ─────────────────────────────────────────────────
@pytest.mark.parametrize("res", [
    {"status": 401, "body": ""},
    {"status": 403, "body": ""},
    {"status": 200, "body": "<html><body>signin</body></html>"},
    {"status": 302, "body": "kcContext = {...}"},
])
def test_probe_auth_signals(fetcher, res):
    v, why = fetcher._rg_session_probe(_PageStub([res]))
    assert v == fetcher._PROBE_AUTH, f"{res} → {v} ({why})"


# ── ③ 그 외 실패는 UNKNOWN (만료로 승격 금지) ────────────────────────────
@pytest.mark.parametrize("res, tag", [
    ({"status": 429, "body": "rate limited"}, "429 스로틀"),
    ({"status": 500, "body": "oops"}, "500"),
    ({"status": 200, "body": "not-json"}, "깨진 JSON"),
    ({"status": 200, "body": '{"other": 1}'}, "예상 형태 아님(list 아닌 dict)"),
    (RuntimeError("Execution context was destroyed"), "evaluate 예외"),
    (None, "응답 형식 이상"),
])
def test_probe_unknown_not_auth(fetcher, res, tag):
    v, why = fetcher._rg_session_probe(_PageStub([res]))
    assert v == fetcher._PROBE_UNKNOWN, f"{tag} → {v} ({why})"
    assert v != fetcher._PROBE_AUTH, f"{tag}를 로그아웃으로 오인하면 남은 주기가 통째로 버려진다"


def test_probe_502_html_error_page_is_not_logout(fetcher):
    """★502 오류 페이지도 HTML이다 — '<html' 하나로 로그아웃을 단정하면 인프라 장애가
    로그아웃으로 둔갑해 사람에게 헛된 재로그인을 시킨다."""
    v, why = fetcher._rg_session_probe(
        _PageStub([{"status": 502, "body": "<html><h1>502 Bad Gateway</h1></html>"}]))
    assert v == fetcher._PROBE_UNKNOWN, why


# ── ④ 루프 중단 판정 — AUTH일 때만 (변이 테스트 대상) ────────────────────
def test_loop_aborts_only_on_auth(fetcher):
    assert fetcher._rg_loop_should_abort(fetcher._PROBE_AUTH) is True
    assert fetcher._rg_loop_should_abort(fetcher._PROBE_UNKNOWN) is False, (
        "판정 불가로 루프를 중단하면 이번 결함이 그대로 재현된다 — 멀쩡한 세션에서 "
        "남은 주기가 버려지고 '로그인 필요'가 거짓으로 기록된다")
    assert fetcher._rg_loop_should_abort(fetcher._PROBE_OK) is False


# ── ⑤ 1차 실패·2차 성공 = OK ─────────────────────────────────────────────
def test_confirmed_recovers_on_second_probe(fetcher, monkeypatch):
    monkeypatch.setattr(fetcher.time, "sleep", lambda _s: None)   # 5초 대기 제거
    page = _PageStub([{"status": 429, "body": "slow down"},
                      {"status": 200, "body": _OK_BODY}])
    assert fetcher._rg_session_verdict_confirmed(page) == fetcher._PROBE_OK
    assert page.calls == 2


def test_confirmed_keeps_unknown_when_both_fail(fetcher, monkeypatch):
    monkeypatch.setattr(fetcher.time, "sleep", lambda _s: None)
    page = _PageStub([{"status": 500, "body": "x"}])
    v = fetcher._rg_session_verdict_confirmed(page)
    assert v == fetcher._PROBE_UNKNOWN
    assert fetcher._rg_loop_should_abort(v) is False


def test_confirmed_reports_auth_when_really_logged_out(fetcher, monkeypatch):
    """진짜 로그아웃이면 여전히 중단한다 — 방어를 없앤 게 아니라 좁힌 것이다(계약 금지선)."""
    monkeypatch.setattr(fetcher.time, "sleep", lambda _s: None)
    page = _PageStub([{"status": 200, "body": "<html>signin</html>"}])
    v = fetcher._rg_session_verdict_confirmed(page)
    assert v == fetcher._PROBE_AUTH
    assert fetcher._rg_loop_should_abort(v) is True


# ── ⑥ 사유가 남는다 ──────────────────────────────────────────────────────
def test_probe_reason_identifies_the_cause(fetcher):
    """종전엔 실패가 False 한 비트라 사후 원인 규명이 불가능했다(6일 침묵의 원인을 못 짚음)."""
    _, why429 = fetcher._rg_session_probe(_PageStub([{"status": 429, "body": "slow"}]))
    _, whyexc = fetcher._rg_session_probe(_PageStub([RuntimeError("ctx destroyed")]))
    assert "429" in why429
    assert "ctx destroyed" in whyexc
    assert why429 != whyexc, "서로 다른 실패가 같은 사유로 뭉개지면 안 된다"


# ── ⑦ 델타(2026-08-03): 프로브 엔드포인트 이전 · status/api 5xx 재시도 · 오리진 이탈 ───────
# 라이브 계측(같은 페이지·같은 쿠키·32회): status/api 1/32, download-list 8/8.
# 판정을 3상태로 나눠도 프로브가 97% UNKNOWN이면 세션 감시는 사실상 죽어 있고, 열거용
# status/api를 1회로 판정하면 회차 대부분이 rc=1로 죽는다(라이브 13:35:37 런이 그랬다).
_LIVE_500_SHELL = (
    '\n\n\n\n\n\n\n\n\n<!doctype html> <html class="font-v2" lang="ko"><head>'
    '<title>Coupang Wing - 김진오, 오픽스</title><meta charset="UTF-8">'
    "<script>window.__GLOBAL_DATA__ = {\n        activeProfile: 'production',\n"
)


def test_live_500_shell_is_unknown_not_auth(fetcher):
    """★사고의 한 줄 요약: HTTP 500 + **로그인된** 셸 HTML ≠ 로그아웃."""
    v, why = fetcher._rg_session_probe(_PageStub([{"status": 500, "body": _LIVE_500_SHELL}]))
    assert v == fetcher._PROBE_UNKNOWN, why
    assert fetcher._rg_loop_should_abort(v) is False


def test_probe_targets_download_list_not_status_api(fetcher):
    """프로브 엔드포인트 계약 — 되돌리면 프로브가 97% UNKNOWN인 장식으로 돌아간다."""
    import inspect
    src = inspect.getsource(fetcher._rg_session_probe)
    assert "RG_DOWNLOAD_LIST_PATH" in src
    assert "RG_STATUS_PATH" not in src


class _UrlPageStub(_PageStub):
    def __init__(self, responses, url):
        super().__init__(responses)
        self.url = url


def test_off_origin_is_auth_evidence(fetcher):
    """진짜 로그아웃은 xauth 로그인 페이지로 리다이렉트된다(2026-07-27 실측, cmd_login 주석)."""
    page = _UrlPageStub([{"status": 200, "body": _OK_BODY}],
                        "https://xauth.coupang.com/auth/realms/seller/protocol/openid-connect/auth")
    v, why = fetcher._rg_session_probe(page)
    assert v == fetcher._PROBE_AUTH, why
    assert page.calls == 0, "오리진 이탈이면 POST를 낭비하지 않는다"


def test_on_origin_probes_normally(fetcher):
    page = _UrlPageStub([{"status": 200, "body": _OK_BODY}],
                        "https://wing.coupang.com/tenants/rfm/settlements/status-new")
    assert fetcher._rg_session_probe(page)[0] == fetcher._PROBE_OK


# ── status/api 재시도 ────────────────────────────────────────────────────
class _StatusPage:
    """status/api용 페이지 스텁 — 응답 시퀀스를 소진하고 마지막 응답을 반복한다."""

    url = "https://wing.coupang.com/tenants/rfm/settlements/status-new"

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def evaluate(self, *_a, **_k):
        self.calls += 1
        return self._responses[min(self.calls - 1, len(self._responses) - 1)]


_STATUS_OK = '{"settlementStatusReports": [{"groupKey": "A01564720-2026-07-27-2026-07-31"}]}'


def test_status_fetch_retries_through_500s(fetcher, monkeypatch):
    """★없으면 프로브만 고친 상태에서 회차 대부분이 rc=1로 죽는다(라이브 13:35:37 재현)."""
    monkeypatch.setattr(fetcher.time, "sleep", lambda _s: None)
    page = _StatusPage([{"status": 500, "body": _LIVE_500_SHELL},
                        {"status": 500, "body": _LIVE_500_SHELL},
                        {"status": 200, "body": _STATUS_OK}])
    verdict, raw = fetcher._rg_fetch_status_raw(page, {"rg_status_days": 35}, budget_s=60)
    assert verdict == fetcher._PROBE_OK
    assert raw == {"settlementStatusReports": [{"groupKey": "A01564720-2026-07-27-2026-07-31"}]}
    assert page.calls == 3


def test_status_fetch_persistent_500_is_unknown_not_auth(fetcher, monkeypatch):
    """예산을 다 써도 500이면 UNKNOWN — AUTH로 접으면 요청이 재시도 없이 소멸한다."""
    monkeypatch.setattr(fetcher.time, "sleep", lambda _s: None)
    page = _StatusPage([{"status": 500, "body": _LIVE_500_SHELL}])
    verdict, raw = fetcher._rg_fetch_status_raw(page, {"rg_status_days": 35}, budget_s=0)
    assert verdict == fetcher._PROBE_UNKNOWN and raw is None


def test_status_fetch_stops_immediately_on_auth(fetcher, monkeypatch):
    """로그아웃이면 재시도해도 통과할 수 없다 — 예산을 낭비하지 않고 즉시 AUTH."""
    monkeypatch.setattr(fetcher.time, "sleep", lambda _s: None)
    page = _StatusPage([{"status": 200, "body": "<html>signin</html>"}])
    verdict, _ = fetcher._rg_fetch_status_raw(page, {"rg_status_days": 35}, budget_s=60)
    assert verdict == fetcher._PROBE_AUTH
    assert page.calls == 1
