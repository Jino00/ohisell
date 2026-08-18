"""ⓒ n-gram 후보가 화이트리스트를 통과하는가 — 읽기 전용 사전 실측.

질문: n-gram 풀링을 만들면 후보가 실제로 나오나, 아니면 화이트리스트가 또 0으로 만드나.
grain: 제외는 «광고그룹» 단위로 걸리므로 (adgroup_id, ngram)을 후보 grain으로 잡는다.
게이트: G1(전환 보호)은 원본 행에서 이미 적용, 그 다음 n-gram 집계 → clk>=10 → 화이트리스트.
"""
import csv, re, sys, collections
sys.path.insert(0, "backend")
from app.services.naver_ad.search_term_judge import _SS_WHITELIST_TOKENS, _SS_MIN_CLICK

R = re.compile(r"[^0-9A-Za-z가-힣]+")
csv.field_size_limit(10**7)

# 화이트리스트 재현
wl = {t.casefold() for t in _SS_WHITELIST_TOKENS}
with open("docs/references/data/67_shopping_upstream_zero/funnel_out4_bep.csv", newline="", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if str(r.get("has_cost", "")).strip() not in ("1", "True", "true"):
            continue
        for t in R.split(r.get("product_name") or ""):
            if len(t) >= 2 and not t.isdigit():
                wl.add(t.casefold())

src = sys.argv[1]
tokcount = collections.Counter()
uni = collections.defaultdict(lambda: [0, 0, 0])   # (adgroup, 1gram) -> [clk, cost, terms]
bi  = collections.defaultdict(lambda: [0, 0, 0])
g1 = 0
for row in csv.DictReader(open(src, newline="", encoding="utf-8")):
    if row["source"] != "shopping":
        continue
    if int(row["conv_purchase_cnt"] or 0) >= 1:      # G1 전환 보호
        continue
    g1 += 1
    ag = row["adgroup_id"]
    clk, cost = int(row["clk"] or 0), int(row["cost"] or 0)
    toks = [t for t in R.split(row["search_term"] or "") if t]
    tokcount[min(len(toks), 5)] += 1
    for t in toks:
        c = uni[(ag, t.casefold())]; c[0] += clk; c[1] += cost; c[2] += 1
    for a, b in zip(toks, toks[1:]):
        c = bi[(ag, f"{a} {b}".casefold())]; c[0] += clk; c[1] += cost; c[2] += 1

print(f"G1 생존(shopping, 14d) = {g1:,}행")
print("\n[검색어 토큰 수 분포] ★2-gram이 가능하려면 토큰 2개 이상이어야 한다")
tot = sum(tokcount.values())
for k in sorted(tokcount):
    lab = f"{k}개" + ("+" if k == 5 else "")
    print(f"  {lab:>4}: {tokcount[k]:>7,} ({tokcount[k]/tot*100:5.1f}%)")

for name, d in (("1-gram", uni), ("2-gram", bi)):
    surv = {k: v for k, v in d.items() if v[0] >= _SS_MIN_CLICK}
    blocked = {k: v for k, v in surv.items() if any(w in k[1] for w in wl)}
    passed = {k: v for k, v in surv.items() if k not in blocked}
    print(f"\n[{name}] 후보 grain=(adgroup, ngram)")
    print(f"  전체 {len(d):,} → clk>={_SS_MIN_CLICK} 통과 {len(surv):,} → 화이트리스트 차단 {len(blocked):,} → **최종 생존 {len(passed):,}**")
    if surv:
        print(f"  차단율 {len(blocked)/len(surv)*100:.1f}%")
    for (ag, ng), v in sorted(passed.items(), key=lambda x: -x[1][1])[:12]:
        print(f"    통과: clk={v[0]:>4} cost={v[1]:>8,} terms={v[2]:>4}  「{ng}」  {ag[-9:]}")
