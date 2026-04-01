# schemas.py — Pydantic 스키마 (요청/응답 모델)
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


# ── Channel ──
class ChannelOut(BaseModel):
    id: int
    name: str
    code: str
    platform: str
    channel_type: str
    account_label: Optional[str] = None
    commission_rate: Decimal
    api_type: str

    model_config = {"from_attributes": True}


# ── ProductMaster ──
class ProductCreate(BaseModel):
    internal_sku: str
    product_name: str
    cost_price: Decimal = Decimal("0")
    category: Optional[str] = None
    memo: Optional[str] = None


class ProductUpdate(BaseModel):
    internal_sku: Optional[str] = None
    product_name: Optional[str] = None
    cost_price: Optional[Decimal] = None
    category: Optional[str] = None
    memo: Optional[str] = None


class MappingCreate(BaseModel):
    channel_id: int
    channel_product_id: str
    channel_product_name: Optional[str] = None
    channel_sku: Optional[str] = None
    selling_price: Decimal = Decimal("0")


class MappingOut(BaseModel):
    id: int
    channel_id: int
    channel_name: Optional[str] = None
    channel_product_id: str
    channel_product_name: Optional[str] = None
    channel_sku: Optional[str] = None
    selling_price: Decimal
    is_active: bool

    model_config = {"from_attributes": True}


class ProductOut(BaseModel):
    id: int
    internal_sku: str
    product_name: str
    cost_price: Decimal
    category: Optional[str] = None
    memo: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    mappings: list[MappingOut] = []

    model_config = {"from_attributes": True}


# ── Order ──
class OrderOut(BaseModel):
    id: int
    channel_id: int
    channel_name: str = ""
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    order_number: str
    platform_product_id: str
    platform_product_name: Optional[str] = None
    quantity: int
    selling_price: Decimal
    shipping_cost: Optional[Decimal] = None
    order_date: datetime
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class OrderListResponse(BaseModel):
    items: list[OrderOut]
    total: int
    page: int
    page_size: int


# ── Sync ──
class SyncRequest(BaseModel):
    date_from: Optional[str] = None  # YYYY-MM-DD
    date_to: Optional[str] = None


class SyncResult(BaseModel):
    channel_id: int
    channel_name: str = ""
    status: str
    new_orders: int = 0
    updated_orders: int = 0
    errors: list[str] = []


class SyncStatusOut(BaseModel):
    channel_id: int
    channel_name: str
    last_sync: Optional[datetime] = None
    status: Optional[str] = None
    records_synced: int = 0


# ── Ad Cost ──
class AdSpendDaily(BaseModel):
    date: str
    total_spend: int
    total_revenue_14d: Optional[int] = None


class AdSpendByOption(BaseModel):
    option_id: str
    product_name: Optional[str] = None
    total_spend: int
    total_revenue_14d: Optional[int] = None
    impressions: int = 0
    clicks: int = 0


# ── Profit Summary ──
class ProfitSummary(BaseModel):
    total_revenue: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")
    total_commission: Decimal = Decimal("0")
    total_ad_spend: Decimal = Decimal("0")
    total_shipping: Decimal = Decimal("0")
    total_vat: Decimal = Decimal("0")
    net_profit: Decimal = Decimal("0")
    order_count: int = 0
