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
# ★★"락 보유"와 "스케줄러 가동"은 다른 사실이다(2026-08-05 적대 리뷰 P1-1).
# 초판은 락을 잡는 순간 리더 플래그를 세우고 **그 뒤에** 스케줄러를 켰다. 기동이 실패하면
# (예: 구 프로세스가 SQLite 쓰기 락을 오래 쥐어 OperationalError) lifespan이 예외를 흡수해
# **락은 쥔 채·크론은 죽은 채·/health는 "리더"라고 답하는** 상태가 된다. 배포 스크립트는
# 바로 그 /health를 성공 판정에 쓰므로 "✅ 무중단 재시작 완료"를 출력하며 크론이 전멸한다.
# 이 저장소가 반복해서 당한 green-while-stale이다. 그래서:
#   · is_leader() = 락 보유 **그리고** 스케줄러 기동 성공. 둘 중 하나라도 아니면 False.
#   · 기동 실패 시 **락을 반납한다** — 자리를 계속 쥐고 있으면 후임도 이어받지 못한다.
#   · 실패해도 포기하지 않고 백오프 후 재시도한다(일시적 DB 경합이 영구 정지가 되면 안 된다).
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
_POLL_SECONDS = float(os.getenv("OHISELL_SCHEDULER_LOCK_POLL", "2.0"))
# 스케줄러 기동 실패 후 재시도 간격 — 폴 간격보다 길게. 2초마다 재시도하면 DB가 계속 바쁠 때
# 로그가 폭주하고 경합을 키운다.
_START_RETRY_SECONDS = float(os.getenv("OHISELL_SCHEDULER_START_RETRY", "30.0"))

_mu = threading.Lock()           # _lock_fd 조작 직렬화(감시 스레드 ↔ 종료 경로 경합 방지)
_lock_fd: int | None = None      # ★열려 있는 동안만 락이 유효하다 — close = 락 해제.
_holds_lock = False
_scheduler_started = False
_stop_event = threading.Event()
_watcher: threading.Thread | None = None


def _try_acquire() -> bool:
    """논블로킹 배타 락 시도. 성공하면 True.

    ★락 보유일 뿐 '리더'가 아니다 — 스케줄러 기동까지 성공해야 리더다(모듈 상단 주석).
    """
    global _lock_fd, _holds_lock
    with _mu:
        if _stop_event.is_set():
            return False
        if _lock_fd is None:
            try:
                _lock_fd = os.open(str(_LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o644)
            except OSError as e:
                # 초판은 이 open이 try 밖에 있어 감시 스레드가 조용히 죽었다(적대 리뷰 P2).
                log.error("스케줄러 락 파일을 열지 못했습니다(%s): %s", _LOCK_PATH, e)
                return False
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False  # 다른 프로세스가 쥐고 있다 — 정상 경로(standby)
        # 누가 잡고 있는지 사람이 볼 수 있게 pid를 남긴다(진단용, 락 자체와는 무관).
        try:
            os.ftruncate(_lock_fd, 0)
            os.write(_lock_fd, f"{os.getpid()}\n".encode())
            os.fsync(_lock_fd)
        except OSError:
            pass
        _holds_lock = True
        return True


def _release_lock() -> None:
    """락만 반납한다(감시 스레드는 계속 살아 재시도할 수 있다)."""
    global _lock_fd, _holds_lock
    with _mu:
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
        _holds_lock = False


def _start_guarded(start_fn: Callable[[], None]) -> bool:
    """락을 쥔 상태에서 스케줄러를 켠다. 실패하면 **락을 반납하고** False.

    ★자리를 계속 쥐고 있으면 안 되는 이유: 이 프로세스는 크론을 못 돌리는데 다른 프로세스도
    이어받지 못한다 — 아무도 스케줄러를 돌리지 않는 상태가 고착된다.
    """
    global _scheduler_started
    try:
        start_fn()
    except Exception as e:
        log.error("스케줄러 기동 실패 — 락을 반납하고 %.0fs 후 재시도합니다: %s",
                  _START_RETRY_SECONDS, e)
        _release_lock()
        return False
    _scheduler_started = True
    return True


def is_leader() -> bool:
    """이 프로세스가 **스케줄러를 실제로 돌리고 있는** 리더인가.

    ★배포 스크립트의 승격 판정 근거이자 /health의 표면이다. 락만 쥐고 기동에 실패한 상태를
    True로 답하면 배포가 "성공"하며 크론이 전멸한다(적대 리뷰 P1-1).
    """
    return _holds_lock and _scheduler_started


def holds_lock() -> bool:
    """락 보유 여부만(진단용) — is_leader와 갈리면 기동 실패 상태다."""
    return _holds_lock


def lock_path() -> str:
    return str(_LOCK_PATH)


def start_when_leader(start_fn: Callable[[], None]) -> bool:
    """리더가 되면 start_fn()을 호출한다. 성공할 때까지 백그라운드에서 재시도한다.

    반환: True=즉시 리더가 되어 스케줄러 기동까지 성공 / False=standby(또는 기동 재시도 대기).

    ★초판과 달리 start_fn의 예외를 호출자에게 올리지 않는다: 올려봐야 lifespan이 로깅만
    하고 넘어가 **락을 쥔 채 크론이 죽은** 상태로 굳었다. 여기서 락을 반납하고 재시도하는
    것이 유일하게 자가 복구되는 경로다. 실패 사실은 is_leader()=False로 표면화된다.
    """
    global _watcher

    if _try_acquire() and _start_guarded(start_fn):
        log.info("스케줄러 리더 획득(pid=%s, lock=%s)", os.getpid(), _LOCK_PATH)
        return True

    log.info(
        "스케줄러 미기동 — standby로 대기합니다(폴 %.1fs, 기동 실패 시 재시도 %.0fs, lock=%s)",
        _POLL_SECONDS, _START_RETRY_SECONDS, _LOCK_PATH,
    )

    def _watch() -> None:
        while not _stop_event.is_set():
            if _stop_event.wait(_POLL_SECONDS):
                return
            if not _try_acquire():
                continue  # 아직 다른 프로세스가 리더
            if _start_guarded(start_fn):
                log.info("스케줄러 리더 승격 완료(pid=%s)", os.getpid())
                return
            # 기동 실패 — 락은 이미 반납됐다. 경합이 가라앉을 시간을 준 뒤 다시 시도한다.
            if _stop_event.wait(_START_RETRY_SECONDS):
                return

    if _watcher is None or not _watcher.is_alive():
        _watcher = threading.Thread(target=_watch, name="scheduler-leader", daemon=True)
        _watcher.start()
    return False


def release() -> None:
    """종료 시 감시 스레드 중단 + 락 해제.

    ★프로세스가 죽으면 커널이 어차피 풀어주므로 이 함수는 '빠른 인계'용이다 —
    graceful shutdown에서 명시적으로 풀어주면 후임이 폴 한 번 만에 승격한다.
    ★_stop_event를 먼저 세운다: 감시 스레드가 종료 중인 프로세스에서 락을 다시 잡고
    스케줄러를 켜는 경합을 막는다(적대 리뷰 P2 — _try_acquire가 이를 확인한다).
    """
    global _scheduler_started
    _stop_event.set()
    _release_lock()
    _scheduler_started = False
