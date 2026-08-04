#!/usr/bin/env python3
# ad_settings_recon.py — 정찰(1회용): 쿠팡 광고 **설정(캠페인·입찰·키워드)** 엔드포인트를 찾는다.
#
# 왜: 「수정 사항」 화면(네이버)이 서는 다리는 두 개다 — ①우리 실집행 로그 ②설정 스냅샷 diff.
#     쿠팡은 성과(비용·노출·클릭·전환)만 수집하고 **설정 상태를 담은 테이블이 없다** → diff를 뜰
#     대상 자체가 없다. 스냅샷을 뜨려면 설정을 주는 API를 알아야 하는데, 그걸 **추측하지 않고**
#     실측으로 확인하는 것이 이 스크립트다(전역 CLAUDE.md 금지선: 추정 금지).
#
# 기존 ad_endpoint_capture.py와 무엇이 다른가:
#   - 그건 오픽스(storage_state + WING) 전용이고 "옵션별 광고비" 찾기용이었다.
#   - 오하이테크는 **CDP 프로필 + SUPPLIERHUB SSO**라 그 도구가 그대로 안 붙는다(D-7).
#   - ohitech_ad_fetcher.py의 `capture`는 `getVendorAdPerformance` 하나만 노리는 좁은 캡처다.
#   → 두 계정을 한 도구로 덮고, 설정 계열 URL을 자동으로 골라 표시한다.
#
# 사용:
#   python3 tools/ad_settings_recon.py ofix
#       → 창이 뜬다. 광고 관리(캠페인 목록 → 캠페인 하나 열기 → 광고그룹 → 키워드/입찰가)를
#         평소처럼 눌러 본 뒤 창을 닫는다.
#   python3 tools/ad_settings_recon.py ohitech
#       → 먼저 `python3 tools/ohitech_ad_fetcher.py chrome` 로 Chrome을 띄우고 로그인해 둘 것.
#         (이 스크립트는 **Chrome을 직접 띄우지 않는다** — 프로필 락·데몬 소유권을 건드리지 않기 위해서다.)
#
# 산출물: ~/.ohisell_ad_settings_recon_<mode>.json  (0600)
#   {captured_at, mode, nav_links[], requests[{method,url,status,content_type,post_data,body}]}
#
# ★안전: 읽기 전용이다. 광고 설정을 바꾸는 요청은 이 스크립트가 만들지 않는다(사람이 화면에서
#   "저장"을 누르지 않는 한 GET/조회만 흐른다). 인증 헤더는 **저장하지 않는다**(토큰 유출 방지).
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HOST = "advertising.coupang.com"
BODY_LIMIT = 40000   # 설정 응답은 목록이라 광고비 응답보다 길다
MAX_WAIT_S = 1800    # 사람이 화면을 도는 시간(창 닫으면 즉시 종료)

# 설정 계열로 보이는 URL 조각 — 화면에 ★로 표시해 눈에 띄게 한다(판단은 사람이).
_SETTINGS_HINTS = (
    "campaign", "adgroup", "ad-group", "keyword", "bid", "budget", "placement",
    "targeting", "creative", "product-ad", "manage", "setting", "status",
)

_OFIX = {
    "config": Path(os.path.expanduser("~/.ohisell_ad_fetcher.json")),
    "default_state": os.path.expanduser("~/.ohisell_ad_state.json"),
    "dash": "https://advertising.coupang.com/marketing/dashboard/sales?_cap_client=WING",
    # 페처(ad_endpoint_capture.py)와 동일한 SSO 재발급 경로 — 비번 없이 keycloak 세션으로 aid 재발급.
    "sso": ("https://advertising.coupang.com/user/login?_cap_client=WING"
            "&returnUrl=%2Fmarketing%2Fdashboard%2Fsales%3F_cap_client%3DWING"),
}
_OHITECH = {
    "config": Path(os.path.expanduser("~/.ohisell_ohitech_ad.json")),
    "dash": "https://advertising.coupang.com/marketing/dashboard/sales?_cap_client=SUPPLIERHUB",
    # ★오하이테크는 SUPPLIERHUB — WING으로 들어가면 오픽스 계정으로 붙는다(D-7).
    "sso": ("https://advertising.coupang.com/user/login?_cap_client=SUPPLIERHUB&_cap_market=KR"
            "&returnUrl=%2Fmarketing%2Fdashboard%2Fsales%3F_cap_client%3DSUPPLIERHUB"),
}


def _out_path(mode: str) -> Path:
    return Path(os.path.expanduser(f"~/.ohisell_ad_settings_recon_{mode}.json"))


def _is_logged_out(url: str) -> bool:
    u = (url or "").lower()
    return any(x in u for x in ("login", "/auth", "sso", "signin"))


def _sso_refresh(page, sso_url: str, timeout_s: int = 45) -> bool:
    """aid 만료 시 keycloak 세션으로 aid 재발급(비번 입력 없음). 페처와 동일 로직."""
    try:
        page.goto("about:blank", timeout=10000)
    except Exception:
        pass
    try:
        page.goto(sso_url, wait_until="domcontentloaded", timeout=40000)
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


def _load_cfg(path: Path) -> dict:
    if not path.is_file():
        print(f"[!] 설정 파일 없음: {path}", file=sys.stderr)
        sys.exit(2)
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    # 파이프로 넘길 때도 진행 상황이 실시간으로 보이게(기본 블록 버퍼링이면 끝날 때까지 침묵한다).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("ofix", "ohitech"):
        print("사용법: ad_settings_recon.py {ofix|ohitech}", file=sys.stderr)
        return 2

    captures: list[dict] = []
    nav_links: list[dict] = []
    out_path = _out_path(mode)

    def on_response(resp) -> None:
        url = resp.url
        if HOST not in url:
            return
        req = resp.request
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
            entry["post_data"] = req.post_data     # GraphQL은 여기 쿼리명이 들어온다
        except Exception:
            entry["post_data"] = None
        ct = entry["content_type"].lower()
        if any(x in ct for x in ("json", "text", "javascript")):
            try:
                body = resp.text()
                entry["body"] = body[:BODY_LIMIT]
                entry["body_truncated"] = len(body) > BODY_LIMIT
            except Exception as e:
                entry["body_error"] = str(e)[:120]
        captures.append(entry)
        hay = (url + " " + (entry["post_data"] or "")).lower()
        tag = "★" if any(k in hay for k in _SETTINGS_HINTS) else " "
        print(f"  {tag} {req.method:4s} {resp.status} {ct[:24]:24s} {url[:104]}")

    def flush() -> None:
        out = {
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode,
            "nav_links": nav_links,
            "requests": captures,
        }
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(out_path, 0o600)   # post_data/body에 계정 식별자가 섞일 수 있다
        except OSError:
            pass

    print("=" * 78)
    print(f"광고 설정 엔드포인트 정찰 — 계정: {mode}")
    print("  창에서: 광고 관리 → 캠페인 목록 → 캠페인 열기 → 광고그룹 → 키워드/입찰가 순으로 눌러 주세요.")
    print("  ★ 표시가 붙은 줄이 설정 계열 후보입니다. 다 돌았으면 창을 닫으면 됩니다.")
    print("=" * 78)

    with sync_playwright() as p:
        if mode == "ofix":
            cfg = _load_cfg(_OFIX["config"])
            state = os.path.expanduser(cfg.get("state_file", _OFIX["default_state"]))
            if not Path(state).is_file():
                print(f"[!] 세션 파일 없음: {state} — 'ad_cost_browser_fetcher.py login' 먼저", file=sys.stderr)
                return 2
            browser = p.chromium.launch(headless=False)
            ctx = browser.new_context(storage_state=state)
            owns_browser = True
        else:
            cfg = _load_cfg(_OHITECH["config"])
            port = int(cfg["cdp_port"])
            try:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            except Exception as e:
                print(f"[!] CDP({port}) 접속 실패: {str(e)[:120]}", file=sys.stderr)
                print("    먼저 `python3 tools/ohitech_ad_fetcher.py chrome` 실행 후 그 창에서 로그인하세요.",
                      file=sys.stderr)
                return 2
            if not browser.contexts:
                print("[!] CDP 컨텍스트 없음 — Chrome 창을 확인하세요.", file=sys.stderr)
                return 2
            ctx = browser.contexts[0]
            owns_browser = False   # ★남의 Chrome이다 — 절대 닫지 않는다(데몬 소유)

        acct = _OFIX if mode == "ofix" else _OHITECH
        ctx.on("response", on_response)
        page = ctx.new_page()
        try:
            page.goto(acct["dash"], wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(3000)
        except Exception as e:
            print(f"[!] 대시보드 이동 경고(계속 진행): {str(e)[:120]}")

        if _is_logged_out(page.url):
            print("[i] aid 만료 — keycloak 세션으로 SSO 자동 재발급 시도(비번 입력 없음)")
            if _sso_refresh(page, acct["sso"]):
                print("[✓] SSO 재발급 성공 — 이제 광고 관리 화면을 눌러 주세요.")
            else:
                print("[!] SSO 재발급 실패 — keycloak 세션도 만료됐습니다.")
                print("    창에서 Jino가 직접 로그인한 뒤 화면을 도세요(AI는 비밀번호를 입력하지 않습니다 — D-7).")

        elapsed = 0
        while elapsed < MAX_WAIT_S:
            try:
                page.wait_for_timeout(3000)   # 이벤트 펌프 + 창/탭 닫히면 예외
            except Exception:
                print("[i] 창 닫힘 감지 — 저장하고 종료")
                break
            elapsed += 3
            # 15초마다 저장 — 중도 중단(창 닫힘·SIGTERM·파이프 끊김)에도 캡처가 남게.
            if elapsed % 15 == 0:
                try:
                    nav_links = page.eval_on_selector_all(
                        "a[href]",
                        "els => els.map(e => ({text:(e.innerText||'').trim().slice(0,40), href:e.href}))"
                        f".filter(x => x.href.includes('{HOST}') && x.text)",
                    )
                except Exception:
                    pass
                flush()   # 중도 중단에도 보존

        flush()
        if owns_browser:
            try:
                browser.close()
            except Exception:
                pass

    settings_like = [c for c in captures
                     if any(k in (c["url"] + " " + (c.get("post_data") or "")).lower()
                            for k in _SETTINGS_HINTS)]
    print("=" * 78)
    print(f"캡처 {len(captures)}건 (설정 계열 후보 {len(settings_like)}건) → {out_path}")
    for c in settings_like[:40]:
        print(f"  ★ {c['method']:4s} {c['status']} {c['url'][:110]}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
