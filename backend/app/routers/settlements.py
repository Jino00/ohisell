# routers/settlements.py — 정산 관리 API
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Channel, Settlement
from app.schemas import (
    SettlementListResponse,
    SettlementOut,
    SettlementSummary,
    SettlementUploadResult,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settlements", tags=["settlements"])


def _settlement_to_out(s: Settlement, channel_name: str = "") -> SettlementOut:
    """Settlement 모델 → SettlementOut 변환.

    product_amount = total_amount - shipping_fee (제품정산 파생).
    음수 방지 가드: shipping_fee가 total_amount 초과 시 0으로 클램프.
    """
    from decimal import Decimal as _D
    tot = s.total_amount or _D("0")
    ship = s.shipping_fee or _D("0")
    prod = tot - ship
    if prod < _D("0"):
        prod = _D("0")
    return SettlementOut(
        id=s.id,
        channel_id=s.channel_id,
        channel_name=channel_name,
        settlement_date=str(s.settlement_date.date()) if hasattr(s.settlement_date, "date") else str(s.settlement_date)[:10],
        settlement_period_start=str(s.settlement_period_start) if s.settlement_period_start else None,
        settlement_period_end=str(s.settlement_period_end) if s.settlement_period_end else None,
        total_amount=str(tot),
        product_amount=str(prod),
        commission=str(s.commission),
        net_amount=str(s.net_amount),
        shipping_fee=str(ship),
        order_count=s.order_count,
        source=s.source,
        memo=s.memo,
    )


@router.get("", response_model=SettlementListResponse)
def list_settlements(
    channel_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """정산 목록 조회 (필터 + 페이지네이션)"""
    query = db.query(Settlement)

    if channel_id:
        query = query.filter(Settlement.channel_id == channel_id)
    if date_from:
        query = query.filter(Settlement.settlement_date >= datetime.fromisoformat(date_from))
    if date_to:
        query = query.filter(Settlement.settlement_date <= datetime.fromisoformat(date_to + "T23:59:59"))

    total = query.count()
    rows = (
        query.order_by(Settlement.settlement_date.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    channel_map = {c.id: c.name for c in db.query(Channel).all()}
    items = [_settlement_to_out(s, channel_map.get(s.channel_id, "")) for s in rows]

    return SettlementListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/upload/{channel_id}", response_model=SettlementUploadResult)
async def upload_settlement_excel(
    channel_id: int,
    file: UploadFile,
    db: Session = Depends(get_db),
):
    """엑셀 업로드로 정산 데이터 일괄 등록

    필수 컬럼: 정산일, 총매출, 수수료, 정산금액
    선택 컬럼: 정산기간시작, 정산기간종료, 주문건수, 배송비, 메모
    """
    channel = db.query(Channel).filter(Channel.id == channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="채널을 찾을 수 없습니다")

    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="xlsx 또는 xls 파일만 업로드 가능합니다")

    try:
        import openpyxl
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl 패키지가 설치되지 않았습니다")

    try:
        content = await file.read()
        from io import BytesIO
        wb = openpyxl.load_workbook(BytesIO(content), read_only=True)
        ws = wb.active
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"엑셀 파일 읽기 실패: {e}")

    # 헤더 매핑
    rows_iter = ws.iter_rows(values_only=True)
    try:
        headers = [str(h).strip() if h else "" for h in next(rows_iter)]
    except StopIteration:
        raise HTTPException(status_code=400, detail="빈 엑셀 파일입니다")

    col_map: dict[str, int] = {}
    required = {"정산일": None, "총매출": None, "수수료": None, "정산금액": None}
    optional = {"정산기간시작": None, "정산기간종료": None, "주문건수": None, "배송비": None, "메모": None}

    for idx, h in enumerate(headers):
        if h in required:
            required[h] = idx
            col_map[h] = idx
        if h in optional:
            optional[h] = idx
            col_map[h] = idx

    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise HTTPException(status_code=400, detail=f"필수 컬럼 누락: {', '.join(missing)}")

    imported = 0
    skipped = 0
    errors: list[str] = []

    for row_num, row in enumerate(rows_iter, start=2):
        try:
            raw_date = row[col_map["정산일"]]
            if raw_date is None:
                skipped += 1
                continue

            # 날짜 파싱
            if isinstance(raw_date, datetime):
                settle_date = raw_date
            elif isinstance(raw_date, date):
                settle_date = datetime(raw_date.year, raw_date.month, raw_date.day)
            else:
                settle_date = datetime.fromisoformat(str(raw_date)[:10])

            total_amount = Decimal(str(row[col_map["총매출"]] or 0))
            commission = Decimal(str(row[col_map["수수료"]] or 0))
            net_amount = Decimal(str(row[col_map["정산금액"]] or 0))

            # 선택 필드
            period_start = None
            if "정산기간시작" in col_map and col_map["정산기간시작"] is not None:
                raw_ps = row[col_map["정산기간시작"]]
                if raw_ps:
                    if isinstance(raw_ps, (datetime, date)):
                        period_start = raw_ps if isinstance(raw_ps, date) else raw_ps.date()
                    else:
                        period_start = date.fromisoformat(str(raw_ps)[:10])

            period_end = None
            if "정산기간종료" in col_map and col_map["정산기간종료"] is not None:
                raw_pe = row[col_map["정산기간종료"]]
                if raw_pe:
                    if isinstance(raw_pe, (datetime, date)):
                        period_end = raw_pe if isinstance(raw_pe, date) else raw_pe.date()
                    else:
                        period_end = date.fromisoformat(str(raw_pe)[:10])

            order_count = None
            if "주문건수" in col_map and col_map["주문건수"] is not None:
                raw_oc = row[col_map["주문건수"]]
                if raw_oc is not None:
                    order_count = int(raw_oc)

            shipping_fee = Decimal("0")
            if "배송비" in col_map and col_map["배송비"] is not None:
                raw_sf = row[col_map["배송비"]]
                if raw_sf is not None:
                    shipping_fee = Decimal(str(raw_sf))

            memo = None
            if "메모" in col_map and col_map["메모"] is not None:
                raw_memo = row[col_map["메모"]]
                if raw_memo:
                    memo = str(raw_memo)

            settlement = Settlement(
                channel_id=channel_id,
                settlement_date=settle_date,
                settlement_period_start=period_start,
                settlement_period_end=period_end,
                total_amount=total_amount,
                commission=commission,
                net_amount=net_amount,
                order_count=order_count,
                shipping_fee=shipping_fee,
                source="excel",
                memo=memo,
            )
            db.add(settlement)
            imported += 1

        except (ValueError, InvalidOperation, TypeError) as e:
            errors.append(f"행 {row_num}: {e}")
            skipped += 1
        except Exception as e:
            errors.append(f"행 {row_num}: 예상치 못한 에러 - {e}")
            skipped += 1

    if imported > 0:
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"DB 저장 실패: {e}")

    wb.close()
    return SettlementUploadResult(imported=imported, skipped=skipped, errors=errors)


@router.get("/summary", response_model=SettlementSummary)
def settlement_summary(
    channel_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """정산 합계 요약"""
    query = db.query(Settlement)

    if channel_id:
        query = query.filter(Settlement.channel_id == channel_id)
    if date_from:
        query = query.filter(Settlement.settlement_date >= datetime.fromisoformat(date_from))
    if date_to:
        query = query.filter(Settlement.settlement_date <= datetime.fromisoformat(date_to + "T23:59:59"))

    result = db.query(
        func.coalesce(func.sum(Settlement.total_amount), 0),
        func.coalesce(func.sum(Settlement.commission), 0),
        func.coalesce(func.sum(Settlement.net_amount), 0),
        func.coalesce(func.sum(Settlement.shipping_fee), 0),
        func.count(Settlement.id),
    ).filter(
        *([Settlement.channel_id == channel_id] if channel_id else []),
        *([Settlement.settlement_date >= datetime.fromisoformat(date_from)] if date_from else []),
        *([Settlement.settlement_date <= datetime.fromisoformat(date_to + "T23:59:59")] if date_to else []),
    ).first()

    return SettlementSummary(
        total_amount=str(result[0]),
        total_commission=str(result[1]),
        total_net=str(result[2]),
        total_shipping_fee=str(result[3]),
        count=result[4],
    )


@router.delete("/{settlement_id}")
def delete_settlement(
    settlement_id: int,
    db: Session = Depends(get_db),
):
    """정산 삭제"""
    settlement = db.query(Settlement).filter(Settlement.id == settlement_id).first()
    if not settlement:
        raise HTTPException(status_code=404, detail="정산 데이터를 찾을 수 없습니다")

    db.delete(settlement)
    db.commit()
    return {"detail": "삭제 완료", "id": settlement_id}
