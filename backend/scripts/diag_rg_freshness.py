# diag_rg_freshness.py — RG 주문 취소(stale) 동작 실증 진단 (읽기 전용)
# 목적: RG 취소건이 ① orders API 목록에서 사라지는지(→reconcile-by-absence)
#        ② 정산 환불로만 나타나는지를 라이브 증거로 판정 (원칙22).
# 사용: prod 서버에서 cwd ~/ohisell/backend, PYTHONPATH=. ./.venv/bin/python scripts/diag_rg_freshness.py FROM TO
#   FROM/TO = yyyymmdd (정산완료 구간 권장, 예 20260501 20260531)
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from app.database import SessionLocal
from app.clients.coupang import CoupangRocketGrowthClient
from app.config import get_coupang_config
from app.models import CoupangRgOrderItem

ACCOUNTS = ["COUPANG_WING1", "COUPANG_WING2"]


def _dt(yyyymmdd: str) -> datetime:
    return datetime.strptime(yyyymmdd, "%Y%m%d")


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: diag_rg_freshness.py FROM(yyyymmdd) TO(yyyymmdd)")
        sys.exit(1)
    from datetime import timedelta
    win_from, win_to = sys.argv[1], sys.argv[2]
    # paidDateTo는 배타적 → 끝날짜 당일 포함하려면 +1일 (rg_order_sync._windows 동일 규칙)
    api_to = (_dt(win_to) + timedelta(days=1)).strftime("%Y%m%d")

    db_from = _dt(win_from)
    db_to = _dt(win_to).replace(hour=23, minute=59, second=59)

    db = SessionLocal()
    try:
        for acc in ACCOUNTS:
            cfg = get_coupang_config(acc)
            if cfg is None:
                print(f"[{acc}] config_missing"); continue
            client = CoupangRocketGrowthClient(cfg)

            # --- 라이브 재조회: orderId 집합 + 옵션 매출 ---
            live_orders: set[str] = set()
            live_rev_by_order: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
            err = None
            try:
                for order in client.iter_rg_orders(paid_date_from=win_from, paid_date_to=api_to):
                    oid = str(order.get("orderId"))
                    live_orders.add(oid)
                    for it in order.get("orderItems", []) or []:
                        q = it.get("salesQuantity") or 0
                        p = it.get("unitSalesPrice")
                        if p is None:
                            p = it.get("salesPrice")
                        if p is not None:
                            live_rev_by_order[oid] += Decimal(str(p)) * Decimal(str(q))
            except Exception as e:  # noqa: BLE001
                err = repr(e)

            # --- DB(우리 적재) 동일 윈도우 ---
            rows = (
                db.query(CoupangRgOrderItem)
                .filter(
                    CoupangRgOrderItem.account_key == acc,
                    CoupangRgOrderItem.paid_at >= db_from,
                    CoupangRgOrderItem.paid_at <= db_to,
                )
                .all()
            )
            db_orders: set[str] = set()
            db_rev_by_order: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
            for r in rows:
                db_orders.add(r.order_id)
                if r.unit_sales_price is not None and r.sales_quantity is not None:
                    db_rev_by_order[r.order_id] += r.unit_sales_price * r.sales_quantity

            absent = db_orders - live_orders   # DB엔 있고 라이브엔 없음 = 취소 후보
            new_live = live_orders - db_orders  # 라이브엔 있고 DB엔 없음 = 미동기화
            absent_rev = sum((db_rev_by_order[o] for o in absent), Decimal(0))
            db_total = sum(db_rev_by_order.values(), Decimal(0))
            live_total = sum(live_rev_by_order.values(), Decimal(0))

            print(f"\n===== [{acc}] vendor={cfg.vendor_id} window {win_from}~{win_to} (api_to={api_to}) =====")
            if err:
                print(f"  ⚠️ LIVE FETCH ERROR: {err}")
            print(f"  live orders={len(live_orders)}  db orders={len(db_orders)}")
            print(f"  live revenue={live_total}  db revenue={db_total}  diff(db-live)={db_total-live_total}")
            print(f"  ABSENT (db-only, 취소후보)={len(absent)}  absent_revenue={absent_rev}")
            if absent:
                for o in sorted(absent)[:20]:
                    print(f"     - {o}  rev={db_rev_by_order[o]}")
            print(f"  NEW in live (미동기화)={len(new_live)}")
            if new_live:
                for o in sorted(new_live)[:10]:
                    print(f"     + {o}  rev={live_rev_by_order[o]}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
