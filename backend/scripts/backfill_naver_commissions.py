# backfill_naver_commissions.py — 기존 네이버 주문 commission_amount 일괄 업데이트
# 용도: naver.py에 commission 파싱 추가 전 동기화된 기존 1,221건 소급 적용
# 실행: cd backend && python scripts/backfill_naver_commissions.py
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database import engine

EXCLUDED = {"cancelled", "returned", "deposit_waiting"}


def _parse_commission(raw_data: str | dict) -> Decimal | None:
    try:
        data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
        po = data.get("productOrder", {})
        total = (
            po.get("paymentCommission", 0)
            + po.get("saleCommission", 0)
            + po.get("knowledgeShoppingSellingInterlockCommission", 0)
            + po.get("channelCommission", 0)
        )
        return Decimal(str(total)) if total else None
    except Exception:
        return None


def run() -> None:
    with Session(engine) as db:
        rows = db.execute(
            text("""
                SELECT o.id, o.raw_data, o.status, o.selling_price
                FROM orders o
                JOIN channels c ON o.channel_id = c.id
                WHERE c.code = 'NAVER'
            """)
        ).fetchall()

    updated = skipped = no_field = 0
    updates: list[dict] = []

    for order_id, raw_data, status, selling_price in rows:
        if not raw_data:
            skipped += 1
            continue

        commission = _parse_commission(raw_data)

        if commission is None:
            no_field += 1
            continue

        updates.append({"id": order_id, "commission_amount": str(commission)})

    print(f"전체: {len(rows)}건 | 업데이트 대상: {len(updates)}건 | raw_data 없음: {skipped}건 | 필드 없음: {no_field}건")

    if not updates:
        print("업데이트할 항목 없음.")
        return

    # Before 스냅샷 (취소/반품 제외, commission_amount 기준)
    with Session(engine) as db:
        before = db.execute(
            text("""
                SELECT COALESCE(SUM(CAST(commission_amount AS REAL)), 0) as comm_sum,
                       COUNT(*) as cnt
                FROM orders o
                JOIN channels c ON o.channel_id = c.id
                WHERE c.code = 'NAVER'
                  AND o.status NOT IN ('cancelled','returned','deposit_waiting')
                  AND o.commission_amount IS NOT NULL
            """)
        ).fetchone()
        print(f"Before: commission_amount 있는 건: {before[1]}건, 합계: {before[0]:,.0f}원")

    # 업데이트 실행
    with Session(engine) as db:
        for item in updates:
            db.execute(
                text("UPDATE orders SET commission_amount = :commission_amount WHERE id = :id"),
                item,
            )
        db.commit()
        updated = len(updates)

    # After 스냅샷
    with Session(engine) as db:
        after = db.execute(
            text("""
                SELECT COALESCE(SUM(CAST(commission_amount AS REAL)), 0) as comm_sum,
                       COALESCE(SUM(CAST(selling_price AS REAL)), 0) as rev_sum,
                       COUNT(*) as cnt
                FROM orders o
                JOIN channels c ON o.channel_id = c.id
                WHERE c.code = 'NAVER'
                  AND o.status NOT IN ('cancelled','returned','deposit_waiting')
                  AND o.commission_amount IS NOT NULL
            """)
        ).fetchone()

        flat_comm = after[1] * 0.055
        print(f"After : commission_amount 건: {after[2]}건, 합계: {after[0]:,.0f}원 ({after[0]/after[1]*100:.2f}%)")
        print(f"5.5% 추정: {flat_comm:,.0f}원")
        print(f"순이익 개선 (수수료 감소): {flat_comm - after[0]:,.0f}원")
        print(f"업데이트 완료: {updated}건")


if __name__ == "__main__":
    run()
