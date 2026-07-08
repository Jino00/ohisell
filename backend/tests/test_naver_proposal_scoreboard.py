# test_naver_proposal_scoreboard.py — 듀얼모드 스프린트 Phase 6 proposal_scoreboard 단위테스트
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverChangeLog, NaverLearningState
from app.services.naver_ad import proposal_scoreboard as sb

TODAY = date(2026, 7, 8)
EXECUTED = TODAY - timedelta(days=15)
VERIFY = EXECUTED + timedelta(days=14)  # == TODAY - 1


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


def _ad_rows(keyword_id, date_from, date_to, daily_clk, daily_conv_amt):
    d = date_from
    rows = []
    while d <= date_to:
        rows.append(NaverAdDaily(
            ad_date=d, campaign_id="cmp1", campaign_type="WEB_SITE",
            adgroup_id="grp1", keyword_id=keyword_id, imp=100, clk=daily_clk, cost=1000,
            conv_direct_amt=daily_conv_amt, conv_indirect_amt=0,
        ))
        d += timedelta(days=1)
    return rows


def _change(entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1", action="update_bid",
            dry_run=True, verify_date=VERIFY, outcome=None):
    return NaverChangeLog(
        entity_type=entity_type, entity_id=entity_id, campaign_id=campaign_id, action=action,
        executed_at=datetime.combine(EXECUTED, datetime.min.time()), verify_date=verify_date,
        dry_run=dry_run, outcome=outcome, proposal_id=1,
    )


# ── evaluate_change ──
def test_evaluate_change_detects_improved(db):
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=14), EXECUTED - timedelta(days=1), daily_clk=1, daily_conv_amt=1000):
        db.add(r)  # before: RPC=1000
    for r in _ad_rows("nkw-1", EXECUTED, VERIFY, daily_clk=1, daily_conv_amt=1500):
        db.add(r)  # after: RPC=1500 (1.5배 개선)
    db.commit()

    change = _change()
    result = sb.evaluate_change(db, change, today=TODAY)
    assert result["outcome"] == "improved"


def test_evaluate_change_detects_declined(db):
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=14), EXECUTED - timedelta(days=1), daily_clk=1, daily_conv_amt=1000):
        db.add(r)
    for r in _ad_rows("nkw-1", EXECUTED, VERIFY, daily_clk=1, daily_conv_amt=500):
        db.add(r)  # after: RPC=500 (절반)
    db.commit()

    result = sb.evaluate_change(db, _change(), today=TODAY)
    assert result["outcome"] == "declined"


def test_evaluate_change_neutral_within_band(db):
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=14), EXECUTED - timedelta(days=1), daily_clk=1, daily_conv_amt=1000):
        db.add(r)
    for r in _ad_rows("nkw-1", EXECUTED, VERIFY, daily_clk=1, daily_conv_amt=1050):
        db.add(r)  # 5% 변화 — neutral 밴드
    db.commit()

    result = sb.evaluate_change(db, _change(), today=TODAY)
    assert result["outcome"] == "neutral"


def test_evaluate_change_none_when_thin_sample(db):
    # 클릭 자체가 LOW_CLICK_THRESHOLD 미만
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=1), EXECUTED - timedelta(days=1), daily_clk=1, daily_conv_amt=100):
        db.add(r)
    db.commit()
    result = sb.evaluate_change(db, _change(), today=TODAY)
    assert result["outcome"] is None
    assert result["actual_json"] is not None  # 판정은 못 해도 실측치는 기록


# ── run_daily ──
def test_run_daily_ignores_dry_run_changes(db):
    db.add(_change(dry_run=True))
    db.commit()
    result = sb.run_daily(db, today=TODAY)
    assert result["pending"] == 0  # dry_run=True는 대상 자체가 아님


def test_run_daily_ignores_changes_not_yet_due(db):
    db.add(_change(dry_run=False, verify_date=TODAY + timedelta(days=5)))
    db.commit()
    result = sb.run_daily(db, today=TODAY)
    assert result["pending"] == 0


def test_run_daily_ignores_already_verified(db):
    db.add(_change(dry_run=False, outcome="improved"))
    db.commit()
    result = sb.run_daily(db, today=TODAY)
    assert result["pending"] == 0


def test_run_daily_verifies_and_rolls_up_accuracy(db):
    for r in _ad_rows("nkw-1", EXECUTED - timedelta(days=14), EXECUTED - timedelta(days=1), daily_clk=1, daily_conv_amt=1000):
        db.add(r)
    for r in _ad_rows("nkw-1", EXECUTED, VERIFY, daily_clk=1, daily_conv_amt=1500):
        db.add(r)
    db.add(_change(dry_run=False))
    db.commit()

    result = sb.run_daily(db, today=TODAY)
    assert result["pending"] == 1
    assert result["verified"] == 1
    assert result["accuracy_by_action"]["update_bid"]["improved_ratio"] == 1.0

    change = db.query(NaverChangeLog).first()
    assert change.outcome == "improved"
    assert change.actual_json is not None

    learning_row = db.query(NaverLearningState).filter(
        NaverLearningState.scope_key == "update_bid", NaverLearningState.metric == "proposal_accuracy",
    ).first()
    assert learning_row is not None
    assert float(learning_row.current_value) == 1.0
    assert learning_row.sample_n == 1


def test_rollup_accuracy_excludes_dry_run_rows(db):
    """codex 지적: dry_run=True 건에 outcome이 채워져 있어도(수동/과거 경로) 정확도 통계에
    섞이면 안 된다 — dry_run=False 실집행 실적만 반영해야 한다."""
    db.add(_change(dry_run=True, outcome="improved"))  # 오염 시도 — 집계에서 빠져야 함
    db.add(_change(dry_run=False, outcome="declined"))
    db.commit()
    accuracy = sb._rollup_accuracy(db)
    assert accuracy["update_bid"]["n"] == 1  # dry_run=True 건은 제외
    assert accuracy["update_bid"]["improved_ratio"] == 0.0


def test_run_daily_leaves_undecided_for_thin_sample_for_retry(db):
    db.add(_change(dry_run=False))  # naver_ad_daily 데이터 없음 → 모수 미달
    db.commit()
    result = sb.run_daily(db, today=TODAY)
    assert result["pending"] == 1
    assert result["verified"] == 0

    change = db.query(NaverChangeLog).first()
    assert change.outcome is None  # 다음 회차 재시도 대상으로 남음
