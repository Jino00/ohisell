# gave_score.py — GAVE 페널티 점수 SA (X3 T2, ref 26 ⑤)
# 역할(SA): S = min{(ROAS/BEP)^γ, 1} × 매출.
#   ROAS가 BEP 이상이면 매출 전액 인정, 미달이면 γ에 비례해 감점.
#   γ = 공격성 다이얼 — γ↑ BEP 미달 벌칙 강화, γ=0 벌칙 없음.
#   제안 성적표(proposal_scoreboard)·flight_loop 목적함수로 채택.
#   D-NAO-1·2와 정합: "이익률 = 다이얼 하한".
#   순수함수 — DB/API 접근 없음(원칙 18).
from __future__ import annotations

from decimal import Decimal


_Q4 = Decimal("0.0001")
_ZERO = Decimal("0")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")

DEFAULT_GAMMA = Decimal("1")


def compute_gave_score(
    *,
    revenue: Decimal | int,
    cost: Decimal | int,
    bep_roas: Decimal,
    gamma: Decimal = DEFAULT_GAMMA,
) -> dict:
    revenue = Decimal(revenue)
    cost = Decimal(cost)

    if cost <= 0:
        return {
            "score": _ZERO,
            "penalty": _ONE,
            "roas": None,
            "roas_ratio": None,
            "gamma": gamma,
        }

    roas = (revenue / cost * _HUNDRED).quantize(_Q4)

    if bep_roas <= 0:
        return {
            "score": revenue.quantize(_Q4),
            "penalty": _ONE,
            "roas": roas,
            "roas_ratio": None,
            "gamma": gamma,
        }

    ratio = roas / bep_roas

    if ratio >= _ONE:
        penalty = _ONE
    elif ratio <= _ZERO:
        penalty = _ZERO
    elif gamma == _ZERO:
        penalty = _ONE
    else:
        penalty = ratio ** gamma
        penalty = min(penalty, _ONE)

    score = (penalty * revenue).quantize(_Q4)

    return {
        "score": score,
        "penalty": penalty.quantize(_Q4),
        "roas": roas,
        "roas_ratio": ratio.quantize(_Q4),
        "gamma": gamma,
    }


def score_batch(
    items: list[dict],
    *,
    bep_roas: Decimal,
    gamma: Decimal = DEFAULT_GAMMA,
    revenue_key: str = "revenue",
    cost_key: str = "cost",
) -> list[dict]:
    results = []
    for item in items:
        scored = compute_gave_score(
            revenue=item.get(revenue_key, 0),
            cost=item.get(cost_key, 0),
            bep_roas=bep_roas,
            gamma=gamma,
        )
        scored["item"] = item
        results.append(scored)
    results.sort(key=lambda r: r["score"], reverse=True)
    return results
