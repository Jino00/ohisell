# test_pacing_controller.py — X2 T2 pacing_controller SA 단위 테스트
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.naver_ad.pacing_controller import (
    find_alpha_budget,
    find_alpha_roas,
    compute_pacing_alpha,
    ALPHA_MIN,
    ALPHA_MAX,
)


# ── find_alpha_budget ──

def _make_curve(elasticity=0.5):
    """α → cost 테스트용 곡선(cost_base=50000, 탄성=elasticity)."""
    points = []
    for i in range(11):
        alpha = 0.5 + i * 0.1
        cost = int(50000 * alpha ** (1 + elasticity))
        revenue = int(100000 * alpha ** elasticity)
        roas = round(revenue / cost, 2) if cost > 0 else 0
        points.append({"alpha": round(alpha, 1), "cost": cost, "revenue": revenue, "roas": roas})
    return points


def test_find_alpha_budget_exact_match():
    points = _make_curve()
    budget = points[5]["cost"]  # α=1.0 → 정확 일치
    alpha = find_alpha_budget(points=points, remaining_budget=budget)
    assert abs(alpha - 1.0) < 0.15


def test_find_alpha_budget_tight_budget():
    points = _make_curve()
    alpha = find_alpha_budget(points=points, remaining_budget=10000)
    assert alpha < 1.0


def test_find_alpha_budget_loose_budget():
    points = _make_curve()
    alpha = find_alpha_budget(points=points, remaining_budget=200000)
    assert alpha == ALPHA_MAX


def test_find_alpha_budget_zero_budget():
    points = _make_curve()
    alpha = find_alpha_budget(points=points, remaining_budget=0)
    assert alpha == ALPHA_MIN


def test_find_alpha_budget_no_points_returns_min():
    alpha = find_alpha_budget(points=[], remaining_budget=50000)
    assert alpha == ALPHA_MIN


# ── find_alpha_roas ──

def test_find_alpha_roas_at_exact_target():
    points = _make_curve()
    target_roas = Decimal(str(points[5]["roas"]))  # α=1.0의 ROAS
    alpha = find_alpha_roas(points=points, target_roas=target_roas)
    assert abs(alpha - 1.0) < 0.15


def test_find_alpha_roas_high_target_restricts():
    points = _make_curve()
    alpha = find_alpha_roas(points=points, target_roas=Decimal("10.0"))
    assert alpha < 1.0


def test_find_alpha_roas_low_target_allows_max():
    points = _make_curve()
    alpha = find_alpha_roas(points=points, target_roas=Decimal("0.1"))
    assert alpha == ALPHA_MAX


def test_find_alpha_roas_no_points_returns_min():
    alpha = find_alpha_roas(points=[], target_roas=Decimal("2.0"))
    assert alpha == ALPHA_MIN


# ── compute_pacing_alpha ──

def test_compute_alpha_takes_min_of_budget_and_roas():
    points = _make_curve()
    result = compute_pacing_alpha(
        points=points,
        remaining_budget=200000,  # loose → αB = max
        target_roas=Decimal("10.0"),  # tight → αC < 1.0
    )
    assert result["alpha"] < 1.0
    assert result["binding_constraint"] == "roas"


def test_compute_alpha_budget_binding():
    points = _make_curve()
    result = compute_pacing_alpha(
        points=points,
        remaining_budget=10000,  # tight → αB small
        target_roas=Decimal("0.1"),  # loose → αC = max
    )
    assert result["alpha"] < 1.0
    assert result["binding_constraint"] == "budget"


def test_compute_alpha_both_loose():
    points = _make_curve()
    result = compute_pacing_alpha(
        points=points,
        remaining_budget=500000,
        target_roas=Decimal("0.01"),
    )
    assert result["alpha"] == ALPHA_MAX
    assert result["binding_constraint"] == "none"


def test_compute_alpha_both_tight():
    points = _make_curve()
    result = compute_pacing_alpha(
        points=points,
        remaining_budget=5000,
        target_roas=Decimal("100.0"),
    )
    assert result["alpha"] == ALPHA_MIN


def test_compute_alpha_returns_structure():
    points = _make_curve()
    result = compute_pacing_alpha(
        points=points,
        remaining_budget=50000,
        target_roas=Decimal("2.0"),
    )
    assert "alpha" in result
    assert "alpha_budget" in result
    assert "alpha_roas" in result
    assert "binding_constraint" in result
    assert ALPHA_MIN <= result["alpha"] <= ALPHA_MAX


def test_compute_alpha_empty_points():
    result = compute_pacing_alpha(
        points=[],
        remaining_budget=50000,
        target_roas=Decimal("2.0"),
    )
    assert result["alpha"] == ALPHA_MIN
    assert result["binding_constraint"] == "no_data"
