# test_naver_line_revenue.py — 네이버 매출·수수료·배송비 정산 정합 계약 (2026-08-04)
#
# ★존재 이유(라이브 전수 대조): 성숙 정산창 2개(n=1,057 / n=1,448)에서 원장과 라인 단위로
#   맞춰보니 종전 공식 `totalPaymentAmount`는 **99.24%**만 일치했다. 틀리는 방식이 둘이었다:
#     ①부분취소: 2개 중 1개가 취소돼도 totalPaymentAmount는 원 주문 금액 그대로 → 매출 과대
#     ②플랫폼 부담 할인: totalPaymentAmount는 '고객이 낸 돈'이라, 네이버가 부담해 우리에게
#       지급하는 차액만큼 매출 과소(실측 4,900원짜리 다수)
#   `remainProductAmount − remainSellerBurdenDiscountAmount`로 바꾸니 **2,505건 100.00%** 일치.
#   이 파일이 그 공식을 고정한다.
from __future__ import annotations

from decimal import Decimal

from app.clients.naver import naver_line_revenue
from app.services.order_delivery import (
    METHOD_NBAESONG,
    METHOD_NORMAL,
    SECTION_SURCHARGE_PAID,
    SHIPPING_COST_NBAESONG,
    SHIPPING_COST_NORMAL,
    section_delivery_fee_of,
    shipping_cost_paid_of,
)


# ── 매출 공식 ────────────────────────────────────────────────────────────
def test_revenue_uses_remain_not_total_on_partial_cancel():
    """라이브 실측 2026071256771511: 2개 중 1개 취소 → 정산 15,900 (totalPaymentAmount는 31,800)."""
    po = {
        "totalPaymentAmount": 31800,
        "totalProductAmount": 35800,
        "remainProductAmount": 17900,
        "remainSellerBurdenDiscountAmount": 2000,
        "quantity": 2,
        "remainQuantity": 1,
    }
    assert naver_line_revenue(po) == Decimal("15900")


def test_revenue_keeps_platform_borne_discount():
    """라이브 실측 2026071417408391: 고객은 11,000 냈지만 정산은 15,900.

    차액 4,900은 **네이버가 부담**해 우리에게 지급한다 — 우리가 못 받는 것은 판매자 부담
    3,900뿐이다. 고객 결제액을 매출로 쓰면 그만큼 과소계상된다.
    """
    po = {
        "totalPaymentAmount": 11000,
        "totalProductAmount": 19800,
        "remainProductAmount": 19800,
        "remainSellerBurdenDiscountAmount": 3900,
        "sellerBurdenDiscountAmount": 3900,
    }
    assert naver_line_revenue(po) == Decimal("15900")


def test_revenue_plain_order():
    po = {
        "totalPaymentAmount": 15900,
        "totalProductAmount": 15900,
        "remainProductAmount": 15900,
        "remainSellerBurdenDiscountAmount": 0,
    }
    assert naver_line_revenue(po) == Decimal("15900")


def test_revenue_falls_back_when_remain_missing():
    """remain 계열이 없는 응답은 종전 값으로 폴백 — 0으로 떨구면 매출이 통째로 사라진다."""
    assert naver_line_revenue({"totalPaymentAmount": 12345}) == Decimal("12345")
    assert naver_line_revenue({}) == Decimal("0")


# ── 제주·도서산간 ────────────────────────────────────────────────────────
def test_section_fee_read_from_field_not_constant():
    """금액은 스마트스토어가 준 값을 그대로 쓴다(Jino 지시) — 상수로 박으면 설정 변경에 못 따라간다."""
    assert section_delivery_fee_of({"sectionDeliveryFee": 3000}) == Decimal("3000")
    assert section_delivery_fee_of({"sectionDeliveryFee": 4000}) == Decimal("4000")
    assert section_delivery_fee_of({}) == Decimal("0")
    assert section_delivery_fee_of(None) == Decimal("0")


def test_section_surcharge_applied_to_cost_too():
    """★수입만 넣고 비용을 빼면 제주 배송이 이익 나는 일로 보인다(반품 배송비와 같은 함정)."""
    assert shipping_cost_paid_of(METHOD_NORMAL) == SHIPPING_COST_NORMAL
    assert (
        shipping_cost_paid_of(METHOD_NORMAL, section=True)
        == SHIPPING_COST_NORMAL + SECTION_SURCHARGE_PAID
    )
    assert (
        shipping_cost_paid_of(METHOD_NBAESONG, section=True)
        == SHIPPING_COST_NBAESONG + SECTION_SURCHARGE_PAID
    )


def test_section_surcharge_is_jino_confirmed_3000():
    """Jino 확정(2026-08-04): '한진택배에는 기본값에 3000원 추가'."""
    assert SECTION_SURCHARGE_PAID == Decimal("3000")


# ── 회귀: 제주 건의 수입과 비용이 짝으로 움직인다 ─────────────────────────
def test_jeju_income_and_cost_move_together():
    """라이브 실측 제주 건: deliveryFeeAmount=0·sectionDeliveryFee=3000 → 정산 DELIVERY 3,000.

    종전엔 deliveryFeeAmount만 읽어 수입이 통째로 0이었다. 이제 수입 3,000이 잡히므로
    비용도 같은 건에서 +3,000이 되어야 이익이 부풀지 않는다.
    """
    po = {"deliveryFeeAmount": 0, "sectionDeliveryFee": 3000, "deliveryAttributeType": "TODAY"}
    income = Decimal(str(po["deliveryFeeAmount"])) + section_delivery_fee_of(po)
    paid = shipping_cost_paid_of(METHOD_NORMAL, section=section_delivery_fee_of(po) > 0)
    assert income == Decimal("3000")
    assert paid - SHIPPING_COST_NORMAL == Decimal("3000")
    # 순효과가 0 근처여야 한다(수수료 제외) — 한쪽만 넣으면 이 등식이 깨진다
    assert income - (paid - SHIPPING_COST_NORMAL) == Decimal("0")
