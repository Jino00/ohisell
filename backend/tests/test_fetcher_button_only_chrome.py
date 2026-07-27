# test_fetcher_button_only_chrome.py — 브라우저 페처 3종의 "순수 버튼-only + per-fetch Chrome" 가드.
#   ★존재 이유(2026-07-27 전환): Chrome 상주 supervisor(KeepAlive)를 폐기하고 poll 데몬이 fetch 때만
#   Chrome을 띄웠다 닫도록 바꿨다. 되돌아가면 "창을 닫아도 30초 뒤 되살아나는" 불편이 재발한다.
#   ① Chrome 소유권 규칙(내가 띄운 것만 내가 닫는다) ② 자동 창 트리거 부재를 고정한다.
#   (브라우저 실동작은 여기서 검증 불가 — 라이브 검증은 운영 절차의 몫.)
import ast
import importlib.util
import inspect
import os
import sys
import textwrap
import types
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
MODULES = ("rocket_supplier_fetcher", "wing_browser_fetcher", "ohitech_ad_fetcher")


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


def _load(name: str):
    """tools/<name>.py를 독립 모듈로 로드(HOME은 호출자가 tmp로 격리)."""
    _ensure_playwright_stub()
    spec = importlib.util.spec_from_file_location(f"_tool_{name}", TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module", params=MODULES)
def fetcher(request, tmp_path_factory):
    """세 페처를 각각 로드. import 시점에 로그파일을 만들므로 HOME을 tmp로 격리한다."""
    home = tmp_path_factory.mktemp("home")
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        yield _load(request.param)
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home


class _FakeProc:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid


def _cfg(tmp_path) -> dict:
    return {"cdp_port": 9299, "cdp_profile": str(tmp_path / "profile")}


# ── ① Chrome 기동 커맨드라인 ──────────────────────────────────────────
def test_chrome_argv_uses_real_chrome_and_config(fetcher, tmp_path):
    argv = fetcher._chrome_argv(_cfg(tmp_path))
    assert argv[0] == "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    assert "--remote-debugging-port=9299" in argv
    assert f"--user-data-dir={tmp_path / 'profile'}" in argv
    # Playwright 번들 Chromium(Chrome for Testing)은 Akamai에 차단된다 — 절대 쓰면 안 됨.
    assert not any("ms-playwright" in a or "Chromium" in a for a in argv)


# ── ② 소유권 규칙 ────────────────────────────────────────────────────
def test_adopts_existing_chrome_and_never_closes_it(fetcher, tmp_path, monkeypatch):
    """이미 떠 있는 Chrome(사람이 띄운 창 등)은 adopt만 — 띄우지도, 닫지도 않는다."""
    closed, launched = [], []
    monkeypatch.setattr(fetcher, "_cdp_alive", lambda _p: True)
    monkeypatch.setattr(fetcher, "_launch_chrome", lambda _c: launched.append(1))
    monkeypatch.setattr(fetcher, "_close_chrome", lambda p: closed.append(p))
    with fetcher._owned_chrome(_cfg(tmp_path)) as owner:
        assert owner.owned is False
    assert launched == []
    assert closed == []


def test_launches_and_closes_own_chrome(fetcher, tmp_path, monkeypatch):
    """안 떠 있으면 내가 띄우고(소유) 작업 후 내가 닫는다 — supervisor 없이 완결."""
    state = {"alive": False}
    closed = []
    proc = _FakeProc()

    def _launch(_cfg_):
        state["alive"] = True
        return proc

    monkeypatch.setattr(fetcher, "_cdp_alive", lambda _p: state["alive"])
    monkeypatch.setattr(fetcher, "_profile_chrome_alive", lambda _p: False)
    monkeypatch.setattr(fetcher, "_launch_chrome", _launch)
    monkeypatch.setattr(fetcher, "_close_chrome", lambda p: closed.append(p))
    with fetcher._owned_chrome(_cfg(tmp_path)) as owner:
        assert owner.owned is True
    assert closed == [proc]


def test_keep_open_leaves_own_chrome_for_human_login(fetcher, tmp_path, monkeypatch):
    """세션 만료로 사람이 로그인해야 하면 내가 띄운 창도 남긴다(닫으면 로그인할 창이 없다)."""
    state = {"alive": False}
    closed = []
    proc = _FakeProc()

    def _launch(_cfg_):
        state["alive"] = True
        return proc

    monkeypatch.setattr(fetcher, "_cdp_alive", lambda _p: state["alive"])
    monkeypatch.setattr(fetcher, "_profile_chrome_alive", lambda _p: False)
    monkeypatch.setattr(fetcher, "_launch_chrome", _launch)
    monkeypatch.setattr(fetcher, "_close_chrome", lambda p: closed.append(p))
    with fetcher._owned_chrome(_cfg(tmp_path)) as owner:
        owner.keep_open = True
    assert closed == []


def test_refuses_when_profile_busy_without_cdp(fetcher, tmp_path, monkeypatch):
    """CDP는 죽었는데 다른 Chrome이 프로필 점유 중 → 중복 launch 금지(프로필 손상)."""
    launched = []
    monkeypatch.setattr(fetcher, "_cdp_alive", lambda _p: False)
    monkeypatch.setattr(fetcher, "_profile_chrome_alive", lambda _p: True)
    monkeypatch.setattr(fetcher, "_launch_chrome", lambda _c: launched.append(1))
    with pytest.raises(RuntimeError):
        with fetcher._owned_chrome(_cfg(tmp_path)):
            pass
    assert launched == []


def test_launch_failure_raises_and_closes_nothing(fetcher, tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "_cdp_alive", lambda _p: False)
    monkeypatch.setattr(fetcher, "_profile_chrome_alive", lambda _p: False)
    monkeypatch.setattr(fetcher, "_launch_chrome", lambda _c: None)
    with pytest.raises(RuntimeError):
        with fetcher._owned_chrome(_cfg(tmp_path)):
            pass


def test_cdp_never_ready_closes_launched_chrome(fetcher, tmp_path, monkeypatch):
    """기동은 됐는데 CDP가 안 올라오면 그 Chrome을 닫고 실패 — 유령 창을 남기지 않는다."""
    closed = []
    proc = _FakeProc()
    monkeypatch.setattr(fetcher, "_cdp_alive", lambda _p: False)
    monkeypatch.setattr(fetcher, "_profile_chrome_alive", lambda _p: False)
    monkeypatch.setattr(fetcher, "_launch_chrome", lambda _c: proc)
    monkeypatch.setattr(fetcher, "_wait_cdp", lambda _p, timeout_s=60: False)
    monkeypatch.setattr(fetcher, "_close_chrome", lambda p: closed.append(p))
    with pytest.raises(RuntimeError):
        with fetcher._owned_chrome(_cfg(tmp_path)):
            pass
    assert closed == [proc]


# ── ③ 자동 창 트리거 부재(회귀 가드) ──────────────────────────────────
def test_no_chrome_supervisor_launch(fetcher):
    """chrome-supervise가 남아 있다면 no-op이어야 한다(Chrome을 띄우면 KeepAlive 부활)."""
    fn = getattr(fetcher, "cmd_chrome_supervise", None)
    if fn is None:
        return  # 완전 제거된 페처(rocket)
    src = _code_only(fn)
    assert "Popen" not in src
    assert "_launch_chrome" not in src


def _code_only(fn) -> str:
    """함수에서 docstring·주석을 뺀 '실행되는 코드'만 — 폐기 설명 문구는 허용해야 하므로.

    (문자열 replace로는 안 된다: py3.13+는 __doc__ 들여쓰기를 컴파일 시 제거해 원본과 다름.)
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    fdef = tree.body[0]
    body = fdef.body
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        fdef.body = body[1:]
    return ast.unparse(fdef)   # unparse는 주석도 제거


def test_poll_has_no_automatic_fetch_trigger(fetcher):
    """poll 루프의 트리거는 버튼(refresh 플래그)뿐 — 시간/신선도 기반 자동 실행 금지."""
    src = _code_only(fetcher.cmd_poll)
    for banned in ("daily_run_hours", "rg_daily_hour", "rg_done_date", "daily_due", "23h"):
        assert banned not in src, f"자동 트리거 잔재: {banned}"


def test_wing_rg_daily_schedule_removed(tmp_path_factory):
    """RG 정산 새벽 일일예약(마지막 자동 창 트리거)이 모듈 전체에서 사라졌는지."""
    src = (TOOLS / "wing_browser_fetcher.py").read_text(encoding="utf-8")
    # 주석의 '폐기' 설명은 허용 — 실제 코드 심볼만 금지.
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert "rg_done_date" not in code
    assert "daily_due" not in code
    assert 'cfg.get("rg_daily_hour"' not in code


def test_supervisor_plists_deleted():
    """상주 supervisor plist는 repo에서 삭제 — 재설치되면 KeepAlive 부활."""
    assert not (TOOLS / "com.ohisell.wing-chrome.plist").exists()
    assert not (TOOLS / "com.ohisell.ohitech-chrome.plist").exists()
    installer = (TOOLS / "install_local_runtime.sh").read_text(encoding="utf-8")
    assert "wing-chrome:wing_browser_fetcher.py" not in installer
    assert "ohitech-chrome:ohitech_ad_fetcher.py" not in installer
