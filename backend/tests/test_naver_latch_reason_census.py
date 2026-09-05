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


def test_non_block_rationale_is_none_not_unclassified():
    """「차단이 아닌 행」과 「분류 실패한 차단 행」을 섞지 않는다 — 후자만 드리프트 신호다."""
    assert census.classify_reason("[시간당밴드] ROAS-UP — 정착창 실측(…)") is None
    assert census.classify_reason("") is None
    assert census.classify_reason(
        f"[시간당밴드] CPC급등 {harness.WRITE_FAILURE_MARKER} HTTPError: 500"
    ) is None


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

import sqlite3


def _mk_db(tmp_path, rows):
    """rows = [(changed_at, rationale, before_value)] — 최소 스키마."""
    path = tmp_path / "census.db"
    con = sqlite3.connect(path)
    con.execute(
        "create table naver_change_log ("
        " changed_at text, entity_type text, action text,"
        " rationale text, before_value text)"
    )
    con.executemany(
        "insert into naver_change_log values (?, 'ad', 'update_bid', ?, ?)", rows
    )
    con.commit()
    con.close()
    return path


def _run_census(tmp_path, rows, capsys, monkeypatch):
    path = _mk_db(tmp_path, rows)
    monkeypatch.setattr(
        "sys.argv", ["latch_reason_census.py", "--db", str(path), "--days", "7"]
    )
    census.main()
    return capsys.readouterr().out


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
