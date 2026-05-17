# manual_revenue_service.py — 수동 매출 입력 SA (SA-1~4)
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models import ManualRevenue


# SA-1: 멱등 upsert (같은 날 재입력 시 덮어쓰기)
def upsert_manual_revenue(
    db: Session,
    channel_id: int,
    revenue_date: date,
    gross_revenue: Decimal,
    memo: str | None = None,
) -> ManualRevenue:
    existing = (
        db.query(ManualRevenue)
        .filter(
            ManualRevenue.channel_id == channel_id,
            ManualRevenue.revenue_date == revenue_date,
        )
        .first()
    )
    if existing:
        existing.gross_revenue = gross_revenue
        existing.memo = memo
        db.commit()
        db.refresh(existing)
        return existing

    record = ManualRevenue(
        channel_id=channel_id,
        revenue_date=revenue_date,
        gross_revenue=gross_revenue,
        memo=memo,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


# SA-2: 목록 조회
def list_manual_revenue(
    db: Session,
    channel_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[ManualRevenue]:
    query = db.query(ManualRevenue)
    if channel_id is not None:
        query = query.filter(ManualRevenue.channel_id == channel_id)
    if date_from is not None:
        query = query.filter(ManualRevenue.revenue_date >= date_from)
    if date_to is not None:
        query = query.filter(ManualRevenue.revenue_date <= date_to)
    return query.order_by(ManualRevenue.revenue_date.desc()).all()


# SA-3: 삭제
def delete_manual_revenue(db: Session, record_id: int) -> bool:
    record = db.query(ManualRevenue).filter(ManualRevenue.id == record_id).first()
    if not record:
        return False
    db.delete(record)
    db.commit()
    return True


# SA-4: 일별 집계 (profit_calculator에서 사용)
def get_daily_manual_revenue(
    db: Session,
    date_from: date,
    date_to: date,
    channel_id: int | None = None,
) -> dict[tuple[int, str], Decimal]:
    """
    {(channel_id, date_str): gross_revenue} 반환
    profit_calculator가 이 값을 매출-only 라인으로 병합함
    """
    query = db.query(ManualRevenue).filter(
        and_(
            ManualRevenue.revenue_date >= date_from,
            ManualRevenue.revenue_date <= date_to,
        )
    )
    if channel_id is not None:
        query = query.filter(ManualRevenue.channel_id == channel_id)

    result: dict[tuple[int, str], Decimal] = {}
    for row in query.all():
        key = (row.channel_id, str(row.revenue_date))
        result[key] = Decimal(str(row.gross_revenue))
    return result
