#!/usr/bin/env python3
"""중복 토큰(같은 정규화 결과를 여러 출처가 만든 경우) 병합 + 모호성 위험 플래그."""
import csv, collections, re

OUT = "/private/tmp/claude-501/-Users-jino-Library-Mobile-Documents-com-apple-CloudDocs-1Personal-AI-Program-Ohiselling/78288970-12ad-4647-a554-94288862eca2/scratchpad/a3_brand"

rows = list(csv.DictReader(open(f"{OUT}/brand_dict_confirmed_raw.csv", encoding="utf-8")))
merged = collections.OrderedDict()
for r in rows:
    t = r["token"]
    if t not in merged:
        merged[t] = {"token": t, "raw": r["raw"], "category": r["category"],
                      "sources": [], "notes": []}
    merged[t]["sources"].append(f"{r['source_file']}:{r['source_column']} ({r['source_detail']})")
    if r["note"]:
        merged[t]["notes"].append(r["note"])

def risk(t):
    if re.fullmatch(r"\d+", t):
        return "high(순수 숫자)"
    if len(t) <= 2:
        return "high(2자 이하)"
    if re.fullmatch(r"[a-z]\d+", t):
        return "medium(알파벳1+숫자, 예: s8)"
    return "low"

out_rows = []
for t, d in merged.items():
    out_rows.append({
        "token": t, "raw": d["raw"], "category": d["category"],
        "ambiguity_risk": risk(t),
        "n_sources": len(d["sources"]),
        "sources": " || ".join(d["sources"]),
        "notes": " | ".join(d["notes"]),
    })

# 정렬: category → token
cat_order = {"self_brand": 0, "brand_root": 1, "model_code": 2}
out_rows.sort(key=lambda r: (cat_order.get(r["category"], 9), r["token"]))

with open(f"{OUT}/brand_dict_confirmed_merged.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["token", "raw", "category", "ambiguity_risk", "n_sources", "sources", "notes"])
    w.writeheader()
    for r in out_rows:
        w.writerow(r)

print(f"merged unique tokens: {len(out_rows)}")
import collections as C
print("category:", C.Counter(r["category"] for r in out_rows))
print("ambiguity_risk:", C.Counter(r["ambiguity_risk"] for r in out_rows))
