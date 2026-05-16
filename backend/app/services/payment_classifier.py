# payment_classifier.py — SA: cafe24 주문 → 결제유형(payment_type) 분류
# 단일 책임: cafe24 주문 dict의 market_id/payment_method/gateway 조합 → payment_type 코드
# 다른 SA를 모름. 순수 함수. (수수료 계산은 CommissionResolver 책임)
from __future__ import annotations

# cafe24 payment_method 중 실제 PG를 타는 결제수단 (point/coupon/mileage 등은 보조)
_PG_METHODS = ("card", "tcash", "cash", "icash", "cell", "prepaid", "cod")


def _primary_method(payment_method) -> str:
    """payment_method (배열 또는 문자열)에서 PG를 타는 주 결제수단 추출."""
    if isinstance(payment_method, list):
        items = [str(m).strip().lower() for m in payment_method]
    elif payment_method:
        items = [str(payment_method).strip().lower()]
    else:
        items = []
    for m in items:
        if m in _PG_METHODS:
            return m
    return "etc"


def classify(order: dict) -> str:
    """cafe24 'order' dict → payment_type 코드.

    판별 우선순위 (242건 실측 검증):
      1) market_id == NCHECKOUT → 네이버페이 (결제수단별)
      2) gateway 카카오 → kakaopay
      3) gateway 토스   → tosspay
      4) 그 외(kcp 등)  → KCP (결제수단별)
    """
    market_id = str(order.get("market_id", "") or "").strip().upper()
    gateway = str(order.get("payment_gateway_name", "") or "").strip().lower()
    pm = _primary_method(order.get("payment_method"))

    if market_id == "NCHECKOUT":
        return {
            "card": "naverpay_card",
            "tcash": "naverpay_transfer",
            "cash": "naverpay_bank",
            "icash": "naverpay_bank",
            "cell": "naverpay_cell",
            "prepaid": "naverpay_aux",
            "cod": "naverpay_aux",
        }.get(pm, "naverpay_aux")

    if "kakao" in gateway:
        return "kakaopay"
    if "toss" in gateway:
        return "tosspay"

    # KCP 및 기타 PG (gateway 비어있거나 kcp)
    return {
        "card": "kcp_card",
        "tcash": "kcp_transfer",
        "cash": "kcp_bank",
        "icash": "kcp_bank",
        "cell": "kcp_card",  # KCP 휴대폰은 별도요율 미제공 → 카드요율 보수 적용
        "prepaid": "kcp_card",
        "cod": "kcp_card",
    }.get(pm, "etc")
