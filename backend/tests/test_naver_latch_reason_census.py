# test_naver_latch_reason_census.py — D-NAO-288 §4-C ⓘ 계수기의 «드리프트 가드»
# 계수기: scripts/measurements/latch_reason_census.py (읽기 전용·앱 임포트 없음)
#
# ★이 파일이 지키는 문장: **「사유 문구를 바꾸면 계수기가 조용히 낡는 일이 없다」**
#   계수기는 prod에서 의존성 없이 돌려고 앱을 임포트하지 않는다(oscillation_symmetry_count와
#   같은 관례). 그 대가로 자유 텍스트에 기대게 되는데, 여기서 **진짜 guardrail_gate를 구동해
#   나온 문자열**로 계수기의 규칙표를 때린다 — 문구가 바뀌면 «미분류»가 되어 이 테스트가 죽는다.
#   (선언만 있고 집행이 없는 표를 만들지 않는다 — 계약 §2-3.)
#
# 실 API 0 · 실쓰기 0 · DB 0. 순수 판정기 + 문자열 분류기.
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.services.naver_ad import guardrail_gate as gate
from app.services.naver_ad import naver_execution_harness as harness

NOW = datetime(2026, 9, 5, 12, 20, 0)


def _load_census():
    path = (
        Path(__file__).resolve().parents[2]
        / "scripts" / "measurements" / "latch_reason_census.py"
    )
    spec = importlib.util.spec_from_file_location("latch_reason_census", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


census = _load_census()


def _ctx(**over):
    """모든 게이트를 통과하는 «건강한» 컨텍스트 — 케이스마다 한 축만 망가뜨린다."""
    base = {
        "current_bid": 190,
        "current_budget": None,
        "roas_corrected": 250.0,
        "target_roas": 150.0,
        "cost_today": 10_000,
        "daily_budget": 500_000,
        "unconverted_spend": 0,
        "last_change_at": None,
        "changes_today_count": 0,
        "auto_exec": True,
        "floor_exempt": False,
        "campaign_weekly_conv": 100,
        "target_weekly_conv": 100,
        "external_check": "ours",
    }
    base.update(over)
    return base


def _prop(proposal_type="bid_up", target_bid=210):
    return {"proposal_type": proposal_type, "target_bid": target_bid, "target_lock": None}


# ══════════════ A. 드리프트 가드 — 진짜 게이트가 뱉는 12종이 전부 «키»로 떨어진다 ══════════════

# (기대 키, proposal, context 덮어쓰기) — 각 항목은 guardrail_gate의 서로 다른 return 한 자리.
GATE_CASES = [
    ("표본 하한 게이트 (D-NAO-286)", _prop(), {"campaign_weekly_conv": 0}),
    ("쿨다운 2h (D-NAO-19)", _prop(), {"last_change_at": NOW - timedelta(hours=1)}),
    ("자동 하향 일일 상한 (D-NAO-125)",
     _prop("bid_down", 170), {"auto_bid_down_today": 3}),
    ("일일 변경 건수 상한", _prop(), {"changes_today_count": 3}),
    ("자동 상향 누적 상한 (D-NAO-129)", _prop(), {"auto_up_base_bid": 100}),
    ("외부변경 확인 fail-closed (D-NAO-130)", _prop(), {"external_check": None}),
    ("스톱로스 (D-NAO-20)", _prop(), {"unconverted_spend": 10_000_000}),
    ("출시창 순위 하한 (D-NAO-121)",
     _prop("bid_down", 170), {"launch_floor_bid": 180, "launch_target_rank": 3}),
    ("BEP 미달 증액 금지", _prop(), {"roas_corrected": 100.0}),
    ("일예산 상한 불가침", _prop(), {"cost_today": 500_000}),
    ("변경폭 상한 (D-NAO-5·71)", _prop(target_bid=300), {}),
    ("방향 불일치 (구조 결함)", _prop(target_bid=180), {}),
    ("current_bid 미확보 (fail-closed)", _prop(), {"current_bid": None}),
    ("target_bid 없음 (구조 결함)", _prop(target_bid=None), {}),
    ("target_bid 범위 밖", _prop(target_bid=7), {}),
]


@pytest.mark.parametrize("expected_key,proposal,over", GATE_CASES,
                         ids=[c[0] for c in GATE_CASES])
def test_real_gate_reason_classifies_to_expected_key(expected_key, proposal, over):
    """★핵심 방어 — 실제 guardrail_gate 문자열 → 계수기 키.

    게이트 문구가 바뀌면 classify_reason이 '(미분류)'를 돌려주고 이 테스트가 죽는다.
    """
    reason = gate.check(proposal, _ctx(**over), now=NOW)
    assert reason is not None, f"{expected_key}: 이 컨텍스트는 차단돼야 하는데 통과했다"

    # harness._guard_failure가 실제로 만드는 rationale 모양 그대로 조립한다.
    rationale = f"[시간당밴드] ROAS-UP(순위 무관) — 어쩌고 {harness.GUARD_BLOCK_MARKER} 가드레일 차단 — {reason}"
    assert census.classify_reason(rationale) == expected_key, (
        f"게이트 사유 문구가 바뀐 것으로 보인다 — 계수기 REASON_RULES 갱신 필요.\n"
        f"  게이트 원문: {reason!r}"
    )


def test_every_reason_rule_is_exercised_by_a_case():
    """규칙표에 «아무도 안 때리는 줄»이 남지 않게 한다 — 죽은 규칙은 거짓 안심이다."""
    covered = {key for key, _, _ in GATE_CASES}
    declared = {key for _, key in census.REASON_RULES}
    assert declared - covered == set(), f"실게이트로 구동되지 않는 규칙: {declared - covered}"
    assert covered - declared == set(), f"규칙표에 없는 기대 키: {covered - declared}"


# ══════════════ B. 마커·경계 ══════════════

def test_markers_match_the_harness_single_source():
    """계수기가 grep하는 마커는 harness가 실제로 쓰는 그 값이어야 한다."""
    assert census.GUARD_BLOCK_MARKER == harness.GUARD_BLOCK_MARKER
    assert census.WRITE_FAILURE_MARKER == harness.WRITE_FAILURE_MARKER


def test_reason_prefix_matches_what_the_harness_actually_writes():
    """★적대 리뷰 P2-8 — 접두사 `가드레일 차단 — `는 이 설계에서 하중이 가장 큰 리터럴이다.

    harness는 `reason = f"가드레일 차단 — {block_reason}"`으로 만든다(:2092·2197·2320).
    상수만 맞추고 «실제로 그 모양이 나오는지»를 안 보면 상수끼리만 일치하는 동어반복이 된다.
    """
    assert census.GUARD_BLOCK_REASON_PREFIX == "가드레일 차단 — "
    src = (
        Path(harness.__file__).read_text()
    )
    assert 'f"가드레일 차단 — {block_reason}"' in src, (
        "harness가 접두사를 바꿨다 — 계수기 GUARD_BLOCK_REASON_PREFIX를 함께 고칠 것"
    )


def test_classify_reason_is_three_way_not_two():
    """★적대 리뷰 P1-2 (a) — 「모름」과 「막힘」을 섞지 않는다.

    초판은 「`[실행 불가]`인데 게이트 사유가 아닌 행」을 None으로 떨궈 **확실히 안 바뀐 행을
    「모름」 통에** 넣었다. 하니스 :267이 금지한 것의 역방향이다.
    """
    # ① 마커 없음 = 「모름」 → None
    assert census.classify_reason("[시간당밴드] ROAS-UP — 정착창 실측(…)") is None
    assert census.classify_reason("") is None
    assert census.classify_reason(
        f"[시간당밴드] CPC급등 {harness.WRITE_FAILURE_MARKER} HTTPError: 500"
    ) is None

    # ② 마커 있고 게이트 사유 아님 = harness 자체 사전 가드 → 「막힘」이되 게이트 키 없음
    assert census.classify_reason(
        f"[시간당밴드] ROAS-UP {harness.GUARD_BLOCK_MARKER} "
        "ad 증액 가드 컨텍스트 불완전(fail-closed) — BEP(roas_corrected=None, target_roas=None)"
    ) == census.NON_GATE_BLOCK

    # ③ 마커 있고 게이트 사유 = 키
    assert census.classify_reason(
        f"x {harness.GUARD_BLOCK_MARKER} 가드레일 차단 — "
        "쿨다운 중 — 마지막 변경 1.0시간 전(최소 2시간 필요, D-NAO-19)"
    ) == "쿨다운 2h (D-NAO-19)"


def test_unknown_block_reason_is_unclassified():
    """모르는 사유는 조용히 통과시키지 않고 «미분류»로 세운다(계수기가 ⚠️를 찍는 입력)."""
    rationale = f"[시간당밴드] ROAS-UP {harness.GUARD_BLOCK_MARKER} 가드레일 차단 — 새로 생긴 사유"
    assert census.classify_reason(rationale) == census.UNCLASSIFIED


# ══════════════ C. 레인 분류 — 실측에서 실제로 틀렸던 자리 ══════════════

def test_rank_leash_wins_over_intraday_up():
    """★회귀 — 순위고삐 사유문은 본문에 「장중loss」를 담는다.

    2026-09-05 첫 계수에서 이 순서를 잘못 둬 순위고삐 9건이 «액셀 장중UP»으로 셌다.
    순서가 뒤집히면 액셀:브레이크 대칭 수(북극성 §7)가 17:20 → 26:11로 거짓말한다.
    """
    rationale = ("[순위고삐] 순위고삐(장중loss) — 추정ROAS 1.1520 < BEP 2.0043, "
                 "당일소진 14583≥하루평균 6919")
    assert census.classify(rationale, census.LANE_RULES) == "브레이크 순위고삐"


@pytest.mark.parametrize("rationale,expected", [
    ("[시간당밴드] CPC급등 — 당일=1633.3원 > 정착창기준=794.7원×2", "브레이크 CPC급등"),
    ("[시간당밴드] ROAS-UP(순위 무관, D-NAO-66) — 정착창 실측(…)", "액셀 ROAS-UP"),
    ("[시간당밴드] 장중 tally 상향 — …", "액셀 장중UP"),
    ("무슨 새 레인 — …", "(미분류)"),
])
def test_lane_classification(rationale, expected):
    assert census.classify(rationale, census.LANE_RULES) == expected


# ══════════════ D. 표면 — 계수기가 «낡았다»고 자백하는 그 줄 ══════════════
#
# ★이 절은 자기검증 변이 M5가 살아남아서 생겼다(2026-09-05). REASON_RULES를 지우거나
#   게이트 문구가 바뀌면 A절이 죽지만, 그건 **저장소 안에서만** 그렇다. prod에서 계수기를
#   돌리는 사람이 「미분류가 몇 건인지」를 못 보면 계수기는 조용히 낡은 수를 낸다 —
#   이 트랙의 상습 실패 모드가 정확히 그것이다(「라벨은 붙고 집행 안 됨」).
#   그래서 **사람이 읽는 마지막 줄**을 여기서 지킨다.

import itertools
import sqlite3


_db_seq = itertools.count()


def _mk_db(tmp_path, rows):
    """rows = [(changed_at, rationale, before_value)] 또는 5튜플(+entity_type, action)."""
    path = tmp_path / f"census{next(_db_seq)}.db"
    con = sqlite3.connect(path)
    con.execute(
        "create table naver_change_log ("
        " changed_at text, entity_type text, action text,"
        " rationale text, before_value text)"
    )
    norm = [
        r if len(r) == 5 else (r[0], "ad", "update_bid", r[1], r[2])
        for r in ((*x,) for x in rows)
    ]
    con.executemany(
        "insert into naver_change_log(changed_at, entity_type, action, rationale, before_value)"
        " values (?, ?, ?, ?, ?)",
        [(r[0], r[1], r[2], r[3], r[4]) for r in norm],
    )
    con.commit()
    con.close()
    return path


def _run_census(tmp_path, rows, capsys, monkeypatch, argv_extra=()):
    path = _mk_db(tmp_path, rows)
    argv = ["latch_reason_census.py", "--db", str(path), *argv_extra]
    monkeypatch.setattr("sys.argv", argv)
    census.main()
    return capsys.readouterr().out


def _blocked(reason):
    return f"[시간당밴드] ROAS-UP {harness.GUARD_BLOCK_MARKER} 가드레일 차단 — {reason}"


_COOLDOWN = "쿨다운 중 — 마지막 변경 1.0시간 전(최소 2시간 필요, D-NAO-19)"


def test_census_confesses_when_a_block_reason_is_unclassified(tmp_path, capsys, monkeypatch):
    """★표면 — 모르는 사유가 섞이면 화면에 ⚠️ 가 뜬다. 이 줄이 없으면 계수기는 조용히 낡는다."""
    out = _run_census(tmp_path, [
        ("2026-09-05 11:20:00",
         f"[시간당밴드] ROAS-UP {harness.GUARD_BLOCK_MARKER} 가드레일 차단 — 아무도 모르는 새 사유",
         None),
    ], capsys, monkeypatch)
    assert "⚠️" in out and "미분류 차단 행 1건" in out


def test_census_is_silent_when_everything_classifies(tmp_path, capsys, monkeypatch):
    """반대 방향 — 다 분류되면 ⚠️ 를 띄우지 않는다(늑대소년 방지)."""
    out = _run_census(tmp_path, [
        ("2026-09-05 11:20:00",
         f"[시간당밴드] ROAS-UP {harness.GUARD_BLOCK_MARKER} 가드레일 차단 — "
         "쿨다운 중 — 마지막 변경 1.0시간 전(최소 2시간 필요, D-NAO-19)",
         None),
        ("2026-09-05 13:20:00", "[시간당밴드] ROAS-UP — 정착창 실측(…)", '{"adAttr":{"bidAmt":1330}}'),
    ], capsys, monkeypatch)
    assert "⚠️" not in out
    assert "전체 2건 · 무쓰기 재발화 1건 (50.0%)" in out
    assert "쿨다운 2h (D-NAO-19)" in out


def test_census_confesses_unknown_lane(tmp_path, capsys, monkeypatch):
    """레인 미분류도 표면에 뜬다 — 액셀:브레이크 대칭 수(북극성 §7)가 조용히 틀리는 것을 막는다."""
    out = _run_census(tmp_path, [
        ("2026-09-05 11:20:00",
         f"[새밴드] 무슨 새 레인 {harness.GUARD_BLOCK_MARKER} 가드레일 차단 — "
         "쿨다운 중 — 마지막 변경 1.0시간 전(최소 2시간 필요, D-NAO-19)",
         None),
    ], capsys, monkeypatch)
    assert "레인 미분류 1건" in out


def test_lane_denominator_excludes_unknowns(tmp_path, capsys, monkeypatch):
    """★적대 리뷰 P1-2 (b) — 레인 표는 «막힌 행»만 센다.

    이 표가 곧 계약·ref 134·트랙이 인용하는 「액셀 N : 브레이크 M」 = 북극성 §7 대칭 수다.
    쓰기 «실패»(모름)를 섞으면 분모가 거짓이 되고, 두 표의 합이 같아서 같은 모집단처럼 보인다.
    """
    out = _run_census(tmp_path, [
        ("2026-09-05 11:20:00", _blocked(_COOLDOWN), None),                       # 막힘·액셀
        ("2026-09-05 12:20:00",                                                   # 「모름」·액셀 문구
         f"[시간당밴드] ROAS-UP {harness.WRITE_FAILURE_MARKER} HTTPError: 400 code=3830", None),
    ], capsys, monkeypatch)
    assert "분모 = `[실행 불가]` 1건" in out and "「모름」 1건 제외" in out
    # 액셀은 «막힌» 1건만 세어야 한다 — 2가 되면 대칭 수가 거짓이 된다.
    lane = out.split("막힌 판정의 레인별")[1]
    assert "1  액셀 ROAS-UP" in lane and "2  액셀 ROAS-UP" not in lane
    assert "「모름」이다" in out          # ℹ️ 자백 줄 (M10)


def test_non_gate_block_is_counted_as_blocked_not_unknown(tmp_path, capsys, monkeypatch):
    """★P1-2 (a) 표면판 — harness 자체 사전 가드는 「확실히 안 바뀐 행」이라 막힌 것으로 센다."""
    out = _run_census(tmp_path, [
        ("2026-09-05 11:20:00",
         f"[시간당밴드] ROAS-UP {harness.GUARD_BLOCK_MARKER} "
         "ad 증액 가드 컨텍스트 불완전(fail-closed) — BEP(roas_corrected=None)", None),
    ], capsys, monkeypatch)
    assert census.NON_GATE_BLOCK in out
    assert "분모 = `[실행 불가]` 1건" in out
    assert "「모름」" not in out.split("--- ★막은")[1].split("---")[0]
    assert "guardrail_gate «밖»의 harness 사전 가드 1건" in out   # ℹ️ 두 번째 자백 줄


def test_grain_filter_actually_filters(tmp_path, capsys, monkeypatch):
    """★적대 리뷰 M9 생존분 — `entity_type` 필터가 무테스트였다(픽스처가 ad만 넣어서)."""
    rows = [
        ("2026-09-05 11:20:00", "ad", "update_bid", _blocked(_COOLDOWN), None),
        ("2026-09-05 11:20:00", "adgroup", "update_bid", _blocked(_COOLDOWN), None),
        ("2026-09-05 11:20:00", "ad", "set_user_lock", _blocked(_COOLDOWN), None),
    ]
    out = _run_census(tmp_path, rows, capsys, monkeypatch)
    assert "전체 1건" in out, "entity_type·action 필터가 안 걸렸다"
    out2 = _run_census(tmp_path, rows, capsys, monkeypatch,
                       argv_extra=("--entity-type", "adgroup"))
    assert "entity_type=adgroup" in out2 and "전체 1건" in out2


def test_default_window_is_the_one_the_contract_quotes(tmp_path, capsys, monkeypatch):
    """★적대 리뷰 M11 생존분 — `--days` 기본값이 무테스트라 조용히 바뀔 수 있었다."""
    assert census.DEFAULT_DAYS == 7
    out = _run_census(tmp_path, [
        ("2026-09-05 11:20:00", _blocked(_COOLDOWN), None),
    ], capsys, monkeypatch)
    assert "최근 7일" in out


def test_window_is_kst_not_utc(tmp_path, capsys, monkeypatch):
    """★적대 리뷰 P2-5 — `changed_at`은 KST-naive인데 sqlite `now`는 UTC다.

    보정이 없으면 창이 실제로 7일 9시간이 되고 「최근 7일」 라벨이 거짓이 된다.
    """
    src = (Path(census.__file__).read_text() if hasattr(census, "__file__")
           else (Path(__file__).resolve().parents[2] / "scripts" / "measurements"
                 / "latch_reason_census.py").read_text())
    assert "'+9 hours'" in src, "KST 보정이 사라졌다"


# ══════════════ D. 그레인 — 창·소재·컷오프를 다시 세울 수 있다 (D-NAO-292 · n=5) ══════════════
#
# ★계약 §4-C ⓘ 원문이 남긴 지시가 이 절이다: *"계수기에는 `entity_id` 필터가 없다 — 일주일 뒤에
#   돌려도 이 도구로는 그 전후 비교를 낼 수 없다. **다음 세션은 여기서 시작한다.**"*
#   그리고 세 필터를 붙여 다시 세어 보니 **27/54는 분자와 분모의 grain이 다른 비율이었다** —
#   27은 소재 1개 무쓰기, 54는 전 소재 전체다. 여기서 고정하는 것은 **그 두 수가 서로 다른
#   질문의 답이라는 것**이고, 그래서 세 필터가 «항상 출력에 찍혀야» 한다.

import sqlite3
import subprocess
import sys

CENSUS_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "measurements" / "latch_reason_census.py"
)


def _mk_grain_db(path, rows):
    """rows: (changed_at, entity_id, wrote)"""
    con = sqlite3.connect(path)
    con.executescript(
        """
        create table naver_change_log (
            id integer primary key, action text, entity_type text, entity_id text,
            changed_at text, before_value text, rationale text
        );
        """
    )
    for i, (changed_at, eid, wrote) in enumerate(rows, start=1):
        con.execute(
            "insert into naver_change_log values (?,?,?,?,?,?,?)",
            (i, "update_bid", "ad", eid, changed_at,
             '{"adAttr":{"bidAmt":1000}}' if wrote else None,
             "[실행 불가] 가드레일 차단 — 쿨다운 중 — 2시간"),
        )
    con.commit()
    con.close()


CENSUS_ROWS = [
    ("2026-09-02 10:20:00", "nad-1", True),
    ("2026-09-03 10:20:00", "nad-1", False),
    ("2026-09-03 11:20:00", "nad-1", False),
    # ★P2-4 — 컷오프와 «정확히 같은» 시각. `--as-of`는 배타(`<`)이므로 이 행은 빠져야 한다.
    ("2026-09-03 12:00:00", "nad-1", False),
    ("2026-09-03 23:20:00", "nad-1", False),   # as-of 12:00 컷오프 밖
    ("2026-09-03 10:20:00", "nad-2", False),   # 다른 소재
    # ★P2-3 — `--until` 당일 행. 포함 경계(`<=`)가 아니면 헤드라인 수가 조용히 줄어든다.
    ("2026-09-05 10:20:00", "nad-1", False),
    ("2026-09-09 10:20:00", "nad-1", False),   # 창 밖
]


_grain_seq = itertools.count()


def _run_grain(tmp_path, *extra):
    # 한 테스트가 두 번 부를 수 있다(경계 비교) — 호출마다 새 파일.
    db = tmp_path / f"c{next(_grain_seq)}.db"
    _mk_grain_db(db, CENSUS_ROWS)
    out = subprocess.run(
        [sys.executable, str(CENSUS_PATH), "--db", str(db),
         "--since", "2026-09-02", "--until", "2026-09-05", *extra],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def test_window_filter_excludes_rows_outside(tmp_path):
    out = _run_grain(tmp_path)
    assert "창 2026-09-02 ~ 2026-09-05" in out
    assert "전체 7건" in out          # 09-09 행만 빠진다


def test_entity_filter_narrows_to_one_creative(tmp_path):
    out = _run_grain(tmp_path, "--entity-id", "nad-1")
    assert "전체 6건" in out
    assert "소재 필터 = nad-1" in out


def test_as_of_cutoff_rewinds_the_instant(tmp_path):
    out = _run_grain(tmp_path, "--entity-id", "nad-1", "--as-of", "2026-09-03 12:00")
    # ★배타 경계 — 12:00:00 «정확히» 그 시각의 행은 빠진다(P2-4).
    assert "전체 3건 · 무쓰기 재발화 2건" in out
    assert "컷오프(--as-of) = 2026-09-03 12:00" in out


def test_as_of_boundary_is_exclusive_at_second_precision(tmp_path):
    """★2R 신규 — 기존 픽스처는 `--as-of '…12:00'`(초 없음)과 행 `'…12:00:00'`의 **문자열 길이**로
    빠졌다. 그래서 `<`를 `<=`로 바꿔도 결과가 같았다 — 주석이 설명하는 메커니즘(배타 경계)과
    실제로 작동한 메커니즘(길이)이 달랐다. 초까지 준 케이스가 진짜 경계를 세운다.
    """
    out = _run_grain(tmp_path, "--entity-id", "nad-1", "--as-of", "2026-09-03 12:00:00")
    assert "전체 3건 · 무쓰기 재발화 2건" in out   # 12:00:00 행은 배타 경계라 빠진다


def test_until_boundary_is_inclusive(tmp_path):
    """★P2-3 — `--until` 당일 행이 빠지면 헤드라인 수가 조용히 줄어든다(prod 실측: 36/25 vs 40/27)."""
    assert "전체 7건" in _run_grain(tmp_path)                      # --until 2026-09-05 (기본)
    assert "전체 6건" in _run_grain(tmp_path, "--until", "2026-09-04")


def test_grain_of_the_ratio_is_always_printed(tmp_path):
    """★27/54가 굳은 이유가 이것이다 — 창·소재·컷오프가 출력에 없으면 두 수의 grain이 안 보인다."""
    out = _run_grain(tmp_path)
    assert "소재 필터 = (전 소재)" in out
    assert "컷오프(--as-of) = (없음 — 현재까지)" in out


def test_days_and_explicit_window_conflict_is_confessed(tmp_path):
    db = tmp_path / "c2.db"
    _mk_grain_db(db, CENSUS_ROWS)
    out = subprocess.run(
        [sys.executable, str(CENSUS_PATH), "--db", str(db),
         "--since", "2026-09-02", "--days", "30"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "--days 는 무시한다" in out


def test_census_connect_is_read_only_ast():
    """교훈 #397 — 리터럴 존재가 아니라 «호출»을 본다(주석에 심어도 통과하지 않게)."""
    import ast

    tree = ast.parse(CENSUS_PATH.read_text())
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "connect"
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "sqlite3"
    ]
    assert calls
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert isinstance(kw.get("uri"), ast.Constant) and kw["uri"].value is True
        arg = call.args[0]
        assert isinstance(arg, ast.JoinedStr)
        tail = arg.values[-1]
        assert isinstance(tail, ast.Constant) and tail.value.endswith("?mode=ro")
