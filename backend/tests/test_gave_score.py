# test_gave_score.py — X3 T2 GAVE 페널티 점수 단위 테스트
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.naver_ad.gave_score import compute_gave_score, DEFAULT_GAMMA


_Q4 = Decimal("0.0001")


class TestBasicScoring:
    def test_roas_above_bep_gets_full_credit(self):
        result = compute_gave_score(
            revenue=Decimal("100000"), cost=Decimal("20000"),
            bep_roas=Decimal("3"), gamma=Decimal("1"),
        )
        # ROAS = 100000/20000 = 5.0 > BEP 3.0 → S = revenue
        assert result["score"] == Decimal("100000")
        assert result["penalty"] == Decimal("1")

    def test_roas_exactly_at_bep(self):
        result = compute_gave_score(
            revenue=Decimal("60000"), cost=Decimal("20000"),
            bep_roas=Decimal("3"), gamma=Decimal("1"),
        )
        # ROAS = 60000/20000 = 3.0 = BEP → penalty = 1
        assert result["penalty"] == Decimal("1")
        assert result["score"] == Decimal("60000")

    def test_roas_below_bep_gets_penalized(self):
        result = compute_gave_score(
            revenue=Decimal("40000"), cost=Decimal("20000"),
            bep_roas=Decimal("3"), gamma=Decimal("1"),
        )
        # ROAS = 40000/20000 = 2.0 < BEP 3.0
        # penalty = (2.0/3.0)^1 = 0.6667
        # score = 0.6667 * 40000 ≈ 26667
        assert result["penalty"] < Decimal("1")
        assert result["penalty"] > Decimal("0")
        assert result["score"] < Decimal("40000")
        expected_penalty = (Decimal("2") / Decimal("3"))
        assert abs(result["penalty"] - expected_penalty) < Decimal("0.001")

    def test_zero_roas_gets_zero_score(self):
        result = compute_gave_score(
            revenue=Decimal("0"), cost=Decimal("20000"),
            bep_roas=Decimal("3"), gamma=Decimal("1"),
        )
        assert result["score"] == Decimal("0")


class TestGammaDial:
    def test_gamma_zero_no_penalty(self):
        result = compute_gave_score(
            revenue=Decimal("40000"), cost=Decimal("20000"),
            bep_roas=Decimal("3"), gamma=Decimal("0"),
        )
        # (ROAS/BEP)^0 = 1 always → no penalty
        assert result["penalty"] == Decimal("1")
        assert result["score"] == Decimal("40000")

    def test_higher_gamma_harsher_penalty(self):
        base = dict(revenue=Decimal("30000"), cost=Decimal("20000"), bep_roas=Decimal("3"))
        s1 = compute_gave_score(**base, gamma=Decimal("1"))
        s2 = compute_gave_score(**base, gamma=Decimal("2"))
        s3 = compute_gave_score(**base, gamma=Decimal("3"))
        # higher gamma → harsher penalty → lower score
        assert s1["score"] > s2["score"] > s3["score"]
        assert s1["penalty"] > s2["penalty"] > s3["penalty"]

    def test_fractional_gamma(self):
        result = compute_gave_score(
            revenue=Decimal("30000"), cost=Decimal("20000"),
            bep_roas=Decimal("3"), gamma=Decimal("0.5"),
        )
        # ROAS = 1.5, BEP = 3.0 → ratio = 0.5
        # penalty = 0.5^0.5 ≈ 0.707
        assert Decimal("0.6") < result["penalty"] < Decimal("0.8")

    def test_default_gamma(self):
        assert DEFAULT_GAMMA == Decimal("1")


class TestEdgeCases:
    def test_zero_cost_zero_score(self):
        result = compute_gave_score(
            revenue=Decimal("0"), cost=Decimal("0"),
            bep_roas=Decimal("3"), gamma=Decimal("1"),
        )
        assert result["score"] == Decimal("0")
        assert result["roas"] is None

    def test_zero_bep_full_credit(self):
        result = compute_gave_score(
            revenue=Decimal("50000"), cost=Decimal("10000"),
            bep_roas=Decimal("0"), gamma=Decimal("1"),
        )
        # BEP = 0 → no ROAS constraint → full credit
        assert result["penalty"] == Decimal("1")
        assert result["score"] == Decimal("50000")

    def test_negative_revenue_handled(self):
        result = compute_gave_score(
            revenue=Decimal("-5000"), cost=Decimal("10000"),
            bep_roas=Decimal("3"), gamma=Decimal("1"),
        )
        assert result["score"] <= Decimal("0")

    def test_very_high_roas_capped_at_one(self):
        result = compute_gave_score(
            revenue=Decimal("1000000"), cost=Decimal("1000"),
            bep_roas=Decimal("1"), gamma=Decimal("1"),
        )
        # ROAS = 1000 >> BEP 1.0 → penalty capped at 1
        assert result["penalty"] == Decimal("1")

    def test_result_structure(self):
        result = compute_gave_score(
            revenue=Decimal("50000"), cost=Decimal("10000"),
            bep_roas=Decimal("3"), gamma=Decimal("1"),
        )
        assert "score" in result
        assert "penalty" in result
        assert "roas" in result
        assert "roas_ratio" in result
        assert "gamma" in result


class TestBatchScoring:
    def test_score_batch(self):
        from app.services.naver_ad.gave_score import score_batch

        items = [
            {"id": "kw-1", "revenue": 100000, "cost": 20000},
            {"id": "kw-2", "revenue": 30000, "cost": 20000},
            {"id": "kw-3", "revenue": 0, "cost": 5000},
        ]
        results = score_batch(
            items,
            bep_roas=Decimal("3"),
            gamma=Decimal("1"),
            revenue_key="revenue",
            cost_key="cost",
        )
        assert len(results) == 3
        # kw-1: ROAS 5.0 > BEP 3.0 → full credit, highest score
        assert results[0]["score"] > results[1]["score"]
        # kw-3: revenue 0 → score 0
        assert results[2]["score"] == Decimal("0")

    def test_score_batch_sorted_descending(self):
        from app.services.naver_ad.gave_score import score_batch

        items = [
            {"revenue": 10000, "cost": 10000},  # ROAS 1.0
            {"revenue": 50000, "cost": 10000},  # ROAS 5.0
            {"revenue": 30000, "cost": 10000},  # ROAS 3.0
        ]
        results = score_batch(items, bep_roas=Decimal("3"), gamma=Decimal("1"))
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)


class TestRoasUnitConsistency:
    """codex P2 regression: ROAS는 ratio 단위(revenue/cost)이며 percent가 아님."""

    def test_roas_is_ratio_not_percent(self):
        result = compute_gave_score(
            revenue=Decimal("60000"), cost=Decimal("20000"),
            bep_roas=Decimal("3"), gamma=Decimal("1"),
        )
        # ROAS should be 3.0 (ratio), not 300 (percent)
        assert result["roas"] == Decimal("3.0000")

    def test_below_bep_penalty_with_ratio_units(self):
        result = compute_gave_score(
            revenue=Decimal("40000"), cost=Decimal("20000"),
            bep_roas=Decimal("3"), gamma=Decimal("1"),
        )
        # ROAS = 2.0, BEP = 3.0 → ratio = 2/3 → penalty ≈ 0.6667
        assert Decimal("0.65") < result["penalty"] < Decimal("0.68")
        assert result["roas"] == Decimal("2.0000")
