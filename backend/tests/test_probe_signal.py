# test_probe_signal.py — probe_signal SA 단위테스트 (D-NAO-58 CD3, D-58-9)
# 순수 계산 SA: naver_ad_daily 창 집계 → 탐침 선행지표 score(즉시구매 + 장바구니×전환율)
# + 보정 ROAS. 쓰기 없음, 실 API 없음.
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily
from app.services.naver_ad import probe_signal
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

CAMPAIGN = "cmp-04"
DAY = date(2026, 7, 15)


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


def _row(db, *, campaign_type="WEB_SITE", adgroup_id="grp-1", keyword_id="nkw-1",
         ad_date=DAY, imp=10, clk=5, cost=1000,
         conv_direct_cnt=0, conv_indirect_cnt=0, conv_direct_amt=0, conv_indirect_amt=0,
         cart_direct_cnt=0, cart_indirect_cnt=0, campaign_id=CAMPAIGN):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id=keyword_id, imp=imp, clk=clk, cost=cost,
        conv_direct_cnt=conv_direct_cnt, conv_indirect_cnt=conv_indirect_cnt,
        conv_direct_amt=conv_direct_amt, conv_indirect_amt=conv_indirect_amt,
        cart_direct_cnt=cart_direct_cnt, cart_indirect_cnt=cart_indirect_cnt,
    ))
    db.commit()


def test_probe_signal_immediate_and_carts_and_adjusted_score(db):
    _row(db, keyword_id="nkw-1", clk=3, cost=1000,
         conv_direct_cnt=1, conv_indirect_cnt=2, cart_direct_cnt=4, cart_indirect_cnt=6,
         conv_direct_amt=3000, conv_indirect_amt=1000)
    sig = probe_signal.probe_signal_score(
        db, grain="keyword", target_id="nkw-1", campaign_id=CAMPAIGN,
        date_from=DAY, date_to=DAY, cart_rate=0.5, correction_factor_value=1.0,
    )
    assert sig["immediate"] == 3          # 1 + 2
    assert sig["carts"] == 10             # 4 + 6
    assert sig["cart_rate"] == 0.5
    assert sig["adjusted_score"] == 3 + 10 * 0.5  # 8.0
    assert sig["cost"] == 1000
    assert sig["clk"] == 3
    assert sig["conv_amt"] == 4000        # 3000 + 1000
    assert sig["roas_corrected"] == round((4000 / 1000) * 1.0, 4)  # 4.0


def test_probe_signal_roas_none_when_cost_zero(db):
    _row(db, keyword_id="nkw-z", clk=0, cost=0, conv_direct_amt=0, conv_indirect_amt=0)
    sig = probe_signal.probe_signal_score(
        db, grain="keyword", target_id="nkw-z", campaign_id=CAMPAIGN,
        date_from=DAY, date_to=DAY, cart_rate=0.3, correction_factor_value=1.5,
    )
    assert sig["cost"] == 0
    assert sig["roas_corrected"] is None


def test_probe_signal_correction_factor_applied(db):
    _row(db, keyword_id="nkw-cf", clk=2, cost=2000, conv_direct_amt=4000)
    sig = probe_signal.probe_signal_score(
        db, grain="keyword", target_id="nkw-cf", campaign_id=CAMPAIGN,
        date_from=DAY, date_to=DAY, cart_rate=0.0, correction_factor_value=1.25,
    )
    # (4000/2000) * 1.25 = 2.5
    assert sig["roas_corrected"] == 2.5


def test_probe_signal_keyword_grain_requires_web_site(db):
    # 같은 keyword_id지만 SHOPPING 행은 keyword grain 집계에서 제외(campaign_type=WEB_SITE만)
    _row(db, keyword_id="nkw-mix", campaign_type="WEB_SITE", clk=3, cost=100, conv_direct_cnt=1)
    _row(db, keyword_id="nkw-mix", campaign_type="SHOPPING", adgroup_id="grp-2", clk=9,
         cost=900, conv_direct_cnt=5)
    sig = probe_signal.probe_signal_score(
        db, grain="keyword", target_id="nkw-mix", campaign_id=CAMPAIGN,
        date_from=DAY, date_to=DAY, cart_rate=0.0, correction_factor_value=1.0,
    )
    assert sig["clk"] == 3            # SHOPPING 행 제외
    assert sig["cost"] == 100
    assert sig["immediate"] == 1


def test_probe_signal_adgroup_grain(db):
    _row(db, adgroup_id="grp-a", keyword_id="", campaign_type="SHOPPING", clk=7, cost=700,
         conv_direct_cnt=2, cart_direct_cnt=3)
    sig = probe_signal.probe_signal_score(
        db, grain="adgroup", target_id="grp-a", campaign_id=CAMPAIGN,
        date_from=DAY, date_to=DAY, cart_rate=1.0, correction_factor_value=1.0,
    )
    assert sig["clk"] == 7
    assert sig["immediate"] == 2
    assert sig["carts"] == 3
    assert sig["adjusted_score"] == 2 + 3 * 1.0


def test_probe_signal_excludes_backfill_sentinel(db):
    _row(db, adgroup_id="grp-b", keyword_id="", campaign_type="SHOPPING", clk=4, cost=400,
         conv_direct_cnt=1)
    # 센티넬 롤업 행은 이중계상 방지 위해 제외 — adgroup grain으로 조회 시 안 잡혀야 정상.
    _row(db, adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="", campaign_type="SHOPPING",
         clk=999, cost=99999, conv_direct_cnt=50)
    sig = probe_signal.probe_signal_score(
        db, grain="adgroup", target_id="grp-b", campaign_id=CAMPAIGN,
        date_from=DAY, date_to=DAY, cart_rate=0.0, correction_factor_value=1.0,
    )
    assert sig["clk"] == 4
    assert sig["cost"] == 400


def test_probe_signal_campaign_grain(db):
    _row(db, keyword_id="nkw-c1", clk=2, cost=200, conv_direct_cnt=1)
    _row(db, adgroup_id="grp-c", keyword_id="", campaign_type="SHOPPING", clk=3, cost=300,
         conv_direct_cnt=1)
    sig = probe_signal.probe_signal_score(
        db, grain="campaign", target_id=CAMPAIGN, campaign_id=CAMPAIGN,
        date_from=DAY, date_to=DAY, cart_rate=0.0, correction_factor_value=1.0,
    )
    assert sig["clk"] == 5       # 캠페인 전체 합산(WEB_SITE + SHOPPING)
    assert sig["cost"] == 500
