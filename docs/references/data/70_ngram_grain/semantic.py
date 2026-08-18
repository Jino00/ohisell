"""의미 단위 분절 — 언어 분석기 없이 «우리가 아는 개념 사전»으로 자른다(읽기 전용).

사전 = ①상품명 토큰(naver_product_bep, has_cost) ②광고그룹명 토큰(=단말 개념) ③기능어(하드코딩 화이트리스트)
방법 = 최장일치(longest-match) 스캔. 사전에 없는 구간은 «잔여»로 남긴다.
★잔여가 곧 「우리 개념 사전에 없는 수식어」 = 무관 유입 후보의 정체다.
"""
import csv, re, sys, collections
sys.path.insert(0,"backend")
from app.services.naver_ad.search_term_judge import _SS_WHITELIST_TOKENS, _SS_MIN_CLICK
R=re.compile(r"[^0-9A-Za-z가-힣]+"); csv.field_size_limit(10**7)

vocab=set()
for t in _SS_WHITELIST_TOKENS:
    if len(t)>=2: vocab.add(t.casefold())
for r in csv.DictReader(open("docs/references/data/67_shopping_upstream_zero/funnel_out4_bep.csv",newline="",encoding="utf-8")):
    if str(r.get("has_cost","")).strip() in ("1","True","true"):
        for t in R.split(r.get("product_name") or ""):
            if len(t)>=2 and not t.isdigit(): vocab.add(t.casefold())
for r in csv.DictReader(open("docs/references/data/66_exclusion_slots/all_shopping_group_counts.csv",newline="",encoding="utf-8")):
    for t in R.split(r.get("group_name") or ""):
        if len(t)>=2 and not t.isdigit(): vocab.add(t.casefold())
V=sorted(vocab,key=len,reverse=True)
print(f"의미 사전 {len(V)}개 (상품명 ∪ 그룹명 ∪ 기능어)")

def seg(s):
    """최장일치 분절 → (인식된 의미단위 리스트, 잔여 조각 리스트)"""
    units,resid,i=[],[],0
    while i<len(s):
        for v in V:
            if s.startswith(v,i): units.append(v); i+=len(v); break
        else:
            j=i
            while j<len(s) and not any(s.startswith(v,j) for v in V): j+=1
            resid.append(s[i:j]); i=j
    return units,resid

rows=[]
for row in csv.DictReader(open(sys.argv[1],newline="",encoding="utf-8")):
    if row["source"]!="shopping" or int(row["conv_purchase_cnt"] or 0)>=1: continue
    rows.append((row["adgroup_id"],(row["search_term"] or "").casefold().replace(" ",""),
                 int(row["clk"] or 0),int(row["cost"] or 0)))

cov=collections.Counter(); unit_agg=collections.defaultdict(lambda:[0,0,0])
resid_agg=collections.defaultdict(lambda:[0,0,0]); pair_agg=collections.defaultdict(lambda:[0,0,0])
for ag,term,clk,cost in rows:
    u,rd=seg(term)
    matched=sum(len(x) for x in u)
    cov[round(matched/max(len(term),1)*10)*10]+=1
    for x in set(u):
        c=unit_agg[(ag,x)]; c[0]+=clk; c[1]+=cost; c[2]+=1
    for x in set(rd):
        if len(x)>=2:
            c=resid_agg[(ag,x)]; c[0]+=clk; c[1]+=cost; c[2]+=1
    for a,b in zip(u,u[1:]):
        c=pair_agg[(ag,f"{a}+{b}")]; c[0]+=clk; c[1]+=cost; c[2]+=1

print(f"\n[분절 커버리지] 검색어 글자 중 사전이 인식한 비율")
tot=sum(cov.values())
for k in sorted(cov,reverse=True)[:6]:
    print(f"  {k:>3}%: {cov[k]:>7,} ({cov[k]/tot*100:5.1f}%)")

for name,d in (("의미단위(1개)",unit_agg),("의미단위 쌍",pair_agg),("★잔여(사전 밖)",resid_agg)):
    surv={k:v for k,v in d.items() if v[0]>=_SS_MIN_CLICK}
    print(f"\n[{name}] 전체 {len(d):,} → clk>={_SS_MIN_CLICK} **{len(surv):,}건**")
    for (ag,g),v in sorted(surv.items(),key=lambda x:-x[1][1])[:10]:
        print(f"    clk={v[0]:>4} cost={v[1]:>8,} 묶인검색어={v[2]:>4}  「{g}」  …{ag[-9:]}")
