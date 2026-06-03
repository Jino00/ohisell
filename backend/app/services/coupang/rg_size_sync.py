# rg_size_sync.py — 로켓그로스 사이즈 동기화 Harness (보관비 CBM 토대, P3/D-14)
# 흐름: 계정별 → RG상품목록(SA) → 단건조회(SA) → items[].rocketGrowthItemData.skuInfo →
#       coupang_product_item에 width/length/height_mm·weight/net_weight_g·cbm upsert.
# 트랙 D-8: vendor 2계정(WING1·WING2) 순회. 호출은 서버 IP에서만. CBM은 보관비 단가 곱셈 기준.
from __future__ import annotations

import logging
import time
from decimal import Decimal

from sqlalchemy.orm import Session

from app.clients.coupang import CoupangRocketGrowthClient
from app.clients.coupang._base import CoupangReadError
from app.config import get_coupang_config
from app.models import CoupangProductItem

log = logging.getLogger(__name__)

RG_ACCOUNTS = ["COUPANG_WING1", "COUPANG_WING2"]
_DETAIL_DELAY = 0.3  # 쿠팡 속도제한 대응 (단건조회 사이 간격)
_CBM_DIVISOR = Decimal(1_000_000_000)  # mm³ → m³ (1m = 1000mm, 1m³ = 1e9 mm³)


def _to_int(value) -> int | None:
    """skuInfo 치수(int 기대). None·비숫자는 None."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _cbm(width_mm: int | None, length_mm: int | None, height_mm: int | None) -> Decimal | None:
    """CBM(m³) = width×length×height(mm) / 1e9. 한 변이라도 없거나 0이면 None(부피 산출 불가)."""
    if not width_mm or not length_mm or not height_mm:
        return None
    return (Decimal(width_mm) * Decimal(length_mm) * Decimal(height_mm) / _CBM_DIVISOR).quantize(
        Decimal("0.000001")
    )


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError):
        return None


def _upsert_size(
    db: Session, account_key: str, vendor_id: str, detail: dict, item: dict, rg: dict
) -> bool:
    """RG 옵션 1건의 사이즈를 coupang_product_item에 upsert (vendor_item_id 기준).

    기존 행(WING product_sync 적재)이면 사이즈만 갱신, RG 전용이라 행이 없으면 최소 행 생성
    (identity + 사이즈 + RG 판매가). WING이 소유하는 가격/수수료는 기존 행이면 건드리지 않는다.
    """
    vii = rg.get("vendorItemId")
    if not vii:  # 임시저장 등 옵션ID 없으면 스킵
        return False
    vii = str(vii)
    sku = rg.get("skuInfo") or {}
    w = _to_int(sku.get("width"))
    l = _to_int(sku.get("length"))
    h = _to_int(sku.get("height"))

    row = (
        db.query(CoupangProductItem)
        .filter(CoupangProductItem.vendor_item_id == vii)
        .first()
    )
    if row is None:
        # RG 전용 옵션(WING 목록에 없던 것) → 최소 행 생성
        row = CoupangProductItem(vendor_item_id=vii)
        row.account_key = account_key
        row.vendor_id = vendor_id
        row.seller_product_id = str(detail.get("sellerProductId", ""))
        row.seller_product_name = (detail.get("sellerProductName") or "")[:300]
        row.item_name = (item.get("itemName") or "")[:300]
        row.external_vendor_sku = rg.get("externalVendorSku")
        price = (rg.get("priceData") or {}).get("salePrice")
        row.sale_price = _dec(price) or Decimal(0)
        row.status_name = detail.get("statusName")
        row.brand = detail.get("brand")
        db.add(row)

    # 사이즈는 항상 갱신(RG 상품조회가 사이즈의 권위 원천)
    row.width_mm = w
    row.length_mm = l
    row.height_mm = h
    row.weight_g = _to_int(sku.get("weight"))
    row.net_weight_g = _to_int(sku.get("netWeight"))
    row.cbm = _cbm(w, l, h)
    return True


def sync_account_rg_sizes(
    db: Session, account_key: str, *, max_products: int | None = None
) -> dict:
    """한 계정의 RG 상품 사이즈 전체 동기화. 반환: 통계 dict."""
    cfg = get_coupang_config(account_key)
    if cfg is None:
        return {"account": account_key, "error": "config_missing"}
    client = CoupangRocketGrowthClient(cfg)
    stats = {"account": account_key, "vendor_id": cfg.vendor_id,
             "rg_products": 0, "options": 0, "sized": 0, "cbm_filled": 0, "errors": 0}

    try:
        for product in client.iter_rg_products():
            spid = product.get("sellerProductId")
            if spid is None:
                continue
            detail = client.get_rg_product(spid)
            if not detail:
                stats["errors"] += 1
                time.sleep(_DETAIL_DELAY)
                continue
            for item in detail.get("items", []) or []:
                rg = item.get("rocketGrowthItemData")
                if not rg:  # 마켓플레이스 전용 옵션(RG 아님) 스킵
                    continue
                stats["options"] += 1
                if _upsert_size(db, account_key, cfg.vendor_id, detail, item, rg):
                    stats["sized"] += 1
                    if _cbm(_to_int((rg.get("skuInfo") or {}).get("width")),
                            _to_int((rg.get("skuInfo") or {}).get("length")),
                            _to_int((rg.get("skuInfo") or {}).get("height"))) is not None:
                        stats["cbm_filled"] += 1
            stats["rg_products"] += 1
            time.sleep(_DETAIL_DELAY)
            if max_products and stats["rg_products"] >= max_products:
                break
    except CoupangReadError as e:
        # 하드 실패는 표면화(stale 위장 방지, 원칙22). 부분 적재분은 커밋 후 에러 보고.
        db.commit()
        stats["read_error"] = str(e)
        log.error("RG 사이즈 동기화 읽기 실패 %s: %s", account_key, e)
        return stats

    db.commit()
    log.info("RG 사이즈 동기화 완료 %s: %s", account_key, stats)
    return stats


def sync_all_rg_sizes(db: Session) -> list[dict]:
    """2개 셀러계정(WING1·WING2) RG 상품 사이즈 전체 동기화."""
    return [sync_account_rg_sizes(db, key) for key in RG_ACCOUNTS]
