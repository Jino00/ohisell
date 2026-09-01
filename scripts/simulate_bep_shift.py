"""배포 «전» BEP 이동 폭 측정 — 읽기 전용 시뮬레이터 (D-NAO-283, 계약 §2-6).

왜 있나: 614개 상품의 자가 한꺼번에 움직이고 PAO가 그 값으로 입찰 상한을 잡는다. 「배포하고
나서 보자」는 이 규모에서 답이 아니다. D-NAO-236이 같은 방식으로 「하한이 방향을 1건도 안
바꾼다」를 배포 전에 확정한 선례가 있다.

★산식을 다시 적지 않는다 — bep_calculator.bep_from_parts를 **그대로** 부른다(교훈 #375).
  조회 스크립트가 산식을 베끼면 예측과 실제가 갈라져도 어느 쪽이 틀렸는지 알 수 없다.

쓰는 법 (prod, 앱 트리를 건드리지 않는다):
    scp backend/app/services/naver_ad/bep_calculator.py sellc:/tmp/bep_new_n2.py
    scp scripts/simulate_bep_shift.py sellc:/tmp/
    ssh sellc "cd /home/ubuntu/ohisell/backend && python3 /tmp/simulate_bep_shift.py /tmp/bep_new_n2.py"

읽기 전용 강제: 엔진을 sqlite `mode=ro`로 연다(쓰기를 섞으면 즉시 예외로 죽는다).
"""
from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import NaverProductBep


def load_new_module(path: str):
    """새 bep_calculator를 «앱 트리 밖»에서 로드한다 — prod 코드는 한 글자도 안 바뀐다."""
    spec = importlib.util.spec_from_file_location("bep_new_n2", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bep_new_n2"] = mod
    spec.loader.exec_module(mod)
    return mod


def main(new_path: str, db_path: str = "ohisell.db") -> None:
    bep_new = load_new_module(new_path)
    engine = create_engine(f"sqlite:///file:{db_path}?mode=ro&uri=true")
    db = sessionmaker(bind=engine)()

    order_rows = bep_new._naver_order_rows(db)
    meta_map = bep_new._meta_by_cpid(db)
    new_logi = bep_new.logistics_by_product(db, orders_by_pid=order_rows, meta=meta_map)
    new_prices = bep_new._unit_prices(db, orders_by_pid=order_rows)
    mult = bep_new.AGG_MULT["standard"]

    stored = db.query(NaverProductBep).all()
    print(f"저장된 행 {len(stored)}개 · 주문 있는 상품 {len(order_rows)} · 메타 {len(meta_map)}")

    clamp_rows, price_rescued, price_changed, logi_basis, price_basis = [], [], [], {}, {}
    null_before = null_after = 0

    for r in stored:
        cpid = r.channel_product_id
        old_sp = Decimal(str(r.selling_price or 0))
        old_logi = Decimal(str(r.logistics_cost or 0))
        old_bep = Decimal(str(r.bep_roas)) if r.bep_roas is not None else None
        rate = Decimal(str(r.commission_rate or 0))
        cost = Decimal(str(r.cost_price or 0))

        # 새 판매가 — calculate_bep과 **같은 우선순위**(orders → mapping → meta).
        # mapping은 스냅샷에 없으므로, 「저장된 sp가 orders와 다르면 mapping이었다」로 되짚는다.
        sp = new_prices.get(cpid, Decimal("0"))
        pbasis = "orders"
        if sp <= 0:
            if old_sp > 0:
                sp, pbasis = old_sp, "mapping"   # 종전에 사람 입력값이 이미 이겼던 행
            else:
                mp = (meta_map.get(cpid) or {}).get("discounted_price")
                if mp and mp > 0:
                    sp, pbasis = mp, "meta"

        row = new_logi.get(cpid)
        lbasis = row["basis"] if row else "default"
        logi = row["logistics"] if row else bep_new.SHIPPING_COST_NORMAL

        new = bep_new.bep_from_parts(sp, rate, cost, logi, mult)
        nb = new["bep_roas"]

        logi_basis[lbasis] = logi_basis.get(lbasis, 0) + 1
        price_basis[pbasis] = price_basis.get(pbasis, 0) + 1
        if old_bep is None:
            null_before += 1
        if nb is None:
            null_after += 1
        if old_sp > 0 and sp != old_sp:
            price_changed.append((cpid, old_sp, sp, pbasis))
        if old_sp <= 0 and sp > 0:
            price_rescued.append((cpid, sp, pbasis))
        # ★합격기준 ③의 대상 = 클램프 자국(저장 logistics_cost == 0)이 있던 행
        if old_logi == 0 and old_bep is not None and nb is not None:
            clamp_rows.append((cpid, old_bep, nb, old_logi, logi, r.product_name))

    print()
    print("── 합격기준 ② bep_roas NULL ──")
    print(f"   전 {null_before} → 후 {null_after}   (목표 36 이하)")

    print()
    print("── 합격기준 ③ 클램프 걸리던 행의 BEP 이동 ──")
    print(f"   대상 {len(clamp_rows)}개")
    if clamp_rows:
        deltas = [float((nb - ob) / ob * 100) for _, ob, nb, _, _, _ in clamp_rows]
        deltas.sort()
        mid = deltas[len(deltas) // 2]
        print(f"   평균 {sum(deltas)/len(deltas):+.2f}% · 중앙값 {mid:+.2f}% "
              f"· 최소 {deltas[0]:+.2f}% · 최대 {deltas[-1]:+.2f}%")
        print(f"   낮아진 행 {sum(1 for d in deltas if d < 0)} / 높아진 행 {sum(1 for d in deltas if d > 0)}")
        print("   표본 5개:")
        for cpid, ob, nb, ol, nl, name in sorted(
                clamp_rows, key=lambda x: (x[2] - x[1]) / x[1])[:5]:
            print(f"     {cpid} {str(name)[:26]:<26} BEP {ob} → {nb}  물류비 {ol} → {nl}")

    print()
    print("── 합격기준 ④ 판매가 회귀 ──")
    print(f"   판매가가 있던 행 중 값이 **바뀐** 행: {len(price_changed)}  (0이어야 한다)")
    for row in price_changed[:10]:
        print("     ", row)
    print(f"   판매가가 없다가 **생긴** 행: {len(price_rescued)}")

    print()
    print("── 합격기준 ⑤ 출처 분포 ──")
    print(f"   물류비: {logi_basis}")
    print(f"   판매가: {price_basis}")

    print()
    print("── 발단 상품 (그룹 52308509) ──")
    for r in stored:
        if r.channel_product_id in ("13687558209", "13687558210"):
            cpid = r.channel_product_id
            sp = (meta_map.get(cpid) or {}).get("discounted_price") or Decimal("0")
            row = new_logi.get(cpid)
            logi = row["logistics"] if row else bep_new.SHIPPING_COST_NORMAL
            lb = row["basis"] if row else "default"
            new = bep_new.bep_from_parts(sp, Decimal(str(r.commission_rate or 0)),
                                         Decimal(str(r.cost_price or 0)), logi, mult)
            print(f"   {cpid}: sp {r.selling_price} → {sp} · 물류비 {r.logistics_cost} → {logi}"
                  f" ({lb}) · BEP {r.bep_roas} → {new['bep_roas']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/bep_new_n2.py")
