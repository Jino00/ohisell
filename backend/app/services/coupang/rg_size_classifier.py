# rg_size_classifier.py — SA1: RG 사이즈 유형 분류 (트랙 RG-Fee S8, D-17)
# 단일 책임: 개별 포장 상품의 (가로·세로·높이 mm, 무게 g) → 공식 사이즈 유형 1개.
# 공식표: 레퍼런스 17 §7 (Wing fee-details 라이브 확보 2026-06-09).
#   세변의 합(cm) AND 무게(kg) 둘 다 충족해야 해당 유형. 하나라도 초과시 상위 유형(= 둘 중 큰 등급).
# 순수함수(부작용 없음) — DB·외부호출 없음. fixture 테스트로 경계값 잠금(D-12).
from __future__ import annotations

# 작은→큰 순서. 상위 채택 비교 시 인덱스가 클수록 큰 등급.
SIZE_TYPES: list[str] = ["극소형", "소형", "중형", "대형1", "대형2", "특대형"]

# 각 유형의 상한(이하). (세변합_cm_max, 무게_kg_max). 초과하면 다음 유형으로.
# 극소형 ~80cm/~2kg, 소형 ~100/~5, 중형 ~120/~10, 대형1 ~140/~15, 대형2 ~160/~20, 특대형 ~250/~30.
_SUM_CM_MAX = [80, 100, 120, 140, 160, 250]
_WEIGHT_KG_MAX = [2, 5, 10, 15, 20, 30]


def _tier_by_threshold(value: float, thresholds: list[float]) -> int:
    """value 이상을 만족하는 최소 등급 인덱스. 모든 상한 초과면 len(=초과)."""
    for idx, ceiling in enumerate(thresholds):
        if value <= ceiling:
            return idx
    return len(thresholds)  # 최대 상한도 초과 → '초과'


def classify_size_type(
    width_mm: int | None,
    length_mm: int | None,
    height_mm: int | None,
    weight_g: int | None,
) -> str | None:
    """치수·무게 → 사이즈 유형. 미측정(None/0)이면 None. 한계 초과면 '초과'.

    규칙(공식): 세변합 등급과 무게 등급을 각각 구해 **더 큰 등급**을 채택.
    """
    dims = [width_mm, length_mm, height_mm, weight_g]
    if any(d is None or d <= 0 for d in dims):
        return None  # 치수/무게 미측정 → 분류 불가(감사 불가 표기용)

    sum_cm = (width_mm + length_mm + height_mm) / 10.0
    weight_kg = weight_g / 1000.0

    tier_sum = _tier_by_threshold(sum_cm, _SUM_CM_MAX)
    tier_weight = _tier_by_threshold(weight_kg, _WEIGHT_KG_MAX)
    tier = max(tier_sum, tier_weight)  # 하나라도 초과 → 상위(큰) 등급

    if tier >= len(SIZE_TYPES):
        return "초과"  # 특대형 한계도 초과 = 입고 불가
    return SIZE_TYPES[tier]
