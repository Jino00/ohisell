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
#   - 판매분석 = /retail-insight/api/business-insight/vi-detail-search (POST JSON) → **하루 단위**
#                루프 → 우리 레코드 계약으로 매핑해 push (트랙 coupang-promo-pnl, D-CPP-2/5).
#   - 프로모션 = /promotion/promotion-request (목록+상세) → 레코드 계약 매핑 → push (D-CPP-1/7).
#     ※ 이 두 스트림만 예외적으로 페처가 매핑한다: 백엔드 파서(clients/coupang/rocket_promo.py)의
#       입력은 "우리 레코드 계약"이고, 쿠팡 원시 스키마를 아는 유일한 층이 여기이기 때문(PLAN §0.1).
#
# 데몬 방식 (2026-07-27 개정 — 순수 버튼-only): launchd KeepAlive 상주 poll 데몬 1개(com.ohisell.rocket).
#   평소엔 30초마다 가벼운 GET만(창 안 뜸). UI '갱신' 버튼 요청을 claim했을 때만 Chrome을 띄우고
#   작업이 끝나면 닫는다. ★Chrome 상주 supervisor(com.ohisell.rocket-chrome, KeepAlive)는 폐기 —
#   Jino가 창을 닫아도 launchd가 되살리던 원인이었다(설계문서 2026-07-27 개정 참조).
#
# 사용:
#   전용 Chrome:  backend/.venv/bin/python3 tools/rocket_supplier_fetcher.py chrome   # 수동 기동→로그인
#   세션 감지:    backend/.venv/bin/python3 tools/rocket_supplier_fetcher.py login    # PO list 200 자동감지
#   실행(1회):    backend/.venv/bin/python3 tools/rocket_supplier_fetcher.py          # run: fetch→push
#   (launchd):    backend/.venv/bin/python3 tools/rocket_supplier_fetcher.py poll     # 버튼 요청만 소비
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
import re
import signal
import subprocess
import sys
import time
import urllib.request
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

# ── 프로모션 손익 레이어(트랙 coupang-promo-pnl) 경로 — 2026-07-28 라이브 정찰 실측 ──
#   ★추측 아님: 아래 셋 다 살아있는 세션에서 200 응답과 필드를 직접 확인하고 적었다.
SALES_SEARCH_PATH = "/retail-insight/api/business-insight/vi-detail-search"   # 판매분석 POST JSON
SUBSCRIPTION_PATH = "/rpd/v2/supplier/subscription/detail"                    # 구독 게이트(D-CPP-5)
PROMOTION_LIST_PATH = "/promotion/promotion-request"                          # 프로모션 목록(Spring Page)
PROMOTION_DETAIL_PATH = "/promotion/promotion-request"                        # + /{requestId}

# ★실제 Google Chrome 고정(Playwright 번들 Chromium 금지): supplier.coupang.com은 Akamai가
#   Chrome for Testing 핑거프린트를 차단한다(트랙 rocket-1p D-1 실측).
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
KST = ZoneInfo("Asia/Seoul")

# prod ingest 엔드포인트(S2/S4.5a 라우터, 무변경)
PO_INGEST_PATH = "/api/coupang/ops/rocket/po/ingest"
SETTLEMENT_INGEST_PATH = "/api/coupang/ops/rocket/settlement/ingest"
PO_DETAIL_INGEST_PATH = "/api/coupang/ops/rocket/po-detail/ingest"
SALES_INGEST_PATH = "/api/coupang/ops/rocket/sales/ingest"            # 트랙 coupang-promo-pnl Phase 1
PROMOTION_INGEST_PATH = "/api/coupang/ops/rocket/promotion/ingest"    # 〃

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

# JSON POST — 판매분석(vi-detail-search)은 검색 조건을 body로 받는다. **조회 전용**(상태 변경 없음).
# 인자=[path, bodyObj]. 반환 {status, body}. 비200(400 INVALID_DATE·403 구독)도 body를 그대로 준다 —
#   호출자가 그 본문에서 유효 구간을 읽어 창을 보정하고, 구독 차단을 실패로 표면화한다(D-CPP-5).
_FETCH_JSON_POST_JS = """async (args) => {
  const [path, body] = args;
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 25000);
  try {
    const r = await fetch(path, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: ctrl.signal,
    });
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
    # ── 프로모션 손익 레이어(트랙 coupang-promo-pnl Phase 1) ──
    # 판매분석: 기본은 **최근 7일 롤링 재수집**(멱등 upsert라 매 실행 덮어써도 안전 — 확정 전
    #   수치가 뒤늦게 정정되는 것을 따라잡는다). sales_backfill_days>0이면 그 일수로 창을 넓힌다
    #   (서버가 알려주는 유효 구간으로 자동 클램프 — 롤링 창이라 하드코딩하면 반드시 틀어진다).
    cfg.setdefault("collect_sales", True)
    cfg.setdefault("sales_days", 7)
    cfg.setdefault("sales_backfill_days", 0)
    cfg.setdefault("sales_page_size", 20)
    cfg.setdefault("sales_max_pages", 40)
    # 프로모션: 전량 upsert(2026-07-28 실측 totalElements=7 — 계정 전체가 한 페이지에 들어온다).
    cfg.setdefault("collect_promotion", True)
    cfg.setdefault("promo_page_size", 25)
    cfg.setdefault("promo_max_pages", 20)
    cfg.setdefault("promo_detail_max", 100)
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
# per-fetch Chrome 수명 (2026-07-27 — supervisor 폐기, 버튼 누를 때만 창)
# ════════════════════════════════════════════════════════════════════
# 왜: 상주 supervisor(launchd KeepAlive=true)는 Jino가 창을 닫으면 10~30초 뒤 Chrome을
#   되살렸다(세션 보온의 대가). 버튼-only 모델에서 창은 "버튼 누른 그 순간 1회"만 떠야 하므로,
#   poll 데몬이 요청을 claim한 뒤 스스로 Chrome을 띄우고 작업이 끝나면 닫는다.
# ★소유권 규칙: 내가 띄운 Chrome만 내가 닫는다. 이미 떠 있던 Chrome(사람이 로그인하려고
#   띄운 창 등)은 adopt만 하고 절대 닫지 않는다.
def _cdp_alive(port: int) -> bool:
    """CDP 디버깅 엔드포인트가 200이면 True (/json/version HTTP 프로브).

    TCP LISTEN만 보면 행(hang)·기동 중 Chrome을 살아있다 오판하므로 실제 응답을 확인한다.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
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
    """프로필을 점유 중인 살아있는 Chrome이 있는지 — SingletonLock PID 생존+cmdline 확인.

    CDP가 행(hang)/기동 중이라 _cdp_alive가 거짓이어도 Chrome이 user-data-dir을 점유 중이면
    lock 청소·중복 launch가 프로필을 손상시킨다(wing/ohitech 페처 codex 교훈 복제). PID 생존만으론
    부족하므로(PID 재사용) 그 PID의 cmdline에 이 프로필이 있어야만 점유로 인정한다.
    """
    # ※_profile_owner_pid와 로직이 겹치지만 **통합하지 않는다**: 이쪽은 PermissionError(타 유저
    #   Chrome이 프로필 점유)를 "점유 중"으로 봐서 기동을 막아야 안전하고, 저쪽은 "우리 것 아님"으로
    #   봐서 adopt를 막아야 안전하다. 실패의 안전한 방향이 서로 반대다.
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
    lock 파일이 분리된 다계정 인스턴스와는 배타되지 않는다. 둘 다 "비어 있음"을 보고 Singleton
    파일을 지운 뒤 Chrome을 이중 기동하면 프로필·쿠키 DB가 손상된다. lock 경로는 프로필에서
    결정되므로 프로세스·페처가 달라도 같은 파일을 놓고 배타된다.
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
    port = int(cfg.get("cdp_port", 9223))
    profile = os.path.expanduser(cfg.get("cdp_profile", "~/.ohisell_supplier_chrome"))
    return [
        CHROME_BIN,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        SUPPLIER_ORIGIN,
    ]


def _launch_chrome(cfg: dict):
    """실제 Chrome을 백그라운드 자식으로 기동. 반환 Popen(실패 시 None). stale lock은 먼저 청소."""
    if not os.path.exists(CHROME_BIN):
        log.error("Chrome을 찾을 수 없습니다: %s", CHROME_BIN)
        return None
    profile = os.path.expanduser(cfg.get("cdp_profile", "~/.ohisell_supplier_chrome"))
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
    기동 실패·프로필 점유 충돌은 RuntimeError(호출자의 기존 오류 경로가 처리).
    """
    owner = owner if owner is not None else _ChromeOwner()
    port = int(cfg.get("cdp_port", 9223))
    profile = os.path.expanduser(cfg.get("cdp_profile", "~/.ohisell_supplier_chrome"))
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
# ④ 판매분석 옵션×일 수집 (트랙 coupang-promo-pnl Phase 1, D-CPP-2/5)
# ────────────────────────────────────────────────────────────────────
# 원천: POST /retail-insight/api/business-insight/vi-detail-search (2026-07-28 라이브 실측).
#   ★그레인 주의: 이 API는 **요청 구간을 합산해서** 준다(7일 요청 = 7일 합계 1행). 옵션×일을
#     만들려면 startDate=endDate로 **하루씩** 호출하는 수밖에 없다. 구간을 그대로 넣고 일별이라
#     믿으면 같은 값이 7일에 복제되어 조용히 7배가 된다.
#   ★유효 구간은 롤링이다(실측 [2026-06-01 ~ 2026-07-27], 약 57일). 범위 밖 날짜는 400
#     INVALID_DATE와 함께 **서버가 유효 구간을 본문에 적어 준다** → 그걸 파싱해 창을 보정한다.
#     일수를 하드코딩하면 매일 하루씩 틀어진다.
#   ★D-CPP-5(구독 게이트): 판매분석은 BETA + 무료체험(현재 종료일 2026-08-20). 접근이 끊기면
#     조용히 0행이 되어 "안 팔린 날"로 보인다 → 구독 조회·403을 **실패로 표면화**한다.
# ════════════════════════════════════════════════════════════════════
class _SalesAccessDenied(RuntimeError):
    """판매분석 접근 차단(구독 만료·권한 회수·403). 조용한 skip 금지 신호(D-CPP-5)."""


def _sales_window_days(cfg: dict, today: date) -> list[date]:
    """수집 대상 날짜 목록(오래된 날 → 최신). 기본 = 최근 sales_days일 롤링 재수집.

    ★오늘을 포함한다: 오늘치가 아직 없으면 서버가 400으로 유효 구간을 알려주고 그때 잘린다.
      'D-1까지'를 코드에 박으면 쿠팡이 마감 시각을 바꾼 날 조용히 하루를 잃는다.
    sales_backfill_days>0이면 그 일수로 창을 넓힌다(유효 구간을 넘으면 자동 클램프).
    """
    backfill = int(cfg.get("sales_backfill_days", 0) or 0)
    n = backfill if backfill > 0 else int(cfg.get("sales_days", 7) or 7)
    n = max(1, n)
    return [today - timedelta(days=i) for i in range(n - 1, -1, -1)]


def _parse_viewable_period(body: str) -> tuple[date, date] | None:
    """400 응답 본문에서 서버가 알려준 유효 구간 `[YYYY-MM-DD ~ YYYY-MM-DD]`을 뽑는다.

    실측 본문: {"code":"INVALID_DATE","message":"[vendorId:A01029796] Date 2025-01-01 is
                outside the viewable period [2026-06-01 ~ 2026-07-27]"}
    ★`[vendorId:...]`도 대괄호지만 '날짜 ~ 날짜' 형태가 아니라서 이 정규식엔 걸리지 않는다.
    """
    m = re.search(r"\[\s*(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})\s*\]", body or "")
    if not m:
        return None
    try:
        lo = date.fromisoformat(m.group(1))
        hi = date.fromisoformat(m.group(2))
    except ValueError:
        return None
    return (lo, hi) if lo <= hi else (hi, lo)


def _clamp_days(days: list[date], period: tuple[date, date] | None) -> list[date]:
    """유효 구간 밖 날짜 제거. period가 없으면 그대로(아직 모르는 상태)."""
    if not period:
        return list(days)
    lo, hi = period
    return [d for d in days if lo <= d <= hi]


def _sales_page_meta(payload: dict) -> dict:
    """vi-detail-search 응답의 paginationDetails(실측 {pageSize,pageNumber,totalResults,totalPages})."""
    pd = (payload or {}).get("paginationDetails") or {}

    def _i(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    return {
        "page_number": _i(pd.get("pageNumber")),
        "total_pages": _i(pd.get("totalPages")),
        "total_results": _i(pd.get("totalResults")),
    }


def _sales_records(payload: dict, day: date) -> list[dict]:
    """쿠팡 원시 vendorItems → **우리 레코드 계약**(PLAN §4 sales). 순수 함수(HTTP 없음).

    실측 매핑(2026-07-28):
      option_id       ← vendorItemDetails.vendorItemId   (쿠팡 옵션ID = 우리 그레인 키)
      sku_id          ← vendorItemDetails.externalSkuIds[0] (= 발주 product_number, 실측 62178970)
      qty             ← businessInsightsMetricsResponse.totalUnitsSold
      revenue         ← 〃.totalGmv        ★소비자 실현가 — 회계 매출 아님(D-CPP-2)
      visitors        ← 〃.totalUniqueVisitor
      conversion_rate ← 〃.pvToOrder       ★이미 0~1 소수다(실측 검산: 140주문/1141PV=0.12269…와
                                            pvToOrder 0.12269938650306748이 자릿수까지 일치).
                                            계약이 0~1을 요구하므로 100으로 나누지 않는다.
      product_name    ← itemName(옵션명; 없으면 productName)
    ★qty/revenue 키는 **항상 넣는다**(값이 None이어도): 백엔드 파서는 '키 없음 = 매핑이 필드명을
      놓침'으로 보고 행을 skip하고, '키 있음 + 빈 값 = 0판매일'로 본다. 쿠팡이 필드명을 바꾸면
      전 행의 값이 None이 되어 배치 경보(blank_qty==accepted)가 울린다 — 이게 유일한 탐지 경로다.
    ★sku_id는 externalSkuIds가 **정확히 하나일 때만** 채운다: 여러 개면 원가 브리지 키를 하나로
      고를 수 없다. 아무거나 고르면 영원히 잘못된 원가에 붙는다(잘못 붙느니 비워 둔다).
    ★vendorItemDetails.vendorId(실측 A00010028)는 **상품의 리테일 vendor**이지 우리 계정축이
      아니다(우리 계정 = A01029796). 계정축 vendor_id는 push 때 설정에서 주입한다.
    """
    out: list[dict] = []
    for it in (payload or {}).get("vendorItems") or []:
        if not isinstance(it, dict):
            continue
        det = it.get("vendorItemDetails") or {}
        met = it.get("businessInsightsMetricsResponse") or {}
        if not isinstance(det, dict) or not isinstance(met, dict):
            continue
        vi = det.get("vendorItemId")
        if vi is None or str(vi).strip() == "":
            continue
        skus = det.get("externalSkuIds") or []
        sku = None
        if isinstance(skus, list) and len(skus) == 1 and skus[0] not in (None, ""):
            sku = str(skus[0])
        out.append({
            "option_id": str(vi),
            "date": day.isoformat(),
            "sku_id": sku,
            "qty": met.get("totalUnitsSold"),
            "revenue": met.get("totalGmv"),
            "visitors": met.get("totalUniqueVisitor"),
            "conversion_rate": met.get("pvToOrder"),
            "product_name": det.get("itemName") or det.get("productName"),
        })
    return out


def _sales_access_ok(page) -> tuple[bool, str]:
    """D-CPP-5 구독 게이트 확인. (True,"") / (False,사유). 사유는 실패 보고에 그대로 실린다."""
    res = _eval_retry(page, _FETCH_TEXT_JS, [SUBSCRIPTION_PATH])
    status = (res or {}).get("status")
    body = (res or {}).get("body") or ""
    if status != 200:
        return False, f"구독 상태 조회 HTTP {status}: {body[:160]}"
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return False, "구독 상태 응답이 JSON 아님(로그인 HTML 의심)"
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not data:
        return False, f"구독 상태 data 없음: {body[:160]}"
    info = data.get("detailInfo") or {}
    log.info(
        "판매분석 구독 게이트: permittedLevel=%s subscribed=%s freeTrialEnd=%s "
        "(D-CPP-5 — 만료되면 수집이 조용히 0행이 된다)",
        data.get("permittedLevel"),
        (info or {}).get("subscribedLevel"),
        (info or {}).get("freeTrialEndDate"),
    )
    return True, ""


def _fetch_sales_page(page, cfg: dict, day: date, page_no: int):
    """판매분석 한 날짜·한 페이지 fetch. 반환 (payload|None, status, body).

    body는 400일 때 유효 구간을 읽기 위해 그대로 돌려준다.
    """
    body = {
        "startDate": day.isoformat(),
        "endDate": day.isoformat(),          # ★하루 단위 — 위 섹션 주석(구간 합산 함정) 참조
        "registrationTypes": ["RETAIL"],
        "pageNumber": page_no,
        "pageSize": int(cfg.get("sales_page_size", 20)),
        "sortBy": "GMV",
        "sortOrder": "DESC",
        "isKanCategoryCode": True,
    }
    res = _eval_retry(page, _FETCH_JSON_POST_JS, [SALES_SEARCH_PATH, body])
    status = (res or {}).get("status")
    text = (res or {}).get("body") or ""
    if status != 200:
        return None, status, text
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None, status, text
    return (payload if isinstance(payload, dict) else None), status, text


def _collect_sales_day(page, cfg: dict, day: date):
    """한 날짜의 전 페이지 수집. 반환 (rows, period_hint|None).

    period_hint는 400 INVALID_DATE에서 읽은 유효 구간(호출자가 나머지 날짜를 클램프).
    403(구독/권한)은 _SalesAccessDenied로 올린다 — 조용히 0행으로 접지 않는다(D-CPP-5).
    """
    max_pages = int(cfg.get("sales_max_pages", 40))
    rows: list[dict] = []
    page_no = 0
    while page_no < max_pages:
        payload, status, text = _fetch_sales_page(page, cfg, day, page_no)
        if status == 403:
            raise _SalesAccessDenied(f"판매분석 403(구독/권한) {day}: {text[:160]}")
        if payload is None:
            period = _parse_viewable_period(text) if status == 400 else None
            if period is not None:
                return rows, period
            raise RuntimeError(f"판매분석 {day} page={page_no} 비정상(status={status}): {text[:160]}")
        rows.extend(_sales_records(payload, day))
        meta = _sales_page_meta(payload)
        total_pages = meta["total_pages"]
        if total_pages <= 0 or page_no + 1 >= total_pages:
            break
        page_no += 1
        page.wait_for_timeout(300)   # 폴라이트 간격
    return rows, None


def _collect_sales_rows(page, cfg: dict) -> tuple[list[dict], dict]:
    """설정 창의 날짜를 하루씩 수집 → 레코드 계약 리스트. 반환 (rows, stats).

    유효 구간을 처음 알게 되면(400 응답) 남은 날짜를 즉시 클램프하고, 그 날짜가 구간 안이면
    **한 번만** 재시도한다(무한 재시도 금지 — 같은 400이 반복되면 그날은 포기하고 계속).
    """
    ok, why = _sales_access_ok(page)
    if not ok:
        raise _SalesAccessDenied(f"판매분석 접근 불가(D-CPP-5): {why}")
    today = datetime.now(KST).date()
    pending = _sales_window_days(cfg, today)
    stats = {"days_requested": len(pending), "days_collected": 0, "days_clamped": 0, "rows": 0}
    period: tuple[date, date] | None = None
    retried: set = set()
    rows: list[dict] = []
    while pending:
        day = pending.pop(0)
        if period and not (period[0] <= day <= period[1]):
            stats["days_clamped"] += 1
            continue
        day_rows, hint = _collect_sales_day(page, cfg, day)
        if hint is not None and period is None:
            period = hint
            log.info("판매분석 유효 구간 수신: %s ~ %s — 창을 자동 보정한다(하드코딩 아님)", *period)
            pending = _clamp_days(pending, period)
        if hint is not None:
            # 이 날짜 자체가 400이었다. 구간 안이면 1회만 재시도, 아니면 클램프로 계산.
            if period and period[0] <= day <= period[1] and day not in retried:
                retried.add(day)
                pending.insert(0, day)
            else:
                stats["days_clamped"] += 1
            continue
        rows.extend(day_rows)
        stats["days_collected"] += 1
        page.wait_for_timeout(300)
    stats["rows"] = len(rows)
    log.info(
        "판매분석 수집: %d일 요청 → %d일 수집·%d일 범위밖, 레코드 %d건",
        stats["days_requested"], stats["days_collected"], stats["days_clamped"], len(rows),
    )
    return rows, stats


# ════════════════════════════════════════════════════════════════════
# ⑤ 프로모션 신청 수집 (트랙 coupang-promo-pnl Phase 1, D-CPP-1/7)
# ────────────────────────────────────────────────────────────────────
# 원천: GET /promotion/promotion-request?requestType=COMMON&page&size (Spring Page) + 상세 /{id}.
#   실측(2026-07-28): totalElements=7 — 계정 전체가 한 페이지에 들어온다. 전량 upsert가 싸다.
#   ★상세 응답은 목록 항목과 **필드가 동일**했다(신규 필드 없음). 그래도 상세를 부르는 이유는
#     ①raw 보존의 정본을 상세로 고정하고 ②쿠팡이 상세에만 필드를 추가할 때 자동으로 따라가기 위함.
#   ★단위 할인액은 목록에도 상세에도 **없다** → D-CPP-7(수기 1칸). 페처는 그 칸을 쓰지 않는다.
# ════════════════════════════════════════════════════════════════════
def _promo_page_meta(payload: dict) -> dict:
    """Spring Page 메타(totalPages/last/number)."""
    def _i(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    return {
        "total_pages": _i((payload or {}).get("totalPages")),
        "number": _i((payload or {}).get("number")),
        "last": bool((payload or {}).get("last")),
    }


def _promotion_record(item: dict) -> dict | None:
    """쿠팡 원시 프로모션 항목 → **우리 레코드 계약**(PLAN §4 promotion). 순수 함수.

    실측 매핑(2026-07-28, Request 687878):
      request_id  ← requestId          promotion_name ← title
      contract_id ← contractId         promotion_type ← promotionType (INSTANT_DISCOUNT)
      status      ← status (COMPLETE)  discount_method ← discountType (FIXED_AMOUNT_WITH_QUANTITY)
      start_at    ← effectiveDate      end_at ← expiryDate      ★둘 다 tz(+09:00) 포함 ISO —
                    백엔드 파서가 KST로 환산해 naive로 저장한다(초 단위 보존).
      share_ratio ← supplierFundRate (100 = 전액 셀러 부담)
      budget_amount ← discountBudget (총예산 — 단위 할인액이 아니다)
      applied_product_count ← detailCount     requested_at ← createdAt
      raw         ← 원본 전체(detailStatus·bmInfoList 등 미매핑 필드 사후 복구용)
    ★없는 것을 지어내지 않는다:
      - discount_value(단위 할인액): 응답에 없다 → None. 수기 unit_discount_amount로 받는다(D-CPP-7).
      - settlement_date(정산일): 목록·상세 어디에도 없다 → None. 화면에만 있는 값이면 추후 별도 정찰.
    """
    if not isinstance(item, dict):
        return None
    rid = item.get("requestId")
    if rid is None or str(rid).strip() == "":
        return None
    return {
        "request_id": str(rid),
        "contract_id": None if item.get("contractId") is None else str(item.get("contractId")),
        "promotion_name": item.get("title"),
        "promotion_type": item.get("promotionType"),
        "status": item.get("status"),
        "start_at": item.get("effectiveDate"),
        "end_at": item.get("expiryDate"),
        "share_ratio": item.get("supplierFundRate"),
        "discount_method": item.get("discountType"),
        "discount_value": None,          # API에 없음(D-CPP-7) — 추측 금지
        "budget_amount": item.get("discountBudget"),
        "settlement_date": None,         # API에 없음(2026-07-28 실측)
        "applied_product_count": item.get("detailCount"),
        "requested_at": item.get("createdAt"),
        "raw": item,
    }


def _fetch_promotion_detail(page, request_id: str) -> dict | None:
    """프로모션 상세 1건. 실패(비200·비JSON)면 None — 목록 항목으로 폴백한다."""
    res = _eval_retry(page, _FETCH_TEXT_JS, [f"{PROMOTION_DETAIL_PATH}/{request_id}"])
    if (res or {}).get("status") != 200:
        return None
    try:
        payload = json.loads((res or {}).get("body") or "")
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) and payload.get("requestId") is not None else None


def _collect_promotion_rows(page, cfg: dict) -> tuple[list[dict], dict]:
    """프로모션 목록(+상세 병합) → 레코드 계약 리스트. 반환 (rows, stats).

    상세 실패는 그 건만 목록 값으로 폴백(수집 자체를 죽이지 않는다). 목록 실패는 RuntimeError.
    """
    size = int(cfg.get("promo_page_size", 25))
    max_pages = int(cfg.get("promo_max_pages", 20))
    detail_max = int(cfg.get("promo_detail_max", 100))
    items: list[dict] = []
    for page_no in range(max_pages):
        q = urlencode({"requestType": "COMMON", "page": page_no, "size": size})
        res = _eval_retry(page, _FETCH_TEXT_JS, [f"{PROMOTION_LIST_PATH}?{q}"])
        status = (res or {}).get("status")
        text = (res or {}).get("body") or ""
        if status != 200:
            raise RuntimeError(f"프로모션 목록 page={page_no} HTTP {status}: {text[:160]}")
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            raise RuntimeError(f"프로모션 목록 page={page_no} 비JSON(세션 만료 의심): {text[:120]}")
        content = (payload or {}).get("content") or []
        items.extend([c for c in content if isinstance(c, dict)])
        meta = _promo_page_meta(payload)
        # 빈 페이지를 종료 조건의 정본으로 둔다(실측: page=99도 200 + content=[]).
        if not content or meta["last"] or (meta["total_pages"] > 0 and page_no + 1 >= meta["total_pages"]):
            break
        page.wait_for_timeout(300)
    rows: list[dict] = []
    detail_ok = 0
    detail_fail = 0
    for i, item in enumerate(items):
        merged = item
        if i < detail_max:
            detail = _fetch_promotion_detail(page, str(item.get("requestId")))
            if detail is not None:
                merged = {**item, **detail}   # 상세가 이긴다(같은 필드면 더 최신·정본)
                detail_ok += 1
            else:
                detail_fail += 1
            page.wait_for_timeout(200)
        rec = _promotion_record(merged)
        if rec is not None:
            rows.append(rec)
    stats = {"listed": len(items), "rows": len(rows),
             "detail_ok": detail_ok, "detail_failed": detail_fail}
    log.info("프로모션 수집: 목록 %d건 → 레코드 %d건(상세 성공 %d·실패 %d)",
             len(items), len(rows), detail_ok, detail_fail)
    if detail_fail:
        log.warning("프로모션 상세 실패 %d건 — 목록 값으로 폴백(필드 누락 가능)", detail_fail)
    return rows, stats


# ════════════════════════════════════════════════════════════════════
# 프로모션 손익 레이어 push
# ════════════════════════════════════════════════════════════════════
_SALES_PUSH_CHUNK = 500   # 백필(수십 일 × 수십 옵션)이 한 요청에 몰려 타임아웃/메모리를 때리지 않게.


def _warn_ingest_health(label: str, resp) -> None:
    """ingest 응답의 수집 건강 신호를 페처 로그에도 남긴다(백엔드 로그만 보면 놓친다).

    skipped>0 = 계약 위반(매핑 점검). blank_qty/blank_revenue == accepted = **매핑 사고**
    (0판매일이 아니다 — 쿠팡 필드명 변경 등으로 전 행의 값이 비었다는 뜻).
    """
    if not isinstance(resp, dict):
        return
    if resp.get("skipped"):
        log.warning("%s ingest: 계약 위반 skip %s건 — 매핑 점검 필요", label, resp.get("skipped"))
    accepted = resp.get("accepted") or 0
    if accepted and (resp.get("blank_qty") == accepted or resp.get("blank_revenue") == accepted):
        log.error(
            "%s ingest: 배치 %d행 전부에서 수량/매출이 빈 값 — 0판매일이 아니라 응답 필드명 변경을 "
            "의심할 것(blank_qty=%s blank_revenue=%s)",
            label, accepted, resp.get("blank_qty"), resp.get("blank_revenue"),
        )


def _push_sales(cfg: dict, rows: list[dict]) -> int:
    """판매분석 레코드 → prod ingest(멱등 upsert). 0=성공/1=실패. 빈 수집은 push 안 함."""
    if not rows:
        log.info("판매분석 push: 레코드 0건 — 건너뜀")
        return 0
    rc = 0
    for i in range(0, len(rows), _SALES_PUSH_CHUNK):
        chunk = rows[i:i + _SALES_PUSH_CHUNK]
        try:
            pr = requests.post(
                cfg["prod_base_url"].rstrip("/") + SALES_INGEST_PATH,
                json={"vendor_id": cfg["vendor_id"], "source": "sales_analysis", "rows": chunk},
                headers={"X-Ingest-Token": cfg["ingest_token"]},
                timeout=60,
            )
        except requests.RequestException as e:
            log.error("판매분석 push 네트워크 오류(%d~): %s", i, e)
            rc = 1
            continue
        if pr.status_code != 200:
            log.error("판매분석 push 실패 HTTP %s — %s", pr.status_code, pr.text[:200])
            rc = 1
            continue
        body = _json_or_text(pr)
        log.info("판매분석 push 성공 %d건 → %s", len(chunk), body)
        _warn_ingest_health("판매분석", body)
    return rc


def _push_promotions(cfg: dict, rows: list[dict]) -> int:
    """프로모션 레코드 → prod ingest(멱등 upsert, grain=request_id). 0=성공/1=실패."""
    if not rows:
        log.info("프로모션 push: 레코드 0건 — 건너뜀")
        return 0
    try:
        pr = requests.post(
            cfg["prod_base_url"].rstrip("/") + PROMOTION_INGEST_PATH,
            json={"vendor_id": cfg["vendor_id"], "rows": rows},
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            timeout=60,
        )
    except requests.RequestException as e:
        log.error("프로모션 push 네트워크 오류: %s", e)
        return 1
    if pr.status_code != 200:
        log.error("프로모션 push 실패 HTTP %s — %s", pr.status_code, pr.text[:200])
        return 1
    body = _json_or_text(pr)
    log.info("프로모션 push 성공 %d건 → %s", len(rows), body)
    _warn_ingest_health("프로모션", body)
    return 0


def _collect_and_push_promo_pnl(page, cfg: dict) -> int:
    """판매분석·프로모션 두 스트림을 수집→push. 반환 rc(0=성공, 1=하나라도 실패).

    두 스트림은 서로 독립이다 — 한쪽 실패가 다른 쪽 push를 막지 않는다(발주/정산 패턴과 동일).
    ★판매분석 접근 차단(D-CPP-5)은 **실패로 남긴다**: 조용히 넘기면 테이블이 멈춘 걸 아무도 모른다.
    """
    rc = 0
    if cfg.get("collect_sales", True):
        try:
            rows, _stats = _collect_sales_rows(page, cfg)
            rc |= _push_sales(cfg, rows)
        except _SalesAccessDenied as e:
            log.error("판매분석 수집 차단(D-CPP-5, 구독/권한 확인 필요): %s", e)
            rc = 1
        except Exception as e:  # noqa: BLE001 — 판매분석 실패가 프로모션 수집을 막지 않는다
            log.error("판매분석 수집 실패: %s", e)
            rc = 1
    if cfg.get("collect_promotion", True):
        try:
            rows, _stats = _collect_promotion_rows(page, cfg)
            rc |= _push_promotions(cfg, rows)
        except Exception as e:  # noqa: BLE001
            log.error("프로모션 수집 실패: %s", e)
            rc = 1
    return rc


# ════════════════════════════════════════════════════════════════════
# run / login / chrome 커맨드
# ════════════════════════════════════════════════════════════════════
def _do_run(cfg: dict) -> int:
    """Chrome 기동(없으면)→연결 → 발주+정산 수집 → prod push → 내가 띄운 창은 닫기.

    push 부분실패는 비0 반환. 세션 만료면 창을 남겨(사람이 그 창에서 로그인) 1을 반환한다.
    """
    if not _push_configured(cfg):
        log.error("run엔 prod_base_url·ingest_token·vendor_id 설정 필요(~/.ohisell_rocket_fetcher.json).")
        return 2
    pages: list[dict] = []
    settle_rows: list[list] = []
    rc_promo_pnl = 0
    try:
        with _owned_chrome(cfg) as owner:
            with sync_playwright() as p:
                with _chrome(p, cfg) as page:
                    if not _goto_origin(page):
                        log.error("supplier 로그아웃 상태(url=%s) — 이 창에서 로그인 후 다시 '갱신'을 누르세요.", page.url)
                        owner.keep_open = True   # 로그인할 창이 필요 → 닫지 않음
                        return RC_LOGIN_REQUIRED
                    if not _session_ok(page, cfg):
                        # ★재시도 대상으로 둔다(codex 1R[P2]): _session_ok는 Playwright 예외·
                        # Akamai 챌린지·일시적 비200까지 전부 False로 접는다. 로그아웃이 확증된
                        # 것이 아니므로 login_required로 소멸시키면 멀쩡한 세션의 일시 장애가
                        # 요청을 지운다. 진짜 로그아웃은 위 _goto_origin(URL 판정)이 잡는다.
                        log.error("발주 list 세션 미응답 — 세션 만료 의심. 이 창에서 로그인 후 다시 '갱신'을 누르세요.")
                        owner.keep_open = True
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
                    # 프로모션 손익 레이어(판매분석·프로모션) — 위 스트림들과 독립. 실패해도
                    #   발주/정산 push는 이미 끝났고, rc로만 실패가 드러난다(조용한 성공 금지).
                    rc_promo_pnl = _collect_and_push_promo_pnl(page, cfg)
    except Exception as e:  # noqa: BLE001 — Chrome 기동/브라우저/수집 오류
        log.error("브라우저 수집 오류: %s", e)
        return 1

    rc = 0 if (rc_po == 0 and rc_st == 0 and detail_failed <= 0 and rc_promo_pnl == 0) else 1
    log.info("run 완료 — 발주 push rc=%d / 정산 push rc=%d / 발주상세 실패=%d / 프로모션손익 rc=%d",
             rc_po, rc_st, detail_failed, rc_promo_pnl)
    # 성공 시 last_success_at 갱신 (UI 폴링 완료 감지용)
    if rc == 0 and _push_configured(cfg):
        # ★완료 신호는 몇 번 재시도한다(codex 5R[P1]): 유실되면 요청이 임대된 채 남아
        #   UI가 헛기다리고 TTL 뒤 중복 수집으로 이어진다.
        for _attempt in range(3):
            try:
                r = requests.post(
                    cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/rocket/fetch-success",
                    headers={"X-Ingest-Token": cfg["ingest_token"]},
                    timeout=10,
                )
                if r.status_code == 200:
                    break
                log.warning("fetch-success 비200(%s) — 재시도 %d/3", r.status_code, _attempt + 1)
            except Exception as e:  # noqa: BLE001
                log.warning("fetch-success 실패(%s) — 재시도 %d/3", str(e)[:80], _attempt + 1)
            time.sleep(2 * (_attempt + 1))
    return rc


# run 반환코드 — 3=로그인 필요(재시도 무의미: 재시도해도 실패하고 창만 반복해서 뜬다, §0).
# 1=그 외 실패(재시도 대상), 2=설정 누락, 0=성공.
RC_LOGIN_REQUIRED = 3


def _prod_report_failure(cfg: dict, error: str, kind: str | None = None,
                        lease: str | None = None) -> None:
    """run 실패를 prod에 보고 → 재시도 판정의 입력(lease 계약, PLAN_coupang-claim-retry-lease).

    보고가 없으면 lease TTL(기본 20분)이 지나야 재시도된다 — 보고하면 다음 폴(30s)에 곧바로.
    kind="login_required"면 prod가 재시도 없이 요청을 소멸시킨다(§0 금지선). best-effort.
    """
    if not _push_configured(cfg):
        return
    body = {"error": str(error)[:300]}
    if kind:
        body["kind"] = kind
    if lease:
        body["lease"] = lease   # 내 임대에 대해서만 보고(stale 보고 차단, codex 1R[P1])
    try:
        requests.post(
            cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/rocket/fetch-error",
            json=body,
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            timeout=10,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("fetch-error 보고 실패(무시): %s", str(e)[:120])


def _prod_rocket_refresh_status(cfg: dict) -> dict:
    """백엔드 rocket refresh-status 폴링."""
    try:
        r = requests.get(
            cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/rocket/refresh-status",
            timeout=10,
        )
        return r.json() if r.status_code == 200 else {}
    except Exception:  # noqa: BLE001
        return {}


def _prod_rocket_claim(cfg: dict) -> dict:
    """백엔드 rocket refresh-claim — 요청 임대(lease). 플래그는 성공/소진까지 보존."""
    try:
        r = requests.post(
            cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/rocket/refresh-claim",
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            timeout=10,
        )
        return r.json() if r.status_code == 200 else {}
    except Exception:  # noqa: BLE001
        return {}


def _prod_rocket_mark_success(cfg: dict) -> None:
    """실행 완료 시 last_success_at 갱신 (직접 DB가 아닌 전용 엔드포인트로).
    현재는 별도 엔드포인트 없으므로 ingest 성공 자체가 last_push_at 역할. 추후 확장 시 사용."""


def cmd_run(cfg: dict) -> int:
    """1회 실행(락으로 동시 실행 방지). UI 갱신 요청이 있으면 claim 후 실행."""
    # UI '갱신' 버튼 요청 확인(push 설정 있을 때만 — 로컬 테스트는 claim 불필요)
    if _push_configured(cfg):
        st = _prod_rocket_refresh_status(cfg)
        if st.get("requested"):
            _prod_rocket_claim(cfg)
            log.info("UI 갱신 요청 소비 — 즉시 실행")
    with _try_fetch_lock() as acquired:
        if not acquired:
            log.warning("다른 실행이 진행 중 — 이번 호출 건너뜀")
            return 0
        return _do_run(cfg)


def cmd_login(cfg: dict, wait_secs: int = 600) -> int:
    """전용 Chrome(없으면 기동)의 새 탭에서 supplier 진입 → 사용자 로그인 자동 감지(PO list 200).

    사람이 조작하는 명령이므로 창은 남긴다(keep_open) — 세션은 Chrome 프로필이 보관.
    """
    log.info("[login] supplier.coupang.com에 로그인하세요(자동 감지, 최대 %d초).", wait_secs)
    ok = False
    try:
        with _owned_chrome(cfg) as owner:
            owner.keep_open = True   # 로그인 창은 사람 것 — 자동으로 닫지 않는다
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
    """CDP용 전용 Chrome 수동 실행(--remote-debugging-port). recon과 같은 프로필 재사용.

    실행 후 브라우저에서 supplier.coupang.com 로그인 → 'login' 명령으로 세션 감지.
    커맨드라인은 per-fetch 기동과 동일(_chrome_argv) — 갈리면 세션/핑거프린트가 갈린다.
    """
    port = int(cfg.get("cdp_port", 9223))
    profile = os.path.expanduser(cfg.get("cdp_profile", "~/.ohisell_supplier_chrome"))
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
    log.info(
        "Chrome 실행됨 (PID %d, CDP port %d, 프로필 %s)\n"
        "  1. 브라우저에서 supplier.coupang.com 로그인\n"
        "  2. 완료 후: rocket_supplier_fetcher.py login",
        proc.pid, port, profile,
    )
    return 0


def cmd_poll(cfg: dict, interval: int = 30) -> int:
    """상주 poll 데몬 — UI '갱신' 버튼 요청만 감지·실행(순수 on-demand).

    30초마다 /rocket/refresh-status 폴링(가벼운 GET, 창 안 뜸) → 요청 있으면 claim → run.
    ★자동(일별 23h) 실행은 제거됨 — 창을 스스로 띄우지 않고 버튼 누를 때만 뜬다.
    ★Chrome은 이 데몬이 run 때만 띄웠다 닫는다(supervisor 폐기, 2026-07-27) — 창이 뜨는
    유일한 순간 = 버튼 클릭 직후 1회.
    낡음/실패는 prod GET /collection-status → 전역 신선도 배너로 가시화(잊어버림 방지).
    launchd KeepAlive 데몬으로 실행. plist: com.ohisell.rocket.plist.
    """
    import time as _time

    if not _push_configured(cfg):
        log.error("poll엔 prod_base_url·ingest_token·vendor_id 설정 필요.")
        return 2

    # 자가복구: 연속 폴링 실패가 쌓이면 종료 → launchd가 fresh 재기동(광고/Wing 페처 패턴).
    # sleep/wake 후 소켓 고착 자동 해소. 30s 간격 × 10 ≈ 5분.
    _MAX_CONSECUTIVE_FAILS = 10
    fails = 0

    log.info("[poll] 시작 — 30초마다 갱신 요청(버튼)만 체크·실행")
    while True:
        try:
            st = _prod_rocket_refresh_status(cfg)
            fails = 0
            needs_run = False

            # UI 버튼 요청 (유일한 트리거)
            lease = None
            if st.get("requested"):
                _claim = _prod_rocket_claim(cfg)
                if _claim.get("claimed", False):
                    lease = _claim.get("lease")   # 내 임대 식별자(실패 보고에 첨부)
                    log.info("[poll] UI 갱신 요청 임대 → 즉시 실행")
                    needs_run = True

            if needs_run:
                rc = cmd_run(cfg)
                log.info("[poll] run 완료 rc=%d", rc)
                if rc != 0:
                    # 실패 보고 = 재시도 판정의 입력(lease 계약). rc==3(로그인 필요)이면
                    # prod가 재시도 없이 요청을 소멸시킨다(§0 — 창만 반복해서 뜬다).
                    _prod_report_failure(
                        cfg, f"로켓 발주/정산 수집 실패(rc={rc})",
                        kind=("login_required" if rc == RC_LOGIN_REQUIRED else None),
                        lease=lease)

        except Exception as e:  # noqa: BLE001
            fails += 1
            log.warning("[poll] 폴링 오류(계속) %d/%d: %s", fails, _MAX_CONSECUTIVE_FAILS, e)
            if fails >= _MAX_CONSECUTIVE_FAILS:
                log.error("[poll] 연속 %d회 실패 — 프로세스 종료(launchd가 fresh로 재기동).", fails)
                return 1

        _time.sleep(interval)


def main() -> None:
    _install_signal_cleanup()   # SIGTERM(launchd bootout) 시 내가 띄운 Chrome 회수
    cfg = load_config()
    arg = sys.argv[1] if len(sys.argv) >= 2 else "run"
    if arg == "chrome":
        sys.exit(cmd_chrome(cfg))
    if arg == "login":
        sys.exit(cmd_login(cfg))
    if arg in ("run", ""):
        sys.exit(cmd_run(cfg))
    if arg == "poll":
        sys.exit(cmd_poll(cfg))
    print("usage: rocket_supplier_fetcher.py [chrome|login|run|poll]", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
