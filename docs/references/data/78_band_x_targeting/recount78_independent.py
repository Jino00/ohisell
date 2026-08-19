#!/usr/bin/env python3
"""ref 78 독립 재계산 — 위임 배경의 관측 1~6을 원 CSV에서 처음부터 다시 세고,
그룹 집합 md5 짝/홀 홀드아웃 + 캠페인 유형 통제를 수행한다.
실행: python3 recount78_independent.py  (repo 루트 기준 상대경로 사용)
입력: 같은 폴더의 out_black_by_group.csv · out_media_block_vs_perf.csv
      + docs/references/data/63_band_decomposition/band_group_total.csv
쓰기 없음(읽기 전용)."""
import csv, statistics, hashlib, os
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
BAND = os.path.join(HERE, "..", "63_band_decomposition", "band_group_total.csv")

def pv(x): return None if x == "" else int(x)

groups = {}
with open(os.path.join(HERE, "out_black_by_group.csv")) as f:
    for r in csv.DictReader(f, delimiter="|"):
        groups[r["adgroup_id"]] = {"blk": int(r["black_media_count"]), "pc": pv(r["pc"]),
            "mob": pv(r["mobile"]), "imp": int(r["imp"]), "clk": int(r["clk"]),
            "cost": int(r["cost"]), "n_days": int(r["n_days"])}
band, ctype = {}, {}
with open(BAND) as f:
    for r in csv.DictReader(f):
        band[r["adgroup_id"]] = r["band"]; ctype[r["adgroup_id"]] = r["campaign_type"]
joined = {g: d for g, d in groups.items() if g in band}

def cat(d):
    if d["pc"] is None: return "미설정"
    return {(1,1):"둘다",(0,1):"모바일만",(1,0):"PC만",(0,0):"둘다꺼짐"}[(d["pc"],d["mob"])]
def half(g): return int(hashlib.md5(g.encode()).hexdigest(), 16) % 2

print(f"전수 {len(groups)} · 밴드 정본 {len(band)} · 조인 {len(joined)} ({len(joined)/len(groups)*100:.1f}%)")
c6 = Counter(cat(d) for d in groups.values())
print(f"A6 전수: {dict(c6)}")
for b in sorted(set(band[g] for g in joined)):
    v = [joined[g]["blk"] for g in joined if band[g] == b]
    print(f"A5 {b}: n={len(v)} med={statistics.median(v)} mean={statistics.mean(v):.2f} max={max(v)} 블랙0={sum(1 for x in v if x==0)}")
for ct in sorted(set(ctype[g] for g in joined)):
    cnt = Counter(cat(joined[g]) for g in joined if ctype[g] == ct)
    n = sum(cnt.values())
    print(f"A6 {ct}: n={n} {dict(cnt)} 둘다비율={cnt['둘다']/n*100:.1f}%")
print("\n-- 홀드아웃(md5 짝/홀) × 유형 통제 --")
for ct in ("SHOPPING", "WEB_SITE"):
    for h in (0, 1):
        sub = {g: d for g, d in joined.items() if ctype[g] == ct and half(g) == h}
        parts = []
        for b in ("band1", "band2", "band3", "band4_unjudgeable"):
            v = [sub[g]["blk"] for g in sub if band[g] == b]
            gs = [g for g in sub if band[g] == b]
            b11 = sum(1 for g in gs if cat(sub[g]) == "둘다")
            if v: parts.append(f"{b} A5med={statistics.median(v)} A6둘다={b11}/{len(gs)}")
        print(f"{ct} half{h}: " + "  ".join(parts))
rows = []
with open(os.path.join(HERE, "out_media_block_vs_perf.csv")) as f:
    for r in csv.DictReader(f, delimiter="|"):
        rows.append({"code": r["code"], "bg": int(r["grps_blocking"]), "dg": int(r["grps_delivered"]), "cost": int(r["cost"])})
both = [r for r in rows if r["bg"] > 0 and r["dg"] > 0]
del_only = [r for r in rows if r["bg"] == 0 and r["dg"] > 0]
blk_only = [r for r in rows if r["bg"] > 0 and r["dg"] == 0]
tc = sum(r["cost"] for r in rows)
print(f"\n코드 합집합 {len(rows)}: 둘다 {len(both)}({sum(r['cost'] for r in both)/tc*100:.1f}%) 차단만 {len(blk_only)} 송출만 {len(del_only)}({sum(r['cost'] for r in del_only)/tc*100:.1f}%)")
