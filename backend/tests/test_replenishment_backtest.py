# test_replenishment_backtest.py — P4 백테스트 Harness fixture 테스트 (트랙 D-18 게이트②)
# _score_window 경계(품절 A>S / 과잉 (S−A)/rate / base_rate=0) + compute_target_level 공유 oracle
# + run_backtest walk-forward(FLAT 무품절·SPIKE 품절·incomplete skip·insufficient) 손계산 대조.
from datetime import date
from unittest.mock import patch

from app.services.coupang.replenishment_calc import calc_replenishment, compute_target_level
from app.services.coupang.replenishment_backtest import (
    _base_rate_asof,
    _score_window,
    run_backtest,
)
from app.services.coupang.sales_velocity_estimator import SEGMENTS


# ════════════════════════════════════════════════
# compute_target_level — 공유 목표재고 공식 (A1, _calc와 백테스트 단일 진실 원천)
# ════════════════════════════════════════════════
def test_compute_target_level_oracle():
    """base_rate=2, mean=2, p90=3, target=7 → safety=(3−2)*2=2, S=7*2+2=16."""
    s, safety = compute_target_level(2.0, 2.0, 3.0, 7)
    assert s == 16.0
    assert safety == 2.0


def test_compute_target_level_safety_floor():
    """p90<mean(이상치)여도 안전재고는 음수 안 됨(max 0). S=7*1+0=7."""
    s, safety = compute_target_level(1.0, 3.0, 2.0, 7)
    assert safety == 0.0
    assert s == 7.0


def test_calc_uses_shared_target_level():
    """P2-3 회귀: 라이브 _calc(calc_replenishment) 안전재고 = 공유 compute_target_level과 동일.

    추출 시 인자순서/target_days 전달이 틀어지면 잡힘(백테스트↔라이브 공식 분기 방지)."""
    velocity = {
        "base_rate": 2.0,
        "segments": {s: 2.0 for s in SEGMENTS},
        "base_source": "order_item",
        "segment_factors": {s: {"factor": 1.0, "confidence": "ok"} for s in SEGMENTS},
    }
    lead = {"mean": 2.0, "p90": 3.0, "source": "option"}
    res = calc_replenishment(
        object(), "X", current_stock=100,
        velocity=velocity, lead_time=lead, in_transit=None, target_days=7,
    )
    _s, safety = compute_target_level(2.0, 2.0, 3.0, 7)  # (16.0, 2.0)
    assert res["safety_stock"] == round(safety, 1) == 2.0


# ════════════════════════════════════════════════
# _score_window — 품절/과잉 경계
# ════════════════════════════════════════════════
def test_score_window_overstock():
    """A<S → 비품절, 과잉 = (10−6)/2 = 2.0일."""
    w = _score_window(10.0, 2.0, 6)
    assert w["stockout"] is False
    assert w["overstock_days"] == 2.0


def test_score_window_stockout():
    """A>S → 품절, 과잉 = max(0,(10−12)/2)=0."""
    w = _score_window(10.0, 2.0, 12)
    assert w["stockout"] is True
    assert w["overstock_days"] == 0.0


def test_score_window_boundary_covered():
    """A==S → 정확히 덮음 = 비품절(경계 포함성)."""
    w = _score_window(10.0, 2.0, 10)
    assert w["stockout"] is False
    assert w["overstock_days"] == 0.0


def test_score_window_zero_rate_none():
    """base_rate=0 → 과잉일수 정의불가 None(방어)."""
    w = _score_window(10.0, 0.0, 3)
    assert w["overstock_days"] is None


# ════════════════════════════════════════════════
# _base_rate_asof — sold_30d 배제(D-결정-B), order_item 합/달력일수
# ════════════════════════════════════════════════
def test_base_rate_asof_order_item():
    """20판매/20일 → 1.0. sold30 미사용이라도 실판매 있으면 base_source != none."""
    rate, source = _base_rate_asof(20, 20)
    assert rate == 1.0
    assert source in ("order_item", "order_item_low")


def test_base_rate_asof_no_sales_none():
    """판매 0 → none(sold_30d 폴백 없음, 백테스트는 order_item만)."""
    rate, source = _base_rate_asof(0, 10)
    assert source == "none"
    assert rate == 0.0


# ════════════════════════════════════════════════
# run_backtest walk-forward (로더 patch, 손계산 oracle)
# ════════════════════════════════════════════════
# TRUST_START=2026-06-04. today=06-24 → last_complete=06-23, n_days=20 (idx 0..19).
_TODAY = date(2026, 6, 24)
_LEAD = {"global": {"mean": 2.0, "p90": 3.0}}  # lead_days=ceil(3)=3


def _run(series_map, **kw):
    inv = {k: 0 for k in series_map}  # 모집단(판매이력 옵션 포함) — P2-4 분모
    with patch("app.services.coupang.replenishment_backtest.kst_today", return_value=_TODAY), \
         patch("app.services.coupang.replenishment_backtest._load_daily_series", return_value=series_map), \
         patch("app.services.coupang.replenishment_backtest._load_inventory_options", return_value=inv), \
         patch("app.services.coupang.replenishment_backtest.estimate_lead_times", return_value=_LEAD), \
         patch("app.services.coupang.replenishment_backtest._load_names", return_value={}):
        return run_backtest(object(), **kw)


def test_backtest_flat_no_stockout():
    """매일 1개씩 20일. lead_only(H=3), train_min=7 → cutoff idx 6..16 = 11 valid, idx 17~19 incomplete.

    각 윈도우: base_rate=1.0 → S=compute_target_level(1,2,3,7)=7+1=8. 미래수요 A=3(1×3일) < 8 → 무품절,
    과잉=(8−3)/1=5.0. → fill_rate=1.0, mean_overstock=5.0, valid=11, incomplete skip=3."""
    res = _run({"FLAT": [1] * 20}, protection_mode="lead_only", train_min_days=7, review_period=7)
    opt = next(o for o in res["options"] if o["vendor_item_id"] == "FLAT")
    assert opt["valid_windows"] == 11
    assert opt["stockout_windows"] == 0
    assert opt["fill_rate"] == 1.0
    assert opt["mean_overstock_days"] == 5.0
    assert opt["skipped_windows"]["incomplete_window"] == 3


def test_backtest_spike_stockout():
    """[1]×17 + [100,100,100]. lead_only(H=3). cutoff idx 14·15·16의 미래 윈도우가 스파이크를 덮음.

    idx16 future=[17,18,19]=300, idx15=[16,17,18]=201, idx14=[15,16,17]=102 → 모두 A>S(=8) 품절.
    idx 6~13(8개)은 future 전부 1×3=3<8 무품절. valid=11, stockout=3 → fill_rate=1−3/11=0.727."""
    res = _run({"SPIKE": [1] * 17 + [100, 100, 100]},
               protection_mode="lead_only", train_min_days=7, review_period=7)
    opt = next(o for o in res["options"] if o["vendor_item_id"] == "SPIKE")
    assert opt["valid_windows"] == 11
    assert opt["stockout_windows"] == 3
    assert opt["fill_rate"] == 0.727


def test_backtest_summary_indicative():
    """단일 옵션 11 윈도우 ≥ INDICATIVE_MIN_WINDOWS(10) → indicative_only=False. covered=1."""
    res = _run({"FLAT": [1] * 20}, protection_mode="lead_only")
    s = res["summary"]
    assert s["options_attempted"] == 1
    assert s["options_covered"] == 1
    assert s["total_valid_windows"] == 11
    assert s["indicative_only"] is False
    assert s["mean_fill_rate"] == 1.0


def test_backtest_full_mode_few_windows():
    """full 모드 H=ceil(3)+7=10. n_days=20, train_min=7 → idx 6..9만 valid(idx+10<=19 → idx<=9).

    valid=4 < 10 → indicative_only=True(데이터 부족 정직 표기)."""
    res = _run({"FLAT": [1] * 20}, protection_mode="full", train_min_days=7, review_period=7)
    opt = next(o for o in res["options"] if o["vendor_item_id"] == "FLAT")
    assert opt["valid_windows"] == 4
    assert res["summary"]["indicative_only"] is True


def test_backtest_insufficient_data():
    """today가 TRUST_START 직후 → 학습+미래 불가 → 윈도우 0, indicative_only=True."""
    with patch("app.services.coupang.replenishment_backtest.kst_today", return_value=date(2026, 6, 8)), \
         patch("app.services.coupang.replenishment_backtest._load_daily_series", return_value={"X": [1, 1, 1, 1]}), \
         patch("app.services.coupang.replenishment_backtest.estimate_lead_times", return_value=_LEAD), \
         patch("app.services.coupang.replenishment_backtest._load_names", return_value={}):
        res = run_backtest(object(), train_min_days=7)
    assert res["summary"]["total_valid_windows"] == 0
    assert res["summary"]["indicative_only"] is True
    assert res["options"] == []
