# test_wing_poll_fetch_error_report.py — cmd_poll이 run 실패를 prod fetch-error로 보고하는지 가드.
#   ★존재 이유(2026-07-27): claim 후 _do_run/_do_rg_run이 실패하면 플래그는 이미 clear라 prod에
#   흔적이 없어 UI가 215초 헛기다린 뒤 'Mac 응답 없음'으로 오진했다. 이 회귀를 막는다.
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
    """requests.post의 성공 응답 스텁 — raise_for_status는 no-op, _push의 200 검사도 통과."""

    status_code = 200
    text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {}


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


# ── ⑨ claim된 작업이 '예외'로 죽어도 보고(codex R2 P2) ─────────────────
#   cmd_login엔 자체 try/except가 없어 Chrome 기동 실패(_owned_chrome RuntimeError)·
#   page.goto 타임아웃이 그대로 올라온다 → 예외도 rc!=0과 똑같이 보고해야 한다.
def test_vs_run_exception_reports_fetch_error(fetcher, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)

    def _boom_do_run(_cfg, _state, login_wait_secs=0):
        raise RuntimeError("chrome_profile_busy")

    monkeypatch.setattr(fetcher, "_do_run", _boom_do_run)

    with pytest.raises(_StopPoll):   # 예외를 먹고 루프가 계속돼야 한다(sleep 도달)
        fetcher.cmd_poll(cfg)

    vs_calls = [c for c in calls if c["url"].endswith("/vendor-summary/fetch-error")]
    assert len(vs_calls) == 1
    assert vs_calls[0]["params"] == {"account_key": "COUPANG_WING1"}
    assert "chrome_profile_busy" in vs_calls[0]["json"]["error"]


def test_vs_missing_state_login_exception_reports(fetcher, tmp_path, monkeypatch):
    """세션 없음 경로: cmd_login이 Chrome 기동 실패로 예외를 던져도 보고된다."""
    cfg = _cfg_no_state(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)

    def _boom_login(_cfg, wait_secs=0):
        raise RuntimeError("cdp_not_ready")

    monkeypatch.setattr(fetcher, "cmd_login", _boom_login)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    vs_calls = [c for c in calls if c["url"].endswith("/vendor-summary/fetch-error")]
    assert len(vs_calls) == 1
    assert "cdp_not_ready" in vs_calls[0]["json"]["error"]


def test_rg_run_exception_reports_fetch_error(fetcher, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=False, rg_requested=True)
    calls = _install_recorder(fetcher, monkeypatch)

    def _boom_rg(_cfg, _state, login_wait_secs=0):
        raise RuntimeError("chrome_launch_failed")

    monkeypatch.setattr(fetcher, "_do_rg_run", _boom_rg)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    rg_calls = [c for c in calls if c["url"].endswith("/rg-settlement/fetch-error")]
    assert len(rg_calls) == 1
    assert "chrome_launch_failed" in rg_calls[0]["json"]["error"]


def test_exception_reason_prefers_last_logged_error(fetcher, tmp_path, monkeypatch):
    """예외 전에 남긴 log.error가 있으면 그것이 사유 — 행동지침이 담긴 쪽이 유용하다."""
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)

    def _boom_do_run(_cfg, _state, login_wait_secs=0):
        fetcher.log.error("프로필 점유 중이나 CDP(9222) 미응답 — 수동 Chrome 종료 필요")
        raise RuntimeError("chrome_profile_busy")

    monkeypatch.setattr(fetcher, "_do_run", _boom_do_run)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    vs_calls = [c for c in calls if c["url"].endswith("/vendor-summary/fetch-error")]
    assert len(vs_calls) == 1
    assert "수동 Chrome 종료 필요" in vs_calls[0]["json"]["error"]


# ── ⑩ net_fails 자가복구는 그대로(폴 GET 네트워크 실패만 카운트) ──────────
def test_poll_network_failure_still_counts_toward_self_restart(fetcher, tmp_path, monkeypatch):
    """_prod_refresh_status의 RequestException은 여전히 net_fails → 한도 도달 시 프로세스 종료."""
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(fetcher, "_MAX_CONSECUTIVE_NET_FAILS", 1)

    def _boom_status(_cfg):
        raise fetcher.requests.RequestException("Max retries exceeded")

    monkeypatch.setattr(fetcher, "_prod_refresh_status", _boom_status)
    monkeypatch.setattr(fetcher.time, "sleep", lambda _s: (_ for _ in ()).throw(_StopPoll()))

    assert fetcher.cmd_poll(cfg) == 1   # sleep 도달 전 즉시 종료 → launchd fresh 재기동


def test_claimed_run_exception_does_not_count_as_net_fail(fetcher, tmp_path, monkeypatch):
    """claim된 작업의 예외는 폴 GET 실패가 아니다 — 자가복구 카운터를 건드리지 않는다."""
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(fetcher, "_MAX_CONSECUTIVE_NET_FAILS", 1)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    _install_recorder(fetcher, monkeypatch)

    def _boom_do_run(_cfg, _state, login_wait_secs=0):
        raise fetcher.requests.RequestException("push 도중 네트워크 오류")

    monkeypatch.setattr(fetcher, "_do_run", _boom_do_run)

    with pytest.raises(_StopPoll):   # 종료(rc=1)가 아니라 루프 계속
        fetcher.cmd_poll(cfg)


# ── ⑪ 로그인은 됐지만 응답 파싱 실패도 보고(codex R3 [P2]) ─────────────────
#   _is_success는 saleSummaryByDate '키 존재'만 보므로 값이 list가 아니면 로그인은 감지되고
#   _summarize만 None이 된다. 예전엔 cmd_login이 조용히 0을 돌려 claim된 회차가 성공도 실패도
#   보고하지 않았다 → UI 215초 헛기다림 후 'Mac 응답 없음' 오진. cmd_login 실제 코드로 태운다.
class _PageStub:
    url = "https://wing.coupang.com/tenants/seller-web/"

    def goto(self, *_a, **_k) -> None:
        return None

    def wait_for_timeout(self, *_a, **_k) -> None:
        return None


@contextlib.contextmanager
def _noop_cm(*_a, **_k):
    yield None


def _patch_login_browser(fetcher, monkeypatch, body: str):
    """cmd_login의 브라우저 층만 스텁 — _summarize/분기 판정은 실제 코드가 돈다."""
    monkeypatch.setattr(fetcher, "sync_playwright", _noop_cm)

    @contextlib.contextmanager
    def _fake_chrome(*_a, **_k):
        yield (_PageStub(), object(), lambda: None)

    monkeypatch.setattr(fetcher, "_chrome", _fake_chrome)
    monkeypatch.setattr(
        fetcher, "_login_wait_loop",
        lambda *_a, **_k: {"status": 200, "body": body},   # 로그인은 감지됨(state 저장 완료)
    )


def test_login_unparseable_response_reports_fetch_error(fetcher, tmp_path, monkeypatch):
    cfg = _cfg_no_state(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)
    # 200 JSON + saleSummaryByDate 키는 있으나 list가 아님 → _is_success True / _summarize None
    _patch_login_browser(fetcher, monkeypatch, '{"saleSummaryByDate": {"oops": 1}}')

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    vs_calls = [c for c in calls if c["url"].endswith("/vendor-summary/fetch-error")]
    assert len(vs_calls) == 1, "파싱 실패가 침묵하면 UI가 'Mac 응답 없음'으로 오진한다"
    assert "파싱 실패" in vs_calls[0]["json"]["error"]
    # ingest push는 없었다(보낼 데이터가 없으므로)
    assert not [c for c in calls if c["url"].endswith("/vendor-summary/ingest")]


def test_login_parseable_response_pushes_and_reports_nothing(fetcher, tmp_path, monkeypatch):
    """같은 경로의 정상 응답: push(=heartbeat)만 나가고 fetch-error는 없다."""
    cfg = _cfg_no_state(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)
    _patch_login_browser(
        fetcher, monkeypatch,
        '{"saleSummaryByDate": [{"date": "2026-07-26", "registrationType": "RFM",'
        ' "gmv": 1000, "unitsSold": 2}]}',
    )

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    assert len([c for c in calls if c["url"].endswith("/vendor-summary/ingest")]) == 1
    assert not [c for c in calls if c["url"].endswith("/vendor-summary/fetch-error")]


def test_cmd_login_returns_1_on_unparseable_response(fetcher, tmp_path, monkeypatch):
    """CLI 'login' 단독 호출도 nonzero — 세션은 저장됐어도 데이터가 없으면 사람이 알아야 한다."""
    cfg = _cfg_no_state(tmp_path)
    _patch_login_browser(fetcher, monkeypatch, '{"saleSummaryByDate": null}')
    monkeypatch.setattr(fetcher.requests, "post", lambda *_a, **_k: _RespStub())

    assert fetcher.cmd_login(cfg, wait_secs=1) == 1


# ── ⑫ RG '정산주기 0개'는 완주 → 성공 전용 신호로 보고(codex R4 [P2]) ──
#   업로드가 없으면 heartbeat(upload-xlsx→rg_mark_heartbeat)가 안 움직이고, claim은 이미
#   요청을 소비했다 → 침묵하면 UI가 215초 헛기다린 뒤 'Mac 응답 없음' 오진.
#   R3처럼 fetch-error에 '실패 아님' 문구를 실어 보내도 프론트는 last_error_at 변화를 전부
#   실패로 읽어 ❌ 실패로 렌더한다 → 소비자가 구분할 수 있는 fetch-success로 보고한다.
def test_rg_no_periods_reports_fetch_success(fetcher, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=False, rg_requested=True)
    calls = _install_recorder(fetcher, monkeypatch)
    monkeypatch.setattr(
        fetcher, "_do_rg_run",
        lambda _cfg, _state, login_wait_secs=0: fetcher._RG_RC_NO_PERIODS,
    )

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    ok_calls = [c for c in calls if c["url"].endswith("/rg-settlement/fetch-success")]
    assert len(ok_calls) == 1
    assert ok_calls[0]["url"] == "http://prod.test/api/coupang/ops/wing/rg-settlement/fetch-success"
    assert ok_calls[0]["params"] == {"account_key": "COUPANG_WING1"}
    assert ok_calls[0]["headers"]["X-Ingest-Token"] == "tok"
    # ★실패로 보고하지 않는다 — 완주한 무작업이 ❌로 렌더되는 것이 R4 지적의 핵심.
    assert not [c for c in calls if c["url"].endswith("/rg-settlement/fetch-error")]


def test_rg_no_periods_success_report_failure_does_not_kill_daemon(fetcher, tmp_path, monkeypatch):
    """성공 보고 POST가 네트워크로 죽어도 데몬은 살아서 루프를 계속한다(보고는 best-effort)."""
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=False, rg_requested=True)
    monkeypatch.setattr(
        fetcher, "_do_rg_run",
        lambda _cfg, _state, login_wait_secs=0: fetcher._RG_RC_NO_PERIODS,
    )

    def _boom(*_a, **_k):
        raise fetcher.requests.RequestException("network down")

    monkeypatch.setattr(fetcher.requests, "post", _boom)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)


def test_rg_success_reports_nothing(fetcher, tmp_path, monkeypatch):
    """업로드 성공(rc=0)이면 heartbeat가 이미 움직였다 — 보고 없음."""
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=False, rg_requested=True)
    calls = _install_recorder(fetcher, monkeypatch)
    monkeypatch.setattr(fetcher, "_do_rg_run", lambda _cfg, _state, login_wait_secs=0: 0)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    assert calls == []


def test_rg_no_periods_return_code_is_distinct_from_failure(fetcher):
    """계약 고정: 3은 실패(1·2)·성공(0)과 겹치지 않는다."""
    assert fetcher._RG_RC_NO_PERIODS == 3
    assert fetcher._RG_RC_NO_PERIODS not in (0, 1, 2)
