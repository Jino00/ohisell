# 이 파일은 배송비 수입/수수료 머니코드 테스트입니다.
# 가드 대상 2종 (2026-08-03, N배송 3,000원 정책 도입 후 발견):
#   ① 배송수입 이중계상 — deliveryFeeAmount는 배송(패키지) 값이 라인마다 복사돼 있는데
#      네이버만 라인 합산하고 있었다. 라이브 채널6 전 기간 62,500원 과대(21패키지).
#      정산이 이를 확정: DELIVERY 원장 행은 배송 1건당 1행(N배송 423패키지 ↔ 110행).
#   ② 배송비 수수료 누락 — "배송비엔 수수료 미부과" 가정이 틀렸다. 정산 DELIVERY 행에
#      주문관리 수수료가 실제로 붙는다(전 기간 1,652행 −119,270원, 3,000원→81원).
#
# N배송 배송비 구조(라이브 실측 438건): deliveryFeeAmount는 항상 3,000이고
#   멤버십(deliveryDiscountAmount=3,000, 61.6%)이면 네이버가 면제하고 그 돈을 우리에게,
#   비멤버십(=0, 37.7%)이면 고객이 우리에게 → **어느 쪽이든 우리 수입 3,000원**.
import json
from decimal import Decimal

import pytest

from app.models import Channel, Order
from app.services.profit_calculator import (
    NAVER_DELIVERY_COMMISSION_RATE,
    _delivery_commission,
    _delivery_income,
    _delivery_income_map,
    _shipment_key,
)

D = Decimal
NAVER = Channel(code="NAVER", channel_type="own")
COUPANG = Channel(code="COUPANG_WING1", channel_type="own")
CAFE24 = Channel(code="CAFE24", channel_type="own")


def _naver_line(pkg: str, fee, *, discount=0) -> Order:
    """N배송 라인 1건. discount=3000이면 멤버십(고객 미결제)이지만 우리 수입은 동일."""
    return Order(
        channel_id=6, order_number=pkg, shipping_cost=fee,
        raw_data=json.dumps({"productOrder": {
            "packageNumber": pkg,
            "deliveryAttributeType": "ARRIVAL_GUARANTEE",
            "deliveryFeeAmount": int(fee or 0),
            "deliveryDiscountAmount": discount,
        }}),
    )


# ── ① 배송수입은 배송(패키지)당 1회 ────────────────────────────────────────
def test_naver_lines_in_one_package_share_the_same_key():
    # ★이중계상의 뿌리: 같은 패키지의 라인들이 같은 배송키를 갖고, 각 라인이 3,000을 들고 있다.
    a, b = _naver_line("pkg-1", D("3000")), _naver_line("pkg-1", D("3000"))
    assert _shipment_key(NAVER, a) == _shipment_key(NAVER, b)
    assert _delivery_income(NAVER, a) == _delivery_income(NAVER, b) == D("3000")
    # 라인 합(6,000)을 쓰면 실제 정산(3,000)의 2배 — 호출부가 배송당 1회만 가산해야 한다.


def test_split_shipment_keeps_two_keys():
    # 분할배송이면 packageNumber가 갈리고 정산 DELIVERY 행도 2개 → 배송비도 2회가 맞다.
    a, b = _naver_line("pkg-1", D("3000")), _naver_line("pkg-2", D("3000"))
    assert _shipment_key(NAVER, a) != _shipment_key(NAVER, b)


def test_membership_and_non_membership_yield_same_income():
    # 멤버십(할인 3,000)이든 아니든 우리가 정산받는 금액은 3,000으로 같다.
    member = _naver_line("pkg-m", D("3000"), discount=3000)
    non_member = _naver_line("pkg-n", D("3000"), discount=0)
    assert _delivery_income(NAVER, member) == _delivery_income(NAVER, non_member) == D("3000")


def test_free_shipping_has_no_income():
    assert _delivery_income(NAVER, _naver_line("pkg-f", None)) == D("0")


def test_cafe24_has_no_delivery_income():
    o = Order(channel_id=2, order_number="x", shipping_cost=D("3000"), raw_data="{}")
    assert _delivery_income(CAFE24, o) == D("0")


# ── _delivery_income_map: 배송별 수입 선행 확정 (codex 2R P2) ─────────────
_CH_MAP = {6: NAVER}


def test_income_map_charges_package_once():
    lines = [_naver_line("pkg-1", D("3000")) for _ in range(3)]  # 한 패키지 3라인
    assert sum(_delivery_income_map(lines, _CH_MAP).values()) == D("3000")


def test_income_map_is_order_independent_and_conservative():
    # ★codex 2R P2 회귀 가드: 값이 갈리면 first-wins는 입력 순서에 따라 답이 달라졌다.
    #   선행 확정 + 최솟값(수입은 적게 = 보수)이라 순서와 무관하다.
    a, b = _naver_line("pkg-x", D("3000")), _naver_line("pkg-x", D("2500"))
    assert _delivery_income_map([a, b], _CH_MAP) == _delivery_income_map([b, a], _CH_MAP)
    assert sum(_delivery_income_map([a, b], _CH_MAP).values()) == D("2500")


def test_income_map_excludes_cancelled_and_consignment():
    live = _naver_line("live", D("3000"))
    dead = _naver_line("dead", D("3000")); dead.status = "cancelled"
    assert sum(_delivery_income_map([live, dead], _CH_MAP).values()) == D("3000")
    consign = {6: Channel(code="NAVER", channel_type="consignment")}
    assert _delivery_income_map([live], consign) == {}


# ── ② 배송비 수취분에 붙는 주문관리 수수료 ─────────────────────────────────
# ★라이브 DELIVERY 원장 행 전수 대조(1,652건). 절사 1,644건 일치(99.5%) vs 반올림 1,522(92.1%).
#   특히 N배송의 유일한 금액인 3,000원은 절사(81)만 맞는다 — 반올림(82)이면 매 건 +1원 과대.
@pytest.mark.parametrize("fee,expected,n_live", [
    (D("3000"), D("81"), 127),    # 실측 81
    (D("2500"), D("68"), 1441),   # 실측 68
    (D("5000"), D("136"), 74),    # 실측 136
    (D("7500"), D("204"), 2),     # 실측 204
])
def test_delivery_commission_matches_live_settlement(fee, expected, n_live):
    assert _delivery_commission(NAVER, fee) == expected


def test_rounding_is_truncation_not_half_up():
    # ★codex 2R P2 회귀 가드: 반올림으로 되돌리면 3,000원이 82가 되어 N배송 매 건 +1원 과대.
    assert _delivery_commission(NAVER, D("3000")) == D("81")
    assert _delivery_commission(NAVER, D("3000")) != D("82")


def test_no_commission_without_delivery_income():
    # 무료배송이면 정산 DELIVERY 행 자체가 없다 → 수수료도 0.
    assert _delivery_commission(NAVER, D("0")) == D("0")


def test_delivery_commission_is_naver_only():
    # 쿠팡·cafe24는 배송비 수수료 구조가 확인되지 않았다 — 추정으로 부과하지 않는다.
    assert _delivery_commission(COUPANG, D("3000")) == D("0")
    assert _delivery_commission(CAFE24, D("3000")) == D("0")
    assert _delivery_commission(None, D("3000")) == D("0")


def test_delivery_commission_never_negative():
    assert _delivery_commission(NAVER, D("-3000")) == D("0")


def test_rate_is_order_management_rate():
    # 상품 주문관리 수수료율(2.724%)과 같은 요율이라는 계약 — 갈라지면 실측과 어긋난다.
    assert NAVER_DELIVERY_COMMISSION_RATE == D("0.02724")


def test_nbaesong_shipping_is_net_negative_but_better_than_free_shipping():
    """N배송 배송 손익 = 수취 3,000 − 수수료 81 − 품고 지불 3,245 = **−326원/건**.

    ★3,020 → 3,245 정정(2026-08-03, 품고 요율표 2: 배송비 극소형 2,050 + 출고 작업비 900,
      VAT 포함). 그래도 일반 무료배송(−1,900원/건)보다 1,574원 유리하다 —
      **이 부등호가 뒤집히면 회귀다**(N배송 전환의 근거 자체가 사라진다)."""
    nb_net = D("3000") - _delivery_commission(NAVER, D("3000")) - D("3245")
    normal_net = D("0") - D("0") - D("1900")
    assert nb_net == D("-326")
    assert nb_net > normal_net
