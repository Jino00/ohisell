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
# ⚠️⚠️ 이 스크립트는 **회계 숫자를 움직인다** — Phase 1의 "회계축 불변"은 신규 테이블·ingest에
#   해당하는 말이고, 이 원가 시드는 예외다. product_master.cost_price는 주문 시점에 박제되는
#   값이 아니라 **조회 때마다 소급 적용**되기 때문이다(코드 실측 2026-07-28):
#     - services/profit_calculator.py:462·598·778  bucket["cost"] += cost_price * quantity
#     - services/naver_ad/bep_calculator.py:310     BEP ROAS의 공헌이익 분모
#   ⇒ 62178970(OHI-TGLASS-IP17PRO) 3,400→3,500 갱신은 ①그 SKU의 **과거 주문 전체**에서 종합조망
#      net_profit을 낮추고 ②아이폰17프로 강화유리를 파는 광고 캠페인의 **BEP ROAS를 올린다**
#      (자동 운영 루프가 읽는 값). 3,500이 옳은 값이라는 것은 Jino 확정이지만, 적용은
#      "숫자가 바뀐다는 것을 알고" 해야 한다 — 적용 전후 종합조망 델타를 기록할 것.
#
# 멱등: 이미 원하는 상태면 건너뛴다. 기본 --dry-run(실제 쓰기는 --apply 명시).
# 실행 위치: prod 서버(같은 DB 파일). 이 스크립트는 저장소에만 두고 실행은 배포 시점에.
#   $ python3 backend/scripts/seed_promo_pnl_costs_20260728.py            # dry-run
#   $ python3 backend/scripts/seed_promo_pnl_costs_20260728.py --apply
import argparse
import json
import os
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
            "SELECT id, internal_sku, product_name, cost_price FROM product_master "
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

    # sqlite3.connect은 없는 경로를 **빈 DB로 만들어 버린다** — 엉뚱한 기계에서 돌리면 유령
    #   파일만 남기고 "no such table"로 죽는다. 존재하는 DB에만 붙는다.
    if not os.path.isfile(args.db):
        print(f"✗ DB 파일 없음: {args.db} (prod에서 실행하거나 --db로 경로를 주세요)")
        return 2

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    print(
        "⚠️ 이 시드는 product_master.cost_price를 바꾸며, 그 값은 과거 주문에 **소급 적용**된다"
        " — 종합조망 net_profit과 광고 BEP ROAS가 함께 움직인다. 적용 전후 델타를 기록할 것."
    )
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
        if cur_cost is None or float(cur_cost) != float(cost):
            # 블라스트 반경을 **먼저 눈에 보이게** 한다: 이 UPDATE가 몇 행을 건드리는지,
            #   그 SKU의 과거 주문이 몇 건인지(= 소급 재계산될 net_profit 범위).
            n_master = cur.execute(
                "SELECT COUNT(*) c FROM product_master WHERE internal_sku = ?", (sku,)
            ).fetchone()["c"]
            try:
                n_orders = cur.execute(
                    "SELECT COUNT(*) c FROM orders WHERE product_id = ?",
                    (st["master"]["id"],),
                ).fetchone()["c"]
            except sqlite3.Error:
                n_orders = "?"
            if n_master != 1:
                print(f"  ✗ {pn}: internal_sku={sku} 행이 {n_master}개 — 안전하지 않아 건너뜀")
                continue
            planned.append(
                f"UPDATE product_master SET cost_price={cost} WHERE internal_sku='{sku}' "
                f"(현재 {cur_cost}, master {n_master}행, ★소급 영향 주문 {n_orders}건)"
            )
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
