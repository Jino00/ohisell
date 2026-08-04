# routers/monthly_fixed_cost.py — 월 고정비 입력 API (POST/GET/DELETE)
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Channel
from app.services.monthly_fixed_cost_service import (
    FIXED_COST_ITEMS,
    delete_monthly_fixed_cost,
    list_monthly_fixed_cost,
    upsert_monthly_fixed_cost,
)

router = APIRouter(prefix="/api/monthly-fixed-cost", tags=["monthly_fixed_cost"])


class MonthlyFixedCostIn(BaseModel):
    channel_id: int
    year_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    item: str
    amount: Decimal = Field(ge=0)
    memo: Optional[str] = None


class MonthlyFixedCostOut(BaseModel):
    id: int
    channel_id: int
    channel_name: str
    year_month: str
    item: str
    amount: str
    memo: Optional[str]
    created_at: str
    updated_at: str


def _to_out(record, db: Session) -> MonthlyFixedCostOut:
    ch = db.query(Channel).filter(Channel.id == record.channel_id).first()
    return MonthlyFixedCostOut(
        id=record.id,
        channel_id=record.channel_id,
        channel_name=ch.name if ch else "",
        year_month=record.year_month,
        item=record.item,
        amount=str(record.amount),
        memo=record.memo,
        created_at=str(record.created_at),
        updated_at=str(record.updated_at),
    )


@router.get("/items", response_model=list[str])
def get_items():
    """입력 가능한 항목명 — 화면이 이 목록으로 셀렉트를 그린다(오타로 항목이 갈리는 것 방지)."""
    return list(FIXED_COST_ITEMS)


@router.post("", response_model=MonthlyFixedCostOut)
def create_or_update(body: MonthlyFixedCostIn, db: Session = Depends(get_db)):
    try:
        record = upsert_monthly_fixed_cost(
            db,
            channel_id=body.channel_id,
            year_month=body.year_month,
            item=body.item,
            amount=body.amount,
            memo=body.memo,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_out(record, db)


@router.get("", response_model=list[MonthlyFixedCostOut])
def get_list(
    channel_id: Optional[int] = None,
    month_from: Optional[str] = None,
    month_to: Optional[str] = None,
    db: Session = Depends(get_db),
):
    try:
        records = list_monthly_fixed_cost(
            db, channel_id=channel_id, month_from=month_from, month_to=month_to
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return [_to_out(r, db) for r in records]


@router.delete("/{record_id}")
def remove(record_id: int, db: Session = Depends(get_db)):
    if not delete_monthly_fixed_cost(db, record_id):
        raise HTTPException(status_code=404, detail="해당 고정비 항목이 없습니다")
    return {"ok": True}
