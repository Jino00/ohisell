# test_wing_session_recovery.py — W1 세션 자가 복구 배선 가드
# (계약 CONTRACT_collection_works_everywhere, 2026-08-23)
#
# ★존재 이유: 이 배선이 없어서 2026-08-23 10:29:37에 Jino의 폰으로 「Mac Chrome에서
#   로그인하세요」가 갔다 — 같은 「전체 갱신」 회차 안에서 옆 레인 셋(오픽스 광고 10:30:04·
#   오하이테크 광고 10:30:12·로켓 공급자허브 10:30:18)은 **같은 Keychain 자격증명으로 스스로
#   복구**하고 있었다. 공용 모듈 `coupang_auth`는 2026-08-03에 Wing을 **포함한** 4종을 위해
#   만들어졌는데 Wing만 3주간 미배선이었다.
#
# ★그러므로 이 파일이 지키는 것은 「함수가 값을 만드나」가 아니라
#   **「사람을 부르기 전에 자동 복구를 시도하는가」**라는 «경로»다. 계약 W4의 변이 규율:
#   **wing에서 coupang_auth 호출을 제거하면 여기 최소 1건이 적색이어야 한다.**
#
# ★Playwright를 띄우지 않는다 — 지키려는 성질이 전부 «무엇을 부르는가 / 어떤 URL을 착지로
#   인정하는가»라 스텁으로 충분하고, 브라우저를 띄우면 CI에서 못 돈다(기존 파일과 같은 방침).
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import coupang_auth  # noqa: E402
import wing_browser_fetcher as w  # noqa: E402


# ════════════════════════════════════════════════════════════════════
# 1. 착지 판정자(_landed_on) — 두 개의 실사고를 재현 방지한다
# ════════════════════════════════════════════════════════════════════

def test_sso_url_is_not_landed():
    """★출발 URL을 착지로 인정하면 «복구를 안 하고도 성공»이 된다.

    coupang_auth._assert_predicate_sound가 이걸 LandingPredicateError로 즉시 터뜨린다
    (설정 실수는 조용히 무력화되느니 시끄럽게 죽는 게 낫다는 모듈 계약). 즉 이 단언이
    깨지면 _recover_rg_session은 **호출되는 순간 예외로 죽는다** — 배선 전체가 무의미해진다.
    """
    assert w._landed_on(w._RG_SSO_URL, w._RG_ORIGIN_HOST) is False


def test_landed_on_requires_authed_path():
    """인증 후 경로(/tenants/)에 있을 때만 착지다."""
    assert w._landed_on(
        "https://wing.coupang.com/tenants/rfm/settlements/status-new", w._RG_ORIGIN_HOST) is True
    # 오리진 루트·로그인 페이지는 착지가 아니다
    assert w._landed_on("https://wing.coupang.com/", w._RG_ORIGIN_HOST) is False


def test_landed_on_parses_host_not_substring():
    """★2026-08-03 실사고의 재현 방지 — 부분문자열 검사는 원리적으로 틀린다.

    ① keycloak 로그인 URL은 redirect_uri 쿼리에 목적지 호스트를 통째로 싣는다. 그래서
       `"wing.coupang.com" in url`은 **로그인 페이지 위에서도 참**이다.
    ② "m-wing.coupang.com"은 "wing.coupang.com"을 **포함한다**(VS 탭을 RG로 오인).
    둘 다 _rg_off_origin이 실제로 당한 사고다(로그인 창이 안 떠 아무도 로그인할 기회를 못 얻음).
    """
    keycloak = ("https://xauth.coupang.com/auth/realms/seller/protocol/openid-connect/auth"
                "?response_type=code&client_id=wing"
                "&redirect_uri=https%3A%2F%2Fwing.coupang.com%2Ftenants%2Frfm%2Fsettlements")
    assert w._landed_on(keycloak, w._RG_ORIGIN_HOST) is False, "로그인 페이지를 착지로 인정했다"

    vs_tab = "https://m-wing.coupang.com/tenants/business-insight/sales-analysis"
    assert w._landed_on(vs_tab, w._RG_ORIGIN_HOST) is False, "m-wing을 정산 오리진으로 인정했다"


def test_landed_on_survives_garbage_url():
    """판정 불가는 '착지 아님'으로 접는다(안전한 방향) — 예외가 새면 복구 경로가 통째로 죽는다."""
    for bad in ("", None, "not a url", "://"):
        assert w._landed_on(bad, w._RG_ORIGIN_HOST) is False


# ════════════════════════════════════════════════════════════════════
# 2. 복구 호출(_recover_rg_session) — 공용 모듈에 올바른 계약으로 넘기는가
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def spy_ensure(monkeypatch):
    """coupang_auth.ensure_session을 가로채 전달 인자를 검사한다."""
    seen = {}

    def _fake(page, **kw):
        seen.update(kw)
        seen["page"] = page
        return coupang_auth.OK

    monkeypatch.setattr(coupang_auth, "ensure_session", _fake)
    return seen


def test_recover_passes_login_id_from_config(spy_ensure):
    """★②Keychain층의 입력은 config의 wing_login_id다.

    이게 안 넘어가면 coupang_auth는 ②를 **에러 없이 건너뛴다**("없다고 깨지지 않는다"는 모듈
    계약). 이 레인은 ①SSO가 원리적으로 무효라(JSESSIONID 세션 쿠키·KEYCLOAK_IDENTITY 부재)
    login_id 없음 = 자동 복구 전면 무력화다 — 그래서 «조용한 성공»이 가장 위험한 실패다.
    """
    cfg = {"account_key": "COUPANG_WING1", "vendor_id": "A01564720", "wing_login_id": "ofixohi"}
    assert w._recover_rg_session(object(), cfg) == coupang_auth.OK
    assert spy_ensure["login_id"] == "ofixohi"


def test_recover_labels_the_account(spy_ensure):
    """알림·로그에 **어느 계정**인지 실린다 — 계정이 둘이라 이름 없는 알림은 처방이 안 된다."""
    cfg = {"account_key": "COUPANG_WING2", "vendor_id": "A01029796", "wing_login_id": "ohitech"}
    w._recover_rg_session(object(), cfg)
    label = spy_ensure["label"]
    assert "COUPANG_WING2" in label and "A01029796" in label


def test_recover_predicate_rejects_its_own_sso_url(spy_ensure):
    """넘긴 is_landed가 넘긴 sso_url을 착지로 인정하면 안 된다(모듈이 예외로 죽는 조건)."""
    w._recover_rg_session(object(), {"wing_login_id": "x"})
    assert spy_ensure["is_landed"](spy_ensure["sso_url"]) is False


def test_recover_warns_when_login_id_missing(spy_ensure, caplog):
    """★login_id 부재는 «침묵»이 아니라 «경고»여야 한다.

    모듈이 조용히 폴백하므로, 로그마저 침묵하면 배선을 해 두고도 「고쳤는데 안 되는」 상태를
    아무도 알아채지 못한다. 파이프라인 침묵은 이 저장소가 반복해서 당한 실패 형태다.
    """
    with caplog.at_level(logging.WARNING):
        w._recover_rg_session(object(), {"account_key": "COUPANG_WING1"})
    assert any("wing_login_id" in r.message % r.args if r.args else "wing_login_id" in r.message
               for r in caplog.records), "login_id 없음이 로그에 안 남는다"


def test_recover_shortens_sso_timeout(spy_ensure):
    """①SSO가 원리적으로 무효인 레인에서 기본 45초는 매 복구마다 그대로 손실이다."""
    w._recover_rg_session(object(), {"wing_login_id": "x"})
    assert spy_ensure["sso_timeout_s"] < 45


def test_recover_passes_app_probe_as_verify(spy_ensure):
    """★verify(앱 프로브)를 넘기는가 — 적대 리뷰 P2-1(2026-08-23, PR #342)의 유일한 생존 변이.

    리뷰어가 `verify=` 인자를 통째로 지웠는데 71건이 전부 초록이었다. 그런데 그 인자는
    **라이브에서 실제로 복구를 살린 것**이다: 2026-08-23 12:42:58 회차에서 URL 착지 판정
    (is_landed, /tenants/ 요구)이 False였고 verify가 그 판정을 뒤집어 복구가 완주했다.
    없으면 진짜 복구를 실패로 오판해 ②③으로 escalate하고 사람에게 불필요한 알림이 간다 —
    즉 「사람이 Mac에 가지 않는다」는 합격기준이 그 한 인자에 달려 있는데 가드가 없었다.
    """
    w._recover_rg_session(object(), {"wing_login_id": "x"})
    assert callable(spy_ensure.get("verify")), "verify(앱 프로브)가 coupang_auth로 안 넘어간다"


# ════════════════════════════════════════════════════════════════════
# 3. ★배선 절단 감지 — 계약 W4가 요구한 «표면 변이»
# ════════════════════════════════════════════════════════════════════

def test_rg_lane_calls_recovery_before_giving_up():
    """★AUTH(로그아웃 확증) 처리 경로가 «사람을 부르기 전에» 자동 복구를 시도하는가.

    이 저장소가 반복해서 당한 실패는 「함수는 맞는데 아무도 그 함수를 부르지 않는다」이고,
    단위 테스트는 그걸 원리적으로 못 본다(2026-08-23 오전 실사고: 화면 문구 단언 3곳이 전부
    초록인데 사용자가 본 화면은 옛 문구였다 — 테스트가 보는 함수와 사람이 보는 화면이 다른
    파일이었다). 그래서 여기서는 «호출의 존재»를 코드 객체에서 직접 본다. 거칠지만 이게 정확히
    계약 W4가 요구한 변이(「wing의 coupang_auth 호출 제거」)를 잡는 층이다.

    ★대상이 `_do_rg_run`인 이유: RG 레인의 **두 진입점이 공유하는 유일한 길목**이다 —
      ①CLI `cmd_rg` ②**폴 데몬**(08-23 10:29:37에 Jino의 폰을 실패시킨 바로 그 경로).
      아래 test_both_rg_entrypoints_route_through_the_choke_point가 그 전제를 지킨다.
      «이 함수가 유일한 길목이다»가 깨지면 배선이 한쪽만 덮으므로 그 단언이 먼저 빨개진다.
    """
    names = w._do_rg_run.__code__.co_names
    assert "_recover_rg_session" in names, (
        "_do_rg_run이 _recover_rg_session을 부르지 않는다 — 자동 복구 배선이 끊겼다. "
        "이 단언이 빨간 것은 08-23 10:29:37 상태(사람을 부르고 끝남)로의 회귀를 뜻한다."
    )


def test_both_rg_entrypoints_route_through_the_choke_point():
    """★위 테스트의 전제 — RG 진입점이 전부 `_do_rg_run`을 지나는가.

    누군가 데몬 경로를 다른 함수로 갈라내면 위 단언은 **초록인 채** 라이브만 옛 동작으로
    돌아간다(「고쳤는데 안 되는」의 전형). 그래서 길목의 유일성 자체를 단언한다.
    """
    assert "_do_rg_run" in w.cmd_rg.__code__.co_names, "CLI 경로가 길목을 우회한다"

    # 폴 데몬 경로: RG 분기를 담은 함수가 _do_rg_run을 참조해야 한다. 데몬 루프는 중첩
    # 람다로 호출하므로 모듈의 어느 코드 객체가 그 이름을 갖는지 재귀로 찾는다.
    import types

    def _refs(code, name, seen=None):
        seen = seen if seen is not None else set()
        if id(code) in seen:
            return False
        seen.add(id(code))
        if name in code.co_names:
            return True
        return any(_refs(c, name, seen) for c in code.co_consts
                   if isinstance(c, types.CodeType))

    daemon = getattr(w, "cmd_poll", None) or getattr(w, "_poll_loop", None)
    assert daemon is not None, "폴 데몬 진입점을 못 찾았다 — 이름이 바뀌었으면 이 테스트를 갱신할 것"
    assert _refs(daemon.__code__, "_do_rg_run"), "데몬 경로가 길목을 우회한다"


def test_recovery_helper_uses_shared_module():
    """복구는 공용 모듈로만 한다 — 레인별 재발명은 「한 곳만 수리」 재발의 기제였다."""
    assert "coupang_auth" in w._recover_rg_session.__code__.co_names or \
           "ensure_session" in w._recover_rg_session.__code__.co_names


# ════════════════════════════════════════════════════════════════════
# 4. VS(판매분석) 경로 — 계약 W1의 «나머지 절반» (2026-08-23 라이브 실사고)
# ════════════════════════════════════════════════════════════════════
#
# ★왜 뒤늦게 생겼나: 계약 W1 원문은 *"WING1·WING2 계정별, **VS·RG 양 경로**의 로그아웃 확증
#   지점"*에 배선하라고 적었는데 **RG 절반만 구현된 채 W1이 완료로 표시**됐다. 잡은 것은
#   테스트도 리뷰도 아니고 **Jino가 폰에서 누른 버튼**이었다 — 17:09 한 회차 안에서
#     17:09:12  VS(판매분석) → 사람 호출
#     17:10:26  RG(정산)     → 자동 재로그인으로 복구, 사람 개입 0
#   같은 계정·같은 Keychain 항목인데 한 레인만 사람을 불렀고, 그 하나 때문에 합격 ①이 미달이었다.

def test_recover_vs_passes_login_id_from_config(spy_ensure):
    """②Keychain층의 입력은 config의 wing_login_id다 — RG와 같은 계약."""
    cfg = {"account_key": "COUPANG_WING1", "vendor_id": "A01564720", "wing_login_id": "ofixohi"}
    assert w._recover_vs_session(object(), cfg) == coupang_auth.OK
    assert spy_ensure["login_id"] == "ofixohi"


def test_recover_vs_labels_the_lane_and_account(spy_ensure):
    """알림·로그에 **어느 계정의 어느 레인**인지 실린다 — RG와 VS가 같은 계정에 둘 다 있다."""
    w._recover_vs_session(object(), {"account_key": "COUPANG_WING1", "vendor_id": "A01564720",
                                     "wing_login_id": "x"})
    label = spy_ensure["label"]
    assert "COUPANG_WING1" in label and "A01564720" in label
    assert "판매분석" in label, "RG와 구분되는 레인 이름이 없으면 알림만 보고는 어디를 볼지 모른다"


def test_recover_vs_predicate_rejects_its_own_sso_url(spy_ensure):
    """넘긴 is_landed가 넘긴 sso_url을 착지로 인정하면 coupang_auth가 즉시 죽는다(모듈 계약)."""
    w._recover_vs_session(object(), {"wing_login_id": "x"})
    assert spy_ensure["is_landed"](spy_ensure["sso_url"]) is False


def test_recover_vs_predicate_requires_the_mobile_origin(spy_ensure):
    """★호스트는 파싱해서 비교해야 한다 — m-wing이 wing을 **포함**한다(2026-08-03 실사고).

    VS는 m-wing(모바일), RG는 wing(데스크톱)이다. 부분문자열로 비교하면 RG 착지를 VS 복구의
    성공으로 오인해 «복구했다는데 계속 실패»가 된다.
    """
    w._recover_vs_session(object(), {"wing_login_id": "x"})
    landed = spy_ensure["is_landed"]
    assert landed("https://m-wing.coupang.com/tenants/business-insight/sales-analysis") is True
    assert landed("https://wing.coupang.com/tenants/settlement/list") is False


def test_recover_vs_passes_app_probe_as_verify(spy_ensure):
    """verify(앱 프로브)가 넘어가는가 — RG에서 적대 리뷰 P2-1이 유일 생존 변이로 잡아낸 자리."""
    w._recover_vs_session(object(), {"wing_login_id": "x"})
    assert callable(spy_ensure.get("verify")), "verify(앱 프로브)가 coupang_auth로 안 넘어간다"


def test_vs_lane_calls_recovery_before_giving_up():
    """★배선 절단 감지 — VS 경로가 «사람을 부르기 전에» 자동 복구를 시도하는가.

    이 단언이 빨간 것은 2026-08-23 17:09:12 상태(같은 회차에 RG는 스스로 복구하는데
    VS만 사람을 부름)로의 회귀를 뜻한다. 함수가 있어도 아무도 안 부르면 없는 것과 같다.
    """
    # 길목은 두 칸이다: _do_run → _vs_recover_and_refetch → _recover_vs_session.
    # 어느 칸이 끊겨도 라이브는 「사람을 부르고 끝」으로 돌아가므로 둘 다 단언한다.
    assert "_vs_recover_and_refetch" in w._do_run.__code__.co_names, (
        "_do_run이 자동 복구 헬퍼를 부르지 않는다 — VS 자동 복구 배선이 끊겼다."
    )
    assert "_recover_vs_session" in w._vs_recover_and_refetch.__code__.co_names, (
        "헬퍼가 _recover_vs_session을 부르지 않는다 — coupang_auth에 닿지 않는다."
    )


def test_both_vs_entrypoints_route_through_the_choke_point():
    """★위 단언의 전제 — VS 진입점이 전부 `_do_run`을 지나는가.

    데몬 경로를 다른 함수로 갈라내면 위 단언은 초록인 채 라이브만 옛 동작으로 돌아간다.
    """
    import types

    def _refs(code, name, seen=None):
        seen = seen if seen is not None else set()
        if id(code) in seen:
            return False
        seen.add(id(code))
        if name in code.co_names:
            return True
        return any(_refs(c, name, seen) for c in code.co_consts
                   if isinstance(c, types.CodeType))

    daemon = getattr(w, "cmd_poll", None)
    assert daemon is not None, "폴 데몬 진입점을 못 찾았다"
    assert _refs(daemon.__code__, "_do_run"), "데몬 VS 경로가 길목을 우회한다"


def test_vs_recover_reconfirms_with_a_real_fetch(monkeypatch):
    """★복구 «선언»을 그대로 믿지 않고 실제 fetch로 재확증하는가.

    인라인이던 시절 「재확증을 지우고 선언만 믿기」 변이가 전건 초록으로 살아남았다.
    선언만 믿으면 「복구했다는데 다음 층이 이유 없이 실패」가 되고, 사람은 로그인할 기회조차
    못 얻은 채 회차만 태운다(2026-08-03 침묵 사고와 같은 형태).
    """
    calls = []
    monkeypatch.setattr(w, "_recover_vs_session", lambda *a, **k: coupang_auth.OK)
    monkeypatch.setattr(w, "_fetch_vendor_summary",
                        lambda *a, **k: calls.append(k) or {"status": 200, "body": "{}"})

    out = w._vs_recover_and_refetch(object(), {"wing_login_id": "x"}, None)
    assert calls, "복구 선언 뒤 재fetch를 안 했다 — 앱이 실제로 응답하는지가 유일한 증거다"
    assert out == {"status": 200, "body": "{}"}


def test_vs_recover_returns_none_when_recovery_failed(monkeypatch):
    """복구가 안 됐으면 재fetch로 회차를 태우지 않고 사람 경로로 넘긴다."""
    calls = []
    monkeypatch.setattr(w, "_recover_vs_session", lambda *a, **k: coupang_auth.LOGIN_REQUIRED)
    monkeypatch.setattr(w, "_fetch_vendor_summary", lambda *a, **k: calls.append(k))

    assert w._vs_recover_and_refetch(object(), {"wing_login_id": "x"}, None) is None
    assert not calls, "복구 실패인데 재fetch를 했다 — 헛된 왕복이고 사람 호출만 늦어진다"


def test_vs_lane_routes_through_the_recover_and_refetch_helper():
    """★배선 절단 감지 — `_do_run`이 그 헬퍼를 실제로 부르는가(위 두 단언의 전제)."""
    assert "_vs_recover_and_refetch" in w._do_run.__code__.co_names, (
        "_do_run이 _vs_recover_and_refetch를 부르지 않는다 — VS 자동 복구 배선이 끊겼다."
    )


# ════════════════════════════════════════════════════════════════════
# 5. verify 프로브가 «실제로 돌아가는가» — 라이브에서만 드러난 결함 (2026-08-23 17:25:38)
# ════════════════════════════════════════════════════════════════════
#
# ★사고: VS 자동 복구의 verify가 `_fetch_vendor_summary(..., retries=0)`을 불렀는데
#   `for attempt in range(1, 0+1)`이 한 번도 안 돌아 `raise last_exc`에 None이 실렸다 →
#   `raise None` → **TypeError**. coupang_auth가 그걸 「세션 검사 오류」로 삼켜 복구를
#   **실패로 오판**했다. 그날 수집이 살아난 건 전혀 다른 경로(로그인 회복 워치의 자동 재개)
#   덕이었고, 내 verify는 한 번도 참을 말한 적이 없다.
# ★기존 테스트 전건이 초록이었다 — spy가 `verify`가 **callable인지**만 봤기 때문이다.
#   「호출 가능하다」와 「호출하면 답을 준다」는 다르다.

class _StubPage:
    """브라우저 없이 fetch 경로를 태우는 최소 스텁."""

    def __init__(self, url="https://m-wing.coupang.com/tenants/business-insight/sales-analysis",
                 body='{"saleSummaryByDate": []}'):
        self.url = url
        self._body = body

    def goto(self, *a, **k):
        return None

    def wait_for_timeout(self, *a, **k):
        return None

    def evaluate(self, *a, **k):
        return {"status": 200, "body": self._body}


def test_fetch_vendor_summary_rejects_zero_retries():
    """★`retries=0`은 조용한 함정(raise None)이 아니라 **시끄러운 거절**이어야 한다."""
    with pytest.raises(ValueError, match="1 이상"):
        w._fetch_vendor_summary(_StubPage(), {}, retries=0)


def test_vs_verify_probe_actually_returns_a_verdict(spy_ensure):
    """★verify를 «호출해 본다» — callable인지만 보면 17:25:38 사고를 못 잡는다.

    복구 직후 앱이 정상 응답하면 True, 로그아웃 상태면 False. 어느 쪽이든 **예외를 던지면
    안 된다** — 던지면 coupang_auth가 그걸 삼켜 «복구 실패»로 오판한다.
    """
    w._recover_vs_session(_StubPage(), {"wing_login_id": "x"})
    verify = spy_ensure["verify"]
    assert verify() is True, "정상 응답인데 verify가 참을 말하지 않는다"


def test_vs_verify_probe_says_false_when_logged_out(spy_ensure, monkeypatch):
    """로그아웃 URL이면 False다 — 예외가 아니라 «아직 복구 안 됨»으로 답한다."""
    monkeypatch.setattr(w, "_is_logged_out", lambda url: True)
    w._recover_vs_session(_StubPage(), {"wing_login_id": "x"})
    assert spy_ensure["verify"]() is False


# ════════════════════════════════════════════════════════════════════
# 6. 적대 리뷰 P2 채택분 (2026-08-23 PR #360 1R) — 생존 변이 3종이 가리킨 구멍
# ════════════════════════════════════════════════════════════════════

def test_vs_recovery_result_reaches_the_run(caplog):
    """★P2-2 — 복구 성공이 «회차의 결과»로 실려야 한다(그래야 push가 가고 폰 패널이 ✅다).

    생존 변이(M5)의 라이브 모습이 최악이다: 로그는 「자동 재로그인으로 세션 복구」라 말하는데
    결과가 안 실려 push가 안 가고 **폰 패널엔 ❌ 「로그인 필요」**가 뜬다. 로그와 화면이
    서로 다른 말을 하는 상태 — 이 계약이 없애려는 «틀린 처방»의 가장 나쁜 형태다.
    """
    ok = {"status": 200, "body": '{"saleSummaryByDate": []}'}
    with caplog.at_level(logging.INFO):
        res, login_needed, keep_open = w._vs_apply_recovery(ok)
    assert res is ok, "복구 결과가 회차 결과로 안 실린다 — push 경로가 통째로 끊긴다"
    assert login_needed is False and keep_open is False, "복구했는데 사람을 부른다"
    assert any("사람 개입 없이" in r.getMessage() for r in caplog.records)


def test_vs_recovery_failure_falls_back_to_a_human(caplog):
    """복구를 선언했는데 재fetch가 실패하면 **정직하게** 사람 경로로 넘긴다(창도 남긴다)."""
    bad = {"status": 401, "body": "signin"}
    with caplog.at_level(logging.WARNING):
        res, login_needed, keep_open = w._vs_apply_recovery(bad)
    assert res is bad and login_needed is True and keep_open is True
    assert any("재fetch가 실패" in r.getMessage() for r in caplog.records), \
        "사유가 로그에 안 남으면 합격 ③의 「정직하게 남는다」가 깨진다"


def test_recover_vs_warns_when_login_id_missing(spy_ensure, caplog):
    """★P2-4 — VS도 login_id 부재를 «경고»한다(RG엔 있는데 VS엔 없던 가드).

    모듈이 조용히 폴백하므로 로그마저 침묵하면 배선해 두고도 「고쳤는데 안 되는」 상태를
    아무도 모른다. Wing은 ①SSO가 원리적으로 무효라 login_id 없음 = 자동 복구 전면 무력화다.
    """
    with caplog.at_level(logging.WARNING):
        w._recover_vs_session(object(), {"account_key": "COUPANG_WING1"})
    assert any("wing_login_id" in r.getMessage() for r in caplog.records), \
        "VS의 login_id 없음이 로그에 안 남는다"


def test_rg_verify_probe_actually_returns_a_verdict(spy_ensure, monkeypatch):
    """★P2-3 — RG의 verify도 «불러 본다». 지금까지 `callable()`만 봐서 같은 구멍이 남아 있었다.

    17:25:38 사고(VS verify가 호출 시 TypeError)와 **정확히 같은 종류**의 결함을 RG에서는
    아무도 못 잡는 상태였다 — 리뷰어가 `_rg_session_ok(page, 0)`으로 바꿔도 전건 초록이었다.
    「호출 가능하다」와 「호출하면 답을 준다」는 다르다.
    """
    monkeypatch.setattr(w, "_rg_session_probe", lambda page: (w._PROBE_OK, "ok"))
    w._recover_rg_session(object(), {"wing_login_id": "x"})
    assert spy_ensure["verify"]() is True, "RG verify가 호출에 답을 못 한다"

    monkeypatch.setattr(w, "_rg_session_probe", lambda page: (w._PROBE_AUTH, "로그아웃"))
    assert spy_ensure["verify"]() is False, "로그아웃인데 참을 말한다"
