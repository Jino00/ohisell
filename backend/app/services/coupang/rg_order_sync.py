# rg_order_sync.py — 로켓그로스 주문 동기화 Harness (향후 RG 매출, P3/D-14)
# 흐름: 계정별 → RG주문 목록(SA, ≤30일 윈도우·nextToken 순회) → coupang_rg_order_item upsert.
# 트랙 D-8: vendor 2계정 순회. 기존 Order와 분리 적재(이중계산 방지). rg_open_api 분당 50회.
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from app.utils.kst import kst_now, kst_today
from decimal import Decimal

from sqlalchemy.orm import Session

from app.clients.coupang import CoupangRocketGrowthClient
from app.clients.coupang._base import CoupangReadError
from app.config import get_coupang_config
from app.models import CoupangRgOrderItem

log = logging.getLogger(__name__)

RG_ACCOUNTS = ["COUPANG_WING1", "COUPANG_WING2"]
from zoneinfo import ZoneInfo as _ZI
_KST = _ZI('Asia/Seoul')
_MAX_WINDOW_DAYS = 30  # API 최대 조회 윈도우 (명세 §4)




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


def _merge_order_items(order: dict) -> list[dict]:
    """같은 주문 안 중복 vendorItemId 라인 병합 — salesQuantity 합산.

    ★라이브 실측 2026-07-17(주문 24101555557691): 목록·단건 API 모두 동일 옵션을
    두 라인(qty 2+2)으로 반환 — (order_id, vendor_item_id) grain 가정을 API가 깬다.
    미병합 시 autoflush=False 세션(database.py)에서 이중 INSERT → UNIQUE 위반으로
    그날 배치 전체 롤백(2026-07-11~17 prod 6일 침묵 사고).
    """
    merged: dict[str, dict] = {}
    for item in order.get("orderItems", []) or []:
        vii = item.get("vendorItemId")
        if not vii:
            continue
        key = str(vii)
        prev = merged.get(key)
        if prev is None:
            merged[key] = dict(item)
            continue
        prev_qty, cur_qty = prev.get("salesQuantity"), item.get("salesQuantity")
        total = (prev_qty or 0) + (cur_qty or 0)
        if prev_qty is not None or cur_qty is not None:
            prev["salesQuantity"] = total
        # 단가가 라인마다 다르면(프로모션 분할 등 가능성) 수량가중 평균으로
        # 매출 불변식(Σ qty×price) 보존 — 하류 3곳이 qty×unit_price로 매출 계산.
        p_prev = _dec(prev.get("unitSalesPrice") if prev.get("unitSalesPrice") is not None
                      else prev.get("salesPrice"))
        p_cur = _dec(item.get("unitSalesPrice") if item.get("unitSalesPrice") is not None
                     else item.get("salesPrice"))
        if (p_prev is not None and p_cur is not None and p_prev != p_cur
                and prev_qty and cur_qty):
            prev["unitSalesPrice"] = str(
                (p_prev * prev_qty + p_cur * cur_qty) / total
            )
            log.warning(
                "RG 주문 %s 옵션 %s 중복 라인 단가 상이(%s vs %s) → 수량가중 %s",
                order.get("orderId"), key, p_prev, p_cur, prev["unitSalesPrice"],
            )
        log.warning(
            "RG 주문 %s 옵션 %s 중복 라인 병합 (누적 qty=%s)",
            order.get("orderId"), key, prev.get("salesQuantity"),
        )
    return list(merged.values())


def _upsert_order_item(
    db: Session, account_key: str, vendor_id: str, order: dict, item: dict,
    pending: dict[tuple[str, str], CoupangRgOrderItem],
) -> bool:
    """RG 주문 옵션 1건을 coupang_rg_order_item에 upsert ((order_id, vendor_item_id) 기준).

    pending: 이 동기화 런에서 add()된 미커밋 행 캐시. autoflush=False라 query가
    pending INSERT를 못 보므로, 같은 키 재등장(페이지네이션 중복 등) 시 재INSERT를 막는다.
    """
    order_id = order.get("orderId")
    vii = item.get("vendorItemId")
    if order_id is None or not vii:
        return False
    order_id, vii = str(order_id), str(vii)
    row = pending.get((order_id, vii))
    if row is None:
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
    pending[(order_id, vii)] = row
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
    """[from, to](양끝 포함)를 ≤30일 윈도우로 분할 (yyyymmdd 문자열 쌍 yield).

    ★ RG 주문 API의 paidDateTo는 배타적(exclusive) — 해당일 00:00:00 기준이라
    paidDateTo=X면 X일 당일 결제건이 0건으로 빠진다(라이브 실측 2026-06-05:
    to=20260604→0건, to=20260605→06-04 22건). 따라서 끝날짜(win_end) 당일을
    포함하려면 paidDateTo를 win_end+1일로 전달한다. span은 최대 30일 유지
    (cur ~ cur+29일 → paidDateTo=cur+30일, 30일 span 라이브 통과 확인).
    """
    cur = date_from
    while cur <= date_to:
        win_end = min(cur + timedelta(days=_MAX_WINDOW_DAYS - 1), date_to)
        # paidDateTo는 배타적 → win_end 당일을 포함하려면 +1일
        yield cur.strftime("%Y%m%d"), (win_end + timedelta(days=1)).strftime("%Y%m%d")
        cur = win_end + timedelta(days=1)


def sync_account_rg_orders(db: Session, account_key: str, *, days: int = 30) -> dict:
    """한 계정의 RG 주문을 최근 days일(≤30일 윈도우 분할) 동기화. 반환: 통계 dict."""
    cfg = get_coupang_config(account_key)
    if cfg is None:
        return {"account": account_key, "error": "config_missing"}
    client = CoupangRocketGrowthClient(cfg)
    stats = {"account": account_key, "vendor_id": cfg.vendor_id,
             "orders": 0, "items": 0, "windows": 0}

    today = kst_now()
    date_from = today - timedelta(days=days)
    pending: dict[tuple[str, str], CoupangRgOrderItem] = {}
    try:
        for win_from, win_to in _windows(date_from, today):
            stats["windows"] += 1
            for order in client.iter_rg_orders(paid_date_from=win_from, paid_date_to=win_to):
                stats["orders"] += 1
                for item in _merge_order_items(order):
                    if _upsert_order_item(db, account_key, cfg.vendor_id, order, item, pending):
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
    """2개 셀러계정(WING1·WING2) RG 주문 동기화.

    계정 격리: 한 계정의 예기치 못한 실패가 다른 계정 수집을 죽이지 않는다
    (2026-07-11~17 WING1 UNIQUE 위반이 WING2까지 6일 전멸시킨 실사고).
    error dict는 _coupang_failed가 잡아 잡 레벨 실패로 표면화된다(거짓 성공 없음).
    """
    results = []
    for key in RG_ACCOUNTS:
        try:
            results.append(sync_account_rg_orders(db, key, days=days))
        except Exception as e:
            db.rollback()
            log.exception("RG 주문 동기화 실패 %s: %s", key, e)
            results.append({"account": key, "error": str(e)})
    return results
