# diag_coverage.py — 조망 결합 토대 커버리지 진단 (읽기 전용)
# 목적: 원가/등록수수료율/master 매칭이 낮은 "원인"을 라이브 데이터로 확정 (원칙22, 추정금지).
# 실행: 서버에서 python3 - < diag_coverage.py (sqlite read-only)
import sqlite3, os, sys

DB = os.environ.get("OHISELL_DB", "/home/ubuntu/ohisell/backend/ohisell.db")
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
c = con.cursor()

def q1(sql, *a):
    return c.execute(sql, a).fetchone()[0]

def section(t):
    print("\n" + "=" * 60)
    print(t)
    print("=" * 60)

def tables():
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}

T = tables()
print(f"DB: {DB}")
print(f"tables present: {sorted(t for t in T if 'coupang' in t or t in ('orders','channels','product_master','product_channel_mapping'))}")

# ── A. 마스터(coupang_product_item) 필드 채움률 ──
section("A. coupang_product_item — 필드 채움률 (sync 마스터)")
tot = q1("SELECT COUNT(*) FROM coupang_product_item")
print(f"총 행: {tot}")
if tot:
    for col, cond in [
        ("supply_price 비어있지않음(>0)", "supply_price IS NOT NULL AND supply_price>0"),
        ("supply_price NULL/0", "supply_price IS NULL OR supply_price=0"),
        ("sale_agent_commission 있음", "sale_agent_commission IS NOT NULL"),
        ("sale_agent_commission >0", "sale_agent_commission IS NOT NULL AND sale_agent_commission>0"),
        ("external_vendor_sku 있음", "external_vendor_sku IS NOT NULL AND external_vendor_sku!=''"),
        ("amount_in_stock 있음", "amount_in_stock IS NOT NULL"),
    ]:
        n = q1(f"SELECT COUNT(*) FROM coupang_product_item WHERE {cond}")
        print(f"  {col:38s}: {n:4d} / {tot}  ({100*n//tot}%)")
    print("  계정별:", c.execute("SELECT account_key, COUNT(*) FROM coupang_product_item GROUP BY account_key").fetchall())

# ── B. 거래 옵션ID 유니버스 + 마스터 매칭률 ──
section("B. 거래 옵션ID 유니버스 → coupang_product_item 매칭률")

def universe(label, sql, key_is_int=False):
    ids = {str(r[0]) for r in c.execute(sql) if r[0] not in (None, "", "0")}
    if not ids:
        print(f"  [{label}] 옵션ID 0개")
        return ids
    placeholders = ",".join("?" * len(ids))
    matched = q1(
        f"SELECT COUNT(*) FROM coupang_product_item WHERE vendor_item_id IN ({placeholders})",
        *ids,
    )
    # 매칭된 것 중 원가/등록율 보유
    cost = q1(
        f"SELECT COUNT(*) FROM coupang_product_item WHERE supply_price IS NOT NULL AND supply_price>0 AND vendor_item_id IN ({placeholders})",
        *ids,
    )
    comm = q1(
        f"SELECT COUNT(*) FROM coupang_product_item WHERE sale_agent_commission IS NOT NULL AND sale_agent_commission>0 AND vendor_item_id IN ({placeholders})",
        *ids,
    )
    print(f"  [{label}] 고유 옵션ID {len(ids)} → master매칭 {matched} ({100*matched//len(ids)}%) | 그중 원가보유 {cost} · 등록율보유 {comm}")
    return ids

orders_u = universe("주문(coupang)",
    "SELECT DISTINCT o.platform_product_id FROM orders o JOIN channels ch ON o.channel_id=ch.id WHERE ch.platform='coupang'")
ads_ad = universe("광고-집행옵션",
    "SELECT DISTINCT ad_option_id FROM coupang_ad_option_daily") if "coupang_ad_option_daily" in T else set()
ads_cv = universe("광고-전환옵션",
    "SELECT DISTINCT conv_option_id FROM coupang_ad_option_daily") if "coupang_ad_option_daily" in T else set()
ret_u = universe("반품",
    "SELECT DISTINCT vendor_item_id FROM coupang_return_item") if "coupang_return_item" in T else set()
fee_u = universe("매출내역(수수료)",
    "SELECT DISTINCT vendor_item_id FROM coupang_revenue_fee") if "coupang_revenue_fee" in T else set()

# ── C. 매출내역의 대체 매칭키 (원가 보강 후보) ──
if "coupang_revenue_fee" in T:
    section("C. coupang_revenue_fee — 대체 식별자/실측율 보유율 (원가 보강 후보)")
    ftot = q1("SELECT COUNT(DISTINCT vendor_item_id) FROM coupang_revenue_fee WHERE vendor_item_id IS NOT NULL")
    fsku = q1("SELECT COUNT(DISTINCT vendor_item_id) FROM coupang_revenue_fee WHERE external_seller_sku_code IS NOT NULL AND external_seller_sku_code!=''")
    fratio = q1("SELECT COUNT(DISTINCT vendor_item_id) FROM coupang_revenue_fee WHERE service_fee_ratio IS NOT NULL")
    print(f"  고유 옵션ID(매출내역): {ftot}")
    print(f"  external_seller_sku_code 보유 옵션: {fsku}  ← ProductMaster.internal_sku 매칭 원가 후보")
    print(f"  service_fee_ratio(실측 수수료율) 보유 옵션: {fratio}  ← 실측율은 풍부(등록율 대체 가능?)")

# ── D. 내부 ProductMaster(원가 원천) 현황 ──
section("D. 내부 ProductMaster / 채널매핑 (원가 원천)")
if "product_master" in T:
    pm = q1("SELECT COUNT(*) FROM product_master")
    pmc = q1("SELECT COUNT(*) FROM product_master WHERE cost_price IS NOT NULL AND cost_price>0")
    print(f"  product_master 행: {pm} | 원가(cost_price>0) 보유: {pmc}")
    skus = [r[0] for r in c.execute("SELECT internal_sku FROM product_master WHERE internal_sku IS NOT NULL")]
    print(f"  internal_sku 보유: {len(skus)}  (샘플: {skus[:5]})")
if "product_channel_mapping" in T:
    mp = q1("SELECT COUNT(*) FROM product_channel_mapping")
    mpc = q1("SELECT COUNT(*) FROM product_channel_mapping m JOIN channels ch ON m.channel_id=ch.id WHERE ch.platform='coupang'")
    print(f"  channel_mapping 총: {mp} | coupang 채널 매핑: {mpc}")

# ── E. 교차: master의 external_vendor_sku ↔ ProductMaster.internal_sku 매칭 가능성 ──
section("E. SKU 매칭 가능성 (coupang_product_item.external_vendor_sku ↔ product_master.internal_sku)")
if "product_master" in T:
    cpi_skus = {r[0] for r in c.execute("SELECT DISTINCT external_vendor_sku FROM coupang_product_item WHERE external_vendor_sku IS NOT NULL AND external_vendor_sku!=''")}
    pm_skus = {r[0] for r in c.execute("SELECT DISTINCT internal_sku FROM product_master WHERE internal_sku IS NOT NULL AND internal_sku!=''")}
    inter = cpi_skus & pm_skus
    print(f"  coupang_product_item external_vendor_sku 고유: {len(cpi_skus)}")
    print(f"  product_master internal_sku 고유: {len(pm_skus)}")
    print(f"  교집합(매칭가능 SKU): {len(inter)}  샘플: {list(inter)[:5]}")

con.close()
print("\n[diag] 완료 — 읽기 전용, DB 무변경")
