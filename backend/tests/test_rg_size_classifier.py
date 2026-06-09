# test_rg_size_classifier.py — SA1 classify_size_type 경계값 fixture 테스트 (트랙 RG-Fee S8, D-12·D-17)
# 공식표(레퍼런스 17 §7): 세변의 합(cm) AND 무게(kg) 둘 다 충족, 하나라도 초과시 상위 등급.
import pytest

from app.services.coupang.rg_size_classifier import (
    SIZE_TYPES,
    classify_size_type,
)


# (width_mm, length_mm, height_mm, weight_g, expected) — 합=mm/10 cm, 무게=g/1000 kg
@pytest.mark.parametrize(
    "w,l,h,wt,expected",
    [
        # 극소형: 세변합 ≤80cm AND 무게 ≤2kg
        (300, 300, 200, 1000, "극소형"),  # 합 80.0cm 경계(이하) · 1kg
        (200, 200, 200, 500, "극소형"),  # 합 60cm · 0.5kg
        # 세변합 80cm 정확히(800mm) → 극소형(이하 포함)
        (400, 300, 100, 2000, "극소형"),  # 합 80.0cm · 무게 2.0kg 정확(극소형 상한)
        # 소형: 80<합≤100 OR 무게 2<≤5 → 상위 채택
        (400, 300, 101, 1000, "소형"),  # 합 80.1cm → 소형 (무게는 극소형)
        (300, 300, 200, 2100, "소형"),  # 합 80cm(극소형) · 무게 2.1kg → 소형(무게가 상위)
        (500, 300, 200, 5000, "소형"),  # 합 100cm 경계 · 무게 5kg 경계
        # 중형: 100<합≤120 OR 5<무게≤10
        (500, 400, 101, 3000, "중형"),  # 합 100.1cm → 중형
        (400, 300, 100, 5100, "중형"),  # 합 80cm(극소형) · 무게 5.1kg → 중형(무게 상위)
        # 대형1: 120<합≤140 OR 10<무게≤15
        (600, 500, 101, 3000, "대형1"),  # 합 120.1cm → 대형1
        (300, 300, 200, 10100, "대형1"),  # 합 80cm · 무게 10.1kg → 대형1
        # 대형2: 140<합≤160 OR 15<무게≤20
        (700, 600, 101, 3000, "대형2"),  # 합 140.1cm → 대형2
        # 특대형: 160<합≤250 OR 20<무게≤30
        (900, 800, 101, 3000, "특대형"),  # 합 180.1cm → 특대형
        (300, 300, 200, 20100, "특대형"),  # 합 80cm · 무게 20.1kg → 특대형
        # ★규칙 핵심: 세변 극소형 + 무게 소형 → 소형 (공식 예시)
        (200, 200, 200, 3000, "소형"),  # 합 60cm(극소형) · 무게 3kg(소형) → 소형
    ],
)
def test_classify_boundaries(w, l, h, wt, expected):
    assert classify_size_type(w, l, h, wt) == expected


def test_over_limit_returns_over():
    # 세변합 250cm 초과 → 입고 불가(over)
    assert classify_size_type(1000, 1000, 600, 3000) == "초과"  # 합 260cm
    # 무게 30kg 초과 → over
    assert classify_size_type(300, 300, 200, 31000) == "초과"  # 31kg


def test_missing_dimensions_returns_none():
    assert classify_size_type(None, 300, 200, 1000) is None
    assert classify_size_type(300, 300, 200, None) is None
    assert classify_size_type(0, 0, 0, 0) is None  # 0 = 미측정 취급


def test_size_types_ordered():
    # SIZE_TYPES는 작은→큰 순서(상위 채택 비교용)
    assert SIZE_TYPES == ["극소형", "소형", "중형", "대형1", "대형2", "특대형"]
