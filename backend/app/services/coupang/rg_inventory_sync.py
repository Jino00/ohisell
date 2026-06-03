# rg_inventory_sync.py — 로켓창고 재고 동기화 Harness (재고관리 + 결합축, P3/D-14)
# 흐름: 계정별 → 재고 summaries(SA, nextToken 순회) → coupang_rg_inventory upsert(vendor_item_id 기준).
# 트랙 D-8: vendor 2계정(WING1·WING2) 순회. rg_open_api 분당 50회 — _base가 429 재시도.
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.clients.coupang import CoupangRocketGrowthClient
from app.clients.coupang._base import CoupangReadError
from app.config import get_coupang_config
from app.models import CoupangRgInventory

log = logging.getLogger(__name__)

RG_ACCOUNTS = ["COUPANG_WING1", "COUPANG_WING2"]


def _to_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _upsert_inventory(db: Session, account_key: str, vendor_id: str, row_data: dict) -> bool:
    """재고 1행을 coupang_rg_inventory에 upsert (vendor_item_id 기준)."""
    vii = row_data.get("vendorItemId")
    if vii is None:
        return False
    vii = str(vii)
    row = (
        db.query(CoupangRgInventory)
        .filter(CoupangRgInventory.vendor_item_id == vii)
        .first()
    )
    if row is None:
        row = CoupangRgInventory(vendor_item_id=vii)
        db.add(row)
    row.account_key = account_key
    row.vendor_id = vendor_id
    ext = row_data.get("externalSkuId")
    row.external_sku_id = str(ext) if ext is not None else None
    row.orderable_qty = _to_int((row_data.get("inventoryDetails") or {}).get("totalOrderableQuantity"))
    row.sold_30d = _to_int((row_data.get("salesCountMap") or {}).get("SALES_COUNT_LAST_THIRTY_DAYS"))
    return True


def sync_account_rg_inventory(db: Session, account_key: str) -> dict:
    """한 계정의 로켓창고 재고 전체 동기화. 반환: 통계 dict."""
    cfg = get_coupang_config(account_key)
    if cfg is None:
        return {"account": account_key, "error": "config_missing"}
    client = CoupangRocketGrowthClient(cfg)
    stats = {"account": account_key, "vendor_id": cfg.vendor_id, "rows": 0, "upserted": 0}

    try:
        for row_data in client.iter_inventory_summaries():
            stats["rows"] += 1
            if _upsert_inventory(db, account_key, cfg.vendor_id, row_data):
                stats["upserted"] += 1
    except CoupangReadError as e:
        db.commit()
        stats["read_error"] = str(e)
        log.error("RG 재고 동기화 읽기 실패 %s: %s", account_key, e)
        return stats

    db.commit()
    log.info("RG 재고 동기화 완료 %s: %s", account_key, stats)
    return stats


def sync_all_rg_inventory(db: Session) -> list[dict]:
    """2개 셀러계정(WING1·WING2) 로켓창고 재고 전체 동기화."""
    return [sync_account_rg_inventory(db, key) for key in RG_ACCOUNTS]
