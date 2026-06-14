#!/usr/bin/env python3
# diag_nonpa_quantify.py — [읽기전용] report/SALES의 ALL_DELIVERED vs DELIVERED 갭 수치화.
# D-14 근거. 6/1~오늘 범위로 날짜별 PA(집행) vs 전체(ALL) 비교 → 비-PA 갭 정량.
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(__file__))
import ad_cost_browser_fetcher as fetcher  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
STATE = os.path.expanduser("~/.ohisell_ad_state.json")
DASH = "https://advertising.coupang.com/marketing/dashboard/sales?_cap_client=WING"

_now = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
START_MS = int(datetime(2026, 6, 1, tzinfo=KST).timestamp() * 1000)
END_MS = int((_now + timedelta(days=1)).timestamp() * 1000)

_POST_JS = """async ({url, body}) => {
  const r = await fetch(url, {method:'POST',
    headers:{'content-type':'application/json','accept':'application/json, text/plain, */*'},
    body: JSON.stringify(body), credentials:'include'});
  return { status: r.status, body: await r.text() };
}"""


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(storage_state=STATE)
        page = ctx.new_page()
        page.goto(DASH, wait_until="domcontentloaded", timeout=40000)
        page.wait_for_timeout(3000)
        if fetcher._is_logged_out(page.url):
            print("aid 만료 — SSO 재발급…")
            if not fetcher._sso_refresh(page):
                print("SSO 실패 — 중단"); ctx.close(); browser.close(); return
            fetcher._save_state(ctx, STATE)
            page.goto(DASH, wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(4000)
        res = page.evaluate(_POST_JS, {
            "url": "https://advertising.coupang.com/marketing/cmg-api/report/SALES",
            "body": {"start": START_MS, "end": END_MS}})
        ctx.close()
        browser.close()

    data = json.loads(res["body"])
    result = data.get("result", data)
    rows = []
    for epoch_ms, m in sorted(result.items()):
        if not isinstance(m, dict):
            continue
        d = datetime.fromtimestamp(int(epoch_ms) / 1000, KST).date()
        pa = int(m.get("DELIVERED_AD_COST") or 0)
        allc = int(m.get("ALL_DELIVERED_AD_COST") or 0)
        rows.append((d, pa, allc, allc - pa))

    print(f"{'날짜':<12}{'집행(PA)':>12}{'전체(ALL)':>12}{'비-PA':>10}")
    print("-" * 46)
    t_pa = t_all = 0
    for d, pa, allc, diff in rows:
        print(f"{str(d):<12}{pa:>12,}{allc:>12,}{diff:>10,}")
        t_pa += pa; t_all += allc
    print("-" * 46)
    print(f"{'합계':<12}{t_pa:>12,}{t_all:>12,}{t_all - t_pa:>10,}")
    if t_pa:
        print(f"\n비-PA 비중: {(t_all - t_pa) / t_pa * 100:.1f}% of 집행")


if __name__ == "__main__":
    main()
