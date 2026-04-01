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
