# routers/sync.py — 채널 동기화 API
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Channel, SyncLog
from app.schemas import SyncRequest, SyncResult, SyncStatusOut
from app.services.sync_service import sync_channel_orders

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/channel/{channel_id}", response_model=SyncResult)
def sync_channel(channel_id: int, body: SyncRequest | None = None, db: Session = Depends(get_db)):
    """단일 채널 주문 동기화"""
    date_from = None
    date_to = None
    if body:
        if body.date_from:
            date_from = date.fromisoformat(body.date_from)
        if body.date_to:
            date_to = date.fromisoformat(body.date_to)

    result = sync_channel_orders(db, channel_id, date_from, date_to)
    return result


@router.post("/all", response_model=list[SyncResult])
def sync_all(body: SyncRequest | None = None, db: Session = Depends(get_db)):
    """API 연동 가능한 전체 채널 동기화"""
    channels = db.query(Channel).filter(Channel.api_type != "excel").all()
    results = []

    date_from = None
    date_to = None
    if body:
        if body.date_from:
            date_from = date.fromisoformat(body.date_from)
        if body.date_to:
            date_to = date.fromisoformat(body.date_to)

    for ch in channels:
        result = sync_channel_orders(db, ch.id, date_from, date_to)
        results.append(result)

    return results


@router.post("/coupang-products")
def sync_coupang_products(
    refresh_inventory: bool = True,
    max_products: int | None = None,
    db: Session = Depends(get_db),
):
    """쿠팡 상품 마스터+채널매핑 동기화 (조망 결합축 적재).

    트랙 D-8: 쿠팡 Open API는 서버 IP 화이트리스트 — 서버에서만 동작(로컬 403).
    max_products: 계정별 상한(드라이런/부분동기화용). refresh_inventory: 재고/판매상태 새로고침.
    """
    from app.services.coupang.product_sync import sync_all_products, sync_account_products, PRODUCT_ACCOUNTS

    if max_products is not None:
        return [
            sync_account_products(
                db, key, refresh_inventory=refresh_inventory, max_products=max_products
            )
            for key in PRODUCT_ACCOUNTS
        ]
    return sync_all_products(db, refresh_inventory=refresh_inventory)


@router.post("/coupang-returns")
def sync_coupang_returns(
    days: int = 35,
    db: Session = Depends(get_db),
):
    """쿠팡 반품/취소/교환 동기화 (순매출 차감 회계축 적재).

    트랙 D-8: 쿠팡 Open API는 서버 IP 화이트리스트 — 서버에서만 동작(로컬 403).
    트랙 D-3: 사실/지표 정리만(전략판단 없음). days: 과거 동기화 기간(31일 윈도우로 자동 분할).
    """
    from app.services.coupang.returns_sync import sync_all_returns

    return sync_all_returns(db, days=days)


@router.post("/coupang-settlement")
def sync_coupang_settlement(
    days: int = 90,
    months: int = 6,
    db: Session = Depends(get_db),
):
    """쿠팡 정산(매출내역+지급내역) 동기화 + 수수료 감사 (회계 진짜 순이익 — D-13).

    트랙 D-8: 쿠팡 Open API는 서버 IP 화이트리스트 — 서버에서만 동작(로컬 403).
    트랙 D-3: 사실/지표 정리만(전략판단 없음). days: 매출내역 인식일 과거기간(7일 윈도우 분할),
    months: 지급내역 인식월 수. 수수료 감사(D-13)=각 옵션 자기 정착 실측율(mode) 기준선 대비
    율 변동 감지 → rate_drift 플래그(자동판단 금지, Jino 확인). stats fee_options_checked/fee_anomaly.
    """
    from app.services.coupang.settlement_sync import sync_all_settlement

    return sync_all_settlement(db, days=days, months=months)


@router.get("/status", response_model=list[SyncStatusOut])
def sync_status(db: Session = Depends(get_db)):
    """채널별 마지막 동기화 상태"""
    channels = db.query(Channel).all()
    statuses = []

    for ch in channels:
        last_log = (
            db.query(SyncLog)
            .filter(SyncLog.channel_id == ch.id)
            .order_by(desc(SyncLog.started_at))
            .first()
        )
        statuses.append(SyncStatusOut(
            channel_id=ch.id,
            channel_name=ch.name,
            last_sync=last_log.completed_at if last_log else None,
            status=last_log.status if last_log else None,
            records_synced=last_log.records_synced if last_log else 0,
        ))

    return statuses
