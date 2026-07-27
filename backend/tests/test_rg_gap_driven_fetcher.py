# test_rg_gap_driven_fetcher.py — Mac 페처 층2(옵션 엑셀) 결손 주도 선택 + 루프 세션 재확인.
#   ★존재 이유: 층2가 매 회차 '최신 1주기'만 받는 한(rg_max_periods=1) 버튼 캐던스가 정산주기
#   생성 속도보다 느려지는 순간 그 사이 주기는 영구 공백이 된다. prod에 "무엇이 비었나"를 묻고
#   결손만 받는다. 여기서 고정하는 것:
#     ① 결손만·최신 우선 ② 회차 상한(rg_max_targets) ③ 조회 실패=기존 동작 폴백(수집 불중단)
#     ④ 결손 0건이면 다운로드 0건(정상 완주) ⑤ 주기 사이 세션 재확인→남은 주기 중단·로그인 필요
#     ⑥ 죽은 config 키(rg_max_periods)를 다시 읽지 않음
from __future__ import annotations

import contextlib
import importlib.util
import inspect
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
    """tools/wing_browser_fetcher.py 독립 로드(HOME은 tmp로 격리 — import 시 로그파일 생성)."""
    _ensure_playwright_stub()
    home = tmp_path_factory.mktemp("home")
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        spec = importlib.util.spec_from_file_location(
            "_tool_wing_rg_gap", TOOLS / "wing_browser_fetcher.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home


_WS = "WAREHOUSING_SHIPPING"
_TR = "CATEGORY_TR"


def _periods(*ends: str) -> list[dict]:
    """열거 결과(최신 우선 정렬된 상태로 넘어온다)."""
    return [{"group_key": f"A01564720-{e}", "period_end": e} for e in ends]


def _gaps(*entries) -> dict:
    return {
        "covered_fee_types": ["delivery", "warehousing"],
        "unmapped_report_types": [],
        "gaps": [{"recognition_date_from": e[0], "recognition_date_to": e[0],
                  "missing_report_types": list(e[1])} for e in entries],
    }


# ── ① 결손만, 최신 우선 ───────────────────────────────────────────────
def test_selects_only_gap_periods(wing):
    periods = _periods("2026-07-19", "2026-07-12", "2026-07-05")
    gaps = _gaps(("2026-07-12", [_WS]))
    got = wing._rg_select_targets(periods, gaps, [_WS], 3)
    assert got == [{"group_key": "A01564720-2026-07-12", "period_end": "2026-07-12",
                    "report_types": [_WS]}]


def test_keeps_newest_first_order(wing):
    periods = _periods("2026-07-19", "2026-07-12", "2026-07-05")
    gaps = _gaps(("2026-07-05", [_WS]), ("2026-07-19", [_WS]))
    assert [t["period_end"] for t in wing._rg_select_targets(periods, gaps, [_WS], 3)] \
        == ["2026-07-19", "2026-07-05"]


def test_only_missing_report_types_are_requested(wing):
    """이미 받은 리포트는 다시 받지 않는다 — 주기당 최대 300초 폴링을 헛쓰지 않게."""
    periods = _periods("2026-07-12")
    gaps = _gaps(("2026-07-12", [_TR]))
    got = wing._rg_select_targets(periods, gaps, [_WS, _TR], 3)
    assert got[0]["report_types"] == [_TR]


def test_unknown_report_type_in_gap_is_filtered(wing):
    """내 설정에 없는 리포트를 결손이라 해도 받지 않는다(설정이 권위)."""
    periods = _periods("2026-07-12")
    gaps = _gaps(("2026-07-12", ["STORAGE_FEE"]))
    assert wing._rg_select_targets(periods, gaps, [_WS], 3) == []


def test_gap_period_not_enumerated_is_skipped(wing):
    """group_key가 없으면 요청 자체가 불가 — 조용히 건너뛴다(옛 주기는 rg_status_days를 넓혀야 열거)."""
    periods = _periods("2026-07-19")
    gaps = _gaps(("2026-05-03", [_WS]))
    assert wing._rg_select_targets(periods, gaps, [_WS], 3) == []


# ── ② 회차 상한 ───────────────────────────────────────────────────────
def test_cap_limits_targets_per_run(wing):
    periods = _periods("2026-07-19", "2026-07-12", "2026-07-05", "2026-06-28")
    gaps = _gaps(*[(p["period_end"], [_WS]) for p in periods])
    got = wing._rg_select_targets(periods, gaps, [_WS], 2)
    assert [t["period_end"] for t in got] == ["2026-07-19", "2026-07-12"]  # 나머지는 다음 회차


def test_default_cap_is_three(wing):
    assert wing._RG_DEFAULT_MAX_TARGETS == 3
    assert wing._cfg_int({}, "rg_max_targets", wing._RG_DEFAULT_MAX_TARGETS) == 3
    assert wing._cfg_int({"rg_max_targets": 5}, "rg_max_targets", 3) == 5
    assert wing._cfg_int({"rg_max_targets": "junk"}, "rg_max_targets", 3) == 3


# ── ③ 폴백: 판정 불가여도 수집은 멈추지 않는다 ─────────────────────────
def test_falls_back_to_latest_when_query_failed(wing):
    periods = _periods("2026-07-19", "2026-07-12")
    got = wing._rg_select_targets(periods, None, [_WS, _TR], 3)
    assert got == [{"group_key": "A01564720-2026-07-19", "period_end": "2026-07-19",
                    "report_types": [_WS, _TR]}]


def test_falls_back_when_nothing_is_judgeable(wing):
    """요청 리포트가 전부 미매핑(covered 없음) → 판정 불가 → 기존 동작."""
    resp = {"covered_fee_types": [], "unmapped_report_types": ["PRODUCT_SIZE_COMPARISON"], "gaps": []}
    got = wing._rg_select_targets(_periods("2026-07-19"), resp, ["PRODUCT_SIZE_COMPARISON"], 3)
    assert [t["period_end"] for t in got] == ["2026-07-19"]


def test_fallback_with_no_periods_is_empty(wing):
    assert wing._rg_select_targets([], None, [_WS], 3) == []


# ── ④ 결손 0건 = 다운로드 0건(정상 완주) ───────────────────────────────
def test_no_gaps_means_no_downloads(wing):
    periods = _periods("2026-07-19", "2026-07-12")
    assert wing._rg_select_targets(periods, _gaps(), [_WS], 3) == []


# ── 결손 조회 HTTP: 실패는 전부 None(폴백 신호) ────────────────────────
class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _cfg() -> dict:
    return {"account_key": "COUPANG_WING1", "prod_base_url": "https://p.example",
            "ingest_token": "tok"}


def test_gap_query_sends_account_days_and_report_types(wing, monkeypatch):
    seen: dict = {}

    def _get(url, params=None, headers=None, timeout=None):
        seen.update({"url": url, "params": params, "headers": headers})
        return _Resp(payload={"gaps": [], "covered_fee_types": ["delivery"]})

    monkeypatch.setattr(wing.requests, "get", _get)
    out = wing._prod_rg_layer2_gaps(_cfg(), [_WS], 35)
    assert out == {"gaps": [], "covered_fee_types": ["delivery"]}
    assert seen["url"].endswith(wing.RG_LAYER2_GAPS_PATH)
    assert seen["params"] == {"account_key": "COUPANG_WING1", "days": 35, "report_types": [_WS]}
    assert seen["headers"]["X-Ingest-Token"] == "tok"


@pytest.mark.parametrize("outcome", ["network", "http500", "notjson", "notdict"])
def test_gap_query_failures_return_none(wing, monkeypatch, outcome):
    def _get(*_a, **_k):
        if outcome == "network":
            raise wing.requests.RequestException("boom")
        if outcome == "http500":
            return _Resp(status=500, text="err")
        if outcome == "notjson":
            return _Resp(payload=None)
        return _Resp(payload=["nope"])

    monkeypatch.setattr(wing.requests, "get", _get)
    assert wing._prod_rg_layer2_gaps(_cfg(), [_WS], 35) is None


def test_gap_query_needs_push_config(wing):
    assert wing._prod_rg_layer2_gaps({}, [_WS], 35) is None


# ── ⑤ _do_rg_run 배선: 세션 재확인·상한·폴백 ───────────────────────────
class _FakePage:
    def goto(self, *_a, **_k):
        return None

    def wait_for_timeout(self, *_a, **_k):
        return None


def _raw(*ends: str) -> dict:
    return {"settlementStatusReports": [
        {"settlementGroupKey": f"A01564720-{e}", "settlementPeriodStartDate": e,
         "settlementPeriodEndDate": e, "settlementStatusReportDetail": {}} for e in ends]}


@pytest.fixture
def rg_run(wing, monkeypatch, tmp_path):
    """_do_rg_run을 브라우저 없이 돌린다 — 다운로드 호출 기록을 돌려준다."""
    calls: list[tuple] = []
    state = {"session_ok": [], "gaps": None, "raw": _raw("2026-07-19")}

    @contextlib.contextmanager
    def _fake_chrome(_p, _cfg, _state, owner=None, **_k):
        yield _FakePage(), object(), (lambda: None)

    @contextlib.contextmanager
    def _fake_playwright():
        yield object()

    def _session_ok(_page):
        seq = state["session_ok"]
        return seq.pop(0) if seq else True

    monkeypatch.setattr(wing, "sync_playwright", _fake_playwright)
    monkeypatch.setattr(wing, "_chrome", _fake_chrome)
    monkeypatch.setattr(wing, "_cdp_mode", lambda _c: False)
    monkeypatch.setattr(wing, "_rg_session_ok", _session_ok)
    monkeypatch.setattr(wing, "_rg_fetch_status_raw", lambda _p, _c: state["raw"])
    monkeypatch.setattr(wing, "_rg_push_status", lambda _c, _r: 0)
    monkeypatch.setattr(wing, "_prod_rg_layer2_gaps", lambda _c, _rt, _d: state["gaps"])
    monkeypatch.setattr(wing, "_rg_push_xlsx", lambda _c, _u, _rt, _gk: 0)

    def _download(_page, group_key, report_type, _req, _timeout):
        calls.append((group_key, report_type))
        return {"url": "https://s3/x.xlsx", "request_time": "1"}

    monkeypatch.setattr(wing, "_rg_download_one", _download)

    cfg = _cfg() | {"vendor_id": "A01564720", "rg_report_types": [_WS],
                    "state_file": str(tmp_path / "state.json")}
    return types.SimpleNamespace(wing=wing, cfg=cfg, calls=calls, state=state)


def test_run_downloads_only_gaps(rg_run):
    rg_run.state["raw"] = _raw("2026-07-19", "2026-07-12")
    rg_run.state["gaps"] = _gaps(("2026-07-12", [_WS]))
    rc = rg_run.wing._do_rg_run(rg_run.cfg, rg_run.cfg["state_file"])
    assert rc == 0
    assert rg_run.calls == [("A01564720-2026-07-12", _WS)]


def test_run_with_no_gaps_downloads_nothing_and_completes(rg_run):
    """받을 게 없으면 조용히 완주한다 — 실패로 보고하면 버튼 요청이 헛되이 재시도된다."""
    rg_run.state["gaps"] = _gaps()
    assert rg_run.wing._do_rg_run(rg_run.cfg, rg_run.cfg["state_file"]) == 0
    assert rg_run.calls == []


def test_run_falls_back_to_latest_when_gap_query_fails(rg_run):
    rg_run.state["raw"] = _raw("2026-07-19", "2026-07-12")
    rg_run.state["gaps"] = None
    assert rg_run.wing._do_rg_run(rg_run.cfg, rg_run.cfg["state_file"]) == 0
    assert rg_run.calls == [("A01564720-2026-07-19", _WS)]


def test_run_respects_max_targets(rg_run):
    ends = ["2026-07-19", "2026-07-12", "2026-07-05", "2026-06-28"]
    rg_run.state["raw"] = _raw(*ends)
    rg_run.state["gaps"] = _gaps(*[(e, [_WS]) for e in ends])
    rg_run.cfg["rg_max_targets"] = 2
    assert rg_run.wing._do_rg_run(rg_run.cfg, rg_run.cfg["state_file"]) == 0
    assert [gk for gk, _ in rg_run.calls] == ["A01564720-2026-07-19", "A01564720-2026-07-12"]


def test_run_stops_remaining_periods_on_session_loss(rg_run):
    """루프 도중 세션이 죽으면 남은 주기는 헛돈다 — 즉시 중단하고 로그인 필요로 보고."""
    ends = ["2026-07-19", "2026-07-12", "2026-07-05"]
    rg_run.state["raw"] = _raw(*ends)
    rg_run.state["gaps"] = _gaps(*[(e, [_WS]) for e in ends])
    # 진입 확인 True → 2번째 주기 직전 False.
    rg_run.state["session_ok"] = [True, False]
    rc = rg_run.wing._do_rg_run(rg_run.cfg, rg_run.cfg["state_file"])
    assert rc == rg_run.wing.RC_LOGIN_REQUIRED
    assert [gk for gk, _ in rg_run.calls] == ["A01564720-2026-07-19"]  # 1번째까지는 유지


def test_run_ignores_dead_rg_max_periods(rg_run):
    """config에 남은 구 상한은 무시된다 — 그 상한이 영구 공백의 원인이었다."""
    ends = ["2026-07-19", "2026-07-12"]
    rg_run.state["raw"] = _raw(*ends)
    rg_run.state["gaps"] = _gaps(*[(e, [_WS]) for e in ends])
    rg_run.cfg["rg_max_periods"] = 1
    assert rg_run.wing._do_rg_run(rg_run.cfg, rg_run.cfg["state_file"]) == 0
    assert len(rg_run.calls) == 2


# ── ⑥ 죽은 키를 코드가 다시 읽지 않는지(소스 레벨 가드) ─────────────────
def test_source_no_longer_reads_dead_config_keys(wing):
    src = inspect.getsource(wing)
    assert 'cfg.get("rg_max_periods"' not in src
    assert 'cfg.get("rg_days"' not in src


def test_gap_query_uses_same_window_as_layer1(wing):
    """결손 판정 창과 층1 열거 창이 어긋나면 '결손인데 열거엔 없는 주기'가 생긴다."""
    src = inspect.getsource(wing._do_rg_run)
    assert "_prod_rg_layer2_gaps(cfg, report_types, _rg_status_days(cfg))" in src
    assert "_rg_status_days(cfg)" in inspect.getsource(wing._rg_fetch_status_raw)
