# backfill_order_exchange_fields.py — 기존 교환 주문의 교환 배송 손익 컬럼 소급 채움
#
# 용도(2026-08-04): raw_data.exchange.* 안에만 있던 재배송 처리일·교환비 청구액·회수 택배사를
# orders 컬럼으로 영속화한 뒤, 이미 쌓인 교환 주문에 소급 적용한다. 이게 없으면 컬럼이 NULL이라
# 과거 교환이 손익에 영영 안 잡힌다(신규 교환만 잡힌다).
# 판별은 services/order_delivery.py 한 곳에서만 한다 — 동기화와 같은 함수라 값이 갈라질 수 없다.
#
# 안전 규칙:
#   · 재실행 안전(idempotent) — 같은 raw_data면 같은 값. 이미 같은 값인 행은 skip.
#   · 교환 정보 부재·EXCHANGE_REJECT·JSON 잘림 → 컬럼 NULL 유지 + 건수 보고(추정 금지).
#     ★REJECT를 NULL로 두는 것이 맞다 — 거부된 교환은 회수도 재발송도 일어나지 않는다.
#
# 실행:
#   cd backend && python scripts/backfill_order_exchange_fields.py --dry-run
#   cd backend && python scripts/backfill_order_exchange_fields.py --channel 6
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session  # noqa: E402

from app.database import engine  # noqa: E402
from app.models import Order  # noqa: E402
from app.services import order_delivery  # noqa: E402

BATCH = 2000


def run(channel_id: int | None, dry_run: bool, batch: int) -> dict:
    stats = {"scanned": 0, "filled": 0, "unchanged": 0, "no_exchange_info": 0, "no_raw_data": 0}
    with Session(engine) as db:
        base = db.query(Order)
        if channel_id is not None:
            base = base.filter(Order.channel_id == channel_id)
        total = base.count()
        print("대상 주문 %d건 (channel=%s, dry_run=%s)" % (total, channel_id or "ALL", dry_run))

        last_id = 0
        while True:
            q = db.query(Order).filter(Order.id > last_id)
            if channel_id is not None:
                q = q.filter(Order.channel_id == channel_id)
            rows = q.order_by(Order.id).limit(batch).all()
            if not rows:
                break
            for o in rows:
                last_id = o.id
                stats["scanned"] += 1
                if not o.raw_data:
                    stats["no_raw_data"] += 1
                    continue
                fields = order_delivery.exchange_fields(o.raw_data)
                if fields is None:
                    stats["no_exchange_info"] += 1   # 교환 아님·REJECT·JSON 잘림 → NULL 유지
                    continue
                if all(getattr(o, k) == v for k, v in fields.items()):
                    stats["unchanged"] += 1
                    continue
                if not dry_run:
                    for k, v in fields.items():
                        setattr(o, k, v)
                stats["filled"] += 1
            if not dry_run:
                db.commit()
            else:
                db.rollback()
            print(
                "  진행 %d/%d — 채움 %d · 동일 %d · 교환정보없음 %d · raw없음 %d"
                % (stats["scanned"], total, stats["filled"], stats["unchanged"],
                   stats["no_exchange_info"], stats["no_raw_data"])
            )
            db.expunge_all()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="주문 교환 배송 손익 컬럼 백필")
    ap.add_argument("--channel", type=int, default=None, help="채널 ID(미지정=전체)")
    ap.add_argument("--dry-run", action="store_true", help="쓰기 없이 집계만")
    ap.add_argument("--batch", type=int, default=BATCH, help="배치 크기")
    a = ap.parse_args()
    stats = run(a.channel, a.dry_run, a.batch)
    print("── 백필 완료 ──")
    for k, v in stats.items():
        print("  %s: %d" % (k, v))


if __name__ == "__main__":
    main()
