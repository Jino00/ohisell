#!/usr/bin/env python3
# wing_browser_fetcher.py — 쿠팡 Wing 판매분석(vendor-summary)을 "실제 브라우저"(Playwright)로 fetch.
#   Wing 세션 자동화 트랙 S1. 광고 페처(ad_cost_browser_fetcher.py)의 헤드풀 패턴을 wing.coupang.com용으로 복제(D-1).
#
# 왜 브라우저인가 (트랙 §2, ref 18):
#   - wing.coupang.com은 Cloudflare(cf_clearance, IP+UA 바인딩)+Akamai(_abck/bm_*)+ALB 다중 봇 방어.
#   - curl/requests 쿠키 재생은 "1회용"(cf_clearance 갱신 불가) → 실제 브라우저가 챌린지를 풀어 세션을 살려둬야 함.
#   - vendor-summary는 모바일 UA 필수(ref 18). cf_clearance가 UA에 바인딩되므로 로그인·fetch 모두 같은 모바일 UA로 수행.
#
# 런타임 경계 (D-4): 이 파일은 Mac 로컬 헤드풀 프로세스다. 백엔드는 호출하지 않는다(push만).
#   S1: 로그인 + vendor-summary fetch·파싱·로그 출력(라이브 검증).
#   S2(현재): 파싱 결과를 prod ingest로 push(account_key·prod_base_url·ingest_token 설정 시) +
#     launchd poll 데몬(com.ohisell.wing)으로 상주화. push 미설정이면 S1처럼 로그 출력만(하위호환).
#
# 사용:
#   1회 로그인:  backend/.venv/bin/python3 tools/wing_browser_fetcher.py login   # 창에서 Wing 로그인(자동 감지)
#   실행(1회):   backend/.venv/bin/python3 tools/wing_browser_fetcher.py          # state 로드 → fetch → push
#   상주 데몬:   backend/.venv/bin/python3 tools/wing_browser_fetcher.py poll      # launchd(com.ohisell.wing)
#
# 설정 파일 ~/.ohisell_wing_fetcher.json (push용):
#   {"account_key":"COUPANG_WING1","prod_base_url":"https://sellc.ohitech.co.kr","ingest_token":"<AD_INGEST_TOKEN>","vs_days":7}
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

CONFIG_PATH = Path(os.path.expanduser("~/.ohisell_wing_fetcher.json"))
LOG_PATH = Path(os.path.expanduser("~/.ohisell_wing_fetcher.log"))
LOCK_PATH = Path(os.path.expanduser("~/.ohisell_wing_fetcher.lock"))
DEFAULT_STATE = os.path.expanduser("~/.ohisell_wing_state.json")

# 로그인이 착지하는 Wing 페이지(판매분석). 모바일 UA라 로그인 시 m-wing.coupang.com(모바일 호스트)로
# 라우팅된다(S1 라이브 실측 2026-06-14). 따라서 진입·fetch 모두 m-wing origin 기준.
DASH_URL = "https://m-wing.coupang.com/tenants/business-insight/sales-analysis"
# ★ vendor-summary는 절대 호스트(wing.coupang.com)로 부르면 브라우저에서 cross-origin CORS 차단.
#   현재 페이지 origin(=로그인 후 m-wing) + 경로로 same-origin 호출해야 200(S1 라이브 실측).
VENDOR_SUMMARY_PATH = "/tenants/rfm-ss/api/business-insight/vendor-summary"
# vendor-summary 모바일 UA 필수(ref 18, inbound.py _UA와 동일). cf_clearance가 이 UA에 바인딩됨.
_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
KST = ZoneInfo("Asia/Seoul")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("wing_browser")

# 브라우저측 vendor-summary fetch — same-origin(location.origin+경로), XSRF-TOKEN 쿠키를 x-xsrf-token
# 헤더로 더블서브밋. AbortController 25s 타임아웃(무한 hang 방지). 인자=[payload, path]. 반환: {status, body}.
_VS_FETCH_JS = """async (args) => {
  const [payload, path] = args;
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 25000);
  const m = document.cookie.match(/(?:^|;\\s*)XSRF-TOKEN=([^;]+)/);
  const headers = {'content-type': 'application/json', 'accept': 'application/json, text/plain, */*'};
  if (m) { try { headers['x-xsrf-token'] = decodeURIComponent(m[1]); } catch (e) { headers['x-xsrf-token'] = m[1]; } }
  try {
    const r = await fetch(location.origin + path, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
      credentials: 'include',
      signal: ctrl.signal,
    });
    return { status: r.status, body: await r.text() };
  } finally { clearTimeout(t); }
}"""


def load_config() -> dict:
    """설정 로드. state_file만 필수(로그인/검증). prod push(S2)는 account_key·prod_base_url·ingest_token.

    설정 파일이 없으면 기본값으로 동작(로그인·검증에는 prod 정보 불필요). push 3종이 다 있으면
    fetch 성공 후 prod ingest로 push한다(없으면 S1처럼 로그 출력만 — 하위호환).
    """
    cfg: dict = {}
    if CONFIG_PATH.is_file():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            log.warning("설정 파일 파싱 실패(기본값 사용): %s", e)
            cfg = {}
    cfg.setdefault("state_file", DEFAULT_STATE)
    return cfg


def _push_configured(cfg: dict) -> bool:
    """prod push에 필요한 3종(account_key·prod_base_url·ingest_token)이 모두 있으면 True."""
    return bool(cfg.get("account_key") and cfg.get("prod_base_url") and cfg.get("ingest_token"))


def _vs_payload(cfg: dict) -> dict:
    """vendor-summary body — 닫힌 과거일 윈도우(D-3, 어제까지). registrationTypes=3P+RG 전체.

    days 기본 7(검증용 작은 윈도우). YYYY-MM-DD 문자열(ref 18).
    """
    days = int(cfg.get("vs_days", 7))
    today = datetime.now(KST).date()
    end = today - timedelta(days=1)            # 어제(오늘은 sync 시차로 부정확 → 제외, D-3)
    start = end - timedelta(days=days - 1)
    return {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "registrationTypes": ["NORMAL", "RFM"],
        "searchIds": [],
    }


def _is_logged_out(url: str) -> bool:
    u = (url or "").lower()
    return any(x in u for x in ("login", "/auth", "sso", "signin", "xauth"))


def _is_success(res) -> bool:
    """fetch 결과가 정상 vendor-summary 응답인지(로그인 감지 신호). 200 + saleSummaryByDate 키."""
    if not res or res.get("status") != 200:
        return False
    body = res.get("body") or ""
    if any(x in body.lower() for x in ("kccontext", "<html", "signin")):
        return False
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and "saleSummaryByDate" in data


def _is_auth_expired(res) -> bool:
    """세션 만료 신호인지. None(리다이렉트)/302/401/403/200-로그인HTML → True."""
    if res is None:
        return True
    status = res.get("status")
    if status in (301, 302, 303, 307, 308, 401, 403):
        return True
    if status == 200:
        body = (res.get("body") or "").lower()
        if any(x in body for x in ("kccontext", "signin", "<html")):
            return True
        return not _is_success(res)  # 200이지만 saleSummaryByDate 없음 → 의심
    return False


def _save_state(context, path: str) -> None:
    """storage_state(세션쿠키 포함)를 0600으로 저장."""
    context.storage_state(path=path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def _summarize(body: str) -> dict | None:
    """vendor-summary 응답 → {dates:{date:{rt:{gmv,units}}}, gmv_3p, gmv_rg, total_gmv}. 실패 시 None.

    saleSummaryByDate[].gmv/unitsSold를 registrationType(NORMAL=3P / RFM=RG)별로 합산(ref 18).
    """
    try:
        data = json.loads(body or "")
    except (ValueError, TypeError):
        return None
    rows = data.get("saleSummaryByDate") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    dates: dict[str, dict[str, dict[str, float]]] = {}
    gmv_3p = gmv_rg = 0.0
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = str(r.get("date") or "")
        rt = str(r.get("registrationType") or "")
        gmv = float(r.get("gmv") or 0)
        units = float(r.get("unitsSold") or 0)
        cell = dates.setdefault(d, {}).setdefault(rt, {"gmv": 0.0, "units": 0.0})
        cell["gmv"] += gmv
        cell["units"] += units
        if rt == "NORMAL":
            gmv_3p += gmv
        elif rt == "RFM":
            gmv_rg += gmv
    total = 0.0
    sm = data.get("summaryMetrics") if isinstance(data, dict) else None
    if isinstance(sm, dict):
        total = float(sm.get("totalGmv") or 0)
    return {
        "dates": dates,
        "gmv_3p": gmv_3p,
        "gmv_rg": gmv_rg,
        "total_gmv": total or (gmv_3p + gmv_rg),
        "last_refresh": (data.get("lastRefreshTimestamp") if isinstance(data, dict) else None),
    }


def _fetch_vendor_summary(page, cfg: dict, retries: int = 2):
    """판매분석 페이지 이동 후 same-origin vendor-summary fetch. 반환: res dict 또는 None(로그아웃)."""
    page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
    page.wait_for_timeout(3000)  # Cloudflare/Akamai JS 챌린지·세션 안정화
    if _is_logged_out(page.url):
        return None
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return page.evaluate(_VS_FETCH_JS, [_vs_payload(cfg), VENDOR_SUMMARY_PATH])
        except Exception as e:  # noqa: BLE001 — 봇감지 순간차단 재시도
            last_exc = e
            if attempt < retries:
                log.warning("fetch 일시 실패(%d/%d) 재시도: %s", attempt, retries, str(e)[:80])
                page.wait_for_timeout(2500)
    raise last_exc


def _log_summary(tag: str, summ: dict, payload: dict) -> None:
    """파싱 요약을 로그로 출력(라이브 검증용). prod push는 S2."""
    log.info(
        "%s vendor-summary %s~%s | 3P GMV=%s · RG GMV=%s · 합계=%s (refresh=%s)",
        tag, payload["startDate"], payload["endDate"],
        f"{summ['gmv_3p']:,.0f}", f"{summ['gmv_rg']:,.0f}",
        f"{summ['total_gmv']:,.0f}", summ.get("last_refresh"),
    )
    for d in sorted(summ["dates"]):
        rt = summ["dates"][d]
        log.info("    %s | 3P=%s · RG=%s", d,
                 f"{rt.get('NORMAL', {}).get('gmv', 0):,.0f}",
                 f"{rt.get('RFM', {}).get('gmv', 0):,.0f}")


def _push(cfg: dict, summ: dict) -> int:
    """파싱 요약 → prod ingest로 push(닫힌일×등록유형 GMV/수량). 0=성공, 1=실패(best-effort).

    body: {account_key, days:[{date, registration_type(NORMAL|RFM), gmv, units_sold, last_refresh}]}.
    push 미설정(account_key 등 누락)이면 호출되지 않음(_do_run에서 게이트). 금액은 원 단위 정수.
    """
    days = []
    last_refresh = summ.get("last_refresh")
    for d in sorted(summ.get("dates", {})):
        for rt, vals in summ["dates"][d].items():
            if rt not in ("NORMAL", "RFM"):
                continue
            days.append({
                "date": d,
                "registration_type": rt,
                "gmv": int(round(vals.get("gmv", 0))),
                "units_sold": int(round(vals.get("units", 0))),
                "last_refresh": last_refresh,
            })
    if not days:
        log.warning("push할 날짜 데이터 없음 — 건너뜀")
        return 1
    try:
        pr = requests.post(
            cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/wing/vendor-summary/ingest",
            json={"account_key": cfg["account_key"], "days": days},
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
        info = pr.text[:120]
    log.info("vendor-summary push 성공: account=%s days=%d → %s",
             cfg["account_key"], len(days), info)
    return 0


def _login_wait_loop(page, ctx, cfg: dict, state: str, wait_secs: int):
    """열린 page에서 사용자 로그인을 자동 감지(vendor-summary 200). 성공 시 state 저장 후 res 반환."""
    waited = 0
    while waited < wait_secs:
        try:
            page.wait_for_timeout(5000)
            waited += 5
            if _is_logged_out(page.url):
                continue
            res = page.evaluate(_VS_FETCH_JS, [_vs_payload(cfg), VENDOR_SUMMARY_PATH])
        except Exception as e:  # noqa: BLE001
            if "closed" in str(e).lower():
                log.error("브라우저 창이 닫혔습니다 — 로그인 미완료(창을 닫지 말고 로그인만 하세요).")
                return None
            continue
        if _is_success(res):
            _save_state(ctx, state)
            return res
    return None


def cmd_login(cfg: dict, wait_secs: int = 600) -> int:
    """헤드풀 창(모바일 UA)을 열고 Wing 로그인을 자동 감지(vendor-summary 200) → state 저장 + 첫 파싱.

    사용자는 뜬 창에서 wing.coupang.com에 로그인만 하면 됨(Enter 불필요).
    """
    state = os.path.expanduser(cfg["state_file"])
    log.info("로그인 브라우저(모바일 UA)를 엽니다 — wing.coupang.com에 로그인하세요(자동 감지, 최대 %d초).", wait_secs)
    res = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(user_agent=_UA)
        try:
            page = ctx.new_page()
            page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
            res = _login_wait_loop(page, ctx, cfg, state, wait_secs)
        finally:
            ctx.close()
            browser.close()
    if res is None:
        log.error("제한 시간 내 로그인 감지 실패 — 다시 시도하세요.")
        return 1
    log.info("로그인 감지·세션 저장 완료: %s", state)
    summ = _summarize(res.get("body") or "")
    if summ:
        _log_summary("[login]", summ, _vs_payload(cfg))
        if _push_configured(cfg):
            return _push(cfg, summ)   # 첫 데이터 즉시 push
    return 0


@contextlib.contextmanager
def _try_fetch_lock():
    """비차단 flock. yield True(획득)/False(이미 사용 중). 광고 페처와 동일 패턴."""
    lock_fd = os.open(str(LOCK_PATH), os.O_WRONLY | os.O_CREAT, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            acquired = False
        yield acquired
    finally:
        if acquired:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _do_run(cfg: dict, state: str, login_wait_secs: int = 0) -> int:
    """state 로드 → vendor-summary fetch → 파싱·출력. 만료 시 재진입(헤드풀 Cloudflare 자동해소) 후 재fetch.

    S1: prod push 없음(파싱 결과 로그 출력으로 라이브 검증). 세션 만료 회복 흐름은 라이브 실측 대상.
    """
    res = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            ctx = browser.new_context(storage_state=state, user_agent=_UA)
            try:
                page = ctx.new_page()
                res = _fetch_vendor_summary(page, cfg)
                if _is_auth_expired(res):
                    # 세션 만료 의심 → 대시보드 재진입으로 Cloudflare 챌린지 자동해소 후 1회 재시도.
                    # (Wing의 무재로그인 재발급 가능 여부는 라이브 실측 대상 — 추정 금지.)
                    log.info("세션 만료 의심 — 대시보드 재진입 후 재fetch 시도")
                    res2 = _fetch_vendor_summary(page, cfg)
                    if _is_success(res2):
                        _save_state(ctx, state)  # 회전 쿠키 보존
                        res = res2
                    elif login_wait_secs > 0:
                        log.info("자동 회복 실패 — 창에서 로그인하세요(자동 감지, 최대 %d초).", login_wait_secs)
                        with contextlib.suppress(Exception):
                            page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
                        relogin = _login_wait_loop(page, ctx, cfg, state, login_wait_secs)
                        res = relogin if relogin is not None else res2
                    else:
                        res = res2
                elif _is_success(res):
                    _save_state(ctx, state)  # 회전된 세션쿠키 갱신
            finally:
                ctx.close()
                browser.close()
    except Exception as e:  # noqa: BLE001
        log.error("브라우저 fetch 오류: %s", e)
        return 1

    if not _is_success(res):
        status = res.get("status") if res else None
        body = (res.get("body") if res else "") or ""
        log.error("vendor-summary 실패 status=%s — %s. 'login' 재실행 필요.",
                  status, body[:160].replace("\n", " "))
        return 1
    summ = _summarize(res.get("body") or "")
    if not summ:
        log.error("vendor-summary 파싱 실패 — 응답 형태 변경 의심: %s", (res.get("body") or "")[:160])
        return 1
    _log_summary("[run]", summ, _vs_payload(cfg))
    if _push_configured(cfg):
        return _push(cfg, summ)   # push 성공이 heartbeat(prod staleness 기준)
    return 0


def cmd_run(cfg: dict) -> int:
    """1회 실행 — state 로드 → fetch → 파싱·출력. 세션 없으면 fail-fast."""
    state = os.path.expanduser(cfg["state_file"])
    if not Path(state).is_file():
        log.error("세션 파일 없음 — 먼저 'login' 실행: %s", state)
        return 2
    with _try_fetch_lock() as acquired:
        if not acquired:
            log.warning("다른 실행이 진행 중 — 이번 호출 건너뜀")
            return 0
        return _do_run(cfg, state, login_wait_secs=0)


_POLL_INTERVAL_S = 15       # 갱신 요청 확인 간격(창 안 뜸, 가벼운 GET)
_LOGIN_WAIT_S = 180         # 세션 만료 시 헤드풀 창 로그인 대기 한도
_MIN_FETCH_INTERVAL_S = 45  # fetch(창) 최소 간격 — 요청 폭주로 창 스팸 방지(광고 패턴)


def _prod_refresh_status(cfg: dict) -> dict:
    r = requests.get(
        cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/wing/vendor-summary/refresh-status",
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _prod_claim(cfg: dict) -> dict:
    r = requests.post(
        cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/wing/vendor-summary/refresh-claim",
        headers={"X-Ingest-Token": cfg["ingest_token"]},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def cmd_poll(cfg: dict) -> int:
    """상주 데몬(별도 plist com.ohisell.wing, D-5) — 갱신 요청이 있을 때만 headful fetch.

    평소엔 가벼운 GET만(창 안 뜸). UI '판매분석 갱신' 버튼이 요청을 set하면 claim 후 fetch 1회+push.
    세션 만료 시 같은 창에서 로그인 대기(아침 첫 클릭이 로그인 겸함, 광고 페처 패턴 D-1).
    push 미설정이면 데몬은 무의미하므로 fail-fast.
    """
    if not _push_configured(cfg):
        log.error("poll 데몬엔 account_key·prod_base_url·ingest_token 필요 — 설정 누락.")
        return 2
    state = os.path.expanduser(cfg["state_file"])
    interval = int(cfg.get("poll_interval_s", _POLL_INTERVAL_S))
    cooldown = int(cfg.get("min_fetch_interval_s", _MIN_FETCH_INTERVAL_S))
    last_fetch = 0.0
    log.info("Wing 폴 데몬 시작 — %ds 간격 확인, fetch 최소간격 %ds(창은 요청 시에만 뜸).", interval, cooldown)
    while True:
        try:
            st = _prod_refresh_status(cfg)
            if st.get("requested"):
                # 쿨다운/락은 claim '전에' 검사 — claim 후 스킵하면 요청 유실(광고 패턴 codex P2).
                if time.monotonic() - last_fetch < cooldown:
                    log.info("fetch 쿨다운 중 — 요청 보류(다음 폴에서 처리)")
                else:
                    with _try_fetch_lock() as acquired:
                        if not acquired:
                            log.info("다른 fetch 진행 중 — 요청 보류(다음 폴에서 처리)")
                        elif _prod_claim(cfg).get("claimed"):
                            last_fetch = time.monotonic()
                            log.info("갱신 요청 감지 — fetch 시작")
                            if not Path(state).is_file():
                                cmd_login(cfg, wait_secs=_LOGIN_WAIT_S)
                            else:
                                _do_run(cfg, state, login_wait_secs=_LOGIN_WAIT_S)  # 락 보유 중
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
