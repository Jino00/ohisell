# response_curve_builder.py — response_curve_builder SA (X2 T1, D-NAO-34)
# 역할(SA): 캠페인 단위 "입찰배수 α → 오늘 예상 비용·매출" 응답곡선 생성.
#   순수 함수 — DB·API 호출 없음(harness가 모든 원료를 pre-compute해 전달, 원칙18-8).
#   원료 = forecast(일 예측) × hourly_pattern(시간대 분포) × 견적 API(스팟 보정, optional)
#   × hourly_snapshot(당일 실적 누적). pacing_controller(T2)가 이 곡선에서 αB·αC를 이분법 탐색.
from __future__ import annotations

import math
from decimal import Decimal

DEFAULT_ELASTICITY = 0.5
_ELASTICITY_MIN = 0.1
_ELASTICITY_MAX = 1.0


def remaining_fraction(*, hourly_weights: list[dict], current_hour: int) -> float:
    """current_hour 이후(포함하지 않음) 남은 비용 비율.

    hourly_weights: [{hour, cost_fraction}] — hourly_pattern.expected_cost_fraction의 원료.
    비어있으면 균등 분포(linear)로 폴백: (24 - current_hour) / 24.
    current_hour=0이면 하루 전체 남음(1.0), 23이면 마지막 1시간(1/24).
    """
    if not hourly_weights:
        return (24 - current_hour) / 24

    by_hour = {w["hour"]: float(w["cost_fraction"]) for w in hourly_weights}
    total = sum(by_hour.values())
    if total <= 0:
        return (24 - current_hour) / 24

    remaining = sum(v for h, v in by_hour.items() if h >= current_hour)
    return remaining / total


def fit_elasticity(*, estimate_samples: list[dict] | None) -> float:
    """견적 API 응답(2+ α-clicks 쌍)으로 로그선형 탄성 추정.

    log(clicks) = a + ε·log(α) → ε = Δlog(clicks)/Δlog(α).
    2점 미만이면 DEFAULT_ELASTICITY 반환. 결과는 [_ELASTICITY_MIN, _ELASTICITY_MAX] 클램프.
    """
    if not estimate_samples or len(estimate_samples) < 2:
        return DEFAULT_ELASTICITY

    valid = [(s["alpha"], s["predicted_clicks"]) for s in estimate_samples
             if s["alpha"] > 0 and s["predicted_clicks"] > 0]
    if len(valid) < 2:
        return DEFAULT_ELASTICITY

    valid.sort(key=lambda x: x[0])
    log_alphas = [math.log(a) for a, _ in valid]
    log_clicks = [math.log(c) for _, c in valid]

    n = len(valid)
    mean_la = sum(log_alphas) / n
    mean_lc = sum(log_clicks) / n
    num = sum((la - mean_la) * (lc - mean_lc) for la, lc in zip(log_alphas, log_clicks))
    den = sum((la - mean_la) ** 2 for la in log_alphas)

    if den < 1e-12:
        return DEFAULT_ELASTICITY

    epsilon = num / den
    return max(_ELASTICITY_MIN, min(_ELASTICITY_MAX, epsilon))


def build_response_curve(
    *,
    forecast: dict,
    hourly_weights: list[dict],
    actuals: dict,
    current_hour: int,
    rpc: Decimal,
    estimate_samples: list[dict] | None = None,
    alpha_range: tuple[float, float, float] = (0.5, 1.5, 0.1),
) -> dict:
    """캠페인 당일 응답곡선: α → (cost, revenue, ROAS).

    forecast: {pred_clk, pred_cost, pred_conv_amt} — NaverForecastDaily 오늘 예측.
    hourly_weights: [{hour, cost_fraction}] — hourly_pattern 원료.
    actuals: {cost, clk, imp, conv_amt} — hourly_snapshot 당일 누적.
    current_hour: KST 0-23.
    rpc: Decimal — 보정 클릭당매출(pooled_rpc × correction_factor, harness precompute).
    estimate_samples: [{alpha, predicted_clicks}] — 견적 API 스팟 보정(optional).
    alpha_range: (min, max, step) — 곡선 해상도.

    반환: {points, actuals, remaining_fraction, pace_ratio, elasticity}.
    """
    frac_rem = remaining_fraction(hourly_weights=hourly_weights, current_hour=current_hour)
    epsilon = fit_elasticity(estimate_samples=estimate_samples)

    pred_cost = forecast.get("pred_cost", 0)
    pred_clk = forecast.get("pred_clk", 0)
    pred_conv_amt = forecast.get("pred_conv_amt", 0)

    cost_so_far = actuals.get("cost", 0)
    clk_so_far = actuals.get("clk", 0)
    conv_amt_so_far = actuals.get("conv_amt", 0)
    rpc_float = float(rpc) if rpc else 0.0
    revenue_so_far = int(clk_so_far * rpc_float) if rpc_float > 0 else conv_amt_so_far

    frac_elapsed = 1.0 - frac_rem
    expected_so_far = pred_cost * frac_elapsed if frac_elapsed > 0 else 0

    if expected_so_far > 0 and cost_so_far > 0:
        pace_ratio = cost_so_far / expected_so_far
    elif cost_so_far > 0 and expected_so_far == 0:
        pace_ratio = 2.0
    else:
        pace_ratio = 1.0

    base_remaining_cost = max(0, pred_cost * frac_rem)
    base_remaining_clk = max(0, pred_clk * frac_rem)

    alpha_min, alpha_max, alpha_step = alpha_range
    points = []
    alpha = alpha_min
    while alpha <= alpha_max + alpha_step * 0.01:
        if pred_cost <= 0 and pred_clk <= 0:
            total_cost = cost_so_far
            total_revenue = int(clk_so_far * rpc_float) if rpc_float > 0 else conv_amt_so_far
        else:
            scaled_remaining_clk = base_remaining_clk * (alpha ** epsilon)
            scaled_remaining_cost = base_remaining_cost * (alpha ** (1 + epsilon))

            total_cost = int(cost_so_far + scaled_remaining_cost)
            remaining_revenue = int(scaled_remaining_clk * rpc_float)
            total_revenue = revenue_so_far + remaining_revenue

        roas = round(total_revenue / total_cost, 2) if total_cost > 0 else 0.0

        points.append({
            "alpha": round(alpha, 2),
            "cost": total_cost,
            "revenue": total_revenue,
            "roas": roas,
        })
        alpha += alpha_step

    return {
        "points": points,
        "actuals": {
            "cost": cost_so_far,
            "clk": clk_so_far,
            "revenue": revenue_so_far,
        },
        "remaining_fraction": round(frac_rem, 4),
        "pace_ratio": round(pace_ratio, 4),
        "elasticity": round(epsilon, 4),
    }
