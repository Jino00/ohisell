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

import base64
import contextlib
import fcntl
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
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

# report/SALES — 날짜 범위 받아 날짜별 확정 광고비/전환매출 반환(과거일만, 오늘 제외).
# payload {start,end} epoch ms. 응답 {result:{<날짜epoch>:{DELIVERED_AD_COST,AD_ATTRIBUTED_SALES,...}}}.
_SALES_FETCH_JS = """async (payload) => {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 25000);
  try {
    const r = await fetch('https://advertising.coupang.com/marketing/cmg-api/report/SALES', {
      method: 'POST',
      headers: {'content-type': 'application/json', 'accept': 'application/json, text/plain, */*'},
      body: JSON.stringify(payload),
      credentials: 'include',
      signal: ctrl.signal,
    });
    return { status: r.status, body: await r.text() };
  } finally { clearTimeout(t); }
}"""


# ── Billboard 보고서(옵션×일별 광고비) — 레퍼런스 16 ─────────────────────────
# 옵션ID×일별 광고비의 유일한 소스. report/SALES(vendor합계)·report/cost(오늘)와 별개.
# 흐름: getCampaignList → requestReport(daily/keyword) → reportList 폴링 → excel-report 다운로드.
GRAPHQL_URL = "https://advertising.coupang.com/marketing-reporting/v2/graphql"
EXCEL_REPORT_URL = "https://advertising.coupang.com/marketing-reporting/v2/api/excel-report?id="
OPTION_LAST_PATH = Path(os.path.expanduser("~/.ohisell_ad_option_last"))  # 일1회 마커(KST date)

_Q_CAMPAIGNS = (
    "query GetCampaignListInBillboard($startDate: Int!, $endDate: Int!, $reportType: ReportType!) {\n"
    "  getCampaignList(\n    startDate: $startDate\n    endDate: $endDate\n    reportType: $reportType\n"
    "  ) {\n    id\n    name\n    __typename\n  }\n}\n"
)
_M_REQUEST_REPORT = (
    "mutation ($startDate: Int!, $endDate: Int!, $campaignIds: [ID], $reportType: ReportType!, "
    "$dateGroup: DateGroup!, $granularity: Granularity, $excludeIfNoClickCount: Boolean) {\n"
    "  requestReport(\n    data: {startDate: $startDate, endDate: $endDate, campaignIds: $campaignIds, "
    "reportType: $reportType, dateGroup: $dateGroup, granularity: $granularity, "
    "excludeIfNoClickCount: $excludeIfNoClickCount}\n  ) {\n    ...ReportRequest\n    __typename\n  }\n}\n\n"
    "fragment ReportRequest on ReportRequest {\n  id\n  requestDate\n  startDate\n  endDate\n  reportType\n"
    "  dateGroup\n  granularity\n  excludeIfNoClickCount\n  campaignName\n  campaignCount\n  status\n"
    "  isLargeReport\n  schedule {\n    scheduleType\n    title\n    __typename\n  }\n  __typename\n}\n"
)
_Q_REPORT_LIST = (
    "query ($reportType: ReportType!, $page: Int!, $pageSize: Int!, $duration: Int!, "
    "$onlyScheduledReport: Boolean) {\n  reportList(\n    data: {reportType: $reportType, page: $page, "
    "pageSize: $pageSize, duration: $duration, onlyScheduledReport: $onlyScheduledReport}\n  ) {\n"
    "    ...ReportList\n    __typename\n  }\n}\n\nfragment ReportList on ReportList {\n  page\n  pageSize\n"
    "  total\n  duration\n  onlyScheduledReport\n  reports {\n    id\n    requestDate\n    startDate\n"
    "    endDate\n    reportType\n    dateGroup\n    granularity\n    excludeIfNoClickCount\n"
    "    campaignName\n    campaignCount\n    status\n    isLargeReport\n    schedule {\n      title\n"
    "      scheduleType\n      createDay\n      requestDate\n      expireAt\n      __typename\n    }\n"
    "    __typename\n  }\n  __typename\n}\n"
)

# same-origin GraphQL POST(배열 배치 형태로 전송 — 캡처와 동일).
_GQL_JS = """async (payload) => {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 30000);
  try {
    const r = await fetch('https://advertising.coupang.com/marketing-reporting/v2/graphql', {
      method: 'POST',
      headers: {'content-type': 'application/json', 'accept': 'application/json'},
      body: JSON.stringify(payload),
      credentials: 'include',
      signal: ctrl.signal,
    });
    return { status: r.status, body: await r.text() };
  } finally { clearTimeout(t); }
}"""

# excel-report 다운로드 → arrayBuffer → base64(바이너리 안전). 파이썬에서 decode.
_DL_JS = """async (url) => {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 60000);
  try {
    const r = await fetch(url, { credentials: 'include', signal: ctrl.signal });
    if (!r.ok) return { status: r.status, b64: null };
    const buf = await r.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let bin = '';
    const CH = 0x8000;
    for (let i = 0; i < bytes.length; i += CH) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
    }
    return { status: r.status, b64: btoa(bin) };
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


def _sales_payload(cfg: dict) -> dict:
    """report/SALES 날짜 범위(최근 N일, 기본 30) — KST 기준 epoch ms {start,end}.

    S5b/D-13: 7→30일. report/SALES는 단일 POST(저비용)이고 날짜당 1행 교체(idempotent)라
    윈도우를 넓혀도 안전. 긴 outage도 30일 내 자가복구 + 과거(5/x) 자연 백필.
    """
    days = int(cfg.get("sales_days", 30))
    now = datetime.now(KST)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight - timedelta(days=days - 1)
    end = midnight + timedelta(days=1)  # 오늘 자정+1일(상한, report/SALES는 오늘 제외 반환)
    return {"start": int(start.timestamp() * 1000), "end": int(end.timestamp() * 1000)}


def _push_sales(cfg: dict, sales_body: str) -> None:
    """report/SALES 응답 → 과거 확정일(오늘 제외) days[] 로 prod ingest push.

    실패해도 report/cost(오늘) push 결과에 영향 없음 — best-effort 보강.
    """
    try:
        data = json.loads(sales_body or "")
    except (ValueError, TypeError):
        log.warning("report/SALES JSON 파싱 실패 — 과거일 갱신 건너뜀")
        return
    result = data.get("result", data) if isinstance(data, dict) else {}
    today = datetime.now(KST).date()
    days = []
    for epoch_ms, m in (result or {}).items():
        if not isinstance(m, dict):
            continue
        try:
            d = datetime.fromtimestamp(int(epoch_ms) / 1000, KST).date()
        except (ValueError, TypeError, OverflowError):
            continue
        if d >= today:  # 오늘은 report/cost(running)이 담당 — 덮어쓰지 않음
            continue
        days.append({
            "date": d.isoformat(),
            "ad_spend": int(m.get("DELIVERED_AD_COST") or 0),       # 집행(PA)
            "all_cost": int(m.get("ALL_DELIVERED_AD_COST") or 0),   # 전체(비-PA 포함, S5a/D-15)
            "conv_sales": int(m.get("AD_ATTRIBUTED_SALES") or 0),
        })
    if not days:
        log.info("report/SALES: 갱신할 과거 확정일 없음")
        return
    try:
        pr = requests.post(
            cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/ad-cost/ingest",
            json={"days": days},
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            timeout=20,
        )
        pr.raise_for_status()
    except requests.RequestException as e:
        log.warning("report/SALES push 실패(무시): %s", str(e)[:120])
        return
    log.info("확정일 push: %s", [(d["date"], d["ad_spend"]) for d in days])


def _gql(page, payload: list) -> dict | None:
    """GraphQL POST(배열 배치) → 첫 응답의 data dict. 실패 시 None."""
    try:
        res = page.evaluate(_GQL_JS, payload)
    except Exception as e:
        log.warning("GraphQL evaluate 실패: %s", str(e)[:100])
        return None
    if not res or res.get("status") not in (200, 201):
        log.warning("GraphQL status=%s body=%s", res and res.get("status"), (res or {}).get("body", "")[:120])
        return None
    try:
        parsed = json.loads(res.get("body") or "")
    except (ValueError, TypeError):
        return None
    item = parsed[0] if isinstance(parsed, list) and parsed else parsed
    if isinstance(item, dict) and item.get("errors"):
        log.warning("GraphQL errors: %s", str(item["errors"])[:160])
    return item.get("data") if isinstance(item, dict) else None


def _option_window(cfg: dict) -> tuple[int, int]:
    """옵션 보고서 날짜 범위(최근 N일, 어제까지) — YYYYMMDD 정수 (start, end).

    D-13 후속(2026-06-14): option_days 기본 7→30. 순이익의 PA 광고비(`ad_spend`)는
    옵션 소스(CoupangAdOptionDaily)에서 차감되는데 7일이면 8~30일차 PA 차감이 누락돼
    순이익이 과대였다(비-PA는 30일 report/SALES로 이미 차감). 30일로 맞춰 PA 커버리지를
    비-PA(sales_days 30)와 정렬한다. Billboard 보고서 생성은 무거우나 일1회(_option_due_today)
    게이트라 비용 제한적 + _do_run이 메인 push 후에 수행해 UI 블록 없음.
    """
    days = int(cfg.get("option_days", 30))
    today = datetime.now(KST).date()
    end = today - timedelta(days=1)            # 어제(오늘은 미확정 → 제외)
    start = end - timedelta(days=days - 1)
    return int(start.strftime("%Y%m%d")), int(end.strftime("%Y%m%d"))


def _fetch_option_report(page, cfg: dict, poll_timeout_s: int = 300):
    """Billboard 보고서(옵션×일별 keyword XLSX)를 생성·폴링·다운로드 → (filename, bytes)|None.

    레퍼런스 16. 인증된 page에서 same-origin 호출. 실패해도 None 반환(메인 push에 무영향).
    """
    start_i, end_i = _option_window(cfg)
    # 1) 캠페인 목록
    data = _gql(page, [{
        "operationName": "GetCampaignListInBillboard",
        "variables": {"startDate": start_i, "endDate": end_i, "reportType": "pa"},
        "query": _Q_CAMPAIGNS,
    }])
    campaigns = (data or {}).get("getCampaignList") if data else None
    if not campaigns:
        log.warning("옵션보고서: 캠페인 목록 없음 — 건너뜀")
        return None
    campaign_ids = [str(c["id"]) for c in campaigns if c.get("id")]

    # 2) 보고서 생성 요청
    data = _gql(page, [{
        "variables": {
            "reportType": "pa", "startDate": start_i, "endDate": end_i,
            "dateGroup": "daily", "granularity": "keyword",
            "excludeIfNoClickCount": True, "campaignIds": campaign_ids,
        },
        "query": _M_REQUEST_REPORT,
    }])
    req = (data or {}).get("requestReport") if data else None
    report_id = str(req["id"]) if req and req.get("id") else None
    if not report_id:
        log.warning("옵션보고서: requestReport id 없음 — 건너뜀")
        return None
    log.info("옵션보고서 요청 id=%s (%s~%s, 캠페인 %d개, status=%s)",
             report_id, start_i, end_i, len(campaign_ids), req.get("status"))

    # 3) 완료 폴링
    waited = 0
    completed = False
    while waited < poll_timeout_s:
        page.wait_for_timeout(3000)
        waited += 3
        data = _gql(page, [{
            "variables": {"reportType": "pa", "page": 1, "pageSize": 20,
                          "duration": 90, "onlyScheduledReport": False},
            "query": _Q_REPORT_LIST,
        }])
        reports = ((data or {}).get("reportList") or {}).get("reports") if data else None
        mine = next((r for r in (reports or []) if str(r.get("id")) == report_id), None)
        if mine and str(mine.get("status")).lower() == "completed":
            completed = True
            break
    if not completed:
        log.warning("옵션보고서: %ds 내 완료 안됨(id=%s) — 건너뜀", poll_timeout_s, report_id)
        return None

    # 4) 다운로드
    try:
        dl = page.evaluate(_DL_JS, EXCEL_REPORT_URL + report_id)
    except Exception as e:
        log.warning("옵션보고서 다운로드 실패: %s", str(e)[:100])
        return None
    if not dl or not dl.get("b64"):
        log.warning("옵션보고서 다운로드 본문 없음 status=%s", dl and dl.get("status"))
        return None
    try:
        content = base64.b64decode(dl["b64"])
    except Exception:
        return None
    # xlsx 매직바이트 검증(codex P2): 세션 만료 시 HTML 로그인 페이지가 올 수 있음 → zip(PK) 아니면 거부.
    if content[:4] != b"PK\x03\x04":
        log.warning("옵션보고서 응답이 xlsx(zip)가 아님(세션 만료 가능) — 건너뜀: head=%r", content[:16])
        return None
    # 파일명: {vendor}_pa_daily_keyword_{start}_{end}.xlsx (파서 vendor_id 추출용).
    # vendor_ids는 광고노드ID(숫자)지만 파서는 'A로 시작' 파일명 필요 → 설정 ad_vendor_code 필수.
    # fail-closed(codex P1): 미설정 시 잘못된 vendor 귀속을 막기 위해 적재하지 않는다.
    vendor_code = str(cfg.get("ad_vendor_code") or "").strip()
    if not vendor_code:
        log.error("ad_vendor_code 설정 누락 — 옵션보고서 적재 중단(잘못된 vendor 귀속 방지). "
                  "~/.ohisell_ad_fetcher.json에 \"ad_vendor_code\":\"A01564720\" 추가 필요.")
        return None
    filename = f"{vendor_code}_pa_daily_keyword_{start_i}_{end_i}.xlsx"
    log.info("옵션보고서 다운로드 완료: %s (%d bytes)", filename, len(content))
    return filename, content


def _push_option_xlsx(cfg: dict, filename: str, content: bytes) -> bool:
    """옵션 XLSX 바이트 → prod option-ingest push(토큰 인증). 성공 시에만 True.

    실패 시 False 반환 → 호출자가 오늘 마커를 set하지 않아 다음 run에서 재시도(codex P1).
    """
    try:
        pr = requests.post(
            cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/ad-cost/option-ingest",
            data=content,
            headers={
                "X-Ingest-Token": cfg["ingest_token"],
                "X-Report-Filename": filename,
                "Content-Type": "application/octet-stream",
            },
            timeout=60,
        )
        pr.raise_for_status()
    except requests.RequestException as e:
        log.warning("옵션보고서 push 실패(다음 run 재시도): %s", str(e)[:140])
        return False
    try:
        info = pr.json()
    except ValueError:
        info = pr.text[:120]
    log.info("옵션보고서 push 성공: %s", info)
    return True


def _option_marker(cfg: dict) -> str:
    """마커 값 = 오늘(KST)|vendor_code|prod_base — 설정/대상이 바뀌면 재실행(codex P2)."""
    today = datetime.now(KST).date().isoformat()
    vendor_code = str(cfg.get("ad_vendor_code") or "A01564720")
    base = cfg.get("prod_base_url", "").rstrip("/")
    return f"{today}|{vendor_code}|{base}"


def _option_due_today(cfg: dict) -> bool:
    """옵션 보고서를 오늘·이 대상으로 아직 안 받았으면 True(일 1회 게이트)."""
    try:
        return OPTION_LAST_PATH.read_text(encoding="utf-8").strip() != _option_marker(cfg)
    except OSError:
        return True


def _mark_option_done(cfg: dict) -> None:
    try:
        OPTION_LAST_PATH.write_text(_option_marker(cfg), encoding="utf-8")
    except OSError:
        pass


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
    # 로그인페이지에서 곧장 SSO_LOGIN_URL로 goto하면 클라이언트 리다이렉트가
    # 진행 중이라 net::ERR_ABORTED가 난다. 빈 페이지로 리셋 후 이동(프로브에서 검증).
    try:
        page.goto("about:blank", timeout=10000)
    except Exception:
        pass
    try:
        page.goto(SSO_LOGIN_URL, wait_until="domcontentloaded", timeout=40000)
    except Exception as e:
        # ERR_ABORTED는 리다이렉트로 인한 중단일 수 있음 — 바로 실패로 보지 말고
        # 아래 URL 폴링 루프로 진입해 대시보드 착지 여부를 확인한다.
        log.warning("SSO goto 경고(폴링으로 확인): %s", str(e)[:120])
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


_KEYCHAIN_SERVICE = "ohisell-coupang-ad"  # security add-generic-password -s 이 값


def _keychain_get(account: str) -> str | None:
    """macOS Keychain에서 비밀번호를 읽는다(평문 저장·로그·git 없음).

    사전 등록(사용자가 1회):
      security add-generic-password -U -s ohisell-coupang-ad -a <아이디> -w
    실패(미등록/잠김) 시 None → 호출부는 수동 로그인으로 폴백.
    """
    if not account:
        return None
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-a", account, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            pw = r.stdout.strip()
            return pw or None
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("Keychain 조회 실패(무시): %s", str(e)[:80])
        return None


def _has_2fa_challenge(page) -> bool:
    """로그인 제출 후 SMS 2FA/인증번호 단계가 떴는지 감지(자동화 불가 → 사람 호출)."""
    try:
        html = page.content().lower()
    except Exception:
        return False
    # 본문에 인증번호 입력/SMS 단계 키워드가 '보이는' 상태인지 대략 판별
    for kw in ("인증번호", "verification code", "otp", "휴대폰으로 전송", "문자로 전송"):
        if kw in html:
            return True
    return False


def _try_auto_login(page, cfg: dict) -> bool:
    """keycloak 로그인 폼에 Keychain 자격증명을 자동입력·제출한다.

    반환 True  = 제출 성공(2FA 없음) → 호출부가 _login_wait_loop로 201 대기.
    반환 False = 자격증명 없음 / 2FA 발생 / 폼 못 찾음 → 호출부가 수동 로그인 폴백.
    비밀번호는 메모리에서만 쓰고 로그에 남기지 않는다.
    """
    login_id = cfg.get("ad_login_id")
    if not login_id:
        return False
    pw = _keychain_get(login_id)
    if not pw:
        log.info("Keychain 자격증명 없음 — 수동 로그인 폴백.")
        return False
    try:
        # keycloak 폼 안정화 대기
        page.wait_for_selector("#username, input[name=username]", timeout=15000)
        page.fill("#username", login_id)
        page.fill("#password", pw)
        page.click("#kc-login")
        page.wait_for_timeout(5000)  # 제출 후 리다이렉트/챌린지 안정화
        if _has_2fa_challenge(page):
            log.warning("자동 로그인 후 2FA(인증번호) 단계 — 자동화 불가, 사람 호출.")
            return False
        log.info("자동 로그인 제출 완료 — 결과 대기.")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("자동 로그인 실패(수동 폴백): %s", str(e)[:100])
        return False


def _notify_mac(title: str, message: str, sound: str = "Glass") -> None:
    """macOS 네이티브 알림(+소리)을 띄운다 — 자동 복구가 실패해 사람 개입이 필요할 때.

    로그인은 어차피 이 Mac에서 해야 하므로 알림도 같은 화면에 띄우는 게 가장 빠르다.
    osascript는 로컬 전용·자격증명 불필요. 실패해도 데몬 흐름에 영향 주지 않는다(best-effort).
    """
    try:
        safe_t = title.replace('"', "'")
        safe_m = message.replace('"', "'")
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_m}" with title "{safe_t}" sound name "{sound}"'],
            timeout=10, check=False,
        )
    except Exception as e:  # noqa: BLE001 — 알림 실패가 데몬을 멈추면 안 됨
        log.warning("macOS 알림 실패(무시): %s", str(e)[:80])


@contextlib.contextmanager
def _try_fetch_lock():
    """비차단 flock. yield True(획득)/False(이미 사용 중). 종료 시 해제.

    버튼 트리거 경로에서 claim 전에 락을 잡아, 락 경합으로 요청이 유실되는 것을 막는다
    (codex P2). 락을 못 잡으면 claim하지 않고 요청을 prod에 남겨 다음 폴에서 처리.
    """
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


def _run_with_lock(cfg: dict, login_wait_secs: int = 0) -> int:
    """세션 파일 확인 + flock(동시 실행 방지) 후 _do_run. login_wait_secs>0이면 keycloak
    만료 시 같은 창에서 로그인 대기(버튼 트리거 경로용)."""
    state = os.path.expanduser(cfg["state_file"])
    if not Path(state).is_file():
        log.error("세션 파일 없음 — 먼저 'login' 실행: %s", state)
        return 2
    with _try_fetch_lock() as acquired:
        if not acquired:
            log.warning("다른 실행이 진행 중 — 이번 호출 건너뜀")
            return 0
        return _do_run(cfg, state, login_wait_secs=login_wait_secs)


def cmd_run(cfg: dict) -> int:
    """스케줄/수동 1회 실행 — keycloak 만료 시 로그인 대기 없이 fail-fast."""
    return _run_with_lock(cfg, login_wait_secs=0)


def _do_run(cfg: dict, state: str, login_wait_secs: int = 0) -> int:
    # aid는 발급+1h 절대 만료라 매 run이 SSO 재발급을 거친다. SSO 재발급은 headful 필수
    # (headless는 xauth Akamai 차단). config "headless"는 무시(호환 위해 키만 유지).
    # login_wait_secs>0(버튼 트리거): keycloak도 만료면 같은 창에서 로그인 대기 후 fetch.
    res = None
    option_payload = None  # (filename, bytes) — Billboard 옵션×일별 보고서(일 1회)
    main_rc: int | None = None  # 메인(report/cost) push 결과 — 컨텍스트 안에서 수행(옵션보고서보다 먼저)
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
                        # keycloak도 만료 → ① Keychain 자동 로그인 시도 → ② 안 되면 수동 대기.
                        # 현재 로그인 폼(xauth keycloak)에서 자동입력. 2FA/미등록이면 사람 호출.
                        auto_ok = _try_auto_login(page, cfg)
                        if not auto_ok:
                            log.info("keycloak 만료 — 창에서 로그인하세요(자동 감지, 최대 %d초).", login_wait_secs)
                            _notify_mac("쿠팡 광고 로그인 필요",
                                        "자동 로그인 불가(2FA/미등록). 방금 열린 창에서 쿠팡 광고에 로그인하세요(3분 내).")
                            try:
                                page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
                            except Exception:
                                pass
                        # 자동·수동 모두 report/cost 201 도달을 _login_wait_loop가 감지.
                        ok_data = _login_wait_loop(page, ctx, cfg, state, login_wait_secs)
                        if ok_data is None:
                            log.error("로그인 시간 초과/취소 — 갱신 취소.")
                            _notify_mac("쿠팡 광고 로그인 미완료",
                                        "시간 초과로 광고비 갱신이 취소됐습니다. 대시보드에서 '광고비 갱신'을 다시 누르세요.")
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
                # 인증 성공(201) 시: 메인(report/cost)+SALES를 먼저 push해 UI를 즉시 unblock한 뒤,
                # 무거운 옵션×일별 Billboard 보고서(일1회·최대 300s)를 받는다. 옵션보고서를 먼저
                # 받으면 그 폴링 시간만큼 사용자 버튼(유일 경로)이 지연돼 UI 폴링 윈도우를 초과한다.
                if res is not None and res.get("status") == 201:
                    try:
                        data = json.loads(res.get("body") or "")
                    except (ValueError, TypeError) as e:
                        log.error("응답 JSON 파싱 실패: %s", e)
                        data = None
                    # 파싱 실패면 SALES·옵션 둘 다 스킵(세션 상태 의심·codex P2-1).
                    if data is not None:
                        main_rc = _push(cfg, data)  # 오늘 running(헤더 "오늘 광고비") — UI 대기값
                        # report/SALES(과거 확정일·30일) — 메인과 독립(별도 fetch)이라 파싱 성공이면
                        # main_rc 무관하게 수행(오늘 running이 아직 없어도 어제 백필은 유효). best-effort:
                        # 예외가 이미 성공한 메인 결과를 뒤엎지 않도록 감싼다(codex P2-2 — push가
                        # 컨텍스트 안으로 이동해 생긴 새 실패모드 차단).
                        try:
                            _sr = page.evaluate(_SALES_FETCH_JS, _sales_payload(cfg))
                            sales_body = _sr.get("body") if _sr else None
                            if sales_body:
                                _push_sales(cfg, sales_body)
                        except Exception as e:
                            log.warning("report/SALES 수집/push 실패(무시): %s", str(e)[:100])
                        # 무거운 옵션×일별 Billboard 보고서(최대 300s·일1회): 메인 push 성공(0) 시에만.
                        # 메인 실패면(특히 prod 네트워크 불가) 옵션 fetch+push도 무의미 → 300s 낭비 방지
                        # (codex P2-1). 과거 데이터라 다음 성공 run에서 따라잡힘(_option_due_today 일1회 게이트).
                        if main_rc == 0 and _option_due_today(cfg):
                            try:
                                option_payload = _fetch_option_report(page, cfg)
                            except Exception as e:
                                log.warning("옵션보고서 수집 실패(무시): %s", str(e)[:100])
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
    # 옵션보고서 push(컨텍스트 밖 — 바이트는 확보됨). best-effort, 성공 시에만 마커.
    if option_payload:
        fn, content = option_payload
        if _push_option_xlsx(cfg, fn, content):  # 옵션×일별(상품별 광고비)
            _mark_option_done(cfg)      # 성공 push 시에만 마커 → 실패 시 다음 run 재시도(codex P1)
    if main_rc is None:
        log.error("메인 push 누락(응답 JSON 파싱 실패) — 갱신 실패 처리.")
        return 1
    return main_rc


_POLL_INTERVAL_S = 15      # 데몬이 갱신 요청을 확인하는 간격(창 안 뜸, 가벼운 GET)
_LOGIN_WAIT_S = 180        # 버튼 클릭 시 keycloak 만료면 로그인 대기 한도
_MIN_FETCH_INTERVAL_S = 45  # fetch(창) 최소 간격 — 외부 요청 폭주로 창 스팸 방지(codex P2)
# 자가복구: 연속 네트워크 실패가 이만큼 쌓이면 프로세스를 종료한다. launchd KeepAlive가
# fresh 프로세스로 재기동 → Mac sleep/wake 후 옛 인터페이스에 묶인 소켓 고착을 자동 해소.
# (장기 실행 프로세스는 fresh Python이 성공해도 계속 'Max retries' 실패하는 macOS 현상.)
_MAX_CONSECUTIVE_NET_FAILS = 20  # 15s 간격 × 20 ≈ 5분


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
    cooldown = int(cfg.get("min_fetch_interval_s", _MIN_FETCH_INTERVAL_S))
    last_fetch = 0.0  # time.monotonic 기준. 쿨다운 내 요청은 claim 보류(요청 보존).
    log.info("폴 데몬 시작 — %ds 간격 확인, fetch 최소간격 %ds(창은 요청 시에만 뜸).", interval, cooldown)
    net_fails = 0  # 연속 네트워크 실패 카운터 — 성공 시 0으로 리셋(자가복구 게이트)
    while True:
        try:
            st = _prod_refresh_status(cfg)
            net_fails = 0
            if st.get("requested"):
                # 쿨다운/락은 claim '전에' 검사 — claim 후 스킵하면 요청이 유실되므로(codex P2).
                # 둘 중 하나라도 막히면 claim하지 않고 요청을 prod에 남겨 다음 폴에서 처리.
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
                                # 세션 없음 → 로그인부터. 대기는 UI 폴링 윈도우(215s) 안인
                                # _LOGIN_WAIT_S로 맞춤 — 기본 600s면 UI가 먼저 포기(codex P2).
                                cmd_login(cfg, wait_secs=_LOGIN_WAIT_S)
                            else:
                                _do_run(cfg, state, login_wait_secs=_LOGIN_WAIT_S)  # 락 보유 중
        except requests.RequestException as e:
            net_fails += 1
            log.warning("폴 확인 실패(네트워크) %d/%d: %s", net_fails, _MAX_CONSECUTIVE_NET_FAILS, str(e)[:80])
            if net_fails >= _MAX_CONSECUTIVE_NET_FAILS:
                log.error("연속 %d회 네트워크 실패 — 프로세스 종료(launchd가 fresh로 재기동).", net_fails)
                return 1
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
