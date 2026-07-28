#!/usr/bin/env python3
# 프로모션 손익 레이어 Phase 1 원가 시드 (트랙 coupang-promo-pnl, 2026-07-28).
#
# 왜 필요한가: 프로모션 기간 손익(Phase 2)을 계산하려면 1P SKU의 원가가 닿아 있어야 한다.
#   1P는 발주상세 상품번호 → rocket_product_cost_map → product_master.internal_sku → cost_price 경로다.
#
# Jino 확정값(대화, VAT 포함):
#   - SKU 62178970 (강화유리 아이폰17프로)     원가 3,500
#   - SKU 69411570 (S26울트라 지문방지필름)   원가 2,351
#
# prod 실측(2026-07-28 SELECT):
#   - 62178970: rocket_product_cost_map → OHI-TGLASS-IP17PRO 매핑 **이미 존재**.
#               product_master.cost_price = 3,400 → **3,500으로 갱신 필요**.
#   - 69411570: 발주 이력 0건(신상품) → 매핑 없음. cost_price 2,351은 OHI-0497
#               (오하이 빛반사, 지문방지 매트 필름 3매, 갤럭시S26울트라)에 **이미 그 값으로** 존재.
#               → 매핑만 선등록하면 된다. 발주상세 행이 없어도 매핑 등록은 가능하다
#                 (rocket_cost_map.upsert_mapping이 라벨 캐시를 optional로 다룸 — 코드 실측).
#
# 멱등: 이미 원하는 상태면 건너뛴다. 기본 --dry-run(실제 쓰기는 --apply 명시).
# 실행 위치: prod 서버(같은 DB 파일). 이 스크립트는 저장소에만 두고 실행은 배포 시점에.
#   $ python3 backend/scripts/seed_promo_pnl_costs_20260728.py            # dry-run
#   $ python3 backend/scripts/seed_promo_pnl_costs_20260728.py --apply
import argparse
import json
import sqlite3
import sys
from datetime import datetime

DEFAULT_DB = "/home/ubuntu/ohisell/backend/ohisell.db"

# (product_number, internal_sku, cost_price, 설명)
TARGETS = [
    ("62178970", "OHI-TGLASS-IP17PRO", 3500, "강화유리 아이폰17프로 (cost 3400→3500 갱신)"),
    ("69411570", "OHI-0497", 2351, "S26울트라 지문방지필름 (신상품 — 매핑 선등록)"),
]


def _fetch_state(cur) -> dict:
    """대상 SKU의 현재 상태(매핑·원가) 스냅샷 — 변경 전 백업 겸 판정 근거."""
    state = {}
    for pn, sku, _cost, _label in TARGETS:
        cur.execute(
            "SELECT product_number, internal_sku, status FROM rocket_product_cost_map "
            "WHERE product_number = ?", (pn,),
        )
        m = cur.fetchone()
        cur.execute(
            "SELECT internal_sku, product_name, cost_price FROM product_master "
            "WHERE internal_sku = ?", (sku,),
        )
        p = cur.fetchone()
        state[pn] = {
            "mapping": dict(m) if m else None,
            "master": dict(p) if p else None,
        }
    return state


def main() -> int:
    ap = argparse.ArgumentParser(description="프로모션 손익 Phase 1 원가 시드(멱등)")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"SQLite 경로(기본 {DEFAULT_DB})")
    ap.add_argument("--apply", action="store_true", help="실제 쓰기(미지정 시 dry-run)")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    before = _fetch_state(cur)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"[before] {json.dumps(before, ensure_ascii=False, default=str)}")

    planned: list[str] = []
    for pn, sku, cost, label in TARGETS:
        st = before[pn]
        if st["master"] is None:
            print(f"  ✗ {pn}: product_master에 internal_sku={sku} 없음 — 건너뜀(수동 확인 필요)")
            continue
        # ① 원가 갱신(다를 때만)
        cur_cost = st["master"]["cost_price"]
        if cur_cost is None or int(cur_cost) != cost:
            planned.append(f"UPDATE product_master SET cost_price={cost} WHERE internal_sku='{sku}' (현재 {cur_cost})")
            if args.apply:
                cur.execute(
                    "UPDATE product_master SET cost_price = ? WHERE internal_sku = ?", (cost, sku)
                )
        else:
            print(f"  = {pn}: cost_price 이미 {cost} — 변경 없음")
        # ② 매핑 등록(없거나 다를 때만)
        m = st["mapping"]
        if m is None:
            planned.append(f"INSERT rocket_product_cost_map {pn} → {sku} (confirmed/manual)")
            if args.apply:
                cur.execute(
                    "INSERT INTO rocket_product_cost_map "
                    "(product_number, internal_sku, status, match_method, note, created_at, updated_at) "
                    "VALUES (?, ?, 'confirmed', 'manual', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (pn, sku, f"promo-pnl seed {stamp}: {label}"),
                )
        elif m["internal_sku"] != sku or m["status"] != "confirmed":
            planned.append(
                f"UPDATE rocket_product_cost_map {pn}: {m['internal_sku']}/{m['status']} → {sku}/confirmed"
            )
            if args.apply:
                cur.execute(
                    "UPDATE rocket_product_cost_map SET internal_sku = ?, status = 'confirmed', "
                    "match_method = 'manual', note = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE product_number = ?",
                    (sku, f"promo-pnl seed {stamp}: {label}", pn),
                )
        else:
            print(f"  = {pn}: 매핑 이미 {sku}/confirmed — 변경 없음")

    if not planned:
        print("변경 사항 없음(이미 목표 상태).")
        con.close()
        return 0

    print("계획된 변경:")
    for p in planned:
        print(f"  - {p}")

    if not args.apply:
        print("\n[dry-run] 실제 반영하려면 --apply 를 붙여 다시 실행하세요.")
        con.close()
        return 0

    con.commit()
    print(f"[after] {json.dumps(_fetch_state(cur), ensure_ascii=False, default=str)}")
    con.close()
    print("완료.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
