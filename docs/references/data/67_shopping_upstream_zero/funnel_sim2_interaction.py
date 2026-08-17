#!/usr/bin/env python3
"""G2(화이트리스트) vs G3(클릭) 상호작용 분해 — 어느 게이트가 진짜 병목인지 독립적으로 재기.
읽기 전용. 원본 CSV만 사용."""
import csv
from collections import defaultdict
from funnel_sim import build_whitelist, is_whitelisted, load_bep, SCRATCH, _SS_MIN_CLICK, _POWERLINK_SOURCE

bep_rows, _ = load_bep()
whitelist = build_whitelist(bep_rows)


def analyze(path, label):
    total_g1_survivors = 0
    clk_ge10 = 0
    clk_ge10_whitelisted = 0
    clk_ge10_not_whitelisted = 0
    clk_lt10 = 0
    clk_lt10_whitelisted = 0
    clk_lt10_not_whitelisted = 0
    by_source = defaultdict(lambda: defaultdict(int))

    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            src = row["source"]
            if src == _POWERLINK_SOURCE:
                continue  # shopping only for this breakdown
            pconv = int(row["conv_purchase_cnt"])
            if pconv >= 1:
                continue
            total_g1_survivors += 1
            clk = int(row["clk"])
            term = row["search_term"]
            hit = is_whitelisted(term, whitelist)
            if clk >= _SS_MIN_CLICK:
                clk_ge10 += 1
                if hit:
                    clk_ge10_whitelisted += 1
                else:
                    clk_ge10_not_whitelisted += 1
            else:
                clk_lt10 += 1
                if hit:
                    clk_lt10_whitelisted += 1
                else:
                    clk_lt10_not_whitelisted += 1

    print(f"\n== {label} (shopping only, G1 survivors=pconv==0) ==")
    print(f" total_g1_survivors={total_g1_survivors}")
    print(f" clk>=10: {clk_ge10}  (whitelisted={clk_ge10_whitelisted}, NOT whitelisted={clk_ge10_not_whitelisted})")
    print(f" clk<10 : {clk_lt10}  (whitelisted={clk_lt10_whitelisted}, NOT whitelisted={clk_lt10_not_whitelisted})")
    print(f" => rows that pass G1+G3(clk>=10) AND G2(not whitelisted) = {clk_ge10_not_whitelisted}  <- candidates before G4/G5 margin gates")


if __name__ == "__main__":
    analyze(f"{SCRATCH}/funnel_out2_group14.csv", "14-day window")
    analyze(f"{SCRATCH}/funnel_out3_group90.csv", "90-day window")
