#!/usr/bin/env python3
# ohitech_ad_fetcher.py — 오하이테크(1P 로켓배송) 광고센터(A01029796) 일별 광고비 fetch → prod push.
#
# 왜 별도 파일인가 (트랙 D-8):
#   - 오픽스 페처(ad_cost_browser_fetcher.py)는 라이브 머니경로 → 무수정 보호(원칙22).
#   - 데이터 소스가 다름: 오픽스=report/cost+SALES, 오하이테크=getVendorAdPerformance GraphQL.
#
# 세션 메커니즘 (트랙 D-8'): CDP 실제 Chrome attach (오픽스 WING 페처와 같은 방식).
#   - playwright 깡통 브라우저(Chrome for Testing)는 xauth.coupang.com keycloak에서 Akamai에 차단(빈 화면).
#   - 실제 Google Chrome을 --remote-debugging-port=9223 + 별도 프로필로 띄우면 Akamai 통과.
#   - 오픽스 WING(9222)과 포트·프로필 분리 = 계정별 세션 분리(D-7).
#   - 페처는 connect_over_cdp로 attach만 → Chrome이 로그인 세션 보관.
#
# 적재 (트랙 D-8ⓑ): 일별 adCostSum → coupang_ad_report(sell_type='Retail', vendor_id='A01029796').
#   → rocket_intelligence._agg_rocket_ad가 자동 합산 → 1P 로켓배송 순이익 반영.
#
# 상주화 (S3, 트랙 D-11 → 2026-07-27 개정): 전용 포트 9224(9223은 rocket/wing2와 충돌).
#   - com.ohisell.ohitech-ad.plist → poll(버튼 갱신 요청만 감지·실행)
#   ★com.ohisell.ohitech-chrome(chrome-supervise, KeepAlive) 상주 supervisor는 폐기됐다.
#     Jino가 창을 닫아도 launchd가 Chrome을 되살리던 원인 → poll 데몬이 버튼 요청을 claim했을
#     때만 Chrome을 띄우고 작업 후 닫는다(설계문서 2026-07-27 개정 참조).
#
# 사용:
#   Chrome 기동:    python3 ohitech_ad_fetcher.py chrome            # 실제 Chrome 띄움 → Jino 직접 로그인
#   쿼리 캡처:      python3 ohitech_ad_fetcher.py capture           # (구버전 GraphQL 캡처, D-9 이후 불필요)
#   1회 실행:       python3 ohitech_ad_fetcher.py run               # report/SALES fetch→push→heartbeat
#   상주 데몬:      python3 ohitech_ad_fetcher.py poll              # launchd가 호출(버튼-poll)
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
    return _cmdline_has_profile(cmdline, profile)


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
        # 정상 경로에선 _owned_chrome이 먼저 기동을 보장한다 — 여기 걸리면 그 밖의 호출.
        log.error("CDP Chrome(%d) 미응답 — 'chrome' 명령으로 띄우고 로그인하세요.", port)
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


# ════════════════════════════════════════════════════════════════════
# per-fetch Chrome 수명 (2026-07-27 — supervisor 폐기, 버튼 누를 때만 창)
# ════════════════════════════════════════════════════════════════════
# 왜: 상주 supervisor(launchd KeepAlive=true)는 Jino가 창을 닫으면 10~30초 뒤 Chrome을
#   되살렸다(세션 보온의 대가). 버튼-only 모델에서 창은 "버튼 누른 그 순간 1회"만 떠야 하므로,
#   poll 데몬이 요청을 claim한 뒤 스스로 Chrome을 띄우고 작업이 끝나면 닫는다.
# ★소유권 규칙: 내가 띄운 Chrome만 내가 닫는다. 이미 떠 있던 Chrome(사람이 로그인하려고
#   띄운 창 등)은 adopt만 하고 절대 닫지 않는다.
def _port_owner_foreign(port: int, profile: str, allow_unverified: bool = False) -> bool:
    """CDP 포트 LISTEN 소유자가 '우리 프로필의 Chrome'임을 **확인하지 못하면** True(=adopt 거부).

    왜(codex R1 P1#3): `/json/version` 200만 보고 adopt하면, 같은 포트에 뜬 무관한 Chrome
    (다른 계정 프로필·다른 자동화 도구)의 컨텍스트로 수집해 **남의 vendor 데이터를 우리
    account_key로 적재**할 수 있다. 포트는 설정값이라 다계정 인스턴스가 포트를 안 바꾸면 실제로 겹친다.

    왜 fail-closed인가(codex R2 재반박 수용): 우리가 adopt할 정당한 창은 수동 `chrome` 커맨드든
    per-fetch 기동이든 전부 `_chrome_argv`로 뜬다 → cmdline에 항상 `--user-data-dir=<프로필>`이 있다.
    따라서 '확인 불가'는 정상 케이스가 아니라 남의 Chrome이라는 신호다. 오적재는 조용하고 되돌리기
    어렵지만 거부는 로그·알림으로 시끄럽다. 현장에서 오판이 나면 설정 `adopt_unverified_chrome:true`로
    옛 동작(확인 불가 시 adopt)으로 되돌릴 수 있다.
    """
    verdict = "adopt(설정 허용)" if allow_unverified else "adopt 거부"
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:  # noqa: BLE001
        log.warning("CDP %d 소유자 확인 불가(lsof 실행 실패) — %s", port, verdict)
        return not allow_unverified
    pids = [p for p in out.split() if p.isdigit()]
    if not pids:
        log.warning("CDP %d LISTEN PID를 찾지 못함 — %s", port, verdict)
        return not allow_unverified
    for pid in pids:
        try:
            cmdline = subprocess.run(
                ["ps", "-o", "command=", "-p", pid],
                capture_output=True, text=True, timeout=3,
            ).stdout
        except Exception:  # noqa: BLE001
            continue
        if _cmdline_has_profile(cmdline, profile):
            return False            # 우리 프로필의 Chrome임이 확인됨 → adopt 정당
    log.warning("CDP %d 소유자가 우리 프로필(%s)이 아님 — %s", port, profile, verdict)
    return not allow_unverified


@contextlib.contextmanager
def _profile_launch_lock(profile: str, timeout_s: int = 90):
    """프로필 단위 기동 직렬화 — '점검 → stale lock 청소 → launch → CDP 대기' 전 구간을 감싼다.

    왜(codex R1 P1#2): 페처별 flock은 *작업* 단위라 같은 프로필을 쓰는 다른 커맨드(chrome/capture)나
    다른 프로세스와는 배타되지 않는다. 둘 다 "비어 있음"을 보고 Singleton 파일을 지운 뒤 Chrome을
    이중 기동하면 프로필·쿠키 DB가 손상된다. lock 경로는 프로필에서 결정되므로 프로세스가
    달라도 같은 파일을 놓고 배타된다.
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
    port = int(cfg["cdp_port"])
    profile = os.path.expanduser(cfg["cdp_profile"])
    return [CHROME_BIN, f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check", DASH_URL]


def _launch_chrome(cfg: dict):
    """실제 Chrome을 백그라운드 자식으로 기동. 반환 Popen(실패 시 None). stale lock 먼저 청소."""
    if not os.path.exists(CHROME_BIN):
        log.error("Chrome을 찾을 수 없습니다: %s", CHROME_BIN)
        return None
    profile = os.path.expanduser(cfg["cdp_profile"])
    # 살아있는 Chrome 없음을 확인한 뒤에만 호출된다 → 크래시 잔재 lock 안전 제거.
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock = os.path.join(profile, name)
        with contextlib.suppress(OSError):
            if os.path.islink(lock) or os.path.exists(lock):
                os.unlink(lock)
    os.makedirs(profile, exist_ok=True)
    return subprocess.Popen(_chrome_argv(cfg), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wait_cdp(port: int, timeout_s: int = 60) -> bool:
    """Chrome 기동 후 CDP가 응답할 때까지 대기(1초 간격). 콜드 스타트 여유."""
    import time as _time

    waited = 0
    while waited < timeout_s:
        if _cdp_alive(port):
            return True
        _time.sleep(1)
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
    기동 실패·프로필 점유 충돌은 RuntimeError(_cdp_page와 같은 오류 경로로 rc=2 처리).
    """
    owner = owner if owner is not None else _ChromeOwner()
    port = int(cfg["cdp_port"])
    profile = os.path.expanduser(cfg["cdp_profile"])
    # 점검~기동~CDP대기 전 구간을 프로필 lock으로 직렬화(이중 기동=프로필 손상 차단).
    with _profile_launch_lock(profile, int(cfg.get("chrome_launch_lock_timeout_s", 90))):
        if _cdp_alive(port):
            if _port_owner_foreign(port, profile, bool(cfg.get("adopt_unverified_chrome", False))):
                log.error("CDP %d를 다른 프로필의 Chrome이 점유 — adopt 거부(세션 오적재 방지).", port)
                _notify_mac("오하이테크 광고 수집 불가",
                            f"포트 {port}를 다른 프로필의 Chrome이 점유 — 그 Chrome 종료 필요.")
                raise RuntimeError("chrome_port_foreign")
            log.info("기존 Chrome(CDP %d) 감지 — adopt(내가 닫지 않음).", port)
        elif _profile_chrome_alive(profile):
            # CDP는 죽었는데 프로필은 점유 중 = 다른 포트/수동 Chrome. 중복 launch는 프로필 손상.
            log.error("프로필 점유 중이나 CDP(%d) 미응답 — 수동 Chrome 종료 필요(%s).", port, profile)
            _notify_mac("오하이테크 광고 수집 불가",
                        f"다른 Chrome이 오하이테크 프로필을 점유 중 — 포트 {port} 기동 불가. 수동 Chrome 종료 필요.")
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
                    log.info("로그인 대기 위해 Chrome 창 유지 — 로그인 후 '광고비 갱신'을 다시 누르세요.")
                else:
                    _close_chrome(owner.proc)
        finally:
            with contextlib.suppress(ValueError):
                _LIVE_OWNERS.remove(owner)


def cmd_chrome(cfg: dict) -> int:
    """CDP용 실제 Google Chrome 인스턴스 수동 실행(--remote-debugging-port + 별도 프로필).

    오하이테크 자동화 전용 프로필 → 오픽스(9222)·개인 Chrome과 충돌·세션공유 없음(D-7).
    실행 후 그 창에서 Jino가 직접 오하이테크 광고센터 로그인(AI 비번입력 금지, D-7).
    커맨드라인은 per-fetch 기동과 동일(_chrome_argv).
    """
    port = int(cfg["cdp_port"])
    profile = os.path.expanduser(cfg["cdp_profile"])
    # ★수동 경로도 같은 프로필 lock 안에서(codex R2) — 이중 기동=프로필 손상.
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
        with _owned_chrome(cfg) as owner, _cdp_page(cfg) as (page, _browser):
            owner.keep_open = True   # 캡처는 사람이 창에서 조회해야 하는 수동 명령 — 창 유지
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

    Chrome이 안 떠 있으면 스스로 띄우고(9224) attach → 끝나면 내가 띄운 창은 닫는다(2026-07-27).
    세션 만료 시 fetch가 로그인HTML/401 → 알림 + 그 창을 남겨(사람 재로그인) 실패 반환.
    """
    owner = _ChromeOwner()
    # 응답 판정은 창을 닫기 전에 끝낸다(with 블록 안) — 세션 만료로 판정되면 그 창을 남겨야
    # 사람이 바로 로그인할 수 있다. 창을 먼저 닫으면 "재로그인하세요" 알림에 로그인할 창이 없다.
    days: list[dict] | None = None
    try:
        with _owned_chrome(cfg, owner), _cdp_page(cfg) as (page, _browser):
            try:
                page.goto(DASH_URL, wait_until="domcontentloaded", timeout=40000)
            except Exception as e:  # noqa: BLE001
                log.warning("대시보드 goto 경고(계속): %s", str(e)[:120])
            page.wait_for_timeout(2000)  # Akamai/세션 안정화
            if _is_logged_out(page.url):
                log.error("세션 만료(로그인 페이지) — Chrome 창에서 오하이테크 재로그인 필요.")
                _notify_mac("오하이테크 광고 로그인 필요", "광고비 수집 세션 만료 — Chrome 창에서 재로그인하세요.")
                owner.keep_open = True   # 로그인할 창이 필요 → 닫지 않음
                return 1
            res = page.evaluate(_SALES_FETCH_JS, _sales_payload(cfg))
            status = res.get("status") if res else None
            body = (res or {}).get("body") or ""
            if status not in (200, 201):
                if status == 200 and any(x in body.lower() for x in ("login", "signin", "kccontext")):
                    log.error("세션 만료(로그인 HTML) — 재로그인 필요.")
                    _notify_mac("오하이테크 광고 로그인 필요", "광고비 수집 세션 만료 — Chrome 창에서 재로그인하세요.")
                    owner.keep_open = True
                else:
                    log.error("report/SALES 비정상 status=%s — %s", status, body[:160].replace("\n", " "))
                return 1
            days = _parse_sales_days(body)
            if days is None:
                # SALES 일별맵 아님(세션만료 200/인증 envelope) — 사일런트 동결 차단(리뷰 P2③).
                log.error("report/SALES 응답이 유효한 SALES 데이터 아님(세션 만료 가능) — 갱신 실패.")
                _notify_mac("오하이테크 광고 수집 실패", "report/SALES 응답 비정상(세션 만료 가능) — Chrome 창에서 재로그인 확인.")
                owner.keep_open = True   # 세션 만료 가능 → 확인·로그인할 창 유지
                return 1
    except RuntimeError:
        return 2
    except Exception as e:  # noqa: BLE001
        log.error("report/SALES fetch 오류: %s", str(e)[:160])
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
    """[폐기됨 2026-07-27] Chrome 상주 supervisor — 이제 아무 것도 띄우지 않는다.

    왜 폐기: launchd KeepAlive로 Chrome 수명을 붙잡는 구조라, Jino가 창을 닫으면 10~30초 뒤
    되살아났다(불편의 실측 범인). 버튼-only 모델에서 Chrome은 poll 데몬이 fetch 때만 띄운다.

    커맨드를 지우지 않고 남기는 이유(전환 안전): Mac에 구 plist(com.ohisell.ohitech-chrome)가
    아직 남아 있는 상태에서 스크립트만 갱신되면 usage 에러 → KeepAlive 크래시 루프가 된다.
    그래서 "Chrome을 띄우지 않고 그냥 block"한다. 전환 절차는 이 잡을 bootout·plist 삭제.
    """
    import time as _time

    log.warning("[deprecated] chrome-supervise는 폐기됨(버튼-only 전환) — Chrome을 띄우지 않고 대기만 합니다. "
                "launchctl bootout gui/$(id -u)/com.ohisell.ohitech-chrome 후 plist를 삭제하세요.")
    while True:  # launchd가 bootout할 때까지 no-op block(재기동 폭주 방지)
        _time.sleep(3600)


def cmd_poll(cfg: dict) -> int:
    """상주 poll 데몬 — '광고비 갱신' 버튼 요청만 감지·실행(순수 on-demand, S3 트랙 D-11).

    poll_interval_s마다 prod refresh-status 확인(가벼운 GET, 창 안 뜸) → requested면 claim(소비) 후 run.
    ★자동(일별 daily_run_hours) 실행은 제거됨 — 창을 스스로 띄우지 않고 버튼 누를 때만 뜬다.
    ★Chrome도 이 데몬이 run 때만 띄웠다 닫는다(supervisor 폐기, 2026-07-27).
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
                                # rc==2=Chrome 기동 실패/프로필 점유 충돌 → 사일런트 정지 방지 알림.
                                if rc == 2:
                                    _notify_mac("오하이테크 광고 수집 불가",
                                                f"수집용 Chrome(CDP {cfg.get('cdp_port')}) 기동 실패 — "
                                                "다른 Chrome이 프로필 점유 중인지 확인.")
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
    _install_signal_cleanup()   # SIGTERM(launchd bootout) 시 내가 띄운 Chrome 회수
    cfg = load_config()
    arg = sys.argv[1] if len(sys.argv) >= 2 else ""
    if arg == "chrome":  # Chrome 기동은 락 불필요(즉시 리턴)
        sys.exit(cmd_chrome(cfg))
    if arg == "chrome-supervise":  # [폐기] 구 plist 전환 안전용 no-op block
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
