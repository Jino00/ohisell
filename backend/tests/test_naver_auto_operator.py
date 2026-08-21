# test_naver_auto_operator.py — auto_operator 일 레인 + 시간당 레인 단위테스트 (D-NAO-49)
# 실 API 호출 0 — naver_sa_writer(라이브 재조회)와 naver_execution_harness.execute(실행)는
# 대부분 mock. 단, "쿨다운/가드레일 차단" 통합 테스트 1건만 harness.execute를 실제로 태워
# guardrail_gate까지 관통하는 것을 증명한다(naver_sa_writer만 최하단에서 mock).
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
    NaverAdDaily,
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverHourlySnapshot,
    NaverKeywordHourly,
    NaverProductBep,
    NaverProposal,
    NaverRetroSignal,
    OpsDiaryEntry,
)
from app.services.naver_ad import auto_operator
from app.services.naver_ad import bid_step_types, diary

CAMPAIGN = "cmp-04"
TODAY = date(2026, 7, 20)
NOW = datetime(2026, 7, 20, 8, 50, 0)  # 일 레인 크론 시각(KST naive)
DAY_START_UTC = datetime.combine(TODAY, datetime.min.time()) - timedelta(hours=9)


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


def _settings(db, *, campaign_id=CAMPAIGN, auto_operate=True, optimizer="ours",
              target_roas_override=None, seed_chain=True, seed_snapshot=True):
    """캠페인 설정 + (기본) 활성 부모체인·당일 스냅샷 시드.

    seed_chain: campaign 엔티티(on)+adgroup grp-1(on, parent=campaign) — codex 2R[P2]
      부모 체인 활성 요구를 기본 충족(키워드 테스트 엔티티는 parent_id='grp-1' 규약).
    seed_snapshot: 당일 NaverHourlySnapshot(cost=0, daily_budget=100000, snapshot_hour=23)
      — codex 2R[P1-2] 부재 fail-closed·codex 3R[P1-1] 신선도(snapshot_hour >= now.hour-1,
      23이면 어떤 now에도 신선)를 기본 통과 + IU1(D-NAO-66) 예산 여력 게이트(_budget_headroom_ok)
      가 읽을 daily_budget/cost도 기본 채운다(capped·여력 있음: cost 0 < 예산 10만). 브레이커/
      신선도/예산 소진 자체를 검증하는 테스트는 seed_snapshot=False로 끄고 직접 시드)."""
    db.add(NaverCampaignSettings(
        campaign_id=campaign_id, auto_operate=auto_operate, optimizer=optimizer,
        target_roas_override=target_roas_override,
    ))
    if seed_chain:
        db.add(NaverEntity(entity_type="campaign", entity_id=campaign_id,
                            campaign_id=campaign_id, status="on"))
        db.add(NaverEntity(entity_type="adgroup", entity_id="grp-1", parent_id=campaign_id,
                            campaign_id=campaign_id, status="on"))
    if seed_snapshot:
        db.add(NaverHourlySnapshot(
            snapshot_at=NOW, ad_date=TODAY, snapshot_hour=23, campaign_id=campaign_id,
            campaign_type="", cost=0, clk=0, imp=0, daily_budget=100000,
        ))
    db.commit()


def _proposal(db, *, proposal_type="bid_up", campaign_id=CAMPAIGN, target_type="keyword",
              target_id="nkw-1", target_bid=None, rationale="[bleeding_keywords] 보정ROAS=1.0 cost=100원 clk=15 — 시뮬 근거=x",
              status="pending", created_at=None, adgroup_id=None):
    p = NaverProposal(
        proposal_type=proposal_type, target_type=target_type, target_id=target_id,
        campaign_id=campaign_id, adgroup_id=adgroup_id, rationale=rationale,
        expected_effect="테스트 예상효과", status=status, target_bid=target_bid,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    ts = created_at if created_at is not None else DAY_START_UTC + timedelta(hours=1)
    db.query(NaverProposal).filter(NaverProposal.id == p.id).update({"created_at": ts})
    db.commit()
    db.refresh(p)
    return p


def _ad_row(db, *, campaign_id=CAMPAIGN, campaign_type="WEB_SITE", adgroup_id="grp-1",
            keyword_id="nkw-1", ad_date, imp=10, clk=5, cost=1000,
            conv_direct_amt=0, conv_indirect_amt=0):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id=keyword_id, imp=imp, clk=clk, cost=cost,
        conv_direct_amt=conv_direct_amt, conv_indirect_amt=conv_indirect_amt,
    ))
    db.commit()


def _retro_signal(db, *, asof_date, board, target_id, campaign_id=CAMPAIGN, grain="keyword",
                   direction="down"):
    db.add(NaverRetroSignal(
        created_at=NOW, asof_date=asof_date, board=board, direction=direction, grain=grain,
        target_id=target_id, campaign_id=campaign_id, cf_asof=1.0, bep_asof=1.0,
        target_asof=1.5, cost_asof=100,
    ))
    db.commit()


def _settlement_window():
    return auto_operator._settlement_window(TODAY)


# ══════════════════════════ 일 레인 ══════════════════════════

def test_daily_lane_reviews_nothing_when_no_auto_operate_campaign(db):
    _proposal(db)  # NaverCampaignSettings 행 자체 없음(auto_operate 기본 False 취급)
    result = auto_operator.run_daily_lane(db, now=NOW)
    assert result == {"reviewed": 0, "approved": 0, "executed": 0, "held": [], "failed": 0,
                      "rejected_stale": 0,
                      "budget_reviewed": 0, "budget_approved": 0, "budget_executed": 0,
                      "budget_failed": 0, "budget_rejected_stale": 0}


def test_daily_lane_ignores_non_auto_operate_campaign(db):
    _settings(db, campaign_id="cmp-other", auto_operate=False)
    _proposal(db, campaign_id="cmp-other", proposal_type="bid_down")
    result = auto_operator.run_daily_lane(db, now=NOW)
    assert result["reviewed"] == 0


def test_daily_lane_ignores_informational_proposal(db):
    _settings(db)
    _proposal(db, proposal_type="trigger_pacing", target_type="campaign", target_id=CAMPAIGN)
    result = auto_operator.run_daily_lane(db, now=NOW)
    assert result["reviewed"] == 0


def test_daily_lane_stale_pending_not_reviewed_but_rejected_for_regeneration(db):
    """codex 11R[P2]: 어제 생성 stale pending은 심사 대상이 아니지만(당일 생성분만),
    pending인 채 남기면 proposal_writer.persist dedup에 걸려 다음 날 갱신 제안이 영구히
    안 생긴다(959~961 좌초 시나리오) — 레인 말미에 rejected 처리해 일일 재생성 사이클 보장."""
    _settings(db)
    p = _proposal(db, proposal_type="bid_down", created_at=DAY_START_UTC - timedelta(hours=1))
    result = auto_operator.run_daily_lane(db, now=NOW)
    assert result["reviewed"] == 0  # 심사는 여전히 당일 생성분만
    assert result["rejected_stale"] == 1
    db.refresh(p)
    assert p.status == "rejected"
    assert "일일 사이클" in p.rationale


def test_daily_lane_held_bid_up_rejected_at_end_for_regeneration(db):
    """codex 11R[P2]: 오늘 hold된 bid_up도 pending으로 남기면 dedup 좌초 — 레인 말미에
    rejected(+사유) 처리, 익일 08:00 생성기가 fresh rationale로 재생성 → 08:50 재심사."""
    p, current_bid = _seed_bid_up_happy_path(db, clk=3)  # 조건② 미충족 → hold
    with patch.object(auto_operator.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": current_bid}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_not_called()
    assert len(result["held"]) == 1  # hold 사유 기록은 유지
    assert result["rejected_stale"] == 1
    db.refresh(p)
    assert p.status == "rejected"
    assert "auto_op 보류" in p.rationale


def test_daily_lane_reject_sweep_never_touches_non_auto_campaign(db):
    """경계: auto_operate=False 캠페인의 pending은 절대 건드리지 않음."""
    _settings(db)  # auto 캠페인(cmp-04)
    _settings(db, campaign_id="cmp-manual", auto_operate=False, seed_chain=False, seed_snapshot=False)
    p_manual = _proposal(db, campaign_id="cmp-manual", proposal_type="bid_down",
                          created_at=DAY_START_UTC - timedelta(days=2))
    result = auto_operator.run_daily_lane(db, now=NOW)
    assert result["rejected_stale"] == 0
    db.refresh(p_manual)
    assert p_manual.status == "pending"


def test_daily_lane_bid_down_always_approved(db):
    _settings(db)
    p = _proposal(db, proposal_type="bid_down", target_bid=900)
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_called_once_with(db, p.id, dry_run=False, now=NOW)
    assert result["reviewed"] == 1
    assert result["approved"] == 1
    assert result["held"] == []
    db.refresh(p)
    assert p.status == "approved"
    assert p.approval_source == "auto_op"  # codex 2R[P1-1] String(12) 계약 준수 단축


def test_daily_lane_bid_down_held_when_target_entity_deleted(db):
    """deleted 엔티티 사전 제외(2026-07-21 실사고): shopping_group_bep 보드가 NaverAdDaily
    집계만 보고 후보를 뽑아, naver_entity status='deleted'인 그룹(맥세이프 69087677/69089452)에
    bid_down 제안이 생성됨 → 일 레인이 무조건 승인 → harness에서 네이버 API 404
    (current_bid 미확보 fail-closed) 매일 반복. 일 레인 심사에서 status!='on' 타깃을 hold로
    사전 제외해 404 실행 시도 자체를 없앤다."""
    _settings(db)
    db.add(NaverEntity(entity_type="adgroup", entity_id="grp-del", parent_id=CAMPAIGN,
                        campaign_id=CAMPAIGN, status="deleted"))
    db.commit()
    p = _proposal(db, proposal_type="bid_down", target_type="adgroup", target_id="grp-del",
                  target_bid=900)
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_not_called()
    assert result["approved"] == 0
    assert len(result["held"]) == 1
    assert result["held"][0]["id"] == p.id
    assert "deleted" in result["held"][0]["reason"]
    db.refresh(p)
    assert p.status == "rejected"  # codex 11R: hold분은 레인 말미 reject(익일 재생성 사이클)


def test_daily_lane_bid_down_held_when_target_entity_off(db):
    """status='off'(수동 정지)도 사전 제외 — 정지 그룹 입찰 조정은 무의미하고, 재개 판단은
    별도 경로(resume) 몫."""
    _settings(db)
    db.add(NaverEntity(entity_type="adgroup", entity_id="grp-off", parent_id=CAMPAIGN,
                        campaign_id=CAMPAIGN, status="off"))
    db.commit()
    _proposal(db, proposal_type="bid_down", target_type="adgroup", target_id="grp-off",
              target_bid=900)
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_not_called()
    assert result["approved"] == 0
    assert len(result["held"]) == 1
    assert "off" in result["held"][0]["reason"]


def test_daily_lane_bid_down_approved_when_target_entity_on(db):
    """경계(과차단 방지): status='on' 엔티티는 가드에 걸리지 않고 기존대로 무조건 승인."""
    _settings(db)  # seed_chain이 grp-1(on) 시드
    p = _proposal(db, proposal_type="bid_down", target_type="adgroup", target_id="grp-1",
                  target_bid=900)
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_called_once_with(db, p.id, dry_run=False, now=NOW)
    assert result["approved"] == 1
    assert result["held"] == []


def test_daily_lane_pause_held_when_target_entity_deleted(db):
    """가드는 일 레인 전 타입 공통(bid_up/bid_down/pause) — deleted 타깃 pause도 404행."""
    _settings(db)
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-del", parent_id="grp-1",
                        campaign_id=CAMPAIGN, status="deleted"))
    db.commit()
    _proposal(db, proposal_type="pause", target_type="keyword", target_id="nkw-del")
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_not_called()
    assert result["approved"] == 0
    assert len(result["held"]) == 1
    assert "deleted" in result["held"][0]["reason"]


def test_daily_lane_pause_held_when_recent_external_stop(db):
    _settings(db)
    p = _proposal(db, proposal_type="pause", target_type="keyword", target_id="nkw-pause")
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-pause", campaign_id=CAMPAIGN,
        action="external_status_change", dry_run=False,
        after_value=json.dumps({"userLock": True}), changed_at=NOW - timedelta(hours=1),
    ))
    db.commit()
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_not_called()
    assert result["approved"] == 0
    assert len(result["held"]) == 1
    assert result["held"][0]["id"] == p.id
    assert "D-NAO-40" in result["held"][0]["reason"]
    db.refresh(p)
    assert p.status == "rejected"  # codex 11R: hold분은 레인 말미 reject(익일 재생성 사이클)


def test_daily_lane_pause_approved_when_no_external_stop(db):
    _settings(db)
    p = _proposal(db, proposal_type="pause", target_type="keyword", target_id="nkw-pause2")
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_called_once_with(db, p.id, dry_run=False, now=NOW)
    assert result["approved"] == 1
    assert result["held"] == []


def _seed_bid_up_happy_path(db, *, target_id="nkw-1", current_bid=1000, target_bid=1100,
                             clk=15, exclude_bleeding=True, roas_row=True):
    _settings(db, target_roas_override=Decimal("2.0"))
    rationale = f"[bleeding_keywords] 보정ROAS=1.0 cost=100원 clk={clk} — 시뮬 근거=x, 추천입찰={target_bid}원"
    p = _proposal(db, proposal_type="bid_up", target_id=target_id, target_bid=target_bid,
                  rationale=rationale)
    window_from, window_to = _settlement_window()
    if roas_row:
        # roas_naver = conv_amt/cost = 3000/1000 = 3.0 >= target 2.0
        _ad_row(db, keyword_id=target_id, ad_date=window_from + timedelta(days=1),
                imp=50, clk=20, cost=1000, conv_direct_amt=3000)
    if exclude_bleeding:
        # 최신 소급채점은 있지만(latest_asof 확보) 이 target은 그 보드에 없음(안 걸림)
        _retro_signal(db, asof_date=TODAY - timedelta(days=1), board="bleeding_keywords",
                      target_id="nkw-other")
    return p, current_bid


def test_daily_lane_bid_up_all_4_conditions_met_approved_and_executed(db):
    p, current_bid = _seed_bid_up_happy_path(db)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": current_bid}), \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "actual_revenue_ratio"}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)

    mock_exec.assert_called_once_with(db, p.id, dry_run=False, now=NOW)
    assert result["reviewed"] == 1
    assert result["approved"] == 1
    assert result["held"] == []
    db.refresh(p)
    assert p.status == "approved"
    assert p.approval_source == "auto_op"  # codex 2R[P1-1] String(12) 계약 준수 단축


def test_daily_lane_bid_up_condition1_fails_step_clamp_out_of_range(db):
    # target_bid=1500이 현재가 1000 대비 +50% — ±15% 상한 이탈
    p, current_bid = _seed_bid_up_happy_path(db, current_bid=1000, target_bid=1500)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": current_bid}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_not_called()
    assert result["approved"] == 0
    assert len(result["held"]) == 1
    assert "①" in result["held"][0]["reason"]
    db.refresh(p)
    # harness에 안 넘어가 'failed' 영구 종결은 아니지만, 레인 말미 sweep이 rejected 처리
    # (codex 11R — pending 잔존 시 dedup 좌초, 익일 생성기가 fresh 데이터로 재생성)
    assert p.status == "rejected"


def test_daily_lane_bid_up_condition2_fails_click_below_10(db):
    p, current_bid = _seed_bid_up_happy_path(db, clk=3)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": current_bid}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_not_called()
    assert "②" in result["held"][0]["reason"]


def test_daily_lane_bid_up_condition3_fails_no_settlement_roas_evidence(db):
    p, current_bid = _seed_bid_up_happy_path(db, roas_row=False)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": current_bid}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_not_called()
    assert "③" in result["held"][0]["reason"]


def test_daily_lane_bid_up_condition4_stale_retro_asof_holds(db):
    """codex 4R[P1]: 08:30 retro 크론이 실패해 당일 as-of(=오늘-1) 행이 없으면, 과거
    성적표에서의 부재를 "bleeding 아님"으로 해석해 bid_up이 자동 실행되면 안 된다
    (fail-open 차단). latest_asof < 기대 as-of(오늘-1) → 조건④ 미충족 hold."""
    p, current_bid = _seed_bid_up_happy_path(db, exclude_bleeding=False)
    # 소급채점 최신 행이 이틀 전 as-of뿐(어제 as-of 없음) — 이 target은 그 보드에도 없음
    _retro_signal(db, asof_date=TODAY - timedelta(days=2), board="bleeding_keywords",
                  target_id="nkw-other")
    with patch.object(auto_operator.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": current_bid}), \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "actual_revenue_ratio"}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_not_called()
    assert result["approved"] == 0
    assert len(result["held"]) == 1
    assert "④" in result["held"][0]["reason"]
    assert "stale" in result["held"][0]["reason"]
    db.refresh(p)
    assert p.status == "rejected"  # codex 11R: hold분은 레인 말미 reject(익일 재생성 사이클)


def test_daily_lane_bid_down_unaffected_by_stale_retro(db):
    """조건④는 bid_up 전용 — 소급채점이 stale이어도 bid_down은 무조건 승인(안전 방향)."""
    _settings(db)
    _retro_signal(db, asof_date=TODAY - timedelta(days=2), board="bleeding_keywords",
                  target_id="nkw-other")
    p = _proposal(db, proposal_type="bid_down", target_bid=900)
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_called_once_with(db, p.id, dry_run=False, now=NOW)
    assert result["approved"] == 1


def test_daily_lane_bid_up_condition4_fails_currently_bleeding(db):
    p, current_bid = _seed_bid_up_happy_path(db, exclude_bleeding=False, target_id="nkw-1")
    window_from, window_to = _settlement_window()
    _retro_signal(db, asof_date=TODAY - timedelta(days=1), board="bleeding_keywords",
                  target_id="nkw-1")
    with patch.object(auto_operator.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": current_bid}), \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "actual_revenue_ratio"}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_not_called()
    assert "④" in result["held"][0]["reason"]


# ── codex 5R[P1-1]: 보정계수 unavailable → ROAS 검증 불가 = fail-closed hold ──

def test_daily_lane_bid_up_held_when_correction_factor_unavailable(db):
    """correction_factor()가 실주문 매출 부재 시 factor=1·source='unavailable'을 반환 —
    그걸 검증된 보정ROAS처럼 쓰면 데이터 정전 시 무보정 convAmt/cost로 bid_up이 승인된다.
    source != 'actual_revenue_ratio'면 조건③ 미충족 hold."""
    p, current_bid = _seed_bid_up_happy_path(db)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": current_bid}), \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "unavailable",
                                     "window_revenue": 0, "window_conv_amt": 0}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_not_called()
    assert result["approved"] == 0
    assert len(result["held"]) == 1
    assert "③" in result["held"][0]["reason"]
    assert "unavailable" in result["held"][0]["reason"]
    db.refresh(p)
    assert p.status == "rejected"  # codex 11R: hold분은 레인 말미 reject(익일 재생성 사이클)


def test_hourly_lane_up_held_when_correction_factor_unavailable(db):
    """시간당 UP도 동일 — 보정계수 unavailable이면 정착ROAS 조건 미충족 → hold.
    DOWN 경로는 영향 없음(안전 방향, 보정 불요)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-up", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-up", ad_date=window_from, clk=20, cost=7000, conv_direct_amt=21000)
    db.commit()

    up_curve = [
        _hour(10, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(11, imp=15, clk=2, cost=35, avg_rank=5.0),
    ]
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "unavailable",
                                     "window_revenue": 0, "window_conv_amt": 0}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_midday, fetch_intraday=lambda tid, d: up_curve,
        )
    mock_exec.assert_not_called()
    assert result["approved"] == 0
    # DL4: rank>4(밴드 하단)인데 UP 게이트가 ROAS(보정계수 unavailable)에서 막힘 → "재시작 대기"
    assert "재시작 대기" in result["held"][0]["reason"]


def test_hourly_lane_down_unaffected_by_correction_factor_unavailable(db):
    """DOWN은 순위/CPC 기반(안전 방향) — 보정계수 상태와 무관하게 진행."""
    _settings(db)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-down", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-down", ad_date=window_from, clk=10, cost=100)
    db.commit()

    curve = [
        _hour(6, imp=15, clk=2, cost=100, avg_rank=2.0),
        _hour(7, imp=15, clk=2, cost=100, avg_rank=2.0),
    ]
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "unavailable",
                                     "window_revenue": 0, "window_conv_amt": 0}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=NOW, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_called_once()


# ── codex 5R[P1-2]: 킬스위치 실행 직전 재확인 — 레인 도중 OFF 시 즉시 정지 ──

def test_daily_lane_kill_switch_mid_run_stops_remaining_executions(db):
    """레인 시작 시 auto_operate 1회 스냅샷만 믿으면, 실행 도중 Jino가 OFF 해도 남은
    실입찰이 진행된다("즉시 정지" 계약 위반). 각 제안의 승인·실행 직전 DB 재조회 — 첫
    실행의 부수효과로 플래그가 꺼지면 두 번째 제안은 미실행."""
    _settings(db)
    p1 = _proposal(db, proposal_type="bid_down", target_id="nkw-k1", target_bid=900)
    p2 = _proposal(db, proposal_type="bid_down", target_id="nkw-k2", target_bid=900)

    def _execute_and_kill(db_arg, proposal_id, **kw):
        db.query(NaverCampaignSettings).filter(
            NaverCampaignSettings.campaign_id == CAMPAIGN
        ).update({"auto_operate": False})
        db.commit()

    with patch.object(auto_operator.naver_execution_harness, "execute",
                       side_effect=_execute_and_kill) as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)

    assert mock_exec.call_count == 1  # 첫 제안만 실행
    assert mock_exec.call_args[0][1] == p1.id
    assert result["approved"] == 1
    held_reasons = [h["reason"] for h in result["held"]]
    assert any("킬스위치" in r for r in held_reasons)
    db.refresh(p2)
    assert p2.status == "pending"  # 두 번째는 승인조차 안 됨


def test_auto_operate_now_sees_external_commit_via_independent_connection(tmp_path):
    """codex 6R[P1]: 세션 경유 조회는 같은 Session 트랜잭션 안 — SQLite(WAL)에서 리더는
    트랜잭션 시작 시점 스냅샷을 보므로 타 프로세스의 OFF 커밋이 안 보일 수 있다.
    _auto_operate_now는 엔진 레벨 독립 커넥션(새 트랜잭션)으로 조회해야 한다 — 파일 기반
    DB(커넥션 실분리, in-memory StaticPool은 커넥션 공유라 검증 불가)에서: 세션이 먼저
    읽기 트랜잭션을 연 상태 → 별도 커넥션으로 OFF 커밋 → False 반환을 증명."""
    from sqlalchemy import create_engine as _create_engine, event

    db_file = tmp_path / "kill_switch.db"
    engine = _create_engine(f"sqlite:///{db_file}")

    @event.listens_for(engine, "connect")
    def _set_wal(dbapi_conn, _rec):  # WAL — 스냅샷 격리가 실재하는 모드로 검증
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed = Session()
    seed.add(NaverCampaignSettings(campaign_id=CAMPAIGN, auto_operate=True, optimizer="ours"))
    seed.commit()
    seed.close()

    lane_session = Session()
    try:
        # 레인이 조기 쿼리로 읽기 트랜잭션을 연 상태를 재현
        assert lane_session.query(NaverCampaignSettings).filter(
            NaverCampaignSettings.campaign_id == CAMPAIGN
        ).one().auto_operate is True

        # 타 프로세스(콘솔)가 별도 커넥션으로 킬스위치 OFF 커밋
        with engine.connect() as other:
            other.execute(
                NaverCampaignSettings.__table__.update()
                .where(NaverCampaignSettings.campaign_id == CAMPAIGN)
                .values(auto_operate=False)
            )
            other.commit()

        # 독립 커넥션 조회 — 세션 스냅샷과 무관하게 OFF가 보여야 함
        assert auto_operator._auto_operate_now(lane_session, CAMPAIGN) is False
        # 행 부재도 False(fail-closed) 유지
        assert auto_operator._auto_operate_now(lane_session, "cmp-none") is False
    finally:
        lane_session.close()
        engine.dispose()


def test_daily_lane_sweep_ad_level_pending_never_rejected(db):
    """B3 GATE P2-2: ad-레벨(소재) pending은 sweep에서도 절대 rejected 안 됨 — 콘솔 Confirm
    대기 정상 상태(rejected 처리하면 승인 창 자체가 소멸). 같은 캠페인의 non-ad stale은 정리됨."""
    _settings(db)  # cmp-04 auto_operate=True
    p_ad = _proposal(db, proposal_type="bid_down", target_type="ad", target_id="ad-1",
                     created_at=DAY_START_UTC - timedelta(hours=1))
    p_kw = _proposal(db, proposal_type="bid_down", target_type="keyword", target_id="nkw-z",
                     created_at=DAY_START_UTC - timedelta(hours=1))
    result = auto_operator.run_daily_lane(db, now=NOW)
    assert result["rejected_stale"] == 1  # keyword만
    db.refresh(p_ad)
    db.refresh(p_kw)
    assert p_ad.status == "pending"   # 소재 = Confirm 대기 보존
    assert p_kw.status == "rejected"  # non-ad stale = 재생성 사이클


def test_daily_lane_sweep_toctou_off_in_window_not_rejected(tmp_path):
    """codex 12R[P2] "정지 ≠ 폐기" TOCTOU 엄격 봉쇄 — 회귀 테스트.

    초판 버그: sweep이 auto_operate 집합을 파이썬으로 먼저 굳힌 뒤 그 정적 집합으로 UPDATE 해,
    "심사 종료 후 ~ reject 커밋 전" 창에서 캠페인이 OFF로 뒤집혀도 그 pending을 여전히 rejected
    처리한다. 수정: 프레시 게이트(rollback)로 스냅샷을 종료하고, 킬스위치(auto_operate IS TRUE)를
    EXISTS 상관 서브쿼리로 원자 UPDATE에 바인딩 → 창 안에서 커밋된 OFF를 UPDATE가 실제로 본다.

    결정론: 실스레드/sleep 없이 _sweep_precommit_seam(프레시 게이트 직후·UPDATE 직전 호출되는
    prod no-op)을 patch해, 창 정확히 그 지점에서 타 프로세스(별도 커넥션)가 OFF를 커밋하도록
    주입한다. 파일 기반 WAL DB 필수(in-memory StaticPool은 커넥션 공유라 '타 프로세스' 재현 불가,
    _auto_operate_now 독립커넥션 테스트와 동일 이유).

    검증: 창 안에서 OFF 된 캠페인(cmp-off)의 stale pending은 pending 유지, 여전히 ON인
    캠페인(cmp-on)의 stale pending은 rejected. rejected_stale = 실제 UPDATE 행 수(cmp-on분만)."""
    from sqlalchemy import create_engine as _create_engine, event

    db_file = tmp_path / "sweep_toctou.db"
    engine = _create_engine(f"sqlite:///{db_file}")

    @event.listens_for(engine, "connect")
    def _set_wal(dbapi_conn, _rec):  # WAL — 스냅샷 격리가 실재하는 모드
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    C_OFF = "cmp-off"  # 창 안에서 OFF 될 캠페인
    C_ON = "cmp-on"    # 계속 ON 인 캠페인
    stale = DAY_START_UTC - timedelta(hours=1)  # 심사 대상 아님(당일 생성분만) → sweep 직행

    seed = Session()
    for cid in (C_OFF, C_ON):
        seed.add(NaverCampaignSettings(campaign_id=cid, auto_operate=True, optimizer="ours"))
    seed.commit()
    made = {}
    for cid in (C_OFF, C_ON):
        for i in range(2):
            p = NaverProposal(proposal_type="bid_down", target_type="keyword",
                              target_id=f"{cid}-kw{i}", campaign_id=cid, status="pending")
            seed.add(p)
            seed.commit()
            seed.query(NaverProposal).filter(NaverProposal.id == p.id).update({"created_at": stale})
            made[(cid, i)] = p.id
    # ad-레벨 pending(계속 ON 캠페인) — sweep 제외(Confirm 대기) 검증
    p_ad = NaverProposal(proposal_type="bid_down", target_type="ad", target_id=f"{C_ON}-ad",
                         campaign_id=C_ON, status="pending")
    seed.add(p_ad)
    seed.commit()
    seed.query(NaverProposal).filter(NaverProposal.id == p_ad.id).update({"created_at": stale})
    ad_id = p_ad.id
    seed.commit()
    seed.close()

    lane_session = Session()
    fired = {"n": 0}

    def _external_off(_db_arg):
        # 창 안(프레시 게이트 후~UPDATE 전)에서 타 프로세스가 cmp-off 킬스위치 OFF 커밋
        fired["n"] += 1
        with engine.connect() as other:
            other.execute(
                NaverCampaignSettings.__table__.update()
                .where(NaverCampaignSettings.campaign_id == C_OFF)
                .values(auto_operate=False)
            )
            other.commit()

    try:
        with patch.object(auto_operator, "_sweep_precommit_seam", side_effect=_external_off):
            result = auto_operator.run_daily_lane(lane_session, now=NOW)

        assert fired["n"] == 1  # seam이 실제로 창 안에서 발동했는지 확인(테스트 무효화 방지)
        # cmp-off: 창 안 OFF → pending 유지(정지 ≠ 폐기)
        for i in range(2):
            row = lane_session.query(NaverProposal).filter(
                NaverProposal.id == made[(C_OFF, i)]).one()
            assert row.status == "pending", f"cmp-off pending #{i} 가 잘못 rejected 됨(TOCTOU)"
        # cmp-on: 여전히 ON → rejected(재생성 사이클)
        for i in range(2):
            row = lane_session.query(NaverProposal).filter(
                NaverProposal.id == made[(C_ON, i)]).one()
            assert row.status == "rejected", f"cmp-on stale pending #{i} 는 정리돼야 함"
            assert "일일 사이클" in row.rationale
        # ad-레벨: Confirm 대기 보존
        assert lane_session.query(NaverProposal).filter(
            NaverProposal.id == ad_id).one().status == "pending"
        # rejected_stale = 실제 UPDATE 행 수 = cmp-on non-ad 2건뿐
        assert result["rejected_stale"] == 2
    finally:
        lane_session.close()
        engine.dispose()


def _capture_sweep_statements(db, *, force_dialect=None, monkeypatch=None):
    """sweep이 실제 실행하는 statement를 모두 가로채 반환(방언 분기 배선 검증용).
    force_dialect: 'postgresql'로 주면 bind dialect.name을 덮어 Postgres 분기를 태운다
    (with_for_update()는 실제 sqlite 엔진에서 실행 시 무해히 생략되지만, 캡처한 Select를
    postgresql dialect로 컴파일하면 FOR UPDATE 배선을 검증할 수 있다)."""
    from sqlalchemy.orm import Session as _Session

    _settings(db)
    _proposal(db, proposal_type="bid_down", target_id="nkw-lock",
              created_at=DAY_START_UTC - timedelta(hours=1))  # stale → sweep 직행

    if force_dialect is not None:
        monkeypatch.setattr(db.get_bind().dialect, "name", force_dialect)

    captured = []
    orig_execute = _Session.execute

    def _spy(self, statement, *a, **k):
        captured.append(statement)
        return orig_execute(self, statement, *a, **k)

    with patch.object(_Session, "execute", _spy):
        result = auto_operator.run_daily_lane(db, now=NOW)
    return captured, result


def test_daily_lane_sweep_sqlite_uses_exists_no_for_update(db):
    """codex 12R[P1 r2] SQLite 분기 배선 — pre-SELECT 없이 킬스위치를 EXISTS로 UPDATE에 바인딩.
    SQLite는 FOR UPDATE를 조용히 생략하므로 pre-SELECT-락은 파이썬 집합을 굳혀 창을 재개방한다
    (라운드2 결함). 따라서 SQLite에선 (a) naver_campaign_settings 대상 FOR UPDATE SELECT를
    발행하지 않고, (b) reject UPDATE가 EXISTS(naver_campaign_settings)를 WHERE에 담아야 한다."""
    from sqlalchemy.dialects import sqlite as sqlite_dialect
    from sqlalchemy.sql import Update

    captured, result = _capture_sweep_statements(db)
    assert result["rejected_stale"] == 1  # sweep 실동작(EXISTS-bound UPDATE 관통)

    def _sq(stmt):
        try:
            return str(stmt.compile(dialect=sqlite_dialect.dialect()))
        except Exception:
            return ""

    # (a) settings 대상 FOR UPDATE SELECT 없음(sqlite는 아예 pre-SELECT-락을 안 씀)
    assert not any("FOR UPDATE" in _sq(s) for s in captured), \
        "SQLite 분기는 FOR UPDATE를 발행하면 안 됨(생략되어 창 재개방)"
    # (b) reject UPDATE(naver_proposals 대상)가 EXISTS(naver_campaign_settings) 바인딩
    reject_updates = [
        s for s in captured
        if isinstance(s, Update) and "naver_proposals" in (sql := _sq(s))
        and "EXISTS" in sql.upper() and "naver_campaign_settings" in sql
    ]
    assert reject_updates, "SQLite 분기 reject UPDATE는 EXISTS(naver_campaign_settings)를 담아야 함"


def test_daily_lane_sweep_postgres_uses_for_update_lock(db, monkeypatch):
    """codex 12R[P1 r2] Postgres 분기 배선 — 라이브 auto 집합을 with_for_update()로 SELECT-락.
    실제 동시-인터리빙 검증은 Postgres CI 필요(여기 없음) → dialect.name을 postgresql로 덮어
    분기를 태우고, sweep이 발행한 settings SELECT를 postgresql dialect로 컴파일해 'FOR UPDATE'
    배선을 정직하게 대체 검증한다. 이 분기의 reject UPDATE는 EXISTS가 아니라 in_(locked_live)."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.sql import Select, Update

    captured, result = _capture_sweep_statements(db, force_dialect="postgresql", monkeypatch=monkeypatch)
    assert result["rejected_stale"] == 1  # sweep 실동작(락 SELECT → in_ UPDATE 관통)

    def _pg(stmt):
        try:
            return str(stmt.compile(dialect=postgresql.dialect()))
        except Exception:
            return ""

    for_update_selects = [
        s for s in captured
        if isinstance(s, Select) and "FOR UPDATE" in (sql := _pg(s))
        and "naver_campaign_settings" in sql
    ]
    assert for_update_selects, (
        "Postgres 분기는 라이브 auto 집합을 with_for_update()로 락 SELECT 해야 함 — "
        "postgresql dialect 컴파일에서 naver_campaign_settings 대상 'FOR UPDATE'가 없음"
    )
    # reject UPDATE는 EXISTS 없이 in_(locked_live)만(락 SELECT가 이미 라이브 집합 확정)
    reject_updates = [s for s in captured if isinstance(s, Update) and "naver_proposals" in _pg(s)]
    assert reject_updates and all("EXISTS" not in _pg(s).upper() for s in reject_updates), \
        "Postgres 분기 reject UPDATE는 EXISTS가 아니라 in_(locked_live)여야 함"


def test_hourly_lane_kill_switch_mid_run_stops_remaining_executions(db):
    """시간당 레인 동일 — 첫 실행 부수효과로 OFF 시 두 번째 핫셋 유닛은 제안 생성/실행 없음."""
    _settings(db)
    window_from, window_to = _settlement_window()
    for eid, day_offset in (("nkw-ka", 0), ("nkw-kb", 1)):
        db.add(NaverEntity(entity_type="keyword", entity_id=eid, parent_id="grp-1",
                            campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
        _ad_row(db, keyword_id=eid, ad_date=window_from + timedelta(days=day_offset),
                clk=10, cost=100)
    db.commit()

    curve = [
        _hour(6, imp=15, clk=2, cost=100, avg_rank=2.0),
        _hour(7, imp=15, clk=2, cost=100, avg_rank=2.0),
    ]  # rank<2.5 → down 판정(두 유닛 모두)

    def _execute_and_kill(db_arg, proposal_id, **kw):
        db.query(NaverCampaignSettings).filter(
            NaverCampaignSettings.campaign_id == CAMPAIGN
        ).update({"auto_operate": False})
        db.commit()

    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute",
                       side_effect=_execute_and_kill) as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda tid, d: curve)

    assert mock_exec.call_count == 1  # 첫 유닛만 실행
    assert result["approved"] == 1
    held_reasons = [h["reason"] for h in result["held"]]
    assert any("킬스위치" in r for r in held_reasons)
    # 두 번째 유닛의 제안은 생성되지 않아야 함(승인 직전 재확인에서 차단)
    assert db.query(NaverProposal).filter(NaverProposal.target_id == "nkw-kb").count() == 0


# ── codex 7R[P1]: 킬스위치 최종 가드 = harness 쓰기 직전(TOCTOU 봉쇄, auto_op* 한정) ──

def test_harness_refuses_auto_op_proposal_when_kill_switch_off(db):
    """승인 커밋~harness 쓰기 사이에 킬스위치가 OFF 되면(레인 pre-check는 이미 통과) 실입찰이
    나가면 안 된다 — harness 실행 진입점이 approval_source가 auto_op/auto_op_hr인 제안에
    한해 _auto_operate_now를 쓰기 직전 재확인. OFF면 writer 미호출·change_log 미기록·
    proposal은 approved인 채 미실행(정직 상태)."""
    from app.services.naver_ad import naver_execution_harness as harness
    from app.services.naver_ad import naver_sa_writer

    _settings(db, auto_operate=False)  # 승인 후 OFF 된 상태를 재현(optimizer='ours' 유지)
    p = _proposal(db, proposal_type="bid_down", target_id="nkw-ks", target_bid=850,
                  status="approved")
    p.approval_source = auto_operator.APPROVAL_SOURCE_HOURLY
    db.commit()

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        with pytest.raises(harness.KillSwitchEngagedError):
            harness.execute(db, p.id, dry_run=False)

    mock_write.assert_not_called()
    db.refresh(p)
    assert p.status == "approved"  # failed로 종결하지 않음 — 미실행 정직 상태
    assert p.executed_change_log_id is None
    assert db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).count() == 0


def test_harness_executes_manual_proposal_even_when_auto_operate_off(db):
    """비영향 가드: 수동 콘솔 승인(approval_source NULL) 제안은 auto_operate=False여도
    정상 실행 — 킬스위치는 auto_operator 레인 전용이며 수동 운영을 막지 않는다."""
    from app.services.naver_ad import naver_execution_harness as harness
    from app.services.naver_ad import naver_sa_writer

    _settings(db, auto_operate=False)
    p = _proposal(db, proposal_type="bid_down", target_id="nkw-manual", target_bid=850,
                  status="approved")
    assert p.approval_source is None  # 수동 콘솔 경로

    result = naver_sa_writer.WriteResult(
        action="update_keyword_bid", before={"bidAmt": 1000, "userLock": False},
        response=None, after={"bidAmt": 850, "userLock": False}, created_ids=[],
    )
    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid", return_value=result) as mock_write:
        log_entry = harness.execute(db, p.id, dry_run=False)

    mock_write.assert_called_once_with("nkw-manual", 850)
    assert log_entry.action == "update_bid"


def test_harness_executes_auto_op_proposal_when_kill_switch_on(db):
    """회귀 가드: 킬스위치 ON이면 auto_op* 제안도 정상 실행(가드가 과차단하지 않음)."""
    from app.services.naver_ad import naver_execution_harness as harness
    from app.services.naver_ad import naver_sa_writer

    _settings(db, auto_operate=True)
    p = _proposal(db, proposal_type="bid_down", target_id="nkw-on", target_bid=850,
                  status="approved")
    p.approval_source = auto_operator.APPROVAL_SOURCE_DAILY
    db.commit()

    result = naver_sa_writer.WriteResult(
        action="update_keyword_bid", before={"bidAmt": 1000, "userLock": False},
        response=None, after={"bidAmt": 850, "userLock": False}, created_ids=[],
    )
    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid", return_value=result) as mock_write:
        log_entry = harness.execute(db, p.id, dry_run=False)

    mock_write.assert_called_once()
    assert log_entry.action == "update_bid"


# ── codex 8R[P1]: 킬스위치 최종 확인 = writer 직전(진입 체크~PUT 사이 잔여 레이스 봉쇄) ──

def test_harness_kill_switch_flip_after_entry_check_blocks_writer(db):
    """execute() 진입 체크(7R)와 writer PUT 사이엔 라이브 재조회·가드레일 평가(수백 ms)가
    있다 — 그 동안의 OFF를 못 잡으면 잔여 레이스. 가드레일 컨텍스트 빌드의 부수효과로
    플래그를 끄면(진입 체크는 ON으로 통과한 뒤) writer 직전 최종 확인이 차단해야 한다:
    writer 미호출·change_log 0건·approved 유지(클레임 원복)."""
    from app.services.naver_ad import naver_execution_harness as harness

    _settings(db, auto_operate=True)  # 진입 체크 시점엔 ON
    p = _proposal(db, proposal_type="bid_down", target_id="nkw-race", target_bid=850,
                  status="approved")
    p.approval_source = auto_operator.APPROVAL_SOURCE_DAILY
    db.commit()

    def _ctx_build_and_kill(db_arg, proposal_arg, now_arg):
        # 진입 체크 통과 후·writer 전 구간(라이브 재조회 자리)에서 킬스위치 OFF를 재현
        db.query(NaverCampaignSettings).filter(
            NaverCampaignSettings.campaign_id == CAMPAIGN
        ).update({"auto_operate": False})
        db.commit()
        return {}

    with patch.object(harness, "_build_guardrail_context", side_effect=_ctx_build_and_kill), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        with pytest.raises(harness.KillSwitchEngagedError):
            harness.execute(db, p.id, dry_run=False)

    mock_write.assert_not_called()
    db.refresh(p)
    assert p.status == "approved"  # executing 클레임이 원복돼야 함(미실행 정직 상태)
    assert p.executed_change_log_id is None
    assert db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).count() == 0


# ══════════════════════════ VT3(D-NAO-82②) 소재 CTR 경보 브리핑(일 레인) ══════════════════════════

def _ctr_alert_rows(db, *, adgroup_id="grp-1", campaign_id=CAMPAIGN, imp=300, clk=0, rank=3.0):
    """ctr_alert의 W3 창(D0-2..D0, D0=NOW.date()-1)에 들어가는 naver_ad_daily 3일 시드 —
    누적 imp≥200·clk=0·avg_rank≤4.0(밴드 내) → 경보 발화. D-NAO-103 이후에는 기대클릭
    게이트(≥2)가 있어 트레일링 창(~D0-3]의 기준 CTR 시드가 함께 있어야 발화한다."""
    d0 = NOW.date() - timedelta(days=1)
    db.add(NaverAdDaily(  # 트레일링 기준 CTR 2%(imp 1000·clk 20) → 기대클릭 = 0.02 × 창 노출
        ad_date=d0 - timedelta(days=10), campaign_id=campaign_id, campaign_type="SHOPPING",
        adgroup_id=adgroup_id, keyword_id="", imp=1000, clk=20, cost=0, rank_sum=3000,
    ))
    for k in range(3):
        db.add(NaverAdDaily(
            ad_date=d0 - timedelta(days=k), campaign_id=campaign_id, campaign_type="SHOPPING",
            adgroup_id=adgroup_id, keyword_id="", imp=imp, clk=clk, cost=0,
            rank_sum=round(rank * imp),
        ))
    db.commit()


def test_daily_lane_ctr_alert_briefing_emitted_when_alert_present(db):
    """경보 있는 날 diary(observe·action=ctr_alert_briefing)+Slack 발화(PX·VT 브리핑 관례 미러)."""
    _settings(db)
    _ctr_alert_rows(db)
    with patch.object(auto_operator.slack_notifier, "notify_text",
                       return_value={"sent": True}) as mock_slack:
        result = auto_operator.run_daily_lane(db, now=NOW)

    assert result["ctr_alerts"] >= 1
    assert result["ctr_alerts_fired"] >= 1  # 첫 판정 = 신규 진입 → 즉시 발화
    briefs = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.action == auto_operator.ACTION_CTR_ALERT_BRIEFING
    ).all()
    assert len(briefs) == 1 and briefs[0].event_type == "observe"
    assert "CTR" in briefs[0].rationale
    mock_slack.assert_called_once()


def test_daily_lane_ctr_alert_briefing_dedupes_w1_w3(db):
    """한 그룹이 W1·W3 동시 발화(clk=0 3일)해도 브리핑은 그룹당 1건 — 건수도 그룹 수(1) 기준
    (2건 중복 집계 금지). D-NAO-103: 창 표기는 사람 말('최근 1일·3일')로 나간다."""
    _settings(db)
    _ctr_alert_rows(db)  # clk=0 3일 → 같은 그룹 W1(D0 단일일)·W3(누적) 동시 발화
    with patch.object(auto_operator.slack_notifier, "notify_text", return_value={"sent": True}):
        result = auto_operator.run_daily_lane(db, now=NOW)

    assert result["ctr_alerts"] == 1  # 그룹 수 기준(2건 아님)
    briefs = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.action == auto_operator.ACTION_CTR_ALERT_BRIEFING
    ).all()
    assert len(briefs) == 1
    rationale = briefs[0].rationale
    assert "새로 생긴 문제 1건" in rationale
    assert rationale.count("grp-1") == 1      # 그룹당 1행(중복 행 없음)
    assert "최근 1일·3일" in rationale         # 내부 창 코드(W1/W3) 노출 금지
    assert "W1" not in rationale and "W3" not in rationale
    assert "D-NAO" not in rationale           # 내부 결정 코드 노출 금지


def test_daily_lane_ctr_alert_repeat_is_suppressed_next_day(db):
    """D-NAO-103 핵심: 같은 그룹이 이튿날에도 판정되면 개별 발화하지 않는다(만성 = 주간 요약행).
    이튿날(화요일)엔 브리핑 자체가 없다 — 판정은 그대로 1건, 발화만 0."""
    _settings(db)
    _ctr_alert_rows(db)
    with patch.object(auto_operator.slack_notifier, "notify_text", return_value={"sent": True}):
        auto_operator.run_daily_lane(db, now=NOW)

    # 이튿날 같은 조건(창이 하루 밀리도록 D0+1 행 추가) — 화요일이라 주간 요약도 아님.
    next_now = NOW + timedelta(days=1)
    db.add(NaverAdDaily(
        ad_date=next_now.date() - timedelta(days=1), campaign_id=CAMPAIGN,
        campaign_type="SHOPPING", adgroup_id="grp-1", keyword_id="",
        imp=300, clk=0, cost=0, rank_sum=900,
    ))
    db.commit()
    with patch.object(auto_operator.slack_notifier, "notify_text",
                       return_value={"sent": True}) as mock_slack2:
        result2 = auto_operator.run_daily_lane(db, now=next_now)

    assert result2["ctr_alerts"] == 1              # 판정은 그대로(래더 skip 게이트는 계속 작동)
    assert result2["ctr_alerts_fired"] == 0        # 사람에게는 안 알림
    assert result2["ctr_alerts_suppressed"] == 1
    mock_slack2.assert_not_called()
    assert db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.action == auto_operator.ACTION_CTR_ALERT_BRIEFING
    ).count() == 1  # 첫날 1건 그대로(이튿날 추가 없음)


def test_daily_lane_ctr_alert_briefing_silent_when_no_alert(db):
    """경보 없는 날(추가 시드 0) → 완전 침묵(diary 0·Slack 0), 일 레인 본작업 결과는 불변."""
    _settings(db)
    with patch.object(auto_operator.slack_notifier, "notify_text") as mock_slack:
        result = auto_operator.run_daily_lane(db, now=NOW)

    assert result["ctr_alerts"] == 0
    assert db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.action == auto_operator.ACTION_CTR_ALERT_BRIEFING
    ).count() == 0
    mock_slack.assert_not_called()


def test_daily_lane_ctr_alert_briefing_failure_is_fail_open(db):
    """ctr_alert 산출 실패 → 브리핑만 fail-open(로그만) — 일 레인 본작업(승인/실행/stale
    정리) 결과에는 영향 없음(예외가 run_daily_lane 밖으로 전파되지 않는다)."""
    _settings(db)
    p = _proposal(db, proposal_type="bid_down", target_bid=900)
    with patch.object(auto_operator.ctr_alert, "detect_ctr_alerts",
                       side_effect=RuntimeError("판정 실패")), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)

    mock_exec.assert_called_once_with(db, p.id, dry_run=False, now=NOW)
    assert result["approved"] == 1  # 본작업 정상 진행(브리핑 실패에 오염되지 않음)
    assert db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.action == auto_operator.ACTION_CTR_ALERT_BRIEFING
    ).count() == 0


# ══════════════════════════ 시간당 레인(A2+A3) ══════════════════════════

def _hour(h, *, imp, clk, cost, avg_rank=None, conv_cnt=0):
    return {"hour": h, "imp": imp, "clk": clk, "cost": cost, "avg_rank": avg_rank, "conv_cnt": conv_cnt}


def test_hourly_lane_hot_set_only_clicks_ge_10_and_imp_today(db):
    _settings(db)
    window_from, window_to = _settlement_window()
    # qualifies: clk=10 in settlement window
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-hot", parent_id="grp-1", campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-hot", ad_date=window_from, clk=10, cost=100)
    # below threshold: clk=5
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-cold", parent_id="grp-1", campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-cold", ad_date=window_from, clk=5, cost=100)
    db.commit()

    calls = []

    def fetch(target_id, stat_date):
        calls.append(target_id)
        if target_id == "nkw-hot":
            return [_hour(9, imp=0, clk=0, cost=0)]  # 당일 imp=0 → held(당일 imp 없음)
        raise AssertionError("hot-set 밖 대상이 조회됨")

    result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=fetch)
    assert calls == ["nkw-hot"]  # nkw-cold는 클릭 미달로 애초에 조회조차 안 됨
    assert result["reviewed"] == 1
    assert result["held"][0]["target_id"] == "nkw-hot"
    assert "imp" in result["held"][0]["reason"]


def test_hourly_lane_low_imp_bucket_held(db):
    _settings(db)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-lowimp", parent_id="grp-1", campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-lowimp", ad_date=window_from, clk=10, cost=100)
    db.commit()

    curve = [_hour(6, imp=5, clk=1, cost=50, avg_rank=3.0), _hour(7, imp=10, clk=1, cost=50, avg_rank=3.0)]
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=NOW, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_not_called()
    assert "표본 부족" in result["held"][0]["reason"]


def test_hourly_lane_down_priority_when_rank_below_2_5(db):
    _settings(db)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-down", parent_id="grp-1", campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-down", ad_date=window_from, clk=10, cost=100)
    db.commit()

    curve = [
        _hour(6, imp=15, clk=2, cost=100, avg_rank=2.0),
        _hour(7, imp=15, clk=2, cost=100, avg_rank=2.0),
        _hour(8, imp=15, clk=2, cost=100, avg_rank=2.0),
    ]
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=NOW, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_called_once()
    assert result["approved"] == 1
    proposal_id = mock_exec.call_args[0][1]
    saved = db.get(NaverProposal, proposal_id)
    assert saved.proposal_type == "bid_down"
    assert saved.approval_source == "auto_op_hr"  # codex 2R[P1-1] String(12) 계약 준수 단축
    assert saved.rationale.startswith("[시간당밴드]")
    assert saved.target_bid == 850  # 1000×0.85 → 10원 올림 클램프


def test_hourly_lane_down_on_cpc_spike(db):
    _settings(db)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-cpc", parent_id="grp-1", campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    # baseline: cost=1000/clk=10 → CPC=100원, 급등 임계=200원(×2)
    _ad_row(db, keyword_id="nkw-cpc", ad_date=window_from, clk=10, cost=1000)
    db.commit()

    curve = [
        _hour(6, imp=15, clk=2, cost=500, avg_rank=3.0),
        _hour(7, imp=15, clk=2, cost=500, avg_rank=3.0),
        _hour(8, imp=10, clk=2, cost=500, avg_rank=3.0),
    ]  # today CPC = 1500/6 = 250원 > 200원
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=NOW, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_called_once()
    proposal_id = mock_exec.call_args[0][1]
    saved = db.get(NaverProposal, proposal_id)
    assert saved.proposal_type == "bid_down"
    assert "CPC급등" in saved.rationale


def test_hourly_lane_up_only_when_all_3_conditions_met(db):
    """★IU-R R2: WEB_SITE keyword UP은 estimate 직행(bid_up_rank)으로 절체 — 클램프(±15%) 아님.
    UP 판정(3조건)은 종전과 동일하고, 스텝은 목표순위(현재−1)의 estimate 필요입찰을 min(경제성
    상한, rank_bid)로 낸다. 정착창 conv=60000→경제성 상한≈1500 ≥ estimate 1200 → target=1200."""
    _settings(db, target_roas_override=Decimal("2.0"))
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-up", parent_id="grp-1", campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    # roas_naver = 60000/7000 ≈ 8.6 >= 2.0, baseline CPC = 7000/20 = 350원. rpc≈3000→경제성 상한≈1500.
    _ad_row(db, keyword_id="nkw-up", ad_date=window_from, clk=20, cost=7000, conv_direct_amt=60000)
    # R2 estimate 경로 예산 pace용 hour12 스냅샷(기본 hour23은 snapshot_hour<=now.hour서 배제).
    db.add(NaverHourlySnapshot(
        snapshot_at=datetime(2026, 7, 20, 12, 0, 0), ad_date=TODAY, snapshot_hour=12,
        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", cost=0, clk=0, imp=0, daily_budget=100000,
    ))
    db.commit()

    up_curve = [
        _hour(10, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(11, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(12, imp=15, clk=2, cost=30, avg_rank=5.0),
    ]  # imp=45, weighted_rank=5.0 → estimate 목표순위 clamp(ceil(5)−1,1,4)=4
    now_midday = datetime(2026, 7, 20, 12, 20, 0)  # 선형기대=740/1440≈0.514 vs 실제0.1 → 저속
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator, "estimate_average_position_bid",
                       return_value=[{"nccKeywordId": "nkw-up", "position": 4, "bid": 1200}]) as mock_est, \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "actual_revenue_ratio"}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_midday, fetch_intraday=lambda tid, d: up_curve,
        )
    mock_exec.assert_called_once()
    mock_est.assert_called_once_with("MOBILE", [{"key": "nkw-up", "position": 4}])  # 동적 목표순위 4
    assert result["rank_direct"] == 1
    proposal_id = mock_exec.call_args[0][1]
    saved = db.get(NaverProposal, proposal_id)
    assert saved.proposal_type == "bid_up_rank"
    assert saved.target_bid == 1200  # min(경제성 상한 1500, estimate 1200) = 1200
    assert saved.rationale.startswith("[순위직행]")


def test_hourly_lane_up_not_fired_when_roas_condition_missing(db):
    """rank>4.0·페이싱저속은 충족하지만 정착창 실적 자체가 없어 ROAS 검증 불가 → hold
    (3조건 동시 충족 요구 — 2개만 만족해도 up 아님). DL4: rank>4 ∧ UP 게이트 ROAS 불통과이므로
    hold 사유는 "재시작 대기"(스로틀 고착 관측)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-up2", parent_id="grp-1", campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    window_from, window_to = _settlement_window()
    # 핫셋 자격만 채우는 별도 클릭 없이는애초에 hot-set에 안 들어가므로, clk>=10인 행을
    # 넣되 cost=0으로 만들어(collected 0 cost) roas 검증만 실패하게 한다.
    _ad_row(db, keyword_id="nkw-up2", ad_date=window_from, clk=10, cost=0)
    db.commit()

    curve = [
        _hour(10, imp=15, clk=1, cost=5, avg_rank=5.0),
        _hour(11, imp=15, clk=1, cost=5, avg_rank=5.0),
        _hour(12, imp=15, clk=1, cost=5, avg_rank=5.0),
    ]
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_midday, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_not_called()
    assert "재시작 대기" in result["held"][0]["reason"]


def test_hourly_lane_default_hold_when_rank_in_neutral_band(db):
    _settings(db)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-neutral", parent_id="grp-1", campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-neutral", ad_date=window_from, clk=10, cost=1000)
    db.commit()

    curve = [
        _hour(6, imp=15, clk=1, cost=50, avg_rank=3.2),
        _hour(7, imp=15, clk=1, cost=50, avg_rank=3.2),
        _hour(8, imp=10, clk=1, cost=50, avg_rank=3.2),
    ]  # today CPC=150/3=50원 < baseline100×2 (CPC 급등 아님)
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=NOW, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_not_called()
    # D-NAO-66: 순위 중립대 개념 폐지 — DOWN(CPC/loss) 조건도 UP(tally/정착ROAS) 근거도 없어 hold.
    # 순위 전제가 사라졌으니 hold 사유는 ROAS 미달(재시작 대기).
    assert "재시작 대기(ROAS 미달)" in result["held"][0]["reason"]


def test_hourly_lane_spend_circuit_breaker_holds_entire_campaign(db):
    _settings(db, seed_snapshot=False)  # 브레이커 검증용 스냅샷을 아래서 직접 시드(now와 same-hour)
    window_from, window_to = _settlement_window()
    # 직전 7일 일평균 = 700/7 = 100원
    for i in range(7):
        _ad_row(db, keyword_id="", adgroup_id="grp-x", campaign_type="SHOPPING",
                ad_date=window_to - timedelta(days=i), clk=1, cost=100)
    db.add(NaverHourlySnapshot(
        snapshot_at=NOW, ad_date=TODAY, snapshot_hour=8, campaign_id=CAMPAIGN,
        campaign_type="SHOPPING", cost=500, clk=10, imp=100,  # 500 > 100×3
    ))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-never", parent_id="grp-1", campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-never", ad_date=window_from, clk=99, cost=100)
    db.commit()

    def fetch(tid, d):
        raise AssertionError("서킷브레이커가 걸리면 intraday 조회 자체가 없어야 함")

    result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=fetch)
    assert result["reviewed"] == 0
    assert len(result["held"]) == 1
    assert result["held"][0]["campaign_id"] == CAMPAIGN
    assert "서킷브레이커" in result["held"][0]["reason"]


def test_hourly_lane_intraday_fetch_failure_skips_group(db):
    _settings(db)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-fail", parent_id="grp-1", campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-fail", ad_date=window_from, clk=10, cost=100)
    db.commit()

    def fetch(tid, d):
        raise RuntimeError("HTTP 400")

    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=fetch)
    mock_exec.assert_not_called()
    assert result["skipped"] == 1
    assert result["held"] == []


def test_clamp_step_15pct_and_10won_rounding():
    assert auto_operator._clamp_step(1234, "up") == 1410  # 1419.1 → floor10
    assert auto_operator._clamp_step(1234, "down") == 1050  # 1048.9 → ceil10
    assert auto_operator._clamp_step(1000, "up") == 1150
    assert auto_operator._clamp_step(1000, "down") == 850
    # 절대하한(70원)에서는 내릴 여지가 없어 스텝 자체가 무의미 — None(스텝 소실, proposal_writer의
    # 스텝 소실 skip 관례와 동일 원칙: 억지로 같은 값을 반환하지 않는다).
    assert auto_operator._clamp_step(70, "down") is None


# ── codex 1R[P1-2]: 핫셋 grain 규약 — WEB_SITE=키워드만 / SHOPPING·BRAND_SEARCH=애드그룹만 ──

def test_hourly_lane_hot_set_excludes_adgroup_of_website_campaign(db):
    """WEB_SITE 캠페인의 adgroup 엔티티는 입찰 grain이 아니다(키워드 단위) — 클릭이 충분해도
    핫셋에서 제외돼야 한다. SHOPPING 캠페인의 keyword 엔티티도 대칭으로 제외."""
    _settings(db)
    window_from, window_to = _settlement_window()
    # WEB_SITE 캠페인의 adgroup 엔티티(grain 위반 — 제외 대상)
    db.add(NaverEntity(entity_type="adgroup", entity_id="grp-web", campaign_id=CAMPAIGN,
                        campaign_type="WEB_SITE", status="on"))
    _ad_row(db, adgroup_id="grp-web", keyword_id="nkw-x", campaign_type="WEB_SITE",
            ad_date=window_from, clk=50, cost=1000)
    # SHOPPING 캠페인의 keyword 엔티티(grain 위반 대칭 — 제외 대상)
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-shop", campaign_id=CAMPAIGN,
                        campaign_type="SHOPPING", status="on"))
    _ad_row(db, keyword_id="nkw-shop", campaign_type="WEB_SITE",
            ad_date=window_from + timedelta(days=1), clk=50, cost=1000)
    # 정상 grain 2건: WEB_SITE keyword + SHOPPING adgroup
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-ok", parent_id="grp-1", campaign_id=CAMPAIGN,
                        campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-ok", campaign_type="WEB_SITE",
            ad_date=window_from + timedelta(days=2), clk=50, cost=1000)
    db.add(NaverEntity(entity_type="adgroup", entity_id="grp-ok", parent_id=CAMPAIGN,
                        campaign_id=CAMPAIGN, campaign_type="SHOPPING", status="on"))
    _ad_row(db, adgroup_id="grp-ok", keyword_id="", campaign_type="SHOPPING",
            ad_date=window_from + timedelta(days=3), clk=50, cost=1000)
    db.commit()

    hot = auto_operator._hot_set_candidates(db, CAMPAIGN, window_from, window_to)
    assert ("adgroup", "grp-web") not in hot
    assert ("keyword", "nkw-shop") not in hot
    assert ("keyword", "nkw-ok") in hot
    assert ("adgroup", "grp-ok") in hot


# ── codex 2R[P1-2]: 당일 스냅샷 부재 → 서킷브레이커 평가 불가 = 캠페인 전체 hold(fail-closed) ──

def test_hourly_lane_snapshot_missing_holds_entire_campaign_fail_closed(db):
    """당일 NaverHourlySnapshot이 없으면 소진 서킷브레이커(×3)를 평가할 수 없다 — 평가
    불가 상태에서 실입찰을 진행하면 안 되므로(fail-closed) UP 조건을 다 채워도 실행 0."""
    _settings(db, target_roas_override=Decimal("2.0"), seed_snapshot=False)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-up", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-up", ad_date=window_from, clk=20, cost=7000, conv_direct_amt=21000)
    db.commit()

    up_curve = [
        _hour(10, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(11, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(12, imp=15, clk=2, cost=30, avg_rank=5.0),
    ]
    now_midday = datetime(2026, 7, 20, 13, 20, 0)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "actual_revenue_ratio"}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_midday, fetch_intraday=lambda tid, d: up_curve,
        )
    mock_exec.assert_not_called()
    assert result["reviewed"] == 0  # 핫셋 순회 자체가 없어야 함(캠페인 전체 hold)
    assert result["executed"] == 0
    assert len(result["held"]) == 1
    assert result["held"][0]["campaign_id"] == CAMPAIGN
    assert "스냅샷" in result["held"][0]["reason"]


def test_hourly_lane_stale_yesterday_snapshot_also_holds(db):
    """어제 스냅샷만 있고 당일 행이 없는 stale 상태도 부재와 동일 취급 — hold."""
    _settings(db, seed_snapshot=False)
    db.add(NaverHourlySnapshot(
        snapshot_at=NOW - timedelta(days=1), ad_date=TODAY - timedelta(days=1),
        snapshot_hour=23, campaign_id=CAMPAIGN, campaign_type="", cost=100, clk=1, imp=10,
    ))
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-any", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-any", ad_date=window_from, clk=10, cost=100)
    db.commit()

    result = auto_operator.run_hourly_lane(
        db, now=NOW, fetch_intraday=lambda tid, d: [_hour(6, imp=50, clk=5, cost=100, avg_rank=2.0)],
    )
    assert result["reviewed"] == 0
    assert "스냅샷" in result["held"][0]["reason"]


# ── codex 3R[P1-1]: 스냅샷 신선도 — 당일 행이어도 몇 시간 전 것이면 stale hold ──

def test_hourly_lane_stale_morning_snapshot_holds_campaign(db):
    """스냅샷 잡이 아침에 쓰고 죽으면 당일 행은 있지만 today_cost가 몇 시간 전 값 —
    그걸로 서킷브레이커를 평가하면 소진 폭주를 놓친다. 최신 snapshot_hour >= now.hour-1
    미달이면 캠페인 hold(fail-closed). 아침 6시 스냅샷만 있고 now=14시 → UP 조건을 다
    채워도 실행 0."""
    _settings(db, target_roas_override=Decimal("2.0"), seed_snapshot=False)
    db.add(NaverHourlySnapshot(
        snapshot_at=datetime(2026, 7, 20, 6, 5, 0), ad_date=TODAY, snapshot_hour=6,
        campaign_id=CAMPAIGN, campaign_type="", cost=100, clk=1, imp=10,
    ))
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-up", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-up", ad_date=window_from, clk=20, cost=7000, conv_direct_amt=21000)
    db.commit()

    up_curve = [
        _hour(11, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(12, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(13, imp=15, clk=2, cost=30, avg_rank=5.0),
    ]
    now_afternoon = datetime(2026, 7, 20, 14, 20, 0)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "actual_revenue_ratio"}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_afternoon, fetch_intraday=lambda tid, d: up_curve,
        )
    mock_exec.assert_not_called()
    assert result["reviewed"] == 0
    assert result["executed"] == 0
    assert len(result["held"]) == 1
    assert result["held"][0]["campaign_id"] == CAMPAIGN
    assert "stale" in result["held"][0]["reason"]


def test_hourly_lane_same_hour_snapshot_is_fresh(db):
    """정상 케이스: 스냅샷 :05·레인 :20이라 same-hour 스냅샷이 표준 — snapshot_hour ==
    now.hour면 신선(브레이커 평가 진행, 핫셋 순회까지 감)."""
    _settings(db, seed_snapshot=False)
    db.add(NaverHourlySnapshot(
        snapshot_at=NOW, ad_date=TODAY, snapshot_hour=8,  # now=8:50과 same-hour
        campaign_id=CAMPAIGN, campaign_type="", cost=0, clk=0, imp=0,
    ))
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-fresh", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-fresh", ad_date=window_from, clk=10, cost=100)
    db.commit()

    result = auto_operator.run_hourly_lane(
        db, now=NOW, fetch_intraday=lambda tid, d: [_hour(9, imp=0, clk=0, cost=0)],
    )
    assert result["reviewed"] == 1  # stale hold 없이 핫셋 순회 진행


# ── codex 3R[P1-2]: 판단 창 = 최근 3시계시간(now.hour-3 ≤ hour < now.hour)으로 제한 ──

def test_hourly_lane_ignores_old_buckets_outside_recent_3_hour_window(db):
    """hh24는 활동 있는 버킷만 반환한다 — "완료 버킷 마지막 3개"가 몇 시간 전 데이터일 수
    있다(이른 아침 활동 후 정오까지 조용한 곡선). 판단 창을 now.hour-3 ≤ hour < now.hour로
    제한하면 창 내 imp=0 → 표본 게이트가 자연 hold. 이른 버킷(rank 2.0 — 포함되면 down
    오판)이 있어도 now=14시엔 hold여야 한다."""
    _settings(db, seed_snapshot=False)
    db.add(NaverHourlySnapshot(
        snapshot_at=datetime(2026, 7, 20, 14, 5, 0), ad_date=TODAY, snapshot_hour=14,
        campaign_id=CAMPAIGN, campaign_type="", cost=0, clk=0, imp=0,
    ))
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-quiet", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-quiet", ad_date=window_from, clk=10, cost=1000)
    db.commit()

    early_curve = [
        _hour(6, imp=15, clk=2, cost=100, avg_rank=2.0),
        _hour(7, imp=15, clk=2, cost=100, avg_rank=2.0),
        _hour(8, imp=15, clk=2, cost=100, avg_rank=2.0),
    ]  # 이른 아침만 활동 — 11~13시대는 응답에 아예 없음(imp 0 취급)
    now_afternoon = datetime(2026, 7, 20, 14, 20, 0)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_afternoon, fetch_intraday=lambda tid, d: early_curve,
        )
    mock_exec.assert_not_called()
    assert "표본 부족" in result["held"][0]["reason"]


# ── codex 2R[P2]: 부모 체인(campaign→adgroup) 활성 확인 — 비활성 체인 아래 실입찰 차단 ──

def test_hourly_lane_excludes_keyword_with_paused_parent_adgroup(db):
    """entity_sync는 부모-자식 status를 캐스케이드하지 않는다 — 부모 adgroup이 off인데
    자식 키워드만 on인 경우 핫셋에서 제외돼야 한다(_on_adgroup_ids 규율과 동일)."""
    _settings(db)  # campaign on + grp-1 on 시드
    window_from, window_to = _settlement_window()
    # off인 부모 adgroup + 그 아래 on 키워드(클릭 충분) — 제외 대상
    db.add(NaverEntity(entity_type="adgroup", entity_id="grp-off", parent_id=CAMPAIGN,
                        campaign_id=CAMPAIGN, status="off"))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-orphan", parent_id="grp-off",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-orphan", adgroup_id="grp-off", ad_date=window_from, clk=50, cost=1000)
    # 대조군: 활성 체인(grp-1 on) 아래 키워드 — 포함
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-alive", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-alive", ad_date=window_from + timedelta(days=1), clk=50, cost=1000)
    db.commit()

    hot = auto_operator._hot_set_candidates(db, CAMPAIGN, window_from, window_to)
    assert ("keyword", "nkw-orphan") not in hot
    assert ("keyword", "nkw-alive") in hot


def test_hourly_lane_excludes_everything_when_campaign_entity_off(db):
    """캠페인 엔티티 자체가 off면(체인 최상위 비활성) 그 캠페인의 핫셋은 비어야 한다 —
    SHOPPING adgroup grain도 동일하게 차단."""
    _settings(db, seed_chain=False)
    db.add(NaverEntity(entity_type="campaign", entity_id=CAMPAIGN, campaign_id=CAMPAIGN,
                        status="off"))
    db.add(NaverEntity(entity_type="adgroup", entity_id="grp-shop", parent_id=CAMPAIGN,
                        campaign_id=CAMPAIGN, campaign_type="SHOPPING", status="on"))
    window_from, window_to = _settlement_window()
    _ad_row(db, adgroup_id="grp-shop", keyword_id="", campaign_type="SHOPPING",
            ad_date=window_from, clk=50, cost=1000)
    db.commit()

    hot = auto_operator._hot_set_candidates(db, CAMPAIGN, window_from, window_to)
    assert hot == []


# ── codex 10R[P1]: 페이싱 기대·실측 시간경계 정렬 — 이른 분(:20) 저속 오판 차단 ──

def test_hourly_lane_normal_pace_no_longer_blocks_up_d_nao_66(db):
    """D-NAO-66(IU1): 페이싱 저속 게이트 폐지 → 예산 여력 게이트로 대체. 종전엔 "정상 페이스"
    (실측 0.5 = 완료 경계 기대 0.5)면 저속 아님으로 UP이 막혔다(hold). 이제는 페이스와 무관하게
    정착창 ROAS≥target ∧ 일예산 잔여가 있으면 UP이 나간다("저속일 때만"이 아니라 "예산 남으면").
    종전 hold → 신 UP 차등."""
    _settings(db, target_roas_override=Decimal("2.0"))  # 기본 스냅샷: daily_budget 10만·cost 0(여력)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-pace", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    # 정착창: cost 7000·conv 60000(rpc≈3000→경제성 상한≈1500)·baseline CPC 350원
    _ad_row(db, keyword_id="nkw-pace", ad_date=window_from, clk=20, cost=7000, conv_direct_amt=60000)
    # R2 estimate 경로 예산 pace용 hour12 스냅샷.
    db.add(NaverHourlySnapshot(
        snapshot_at=datetime(2026, 7, 20, 12, 0, 0), ad_date=TODAY, snapshot_hour=12,
        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", cost=0, clk=0, imp=0, daily_budget=100000,
    ))
    db.commit()

    curve = [  # 완료 시간대(9~11) 합계 500 = 일평균의 절반(종전 '정상 페이스' = UP 불발이던 케이스)
        _hour(9, imp=15, clk=2, cost=167, avg_rank=5.0),
        _hour(10, imp=15, clk=2, cost=167, avg_rank=5.0),
        _hour(11, imp=15, clk=2, cost=166, avg_rank=5.0),
    ]  # CPC 500/6≈83<350×2(급등 아님) — 순위·페이싱 무관, ROAS+예산만이 UP을 결정
    now_minute20 = datetime(2026, 7, 20, 12, 20, 0)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator, "estimate_average_position_bid",
                       return_value=[{"nccKeywordId": "nkw-pace", "position": 4, "bid": 1200}]), \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "actual_revenue_ratio"}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_minute20, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_called_once()  # 예산 여력 있으면 정상 페이스여도 UP(D-NAO-66)
    assert result["rank_direct"] == 1
    saved = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert saved.proposal_type == "bid_up_rank"  # R2: 파워링크 estimate 직행
    assert "ROAS-UP" in saved.rationale and "예산 여력" in saved.rationale


def test_hourly_lane_up_held_when_daily_budget_exhausted(db):
    """D-NAO-66(IU1) 예산 여력 게이트 반대측: 정착창 ROAS≥target여도 일예산을 이미 소진했으면
    (cost_today ≥ daily_budget) UP은 hold — 예산 불가침(§0 불변 가드). PLAN §3-1④."""
    _settings(db, target_roas_override=Decimal("2.0"), seed_snapshot=False)
    # capped 예산 + 소진 완료 스냅샷(now=12:20과 same-hour라 신선). cost 2500 ≥ 예산 2000(소진)
    # 이지만 서킷브레이커(정착창 일평균 1000×3=3000)는 안 걸리게(2500<3000) 맞춤 — 예산 여력
    # 게이트만 단독 검증.
    db.add(NaverHourlySnapshot(
        snapshot_at=datetime(2026, 7, 20, 12, 5, 0), ad_date=TODAY, snapshot_hour=12,
        campaign_id=CAMPAIGN, campaign_type="", cost=2500, clk=100, imp=1000, daily_budget=2000,
    ))
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-exhaust", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-exhaust", ad_date=window_from, clk=20, cost=7000, conv_direct_amt=21000)
    db.commit()

    curve = [
        _hour(9, imp=15, clk=2, cost=17, avg_rank=5.0),
        _hour(10, imp=15, clk=2, cost=17, avg_rank=5.0),
        _hour(11, imp=15, clk=2, cost=16, avg_rank=5.0),
    ]  # 정착창 ROAS 3.0≥2.0(UP 후보)이나 예산 소진 → hold
    now_minute20 = datetime(2026, 7, 20, 12, 20, 0)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "actual_revenue_ratio"}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_minute20, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_not_called()
    assert result["approved"] == 0
    assert any("예산 여력 없음" in h["reason"] and "소진" in h["reason"] for h in result["held"])


# ── codex 1R[P2]: 진행 중(부분) 시간대 제외 — 완료 시간대(hour < now.hour)만 판정에 사용 ──

def test_hourly_lane_excludes_in_progress_hour_bucket(db):
    """:20 실행 시 hh24 응답에 현재 시간대(20분치 부분 데이터)가 섞여 온다 — 그대로 마지막
    3개를 취하면 부분 데이터로 오판한다. 현재 시간대 버킷(rank=1.0, 포함 시 확실히 down
    판정)이 있어도 제외되고, 직전 3개 완료 시간대(중립 rank=3.2)로 hold가 나와야 한다."""
    _settings(db)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-partial", parent_id="grp-1", campaign_id=CAMPAIGN,
                        campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-partial", ad_date=window_from, clk=10, cost=1000)
    db.commit()

    now = datetime(2026, 7, 20, 12, 20, 0)  # 12시대 진행 중(20분 경과)
    curve = [
        _hour(9, imp=15, clk=1, cost=50, avg_rank=3.2),
        _hour(10, imp=15, clk=1, cost=50, avg_rank=3.2),
        _hour(11, imp=15, clk=1, cost=50, avg_rank=3.2),
        _hour(12, imp=100, clk=1, cost=50, avg_rank=1.0),  # 진행 중 부분 버킷 — 포함돼도(과열밴드 폐지) DOWN 아님
    ]
    with patch.object(auto_operator.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_not_called()  # 완료 3개(9~11)만 판정 — CPC/loss/tally 근거 없어 hold
    # D-NAO-66: 진행 중 부분 버킷 제외 로직은 그대로 검증(완료 버킷만 판정). hold 사유는 ROAS 미달.
    assert "재시작 대기(ROAS 미달)" in result["held"][0]["reason"]


def test_hourly_lane_completed_buckets_only_for_imp_gate(db):
    """imp 표본 게이트(≥30)도 완료 시간대만 센다 — 진행 중 버킷의 imp가 표본을 부풀려
    성급한 판정을 만들면 안 된다."""
    _settings(db)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-pad", parent_id="grp-1", campaign_id=CAMPAIGN,
                        campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-pad", ad_date=window_from, clk=10, cost=1000)
    db.commit()

    now = datetime(2026, 7, 20, 12, 20, 0)
    curve = [
        _hour(10, imp=5, clk=1, cost=50, avg_rank=2.0),
        _hour(11, imp=5, clk=1, cost=50, avg_rank=2.0),
        _hour(12, imp=100, clk=1, cost=50, avg_rank=2.0),  # 진행 중 — 제외하면 imp=10 < 30
    ]
    with patch.object(auto_operator.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_not_called()
    assert "표본 부족" in result["held"][0]["reason"]


# ── 통합 케이스: 쿨다운/가드레일 차단이 실행을 실제로 막는지(harness.execute 실호출) ──

def test_hourly_lane_execution_blocked_by_real_guardrail_cooldown(db):
    _settings(db)
    window_from, window_to = _settlement_window()
    target_id = "nkw-cd"
    db.add(NaverEntity(entity_type="keyword", entity_id=target_id, parent_id="grp-1", campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id=target_id, ad_date=window_from, clk=10, cost=100)
    # 1시간 전 우리 시스템이 이미 이 키워드를 변경 — 쿨다운 5시간 이내(guardrail_gate)
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id=target_id, campaign_id=CAMPAIGN,
        action="update_bid", dry_run=False,
        after_value=json.dumps({"bidAmt": 1000, "userLock": False}),
        changed_at=NOW - timedelta(hours=1),
    ))
    db.commit()

    curve = [
        _hour(6, imp=15, clk=2, cost=100, avg_rank=2.0),
        _hour(7, imp=15, clk=2, cost=100, avg_rank=2.0),
        _hour(8, imp=15, clk=2, cost=100, avg_rank=2.0),
    ]  # rank<2.5 → down 판정
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}):
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda tid, d: curve)

    assert result["approved"] == 1  # auto_operator는 승인까지는 함(harness가 최종 차단)
    assert result["executed"] == 0
    assert result["failed"] == 1

    proposals = db.query(NaverProposal).filter(NaverProposal.target_id == target_id).all()
    assert len(proposals) == 1
    assert proposals[0].status == "failed"  # guardrail_gate 차단 → harness._guard_failure

    logs = db.query(NaverChangeLog).filter(
        NaverChangeLog.entity_id == target_id, NaverChangeLog.action == "update_bid",
    ).order_by(NaverChangeLog.id.desc()).all()
    blocked = [l for l in logs if l.outcome == "failed"]
    assert len(blocked) == 1
    assert "가드레일 차단" in blocked[0].rationale
    assert "쿨다운" in blocked[0].rationale


# ══════════════════════════ CD2 클릭 탐침 루프 (D-NAO-58) ══════════════════════════
# _probe_trigger 순수 단위 + run_hourly_lane 탐침 분기 통합. now.hour=8이면 완료 2시간 창은
# [6, 8) = 시간대 6·7만 집계.


def test_probe_trigger_fires_when_zero_clicks_high_imp_rank_in_band():
    """clk=0 ∧ imp≥30 ∧ (창 내)rank≥2.5(밴드 안/하단) → 탐침 발동(True)."""
    now = datetime(2026, 7, 20, 8, 20, 0)  # 완료 창 [6, 8)
    curve = [_hour(6, imp=20, clk=0, cost=200, avg_rank=3.0),
             _hour(7, imp=20, clk=0, cost=200, avg_rank=3.0)]
    fired, reason = auto_operator._probe_trigger(curve, now)
    assert fired is True
    assert "imp=40" in reason  # imp_sum 명시
    assert "3.0" in reason  # rank 명시(창 내 가중)


def test_probe_trigger_no_fire_when_imp_below_min():
    """imp<30(노출 부족 무클릭 — 순위 병리가 아님) → 미발동."""
    now = datetime(2026, 7, 20, 8, 20, 0)
    curve = [_hour(6, imp=10, clk=0, cost=100, avg_rank=3.0),
             _hour(7, imp=10, clk=0, cost=100, avg_rank=3.0)]
    fired, _ = auto_operator._probe_trigger(curve, now)
    assert fired is False


def test_probe_trigger_no_fire_when_clicks_present():
    """clk>0(이미 클릭 살아있음) → 미발동."""
    now = datetime(2026, 7, 20, 8, 20, 0)
    curve = [_hour(6, imp=20, clk=1, cost=200, avg_rank=3.0),
             _hour(7, imp=20, clk=0, cost=200, avg_rank=3.0)]
    fired, _ = auto_operator._probe_trigger(curve, now)
    assert fired is False


def test_probe_trigger_no_fire_when_window_rank_above_band():
    """창 내 가중 rank<2.5(밴드 상단/과열 — 위치가 아닌 수요 문제) → 미발동."""
    now = datetime(2026, 7, 20, 8, 20, 0)
    curve = [_hour(6, imp=20, clk=0, cost=200, avg_rank=2.0),
             _hour(7, imp=20, clk=0, cost=200, avg_rank=2.0)]
    fired, _ = auto_operator._probe_trigger(curve, now)
    assert fired is False


def test_probe_trigger_no_fire_when_window_rank_all_none():
    """창 내 rank 근거 전부 None → fail-closed(미발동)."""
    now = datetime(2026, 7, 20, 8, 20, 0)
    curve = [_hour(6, imp=20, clk=0, cost=200, avg_rank=None),
             _hour(7, imp=20, clk=0, cost=200, avg_rank=None)]
    fired, reason = auto_operator._probe_trigger(curve, now)
    assert fired is False
    assert "근거 없음" in reason


def test_probe_trigger_no_fire_before_2am_boundary():
    """now.hour<2 — 완료 2시간 창이 비는 경계 → 표본 없음(미발동)."""
    now = datetime(2026, 7, 20, 1, 20, 0)
    curve = [_hour(0, imp=100, clk=0, cost=500, avg_rank=3.0)]
    fired, _ = auto_operator._probe_trigger(curve, now)
    assert fired is False


def test_probe_trigger_window_excludes_current_and_old_buckets():
    """창 정확성: [now.hour-2, now.hour)만 집계 — 현재 시간대(8, 진행중)와 2시간 초과(5)는 제외."""
    now = datetime(2026, 7, 20, 8, 20, 0)  # 창 [6, 8)
    curve = [
        _hour(5, imp=100, clk=5, cost=500, avg_rank=3.0),  # 2시간 초과 — 제외
        _hour(6, imp=20, clk=0, cost=200, avg_rank=3.0),   # 창 내
        _hour(7, imp=20, clk=0, cost=200, avg_rank=3.0),   # 창 내
        _hour(8, imp=100, clk=5, cost=500, avg_rank=3.0),  # 현재(진행중) — 제외
    ]
    fired, reason = auto_operator._probe_trigger(curve, now)
    assert fired is True  # 창 밖 clk(5+5)를 셌다면 clk_sum>0로 미발동했을 것
    assert "imp=40" in reason  # 창 밖 imp(100+100)를 셌다면 240이었을 것


def test_probe_trigger_rank_from_window_only_not_3h_window():
    """★R1 P3-1 회귀 고정: rank는 클릭 2시간 창에서만 산출한다. 3h 전(클릭창 밖) 저순위·
    고노출 버킷(hour5 rank5.0 imp100)이 최근 2h(rank2.0·클릭0)를 밴드 사각지대로 오판하지
    않는다 — 초판(3h 가중 rank≈4.14≥2.5)은 탐침을 쐈으나 이제 2h창 rank=2.0<2.5로 미발동."""
    now = datetime(2026, 7, 20, 8, 20, 0)  # 창 [6, 8)
    curve = [
        _hour(5, imp=100, clk=0, cost=500, avg_rank=5.0),  # 클릭창 밖 — rank에 섞이면 안 됨
        _hour(6, imp=20, clk=0, cost=200, avg_rank=2.0),
        _hour(7, imp=20, clk=0, cost=200, avg_rank=2.0),
    ]
    fired, reason = auto_operator._probe_trigger(curve, now)
    assert fired is False
    assert "2.00" in reason  # 창 내 가중 rank=2.0(hour5 5.0을 섞었다면 4.14였을 것)


def _probe_curve():
    """밴드 판정이 hold(rank 2.5~4 중립)이면서 탐침 트리거가 참인 곡선(midday, 창 [10,12))."""
    return [_hour(10, imp=20, clk=0, cost=300, avg_rank=3.0),
            _hour(11, imp=20, clk=0, cost=300, avg_rank=3.0)]


def test_hourly_lane_probe_fires_on_hold_verdict_and_tags_probe(db):
    """밴드 판정 hold(중립 rank 3.0·클릭0)인데 탐침 트리거 참 → up 제안 생성, approval_source=
    'probe_op', harness.execute 호출, diary actor 경로=probe."""
    _settings(db)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-probe", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-probe", ad_date=window_from, clk=10, cost=1000)
    db.commit()

    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_midday, fetch_intraday=lambda tid, d: _probe_curve(),
        )

    mock_exec.assert_called_once()
    assert result["approved"] == 1
    assert result["probed"] == 1
    proposal_id = mock_exec.call_args[0][1]
    saved = db.get(NaverProposal, proposal_id)
    assert saved.proposal_type == "bid_up"
    assert saved.approval_source == auto_operator.APPROVAL_SOURCE_PROBE == "probe_op"
    assert saved.rationale.startswith("[클릭탐침]")
    assert saved.target_bid == 1150  # 1000×1.15 → 10원 내림(탐침 방향은 항상 up)
    # 실행되면 harness가 이 approval_source로 diary actor=probe를 남긴다(완료 기준 경로)
    assert diary.actor_from_approval_source(saved.approval_source) == diary.ACTOR_PROBE


def test_hourly_lane_probe_not_fired_when_verdict_is_action(db):
    """밴드 판정이 action(여기선 loss 고삐 DOWN)이면 탐침 미발동(이중 발동 금지) — 정상 처리.
    ★D-NAO-66: 과열밴드 DOWN이 폐지돼 이 '액션' 트리거를 loss 고삐로 교체. 탐침 조건(clk=0·
    imp≥30·창내 rank≥2.5)을 모두 충족하는 곡선이지만, loss 고삐가 먼저 DOWN을 내므로 verdict가
    hold가 아니라 탐침 분기에 진입조차 하지 않음을 검증한다."""
    _settings(db)
    _seed_product_bep(db, adgroup_id="grp-1", bep_roas=Decimal("2.5"))  # 원가 있어 고삐 평가 가능
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-dn", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-dn", ad_date=window_from, clk=10, cost=100)  # 정착창 일평균 cost 100/7≈14
    db.commit()

    # clk=0·imp≥30·창내 rank 3.0≥2.5 = 탐침 조건 충족. 그러나 conv_cnt=0 → est ROAS 0<BEP 2.5,
    # 당일소진 600≥하루평균14 → loss 고삐 DOWN이 먼저 발동(탐침 분기 미진입). CPC는 clk=0이라 None(급등 아님).
    curve = [_hour(9, imp=20, clk=0, cost=200, avg_rank=3.0, conv_cnt=0),
             _hour(10, imp=20, clk=0, cost=200, avg_rank=3.0, conv_cnt=0),
             _hour(11, imp=20, clk=0, cost=200, avg_rank=3.0, conv_cnt=0)]
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_midday, fetch_intraday=lambda tid, d: curve,
        )

    mock_exec.assert_called_once()
    assert result["probed"] == 0
    saved = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert saved.proposal_type == "bid_down"
    assert saved.rationale.startswith("[순위고삐]")  # loss 고삐 DOWN(탐침 아님)
    assert saved.approval_source == auto_operator.APPROVAL_SOURCE_HOURLY


def test_hourly_lane_probe_not_fired_when_kill_switch_off(db):
    """auto_operate=False → 캠페인 자체가 레인 대상 밖 → 탐침 미발동·미집행."""
    _settings(db, auto_operate=False)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-off", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-off", ad_date=window_from, clk=10, cost=1000)
    db.commit()

    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_midday, fetch_intraday=lambda tid, d: _probe_curve(),
        )
    mock_exec.assert_not_called()
    assert result["reviewed"] == 0
    assert result["probed"] == 0


def test_actor_from_approval_source_probe():
    """probe_op → probe actor 매핑(CD3/diary가 탐침 실행을 식별하는 경로)."""
    assert diary.actor_from_approval_source("probe_op") == diary.ACTOR_PROBE == "probe"


def test_harness_refuses_probe_proposal_when_kill_switch_off(db):
    """탐침 우회 경로 금지: 탐침(approval_source=probe_op) 제안도 auto_op*과 동일하게 harness
    쓰기 직전 킬스위치 최종 가드를 받는다 — OFF면 실입찰 거부(writer 미호출)."""
    from app.services.naver_ad import naver_execution_harness as harness

    _settings(db, auto_operate=False)
    p = _proposal(db, proposal_type="bid_up", target_id="nkw-pks", target_bid=1150,
                  status="approved")
    p.approval_source = auto_operator.APPROVAL_SOURCE_PROBE
    db.commit()

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        with pytest.raises(harness.KillSwitchEngagedError):
            harness.execute(db, p.id, dry_run=False)

    mock_write.assert_not_called()
    db.refresh(p)
    assert p.status == "approved"  # 미실행 정직 상태
    assert db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).count() == 0


def test_hourly_lane_probe_blocked_by_real_guardrail_bep(db):
    """★R1 P3-2: 탐침이 밴드 사각지대에서 발동해도 실제 guardrail_gate.check(mock 아님)의
    BEP 하한(D-NAO-1)에 걸린다 — 정착창 보정ROAS(1.0) < 목표(2.0)면 증액 금지. writer 미호출,
    change_log는 차단 기록(failed), 결과 failed. 탐침 우회 없음의 실행 증명.

    guardrail context precompute(_build_guardrail_context = DB 재조회)만 스텁하고(기존
    test_execute_update_bid_adgroup_bid_up_blocked_by_real_guardrail_bep의 확립된 관례 —
    check 자체는 실제 실행), 나머지(guardrail_gate.check·harness 실행 경로)는 전부 실동작."""
    from app.services.naver_ad import naver_execution_harness as harness

    _settings(db)  # auto_operate=True·optimizer='ours'
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-probe-bep", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-probe-bep", ad_date=window_from, clk=10, cost=1000)
    db.commit()

    # 탐침 target_bid=1150(현재 1000×1.15). 실 guardrail가 BEP 미달로 차단하도록 컨텍스트 주입:
    # 방향/변경폭은 통과(1150>1000, 15%)시키고 보정ROAS 1.0<목표 2.0으로 BEP만 발동.
    bep_fail_ctx = {
        "current_bid": 1000, "roas_corrected": 1.0, "target_roas": 2.0,  # BEP 미달
        "unconverted_spend": 0, "cost_today": 0, "daily_budget": 50_000,
        "last_change_at": None, "changes_today_count": 0,
    }
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(harness, "_build_guardrail_context", return_value=bep_fail_ctx), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        result = auto_operator.run_hourly_lane(
            db, now=now_midday, fetch_intraday=lambda tid, d: _probe_curve(),
        )

    # 탐침은 승인·발동됐으나 실 guardrail가 실행을 차단 → executed 0, failed 1.
    assert result["approved"] == 1
    assert result["probed"] == 1
    assert result["executed"] == 0
    assert result["failed"] == 1
    mock_write.assert_not_called()  # BEP 차단으로 writer 미호출(실입찰 안 나감)

    proposals = db.query(NaverProposal).filter(NaverProposal.target_id == "nkw-probe-bep").all()
    assert len(proposals) == 1
    assert proposals[0].approval_source == auto_operator.APPROVAL_SOURCE_PROBE
    assert proposals[0].status == "failed"  # 실 guardrail 차단 → harness._guard_failure

    blocked = db.query(NaverChangeLog).filter(
        NaverChangeLog.entity_id == "nkw-probe-bep", NaverChangeLog.outcome == "failed",
    ).all()
    assert len(blocked) == 1
    assert "BEP 미달" in blocked[0].rationale  # 실 guardrail_gate.check가 낸 실제 사유


# ══════════════════════════ RL5(CD5) 과climb 방지 게이트 (D-NAO-60) ══════════════════════════
# _learned_optimal_skip: 탐침(_probe_trigger) 발동 직후 게이트 — env_cell의 학습된 최적 순위
# 밴드(probe_learning_loop.learned_probe_rank)에 이미 도달했으면 up 승격을 생략한다(이익
# 스팟밴드 2.5~4를 넘어 비싼 상위로 과climb 방지, D-NAO-59). 창은 _probe_trigger와 동일
# [now.hour-2, now.hour). now=2026-07-20(Monday)→env_cell='weekday'.

def _seed_learned_band(db, *, avg_rank, campaign_id=CAMPAIGN, base_date=date(2026, 7, 13)):
    """optimal_band가 avg_rank가 속한 밴드로 확정되도록 weekday env_cell에 3일치를 심는다
    (imp≥100 총합·days≥3·ctr_shrunk≥신호하한, conv_cnt=0=CTR 폴백 — basis는 이 게이트
    테스트의 관심사가 아니다, Part B가 별도로 검증)."""
    for i in range(3):
        db.add(NaverKeywordHourly(
            ad_date=base_date + timedelta(days=i), hour=9, entity_type="keyword",
            entity_id="nkw-learn", adgroup_id="grp-1", campaign_id=campaign_id,
            campaign_type="WEB_SITE", imp=50, clk=10, cost=500, avg_rank=avg_rank,
        ))
    db.commit()


def test_learned_optimal_skip_false_when_no_learned_band(db):
    """학습된 최적 밴드가 없으면(데이터 없음) 게이트가 막지 않는다 — CD2 폴백(무조건 탐침)."""
    now = datetime(2026, 7, 20, 8, 20, 0)
    curve = [_hour(6, imp=20, clk=0, cost=200, avg_rank=3.0),
             _hour(7, imp=20, clk=0, cost=200, avg_rank=3.0)]
    skip, reason = auto_operator._learned_optimal_skip(db, curve, now, CAMPAIGN)
    assert skip is False
    assert "학습된 최적 밴드 없음" in reason


def test_learned_optimal_skip_true_when_already_in_learned_band(db):
    """현재 창 가중 rank가 학습 최적밴드 상한보다 낮으면(이미 그 밴드 안/더 상위) 탐침 생략."""
    _seed_learned_band(db, avg_rank=Decimal("2.2"))  # 학습 최적밴드 = 2.0-2.5(상한 2.5)
    now = datetime(2026, 7, 20, 8, 20, 0)  # 창 [6,8)
    curve = [_hour(6, imp=20, clk=0, cost=200, avg_rank=2.2),
             _hour(7, imp=20, clk=0, cost=200, avg_rank=2.2)]  # 현재 rank 2.2<2.5 → 이미 도달
    skip, reason = auto_operator._learned_optimal_skip(db, curve, now, CAMPAIGN)
    assert skip is True
    assert "이미 도달" in reason
    assert "탐침 생략" in reason


def test_learned_optimal_skip_false_when_below_learned_band(db):
    """현재 창 가중 rank가 학습 최적밴드 상한 이상이면(아직 하위) 탐침 상향을 막지 않는다."""
    _seed_learned_band(db, avg_rank=Decimal("2.2"))  # 학습 최적밴드 = 2.0-2.5(상한 2.5)
    now = datetime(2026, 7, 20, 8, 20, 0)
    curve = [_hour(6, imp=20, clk=0, cost=200, avg_rank=3.0),
             _hour(7, imp=20, clk=0, cost=200, avg_rank=3.0)]  # 현재 rank 3.0≥2.5 → 아직 하위
    skip, reason = auto_operator._learned_optimal_skip(db, curve, now, CAMPAIGN)
    assert skip is False
    assert "하위" in reason


def test_learned_optimal_skip_always_true_for_open_ended_band(db):
    """학습 최적밴드가 '4.0+'(상한 없음)면 현재 rank가 이미 훨씬 좋아도(rank 2.0) 더 올릴
    이유가 없어 항상 skip=True."""
    _seed_learned_band(db, avg_rank=Decimal("4.5"))  # 학습 최적밴드 = 4.0+(상한 없음)
    now = datetime(2026, 7, 20, 8, 20, 0)
    curve = [_hour(6, imp=20, clk=0, cost=200, avg_rank=2.0),
             _hour(7, imp=20, clk=0, cost=200, avg_rank=2.0)]
    skip, reason = auto_operator._learned_optimal_skip(db, curve, now, CAMPAIGN)
    assert skip is True


def test_learned_optimal_skip_false_when_no_rank_evidence(db):
    """창 내 avg_rank가 전부 None(근거 없음) → 게이트 미적용(fail-open, 안전한 쪽=차단 안 함)."""
    now = datetime(2026, 7, 20, 8, 20, 0)
    curve = [_hour(6, imp=20, clk=0, cost=200, avg_rank=None),
             _hour(7, imp=20, clk=0, cost=200, avg_rank=None)]
    skip, reason = auto_operator._learned_optimal_skip(db, curve, now, CAMPAIGN)
    assert skip is False
    assert "순위 근거 없음" in reason


def test_hourly_lane_probe_skipped_when_learned_optimal_already_reached(db):
    """run_hourly_lane 통합: 학습된 최적 밴드가 이미(또는 그 이상) 도달됐으면 탐침이 up으로
    승격되지 않고 hold 유지 — 과climb 방지(D-NAO-59). proposal 자체가 생성되지 않는다."""
    _settings(db)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-probe-skip", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-probe-skip", ad_date=window_from, clk=10, cost=1000)
    _seed_learned_band(db, avg_rank=Decimal("4.5"))  # 학습 최적밴드 = 4.0+(항상 skip)

    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_midday, fetch_intraday=lambda tid, d: _probe_curve(),
        )

    mock_exec.assert_not_called()
    assert result["probed"] == 0
    assert result["approved"] == 0
    held_reasons = [h["reason"] for h in result["held"]]
    assert any("탐침 생략" in r for r in held_reasons)
    assert db.query(NaverProposal).filter(NaverProposal.target_id == "nkw-probe-skip").count() == 0


def test_hourly_lane_probe_still_fires_when_below_learned_band(db):
    """학습된 최적 밴드보다 현재 rank가 하위(아직 목표 미달)면 탐침은 그대로 up으로 승격되고
    실행된다 — 게이트가 무조건 차단하는 게 아님을 증명."""
    _settings(db)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-probe-go", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-probe-go", ad_date=window_from, clk=10, cost=1000)
    _seed_learned_band(db, avg_rank=Decimal("2.2"))  # 학습 최적밴드 = 2.0-2.5(상한 2.5)

    # _probe_curve() 창 가중 rank=3.0 ≥ 2.5(학습 밴드 상한) → 아직 학습 목표 미달, 탐침 진행
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_midday, fetch_intraday=lambda tid, d: _probe_curve(),
        )

    mock_exec.assert_called_once()
    assert result["probed"] == 1
    saved = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert saved.rationale.startswith("[클릭탐침]")
    assert "CD5 목표" in saved.rationale


# ══════════════════════════ RL3 순위 고삐(D-NAO-60, 장중 loss DOWN) ══════════════════════════

def _seed_product_bep(db, *, adgroup_id="grp-1", mall_product_id="p-leash", campaign_id=CAMPAIGN,
                       price=Decimal("1000"), margin=Decimal("400"), bep_roas=Decimal("2.5")):
    """단일 상품 매핑(주문 없음 → 단순평균 경로, campaign_target_resolver 관례)."""
    db.add(NaverAdgroupProduct(adgroup_id=adgroup_id, campaign_id=campaign_id, mall_product_id=mall_product_id))
    db.add(NaverProductBep(
        channel_id=6, channel_product_id=mall_product_id, has_cost=True,
        selling_price=price, contribution_margin=margin, bep_roas=bep_roas,
    ))
    db.commit()


def _leash_keyword(db, *, target_id="nkw-leash", parent_id="grp-1", campaign_id=CAMPAIGN):
    db.add(NaverEntity(entity_type="keyword", entity_id=target_id, parent_id=parent_id,
                        campaign_id=campaign_id, campaign_type="WEB_SITE", status="on"))
    db.commit()


def test_intraday_loss_leash_fires_when_underwater_and_spend_met(db):
    """(a) 추정ROAS<BEP ∧ 당일소진≥하루평균 → 발동(True)."""
    _seed_product_bep(db, bep_roas=Decimal("2.5"))
    _leash_keyword(db)
    curve = [_hour(6, imp=20, clk=2, cost=2000, avg_rank=3.0, conv_cnt=1)]  # revenue=1000·cost=2000→roas=0.5<2.5
    baseline_agg = {"clk": 10, "cost": 7000, "conv_amt": 0}  # avg_daily=1000, today_cost=2000≥1000
    fired, reason = auto_operator._intraday_loss_leash(
        db, target_type="keyword", target_id="nkw-leash", campaign_id=CAMPAIGN,
        curve=curve, now=NOW, baseline_agg=baseline_agg,
    )
    assert fired is True
    assert "순위고삐" in reason


def test_intraday_loss_leash_not_fired_when_roas_above_bep(db):
    """(b) 추정ROAS≥BEP → 미발동."""
    _seed_product_bep(db, bep_roas=Decimal("2.5"))
    _leash_keyword(db)
    curve = [_hour(6, imp=20, clk=2, cost=2000, avg_rank=3.0, conv_cnt=6)]  # revenue=6000/cost=2000=3.0≥2.5
    baseline_agg = {"clk": 10, "cost": 7000, "conv_amt": 0}
    fired, reason = auto_operator._intraday_loss_leash(
        db, target_type="keyword", target_id="nkw-leash", campaign_id=CAMPAIGN,
        curve=curve, now=NOW, baseline_agg=baseline_agg,
    )
    assert fired is False
    assert "고삐 불발" in reason


def test_intraday_loss_leash_deferred_when_spend_below_daily_average(db):
    """(c) 당일소진<하루평균 → 유보(과소추정 방어) — ROAS가 이미 BEP 미달이어도 미발동."""
    _seed_product_bep(db, bep_roas=Decimal("2.5"))
    _leash_keyword(db)
    curve = [_hour(6, imp=20, clk=2, cost=500, avg_rank=3.0, conv_cnt=0)]  # roas=0<2.5(underwater)
    baseline_agg = {"clk": 10, "cost": 7000, "conv_amt": 0}  # avg_daily=1000, today_cost=500<1000
    fired, reason = auto_operator._intraday_loss_leash(
        db, target_type="keyword", target_id="nkw-leash", campaign_id=CAMPAIGN,
        curve=curve, now=NOW, baseline_agg=baseline_agg,
    )
    assert fired is False
    assert "판정 유보" in reason


def test_intraday_loss_leash_not_fired_when_price_bep_unavailable(db):
    """(d) 상품 매핑/원가 미확인 → 미발동(price·bep_roas None)."""
    _leash_keyword(db)  # 매핑 없음(NaverAdgroupProduct/NaverProductBep 미시드)
    curve = [_hour(6, imp=20, clk=2, cost=2000, avg_rank=3.0, conv_cnt=0)]
    baseline_agg = {"clk": 10, "cost": 7000, "conv_amt": 0}
    fired, reason = auto_operator._intraday_loss_leash(
        db, target_type="keyword", target_id="nkw-leash", campaign_id=CAMPAIGN,
        curve=curve, now=NOW, baseline_agg=baseline_agg,
    )
    assert fired is False
    assert "단가/BEP 미확인" in reason


def test_intraday_loss_leash_not_fired_when_no_spend_today(db):
    """(e) 당일 cost=0 → estimated_intraday_roas가 None → 미발동."""
    _seed_product_bep(db, bep_roas=Decimal("2.5"))
    _leash_keyword(db)
    curve = [_hour(6, imp=0, clk=0, cost=0, avg_rank=None, conv_cnt=0)]
    baseline_agg = {"clk": 10, "cost": 7000, "conv_amt": 0}
    fired, reason = auto_operator._intraday_loss_leash(
        db, target_type="keyword", target_id="nkw-leash", campaign_id=CAMPAIGN,
        curve=curve, now=NOW, baseline_agg=baseline_agg,
    )
    assert fired is False
    assert "당일 소진 없음" in reason


def test_intraday_loss_leash_resolves_keyword_parent_adgroup(db):
    """(f) keyword target_type이 NaverEntity.parent_id로 부모 광고그룹을 정확히 해석한다
    (adgroup 매핑은 부모 id에만 시드했는데 leash가 정상 발동 = 해석 성공의 증거)."""
    _seed_product_bep(db, adgroup_id="grp-parent", bep_roas=Decimal("2.5"))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-child", parent_id="grp-parent",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    db.commit()
    curve = [_hour(6, imp=20, clk=2, cost=2000, avg_rank=3.0, conv_cnt=1)]  # roas=0.5<2.5
    baseline_agg = {"clk": 10, "cost": 7000, "conv_amt": 0}
    fired, reason = auto_operator._intraday_loss_leash(
        db, target_type="keyword", target_id="nkw-child", campaign_id=CAMPAIGN,
        curve=curve, now=NOW, baseline_agg=baseline_agg,
    )
    assert fired is True


def test_intraday_loss_leash_not_fired_when_keyword_entity_missing(db):
    """(f-역) NaverEntity 행 자체가 없는 키워드 → adgroup 해석 불가로 미발동(fail-closed)."""
    curve = [_hour(6, imp=20, clk=2, cost=2000, avg_rank=3.0, conv_cnt=1)]
    baseline_agg = {"clk": 10, "cost": 7000, "conv_amt": 0}
    fired, reason = auto_operator._intraday_loss_leash(
        db, target_type="keyword", target_id="nkw-ghost", campaign_id=CAMPAIGN,
        curve=curve, now=NOW, baseline_agg=baseline_agg,
    )
    assert fired is False
    assert "adgroup 해석 불가" in reason


def test_intraday_loss_leash_works_directly_on_adgroup_target_type(db):
    """target_type='adgroup'이면 target_id를 그대로 adgroup_id로 사용(SHOPPING/BRAND_SEARCH grain)."""
    _seed_product_bep(db, adgroup_id="grp-shop", bep_roas=Decimal("2.5"))
    curve = [_hour(6, imp=20, clk=2, cost=2000, avg_rank=3.0, conv_cnt=1)]  # roas=0.5<2.5
    baseline_agg = {"clk": 10, "cost": 7000, "conv_amt": 0}
    fired, reason = auto_operator._intraday_loss_leash(
        db, target_type="adgroup", target_id="grp-shop", campaign_id=CAMPAIGN,
        curve=curve, now=NOW, baseline_agg=baseline_agg,
    )
    assert fired is True


def test_judge_hourly_leash_fires_down_even_when_up_conditions_present(db):
    """_judge_hourly 통합: rank>4(UP 자격)인데 장중 loss 고삐가 먼저 걸리면 고삐 DOWN이
    이긴다(우선순위: 과열밴드DOWN·CPC급등DOWN 뒤, UP 앞) — leash 플래그로 구분 가능해야 한다."""
    _seed_product_bep(db, bep_roas=Decimal("2.5"))
    _leash_keyword(db)
    window_from, window_to = _settlement_window()
    # 정착창 실적(=baseline_agg 소스): cost=7000 → avg_daily=1000. CPC 급등 임계=350×2=700 초과 안 함.
    _ad_row(db, keyword_id="nkw-leash", ad_date=window_from, clk=20, cost=7000)
    db.commit()

    curve = [
        _hour(10, imp=15, clk=2, cost=400, avg_rank=5.0, conv_cnt=0),
        _hour(11, imp=15, clk=2, cost=400, avg_rank=5.0, conv_cnt=0),
        _hour(12, imp=15, clk=2, cost=400, avg_rank=5.0, conv_cnt=0),
    ]  # weighted_rank=5.0>4.0(UP 자격), today_cost=1200≥avg1000, conv=0→est_roas=0<2.5(고삐 발동)
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    verdict = auto_operator._judge_hourly(
        db, target_type="keyword", target_id="nkw-leash", campaign_id=CAMPAIGN,
        curve=curve, now=now_midday,
    )
    assert verdict["direction"] == "down"
    assert verdict.get("leash") is True
    assert "순위고삐" in verdict["reason"]


def test_judge_hourly_leash_not_fired_when_sample_insufficient(db):
    """표본 부족(imp<30)이면 고삐 판정 자체에 도달하지 않고 기존대로 hold(leash 상품 매핑이
    있어도 표본 게이트가 우선)."""
    _seed_product_bep(db, bep_roas=Decimal("2.5"))
    _leash_keyword(db)
    curve = [_hour(6, imp=5, clk=0, cost=2000, avg_rank=3.0, conv_cnt=0)]  # imp합계=5<30
    verdict = auto_operator._judge_hourly(
        db, target_type="keyword", target_id="nkw-leash", campaign_id=CAMPAIGN,
        curve=curve, now=NOW,
    )
    assert verdict["direction"] == "hold"
    assert "leash" not in verdict


def test_hourly_lane_leash_fires_and_tags_rationale(db):
    """run_hourly_lane 통합: 고삐 발동 → rationale [순위고삐] 접두, approval_source=
    APPROVAL_SOURCE_HOURLY(신규 소스 안 만듦), harness.execute 경유, diary actor=ACTOR_HOURLY."""
    _settings(db)
    _seed_product_bep(db, bep_roas=Decimal("2.5"))
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-leash-lane", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-leash-lane", ad_date=window_from, clk=20, cost=7000)
    db.commit()

    curve = [
        _hour(6, imp=15, clk=2, cost=400, avg_rank=3.2, conv_cnt=0),
        _hour(7, imp=15, clk=2, cost=400, avg_rank=3.2, conv_cnt=0),
        _hour(8, imp=15, clk=2, cost=400, avg_rank=3.2, conv_cnt=0),
    ]  # 중립밴드(과열/CPC급등 아님)·today_cost=1200≥avg1000·est_roas=0<2.5 → 고삐 발동
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=NOW, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_called_once()
    assert result["approved"] == 1
    proposal_id = mock_exec.call_args[0][1]
    saved = db.get(NaverProposal, proposal_id)
    assert saved.proposal_type == "bid_down"
    assert saved.rationale.startswith("[순위고삐]")
    assert saved.approval_source == auto_operator.APPROVAL_SOURCE_HOURLY  # 신규 소스 안 만듦
    assert saved.target_bid == 850  # 1000×0.85 → 10원 올림 클램프(일반 down과 동일 스텝)


def test_hourly_lane_leash_blocked_by_real_guardrail_cooldown(db):
    """★독립 Opus 리뷰 권고(RL3 GATE PASS) — 고삐 우회 없음 증명. 고삐(is_leash)가 발동해도
    execute()가 여느 bid_down과 동일하게 guardrail_gate를 전량 통과해야 한다(§금지선 —
    고삐 전용 우회 경로 금지). 이 유닛에 2시간 이내 성공 change_log(after_value 존재·
    dry_run=False)를 실제로 심어(mock 아님) guardrail_gate.check가 진짜 쿨다운 차단을 내는지
    확인한다 — test_hourly_lane_execution_blocked_by_real_guardrail_cooldown(일반 시간당밴드
    down)과 동일 관례를 고삐 경로에 그대로 적용(naver_sa_writer만 최하단에서 mock, harness.
    execute·guardrail_gate.check는 실호출)."""
    _settings(db)
    _seed_product_bep(db, bep_roas=Decimal("2.5"))
    window_from, window_to = _settlement_window()
    target_id = "nkw-leash-cd"
    db.add(NaverEntity(entity_type="keyword", entity_id=target_id, parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id=target_id, ad_date=window_from, clk=20, cost=7000)  # avg_daily=1000·CPC급등 아님
    # 1시간 전 우리 시스템이 이미 이 키워드를 변경 — 쿨다운 2h 이내(guardrail_gate D-NAO-55)
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id=target_id, campaign_id=CAMPAIGN,
        action="update_bid", dry_run=False,
        after_value=json.dumps({"bidAmt": 1000, "userLock": False}),
        changed_at=NOW - timedelta(hours=1),
    ))
    db.commit()

    curve = [
        _hour(6, imp=15, clk=2, cost=400, avg_rank=3.2, conv_cnt=0),
        _hour(7, imp=15, clk=2, cost=400, avg_rank=3.2, conv_cnt=0),
        _hour(8, imp=15, clk=2, cost=400, avg_rank=3.2, conv_cnt=0),
    ]  # 중립밴드(과열/CPC급등 아님)·today_cost=1200≥avg1000·est_roas=0<2.5 → 고삐 발동

    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_sa_writer, "update_keyword_bid") as mock_write:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda tid, d: curve)

    # 고삐 제안은 승인까지는 됐으나(auto_operator 책임 끝) 실 harness.execute()의 실 guardrail_gate가
    # 쿨다운으로 최종 차단(harness 책임) — 실 입찰변경(update_keyword_bid)은 발생하지 않는다.
    assert result["approved"] == 1
    assert result["executed"] == 0
    assert result["failed"] == 1
    mock_write.assert_not_called()  # 우회 없음의 실행 증명 — 고삐도 쓰기 초크포인트를 못 벗어난다

    proposals = db.query(NaverProposal).filter(NaverProposal.target_id == target_id).all()
    assert len(proposals) == 1
    assert proposals[0].proposal_type == "bid_down"
    assert proposals[0].rationale.startswith("[순위고삐]")
    assert proposals[0].approval_source == auto_operator.APPROVAL_SOURCE_HOURLY  # 신규 소스 안 만듦
    assert proposals[0].status == "failed"  # 실 guardrail_gate 차단 → harness._guard_failure

    logs = db.query(NaverChangeLog).filter(
        NaverChangeLog.entity_id == target_id, NaverChangeLog.action == "update_bid",
    ).order_by(NaverChangeLog.id.desc()).all()
    blocked = [l for l in logs if l.outcome == "failed"]
    assert len(blocked) == 1
    assert "가드레일 차단" in blocked[0].rationale
    assert "쿨다운" in blocked[0].rationale  # 실 guardrail_gate.check가 낸 실제 사유(D-NAO-19/55)


# ══════════════════════════ DL4 익일 밴드 재시작 + 관성 + 자정상태 (D-NAO-65) ══════════════════════════
# 재시작 = 기존 시간당 UP 경로(BEP 게이트 종속)가 자연 수행 / 재시작 천장 = learned band /
# 승자 관성 = 강제 하향 경로 없음 / 총계 커플링(DL3 changes_today_count) 실순서 고정.


def test_dl4_yesterday_leashed_healthy_unit_restarts_up_today(db):
    """item1(이미 자연 수행): 어제 고삐로 rank>4까지 내려간 '건강' 유닛(정착창 D-8~D-2 ROAS≥
    target·오늘 페이싱 저속)이 다음날 기존 UP 경로로 자연 재시작 상향. 어제 고삐 bid_down 이력
    (change_log)이 오늘 UP을 막지 않는다(카운트 일 리셋의 실순서). 학습된 밴드가 '없는 셀'이라
    재시작 천장 게이트는 폴백 통과 — 재시작 메커니즘에 신규 코드 불요, 배선/검증만."""
    _settings(db, target_roas_override=Decimal("2.0"))
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-restart", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    # 정착창(D-8~D-2) 건강: ROAS 60000/7000≈8.6 ≥ 2.0(rpc≈3000→경제성 상한≈1500), baseline CPC 350원
    _ad_row(db, keyword_id="nkw-restart", ad_date=window_from, clk=20, cost=7000, conv_direct_amt=60000)
    # 어제(D-1) 고삐 하향 이력 — 오늘 재시작 UP을 막으면 안 됨(자정 KST-today 리셋, prefilter도 통과)
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-restart", campaign_id=CAMPAIGN,
        action="update_bid", dry_run=False,
        after_value=json.dumps({"bidAmt": 850, "userLock": False}),
        changed_at=NOW - timedelta(days=1),
    ))
    # R2 estimate 경로 예산 pace용 hour12 스냅샷.
    db.add(NaverHourlySnapshot(
        snapshot_at=datetime(2026, 7, 20, 12, 0, 0), ad_date=TODAY, snapshot_hour=12,
        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", cost=0, clk=0, imp=0, daily_budget=100000,
    ))
    db.commit()

    up_curve = [
        _hour(10, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(11, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(12, imp=15, clk=2, cost=30, avg_rank=5.0),
    ]  # rank 5.0>4(어제 고삐로 스로틀됨) → estimate 목표순위 4
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 850}), \
         patch.object(auto_operator, "estimate_average_position_bid",
                       return_value=[{"nccKeywordId": "nkw-restart", "position": 4, "bid": 1200}]), \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "actual_revenue_ratio"}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_midday, fetch_intraday=lambda tid, d: up_curve,
        )
    mock_exec.assert_called_once()  # 어제 고삐가 오늘 재시작 UP을 막지 않음(estimate 직행으로 절체)
    assert result["rank_direct"] == 1
    saved = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert saved.proposal_type == "bid_up_rank"  # R2: 파워링크 estimate 직행
    assert saved.target_bid == 1200  # min(경제성 상한 1500, estimate 1200), 현재 850 초과 유효 스텝
    assert saved.approval_source == auto_operator.APPROVAL_SOURCE_HOURLY  # 탐침 아님(일반 재시작)


def test_dl4_judge_hourly_restart_waiting_reason_when_settlement_roas_below_target(db):
    """item5(Fable 조건①: 스로틀 고착 관측): rank>4(스로틀/저입찰)인데 정착창 보정ROAS<target →
    hold 사유에 '재시작 대기(정착창 ROAS 미달)' 명시. 만성 sub-BEP 유닛이 UP 게이트를 못 넘고
    바닥에 눌러앉는 관측 신호를 기존 hold reason으로 표면화(새 테이블 없음)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-wait", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-wait", ad_date=window_from, clk=20, cost=7000, conv_direct_amt=7000)  # ROAS 1.0<2.0
    db.commit()
    curve = [
        _hour(10, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(11, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(12, imp=15, clk=2, cost=30, avg_rank=5.0),
    ]
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    with patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "actual_revenue_ratio"}):
        verdict = auto_operator._judge_hourly(
            db, target_type="keyword", target_id="nkw-wait", campaign_id=CAMPAIGN,
            curve=curve, now=now_midday,
        )
    assert verdict["direction"] == "hold"
    # D-NAO-66: 순위 전제 폐지 후 "재시작 대기"의 진짜 사유 = ROAS 미달(장중 tally·정착 둘 다 불통과).
    assert "재시작 대기(ROAS 미달)" in verdict["reason"]
    assert "정착" in verdict["reason"] and "ROAS" in verdict["reason"]


def test_dl4_general_up_not_capped_by_learned_band_d_nao_66(db):
    """★D-NAO-66(IU2) 재시작 천장 폐지(구 test_dl4_general_up_skipped_when_learned_band_reached
    를 반전): 종전엔 일반 UP이 학습밴드(4.0+·이미 도달)에 걸려 _learned_optimal_skip 천장이 상향을
    취소했다(hold). 이제 learned band는 하드 캡이 아니라 **탐침 프라이어**로 강등 — ROAS-driven
    UP은 밴드를 참조하지 않고 정착창 ROAS≥target ∧ 예산 여력이면 그대로 상향한다(순위는 목표가
    아니라 결과). 오버슛은 스텝1+쿨다운2h로 자연 캡."""
    _settings(db, target_roas_override=Decimal("2.0"))
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-cap", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-cap", ad_date=window_from, clk=20, cost=7000, conv_direct_amt=60000)  # rpc≈3000→상한≈1500
    _seed_learned_band(db, avg_rank=Decimal("4.5"))  # 학습 최적밴드 4.0+ — 종전엔 여기서 UP 취소됐음
    db.add(NaverHourlySnapshot(  # R2 estimate 경로 예산 pace용 hour12 스냅샷
        snapshot_at=datetime(2026, 7, 20, 12, 0, 0), ad_date=TODAY, snapshot_hour=12,
        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", cost=0, clk=0, imp=0, daily_budget=100000,
    ))
    db.commit()

    up_curve = [
        _hour(10, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(11, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(12, imp=15, clk=2, cost=30, avg_rank=5.0),
    ]  # rank 5.0 — 학습밴드 4.0+에 이미 있으나 D-NAO-66은 밴드로 UP을 막지 않는다
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator, "estimate_average_position_bid",
                       return_value=[{"nccKeywordId": "nkw-cap", "position": 4, "bid": 1200}]), \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "actual_revenue_ratio"}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_midday, fetch_intraday=lambda tid, d: up_curve,
        )
    mock_exec.assert_called_once()  # 학습밴드 천장 없음 — ROAS+예산으로 UP 진행(estimate 직행)
    assert result["approved"] == 1
    assert result["rank_direct"] == 1
    assert not any("재시작 천장" in h["reason"] for h in result["held"])
    saved = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert saved.proposal_type == "bid_up_rank"  # R2: 파워링크 estimate 직행


def test_dl4_general_up_proceeds_regardless_of_learned_band(db):
    """D-NAO-66: 학습밴드가 상한이든 하한이든 ROAS-driven UP은 참조하지 않고 진행(밴드=탐침
    프라이어). 학습밴드 2.0-2.5(현재 rank 5.0가 하위)든 위 4.0+든 결과 동일 — UP 실행."""
    _settings(db, target_roas_override=Decimal("2.0"))
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-go", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-go", ad_date=window_from, clk=20, cost=7000, conv_direct_amt=60000)  # rpc≈3000→상한≈1500
    _seed_learned_band(db, avg_rank=Decimal("2.2"))  # 학습 최적밴드 2.0-2.5 — UP 판정에 무관(참조 안 함)
    db.add(NaverHourlySnapshot(  # R2 estimate 경로 예산 pace용 hour12 스냅샷
        snapshot_at=datetime(2026, 7, 20, 12, 0, 0), ad_date=TODAY, snapshot_hour=12,
        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", cost=0, clk=0, imp=0, daily_budget=100000,
    ))
    db.commit()

    up_curve = [
        _hour(10, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(11, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(12, imp=15, clk=2, cost=30, avg_rank=5.0),
    ]  # rank 5.0 ≥ 학습밴드 상한 2.5 → 아직 하위, 재시작 진행
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator, "estimate_average_position_bid",
                       return_value=[{"nccKeywordId": "nkw-go", "position": 4, "bid": 1200}]), \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "actual_revenue_ratio"}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_midday, fetch_intraday=lambda tid, d: up_curve,
        )
    mock_exec.assert_called_once()
    assert result["rank_direct"] == 1
    saved = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert saved.proposal_type == "bid_up_rank"  # R2: 파워링크 estimate 직행
    assert saved.approval_source == auto_operator.APPROVAL_SOURCE_HOURLY  # 일반 재시작 UP(탐침 아님)


def test_iu1_band_inside_healthy_unit_climbs_up_d_nao_66(db):
    """★D-NAO-66(IU1) §3-1①: 밴드 안(rank 3.0)·ROAS 양호·loss 없음 유닛은 강제 하향이 아니라
    profitable-climb으로 UP(순위 무관, target ROAS 유지가 지배). 종전엔 rank가 4 이하라 UP 전제
    (>4)에 안 걸려 hold였다 → 신 UP 차등. 승자는 내려가지 않을 뿐 아니라(관성) 이익 구간이면
    계속 오른다(§0 사이클). leash는 미발동(오늘 loss 없음)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _seed_product_bep(db, adgroup_id="grp-1", bep_roas=Decimal("2.0"))  # 고삐가 '평가'되되 미발동임을 보장
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-inertia", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-inertia", ad_date=window_from, clk=20, cost=7000, conv_direct_amt=21000)
    db.commit()
    curve = [
        _hour(10, imp=20, clk=2, cost=200, avg_rank=3.0, conv_cnt=2),
        _hour(11, imp=20, clk=2, cost=200, avg_rank=3.0, conv_cnt=2),
        _hour(12, imp=20, clk=2, cost=200, avg_rank=3.0, conv_cnt=2),
    ]  # 밴드 안 rank 3.0·CPC 정상·오늘 loss 없음(장중 est ROAS = conv 6×price1000/600 = 10 ≥ target×1.2)
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    # 보정계수 실측(factor 1) 패치 — 정착창 ROAS 3.0 ≥ 2.0(settle ok). 미패치 시 주문매출 0으로
    # factor 0 → 정착 "명시적 미달" → GATE P2-A 정산 거부권이 발동해 이 테스트의 관심사(순위
    # 전제 폐지)가 아닌 다른 게이트를 검증하게 된다(거부권 자체는 전용 테스트가 검증).
    with patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "actual_revenue_ratio"}):
        verdict = auto_operator._judge_hourly(
            db, target_type="keyword", target_id="nkw-inertia", campaign_id=CAMPAIGN,
            curve=curve, now=now_midday,
        )
    assert verdict["direction"] == "up"  # 밴드 안이어도 이익이면 상향(종전 hold와 차등)
    assert verdict.get("leash") is not True  # 강제 하향 없음(오늘 loss 없음)
    assert "ROAS-UP(순위 무관, D-NAO-66)" in verdict["reason"]


def test_iu2_top_rank_no_longer_force_downed_when_no_loss(db):
    """★D-NAO-66(IU2) §3-1②: 밴드 상단(rank 2.0=1~2등)이어도 순위만으로는 강제 하향하지 않는다
    (과열밴드 DOWN 폐지). loss 신호(CPC 급등·est ROAS<BEP)가 없으면 DOWN이 아니라 hold — 순위는
    목표가 아니라 결과. 종전엔 rank<2.5 = 무조건 과열밴드 DOWN이었다 → 신 hold 차등."""
    _settings(db)
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-hot", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    # baseline CPC 100원(cost1000/clk10) → 당일 CPC 50(300/6)<200 급등 아님. 원가 미매핑 → loss 고삐 미평가.
    _ad_row(db, keyword_id="nkw-hot", ad_date=window_from, clk=10, cost=1000)
    db.commit()
    curve = [
        _hour(10, imp=20, clk=2, cost=100, avg_rank=2.0),
        _hour(11, imp=20, clk=2, cost=100, avg_rank=2.0),
        _hour(12, imp=20, clk=2, cost=100, avg_rank=2.0),
    ]  # rank 2.0(상단) — 종전 과열밴드 DOWN, 이제 loss 신호 없어 hold
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    verdict = auto_operator._judge_hourly(
        db, target_type="keyword", target_id="nkw-hot", campaign_id=CAMPAIGN,
        curve=curve, now=now_midday,
    )
    assert verdict["direction"] == "hold"  # 순위만으로 강제 하향 없음(과열밴드 폐지)
    assert verdict.get("leash") is not True


def test_iu2_top_rank_underwater_high_spend_still_leashed(db):
    """★D-NAO-66(IU2) §3-1③ 안전망 생존: 과열밴드 DOWN을 삭제해도 상단 순위(rank 2.0)의 무전환
    고지출 유닛은 loss 고삐 DOWN이 잡는다(전환 0 → est ROAS 0 < BEP, 당일소진≥하루평균). 상단이
    이상 지출일 때의 안전망은 순위 규칙이 아니라 ROAS/BEP 규칙이 담당한다."""
    _settings(db)
    _seed_product_bep(db, adgroup_id="grp-1", bep_roas=Decimal("2.5"))  # 원가 매핑 → 고삐 평가 가능
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-bleed", parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-bleed", ad_date=window_from, clk=10, cost=700)  # 정착창 일평균 cost 100
    db.commit()
    curve = [
        _hour(10, imp=20, clk=2, cost=200, avg_rank=2.0, conv_cnt=0),
        _hour(11, imp=20, clk=2, cost=200, avg_rank=2.0, conv_cnt=0),
        _hour(12, imp=20, clk=2, cost=200, avg_rank=2.0, conv_cnt=0),
    ]  # rank 2.0 상단·전환 0·당일소진 600≥하루평균100 → est ROAS 0<BEP 2.5 → loss 고삐 DOWN
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    verdict = auto_operator._judge_hourly(
        db, target_type="keyword", target_id="nkw-bleed", campaign_id=CAMPAIGN,
        curve=curve, now=now_midday,
    )
    assert verdict["direction"] == "down"
    assert verdict.get("leash") is True  # 순위 규칙이 아니라 ROAS/BEP 고삐가 안전망
    assert "순위고삐" in verdict["reason"]


# ── GATE P2-A(D-NAO-66): 정산 거부권(veto) + 장중-단독 UP 일 1스텝 캡 ──

def _seed_executed_bid_up_today(db, *, target_id, changed_at, target_type="keyword"):
    """오늘 성공한 상향 실쓰기 1건(change_log×proposal 조인 카운터 대상) 시드."""
    p = NaverProposal(
        proposal_type="bid_up", target_type=target_type, target_id=target_id,
        campaign_id=CAMPAIGN, rationale="이전 상향", expected_effect="-", status="approved",
        target_bid=1150,
    )
    db.add(p)
    db.flush()
    db.add(NaverChangeLog(
        entity_type=target_type, entity_id=target_id, campaign_id=CAMPAIGN,
        action="update_bid", dry_run=False, proposal_id=p.id,
        after_value=json.dumps({"bidAmt": 1150, "userLock": False}),
        changed_at=changed_at,
    ))
    db.commit()


def test_gate_p2a_settlement_veto_blocks_intraday_only_up(db):
    """①정산 거부권: 정착창이 **명시적 미달**(데이터 충분·ROAS 1.0<target 2.0)이면 장중 추정이
    아무리 좋아도(tally 6·est ROAS 10) UP 금지 — 정산(간접전환 포함 실측)이 나쁘다는데 장중
    추정(과대귀속 가능)만으로 올리지 않는다."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _seed_product_bep(db, adgroup_id="grp-1", price=Decimal("1000"), bep_roas=Decimal("2.0"))
    _leash_keyword(db, target_id="nkw-veto")
    window_from, _ = _settlement_window()
    _ad_row(db, keyword_id="nkw-veto", ad_date=window_from, clk=20, cost=7000, conv_direct_amt=7000)  # ROAS 1.0<2.0
    db.commit()
    curve = [
        _hour(10, imp=20, clk=2, cost=200, avg_rank=3.0, conv_cnt=2),
        _hour(11, imp=20, clk=2, cost=200, avg_rank=3.0, conv_cnt=2),
        _hour(12, imp=20, clk=2, cost=100, avg_rank=3.0, conv_cnt=2),
    ]  # est ROAS = 6000/500=12 ≥ 2.4(장중 좋음)·CPC 83<350×2·leash 미발동(est≥bep)
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    with patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "actual_revenue_ratio"}):
        verdict = auto_operator._judge_hourly(
            db, target_type="keyword", target_id="nkw-veto", campaign_id=CAMPAIGN,
            curve=curve, now=now_midday,
        )
    assert verdict["direction"] == "hold"
    assert "정산 거부권" in verdict["reason"]


def test_gate_p2a_intraday_only_up_capped_at_one_step_per_day(db):
    """②장중-단독 일 1스텝 캡: 정착창 판정불가(실적 없음=unknown) + 장중 tally 좋음 → 오늘 첫
    UP은 발사, 성공 상향 1건이 기록된 뒤엔 같은 날 추가 UP 금지(hold — +15%/일 제한). 다음날
    정산이 따라와 veto(below)하거나 승인(ok)한다."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _seed_product_bep(db, adgroup_id="grp-1", price=Decimal("1000"), bep_roas=Decimal("2.0"))
    _leash_keyword(db, target_id="nkw-cap1")
    window_from, _ = _settlement_window()
    _ad_row(db, keyword_id="nkw-cap1", ad_date=window_from, clk=20, cost=0)  # 실적 cost 0 → settle unknown
    db.commit()
    curve = [
        _hour(10, imp=20, clk=2, cost=250, avg_rank=3.0, conv_cnt=2),
        _hour(11, imp=20, clk=2, cost=250, avg_rank=3.0, conv_cnt=1),
    ]  # est ROAS = 3000/500=6.0 ≥ 2.4·tally 3≥2·imp 40≥30
    now_midday = datetime(2026, 7, 20, 12, 20, 0)

    # 오늘 첫 판정 — 장중 단독 근거 UP 발사
    first = auto_operator._judge_hourly(
        db, target_type="keyword", target_id="nkw-cap1", campaign_id=CAMPAIGN,
        curve=curve, now=now_midday,
    )
    assert first["direction"] == "up"
    assert "장중 tally" in first["reason"]

    # 오늘 성공 상향 1건 기록 후 — 같은 날 두 번째 장중 단독 UP은 캡에 걸려 hold
    _seed_executed_bid_up_today(db, target_id="nkw-cap1", changed_at=now_midday - timedelta(hours=2))
    second = auto_operator._judge_hourly(
        db, target_type="keyword", target_id="nkw-cap1", campaign_id=CAMPAIGN,
        curve=curve, now=now_midday,
    )
    assert second["direction"] == "hold"
    assert "1스텝 캡" in second["reason"]


def test_gate_p2a_settlement_ok_up_not_limited_by_one_step_cap(db):
    """③settle ok 근거 UP은 1스텝 캡 미적용 — 오늘 이미 성공 상향이 있어도 정착창 실측이
    합격이면 UP 진행(기존 가드레일 일일상한 3이 최종 방어선, 판정 레벨 추가 제한 없음)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _leash_keyword(db, target_id="nkw-settle")
    window_from, _ = _settlement_window()
    _ad_row(db, keyword_id="nkw-settle", ad_date=window_from, clk=20, cost=7000, conv_direct_amt=21000)  # ROAS 3.0≥2.0
    db.commit()
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    _seed_executed_bid_up_today(db, target_id="nkw-settle", changed_at=now_midday - timedelta(hours=3))
    curve = [
        _hour(10, imp=20, clk=2, cost=35, avg_rank=3.0),
        _hour(11, imp=20, clk=2, cost=35, avg_rank=3.0),
    ]  # conv_cnt 0 → intraday 불발, settle ok 단독 근거
    with patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "actual_revenue_ratio"}):
        verdict = auto_operator._judge_hourly(
            db, target_type="keyword", target_id="nkw-settle", campaign_id=CAMPAIGN,
            curve=curve, now=now_midday,
        )
    assert verdict["direction"] == "up"
    assert "정착창 실측" in verdict["reason"]


# ── IU1(D-NAO-66) 장중 tally UP 게이트 순수 단위(_intraday_up_ok) — §3-1④ fail-closed ──

def test_intraday_up_ok_fails_when_price_unknown(db):
    """원가 미확인 상품(product_bep 매핑 없음)이면 price=None → 장중 UP 판정 불가(fail-closed,
    RL3 고삐와 동일 관례)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _leash_keyword(db, target_id="nkw-noprice")  # 원가 매핑 없음
    curve = [_hour(9, imp=40, clk=2, cost=100, avg_rank=3.0, conv_cnt=3)]
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    ok, reason = auto_operator._intraday_up_ok(
        db, target_type="keyword", target_id="nkw-noprice", campaign_id=CAMPAIGN,
        curve=curve, now=now_midday,
    )
    assert ok is False and "단가 미확인" in reason


def test_intraday_up_ok_fails_when_conv_tally_below_min(db):
    """직접전환 tally < _INTRADAY_UP_MIN_CONV(2)면 추정 ROAS가 높아도 상향 근거 부족(불발)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _seed_product_bep(db, adgroup_id="grp-1", price=Decimal("1000"), bep_roas=Decimal("2.0"))
    _leash_keyword(db, target_id="nkw-thin")
    # conv 1건뿐(<2) — 추정 ROAS(1000/100=10)는 target×1.2 넘어도 tally 미달로 불발
    curve = [_hour(9, imp=40, clk=2, cost=100, avg_rank=3.0, conv_cnt=1)]
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    ok, reason = auto_operator._intraday_up_ok(
        db, target_type="keyword", target_id="nkw-thin", campaign_id=CAMPAIGN,
        curve=curve, now=now_midday,
    )
    assert ok is False and "tally" in reason


def test_intraday_up_ok_fails_when_roas_below_margin(db):
    """추정 ROAS가 target×여유계수(1.2)에 못 미치면 불발(과소추정에도 확실 이익 구간만 상향)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _seed_product_bep(db, adgroup_id="grp-1", price=Decimal("1000"), bep_roas=Decimal("2.0"))
    _leash_keyword(db, target_id="nkw-marg")
    # conv 2·price 1000 → revenue 2000, cost 1000 → 추정 ROAS 2.0 < target 2.0×1.2=2.4 → 불발
    curve = [_hour(9, imp=40, clk=2, cost=1000, avg_rank=3.0, conv_cnt=2)]
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    ok, reason = auto_operator._intraday_up_ok(
        db, target_type="keyword", target_id="nkw-marg", campaign_id=CAMPAIGN,
        curve=curve, now=now_midday,
    )
    assert ok is False and "여유 미달" in reason


def test_intraday_up_ok_true_when_all_met(db):
    """전환 tally≥2 ∧ 추정 ROAS ≥ target×1.2 ∧ 표본 충분 → 장중 UP 근거 성립(순위 무관)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _seed_product_bep(db, adgroup_id="grp-1", price=Decimal("1000"), bep_roas=Decimal("2.0"))
    _leash_keyword(db, target_id="nkw-up-ok")
    # conv 3·price 1000 → revenue 3000, cost 500 → 추정 ROAS 6.0 ≥ 2.4, imp 40≥30
    curve = [_hour(9, imp=40, clk=2, cost=500, avg_rank=3.0, conv_cnt=3)]
    now_midday = datetime(2026, 7, 20, 12, 20, 0)
    ok, reason = auto_operator._intraday_up_ok(
        db, target_type="keyword", target_id="nkw-up-ok", campaign_id=CAMPAIGN,
        curve=curve, now=now_midday,
    )
    assert ok is True and "tally 충족" in reason


def test_dl4_morning_restart_up_passes_cap_after_yesterdays_downs_reset():
    """item4(DL4×DL3 커플링·실순서): 아침 재시작 UP은 changes_today_count=0(KST-today 리셋)에서
    오므로 어제 bid_down이 몇 회였든 일일상한(3)에 안 걸린다. 어제 카운트가 오늘로 새면(리셋
    실패) 이 UP이 막힌다 — guardrail_gate가 실제로 통과시키는지 고정(harness의 카운트 일 리셋
    계산은 test_naver_execution_harness에서 별도 실증)."""
    from app.services.naver_ad import guardrail_gate as gate
    now = datetime(2026, 7, 20, 8, 30, 0)
    ctx = {
        "current_bid": 1000, "current_budget": None, "roas_corrected": 3.0, "target_roas": 2.0,
        "cost_today": 0, "daily_budget": 50_000, "unconverted_spend": 0,
        "last_change_at": now - timedelta(hours=20),  # 어제 마지막 고삐(쿨다운 2h 경과)
        "changes_today_count": 0,  # 자정 리셋 — 어제 8회는 오늘 총계에 안 샘
    }
    reason = gate.check(
        {"proposal_type": "bid_up", "target_bid": 1150, "target_lock": None, "target_budget": None},
        ctx, now=now,
    )
    assert reason is None  # 아침 재시작 UP 통과


def test_dl4_same_day_down_flood_leaks_up_slot_but_bid_down_stays_exempt():
    """DL3 리뷰 P3 입력(커플링 명시): changes_today_count는 유형 무관 총계 → 같은 날 DOWN 8회면
    UP 슬롯(상한 3)이 잠식돼 bid_up은 차단(실순서상 아침 재시작=count 0이라 대체로 무해하나
    커플링 고정). bid_down은 DL3 면제라 8회째도 통과('쭉 낮추다가')."""
    from app.services.naver_ad import guardrail_gate as gate
    now = datetime(2026, 7, 20, 15, 0, 0)
    ctx = {
        "current_bid": 1000, "current_budget": None, "roas_corrected": 3.0, "target_roas": 2.0,
        "cost_today": 0, "daily_budget": 50_000, "unconverted_spend": 0,
        "last_change_at": None, "changes_today_count": 8,
    }
    up = gate.check(
        {"proposal_type": "bid_up", "target_bid": 1150, "target_lock": None, "target_budget": None},
        ctx, now=now,
    )
    assert up is not None and "일일 변경" in up  # UP 슬롯 잠식(총계 8≥3)
    down = gate.check(
        {"proposal_type": "bid_down", "target_bid": 850, "target_lock": None, "target_budget": None},
        ctx, now=now,
    )
    assert down is None  # bid_down은 DL3 면제(쭉 낮추다가)


# ══════════════════════ IU-R R1 쇼핑검색 폐루프 순위 서보 ══════════════════════
# grain 라우팅(SHOPPING adgroup=서보 / BRAND_SEARCH adgroup=±15% / DOWN·probe·ad=기존),
# bid_up_servo proposal_type·서보 스텝·예산 pace·데드밴드/최상단 hold·ad 누출 0·실집행.


_SERVO_NOW = datetime(2026, 7, 20, 12, 20, 0)
_SERVO_CORR = {"factor": Decimal("1"), "source": "actual_revenue_ratio"}


def _servo_shopping_unit(db, *, campaign_type="SHOPPING", adgroup_id="grp-shop",
                         settle_clk=20, settle_cost=7000, settle_conv=60000,
                         snap_daily_budget=100000, snap_cost=0):
    """서보 대상 쇼검(또는 BRAND_SEARCH) 광고그룹 시드 — 정착창 ROAS ok(UP 발동) + 핫셋 자격
    + **현실적 당일 스냅샷(snapshot_hour=12=_SERVO_NOW.hour)**. P2-2 이후 서보 pace는
    snapshot_hour<=now.hour만 쓰므로(미래 스냅샷 배제) _settings 기본 hour23은 서보 pace에서
    배제된다 — 서보 테스트용 hour12 스냅샷을 여기서 별도 시드(서킷브레이커 신선·pace 소스 둘 다
    충족). snap_daily_budget/snap_cost로 pace 시나리오(여유/차단/uncapped)를 제어한다."""
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=CAMPAIGN,
                        campaign_id=CAMPAIGN, campaign_type=campaign_type, status="on"))
    window_from, _ = _settlement_window()
    _ad_row(db, adgroup_id=adgroup_id, keyword_id="", campaign_type=campaign_type,
            ad_date=window_from, clk=settle_clk, cost=settle_cost, conv_direct_amt=settle_conv)
    db.add(NaverHourlySnapshot(
        snapshot_at=_SERVO_NOW, ad_date=TODAY, snapshot_hour=12, campaign_id=CAMPAIGN,
        campaign_type=campaign_type, cost=snap_cost, clk=0, imp=0, daily_budget=snap_daily_budget,
    ))
    db.commit()


def _servo_curve(avg_rank=5.0):
    # imp=45(≥30)·avg_rank·오늘 CPC=300/6=50원<정착350×2(급등 아님)·오늘소진 작음.
    return [
        _hour(9, imp=15, clk=2, cost=100, avg_rank=avg_rank),
        _hour(10, imp=15, clk=2, cost=100, avg_rank=avg_rank),
        _hour(11, imp=15, clk=2, cost=100, avg_rank=avg_rank),
    ]


def test_hourly_lane_shopping_adgroup_up_routes_to_servo(db):
    """UP∧SHOPPING adgroup → 순위 서보(bid_up_servo). 관측 4.9위→목표 4위·서보 스텝(콜드
    1150=1000×1.15, 경제성 상한 1500·캡 1500 내). 정상 유닛은 hold되지 않는다(codex P1-2 봉인)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _servo_shopping_unit(db)  # rpc≈3000 → 경제성 상한 1500
    curve = _servo_curve(avg_rank=4.9)
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid",
                       return_value={"source": "group", "effective_bid": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    mock_exec.assert_called_once()
    assert result["servo"] == 1
    assert result["held"] == []  # 정상 유닛 hold 아님(codex P1-2)
    saved = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert saved.proposal_type == "bid_up_servo"
    assert saved.target_bid == 1150
    assert saved.target_type == "adgroup"
    assert saved.approval_source == auto_operator.APPROVAL_SOURCE_HOURLY
    assert saved.rationale.startswith("[순위서보]")


# ══════ D-NAO-218(M2-b2): rank_servo:49 소비처 — 경제성상한 명목 스케일 환산 ══════
# ref 65 정정 #2: economic_ceiling(실효/원가 스케일)을 current_bid·target_bid(명목)와
# 같은 스케일로 비교하면 기기가중치≠100일 때 어긋난다. 두 테스트가 같은 시나리오
# (경제성상한 고정 1100원 — _servo_economic_ceiling을 직접 패치해 RPC 산정과 분리)를
# 가중치 미설정/50%로 갈라 target_bid 차이를 고정한다(배선 전/후 산출 차이의 라이브 증거).


def test_hourly_lane_servo_ceiling_unweighted_clamps_at_nominal_value(db):
    """회귀 기준선: 기기가중치 미설정(NULL) → 100 취급 → 경제성상한 그대로(1100원)가
    raw_target(1150)보다 작아 거기서 잘린다. target_bid=1100(현재 1000 초과·70원 이상 유효)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _servo_shopping_unit(db, adgroup_id="grp-shop-nodw")
    curve = _servo_curve(avg_rank=4.9)
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid",
                       return_value={"source": "group", "effective_bid": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator, "_servo_economic_ceiling", return_value=1100), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    mock_exec.assert_called_once()
    assert result["servo"] == 1
    saved = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert saved.target_bid == 1100  # 경제성상한(1100)이 raw_target(1150)보다 작아 거기서 잘림


def test_hourly_lane_servo_ceiling_device_weighted_does_not_over_clamp(db):
    """★핵심 회귀(M2-b2 합격②): 같은 경제성상한 1100원인데 이 그룹의 기기가중치가 50/50이면
    실제로는 명목 1원당 절반만 나간다 — 상한을 명목 스케일로 되돌리면 2200원이 되어
    raw_target(1150)이 더 이상 안 잘린다. target_bid=1150(위 테스트의 1100과 달라야 한다 —
    이게 배선 전/후 산출값 차이의 라이브 증거)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _servo_shopping_unit(db, adgroup_id="grp-shop-dw")
    # entity_sync가 매일 채우는 자리 — 여기선 소비 로직만 검증하므로 직접 시드한다.
    db.query(NaverEntity).filter(NaverEntity.entity_id == "grp-shop-dw").update(
        {"pc_bid_weight": 50, "mobile_bid_weight": 50}
    )
    db.commit()
    curve = _servo_curve(avg_rank=4.9)
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid",
                       return_value={"source": "group", "effective_bid": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator, "_servo_economic_ceiling", return_value=1100), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    mock_exec.assert_called_once()
    assert result["servo"] == 1
    saved = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert saved.target_bid == 1150  # raw_target(콜드 1000×1.15) — 명목환산상한 2200이 안 잘림


def test_hourly_lane_servo_logs_partial_device_weight_via_or_not_and(db, caplog):
    """★P2-3(적대 리뷰 채택, 변이8 생존 대응) — 기기가중치 로그 분기는 **OR**다(둘 중 하나만
    100이 아니어도 찍힌다). pc=100·mobile=70은 배율=max(100,70)/100=1이라 target_bid로는
    두 그룹(로그 분기 AND vs OR)을 못 가른다 — 로그 자체를 캡처해서 가른다. AND로 바뀌면
    이 케이스(pc=100 ∧ mobile≠100)에서 조용히 안 찍힌다."""
    import logging as _logging
    caplog.set_level(_logging.DEBUG, logger="app.services.naver_ad.auto_operator")
    _settings(db, target_roas_override=Decimal("2.0"))
    _servo_shopping_unit(db, adgroup_id="grp-shop-mixed")
    db.query(NaverEntity).filter(NaverEntity.entity_id == "grp-shop-mixed").update(
        {"pc_bid_weight": 100, "mobile_bid_weight": 70}
    )
    db.commit()
    curve = _servo_curve(avg_rank=4.9)
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid",
                       return_value={"source": "group", "effective_bid": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator, "_servo_economic_ceiling", return_value=1100), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    mock_exec.assert_called_once()
    assert any("기기가중치 pc=100 mobile=70" in r.message for r in caplog.records), (
        "OR 분기가 AND로 바뀌면(pc=100이라 앞항 False) 이 로그가 조용히 사라진다"
    )


def test_hourly_lane_brand_search_adgroup_up_uses_clamp_not_servo(db):
    """UP∧BRAND_SEARCH adgroup → 기존 _clamp_step ±15%(codex P1-3, 서보 미적용·UP 회귀 아님).
    proposal_type=bid_up(서보 아님)·target_bid=1150(±15%)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _servo_shopping_unit(db, campaign_type="BRAND_SEARCH", adgroup_id="grp-bs")
    curve = _servo_curve(avg_rank=4.9)
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid",
                       return_value={"source": "group", "effective_bid": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    mock_exec.assert_called_once()
    assert result["servo"] == 0
    saved = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert saved.proposal_type == "bid_up"  # 서보 아님
    assert saved.target_bid == 1150


def test_hourly_lane_servo_deadband_converged_hold(db):
    """관측 4.1위(목표 4·|4.1−4|=0.1≤데드밴드 0.3) → 서보 스텝 없음(진동 차단). execute 없음."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _servo_shopping_unit(db)
    curve = _servo_curve(avg_rank=4.1)
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid",
                       return_value={"source": "group", "effective_bid": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    mock_exec.assert_not_called()
    assert result["servo"] == 0
    assert any("[순위서보]" in h["reason"] and "데드밴드" in h["reason"] for h in result["held"])


def test_hourly_lane_servo_top_of_page_hold(db):
    """관측 1.2위(≤1+데드밴드) → converged hold(1위권은 더 올릴 순위 없음, codex P1-4)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _servo_shopping_unit(db)
    curve = _servo_curve(avg_rank=1.2)
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid",
                       return_value={"source": "group", "effective_bid": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    mock_exec.assert_not_called()
    assert result["servo"] == 0
    assert any("[순위서보]" in h["reason"] and "최상단" in h["reason"] for h in result["held"])


def test_hourly_lane_servo_budget_pace_blocks(db):
    """서보 스텝은 나오지만 예산 pace(잔여시간 예상지출>잔여예산×0.8)로 사전 차단(codex P1-2).
    hour12 스냅샷 daily_budget=1000(잔여 적음) → pace 초과. _budget_headroom_ok는 _settings
    기본 hour23(budget 100000)으로 통과(pace가 유일 차단자)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _servo_shopping_unit(db, snap_daily_budget=1000)  # hour12 잔여 적음 → pace 차단
    curve = _servo_curve(avg_rank=4.9)
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid",
                       return_value={"source": "group", "effective_bid": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    mock_exec.assert_not_called()
    assert result["servo"] == 0
    assert any("[순위서보]" in h["reason"] and "pace" in h["reason"] for h in result["held"])


def test_hourly_lane_servo_budget_pace_uncapped_passes(db):
    """hour12 스냅샷 daily_budget=0(uncapped) → 서보 pace 제약 없음, 서보 스텝 통과."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _servo_shopping_unit(db, snap_daily_budget=0, snap_cost=999999)  # hour12 uncapped
    curve = _servo_curve(avg_rank=4.9)
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid",
                       return_value={"source": "group", "effective_bid": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    mock_exec.assert_called_once()
    assert result["servo"] == 1


def test_hourly_lane_servo_not_used_when_ad_routed_up_held(db):
    """ad-라우팅 유닛(실효=소재입찰)의 UP은 서보 진입 전에 이미 hold(카나리 2단계) — 서보
    누출 0. bid_up_servo 제안 0건·execute 없음(codex ⑥ ad 카나리 UP 누출 0)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _servo_shopping_unit(db)
    curve = _servo_curve(avg_rank=4.9)
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid",
                       return_value={"source": "ad", "effective_bid": 900, "max_ad_id": "ad-1"}), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    mock_exec.assert_not_called()
    assert result["servo"] == 0
    assert db.query(NaverProposal).filter(NaverProposal.proposal_type == "bid_up_servo").count() == 0


def test_hourly_lane_servo_hot_set_excludes_zero_click(db):
    """codex P2-4 봉인: 정착창 clk<10 유닛은 핫셋에서 제외 → 서보가 clk=0 유닛에 절대 안 붙는다
    (pooled_rpc가 상위 prior로 양수 상한을 만들 수 있어도 핫셋 게이트가 실무 방어)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _servo_shopping_unit(db, settle_clk=5, settle_cost=100, settle_conv=300)  # clk<10 → 핫셋 제외
    curve = _servo_curve(avg_rank=4.9)
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid",
                       return_value={"source": "group", "effective_bid": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    mock_exec.assert_not_called()
    assert result["reviewed"] == 0  # 핫셋 밖 → 심사 자체 없음
    assert result["servo"] == 0


def test_bid_up_servo_not_in_daily_lane_types():
    """서보 타입은 시간당 레인 inline 전용 — 일 레인 pending 재처리 경로에 안 태운다(codex P2)."""
    assert "bid_up_servo" not in auto_operator._DAILY_LANE_PROPOSAL_TYPES


def test_hourly_lane_servo_real_execute_exceeds_15pct_passes_guardrail(db):
    """실집행 배선(D-NAO-68) + ±15% 면제 실효: 서보 콜드 스텝을 +30%로 튜닝해 서보가 1300원을
    산정 → 실제 execute(dry_run=False)가 real guardrail_gate.check(면제)를 통과해
    update_adgroup_bid로 실쓰기. 대조 bid_up(+30%)은 guardrail 단위 테스트에서 차단됨."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _servo_shopping_unit(db)  # 경제성 상한 1500 ≥ 1300
    curve = _servo_curve(avg_rank=4.9)
    write_result = auto_operator.naver_sa_writer.WriteResult(
        action="update_adgroup_bid", before={"bidAmt": 1000}, response={"bidAmt": 1300},
        after={"bidAmt": 1300}, created_ids=[],
    )
    # execute가 넘겨받을 guardrail 컨텍스트(완전·통과값) — 서보 스텝만 real guardrail로 검증.
    clean_ctx = {
        "current_bid": 1000, "current_budget": None, "roas_corrected": 3.0, "target_roas": 2.0,
        "cost_today": 0, "daily_budget": 100_000, "unconverted_spend": 0,
        "last_change_at": None, "changes_today_count": 0,
    }
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid",
                       return_value={"source": "group", "effective_bid": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator.rank_servo, "_SERVO_DEFAULT_STEP_PCT", Decimal("0.30")), \
         patch.object(auto_operator.naver_execution_harness, "_build_guardrail_context", return_value=clean_ctx), \
         patch.object(auto_operator.naver_execution_harness, "_RANK_STEP_MAX_AGE_MINUTES", 10**9), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer, "update_adgroup_bid",
                       return_value=write_result) as mock_write:
        # ★신선도 게이트(P1-1) 무력화: 이 테스트는 면제/실쓰기 검증용 — created_at은 실시간
        #   UTC인데 now는 합성(_SERVO_NOW)이라 age 비교가 실clock에 종속되므로 상한을 크게 둔다.
        #   신선도 게이트 자체는 test_execute_servo_freshness_* 에서 created_at 제어로 별도 검증.
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    assert result["servo"] == 1 and result["executed"] == 1
    mock_write.assert_called_once_with("grp-shop", 1300)  # +30% 실쓰기(±15% 면제 실효)
    log_row = db.query(NaverChangeLog).filter(
        NaverChangeLog.action == "update_bid", NaverChangeLog.dry_run.is_(False),
        NaverChangeLog.after_value.isnot(None),
    ).order_by(NaverChangeLog.id.desc()).first()
    assert log_row is not None and json.loads(log_row.after_value) == {"bidAmt": 1300}


# ── P1-1(c) 신선도 게이트: stale 서보 제안(콘솔 재실행/재시도) fail-closed, 신선분 통과 ──
def test_execute_servo_freshness_stale_fails_closed(db):
    """생성 후 _RANK_STEP_MAX_AGE_MINUTES(10분) 초과 서보 제안 execute → fail-closed 종결
    (경제성 상한·pace 재검증 없이 면제만 적용되는 stale 재실행 봉쇄, P1-1)."""
    _settings(db)
    now = datetime(2026, 7, 20, 12, 20, 0)  # KST
    # created_at은 UTC 저장 → +9h가 KST. age 1h(>10분) 되게 created_at = now(KST)−9h−1h.
    stale_utc = now - timedelta(hours=9) - timedelta(hours=1)
    p = _proposal(db, proposal_type="bid_up_servo", target_type="adgroup", target_id="grp-1",
                   target_bid=1150, status="approved", created_at=stale_utc)
    with patch.object(auto_operator.naver_execution_harness.naver_sa_writer, "update_adgroup_bid") as mock_write:
        with pytest.raises(auto_operator.naver_execution_harness.MissingExecutionTargetError):
            auto_operator.naver_execution_harness.execute(db, p.id, dry_run=False, now=now)
    mock_write.assert_not_called()  # writer 미호출(fail-closed)
    db.refresh(p)
    assert p.status == "failed"
    log_row = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).first()
    assert log_row is not None and "신선도" in log_row.rationale and log_row.outcome == "failed"


def test_execute_servo_freshness_fresh_passes(db):
    """방금 생성(age≈0)한 서보 제안은 신선도 게이트 통과 → 실쓰기 도달(guardrail은 mock)."""
    _settings(db)
    now = datetime(2026, 7, 20, 12, 20, 0)
    fresh_utc = now - timedelta(hours=9)  # created_at_kst ≈ now → age≈0
    p = _proposal(db, proposal_type="bid_up_servo", target_type="adgroup", target_id="grp-1",
                   target_bid=1150, status="approved", created_at=fresh_utc)
    # codex R2 P1 이후 rank-step은 base_bid 마커 필수(부재=fail-closed) — 정상 인라인 생성분과
    # 동형으로 마커를 심는다(base=라이브 current 1000 → TOCTOU 일치).
    p.expected_effect = bid_step_types.encode_base_bid(p.expected_effect, 1000)
    db.commit()
    clean_ctx = {
        "current_bid": 1000, "current_budget": None, "roas_corrected": 3.0, "target_roas": 2.0,
        "cost_today": 0, "daily_budget": 100_000, "unconverted_spend": 0,
        "last_change_at": None, "changes_today_count": 0,
    }
    write_result = auto_operator.naver_sa_writer.WriteResult(
        action="update_adgroup_bid", before={"bidAmt": 1000}, response={"bidAmt": 1150},
        after={"bidAmt": 1150}, created_ids=[],
    )
    with patch.object(auto_operator.naver_execution_harness, "_build_guardrail_context", return_value=clean_ctx), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer, "update_adgroup_bid",
                       return_value=write_result) as mock_write:
        log_entry = auto_operator.naver_execution_harness.execute(db, p.id, dry_run=False, now=now)
    mock_write.assert_called_once_with("grp-1", 1150)
    assert json.loads(log_entry.after_value) == {"bidAmt": 1150}


# ── P1-2 예산 pace 관측 슬롯 분모: 자정 직후 hold·관측 슬롯만 나눔 ──
def test_servo_budget_pace_midnight_holds_no_observed_slot(db):
    """now.hour==0(완료 창 [−3,0)에 그날 버킷 없음) → observed=0 → fail-closed hold(자정 직후)."""
    _settings(db, seed_snapshot=False)
    db.add(NaverHourlySnapshot(
        snapshot_at=datetime(2026, 7, 20, 0, 0, 0), ad_date=TODAY, snapshot_hour=0,
        campaign_id=CAMPAIGN, campaign_type="SHOPPING", cost=0, clk=0, imp=0, daily_budget=100_000,
    ))
    db.commit()
    now = datetime(2026, 7, 20, 0, 20, 0)  # hour0 → 완료 창 [−3,0) 비어 있음
    curve = [_hour(0, imp=10, clk=1, cost=50, avg_rank=4.9)]  # 진행중 hour0(창 밖) — observed 0
    ok, reason = auto_operator._servo_budget_pace_ok(
        db, campaign_id=CAMPAIGN, curve=curve, now=now, target_bid=1150,
    )
    assert ok is False and "관측 0" in reason


def test_servo_budget_pace_divides_by_observed_not_fixed_three(db):
    """관측 슬롯 2개(imp>0)만 있으면 pace=clk합÷2(고정 3 아님) — 과소추정 방지(P1-2)."""
    _settings(db, seed_snapshot=False)
    db.add(NaverHourlySnapshot(
        snapshot_at=_SERVO_NOW, ad_date=TODAY, snapshot_hour=11, campaign_id=CAMPAIGN,
        campaign_type="SHOPPING", cost=0, clk=0, imp=0, daily_budget=100_000,
    ))  # snapshot_hour<=now.hour(12) — P2-2 필터 통과
    db.commit()
    now = datetime(2026, 7, 20, 12, 20, 0)
    # 완료 창 [9,12): 두 슬롯만 존재(imp>0), 각 clk=6 → clk합 12. observed=2 → pace=6/슬롯.
    # 예상지출 = 6 × (24−12) × 1150 = 82,800 > 잔여 100,000×0.8=80,000 → 차단.
    # 고정 3으로 나눴다면 pace=4 → 4×12×1150=55,200 ≤ 80,000 → 통과(과대 허용). 차등 봉인.
    curve = [
        _hour(10, imp=15, clk=6, cost=100, avg_rank=4.9),
        _hour(11, imp=15, clk=6, cost=100, avg_rank=4.9),
    ]
    ok, reason = auto_operator._servo_budget_pace_ok(
        db, campaign_id=CAMPAIGN, curve=curve, now=now, target_bid=1150,
    )
    assert ok is False and "관측 2슬롯" in reason


def test_servo_budget_pace_ignores_future_snapshot(db):
    """P2-2: 같은 ad_date 내 snapshot_hour>now.hour(미래) 스냅샷은 배제 — 과거 최신만 사용."""
    _settings(db, seed_snapshot=False)
    now = datetime(2026, 7, 20, 12, 20, 0)
    # 미래(23시) 스냅샷 = daily_budget 큼(잘못 쓰면 통과) / 현재(12시) 스냅샷 = 잔여 적음(차단).
    db.add(NaverHourlySnapshot(
        snapshot_at=now, ad_date=TODAY, snapshot_hour=12, campaign_id=CAMPAIGN,
        campaign_type="SHOPPING", cost=0, clk=0, imp=0, daily_budget=1000,
    ))
    db.add(NaverHourlySnapshot(
        snapshot_at=now, ad_date=TODAY, snapshot_hour=23, campaign_id=CAMPAIGN,
        campaign_type="SHOPPING", cost=0, clk=0, imp=0, daily_budget=10_000_000,
    ))
    db.commit()
    curve = _servo_curve(avg_rank=4.9)  # 9,10,11 각 clk=2
    ok, reason = auto_operator._servo_budget_pace_ok(
        db, campaign_id=CAMPAIGN, curve=curve, now=now, target_bid=1150,
    )
    # 12시 스냅샷(잔여 1000) 사용 → 예상지출 크게 초과 → 차단(미래 23시 스냅샷 무시 증명).
    assert ok is False and "pace 초과" in reason


# ══════════════════════════════════════════════════════════════════════════════
# IU-R R2(D-NAO-67 원리③) 파워링크 estimate 직행 — 동적 목표순위·min(경제성 상한, rank_bid)·
# ±15% 면제·fail-closed(estimate 이상값 5종·최상단)·TOCTOU·estimate 캡/캐시/prefilter·다운스트림.
# ══════════════════════════════════════════════════════════════════════════════


def _estimate_ws_unit(db, *, keyword_id="nkw-est", settle_clk=20, settle_cost=7000,
                      settle_conv=60000, snap_daily_budget=100000, snap_cost=0):
    """파워링크(WEB_SITE) 키워드 estimate 직행 대상 시드 — 정착창 ROAS ok(UP 발동)·핫셋 자격
    (clk≥10)·rpc≈3000→경제성 상한≈1500·hour12 스냅샷(pace 소스, snapshot_hour<=now.hour)."""
    db.add(NaverEntity(entity_type="keyword", entity_id=keyword_id, parent_id="grp-1",
                        campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    window_from, _ = _settlement_window()
    _ad_row(db, keyword_id=keyword_id, adgroup_id="grp-1", ad_date=window_from,
            clk=settle_clk, cost=settle_cost, conv_direct_amt=settle_conv)
    db.add(NaverHourlySnapshot(
        snapshot_at=_SERVO_NOW, ad_date=TODAY, snapshot_hour=12, campaign_id=CAMPAIGN,
        campaign_type="WEB_SITE", cost=snap_cost, clk=0, imp=0, daily_budget=snap_daily_budget,
    ))
    db.commit()


def _est(kw, bid, position=4):
    return [{"nccKeywordId": kw, "position": position, "bid": bid}]


# ── (A) _estimate_target_position 순수: 동적 목표(고정 2 아님)·1~4 clamp·최상단/None hold ──
@pytest.mark.parametrize("wr,expected", [
    (4.9, 4), (5.0, 4), (3.2, 3), (2.4, 2),  # ceil−1 동적(관측 3.2→ceil4−1=3, 2.4→ceil3−1=2)
    (9.0, 4),  # clamp 상한 4
    (1.31, 1),  # 1+deadband(0.3) 초과 → position 1 요청 허용
    (1.3, None), (1.2, None),  # ≤1+deadband → 최상단 converged hold(position 1 요청 차단)
    (None, None),  # weighted_rank None → fail-closed hold
])
def test_estimate_target_position_dynamic_and_clamp(wr, expected):
    assert auto_operator._estimate_target_position(wr) == expected


# ── (B) _fetch_estimate_rank_bid 캐시·회당 캡: 호출 수 봉인 ──
def test_fetch_estimate_cache_reuses_single_api_call():
    cache, counter = {}, {"n": 0}
    with patch.object(auto_operator, "estimate_average_position_bid",
                       return_value=_est("kw-1", 1200)) as mock_est:
        b1, _ = auto_operator._fetch_estimate_rank_bid("kw-1", 4, cache=cache, counter=counter)
        b2, note2 = auto_operator._fetch_estimate_rank_bid("kw-1", 4, cache=cache, counter=counter)
    assert b1 == 1200 and b2 == 1200
    assert mock_est.call_count == 1  # 두 번째는 런 캐시 재사용(API 호출 없음)
    assert counter["n"] == 1 and "캐시" in note2


def test_fetch_estimate_budget_cap_blocks_call():
    cache, counter = {}, {"n": auto_operator._RUN_ESTIMATE_BUDGET}  # 이미 캡 도달
    with patch.object(auto_operator, "estimate_average_position_bid") as mock_est:
        rank_bid, note = auto_operator._fetch_estimate_rank_bid("kw-x", 3, cache=cache, counter=counter)
    assert rank_bid is None and "캡" in note
    mock_est.assert_not_called()  # 캡 도달 시 API 호출 자체가 없음


# ── (C) run_hourly_lane estimate 직행: min(경제성 상한, rank_bid) 양방향 ──
def test_estimate_min_ceiling_binds_when_estimate_high(db):
    """estimate 2000 > 경제성 상한 1500 → target=1500(상한까지만, D-NAO-19)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _estimate_ws_unit(db)
    curve = _servo_curve(avg_rank=4.9)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator, "estimate_average_position_bid", return_value=_est("nkw-est", 2000)), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    mock_exec.assert_called_once()
    assert result["rank_direct"] == 1
    saved = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert saved.proposal_type == "bid_up_rank"
    assert saved.target_bid == 1500  # min(경제성 상한 1500, estimate 2000)


def test_estimate_rank_bid_binds_when_below_ceiling(db):
    """estimate 1200 < 경제성 상한 1500 → target=1200(estimate가 상한)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _estimate_ws_unit(db)
    curve = _servo_curve(avg_rank=4.9)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator, "estimate_average_position_bid", return_value=_est("nkw-est", 1200)), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    mock_exec.assert_called_once()
    saved = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert saved.target_bid == 1200


# ── (D) fail-closed: estimate 이상값 5종 + 최상단 각각 hold(execute 없음) ──
def _run_estimate_holds(db, *, estimate_return=None, estimate_side_effect=None, avg_rank=4.9,
                        current_bid=1000):
    _settings(db, target_roas_override=Decimal("2.0"))
    _estimate_ws_unit(db)
    curve = _servo_curve(avg_rank=avg_rank)
    est_kwargs = {}
    if estimate_side_effect is not None:
        est_kwargs["side_effect"] = estimate_side_effect
    else:
        est_kwargs["return_value"] = estimate_return
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": current_bid}), \
         patch.object(auto_operator, "estimate_average_position_bid", **est_kwargs), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    mock_exec.assert_not_called()
    assert result["rank_direct"] == 0
    return result


def test_estimate_fail_closed_api_exception(db):
    result = _run_estimate_holds(db, estimate_side_effect=RuntimeError("HTTP 500"))
    assert any("[순위직행]" in h["reason"] and "estimate 호출 실패" in h["reason"] for h in result["held"])


def test_estimate_fail_closed_rank_bid_missing(db):
    result = _run_estimate_holds(db, estimate_return=[])  # nccKeywordId 매칭 없음 → None
    assert any("이상값" in h["reason"] and "rank_bid=None" in h["reason"] for h in result["held"])


def test_estimate_fail_closed_bid_key_absent_no_crash(db):
    """GATE R2 P1 봉인 — 네이버가 매칭 행을 bid 키 없이 반환해도(산정 불가 키워드) KeyError로
    레인이 죽지 않고 rank_bid=None → 이상값 fail-closed hold로 흡수된다."""
    result = _run_estimate_holds(db, estimate_return=[{"nccKeywordId": "nkw-est", "position": 4}])
    assert any("이상값" in h["reason"] and "rank_bid=None" in h["reason"] for h in result["held"])


def test_estimate_fail_closed_rank_bid_zero(db):
    result = _run_estimate_holds(db, estimate_return=_est("nkw-est", 0))
    assert any("이상값" in h["reason"] and "rank_bid=0" in h["reason"] for h in result["held"])


def test_estimate_fail_closed_not_ten_multiple(db):
    result = _run_estimate_holds(db, estimate_return=_est("nkw-est", 1205))
    assert any("이상값" in h["reason"] and "1205" in h["reason"] for h in result["held"])


def test_estimate_fail_closed_out_of_range(db):
    result = _run_estimate_holds(db, estimate_return=_est("nkw-est", 100010))  # >100,000
    assert any("이상값" in h["reason"] for h in result["held"])


def test_estimate_fail_closed_at_or_below_current(db):
    # estimate 900 ≤ 현재 1000 → 유효 스텝 없음 hold(순위 근거로도 현재 이하는 스텝 아님).
    result = _run_estimate_holds(db, estimate_return=_est("nkw-est", 900), current_bid=1000)
    assert any("유효 스텝 없음" in h["reason"] for h in result["held"])


def test_estimate_top_of_page_hold_no_api_call(db):
    """최상단(관측 1.2위≤1+deadband) → converged hold, estimate 호출 자체가 없다(position 1 차단)."""
    _settings(db, target_roas_override=Decimal("2.0"))
    _estimate_ws_unit(db)
    curve = _servo_curve(avg_rank=1.2)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator, "estimate_average_position_bid") as mock_est, \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    mock_exec.assert_not_called()
    mock_est.assert_not_called()  # 최상단은 estimate 호출 전에 hold
    assert any("[순위직행]" in h["reason"] and "최상단" in h["reason"] for h in result["held"])


# ── (E) prefilter: 쿨다운/일일캡 걸린 유닛은 estimate 호출 자체가 없음(호출 수로 봉인) ──
def test_estimate_prefilter_cooldown_skips_estimate_call(db):
    _settings(db, target_roas_override=Decimal("2.0"))
    _estimate_ws_unit(db)
    db.add(NaverChangeLog(  # 1시간 전 실쓰기 → 쿨다운 2h 이내
        entity_type="keyword", entity_id="nkw-est", campaign_id=CAMPAIGN, action="update_bid",
        dry_run=False, after_value=json.dumps({"bidAmt": 1000}), changed_at=_SERVO_NOW - timedelta(hours=1),
    ))
    db.commit()
    curve = _servo_curve(avg_rank=4.9)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator, "estimate_average_position_bid") as mock_est, \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    mock_est.assert_not_called()  # 쿨다운 prefilter → estimate 호출 절약(R1 GATE P2-2)
    mock_exec.assert_not_called()
    assert any("[순위직행]" in h["reason"] and "prefilter" in h["reason"] and "쿨다운" in h["reason"] for h in result["held"])


def test_estimate_prefilter_daily_cap_skips_estimate_call(db):
    _settings(db, target_roas_override=Decimal("2.0"))
    _estimate_ws_unit(db)
    for i in range(3):  # 오늘 3회 실쓰기 → 일일상한 3 도달
        db.add(NaverChangeLog(
            entity_type="keyword", entity_id="nkw-est", campaign_id=CAMPAIGN, action="update_bid",
            dry_run=False, after_value=json.dumps({"bidAmt": 1000}),
            changed_at=datetime(2026, 7, 20, 3 + i, 0, 0),  # 오늘·쿨다운 밖(now 12시)
        ))
    db.commit()
    curve = _servo_curve(avg_rank=4.9)
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator, "estimate_average_position_bid") as mock_est, \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    mock_est.assert_not_called()  # 일일캡 prefilter → estimate 호출 절약
    assert any("[순위직행]" in h["reason"] and "일일" in h["reason"] for h in result["held"])


# ── (F) 실집행(D-NAO-68) + ±15% 면제 실효: estimate 1300(+30%) 실쓰기 통과 ──
def test_estimate_real_execute_exceeds_15pct_passes_guardrail(db):
    _settings(db, target_roas_override=Decimal("2.0"))
    _estimate_ws_unit(db)  # 경제성 상한 1500 ≥ 1300
    curve = _servo_curve(avg_rank=4.9)
    write_result = auto_operator.naver_sa_writer.WriteResult(
        action="update_keyword_bid", before={"bidAmt": 1000, "userLock": False},
        response={"bidAmt": 1300}, after={"bidAmt": 1300, "userLock": False}, created_ids=[],
    )
    clean_ctx = {
        "current_bid": 1000, "current_budget": None, "roas_corrected": 3.0, "target_roas": 2.0,
        "cost_today": 0, "daily_budget": 100_000, "unconverted_spend": 0,
        "last_change_at": None, "changes_today_count": 0,
    }
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator, "estimate_average_position_bid", return_value=_est("nkw-est", 1300)), \
         patch.object(auto_operator.diagnosis, "correction_factor", return_value=_SERVO_CORR), \
         patch.object(auto_operator.naver_execution_harness, "_build_guardrail_context", return_value=clean_ctx), \
         patch.object(auto_operator.naver_execution_harness, "_RANK_STEP_MAX_AGE_MINUTES", 10**9), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer, "update_keyword_bid",
                       return_value=write_result) as mock_write:
        result = auto_operator.run_hourly_lane(db, now=_SERVO_NOW, fetch_intraday=lambda tid, d: curve)
    assert result["rank_direct"] == 1 and result["executed"] == 1
    mock_write.assert_called_once_with("nkw-est", 1300)  # +30% 실쓰기(±15% 면제 실효)


# ── (G) TOCTOU: 제안 시점 base ≠ 실행 시점 라이브 bid → failed(stale)·writer 미호출 ──
def _rank_proposal(db, *, base_bid, target_bid=1300, now, proposal_type="bid_up_rank",
                   with_marker=True):
    effect = "파워링크 estimate 직행"
    if with_marker:
        effect = auto_operator.encode_base_bid(effect, base_bid)
    p = NaverProposal(
        proposal_type=proposal_type, target_type="keyword", target_id="nkw-t",
        campaign_id=CAMPAIGN, rationale="[순위직행] x", expected_effect=effect,
        status="approved", target_bid=target_bid,
    )
    db.add(p); db.commit()
    fresh_utc = now - timedelta(hours=9)  # age≈0 → 신선도 게이트 통과
    db.query(NaverProposal).filter(NaverProposal.id == p.id).update({"created_at": fresh_utc})
    db.commit(); db.refresh(p)
    return p


def test_execute_rank_toctou_mismatch_fails_stale(db):
    _settings(db)
    now = datetime(2026, 7, 20, 12, 20, 0)
    p = _rank_proposal(db, base_bid=1000, now=now)  # 제안 시점 base 1000
    live_ctx = {  # 실행 시점 라이브 1100 ≠ 1000 → TOCTOU 중단
        "current_bid": 1100, "current_budget": None, "roas_corrected": 3.0, "target_roas": 2.0,
        "cost_today": 0, "daily_budget": 100_000, "unconverted_spend": 0,
        "last_change_at": None, "changes_today_count": 0,
    }
    with patch.object(auto_operator.naver_execution_harness, "_build_guardrail_context", return_value=live_ctx), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        with pytest.raises(auto_operator.naver_execution_harness.MissingExecutionTargetError):
            auto_operator.naver_execution_harness.execute(db, p.id, dry_run=False, now=now)
    mock_write.assert_not_called()  # 재산정 없이 중단(초크포인트 순수성)
    db.refresh(p)
    assert p.status == "failed"
    log_row = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).first()
    assert log_row is not None and "TOCTOU" in log_row.rationale and log_row.outcome == "failed"


def test_execute_rank_toctou_match_proceeds(db):
    _settings(db)
    now = datetime(2026, 7, 20, 12, 20, 0)
    p = _rank_proposal(db, base_bid=1000, target_bid=1300, now=now)
    match_ctx = {  # 라이브 1000 == base 1000 → 통과
        "current_bid": 1000, "current_budget": None, "roas_corrected": 3.0, "target_roas": 2.0,
        "cost_today": 0, "daily_budget": 100_000, "unconverted_spend": 0,
        "last_change_at": None, "changes_today_count": 0,
    }
    write_result = auto_operator.naver_sa_writer.WriteResult(
        action="update_keyword_bid", before={"bidAmt": 1000, "userLock": False},
        response={"bidAmt": 1300}, after={"bidAmt": 1300, "userLock": False}, created_ids=[],
    )
    with patch.object(auto_operator.naver_execution_harness, "_build_guardrail_context", return_value=match_ctx), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer, "update_keyword_bid",
                       return_value=write_result) as mock_write:
        auto_operator.naver_execution_harness.execute(db, p.id, dry_run=False, now=now)
    mock_write.assert_called_once_with("nkw-t", 1300)


def test_execute_rank_no_marker_fails_closed(db):
    """마커 부재 = fail-closed(codex R2 P1 — 뒤집힘): rank-step은 ±15% 면제 타입이라 산정 base
    검증 없이 실행 금지. run_hourly_lane 밖 생성/변조 제안이 면제만 업고 실행되는 경로 차단."""
    _settings(db)
    now = datetime(2026, 7, 20, 12, 20, 0)
    p = _rank_proposal(db, base_bid=1000, target_bid=1300, now=now, with_marker=False)
    ctx = {  # 라이브 1100 ≠ (없는) base — 마커 없으니 TOCTOU 건너뜀
        "current_bid": 1100, "current_budget": None, "roas_corrected": 3.0, "target_roas": 2.0,
        "cost_today": 0, "daily_budget": 100_000, "unconverted_spend": 0,
        "last_change_at": None, "changes_today_count": 0,
    }
    write_result = auto_operator.naver_sa_writer.WriteResult(
        action="update_keyword_bid", before={"bidAmt": 1100}, response={"bidAmt": 1300},
        after={"bidAmt": 1300}, created_ids=[],
    )
    with patch.object(auto_operator.naver_execution_harness, "_build_guardrail_context", return_value=ctx), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer, "update_keyword_bid",
                       return_value=write_result) as mock_write:
        with pytest.raises(auto_operator.naver_execution_harness.MissingExecutionTargetError,
                           match="마커 부재"):
            auto_operator.naver_execution_harness.execute(db, p.id, dry_run=False, now=now)
    mock_write.assert_not_called()  # 마커 없으면 writer 미도달(fail-closed)
    db.refresh(p)
    assert p.status == "failed"


# ── (H) 다운스트림 정합: bid_up_rank는 일 레인 재처리 제외(inline 전용) ──
def test_bid_up_rank_not_in_daily_lane_types():
    assert "bid_up_rank" not in auto_operator._DAILY_LANE_PROPOSAL_TYPES


def test_bid_up_rank_excluded_from_delegation():
    from app.services.naver_ad import delegation_gate
    assert "bid_up_rank" not in delegation_gate.delegable_types()  # rank-step 위임 영구 제외(inline)


# ═════════════ 학습밴드 vs 하드코딩 2.5 충돌 해소 (2026-07-28) ═════════════
# 상수 2.5가 학습된 최적밴드(1.0-2.0)를 이겨서 탐침이 자기 최적점에 도달하지 못하고,
# 그것을 막으려던 CD5 게이트도 2.5>2.0이라 영원히 발동하지 않던 구조를 해소한다.

def test_probe_rank_floor_lowers_only_for_learned_band_below_prior():
    """학습밴드 상한이 2.5보다 좋을 때만 하한을 낮춘다 — 그 외는 전부 종전 그대로."""
    from decimal import Decimal as D
    assert auto_operator._probe_rank_floor("1.0-2.0") == D("2")   # ★유일하게 값이 바뀌는 경우
    assert auto_operator._probe_rank_floor("2.0-2.5") is None     # band_high==2.5 = 프라이어와 동일
    assert auto_operator._probe_rank_floor("2.5-3.0") is None     # 느슨한 쪽으로 완화하지 않는다
    assert auto_operator._probe_rank_floor("3.0-4.0") is None
    assert auto_operator._probe_rank_floor("4.0+") is None        # 개방 밴드 = CD5가 담당(종전 경로)
    assert auto_operator._probe_rank_floor(None) is None          # 학습값 없음 = 프라이어 유지


def test_probe_rank_floor_unknown_label_falls_back_to_prior():
    """알 수 없는 라벨은 예외를 밖으로 던지지 않고 프라이어로 폴백(조용한 오판정 방지)."""
    assert auto_operator._probe_rank_floor("존재하지-않는-밴드") is None


def test_probe_trigger_reaches_learned_optimum_when_floor_lowered():
    """★핵심 회귀: 학습밴드 1.0-2.0이면 rank 2.0~2.5 구간에서도 탐침이 발동한다.
    이 구간이 막혀 있어서 탐침이 학습 최적점(2.0)에 구조적으로 도달할 수 없었다."""
    from decimal import Decimal as D
    now = datetime(2026, 7, 20, 8, 20, 0)
    curve = [_hour(6, imp=20, clk=0, cost=200, avg_rank=2.2),
             _hour(7, imp=20, clk=0, cost=200, avg_rank=2.2)]
    # 종전(하한 2.5): 미발동
    assert auto_operator._probe_trigger(curve, now)[0] is False
    # 학습밴드 1.0-2.0 반영(하한 2.0): 발동
    fired, reason = auto_operator._probe_trigger(
        curve, now, rank_floor=auto_operator._probe_rank_floor("1.0-2.0"),
    )
    assert fired is True
    assert "학습밴드" in reason  # 근거가 하드코딩이 아니라 학습값임이 사유에 남는다
    # 학습 최적점 안쪽(2.0 미만)까지 오면 다시 멈춘다 — 무한 상승 아님
    inner = [_hour(6, imp=20, clk=0, cost=200, avg_rank=1.5),
             _hour(7, imp=20, clk=0, cost=200, avg_rank=1.5)]
    assert auto_operator._probe_trigger(inner, now, rank_floor=D("2"))[0] is False


def test_probe_trigger_default_floor_unchanged_without_learned_band():
    """학습밴드가 없으면 종전과 완전히 동일(rank 2.2는 미발동, 3.0은 발동)."""
    now = datetime(2026, 7, 20, 8, 20, 0)
    below = [_hour(6, imp=20, clk=0, cost=200, avg_rank=2.2),
             _hour(7, imp=20, clk=0, cost=200, avg_rank=2.2)]
    above = [_hour(6, imp=20, clk=0, cost=200, avg_rank=3.0),
             _hour(7, imp=20, clk=0, cost=200, avg_rank=3.0)]
    assert auto_operator._probe_trigger(below, now, rank_floor=None)[0] is False
    assert auto_operator._probe_trigger(above, now, rank_floor=None)[0] is True


def test_learned_band_of_swallows_lookup_failure(monkeypatch):
    """학습밴드 조회 실패가 시간당 레인 전체를 막지 않는다(None 폴백)."""
    from app.services.naver_ad import probe_learning_loop

    def _boom(*a, **kw):
        raise RuntimeError("aggregate 실패")

    monkeypatch.setattr(probe_learning_loop, "learned_probe_rank", _boom)
    assert auto_operator._learned_band_of(
        None, datetime(2026, 7, 20, 8, 20, 0), "cmp-x",
    ) is None


# ═══════ 학습밴드 스코프 불일치 해소 (2026-07-29, Jino 확정) ═══════
# 09:03 학습 잡은 계정 전체 밴드를 승격시키는데 게이트는 캠페인별만 읽어서,
# 학습해놓고 아무도 안 읽는 상태였다. 자기 밴드가 없을 때만 계정 밴드를 빌리되
# **사후 고삐(RL3)가 발동 가능한 유닛(BEP 확인)에만** 연다.

def test_account_band_fallback_requires_bep(monkeypatch):
    """BEP 미확인 유닛은 폴백을 안 쓴다 — 사후 고삐가 fail-closed로 침묵하기 때문."""
    from app.services.naver_ad import intraday_roas

    monkeypatch.setattr(auto_operator, "_resolve_adgroup_id", lambda *a, **kw: "grp-1")
    monkeypatch.setattr(intraday_roas, "adgroup_unit_price",
                        lambda db, aid: {"price": None, "bep_roas": None})
    ok, why = auto_operator._account_band_fallback_ok(None, "keyword", "kw-1")
    assert ok is False
    assert "BEP 미확인" in why

    monkeypatch.setattr(intraday_roas, "adgroup_unit_price",
                        lambda db, aid: {"price": 16800, "bep_roas": Decimal("2.03")})
    ok, why = auto_operator._account_band_fallback_ok(None, "keyword", "kw-1")
    assert ok is True


def test_account_band_fallback_blocked_when_adgroup_unresolved(monkeypatch):
    """adgroup 해석 실패도 fail-closed — 근거 없이 빌린 값으로 올리지 않는다."""
    monkeypatch.setattr(auto_operator, "_resolve_adgroup_id", lambda *a, **kw: None)
    ok, _ = auto_operator._account_band_fallback_ok(None, "keyword", "kw-1")
    assert ok is False


def test_account_band_fallback_swallows_lookup_error(monkeypatch):
    """BEP 조회가 터져도 레인을 막지 않고 폴백만 보류한다."""
    from app.services.naver_ad import intraday_roas

    def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(auto_operator, "_resolve_adgroup_id", lambda *a, **kw: "grp-1")
    monkeypatch.setattr(intraday_roas, "adgroup_unit_price", _boom)
    ok, why = auto_operator._account_band_fallback_ok(None, "keyword", "kw-1")
    assert ok is False and "조회 실패" in why


def test_learned_bands_of_returns_own_and_account(monkeypatch):
    """(자기 밴드, 계정 밴드) 둘 다 반환 — 호출부가 조건부로 고를 수 있게."""
    calls = []

    def _fake(db, now, campaign_id):
        calls.append(campaign_id)
        return "1.0-2.0" if campaign_id is None else None

    monkeypatch.setattr(auto_operator, "_learned_band_of", _fake)
    own, acct = auto_operator._learned_bands_of(None, datetime(2026, 7, 29, 10, 0), "cmp-x")
    assert own is None and acct == "1.0-2.0"
    assert calls == ["cmp-x", None]  # 자기 → 계정 순서


def test_probe_floor_uses_account_band_only_via_fallback():
    """★핵심: 계정 밴드가 1.0-2.0이어도 그것을 '쓰기로 결정'해야만 하한이 내려간다.
    폴백이 막히면(BEP 미확인) 하한은 종전 프라이어 2.5 그대로다."""
    from decimal import Decimal as D
    now = datetime(2026, 7, 20, 8, 20, 0)
    curve = [_hour(6, imp=20, clk=0, cost=200, avg_rank=2.2),
             _hour(7, imp=20, clk=0, cost=200, avg_rank=2.2)]
    # 폴백 차단 → band=None → 하한 2.5 → 미발동(종전과 동일)
    assert auto_operator._probe_trigger(
        curve, now, rank_floor=auto_operator._probe_rank_floor(None))[0] is False
    # 폴백 허용 → band='1.0-2.0' → 하한 2.0 → 발동
    fired, reason = auto_operator._probe_trigger(
        curve, now, rank_floor=auto_operator._probe_rank_floor("1.0-2.0"))
    assert fired is True and "학습밴드" in reason
