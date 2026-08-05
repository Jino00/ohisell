# scheduler_leader.py — 스케줄러 단일 인스턴스 보장(파일 락 기반 리더 선출)
#
# ★존재 이유(2026-08-05, 무중단 배포와 한 몸): 이 앱은 인프로세스 APScheduler를 돌린다.
# 블루-그린 배포는 신·구 프로세스가 수십 초 겹치는데, 두 프로세스가 각자 스케줄러를 켜면
# 그 창에 발화한 크론이 **두 번** 돈다(주문 동기화·광고 적재·자동입찰 전부). 겹침은
# 무중단의 대가이므로 없앨 수 없고, 대신 "스케줄러는 한 프로세스만"을 구조로 강제한다.
#
# ★왜 파일 락(flock)인가: ①프로세스가 죽으면 커널이 락을 자동 해제한다 — 크래시·SIGKILL·
# 배포 종료 어느 경로든 리더 자리가 반드시 비워진다(타임아웃 기반 리스는 이 보장이 없어
# "죽었는데 아무도 안 이어받는" 구간이 생긴다). ②DB 스키마 변경이 필요 없다(금지선).
# ③같은 호스트 단일 서버 배포라 분산 합의가 필요 없다.
#
# 동작: 부팅 시 논블로킹으로 락을 시도해 성공하면 즉시 리더(스케줄러 기동), 실패하면
# standby로 남아 백그라운드 스레드가 주기적으로 재시도한다. 구 프로세스가 종료되면
# 커널이 락을 풀고, 다음 폴에서 신 프로세스가 승격해 스케줄러를 켠다.
from __future__ import annotations

import fcntl
import logging
import os
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# backend/ 디렉터리에 둔다(DB 파일과 같은 자리) — cwd에 의존하면 실행 방식에 따라 락 파일이
# 갈라져 "둘 다 리더"가 되므로 __file__ 기준 절대경로로 고정한다.
_DEFAULT_LOCK_PATH = Path(__file__).resolve().parents[2] / ".scheduler.lock"
_LOCK_PATH = Path(os.getenv("OHISELL_SCHEDULER_LOCK", str(_DEFAULT_LOCK_PATH)))

# 승격 폴 간격 — 구 프로세스 종료 후 이 시간 안에 신 프로세스가 스케줄러를 켠다.
# 짧을수록 배포 중 "스케줄러 없는 구간"이 줄지만, 그만큼 헛도는 폴이 늘어난다.
_POLL_SECONDS = float(os.getenv("OHISELL_SCHEDULER_LOCK_POLL", "2.0"))

_lock_fd: int | None = None      # ★절대 close 하지 말 것 — close는 곧 락 해제다.
_is_leader = False
_stop_event = threading.Event()
_watcher: threading.Thread | None = None


def _try_acquire() -> bool:
    """논블로킹 배타 락 시도. 성공하면 True(이 프로세스가 리더)."""
    global _lock_fd, _is_leader
    if _lock_fd is None:
        _lock_fd = os.open(str(_LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False  # 다른 프로세스가 리더 — 정상 경로(standby)
    # 누가 잡고 있는지 사람이 볼 수 있게 pid를 남긴다(운영 중 진단용, 락 자체와는 무관).
    try:
        os.ftruncate(_lock_fd, 0)
        os.write(_lock_fd, f"{os.getpid()}\n".encode())
        os.fsync(_lock_fd)
    except OSError:
        pass
    _is_leader = True
    return True


def is_leader() -> bool:
    """이 프로세스가 스케줄러 리더인가(운영 진단·상태 표면용)."""
    return _is_leader


def lock_path() -> str:
    return str(_LOCK_PATH)


def start_when_leader(start_fn: Callable[[], None]) -> bool:
    """리더가 되면 start_fn()을 **정확히 한 번** 호출한다.

    반환: True=즉시 리더가 되어 동기 호출됨 / False=standby로 남아 백그라운드 대기 중.

    ★start_fn의 예외는 삼키지 않고 올린다(동기 경로) — 부팅 시 스케줄러 실패는
    호출자(lifespan)가 이미 로깅·처리하는 1급 신호다. 백그라운드 승격 경로에서는
    올릴 곳이 없으므로 로깅만 하고 스레드를 끝낸다.
    """
    global _watcher

    if _try_acquire():
        start_fn()
        log.info("스케줄러 리더 획득(pid=%s, lock=%s)", os.getpid(), _LOCK_PATH)
        return True

    log.info(
        "스케줄러 standby — 다른 프로세스가 리더입니다. %.1fs 간격으로 승격 대기(lock=%s)",
        _POLL_SECONDS, _LOCK_PATH,
    )

    def _watch() -> None:
        while not _stop_event.is_set():
            if _stop_event.wait(_POLL_SECONDS):
                return
            if _try_acquire():
                try:
                    start_fn()
                    log.info("스케줄러 리더 승격 완료(pid=%s)", os.getpid())
                except Exception as e:  # 백그라운드라 올릴 곳이 없다 — 남기고 끝낸다.
                    log.error("스케줄러 리더 승격 후 시작 실패: %s", e)
                return

    _watcher = threading.Thread(target=_watch, name="scheduler-leader", daemon=True)
    _watcher.start()
    return False


def release() -> None:
    """종료 시 감시 스레드 중단 + 락 해제.

    ★프로세스가 죽으면 커널이 어차피 풀어주므로 이 함수는 '빠른 인계'용이다 —
    graceful shutdown에서 명시적으로 풀어주면 후임이 폴 한 번 만에 승격한다.
    """
    global _lock_fd, _is_leader
    _stop_event.set()
    if _lock_fd is not None:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(_lock_fd)
        except OSError:
            pass
        _lock_fd = None
    _is_leader = False
