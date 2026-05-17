# routers/manual_revenue.py — 수동 매출 입력 API (POST/GET/DELETE)
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.manual_revenue_service import (
    delete_manual_revenue,
    list_manual_revenue,
    upsert_manual_revenue,
)

router = APIRouter(prefix="/api/manual-revenue", tags=["manual_revenue"])


class ManualRevenueIn(BaseModel):
    channel_id: int
    revenue_date: date
    gross_revenue: Decimal = Field(gt=0)
    memo: Optional[str] = None


class ManualRevenueOut(BaseModel):
    id: int
    channel_id: int
    channel_name: str
    revenue_date: date
    gross_revenue: str
    memo: Optional[str]
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


@router.post("", response_model=ManualRevenueOut)
def create_or_update(body: ManualRevenueIn, db: Session = Depends(get_db)):
    record = upsert_manual_revenue(
        db,
        channel_id=body.channel_id,
        revenue_date=body.revenue_date,
        gross_revenue=body.gross_revenue,
        memo=body.memo,
    )
    return _to_out(record)


@router.get("", response_model=list[ManualRevenueOut])
def get_list(
    channel_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
):
    records = list_manual_revenue(db, channel_id=channel_id, date_from=date_from, date_to=date_to)
    return [_to_out(r) for r in records]


@router.delete("/{record_id}")
def delete(record_id: int, db: Session = Depends(get_db)):
    ok = delete_manual_revenue(db, record_id)
    if not ok:
        raise HTTPException(status_code=404, detail="record not found")
    return {"ok": True}


def _to_out(record) -> dict:
    return {
        "id": record.id,
        "channel_id": record.channel_id,
        "channel_name": record.channel.name if record.channel else "",
        "revenue_date": record.revenue_date,
        "gross_revenue": str(record.gross_revenue),
        "memo": record.memo,
        "created_at": record.created_at.isoformat() if record.created_at else "",
        "updated_at": record.updated_at.isoformat() if record.updated_at else "",
    }
