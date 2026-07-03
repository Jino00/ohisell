# test_intelligence_characterization.py — S3 트랙 T2(codex #13): 재사용 밑줄함수 의미 고정
# product_pnl.py(S3)가 intelligence.py의 _agg_orders·_agg_rg_orders·_agg_ads·_cost_master·
# _agg_fees를 그대로 import해 재사용한다(D1). 이 파일은 "지금 이 함수들이 뭘 하는지"를
# 회귀테스트로 못박아, intelligence.py 내부 구현이 바뀔 때 product_pnl.py가 조용히 깨지는
# 것을 방지한다(시그니처뿐 아니라 반환 grain·필터 의미까지).
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    Channel, CoupangAdOptionDaily, CoupangProductItem, CoupangRevenueFee,
    CoupangRgOrderItem, Order, ProductChannelMapping, ProductMaster,
)
from app.services.coupang.intelligence import (
    _agg_ads, _agg_fees, _agg_orders, _agg_rg_orders, _cost_master,
)

_Z = Decimal(0)
WIN = (date(2026, 6, 1), date(2026, 6, 30))
OD = datetime(2026, 6, 5, 12, 0, 0)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _ch(db, cid, code, platform="coupang", company="ofix"):
    db.add(Channel(id=cid, name=f"ch_{code}", code=code, platform=platform, company=company))


# ─── _agg_orders: revenue=Σselling_price(이미 라인총액, qty 재곱 금지), REVENUE_EXCLUDED 제외 ───
def test_agg_orders_revenue_is_sum_of_selling_price_no_qty_multiply(db):
    """selling_price는 이미 라인총액(orderPrice) — ×quantity 하면 안 됨(S2 이중계상 회귀 가드)."""
    _ch(db, 1, "COUPANG_WING1")
    db.add(Order(channel_id=1, order_number="O1", platform_product_id="V1",
                 quantity=2, selling_price=Decimal("33800"), order_date=OD, status="delivered"))
    db.commit()
    out = _agg_orders(db, *WIN)
    assert out["V1"]["revenue"] == Decimal("33800")  # 33800이지 33800*2가 아님
    assert out["V1"]["qty"] == 2
    assert out["V1"]["unit_price"] == Decimal("16900")  # 33800/2


def test_agg_orders_excludes_revenue_excluded_statuses(db):
    """취소/반품/입금전 상태는 매출 집계에서 제외(profit_calculator와 동일 기준)."""
    _ch(db, 1, "COUPANG_WING1")
    db.add(Order(channel_id=1, order_number="O1", platform_product_id="V1",
                 quantity=1, selling_price=Decimal("10000"), order_date=OD, status="cancelled"))
    db.commit()
    out = _agg_orders(db, *WIN)
    assert "V1" not in out


def test_agg_orders_channel_ids_filters_isolate(db):
    """channel_ids 주면 해당 채널만, None이면 전체(계정 분리 등가성)."""
    _ch(db, 1, "COUPANG_WING1")
    _ch(db, 2, "COUPANG_WING2")
    db.add(Order(channel_id=1, order_number="O1", platform_product_id="V1",
                 quantity=1, selling_price=Decimal("1000"), order_date=OD, status="delivered"))
    db.add(Order(channel_id=2, order_number="O2", platform_product_id="V2",
                 quantity=1, selling_price=Decimal("2000"), order_date=OD, status="delivered"))
    db.commit()
    scoped = _agg_orders(db, *WIN, channel_ids=[1])
    assert set(scoped.keys()) == {"V1"}
    all_ = _agg_orders(db, *WIN, channel_ids=None)
    assert set(all_.keys()) == {"V1", "V2"}


# ─── _agg_rg_orders: revenue=Σ(unit_sales_price×sales_quantity), paid_at 기준, account_key 필터 ───
def test_agg_rg_orders_revenue_is_unit_price_times_qty(db):
    db.add(CoupangRgOrderItem(
        order_id="RG1", vendor_item_id="V1", account_key="COUPANG_WING1", vendor_id="A01564720",
        sales_quantity=3, unit_sales_price=Decimal("5000"), paid_at=OD))
    db.commit()
    out = _agg_rg_orders(db, *WIN)
    assert out["V1"]["revenue"] == Decimal("15000")  # 5000*3
    assert out["V1"]["qty"] == 3


def test_agg_rg_orders_account_key_filter(db):
    db.add(CoupangRgOrderItem(
        order_id="RG1", vendor_item_id="V1", account_key="COUPANG_WING1", vendor_id="A01564720",
        sales_quantity=1, unit_sales_price=Decimal("1000"), paid_at=OD))
    db.add(CoupangRgOrderItem(
        order_id="RG2", vendor_item_id="V2", account_key="COUPANG_WING2", vendor_id="A01029796",
        sales_quantity=1, unit_sales_price=Decimal("2000"), paid_at=OD))
    db.commit()
    scoped = _agg_rg_orders(db, *WIN, account_key="COUPANG_WING1")
    assert set(scoped.keys()) == {"V1"}


# ─── _agg_ads: 비용/노출/클릭은 ad_option_id 귀속, 매출/주문은 conv_option_id 귀속(분리) ───
def test_agg_ads_cost_and_conversion_attributed_separately(db):
    """같은 옵션이 ad_option_id에도 conv_option_id에도 있으면 한 행에 합쳐지지만,
    ad_option_id만 있는 행은 spend만 채워지고 conv_revenue는 0으로 남는다(간접전환 분리, D-9)."""
    db.add(CoupangAdOptionDaily(
        report_date=date(2026, 6, 5), vendor_id="A01564720", sell_type="3P",
        ad_option_id="V1", conv_option_id="V2",  # 광고는 V1에 걸었는데 전환은 V2에서(간접전환)
        ad_spend=Decimal("1000"), impressions=100, clicks=5,
        conversion_revenue=Decimal("9999"), orders=1, sales_qty=1))
    db.commit()
    out = _agg_ads(db, *WIN)
    assert out["V1"]["spend"] == Decimal("1000")
    assert out["V1"]["conv_revenue"] == _Z  # V1은 비용만, 전환은 안 걸림
    assert out["V2"]["conv_revenue"] == Decimal("9999")  # V2는 전환만
    assert out["V2"]["spend"] == _Z


def test_agg_ads_vendor_id_filter(db):
    db.add(CoupangAdOptionDaily(
        report_date=date(2026, 6, 5), vendor_id="A01564720", sell_type="3P",
        ad_option_id="V1", conv_option_id="V1", ad_spend=Decimal("100")))
    db.add(CoupangAdOptionDaily(
        report_date=date(2026, 6, 5), vendor_id="A01029796", sell_type="3P",
        ad_option_id="V2", conv_option_id="V2", ad_spend=Decimal("200")))
    db.commit()
    scoped = _agg_ads(db, *WIN, vendor_id="A01564720")
    assert set(scoped.keys()) == {"V1"}


# ─── _cost_master: vendor_item_id → product_master 다리, coupang+is_active 매핑 경유 ───
def test_cost_master_joins_via_active_coupang_mapping(db):
    """★발견(T2): _cost_master는 internal_sku를 노출하지 않는다 — cost_price·name만 반환.
    product_pnl.py(T3)가 internal_sku로 그룹핑하려면 이 함수를 재사용해도 internal_sku 매핑은
    product_channel_mapping을 별도로 조회해야 한다(계획 SA-1에 이미 반영됨, 이 함수에 의존 금지)."""
    _ch(db, 1, "COUPANG_WING1")
    pm = ProductMaster(internal_sku="OHI-0001", product_name="테스트옵션", cost_price=Decimal("3000"))
    db.add(pm)
    db.flush()
    db.add(ProductChannelMapping(product_id=pm.id, channel_id=1, channel_product_id="V1",
                                 selling_price=Decimal("10000"), is_active=True))
    db.commit()
    out = _cost_master(db)
    assert out["V1"]["cost_price"] == Decimal("3000")
    assert set(out["V1"].keys()) == {"cost_price", "name"}  # internal_sku 없음(회귀 가드)


def test_cost_master_excludes_inactive_mapping(db):
    """is_active=False 매핑은 원가 다리에서 제외(회귀 가드)."""
    _ch(db, 1, "COUPANG_WING1")
    pm = ProductMaster(internal_sku="OHI-0001", product_name="테스트", cost_price=Decimal("1000"))
    db.add(pm)
    db.flush()
    db.add(ProductChannelMapping(product_id=pm.id, channel_id=1, channel_product_id="V1",
                                 selling_price=Decimal("1000"), is_active=False))
    db.commit()
    out = _cost_master(db)
    assert "V1" not in out


def test_cost_master_excludes_non_coupang_channel(db):
    """cafe24/네이버 채널 매핑은 _cost_master(쿠팡 전용 다리)에서 제외 — product_pnl.py가
    네이버/cafe24 원가는 다른 경로(ProductMaster.internal_sku 직접)로 가져와야 함을 증명."""
    _ch(db, 1, "CAFE24", platform="cafe24")
    pm = ProductMaster(internal_sku="OHI-0002", product_name="cafe24옵션", cost_price=Decimal("500"))
    db.add(pm)
    db.flush()
    db.add(ProductChannelMapping(product_id=pm.id, channel_id=1, channel_product_id="C1",
                                 selling_price=Decimal("1000"), is_active=True))
    db.commit()
    out = _cost_master(db)
    assert "C1" not in out


# ─── _agg_fees: total_fee=service_fee+service_fee_vat, recognition_date 기준, account_key 필터 ───
def test_agg_fees_total_is_fee_plus_vat(db):
    db.add(CoupangRevenueFee(
        order_id="O1", vendor_item_id="V1", recognition_date=date(2026, 6, 6),
        sale_type="SALE", account_key="COUPANG_WING1", vendor_id="A01564720",
        service_fee=Decimal("780"), service_fee_vat=Decimal("78")))
    db.commit()
    out = _agg_fees(db, *WIN)
    assert out["V1"]["total_fee"] == Decimal("858")  # 780+78


def test_agg_fees_account_key_filter(db):
    db.add(CoupangRevenueFee(
        order_id="O1", vendor_item_id="V1", recognition_date=date(2026, 6, 6),
        sale_type="SALE", account_key="COUPANG_WING1", vendor_id="A01564720",
        service_fee=Decimal("100"), service_fee_vat=Decimal("10")))
    db.add(CoupangRevenueFee(
        order_id="O2", vendor_item_id="V2", recognition_date=date(2026, 6, 6),
        sale_type="SALE", account_key="COUPANG_WING2", vendor_id="A01029796",
        service_fee=Decimal("200"), service_fee_vat=Decimal("20")))
    db.commit()
    scoped = _agg_fees(db, *WIN, account_key="COUPANG_WING1")
    assert set(scoped.keys()) == {"V1"}
