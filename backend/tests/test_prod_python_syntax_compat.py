# test_prod_python_syntax_compat.py — prod 파이썬(3.10) 문법 호환 가드.
#
# ★존재 이유(2026-07-27 실사고, CS 스프린트): 개발 환경은 Python 3.14, **prod는 3.10**이다.
#   PEP 701(3.12+)로 허용된 f-string 중첩(같은 따옴표·여러 줄)을 쓴 모듈이 로컬 테스트
#   3,497건을 전부 통과하고 **배포 후 prod에서 SyntaxError로 import가 통째로 죽었다.**
#   테스트가 새 문법을 쓰는 인터프리터에서만 돌면 이 클래스의 결함을 원리적으로 못 잡는다.
#
# 이 테스트는 prod보다 낮거나 같은 세대의 인터프리터(3.10/3.11 — 둘 다 PEP 701 이전)로
# app 패키지 전체를 **컴파일만** 해본다(실행 아님 — import 부작용 없음).
# 해당 인터프리터가 없으면 skip한다(CI/다른 개발기 배려) — 있으면 반드시 돈다.
from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

# prod = Python 3.10. 3.11도 PEP 701 이전이라 같은 문법 세대로 취급한다(실측 확인).
_CANDIDATES = ("python3.10", "python3.11")
_APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"


def _find_old_python() -> str | None:
    for name in _CANDIDATES:
        p = shutil.which(name) or f"/opt/homebrew/bin/{name}"
        if shutil.which(p) or pathlib.Path(p).exists():
            return p
    return None


def test_app_package_compiles_on_prod_python_generation():
    """app/ 전체가 prod 세대 파이썬에서 문법 오류 없이 컴파일되는가.

    실패하면 대개 3.12+ 전용 문법이다 — 가장 흔한 함정은 **f-string 안에 같은 따옴표
    f-string을 중첩**하거나 f-string 표현식을 여러 줄로 쓴 것(PEP 701).
    """
    py = _find_old_python()
    if py is None:
        pytest.skip(f"prod 세대 파이썬({'/'.join(_CANDIDATES)}) 없음 — 문법 호환 가드 skip")

    files = sorted(str(p) for p in _APP_DIR.rglob("*.py"))
    assert files, "app/*.py를 하나도 못 찾았다(경로 오류)"

    # py_compile은 첫 실패에서 멈추므로, 전량 검사해 실패 목록을 한 번에 보여준다.
    script = (
        "import sys, py_compile, tempfile\n"
        "bad = []\n"
        "with tempfile.TemporaryDirectory() as d:\n"
        "    for f in sys.argv[1:]:\n"
        "        try:\n"
        "            py_compile.compile(f, cfile=d + '/x.pyc', doraise=True)\n"
        "        except py_compile.PyCompileError as e:\n"
        "            bad.append(str(e))\n"
        "print('\\n'.join(bad))\n"
        "sys.exit(1 if bad else 0)\n"
    )
    r = subprocess.run([py, "-c", script, *files], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, (
        f"prod 세대 파이썬({py})에서 컴파일 실패 — 3.12+ 전용 문법을 썼을 가능성이 높다"
        f"(가장 흔한 원인: PEP 701 f-string 중첩):\n{r.stdout}\n{r.stderr}"
    )
