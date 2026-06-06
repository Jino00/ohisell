#!/usr/bin/env python3
# ad_cost_browser_fetcher.py — 쿠팡 광고비를 "실제 브라우저"(Playwright)로 fetch → prod push.
#
# 왜 브라우저인가 (실측):
#   - advertising.coupang.com은 Akamai가 데이터센터 IP를 차단(prod=403). residential IP만 통과.
#   - curl/requests 쿠키 재생은 "1회용"(첫 성공 직후 세션 토큰 회전·무효화 → 로그인 튕김).
#   - 실제 브라우저는 토큰 회전을 자동 유지하고 Akamai를 통과.
#
# 세션 유지: Playwright storage_state(살아있는 쿠키 전체=세션쿠키 포함 직렬화)를 파일로 저장.
#   login이 저장 → run이 로드 후 fetch → 회전된 쿠키를 다시 저장. (영속 프로필은 세션쿠키를
#   컨텍스트 종료 시 버리므로 storage_state를 쓴다.)
#
# 사용:
#   1회 로그인:  python3 ad_cost_browser_fetcher.py login   # 창에서 로그인(자동 감지)
#   실행(스케줄): python3 ad_cost_browser_fetcher.py          # headless fetch→push (launchd 매시)
from __future__ import annotations

import fcntl
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

CONFIG_PATH = Path(os.path.expanduser("~/.ohisell_ad_fetcher.json"))
LOG_PATH = Path(os.path.expanduser("~/.ohisell_ad_fetcher.log"))
LOCK_PATH = Path(os.path.expanduser("~/.ohisell_ad_fetcher.lock"))
DEFAULT_STATE = os.path.expanduser("~/.ohisell_ad_state.json")
DASH_URL = "https://advertising.coupang.com/marketing/dashboard/sales?_cap_client=WING"
# aid 쿠키 만료(발급+1h 절대) 시 keycloak 세션(12h)으로 재발급하는 SSO 시작 URL(WING).
# 진입 → xauth.coupang.com keycloak authorize → 세션 살아있으면 비번 없이 callback → aid 재발급.
SSO_LOGIN_URL = (
    "https://advertising.coupang.com/user/login?_cap_client=WING"
    "&returnUrl=%2Fmarketing%2Fdashboard%2Fsales%3F_cap_client%3DWING"
)
KST = ZoneInfo("Asia/Seoul")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("ad_browser")

# 브라우저측 fetch — AbortController 25s 타임아웃(무한 hang 방지).
_FETCH_JS = """async (payload) => {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 25000);
  try {
    const r = await fetch('https://advertising.coupang.com/marketing/cmg-api/report/cost', {
      method: 'POST',
      headers: {'content-type': 'application/json', 'accept': 'application/json, text/plain, */*'},
      body: JSON.stringify(payload),
      credentials: 'include',
      signal: ctrl.signal,
    });
    return { status: r.status, body: await r.text() };
  } finally { clearTimeout(t); }
}"""


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        log.error("설정 파일 없음: %s (README 참조)", CONFIG_PATH)
        sys.exit(2)
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for k in ("prod_base_url", "ingest_token", "vendor_ids"):
        if not cfg.get(k):
            log.error("설정 누락: %s", k)
            sys.exit(2)
    cfg.setdefault("state_file", DEFAULT_STATE)
    try:
        cfg["vendor_ids"] = [int(v) for v in cfg["vendor_ids"]]
    except (ValueError, TypeError):
        log.error("vendor_ids는 정수 목록이어야 함: %r", cfg.get("vendor_ids"))
        sys.exit(2)
    return cfg


def _payload(cfg: dict) -> dict:
    return {"parentNodes": [{"adNodeId": v, "campaignType": "PA"} for v in cfg["vendor_ids"]]}


def _is_logged_out(url: str) -> bool:
    u = (url or "").lower()
    return any(x in u for x in ("login", "/auth", "sso", "signin"))


def _is_auth_expired(res) -> bool:
    """fetch 결과가 aid 만료(세션 인증 실패) 신호인지. SSO 재발급을 트리거할지 판단.

    None(로그인 페이지 리다이렉트) / 401·403 / 200이지만 본문이 로그인 HTML —
    모두 aid 만료다. keycloak 세션이 아직 살아 있으면 재발급으로 회복 가능하다.
    """
    if res is None:
        return True
    status = res.get("status")
    if status in (401, 403):
        return True
    if status == 200:
        body = (res.get("body") or "").lower()
        return any(x in body for x in ("login", "signin", "kccontext"))
    return False


def _save_state(context, path: str) -> None:
    """storage_state(세션쿠키 포함)를 0600으로 저장."""
    context.storage_state(path=path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _push(cfg: dict, data: dict) -> int:
    """응답 data → 설정 vendor만 추출해 prod ingest push. 0=성공."""
    wanted = {str(v) for v in cfg["vendor_ids"]}
    vendors = [
        {"vendor_id": str(vid), "day_cost": int(c.get("day") or 0), "month_cost": int(c.get("month") or 0)}
        for vid, c in data.items()
        if str(vid) in wanted and isinstance(c, dict)
    ]
    if not vendors:
        log.warning("응답에 설정된 vendor 데이터 없음: %s", str(data)[:160])
        return 1
    today = datetime.now(KST).date().isoformat()
    try:
        pr = requests.post(
            cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/ad-cost/ingest",
            json={"date": today, "vendors": vendors},
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            timeout=20,
        )
    except requests.RequestException as e:
        log.error("prod push 네트워크 오류: %s", e)
        return 1
    if pr.status_code != 200:
        log.error("prod push 실패 HTTP %s — %s", pr.status_code, pr.text[:160])
        return 1
    try:
        info = pr.json()
    except ValueError:
        info = pr.text[:160]
    log.info("성공: %s push %s → %s", today,
             [(v["vendor_id"], v["day_cost"]) for v in vendors], info)
    return 0


def _fetch(page, cfg: dict, retries: int = 2):
    """대시보드 이동 후 same-origin fetch. 반환: res dict 또는 None(로그아웃).

    쿠팡 봇 감지(Spoofing chunk)가 가끔 'Failed to fetch'로 순간 차단하므로
    evaluate를 짧은 간격으로 retries회 재시도한다. 마지막 시도도 실패하면 예외 전파.
    """
    page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
    page.wait_for_timeout(2500)  # Akamai JS 센서·세션 안정화
    if _is_logged_out(page.url):
        return None
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return page.evaluate(_FETCH_JS, _payload(cfg))
        except Exception as e:
            last_exc = e
            if attempt < retries:
                log.warning("fetch 일시 실패(%d/%d) 재시도: %s", attempt, retries, str(e)[:80])
                page.wait_for_timeout(2500)
    raise last_exc


def _sso_refresh(page, timeout_s: int = 45) -> bool:
    """aid 만료 시 keycloak 세션으로 aid를 재발급한다(비번 입력 없음).

    WING 로그인 시작 URL → keycloak authorize(Akamai JS 챌린지 ~16초) → callback → 대시보드.
    headful에서만 통과(headless는 xauth Akamai가 Access Denied로 차단). 성공 시 True.
    keycloak 세션(12h)도 만료됐으면 로그인 폼에 머물러 False.
    """
    try:
        page.goto(SSO_LOGIN_URL, wait_until="domcontentloaded", timeout=40000)
    except Exception as e:
        log.error("SSO 재발급 goto 오류: %s", str(e)[:120])
        return False
    waited = 0
    while waited < timeout_s:
        page.wait_for_timeout(2000)
        waited += 2
        try:
            u = page.url
        except Exception:
            continue  # 네비게이션 중
        if "/marketing/dashboard" in u and not _is_logged_out(u):
            page.wait_for_timeout(1500)  # aid 쿠키 안정화
            return True
    return False


def _login_wait_loop(page, ctx, cfg: dict, state: str, wait_secs: int):
    """열린 page에서 사용자 로그인을 자동 감지(report/cost 201). 성공 시 state 저장 후
    파싱된 data dict 반환, 시간초과/창닫힘/파싱실패 시 None."""
    waited = 0
    while waited < wait_secs:
        try:
            page.wait_for_timeout(5000)
            waited += 5
            if _is_logged_out(page.url):
                continue
            res = page.evaluate(_FETCH_JS, _payload(cfg))
        except Exception as e:
            if "closed" in str(e).lower():
                log.error("브라우저 창이 닫혔습니다 — 로그인 미완료(창을 닫지 말고 로그인만 하세요).")
                return None
            continue
        if res.get("status") == 201:
            _save_state(ctx, state)
            try:
                return json.loads(res.get("body") or "")
            except (ValueError, TypeError):
                return None
    return None


def cmd_login(cfg: dict, wait_secs: int = 600) -> int:
    """헤드풀 창을 열고 로그인을 자동 감지(report/cost 201) → storage_state 저장 + 첫 push.

    Enter 불필요(백그라운드 가능). 사용자는 뜬 창에서 로그인만 하면 됨.
    """
    state = os.path.expanduser(cfg["state_file"])
    log.info("로그인 브라우저를 엽니다 — advertising.coupang.com에 로그인하세요(자동 감지, 최대 %d초).", wait_secs)
    ok_data = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        try:
            page = ctx.new_page()
            page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
            ok_data = _login_wait_loop(page, ctx, cfg, state, wait_secs)
        finally:
            ctx.close()
            browser.close()
    if ok_data is None:
        log.error("제한 시간 내 로그인 감지 실패 — 다시 시도하세요.")
        return 1
    log.info("로그인 감지·세션 저장 완료: %s", state)
    return _push(cfg, ok_data)  # 첫 데이터 즉시 push


def _run_with_lock(cfg: dict, login_wait_secs: int = 0) -> int:
    """세션 파일 확인 + flock(동시 실행 방지) 후 _do_run. login_wait_secs>0이면 keycloak
    만료 시 같은 창에서 로그인 대기(버튼 트리거 경로용)."""
    state = os.path.expanduser(cfg["state_file"])
    if not Path(state).is_file():
        log.error("세션 파일 없음 — 먼저 'login' 실행: %s", state)
        return 2

    # 동시 실행 방지(flock, 프로세스 종료 시 자동 해제)
    lock_fd = os.open(str(LOCK_PATH), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.warning("다른 실행이 진행 중 — 이번 호출 건너뜀")
        os.close(lock_fd)
        return 0
    try:
        return _do_run(cfg, state, login_wait_secs=login_wait_secs)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def cmd_run(cfg: dict) -> int:
    """스케줄/수동 1회 실행 — keycloak 만료 시 로그인 대기 없이 fail-fast."""
    return _run_with_lock(cfg, login_wait_secs=0)


def _do_run(cfg: dict, state: str, login_wait_secs: int = 0) -> int:
    # aid는 발급+1h 절대 만료라 매 run이 SSO 재발급을 거친다. SSO 재발급은 headful 필수
    # (headless는 xauth Akamai 차단). config "headless"는 무시(호환 위해 키만 유지).
    # login_wait_secs>0(버튼 트리거): keycloak도 만료면 같은 창에서 로그인 대기 후 fetch.
    res = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            ctx = browser.new_context(storage_state=state)
            try:
                page = ctx.new_page()
                res = _fetch(page, cfg)
                if _is_auth_expired(res):
                    # aid 만료(None/401/403/로그인HTML) → keycloak 세션으로 SSO 재발급
                    log.info("aid 만료 — keycloak 세션으로 SSO 재발급 시도")
                    if _sso_refresh(page):
                        _save_state(ctx, state)  # keycloak 12h 갱신분 즉시 보존
                        res = _fetch(page, cfg)
                        if res is not None and res.get("status") == 201:
                            _save_state(ctx, state)  # 재fetch 후 회전 쿠키 최종 보존
                        log.info("SSO 재발급 완료 — fetch 재시도")
                    elif login_wait_secs > 0:
                        # keycloak도 만료 → 같은 창에서 수동 로그인 대기(버튼 트리거, 아침 첫 클릭)
                        log.info("keycloak 만료 — 창에서 로그인하세요(자동 감지, 최대 %d초).", login_wait_secs)
                        try:
                            page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
                        except Exception:
                            pass
                        ok_data = _login_wait_loop(page, ctx, cfg, state, login_wait_secs)
                        if ok_data is None:
                            log.error("로그인 시간 초과/취소 — 갱신 취소.")
                            return 1
                        log.info("로그인 완료 — fetch")
                        res = _fetch(page, cfg)
                        if res is not None and res.get("status") == 201:
                            _save_state(ctx, state)
                    else:
                        log.error("세션 만료 — keycloak 세션도 만료. 'login' 재실행 필요.")
                        return 1
                elif res.get("status") == 201:
                    _save_state(ctx, state)  # 회전된 세션쿠키 갱신
            finally:
                ctx.close()
                browser.close()
    except Exception as e:
        log.error("브라우저 fetch 오류: %s", e)
        return 1

    if res is None:
        log.error("세션 만료 — 로그인 페이지로 리다이렉트. 'login' 재실행 필요.")
        return 1
    status = res.get("status")
    if status != 201:
        body = res.get("body") or ""
        if status == 200 and any(x in body.lower() for x in ("login", "signin", "kccontext")):
            log.error("세션 만료(로그인 HTML 반환) — 'login' 재실행 필요. status=200")
        else:
            log.error("fetch 비정상 status=%s — %s", status, body[:160].replace("\n", " "))
        return 1
    try:
        data = json.loads(res.get("body") or "")
    except (ValueError, TypeError) as e:
        log.error("응답 JSON 파싱 실패: %s", e)
        return 1
    return _push(cfg, data)


_POLL_INTERVAL_S = 15      # 데몬이 갱신 요청을 확인하는 간격(창 안 뜸, 가벼운 GET)
_LOGIN_WAIT_S = 180        # 버튼 클릭 시 keycloak 만료면 로그인 대기 한도


def _prod_refresh_status(cfg: dict) -> dict:
    r = requests.get(
        cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/ad-cost/refresh-status",
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _prod_claim(cfg: dict) -> dict:
    r = requests.post(
        cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/ad-cost/refresh-claim",
        headers={"X-Ingest-Token": cfg["ingest_token"]},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def cmd_poll(cfg: dict) -> int:
    """상주 데몬 — 갱신 요청을 주기적으로 확인하고, 요청이 있을 때만 headful fetch.

    평소엔 가벼운 GET만(창 안 뜸). 대시보드 버튼이 요청을 set하면 claim 후 fetch 1회.
    keycloak 만료 시 같은 창에서 로그인 대기(아침 첫 클릭이 로그인을 겸함).
    """
    state = os.path.expanduser(cfg["state_file"])
    interval = int(cfg.get("poll_interval_s", _POLL_INTERVAL_S))
    log.info("폴 데몬 시작 — %ds 간격으로 갱신 요청 확인(창은 요청 시에만 뜸).", interval)
    while True:
        try:
            st = _prod_refresh_status(cfg)
            if st.get("requested"):
                claim = _prod_claim(cfg)
                if claim.get("claimed"):
                    log.info("갱신 요청 감지 — fetch 시작")
                    if not Path(state).is_file():
                        cmd_login(cfg)          # 세션 없음 → 로그인부터(첫 데이터 push 포함)
                    else:
                        _run_with_lock(cfg, login_wait_secs=_LOGIN_WAIT_S)
        except requests.RequestException as e:
            log.warning("폴 확인 실패(네트워크): %s", str(e)[:80])
        except Exception as e:  # noqa: BLE001 — 데몬은 어떤 오류에도 죽지 않는다
            log.error("폴 루프 오류: %s", str(e)[:160])
        time.sleep(interval)


def main() -> None:
    cfg = load_config()
    arg = sys.argv[1] if len(sys.argv) >= 2 else ""
    if arg == "login":
        sys.exit(cmd_login(cfg))
    if arg == "poll":
        try:
            sys.exit(cmd_poll(cfg))
        except KeyboardInterrupt:
            log.info("폴 데몬 종료")
            sys.exit(0)
    sys.exit(cmd_run(cfg))


if __name__ == "__main__":
    main()
