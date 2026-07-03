# product_sync.py — 상품 동기화 Harness (조망 결합축 적재)
# 흐름: 계정별 → 목록조회(SA) → 단건조회(SA, 옵션ID·원가) → [옵션재고조회(SA)] →
#       coupang_product_item upsert + ProductChannelMapping 자동 upsert(주문 자동연결 복구).
# 트랙 D-8: vendor 2계정(WING1·WING2)이면 2개 셀러계정 전체 커버(RG 중복 불필요). 호출은 서버 IP에서만.
from __future__ import annotations

import logging
import time
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.clients.coupang import CoupangProductClient
from app.config import get_coupang_config
from app.models import Channel, CoupangProductItem, ProductChannelMapping, ProductMaster

log = logging.getLogger(__name__)

# vendor_id 2계정을 커버하는 크레덴셜 (D-8: WING1=A01564720, WING2=A01029796)
PRODUCT_ACCOUNTS = ["COUPANG_WING1", "COUPANG_WING2"]
_DETAIL_DELAY = 0.3  # 쿠팡 속도제한 대응 (단건조회 사이 간격)


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _upsert_item(
    db: Session, account_key: str, vendor_id: str, detail: dict, item: dict, inv: dict | None
) -> None:
    """옵션 1건을 coupang_product_item에 upsert (vendor_item_id 기준).

    vendorItemId는 쿠팡 전역 유일·단일계정 소유(라이브 확인: 타계정 조회 시 400).
    그래서 vendor_item_id 단독을 키로 쓴다(Order.platform_product_id 조인과 일치).
    만약 기존 행의 account_key가 바뀌면 = 전역유일 가정 위반 신호 → 경고(침묵 손상 방지).
    """
    vii = str(item.get("vendorItemId"))
    row = (
        db.query(CoupangProductItem)
        .filter(CoupangProductItem.vendor_item_id == vii)
        .first()
    )
    if row is None:
        row = CoupangProductItem(vendor_item_id=vii)
        db.add(row)
    elif row.account_key and row.account_key != account_key:
        log.warning(
            "vendorItemId %s account 변경 감지 %s→%s (전역유일 가정 위반 가능)",
            vii, row.account_key, account_key,
        )

    row.account_key = account_key
    row.vendor_id = vendor_id
    row.seller_product_id = str(detail.get("sellerProductId", ""))
    row.seller_product_name = (detail.get("sellerProductName") or "")[:300]
    row.item_name = (item.get("itemName") or "")[:300]
    row.external_vendor_sku = item.get("externalVendorSku")
    row.sale_price = _dec(item.get("salePrice")) or Decimal(0)
    row.supply_price = _dec(item.get("supplyPrice"))
    row.sale_agent_commission = _dec(item.get("saleAgentCommission"))
    row.max_buy_count = item.get("maximumBuyCount")
    row.status_name = detail.get("statusName")
    row.brand = detail.get("brand")
    row.category_id = detail.get("categoryId")
    # 실시간 재고/판매상태는 inventories SA에서만 (단건조회 items엔 없음)
    if inv is not None:
        row.amount_in_stock = inv.get("amountInStock")
        row.on_sale = inv.get("onSale")
        live_price = _dec(inv.get("salePrice"))
        if live_price is not None:
            row.sale_price = live_price


def _maybe_upsert_mapping(db: Session, channel: Channel, detail: dict, item: dict) -> bool:
    """externalVendorSku가 product_master.internal_sku와 일치하면 채널매핑 upsert.

    주문 자동연결(_auto_link_product)을 복구한다. 매칭 없으면 건너뜀(고아 master 생성 안 함).
    """
    sku = item.get("externalVendorSku")
    if not sku:
        return False
    master = (
        db.query(ProductMaster).filter(ProductMaster.internal_sku == sku).first()
    )
    if master is None:
        return False
    vii = str(item.get("vendorItemId"))
    mapping = (
        db.query(ProductChannelMapping)
        .filter(
            ProductChannelMapping.channel_id == channel.id,
            ProductChannelMapping.channel_product_id == vii,
        )
        .first()
    )
    if mapping is None:
        mapping = ProductChannelMapping(
            product_id=master.id, channel_id=channel.id, channel_product_id=vii
        )
        db.add(mapping)
    elif mapping.mapping_source == "excel_master":
        # 상품 연관맵 트랙 D-6: 엑셀 마스터가 진실원천인 매핑은 SKU 매칭 자동동기화가
        # 덮어쓰지 않는다(스케줄러 주기마다 조용히 재배정되는 회귀 방지).
        return False
    mapping.product_id = master.id  # [P2] 기존 매핑도 현재 매칭 master로 재지정(stale 방지)
    mapping.channel_product_name = (item.get("itemName") or "")[:200]
    mapping.channel_sku = sku
    mapping.selling_price = _dec(item.get("salePrice")) or Decimal(0)
    mapping.is_active = True
    return True


def _channels_for_vendor(db: Session, vendor_id: str) -> list[Channel]:
    """같은 vendor_id를 쓰는 모든 쿠팡 채널(WING+RG). [P1#2]

    WING1≡RG1(같은 vendor) → 한 vendor의 상품 매핑을 WING·RG 채널 양쪽에 만들어야
    RG 주문(별도 channel_id 저장)도 _auto_link_product로 자동연결된다.
    """
    out: list[Channel] = []
    for ch in db.query(Channel).filter(Channel.api_type == "hmac").all():
        if not ch.api_config_key:
            continue
        c = get_coupang_config(ch.api_config_key)
        if c and c.vendor_id == vendor_id:
            out.append(ch)
    return out


def sync_account_products(
    db: Session,
    account_key: str,
    *,
    refresh_inventory: bool = False,
    max_products: int | None = None,
) -> dict:
    """한 계정의 상품 전체를 순회·적재. 반환: 통계 dict."""
    cfg = get_coupang_config(account_key)
    if cfg is None:
        return {"account": account_key, "error": "config_missing"}
    client = CoupangProductClient(cfg)
    channels = _channels_for_vendor(db, cfg.vendor_id)  # WING+RG 모두 [P1#2]
    stats = {"account": account_key, "vendor_id": cfg.vendor_id,
             "products": 0, "items": 0, "mappings": 0, "errors": 0,
             "channels": [c.api_config_key for c in channels]}

    for product in client.iter_products():
        spid = product.get("sellerProductId")
        if spid is None:
            continue
        detail = client.get_product(spid)
        if not detail:
            stats["errors"] += 1
            time.sleep(_DETAIL_DELAY)
            continue
        for item in detail.get("items", []) or []:
            if not item.get("vendorItemId"):  # 임시저장 옵션은 null → 스킵
                continue
            inv = None
            if refresh_inventory:
                inv = client.get_item_inventory(item["vendorItemId"])
            _upsert_item(db, account_key, cfg.vendor_id, detail, item, inv)
            stats["items"] += 1
            for ch in channels:  # WING+RG 모든 채널에 매핑 [P1#2]
                if _maybe_upsert_mapping(db, ch, detail, item):
                    stats["mappings"] += 1
        stats["products"] += 1
        time.sleep(_DETAIL_DELAY)
        if max_products and stats["products"] >= max_products:
            break

    db.commit()
    log.info("쿠팡 상품 동기화 완료 %s: %s", account_key, stats)
    return stats


def sync_all_products(db: Session, *, refresh_inventory: bool = False) -> list[dict]:
    """2개 셀러계정(WING1·WING2) 상품 전체 동기화."""
    return [
        sync_account_products(db, key, refresh_inventory=refresh_inventory)
        for key in PRODUCT_ACCOUNTS
    ]
