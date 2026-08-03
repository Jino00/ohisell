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
