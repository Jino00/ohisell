#!/usr/bin/env python3
# ad_endpoint_capture.py — 1회용 정찰: 광고 콘솔의 "옵션별 광고비" 엔드포인트를 찾는다.
#
# 왜: report/SALES는 vendor 합계라 상품별(옵션별) 광고비를 못 준다. Jino가 수동
#     업로드하는 XLSX(또는 화면에 보이는 옵션별 표)가 어떤 API/다운로드 요청에서
#     나오는지 "추측 없이" 실측으로 확인한다(추정 금지 원칙).
#
# 동작: 기존 페처 로그인 세션(storage_state)을 그대로 써서 headful 창을 띄운다.
#       Jino가 평소처럼 광고비 보는 화면으로 가서 + 엑셀 다운로드를 누르는 동안,
#       advertising.coupang.com 으로 나간 모든 XHR/fetch 요청(URL·payload·응답)과
#       파일 다운로드를 ~/.ohisell_ad_capture.json 에 기록한다.
#
# 사용:  python3 ad_endpoint_capture.py        # 창이 뜨면 평소처럼 조회+엑셀 다운로드 → 창 닫기
#        (창을 닫거나 최대 대기시간 도달 시 캡처 파일 저장)
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

CONFIG_PATH = Path(os.path.expanduser("~/.ohisell_ad_fetcher.json"))
DEFAULT_STATE = os.path.expanduser("~/.ohisell_ad_state.json")
CAPTURE_PATH = Path(os.path.expanduser("~/.ohisell_ad_capture.json"))
DOWNLOAD_DIR = Path(os.path.expanduser("~/.ohisell_ad_capture_downloads"))
DASH_URL = "https://advertising.coupang.com/marketing/dashboard/sales?_cap_client=WING"
SSO_LOGIN_URL = (
    "https://advertising.coupang.com/user/login?_cap_client=WING"
    "&returnUrl=%2Fmarketing%2Fdashboard%2Fsales%3F_cap_client%3DWING"
)
MAX_WAIT_S = 900  # interactive 모드 최대 대기(창 닫으면 즉시 종료)
AUTO_CAPTURE_S = 12  # auto 모드: 대시보드 로드 후 lazy XHR까지 캡처할 시간
HOST = "advertising.coupang.com"
BODY_LIMIT = 20000  # 응답 본문 저장 상한(문자)


def _is_logged_out(url: str) -> bool:
    u = (url or "").lower()
    return any(x in u for x in ("login", "/auth", "sso", "signin"))


def _sso_refresh(page, timeout_s: int = 45) -> bool:
    """aid 만료 시 keycloak 세션으로 aid 재발급(비번 없음). 페처와 동일 로직."""
    try:
        page.goto("about:blank", timeout=10000)
    except Exception:
        pass
    try:
        page.goto(SSO_LOGIN_URL, wait_until="domcontentloaded", timeout=40000)
    except Exception as e:
        print(f"[i] SSO goto 경고(폴링으로 확인): {str(e)[:100]}")
    waited = 0
    while waited < timeout_s:
        page.wait_for_timeout(2000)
        waited += 2
        try:
            u = page.url
        except Exception:
            continue
        if "/marketing/dashboard" in u and not _is_logged_out(u):
            page.wait_for_timeout(1500)
            return True
    return False


def load_state() -> str:
    if not CONFIG_PATH.is_file():
        print(f"[!] 설정 파일 없음: {CONFIG_PATH} — 먼저 페처 login 필요", file=sys.stderr)
        sys.exit(2)
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    state = os.path.expanduser(cfg.get("state_file", DEFAULT_STATE))
    if not Path(state).is_file():
        print(f"[!] 세션 파일 없음: {state} — 먼저 'ad_cost_browser_fetcher.py login' 실행", file=sys.stderr)
        sys.exit(2)
    return state


def main() -> int:
    state = load_state()
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    captures: list[dict] = []
    downloads: list[dict] = []

    def on_response(resp) -> None:
        url = resp.url
        if HOST not in url:
            return
        req = resp.request
        # XHR/fetch/document만 (이미지·폰트·css 제외)
        if req.resource_type not in ("xhr", "fetch", "document", "other"):
            return
        entry: dict = {
            "ts": round(time.time(), 2),
            "method": req.method,
            "url": url,
            "resource_type": req.resource_type,
            "status": resp.status,
            "content_type": resp.headers.get("content-type", ""),
        }
        try:
            entry["post_data"] = req.post_data
        except Exception:
            entry["post_data"] = None
        ct = entry["content_type"].lower()
        # JSON/텍스트 응답만 본문 저장(바이너리·xlsx는 크기만)
        if any(x in ct for x in ("json", "text", "javascript")):
            try:
                body = resp.text()
                entry["body"] = body[:BODY_LIMIT]
                entry["body_truncated"] = len(body) > BODY_LIMIT
            except Exception as e:
                entry["body_error"] = str(e)[:120]
        else:
            try:
                entry["body_bytes"] = len(resp.body())
            except Exception:
                entry["body_bytes"] = None
        captures.append(entry)
        tag = "★" if any(k in url.lower() for k in ("report", "download", "export", "excel", "stat")) else " "
        print(f"  {tag} {req.method:4s} {resp.status} {ct[:30]:30s} {url[:110]}")

    def on_download(dl) -> None:
        try:
            fname = dl.suggested_filename
            dest = DOWNLOAD_DIR / fname
            dl.save_as(str(dest))
            rec = {"url": dl.url, "filename": fname, "saved_to": str(dest)}
            downloads.append(rec)
            print(f"  ⬇  다운로드 캡처: {fname}  ({dl.url[:100]})")
        except Exception as e:
            print(f"  ⬇  다운로드 캡처 실패: {e}")

    # auto(기본): SSO 자동 재발급 후 대시보드 API를 무인 캡처. interactive: 사람이 직접 탐색.
    mode = "interactive" if (len(sys.argv) > 1 and sys.argv[1] == "interactive") else "auto"
    nav_links: list[dict] = []
    print("=" * 78)
    print(f"광고 엔드포인트 정찰 — 모드: {mode}")
    if mode == "interactive":
        print("  창이 뜨면: 1) 옵션별 광고비 화면 이동 2) 엑셀 다운로드 3) 창 닫기")
    else:
        print("  무인: SSO 자동 재발급 → 대시보드 API + 메뉴 링크 캡처 (개입 불필요)")
    print("=" * 78)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(storage_state=state, accept_downloads=True)
        ctx.on("response", on_response)
        page = ctx.new_page()
        page.on("download", on_download)
        try:
            page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(2500)
        except Exception as e:
            print(f"[!] 대시보드 이동 경고(계속 진행): {e}")

        if mode == "auto":
            if _is_logged_out(page.url):
                print("[i] aid 만료 — keycloak 세션으로 SSO 자동 재발급 시도")
                if _sso_refresh(page):
                    print("[✓] SSO 재발급 성공 — 대시보드 재로드하며 캡처")
                    try:
                        page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
                    except Exception:
                        pass
                else:
                    print("[!] SSO 재발급 실패 — keycloak 세션도 만료. interactive 모드로 로그인 필요.")
            # 대시보드 lazy XHR(차트·표 API)까지 캡처
            page.wait_for_timeout(AUTO_CAPTURE_S * 1000)
            # 콘솔 좌측/상단 메뉴 링크 덤프 → 옵션별 '보고서' 페이지 URL 발견용
            try:
                nav_links = page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(e => ({text:(e.innerText||'').trim().slice(0,40), href:e.href}))"
                    ".filter(x => x.href.includes('advertising.coupang.com') && x.text)",
                )
            except Exception as e:
                print(f"[i] 링크 덤프 실패: {str(e)[:80]}")
        else:
            # interactive: 사람이 로그인→옵션화면→엑셀다운로드. wait_for_timeout이 이벤트를
            # 펌프(캡처 누락 방지)하고, 창이 닫히면 예외를 던져 확실히 종료된다. 매 주기마다
            # 인증 상태면 세션을 디스크에 저장(데몬 복구) + 캡처를 flush(중도 중단에도 보존).
            def _flush_capture() -> None:
                out = {"captured_at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "interactive",
                       "nav_links": nav_links, "requests": captures, "downloads": downloads}
                try:
                    CAPTURE_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass

            saved_state = False
            elapsed = 0
            while elapsed < MAX_WAIT_S:
                try:
                    page.wait_for_timeout(3000)  # 이벤트 펌프 + 창 닫히면 예외
                except Exception:
                    print("[i] 브라우저 창 닫힘 감지 — 저장하고 종료")
                    break
                elapsed += 3
                if not browser.is_connected():
                    print("[i] 브라우저 연결 끊김 — 저장하고 종료")
                    break
                try:
                    cur = page.url
                except Exception:
                    break
                # 인증되면(로그인 페이지 아님) 세션을 디스크에 저장 → 데몬 복구. 매 주기 갱신.
                if cur and not _is_logged_out(cur):
                    try:
                        ctx.storage_state(path=state)
                        os.chmod(state, 0o600)
                        if not saved_state:
                            print(f"[✓] 로그인 감지 — 세션 저장(데몬 복구): {state}")
                            saved_state = True
                    except Exception:
                        pass
                _flush_capture()  # 옵션 화면 이동·엑셀 다운로드 즉시 보존

        try:
            ctx.close()
            browser.close()
        except Exception:
            pass

    out = {"captured_at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": mode,
           "nav_links": nav_links, "requests": captures, "downloads": downloads}
    CAPTURE_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 78)
    print(f"[✓] 캡처 {len(captures)}건, 다운로드 {len(downloads)}건 저장: {CAPTURE_PATH}")
    print(f"    다운로드 파일: {DOWNLOAD_DIR}")
    print("    이 파일을 Claude에게 알려주면 옵션별 엔드포인트를 분석합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
