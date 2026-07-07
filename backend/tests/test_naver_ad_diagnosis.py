# test_naver_ad_diagnosis.py — 네이버 SA 광고 최적화 트랙 P2-S2(진단 엔진) 단위 테스트
# 커버: account_diagnosis 보드 6개(출혈·굶는승자·확장버킷·쇼핑그룹BEP·제외후보·3단분류·악순환)
#   + diagnosis harness 조립(보정계수·계정 BEP/목표ROAS 없을 때 폴백).
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverEntity, NaverProductBep, NaverSearchTermDaily, Order
from app.services.naver_ad import account_diagnosis as diag
from app.services.naver_ad.diagnosis import build_diagnosis

D0 = date(2026, 7, 1)
D_TO = date(2026, 7, 15)  # 15일 창(실측 베이스라인과 동일)


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


def _row(db, ad_date, campaign_id, campaign_type, adgroup_id, keyword_id, imp, clk, cost, direct=0, indirect=0):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id=keyword_id,
        imp=imp, clk=clk, cost=cost, rank_sum=imp * 3,
        conv_direct_cnt=1 if direct else 0, conv_indirect_cnt=1 if indirect else 0,
        conv_direct_amt=direct, conv_indirect_amt=indirect,
    ))


# ── bleeding_keywords ──
def test_bleeding_keywords_below_bep_sorted_by_cost(db):
    # 키워드A: cost 10000, conv_amt 5000 → roas 0.5 (BEP 2.0 미만 = 출혈)
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 100, 10, 10000, direct=5000)
    # 키워드B: cost 5000, conv_amt 20000 → roas 4.0 (BEP 이상 = 정상)
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-2", 100, 10, 5000, direct=20000)
    db.commit()

    out = diag.bleeding_keywords(db, D0, D0, bep_roas=Decimal("2.0"), correction_factor=Decimal("1"))
    assert len(out) == 1
    assert out[0]["keyword_id"] == "nkw-1"
    assert out[0]["roas_corrected"] == 0.5


def test_bleeding_keywords_excludes_expansion_bucket(db):
    # keyword_id='' (확장버킷) — 출혈이어도 이 보드 대상 아님
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "", 100, 10, 10000, direct=1000)
    db.commit()
    out = diag.bleeding_keywords(db, D0, D0, bep_roas=Decimal("2.0"), correction_factor=Decimal("1"))
    assert out == []


def test_bleeding_keywords_applies_correction_factor(db):
    # roas_naver=1.0(BEP=2.0 미만) 이지만 보정계수 0.3 적용 시 더 낮아짐(여전히 출혈)
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 100, 10, 10000, direct=10000)
    db.commit()
    out = diag.bleeding_keywords(db, D0, D0, bep_roas=Decimal("2.0"), correction_factor=Decimal("0.3"))
    assert len(out) == 1
    assert out[0]["roas_corrected"] == pytest.approx(0.3)


# ── starving_winners ──
def test_starving_winners_high_roas_low_clicks(db):
    days = (D_TO - D0).days + 1  # 15일
    # 15일간 총 클릭 10 → 일평균 0.67 (<1), roas 매우 높음
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 50, 10, 5000, direct=50000)
    db.commit()
    out = diag.starving_winners(db, D0, D_TO, target_roas=Decimal("2.75"), correction_factor=Decimal("1"))
    assert len(out) == 1
    assert out[0]["keyword_id"] == "nkw-1"
    assert out[0]["avg_daily_clk"] < 1.0


def test_starving_winners_excludes_high_click_winner(db):
    days = (D_TO - D0).days + 1
    # 일평균 클릭 30 이상(>=1) — 굶는 상태 아님, 이미 충분히 노출됨
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 1000, 30 * days, 50000, direct=500000)
    db.commit()
    out = diag.starving_winners(db, D0, D_TO, target_roas=Decimal("2.75"), correction_factor=Decimal("1"))
    assert out == []


# ── expansion_bucket ──
def test_expansion_bucket_cost_share(db):
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 100, 10, 6000, direct=6000)  # 등록 키워드
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "", 100, 10, 4000, direct=4000)       # 확장버킷
    db.commit()
    out = diag.expansion_bucket(db, D0, D0, correction_factor=Decimal("1"))
    assert out["cost"] == 4000
    assert out["web_site_total_cost"] == 10000
    assert out["cost_share"] == 0.4


# ── shopping_group_bep ──
def test_shopping_group_bep_flags_underperforming_group(db):
    # SHOPPING은 keyword_id='' — 그룹 단위 집계
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-a", "", 100, 10, 8000, direct=8000)   # roas 1.0 < bep 2.0
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-b", "", 100, 10, 8000, direct=40000)  # roas 5.0 >= bep
    db.commit()
    out = diag.shopping_group_bep(db, D0, D0, bep_roas=Decimal("2.0"), correction_factor=Decimal("1"))
    assert len(out) == 1
    assert out[0]["adgroup_id"] == "grp-a"


# ── exclusion_candidates ──
def test_exclusion_candidates_sorted_by_cost(db):
    db.add(NaverSearchTermDaily(
        ad_date=D0, campaign_id="cmp1", adgroup_id="grp1", search_term="싼키워드",
        source="expkeyword", imp=100, clk=5, cost=1000, rank_sum=300,
    ))
    db.add(NaverSearchTermDaily(
        ad_date=D0, campaign_id="cmp1", adgroup_id="grp1", search_term="비싼키워드",
        source="expkeyword", imp=100, clk=5, cost=9000, rank_sum=300,
    ))
    db.commit()
    out = diag.exclusion_candidates(db, D0, D0, limit=10)
    assert out[0]["search_term"] == "비싼키워드"
    assert out[0]["cost"] == 9000


# ── keyword_triage ──
def test_keyword_triage_three_buckets(db):
    # 판정가능: 최근 30일 클릭 10 이상
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-judge", campaign_id="cmp1",
                        campaign_type="WEB_SITE", status="on", name="판정가능키워드"))
    _row(db, D_TO - timedelta(days=5), "cmp1", "WEB_SITE", "grp1", "nkw-judge", 500, 15, 5000, direct=10000)
    # 육성후보: 저클릭 + 월검색량>0
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-grow", campaign_id="cmp1",
                        campaign_type="WEB_SITE", status="on", name="육성후보키워드", monthly_volume=500))
    # 진짜정리: 저클릭 + 월검색량 0
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-dead", campaign_id="cmp1",
                        campaign_type="WEB_SITE", status="on", name="죽은키워드", monthly_volume=0))
    db.commit()

    out = diag.keyword_triage(db, as_of=D_TO)
    assert out["total"] == 3
    assert out["judgeable"] == 1
    assert out["growth_candidate"] == 1
    assert out["dead"] == 1


# ── vicious_cycle_flags ──
def test_vicious_cycle_detects_declining_thinning_campaign(db):
    # 이전기간(D_TO-29 ~ D_TO-7, 23일): 클릭 230(일평균10), roas 3.0(양호)
    prior_start = D_TO - timedelta(days=29)
    _row(db, prior_start, "cmp1", "WEB_SITE", "grp1", "nkw-1", 2300, 230, 100000, direct=300000)
    # 최근기간(D_TO-6 ~ D_TO, 7일): 클릭 14(일평균2, 하락) 그리고 roas 1.0(하락+목표미달)
    recent_start = D_TO - timedelta(days=6)
    _row(db, recent_start, "cmp1", "WEB_SITE", "grp1", "nkw-1", 140, 14, 10000, direct=10000)
    db.commit()

    out = diag.vicious_cycle_flags(db, D_TO, target_roas=Decimal("2.75"), correction_factor=Decimal("1"))
    assert len(out) == 1
    assert out[0]["campaign_id"] == "cmp1"


def test_vicious_cycle_ignores_stable_campaign(db):
    prior_start = D_TO - timedelta(days=29)
    _row(db, prior_start, "cmp1", "WEB_SITE", "grp1", "nkw-1", 2300, 230, 100000, direct=300000)
    recent_start = D_TO - timedelta(days=6)
    _row(db, recent_start, "cmp1", "WEB_SITE", "grp1", "nkw-1", 700, 70, 30000, direct=90000)
    db.commit()

    out = diag.vicious_cycle_flags(db, D_TO, target_roas=Decimal("2.75"), correction_factor=Decimal("1"))
    assert out == []


# ── diagnosis harness ──
def test_build_diagnosis_errors_gracefully_without_bep_data(db):
    result = build_diagnosis(db, D0, D_TO)
    assert result["boards"] is None
    assert "error" in result


def test_correction_factor_aligns_window_to_short_real_data_history(db):
    # 파이프라인 가동 초기 시나리오: naver_ad_daily 실단위 데이터는 최근 3일치만 존재하는데
    # 주문(매출)은 30일 내내 있음 — 계수는 반드시 겹치는 3일 창만 비교해야 함(30일 대 3일 왜곡 방지).
    db.add(NaverProductBep(
        channel_id=6, channel_product_id="cp-1", product_name="테스트상품",
        selling_price=Decimal("10000"), cost_price=Decimal("5000"),
        commission_rate=Decimal("0.05"), logistics_cost=Decimal("1000"),
        contribution_margin=Decimal("3000"), bep_roas=Decimal("3.3333"),
        aggressiveness="standard", target_roas=Decimal("3.8333"), has_cost=True,
    ))
    # 30일 내내 매일 10000원 주문 발생(총 300000원) — 그중 최근 3일만 naver_ad_daily 실단위 존재
    for i in range(30):
        db.add(Order(channel_id=6, platform_product_id="cp-1", order_number=f"ORD-{i}",
                      order_date=D_TO - timedelta(days=i), status="정상", selling_price=Decimal("10000")))
    for i in range(3):
        _row(db, D_TO - timedelta(days=i), "cmp1", "WEB_SITE", "grp1", "nkw-1", 100, 10, 1000, direct=1000)
    db.commit()

    result = build_diagnosis(db, D0, D_TO)
    cf = result["correction_factor"]
    # 3일 창(최근 3일 매출=30000)만 비교 — 30일 매출(300000)을 3일 convAmt(3000)로 나누면 안 됨
    assert cf["window_revenue"] == 30000
    assert cf["window_conv_amt"] == 3000
    assert cf["factor"] == pytest.approx(10.0)


def test_correction_factor_unavailable_when_no_real_data(db):
    out = diag.earliest_real_data_date(db, D_TO, lookback_days=30)
    assert out is None


def test_build_diagnosis_assembles_all_boards(db):
    db.add(NaverProductBep(
        channel_id=6, channel_product_id="cp-1", product_name="테스트상품",
        selling_price=Decimal("10000"), cost_price=Decimal("5000"),
        commission_rate=Decimal("0.05"), logistics_cost=Decimal("1000"),
        contribution_margin=Decimal("3000"), bep_roas=Decimal("3.3333"),
        aggressiveness="standard", target_roas=Decimal("3.8333"), has_cost=True,
    ))
    db.add(Order(channel_id=6, platform_product_id="cp-1", order_number="ORD-1",
                  order_date=D_TO, status="정상", selling_price=Decimal("10000")))
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 100, 10, 10000, direct=1000)
    db.commit()

    result = build_diagnosis(db, D0, D_TO)
    assert result["boards"] is not None
    assert set(result["boards"]) == {
        "bleeding_keywords", "starving_winners", "expansion_bucket",
        "shopping_group_bep", "exclusion_candidates", "keyword_triage", "vicious_cycle",
    }
    assert result["account_bep_roas"] == pytest.approx(3.3333)
