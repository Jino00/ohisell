# diag_bridge.py — 거래 옵션ID ↔ product_channel_mapping ↔ product_master(원가) 다리 검증 (읽기전용)
# 가설: 원가는 coupang_product_item.supply_price(5%)가 아니라 내부 product_master.cost_price(89%)에
#       product_channel_mapping(coupang 1046건)으로 닿는다. 이 다리의 실제 커버리지를 측정.
import sqlite3, os
DB = os.environ.get("OHISELL_DB", "/home/ubuntu/ohisell/backend/ohisell.db")
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); c = con.cursor()

def ids(sql):
    return {str(r[0]) for r in c.execute(sql) if r[0] not in (None, "", "0")}

# coupang 채널 매핑: channel_product_id(=vendorItemId) → cost_price 보유여부
c4 = c.execute("""
  SELECT m.channel_product_id, pm.cost_price
  FROM product_channel_mapping m
  JOIN channels ch ON m.channel_id=ch.id
  JOIN product_master pm ON m.product_id=pm.id
  WHERE ch.platform='coupang'
""").fetchall()
map_all = {str(r[0]) for r in c4 if r[0]}
map_cost = {str(r[0]) for r in c4 if r[0] and r[1] and r[1] > 0}
print(f"coupang 채널매핑 옵션ID 고유: {len(map_all)} | 그중 원가>0: {len(map_cost)}")

universes = {
 "주문": "SELECT DISTINCT o.platform_product_id FROM orders o JOIN channels ch ON o.channel_id=ch.id WHERE ch.platform='coupang'",
 "광고집행": "SELECT DISTINCT ad_option_id FROM coupang_ad_option_daily",
 "반품": "SELECT DISTINCT vendor_item_id FROM coupang_return_item",
 "매출내역": "SELECT DISTINCT vendor_item_id FROM coupang_revenue_fee",
}
print("\n거래 유니버스 → 매핑다리 커버리지 (원가 닿는 비율):")
for label, sql in universes.items():
    u = ids(sql)
    if not u:
        print(f"  [{label}] 0개"); continue
    via_map = u & map_all
    via_cost = u & map_cost
    print(f"  [{label}] {len(u):4d} → 매핑매칭 {len(via_map):4d} ({100*len(via_map)//len(u)}%) | 원가닿음 {len(via_cost):4d} ({100*len(via_cost)//len(u)}%)")

# 합집합(조망 전체 옵션) 커버리지
allu = set()
for sql in universes.values():
    allu |= ids(sql)
print(f"\n조망 전체 거래옵션(합집합) {len(allu)} → 매핑매칭 {len(allu & map_all)} | 원가닿음 {len(allu & map_cost)}")

# 현재 vs 잠재 원가 커버리지 비교
cur_cost_ids = ids("SELECT vendor_item_id FROM coupang_product_item WHERE supply_price IS NOT NULL AND supply_price>0")
print(f"\n현재 원가원천(coupang_product_item.supply_price>0) 옵션: {len(cur_cost_ids)}")
print(f"  그중 실거래 합집합에 등장: {len(cur_cost_ids & allu)}")
print(f"잠재 원가원천(매핑→product_master.cost) 옵션 중 실거래 등장: {len(allu & map_cost)}  ← 개선 잠재력")

con.close()
print("\n[diag-bridge] 완료 — 읽기전용")
