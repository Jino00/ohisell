# commission_resolver.py — SA: payment_type + 금액 → PG 수수료액
# 단일 책임: 공식 요율표 적용 (KCP/카카오/토스=VAT별도 ×1.1, 네이버페이=VAT포함)
# 다른 SA를 모름. 순수 함수.
# 출처: KCP/카카오/토스 cafe24 결제대행사 안내 / 네이버페이 help.admin.pay.naver.com
#       (네이버페이 신용카드 등급 = 영세, Jino 확인)
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_VAT = Decimal("1.1")  # KCP/카카오/토스: 수수료에 VAT 별도 → ×1.1
_WON = Decimal("1")    # 원화 정산은 원 단위. (PG별 절사/반올림 정확 규칙 미확인 → 표준 반올림)

# 정률 요율 (VAT 반영 완료된 실효율)
_RATE = {
    "kcp_card": Decimal("0.035") * _VAT,
    "kakaopay": Decimal("0.035") * _VAT,
    "tosspay": Decimal("0.035") * _VAT,
    # 네이버페이 (요율에 VAT 이미 포함)
    "naverpay_card": Decimal("0.0187"),      # 영세
    "naverpay_transfer": Decimal("0.0165"),
    "naverpay_aux": Decimal("0.0374"),
    "naverpay_cell": Decimal("0.0385"),
}

# 주문 단위로 1회만 부과되는 유형 (정액/상한/최저). Harness가 '주문총액'으로 호출 후 라인 비례배분.
#  - kcp_bank: 300원 정액
#  - naverpay_bank: min(금액×1%, 275) 상한
#  - kcp_transfer: 최저 200원 floor가 거래(주문) 단위 → 라인별 호출 시 floor 중복 (Codex P1-1)
PER_ORDER_TYPES = frozenset({"kcp_bank", "naverpay_bank", "kcp_transfer"})

# 수수료 0 유형 (적립금/쿠폰 전액결제·에스크로 = PG 미경유, 0이 정확)
_ZERO_TYPES = frozenset({"kcp_escrow", "etc"})

# 인식 불가 payment_type 보수 요율 (과소청구 = 순이익 과대 방지, Codex P1-2)
_FALLBACK_RATE = Decimal("0.0385")


def resolve(payment_type: str, amount: Decimal | int | float) -> Decimal:
    """payment_type + 금액(주문총액 또는 라인매출) → 수수료액 (원 단위 반올림).

    - 정률 유형: 라인매출로 호출해도 합산 시 정확 (선형).
    - PER_ORDER_TYPES(kcp_bank/naverpay_bank/kcp_transfer): 반드시 '주문총액'으로 호출.
      kcp_bank=300원 정액×1.1, naverpay_bank=min(금액×1%,275), kcp_transfer=max(금액×1.8%,200)×1.1.
    - 인식 불가 유형: 0이 아닌 보수 요율(_FALLBACK_RATE) 적용. 호출 Harness가 로깅 책임.
    - 음수 금액(환불/조정 오입력)은 0으로 클램프 (호출 계약: 상태필터 후 settled amount 전달).
    """
    amt = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    if amt < 0:
        amt = Decimal("0")

    if payment_type in _ZERO_TYPES:
        return Decimal("0")

    if payment_type == "kcp_bank":
        return (Decimal("300") * _VAT).quantize(_WON, ROUND_HALF_UP)

    if payment_type == "naverpay_bank":
        fee = amt * Decimal("0.01")
        cap = Decimal("275")
        return (fee if fee < cap else cap).quantize(_WON, ROUND_HALF_UP)

    if payment_type == "kcp_transfer":
        fee = amt * Decimal("0.018")
        floor = Decimal("200")
        base = fee if fee > floor else floor
        return (base * _VAT).quantize(_WON, ROUND_HALF_UP)

    if payment_type == "naverpay_transfer":
        return (amt * _RATE["naverpay_transfer"]).quantize(_WON, ROUND_HALF_UP)

    rate = _RATE.get(payment_type, _FALLBACK_RATE)
    return (amt * rate).quantize(_WON, ROUND_HALF_UP)
