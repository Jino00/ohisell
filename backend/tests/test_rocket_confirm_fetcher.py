# test_rocket_confirm_fetcher.py — Mac 페처의 **사전 GET 게이트**와 단발 POST.
# 계약 CONTRACT_1p_invoice_confirm_write §2·§3·§6(변이 조건) — Jino 승인 2026-08-28.
#
# ★왜 이 파일이 따로 있나: 게이트가 백엔드와 페처 **양쪽**에 있는데 성질이 다르다.
#   백엔드 게이트는 «우리 원장으로 알 수 있는 것»(RI인가·굳었나·진행 중인가)을 막고,
#   페처 게이트는 «지금 정말 누를 수 있는가»를 supplier 실HTML로 잰다. 후자가 멱등성
#   `[미상]`을 실험 없이 우회하는 유일한 방벽이다(ref 106 §6-1) — 그런데 그건 브라우저를
#   쓰는 코드라 백엔드 테스트가 원리적으로 못 닿는다. 가짜 page로 그 분기만 겨눈다.
#
# ★★이 파일이 죽여야 하는 변이:
#   ① `_confirm_precheck`의 비200/예외를 button_absent로 접기 → 「못 봤다」가 「없다」가 된다
#   ② `cmd_confirm_invoice`의 `if precheck != "button_present"` 제거 → 확인 없이 POST가 나간다
#   ③ POST에 `retries=1` 대신 기본값(2) 쓰기 → 평가 실패 시 **같은 POST가 두 번** 나간다
from __future__ import annotations

import importlib.util
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
def fetcher(tmp_path_factory):
    _ensure_playwright_stub()
    # ★페처는 같은 폴더의 `coupang_auth`를 import한다(설치 시 함께 복사되는 공용 모듈).
    #   이 줄이 없으면 **다른 테스트 파일이 먼저 돌았는지**에 따라 결과가 갈린다 —
    #   `test_rocket_promo_fetcher.py`가 지금 정확히 그 상태다(단독 실행 시 40 errors,
    #   `test_scheduler_watchdog_poll.py`가 먼저 돌면 통과). 이 파일은 자립한다.
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    home = tmp_path_factory.mktemp("home")
    old = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        spec = importlib.util.spec_from_file_location(
            "_tool_rocket_supplier_fetcher_confirm", TOOLS / "rocket_supplier_fetcher.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        if old is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old


# 실측 마크업(ref 106 §3) — `disabled` 속성이 없고 poSeq를 data 속성으로 싣는다.
_BUTTON_HTML = (
    '<div class="btn-group">'
    '<button id="btnConfirmInvoice" class="btn btn-default" data-po-seq="139791428">'
    '거래명세서확인</button></div>'
)
# 대조군(CI·PA·RP) 실HTML에는 이 마크업이 아예 없다 — 서버측 상태 게이트다.
# ★발주번호는 남아 있다(진짜 상세 페이지다) — 「버튼 없음」과 「상세가 아님」을 가르는 표식.
_NO_BUTTON_HTML = (
    '<div class="po-header">발주번호 140163784</div>'
    '<div class="btn-group"><button id="btnPrint">인쇄</button></div>'
)


class FakePage:
    """page.evaluate만 흉내내는 최소 스텁. 호출 순서를 기록한다(POST가 나갔는지가 판정 대상)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, object]] = []

    def evaluate(self, js, arg):
        self.calls.append((js, arg))
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def wait_for_timeout(self, _ms):  # _eval_retry가 재시도 사이에 부른다
        return None


# ──────────────────────────────────────────────
# ① 사전 GET 판정 — 「없다」와 「못 봤다」를 가른다
# ──────────────────────────────────────────────
def test_precheck_button_present(fetcher):
    page = FakePage([{"status": 200, "body": _BUTTON_HTML}])
    assert fetcher._confirm_precheck(page, 139791428) == ("button_present", 200)


def test_precheck_button_absent(fetcher):
    """대조군 실측: CI·PA·RP엔 버튼 마크업이 0건이다(ref 106 §2)."""
    page = FakePage([{"status": 200, "body": _NO_BUTTON_HTML}])
    assert fetcher._confirm_precheck(page, 140163784) == ("button_absent", 200)


@pytest.mark.parametrize(
    "res",
    [
        {"status": 500, "body": "oops"},          # 비200
        {"status": 200, "body": ""},              # 빈 본문
        {"status": None, "body": None},           # 판독 불능
    ],
)
def test_precheck_failure_is_not_absent(fetcher, res):
    """★비정상 응답을 button_absent로 접으면 「이미 처리됨」으로 조용히 종결된다 — 원칙22."""
    page = FakePage([res])
    precheck, _status = fetcher._confirm_precheck(page, 139791428)
    assert precheck == "fetch_failed"


def test_precheck_exception_is_fetch_failed(fetcher):
    page = FakePage([RuntimeError("Akamai stale"), RuntimeError("Akamai stale")])
    assert fetcher._confirm_precheck(page, 139791428) == ("fetch_failed", None)


# ★적대 리뷰 1R P1-1 — 200인데 상세가 아닌 문서.
_LOGIN_HTML = (
    "<!DOCTYPE html><html><head><title>Login</title></head><body>"
    '<form action="/login"><input name="username">'
    '<input type="password" name="password"><button>로그인</button></form></body></html>'
)


def test_precheck_200_login_page_is_fetch_failed_not_absent(fetcher):
    """★세션 소프트 만료 시 supplier는 오리진 URL이 멀쩡한 채 SSR만 200 로그인 HTML을 준다.

    그걸 button_absent로 읽으면 **손도 안 댄 발주가 「이미 처리됨」으로 거짓 종결**되고,
    되돌릴 수 없는 쓰기의 감사 레코드에 거짓이 남는다. 이 파일의 다른 SSR 소비자는 전부
    같은 조건(looksLogin)으로 막고 있었는데 이 경로만 없었다.
    """
    page = FakePage([{"status": 200, "body": _LOGIN_HTML}])
    assert fetcher._confirm_precheck(page, 139791428) == ("fetch_failed", 200)


def test_precheck_200_without_that_po_is_fetch_failed(fetcher):
    """버튼 «부재»를 판정하려면 받은 HTML이 **그 PO의 상세**여야 한다(양성 확인)."""
    page = FakePage([{"status": 200, "body": "<html><body>서비스 점검 중입니다</body></html>"}])
    assert fetcher._confirm_precheck(page, 139791428) == ("fetch_failed", 200)


def test_precheck_absent_requires_the_po_number_present(fetcher):
    """정상 상세(발주번호 있음 · 버튼 없음)는 button_absent가 맞다 — 과잉 차단이 아님을 잰다."""
    body = '<html><body><h1>발주 139791428</h1><button id="btnPrint">인쇄</button></body></html>'
    page = FakePage([{"status": 200, "body": body}])
    assert fetcher._confirm_precheck(page, 139791428) == ("button_absent", 200)


# ──────────────────────────────────────────────
# ② 게이트 — 버튼이 없으면 POST가 «나가지 않는다»
# ──────────────────────────────────────────────
def _run_confirm_with(fetcher, monkeypatch, page, *, claim=True):
    """cmd_confirm_invoice를 가짜 Chrome/prod로 돌린다. 반환 (rc, 보고 body, page)."""
    import contextlib

    reports: list[dict] = []
    monkeypatch.setattr(fetcher, "_push_configured", lambda cfg: True)
    monkeypatch.setattr(
        fetcher, "_prod_confirm_claim",
        lambda cfg: ({"claimed": True, "command_id": 1, "purchase_order_seq": 139791428,
                      "lease": "L1"} if claim else {"claimed": False}),
    )
    monkeypatch.setattr(fetcher, "_prod_confirm_report",
                        lambda cfg, body: (reports.append(body), True)[1])
    monkeypatch.setattr(fetcher, "_goto_origin", lambda p: True)

    class _Owner:
        keep_open = False

    @contextlib.contextmanager
    def _owned(cfg, owner=None):
        yield _Owner()

    @contextlib.contextmanager
    def _sync_pw():
        yield object()

    @contextlib.contextmanager
    def _chrome(p, cfg, owner=None):
        yield page

    monkeypatch.setattr(fetcher, "_owned_chrome", _owned)
    monkeypatch.setattr(fetcher, "sync_playwright", _sync_pw)
    monkeypatch.setattr(fetcher, "_chrome", _chrome)
    rc = fetcher.cmd_confirm_invoice({"prod_base_url": "http://x", "ingest_token": "t",
                                      "vendor_id": "A01029796"})
    return rc, (reports[0] if reports else None), page


def test_button_absent_never_posts(fetcher, monkeypatch):
    """★핵심 변이 표적: 게이트를 지우면 여기서 POST가 한 번 더 나간다."""
    # 임대되는 PO는 139791428이므로 그 번호가 든 «버튼 없는 상세»를 준다.
    page = FakePage([{"status": 200, "body": _NO_BUTTON_HTML.replace("140163784", "139791428")}])
    rc, report, page = _run_confirm_with(fetcher, monkeypatch, page)
    assert rc == 0
    assert report["precheck"] == "button_absent"
    # POST를 안 보냈다 = evaluate가 «사전 GET 1회»로 끝났다.
    assert len(page.calls) == 1
    assert "http_status" not in report and "response_body" not in report


def test_fetch_failed_never_posts(fetcher, monkeypatch):
    page = FakePage([{"status": 502, "body": "gw"}])
    rc, report, page = _run_confirm_with(fetcher, monkeypatch, page)
    assert rc == 0
    assert report["precheck"] == "fetch_failed"
    assert len(page.calls) == 1


def test_button_present_posts_exactly_once(fetcher, monkeypatch):
    page = FakePage([
        {"status": 200, "body": _BUTTON_HTML},          # 사전 GET
        {"status": 200, "body": '{"success":true}'},    # POST
    ])
    rc, report, page = _run_confirm_with(fetcher, monkeypatch, page)
    assert rc == 0
    assert report["precheck"] == "button_present"
    assert report["http_status"] == 200
    assert report["response_body"] == '{"success":true}'
    # 정확히 2회 — 사전 GET 1 + POST 1. POST가 두 번이면 같은 확인을 두 번 누른 것이다.
    assert len(page.calls) == 2
    assert page.calls[1][0] is fetcher._FETCH_FORM_POST_JS
    assert page.calls[1][1] == ["/scm/purchase/order/confirmInvoice?purchaseOrderSeq=139791428"]


def test_post_is_not_retried(fetcher, monkeypatch):
    """★POST는 단발이다(`retries=1`). 기본값(2)으로 되돌리는 변이가 여기서 죽는다."""
    page = FakePage([
        {"status": 200, "body": _BUTTON_HTML},
        RuntimeError("evaluate 실패"),
        # 재시도가 살아 있으면 이 응답을 먹고 rc=0으로 «성공»해 버린다.
        {"status": 200, "body": '{"success":true}'},
    ])
    rc, report, page = _run_confirm_with(fetcher, monkeypatch, page)
    assert rc == 1
    # POST 시도는 1회뿐 — 남은 응답이 소비되지 않았다.
    assert len(page.calls) == 2
    assert len(page.responses) == 1
    # ★실패로 «단정»하지 않는다: http_status가 없으므로 백엔드가 unknown으로 잠근다.
    assert "http_status" not in report
    assert "예외" in report["error"]


def test_no_pending_command_does_nothing(fetcher, monkeypatch):
    """대기 명령이 없으면 창을 띄우지 않는다 — Chrome은 누를 때만 뜬다."""
    page = FakePage([])
    rc, report, page = _run_confirm_with(fetcher, monkeypatch, page, claim=False)
    assert rc == 0 and report is None and page.calls == []


# ──────────────────────────────────────────────
# ③ 요청 규격 — 실측(ref 106 §3)과 어긋나면 깨진다
# ──────────────────────────────────────────────
def test_form_post_js_matches_observed_jquery_shape(fetcher):
    """`$.post(url, callback)` = 바디 없음 · form-urlencoded · X-Requested-With."""
    js = fetcher._FETCH_FORM_POST_JS
    assert "method: 'POST'" in js
    assert "credentials: 'include'" in js
    assert "application/x-www-form-urlencoded" in js
    assert "X-Requested-With" in js
    # ★바디를 실으면 실측과 달라진다 — 없는 바디를 지어내지 않는다.
    #   («반환값»의 `body: await r.text()`와 «요청 옵션»의 body를 섞지 않도록 요청 부분만 본다.)
    init = js.split("fetch(path, {", 1)[1].split("});", 1)[0]
    assert "body" not in init


def test_confirm_path_and_marker_are_the_observed_ones(fetcher):
    assert fetcher.CONFIRM_INVOICE_PATH == "/scm/purchase/order/confirmInvoice"
    assert fetcher.CONFIRM_BUTTON_MARKER == 'id="btnConfirmInvoice"'


# ──────────────────────────────────────────────
# ④ 보고의 자백 — 200이 «받아들여졌다»가 아니다 (적대 리뷰 1R P1-2)
# ──────────────────────────────────────────────
class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"accepted": True, "command_id": 1, "state": "succeeded"}, True),
        # ★지각 보고: 200인데 거부다. True로 세면 이 층에서 사건이 아예 관측되지 않는다.
        ({"accepted": False, "recorded": True, "reason": "이미 종결된 명령입니다(unknown)"}, False),
        ({"accepted": False, "recorded": False, "reason": "알 수 없는 lease"}, False),
        (None, True),   # JSON이 아니면 종전대로 성공 취급(보수적으로 바꾸지 않는다)
    ],
)
def test_report_does_not_read_200_as_accepted(fetcher, monkeypatch, payload, expected):
    monkeypatch.setattr(fetcher.requests, "post", lambda *a, **k: _Resp(200, payload))
    ok = fetcher._prod_confirm_report(
        {"prod_base_url": "http://x", "ingest_token": "t"}, {"lease": "L"}
    )
    assert ok is expected


def test_confirm_failure_does_not_touch_the_refresh_channel(fetcher):
    """★P2-3: 확인 실패를 갱신 채널에 lease 없이 보고하면 **남이 claim한 갱신 요청이 소멸**한다.

    poll 루프의 확인 분기에 `_prod_report_failure` 호출이 남아 있으면 안 된다 —
    확인 채널은 자기 보고 경로를 이미 갖고 있다.
    """
    import inspect

    src = inspect.getsource(fetcher.cmd_poll)
    confirm_branch = src.split("_prod_confirm_pending", 1)[1].split("if needs_run:", 1)[0]
    assert "_prod_report_failure" not in confirm_branch
