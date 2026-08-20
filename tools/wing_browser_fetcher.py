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
# 창 수명 (2026-07-27 개정 — 순수 버튼-only): 창이 뜨는 유일한 순간 = 버튼 요청을 claim한 직후 1회.
#   ★com.ohisell.wing-chrome(chrome-supervise, KeepAlive) 상주 supervisor는 폐기됐다 — Jino가
#     창을 닫아도 launchd가 되살리던 원인. 이제 poll 데몬이 fetch 때만 Chrome을 띄우고 닫는다.
#   ★RG 정산 새벽 일일 예약(rg_daily_hour)도 제거 — RG도 버튼 요청만 소비한다(마지막 자동 창 트리거).
#
# 설정 파일 ~/.ohisell_wing_fetcher.json (push용):
#   {"account_key":"COUPANG_WING1","prod_base_url":"https://sellc.ohitech.co.kr","ingest_token":"<AD_INGEST_TOKEN>","vs_days":7,
#    "vendor_id":"A01564720","rg_report_types":["WAREHOUSING_SHIPPING"],"rg_max_targets":3,
#    "rg_status_days":35,"rg_min_interval_s":3600}
#   (vs_days=판매분석 롤링 창 폭(기본 45, 미지정 시). vs_chunk_days=한 요청 최대 폭(기본 7).
#    ★config에 vs_days가 남아 있으면 config가 이긴다 — 7로 박혀 있으면 자가치유가 안 된다(지우거나 45로).
#    vendor_id·rg_* 는 'rg' 명령·poll 데몬 RG 분기용. account_key=WING1→vendor_id A01564720(오픽스),
#    WING2→A01029796(오하이테크). rg_daily_hour은 무시됨(폐기·버튼-only), rg_min_interval_s=RG 실행 최소간격.
#    rg_status_days=층1(계정 수수료) push 윈도우 **겸 층2 결손 조회 창**(기본 35, 백필 시 90으로 1회 실행).
#    rg_max_targets=한 회차에 받을 결손 주기 수 상한(기본 3).
#    ★죽은 키(설정에 남아 있어도 무시됨): rg_max_periods — 구 '최근 1주만' 상한이었고 그것이
#      영구 공백(WING1 층2 04-20~05-03 등)의 원인이었다. 지금은 결손 주도로 고른다.
#      rg_days — 층1/층2 통합 후 아무도 읽지 않는다.)
# 층2(옵션 엑셀) 자가치유(2026-07-27): 층1 push 후 prod에 결손 주기를 묻고(layer2-gaps) 결손만
#   최신 우선으로 rg_max_targets개까지 받는다. 조회 실패 시 기존 동작(최신 1주기)으로 안전 저하.
# 다계정 인스턴스 분리 env(D-7): OHISELL_WING_CONFIG(config)·OHISELL_WING_LOG(로그)·OHISELL_WING_LOCK(lock).
#   WING2(오하이테크) 인스턴스: tools/com.ohisell.wing2.plist 참조(env 3종 + 별도 state_file).
#   ★버튼 큐도 계정 차원이라 두 인스턴스가 경쟁하지 않는다 — refresh-status/claim은 cfg["account_key"]를
#     쿼리로 보내고 백엔드가 계정별 상태행으로 가른다(2026-07-27).
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

# 다계정 지원(D-7): OHISELL_WING_CONFIG로 config 경로 override → 오하이테크(WING2) 별도 인스턴스.
# 미설정 시 기존 경로(하위호환).
CONFIG_PATH = Path(os.path.expanduser(os.getenv("OHISELL_WING_CONFIG", "~/.ohisell_wing_fetcher.json")))
LOG_PATH = Path(os.path.expanduser(os.getenv("OHISELL_WING_LOG", "~/.ohisell_wing_fetcher.log")))
# ★다계정 인스턴스 분리(D-7): LOCK_PATH가 고정이면 WING2 인스턴스가 WING1과 같은 lock 파일을
#   놓고 상호 배제돼(한쪽 flock이 다른쪽을 통째로 건너뛰게 함) 서로의 실행을 죽인다. OHISELL_WING_LOCK
#   env로 분리 가능하게 한다(기본값 불변 — 기존 WING1 인스턴스 하위호환). state 경로는 cfg["state_file"]로
#   이미 인스턴스별 분리 가능(각 config 파일이 자기 state_file 지정), LOG_PATH는 OHISELL_WING_LOG로 분리.
LOCK_PATH = Path(os.path.expanduser(os.getenv("OHISELL_WING_LOCK", "~/.ohisell_wing_fetcher.lock")))
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
# CDP 모드에서 띄우는 브라우저 — ★반드시 실제 Google Chrome(Playwright 번들 Chromium 금지).
# Chrome for Testing 핑거프린트는 쿠팡 Cloudflare/Akamai에 걸린다(D-1 실측).
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_START_URL = "https://wing.coupang.com"
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
    // ★redirect:'manual'인 이유(2026-08-03 라이브 실측): 로그아웃되면 이 JSON API가 302로
    //   /login → /logout → helpseller.coupang.com(크로스 오리진)까지 튕긴다. 기본값
    //   redirect:'follow'면 그 체인 끝에서 fetch가 'TypeError: Failed to fetch'로 **터져서**,
    //   명백한 로그아웃이 '판정 불가(예외)'로 뭉개진다. manual이면 opaqueredirect로 조용히
    //   돌아와 "리다이렉트당했다 = 로그인 필요"를 확증할 수 있다.
    //   기존 성공 경로는 무영향 — 이 API들의 성공은 항상 200이고, 호출자는 이미 200만 성공으로
    //   친다(3xx는 원래도 실패였다). 달라지는 건 '왜 실패했는지 알게 된다'는 것뿐이다.
    const r = await fetch(location.origin + path, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
      credentials: 'include',
      redirect: 'manual',
      signal: ctrl.signal,
    });
    const redirected = (r.type === 'opaqueredirect') || (r.status >= 300 && r.status < 400);
    return { status: r.status, body: redirected ? '' : await r.text(),
             redirected, respType: r.type };
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


# ── 판매분석 요청 창 (2026-07-27 자가치유 수정) ──────────────────────────
# 왜 45일인가: 수집이 순수 버튼-only(사람이 누를 때만)라, 요청 창이 7일이면 버튼 간격이 7일을
#   넘는 순간 그 사이 날짜는 "영구 누락"된다(실측: prod coupang_vendor_summary_daily에 06-20~07-09
#   20일·07-17~07-19 3일 구멍). 그 구멍 때문에 revenue_canonical의 완결성 게이트
#   (days_with_data >= expected_days)가 거의 항상 False → Wing 정본화가 조용히 주문기반 폴백.
#   창을 45일 롤링으로 넓히면 45일 이내 공백은 다음 버튼 클릭에서 자동으로 메워진다(자가치유).
# 왜 청크인가: 45일 범위를 한 번에 받아본 근거가 없다. 로그(~/.ohisell_wing_fetcher.log) 전 이력이
#   7일 창뿐이고(2026-06-07~ 2026-07-26 전 회차), 쿠팡 문서·코드 어디에도 허용 범위 명시가 없다.
#   "아마 되겠지"로 넓히면 400 한 방에 수집 전체가 죽으므로, 라이브로 실증된 유일한 폭(7일)을
#   단위로 잘라 여러 번 요청한다. 각 청크 push는 기존 upsert 경로 그대로 — 멱등이라 중복 안전.
_VS_DEFAULT_DAYS = 45        # 롤링 창 전체 폭(자가치유 가능한 공백 한도)
_VS_DEFAULT_CHUNK_DAYS = 7   # 한 요청의 최대 폭 — 라이브로 검증된 값


def _cfg_int(cfg: dict, key: str, default: int) -> int:
    """config 정수값 — 없음·0·None·오타 문자열이면 기본값(설정 오타가 수집을 죽이지 않게)."""
    try:
        v = int(cfg.get(key) or default)
    except (TypeError, ValueError):
        log.warning("설정 %s 값이 정수가 아님(%r) — 기본값 %d 사용", key, cfg.get(key), default)
        return default
    return v if v > 0 else default


def _vs_windows(cfg: dict) -> list[tuple[object, object]]:
    """어제까지 롤링 vs_days일을 vs_chunk_days 단위로 자른 (start, end) 목록. 최신 청크가 맨 앞.

    최신 우선인 이유: 중간에 세션이 끊겨도 가장 중요한 최근 날짜는 이미 받아둔 상태가 된다.
    config의 vs_days가 있으면 그 값이 우선(기존 계약 유지).
    ★vs_chunk_days는 '줄이는' 방향만 허용한다(≤7). 실증된 폭을 config 한 줄로 넘겨버리면
      최신 청크부터 400으로 죽어 수집 전체가 멈춘다 — 넓히려면 라이브 증거와 함께 코드로(원칙 22).
    """
    days = _cfg_int(cfg, "vs_days", _VS_DEFAULT_DAYS)
    chunk = min(_cfg_int(cfg, "vs_chunk_days", _VS_DEFAULT_CHUNK_DAYS), _VS_DEFAULT_CHUNK_DAYS)
    end = datetime.now(KST).date() - timedelta(days=1)  # 어제(오늘은 sync 시차로 부정확 → 제외, D-3)
    first = end - timedelta(days=days - 1)
    windows: list[tuple[object, object]] = []
    cur_end = end
    while cur_end >= first:
        cur_start = max(first, cur_end - timedelta(days=chunk - 1))
        windows.append((cur_start, cur_end))
        cur_end = cur_start - timedelta(days=1)
    return windows


def _vs_payload(cfg: dict, window: tuple | None = None) -> dict:
    """vendor-summary body — 닫힌 과거일 윈도우(D-3, 어제까지). registrationTypes=3P+RG 전체.

    window=None이면 가장 최근 청크(기존 호출부 호환 — 로그인 감지 프로브 등이 쓰는 가벼운 1회분).
    YYYY-MM-DD 문자열(ref 18).
    """
    start, end = window if window else _vs_windows(cfg)[0]
    return {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "registrationTypes": ["NORMAL", "RFM"],
        "searchIds": [],
    }


# ── 옵션(vendorItem)축 — 판매분석 «일자×옵션» (D-CPP-36, 2026-08-10 라이브 정찰) ──────
# 요약축과 **같은 origin·같은 세션·같은 _POST_JSON_JS**를 쓴다. 새 fetch를 쓰면 Akamai 센서가
#   `TypeError: Failed to fetch`로 막는다(정찰 중 실측 — XSRF 헤더가 없으면 통과 못 한다).
VI_DETAIL_PATH = "/tenants/rfm-ss/api/business-insight/vi-detail-search"
# 한 요청당 옵션 수. 실측 응답의 paginationDetails가 totalPages를 주므로 순회는 그걸 따른다.
_VI_PAGE_SIZE = 50
# 옵션축을 받을 창 폭(일). ★요약축(45일)보다 좁은 이유: 옵션축은 **일자마다 따로 호출**해야
#   한다(응답이 startDate~endDate 창 집계라 일별 값이 없다). 45일이면 매 회차 45회 요청이 되고
#   그건 봇 감지 하에서 「조회만, 천천히」를 어긴다. 7일이면 회차당 7~10요청이고, 한 회차를
#   놓쳐도 다음 회차가 겹쳐 덮으므로 구멍이 안 남는다(요약축의 45일 자가치유와 같은 원리를
#   좁은 창으로 얻는다). 백필이 필요하면 config `vi_days`를 한 번 늘려 돌린다.
_VI_DEFAULT_DAYS = 7
# ★`vi_days`를 늘리려면 서버의 `CONSERVATION_WINDOW_DAYS`(scheduler_health.py)도 같이 늘려야
#   한다. 검사창이 수집창보다 넓으면 «아무도 다시 받지 않는 구간»에 보존식을 단언하게 되고,
#   그 불일치는 어떤 회차도 고칠 수 없다 → 수리 경로 없는 영구 빨강(적대 리뷰 P1-3).
# 하루당 페이지 상한 — 실측은 3페이지(146옵션/50)다. 응답이 거대한 totalPages를 줘도
#   여기서 멈춘다(봇 감지 하에서 요청 폭주가 계정을 잃는 길이다).
_VI_MAX_PAGES = 20
# 페이지 사이 지연(ms) — 요약축 과거창(500ms)과 같은 계열. 일자 사이엔 더 길게 둔다.
_VI_PAGE_DELAY_MS = 700
_VI_DAY_DELAY_MS = 1200


def _vi_days(cfg: dict) -> list[date]:
    """옵션축을 받을 날짜 목록 — 어제부터 과거로 vi_days일(닫힌 과거일만, 최신 우선).

    요약축과 같은 «닫힌 과거일» 규율(D-3): 오늘은 아직 확정이 아니라 받지 않는다.
    """
    days = _cfg_int(cfg, "vi_days", _VI_DEFAULT_DAYS)
    # 요약축 `_vs_windows`와 같은 기준일 계산(KST 어제) — 두 축의 창이 어긋나면 보존식이 헛돈다.
    yesterday = datetime.now(KST).date() - timedelta(days=1)
    return [yesterday - timedelta(days=i) for i in range(days)]


def _vi_payload(day: date, page_number: int) -> dict:
    """vi-detail-search body — 하루 단위(startDate == endDate).

    ★단일 일자 창이 요약축과 정확히 일치함을 실측했다(2026-08-10: 08-05 188,800=188,800,
      08-07 86,500=86,500). 그래서 일자별로 받아도 합이 요약축과 어긋나지 않는다.
    """
    iso = day.isoformat()
    return {
        "startDate": iso,
        "endDate": iso,
        "registrationTypes": ["NORMAL", "RFM"],
        "pageNumber": page_number,
        "pageSize": _VI_PAGE_SIZE,
        "sortBy": "GMV",
        "sortOrder": "DESC",
        "includeSoldVICount": True,
    }


def _parse_vi_page(body: str, day: date) -> tuple[list[dict], int] | None:
    """vi-detail-search 응답 1페이지 → (행 목록, totalPages). 실패 시 None.

    ★판매 0인 옵션도 그대로 담는다(GMV 0 행). 화면에 146개가 뜨는데 판매는 11개뿐이라
      «안 팔린 옵션»이 데이터에 없으면 「안 팔린 날 광고비」 같은 후속 질문을 못 한다.
    """
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or "vendorItems" not in data:
        return None
    pag = data.get("paginationDetails") or {}
    try:
        total_pages = int(pag.get("totalPages") or 1)
    except (TypeError, ValueError):
        total_pages = 1
    # ★상한(적대 리뷰 P2-3): 응답이 totalPages=5000을 주면 하루에 5000요청을 그대로 순회해
    #   「조회만, 천천히」를 정면으로 위반한다(실측 추정 58분/1일치). 실제 규모는 3페이지다.
    total_pages = min(max(total_pages, 1), _VI_MAX_PAGES)
    rows: list[dict] = []
    for it in data.get("vendorItems") or []:
        if not isinstance(it, dict):
            continue
        d = it.get("vendorItemDetails") or {}
        m = it.get("businessInsightsMetricsResponse") or {}
        vid = d.get("vendorItemId")
        rt = d.get("registrationType")
        if vid is None or rt not in ("NORMAL", "RFM"):
            continue  # 조인축·등록유형이 없으면 쓸 수 없다(스키마 방어)
        # ★행 단위 방어(적대 리뷰 P2-4): 쿠팡이 `totalGmv: "34,300"`처럼 타입을 바꾸면 종전엔
        #   ValueError가 `_fetch_vi_detail` 밖으로 나가 **그 회차에 이미 모은 날짜까지 전부**
        #   폐기됐다. 한 행의 형태 변화가 여러 날의 수집을 죽이면 안 된다.
        try:
            gmv = int(round(float(m.get("totalGmv") or 0)))
            units = int(round(float(m.get("totalUnitsSold") or 0)))
            orders = int(round(float(m.get("totalOrders") or 0)))
        except (TypeError, ValueError):
            log.warning("옵션 지표 파싱 실패 %s vid=%s — 이 행만 건너뜀(응답 형태 변경 의심)",
                        day, vid)
            continue
        rows.append({
            "date": day.isoformat(),
            "vendor_item_id": str(vid),
            "registration_type": rt,
            "gmv": gmv,
            "units_sold": units,
            "total_orders": orders,
            "item_name": d.get("itemName"),
            "product_id": (str(d["productId"]) if d.get("productId") is not None else None),
            # 원본 보존 — UV/PV/검색량은 이번 범위가 아니지만 재조회가 봇 감지 때문에 비싸다.
            "raw_metrics": m if isinstance(m, dict) else None,
        })
    return rows, total_pages


def _fetch_vi_detail(page, cfg: dict) -> list[dict]:
    """열린 page에서 옵션축을 일자별로 순회 수집. best-effort — 실패한 날은 건너뛴다.

    ★best-effort인 이유: 옵션축은 «있으면 더 좋은» 축이고, 한 날짜가 실패했다고 요약축 push까지
      죽이면 정본 매출 축 전체가 멈춘다(요약축은 이미 검증된 경로다). 다음 회차의 겹치는 창이
      실패한 날을 덮는다. 단 **조용히 넘어가지 않는다** — 각 실패를 WARNING으로 남긴다.
    """
    out: list[dict] = []
    for day in _vi_days(cfg):
        page_number, total_pages = 0, 1
        while page_number < total_pages:
            try:
                page.wait_for_timeout(_VI_PAGE_DELAY_MS)  # 연속 POST 완화(봇감지)
                res = page.evaluate(_POST_JSON_JS, [_vi_payload(day, page_number), VI_DETAIL_PATH])
            except Exception as e:  # noqa: BLE001 — 봇감지 순간차단
                log.warning("옵션축 fetch 오류 %s p%d: %s", day, page_number, str(e)[:80])
                break
            if not res or res.get("status") != 200:
                if _is_auth_expired(res):
                    log.warning("옵션축 fetch 중 세션 만료 신호 — 남은 날짜 중단(%s)", day)
                    return out
                log.warning("옵션축 fetch 실패 %s p%d status=%s",
                            day, page_number, res.get("status") if res else None)
                break
            parsed = _parse_vi_page(res.get("body") or "", day)
            if parsed is None:
                log.warning("옵션축 파싱 실패 %s p%d — 응답 형태 변경 의심: %s",
                            day, page_number, (res.get("body") or "")[:160])
                break
            rows, total_pages = parsed
            out.extend(rows)
            page_number += 1
        page.wait_for_timeout(_VI_DAY_DELAY_MS)
    return out


def _push_vendor_item_sales(cfg: dict, rows: list[dict], last_refresh: str | None) -> int:
    """옵션축 행 → prod ingest push. 0=성공, 1=실패(best-effort — 요약축 결과를 덮지 않는다)."""
    if not rows:
        log.warning("옵션축 push할 행 없음 — 건너뜀")
        return 1
    payload = [{**r, "last_refresh": last_refresh} for r in rows]
    try:
        pr = requests.post(
            cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/wing/vendor-item-sales/ingest",
            json={"account_key": cfg["account_key"], "rows": payload},
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            auth=_basic_auth(cfg),
            timeout=60,  # 요약축(20s)보다 길게 — 행 수가 수백~수천이다
        )
    except requests.RequestException as e:
        log.error("옵션축 push 네트워크 오류: %s", e)
        return 1
    if pr.status_code != 200:
        log.error("옵션축 push 실패 HTTP %s — %s", pr.status_code, pr.text[:200])
        return 1
    log.info("옵션축 push 성공: account=%s rows=%d → %s",
             cfg["account_key"], len(rows), pr.text[:160])
    return 0


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


def _save_state(context, path: str, *, cdp: bool = False) -> None:
    """storage_state(세션쿠키 포함)를 0600으로 저장.

    CDP 모드(P4, 2026-07-27 수정): 실제 쿠키는 Chrome 프로필 디렉터리가 보관하므로 여기서
    쓰는 파일은 세션 자체가 아니라 "로그인 완료" 마커일 뿐이다. ★이전에는 이 분기가 완전
    no-op이라 state_file이 전혀 생기지 않았고, `rg`/`run`의 존재 게이트(`Path(state).is_file()`)가
    매 회차 fail-fast했다(07-27 13:54 실사고 — WING1은 CDP 전환 이전 구식 파일이 우연히 남아
    통과했고, WING2는 수동 스텁으로 임시 우회 중이었다). 마커를 실제로 생성해 게이트를 통과시킨다.
    포맷은 legacy(비-CDP) 경로가 이 파일을 storage_state로 오독해도 깨지지 않도록 빈
    storage_state 형태(cookies/origins 빈 배열)에 메타를 얹는다.
    """
    if cdp:
        marker = {
            "cookies": [],
            "origins": [],
            "cdp_marker": True,
            "logged_in_at": datetime.now(KST).isoformat(),
        }
        try:
            Path(path).write_text(json.dumps(marker), encoding="utf-8")
            os.chmod(path, 0o600)
        except OSError as e:
            log.error("CDP 로그인 마커 저장 실패(%s): %s — 다음 실행도 게이트에 막힐 수 있음", path, e)
        return
    context.storage_state(path=path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def _cdp_mode(cfg: dict) -> bool:
    """설정에 cdp_port가 있으면 CDP(실제 Chrome) 모드."""
    return bool(cfg.get("cdp_port"))


# ════════════════════════════════════════════════════════════════════
# per-fetch Chrome 수명 (2026-07-27 — supervisor 폐기, 버튼 누를 때만 창)
# ════════════════════════════════════════════════════════════════════
# 왜: 상주 supervisor(launchd KeepAlive=true)는 Jino가 창을 닫으면 10~30초 뒤 Chrome을
#   되살렸다(세션 보온의 대가). 버튼-only 모델에서 창은 "버튼 누른 그 순간 1회"만 떠야 하므로,
#   poll 데몬이 요청을 claim한 뒤 스스로 Chrome을 띄우고 작업이 끝나면 닫는다.
# ★소유권 규칙: 내가 띄운 Chrome만 내가 닫는다. 이미 떠 있던 Chrome(사람이 로그인하려고
#   띄운 창 등)은 adopt만 하고 절대 닫지 않는다.
#
# ── 상주 모드(설정 `chrome_resident: true`, 2026-08-03) ──────────────────────────
# 왜 필요한가(실측): Wing 로그인은 `JSESSIONID`(wing.coupang.com) **세션 쿠키**에 얹혀 있고
#   xauth엔 무언 재발급용 SSO 쿠키(KEYCLOAK_IDENTITY 등)가 **없다**. 즉 Chrome을 닫으면
#   로그인은 반드시 사라진다 — 두 계정 모두. per-fetch 수명에서는 회차마다 로그아웃으로 뜨고,
#   실제로 2026-08-03 WING1은 fresh launch 4회차 전부 사람이 창에서 로그인해 통과했다
#   (12:43:27→12:44:13, 12:55:43→12:56:05, 13:06:29→13:07:03). 로그인 횟수를 정하는 건
#   세션 수명이 아니라 **Chrome 수명**이다.
# 어떻게: 내가 띄운 창을 작업 후 닫지 않는다. 다음 회차는 그 창을 adopt하고 세션이 이어진다.
# ★사람이 닫은 창은 즉시 되살리지 않는다 — 07-27에 상주 supervisor(launchd KeepAlive)를
#   폐기한 사유가 바로 "닫으면 10~30초 뒤 되살아난다"였다. 창이 새로 뜨는 순간은 예나 지금이나
#   '갱신 버튼을 누른 직후' 하나뿐이고, 달라진 것은 그 창을 닫지 않는다는 것뿐이다.
#   그래서 상주는 별도 KeepAlive 잡이 아니라 이 소유권 규칙 안에 산다(프로필 기동을 두 주체가
#   다투지 않는다). 되살아난 Chrome은 항상 로그아웃 상태이므로 로그인 안내가 반드시 뜬다
#   — 그 안내를 침묵시키던 결함이 `_rg_off_origin`이었다(같은 날 수정).
# (_cdp_alive·_profile_chrome_alive는 파일 하단 정의 — 호출 시점에 해석된다.)
def _port_owner_foreign(port: int, profile: str, allow_unverified: bool = False) -> bool:
    """CDP 포트를 LISTEN 중인 프로세스가 '우리 프로필의 Chrome'임을 **확인하지 못하면** True(=adopt 거부).

    왜(codex R1 P1#3): `/json/version` 200만 보고 adopt하면, 같은 포트에 뜬 무관한 Chrome
    (다른 계정 프로필·다른 자동화 도구)의 컨텍스트로 수집해 **남의 vendor 데이터를 우리
    account_key로 적재**할 수 있다. 포트는 설정값이라 다계정 인스턴스가 포트를 안 바꾸면 실제로 겹친다.

    판정 방식 = **PID 동일성**(codex R4): LISTEN PID == 우리 프로필의 SingletonLock PID인지만 본다.
    cmdline 문자열 대조는 쓰지 않는다 — macOS `ps -o command=`는 argv를 공백으로 flatten하므로
    공백을 품은 인자 하나(`"https://x/a --user-data-dir=<우리경로>"`)와 진짜 인자 두 개를
    구분할 수 없다(정규식 경계로는 원리적으로 해소 불가). PID 비교엔 그 모호성이 없다.

    왜 fail-closed(codex R2): 우리가 adopt할 정당한 창은 수동 `chrome` 커맨드든 per-fetch 기동이든
    전부 `_chrome_argv`로 우리 프로필에 뜬다 → SingletonLock PID가 항상 존재한다. 따라서
    '확인 불가'는 정상 케이스가 아니라 남의 Chrome 신호다. 오적재는 조용하고 되돌리기 어렵지만
    거부는 로그·알림으로 시끄럽다. 현장 오판 시 설정 `adopt_unverified_chrome:true`로 옛 동작 복귀.
    """
    verdict = "adopt(설정 허용)" if allow_unverified else "adopt 거부"
    owner_pid = _profile_owner_pid(profile)
    if owner_pid is None:
        log.warning("프로필(%s) SingletonLock 없음 — CDP %d 소유자 확인 불가 → %s", profile, port, verdict)
        return not allow_unverified
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:  # noqa: BLE001
        log.warning("CDP %d 소유자 확인 불가(lsof 실행 실패) — %s", port, verdict)
        return not allow_unverified
    pids = {p for p in out.split() if p.isdigit()}
    if not pids:
        log.warning("CDP %d LISTEN PID를 찾지 못함 — %s", port, verdict)
        return not allow_unverified
    if str(owner_pid) in pids:
        return False            # LISTEN 프로세스 = 우리 프로필을 점유한 Chrome → adopt 정당
    log.warning("CDP %d LISTEN PID %s가 우리 프로필 점유 PID %d와 불일치 — %s",
                port, sorted(pids), owner_pid, verdict)
    return not allow_unverified


@contextlib.contextmanager
def _profile_launch_lock(profile: str, timeout_s: int = 90):
    """프로필 단위 기동 직렬화 — '점검 → stale lock 청소 → launch → CDP 대기' 전 구간을 감싼다.

    왜(codex R1 P1#2): 페처별 flock은 *작업* 단위라 같은 프로필을 쓰는 다른 커맨드(login/chrome)나
    LOCK 파일이 분리된 다계정 인스턴스(OHISELL_WING_LOCK, D-7)와는 배타되지 않는다. 둘 다
    "비어 있음"을 보고 Singleton 파일을 지운 뒤 Chrome을 이중 기동하면 프로필·쿠키 DB가 손상된다.
    lock 경로는 프로필에서 결정되므로 프로세스·페처가 달라도 같은 파일을 놓고 배타된다.
    (프로필 디렉터리 *안*에 두지 않는다 — Chrome이 지우거나 프로필 재생성 시 사라진다.)
    """
    lock_path = os.path.expanduser(profile).rstrip("/") + ".launchlock"
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    acquired = False
    try:
        waited = 0
        while waited < timeout_s:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                time.sleep(1)
                waited += 1
        if not acquired:
            log.error("Chrome 기동 lock 대기 초과(%ds) — 다른 프로세스가 같은 프로필 기동 중: %s",
                      timeout_s, lock_path)
            raise RuntimeError("chrome_launch_lock_timeout")
        yield
    finally:
        if acquired:
            with contextlib.suppress(Exception):
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _chrome_argv(cfg: dict) -> list[str]:
    """Chrome 기동 커맨드라인 — 수동 'chrome' 커맨드와 per-fetch 기동이 반드시 동일해야 한다
    (포트·프로필·플래그가 갈리면 세션/핑거프린트가 갈린다)."""
    port = int(cfg.get("cdp_port", 9222))
    profile = os.path.expanduser(cfg.get("cdp_profile", "~/.ohisell_wing_chrome"))
    return [
        CHROME_BIN,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        CHROME_START_URL,
    ]


def _launch_chrome(cfg: dict):
    """실제 Chrome을 백그라운드 자식으로 기동. 반환 Popen(실패 시 None). stale lock 먼저 청소."""
    if not os.path.exists(CHROME_BIN):
        log.error("Chrome을 찾을 수 없습니다: %s", CHROME_BIN)
        return None
    profile = os.path.expanduser(cfg.get("cdp_profile", "~/.ohisell_wing_chrome"))
    # 살아있는 Chrome 없음을 확인한 뒤에만 호출된다 → 크래시 잔재 lock 안전 제거.
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock = os.path.join(profile, name)
        with contextlib.suppress(OSError):
            if os.path.islink(lock) or os.path.exists(lock):
                os.unlink(lock)
    os.makedirs(profile, exist_ok=True)
    return subprocess.Popen(
        _chrome_argv(cfg),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_cdp(port: int, timeout_s: int = 60) -> bool:
    """Chrome 기동 후 CDP가 응답할 때까지 대기(1초 간격). 콜드 스타트 여유."""
    waited = 0
    while waited < timeout_s:
        if _cdp_alive(port):
            return True
        time.sleep(1)
        waited += 1
    return False


def _close_chrome(proc, grace_s: int = 15) -> None:
    """내가 띄운 Chrome 종료(SIGTERM → grace_s 대기 → SIGKILL).

    SIGTERM은 Chrome의 정상 종료 경로다(핸들러 보유 → 세션·쿠키 flush). SIGKILL은 무응답 시
    최후수단이며, 그때 남는 Singleton 잔재는 다음 기동의 stale lock 청소가 처리한다.
    """
    log.info("작업 완료 — 내가 띄운 Chrome(PID %s) 종료.", getattr(proc, "pid", "?"))
    with contextlib.suppress(Exception):
        proc.terminate()
    try:
        proc.wait(timeout=grace_s)
    except Exception:  # noqa: BLE001 — SIGTERM 무응답
        log.warning("Chrome SIGTERM 무응답 — SIGKILL.")
        with contextlib.suppress(Exception):
            proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)


# 시그널/비정상 종료 시 회수할 '내가 띄운' Chrome 목록(_owned_chrome이 등록·해제).
_LIVE_OWNERS: list = []


def _cleanup_owned_chromes(_signum=None, _frame=None) -> None:
    """SIGTERM/SIGHUP·프로세스 종료 시 내가 띄운 Chrome 회수.

    왜(codex R1 P1#4): 파이썬 기본 SIGTERM 처리는 예외를 던지지 않고 즉시 죽으므로
    `_owned_chrome`의 finally가 **실행되지 않는다.** 설치 스크립트는 배포마다 poll 데몬을
    `launchctl bootout`하므로, fetch 중 재설치하면 데몬만 죽고 Chrome이 남는다 → 다음 데몬은
    그 Chrome을 adopt(=닫을 책임 없음)해 버튼-only인데도 창이 영구 잔류한다.
    keep_open 창은 사람이 로그인 중일 수 있으므로 평소 규칙대로 남긴다.
    ★상주(resident) 창도 남긴다 — 배포마다 데몬을 bootout 하는데 여기서 닫으면 **재배포가
      곧 로그아웃**이 된다(Wing 로그인은 JSESSIONID 세션 쿠키라 Chrome과 함께 죽는다,
      2026-08-03 실측). 남겨 두면 새 데몬이 그대로 adopt해 세션이 이어진다.
    """
    if _signum is not None:
        # 재진입 차단(codex R2): 정리 도중 두 번째 시그널이 들어오면 handler가 겹쳐 돈다.
        for _s in (signal.SIGTERM, signal.SIGHUP):
            with contextlib.suppress(Exception):
                signal.signal(_s, signal.SIG_IGN)
    for owner in list(_LIVE_OWNERS):
        if owner.should_close:
            with contextlib.suppress(Exception):
                _close_chrome(owner.proc, grace_s=5)   # 시그널 경로는 짧게
            owner.proc = None
    if _signum is not None:
        os._exit(128 + int(_signum))


def _install_signal_cleanup() -> None:
    """SIGTERM/SIGHUP 회수 핸들러 + 정상 종료 경로(atexit) 등록. main()에서 1회 호출."""
    import atexit

    for _sig in (signal.SIGTERM, signal.SIGHUP):
        with contextlib.suppress(Exception):
            signal.signal(_sig, _cleanup_owned_chromes)
    # KeyboardInterrupt·sys.exit 등 정상 종료 경로(핸들러가 안 도는 경우) 커버.
    atexit.register(_cleanup_owned_chromes)


class _ChromeOwner:
    """이번 작업 동안의 Chrome 소유권. proc=None이면 adopt(남의 창) → 절대 닫지 않는다.

    keep_open=True면 내가 띄운 창이라도 남긴다(세션 만료 → 사람이 그 창에서 로그인해야 하는 경우).
    resident=True면 상주 모드라 **작업이 끝나도 닫지 않는다**(설정 chrome_resident, 아래 주석).
      keep_open과 따로 두는 이유: 호출자는 로그인 성공 후 keep_open을 False로 되돌리는데
      (_do_rg_run), 상주를 keep_open에 얹으면 그 한 줄이 상주를 조용히 해제한다.
    """

    def __init__(self) -> None:
        self.proc = None
        self.keep_open = False
        self.resident = False

    @property
    def owned(self) -> bool:
        return self.proc is not None

    @property
    def should_close(self) -> bool:
        return self.owned and not self.keep_open and not self.resident


@contextlib.contextmanager
def _owned_chrome(cfg: dict, owner: "_ChromeOwner | None" = None):
    """Chrome 가용 보장 컨텍스트 — 없으면 띄우고(소유), 있으면 adopt. 종료 시 소유분만 닫는다.

    yield owner(_ChromeOwner). 호출자는 owner.keep_open=True로 창 유지를 요청할 수 있다.
    기동 실패·프로필 점유 충돌은 RuntimeError(호출자의 기존 브라우저 오류 경로가 처리).
    """
    owner = owner if owner is not None else _ChromeOwner()
    port = int(cfg.get("cdp_port", 9222))
    profile = os.path.expanduser(cfg.get("cdp_profile", "~/.ohisell_wing_chrome"))
    # 점검~기동~CDP대기 전 구간을 프로필 lock으로 직렬화(이중 기동=프로필 손상 차단).
    with _profile_launch_lock(profile, int(cfg.get("chrome_launch_lock_timeout_s", 90))):
        if _cdp_alive(port):
            if _port_owner_foreign(port, profile, bool(cfg.get("adopt_unverified_chrome", False))):
                log.error("CDP %d를 다른 프로필의 Chrome이 점유 — adopt 거부(세션 오적재 방지).", port)
                raise RuntimeError("chrome_port_foreign")
            log.info("기존 Chrome(CDP %d) 감지 — adopt(내가 닫지 않음).", port)
        elif _profile_chrome_alive(profile):
            # CDP는 죽었는데 프로필은 점유 중 = 다른 포트/수동 Chrome. 중복 launch는 프로필 손상.
            log.error("프로필 점유 중이나 CDP(%d) 미응답 — 수동 Chrome 종료 필요(%s).", port, profile)
            raise RuntimeError("chrome_profile_busy")
        else:
            proc = _launch_chrome(cfg)
            if proc is None:
                raise RuntimeError("chrome_launch_failed")
            owner.proc = proc
            owner.resident = bool(cfg.get("chrome_resident", False))
            _LIVE_OWNERS.append(owner)   # 시그널 종료 시 회수 대상
            log.info("Chrome 기동(PID %d, CDP %d) — %s.", proc.pid, port,
                     "상주(작업 후 닫지 않음)" if owner.resident else "작업 후 닫음")
            if not _wait_cdp(port):
                log.error("Chrome CDP(%d) 기동 대기 초과 — 종료.", port)
                _close_chrome(proc)
                owner.proc = None
                with contextlib.suppress(ValueError):
                    _LIVE_OWNERS.remove(owner)
                raise RuntimeError("cdp_not_ready")
    try:
        yield owner
    finally:
        try:
            if owner.owned:
                if owner.resident:
                    log.info("상주 모드 — Chrome(PID %s) 유지(다음 회차가 adopt).",
                             getattr(owner.proc, "pid", "?"))
                elif owner.keep_open:
                    log.info("로그인 대기 위해 Chrome 창 유지 — 로그인 후 '갱신' 버튼을 다시 누르세요.")
                else:
                    _close_chrome(owner.proc)
        finally:
            with contextlib.suppress(ValueError):
                _LIVE_OWNERS.remove(owner)


@contextlib.contextmanager
def _chrome(p, cfg: dict, state: str, *, load_state: bool = True, owner: "_ChromeOwner | None" = None):
    """브라우저 세션 컨텍스트 매니저.

    CDP 모드(cdp_port 설정 시): 실제 Chrome을 (없으면) 띄워 연결 → Akamai 핑거프린트 없음.
      내가 띄운 Chrome은 컨텍스트 종료 시 닫는다(2026-07-27, supervisor 폐기). 호출자가 owner를
      넘기면 owner.keep_open=True로 창 유지를 요청할 수 있다(세션 만료 → 사람 로그인 대기).
    레거시 모드: Playwright Chromium 새로 실행(기존 동작 — 창 수명은 원래부터 per-fetch).

    yield (page, ctx, save_fn):
        save_fn() — CDP 모드는 no-op, 레거시는 storage_state 저장.
    """
    cdp = _cdp_mode(cfg)
    if cdp:
        port = int(cfg.get("cdp_port", 9222))
        with _owned_chrome(cfg, owner):
            browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
            ctx = browser.contexts[0] if browser.contexts else browser.new_context(user_agent=_UA)
            page = ctx.new_page()
            try:
                yield page, ctx, lambda: None  # Chrome이 세션 보관 → save no-op
            finally:
                with contextlib.suppress(Exception):
                    page.close()
                with contextlib.suppress(Exception):
                    browser.close()  # disconnect only — Chrome 종료는 _owned_chrome이 판단
    else:
        browser = p.chromium.launch(headless=False)
        kw: dict = {"user_agent": _UA}
        if load_state and os.path.exists(state):
            kw["storage_state"] = state
        ctx = browser.new_context(**kw)
        page = ctx.new_page()
        try:
            yield page, ctx, lambda: _save_state(ctx, state)
        finally:
            with contextlib.suppress(Exception):
                ctx.close()
            with contextlib.suppress(Exception):
                browser.close()


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


def _fetch_vendor_summary(page, cfg: dict, retries: int = 2, window: tuple | None = None):
    """판매분석 페이지 이동 후 same-origin vendor-summary fetch. 반환: res dict 또는 None(로그아웃).

    window=None이면 가장 최근 청크(_vs_payload 기본).
    """
    page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
    page.wait_for_timeout(3000)  # Cloudflare/Akamai JS 챌린지·세션 안정화
    if _is_logged_out(page.url):
        return None
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return page.evaluate(_POST_JSON_JS, [_vs_payload(cfg, window), VENDOR_SUMMARY_PATH])
        except Exception as e:  # noqa: BLE001 — 봇감지 순간차단 재시도
            last_exc = e
            if attempt < retries:
                log.warning("fetch 일시 실패(%d/%d) 재시도: %s", attempt, retries, str(e)[:80])
                page.wait_for_timeout(2500)
    raise last_exc


def _fetch_older_windows(page, cfg: dict, windows: list | None = None) -> list[str]:
    """최신 청크 성공 후, 나머지 과거 청크들을 같은 페이지에서 이어서 fetch. 반환: 성공한 body 목록.

    windows=받을 과거 창 목록(호출자가 한 번 계산해 넘긴다 — 실행 중 KST 자정을 넘겨도 기준일이
    흔들리지 않게. None이면 지금 기준으로 계산한 창의 과거분).
    best-effort — 한 청크가 실패해도 나머지는 계속(그 날짜는 다음 버튼 클릭에서 다시 시도된다).
    세션 만료 신호가 뜨면 더 눌러봐야 소용없으므로 중단한다.
    """
    bodies: list[str] = []
    for start, end in (windows if windows is not None else _vs_windows(cfg)[1:]):
        try:
            page.wait_for_timeout(500)  # 연속 POST 완화(봇감지)
            res = page.evaluate(_POST_JSON_JS, [_vs_payload(cfg, (start, end)), VENDOR_SUMMARY_PATH])
        except Exception as e:  # noqa: BLE001 — 한 청크 실패가 전체를 죽이지 않는다
            log.warning("과거창 fetch 오류 %s~%s: %s", start, end, str(e)[:80])
            continue
        if _is_success(res):
            bodies.append(res.get("body") or "")
            continue
        if _is_auth_expired(res):
            log.warning("과거창 fetch 중 세션 만료 신호 — 남은 창 중단(%s~%s)", start, end)
            break
        log.warning("과거창 fetch 실패 %s~%s status=%s", start, end, res.get("status") if res else None)
    return bodies


def _merge_summary(base: dict, other: dict) -> dict:
    """과거창 요약을 base에 합친다(날짜 단위 합집합). 같은 날짜가 겹치면 나중 값으로 덮어쓴다.

    같은 날짜를 두 청크가 담는 일은 없지만(창이 겹치지 않음), 겹쳐도 합산이 아닌 대체라 이중계상 없음.
    """
    for d, by_type in (other.get("dates") or {}).items():
        base.setdefault("dates", {})[d] = by_type
    # 합계는 병합된 dates에서 재계산(원 응답의 summaryMetrics는 청크별이라 못 쓴다).
    gmv_3p = gmv_rg = 0.0
    for by_type in base.get("dates", {}).values():
        gmv_3p += float(by_type.get("NORMAL", {}).get("gmv", 0) or 0)
        gmv_rg += float(by_type.get("RFM", {}).get("gmv", 0) or 0)
    base["gmv_3p"] = gmv_3p
    base["gmv_rg"] = gmv_rg
    base["total_gmv"] = gmv_3p + gmv_rg
    return base


def _observed_span(summ: dict, cfg: dict) -> dict:
    """로그용 — 실제로 받아온 날짜의 span(요청한 창이 아니라 '받은 것'을 찍는다, 원칙 22)."""
    dates = sorted(summ.get("dates") or {})
    if not dates:
        return _vs_payload(cfg)
    return {"startDate": dates[0], "endDate": dates[-1]}


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
            auth=_basic_auth(cfg),
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


def _login_wait_loop(page, ctx, cfg: dict, state: str, wait_secs: int, *, cdp: bool = False,
                     window: tuple | None = None):
    """열린 page에서 사용자 로그인을 자동 감지(vendor-summary 200). 성공 시 state 저장 후 res 반환.

    window=프로브가 쓸 최신 청크(호출자가 실행 시작 시점에 계산해 넘긴다). 로그인 대기 중 KST 자정을
    넘겨도 이 res와 뒤이어 받는 과거창이 어긋나지 않게 — 고정된 창을 쓴다(넘어간 하루는 다음 회차 몫).

    ★codex 1R[P2](P4 후속): `_save_state`의 CDP 마커 저장이 실패(디스크 풀·권한 등 OSError)해도
    호출부가 그걸 무시하고 성공으로 리턴하면 "로그인 감지·세션 저장 완료" 로그가 거짓이 되고,
    이 함수가 고치려는 바로 그 게이트(P4)를 다음 회차에 다시 막는다(green-while-dead와 동일 계열
    실수). 저장 직후 실제로 파일이 생겼는지 확인하고, 안 생겼으면 성공으로 리턴하지 않고 남은
    시간 동안 재시도한다(로그인 자체는 이미 됐으니 다음 폴에서 같은 세션으로 저장만 재시도).
    """
    waited = 0
    while waited < wait_secs:
        try:
            page.wait_for_timeout(5000)
            waited += 5
            if _is_logged_out(page.url):
                continue
            res = page.evaluate(_POST_JSON_JS, [_vs_payload(cfg, window), VENDOR_SUMMARY_PATH])
        except Exception as e:  # noqa: BLE001
            if "closed" in str(e).lower():
                log.error("브라우저 창이 닫혔습니다 — 로그인 미완료(창을 닫지 말고 로그인만 하세요).")
                return None
            continue
        if _is_success(res):
            _save_state(ctx, state, cdp=cdp)
            if os.path.exists(state):
                return res
            log.error("로그인은 확인됐으나 세션 마커 저장 실패(%s) — 재시도 대기", state)
    return None


def _rg_applicable(cfg: dict) -> bool:
    """이 인스턴스가 RG(정산) 레인을 실제로 쓰는가 — `_do_rg_run`의 전제조건과 같은 판별.

    RG는 vendor_id(정산주기 열거 키)와 push 3종이 모두 있어야 돌아간다(_do_rg_run 진입부에서
    둘 중 하나라도 없으면 rc=2로 fail-fast). 둘이 없는 인스턴스는 RG를 아예 안 쓰므로
    데스크톱(wing.coupang.com) 세션을 요구할 이유가 없다 — 프로브를 걸면 "쓰지도 않는 레인"
    때문에 로그인이 실패로 보고된다.
    """
    return bool(str(cfg.get("vendor_id") or "").strip()) and _push_configured(cfg)


def cmd_login(cfg: dict, wait_secs: int = 600, rg_probe: bool = True) -> int:
    """로그인 세션 초기화 + 자동 감지(vendor-summary 200) → state 저장 + 첫 파싱.

    CDP 모드: Chrome(없으면 기동)의 새 탭에서 wing.coupang.com 열고 로그인 감지.
      사람이 조작하는 명령이므로 창은 남긴다(keep_open) — 세션은 Chrome 프로필이 보관.
    레거시 모드: Playwright Chromium 헤드풀 창(모바일 UA) 새로 실행.

    ★rg_probe(2026-07-27 라이브 실측): 판매분석(m-wing, 모바일)과 정산(wing.coupang.com,
      데스크톱 xauth SSO)은 **세션이 따로 논다**. 같은 Chrome에서 vendor-summary=200인데
      정산 goto는 xauth 로그인 페이지로 리다이렉트되고 status/api는 404인 상태가 실제로
      관측됐다. VS 프로브만으로 "로그인 완료"를 선언하면 그 침묵이 데스크톱 세션 만료를
      가리고, 직후 `rg`(login_wait_secs=0)가 세션 판정 실패로 fail-fast한다.
      → 창이 아직 열려 있는 동안 정산 세션까지 확인하고, 없으면 사람에게 마저 로그인시킨다
      (워밍 네비게이션으로는 해결 불가 — 사람 재로그인만 가능).
      rg_probe=False는 데몬 VS 레인용(cmd_poll 주석 참조).
    ★판정은 **3값**이다(2026-08-03, 층2·진입 경로와 동일 계약). 종전엔 보수적 bool이라
      판정 불가(업스트림 500·깨진 JSON·goto 예외)까지 "데스크톱 세션 만료"로 불러 **로그인해
      있는 사람에게 로그인을 시켰고**, 끝내 못 잡으면 멀쩡한 세션에 rc=5를 돌려줬다.
      로그아웃은 AUTH(오리진 이탈·리다이렉트·로그인 HTML)로 확증되지 UNKNOWN으로 오지
      않는다 — 그래서 UNKNOWN은 로그인을 요구하지 않고, 대신 "확인됨"이라고 주장하지도
      않는다. 진짜로 죽었다면 이어지는 `rg`가 자기 진입 프로브에서 AUTH로 잡는다.
    """
    state = os.path.expanduser(cfg["state_file"])
    cdp = _cdp_mode(cfg)
    owner = _ChromeOwner()
    owner.keep_open = True   # 로그인 창은 사람 것 — 자동으로 닫지 않는다
    mode_label = "Chrome(CDP)" if cdp else "Playwright Chromium(모바일 UA)"
    log.info("[login] %s 실행 — wing.coupang.com에 로그인하세요(자동 감지, 최대 %d초).", mode_label, wait_secs)
    res = None
    older_bodies: list[str] = []
    # ★레거시(비CDP) 모드는 프로브 대상이 아니다: cmd_login이 load_state=False로 fresh context를
    #   열기 때문에 사람이 반드시 실제 xauth SSO 로그인을 수행하고, 그 결과 두 호스트 쿠키가
    #   함께 fresh해진다. "기존 세션이 VS 프로브를 즉시 통과해 사람이 로그인을 건너뛴다"는
    #   마스킹 시나리오 자체가 원리적으로 생기지 않는다(=프로브가 잡을 것이 없다).
    probe_rg = bool(rg_probe and cdp and _rg_applicable(cfg))
    rg_verdict = _PROBE_OK   # 프로브 미수행이면 기존 동작 그대로(성공 취급)
    started = time.monotonic()
    windows = _vs_windows(cfg)   # 프로브·과거창이 같은 기준일을 쓰도록 1회만 계산(자정 경계)
    with sync_playwright() as p:
        with _chrome(p, cfg, state, load_state=False, owner=owner) as (page, ctx, _save):
            page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
            res = _login_wait_loop(page, ctx, cfg, state, wait_secs, cdp=cdp, window=windows[0])
            # 로그인 감지 프로브는 최신 청크 1회분이다. 여기서 멈추면 "로그인이 버튼 요청을
            # 소비한" 회차는 자가치유를 못 타고 7일만 push된다 → 로그인 성공 직후 과거창도 이어 받는다.
            if res is not None:
                older_bodies = _fetch_older_windows(page, cfg, windows[1:])
                if probe_rg:
                    # 창이 아직 열려 있는 이 블록 안에서만 가능하다 — 밖으로 나가면 사람이
                    # 이어서 로그인할 창이 없다. 프로브 자체가 터져도(창 닫힘 등) VS 결과
                    # 처리는 계속한다: 이미 받은 데이터를 버리면 버튼 요청 한 회차가 증발한다.
                    try:
                        page.goto(RG_DASH_URL, wait_until="domcontentloaded", timeout=40000)
                        page.wait_for_timeout(3500)   # Cloudflare/Akamai JS 챌린지 안정화
                        # 1차 실패 시 짧은 지연 후 1회 재확인 — 업스트림 500 blip을 로그아웃으로
                        #   승격하지 않기 위해서다(층2 루프와 같은 헬퍼·같은 판단기준).
                        rg_verdict = _rg_session_verdict_confirmed(page)
                        if rg_verdict == _PROBE_AUTH:
                            remaining = max(0, wait_secs - int(time.monotonic() - started))
                            log.info(
                                "판매분석(VS) 세션은 정상 — RG(정산) 데스크톱 세션 만료(로그아웃 확증). "
                                "같은 창에서 wing.coupang.com에 로그인하세요(자동 감지, 최대 %d초).",
                                remaining)
                            if _rg_login_wait(page, ctx, state, remaining, cdp=cdp):
                                rg_verdict = _PROBE_OK
                    except Exception as e:  # noqa: BLE001 — 프로브 실패는 VS 결과를 무효화하지 않는다
                        # 창 닫힘·네비게이션 실패는 '로그아웃 확증'이 아니라 판정 불가다.
                        log.error("RG(정산) 세션 프로브 실패(판정 불가로 접는다): %s", str(e)[:160])
                        # ★단 이미 AUTH를 확증한 뒤 로그인 대기 중에 터진 것이라면 판정을 지우지
                        #   않는다 — 로그아웃은 여전히 사실이고 로그인은 완료되지 않았다.
                        if rg_verdict != _PROBE_AUTH:
                            rg_verdict = _PROBE_UNKNOWN
    if res is None:
        log.error("제한 시간 내 로그인 감지 실패 — 다시 시도하세요.")
        return RC_LOGIN_REQUIRED   # ★로그인 자체가 안 된 경우만 '로그인 필요'(codex 1R[P2])
    log.info("로그인 감지·세션 저장 완료: %s%s", state,
             " (RG 정산 데스크톱 세션도 확인됨)" if (probe_rg and rg_verdict == _PROBE_OK) else "")
    if probe_rg and rg_verdict == _PROBE_UNKNOWN:
        # ★"확인됨"도 "만료"도 아니다 — 둘 다 주장하지 않는 게 이 분기의 전부다.
        #   0을 돌려주는 근거: 로그인을 요구할 근거(AUTH)가 없고, 정말 죽었다면 `rg` 진입
        #   프로브가 AUTH로 잡아 창을 띄운다. 여기서 rc=5를 내면 멀쩡한 세션에 대고
        #   사람에게 헛로그인을 시킨다(2026-08-03 실측된 오보 계열).
        log.warning("RG(정산) 세션 판정 불가 — 로그아웃 확증이 아니므로 로그인을 요구하지 "
                    "않는다('rg' 실행 시 진입 프로브가 다시 판정한다).")
    # 로그인은 됐는데 push가 실패한 경우는 재시도 대상 — login_required로 보고하면
    # 멀쩡한 세션을 두고 요청이 소멸한다(거짓 로그인 문제).
    summ = _summarize(res.get("body") or "")
    if not summ:
        # ★파싱 실패는 0이 아니다(R2): _is_success는 saleSummaryByDate '키 존재'만 보고,
        #   _summarize는 그 값이 list일 때만 요약한다 — 200 JSON이지만 값이 dict/null이면
        #   세션은 저장됐어도 push할 데이터가 없다. 여기서 0을 반환하면 claim된 회차가 성공
        #   (_push→heartbeat)도 실패(fetch-error)도 보고하지 않아 요청이 임대된 채 남고,
        #   UI는 215초 헛기다린 뒤 'Mac 응답 없음'(Mac 꺼짐)으로 오진한다(lease TTL 20분).
        #   → 보고 가능한 실패로 만든다. 세션 파일은 이미 저장됐으므로 다음 재시도는 _do_run
        #   분기로 가서 같은 사유를 rc=1로 보고한다(무한 재로그인 루프 없음).
        #   RC_LOGIN_REQUIRED가 아닌 1인 이유: 로그인 자체는 됐다 — 재시도 대상이다.
        log.error("vendor-summary 파싱 실패 — 응답 형태 변경 의심: %s", (res.get("body") or "")[:160])
        return 1
    for body in older_bodies:
        older = _summarize(body)
        if older:
            _merge_summary(summ, older)
    _log_summary("[login]", summ, _observed_span(summ, cfg))
    if _push_configured(cfg):
        rc = _push(cfg, summ)   # 첫 데이터 즉시 push
        if rc != 0:
            # push 실패가 우선이다 — VS 레인이 실제로 실패했고 재시도 대상이다.
            # RG 미확보는 그 위에 얹힌 부가 상태이므로 로그로만 남긴다.
            if rg_verdict == _PROBE_AUTH:
                log.error("(추가) RG(정산) 데스크톱 로그인도 미완 — push 재시도 성공 후 'rg' 전에 로그인 필요.")
            return rc
    if rg_verdict == _PROBE_AUTH:
        # VS는 전부 성공(로그인·파싱·push)했지만 정산 데스크톱 세션만 못 잡았다. 0을 돌려주면
        # 그 침묵이 그대로 'rg' fail-fast로 이어진다(이 프로브가 존재하는 이유).
        # ★rc=5는 **AUTH(로그아웃 확증)일 때만**이다 — 판정 불가는 위에서 0으로 빠진다.
        log.error("판매분석(VS) 로그인·push 완료 — RG(정산) 데스크톱 로그인 미완(창은 열어 둠, "
                  "'login' 재실행 또는 창에서 로그인 후 'rg' 실행).")
        return RC_RG_LOGIN_REQUIRED
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
    cdp = _cdp_mode(cfg)
    owner = _ChromeOwner()   # 창 소유권 — 로그인 미완료 시 창을 남기기 위해 직접 만든다
    res = None
    older_bodies: list[str] = []   # 과거 청크들(자가치유 창) — 최신 청크 성공 후에만 채워진다
    vi_rows: list[dict] = []       # 옵션축(D-CPP-36) — 요약축 성공 후 같은 세션에서 채워진다
    # 창은 실행 시작 시점에 한 번만 계산한다 — 실행 중 KST 자정을 넘겨도 청크가 하루 밀려
    # 겹치거나(중복) 새 어제가 빠지지(누락) 않게.
    windows = _vs_windows(cfg)
    login_needed = False     # 사람 로그인이 필요한 실패인지(=재시도해도 소용없음, §0)
    try:
        with sync_playwright() as p:
            with _chrome(p, cfg, state, owner=owner) as (page, ctx, save):
                res = _fetch_vendor_summary(page, cfg, window=windows[0])
                if _is_auth_expired(res):
                    # 세션 만료 의심 → 대시보드 재진입으로 챌린지 자동해소 후 1회 재시도.
                    log.info("세션 만료 의심 — 대시보드 재진입 후 재fetch 시도")
                    res2 = _fetch_vendor_summary(page, cfg, window=windows[0])
                    if _is_success(res2):
                        save()  # 회전 쿠키 보존 (CDP: no-op)
                        res = res2
                    elif login_wait_secs > 0:
                        log.info("자동 회복 실패 — 창에서 로그인하세요(자동 감지, 최대 %d초).", login_wait_secs)
                        with contextlib.suppress(Exception):
                            page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
                        relogin = _login_wait_loop(page, ctx, cfg, state, login_wait_secs, cdp=cdp,
                                                   window=windows[0])
                        res = relogin if relogin is not None else res2
                        if relogin is None:
                            owner.keep_open = True   # 로그인 미완료 → 창 남김(이어서 로그인 가능)
                            login_needed = True
                    else:
                        res = res2
                        owner.keep_open = True       # 로그인 대기 없는 경로 → 창 남김
                        login_needed = True
                elif _is_success(res):
                    save()  # 회전된 세션쿠키 갱신 (CDP: no-op)
                # 최신 청크가 살아있을 때만 과거 청크를 이어 받는다(자가치유 — 버튼 공백 메우기).
                if _is_success(res):
                    older_bodies = _fetch_older_windows(page, cfg, windows[1:])
                    # 옵션축(D-CPP-36)을 **같은 페이지·같은 회차**에서 이어 받는다.
                    # ★같은 세션에서 받아야 보존식(Σ옵션 == 요약)이 같은 시점을 비교한다. 별도
                    #   회차로 나누면 그 사이 쿠팡 lastRefresh가 돌아 두 축이 다른 순간을 담는다.
                    # ★요약축 push를 막지 않는다 — 예외를 여기서 삼킨다(옵션축은 요약축의
                    #   부가축이고, 요약축은 이미 검증된 정본 경로다).
                    try:
                        vi_rows = _fetch_vi_detail(page, cfg)
                    except Exception as e:  # noqa: BLE001
                        log.warning("옵션축 수집 중단(요약축은 계속): %s", str(e)[:120])
                        vi_rows = []
    except Exception as e:  # noqa: BLE001
        log.error("브라우저 fetch 오류: %s", e)
        return 1

    if not _is_success(res):
        status = res.get("status") if res else None
        body = (res.get("body") if res else "") or ""
        log.error("vendor-summary 실패 status=%s — %s. 'login' 재실행 필요.",
                  status, body[:160].replace("\n", " "))
        return RC_LOGIN_REQUIRED if login_needed else 1
    summ = _summarize(res.get("body") or "")
    if not summ:
        log.error("vendor-summary 파싱 실패 — 응답 형태 변경 의심: %s", (res.get("body") or "")[:160])
        return 1
    for body in older_bodies:
        older = _summarize(body)
        if older:
            _merge_summary(summ, older)
    _log_summary("[run]", summ, _observed_span(summ, cfg))
    if _push_configured(cfg):
        rc = _push(cfg, summ)   # push 성공이 heartbeat(prod staleness 기준)
        # 옵션축은 요약축 push **뒤에** 보낸다. 반환코드는 요약축이 진다 — 옵션축 실패로
        # 회차를 «실패»로 오보하면 lease가 요청을 되살려 창이 반복해서 뜬다(부가축이 정본축의
        # 성공 판정을 뒤집으면 안 된다). 옵션축 결과는 로그와 헬스 신선도가 표면화한다.
        vi_sold = sum(1 for r in vi_rows if r["units_sold"] != 0)
        log.info("[run] 옵션축 %d행(판매발생 %d) — 날짜 %d일",
                 len(vi_rows), vi_sold, len({r["date"] for r in vi_rows}))
        _push_vendor_item_sales(cfg, vi_rows, summ.get("last_refresh"))
        return rc
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


# run 반환코드 — 3=로그인 필요(재시도 무의미: 창만 반복해서 뜬다, §0). 1=그 외 실패(재시도 대상).
RC_LOGIN_REQUIRED = 3
# 4=이번 회차는 중복 요청(dedup)에 막혀 받지 못했다 — 실패도 성공도 아니고 "나중에 다시".
# 30초 뒤 재시도는 여전히 dedup 윈도우 안이므로 긴 백오프를 쓴다(codex 4R[P1]).
RC_RETRY_LATER = 4
# 5=VS(판매분석) 로그인·push는 전부 성공했으나 RG(정산) 데스크톱 세션만 미확보 —
# 사람이 wing.coupang.com에 다시 로그인해야 한다. **cmd_login CLI 전용**이다: 데몬 VS 레인은
# rg_probe=False로 호출하므로 이 코드가 올라오지 않는다(RG 실패로 VS 요청이 오보되면 안 됨).
RC_RG_LOGIN_REQUIRED = 5
_VS_ERROR_PATH = "/api/coupang/ops/wing/vendor-summary/fetch-error"
_RG_ERROR_PATH = "/api/coupang/ops/wing/rg-settlement/fetch-error"
_RG_COMPLETE_PATH = "/api/coupang/ops/wing/rg-settlement/refresh-complete"

_POLL_INTERVAL_S = 15       # 갱신 요청 확인 간격(창 안 뜸, 가벼운 GET)
_LOGIN_WAIT_S = 180         # 세션 만료 시 헤드풀 창 로그인 대기 한도
_MIN_FETCH_INTERVAL_S = 45  # fetch(창) 최소 간격 — 요청 폭주로 창 스팸 방지(광고 패턴)
# 자가복구: 연속 네트워크 실패가 쌓이면 종료 → launchd가 fresh 재기동(광고 페처 패턴).
# sleep/wake 후 소켓 고착(fresh Python은 성공해도 장기 프로세스만 'Max retries') 자동 해소.
_MAX_CONSECUTIVE_NET_FAILS = 20  # 15s 간격 × 20 ≈ 5분


def _basic_auth(cfg: dict):
    """prod Basic Auth 자격증명 — 없으면 None(인증 켜기 전까지 기존 동작 유지).

    ★설정에 키가 없으면 None을 돌려준다. 그래야 이 커밋을 먼저 배포해 두고
      나중에 nginx를 켜는 «순서»가 성립한다(둘을 동시에 바꾸면 되돌릴 곳이 두 곳이 된다).
    """
    u = cfg.get("basic_auth_user")
    p = cfg.get("basic_auth_pass")
    return (u, p) if u and p else None


# ★버튼 큐는 계정 차원(2026-07-27, WING2 인스턴스 편입): account_key를 안 보내면 백엔드가
#   WING1 큐로 해석한다 → WING2 인스턴스가 오픽스(WING1) 버튼 요청을 claim해 가져가는 도난이
#   난다(claim=원자적, 먼저 집는 쪽이 이김). 아래 4개 호출 모두 자기 계정을 명시한다.
def _prod_refresh_status(cfg: dict) -> dict:
    r = requests.get(
        cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/wing/vendor-summary/refresh-status",
        params={"account_key": cfg["account_key"]},
        auth=_basic_auth(cfg),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _prod_claim(cfg: dict) -> dict:
    r = requests.post(
        cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/wing/vendor-summary/refresh-claim",
        params={"account_key": cfg["account_key"]},
        headers={"X-Ingest-Token": cfg["ingest_token"]},
        auth=_basic_auth(cfg),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _prod_rg_refresh_status(cfg: dict) -> dict:
    r = requests.get(
        cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/wing/rg-settlement/refresh-status",
        params={"account_key": cfg["account_key"]},
        auth=_basic_auth(cfg),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _prod_rg_claim(cfg: dict) -> dict:
    r = requests.post(
        cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/wing/rg-settlement/refresh-claim",
        params={"account_key": cfg["account_key"]},
        headers={"X-Ingest-Token": cfg["ingest_token"]},
        auth=_basic_auth(cfg),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _prod_notify_rg_complete(cfg: dict, lease: str | None = None) -> None:
    """RG run 정상 완주 → 갱신 요청 소멸(lease 계약). 업로드가 이미 소멸시켰으면 무해한 no-op.

    lease: 내 임대에 대해서만 완료 처리(20분 넘긴 run이 남의 요청을 지우는 것 차단).
    """
    if not _push_configured(cfg):
        return
    body = {"lease": lease} if lease else {}
    # ★완료 신호는 몇 번 재시도한다(codex 5R[P1]): 이 한 번의 POST가 유실되면 요청이 임대된
    #   채 남아 UI 타임아웃·중복 수집·거짓 소진으로 번진다(RG는 자동완료 안전망도 꺼져 있다).
    for attempt in range(3):
        try:
            r = requests.post(
                cfg["prod_base_url"].rstrip("/") + _RG_COMPLETE_PATH,
                params={"account_key": cfg["account_key"]},   # ★계정 명시 — 아래 R3 주석 참조
                json=body,
                headers={"X-Ingest-Token": cfg["ingest_token"]},
                auth=_basic_auth(cfg),
                timeout=10,
            )
            if r.status_code == 200:
                return
            log.warning("RG refresh-complete 비200(%s) — 재시도 %d/3", r.status_code, attempt + 1)
        except Exception as e:  # noqa: BLE001
            log.warning("RG refresh-complete 실패(%s) — 재시도 %d/3", str(e)[:80], attempt + 1)
        time.sleep(2 * (attempt + 1))
    log.error("RG refresh-complete 최종 실패 — 요청이 임대된 채 남는다(TTL 후 재시도됨).")


def _prod_report_failure(cfg: dict, path: str, error: str, kind: str | None = None,
                        lease: str | None = None) -> None:
    """run 실패를 prod에 보고 → 재시도 판정의 입력(lease 계약, PLAN_coupang-claim-retry-lease).

    보고가 없으면 lease TTL(기본 20분)이 지나야 재시도된다 — 보고하면 다음 폴에서 곧바로.
    kind="login_required"면 prod가 재시도 없이 요청을 소멸시킨다(§0 금지선: 재시도해도
    실패하고 창만 반복해서 뜬다). best-effort — 보고 실패가 run을 더 망가뜨리면 안 된다.

    ★account_key를 반드시 보낸다(R3, 2026-07-27 버그 수정): 버튼 큐는 계정 차원인데(WING2
    인스턴스 편입) 이 호출만 계정을 안 보내고 있었다 — 엔드포인트 기본값이 WING1이라 WING2
    데몬의 실패 보고가 WING1 상태행으로 갔다. 거기엔 내 lease가 없으니 report_failure가
    'stale 실패 보고 무시'로 접었고(refresh_contract), 결과적으로 WING2의 실패는 **아무데도
    기록되지 않고 사라졌다** — 임대는 TTL 20분까지 살아남아 UI가 'Mac 응답 없음' 오진.
    (같은 누락이 _prod_notify_rg_complete에도 있었다. status·claim 4개 호출은 이미 명시 중.)
    """
    if not _push_configured(cfg):
        return
    body = {"error": str(error)[:300]}
    if kind:
        body["kind"] = kind
    if lease:
        body["lease"] = lease   # 내 임대에 대해서만 보고(stale 보고 차단, codex 1R[P1])
    try:
        r = requests.post(
            cfg["prod_base_url"].rstrip("/") + path,
            params={"account_key": cfg["account_key"]},
            json=body,
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            auth=_basic_auth(cfg),
            timeout=10,
        )
        # ★비200을 침묵시키지 않는다(codex 1R[P2], 2026-08-03): 이 POST가 실패의 **유일한**
        #   흔적(last_error_at)을 만든다. 401(토큰 만료)·422·500이어도 조용히 끝나면 화면은
        #   "아직 진행 중"으로 보이다가 폴링 창을 헛기다린 뒤 뭉뚱그린 문구를 낸다 — 이
        #   엔드포인트가 막으려고 만들어진 바로 그 상태로 되돌아간다.
        if r.status_code != 200:
            log.warning("fetch-error 보고 비200(%s) — 실패 흔적이 prod에 안 남는다: %s",
                        r.status_code, r.text[:120])
    except Exception as e:  # noqa: BLE001
        log.warning("fetch-error 보고 실패(무시): %s", str(e)[:120])


class _LastErrorCapture(logging.Handler):
    """run 동안 마지막 log.error 메시지를 붙잡는다 — fetch-error 보고의 사유로 쓴다."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.last: str | None = None

    def emit(self, record: logging.LogRecord) -> None:
        self.last = record.getMessage()


@contextlib.contextmanager
def _capture_last_error():
    h = _LastErrorCapture()
    log.addHandler(h)
    try:
        yield h
    finally:
        log.removeHandler(h)


def _run_claimed(fn) -> tuple[int, str | None]:
    """claim 후 실행되는 작업을 돌린다 → (rc, 사유). 예외도 rc=1로 정규화한다.

    ★왜 예외를 잡는가(R1): claim은 요청을 소비하지 않지만 **임대**를 잡는다. 예외로 조용히
    폴 루프의 외곽 핸들러까지 빠져나가면 실패 보고가 없어 임대가 TTL(기본 20분)까지 살아있고,
    그 뒤 재claim → 또 20분 → 3회 소진 reaper까지 40~60분이 걸린다. UI는 215초에 포기하므로
    그동안 'Mac 응답 없음'(Mac 꺼짐/미설치)으로 오진하고, 창은 3번 뜬다. 보고하면 임대만
    반납돼 다음 폴에서 곧바로 재시도된다 — rc!=0과 똑같이 취급해야 한다.
      실제 탈출 경로: cmd_login은 자체 try/except가 없어 Chrome 기동 실패(_owned_chrome
      RuntimeError: chrome_profile_busy·chrome_launch_failed·cdp_not_ready 등)·page.goto
      타임아웃이 그대로 올라온다. _do_run/_do_rg_run도 브라우저 블록 **밖**(설정 int 변환·
      꼬리의 _push)은 자체 try에 덮이지 않는다.

    ★왜 사유를 캡처하는가(R1): 호출자가 아는 것은 rc뿐이라 보고 문구가 "…실패(rc=1)"에
    그친다 — UI에 원인이 안 뜬다. 실패 경로는 직전에 반드시 log.error로 사유를 남기므로
    (예: "vendor-summary 실패 status=403 … 'login' 재실행 필요") 그것을 사유로 쓴다.
    없으면 예외 텍스트, 그것도 없으면 호출자의 일반 문구로 폴백한다.
    """
    with _capture_last_error() as cap:
        try:
            return fn(), cap.last
        except Exception as e:  # noqa: BLE001 — 데몬은 죽지 않는다. 대신 반드시 보고한다.
            # 사유는 log.error '전에' 확정한다 — 아래 로그가 cap.last를 덮어쓰면 안 된다.
            reason = cap.last or f"{type(e).__name__}: {e}"
            log.error("claim된 작업 예외 — 실패로 보고: %s", str(e)[:160])
            return 1, reason


def cmd_poll(cfg: dict) -> int:
    """상주 데몬(별도 plist com.ohisell.wing, D-5) — 갱신 '버튼' 요청이 있을 때만 headful 창.

    평소엔 가벼운 GET만(창 안 뜸). 두 흐름을 함께 소비:
      ① vendor-summary: '판매분석 갱신' 버튼 → claim → fetch+push.
      ② RG 정산(S4-P2): 'RG 정산 갱신' 버튼 → claim → _do_rg_run(엑셀 다운로드+push).
         ★새벽 일일 예약(rg_daily_hour)은 제거됨(2026-07-27) — 마지막 남은 자동 창 트리거였다.
         RG도 버튼 요청만 소비한다. 낡음은 prod /collection-status 전역 신선도 배너로 가시화.
      ★Chrome도 fetch 때만 띄웠다 닫는다(supervisor 폐기) — 창은 버튼 직후 1회만.
    세션 만료 시 같은 창에서 로그인 대기(아침 첫 트리거가 로그인 겸함, 광고 페처 패턴 D-1).
    push 미설정이면 데몬은 무의미하므로 fail-fast.
    """
    if not _push_configured(cfg):
        log.error("poll 데몬엔 account_key·prod_base_url·ingest_token 필요 — 설정 누락.")
        return 2
    state = os.path.expanduser(cfg["state_file"])
    interval = int(cfg.get("poll_interval_s", _POLL_INTERVAL_S))
    cooldown = int(cfg.get("min_fetch_interval_s", _MIN_FETCH_INTERVAL_S))
    # ★기준점은 `0.0`이 아니라 `None`(= 이번 프로세스에서 한 번도 안 돌림)이다.
    #   `time.monotonic()`은 macOS에서 **부팅 이후 경과초**라(실측 2026-08-04: 부팅
    #   11:26:03 · monotonic 21,214 ≈ uptime 5:53), `0.0`을 기준으로 삼으면
    #   `monotonic - 0.0 >= 쿨다운`이 곧 **"부팅 후 쿨다운만큼 지났나"**가 된다.
    #   RG는 쿨다운이 3600초라 **재부팅 후 1시간 동안 버튼이 통째로 침묵했다** — claim도
    #   로그도 실패 보고도 없이. 요청 플래그는 살아남고 UI는 215초 뒤 "Mac 응답 없음"으로
    #   오진한다(이 파일 R1~R3 가드가 막으려던 실패 모드인데, 가드에 **도달조차 못 했다**).
    #   2026-06-14 S4 도입부터 7주간 잠복했고, 테스트가 부팅 1시간 안에 돌 때만 드러났다.
    last_fetch: float | None = None
    # RG 정산(S4-P2): 온디맨드 버튼만. RG는 주 단위·느림(생성 대기) → 별도 쿨다운 유지.
    rg_cooldown = int(cfg.get("rg_min_interval_s", 3600))   # RG 실행 최소 간격(실패 재시도 폭주 방지)
    last_rg: float | None = None
    # ★재시도용 짧은 백오프(codex 2R[P1]): RG 쿨다운은 1시간이라, 재시도 가능한 실패 뒤
    # 다음 시도가 1시간 뒤가 된다 — UI는 215초에 포기하고 3회 소진에 2시간이 걸린다.
    # 실패로 임대를 반납한 경우에 한해 쿨다운을 이 시각까지 면제한다(요청이 살아있는 동안만).
    rg_retry_at: float | None = None
    rg_retry_backoff = int(cfg.get("rg_retry_backoff_s", 30))
    # 중복 요청(dedup)에 막힌 회차용 긴 백오프 — 30초 뒤 재시도는 반드시 또 dup이 된다.
    rg_dup_backoff = int(cfg.get("rg_dup_backoff_s", 900))
    log.info("Wing 폴 데몬 시작 — %ds 간격 확인, fetch 최소간격 %ds. RG 버튼 전용·간격 %ds(창은 버튼 요청 시에만 뜸).",
             interval, cooldown, rg_cooldown)
    net_fails = 0  # 연속 네트워크 실패 카운터(vendor-summary 폴 기준) — 성공 시 리셋
    while True:
        try:
            st = _prod_refresh_status(cfg)
            net_fails = 0
            if st.get("requested"):
                # 쿨다운/락은 claim '전에' 검사 — claim 후 스킵하면 요청 유실(광고 패턴 codex P2).
                if last_fetch is not None and time.monotonic() - last_fetch < cooldown:
                    log.info("fetch 쿨다운 중 — 요청 보류(다음 폴에서 처리)")
                else:
                    with _try_fetch_lock() as acquired:
                        if not acquired:
                            log.info("다른 fetch 진행 중 — 요청 보류(다음 폴에서 처리)")
                        else:
                            _claim = _prod_claim(cfg)
                            if _claim.get("claimed"):
                                lease = _claim.get("lease")   # 내 임대 식별자(실패 보고에 첨부)
                                last_fetch = time.monotonic()
                                log.info("갱신 요청 감지 — fetch 시작")
                                # _run_claimed: 예외도 rc=1로 정규화하고 사유(마지막 log.error)를
                                # 함께 돌려준다 — 조용한 탈출로 임대가 20분 묶이는 것을 막는다(R1).
                                if not Path(state).is_file():
                                    # ★rg_probe=False: 이 레인에서 "성공"의 의미는 VS 수집 완료
                                    #   =버튼 요청 소비다. RG 데스크톱 세션 미확보(rc=5)까지
                                    #   여기서 실패로 접으면 멀쩡히 받은 판매분석이 fetch-error로
                                    #   오보된다. RG 레인은 _do_rg_run(login_wait_secs=180)의
                                    #   자가회복 경로를 따로 갖고 있다.
                                    rc, reason = _run_claimed(
                                        lambda: cmd_login(cfg, wait_secs=_LOGIN_WAIT_S,
                                                          rg_probe=False))
                                else:
                                    rc, reason = _run_claimed(   # 락 보유 중
                                        lambda: _do_run(cfg, state, login_wait_secs=_LOGIN_WAIT_S))
                                if rc != 0:
                                    _prod_report_failure(
                                        cfg, _VS_ERROR_PATH,
                                        reason or f"판매분석 수집 실패(rc={rc})",
                                        kind=("login_required" if rc == RC_LOGIN_REQUIRED else None),
                                        lease=lease)
        except requests.RequestException as e:
            net_fails += 1
            log.warning("폴 확인 실패(네트워크) %d/%d: %s", net_fails, _MAX_CONSECUTIVE_NET_FAILS, str(e)[:80])
            if net_fails >= _MAX_CONSECUTIVE_NET_FAILS:
                log.error("연속 %d회 네트워크 실패 — 프로세스 종료(launchd가 fresh로 재기동).", net_fails)
                return 1
        except Exception as e:  # noqa: BLE001 — 데몬은 어떤 오류에도 죽지 않는다
            log.error("폴 루프 오류: %s", str(e)[:160])

        # ── RG 정산 다운로드: 버튼 요청만 소비(2026-07-27 — 새벽 일일예약 제거, 순수 버튼-only) ──
        try:
            rg_st = _prod_rg_refresh_status(cfg)
            _rg_ready = (last_rg is None or time.monotonic() - last_rg >= rg_cooldown) or (
                rg_retry_at is not None and time.monotonic() >= rg_retry_at)
            if bool(rg_st.get("requested")) and _rg_ready:
                with _try_fetch_lock() as acquired:
                    if not acquired:
                        log.info("RG: 다른 fetch 진행 중 — 보류(다음 폴)")
                    else:
                        # claim = 원자적 임대(요청 플래그는 보존, 2026-07-27 lease 계약).
                        #   실패를 보고하면 임대만 반납돼 다음 폴에서 자동 재시도된다(최대 3회).
                        #   로그인 필요는 재시도 제외 — 창만 반복해서 뜨기 때문(PLAN §0).
                        _rg_claimed = _prod_rg_claim(cfg)
                        if _rg_claimed.get("claimed", False):
                            rg_lease = _rg_claimed.get("lease")
                            last_rg = time.monotonic()
                            rg_retry_at = None   # 이번 시도가 시작됨 — 면제 소진
                            log.info("RG 정산 다운로드 트리거(버튼)")
                            if not Path(state).is_file():
                                log.warning("RG: 세션 파일 없음 — 'login' 필요(이번 회차 스킵)")
                                _prod_report_failure(cfg, _RG_ERROR_PATH,
                                                     "세션 파일 없음 — 로그인 필요",
                                                     kind="login_required", lease=rg_lease)
                            else:
                                # _run_claimed: 예외도 rc=1·사유 동반으로 정규화(R1) — 조용한
                                # 탈출로 임대가 TTL 20분 묶이면 UI가 'Mac 응답 없음'으로 오진.
                                rc, reason = _run_claimed(
                                    lambda: _do_rg_run(cfg, state, login_wait_secs=_LOGIN_WAIT_S))
                                if rc != 0:
                                    _prod_report_failure(
                                        cfg, _RG_ERROR_PATH,
                                        reason or f"RG 정산 수집 실패(rc={rc})",
                                        kind=("login_required" if rc == RC_LOGIN_REQUIRED else None),
                                        lease=rg_lease)
                                    if rc != RC_LOGIN_REQUIRED:
                                        # 재시도 가능한 실패 → 1시간 쿨다운을 면제하고 곧 다시
                                        # 시도한다(요청이 살아있을 때만 실제로 claim된다).
                                        # dedup에 막힌 회차는 짧은 재시도가 무의미 → 긴 백오프.
                                        rg_retry_at = time.monotonic() + (
                                            rg_dup_backoff if rc == RC_RETRY_LATER else rg_retry_backoff)
                                else:
                                    # 정상 완주 신호 — 받을 게 없어 업로드 0건이었어도 요청은
                                    # 여기서 소멸한다(없으면 창을 3번 더 띄운 뒤 거짓 실패).
                                    _prod_notify_rg_complete(cfg, lease=rg_lease)
        except requests.RequestException as e:
            log.warning("RG 폴 확인 실패(네트워크): %s", str(e)[:80])
        except Exception as e:  # noqa: BLE001 — 데몬은 어떤 오류에도 죽지 않는다
            log.error("RG 폴 루프 오류: %s", str(e)[:160])
        time.sleep(interval)


# ════════════════════════════════════════════════════════════════════
# S4: RG 정산 엑셀 자동 다운로드 (Wing 세션 자동화 트랙 D-8)
# ════════════════════════════════════════════════════════════════════
# 흐름(전부 살아있는 브라우저 세션 same-origin POST, D-5/D-6):
#   ① status/api          → 정산주기(settlementGroupKey) 목록 열거(무엇을 받을 수 있나)
#   ①' (Mac→prod) GET /api/coupang/ops/wing/rg-settlement/layer2-gaps → 결손 주기(무엇이 비었나).
#       ①∩①' = 이번 회차 대상. 결손이 0이면 다운로드도 0(정상 완주).
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
RG_PRODUCT_SIZE_UPLOAD_PATH = "/api/coupang/ops/rg/product-size/upload-xlsx"
# 층2 결손 조회(읽기 전용) — "옵션 엑셀이 비어 있는 주기"를 prod에 묻는다(자가치유 D1).
RG_LAYER2_GAPS_PATH = "/api/coupang/ops/wing/rg-settlement/layer2-gaps"
# 전체 sellerReportType 목록 (ExcelModal.js i18n에서 확보, 2026-06-15 라이브 API 검증 완료).
# 파서 검증 완료: WAREHOUSING_SHIPPING(입출고/배송비). 나머지 8종은 API 200 확인·파서 미구현.
# 설정 파일 rg_report_types로 override 가능.
CONFIRMED_SELLER_REPORT_TYPES = [
    "CATEGORY_TR",             # 판매수수료 리포트
    "WAREHOUSING_SHIPPING",    # 입출고/배송비 리포트 (파서 구현)
    "STORAGE_FEE",             # 보관비 리포트
    "INVENTORY_COMPENSATION",  # 재고 손실 보상 리포트
    "BARCODE_LABELING_FEE",    # 부가서비스비 리포트
    "PRODUCT_SIZE_COMPARISON", # 상품별 사이즈 리포트
    "CRETURN_PICKUP_RESTOCKING", # 반품 회수/재입고 비용 리포트
    "VRETURN_HANDLING",        # 반출비 리포트
    "VRETURN_SHIPPING",        # 반출 배송 서비스비 리포트
]
RG_REPORT_TYPES_DEFAULT = ["WAREHOUSING_SHIPPING"]
# 회차당 다운로드 주기 상한(config rg_max_targets로 조정). 층2는 주기×리포트당 최대 300초
# 폴링이라 무상한이면 한 회차가 폭주한다 — 결손이 더 많으면 최신 우선으로 자르고 나머지는
# 다음 회차가 이어받는다(자연 롤링). 3 = 최악 3주기×1리포트×300초 ≈ 15분(TTL 20분 안).
_RG_DEFAULT_MAX_TARGETS = 3
# 결손 조회 창 상한 — 백엔드 라우터의 days le=400과 같은 값(넘기면 422 → 조용한 폴백).
_RG_GAPS_MAX_DAYS = 400
# 회차 시간 예산 근거(리뷰 R3 [P2-1]): lease TTL 20분을 넘기면 mark_success/report_failure가
#   claimed_at 불일치로 폐기되고 요청이 살아남아 재claim → 창이 다시 뜬다(lease 계약이 막으려던
#   증상 그 자체). 상한의 *단위*는 D2 확정("회차당 주기 수")이라 건드리지 않고, 직교하는 시간
#   예산을 건다. ★refresh_contract._LEASE_TTL_MIN(기본 20분)과 결합 — 그쪽이 바뀌면 여기도.
_RG_LEASE_TTL_S = 1200
_RG_ONE_REPORT_TAIL_S = 150   # S3 GET(90s) + prod push(60s) — 폴링 뒤에 항상 따라붙는 꼬리
# 여유분 90s: 브라우저 teardown(~25s) + refresh-complete 통지 + 주기 사이 세션 재확인 지연(5s×n).
#   경계 확인 — 예산 660 + 최악 1건 450 + teardown 25 = 1135 < TTL 1200.
_RG_BUDGET_SLACK_S = 90
_RG_POLL_INTERVAL_S = 8       # download-list 폴링 간격
_RG_POLL_TIMEOUT_S = 300      # 생성 완료 최대 대기(5분)
# ── status/api 5xx 재시도 (2026-08-03 라이브 계측) ──────────────────────────
# status/api는 업스트림이 상시 불안정하다. 같은 페이지·같은 쿠키로 32회 호출한 실측에서
#   status/api 1/32 성공(창 폭 35/21/7/1일 전부 동일), download-list/api 8/8 성공.
#   실패는 0.55초 fast-fail HTTP 500이고 본문은 **로그인된** Wing 셸 HTML
#   (`<title>Coupang Wing - …</title>`, `__GLOBAL_DATA__ activeProfile:'production'`, istio-envoy).
# 120초 근거: 라이브 회복 실측 22s·46s·34s(08-03 12:43·12:55·13:06)에 여유 2배 이상.
#   회차 예산(660s) 안이라 다운로드 몫을 크게 갉지 않는다.
_RG_STATUS_RETRY_S = 120
_RG_STATUS_RETRY_INTERVAL_S = 5
# 로그아웃은 정산 URL이 xauth 로그인 페이지로 리다이렉트되는 형태로도 드러난다(_rg_off_origin).
_RG_ORIGIN_HOST = "wing.coupang.com"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _kst_date_to_utc_iso(kst_date) -> str:
    """KST date → status/api용 UTC ISO 'YYYY-MM-DDT15:00:00.000Z'(KST 00:00=UTC, client와 동일)."""
    return f"{kst_date.isoformat()}T15:00:00.000Z"


def _rg_status_days(cfg: dict) -> int:
    """층1 status/api 조회 창(일). 기본 35(월경계 분할 주기+여유), 백필 시 config로 90 등 오버라이드.

    ★층2 결손 조회(_prod_rg_layer2_gaps)도 같은 값을 쓴다 — 열거된 주기와 결손 판정 창이
      어긋나면 "결손이라는데 열거엔 없는 주기"가 생겨 영영 못 메운다.
    """
    return _cfg_int(cfg, "rg_status_days", 35)


def _rg_status_payload(cfg: dict, days: int | None = None) -> dict:
    """status/api body — 최근 윈도우(매출인식일 SALES, D-10). 정산주기 열거·계정 수집 공용.

    days=None이면 21일 — 세션 판정 프로브(_rg_session_ok)가 빈 cfg로 부르는 가벼운 기본값이다.
    (config의 rg_days는 층1/층2 통합 후 아무도 읽지 않는다 — 죽은 키. 설정에 남아 있어도 무시.)
    층1 계정 수집(_rg_fetch_status_raw)은 _rg_status_days(기본 35, 백필 시 90)를 명시 전달한다.
    """
    if days is None:
        days = 21   # 프로브 전용 폭 — 닫힌 주별 정산 여러 건 포함
    today = datetime.now(KST).date()
    return {
        "startDate": _kst_date_to_utc_iso(today - timedelta(days=days)),
        "endDate": _kst_date_to_utc_iso(today),
        "searchDateType": "SALES",
    }


def _rg_fetch_status_raw(page, cfg: dict, *, budget_s: float = _RG_STATUS_RETRY_S
                         ) -> tuple[str, dict | None]:
    """status/api → (판정, raw dict). **5xx는 예산 안에서 재시도**(층1 push + 열거 공용 소스).

    윈도우=rg_status_days(기본 35 — 월경계 분할 주기+여유, 백필 시 cfg로 90 오버라이드).
    ★이 함수가 유일한 status/api 소스여야 push와 열거가 같은 raw를 공유(이중 호출 금지, §1.6).
    ★재시도가 필수인 이유(_RG_STATUS_RETRY_S 주석의 실측): status/api는 1/32만 성공한다.
      재시도 없이 1회로 판정하면 회차 대부분이 '열거 실패 rc=1'로 죽는다 — 실제로 프로브만
      고친 상태의 라이브 런(2026-08-03 13:35:37)이 정확히 그렇게 rc=1로 끝났다.
      종전엔 _rg_login_wait(5초×180초)가 **우연히** 이 재시도를 대행하고 있었을 뿐이라,
      프로브가 즉시 통과하게 되는 순간 그 우연한 보호막이 사라진다.
    AUTH(로그아웃 확증)면 즉시 반환한다 — 재시도해도 로그인 전엔 통과할 수 없다.
    """
    deadline = time.monotonic() + max(0.0, budget_s)
    attempt = 0
    while True:
        attempt += 1
        off = _rg_off_origin(page)
        if off:
            log.error("RG status/api 오리진 이탈(로그인 필요) — url=%s", off[:120])
            return _PROBE_AUTH, None
        verdict, why, data = _rg_probe_endpoint(
            page, RG_STATUS_PATH, _rg_status_payload(cfg, days=_rg_status_days(cfg)),
            expect_key="settlementStatusReports")
        if verdict == _PROBE_OK:
            if attempt > 1:
                log.info("RG status/api %d회 재시도 끝에 성공 — 일시적 업스트림 500이었다.", attempt)
            return verdict, data
        log.info("RG status/api %s(%d회차) — %s", verdict, attempt, why)
        if verdict == _PROBE_AUTH:
            return verdict, None
        if time.monotonic() >= deadline:
            log.error("RG status/api %d회 재시도(%.0fs) 모두 실패 — 업스트림 장애로 본다"
                      "(로그아웃 아님, 재시도 대상).", attempt, budget_s)
            return verdict, None
        time.sleep(_RG_STATUS_RETRY_INTERVAL_S)


def _rg_push_status(cfg: dict, raw: dict) -> int:
    """status/api raw JSON → prod ingest-status push. 0=성공/1=실패. (_rg_push_xlsx 에러 처리 미러)

    ★fail-soft: push 실패는 log만 하고 계속 — 엑셀(다운로드) 흐름을 죽이면 안 된다(§1.5).
    account_key 명시(백엔드가 RG_ACCOUNTS 검증) + X-Ingest-Token(엑셀 push와 동일 인증).
    """
    try:
        pr = requests.post(
            cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/wing/rg-settlement/ingest-status",
            params={"account_key": cfg["account_key"]},
            json=raw,
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            auth=_basic_auth(cfg),
            timeout=60,
        )
    except requests.RequestException as e:
        log.error("RG status push 네트워크 오류: %s", e)
        return 1
    if pr.status_code != 200:
        log.error("RG status push 실패 HTTP %s — %s", pr.status_code, pr.text[:200])
        return 1
    try:
        info = pr.json()
    except ValueError:
        info = pr.text[:120]
    log.info("RG status push 성공 → %s", info)
    return 0


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


def _rg_kst_date_str(value) -> str:
    """쿠팡 정산 날짜(UTC ISO "YYYY-MM-DDTHH:mm:ssZ") → KST 날짜 "YYYY-MM-DD". 실패=''.

    ★백엔드 `rg_settlement_sync._parse_date`와 **같은 규칙**이어야 한다. 쿠팡은 정산주기 경계일을
      KST 자정(= D-1T15:00:00Z)으로 주므로 앞 10자를 그대로 쓰면 **항상 하루 이르다**.
      백엔드는 KST로 변환해 `recognition_date_to`에 저장하는데, 층2 결손 매칭은 그 값과 여기서
      만든 문자열을 대조한다 → 앞 10자를 쓰면 매칭이 **상시 100% 실패**하고, 그런데도 "결손 없음"
      으로 조용히 완주해 층2 수집이 통째로 멈춘다(적대적 리뷰 R1 [P1-1] 실행 재현).
      실측: `settlementPeriodEndDate="2026-04-11T15:00:00Z"` ↔ `settlementGroupKey`
      `"A01564720-2026-04-06-2026-04-12"` — group_key 자체가 KST라는 증거다.
    ★백엔드에서 import하지 않는다 — 페처는 prod 코드를 못 읽는 독립 스크립트다. 규칙 동기화는
      실측 포맷 계약 테스트(test_rg_gap_driven_fetcher)로 고정한다.
    ★파싱 실패는 예외가 아니라 ''다 — 리포트 한 건의 날짜 이상이 회차 전체를 rc=1로 죽이면 안 된다.
    """
    if not value:
        return ""
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        log.warning("RG 정산 날짜 파싱 실패(해당 값만 무시): %s", s[:40])
        return ""
    return dt.astimezone(KST).date().isoformat()


def _rg_enumerate_group_keys(raw: dict, vendor_id: str) -> list[dict]:
    """status/api raw dict → [{group_key, period_end}]. settlementGroupKey 우선, 없으면 vendorId-start-end.

    ★층1: raw를 인자로 받는다(page 의존 제거) — _rg_fetch_status_raw가 한 번만 POST한 raw를
      push와 열거가 공유해 status/api 이중 호출을 막는다(§1.6).
    설치분(가지급/확정)으로 같은 기간 리포트가 2개 와도 group_key 동일 → dedupe.
    """
    data = raw
    if not isinstance(data, dict):
        raise RuntimeError("status/api 응답 비정상(dict 아님)")
    reports = data.get("settlementStatusReports")
    if not isinstance(reports, list):
        raise RuntimeError("status/api에 settlementStatusReports 없음(스키마 드리프트 의심)")
    out: list[dict] = []
    seen: set[str] = set()
    for rep in reports:
        if not isinstance(rep, dict):
            continue
        # ★UTC→KST 변환 필수(앞 10자 절단 금지) — 이유는 _rg_kst_date_str docstring.
        #   start/end는 settlementGroupKey 부재 시 합성 키의 재료이기도 하다. 합성 키는 라벨이
        #   아니라 `settlementGroupKeys`로 쿠팡에 **전송**되므로(_rg_download_one) 여기서
        #   KST가 아니면 존재하지 않는 주기를 요청하게 된다(리뷰 R3).
        start = _rg_kst_date_str(rep.get("settlementPeriodStartDate"))
        end = _rg_kst_date_str(rep.get("settlementPeriodEndDate"))
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


def _prod_rg_layer2_gaps(cfg: dict, report_types: list[str], days: int) -> dict | None:
    """prod에 "층2(옵션 엑셀)가 비어 있는 주기"를 묻는다(읽기 전용). 실패=None → 호출자가 폴백.

    ★수집이 멈추면 안 된다(D5): 네트워크·비200·JSON 깨짐 등 어떤 이유로든 못 물어보면 None을
      돌려주고, 호출자는 기존 동작(최신 1주기)으로 안전 저하한다.
    """
    if not _push_configured(cfg):
        return None
    # ★엔드포인트 상한(400일)으로 클램프 — 넘기면 422가 나고 조용히 폴백만 남는다. 백필하려고
    #   rg_status_days를 크게 준 회차에서 오히려 자가치유가 꺼지는 역설이 된다(리뷰 R1 [P2-4]).
    days = max(1, min(int(days), _RG_GAPS_MAX_DAYS))
    try:
        r = requests.get(
            cfg["prod_base_url"].rstrip("/") + RG_LAYER2_GAPS_PATH,
            params={"account_key": cfg["account_key"], "days": days,
                    "report_types": list(report_types)},
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            auth=_basic_auth(cfg),
            timeout=20,
        )
    except Exception as e:  # noqa: BLE001 — 조회 실패는 수집을 죽이지 않는다
        log.warning("RG 층2 결손 조회 실패(네트워크) — 최신 1주기로 폴백: %s", str(e)[:120])
        return None
    if r.status_code != 200:
        log.warning("RG 층2 결손 조회 비200(%s) — 최신 1주기로 폴백: %s",
                    r.status_code, r.text[:120])
        return None
    try:
        data = r.json()
    except ValueError:
        log.warning("RG 층2 결손 조회 응답이 JSON 아님 — 최신 1주기로 폴백.")
        return None
    return data if isinstance(data, dict) else None


def _rg_select_targets(periods: list[dict], gaps: dict | None,
                       report_types: list[str], max_targets: int) -> list[dict]:
    """열거된 정산주기 + prod 결손 목록 → 이번 회차 다운로드 대상(최신 우선·회차 상한).

    periods: [{group_key, period_end}] — 호출자가 최신 우선으로 정렬해 넘긴다.
    gaps: prod 응답 dict, None이면 조회 실패(판정 불가).
    반환: [{group_key, period_end, report_types}] — 주기마다 **결손인 리포트만** 받는다.

    폴백(D5): gaps=None이거나 커버 fee_type이 하나도 없으면(요청 리포트 전부 미매핑) 기존
      동작인 '최신 1주기 × 전체 report_types'로 되돌린다 — 판정을 못 한다고 수집을 멈추지 않는다.
    ★결손인데 열거에 없는 주기는 group_key가 없어 요청할 수 없다 → 조용히 건너뛴다.
      (옛 주기를 메우려면 rg_status_days를 넓혀 층1 열거 창부터 키워야 한다.)
    ★미매핑 리포트는 결손 판정 대상이 아니라 '최신 주기 의무 동반'이다 — 파서가 없어 결손 목록에
      **절대 뜨지 않으므로**, 결손 주도로만 고르면 영구 미수집이 된다. 라이브 config가 정확히 그
      조합(WAREHOUSING_SHIPPING + PRODUCT_SIZE_COMPARISON)이라 실제로 PRODUCT_SIZE가 끊긴다
      (적대적 리뷰 R1 [P1-2]). 구 동작(최신 1주기 × 전체 report_types)을 그 리포트에 한해 유지한다.
    """
    fallback = [{"group_key": p["group_key"], "period_end": p["period_end"],
                 "report_types": list(report_types)} for p in periods[:1]]
    if not isinstance(gaps, dict) or not gaps.get("covered_fee_types"):
        return fallback

    missing_by_end: dict[str, list[str]] = {}
    for g in gaps.get("gaps") or []:
        if not isinstance(g, dict):
            continue
        end = str(g.get("recognition_date_to") or "")
        want = [rt for rt in (g.get("missing_report_types") or []) if rt in report_types]
        if end and want:
            missing_by_end[end] = want

    targets: list[dict] = []
    for p in periods:
        want = missing_by_end.get(str(p["period_end"]))
        if not want:
            continue
        targets.append({"group_key": p["group_key"], "period_end": p["period_end"],
                        "report_types": list(want)})
        if len(targets) >= max_targets:
            break   # 나머지는 다음 회차로(자연 롤링) — 주기당 최대 300초 폴링이라 회차가 폭주한다

    # 판정 불가(미매핑) 리포트를 최신 주기에 동반시킨다 — 상한과 무관한 의무 항목이다.
    #   상한이 이걸 밀어내면 다시 영구 미수집이 되므로 결손 슬롯을 소모시키지 않는다.
    riders = [rt for rt in report_types
              if rt in set(gaps.get("unmapped_report_types") or [])]
    if riders and periods:
        newest = periods[0]
        for t in targets:
            # 최신 주기가 이미 결손 target이면 거기에 합류 — 같은 group_key로 target을 둘 만들면
            #   상한 한 칸을 헛되이 쓴다(리뷰 R3).
            if t["group_key"] == newest["group_key"]:
                t["report_types"] += [rt for rt in riders if rt not in t["report_types"]]
                break
        else:
            # ★index 0의 트레이드오프: 예산이 빠듯한 회차엔 floor 1건을 rider가 가져가 그 회차
            #   결손 치유가 0이 될 수 있다. 그래도 앞에 두는 이유 — PRODUCT_SIZE는 매번 전량
            #   스냅샷이라 하루 밀려도 무손실이지만, 끊기면 되살릴 방법이 없다([P1-2] 재발).
            #   결손 주기 쪽은 다음 회차가 그대로 이어받는다(D2 자연 롤링).
            targets.insert(0, {"group_key": newest["group_key"],
                               "period_end": newest["period_end"],
                               "report_types": list(riders)})
    return targets


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


# dup(이미 접수된 생성요청) 표식 — 실패가 아니라 "이번 회차 스킵"이다(codex 3R[P1]).
RG_DUP_SKIP = object()


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
        # ★실패로 세지 않는다(codex 3R[P1]): 재시도는 30초 뒤라 dedup 윈도우 안이라 항상 dup이
        #   된다 — 실패로 세면 재시도 3회를 dup으로 소진하고 "3회 소진"이라는 거짓 실패로 끝난다.
        #   dup은 "이미 생성 요청이 접수됨"이라는 의도된 no-op이므로 스킵으로 분류한다.
        log.info("RG 중복 요청(기간 안전식별 불가) — 스킵 %s/%s", report_type, group_key)
        return RG_DUP_SKIP
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
    """S3에서 xlsx GET(무인증·24h) → prod 업로드. 0=성공/1=실패.

    PRODUCT_SIZE_COMPARISON: 실측 사이즈 전용 엔드포인트로 push.
    나머지: 기존 정산 ingest 엔드포인트 사용.
    """
    try:
        r = requests.get(url, timeout=90)
        r.raise_for_status()
    except requests.RequestException as e:
        log.error("RG S3 다운로드 실패 %s/%s: %s", report_type, group_key, e)
        return 1
    content = r.content
    filename = url.split("?", 1)[0].rsplit("/", 1)[-1] or f"{report_type}.xlsx"

    if report_type == "PRODUCT_SIZE_COMPARISON":
        upload_path = RG_PRODUCT_SIZE_UPLOAD_PATH
        params = {"source_group_key": group_key}
    else:
        upload_path = RG_UPLOAD_PATH
        # account_key 명시(codex P1#3): S3 경로 파일명에만 의존하지 않고 설정 계정으로 직접 지정.
        #   백엔드는 명시 account_key와 파일명 vendor_id 일치도 검증(WING1↔A01564720) → 오배치도 차단.
        params = {"account_key": cfg["account_key"]}

    try:
        pr = requests.post(
            cfg["prod_base_url"].rstrip("/") + upload_path,
            params=params,
            files={"file": (filename, content, _XLSX_MIME)},
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            auth=_basic_auth(cfg),
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


# ── 세션 프로브 판정 3값 ───────────────────────────────────────────────────────
# ★왜 bool이 아닌가(2026-08-03 라이브 실측): 한 비트로는 "로그아웃"과 "판정 불가"를 구분할 수
#   없다. _rg_json이 비200·깨진 JSON·예외를 전부 None으로 접고 호출자가 그걸 곧장 '세션 만료'로
#   읽어, 멀쩡한 세션에서 남은 주기가 통째로 버려졌다(3/3 재현: 다운로드 성공 6~7초 뒤 실패).
#   대조군까지 성립했다 — 같은 세션·2분 간격에 대상 2개면 중단, 1개면 성공.
_PROBE_OK = "ok"
_PROBE_AUTH = "auth"        # 로그인 필요가 **확실**(로그인 HTML·401·403)
_PROBE_UNKNOWN = "unknown"  # 판정 불가(비200·본문 이상·예외) — 세션 만료로 승격하지 않는다


def _rg_off_origin(page) -> str:
    """페이지가 정산 오리진(wing.coupang.com)을 벗어나 있으면 그 URL, 아니면 ''.

    ★로그아웃의 **관측된** 형태다(cmd_login 주석의 2026-07-27 실측): 정산 URL이 xauth 로그인
      페이지로 리다이렉트되고 그 오리진의 status/api는 404를 준다. same-origin POST는
      location.origin 기반이라 애초에 엉뚱한 호스트로 나간다. 본문 마커보다 확실하고 싸다
      — 404 자체는 UNKNOWN이지만(정상 세션에서도 경로 변경으로 날 수 있다) 오리진 이탈은 확증이다.

    ★판정은 **호스트 파싱**으로 한다 — 부분문자열 검사는 원리적으로 이 형태를 못 본다
      (2026-08-03 WING2 라이브 실측). Keycloak 로그인 URL은 돌아갈 주소를 쿼리에 싣는다:
          https://xauth.coupang.com/auth/realms/seller/protocol/openid-connect/auth
              ?response_type=code&client_id=wing&redirect_uri=https%3A%2F%2Fwing.coupang.com%2F...
      `_RG_ORIGIN_HOST not in url`은 저 redirect_uri 안의 'wing.coupang.com'에 걸려 **로그인
      페이지 위에 서서 '오리진 유지'라고 답했다.** 그 결과 로그아웃이 AUTH로 확증되지 못하고
      404 → UNKNOWN → "업스트림 장애(로그아웃 아님)"로 오분류되어 **로그인 창이 뜨지 않았고**,
      아무도 로그인할 기회를 얻지 못한 채 회차마다 120초 재시도만 태웠다(15:22·15:25·15:27 전손).
      침묵의 형태가 정확히 이것이다 — 고장은 시끄러워야 고쳐진다.
    """
    try:
        url = str(page.url or "")
    except Exception:  # noqa: BLE001 — 창 닫힘 등은 판정 불가로 접는다
        return ""
    if not url or url.startswith("about:"):
        return ""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:          # 파싱 불가 URL = 우리 오리진이 아님(확인된 이탈로 취급)
        host = ""
    return "" if host == _RG_ORIGIN_HOST else url


def _rg_probe_endpoint(page, path: str, payload: dict, *, expect_key: str | None = None,
                       expect_list: bool = False) -> tuple[str, str, object]:
    """same-origin POST 1회 → (판정, 사유, 파싱값). 사유는 그대로 로그에 남긴다.

    ★사유를 남기는 게 이 함수의 존재 이유다: 종전엔 실패가 False 한 비트라 로그아웃인지
      429인지 500인지 evaluate 예외인지 사후에 구분할 방법이 없었다(그래서 6일 침묵의 원인을
      로그만으로 못 짚었다).
    ★HTML 마커로 로그인을 단정할 때 status를 함께 본다: 502 오류 페이지도 HTML이라
      '<html'만으로 로그인 필요라고 부르면 인프라 장애가 로그아웃으로 둔갑한다.
      (실측 2026-08-03: status/api 500의 본문은 **로그인된** Wing 셸 HTML이다.)
    """
    try:
        res = _rg_post(page, path, payload)
    except Exception as e:  # noqa: BLE001 — 네비게이션 중 evaluate 실패 등
        return _PROBE_UNKNOWN, f"evaluate 예외: {str(e)[:120]}", None
    if not isinstance(res, dict):
        return _PROBE_UNKNOWN, f"응답 형식 이상: {str(res)[:80]}", None
    status = res.get("status")
    body = res.get("body") or ""
    low = body.lower()
    if res.get("redirected"):
        # ★JSON API가 리다이렉트당했다 = 로그인 필요. 라이브 실측 체인(2026-08-03 14:05):
        #   download-list/api → 302 → wing/login → 302 → wing/logout → helpseller(크로스 오리진).
        #   redirect:'follow'였을 땐 이게 TypeError로 터져 '판정 불가'로 뭉개졌다.
        return _PROBE_AUTH, f"로그인 필요(리다이렉트 — status={status}, type={res.get('respType')})", None
    marker = next((m for m in ("kccontext", "signin", "xauth") if m in low), None)
    if marker is None and status == 200 and "<html" in low:
        marker = "html"
    if status in (401, 403) or marker:
        # ★AUTH에선 body를 남기지 않는다: 로그인 페이지 본문엔 CSRF/세션 토큰이 실릴 수 있는데
        #   "로그인 페이지였다"는 사실 외에 진단 가치가 없다(어느 신호가 맞았는지만 남긴다).
        #   UNKNOWN 쪽은 반대다 — 서버 오류 본문이 원인 규명의 거의 유일한 단서라 앞부분을 남긴다.
        return _PROBE_AUTH, f"로그인 필요(status={status}, 신호={marker or 'status'})", None
    if status != 200:
        return _PROBE_UNKNOWN, f"비200(status={status}, body={body[:120]!r})", None
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return _PROBE_UNKNOWN, f"JSON 파싱 실패(body={body[:120]!r})", None
    if expect_list:
        # download-list는 항목 배열을 준다(라이브 실측 'JSON list n=5'). 딕트가 오면 응답 형태가
        #   바뀐 것이므로 '살아 있다'고 단정하지 않는다 — 형태 드리프트를 조용히 통과시키면
        #   프로브가 아무것도 증명하지 못하는 장식이 된다.
        if isinstance(data, list):
            return _PROBE_OK, "ok", data
        keys = list(data)[:6] if isinstance(data, dict) else type(data).__name__
        return _PROBE_UNKNOWN, f"예상 형태(list) 아님: {keys}", None
    if expect_key is None:
        return _PROBE_OK, "ok", data
    if isinstance(data, dict) and expect_key in data:
        return _PROBE_OK, "ok", data
    keys = list(data)[:6] if isinstance(data, dict) else type(data).__name__
    return _PROBE_UNKNOWN, f"예상 키({expect_key}) 없음: {keys}", None


def _rg_session_probe(page) -> tuple[str, str]:
    """정산 세션 (판정, 사유) — 프로브 엔드포인트는 **download-list/api**다.

    ★왜 status/api가 아닌가(2026-08-03 라이브 계측, 같은 페이지·같은 쿠키·32회 호출):
        status/api      1/32 성공 (창 폭 35/21/7/1일 전부 동일 — 질의 무게와 무관)
        download-list   8/8  성공 (0.49s)
      "로그인했는가"의 정의를 계정에서 **가장 불안정한 엔드포인트**에 걸어 둔 것이 이 사고의
      뿌리였다. 판정을 3상태로 나눠도 프로브가 97% UNKNOWN이면 세션 감시는 사실상 죽어 있다
      — download-list는 같은 오리진·같은 쿠키·같은 XSRF로 계정의 다운로드 목록을 돌려주므로
      세션 신선도를 같은 강도로 증명하면서 20배 이상 싸고 안정적이다.
    ★단서: download-list의 **로그아웃 시 응답 형태는 라이브로 검증하지 못했다**(실제 세션을
      끊어볼 수 없어서). 그래서 오리진 이탈을 1차 확증으로 먼저 보고, 본문 마커가 없으면
      AUTH라 부르지 않는다. 진짜 로그아웃이면 이어지는 request-download가 재시도 가능한
      실패로 떨어져 회수된다(_rg_loop_should_abort의 판단기준과 같은 비대칭).
    """
    off = _rg_off_origin(page)
    if off:
        return _PROBE_AUTH, f"로그인 필요(오리진 이탈 — url={off[:100]})"
    now_ms = int(time.time() * 1000)
    verdict, why, _ = _rg_probe_endpoint(
        page, RG_DOWNLOAD_LIST_PATH,
        {"requestTimeFrom": str(now_ms - 24 * 3600 * 1000), "requestTimeTo": str(now_ms + 60_000)},
        expect_list=True)
    return verdict, why


def _rg_session_ok(page) -> bool:
    """프로브가 OK를 확증했는가 — **로그인 대기 루프 전용**.

    ★호스트 무관(location.origin same-origin). vendor-summary(m-wing) 감지로 정산 세션을 판단하면
    틀리므로(셀프리뷰 A), 정산 오리진에서 직접 판정한다. 빈 cfg 기본값으로 호출 가능.
    ★여기서만 보수적(판정 불가도 False)인 게 맞는 이유: 유일한 호출부 _rg_login_wait은 "로그인이
      **됐는가**"를 폴링하는 루프다. UNKNOWN에서 True를 주면 로그인 안 된 세션에 마커를 저장하고
      빠져나온다 — 즉 여기서 False는 '만료 선언'이 아니라 '아직 확증 못 했으니 더 기다린다'다.
      반면 만료를 **선언**하는 경로(진입·층2 루프·cmd_login 프로브)는 판정 불가를 로그아웃으로
      승격하면 안 되므로 전부 _rg_session_probe / _rg_session_verdict_confirmed를 쓴다.
    """
    verdict, why = _rg_session_probe(page)
    if verdict != _PROBE_OK:
        log.info("RG 세션 프로브 %s — %s", verdict, why)
    return verdict == _PROBE_OK


def _rg_loop_should_abort(verdict: str) -> bool:
    """층2 루프를 중단할지 — **AUTH일 때만** True.

    한 줄짜리를 굳이 함수로 뽑은 이유: 이게 이번 결함의 전부다(판정 불가를 중단 사유로 쓴 것).
    루프 안에 인라인으로 두면 브라우저 없이 검증할 수 없어 변이 테스트가 불가능하다.
    """
    return verdict == _PROBE_AUTH


def _rg_session_verdict_confirmed(page, *, delay_s: float = 5.0) -> str:
    """층2 루프용 — 실패 시 짧은 지연 후 1회 재확인하고 **판정값**을 그대로 돌려준다.

    반환은 _PROBE_OK / _PROBE_AUTH / _PROBE_UNKNOWN. 호출자는 AUTH일 때만 중단한다.
    ★UNKNOWN을 중단 사유로 쓰지 않는 이유(계약 판단기준): 프로브 오판의 비용이 오검출 비용보다
      크다. 오판이면 남은 주기가 통째로 버려지고 "로그인 필요"라는 거짓 사유가 기록되지만,
      정말로 세션이 죽었다면 이어지는 다운로드가 어차피 실패해 rc=1(재시도 가능)로 잡힌다.
      즉 UNKNOWN에서 계속 진행하는 쪽이 어느 경우에도 더 나쁘지 않다.
    """
    verdict, why = _rg_session_probe(page)
    if verdict == _PROBE_OK:
        return verdict
    log.info("RG 세션 프로브 1차 %s — %s (%.0f초 후 재확인)", verdict, why, delay_s)
    time.sleep(delay_s)
    verdict2, why2 = _rg_session_probe(page)
    if verdict2 == _PROBE_OK:
        log.info("RG 세션 프로브 재확인 ok — 1차는 일시 실패였다(중단하지 않는다).")
    else:
        log.warning("RG 세션 프로브 재확인도 %s — %s", verdict2, why2)
    return verdict2


def _rg_login_wait(page, ctx, state: str, secs: int, *, cdp: bool = False) -> bool:
    """정산 페이지에서 사용자 로그인 자동 감지(status/api 200). 성공 시 state 저장. (데몬 회복 경로)

    ★codex 1R[P2](P4 후속, _login_wait_loop와 동일 수리): 저장 직후 파일이 실제로 생겼는지
    확인 — CDP 마커 저장이 조용히 실패(OSError)했는데 True를 리턴하면 게이트가 다음 회차도 막힌다.
    """
    waited = 0
    while waited < secs:
        try:
            page.wait_for_timeout(5000)
            waited += 5
            if _rg_session_ok(page):
                _save_state(ctx, state, cdp=cdp)
                if os.path.exists(state):
                    return True
                log.error("RG 로그인은 확인됐으나 세션 마커 저장 실패(%s) — 재시도 대기", state)
        except Exception as e:  # noqa: BLE001
            if "closed" in str(e).lower():
                log.error("브라우저 창이 닫혔습니다 — RG 로그인 미완료.")
                return False
    return False


def _do_rg_run(cfg: dict, state: str, login_wait_secs: int = 0) -> int:
    """state 로드 → 정산 페이지 → 층1 push → **결손 조회** → 결손 주기만 다운로드 → prod push.

    push 설정 필수(다운로드만 하고 버릴 이유 없음). 세션 판정·만료 회복은 정산 status/api 기반.
    반환: 0=완주 / 1=실패(재시도) / RC_RETRY_LATER=dup에 막힘(긴 백오프) /
          RC_LOGIN_REQUIRED=층2 루프 중 세션 만료(재시도 제외, 창 유지).
    ★회차 시간예산(_RG_LEASE_TTL_S 주석)에 걸려 대상을 다 못 돌아도 **0(완주)**이다 — 이번 몫은
      실제로 진전됐고 남은 결손은 다음 버튼/크론이 이어받는다(D2의 '나머지는 다음 회차'). 1로
      두면 재시도 예산 3회를 갉아먹을 뿐 더 받지 못한다.
    """
    # ★시계는 **함수 진입 시각**부터다(리뷰 R5). TTL은 claim부터 도는데 cmd_poll은 claim 직후
    #   곧바로 여기를 부르므로 진입 ≈ claim이다. 브라우저 기동(프로필 락 대기·CDP 접속·goto)과
    #   층1 push·결손 조회에만 최악 ~300초가 든다 — 그걸 시계 밖에 두면 예산을 다 지켜도
    #   claim 기준으로는 TTL을 넘겨 보고가 stale로 폐기된다(창 재출현).
    run_started = time.monotonic()
    if not _push_configured(cfg):
        log.error("RG 다운로드엔 push 설정(account_key·prod_base_url·ingest_token) 필요.")
        return 2
    vendor_id = str(cfg.get("vendor_id") or "").strip()
    if not vendor_id:
        log.error("RG 다운로드엔 설정에 vendor_id 필요(예 A01564720).")
        return 2
    report_types = cfg.get("rg_report_types") or RG_REPORT_TYPES_DEFAULT
    # ★rg_max_periods(구 '최근 1주만')는 더 이상 읽지 않는다 — 그 상한이 바로 영구 공백의 원인이다.
    #   config에 남아 있어도 무시된다. 이제 상한은 '결손 중 몇 개까지 한 회차에'(rg_max_targets)다.
    max_targets = _cfg_int(cfg, "rg_max_targets", _RG_DEFAULT_MAX_TARGETS)
    poll_timeout = int(cfg.get("rg_poll_timeout_s", _RG_POLL_TIMEOUT_S))

    cdp = _cdp_mode(cfg)
    owner = _ChromeOwner()   # 창 소유권 — 로그인 미완료 시 창을 남기기 위해 직접 만든다
    pushed = failed = skipped = 0
    session_lost = False     # 층2 루프 도중 세션 만료(D3) — 남은 주기 중단 + 로그인 필요로 보고
    try:
        with sync_playwright() as p:
            with _chrome(p, cfg, state, owner=owner) as (page, ctx, save):
                page.goto(RG_DASH_URL, wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(3500)   # Cloudflare/Akamai JS 챌린지 안정화
                entry_verdict, entry_why = _rg_session_probe(page)
                if entry_verdict != _PROBE_OK:
                    log.info("RG 진입 프로브 %s — %s", entry_verdict, entry_why)
                if entry_verdict == _PROBE_AUTH:
                    # ★AUTH(로그아웃 확증)일 때만 로그인 창을 띄운다. 종전엔 업스트림 500도
                    #   여기로 떨어져 멀쩡한 세션에 매 회차 '세션 만료 의심'을 오보했다
                    #   (08-03 12:12~13:07 전 회차). 창은 열어 두므로 사람이 늦게 로그인해도
                    #   다음 재시도가 자동으로 이어받는다.
                    owner.keep_open = True    # 로그인할 창이 필요 → 닫지 않음
                    if login_wait_secs <= 0:
                        log.error("RG: 로그인 필요(정산 세션 로그아웃 확증). 'login' 또는 데몬 로그인 필요.")
                        return 1
                    log.info("RG: 로그아웃 확증 — 창에서 로그인하세요(자동 감지, 최대 %d초).", login_wait_secs)
                    if not _rg_login_wait(page, ctx, state, login_wait_secs, cdp=cdp):
                        log.error("RG: 로그인 감지 실패(창은 열어 둠 — 다음 재시도가 이어받는다).")
                        return 1
                    owner.keep_open = False   # 로그인 성공 → 평소대로 작업 후 창 닫음
                elif entry_verdict == _PROBE_OK:
                    save()  # 세션 유효 → 회전 쿠키 보존 (CDP: no-op)
                # UNKNOWN은 여기서 멈추지 않는다 — 바로 아래 status/api가 예산 안에서 재시도하며
                #   진짜 로그아웃이면 거기서 AUTH가 나온다(설계된 2차 관문).

                # 층1: status/api raw를 fetch(5xx는 재시도) → prod push → 같은 raw로 열거.
                try:
                    raw_verdict, raw = _rg_fetch_status_raw(page, cfg)
                except Exception as e:  # noqa: BLE001 — 응답 비정상/챌린지 → 실패 보고
                    log.error("RG status/api fetch 실패(정산 페이지 same-origin 200 미확인?): %s", e)
                    return 1
                if raw_verdict == _PROBE_AUTH:
                    owner.keep_open = True    # 사람이 로그인할 창을 남긴다
                    log.error("RG: status/api에서 로그아웃 확증 — 로그인 필요.")
                    return RC_LOGIN_REQUIRED
                if not isinstance(raw, dict):
                    # 업스트림 500 지속 — 로그아웃이 아니므로 **재시도 대상**이다(요청 소멸 금지).
                    log.error("RG status/api 지속 실패(업스트림 장애로 판단) — 재시도 대상.")
                    return 1
                # 계정 단위 수수료 push는 엑셀 흐름과 독립(fail-soft): 실패해도 다운로드는 계속.
                # ★단 결손 판정은 층1 DB를 소스로 삼는다 — push가 실패한 회차엔 최신 주기가 층1에
                #   없어 "결손 아님"으로 보이고 다운로드가 0이 된다(리뷰 R1 [P2-3]).
                #   그럴 땐 결손 조회를 건너뛰고 폴백(최신 1주기)을 타서 기존 동작을 지킨다.
                layer1_pushed = True
                if _push_configured(cfg):
                    layer1_pushed = (_rg_push_status(cfg, raw) == 0)
                try:
                    periods = _rg_enumerate_group_keys(raw, vendor_id)
                except Exception as e:  # noqa: BLE001 — 응답 비정상 → 실패 보고
                    log.error("RG status/api 열거 실패: %s", e)
                    return 1
                if not periods:
                    log.info("RG: status/api에 정산주기 없음 — 다운로드 건너뜀.")
                    return 0
                periods.sort(key=lambda x: x["period_end"], reverse=True)
                # 결손 주도 선택(D1): prod에 "층2가 빈 주기"를 묻고 그것만 받는다. 조회 실패 시
                #   기존 동작(최신 1주기)으로 안전 저하 — 수집이 아예 멈추면 안 된다(D5).
                if layer1_pushed:
                    gaps = _prod_rg_layer2_gaps(cfg, report_types, _rg_status_days(cfg))
                else:
                    log.warning("RG: 층1 push 실패 회차 — 결손 조회 생략하고 최신 1주기로 폴백.")
                    gaps = None
                targets = _rg_select_targets(periods, gaps, report_types, max_targets)
                # ★관측(리뷰 R1 [P2-5]): '결손 0'과 '결손은 있는데 하나도 매칭 못 함'을 같은
                #   문장으로 찍으면, 날짜 규칙이 어긋난 전면 고장이 완전히 건강한 로그로 보인다
                #   (실제로 [P1-1]이 그랬다). 결손 건수와 매칭 건수를 함께 남긴다.
                gap_n = len((gaps or {}).get("gaps") or []) if isinstance(gaps, dict) else -1
                if not targets:
                    log.info("RG: 다운로드 대상 없음 — 열거 주기 %d개 / prod 결손 %s / 매칭 0개.",
                             len(periods), gap_n if gap_n >= 0 else "조회실패")
                    if gap_n > 0:
                        log.error("RG: ★결손 %d개인데 열거와 하나도 매칭되지 않았다 — 주기 날짜 "
                                  "규칙 불일치 의심(period_end↔recognition_date_to).", gap_n)
                else:
                    log.info("RG: 열거 %d개 / prod 결손 %s → 대상 %d개(상한 %d) %s",
                             len(periods), gap_n if gap_n >= 0 else "조회실패",
                             len(targets), max_targets,
                             [(t["group_key"], t["report_types"]) for t in targets])

                # 회차 시간 예산 — 상수 근거는 _RG_LEASE_TTL_S 주석. 리포트 1건 최악을 뺀 값이라
                #   '검사 통과 직후 1건 최악'이어도 TTL 안에서 끝난다.
                budget_s = max(60, _RG_LEASE_TTL_S - (poll_timeout + _RG_ONE_REPORT_TAIL_S)
                               - _RG_BUDGET_SLACK_S)
                planned = sum(len(t["report_types"]) for t in targets)
                done = 0
                budget_out = False

                base_ms = int(time.time() * 1000)
                idx = 0
                for n, t in enumerate(targets):
                    # ★주기 사이 세션 재확인(D3): 층2는 주기×리포트당 최대 300초 폴링이라, 진입 시
                    #   1회 확인만으로는 루프 도중 만료를 못 잡는다 — 남은 주기가 통째로 헛돈다.
                    #   이미 받아 push한 주기는 그대로 두고(멱등 적재), 남은 주기만 중단한다.
                    #   ★blip 1회로 승격하지 않는다(리뷰 [P2-2]) — _rg_session_verdict_confirmed 참조.
                    #   ★2026-08-03: **판정 불가(UNKNOWN)로는 중단하지 않는다.** 종전엔 비200·깨진
                    #     JSON·예외까지 전부 '세션 만료'로 승격해, 멀쩡한 세션에서 남은 주기가 통째로
                    #     버려지고 "로그인 필요"라는 거짓 사유가 기록됐다(라이브 3/3 재현 — 매번
                    #     다운로드 성공 6~7초 뒤). 대조군도 성립: 같은 세션·2분 간격에 대상 2개면
                    #     중단, 1개면 성공. 즉 끊은 건 세션이 아니라 이 프로브였다.
                    #     rider(PRODUCT_SIZE_COMPARISON)가 항상 슬롯 #1을 차지하므로 진짜 정산
                    #     결손은 구조적으로 항상 #2 이하 → 이 오판 하나가 결손 충전을 영구히 막았다.
                    if n > 0:
                        _verdict = _rg_session_verdict_confirmed(page)
                        if _rg_loop_should_abort(_verdict):
                            log.error("RG: 층2 루프 중 세션 만료 — 남은 주기 %d개 중단(로그인 필요).",
                                      len(targets) - n)
                            # keep_open은 CDP 모드에서만 실효(레거시 _chrome 경로는 finally에서
                            #   무조건 닫는다) — 라이브는 CDP라 의도대로 동작한다(리뷰 [P2-9]).
                            owner.keep_open = True   # 사람이 로그인할 창을 남긴다
                            session_lost = True
                            break
                        if _verdict != _PROBE_OK:
                            log.warning(
                                "RG: 주기 사이 세션 프로브 판정 불가 — 중단하지 않고 계속한다"
                                "(정말 죽었다면 이어지는 다운로드가 rc=1로 잡혀 재시도된다).")
                    for rt in t["report_types"]:
                        # ★예산 검사는 '리포트 하나를 시작하기 전마다'다(리뷰 R3 [P2-1]).
                        #   주기 단위로만 재면, 예산을 아슬하게 통과한 마지막 주기가 리포트 여러 건을
                        #   통째로 돌려 TTL을 넘길 수 있다. 첫 1건은 무조건 실행(floor) — 아무것도
                        #   못 하고 끝나면 결손이 영영 안 준다.
                        if done and time.monotonic() - run_started > budget_s:
                            log.info("RG: 회차 시간예산 %ds 초과 — 남은 결손 %d건은 다음 회차로"
                                     "(자연 롤링).", budget_s, planned - done)
                            budget_out = True
                            break
                        req_time = base_ms + idx
                        idx += 1
                        done += 1
                        got = _rg_download_one(page, t["group_key"], rt, req_time, poll_timeout)
                        if got is RG_DUP_SKIP:
                            skipped += 1   # 실패 아님 — 재시도해도 계속 dup이다
                            continue
                        if not got:
                            failed += 1
                            continue
                        if _rg_push_xlsx(cfg, got["url"], rt, t["group_key"]) == 0:
                            pushed += 1
                        else:
                            failed += 1
                    if budget_out:
                        break
                if not session_lost:
                    save()  # 회전 쿠키 보존 (CDP: no-op)
                # ★세션이 죽은 뒤엔 저장하지 않는다 — 로그아웃된 쿠키로 state 파일을 덮으면
                #   다음 회차가 물려받을 게 없다(창은 열어 두었으니 사람이 로그인하면 갱신된다).
    except Exception as e:  # noqa: BLE001 — 브라우저 오류는 1로 보고(데몬은 죽지 않음)
        log.error("RG 브라우저 실행 오류: %s", e)
        return 1
    log.info("RG 다운로드 완료 — push 성공 %d / 실패 %d / 중복스킵 %d", pushed, failed, skipped)
    # ★세션 만료가 최우선(D3): 재시도해도 실패하고 창만 반복해서 뜬다 → lease 계약의
    #   login_required 경로로 보고한다(재시도 예산 소진이 아니라 요청 소멸 + 사람 안내).
    if session_lost:
        return RC_LOGIN_REQUIRED
    if failed:
        return 1
    # ★dup만 있고 실패가 없어도 '완료'가 아니다(codex 4R[P1]): 그 리포트는 이번에도 못 받았다.
    #   완료로 보고하면 요청이 닫혀 영영 안 받는다 → 재시도 대상(긴 백오프)으로 돌린다.
    return RC_RETRY_LATER if skipped else 0


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


def cmd_chrome(cfg: dict) -> int:
    """CDP용 전용 Chrome 인스턴스 수동 실행(--remote-debugging-port).

    Wing 자동화 전용 프로필 사용 → 기존 Chrome과 충돌 없음.
    실행 후 브라우저에서 쿠팡 로그인 → 'login' 명령으로 세션 감지.
    커맨드라인은 per-fetch 기동과 동일(_chrome_argv) — 갈리면 세션/핑거프린트가 갈린다.

    설정 키(~/.ohisell_wing_fetcher.json):
        cdp_port       : CDP 포트 번호 (기본 9222)
        cdp_profile    : Chrome 프로필 경로 (기본 ~/.ohisell_wing_chrome)
    """
    port = int(cfg.get("cdp_port", 9222))
    profile = os.path.expanduser(cfg.get("cdp_profile", "~/.ohisell_wing_chrome"))
    # ★수동 경로도 같은 프로필 lock 안에서(codex R2): 여기서 락을 빼면 `chrome`을 두 번 치거나
    #   fetch와 겹칠 때 둘 다 "비어 있음"을 보고 Singleton을 지운 뒤 이중 기동 → 프로필 손상.
    with _profile_launch_lock(profile, int(cfg.get("chrome_launch_lock_timeout_s", 90))):
        if _cdp_alive(port):
            log.info("CDP Chrome(%d) 이미 실행 중 — 그 창에서 로그인하세요.", port)
            return 0
        if _profile_chrome_alive(profile):
            log.error("프로필 점유 중이나 CDP(%d) 미응답 — 그 Chrome을 먼저 종료하세요(%s).", port, profile)
            return 1
        proc = _launch_chrome(cfg)
        if proc is None:
            return 1
    log.info(
        "Chrome 실행됨 (PID %d, CDP port %d, 프로필 %s)\n"
        "  1. 브라우저에서 쿠팡 계정으로 로그인\n"
        "  2. 완료 후: python3 wing_browser_fetcher.py login",
        proc.pid, port, profile,
    )
    return 0


def _cdp_alive(port: int) -> bool:
    """CDP 디버깅 엔드포인트가 응답하면 True (/json/version HTTP 프로브).

    TCP LISTEN만 보면 행(hang)·기동중 Chrome을 살아있다 오판할 수 있어
    실제 CDP 응답을 확인한다.
    """
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=3
        ) as r:
            return r.status == 200
    except Exception:
        return False


# PID 재사용 판별 허용오차: ps etime은 1초 해상도라 '시작 == lock 생성' 동시간대를 흡수한다.
# 재사용 PID는 원 Chrome이 죽은 뒤에야 배정되므로 실제로는 분·시간 단위로 벌어진다.
_PID_REUSE_TOLERANCE_S = 5


def _proc_start_epoch(pid: int) -> float | None:
    """PID의 프로세스 시작 시각(epoch 초). 확인 불가면 None.

    macOS ps에는 `etimes`(초)가 없다 — `etime`([[dd-]hh:]mm:ss)을 파싱해 now-경과로 환산한다.
    (`lstart`는 로케일 의존 텍스트라 파싱이 더 취약하다.)
    """
    try:
        et = subprocess.run(
            ["ps", "-o", "etime=", "-p", str(pid)],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return None
    m = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", et)
    if not m:
        return None
    d, h, mi, sec = m.groups()
    elapsed = int(d or 0) * 86400 + int(h or 0) * 3600 + int(mi) * 60 + int(sec)
    return time.time() - elapsed


def _profile_owner_pid(profile: str) -> int | None:
    """이 프로필을 **지금** 점유 중인 Chrome 브라우저 프로세스 PID. stale이면 None.

    Chrome은 user-data-dir 안 SingletonLock을 'hostname-PID'로 건다. 그 PID가 곧 DevTools
    포트를 LISTEN하는 브라우저 프로세스다(Chromium ProcessSingleton / RemoteDebuggingServer).

    ★심볼릭링크만 믿으면 안 된다(codex R5): Chrome이 크래시하면 SingletonLock이 남고, macOS가
    그 PID를 무관한 프로세스에 재사용할 수 있다. 그 프로세스가 마침 우리 CDP 포트를 LISTEN하면
    PID가 같다는 이유로 adopt돼 버린다. 그래서 4중으로 확인한다:

      ① PID 파싱  ② 그 PID가 살아 있음
      ③ ★PID 재사용 아님 — 그 프로세스의 **시작 시각이 lock 생성 시각보다 앞**이어야 한다.
         정상 Chrome은 뜬 직후 lock을 걸므로 start < lock_mtime이다. 반대로 재사용된 PID는
         **lock이 만들어진 뒤에** 시작했으므로 start > lock_mtime이 되어 걸러진다.
         이것이 재사용을 직접 판별하는 유일한 조건이다(codex R6: ①②는 재사용으로 자동 충족되고
         cmdline은 ps의 argv flatten 때문에 단독 판별자가 될 수 없다 — R4).
      ④ 그 프로세스가 우리 프로필로 도는 Chrome임(cmdline) — ③ 위에 얹는 방어 심화.

    (호스트명은 검사하지 않는다 — macOS는 `.local` 이름이 바뀌는 일이 있어 오거부 위험이 더 크다.)
    """
    prof = os.path.expanduser(profile)
    lock = os.path.join(prof, "SingletonLock")
    try:
        target = os.readlink(lock)
        lock_mtime = os.lstat(lock).st_mtime      # 심볼릭링크 자체의 생성 시각
    except OSError:
        return None
    try:
        pid = int(target.rsplit("-", 1)[-1])       # 호스트명에 '-'가 있어도 마지막만
    except ValueError:
        return None
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)                            # 시그널 0 = 존재만 확인
    except ProcessLookupError:
        return None                                # 죽은 PID = 크래시 잔재 lock
    except PermissionError:
        return None                                # 타 유저 소유 = 우리 Chrome 아님
    started = _proc_start_epoch(pid)
    if started is None:
        return None                                # 시작 시각 확인 불가 → 재사용 배제 못 함
    if started > lock_mtime + _PID_REUSE_TOLERANCE_S:
        # lock보다 나중에 시작한 프로세스 = PID 재사용. 원래 Chrome은 이미 죽었다.
        return None
    try:
        cmdline = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except Exception:  # noqa: BLE001 — 확인 불가 → 소유 미확정
        return None
    return pid if _cmdline_has_profile(cmdline, profile) else None


def _cmdline_has_profile(cmdline: str, profile: str) -> bool:
    """cmdline에 '--user-data-dir=<정확히 이 프로필>'이 있는지 — 접두 오탐 차단.

    단순 substring이면 프로필 `/tmp/p`가 남의 `/tmp/profile`에 매칭돼 무관한 Chrome을 우리 것으로
    오인한다(codex R2 신규 P1). 값 뒤가 공백이거나 줄 끝이어야 한다.
    """
    prof = os.path.expanduser(profile).rstrip("/")
    # 선행 경계도 필수(codex R3): 뒤만 보면 `--some-option=--user-data-dir=/tmp/p`나 URL 안에
    # 같은 문자열이 섞인 무관한 인자가 '우리 프로필 확인'으로 통과해 fail-closed를 뚫는다.
    return re.search(r"(?:^|\s)--user-data-dir=" + re.escape(prof) + r"/?(?=\s|$)",
                     cmdline) is not None


def _profile_chrome_alive(profile: str) -> bool:
    """프로필 디렉터리를 점유 중인 살아있는 Chrome이 있는지 — SingletonLock PID 생존 확인.

    CDP가 행(hang)이거나 기동 중이라 _cdp_alive가 거짓이어도, Chrome 프로세스가
    user-data-dir를 점유 중이면 lock 청소·중복 launch가 프로필을 손상시킨다(codex P2#1).
    Chrome의 SingletonLock 심볼릭링크 타깃은 'hostname-PID' 형식 → 끝 PID로 생존 판정.
    """
    # ※_profile_owner_pid와 로직이 겹치지만 **통합하지 않는다**: 이쪽은 PermissionError(타 유저
    #   Chrome이 프로필 점유)를 "점유 중"으로 봐서 기동을 막아야 안전하고, 저쪽은 "우리 것 아님"으로
    #   봐서 adopt를 막아야 안전하다. 실패의 안전한 방향이 서로 반대다.
    lock = os.path.join(profile, "SingletonLock")
    try:
        target = os.readlink(lock)  # 예: "Jino-MacBookPro.local-19029"
    except OSError:
        return False  # 링크 없음 = 점유 없음
    try:
        pid = int(target.rsplit("-", 1)[-1])  # 호스트명에 '-' 있어도 마지막만
    except ValueError:
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)  # 시그널 0 = 존재만 확인
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # 타유저 소유(우리 Chrome은 동일 유저) → 점유 단정 말고 cmdline으로 검증(codex R3)
    # PID 생존만으론 부족 — 크래시 후 PID 재사용(무관 프로세스)·타호스트/유저 lock이면
    # 무한 adopt로 크래시 복구가 무력화된다(codex R2 P2). 그 PID의 cmdline에 이 프로필
    # user-data-dir가 있어야만 "우리 Chrome 점유"로 인정. 불일치면 stale → 청소·재기동.
    import subprocess

    try:
        cmdline = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except Exception:
        return False  # cmdline 확인 불가 → 안전하게 stale 취급(청소·재기동 허용)
    return _cmdline_has_profile(cmdline, profile)


def main() -> None:
    _install_signal_cleanup()   # SIGTERM(launchd bootout) 시 내가 띄운 Chrome 회수
    cfg = load_config()
    arg = sys.argv[1] if len(sys.argv) >= 2 else ""
    if arg == "login":
        sys.exit(cmd_login(cfg))
    if arg == "chrome":
        sys.exit(cmd_chrome(cfg))
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
