# test_naver_guardrail_gate.py — X1b T2 guardrail_gate 순수 판정 함수 단위테스트
# 근거: docs/PLAN_naver-ad-execution-loop.md §3 X1b, D-NAO-5/19/20. DB·API 접근 없음.
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services.naver_ad import guardrail_gate as gate


NOW = datetime(2026, 7, 11, 10, 0, 0)


def _bid_proposal(proposal_type="bid_up", target_bid=210):
    return {"proposal_type": proposal_type, "target_bid": target_bid, "target_lock": None}


def _lock_proposal(proposal_type="pause", target_lock=True):
    return {"proposal_type": proposal_type, "target_bid": None, "target_lock": target_lock}


def _budget_proposal(proposal_type="budget_up", target_budget=150_000):
    return {
        "proposal_type": proposal_type, "target_bid": None, "target_lock": None,
        "target_budget": target_budget,
    }


def _ctx(**overrides):
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
    }
    base.update(overrides)
    return base


# ── bid_up: 통과 경로 ─────────────────────────────────────────────────────


def test_bid_up_within_all_limits_passes():
    # 190 -> 210는 +10% 이내(±15% 상한)
    assert gate.check(_bid_proposal("bid_up", 210), _ctx(), now=NOW) is None


def test_bid_down_within_all_limits_passes():
    assert gate.check(_bid_proposal("bid_down", 170), _ctx(), now=NOW) is None


# ── 클램프(70~100,000원, 10원 단위) ──────────────────────────────────────


def test_bid_below_min_blocked():
    reason = gate.check(_bid_proposal("bid_up", 60), _ctx(current_bid=60), now=NOW)
    assert reason is not None
    assert "70~100,000" in reason


def test_bid_above_max_blocked():
    reason = gate.check(_bid_proposal("bid_up", 100_010), _ctx(current_bid=90_000), now=NOW)
    assert reason is not None


def test_bid_not_multiple_of_10_blocked():
    reason = gate.check(_bid_proposal("bid_up", 205), _ctx(), now=NOW)
    assert reason is not None


def test_target_bid_missing_blocked():
    reason = gate.check(_bid_proposal("bid_up", None), _ctx(), now=NOW)
    assert reason is not None
    assert "target_bid" in reason


# ── VT4 P1-1: campaign_type 인지형 입찰 하한(SHOPPING 50 / 그 외 70) ──────────


def test_shopping_bid_60_passes_min_bid():
    """SHOPPING이면 하한 50 — 50→60 탐색 스텝(bid_up_explore, 변경폭 30% 상한)이 게이트 통과."""
    reason = gate.check(
        _bid_proposal("bid_up_explore", 60),
        _ctx(current_bid=50, campaign_type="SHOPPING"),
        now=NOW,
    )
    assert reason is None


def test_non_shopping_bid_60_blocked_by_70_floor():
    """campaign_type 미확보(None)면 보수 70 하한 — 60원은 유효 범위 밖으로 차단."""
    reason = gate.check(
        _bid_proposal("bid_up_explore", 60),
        _ctx(current_bid=50, campaign_type=None),
        now=NOW,
    )
    assert reason is not None
    assert "70~100,000" in reason


def test_web_site_bid_60_blocked_by_70_floor():
    """WEB_SITE(파워링크 키워드 grain)는 70 하한 유지 — 60원 차단."""
    reason = gate.check(
        _bid_proposal("bid_up_explore", 60),
        _ctx(current_bid=50, campaign_type="WEB_SITE"),
        now=NOW,
    )
    assert reason is not None
    assert "70~100,000" in reason


def test_shopping_bid_40_still_blocked_below_50():
    """SHOPPING이어도 50 미만(40원)은 차단 — 하한 표기가 동적으로 50~로 바뀐다."""
    reason = gate.check(
        _bid_proposal("bid_up_explore", 40),
        _ctx(current_bid=40, campaign_type="SHOPPING"),
        now=NOW,
    )
    assert reason is not None
    assert "50~100,000" in reason


# ── 변경폭 ±15% ───────────────────────────────────────────────────────────


def test_bid_up_change_pct_exceeds_15_percent_blocked():
    # 190 -> 230은 +21.05%
    reason = gate.check(_bid_proposal("bid_up", 230), _ctx(current_bid=190), now=NOW)
    assert reason is not None
    assert "변경폭" in reason


def test_bid_down_change_pct_exceeds_15_percent_blocked():
    # 190 -> 150은 -21.05%
    reason = gate.check(_bid_proposal("bid_down", 150), _ctx(current_bid=190), now=NOW)
    assert reason is not None
    assert "변경폭" in reason


def test_bid_up_change_pct_exactly_at_boundary_passes():
    # 200 -> 230은 정확히 +15%
    reason = gate.check(_bid_proposal("bid_up", 230), _ctx(current_bid=200), now=NOW)
    assert reason is None


def test_growth_bid_up_exempt_from_change_pct(monkeypatch):
    # D-NAO-20-③: 신규/육성 트랙은 ±15% 비적용 — 190 -> 300(+57.9%)도 변경폭 사유로는 안 걸림
    reason = gate.check(
        _bid_proposal("growth_bid_up", 300),
        _ctx(current_bid=190, unconverted_spend=0),
        now=NOW,
    )
    assert reason is None


def test_current_bid_missing_blocked_fail_closed():
    reason = gate.check(_bid_proposal("bid_up", 210), _ctx(current_bid=None), now=NOW)
    assert reason is not None
    assert "current_bid" in reason


# ── 스톱로스 절대액 (bid_up/growth_bid_up만) ─────────────────────────────


def test_bid_up_stop_loss_reached_blocked():
    # stop_loss_amount = target_bid(210) * STOP_LOSS_CLICK_MULTIPLE(10) = 2,100원
    reason = gate.check(
        _bid_proposal("bid_up", 210), _ctx(current_bid=190, unconverted_spend=2_100), now=NOW,
    )
    assert reason is not None
    assert "스톱로스" in reason


def test_bid_up_stop_loss_not_reached_passes():
    reason = gate.check(
        _bid_proposal("bid_up", 210), _ctx(current_bid=190, unconverted_spend=1_000), now=NOW,
    )
    assert reason is None


def test_bid_down_not_subject_to_stop_loss():
    # bid_down은 지출 축소 방향이라 스톱로스 무관 — unconverted_spend가 커도 통과
    reason = gate.check(
        _bid_proposal("bid_down", 170), _ctx(current_bid=190, unconverted_spend=999_999), now=NOW,
    )
    assert reason is None


# ── BEP 미달 증액 금지 (bid_up/growth_bid_up만) ──────────────────────────


def test_bid_up_bep_below_target_blocked():
    reason = gate.check(
        _bid_proposal("bid_up", 210),
        _ctx(current_bid=190, roas_corrected=100.0, target_roas=150.0),
        now=NOW,
    )
    assert reason is not None
    assert "BEP" in reason


def test_bid_down_not_subject_to_bep_check():
    # bid_down은 손실 축소 방향이라 BEP 미달이어도 통과(오히려 그게 목적)
    reason = gate.check(
        _bid_proposal("bid_down", 170),
        _ctx(current_bid=190, roas_corrected=100.0, target_roas=150.0),
        now=NOW,
    )
    assert reason is None


def test_bid_down_passes_with_only_current_bid_minimal_context():
    """쇼핑 adgroup 대상(D-NAO-16 3단계 SHOPPING 확장): _build_guardrail_context가
    adgroup_window_agg 미구현(정직 경계)이라 current_bid 외 전부 None/기본값으로 채워도,
    bid_down은 up 전용 검사(스톱로스·BEP·일예산)를 면제받으므로 current_bid + 쿨다운/일일
    카운트만으로 guardrail_gate를 통과해야 한다(±15%·클램프는 여전히 검사됨)."""
    minimal_ctx = {
        "current_bid": 1200, "current_budget": None, "roas_corrected": None, "target_roas": None,
        "cost_today": None, "daily_budget": None, "unconverted_spend": None,
        "last_change_at": None, "changes_today_count": 0,
    }
    # 1200 -> 1100은 -8.3%(±15% 이내), 방향도 일치
    reason = gate.check(_bid_proposal("bid_down", 1100), minimal_ctx, now=NOW)
    assert reason is None


def test_bid_down_minimal_context_still_enforces_clamp_and_change_pct():
    """위 최소 컨텍스트에서도 클램프·변경폭 검사는 여전히 살아있다(면제되는 건 up 전용
    검사뿐 — bid_down이 '전부 무검증'은 아님)."""
    minimal_ctx = {
        "current_bid": 1200, "current_budget": None, "roas_corrected": None, "target_roas": None,
        "cost_today": None, "daily_budget": None, "unconverted_spend": None,
        "last_change_at": None, "changes_today_count": 0,
    }
    # 1200 -> 900은 -25%(±15% 초과)
    reason = gate.check(_bid_proposal("bid_down", 900), minimal_ctx, now=NOW)
    assert reason is not None
    assert "변경폭" in reason


# ── 일예산 상한 불가침 (bid_up/growth_bid_up만) ──────────────────────────


def test_bid_up_daily_budget_exhausted_blocked():
    reason = gate.check(
        _bid_proposal("bid_up", 210),
        _ctx(current_bid=190, cost_today=500_000, daily_budget=500_000),
        now=NOW,
    )
    assert reason is not None
    assert "일예산" in reason


def test_bid_up_zero_daily_budget_treated_as_unset_passes():
    # [codex P2] dailyBudget=0은 budget_allocator 기존 관행상 "미설정"(useDailyBudget=false)
    # — cost_today>=0은 항상 참이 되어 uncapped 캠페인의 정상 bid_up까지 차단하면 안 됨
    reason = gate.check(
        _bid_proposal("bid_up", 210), _ctx(current_bid=190, cost_today=50_000, daily_budget=0), now=NOW,
    )
    assert reason is None


def test_bid_up_daily_budget_not_exhausted_passes():
    reason = gate.check(
        _bid_proposal("bid_up", 210),
        _ctx(current_bid=190, cost_today=499_990, daily_budget=500_000),
        now=NOW,
    )
    assert reason is None


# ── 쿨다운 (전 액션 유형 공통) ────────────────────────────────────────────


def test_cooldown_active_blocked():
    reason = gate.check(
        _bid_proposal("bid_up", 210),
        _ctx(current_bid=190, last_change_at=NOW - timedelta(hours=gate._COOLDOWN_HOURS / 2)),
        now=NOW,
    )
    assert reason is not None
    assert "쿨다운" in reason


def test_cooldown_elapsed_passes():
    reason = gate.check(
        _bid_proposal("bid_up", 210),
        _ctx(current_bid=190, last_change_at=NOW - timedelta(hours=6)),
        now=NOW,
    )
    assert reason is None


def test_cooldown_applies_to_lock_types_too():
    reason = gate.check(
        _lock_proposal("pause", True),
        _ctx(last_change_at=NOW - timedelta(hours=1)),
        now=NOW,
    )
    assert reason is not None
    assert "쿨다운" in reason


def test_no_prior_change_no_cooldown():
    reason = gate.check(_bid_proposal("bid_up", 210), _ctx(current_bid=190, last_change_at=None), now=NOW)
    assert reason is None


# ── 일일 변경 건수 상한 (전 액션 유형 공통) ──────────────────────────────


def test_daily_change_cap_reached_blocked():
    reason = gate.check(
        _bid_proposal("bid_up", 210), _ctx(current_bid=190, changes_today_count=3), now=NOW,
    )
    assert reason is not None
    assert "일일 변경" in reason


def test_daily_change_cap_not_reached_passes():
    reason = gate.check(
        _bid_proposal("bid_up", 210), _ctx(current_bid=190, changes_today_count=2), now=NOW,
    )
    assert reason is None


# ── 정지·재개(pause/resume) ──────────────────────────────────────────────


def test_pause_passes():
    assert gate.check(_lock_proposal("pause", True), _ctx(), now=NOW) is None


def test_resume_passes():
    assert gate.check(_lock_proposal("resume", False), _ctx(), now=NOW) is None


def test_lock_target_lock_missing_blocked():
    reason = gate.check(_lock_proposal("pause", None), _ctx(), now=NOW)
    assert reason is not None
    assert "target_lock" in reason


def test_lock_target_lock_non_bool_blocked():
    reason = gate.check(
        {"proposal_type": "pause", "target_bid": None, "target_lock": "true"}, _ctx(), now=NOW,
    )
    assert reason is not None


# ── 방향 불일치 (codex P2, 2026-07-10) ───────────────────────────────────


def test_bid_down_with_target_bid_above_current_blocked():
    # 구조 결함/stale 행 방어: proposal_type='bid_down'인데 target_bid가 오히려 인상 방향
    reason = gate.check(_bid_proposal("bid_down", 210), _ctx(current_bid=190), now=NOW)
    assert reason is not None
    assert "방향" in reason


def test_bid_up_with_target_bid_below_current_blocked():
    reason = gate.check(_bid_proposal("bid_up", 170), _ctx(current_bid=190), now=NOW)
    assert reason is not None
    assert "방향" in reason


def test_growth_bid_up_with_target_bid_below_current_blocked():
    reason = gate.check(_bid_proposal("growth_bid_up", 170), _ctx(current_bid=190), now=NOW)
    assert reason is not None
    assert "방향" in reason


def test_bid_up_with_target_bid_equal_current_blocked_as_no_direction():
    # hold 방향은 proposal_writer가 애초에 제안을 안 만들지만(direction='hold'는 제안 자체가
    # 없음), 구조 결함 방어 차원에서 방향성이 없는 등가도 차단
    reason = gate.check(_bid_proposal("bid_up", 190), _ctx(current_bid=190), now=NOW)
    assert reason is not None
    assert "방향" in reason


def test_pause_with_target_lock_false_blocked():
    # proposal_type='pause'인데 target_lock=False(재개 방향) — 방향 불일치
    reason = gate.check(_lock_proposal("pause", False), _ctx(), now=NOW)
    assert reason is not None
    assert "방향" in reason


def test_resume_with_target_lock_true_blocked():
    reason = gate.check(_lock_proposal("resume", True), _ctx(), now=NOW)
    assert reason is not None
    assert "방향" in reason


# ── 지원하지 않는 유형 ────────────────────────────────────────────────────


def test_unsupported_proposal_type_blocked():
    reason = gate.check(
        {"proposal_type": "negative_keyword", "target_bid": None, "target_lock": None}, _ctx(), now=NOW,
    )
    assert reason is not None
    assert "지원하지 않는" in reason


# ── budget_up/budget_down (P2, D-NAO-42-f) ───────────────────────────────


def test_budget_up_within_100pct_cap_passes():
    # 100,000 -> 150,000은 +50%(상한 100% 이내), BEP·스톱로스도 통과
    reason = gate.check(
        _budget_proposal("budget_up", 150_000), _ctx(current_budget=100_000), now=NOW,
    )
    assert reason is None


def test_budget_down_free_pass_even_with_bep_and_stoploss_violations():
    # 감액은 자유(⑥) — BEP 미달·무전환 지출이 있어도 방향+클램프만 통과하면 됨
    reason = gate.check(
        _budget_proposal("budget_down", 50_000),
        _ctx(current_budget=100_000, roas_corrected=10.0, target_roas=150.0, unconverted_spend=999_999),
        now=NOW,
    )
    assert reason is None


def test_budget_target_budget_missing_blocked():
    reason = gate.check(
        _budget_proposal("budget_up", None), _ctx(current_budget=100_000), now=NOW,
    )
    assert reason is not None
    assert "target_budget" in reason


def test_budget_target_budget_zero_blocked():
    reason = gate.check(
        _budget_proposal("budget_up", 0), _ctx(current_budget=100_000), now=NOW,
    )
    assert reason is not None
    assert "target_budget" in reason


def test_budget_target_budget_negative_blocked():
    reason = gate.check(
        _budget_proposal("budget_up", -1000), _ctx(current_budget=100_000), now=NOW,
    )
    assert reason is not None
    assert "target_budget" in reason


def test_budget_target_budget_non_int_blocked():
    reason = gate.check(
        _budget_proposal("budget_up", 100_000.5), _ctx(current_budget=100_000), now=NOW,
    )
    assert reason is not None
    assert "target_budget" in reason


def test_budget_current_budget_missing_blocked_fail_closed():
    reason = gate.check(
        _budget_proposal("budget_up", 150_000), _ctx(current_budget=None), now=NOW,
    )
    assert reason is not None
    assert "current_budget" in reason


def test_budget_up_with_target_below_current_blocked_direction():
    reason = gate.check(
        _budget_proposal("budget_up", 90_000), _ctx(current_budget=100_000), now=NOW,
    )
    assert reason is not None
    assert "방향" in reason


def test_budget_up_with_target_equal_current_blocked_direction():
    # hold 방향(증액 아님)도 구조 결함 방어 차원에서 방향 불일치로 차단(bid와 동형)
    reason = gate.check(
        _budget_proposal("budget_up", 100_000), _ctx(current_budget=100_000), now=NOW,
    )
    assert reason is not None
    assert "방향" in reason


def test_budget_down_with_target_above_current_blocked_direction():
    reason = gate.check(
        _budget_proposal("budget_down", 110_000), _ctx(current_budget=100_000), now=NOW,
    )
    assert reason is not None
    assert "방향" in reason


def test_budget_down_with_target_equal_current_blocked_direction():
    reason = gate.check(
        _budget_proposal("budget_down", 100_000), _ctx(current_budget=100_000), now=NOW,
    )
    assert reason is not None
    assert "방향" in reason


def test_budget_up_exactly_at_100pct_cap_passes():
    # 100,000 -> 200,000은 정확히 +100%(상한 경계, 캠페인당 최대 2배)
    reason = gate.check(
        _budget_proposal("budget_up", 200_000), _ctx(current_budget=100_000), now=NOW,
    )
    assert reason is None


def test_budget_up_over_100pct_cap_blocked():
    # 100,000 -> 200,010은 +100%를 아주 조금 초과
    reason = gate.check(
        _budget_proposal("budget_up", 200_010), _ctx(current_budget=100_000), now=NOW,
    )
    assert reason is not None
    assert "100%" in reason or "증액폭" in reason


def test_budget_down_not_subject_to_100pct_cap():
    # 감액 방향이라 100% 캡 자체가 적용 안 됨(감액은 상한 없이 자유)
    reason = gate.check(
        _budget_proposal("budget_down", 1_000), _ctx(current_budget=100_000), now=NOW,
    )
    assert reason is None


def test_budget_up_stop_loss_zero_conversion_blocked():
    # 스톱로스(⑤): 무전환 지출이 조금이라도 있으면 절대금액과 무관하게 차단(제로 톨러런스)
    reason = gate.check(
        _budget_proposal("budget_up", 150_000),
        _ctx(current_budget=100_000, unconverted_spend=1),
        now=NOW,
    )
    assert reason is not None
    assert "스톱로스" in reason


def test_budget_up_no_unconverted_spend_passes_stop_loss_check():
    reason = gate.check(
        _budget_proposal("budget_up", 150_000),
        _ctx(current_budget=100_000, unconverted_spend=0),
        now=NOW,
    )
    assert reason is None


def test_budget_down_not_subject_to_stop_loss():
    reason = gate.check(
        _budget_proposal("budget_down", 50_000),
        _ctx(current_budget=100_000, unconverted_spend=999_999),
        now=NOW,
    )
    assert reason is None


def test_budget_up_bep_below_target_blocked():
    reason = gate.check(
        _budget_proposal("budget_up", 150_000),
        _ctx(current_budget=100_000, roas_corrected=100.0, target_roas=150.0),
        now=NOW,
    )
    assert reason is not None
    assert "BEP" in reason


# ── Fix 3(codex P1, D-NAO-42-f④): BEP 증거 없음(None) fail-closed(budget_up만) ──


def test_budget_up_bep_roas_corrected_none_blocked_fail_closed():
    """_check_bid는 증거 없으면 fail-open(그냥 통과)이지만, _check_budget은 의도적으로
    다르다 — 예산은 더 무거운 레버라 근거값을 못 구했으면 증액하지 않는다."""
    reason = gate.check(
        _budget_proposal("budget_up", 150_000),
        _ctx(current_budget=100_000, roas_corrected=None, target_roas=150.0),
        now=NOW,
    )
    assert reason is not None
    assert "BEP" in reason


def test_budget_up_bep_target_roas_none_blocked_fail_closed():
    reason = gate.check(
        _budget_proposal("budget_up", 150_000),
        _ctx(current_budget=100_000, roas_corrected=250.0, target_roas=None),
        now=NOW,
    )
    assert reason is not None
    assert "BEP" in reason


def test_budget_up_bep_both_present_and_sufficient_passes():
    reason = gate.check(
        _budget_proposal("budget_up", 150_000),
        _ctx(current_budget=100_000, roas_corrected=250.0, target_roas=150.0),
        now=NOW,
    )
    assert reason is None


def test_budget_down_not_subject_to_bep_evidence_check():
    """budget_down은 애초에 BEP 검사 자체가 면제 — roas_corrected/target_roas가 둘 다
    None이어도 통과해야 한다(Fix 3은 budget_up 경로에만 적용)."""
    reason = gate.check(
        _budget_proposal("budget_down", 50_000),
        _ctx(current_budget=100_000, roas_corrected=None, target_roas=None),
        now=NOW,
    )
    assert reason is None


# ── Fix 4(codex P1, D-NAO-42-f③): current_budget<=0 fail-closed(budget_up만) ──


def test_budget_up_current_budget_zero_blocked_fail_closed():
    """current_budget=0(미설정/무제한)에서는 "+100%"가 정의 불가 — 과거엔
    `if current_budget > 0:` 가드가 이 케이스를 조용히 건너뛰어 +100%캡이 무력화됐었다."""
    reason = gate.check(
        _budget_proposal("budget_up", 150_000), _ctx(current_budget=0), now=NOW,
    )
    assert reason is not None
    assert "0" in reason


def test_budget_down_current_budget_zero_not_specially_blocked():
    """Fix 4는 budget_up 전용 — budget_down은 current_budget=0이어도 방향 검사만
    적용된다(0에서 더 내릴 target_budget은 없으므로 방향 불일치로 자연히 막힘)."""
    reason = gate.check(
        _budget_proposal("budget_down", 50_000), _ctx(current_budget=0), now=NOW,
    )
    assert reason is not None
    assert "방향" in reason  # "+100% 정의 불가" 사유가 아니라 방향 불일치로 막힘


def test_budget_up_bep_at_or_above_target_passes():
    reason = gate.check(
        _budget_proposal("budget_up", 150_000),
        _ctx(current_budget=100_000, roas_corrected=150.0, target_roas=150.0),
        now=NOW,
    )
    assert reason is None


def test_budget_down_not_subject_to_bep_check():
    reason = gate.check(
        _budget_proposal("budget_down", 50_000),
        _ctx(current_budget=100_000, roas_corrected=10.0, target_roas=150.0),
        now=NOW,
    )
    assert reason is None


def test_budget_up_cooldown_applies():
    reason = gate.check(
        _budget_proposal("budget_up", 150_000),
        _ctx(current_budget=100_000, last_change_at=NOW - timedelta(hours=gate._COOLDOWN_HOURS / 2)),
        now=NOW,
    )
    assert reason is not None
    assert "쿨다운" in reason


def test_budget_down_cooldown_applies_too():
    # 쿨다운·일일상한은 전 유형 공통(감액도 예외 아님) — §5-C step7
    reason = gate.check(
        _budget_proposal("budget_down", 50_000),
        _ctx(current_budget=100_000, last_change_at=NOW - timedelta(hours=gate._COOLDOWN_HOURS / 2)),
        now=NOW,
    )
    assert reason is not None
    assert "쿨다운" in reason


def test_budget_up_daily_change_cap_applies():
    reason = gate.check(
        _budget_proposal("budget_up", 150_000),
        _ctx(current_budget=100_000, changes_today_count=3),
        now=NOW,
    )
    assert reason is not None
    assert "일일 변경" in reason


# ── DL3(D-NAO-65): bid_down 일일상한 면제 ("쭉 낮추다가") ───────────────────
# 안전방향(하향=노출↓=지출↓)만 _MAX_DAILY_CHANGES에서 면제. 쿨다운 2h·클램프·방향검증·
# BEP·스톱로스는 그대로. bid_up·growth_bid_up·budget·pause/resume은 상한 3 유지.


def test_bid_down_exempt_from_daily_cap_over_limit_passes():
    # 4번째 하향(changes_today_count=3)이어도 일일상한에 안 걸림 — 쿨다운 지난 상태
    reason = gate.check(
        _bid_proposal("bid_down", 170),
        _ctx(current_bid=190, changes_today_count=3, last_change_at=None),
        now=NOW,
    )
    assert reason is None


def test_bid_down_exempt_from_daily_cap_even_far_over_limit_passes():
    # 상한을 크게 초과(8번째)해도 면제 — "쭉 낮추다가" 유닛당 하루 ~8 스텝
    reason = gate.check(
        _bid_proposal("bid_down", 170),
        _ctx(current_bid=190, changes_today_count=7, last_change_at=None),
        now=NOW,
    )
    assert reason is None


def test_bid_down_exempt_from_cap_but_cooldown_still_blocks():
    # 일일상한은 면제되지만 쿨다운 2h는 여전히 유효 — 두 방어를 분리 검증
    reason = gate.check(
        _bid_proposal("bid_down", 170),
        _ctx(
            current_bid=190,
            changes_today_count=3,
            last_change_at=NOW - timedelta(hours=gate._COOLDOWN_HOURS / 2),
        ),
        now=NOW,
    )
    assert reason is not None
    assert "쿨다운" in reason


def test_bid_down_over_cap_still_enforces_direction_stale_row():
    # 방향 불일치 stale 행(bid_down인데 target≥current)은 일일상한 면제와 무관하게
    # 여전히 fail-closed 차단 — 면제가 방어망을 우회시키지 않음
    reason = gate.check(
        _bid_proposal("bid_down", 210),
        _ctx(current_bid=190, changes_today_count=3, last_change_at=None),
        now=NOW,
    )
    assert reason is not None
    assert "방향" in reason


def test_bid_up_not_exempt_from_daily_cap_still_blocked():
    # 회귀: bid_up은 면제 아님 — 상한 3에서 여전히 차단
    reason = gate.check(
        _bid_proposal("bid_up", 210),
        _ctx(current_bid=190, changes_today_count=3, last_change_at=None),
        now=NOW,
    )
    assert reason is not None
    assert "일일 변경" in reason


def test_growth_bid_up_not_exempt_from_daily_cap_still_blocked():
    # 회귀: growth_bid_up(상향 계열)도 면제 아님 — 면제는 bid_down에만
    reason = gate.check(
        _bid_proposal("growth_bid_up", 210),
        _ctx(current_bid=190, changes_today_count=3, last_change_at=None),
        now=NOW,
    )
    assert reason is not None
    assert "일일 변경" in reason


def test_pause_not_exempt_from_daily_cap_still_blocked():
    # 회귀: pause는 면제 아님 — 상한 3에서 여전히 차단
    reason = gate.check(
        _lock_proposal("pause", True),
        _ctx(changes_today_count=3, last_change_at=None),
        now=NOW,
    )
    assert reason is not None
    assert "일일 변경" in reason


def test_budget_down_not_exempt_from_daily_cap_still_blocked():
    # 회귀: budget_down은 하향이지만 면제 아님 — 면제 대상은 bid_down 계열뿐
    reason = gate.check(
        _budget_proposal("budget_down", 50_000),
        _ctx(current_budget=100_000, changes_today_count=3, last_change_at=None),
        now=NOW,
    )
    assert reason is not None
    assert "일일 변경" in reason
