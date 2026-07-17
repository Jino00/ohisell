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
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverHourlySnapshot,
    NaverProposal,
    NaverRetroSignal,
)
from app.services.naver_ad import auto_operator

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
    seed_snapshot: 당일 NaverHourlySnapshot(cost=0, snapshot_hour=23) — codex 2R[P1-2]
      부재 fail-closed와 codex 3R[P1-1] 신선도(snapshot_hour >= now.hour-1, 23이면 어떤
      now에도 신선)를 기본 통과(브레이커/신선도 자체를 검증하는 테스트는 False로 끄고
      직접 시드)."""
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
            campaign_type="", cost=0, clk=0, imp=0,
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
    assert result == {"reviewed": 0, "approved": 0, "executed": 0, "held": [], "failed": 0}


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


def test_daily_lane_ignores_stale_pending_created_yesterday(db):
    _settings(db)
    _proposal(db, proposal_type="bid_down", created_at=DAY_START_UTC - timedelta(hours=1))
    result = auto_operator.run_daily_lane(db, now=NOW)
    assert result["reviewed"] == 0


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
    assert p.status == "pending"


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
    assert p.status == "pending"  # harness에 안 넘어가 failed로 종결되지 않음


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
    assert p.status == "pending"


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
    assert p.status == "pending"


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
    assert result["held"][0]["reason"] == "판정 조건 미충족(기본 hold)"


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


# ══════════════════════════ 시간당 레인(A2+A3) ══════════════════════════

def _hour(h, *, imp, clk, cost, avg_rank=None):
    return {"hour": h, "imp": imp, "clk": clk, "cost": cost, "avg_rank": avg_rank}


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
    _settings(db, target_roas_override=Decimal("2.0"))
    window_from, window_to = _settlement_window()
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-up", parent_id="grp-1", campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    # roas_naver = 21000/7000 = 3.0 >= 2.0, baseline CPC = 7000/20 = 350원
    _ad_row(db, keyword_id="nkw-up", ad_date=window_from, clk=20, cost=7000, conv_direct_amt=21000)
    db.commit()

    up_curve = [
        _hour(10, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(11, imp=15, clk=2, cost=35, avg_rank=5.0),
        _hour(12, imp=15, clk=2, cost=30, avg_rank=5.0),
    ]  # imp=45, weighted_rank=5.0>4.0, today CPC=100/6≈16.7(급등 아님), 오늘소진100 ≪ 일평균1000
    now_midday = datetime(2026, 7, 20, 12, 20, 0)  # 선형기대=740/1440≈0.514 vs 실제0.1 → 저속
    with patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.diagnosis, "correction_factor",
                       return_value={"factor": Decimal("1"), "source": "actual_revenue_ratio"}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now_midday, fetch_intraday=lambda tid, d: up_curve,
        )
    mock_exec.assert_called_once()
    proposal_id = mock_exec.call_args[0][1]
    saved = db.get(NaverProposal, proposal_id)
    assert saved.proposal_type == "bid_up"
    assert saved.target_bid == 1150  # 1000×1.15 → 10원 내림 클램프(이미 배수)


def test_hourly_lane_up_not_fired_when_roas_condition_missing(db):
    """rank>4.0·페이싱저속은 충족하지만 정착창 실적 자체가 없어 ROAS 검증 불가 → hold
    (3조건 동시 충족 요구 — 2개만 만족해도 up 아님)."""
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
    assert result["held"][0]["reason"] == "판정 조건 미충족(기본 hold)"


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
    ]  # rank 2.5~4.0 사이(중립), today CPC=150/3=50원 < baseline100×2
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=NOW, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_not_called()
    assert result["held"][0]["reason"] == "판정 조건 미충족(기본 hold)"


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
        _hour(12, imp=100, clk=1, cost=50, avg_rank=1.0),  # 진행 중 부분 버킷 — 포함되면 down 오판
    ]
    with patch.object(auto_operator.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(
            db, now=now, fetch_intraday=lambda tid, d: curve,
        )
    mock_exec.assert_not_called()  # 완료 3개(9~11, 중립)로 판정 → hold
    assert result["held"][0]["reason"] == "판정 조건 미충족(기본 hold)"


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
