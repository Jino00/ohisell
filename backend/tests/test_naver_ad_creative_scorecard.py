# test_naver_ad_creative_scorecard.py — D-NAO-140 S2: 소재별 성과 조회
#
# 이 화면의 존재 이유: 캠페인 평균이 적자 소재를 가린다. 2026-08-03 실측으로 캠페인 03의
# ROAS는 2.07~3.26인데 그 안의 소재 하나는 0.61이었다(3일 10.4만원 써서 6.4만원).
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdCreativeDaily, NaverAdgroupProduct, NaverEntity, NaverProductBep
from app.services.naver_ad import creative_scorecard

D1 = date(2026, 8, 1)
D3 = date(2026, 8, 3)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield s
    finally:
        s.close()


def _perf(db, ad_id, day, *, cost, rev, imp=100, clk=5, conv=1, grp="grp-1", cmp="cmp-1"):
    db.add(NaverAdCreativeDaily(ad_date=day, ad_id=ad_id, campaign_id=cmp, adgroup_id=grp,
                                imp=imp, clk=clk, cost=cost, conv_direct_cnt=conv,
                                conv_direct_amt=rev))
    db.commit()


def _mapping(db, ad_id, product, *, bep=None, bid=1000, grp="grp-1", name="상품A"):
    db.add(NaverAdgroupProduct(adgroup_id=grp, campaign_id="cmp-1", mall_product_id=product,
                               product_name=name, ad_id=ad_id, ad_bid_amt=bid))
    if bep is not None:
        db.add(NaverProductBep(channel_id=creative_scorecard.NAVER_CHANNEL_ID,
                               channel_product_id=product, bep_roas=bep))
    db.commit()


def _build(db, **kw):
    return creative_scorecard.build(db, since=D1, until=D3, **kw)


def test_sums_across_days_not_single_day(db):
    """★구간 합산이 기본 — 소재당 전환이 하루 0~3건이라 하루치 ROAS는 노이즈가 신호보다 크다."""
    _perf(db, "nad-A", D1, cost=100, rev=0, conv=0)
    _perf(db, "nad-A", D3, cost=300, rev=800, conv=2)
    row = _build(db)["rows"][0]
    assert (row["cost"], row["rev"], row["days"]) == (400, 800, 2)
    assert row["roas"] == 2.0


def test_verdict_is_three_state_and_never_guesses(db):
    """★BEP를 모르면 판정하지 않는다 — 모르는 걸 '미달'로 적으면 매핑 결손이 적자로 둔갑한다."""
    _perf(db, "nad-below", D1, cost=1000, rev=500)
    _perf(db, "nad-above", D1, cost=1000, rev=3000)
    _perf(db, "nad-nobep", D1, cost=1000, rev=100)
    _mapping(db, "nad-below", "P1", bep=1.5)
    _mapping(db, "nad-above", "P2", bep=1.5, grp="grp-2")
    _mapping(db, "nad-nobep", "P3", bep=None, grp="grp-3")  # BEP 행 없음
    by = {r["ad_id"]: r for r in _build(db)["rows"]}
    assert by["nad-below"]["verdict"] == "below" and by["nad-below"]["bep_gap"] == -1.0
    assert by["nad-above"]["verdict"] == "above" and by["nad-above"]["bep_gap"] == 1.5
    assert by["nad-nobep"]["verdict"] is None and by["nad-nobep"]["bep_roas"] is None


def test_unmapped_ad_still_shows_performance(db):
    """상품 매핑이 없어도 성과는 보여준다 — 안 보이면 그 광고비가 존재하지 않는 것처럼 읽힌다."""
    _perf(db, "nad-orphan", D1, cost=5000, rev=0)
    row = _build(db)["rows"][0]
    assert row["cost"] == 5000 and row["product_name"] is None and row["verdict"] is None


def test_zero_cost_ad_has_no_roas(db):
    """비용 0이면 ROAS는 정의되지 않는다 — 0으로 적으면 '수익이 0'으로 읽힌다."""
    _perf(db, "nad-free", D1, cost=0, rev=0, conv=0)
    assert _build(db)["rows"][0]["roas"] is None


def test_totals_ignore_paging(db):
    """★합계는 페이지와 무관하다 — 한 페이지만 보고 '이게 전부'로 읽지 않게."""
    for i in range(3):
        _perf(db, f"nad-{i}", D1, cost=1000 * (i + 1), rev=0)
    res = _build(db, limit=1)
    assert len(res["rows"]) == 1 and res["total"] == 3
    assert res["totals"] == {"ads": 3, "cost": 6000, "rev": 0, "clk": 15}
    assert res["rows"][0]["ad_id"] == "nad-2"  # 기본 정렬 = 광고비 내림차순


def test_latest_mapping_wins_for_moved_ad(db):
    """★같은 소재가 그룹을 옮기면 매핑 행이 둘 남는다(누적 테이블). 실행 경로와 **같은 규칙**으로
    최신 관측을 채택한다 — 조회 화면과 실행이 서로 다른 상품을 가리키면 판단이 어긋난다."""
    _perf(db, "nad-A", D1, cost=100, rev=200)
    _mapping(db, "nad-A", "OLD", bep=1.5, grp="grp-old", name="옛 상품")
    _mapping(db, "nad-A", "NEW", bep=2.0, grp="grp-new", name="새 상품")
    row = _build(db)["rows"][0]
    assert row["mall_product_id"] == "NEW" and row["bep_roas"] == 2.0


def test_campaign_filter_and_names(db):
    _perf(db, "nad-A", D1, cost=100, rev=0, cmp="cmp-1")
    _perf(db, "nad-B", D1, cost=200, rev=0, cmp="cmp-2", grp="grp-2")
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp-1", name="03. 아이폰_강화유리"))
    db.commit()
    res = _build(db, campaign_id="cmp-1")
    assert res["total"] == 1
    assert res["rows"][0]["campaign_name"] == "03. 아이폰_강화유리"


def test_window_excludes_outside_days(db):
    _perf(db, "nad-A", D1 - timedelta(days=1), cost=999, rev=0)
    _perf(db, "nad-A", D1, cost=100, rev=0)
    assert _build(db)["rows"][0]["cost"] == 100
