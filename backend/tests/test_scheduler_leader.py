# test_scheduler_leader.py — 스케줄러 단일 인스턴스 보장 가드.
#
# ★왜 서브프로세스로 경합을 만드는가: 무중단 배포의 겹침 구간은 **별개 프로세스 둘**이다.
# 같은 프로세스 안에서 흉내내면 flock 의미가 달라져(파일 기술자 단위) 실제 배포와 다른 것을
# 검증하게 된다. 여기서는 진짜 자식 프로세스가 락을 쥔 상태를 만들어, ①standby로 떨어지는지
# ②그 프로세스가 죽으면 자동 승격하는지를 본다 — 이 둘이 배포 성공의 전제다.
#
# ★★기동 실패 계열(2026-08-05 적대 리뷰 P1-1): 초판은 락을 잡는 즉시 리더 플래그를 세우고
# 그 뒤에 스케줄러를 켰다. 기동이 실패하면 **락은 쥔 채 크론은 죽었는데 /health는 리더**라고
# 답했고, 배포 스크립트는 그것을 성공 근거로 써서 "완료"를 출력했다(green-while-stale).
# 아래 테스트들이 그 조합을 금지한다.
from __future__ import annotations

import importlib
import subprocess
import sys
import textwrap
import time

import pytest


def _fresh_module(lock_path, monkeypatch, poll="0.2", start_retry="0.2"):
    """락 경로/간격을 바꿔 모듈을 다시 읽는다(경로는 import 시점에 고정되므로)."""
    monkeypatch.setenv("OHISELL_SCHEDULER_LOCK", str(lock_path))
    monkeypatch.setenv("OHISELL_SCHEDULER_LOCK_POLL", poll)
    monkeypatch.setenv("OHISELL_SCHEDULER_START_RETRY", start_retry)
    import app.services.scheduler_leader as mod

    return importlib.reload(mod)


def _holder_process(lock_path):
    """락을 쥐고 stdin이 닫힐 때까지 사는 자식 프로세스(= 구 배포 프로세스 역할)."""
    code = textwrap.dedent(
        f"""
        import fcntl, os, sys
        fd = os.open({str(lock_path)!r}, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        sys.stdout.write("locked\\n"); sys.stdout.flush()
        sys.stdin.read()
        """
    )
    p = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    assert p.stdout.readline().strip() == "locked"  # 락을 실제로 쥔 뒤에 진행
    return p


def _lock_is_free(lock_path) -> bool:
    """지금 이 락을 **다른 프로세스가** 잡을 수 있는가 = 진짜로 반납됐는가."""
    code = textwrap.dedent(
        f"""
        import fcntl, os, sys
        fd = os.open({str(lock_path)!r}, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            sys.exit(1)
        sys.exit(0)
        """
    )
    return subprocess.run([sys.executable, "-c", code], timeout=10).returncode == 0


def test_단독이면_즉시_리더가_되어_스케줄러를_켠다(tmp_path, monkeypatch):
    mod = _fresh_module(tmp_path / "s.lock", monkeypatch)
    calls = []
    try:
        assert mod.start_when_leader(lambda: calls.append("start")) is True
        assert calls == ["start"]
        assert mod.is_leader() is True
    finally:
        mod.release()


def test_다른_프로세스가_리더면_standby로_남고_스케줄러를_켜지_않는다(tmp_path, monkeypatch):
    lock = tmp_path / "s.lock"
    holder = _holder_process(lock)
    mod = _fresh_module(lock, monkeypatch)
    calls = []
    try:
        assert mod.start_when_leader(lambda: calls.append("start")) is False
        assert calls == []              # ★핵심: 겹침 구간에 크론이 두 번 돌지 않는다
        assert mod.is_leader() is False
    finally:
        mod.release()
        holder.stdin.close()
        holder.wait(timeout=10)


def test_구_프로세스가_죽으면_자동_승격해_스케줄러를_켠다(tmp_path, monkeypatch):
    lock = tmp_path / "s.lock"
    holder = _holder_process(lock)
    mod = _fresh_module(lock, monkeypatch, poll="0.1")
    calls = []
    try:
        assert mod.start_when_leader(lambda: calls.append("start")) is False

        holder.stdin.close()            # 구 프로세스 종료 → 커널이 락 해제
        holder.wait(timeout=10)

        deadline = time.time() + 10
        while time.time() < deadline and not calls:
            time.sleep(0.05)

        assert calls == ["start"], "구 프로세스 종료 후에도 승격하지 않았다"
        assert mod.is_leader() is True
    finally:
        mod.release()


# ── 기동 실패 계열 (P1-1) ────────────────────────────────────────────
def test_스케줄러_기동이_실패하면_리더라고_답하지_않는다(tmp_path, monkeypatch):
    """★배포 스크립트의 유일한 성공 판정 근거다. 여기서 True가 나오면 크론이 죽어도 배포가
    '완료'를 출력한다(green-while-stale)."""
    mod = _fresh_module(tmp_path / "s.lock", monkeypatch, start_retry="60")

    def boom():
        raise RuntimeError("database is locked")

    try:
        assert mod.start_when_leader(boom) is False
        assert mod.is_leader() is False
        assert mod.holds_lock() is False   # 자리를 계속 쥐고 있으면 후임도 못 이어받는다
    finally:
        mod.release()


def test_기동_실패시_락을_반납해_다른_프로세스가_이어받을_수_있다(tmp_path, monkeypatch):
    lock = tmp_path / "s.lock"
    mod = _fresh_module(lock, monkeypatch, start_retry="60")
    try:
        mod.start_when_leader(lambda: (_ for _ in ()).throw(RuntimeError("기동 실패")))
        assert _lock_is_free(lock), "기동에 실패했는데 락을 붙들고 있다"
    finally:
        mod.release()


def test_기동이_일시적으로_실패해도_재시도로_결국_켜진다(tmp_path, monkeypatch):
    """일시적 DB 경합이 '영구 스케줄러 정지'가 되면 안 된다."""
    mod = _fresh_module(tmp_path / "s.lock", monkeypatch, poll="0.05", start_retry="0.05")
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("database is locked")

    try:
        assert mod.start_when_leader(flaky) is False   # 첫 시도 실패 → standby로 떨어진다

        deadline = time.time() + 10
        while time.time() < deadline and not mod.is_leader():
            time.sleep(0.05)

        assert mod.is_leader() is True, f"재시도로 승격하지 못했다(시도 {len(attempts)}회)"
        assert len(attempts) >= 3
    finally:
        mod.release()


# ── 승격 경로의 중복 호출 금지 ───────────────────────────────────────
def test_승격_경로에서도_스케줄러를_두_번_켜지_않는다(tmp_path, monkeypatch):
    """★초판 테스트는 홀더 없이 돌아 watcher 스레드를 아예 만들지 않았다 — 즉 중복 위험이
    있는 경로를 전혀 타지 않고 항상 통과했다(적대 리뷰 P2). 실제 승격 경로로 검증한다."""
    lock = tmp_path / "s.lock"
    holder = _holder_process(lock)
    mod = _fresh_module(lock, monkeypatch, poll="0.05")
    calls = []
    try:
        assert mod.start_when_leader(lambda: calls.append("start")) is False
        holder.stdin.close()
        holder.wait(timeout=10)

        deadline = time.time() + 10
        while time.time() < deadline and not calls:
            time.sleep(0.05)
        assert calls == ["start"]

        time.sleep(0.5)                 # 승격 후에도 감시 스레드가 계속 돌지 않는지
        assert calls == ["start"], "스케줄러를 두 번 켰다 — APScheduler에 잡이 중복 등록된다"
    finally:
        mod.release()


def test_release_후에는_다른_프로세스가_리더가_될_수_있다(tmp_path, monkeypatch):
    """★초판은 자식이 락을 못 잡으면 실패가 아니라 readline()에서 무한 대기했다(적대 리뷰 P2).
    잡을 수 있는지를 직접 판정한다."""
    lock = tmp_path / "s.lock"
    mod = _fresh_module(lock, monkeypatch)
    mod.start_when_leader(lambda: None)
    assert mod.is_leader() is True

    mod.release()
    assert mod.is_leader() is False
    assert _lock_is_free(lock), "release() 후에도 락이 풀리지 않았다"


def test_종료_중에는_감시_스레드가_락을_다시_잡지_않는다(tmp_path, monkeypatch):
    """release() 뒤 승격이 일어나면 이미 stop_scheduler를 지난 프로세스가 크론을 켠다."""
    lock = tmp_path / "s.lock"
    holder = _holder_process(lock)
    mod = _fresh_module(lock, monkeypatch, poll="0.05")
    calls = []
    try:
        mod.start_when_leader(lambda: calls.append("start"))
        mod.release()                   # 종료 개시
        holder.stdin.close()            # 그 직후 구 프로세스가 죽어 락이 비었다
        holder.wait(timeout=10)

        time.sleep(0.6)
        assert calls == [], "종료 중인데 승격해 스케줄러를 켰다"
        assert mod.is_leader() is False
    finally:
        mod.release()
