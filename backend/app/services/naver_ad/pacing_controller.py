# pacing_controller.py — pacing_controller SA (X2 T2, D-NAO-34)
# 역할(SA): response_curve_builder가 산출한 곡선에서 예산충족 배수(αB)와 BEP-ROAS충족
#   배수(αC)를 각각 이분법(1차원 근 찾기)으로 구해 min{αB, αC}를 반환한다.
#   순수 함수 — DB·API 호출 없음(원칙18-8). 어느 제약이 물렸는지 라벨을 함께 반환해
#   콘솔에서 해석 가능하게 한다.
from __future__ import annotations

from decimal import Decimal

ALPHA_MIN = 0.5
ALPHA_MAX = 1.5


def _interpolate_cost(points: list[dict], target_cost: float) -> float:
    """곡선 점들 사이 선형 보간으로 target_cost에 도달하는 α를 찾는다."""
    if not points:
        return ALPHA_MIN

    if target_cost <= 0:
        return ALPHA_MIN

    if target_cost >= points[-1]["cost"]:
        return points[-1]["alpha"]

    if target_cost <= points[0]["cost"]:
        return points[0]["alpha"]

    for i in range(len(points) - 1):
        c0, c1 = points[i]["cost"], points[i + 1]["cost"]
        a0, a1 = points[i]["alpha"], points[i + 1]["alpha"]
        if c0 <= target_cost <= c1:
            if c1 == c0:
                return a0
            frac = (target_cost - c0) / (c1 - c0)
            return a0 + frac * (a1 - a0)

    return points[-1]["alpha"]


def _interpolate_roas(points: list[dict], target_roas: float) -> float:
    """곡선 점들에서 ROAS가 target_roas 이상이 되는 최대 α를 찾는다.
    ROAS는 α가 커질수록 감소(수확체감)하므로 역방향 탐색."""
    if not points:
        return ALPHA_MIN

    for i in range(len(points) - 1, -1, -1):
        if points[i]["roas"] >= target_roas:
            return points[i]["alpha"]

    return ALPHA_MIN


def find_alpha_budget(*, points: list[dict], remaining_budget: int | float) -> float:
    """예산 제약 αB: cost(αB) ≤ remaining_budget인 최대 α."""
    if not points:
        return ALPHA_MIN
    alpha = _interpolate_cost(points, remaining_budget)
    return max(ALPHA_MIN, min(ALPHA_MAX, round(alpha, 2)))


def find_alpha_roas(*, points: list[dict], target_roas: Decimal) -> float:
    """ROAS 제약 αC: ROAS(αC) ≥ target_roas인 최대 α."""
    if not points:
        return ALPHA_MIN
    alpha = _interpolate_roas(points, float(target_roas))
    return max(ALPHA_MIN, min(ALPHA_MAX, round(alpha, 2)))


def compute_pacing_alpha(
    *,
    points: list[dict],
    remaining_budget: int | float,
    target_roas: Decimal,
) -> dict:
    """최종 페이싱 배수 = min{αB, αC} + 어느 제약이 물렸는지 라벨.

    반환: {alpha, alpha_budget, alpha_roas, binding_constraint}.
    binding_constraint: "budget" | "roas" | "none" | "no_data".
    """
    if not points:
        return {
            "alpha": ALPHA_MIN,
            "alpha_budget": ALPHA_MIN,
            "alpha_roas": ALPHA_MIN,
            "binding_constraint": "no_data",
        }

    alpha_b = find_alpha_budget(points=points, remaining_budget=remaining_budget)
    alpha_r = find_alpha_roas(points=points, target_roas=target_roas)
    alpha = min(alpha_b, alpha_r)

    if alpha_b >= ALPHA_MAX and alpha_r >= ALPHA_MAX:
        binding = "none"
    elif alpha_b <= alpha_r:
        binding = "budget"
    else:
        binding = "roas"

    return {
        "alpha": round(alpha, 2),
        "alpha_budget": round(alpha_b, 2),
        "alpha_roas": round(alpha_r, 2),
        "binding_constraint": binding,
    }
