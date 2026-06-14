# rg_product_size_sync.py — SA: 쿠팡 실측 사이즈 XLSX 파서 + DB ingest
# 단일 책임: PRODUCT_SIZE_COMPARISON XLSX bytes → coupang_product_size 테이블 upsert.
# 쿠팡 물류센터 실측 사이즈가 배송비 과금 기준이므로 이 데이터가 anomaly 판단 우선순위 최고.
from __future__ import annotations

import io
import logging
from typing import Optional

import openpyxl
from sqlalchemy.orm import Session

from app.models import CoupangProductSize

log = logging.getLogger(__name__)

# PRODUCT_SIZE_COMPARISON 시트 헤더 행 번호 (1-indexed)
_HEADER_ROW = 15
_DATA_START_ROW = 17


def parse_product_size_xlsx(xlsx_bytes: bytes) -> list[dict]:
    """PRODUCT_SIZE_COMPARISON XLSX → 행 목록.

    반환: [{"vendor_item_id", "seller_product_id", "sku_id",
             "product_name", "option_name", "size_type"}, ...]
    size_type은 [2025-01-06 부터] 열 값 (7번째 열, index 6).
    """
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    results = []

    for i, row in enumerate(rows, 1):
        if i < _DATA_START_ROW:
            continue
        if not row or row[0] is None:
            continue

        seller_product_id = str(row[0]).strip() if row[0] is not None else None
        vendor_item_id = str(row[1]).strip() if row[1] is not None else None
        sku_id = str(row[2]).strip() if row[2] is not None else None
        product_name = str(row[3]).strip() if row[3] is not None else None
        option_name = str(row[4]).strip() if row[4] is not None else None
        size_type_new = str(row[6]).strip() if len(row) > 6 and row[6] is not None else None

        if not vendor_item_id or not size_type_new:
            continue

        results.append({
            "vendor_item_id": vendor_item_id,
            "seller_product_id": seller_product_id,
            "sku_id": sku_id,
            "product_name": product_name,
            "option_name": option_name,
            "size_type": size_type_new,
        })

    return results


def ingest_product_size(
    db: Session,
    xlsx_bytes: bytes,
    source_group_key: Optional[str] = None,
) -> dict:
    """XLSX bytes → DB upsert. 반환: {"upserted": N, "skipped": M}."""
    rows = parse_product_size_xlsx(xlsx_bytes)
    if not rows:
        log.warning("product_size: 파싱 결과 0건")
        return {"upserted": 0, "skipped": 0}

    upserted = skipped = 0
    for r in rows:
        vid = r["vendor_item_id"]
        existing = db.query(CoupangProductSize).filter_by(vendor_item_id=vid).first()
        if existing:
            existing.size_type = r["size_type"]
            existing.seller_product_id = r["seller_product_id"]
            existing.sku_id = r["sku_id"]
            existing.product_name = r["product_name"]
            existing.option_name = r["option_name"]
            if source_group_key:
                existing.source_group_key = source_group_key
        else:
            db.add(CoupangProductSize(
                vendor_item_id=vid,
                seller_product_id=r["seller_product_id"],
                sku_id=r["sku_id"],
                product_name=r["product_name"],
                option_name=r["option_name"],
                size_type=r["size_type"],
                source_group_key=source_group_key,
            ))
        upserted += 1

    db.commit()
    log.info("product_size ingest 완료: upserted=%d", upserted)
    return {"upserted": upserted, "skipped": skipped}


def get_coupang_size_type(db: Session, vendor_item_id: str) -> Optional[str]:
    """DB에서 쿠팡 실측 사이즈 조회. 없으면 None."""
    row = db.query(CoupangProductSize.size_type).filter_by(
        vendor_item_id=str(vendor_item_id)
    ).first()
    return row[0] if row else None
