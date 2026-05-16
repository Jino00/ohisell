# backfill_cafe24_payments.py — 기존 cafe24 주문 결제유형/수수료/상태/배송비 재산출
# raw_data가 10000자에서 잘려 있어도 식별자(market_id/payment_method/order_status)는
# JSON 앞부분이라 정규식으로 복구 가능. 동기화 클라이언트와 동일 SA를 사용한다.
# 1회성 유지보수 스크립트. 멱등(idempotent) — 반복 실행해도 결과 동일.
from __future__ import annotations

import re
import sys
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, ".")

from app.database import SessionLocal  # noqa: E402
from app.models import Channel, Order  # noqa: E402
from app.services.cafe24_status_mapper import (  # noqa: E402
    REVENUE_EXCLUDED,
    normalize_status,
)
from app.services.commission_resolver import PER_ORDER_TYPES  # noqa: E402
from app.services.commission_resolver import resolve as resolve_commission  # noqa: E402
from app.services.payment_classifier import classify as classify_payment  # noqa: E402
from app.services.shipping_resolver import per_order_shipping  # noqa: E402

_RE_MARKET = re.compile(r'"market_id"\s*:\s*"([^"]*)"')
_RE_GATEWAY = re.compile(r'"payment_gateway_name"\s*:\s*"([^"]*)"')
_RE_PM = re.compile(r'"payment_method"\s*:\s*\[([^\]]*)\]')
_RE_STATUS = re.compile(r'"order_status"\s*:\s*"([^"]*)"')


def _extract_order_dict(raw: str) -> dict:
    m = _RE_MARKET.search(raw)
    g = _RE_GATEWAY.search(raw)
    p = _RE_PM.search(raw)
    pm = []
    if p:
        pm = [x.strip().strip('"').strip("'") for x in p.group(1).split(",") if x.strip()]
    return {
        "market_id": m.group(1) if m else "",
        "payment_gateway_name": g.group(1) if g else "",
        "payment_method": pm,
    }


def _line_status(raw: str) -> str:
    """라인 자체 raw_data에서 상태 추출.

    raw_data = {"order": item, "item": detail} 구조라 order_status가 2개 등장:
      [0]=주문(order) 단위, [1]=해당 라인(detail) 단위.
    부분취소/반품 정확도를 위해 detail([1]) 우선, 잘려서 없으면 order([0]) 폴백.
    """
    occ = _RE_STATUS.findall(raw)
    if len(occ) >= 2:
        return occ[1]
    return occ[0] if occ else ""


def main() -> None:
    db = SessionLocal()
    try:
        ch = db.query(Channel).filter(Channel.code == "CAFE24").first()
        if not ch:
            print("CAFE24 채널 없음")
            return
        ship_per_order = per_order_shipping("CAFE24")

        orders = (
            db.query(Order)
            .filter(Order.channel_id == ch.id)
            .order_by(Order.order_number)
            .all()
        )
        by_no: dict[str, list[Order]] = defaultdict(list)
        for o in orders:
            by_no[o.order_number].append(o)

        updated = 0
        for _, lines in by_no.items():
            payment_type = classify_payment(_extract_order_dict(lines[0].raw_data or ""))

            # 라인별 상태 (detail 우선) → 매출 포함 라인에만 수수료/배송비 배분
            statuses = [
                normalize_status(_line_status(l.raw_data or "")) for l in lines
            ]
            incl = [i for i, s in enumerate(statuses) if s not in REVENUE_EXCLUDED]
            incl_total = sum(
                (lines[i].selling_price or Decimal("0")) * lines[i].quantity for i in incl
            ) or Decimal("0")

            ship_total = ship_per_order if incl else Decimal("0")
            order_fee = (
                resolve_commission(payment_type, incl_total)
                if (payment_type in PER_ORDER_TYPES and incl)
                else None
            )

            def _alloc(total: Decimal, incl=incl, incl_total=incl_total) -> dict[int, Decimal]:
                out: dict[int, Decimal] = {}
                acc = Decimal("0")
                for k, i in enumerate(incl):
                    if k == len(incl) - 1:
                        out[i] = total - acc
                    else:
                        rev_i = (lines[i].selling_price or Decimal("0")) * lines[i].quantity
                        v = (
                            (total * rev_i / incl_total).quantize(Decimal("1"))
                            if incl_total
                            else Decimal("0")
                        )
                        out[i] = v
                        acc += v
                return out

            ship_alloc = _alloc(ship_total) if incl else {}
            fee_alloc = _alloc(order_fee) if (order_fee is not None) else None

            for idx, l in enumerate(lines):
                st = statuses[idx]
                line_rev = (l.selling_price or Decimal("0")) * l.quantity
                if st in REVENUE_EXCLUDED:
                    commission = Decimal("0")
                    ship = Decimal("0")
                elif fee_alloc is not None:
                    commission = fee_alloc[idx]
                    ship = ship_alloc[idx]
                else:
                    commission = resolve_commission(payment_type, line_rev)
                    ship = ship_alloc[idx]

                l.payment_type = payment_type
                l.commission_amount = commission
                l.status = st
                l.shipping_cost = ship
                updated += 1

        db.commit()
        print(f"백필 완료: {len(by_no)} 주문 / {updated} 라인 갱신")
    finally:
        db.close()


if __name__ == "__main__":
    main()
