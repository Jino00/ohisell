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

from app.utils.kst import kst_now

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

    monkeypatch.setattr(wing.time, "sleep", lambda *_a, **_k: None)  # 세션 재확인 지연 제거
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
    # 진입 확인 True → 2번째 주기 직전 False가 **두 번**(재확인까지 실패)이어야 만료로 본다.
    rg_run.state["session_ok"] = [True, False, False]
    rc = rg_run.wing._do_rg_run(rg_run.cfg, rg_run.cfg["state_file"])
    assert rc == rg_run.wing.RC_LOGIN_REQUIRED
    assert [gk for gk, _ in rg_run.calls] == ["A01564720-2026-07-19"]  # 1번째까지는 유지


def test_run_survives_single_session_blip(rg_run):
    """★프로브 blip 1회는 세션 만료가 아니다(리뷰 [P2-2]).

    _rg_session_ok는 로그아웃뿐 아니라 비200·깨진 JSON·evaluate 예외도 전부 False로 접는다.
    300초 폴링 직후의 흔들림 한 번으로 남은 결손 충전을 취소하고 '로그인 필요'를 오보하면,
    다운로드 실패(재시도 가능)보다 blip이 더 치명이 되는 역전이 생긴다.
    """
    ends = ["2026-07-19", "2026-07-12"]
    rg_run.state["raw"] = _raw(*ends)
    rg_run.state["gaps"] = _gaps(*[(e, [_WS]) for e in ends])
    rg_run.state["session_ok"] = [True, False, True]   # 2번째 주기 직전 blip → 재확인 성공
    assert rg_run.wing._do_rg_run(rg_run.cfg, rg_run.cfg["state_file"]) == 0
    assert len(rg_run.calls) == 2   # 중단되지 않고 끝까지 간다


def test_run_falls_back_when_layer1_push_failed(rg_run, monkeypatch):
    """층1 push가 실패한 회차엔 최신 주기가 DB 층1에 없다 → '결손 아님'으로 보여 0건이 된다.

    그 회차는 결손 조회를 건너뛰고 기존 동작(최신 1주기)을 타야 한다(리뷰 [P2-3]).
    """
    rg_run.state["raw"] = _raw("2026-07-19", "2026-07-12")
    rg_run.state["gaps"] = _gaps()          # 조회했다면 '결손 없음'이었을 것
    monkeypatch.setattr(rg_run.wing, "_rg_push_status", lambda _c, _r: 1)
    assert rg_run.wing._do_rg_run(rg_run.cfg, rg_run.cfg["state_file"]) == 0
    assert [gk for gk, _ in rg_run.calls] == ["A01564720-2026-07-19"]


def test_run_stops_on_time_budget(rg_run, monkeypatch):
    """★회차 시간예산(리뷰 R3 [P2-1]): lease TTL 20분을 넘기면 보고가 stale로 폐기되고
    요청이 살아남아 재claim → 창이 다시 뜬다. 예산을 넘기면 남은 대상을 다음 회차로 넘긴다.
    첫 1건은 floor로 무조건 실행되어야 한다(아무것도 못 하면 결손이 영영 안 준다).
    """
    ends = ["2026-07-19", "2026-07-12", "2026-07-05"]
    rg_run.state["raw"] = _raw(*ends)
    rg_run.state["gaps"] = _gaps(*[(e, [_WS]) for e in ends])
    clock = {"t": 0.0}
    monkeypatch.setattr(rg_run.wing.time, "monotonic", lambda: clock["t"])

    def _slow(_page, group_key, report_type, _req, _timeout):
        rg_run.calls.append((group_key, report_type))
        clock["t"] += 10_000     # 1건마다 예산을 훌쩍 넘긴다
        return {"url": "https://s3/x.xlsx", "request_time": "1"}

    monkeypatch.setattr(rg_run.wing, "_rg_download_one", _slow)
    assert rg_run.wing._do_rg_run(rg_run.cfg, rg_run.cfg["state_file"]) == 0   # 완주 처리
    assert [gk for gk, _ in rg_run.calls] == ["A01564720-2026-07-19"]   # floor 1건만


def test_run_ignores_dead_rg_max_periods(rg_run):
    """config에 남은 구 상한은 무시된다 — 그 상한이 영구 공백의 원인이었다."""
    ends = ["2026-07-19", "2026-07-12"]
    rg_run.state["raw"] = _raw(*ends)
    rg_run.state["gaps"] = _gaps(*[(e, [_WS]) for e in ends])
    rg_run.cfg["rg_max_periods"] = 1
    assert rg_run.wing._do_rg_run(rg_run.cfg, rg_run.cfg["state_file"]) == 0
    assert len(rg_run.calls) == 2


# ── ⑦ 실측 포맷 계약: 주기 날짜 규칙이 백엔드와 같아야 한다 ──────────────
# ★이 절이 없어서 [P1-1](상시 100% 결손 미스매칭)이 143개 테스트를 통과했다. 기존 mock은
#   settlementPeriodEndDate에 맨 날짜를 넣었지만 실제 API는 KST 자정=D-1T15:00:00Z를 준다.
_LIVE_RAW = {   # S0 실측 캡처(2026-06-09) 포맷 그대로
    "settlementStatusReports": [{
        "settlementGroupKey": "A01564720-2026-04-06-2026-04-12",
        "settlementPeriodStartDate": "2026-04-05T15:00:00Z",   # → KST 2026-04-06
        "settlementPeriodEndDate": "2026-04-11T15:00:00Z",     # → KST 2026-04-12
        "settlementStatusReportDetail": {},
    }]
}


def test_period_end_is_kst_not_truncated_utc(wing):
    """앞 10자 절단이면 '2026-04-11'(하루 이름) — group_key 꼬리가 KST라는 증거와 어긋난다."""
    got = wing._rg_enumerate_group_keys(_LIVE_RAW, "A01564720")
    assert got == [{"group_key": "A01564720-2026-04-06-2026-04-12",
                    "period_end": "2026-04-12"}]


def test_date_rule_matches_backend_parse_date(wing):
    """페처와 백엔드가 **같은 규칙**이어야 결손 매칭이 성립한다(페처는 backend를 import 못 함)."""
    from app.services.coupang.rg_settlement_sync import _parse_date
    for raw in ("2026-04-11T15:00:00Z", "2026-01-01T00:00:00Z", "2026-12-31T15:00:00Z"):
        assert wing._rg_kst_date_str(raw) == _parse_date(raw).isoformat()


def test_synthesized_group_key_uses_kst(wing):
    """settlementGroupKey 부재 시 합성 키는 쿠팡에 **전송**된다 — UTC면 없는 주기를 요청한다."""
    raw = {"settlementStatusReports": [{
        "settlementPeriodStartDate": "2026-04-05T15:00:00Z",
        "settlementPeriodEndDate": "2026-04-11T15:00:00Z"}]}
    assert wing._rg_enumerate_group_keys(raw, "A01564720")[0]["group_key"] == \
        "A01564720-2026-04-06-2026-04-12"


def test_unparsable_date_is_skipped_not_fatal(wing):
    """날짜 한 건이 깨져도 회차 전체를 죽이면 안 된다(rc=1로 재시도만 소모)."""
    assert wing._rg_kst_date_str("쓰레기") == ""
    raw = {"settlementStatusReports": [
        {"settlementPeriodStartDate": "x", "settlementPeriodEndDate": "y"},
        {"settlementGroupKey": "A01564720-2026-04-06-2026-04-12",
         "settlementPeriodEndDate": "2026-04-11T15:00:00Z"}]}
    assert [p["period_end"] for p in wing._rg_enumerate_group_keys(raw, "A01564720")] \
        == ["2026-04-12"]


# ── ⑧ 미매핑 리포트는 결손 판정 대상이 아니라 '최신 주기 의무 동반' ────────
# ★[P1-2]: 라이브 config가 ["WAREHOUSING_SHIPPING","PRODUCT_SIZE_COMPARISON"]인데 후자는 파서가
#   없어 결손 목록에 절대 안 뜬다 → 결손 주도로만 고르면 영구 미수집이 된다.
_PS = "PRODUCT_SIZE_COMPARISON"


def _gaps_mixed(*entries) -> dict:
    g = _gaps(*entries)
    g["unmapped_report_types"] = [_PS]
    return g


def test_unmapped_report_rides_along_on_newest_period(wing):
    periods = _periods("2026-07-19", "2026-07-12")
    got = wing._rg_select_targets(periods, _gaps_mixed(("2026-07-12", [_WS])), [_WS, _PS], 3)
    assert got[0] == {"group_key": "A01564720-2026-07-19",
                      "period_end": "2026-07-19", "report_types": [_PS]}
    assert got[1]["report_types"] == [_WS]


def test_unmapped_rider_merges_into_newest_gap_target(wing):
    """최신 주기가 결손이기도 하면 target을 둘 만들지 않는다 — 상한 한 칸을 헛되이 쓴다(R3)."""
    periods = _periods("2026-07-19", "2026-07-12")
    got = wing._rg_select_targets(periods, _gaps_mixed(("2026-07-19", [_WS])), [_WS, _PS], 3)
    assert len(got) == 1
    assert got[0]["group_key"] == "A01564720-2026-07-19"
    assert got[0]["report_types"] == [_WS, _PS]


def test_unmapped_rider_survives_when_cap_is_full(wing):
    """상한이 결손으로 꽉 차도 미매핑 리포트는 밀려나지 않는다(밀리면 영구 미수집 재발)."""
    ends = ["2026-07-19", "2026-07-12", "2026-07-05"]
    got = wing._rg_select_targets(_periods(*ends), _gaps_mixed(*[(e, [_WS]) for e in ends[1:]]),
                                  [_WS, _PS], 2)
    assert _PS in got[0]["report_types"]
    assert got[0]["group_key"] == "A01564720-2026-07-19"


def test_unmapped_rider_absent_when_not_configured(wing):
    """cfg에 없는 리포트를 prod가 unmapped로 알려줘도 임의로 받지 않는다."""
    got = wing._rg_select_targets(_periods("2026-07-19"), _gaps_mixed(("2026-07-19", [_WS])),
                                  [_WS], 3)
    assert got[0]["report_types"] == [_WS]


def test_gap_query_days_are_clamped_to_endpoint_limit(wing, monkeypatch):
    """days>400이면 422 → 조용한 폴백. 백필하려다 자가치유가 꺼지는 역설(리뷰 [P2-4])."""
    seen: dict = {}

    def _get(_url, params=None, headers=None, timeout=None):
        seen.update(params)
        return _Resp(200, {"covered_fee_types": ["warehousing"], "gaps": []})

    monkeypatch.setattr(wing.requests, "get", _get)
    wing._prod_rg_layer2_gaps(_cfg(), [_WS], 3650)
    assert seen["days"] == 400


# ── ⑨ 엔드투엔드 계약: 실측 raw → 열거 → 백엔드 결손 응답 → 대상 선택 ────
# ★리뷰 [P2-10]: 기존 테스트는 양쪽 접점을 한 번도 실행하지 않았다(_prod_rg_layer2_gaps를 통째로
#   monkeypatch). 그래서 [P1-1](날짜 규칙)·[P1-2](미매핑 리포트)가 둘 다 통과했다.
#   이 테스트 하나가 두 P1을 동시에 잡는다 — 실측 포맷을 백엔드 실제 응답까지 흘린다.
def test_contract_live_raw_to_backend_gaps_to_targets(wing):
    from decimal import Decimal

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base
    from app.models import CoupangRgSettlementFee
    from app.services.coupang.rg_settlement_sync import _parse_date, layer2_gaps

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        rep = _LIVE_RAW["settlementStatusReports"][0]
        # 백엔드가 실제로 저장하는 방식 그대로 층1을 심는다(_parse_date = UTC→KST).
        pfrom = _parse_date(rep["settlementPeriodStartDate"])
        pto = _parse_date(rep["settlementPeriodEndDate"])
        for ft in ("warehousing", "delivery"):
            db.add(CoupangRgSettlementFee(
                account_key="COUPANG_WING1", recognition_date_from=pfrom,
                recognition_date_to=pto, fee_type=ft, vendor_item_id="",
                raw_type="status/api", amount=Decimal("1000")))
        db.commit()

        # 창은 그 주기를 포함할 만큼 넓게(실측 fixture는 과거 날짜).
        days = (kst_now().date() - pto).days + 5
        gaps = layer2_gaps(db, "COUPANG_WING1", days=days, report_types=[_WS, _PS])
        assert gaps["unmapped_report_types"] == [_PS]
        assert [g["recognition_date_to"] for g in gaps["gaps"]] == [pto.isoformat()]

        periods = wing._rg_enumerate_group_keys(_LIVE_RAW, "A01564720")
        targets = wing._rg_select_targets(periods, gaps, [_WS, _PS], 3)
        # 열거 period_end와 백엔드 recognition_date_to가 실제로 만나야 대상이 생긴다.
        assert targets == [{"group_key": "A01564720-2026-04-06-2026-04-12",
                            "period_end": "2026-04-12",
                            "report_types": [_WS, _PS]}]
    finally:
        db.close()


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
