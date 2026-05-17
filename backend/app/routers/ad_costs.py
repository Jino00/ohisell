# routers/ad_costs.py — 광고비 조회 + GFA CSV 업로드 API
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_ad_db, get_db
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


@router.post("/gfa/upload")
async def upload_gfa_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """GFA(ADVoost) 광고비 CSV 업로드 → ad_costs 저장.
    파일 형식: theohi11_광고비 보고서_YYYYMMDD_YYYYMMDD.csv
    필수 컬럼: 기간(YYYY.MM.DD.), ADVoost 쇼핑(총비용)
    """
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="CSV 파일만 업로드 가능합니다.")

    content = await file.read()
    text_content = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text_content))

    naver_row = db.execute(
        text("SELECT id FROM channels WHERE code = 'NAVER' LIMIT 1")
    ).fetchone()
    if not naver_row:
        raise HTTPException(status_code=500, detail="NAVER 채널이 DB에 없습니다.")
    naver_id = naver_row[0]

    inserted = 0
    skipped = 0
    records: list[dict] = []

    for row in reader:
        date_str = row.get("기간", "").strip().rstrip(".")
        if not date_str:
            skipped += 1
            continue
        try:
            ad_date = date.fromisoformat(date_str.replace(".", "-"))
        except ValueError:
            skipped += 1
            continue

        spend_str = (row.get("ADVoost 쇼핑") or row.get("총비용") or "0").strip().replace(",", "")
        try:
            spend = Decimal(spend_str)
        except Exception:
            skipped += 1
            continue

        if spend <= 0:
            skipped += 1
            continue

        records.append({"date": ad_date, "spend": spend})

    if not records:
        raise HTTPException(status_code=422, detail="저장할 데이터가 없습니다. 컬럼(기간, ADVoost 쇼핑)을 확인하세요.")

    for rec in records:
        db.execute(
            text("""
                DELETE FROM ad_costs
                WHERE channel_id = :cid AND source = 'gfa:쇼핑' AND ad_date = :ad_date
            """),
            {"cid": naver_id, "ad_date": rec["date"].isoformat()},
        )
        db.execute(
            text("""
                INSERT INTO ad_costs (channel_id, product_id, ad_date, ad_spend, ad_revenue, source, created_at)
                VALUES (:cid, NULL, :ad_date, :spend, NULL, 'gfa:쇼핑', datetime('now'))
            """),
            {"cid": naver_id, "ad_date": rec["date"].isoformat(), "spend": str(rec["spend"])},
        )
        inserted += 1

    db.commit()

    total_spend = sum(r["spend"] for r in records)
    return {
        "inserted": inserted,
        "skipped": skipped,
        "total_spend": int(total_spend),
        "dates": [r["date"].isoformat() for r in records],
    }
