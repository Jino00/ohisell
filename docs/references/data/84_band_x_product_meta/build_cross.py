#!/usr/bin/env python3
"""ⓒ 밴드 × 상품 메타 «현재 단면» 교차표 + 조인율 양방향 (D-NAO-212 · C10).

입력 ①`extract_band_x_product_meta.sql`의 [3] 블록 출력(psv, 헤더 포함)
     ②밴드 정본 CSV `docs/references/data/63_band_decomposition/band_group_total.csv`

★**배분하지 않는다**(D-NAO-194 fan-out 원칙 승계): 한 광고그룹에 상품이 여러 개 붙으므로
  «그룹의 비용»을 상품에 나누면 상한 성질이 깨진다. 그래서 이 표가 세는 것은 **(그룹,상품) 쌍
  수와 distinct 상품 수**이지 금액이 아니다.
★이 표는 **단면**이다 — A/B/C 연관 판정을 내리지 않는다(홀드아웃 규율을 안 탔다).
★WEB_SITE 그룹은 상품이 구조적으로 안 붙는다(쇼핑 소재에서만 mall_product_id가 온다) —
  「미조인」을 결함으로 읽지 않도록 분모를 캠페인 유형별로 갈라 적는다.
"""
import csv, sys
from collections import Counter, defaultdict

pairs_path, band_path = sys.argv[1], sys.argv[2]

band_of, ctype_of = {}, {}
with open(band_path, encoding="utf-8") as f:
    for r in csv.DictReader(f):
        band_of[r["adgroup_id"]] = r["band"]
        ctype_of[r["adgroup_id"]] = r["campaign_type"]

rows = []
with open(pairs_path, encoding="utf-8") as f:
    for r in csv.DictReader(f, delimiter="|"):
        rows.append(r)

def price_band(v):
    if not v: return "(메타 없음)"
    n = int(float(v))
    for hi, label in ((5000,"~5천"), (10000,"5천~1만"), (20000,"1만~2만"),
                      (30000,"2만~3만"), (50000,"3만~5만")):
        if n < hi: return label
    return "5만~"

pair_total = len(rows)
matched = [r for r in rows if r["channel_product_no"]]
in_band = [r for r in rows if r["adgroup_id"] in band_of]

print("## 조인율 (양방향)")
print(f"- (그룹,상품) 쌍 총계: {pair_total} · 그중 메타 매칭 {len(matched)} "
      f"({len(matched)/pair_total*100:.1f}%)" if pair_total else "- 쌍 0건")
print()
print("## 밴드 × 판매상태 (쌍 수 · 배분 없음)")
cross = Counter((band_of.get(r["adgroup_id"], "(밴드 없음)"),
                 r["status_type"] or "(메타 없음)") for r in rows)
bands = sorted({b for b, _ in cross}); stats = sorted({s for _, s in cross})
print("| 밴드 | " + " | ".join(stats) + " | 합 |")
print("|---" * (len(stats) + 2) + "|")
for b in bands:
    cells = [cross[(b, s)] for s in stats]
    print(f"| {b} | " + " | ".join(str(c) for c in cells) + f" | {sum(cells)} |")

print()
print("## 밴드 × 가격대(할인가) — 쌍 수")
pc = Counter((band_of.get(r["adgroup_id"], "(밴드 없음)"),
              price_band(r["discounted_price"] or r["sale_price"])) for r in rows)
pbs = ["~5천", "5천~1만", "1만~2만", "2만~3만", "3만~5만", "5만~", "(메타 없음)"]
pbs = [p for p in pbs if any(b for b in bands if pc[(b, p)])]
print("| 밴드 | " + " | ".join(pbs) + " | 합 |")
print("|---" * (len(pbs) + 2) + "|")
for b in bands:
    cells = [pc[(b, p)] for p in pbs]
    print(f"| {b} | " + " | ".join(str(c) for c in cells) + f" | {sum(cells)} |")

print()
print("## 검산 — Σ(밴드별 쌍) + 밴드 없는 쌍 == 총 쌍")
by_band = Counter(band_of.get(r["adgroup_id"], "(밴드 없음)") for r in rows)
s_band = sum(v for k, v in by_band.items() if k != "(밴드 없음)")
print(f"- Σ밴드 {s_band} + 밴드없음 {by_band['(밴드 없음)']} = {s_band + by_band['(밴드 없음)']} "
      f"(총 쌍 {pair_total}) → {'일치' if s_band + by_band['(밴드 없음)'] == pair_total else '불일치'}")
print()
print("## 캠페인 유형별 매칭 (WEB_SITE는 상품이 구조적으로 안 붙는다)")
by_ct = defaultdict(lambda: [0, 0])
for r in rows:
    ct = ctype_of.get(r["adgroup_id"], "(밴드 없음)")
    by_ct[ct][0] += 1
    if r["channel_product_no"]: by_ct[ct][1] += 1
for ct, (tot, m) in sorted(by_ct.items()):
    print(f"- {ct}: 쌍 {tot} · 매칭 {m} ({m/tot*100:.1f}%)" if tot else f"- {ct}: 0")
