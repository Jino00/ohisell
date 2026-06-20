#!/usr/bin/env python3
# 원가 누락 10옵션 일괄 등록 (Wing 매출 정합 트랙 S2 사전작업, 2026-06-20).
# Jino 확정 원가: 버디필름 10장=2151/20장=4131/30장=6111, EZ툴 프라이버시=4880, 3D풀커버=2400.
# 멱등: 같은 (vid, channel 1) 활성 매핑이 이미 있으면 건너뜀. 실행 전 영향 행 백업(JSON).
import json
import sqlite3
import sys
from datetime import datetime

DB = "/home/ubuntu/ohisell/backend/ohisell.db"
CH = 1  # COUPANG_WING1 (오픽스)

# (vid, product_master_id|None for new30, selling_price, label)
NEW30_SKU = "OHI-BUDDY-BIGORI-30MAE"
NEW30_NAME = "[30매] 오하이 버디필름 장타 비거리스티커 드라이버 & 우드 헤드 보호 슬라이스방지"
NEW30_COST = 6111

# product_id 'NEW30' = 신규 30장 master(런타임 해소)
TARGETS = [
    ("95571078149", 567, 14200, "버디필름 우드 1개=10장 2151"),
    ("95571078150", 567, 14200, "버디필름 드라이버 1개=10장 2151"),
    ("95571078155", 566, 26580, "버디필름 우드 2개=20장 4131"),
    ("95571078154", "NEW30", 38950, "버디필름 우드 3개=30장 6111"),
    ("95571078156", "NEW30", 38950, "버디필름 드라이버 3개=30장 6111"),
    ("95535536290", 711, 18900, "EZ툴 프라이버시 아이폰17 4880"),
    ("95535536294", 708, 18900, "EZ툴 프라이버시 아이폰Air 4880"),
    ("95535536316", 715, 18900, "EZ툴 프라이버시 아이폰16 4880"),
    ("95570603427", 899, 15900, "전면3D풀커버 TPU 3p 2400"),
    ("95570603438", 899, 15900, "전면3D풀커버 TPU +가이드 2400"),
]
# 기존 잘못 매핑 교정: 95571078153(드라이버 2개=20장) 901(4400)→566(4131)
FIX_MAP_ID = 2737
FIX_FROM_PRODUCT = 901
FIX_TO_PRODUCT = 566


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── 0. 백업(영향 행) ──────────────────────────────────────────
    vids = [t[0] for t in TARGETS] + ["95571078153"]
    qmarks = ",".join("?" * len(vids))
    backup = {
        "mappings": [dict(r) for r in cur.execute(
            f"SELECT * FROM product_channel_mapping WHERE channel_product_id IN ({qmarks})", vids)],
        "fix_map_row": [dict(r) for r in cur.execute(
            "SELECT * FROM product_channel_mapping WHERE id=?", (FIX_MAP_ID,))],
    }
    bpath = f"/home/ubuntu/ohisell/backend/scripts/backup_costs_{stamp}.json"
    with open(bpath, "w") as f:
        json.dump(backup, f, ensure_ascii=False, indent=2, default=str)
    print(f"[backup] {bpath} (mappings={len(backup['mappings'])})")

    # ── 1. 30장 신규 master (멱등: internal_sku로 조회) ─────────────
    row = cur.execute("SELECT id FROM product_master WHERE internal_sku=?", (NEW30_SKU,)).fetchone()
    if row:
        new30_id = row["id"]
        print(f"[master] 30장 master 이미 존재 id={new30_id}")
    else:
        cur.execute(
            "INSERT INTO product_master (internal_sku, product_name, cost_price, memo) VALUES (?,?,?,?)",
            (NEW30_SKU, NEW30_NAME, NEW30_COST, "auto 2026-06-20 Jino 확정 30장=6111"))
        new30_id = cur.lastrowid
        print(f"[master] 30장 master 신규 생성 id={new30_id} cost={NEW30_COST}")

    # ── 2. 매핑 삽입 (멱등) ───────────────────────────────────────
    inserted, skipped = 0, 0
    for vid, pid, sp, label in TARGETS:
        product_id = new30_id if pid == "NEW30" else pid
        exists = cur.execute(
            "SELECT id FROM product_channel_mapping WHERE channel_product_id=? AND channel_id=? AND is_active=1",
            (vid, CH)).fetchone()
        if exists:
            print(f"[map] SKIP {vid} (이미 활성매핑 id={exists['id']}) — {label}")
            skipped += 1
            continue
        cur.execute(
            "INSERT INTO product_channel_mapping "
            "(product_id, channel_id, channel_product_id, selling_price, is_active) VALUES (?,?,?,?,1)",
            (product_id, CH, vid, sp))
        print(f"[map] INSERT {vid} → master {product_id} sp={sp} — {label}")
        inserted += 1

    # ── 3. 기존 오매핑 교정 (조건부) ───────────────────────────────
    frow = cur.execute("SELECT product_id FROM product_channel_mapping WHERE id=?", (FIX_MAP_ID,)).fetchone()
    if frow and frow["product_id"] == FIX_FROM_PRODUCT:
        cur.execute("UPDATE product_channel_mapping SET product_id=? WHERE id=?", (FIX_TO_PRODUCT, FIX_MAP_ID))
        print(f"[fix] map {FIX_MAP_ID}: product {FIX_FROM_PRODUCT}(4400)→{FIX_TO_PRODUCT}(4131)")
    elif frow:
        print(f"[fix] SKIP map {FIX_MAP_ID} (현재 product_id={frow['product_id']}, 이미 교정/변경됨)")

    con.commit()
    print(f"\n[done] inserted={inserted} skipped={skipped}")
    con.close()


if __name__ == "__main__":
    main()
