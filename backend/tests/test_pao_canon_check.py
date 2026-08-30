# test_pao_canon_check.py — `scripts/check_pao_canon.py` 회귀 테스트
#
# 왜 있나(계약 `docs/contracts/CONTRACT_pao_ops_canon.md` §5 S3-a·S3-b, 2026-08-30):
#   PAO_OPS.md의 좌표(파일·심볼·API·테이블.컬럼·크론)가 아직 실재하는지 세는 검사기.
#   ★가장 위험한 자리는 MEASURED 마커 짝이 안 맞는데 조용히 통과하는 것 — 그러면
#   문서 뒷부분이 통째로 검사 대상에서 빠지는데 「실패 0건」과 「검사 안 됨」이 같은
#   숫자로 보인다(교훈 #123). 그래서 마커 불일치는 이 테스트의 1급 시민이다.
#
# ★값을 만드는 층(반환값)과 사람이 읽는 층(stdout 문자열)을 둘 다 지킨다 — 이 저장소가
#   반복해서 밟은 병이 「값은 맞는데 사람이 못 보는」 결함이었다. 그래서 여기 테스트는
#   run()의 exit code뿐 아니라 capsys로 잡은 출력 문자열 안에 좌표 이름·유형별 분포가
#   실제로 «보이는지»까지 확인한다.
from __future__ import annotations

import importlib.util
import pathlib

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check_pao_canon.py"
_REPO_ROOT = _SCRIPT.parent.parent
_REAL_DOC = _REPO_ROOT / "docs" / "PAO_OPS.md"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_pao_canon", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


# ═══ 1. 정상 경로 — 실제 문서 ═══════════════════════════════════════════════

def test_real_doc_has_zero_failures(mod, capsys):
    """현재 docs/PAO_OPS.md를 검사하면 마커 짝이 맞고 실패 0, 4카운트가 다 나온다."""
    code = mod.run(_REAL_DOC)
    out = capsys.readouterr().out
    assert code == 0
    assert "전체 " in out and "통과 " in out and "실패 0" in out and "미분류 " in out


def test_real_doc_marker_regions_are_balanced(mod):
    """실측 마커 열림/닫힘 짝이 맞는다 — MarkerError 없이 지나간다(별도 API로도 확인)."""
    text = _REAL_DOC.read_text(encoding="utf-8")
    regions = mod.find_excluded_regions(text)  # 마커 불일치면 여기서 MarkerError
    assert len(regions) >= 1


# ═══ 2. 변이: 죽은 좌표 검출(계약 §5 S3-b) ══════════════════════════════════

def test_dead_file_coordinate_is_named_in_failure_output(mod, tmp_path, capsys):
    """존재하지 않는 파일 좌표(오타 1글자)를 넣으면 그 좌표 «이름»이 실패 목록에 뜬다.

    「실패 개수가 1」만 보면 부족하다 — 사람이 읽는 자리(stdout)에 정확한 이름이
    있어야 «어디를 고칠지» 알 수 있다."""
    doc = tmp_path / "fixture.md"
    dead_coord = "docs/PAO_OPS_TYPOX.md"  # 실제로 존재하지 않는 파일명(오타)
    doc.write_text(f"# fixture\n\n죽은 좌표: `{dead_coord}`\n", encoding="utf-8")

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 1
    assert "실패 1" in out
    assert dead_coord in out


def test_live_file_coordinate_does_not_fail(mod, tmp_path, capsys):
    """대조군 — 오타 없는 실제 파일 좌표는 실패하지 않는다."""
    doc = tmp_path / "fixture.md"
    doc.write_text("# fixture\n\n살아있는 좌표: `docs/PAO_OPS.md`\n", encoding="utf-8")

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 0
    assert "실패 0" in out


# ═══ 3. 마커 불일치가 조용히 통과하지 않는다 ═══════════════════════════════

def test_unclosed_marker_errors_out(mod, tmp_path, capsys):
    """여는 마커만 있는 픽스처는 에러로 종료한다(exit code != 0) — 침묵 통과 금지."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n<!-- MEASURED -->\n죽은 좌표라도 안 잡혀야 정상: "
        "`docs/PAO_OPS_TYPOX.md`\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code != 0
    # ★이게 이 테스트의 핵심: 「검사 안 됨」이 「전체/통과/실패/미분류」 4카운트 성공
    # 출력 형태로 위장하면 안 된다(교훈 #123 — 발견 0건과 검사 안 됨이 같은 숫자로 보임).
    assert "전체 " not in out
    assert "마커" in out


def test_close_before_open_errors_out(mod, tmp_path, capsys):
    """닫는 마커가 여는 마커보다 먼저 나오는 경우도 같은 취급(에러 종료)."""
    doc = tmp_path / "fixture.md"
    doc.write_text("# fixture\n\n<!-- /MEASURED -->\n본문\n", encoding="utf-8")

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code != 0
    assert "마커" in out


def test_mismatched_marker_counts_error_out(mod, tmp_path, capsys):
    """여는 마커 2개·닫는 마커 1개처럼 수가 안 맞아도 에러 종료."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n<!-- MEASURED -->\nA\n<!-- /MEASURED -->\n\n"
        "<!-- MEASURED -->\nB (안 닫힘)\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code != 0
    assert "마커" in out


# ═══ 4. MEASURED 안의 좌표는 검사되지 않는다 ═══════════════════════════════

def test_dead_coordinate_inside_measured_block_is_ignored(mod, tmp_path, capsys):
    """블록 «안»에 죽은 파일 좌표를 넣어도 실패가 안 뜬다."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n<!-- MEASURED -->\n"
        "실측값 스냅샷 — 죽은 좌표라도 검사 제외: `docs/PAO_OPS_TYPOX.md`\n"
        "<!-- /MEASURED -->\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 0
    assert "실패 0" in out
    assert "전체 0" in out  # 블록 안 백틱뿐이라 검사 대상 자체가 0


# ═══ 5. 한 줄 열고 닫는 블록 + 백틱 안 리터럴 마커 예시 ════════════════════

def test_single_line_measured_block_excludes_its_content(mod, tmp_path, capsys):
    """한 줄 안에서 열고 닫는 MEASURED 블록도 정상 제외된다(§0-1·§0-3 스타일)."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n"
        "총 개수는 <!-- MEASURED -->죽은 좌표 `docs/PAO_OPS_TYPOX.md` 포함<!-- /MEASURED --> 이다.\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 0
    assert "실패 0" in out


def test_literal_marker_example_in_backticks_does_not_crash_or_miscount(mod, tmp_path, capsys):
    """PAO_OPS.md 6행 스타일: 백틱 안에 마커 리터럴 예시(짝 맞음)가 있어도 오작동하지 않는다.

    이 케이스가 현재 문서 헤더에 실재한다(§2-2 지시: "이 케이스에서 오작동하지 않아야
    하고, 이걸 테스트로 고정하세요")."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n"
        "> 규약: 실측값은 `<!-- MEASURED -->` … `<!-- /MEASURED -->` 블록 안에 쓴다.\n\n"
        "본문 좌표: `docs/PAO_OPS.md`\n",
        encoding="utf-8",
    )

    # 크래시 없이 끝나야 한다 — 마커 수가 맞으므로(리터럴 쌍) MarkerError 없음.
    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 0
    assert "실패 0" in out


# ═══ 6. 미분류가 실패로 새지 않는다 ════════════════════════════════════════

def test_unclassified_plain_values_are_not_failures(mod, tmp_path, capsys):
    """평범한 값(금액·상태 단어 등)은 미분류로 세지고 실패로 안 친다."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n"
        "오늘 비용 `1,234원` · 상태 `ok` · 무엇도 아닌 문장 `이건 그냥 값이다`\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 0
    assert "실패 0" in out
    assert "미분류 3" in out


# ═══ 7. 유형별 분포가 출력에 있다 ══════════════════════════════════════════

def test_type_breakdown_is_printed_and_matches_five_types(mod, tmp_path, capsys):
    """분류 붕괴를 눈으로 잡는 자리 — 파일/심볼/API/테이블.컬럼/크론이 stdout에 다 있다.

    각 유형에 실재하는 좌표를 하나씩 넣어 5유형이 정확히 각 1건씩 잡히는지도 함께 본다."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n"
        "파일: `docs/PAO_OPS.md`\n"
        "심볼: `backend/app/services/scheduler_service.py::_singleflight_lock`\n"
        "API: `GET /api/scheduler/health`\n"
        "테이블: `ad_costs.ad_spend`\n"
        "크론: `run_naver_profit_scorecard`\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 0
    assert "실패 0" in out
    assert "전체 5" in out
    assert "미분류 0" in out
    assert "유형별" in out
    assert "파일 1" in out
    assert "심볼 1" in out
    assert "API 1" in out
    assert "테이블.컬럼 1" in out
    assert "크론 1" in out


def test_type_breakdown_reflects_a_broken_type(mod, tmp_path, capsys):
    """유형별 분포는 실패가 섞여도(그 유형 카운트 자체는 안 줄고) 정직하게 보인다 —
    분류와 판정은 다른 층이라, 실패해도 «그 유형으로 셌다»는 사실은 남아야 한다."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n"
        "죽은 심볼: `backend/app/services/scheduler_service.py::_no_such_symbol_xyz`\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 1
    assert "실패 1" in out
    assert "심볼 1" in out
    assert "_no_such_symbol_xyz" in out


# ═══ 8. CLI 진입점(문서 인자) 스모크 ════════════════════════════════════════

def test_main_accepts_doc_argument(mod, tmp_path, capsys):
    """--doc 인자로 임의 경로를 넣을 수 있다(테스트 픽스처 주입 경로)."""
    doc = tmp_path / "fixture.md"
    doc.write_text("# fixture\n\n좌표 없음\n", encoding="utf-8")

    code = mod.main(["--doc", str(doc)])
    out = capsys.readouterr().out

    assert code == 0
    assert "전체 0" in out


def test_main_reports_missing_doc(mod, tmp_path, capsys):
    """문서 자체가 없으면(경로 오타 등) 비-0으로 종료하고 이유를 찍는다."""
    missing = tmp_path / "does_not_exist.md"

    code = mod.main(["--doc", str(missing)])
    out = capsys.readouterr().out

    assert code != 0
    assert "없음" in out
