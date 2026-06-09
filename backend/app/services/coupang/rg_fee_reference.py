# rg_fee_reference.py — SA2: RG 사이즈 유형별 수수료 최소 레퍼런스 (트랙 RG-Fee S8, D-17)
# 단일 책임: 사이즈 유형 → 입출고비·배송비 **최소 금액**(VAT 별도).
# ★출처: 레퍼런스 17 §7 (Wing 로켓그로스 공식 비용표, 라이브 2026-06-09). "~원 부터" = 하한.
# ★중요: 이 값은 **최소치**이며 상한이 아니다. 정확액은 카테고리·판매가·합포장·저가할인에 따라
#   더 커질 수 있다(D-17). 따라서 감사는 "이 최소보다 낮으면 이상", "한참 높으면 검토"의
#   스크리닝 신호로만 쓰고 정확 청구액 판정은 하지 않는다(D-5: 사실만, 판단은 Jino).
from __future__ import annotations

from app.services.coupang.rg_size_classifier import SIZE_TYPES

# 사이즈 유형 → {"warehousing": 입출고비 최소(원, VAT별도), "delivery": 배송비 최소}
_FEE_FLOOR: dict[str, dict[str, int]] = {
    "극소형": {"warehousing": 600, "delivery": 1350},
    "소형": {"warehousing": 650, "delivery": 1550},
    "중형": {"warehousing": 1250, "delivery": 2100},
    "대형1": {"warehousing": 1375, "delivery": 2200},
    "대형2": {"warehousing": 1375, "delivery": 4100},
    "특대형": {"warehousing": 1375, "delivery": 5600},
}


def expected_fee_floor(size_type: str | None) -> dict | None:
    """사이즈 유형 → {'warehousing': 최소, 'delivery': 최소} (VAT 별도). 미지 유형이면 None."""
    if size_type is None:
        return None
    return _FEE_FLOOR.get(size_type)


def implied_size_from_delivery(per_unit_delivery: float) -> str | None:
    """단가(배송비/단위)로부터 **하한 정합 사이즈 유형**(이 단가를 설명하는 최대 등급).

    배송비 최소금액은 등급에 따라 단조 증가 → per-unit이 floor(T) 이상인 최대 T를 반환.
    None = 최소(극소형) 배송비 floor에도 못 미침(below_floor 신호).
    """
    result = None
    for st in SIZE_TYPES:
        if per_unit_delivery >= _FEE_FLOOR[st]["delivery"]:
            result = st
        else:
            break
    return result
