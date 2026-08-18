#!/usr/bin/env python3
import csv

OUT = "/private/tmp/claude-501/-Users-jino-Library-Mobile-Documents-com-apple-CloudDocs-1Personal-AI-Program-Ohiselling/78288970-12ad-4647-a554-94288862eca2/scratchpad/a3_brand"

dict_rows = list(csv.DictReader(open(f"{OUT}/brand_dict_confirmed_merged.csv", encoding="utf-8")))
token_stats = {r["token"]: r for r in csv.DictReader(open(f"{OUT}/out_token_stats.csv", encoding="utf-8"))}

final = []
for r in dict_rows:
    ts = token_stats.get(r["token"], {"n_combo": 0, "n_daily_rows": 0, "clk": 0, "cost": 0, "conv_cnt": 0, "conv_amt": 0})
    final.append({
        "token": r["token"], "raw": r["raw"], "category": r["category"],
        "ambiguity_risk": r["ambiguity_risk"],
        "matched_n_combo": ts["n_combo"], "matched_clk": ts["clk"], "matched_cost": ts["cost"],
        "matched_conv_cnt": ts["conv_cnt"], "matched_conv_amt": ts["conv_amt"],
        "sources": r["sources"], "notes": r["notes"],
    })

final.sort(key=lambda r: -int(r["matched_cost"]))

with open(f"{OUT}/brand_dict_confirmed.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["token", "raw", "category", "ambiguity_risk",
                                       "matched_n_combo", "matched_clk", "matched_cost",
                                       "matched_conv_cnt", "matched_conv_amt", "sources", "notes"])
    w.writeheader()
    for r in final:
        w.writerow(r)

print(f"wrote {len(final)} confirmed tokens sorted by matched_cost desc")
for r in final[:15]:
    print(f"  {r['token']:12} {r['category']:12} cost={int(r['matched_cost']):>10,} clk={int(r['matched_clk']):>6,} risk={r['ambiguity_risk']}")

# pending — already have cost measurements from ad-hoc substring scan
pending_cost = {
    "소다케이스": (51, 6, 2372),
    "버디": (2409, 710, 398874),
    "a73": (72, 4, 2847),
    "일미리케이스": (107, 89, 17779),
}
pending_rows = list(csv.DictReader(open(f"{OUT}/brand_dict_pending_jino_raw.csv", encoding="utf-8")))
final_pending = []
for r in pending_rows:
    key = r["token"]
    n_combo, clk, cost = pending_cost.get(key, (None, None, None))
    final_pending.append({
        "token": r["token"], "raw": r["raw"],
        "substring_n_combo": n_combo, "substring_clk": clk, "substring_cost": cost,
        "reason": r["reason"], "source_detail": r["source_detail"],
    })
final_pending.sort(key=lambda r: -(r["substring_cost"] or 0))
with open(f"{OUT}/brand_dict_pending_jino.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["token", "raw", "substring_n_combo", "substring_clk", "substring_cost", "reason", "source_detail"])
    w.writeheader()
    for r in final_pending:
        w.writerow(r)
print(f"\nwrote {len(final_pending)} pending tokens")
