#!/usr/bin/env python3
"""B2~B4: 사전 적용 분류 + 토큰수/길이 축 + band 교차. 전량 읽기 전용, 로컬 집계만.
입력: q1_agg_out.csv(prod 집계 다운로드), brand_dict_confirmed_merged.csv, band_group_total.csv
"""
import csv, re, json, sys, collections

REPO = "/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling"
OUT = "/private/tmp/claude-501/-Users-jino-Library-Mobile-Documents-com-apple-CloudDocs-1Personal-AI-Program-Ohiselling/78288970-12ad-4647-a554-94288862eca2/scratchpad/a3_brand"
csv.field_size_limit(10**7)

NORM = re.compile(r"[^0-9A-Za-z가-힣]")
def norm(s):
    return NORM.sub("", (s or "")).casefold()

# ── Trie ──
class Trie:
    __slots__ = ("root",)
    def __init__(self):
        self.root = {}
    def insert(self, token):
        node = self.root
        for ch in token:
            node = node.setdefault(ch, {})
        node["$"] = True
    def longest_end(self, s, i):
        node = self.root
        j = i
        last = -1
        while j < len(s):
            ch = s[j]
            if ch not in node:
                break
            node = node[ch]
            j += 1
            if "$" in node:
                last = j
        return last

def segment(s, trie):
    i = 0
    units = []
    while i < len(s):
        end = trie.longest_end(s, i)
        if end == -1:
            i += 1
        else:
            units.append(s[i:end])
            i = end
    matched = sum(len(u) for u in units)
    return units, matched

# ── 1. 브랜드 사전(97 tokens, B1 산출) ──
brand_rows = list(csv.DictReader(open(f"{OUT}/brand_dict_confirmed_merged.csv", encoding="utf-8")))
brand_all = [r["token"] for r in brand_rows]
brand_lowrisk = [r["token"] for r in brand_rows if r["ambiguity_risk"] == "low"]
selfbrand_tokens = [r["token"] for r in brand_rows if r["category"] == "self_brand"]

trie_brand_all = Trie()
for t in brand_all:
    trie_brand_all.insert(t)
trie_brand_low = Trie()
for t in brand_lowrisk:
    trie_brand_low.insert(t)
trie_selfbrand = Trie()
for t in selfbrand_tokens:
    trie_selfbrand.insert(t)

print(f"[dict] brand_all={len(brand_all)} brand_lowrisk={len(brand_lowrisk)} selfbrand={len(selfbrand_tokens)}", file=sys.stderr)

# ── 2. 의미 사전(semantic.py 재현 — B3 의미단위 축) ──
_SS_WHITELIST_TOKENS = ("아이폰", "아이패드", "맥세이프", "강화유리", "지문방지", "보호필름")
R = re.compile(r"[^0-9A-Za-z가-힣]+")
vocab = set()
for t in _SS_WHITELIST_TOKENS:
    if len(t) >= 2:
        vocab.add(t.casefold())
n_has_cost = 0
for r in csv.DictReader(open(f"{REPO}/docs/references/data/67_shopping_upstream_zero/funnel_out4_bep.csv", encoding="utf-8")):
    if str(r.get("has_cost", "")).strip() in ("1", "True", "true"):
        n_has_cost += 1
        for t in R.split(r.get("product_name") or ""):
            if len(t) >= 2 and not t.isdigit():
                vocab.add(t.casefold())
n_group = 0
for r in csv.DictReader(open(f"{REPO}/docs/references/data/66_exclusion_slots/all_shopping_group_counts.csv", encoding="utf-8")):
    n_group += 1
    for t in R.split(r.get("group_name") or ""):
        if len(t) >= 2 and not t.isdigit():
            vocab.add(t.casefold())
print(f"[semantic] 의미 사전 {len(vocab)}개 (product has_cost 행={n_has_cost}, group 행={n_group}) — D-NAO-191 문서 주장 '464개'와 대조", file=sys.stderr)

trie_sem = Trie()
for t in vocab:
    trie_sem.insert(t)

with open(f"{OUT}/semantic_vocab_size.txt", "w") as f:
    f.write(f"{len(vocab)}\n")

# ── 3. band_group_total.csv 로드 ──
band_rows = list(csv.DictReader(open(f"{REPO}/docs/references/data/63_band_decomposition/band_group_total.csv", encoding="utf-8")))
band_map = {r["adgroup_id"]: r for r in band_rows}
print(f"[band] adgroups={len(band_map)}", file=sys.stderr)

# ── 4. q1_agg_out.csv 스트리밍 집계 ──
def new_acc():
    return {"n_combo": 0, "n_daily": 0, "clk": 0, "cost": 0, "conv_cnt": 0, "conv_amt": 0}

def add_acc(acc, clk, cost, conv_cnt, conv_amt, n_rows):
    acc["n_combo"] += 1
    acc["n_daily"] += n_rows
    acc["clk"] += clk
    acc["cost"] += cost
    acc["conv_cnt"] += conv_cnt
    acc["conv_amt"] += conv_amt

# B2: 브랜드 매치 여부 × source
b2 = collections.defaultdict(new_acc)
b2_lowrisk = collections.defaultdict(new_acc)
b2_selfbrand = collections.defaultdict(new_acc)
cov_hist_brand = collections.Counter()  # (source, bucket) -> n_combo

# B3: 길이 버킷, 의미단위 개수 버킷 × source
LEN_BUCKETS = [(1, 3), (4, 6), (7, 10), (11, 15), (16, 20), (21, 10**6)]
def len_bucket(n):
    for lo, hi in LEN_BUCKETS:
        if lo <= n <= hi:
            return f"{lo}-{hi if hi < 10**6 else '+'}"
    return "0"
b3_len = collections.defaultdict(new_acc)
b3_unit = collections.defaultdict(new_acc)
cov_hist_sem = collections.Counter()

# B4: band × campaign_type × brand_match, band × campaign_type × len_bucket, band × campaign_type × unit_bucket
b4_brand = collections.defaultdict(new_acc)
b4_len = collections.defaultdict(new_acc)
b4_unit = collections.defaultdict(new_acc)
join_match = collections.defaultdict(new_acc)  # (source, matched_bool)

# source↔campaign_type 정합성 체크(밴드맵에 있는 adgroup만)
src_ctype_check = collections.Counter()

# B5: 토큰별 매치 검색어 수·비용(전체 사전 97개, 중복매치는 검색어당 토큰별 1회만 가산)
token_stats = collections.defaultdict(new_acc)

path = f"{OUT}/q1_agg_out.csv"
n = 0
with open(path, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        n += 1
        if n % 200000 == 0:
            print(f"...{n:,}", file=sys.stderr)
        ag = row["adgroup_id"]
        term_raw = row["search_term"]
        source = row["source"]
        clk = int(row["clk"] or 0)
        cost = int(row["cost"] or 0)
        conv_cnt = int(row["conv_cnt"] or 0)
        conv_amt = int(row["conv_amt"] or 0)
        n_rows = int(row["n_rows"] or 0)

        term = norm(term_raw)
        tlen = len(term)

        band_r = band_map.get(ag)
        ctype = band_r["campaign_type"] if band_r else None
        band = band_r["band"] if band_r else None

        if band_r:
            src_ctype_check[(source, ctype)] += 1

        # B2: brand match (all-risk 사전)
        units_b, matched_b = segment(term, trie_brand_all) if tlen else ([], 0)
        is_brand = len(units_b) > 0
        add_acc(b2[(source, is_brand)], clk, cost, conv_cnt, conv_amt, n_rows)
        for tok in set(units_b):
            add_acc(token_stats[tok], clk, cost, conv_cnt, conv_amt, n_rows)
        if tlen:
            bucket = round(matched_b / tlen * 10) * 10
            cov_hist_brand[(source, bucket)] += 1

        # low-risk 버전
        units_bl, _ = segment(term, trie_brand_low) if tlen else ([], 0)
        is_brand_lr = len(units_bl) > 0
        add_acc(b2_lowrisk[(source, is_brand_lr)], clk, cost, conv_cnt, conv_amt, n_rows)

        # self-brand
        units_s, _ = segment(term, trie_selfbrand) if tlen else ([], 0)
        is_self = len(units_s) > 0
        add_acc(b2_selfbrand[(source, is_self)], clk, cost, conv_cnt, conv_amt, n_rows)

        # B3: 길이버킷 / 의미단위
        lb = len_bucket(tlen)
        add_acc(b3_len[(source, lb)], clk, cost, conv_cnt, conv_amt, n_rows)

        units_sem, matched_sem = segment(term, trie_sem) if tlen else ([], 0)
        ub = len(units_sem)
        ub_bucket = str(ub) if ub <= 4 else "5+"
        add_acc(b3_unit[(source, ub_bucket)], clk, cost, conv_cnt, conv_amt, n_rows)
        if tlen:
            bucket2 = round(matched_sem / tlen * 10) * 10
            cov_hist_sem[(source, bucket2)] += 1

        # B4: band 교차(campaign_type 있는 것만 의미 있음; 없으면 join miss로 별도 집계)
        matched_join = band_r is not None
        add_acc(join_match[(source, matched_join)], clk, cost, conv_cnt, conv_amt, n_rows)
        if band_r:
            add_acc(b4_brand[(ctype, band, is_brand)], clk, cost, conv_cnt, conv_amt, n_rows)
            add_acc(b4_len[(ctype, band, lb)], clk, cost, conv_cnt, conv_amt, n_rows)
            add_acc(b4_unit[(ctype, band, ub_bucket)], clk, cost, conv_cnt, conv_amt, n_rows)

print(f"[done] total rows processed: {n:,}", file=sys.stderr)

def dump(name, d, key_names):
    with open(f"{OUT}/{name}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(list(key_names) + ["n_combo", "n_daily_rows", "clk", "cost", "conv_cnt", "conv_amt"])
        for k, acc in sorted(d.items(), key=lambda x: -x[1]["cost"]):
            keyvals = list(k) if isinstance(k, tuple) else [k]
            w.writerow(keyvals + [acc["n_combo"], acc["n_daily"], acc["clk"], acc["cost"], acc["conv_cnt"], acc["conv_amt"]])

dump("out_b2_brand_match", b2, ["source", "is_brand"])
dump("out_b2_brand_match_lowrisk", b2_lowrisk, ["source", "is_brand_lowrisk"])
dump("out_b2_selfbrand_match", b2_selfbrand, ["source", "is_selfbrand"])
dump("out_b3_len_bucket", b3_len, ["source", "len_bucket"])
dump("out_b3_unit_bucket", b3_unit, ["source", "unit_bucket"])
dump("out_b4_band_x_brand", b4_brand, ["campaign_type", "band", "is_brand"])
dump("out_b4_band_x_len", b4_len, ["campaign_type", "band", "len_bucket"])
dump("out_b4_band_x_unit", b4_unit, ["campaign_type", "band", "unit_bucket"])
dump("out_join_match_rate", join_match, ["source", "matched_to_band"])

with open(f"{OUT}/out_cov_hist_brand.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["source", "bucket_pct", "n_combo"])
    for (src, b), n_ in sorted(cov_hist_brand.items()):
        w.writerow([src, b, n_])

with open(f"{OUT}/out_cov_hist_semantic.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["source", "bucket_pct", "n_combo"])
    for (src, b), n_ in sorted(cov_hist_sem.items()):
        w.writerow([src, b, n_])

with open(f"{OUT}/out_src_ctype_check.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["source", "campaign_type", "n_combo"])
    for (src, ct), n_ in sorted(src_ctype_check.items()):
        w.writerow([src, ct, n_])

with open(f"{OUT}/out_token_stats.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["token", "n_combo", "n_daily_rows", "clk", "cost", "conv_cnt", "conv_amt"])
    for tok, acc in sorted(token_stats.items(), key=lambda x: -x[1]["cost"]):
        w.writerow([tok, acc["n_combo"], acc["n_daily"], acc["clk"], acc["cost"], acc["conv_cnt"], acc["conv_amt"]])

print("done writing outputs", file=sys.stderr)
