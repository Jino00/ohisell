# test_scheduler_leader.py — 스케줄러 단일 인스턴스 보장 가드.
#
# ★왜 서브프로세스로 경합을 만드는가: 무중단 배포의 겹침 구간은 **별개 프로세스 둘**이다.
# 같은 프로세스 안에서 흉내내면 flock 의미가 달라져(파일 기술자 단위) 실제 배포와 다른 것을
# 검증하게 된다. 여기서는 진짜 자식 프로세스가 락을 쥔 상태를 만들어, ①standby로 떨어지는지
# ②그 프로세스가 죽으면 자동 승격하는지를 본다 — 이 둘이 배포 성공의 전제다.
from __future__ import annotations

import importlib
import subprocess
import sys
import textwrap
import time

import pytest


def _fresh_module(lock_path, monkeypatch, poll="0.2"):
    """락 경로/폴 간격을 바꿔 모듈을 다시 읽는다(경로는 import 시점에 고정되므로)."""
    monkeypatch.setenv("OHISELL_SCHEDULER_LOCK", str(lock_path))
    monkeypatch.setenv("OHISELL_SCHEDULER_LOCK_POLL", poll)
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
            time.sleep(0.1)

        assert calls == ["start"], "구 프로세스 종료 후에도 승격하지 않았다"
        assert mod.is_leader() is True
    finally:
        mod.release()


def test_승격은_정확히_한_번만_일어난다(tmp_path, monkeypatch):
    """★start_fn이 두 번 불리면 APScheduler에 잡이 중복 등록된다 — 겹침 방지의 반대편 실패."""
    mod = _fresh_module(tmp_path / "s.lock", monkeypatch)
    calls = []
    try:
        mod.start_when_leader(lambda: calls.append("start"))
        time.sleep(0.5)
        assert calls == ["start"]
    finally:
        mod.release()


def test_release_후에는_다른_프로세스가_리더가_될_수_있다(tmp_path, monkeypatch):
    lock = tmp_path / "s.lock"
    mod = _fresh_module(lock, monkeypatch)
    mod.start_when_leader(lambda: None)
    assert mod.is_leader() is True
    mod.release()

    holder = _holder_process(lock)      # 락이 실제로 풀렸으면 자식이 즉시 잡는다
    try:
        assert holder.poll() is None
    finally:
        holder.stdin.close()
        holder.wait(timeout=10)
