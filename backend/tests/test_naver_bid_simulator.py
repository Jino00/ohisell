# test_naver_bid_simulator.py — P2-S3 T2 bid_simulator 단위테스트
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.naver_ad import bid_simulator as sim
from app.services.naver_ad.account_diagnosis import LOW_CLICK_THRESHOLD


ACCOUNT = {"clk": 100_000, "conv_amt": 500_000_000}  # account RPC = 5000원/클릭


def test_pooled_rpc_high_click_converges_to_raw():
    """클릭이 충분히 많으면(_SHRINK_K 대비) 관측 raw RPC에 수렴."""
    keyword_row = {"clk": 100_000, "conv_amt": 1_000_000_000}  # raw RPC = 10000원
    group_agg = {"clk": 100_000, "conv_amt": 1_000_000_000}
    campaign_agg = {"clk": 100_000, "conv_amt": 1_000_000_000}
    rpc = sim.pooled_rpc(keyword_row, group_agg, campaign_agg, ACCOUNT)
    assert rpc > Decimal("9990")  # raw(10000)에 근접 — prior(5000) 영향 미미


def test_pooled_rpc_low_click_pulled_toward_prior():
    """클릭이 적으면(<LOW_CLICK_THRESHOLD) 관측 raw가 극단이어도 상위 prior 쪽으로 크게 수축."""
    keyword_row = {"clk": 1, "conv_amt": 1_000_000}  # raw RPC = 1,000,000원(극단치, 표본 1건)
    group_agg = {"clk": 0, "conv_amt": 0}  # 그룹도 표본 없음 → 캠페인 prior로
    campaign_agg = {"clk": 0, "conv_amt": 0}  # 캠페인도 표본 없음 → 계정 prior(5000원)로
    rpc = sim.pooled_rpc(keyword_row, group_agg, campaign_agg, ACCOUNT)
    # keyword clk=1 vs _SHRINK_K=10 → prior(5000) 비중이 raw보다 커 극단치가 크게 눌림
    # (1*1,000,000 + 10*5,000) / 11 ≈ 95,455 — raw(1,000,000)의 1/10 수준으로 수축
    assert Decimal("5000") < rpc < Decimal("100000")


def test_pooled_rpc_zero_click_returns_prior_unchanged():
    keyword_row = {"clk": 0, "conv_amt": 0}
    group_agg = {"clk": 0, "conv_amt": 0}
    campaign_agg = {"clk": 0, "conv_amt": 0}
    rpc = sim.pooled_rpc(keyword_row, group_agg, campaign_agg, ACCOUNT)
    assert rpc == Decimal("5000.0000")  # 표본 전무 → 계정 RPC 그대로


def test_affordable_ceiling_basic():
    # rpc=5000, target_roas=5 → ceiling = 1000원
    assert sim.affordable_ceiling(Decimal("5000"), Decimal("5")) == 1000


def test_affordable_ceiling_division_guard_zero_rpc():
    assert sim.affordable_ceiling(Decimal("0"), Decimal("5")) == 0
    assert sim.affordable_ceiling(Decimal("-100"), Decimal("5")) == 0


def test_affordable_ceiling_rounds_down_to_valid_bid_increment():
    """라이브검증(T8, 2026-07-07): 네이버 입찰가는 10원 단위만 유효(그 외 400 거부) —
    임의 정수(예: 1025원)를 그대로 반환하면 estimate/실제 입찰 등록에서 전부 거부당한다."""
    # rpc=5125, target_roas=5 → raw ceiling=1025원 → 10원 단위 내림 = 1020원
    assert sim.affordable_ceiling(Decimal("5125"), Decimal("5")) == 1020
    # rpc=613*5=3065, target_roas=5 → raw ceiling=613원 → 내림 610원
    assert sim.affordable_ceiling(Decimal("3065"), Decimal("5")) == 610


def test_affordable_ceiling_below_min_bid_returns_zero_not_rounded_up():
    """내림 결과가 최소입찰가(70원) 미만이면 70원으로 올리지 않고 0(입찰 근거 없음)을 반환
    — 보수적 상한 원칙 유지(수익성 없는데 억지로 최소가를 추천하지 않음)."""
    # rpc=650, target_roas=10 → raw ceiling=65원 → 10원 단위 내림해도 60원(<70) → 0
    assert sim.affordable_ceiling(Decimal("650"), Decimal("10")) == 0


def test_affordable_ceiling_clamps_to_max_bid():
    # rpc가 극단적으로 커도 100,000원 상한을 넘지 않는다(실측 유효 상한).
    assert sim.affordable_ceiling(Decimal("50_000_000"), Decimal("1")) == 100_000


def test_affordable_ceiling_rejects_invalid_target_roas():
    with pytest.raises(ValueError):
        sim.affordable_ceiling(Decimal("5000"), Decimal("0"))
    with pytest.raises(ValueError):
        sim.affordable_ceiling(Decimal("5000"), Decimal("-1"))


def _base_kwargs(**overrides):
    kwargs = dict(
        keyword_row={"clk": 100_000, "conv_amt": 500_000_000, "bid_amt": 300},  # RPC≈5000
        target_roas=Decimal("5"),  # ceiling ≈ 1000원
        group_agg=ACCOUNT, campaign_agg=ACCOUNT, account_agg=ACCOUNT,
    )
    kwargs.update(overrides)
    return kwargs


def test_simulate_bid_rank_target_binds_when_lower_than_ceiling():
    out = sim.simulate_bid(**_base_kwargs(estimate={"rank_bid": 400, "rank_position": 1, "device": "MOBILE"}))
    assert out["economic_ceiling"] == 1000
    assert out["rank_bid"] == 400
    assert out["recommended_bid"] == 400  # min(1000, 400)
    assert out["basis"] == "rank_target"
    assert out["direction"] == "up"  # 400 > 현재입찰 300
    assert out["capability_flags"]["estimate_ok"] is True


def test_simulate_bid_economic_ceiling_binds_when_lower_than_rank():
    out = sim.simulate_bid(**_base_kwargs(estimate={"rank_bid": 2000, "device": "MOBILE"}))
    assert out["recommended_bid"] == 1000  # min(1000, 2000)
    assert out["basis"] == "economic_ceiling"


def test_simulate_bid_no_estimate_falls_back_to_ceiling_only():
    out = sim.simulate_bid(**_base_kwargs(estimate=None))
    assert out["rank_bid"] is None
    assert out["recommended_bid"] == out["economic_ceiling"]
    assert out["basis"] == "economic_ceiling_only"
    assert out["capability_flags"]["estimate_ok"] is False
    assert out["capability_flags"]["performance_estimate_ok"] is False


def test_simulate_bid_direction_down_and_hold():
    down = sim.simulate_bid(**_base_kwargs(
        keyword_row={"clk": 100_000, "conv_amt": 500_000_000, "bid_amt": 5000},
        estimate={"rank_bid": 2000},
    ))
    assert down["direction"] == "down"  # recommended(1000) < 현재(5000)

    hold = sim.simulate_bid(**_base_kwargs(
        keyword_row={"clk": 100_000, "conv_amt": 500_000_000, "bid_amt": 1000},
        estimate={"rank_bid": 2000},
    ))
    assert hold["direction"] == "hold"  # recommended(1000) == 현재(1000)

    no_current = sim.simulate_bid(**_base_kwargs(
        keyword_row={"clk": 100_000, "conv_amt": 500_000_000, "bid_amt": None},
        estimate={"rank_bid": 2000},
    ))
    assert no_current["direction"] == "hold"  # 현재 입찰가 미상 — 방향 판단 불가


def test_simulate_bid_expected_effect_text_uses_predicted_clicks():
    out = sim.simulate_bid(**_base_kwargs(
        estimate={"rank_bid": 400, "predicted_clicks": 100, "device": "MOBILE"},
    ))
    assert out["capability_flags"]["performance_estimate_ok"] is True
    assert "추정클릭 100" in out["expected_effect_text"]
    assert "전환 아님" in out["expected_effect_text"]  # false precision 방지 문구


def test_simulate_bid_applies_estimate_bias_from_learning_state():
    """Phase 6 학습루프2 — learning_state의 estimate_bias factor가 predicted_revenue
    표시에만 반영되고 recommended_bid/economic_ceiling은 그대로여야 한다."""
    base = sim.simulate_bid(**_base_kwargs(
        estimate={"rank_bid": 400, "predicted_clicks": 100, "device": "MOBILE"},
    ))
    biased = sim.simulate_bid(**_base_kwargs(
        estimate={"rank_bid": 400, "predicted_clicks": 100, "device": "MOBILE"},
        learning_state={"estimate_bias": {"factor": Decimal("2"), "confidence": Decimal("1")}},
    ))
    assert biased["recommended_bid"] == base["recommended_bid"]
    assert biased["economic_ceiling"] == base["economic_ceiling"]
    assert "학습보정계수=2" in biased["expected_effect_text"]
    assert "학습보정계수" not in base["expected_effect_text"]


def test_simulate_bid_ignores_nonpositive_or_missing_bias_factor():
    out_none = sim.simulate_bid(**_base_kwargs(
        estimate={"rank_bid": 400, "predicted_clicks": 100, "device": "MOBILE"},
        learning_state={"estimate_bias": {"factor": None, "confidence": None}},
    ))
    out_zero = sim.simulate_bid(**_base_kwargs(
        estimate={"rank_bid": 400, "predicted_clicks": 100, "device": "MOBILE"},
        learning_state={"estimate_bias": {"factor": Decimal("0"), "confidence": Decimal("1")}},
    ))
    assert "학습보정계수" not in out_none["expected_effect_text"]
    assert "학습보정계수" not in out_zero["expected_effect_text"]


def test_simulate_bid_is_new_or_growth_label_only_no_math_change():
    base = sim.simulate_bid(**_base_kwargs(estimate={"rank_bid": 400}))
    labeled = sim.simulate_bid(**_base_kwargs(estimate={"rank_bid": 400}, is_new_or_growth=True))
    assert base["recommended_bid"] == labeled["recommended_bid"]
    assert base["capability_flags"]["is_new_or_growth"] is False
    assert labeled["capability_flags"]["is_new_or_growth"] is True


def test_simulate_bid_keyword_sample_thin_flag():
    thin = sim.simulate_bid(**_base_kwargs(
        keyword_row={"clk": LOW_CLICK_THRESHOLD - 1, "conv_amt": 1000, "bid_amt": 100},
        estimate={"rank_bid": 400},
    ))
    thick = sim.simulate_bid(**_base_kwargs(estimate={"rank_bid": 400}))  # clk=100_000
    assert thin["capability_flags"]["keyword_sample_thin"] is True
    assert thick["capability_flags"]["keyword_sample_thin"] is False
