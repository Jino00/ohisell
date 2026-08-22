# test_wing_login_watch.py — W2/W3 로그인 워치·탭 수명 가드 (2026-08-22 적대 리뷰 1R 후속).
#
# ★존재 이유: 리뷰가 변이 주입으로 실증했다 — 이 파일이 없던 시점에
#   `tools/wing_browser_fetcher.py`에 이번 계약이 추가한 코드는 **테스트가 0건**이었다.
#   탭 유지(keep_open)를 무시하게 바꿔도, 프룬 방향을 뒤집어도, 금지선(새 탭 금지)을 어겨도,
#   W2의 rc 승격을 원복해도 tools 36개가 전부 초록이었다.
#   계약 판단기준 5「「창을 남긴다」는 항상 «탭 층»에서 검증한다」가 코드에는 반영됐는데
#   **검증 층에는 반영되지 않았다** — 그 간극을 메운다.
#
# ★Playwright를 띄우지 않는다. 여기서 지키려는 성질은 전부 «어느 탭을 고르는가 / 무엇을
#   닫는가 / 무엇을 부르지 않는가»라 스텁으로 충분하고, 브라우저를 띄우면 CI에서 못 돈다.
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import wing_browser_fetcher as w  # noqa: E402


class _StubPage:
    def __init__(self, url: str):
        self.url = url
        self.closed = False
        self.goto_calls = 0

    def close(self):
        self.closed = True

    def goto(self, *a, **kw):        # 워치 경로에서 불리면 그 자체가 결함이다(P1-4)
        self.goto_calls += 1


class _StubCtx:
    def __init__(self, pages):
        self.pages = pages
        self.new_page_calls = 0

    def new_page(self):
        self.new_page_calls += 1
        pg = _StubPage("about:blank")
        self.pages.append(pg)
        return pg


# ── _page_host: 부분문자열 금지 (P1-2) ─────────────────────────────────────
class TestPageHost:
    def test_m_wing과_wing을_가른다(self):
        """★이 저장소가 이미 값을 치른 사고다(_rg_off_origin docstring, 2026-08-03).

        `"wing.coupang.com" in url`은 m-wing URL에서도 참이라, RG 워치가 VS 탭을 집었다.
        """
        assert w._page_host(_StubPage("https://m-wing.coupang.com/x")) == "m-wing.coupang.com"
        assert w._page_host(_StubPage("https://wing.coupang.com/y")) == "wing.coupang.com"
        assert w._page_host(_StubPage("https://m-wing.coupang.com/x")) != w._RG_ORIGIN_HOST

    def test_keycloak_로그인URL은_목적지_호스트로_읽히지_않는다(self):
        """로그인 URL은 redirect_uri에 목적지를 싣는다 — 부분문자열이면 여기서 무너진다."""
        u = ("https://xauth.coupang.com/auth/realms/seller/protocol/openid-connect/auth"
             "?response_type=code&client_id=wing&redirect_uri=https%3A%2F%2Fwing.coupang.com%2F")
        assert w._page_host(_StubPage(u)) == "xauth.coupang.com"
        assert w._page_host(_StubPage(u)) != w._RG_ORIGIN_HOST

    def test_about_blank과_빈값은_빈_호스트다(self):
        assert w._page_host(_StubPage("about:blank")) == ""
        assert w._page_host(_StubPage("")) == ""


# ── _login_watch_probe: 탭 선택과 금지선 ────────────────────────────────────
class TestLoginWatchProbe:
    def _run(self, monkeypatch, pages, host, probe):
        """CDP 연결을 스텁으로 갈아끼우고 _login_watch_probe를 돌린다."""
        ctx = _StubCtx(pages)

        class _Browser:
            contexts = [ctx]

            def close(self):
                pass

        class _Chromium:
            @staticmethod
            def connect_over_cdp(_url):
                return _Browser()

        class _PW:
            chromium = _Chromium()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(w, "sync_playwright", lambda: _PW())
        monkeypatch.setattr(w, "_cdp_mode", lambda _cfg: True)
        out = w._login_watch_probe({"cdp_port": 9222}, host=host, probe=probe)
        return out, ctx

    def test_RG워치는_m_wing탭을_집지_않는다(self, monkeypatch):
        """★P1-2 재현 가드. VS가 남긴 m-wing 탭이 **먼저** 있는 통상 배치."""
        m_wing = _StubPage("https://m-wing.coupang.com/tenants/business-insight/x")
        wing = _StubPage("https://wing.coupang.com/tenants/rfm/settlements/status-new")
        picked = []
        out, _ = self._run(monkeypatch, [m_wing, wing], w._RG_ORIGIN_HOST,
                           lambda pg: (picked.append(pg), True)[1])
        assert picked == [wing], "RG 워치가 m-wing 탭을 집으면 자동 회복이 영원히 발화 안 한다"
        assert out is True

    def test_VS워치는_wing탭을_집지_않는다(self, monkeypatch):
        m_wing = _StubPage("https://m-wing.coupang.com/x")
        wing = _StubPage("https://wing.coupang.com/y")
        picked = []
        self._run(monkeypatch, [wing, m_wing], w._VS_ORIGIN_HOST,
                  lambda pg: (picked.append(pg), True)[1])
        assert picked == [m_wing]

    def test_같은_호스트_탭이_여럿이면_최신을_고른다(self, monkeypatch):
        old = _StubPage("https://wing.coupang.com/old")
        new = _StubPage("https://wing.coupang.com/new")
        picked = []
        self._run(monkeypatch, [old, new], w._RG_ORIGIN_HOST,
                  lambda pg: (picked.append(pg), True)[1])
        assert picked == [new], "방금 로그인 화면이 뜬 탭은 가장 최근에 생긴 것이다"

    def test_해당_호스트_탭이_없으면_새_탭을_만들지_않는다(self, monkeypatch):
        """★금지선: 창이 뜨는 유일한 순간은 버튼 클릭 직후여야 한다(2026-07-27)."""
        called = []
        out, ctx = self._run(monkeypatch, [_StubPage("https://example.com/")],
                             w._RG_ORIGIN_HOST, lambda pg: called.append(pg) or True)
        assert out is None, "확인 못 한 것은 False가 아니라 «확인 불가»다"
        assert ctx.new_page_calls == 0, "워치가 새 탭을 만들면 금지선 위반이다"
        assert called == []

    def test_로그인_페이지에_머물러_있으면_회복이_아니다(self, monkeypatch):
        xauth = _StubPage("https://xauth.coupang.com/auth/realms/seller/"
                          "protocol/openid-connect/auth?redirect_uri=https%3A%2F%2Fwing.coupang.com")
        out, _ = self._run(monkeypatch, [xauth], w._RG_ORIGIN_HOST, lambda pg: True)
        assert out is None

    def test_CDP모드가_아니면_확인불가(self, monkeypatch):
        monkeypatch.setattr(w, "_cdp_mode", lambda _cfg: False)
        assert w._login_watch_probe({}, host="wing.coupang.com", probe=lambda pg: True) is None


# ── _prune_stale_tabs: 무엇을 남기는가 ──────────────────────────────────────
class TestPruneStaleTabs:
    def test_오래된_것부터_닫고_최신을_남긴다(self):
        """★뒤집히면 사람이 로그인할 그 탭을 닫는다(생존 변이 #16)."""
        pages = [_StubPage(f"https://wing.coupang.com/{i}") for i in range(6)]
        w._prune_stale_tabs(_StubCtx(pages), keep=3)
        assert [p.closed for p in pages] == [True, True, True, False, False, False]

    def test_상한_이하면_아무것도_안_닫는다(self):
        pages = [_StubPage(f"https://wing.coupang.com/{i}") for i in range(3)]
        w._prune_stale_tabs(_StubCtx(pages), keep=3)
        assert not any(p.closed for p in pages)

    def test_전부_about_blank여도_상한만큼은_남긴다(self):
        """★P2-7: 전부 닫으면 탭 0개가 되어 Chrome이 스스로 종료될 수 있다."""
        pages = [_StubPage("about:blank") for _ in range(6)]
        w._prune_stale_tabs(_StubCtx(pages), keep=3)
        assert sum(1 for p in pages if not p.closed) == 3

    def test_실제_탭이_상한을_채우면_blank는_전부_버린다(self):
        blanks = [_StubPage("about:blank") for _ in range(2)]
        real = [_StubPage(f"https://wing.coupang.com/{i}") for i in range(3)]
        w._prune_stale_tabs(_StubCtx(blanks + real), keep=3)
        assert all(p.closed for p in blanks)
        assert not any(p.closed for p in real)


# ── _revive_lane: 자동 재개의 «멈추는 자리» ─────────────────────────────────
class TestReviveLane:
    def test_상한을_넘기면_재요청하지_않는다(self, monkeypatch):
        """★P1-3의 무한 루프에 대한 상한(45초 주기로 알림 2건 + 탭 1개를 영원히)."""
        calls = []
        monkeypatch.setattr(w, "_prod_request_refresh", lambda c, p: calls.append(p) or True)
        monkeypatch.setattr(w, "_notify_mac", lambda *a, **k: None)
        out = w._revive_lane({}, "RG 정산", "/p", w._MAX_AUTO_REVIVES)
        assert calls == [], "상한 소진 후에도 재요청하면 루프가 안 멈춘다"
        assert out is False, "워치도 함께 꺼야 30초마다 헛도는 것을 멈춘다"

    def test_상한_안이면_재요청한다(self, monkeypatch):
        calls = []
        monkeypatch.setattr(w, "_prod_request_refresh", lambda c, p: calls.append(p) or True)
        monkeypatch.setattr(w, "_notify_mac", lambda *a, **k: None)
        assert w._revive_lane({}, "RG 정산", "/p", 0) is False
        assert calls == ["/p"]

    def test_재요청_실패면_이어간다고_말하지_않는다(self, monkeypatch):
        """★P2-10: 아무도 안 이어받는데 이어받는다고 말하는 것이 «틀린 처방»이다."""
        msgs = []
        monkeypatch.setattr(w, "_prod_request_refresh", lambda c, p: False)
        monkeypatch.setattr(w, "_notify_mac", lambda t, m, **k: msgs.append(m))
        w._revive_lane({}, "판매분석", "/p", 0)
        assert msgs and "자동으로 이어갑니다" not in msgs[0]
        assert "눌러주세요" in msgs[0]


# ── W2: 데몬 경로가 «기다리지 않는다»와 «보지 않는다»를 구분하는가 ─────────────
class TestLoginWaitLoopProbesAtLeastOnce:
    def test_wait_secs_0에도_프로브를_1회_한다(self, monkeypatch, tmp_path):
        """★P1-3 재현 가드.

        초판은 `while waited < wait_secs`라 wait_secs=0이면 evaluate 호출이 **0회**였다.
        그러면 Chrome이 완전히 로그인돼 있어도 마커 파일이 없는 한 cmd_login이 무조건
        RC_LOGIN_REQUIRED를 내고, W3 자동 재요청과 맞물려 무한 루프가 된다.
        """
        state = tmp_path / "state.json"
        evals = []

        class _P(_StubPage):
            def evaluate(self, _js, _arg):
                evals.append(1)
                return {"status": 200, "body": "{}"}

        page = _P("https://m-wing.coupang.com/x")
        monkeypatch.setattr(w, "_is_logged_out", lambda _u: False)
        monkeypatch.setattr(w, "_is_success", lambda _r: True)
        monkeypatch.setattr(w, "_save_state", lambda *a, **k: state.write_text("{}"))
        monkeypatch.setattr(w, "_vs_payload", lambda *a, **k: {})

        res = w._login_wait_loop(page, None, {}, str(state), 0, cdp=True, window=None)
        assert len(evals) == 1, "wait_secs=0은 «기다리지 않는다»이지 «보지 않는다»가 아니다"
        assert res is not None, "로그인돼 있으면 즉시 성공으로 돌아와야 한다"

    def test_로그아웃이면_wait_secs_0에서_바로_None(self, monkeypatch, tmp_path):
        page = _StubPage("https://xauth.coupang.com/login")
        monkeypatch.setattr(w, "_is_logged_out", lambda _u: True)
        assert w._login_wait_loop(page, None, {}, str(tmp_path / "s"), 0,
                                  cdp=True, window=None) is None


# ── P1-4: 워치가 사람의 로그인 폼을 갈아엎지 않는가 ──────────────────────────
class TestWatchNeverNavigates:
    def test_VS_회복_프로브는_goto를_부르지_않는다(self, monkeypatch):
        """★`_login_watch_probe` docstring이 명시적으로 금지한 동작이다.

        초판은 `_fetch_vendor_summary`를 불렀고 그 첫 줄이 page.goto(DASH_URL)라,
        사람이 로그인 폼을 채우는 탭을 30초마다 덮어썼다.
        """
        class _P(_StubPage):
            def evaluate(self, _js, _arg):
                return {"status": 200, "body": "{}"}

        page = _P("https://m-wing.coupang.com/x")
        monkeypatch.setattr(w, "_is_logged_out", lambda _u: False)
        monkeypatch.setattr(w, "_is_success", lambda _r: True)
        monkeypatch.setattr(w, "_vs_payload", lambda *a, **k: {})
        monkeypatch.setattr(w, "_vs_windows", lambda _cfg: [(None, None)])
        monkeypatch.setattr(w, "_cdp_mode", lambda _cfg: True)

        captured = {}

        def _probe_capture(cfg, *, host, probe):
            captured["probe"] = probe
            return probe(page)

        monkeypatch.setattr(w, "_login_watch_probe", _probe_capture)
        assert w._vs_login_recovered({}) is True
        assert page.goto_calls == 0, "워치가 navigate 하면 사람의 로그인 입력이 날아간다"

    def test_소스에_goto를_부르는_수집함수가_없다(self):
        """★변이 저항: 구현을 되돌려 `_fetch_vendor_summary`를 다시 부르면 여기서 터진다."""
        import ast
        import inspect
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(w._vs_login_recovered)))
        called = {
            n.func.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        # docstring이 그 이름을 «설명»하는 것은 괜찮다 — 금지되는 것은 «호출»이다.
        assert "_fetch_vendor_summary" not in called, (
            "그 함수는 첫 줄에서 page.goto를 한다 — 워치에서 부르면 P1-4 재발"
        )


# ── W3: keep_open이 탭 층까지 지켜지는가 ────────────────────────────────────
class TestKeepOpenReachesTheTabLayer:
    """★생존 변이 #15. 초판 가드는 소스에 "owner.keep_open" 문자열이 있는지만 봤는데,
    조건을 `if True:`로 바꿔도 **바로 위 주석**에 그 문자열이 남아 통과했다 — 소스 검사가
    주석까지 세는 순간 그 가드는 아무것도 안 지킨다. 행동으로 본다.
    """

    @staticmethod
    def _drive(monkeypatch, keep_open: bool):
        """_chrome(CDP 분기)을 스텁 위에서 한 번 돌리고, 탭이 닫혔는지 돌려준다."""
        import contextlib as _c
        page = _StubPage("https://wing.coupang.com/x")
        ctx = _StubCtx([])

        class _Browser:
            contexts = [ctx]

            def close(self):
                pass

        class _Chromium:
            @staticmethod
            def connect_over_cdp(_url):
                return _Browser()

        class _PW:
            chromium = _Chromium()

        # ctx.new_page()가 우리 스텁 page를 주도록.
        monkeypatch.setattr(ctx, "new_page", lambda: page)

        owner = w._ChromeOwner()
        owner.keep_open = keep_open

        @_c.contextmanager
        def _fake_owned(_cfg, _owner=None):
            yield owner

        monkeypatch.setattr(w, "_owned_chrome", _fake_owned)
        monkeypatch.setattr(w, "_cdp_mode", lambda _cfg: True)
        monkeypatch.setattr(w, "_prune_stale_tabs", lambda *a, **k: None)

        with w._chrome(_PW(), {"cdp_port": 9222}, "/tmp/none", owner=owner):
            pass
        return page.closed

    def test_keep_open이면_탭을_남긴다(self, monkeypatch):
        """★2026-08-22 12:55 실측의 코드 원인: _owned_chrome은 «프로세스»만 지키고
        _chrome이 «탭»을 무조건 닫아, 「창은 열어 둠」이라 써 놓고 로그인할 곳이 없었다."""
        assert self._drive(monkeypatch, keep_open=True) is False

    def test_평소에는_탭을_닫는다(self, monkeypatch):
        """반대 방향도 고정한다 — 항상 남기면 탭이 무한히 쌓인다."""
        assert self._drive(monkeypatch, keep_open=False) is True
