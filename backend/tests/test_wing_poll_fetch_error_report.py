# test_wing_poll_fetch_error_report.py — cmd_poll이 run 실패를 prod fetch-error로 보고하는지 가드.
#   ★존재 이유(2026-07-27): claim 후 _do_run/_do_rg_run이 실패하면 플래그는 이미 clear라 prod에
#   흔적이 없어 UI가 215초 헛기다린 뒤 'Mac 응답 없음'으로 오진했다. 이 회귀를 막는다.
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


def _load(name: str):
    """tools/<name>.py를 독립 모듈로 로드(HOME은 호출자가 tmp로 격리)."""
    _ensure_playwright_stub()
    spec = importlib.util.spec_from_file_location(f"_tool_{name}", TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def fetcher(tmp_path_factory):
    """wing_browser_fetcher를 로드. import 시점에 로그파일을 만들므로 HOME을 tmp로 격리한다."""
    home = tmp_path_factory.mktemp("home")
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        yield _load("wing_browser_fetcher")
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home


class _StopPoll(Exception):
    """time.sleep(interval)까지 도달했다는 표식 — cmd_poll은 무한루프라 이걸로 탈출시킨다."""


class _RespStub:
    """requests.post의 성공 응답 스텁 — raise_for_status는 no-op."""

    def raise_for_status(self) -> None:
        return None


def _cfg(tmp_path) -> dict:
    state_file = tmp_path / "state.json"
    state_file.write_text("{}", encoding="utf-8")  # 존재해야 _do_run 분기(cmd_login 아님)를 탄다
    return {
        "account_key": "COUPANG_WING1",
        "prod_base_url": "http://prod.test",
        "ingest_token": "tok",
        "state_file": str(state_file),
    }


def _cfg_no_state(tmp_path) -> dict:
    """state 파일이 '없는' 설정 — 세션 없음 분기(cmd_login / RG 스킵)를 태운다."""
    cfg = _cfg(tmp_path)
    Path(cfg["state_file"]).unlink()
    return cfg


def _patch_common(fetcher, monkeypatch, *, vs_requested: bool, rg_requested: bool):
    monkeypatch.setattr(fetcher, "_prod_refresh_status", lambda _cfg: {"requested": vs_requested})
    monkeypatch.setattr(fetcher, "_prod_claim", lambda _cfg: {"claimed": vs_requested})
    monkeypatch.setattr(fetcher, "_prod_rg_refresh_status", lambda _cfg: {"requested": rg_requested})
    monkeypatch.setattr(fetcher, "_prod_rg_claim", lambda _cfg: {"claimed": rg_requested})
    monkeypatch.setattr(fetcher.time, "sleep", lambda _s: (_ for _ in ()).throw(_StopPoll()))


def _install_recorder(fetcher, monkeypatch):
    """모듈이 로드한 requests 객체의 post만 패치 — 함수레벨 패치 후엔 이 recorder만 남는다."""
    calls: list[dict] = []

    def _post(url, params=None, headers=None, json=None, timeout=None):  # noqa: A002
        calls.append({"url": url, "params": params, "headers": headers, "json": json})
        return _RespStub()

    monkeypatch.setattr(fetcher.requests, "post", _post)
    return calls


# ── ① VS 실패 보고 ──────────────────────────────────────────────────
def test_vs_failure_reports_fetch_error(fetcher, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)

    def _fake_do_run(_cfg, _state, login_wait_secs=0):
        fetcher.log.error("가짜 원인 XYZ status=403")
        return 1

    monkeypatch.setattr(fetcher, "_do_run", _fake_do_run)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    vs_calls = [c for c in calls if c["url"].endswith("/vendor-summary/fetch-error")]
    assert len(vs_calls) == 1
    call = vs_calls[0]
    assert call["params"] == {"account_key": "COUPANG_WING1"}
    assert call["headers"]["X-Ingest-Token"] == "tok"
    assert "가짜 원인 XYZ" in call["json"]["error"]


# ── ② RG 실패 보고 ──────────────────────────────────────────────────
def test_rg_failure_reports_fetch_error(fetcher, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=False, rg_requested=True)
    calls = _install_recorder(fetcher, monkeypatch)

    def _fake_do_rg_run(_cfg, _state, login_wait_secs=0):
        fetcher.log.error("RG 가짜 원인 ABC")
        return 1

    monkeypatch.setattr(fetcher, "_do_rg_run", _fake_do_rg_run)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    rg_calls = [c for c in calls if c["url"].endswith("/rg-settlement/fetch-error")]
    assert len(rg_calls) == 1
    call = rg_calls[0]
    assert call["params"] == {"account_key": "COUPANG_WING1"}
    assert call["headers"]["X-Ingest-Token"] == "tok"
    assert "RG 가짜 원인 ABC" in call["json"]["error"]


# ── ③ 성공 시 보고 없음 ──────────────────────────────────────────────
def test_success_reports_nothing(fetcher, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)
    monkeypatch.setattr(fetcher, "_do_run", lambda _cfg, _state, login_wait_secs=0: 0)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    assert calls == []


# ── ④ 사유는 300자로 절단 ────────────────────────────────────────────
def test_reason_is_truncated(fetcher, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)

    long_msg = "가" * 1000

    def _fake_do_run(_cfg, _state, login_wait_secs=0):
        fetcher.log.error(long_msg)
        return 1

    monkeypatch.setattr(fetcher, "_do_run", _fake_do_run)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    vs_calls = [c for c in calls if c["url"].endswith("/vendor-summary/fetch-error")]
    assert len(vs_calls) == 1
    assert len(vs_calls[0]["json"]["error"]) <= 300


# ── ⑤ 보고 실패가 데몬을 죽이지 않는다 ────────────────────────────────
def test_report_failure_does_not_kill_daemon(fetcher, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    monkeypatch.setattr(fetcher, "_do_run", lambda _cfg, _state, login_wait_secs=0: 1)

    def _boom(*_a, **_k):
        raise fetcher.requests.RequestException("network down")

    monkeypatch.setattr(fetcher.requests, "post", _boom)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)


# ── ⑥ 아무 로그도 없을 때 fallback 사유 ────────────────────────────────
def test_fallback_reason_when_nothing_logged(fetcher, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)
    monkeypatch.setattr(fetcher, "_do_run", lambda _cfg, _state, login_wait_secs=0: 1)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    vs_calls = [c for c in calls if c["url"].endswith("/vendor-summary/fetch-error")]
    assert len(vs_calls) == 1
    assert vs_calls[0]["json"]["error"] == "vendor-summary run 실패 rc=1"


# ── ⑦ 세션 없음 + login 실패도 보고(codex R1 P2) ────────────────────────
def test_vs_missing_state_login_failure_reports(fetcher, tmp_path, monkeypatch):
    cfg = _cfg_no_state(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)

    def _fake_login(_cfg, wait_secs=0):
        fetcher.log.error("제한 시간 내 로그인 감지 실패 — 다시 시도하세요.")
        return 1

    monkeypatch.setattr(fetcher, "cmd_login", _fake_login)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    vs_calls = [c for c in calls if c["url"].endswith("/vendor-summary/fetch-error")]
    assert len(vs_calls) == 1
    assert vs_calls[0]["params"] == {"account_key": "COUPANG_WING1"}
    assert vs_calls[0]["headers"]["X-Ingest-Token"] == "tok"
    assert "로그인 감지 실패" in vs_calls[0]["json"]["error"]


def test_vs_missing_state_login_success_reports_nothing(fetcher, tmp_path, monkeypatch):
    """login 성공 시엔 _push가 last_success_at을 움직인다 — 별도 보고 없음."""
    cfg = _cfg_no_state(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)
    monkeypatch.setattr(fetcher, "cmd_login", lambda _cfg, wait_secs=0: 0)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    assert calls == []


# ── ⑧ RG 세션 없음 스킵도 보고(codex R1 P2) ────────────────────────────
def test_rg_missing_state_reports_fetch_error(fetcher, tmp_path, monkeypatch):
    cfg = _cfg_no_state(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=False, rg_requested=True)
    calls = _install_recorder(fetcher, monkeypatch)

    def _must_not_run(*_a, **_k):
        raise AssertionError("세션 없음인데 RG run이 실행됐다")

    monkeypatch.setattr(fetcher, "_do_rg_run", _must_not_run)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    rg_calls = [c for c in calls if c["url"].endswith("/rg-settlement/fetch-error")]
    assert len(rg_calls) == 1
    assert rg_calls[0]["params"] == {"account_key": "COUPANG_WING1"}
    assert rg_calls[0]["headers"]["X-Ingest-Token"] == "tok"
    assert "세션 파일 없음" in rg_calls[0]["json"]["error"]
