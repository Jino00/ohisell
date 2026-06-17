#!/usr/bin/env python3
# rocket_supplier_fetcher.py — 쿠팡 로켓배송(1P) supplier.coupang.com 발주/납품/정산을
#   "실제 브라우저"(Playwright CDP)로 수집해 prod ingest로 push (트랙 rocket-1p S3).
#   wing_browser_fetcher.py 의 헤드풀 CDP 패턴을 supplier.coupang.com용으로 복제(D-1).
#
# 왜 브라우저인가 (ref 20 §1):
#   - supplier.coupang.com은 Akamai 봇 방어(sensor data POST) 존재 → curl/requests 직타 차단 위험.
#   - 살아있는 Chrome 세션이 챌린지를 풀어두고, page-context fetch(credentials:include)로 수집.
#
# 런타임 경계 (D-1): 이 파일은 Mac 로컬 프로세스다. 백엔드는 호출하지 않는다(raw push만).
#   파싱·머니수학은 백엔드(clients/coupang/rocket_supplier.py)가 담당. 이 페처는 원시 수집+push만.
#   - 발주/납품 = /po-web/app/purchase-order/list (JSON) → page 루프 → raw 페이지 그대로 push.
#   - 정산     = /scm/settlement/general/purchase/account (SSR HTML) → DOMParser로 <table> rows 추출 → push.
#
# 데몬 방식 (Option A 시간예약형, Jino 승인 2026-06-17): launchd StartCalendarInterval로 매일 'run' 1회.
#   1P 데이터는 느리게 변함(발주는 때때로·정산은 주 단위) → 상주 poll 불필요. 온디맨드 버튼은 S5에서.
#
# 사용:
#   전용 Chrome:  backend/.venv/bin/python3 tools/rocket_supplier_fetcher.py chrome   # 9223 실행→로그인
#   세션 감지:    backend/.venv/bin/python3 tools/rocket_supplier_fetcher.py login    # PO list 200 자동감지
#   실행(1회):    backend/.venv/bin/python3 tools/rocket_supplier_fetcher.py          # run: fetch→push
#   (launchd):    backend/.venv/bin/python3 tools/rocket_supplier_fetcher.py run
#
# 설정 파일 ~/.ohisell_rocket_fetcher.json (push용):
#   {"cdp_port":9223, "cdp_profile":"~/.ohisell_supplier_chrome",
#    "prod_base_url":"https://sellc.ohitech.co.kr", "ingest_token":"<AD_INGEST_TOKEN>",
#    "vendor_id":"A01029796", "po_days":90, "settle_days":90}
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

import requests
from playwright.sync_api import sync_playwright

CONFIG_PATH = Path(os.path.expanduser("~/.ohisell_rocket_fetcher.json"))
LOG_PATH = Path(os.path.expanduser("~/.ohisell_rocket_fetcher.log"))
LOCK_PATH = Path(os.path.expanduser("~/.ohisell_rocket_fetcher.lock"))

SUPPLIER_ORIGIN = "https://supplier.coupang.com"
PO_LIST_PATH = "/po-web/app/purchase-order/list"               # 발주+납품 JSON (ref20 §3)
SETTLEMENT_PATH = "/scm/settlement/general/purchase/account"   # 정산 SSR HTML (ref20 §4)
PO_DETAIL_PATH = "/scm/purchase/order/get"                     # 발주상세 per-SKU SSR HTML (ref20b, S4.5a)

KST = ZoneInfo("Asia/Seoul")

# prod ingest 엔드포인트(S2/S4.5a 라우터, 무변경)
PO_INGEST_PATH = "/api/coupang/ops/rocket/po/ingest"
SETTLEMENT_INGEST_PATH = "/api/coupang/ops/rocket/settlement/ingest"
PO_DETAIL_INGEST_PATH = "/api/coupang/ops/rocket/po-detail/ingest"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("rocket_supplier")


# ════════════════════════════════════════════════════════════════════
# 브라우저측 page-context fetch 헬퍼 (모든 supplier 읽기는 GET·same-origin·쿠키 자동)
# ════════════════════════════════════════════════════════════════════
# JSON GET — 응답 텍스트 그대로 반환(파싱은 Python/백엔드). AbortController 25s 타임아웃.
# 인자=[path]. 반환 {status, body}.
_FETCH_TEXT_JS = """async (args) => {
  const [path] = args;
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 25000);
  try {
    const r = await fetch(path, { credentials: 'include', signal: ctrl.signal });
    return { status: r.status, body: await r.text() };
  } finally { clearTimeout(t); }
}"""

# 정산 SSR HTML GET — fetch한 HTML을 DOMParser로 파싱해 '계산서번호' 헤더를 가진 <table> rows를
# 추출(헤더+데이터, 셀 텍스트 배열). 네비게이션 없이 fetch만(Akamai 재챌린지 회피).
# 인자=[path]. 반환 {status, rows:[[셀...],...], looksLogin}.
_FETCH_SETTLEMENT_JS = r"""async (args) => {
  const [path] = args;
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 25000);
  try {
    const r = await fetch(path, { credentials: 'include', signal: ctrl.signal });
    const html = await r.text();
    const lower = html.toLowerCase();
    const looksLogin = (lower.includes('login') || lower.includes('signin')) &&
                       (lower.includes('password') || lower.includes('passport'));
    let rows = [];
    try {
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const tables = [...doc.querySelectorAll('table')];
      for (const tb of tables) {
        const trs = [...tb.querySelectorAll('tr')].map(tr =>
          [...tr.querySelectorAll('td,th')].map(td => td.innerText.trim().replace(/\s+/g, ' ')));
        if (trs.length && trs[0].some(c => c.indexOf('계산서번호') >= 0)) {
          rows = trs;
          break;
        }
      }
    } catch (e) { /* 파싱 실패 시 rows=[] */ }
    return { status: r.status, rows, looksLogin };
  } finally { clearTimeout(t); }
}"""

# 발주상세 SSR HTML GET(S4.5a) — fetch한 HTML을 DOMParser로 파싱해 per-SKU <table>(헤더에
# '상품번호'·'발주금액'·'매입가' 토큰을 모두 가진 표=ref20b Table[7])의 rows를 추출. 인덱스 대신
# 헤더 토큰으로 선택(테이블 순서 변동 방어). 인자=[path]. 반환 {status, rows:[[셀...],...], looksLogin}.
_FETCH_PO_DETAIL_JS = r"""async (args) => {
  const [path] = args;
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 25000);
  try {
    const r = await fetch(path, { credentials: 'include', signal: ctrl.signal });
    const html = await r.text();
    const lower = html.toLowerCase();
    const looksLogin = (lower.includes('login') || lower.includes('signin')) &&
                       (lower.includes('password') || lower.includes('passport'));
    let rows = [];
    try {
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const tables = [...doc.querySelectorAll('table')];
      for (const tb of tables) {
        const trs = [...tb.querySelectorAll('tr')].map(tr =>
          [...tr.querySelectorAll('td,th')].map(td => td.innerText.trim().replace(/\s+/g, ' ')));
        const flat = trs.flat().join('|');
        if (flat.indexOf('상품번호') >= 0 && flat.indexOf('발주금액') >= 0 && flat.indexOf('매입가') >= 0) {
          rows = trs;
          break;
        }
      }
    } catch (e) { /* 파싱 실패 시 rows=[] */ }
    return { status: r.status, rows, looksLogin };
  } finally { clearTimeout(t); }
}"""


# ════════════════════════════════════════════════════════════════════
# 설정 / 락
# ════════════════════════════════════════════════════════════════════
def load_config() -> dict:
    """설정 로드. 없으면 기본값(로그인·검증은 prod 정보 불필요)."""
    cfg: dict = {}
    if CONFIG_PATH.is_file():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            log.warning("설정 파일 파싱 실패(기본값 사용): %s", e)
            cfg = {}
    cfg.setdefault("cdp_port", 9223)
    cfg.setdefault("cdp_profile", "~/.ohisell_supplier_chrome")
    cfg.setdefault("po_days", 90)
    cfg.setdefault("settle_days", 90)
    cfg.setdefault("po_max_pages", 100)
    cfg.setdefault("settle_max_pages", 100)
    # 발주상세 per-SKU 수집(S4.5a): 최근 po_detail_days 발주만, po_detail_max건 캡(런타임 바운드).
    cfg.setdefault("collect_po_detail", True)
    cfg.setdefault("po_detail_days", 45)
    cfg.setdefault("po_detail_max", 80)
    return cfg


def _push_configured(cfg: dict) -> bool:
    """prod push에 필요한 3종(prod_base_url·ingest_token·vendor_id)이 모두 있으면 True."""
    return bool(cfg.get("prod_base_url") and cfg.get("ingest_token") and cfg.get("vendor_id"))


@contextlib.contextmanager
def _try_fetch_lock():
    """비차단 flock. yield True(획득)/False(이미 사용 중). wing/광고 페처와 동일 패턴."""
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


# ════════════════════════════════════════════════════════════════════
# 브라우저 세션 (CDP 모드 — 살아있는 Chrome에 연결, 세션은 Chrome이 보관)
# ════════════════════════════════════════════════════════════════════
@contextlib.contextmanager
def _chrome(p, cfg: dict):
    """전용 Chrome(CDP)에 연결 → 기존 로그인 컨텍스트의 새 탭. Akamai 핑거프린트 없음(실제 Chrome).

    yield page. 종료 시 탭만 닫고 Chrome 자체는 유지(disconnect only).
    """
    port = int(cfg.get("cdp_port", 9223))
    browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    page = ctx.new_page()
    try:
        yield page
    finally:
        with contextlib.suppress(Exception):
            page.close()
        with contextlib.suppress(Exception):
            browser.close()  # disconnect only, Chrome 자체는 유지


def _eval_retry(page, js: str, arg, retries: int = 2):
    """page.evaluate 재시도(봇감지 순간차단·일시 실패 대비). 마지막 예외 재발생."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return page.evaluate(js, arg)
        except Exception as e:  # noqa: BLE001 — 일시 실패 재시도
            last_exc = e
            if attempt < retries:
                log.warning("fetch 일시 실패(%d/%d) 재시도: %s", attempt, retries, str(e)[:80])
                page.wait_for_timeout(2500)
    raise last_exc


def _goto_origin(page) -> bool:
    """supplier 오리진 진입(쿠키 same-origin fetch 준비) + Akamai JS 안정화. 로그아웃이면 False."""
    page.goto(SUPPLIER_ORIGIN, wait_until="domcontentloaded", timeout=40000)
    page.wait_for_timeout(3000)  # Akamai 챌린지·세션 안정화
    return not _is_logged_out(page.url)


def _is_logged_out(url: str) -> bool:
    u = (url or "").lower()
    return any(x in u for x in ("login", "/auth", "sso", "signin", "passport"))


# ════════════════════════════════════════════════════════════════════
# 세션 판정 — PO list가 정상 JSON(success/body.body)을 주면 로그인 상태
# ════════════════════════════════════════════════════════════════════
def _po_query(cfg: dict, page_no: int, *, days: int | None = None) -> str:
    """발주 list 쿼리스트링. searchDateType=PURCHASE_ORDER_DATE(발주일=매출기준, D-3)."""
    n = int(days if days is not None else cfg.get("po_days", 90))
    today = datetime.now(KST).date()
    start = today - timedelta(days=n)
    return urlencode({
        "page": page_no,
        "searchDateType": "PURCHASE_ORDER_DATE",
        "searchStartDate": start.isoformat(),
        "searchEndDate": today.isoformat(),
        "centerCode": "",
        "purchaseOrderIdArray": "",
        "vendorPaymentInfoSeq": "",
        "purchaseOrderStatus": "",
        "purchaseOrderType": "",
        "skuIdArray": "",
        "crossdock": "",
        "transportType": "",
    })


def _fetch_po_page(page, cfg: dict, page_no: int, *, days: int | None = None):
    """발주 list 한 페이지 fetch. 반환 (payload_dict | None, status). None=비JSON(로그인 등)."""
    path = f"{PO_LIST_PATH}?{_po_query(cfg, page_no, days=days)}"
    res = _eval_retry(page, _FETCH_TEXT_JS, [path])
    status = (res or {}).get("status")
    body = (res or {}).get("body") or ""
    if status != 200:
        return None, status
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return None, status   # 로그인 HTML 등 비JSON
    return (payload if isinstance(payload, dict) else None), status


def _session_ok(page, cfg: dict) -> bool:
    """발주 list page=1(작은 윈도우) 200+JSON이면 로그인 상태."""
    try:
        payload, _ = _fetch_po_page(page, cfg, 1, days=7)
    except Exception:  # noqa: BLE001 — 네비게이션 중 evaluate 실패 등은 '아직 아님'
        return False
    if not isinstance(payload, dict):
        return False
    outer = payload.get("body")
    return isinstance(outer, dict) and isinstance(outer.get("body"), list)


def _page_meta(payload: dict) -> dict:
    """list envelope에서 페이지네이션 메타(파서 import 없이 최소 읽기). body.lastPageNumber 등."""
    outer = (payload or {}).get("body") or {}
    if not isinstance(outer, dict):
        return {"current_page": 0, "last_page_number": 0}
    def _i(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0
    return {
        "current_page": _i(outer.get("currentPage")),
        "last_page_number": _i(outer.get("lastPageNumber")),
    }


# ════════════════════════════════════════════════════════════════════
# ① + ② 발주/납품 수집 (page=1..lastPageNumber 루프, raw 페이지 그대로)
# ════════════════════════════════════════════════════════════════════
def _collect_po_pages(page, cfg: dict) -> list[dict]:
    """발주 list 전 페이지 raw JSON 수집. lastPageNumber까지(또는 po_max_pages 캡). 실패 시 RuntimeError."""
    max_pages = int(cfg.get("po_max_pages", 100))
    pages: list[dict] = []
    page_no = 1
    while page_no <= max_pages:
        payload, status = _fetch_po_page(page, cfg, page_no)
        if payload is None:
            raise RuntimeError(f"발주 list page={page_no} 비정상(status={status}) — 세션 만료 의심")
        pages.append(payload)
        meta = _page_meta(payload)
        last = meta["last_page_number"]
        if last <= 0 or page_no >= last:
            break
        page_no += 1
    log.info("발주 list 수집: %d페이지", len(pages))
    return pages


# ════════════════════════════════════════════════════════════════════
# ③ 정산 수집 (SSR HTML → DOMParser rows, page 루프, invoice 단위 진행 가드)
# ════════════════════════════════════════════════════════════════════
def _settle_query(cfg: dict, page_no: int) -> str:
    """정산 폼-GET 쿼리. paymentSearchType 등은 ref20 실측값(설정 override 가능)."""
    days = int(cfg.get("settle_days", 90))
    today = datetime.now(KST).date()
    start = today - timedelta(days=days)
    return urlencode({
        "page": page_no,
        "size": int(cfg.get("settle_page_size", 50)),
        "billIssueType": cfg.get("settle_bill_issue_type", "DIRECT"),
        "startDate": start.isoformat(),
        "endDate": today.isoformat(),
        "paymentPurchaseSearchType": "",
        "vendorPaymentInfoSeq": "",
        "paymentSearchType": cfg.get("settle_payment_search_type", "COMPLETE"),
    })


def _invoice_idx(header: list) -> int:
    """헤더에서 '계산서번호' 컬럼 인덱스(없으면 -1)."""
    for i, c in enumerate(header):
        if "계산서번호" in str(c):
            return i
    return -1


def _collect_settlement_rows(page, cfg: dict) -> list[list]:
    """정산 전 페이지 DOM rows 수집 → [헤더] + 전체 데이터행. invoice 단위 dedup·진행 가드.

    SSR 페이저가 마지막 페이지를 clamp 재서빙해도(같은 계산서 반복) 신규행 0이면 종료.
    실패(세션 만료) 시 RuntimeError.
    """
    max_pages = int(cfg.get("settle_max_pages", 100))
    header: list | None = None
    inv_idx = -1
    data: list[list] = []
    seen: set = set()
    for page_no in range(1, max_pages + 1):
        path = f"{SETTLEMENT_PATH}?{_settle_query(cfg, page_no)}"
        res = _eval_retry(page, _FETCH_SETTLEMENT_JS, [path])
        status = (res or {}).get("status")
        if status != 200 or (res or {}).get("looksLogin"):
            raise RuntimeError(f"정산 page={page_no} 비정상(status={status}, login={res and res.get('looksLogin')}) — 세션 만료 의심")
        rows = (res or {}).get("rows") or []
        if not rows:
            break  # 계산서번호 테이블 없음 = 데이터 없음
        if header is None:
            header = [str(c) for c in rows[0]]
            inv_idx = _invoice_idx(header)
        page_data = rows[1:]
        new = 0
        for r in page_data:
            key = r[inv_idx] if (0 <= inv_idx < len(r)) else tuple(r)
            if key in seen:
                continue
            seen.add(key)
            data.append(r)
            new += 1
        if new == 0:
            break  # 신규행 없음(빈 페이지/마지막 clamp) → 종료
    if header is None:
        log.info("정산 수집: 계산서 테이블 없음(데이터 0)")
        return []
    log.info("정산 수집: 계산서 %d건", len(data))
    return [header] + data


# ════════════════════════════════════════════════════════════════════
# 발주상세 per-SKU 수집 (S4.5a) — 최근 PO만, 캡, PO별 fetch→push. Akamai stale 시 오리진 리로드 재무장.
# ════════════════════════════════════════════════════════════════════
def _po_detail_targets(pages: list[dict], cfg: dict) -> list[int]:
    """수집한 발주 페이지에서 발주상세 대상 PO seq 선정(최근 po_detail_days·po_detail_max건, 최신순)."""
    days = int(cfg.get("po_detail_days", 45))
    cap = int(cfg.get("po_detail_max", 80))
    cutoff = datetime.now(KST).date() - timedelta(days=days)
    rows: list[tuple[str, int]] = []  # (createdAt[:10], seq) — created desc 정렬용
    seen: set[int] = set()
    for payload in pages or []:
        outer = (payload or {}).get("body") or {}
        if not isinstance(outer, dict):
            continue
        for po in (outer.get("body") or []):
            if not isinstance(po, dict):
                continue
            seq = po.get("purchaseOrderSeq")
            if seq is None or int(seq) in seen:
                continue
            created = str(po.get("createdAt") or "")[:10]
            try:
                cdate = date.fromisoformat(created) if created else None
            except ValueError:
                cdate = None
            if cdate is not None and cdate < cutoff:
                continue  # 윈도우 밖(오래된 발주) — 상세 생략
            seen.add(int(seq))
            rows.append((created, int(seq)))
    rows.sort(reverse=True)  # 최신 발주 우선
    return [seq for _, seq in rows[:cap]]


def _fetch_po_detail_rows(page, po_seq: int):
    """발주상세 한 건 fetch → per-SKU 테이블 rows. 반환 (rows|None, status). None=비정상/로그인/빈테이블."""
    path = f"{PO_DETAIL_PATH}/{po_seq}"
    res = _eval_retry(page, _FETCH_PO_DETAIL_JS, [path])
    status = (res or {}).get("status")
    if status != 200 or (res or {}).get("looksLogin"):
        return None, status
    rows = (res or {}).get("rows") or []
    return (rows if rows else None), status


def _collect_and_push_po_details(page, cfg: dict, pages: list[dict]) -> tuple[int, int]:
    """대상 PO별 발주상세 fetch→push. Akamai stale("Failed to fetch"/비200) 시 오리진 리로드 1회 재무장.

    pages: 이미 수집한 발주 list 페이지들(대상 PO seq 선정용). 발주/정산 push와 독립.
    반환 (pushed, failed). 연속 실패 다수면 세션 의심 → 조기 종료(발주/정산 push는 이미 별도 완료).
    """
    targets = _po_detail_targets(pages or [], cfg)
    if not targets:
        log.info("발주상세: 대상 PO 0건 — 건너뜀")
        return 0, 0
    log.info("발주상세 수집 대상 %d건(최근 %d일·캡 %d)", len(targets), cfg.get("po_detail_days"), cfg.get("po_detail_max"))
    pushed = 0
    failed = 0
    consec_fail = 0
    for seq in targets:
        rows = None
        for attempt in (1, 2):
            try:
                rows, status = _fetch_po_detail_rows(page, seq)
            except Exception as e:  # noqa: BLE001 — Akamai stale 등 일시 실패
                rows, status = None, str(e)[:60]
            if rows is not None:
                break
            if attempt == 1:
                # Akamai 센서 재무장(ref20b §1): 오리진 리로드 후 재시도
                with contextlib.suppress(Exception):
                    _goto_origin(page)
        if rows is None:
            failed += 1
            consec_fail += 1
            log.warning("발주상세 PO=%d 실패(status=%s)", seq, status)
            if consec_fail >= 5:
                log.error("발주상세 연속 실패 5건 — 세션 의심, 상세 수집 조기 종료")
                break
            continue
        consec_fail = 0
        if _push_po_items(cfg, seq, rows) == 0:
            pushed += 1
        else:
            failed += 1
        page.wait_for_timeout(400)  # 폴라이트 간격
    log.info("발주상세 완료 — push %d건 / 실패 %d건", pushed, failed)
    return pushed, failed


def _push_po_items(cfg: dict, po_seq: int, rows: list[list]) -> int:
    """발주상세 per-SKU DOM rows → prod ingest(PO별 snapshot replace). 0=성공/1=실패."""
    try:
        pr = requests.post(
            cfg["prod_base_url"].rstrip("/") + PO_DETAIL_INGEST_PATH,
            json={"purchase_order_seq": po_seq, "vendor_id": cfg["vendor_id"], "rows": rows},
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            timeout=60,
        )
    except requests.RequestException as e:
        log.error("발주상세 push PO=%d 네트워크 오류: %s", po_seq, e)
        return 1
    if pr.status_code != 200:
        log.error("발주상세 push PO=%d 실패 HTTP %s — %s", po_seq, pr.status_code, pr.text[:200])
        return 1
    return 0


# ════════════════════════════════════════════════════════════════════
# prod push
# ════════════════════════════════════════════════════════════════════
def _push_po(cfg: dict, pages: list[dict]) -> int:
    """발주 raw 페이지들 → prod ingest. 0=성공/1=실패. 빈 수집은 push 안 함(no-op 성공)."""
    if not pages:
        log.info("발주 push: 수집 0페이지 — 건너뜀")
        return 0
    try:
        pr = requests.post(
            cfg["prod_base_url"].rstrip("/") + PO_INGEST_PATH,
            json={"pages": pages},
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            timeout=60,
        )
    except requests.RequestException as e:
        log.error("발주 push 네트워크 오류: %s", e)
        return 1
    if pr.status_code != 200:
        log.error("발주 push 실패 HTTP %s — %s", pr.status_code, pr.text[:200])
        return 1
    log.info("발주 push 성공 → %s", _json_or_text(pr))
    return 0


def _push_settlement(cfg: dict, rows: list[list]) -> int:
    """정산 DOM rows(헤더 포함) → prod ingest. 0=성공/1=실패. 빈 수집은 push 안 함."""
    if not rows or len(rows) < 2:
        log.info("정산 push: 데이터행 0 — 건너뜀")
        return 0
    try:
        pr = requests.post(
            cfg["prod_base_url"].rstrip("/") + SETTLEMENT_INGEST_PATH,
            json={"vendor_id": cfg["vendor_id"], "rows": rows},
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            timeout=60,
        )
    except requests.RequestException as e:
        log.error("정산 push 네트워크 오류: %s", e)
        return 1
    if pr.status_code != 200:
        log.error("정산 push 실패 HTTP %s — %s", pr.status_code, pr.text[:200])
        return 1
    log.info("정산 push 성공(vendor=%s) → %s", cfg["vendor_id"], _json_or_text(pr))
    return 0


def _json_or_text(resp):
    try:
        return resp.json()
    except ValueError:
        return resp.text[:120]


# ════════════════════════════════════════════════════════════════════
# run / login / chrome 커맨드
# ════════════════════════════════════════════════════════════════════
def _do_run(cfg: dict) -> int:
    """살아있는 Chrome 연결 → 발주+정산 수집 → prod push. push 부분실패는 비0 반환."""
    if not _push_configured(cfg):
        log.error("run엔 prod_base_url·ingest_token·vendor_id 설정 필요(~/.ohisell_rocket_fetcher.json).")
        return 2
    pages: list[dict] = []
    settle_rows: list[list] = []
    try:
        with sync_playwright() as p:
            with _chrome(p, cfg) as page:
                if not _goto_origin(page):
                    log.error("supplier 로그아웃 상태(url=%s) — 'login' 또는 'chrome' 후 로그인 필요.", page.url)
                    return 1
                if not _session_ok(page, cfg):
                    log.error("발주 list 세션 미응답 — 세션 만료. 'login' 재실행 필요.")
                    return 1
                pages = _collect_po_pages(page, cfg)
                try:
                    settle_rows = _collect_settlement_rows(page, cfg)
                except Exception as e:  # noqa: BLE001 — 정산 실패는 발주 push를 막지 않음
                    log.error("정산 수집 실패(발주는 계속 push): %s", e)
                    settle_rows = []
                # 발주 push 먼저(상세는 PO 적재 후가 안전·드리프트 무관) → 그다음 발주상세
                rc_po = _push_po(cfg, pages)
                rc_st = _push_settlement(cfg, settle_rows)
                detail_failed = 0
                if cfg.get("collect_po_detail", True):
                    try:
                        _, detail_failed = _collect_and_push_po_details(page, cfg, pages)
                    except Exception as e:  # noqa: BLE001 — 상세 실패는 발주/정산 push를 무효화하지 않음
                        log.error("발주상세 수집 실패(발주/정산은 push됨): %s", e)
                        detail_failed = -1
    except Exception as e:  # noqa: BLE001 — 브라우저/수집 오류
        log.error("브라우저 수집 오류: %s", e)
        return 1

    rc = 0 if (rc_po == 0 and rc_st == 0 and detail_failed <= 0) else 1
    log.info("run 완료 — 발주 push rc=%d / 정산 push rc=%d / 발주상세 실패=%d", rc_po, rc_st, detail_failed)
    return rc


def cmd_run(cfg: dict) -> int:
    """1회 실행(락으로 동시 실행 방지). 세션 없으면 _do_run 내부에서 fail-fast."""
    with _try_fetch_lock() as acquired:
        if not acquired:
            log.warning("다른 실행이 진행 중 — 이번 호출 건너뜀")
            return 0
        return _do_run(cfg)


def cmd_login(cfg: dict, wait_secs: int = 600) -> int:
    """전용 Chrome(CDP)의 새 탭에서 supplier 진입 → 사용자 로그인 자동 감지(PO list 200)."""
    log.info("[login] supplier.coupang.com에 로그인하세요(자동 감지, 최대 %d초).", wait_secs)
    ok = False
    try:
        with sync_playwright() as p:
            with _chrome(p, cfg) as page:
                with contextlib.suppress(Exception):
                    page.goto(SUPPLIER_ORIGIN, wait_until="domcontentloaded", timeout=40000)
                waited = 0
                while waited < wait_secs:
                    try:
                        page.wait_for_timeout(5000)
                        waited += 5
                        if _is_logged_out(page.url):
                            continue
                        if _session_ok(page, cfg):
                            ok = True
                            break
                    except Exception as e:  # noqa: BLE001
                        if "closed" in str(e).lower():
                            log.error("탭이 닫혔습니다 — 로그인 미완료(탭을 닫지 말고 로그인만 하세요).")
                            return 1
                        continue
    except Exception as e:  # noqa: BLE001
        log.error("로그인 감지 오류: %s", e)
        return 1
    if not ok:
        log.error("제한 시간 내 로그인 감지 실패 — 다시 시도하세요.")
        return 1
    log.info("로그인 감지 완료(세션은 Chrome이 보관).")
    return 0


def cmd_chrome(cfg: dict) -> int:
    """CDP용 전용 Chrome 실행(--remote-debugging-port). recon과 같은 프로필 9223 재사용.

    실행 후 브라우저에서 supplier.coupang.com 로그인 → 'login' 명령으로 세션 감지.
    """
    import subprocess

    port = int(cfg.get("cdp_port", 9223))
    profile = os.path.expanduser(cfg.get("cdp_profile", "~/.ohisell_supplier_chrome"))
    chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not os.path.exists(chrome_bin):
        log.error("Chrome을 찾을 수 없습니다: %s", chrome_bin)
        return 1
    os.makedirs(profile, exist_ok=True)
    proc = subprocess.Popen(
        [
            chrome_bin,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            SUPPLIER_ORIGIN,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    log.info(
        "Chrome 실행됨 (PID %d, CDP port %d, 프로필 %s)\n"
        "  1. 브라우저에서 supplier.coupang.com 로그인\n"
        "  2. 완료 후: rocket_supplier_fetcher.py login",
        proc.pid, port, profile,
    )
    return 0


def main() -> None:
    cfg = load_config()
    arg = sys.argv[1] if len(sys.argv) >= 2 else "run"
    if arg == "chrome":
        sys.exit(cmd_chrome(cfg))
    if arg == "login":
        sys.exit(cmd_login(cfg))
    if arg in ("run", ""):
        sys.exit(cmd_run(cfg))
    print("usage: rocket_supplier_fetcher.py [chrome|login|run]", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
