# test_naver_ad_visibility_vf.py — 스프린트 VF(가시성 우선, D-NAO-83) 단위·통합-라이트 테스트.
# 커버(PLAN §4 완료 기준):
#   ① classify_visibility 경계(None/4.0/4.01/5.0/5.01)
#   ② evidence_window 활성/비활성 4갈래(저수요·예산소진·클릭확보·표본충분)
#   ③ evidence_ceiling(캠페인 90일 RPC 상한·표본부족 None·bep None·70원 하한 None)
#   ④ ladder_judgment ghost_hold(유령∧창 비활성 스텝 금지·창 활성/None pass-through·밴드 무영향)
#   ⑤ exploration_ceiling evidence_ceiling_value 패스스루
#   ⑥ 통합-라이트(레인): 유령 순위 그룹 — 창 활성 시 상한 해방 발사 / 창 비활성 시 ghost_hold
# 실 네이버 API 0 — naver_sa_writer·harness.execute는 mock. DB 픽스처는 BX3 관례.
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
    NaverRetroSignal,
    OpsDiaryEntry,
)
from app.services.naver_ad import auto_operator, exploration, visibility
from app.services.naver_ad.bid_step_types import (
    decode_exploration_ceiling,
    encode_exploration_ceiling,
)

CAMP = "cmp-shop"
GRP = "ag-ghost"
TODAY = date(2026, 7, 21)
NOW = datetime(2026, 7, 21, 8, 20, 0)  # 시간당 레인 크론(KST naive)


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


def _daily(db, *, ad_date, adgroup_id=GRP, campaign_id=CAMP, campaign_type="SHOPPING",
           imp=0, clk=0, cost=0, conv_direct_amt=0, conv_indirect_amt=0, rank_sum=0):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id="-", imp=imp, clk=clk, cost=cost,
        conv_direct_amt=conv_direct_amt, conv_indirect_amt=conv_indirect_amt, rank_sum=rank_sum,
    ))
    db.commit()


# ══════════════════════ ① classify_visibility 경계 ══════════════════════

def test_classify_visibility_boundaries():
    assert visibility.classify_visibility(None) == "unknown"
    assert visibility.classify_visibility(Decimal("4.0")) == "in_band"
    assert visibility.classify_visibility(Decimal("4.01")) == "visible"
    assert visibility.classify_visibility(Decimal("5.0")) == "visible"
    assert visibility.classify_visibility(Decimal("5.01")) == "ghost"
    # float 입력도 허용(레인 avg_rank는 float)
    assert visibility.classify_visibility(6.0) == "ghost"


# ══════════════════════ ② evidence_window ══════════════════════

def test_evidence_window_active_happy_path(db):
    """고수요(7일노출 217)·표본미달(정착 clk 3<10)·예산여유·클릭미달 → 활성."""
    _daily(db, ad_date=TODAY - timedelta(days=5), imp=217, clk=3, cost=1000)  # 7일창∩정착창
    win = visibility.evidence_window(db, GRP, CAMP, TODAY, settlement_clk=3)
    assert win["active"] is True
    assert win["demand_imp_7d"] == 217 and win["spend_7d"] == 1000 and win["clicks_7d"] == 3
    assert "활성" in win["reason"]


def test_evidence_window_inactive_low_demand(db):
    """7일 노출<100(저수요) → 비활성(무수요 그룹 배제)."""
    _daily(db, ad_date=TODAY - timedelta(days=5), imp=50, clk=1, cost=500)
    win = visibility.evidence_window(db, GRP, CAMP, TODAY, settlement_clk=1)
    assert win["active"] is False and "저수요" in win["reason"]


def test_evidence_window_inactive_budget_exhausted(db):
    """7일 지출≥50,000(예산 소진) → 비활성."""
    _daily(db, ad_date=TODAY - timedelta(days=3), imp=300, clk=5, cost=50_000)
    win = visibility.evidence_window(db, GRP, CAMP, TODAY, settlement_clk=5)
    assert win["active"] is False and "예산소진" in win["reason"]


def test_evidence_window_inactive_click_target_met(db):
    """7일 클릭≥15(증거 확보) → 비활성(통상 판정 복귀)."""
    _daily(db, ad_date=TODAY - timedelta(days=3), imp=300, clk=15, cost=1000)
    win = visibility.evidence_window(db, GRP, CAMP, TODAY, settlement_clk=5)
    assert win["active"] is False and "클릭확보" in win["reason"]


def test_evidence_window_inactive_sample_sufficient(db):
    """정착창 clk≥10(표본 충분) → 비활성(핫셋/통상 ROAS 경로)."""
    _daily(db, ad_date=TODAY - timedelta(days=5), imp=300, clk=3, cost=1000)
    win = visibility.evidence_window(db, GRP, CAMP, TODAY, settlement_clk=12)
    assert win["active"] is False and "표본충분" in win["reason"]


def test_evidence_window_fallback_settlement_query(db):
    """settlement_clk 미주입 → 자체 폴백 쿼리([today-8,today-2] clk 합)로 표본 판정."""
    _daily(db, ad_date=TODAY - timedelta(days=5), imp=200, clk=3, cost=1000)  # 정착창 안
    win = visibility.evidence_window(db, GRP, CAMP, TODAY)  # settlement_clk 미주입
    assert win["settlement_clk"] == 3 and win["active"] is True


# ══════════════════════ ③ evidence_ceiling ══════════════════════

def _campaign_90d_history(db, *, clk, conv_amt, ad_date=None):
    _daily(db, ad_date=ad_date or (TODAY - timedelta(days=10)), adgroup_id="ag-hist",
           imp=1000, clk=clk, cost=1000, conv_direct_amt=conv_amt)


def test_evidence_ceiling_happy_min_heuristic(db):
    """캠페인 90일 rpc=10000·bep 1.5 → economic 6660, heuristic(2290×2)=4580 → min=4580."""
    _campaign_90d_history(db, clk=40, conv_amt=400_000)  # rpc=400000/40=10000
    ceil = visibility.evidence_ceiling(db, CAMP, Decimal("1.5"), 2290, TODAY)
    assert ceil == 4580  # min(economic 6660, heuristic 4580)


def test_evidence_ceiling_economic_wins_when_lower(db):
    """현재입찰 큰 경우: heuristic(10000×2=20000) > economic(6660) → economic 채택."""
    _campaign_90d_history(db, clk=40, conv_amt=400_000)
    ceil = visibility.evidence_ceiling(db, CAMP, Decimal("1.5"), 10000, TODAY)
    assert ceil == 6660  # economic = 10000/1.5=6666→6660


def test_evidence_ceiling_none_when_clk_below_min(db):
    """캠페인 90일 clk<30(표본 부족) → None(레거시 폴백)."""
    _campaign_90d_history(db, clk=20, conv_amt=200_000)
    assert visibility.evidence_ceiling(db, CAMP, Decimal("1.5"), 2290, TODAY) is None


def test_evidence_ceiling_none_when_bep_none(db):
    """bep_roas None → None."""
    _campaign_90d_history(db, clk=40, conv_amt=400_000)
    assert visibility.evidence_ceiling(db, CAMP, None, 2290, TODAY) is None


def test_evidence_ceiling_none_when_affordable_floors_to_zero(db):
    """rpc 극소(2.5)·bep 1.5 → affordable_ceiling 70원 하한 미달=0 → None."""
    _campaign_90d_history(db, clk=40, conv_amt=100)  # rpc=100/40=2.5 → economic 0
    assert visibility.evidence_ceiling(db, CAMP, Decimal("1.5"), 2290, TODAY) is None


# ══════════════════════ ④ ladder_judgment ghost_hold ══════════════════════

_PRIOR = {"bid": 1000, "rank": 7}  # last_probe not None(첫 탐색 아님)


def test_ladder_ghost_hold_when_inactive(db):
    """유령(rank 6)·증거창 비활성(False) → ghost_hold(스텝 금지)."""
    verdict, reason = exploration.ladder_judgment(
        _PRIOR, {"clk": 0, "avg_rank": 6.0}, 5000, 1000, evidence_active=False,
    )
    assert verdict == "ghost_hold" and "유령" in reason


def test_ladder_ghost_active_is_legacy_step(db):
    """유령(rank 6)·증거창 활성(True) → 기존 판정(step_up) — 유령이어도 창 열리면 스텝."""
    verdict, _r = exploration.ladder_judgment(
        _PRIOR, {"clk": 0, "avg_rank": 6.0}, 5000, 1000, evidence_active=True,
    )
    assert verdict == "step_up"


def test_ladder_ghost_none_is_legacy_step(db):
    """evidence_active=None(레거시·미주입) → True로 정규화 = step_up(회귀 0)."""
    verdict, _r = exploration.ladder_judgment(
        _PRIOR, {"clk": 0, "avg_rank": 6.0}, 5000, 1000,
    )
    assert verdict == "step_up"


def test_ladder_in_band_unaffected_by_inactive(db):
    """밴드 안(rank 3.5)은 유령 아님 → 증거창 비활성이어도 stop_observe(무영향)."""
    verdict, _r = exploration.ladder_judgment(
        _PRIOR, {"clk": 0, "avg_rank": 3.5}, 5000, 1000, evidence_active=False,
    )
    assert verdict == "stop_observe"


def test_ladder_visible_not_ghost_when_inactive(db):
    """가시권(rank 4.5, ≤5)은 유령 아님 → 증거창 비활성이어도 ghost_hold 아님(step_up)."""
    verdict, _r = exploration.ladder_judgment(
        _PRIOR, {"clk": 0, "avg_rank": 4.5}, 5000, 1000, evidence_active=False,
    )
    assert verdict == "step_up"


# ── 첫 탐색(last_probe None) 유령 차단(codex 1R P1) — 첫 스텝도 유령이면 금지 ──

def test_ladder_first_step_ghost_hold_when_inactive():
    """last_probe None·유령(rank 6)·증거창 비활성 → ghost_hold(첫 스텝 차단, capped/start 앞)."""
    verdict, reason = exploration.ladder_judgment(
        None, {"clk": 0, "avg_rank": 6.0}, 5000, 1000, evidence_active=False,
    )
    assert verdict == "ghost_hold" and "첫 스텝 차단" in reason


def test_ladder_first_step_start_when_active():
    """last_probe None·유령(rank 6)·증거창 활성(True) → start(창 열림, 첫 스텝 허용)."""
    verdict, _r = exploration.ladder_judgment(
        None, {"clk": 0, "avg_rank": 6.0}, 5000, 1000, evidence_active=True,
    )
    assert verdict == "start"


def test_ladder_first_step_start_when_legacy_none():
    """last_probe None·유령(rank 6)·evidence_active=None(레거시) → start(pass-through 보존·회귀 0)."""
    verdict, _r = exploration.ladder_judgment(
        None, {"clk": 0, "avg_rank": 6.0}, 5000, 1000,
    )
    assert verdict == "start"


def test_ladder_first_step_start_when_rank_none_blind():
    """last_probe None·rank None(imp=0 눈먼 콜드)·증거창 비활성 → start(눈먼 첫 스텝 경로 보존, VT4)."""
    verdict, _r = exploration.ladder_judgment(
        None, {"clk": 0, "avg_rank": None}, 5000, 1000, evidence_active=False,
    )
    assert verdict == "start"


# ══════════════════════ ⑤ exploration_ceiling 패스스루 ══════════════════════

def test_exploration_ceiling_passthrough(db):
    """evidence_ceiling_value 주입 시 그 값을 그대로 반환(레거시 산식 우회)."""
    wf, wt = TODAY - timedelta(days=8), TODAY - timedelta(days=2)
    assert exploration.exploration_ceiling(db, CAMP, GRP, 2290, wf, wt, evidence_ceiling_value=3000) == 3000


def test_exploration_ceiling_legacy_when_value_none(db):
    """evidence_ceiling_value=None → 레거시 경로(BEP 전무 → fail-closed current_bid 반환)."""
    wf, wt = TODAY - timedelta(days=8), TODAY - timedelta(days=2)
    assert exploration.exploration_ceiling(db, CAMP, GRP, 1000, wf, wt, evidence_ceiling_value=None) == 1000


# ══════════════════════ ⑥ 통합-라이트(레인) ══════════════════════

def _lane_setup(db, *, group_bid=2290, settle_clk=3):
    """유령 순위 SHOPPING 그룹 시드 — 캠페인/그룹 엔티티(on)·정착창 표본미달·7일 고수요·
    캠페인 90일 실측 RPC(상한 해방 원료)·BEP·신선 스냅샷/소급."""
    db.add(NaverCampaignSettings(campaign_id=CAMP, auto_operate=True, optimizer="ours"))
    db.add(NaverEntity(entity_type="campaign", entity_id=CAMP, campaign_id=CAMP,
                       campaign_type="SHOPPING", status="on"))
    db.add(NaverEntity(entity_type="adgroup", entity_id=GRP, parent_id=CAMP,
                       campaign_id=CAMP, campaign_type="SHOPPING", status="on"))
    # 정착창∩7일창(today-5): 표본미달(clk<10)·고수요(imp≥100)·저지출.
    _daily(db, ad_date=TODAY - timedelta(days=5), imp=217, clk=settle_clk, cost=1000)
    # 캠페인 90일 이력(today-30, 7일·정착창 밖): clk 43 확보·rpc≈9302 → economic 상한 해방.
    _daily(db, ad_date=TODAY - timedelta(days=30), imp=1000, clk=40, cost=1000, conv_direct_amt=400_000)
    db.add(NaverProductBep(channel_id=6, channel_product_id="pidX", product_name="p",
                           selling_price=Decimal("10000"), has_cost=True,
                           contribution_margin=Decimal("4000"), bep_roas=Decimal("1.5")))
    db.add(NaverAdgroupProduct(adgroup_id=GRP, campaign_id=CAMP, mall_product_id="pidX"))
    db.add(NaverHourlySnapshot(snapshot_at=NOW, ad_date=TODAY, snapshot_hour=23, campaign_id=CAMP,
                               campaign_type="SHOPPING", cost=0, clk=0, imp=0, daily_budget=100000))
    db.commit()
    # 소급채점 신선 더미(우리 그룹 밖) → daily 손실 게이트 신선도 통과 + 우리 그룹은 손실 보드 밖.
    db.add(NaverRetroSignal(created_at=NOW, asof_date=TODAY - timedelta(days=1),
                            board="shopping_group_bep", direction="down", grain="adgroup",
                            target_id="dummy-fresh", campaign_id=CAMP, cf_asof=1.0, bep_asof=1.5,
                            target_asof=2.0, cost_asof=0))
    db.commit()


def _prior_step(db, *, changed_at):
    """직전 탐색 UP 성공 실쓰기(change_log) — last_probe not None(ghost_hold 분기 도달용)."""
    import json
    p = NaverProposal(
        proposal_type="bid_up_explore", target_type="adgroup", target_id=GRP,
        campaign_id=CAMP, adgroup_id=GRP, rationale="[탐색UP] 직전",
        expected_effect=encode_exploration_ceiling("직전", 100000),
        status="approved", target_bid=2290, approval_source=exploration.APPROVAL_SOURCE_EXPLORE,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    db.add(NaverChangeLog(
        entity_type="adgroup", entity_id=GRP, campaign_id=CAMP, action="update_bid",
        rationale="[탐색UP] 직전", proposal_id=p.id, dry_run=False,
        before_value=json.dumps({"bidAmt": 2000}), after_value=json.dumps({"bidAmt": 2290}),
        changed_at=changed_at, executed_at=changed_at,
    ))
    db.commit()


def _run_lane(db, curve):
    eff = {"source": "group", "effective_bid": 2290, "max_ad_id": None}
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 2290}), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid", return_value=eff), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda tid, d: curve)
    return result, mock_exec


def _ghost_curve():
    return [{"hour": 5, "imp": 20, "clk": 0, "cost": 0, "avg_rank": 5.1, "conv_cnt": 0},
            {"hour": 6, "imp": 20, "clk": 0, "cost": 0, "avg_rank": 5.1, "conv_cnt": 0}]


def test_lane_evidence_active_ghost_releases_ceiling_and_fires(db):
    """유령(rank 5.1) 그룹·증거창 활성 → 상한 해방(evidence_ceiling 4580) 발사(target>2290·
    ceiling 마커=evidence 상한). 첫 탐색(last_probe 없음) → start → 적응 스텝."""
    _lane_setup(db)
    result, mock_exec = _run_lane(db, _ghost_curve())
    assert result["explored"] == 1 and result["explored_ghost_hold"] == 0
    p = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert p.proposal_type == "bid_up_explore" and p.target_type == "adgroup"
    assert p.target_bid > 2290  # 상한 해방으로 유령→밴드 방향 스텝
    assert decode_exploration_ceiling(p.expected_effect) == 4580  # evidence 상한(min(6660,4580))


def test_lane_evidence_inactive_ghost_holds(db):
    """유령(rank 5.1) 그룹·증거창 비활성(7일 지출≥50k) → ghost_hold(발사 0·카운터 1)."""
    _lane_setup(db)
    _daily(db, ad_date=TODAY - timedelta(days=1), imp=10, clk=0, cost=50_000)  # 예산 소진 → 창 비활성
    _prior_step(db, changed_at=NOW - timedelta(days=1))  # last_probe not None → ghost_hold 분기 도달
    result, mock_exec = _run_lane(db, _ghost_curve())
    assert result["explored"] == 0 and result["explored_ghost_hold"] == 1
    mock_exec.assert_not_called()
    assert len(result["ghost_hold_groups"]) == 1
    assert result["ghost_hold_groups"][0]["adgroup_id"] == GRP


# ══════════════════════ 일 레인 유령 가시성 관측(VT3b 최소형) ══════════════════════

def test_daily_ghost_visibility_briefing_writes_observation(db):
    """일 레인: D-1 SHOPPING 유령(순위>5)∧증거창 비활성 그룹 → diary observe 1건 기록."""
    _lane_setup(db)
    # D-1(어제) 유령 순위 실적(avg_rank = rank_sum/imp = 510/100 = 5.1 > 5) — 관측 원료.
    _daily(db, ad_date=TODAY - timedelta(days=1), imp=100, clk=0, cost=50_000, rank_sum=510)
    result = auto_operator.run_daily_lane(db, now=NOW)
    assert result["ghost_visibility_observed"] == 1
    obs = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.event_type == "observe",
        OpsDiaryEntry.action == auto_operator.ACTION_GHOST_VISIBILITY_BRIEFING,
    ).all()
    assert len(obs) == 1 and GRP in (obs[0].rationale or "")


def test_daily_ghost_visibility_silent_when_none(db):
    """관측 대상 없음(유령이나 창 활성) → 침묵(diary 미기록)."""
    _lane_setup(db)
    # D-1 유령이지만 증거창 활성(저지출) → 관측 대상 아님.
    _daily(db, ad_date=TODAY - timedelta(days=1), imp=100, clk=0, cost=500, rank_sum=510)
    result = auto_operator.run_daily_lane(db, now=NOW)
    assert result["ghost_visibility_observed"] == 0
    obs = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.action == auto_operator.ACTION_GHOST_VISIBILITY_BRIEFING,
    ).all()
    assert obs == []
