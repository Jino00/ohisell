# test_naver_vitality_lane.py — VT2 시간당 레인 vitality 스텝 + 브리핑 테스트 (D-NAO-81 B축).
# 커버(PLAN §2, GATE): ②가드레일 상속(우회 경로 없음 — execute/guardrail_gate 관통) ③무경보
#   무동작·무브리핑 ④캠페인 일일 캡·48h 쿨다운 ⑥기존 시간당 레인 회귀 0(vitality 키만 추가).
#   GATE ①(정지 개체 소생 0)·⑤(스코프 밖 0)는 SA 단(test_naver_vitality_signal.py)에서 고정.
# 실 API 0 — naver_execution_harness.execute·_live_current_bid·slack은 mock(가드레일 관통
#   1건만 최하단 writer까지 실제로 태운다).
from __future__ import annotations

from datetime import date, datetime, timedelta
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
    NaverProposal,
    OpsDiaryEntry,
)
from app.services.naver_ad import auto_operator, naver_sa_writer

CAMPAIGN = "cmp-03"
GROUP = "grp-03"
NOW = datetime(2026, 7, 19, 8, 20, 0)  # as_of=07-18(스파이럴 성립일)

_IMP = {
    date(2026, 7, 12): 2023, date(2026, 7, 13): 2103, date(2026, 7, 14): 1812,
    date(2026, 7, 15): 1559, date(2026, 7, 16): 924, date(2026, 7, 17): 1175,
    date(2026, 7, 18): 796,
}
_RANK = {
    date(2026, 7, 12): 3.0, date(2026, 7, 13): 3.0, date(2026, 7, 14): 3.0,
    date(2026, 7, 15): 3.2, date(2026, 7, 16): 3.5, date(2026, 7, 17): 3.9,
    date(2026, 7, 18): 4.7,
}


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


def _result() -> dict:
    return {"failed": 0, "vitality_alerts": 0, "vitality_fired": 0, "vitality_held": []}


def _seed_spiral(db, *, imp=None, auto_operate=True):
    imp = imp or _IMP
    db.add(NaverCampaignSettings(campaign_id=CAMPAIGN, auto_operate=auto_operate, optimizer="ours"))
    db.add(NaverEntity(entity_type="adgroup", entity_id=GROUP, parent_id=CAMPAIGN,
                       campaign_id=CAMPAIGN, status="on"))
    for d, v in imp.items():
        db.add(NaverAdDaily(
            ad_date=d, campaign_id=CAMPAIGN, campaign_type="SHOPPING", adgroup_id=GROUP,
            keyword_id="", imp=v, clk=20, cost=10000, rank_sum=round(_RANK[d] * v),
            conv_direct_cnt=2, conv_indirect_cnt=0, conv_direct_amt=50000, conv_indirect_amt=0,
        ))
    db.commit()


def _seed_fire_log(db, *, entity_id, changed_at, campaign_id=CAMPAIGN):
    db.add(NaverChangeLog(
        entity_type="adgroup", entity_id=entity_id, campaign_id=campaign_id, action="update_bid",
        rationale=f"{auto_operator.VITALITY_RATIONALE_PREFIX} 과거 발사", dry_run=False,
        after_value='{"bidAmt": 1150}', changed_at=changed_at, executed_at=changed_at,
    ))
    db.commit()


# ══════════════════════════ 발사(정상 경로) ══════════════════════════

def test_vitality_fires_revive_via_hourly_up_path(db):
    """경보 시 소생 그룹을 기존 시간당 밴드 UP 경로로 발사 — proposal_type=bid_up·adgroup·
    approval_source=APPROVAL_SOURCE_HOURLY·rationale [스파이럴복원]·target_bid=_clamp_step(up)."""
    _seed_spiral(db)
    result = _result()
    with patch.object(auto_operator, "_live_current_bid", return_value=1000), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec, \
         patch.object(auto_operator.slack_notifier, "notify_text", return_value={"sent": True}):
        auto_operator._run_vitality_step(db, NOW, result)

    assert result["vitality_alerts"] == 1
    assert result["vitality_fired"] == 1
    mock_exec.assert_called_once()
    p = db.query(NaverProposal).one()
    assert p.proposal_type == "bid_up" and p.target_type == "adgroup" and p.target_id == GROUP
    assert p.approval_source == auto_operator.APPROVAL_SOURCE_HOURLY
    assert p.target_bid == 1150  # 1000×1.15, 10원 내림
    assert p.rationale.startswith(auto_operator.VITALITY_RATIONALE_PREFIX)


def test_vitality_briefing_emitted_on_alert(db):
    """경보 있던 날 diary(observe·action=vitality_spiral_briefing)+Slack 발화(PX 관례 미러)."""
    _seed_spiral(db)
    with patch.object(auto_operator, "_live_current_bid", return_value=1000), \
         patch.object(auto_operator.naver_execution_harness, "execute"), \
         patch.object(auto_operator.slack_notifier, "notify_text", return_value={"sent": True}) as mock_slack:
        auto_operator._run_vitality_step(db, NOW, _result())

    briefs = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.action == auto_operator.ACTION_VITALITY_BRIEFING
    ).all()
    assert len(briefs) == 1 and briefs[0].event_type == "observe"
    mock_slack.assert_called_once()


# ══════════════════════════ GATE ② 가드레일 상속(우회 없음) ══════════════════════════

def test_vitality_fire_goes_through_guardrail_and_execute(db):
    """복원 UP은 새 쓰기 경로가 아니라 naver_execution_harness.execute→(executor BEP 완전성
    가드)→guardrail_gate.check→writer를 관통한다(우회 경로 0). 완전한 가드 컨텍스트를 주면
    guardrail_gate.check 호출·writer 호출·change_log [스파이럴복원] update_bid 기록."""
    from app.services.naver_ad import naver_execution_harness as harness

    _seed_spiral(db)
    ctx = {"roas_corrected": 2.0, "target_roas": 1.0, "daily_budget": 0, "cost_today": 0,
           "current_bid": 1000, "last_change_at": None, "changes_today_count": 0}
    write_result = naver_sa_writer.WriteResult(
        action="update_adgroup_bid", before={"bidAmt": 1000, "userLock": False},
        response=None, after={"bidAmt": 1150, "userLock": False}, created_ids=[],
    )
    with patch.object(auto_operator, "_live_current_bid", return_value=1000), \
         patch.object(harness, "_build_guardrail_context", return_value=ctx), \
         patch.object(harness.guardrail_gate, "check", return_value=None) as mock_check, \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid", return_value=write_result) as mock_write, \
         patch.object(auto_operator.slack_notifier, "notify_text", return_value={"sent": True}):
        result = _result()
        auto_operator._run_vitality_step(db, NOW, result)

    mock_check.assert_called_once()  # 가드레일이 발사 경로 위에 있음(우회 없음)
    mock_write.assert_called_once_with(GROUP, 1150)
    assert result["vitality_fired"] == 1
    log_row = db.query(NaverChangeLog).filter(NaverChangeLog.action == "update_bid").one()
    assert log_row.rationale.startswith(auto_operator.VITALITY_RATIONALE_PREFIX)


def test_vitality_fire_fail_closed_when_bep_context_incomplete(db):
    """우회 없음(반대 증명): 가드 컨텍스트가 불완전(BEP/일예산 None)하면 adgroup 증액은
    executor 완전성 가드에 fail-closed 차단 — writer 미호출·발사 0·change_log outcome=failed.
    복원 UP도 일반 시간당 UP과 똑같이 D-NAO-1 이익하한 가드를 상속한다."""
    from app.services.naver_ad import naver_execution_harness as harness

    _seed_spiral(db)
    with patch.object(auto_operator, "_live_current_bid", return_value=1000), \
         patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None) as mock_check, \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid") as mock_write, \
         patch.object(auto_operator.slack_notifier, "notify_text", return_value={"sent": True}):
        result = _result()
        auto_operator._run_vitality_step(db, NOW, result)

    mock_write.assert_not_called()      # BEP 컨텍스트 불완전 → 실쓰기 차단(우회 없음)
    mock_check.assert_not_called()      # 완전성 가드가 guardrail_gate 앞에서 fail-closed
    assert result["vitality_fired"] == 0 and result["failed"] == 1
    log_row = db.query(NaverChangeLog).filter(NaverChangeLog.action == "update_bid").one()
    assert log_row.outcome == "failed"


# ══════════════════════════ GATE ③ 무경보 무동작·무브리핑 ══════════════════════════

def test_no_alert_no_action_no_briefing(db):
    """스파이럴 아님(노출 상승 추세) → 경보 0·발사 0·brief 0·proposal 0(완전 침묵)."""
    rising = {d: 500 + i * 100 for i, d in enumerate(sorted(_IMP))}  # 단조 상승
    _seed_spiral(db, imp=rising)
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec, \
         patch.object(auto_operator.slack_notifier, "notify_text") as mock_slack:
        result = _result()
        auto_operator._run_vitality_step(db, NOW, result)

    assert result["vitality_alerts"] == 0 and result["vitality_fired"] == 0
    mock_exec.assert_not_called()
    mock_slack.assert_not_called()
    assert db.query(NaverProposal).count() == 0
    assert db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.action == auto_operator.ACTION_VITALITY_BRIEFING
    ).count() == 0


# ══════════════════════════ GATE ④ 캡·쿨다운 ══════════════════════════

def test_daily_cap_blocks_sixth_fire(db):
    """캠페인 일일 캡 — 오늘 이미 [스파이럴복원] 5건이면 추가 발사 차단(held·execute 미호출)."""
    _seed_spiral(db)
    for i in range(5):
        _seed_fire_log(db, entity_id=f"grp-x{i}", changed_at=datetime(2026, 7, 19, 3, 0))
    with patch.object(auto_operator, "_live_current_bid", return_value=1000), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec, \
         patch.object(auto_operator.slack_notifier, "notify_text", return_value={"sent": True}):
        result = _result()
        auto_operator._run_vitality_step(db, NOW, result)

    assert result["vitality_fired"] == 0
    mock_exec.assert_not_called()
    assert any("일일 캡" in h["reason"] for h in result["vitality_held"])


def test_group_48h_cooldown_blocks_refire(db):
    """같은 그룹 48h 재발사 쿨다운 — 47h 전 발사 이력 있으면 held(execute 미호출)."""
    _seed_spiral(db)
    _seed_fire_log(db, entity_id=GROUP, changed_at=NOW - timedelta(hours=47))
    with patch.object(auto_operator, "_live_current_bid", return_value=1000), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec, \
         patch.object(auto_operator.slack_notifier, "notify_text", return_value={"sent": True}):
        result = _result()
        auto_operator._run_vitality_step(db, NOW, result)

    assert result["vitality_fired"] == 0
    mock_exec.assert_not_called()
    assert any("쿨다운" in h["reason"] for h in result["vitality_held"])


def test_cooldown_expired_allows_refire(db):
    """49h 전 발사(48h 경과)면 쿨다운 해제 → 재발사 허용."""
    _seed_spiral(db)
    _seed_fire_log(db, entity_id=GROUP, changed_at=NOW - timedelta(hours=49))
    with patch.object(auto_operator, "_live_current_bid", return_value=1000), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec, \
         patch.object(auto_operator.slack_notifier, "notify_text", return_value={"sent": True}):
        result = _result()
        auto_operator._run_vitality_step(db, NOW, result)

    assert result["vitality_fired"] == 1
    mock_exec.assert_called_once()


def test_cooldown_counts_blocked_attempts(db):
    """GATE P2-2 재시도 폭풍 감쇠 — BEP 차단 등 실패·가드거부 시도 행(after_value=None)도 48h
    쿨다운으로 재발사 억제(after_value 필터 없음). BEP 차단은 시간 단위로 안 바뀌는 조건이라
    시도 자체를 봉투 보수 우선으로 감쇠 — 매시간 라이브 GET 다발·failed 행 누적을 막는다."""
    _seed_spiral(db)
    db.add(NaverChangeLog(
        entity_type="adgroup", entity_id=GROUP, campaign_id=CAMPAIGN, action="update_bid",
        rationale=f"{auto_operator.VITALITY_RATIONALE_PREFIX} 과거 시도 [실행 불가] 가드레일 차단 — BEP",
        dry_run=False, after_value=None,
        changed_at=NOW - timedelta(hours=1), executed_at=NOW - timedelta(hours=1),
    ))
    db.commit()
    with patch.object(auto_operator, "_live_current_bid", return_value=1000), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec, \
         patch.object(auto_operator.slack_notifier, "notify_text", return_value={"sent": True}):
        result = _result()
        auto_operator._run_vitality_step(db, NOW, result)

    assert result["vitality_fired"] == 0
    mock_exec.assert_not_called()
    assert any("쿨다운" in h["reason"] for h in result["vitality_held"])


# ══════════════════════════ GATE P3 회복력(발사 준비 fail-soft) ══════════════════════════

def test_fire_prep_exception_is_fail_soft(db):
    """GATE P3 — 준비 구간(_live_current_bid) 예외는 fail-soft: 해당 타깃만 held되고 예외가
    전파되지 않아 브리핑은 정상 발화한다(한 타깃 예외가 남은 타깃·브리핑을 죽이지 않음)."""
    _seed_spiral(db)
    with patch.object(auto_operator, "_live_current_bid", side_effect=RuntimeError("라이브 GET 폭발")), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec, \
         patch.object(auto_operator.slack_notifier, "notify_text", return_value={"sent": True}) as mock_slack:
        result = _result()
        auto_operator._run_vitality_step(db, NOW, result)  # 예외 전파 없음

    assert result["vitality_fired"] == 0
    mock_exec.assert_not_called()
    assert any("fail-soft" in h["reason"] for h in result["vitality_held"])
    mock_slack.assert_called_once()  # 브리핑은 살아있음(fail-soft)


# ══════════════════════════ GATE ⑥ 기존 시간당 레인 회귀 0 ══════════════════════════

def test_hourly_lane_carries_vitality_keys_and_no_alert_silent(db):
    """run_hourly_lane이 vitality 키(vitality_alerts/fired/held)를 반환에 싣고, 무경보면
    기존 레인 동작 불변(핫셋 없음=0). auto_operate 캠페인은 있으나 스파이럴 데이터 없음."""
    db.add(NaverCampaignSettings(campaign_id="cmp-04", auto_operate=True, optimizer="ours"))
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp-04", campaign_id="cmp-04", status="on"))
    db.commit()
    with patch.object(auto_operator.naver_execution_harness, "execute"), \
         patch.object(auto_operator.slack_notifier, "notify_text"):
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda tid, d: [])

    # 기존 키 보존
    for k in ("reviewed", "approved", "executed", "held", "skipped", "failed"):
        assert k in result
    # vitality 키 추가·무경보 침묵
    assert result["vitality_alerts"] == 0
    assert result["vitality_fired"] == 0
    assert result["vitality_held"] == []
