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


# ═══ 4. MEASURED 안의 «좌표 모양» 백틱은 오염으로 잡힌다(P1 수리, 2026-08-30) ═══
#
# ★설계 변경: 예전엔 MEASURED 블록 안의 백틱은 무엇이든(죽은 좌표라도) 조용히 검사
# 제외됐다. 그런데 그 규칙 자체가 D8·D11(마커 쌍이 산문에 잘못 삽입되거나 닫는 마커가
# 아래로 밀리는 사고)을 침묵시키는 구멍이었다 — 삼켜진 진짜 좌표가 「검사 제외」와
# 똑같이 보였기 때문이다. 그래서 지금은: 블록 안에서 5유형 중 하나로 «분류만 되면»
# (살았든 죽었든) 그 자체가 오염 실패다. 블록은 «값»만 담아야 한다.

def test_classified_coordinate_inside_measured_block_is_flagged_as_leak(mod, tmp_path, capsys):
    """블록 «안»에 좌표 모양 백틱(살아있는 심볼이라도)이 있으면 오염 실패로 뜬다."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n<!-- MEASURED -->\n"
        "실측값 스냅샷인데 좌표가 섞였다: "
        "`backend/app/services/scheduler_service.py::_ensure_default_states`\n"
        "<!-- /MEASURED -->\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 1
    assert "블록내 좌표오염 1" in out
    assert "MEASURED 블록 내 좌표 오염 목록:" in out
    assert "_ensure_default_states" in out
    assert "전체 0" in out  # 블록 밖 좌표는 여전히 0(오염은 별도 카운트)


def test_dead_coordinate_inside_measured_block_is_also_flagged(mod, tmp_path, capsys):
    """죽은 좌표라도(존재 여부와 무관) 블록 안에 있으면 오염으로 잡힌다."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n<!-- MEASURED -->\n"
        "실측값 스냅샷 — 죽은 좌표가 섞였다: `docs/PAO_OPS_TYPOX.md`\n"
        "<!-- /MEASURED -->\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 1
    assert "docs/PAO_OPS_TYPOX.md" in out
    assert "블록내 좌표오염 1" in out


def test_unclassified_value_inside_measured_block_is_not_a_leak(mod, tmp_path, capsys):
    """값(좌표 모양이 아닌 백틱)은 블록 안에 있어도 오염이 아니다 — 정상 경로."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n<!-- MEASURED -->\n"
        "관측 2026-08-30 17:17 — 오늘 값 `19,923,726원`\n"
        "<!-- /MEASURED -->\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 0
    assert "실패 0" in out
    assert "블록내 좌표오염 0" in out
    assert "전체 0" in out  # 블록 안 백틱뿐이라 검사 대상 자체가 0


# ═══ 5. 한 줄 열고 닫는 블록 + 백틱 안 리터럴 마커 예시 ════════════════════

def test_single_line_measured_block_excludes_its_content(mod, tmp_path, capsys):
    """한 줄 안에서 열고 닫는 MEASURED 블록도 정상 제외된다(§0-1·§0-3 스타일).

    ★내용은 좌표 모양이 아닌 «값»이어야 한다 — 좌표 모양이면 §4의 오염 검사에 걸린다
    (그게 의도된 동작이다). 이 테스트는 순수하게 «한 줄 블록의 마커 파싱»만 본다."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n"
        "총 개수는 <!-- MEASURED -->오늘 값 `19,923,726원` 포함<!-- /MEASURED --> 이다.\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 0
    assert "실패 0" in out
    assert "블록내 좌표오염 0" in out


def test_literal_marker_example_in_backticks_does_not_crash_or_miscount(mod, tmp_path, capsys):
    """PAO_OPS.md 6행 스타일: 백틱 안에 마커 리터럴 예시(짝 맞음)가 있어도 오작동하지 않는다.

    ★P1 수리(2026-08-30): 예전엔 이 리터럴 예시가 «진짜 마커»로 잡혀 구간 하나를
    만들었다(실측 doc: 전체 7구간 중 1개가 이 리터럴). 지금은 백틱 안 마커를 마커로
    안 치므로 region이 0개다 — 리터럴 텍스트 자체는 그냥 미분류 값으로 남는다."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n"
        "> 규약: 실측값은 `<!-- MEASURED -->` … `<!-- /MEASURED -->` 블록 안에 쓴다.\n\n"
        "본문 좌표: `docs/PAO_OPS.md`\n",
        encoding="utf-8",
    )

    text = doc.read_text(encoding="utf-8")
    regions = mod.find_excluded_regions(text)
    assert regions == []  # 백틱 안 마커는 region을 만들지 않는다

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 0
    assert "실패 0" in out
    assert "MEASURED 구간 0" in out


def test_markers_inside_fenced_code_block_are_not_real_markers(mod, tmp_path, capsys):
    """펜스 코드블록(```) 안의 마커도 마커로 안 친다."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n"
        "```\n<!-- MEASURED -->\n예시일 뿐\n<!-- /MEASURED -->\n```\n\n"
        "본문 좌표: `docs/PAO_OPS.md`\n",
        encoding="utf-8",
    )

    text = doc.read_text(encoding="utf-8")
    regions = mod.find_excluded_regions(text)
    assert regions == []

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


# ═══ 9. 제외량은 항상 출력된다(P1 수리 ③) ══════════════════════════════════
#
# ★이 카운트는 「출력에 보이는가」가 핵심이다(위임문 §1). 값만 계산하고 stdout에
# 안 찍으면 고친 게 아니다 — 그래서 여기서 문자열 존재를 고정한다.

def test_region_and_exclusion_counts_are_always_printed(mod, tmp_path, capsys):
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n<!-- MEASURED -->\n오늘 값 `1,234원`\n<!-- /MEASURED -->\n\n"
        "본문 좌표: `docs/PAO_OPS.md`\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 0
    assert "MEASURED 구간 1" in out
    assert "제외 백틱 1" in out
    assert "블록내 좌표오염 0" in out


# ═══ 10. D8·D11 회귀 — 마커 «개수»는 맞는데 구간이 잘못 벌어지는 경우 ══════
#
# 리�이의 실제 재현(2026-08-30): D8은 정상 산문 한가운데 마커 쌍을 삽입, D11은 닫는
# 마커를 실제 위치보다 아래로 옮기는 편집. 둘 다 예전 코드에서는 exit 0으로 조용히
# 좌표를 삼켰다. 지금은 삼켜진 구간 안에 좌표 모양 백틱이 있으면 §4의 오염 검사가
# 잡는다.

def test_d8_marker_pair_inserted_around_prose_is_caught(mod, tmp_path, capsys):
    """D8: 마커 개수는 맞게(쌍으로) 산문 중간에 삽입 — 그 안의 진짜 좌표가 잡힌다."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n"
        "이 절은 원래 검사 대상이었다. <!-- MEASURED -->실수로 감싼 진짜 좌표: "
        "`backend/app/services/scheduler_service.py::_ensure_default_states`"
        "<!-- /MEASURED --> 여기까지.\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code != 0
    assert "블록내 좌표오염" in out
    assert "_ensure_default_states" in out


def test_d11_closing_marker_shifted_down_is_caught(mod, tmp_path, capsys):
    """D11: 닫는 마커가 원래 자리보다 아래로 밀려 그 사이 좌표를 삼킨다 — 잡혀야 한다."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n"
        "<!-- MEASURED -->\n오늘 값 `1,234원`\n<!-- /MEASURED -->\n\n"
        "그런데 닫는 마커가 실수로 여기까지 밀렸다고 가정하면: <!-- MEASURED -->\n"
        "정상적으로는 검사돼야 할 좌표: `docs/PAO_OPS_TYPOX.md`\n<!-- /MEASURED -->\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code != 0
    assert "블록내 좌표오염" in out
    assert "docs/PAO_OPS_TYPOX.md" in out


# ═══ 11. P2-2 — 테이블.컬럼 교차 오염 ══════════════════════════════════════

def test_table_column_cross_contamination_fails(mod, tmp_path, capsys):
    """테이블은 A에 실재하고 컬럼은 B에만 실재하는 «교차 오염» 좌표는 실패해야 한다.

    `naver_ad_daily`는 실재하는 테이블이고 `executed_change_log_id`는 실재하는 컬럼이지만
    ─ 서로 다른 테이블(`naver_proposals`) 소속이다. 예전 구현은 파일 전체에서 각각
    독립적으로 존재만 확인해 이 조합을 통과시켰다(P2-2, 2026-08-30 리뷰 재현)."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n교차 오염 좌표: `naver_ad_daily.executed_change_log_id`\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 1
    assert "실패 1" in out
    assert "naver_ad_daily.executed_change_log_id" in out


def test_table_column_correct_pairing_still_passes(mod, tmp_path, capsys):
    """대조군 — 실제 소속 조합은 여전히 통과한다."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n올바른 좌표: `naver_ad_daily.cost`\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 0
    assert "실패 0" in out


# ═══ 12. P2-3 — API 중간 세그먼트 오염 ═════════════════════════════════════

def test_api_middle_segment_contamination_fails(mod, tmp_path, capsys):
    """접미사만 실재하고 중간 세그먼트가 조작된 API 경로는 실패해야 한다.

    예전 폴백은 가장 긴 접미사("/health")가 routers 원문에 «리터럴 부분문자열»로
    있다는 이유만으로 이 좌표를 통과시켰다(P2-3, 2026-08-30 리뷰 재현)."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n오염된 API: `GET /api/COMPLETELY-BOGUS/health`\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 1
    assert "실패 1" in out
    assert "GET /api/COMPLETELY-BOGUS/health" in out


def test_api_exact_match_still_passes(mod, tmp_path, capsys):
    """대조군 — exact 매치 API 좌표는 여전히 통과한다."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n실재 API: `GET /api/scheduler/health`\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 0
    assert "실패 0" in out


# ═══ 13. P2-6 — 점으로 시작하는 파일 경로 ══════════════════════════════════

def test_dot_prefixed_file_path_is_classified_and_checked(mod, tmp_path, capsys):
    """`.github/workflows/...`처럼 점으로 시작하는 경로도 "file"로 분류·검사된다."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n죽은 점파일: `.github/workflows/does-not-exist-xyz.yml`\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 1
    assert "실패 1" in out
    assert "파일 1" in out
    assert ".github/workflows/does-not-exist-xyz.yml" in out


def test_dot_prefixed_live_file_path_passes(mod, tmp_path, capsys):
    """대조군 — 실재하는 점 경로는 통과한다."""
    live_path = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
    if not live_path.exists():
        pytest.skip(".github/workflows/ci.yml이 없는 환경")

    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n실재 점파일: `.github/workflows/ci.yml`\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 0
    assert "실패 0" in out


def test_frontend_route_leading_slash_stays_unclassified(mod, tmp_path, capsys):
    """P2-6 수리 후에도 앞에 "/"만 있는 프런트 라우팅 경로는 여전히 미분류다."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        "# fixture\n\n라우팅: `/naver-ad/performance`\n",
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 0
    assert "실패 0" in out
    assert "미분류 1" in out


def test_load_dotenv_snippet_stays_unclassified(mod, tmp_path, capsys):
    """P2-6 수리 후에도 괄호·따옴표가 섞인 코드 스니펫은 여전히 미분류다."""
    doc = tmp_path / "fixture.md"
    doc.write_text(
        '# fixture\n\n스니펫: `load_dotenv("/home/ubuntu/ohisell/backend/.env")`\n',
        encoding="utf-8",
    )

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 0
    assert "실패 0" in out
    assert "미분류 1" in out


# ═══ 14. P2-10 — cron·table·api 죽은 좌표가 전체 파이프라인에서 잡힌다 ═════
#
# ★기존 테스트는 check_cron/check_table/check_api를 각각 `return True, ""`로 바꿔도
# 15 passed 전건 초록이었다(리뷰 변이 M12·M13·M14 생존, 2026-08-30). 이 세 유형이 검사
# 대상 94건 중 44%였는데 죽은 좌표를 잡는 회귀가 하나도 없었다는 뜻이다. 아래는 각
# 유형에 대해 mod.run()「전체 파이프라인」으로 죽은 좌표를 넣어 실패 목록에 이름이
# 뜨는지 확인한다 — check_*()를 직접 부르지 않는다(그러면 스텁 변이를 못 잡는다).

def test_dead_cron_coordinate_is_named_in_failure_output(mod, tmp_path, capsys):
    doc = tmp_path / "fixture.md"
    dead_coord = "run_naver_totally_bogus_job_xyz"
    doc.write_text(f"# fixture\n\n죽은 크론: `{dead_coord}`\n", encoding="utf-8")

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 1
    assert "실패 1" in out
    assert dead_coord in out
    assert "크론 1" in out


def test_dead_table_coordinate_is_named_in_failure_output(mod, tmp_path, capsys):
    doc = tmp_path / "fixture.md"
    dead_coord = "bogus_table_zzz.bogus_column_zzz"
    doc.write_text(f"# fixture\n\n죽은 테이블: `{dead_coord}`\n", encoding="utf-8")

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 1
    assert "실패 1" in out
    assert dead_coord in out
    assert "테이블.컬럼 1" in out


def test_dead_api_coordinate_is_named_in_failure_output(mod, tmp_path, capsys):
    doc = tmp_path / "fixture.md"
    dead_coord = "GET /api/zzz-nonexistent-route-xyz123"
    doc.write_text(f"# fixture\n\n죽은 API: `{dead_coord}`\n", encoding="utf-8")

    code = mod.run(doc)
    out = capsys.readouterr().out

    assert code == 1
    assert "실패 1" in out
    assert dead_coord in out
    assert "API 1" in out


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
