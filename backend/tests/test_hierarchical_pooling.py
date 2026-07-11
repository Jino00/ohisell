# test_hierarchical_pooling.py — X3 T1 DHEB 계층 EB 풀링 단위 테스트
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.naver_ad.hierarchical_pooling import (
    METRICS,
    pool_metric,
    pool_all,
    shrink,
)


_Q4 = Decimal("0.0001")


# ── shrink 기본 동작 ─────────────────────────────────────────

class TestShrink:
    def test_zero_observations_returns_prior(self):
        assert shrink(n=0, raw=Decimal("0"), prior=Decimal("0.05"), k=10) == Decimal("0.05")

    def test_high_observations_converges_to_raw(self):
        result = shrink(n=10000, raw=Decimal("0.03"), prior=Decimal("0.10"), k=10)
        assert abs(result - Decimal("0.03")) < Decimal("0.001")

    def test_equal_weight_at_k(self):
        result = shrink(n=10, raw=Decimal("0.08"), prior=Decimal("0.02"), k=10)
        expected = (10 * Decimal("0.08") + 10 * Decimal("0.02")) / 20
        assert result == expected

    def test_negative_raw_allowed(self):
        result = shrink(n=5, raw=Decimal("-0.01"), prior=Decimal("0.05"), k=10)
        assert result > Decimal("-0.01")
        assert result < Decimal("0.05")


# ── pool_metric: 단일 메트릭 계층 풀링 ──────────────────────

class TestPoolMetric:
    @pytest.fixture
    def high_data(self):
        return {
            "keyword": {"imp": 5000, "clk": 200, "conv_cnt": 20, "conv_amt": 100000},
            "adgroup": {"imp": 20000, "clk": 800, "conv_cnt": 80, "conv_amt": 400000},
            "campaign": {"imp": 100000, "clk": 4000, "conv_cnt": 400, "conv_amt": 2000000},
            "account": {"imp": 500000, "clk": 20000, "conv_cnt": 2000, "conv_amt": 10000000},
        }

    @pytest.fixture
    def sparse_keyword(self):
        return {
            "keyword": {"imp": 10, "clk": 1, "conv_cnt": 0, "conv_amt": 0},
            "adgroup": {"imp": 5000, "clk": 200, "conv_cnt": 20, "conv_amt": 100000},
            "campaign": {"imp": 50000, "clk": 2000, "conv_cnt": 200, "conv_amt": 1000000},
            "account": {"imp": 300000, "clk": 12000, "conv_cnt": 1200, "conv_amt": 6000000},
        }

    def test_rpc_high_data_near_raw(self, high_data):
        result = pool_metric(
            high_data["keyword"], high_data["adgroup"],
            high_data["campaign"], high_data["account"], "rpc",
        )
        raw_rpc = Decimal("100000") / Decimal("200")  # 500
        assert abs(result - raw_rpc) < Decimal("50")

    def test_ctr_high_data_near_raw(self, high_data):
        result = pool_metric(
            high_data["keyword"], high_data["adgroup"],
            high_data["campaign"], high_data["account"], "ctr",
        )
        raw_ctr = Decimal("200") / Decimal("5000")  # 0.04
        assert abs(result - raw_ctr) < Decimal("0.005")

    def test_cvr_high_data_near_raw(self, high_data):
        result = pool_metric(
            high_data["keyword"], high_data["adgroup"],
            high_data["campaign"], high_data["account"], "cvr",
        )
        raw_cvr = Decimal("20") / Decimal("200")  # 0.10
        assert abs(result - raw_cvr) < Decimal("0.02")

    def test_sparse_keyword_pulled_toward_parent(self, sparse_keyword):
        result = pool_metric(
            sparse_keyword["keyword"], sparse_keyword["adgroup"],
            sparse_keyword["campaign"], sparse_keyword["account"], "rpc",
        )
        # keyword raw RPC = 0/1 = 0, but parent has substantial RPC
        # should be pulled toward adgroup/campaign level
        assert result > Decimal("0")

    def test_sparse_keyword_ctr_pulled_toward_parent(self, sparse_keyword):
        result = pool_metric(
            sparse_keyword["keyword"], sparse_keyword["adgroup"],
            sparse_keyword["campaign"], sparse_keyword["account"], "ctr",
        )
        raw_ctr = Decimal("1") / Decimal("10")  # 0.10
        parent_ctr = Decimal("200") / Decimal("5000")  # 0.04
        # with only 10 impressions, should be pulled toward parent
        assert result < raw_ctr
        assert result > parent_ctr * Decimal("0.5")

    def test_zero_click_keyword_cvr_falls_back(self, sparse_keyword):
        zero_clk = {"imp": 50, "clk": 0, "conv_cnt": 0, "conv_amt": 0}
        result = pool_metric(
            zero_clk, sparse_keyword["adgroup"],
            sparse_keyword["campaign"], sparse_keyword["account"], "cvr",
        )
        # clk=0 → raw CVR=0, n=0 → returns parent prior
        assert result > Decimal("0")

    def test_unknown_metric_raises(self, high_data):
        with pytest.raises(ValueError, match="unknown"):
            pool_metric(
                high_data["keyword"], high_data["adgroup"],
                high_data["campaign"], high_data["account"], "unknown_metric",
            )


# ── pool_all: 3종 동시 풀링 ─────────────────────────────────

class TestPoolAll:
    def test_returns_all_three_metrics(self):
        kw = {"imp": 1000, "clk": 50, "conv_cnt": 5, "conv_amt": 25000}
        grp = {"imp": 10000, "clk": 500, "conv_cnt": 50, "conv_amt": 250000}
        camp = {"imp": 50000, "clk": 2500, "conv_cnt": 250, "conv_amt": 1250000}
        acct = {"imp": 200000, "clk": 10000, "conv_cnt": 1000, "conv_amt": 5000000}
        result = pool_all(kw, grp, camp, acct)
        assert "ctr" in result
        assert "cvr" in result
        assert "rpc" in result
        assert all(isinstance(v, Decimal) for v in result.values())

    def test_all_zeros_returns_zeros(self):
        zero = {"imp": 0, "clk": 0, "conv_cnt": 0, "conv_amt": 0}
        result = pool_all(zero, zero, zero, zero)
        assert result["ctr"] == Decimal("0")
        assert result["cvr"] == Decimal("0")
        assert result["rpc"] == Decimal("0")

    def test_consistent_with_individual_calls(self):
        kw = {"imp": 500, "clk": 25, "conv_cnt": 3, "conv_amt": 15000}
        grp = {"imp": 5000, "clk": 250, "conv_cnt": 25, "conv_amt": 125000}
        camp = {"imp": 30000, "clk": 1500, "conv_cnt": 150, "conv_amt": 750000}
        acct = {"imp": 150000, "clk": 7500, "conv_cnt": 750, "conv_amt": 3750000}
        all_result = pool_all(kw, grp, camp, acct)
        for metric in METRICS:
            individual = pool_metric(kw, grp, camp, acct, metric)
            assert all_result[metric] == individual


# ── backward compat: pooled_rpc 일관성 ──────────────────────

class TestBackwardCompat:
    def test_pool_metric_rpc_matches_existing_pooled_rpc_direction(self):
        """pool_metric('rpc') should shrink in the same direction as bid_simulator.pooled_rpc."""
        kw = {"imp": 100, "clk": 5, "conv_cnt": 1, "conv_amt": 5000}
        grp = {"imp": 2000, "clk": 100, "conv_cnt": 10, "conv_amt": 50000}
        camp = {"imp": 20000, "clk": 1000, "conv_cnt": 100, "conv_amt": 500000}
        acct = {"imp": 100000, "clk": 5000, "conv_cnt": 500, "conv_amt": 2500000}

        result = pool_metric(kw, grp, camp, acct, "rpc")
        raw_rpc = Decimal("5000") / Decimal("5")  # 1000
        account_rpc = Decimal("2500000") / Decimal("5000")  # 500
        # with only 5 clicks, should be pulled toward parent (< raw)
        assert result < raw_rpc
        assert result > account_rpc


# ── 경계 케이스 ─────────────────────────────────────────────

class TestEdgeCases:
    def test_single_impression_keyword(self):
        kw = {"imp": 1, "clk": 1, "conv_cnt": 1, "conv_amt": 50000}
        grp = {"imp": 10000, "clk": 400, "conv_cnt": 40, "conv_amt": 200000}
        camp = {"imp": 50000, "clk": 2000, "conv_cnt": 200, "conv_amt": 1000000}
        acct = {"imp": 200000, "clk": 8000, "conv_cnt": 800, "conv_amt": 4000000}
        result = pool_all(kw, grp, camp, acct)
        # should not crash, values should be finite
        assert all(v >= 0 for v in result.values())
        # CTR with 1 imp should be heavily shrunk toward parent
        assert result["ctr"] < Decimal("0.5")

    def test_keyword_better_than_parents(self):
        kw = {"imp": 500, "clk": 100, "conv_cnt": 50, "conv_amt": 500000}
        grp = {"imp": 5000, "clk": 200, "conv_cnt": 10, "conv_amt": 50000}
        camp = {"imp": 50000, "clk": 2000, "conv_cnt": 100, "conv_amt": 500000}
        acct = {"imp": 200000, "clk": 8000, "conv_cnt": 400, "conv_amt": 2000000}
        result = pool_all(kw, grp, camp, acct)
        # keyword is much better than parents — pooling should still pull toward parent somewhat
        raw_cvr = Decimal("50") / Decimal("100")  # 0.50
        assert result["cvr"] < raw_cvr

    def test_missing_keys_default_to_zero(self):
        kw = {"clk": 10}  # missing imp, conv_cnt, conv_amt
        full = {"imp": 10000, "clk": 500, "conv_cnt": 50, "conv_amt": 250000}
        result = pool_all(kw, full, full, full)
        assert result["ctr"] >= Decimal("0")
        assert result["rpc"] >= Decimal("0")
