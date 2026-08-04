# test_wing_poll_fetch_error_report.py — Wing poll 데몬의 "claim된 작업은 반드시 종단 신호를
#   남긴다" 가드(R1~R3, 2026-07-27).
#
# ★존재 이유: lease 계약(PR #106)은 claim이 요청 플래그를 소비하지 않게 바꿔 재시도 3회를
#   보장했지만, **아무 보고도 없이 조용히 끝나는 경로**는 여전히 남아 있었다:
#     ① claim된 작업이 예외로 폴 루프 외곽 핸들러까지 탈출 (cmd_login은 try/except가 없고,
#        _do_run/_do_rg_run도 브라우저 블록 밖은 자체 try에 안 덮인다)
#     ② cmd_login이 "로그인은 됐지만 응답 파싱 실패"에서 rc=0을 돌려줌
#   둘 다 임대가 TTL(기본 20분)까지 살아있고 그 뒤 재claim → 3회 소진까지 40~60분이 걸린다.
#   UI는 215초에 포기하므로 그동안 "⚠️ Mac 응답 없음"(Mac 꺼짐/미설치)으로 **오진**하고,
#   창은 3번 뜬다. 이 파일은 두 경로가 즉시 fetch-error를 남기는지 고정한다.
#
# ★함께 고정하는 것:
#   - 보고 사유 = 마지막 log.error(실제 원인)여야 한다. "…실패(rc=1)"만 보내면 UI에 원인이 안 뜬다.
#   - kind=login_required·lease 전달은 회귀 금지(PR #106의 소멸/재시도 판정 입력).
#   - account_key는 모든 보고에 실려야 한다 — WING2 보고가 WING1 행으로 가면 lease 불일치로
#     조용히 버려진다(refresh_contract의 'stale 실패 보고 무시').
#   (브라우저 실동작은 여기서 검증 불가 — 라이브 검증은 운영 절차의 몫.)
import contextlib
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import CoupangWingCookie
from app.services.coupang import rg_settlement_sync

TOOLS = Path(__file__).resolve().parents[2] / "tools"
_TOKEN = "test-token-123"


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


def _cfg(tmp_path, account_key: str = "COUPANG_WING1") -> dict:
    state_file = tmp_path / "state.json"
    state_file.write_text("{}", encoding="utf-8")  # 존재해야 _do_run 분기(cmd_login 아님)를 탄다
    return {
        "account_key": account_key,
        "prod_base_url": "http://prod.test",
        "ingest_token": "tok",
        "state_file": str(state_file),
    }


def _cfg_no_state(tmp_path, account_key: str = "COUPANG_WING1") -> dict:
    """state 파일이 '없는' 설정 — 세션 없음 분기(cmd_login / RG 스킵)를 태운다."""
    cfg = _cfg(tmp_path, account_key)
    Path(cfg["state_file"]).unlink()
    return cfg


_LEASE = "2026-07-27T10:00:00"


def _patch_common(fetcher, monkeypatch, *, vs_requested: bool, rg_requested: bool):
    """폴의 HTTP GET/claim 층만 스텁 — 판정·보고 분기는 실제 코드가 돈다."""
    monkeypatch.setattr(fetcher, "_prod_refresh_status", lambda _cfg: {"requested": vs_requested})
    monkeypatch.setattr(
        fetcher, "_prod_claim", lambda _cfg: {"claimed": vs_requested, "lease": _LEASE})
    monkeypatch.setattr(fetcher, "_prod_rg_refresh_status", lambda _cfg: {"requested": rg_requested})
    monkeypatch.setattr(
        fetcher, "_prod_rg_claim", lambda _cfg: {"claimed": rg_requested, "lease": _LEASE})
    monkeypatch.setattr(fetcher.time, "sleep", lambda _s: (_ for _ in ()).throw(_StopPoll()))


def _install_recorder(fetcher, monkeypatch):
    """모듈이 로드한 requests 객체의 post만 패치 — 함수레벨 패치 후엔 이 recorder만 남는다."""
    calls: list[dict] = []

    def _post(url, params=None, headers=None, json=None, timeout=None):  # noqa: A002
        calls.append({"url": url, "params": params, "headers": headers, "json": json})
        return _RespStub()

    monkeypatch.setattr(fetcher.requests, "post", _post)
    return calls


def _only(calls: list[dict], suffix: str) -> dict:
    """해당 엔드포인트로 나간 호출이 정확히 1건임을 확인하고 그것을 돌려준다."""
    hits = [c for c in calls if c["url"].endswith(suffix)]
    assert len(hits) == 1, f"{suffix} 호출 {len(hits)}건 (기대 1건): {[c['url'] for c in calls]}"
    return hits[0]


def _none(calls: list[dict], suffix: str) -> None:
    assert not [c for c in calls if c["url"].endswith(suffix)], f"{suffix}가 나가면 안 된다"


# ════════════════════════════════════════════════════════════════════
# R1-a: claim된 작업의 예외도 반드시 보고된다 (조용한 탈출 금지)
# ════════════════════════════════════════════════════════════════════
def test_vs_run_exception_reports_fetch_error(fetcher, tmp_path, monkeypatch):
    """_do_run이 던진 예외 — 보고 없이 탈출하면 임대가 20분 묶여 UI가 'Mac 응답 없음' 오진."""
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)

    def _boom(_cfg, _state, login_wait_secs=0):
        raise RuntimeError("chrome_launch_failed")

    monkeypatch.setattr(fetcher, "_do_run", _boom)

    with pytest.raises(_StopPoll):   # 데몬은 죽지 않는다(루프 계속)
        fetcher.cmd_poll(cfg)

    call = _only(calls, "/vendor-summary/fetch-error")
    assert "chrome_launch_failed" in call["json"]["error"]
    # 예외는 '로그인 필요'가 아니다 — 재시도 대상으로 남겨야 한다(kind 없음)
    assert "kind" not in call["json"]


def test_vs_missing_state_login_exception_reports(fetcher, tmp_path, monkeypatch):
    """세션 없음 분기의 cmd_login은 자체 try/except가 없다 — Chrome 기동 실패가 그대로 올라온다."""
    cfg = _cfg_no_state(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)

    def _boom(_cfg, wait_secs=0, rg_probe=True):   # rg_probe=데몬 VS 레인은 False로 부른다
        raise RuntimeError("chrome_profile_busy")

    monkeypatch.setattr(fetcher, "cmd_login", _boom)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    assert "chrome_profile_busy" in _only(calls, "/vendor-summary/fetch-error")["json"]["error"]


def test_rg_run_exception_reports_fetch_error(fetcher, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=False, rg_requested=True)
    calls = _install_recorder(fetcher, monkeypatch)

    def _boom(_cfg, _state, login_wait_secs=0):
        raise RuntimeError("cdp_not_ready")

    monkeypatch.setattr(fetcher, "_do_rg_run", _boom)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    assert "cdp_not_ready" in _only(calls, "/rg-settlement/fetch-error")["json"]["error"]
    # 완주 신호는 나가면 안 된다 — 실패한 run이 요청을 소멸시키면 재시도가 사라진다
    _none(calls, "/rg-settlement/refresh-complete")


def test_claimed_run_exception_does_not_count_as_net_fail(fetcher, tmp_path, monkeypatch):
    """claim된 작업의 예외는 폴 GET 실패가 아니다 — 자가재기동 카운터를 건드리지 않는다."""
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(fetcher, "_MAX_CONSECUTIVE_NET_FAILS", 1)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)

    def _boom(_cfg, _state, login_wait_secs=0):
        raise fetcher.requests.RequestException("push 도중 네트워크 오류")

    monkeypatch.setattr(fetcher, "_do_run", _boom)

    with pytest.raises(_StopPoll):   # 프로세스 종료(rc=1)가 아니라 루프 계속
        fetcher.cmd_poll(cfg)

    # 그리고 침묵하지 않는다 — 보고는 나간다
    assert "네트워크 오류" in _only(calls, "/vendor-summary/fetch-error")["json"]["error"]


def test_poll_network_failure_still_counts_toward_self_restart(fetcher, tmp_path, monkeypatch):
    """대조군: 폴 GET의 RequestException은 여전히 net_fails → 한도 도달 시 프로세스 종료."""
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(fetcher, "_MAX_CONSECUTIVE_NET_FAILS", 1)

    def _boom_status(_cfg):
        raise fetcher.requests.RequestException("Max retries exceeded")

    monkeypatch.setattr(fetcher, "_prod_refresh_status", _boom_status)
    monkeypatch.setattr(fetcher.time, "sleep", lambda _s: (_ for _ in ()).throw(_StopPoll()))

    assert fetcher.cmd_poll(cfg) == 1   # sleep 도달 전 즉시 종료 → launchd fresh 재기동


# ════════════════════════════════════════════════════════════════════
# R1-b: 보고 사유는 실제 원인(마지막 log.error)이어야 한다
# ════════════════════════════════════════════════════════════════════
def test_vs_failure_reason_is_actual_log_error(fetcher, tmp_path, monkeypatch):
    """rc만 보내면 UI가 '판매분석 수집 실패(rc=1)'를 띄운다 — 사람이 할 행동을 알 수 없다."""
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)

    def _fail(_cfg, _state, login_wait_secs=0):
        fetcher.log.error("vendor-summary 실패 status=403 — 'login' 재실행 필요.")
        return 1

    monkeypatch.setattr(fetcher, "_do_run", _fail)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    err = _only(calls, "/vendor-summary/fetch-error")["json"]["error"]
    assert "status=403" in err and "login" in err
    assert "rc=" not in err   # 일반 문구로 덮이지 않았다


def test_rg_failure_reason_is_actual_log_error(fetcher, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=False, rg_requested=True)
    calls = _install_recorder(fetcher, monkeypatch)

    def _fail(_cfg, _state, login_wait_secs=0):
        fetcher.log.error("RG status/api 응답 비정상(200/JSON 아님) — 세션·챌린지 확인.")
        return 1

    monkeypatch.setattr(fetcher, "_do_rg_run", _fail)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    assert "응답 비정상" in _only(calls, "/rg-settlement/fetch-error")["json"]["error"]


def test_reason_falls_back_to_generic_when_nothing_logged(fetcher, tmp_path, monkeypatch):
    """log.error 없이 실패한 경로 — 사유가 비어 침묵하는 대신 일반 문구로 보고한다."""
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)
    monkeypatch.setattr(fetcher, "_do_run", lambda _c, _s, login_wait_secs=0: 1)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    assert _only(calls, "/vendor-summary/fetch-error")["json"]["error"] == "판매분석 수집 실패(rc=1)"


def test_exception_reason_prefers_last_logged_error(fetcher, tmp_path, monkeypatch):
    """예외 직전에 남긴 log.error가 예외 텍스트보다 구체적이다 — 그쪽을 사유로 쓴다."""
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)

    def _fail(_cfg, _state, login_wait_secs=0):
        fetcher.log.error("정산 페이지 same-origin 200 미확인 — 쿠키 커버리지 의심")
        raise RuntimeError("Timeout 40000ms exceeded")

    monkeypatch.setattr(fetcher, "_do_run", _fail)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    err = _only(calls, "/vendor-summary/fetch-error")["json"]["error"]
    assert "same-origin 200 미확인" in err
    # 래퍼가 남기는 자기 로그("claim된 작업 예외 …")가 사유를 덮어써선 안 된다
    assert "claim된 작업 예외" not in err


# ════════════════════════════════════════════════════════════════════
# R1-c: PR #106의 계약(kind·lease·성공 침묵)은 회귀 금지
# ════════════════════════════════════════════════════════════════════
def test_login_required_still_sends_kind(fetcher, tmp_path, monkeypatch):
    """RC_LOGIN_REQUIRED는 재시도 제외 신호 — 사유를 실제 원인으로 바꿔도 kind는 유지된다."""
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)
    monkeypatch.setattr(
        fetcher, "_do_run", lambda _c, _s, login_wait_secs=0: fetcher.RC_LOGIN_REQUIRED)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    assert _only(calls, "/vendor-summary/fetch-error")["json"]["kind"] == "login_required"


def test_lease_is_forwarded_in_report(fetcher, tmp_path, monkeypatch):
    """claim 응답의 lease를 되돌려줘야 prod가 stale 보고를 걸러낼 수 있다."""
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)
    monkeypatch.setattr(fetcher, "_do_run", lambda _c, _s, login_wait_secs=0: 1)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    assert _only(calls, "/vendor-summary/fetch-error")["json"]["lease"] == _LEASE


def test_rg_missing_state_still_reports_login_required(fetcher, tmp_path, monkeypatch):
    """세션 파일 없음 = 확증된 로그인 필요 → 재시도 없이 소멸(창만 3번 뜨는 것 방지)."""
    cfg = _cfg_no_state(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=False, rg_requested=True)
    calls = _install_recorder(fetcher, monkeypatch)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    call = _only(calls, "/rg-settlement/fetch-error")
    assert call["json"]["kind"] == "login_required"
    assert call["json"]["lease"] == _LEASE


def test_vs_success_reports_nothing(fetcher, tmp_path, monkeypatch):
    """성공은 push(=heartbeat)가 알린다 — fetch-error가 나가면 UI가 ❌로 렌더한다."""
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)
    monkeypatch.setattr(fetcher, "_do_run", lambda _c, _s, login_wait_secs=0: 0)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    assert calls == []


def test_rg_success_notifies_complete_only(fetcher, tmp_path, monkeypatch):
    """RG 정상 완주 → refresh-complete로 요청 소멸. fetch-error는 없다."""
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=False, rg_requested=True)
    calls = _install_recorder(fetcher, monkeypatch)
    monkeypatch.setattr(fetcher, "_do_rg_run", lambda _c, _s, login_wait_secs=0: 0)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    assert _only(calls, "/rg-settlement/refresh-complete")["json"]["lease"] == _LEASE
    _none(calls, "/rg-settlement/fetch-error")


def test_report_failure_does_not_kill_daemon(fetcher, tmp_path, monkeypatch):
    """보고 자체가 실패해도 데몬은 계속 돈다 — 보고는 best-effort다."""
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    monkeypatch.setattr(fetcher, "_do_run", lambda _c, _s, login_wait_secs=0: 1)

    def _boom_post(*_a, **_k):
        raise fetcher.requests.RequestException("prod down")

    monkeypatch.setattr(fetcher.requests, "post", _boom_post)

    with pytest.raises(_StopPoll):   # 예외가 아니라 sleep까지 도달 = 루프 생존
        fetcher.cmd_poll(cfg)


# ════════════════════════════════════════════════════════════════════
# R2: cmd_login 파싱 실패는 0이 아니다
# ════════════════════════════════════════════════════════════════════
#   _is_success는 saleSummaryByDate '키 존재'만 보므로 값이 list가 아니면 로그인은 감지되고
#   _summarize만 None이 된다. 예전엔 cmd_login이 조용히 0을 돌려 claim된 회차가 성공도 실패도
#   보고하지 않았다 → 임대 20분 + UI 215초 헛기다림 후 'Mac 응답 없음' 오진.
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

    call = _only(calls, "/vendor-summary/fetch-error")
    assert "파싱 실패" in call["json"]["error"]
    # 로그인 자체는 됐다 → 재시도 대상(kind 없음). login_required면 멀쩡한 세션을 두고 소멸한다.
    assert "kind" not in call["json"]
    _none(calls, "/vendor-summary/ingest")   # 보낼 데이터가 없었다


def test_login_parseable_response_pushes_and_reports_nothing(fetcher, tmp_path, monkeypatch):
    """같은 경로의 정상 응답: push(=heartbeat)만 나가고 fetch-error는 없다(happy path 회귀 가드)."""
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

    _only(calls, "/vendor-summary/ingest")
    _none(calls, "/vendor-summary/fetch-error")


def test_cmd_login_returns_1_on_unparseable_response(fetcher, tmp_path, monkeypatch):
    """CLI 'login' 단독 호출도 nonzero — 세션은 저장됐어도 데이터가 없으면 사람이 알아야 한다."""
    cfg = _cfg_no_state(tmp_path)
    _patch_login_browser(fetcher, monkeypatch, '{"saleSummaryByDate": null}')
    monkeypatch.setattr(fetcher.requests, "post", lambda *_a, **_k: _RespStub())

    assert fetcher.cmd_login(cfg, wait_secs=1) == 1


# ════════════════════════════════════════════════════════════════════
# R3: 모든 보고에 account_key — WING2 보고가 WING1 행으로 새면 조용히 버려진다
# ════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("account_key", ["COUPANG_WING1", "COUPANG_WING2"])
def test_fetch_error_carries_account_key(fetcher, tmp_path, monkeypatch, account_key):
    """★버그였던 것: 이 호출만 계정을 안 보내 엔드포인트 기본값(WING1) 행으로 갔다 — 거기엔 내
    lease가 없으니 prod가 'stale 실패 보고 무시'로 접어 WING2 실패가 사라졌다.
    """
    cfg = _cfg(tmp_path, account_key)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)
    monkeypatch.setattr(fetcher, "_do_run", lambda _c, _s, login_wait_secs=0: 1)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    assert _only(calls, "/vendor-summary/fetch-error")["params"] == {"account_key": account_key}


@pytest.mark.parametrize("account_key", ["COUPANG_WING1", "COUPANG_WING2"])
def test_rg_complete_carries_account_key(fetcher, tmp_path, monkeypatch, account_key):
    """★버그였던 것: 완료 신호도 계정을 안 보냈다 — WING2 요청이 안 지워져 창 3번 뒤 거짓 실패."""
    cfg = _cfg(tmp_path, account_key)
    _patch_common(fetcher, monkeypatch, vs_requested=False, rg_requested=True)
    calls = _install_recorder(fetcher, monkeypatch)
    monkeypatch.setattr(fetcher, "_do_rg_run", lambda _c, _s, login_wait_secs=0: 0)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    assert _only(calls, "/rg-settlement/refresh-complete")["params"] == {"account_key": account_key}


# ── R3 라우터측: refresh-complete가 계정별 상태행을 쓴다 ──────────────────
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AD_INGEST_TOKEN", _TOKEN)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    seed = TestingSession()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


_RG_COMPLETE_URL = "/api/coupang/ops/wing/rg-settlement/refresh-complete"


def _rg_row(db, account_key: str) -> CoupangWingCookie | None:
    return (
        db.query(CoupangWingCookie)
        .filter(CoupangWingCookie.account_key == rg_settlement_sync._rg_state_key(account_key))
        .first()
    )


def test_refresh_complete_respects_account_key(client):
    """WING2 완주 신호는 WING2 요청만 지운다 — 예전엔 WING1 행을 하드코딩해 둘 다 틀렸다."""
    c, seed = client
    leases = {}
    for ak in ("COUPANG_WING1", "COUPANG_WING2"):
        rg_settlement_sync.rg_request_refresh(seed, ak)
        leases[ak] = rg_settlement_sync.rg_claim_refresh(seed, ak)["lease"]

    r = c.post(_RG_COMPLETE_URL, params={"account_key": "COUPANG_WING2"},
               json={"lease": leases["COUPANG_WING2"]},
               headers={"X-Ingest-Token": _TOKEN})
    assert r.status_code == 200 and r.json()["ok"] is True

    seed.expire_all()
    assert _rg_row(seed, "COUPANG_WING2").refresh_requested_at is None   # 소멸
    assert _rg_row(seed, "COUPANG_WING1").refresh_requested_at is not None  # 남의 요청 불변


def test_refresh_complete_defaults_to_wing1(client):
    """하위호환 — account_key를 안 보내는 페처 호출은 WING1을 닫는다.

    ★lease는 2026-08-03부터 필수(codex 3R[P1]) — account_key 기본값만 하위호환이다.
    """
    c, seed = client
    rg_settlement_sync.rg_request_refresh(seed, "COUPANG_WING1")
    lease = rg_settlement_sync.rg_claim_refresh(seed, "COUPANG_WING1")["lease"]

    assert c.post(_RG_COMPLETE_URL).status_code == 401           # 토큰 필요(형제와 동일 규칙)
    assert c.post(_RG_COMPLETE_URL, json={"lease": lease},
                  headers={"X-Ingest-Token": _TOKEN}).status_code == 200

    seed.expire_all()
    row = _rg_row(seed, "COUPANG_WING1")
    assert row.refresh_requested_at is None
    assert row.last_success_at is None       # 신선도 시계는 불변(받은 게 없으면 데이터도 그대로)


def test_refresh_complete_rejects_unknown_account(client):
    """오타 계정은 400 — 유령 상태행이 생기면 아무도 안 보는 큐가 만들어진다."""
    c, _seed = client
    r = c.post(_RG_COMPLETE_URL, params={"account_key": "COUPANG_WING9"},
               headers={"X-Ingest-Token": _TOKEN})
    assert r.status_code == 400


# ════════════════════════════════════════════════════════════════════
# R4: 쿨다운 기준점 — 부팅 직후를 "방금 돌린 직후"로 착각하지 않는다
#   (2026-08-04 발견. 이 파일의 6건이 어떤 날은 실패하고 어떤 날은 통과해서 추적했더니
#    테스트가 아니라 데몬이 틀렸다 — 부팅 후 1시간 동안 RG 버튼이 완전히 침묵했다.)
# ════════════════════════════════════════════════════════════════════
def test_rg_consumes_the_button_right_after_boot(fetcher, tmp_path, monkeypatch):
    """★재부팅 직후에도 RG 버튼 요청은 즉시 소비돼야 한다.

    macOS에서 `time.monotonic()`은 **부팅 이후 경과초**다(실측 2026-08-04: 부팅
    11:26:03 · monotonic 21,214 ≈ uptime 5:53). 그런데 쿨다운 기준점을 `0.0`으로
    잡으면 `monotonic - 0.0 >= 3600`이 곧 "부팅 후 1시간 지났나"가 되어, 그 전에는
    **한 번도 안 돌렸는데 방금 돌린 것으로 취급**된다.

    그 결과가 이 파일이 막으려는 실패 모드 그 자체였다 — claim도 로그도 실패 보고도
    없이 침묵하고, 요청 플래그는 살아남고, UI는 215초 뒤 "Mac 응답 없음"으로 오진한다.
    가드가 있어도 **가드에 도달하지 못하면** 소용이 없다.
    """
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=False, rg_requested=True)
    calls = _install_recorder(fetcher, monkeypatch)
    monkeypatch.setattr(fetcher, "_do_rg_run", lambda _c, _s, login_wait_secs=0: 0)
    monkeypatch.setattr(fetcher.time, "monotonic", lambda: 100.0)  # 부팅 100초 뒤

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    assert _only(calls, "/rg-settlement/refresh-complete")["json"]["lease"] == _LEASE


def test_vs_fetches_right_after_boot(fetcher, tmp_path, monkeypatch):
    """판매분석 레인도 같은 기준점을 쓴다 — 쿨다운 45초라 피해는 작지만 결함은 같다."""
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=True, rg_requested=False)
    calls = _install_recorder(fetcher, monkeypatch)
    ran: list[bool] = []
    monkeypatch.setattr(fetcher, "_do_run",
                        lambda _c, _s, login_wait_secs=0: (ran.append(True), 0)[1])
    monkeypatch.setattr(fetcher.time, "monotonic", lambda: 10.0)  # 부팅 10초 뒤

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    assert ran == [True], "부팅 직후라는 이유로 fetch가 보류되면 안 된다"
    _none(calls, "/vendor-summary/fetch-error")


def test_rg_cooldown_still_holds_after_a_real_run(fetcher, tmp_path, monkeypatch):
    """★쿨다운 자체는 살아 있어야 한다 — 위 수정이 해머링 방지를 없애면 안 된다.

    한 번 돌린 뒤 같은 폴 루프의 다음 회차는 보류돼야 한다. 루프를 두 바퀴 돌리기 위해
    `time.sleep`은 첫 회차에서 통과시키고 둘째 회차에서 탈출시킨다.
    """
    cfg = _cfg(tmp_path)
    _patch_common(fetcher, monkeypatch, vs_requested=False, rg_requested=True)
    calls = _install_recorder(fetcher, monkeypatch)
    runs: list[int] = []
    monkeypatch.setattr(fetcher, "_do_rg_run",
                        lambda _c, _s, login_wait_secs=0: (runs.append(1), 0)[1])
    monkeypatch.setattr(fetcher.time, "monotonic", lambda: 100.0)  # 시간은 흐르지 않는다
    laps = {"n": 0}

    def _sleep(_s):
        laps["n"] += 1
        if laps["n"] >= 2:
            raise _StopPoll()

    monkeypatch.setattr(fetcher.time, "sleep", _sleep)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(cfg)

    assert runs == [1], f"쿨다운이 죽었다 — 두 바퀴에서 {len(runs)}회 실행됨"
    assert len([c for c in calls if c["url"].endswith("/rg-settlement/refresh-complete")]) == 1
