# test_naver_launch_rank_floor.py — D-NAO-121 출시창 순위 하한 SA 단위테스트.
# 근거: launch_rank_floor.py 신규(회귀 0), guardrail_gate._check_bid의 출시창 하한 분기.
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverBidEstimateDaily, NaverLearningState
from app.services.naver_ad import guardrail_gate as gate
from app.services.naver_ad import launch_rank_floor as lrf

TODAY = date(2026, 7, 29)
AD_ID = "nad-1"
ADGROUP_ID = "grp-1"
CAMPAIGN_ID = "cmp-1"
NOW = datetime(2026, 7, 29, 10, 0, 0)


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


def _exposure_row(ad_date, adgroup_id=ADGROUP_ID, imp=10):
    return NaverAdDaily(
        ad_date=ad_date, campaign_id=CAMPAIGN_ID, campaign_type="SHOPPING",
        adgroup_id=adgroup_id, keyword_id="", imp=imp, clk=1, cost=1000,
    )


def _ladder_row(rank, bid, ladder_date=TODAY, ad_id=AD_ID, device="MOBILE"):
    # position은 0-베이스: 1위=0, 4위=3.
    return NaverBidEstimateDaily(
        date=ladder_date, ad_id=ad_id, adgroup_id=ADGROUP_ID, campaign_id=CAMPAIGN_ID,
        device=device, position=rank - 1, bid=bid, is_floor=False,
    )


# ── get_target_rank / set_target_rank 왕복 ──────────────────────────────────


def test_target_rank_round_trip(db):
    assert lrf.get_target_rank(db, AD_ID) is None
    lrf.set_target_rank(db, AD_ID, 4, note="test")
    db.commit()
    assert lrf.get_target_rank(db, AD_ID) == 4


def test_target_rank_round_trip_update_overwrites(db):
    lrf.set_target_rank(db, AD_ID, 4)
    db.commit()
    lrf.set_target_rank(db, AD_ID, 3)
    db.commit()
    assert lrf.get_target_rank(db, AD_ID) == 3
    # 같은 (scope, scope_key, metric)에 행이 하나만 있어야 함(upsert, 중복 생성 없음)
    rows = db.query(NaverLearningState).filter(
        NaverLearningState.scope == "entity", NaverLearningState.scope_key == AD_ID,
        NaverLearningState.metric == lrf.METRIC_TARGET_RANK,
    ).all()
    assert len(rows) == 1


# ── floor_for: 목표순위 미지정 ────────────────────────────────────────────


def test_floor_for_no_target_rank_returns_none(db):
    db.add(_exposure_row(TODAY - timedelta(days=1)))
    db.commit()
    out = lrf.floor_for(db, ad_id=AD_ID, adgroup_id=ADGROUP_ID, today=TODAY)
    assert out["floor_bid"] is None
    assert out["target_rank"] is None
    assert "미지정" in out["reason"]


# ── floor_for: 출시창 안 + 사다리에 목표순위 가격 존재 ──────────────────────


def test_floor_for_within_window_uses_ladder_price(db):
    lrf.set_target_rank(db, AD_ID, 4)
    db.add(_exposure_row(TODAY - timedelta(days=5)))  # 5일 전 첫 노출 -> 출시창(21일) 안
    db.add(_ladder_row(rank=4, bid=1390))
    db.commit()

    out = lrf.floor_for(db, ad_id=AD_ID, adgroup_id=ADGROUP_ID, today=TODAY)
    assert out["floor_bid"] == 1390
    assert out["target_rank"] == 4
    assert out["days_since_launch"] == 5
    assert out["ladder_date"] == TODAY
    assert "4위" in out["reason"]


# ── floor_for: 출시창 밖(22일) ───────────────────────────────────────────


def test_floor_for_outside_window_returns_none(db):
    lrf.set_target_rank(db, AD_ID, 4)
    db.add(_exposure_row(TODAY - timedelta(days=22)))  # 22일 전 첫 노출 -> 출시창(21일) 밖
    db.add(_ladder_row(rank=4, bid=1390))
    db.commit()

    out = lrf.floor_for(db, ad_id=AD_ID, adgroup_id=ADGROUP_ID, today=TODAY)
    assert out["floor_bid"] is None
    assert out["days_since_launch"] == 22
    assert "출시창 종료" in out["reason"]


def test_floor_for_exactly_at_window_boundary_still_applies(db):
    """21일 = 창 안(> 조건이라 21은 포함)."""
    lrf.set_target_rank(db, AD_ID, 4)
    db.add(_exposure_row(TODAY - timedelta(days=21)))
    db.add(_ladder_row(rank=4, bid=1390))
    db.commit()

    out = lrf.floor_for(db, ad_id=AD_ID, adgroup_id=ADGROUP_ID, today=TODAY)
    assert out["floor_bid"] == 1390


# ── floor_for: 노출 이력 없음 ────────────────────────────────────────────


def test_floor_for_no_exposure_history_returns_none(db):
    lrf.set_target_rank(db, AD_ID, 4)
    db.commit()
    out = lrf.floor_for(db, ad_id=AD_ID, adgroup_id=ADGROUP_ID, today=TODAY)
    assert out["floor_bid"] is None
    assert out["days_since_launch"] is None
    assert "노출 이력 없음" in out["reason"]


# ── floor_for: 사다리 당일 행 없음 -> 최근 행 폴백 ──────────────────────────


def test_floor_for_falls_back_to_most_recent_ladder_row(db):
    lrf.set_target_rank(db, AD_ID, 4)
    db.add(_exposure_row(TODAY - timedelta(days=5)))
    stale_date = TODAY - timedelta(days=2)
    db.add(_ladder_row(rank=4, bid=1450, ladder_date=stale_date))
    db.commit()

    out = lrf.floor_for(db, ad_id=AD_ID, adgroup_id=ADGROUP_ID, today=TODAY)
    assert out["floor_bid"] == 1450
    assert out["ladder_date"] == stale_date
    assert "사다리" in out["reason"]  # stale 라벨


def test_floor_for_no_ladder_row_at_all_returns_none(db):
    lrf.set_target_rank(db, AD_ID, 4)
    db.add(_exposure_row(TODAY - timedelta(days=5)))
    db.commit()
    out = lrf.floor_for(db, ad_id=AD_ID, adgroup_id=ADGROUP_ID, today=TODAY)
    assert out["floor_bid"] is None
    assert "사다리" in out["reason"]


# ── guardrail_gate 통합: 출시창 하한이 bid_down을 차단/통과 ─────────────────


def _bid_proposal(proposal_type="bid_down", target_bid=1000):
    return {"proposal_type": proposal_type, "target_bid": target_bid, "target_lock": None}


def _ctx(**overrides):
    base = {
        "current_bid": 1600,
        "current_budget": None,
        "roas_corrected": 250.0,
        "target_roas": 150.0,
        "cost_today": 10_000,
        "daily_budget": 500_000,
        "unconverted_spend": 0,
        "last_change_at": None,
        "changes_today_count": 0,
        "campaign_type": "SHOPPING",
    }
    base.update(overrides)
    return base


# current_bid=1600 기준, target 1400/1500은 각각 -12.5%/-6.25%로 ±15% 변경폭 상한 안쪽 —
# 이 테스트들이 검증하려는 건 출시창 하한 분기이지 변경폭 클램프가 아니므로 그 상한부터 피한다.


def test_guardrail_blocks_bid_down_below_launch_floor():
    reason = gate.check(
        _bid_proposal("bid_down", 1400),
        _ctx(launch_floor_bid=1500, launch_target_rank=4),
        now=NOW,
    )
    assert reason is not None
    assert "출시창 순위 하한" in reason


def test_guardrail_passes_bid_down_at_or_above_launch_floor():
    reason = gate.check(
        _bid_proposal("bid_down", 1500),
        _ctx(launch_floor_bid=1500, launch_target_rank=4),
        now=NOW,
    )
    assert reason is None


def test_guardrail_passes_bid_down_when_floor_absent_behavior_unchanged():
    """하한 없음(None) — 기존 bid_down 동작 그대로 통과(하위호환 회귀)."""
    reason = gate.check(
        _bid_proposal("bid_down", 1400),
        _ctx(launch_floor_bid=None, launch_target_rank=None),
        now=NOW,
    )
    assert reason is None


def test_guardrail_passes_bid_down_when_context_missing_launch_keys_entirely():
    """context 딕셔너리에 launch_floor_bid 키 자체가 없어도(.get 기본 None) 통과 — 구 호출부 호환."""
    ctx = _ctx()
    ctx.pop("launch_floor_bid", None)
    ctx.pop("launch_target_rank", None)
    reason = gate.check(_bid_proposal("bid_down", 1400), ctx, now=NOW)
    assert reason is None
