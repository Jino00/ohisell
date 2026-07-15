# test_naver_ad_diagnosis.py — 네이버 SA 광고 최적화 트랙 P2-S2(진단 엔진) 단위 테스트
# 커버: account_diagnosis 보드 6개(출혈·굶는승자·확장버킷·쇼핑그룹BEP·제외후보·3단분류·악순환)
#   + diagnosis harness 조립(보정계수·계정 BEP/목표ROAS 없을 때 폴백).
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverChangeLog, NaverEntity, NaverProductBep, NaverSearchTermDaily, Order
from app.services.naver_ad import account_diagnosis as diag
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
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
        "shopping_group_bep", "shopping_group_growth", "exclusion_candidates", "keyword_triage",
        "vicious_cycle",
        "pause_candidates", "resume_candidates",
        "shopping_pause_candidates", "shopping_resume_candidates",
    }
    assert result["account_bep_roas"] == pytest.approx(3.3333)


# ── pause_candidates (X1b T3, D-NAO-38) ──


def _entity(db, entity_type, entity_id, *, status="on", bid_amt=None, campaign_id="cmp1",
            parent_id="grp1", campaign_type="WEB_SITE"):
    db.add(NaverEntity(
        entity_type=entity_type, entity_id=entity_id, parent_id=parent_id,
        campaign_id=campaign_id, campaign_type=campaign_type, name=entity_id,
        status=status, bid_amt=bid_amt,
    ))


def _seed_active_parents(db, *, campaign_id="cmp1", adgroup_id="grp1"):
    """부모 체인(campaign→adgroup)이 status='on'인 정상 동기화 상태 — pause_candidates가
    이제 이 체인을 요구하므로(codex P2, F2a와 동일 근거) 대부분 테스트가 이 시드를 쓴다."""
    db.add(NaverEntity(entity_type="campaign", entity_id=campaign_id, parent_id="",
                        campaign_id=campaign_id, campaign_type="WEB_SITE", name=campaign_id, status="on"))
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
                        campaign_id=campaign_id, campaign_type="WEB_SITE", name=adgroup_id, status="on"))


def test_pause_candidates_zero_conversion_over_stop_loss(db):
    # bid_amt=200 → 스톱로스 절대액 = 200*10(LOW_CLICK_THRESHOLD) = 2,000원. 무전환 누적비용 2,500원.
    _seed_active_parents(db)
    _entity(db, "keyword", "nkw-1", status="on", bid_amt=200)
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 500, 25, 2500, direct=0, indirect=0)
    db.commit()

    out = diag.pause_candidates(db, D0, D0)
    assert len(out) == 1
    assert out[0]["keyword_id"] == "nkw-1"
    assert out[0]["current_bid"] == 200
    assert out[0]["stop_loss_amount"] == 2000


def test_pause_candidates_excludes_converting_keyword(db):
    _seed_active_parents(db)
    _entity(db, "keyword", "nkw-1", status="on", bid_amt=200)
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 500, 25, 2500, direct=3000)  # 전환 있음
    db.commit()

    out = diag.pause_candidates(db, D0, D0)
    assert out == []


def test_pause_candidates_excludes_cost_below_stop_loss(db):
    _seed_active_parents(db)
    _entity(db, "keyword", "nkw-1", status="on", bid_amt=200)
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 200, 5, 1000, direct=0)  # 1,000 < 2,000
    db.commit()

    out = diag.pause_candidates(db, D0, D0)
    assert out == []


def test_pause_candidates_excludes_already_off(db):
    _seed_active_parents(db)
    _entity(db, "keyword", "nkw-1", status="off", bid_amt=200)  # 이미 정지됨
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 500, 25, 2500, direct=0)
    db.commit()

    out = diag.pause_candidates(db, D0, D0)
    assert out == []


def test_pause_candidates_excludes_missing_entity(db):
    # NaverEntity 행 자체가 없음(bid_amt 미확보) — fail-closed 스킵
    _seed_active_parents(db)
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 500, 25, 2500, direct=0)
    db.commit()

    out = diag.pause_candidates(db, D0, D0)
    assert out == []


def test_pause_candidates_excludes_keyword_under_paused_adgroup(db):
    """[codex P2] entity_sync는 부모-자식 status를 캐스케이드하지 않는다(F2a와 동일 근거,
    D-NAO-27) — 광고그룹이 off인데 키워드 자체는 status='on'으로 남을 수 있다. 이 상태에서
    pause 제안을 실행하면 키워드에 별도 userLock이 걸려, 나중에 광고그룹만 재개해도
    키워드는 계속 잠긴 채로 남는다(의도치 않은 영구 정지)."""
    db.add(NaverEntity(entity_type="adgroup", entity_id="grp1", parent_id="cmp1",
                        campaign_id="cmp1", campaign_type="WEB_SITE", name="grp1", status="off"))
    _entity(db, "keyword", "nkw-1", status="on", bid_amt=200, parent_id="grp1")
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 500, 25, 2500, direct=0)
    db.commit()

    out = diag.pause_candidates(db, D0, D0)
    assert out == []


def test_pause_candidates_excludes_keyword_under_paused_campaign(db):
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp1", parent_id="",
                        campaign_id="cmp1", campaign_type="WEB_SITE", name="cmp1", status="off"))
    db.add(NaverEntity(entity_type="adgroup", entity_id="grp1", parent_id="cmp1",
                        campaign_id="cmp1", campaign_type="WEB_SITE", name="grp1", status="on"))
    _entity(db, "keyword", "nkw-1", status="on", bid_amt=200, parent_id="grp1")
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 500, 25, 2500, direct=0)
    db.commit()

    out = diag.pause_candidates(db, D0, D0)
    assert out == []


def test_pause_candidates_includes_keyword_when_parent_chain_all_on(db):
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp1", parent_id="",
                        campaign_id="cmp1", campaign_type="WEB_SITE", name="cmp1", status="on"))
    db.add(NaverEntity(entity_type="adgroup", entity_id="grp1", parent_id="cmp1",
                        campaign_id="cmp1", campaign_type="WEB_SITE", name="grp1", status="on"))
    _entity(db, "keyword", "nkw-1", status="on", bid_amt=200, parent_id="grp1")
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 500, 25, 2500, direct=0)
    db.commit()

    out = diag.pause_candidates(db, D0, D0)
    assert len(out) == 1
    assert out[0]["keyword_id"] == "nkw-1"


def test_pause_candidates_sorted_by_cost_desc(db):
    _seed_active_parents(db)
    db.add(NaverEntity(entity_type="adgroup", entity_id="grp2", parent_id="cmp1",
                        campaign_id="cmp1", campaign_type="WEB_SITE", name="grp2", status="on"))
    _entity(db, "keyword", "nkw-1", status="on", bid_amt=200)
    _entity(db, "keyword", "nkw-2", status="on", bid_amt=200, parent_id="grp2")
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 500, 25, 2000, direct=0)
    _row(db, D0, "cmp1", "WEB_SITE", "grp2", "nkw-2", 800, 40, 3000, direct=0)
    db.commit()

    out = diag.pause_candidates(db, D0, D0)
    assert [r["keyword_id"] for r in out] == ["nkw-2", "nkw-1"]


# ── resume_candidates (X1b T3, D-NAO-38) ──
# codex[P2, X1b T4]: naver_execution_harness가 pause/resume 둘 다 change_log에
# action="set_user_lock"으로 기록한다(_ACTION_BY_PROPOSAL_TYPE이 두 proposal_type을 하나의
# 실행 액션으로 묶음 — update_bid가 bid_up/bid_down/growth_bid_up을 묶는 것과 동일 관례).
# 정지/재개 방향은 action 문자열이 아니라 after_value(userLock 실제 결과값)로 판별해야 한다.


def _lock_log(entity_id, campaign_id, *, locked, proposal_id, changed_at, entity_type="keyword"):
    # dry_run=False + after_value 존재 = 실제 성공한 쓰기(outcome은 D+14 채점 전 NULL이
    # 정상 — proposal_scoreboard 배선, X1b T5 Claude 적대적 리뷰 수정 참조).
    return NaverChangeLog(
        entity_type=entity_type, entity_id=entity_id, campaign_id=campaign_id, action="set_user_lock",
        proposal_id=proposal_id, dry_run=False, changed_at=changed_at,
        after_value=json.dumps({"userLock": locked}),
    )


def test_resume_candidates_recovered_roas_since_our_pause(db):
    _entity(db, "keyword", "nkw-off-1", status="off", bid_amt=190)
    # 정지 직전 30일 창(D_TO-30 ~ D_TO-1)에 양호한 실적(보정ROAS 5.0 ≥ 목표 2.0)
    pre_pause_date = D_TO - timedelta(days=5)
    _row(db, pre_pause_date, "cmp1", "WEB_SITE", "grp1", "nkw-off-1", 100, 10, 2000, direct=10000)
    db.add(_lock_log("nkw-off-1", "cmp1", locked=True, proposal_id=1,
                      changed_at=datetime.combine(D_TO, datetime.min.time())))
    db.commit()

    out = diag.resume_candidates(db, D_TO, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"))
    assert len(out) == 1
    assert out[0]["keyword_id"] == "nkw-off-1"
    assert out[0]["roas_at_pause"] == 5.0


def test_resume_candidates_excludes_manual_pause_no_proposal_id(db):
    _entity(db, "keyword", "nkw-off-1", status="off", bid_amt=190)
    pre_pause_date = D_TO - timedelta(days=5)
    _row(db, pre_pause_date, "cmp1", "WEB_SITE", "grp1", "nkw-off-1", 100, 10, 2000, direct=10000)
    db.add(_lock_log(  # Jino가 콘솔에서 수동 정지 — 우리가 재개 판단 금지
        "nkw-off-1", "cmp1", locked=True, proposal_id=None,
        changed_at=datetime.combine(D_TO, datetime.min.time()),
    ))
    db.commit()

    out = diag.resume_candidates(db, D_TO, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"))
    assert out == []


def test_resume_candidates_excludes_roas_still_below_target(db):
    _entity(db, "keyword", "nkw-off-1", status="off", bid_amt=190)
    pre_pause_date = D_TO - timedelta(days=5)
    _row(db, pre_pause_date, "cmp1", "WEB_SITE", "grp1", "nkw-off-1", 100, 10, 2000, direct=1000)  # roas=0.5
    db.add(_lock_log("nkw-off-1", "cmp1", locked=True, proposal_id=1,
                      changed_at=datetime.combine(D_TO, datetime.min.time())))
    db.commit()

    out = diag.resume_candidates(db, D_TO, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"))
    assert out == []


def test_resume_candidates_excludes_off_keyword_without_pause_log(db):
    # status='off'지만 change_log에 정지 기록이 없음(예: 네이버 콘솔에서 직접 조작, 추적 불가)
    _entity(db, "keyword", "nkw-off-1", status="off", bid_amt=190)
    db.commit()

    out = diag.resume_candidates(db, D_TO, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"))
    assert out == []


def test_resume_candidates_excludes_currently_on_keyword(db):
    _entity(db, "keyword", "nkw-1", status="on", bid_amt=190)
    db.add(_lock_log("nkw-1", "cmp1", locked=True, proposal_id=1,
                      changed_at=datetime.combine(D_TO, datetime.min.time())))
    db.commit()

    out = diag.resume_candidates(db, D_TO, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"))
    assert out == []


def test_resume_candidates_uses_most_recent_pause_when_multiple(db):
    # 정지→(다른 이유로 우리가 아닌?)재개→재정지 이력이 있어도 가장 최근 정지만 사용
    _entity(db, "keyword", "nkw-off-1", status="off", bid_amt=190)
    old_pre_pause = D_TO - timedelta(days=40)
    recent_pre_pause = D_TO - timedelta(days=5)
    _row(db, old_pre_pause, "cmp1", "WEB_SITE", "grp1", "nkw-off-1", 100, 10, 2000, direct=1000)  # 낮은 roas(옛 정지 근거)
    _row(db, recent_pre_pause, "cmp1", "WEB_SITE", "grp1", "nkw-off-1", 100, 10, 2000, direct=10000)  # 높은 roas(최근 정지 근거)
    db.add(_lock_log("nkw-off-1", "cmp1", locked=True, proposal_id=1,
                      changed_at=datetime.combine(D_TO - timedelta(days=35), datetime.min.time())))
    db.add(_lock_log("nkw-off-1", "cmp1", locked=True, proposal_id=2,
                      changed_at=datetime.combine(D_TO, datetime.min.time())))
    db.commit()

    out = diag.resume_candidates(db, D_TO, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"))
    assert len(out) == 1
    assert out[0]["roas_at_pause"] == 5.0  # 최근 정지 직전 창 기준(옛 창의 0.5가 아님)


def test_resume_candidates_excludes_when_latest_lock_change_is_manual_repause_after_our_resume(db):
    """[codex P2] 정지(우리, proposal_id=1) → 재개(우리, userLock=false) → 재정지(Jino가 콘솔에서
    수동, userLock=true·proposal_id=None) 이력이면, status='off'인 지금 상태의 진짜 원인은
    가장 최근의 수동 재정지다. 옛 우리 정지(proposal_id=1)를 max(changed_at)로 잘못 채택하면
    안 됨 — 최근 잠금변경 자체가 정지(userLock=true)이고 proposal_id도 있어야 재개 후보."""
    _entity(db, "keyword", "nkw-off-1", status="off", bid_amt=190)
    pre_pause_date = D_TO - timedelta(days=40)
    _row(db, pre_pause_date, "cmp1", "WEB_SITE", "grp1", "nkw-off-1", 100, 10, 2000, direct=10000)  # roas=5.0
    db.add(_lock_log(  # ①우리 정지
        "nkw-off-1", "cmp1", locked=True, proposal_id=1,
        changed_at=datetime.combine(D_TO - timedelta(days=35), datetime.min.time()),
    ))
    db.add(_lock_log(  # ②우리 재개(예: 이전 회차 resume_candidates가 실행됨)
        "nkw-off-1", "cmp1", locked=False, proposal_id=2,
        changed_at=datetime.combine(D_TO - timedelta(days=20), datetime.min.time()),
    ))
    db.add(_lock_log(  # ③Jino가 콘솔에서 수동 재정지 — 최신 이벤트, proposal_id 없음
        "nkw-off-1", "cmp1", locked=True, proposal_id=None,
        changed_at=datetime.combine(D_TO - timedelta(days=5), datetime.min.time()),
    ))
    db.commit()

    out = diag.resume_candidates(db, D_TO, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"))
    assert out == []


def test_resume_candidates_includes_when_latest_lock_change_is_our_repause(db):
    """대조군: ①우리 정지 ②우리 재개 ③우리 재정지(proposal_id 있음) — 최신 이벤트가 여전히
    우리 정지라면 정상적으로 후보가 돼야 한다."""
    _entity(db, "keyword", "nkw-off-1", status="off", bid_amt=190)
    recent_pre_pause = D_TO - timedelta(days=5)
    _row(db, recent_pre_pause, "cmp1", "WEB_SITE", "grp1", "nkw-off-1", 100, 10, 2000, direct=10000)  # roas=5.0
    db.add(_lock_log("nkw-off-1", "cmp1", locked=True, proposal_id=1,
                      changed_at=datetime.combine(D_TO - timedelta(days=35), datetime.min.time())))
    db.add(_lock_log("nkw-off-1", "cmp1", locked=False, proposal_id=2,
                      changed_at=datetime.combine(D_TO - timedelta(days=20), datetime.min.time())))
    db.add(_lock_log("nkw-off-1", "cmp1", locked=True, proposal_id=3,
                      changed_at=datetime.combine(D_TO, datetime.min.time())))
    db.commit()

    out = diag.resume_candidates(db, D_TO, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"))
    assert len(out) == 1
    assert out[0]["roas_at_pause"] == 5.0


def test_resume_candidates_excludes_when_latest_lock_change_after_value_unparseable(db):
    """after_value가 JSON이 아니면(구 데이터·손상) 방향 판별 불가 — fail-closed 제외.
    dry_run=False 명시 — dry_run 필터가 아니라 파싱 실패 경로로 제외되는 것을 검증."""
    _entity(db, "keyword", "nkw-off-1", status="off", bid_amt=190)
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-off-1", campaign_id="cmp1", action="set_user_lock",
        proposal_id=1, dry_run=False, changed_at=datetime.combine(D_TO, datetime.min.time()),
        after_value="not json",
    ))
    db.commit()

    out = diag.resume_candidates(db, D_TO, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"))
    assert out == []


def test_resume_candidates_excludes_when_external_repause_logged_by_entity_sync(db):
    """[codex P2, D-NAO-40] 시나리오: 우리 정지(proposal_id=1) → 외부 재개(change_log 미기록)
    → 외부 재정지(entity_sync가 external_status_change 기록, proposal_id=None).
    resume_candidates 쿼리가 external_status_change도 고려해 external 재정지가 최신이면
    재개 후보에서 제외해야 한다."""
    _entity(db, "keyword", "nkw-off-1", status="off", bid_amt=190)
    # 정지 직전 창(정지=D_TO기준)에 양호한 실적 — roas 5.0
    pre_pause_date = D_TO - timedelta(days=2)
    _row(db, pre_pause_date, "cmp1", "WEB_SITE", "grp1", "nkw-off-1", 100, 10, 2000, direct=10000)
    # ①우리 정지(D_TO)
    db.add(_lock_log("nkw-off-1", "cmp1", locked=True, proposal_id=1,
                      changed_at=datetime.combine(D_TO, datetime.min.time())))
    # ②외부 재정지(entity_sync가 감지, D_TO+3일) — action=external_status_change, proposal_id=None
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-off-1", campaign_id="cmp1",
        action="external_status_change", proposal_id=None, dry_run=False,
        changed_at=datetime.combine(D_TO + timedelta(days=3), datetime.min.time()),
        after_value=json.dumps({"userLock": True}),
        before_value=json.dumps({"userLock": False}),
    ))
    db.commit()

    out = diag.resume_candidates(db, D_TO, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"))
    assert out == []  # 외부 재정지가 최신 → 재개 금지


def test_resume_candidates_uses_per_campaign_target_roas_override(db):
    """[codex P2] 계정 기본 target_roas(2.0)만 쓰면 캠페인 override(5.0)가 무시된다 —
    roas_at_pause=3.0은 계정기본(2.0)은 넘지만 캠페인 override(5.0)는 못 넘으므로 후보에서
    빠져야 한다(compute_bid_sims의 _make_target_roas_resolver와 동일 재발버그 패턴,
    2026-07-07 라이브검증 이력 참조)."""
    _entity(db, "keyword", "nkw-off-1", status="off", bid_amt=190, campaign_id="cmp-override")
    pre_pause_date = D_TO - timedelta(days=5)
    _row(db, pre_pause_date, "cmp-override", "WEB_SITE", "grp1", "nkw-off-1", 100, 10, 3000, direct=9000)  # roas=3.0
    db.add(_lock_log("nkw-off-1", "cmp-override", locked=True, proposal_id=1,
                      changed_at=datetime.combine(D_TO, datetime.min.time())))
    db.commit()

    resolver = lambda cid: Decimal("5.0") if cid == "cmp-override" else Decimal("2.0")  # noqa: E731
    out = diag.resume_candidates(db, D_TO, target_roas_resolver=resolver, correction_factor=Decimal("1"))
    assert out == []  # 3.0 < campaign override 5.0


def test_resume_candidates_passes_with_campaign_override_when_roas_clears_it(db):
    _entity(db, "keyword", "nkw-off-1", status="off", bid_amt=190, campaign_id="cmp-override")
    pre_pause_date = D_TO - timedelta(days=5)
    _row(db, pre_pause_date, "cmp-override", "WEB_SITE", "grp1", "nkw-off-1", 100, 10, 1000, direct=6000)  # roas=6.0
    db.add(_lock_log("nkw-off-1", "cmp-override", locked=True, proposal_id=1,
                      changed_at=datetime.combine(D_TO, datetime.min.time())))
    db.commit()

    resolver = lambda cid: Decimal("5.0") if cid == "cmp-override" else Decimal("2.0")  # noqa: E731
    out = diag.resume_candidates(db, D_TO, target_roas_resolver=resolver, correction_factor=Decimal("1"))
    assert len(out) == 1
    assert out[0]["roas_at_pause"] == 6.0


# ── shopping_pause_candidates (X1b-S S1, D-NAO-43) ────────────────────────
# pause_candidates(WEB_SITE 키워드 전용)의 SHOPPING adgroup 대칭 확장 — 대상 자체가
# 광고그룹(entity_type='adgroup', campaign_type='SHOPPING')이라 부모체인은 캠페인→adgroup
# 2단만(키워드판의 3단과 달리 대상 자신이 이미 adgroup).


def _shopping_entity(db, adgroup_id, *, status="on", bid_amt=None, campaign_id="cmp-shop"):
    db.add(NaverEntity(
        entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
        campaign_id=campaign_id, campaign_type="SHOPPING", name=adgroup_id,
        status=status, bid_amt=bid_amt,
    ))


def _seed_shopping_active_campaign(db, *, campaign_id="cmp-shop"):
    db.add(NaverEntity(entity_type="campaign", entity_id=campaign_id, parent_id="",
                        campaign_id=campaign_id, campaign_type="SHOPPING", name=campaign_id, status="on"))


def test_shopping_pause_candidates_zero_conversion_over_stop_loss(db):
    # bid_amt=200 → 스톱로스 절대액 = 200*10 = 2,000원. 무전환 누적비용 2,500원.
    _seed_shopping_active_campaign(db)
    _shopping_entity(db, "grp-shop-1", status="on", bid_amt=200)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 2500, direct=0, indirect=0)
    db.commit()

    out = diag.shopping_pause_candidates(db, D0, D0)
    assert len(out) == 1
    assert out[0]["adgroup_id"] == "grp-shop-1"
    assert out[0]["campaign_id"] == "cmp-shop"
    assert out[0]["current_bid"] == 200
    assert out[0]["stop_loss_amount"] == 2000
    assert out[0]["cost"] == 2500
    assert out[0]["conv_amt"] == 0


def test_shopping_pause_candidates_excludes_converting_adgroup(db):
    _seed_shopping_active_campaign(db)
    _shopping_entity(db, "grp-shop-1", status="on", bid_amt=200)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 2500, direct=3000)  # 전환 있음
    db.commit()

    out = diag.shopping_pause_candidates(db, D0, D0)
    assert out == []


def test_shopping_pause_candidates_excludes_cost_below_stop_loss(db):
    _seed_shopping_active_campaign(db)
    _shopping_entity(db, "grp-shop-1", status="on", bid_amt=200)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 200, 5, 1000, direct=0)  # 1,000 < 2,000
    db.commit()

    out = diag.shopping_pause_candidates(db, D0, D0)
    assert out == []


def test_shopping_pause_candidates_excludes_already_off(db):
    _seed_shopping_active_campaign(db)
    _shopping_entity(db, "grp-shop-1", status="off", bid_amt=200)  # 이미 정지됨
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 2500, direct=0)
    db.commit()

    out = diag.shopping_pause_candidates(db, D0, D0)
    assert out == []


def test_shopping_pause_candidates_excludes_missing_bid_amt(db):
    # NaverEntity 행 자체가 없음(bid_amt 미확보) — fail-closed 스킵
    _seed_shopping_active_campaign(db)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 2500, direct=0)
    db.commit()

    out = diag.shopping_pause_candidates(db, D0, D0)
    assert out == []


def test_shopping_pause_candidates_excludes_entity_bid_amt_none_even_if_row_exists(db):
    # NaverEntity 행은 있지만 bid_amt=None(미확보) — fail-closed 스킵
    _seed_shopping_active_campaign(db)
    _shopping_entity(db, "grp-shop-1", status="on", bid_amt=None)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 2500, direct=0)
    db.commit()

    out = diag.shopping_pause_candidates(db, D0, D0)
    assert out == []


def test_shopping_pause_candidates_excludes_adgroup_under_paused_campaign(db):
    """pause_candidates의 부모체인 검사(codex P2)와 동일 근거 — 캠페인이 off인데 그룹
    자체는 status='on'으로 남을 수 있다(entity_sync가 캐스케이드하지 않음, D-NAO-27)."""
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp-shop", parent_id="",
                        campaign_id="cmp-shop", campaign_type="SHOPPING", name="cmp-shop", status="off"))
    _shopping_entity(db, "grp-shop-1", status="on", bid_amt=200)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 2500, direct=0)
    db.commit()

    out = diag.shopping_pause_candidates(db, D0, D0)
    assert out == []


def test_shopping_pause_candidates_includes_when_parent_campaign_on(db):
    _seed_shopping_active_campaign(db)
    _shopping_entity(db, "grp-shop-1", status="on", bid_amt=200)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 2500, direct=0)
    db.commit()

    out = diag.shopping_pause_candidates(db, D0, D0)
    assert len(out) == 1
    assert out[0]["adgroup_id"] == "grp-shop-1"


def test_shopping_pause_candidates_excludes_web_site_campaign_type(db):
    # WEB_SITE 캠페인/그룹은 pause_candidates(키워드판)의 대상 — 이 보드는 SHOPPING 전용
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp-web", parent_id="",
                        campaign_id="cmp-web", campaign_type="WEB_SITE", name="cmp-web", status="on"))
    db.add(NaverEntity(entity_type="adgroup", entity_id="grp-web-1", parent_id="cmp-web",
                        campaign_id="cmp-web", campaign_type="WEB_SITE", name="grp-web-1",
                        status="on", bid_amt=200))
    _row(db, D0, "cmp-web", "WEB_SITE", "grp-web-1", "", 500, 25, 2500, direct=0)
    db.commit()

    out = diag.shopping_pause_candidates(db, D0, D0)
    assert out == []


def test_shopping_pause_candidates_sorted_by_cost_desc(db):
    _seed_shopping_active_campaign(db)
    _shopping_entity(db, "grp-shop-1", status="on", bid_amt=200)
    _shopping_entity(db, "grp-shop-2", status="on", bid_amt=200)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 2000, direct=0)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-2", "", 800, 40, 3000, direct=0)
    db.commit()

    out = diag.shopping_pause_candidates(db, D0, D0)
    assert [r["adgroup_id"] for r in out] == ["grp-shop-2", "grp-shop-1"]


# ── shopping_resume_candidates (X1b-S S1, D-NAO-43) ───────────────────────
# resume_candidates(WEB_SITE 키워드 전용)의 SHOPPING adgroup 대칭 확장 — D-NAO-40 안전판별
# (proposal_id 존재·최신 잠금변경이 우리 정지일 때만)을 entity_type='adgroup'으로 계승.


def _shopping_lock_log(adgroup_id, campaign_id, *, locked, proposal_id, changed_at):
    return NaverChangeLog(
        entity_type="adgroup", entity_id=adgroup_id, campaign_id=campaign_id, action="set_user_lock",
        proposal_id=proposal_id, dry_run=False, changed_at=changed_at,
        after_value=json.dumps({"userLock": locked}),
    )


def test_shopping_resume_candidates_recovered_roas_since_our_pause(db):
    _shopping_entity(db, "grp-shop-off-1", status="off", bid_amt=190)
    pre_pause_date = D_TO - timedelta(days=5)
    _row(db, pre_pause_date, "cmp-shop", "SHOPPING", "grp-shop-off-1", "", 100, 10, 2000, direct=10000)
    db.add(_shopping_lock_log("grp-shop-off-1", "cmp-shop", locked=True, proposal_id=1,
                               changed_at=datetime.combine(D_TO, datetime.min.time())))
    db.commit()

    out = diag.shopping_resume_candidates(
        db, D_TO, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"),
    )
    assert len(out) == 1
    assert out[0]["adgroup_id"] == "grp-shop-off-1"
    assert out[0]["roas_at_pause"] == 5.0


def test_shopping_resume_candidates_excludes_manual_pause_no_proposal_id(db):
    _shopping_entity(db, "grp-shop-off-1", status="off", bid_amt=190)
    pre_pause_date = D_TO - timedelta(days=5)
    _row(db, pre_pause_date, "cmp-shop", "SHOPPING", "grp-shop-off-1", "", 100, 10, 2000, direct=10000)
    db.add(_shopping_lock_log(  # Jino가 콘솔에서 수동 정지 — 우리가 재개 판단 금지
        "grp-shop-off-1", "cmp-shop", locked=True, proposal_id=None,
        changed_at=datetime.combine(D_TO, datetime.min.time()),
    ))
    db.commit()

    out = diag.shopping_resume_candidates(
        db, D_TO, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"),
    )
    assert out == []


def test_shopping_resume_candidates_excludes_roas_still_below_target(db):
    _shopping_entity(db, "grp-shop-off-1", status="off", bid_amt=190)
    pre_pause_date = D_TO - timedelta(days=5)
    _row(db, pre_pause_date, "cmp-shop", "SHOPPING", "grp-shop-off-1", "", 100, 10, 2000, direct=1000)  # roas=0.5
    db.add(_shopping_lock_log("grp-shop-off-1", "cmp-shop", locked=True, proposal_id=1,
                               changed_at=datetime.combine(D_TO, datetime.min.time())))
    db.commit()

    out = diag.shopping_resume_candidates(
        db, D_TO, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"),
    )
    assert out == []


def test_shopping_resume_candidates_excludes_off_adgroup_without_pause_log(db):
    _shopping_entity(db, "grp-shop-off-1", status="off", bid_amt=190)
    db.commit()

    out = diag.shopping_resume_candidates(
        db, D_TO, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"),
    )
    assert out == []


def test_shopping_resume_candidates_excludes_currently_on_adgroup(db):
    _shopping_entity(db, "grp-shop-1", status="on", bid_amt=190)
    db.add(_shopping_lock_log("grp-shop-1", "cmp-shop", locked=True, proposal_id=1,
                               changed_at=datetime.combine(D_TO, datetime.min.time())))
    db.commit()

    out = diag.shopping_resume_candidates(
        db, D_TO, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"),
    )
    assert out == []


def test_shopping_resume_candidates_excludes_when_latest_lock_change_is_manual_repause(db):
    """D-NAO-40 안전판별 계승: 정지(우리)→재개(우리)→재정지(Jino 수동, proposal_id=None)면
    최신 잠금변경이 수동 재정지라 재개 후보에서 빠져야 한다."""
    _shopping_entity(db, "grp-shop-off-1", status="off", bid_amt=190)
    pre_pause_date = D_TO - timedelta(days=40)
    _row(db, pre_pause_date, "cmp-shop", "SHOPPING", "grp-shop-off-1", "", 100, 10, 2000, direct=10000)
    db.add(_shopping_lock_log(
        "grp-shop-off-1", "cmp-shop", locked=True, proposal_id=1,
        changed_at=datetime.combine(D_TO - timedelta(days=35), datetime.min.time()),
    ))
    db.add(_shopping_lock_log(
        "grp-shop-off-1", "cmp-shop", locked=False, proposal_id=2,
        changed_at=datetime.combine(D_TO - timedelta(days=20), datetime.min.time()),
    ))
    db.add(_shopping_lock_log(
        "grp-shop-off-1", "cmp-shop", locked=True, proposal_id=None,
        changed_at=datetime.combine(D_TO - timedelta(days=5), datetime.min.time()),
    ))
    db.commit()

    out = diag.shopping_resume_candidates(
        db, D_TO, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"),
    )
    assert out == []


def test_shopping_resume_candidates_excludes_when_external_repause_logged(db):
    """D-NAO-40: entity_sync가 기록한 external_status_change 재정지가 최신이면 재개 후보 제외."""
    _shopping_entity(db, "grp-shop-off-1", status="off", bid_amt=190)
    pre_pause_date = D_TO - timedelta(days=2)
    _row(db, pre_pause_date, "cmp-shop", "SHOPPING", "grp-shop-off-1", "", 100, 10, 2000, direct=10000)
    db.add(_shopping_lock_log("grp-shop-off-1", "cmp-shop", locked=True, proposal_id=1,
                               changed_at=datetime.combine(D_TO, datetime.min.time())))
    db.add(NaverChangeLog(
        entity_type="adgroup", entity_id="grp-shop-off-1", campaign_id="cmp-shop",
        action="external_status_change", proposal_id=None, dry_run=False,
        changed_at=datetime.combine(D_TO + timedelta(days=3), datetime.min.time()),
        after_value=json.dumps({"userLock": True}),
        before_value=json.dumps({"userLock": False}),
    ))
    db.commit()

    out = diag.shopping_resume_candidates(
        db, D_TO, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"),
    )
    assert out == []


def test_shopping_resume_candidates_uses_per_campaign_target_roas_override(db):
    _shopping_entity(db, "grp-shop-off-1", status="off", bid_amt=190, campaign_id="cmp-shop-override")
    pre_pause_date = D_TO - timedelta(days=5)
    _row(db, pre_pause_date, "cmp-shop-override", "SHOPPING", "grp-shop-off-1", "", 100, 10, 3000, direct=9000)  # roas=3.0
    db.add(_shopping_lock_log("grp-shop-off-1", "cmp-shop-override", locked=True, proposal_id=1,
                               changed_at=datetime.combine(D_TO, datetime.min.time())))
    db.commit()

    resolver = lambda cid: Decimal("5.0") if cid == "cmp-shop-override" else Decimal("2.0")  # noqa: E731
    out = diag.shopping_resume_candidates(db, D_TO, target_roas_resolver=resolver, correction_factor=Decimal("1"))
    assert out == []  # 3.0 < campaign override 5.0


# ── campaign_window_agg (P2, D-NAO-42-f) ─────────────────────────────────


def test_campaign_window_agg_sums_cost_and_conv_amt_across_adgroups_and_keywords(db):
    # 같은 캠페인의 서로 다른 광고그룹/키워드 행이 합산되어야 함
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 100, 10, 3000, direct=1000, indirect=500)
    _row(db, D0, "cmp1", "WEB_SITE", "grp2", "nkw-2", 50, 5, 2000, direct=0, indirect=0)
    # 다른 캠페인 — 집계에서 제외되어야 함
    _row(db, D0, "cmp2", "WEB_SITE", "grp1", "nkw-3", 100, 10, 9000, direct=9000)
    db.commit()

    out = diag.campaign_window_agg(db, "cmp1", D0, D0)
    assert out == {"cost": 5000, "conv_amt": 1500}


def test_campaign_window_agg_includes_shopping_campaign_type():
    # 이 테스트는 SHOPPING 타입도 집계되어야 함(예산 통제는 캠페인 그레인 — WEB_SITE 전용
    # 아님, keyword_window_agg와의 핵심 차이). db fixture 재사용 위해 아래에서 세션 생성.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        _row(db, D0, "cmp-shopping", "SHOPPING", "grp1", "", 200, 20, 4000, direct=8000)
        db.commit()
        out = diag.campaign_window_agg(db, "cmp-shopping", D0, D0)
        assert out == {"cost": 4000, "conv_amt": 8000}  # WEB_SITE 필터가 없어 SHOPPING도 잡힘
    finally:
        db.close()


def test_campaign_window_agg_excludes_backfill_sentinel_adgroup(db):
    # 백필 센티널 행(adgroup_id=BACKFILL_SENTINEL_ADGROUP)은 실단위 행과의 이중집계를
    # 막기 위해 제외한다(keyword_window_agg와 동일 규율).
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 100, 10, 3000, direct=1000)
    _row(db, D0, "cmp1", "WEB_SITE", BACKFILL_SENTINEL_ADGROUP, "", 100, 10, 999_999, direct=0)
    db.commit()

    out = diag.campaign_window_agg(db, "cmp1", D0, D0)
    assert out == {"cost": 3000, "conv_amt": 1000}


def test_campaign_window_agg_no_data_returns_zeros(db):
    out = diag.campaign_window_agg(db, "cmp-nonexistent", D0, D0)
    assert out == {"cost": 0, "conv_amt": 0}


def test_campaign_window_agg_respects_date_window(db):
    _row(db, D0, "cmp1", "WEB_SITE", "grp1", "nkw-1", 100, 10, 3000, direct=1000)
    _row(db, D0 - timedelta(days=1), "cmp1", "WEB_SITE", "grp1", "nkw-1", 100, 10, 5000, direct=2000)
    db.commit()

    out = diag.campaign_window_agg(db, "cmp1", D0, D0)
    assert out == {"cost": 3000, "conv_amt": 1000}  # 창 밖(D0-1) 행은 제외


# ── shopping_group_growth (X1b-S S3, D-NAO-43 성장 확장) ──────────────────
# shopping_group_bep(적자, down)의 반대 — 수익 그룹(보정ROAS≥캠페인목표) 성장 후보.
# _on_adgroup_ids(부모 체인 전부 on) + entity bid_amt 확보를 요구한다(fail-closed, downstream
# bid_simulator/실쓰기가 현재 입찰가를 필요로 함 — shopping_pause_candidates와 동일 근거).


def test_shopping_group_growth_flags_profitable_group_above_target(db):
    _seed_shopping_active_campaign(db)
    _shopping_entity(db, "grp-shop-1", status="on", bid_amt=1000)
    # cost=8000, conv_amt=40000 → roas=5.0 ≥ 목표 2.0(성장 후보)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 8000, direct=40000)
    db.commit()

    out = diag.shopping_group_growth(
        db, D0, D0, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"),
    )
    assert len(out) == 1
    assert out[0]["adgroup_id"] == "grp-shop-1"
    assert out[0]["campaign_id"] == "cmp-shop"
    assert out[0]["cost"] == 8000
    assert out[0]["conv_amt"] == 40000
    assert out[0]["roas_corrected"] == 5.0
    # codex[P2]: clk를 실어야 compute_bid_sims가 이 그룹 자신의 RPC를 계산(캠페인 폴백 방지)
    assert out[0]["clk"] == 25


def test_shopping_group_growth_excludes_deficit_group_below_target(db):
    _seed_shopping_active_campaign(db)
    _shopping_entity(db, "grp-shop-1", status="on", bid_amt=1000)
    # cost=8000, conv_amt=8000 → roas=1.0 < 목표 2.0(적자/미달, 성장 대상 아님)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 8000, direct=8000)
    db.commit()

    out = diag.shopping_group_growth(
        db, D0, D0, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"),
    )
    assert out == []


def test_shopping_group_growth_excludes_when_parent_campaign_off(db):
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp-shop", parent_id="",
                        campaign_id="cmp-shop", campaign_type="SHOPPING", name="cmp-shop", status="off"))
    _shopping_entity(db, "grp-shop-1", status="on", bid_amt=1000)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 8000, direct=40000)
    db.commit()

    out = diag.shopping_group_growth(
        db, D0, D0, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"),
    )
    assert out == []


def test_shopping_group_growth_excludes_when_adgroup_off(db):
    _seed_shopping_active_campaign(db)
    _shopping_entity(db, "grp-shop-1", status="off", bid_amt=1000)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 8000, direct=40000)
    db.commit()

    out = diag.shopping_group_growth(
        db, D0, D0, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"),
    )
    assert out == []


def test_shopping_group_growth_excludes_missing_bid_amt(db):
    # NaverEntity 행 자체가 없음(bid_amt 미확보) — fail-closed 스킵(downstream 실행이
    # 현재 입찰가를 필요로 함, shopping_pause_candidates와 동일 근거)
    _seed_shopping_active_campaign(db)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 8000, direct=40000)
    db.commit()

    out = diag.shopping_group_growth(
        db, D0, D0, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"),
    )
    assert out == []


def test_shopping_group_growth_excludes_entity_bid_amt_none_even_if_row_exists(db):
    _seed_shopping_active_campaign(db)
    _shopping_entity(db, "grp-shop-1", status="on", bid_amt=None)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 8000, direct=40000)
    db.commit()

    out = diag.shopping_group_growth(
        db, D0, D0, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"),
    )
    assert out == []


def test_shopping_group_growth_excludes_zero_cost(db):
    _seed_shopping_active_campaign(db)
    _shopping_entity(db, "grp-shop-1", status="on", bid_amt=1000)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 0, 0, direct=0)
    db.commit()

    out = diag.shopping_group_growth(
        db, D0, D0, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"),
    )
    assert out == []


def test_shopping_group_growth_excludes_web_site_campaign_type(db):
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp-web", parent_id="",
                        campaign_id="cmp-web", campaign_type="WEB_SITE", name="cmp-web", status="on"))
    db.add(NaverEntity(entity_type="adgroup", entity_id="grp-web-1", parent_id="cmp-web",
                        campaign_id="cmp-web", campaign_type="WEB_SITE", name="grp-web-1",
                        status="on", bid_amt=1000))
    _row(db, D0, "cmp-web", "WEB_SITE", "grp-web-1", "", 500, 25, 8000, direct=40000)
    db.commit()

    out = diag.shopping_group_growth(
        db, D0, D0, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"),
    )
    assert out == []


def test_shopping_group_growth_applies_correction_factor(db):
    _seed_shopping_active_campaign(db)
    _shopping_entity(db, "grp-shop-1", status="on", bid_amt=1000)
    # roas_naver=1.0, 보정계수 3.0 적용 → roas_corrected=3.0 ≥ 목표 2.0
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 8000, direct=8000)
    db.commit()

    out = diag.shopping_group_growth(
        db, D0, D0, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("3.0"),
    )
    assert len(out) == 1
    assert out[0]["roas_corrected"] == pytest.approx(3.0)


def test_shopping_group_growth_uses_per_campaign_target_roas_override(db):
    _seed_shopping_active_campaign(db, campaign_id="cmp-shop-override")
    _shopping_entity(db, "grp-shop-1", status="on", bid_amt=1000, campaign_id="cmp-shop-override")
    # roas=3.0 — 계정 기본 2.0은 넘지만 캠페인 override 5.0은 못 넘음
    _row(db, D0, "cmp-shop-override", "SHOPPING", "grp-shop-1", "", 500, 25, 3000, direct=9000)
    db.commit()

    resolver = lambda cid: Decimal("5.0") if cid == "cmp-shop-override" else Decimal("2.0")  # noqa: E731
    out = diag.shopping_group_growth(db, D0, D0, target_roas_resolver=resolver, correction_factor=Decimal("1"))
    assert out == []  # 3.0 < campaign override 5.0


def test_shopping_group_growth_sorted_by_roas_desc(db):
    _seed_shopping_active_campaign(db)
    _shopping_entity(db, "grp-shop-1", status="on", bid_amt=1000)
    _shopping_entity(db, "grp-shop-2", status="on", bid_amt=1000)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 8000, direct=24000)   # roas=3.0
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-2", "", 500, 25, 8000, direct=80000)   # roas=10.0
    db.commit()

    out = diag.shopping_group_growth(
        db, D0, D0, target_roas_resolver=lambda cid: Decimal("2.0"), correction_factor=Decimal("1"),
    )
    assert [r["adgroup_id"] for r in out] == ["grp-shop-2", "grp-shop-1"]


# ── adgroup_window_agg (P2/S3, D-NAO-42-f PLAN §3 S2/S3 스코프 실현) ──────
# keyword_window_agg의 adgroup grain 대칭판(공개, campaign_type 무관) — 실행하너스(S3)
# 가드레일 컨텍스트의 원료. _shopping_adgroup_window_agg(private, SHOPPING 필터·
# shopping_resume_candidates 전용)와 별개 — 그 함수는 그대로 유지된다.


def test_adgroup_window_agg_sums_cost_and_conv_amt(db):
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 8000, direct=3000, indirect=1000)
    db.commit()

    out = diag.adgroup_window_agg(db, "grp-shop-1", D0, D0)
    assert out["cost"] == 8000
    assert out["conv_amt"] == 4000


def test_adgroup_window_agg_includes_conv_cnt(db):
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 8000, direct=3000, indirect=1000)
    db.commit()

    out = diag.adgroup_window_agg(db, "grp-shop-1", D0, D0)
    assert out["conv_cnt"] == 2  # direct 1건 + indirect 1건(_row 헬퍼가 각 1건씩 세팅)


def test_adgroup_window_agg_excludes_other_adgroups(db):
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 8000, direct=3000)
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-2", "", 500, 25, 9000, direct=1000)
    db.commit()

    out = diag.adgroup_window_agg(db, "grp-shop-1", D0, D0)
    assert out == {"cost": 8000, "conv_amt": 3000, "conv_cnt": 1}


def test_adgroup_window_agg_excludes_backfill_sentinel_adgroup(db):
    _row(db, D0, "cmp-shop", "SHOPPING", BACKFILL_SENTINEL_ADGROUP, "", 500, 25, 999_999, direct=0)
    db.commit()

    out = diag.adgroup_window_agg(db, BACKFILL_SENTINEL_ADGROUP, D0, D0)
    assert out == {"cost": 0, "conv_amt": 0, "conv_cnt": 0}


def test_adgroup_window_agg_no_data_returns_zeros(db):
    out = diag.adgroup_window_agg(db, "grp-nonexistent", D0, D0)
    assert out == {"cost": 0, "conv_amt": 0, "conv_cnt": 0}


def test_adgroup_window_agg_respects_date_window(db):
    _row(db, D0, "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 8000, direct=3000)
    _row(db, D0 - timedelta(days=1), "cmp-shop", "SHOPPING", "grp-shop-1", "", 500, 25, 5000, direct=2000)
    db.commit()

    out = diag.adgroup_window_agg(db, "grp-shop-1", D0, D0)
    assert out == {"cost": 8000, "conv_amt": 3000, "conv_cnt": 1}  # 창 밖(D0-1) 행은 제외
