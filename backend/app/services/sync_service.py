# sync_service.py — 채널 동기화 오케스트레이션
from __future__ import annotations

import json
import logging
from app.utils.kst import kst_now, kst_today
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

        def _on_cafe24_token_refreshed(access_token, refresh_token, expires_at, refresh_expires_at):
            """cafe24 토큰 갱신 시 DB 자동 업데이트"""
            try:
                if token_row:
                    token_row.access_token = access_token
                    token_row.refresh_token = refresh_token
                    token_row.expires_at = expires_at
                    token_row.refresh_token_expires_at = refresh_expires_at
                    db.commit()
                    log.info("cafe24 토큰 DB 업데이트 완료 (channel_id=%d)", channel.id)
            except Exception as e:
                log.error("cafe24 토큰 DB 업데이트 실패: %s", e)
                raise

        def _cafe24_token_reader():
            """_CAFE24_REFRESH_LOCK 획득 후 DB 최신 토큰 재조회용 콜백"""
            db.refresh(token_row)
            return (token_row.refresh_token, token_row.access_token, token_row.expires_at)

        return Cafe24Client(
            config,
            access_token=token_row.access_token if token_row else None,
            refresh_token=token_row.refresh_token if token_row else None,
            on_token_refreshed=_on_cafe24_token_refreshed if db and token_row else None,
            token_reader=_cafe24_token_reader if db and token_row else None,
        )

    return None


def _is_coupang_order_for_channel(raw_data: dict, is_wing: bool) -> bool:
    """쿠팡 주문이 Wing/RG 채널에 맞는지 shipmentType으로 판별"""
    shipment_type = raw_data.get("shipmentType", "")
    # THIRD_PARTY = 판매자 직배송 (Wing)
    # GROWTH, COUPANG 등 = 쿠팡 물류 (로켓그로스)
    if is_wing:
        return shipment_type == "THIRD_PARTY"
    return shipment_type != "THIRD_PARTY"


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


# 매출에 잡히는 활성 주문상태. 취소 시 쿠팡 활성 ordersheets에서 사라짐(S6 reconcile 대상).
_ACTIVE_ORDER_STATUSES = ("delivered", "confirmed", "shipped")


def _reconcile_absent_orders(
    db: Session, channel_id: int, platform: str,
    date_from: date, date_to: date, fetched_ids: set[str],
    *, fetch_complete: bool = True, grace_days: int = 0,
) -> int:
    """S6 reconcile-by-absence: 윈도우 내 DB엔 활성인데 쿠팡 전체조회에 없는 주문을 cancelled 처리.

    취소된 주문은 쿠팡 활성 ordersheets에서 사라지고 취소접수(반품 API)도 없을 수 있어(라이브 확정
    2026-06-14, 트랙 D-10) Order.status가 stale(delivered/confirmed)로 남아 매출이 과다계상된다.
    전체조회가 성공한 윈도우에서 누락된 활성주문 = 쿠팡에서 사라짐 = 취소로 본다.

    안전장치(거짓취소 방지):
    - 쿠팡 채널만(disappearance 동작 라이브 확정). 타 채널은 조회 시맨틱이 달라 제외.
    - **fetch_complete=False면 건너뜀**(Codex P1#1): fetch_orders가 상태/일/페이지 일부라도 실패하면
      부분 목록이라 멀쩡한 주문을 거짓취소할 수 있음 → 전 스윕 성공 시에만 실행.
    - fetched_ids 비어있으면 건너뜀(전체조회 0건 → mass-cancel 위험).
    - **grace_days inset(Codex P1#2)**: fetch는 createdAt 필터, reconcile는 order_date(=paidAt) 필터.
      createdAt ≤ paidAt이므로 하한을 grace만큼 올리면(예 7일) 후보의 createdAt이 항상 fetch 윈도우
      안에 들어와 경계 거짓취소(가상계좌 등 결제지연)를 제거. 롤링 윈도우라 커버리지 손실 미미.
    - 블라스트 반경 캡: 윈도우 활성주문의 30%(최소 5건) 초과 누락이면 부분조회 실패 의심 → 중단.
    - 활성상태만 변경(cancelled/returned/pending은 불변), order_date 윈도우 내만.
    반환: cancelled 처리한 건수.
    """
    if platform != "coupang" or not fetched_ids or not fetch_complete:
        return 0
    eff_from = date_from + timedelta(days=grace_days)
    if eff_from > date_to:
        return 0
    win_start = datetime.combine(eff_from, datetime.min.time())
    win_end = datetime.combine(date_to, datetime.max.time())
    candidates = (
        db.query(Order)
        .filter(
            Order.channel_id == channel_id,
            Order.order_date >= win_start,
            Order.order_date <= win_end,
            Order.status.in_(_ACTIVE_ORDER_STATUSES),
        )
        .all()
    )
    absent = [o for o in candidates if o.order_number not in fetched_ids]
    if not absent:
        return 0
    cap = max(5, int(len(candidates) * 0.3))
    if len(absent) > cap:
        log.warning(
            "[reconcile] 채널 %s 윈도우 %s~%s 사라진 활성주문 %d건 > 캡 %d(활성 %d) — "
            "조회 불완전 의심, 취소처리 건너뜀(거짓취소 방지).",
            channel_id, date_from, date_to, len(absent), cap, len(candidates),
        )
        return 0
    for o in absent:
        o.status = "cancelled"
    log.info("[reconcile] 채널 %s 사라진 주문 %d건 cancelled(윈도우 %s~%s).",
             channel_id, len(absent), date_from, date_to)
    return len(absent)


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
        date_from = kst_today() - timedelta(days=7)
    if date_to is None:
        date_to = kst_today()

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
    reconciled_cancelled = 0
    errors: list[str] = []

    try:
        raw_orders = client.fetch_orders(date_from, date_to)

        # 쿠팡 Wing/RG 동일 API 키 → shipmentType으로 필터링
        if channel.platform == "coupang" and raw_orders:
            is_wing = "WING" in channel.code
            raw_orders = [
                r for r in raw_orders
                if _is_coupang_order_for_channel(r.raw_data, is_wing)
            ]

        # 그레인 = (주문번호, 상품번호, 주문라인ID). 네이버는 line_id=productOrderId라
        # 분할 주문라인을 각각 보존. 쿠팡/cafe24는 line_id="" → 동작 불변.
        seen: set[tuple[str, str, str]] = set()  # 같은 배치 내 중복 방지
        for raw in raw_orders:
            dedup_key = (raw.order_number, raw.platform_product_id, raw.platform_order_line_id)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            existing = db.query(Order).filter(
                and_(
                    Order.channel_id == channel_id,
                    Order.order_number == raw.order_number,
                    Order.platform_product_id == raw.platform_product_id,
                    Order.platform_order_line_id == raw.platform_order_line_id,
                )
            ).first()

            # 마이그레이션 호환 브리지: line_id 백필이 못 채운 레거시 행(line_id="")이
            # 남아 있고 지금 라인은 실제 line_id를 가졌다면, 새 행을 insert하지 말고
            # 레거시 행을 승격(promote)한다 — 안 그러면 빈 line_id 행 + 새 행이 공존해
            # 수량·매출이 이중 계산된다. 쿠팡/cafe24는 line_id=""이라 이 분기를 타지 않음.
            if existing is None and raw.platform_order_line_id:
                legacy = db.query(Order).filter(
                    and_(
                        Order.channel_id == channel_id,
                        Order.order_number == raw.order_number,
                        Order.platform_product_id == raw.platform_product_id,
                        Order.platform_order_line_id == "",
                    )
                ).first()
                if legacy is not None:
                    legacy.platform_order_line_id = raw.platform_order_line_id
                    existing = legacy

            if existing:
                existing.quantity = raw.quantity
                existing.selling_price = raw.selling_price
                existing.shipping_cost = raw.shipping_cost
                existing.status = raw.status
                existing.platform_product_name = raw.platform_product_name
                existing.payment_type = raw.payment_type
                existing.commission_amount = raw.commission_amount
                existing.raw_data = _truncate_raw_data(raw.raw_data)
                _auto_link_product(db, existing)
                updated_count += 1
            else:
                order = Order(
                    channel_id=channel_id,
                    order_number=raw.order_number,
                    platform_product_id=raw.platform_product_id,
                    platform_order_line_id=raw.platform_order_line_id,
                    platform_product_name=raw.platform_product_name,
                    quantity=raw.quantity,
                    selling_price=raw.selling_price,
                    shipping_cost=raw.shipping_cost,
                    order_date=datetime.fromisoformat(raw.order_date) if isinstance(raw.order_date, str) else raw.order_date,
                    status=raw.status,
                    payment_type=raw.payment_type,
                    commission_amount=raw.commission_amount,
                    raw_data=_truncate_raw_data(raw.raw_data),
                )
                _auto_link_product(db, order)
                db.add(order)
                db.flush()  # 즉시 flush해서 다음 루프의 SELECT에 반영
                new_count += 1

        # S6 reconcile-by-absence: 쿠팡에서 사라진(취소) 주문을 cancelled 처리(트랙 D-10).
        # raw_orders는 위에서 채널 필터(wing/rg) 완료된 목록 → 전체조회 성공 시 fetched_ids로 사용.
        # fetch_complete=클라이언트 완전성 플래그(부분조회면 reconcile 차단, Codex P1#1).
        # grace_days=10: createdAt vs paidAt 경계 거짓취소 방지(Codex P1#2). 근거 — 활성(결제완료)
        # 주문의 created→paid 간격은 카드=즉시, 가상계좌=입금기한(쿠팡 최대 7일) 내 또는 자동취소.
        # 즉 활성주문의 최대 간격 ≤7일 → 마진 포함 10일이면 후보의 createdAt이 항상 fetch 윈도우 안.
        # (쿠팡이 가상계좌 기한을 10일 초과로 늘리면 재검토 — 트랙 D-10.)
        fetched_ids = {r.order_number for r in raw_orders}
        reconciled_cancelled = _reconcile_absent_orders(
            db, channel_id, channel.platform, date_from, date_to, fetched_ids,
            fetch_complete=getattr(client, "last_fetch_complete", False),
            grace_days=10,
        )

        db.commit()
        sync_log.status = "success"
        sync_log.records_synced = new_count + updated_count
        sync_log.completed_at = kst_now()
        db.commit()

    except Exception as e:
        log.exception("동기화 에러 (channel=%s)", channel.name)
        errors.append(str(e))
        try:
            db.rollback()
            sync_log.status = "error"
            sync_log.error_message = str(e)[:500]
            sync_log.completed_at = kst_now()
            db.commit()
        except Exception:
            pass  # sync_log 업데이트 실패해도 진행

    return {
        "channel_id": channel_id,
        "channel_name": channel.name,
        "status": sync_log.status,
        "new_orders": new_count,
        "updated_orders": updated_count,
        "reconciled_cancelled": reconciled_cancelled,
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
