# rg_fee_anomaly.py — SA3: RG 청구액 이상치 탐지 (트랙 RG-Fee S8, D-17)
# 단일 책임: (치수·무게, 주별 청구 배송/입출고비, 판매수량) → 이상치 플래그 + 근거 수치.
# ★스크리닝 도구지 확정 계산기 아님(D-5). 합포장으로 주별집계÷수량이 깔끔한 단가가 아닐 수 있어
#   플래그는 "사람 검토 신호"이지 과오청구 단정이 아니다. 근거 수치를 함께 반환해 Jino가 판단.
# 순수함수 — DB·외부호출 없음. SA1(분류)·SA2(floor)만 호출. fixture 테스트로 잠금(D-12).
from __future__ import annotations

from app.services.coupang.rg_fee_reference import (
    expected_fee_floor,
    implied_size_from_delivery,
)
from app.services.coupang.rg_size_classifier import SIZE_TYPES, classify_size_type

# 배송 단가가 우리 사이즈 최소금액의 이 배수 이상이면 "검토" 플래그.
# floor는 최소치라 1~1.x배는 카테고리·판매가 변동으로 정상일 수 있음(오탐). 2배+는 gross outlier.
_DELIVERY_OVERCHARGE_MULTIPLE = 2.0


def detect_fee_anomalies(
    width_mm: int | None,
    length_mm: int | None,
    height_mm: int | None,
    weight_g: int | None,
    *,
    delivery_amount: float | None,
    warehousing_amount: float | None,
    quantity: int | None,
    order_count: int | None = None,
    coupang_size_type: str | None = None,
) -> dict:
    """이상치 플래그 + 근거 수치 반환.

    정규화(codex P2): 배송비는 합포장 시 **주문당 1회** → order_count로 나눔(주문당 단가).
      입출고비는 **수량당** → quantity로 나눔. order_count 미지정 시 quantity로 폴백.
    배송 floor·입출고 floor 모두 각 부과단위(주문당·수량당)와 정합.

    플래그:
      missing_dims        치수 미측정 → 감사 불가
      oversize            '초과'(입고 불가 사이즈) 분류 — 데이터 점검
      unit_unknown        수량 없음/0 → 단가 정규화 불가(과오청구 단정 금지)
      below_floor         단가 < 우리 사이즈 최소금액 → 데이터/사이즈 과대등록 의심
      size_mismatch_high  배송 주문당 단가가 우리 사이즈 최소금액의 2배+ AND 큰 등급에 정합
                          → 과대측정/과오청구 의심(정확 금액표로 확인 필요). floor는 최소치라
                          1~1.x배는 정상 가능 → 배수 임계로 오탐 억제.
    """
    # 쿠팡 실측 사이즈가 있으면 우선 사용(과금 기준). 없으면 등록 치수로 분류(폴백).
    size_type = coupang_size_type or classify_size_type(width_mm, length_mm, height_mm, weight_g)
    flags: list[str] = []
    result: dict = {
        "size_type": size_type,
        "size_source": "coupang_measured" if coupang_size_type else "registered_dims",
        "per_unit_delivery": None,
        "per_unit_warehousing": None,
        "floor": expected_fee_floor(size_type),
        "implied_size_delivery": None,
        "flags": flags,
    }

    if size_type is None:
        flags.append("missing_dims")
        return result
    if size_type == "초과":
        flags.append("oversize")
        return result

    if not quantity or quantity <= 0:
        flags.append("unit_unknown")
        return result

    floor = expected_fee_floor(size_type) or {}
    our_idx = SIZE_TYPES.index(size_type)
    # 배송비는 주문당(합포장) → 주문수로 정규화. 미지정 시 수량 폴백(보수적).
    delivery_divisor = order_count if order_count and order_count > 0 else quantity

    if delivery_amount is not None:
        per_order_delivery = delivery_amount / delivery_divisor
        result["per_unit_delivery"] = round(per_order_delivery, 2)
        implied = implied_size_from_delivery(per_order_delivery)
        result["implied_size_delivery"] = implied
        floor_delivery = floor.get("delivery")
        if floor_delivery is not None and per_order_delivery < floor_delivery:
            flags.append("below_floor")
        elif (
            coupang_size_type is None  # 쿠팡 실측값 있으면 스킵 — 실측값이 과금 기준
            and implied is not None
            and SIZE_TYPES.index(implied) > our_idx
            and floor_delivery is not None
            and per_order_delivery >= floor_delivery * _DELIVERY_OVERCHARGE_MULTIPLE
        ):
            # 쿠팡 실측 사이즈 미수집 + 배송비가 우리 등급보다 큰 사이즈에 정합 + 2배+
            # → 과대측정 의심. 쿠팡 실측값이 있으면 그 값이 과금 기준이므로 이 추정 불필요.
            flags.append("size_mismatch_high")

    if warehousing_amount is not None:
        per_unit_wh = warehousing_amount / quantity
        result["per_unit_warehousing"] = round(per_unit_wh, 2)
        floor_wh = floor.get("warehousing")
        if floor_wh is not None and per_unit_wh < floor_wh and "below_floor" not in flags:
            flags.append("below_floor")

    return result
