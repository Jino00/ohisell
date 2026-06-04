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
    # 채널 원자 주문라인 식별자 (네이버=productOrderId). 기본 ""(쿠팡/cafe24는 미사용 → 저장 그레인 불변)
    platform_order_line_id: str = ""
    # cafe24 전용 (다른 클라이언트는 None → sync가 그대로 저장, profit_calc는 cafe24만 사용)
    payment_type: str | None = None
    commission_amount: Decimal | None = None


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
