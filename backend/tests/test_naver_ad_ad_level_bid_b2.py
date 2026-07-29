# test_naver_ad_ad_level_bid_b2.py — 스프린트 B Phase 2(B2: 실효입찰 파생 + 진단 재정의, D-NAO-65)
# 커버: (1) effective_bid.adgroup_effective_bid 순수 파생(전부 false→max·혼합→max(소재,그룹)·
#   전부 true→그룹입찰·데이터없음→폴백·정지소재 제외·근거 필드)
#   (2) account_diagnosis.shopping_pause_candidates 실효입찰 재정의(스톱로스 임계=실효×10·
#   lever_broken=CPC>5×실효·board effective_bid 필드·B1 데이터 없으면 종전 그룹입찰 폴백)
#   (3) proposal_writer._stop_loss_proposal 쇼핑 at-floor/사유문이 effective_bid 소비
#   (실효≫그룹=레버 미연결 → 그룹 bid_down 무의미 → lever/pause에 위임, target_bid는 그룹 스텝)
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily,
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverHourlySnapshot,
    NaverProposal,
)
from app.services.naver_ad import account_diagnosis as diag
from app.services.naver_ad import auto_operator, effective_bid, proposal_writer

D0 = date(2026, 7, 1)
D_TO = date(2026, 7, 15)  # 다일 창 테스트용(GATE P2-1)


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


def _ad(db, adgroup_id, mall_product_id, *, ad_id, ad_bid_amt, use_group, user_lock=False):
    db.add(NaverAdgroupProduct(
        adgroup_id=adgroup_id, campaign_id="cmp-shop", mall_product_id=mall_product_id,
        product_name=mall_product_id, ad_id=ad_id, ad_bid_amt=ad_bid_amt,
        use_group_bid_amt=use_group, ad_user_lock=user_lock,
    ))


def _daily(db, ad_date, adgroup_id, *, clk, cost, direct=0, campaign_id="cmp-shop"):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type="SHOPPING",
        adgroup_id=adgroup_id, keyword_id="", imp=clk * 20, clk=clk, cost=cost, rank_sum=clk * 60,
        conv_direct_cnt=1 if direct else 0, conv_direct_amt=direct,
    ))


def _shopping_entity(db, adgroup_id, *, status="on", bid_amt, campaign_id="cmp-shop"):
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
                       campaign_id=campaign_id, campaign_type="SHOPPING", name=adgroup_id,
                       status=status, bid_amt=bid_amt))


def _active_campaign(db, campaign_id="cmp-shop"):
    db.add(NaverEntity(entity_type="campaign", entity_id=campaign_id, parent_id="",
                       campaign_id=campaign_id, campaign_type="SHOPPING", name=campaign_id, status="on"))


# ══════════════════════════════════════════════════════════════════
# (1) effective_bid.adgroup_effective_bid — 순수 파생 SA
# ══════════════════════════════════════════════════════════════════
def test_effective_bid_all_false_uses_max_ad_bid(db):
    """소재 전부 useGroupBidAmt=false → 실효입찰 = max(소재 bidAmt)(그룹입찰 무시)."""
    _ad(db, "grp-1", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)
    _ad(db, "grp-1", "p2", ad_id="nad-2", ad_bid_amt=1200, use_group=False)
    db.commit()
    r = effective_bid.adgroup_effective_bid(db, "grp-1", 50)
    assert r["effective_bid"] == 1200
    assert r["has_ad_data"] is True
    assert r["source"] == "ad"
    assert r["max_ad_id"] == "nad-2"
    assert r["ad_count"] == 2


def test_effective_bid_mixed_uses_max_of_ad_and_group(db):
    """혼합(false 소재 + true 소재) → 실효입찰 = max(소재 bidAmt, 그룹입찰)."""
    _ad(db, "grp-1", "p1", ad_id="nad-1", ad_bid_amt=300, use_group=False)
    _ad(db, "grp-1", "p2", ad_id="nad-2", ad_bid_amt=None, use_group=True)  # 그룹입찰 사용
    db.commit()
    r = effective_bid.adgroup_effective_bid(db, "grp-1", 500)  # 그룹입찰 500 > 소재 300
    assert r["effective_bid"] == 500
    assert r["source"] == "group"
    assert r["max_ad_id"] is None
    assert r["has_ad_data"] is True


def test_effective_bid_mixed_ad_wins(db):
    """혼합이지만 소재 bidAmt가 그룹입찰보다 큼 → 실효 = 소재 bidAmt."""
    _ad(db, "grp-1", "p1", ad_id="nad-1", ad_bid_amt=900, use_group=False)
    _ad(db, "grp-1", "p2", ad_id="nad-2", ad_bid_amt=None, use_group=True)
    db.commit()
    r = effective_bid.adgroup_effective_bid(db, "grp-1", 50)
    assert r["effective_bid"] == 900
    assert r["source"] == "ad"
    assert r["max_ad_id"] == "nad-1"


def test_effective_bid_all_true_uses_group_bid(db):
    """소재 전부 useGroupBidAmt=true → 실효입찰 = 그룹입찰."""
    _ad(db, "grp-1", "p1", ad_id="nad-1", ad_bid_amt=None, use_group=True)
    _ad(db, "grp-1", "p2", ad_id="nad-2", ad_bid_amt=None, use_group=True)
    db.commit()
    r = effective_bid.adgroup_effective_bid(db, "grp-1", 350)
    assert r["effective_bid"] == 350
    assert r["source"] == "group"
    assert r["has_ad_data"] is True


def test_effective_bid_no_ad_data_falls_back_to_group(db):
    """소재 데이터 없음(sync 전) → 그룹입찰 폴백(fail-safe·하위호환)."""
    r = effective_bid.adgroup_effective_bid(db, "grp-missing", 640)
    assert r["effective_bid"] == 640
    assert r["has_ad_data"] is False
    assert r["source"] == "group"
    assert r["max_ad_id"] is None
    assert r["ad_count"] == 0


def test_effective_bid_null_columns_fall_back_to_group(db):
    """행은 있으나 입찰 컬럼 전부 NULL(B1 sync 전 기존 행) → 폴백(데이터 없음 취급)."""
    db.add(NaverAdgroupProduct(adgroup_id="grp-1", campaign_id="cmp-shop",
                               mall_product_id="p1", product_name="p1"))  # 입찰 컬럼 미주입 = NULL
    db.commit()
    r = effective_bid.adgroup_effective_bid(db, "grp-1", 200)
    assert r["effective_bid"] == 200
    assert r["has_ad_data"] is False


def test_effective_bid_excludes_stopped_ad(db):
    """정지 소재(ad_user_lock=True)는 서빙 안 하므로 실효입찰 계산에서 제외."""
    _ad(db, "grp-1", "p1", ad_id="nad-1", ad_bid_amt=2000, use_group=False, user_lock=True)  # 정지
    _ad(db, "grp-1", "p2", ad_id="nad-2", ad_bid_amt=800, use_group=False)
    db.commit()
    r = effective_bid.adgroup_effective_bid(db, "grp-1", 50)
    assert r["effective_bid"] == 800  # 정지 소재 2000 제외
    assert r["max_ad_id"] == "nad-2"
    assert r["ad_count"] == 1


def test_effective_bid_all_stopped_falls_back_to_group(db):
    """모든 소재 정지 → 유효 소재 없음 → 그룹입찰 폴백."""
    _ad(db, "grp-1", "p1", ad_id="nad-1", ad_bid_amt=2000, use_group=False, user_lock=True)
    db.commit()
    r = effective_bid.adgroup_effective_bid(db, "grp-1", 120)
    assert r["effective_bid"] == 120
    assert r["has_ad_data"] is False


# ══════════════════════════════════════════════════════════════════
# (2) shopping_pause_candidates — 실효입찰 기준 재정의
# ══════════════════════════════════════════════════════════════════
def test_shopping_pause_stop_loss_threshold_uses_effective_bid(db):
    """스톱로스 임계 = 실효입찰×10. 그룹입찰 50이라 종전 임계 500이면 진입했을 비용(cost=5000)이
    실효입찰 1990(임계 19,900) 기준으론 미달 → 미진입(Fable 관측: 임계 상승=지혈 지연)."""
    _active_campaign(db)
    _shopping_entity(db, "grp-mo", bid_amt=50)
    _ad(db, "grp-mo", "p1", ad_id="nad-1", ad_bid_amt=1990, use_group=False)
    _daily(db, D0, "grp-mo", clk=3, cost=5000, direct=0)  # 무전환, 5000 < 19,900
    db.commit()
    out = diag.shopping_pause_candidates(db, D0, D0, bep_roas=Decimal("1.5"),
                                         correction_factor=Decimal("1.0"))
    assert out == []


def test_shopping_pause_effective_bid_field_and_threshold(db):
    """실효입찰 1990(그룹 50) → board effective_bid=1990·stop_loss_amount=19,900. 무전환·비용
    20,400 ≥ 19,900 → 진입(zero_conv). CPC 1,700 < 5×1990=9,950 → lever_broken 미발동."""
    _active_campaign(db)
    _shopping_entity(db, "grp-mo", bid_amt=50)
    _ad(db, "grp-mo", "p1", ad_id="nad-1", ad_bid_amt=1990, use_group=False)
    _daily(db, D0, "grp-mo", clk=12, cost=20400, direct=0)  # CPC 1700, 무전환
    db.commit()
    out = diag.shopping_pause_candidates(db, D0, D0, bep_roas=Decimal("1.5"),
                                         correction_factor=Decimal("1.0"))
    assert len(out) == 1
    r = out[0]
    assert r["effective_bid"] == 1990
    assert r["current_bid"] == 50           # 그룹입찰은 그대로(target_bid 스텝 기준용)
    assert r["stop_loss_amount"] == 19900
    assert r["reason"] == "zero_conv"
    assert r["chronic_cpc"] == 1700
    assert r["lever_broken"] is False       # 실효≈CPC라 레버끊김 오판 소멸


def test_shopping_pause_lever_broken_flips_false_with_ad_data(db):
    """전환 있는 저ROAS 그룹: 그룹입찰 50 기준이면 lever_broken=True였으나, B1 실효입찰 1990을
    인식하면 CPC 1,700 < 5×1990 → lever_broken=False → nonzero_conv 게이트 미통과 → 미진입."""
    _active_campaign(db)
    _shopping_entity(db, "grp-mo", bid_amt=50)
    _ad(db, "grp-mo", "p1", ad_id="nad-1", ad_bid_amt=1990, use_group=False)
    _daily(db, D0, "grp-mo", clk=12, cost=20400, direct=1000)  # CPC 1700, 전환 있으나 roas≪bep
    db.commit()
    out = diag.shopping_pause_candidates(db, D0, D0, bep_roas=Decimal("1.5"),
                                         correction_factor=Decimal("1.0"))
    assert out == []


def test_shopping_pause_no_ad_data_keeps_group_bid_behavior(db):
    """MO형이나 B1 데이터 없는(sync 전) 그룹 → 종전 동작(그룹입찰 50 기준) 폴백: 임계 500·
    lever_broken=CPC 1700>5×50=250 → 전환 있는 레버끊김으로 진입(하위호환)."""
    _active_campaign(db)
    _shopping_entity(db, "grp-mo", bid_amt=50)  # NaverAdgroupProduct 행 없음(sync 전)
    _daily(db, D0, "grp-mo", clk=12, cost=20400, direct=1000)  # CPC 1700
    db.commit()
    out = diag.shopping_pause_candidates(db, D0, D0, bep_roas=Decimal("1.5"),
                                         correction_factor=Decimal("1.0"))
    assert len(out) == 1
    assert out[0]["reason"] == "lever_broken"
    assert out[0]["lever_broken"] is True
    assert out[0]["effective_bid"] == 50   # 폴백=그룹입찰
    assert out[0]["stop_loss_amount"] == 500


def test_shopping_pause_genuine_lever_broken_retained_when_ad_bid_low(db):
    """진짜 레버끊김(소재 bidAmt도 하한 70인데 CPC 폭등) → 실효입찰 기준으로도 lever_broken
    유지(완료기준 b). 실효=소재 70(at-floor), CPC 800 > 5×70=350 → lever_broken=True."""
    _active_campaign(db)
    _shopping_entity(db, "grp-x", bid_amt=70)
    _ad(db, "grp-x", "p1", ad_id="nad-1", ad_bid_amt=70, use_group=False)  # 소재입찰도 하한
    _daily(db, D0, "grp-x", clk=5, cost=4000, direct=500)  # CPC 800 ≫ 350, roas≪bep
    db.commit()
    out = diag.shopping_pause_candidates(db, D0, D0, bep_roas=Decimal("1.5"),
                                         correction_factor=Decimal("1.0"))
    assert len(out) == 1
    assert out[0]["reason"] == "lever_broken"
    assert out[0]["lever_broken"] is True
    assert out[0]["effective_bid"] == 70


# ══════════════════════════════════════════════════════════════════
# (3) _stop_loss_proposal(쇼핑) — effective_bid 소비(at-floor/레버연결)
# ══════════════════════════════════════════════════════════════════
def test_stop_loss_adgroup_lever_connected_produces_group_bid_down():
    """실효입찰 == 그룹입찰(그룹입찰이 실효 레버) + 하향 여지 → 종전 그룹 bid_down 고삐(불변).
    target_bid = 그룹 스텝(200→170)."""
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "cost": 5000, "conv_amt": 0,
           "current_bid": 200, "effective_bid": 200, "stop_loss_amount": 2000}
    p = proposal_writer._stop_loss_proposal(row, target_type="adgroup", manual_bid=True)
    assert p["proposal_type"] == "bid_down"
    assert p["target_bid"] == 170  # 그룹 스텝(B2는 소재입찰 안 씀)


def test_stop_loss_adgroup_lever_disconnected_skips_group_bid_down():
    """실효입찰 ≫ 그룹입찰(소재-레벨 입찰이 그룹입찰 우회 = 레버 미연결) → 그룹 bid_down은
    무의미하므로 제안하지 않고 lever/pause 판정에 위임. lever_broken/floor_bleed 미태깅이면
    바닥 대기(None) — 소재-레벨 제어는 B3."""
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "cost": 20400, "conv_amt": 0,
           "current_bid": 200, "effective_bid": 800, "stop_loss_amount": 8000}
    p = proposal_writer._stop_loss_proposal(row, target_type="adgroup", manual_bid=True)
    assert p is None  # 그룹 bid_down 200→170은 무의미(소재가 800 사용) → 제안 안 함


def test_stop_loss_adgroup_lever_disconnected_but_lever_broken_pauses():
    """레버 미연결이어도 진짜 lever_broken 태깅이면 예외② 터미널 pause(안전망 유지)."""
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "cost": 20400, "conv_amt": 0,
           "current_bid": 200, "effective_bid": 800, "stop_loss_amount": 8000,
           "lever_broken": True, "chronic_cpc": 5000}
    p = proposal_writer._stop_loss_proposal(row, target_type="adgroup", manual_bid=True)
    assert p["proposal_type"] == "pause"
    assert p["target_lock"] is True


def test_stop_loss_adgroup_missing_effective_bid_falls_back_to_group():
    """effective_bid 미제공(하위호환) → 그룹입찰 기준(종전 동작). 하향 여지 있으면 bid_down."""
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "cost": 2500, "conv_amt": 0,
           "current_bid": 200, "stop_loss_amount": 2000}
    p = proposal_writer._stop_loss_proposal(row, target_type="adgroup", manual_bid=True)
    assert p["proposal_type"] == "bid_down"
    assert p["target_bid"] == 170


# ══════════════════════════════════════════════════════════════════
# GATE P3-2 — adgroup_effective_bids 배치(1쿼리) = 단건과 동일 결과
# ══════════════════════════════════════════════════════════════════
def test_effective_bids_batch_matches_single(db):
    """배치 조회가 단건 API와 동일 결과 — 보드 루프 N+1 제거용(내부 공유 파생 로직)."""
    _ad(db, "grp-a", "p1", ad_id="nad-a1", ad_bid_amt=800, use_group=False)
    _ad(db, "grp-a", "p2", ad_id="nad-a2", ad_bid_amt=1200, use_group=False)
    _ad(db, "grp-b", "p1", ad_id="nad-b1", ad_bid_amt=None, use_group=True)
    db.commit()
    group_bids = {"grp-a": 50, "grp-b": 350, "grp-missing": 640}
    batch = effective_bid.adgroup_effective_bids(db, group_bids)
    assert set(batch) == {"grp-a", "grp-b", "grp-missing"}
    for aid, gbid in group_bids.items():
        assert batch[aid] == effective_bid.adgroup_effective_bid(db, aid, gbid)
    assert batch["grp-a"]["effective_bid"] == 1200
    assert batch["grp-b"]["source"] == "group"
    assert batch["grp-missing"]["has_ad_data"] is False


# ══════════════════════════════════════════════════════════════════
# GATE P2-1 — 레버 미연결 유닛의 zero_conv 증거 창 = 만성 7일(침묵 대역 해소)
# ══════════════════════════════════════════════════════════════════
def test_shopping_pause_disconnected_uses_chronic_7d_window(db):
    """레버 미연결(source='ad', 실효 1990·그룹 50) 무전환 유닛: DL1 3일 폴백이면 일 3,000원
    출혈은 9,000 < 임계 19,900으로 영구 침묵 — 만성 7일 창으로 21,000 ≥ 19,900 → 진입.
    window_days도 7일 창 기준(floor_bleed 밸브 판정 정합)."""
    _active_campaign(db)
    _shopping_entity(db, "grp-mo", bid_amt=50)
    _ad(db, "grp-mo", "p1", ad_id="nad-1", ad_bid_amt=1990, use_group=False)
    for i in range(7):  # D_TO-6 .. D_TO — 일 3,000원 무전환 출혈
        _daily(db, D_TO - timedelta(days=i), "grp-mo", clk=2, cost=3000, direct=0)
    db.commit()
    out = diag.shopping_pause_candidates(db, D0, D_TO, bep_roas=Decimal("1.5"),
                                         correction_factor=Decimal("1.0"))
    assert len(out) == 1
    r = out[0]
    assert r["reason"] == "zero_conv"
    assert r["cost"] == 21000            # 7일 창 합산(3일 폴백이면 9,000)
    assert r["stop_loss_amount"] == 19900
    assert r["window_days"] == 7          # 밸브 판정도 7일 창 기준
    assert r["floor_bleed"] is True


def test_shopping_pause_disconnected_three_days_only_insufficient(db):
    """같은 미연결 유닛이나 출혈이 3일치(9,000)뿐 → 실효입찰 10클릭 증거 미달 = 미진입
    (성급 사살 금지가 정당한 케이스)."""
    _active_campaign(db)
    _shopping_entity(db, "grp-mo", bid_amt=50)
    _ad(db, "grp-mo", "p1", ad_id="nad-1", ad_bid_amt=1990, use_group=False)
    for i in range(3):  # D_TO-2 .. D_TO만
        _daily(db, D_TO - timedelta(days=i), "grp-mo", clk=2, cost=3000, direct=0)
    db.commit()
    out = diag.shopping_pause_candidates(db, D0, D_TO, bep_roas=Decimal("1.5"),
                                         correction_factor=Decimal("1.0"))
    assert out == []


def test_shopping_pause_disconnected_same_day_ad_change_held(db):
    """codex 소급[P2] 2026-07-20: 미연결 유닛의 소재입찰을 **오늘** 바꿨으면(zero-conv 보류가
    adgroup 변경일이 아니라 소재 변경일 기준이어야) 귀속 지연 완충으로 당일 zero_conv 미진입.
    종전엔 last_change_date(adgroup-레벨, 이 유닛엔 None)만 봐서 당일 즉시 후보 진입했다."""
    _active_campaign(db)
    _shopping_entity(db, "grp-mo", bid_amt=50)
    _ad(db, "grp-mo", "p1", ad_id="nad-1", ad_bid_amt=1990, use_group=False)
    for i in range(7):
        _daily(db, D_TO - timedelta(days=i), "grp-mo", clk=2, cost=3000, direct=0)
    # 오늘(D_TO) 소재입찰 변경 성공 기록(entity_type='ad'·update_bid·KST-naive changed_at)
    db.add(NaverChangeLog(
        entity_type="ad", entity_id="nad-1", campaign_id="cmp-shop", action="update_bid",
        after_value='{"bidAmt": 1690}', dry_run=False,
        changed_at=datetime(D_TO.year, D_TO.month, D_TO.day, 9, 0, 0),
    ))
    db.commit()
    out = diag.shopping_pause_candidates(db, D0, D_TO, bep_roas=Decimal("1.5"),
                                         correction_factor=Decimal("1.0"))
    assert out == []  # 변경 당일 하루 창 — 다음날부터 정상 판정


def test_shopping_pause_disconnected_prior_day_ad_change_not_held(db):
    """소재입찰 변경이 어제(D_TO-1)면 보류 대상 아님 — 창은 변경일 이후로 절체돼 판정 지속.
    (창 2일치 비용 6,000 < 임계라 이 케이스는 증거 미달 미진입 — 보류가 아니라 임계 게이트.)"""
    _active_campaign(db)
    _shopping_entity(db, "grp-mo", bid_amt=50)
    _ad(db, "grp-mo", "p1", ad_id="nad-1", ad_bid_amt=990, use_group=False)  # 임계 9,900
    for i in range(7):
        _daily(db, D_TO - timedelta(days=i), "grp-mo", clk=5, cost=6000, direct=0)
    prev = D_TO - timedelta(days=1)
    db.add(NaverChangeLog(
        entity_type="ad", entity_id="nad-1", campaign_id="cmp-shop", action="update_bid",
        after_value='{"bidAmt": 990}', dry_run=False,
        changed_at=datetime(prev.year, prev.month, prev.day, 9, 0, 0),
    ))
    db.commit()
    out = diag.shopping_pause_candidates(db, D0, D_TO, bep_roas=Decimal("1.5"),
                                         correction_factor=Decimal("1.0"))
    assert len(out) == 1  # 어제+오늘 2일 창 12,000 ≥ 임계 9,900 → 보류 없이 정상 진입
    assert out[0]["reason"] == "zero_conv"


def test_shopping_pause_connected_keeps_dl1_window(db):
    """레버 연결 유닛(source='group')은 기존 DL1 행동 창(3일 폴백) 유지 — 그룹입찰 300·임계
    3,000, 일 900원 × 7일이면 3일 창 2,700 < 3,000 → 미진입(7일 창이면 6,300 ≥ 3,000으로
    진입했을 것 — DL1 시점 정합이 이 유닛엔 여전히 유효: 우리가 입찰을 바꾸는 유닛이라
    변경 후 창 절체가 필요)."""
    _active_campaign(db)
    _shopping_entity(db, "grp-ok", bid_amt=300)
    _ad(db, "grp-ok", "p1", ad_id="nad-1", ad_bid_amt=None, use_group=True)  # 그룹입찰 사용=연결
    for i in range(7):
        _daily(db, D_TO - timedelta(days=i), "grp-ok", clk=3, cost=900, direct=0)
    db.commit()
    out = diag.shopping_pause_candidates(db, D0, D_TO, bep_roas=Decimal("1.5"),
                                         correction_factor=Decimal("1.0"))
    assert out == []


# ══════════════════════════════════════════════════════════════════
# GATE P2-2② — proposal_writer 밴드 보드(shopping_group_bep/growth)의 미연결 그룹 억제
# ══════════════════════════════════════════════════════════════════
def _sim(direction="down", ceiling=100, recommended=100, current_bid=None):
    return {
        "recommended_bid": recommended, "economic_ceiling": ceiling, "rank_bid": None,
        "direction": direction, "basis": "economic_ceiling", "current_bid": current_bid,
        "expected_effect_text": "테스트 expected_effect",
        "capability_flags": {"estimate_ok": False, "performance_estimate_ok": False,
                             "is_new_or_growth": False, "keyword_sample_thin": False},
    }


def _bep_board_row(**overrides):
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-d", "cost": 8000,
           "conv_amt": 1000, "roas_naver": 0.125, "roas_corrected": 0.125}
    row.update(overrides)
    return row


def _diagnosis(**boards):
    return {"window": {}, "correction_factor": {"factor": 1.0}, "boards": boards}


def test_build_shopping_bep_skips_disconnected_group(db):
    """미연결 그룹(source='ad')에 그룹입찰 bid_down 제안 금지 — 지출 무효 + DL1 행동 창
    리셋으로 P2-1 그물 지연 + changes_today 슬롯 낭비. 제안 미생성.

    D-NAO-125(2026-07-29): 이 테스트가 서있던 전제(스코프=AD_BID_CANARY_CAMPAIGNS 상수)가
    킬스위치(AD_BID_ROUTING_ENABLED)로 대체됐다 — 기본 상태(킬스위치 on)에서는 미연결 그룹도
    이제 ad-레벨 bid_down으로 라우팅된다(의도된 변경, test_naver_auto_operator.py의 신규
    회귀가 그 경로를 커버). 이 테스트는 "킬스위치 off일 때의 구제 경로"로 의미를 좁혀 보존한다.
    """
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    _ad(db, "grp-d", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)  # 미연결
    db.commit()
    diagnosis = _diagnosis(shopping_group_bep=[_bep_board_row()])
    with patch.object(auto_operator, "AD_BID_ROUTING_ENABLED", False):
        out = proposal_writer.build(
            db, diagnosis,
            bid_sims={("adgroup", "grp-d"): _sim(direction="down", current_bid=200)},
            as_of=D_TO,
        )
    assert out == []


def test_build_shopping_bep_connected_group_still_proposes(db):
    """연결 그룹(전 소재 useGroupBidAmt=true = 그룹입찰이 실효 레버) → 종전 bid_down 유지."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    _ad(db, "grp-d", "p1", ad_id="nad-1", ad_bid_amt=None, use_group=True)  # 연결
    db.commit()
    diagnosis = _diagnosis(shopping_group_bep=[_bep_board_row()])
    out = proposal_writer.build(
        db, diagnosis,
        bid_sims={("adgroup", "grp-d"): _sim(direction="down", current_bid=200)},
        as_of=D_TO,
    )
    assert len(out) == 1
    assert out[0]["proposal_type"] == "bid_down"
    assert out[0]["target_id"] == "grp-d"


def test_build_shopping_bep_no_ad_data_falls_back_to_propose(db):
    """B1 데이터 없음(sync 전) → 폴백 = 기존 동작(그룹입찰 유효 가정, 제안 생성)."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(shopping_group_bep=[_bep_board_row()])
    out = proposal_writer.build(
        db, diagnosis,
        bid_sims={("adgroup", "grp-d"): _sim(direction="down", current_bid=200)},
        as_of=D_TO,
    )
    assert len(out) == 1


def test_build_shopping_growth_skips_disconnected_group(db):
    """성장(bid_up)도 대칭 — 미연결 그룹의 그룹입찰 bid_up은 지출·노출에 무효, 제안 미생성."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    _ad(db, "grp-g", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)  # 미연결
    db.commit()
    diagnosis = _diagnosis(shopping_group_growth=[
        _bep_board_row(adgroup_id="grp-g", conv_amt=40000, roas_naver=5.0, roas_corrected=5.0),
    ])
    out = proposal_writer.build(
        db, diagnosis,
        # recommended > current×1.15라 종전 코드면 클램프 230원 bid_up이 생성됐을 조건
        bid_sims={("adgroup", "grp-g"): _sim(direction="up", recommended=1300, ceiling=1300,
                                             current_bid=200)},
        as_of=D_TO,
    )
    assert out == []


# ══════════════════════════════════════════════════════════════════
# GATE P2-2① — auto_operator 시간당 레인의 미연결 그룹 억제
# ══════════════════════════════════════════════════════════════════
CAMP = "cmp-shop"
TODAY = date(2026, 7, 20)
NOW = datetime(2026, 7, 20, 8, 50, 0)


def _seed_hourly_shopping(db, *, adgroup_id="grp-hot"):
    """auto_operate ours SHOPPING 캠페인 + 핫셋 자격(정착창 clk≥10) adgroup + 신선 스냅샷."""
    db.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="ours", auto_operate=True))
    db.add(NaverEntity(entity_type="campaign", entity_id=CAMP, campaign_id=CAMP, status="on"))
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=CAMP,
                       campaign_id=CAMP, campaign_type="SHOPPING", status="on"))
    db.add(NaverHourlySnapshot(snapshot_at=NOW, ad_date=TODAY, snapshot_hour=23,
                               campaign_id=CAMP, campaign_type="", cost=0, clk=0, imp=0))
    window_from, _ = auto_operator._settlement_window(TODAY)
    db.add(NaverAdDaily(ad_date=window_from, campaign_id=CAMP, campaign_type="SHOPPING",
                        adgroup_id=adgroup_id, keyword_id="", imp=200, clk=20, cost=2000))
    db.commit()


def _overheat_curve():
    """밴드 DOWN 판정 곡선 — ★D-NAO-66으로 과열밴드 DOWN(rank<2.5) 폐지됨에 따라 CPC 급등으로
    DOWN을 유발한다(정착창 baseline CPC 100원 = _seed_hourly_shopping cost2000/clk20 → 당일
    CPC 250원 > 100×2). 이 파일 테스트는 'DOWN 방향의 소재-레벨 라우팅'이 관심사라, DOWN을
    내는 트리거는 CPC 급등이든 순위든 무방(rank는 판정에서 무관해짐)."""
    return [
        {"hour": 6, "imp": 15, "clk": 2, "cost": 500, "avg_rank": 2.0},
        {"hour": 7, "imp": 15, "clk": 2, "cost": 500, "avg_rank": 2.0},
        {"hour": 8, "imp": 15, "clk": 2, "cost": 500, "avg_rank": 2.0},
    ]


def test_hourly_lane_holds_band_down_for_disconnected_group(db):
    """미연결 그룹(실효=소재 800·그룹 200) 밴드 DOWN → 집행 skip + held 사유 '[레버 미연결]'.
    그룹입찰 하향은 지출 무효이면서 DL1 행동 창만 리셋(P2-1 그물 지연)·슬롯 낭비.

    D-NAO-125(2026-07-29): 기본 상태(킬스위치 on)에서는 이 시나리오가 오히려 ad-레벨
    bid_down 인라인 자동 실행으로 열린다(의도된 변경 — test_naver_auto_operator.py 신규
    회귀가 그 경로를 커버). 이 테스트는 킬스위치 off로 좁혀 종전 hold 회귀를 보존한다."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)  # 미연결
    db.commit()
    with patch.object(auto_operator, "AD_BID_ROUTING_ENABLED", False), \
         patch.object(auto_operator.naver_sa_writer, "_get_adgroup",
                      return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=NOW, fetch_intraday=lambda tid, d: _overheat_curve(),
        )
    mock_exec.assert_not_called()
    assert result["approved"] == 0
    reasons = [h["reason"] for h in result["held"]]
    assert any("[레버 미연결]" in r for r in reasons)
    assert any("800" in r for r in reasons)  # 실효=소재입찰 명기
    assert any("B3 대기" in r for r in reasons)


def test_hourly_lane_band_down_proceeds_for_connected_group(db):
    """연결 그룹(전 소재 useGroupBidAmt=true) → 종전 밴드 DOWN 그대로 집행(회귀 0)."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=None, use_group=True)  # 연결
    db.commit()
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup",
                      return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=NOW, fetch_intraday=lambda tid, d: _overheat_curve(),
        )
    mock_exec.assert_called_once()
    assert result["approved"] == 1
    saved = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert saved.proposal_type == "bid_down"


def test_hourly_lane_band_down_proceeds_when_no_ad_data(db):
    """B1 데이터 없음(sync 전) → 폴백 = 기존 동작(그룹입찰 유효 가정, 집행)."""
    _seed_hourly_shopping(db)
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup",
                      return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=NOW, fetch_intraday=lambda tid, d: _overheat_curve(),
        )
    mock_exec.assert_called_once()
    assert result["approved"] == 1


# ══════════════════════════════════════════════════════════════════
# GATE P3-1 — floor_bleed 밸브 사유문: 레버 미연결 경유 분기(정직 경계)
# ══════════════════════════════════════════════════════════════════
def test_floor_bleed_valve_rationale_disconnected_variant():
    """레버 미연결(effective_source='ad') 경유 floor_bleed pause 사유문은 '바닥'이 아니라
    '레버 미연결 대기'로 명시 — 입찰이 바닥이라 대기한 게 아니라 그룹입찰이 무효라 대기한
    것(사실과 다른 사유문 금지)."""
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "cost": 21000, "conv_amt": 0,
           "current_bid": 50, "effective_bid": 1990, "effective_source": "ad",
           "stop_loss_amount": 19900, "floor_bleed": True, "window_days": 7}
    p = proposal_writer._stop_loss_proposal(row, target_type="adgroup", manual_bid=True)
    assert p["proposal_type"] == "pause"
    assert "레버 미연결" in p["rationale"]
    assert "1990" in p["rationale"]      # 실효 소재입찰 명기
    assert "그룹입찰" in p["rationale"]   # 그룹입찰 무효 명시
    assert "7일" in p["rationale"]
    assert "바닥" not in p["rationale"]   # 바닥 대기가 아님(정직 경계)


def test_floor_bleed_valve_rationale_connected_unchanged():
    """레버 연결(effective_source='group') at-floor floor_bleed는 종전 '바닥' 사유문 유지."""
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "cost": 2400, "conv_amt": 0,
           "current_bid": 70, "effective_bid": 70, "effective_source": "group",
           "stop_loss_amount": 700, "floor_bleed": True, "window_days": 3}
    p = proposal_writer._stop_loss_proposal(row, target_type="adgroup", manual_bid=True)
    assert p["proposal_type"] == "pause"
    assert "바닥" in p["rationale"]
    assert "레버 미연결" not in p["rationale"]
