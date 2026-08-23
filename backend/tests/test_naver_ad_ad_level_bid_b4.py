# test_naver_ad_ad_level_bid_b4.py — 스프린트 B Phase 4(B4: 예외② 해소 + 재개 흐름, D-NAO-65)
# 커버(GATE P2 3건 반영):
#   (1) proposal_writer._stop_loss_proposal — lever_broken 조건부 은퇴(카나리): 하향 여지 있는
#       lever_broken 유닛은 pause 대신 소재-레벨 bid_down / 소재 at-floor lever_broken은 pause 유지
#       / 비카나리는 기존 pause(회귀 0)
#   (2) account_diagnosis.shopping_lever_resume_candidates — 우리가 pause한 미연결(MO형) 그룹의
#       "바닥에서 재개" 분기(GATE P2-1: 실효입찰 ≤ 바닥 70 → resume / 그 위 → bid_down_first)
#       + flip-flop 쿨다운(GATE P2-3①: 재pause 3일 내 제외)
#   (3) proposal_writer.build() 라우팅 — 카나리 한정·ML 사전 제외(GATE P2-3②)·순서강제(ad
#       bid_down pending 공존 시 resume 금지)·rationale 정직화(GATE P2-1 c: Confirm 대기 명시)
#   (4) delegation_gate·expert 브리핑 — 카나리 캠페인 전면 Confirm-only(GATE P2-2)
#   (5) diagnosis 하니스 배선(신규 보드 노출)
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverProductBep,
    NaverProposal,
)
from app.services.naver_ad import account_diagnosis as diag
from app.services.naver_ad import auto_operator, delegation_gate, proposal_writer

CAMP = "cmp-ours"
TODAY = date(2026, 7, 20)


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


# ══════════════════════════════════════════════════════════════════
# (1) item 1 — lever_broken 조건부 은퇴(_stop_loss_proposal 직접)
# ══════════════════════════════════════════════════════════════════
def _lever_broken_row(**o):
    row = {"campaign_id": CAMP, "adgroup_id": "grp-mo", "cost": 20400, "conv_amt": 1500,
           "current_bid": 200, "effective_bid": 800, "effective_source": "ad",
           "effective_ad_id": "nad-1", "stop_loss_amount": 8000, "clk": 12,
           "lever_broken": True, "chronic_cpc": 5000}
    row.update(o)
    return row


def test_stop_loss_canary_lever_broken_steppable_routes_to_ad_bid_down():
    """[B4 item 1] 카나리 + lever_broken이지만 소재 실효입찰에 하향 여지(800→680)가 있으면
    pause 대신 소재-레벨 bid_down으로 라우팅(예외② leash화) — B3의 `not lever_broken` 제외
    조건이 카나리에서 해제됐다."""
    p = proposal_writer._stop_loss_proposal(
        _lever_broken_row(), target_type="adgroup", manual_bid=True, ad_bid_canary=True,
    )
    assert p["proposal_type"] == "bid_down"
    assert p["target_type"] == "ad"
    assert p["target_id"] == "nad-1"
    assert p["adgroup_id"] == "grp-mo"
    assert p["target_bid"] == 680


def test_stop_loss_canary_lever_broken_at_floor_still_pauses():
    """[B4 item 1] 소재 실효입찰까지 하한(70)인 진짜 레버불능(CPC 폭등)은 하향 여지 없음 →
    소재 스텝 소실 → pause 잔존(진짜 레버불능만 터미널 pause)."""
    p = proposal_writer._stop_loss_proposal(
        _lever_broken_row(effective_bid=70, stop_loss_amount=700), target_type="adgroup",
        manual_bid=True, ad_bid_canary=True,
    )
    assert p["proposal_type"] == "pause"


def test_stop_loss_non_canary_lever_broken_pauses():
    """[B4 item 1] 비카나리는 은퇴 안 함 — lever_broken은 기존 pause 유지(회귀 0)."""
    p = proposal_writer._stop_loss_proposal(
        _lever_broken_row(), target_type="adgroup", manual_bid=True, ad_bid_canary=False,
    )
    assert p["proposal_type"] == "pause"


# ══════════════════════════════════════════════════════════════════
# (2) item 2/5 — shopping_lever_resume_candidates: 바닥 재개 분기(GATE P2-1)
#     + flip-flop 쿨다운(GATE P2-3①)
# ══════════════════════════════════════════════════════════════════
def _off_shop_group(db, adgroup_id, *, bid_amt=50, campaign_id=CAMP, status="off",
                     pc_bid_weight=None, mobile_bid_weight=None):
    # ★D-NAO-218(M2-b2) 픽스처 확장 — pc/mobile_bid_weight를 실을 수 있는 모양으로(적대 리뷰
    # 1R P1 지적: 기존 모양은 가중치를 아예 못 만들어 회귀를 원리적으로 못 잡았다).
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
                       campaign_id=campaign_id, campaign_type="SHOPPING", name=adgroup_id,
                       status=status, bid_amt=bid_amt,
                       pc_bid_weight=pc_bid_weight, mobile_bid_weight=mobile_bid_weight))


def _mat(db, adgroup_id, mall_product_id, *, ad_id, ad_bid_amt, use_group=False,
         campaign_id=CAMP):
    db.add(NaverAdgroupProduct(
        adgroup_id=adgroup_id, campaign_id=campaign_id, mall_product_id=mall_product_id,
        product_name=mall_product_id, ad_id=ad_id, ad_bid_amt=ad_bid_amt,
        use_group_bid_amt=use_group, ad_user_lock=False,
    ))


def _bep(db, mall_product_id, *, selling_price=10000, target_roas="2.5"):
    db.add(NaverProductBep(
        channel_id=6, channel_product_id=mall_product_id, product_name=mall_product_id,
        selling_price=Decimal(str(selling_price)), cost_price=Decimal("4000"),
        commission_rate=Decimal("0.05"), logistics_cost=Decimal("1000"),
        contribution_margin=Decimal("4000"), bep_roas=Decimal("2.0"),
        aggressiveness="standard", target_roas=Decimal(str(target_roas)), has_cost=True,
    ))


def _lock_log(db, adgroup_id, *, locked, proposal_id=1, campaign_id=CAMP, changed_at=None):
    db.add(NaverChangeLog(
        entity_type="adgroup", entity_id=adgroup_id, campaign_id=campaign_id,
        action="set_user_lock", proposal_id=proposal_id, dry_run=False,
        changed_at=changed_at or datetime(2026, 7, 20, 8, 50),
        after_value=json.dumps({"userLock": locked}),
    ))


def test_lever_resume_classifies_bid_down_first_above_floor(db):
    """[GATE P2-1] 소재 실효입찰(800) > 바닥(70, _MIN_BID 규약) → 'bid_down_first'(바닥까지
    단계 하향 후 재개 — 800원 그대로 재개하면 재출혈. MO 실사례: CVR 0.9% 기준 현실 BEP CPC
    ~78원이라 판매가/target_roas류 안전선은 실질 무필터였다)."""
    _off_shop_group(db, "grp-mo")
    _mat(db, "grp-mo", "cp-1", ad_id="nad-1", ad_bid_amt=800)
    _lock_log(db, "grp-mo", locked=True)
    db.commit()
    out = diag.shopping_lever_resume_candidates(db, date_to=TODAY)
    assert len(out) == 1
    assert out[0]["adgroup_id"] == "grp-mo"
    assert out[0]["action"] == "bid_down_first"
    assert out[0]["effective_bid"] == 800
    assert out[0]["effective_ad_id"] == "nad-1"
    assert "safe_cpc" not in out[0]  # 무필터 안전선 코드 제거(정직 경계)


def test_lever_resume_classifies_resume_at_floor(db):
    """[GATE P2-1] 소재 실효입찰(70) = 바닥 → 'resume'(최소 노출로 증거 축적 재개 준비 완료)."""
    _off_shop_group(db, "grp-mo")
    _mat(db, "grp-mo", "cp-1", ad_id="nad-1", ad_bid_amt=70)
    _lock_log(db, "grp-mo", locked=True)
    db.commit()
    out = diag.shopping_lever_resume_candidates(db, date_to=TODAY)
    assert len(out) == 1
    assert out[0]["action"] == "resume"
    assert out[0]["effective_bid"] == 70


def test_lever_resume_classifies_above_floor_using_nominal_not_device_weighted(db):
    """★P1 회귀 잠금(적대 리뷰 1R FAIL 재현분, D-NAO-218) — 소재 실효입찰(명목 140원)에
    기기가중치 50/50이 걸려 있어도 분기는 **명목** 기준(action='bid_down_first')이어야 한다.
    재정의판(초판 결함)이면 기기가중 적용값(70원=바닥)을 써서 'resume'으로 잘못 분류돼
    재개 준비(하향) 단계를 건너뛰고 800원급 소재가 그대로 재개될 수 있었다."""
    _off_shop_group(db, "grp-mo", pc_bid_weight=50, mobile_bid_weight=50)
    _mat(db, "grp-mo", "cp-1", ad_id="nad-1", ad_bid_amt=140)
    _lock_log(db, "grp-mo", locked=True)
    db.commit()
    out = diag.shopping_lever_resume_candidates(db, date_to=TODAY)
    assert len(out) == 1
    assert out[0]["action"] == "bid_down_first"  # ★140원(명목)은 바닥이 아니다
    assert out[0]["effective_bid"] == 140


def test_lever_resume_no_product_bep_still_classified(db):
    """[GATE P2-1] 분기 기준이 바닥(guardrail _MIN_BID)이라 상품 BEP 부재와 무관 —
    판매가/target_roas 안전선 의존 제거를 잠근다."""
    _off_shop_group(db, "grp-mo")
    _mat(db, "grp-mo", "cp-1", ad_id="nad-1", ad_bid_amt=70)
    # NaverProductBep 미적재
    _lock_log(db, "grp-mo", locked=True)
    db.commit()
    out = diag.shopping_lever_resume_candidates(db, date_to=TODAY)
    assert len(out) == 1
    assert out[0]["action"] == "resume"


def test_lever_resume_excludes_connected_group(db):
    """레버 연결(전 소재 useGroupBidAmt=true = source='group')은 소재-레벨 재개 대상 아님."""
    _off_shop_group(db, "grp-conn")
    _mat(db, "grp-conn", "cp-1", ad_id="nad-1", ad_bid_amt=None, use_group=True)
    _lock_log(db, "grp-conn", locked=True)
    db.commit()
    assert diag.shopping_lever_resume_candidates(db, date_to=TODAY) == []


def test_lever_resume_excludes_manual_pause_no_proposal_id(db):
    """수동/외부 정지(proposal_id 없음)는 우리 재개 판단 금지 — 제외."""
    _off_shop_group(db, "grp-mo")
    _mat(db, "grp-mo", "cp-1", ad_id="nad-1", ad_bid_amt=70)
    _lock_log(db, "grp-mo", locked=True, proposal_id=None)
    db.commit()
    assert diag.shopping_lever_resume_candidates(db, date_to=TODAY) == []


def test_lever_resume_excludes_currently_on_group(db):
    """현재 on 그룹은 재개 대상 아님(off만) — shopping_pause_candidates 소관."""
    _off_shop_group(db, "grp-on", status="on")
    _mat(db, "grp-on", "cp-1", ad_id="nad-1", ad_bid_amt=70)
    _lock_log(db, "grp-on", locked=True)
    db.commit()
    assert diag.shopping_lever_resume_candidates(db, date_to=TODAY) == []


def test_lever_resume_excludes_repause_within_cooldown(db):
    """[GATE P2-3①] flip-flop 방지 — pause→resume→재pause 이력이 있고 재pause가 최근 3일 내
    (_LEVER_RESUME_COOLDOWN_DAYS)면 제외(D-NAO-19 계승: 재개하자마자 또 재개 제안 금지)."""
    _off_shop_group(db, "grp-ff")
    _mat(db, "grp-ff", "cp-1", ad_id="nad-1", ad_bid_amt=70)
    _lock_log(db, "grp-ff", locked=True, changed_at=datetime(2026, 7, 10, 8, 50))
    _lock_log(db, "grp-ff", locked=False, changed_at=datetime(2026, 7, 12, 9, 0))  # 우리 재개
    _lock_log(db, "grp-ff", locked=True, changed_at=datetime(2026, 7, 19, 8, 50))  # 재pause D-1
    db.commit()
    assert diag.shopping_lever_resume_candidates(db, date_to=TODAY) == []


def test_lever_resume_includes_repause_after_cooldown(db):
    """[GATE P2-3①] 재pause가 3일 이상 경과했으면 다시 후보(쿨다운 해소)."""
    _off_shop_group(db, "grp-ff")
    _mat(db, "grp-ff", "cp-1", ad_id="nad-1", ad_bid_amt=70)
    _lock_log(db, "grp-ff", locked=True, changed_at=datetime(2026, 7, 10, 8, 50))
    _lock_log(db, "grp-ff", locked=False, changed_at=datetime(2026, 7, 12, 9, 0))
    _lock_log(db, "grp-ff", locked=True, changed_at=datetime(2026, 7, 16, 8, 50))  # D-4
    db.commit()
    out = diag.shopping_lever_resume_candidates(db, date_to=TODAY)
    assert len(out) == 1
    assert out[0]["adgroup_id"] == "grp-ff"


def test_lever_resume_first_pause_recent_not_cooled(db):
    """[GATE P2-3①] 최초 pause(재pause 아님 — 선행 resume 없음)는 당일이어도 쿨다운 미적용
    (쿨다운은 flip-flop 이력에만)."""
    _off_shop_group(db, "grp-first")
    _mat(db, "grp-first", "cp-1", ad_id="nad-1", ad_bid_amt=70)
    _lock_log(db, "grp-first", locked=True, changed_at=datetime(2026, 7, 20, 8, 50))  # 당일
    db.commit()
    out = diag.shopping_lever_resume_candidates(db, date_to=TODAY)
    assert len(out) == 1


# ══════════════════════════════════════════════════════════════════
# (3) build() 라우팅 — 카나리·ML 사전 제외·순서강제·rationale 정직화
# ══════════════════════════════════════════════════════════════════
def _diagnosis(**boards):
    return {"window": {}, "correction_factor": {"factor": 1.0, "factor_low": 1.0, "factor_high": 1.0, "factor_point": 1.0}, "boards": boards}


def _resume_board_row(action, **o):
    row = {"campaign_id": CAMP, "adgroup_id": "grp-mo",
           "effective_bid": 70 if action == "resume" else 800,
           "effective_ad_id": "nad-1", "action": action,
           "paused_at": "2026-07-20T08:50:00"}
    row.update(o)
    return row


def _ours(db, campaign_id=CAMP):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer="ours"))
    db.commit()


def test_build_lever_resume_bid_down_first_canary_generates_ad_bid_down(db):
    """[B4 item 5] 카나리 + 수동입찰 + bid_down_first → 소재-레벨 bid_down 제안(Confirm-only
    shape: target_type='ad')로 재개 준비. 소재 800 하향 스텝 680."""
    _ours(db)
    diagnosis = _diagnosis(shopping_lever_resume_candidates=[_resume_board_row("bid_down_first")])
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({CAMP})), \
         patch.object(proposal_writer, "_adgroup_is_manual_bid", return_value=True):
        out = proposal_writer.build(db, diagnosis, as_of=TODAY)
    assert len(out) == 1
    assert out[0]["proposal_type"] == "bid_down"
    assert out[0]["target_type"] == "ad"
    assert out[0]["target_id"] == "nad-1"
    assert out[0]["adgroup_id"] == "grp-mo"
    assert out[0]["target_bid"] == 680


def test_build_lever_resume_resume_canary_generates_resume(db):
    """[B4 item 2] 카나리 + 수동입찰 + resume(바닥, ad bid_down pending 없음) → resume 제안.
    [GATE P2-1 c] rationale 정직화 — '자동 감시' 부재, 교정 제안 = 콘솔 Confirm 대기 명시."""
    _ours(db)
    diagnosis = _diagnosis(shopping_lever_resume_candidates=[_resume_board_row("resume")])
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({CAMP})), \
         patch.object(proposal_writer, "_adgroup_is_manual_bid", return_value=True):
        out = proposal_writer.build(db, diagnosis, as_of=TODAY)
    assert len(out) == 1
    assert out[0]["proposal_type"] == "resume"
    assert out[0]["target_type"] == "adgroup"
    assert out[0]["target_id"] == "grp-mo"
    assert out[0]["target_lock"] is False
    assert "자동 감시" not in out[0]["rationale"]  # GATE P2-1 c: 카나리 체제에선 거짓
    assert "Confirm 대기" in out[0]["rationale"]


def test_build_lever_resume_blocked_by_pending_ad_bid_down(db):
    """[B4 item 4] 순서강제 — 같은 그룹에 ad bid_down pending이 있으면 resume 제안 금지
    (하향 먼저, 800원 그대로 재개 방지)."""
    _ours(db)
    db.add(NaverProposal(
        proposal_type="bid_down", target_type="ad", target_id="nad-1", campaign_id=CAMP,
        adgroup_id="grp-mo", rationale="[소재입찰] 준비", expected_effect="x",
        status="pending", target_bid=680,
    ))
    db.commit()
    diagnosis = _diagnosis(shopping_lever_resume_candidates=[_resume_board_row("resume")])
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({CAMP})), \
         patch.object(proposal_writer, "_adgroup_is_manual_bid", return_value=True):
        out = proposal_writer.build(db, diagnosis, as_of=TODAY)
    assert [p for p in out if p["proposal_type"] == "resume"] == []


@pytest.mark.parametrize("manual_bid", [False, None])
def test_build_lever_resume_ml_or_unknown_group_skipped(db, manual_bid):
    """[GATE P2-3②] 부모 그룹이 ML 자동입찰(False) 또는 판정불가(None)면 resume·bid_down_first
    모두 미생성 — 예외① 부류는 소재 leash 불가라 재개하면 통제 수단이 없다(fail-closed)."""
    _ours(db)
    diagnosis = _diagnosis(shopping_lever_resume_candidates=[
        _resume_board_row("resume"),
        _resume_board_row("bid_down_first", adgroup_id="grp-2", effective_ad_id="nad-2"),
    ])
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({CAMP})), \
         patch.object(proposal_writer, "_adgroup_is_manual_bid", return_value=manual_bid):
        out = proposal_writer.build(db, diagnosis, as_of=TODAY)
    assert out == []


def test_build_lever_resume_non_canary_skips(db):
    """[B4 스코프] 비카나리(기본) → 재개 준비/재개 모두 미생성(B3 카나리 스코프 계승).
    비카나리는 ML 재조회조차 안 함(API 콜 최소 — 카나리 소수 그룹만).

    D-NAO-125(2026-07-29): 기본 상태(킬스위치 on)에서는 이 캠페인도 이제 열린다(의도된
    변경). 이 테스트는 킬스위치 off로 좁혀 종전 비카나리 skip 회귀를 보존한다."""
    _ours(db)
    diagnosis = _diagnosis(shopping_lever_resume_candidates=[
        _resume_board_row("bid_down_first"),
        _resume_board_row("resume", adgroup_id="grp-2", effective_ad_id="nad-2"),
    ])
    with patch.object(auto_operator, "AD_BID_ROUTING_ENABLED", False), \
         patch.object(proposal_writer, "_adgroup_is_manual_bid") as m_manual:
        out = proposal_writer.build(db, diagnosis, as_of=TODAY)  # 카나리 패치 없음 + 킬스위치 off
    assert out == []
    m_manual.assert_not_called()


def test_build_lever_resume_non_ours_skips(db):
    """optimizer != 'ours' 캠페인은 미노출(D-NAO-13)."""
    db.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="none"))
    db.commit()
    diagnosis = _diagnosis(shopping_lever_resume_candidates=[_resume_board_row("resume")])
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({CAMP})), \
         patch.object(proposal_writer, "_adgroup_is_manual_bid", return_value=True):
        out = proposal_writer.build(db, diagnosis, as_of=TODAY)
    assert out == []


# ══════════════════════════════════════════════════════════════════
# (4) GATE P2-2 — 카나리 캠페인 위임·브리핑 전면 제외(Confirm-only)
# ══════════════════════════════════════════════════════════════════
def _delegation_skipped():
    return {"not_delegated": 0, "not_pending": 0, "blocked": 0, "optimizer": 0,
            "budget_envelope": 0, "ad_confirm_only": 0, "canary_confirm_only": 0}


def _pending(db, *, proposal_type, target_type, target_id, campaign_id, target_bid=None,
             target_lock=None, adgroup_id=None):
    p = NaverProposal(
        proposal_type=proposal_type, target_type=target_type, target_id=target_id,
        campaign_id=campaign_id, adgroup_id=adgroup_id, rationale="테스트",
        expected_effect="테스트", status="pending", target_bid=target_bid,
        target_lock=target_lock,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_delegation_excludes_canary_campaign_resume(db):
    """[GATE P2-2] 카나리 캠페인의 resume(target_type='adgroup' — B3 ad 필터가 못 잡는 유형)도
    위임 자동발사 금지 — 카나리 기간 = 캠페인 전체가 Confirm-only(사람 감독) 기간."""
    db.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="ours"))
    db.commit()
    p = _pending(db, proposal_type="resume", target_type="adgroup", target_id="grp-mo",
                 campaign_id=CAMP, target_lock=False)
    skipped = _delegation_skipped()
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({CAMP})):
        assert delegation_gate._eligible(db, p, {"resume"}, skipped) is False
    assert skipped["canary_confirm_only"] == 1


def test_delegation_excludes_canary_campaign_adgroup_bid_down(db):
    """[GATE P2-2] 카나리 캠페인은 adgroup bid_down도 위임 제외(target_type 무관 전면)."""
    db.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="ours"))
    db.commit()
    p = _pending(db, proposal_type="bid_down", target_type="adgroup", target_id="grp-mo",
                 campaign_id=CAMP, target_bid=170)
    skipped = _delegation_skipped()
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({CAMP})):
        assert delegation_gate._eligible(db, p, {"bid_down"}, skipped) is False
    assert skipped["canary_confirm_only"] == 1


def test_delegation_non_canary_campaign_still_passes(db):
    """[GATE P2-2 회귀 0] 비카나리 캠페인 adgroup bid_down은 종전대로 eligible."""
    db.add(NaverCampaignSettings(campaign_id="cmp-other", optimizer="ours"))
    db.commit()
    p = _pending(db, proposal_type="bid_down", target_type="adgroup", target_id="grp-x",
                 campaign_id="cmp-other", target_bid=170)
    skipped = _delegation_skipped()
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({CAMP})):
        assert delegation_gate._eligible(db, p, {"bid_down"}, skipped) is True
    assert skipped["canary_confirm_only"] == 0


def test_expert_briefing_excludes_canary_campaign_pending(db):
    """[GATE P2-2] 브리핑 pending_proposals에서 카나리 캠페인 제안 제외(target_type 무관) —
    위임 실행 불가(Confirm-only) 카드가 Ava 검토 대상에 실리면 혼란. 비카나리는 포함(회귀 0)."""
    from app.services.naver_ad import expert_briefing_builder
    canary_p = _pending(db, proposal_type="resume", target_type="adgroup", target_id="grp-mo",
                        campaign_id=CAMP, target_lock=False)
    other_p = _pending(db, proposal_type="bid_down", target_type="keyword", target_id="nkw-1",
                       campaign_id="cmp-other", target_bid=170)
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({CAMP})):
        briefing = expert_briefing_builder.build(db, as_of=TODAY)
    ids = {p["id"] for p in briefing["pending_proposals"]}
    assert other_p.id in ids
    assert canary_p.id not in ids


def test_delegation_run_gate_skipped_dicts_have_canary_counter(db):
    """run_gate의 skipped 요약 dict에도 canary_confirm_only 키 존재(감사 표면 정합)."""
    out = delegation_gate.run_gate(db, run_id=999999)  # run 없음 → skipped 요약 반환
    assert "canary_confirm_only" in out["skipped"]


# ══════════════════════════════════════════════════════════════════
# (5) diagnosis 하니스 배선
# ══════════════════════════════════════════════════════════════════
def test_build_diagnosis_includes_lever_resume_board(db):
    """build_diagnosis boards에 shopping_lever_resume_candidates 신규 보드 노출."""
    from app.services.naver_ad.diagnosis import build_diagnosis
    _bep(db, "cp-1")
    from app.models import Order
    db.add(Order(channel_id=6, platform_product_id="cp-1", order_number="ORD-1",
                 order_date=TODAY, status="정상", selling_price=Decimal("10000")))
    db.commit()
    result = build_diagnosis(db, date(2026, 7, 1), date(2026, 7, 15))
    assert result["boards"] is not None
    assert "shopping_lever_resume_candidates" in result["boards"]
