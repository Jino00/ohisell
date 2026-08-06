# test_safe_deploy_frontend.py — safe_deploy.sh --frontend 스탬프 CAS 회귀 테스트
# 실체는 bash 하니스(scripts/tests/safe_deploy_frontend_test.sh)다: ssh/rsync를 shim으로
# 갈아끼워 prod를 안 건드리고 "다른 세션이 배포했으면 거부"를 검증한다.
#
# ★왜 게이트에 무는가(2026-08-06 사고): dist는 통짜 rsync라 파일 CAS가 안 걸렸고, 그 틈으로
#   병행 세션이 09:09·09:23 두 번 서로의 프론트 수정을 조용히 지웠다. 가드가 조용히 무력화되면
#   증상이 또 "내가 고친 게 사라졌다"로만 나타나므로, 여기서 빨간불이 나게 둔다.
#
# ★하니스는 git 추적 파일이다 — 없어지면 skip이 아니라 fail이어야 한다(사라졌는데 green이면
#   의미 없다). backend/tests의 다른 셸-하니스 래퍼와 동일한 원칙.
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = (
    Path(__file__).resolve().parents[2] / "scripts" / "tests" / "safe_deploy_frontend_test.sh"
)


@pytest.mark.skipif(shutil.which("git") is None, reason="git 필요")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash 필요")
@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync 필요(하니스가 shim으로 대체하지만 경로 확인용)")
def test_safe_deploy_frontend_stamp_cas():
    assert HARNESS.exists(), (
        f"하니스 파일이 사라졌습니다: {HARNESS} (git 추적 파일 — skip이 아니라 fail)"
    )
    r = subprocess.run(
        ["bash", str(HARNESS)], capture_output=True, text=True, timeout=300
    )
    assert r.returncode == 0, f"safe_deploy frontend 하니스 실패:\n{r.stdout}\n{r.stderr}"
    assert "FAIL=0" in r.stdout, r.stdout
