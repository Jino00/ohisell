# test_wing_vi_detail.py — 페처 «옵션축»(vi-detail-search) 수집·파싱·push 가드 (D-CPP-36).
#
# ## 존재 이유 (적대 리뷰 2026-08-10)
#
# 백엔드 판정·배선층은 촘촘한데 **페처 옵션축 경로는 테스트 0건**이었고, 리뷰의 변이 주입에서
# 9건이 그대로 생존했다. 그중 둘은 조용한 사고다:
#   · `gmv ← totalUnitsSold` 오배선 — **돈 축이 통째로 틀린다**(GMV 자리에 수량이 들어간다)
#   · push URL을 요약축 엔드포인트로 — 요약축 GMV를 옵션값으로 덮을 수 있다
# 나머지도 조용하다: 오늘 포함(닫힌 과거일 위반) · `totalPages` 무시(2페이지+ 누락) ·
# 세션 만료 조기중단 제거. 전부 «에러 없이 값만 틀리는» 계열이다.
#
# 이 파일이 지키는 것은 브라우저 동작이 아니라 **경계의 계약**이다: 무엇을 요청하고, 응답의
# 어느 필드가 어디로 가고, 어디로 보내는가.
import importlib.util
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
            "_tool_wing_vi_detail", TOOLS / "wing_browser_fetcher.py")
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


# 2026-08-10 라이브 정찰 응답의 실제 모양(축약).
def _live_page(vendor_items, total_pages=1, page_number=0):
    import json
    return json.dumps({
        "vendorItems": vendor_items,
        "soldVICount": sum(1 for v in vendor_items
                           if (v.get("businessInsightsMetricsResponse") or {}).get("totalUnitsSold")),
        "paginationDetails": {
            "pageSize": 50, "pageNumber": page_number,
            "totalResults": 146, "totalPages": total_pages,
        },
    })


def _item(vid, gmv, units, rt="NORMAL", orders=None, name="오하이 풀커버 강화유리", pid=6089846168):
    return {
        "vendorItemDetails": {
            "vendorId": "A01029796", "vendorItemId": vid, "itemName": name,
            "productName": name, "productId": pid, "registrationType": rt,
            "externalSkuIds": [], "displayCategoryCode": 62618,
        },
        "businessInsightsMetricsResponse": {
            "totalGmv": float(gmv), "totalUnitsSold": float(units),
            "totalOrders": float(orders if orders is not None else units),
            "totalPageViews": 13.0, "totalUniqueVisitor": 9.0, "searchVolume": 0.0,
        },
    }


# ═══ 요청 창 — 닫힌 과거일만 ═══


def test_vi_window_is_yesterday_backwards_and_never_includes_today(wing):
    """★★오늘을 포함하면 안 된다(D-3 닫힌 과거일).

    오늘은 아직 확정이 아니라 값이 흔들린다. 게다가 요약축은 오늘을 안 받으므로 옵션축만
    오늘을 받으면 보존식에서 `option_only`가 되고 — 그건 healthy를 깨지 않아 **조용하다**.
    즉 이 규율이 깨지면 아무 경보도 없이 두 축이 다른 기간을 세게 된다.
    """
    days = wing._vi_days({})
    today = datetime.now(KST).date()
    assert today not in days, "오늘을 받고 있다 — 닫힌 과거일 규율 위반"
    assert days[0] == _yesterday(), "최신이 어제여야 한다"
    assert len(days) == wing._VI_DEFAULT_DAYS == 7
    # 연속·내림차순·중복 없음
    assert days == sorted(days, reverse=True)
    assert len(set(days)) == len(days)
    assert days[-1] == _yesterday() - timedelta(days=6)


def test_vi_days_respects_config_override(wing):
    """백필용으로 `vi_days`를 늘릴 수 있다(오타·0은 기본값으로 강등)."""
    assert len(wing._vi_days({"vi_days": 14})) == 14
    assert len(wing._vi_days({"vi_days": 0})) == 7
    assert len(wing._vi_days({"vi_days": "이상한값"})) == 7


def test_vi_collection_window_matches_server_conservation_window(wing):
    """★★수집창과 서버 검사창이 같아야 한다(적대 리뷰 P1-3).

    검사창이 더 넓으면 «아무도 다시 받지 않는 구간»에 보존식을 단언하게 되고, 그 불일치는
    어떤 회차도 고칠 수 없다 → 수리 경로 없는 영구 빨강. 두 상수는 한 계약의 양쪽이므로
    한쪽만 바꾸면 여기서 죽는다.
    """
    from app.services.scheduler_health import CONSERVATION_WINDOW_DAYS

    assert CONSERVATION_WINDOW_DAYS == wing._VI_DEFAULT_DAYS


def test_vi_payload_is_a_single_day_window(wing):
    """★하루 단위 창 — 응답이 창 집계라 일별 값을 얻는 유일한 방법이다.

    단일 일자 창이 요약축과 정확히 일치함을 라이브로 확인했다(08-05 188,800 · 08-07 86,500).
    """
    p = wing._vi_payload(date(2026, 8, 5), 0)
    assert p["startDate"] == p["endDate"] == "2026-08-05"
    assert p["registrationTypes"] == ["NORMAL", "RFM"]
    assert p["pageNumber"] == 0
    assert p["pageSize"] == wing._VI_PAGE_SIZE


def test_vi_endpoint_path_is_the_reconnoitred_one(wing):
    """★경로·헬퍼를 바꾸면 Akamai가 막는다 — 실측된 조합을 고정한다."""
    assert wing.VI_DETAIL_PATH == "/tenants/rfm-ss/api/business-insight/vi-detail-search"


# ═══ 파싱 — 어느 필드가 어디로 가는가 ═══


def test_parse_maps_gmv_from_totalGmv_not_from_units(wing):
    """★★돈 축 오배선 방어: gmv는 `totalGmv`에서 온다.

    `gmv ← totalUnitsSold` 변이가 리뷰에서 생존했다. 그러면 GMV 자리에 수량(7)이 들어가
    3P 매출이 통째로 무너지는데 **에러는 안 난다**. 값을 서로 다르게 둬서 뒤바뀜을 잡는다.
    """
    rows, _ = wing._parse_vi_page(_live_page([_item(87286712272, 132300, 7, orders=5)]),
                                  date(2026, 8, 5))
    assert len(rows) == 1
    r = rows[0]
    assert r["gmv"] == 132300, "gmv가 totalGmv가 아니다 — 돈 축이 틀렸다"
    assert r["units_sold"] == 7
    assert r["total_orders"] == 5
    assert r["vendor_item_id"] == "87286712272"   # 문자열 조인축
    assert r["registration_type"] == "NORMAL"
    assert r["date"] == "2026-08-05"
    assert r["product_id"] == "6089846168"
    assert r["raw_metrics"]["totalPageViews"] == 13.0  # 원본 보존


def test_parse_returns_total_pages_so_later_pages_are_not_dropped(wing):
    """★`totalPages`를 그대로 돌려준다 — 1로 뭉개면 2페이지 이후가 통째로 누락된다."""
    rows, pages = wing._parse_vi_page(_live_page([_item(1, 100, 1)], total_pages=3),
                                      date(2026, 8, 5))
    assert pages == 3
    assert len(rows) == 1


def test_parse_caps_absurd_total_pages(wing):
    """★상한 — 거대한 totalPages를 그대로 순회하면 봇 감지 하에서 요청이 폭주한다."""
    _, pages = wing._parse_vi_page(_live_page([], total_pages=5000), date(2026, 8, 5))
    assert pages == wing._VI_MAX_PAGES


def test_parse_keeps_unsold_options(wing):
    """★판매 0인 옵션도 담는다 — 146개 중 11개만 팔린다.

    «안 팔린 옵션»이 데이터에 없으면 「안 팔린 날의 광고비」 같은 후속 질문을 못 한다.
    """
    rows, _ = wing._parse_vi_page(
        _live_page([_item(1, 0, 0), _item(2, 18900, 1)]), date(2026, 8, 5))
    assert len(rows) == 2


def test_parse_skips_rows_without_join_key_or_known_type(wing):
    """옵션ID 없음·모르는 등록유형은 버린다(스키마 방어)."""
    bad_no_vid = _item(None, 100, 1)
    bad_rt = _item(3, 100, 1, rt="WHOLESALE")
    rows, _ = wing._parse_vi_page(
        _live_page([bad_no_vid, bad_rt, _item(4, 100, 1)]), date(2026, 8, 5))
    assert [r["vendor_item_id"] for r in rows] == ["4"]


def test_parse_survives_a_type_change_in_one_row(wing):
    """★한 행의 형태 변화가 그 날짜 전체를 죽이지 않는다(적대 리뷰 P2-4).

    종전엔 `float("34,300")`의 ValueError가 밖으로 나가 **이미 모은 날짜까지 전부** 폐기됐다.
    """
    weird = _item(9, 0, 0)
    weird["businessInsightsMetricsResponse"]["totalGmv"] = "34,300"
    rows, _ = wing._parse_vi_page(_live_page([weird, _item(10, 18900, 1)]), date(2026, 8, 5))
    assert [r["vendor_item_id"] for r in rows] == ["10"], "나쁜 행만 건너뛰어야 한다"


def test_parse_rejects_a_non_vi_response(wing):
    """응답 형태가 바뀌면 None — 0건으로 오인해 «판매 없음»으로 적재하면 안 된다."""
    assert wing._parse_vi_page('{"saleSummaryByDate": []}', date(2026, 8, 5)) is None
    assert wing._parse_vi_page("<html>login</html>", date(2026, 8, 5)) is None
    assert wing._parse_vi_page("", date(2026, 8, 5)) is None


def test_parse_accepts_negative_gmv(wing):
    """★환불이 판매를 넘는 날은 정당하게 음수다 — 클램프하면 사실이 사라진다."""
    rows, _ = wing._parse_vi_page(_live_page([_item(1, -12900, -1)]), date(2026, 8, 5))
    assert rows[0]["gmv"] == -12900


# ═══ 순회 — 페이지·세션만료·부분성공 ═══


class _FakePage:
    """`page.evaluate(js, [payload, path])`만 흉내내는 최소 스텁. 대기는 no-op."""

    def __init__(self, responder):
        self.responder = responder
        self.calls: list[tuple[dict, str]] = []

    def wait_for_timeout(self, _ms):
        pass

    def evaluate(self, _js, args):
        payload, path = args
        self.calls.append((payload, path))
        return self.responder(payload, path, len(self.calls))


def test_fetch_walks_every_page(wing):
    """★모든 페이지를 돈다 — totalPages=3이면 pageNumber 0·1·2를 요청한다."""
    def responder(payload, _path, _n):
        pn = payload["pageNumber"]
        return {"status": 200, "body": _live_page(
            [_item(100 + pn, 1000 * (pn + 1), 1)], total_pages=3, page_number=pn)}

    page = _FakePage(responder)
    rows = wing._fetch_vi_detail(page, {"vi_days": 1})
    assert [c[0]["pageNumber"] for c in page.calls] == [0, 1, 2]
    assert len(rows) == 3
    assert all(c[1] == wing.VI_DETAIL_PATH for c in page.calls)


def test_fetch_stops_immediately_on_session_expiry(wing):
    """★★세션이 만료되면 남은 날짜를 **중단**한다.

    계속 두드리면 봇 감지에 걸리고, 어차피 사람이 로그인해야 한다. 리뷰에서 이 조기중단을
    제거한 변이가 생존했다 — 7일이면 7번을 헛되이 두드린다.
    """
    def responder(_payload, _path, _n):
        return {"status": 302, "body": ""}

    page = _FakePage(responder)
    rows = wing._fetch_vi_detail(page, {"vi_days": 7})
    assert rows == []
    assert len(page.calls) == 1, "만료를 보고도 계속 요청했다"


def test_fetch_keeps_earlier_days_when_a_later_day_fails(wing):
    """★부분 성공을 보존한다 — 한 날짜 실패가 이미 모은 날짜를 폐기하면 안 된다."""
    def responder(payload, _path, _n):
        if payload["startDate"] == _yesterday().isoformat():
            return {"status": 200, "body": _live_page([_item(1, 18900, 1)])}
        return {"status": 500, "body": "boom"}

    rows = wing._fetch_vi_detail(_FakePage(responder), {"vi_days": 3})
    assert len(rows) == 1
    assert rows[0]["date"] == _yesterday().isoformat()


def test_fetch_does_not_raise_when_evaluate_explodes(wing):
    """★브라우저측 예외도 삼킨다(요약축 push를 죽이면 안 된다) — 단 조용하진 않다(WARNING)."""
    def responder(*_a):
        raise RuntimeError("Failed to fetch")

    assert wing._fetch_vi_detail(_FakePage(responder), {"vi_days": 2}) == []


# ═══ push — 어디로 보내는가 ═══


def test_push_goes_to_the_option_axis_endpoint_not_the_summary_one(wing, monkeypatch):
    """★★엔드포인트 오배선 방어.

    리뷰에서 push URL을 요약축(`/vendor-summary/ingest`)으로 바꾼 변이가 생존했다. 그러면
    요약축이 이 행들을 받아 **요약 GMV를 옵션값으로 덮을 수 있다** — 대조 상대가 오염되면
    보존식은 조용히 통과하면서 손익만 틀린다.
    """
    seen = {}

    class _Resp:
        status_code = 200
        text = '{"ingested": 1}'

    def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
        seen["url"] = url
        seen["json"] = json
        seen["headers"] = headers
        return _Resp()

    monkeypatch.setattr(wing.requests, "post", fake_post)
    cfg = {"account_key": "COUPANG_WING2", "prod_base_url": "https://x", "ingest_token": "T"}
    rc = wing._push_vendor_item_sales(cfg, [{
        "date": "2026-08-05", "vendor_item_id": "1", "registration_type": "NORMAL",
        "gmv": 18900, "units_sold": 1, "total_orders": 1,
    }], "2026-08-10T12:20:43Z")

    assert rc == 0
    assert seen["url"].endswith("/api/coupang/ops/wing/vendor-item-sales/ingest"), \
        f"옵션축이 아닌 곳으로 보낸다: {seen['url']}"
    assert seen["json"]["account_key"] == "COUPANG_WING2"
    assert seen["headers"]["X-Ingest-Token"] == "T"
    # last_refresh가 모든 행에 실린다 — 어느 시점 스냅샷인지 없으면 두 축의 동시성을 못 따진다.
    assert seen["json"]["rows"][0]["last_refresh"] == "2026-08-10T12:20:43Z"


def test_push_reports_failure_and_does_not_raise(wing, monkeypatch):
    """★push 실패는 1을 돌려주고 끝 — 요약축 회차를 실패로 오보하면 lease가 창을 반복해 띄운다."""
    class _Resp:
        status_code = 413
        text = "Request Entity Too Large"

    monkeypatch.setattr(wing.requests, "post", lambda *a, **k: _Resp())
    cfg = {"account_key": "COUPANG_WING2", "prod_base_url": "https://x", "ingest_token": "T"}
    assert wing._push_vendor_item_sales(cfg, [{"date": "2026-08-05", "vendor_item_id": "1",
                                               "registration_type": "NORMAL", "gmv": 1,
                                               "units_sold": 1, "total_orders": 1}], None) == 1


def test_push_skips_when_there_is_nothing_to_send(wing, monkeypatch):
    """빈 행은 보내지 않는다(빈 배치는 라우터가 400으로 거부한다 — 헛된 에러 로그 방지)."""
    called = {"n": 0}

    def fake_post(*_a, **_k):
        called["n"] += 1

    monkeypatch.setattr(wing.requests, "post", fake_post)
    assert wing._push_vendor_item_sales({"account_key": "A", "prod_base_url": "https://x",
                                         "ingest_token": "T"}, [], None) == 1
    assert called["n"] == 0
