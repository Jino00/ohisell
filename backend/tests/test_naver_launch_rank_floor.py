# test_naver_launch_rank_floor.py — D-NAO-121 출시창 순위 하한 SA 단위테스트.
# 근거: launch_rank_floor.py 신규(회귀 0), guardrail_gate._check_bid의 출시창 하한 분기.
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily,
    NaverAdgroupProduct,
    NaverBidEstimateDaily,
    NaverLearningState,
)
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


def _ladder_row(rank, bid, ladder_date=TODAY, ad_id=AD_ID, device="MOBILE", is_floor=False):
    """★2026-07-29 정정: position은 **1-베이스**(1위=1, 4위=4)이고 0은 최소노출입찰가다
    (models.NaverBidEstimateDaily docstring). 종전 이 헬퍼가 `position=rank - 1`로 저장해
    코드의 오프바이원과 정확히 같은 오류를 복제했고, 그래서 테스트가 통과하면서 버그를
    가렸다(codex 리뷰 P1). 헬퍼는 실제 규약을 따르고, 규약 자체는 아래 회귀 테스트가 못박는다."""
    return NaverBidEstimateDaily(
        date=ladder_date, ad_id=ad_id, adgroup_id=ADGROUP_ID, campaign_id=CAMPAIGN_ID,
        device=device, position=rank, bid=bid, is_floor=is_floor,
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


# ── codex 리뷰 P1 회귀 (2026-07-29) ─────────────────────────────────────────


def test_ladder_position_is_one_based_not_zero_based(db):
    """★목표 4위는 position 4를 읽어야 한다 — 종전엔 rank-1로 조회해 3위 가격을 강제했다.

    라이브 3개 상품이 전부 목표 4위였으므로 실제로 한 칸 비싼 값이 하한이 되어 있었다.
    """
    db.add(_exposure_row(TODAY - timedelta(days=1)))
    for rank, bid in ((1, 5000), (2, 4000), (3, 3000), (4, 2000)):
        db.add(_ladder_row(rank, bid))
    lrf.set_target_rank(db, AD_ID, 4, note="test")
    db.commit()

    out = lrf.floor_for(db, ad_id=AD_ID, adgroup_id=ADGROUP_ID, today=TODAY)
    assert out["floor_bid"] == 2000, "4위 목표는 4위 가격(2000)이어야 한다"


def test_rank_one_does_not_read_exposure_minimum(db):
    """★position 0은 순위가 아니라 최소노출입찰가다 — 1위 목표가 이걸 읽으면 순위를 잃는다."""
    db.add(_exposure_row(TODAY - timedelta(days=1)))
    db.add(_ladder_row(1, 5000))
    db.add(NaverBidEstimateDaily(  # position 0 = 최소노출입찰가(가상 position)
        date=TODAY, ad_id=AD_ID, adgroup_id=ADGROUP_ID, campaign_id=CAMPAIGN_ID,
        device="MOBILE", position=0, bid=50, is_floor=False,
    ))
    lrf.set_target_rank(db, AD_ID, 1, note="test")
    db.commit()

    out = lrf.floor_for(db, ad_id=AD_ID, adgroup_id=ADGROUP_ID, today=TODAY)
    assert out["floor_bid"] == 5000, "1위 목표가 최소노출입찰가 50원을 읽으면 안 된다"


def test_is_floor_rows_are_not_treated_as_market_price(db):
    """★is_floor=시세 무의미 표식. bep_breakdown이 같은 이유로 이미 제외한다 —
    신제품 사다리가 이 상태가 되기 쉬워 하한이 50원으로 무너질 수 있었다."""
    db.add(_exposure_row(TODAY - timedelta(days=1)))
    db.add(_ladder_row(4, 50, is_floor=True))
    lrf.set_target_rank(db, AD_ID, 4, note="test")
    db.commit()

    out = lrf.floor_for(db, ad_id=AD_ID, adgroup_id=ADGROUP_ID, today=TODAY)
    assert out["floor_bid"] is None, "시세 무의미 행을 하한으로 쓰면 안 된다"


def test_is_floor_row_does_not_shadow_a_valid_older_row(db):
    """당일이 is_floor여도 유효한 과거 행이 있으면 그쪽으로 폴백해야 한다."""
    db.add(_exposure_row(TODAY - timedelta(days=2)))
    db.add(_ladder_row(4, 2000, ladder_date=TODAY - timedelta(days=1)))
    db.add(_ladder_row(4, 50, ladder_date=TODAY, is_floor=True))
    lrf.set_target_rank(db, AD_ID, 4, note="test")
    db.commit()

    out = lrf.floor_for(db, ad_id=AD_ID, adgroup_id=ADGROUP_ID, today=TODAY)
    assert out["floor_bid"] == 2000
    assert out["ladder_date"] == TODAY - timedelta(days=1)


# ── group_floor_for — 그룹 입찰 경로 하한 (codex P1) ────────────────────────


def _mapping(db, *, ad_id, adgroup_id=ADGROUP_ID, use_group=None, mall="p1"):
    db.add(NaverAdgroupProduct(
        adgroup_id=adgroup_id, campaign_id=CAMPAIGN_ID, mall_product_id=mall,
        ad_id=ad_id, use_group_bid_amt=use_group,
    ))


def test_group_floor_none_without_launch_targets(db):
    _mapping(db, ad_id=AD_ID, use_group=True)
    db.commit()
    assert lrf.group_floor_for(db, adgroup_id=ADGROUP_ID, today=TODAY)["floor_bid"] is None


def test_group_floor_uses_group_bid_ads(db):
    """★useGroupBidAmt=true 소재의 실효 레버는 그룹 입찰 — 그 하한이 그룹 인하를 막아야 한다."""
    db.add(_exposure_row(TODAY - timedelta(days=1)))
    db.add(_ladder_row(4, 2000))
    _mapping(db, ad_id=AD_ID, use_group=True)
    lrf.set_target_rank(db, AD_ID, 4, note="test")
    db.commit()

    out = lrf.group_floor_for(db, adgroup_id=ADGROUP_ID, today=TODAY)
    assert out["floor_bid"] == 2000 and out["binding_ad_id"] == AD_ID


def test_group_floor_ignores_ads_with_own_bid(db):
    """use_group_bid_amt=False면 그룹 입찰이 그 소재를 안 움직인다 — 하한 대상 아님."""
    db.add(_exposure_row(TODAY - timedelta(days=1)))
    db.add(_ladder_row(4, 2000))
    _mapping(db, ad_id=AD_ID, use_group=False)
    lrf.set_target_rank(db, AD_ID, 4, note="test")
    db.commit()

    assert lrf.group_floor_for(db, adgroup_id=ADGROUP_ID, today=TODAY)["floor_bid"] is None


def test_group_floor_includes_unknown_use_group_bid(db):
    """NULL(미수집)은 포함한다 — 오탐 대가는 '인하 한 번 못함', 누락 대가는 '1페이지 이탈'."""
    db.add(_exposure_row(TODAY - timedelta(days=1)))
    db.add(_ladder_row(4, 2000))
    _mapping(db, ad_id=AD_ID, use_group=None)
    lrf.set_target_rank(db, AD_ID, 4, note="test")
    db.commit()

    assert lrf.group_floor_for(db, adgroup_id=ADGROUP_ID, today=TODAY)["floor_bid"] == 2000


def test_group_floor_takes_max_across_ads(db):
    """소재가 여럿이면 가장 높은 하한이 그룹을 구속한다(하나라도 밀리면 안 되므로)."""
    db.add(_exposure_row(TODAY - timedelta(days=1)))
    db.add(_ladder_row(4, 2000, ad_id="nad-a"))
    db.add(_ladder_row(4, 3500, ad_id="nad-b"))
    _mapping(db, ad_id="nad-a", use_group=True, mall="p1")
    _mapping(db, ad_id="nad-b", use_group=True, mall="p2")
    lrf.set_target_rank(db, "nad-a", 4, note="test")
    lrf.set_target_rank(db, "nad-b", 4, note="test")
    db.commit()

    out = lrf.group_floor_for(db, adgroup_id=ADGROUP_ID, today=TODAY)
    assert out["floor_bid"] == 3500 and out["binding_ad_id"] == "nad-b"
