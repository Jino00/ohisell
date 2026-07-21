# test_naver_ad_exploration_bx2.py — 스프린트 B-X BX2(소재입찰 UP 손 개방, D-NAO-70·71).
# 커버(계획서 §검증 1 차등):
#   ① explore_op 외 승인원의 소재(ad) UP 실쓰기 거부(비탐색 UP·다른 승인원)
#   ② explore_op DOWN도 허용(손 자체는 양방향 — 래더는 UP만 씀)
#   ③ 카나리 해제 후에도 킬스위치 차단(explore_op 화이트리스트, auto_operate OFF)
#   ④ 30% 통과·31% 차단(탐색 스텝 상한=30% 고정, 비탐색 bid_up은 15% 유지)
#   ⑤ exploration_ceiling 폴백 사다리(그룹→캠페인→계정 BEP·경제성 vs 휴리스틱 min·fail-closed)
#   ⑥ 자동발사: explore_op 외 전 경로 여전히 봉쇄(delegation delegable 제외·비탐색 UP 봉쇄)
# 실 API 0(writer는 전부 mock). DB 픽스처는 test_naver_ad_ad_level_bid_b3 관례.
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
    NaverEntity,
    NaverProductBep,
    NaverProposal,
)
from app.services.naver_ad import (
    campaign_target_resolver as ctr,
    delegation_gate,
    exploration,
    guardrail_gate,
)
from app.services.naver_ad import naver_execution_harness as harness
from app.services.naver_ad.bid_step_types import EXPLORATION_STEP_TYPES
from app.services.naver_ad.naver_sa_writer import WriteResult

AD_ID = "ad-explore-1"
GRP_ID = "grp-cold-1"
CAMP = "cmp-explore"
NAVER_CH = 6
NOW = datetime(2026, 7, 21, 12, 0, 0)


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


def _settings(db, *, campaign_id=CAMP, optimizer="ours", auto_operate=False):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer=optimizer, auto_operate=auto_operate))
    db.commit()


def _ad_proposal(db, *, proposal_type, target_bid=1040, adgroup_id=GRP_ID,
                 campaign_id=CAMP, target_type="ad", target_id=AD_ID,
                 status="approved", approval_source=None, ceiling=100_000):
    # BX3(P2①): 탐색 스텝(bid_up_explore)은 harness 쓰기-경계 하드 게이트가 expected_effect의
    # 경제성 상한 마커를 요구한다 — 성공 경로 테스트는 넉넉한 상한 마커를 심는다(상한 초과 차단은
    # 별도 테스트에서 낮은 ceiling으로 검증).
    from app.services.naver_ad.bid_step_types import encode_exploration_ceiling
    effect = encode_exploration_ceiling("탐색", ceiling) if proposal_type == "bid_up_explore" else "탐색"
    p = NaverProposal(
        proposal_type=proposal_type, target_type=target_type, target_id=target_id,
        campaign_id=campaign_id, adgroup_id=adgroup_id, rationale="탐색", expected_effect=effect,
        status=status, target_bid=target_bid, approval_source=approval_source,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _ad_write_result(before_bid=800, after_bid=1040):
    import json
    return WriteResult(
        action="update_ad_bid",
        before={"nccAdId": AD_ID, "adAttr": json.dumps({"bidAmt": before_bid, "useGroupBidAmt": False})},
        response=None,
        after={"nccAdId": AD_ID, "adAttr": json.dumps({"bidAmt": after_bid, "useGroupBidAmt": False})},
        created_ids=[],
    )


# ══════════════════════════════════════════════════════════════════
# ① explore_op 외 승인원의 소재 UP 거부 + 뚫기(explore_op UP 허용)
# ══════════════════════════════════════════════════════════════════
def test_explore_op_ad_up_writes(db):
    """뚫기: explore_op + bid_up_explore 소재 UP → 실쓰기 성공(전 캠페인·카나리 무관)."""
    p = _ad_proposal(db, proposal_type="bid_up_explore",
                     approval_source=exploration.APPROVAL_SOURCE_EXPLORE)
    _settings(db, auto_operate=True)  # explore_op 킬스위치 화이트리스트
    ctx = {"current_bid": 800, "roas_corrected": None, "target_roas": None, "unconverted_spend": None,
           "cost_today": None, "daily_budget": None, "last_change_at": None, "changes_today_count": 0}
    with patch.object(harness, "_build_guardrail_context", return_value=ctx), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_ad_bid",
                      return_value=_ad_write_result(800, 1040)) as mad:
        log_entry = harness.execute(db, p.id, dry_run=False)
    mad.assert_called_once_with(AD_ID, 1040)
    assert log_entry.entity_type == "ad"


def test_console_null_source_ad_up_rejected(db):
    """① 콘솔 수동(approval_source=None)의 소재 UP은 거부 — 탐색 자동 경로만 개방."""
    p = _ad_proposal(db, proposal_type="bid_up_explore", approval_source=None)
    _settings(db)
    with patch.object(harness.naver_sa_writer, "update_ad_bid") as mad:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mad.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_other_source_ad_up_rejected(db):
    """① 다른 승인원(auto_op_hr)의 소재 UP도 거부 — 킬스위치는 통과(ON)해도 ad-guard가 막는다."""
    from app.services.naver_ad.auto_operator import APPROVAL_SOURCE_HOURLY
    p = _ad_proposal(db, proposal_type="bid_up_explore", approval_source=APPROVAL_SOURCE_HOURLY)
    _settings(db, auto_operate=True)  # 킬스위치 통과 보장 → ad-guard 거부를 격리 검증
    with patch.object(harness.naver_sa_writer, "update_ad_bid") as mad:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mad.assert_not_called()


def test_explore_op_non_exploration_up_type_rejected(db):
    """① explore_op이라도 비탐색 UP 타입(bid_up)은 거부 — 탐색 스텝 타입만 개방(폭 제어)."""
    p = _ad_proposal(db, proposal_type="bid_up", approval_source=exploration.APPROVAL_SOURCE_EXPLORE)
    _settings(db, auto_operate=True)
    with patch.object(harness.naver_sa_writer, "update_ad_bid") as mad:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mad.assert_not_called()


# ══════════════════════════════════════════════════════════════════
# ② codex P2 역방향 잠금: explore_op은 탐색 UP 전용 승인원 — explore_op + bid_down 거부
# (소재 DOWN은 콘솔 Confirm/기타 승인원 몫. 쌍방향 잠금: 탐색타입 ⟺ explore_op)
# ══════════════════════════════════════════════════════════════════
def test_explore_op_ad_down_rejected(db):
    """codex P2: explore_op + bid_down → fail-closed(승인원 오용 — explore_op은 탐색 UP 전용)."""
    p = _ad_proposal(db, proposal_type="bid_down", target_bid=680,
                     approval_source=exploration.APPROVAL_SOURCE_EXPLORE)
    _settings(db, auto_operate=True)
    with patch.object(harness.naver_sa_writer, "update_ad_bid") as mad:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mad.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_console_bid_down_still_allowed(db):
    """대조: 콘솔(None) + bid_down은 여전히 허용(비탐색+비explore_op → 쌍방향 잠금 통과)."""
    p = _ad_proposal(db, proposal_type="bid_down", target_bid=680, approval_source=None)
    _settings(db)
    with patch.object(harness, "_build_guardrail_context", return_value={"current_bid": 800}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_ad_bid",
                      return_value=_ad_write_result(800, 680)) as mad:
        harness.execute(db, p.id, dry_run=False)
    mad.assert_called_once_with(AD_ID, 680)


def test_real_write_blocker_explore_op_bid_down_rejected(db):
    """codex P2(콘솔 판정): explore_op + bid_down → executable=False(승인원 오용)."""
    p = _ad_proposal(db, proposal_type="bid_down", target_bid=680,
                     approval_source=exploration.APPROVAL_SOURCE_EXPLORE)
    blocked = harness.real_write_blocker(p)
    assert blocked is not None and "탐색 스텝 타입 전용" in blocked


# ══════════════════════════════════════════════════════════════════
# ③ 카나리 해제 후에도 킬스위치 차단
# ══════════════════════════════════════════════════════════════════
def test_explore_op_ad_up_killswitch_off_blocks(db):
    """③ explore_op 소재 UP도 auto_operate OFF면 KillSwitchEngagedError(쓰기 없음, probe_op 관례)."""
    _settings(db, auto_operate=False)
    p = _ad_proposal(db, proposal_type="bid_up_explore",
                     approval_source=exploration.APPROVAL_SOURCE_EXPLORE)
    with patch.object(harness.naver_sa_writer, "update_ad_bid") as mad:
        with pytest.raises(harness.KillSwitchEngagedError):
            harness.execute(db, p.id, dry_run=False)
    mad.assert_not_called()


# ══════════════════════════════════════════════════════════════════
# ④ 30% 통과·31% 차단(탐색만 30%·비탐색 15% 유지)
# ══════════════════════════════════════════════════════════════════
def _gctx(**o):
    base = {"current_bid": 800, "roas_corrected": None, "target_roas": None,
            "cost_today": None, "daily_budget": None, "unconverted_spend": None,
            "last_change_at": None, "changes_today_count": 0}
    base.update(o)
    return base


def _gate(ptype, target_bid, **ctx):
    return guardrail_gate.check(
        {"proposal_type": ptype, "target_bid": target_bid, "target_lock": None},
        _gctx(**ctx), now=NOW,
    )


def test_exploration_30pct_passes():
    # 800 → 1040 = +30% 정확 → 통과(strict > 이므로 30%는 경계 통과)
    assert _gate("bid_up_explore", 1040) is None


def test_exploration_31pct_blocked():
    # 800 → 1050 = +31.25% → 차단
    r = _gate("bid_up_explore", 1050)
    assert r is not None and "변경폭" in r


def test_plain_bid_up_30pct_still_blocked_at_15():
    # 비탐색 bid_up은 여전히 15% 상한 — 30%는 차단(탐색만 30% 상한, 우회 아님)
    r = _gate("bid_up", 1040)
    assert r is not None and "변경폭" in r


def test_exploration_step_pct_matches_guardrail_cap():
    # exploration._EXPLORATION_STEP_PCT(래더 스텝) == guardrail 30% 상한(정합 — 스텝이 상한 초과 금지)
    assert exploration._EXPLORATION_STEP_PCT == guardrail_gate._EXPLORATION_MAX_CHANGE_PCT == Decimal("0.30")


# ══════════════════════════════════════════════════════════════════
# BEP 완전성 게이트 면제(탐색은 표본 없음) — 타입 기반(탐색만)임을 차등 고정
# ══════════════════════════════════════════════════════════════════
def _adgroup_write_result():
    return WriteResult(action="update_adgroup_bid", before={"bidAmt": 800}, response=None,
                       after={"bidAmt": 1040}, created_ids=[])


def test_adgroup_exploration_up_bep_context_exempt(db):
    """탐색(bid_up_explore) adgroup UP은 BEP 원료 None이어도 완전성 게이트 통과(콜드=표본 없음)."""
    p = _ad_proposal(db, proposal_type="bid_up_explore", target_type="adgroup", target_id=GRP_ID,
                     approval_source=exploration.APPROVAL_SOURCE_EXPLORE)
    _settings(db, auto_operate=True)
    ctx = {"current_bid": 800, "roas_corrected": None, "target_roas": None, "unconverted_spend": None,
           "cost_today": None, "daily_budget": None, "last_change_at": None, "changes_today_count": 0}
    with patch.object(harness, "_build_guardrail_context", return_value=ctx), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid",
                      return_value=_adgroup_write_result()) as mag:
        harness.execute(db, p.id, dry_run=False)
    mag.assert_called_once_with(GRP_ID, 1040)


def test_adgroup_plain_bid_up_bep_context_still_required(db):
    """대조: 비탐색(bid_up) adgroup UP은 BEP 원료 None이면 여전히 완전성 게이트 fail-closed(면제=탐색만)."""
    p = _ad_proposal(db, proposal_type="bid_up", target_type="adgroup", target_id=GRP_ID)
    _settings(db)
    ctx = {"current_bid": 800, "roas_corrected": None, "target_roas": None, "unconverted_spend": None,
           "cost_today": None, "daily_budget": None, "last_change_at": None, "changes_today_count": 0}
    with patch.object(harness, "_build_guardrail_context", return_value=ctx), \
         patch.object(harness.guardrail_gate, "check") as mgate, \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid") as mag:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mgate.assert_not_called()
    mag.assert_not_called()


# ══════════════════════════════════════════════════════════════════
# ⑤ exploration_ceiling 폴백 사다리
# ══════════════════════════════════════════════════════════════════
WIN_FROM = date(2026, 7, 13)
WIN_TO = date(2026, 7, 19)


def _bep(db, *, pid, bep_roas, has_cost=True):
    db.add(NaverProductBep(channel_id=NAVER_CH, channel_product_id=pid, product_name="p",
                           selling_price=Decimal("10000"), has_cost=has_cost,
                           bep_roas=Decimal(str(bep_roas))))
    db.commit()


def _map(db, *, adgroup_id, pid, campaign_id=CAMP):
    db.add(NaverAdgroupProduct(adgroup_id=adgroup_id, campaign_id=campaign_id, mall_product_id=pid))
    db.commit()


def _settle(db, *, adgroup_id=GRP_ID, campaign_id=CAMP, clk=100, conv_amt=100000):
    """단일 정착창 행 — adgroup/campaign/account grain이 동일 집계가 되도록 1행만 심는다
    (pooled_rpc가 raw rpc = conv_amt/clk로 수렴)."""
    db.add(NaverAdDaily(
        ad_date=date(2026, 7, 16), campaign_id=campaign_id, campaign_type="SHOPPING",
        adgroup_id=adgroup_id, keyword_id="-", imp=1000, clk=clk, cost=1000,
        conv_direct_amt=conv_amt, conv_indirect_amt=0,
    ))
    db.commit()


# (a) BEP 폴백 사다리: 그룹 → 캠페인 → 계정 → None
def test_bep_ladder_adgroup_level():
    # _resolve_exploration_bep_roas: 그룹 매핑 상품 BEP 우선
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    _bep(s, pid="pidA", bep_roas="3.0")
    _map(s, adgroup_id=GRP_ID, pid="pidA")
    assert exploration._resolve_exploration_bep_roas(s, CAMP, GRP_ID) == Decimal("3.0")
    s.close()


def test_bep_ladder_campaign_level_when_adgroup_absent():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    _bep(s, pid="pidB", bep_roas="4.0")
    _map(s, adgroup_id="grp-other", pid="pidB")  # 같은 캠페인의 다른 그룹 → 캠페인 grain엔 잡히나 GRP엔 없음
    assert exploration._resolve_exploration_bep_roas(s, CAMP, GRP_ID) == Decimal("4.0")
    s.close()


def test_bep_ladder_account_default_when_no_mapping():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    _bep(s, pid="pidC", bep_roas="5.0")  # 매핑 없음 → 계정 기본 BEP(매출가중, 주문 없으면 단순평균)
    assert exploration._resolve_exploration_bep_roas(s, CAMP, GRP_ID) == Decimal("5.0")
    s.close()


def test_bep_ladder_none_when_no_bep():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    assert exploration._resolve_exploration_bep_roas(s, CAMP, GRP_ID) is None
    s.close()


# (b) exploration_ceiling: 경제성 vs 휴리스틱 min·fail-closed
def test_ceiling_economic_below_heuristic(db):
    # BEP 큰값(10.0) → economic 낮음(rpc 1000/10=100), heuristic=800×2=1600 → min=100
    _bep(db, pid="pidA", bep_roas="10.0")
    _map(db, adgroup_id=GRP_ID, pid="pidA")
    _settle(db, clk=100, conv_amt=100000)  # rpc=1000
    c = exploration.exploration_ceiling(db, CAMP, GRP_ID, 800, WIN_FROM, WIN_TO)
    assert c == 100


def test_ceiling_heuristic_below_economic(db):
    # BEP 작은값(0.5) → economic 높음(1000/0.5=2000), heuristic=1600 → min=1600(휴리스틱이 상한)
    _bep(db, pid="pidA", bep_roas="0.5")
    _map(db, adgroup_id=GRP_ID, pid="pidA")
    _settle(db, clk=100, conv_amt=100000)  # rpc=1000
    c = exploration.exploration_ceiling(db, CAMP, GRP_ID, 800, WIN_FROM, WIN_TO)
    assert c == 1600


def test_ceiling_failclosed_current_bid_when_no_bep(db):
    # codex P1: BEP 부재(경제 증거 전무) → ceiling = current_bid(800) → ladder capped(상향 불가).
    # 휴리스틱(1600) 반환은 fail-open이라 금지(current<ceiling으로 근거 없이 상향됨).
    _settle(db, clk=100, conv_amt=100000)
    c = exploration.exploration_ceiling(db, CAMP, GRP_ID, 800, WIN_FROM, WIN_TO)
    assert c == 800


def test_ceiling_failclosed_current_bid_when_no_settlement(db):
    # codex P1: BEP 있으나 rpc≤0(경제 증거 전무) → ceiling = current_bid(800) → capped.
    _bep(db, pid="pidA", bep_roas="2.0")
    _map(db, adgroup_id=GRP_ID, pid="pidA")
    c = exploration.exploration_ceiling(db, CAMP, GRP_ID, 800, WIN_FROM, WIN_TO)
    assert c == 800


def test_ceiling_failclosed_capped_drives_ladder(db):
    # codex P1 폐루프: 경제 증거 전무 → ceiling==current_bid → ladder_judgment가 'capped'(상향 불가).
    _settle(db, clk=100, conv_amt=100000)  # BEP 없음 → 증거 전무
    ceiling = exploration.exploration_ceiling(db, CAMP, GRP_ID, 800, WIN_FROM, WIN_TO)
    assert ceiling == 800
    verdict, _ = exploration.ladder_judgment(
        last_probe={"bid": 800}, since_step_stats={"clk": 0}, ceiling=ceiling, current_bid=800,
    )
    assert verdict == "capped"


def test_ceiling_zero_when_no_bep_and_no_current_bid(db):
    # 상한 근거 전무(BEP None + current_bid≤0) → current_bid(0) 반환 = 탐색 발동 불가
    c = exploration.exploration_ceiling(db, CAMP, GRP_ID, 0, WIN_FROM, WIN_TO)
    assert c == 0


def test_ceiling_economic_when_current_bid_absent(db):
    # current_bid 부재(0)여도 경제 근거 있으면 경제 상한 채택(heuristic=0으로 min하지 않음)
    _bep(db, pid="pidA", bep_roas="0.5")
    _map(db, adgroup_id=GRP_ID, pid="pidA")
    _settle(db, clk=100, conv_amt=100000)  # economic=2000
    c = exploration.exploration_ceiling(db, CAMP, GRP_ID, 0, WIN_FROM, WIN_TO)
    assert c == 2000


# ══════════════════════════════════════════════════════════════════
# ⑥ 자동발사: explore_op 외 전 경로 봉쇄(delegation은 탐색 타입 제외)
# ══════════════════════════════════════════════════════════════════
def test_delegable_types_excludes_exploration():
    """탐색 스텝(bid_up_explore)은 위임 가능 유형에서 영구 제외 — 위임 자동발사 경로 봉쇄
    (rank-step과 동형: 레인 inline 전용, 위임으로 새면 경제성 상한·killswitch 화이트리스트 우회)."""
    delegable = delegation_gate.delegable_types()
    assert EXPLORATION_STEP_TYPES.isdisjoint(delegable)
    assert "bid_up_explore" not in delegable


def test_real_write_blocker_explore_op_up_executable(db):
    """콘솔 executable 판정: explore_op + bid_up_explore → 실행 가능(None, 뚫기)."""
    p = _ad_proposal(db, proposal_type="bid_up_explore",
                     approval_source=exploration.APPROVAL_SOURCE_EXPLORE)
    assert harness.real_write_blocker(p) is None


def test_real_write_blocker_console_up_not_executable(db):
    """콘솔(None) 소재 UP은 실행 버튼 비활성(탐색 explore_op만 개방)."""
    p = _ad_proposal(db, proposal_type="bid_up_explore", approval_source=None)
    blocked = harness.real_write_blocker(p)
    assert blocked is not None and "탐색" in blocked


# ══════════════════════════════════════════════════════════════════
# GATE P1 수정: 탐색 스텝 explore_op 요구를 전 target_type로 대칭화
# (ad-only가 아니라 adgroup/keyword도 explore_op 아니면 봉쇄 — 최약 UP 경로 차단)
# ══════════════════════════════════════════════════════════════════
def test_adgroup_console_exploration_up_rejected(db):
    """GATE P1: bid_up_explore + adgroup + 콘솔(None) → 쓰기 직전 fail-closed(explore_op 미요구
    구멍 차단). 수정 전엔 이 경로가 이익하한 면제 + killswitch 밖 최약 UP였다."""
    p = _ad_proposal(db, proposal_type="bid_up_explore", target_type="adgroup", target_id=GRP_ID,
                     approval_source=None)
    _settings(db)
    with patch.object(harness.naver_sa_writer, "update_adgroup_bid") as mag:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mag.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_keyword_none_exploration_up_rejected(db):
    """GATE P1: bid_up_explore + keyword + None → fail-closed(전 target_type 대칭)."""
    p = _ad_proposal(db, proposal_type="bid_up_explore", target_type="keyword", target_id="nkw-1",
                     approval_source=None)
    _settings(db)
    with patch.object(harness.naver_sa_writer, "update_keyword_bid") as mkw:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mkw.assert_not_called()


def test_adgroup_other_source_exploration_up_rejected(db):
    """GATE P1: bid_up_explore + adgroup + auto_op_hr(비 explore_op) → fail-closed(킬스위치 ON이어도)."""
    from app.services.naver_ad.auto_operator import APPROVAL_SOURCE_HOURLY
    p = _ad_proposal(db, proposal_type="bid_up_explore", target_type="adgroup", target_id=GRP_ID,
                     approval_source=APPROVAL_SOURCE_HOURLY)
    _settings(db, auto_operate=True)
    with patch.object(harness.naver_sa_writer, "update_adgroup_bid") as mag:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mag.assert_not_called()


def test_adgroup_explore_op_exploration_up_writes(db):
    """GATE P1 정상 경로: bid_up_explore + adgroup + explore_op(킬스위치 ON) → 통과(그룹 탐색 경로
    살아있음 — BX3 source='group' 배선 전제). 이익하한 면제도 explore_op이라 정상 적용."""
    p = _ad_proposal(db, proposal_type="bid_up_explore", target_type="adgroup", target_id=GRP_ID,
                     approval_source=exploration.APPROVAL_SOURCE_EXPLORE)
    _settings(db, auto_operate=True)
    ctx = {"current_bid": 800, "roas_corrected": None, "target_roas": None, "unconverted_spend": None,
           "cost_today": None, "daily_budget": None, "last_change_at": None, "changes_today_count": 0}
    with patch.object(harness, "_build_guardrail_context", return_value=ctx), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid",
                      return_value=_adgroup_write_result()) as mag:
        harness.execute(db, p.id, dry_run=False)
    mag.assert_called_once_with(GRP_ID, 1040)


def test_real_write_blocker_four_quadrant_reprobe(db):
    """GATE P1 재프로빙 — (AD/ADGROUP) × (console/explore_op) 4분면. 수정 전 ADGROUP+console이
    None(통과)이던 구멍이 이제 차단(not None). 정상 경로(explore_op) 양쪽 None."""
    def _p(target_type, target_id, source):
        return _ad_proposal(db, proposal_type="bid_up_explore", target_type=target_type,
                            target_id=target_id, approval_source=source)
    EXP = exploration.APPROVAL_SOURCE_EXPLORE
    # 콘솔(None) — 양쪽 차단
    assert harness.real_write_blocker(_p("ad", "ad-q1", None)) is not None
    assert harness.real_write_blocker(_p("adgroup", "grp-q2", None)) is not None  # ← GATE가 실증한 구멍
    # explore_op — 양쪽 실행 가능(None)
    assert harness.real_write_blocker(_p("ad", "ad-q3", EXP)) is None
    assert harness.real_write_blocker(_p("adgroup", "grp-q4", EXP)) is None
