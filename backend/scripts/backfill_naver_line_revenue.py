#!/usr/bin/env python3
"""네이버 주문의 매출·수수료·배송수입·배송비를 정산 정합 공식으로 소급 재계산한다 (2026-08-04).

★왜 필요한가: 수집부(`app/clients/naver.py`)만 고치면 **앞으로 들어올 주문**만 맞고, 이미
  저장된 13,000여 건은 옛 공식 그대로 남는다. 손익·BEP·광고 자동운영이 전부 과거 데이터를
  읽으므로 소급하지 않으면 대시보드는 계속 어긋난 값을 보여준다.

바꾸는 것 4가지 (전부 raw_data에서 재계산 — 새 API 호출 없음):
  ① selling_price      → naver_line_revenue(po)  = remainProductAmount − remainSellerBurdenDiscountAmount
  ② commission_amount  → 부분취소면 remain 비율로 축소(절사)
  ③ shipping_cost      → deliveryFeeAmount + sectionDeliveryFee (제주·도서산간 수입 포함)
  ④ shipping_cost_paid → 제주면 기본단가 + 3,000 (수입만 넣고 비용을 빼지 않기 위함)

사용:
  python3 scripts/backfill_naver_line_revenue.py            # dry-run(기본) — 변경 예정만 출력
  python3 scripts/backfill_naver_line_revenue.py --apply    # 실제 반영
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.clients.naver import naver_line_revenue  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Channel, Order  # noqa: E402
from app.services import order_delivery  # noqa: E402


def _po(raw):
    if not raw:
        return None
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict):
        return None
    return d.get("productOrder") or d


def _commission(po: dict) -> Decimal | None:
    keys = (
        "paymentCommission",
        "saleCommission",
        "knowledgeShoppingSellingInterlockCommission",
        "channelCommission",
    )
    if not any(k in po for k in keys):
        return None
    c = Decimal(str(sum(po.get(k, 0) for k in keys)))
    total = Decimal(str(po.get("totalProductAmount") or 0))
    remain = Decimal(str(po.get("remainProductAmount") or 0))
    if total > 0 and remain != total:
        c = (c * remain / total).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 반영(기본은 dry-run)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        ch = db.query(Channel).filter(Channel.code == "NAVER").first()
        if not ch:
            print("NAVER 채널 없음")
            return 1
        rows = db.query(Order).filter(Order.channel_id == ch.id).all()
        print(f"대상 네이버 주문 라인: {len(rows):,}건")

        stats = Counter()
        delta_rev = Decimal(0)
        delta_com = Decimal(0)
        delta_ship_in = Decimal(0)
        delta_ship_paid = Decimal(0)
        samples: list[str] = []

        for o in rows:
            po = _po(o.raw_data)
            if po is None:
                stats["raw_없음_건너뜀"] += 1
                continue

            new_rev = naver_line_revenue(po)
            new_com = _commission(po)
            section = order_delivery.section_delivery_fee_of(po)
            new_ship_in = Decimal(str(po.get("deliveryFeeAmount") or 0)) + section
            method = order_delivery.shipping_method_of(po.get("deliveryAttributeType"))
            new_ship_paid = order_delivery.shipping_cost_paid_of(method, section=section > 0)

            old_rev = Decimal(str(o.selling_price or 0))
            if new_rev != old_rev:
                stats["매출_변경"] += 1
                delta_rev += new_rev - old_rev
                if len(samples) < 10:
                    samples.append(
                        f"  매출  {o.platform_order_line_id} {old_rev:,} → {new_rev:,}"
                    )
                if args.apply:
                    o.selling_price = new_rev

            if new_com is not None:
                old_com = Decimal(str(o.commission_amount or 0))
                if new_com != old_com:
                    stats["수수료_변경"] += 1
                    delta_com += new_com - old_com
                    if args.apply:
                        o.commission_amount = new_com

            old_ship_in = Decimal(str(o.shipping_cost or 0))
            if new_ship_in != old_ship_in:
                stats["배송수입_변경"] += 1
                delta_ship_in += new_ship_in - old_ship_in
                if args.apply:
                    # 0이면 None으로 되돌린다(수집부와 같은 표현 — "없음"과 "0원"을 섞지 않는다)
                    o.shipping_cost = new_ship_in if new_ship_in else None

            if o.shipping_cost_paid is not None:
                old_paid = Decimal(str(o.shipping_cost_paid))
                if new_ship_paid != old_paid:
                    stats["배송비_변경"] += 1
                    delta_ship_paid += new_ship_paid - old_paid
                    if args.apply:
                        o.shipping_cost_paid = new_ship_paid

        print()
        for k, v in sorted(stats.items()):
            print(f"  {k:20s} {v:>7,}건")
        print()
        print(f"  매출 순변화     {delta_rev:>+14,.0f}원")
        print(f"  수수료 순변화   {delta_com:>+14,.0f}원")
        print(f"  배송수입 순변화 {delta_ship_in:>+14,.0f}원")
        print(f"  배송비 순변화   {delta_ship_paid:>+14,.0f}원")
        net = delta_rev - delta_com + delta_ship_in - delta_ship_paid
        print(f"  → 순이익 영향   {net:>+14,.0f}원")
        if samples:
            print("\n  변경 샘플:")
            for s in samples:
                print(s)

        if args.apply:
            db.commit()
            print("\n✅ 반영 완료(commit)")
        else:
            print("\n(dry-run — 반영하려면 --apply)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
