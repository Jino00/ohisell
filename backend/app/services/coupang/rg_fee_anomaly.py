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
) -> dict:
    """이상치 플래그 + 근거 수치 반환.

    플래그:
      missing_dims        치수 미측정 → 감사 불가
      oversize            '초과'(입고 불가 사이즈) 분류 — 데이터 점검
      unit_unknown        수량 없음/0 → 단가 정규화 불가(과오청구 단정 금지)
      below_floor         단가 < 우리 사이즈 최소금액 → 데이터/사이즈 과대등록 의심
      size_mismatch_high  배송 단가가 우리 사이즈 최소금액의 2배+ AND 큰 등급에 정합
                          → 과대측정/과오청구 의심(정확 금액표로 확인 필요). floor는 최소치라
                          1~1.x배는 정상 가능 → 배수 임계로 오탐 억제.
    """
    size_type = classify_size_type(width_mm, length_mm, height_mm, weight_g)
    flags: list[str] = []
    result: dict = {
        "size_type": size_type,
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

    if delivery_amount is not None:
        per_unit_delivery = delivery_amount / quantity
        result["per_unit_delivery"] = round(per_unit_delivery, 2)
        implied = implied_size_from_delivery(per_unit_delivery)
        result["implied_size_delivery"] = implied
        floor_delivery = floor.get("delivery")
        if floor_delivery is not None and per_unit_delivery < floor_delivery:
            flags.append("below_floor")
        elif (
            implied is not None
            and SIZE_TYPES.index(implied) > our_idx
            and floor_delivery is not None
            and per_unit_delivery >= floor_delivery * _DELIVERY_OVERCHARGE_MULTIPLE
        ):
            # 우리 등급보다 큰 사이즈에 정합 + 최소금액 2배+ → 과대측정/과오청구 의심
            flags.append("size_mismatch_high")

    if warehousing_amount is not None:
        per_unit_wh = warehousing_amount / quantity
        result["per_unit_warehousing"] = round(per_unit_wh, 2)
        floor_wh = floor.get("warehousing")
        if floor_wh is not None and per_unit_wh < floor_wh and "below_floor" not in flags:
            flags.append("below_floor")

    return result
