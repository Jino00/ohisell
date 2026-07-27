# test_wing_vs_window.py — wing 판매분석(vendor-summary) 요청 창의 자가치유 가드.
#   ★존재 이유(2026-07-27): 수집이 순수 버튼-only라 요청 창이 7일이면 버튼 간격이 7일을 넘는 순간
#   그 사이 날짜가 영구 누락된다(실측 구멍 06-20~07-09·07-17~07-19). 창을 45일 롤링으로 넓히고
#   라이브 실증된 폭(7일) 단위로 청크 분할해 요청한다. 여기서 고정하는 것:
#     ① 롤링 창 폭 기본 45일 ② 청크 최대 7일 ③ 창 연속·무겹침·어제 종료 ④ config 우선
#     ⑤ 과거창 best-effort(한 청크 실패가 전체를 죽이지 않음) ⑥ 병합 시 이중계상 없음.
import importlib.util
import inspect
import json
import os
import sys
import types
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
KST = ZoneInfo("Asia/Seoul")


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
            "_tool_wing_vs_window", TOOLS / "wing_browser_fetcher.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home


def _yesterday() -> date:
    return datetime.now(KST).date() - timedelta(days=1)


# ── ① 기본값: 45일 롤링 창을 7일 청크로 ────────────────────────────────
def test_default_window_is_45_days_in_7_day_chunks(wing):
    wins = wing._vs_windows({})
    assert wing._VS_DEFAULT_DAYS == 45
    assert wing._VS_DEFAULT_CHUNK_DAYS == 7
    assert len(wins) == 7                      # 45일 = 7일×6 + 3일
    assert wins[0][1] == _yesterday()          # 최신 청크가 맨 앞이고 어제로 끝난다
    assert wins[-1][0] == _yesterday() - timedelta(days=44)
    for start, end in wins:
        assert (end - start).days + 1 <= 7     # 어떤 청크도 검증된 폭을 넘지 않는다
        assert start <= end


def test_windows_are_contiguous_and_non_overlapping(wing):
    """구멍(누락일)도, 겹침(중복 요청)도 없어야 한다 — 창 전체가 정확히 1회씩 덮인다."""
    wins = wing._vs_windows({})
    covered: list[date] = []
    for start, end in wins:
        d = start
        while d <= end:
            covered.append(d)
            d += timedelta(days=1)
    assert len(covered) == len(set(covered)) == 45
    assert min(covered) == _yesterday() - timedelta(days=44)
    assert max(covered) == _yesterday()


# ── ② config 우선(기존 계약) ──────────────────────────────────────────
def test_config_vs_days_overrides_default(wing):
    """config에 vs_days가 있으면 그 값이 이긴다 — 7이면 청크 1개(=기존 동작)."""
    wins = wing._vs_windows({"vs_days": 7})
    assert len(wins) == 1
    assert wins[0] == (_yesterday() - timedelta(days=6), _yesterday())


def test_config_chunk_days_can_only_shrink(wing):
    """vs_chunk_days는 줄이는 방향만 — 실증된 7일 폭을 config 한 줄로 넘기면 최신 청크부터 죽는다."""
    assert [(e - s).days + 1 for s, e in wing._vs_windows({"vs_days": 10, "vs_chunk_days": 3})] \
        == [3, 3, 3, 1]
    wide = wing._vs_windows({"vs_days": 30, "vs_chunk_days": 14})
    assert max((e - s).days + 1 for s, e in wide) == 7      # 14를 요구해도 7로 클램프


def test_zero_or_garbage_falls_back_to_defaults(wing):
    """0·None·오타 문자열은 무한루프/빈 창/예외를 만든다 — 기본값으로 떨어뜨린다."""
    assert len(wing._vs_windows({"vs_days": 0, "vs_chunk_days": 0})) == 7
    assert len(wing._vs_windows({"vs_days": None})) == 7
    assert len(wing._vs_windows({"vs_days": "garbage", "vs_chunk_days": "nope"})) == 7
    assert len(wing._vs_windows({"vs_days": -5})) == 7


# ── ③ payload 구성 ────────────────────────────────────────────────────
def test_payload_defaults_to_most_recent_chunk(wing):
    """window 인자 없는 기존 호출부(로그인 감지 프로브)는 최신 청크 1개만 요청한다."""
    p = wing._vs_payload({})
    assert p["endDate"] == _yesterday().isoformat()
    assert p["startDate"] == (_yesterday() - timedelta(days=6)).isoformat()
    assert p["registrationTypes"] == ["NORMAL", "RFM"]
    assert p["searchIds"] == []


def test_payload_uses_given_window(wing):
    p = wing._vs_payload({}, (date(2026, 6, 20), date(2026, 6, 26)))
    assert p["startDate"] == "2026-06-20"
    assert p["endDate"] == "2026-06-26"


def test_payload_never_includes_today(wing):
    """오늘은 sync 시차로 부정확 → 어떤 창에도 들어가면 안 된다(D-3)."""
    today = datetime.now(KST).date().isoformat()
    for start, end in wing._vs_windows({}):
        assert end.isoformat() < today


# ── ④ 과거창 fetch: best-effort ───────────────────────────────────────
class _FakePage:
    """page.evaluate만 흉내 — 창별 응답을 주입한다."""

    def __init__(self, responses):
        self.responses = responses      # list[dict|Exception]
        self.calls: list[dict] = []

    def wait_for_timeout(self, _ms):
        return None

    def evaluate(self, _js, args):
        payload = args[0]
        self.calls.append(payload)
        r = self.responses[len(self.calls) - 1]
        if isinstance(r, Exception):
            raise r
        return r


def _ok(day: str, gmv: int = 1000) -> dict:
    return {"status": 200, "body": json.dumps({
        "saleSummaryByDate": [
            {"date": day, "registrationType": "NORMAL", "gmv": gmv, "unitsSold": 1},
        ],
        "summaryMetrics": {"totalGmv": gmv},
    })}


def test_older_windows_fetches_all_remaining_chunks(wing):
    cfg = {"vs_days": 21}                     # 7일×3 = 청크 3개, 과거창 2개
    page = _FakePage([_ok("2026-07-10"), _ok("2026-07-03")])
    bodies = wing._fetch_older_windows(page, cfg)
    assert len(page.calls) == 2               # 최신 청크는 이미 받았으므로 제외
    assert len(bodies) == 2
    wins = wing._vs_windows(cfg)
    assert [c["startDate"] for c in page.calls] == [wins[1][0].isoformat(), wins[2][0].isoformat()]


def test_older_windows_uses_caller_supplied_windows(wing):
    """호출자가 넘긴 창을 그대로 쓴다 — 실행 중 KST 자정을 넘겨도 기준일이 흔들리지 않게(재계산 금지)."""
    fixed = [(date(2026, 6, 1), date(2026, 6, 7)), (date(2026, 5, 25), date(2026, 5, 31))]
    page = _FakePage([_ok("2026-06-01"), _ok("2026-05-25")])
    wing._fetch_older_windows(page, {"vs_days": 45}, fixed)
    assert [(c["startDate"], c["endDate"]) for c in page.calls] == [
        ("2026-06-01", "2026-06-07"), ("2026-05-25", "2026-05-31")]


def test_older_window_failure_does_not_kill_the_rest(wing):
    """한 청크가 터져도 나머지는 계속 — 그 날짜는 다음 버튼 클릭에서 다시 시도된다."""
    cfg = {"vs_days": 21}
    page = _FakePage([RuntimeError("boom"), _ok("2026-07-03")])
    bodies = wing._fetch_older_windows(page, cfg)
    assert len(page.calls) == 2 and len(bodies) == 1


def test_older_windows_stop_on_auth_expiry(wing):
    """세션이 죽으면 남은 창을 더 눌러봐야 소용없다 — 즉시 중단."""
    cfg = {"vs_days": 45}
    page = _FakePage([{"status": 302, "body": ""}] + [_ok("2026-07-03")] * 6)
    bodies = wing._fetch_older_windows(page, cfg)
    assert bodies == [] and len(page.calls) == 1


# ── ⑤ 병합: 이중계상 없음 ─────────────────────────────────────────────
def test_merge_summary_unions_dates_and_recomputes_totals(wing):
    base = wing._summarize(_ok("2026-07-20", 1000)["body"])
    older = wing._summarize(_ok("2026-07-10", 500)["body"])
    merged = wing._merge_summary(base, older)
    assert sorted(merged["dates"]) == ["2026-07-10", "2026-07-20"]
    assert merged["gmv_3p"] == 1500
    assert merged["total_gmv"] == 1500        # summaryMetrics(청크별)를 그대로 쓰면 1000이 됨


def test_merge_same_date_replaces_not_adds(wing):
    """창은 겹치지 않지만, 겹쳐도 합산이 아니라 대체 — 이중계상 방지."""
    base = wing._summarize(_ok("2026-07-20", 1000)["body"])
    dup = wing._summarize(_ok("2026-07-20", 1000)["body"])
    merged = wing._merge_summary(base, dup)
    assert merged["gmv_3p"] == 1000


# ── ⑥ 호출부 배선(브라우저를 띄울 수 없으므로 소스 레벨 가드) ─────────────
def test_do_run_computes_windows_once_and_passes_them(wing):
    """실행 중 자정을 넘겨도 흔들리지 않게 — 창은 _do_run에서 1회 계산해 넘긴다."""
    src = inspect.getsource(wing._do_run)
    assert src.count("_vs_windows(cfg)") == 1
    assert "window=windows[0]" in src
    assert "_fetch_older_windows(page, cfg, windows[1:])" in src


def test_login_also_fetches_older_windows_with_fixed_window(wing):
    """로그인 회차도 자가치유를 타되(프로브 7일만 push 금지), 프로브·과거창이 같은 기준일을 써야 한다.

    로그인 대기는 최대 600초 — 그 사이 자정을 넘으면 프로브와 과거창이 하루 어긋나 중복·누락이 난다.
    """
    src = inspect.getsource(wing.cmd_login)
    assert src.count("_vs_windows(cfg)") == 1
    assert "window=windows[0]" in src
    assert "_fetch_older_windows(page, cfg, windows[1:])" in src
    # 재로그인(_do_run) 경로도 같은 고정 창을 쓴다.
    assert "window=windows[0]" in inspect.getsource(wing._do_run)
    # 프로브 자체가 넘겨받은 창을 쓰는지(_vs_payload(cfg) 재계산 금지).
    assert "_vs_payload(cfg, window)" in inspect.getsource(wing._login_wait_loop)


def test_observed_span_reports_what_we_got(wing):
    """로그는 '요청한 창'이 아니라 '받은 날짜'를 찍는다(원칙 22)."""
    summ = wing._merge_summary(
        wing._summarize(_ok("2026-07-20")["body"]),
        wing._summarize(_ok("2026-07-10")["body"]),
    )
    span = wing._observed_span(summ, {})
    assert span["startDate"] == "2026-07-10" and span["endDate"] == "2026-07-20"
    empty = wing._observed_span({"dates": {}}, {})
    assert empty["endDate"] == _yesterday().isoformat()   # 빈 결과는 요청 창으로 폴백
