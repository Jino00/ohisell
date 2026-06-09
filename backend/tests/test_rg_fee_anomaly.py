# test_rg_fee_anomaly.py — SA3 detect_fee_anomalies fixture 테스트 (트랙 RG-Fee S8, D-12·D-17)
# 스크리닝 신호 검증: missing_dims / oversize / unit_unknown / below_floor / size_mismatch_high.
from app.services.coupang.rg_fee_anomaly import detect_fee_anomalies


def test_normal_small_item_no_flags():
    # 극소형(합 37.3cm, 137g) · 단가 배송 1900(>=극소형 floor 1350) · 입출고 1100 · qty 1
    r = detect_fee_anomalies(227, 126, 20, 137, delivery_amount=1900, warehousing_amount=1100, quantity=1)
    assert r["size_type"] == "극소형"
    assert r["per_unit_delivery"] == 1900
    assert "below_floor" not in r["flags"]
    # 배송 1900은 소형(1550) floor 충족 but 중형(2100) 미달 → implied 소형. our=극소형 → 한단계 위.
    assert r["implied_size_delivery"] == "소형"
    # ★1900/극소형floor1350 = 1.41배 < 2배 → 정상(오탐 억제). size_mismatch_high 미발화.
    assert r["flags"] == []


def test_missing_dims_flag():
    r = detect_fee_anomalies(None, 100, 20, 137, delivery_amount=1900, warehousing_amount=1100, quantity=1)
    assert r["size_type"] is None
    assert "missing_dims" in r["flags"]


def test_unit_unknown_when_no_quantity():
    r = detect_fee_anomalies(227, 126, 20, 137, delivery_amount=1900, warehousing_amount=1100, quantity=0)
    assert "unit_unknown" in r["flags"]
    assert r["per_unit_delivery"] is None


def test_size_mismatch_high_overcharge_suspect():
    # 극소형 치수인데 배송비 단가 19,750 → 특대형(5600) floor도 한참 초과 → 큰 불일치
    r = detect_fee_anomalies(227, 126, 20, 137, delivery_amount=19750, warehousing_amount=1100, quantity=1)
    assert r["size_type"] == "극소형"
    assert r["implied_size_delivery"] == "특대형"
    assert "size_mismatch_high" in r["flags"]


def test_below_floor_flag():
    # 중형(합 110cm) 치수인데 배송비 단가 1000 → 중형 floor 2100 미달 → below_floor
    r = detect_fee_anomalies(500, 400, 200, 6000, delivery_amount=1000, warehousing_amount=500, quantity=1)
    assert r["size_type"] == "중형"
    assert "below_floor" in r["flags"]


def test_oversize_flag():
    r = detect_fee_anomalies(1000, 1000, 600, 3000, delivery_amount=5600, warehousing_amount=1375, quantity=1)
    assert r["size_type"] == "초과"
    assert "oversize" in r["flags"]
