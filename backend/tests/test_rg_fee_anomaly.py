# test_rg_fee_anomaly.py — SA3 detect_fee_anomalies fixture 테스트 (트랙 RG-Fee S8, D-12·D-17)
# 스크리닝 신호 검증: missing_dims / oversize / unit_unknown / below_floor / size_mismatch_high.
from app.services.coupang.rg_fee_anomaly import (
    detect_fee_anomalies,
    detect_fee_anomalies_by_period,
)


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


def test_copackable_multiqty_no_false_below_floor():
    # codex P2-1 회귀: 극소형 3개를 1주문에 합포장 판매 → 배송비 1회(1900) 청구.
    # 배송은 order_count(1)로 정규화해야 함. quantity(3)로 나누면 633<floor → 오탐 below_floor.
    r = detect_fee_anomalies(
        227, 126, 20, 137,
        delivery_amount=1900, warehousing_amount=3300, quantity=3, order_count=1,
    )
    assert r["per_unit_delivery"] == 1900  # 주문당 1회 (3으로 안 나눔)
    assert "below_floor" not in r["flags"]
    assert r["per_unit_warehousing"] == 1100  # 입출고는 수량당 (3300/3)


def test_below_floor_flag():
    # 중형(합 110cm) 치수인데 배송비 단가 1000 → 중형 floor 2100 미달 → below_floor
    r = detect_fee_anomalies(500, 400, 200, 6000, delivery_amount=1000, warehousing_amount=500, quantity=1)
    assert r["size_type"] == "중형"
    assert "below_floor" in r["flags"]


def test_oversize_flag():
    r = detect_fee_anomalies(1000, 1000, 600, 3000, delivery_amount=5600, warehousing_amount=1375, quantity=1)
    assert r["size_type"] == "초과"
    assert "oversize" in r["flags"]


# ── 실측 vs 청구 불일치 (2026-08-03 신설) ────────────────────────────────
# ★배경: 종전엔 쿠팡 실측값이 있으면 불일치 판정을 **통째로 스킵**했다("실측값이 과금 기준이므로
#   추정 불필요"). 그 논리는 "실측 사이즈 = 청구 사이즈"를 전제하는데 라이브가 반증했다 —
#   옵션 91313543029(아이패드미니 필름, WING2, prod /rg/fee-audit 2026-08-03 실측):
#     size_type='극소형' · size_source='coupang_measured'
#     charged_delivery=8100 / order_count=2 → 주문당 4,050원 (극소형 floor 1,350의 3.0배)
#     implied_size_delivery='대형1'
#   실측이 들어온 것이 불일치를 해소한 게 아니라 확인해 줬는데, 코드는 그걸 "볼 필요 없음"으로
#   읽고 신호를 껐다. 2026-06-15에 올라온 플래그가 숫자 하나 안 바뀐 채 조용히 사라졌다.
_LIVE = dict(width_mm=355, length_mm=245, height_mm=5, weight_g=169,
             delivery_amount=8100.0, warehousing_amount=4700.0, quantity=2, order_count=2)


def test_measured_vs_billed_mismatch_is_flagged():
    """★회귀: 실측 '극소형' + 청구 함의 '대형1' 3배 → 신호가 떠야 한다(종전엔 침묵)."""
    r = detect_fee_anomalies(**_LIVE, coupang_size_type="극소형")
    assert r["size_type"] == "극소형"
    assert r["size_source"] == "coupang_measured"
    assert r["per_unit_delivery"] == 4050.0
    assert r["implied_size_delivery"] == "대형1"
    assert "measured_vs_billed_mismatch" in r["flags"], "실측값이 있으면 오히려 강한 신호다"


def test_measured_case_does_not_use_the_weaker_flag_name():
    """실측 기준 불일치는 등록치수 기준 추정(size_mismatch_high)과 이름이 달라야 한다.

    둘의 신호 세기가 다르기 때문이다 — 실측 기준은 '등록 치수가 틀렸다'는 설명이 이미 제거됐다.
    """
    r = detect_fee_anomalies(**_LIVE, coupang_size_type="극소형")
    assert "size_mismatch_high" not in r["flags"]


def test_registered_dims_case_keeps_size_mismatch_high():
    """실측 미확보면 종전 이름 그대로 — 기존 계약 불변."""
    r = detect_fee_anomalies(**_LIVE)
    assert r["size_source"] == "registered_dims"
    assert "size_mismatch_high" in r["flags"]
    assert "measured_vs_billed_mismatch" not in r["flags"]


def test_measured_matching_billing_stays_clean():
    """실측과 청구가 정합하면 플래그 없음 — 스킵 제거가 오탐을 만들지 않는지 확인."""
    r = detect_fee_anomalies(**{**_LIVE, "delivery_amount": 2700.0},
                             coupang_size_type="대형1")
    assert r["size_type"] == "대형1"
    assert "measured_vs_billed_mismatch" not in r["flags"]


def test_measured_below_floor_still_wins():
    """하한 미달은 여전히 below_floor로 — 분기 우선순위 불변."""
    r = detect_fee_anomalies(**{**_LIVE, "delivery_amount": 100.0},
                             coupang_size_type="극소형")
    assert "below_floor" in r["flags"]
    assert "measured_vs_billed_mismatch" not in r["flags"]


# ── detect_fee_anomalies_by_period — 주기별 판정 + 미대응 주기 강등(2026-08-03, ⓒ+ⓑ) ──
# 실사고 재현 잠금: 아이패드미니 필름(91313543029)은 정산주기 4개에 각 2,025원이 청구됐는데
# 4월 주문이 DB에 없어(RG 주문 수집 2026-06-03 시작) 감사가 8,100÷2주문=4,050원을 만들고
# '실측 vs 청구 불일치'로 올렸다. 주기별 판정이면 단가는 2,025원이고 플래그는 없어야 한다.

_IPAD_DIMS = (355, 245, 5, 169)  # 극소형(세변합 60.5cm·169g)


def _period(df, dt, dlv, wh, orders, qty):
    return {"date_from": df, "date_to": dt, "delivery": dlv, "warehousing": wh,
            "order_count": orders, "quantity": qty}


def test_by_period_live_false_positive_is_gone():
    """★핵심 회귀: 주문 미수집 주기가 섞여도 단가는 주기 단가(2,025)여야 한다."""
    periods = [
        _period("2026-04-13", "2026-04-19", 2025, 1175, 0, 0),  # 주문 미수집
        _period("2026-04-20", "2026-04-26", 2025, 1175, 0, 0),  # 주문 미수집
        _period("2026-05-11", "2026-05-17", 2025, 1175, 1, 1),
        _period("2026-05-18", "2026-05-24", 2025, 1175, 1, 1),
    ]
    r = detect_fee_anomalies_by_period(*_IPAD_DIMS, periods=periods,
                                       coupang_size_type="극소형")
    assert r["per_unit_delivery"] == 2025
    assert r["per_unit_warehousing"] == 1175
    # 단가의 분자는 판정 주기 청구액(2주기)이지 전 주기 합(8,100)이 아니다.
    assert r["judged_delivery"] == 4050
    # 종전 산술(8100 ÷ 2주문 = 4050)이 되살아나면 이 단정이 깨진다.
    assert r["per_unit_delivery"] != 4050
    assert r["implied_size_delivery"] == "소형"  # 대형1이 아니다
    assert r["flags"] == []
    assert (r["periods_total"], r["periods_judged"], r["periods_unmatched"]) == (4, 2, 2)


def test_by_period_no_matched_period_downgrades_to_unit_unknown():
    """ⓑ: 대응 주기가 하나도 없으면 판정하지 않는다 — 단가를 만들지 않고 보류."""
    periods = [
        _period("2026-04-13", "2026-04-19", 1900, 1100, 0, 0),
        _period("2026-05-11", "2026-05-17", 1900, 1100, 0, 0),
    ]
    r = detect_fee_anomalies_by_period(*_IPAD_DIMS, periods=periods,
                                       coupang_size_type="극소형")
    assert r["flags"] == ["unit_unknown"]
    assert r["per_unit_delivery"] is None
    assert r["judged_delivery"] is None
    assert (r["periods_judged"], r["periods_unmatched"]) == (0, 2)


def test_by_period_gross_anomaly_survives_aggregation():
    """강등이 진짜 신호를 죽이지 않는다: 집계해도 큰 과청구는 플래그로 남는다."""
    periods = [
        _period("2026-05-11", "2026-05-17", 1900, 1100, 1, 1),    # 정상
        _period("2026-05-18", "2026-05-24", 19750, 1100, 1, 1),   # 특대형 정합 = 이상
        _period("2026-05-25", "2026-05-31", 1900, 1100, 0, 0),    # 미대응(제외)
    ]
    r = detect_fee_anomalies_by_period(*_IPAD_DIMS, periods=periods,
                                       coupang_size_type="극소형")
    # 판정은 집계(21,650 ÷ 2주문 = 10,825) 1회로 한다.
    assert r["per_unit_delivery"] == 10825
    assert r["implied_size_delivery"] == "특대형"
    assert "measured_vs_billed_mismatch" in r["flags"]
    # 주기별 상세에 어느 주기가 이상해 보이는지 남는다(집계가 희석해도 사람이 볼 수 있게).
    bad = [p for p in r["period_detail"] if p["flags"]]
    assert len(bad) == 1 and bad[0]["date_from"] == "2026-05-18"
    assert r["periods_flagged"] == 1
    assert [p["judged"] for p in r["period_detail"]] == [True, True, False]


def test_by_period_intra_period_order_undercount_does_not_flag():
    """★라이브 회귀(95570603482): 주기 **안에서** 주문이 부분 수집돼도 플래그를 만들지 않는다.

    같은 옵션이 1,975원/1주문(정상)과 3,950원/1주문(=2주문분 청구·1건만 수집)을 함께 갖는다.
    주기별로 판정하면 후자가 measured_vs_billed_mismatch가 되지만(오탐 3→20건의 원인),
    집계하면 13,825 ÷ 6주문 = 2,304원 = 극소형 최소의 1.7배 → 정상 구간이다.
    """
    periods = [
        _period("2026-06-22", "2026-06-28", 1975, 1125, 1, 1),
        _period("2026-07-01", "2026-07-05", 3950, 2250, 1, 1),   # 분모 결손
        _period("2026-07-06", "2026-07-12", 1975, 1125, 1, 1),
        _period("2026-07-13", "2026-07-19", 3950, 2250, 2, 2),
        _period("2026-07-20", "2026-07-26", 1975, 1125, 1, 1),
    ]
    r = detect_fee_anomalies_by_period(*_IPAD_DIMS, periods=periods,
                                       coupang_size_type="극소형")
    assert r["judged_delivery"] == 13825
    assert r["per_unit_delivery"] == 2304.17
    assert r["flags"] == []
    # 주기 단위로는 이상해 보이는 주기가 있었다는 사실은 남긴다.
    assert r["periods_flagged"] == 1


def test_by_period_missing_dims_short_circuits():
    r = detect_fee_anomalies_by_period(None, 245, 5, 169,
                                       periods=[_period("2026-05-11", "2026-05-17", 1900, 1100, 1, 1)])
    assert r["flags"] == ["missing_dims"]
    assert r["size_type"] is None


def test_by_period_empty_periods_is_unit_unknown_not_crash():
    r = detect_fee_anomalies_by_period(*_IPAD_DIMS, periods=[], coupang_size_type="극소형")
    assert r["flags"] == ["unit_unknown"]
    assert r["periods_total"] == 0
