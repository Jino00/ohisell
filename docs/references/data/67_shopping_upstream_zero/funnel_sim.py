#!/usr/bin/env python3
"""읽기 전용 게이트 시뮬레이션 — search_term_judge.judge_search_terms 로직을 CSV로부터 재현.
쓰기 없음. prod DB에 손대지 않음. 코드 원본: backend/app/services/naver_ad/search_term_judge.py
"""
import csv, re, sys
from collections import defaultdict
from decimal import Decimal

SCRATCH = "/private/tmp/claude-501/-Users-jino-Library-Mobile-Documents-com-apple-CloudDocs-1Personal-AI-Program-Ohiselling/da48087f-ae21-4a2a-bd0f-595bd9fe31a1/scratchpad"

_SS_MIN_CLICK = 10
_SS_WHITELIST_TOKENS = ("아이폰", "아이패드", "맥세이프", "강화유리", "지문방지", "보호필름")
_TOKEN_SPLIT_RE = re.compile(r"[^0-9A-Za-z가-힣]+")
_POWERLINK_SOURCE = "expkeyword"


def build_whitelist(bep_rows):
    tokens = {t.casefold() for t in _SS_WHITELIST_TOKENS}
    for name, has_cost in bep_rows:
        if not has_cost:
            continue
        if not name:
            continue
        for tok in _TOKEN_SPLIT_RE.split(name):
            if len(tok) >= 2 and not tok.isdigit():
                tokens.add(tok.casefold())
    return tokens


def is_whitelisted(term, tokens):
    if not term:
        return False
    norm = term.casefold()
    for tok in tokens:
        if tok in norm:
            return tok
    return None


def load_bep():
    rows = []  # (product_name, has_cost bool)
    margin_by_cpid = {}
    with open(f"{SCRATCH}/funnel_out4_bep.csv", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            has_cost = row["has_cost"] == "1"
            rows.append((row["product_name"], has_cost))
            if has_cost:
                cpid = row["channel_product_id"]
                cm = row["contribution_margin"]
                margin_by_cpid[cpid] = Decimal(cm) if cm not in (None, "") else None
    return rows, margin_by_cpid


def load_adgroup_product():
    m = defaultdict(list)  # adgroup_id -> [mall_product_id,...] (order preserved, dedup later)
    with open(f"{SCRATCH}/funnel_out5_adgroup_product.csv", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            m[row["adgroup_id"]].append(row["mall_product_id"])
    return m


def load_order_revenue():
    rev = {}
    with open(f"{SCRATCH}/funnel_out6_order_revenue.csv", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            rev[row["platform_product_id"]] = Decimal(row["rev"]) if row["rev"] not in (None, "") else Decimal(0)
    return rev


def weighted_margin_for_adgroup(adgroup_id, adgroup_product_map, margin_by_cpid, order_rev):
    """campaign_target_resolver.weighted_product_value_for_adgroup(contribution_margin) 재현.
    has_cost=True 상품만, 매출가중, 매출 0이면 단순평균. cpid 목록 비었거나 매칭 상품 0개면 None."""
    cpids = adgroup_product_map.get(adgroup_id)
    if not cpids:
        return None
    uniq = list(dict.fromkeys(cpids))
    rows = [(pid, margin_by_cpid[pid]) for pid in uniq if pid in margin_by_cpid and margin_by_cpid[pid] is not None]
    if not rows:
        return None
    weighted_sum = Decimal(0)
    weight_total = Decimal(0)
    for pid, val in rows:
        rev = order_rev.get(pid, Decimal(0))
        weighted_sum += val * rev
        weight_total += rev
    if weight_total > 0:
        return weighted_sum / weight_total
    return sum((v for _, v in rows), Decimal(0)) / len(rows)


def run_funnel(window_csv, label):
    bep_rows, margin_by_cpid = load_bep()
    adgroup_product_map = load_adgroup_product()
    order_rev = load_order_revenue()
    whitelist = build_whitelist(bep_rows)

    margin_cache = {}
    def get_margin(adgroup_id):
        if adgroup_id not in margin_cache:
            margin_cache[adgroup_id] = weighted_margin_for_adgroup(adgroup_id, adgroup_product_map, margin_by_cpid, order_rev)
        return margin_cache[adgroup_id]

    stats = {}
    for src in ("shopping", "expkeyword", "ALL"):
        stats[src] = {
            "g0": 0, "g1_drop": 0, "g2_drop": 0, "g3_drop": 0, "g4_drop": 0, "g5_drop": 0, "g6_drop": 0,
            "survive_all_gates": 0,
        }

    clk_hist = defaultdict(int)  # for shopping only, post-G1/G2 survivors' clk distribution
    clk_hist_max = 0
    whitelist_hits = defaultdict(int)  # token -> count of shopping rows dropped by it (post G1 survivors)
    margin_none_adgroups = set()
    margin_present_adgroups = set()

    # sensitivity counters (post all other gates, varying one gate)
    sens = {
        "g2_off_survive": 0,       # skip G2 entirely
        "g3_clk5_survive": 0,      # G3 threshold clk>=5 instead of 10
        "g4_failopen_survive": 0,  # skip G4 (and thus G5 uses cost<margin only if margin exists, else treat as pass)
    }

    full_pass_rows = []

    with open(window_csv, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            src = row["source"]
            campaign_id = row["campaign_id"]
            adgroup_id = row["adgroup_id"]
            term = row["search_term"]
            clk = int(row["clk"])
            cost = int(row["cost"])
            pconv = int(row["conv_purchase_cnt"])

            for key in (src, "ALL"):
                stats[key]["g0"] += 1

            # G1
            if pconv >= 1:
                for key in (src, "ALL"):
                    stats[key]["g1_drop"] += 1
                continue

            # G2 whitelist
            hit = is_whitelisted(term, whitelist)
            if hit:
                for key in (src, "ALL"):
                    stats[key]["g2_drop"] += 1
                if src == "shopping":
                    whitelist_hits[hit] += 1
            else:
                # record clk distribution among G1+G2 survivors (shopping only)
                if src == "shopping":
                    if clk == 0:
                        clk_hist["0"] += 1
                    elif clk == 1:
                        clk_hist["1"] += 1
                    elif clk <= 4:
                        clk_hist["2-4"] += 1
                    elif clk <= 9:
                        clk_hist["5-9"] += 1
                    else:
                        clk_hist["10+"] += 1
                    clk_hist_max = max(clk_hist_max, clk)

            # sensitivity: g2 off -> continue past whitelist as if not hit
            # (compute independently below with fresh chain per row)

            if hit:
                continue

            # G3 click
            if clk < _SS_MIN_CLICK:
                for key in (src, "ALL"):
                    stats[key]["g3_drop"] += 1
                pass_g3 = False
            else:
                pass_g3 = True

            # For sensitivity g3_clk5: independent check
            if not hit and pconv < 1 and clk >= 5:
                margin_s = get_margin(adgroup_id)
                if margin_s is not None and margin_s > 0 and cost >= margin_s and src != _POWERLINK_SOURCE:
                    sens["g3_clk5_survive"] += 1

            if not pass_g3:
                continue

            # G4 margin existence
            margin = get_margin(adgroup_id)
            if margin is None or margin <= 0:
                for key in (src, "ALL"):
                    stats[key]["g4_drop"] += 1
                margin_none_adgroups.add(adgroup_id)
                pass_g4 = False
            else:
                pass_g4 = True
                margin_present_adgroups.add(adgroup_id)

            # sensitivity: g4 fail-open (treat missing margin as pass, use cost>=0 -> always pass g5 if margin missing)
            if not hit and pconv < 1 and clk >= _SS_MIN_CLICK:
                if margin is None or margin <= 0:
                    # fail-open: g5 auto-pass (no min_cost to compare) -> survives g4/g5 if not powerlink
                    if src != _POWERLINK_SOURCE:
                        sens["g4_failopen_survive"] += 1
                else:
                    if cost >= margin and src != _POWERLINK_SOURCE:
                        sens["g4_failopen_survive"] += 1

            if not pass_g4:
                continue

            # G5 cost >= margin
            if cost < margin:
                for key in (src, "ALL"):
                    stats[key]["g5_drop"] += 1
                continue

            # G6 type separation
            if src == _POWERLINK_SOURCE:
                for key in (src, "ALL"):
                    stats[key]["g6_drop"] += 1
                continue

            for key in (src, "ALL"):
                stats[key]["survive_all_gates"] += 1
            full_pass_rows.append(row)

    # g2_off sensitivity: rows that fail ONLY g2 (i.e. pass g1, are whitelisted, but would otherwise pass g3/g4/g5, not powerlink)
    # recompute in a second pass restricted to whitelisted+shopping rows already excluded above; simplest: rerun with g2 skipped entirely
    with open(window_csv, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            src = row["source"]
            if src == _POWERLINK_SOURCE:
                continue
            pconv = int(row["conv_purchase_cnt"])
            if pconv >= 1:
                continue
            clk = int(row["clk"])
            if clk < _SS_MIN_CLICK:
                continue
            adgroup_id = row["adgroup_id"]
            margin = get_margin(adgroup_id)
            if margin is None or margin <= 0:
                continue
            cost = int(row["cost"])
            if cost < margin:
                continue
            sens["g2_off_survive"] += 1

    return {
        "label": label,
        "stats": stats,
        "clk_hist": dict(clk_hist),
        "clk_hist_max": clk_hist_max,
        "whitelist_hits": dict(sorted(whitelist_hits.items(), key=lambda x: -x[1])[:20]),
        "margin_none_adgroups": len(margin_none_adgroups),
        "margin_present_adgroups": len(margin_present_adgroups),
        "sensitivity": sens,
        "full_pass_rows": full_pass_rows,
    }


def print_report(res):
    print(f"\n===== {res['label']} =====")
    for src in ("shopping", "expkeyword", "ALL"):
        s = res["stats"][src]
        g0 = s["g0"]
        print(f"-- source={src} g0(entry)={g0}")
        running = g0
        for gate in ("g1_drop", "g2_drop", "g3_drop", "g4_drop", "g5_drop", "g6_drop"):
            d = s[gate]
            running -= d
            print(f"   {gate}: dropped={d}  survive_after={running}")
        print(f"   survive_all_gates(reported)={s['survive_all_gates']}  (check running={running})")
    print(f" clk histogram (post G1+G2 survivors, shopping): {res['clk_hist']}  max_clk={res['clk_hist_max']}")
    print(f" top whitelist tokens (shopping, rows dropped by each): {res['whitelist_hits']}")
    print(f" margin adgroups: present={res['margin_present_adgroups']} none={res['margin_none_adgroups']}")
    print(f" sensitivity: {res['sensitivity']}")
    print(f" full_pass_rows count: {len(res['full_pass_rows'])}")
    for row in res["full_pass_rows"][:20]:
        print("   PASS:", row)


if __name__ == "__main__":
    res14 = run_funnel(f"{SCRATCH}/funnel_out2_group14.csv", "14-day window (2026-08-04~2026-08-17)")
    print_report(res14)
    res90 = run_funnel(f"{SCRATCH}/funnel_out3_group90.csv", "90-day window (2026-05-20~2026-08-17)")
    print_report(res90)
