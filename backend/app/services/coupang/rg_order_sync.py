# rg_order_sync.py — 로켓그로스 주문 동기화 Harness (향후 RG 매출, P3/D-14)
# 흐름: 계정별 → RG주문 목록(SA, ≤30일 윈도우·nextToken 순회) → coupang_rg_order_item upsert.
# 트랙 D-8: vendor 2계정 순회. 기존 Order와 분리 적재(이중계산 방지). rg_open_api 분당 50회.
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.clients.coupang import CoupangRocketGrowthClient
from app.clients.coupang._base import CoupangReadError
from app.config import get_coupang_config
from app.models import CoupangRgOrderItem

log = logging.getLogger(__name__)

RG_ACCOUNTS = ["COUPANG_WING1", "COUPANG_WING2"]
_KST = timezone(timedelta(hours=9))
_MAX_WINDOW_DAYS = 30  # API 최대 조회 윈도우 (명세 §4)


def _kst_today() -> datetime:
    return datetime.now(_KST)


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError):
        return None


def _parse_paid_at(value) -> datetime | None:
    """paidAt 정규화 → KST naive datetime. 목록 API=ms epoch 문자열, 단건 API=ISO 문자열(둘 다 방어)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # ms epoch (예 "1746093162000")
    if s.isdigit():
        try:
            return datetime.fromtimestamp(int(s) / 1000, _KST).replace(tzinfo=None)
        except (ValueError, OverflowError, OSError):
            return None
    # ISO (예 "2024-06-02T17:13:27Z")
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is not None:
            dt = dt.astimezone(_KST).replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def _upsert_order_item(
    db: Session, account_key: str, vendor_id: str, order: dict, item: dict
) -> bool:
    """RG 주문 옵션 1건을 coupang_rg_order_item에 upsert ((order_id, vendor_item_id) 기준)."""
    order_id = order.get("orderId")
    vii = item.get("vendorItemId")
    if order_id is None or not vii:
        return False
    order_id, vii = str(order_id), str(vii)
    row = (
        db.query(CoupangRgOrderItem)
        .filter(
            CoupangRgOrderItem.order_id == order_id,
            CoupangRgOrderItem.vendor_item_id == vii,
        )
        .first()
    )
    if row is None:
        row = CoupangRgOrderItem(order_id=order_id, vendor_item_id=vii)
        db.add(row)
    row.account_key = account_key
    row.vendor_id = vendor_id
    row.product_name = (item.get("productName") or "")[:300]
    row.sales_quantity = item.get("salesQuantity")
    # ⚠️ 단가 필드 API 불일치: 목록=unitSalesPrice, 단건=salesPrice (명세 §4) → 둘 다 방어
    row.unit_sales_price = _dec(item.get("unitSalesPrice") if item.get("unitSalesPrice") is not None
                                else item.get("salesPrice"))
    row.currency = item.get("currency")
    row.paid_at = _parse_paid_at(order.get("paidAt"))
    return True


def _windows(date_from: datetime, date_to: datetime):
    """[from, to]를 ≤30일 윈도우로 분할 (yyyymmdd 문자열 쌍 yield)."""
    cur = date_from
    while cur <= date_to:
        win_end = min(cur + timedelta(days=_MAX_WINDOW_DAYS - 1), date_to)
        yield cur.strftime("%Y%m%d"), win_end.strftime("%Y%m%d")
        cur = win_end + timedelta(days=1)


def sync_account_rg_orders(db: Session, account_key: str, *, days: int = 30) -> dict:
    """한 계정의 RG 주문을 최근 days일(≤30일 윈도우 분할) 동기화. 반환: 통계 dict."""
    cfg = get_coupang_config(account_key)
    if cfg is None:
        return {"account": account_key, "error": "config_missing"}
    client = CoupangRocketGrowthClient(cfg)
    stats = {"account": account_key, "vendor_id": cfg.vendor_id,
             "orders": 0, "items": 0, "windows": 0}

    today = _kst_today()
    date_from = today - timedelta(days=days)
    try:
        for win_from, win_to in _windows(date_from, today):
            stats["windows"] += 1
            for order in client.iter_rg_orders(paid_date_from=win_from, paid_date_to=win_to):
                stats["orders"] += 1
                for item in order.get("orderItems", []) or []:
                    if _upsert_order_item(db, account_key, cfg.vendor_id, order, item):
                        stats["items"] += 1
    except CoupangReadError as e:
        db.commit()
        stats["read_error"] = str(e)
        log.error("RG 주문 동기화 읽기 실패 %s: %s", account_key, e)
        return stats

    db.commit()
    log.info("RG 주문 동기화 완료 %s: %s", account_key, stats)
    return stats


def sync_all_rg_orders(db: Session, *, days: int = 30) -> list[dict]:
    """2개 셀러계정(WING1·WING2) RG 주문 동기화."""
    return [sync_account_rg_orders(db, key, days=days) for key in RG_ACCOUNTS]
