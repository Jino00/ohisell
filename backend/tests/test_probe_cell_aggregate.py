# test_probe_cell_aggregate.py — probe_cell_aggregate SA 단위테스트 (D-NAO-58 CD4)
# 순수 계산 SA: naver_keyword_hourly/naver_ad_daily 창 집계 → 환경 셀×순위 밴드 클릭곡선.
# 쓰기 없음, 실 API 없음.
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverKeywordHourly
from app.services.naver_ad import probe_cell_aggregate as pca
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

WEEKDAY = date(2026, 7, 15)   # 수요일, 공휴일 아님
WEEKEND = date(2026, 7, 18)   # 토요일
HOLIDAY = date(2026, 1, 1)    # 신정(공휴일, 목요일 — day_class는 요일 무관 holiday 우선)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _hour_row(db, *, ad_date, hour=10, avg_rank=Decimal("3.0"), imp=50, clk=5, cost=500,
              conv_cnt=0, entity_id="nkw-1", campaign_id="cmp-1", adgroup_id="grp-1",
              entity_type="keyword", campaign_type="WEB_SITE"):
    db.add(NaverKeywordHourly(
        ad_date=ad_date, hour=hour, entity_type=entity_type, entity_id=entity_id,
        adgroup_id=adgroup_id, campaign_id=campaign_id, campaign_type=campaign_type,
        imp=imp, clk=clk, cost=cost, avg_rank=avg_rank, conv_cnt=conv_cnt,
    ))
    db.commit()


def _ad_daily_row(db, *, ad_date, campaign_id="cmp-1", adgroup_id="grp-1", keyword_id="nkw-1",
                   campaign_type="WEB_SITE", conv_direct_cnt=0, conv_indirect_cnt=0,
                   cart_direct_cnt=0, cart_indirect_cnt=0):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id=keyword_id, imp=10, clk=1, cost=100,
        conv_direct_cnt=conv_direct_cnt, conv_indirect_cnt=conv_indirect_cnt,
        cart_direct_cnt=cart_direct_cnt, cart_indirect_cnt=cart_indirect_cnt,
    ))
    db.commit()


# ══════════════════════════ env_cell_of_date ══════════════════════════

def test_env_cell_weekday():
    assert pca.env_cell_of_date(WEEKDAY) == "weekday"


def test_env_cell_weekend():
    assert pca.env_cell_of_date(WEEKEND) == "weekend"


def test_env_cell_holiday_overrides_weekday_label():
    assert pca.env_cell_of_date(HOLIDAY) == "holiday"


def test_env_cell_multi_axes_joins_with_pipe():
    label = pca.env_cell_of_date(WEEKDAY, axes=("day_class", "iphone_window", "season"))
    parts = label.split("|")
    assert len(parts) == 3
    assert parts[0] == "weekday"
    assert parts[2] == "summer"  # 7월


# ══════════════════════════ rank_band_of ══════════════════════════

def test_rank_band_boundaries():
    assert pca.rank_band_of(Decimal("2.0")) == "2.0-2.5"
    assert pca.rank_band_of(Decimal("1.99")) == "1.0-2.0"
    assert pca.rank_band_of(Decimal("2.5")) == "2.5-3.0"
    assert pca.rank_band_of(Decimal("4.0")) == "4.0+"
    assert pca.rank_band_of(None) is None


def test_rank_band_accepts_float():
    assert pca.rank_band_of(3.2) == "3.0-4.0"


# ══════════════════════════ aggregate_cells ══════════════════════════

def test_aggregate_cells_empty_data_returns_empty_dict(db):
    result = pca.aggregate_cells(db, as_of=WEEKDAY)
    assert result["cells"] == {}


def test_aggregate_cells_buckets_by_cell_and_band(db):
    # 밴드 2.0-2.5 (imp 충분) — 고 CTR
    _hour_row(db, ad_date=WEEKDAY, hour=9, avg_rank=Decimal("2.2"), imp=100, clk=20, cost=1000)
    _hour_row(db, ad_date=WEEKDAY - timedelta(days=1),
              hour=9, avg_rank=Decimal("2.2"), imp=100, clk=20, cost=1000)
    # 밴드 3.0-4.0 (imp 충분) — 저 CTR
    _hour_row(db, ad_date=WEEKDAY, hour=10, avg_rank=Decimal("3.5"), imp=100, clk=2, cost=1000)
    _hour_row(db, ad_date=WEEKDAY - timedelta(days=2),
              hour=10, avg_rank=Decimal("3.5"), imp=100, clk=2, cost=1000)
    # 밴드 4.0+ 저노출(imp<30) — optimal_band 후보에서 제외돼야 함
    _hour_row(db, ad_date=WEEKDAY, hour=11, avg_rank=Decimal("5.0"), imp=5, clk=5, cost=50)

    result = pca.aggregate_cells(db, as_of=WEEKDAY, window_days=30)
    cell = result["cells"]["weekday"]

    assert cell["imp"] == 100 + 100 + 100 + 100 + 5
    assert cell["clk"] == 20 + 20 + 2 + 2 + 5
    assert cell["days"] == 3  # WEEKDAY, WEEKDAY-1, WEEKDAY-2 서로 다른 3일
    assert set(cell["bands"].keys()) == {"2.0-2.5", "3.0-4.0", "4.0+"}
    assert cell["bands"]["4.0+"]["imp"] == 5  # 저노출 밴드도 집계는 되지만
    # optimal_band는 imp≥30인 밴드 중 ctr_shrunk argmax(2.0-2.5가 3.0-4.0보다 CTR 높음)
    assert cell["optimal_band"] == "2.0-2.5"


def test_aggregate_cells_ctr_shrunk_between_raw_and_cell_prior(db):
    # 밴드(imp=30, clk=15 → raw ctr=0.5)와 나머지 셀(imp=1000,clk=10 → 전체 낮은 ctr)로
    # 셀 prior가 밴드 raw보다 훨씬 낮게 만들어 ctr_shrunk가 그 사이에 위치하는지 확인.
    _hour_row(db, ad_date=WEEKDAY, hour=1, avg_rank=Decimal("2.2"), imp=30, clk=15, cost=300)
    _hour_row(db, ad_date=WEEKDAY, hour=2, avg_rank=Decimal("3.5"), imp=1000, clk=10, cost=1000)

    result = pca.aggregate_cells(db, as_of=WEEKDAY, window_days=1)
    cell = result["cells"]["weekday"]
    band = cell["bands"]["2.0-2.5"]
    raw = band["ctr_raw"]
    shrunk = band["ctr_shrunk"]
    cell_prior = cell["ctr"]
    assert min(raw, cell_prior) <= shrunk <= max(raw, cell_prior)
    assert shrunk != raw  # shrink가 실제로 prior 방향으로 당김


def test_aggregate_cells_excludes_none_avg_rank(db):
    _hour_row(db, ad_date=WEEKDAY, hour=1, avg_rank=None, imp=1000, clk=500, cost=1000)
    result = pca.aggregate_cells(db, as_of=WEEKDAY, window_days=1)
    assert result["cells"] == {}


def test_aggregate_cells_optimal_band_none_when_ctr_below_signal_min(db):
    # imp 충분하지만 ctr_shrunk가 _CTR_SIGNAL_MIN(0.01) 미만이면 승격 신호 없음
    _hour_row(db, ad_date=WEEKDAY, hour=1, avg_rank=Decimal("2.2"), imp=1000, clk=1, cost=1000)
    result = pca.aggregate_cells(db, as_of=WEEKDAY, window_days=1)
    assert result["cells"]["weekday"]["optimal_band"] is None


def test_aggregate_cells_campaign_filter(db):
    _hour_row(db, ad_date=WEEKDAY, hour=1, avg_rank=Decimal("2.2"), imp=100, clk=10,
              campaign_id="cmp-a")
    _hour_row(db, ad_date=WEEKDAY, hour=2, avg_rank=Decimal("2.2"), imp=999, clk=1,
              campaign_id="cmp-b")
    result = pca.aggregate_cells(db, as_of=WEEKDAY, window_days=1, campaign_id="cmp-a")
    assert result["cells"]["weekday"]["imp"] == 100


# ══════════════════════════ cell_leading_indicator ══════════════════════════

def test_cell_leading_indicator_sums_immediate_and_carts(db):
    _ad_daily_row(db, ad_date=WEEKDAY, conv_direct_cnt=1, conv_indirect_cnt=2,
                  cart_direct_cnt=3, cart_indirect_cnt=4)
    _ad_daily_row(db, ad_date=WEEKDAY, keyword_id="nkw-2", conv_direct_cnt=1,
                  cart_direct_cnt=1)
    result = pca.cell_leading_indicator(db, as_of=WEEKDAY, window_days=1)
    assert result["weekday"]["immediate"] == 1 + 2 + 1
    assert result["weekday"]["carts"] == 3 + 4 + 1


def test_cell_leading_indicator_excludes_backfill_sentinel(db):
    _ad_daily_row(db, ad_date=WEEKDAY, conv_direct_cnt=1, cart_direct_cnt=1)
    _ad_daily_row(db, ad_date=WEEKDAY, adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="",
                  conv_direct_cnt=999, cart_direct_cnt=999)
    result = pca.cell_leading_indicator(db, as_of=WEEKDAY, window_days=1)
    assert result["weekday"]["immediate"] == 1
    assert result["weekday"]["carts"] == 1


def test_cell_leading_indicator_empty_returns_empty_dict(db):
    result = pca.cell_leading_indicator(db, as_of=WEEKDAY, window_days=1)
    assert result == {}


# ══════════════════════════ RL5(CD5) 이익 가중 승격 — conv_cnt 우선 ══════════════════════════
# D-NAO-60 RL5 Part B: 전환 신호(conv_cnt) 있으면 전환 최다 밴드 우선, 없으면(백필기간) 기존
# CTR argmax 폴백. optimal_band 라벨 규약은 하위호환(learned_probe_rank/_is_promotable 불변).

def test_optimal_band_prefers_conv_cnt_over_ctr_when_conversions_present(db):
    # 밴드 2.0-2.5: CTR 높지만 전환 0 / 밴드 3.0-4.0: CTR 낮지만 전환 있음 → conv 밴드가 이겨야 함
    _hour_row(db, ad_date=WEEKDAY, hour=9, avg_rank=Decimal("2.2"), imp=100, clk=20, cost=1000,
              conv_cnt=0)
    _hour_row(db, ad_date=WEEKDAY - timedelta(days=1),
              hour=9, avg_rank=Decimal("2.2"), imp=100, clk=20, cost=1000, conv_cnt=0)
    _hour_row(db, ad_date=WEEKDAY, hour=10, avg_rank=Decimal("3.5"), imp=100, clk=2, cost=1000,
              conv_cnt=3)
    _hour_row(db, ad_date=WEEKDAY - timedelta(days=2),
              hour=10, avg_rank=Decimal("3.5"), imp=100, clk=2, cost=1000, conv_cnt=2)

    result = pca.aggregate_cells(db, as_of=WEEKDAY, window_days=30)
    cell = result["cells"]["weekday"]
    assert cell["bands"]["2.0-2.5"]["conv_cnt"] == 0
    assert cell["bands"]["3.0-4.0"]["conv_cnt"] == 5
    assert cell["optimal_band"] == "3.0-4.0"  # CTR argmax였다면 2.0-2.5가 이겼을 것
    assert cell["optimal_basis"] == "conv"


def test_optimal_band_falls_back_to_ctr_when_no_conversions(db):
    # 기존 동작(conv_cnt=0 전부) — CTR argmax(2.0-2.5) 그대로 유지되고 basis="ctr"
    _hour_row(db, ad_date=WEEKDAY, hour=9, avg_rank=Decimal("2.2"), imp=100, clk=20, cost=1000)
    _hour_row(db, ad_date=WEEKDAY - timedelta(days=1),
              hour=9, avg_rank=Decimal("2.2"), imp=100, clk=20, cost=1000)
    _hour_row(db, ad_date=WEEKDAY, hour=10, avg_rank=Decimal("3.5"), imp=100, clk=2, cost=1000)
    _hour_row(db, ad_date=WEEKDAY - timedelta(days=2),
              hour=10, avg_rank=Decimal("3.5"), imp=100, clk=2, cost=1000)

    result = pca.aggregate_cells(db, as_of=WEEKDAY, window_days=30)
    cell = result["cells"]["weekday"]
    assert cell["optimal_band"] == "2.0-2.5"
    assert cell["optimal_basis"] == "ctr"


def test_optimal_band_conv_tie_break_by_ctr_shrunk(db):
    # 두 밴드 conv_cnt 동률(3) → ctr_shrunk 높은 쪽이 이겨야 함(2.0-2.5는 CTR 훨씬 높음)
    _hour_row(db, ad_date=WEEKDAY, hour=9, avg_rank=Decimal("2.2"), imp=100, clk=20, cost=1000,
              conv_cnt=3)
    _hour_row(db, ad_date=WEEKDAY - timedelta(days=1),
              hour=9, avg_rank=Decimal("2.2"), imp=100, clk=20, cost=1000, conv_cnt=0)
    _hour_row(db, ad_date=WEEKDAY, hour=10, avg_rank=Decimal("3.5"), imp=100, clk=2, cost=1000,
              conv_cnt=3)
    _hour_row(db, ad_date=WEEKDAY - timedelta(days=2),
              hour=10, avg_rank=Decimal("3.5"), imp=100, clk=2, cost=1000, conv_cnt=0)

    result = pca.aggregate_cells(db, as_of=WEEKDAY, window_days=30)
    cell = result["cells"]["weekday"]
    assert cell["bands"]["2.0-2.5"]["conv_cnt"] == cell["bands"]["3.0-4.0"]["conv_cnt"] == 3
    assert cell["optimal_band"] == "2.0-2.5"
    assert cell["optimal_basis"] == "conv"


def test_optimal_band_conv_candidate_below_ctr_signal_min_falls_back(db):
    # 유일한 전환 밴드가 ctr_shrunk < _CTR_SIGNAL_MIN(0.01)이면 conv 후보에서 제외되고
    # 나머지 CTR argmax로 폴백(전환 있다고 무조건 승격시키지 않음).
    _hour_row(db, ad_date=WEEKDAY, hour=9, avg_rank=Decimal("2.2"), imp=100, clk=20, cost=1000,
              conv_cnt=0)
    _hour_row(db, ad_date=WEEKDAY - timedelta(days=1),
              hour=9, avg_rank=Decimal("2.2"), imp=100, clk=20, cost=1000, conv_cnt=0)
    # 전환은 있지만 클릭 자체가 거의 없어 ctr_shrunk가 신호하한 미달
    _hour_row(db, ad_date=WEEKDAY, hour=10, avg_rank=Decimal("3.5"), imp=1000, clk=1, cost=1000,
              conv_cnt=1)
    _hour_row(db, ad_date=WEEKDAY - timedelta(days=2),
              hour=10, avg_rank=Decimal("3.5"), imp=1000, clk=0, cost=1000, conv_cnt=0)

    result = pca.aggregate_cells(db, as_of=WEEKDAY, window_days=30)
    cell = result["cells"]["weekday"]
    assert cell["bands"]["3.0-4.0"]["ctr_shrunk"] < Decimal("0.01")
    assert cell["optimal_band"] == "2.0-2.5"  # CTR 폴백
    assert cell["optimal_basis"] == "ctr"


def test_optimal_band_none_when_no_candidates_conv_or_ctr(db):
    _hour_row(db, ad_date=WEEKDAY, hour=1, avg_rank=Decimal("2.2"), imp=1000, clk=1, cost=1000)
    result = pca.aggregate_cells(db, as_of=WEEKDAY, window_days=1)
    cell = result["cells"]["weekday"]
    assert cell["optimal_band"] is None
    assert cell["optimal_basis"] == "ctr"


# ══════════════════════════ rank_band_upper ══════════════════════════

def test_rank_band_upper_returns_hi_for_bounded_band():
    assert pca.rank_band_upper("2.0-2.5") == Decimal("2.5")
    assert pca.rank_band_upper("1.0-2.0") == Decimal("2.0")
    assert pca.rank_band_upper("2.5-3.0") == Decimal("3.0")
    assert pca.rank_band_upper("3.0-4.0") == Decimal("4.0")


def test_rank_band_upper_none_for_open_ended_band():
    assert pca.rank_band_upper("4.0+") is None


def test_rank_band_upper_unknown_label_raises():
    with pytest.raises(ValueError):
        pca.rank_band_upper("not-a-band")
