#!/usr/bin/env python3
"""ref 78 재분할 검정 (D-NAO-209 후속) — 단일 md5(adgroup_id)%2 분할 1회(seed0,
recount78_independent.py의 half())에 서 있던 ref78 §2·§3의 홀드아웃 주장을
시드 3종으로 재분할해 재현율을 다시 센다.

읽기 전용. prod 접속 없음. 창·집계 정의·필터는 원문(ref78)과 동일하게 유지하고
«분할 시드»만 바꾼다.

입력(전부 로컬 CSV, 수정 없음):
  docs/references/data/63_band_decomposition/band_group_total.csv        (밴드 정본, 391일)
  docs/references/data/78_band_x_targeting/out_black_by_group.csv        (그룹×블랙개수×A6, 1,013그룹)

실행: python3 reseed_ref78.py
출력: stdout에 시드별 표. RESEED_ref78_20260820.md의 표는 이 출력에서 옮겨 적었다.
"""
import csv
import hashlib
import os
import statistics
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.dirname(HERE)
BAND_CSV = os.path.join(DATA, "..", "63_band_decomposition", "band_group_total.csv")
BLACK_CSV = os.path.join(DATA, "out_black_by_group.csv")

SEEDS = {
    "seed0": None,   # 원문 규칙: recount78_independent.py의 half() = md5(adgroup_id) % 2 (접두어 없음)
    "s1": "s1:",
    "s2": "s2:",
    "s3": "s3:",
}

BANDS = ("band1", "band2", "band3", "band4_unjudgeable")


def pv(x):
    return None if x == "" else int(x)


def cat(d):
    if d["pc"] is None:
        return "미설정"
    return {(1, 1): "둘다", (0, 1): "모바일만", (1, 0): "PC만", (0, 0): "둘다꺼짐"}[(d["pc"], d["mob"])]


def half(adgroup_id: str, prefix) -> int:
    s = adgroup_id if prefix is None else prefix + adgroup_id
    return int(hashlib.md5(s.encode()).hexdigest(), 16) % 2


def load():
    groups = {}
    with open(BLACK_CSV) as f:
        for r in csv.DictReader(f, delimiter="|"):
            groups[r["adgroup_id"]] = {
                "blk": int(r["black_media_count"]), "pc": pv(r["pc"]), "mob": pv(r["mobile"]),
                "imp": int(r["imp"]), "clk": int(r["clk"]), "cost": int(r["cost"]),
                "n_days": int(r["n_days"]),
            }
    band, ctype = {}, {}
    with open(BAND_CSV) as f:
        for r in csv.DictReader(f):
            band[r["adgroup_id"]] = r["band"]
            ctype[r["adgroup_id"]] = r["campaign_type"]
    joined = {g: d for g, d in groups.items() if g in band}
    return groups, band, ctype, joined


def a5_median_by_type_band(joined, band, ctype, prefix):
    """§2-2 재현: 유형 통제 후 밴드별 블랙 개수 중앙값, half0/half1."""
    out = {}
    for ct in ("WEB_SITE", "SHOPPING"):
        for h in (0, 1):
            sub = {g: d for g, d in joined.items() if ctype[g] == ct and half(g, prefix) == h}
            for b in BANDS:
                v = [sub[g]["blk"] for g in sub if band[g] == b]
                if v:
                    out[(ct, b, h)] = {"median": statistics.median(v), "mean": statistics.mean(v), "n": len(v)}
    return out


def a6_both_ratio_by_type_band(joined, band, ctype, prefix):
    """§3-2 재현: 유형 통제 후 밴드별 A6 '둘다' 비율, half0/half1."""
    out = {}
    for ct in ("WEB_SITE", "SHOPPING"):
        for h in (0, 1):
            sub = {g: d for g, d in joined.items() if ctype[g] == ct and half(g, prefix) == h}
            for b in BANDS:
                gs = [g for g in sub if band[g] == b]
                if gs:
                    both = sum(1 for g in gs if cat(sub[g]) == "둘다")
                    out[(ct, b, h)] = {"both_pct": both / len(gs) * 100, "n": len(gs)}
    return out


def main():
    groups, band, ctype, joined = load()
    print(f"[검산] 전수 {len(groups)} · 밴드 정본 {len(band)} · 조인 {len(joined)} ({len(joined)/len(groups)*100:.1f}%)")

    for seed_name, prefix in SEEDS.items():
        print(f"\n===== {seed_name} (prefix={prefix!r}) =====")

        a5 = a5_median_by_type_band(joined, band, ctype, prefix)
        print("  -- §2-2 A5 유형통제 밴드별 중앙값(half0/half1) --")
        for ct in ("WEB_SITE", "SHOPPING"):
            row = []
            for b in BANDS:
                d0 = a5.get((ct, b, 0))
                d1 = a5.get((ct, b, 1))
                if d0 and d1:
                    row.append(f"{b}={d0['median']}/{d1['median']}(n={d0['n']}/{d1['n']})")
            print(f"     {ct}: " + "  ".join(row))

        print("  -- §2-3 대조군: SHOPPING band4 평균(half0/half1) --")
        d0 = a5.get(("SHOPPING", "band4_unjudgeable", 0))
        d1 = a5.get(("SHOPPING", "band4_unjudgeable", 1))
        if d0 and d1:
            print(f"     band4 평균: half0={d0['mean']:.1f}(n={d0['n']}) half1={d1['mean']:.1f}(n={d1['n']})")

        a6 = a6_both_ratio_by_type_band(joined, band, ctype, prefix)
        print("  -- §3-2 A6 WEB_SITE '둘다' 비율(half0/half1) --")
        for b in BANDS:
            d0 = a6.get(("WEB_SITE", b, 0))
            d1 = a6.get(("WEB_SITE", b, 1))
            if d0 and d1:
                print(f"     {b}: {d0['both_pct']:.0f}%/{d1['both_pct']:.0f}% (n={d0['n']}/{d1['n']})")
        print("  -- §3-2 A6 SHOPPING '둘다' 비율(half0/half1, 대조·교차불능 확인) --")
        for b in BANDS:
            d0 = a6.get(("SHOPPING", b, 0))
            d1 = a6.get(("SHOPPING", b, 1))
            if d0 and d1:
                print(f"     {b}: {d0['both_pct']:.0f}%/{d1['both_pct']:.0f}% (n={d0['n']}/{d1['n']})")


if __name__ == "__main__":
    main()
