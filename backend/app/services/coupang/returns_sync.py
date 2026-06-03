# returns_sync.py — 반품/취소/교환 동기화 Harness (순매출 차감 회계축 적재)
# 흐름: 계정별 → [RETURN·CANCEL 목록(SA) → coupang_return_item upsert] →
#       [반품철회 이력(SA) → withdrawn 마킹] → [교환 목록(SA) → coupang_exchange upsert].
# 트랙 D-8: vendor 2계정(WING1·WING2) 순회(RG 중복 불필요). 호출은 서버 IP에서만(로컬 403).
# 트랙 D-3: 사실/지표 정리만 — 전략판단 없음.
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.clients.coupang import CoupangExchangeClient, CoupangReturnClient
from app.config import get_coupang_config
from app.models import CoupangExchange, CoupangReturnItem

log = logging.getLogger(__name__)

RETURN_ACCOUNTS = ["COUPANG_WING1", "COUPANG_WING2"]
_MAX_SPAN_DAYS = 31  # 반품 목록 조회 최대 조회기간(쿠팡 제약)


def _date_windows(days: int) -> list[tuple[str, str]]:
    """오늘 기준 과거 days일을 ≤31일 윈도우들로 분할. 각 ("yyyy-MM-dd","yyyy-MM-dd")."""
    today = datetime.now().date()
    start = today - timedelta(days=max(days, 1))
    windows: list[tuple[str, str]] = []
    cursor = start
    while cursor <= today:
        chunk_end = min(cursor + timedelta(days=_MAX_SPAN_DAYS - 1), today)
        windows.append((cursor.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        cursor = chunk_end + timedelta(days=1)
    return windows


def _parse_dt(value) -> datetime | None:
    """쿠팡 createdAt(ISO-8601 또는 yyyy-MM-ddThh:mm:ss)을 naive datetime으로. 실패 시 None."""
    if not value:
        return None
    s = str(value)
    try:
        dt = datetime.fromisoformat(s)
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def _upsert_return_item(
    db: Session, account_key: str, vendor_id: str, receipt: dict, item: dict
) -> bool:
    """반품 옵션 1건을 coupang_return_item에 upsert ((receipt_id, vendor_item_id) 기준)."""
    receipt_id = str(receipt.get("receiptId") or "")
    vii = str(item.get("vendorItemId") or "")
    if not receipt_id or not vii:
        return False
    row = (
        db.query(CoupangReturnItem)
        .filter(
            CoupangReturnItem.receipt_id == receipt_id,
            CoupangReturnItem.vendor_item_id == vii,
        )
        .first()
    )
    if row is None:
        row = CoupangReturnItem(receipt_id=receipt_id, vendor_item_id=vii)
        db.add(row)
    row.account_key = account_key
    row.vendor_id = vendor_id
    row.order_id = str(receipt.get("orderId") or "")
    row.receipt_type = receipt.get("receiptType") or "RETURN"
    row.receipt_status = receipt.get("receiptStatus")
    row.cancel_count = int(item.get("cancelCount") or 0)
    row.purchase_count = item.get("purchaseCount")
    row.seller_product_id = (
        str(item.get("sellerProductId")) if item.get("sellerProductId") else None
    )
    row.seller_product_name = (item.get("sellerProductName") or "")[:300] or None
    row.vendor_item_name = (item.get("vendorItemName") or "")[:300] or None
    row.fault_by_type = receipt.get("faultByType")
    # 사유: 카테고리 + 상세 (사실 기록만)
    cat = receipt.get("cancelReasonCategory1") or ""
    detail = receipt.get("cancelReason") or ""
    row.cancel_reason = (f"{cat} {detail}".strip() or None)
    row.release_status = item.get("releaseStatus")
    row.pre_refund = receipt.get("preRefund")
    row.requested_at = _parse_dt(receipt.get("createdAt"))
    row.modified_at = _parse_dt(receipt.get("modifiedAt"))
    return True


def _mark_withdrawn(db: Session, account_key: str, vendor_id: str, withdraw: dict) -> int:
    """반품철회 1건을 받아 해당 (receipt_id, vendor_item_id) 행의 withdrawn=True. 갱신 행수 반환."""
    cancel_id = str(withdraw.get("cancelId") or "")
    if not cancel_id:
        return 0
    vii_list = [str(v) for v in (withdraw.get("vendorItemIds") or [])]
    q = db.query(CoupangReturnItem).filter(CoupangReturnItem.receipt_id == cancel_id)
    if vii_list:
        q = q.filter(CoupangReturnItem.vendor_item_id.in_(vii_list))
    count = 0
    for row in q.all():
        if not row.withdrawn:
            count += 1
        row.withdrawn = True
    return count


def _upsert_exchange(
    db: Session, account_key: str, vendor_id: str, exchange: dict
) -> int:
    """교환 1건의 아이템들을 coupang_exchange에 upsert. 적재된 옵션 행수 반환.

    exchangeItems[] 내부 구조가 명세상 미확정 → 방어적 추출(여러 후보 키 시도).
    vendorItemId를 못 찾으면 스킵+로그(추정 금지). 라이브에서 실제 구조 확인 후 보정.
    """
    exchange_id = str(exchange.get("exchangeId") or "")
    order_id = str(exchange.get("orderId") or "")
    if not exchange_id:
        return 0
    items = (
        exchange.get("exchangeItems")
        or exchange.get("items")
        or exchange.get("orderItems")
        or []
    )
    n = 0
    for item in items:
        vii = str(item.get("vendorItemId") or "")
        if not vii:
            log.info("교환 %s 아이템에 vendorItemId 없음 → 스킵(라이브 구조 확인 필요)", exchange_id)
            continue
        row = (
            db.query(CoupangExchange)
            .filter(
                CoupangExchange.exchange_id == exchange_id,
                CoupangExchange.vendor_item_id == vii,
            )
            .first()
        )
        if row is None:
            row = CoupangExchange(exchange_id=exchange_id, vendor_item_id=vii)
            db.add(row)
        row.account_key = account_key
        row.vendor_id = vendor_id
        row.order_id = order_id
        row.exchange_status = exchange.get("exchangeStatus")
        row.order_delivery_status = exchange.get("orderDeliveryStatusCode")
        row.refer_type = exchange.get("referType")
        row.vendor_item_name = (item.get("vendorItemName") or "")[:300] or None
        row.exchange_count = item.get("quantity") or item.get("cancelCount")
        row.requested_at = _parse_dt(exchange.get("createdAt"))
        n += 1
    return n


def sync_account_returns(db: Session, account_key: str, *, days: int = 35) -> dict:
    """한 계정의 반품/취소/교환을 과거 days일 동기화. 반환: 통계 dict.

    하드에러(config 누락)는 결과 dict의 error로 표면화 → 소비자가 raise 판단.
    """
    cfg = get_coupang_config(account_key)
    if cfg is None:
        return {"account": account_key, "error": "config_missing"}
    ret_client = CoupangReturnClient(cfg)
    exc_client = CoupangExchangeClient(cfg)
    vendor_id = cfg.vendor_id
    stats = {
        "account": account_key, "vendor_id": vendor_id,
        "returns": 0, "cancels": 0, "items": 0,
        "withdrawn": 0, "exchanges": 0, "errors": 0,
    }
    windows = _date_windows(days)

    for win_from, win_to in windows:
        # 반품(RETURN) + 취소(CANCEL) 두 타입. CANCEL은 status·orderId 제외(쿠팡 제약).
        for cancel_type in ("RETURN", "CANCEL"):
            try:
                for receipt in ret_client.iter_return_requests(
                    created_at_from=win_from, created_at_to=win_to, cancel_type=cancel_type
                ):
                    rtype = receipt.get("receiptType") or cancel_type
                    if rtype == "CANCEL":
                        stats["cancels"] += 1
                    else:
                        stats["returns"] += 1
                    for item in receipt.get("returnItems", []) or []:
                        if _upsert_return_item(db, account_key, vendor_id, receipt, item):
                            stats["items"] += 1
            except Exception:  # noqa: BLE001 — 한 윈도우 실패가 전체를 막지 않게
                log.exception("반품 목록 동기화 실패 %s %s~%s (%s)",
                              account_key, win_from, win_to, cancel_type)
                stats["errors"] += 1

        # 반품철회 → withdrawn 마킹 (차감 제외)
        try:
            for withdraw in ret_client.iter_return_withdraw(
                date_from=win_from, date_to=win_to
            ):
                stats["withdrawn"] += _mark_withdrawn(db, account_key, vendor_id, withdraw)
        except Exception:  # noqa: BLE001
            log.exception("반품철회 동기화 실패 %s %s~%s", account_key, win_from, win_to)
            stats["errors"] += 1

        # 교환 (운영 기록 — 회계 차감 없음). createdAt 형식은 분초까지.
        try:
            ex_from = f"{win_from}T00:00:00"
            ex_to = f"{win_to}T23:59:59"
            for exchange in exc_client.iter_exchange_requests(
                created_at_from=ex_from, created_at_to=ex_to
            ):
                stats["exchanges"] += _upsert_exchange(db, account_key, vendor_id, exchange)
        except Exception:  # noqa: BLE001
            log.exception("교환 동기화 실패 %s %s~%s", account_key, win_from, win_to)
            stats["errors"] += 1

    db.commit()
    log.info("쿠팡 반품/교환 동기화 완료 %s: %s", account_key, stats)
    return stats


def sync_all_returns(db: Session, *, days: int = 35) -> list[dict]:
    """2개 셀러계정(WING1·WING2) 반품/취소/교환 전체 동기화."""
    return [sync_account_returns(db, key, days=days) for key in RETURN_ACCOUNTS]
