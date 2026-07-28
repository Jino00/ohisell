# test_safe_deploy_migrate.py — safe_deploy.sh의 alembic 마이그레이션 가드 회귀 테스트
# 실체는 bash 하니스(scripts/tests/safe_deploy_migrate_test.sh)다: ssh/scp/alembic를 shim으로
# 갈아끼워 prod를 안 건드리고 "마이그레이션 선행" 순서와 거부 조건을 검증한다.
# 여기서는 pytest 게이트에 물리기만 한다(순서가 뒤집히면 테스트 스위트가 빨간불).
#
# ★하니스는 git 추적 파일이다 — 없어지면 skip이 아니라 fail이어야 한다(사라졌는데 green이면
#   의미 없다). backend/tests의 다른 셸-하니스 래퍼와 동일한 원칙.
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = (
    Path(__file__).resolve().parents[2] / "scripts" / "tests" / "safe_deploy_migrate_test.sh"
)


@pytest.mark.skipif(shutil.which("git") is None, reason="git 필요")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash 필요")
def test_safe_deploy_migration_ordering():
    assert HARNESS.exists(), (
        f"하니스 파일이 사라졌습니다: {HARNESS} (git 추적 파일 — skip이 아니라 fail)"
    )
    r = subprocess.run(
        ["bash", str(HARNESS)], capture_output=True, text=True, timeout=300
    )
    assert r.returncode == 0, f"safe_deploy 마이그 하니스 실패:\n{r.stdout}\n{r.stderr}"
    assert "FAIL=0" in r.stdout, r.stdout
