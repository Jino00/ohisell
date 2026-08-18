"""문자 n-gram으로 풀링이 가능한가 — 한국어 무공백 검색어 대응 실측(읽기 전용)."""
import csv, re, sys, collections
sys.path.insert(0, "backend")
from app.services.naver_ad.search_term_judge import _SS_WHITELIST_TOKENS, _SS_MIN_CLICK
R = re.compile(r"[^0-9A-Za-z가-힣]+"); csv.field_size_limit(10**7)
wl = {t.casefold() for t in _SS_WHITELIST_TOKENS}
for r in csv.DictReader(open("docs/references/data/67_shopping_upstream_zero/funnel_out4_bep.csv", newline="", encoding="utf-8")):
    if str(r.get("has_cost","")).strip() in ("1","True","true"):
        for t in R.split(r.get("product_name") or ""):
            if len(t)>=2 and not t.isdigit(): wl.add(t.casefold())

rows=[]
for row in csv.DictReader(open(sys.argv[1], newline="", encoding="utf-8")):
    if row["source"]!="shopping" or int(row["conv_purchase_cnt"] or 0)>=1: continue
    rows.append((row["adgroup_id"], (row["search_term"] or "").casefold().replace(" ",""),
                 int(row["clk"] or 0), int(row["cost"] or 0)))
print(f"G1 생존 {len(rows):,}행 · 그룹 {len({r[0] for r in rows}):,}개\n")
for n in (3,4,5):
    d=collections.defaultdict(lambda:[0,0,0])
    for ag,term,clk,cost in rows:
        for g in {term[i:i+n] for i in range(max(0,len(term)-n+1))}:
            c=d[(ag,g)]; c[0]+=clk; c[1]+=cost; c[2]+=1
    surv={k:v for k,v in d.items() if v[0]>=_SS_MIN_CLICK}
    blocked={k for k in surv if any(w in k[1] for w in wl)}
    passed={k:v for k,v in surv.items() if k not in blocked}
    pooled=[v for v in surv.values() if v[2]>=2]
    print(f"[문자 {n}-gram] 전체 {len(d):,} → clk>=10 {len(surv):,} → 화이트리스트 차단 {len(blocked):,} → **생존 {len(passed):,}**"
          + (f" (차단율 {len(blocked)/len(surv)*100:.1f}%)" if surv else ""))
    print(f"   └ clk>=10 중 «2개 이상 검색어를 묶은» 것 {len(pooled):,}건 = 풀링이 실제로 일어난 증거")
    for (ag,g),v in sorted(passed.items(), key=lambda x:-x[1][1])[:8]:
        print(f"     통과: clk={v[0]:>4} cost={v[1]:>8,} 묶인검색어={v[2]:>4}  「{g}」  {ag[-9:]}")
