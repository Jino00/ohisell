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


# ──────────────────────────────────────────────
# 쿠팡 정산 도메인 조회 (P4, D-10/D-11) — 사실/지표 정리만(D-3)
# ──────────────────────────────────────────────
@router.get("/coupang-fees")
def coupang_fee_comparison(
    only_mismatch: bool = Query(False),
    limit: int = Query(300, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """쿠팡 옵션별 실측 판매수수료율(serviceFeeRatio) 목록 (D-10 → D-13).

    ⚠️ D-13: registered(sale_agent_commission)는 라이브에서 전부 0(판매대행 수수료라
    카테고리 판매수수료 아님) → registered_ratio는 참고불가(항상 0·mismatch 항상 False).
    실제 수수료 이상 감사는 옵션 자기기준선(정착 실측율) 방식으로 /coupang-fee-anomalies
    (change_type=rate_drift)에서 수행한다. 이 엔드포인트는 옵션별 실측율 현황 표시용.
    사실 정리만(D-3) — 판단은 Jino. only_mismatch=True면 (구)불일치만(현재 무의미).
    """
    from app.models import CoupangProductItem, CoupangRevenueFee

    # 옵션별 최근 매출내역 행(id 최대 ≈ 최근 적재)을 실측율 대표값으로
    sub = (
        db.query(func.max(CoupangRevenueFee.id))
        .filter(CoupangRevenueFee.service_fee_ratio.isnot(None))
        .group_by(CoupangRevenueFee.vendor_item_id)
        .subquery()
    )
    rows = db.query(CoupangRevenueFee).filter(CoupangRevenueFee.id.in_(sub)).all()
    pi_map = {pi.vendor_item_id: pi for pi in db.query(CoupangProductItem).all()}

    result = []
    for r in rows:
        pi = pi_map.get(r.vendor_item_id)
        registered = pi.sale_agent_commission if pi else None
        observed = r.service_fee_ratio
        # 등록율 0/None은 '미설정'(쿠팡 최소 4%) → 비교 불가, mismatch 아님(라이브 실측 보정)
        reg_valid = registered is not None and Decimal(str(registered)) > 0
        mismatch = (
            reg_valid and observed is not None
            and abs(Decimal(str(observed)) - Decimal(str(registered))) > Decimal("0.01")
        )
        if only_mismatch and not mismatch:
            continue
        result.append({
            "vendor_item_id": r.vendor_item_id,
            "vendor_item_name": r.vendor_item_name,
            "account_key": r.account_key,
            "observed_ratio": str(observed) if observed is not None else None,
            "registered_ratio": str(registered) if registered is not None else None,
            "mismatch": mismatch,
            "recent_recognition_date": str(r.recognition_date) if r.recognition_date else None,
        })
    result.sort(key=lambda x: (not x["mismatch"], x["vendor_item_id"]))
    return {"count": len(result), "items": result[:limit]}


@router.get("/coupang-fee-anomalies")
def coupang_fee_anomalies(
    include_resolved: bool = Query(False),
    db: Session = Depends(get_db),
):
    """쿠팡 수수료율 감사 로그 (D-13 자기기준선). 미해소 이상=Jino 확인 대상.

    change_type=rate_drift: 한 옵션의 정착 실측율(기준선=registered_ratio)과 다른 율
    (observed_ratio)이 기간 내 감지됨 = 율 변동/과오청구 의심. 자동 판단·자동 수용 안 함(D-3).
    Jino가 정당변동/과오청구 판정 후 수동 resolve. (구 D-11 legitimate/anomaly·자동갱신 폐기.)
    """
    from app.models import CoupangFeeChangeLog

    q = db.query(CoupangFeeChangeLog).order_by(CoupangFeeChangeLog.detected_at.desc())
    if not include_resolved:
        q = q.filter(CoupangFeeChangeLog.resolved.is_(False))
    rows = q.limit(500).all()
    return [{
        "vendor_item_id": r.vendor_item_id,
        "change_type": r.change_type,
        "registered_ratio": str(r.registered_ratio) if r.registered_ratio is not None else None,
        "observed_ratio": str(r.observed_ratio) if r.observed_ratio is not None else None,
        "reauthored_ratio": str(r.reauthored_ratio) if r.reauthored_ratio is not None else None,
        "order_id": r.order_id,
        "recognition_date": str(r.recognition_date) if r.recognition_date else None,
        "resolved": r.resolved,
        "note": r.note,
        "detected_at": r.detected_at.isoformat() if r.detected_at else None,
    } for r in rows]


@router.get("/coupang-payouts")
def coupang_payouts(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """쿠팡 지급내역(주/월 정산) 목록 — 서비스이용료·차감·최종지급액 사실 정리(D-3)."""
    from app.models import CoupangSettlementPayout

    rows = (
        db.query(CoupangSettlementPayout)
        .order_by(CoupangSettlementPayout.settlement_date.desc())
        .limit(limit)
        .all()
    )
    return [{
        "account_key": r.account_key,
        "vendor_id": r.vendor_id,
        "settlement_type": r.settlement_type,
        "settlement_date": str(r.settlement_date) if r.settlement_date else None,
        "recognition_from": str(r.revenue_recognition_date_from) if r.revenue_recognition_date_from else None,
        "recognition_to": str(r.revenue_recognition_date_to) if r.revenue_recognition_date_to else None,
        "total_sale": str(r.total_sale),
        "service_fee": str(r.service_fee),
        "seller_service_fee": str(r.seller_service_fee),
        "deduction_amount": str(r.deduction_amount),
        "final_amount": str(r.final_amount),
        "status": r.status,
    } for r in rows]


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
