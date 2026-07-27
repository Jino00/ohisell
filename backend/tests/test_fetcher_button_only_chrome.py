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
def test_chrome_supervise_stub_removed(fetcher):
    """chrome-supervise no-op 스텁은 완전히 제거됐다(2026-07-27 재확인: 구 plist 소멸 확정).

    ★2026-07-27 도입 당시엔 "구 plist가 아직 Mac에 남아 있을 수 있으니 usage 에러로 인한
    KeepAlive 크래시 루프를 막는 2차 방어"로 no-op 스텁을 남겼다. 이후 재점검에서
    ~/Library/LaunchAgents·/Library/LaunchAgents·/Library/LaunchDaemons·launchctl list 전부에서
    wing-chrome/ohitech-chrome/rocket-chrome supervisor 잡이 실재하지 않음을 확인 →
    2차 방어의 존재 이유가 소멸해 스텁·라우팅을 제거했다. 되돌아오면(=재추가) 회귀다.
    """
    assert getattr(fetcher, "cmd_chrome_supervise", None) is None, (
        "chrome-supervise 스텁이 되살아났다 — 구 plist 소멸을 재확인하지 않고 재추가했다면 회귀"
    )
    src = _code_only(fetcher.main)
    assert "chrome-supervise" not in src
    assert "cmd_chrome_supervise" not in src


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


def test_port_owner_unverified_is_refused_by_default(fetcher, tmp_path, monkeypatch):
    """소유자 확인 불가 = adopt 거부(fail-closed).

    우리가 adopt할 정당한 창은 전부 _chrome_argv로 떠서 cmdline에 프로필이 있다 → '확인 불가'는
    정상 케이스가 아니라 남의 Chrome 신호다(codex R2). 오적재는 조용하고 거부는 시끄럽다.
    """
    def _boom(*_a, **_k):
        raise OSError("lsof 없음")

    profile = str(tmp_path / "profile")
    _mk_lock(profile, os.getpid())
    monkeypatch.setattr(fetcher, "_proc_start_epoch", lambda _p: 0.0)
    monkeypatch.setattr(fetcher.subprocess, "run", _boom)
    assert fetcher._port_owner_foreign(9299, profile) is True
    # 현장 오판 시 설정으로 옛 동작 복귀 가능.
    assert fetcher._port_owner_foreign(9299, profile, True) is False


def _mk_lock(profile: str, pid: int) -> None:
    os.makedirs(profile, exist_ok=True)
    lp = os.path.join(profile, "SingletonLock")
    if os.path.islink(lp) or os.path.exists(lp):
        os.unlink(lp)
    os.symlink(f"myhost-{pid}", lp)


class _R:
    def __init__(self, out): self.stdout = out


def test_port_owner_verified_by_pid_identity(fetcher, tmp_path, monkeypatch):
    """소유 판정은 PID 동일성으로 — cmdline 문자열 대조는 argv 경계를 복원할 수 없다(codex R4).

    macOS `ps -o command=`는 argv를 공백으로 flatten하므로 공백을 품은 인자 하나와 진짜 인자
    두 개를 구분할 수 없다. 그래서 '우리 프로필 lock의 살아있는 PID == LISTEN PID'가 양성 증거다.
    """
    profile = str(tmp_path / "profile")
    live = os.getpid()                       # 살아있는 PID(테스트 프로세스)
    _mk_lock(profile, live)

    def _run(argv, *_a, **_k):
        if argv[0] == "lsof":
            return _R(f"{live}\n")
        return _R(f"chrome --user-data-dir={profile} --no-first-run\n")   # ps

    monkeypatch.setattr(fetcher.subprocess, "run", _run)
    monkeypatch.setattr(fetcher, "_proc_start_epoch", lambda _p: 0.0)   # lock보다 먼저 시작
    assert fetcher._port_owner_foreign(9299, profile) is False   # 일치 → adopt 정당

    def _run_other(argv, *_a, **_k):
        if argv[0] == "lsof":
            return _R("999999\n")           # 다른 프로세스가 포트를 잡음
        return _R(f"chrome --user-data-dir={profile} --no-first-run\n")

    monkeypatch.setattr(fetcher.subprocess, "run", _run_other)
    monkeypatch.setattr(fetcher, "_proc_start_epoch", lambda _p: 0.0)
    assert fetcher._port_owner_foreign(9299, profile) is True    # 불일치 → 거부


def test_pid_reuse_refused_even_if_cmdline_matches(fetcher, tmp_path, monkeypatch):
    """★codex R6: 재사용된 PID는 ①생존·④cmdline을 모두 통과할 수 있다 — 시작 시각으로 판별.

    stale lock의 PID가 재배정되면 '살아있음'은 자동 충족이고, cmdline은 ps의 argv flatten 때문에
    단독 판별자가 될 수 없다(R4). 재사용을 직접 가르는 유일한 사실은 **그 프로세스가 lock보다
    나중에 시작했다**는 것이다.
    """
    profile = str(tmp_path / "profile")
    live = os.getpid()
    _mk_lock(profile, live)
    lock_mtime = os.lstat(os.path.join(profile, "SingletonLock")).st_mtime

    # cmdline은 우리 프로필과 완전히 일치(위조 성공 상황) — 그래도 거부돼야 한다.
    monkeypatch.setattr(fetcher.subprocess, "run",
                        lambda argv, *_a, **_k: _R(f"{live}\n") if argv[0] == "lsof"
                        else _R(f"chrome --user-data-dir={profile}\n"))
    # 프로세스가 lock보다 1시간 늦게 시작 = PID 재사용
    monkeypatch.setattr(fetcher, "_proc_start_epoch", lambda _p: lock_mtime + 3600)
    assert fetcher._profile_owner_pid(profile) is None
    assert fetcher._port_owner_foreign(9299, profile) is True
    # 정상: lock보다 먼저 시작한 프로세스는 통과
    monkeypatch.setattr(fetcher, "_proc_start_epoch", lambda _p: lock_mtime - 1)
    assert fetcher._profile_owner_pid(profile) == live
    # 시작 시각 확인 불가 → 재사용을 배제할 수 없으므로 거부
    monkeypatch.setattr(fetcher, "_proc_start_epoch", lambda _p: None)
    assert fetcher._profile_owner_pid(profile) is None


def test_proc_start_epoch_parses_ps_etime(fetcher, monkeypatch):
    """macOS ps etime([[dd-]hh:]mm:ss) 파싱 — etimes(초)는 macOS에 없다."""
    import time as _t

    for etime, secs in (("00:03", 3), ("05:09", 309), ("01:02:03", 3723), ("01-23:04:49", 169489)):
        monkeypatch.setattr(fetcher.subprocess, "run", lambda *_a, _e=etime, **_k: _R(_e + "\n"))
        got = fetcher._proc_start_epoch(1234)
        assert abs((_t.time() - secs) - got) < 2, etime
    monkeypatch.setattr(fetcher.subprocess, "run", lambda *_a, **_k: _R("garbage\n"))
    assert fetcher._proc_start_epoch(1234) is None


def test_stale_lock_pid_reuse_is_refused(fetcher, tmp_path, monkeypatch):
    """★크래시 잔재 lock의 PID가 무관한 프로세스에 재사용된 경우 adopt 거부(codex R5).

    SingletonLock이 남은 채 macOS가 그 PID를 다른 프로세스에 재배정하고, 하필 그 프로세스가
    우리 CDP 포트를 LISTEN하면 'PID 동일'만으로는 남의 리스너를 adopt하게 된다.
    → PID 생존 + 그 PID가 실제로 우리 프로필의 Chrome인지(cmdline)까지 AND로 확인한다.
    """
    profile = str(tmp_path / "profile")
    live = os.getpid()
    _mk_lock(profile, live)                  # PID는 살아있지만 우리 Chrome이 아님

    def _run(argv, *_a, **_k):
        if argv[0] == "lsof":
            return _R(f"{live}\n")          # 재사용된 PID가 포트를 LISTEN 중
        return _R("/usr/bin/some-unrelated-daemon --serve\n")   # ps: Chrome도 아니고 우리 프로필도 아님

    monkeypatch.setattr(fetcher.subprocess, "run", _run)
    monkeypatch.setattr(fetcher, "_proc_start_epoch", lambda _p: 0.0)   # 시작시각은 정상이라 가정
    assert fetcher._profile_owner_pid(profile) is None                  # cmdline 불일치로 거부
    assert fetcher._port_owner_foreign(9299, profile) is True


def test_dead_lock_pid_is_stale(fetcher, tmp_path):
    """죽은 PID의 lock은 stale — 소유자 없음으로 본다(그 자체로 adopt 거부 사유)."""
    profile = str(tmp_path / "profile")
    _mk_lock(profile, 999999)                # 존재하지 않을 PID
    assert fetcher._profile_owner_pid(profile) is None


def test_port_owner_refused_without_singleton_lock(fetcher, tmp_path, monkeypatch):
    """프로필에 SingletonLock이 없으면 소유 확인 불가 → fail-closed(설정으로만 완화)."""
    profile = str(tmp_path / "noprofile")
    monkeypatch.setattr(fetcher.subprocess, "run", lambda *_a, **_k: None)
    assert fetcher._port_owner_foreign(9299, profile) is True
    assert fetcher._port_owner_foreign(9299, profile, True) is False


def test_profile_match_is_not_prefix_substring(fetcher):
    """'--user-data-dir=/tmp/profile'이 프로필 '/tmp/p'로 매칭되면 남의 Chrome을 adopt한다."""
    line = "/Applications/Google Chrome --user-data-dir=/tmp/profile --no-first-run"
    assert fetcher._cmdline_has_profile(line, "/tmp/profile") is True
    assert fetcher._cmdline_has_profile(line, "/tmp/p") is False, "접두 오탐 — 경계 검사 필요"
    assert fetcher._cmdline_has_profile(line, "/tmp/profile/") is True   # 끝 슬래시 정규화
    # 줄 끝 케이스
    assert fetcher._cmdline_has_profile("chrome --user-data-dir=/tmp/p", "/tmp/p") is True
    # 선행 경계(codex R3): 다른 인자 값 안에 문자열이 박혀 있으면 소유 확인으로 쳐선 안 된다.
    assert fetcher._cmdline_has_profile(
        "chrome --some-option=--user-data-dir=/tmp/p", "/tmp/p") is False
    assert fetcher._cmdline_has_profile(
        "chrome --url=http://x/?a=--user-data-dir=/tmp/p ", "/tmp/p") is False
    # 탭 구분자도 인자 경계
    assert fetcher._cmdline_has_profile("chrome\t--user-data-dir=/tmp/p", "/tmp/p") is True


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
