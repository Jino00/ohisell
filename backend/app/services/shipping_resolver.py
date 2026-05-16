# shipping_resolver.py — SA: 채널 → 주문 1건당 우리 부담 배송비
# 단일 책임: 채널별 출고 배송비 상수 (cafe24=한진택배 1,900원/주문, 고객 무료배송)
# 다른 SA를 모름. 순수 함수.
from __future__ import annotations

from decimal import Decimal

# 채널코드 → 주문 1건당 우리 부담 배송비 (Jino 확정)
_PER_ORDER_SHIPPING = {
    "CAFE24": Decimal("1900"),
}


def per_order_shipping(channel_code: str | None) -> Decimal:
    """채널코드 → 주문 1건당 우리 부담 배송비. 미정의 채널은 0 (기존 로직 유지)."""
    if not channel_code:
        return Decimal("0")
    return _PER_ORDER_SHIPPING.get(channel_code.strip().upper(), Decimal("0"))
