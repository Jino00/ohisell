# test_naver_ex_wiring.py — EX 확장 스프린트 레인 배선 테스트(D-NAO-85·87)
# 갈래 A(EX 제안 생성 배선·proposal_pipeline) / B(P3 프라이어 폴백·auto_operator._check_bid_up_conditions)
# / C(예산 봉투·budget_envelope + 일 레인 자동 심사) / D(deep_ok 산출·auto_operator._deep_expansion_ok).
# 실 API 0·실쓰기 0(harness.execute·라이브 재조회는 patch).
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily, NaverCampaignSettings, NaverChangeLog, NaverEntity, NaverHourlySnapshot,
    NaverKeywordHourly, NaverProposal, OpsDiaryEntry,
)
from app.services.naver_ad import (
    auto_operator, budget_envelope, expansion_allocator, expansion_pressure, proposal_pipeline,
)

CAMPAIGN = "cmp-shop"
TODAY = date(2026, 7, 23)
NOW = datetime(2026, 7, 23, 8, 50, 0)
DAY_START_UTC = datetime.combine(TODAY, datetime.min.time()) - timedelta(hours=9)
IN_WINDOW = TODAY - timedelta(days=5)  # 정착창(D-8~D-2) 안
ACTUAL_FACTOR = {"factor": Decimal("1"), "source": "actual_revenue_ratio"}
UNAVAILABLE_FACTOR = {"factor": Decimal("1"), "source": "unavailable"}


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


def _settings(db, *, campaign_id=CAMPAIGN, auto_operate=True, optimizer="ours"):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, auto_operate=auto_operate, optimizer=optimizer))
    db.commit()


def _campaign_entity(db, *, campaign_id=CAMPAIGN):
    db.add(NaverEntity(entity_type="campaign", entity_id=campaign_id,
                       campaign_id=campaign_id, campaign_type="SHOPPING", status="on"))
    db.commit()


def _adgroup(db, *, adgroup_id, campaign_id=CAMPAIGN, bid_amt=1000):
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
                       campaign_id=campaign_id, campaign_type="SHOPPING", status="on", bid_amt=bid_amt))
    db.commit()


def _daily(db, *, adgroup_id, ad_date, clk=0, imp=100, cost=1000, conv_amt=0, campaign_id=CAMPAIGN):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type="SHOPPING",
        adgroup_id=adgroup_id, keyword_id="-", imp=imp, clk=clk, cost=cost,
        conv_direct_amt=conv_amt, conv_indirect_amt=0,
    ))
    db.commit()


def _hourly_rank(db, *, adgroup_id, avg_rank, imp=100, campaign_id=CAMPAIGN):
    db.add(NaverKeywordHourly(
        ad_date=TODAY - timedelta(days=3), hour=10, entity_type="adgroup", entity_id=adgroup_id,
        adgroup_id=adgroup_id, campaign_id=campaign_id, campaign_type="SHOPPING",
        imp=imp, clk=0, cost=0, avg_rank=avg_rank,
    ))
    db.commit()


def _snapshot(db, *, campaign_id=CAMPAIGN, daily_budget, cost=0):
    db.add(NaverHourlySnapshot(
        snapshot_at=NOW, ad_date=TODAY, snapshot_hour=23, campaign_id=campaign_id,
        campaign_type="SHOPPING", cost=cost, clk=0, imp=0, daily_budget=daily_budget,
    ))
    db.commit()


def _patch_bep(value):
    """캠페인/그룹 매핑 BEP=None, 계정 기본 BEP=value(폴백 사다리 통과) — 압력·배분·deep_ok 공용."""
    return patch.multiple(
        expansion_pressure.campaign_target_resolver,
        weighted_product_value_for_campaign=lambda db, cid, col: None,
        account_default_bep_roas=lambda db: Decimal(str(value)),
    )


# ═══════════════════════ A. EX 제안 생성 배선(run_expansion_stage) ═══════════════════════

def test_A_expansion_mode_generates_ex_bid_up(db):
    """확장 모드 캠페인 → [EX확장] bid_up 제안 생성(target_type=adgroup·clk=자기정착clk·target_bid +15%)."""
    _settings(db)
    _campaign_entity(db)
    _adgroup(db, adgroup_id="ag-1", bid_amt=1000)
    _hourly_rank(db, adgroup_id="ag-1", avg_rank=Decimal("3.0"))  # 밴드 내(1층)
    # 캠페인 clk=40≥30·cost>0·ROAS 4.5/BEP 1.5=3.0≥1.25 → 확장. 그룹 own clk 40≥10·own ROAS≥BEP.
    _daily(db, adgroup_id="ag-1", ad_date=IN_WINDOW, clk=40, cost=1000, conv_amt=4500)
    with _patch_bep("1.5"), \
         patch.object(expansion_pressure.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR), \
         patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR), \
         patch.object(expansion_allocator.ctr_alert, "detect_ctr_alerts", return_value={"alerts": []}):
        n = proposal_pipeline.run_expansion_stage(db, today=TODAY)
    assert n == 1
    p = db.query(NaverProposal).filter(NaverProposal.proposal_type == "bid_up").one()
    assert p.target_type == "adgroup"
    assert p.target_id == "ag-1"
    assert p.rationale.startswith(expansion_pressure.EX_RATIONALE_PREFIX)
    assert "clk=40" in p.rationale
    assert p.target_bid == 1150  # 1000×1.15=1150(10원 내림)
    # 자기 정착창 clk(40)이 _extract_rationale_clk로 파싱돼야 P3 심사에 쓰인다.
    assert auto_operator._extract_rationale_clk(p.rationale) == 40


def test_A_non_expansion_generates_nothing_but_records_observe(db):
    """비확장(갭 미달) 캠페인 → bid_up 0건, 단 diary observe(판정)는 기록."""
    _settings(db)
    _campaign_entity(db)
    _adgroup(db, adgroup_id="ag-1", bid_amt=1000)
    # ROAS 1.0/BEP 1.5=0.667<1.25 → 비확장.
    _daily(db, adgroup_id="ag-1", ad_date=IN_WINDOW, clk=40, cost=1000, conv_amt=1000)
    with _patch_bep("1.5"), \
         patch.object(expansion_pressure.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        n = proposal_pipeline.run_expansion_stage(db, today=TODAY)
    assert n == 0
    assert db.query(NaverProposal).filter(NaverProposal.proposal_type == "bid_up").count() == 0
    observe = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.action == proposal_pipeline._EX_OBSERVE_ACTION).all()
    assert len(observe) == 1


def test_A_no_auto_operate_returns_zero(db):
    """auto_operate 캠페인 없음 → 0(판정 스킵)."""
    assert proposal_pipeline.run_expansion_stage(db, today=TODAY) == 0


# ═══════════════════════ B. P3 UP게이트 프라이어 폴백 ═══════════════════════

def _ex_proposal(clk, *, rationale_prefix=expansion_pressure.EX_RATIONALE_PREFIX):
    return SimpleNamespace(
        target_bid=1100, target_type="adgroup", target_id="ag-1", campaign_id=CAMPAIGN,
        rationale=f"{rationale_prefix} clk={clk} tier=1 judged_by=campaign_prior roas_ratio=2.0",
    )


def _alloc(*adgroup_ids):
    return [{"adgroup_id": aid, "rank_tier": 1, "own_clk": 3, "judged_by": "campaign_prior",
             "marginal_stop": False, "reason": "x", "weighted_rank": Decimal("3.0")}
            for aid in adgroup_ids]


def test_B_ex_unknown_prior_fallback_passes(db):
    """[EX확장] + ②미달(clk<10) + ③unknown + 확장모드 + 배분 목록에 포함 → 폴백 통과(None)."""
    p = _ex_proposal(3)
    with patch.object(auto_operator, "_live_current_bid", return_value=1000), \
         patch.object(auto_operator, "_settlement_roas_status", return_value=("unknown", "표본 부족")), \
         patch.object(auto_operator, "_bleeding_hold_reason", return_value=None), \
         patch.object(expansion_pressure, "judge_campaign_pressure",
                      return_value={"expansion_mode": True, "reason": "확장", "bep_roas": Decimal("1.5"),
                                    "roas_ratio": Decimal("2.0")}), \
         patch.object(auto_operator.expansion_allocator, "allocate_expansion", return_value=_alloc("ag-1")):
        got = auto_operator._check_bid_up_conditions(db, p, TODAY, ex_ctx=auto_operator._new_ex_review_ctx())
    assert got is None


def test_B_ex_membership_reverify_fails_holds(db):
    """★P1: [EX확장] + 확장모드지만 08:50 재실행 배분 목록에 이 그룹이 없으면 → hold(태그만 안 믿음)."""
    p = _ex_proposal(3)
    with patch.object(auto_operator, "_live_current_bid", return_value=1000), \
         patch.object(auto_operator, "_settlement_roas_status", return_value=("unknown", "표본 부족")), \
         patch.object(auto_operator, "_bleeding_hold_reason", return_value=None), \
         patch.object(expansion_pressure, "judge_campaign_pressure",
                      return_value={"expansion_mode": True, "reason": "확장", "bep_roas": Decimal("1.5")}), \
         patch.object(auto_operator.expansion_allocator, "allocate_expansion",
                      return_value=_alloc("ag-other")):  # 배분 목록에 ag-1 없음(위조 태그/탈락)
        got = auto_operator._check_bid_up_conditions(db, p, TODAY, ex_ctx=auto_operator._new_ex_review_ctx())
    assert got is not None and "멤버십 재검증 실패" in got


def test_B_ex_below_veto_holds(db):
    """[EX확장] + ②미달 + ③below(명시적 미달) → 폴백 불가·hold(거부권 유지, 배분 재검증 전 차단)."""
    p = _ex_proposal(3)
    with patch.object(auto_operator, "_live_current_bid", return_value=1000), \
         patch.object(auto_operator, "_settlement_roas_status", return_value=("below", "보정ROAS<목표")), \
         patch.object(auto_operator, "_bleeding_hold_reason", return_value=None):
        got = auto_operator._check_bid_up_conditions(db, p, TODAY, ex_ctx=auto_operator._new_ex_review_ctx())
    assert got is not None and got.startswith("③")


def test_B_ex_unknown_but_not_expansion_holds(db):
    """[EX확장] + ②미달 + ③unknown이나 캠페인 비확장 → 폴백 불가·hold."""
    p = _ex_proposal(3)
    with patch.object(auto_operator, "_live_current_bid", return_value=1000), \
         patch.object(auto_operator, "_settlement_roas_status", return_value=("unknown", "표본 부족")), \
         patch.object(auto_operator, "_bleeding_hold_reason", return_value=None), \
         patch.object(expansion_pressure, "judge_campaign_pressure",
                      return_value={"expansion_mode": False, "reason": "갭 부족"}):
        got = auto_operator._check_bid_up_conditions(db, p, TODAY, ex_ctx=auto_operator._new_ex_review_ctx())
    assert got is not None and "폴백 불가" in got


def test_B_non_ex_unknown_regression_holds(db):
    """비EX bid_up + ②미달 → 폴백 없이 즉시 hold(회귀 0) — ③ 판정도 안 감."""
    p = _ex_proposal(3, rationale_prefix="[bleeding_keywords]")  # 비EX 접두
    with patch.object(auto_operator, "_live_current_bid", return_value=1000), \
         patch.object(auto_operator, "_settlement_roas_status") as m_status:
        got = auto_operator._check_bid_up_conditions(db, p, TODAY, ex_ctx=auto_operator._new_ex_review_ctx())
    assert got is not None and got.startswith("②")
    m_status.assert_not_called()  # 비EX·clk 미달은 ③ 판정 전 즉시 hold


# ═══════════════════════ C. D-NAO-87 예산 봉투 ═══════════════════════

def _seed_30d_spend(db, *, campaign_id=CAMPAIGN, per_day_cost, days=1):
    """과거 30일 창(D-30~D-1) 안에 지출 시드(관측일수=days)."""
    for i in range(days):
        _daily(db, adgroup_id="ag-b", ad_date=TODAY - timedelta(days=i + 1),
               clk=0, cost=per_day_cost, conv_amt=0, campaign_id=campaign_id)


def test_C_envelope_04_scenario_needs_raise(db):
    """04 시나리오: 현재 30,000·30일 일평균 8,115 → 봉투 5만(바닥)·target 5만(+67%<+100%)·needs_raise."""
    _settings(db)
    _snapshot(db, daily_budget=30000)
    _seed_30d_spend(db, per_day_cost=8115, days=1)
    env = {e["campaign_id"]: e for e in budget_envelope.compute_envelopes(db, today=TODAY)}[CAMPAIGN]
    assert env["needs_raise"] is True
    assert env["envelope"] == 50000
    assert env["target_budget"] == 50000


def test_C_envelope_03_scenario_no_raise(db):
    """03 시나리오: 현재 50,000 ≥ 봉투 50,000 → 제안 0(needs_raise False)."""
    _settings(db)
    _snapshot(db, daily_budget=50000)
    _seed_30d_spend(db, per_day_cost=20000, days=1)  # 봉투 max(30000, 50000)=50000
    env = {e["campaign_id"]: e for e in budget_envelope.compute_envelopes(db, today=TODAY)}[CAMPAIGN]
    assert env["needs_raise"] is False
    assert env["envelope"] == 50000


def test_C_envelope_plus100_clamp(db):
    """+100% 클램프: 현재 20,000·봉투 60,000 → target=min(60000, 40000)=40,000."""
    _settings(db)
    _snapshot(db, daily_budget=20000)
    _seed_30d_spend(db, per_day_cost=40000, days=1)  # 봉투 max(60000, 50000)=60000
    env = {e["campaign_id"]: e for e in budget_envelope.compute_envelopes(db, today=TODAY)}[CAMPAIGN]
    assert env["needs_raise"] is True
    assert env["envelope"] == 60000
    assert env["target_budget"] == 40000


def test_C_envelope_uncapped_excluded(db):
    """uncapped(daily_budget=0) → needs_raise False + 사유(자동 감액 없음)."""
    _settings(db)
    _snapshot(db, daily_budget=0)
    _seed_30d_spend(db, per_day_cost=8000, days=1)
    env = {e["campaign_id"]: e for e in budget_envelope.compute_envelopes(db, today=TODAY)}[CAMPAIGN]
    assert env["needs_raise"] is False
    assert "uncapped" in env["reason"]


def test_C_envelope_missing_budget_fail_closed(db):
    """당일 hourly snapshot 부재 → daily_budget 미확보 fail-closed(needs_raise False)."""
    _settings(db)
    _seed_30d_spend(db, per_day_cost=8000, days=1)  # snapshot 없음
    env = {e["campaign_id"]: e for e in budget_envelope.compute_envelopes(db, today=TODAY)}[CAMPAIGN]
    assert env["needs_raise"] is False
    assert env["current_budget"] is None


def test_C_stage_generates_tagged_budget_up(db):
    """run_budget_envelope_stage → [예산봉투] 접두 budget_up 생성 + budget_auto_eligible 세팅."""
    _settings(db)
    _snapshot(db, daily_budget=30000)
    _seed_30d_spend(db, per_day_cost=8115, days=1)
    n = proposal_pipeline.run_budget_envelope_stage(db, today=TODAY)
    assert n == 1
    p = db.query(NaverProposal).filter(NaverProposal.proposal_type == "budget_up").one()
    assert p.rationale.startswith(budget_envelope.BUDGET_ENVELOPE_PREFIX)
    assert p.target_budget == 50000
    assert p.budget_auto_eligible is True  # 라운드 봉투 분류(5만<10만 캡)


def _budget_up_proposal(db, *, rationale, target_budget=50000, budget_auto_eligible=None):
    p = NaverProposal(
        proposal_type="budget_up", target_type="campaign", target_id=CAMPAIGN,
        campaign_id=CAMPAIGN, rationale=rationale, expected_effect="x",
        status="pending", target_budget=target_budget, budget_auto_eligible=budget_auto_eligible,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    db.query(NaverProposal).filter(NaverProposal.id == p.id).update(
        {"created_at": DAY_START_UTC + timedelta(hours=1)})
    db.commit()
    return p


def test_C_lane_auto_approves_tagged_only(db):
    """일 레인 봉투 심사: [예산봉투]∧자율분 budget_up만 자동 승인·집행. 비태그·라운드캡 초과분은
    pending 불변(Confirm 전용)."""
    _settings(db)
    tagged = _budget_up_proposal(db, rationale=f"{budget_envelope.BUDGET_ENVELOPE_PREFIX} 봉투 램프",
                                 budget_auto_eligible=True)
    untagged = _budget_up_proposal(db, rationale="[budget_allocator] 일예산 소진", budget_auto_eligible=True)
    overcap = _budget_up_proposal(db, rationale=f"{budget_envelope.BUDGET_ENVELOPE_PREFIX} 초과분",
                                  budget_auto_eligible=False)
    executed = []
    with patch.object(auto_operator, "_auto_operate_now", return_value=True), \
         patch.object(auto_operator.naver_execution_harness, "execute",
                      side_effect=lambda db, pid, **kw: executed.append(pid)):
        result = auto_operator.run_daily_lane(db, now=NOW)
    assert result["budget_reviewed"] == 1  # 태그∧자율분만 스코프
    assert result["budget_approved"] == 1
    assert result["budget_executed"] == 1
    assert executed == [tagged.id]
    db.refresh(tagged)
    db.refresh(untagged)
    db.refresh(overcap)
    assert tagged.status == "approved"
    assert untagged.status == "pending"  # 비태그 = Confirm 전용 불변
    assert overcap.status == "pending"   # 라운드캡 초과분 = Confirm 대기(캡 존속)


def _envelope_change_log(db, *, campaign_id=CAMPAIGN, changed_at=NOW):
    """오늘(KST) [예산봉투] 경로 성공 update_budget 집행 1건(복리 게이트 테스트용) —
    harness _execute_update_budget 성공 행 규격(dry_run=False·after_value 존재·접두 rationale)."""
    db.add(NaverChangeLog(
        entity_type="campaign", entity_id=campaign_id, campaign_id=campaign_id,
        action="update_budget", rationale=f"{budget_envelope.BUDGET_ENVELOPE_PREFIX} 봉투 램프",
        dry_run=False, after_value='{"dailyBudget": 50000}', changed_at=changed_at, executed_at=changed_at,
    ))
    db.commit()


def test_C_stage_skips_when_raised_today(db):
    """★P2 복리 차단(생성 단계): 오늘 이미 [예산봉투] 성공 집행 → 제안 생성 skip(0건)."""
    _settings(db)
    _snapshot(db, daily_budget=30000)
    _seed_30d_spend(db, per_day_cost=8115, days=1)
    _envelope_change_log(db, changed_at=NOW)  # 오늘 이미 성공 집행
    assert proposal_pipeline.run_budget_envelope_stage(db, today=TODAY) == 0


def test_C_lane_kst_daily_gate_holds(db):
    """★P2 복리 차단(일 레인 이중 게이트): 오늘 이미 성공 집행된 캠페인의 [예산봉투] pending은 hold."""
    _settings(db)
    tagged = _budget_up_proposal(db, rationale=f"{budget_envelope.BUDGET_ENVELOPE_PREFIX} 봉투 램프",
                                 budget_auto_eligible=True)
    _envelope_change_log(db, changed_at=NOW)
    with patch.object(auto_operator, "_auto_operate_now", return_value=True), \
         patch.object(auto_operator.naver_execution_harness, "execute") as m_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    assert result["budget_reviewed"] == 1
    assert result["budget_approved"] == 0
    m_exec.assert_not_called()
    db.refresh(tagged)
    assert tagged.status == "pending"  # 복리 게이트로 hold


# ═══════════════════════ Slack 통지(codex P2) ═══════════════════════

def test_slack_notify_emits_when_nonzero(db):
    """EX/봉투 제안이 있으면 전용 notify_text 1건(요약)."""
    sent = []
    with patch.object(proposal_pipeline.slack_notifier, "notify_text",
                      side_effect=lambda text, **kw: sent.append(text)):
        proposal_pipeline._notify_ex_budget(db, ex_n=2, budget_n=1)
    assert len(sent) == 1
    assert "확장 압력 UP 제안 2건" in sent[0]
    assert "예산 봉투 증액 제안 1건" in sent[0]


def test_slack_notify_skips_when_zero(db):
    """EX·봉투 둘 다 0건이면 통지 생략."""
    sent = []
    with patch.object(proposal_pipeline.slack_notifier, "notify_text",
                      side_effect=lambda text, **kw: sent.append(text)):
        proposal_pipeline._notify_ex_budget(db, ex_n=0, budget_n=0)
    assert sent == []


def test_slack_notify_fail_open(db):
    """통지 실패는 파이프라인을 죽이지 않는다(fail-open — 예외 삼킴)."""
    with patch.object(proposal_pipeline.slack_notifier, "notify_text",
                      side_effect=RuntimeError("slack down")):
        proposal_pipeline._notify_ex_budget(db, ex_n=1, budget_n=0)  # 예외 전파 안 됨


# ═══════════════════════ D. deep_ok 산출(_deep_expansion_ok) ═══════════════════════

PRIORS = {"adgroup:ag-1": Decimal("100")}


def _seed_deep_group(db, *, conv_amt):
    _campaign_entity(db)
    _adgroup(db, adgroup_id="ag-1", bid_amt=1000)
    _daily(db, adgroup_id="ag-1", ad_date=IN_WINDOW, clk=20, cost=1000, conv_amt=conv_amt)


def _deep(db, **kw):
    defaults = dict(campaign_id=CAMPAIGN, adgroup_id="ag-1", today=TODAY, settled_clk=20, response_priors=PRIORS)
    defaults.update(kw)
    return auto_operator._deep_expansion_ok(db, **defaults)


def test_D_deep_ok_all_conditions_met(db):
    """clk≥10 ∧ slope 프라이어 존재 ∧ 보정ROAS≥bep×1.25 → True."""
    _seed_deep_group(db, conv_amt=3000)  # ROAS 3.0/BEP 1.5=2.0≥1.25
    with _patch_bep_adgroup("1.5"), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        assert _deep(db) is True


def test_D_deep_ok_false_clk_below_10(db):
    """자기 정착창 clk<10 → False(조기 반환)."""
    _seed_deep_group(db, conv_amt=3000)
    assert _deep(db, settled_clk=5) is False


def test_D_deep_ok_false_no_slope_prior(db):
    """slope 프라이어 부재 → False."""
    _seed_deep_group(db, conv_amt=3000)
    assert _deep(db, response_priors={}) is False


def test_D_deep_ok_false_roas_below_ratio(db):
    """보정ROAS < bep×1.25 → False."""
    _seed_deep_group(db, conv_amt=1500)  # ROAS 1.5/BEP 1.5=1.0<1.25
    with _patch_bep_adgroup("1.5"), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        assert _deep(db) is False


def test_D_deep_ok_false_correction_unavailable(db):
    """보정계수 unavailable → False(무보정 추정으로 과열 진입 금지)."""
    _seed_deep_group(db, conv_amt=3000)
    with _patch_bep_adgroup("1.5"), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=UNAVAILABLE_FACTOR):
        assert _deep(db) is False


def _patch_bep_adgroup(value):
    """exploration.resolve_exploration_bep_roas 폴백 사다리 통과(그룹/캠페인 None·계정 기본=value)."""
    from app.services.naver_ad import exploration
    return patch.multiple(
        exploration.campaign_target_resolver,
        weighted_product_value_for_adgroup=lambda db, aid, col: None,
        weighted_product_value_for_campaign=lambda db, cid, col: None,
        account_default_bep_roas=lambda db: Decimal(str(value)),
    )
