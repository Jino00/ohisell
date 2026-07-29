# test_wing_login_rg_probe.py — cmd_login이 RG(정산) 데스크톱 세션까지 확인하는지(투 호스트 갭).
#
# ★존재 이유(2026-07-27 라이브 실측): 판매분석(m-wing.coupang.com, 모바일 세션)과 정산
#   (wing.coupang.com, 데스크톱 xauth SSO OIDC client_id=wing)은 **같은 Chrome 안에서도 세션이
#   따로 논다**. vendor-summary=200인데 정산 goto는 xauth 로그인 페이지로 리다이렉트되고
#   status/api는 404인 상태가 실측됐다. 그런데 cmd_login은 VS 프로브만 보고 "로그인 완료"
#   마커를 쓰고 rc=0을 돌려줬다 → 직후 `rg`(cmd_rg, login_wait_secs=0)는 _rg_session_ok 실패로
#   fail-fast. 즉 **침묵 성공이 데스크톱 세션 만료를 마스킹**했다(워밍 네비게이션으로는 해결
#   불가 — 사람 재로그인만 가능).
#
# 여기서 고정하는 것:
#   ① 창이 아직 열려 있는 동안 RG_DASH_URL을 열어 _rg_session_ok로 판정한다.
#   ② 만료면 같은 창에서 _rg_login_wait으로 사람 로그인을 기다린다(남은 시간만큼).
#   ③ 끝내 못 잡으면 rc=RC_RG_LOGIN_REQUIRED(5) — 단, VS push는 그대로 나간다(데이터 손실 금지).
#   ④ 반환 우선순위 불변: 로그인 실패 3 > 파싱 실패 1 > push 실패 rc > RG 미확보 5.
#   ⑤ 데몬 VS 레인(rg_probe=False)·RG 미사용 인스턴스(vendor_id 없음)·레거시(비CDP)는 프로브 제외.
#   (브라우저 실동작은 여기서 검증 불가 — 라이브 검증은 운영 절차의 몫.)
import contextlib
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"


def _ensure_playwright_stub() -> None:
    """페처는 playwright를 import한다 — 백엔드 테스트 환경엔 없을 수 있으므로 스텁."""
    if "playwright.sync_api" in sys.modules:
        return
    pkg = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")

    def _stub_sync_playwright(*_a, **_k):  # 테스트는 브라우저를 띄우지 않는다
        raise RuntimeError("playwright stub — 테스트에서 브라우저 사용 금지")

    sync_api.sync_playwright = _stub_sync_playwright
    pkg.sync_api = sync_api
    sys.modules.setdefault("playwright", pkg)
    sys.modules["playwright.sync_api"] = sync_api


@pytest.fixture()
def fetcher(tmp_path_factory):
    """wing_browser_fetcher를 로드. import 시점에 로그파일을 만들므로 HOME을 tmp로 격리한다."""
    _ensure_playwright_stub()
    home = tmp_path_factory.mktemp("home")
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        spec = importlib.util.spec_from_file_location(
            "_tool_wing_login_rg_probe", TOOLS / "wing_browser_fetcher.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home


_GOOD_BODY = ('{"saleSummaryByDate": [{"date": "2026-07-26", "registrationType": "RFM",'
              ' "gmv": 1000, "unitsSold": 2}]}')


class _RespStub:
    """requests.post 성공 응답 스텁 — _push의 200 검사를 통과시킨다."""

    status_code = 200
    text = ""

    def json(self) -> dict:
        return {}


class _PageStub:
    """goto 이력만 기록하는 페이지 — 프로브가 실제로 정산 페이지를 열었는지 관측한다."""

    url = "https://m-wing.coupang.com/tenants/business-insight/sales-analysis"

    def __init__(self) -> None:
        self.gotos: list[str] = []

    def goto(self, url, **_k) -> None:
        self.gotos.append(url)

    def wait_for_timeout(self, *_a, **_k) -> None:
        return None


def _cfg(tmp_path, *, cdp: bool = True, vendor_id: str | None = "A01564720") -> dict:
    cfg = {
        "account_key": "COUPANG_WING2",
        "prod_base_url": "http://prod.test",
        "ingest_token": "tok",
        "state_file": str(tmp_path / "state.json"),
    }
    if cdp:
        cfg["cdp_port"] = 9299
        cfg["cdp_profile"] = str(tmp_path / "profile")
    if vendor_id is not None:
        cfg["vendor_id"] = vendor_id
    return cfg


@contextlib.contextmanager
def _noop_cm(*_a, **_k):
    yield None


def _patch_browser(fetcher, monkeypatch, *, body: str = _GOOD_BODY) -> _PageStub:
    """cmd_login의 브라우저 층만 스텁 — 프로브 게이트·반환 분기는 실제 코드가 돈다."""
    page = _PageStub()
    monkeypatch.setattr(fetcher, "sync_playwright", _noop_cm)

    @contextlib.contextmanager
    def _fake_chrome(*_a, **_k):
        yield (page, object(), lambda: None)

    monkeypatch.setattr(fetcher, "_chrome", _fake_chrome)
    monkeypatch.setattr(   # VS 로그인은 감지됨(state 저장 완료)
        fetcher, "_login_wait_loop", lambda *_a, **_k: {"status": 200, "body": body})
    monkeypatch.setattr(fetcher, "_fetch_older_windows", lambda *_a, **_k: [])
    return page


def _record_push(fetcher, monkeypatch) -> list[str]:
    calls: list[str] = []

    def _post(url, **_k):
        calls.append(url)
        return _RespStub()

    monkeypatch.setattr(fetcher.requests, "post", _post)
    return calls


def _pushed(calls: list[str]) -> bool:
    return any(c.endswith("/vendor-summary/ingest") for c in calls)


# ── ① 프로브 성공 = 기존과 동일한 rc=0 ────────────────────────────────
def test_rg_probe_success_returns_zero(fetcher, tmp_path, monkeypatch):
    page = _patch_browser(fetcher, monkeypatch)
    calls = _record_push(fetcher, monkeypatch)
    monkeypatch.setattr(fetcher, "_rg_session_ok", lambda _p: True)
    monkeypatch.setattr(
        fetcher, "_rg_login_wait",
        lambda *_a, **_k: pytest.fail("세션이 살아있는데 로그인 대기에 들어가면 안 된다"))

    assert fetcher.cmd_login(_cfg(tmp_path), wait_secs=5) == 0
    assert fetcher.RG_DASH_URL in page.gotos   # 정산 페이지를 실제로 열었다
    assert _pushed(calls)


# ── ② 프로브 실패 → 같은 창에서 사람 로그인 성공 → rc=0 ──────────────────
def test_rg_probe_failure_recovered_by_login_wait(fetcher, tmp_path, monkeypatch):
    """세션 만료를 잡아내고 같은 창에서 회복하는 것이 이 프로브의 본래 목적이다."""
    _patch_browser(fetcher, monkeypatch)
    calls = _record_push(fetcher, monkeypatch)
    waits: list[int] = []
    monkeypatch.setattr(fetcher, "_rg_session_ok", lambda _p: False)

    def _wait(_page, _ctx, _state, secs, *, cdp=False):
        waits.append(secs)
        return True

    monkeypatch.setattr(fetcher, "_rg_login_wait", _wait)

    assert fetcher.cmd_login(_cfg(tmp_path), wait_secs=600) == 0
    assert waits and 0 < waits[0] <= 600   # 남은 시간(경과분 차감)으로 기다린다
    assert _pushed(calls)


# ── ③ 끝내 미확보 → rc=5, 단 VS push는 나간다 ──────────────────────────
def test_rg_login_never_completes_returns_rc5_but_pushes(fetcher, tmp_path, monkeypatch):
    """★핵심: 침묵(rc=0) 금지. 동시에 이미 받은 판매분석 데이터를 버리지도 않는다."""
    _patch_browser(fetcher, monkeypatch)
    calls = _record_push(fetcher, monkeypatch)
    monkeypatch.setattr(fetcher, "_rg_session_ok", lambda _p: False)
    monkeypatch.setattr(fetcher, "_rg_login_wait", lambda *_a, **_k: False)

    assert fetcher.cmd_login(_cfg(tmp_path), wait_secs=5) == fetcher.RC_RG_LOGIN_REQUIRED
    assert fetcher.RC_RG_LOGIN_REQUIRED == 5
    assert _pushed(calls)


def test_probe_exception_is_not_fatal_to_vs_result(fetcher, tmp_path, monkeypatch):
    """프로브 goto가 터져도(창 닫힘 등) VS 파싱·push는 계속하고 rc만 5로 접는다."""
    page = _patch_browser(fetcher, monkeypatch)
    calls = _record_push(fetcher, monkeypatch)

    def _boom(url, **_k):
        page.gotos.append(url)
        if url == fetcher.RG_DASH_URL:
            raise RuntimeError("Target page, context or browser has been closed")

    monkeypatch.setattr(page, "goto", _boom)
    monkeypatch.setattr(fetcher, "_rg_session_ok", lambda _p: True)

    assert fetcher.cmd_login(_cfg(tmp_path), wait_secs=5) == fetcher.RC_RG_LOGIN_REQUIRED
    assert _pushed(calls)


# ── ④ 게이트: 프로브를 걸지 않아야 하는 경우들 ─────────────────────────
def _fail_if_probed(fetcher, monkeypatch) -> None:
    monkeypatch.setattr(
        fetcher, "_rg_session_ok", lambda _p: pytest.fail("프로브를 수행하면 안 되는 경로다"))
    monkeypatch.setattr(
        fetcher, "_rg_login_wait", lambda *_a, **_k: pytest.fail("로그인 대기에 들어가면 안 된다"))


def test_daemon_path_rg_probe_false_skips_probe(fetcher, tmp_path, monkeypatch):
    """데몬 VS 레인: 'VS 성공=요청 소비'가 정확한 의미 — RG 미확보가 VS 실패로 오보되면 안 된다."""
    page = _patch_browser(fetcher, monkeypatch)
    _record_push(fetcher, monkeypatch)
    _fail_if_probed(fetcher, monkeypatch)

    assert fetcher.cmd_login(_cfg(tmp_path), wait_secs=5, rg_probe=False) == 0
    assert fetcher.RG_DASH_URL not in page.gotos


def test_no_vendor_id_skips_probe(fetcher, tmp_path, monkeypatch):
    """RG를 안 쓰는 인스턴스(vendor_id 없음)에 데스크톱 세션을 요구하면 거짓 실패가 된다."""
    page = _patch_browser(fetcher, monkeypatch)
    _record_push(fetcher, monkeypatch)
    _fail_if_probed(fetcher, monkeypatch)

    assert fetcher.cmd_login(_cfg(tmp_path, vendor_id=None), wait_secs=5) == 0
    assert fetcher.RG_DASH_URL not in page.gotos


def test_legacy_non_cdp_skips_probe(fetcher, tmp_path, monkeypatch):
    """레거시(비CDP)는 fresh context라 사람이 반드시 xauth SSO를 통과한다 — 마스킹이 불가능."""
    page = _patch_browser(fetcher, monkeypatch)
    _record_push(fetcher, monkeypatch)
    _fail_if_probed(fetcher, monkeypatch)

    assert fetcher.cmd_login(_cfg(tmp_path, cdp=False), wait_secs=5) == 0
    assert fetcher.RG_DASH_URL not in page.gotos


def test_poll_daemon_calls_login_with_rg_probe_false(fetcher):
    """호출부 회귀 가드 — 데몬이 기본값(rg_probe=True)으로 부르면 rc=5가 VS 실패로 오보된다."""
    import ast
    import inspect
    import textwrap

    src = ast.unparse(ast.parse(textwrap.dedent(inspect.getsource(fetcher.cmd_poll))))
    assert "rg_probe=False" in src


# ── ⑤ 반환 우선순위: 기존 코드가 이긴다 ────────────────────────────────
def test_push_failure_wins_over_rg_login_required(fetcher, tmp_path, monkeypatch):
    """push 실패는 VS 레인의 실제 실패(재시도 대상) — RG 미확보로 덮으면 재시도 판정이 바뀐다."""
    _patch_browser(fetcher, monkeypatch)
    monkeypatch.setattr(fetcher, "_rg_session_ok", lambda _p: False)
    monkeypatch.setattr(fetcher, "_rg_login_wait", lambda *_a, **_k: False)

    def _post(*_a, **_k):
        raise fetcher.requests.RequestException("prod down")

    monkeypatch.setattr(fetcher.requests, "post", _post)

    rc = fetcher.cmd_login(_cfg(tmp_path), wait_secs=5)
    assert rc == 1 and rc != fetcher.RC_RG_LOGIN_REQUIRED


def test_parse_failure_wins_over_rg_login_required(fetcher, tmp_path, monkeypatch):
    """파싱 실패(rc=1)도 우선 — RG 프로브가 기존 실패 사유를 가리면 안 된다."""
    _patch_browser(fetcher, monkeypatch, body='{"saleSummaryByDate": null}')
    _record_push(fetcher, monkeypatch)
    monkeypatch.setattr(fetcher, "_rg_session_ok", lambda _p: False)
    monkeypatch.setattr(fetcher, "_rg_login_wait", lambda *_a, **_k: False)

    assert fetcher.cmd_login(_cfg(tmp_path), wait_secs=5) == 1


def test_vs_login_failure_still_returns_login_required(fetcher, tmp_path, monkeypatch):
    """VS 로그인 자체 실패는 여전히 3 — 프로브는 아예 도달하지 않는다."""
    page = _patch_browser(fetcher, monkeypatch)
    monkeypatch.setattr(fetcher, "_login_wait_loop", lambda *_a, **_k: None)
    _fail_if_probed(fetcher, monkeypatch)

    assert fetcher.cmd_login(_cfg(tmp_path), wait_secs=5) == fetcher.RC_LOGIN_REQUIRED
    assert fetcher.RG_DASH_URL not in page.gotos


# ── ⑥ _rg_applicable 단위 ─────────────────────────────────────────────
def test_rg_applicable_requires_vendor_id_and_push(fetcher, tmp_path):
    full = _cfg(tmp_path)
    assert fetcher._rg_applicable(full) is True
    assert fetcher._rg_applicable({**full, "vendor_id": "  "}) is False   # 공백만 = 없음
    assert fetcher._rg_applicable(_cfg(tmp_path, vendor_id=None)) is False
    assert fetcher._rg_applicable({**full, "ingest_token": ""}) is False  # push 3종 불완전
    assert fetcher._rg_applicable({}) is False
