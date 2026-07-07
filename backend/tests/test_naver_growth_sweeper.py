# test_naver_growth_sweeper.py — 듀얼모드 스프린트 Phase 2 growth_sweeper(SA) 단위테스트
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverEntity
from app.services.naver_ad import growth_sweeper
from app.services.naver_ad.account_diagnosis import LOW_CLICK_THRESHOLD

AS_OF = date(2026, 7, 6)
D_FROM = AS_OF - timedelta(days=14)


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


def _entity(**overrides):
    row = {"entity_type": "keyword", "entity_id": "nkw-1", "parent_id": "grp-1",
           "campaign_id": "cmp-1", "campaign_type": "WEB_SITE", "name": "테스트키워드",
           "status": "on", "bid_amt": 100}
    row.update(overrides)
    return NaverEntity(**row)


def _ad_row(db, keyword_id, campaign_id="cmp-1", adgroup_id="grp-1", clk=0, conv_amt=0):
    db.add(NaverAdDaily(
        ad_date=AS_OF, campaign_id=campaign_id, campaign_type="WEB_SITE",
        adgroup_id=adgroup_id, keyword_id=keyword_id,
        imp=clk * 10, clk=clk, cost=clk * 50, rank_sum=clk * 3,
        conv_direct_cnt=1 if conv_amt else 0, conv_direct_amt=conv_amt,
    ))


# account RPC(=conv_amt/clk)를 키워드 raw RPC와 동일(1000)하게 맞춰, 계층 베이지안 수축이
# prior와 관측값 사이에서 값을 바꾸지 않도록 함(테스트 기대값을 단순 산식으로 검증하기 위함).
_AGG_RICH_ACCOUNT = {"group": {}, "campaign": {}, "account": {"clk": 1000, "conv_amt": 1_000_000}}


def _target_roas_for(_campaign_id: str) -> Decimal:
    return Decimal("2")


def test_excludes_keyword_without_bid_amt(db):
    db.add(_entity(bid_amt=None))
    db.commit()
    out = growth_sweeper.find_growth_candidates(
        db, D_FROM, AS_OF, correction_factor=Decimal("1"), agg=_AGG_RICH_ACCOUNT,
        target_roas_for=_target_roas_for,
    )
    assert out == []


def test_excludes_non_web_site_and_off_status(db):
    db.add(_entity(entity_id="nkw-shop", campaign_type="SHOPPING"))
    db.add(_entity(entity_id="nkw-off", status="off"))
    db.commit()
    out = growth_sweeper.find_growth_candidates(
        db, D_FROM, AS_OF, correction_factor=Decimal("1"), agg=_AGG_RICH_ACCOUNT,
        target_roas_for=_target_roas_for,
    )
    assert out == []


def test_bootstrap_account_with_no_clicks_returns_empty(db):
    """D-NAO-9 모수 게이트 — 계정 전체에 클릭이 사실상 없으면(부트스트랩) 풀링 신뢰도가
    없어 스윕 자체를 건너뛴다."""
    db.add(_entity())
    db.commit()
    empty_agg = {"group": {}, "campaign": {}, "account": {"clk": LOW_CLICK_THRESHOLD - 1, "conv_amt": 0}}
    out = growth_sweeper.find_growth_candidates(
        db, D_FROM, AS_OF, correction_factor=Decimal("1"), agg=empty_agg,
        target_roas_for=_target_roas_for,
    )
    assert out == []


def test_gap_positive_candidate_included_with_expected_fields(db):
    db.add(_entity(entity_id="nkw-1", bid_amt=100))
    _ad_row(db, "nkw-1", clk=100, conv_amt=100_000)  # RPC=1000, target_roas=2 → ceiling=500
    db.commit()

    out = growth_sweeper.find_growth_candidates(
        db, D_FROM, AS_OF, correction_factor=Decimal("1"), agg=_AGG_RICH_ACCOUNT,
        target_roas_for=_target_roas_for,
    )
    assert len(out) == 1
    c = out[0]
    assert c["keyword_id"] == "nkw-1"
    assert c["current_bid"] == 100
    assert c["economic_ceiling"] == 500
    assert c["gap"] == 400
    assert c["clk"] == 100
    assert c["sample_thin"] is False


def test_no_gap_when_current_bid_already_above_ceiling(db):
    db.add(_entity(entity_id="nkw-1", bid_amt=1000))  # 이미 상한(500) 이상 — 확장 대상 아님
    _ad_row(db, "nkw-1", clk=100, conv_amt=100_000)
    db.commit()

    out = growth_sweeper.find_growth_candidates(
        db, D_FROM, AS_OF, correction_factor=Decimal("1"), agg=_AGG_RICH_ACCOUNT,
        target_roas_for=_target_roas_for,
    )
    assert out == []


def test_zero_ceiling_excluded_no_revenue_signal(db):
    db.add(_entity(entity_id="nkw-1", bid_amt=100))
    # 클릭은 있지만 전환매출 0 → RPC=0 → ceiling=0(수익 근거 없음, negative_keyword 소관)
    _ad_row(db, "nkw-1", clk=100, conv_amt=0)
    db.commit()

    out = growth_sweeper.find_growth_candidates(
        db, D_FROM, AS_OF, correction_factor=Decimal("1"), agg=_AGG_RICH_ACCOUNT,
        target_roas_for=_target_roas_for,
    )
    assert out == []


def test_candidates_sorted_by_gap_descending(db):
    db.add(_entity(entity_id="nkw-small-gap", bid_amt=400))
    db.add(_entity(entity_id="nkw-big-gap", bid_amt=10))
    _ad_row(db, "nkw-small-gap", clk=100, conv_amt=100_000)  # ceiling=500, gap=100
    _ad_row(db, "nkw-big-gap", clk=100, conv_amt=100_000)    # ceiling=500, gap=490
    db.commit()

    out = growth_sweeper.find_growth_candidates(
        db, D_FROM, AS_OF, correction_factor=Decimal("1"), agg=_AGG_RICH_ACCOUNT,
        target_roas_for=_target_roas_for,
    )
    assert [c["keyword_id"] for c in out] == ["nkw-big-gap", "nkw-small-gap"]


def test_sample_thin_flag_set_below_low_click_threshold(db):
    db.add(_entity(entity_id="nkw-thin", bid_amt=10))
    _ad_row(db, "nkw-thin", clk=LOW_CLICK_THRESHOLD - 1, conv_amt=5000)
    db.commit()

    out = growth_sweeper.find_growth_candidates(
        db, D_FROM, AS_OF, correction_factor=Decimal("1"), agg=_AGG_RICH_ACCOUNT,
        target_roas_for=_target_roas_for,
    )
    assert len(out) == 1
    assert out[0]["sample_thin"] is True


def test_correction_factor_scales_ceiling(db):
    db.add(_entity(entity_id="nkw-1", bid_amt=100))
    _ad_row(db, "nkw-1", clk=100, conv_amt=100_000)  # raw RPC=1000
    db.commit()

    out = growth_sweeper.find_growth_candidates(
        db, D_FROM, AS_OF, correction_factor=Decimal("0.5"), agg=_AGG_RICH_ACCOUNT,
        target_roas_for=_target_roas_for,
    )
    # 보정RPC=500, target_roas=2 → ceiling=250
    assert out[0]["economic_ceiling"] == 250
    assert out[0]["gap"] == 150
