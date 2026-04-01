# base.py — 채널 API 클라이언트 추상 인터페이스
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass
class RawOrder:
    """플랫폼에서 가져온 원본 주문 데이터 (정규화 전)"""
    order_number: str
    platform_product_id: str
    platform_product_name: str
    quantity: int
    selling_price: Decimal
    shipping_cost: Decimal | None  # None이면 배송비 포함 상품
    order_date: str  # ISO format
    status: str
    raw_data: dict


class BaseChannelClient(ABC):
    """채널 API 클라이언트 인터페이스"""

    @abstractmethod
    def test_connection(self) -> dict:
        """API 연결 테스트 → {status: ok|error, message: str}"""
        ...

    @abstractmethod
    def fetch_orders(self, date_from: date, date_to: date) -> list[RawOrder]:
        """주문 데이터 수집"""
        ...
