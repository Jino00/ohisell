# coupang_auth.py — 쿠팡 페처 4종의 **세션 자가 복구** 공용 구현.
#
# 왜 만드는가(2026-08-03 실측):
#   페처 4종 중 오픽스 광고(ad_cost_browser_fetcher)만 자동 재로그인을 갖고 있었고, 나머지
#   3종(오하이테크 광고·로켓 공급자허브·Wing)은 세션이 풀리면 그냥 rc=3으로 멈춰 사람을 불렀다.
#   그날 하루에만 Jino가 3번 로그인했다(09:31·10:07·11:11 — 오하이테크 세션 수명 ≈2시간).
#   그 결과 같은 날 배포한 워치독의 "위급 시 자동 갱신"도 반쪽이 된다 — 자동으로 당겨도
#   세션이 죽어 있으면 결국 사람을 부르기 때문이다.
#
# 3층 복구(오픽스 구현을 그대로 일반화한 것 — 새로 발명한 게 아니다):
#   ① aid 만료(발급+1h 절대) → SSO 재진입으로 재발급. **비밀번호 안 씀.**
#      keycloak 세션(12h)이 살아 있으면 통과 → 수동 로그인 주기가 1h → 12h로 늘어난다.
#   ② keycloak 세션도 만료 → Keychain 자격증명으로 폼 자동입력.
#      ★계정별 Keychain 등록은 **Jino가 1회** 해야 한다(아래 KEYCHAIN_SETUP 참조).
#        등록이 없으면 조용히 ①까지만 하고 폴백한다 — 없다고 깨지지 않는다.
#   ③ 2FA·실패 → macOS 알림으로 사람 호출. 어차피 로그인은 이 Mac에서 해야 한다.
#
# ★headful 전용: xauth의 Akamai가 headless를 Access Denied로 막는다(오픽스 구현 주석 실측).
# ★프로필별 쿠키: 페처마다 전용 Chrome 프로필이라 keycloak 세션은 **공유되지 않는다**.
#   한 계정에 로그인해도 다른 프로필은 여전히 자기 세션이 필요하다.
# ★비밀번호는 메모리에서만 쓰고 로그·파일·git 어디에도 남기지 않는다.
#
# KEYCHAIN_SETUP (Jino가 계정마다 1회, 터미널에서):
#   security add-generic-password -U -s ohisell-coupang-ad -a <로그인ID> -w
#   (-w 뒤에 비번을 안 적으면 프롬프트로 안전하게 입력받는다)
from __future__ import annotations

import logging
import subprocess
from typing import Callable

log = logging.getLogger(__name__)

# 오픽스 광고 페처가 이미 쓰던 서비스명. 계정(-a)만 다르게 등록하면 4종이 같은 서비스를 공유한다.
KEYCHAIN_SERVICE = "ohisell-coupang-ad"

# ensure_session 반환값 — 호출부가 rc를 정하는 근거.
OK = "ok"                      # 세션 살아있음(또는 복구 성공)
LOGIN_REQUIRED = "login_required"   # 사람이 로그인해야 함(창을 남길 것)
TWO_FACTOR = "2fa"             # 자격증명은 맞는데 OTP 단계 — 자동화 불가, 사람 호출


def keychain_get(account: str, service: str = KEYCHAIN_SERVICE) -> str | None:
    """macOS Keychain에서 비밀번호를 읽는다(평문 저장·로그·git 없음).

    미등록/잠김/실패는 전부 None → 호출부는 수동 로그인으로 폴백한다.
    ★반환값을 절대 로그에 찍지 마라.
    """
    if not account:
        return None
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return r.stdout.strip() or None
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("Keychain 조회 실패(무시): %s", str(e)[:80])
        return None


def notify_mac(title: str, message: str, sound: str = "Glass") -> None:
    """macOS 네이티브 알림 — 자동 복구가 실패해 사람 개입이 필요할 때.

    로그인은 어차피 이 Mac에서 해야 하므로 알림도 같은 화면에 띄우는 게 가장 빠르다.
    best-effort: 실패해도 데몬 흐름에 영향 주지 않는다.
    """
    try:
        safe_t = title.replace('"', "'")
        safe_m = message.replace('"', "'")
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_m}" with title "{safe_t}" sound name "{sound}"'],
            timeout=10, check=False,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("macOS 알림 실패(무시): %s", str(e)[:80])


def otp_input_visible(page) -> bool:
    """제출 후 '보이는' OTP/인증번호 입력칸이 있는지 — 진짜 2FA 단계 판별.

    ★대시보드 본문에도 '인증번호' 글자가 있어 전체 HTML 키워드 스캔은 오탐한다(오픽스 검증서).
      실제 2FA는 인증 페이지에 **보이는** 입력 필드가 생긴다 → 그것만 신뢰한다.
    """
    try:
        return bool(page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('input'));
            return els.some(e => {
                const hint = ((e.placeholder||'') + ' ' + (e.name||'') + ' ' + (e.id||'')).toLowerCase();
                const vis = e.offsetParent !== null;  // 화면에 보임
                return vis && (hint.includes('인증') || hint.includes('otp')
                       || hint.includes('verification') || hint.includes('code'));
            });
        }"""))
    except Exception:
        return False


# 첫 판정 전 안정화 시간(ms). ★리다이렉트 체인이 시작되기도 전에 판정하면 **출발 URL을
# 착지로 오인**한다(적대적 리뷰 P1). 페처들이 goto 후 3000ms를 넣는 것과 같은 이유다 —
# goto 반환 시점의 page.url은 확정이 아니다.
_SETTLE_MS = 3000


def _poll_landed(page, is_landed: Callable[[str], bool], timeout_s: int) -> bool:
    """리다이렉트 체인이 끝나 목적지에 착지했는지 URL 폴링으로 대기.

    ★_SETTLE_MS 뒤에 첫 판정을 한다. 그전에 판정하면 아직 이동하지 않은 출발 URL이
      is_landed를 만족해 "복구 성공"으로 오판하고, 그 결과 ②Keychain·③알림이 통째로
      건너뛰어진다 — 사람은 아무 신호도 못 받고 수집만 조용히 실패한다.
    ★wait_for_timeout은 드라이버 왕복이라 페이지/브라우저가 닫히면 raise한다(적대적 리뷰 P2).
      사람이 "이 창에서 로그인하세요" 안내를 보고 창을 닫는 흔한 경로에서 예외가 새어 나가면
      호출부가 rc=3(로그인 필요)이어야 할 상황을 rc=1(재시도)로 오분류한다 → 여기서 잡는다.
    """
    try:
        page.wait_for_timeout(_SETTLE_MS)
    except Exception as e:  # noqa: BLE001 — 창이 닫혔다 = 복구 불가(사람이 개입해야 함)
        log.warning("복구 대기 중 페이지 소실(창 닫힘 추정): %s", type(e).__name__)
        return False
    waited = _SETTLE_MS / 1000.0
    while waited < timeout_s:
        try:
            page.wait_for_timeout(2000)
        except Exception as e:  # noqa: BLE001
            log.warning("복구 대기 중 페이지 소실(창 닫힘 추정): %s", type(e).__name__)
            return False
        waited += 2
        try:
            u = page.url
        except Exception:
            continue  # 네비게이션 중
        if is_landed(u):
            try:
                page.wait_for_timeout(1500)  # 쿠키 안정화
            except Exception:  # noqa: BLE001 — 착지는 이미 확인됨
                pass
            return True
    return False


class LandingPredicateError(RuntimeError):
    """착지 판정자가 **출발 URL을 착지로 인정**한다 = 복구를 안 하고도 성공이 된다.

    이걸 런타임에 터뜨리는 이유(적대적 리뷰 P1): 이 실수는 조용히 지나가면
    ②Keychain·③알림을 통째로 건너뛰게 만들고, 사람은 아무 신호도 못 받는다.
    설정 실수는 **시끄럽게 죽는 게** 조용히 무력화되는 것보다 낫다.
    """


def _assert_predicate_sound(sso_url: str, is_landed: Callable[[str], bool]) -> None:
    """출발 URL이 착지 판정을 만족하면 그 판정자는 쓸 수 없다."""
    if is_landed(sso_url):
        raise LandingPredicateError(
            f"is_landed가 출발 URL을 착지로 인정한다(sso_url={sso_url!r}). "
            "복구 여부와 무관하게 성공이 되므로 판정자를 좁혀라 "
            "(예: 인증 후에만 나오는 경로를 요구)."
        )


def _goto_reset(page, url: str) -> bool:
    """about:blank로 리셋한 뒤 url로 이동한다. **이동이 성공했는지**를 돌려준다.

    ★리셋하는 이유: 로그인 페이지에서 곧장 goto하면 클라이언트 리다이렉트가 진행 중이라
      net::ERR_ABORTED가 난다(오픽스 프로브에서 검증된 사항).
    ★반환값이 필요한 이유(적대적 리뷰 P1): 실패를 삼키면 page.url이 **직전 URL 그대로** 남고,
      호출부가 그걸 착지로 오인해 "아무 데도 안 갔는데 복구 성공"이 된다. 실패는 폴링으로
      만회할 수 있으니 예외는 계속 삼키되, 삼켰다는 사실은 호출부에 알린다.
    """
    try:
        page.goto("about:blank", timeout=10000)
    except Exception:  # noqa: BLE001
        pass
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=40000)
        return True
    except Exception as e:  # noqa: BLE001
        # ERR_ABORTED는 리다이렉트로 인한 중단일 수 있다 — 바로 실패로 보지 말고 폴링으로 확인.
        log.warning("goto 경고(폴링으로 확인): %s", str(e)[:120])
        return False


def sso_refresh(
    page,
    sso_url: str,
    is_landed: Callable[[str], bool],
    *,
    timeout_s: int = 45,
) -> bool:
    """① aid 만료 시 keycloak 세션으로 재발급한다. **비밀번호 안 씀.**

    로그인 시작 URL → keycloak authorize(Akamai JS 챌린지 ~16초) → callback → 목적지 착지.
    keycloak 세션(12h)도 만료됐으면 로그인 폼에 머물러 False.

    ★about:blank로 먼저 리셋한다: 로그인 페이지에서 곧장 sso_url로 goto하면 클라이언트
      리다이렉트가 진행 중이라 net::ERR_ABORTED가 난다(오픽스 프로브에서 검증된 사항).
    """
    _assert_predicate_sound(sso_url, is_landed)
    _goto_reset(page, sso_url)
    return _poll_landed(page, is_landed, timeout_s)


# 로그인 폼 셀렉터 — ★id에 기대지 마라(2026-08-03 라이브 실측으로 뒤집힌 가정).
#   초판은 오픽스 구현에서 `#username`/`#kc-login`을 그대로 가져왔는데, **supplier-hub 테마의
#   keycloak 폼에는 id가 하나도 없다**: input[name=username]/[name=password], 제출은 id 없는
#   button[type=submit]. 그래서 오하이테크 광고 13:34:00 실측에서 ②층이 폼을 코앞에 두고
#   `wait_for_selector` 15초 타임아웃으로 죽었다. name 속성은 keycloak이 POST에 쓰는 계약이라
#   테마가 바뀌어도 남는다 — id보다 이쪽이 안정적이다.
_USERNAME_SEL = "input[name=username]"
_PASSWORD_SEL = "input[name=password]"

# ★로그인 폼을 **하나로 특정**해 그 안에서만 입력·제출한다(적대적 리뷰 P1).
#   전역 셀렉터로는 안 된다: playwright의 page.fill/click은 strict가 아니라 **첫 매치**를 쓰고,
#   keycloak의 회원가입 폼에도 name=password가 있다. 그러면 `form:has(input[name=password])`가
#   두 폼을 다 물고, 무엇보다 fill은 스코프가 아예 없어 **평문 비번이 가입 폼에 들어가 그대로
#   제출될 수 있다**. 제출만 폼으로 가두는 건 장식이었다.
#   판정 근거는 keycloak의 계약인 form action이다(실측: 로그인 폼 action은
#   `.../login-actions/authenticate?session_code=...`, 가입 폼은 `/registration/...`).
_LOGIN_FORM_SELS = (
    "form[action*='login-actions/authenticate']",
    f"form:has({_PASSWORD_SEL})",
)


def _login_form(page):
    """로그인 폼 로케이터를 **유일하게** 특정한다. 못 하면 예외(→ 수동 폴백).

    ★후보가 2개 이상이면 그 셀렉터는 버리고 다음으로 넘어간다 — 아무거나 고르는 것보다
      시끄럽게 실패하는 게 낫다(엉뚱한 폼에 비번을 넣는 것이 최악이다).
    """
    for sel in _LOGIN_FORM_SELS:
        loc = page.locator(sel)
        n = loc.count()
        if n == 1:
            return loc
        if n > 1:
            log.warning("로그인 폼 후보 %d개(%s) — 모호해서 채택하지 않는다.", n, sel)
    raise RuntimeError("로그인 폼을 유일하게 특정하지 못했다")


# 제출 버튼 후보 — **이미 로그인 폼 안으로 스코프된 상태**에서 쓴다(위 _login_form).
#   ★`#kc-login`을 앞에 두지 않는다(적대적 리뷰 P2): 이 모듈을 쓰는 두 페처는 둘 다 id가 없는
#     supplier-hub 테마라, 앞에 두면 **성공 경로마다 5초를 버린다**. 호환용으로 뒤에만 남긴다.
_SUBMIT_SELS = ("button[type=submit]", "input[type=submit]", "#kc-login")


def _click_login_submit(form) -> None:
    """로그인 폼 **안에서** 제출을 누른다. 못 찾으면 예외(→ 수동 폴백)."""
    last = None
    for sel in _SUBMIT_SELS:
        try:
            form.locator(sel).first.click(timeout=5000)
            return
        except Exception as e:  # noqa: BLE001 — 다음 후보로 넘어간다
            last = e
    raise RuntimeError(
        "로그인 제출 버튼을 찾지 못했다"
        + (f"({type(last).__name__})" if last is not None else "")
    )


def try_auto_login(
    page,
    login_id: str | None,
    is_landed: Callable[[str], bool],
    *,
    service: str = KEYCHAIN_SERVICE,
    timeout_s: int = 25,
    form_url: str | None = None,
) -> str:
    """② keycloak 로그인 폼에 Keychain 자격증명을 자동입력·제출한다.

    반환: OK(착지) / TWO_FACTOR(OTP 단계) / LOGIN_REQUIRED(자격증명 없음·폼 못 찾음·실패).
    ★비밀번호는 메모리에서만 쓰고 로그에 안 남긴다.

    ★form_url: **폼으로 다시 진입하고 나서** 채운다. ①이 폼을 띄워놨더라도 그 사이에
      verify(앱 세션 검사)가 대시보드로 **이동**해버리기 때문이다 — 그러면 ②는 입력칸이 없는
      화면에서 기다리다 죽는다(2026-08-03 13:58:00 라이브: `wait_for_selector` 15초 타임아웃.
      권위값 검사가 자기 뒤의 층을 망가뜨리고 있었다). 재진입은 멱등이라 손해가 없다.
    """
    if not login_id:
        return LOGIN_REQUIRED
    pw = keychain_get(login_id, service)
    if not pw:
        log.info("Keychain 자격증명 없음(%s) — 수동 로그인 폴백.", login_id)
        return LOGIN_REQUIRED
    try:
        if form_url:
            # ★판정자 건전성은 여기서도 요구한다(적대적 리뷰 P2-3): form_url은 공개 인자라
            #   헐거운 판정자로 직접 호출되면 goto 직후 즉시 거짓 OK가 난다.
            _assert_predicate_sound(form_url, is_landed)
            navigated = _goto_reset(page, form_url)
            # ★재진입 도중 세션이 살아나 착지했으면 폼을 기다릴 이유가 없다(비번도 안 쓴다).
            #   단 **goto가 실제로 성공했을 때만** 묻는다(적대적 리뷰 P1): _goto_reset은 실패를
            #   삼키므로, 창이 죽으면 page.url이 **직전 URL 그대로** 남는다. 바로 앞에서 verify가
            #   대시보드로 이동해뒀다면 그 stale URL이 is_landed를 만족해 **아무 데도 안 가고,
            #   비번도 안 넣고, 권위값 검사가 '복구 안 됨'이라 말하는데도 OK**가 된다
            #   (③알림도 건너뛰어 사람은 아무 신호를 못 받는다).
            #   판정은 _poll_landed에 맡긴다 — 안정화(3s)와 창 닫힘 감지가 거기 들어 있다.
            if navigated and _poll_landed(page, is_landed, 6):
                log.info("로그인 폼 재진입 중 착지 — 비번 없이 복구.")
                return OK
        page.wait_for_selector(_USERNAME_SEL, timeout=15000)
        form = _login_form(page)
        form.locator(_USERNAME_SEL).fill(login_id)
        form.locator(_PASSWORD_SEL).fill(pw)
        _click_login_submit(form)
        if _poll_landed(page, is_landed, timeout_s):
            log.info("자동 로그인 성공 — 목적지 착지.")
            return OK
        # 미착지 → 2FA인지 단순 실패인지 구분(알림 문구가 달라진다)
        if otp_input_visible(page):
            log.warning("자동 로그인 후 2FA(인증번호) 단계 — 자동화 불가, 사람 호출.")
            return TWO_FACTOR
        log.warning("자동 로그인 실패(목적지 미착지) — 수동 폴백.")
        return LOGIN_REQUIRED
    except Exception as e:  # noqa: BLE001
        # ★★예외 메시지를 그대로 찍지 마라(적대적 리뷰 P2): playwright의 page.fill은 내부
        #   로그에 `fill("<값>")`을 남기고, 실패 시 그 call log가 예외 메시지에 통째로 붙는다
        #   → str(e)를 로그에 찍으면 **평문 비밀번호가 로그 파일에 남는다**(~/.ohisell_*.log는
        #   0644라 다른 사용자도 읽는다). 길이 절단([:100])으로 우연히 막히던 것을 구조로 바꾼다.
        #   예외 타입 + call log 앞부분만 남긴다.
        safe = str(e).split("Call log:")[0].strip()[:120]
        log.warning("자동 로그인 실패(수동 폴백): %s: %s", type(e).__name__, safe)
        return LOGIN_REQUIRED


def ensure_session(
    page,
    *,
    sso_url: str,
    is_landed: Callable[[str], bool],
    login_id: str | None = None,
    label: str = "쿠팡",
    service: str = KEYCHAIN_SERVICE,
    sso_timeout_s: int = 45,
    login_timeout_s: int = 25,
    verify: Callable[[], bool] | None = None,
) -> str:
    """세션이 풀린 상태에서 ①→②→③ 순으로 복구를 시도한다.

    반환 OK면 호출부는 그대로 수집을 이어가면 된다. LOGIN_REQUIRED/TWO_FACTOR면 창을 남기고
    rc=3(로그인 필요)으로 끝내야 한다 — 재시도해도 같은 자리에서 죽고 창만 반복해서 뜬다.

    ★①을 먼저 하는 이유: 비밀번호를 안 쓰는 경로가 항상 우선이다. aid(1h) 만료가 대부분이고
      keycloak 세션이 살아 있으면 여기서 끝난다 — 수동 로그인 주기가 크게 늘어난다.

    ★verify(선택): **앱 자신의 세션 검사**를 권위값으로 쓴다. URL 판정(is_landed)은 폴링을
      언제 멈출지 정하는 휴리스틱일 뿐, 복구 성공의 최종 판정자가 아니다. 둘을 하나로 쓰면
      내 URL 추측이 틀렸을 때 **진짜 복구를 실패로 오판**해 ②③으로 잘못 escalate하고,
      사람에게 불필요한 로그인 알림이 간다(2026-08-03: supplier의 SSO returnUrl이 오리진
      루트라 /dashboard를 요구하는 판정이 정상 복구를 거부할 여지가 있었다).
      verify가 주어지면 각 층 뒤에 호출해 그 결과를 우선한다.

    ★sso_url은 **그 계정이 실제로 타는 로그인 클라이언트**여야 한다. 틀리면 ①이 관통하지 못할
      뿐 아니라 ②가 도달하는 자리에 폼이 없어 3층이 통째로 무력해진다(2026-08-03 오하이테크
      광고: 로켓배송 계정에 WING 진입을 써서 '역할 선택' 화면에 멈췄다).
    """
    def _confirmed(stage: str) -> bool:
        if verify is None:
            return False
        try:
            ok = bool(verify())
        except Exception as e:  # noqa: BLE001 — 검사 실패는 '복구 안 됨'으로 본다(안전한 방향)
            log.warning("[%s] 세션 검사 오류(%s): %s", label, stage, type(e).__name__)
            return False
        if ok:
            log.info("[%s] 앱 세션 검사 통과(%s) — 복구 확정.", label, stage)
        return ok

    landed = sso_refresh(page, sso_url, is_landed, timeout_s=sso_timeout_s)
    if landed:
        log.info("[%s] SSO 재발급 성공(비번 없이) — 세션 복구.", label)
        return OK
    # ★URL 판정이 실패해도 앱이 살아 있다고 하면 복구된 것이다(판정자 과잉 협소 방어).
    if _confirmed("SSO 후"):
        return OK

    log.info("[%s] SSO로 복구 안 됨 — Keychain 자동 로그인 시도.", label)
    # ★form_url=sso_url: 바로 위 _confirmed가 대시보드로 **이동**했을 수 있다. 그 자리엔 폼이
    #   없으므로 ②는 반드시 로그인 진입으로 되돌아가서 시작해야 한다(위 docstring 참조).
    res = try_auto_login(
        page, login_id, is_landed,
        service=service, timeout_s=login_timeout_s, form_url=sso_url,
    )
    if res == OK or (res != TWO_FACTOR and _confirmed("자동 로그인 후")):
        log.info("[%s] 자동 로그인으로 세션 복구.", label)
        return OK

    if res == TWO_FACTOR:
        notify_mac(f"{label} 로그인 필요(2FA)", "인증번호 단계 — 열린 Chrome 창에서 직접 로그인하세요.")
    else:
        notify_mac(f"{label} 로그인 필요", "자동 복구 실패 — 열린 Chrome 창에서 직접 로그인하세요.")
    return res
