#!/usr/bin/env python3
# diag_nonpa_adcost.py — [읽기전용 조사] 쿠팡 "전체 광고비" vs "집행(PA)" 갭 정체 확인.
# S5/D-14: 비-PA 광고상품(브랜드/디스플레이 등)이 무엇·얼마인지 라이브 관찰.
# 기존 페처 세션(storage_state)을 LOAD만(저장 안 함) → 대시보드 네트워크 캡처 + report 직접 호출.
# 아무것도 prod에 push하지 않음. 페처 상태(state/marker) 불변.
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

# 페처의 SSO 재발급 로직 재사용(aid 1h 만료 → keycloak 세션으로 재발급)
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import ad_cost_browser_fetcher as fetcher  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
STATE = os.path.expanduser("~/.ohisell_ad_state.json")
CFG = json.loads(open(os.path.expanduser("~/.ohisell_ad_fetcher.json")).read())
VENDORS = [int(v) for v in CFG["vendor_ids"]]
DASH = "https://advertising.coupang.com/marketing/dashboard/sales?_cap_client=WING"

# 최근 7일 epoch ms (report/SALES와 동일 윈도우)
_now = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
START_MS = int((_now - timedelta(days=6)).timestamp() * 1000)
END_MS = int((_now + timedelta(days=1)).timestamp() * 1000)

# 브라우저측 same-origin POST 헬퍼
_POST_JS = """async ({url, body}) => {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 25000);
  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: {'content-type':'application/json','accept':'application/json, text/plain, */*'},
      body: JSON.stringify(body), credentials: 'include', signal: ctrl.signal,
    });
    return { status: r.status, body: await r.text() };
  } finally { clearTimeout(t); }
}"""


def main() -> None:
    captured = []  # 대시보드가 자발적으로 부른 cmg-api 호출

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(storage_state=STATE)
        page = ctx.new_page()

        def on_response(resp):
            u = resp.url
            if "cmg-api" in u or "marketing-reporting" in u or "report" in u.lower():
                try:
                    body = resp.text()
                except Exception:
                    body = "<no-body>"
                captured.append({"url": u, "status": resp.status, "body": body[:4000]})

        page.on("response", on_response)
        print(f"대시보드 로딩… (창이 뜹니다. 읽기전용)")
        page.goto(DASH, wait_until="domcontentloaded", timeout=40000)
        page.wait_for_timeout(3000)
        if fetcher._is_logged_out(page.url):
            print("aid 만료 — keycloak SSO 재발급 시도…")
            if fetcher._sso_refresh(page):
                fetcher._save_state(ctx, STATE)  # 회전된 aid 보존(페처와 동일, 무해)
                print("SSO 재발급 성공 — 대시보드 재진입")
                captured.clear()
                page.goto(DASH, wait_until="domcontentloaded", timeout=40000)
            else:
                print("SSO 재발급 실패 — keycloak 세션도 만료. 조사 중단.")
                ctx.close(); browser.close(); return
        page.wait_for_timeout(8000)  # Akamai 안정화 + 대시보드 위젯 로드 대기
        print(f"현재 URL: {page.url}")

        print("\n" + "=" * 70)
        print("【A】 대시보드가 자발적으로 호출한 광고 API (전체 광고비 위젯 포함)")
        print("=" * 70)
        for c in captured:
            print(f"\n--- {c['status']} {c['url'][:110]}")
            print(c["body"][:1500])

        # 【B】 report/cost — campaignType별 직접 호출(PA vs 다른 타입)
        print("\n" + "=" * 70)
        print("【B】 report/cost campaignType 비교 (오늘 running)")
        print("=" * 70)
        for ctype in ("PA", "ALL", "PB", "BRAND", "DISPLAY", "CPC", "CPM"):
            payload = {"parentNodes": [{"adNodeId": v, "campaignType": ctype} for v in VENDORS]}
            try:
                res = page.evaluate(_POST_JS,
                                    {"url": "https://advertising.coupang.com/marketing/cmg-api/report/cost",
                                     "body": payload})
            except Exception as e:
                print(f"  {ctype}: evaluate 실패 {str(e)[:60]}")
                continue
            print(f"  campaignType={ctype}: status={res.get('status')} body={res.get('body','')[:200]}")

        # 【C】 report/SALES — 전체 응답 필드 확인(DELIVERED_AD_COST 외 다른 비용 필드?)
        print("\n" + "=" * 70)
        print("【C】 report/SALES 전체 응답 필드 (날짜별 어떤 비용 키들이 오는가)")
        print("=" * 70)
        try:
            res = page.evaluate(_POST_JS,
                                {"url": "https://advertising.coupang.com/marketing/cmg-api/report/SALES",
                                 "body": {"start": START_MS, "end": END_MS}})
            print(f"status={res.get('status')}")
            try:
                data = json.loads(res.get("body") or "")
                result = data.get("result", data)
                # 첫 날짜 항목의 키 전부 + 전 기간 합계
                first = None
                total_delivered = 0
                for k, v in (result or {}).items():
                    if isinstance(v, dict):
                        if first is None:
                            first = (k, v)
                        total_delivered += int(v.get("DELIVERED_AD_COST") or 0)
                if first:
                    print(f"  첫 항목 키들: {list(first[1].keys())}")
                    print(f"  첫 항목 전체: {json.dumps(first[1], ensure_ascii=False)[:600]}")
                print(f"  Σ DELIVERED_AD_COST(최근7일): {total_delivered:,}")
            except Exception as e:
                print(f"  파싱 실패: {e}  body={res.get('body','')[:300]}")
        except Exception as e:
            print(f"  SALES 호출 실패: {str(e)[:80]}")

        ctx.close()  # storage_state 저장 안 함 — 페처 세션 불변
        browser.close()

    print("\n조사 완료 (push/저장 없음).")


if __name__ == "__main__":
    main()
