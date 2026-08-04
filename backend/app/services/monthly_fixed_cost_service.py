# monthly_fixed_cost_service.py — 월 고정비 입력·일할 배분 SA
#
# 출처: 두핸즈(3PL) 월 정산서. 2026-07 실측 11일치 139,634원 → 월 ≈38만원.
# 이 비용들은 재고·입고 기반이라 주문 축에 붙지 않아, 여태 손익에 **한 번도 안 잡혔다**.
from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.models import MonthlyFixedCost

ZERO = Decimal("0")
CENT = Decimal("0.01")

# 항목명은 3PL 정산서의 명세 항목을 그대로 쓴다. 고정 집합인 이유(Jino 2026-08-04):
# 자유 입력이면 오타 하나로 같은 항목이 둘로 갈라져 추이가 끊긴다.
FIXED_COST_ITEMS: tuple[str, ...] = ("입고비", "보관료", "항공도선료", "합포장비")

_YM_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def validate_year_month(year_month: str) -> str:
    if not _YM_RE.match(year_month or ""):
        raise ValueError(f"year_month는 'YYYY-MM' 형식이어야 합니다: {year_month!r}")
    return year_month


def validate_item(item: str) -> str:
    if item not in FIXED_COST_ITEMS:
        raise ValueError(f"item은 {list(FIXED_COST_ITEMS)} 중 하나여야 합니다: {item!r}")
    return item


# SA-1: 멱등 upsert — (채널, 월, 항목) 재입력 시 덮어쓰기
def upsert_monthly_fixed_cost(
    db: Session,
    channel_id: int,
    year_month: str,
    item: str,
    amount: Decimal,
    memo: str | None = None,
) -> MonthlyFixedCost:
    validate_year_month(year_month)
    validate_item(item)
    if Decimal(amount) < 0:
        raise ValueError("amount는 0 이상이어야 합니다")

    existing = (
        db.query(MonthlyFixedCost)
        .filter(
            MonthlyFixedCost.channel_id == channel_id,
            MonthlyFixedCost.year_month == year_month,
            MonthlyFixedCost.item == item,
        )
        .first()
    )
    if existing:
        existing.amount = amount
        existing.memo = memo
        db.commit()
        db.refresh(existing)
        return existing

    record = MonthlyFixedCost(
        channel_id=channel_id,
        year_month=year_month,
        item=item,
        amount=amount,
        memo=memo,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


# SA-2: 목록 조회
def list_monthly_fixed_cost(
    db: Session,
    channel_id: int | None = None,
    month_from: str | None = None,
    month_to: str | None = None,
) -> list[MonthlyFixedCost]:
    query = db.query(MonthlyFixedCost)
    if channel_id is not None:
        query = query.filter(MonthlyFixedCost.channel_id == channel_id)
    if month_from is not None:
        query = query.filter(MonthlyFixedCost.year_month >= validate_year_month(month_from))
    if month_to is not None:
        query = query.filter(MonthlyFixedCost.year_month <= validate_year_month(month_to))
    return query.order_by(
        MonthlyFixedCost.year_month.desc(), MonthlyFixedCost.item
    ).all()


# SA-3: 삭제
def delete_monthly_fixed_cost(db: Session, record_id: int) -> bool:
    record = db.query(MonthlyFixedCost).filter(MonthlyFixedCost.id == record_id).first()
    if not record:
        return False
    db.delete(record)
    db.commit()
    return True


def allocate_month_daily(total: Decimal, year: int, month: int) -> dict[str, Decimal]:
    """월 총액을 그 달의 일수로 일할 배분한다. **합계는 총액과 원 단위까지 정확히 일치**한다.

    ★단순히 `total / days`를 매일 쓰면 안 된다 — 380,034 / 31은 무한소수라 31번 더해도
      총액이 안 나온다. 누적 배분의 차분을 쓰면 마지막 날이 잔여를 흡수해 합이 닫힌다:
        alloc(i) = round(total × i / days) − round(total × (i−1) / days)
      (회계에서 표준적인 잔여 배분 방식이고, 어느 날짜 구간을 잘라 봐도 이중계상이 없다.)
    """
    days = calendar.monthrange(year, month)[1]
    out: dict[str, Decimal] = {}
    prev_cum = ZERO
    for i in range(1, days + 1):
        cum = (Decimal(total) * i / days).quantize(CENT, rounding=ROUND_HALF_UP)
        out[date(year, month, i).isoformat()] = cum - prev_cum
        prev_cum = cum
    return out


# SA-4: 일별 집계 (profit_calculator에서 사용)
def get_daily_fixed_cost(
    db: Session,
    date_from: date,
    date_to: date,
    channel_id: int | None = None,
) -> dict[tuple[int, str], Decimal]:
    """{(channel_id, date_str): 그날 몫} 반환 — 조회 창에 걸친 날짜만.

    ⚠️조회 창이 월 중간을 자르면 그 달 몫도 잘린다(의도된 동작). 월 전체를 조회하면
      합계가 정확히 월 총액이다.
    """
    months = _months_in_range(date_from, date_to)
    if not months:
        return {}

    query = db.query(MonthlyFixedCost).filter(MonthlyFixedCost.year_month.in_(months))
    if channel_id is not None:
        query = query.filter(MonthlyFixedCost.channel_id == channel_id)

    # (채널, 월) 총액으로 먼저 합친다 — 항목별로 따로 배분해 반올림하면 잔여가 항목 수만큼 생긴다.
    totals: dict[tuple[int, str], Decimal] = {}
    for row in query.all():
        key = (row.channel_id, row.year_month)
        totals[key] = totals.get(key, ZERO) + Decimal(str(row.amount))

    result: dict[tuple[int, str], Decimal] = {}
    for (cid, ym), total in totals.items():
        if total == ZERO:
            continue
        year, month = int(ym[:4]), int(ym[5:7])
        for d_str, amount in allocate_month_daily(total, year, month).items():
            if date_from.isoformat() <= d_str <= date_to.isoformat() and amount != ZERO:
                result[(cid, d_str)] = amount
    return result


def _months_in_range(date_from: date, date_to: date) -> list[str]:
    if date_from > date_to:
        return []
    months: list[str] = []
    y, m = date_from.year, date_from.month
    while (y, m) <= (date_to.year, date_to.month):
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months
