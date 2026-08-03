# test_naver_ad_adgroup_product_freshness.py — 매핑 신선도 필터 계약 (codex 3R P1)
#
# ★배경: 2026-07-31 전량 삭제 사고 이후 `shopping_ad_product_sync`는 아무것도 지우지 않는
#   누적 upsert가 됐다. 그래서 `naver_adgroup_product`는 "현재 매핑"이 아니라 **역대 관측의
#   합집합**인데 소비자들은 여전히 현재값으로 읽고 있었고, 그 오염이 target ROAS·프록시 매출·
#   예산 증액·콜드 입찰 문맥까지 갔다. 삭제로 되돌리는 대신 **읽는 시점에 배제**한다.
#
# ★이 파일이 고정하는 두 방향:
#   ①필터를 **거는 곳** — stale이 결과를 오염시키지 않는다.
#   ②필터를 **일부러 안 거는 곳** — 거는 순간 더 위험해지는 자리가 있다(분모·hold 게이트·
#     스톱로스 창·자동제외 보호목록). 그 비대칭이 사고가 아니라 설계임을 여기서 못박는다.
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdgroupProduct, NaverCampaignSettings, NaverEntity, NaverProductBep, Order,
)
from app.services.naver_ad import (
    adgroup_product_freshness as freshness,
    campaign_target_resolver, cold_start_bid_lane, effective_bid, product_campaign_share,
    today_proxy_revenue,
)
from app.utils.kst import kst_now


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


def _mapping(db, *, adgroup_id, campaign_id, product_id, age_days=0, ad_id=None,
             bid=None, use_group=False):
    """age_days만큼 오래된 관측 1행."""
    db.add(NaverAdgroupProduct(
        adgroup_id=adgroup_id, campaign_id=campaign_id, mall_product_id=product_id,
        product_name=f"상품{product_id}", ad_id=ad_id, ad_bid_amt=bid,
        use_group_bid_amt=use_group,
        synced_at=kst_now() - timedelta(days=age_days),
    ))


STALE = freshness.FRESHNESS_WINDOW_DAYS + 1   # 확실히 창 밖
FRESH = 0                                     # 방금 관측


# ══════════════════════════════════════════════════════════════════
# ① 헬퍼 자체
# ══════════════════════════════════════════════════════════════════
def test_fresh_condition_excludes_only_rows_outside_window(db):
    _mapping(db, adgroup_id="g1", campaign_id="c1", product_id="p-fresh", age_days=FRESH)
    _mapping(db, adgroup_id="g1", campaign_id="c1", product_id="p-edge",
             age_days=freshness.FRESHNESS_WINDOW_DAYS - 1)
    _mapping(db, adgroup_id="g1", campaign_id="c1", product_id="p-stale", age_days=STALE)
    db.commit()

    got = {r.mall_product_id for r in freshness.fresh_rows(db).all()}
    assert got == {"p-fresh", "p-edge"}


def test_fresh_rows_by_ad_id_prefers_most_recent_observation(db):
    """같은 ad_id가 옛 그룹·새 그룹에 공존하면 **최신 관측**을 채택한다."""
    _mapping(db, adgroup_id="g-old", campaign_id="c-old", product_id="p1",
             ad_id="nad-1", age_days=3)
    _mapping(db, adgroup_id="g-new", campaign_id="c-new", product_id="p1",
             ad_id="nad-1", age_days=0)
    db.commit()

    picked = freshness.fresh_rows_by_ad_id(db, ["nad-1"])
    assert picked["nad-1"].adgroup_id == "g-new"
    assert picked["nad-1"].campaign_id == "c-new"


def test_fresh_rows_by_ad_id_warns_when_both_sides_are_fresh(db, caplog):
    """★양쪽 다 신선한 중복은 진짜 이상 상태 — 조용히 넘기지 않는다."""
    _mapping(db, adgroup_id="g-a", campaign_id="c-a", product_id="p1", ad_id="nad-1", age_days=0)
    _mapping(db, adgroup_id="g-b", campaign_id="c-b", product_id="p1", ad_id="nad-1", age_days=1)
    db.commit()

    with caplog.at_level(logging.WARNING, logger=freshness.__name__):
        freshness.fresh_rows_by_ad_id(db, ["nad-1"])

    assert any("양쪽 다 신선한" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]


def test_fresh_rows_by_ad_id_is_silent_when_only_one_is_fresh(db, caplog):
    """옛 행이 창 밖이면 중복이 아니다 — 경보를 내지 않는다(소음 방지)."""
    _mapping(db, adgroup_id="g-old", campaign_id="c-old", product_id="p1",
             ad_id="nad-1", age_days=STALE)
    _mapping(db, adgroup_id="g-new", campaign_id="c-new", product_id="p1",
             ad_id="nad-1", age_days=0)
    db.commit()

    with caplog.at_level(logging.WARNING, logger=freshness.__name__):
        picked = freshness.fresh_rows_by_ad_id(db, ["nad-1"])

    assert picked["nad-1"].adgroup_id == "g-new"
    assert [r.getMessage() for r in caplog.records] == []


# ══════════════════════════════════════════════════════════════════
# ② 필터를 **거는** 소비자 — stale이 결과를 오염시키지 않는다
# ══════════════════════════════════════════════════════════════════
def test_target_resolver_ignores_stale_product(db):
    """★stale 상품이 target ROAS 가중평균을 오염시키지 않는다.

    옛 상품(BEP 500)이 남아 있으면 필터 없이는 평균이 끌려간다. 현재 상품(BEP 150)만 반영돼야
    그 값이 auto_operator·실행 직전 harness에 정직하게 전달된다.
    """
    _mapping(db, adgroup_id="g1", campaign_id="c1", product_id="p-now", age_days=FRESH)
    _mapping(db, adgroup_id="g1", campaign_id="c1", product_id="p-gone", age_days=STALE)
    db.add_all([
        NaverProductBep(channel_id=6, channel_product_id="p-now", has_cost=True,
                        target_roas=Decimal("150")),
        NaverProductBep(channel_id=6, channel_product_id="p-gone", has_cost=True,
                        target_roas=Decimal("500")),
    ])
    db.commit()

    r = campaign_target_resolver.resolve_adgroup_target_roas(db, "g1")
    assert r["source"] == "product_bep"
    assert r["target_roas"] == Decimal("150"), "stale 상품이 섞이면 325(단순평균)가 된다"


def test_product_moved_between_campaigns_is_counted_once(db):
    """★A→B 이동 시나리오: 프록시 매출이 두 캠페인으로 갈라지지 않는다.

    상품 P가 캠페인 A→B로 옮겨가면 A의 옛 행과 B의 새 행이 공존한다. 매출 귀속(분자)은
    **현재 유효한 매핑만** 봐야 하므로, A는 P를 자기 상품으로 주장하지 못한다.
    """
    _mapping(db, adgroup_id="g-a", campaign_id="c-a", product_id="P", age_days=STALE)
    _mapping(db, adgroup_id="g-b", campaign_id="c-b", product_id="P", age_days=FRESH)
    db.commit()

    by_campaign = today_proxy_revenue.product_ids_by_campaign(db, ["c-a", "c-b"])
    assert by_campaign.get("c-b") == ["P"]
    assert "c-a" not in by_campaign, "떠난 캠페인이 아직 그 상품을 주장하면 매출이 양쪽에 잡힌다"


def test_cold_start_lane_does_not_use_stale_context(db):
    """★stale 문맥으로 라이브 ad_id에 입찰 후보를 만들지 않는다."""
    db.add(NaverCampaignSettings(campaign_id="c-old", optimizer="ours", auto_operate=True))
    db.add(NaverCampaignSettings(campaign_id="c-new", optimizer="ours", auto_operate=True))
    # 같은 소재가 옛 캠페인 문맥으로도 남아 있다(누적 upsert라 지워지지 않는다)
    _mapping(db, adgroup_id="g-old", campaign_id="c-old", product_id="P",
             ad_id="nad-1", bid=800, age_days=STALE)
    _mapping(db, adgroup_id="g-new", campaign_id="c-new", product_id="P",
             ad_id="nad-1", bid=1600, age_days=FRESH)
    db.commit()

    cold = cold_start_bid_lane.select_cold_ads(db, date(2026, 8, 3))
    contexts = {(c["campaign_id"], c["adgroup_id"]) for c in cold}
    assert ("c-old", "g-old") not in contexts, "옛 문맥으로 라이브 소재에 입찰하려 한다"


def test_target_resolver_falls_back_conservatively_when_all_stale(db):
    """★필터 후 0행이면 폴백 — 숫자를 지어내지 않고 계정 기본값으로 내려간다(기존 동작).

    이 폴백은 매핑이 애초에 0행일 때 이미 일어나던 경로다(새 위험이 아님).
    """
    _mapping(db, adgroup_id="g1", campaign_id="c1", product_id="p-gone", age_days=STALE)
    db.add_all([
        NaverProductBep(channel_id=6, channel_product_id="p-gone", has_cost=True,
                        target_roas=Decimal("500")),
        NaverProductBep(channel_id=6, channel_product_id="acc", has_cost=True,
                        target_roas=Decimal("222")),
        Order(channel_id=6, platform_product_id="acc", selling_price=Decimal("1000"),
              quantity=1, order_date=date(2026, 8, 1), order_number="o1"),
    ])
    db.commit()

    r = campaign_target_resolver.resolve_target_roas(db, "c1")
    assert r["source"] == "account_default"
    assert r["target_roas"] == Decimal("222")


def test_today_proxy_revenue_reports_unknown_not_zero_when_all_stale(db):
    """★폴백 방향 확인: 매출을 0원으로 접지 않고 '모름'으로 남긴다(증액 근거가 되지 않음)."""
    _mapping(db, adgroup_id="g1", campaign_id="c1", product_id="p-gone", age_days=STALE)
    db.commit()

    out = today_proxy_revenue.build(db, ["c1"], date(2026, 8, 3))
    assert out["c1"]["revenue"] is None
    assert out["c1"]["reason"]


# ══════════════════════════════════════════════════════════════════
# ③ 필터를 **일부러 안 거는** 자리 — 걸면 더 위험해진다
#
# 2026-08-03 폴백 방향 전수 조사 결과. 이 비대칭은 사고가 아니라 설계다.
# ══════════════════════════════════════════════════════════════════
def test_denominator_is_not_freshness_filtered(db):
    """★★분모(campaigns_per_product)에는 필터를 걸지 않는다.

    호출부가 `shares.get(pid, 1)`로 읽으므로 행이 빠지면 분모가 1로 떨어지고, 같은 매출이
    여러 캠페인에 100%씩 계상돼 **예산 증액이 부당 통과**한다. 분자에만 걸고 분모에는 안 거는
    비대칭이 보수 방향(프록시 ROAS 과소 → 증액이 덜 열림)이다.
    """
    _mapping(db, adgroup_id="g-a", campaign_id="c-a", product_id="P", age_days=STALE)
    _mapping(db, adgroup_id="g-b", campaign_id="c-b", product_id="P", age_days=FRESH)
    db.commit()

    assert product_campaign_share.campaigns_per_product(db, ["P"]) == {"P": 2}, (
        "분모에 신선도 필터가 걸리면 1이 되고, 그 순간 매출이 양쪽에 100%씩 계상된다"
    )


def test_effective_bid_still_sees_stale_ad_level_bids(db):
    """★★effective_bid에는 필터를 걸지 않는다.

    걸면 `source`가 영영 'group'이 되어 auto_operator의 "[레버 미연결]" hold 게이트와
    account_diagnosis의 만성 7일 스톱로스 창이 **동시에 꺼진다**(held되던 입찰이 집행됨).
    """
    _mapping(db, adgroup_id="g1", campaign_id="c1", product_id="P",
             ad_id="nad-1", bid=1600, use_group=False, age_days=STALE)
    db.commit()

    out = effective_bid.adgroup_effective_bids(db, {"g1": 500})
    assert out["g1"]["source"] == "ad", "오래된 관측이라도 '소재 입찰이 실효'라는 사실은 유효하다"
    assert out["g1"]["effective_bid"] == 1600
