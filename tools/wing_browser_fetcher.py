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
#   실행(1회):   backend/.venv/bin/python3 tools/wing_browser_fetcher.py          # state 로드 → vendor-summary fetch → push
#   RG 정산(S4): backend/.venv/bin/python3 tools/wing_browser_fetcher.py rg        # 정산 엑셀 다운로드 → prod push
#   상주 데몬:   backend/.venv/bin/python3 tools/wing_browser_fetcher.py poll      # launchd(com.ohisell.wing)
#
# 설정 파일 ~/.ohisell_wing_fetcher.json (push용):
#   {"account_key":"COUPANG_WING1","prod_base_url":"https://sellc.ohitech.co.kr","ingest_token":"<AD_INGEST_TOKEN>","vs_days":7,
#    "vendor_id":"A01564720","rg_report_types":["WAREHOUSING_SHIPPING"],"rg_max_periods":1,"rg_days":21,
#    "rg_daily_hour":7,"rg_min_interval_s":3600}
#   (vendor_id·rg_* 는 'rg' 명령·poll 데몬 RG 분기용. account_key=WING1→vendor_id A01564720(오픽스),
#    WING2→A01029796(오하이테크). rg_daily_hour=새벽 일일예약 KST 시각, rg_min_interval_s=RG 실행 최소간격.)
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

# 브라우저측 same-origin JSON POST — location.origin+경로, XSRF-TOKEN 쿠키를 x-xsrf-token 헤더로
# 더블서브밋. AbortController 25s 타임아웃(무한 hang 방지). 인자=[payload, path]. 반환: {status, body}.
# vendor-summary(S2)·RG 정산 다운로드(S4) 양쪽이 공용으로 쓴다(같은 인증·세션·CORS 규칙, D-6).
_POST_JSON_JS = """async (args) => {
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
            return page.evaluate(_POST_JSON_JS, [_vs_payload(cfg), VENDOR_SUMMARY_PATH])
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
            res = page.evaluate(_POST_JSON_JS, [_vs_payload(cfg), VENDOR_SUMMARY_PATH])
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


def _prod_rg_refresh_status(cfg: dict) -> dict:
    r = requests.get(
        cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/wing/rg-settlement/refresh-status",
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _prod_rg_claim(cfg: dict) -> dict:
    r = requests.post(
        cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/wing/rg-settlement/refresh-claim",
        headers={"X-Ingest-Token": cfg["ingest_token"]},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def cmd_poll(cfg: dict) -> int:
    """상주 데몬(별도 plist com.ohisell.wing, D-5) — 갱신 요청/예약이 있을 때만 headful 창.

    평소엔 가벼운 GET만(창 안 뜸). 두 흐름을 함께 소비:
      ① vendor-summary: '판매분석 갱신' 버튼 → claim → fetch+push.
      ② RG 정산(S4-P2): 'RG 정산 갱신' 버튼(온디맨드) + 새벽 일일 예약(rg_daily_hour 이후 1회)
         → claim/예약 → _do_rg_run(엑셀 다운로드+push). RG 별도 쿨다운(주 단위·느림).
    세션 만료 시 같은 창에서 로그인 대기(아침 첫 트리거가 로그인 겸함, 광고 페처 패턴 D-1).
    push 미설정이면 데몬은 무의미하므로 fail-fast.
    """
    if not _push_configured(cfg):
        log.error("poll 데몬엔 account_key·prod_base_url·ingest_token 필요 — 설정 누락.")
        return 2
    state = os.path.expanduser(cfg["state_file"])
    interval = int(cfg.get("poll_interval_s", _POLL_INTERVAL_S))
    cooldown = int(cfg.get("min_fetch_interval_s", _MIN_FETCH_INTERVAL_S))
    last_fetch = 0.0
    # RG 정산(S4-P2): 온디맨드 버튼 + 새벽 일일 예약. RG는 주 단위·느림(생성 대기) → 별도 쿨다운.
    rg_daily_hour = int(cfg.get("rg_daily_hour", 7))        # KST 일일 예약 시각(이후 1회)
    rg_cooldown = int(cfg.get("rg_min_interval_s", 3600))   # RG 실행 최소 간격(실패 재시도 폭주 방지)
    last_rg = 0.0
    rg_done_date = None                                     # 오늘 일일 실행 완료 표시(KST date)
    log.info("Wing 폴 데몬 시작 — %ds 간격 확인, fetch 최소간격 %ds. RG 일일예약 %02d시·간격 %ds(창은 요청 시에만 뜸).",
             interval, cooldown, rg_daily_hour, rg_cooldown)
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

        # ── RG 정산 다운로드: 온디맨드 버튼 or 새벽 일일 예약(codex P1#1 — 데몬이 RG도 소비) ──
        try:
            now = datetime.now(KST)
            rg_st = _prod_rg_refresh_status(cfg)
            rg_requested = bool(rg_st.get("requested"))
            last_succ = str(rg_st.get("last_success_at") or "")
            ran_today = last_succ[:10] == now.date().isoformat()   # 오늘 push 있었나
            daily_due = (now.hour >= rg_daily_hour and not ran_today and rg_done_date != now.date())
            if (rg_requested or daily_due) and (time.monotonic() - last_rg >= rg_cooldown):
                with _try_fetch_lock() as acquired:
                    if not acquired:
                        log.info("RG: 다른 fetch 진행 중 — 보류(다음 폴)")
                    else:
                        # 온디맨드면 claim으로 소비(요청 유실 방지). 일일예약은 플래그 없음 → claim 불필요.
                        # NOTE(codex P2): claim은 실행 성공 전에 이뤄져 실패 시 버튼요청 유실(vendor-summary와
                        #   동일 패턴). 일일예약이 실패를 재시도 커버. RG 새로고침 UI 버튼 추가 시 재검토.
                        proceed = _prod_rg_claim(cfg).get("claimed", False) if rg_requested else True
                        if proceed:
                            last_rg = time.monotonic()
                            log.info("RG 정산 다운로드 트리거(%s)", "버튼" if rg_requested else "일일예약")
                            if not Path(state).is_file():
                                log.warning("RG: 세션 파일 없음 — 'login' 필요(이번 회차 스킵)")
                            elif _do_rg_run(cfg, state, login_wait_secs=_LOGIN_WAIT_S) == 0:
                                rg_done_date = now.date()   # 성공 시 오늘 일일예약 완료 표시
        except requests.RequestException as e:
            log.warning("RG 폴 확인 실패(네트워크): %s", str(e)[:80])
        except Exception as e:  # noqa: BLE001 — 데몬은 어떤 오류에도 죽지 않는다
            log.error("RG 폴 루프 오류: %s", str(e)[:160])
        time.sleep(interval)


# ════════════════════════════════════════════════════════════════════
# S4: RG 정산 엑셀 자동 다운로드 (Wing 세션 자동화 트랙 D-8)
# ════════════════════════════════════════════════════════════════════
# 흐름(전부 살아있는 브라우저 세션 same-origin POST, D-5/D-6):
#   ① status/api          → 정산주기(settlementGroupKey) 목록 열거(어떤 주를 받을지)
#   ② request-download/api → 엑셀 생성요청(requestTime=내가 정한 고유값)
#   ③ download-list/api 폴링 → downloadStatus=="COMPLETED" + 내 requestTime 매칭
#   ④ download/api/v2     → S3 presigned url
#   ⑤ (Mac) requests.get(S3, 무인증·24h 유효) → xlsx bytes
#   ⑥ (Mac→prod) POST /api/coupang/ops/rg/settlement/upload-xlsx (기존 ingest 재사용, 백엔드 무변경)
# API 양식·응답은 2026-06-14 오픽스 WING1 DevTools 캡처로 검증(ref17 §8-2). 코드 body·필드명 일치.
# 매칭 키=requestTime(캡처 확인: download-list 항목 requestTime == request-download에 보낸 값).
#
# ★미검증·de-risk(원칙22, D-8): 페처는 판매분석(m-wing)에 로그인돼 있다. 정산 페이지(데스크톱
# 호스트 wing.coupang.com)가 이 세션에서 어느 origin에 착지하는지·same-origin 200 여부는 라이브 실측.
# location.origin+경로라 호스트는 자동 대응되나, cf_clearance가 정산 호스트를 커버하는지는 실측 대상.

RG_DASH_URL = "https://wing.coupang.com/tenants/rfm/settlements/status-new"
RG_STATUS_PATH = "/tenants/rfm/v2/settlements/status/api"
RG_REQUEST_DOWNLOAD_PATH = "/tenants/rfm/v2/settlements/request-download/api"
RG_DOWNLOAD_LIST_PATH = "/tenants/rfm/v2/settlements/download-list/api"
RG_DOWNLOAD_V2_PATH = "/tenants/rfm/v2/settlements/download/api/v2"
RG_UPLOAD_PATH = "/api/coupang/ops/rg/settlement/upload-xlsx"
# 기본 다운로드 대상 — 파서 검증 완료 1종(ref17 §8-1). 나머지 7종 코드명 확보 후 확장(D-8).
RG_REPORT_TYPES_DEFAULT = ["WAREHOUSING_SHIPPING"]
_RG_POLL_INTERVAL_S = 8       # download-list 폴링 간격
_RG_POLL_TIMEOUT_S = 300      # 생성 완료 최대 대기(5분)
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _kst_date_to_utc_iso(kst_date) -> str:
    """KST date → status/api용 UTC ISO 'YYYY-MM-DDT15:00:00.000Z'(KST 00:00=UTC, client와 동일)."""
    return f"{kst_date.isoformat()}T15:00:00.000Z"


def _rg_status_payload(cfg: dict) -> dict:
    """status/api body — 최근 윈도우(매출인식일 SALES, D-10). 정산주기 열거용."""
    days = int(cfg.get("rg_days", 21))   # 최근 ~3주 → 닫힌 주별 정산 여러 건 포함
    today = datetime.now(KST).date()
    return {
        "startDate": _kst_date_to_utc_iso(today - timedelta(days=days)),
        "endDate": _kst_date_to_utc_iso(today),
        "searchDateType": "SALES",
    }


def _rg_post(page, path: str, payload: dict):
    """정산 same-origin POST(vendor-summary와 동일 헬퍼 재사용). 반환 {status, body}."""
    return page.evaluate(_POST_JSON_JS, [payload, path])


def _rg_json(res):
    """res({status, body}) → 파싱 JSON. 200 아니거나 로그인 HTML이면 None."""
    if not res or res.get("status") != 200:
        return None
    body = res.get("body") or ""
    if any(x in body.lower() for x in ("kccontext", "<html", "signin")):
        return None
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return None


def _rg_enumerate_group_keys(page, cfg: dict, vendor_id: str) -> list[dict]:
    """status/api → [{group_key, period_end}]. settlementGroupKey 우선, 없으면 vendorId-start-end 생성.

    설치분(가지급/확정)으로 같은 기간 리포트가 2개 와도 group_key 동일 → dedupe.
    """
    res = _rg_post(page, RG_STATUS_PATH, _rg_status_payload(cfg))
    data = _rg_json(res)
    if not isinstance(data, dict):
        raise RuntimeError(f"status/api 응답 비정상(status={res.get('status') if res else None})")
    reports = data.get("settlementStatusReports")
    if not isinstance(reports, list):
        raise RuntimeError("status/api에 settlementStatusReports 없음(스키마 드리프트 의심)")
    out: list[dict] = []
    seen: set[str] = set()
    for rep in reports:
        if not isinstance(rep, dict):
            continue
        start = str(rep.get("settlementPeriodStartDate") or "")[:10]
        end = str(rep.get("settlementPeriodEndDate") or "")[:10]
        gk = str(rep.get("settlementGroupKey") or "").strip()
        if not gk:
            if not (start and end):
                continue
            gk = f"{vendor_id}-{start}-{end}"
        if not end:
            end = gk[-10:]   # group_key 꼬리에서 추출 폴백
        if gk in seen:
            continue
        seen.add(gk)
        out.append({"group_key": gk, "period_end": end})
    return out


def _rg_find_completed(page, from_ms: int, want: str):
    """download-list 1회 조회 → 내 requestTime(want)의 COMPLETED 항목만(정확 매칭). 없으면 None.

    ★폴백 없음(codex P1): download-list 항목은 기간(group_key)을 안 실어준다
    (requestedSettlementGroupKeys=null·recognitionDate*=null, 라이브 캡처 확인). reportType-only
    폴백은 다른 주/수동 생성분을 현재 기간으로 오업로드할 수 있어 제거. 내가 보낸 고유 requestTime만 신뢰.
    """
    to_ms = int(time.time() * 1000) + 60_000
    res = _rg_post(page, RG_DOWNLOAD_LIST_PATH,
                   {"requestTimeFrom": str(from_ms), "requestTimeTo": str(to_ms)})
    items = _rg_json(res)
    if not isinstance(items, list):
        return None
    for it in items:
        if (isinstance(it, dict) and str(it.get("requestTime")) == want
                and it.get("downloadStatus") == "COMPLETED"):
            return it
    return None


def _rg_download_one(page, group_key: str, report_type: str, req_time_ms: int, poll_timeout: int):
    """단일 (group_key, report_type): 생성요청 → 폴링 → v2. 반환 {url, request_time} 또는 None."""
    res = _rg_post(page, RG_REQUEST_DOWNLOAD_PATH, {
        "sellerReportType": report_type,
        "requestTime": str(req_time_ms),
        "settlementGroupKeys": [group_key],
        "locale": "ko",
    })
    data = _rg_json(res)
    if not isinstance(data, dict):
        log.warning("RG request-download 실패 %s/%s status=%s",
                    report_type, group_key, res.get("status") if res else None)
        return None
    if data.get("duplicateRequest"):
        # ★dup 스킵(codex P1): 기존 생성분은 download-list로 기간 식별 불가 → 오업로드 위험.
        #   일일 캐던스(>24h)는 dedup 윈도우 밖이라 dup이 거의 없음. dup이면 이번 회차만 건너뛰고
        #   직전(생성 당시 fresh)·다음 fresh 실행에 맡긴다. 빠른 재실행 시 스킵이 안전한 선택.
        log.info("RG 중복 요청(기간 안전식별 불가) — 스킵 %s/%s", report_type, group_key)
        return None
    want = str(req_time_ms)
    from_ms = req_time_ms - 24 * 3600 * 1000

    item = None
    deadline = time.time() + poll_timeout
    while time.time() < deadline and item is None:
        item = _rg_find_completed(page, from_ms, want)
        if item is None:
            time.sleep(_RG_POLL_INTERVAL_S)
    if item is None:
        log.warning("RG 생성 대기 타임아웃 %s/%s", report_type, group_key)
        return None

    rt = str(item.get("requestTime"))
    res2 = _rg_post(page, RG_DOWNLOAD_V2_PATH, {"requestTime": rt, "locale": "ko"})
    d2 = _rg_json(res2)
    url = d2.get("url") if isinstance(d2, dict) else None
    if not url:
        log.warning("RG download/api/v2 url 없음 %s/%s", report_type, group_key)
        return None
    return {"url": url, "request_time": rt}


def _rg_push_xlsx(cfg: dict, url: str, report_type: str, group_key: str) -> int:
    """S3에서 xlsx GET(무인증·24h) → prod 업로드(기존 ingest 재사용). 0=성공/1=실패."""
    try:
        r = requests.get(url, timeout=90)
        r.raise_for_status()
    except requests.RequestException as e:
        log.error("RG S3 다운로드 실패 %s/%s: %s", report_type, group_key, e)
        return 1
    content = r.content
    filename = url.split("?", 1)[0].rsplit("/", 1)[-1] or f"{report_type}.xlsx"
    try:
        pr = requests.post(
            cfg["prod_base_url"].rstrip("/") + RG_UPLOAD_PATH,
            # account_key 명시(codex P1#3): S3 경로 파일명에만 의존하지 않고 설정 계정으로 직접 지정.
            #   백엔드는 명시 account_key와 파일명 vendor_id 일치도 검증(WING1↔A01564720) → 오배치도 차단.
            params={"account_key": cfg["account_key"]},
            files={"file": (filename, content, _XLSX_MIME)},
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            timeout=60,
        )
    except requests.RequestException as e:
        log.error("RG prod push 네트워크 오류 %s: %s", filename, e)
        return 1
    if pr.status_code != 200:
        log.error("RG prod push 실패 HTTP %s — %s", pr.status_code, pr.text[:200])
        return 1
    try:
        info = pr.json()
    except ValueError:
        info = pr.text[:120]
    log.info("RG push 성공 %s (%d bytes) → %s", filename, len(content), info)
    return 0


def _rg_session_ok(page) -> bool:
    """정산 status/api가 정상 JSON(settlementStatusReports 키)을 주면 로그인 상태.

    ★호스트 무관(location.origin same-origin). vendor-summary(m-wing) 감지로 정산 세션을 판단하면
    틀리므로(셀프리뷰 A), 정산 자체의 status/api로 직접 판정한다. 빈 cfg 기본값으로 호출 가능.
    """
    try:
        data = _rg_json(_rg_post(page, RG_STATUS_PATH, _rg_status_payload({})))
    except Exception:  # noqa: BLE001 — 네비게이션 중 evaluate 실패 등은 '아직 아님'으로 처리
        return False
    return isinstance(data, dict) and "settlementStatusReports" in data


def _rg_login_wait(page, ctx, state: str, secs: int) -> bool:
    """정산 페이지에서 사용자 로그인 자동 감지(status/api 200). 성공 시 state 저장. (데몬 회복 경로)"""
    waited = 0
    while waited < secs:
        try:
            page.wait_for_timeout(5000)
            waited += 5
            if _rg_session_ok(page):
                _save_state(ctx, state)
                return True
        except Exception as e:  # noqa: BLE001
            if "closed" in str(e).lower():
                log.error("브라우저 창이 닫혔습니다 — RG 로그인 미완료.")
                return False
    return False


def _do_rg_run(cfg: dict, state: str, login_wait_secs: int = 0) -> int:
    """state 로드 → 정산 페이지 → 정산주기 열거 → (key×reportType) 다운로드 → prod push.

    push 설정 필수(다운로드만 하고 버릴 이유 없음). 세션 판정·만료 회복은 정산 status/api 기반.
    """
    if not _push_configured(cfg):
        log.error("RG 다운로드엔 push 설정(account_key·prod_base_url·ingest_token) 필요.")
        return 2
    vendor_id = str(cfg.get("vendor_id") or "").strip()
    if not vendor_id:
        log.error("RG 다운로드엔 설정에 vendor_id 필요(예 A01564720).")
        return 2
    report_types = cfg.get("rg_report_types") or RG_REPORT_TYPES_DEFAULT
    max_periods = int(cfg.get("rg_max_periods", 1))   # P1: 최근 1주(dup 모호성 회피)
    poll_timeout = int(cfg.get("rg_poll_timeout_s", _RG_POLL_TIMEOUT_S))

    pushed = failed = 0
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            ctx = browser.new_context(storage_state=state, user_agent=_UA)
            try:
                page = ctx.new_page()
                page.goto(RG_DASH_URL, wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(3500)   # Cloudflare/Akamai JS 챌린지 안정화
                if not _rg_session_ok(page):
                    if login_wait_secs <= 0:
                        log.error("RG: 세션 만료(정산 status/api 미응답). 'login' 또는 데몬 로그인 필요.")
                        return 1
                    log.info("RG: 세션 만료 — 창에서 로그인하세요(자동 감지, 최대 %d초).", login_wait_secs)
                    if not _rg_login_wait(page, ctx, state, login_wait_secs):
                        log.error("RG: 로그인 감지 실패.")
                        return 1
                else:
                    _save_state(ctx, state)   # 세션 유효 → 회전 쿠키 보존

                try:
                    periods = _rg_enumerate_group_keys(page, cfg, vendor_id)
                except Exception as e:  # noqa: BLE001 — 응답 비정상/챌린지 → 실패 보고
                    log.error("RG status/api 열거 실패(정산 페이지 same-origin 200 미확인?): %s", e)
                    return 1
                if not periods:
                    log.info("RG: status/api에 정산주기 없음 — 다운로드 건너뜀.")
                    return 0
                periods.sort(key=lambda x: x["period_end"], reverse=True)
                targets = periods[:max_periods]
                log.info("RG: 정산주기 %d개 중 최근 %d개 처리 → %s",
                         len(periods), len(targets), [t["group_key"] for t in targets])

                base_ms = int(time.time() * 1000)
                idx = 0
                for t in targets:
                    for rt in report_types:
                        req_time = base_ms + idx
                        idx += 1
                        got = _rg_download_one(page, t["group_key"], rt, req_time, poll_timeout)
                        if not got:
                            failed += 1
                            continue
                        if _rg_push_xlsx(cfg, got["url"], rt, t["group_key"]) == 0:
                            pushed += 1
                        else:
                            failed += 1
                _save_state(ctx, state)   # 회전 쿠키 보존
            finally:
                ctx.close()
                browser.close()
    except Exception as e:  # noqa: BLE001 — 브라우저 오류는 1로 보고(데몬은 죽지 않음)
        log.error("RG 브라우저 실행 오류: %s", e)
        return 1
    log.info("RG 다운로드 완료 — push 성공 %d / 실패 %d", pushed, failed)
    return 0 if failed == 0 else 1


def cmd_rg(cfg: dict) -> int:
    """1회 RG 정산 엑셀 자동 다운로드(state 로드 → 다운로드 → prod push). 세션 없으면 fail-fast."""
    state = os.path.expanduser(cfg["state_file"])
    if not Path(state).is_file():
        log.error("세션 파일 없음 — 먼저 'login' 실행: %s", state)
        return 2
    with _try_fetch_lock() as acquired:
        if not acquired:
            log.warning("다른 실행이 진행 중 — 이번 호출 건너뜀")
            return 0
        return _do_rg_run(cfg, state, login_wait_secs=0)


def main() -> None:
    cfg = load_config()
    arg = sys.argv[1] if len(sys.argv) >= 2 else ""
    if arg == "login":
        sys.exit(cmd_login(cfg))
    if arg == "rg":
        sys.exit(cmd_rg(cfg))
    if arg == "poll":
        try:
            sys.exit(cmd_poll(cfg))
        except KeyboardInterrupt:
            log.info("폴 데몬 종료")
            sys.exit(0)
    sys.exit(cmd_run(cfg))


if __name__ == "__main__":
    main()
