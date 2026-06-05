#!/usr/bin/env python3
# ad_cost_browser_fetcher.py — 쿠팡 광고비를 "실제 브라우저"(Playwright 영속 프로필)로 fetch → prod push.
#
# 왜 브라우저인가 (실측 근거):
#   - advertising.coupang.com은 Akamai가 데이터센터 IP를 차단(prod=403).
#   - curl/requests로 쿠키를 재생하면 "1회용": 첫 요청 성공 직후 세션 토큰이 회전·무효화되어
#     두 번째 호출부터 Keycloak 로그인으로 튕김. 자동화 불가.
#   - 실제 브라우저(영속 프로필)는 JS 센서로 Akamai를 통과하고, 토큰 회전을 자동 유지 →
#     한 번 로그인하면 며칠~몇 주 무인 동작.
#
# 사용:
#   1회 로그인:  python3 ad_cost_browser_fetcher.py login   # 창 떠서 직접 로그인 → Enter
#   실행(스케줄): python3 ad_cost_browser_fetcher.py          # fetch→prod push (launchd 매시)
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
DEFAULT_PROFILE = os.path.expanduser("~/.ohisell_ad_browser_profile")
DASH_URL = "https://advertising.coupang.com/marketing/dashboard/sales?_cap_client=WING"
ADS_API = "https://advertising.coupang.com/marketing/cmg-api/report/cost"
KST = ZoneInfo("Asia/Seoul")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("ad_browser")

# AbortController로 25s 타임아웃 — 브라우저측 fetch가 무한정 매달려 프로필이 잠기는 것 방지(codex P1).
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
    cfg.setdefault("browser_profile_dir", DEFAULT_PROFILE)
    try:  # vendor_ids 정수 검증(설정 오류를 명확한 메시지로, codex P2)
        cfg["vendor_ids"] = [int(v) for v in cfg["vendor_ids"]]
    except (ValueError, TypeError):
        log.error("vendor_ids는 정수 목록이어야 함: %r", cfg.get("vendor_ids"))
        sys.exit(2)
    return cfg


def _is_logged_out(url: str) -> bool:
    u = (url or "").lower()
    return any(x in u for x in ("login", "/auth", "sso", "signin"))


def cmd_login(cfg: dict) -> None:
    profile = os.path.expanduser(cfg["browser_profile_dir"])
    log.info("로그인 브라우저를 엽니다 — 창에서 advertising.coupang.com에 로그인하세요.")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(profile, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(DASH_URL)
        input("로그인(대시보드가 보이면)을 마친 뒤 이 터미널에서 Enter를 누르세요... ")
        ctx.close()
    log.info("로그인 프로필 저장 완료: %s", profile)


def cmd_run(cfg: dict) -> int:
    profile = os.path.expanduser(cfg["browser_profile_dir"])
    if not Path(profile).is_dir():
        log.error("브라우저 프로필 없음 — 먼저 'login' 실행: %s", profile)
        return 2

    # 동시 실행 방지(codex P2): 직전 run이 1h 넘게 돌면 launchd가 또 띄워 프로필이 잠김.
    # flock은 프로세스 종료 시 자동 해제 → stale lock 자동 처리.
    lock_fd = os.open(str(LOCK_PATH), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.warning("다른 실행이 진행 중 — 이번 호출 건너뜀")
        os.close(lock_fd)
        return 0
    try:
        return _do_run(cfg, profile)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _do_run(cfg: dict, profile: str) -> int:

    payload = {"parentNodes": [{"adNodeId": int(v), "campaignType": "PA"} for v in cfg["vendor_ids"]]}
    headless = bool(cfg.get("headless", True))
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(profile, headless=headless)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(2500)  # Akamai JS 센서·세션 안정화
                if _is_logged_out(page.url):
                    log.error("세션 만료 — 로그인 페이지로 리다이렉트(%s). 'login' 재실행 필요.", page.url)
                    return 1
                res = page.evaluate(_FETCH_JS, payload)
            finally:
                ctx.close()
    except Exception as e:
        log.error("브라우저 fetch 오류: %s", e)
        return 1

    status = res.get("status")
    if status != 201:
        body = res.get("body") or ""
        snippet = body[:160].replace("\n", " ")
        if status == 200 and any(x in body.lower() for x in ("login", "signin", "kccontext")):
            log.error("세션 만료(로그인 HTML 반환) — 'login' 재실행 필요. status=200")
        else:
            log.error("fetch 비정상 status=%s — %s", status, snippet)
        return 1
    try:
        data = json.loads(res.get("body") or "")
    except (ValueError, TypeError) as e:
        log.error("응답 JSON 파싱 실패: %s", e)
        return 1

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

    try:  # 200인데 본문이 비JSON이어도 push는 성공 — 로깅에서 죽지 않게(codex P2)
        info = pr.json()
    except ValueError:
        info = pr.text[:160]
    log.info("성공: %s push %s → %s", today,
             [(v["vendor_id"], v["day_cost"]) for v in vendors], info)
    return 0


def main() -> None:
    cfg = load_config()
    if len(sys.argv) >= 2 and sys.argv[1] == "login":
        cmd_login(cfg)
        return
    sys.exit(cmd_run(cfg))


if __name__ == "__main__":
    main()
