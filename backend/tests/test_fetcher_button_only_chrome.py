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
    monkeypatch.setattr(fetcher, "_port_owner_foreign", lambda *_a: False)   # 우리 프로필 확인됨
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
    # rocket 포함 3종 모두 필수: Jino Mac에 com.ohisell.rocket-chrome(chrome-supervise, KeepAlive)가
    # 실재한다(2026-07-27 실측). 커맨드가 없으면 새 .py 설치 즉시 usage 에러 → 30초 크래시 루프.
    assert fn is not None, "chrome-supervise 스텁 없음 — 구 plist가 크래시 루프에 빠진다"
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


# ── ④ 이중 기동·오adopt·시그널 누수 방어(codex R1 P1#2~#4) ─────────────
def test_launch_is_serialized_by_profile_lock(fetcher, tmp_path, monkeypatch):
    """기동 결정 구간이 프로필 lock 안에서 일어나야 한다 — 두 프로세스 이중 기동=프로필 손상.

    lock을 이미 잡고 있으면(=다른 프로세스가 기동 중) launch를 시도조차 하지 않는다.
    """
    import fcntl

    cfg = _cfg(tmp_path)
    profile = cfg["cdp_profile"]
    launched = []
    monkeypatch.setattr(fetcher, "_cdp_alive", lambda _p: False)
    monkeypatch.setattr(fetcher, "_profile_chrome_alive", lambda _p: False)
    monkeypatch.setattr(fetcher, "_launch_chrome", lambda _c: launched.append(1))
    # 외부 프로세스가 같은 프로필 lock을 점유한 상황 재현.
    fd = os.open(profile.rstrip("/") + ".launchlock", os.O_WRONLY | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(RuntimeError):
            with fetcher._owned_chrome({**cfg, "chrome_launch_lock_timeout_s": 1}):
                pass
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert launched == [], "프로필 lock 점유 중인데 Chrome을 이중 기동했다"


def test_profile_launch_lock_is_outside_profile_dir(fetcher, tmp_path):
    """lock 파일은 프로필 디렉터리 *밖*에 — 안에 두면 프로필 재생성 시 사라져 무력화된다."""
    profile = str(tmp_path / "profile")
    with fetcher._profile_launch_lock(profile, timeout_s=5):
        pass
    assert os.path.exists(profile + ".launchlock")
    assert not os.path.exists(os.path.join(profile, ".launchlock"))


def test_refuses_adopting_chrome_of_another_profile(fetcher, tmp_path, monkeypatch):
    """포트가 살아 있어도 그 Chrome이 '다른 프로필'이면 adopt 거부 — 남의 계정 데이터 적재 차단."""
    monkeypatch.setattr(fetcher, "_cdp_alive", lambda _p: True)
    monkeypatch.setattr(fetcher, "_port_owner_foreign", lambda *_a: True)
    monkeypatch.setattr(fetcher, "_launch_chrome", lambda _c: pytest.fail("기동하면 안 됨"))
    monkeypatch.setattr(fetcher, "_notify_mac", lambda *_a, **_k: None, raising=False)
    with pytest.raises(RuntimeError):
        with fetcher._owned_chrome(_cfg(tmp_path)):
            pass


def test_port_owner_unverified_is_refused_by_default(fetcher, monkeypatch):
    """소유자 확인 불가 = adopt 거부(fail-closed).

    우리가 adopt할 정당한 창은 전부 _chrome_argv로 떠서 cmdline에 프로필이 있다 → '확인 불가'는
    정상 케이스가 아니라 남의 Chrome 신호다(codex R2). 오적재는 조용하고 거부는 시끄럽다.
    """
    def _boom(*_a, **_k):
        raise OSError("lsof 없음")

    monkeypatch.setattr(fetcher.subprocess, "run", _boom)
    assert fetcher._port_owner_foreign(9299, "/tmp/p") is True
    # 현장 오판 시 설정으로 옛 동작 복귀 가능.
    assert fetcher._port_owner_foreign(9299, "/tmp/p", True) is False


def test_profile_match_is_not_prefix_substring(fetcher):
    """'--user-data-dir=/tmp/profile'이 프로필 '/tmp/p'로 매칭되면 남의 Chrome을 adopt한다."""
    line = "/Applications/Google Chrome --user-data-dir=/tmp/profile --no-first-run"
    assert fetcher._cmdline_has_profile(line, "/tmp/profile") is True
    assert fetcher._cmdline_has_profile(line, "/tmp/p") is False, "접두 오탐 — 경계 검사 필요"
    assert fetcher._cmdline_has_profile(line, "/tmp/profile/") is True   # 끝 슬래시 정규화
    # 줄 끝 케이스
    assert fetcher._cmdline_has_profile("chrome --user-data-dir=/tmp/p", "/tmp/p") is True


def test_cmd_chrome_takes_profile_launch_lock(fetcher, tmp_path, monkeypatch):
    """수동 chrome 커맨드도 프로필 lock 안에서 — 여기서 새면 fetch와 겹쳐 이중 기동된다."""
    import fcntl

    cfg = {**_cfg(tmp_path), "chrome_launch_lock_timeout_s": 1}
    launched = []
    monkeypatch.setattr(fetcher, "_cdp_alive", lambda _p: False)
    monkeypatch.setattr(fetcher, "_profile_chrome_alive", lambda _p: False)
    monkeypatch.setattr(fetcher, "_launch_chrome", lambda _c: launched.append(1))
    fd = os.open(cfg["cdp_profile"].rstrip("/") + ".launchlock", os.O_WRONLY | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(RuntimeError):
            fetcher.cmd_chrome(cfg)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert launched == [], "lock 점유 중인데 수동 chrome이 이중 기동했다"


def test_cmd_chrome_refuses_when_profile_busy(fetcher, tmp_path, monkeypatch):
    """CDP 미응답인데 프로필이 점유 중이면 수동 chrome도 기동 거부(Singleton 삭제=손상)."""
    launched = []
    monkeypatch.setattr(fetcher, "_cdp_alive", lambda _p: False)
    monkeypatch.setattr(fetcher, "_profile_chrome_alive", lambda _p: True)
    monkeypatch.setattr(fetcher, "_launch_chrome", lambda _c: launched.append(1))
    assert fetcher.cmd_chrome(_cfg(tmp_path)) == 1
    assert launched == []


def test_signal_cleanup_closes_owned_chrome(fetcher, monkeypatch):
    """SIGTERM 경로(=finally가 안 도는 경로)에서도 내가 띄운 Chrome을 회수해야 한다."""
    closed = []
    monkeypatch.setattr(fetcher, "_close_chrome", lambda p, grace_s=15: closed.append(p))
    owner = fetcher._ChromeOwner()
    owner.proc = _FakeProc()
    fetcher._LIVE_OWNERS.append(owner)
    try:
        fetcher._cleanup_owned_chromes()   # signum=None → os._exit 안 함
    finally:
        if owner in fetcher._LIVE_OWNERS:
            fetcher._LIVE_OWNERS.remove(owner)
    assert closed and owner.proc is None


def test_signal_cleanup_keeps_login_window(fetcher, monkeypatch):
    """keep_open(사람이 로그인 중) 창은 시그널 경로에서도 닫지 않는다."""
    closed = []
    monkeypatch.setattr(fetcher, "_close_chrome", lambda p, grace_s=15: closed.append(p))
    owner = fetcher._ChromeOwner()
    owner.proc = _FakeProc()
    owner.keep_open = True
    fetcher._LIVE_OWNERS.append(owner)
    try:
        fetcher._cleanup_owned_chromes()
    finally:
        if owner in fetcher._LIVE_OWNERS:
            fetcher._LIVE_OWNERS.remove(owner)
    assert closed == []


def test_signal_handler_installed_in_main(fetcher):
    """main()이 시그널 회수를 설치하지 않으면 SIGTERM 누수 방어가 죽은 코드가 된다."""
    src = _code_only(fetcher.main)
    assert "_install_signal_cleanup()" in src


def test_owner_unregistered_after_normal_run(fetcher, tmp_path, monkeypatch):
    """정상 종료 후 _LIVE_OWNERS가 비어야 한다 — 안 비면 이미 죽은 proc을 재차 종료 시도."""
    proc = _FakeProc()
    state = {"alive": False}

    def _launch(_c):
        state["alive"] = True
        return proc

    monkeypatch.setattr(fetcher, "_cdp_alive", lambda _p: state["alive"])
    monkeypatch.setattr(fetcher, "_profile_chrome_alive", lambda _p: False)
    monkeypatch.setattr(fetcher, "_launch_chrome", _launch)
    monkeypatch.setattr(fetcher, "_close_chrome", lambda p, grace_s=15: None)
    before = len(fetcher._LIVE_OWNERS)
    with fetcher._owned_chrome(_cfg(tmp_path)):
        assert len(fetcher._LIVE_OWNERS) == before + 1
    assert len(fetcher._LIVE_OWNERS) == before


# ── ⑤ launchd 전환(구 supervisor 실제 제거) ──────────────────────────
def test_installer_removes_deprecated_supervisor_jobs():
    """설치 스크립트가 구 supervisor 잡을 실제로 bootout·plist 삭제해야 한다.

    설치 목록에서 빼기만 하면 이미 로드된 구 잡은 계속 Chrome을 상주시킨다(codex R1 P1#1).
    """
    installer = (TOOLS / "install_local_runtime.sh").read_text(encoding="utf-8")
    for label in ("wing-chrome", "ohitech-chrome", "rocket-chrome"):
        assert label in installer, f"구 supervisor 정리 대상 누락: {label}"
    assert "launchctl bootout" in installer
    assert "rm -f \"$_dep_plist\"" in installer
    # 제거 실패를 조용히 넘기면 안 된다 — 설치를 실패로 끝내야 한다.
    assert "설치 중단" in installer and "exit 1" in installer


def test_supervisor_plists_deleted():
    """상주 supervisor plist는 repo에서 삭제 — 재설치되면 KeepAlive 부활."""
    assert not (TOOLS / "com.ohisell.wing-chrome.plist").exists()
    assert not (TOOLS / "com.ohisell.ohitech-chrome.plist").exists()
    installer = (TOOLS / "install_local_runtime.sh").read_text(encoding="utf-8")
    assert "wing-chrome:wing_browser_fetcher.py" not in installer
    assert "ohitech-chrome:ohitech_ad_fetcher.py" not in installer
