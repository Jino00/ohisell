# sync_service.py — 채널 동기화 오케스트레이션
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.clients.base import BaseChannelClient, RawOrder
from app.clients.cafe24 import Cafe24Client
from app.clients.coupang import CoupangClient
from app.clients.naver import NaverClient
from app.config import get_cafe24_config, get_coupang_config, get_naver_config
from app.models import Channel, OAuthToken, Order, ProductChannelMapping, SyncLog

log = logging.getLogger(__name__)

MAX_RAW_DATA_SIZE = 10_000  # 10KB


def _get_client_for_channel(channel: Channel, db: Session | None = None) -> BaseChannelClient | None:
    """채널의 api_type에 따라 적절한 클라이언트 반환"""
    if channel.api_type == "hmac" and channel.api_config_key:
        config = get_coupang_config(channel.api_config_key)
        if config is None:
            return None
        return CoupangClient(config)

    if channel.api_type == "oauth2_bcrypt" and channel.api_config_key:
        config = get_naver_config(channel.api_config_key)
        if config is None:
            return None
        token_row = None
        if db:
            token_row = db.query(OAuthToken).filter(OAuthToken.channel_id == channel.id).first()
        return NaverClient(config, access_token=token_row.access_token if token_row else None)

    if channel.api_type == "oauth2_code" and channel.api_config_key:
        config = get_cafe24_config(channel.api_config_key)
        if config is None:
            return None
        token_row = None
        if db:
            token_row = db.query(OAuthToken).filter(OAuthToken.channel_id == channel.id).first()
        return Cafe24Client(
            config,
            access_token=token_row.access_token if token_row else None,
            refresh_token=token_row.refresh_token if token_row else None,
        )

    return None


def _auto_link_product(db: Session, order: Order) -> None:
    """주문의 platform_product_id로 상품 자동 매핑"""
    if order.product_id is not None:
        return
    mapping = db.query(ProductChannelMapping).filter(
        and_(
            ProductChannelMapping.channel_id == order.channel_id,
            ProductChannelMapping.channel_product_id == order.platform_product_id,
            ProductChannelMapping.is_active.is_(True),
        )
    ).first()
    if mapping:
        order.product_id = mapping.product_id


def _truncate_raw_data(raw: dict) -> str | None:
    """raw_data를 JSON 문자열로 변환, 최대 크기 제한"""
    try:
        s = json.dumps(raw, ensure_ascii=False, default=str)
        if len(s) > MAX_RAW_DATA_SIZE:
            return s[:MAX_RAW_DATA_SIZE] + "...(truncated)"
        return s
    except Exception:
        return None


def sync_channel_orders(
    db: Session,
    channel_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """단일 채널 주문 동기화

    Returns: {channel_id, channel_name, status, new_orders, updated_orders, errors}
    """
    channel = db.query(Channel).filter(Channel.id == channel_id).first()
    if not channel:
        return {"channel_id": channel_id, "status": "error", "errors": ["채널을 찾을 수 없습니다"]}

    if channel.api_type == "excel":
        return {
            "channel_id": channel_id,
            "channel_name": channel.name,
            "status": "skipped",
            "errors": ["엑셀 전용 채널입니다. 엑셀 업로드를 사용하세요."],
        }

    # 동시 실행 방지
    running = db.query(SyncLog).filter(
        and_(SyncLog.channel_id == channel_id, SyncLog.status == "running")
    ).first()
    if running:
        return {
            "channel_id": channel_id,
            "channel_name": channel.name,
            "status": "error",
            "errors": ["이미 동기화가 진행 중입니다"],
        }

    client = _get_client_for_channel(channel, db)
    if client is None:
        return {
            "channel_id": channel_id,
            "channel_name": channel.name,
            "status": "error",
            "errors": ["API 키가 설정되지 않았습니다. .env를 확인하세요."],
        }

    if date_from is None:
        date_from = date.today() - timedelta(days=7)
    if date_to is None:
        date_to = date.today()

    # SyncLog 시작
    sync_log = SyncLog(
        channel_id=channel_id,
        sync_type="orders",
        status="running",
        date_from=date_from,
        date_to=date_to,
    )
    db.add(sync_log)
    db.commit()

    new_count = 0
    updated_count = 0
    errors: list[str] = []

    try:
        raw_orders = client.fetch_orders(date_from, date_to)

        for raw in raw_orders:
            existing = db.query(Order).filter(
                and_(
                    Order.channel_id == channel_id,
                    Order.order_number == raw.order_number,
                    Order.platform_product_id == raw.platform_product_id,
                )
            ).first()

            if existing:
                existing.quantity = raw.quantity
                existing.selling_price = raw.selling_price
                existing.shipping_cost = raw.shipping_cost
                existing.status = raw.status
                existing.platform_product_name = raw.platform_product_name
                existing.raw_data = _truncate_raw_data(raw.raw_data)
                _auto_link_product(db, existing)
                updated_count += 1
            else:
                order = Order(
                    channel_id=channel_id,
                    order_number=raw.order_number,
                    platform_product_id=raw.platform_product_id,
                    platform_product_name=raw.platform_product_name,
                    quantity=raw.quantity,
                    selling_price=raw.selling_price,
                    shipping_cost=raw.shipping_cost,
                    order_date=datetime.fromisoformat(raw.order_date) if isinstance(raw.order_date, str) else raw.order_date,
                    status=raw.status,
                    raw_data=_truncate_raw_data(raw.raw_data),
                )
                _auto_link_product(db, order)
                db.add(order)
                new_count += 1

        db.commit()
        sync_log.status = "success"
        sync_log.records_synced = new_count + updated_count
        sync_log.completed_at = datetime.now()
        db.commit()

    except Exception as e:
        log.exception("동기화 에러 (channel=%s)", channel.name)
        errors.append(str(e))
        sync_log.status = "error"
        sync_log.error_message = str(e)
        sync_log.completed_at = datetime.now()
        db.commit()

    return {
        "channel_id": channel_id,
        "channel_name": channel.name,
        "status": sync_log.status,
        "new_orders": new_count,
        "updated_orders": updated_count,
        "errors": errors,
    }


def relink_unlinked_orders(db: Session, channel_id: int | None = None) -> int:
    """미링크 주문 재매핑 (매핑 추가 시 호출)"""
    query = db.query(Order).filter(Order.product_id.is_(None))
    if channel_id:
        query = query.filter(Order.channel_id == channel_id)

    count = 0
    for order in query.all():
        _auto_link_product(db, order)
        if order.product_id is not None:
            count += 1

    if count > 0:
        db.commit()
    return count
