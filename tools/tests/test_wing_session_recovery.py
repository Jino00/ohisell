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
