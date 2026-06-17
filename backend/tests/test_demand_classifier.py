# test_demand_classifier.py — demand_classifier SA fixture 테스트 (Phase 2 S8, D-14·X1, R4)
# ADI/CV² 4분면 경계값(Syntetos-Boylan) + unknown 게이트 + X1 버킷 + 집계 검증.
# oracle 값은 손계산(주석에 산식 명시) — statsforecast 레퍼런스 안 씀(X8).
from datetime import date
from unittest.mock import patch

from app.services.coupang.demand_classifier import (
    ACTIVE_MIN_NONZERO_DAYS,
    ADI_CUT,
    CV2_CUT,
    _adi,
    _bucket,
    _classify,
    _classify_series,
    _cv2_nonzero,
    classify_demand,
    classify_demand_one,
)


# ════════════════════════════════════════════════
# _adi 단위 테스트
# ════════════════════════════════════════════════
def test_adi_daily_demand():
    """매일 판매 → ADI = 14/14 = 1.0."""
    assert _adi([5] * 14) == 1.0


def test_adi_intermittent():
    """14일 중 3일 판매 → ADI = 14/3 ≈ 4.667."""
    series = [5, 0, 0, 0, 0, 5, 0, 0, 0, 0, 5, 0, 0, 0]
    assert _adi(series) == 14 / 3


def test_adi_no_demand_none():
    """판매 0일 → ADI None."""
    assert _adi([0] * 14) is None


# ════════════════════════════════════════════════
# _cv2_nonzero 단위 테스트 (0인 날 제외, 모집단 std)
# ════════════════════════════════════════════════
def test_cv2_constant_size_zero():
    """수량 항상 동일(5) → 변동 0 → CV² = 0."""
    series = [5, 0, 0, 0, 0, 5, 0, 0, 0, 0, 5, 0, 0, 0]
    assert _cv2_nonzero(series) == 0.0


def test_cv2_erratic():
    """nonzero=[1,9]×5, mean=5, pop_var=16, std=4 → CV²=(4/5)²=0.64."""
    series = [1, 9] * 5
    assert abs(_cv2_nonzero(series) - 0.64) < 1e-9


def test_cv2_lumpy():
    """nonzero=[1,5,12], mean=6, pop_var=(25+1+36)/3=20.667, std=4.546 → CV²=0.5741."""
    series = [1, 0, 0, 0, 0, 5, 0, 0, 0, 0, 12, 0, 0, 0]
    assert abs(_cv2_nonzero(series) - 0.574074) < 1e-5


def test_cv2_single_nonzero_none():
    """nonzero 1개 → 표준편차 정의 불가 → None(② 게이트)."""
    assert _cv2_nonzero([0, 0, 3, 0, 0]) is None


def test_cv2_zero_nonzero_none():
    """nonzero 0개 → None."""
    assert _cv2_nonzero([0] * 14) is None


# ════════════════════════════════════════════════
# _classify 경계값 테스트 (컷 포함성)
# ════════════════════════════════════════════════
def test_classify_smooth():
    assert _classify(1.0, 0.0) == "smooth"


def test_classify_erratic():
    assert _classify(1.0, 0.64) == "erratic"


def test_classify_intermittent():
    assert _classify(4.667, 0.0) == "intermittent"


def test_classify_lumpy():
    assert _classify(4.667, 0.574) == "lumpy"


def test_classify_adi_boundary_inclusive():
    """ADI 정확히 1.32 → 간헐(>=)로 분류."""
    assert _classify(ADI_CUT, 0.0) == "intermittent"
    assert _classify(ADI_CUT - 0.01, 0.0) == "smooth"


def test_classify_cv2_boundary_inclusive():
    """CV² 정확히 0.49 → erratic(>=)로 분류."""
    assert _classify(1.0, CV2_CUT) == "erratic"
    assert _classify(1.0, CV2_CUT - 0.01) == "smooth"


def test_classify_unknown_when_none():
    assert _classify(None, 0.0) == "unknown"
    assert _classify(5.0, None) == "unknown"
    assert _classify(None, None) == "unknown"


# ════════════════════════════════════════════════
# _bucket 테스트 (X1)
# ════════════════════════════════════════════════
def test_bucket_zero_signal():
    assert _bucket(0) == "zero_signal"


def test_bucket_sparse():
    assert _bucket(1) == "sparse"
    assert _bucket(ACTIVE_MIN_NONZERO_DAYS - 1) == "sparse"


def test_bucket_active():
    assert _bucket(ACTIVE_MIN_NONZERO_DAYS) == "active"
    assert _bucket(14) == "active"


# ════════════════════════════════════════════════
# _classify_series 통합 (라벨+버킷 동시)
# ════════════════════════════════════════════════
def test_series_smooth_active():
    info = _classify_series([5] * 14)
    assert info["demand_class"] == "smooth"
    assert info["bucket"] == "active"
    assert info["nonzero_days"] == 14
    assert info["observed_qty"] == 70


def test_series_intermittent_sparse():
    info = _classify_series([5, 0, 0, 0, 0, 5, 0, 0, 0, 0, 5, 0, 0, 0])
    assert info["demand_class"] == "intermittent"
    assert info["bucket"] == "sparse"  # nonzero=3 < 7
    assert info["adi"] == round(14 / 3, 3)
    assert info["cv2"] == 0.0


def test_series_one_sale_unknown_sparse():
    """nonzero=1 → class unknown(CV² 불가), bucket sparse."""
    info = _classify_series([0, 0, 3, 0, 0])
    assert info["demand_class"] == "unknown"
    assert info["bucket"] == "sparse"
    assert info["cv2"] is None


def test_series_all_zero_unknown_zerosignal():
    info = _classify_series([0] * 14)
    assert info["demand_class"] == "unknown"
    assert info["bucket"] == "zero_signal"
    assert info["adi"] is None


# ════════════════════════════════════════════════
# classify_demand 집계 (로더 patch)
# ════════════════════════════════════════════════
_WINDOW = (date(2026, 6, 4), date(2026, 6, 17), 14)


def test_classify_demand_summary():
    series_map = {
        "SMOOTH": [5] * 14,                                          # active / smooth
        "INT": [5, 0, 0, 0, 0, 5, 0, 0, 0, 0, 5, 0, 0, 0],          # sparse / intermittent
        "ONE": [0, 0, 3] + [0] * 11,                                 # sparse / unknown
    }
    inv = {"SMOOTH": 50, "INT": 15, "ONE": 3, "DEAD": 0}            # DEAD: 재고만, 판매 0

    with patch("app.services.coupang.demand_classifier._trust_window", return_value=_WINDOW), \
         patch("app.services.coupang.demand_classifier._load_daily_series", return_value=series_map), \
         patch("app.services.coupang.demand_classifier._load_inventory_options", return_value=inv):
        res = classify_demand(object())

    opts = res["options"]
    assert opts["SMOOTH"]["demand_class"] == "smooth"
    assert opts["SMOOTH"]["bucket"] == "active"
    assert opts["INT"]["demand_class"] == "intermittent"
    assert opts["ONE"]["demand_class"] == "unknown"
    assert opts["DEAD"]["bucket"] == "zero_signal"       # 판매 0 → 정직 표기
    assert opts["DEAD"]["demand_class"] == "unknown"
    assert opts["DEAD"]["sold_30d"] == 0
    assert opts["SMOOTH"]["sold_30d"] == 50              # 참고치 passthrough

    summary = res["summary"]
    assert summary["total"] == 4
    assert summary["by_bucket"]["zero_signal"] == 1
    assert summary["by_bucket"]["active"] == 1
    assert summary["by_bucket"]["sparse"] == 2
    assert summary["by_class"]["unknown"] == 2           # ONE + DEAD


def test_classify_demand_one_found():
    series_map = {"INT": [5, 0, 0, 0, 0, 5, 0, 0, 0, 0, 5, 0, 0, 0]}
    inv = {"INT": 15}
    with patch("app.services.coupang.demand_classifier._trust_window", return_value=_WINDOW), \
         patch("app.services.coupang.demand_classifier._load_daily_series", return_value=series_map), \
         patch("app.services.coupang.demand_classifier._load_inventory_options", return_value=inv):
        res = classify_demand_one(object(), "INT")
    assert res is not None
    assert res["demand_class"] == "intermittent"
    assert res["sold_30d"] == 15


def test_classify_demand_one_missing_none():
    """재고·판매 둘 다 없는 옵션 → None."""
    with patch("app.services.coupang.demand_classifier._trust_window", return_value=_WINDOW), \
         patch("app.services.coupang.demand_classifier._load_daily_series", return_value={}), \
         patch("app.services.coupang.demand_classifier._load_inventory_options", return_value={}):
        res = classify_demand_one(object(), "GHOST")
    assert res is None
