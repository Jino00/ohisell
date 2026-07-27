# test_wing_cdp_login_marker.py — CDP 모드 login이 state 마커 파일을 실제로 남기는지(P4).
#
# ★존재 이유(2026-07-27 13:54 실사고): CDP 모드에서 `cmd_login`이 "로그인 감지·세션 저장 완료"
#   로그를 남기지만 `_save_state`는 `if cdp: return`으로 완전 no-op이었다 — state_file이 생기지
#   않아 `rg`/`run`의 존재 게이트(`Path(state).is_file()`)가 매 회차 fail-fast했다. WING1은 CDP
#   전환 이전 구식 state.json 파일이 우연히 남아있어 게이트를 통과했을 뿐이고, WING2는 수동 스텁
#   (`~/.ohisell_wing2_state.json`, 30바이트)으로 임시 우회 중이었다(코드 미수정 — 재발 시한폭탄).
#
# 여기서 고정하는 것:
#   ① CDP 모드 `_save_state`는 실제로 파일을 만든다(더 이상 no-op) — 게이트가 통과한다.
#   ② 마커 내용은 legacy(비-CDP) 모드가 storage_state로 오독해도 깨지지 않는 형태
#      (cookies/origins 빈 배열 + 메타)다.
#   ③ 비-CDP 경로는 회귀 없음 — 여전히 `context.storage_state(path=...)`를 호출한다.
#   ④ 기존 수동 스텁·WING1 구식 state.json(내용이 다른 임의 파일)과 하위호환 — 게이트는 존재
#      여부만 보므로 그 파일들도 그대로 통과한다(이 테스트는 새 마커 생성 쪽만 고정).
import importlib.util
import json
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


@pytest.fixture(scope="module")
def wing(tmp_path_factory):
    """tools/wing_browser_fetcher.py를 독립 로드(HOME은 tmp로 격리 — import 시 로그파일 생성)."""
    _ensure_playwright_stub()
    home = tmp_path_factory.mktemp("home")
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        spec = importlib.util.spec_from_file_location(
            "_tool_wing_cdp_login_marker", TOOLS / "wing_browser_fetcher.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home


class _FakeContext:
    """레거시 경로가 부르는 context.storage_state(path=...)만 흉내."""

    def __init__(self):
        self.storage_state_calls: list[str] = []

    def storage_state(self, path: str) -> None:
        self.storage_state_calls.append(path)
        Path(path).write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")


# ── ① CDP 모드: 마커 파일이 실제로 생긴다 ────────────────────────────────
def test_cdp_save_state_creates_marker_file(wing, tmp_path):
    state = tmp_path / "wing2_state.json"
    assert not state.is_file()
    ctx = _FakeContext()

    wing._save_state(ctx, str(state), cdp=True)

    assert state.is_file()                    # ★게이트(Path(state).is_file()) 통과
    assert ctx.storage_state_calls == []       # CDP 모드는 진짜 storage_state를 호출하지 않는다


def test_cdp_marker_content_is_legacy_safe(wing, tmp_path):
    """마커 포맷은 legacy 모드가 storage_state로 오독해도 안전해야 한다(빈 cookies/origins)."""
    state = tmp_path / "wing2_state.json"
    wing._save_state(None, str(state), cdp=True)

    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["cookies"] == []
    assert data["origins"] == []
    assert data.get("cdp_marker") is True
    assert "logged_in_at" in data


def test_cdp_marker_file_permission_is_0600(wing, tmp_path):
    state = tmp_path / "wing2_state.json"
    wing._save_state(None, str(state), cdp=True)
    mode = state.stat().st_mode & 0o777
    assert mode == 0o600


# ── ② 비-CDP(레거시) 경로는 회귀 없음 — 여전히 context.storage_state를 호출 ──────
def test_legacy_save_state_still_calls_context_storage_state(wing, tmp_path):
    state = tmp_path / "wing1_state.json"
    ctx = _FakeContext()

    wing._save_state(ctx, str(state), cdp=False)

    assert ctx.storage_state_calls == [str(state)]
    assert state.is_file()


def test_legacy_is_default_mode(wing, tmp_path):
    """cdp 인자를 생략하면 기존(비-CDP) 동작 — 하위호환."""
    state = tmp_path / "default_state.json"
    ctx = _FakeContext()

    wing._save_state(ctx, str(state))

    assert ctx.storage_state_calls == [str(state)]


# ── ③ 게이트 자체: 로그인 성공 경로가 마커를 남기고 게이트를 통과시키는지 ────────
def test_login_wait_loop_cdp_success_leaves_gate_passable(wing, tmp_path):
    """_login_wait_loop 성공 경로 = cmd_login의 실제 저장 지점. 마커가 남아야 rg/run 게이트 통과."""
    state = tmp_path / "wing2_state.json"

    class _Page:
        def __init__(self):
            self.url = "https://m-wing.coupang.com/tenants/business-insight/sales-analysis"
            self._n = 0

        def wait_for_timeout(self, _ms):
            return None

        def evaluate(self, _js, _args):
            return {"status": 200, "body": json.dumps({
                "saleSummaryByDate": [
                    {"date": "2026-07-27", "registrationType": "NORMAL", "gmv": 100, "unitsSold": 1},
                ],
                "summaryMetrics": {"totalGmv": 100},
            })}

    ctx = _FakeContext()
    res = wing._login_wait_loop(_Page(), ctx, {}, str(state), wait_secs=5, cdp=True)

    assert res is not None
    assert state.is_file()                     # ★핵심: 이제 rg/run 게이트가 통과한다
    assert ctx.storage_state_calls == []        # CDP 모드는 진짜 storage_state를 안 씀


# ── ④ codex 1R[P2]: 마커 저장이 조용히 실패하면 성공으로 리턴하지 않는다 ──────────
class _AlwaysOkPage:
    """vendor-summary 프로브는 매 회차 성공(로그인은 이미 됐다는 가정) — 마커 저장만 문제."""

    url = "https://m-wing.coupang.com/tenants/business-insight/sales-analysis"

    def wait_for_timeout(self, _ms):
        return None

    def evaluate(self, _js, _args):
        return {"status": 200, "body": json.dumps({
            "saleSummaryByDate": [
                {"date": "2026-07-27", "registrationType": "NORMAL", "gmv": 100, "unitsSold": 1},
            ],
            "summaryMetrics": {"totalGmv": 100},
        })}


def test_login_wait_loop_retries_when_marker_write_silently_fails(wing, tmp_path, monkeypatch):
    """codex 1R[P2]: _save_state가 OSError를 삼켜도(디스크 풀 등) 파일이 안 생겼으면 성공
    처리하면 안 된다 — 로그인은 됐으니(vendor-summary 200) 남은 시간 동안 저장만 재시도한다."""
    state = tmp_path / "wing2_state.json"
    calls = {"n": 0}

    def flaky_save_state(_ctx, path, *, cdp=False):
        calls["n"] += 1
        if calls["n"] < 3:
            return  # 마커 저장 실패를 흉내(파일 미생성) — _save_state의 실제 OSError 삼킴과 동일 관측
        Path(path).write_text("{}", encoding="utf-8")

    monkeypatch.setattr(wing, "_save_state", flaky_save_state)

    res = wing._login_wait_loop(_AlwaysOkPage(), object(), {}, str(state), wait_secs=60, cdp=True)

    assert res is not None
    assert calls["n"] == 3           # 두 번은 저장 실패로 재시도, 세 번째에 성공
    assert state.is_file()


def test_login_wait_loop_gives_up_if_marker_never_appears(wing, tmp_path, monkeypatch):
    """저장이 끝까지 실패하면(디스크가 계속 안 됨) 거짓 성공 대신 timeout(None)으로 떨어진다."""
    state = tmp_path / "wing2_state.json"

    def never_saves(_ctx, _path, *, cdp=False):
        return  # 파일을 절대 안 만든다

    monkeypatch.setattr(wing, "_save_state", never_saves)

    res = wing._login_wait_loop(_AlwaysOkPage(), object(), {}, str(state), wait_secs=10, cdp=True)

    assert res is None
    assert not state.is_file()


def test_rg_login_wait_retries_when_marker_write_silently_fails(wing, tmp_path, monkeypatch):
    """_rg_login_wait도 같은 수리 대상(codex 1R[P2]) — RG 로그인 회복 경로."""
    state = tmp_path / "wing2_state.json"
    calls = {"n": 0}

    def flaky_save_state(_ctx, path, *, cdp=False):
        calls["n"] += 1
        if calls["n"] < 2:
            return
        Path(path).write_text("{}", encoding="utf-8")

    monkeypatch.setattr(wing, "_save_state", flaky_save_state)

    class _RgOkPage:
        def wait_for_timeout(self, _ms):
            return None

    monkeypatch.setattr(wing, "_rg_session_ok", lambda _page: True)

    ok = wing._rg_login_wait(_RgOkPage(), object(), str(state), secs=60, cdp=True)

    assert ok is True
    assert calls["n"] == 2
    assert state.is_file()
