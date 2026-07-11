# hierarchical_pooling.py — DHEB 계층 EB 풀링 SA (X3 T1, ref 26 ②)
# 역할(SA): CTR/CVR/RPC를 계정→캠페인→광고그룹→키워드 4계층 Empirical Bayes
#   축소추정(shrinkage)으로 일반화. 기존 pooled_rpc(bid_simulator)의 확장.
#   3만 롱테일 키워드의 keyword grain 예측 원료를 만든다.
#   순수함수 — DB/API 접근 없음(원칙 18).
from __future__ import annotations

from decimal import Decimal


_Q4 = Decimal("0.0001")
_ZERO = Decimal("0")

SHRINK_K = Decimal("10")

METRICS: dict[str, tuple[str, str]] = {
    "ctr": ("clk", "imp"),
    "cvr": ("conv_cnt", "clk"),
    "rpc": ("conv_amt", "clk"),
}


def shrink(n: int | Decimal, raw: Decimal, prior: Decimal, k: int | Decimal = SHRINK_K) -> Decimal:
    n = Decimal(n)
    k = Decimal(k)
    if n + k == 0:
        return _ZERO
    return (n * raw + k * prior) / (n + k)


def _level_metric(agg: dict, metric: str, prior: Decimal) -> Decimal:
    num_key, den_key = METRICS[metric]
    num = agg.get(num_key, 0)
    den = agg.get(den_key, 0)
    raw = (Decimal(num) / Decimal(den)) if den else _ZERO
    return shrink(n=den, raw=raw, prior=prior)


def pool_metric(
    keyword_row: dict,
    group_agg: dict,
    campaign_agg: dict,
    account_agg: dict,
    metric: str,
) -> Decimal:
    if metric not in METRICS:
        raise ValueError(f"unknown metric '{metric}', expected one of {list(METRICS)}")

    num_key, den_key = METRICS[metric]

    acct_den = account_agg.get(den_key, 0)
    acct_raw = (
        Decimal(account_agg.get(num_key, 0)) / Decimal(acct_den)
    ) if acct_den else _ZERO

    campaign_pooled = _level_metric(campaign_agg, metric, prior=acct_raw)
    group_pooled = _level_metric(group_agg, metric, prior=campaign_pooled)
    keyword_pooled = _level_metric(keyword_row, metric, prior=group_pooled)

    return keyword_pooled.quantize(_Q4)


def pool_all(
    keyword_row: dict,
    group_agg: dict,
    campaign_agg: dict,
    account_agg: dict,
) -> dict[str, Decimal]:
    return {
        metric: pool_metric(keyword_row, group_agg, campaign_agg, account_agg, metric)
        for metric in METRICS
    }
