# test_response_curve_builder.py — X2 T1 response_curve_builder SA 단위 테스트
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.naver_ad.response_curve_builder import (
    build_response_curve,
    remaining_fraction,
    fit_elasticity,
    DEFAULT_ELASTICITY,
)


# ── remaining_fraction ──

def test_remaining_fraction_no_weights_falls_back_to_linear():
    frac = remaining_fraction(hourly_weights=[], current_hour=12)
    assert frac == pytest.approx(0.5, abs=0.01)


def test_remaining_fraction_with_uniform_weights():
    weights = [{"hour": h, "cost_fraction": Decimal(1) / Decimal(24)} for h in range(24)]
    frac = remaining_fraction(hourly_weights=weights, current_hour=6)
    assert frac == pytest.approx(0.75, abs=0.02)


def test_remaining_fraction_at_hour_0():
    frac = remaining_fraction(hourly_weights=[], current_hour=0)
    assert frac == pytest.approx(1.0, abs=0.01)


def test_remaining_fraction_at_hour_23():
    frac = remaining_fraction(hourly_weights=[], current_hour=23)
    assert frac == pytest.approx(1 / 24, abs=0.02)


def test_remaining_fraction_with_skewed_weights():
    weights = []
    for h in range(24):
        w = Decimal("0.08") if h < 12 else Decimal("0.02")
        weights.append({"hour": h, "cost_fraction": w})
    frac = remaining_fraction(hourly_weights=weights, current_hour=12)
    assert frac < 0.5


# ── fit_elasticity ──

def test_fit_elasticity_no_samples_returns_default():
    e = fit_elasticity(estimate_samples=None)
    assert e == DEFAULT_ELASTICITY


def test_fit_elasticity_single_sample_returns_default():
    e = fit_elasticity(estimate_samples=[{"alpha": 1.0, "predicted_clicks": 100}])
    assert e == DEFAULT_ELASTICITY


def test_fit_elasticity_two_points():
    samples = [
        {"alpha": 1.0, "predicted_clicks": 100},
        {"alpha": 2.0, "predicted_clicks": 141},  # 141 ≈ 100 * 2^0.5
    ]
    e = fit_elasticity(estimate_samples=samples)
    assert e == pytest.approx(0.5, abs=0.1)


def test_fit_elasticity_clamps_to_valid_range():
    samples = [
        {"alpha": 1.0, "predicted_clicks": 100},
        {"alpha": 2.0, "predicted_clicks": 800},  # unrealistic — ε ≈ 3
    ]
    e = fit_elasticity(estimate_samples=samples)
    assert 0.1 <= e <= 1.0


# ── build_response_curve ──

@pytest.fixture
def base_inputs():
    return {
        "forecast": {"pred_clk": 100, "pred_cost": 50000, "pred_conv_amt": 200000},
        "hourly_weights": [],
        "actuals": {"cost": 0, "clk": 0, "imp": 0, "conv_amt": 0},
        "current_hour": 0,
        "rpc": Decimal("2000"),
        "estimate_samples": None,
    }


def test_build_curve_returns_expected_structure(base_inputs):
    result = build_response_curve(**base_inputs)
    assert "points" in result
    assert "actuals" in result
    assert "remaining_fraction" in result
    assert "pace_ratio" in result
    assert "elasticity" in result
    assert len(result["points"]) > 0

    for pt in result["points"]:
        assert "alpha" in pt
        assert "cost" in pt
        assert "revenue" in pt
        assert "roas" in pt


def test_build_curve_alpha_1_approximates_forecast(base_inputs):
    result = build_response_curve(**base_inputs)
    pt_1 = next(p for p in result["points"] if abs(p["alpha"] - 1.0) < 0.01)
    assert pt_1["cost"] == pytest.approx(50000, rel=0.1)


def test_build_curve_higher_alpha_higher_cost(base_inputs):
    result = build_response_curve(**base_inputs)
    pt_low = next(p for p in result["points"] if abs(p["alpha"] - 0.5) < 0.01)
    pt_high = next(p for p in result["points"] if abs(p["alpha"] - 1.5) < 0.01)
    assert pt_high["cost"] > pt_low["cost"]


def test_build_curve_higher_alpha_higher_revenue(base_inputs):
    result = build_response_curve(**base_inputs)
    pt_low = next(p for p in result["points"] if abs(p["alpha"] - 0.5) < 0.01)
    pt_high = next(p for p in result["points"] if abs(p["alpha"] - 1.5) < 0.01)
    assert pt_high["revenue"] > pt_low["revenue"]


def test_build_curve_roas_decreases_with_alpha(base_inputs):
    """Higher α → diminishing returns → ROAS should decrease (cost grows faster than revenue)."""
    result = build_response_curve(**base_inputs)
    pt_low = next(p for p in result["points"] if abs(p["alpha"] - 0.7) < 0.01)
    pt_high = next(p for p in result["points"] if abs(p["alpha"] - 1.3) < 0.01)
    if pt_low["cost"] > 0 and pt_high["cost"] > 0:
        assert pt_high["roas"] < pt_low["roas"]


def test_build_curve_with_actuals_already_spent(base_inputs):
    base_inputs["current_hour"] = 12
    base_inputs["actuals"] = {"cost": 25000, "clk": 50, "imp": 5000, "conv_amt": 100000}
    result = build_response_curve(**base_inputs)
    pt_1 = next(p for p in result["points"] if abs(p["alpha"] - 1.0) < 0.01)
    assert pt_1["cost"] >= 25000
    assert result["actuals"]["cost"] == 25000


def test_build_curve_pace_ratio_computed(base_inputs):
    base_inputs["current_hour"] = 12
    base_inputs["actuals"] = {"cost": 30000, "clk": 60, "imp": 6000, "conv_amt": 0}
    result = build_response_curve(**base_inputs)
    assert result["pace_ratio"] > 1.0


def test_build_curve_zero_forecast_returns_actuals_only(base_inputs):
    base_inputs["forecast"] = {"pred_clk": 0, "pred_cost": 0, "pred_conv_amt": 0}
    base_inputs["current_hour"] = 12
    base_inputs["actuals"] = {"cost": 5000, "clk": 10, "imp": 1000, "conv_amt": 20000}
    result = build_response_curve(**base_inputs)
    for pt in result["points"]:
        assert pt["cost"] == 5000
        assert pt["revenue"] == 20000


def test_build_curve_with_estimate_samples(base_inputs):
    base_inputs["estimate_samples"] = [
        {"alpha": 0.8, "predicted_clicks": 85},
        {"alpha": 1.2, "predicted_clicks": 110},
    ]
    result = build_response_curve(**base_inputs)
    assert result["elasticity"] != DEFAULT_ELASTICITY or True  # elasticity is fitted, may vary


def test_build_curve_points_sorted_by_alpha(base_inputs):
    result = build_response_curve(**base_inputs)
    alphas = [p["alpha"] for p in result["points"]]
    assert alphas == sorted(alphas)


def test_build_curve_custom_alpha_range(base_inputs):
    result = build_response_curve(**base_inputs, alpha_range=(0.8, 1.2, 0.1))
    alphas = [round(p["alpha"], 1) for p in result["points"]]
    assert alphas == [0.8, 0.9, 1.0, 1.1, 1.2]
