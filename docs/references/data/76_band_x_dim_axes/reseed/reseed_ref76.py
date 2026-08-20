#!/usr/bin/env python3
"""ref 76 재분할 검정 (D-NAO-209 후속) — 단일 md5(adgroup_id)%2 분할 1회에 서 있던
ref76 §4의 홀드아웃 주장을 시드 3종으로 재분할해 재현율을 다시 센다.

읽기 전용. prod 접속 없음. 창·집계 정의·필터는 원문(ref76)과 동일하게 유지하고
«분할 시드»만 바꾼다.

입력(전부 로컬 CSV, 수정 없음):
  docs/references/data/63_band_decomposition/band_group_total.csv   (밴드 정본, 391일)
  docs/references/data/76_band_x_dim_axes/raw_q4_axis_by_group.csv  (그룹×dim_type(h/r/m)×dim_value 롤업, 171일)
  docs/references/data/76_band_x_dim_axes/raw_q5_cell_by_group.csv  (그룹×시간×지역×매체 결합 칸, 유료 칸만)

실행: python3 reseed_ref76.py
출력: stdout에 시드별 표. RESEED_ref76_20260820.md의 표는 이 출력에서 옮겨 적었다.
"""
import csv
import hashlib
import os
import statistics
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.dirname(HERE)
BAND_CSV = os.path.join(DATA, "..", "63_band_decomposition", "band_group_total.csv")
Q4_CSV = os.path.join(DATA, "raw_q4_axis_by_group.csv")
Q5_CSV = os.path.join(DATA, "raw_q5_cell_by_group.csv")

SEEDS = {
    "seed0": None,     # 원문 규칙: md5(adgroup_id) % 2 (접두어 없음)
    "s1": "s1:",
    "s2": "s2:",
    "s3": "s3:",
}

TIME_BLOCKS = {
    **{f"{h:02d}": "00-06" for h in range(0, 7)},
    **{f"{h:02d}": "07-09" for h in range(7, 10)},
    **{f"{h:02d}": "10-17" for h in range(10, 18)},
    **{f"{h:02d}": "18-21" for h in range(18, 22)},
    **{f"{h:02d}": "22-23" for h in range(22, 24)},
}


def half(adgroup_id: str, prefix) -> int:
    s = adgroup_id if prefix is None else prefix + adgroup_id
    return int(hashlib.md5(s.encode()).hexdigest(), 16) % 2


def load_band():
    band, ctype = {}, {}
    with open(BAND_CSV) as f:
        for r in csv.DictReader(f):
            band[r["adgroup_id"]] = r["band"]
            ctype[r["adgroup_id"]] = r["campaign_type"]
    return band, ctype


def load_q4():
    """dim_type in {h,r,m} 별 그룹 롤업. rows[dim_type][adgroup_id][dim_value] = (imp,clk,cost,rank_sum)"""
    rows = defaultdict(lambda: defaultdict(dict))
    with open(Q4_CSV) as f:
        for r in csv.DictReader(f):
            rows[r["dim_type"]][r["adgroup_id"]][r["dim_value"]] = {
                "imp": int(r["imp"]), "clk": int(r["clk"]), "cost": int(r["cost"]),
                "rank_sum": int(r["rank_sum"]),
            }
    return rows


def load_q5():
    """결합 칸(유료 칸만): rows[adgroup_id] = list of (hour,region,media,imp,clk,cost)"""
    rows = defaultdict(list)
    with open(Q5_CSV) as f:
        for r in csv.DictReader(f):
            rows[r["adgroup_id"]].append({
                "hour": r["hour_code"], "region": r["region_code"], "media": r["media_code"],
                "imp": int(r["imp"]), "clk": int(r["clk"]), "cost": int(r["cost"]),
            })
    return rows


def group_totals(q4h, groups):
    """그룹별 (imp,clk,cost,rank_sum) 총계 — h축 dim_value 전체 합산(=그룹 전체 트래픽, r·m축과 총합 동일해야 함)."""
    out = {}
    for g in groups:
        d = q4h.get(g, {})
        imp = sum(v["imp"] for v in d.values())
        clk = sum(v["clk"] for v in d.values())
        cost = sum(v["cost"] for v in d.values())
        rank_sum = sum(v["rank_sum"] for v in d.values())
        out[g] = {"imp": imp, "clk": clk, "cost": cost, "rank_sum": rank_sum}
    return out


def band_time_block_share(q4h, groups_by_band):
    """밴드별 시간 블록 비용 몫(§3-2 재현 대상)."""
    out = {}
    for b, gs in groups_by_band.items():
        block_cost = defaultdict(int)
        total = 0
        for g in gs:
            for dv, v in q4h.get(g, {}).items():
                blk = TIME_BLOCKS.get(dv)
                if blk is None:
                    continue
                block_cost[blk] += v["cost"]
                total += v["cost"]
        if total == 0:
            out[b] = {}
            continue
        out[b] = {blk: block_cost.get(blk, 0) / total * 100 for blk in
                   ["00-06", "07-09", "10-17", "18-21", "22-23"]}
    return out


def band_media_concentration(q4m, groups_by_band):
    """밴드별 매체 top3 몫 · HHI (§3-4/§4-B 재현 대상)."""
    out = {}
    for b, gs in groups_by_band.items():
        media_cost = defaultdict(int)
        total = 0
        for g in gs:
            for dv, v in q4m.get(g, {}).items():
                media_cost[dv] += v["cost"]
                total += v["cost"]
        if total == 0:
            out[b] = {"top3": None, "hhi": None, "codes": 0}
            continue
        shares = sorted((c / total for c in media_cost.values()), reverse=True)
        top3 = sum(shares[:3]) * 100
        hhi = sum(s ** 2 for s in shares)
        out[b] = {"top3": top3, "hhi": hhi, "codes": len(media_cost)}
    return out


def band_media_share(q4m, groups_by_band, media="8753"):
    """밴드별 단일 매체(8753) 비용 몫 — §4-B 원문 인용치(57.1/56.8% 등)와 직접 대조."""
    out = {}
    for b, gs in groups_by_band.items():
        media_cost = defaultdict(int)
        total = 0
        for g in gs:
            for dv, v in q4m.get(g, {}).items():
                media_cost[dv] += v["cost"]
                total += v["cost"]
        out[b] = (media_cost.get(media, 0) / total * 100) if total else None
    return out


def band_media8753_ctr(q4m, groups_by_band, media="8753"):
    """§4-C 재현: 최대 매체(8753) 내부 CTR band1 vs band3."""
    out = {}
    for b, gs in groups_by_band.items():
        imp = clk = 0
        for g in gs:
            v = q4m.get(g, {}).get(media)
            if v:
                imp += v["imp"]
                clk += v["clk"]
        out[b] = (clk / imp * 100) if imp else None
    return out


def band_ctr_and_rank(totals, groups_by_band):
    """§4-D 대조군: 밴드 총계 CTR·평균순위(rank_sum/imp)."""
    out = {}
    for b, gs in groups_by_band.items():
        imp = sum(totals[g]["imp"] for g in gs)
        clk = sum(totals[g]["clk"] for g in gs)
        rank_sum = sum(totals[g]["rank_sum"] for g in gs)
        ctr = (clk / imp * 100) if imp else None
        avg_rank = (rank_sum / imp) if imp else None
        out[b] = {"ctr": ctr, "avg_rank": avg_rank, "imp": imp}
    return out


def main():
    band, ctype = load_band()
    q4 = load_q4()
    q4h, q4r, q4m = q4["h"], q4["r"], q4["m"]

    all_groups = sorted(set(q4h.keys()) | set(q4r.keys()) | set(q4m.keys()))
    # SHOPPING 밴드 소속만 (ref76 전체 범위와 동일 — 원문이 이미 SHOPPING 307그룹으로 제한)
    all_groups = [g for g in all_groups if g in band and ctype.get(g) == "SHOPPING"]
    print(f"[검산] SHOPPING 밴드 조인 그룹 = {len(all_groups)} (원문 307과 비교)")

    totals = group_totals(q4h, all_groups)

    for seed_name, prefix in SEEDS.items():
        print(f"\n===== {seed_name} (prefix={prefix!r}) =====")
        for h in (0, 1):
            sub = [g for g in all_groups if half(g, prefix) == h]
            groups_by_band = defaultdict(list)
            for g in sub:
                groups_by_band[band[g]].append(g)
            gb = {b: groups_by_band.get(b, []) for b in ("band1", "band2", "band3", "band4_unjudgeable")}

            print(f"  -- half{h} n(band1/3)={len(gb['band1'])}/{len(gb['band3'])} --")

            # §4-A 시간대 블록 몫 band1 vs band3
            tb = band_time_block_share(q4h, gb)
            for blk in ["00-06", "07-09", "10-17", "18-21", "22-23"]:
                b1 = tb["band1"].get(blk)
                b3 = tb["band3"].get(blk)
                if b1 is not None and b3 is not None:
                    print(f"     시간블록 {blk}: band1={b1:.1f}% band3={b3:.1f}% gap={abs(b1-b3):.1f}pp")

            # §4-B 매체 집중도 (top3·HHI) + 8753 단일 매체 몫(원문 인용치 57.1/56.8% 대조축)
            mc = band_media_concentration(q4m, gb)
            ms8753 = band_media_share(q4m, gb, "8753")
            for b in ("band1", "band3"):
                d = mc[b]
                if d["top3"] is not None:
                    print(f"     매체집중 {b}: top3={d['top3']:.1f}% HHI={d['hhi']:.3f} codes={d['codes']} "
                          f"8753단독몫={ms8753[b]:.1f}%")

            # §4-C 8753 내 CTR
            ctr8753 = band_media8753_ctr(q4m, gb)
            b1c, b3c = ctr8753.get("band1"), ctr8753.get("band3")
            if b1c is not None and b3c is not None:
                print(f"     8753내CTR: band1={b1c:.2f}% band3={b3c:.2f}%")

            # §4-D 대조군: 밴드 총계 CTR·평균순위
            cr = band_ctr_and_rank(totals, gb)
            b1, b3 = cr["band1"], cr["band3"]
            print(f"     총계CTR: band1={b1['ctr']:.3f}% band3={b3['ctr']:.3f}%  "
                  f"평균순위: band1={b1['avg_rank']:.2f} band3={b3['avg_rank']:.2f}")


if __name__ == "__main__":
    main()
