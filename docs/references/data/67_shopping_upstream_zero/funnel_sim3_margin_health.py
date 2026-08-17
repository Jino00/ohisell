#!/usr/bin/env python3
"""G4(margin 존재) 게이트의 «일반 건강도» — funnel에서 거의 호출되지 않으므로(G3가 이미 대부분
걸러냄) 별도로: 14일 창에 등장하는 모든 distinct adgroup_id 중 margin을 구할 수 있는 비율.
읽기 전용."""
import csv
from funnel_sim import load_bep, load_adgroup_product, load_order_revenue, weighted_margin_for_adgroup, SCRATCH

bep_rows, margin_by_cpid = load_bep()
adgroup_product_map = load_adgroup_product()
order_rev = load_order_revenue()


def analyze(path, label):
    adgroups_shopping = set()
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            if row["source"] != "shopping":
                continue
            adgroups_shopping.add(row["adgroup_id"])

    has_margin = 0
    no_margin_no_mapping = 0
    no_margin_mapped_no_cost = 0
    for ag in adgroups_shopping:
        cpids = adgroup_product_map.get(ag)
        m = weighted_margin_for_adgroup(ag, adgroup_product_map, margin_by_cpid, order_rev)
        if m is not None and m > 0:
            has_margin += 1
        elif not cpids:
            no_margin_no_mapping += 1
        else:
            no_margin_mapped_no_cost += 1

    print(f"\n== {label} ==")
    print(f" distinct shopping adgroups in window: {len(adgroups_shopping)}")
    print(f" margin resolvable (>0): {has_margin}")
    print(f" margin None (no naver_adgroup_product mapping at all): {no_margin_no_mapping}")
    print(f" margin None (mapped but no has_cost=True product_bep match): {no_margin_mapped_no_cost}")


if __name__ == "__main__":
    analyze(f"{SCRATCH}/funnel_out2_group14.csv", "14-day window shopping adgroups")
    analyze(f"{SCRATCH}/funnel_out3_group90.csv", "90-day window shopping adgroups")
