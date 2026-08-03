# test_coupang_auth.py — 세션 자가 복구 판정 가드 (적대적 리뷰 P1/P2 회귀).
#   ★존재 이유: 이 판정이 틀리면 복구를 안 하고도 "성공"이 되고, 그러면 ②Keychain·③알림이
#     통째로 건너뛰어져 **사람은 아무 신호도 못 받는다**. 조용한 무력화가 최악이다.
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import coupang_auth as auth  # noqa: E402


class TestLandingPredicateGuard:
    def test_출발URL을_착지로_인정하는_판정자는_거부된다(self):
        """적대적 리뷰 P1: 로켓이 SSO_LOGIN_URL=origin/, is_landed=startswith(origin)이라
        아무 데도 안 가고 성공이 됐다. 이제 즉시 예외로 죽는다."""
        origin = "https://supplier.coupang.com"
        bad = lambda u: u.startswith(origin)  # noqa: E731 — 옛 구현 재현
        with pytest.raises(auth.LandingPredicateError):
            auth._assert_predicate_sound(origin + "/", bad)

    def test_좁힌_판정자는_통과한다(self):
        origin = "https://supplier.coupang.com"
        good = lambda u: u.startswith(origin) and "/dashboard" in u  # noqa: E731
        auth._assert_predicate_sound(origin + "/", good)  # 예외 없어야 함

    def test_광고_페처_판정자도_건전하다(self):
        """오픽스/오하이테크는 출발(/user/login)과 착지(/marketing/dashboard)가 배타적."""
        sso = ("https://advertising.coupang.com/user/login?_cap_client=WING"
               "&returnUrl=%2Fmarketing%2Fdashboard%2Fsales")
        def is_landed(u):
            return "/marketing/dashboard" in u and not any(
                x in u.lower() for x in ("login", "/auth", "sso", "signin", "xauth"))
        auth._assert_predicate_sound(sso, is_landed)


class TestSettleBeforeJudging:
    def test_첫_판정_전에_안정화한다(self):
        """★안정화 없이 판정하면 리다이렉트 시작 전 출발 URL을 착지로 오인한다."""
        assert auth._SETTLE_MS >= 3000, "페처들의 goto 후 안정화(3000ms)보다 짧으면 안 된다"

    def test_창이_닫히면_예외가_새지_않고_False다(self):
        """적대적 리뷰 P2: wait_for_timeout은 드라이버 왕복이라 창이 닫히면 raise한다.
        새어 나가면 호출부가 rc=3(로그인 필요)을 rc=1(재시도)로 오분류한다."""
        class DeadPage:
            url = "https://x/"
            def wait_for_timeout(self, ms):
                raise RuntimeError("Target page, context or browser has been closed")
        assert auth._poll_landed(DeadPage(), lambda u: True, 10) is False


class TestCredentialLeak:
    def test_예외_메시지의_call_log를_잘라낸다(self):
        """★playwright의 page.fill은 내부 로그에 fill("<값>")을 남기고, 실패 시 그 call log가
        예외 메시지에 붙는다 → str(e)를 그대로 찍으면 평문 비밀번호가 로그에 남는다."""
        import inspect
        src = inspect.getsource(auth.try_auto_login)
        assert 'split("Call log:")' in src, "call log 절단이 사라졌다 — 비번이 로그로 샌다"
        assert "str(e)[:100]" not in src, "길이 절단은 우연한 방어다 — 구조로 막아야 한다"

    def test_keychain_반환값이_로그로_안_간다(self):
        import inspect
        src = inspect.getsource(auth.keychain_get)
        # 반환값(pw)을 log에 넘기는 호출이 없어야 한다
        assert "log" not in src.split("return r.stdout")[1].split("except")[0]

    def test_미등록_계정은_None(self):
        assert auth.keychain_get("__존재하지_않는_계정__") is None


class TestFetcherPredicates:
    """실제 페처의 판정자를 **호출해서** 건전한지 본다.

    ★소스에 문자열이 있는지 보는 검사는 무력하다: 판정자 본문을 `return True`로 바꿔도
      문자열 검사는 통과한다(실제로 변이 테스트에서 통과해버렸다). 행동을 검사한다.
    """

    def _mod(self, name):
        import importlib
        return importlib.import_module(name)

    def test_로켓_판정자가_출발URL을_거부한다(self):
        m = self._mod("rocket_supplier_fetcher")
        assert m._is_landed(m.SSO_LOGIN_URL) is False, (
            "출발 URL이 착지로 인정되면 복구 없이 성공이 되고 ②③이 건너뛰어진다")
        # 인증 후 경로는 인정해야 한다(과잉 차단이면 항상 실패로 떨어진다)
        assert m._is_landed(m.SUPPLIER_ORIGIN + "/dashboard/KR") is True
        # keycloak 리다이렉트는 거부
        assert m._is_landed("https://xauth.coupang.com/auth/realms/seller/x") is False
        # ★착지는 **존재의 증명**이어야 한다 — "로그인 페이지가 아님"만으로는 부족하다.
        #   빈 페이지·오리진 루트는 아직 아무 데도 안 간 상태다.
        assert m._is_landed("about:blank") is False
        assert m._is_landed(m.SUPPLIER_ORIGIN + "/") is False
        # 런타임 가드가 이 판정자를 통과시켜야 한다
        auth._assert_predicate_sound(m.SSO_LOGIN_URL, m._is_landed)

    def test_오하이테크_판정자가_출발URL을_거부한다(self):
        m = self._mod("ohitech_ad_fetcher")
        assert m._is_landed(m.SSO_LOGIN_URL) is False
        assert m._is_landed("https://advertising.coupang.com/marketing/dashboard/sales") is True
        assert m._is_landed("https://advertising.coupang.com/user/login") is False
        # ★"로그인 페이지가 아님"만으로 착지를 인정하면 빈 페이지·오리진 루트가 통과한다.
        assert m._is_landed("about:blank") is False
        assert m._is_landed("https://advertising.coupang.com/") is False
        auth._assert_predicate_sound(m.SSO_LOGIN_URL, m._is_landed)


class TestConfigKeySeparation:
    """적대적 리뷰 P2: 오픽스와 같은 키 이름을 쓰면 config 복사 시 계정이 섞이고,
    vendor_id는 리터럴로 push되므로 **남의 광고비가 우리 vendor로 조용히 적재**된다."""

    def _load(self, name):
        p = pathlib.Path(__file__).resolve().parents[1] / f"{name}.py"
        return p.read_text(encoding="utf-8")

    def test_오하이테크는_오픽스와_다른_키를_쓴다(self):
        src = self._load("ohitech_ad_fetcher")
        assert 'cfg.get("ohitech_ad_login_id")' in src
        assert 'cfg.get("ad_login_id")' not in src, "오픽스와 같은 키 = 복사 사고 경로"

    def test_로켓도_고유_키를_쓴다(self):
        src = self._load("rocket_supplier_fetcher")
        assert 'cfg.get("supplier_login_id")' in src

    def test_셋업_스크립트가_그_키들을_쓴다(self):
        p = pathlib.Path(__file__).resolve().parents[1] / "setup_fetcher_autologin.sh"
        s = p.read_text(encoding="utf-8")
        assert "ohitech_ad_login_id" in s and "supplier_login_id" in s


class TestRealLoginFormDom:
    """★2026-08-03 라이브에서 ②층이 죽은 자리의 회귀.

    실측 DOM(xauth.coupang.com, client_id=supplier-hub, title='Supplier Hub'):
      inputs : name=username / name=password  — **id가 하나도 없다**
      buttons: button[type=submit]'로그인'(로그인 폼) + button[type=submit]'가입하기'(가입 폼)
    초판은 `#username`/`#kc-login`을 요구해 폼을 코앞에 두고 15초 타임아웃으로 죽었다.
    """

    # ── 가짜 DOM ──────────────────────────────────────────────────────
    # ★셀렉터 문자열 집합으로 모델링하면 안 된다(적대적 리뷰 P2): DOM 순서·중복을 재현하지 못해
    #   취약한 셀렉터를 계약으로 고정해버린다. 폼 2개를 실제 구조로 두되 **가입 폼을 앞에** 놓아
    #   '첫 매치를 집는' playwright 기본 동작이 위험한 순서를 그대로 재현한다.
    _REGISTER = {
        "action": "https://xauth.coupang.com/auth/realms/seller/login-actions/registration",
        "inputs": {"username": "", "password": "", "password-confirm": ""},
        "submits": ["가입하기"],
    }
    _LOGIN = {
        "action": "https://xauth.coupang.com/auth/realms/seller/login-actions/authenticate?session_code=x",
        "inputs": {"username": "", "password": ""},   # ★id는 하나도 없다(실측)
        "submits": ["로그인"],
    }

    class _Loc:
        """필요한 만큼만 흉내 낸 playwright Locator(비-strict: first 매치를 쓴다)."""

        def __init__(self, page, forms, sel=None):
            self.page, self.forms, self.sel = page, forms, sel

        def count(self):
            return len(self.forms)

        @property
        def first(self):
            return self

        def locator(self, sel):
            return type(self)(self.page, self.forms, sel)

        def _target(self):
            if not self.forms:
                raise RuntimeError(f"Timeout: no element for {self.sel}")
            return self.forms[0]

        def fill(self, val):
            f = self._target()
            name = self.sel.split("name=")[1].rstrip("]")
            if name not in f["inputs"]:
                raise RuntimeError(f"Timeout: no {self.sel}")
            f["inputs"][name] = val
            self.page.filled.append((f["action"], name, val))

        def click(self, timeout=None):
            f = self._target()
            if self.sel not in ("button[type=submit]", "input[type=submit]"):
                raise RuntimeError(f"Timeout: no {self.sel}")   # #kc-login 등은 없다
            if self.sel == "input[type=submit]":
                raise RuntimeError("Timeout: no input[type=submit]")
            self.page.clicked.append((f["action"], f["submits"][0]))
            self.page.url = "https://supplier.coupang.com/dashboard/KR"

    class _FormPage:
        """실측 DOM(가입 폼 + 로그인 폼)을 가진 가짜 페이지."""

        def __init__(self, forms=None):
            import copy
            self.url = "https://xauth.coupang.com/auth/realms/seller/protocol/openid-connect/auth"
            self.forms = copy.deepcopy(forms) if forms is not None else copy.deepcopy(
                [TestRealLoginFormDom._REGISTER, TestRealLoginFormDom._LOGIN])
            self.filled: list = []
            self.clicked: list = []
            self.goto_urls: list = []

        def _has_form(self):
            return any(f["inputs"] for f in self.forms)

        def wait_for_selector(self, sel, timeout=None):
            if not self._has_form():
                raise RuntimeError(f"Timeout: selector not found: {sel}")

        def locator(self, sel):
            if sel.startswith("form[action*="):
                needle = sel.split("'")[1]
                m = [f for f in self.forms if needle in f["action"]]
            elif sel.startswith("form:has("):
                inner = sel[len("form:has("):-1]
                name = inner.split("name=")[1].rstrip("]")
                m = [f for f in self.forms if name in f["inputs"]]
            else:
                m = list(self.forms)
            return TestRealLoginFormDom._Loc(self, m, sel)

        def goto(self, url, **kw):
            self.goto_urls.append(url)
            self.url = url

        def wait_for_timeout(self, ms):
            pass

        def evaluate(self, js):
            return False

    def test_id_없는_폼에서도_자동로그인이_완주한다(self, monkeypatch):
        monkeypatch.setattr(auth, "keychain_get", lambda a, s=None: "pw")
        pg = self._FormPage()
        res = auth.try_auto_login(
            pg, "ohitech",
            lambda u: u.startswith("https://supplier.coupang.com") and "/dashboard" in u,
            timeout_s=10,
        )
        assert res == auth.OK, "id 없는 실제 폼에서 죽으면 안 된다(13:34:00 라이브 결함)"
        assert [n for _, n, _ in pg.filled] == ["username", "password"]

    def test_비밀번호가_가입_폼에_들어가지_않는다(self, monkeypatch):
        """★적대적 리뷰 P1: keycloak 가입 폼에도 name=password가 있고, page.fill은 strict가
        아니라 **첫 매치**를 채운다. 가입 폼이 앞서면 평문 비번이 회원가입으로 제출된다."""
        monkeypatch.setattr(auth, "keychain_get", lambda a, s=None: "SECRET")
        pg = self._FormPage()   # 가입 폼이 **앞**에 있다
        auth.try_auto_login(pg, "ohitech", lambda u: "/dashboard" in u, timeout_s=10)
        assert pg.filled, "아무것도 안 채웠다면 이 검사는 무의미하다"
        for action, name, val in pg.filled:
            assert "registration" not in action, (
                f"비밀번호가 가입 폼({action})에 들어갔다 — 회원가입으로 평문 전송된다")
        assert all("authenticate" in a for a, _, _ in pg.filled)

    def test_제출은_로그인_폼_안에서만_눌린다(self, monkeypatch):
        """'가입하기'도 button[type=submit]이다 — 전역 셀렉터는 그걸 누른다."""
        monkeypatch.setattr(auth, "keychain_get", lambda a, s=None: "pw")
        pg = self._FormPage()
        auth.try_auto_login(pg, "ohitech", lambda u: "/dashboard" in u, timeout_s=10)
        assert pg.clicked, "제출을 아예 안 눌렀다"
        for action, label in pg.clicked:
            assert label == "로그인" and "authenticate" in action, (
                f"엉뚱한 버튼을 눌렀다: {label} @ {action}")

    def test_로그인_폼이_모호하면_시끄럽게_실패한다(self, monkeypatch):
        """아무거나 고르는 것보다 사람을 부르는 게 낫다 — 엉뚱한 폼에 비번을 넣는 게 최악이다."""
        monkeypatch.setattr(auth, "keychain_get", lambda a, s=None: "pw")
        dup = dict(self._LOGIN, action="https://x/login-actions/authenticate?session_code=1")
        dup2 = dict(self._LOGIN, action="https://x/login-actions/authenticate?session_code=2")
        pg = self._FormPage(forms=[dup, dup2])
        assert auth.try_auto_login(pg, "ohitech", lambda u: True, timeout_s=5) == auth.LOGIN_REQUIRED
        assert pg.filled == [], "모호한데도 채웠다"

    def test_verify가_폼을_치워도_키체인층은_되돌아간다(self, monkeypatch):
        """★13:58:00 라이브 결함: ①과 ② 사이의 verify(권위값 검사)가 대시보드로 **이동**해
        ②가 쓸 폼을 페이지에서 치웠다. ②는 입력칸 없는 화면에서 15초 기다리다 죽었다 —
        권위값 검사가 자기 뒤의 층을 망가뜨린 것이다."""
        monkeypatch.setattr(auth, "keychain_get", lambda a, s=None: "pw")

        DASH = "https://advertising.coupang.com/marketing/dashboard/sales"
        ROLE = "https://advertising.coupang.com/user/login?callback_url=x"  # 입력칸 0개

        forms = [dict(self._LOGIN)]

        class _FormOnlyAtSso(self._FormPage):
            """폼은 **로그인 진입으로 goto했을 때만** 존재한다(실제와 같다)."""
            def __init__(self):
                super().__init__(forms=forms)
                self.url = ROLE
                self.forms = []          # 시작 = 역할 선택 화면(입력칸 0개)

            def goto(self, url, **kw):
                super().goto(url, **kw)
                import copy
                self.forms = copy.deepcopy(forms) if "_cap_client" in url else []

        pg = _FormOnlyAtSso()
        sso = "https://advertising.coupang.com/user/login?_cap_client=SUPPLIERHUB&_cap_market=KR"

        def verify_navigates():
            """★권위값 검사는 '실제로 들어가지는지'라 본질적으로 **이동한다** — 그게 폼을 치운다."""
            pg.goto(DASH)   # → on_form=False, url=역할 선택 화면과 같은 상태
            pg.url = ROLE
            return False

        res = auth.ensure_session(
            pg, sso_url=sso,
            is_landed=lambda u: "/dashboard/KR" in u,
            login_id="ohitech",
            verify=verify_navigates,
            sso_timeout_s=0, login_timeout_s=10,
        )
        assert res == auth.OK, "②는 폼으로 되돌아가서 시작해야 한다(재진입은 멱등)"
        # ①의 진입과 ②의 재진입, 최소 2번은 로그인 진입으로 갔어야 한다
        assert pg.goto_urls.count(sso) >= 2, "verify가 치운 뒤 재진입하지 않았다"

    def test_재진입_중_착지하면_비번을_안_쓴다(self, monkeypatch):
        """재진입이 곧 SSO 재시도다 — 그 사이 세션이 살아나면 폼을 기다릴 이유가 없다."""
        monkeypatch.setattr(auth, "keychain_get", lambda a, s=None: "pw")

        class _Recovered(self._FormPage):
            def goto(self, url, **kw):
                self.goto_urls.append(url)
                self.url = "https://supplier.coupang.com/dashboard/KR" if url != "about:blank" else url

        pg = _Recovered()
        res = auth.try_auto_login(
            pg, "ohitech", lambda u: "/dashboard/KR" in u,
            form_url="https://x/login", timeout_s=10,
        )
        assert res == auth.OK
        assert pg.filled == [], "이미 착지했는데 비밀번호를 입력하면 안 된다"

    def test_goto가_실패하면_stale_URL을_착지로_인정하지_않는다(self, monkeypatch):
        """★적대적 리뷰 P1(내 수정이 만든 신규 회귀): _goto_reset은 실패를 삼키므로 창이 죽으면
        page.url이 **직전 URL 그대로** 남는다. 바로 앞 verify가 대시보드로 이동해뒀다면 그 stale
        URL이 is_landed를 만족해 **아무 데도 안 가고, 비번도 안 넣고, OK**가 된다 — ③알림까지
        건너뛰어 사람은 아무 신호도 못 받는다. 조용한 무력화가 최악이다."""
        monkeypatch.setattr(auth, "keychain_get", lambda a, s=None: "pw")

        class _DeadNav(self._FormPage):
            """goto가 전부 실패한다(창 소실). url은 verify가 남긴 대시보드 그대로."""
            def __init__(self):
                super().__init__()
                self.url = "https://advertising.coupang.com/marketing/dashboard/sales"
                self.forms = []          # 폼도 없다

            def goto(self, url, **kw):
                self.goto_urls.append(url)
                raise RuntimeError("Target page, context or browser has been closed")

        pg = _DeadNav()
        res = auth.try_auto_login(
            pg, "ohitech", lambda u: "/marketing/dashboard" in u,
            form_url="https://advertising.coupang.com/user/login?_cap_client=SUPPLIERHUB",
            timeout_s=10,
        )
        assert res == auth.LOGIN_REQUIRED, "이동에 실패했는데 stale URL로 복구 성공이 되면 안 된다"
        assert pg.filled == []

    def test_제출_버튼을_못_찾으면_수동_폴백(self, monkeypatch):
        """조용히 성공으로 넘어가면 사람은 아무 신호도 못 받는다."""
        monkeypatch.setattr(auth, "keychain_get", lambda a, s=None: "pw")
        no_submit = dict(self._LOGIN, submits=[])
        pg = self._FormPage(forms=[no_submit])
        assert auth.try_auto_login(pg, "ohitech", lambda u: True) == auth.LOGIN_REQUIRED


class TestOhitechLoginClient:
    """★오하이테크는 **로켓배송(1P) 공급자** 계정이다 — 오픽스의 WING 진입을 그대로 쓰면
    세션이 풀렸을 때 입력칸이 0개인 '역할 선택' 화면에 멈춰 3층이 통째로 무력해진다.

    라이브 실측(2026-08-03) 체인:
      /user/login?_cap_client=SUPPLIERHUB&_cap_market=KR
        → /login_sxauth?client=SUPPLIERHUB&market=KR
        → xauth realms/seller?client_id=supplier-hub&redirect_uri=.../keycloak_callback
        → /keycloak_callback → /marketing/dashboard/sales   (세션 살아있으면 비번 없이)
    ★`SUPPLIER`도, `_cap_market` 누락도 역할 선택 화면으로 되돌아간다(둘 다 실측).
    """

    def _oh(self):
        import ohitech_ad_fetcher as oh
        return oh

    def test_진입_클라이언트가_SUPPLIERHUB다(self):
        url = self._oh().SSO_LOGIN_URL
        assert "_cap_client=SUPPLIERHUB" in url, (
            "WING 진입은 로켓배송 계정에서 역할 선택 화면으로 튕긴다(13:52 실측)")
        assert "_cap_client=SUPPLIER&" not in url, "SUPPLIER는 통하지 않는다 — SUPPLIERHUB다"
        assert "_cap_market=KR" in url, "시장 파라미터가 없으면 역할 선택 화면으로 되돌아간다"

    def test_오픽스와_다른_진입을_쓴다(self):
        import ad_cost_browser_fetcher as ofix  # 오픽스는 WING 그대로여야 한다
        assert "_cap_client=WING" in ofix.SSO_LOGIN_URL
        assert self._oh().SSO_LOGIN_URL != ofix.SSO_LOGIN_URL, (
            "계정 유형이 다르면 진입 클라이언트도 달라야 한다")

    def test_판정자는_여전히_건전하다(self):
        oh = self._oh()
        assert oh._is_landed(oh.SSO_LOGIN_URL) is False
        auth._assert_predicate_sound(oh.SSO_LOGIN_URL, oh._is_landed)


class TestVerifyIsAuthoritative:
    """★URL 판정은 휴리스틱, 앱 세션 검사가 권위값.

    둘을 하나로 쓰면 내 URL 추측이 틀렸을 때 **진짜 복구를 실패로 오판**해 사람에게
    불필요한 로그인 알림이 간다(2026-08-03: supplier SSO returnUrl이 오리진 루트라
    /dashboard를 요구하는 판정이 정상 복구를 거부할 여지가 있었다).
    """

    class _Page:
        def __init__(self, url="https://x/login"):
            self.url = url
        def goto(self, u, **kw):
            self.url = u
        def wait_for_timeout(self, ms):
            pass

    def test_URL판정_실패해도_앱검사가_통과하면_OK(self, monkeypatch):
        monkeypatch.setattr(auth, "notify_mac", lambda *a, **k: None)
        r = auth.ensure_session(
            self._Page(),
            sso_url="https://x/login",
            is_landed=lambda u: False,      # 절대 착지 안 했다고 우기는 판정자
            login_id=None,                   # ②는 비활성
            verify=lambda: True,             # 앱은 세션 살아있다고 답한다
            sso_timeout_s=0, login_timeout_s=0,
        )
        assert r == auth.OK, "앱이 살아있다는데 URL 추측으로 실패 처리하면 안 된다"

    def test_앱검사도_실패하면_LOGIN_REQUIRED(self, monkeypatch):
        called = {}
        monkeypatch.setattr(auth, "notify_mac", lambda t, m, **k: called.setdefault("n", (t, m)))
        r = auth.ensure_session(
            self._Page(),
            sso_url="https://x/login",
            is_landed=lambda u: False,
            login_id=None,
            verify=lambda: False,
            sso_timeout_s=0, login_timeout_s=0,
        )
        assert r == auth.LOGIN_REQUIRED
        assert "n" in called, "복구 실패면 사람을 불러야 한다"

    def test_verify가_예외를_던져도_복구됨으로_오판하지_않는다(self, monkeypatch):
        monkeypatch.setattr(auth, "notify_mac", lambda *a, **k: None)
        def boom():
            raise RuntimeError("창 닫힘")
        r = auth.ensure_session(
            self._Page(), sso_url="https://x/login", is_landed=lambda u: False,
            login_id=None, verify=boom, sso_timeout_s=0, login_timeout_s=0,
        )
        assert r == auth.LOGIN_REQUIRED

    def test_두_페처가_verify를_넘긴다(self):
        import inspect
        import rocket_supplier_fetcher as rk
        import ohitech_ad_fetcher as oh
        assert "verify=" in inspect.getsource(rk._recover_session)
        assert "verify=" in inspect.getsource(oh._recover_session)
