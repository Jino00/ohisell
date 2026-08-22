#!/usr/bin/env python3
# scheduler_watchdog_poll.py — prod의 GET /api/scheduler/health를 주기적으로 폴해서
#   비정상(스케줄러 정지/잡 미등록/failed/stale/never_succeeded)이면 Mac 네이티브 알림을 띄운다.
#
# 왜 별도 데몬인가(원칙8/18 단일책임): ad_cost 페처는 Playwright(브라우저·쿠키)로 무겁고
#   책임이 다르다. 워치독 폴은 가벼운 JSON GET + 알림뿐 → 의존(requests)·수명·주기가 전혀 달라
#   독립 파일·독립 launchd 잡으로 분리한다. (_notify_mac은 12줄이라 의도적으로 복제 — 무거운
#   ad_cost 모듈을 import하면 playwright까지 끌려와 폴러가 무거워지므로.)
#
# 한계(원칙22 명시): Mac이 꺼지거나 잠들면 알림 공백(bounded gap). 서버측 푸시 채널은 PLAN T6(TODO).
#
# 사용:  python3 scheduler_watchdog_poll.py poll     # 상주 데몬(launchd KeepAlive)
#        python3 scheduler_watchdog_poll.py once     # 1회 폴(수동 점검·테스트)
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

# prod_base_url은 워치독 전용 설정(있으면)→ad 페처 설정 순으로 읽는다(새 설정 강요 안 함).
WATCHDOG_CONFIG = Path(os.path.expanduser("~/.ohisell_watchdog.json"))
AD_CONFIG = Path(os.path.expanduser("~/.ohisell_ad_fetcher.json"))
STATE_PATH = Path(os.path.expanduser("~/.ohisell_watchdog_state.json"))
LOG_PATH = Path(os.path.expanduser("~/.ohisell_watchdog.log"))
DEFAULT_PROD_URL = "https://sellc.ohitech.co.kr"

POLL_INTERVAL_S = 1800        # 30분 — 감시 잡은 일/2시간 주기라 빠른 폴이 불필요(전력·노이즈 절약).
DEBOUNCE_S = 21600            # 같은 문제 재알림 억제 창(6h) — stale 잡이 며칠 가도 6h마다 1번만.
STARTUP_DEBOUNCE_S = 3600     # 기동 알림도 디바운스(launchd 재기동 스팸 방지, 1h).
HTTP_TIMEOUT_S = 15
# prod 연속 unreachable 임계 — 이만큼 연속 실패하면 'prod 도달 불가'를 1회 알림(워치독이
# 잡 상태를 못 보는 것 자체가 신호). 데몬은 죽지 않고 계속 재시도.
MAX_CONSECUTIVE_NET_FAILS = 6  # 30분 간격 × 6 = 3시간

log = logging.getLogger("watchdog_poll")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)


def _notify_mac(title: str, message: str, sound: str = "Glass") -> None:
    """macOS 네이티브 알림(+소리). osascript 로컬 전용·자격증명 불필요. best-effort(실패 무시).

    (ad_cost_browser_fetcher._notify_mac의 의도적 복제 — 무거운 모듈 import 회피, 위 헤더 참조.)
    """
    try:
        # AppleScript 인젝션/깨짐 방지: 백슬래시 제거 + 큰따옴표→작은따옴표 + 개행 공백화.
        # (입력은 내부 job_name+한글 라벨이라 사실상 안전하나 견고하게 — codex S5 [P2].)
        def _safe(s: str) -> str:
            return s.replace("\\", "").replace('"', "'").replace("\n", " ").replace("\r", " ")
        safe_t = _safe(title)
        safe_m = _safe(message)
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_m}" with title "{safe_t}" sound name "{sound}"'],
            timeout=10, check=False,
        )
    except Exception as e:  # noqa: BLE001 — 알림 실패가 데몬을 멈추면 안 됨
        log.warning("macOS 알림 실패(무시): %s", str(e)[:80])


def _basic_auth(cfg: dict):
    """prod Basic Auth 자격증명 — 없으면 None(인증 켜기 전까지 기존 동작 유지).

    ★설정에 키가 없으면 None을 돌려준다. 그래야 이 커밋을 먼저 배포해 두고
      나중에 nginx를 켜는 «순서»가 성립한다(둘을 동시에 바꾸면 되돌릴 곳이 두 곳이 된다).
    """
    u = cfg.get("basic_auth_user")
    p = cfg.get("basic_auth_pass")
    return (u, p) if u and p else None


def _load_cfg() -> dict:
    """폴 파라미터·prod_base_url 로드. 워치독 설정의 폴/디바운스 오버라이드는 prod_base_url
    유무와 무관하게 채택하고(codex S5 [P2]), prod_base_url은 워치독→ad 페처→기본값 순 폴백.
    basic_auth_user/basic_auth_pass도 같은 폴백 순서(워치독 전용→ad 페처)로 읽는다."""
    cfg: dict = {}
    # 1) 워치독 전용 설정: 오버라이드 키는 prod_base_url 없어도 채택. prod_base_url 있으면 함께.
    if WATCHDOG_CONFIG.is_file():
        try:
            data = json.loads(WATCHDOG_CONFIG.read_text(encoding="utf-8"))
            for k in ("poll_interval_s", "debounce_s", "max_consecutive_net_fails",
                      "basic_auth_user", "basic_auth_pass"):
                if k in data:
                    cfg[k] = data[k]
            if data.get("prod_base_url"):
                cfg["prod_base_url"] = data["prod_base_url"]
        except Exception as e:  # noqa: BLE001
            log.warning("워치독 설정 읽기 실패(%s): %s", WATCHDOG_CONFIG, str(e)[:80])
    # 2) prod_base_url·basic_auth_* 미정 시 ad 페처 설정에서 폴백(이미 Mac에 존재).
    if AD_CONFIG.is_file() and ("prod_base_url" not in cfg or "basic_auth_user" not in cfg):
        try:
            data = json.loads(AD_CONFIG.read_text(encoding="utf-8"))
            if "prod_base_url" not in cfg and data.get("prod_base_url"):
                cfg["prod_base_url"] = data["prod_base_url"]
            if "basic_auth_user" not in cfg and data.get("basic_auth_user"):
                cfg["basic_auth_user"] = data["basic_auth_user"]
            if "basic_auth_pass" not in cfg and data.get("basic_auth_pass"):
                cfg["basic_auth_pass"] = data["basic_auth_pass"]
        except Exception as e:  # noqa: BLE001
            log.warning("ad 설정 읽기 실패(%s): %s", AD_CONFIG, str(e)[:80])
    cfg.setdefault("prod_base_url", DEFAULT_PROD_URL)
    return cfg


def _load_state() -> dict:
    if STATE_PATH.is_file():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 상태 손상 시 빈 상태로 재시작(알림 한 번 더 뜰 뿐)
            return {}
    return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.warning("상태 저장 실패(무시): %s", str(e)[:80])


def _problem_keys(health: dict) -> list[str]:
    """health 응답에서 '문제' 키 목록을 만든다(디바운스·집계 단위). healthy면 빈 목록."""
    keys: list[str] = []
    if not health.get("scheduler_running", True):
        keys.append("scheduler_down")
    for j in health.get("missing_jobs", []) or []:
        keys.append(f"missing:{j}")
    for bucket in ("failed", "stale", "never_succeeded"):
        for v in health.get(bucket, []) or []:
            keys.append(f"{bucket}:{v.get('job_name', '?')}")
    # 쿠키 만료(fail-soft 잡이 조용히 멈추는 사고) — account_key 단위.
    for c in health.get("cookies_stale", []) or []:
        keys.append(f"cookie:{c.get('account_key', '?')}")
    # 데이터 나이(잡·쿠키 보고가 거짓말해도 최신 데이터 나이는 거짓말 못 함) — name:account_key 단위.
    for v in health.get("data_stale", []) or []:
        keys.append(f"data:{v.get('name', '?')}:{v.get('account_key', '')}")
    return keys


def _summarize(keys: list[str]) -> str:
    """문제 키들을 사람이 읽을 한 줄 요약으로(집계 단일 알림)."""
    groups: dict[str, list[str]] = {}
    for k in keys:
        kind, _, name = k.partition(":")
        groups.setdefault(kind, []).append(name or "")
    label = {
        "scheduler_down": "스케줄러 정지",
        "missing": "미등록",
        "failed": "실패",
        "stale": "지연(stale)",
        "never_succeeded": "성공기록없음",
        "cookie": "쿠키만료",
        "data": "데이터끊김",
    }
    parts = []
    for kind, names in groups.items():
        if kind == "scheduler_down":
            parts.append(label[kind])
        else:
            names = [n for n in names if n]
            parts.append(f"{label.get(kind, kind)}: {', '.join(names)}")
    return " / ".join(parts)


def _maybe_notify(keys: list[str], state: dict, now: float, *, title: str) -> None:
    """디바운스 창을 지난 '새' 문제만 모아 단일 알림. 해소된 문제는 상태에서 제거(재발 시 재알림)."""
    debounce = state.get("_debounce_s", DEBOUNCE_S)
    seen = state.setdefault("problems", {})
    # 처음 보는 문제는 무조건 알림(now 크기에 의존하지 않음), 이미 본 문제는 디바운스 창 경과 시만.
    fresh = [k for k in keys if k not in seen or (now - seen[k]) >= debounce]
    # 현재 존재하는 문제만 타임스탬프 유지/갱신, 사라진 문제는 prune(재발 시 즉시 재알림).
    current = set(keys)
    for k in list(seen.keys()):
        if k not in current:
            del seen[k]
    if fresh:
        msg = _summarize(fresh)
        log.warning("워치독 알림: %s", msg)
        _notify_mac(title, msg, sound="Basso")
        for k in fresh:
            seen[k] = now
    else:
        # 새 문제는 없지만 기존 문제 지속 — 타임스탬프 유지(없는 키만 now로 초기화).
        for k in keys:
            seen.setdefault(k, now)


def _poll_once(cfg: dict, state: dict) -> bool:
    """1회 폴. 성공 시 True(네트워크 OK), 실패 시 False. 알림은 내부에서 처리."""
    url = cfg["prod_base_url"].rstrip("/") + "/api/scheduler/health"
    now = time.time()
    r = requests.get(url, auth=_basic_auth(cfg), timeout=HTTP_TIMEOUT_S)
    r.raise_for_status()
    health = r.json()
    keys = _problem_keys(health)
    if health.get("healthy"):
        # 정상 — 직전에 떠 있던 문제 상태를 비워 다음 발생 시 즉시 알리게 한다.
        if state.get("problems"):
            log.info("스케줄러 정상 회복 — 문제 상태 초기화")
        state["problems"] = {}
    else:
        _maybe_notify(keys, state, now, title="⚠️ 스케줄러 워치독")
    return True


def cmd_poll(cfg: dict) -> int:
    interval = int(cfg.get("poll_interval_s", POLL_INTERVAL_S))
    max_fails = int(cfg.get("max_consecutive_net_fails", MAX_CONSECUTIVE_NET_FAILS))
    state = _load_state()
    state["_debounce_s"] = int(cfg.get("debounce_s", DEBOUNCE_S))
    log.info("워치독 폴 데몬 시작 — %ds 간격, prod=%s", interval, cfg["prod_base_url"])

    # 기동 알림(디바운스 — launchd 재기동 스팸 방지). 사람이 데몬 생존을 인지하게.
    now = time.time()
    if now - state.get("_startup_notified_at", 0) >= STARTUP_DEBOUNCE_S:
        _notify_mac("스케줄러 워치독 시작", "백엔드 잡 상태 감시를 시작합니다.", sound="Glass")
        state["_startup_notified_at"] = now
    _save_state(state)

    net_fails = 0
    while True:
        try:
            _poll_once(cfg, state)
            net_fails = 0
            state.pop("_net_unreachable_notified", None)
        except requests.RequestException as e:
            net_fails += 1
            log.warning("health 폴 실패(네트워크) %d/%d: %s", net_fails, max_fails, str(e)[:100])
            if net_fails >= max_fails and not state.get("_net_unreachable_notified"):
                _notify_mac("⚠️ 스케줄러 워치독", f"prod 도달 불가 {net_fails}회 연속 — 백엔드/네트워크 확인", sound="Basso")
                state["_net_unreachable_notified"] = True
        except Exception as e:  # noqa: BLE001 — 데몬은 어떤 오류에도 죽지 않는다
            log.error("폴 루프 오류: %s", str(e)[:160])
        _save_state(state)
        time.sleep(interval)


def cmd_once(cfg: dict) -> int:
    """1회 폴(테스트·수동 점검). 알림 디바운스는 상태파일을 따른다."""
    state = _load_state()
    state["_debounce_s"] = int(cfg.get("debounce_s", DEBOUNCE_S))
    try:
        _poll_once(cfg, state)
        _save_state(state)
        return 0
    except requests.RequestException as e:
        log.error("health 폴 실패: %s", str(e)[:120])
        return 1


def main() -> None:
    cfg = _load_cfg()
    arg = sys.argv[1] if len(sys.argv) >= 2 else "poll"
    if arg == "once":
        sys.exit(cmd_once(cfg))
    try:
        sys.exit(cmd_poll(cfg))
    except KeyboardInterrupt:
        log.info("워치독 폴 데몬 종료")
        sys.exit(0)


if __name__ == "__main__":
    main()
