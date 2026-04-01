# routers/ad_costs.py — 광고비 조회 API (ohi-ad-intelligence 연동)
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query

from app.database import get_ad_db
from app.schemas import AdSpendByOption, AdSpendDaily
from app.services.ad_cost_reader import get_ad_spend_by_option, get_daily_ad_spend

router = APIRouter(prefix="/api/ad-costs", tags=["ad-costs"])


@router.get("/daily", response_model=list[AdSpendDaily])
def daily_ad_spend(
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: str = Query(..., description="YYYY-MM-DD"),
    ad_db=Depends(get_ad_db),
):
    """일별 총 광고비 조회"""
    if ad_db is None:
        return []
    return get_daily_ad_spend(
        ad_db,
        date.fromisoformat(date_from),
        date.fromisoformat(date_to),
    )


@router.get("/by-option", response_model=list[AdSpendByOption])
def ad_spend_by_option(
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: str = Query(..., description="YYYY-MM-DD"),
    option_id: str | None = Query(None),
    ad_db=Depends(get_ad_db),
):
    """상품(option_id)별 광고비 집계"""
    if ad_db is None:
        return []
    return get_ad_spend_by_option(
        ad_db,
        date.fromisoformat(date_from),
        date.fromisoformat(date_to),
        option_id,
    )
