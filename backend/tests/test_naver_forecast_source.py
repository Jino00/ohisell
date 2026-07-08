# test_naver_forecast_source.py — 예측·전문가 스프린트 F2a forecast_source(SA) 단위테스트
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily
from app.services.naver_ad import forecast_source
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

TODAY = date(2026, 7, 8)
YESTERDAY = TODAY - timedelta(days=1)
CAMPAIGN = "cmp-1"
ADGROUP = "adg-1"


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


def _row(db, *, ad_date, campaign_id=CAMPAIGN, adgroup_id, keyword_id="", clk=100, cost=1000, conv_amt=0):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type="WEB_SITE",
        adgroup_id=adgroup_id, keyword_id=keyword_id,
        imp=clk * 10, clk=clk, cost=cost, rank_sum=0,
        conv_direct_cnt=0, conv_indirect_cnt=1 if conv_amt else 0,
        conv_direct_amt=0, conv_indirect_amt=conv_amt,
    ))


def test_daily_series_campaign_reads_sentinel_rows(db):
    _row(db, ad_date=YESTERDAY, adgroup_id=BACKFILL_SENTINEL_ADGROUP, clk=100, cost=1000)
    db.commit()

    series = forecast_source.daily_series(db, grain="campaign", scope_key=CAMPAIGN, date_from=YESTERDAY, date_to=YESTERDAY)

    assert series[YESTERDAY] == {"clk": 100, "cost": 1000, "conv_amt": 0}


def test_daily_series_adgroup_aggregates_keyword_rows_same_date(db):
    """같은 날짜에 키워드별로 흩어진 P0 실단위 행을 adgroup 하나로 합산해야 한다."""
    _row(db, ad_date=YESTERDAY, adgroup_id=ADGROUP, keyword_id="nkw-1", clk=30, cost=300)
    _row(db, ad_date=YESTERDAY, adgroup_id=ADGROUP, keyword_id="nkw-2", clk=70, cost=700)
    db.commit()

    series = forecast_source.daily_series(db, grain="adgroup", scope_key=ADGROUP, date_from=YESTERDAY, date_to=YESTERDAY)

    assert series[YESTERDAY] == {"clk": 100, "cost": 1000, "conv_amt": 0}


def test_daily_series_adgroup_excludes_other_adgroups(db):
    _row(db, ad_date=YESTERDAY, adgroup_id=ADGROUP, keyword_id="nkw-1", clk=30, cost=300)
    _row(db, ad_date=YESTERDAY, adgroup_id="adg-other", keyword_id="nkw-9", clk=999, cost=9999)
    db.commit()

    series = forecast_source.daily_series(db, grain="adgroup", scope_key=ADGROUP, date_from=YESTERDAY, date_to=YESTERDAY)

    assert series[YESTERDAY] == {"clk": 30, "cost": 300, "conv_amt": 0}


def test_daily_series_keyword_reads_individual_rows(db):
    _row(db, ad_date=YESTERDAY, adgroup_id=ADGROUP, keyword_id="nkw-1", clk=30, cost=300, conv_amt=500)
    _row(db, ad_date=YESTERDAY, adgroup_id=ADGROUP, keyword_id="nkw-2", clk=70, cost=700)
    db.commit()

    series = forecast_source.daily_series(db, grain="keyword", scope_key="nkw-1", date_from=YESTERDAY, date_to=YESTERDAY)

    assert series[YESTERDAY] == {"clk": 30, "cost": 300, "conv_amt": 500}


def test_daily_series_rejects_unsupported_grain(db):
    with pytest.raises(ValueError):
        forecast_source.daily_series(db, grain="account", scope_key="acc-1", date_from=YESTERDAY, date_to=YESTERDAY)


def test_daily_series_rejects_empty_scope_key(db):
    """codex review(F2a): scope_key=''는 어떤 grain에서도 진짜 스코프가 아니라
    SHOPPING/BRAND_SEARCH keyword_id sentinel과 충돌할 수 있어 명시적으로 막는다."""
    with pytest.raises(ValueError):
        forecast_source.daily_series(db, grain="keyword", scope_key="", date_from=YESTERDAY, date_to=YESTERDAY)


def test_daily_series_keyword_sums_duplicate_rows_same_date(db):
    """같은 (date, keyword_id) 조합이 여러 캠페인/그룹에 걸쳐 있어도 마지막 값이 아니라 합산해야 한다."""
    _row(db, ad_date=YESTERDAY, campaign_id="cmp-1", adgroup_id=ADGROUP, keyword_id="nkw-shared", clk=10, cost=100)
    _row(db, ad_date=YESTERDAY, campaign_id="cmp-2", adgroup_id="adg-2", keyword_id="nkw-shared", clk=20, cost=200)
    db.commit()

    series = forecast_source.daily_series(db, grain="keyword", scope_key="nkw-shared", date_from=YESTERDAY, date_to=YESTERDAY)

    assert series[YESTERDAY] == {"clk": 30, "cost": 300, "conv_amt": 0}


def test_active_days_counts_dates_with_positive_cost(db):
    _row(db, ad_date=TODAY - timedelta(days=3), adgroup_id=BACKFILL_SENTINEL_ADGROUP, cost=1000)
    _row(db, ad_date=TODAY - timedelta(days=2), adgroup_id=BACKFILL_SENTINEL_ADGROUP, cost=0)
    _row(db, ad_date=TODAY - timedelta(days=1), adgroup_id=BACKFILL_SENTINEL_ADGROUP, cost=1000)
    db.commit()

    n = forecast_source.active_days(db, grain="campaign", scope_key=CAMPAIGN, date_from=TODAY - timedelta(days=3), date_to=YESTERDAY)

    assert n == 2


def test_active_days_adgroup_aggregates_before_counting(db):
    """부분 행마다 cost가 0이어도 합산 결과가 양수면 활동일로 센다."""
    _row(db, ad_date=YESTERDAY, adgroup_id=ADGROUP, keyword_id="nkw-1", cost=0)
    _row(db, ad_date=YESTERDAY, adgroup_id=ADGROUP, keyword_id="nkw-2", cost=500)
    db.commit()

    n = forecast_source.active_days(db, grain="adgroup", scope_key=ADGROUP, date_from=YESTERDAY, date_to=YESTERDAY)

    assert n == 1
