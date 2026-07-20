# test_naver_rank_servo.py — IU-R R1 쇼핑검색 폐루프 순위 서보 SA 단위 테스트(PLAN §2 R1·§3-2).
#   순수 함수 decide_servo_step의 목표순위(ceil−1)·최상단 가드·데드밴드 관망·서보 캡·경제성
#   상한 캡·콜드스타트/response_prior 스텝·반올림 순서·fail-closed 3종을 못박는다.
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.naver_ad import bid_simulator, rank_servo


def _call(weighted_rank, current_bid, *, imp_sum=45, economic_ceiling=5000, response_prior=None):
    return rank_servo.decide_servo_step(
        weighted_rank, current_bid, imp_sum=imp_sum,
        economic_ceiling=economic_ceiling, response_prior=response_prior,
    )


# ── 목표 순위 = ceil(현재)−1(codex 지적2, floor 아님) ──
def test_target_rank_is_ceil_minus_one():
    out = _call(Decimal("4.9"), 1000)
    assert out["target_rank"] == 4  # ceil(4.9)=5 → 4위(한 단 위)
    assert out["target_bid"] is not None


# ── 콜드스타트(response_prior=None) 기본 스텝 = +15% 클램프 ──
def test_cold_start_default_step_is_15pct():
    out = _call(Decimal("4.9"), 1000, economic_ceiling=5000)
    assert out["target_bid"] == 1150  # 1000×1.15 → 10원 내림, 캡·상한 내


# ── response_prior 기울기로 ±15% 초과 스텝(차등) ──
def test_response_prior_step_exceeds_15pct():
    # delta=4.9−4=0.9, increment=500×0.9=450 → 1450원(+45% > ±15%), 캡 1500·상한 5000 내.
    out = _call(Decimal("4.9"), 1000, economic_ceiling=5000, response_prior=Decimal("500"))
    assert out["target_bid"] == 1450
    change_pct = (out["target_bid"] - 1000) / 1000
    assert change_pct > 0.15  # ±15% 면제의 실효(경제성 상한·캡이 대체 상한)


# ── 서보 절대 스텝 캡(±50%) — 프라이어가 폭주해도 current×1.5로 클램프 ──
def test_servo_absolute_step_cap():
    out = _call(Decimal("4.9"), 1000, economic_ceiling=100_000, response_prior=Decimal("100000"))
    assert out["target_bid"] == 1500  # 1000×1.5 캡


# ── 경제성 상한이 스텝을 캡(순위 근거로도 상한 초과 불가) ──
def test_economic_ceiling_caps_step():
    out = _call(Decimal("4.9"), 1000, economic_ceiling=1080, response_prior=Decimal("500"))
    assert out["target_bid"] == 1080  # raw 1450 → 경제성 상한 1080으로 캡


# ── 경제성 상한이 현재가 이하 → 스텝 사라짐(hold) ──
def test_economic_ceiling_below_current_holds():
    out = _call(Decimal("4.9"), 1000, economic_ceiling=900)
    assert out["target_bid"] is None
    assert "유효 스텝 없음" in out["step_reason"]


# ── 10원 내림 반올림 순서 ──
def test_ten_won_floor_rounding():
    # increment=333×0.9=299.7 → raw 1299.7 → 10원 내림 1290.
    out = _call(Decimal("4.9"), 1000, economic_ceiling=5000, response_prior=Decimal("333"))
    assert out["target_bid"] == 1290


# ── 데드밴드 관망(진동 차단, codex 지적3·4) ──
@pytest.mark.parametrize("wr", ["4.1", "4.3"])  # target=4, |wr−4|≤0.3
def test_deadband_hold(wr):
    out = _call(Decimal(wr), 1000)
    assert out["target_bid"] is None
    assert "데드밴드" in out["step_reason"]
    assert out["target_rank"] == 4


def test_just_outside_deadband_steps():
    out = _call(Decimal("4.31"), 1000)  # |4.31−4|=0.31 > 0.3 → 스텝
    assert out["target_bid"] is not None
    assert out["target_rank"] == 4


# ── 최상단 가드(codex P1-4) — 1위권은 더 올릴 순위 없음 ──
@pytest.mark.parametrize("wr", ["1.0", "1.2", "1.3"])  # ≤ 1+deadband
def test_top_of_page_converged_hold(wr):
    out = _call(Decimal(wr), 1000)
    assert out["target_bid"] is None
    assert "최상단" in out["step_reason"]
    assert out["target_rank"] == 1


def test_just_below_top_steps_toward_rank_1():
    out = _call(Decimal("1.5"), 1000)  # ceil=2 → target=1, |1.5−1|=0.5>0.3 → 스텝
    assert out["target_bid"] is not None
    assert out["target_rank"] == 1


# ── fail-closed 3종(codex 지적5) ──
def test_fail_closed_rank_none():
    out = _call(None, 1000)
    assert out["target_bid"] is None and "None" in out["step_reason"]


def test_fail_closed_low_sample():
    out = _call(Decimal("4.9"), 1000, imp_sum=29)
    assert out["target_bid"] is None and "표본" in out["step_reason"]


def test_fail_closed_zero_ceiling():
    out = _call(Decimal("4.9"), 1000, economic_ceiling=0)
    assert out["target_bid"] is None and "economic_ceiling" in out["step_reason"]


def test_unresponsive_always_false_in_r1():
    # 무영속 재구성(무반응 N스텝)은 R3 harness 몫 — 순수 함수는 항상 False(계약 형태 유지).
    assert _call(Decimal("4.9"), 1000)["unresponsive"] is False


# ── codex P2-4: pooled_rpc는 하위 clk=0이어도 상위 prior로 양수 ceiling을 만들 수 있다 —
#    핫셋 게이트(clk≥10)가 실무 방어이나, SA 자체는 양수 상한이면 스텝한다(봉인은 harness 테스트).
def test_pooled_rpc_positive_from_prior_even_when_unit_clk_zero():
    rpc = bid_simulator.pooled_rpc(
        {"clk": 0, "conv_amt": 0},
        {"clk": 0, "conv_amt": 0},
        {"clk": 100, "conv_amt": 300_000},  # 캠페인 prior에 매출
        {"clk": 100, "conv_amt": 300_000},
    )
    assert rpc > 0  # 하위 clk=0이어도 상위 prior로 양수
    ceiling = bid_simulator.affordable_ceiling(rpc, Decimal("2.0"))
    assert ceiling > 0
    # 양수 상한이면 서보 SA는 스텝을 낸다(핫셋 clk 게이트가 실무 방어 — harness 테스트에서 봉인).
    out = _call(Decimal("4.9"), 1000, economic_ceiling=ceiling)
    assert out["target_bid"] is not None
