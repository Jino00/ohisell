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


def _fetch(page, cfg: dict):
    """대시보드 이동 후 same-origin fetch. 반환: res dict 또는 None(로그아웃)."""
    page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
    page.wait_for_timeout(2500)  # Akamai JS 센서·세션 안정화
    if _is_logged_out(page.url):
        return None
    return page.evaluate(_FETCH_JS, _payload(cfg))


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
                        break
                    continue
                if res.get("status") == 201:
                    try:
                        ok_data = json.loads(res.get("body") or "")
                    except (ValueError, TypeError):
                        ok_data = None
                    _save_state(ctx, state)
                    break
        finally:
            ctx.close()
            browser.close()
    if ok_data is None:
        log.error("제한 시간 내 로그인 감지 실패 — 다시 시도하세요.")
        return 1
    log.info("로그인 감지·세션 저장 완료: %s", state)
    return _push(cfg, ok_data)  # 첫 데이터 즉시 push


def cmd_run(cfg: dict) -> int:
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
        return _do_run(cfg, state)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _do_run(cfg: dict, state: str) -> int:
    headless = bool(cfg.get("headless", True))
    res = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(storage_state=state)
            try:
                page = ctx.new_page()
                res = _fetch(page, cfg)
                if res is not None and res.get("status") == 201:
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


def main() -> None:
    cfg = load_config()
    if len(sys.argv) >= 2 and sys.argv[1] == "login":
        sys.exit(cmd_login(cfg))
    sys.exit(cmd_run(cfg))


if __name__ == "__main__":
    main()
