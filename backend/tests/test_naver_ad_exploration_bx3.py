# test_naver_ad_exploration_bx3.py — 스프린트 B-X BX3(탐색-UP 레인 배선, D-NAO-70·71).
# 커버(PLAN §검증 1 차등): 콜드(imp=0) 소폭 스텝 · 순위 피드백 적응 스텝 · 밴드 도달 정지 ·
#   rank≤2.5 진단 종료 · ceiling 클램프+쓰기경계 하드게이트 · 손실고삐 skip · 핫셋 상호배타 ·
#   WEB_SITE 제외 · 소재 1개만 · 쿨다운 2h(KST) · 재가동 태그.
# 실 네이버 API 0 — naver_sa_writer(라이브 재조회)·harness.execute는 mock.
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
    NaverProductBep,
    NaverProposal,
)
from app.services.naver_ad import auto_operator, exploration
from app.services.naver_ad import naver_execution_harness as harness
from app.services.naver_ad.bid_step_types import decode_exploration_ceiling, encode_exploration_ceiling

CAMP = "cmp-shop"
GRP = "ag-explore"
TODAY = date(2026, 7, 21)
NOW = datetime(2026, 7, 21, 8, 20, 0)  # 시간당 레인 크론(KST naive), now.hour=8


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


def _hour(h, *, imp, clk, cost, avg_rank=None, conv_cnt=0):
    return {"hour": h, "imp": imp, "clk": clk, "cost": cost, "avg_rank": avg_rank, "conv_cnt": conv_cnt}


def _setup(db, *, campaign_type="SHOPPING", settle_clk=3, settle_conv=30000, group_bid=1000,
           adgroup_id=GRP, bep_roas="0.5"):
    """탐색 후보 SHOPPING 그룹 시드 — 캠페인/그룹 엔티티(on)·정착창 실적(clk<10=핫셋 미달)·
    BEP 매핑(경제성 상한)·신선 스냅샷(서킷브레이커 통과)."""
    db.add(NaverCampaignSettings(campaign_id=CAMP, auto_operate=True, optimizer="ours"))
    db.add(NaverEntity(entity_type="campaign", entity_id=CAMP, campaign_id=CAMP,
                       campaign_type=campaign_type, status="on"))
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=CAMP,
                       campaign_id=CAMP, campaign_type=campaign_type, status="on"))
    db.add(NaverAdDaily(  # 정착창(7/15 ∈ [7/13,7/19]) — 후보(clk<10)·경제성 상한 rpc 원료
        ad_date=date(2026, 7, 15), campaign_id=CAMP, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id="-", imp=100, clk=settle_clk, cost=1000,
        conv_direct_amt=settle_conv, conv_indirect_amt=0,
    ))
    db.add(NaverProductBep(channel_id=6, channel_product_id="pidX", product_name="p",
                           selling_price=Decimal("10000"), has_cost=True,
                           contribution_margin=Decimal("4000"), bep_roas=Decimal(str(bep_roas))))
    db.add(NaverAdgroupProduct(adgroup_id=adgroup_id, campaign_id=CAMP, mall_product_id="pidX"))
    db.add(NaverHourlySnapshot(  # 신선(hour 23) → 서킷브레이커 통과
        snapshot_at=NOW, ad_date=TODAY, snapshot_hour=23, campaign_id=CAMP,
        campaign_type=campaign_type, cost=0, clk=0, imp=0, daily_budget=100000,
    ))
    db.commit()


def _prior_step(db, *, adgroup_id=GRP, target_type="adgroup", target_id=GRP,
                before_bid=900, after_bid=1000, changed_at=None):
    """직전 탐색 UP 성공 실쓰기(change_log) 시드 — last_probe·쿨다운·기울기 원료."""
    import json
    p = NaverProposal(
        proposal_type="bid_up_explore", target_type=target_type, target_id=target_id,
        campaign_id=CAMP, adgroup_id=adgroup_id, rationale="[탐색UP] 직전",
        expected_effect=encode_exploration_ceiling("직전", 100000),
        status="approved", target_bid=after_bid, approval_source=exploration.APPROVAL_SOURCE_EXPLORE,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    if target_type == "ad":
        before_v = json.dumps({"nccAdId": target_id, "adAttr": json.dumps({"bidAmt": before_bid})})
        after_v = json.dumps({"nccAdId": target_id, "adAttr": json.dumps({"bidAmt": after_bid})})
    else:
        before_v = json.dumps({"bidAmt": before_bid})
        after_v = json.dumps({"bidAmt": after_bid})
    db.add(NaverChangeLog(
        entity_type=target_type, entity_id=target_id, campaign_id=CAMP, action="update_bid",
        rationale="[탐색UP] 직전", proposal_id=p.id, dry_run=False,
        before_value=before_v, after_value=after_v,
        changed_at=changed_at or (NOW - timedelta(days=1)), executed_at=changed_at or (NOW - timedelta(days=1)),
    ))
    db.commit()


def _run(db, curve, *, eff=None):
    """탐색 레인만 관측(harness.execute·라이브 재조회 mock). 반환 (result, mock_exec)."""
    eff = eff or {"source": "group", "effective_bid": 1000, "max_ad_id": None,
                  "has_ad_data": False, "group_bid": 1000, "ad_count": 0}
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid", return_value=900), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid", return_value=eff), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda tid, d: curve)
    return result, mock_exec


# ══════════════════════ 스텝 발사(step_up / cold / adaptive) ══════════════════════

def test_lane_step_up_below_band_fires(db):
    """밴드 밖(rank 6)·무클릭·첫 스텝 → 탐색 UP 발사(explore_op·bid_up_explore·ceiling 마커)."""
    _setup(db)
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(7, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 1
    mock_exec.assert_called_once()
    p = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert p.proposal_type == "bid_up_explore"
    assert p.approval_source == exploration.APPROVAL_SOURCE_EXPLORE
    assert p.target_type == "adgroup" and p.target_id == GRP
    assert p.target_bid == 1100  # 첫 스텝 보수 +10%(1000→1100), ceiling 2000 이내
    assert p.rationale.startswith("[탐색UP]")
    assert decode_exploration_ceiling(p.expected_effect) is not None  # 쓰기경계 하드게이트 원료


def test_lane_cold_imp0_small_step(db):
    """콜드(imp=0=rank 관측 불가) → 보수적 소폭(+10%) blind 스텝."""
    _setup(db)
    curve = [_hour(5, imp=0, clk=0, cost=0, avg_rank=None),
             _hour(6, imp=0, clk=0, cost=0, avg_rank=None),
             _hour(7, imp=0, clk=0, cost=0, avg_rank=None)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 1
    p = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert p.target_bid == 1100  # blind +10%


def test_lane_adaptive_slope_step(db):
    """순위 피드백 적응 스텝(기울기 有): 직전 900→1000에 rank 8→6(어제 스텝), 오늘 rank 6 →
    목표 rank4까지 필요 증분 = (100원/rank2)×(6-4)=100 → 1100. last_probe.rank는 스텝 前 순위."""
    _setup(db)
    # 직전 스텝은 어제(step_hour=0 분기) → last_probe.rank=None이 아니라, 오늘 관측만으로 기울기가
    # 필요하므로 직전 스텝을 오늘 hour3에 두고 그 前(hour<3) rank=8, 그 後(hour>=3) rank=6로 관측.
    _prior_step(db, before_bid=900, after_bid=1000,
                changed_at=datetime(2026, 7, 21, 3, 30, 0))
    curve = [_hour(1, imp=20, clk=0, cost=0, avg_rank=8.0),  # 스텝 前
             _hour(2, imp=20, clk=0, cost=0, avg_rank=8.0),
             _hour(4, imp=20, clk=0, cost=0, avg_rank=6.0),  # 스텝 後(현 사이클)
             _hour(5, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 1
    p = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert p.target_bid == 1100  # 1000 + (100/2)×(6-4)=1100 (눈먼 30%=1300 아님)


def test_lane_reactivate_tag(db):
    """④ 밴드 밖·무클릭·상한 미도달·과거 클릭 이력(정착 clk>0)·24h 정체 → [탐색UP·재가동]."""
    _setup(db, settle_clk=4)  # 정착 clk=4>0 = 과거 클릭 이력(핫셋 미달)
    _prior_step(db, changed_at=NOW - timedelta(days=1))  # 어제 스텝(쿨다운 통과)
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=5.0),  # 밴드 밖·무클릭·flow 0
             _hour(6, imp=20, clk=0, cost=0, avg_rank=5.0),
             _hour(7, imp=20, clk=0, cost=0, avg_rank=5.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 1
    p = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert p.rationale.startswith("[탐색UP·재가동]")


# ══════════════════════ 상향 정지/종료(held / capped / not_rank) ══════════════════════

def test_lane_band_reached_holds(db):
    """③ 밴드 도달(rank 3.5)·무클릭 → 상향 정지·관측(제안 생성 없음, explored_held)."""
    _setup(db)
    _prior_step(db, changed_at=NOW - timedelta(days=1))  # 직전 스텝 존재 → ladder 밴드 분기
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=3.5),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=3.5),
             _hour(7, imp=20, clk=0, cost=0, avg_rank=3.5)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0 and result["explored_held"] == 1
    mock_exec.assert_not_called()


def test_lane_not_rank_diagnosis_end(db):
    """② 과열밴드(rank 2.3)인데 클릭0 지속 → 순위 병리 아님 진단 종료(explored_not_rank)."""
    _setup(db)
    _prior_step(db, changed_at=NOW - timedelta(days=1))
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=2.3),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=2.3),
             _hour(7, imp=20, clk=0, cost=0, avg_rank=2.3)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0 and result["explored_not_rank"] == 1
    mock_exec.assert_not_called()


def test_lane_click_arrived_holds(db):
    """① 이번 사이클 클릭 발생 → 상향 정지·관측 인계(explored_held, 제안 없음)."""
    _setup(db)
    _prior_step(db, changed_at=NOW - timedelta(days=1))
    curve = [_hour(5, imp=20, clk=1, cost=100, avg_rank=5.0),
             _hour(6, imp=20, clk=1, cost=100, avg_rank=5.0),
             _hour(7, imp=20, clk=0, cost=0, avg_rank=5.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0 and result["explored_held"] == 1
    mock_exec.assert_not_called()


def test_lane_capped_when_ceiling_reached(db):
    """④ 경제성 상한 도달(현 입찰≥상한)·무클릭 → 종료(explored_capped). 낮은 heuristic 상한 유도:
    bep 큰값으로 economic 낮춰 min 상한이 현재입찰 근처 → capped."""
    _setup(db, settle_clk=3, settle_conv=3000, bep_roas="10.0")  # rpc=1000, economic=1000/10=100
    _prior_step(db, changed_at=NOW - timedelta(days=1))
    # ceiling = min(economic 100, heuristic 2000)=100 < current 1000 → 밴드 밖 무클릭 → capped
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0 and result["explored_capped"] == 1
    mock_exec.assert_not_called()


# ══════════════════════ 라우팅·격리·경계 ══════════════════════

def test_lane_source_ad_updates_single_ad(db):
    """소재-레벨(source='ad') → 소재입찰 1개만(max 실효) update. target_type='ad'·단일 소재."""
    _setup(db)
    eff = {"source": "ad", "effective_bid": 900, "max_ad_id": "ad-max",
           "has_ad_data": True, "group_bid": 1000, "ad_count": 3}
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve, eff=eff)
    assert result["explored"] == 1
    p = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert p.target_type == "ad" and p.target_id == "ad-max"  # 단일 max 소재만
    assert p.adgroup_id == GRP  # 소재 제안 필수 컨텍스트
    assert p.target_bid == 990  # 900×1.10 blind 첫 스텝


def test_lane_web_site_excluded(db):
    """파워링크(WEB_SITE) 캠페인 → 탐색 후보 0(exploration_candidates가 SHOPPING만)."""
    _setup(db, campaign_type="WEB_SITE")
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0 and result["explored_held"] == 0
    # WEB_SITE adgroup은 핫셋 grain(키워드)도 아니라 어떤 레인도 타지 않음
    mock_exec.assert_not_called()


def test_lane_hotset_mutually_exclusive(db):
    """핫셋(정착 clk≥10)은 탐색 후보 아님 — 탐색 발사 0(핫셋 경로만)."""
    _setup(db, settle_clk=12)  # clk≥10 = 핫셋
    curve = [_hour(5, imp=0, clk=0, cost=0)]  # 당일 imp 0 → 핫셋 hold, 탐색 후보엔 애초 없음
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0


def test_lane_cooldown_2h_kst_blocks(db):
    """쿨다운 2h(KST) — 직전 [탐색UP] change_log가 1.5h 전(<2h) → 미발동."""
    _setup(db)
    _prior_step(db, changed_at=NOW - timedelta(hours=1, minutes=30))  # 1.5h < 2h
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0 and result["explored_held"] == 0
    mock_exec.assert_not_called()


def test_lane_cooldown_2h_kst_passes_after(db):
    """쿨다운 경계: 직전 스텝 2h+ 전 → 발동(같은 KST tz 비교)."""
    _setup(db)
    _prior_step(db, changed_at=NOW - timedelta(hours=2, minutes=1))
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, _mock = _run(db, curve)
    assert result["explored"] == 1


# ══════════════════════ 손실고삐 캠페인 제외(봉투#5) ══════════════════════

def test_lane_leash_skips_exploration(db):
    """손실고삐(is_leash DOWN) 발동 캠페인 → 탐색 레인 skip(UP·DOWN 충돌 금지, 봉투#5).
    _judge_hourly가 leash verdict를 내면 campaign_leashed=True → 그 캠페인의 탐색 후보(ag-explore)는
    발사되지 않는다(같은 캠페인 순회 안에서 핫셋 먼저·탐색 나중)."""
    _setup(db)  # 탐색 후보 ag-explore(clk<10)
    # 같은 캠페인 핫셋 그룹(clk≥10) — _judge_hourly를 patch해 leash DOWN을 확정 주입.
    db.add(NaverEntity(entity_type="adgroup", entity_id="ag-hot", parent_id=CAMP,
                       campaign_id=CAMP, campaign_type="SHOPPING", status="on"))
    db.add(NaverAdDaily(ad_date=date(2026, 7, 15), campaign_id=CAMP, campaign_type="SHOPPING",
                        adgroup_id="ag-hot", keyword_id="-", imp=500, clk=20, cost=700,
                        conv_direct_amt=0, conv_indirect_amt=0))  # 핫셋(clk20)
    db.commit()

    def fetch(tid, d):
        if tid == "ag-hot":
            return [_hour(6, imp=40, clk=2, cost=200, avg_rank=3.0, conv_cnt=0)]
        return [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0)]  # 탐색 후보(leash 없었으면 발사됐을 것)

    def fake_judge(db_, *, target_type, target_id, campaign_id, curve, now):
        return {"direction": "down", "reason": "장중 loss", "leash": True}

    with patch.object(auto_operator, "_judge_hourly", side_effect=fake_judge), \
         patch.object(auto_operator, "_live_current_bid", return_value=1000), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid",
                      return_value={"source": "group", "effective_bid": 1000, "max_ad_id": None}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=fetch)

    # 핫셋 leash DOWN은 나가되(approved 1) 탐색은 skip(explored 0).
    assert result["explored"] == 0 and result["approved"] == 1
    saved = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert saved.rationale.startswith("[순위고삐]") and saved.proposal_type == "bid_down"


# ══════════════════════ 쓰기-경계 하드 게이트(P2①, harness) ══════════════════════

def _explore_proposal(db, *, target_bid, expected_effect, target_type="adgroup", target_id=GRP,
                      approval_source=None):
    p = NaverProposal(
        proposal_type="bid_up_explore", target_type=target_type, target_id=target_id,
        campaign_id=CAMP, adgroup_id=GRP, rationale="탐색", expected_effect=expected_effect,
        status="approved", target_bid=target_bid,
        approval_source=approval_source or exploration.APPROVAL_SOURCE_EXPLORE,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _settings_only(db):
    db.add(NaverCampaignSettings(campaign_id=CAMP, auto_operate=True, optimizer="ours"))
    db.commit()


def test_harness_ceiling_gate_blocks_missing_marker(db):
    """P2①: 탐색 스텝 expected_effect에 ceiling 마커 부재 → fail-closed(경제 근거 없이 상향 금지)."""
    _settings_only(db)
    p = _explore_proposal(db, target_bid=1100, expected_effect="마커 없음")
    with patch.object(harness.naver_sa_writer, "update_adgroup_bid") as mag:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mag.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_harness_ceiling_gate_blocks_target_over_ceiling(db):
    """P2①: target_bid > 마커 상한 → fail-closed(레인 클램프 불신·쓰기경계 재검증)."""
    _settings_only(db)
    p = _explore_proposal(db, target_bid=1100, expected_effect=encode_exploration_ceiling("x", 1000))
    with patch.object(harness.naver_sa_writer, "update_adgroup_bid") as mag:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mag.assert_not_called()


def test_harness_ceiling_gate_passes_within_ceiling(db):
    """P2①: target_bid ≤ 상한 → 게이트 통과(그 후 guardrail mock None → 실쓰기)."""
    _settings_only(db)
    p = _explore_proposal(db, target_bid=1000, expected_effect=encode_exploration_ceiling("x", 1500))
    ctx = {"current_bid": 800, "roas_corrected": None, "target_roas": None, "unconverted_spend": None,
           "cost_today": None, "daily_budget": None, "last_change_at": None, "changes_today_count": 0}
    from app.services.naver_ad.naver_sa_writer import WriteResult
    wr = WriteResult(action="update_adgroup_bid", before={"bidAmt": 800}, response=None,
                     after={"bidAmt": 1000}, created_ids=[])
    with patch.object(harness, "_build_guardrail_context", return_value=ctx), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid", return_value=wr) as mag:
        harness.execute(db, p.id, dry_run=False)
    mag.assert_called_once_with(GRP, 1000)
