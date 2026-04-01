# models.py — SQLAlchemy 모델 (ohisell 핵심 테이블 5개)
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Channel(Base):
    """판매 채널 (쿠팡, 네이버 스마트스토어, 자사몰 등)"""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    commission_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=0
    )
    memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    orders: Mapped[list["Order"]] = relationship(back_populates="channel")
    settlements: Mapped[list["Settlement"]] = relationship(back_populates="channel")


class Product(Base):
    """상품 마스터"""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    cost_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    orders: Mapped[list["Order"]] = relationship(back_populates="product")
    inventory_records: Mapped[list["Inventory"]] = relationship(
        back_populates="product"
    )


class Order(Base):
    """주문 (채널별 매출 데이터)"""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    order_number: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    selling_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    order_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="completed"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    channel: Mapped["Channel"] = relationship(back_populates="orders")
    product: Mapped["Product"] = relationship(back_populates="orders")


class Settlement(Base):
    """정산 (채널별 정산금, 수수료)"""

    __tablename__ = "settlements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    settlement_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    commission: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    channel: Mapped["Channel"] = relationship(back_populates="settlements")


class Inventory(Base):
    """재고 입출고 이력"""

    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    change_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # "in" (입고) | "out" (출고)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    product: Mapped["Product"] = relationship(back_populates="inventory_records")
