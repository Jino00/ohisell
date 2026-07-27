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
#    "vendor_id":"A01564720","rg_report_types":["WAREHOUSING_SHIPPING"],"rg_max_periods":1,"rg_days":21,
#    "rg_status_days":35,"rg_min_interval_s":3600}
#   (vendor_id·rg_* 는 'rg' 명령·poll 데몬 RG 분기용. account_key=WING1→vendor_id A01564720(오픽스),
#    WING2→A01029796(오하이테크). rg_daily_hour은 무시됨(폐기·버튼-only), rg_min_interval_s=RG 실행 최소간격.
#    rg_status_days=층1 계정 수수료 push 윈도우(기본 35, 백필 시 90으로 1회 실행). rg_days=엑셀 열거 윈도우.)
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
from datetime import datetime, timedelta
from pathlib import Path
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
    const r = await fetch(location.origin + path, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
      credentials: 'include',
      signal: ctrl.signal,
    });
    return { status: r.status, body: await r.text() };
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


def _vs_payload(cfg: dict) -> dict:
    """vendor-summary body — 닫힌 과거일 윈도우(D-3, 어제까지). registrationTypes=3P+RG 전체.

    days 기본 7(검증용 작은 윈도우). YYYY-MM-DD 문자열(ref 18).
    """
    days = int(cfg.get("vs_days", 7))
    today = datetime.now(KST).date()
    end = today - timedelta(days=1)            # 어제(오늘은 sync 시차로 부정확 → 제외, D-3)
    start = end - timedelta(days=days - 1)
    return {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "registrationTypes": ["NORMAL", "RFM"],
        "searchIds": [],
    }


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
    """storage_state(세션쿠키 포함)를 0600으로 저장. CDP 모드에서는 Chrome이 세션을 보관하므로 no-op."""
    if cdp:
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
    """
    if _signum is not None:
        # 재진입 차단(codex R2): 정리 도중 두 번째 시그널이 들어오면 handler가 겹쳐 돈다.
        for _s in (signal.SIGTERM, signal.SIGHUP):
            with contextlib.suppress(Exception):
                signal.signal(_s, signal.SIG_IGN)
    for owner in list(_LIVE_OWNERS):
        if owner.owned and not owner.keep_open:
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
    """

    def __init__(self) -> None:
        self.proc = None
        self.keep_open = False

    @property
    def owned(self) -> bool:
        return self.proc is not None


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
            _LIVE_OWNERS.append(owner)   # 시그널 종료 시 회수 대상
            log.info("Chrome 기동(PID %d, CDP %d) — 작업 후 닫음.", proc.pid, port)
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
                if owner.keep_open:
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


def _fetch_vendor_summary(page, cfg: dict, retries: int = 2):
    """판매분석 페이지 이동 후 same-origin vendor-summary fetch. 반환: res dict 또는 None(로그아웃)."""
    page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
    page.wait_for_timeout(3000)  # Cloudflare/Akamai JS 챌린지·세션 안정화
    if _is_logged_out(page.url):
        return None
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return page.evaluate(_POST_JSON_JS, [_vs_payload(cfg), VENDOR_SUMMARY_PATH])
        except Exception as e:  # noqa: BLE001 — 봇감지 순간차단 재시도
            last_exc = e
            if attempt < retries:
                log.warning("fetch 일시 실패(%d/%d) 재시도: %s", attempt, retries, str(e)[:80])
                page.wait_for_timeout(2500)
    raise last_exc


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


def _login_wait_loop(page, ctx, cfg: dict, state: str, wait_secs: int, *, cdp: bool = False):
    """열린 page에서 사용자 로그인을 자동 감지(vendor-summary 200). 성공 시 state 저장 후 res 반환."""
    waited = 0
    while waited < wait_secs:
        try:
            page.wait_for_timeout(5000)
            waited += 5
            if _is_logged_out(page.url):
                continue
            res = page.evaluate(_POST_JSON_JS, [_vs_payload(cfg), VENDOR_SUMMARY_PATH])
        except Exception as e:  # noqa: BLE001
            if "closed" in str(e).lower():
                log.error("브라우저 창이 닫혔습니다 — 로그인 미완료(창을 닫지 말고 로그인만 하세요).")
                return None
            continue
        if _is_success(res):
            _save_state(ctx, state, cdp=cdp)
            return res
    return None


def cmd_login(cfg: dict, wait_secs: int = 600) -> int:
    """로그인 세션 초기화 + 자동 감지(vendor-summary 200) → state 저장 + 첫 파싱.

    CDP 모드: Chrome(없으면 기동)의 새 탭에서 wing.coupang.com 열고 로그인 감지.
      사람이 조작하는 명령이므로 창은 남긴다(keep_open) — 세션은 Chrome 프로필이 보관.
    레거시 모드: Playwright Chromium 헤드풀 창(모바일 UA) 새로 실행.
    """
    state = os.path.expanduser(cfg["state_file"])
    cdp = _cdp_mode(cfg)
    owner = _ChromeOwner()
    owner.keep_open = True   # 로그인 창은 사람 것 — 자동으로 닫지 않는다
    mode_label = "Chrome(CDP)" if cdp else "Playwright Chromium(모바일 UA)"
    log.info("[login] %s 실행 — wing.coupang.com에 로그인하세요(자동 감지, 최대 %d초).", mode_label, wait_secs)
    res = None
    with sync_playwright() as p:
        with _chrome(p, cfg, state, load_state=False, owner=owner) as (page, ctx, _save):
            page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
            res = _login_wait_loop(page, ctx, cfg, state, wait_secs, cdp=cdp)
    if res is None:
        log.error("제한 시간 내 로그인 감지 실패 — 다시 시도하세요.")
        return 1
    log.info("로그인 감지·세션 저장 완료: %s", state)
    summ = _summarize(res.get("body") or "")
    if not summ:
        # ★codex R3 [P2]: _is_success는 saleSummaryByDate '키 존재'만 보고, _summarize는 그 값이
        #   list일 때만 요약한다 — 200 JSON이지만 값이 dict/null이면 세션은 저장됐어도 push할
        #   데이터가 없다. 여기서 0을 반환하면 claim된 poll 회차가 성공(_push)도 실패도 보고하지
        #   않아 prod에 흔적이 없고, UI가 215초 헛기다린 뒤 'Mac 응답 없음'(Mac 꺼짐)으로 오진한다.
        #   → 보고 가능한 실패로 만든다. 세션 파일은 이미 저장됐으므로 다음 회차는 _do_run 분기로
        #   가서 같은 사유를 rc=1로 보고한다(무한 재로그인 루프 없음).
        log.error("vendor-summary 파싱 실패 — 응답 형태 변경 의심: %s", (res.get("body") or "")[:160])
        return 1
    _log_summary("[login]", summ, _vs_payload(cfg))
    if _push_configured(cfg):
        return _push(cfg, summ)   # 첫 데이터 즉시 push
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
    try:
        with sync_playwright() as p:
            with _chrome(p, cfg, state, owner=owner) as (page, ctx, save):
                res = _fetch_vendor_summary(page, cfg)
                if _is_auth_expired(res):
                    # 세션 만료 의심 → 대시보드 재진입으로 챌린지 자동해소 후 1회 재시도.
                    log.info("세션 만료 의심 — 대시보드 재진입 후 재fetch 시도")
                    res2 = _fetch_vendor_summary(page, cfg)
                    if _is_success(res2):
                        save()  # 회전 쿠키 보존 (CDP: no-op)
                        res = res2
                    elif login_wait_secs > 0:
                        log.info("자동 회복 실패 — 창에서 로그인하세요(자동 감지, 최대 %d초).", login_wait_secs)
                        with contextlib.suppress(Exception):
                            page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
                        relogin = _login_wait_loop(page, ctx, cfg, state, login_wait_secs, cdp=cdp)
                        res = relogin if relogin is not None else res2
                        if relogin is None:
                            owner.keep_open = True   # 로그인 미완료 → 창 남김(이어서 로그인 가능)
                    else:
                        res = res2
                        owner.keep_open = True       # 로그인 대기 없는 경로 → 창 남김
                elif _is_success(res):
                    save()  # 회전된 세션쿠키 갱신 (CDP: no-op)
    except Exception as e:  # noqa: BLE001
        log.error("브라우저 fetch 오류: %s", e)
        return 1

    if not _is_success(res):
        status = res.get("status") if res else None
        body = (res.get("body") if res else "") or ""
        log.error("vendor-summary 실패 status=%s — %s. 'login' 재실행 필요.",
                  status, body[:160].replace("\n", " "))
        return 1
    summ = _summarize(res.get("body") or "")
    if not summ:
        log.error("vendor-summary 파싱 실패 — 응답 형태 변경 의심: %s", (res.get("body") or "")[:160])
        return 1
    _log_summary("[run]", summ, _vs_payload(cfg))
    if _push_configured(cfg):
        return _push(cfg, summ)   # push 성공이 heartbeat(prod staleness 기준)
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


_POLL_INTERVAL_S = 15       # 갱신 요청 확인 간격(창 안 뜸, 가벼운 GET)
_LOGIN_WAIT_S = 180         # 세션 만료 시 헤드풀 창 로그인 대기 한도
_MIN_FETCH_INTERVAL_S = 45  # fetch(창) 최소 간격 — 요청 폭주로 창 스팸 방지(광고 패턴)
# 자가복구: 연속 네트워크 실패가 쌓이면 종료 → launchd가 fresh 재기동(광고 페처 패턴).
# sleep/wake 후 소켓 고착(fresh Python은 성공해도 장기 프로세스만 'Max retries') 자동 해소.
_MAX_CONSECUTIVE_NET_FAILS = 20  # 15s 간격 × 20 ≈ 5분


# ★버튼 큐는 계정 차원(2026-07-27, WING2 인스턴스 편입): account_key를 안 보내면 백엔드가
#   WING1 큐로 해석한다 → WING2 인스턴스가 오픽스(WING1) 버튼 요청을 claim해 가져가는 도난이
#   난다(claim=원자적, 먼저 집는 쪽이 이김). 아래 4개 호출 모두 자기 계정을 명시한다.
def _prod_refresh_status(cfg: dict) -> dict:
    r = requests.get(
        cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/wing/vendor-summary/refresh-status",
        params={"account_key": cfg["account_key"]},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _prod_claim(cfg: dict) -> dict:
    r = requests.post(
        cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/wing/vendor-summary/refresh-claim",
        params={"account_key": cfg["account_key"]},
        headers={"X-Ingest-Token": cfg["ingest_token"]},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _prod_rg_refresh_status(cfg: dict) -> dict:
    r = requests.get(
        cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/wing/rg-settlement/refresh-status",
        params={"account_key": cfg["account_key"]},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _prod_rg_claim(cfg: dict) -> dict:
    r = requests.post(
        cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/wing/rg-settlement/refresh-claim",
        params={"account_key": cfg["account_key"]},
        headers={"X-Ingest-Token": cfg["ingest_token"]},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _prod_report_fetch_error(cfg: dict, path: str, reason: str) -> None:
    """run 실패를 prod에 보고(last_error/last_error_at) — 보고 실패는 로그만(데몬 생존 우선).

    ★claim의 짝: claim 후 run이 실패하면 플래그는 이미 clear라 prod에 흔적이 없어
    UI가 215초 헛기다린 뒤 'Mac 응답 없음'(Mac 꺼짐)으로 오진한다. 실패는 이 POST가 알린다.
    """
    try:
        r = requests.post(
            cfg["prod_base_url"].rstrip("/") + path,
            params={"account_key": cfg["account_key"]},
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            json={"error": reason[:300]},  # 백엔드 컬럼 절단(300)과 동일 — 보고 자체가 길어서 죽지 않게
            timeout=15,
        )
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001 — 보고 실패로 데몬을 죽이지 않는다
        log.warning("fetch-error 보고 실패(%s): %s", path, str(e)[:120])


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

    ★왜(codex R2 P2): claim이 이미 요청을 소비했으므로 예외로 조용히 빠져나가면 prod에 흔적이
    없어 UI가 215초 헛기다린 뒤 'Mac 응답 없음'(Mac 꺼짐)으로 오진한다. rc!=0과 똑같이 보고해야
    한다. cmd_login은 자체 try/except가 없어 Chrome 기동 실패(_owned_chrome RuntimeError:
    chrome_profile_busy·chrome_launch_failed·cdp_not_ready 등)·page.goto 타임아웃이 그대로
    올라오고, _do_run/_do_rg_run도 브라우저 블록 밖(설정 int 변환·_push)은 덮이지 않는다.
    사유는 마지막 log.error 우선(행동지침이 담긴다), 없으면 예외 텍스트.
    """
    with _capture_last_error() as cap:
        try:
            return fn(), cap.last
        except Exception as e:  # noqa: BLE001 — 데몬은 죽지 않는다. 대신 반드시 보고한다.
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
    last_fetch = 0.0
    # RG 정산(S4-P2): 온디맨드 버튼만. RG는 주 단위·느림(생성 대기) → 별도 쿨다운 유지.
    rg_cooldown = int(cfg.get("rg_min_interval_s", 3600))   # RG 실행 최소 간격(실패 재시도 폭주 방지)
    last_rg = 0.0
    log.info("Wing 폴 데몬 시작 — %ds 간격 확인, fetch 최소간격 %ds. RG 버튼 전용·간격 %ds(창은 버튼 요청 시에만 뜸).",
             interval, cooldown, rg_cooldown)
    net_fails = 0  # 연속 네트워크 실패 카운터(vendor-summary 폴 기준) — 성공 시 리셋
    while True:
        try:
            st = _prod_refresh_status(cfg)
            net_fails = 0
            if st.get("requested"):
                # 쿨다운/락은 claim '전에' 검사 — claim 후 스킵하면 요청 유실(광고 패턴 codex P2).
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
                                # 세션 없음 → 이 창이 로그인 겸 첫 fetch(성공 시 _push가 heartbeat).
                                # ★실패도 반드시 보고: claim으로 플래그는 이미 clear라 침묵하면
                                #   prod에 흔적이 없어 UI가 215초 헛기다린 뒤 'Mac 응답 없음' 오진.
                                rc, reason = _run_claimed(
                                    lambda: cmd_login(cfg, wait_secs=_LOGIN_WAIT_S))
                                if rc != 0:
                                    _prod_report_fetch_error(
                                        cfg, "/api/coupang/ops/wing/vendor-summary/fetch-error",
                                        reason or f"세션 없음 — login 실패 rc={rc}")
                            else:
                                rc, reason = _run_claimed(
                                    lambda: _do_run(cfg, state, login_wait_secs=_LOGIN_WAIT_S))  # 락 보유 중
                                if rc != 0:
                                    _prod_report_fetch_error(
                                        cfg, "/api/coupang/ops/wing/vendor-summary/fetch-error",
                                        reason or f"vendor-summary run 실패 rc={rc}")
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
            if bool(rg_st.get("requested")) and (time.monotonic() - last_rg >= rg_cooldown):
                with _try_fetch_lock() as acquired:
                    if not acquired:
                        log.info("RG: 다른 fetch 진행 중 — 보류(다음 폴)")
                    else:
                        # claim으로 원자적 소비(요청 유실 방지).
                        # NOTE(codex P2): claim은 실행 성공 전에 이뤄져 실패 시 버튼요청이 유실된다
                        #   (vendor-summary와 동일 패턴). 일일예약이 재시도로 덮어주던 것이 사라졌으므로,
                        #   실패 시에는 사람이 버튼을 다시 누른다(낡음은 전역 신선도 배너가 표면화).
                        if _prod_rg_claim(cfg).get("claimed", False):
                            last_rg = time.monotonic()
                            log.info("RG 정산 다운로드 트리거(버튼)")
                            if not Path(state).is_file():
                                # 이번 회차는 스킵하되 침묵하지 않는다 — claim이 이미 요청을 소비해
                                # prod에 흔적이 없으면 UI가 215초 헛기다린 뒤 'Mac 응답 없음' 오진.
                                log.warning("RG: 세션 파일 없음 — 'login' 필요(이번 회차 스킵)")
                                _prod_report_fetch_error(
                                    cfg, "/api/coupang/ops/wing/rg-settlement/fetch-error",
                                    "RG: 세션 파일 없음 — 'login' 필요")
                            else:
                                rc, reason = _run_claimed(
                                    lambda: _do_rg_run(cfg, state, login_wait_secs=_LOGIN_WAIT_S))
                                if rc == _RG_RC_NO_PERIODS:
                                    # ★codex R3 [P2]: 정산주기 0개면 업로드가 없어 heartbeat
                                    #   (upload-xlsx→rg_mark_heartbeat)가 안 움직인다. 성공 전용
                                    #   신호가 없으므로(RG엔 fetch-success 엔드포인트 없음) 이
                                    #   fetch-error로 last_error_at을 움직여 UI 폴링을 진실하게
                                    #   끝낸다 — 사유 문구가 '실패 아님'을 명시한다. status는
                                    #   건드리지 않으므로(rg_mark_fetch_error) 쿠키만료 배너는 안 뜬다.
                                    _prod_report_fetch_error(
                                        cfg, "/api/coupang/ops/wing/rg-settlement/fetch-error",
                                        "RG: 정산주기 없음 — 다운로드 대상 0건"
                                        "(실패 아님, 조회는 완주)")
                                elif rc != 0:
                                    _prod_report_fetch_error(
                                        cfg, "/api/coupang/ops/wing/rg-settlement/fetch-error",
                                        reason or f"RG run 실패 rc={rc}")
        except requests.RequestException as e:
            log.warning("RG 폴 확인 실패(네트워크): %s", str(e)[:80])
        except Exception as e:  # noqa: BLE001 — 데몬은 어떤 오류에도 죽지 않는다
            log.error("RG 폴 루프 오류: %s", str(e)[:160])
        time.sleep(interval)


# ════════════════════════════════════════════════════════════════════
# S4: RG 정산 엑셀 자동 다운로드 (Wing 세션 자동화 트랙 D-8)
# ════════════════════════════════════════════════════════════════════
# 흐름(전부 살아있는 브라우저 세션 same-origin POST, D-5/D-6):
#   ① status/api          → 정산주기(settlementGroupKey) 목록 열거(어떤 주를 받을지)
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
_RG_POLL_INTERVAL_S = 8       # download-list 폴링 간격
_RG_POLL_TIMEOUT_S = 300      # 생성 완료 최대 대기(5분)
# _do_rg_run 전용 종료코드 — '완주했으나 업로드 0건'(정산주기 없음). 실패(1·2)와 구분해야 한다:
# 성공 신호(last_success_at)는 upload-xlsx만 움직이므로 업로드가 0건이면 아무 시각도 안 움직이고,
# claim된 버튼 요청은 이미 소비돼 UI가 215초 헛기다린 뒤 'Mac 응답 없음'으로 오진한다(codex R3 [P2]).
# cmd_poll이 이 코드를 정보성 사유로 보고해 폴링을 진실하게 끝낸다. CLI(cmd_rg)는 nonzero=주의 신호.
_RG_RC_NO_PERIODS = 3
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _kst_date_to_utc_iso(kst_date) -> str:
    """KST date → status/api용 UTC ISO 'YYYY-MM-DDT15:00:00.000Z'(KST 00:00=UTC, client와 동일)."""
    return f"{kst_date.isoformat()}T15:00:00.000Z"


def _rg_status_payload(cfg: dict, days: int | None = None) -> dict:
    """status/api body — 최근 윈도우(매출인식일 SALES, D-10). 정산주기 열거·계정 수집 공용.

    days=None이면 기존 동작 유지(cfg['rg_days'], 기본 21 — 다운로드 열거용, 기존 호출 불변).
    층1 계정 수집(_rg_fetch_status_raw)은 rg_status_days(기본 35, 백필 시 90)를 명시 전달한다.
    """
    if days is None:
        days = int(cfg.get("rg_days", 21))   # 최근 ~3주 → 닫힌 주별 정산 여러 건 포함
    today = datetime.now(KST).date()
    return {
        "startDate": _kst_date_to_utc_iso(today - timedelta(days=days)),
        "endDate": _kst_date_to_utc_iso(today),
        "searchDateType": "SALES",
    }


def _rg_fetch_status_raw(page, cfg: dict) -> dict | None:
    """status/api를 **1회** POST해 raw JSON dict 반환(층1: prod push + group key 열거 공용 소스).

    윈도우=rg_status_days(기본 35 — 월경계 분할 주기+여유, 백필 시 cfg로 90 오버라이드).
    200이 아니거나 로그인 HTML이면 None(_rg_json 규칙). 호출자가 None을 세션 이상으로 처리.
    ★이 함수가 유일한 status/api 소스여야 push와 열거가 같은 raw를 공유(이중 호출 금지, §1.6).
    """
    days = int(cfg.get("rg_status_days", 35))
    res = _rg_post(page, RG_STATUS_PATH, _rg_status_payload(cfg, days=days))
    return _rg_json(res)


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
        start = str(rep.get("settlementPeriodStartDate") or "")[:10]
        end = str(rep.get("settlementPeriodEndDate") or "")[:10]
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
        log.info("RG 중복 요청(기간 안전식별 불가) — 스킵 %s/%s", report_type, group_key)
        return None
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


def _rg_session_ok(page) -> bool:
    """정산 status/api가 정상 JSON(settlementStatusReports 키)을 주면 로그인 상태.

    ★호스트 무관(location.origin same-origin). vendor-summary(m-wing) 감지로 정산 세션을 판단하면
    틀리므로(셀프리뷰 A), 정산 자체의 status/api로 직접 판정한다. 빈 cfg 기본값으로 호출 가능.
    """
    try:
        data = _rg_json(_rg_post(page, RG_STATUS_PATH, _rg_status_payload({})))
    except Exception:  # noqa: BLE001 — 네비게이션 중 evaluate 실패 등은 '아직 아님'으로 처리
        return False
    return isinstance(data, dict) and "settlementStatusReports" in data


def _rg_login_wait(page, ctx, state: str, secs: int, *, cdp: bool = False) -> bool:
    """정산 페이지에서 사용자 로그인 자동 감지(status/api 200). 성공 시 state 저장. (데몬 회복 경로)"""
    waited = 0
    while waited < secs:
        try:
            page.wait_for_timeout(5000)
            waited += 5
            if _rg_session_ok(page):
                _save_state(ctx, state, cdp=cdp)
                return True
        except Exception as e:  # noqa: BLE001
            if "closed" in str(e).lower():
                log.error("브라우저 창이 닫혔습니다 — RG 로그인 미완료.")
                return False
    return False


def _do_rg_run(cfg: dict, state: str, login_wait_secs: int = 0) -> int:
    """state 로드 → 정산 페이지 → 정산주기 열거 → (key×reportType) 다운로드 → prod push.

    push 설정 필수(다운로드만 하고 버릴 이유 없음). 세션 판정·만료 회복은 정산 status/api 기반.

    반환: 0=업로드 1건 이상 성공(=prod heartbeat 갱신) / 1=실패 / 2=설정 누락 /
          3=_RG_RC_NO_PERIODS(조회는 완주, 정산주기 0개 → 업로드 0건. 실패 아님).
    """
    if not _push_configured(cfg):
        log.error("RG 다운로드엔 push 설정(account_key·prod_base_url·ingest_token) 필요.")
        return 2
    vendor_id = str(cfg.get("vendor_id") or "").strip()
    if not vendor_id:
        log.error("RG 다운로드엔 설정에 vendor_id 필요(예 A01564720).")
        return 2
    report_types = cfg.get("rg_report_types") or RG_REPORT_TYPES_DEFAULT
    max_periods = int(cfg.get("rg_max_periods", 1))   # P1: 최근 1주(dup 모호성 회피)
    poll_timeout = int(cfg.get("rg_poll_timeout_s", _RG_POLL_TIMEOUT_S))

    cdp = _cdp_mode(cfg)
    owner = _ChromeOwner()   # 창 소유권 — 로그인 미완료 시 창을 남기기 위해 직접 만든다
    pushed = failed = 0
    try:
        with sync_playwright() as p:
            with _chrome(p, cfg, state, owner=owner) as (page, ctx, save):
                page.goto(RG_DASH_URL, wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(3500)   # Cloudflare/Akamai JS 챌린지 안정화
                if not _rg_session_ok(page):
                    owner.keep_open = True    # 로그인할 창이 필요 → 닫지 않음
                    if login_wait_secs <= 0:
                        log.error("RG: 세션 만료(정산 status/api 미응답). 'login' 또는 데몬 로그인 필요.")
                        return 1
                    log.info("RG: 세션 만료 — 창에서 로그인하세요(자동 감지, 최대 %d초).", login_wait_secs)
                    if not _rg_login_wait(page, ctx, state, login_wait_secs, cdp=cdp):
                        log.error("RG: 로그인 감지 실패.")
                        return 1
                    owner.keep_open = False   # 로그인 성공 → 평소대로 작업 후 창 닫음
                else:
                    save()  # 세션 유효 → 회전 쿠키 보존 (CDP: no-op)

                # 층1: status/api raw를 1회 fetch → prod push(계정 수수료 적재) → 같은 raw로 열거.
                try:
                    raw = _rg_fetch_status_raw(page, cfg)
                except Exception as e:  # noqa: BLE001 — 응답 비정상/챌린지 → 실패 보고
                    log.error("RG status/api fetch 실패(정산 페이지 same-origin 200 미확인?): %s", e)
                    return 1
                if not isinstance(raw, dict):
                    log.error("RG status/api 응답 비정상(200/JSON 아님) — 세션·챌린지 확인.")
                    return 1
                # 계정 단위 수수료 push는 엑셀 흐름과 독립(fail-soft): 실패해도 다운로드는 계속.
                if _push_configured(cfg):
                    _rg_push_status(cfg, raw)
                try:
                    periods = _rg_enumerate_group_keys(raw, vendor_id)
                except Exception as e:  # noqa: BLE001 — 응답 비정상 → 실패 보고
                    log.error("RG status/api 열거 실패: %s", e)
                    return 1
                if not periods:
                    # 완주했지만 업로드 0건 → 성공 시각(upload-xlsx heartbeat)이 안 움직인다.
                    # 0으로 뭉개면 cmd_poll이 침묵해 UI가 무응답으로 오진한다 → 구분되는 코드.
                    log.info("RG: status/api에 정산주기 없음 — 다운로드 건너뜀.")
                    return _RG_RC_NO_PERIODS
                periods.sort(key=lambda x: x["period_end"], reverse=True)
                targets = periods[:max_periods]
                log.info("RG: 정산주기 %d개 중 최근 %d개 처리 → %s",
                         len(periods), len(targets), [t["group_key"] for t in targets])

                base_ms = int(time.time() * 1000)
                idx = 0
                for t in targets:
                    for rt in report_types:
                        req_time = base_ms + idx
                        idx += 1
                        got = _rg_download_one(page, t["group_key"], rt, req_time, poll_timeout)
                        if not got:
                            failed += 1
                            continue
                        if _rg_push_xlsx(cfg, got["url"], rt, t["group_key"]) == 0:
                            pushed += 1
                        else:
                            failed += 1
                save()  # 회전 쿠키 보존 (CDP: no-op)
    except Exception as e:  # noqa: BLE001 — 브라우저 오류는 1로 보고(데몬은 죽지 않음)
        log.error("RG 브라우저 실행 오류: %s", e)
        return 1
    log.info("RG 다운로드 완료 — push 성공 %d / 실패 %d", pushed, failed)
    return 0 if failed == 0 else 1


def cmd_rg(cfg: dict) -> int:
    """1회 RG 정산 엑셀 자동 다운로드(state 로드 → 다운로드 → prod push). 세션 없으면 fail-fast.

    종료코드는 _do_rg_run과 동일 — 3=정산주기 0개(실패 아님·업로드 없음)도 nonzero다.
    수동 실행 전용 명령이라(plist·크론은 전부 `poll`) 운영에 영향 없음.
    """
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


def cmd_chrome_supervise(cfg: dict) -> int:
    """[폐기됨 2026-07-27] Chrome 상주 supervisor — 이제 아무 것도 띄우지 않는다.

    왜 폐기: launchd KeepAlive로 Chrome 수명을 붙잡는 구조라, Jino가 창을 닫으면 10~30초 뒤
    되살아났다(불편의 실측 범인). 버튼-only 모델에서 Chrome은 poll 데몬이 fetch 때만 띄운다.

    커맨드를 지우지 않고 남기는 이유(전환 안전): Mac에 구 plist(com.ohisell.wing-chrome)가
    아직 남아 있는 상태에서 스크립트만 갱신되면 usage 에러 → KeepAlive 크래시 루프가 된다.
    그래서 "Chrome을 띄우지 않고 그냥 block"한다. 전환 절차는 이 잡을 bootout·plist 삭제.
    """
    log.warning("[deprecated] chrome-supervise는 폐기됨(버튼-only 전환) — Chrome을 띄우지 않고 대기만 합니다. "
                "launchctl bootout gui/$(id -u)/com.ohisell.wing-chrome 후 plist를 삭제하세요.")
    while True:  # launchd가 bootout할 때까지 no-op block(재기동 폭주 방지)
        time.sleep(3600)


def main() -> None:
    _install_signal_cleanup()   # SIGTERM(launchd bootout) 시 내가 띄운 Chrome 회수
    cfg = load_config()
    arg = sys.argv[1] if len(sys.argv) >= 2 else ""
    if arg == "login":
        sys.exit(cmd_login(cfg))
    if arg == "chrome":
        sys.exit(cmd_chrome(cfg))
    if arg == "chrome-supervise":  # [폐기] 구 plist 전환 안전용 no-op block
        sys.exit(cmd_chrome_supervise(cfg))
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
