#!/usr/bin/env python3
# ohitech_ad_fetcher.py — 오하이테크(1P 로켓배송) 광고센터(A01029796) 일별 광고비 fetch → prod push.
#
# 왜 별도 파일인가 (트랙 D-8):
#   - 오픽스 페처(ad_cost_browser_fetcher.py)는 라이브 머니경로 → 무수정 보호(원칙22).
#   - 데이터 소스가 다름: 오픽스=report/cost+SALES, 오하이테크=getVendorAdPerformance GraphQL.
#
# 세션 메커니즘 (트랙 D-8'): CDP 실제 Chrome attach (오픽스 WING의 com.ohisell.wing-chrome 패턴 복제).
#   - playwright 깡통 브라우저(Chrome for Testing)는 xauth.coupang.com keycloak에서 Akamai에 차단(빈 화면).
#   - 실제 Google Chrome을 --remote-debugging-port=9223 + 별도 프로필로 띄우면 Akamai 통과.
#   - 오픽스 WING(9222)과 포트·프로필 분리 = 계정별 세션 분리(D-7).
#   - 페처는 connect_over_cdp로 attach만 → Chrome이 로그인 세션 보관.
#
# 적재 (트랙 D-8ⓑ): 일별 adCostSum → coupang_ad_report(sell_type='Retail', vendor_id='A01029796').
#   → rocket_intelligence._agg_rocket_ad가 자동 합산 → 1P 로켓배송 순이익 반영.
#
# 상주화 (S3, 트랙 D-11): 전용 포트 9224 + launchd KeepAlive(9223은 rocket/wing2와 충돌).
#   - com.ohisell.ohitech-chrome.plist → chrome-supervise(Chrome 상주, self-heal)
#   - com.ohisell.ohitech-ad.plist     → poll(버튼 갱신 감지 + 일별 자동 run)
#
# 사용:
#   Chrome 기동:    python3 ohitech_ad_fetcher.py chrome            # 실제 Chrome 띄움 → Jino 직접 로그인
#   Chrome 상주:    python3 ohitech_ad_fetcher.py chrome-supervise  # launchd가 호출(포그라운드 block)
#   쿼리 캡처:      python3 ohitech_ad_fetcher.py capture           # (구버전 GraphQL 캡처, D-9 이후 불필요)
#   1회 실행:       python3 ohitech_ad_fetcher.py run               # report/SALES fetch→push→heartbeat
#   상주 데몬:      python3 ohitech_ad_fetcher.py poll              # launchd가 호출(버튼-poll + 일별)
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

CONFIG_PATH = Path(os.path.expanduser("~/.ohisell_ohitech_ad.json"))
LOG_PATH = Path(os.path.expanduser("~/.ohisell_ohitech_ad.log"))
LOCK_PATH = Path(os.path.expanduser("~/.ohisell_ohitech_ad.lock"))
# getVendorAdPerformance 라이브 캡처 결과(쿼리·변수·응답 샘플) — S1b가 읽어 쿼리 고정.
CAPTURE_PATH = Path(os.path.expanduser("~/.ohisell_ohitech_ad_capture.json"))

# 광고센터 매출 성장(getVendorAdPerformance 발사 페이지). 계정은 로그인 세션이 결정.
DASH_URL = "https://advertising.coupang.com/marketing/dashboard/sales?_cap_client=WING"
GRAPHQL_URL = "https://advertising.coupang.com/marketing-reporting/v2/graphql"
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# 전용 포트 9224 (트랙 D-11): 9222=WING1(launchd 상주), 9223=rocket/wing2 수동 공유.
# 9223에 launchd 상주화하면 rocket/wing2와 영구 충돌(엉뚱한 세션 attach→오벤더 push) → 9224 전용.
DEFAULT_CDP_PORT = 9224
DEFAULT_CDP_PROFILE = os.path.expanduser("~/.ohisell_ohitech_chrome")
KST = ZoneInfo("Asia/Seoul")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("ohitech_ad")


def load_config() -> dict:
    """설정 로드·검증. 필수=prod_base_url, ingest_token. CDP/vendor 기본값 채움(D-8ⓑ/D-8')."""
    if not CONFIG_PATH.is_file():
        log.error("설정 파일 없음: %s (README 참조)", CONFIG_PATH)
        sys.exit(2)
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for k in ("prod_base_url", "ingest_token"):
        if not cfg.get(k):
            log.error("설정 누락: %s", k)
            sys.exit(2)
    cfg.setdefault("ad_vendor_code", "A01029796")          # coupang_ad_report.vendor_id
    cfg.setdefault("cdp_port", DEFAULT_CDP_PORT)
    cfg.setdefault("cdp_profile", DEFAULT_CDP_PROFILE)
    cfg.setdefault("poll_interval_s", 60)        # poll: refresh-status 확인 주기
    cfg.setdefault("min_fetch_interval_s", 60)   # poll: 연속 fetch 최소 간격(해머링 방지)
    # daily_run_hours(일별 자동 run)는 제거됨 — 순수 on-demand(버튼만). 신선도=collection-status 배너.
    return cfg


def _is_logged_out(url: str) -> bool:
    u = (url or "").lower()
    return any(x in u for x in ("login", "/auth", "sso", "signin", "xauth"))


def _cdp_alive(port: int) -> bool:
    """CDP 디버깅 엔드포인트가 200이면 True (/json/version HTTP 프로브)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _profile_chrome_alive(profile: str) -> bool:
    """프로필을 점유 중인 살아있는 Chrome이 있는지 — SingletonLock PID 생존+cmdline 확인.

    CDP가 행(hang)/기동 중이라 _cdp_alive가 거짓이어도 Chrome이 user-data-dir을 점유 중이면
    lock 청소·중복 launch가 프로필을 손상시킨다(wing 페처 codex 교훈 복제). SingletonLock
    심볼릭링크 타깃 'hostname-PID'의 PID 생존 + 그 PID cmdline에 이 프로필이 있어야만 점유 인정
    (PID 재사용·타프로필 오인 방지).
    """
    lock = os.path.join(profile, "SingletonLock")
    try:
        target = os.readlink(lock)  # 예: "Jino-MacBookPro.local-19029"
    except OSError:
        return False
    try:
        pid = int(target.rsplit("-", 1)[-1])
    except ValueError:
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    try:
        cmdline = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except Exception:  # noqa: BLE001
        return False
    return f"--user-data-dir={profile}" in cmdline


def _notify_mac(title: str, message: str, sound: str = "Glass") -> None:
    """macOS 네이티브 알림(+소리) — 세션 만료 등 사람 개입 필요 시. best-effort."""
    try:
        safe_t = title.replace('"', "'")
        safe_m = message.replace('"', "'")
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_m}" with title "{safe_t}" sound name "{sound}"'],
            timeout=10, check=False,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("macOS 알림 실패(무시): %s", str(e)[:80])


@contextlib.contextmanager
def _try_lock():
    """비차단 flock. yield True(획득)/False(이미 사용 중). 두 명령 동시 실행 방지."""
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


@contextlib.contextmanager
def _cdp_page(cfg: dict):
    """CDP로 실제 Chrome에 attach → (page, browser). 종료 시 page만 닫고 Chrome은 유지(disconnect)."""
    port = int(cfg["cdp_port"])
    if not _cdp_alive(port):
        log.error("CDP Chrome(%d) 미응답 — 먼저 'chrome' 명령으로 띄우고 로그인하세요.", port)
        raise RuntimeError("cdp_not_alive")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            yield page, browser
        finally:
            with contextlib.suppress(Exception):
                page.close()
            with contextlib.suppress(Exception):
                browser.close()  # disconnect only — Chrome 자체는 유지


def cmd_chrome(cfg: dict) -> int:
    """CDP용 실제 Google Chrome 인스턴스 실행(--remote-debugging-port + 별도 프로필).

    오하이테크 자동화 전용 프로필 → 오픽스(9222)·개인 Chrome과 충돌·세션공유 없음(D-7).
    실행 후 그 창에서 Jino가 직접 오하이테크 광고센터 로그인(AI 비번입력 금지, D-7).
    """
    port = int(cfg["cdp_port"])
    profile = os.path.expanduser(cfg["cdp_profile"])
    if _cdp_alive(port):
        log.info("CDP Chrome(%d) 이미 실행 중 — 그 창에서 로그인하세요.", port)
        return 0
    if not os.path.exists(CHROME_BIN):
        log.error("Chrome을 찾을 수 없습니다: %s", CHROME_BIN)
        return 1
    os.makedirs(profile, exist_ok=True)
    proc = subprocess.Popen(
        [CHROME_BIN, f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
         "--no-first-run", "--no-default-browser-check", DASH_URL],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    log.info("Chrome 실행됨 (PID %d, CDP %d, 프로필 %s) — 창에서 오하이테크 광고센터 로그인.",
             proc.pid, port, profile)
    return 0


def cmd_capture(cfg: dict, wait_secs: int = 90) -> int:
    """getVendorAdPerformance 요청을 라이브 캡처(쿼리·변수·응답) → CAPTURE_PATH 저장.

    D-8ⓔ 블로커 해소: 쿼리 문자열 미저장 → 추정 금지 → 로그인된 실제 Chrome에서 실제 요청을 가로채 기록.
    새 페이지로 매출성장 대시보드를 열면 getVendorAdPerformance가 발사됨(필요 시 Jino가 기간 조회).
    """
    captured: dict = {}

    def _on_request(req) -> None:
        with contextlib.suppress(Exception):
            if GRAPHQL_URL not in req.url:
                return
            body = req.post_data or ""
            if "getVendorAdPerformance" in body and "request" not in captured:
                captured["url"] = req.url
                captured["request"] = body
                captured["headers"] = {k: v for k, v in req.headers.items()
                                       if k.lower() in ("content-type", "accept", "x-cap-client")}
                log.info("getVendorAdPerformance 요청 캡처됨 (%d bytes)", len(body))

    def _on_response(res) -> None:
        with contextlib.suppress(Exception):
            if GRAPHQL_URL in res.url and "request" in captured and "response" not in captured:
                if "getVendorAdPerformance" in (res.request.post_data or ""):
                    captured["response"] = res.text()[:6000]  # 응답 키 구조 확인용 샘플

    try:
        with _cdp_page(cfg) as (page, _browser):
            page.on("request", _on_request)
            page.on("response", _on_response)
            log.info("매출성장 대시보드로 이동 — getVendorAdPerformance 발사 대기(최대 %d초).", wait_secs)
            log.info("자동 포착이 안 되면 창에서 '광고 성과' 기간(예: 최근 7일)을 한 번 조회해 주세요.")
            try:
                page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
            except Exception as e:  # noqa: BLE001
                log.warning("대시보드 goto 경고(계속 대기): %s", str(e)[:120])
            if _is_logged_out(page.url):
                log.error("로그인 안 됨(%s) — Chrome 창에서 오하이테크 로그인 후 재시도.", page.url[:80])
                return 1
            waited = 0
            while waited < wait_secs and "response" not in captured:
                page.wait_for_timeout(3000)
                waited += 3
    except RuntimeError:
        return 2

    if "request" not in captured:
        log.error("getVendorAdPerformance 미캡처 — 창에서 '광고 성과' 조회 후 재시도.")
        return 1
    captured["captured_at"] = datetime.now(KST).isoformat()
    CAPTURE_PATH.write_text(json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(CAPTURE_PATH, 0o600)
    log.info("캡처 저장 완료: %s (응답 %s)", CAPTURE_PATH,
             "포함" if "response" in captured else "미포착")
    return 0


# report/SALES — 오하이테크 매출성장 페이지가 쓰는 일별 확정 광고비 소스(D-9, 오픽스와 동일).
# 응답 {result:{<날짜epoch ms>:{DELIVERED_AD_COST,ALL_DELIVERED_AD_COST,AD_ATTRIBUTED_SALES,...}}}.
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


def _sales_payload(cfg: dict) -> dict:
    """report/SALES 날짜 범위(최근 N일, 기본 30) — KST 기준 epoch ms {start,end}. 오늘 제외(상한)."""
    days = int(cfg.get("sales_days", 30))
    now = datetime.now(KST)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight - timedelta(days=days - 1)
    end = midnight + timedelta(days=1)  # 오늘 자정+1(상한, report/SALES는 오늘 제외 반환)
    return {"start": int(start.timestamp() * 1000), "end": int(end.timestamp() * 1000)}


def _parse_sales_days(sales_body: str) -> list[dict] | None:
    """report/SALES 응답 → 과거 확정일(오늘 제외) days[]. ad_spend=전체(ALL_DELIVERED, D-10).

    실패(None) 판정 — 사일런트 동결 차단(리뷰 P2③, 원칙22): JSON 파싱 실패, 또는 응답이
    SALES 일별맵이 아님(epoch 키 0개 = 세션만료 200·인증/에러 envelope). cmd_run이 None을 받으면
    알림 후 실패 처리한다(빈 [] 를 '갱신 없음'으로 오인해 exit 0 하던 사일런트 실패를 막음).
    필드 부재 날짜 skip(리뷰 P2④): ALL_DELIVERED_AD_COST 키가 없는 날은 0으로 덮어쓰지 않고 건너뜀.
    """
    try:
        data = json.loads(sales_body or "")
    except (ValueError, TypeError):
        return None
    result = data.get("result", data) if isinstance(data, dict) else None
    if not isinstance(result, dict):
        return None  # 일별맵 구조 아님 → 실패(인증/에러 envelope)
    today = datetime.now(KST).date()
    days = []
    saw_epoch = False
    for epoch_ms, m in result.items():
        try:
            d = datetime.fromtimestamp(int(epoch_ms) / 1000, KST).date()
        except (ValueError, TypeError, OverflowError):
            continue  # epoch 키가 아님
        saw_epoch = True
        if not isinstance(m, dict) or d >= today:  # 오늘은 미확정 → 제외
            continue
        if "ALL_DELIVERED_AD_COST" not in m:  # 필드 부재 → skip(0 클로버 방지, P2④)
            continue
        days.append({
            "date": d.isoformat(),
            "ad_spend": int(m.get("ALL_DELIVERED_AD_COST") or 0),  # 전체(비-PA 포함, D-10) = 차감값
            "pa_cost": int(m.get("DELIVERED_AD_COST") or 0),       # 집행(PA) — 참고/검산용
            "conv_sales": int(m.get("AD_ATTRIBUTED_SALES") or 0),
        })
    if not saw_epoch:
        return None  # epoch 키 0개 = SALES 일별맵 아님 → 실패(세션만료/에러, P2③)
    return days


def _push_days(cfg: dict, days: list[dict]) -> int:
    """일별 광고비 → prod /rocket/ad-cost/ingest push(토큰 인증). 0=성공."""
    vendor_id = str(cfg["ad_vendor_code"])
    try:
        pr = requests.post(
            cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/rocket/ad-cost/ingest",
            json={"vendor_id": vendor_id, "days": days},
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            timeout=30,
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
    total = sum(d["ad_spend"] for d in days)
    log.info("성공: %s Retail %d일 push(전체합 %s) → %s", vendor_id, len(days), f"{total:,}", info)
    return 0


def _prod_base(cfg: dict) -> str:
    return cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/rocket/ad-cost"


def _prod_refresh_status(cfg: dict) -> dict:
    """prod 갱신 요청/완료 상태(GET, 토큰 불필요). poll이 요청·last_success_at 확인."""
    r = requests.get(_prod_base(cfg) + "/refresh-status", timeout=15)
    r.raise_for_status()
    return r.json()


def _prod_claim(cfg: dict) -> dict:
    """갱신 요청을 원자적으로 소비(POST, 토큰). claimed=True면 이 데몬이 처리권 획득."""
    r = requests.post(
        _prod_base(cfg) + "/refresh-claim",
        headers={"X-Ingest-Token": cfg["ingest_token"]},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _mark_fetch_success(cfg: dict) -> None:
    """run 성공 후 prod last_success_at 갱신(best-effort — 머니데이터는 이미 push됨).

    실패해도 run은 성공으로 둔다(heartbeat용일 뿐). UI 버튼 폴링이 완료를 감지하는 신호.
    """
    try:
        r = requests.post(
            _prod_base(cfg) + "/fetch-success",
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            timeout=15,
        )
        if r.status_code != 200:
            log.warning("fetch-success 비200(무시): %s %s", r.status_code, r.text[:120])
    except requests.RequestException as e:
        log.warning("fetch-success 네트워크 오류(무시): %s", str(e)[:120])


def cmd_run(cfg: dict) -> int:
    """report/SALES fetch(오하이테크 세션) → 일별 파싱 → prod push(coupang_ad_report Retail). D-9/D-10.

    CDP 실제 Chrome(9223)에 attach. 세션 만료 시 fetch가 로그인HTML/401 → 알림 후 실패(사람 재로그인).
    """
    try:
        with _cdp_page(cfg) as (page, _browser):
            try:
                page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
            except Exception as e:  # noqa: BLE001
                log.warning("대시보드 goto 경고(계속): %s", str(e)[:120])
            page.wait_for_timeout(2000)  # Akamai/세션 안정화
            if _is_logged_out(page.url):
                log.error("세션 만료(로그인 페이지) — Chrome 창에서 오하이테크 재로그인 필요.")
                _notify_mac("오하이테크 광고 로그인 필요", "광고비 수집 세션 만료 — Chrome 창에서 재로그인하세요.")
                return 1
            res = page.evaluate(_SALES_FETCH_JS, _sales_payload(cfg))
    except RuntimeError:
        return 2
    except Exception as e:  # noqa: BLE001
        log.error("report/SALES fetch 오류: %s", str(e)[:160])
        return 1

    status = res.get("status") if res else None
    body = (res or {}).get("body") or ""
    if status not in (200, 201):
        if status == 200 and any(x in body.lower() for x in ("login", "signin", "kccontext")):
            log.error("세션 만료(로그인 HTML) — 재로그인 필요.")
            _notify_mac("오하이테크 광고 로그인 필요", "광고비 수집 세션 만료 — Chrome 창에서 재로그인하세요.")
        else:
            log.error("report/SALES 비정상 status=%s — %s", status, body[:160].replace("\n", " "))
        return 1
    days = _parse_sales_days(body)
    if days is None:
        # SALES 일별맵 아님(세션만료 200/인증 envelope) — 사일런트 동결 차단(리뷰 P2③).
        log.error("report/SALES 응답이 유효한 SALES 데이터 아님(세션 만료 가능) — 갱신 실패.")
        _notify_mac("오하이테크 광고 수집 실패", "report/SALES 응답 비정상(세션 만료 가능) — Chrome 창에서 재로그인 확인.")
        return 1
    if not days:
        # 세션은 정상이나 확정 과거일 없음 → 성공으로 처리(heartbeat 갱신: UI 완료 감지).
        log.info("report/SALES: 갱신할 과거 확정일 없음")
        _mark_fetch_success(cfg)
        return 0
    rc = _push_days(cfg, days)
    if rc == 0:
        _mark_fetch_success(cfg)  # push 성공 → UI 폴링이 last_success_at 변화로 완료 감지
    return rc


def cmd_chrome_supervise(cfg: dict) -> int:
    """CDP Chrome을 launchd KeepAlive로 상주화하는 포그라운드 supervisor (S3, 트랙 D-11).

    launchd가 "이 프로세스 수명 = Chrome 수명"으로 인식하도록 block한다(재부팅/종료/크래시
    자동 복구). cmd_chrome은 Popen 후 즉시 리턴해 KeepAlive와 호환 불가하므로 별도 명령.
    wing 페처(com.ohisell.wing-chrome) 패턴 복제 — 9224 전용 포트·프로필(D-11 충돌 회피).

    동작: ① 기존 Chrome 점유 시(CDP 응답 or 프로필 lock 생존) adopt 후 종료까지 대기
          ② 살아있는 Chrome 없으면 stale SingletonLock 청소
          ③ Chrome을 포그라운드 자식으로 launch하고 proc.wait()로 수명 동안 block.
    SIGTERM/INT 수신 시 자식 Chrome도 정리 후 종료(다음 supervisor가 죽어가는 Chrome adopt 방지).
    """
    import signal
    import time as _time

    port = int(cfg["cdp_port"])
    profile = os.path.expanduser(cfg["cdp_profile"])
    if not os.path.exists(CHROME_BIN):
        log.error("Chrome을 찾을 수 없습니다: %s", CHROME_BIN)
        return 1

    # ① 기존 Chrome adopt — 우리가 띄우지 않은 Chrome이 점유 중이면 종료까지 대기.
    #   ★주의(리뷰 P2): 같은 프로필을 다른 포트(9223 수동 Chrome 등)가 점유하면 우리 포트(9224)
    #   CDP는 죽었는데 프로필 lock은 살아 있어 영영 9224 Chrome을 못 띄운다(사일런트 정지).
    #   이 상태가 지속되면 8분 후 1회 알림(사람이 수동 Chrome 종료하도록).
    if _cdp_alive(port) or _profile_chrome_alive(profile):
        log.info("기존 Chrome 점유 감지(CDP=%s, lock=%s) — adopt, 종료 감지까지 대기.",
                 _cdp_alive(port), _profile_chrome_alive(profile))
        waited = 0
        alerted = False
        while _cdp_alive(port) or _profile_chrome_alive(profile):
            _time.sleep(15)
            waited += 15
            if not alerted and waited >= 480 and _profile_chrome_alive(profile) and not _cdp_alive(port):
                _notify_mac("오하이테크 광고 Chrome 정지",
                            f"다른 Chrome이 오하이테크 프로필을 점유 중 — 포트 {port} 상주 불가. "
                            "수동 Chrome(9223 등) 종료 필요.")
                alerted = True
        log.info("기존 Chrome 종료 감지 — supervisor 종료(launchd 재기동).")
        return 0

    # ② stale SingletonLock 청소(살아있는 Chrome 없음 확인됨 → 크래시 잔재 안전 제거).
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock = os.path.join(profile, name)
        with contextlib.suppress(OSError):
            if os.path.islink(lock) or os.path.exists(lock):
                os.unlink(lock)
    os.makedirs(profile, exist_ok=True)

    # ③ Chrome을 포그라운드 자식으로 launch하고 수명 동안 block.
    log.info("CDP Chrome 기동 (port %d, profile %s)", port, profile)
    proc = subprocess.Popen(
        [CHROME_BIN, f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
         "--no-first-run", "--no-default-browser-check", DASH_URL],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    def _terminate(signum, _frame):
        log.info("시그널 %d 수신 — Chrome(PID %d) 종료 대기.", signum, proc.pid)
        with contextlib.suppress(Exception):
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            log.warning("Chrome SIGTERM 무응답 — SIGKILL.")
            with contextlib.suppress(Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                proc.wait(timeout=5)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    rc = proc.wait()
    log.info("Chrome 종료(rc=%s) — supervisor 종료(launchd 재기동).", rc)
    return 0


def cmd_poll(cfg: dict) -> int:
    """상주 poll 데몬 — '광고비 갱신' 버튼 요청만 감지·실행(순수 on-demand, S3 트랙 D-11).

    poll_interval_s마다 prod refresh-status 확인 → requested면 claim(소비) 후 run.
    ★자동(일별 daily_run_hours) 실행은 제거됨 — 창을 스스로 띄우지 않고 버튼 누를 때만 뜬다.
    낡음/실패는 prod GET /collection-status → 전역 신선도 배너로 가시화(잊어버림 방지).
    run은 _try_lock 하에 수행(수동 run과 충돌 방지). 광고비는 prod 직접 fetch 불가(D-4)라
    이 데몬이 유일한 갱신 경로. launchd KeepAlive(com.ohisell.ohitech-ad.plist).
    연속 네트워크 실패가 쌓이면 종료 → launchd가 fresh 재기동(sleep/wake 소켓 고착 해소).
    """
    import time as _time

    interval = int(cfg.get("poll_interval_s", 60))
    cooldown = int(cfg.get("min_fetch_interval_s", 60))
    _MAX_FAILS = 10
    last_fetch = 0.0   # time.monotonic — 해머링 방지 쿨다운
    net_fails = 0
    auth_alerted = False  # claim 401 알림 디바운스(성공 claim 시 해제) — 토큰 불일치 스팸 방지
    log.info("[poll] 시작 — %ds 간격 갱신요청(버튼)만 확인·실행, 포트 %s",
             interval, cfg.get("cdp_port"))
    while True:
        try:
            st = _prod_refresh_status(cfg)
            net_fails = 0
            trigger = "button" if st.get("requested") else None  # 자동 트리거 제거(on-demand)

            if trigger:
                if _time.monotonic() - last_fetch < cooldown:
                    log.info("[poll] 쿨다운 중 — %s 보류(다음 폴에서 처리)", trigger)
                else:
                    with _try_lock() as acquired:
                        if not acquired:
                            log.info("[poll] 다른 실행 진행 중 — 보류")
                        else:
                            # claim 실패 = 요청 없음/타 데몬 선점 → run 스킵(요청 유실 방지).
                            # claim이 raise(401 등)하면 아래 except가 처리. 200이면 인증 정상.
                            proceed = _prod_claim(cfg).get("claimed", False)
                            auth_alerted = False
                            if not proceed:
                                log.info("[poll] claim 미획득 — 보류")
                            if proceed:
                                last_fetch = _time.monotonic()
                                log.info("[poll] %s 트리거 → run", trigger)
                                rc = cmd_run(cfg)
                                log.info("[poll] run 완료 rc=%d", rc)
                                # 실패 가시성(리뷰 P3): rc==1(세션만료)은 cmd_run이 이미 알림.
                                # rc==2=수집 Chrome 미응답 → 사일런트 정지 방지 위해 알림.
                                if rc == 2:
                                    _notify_mac("오하이테크 광고 수집 불가",
                                                f"수집용 Chrome(CDP {cfg.get('cdp_port')}) 미응답 — 상주 Chrome 잡 확인.")
        except requests.RequestException as e:
            net_fails += 1
            _status = getattr(getattr(e, "response", None), "status_code", None)
            if _status == 401 and not auth_alerted:
                log.error("[poll] 인증 실패(401) — ingest_token이 prod와 불일치.")
                _notify_mac("오하이테크 광고 인증 실패",
                            "ingest_token 불일치 — ~/.ohisell_ohitech_ad.json 토큰 확인.")
                auth_alerted = True
            log.warning("[poll] refresh-status 실패(네트워크) %d/%d: %s",
                        net_fails, _MAX_FAILS, str(e)[:80])
            if net_fails >= _MAX_FAILS:
                log.error("[poll] 연속 %d회 네트워크 실패 — 종료(launchd fresh 재기동).", net_fails)
                return 1
        except Exception as e:  # noqa: BLE001 — 데몬은 어떤 오류에도 죽지 않는다
            log.error("[poll] 루프 오류(계속): %s", str(e)[:160])
        _time.sleep(interval)


def main() -> None:
    cfg = load_config()
    arg = sys.argv[1] if len(sys.argv) >= 2 else ""
    if arg == "chrome":  # Chrome 기동은 락 불필요(즉시 리턴)
        sys.exit(cmd_chrome(cfg))
    if arg == "chrome-supervise":  # 포그라운드 block(launchd 상주) — 락 불필요(별 프로세스)
        sys.exit(cmd_chrome_supervise(cfg))
    if arg == "poll":  # 상주 데몬 — 락은 run마다 내부에서 획득(전체 점유 금지)
        try:
            sys.exit(cmd_poll(cfg))
        except KeyboardInterrupt:
            log.info("폴 데몬 종료")
            sys.exit(0)
    with _try_lock() as acquired:
        if not acquired:
            log.warning("다른 실행이 진행 중 — 이번 호출 건너뜀")
            sys.exit(0)
        if arg == "capture":
            sys.exit(cmd_capture(cfg))
        sys.exit(cmd_run(cfg))


if __name__ == "__main__":
    main()
