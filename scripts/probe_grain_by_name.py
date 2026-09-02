"""읽기 전용 — 강화판. 상품명 «전체»에서 구성 신호를 뽑아 비교한다."""
import re, sqlite3
from collections import defaultdict
con = sqlite3.connect("file:/home/ubuntu/ohisell/backend/ohisell.db?mode=ro", uri=True)
cur = con.cursor()
rows = cur.execute("""
    SELECT r.id, r.product_name, r.recipe_kind, l.internal_sku, p.product_name, p.cost_price,
           (SELECT std_cost_inc_vat FROM cost_standard cs WHERE cs.recipe_id=r.id LIMIT 1)
    FROM cost_recipe r
    JOIN cost_recipe_link l ON l.recipe_id=r.id AND l.status='approved'
    JOIN product_master p ON p.internal_sku=l.internal_sku
    WHERE r.status='approved'
""").fetchall()
by=defaultdict(list)
for x in rows: by[x[0]].append(x)

def sig(name):
    """구성 신호: N매 전부 + 후면/힌지/내부/외부/세트 유무."""
    n=name or ""
    counts=tuple(sorted(re.findall(r"(\\d+)\\s*매", n)))
    flags=tuple(k for k in ("후면","힌지","내부","외부","세트","트라이") if k in n)
    return (counts, flags)

susp=[]
for rid, items in by.items():
    prices={str(i[5]) for i in items if i[5] is not None}
    if len(prices)!=1: continue
    sigs={sig(i[4]) for i in items}
    if len(sigs)>1: susp.append((rid, items, prices, sigs))
print(f"현재가 1종인 승인 레시피 중 «구성 신호가 여러 종»: {len(susp)}개")
for rid, items, prices, sigs in susp:
    std=items[0][6]
    print(f"  ⚠️ r{rid} 「{items[0][1][:40]}」 kind={items[0][2]} 현재가 {sorted(prices)} 계산값 {std} SKU {len(items)}")
    for s in sorted(sigs, key=str):
        ex=[i for i in items if sig(i[4])==s][:1]
        print(f"       {s}  예: {ex[0][3]} {ex[0][4][:52]}")
con.close()
