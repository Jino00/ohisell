# verify_bep_delivery_parity.py — 배송 구분 영속화 후 BEP 산출값 불변 증명 (Jino 지시 2026-07-28)
#
# 왜 필요한가: BEP는 입찰 상한에 직결된다. 배송비 판별을 raw_data 파싱에서 영속 컬럼으로
# 옮기면서 값이 1원이라도 움직이면 실집행이 흔들린다 → **전 상품 차이 0**을 라이브 데이터로
# 증명하고 넘어간다(원칙22: 격리 통과 ≠ 라이브 합격).
#
# 두 층위로 검증한다.
#   [1] 격리 A/B — _avg_qty_and_logistics를 두 번 돌린다.
#       (A) 현재 코드(영속 컬럼 우선)  vs  (B) 변경 전 로직(raw_data 파싱만)
#       배송 데이터가 BEP로 들어가는 **유일한 통로**가 이 함수의 logistics다 →
#       여기서 전 cpid 차이 0이면 BEP도 원리적으로 불변.
#   [2] 종단 대조(--recalc) — 저장된 naver_product_bep(구코드 산출)를 스냅샷한 뒤
#       calculate_bep을 다시 돌려 전 상품 bep_roas/target_roas/logistics_cost를 대조한다.
#       (calculate_bep은 prod 크론이 정기 실행하는 멱등 작업이라 재실행 자체는 통상 운용이다.)
#
# 실행:
#   cd backend && python scripts/verify_bep_delivery_parity.py            # [1]만(읽기 전용)
#   cd backend && python scripts/verify_bep_delivery_parity.py --recalc   # [1]+[2]
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session  # noqa: E402

from app.database import engine  # noqa: E402
from app.models import NaverProductBep  # noqa: E402
from app.services import order_delivery  # noqa: E402
from app.services.naver_ad import bep_calculator  # noqa: E402

# 변경 전(2026-07-27 시점) _order_shipping_cost 원본 — 영속 컬럼을 모르는 순수 raw_data 파서.
# 대조군이므로 여기 로직은 절대 "개선"하지 말 것(구 동작 그대로여야 대조가 성립한다).
_LEGACY_NORMAL = Decimal("1900")
_LEGACY_NBAESONG = Decimal("3020")


def _legacy_order_shipping_cost(order_row=None) -> Decimal:
    if not order_row:
        return _LEGACY_NORMAL
    if isinstance(order_row, dict):
        rd = order_row.get("raw_data")
    else:
        rd = getattr(order_row, "raw_data", None)
    parsed = None
    if isinstance(rd, dict):
        parsed = rd
    elif isinstance(rd, str) and rd:
        try:
            obj = json.loads(rd)
            parsed = obj if isinstance(obj, dict) else None
        except (ValueError, TypeError):
            parsed = None
    if not isinstance(parsed, dict):
        return _LEGACY_NORMAL
    po = parsed.get("productOrder")
    if not isinstance(po, dict):
        return _LEGACY_NORMAL
    if po.get("deliveryAttributeType") == "ARRIVAL_GUARANTEE":
        return _LEGACY_NBAESONG
    return _LEGACY_NORMAL


_KEYS = ("avg_qty", "shipping", "collected", "net_ship", "logistics", "orders")


def isolation_ab(db: Session) -> int:
    new = bep_calculator._avg_qty_and_logistics(db)
    orig = order_delivery.order_shipping_cost
    order_delivery.order_shipping_cost = _legacy_order_shipping_cost  # type: ignore[assignment]
    try:
        old = bep_calculator._avg_qty_and_logistics(db)
    finally:
        order_delivery.order_shipping_cost = orig  # type: ignore[assignment]

    diffs = 0
    cpids = sorted(set(new) | set(old))
    for cpid in cpids:
        a, b = new.get(cpid), old.get(cpid)
        if a is None or b is None:
            print("  ✗ %s: 한쪽에만 존재 (new=%s old=%s)" % (cpid, a is not None, b is not None))
            diffs += 1
            continue
        for k in _KEYS:
            if a[k] != b[k]:
                print("  ✗ %s.%s: new=%s old=%s" % (cpid, k, a[k], b[k]))
                diffs += 1
    print("[1] 격리 A/B — 상품 %d개 대조, 차이 %d건" % (len(cpids), diffs))
    return diffs


_BEP_COLS = ("selling_price", "cost_price", "logistics_cost", "contribution_margin",
             "bep_roas", "target_roas", "commission_rate", "has_cost")


def _snapshot(db: Session) -> dict:
    out = {}
    for r in db.query(NaverProductBep).all():
        out[r.channel_product_id] = {c: getattr(r, c) for c in _BEP_COLS}
    return out


def end_to_end(db: Session) -> int:
    before = _snapshot(db)
    if not before:
        print("[2] 저장된 naver_product_bep 0행 — 대조 생략(먼저 calculate_bep 1회 필요)")
        return 0
    meta = bep_calculator.calculate_bep(db)
    db.expire_all()
    after = _snapshot(db)
    diffs = 0
    cpids = sorted(set(before) | set(after))
    for cpid in cpids:
        a, b = before.get(cpid), after.get(cpid)
        if a is None or b is None:
            print("  ✗ %s: 한쪽에만 존재 (before=%s after=%s)" % (cpid, a is not None, b is not None))
            diffs += 1
            continue
        for c in _BEP_COLS:
            if a[c] != b[c]:
                print("  ✗ %s.%s: before=%s after=%s" % (cpid, c, a[c], b[c]))
                diffs += 1
    print("[2] 종단 대조 — 상품 %d개, 차이 %d건 (recalc meta=%s)" % (len(cpids), diffs, meta))
    return diffs


def main() -> None:
    ap = argparse.ArgumentParser(description="BEP 산출값 불변 증명")
    ap.add_argument("--recalc", action="store_true", help="[2] 종단 대조까지(테이블 재생성)")
    a = ap.parse_args()
    with Session(engine) as db:
        total = isolation_ab(db)
        if a.recalc:
            total += end_to_end(db)
    print("── 결과: %s ──" % ("PASS (차이 0)" if total == 0 else "FAIL (차이 %d건)" % total))
    sys.exit(0 if total == 0 else 1)


if __name__ == "__main__":
    main()
