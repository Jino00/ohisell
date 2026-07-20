# test_naver_bid_step_types.py — IU-R R0 UP 타입 레지스트리 단일 소스화 단위 + 차등 테스트.
#   (A) bid_step_types 레지스트리 자체 단위 테스트(멤버십·is_bid_up·direction_of·RANK_STEP_TYPES 빈 셋).
#   (B) 차등 테스트 — 리팩터 전 하드코딩 리터럴을 골든값으로 고정해, 레지스트리로 이관된
#       guardrail 판정·_ACTION_BY_PROPOSAL_TYPE 매핑·direction 매핑·_executed_bid_ups_today 카운터가
#       "동일 입력 → 동일 결과"임을 못박는다(행위 불변 = 판정 산출물 동일, PLAN §2 R0 · codex P2).
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverChangeLog, NaverProposal
from app.services.naver_ad import auto_operator, diary_outcome, guardrail_gate
from app.services.naver_ad import naver_execution_harness as harness
from app.services.naver_ad.bid_step_types import (
    BID_DOWN_TYPES,
    BID_UP_TYPES,
    CHANGE_PCT_EXEMPT_TYPES,
    RANK_STEP_TYPES,
    direction_of,
    is_bid_up,
)

# 리팩터 전(pre-R0) 하드코딩 리터럴 — 차등 테스트의 골든값. 이 값들이 바뀌면 = 행위 변경.
_GOLDEN_BID_UP = frozenset({"bid_up", "growth_bid_up"})
_GOLDEN_BID_DOWN = frozenset({"bid_down"})
_GOLDEN_EXEMPT = frozenset({"growth_bid_up"})


# ══════════════════════════════════════════════════════════════════
# (A) 레지스트리 자체 단위 테스트
# ══════════════════════════════════════════════════════════════════
def test_registry_membership_sets():
    assert BID_UP_TYPES == _GOLDEN_BID_UP
    assert BID_DOWN_TYPES == _GOLDEN_BID_DOWN
    assert CHANGE_PCT_EXEMPT_TYPES == _GOLDEN_EXEMPT


def test_rank_step_types_empty_placeholder():
    # R0는 신규 rank 타입을 아직 도입하지 않는다 — 자리만 잡은 빈 셋(R1/R2에서 채움).
    assert RANK_STEP_TYPES == frozenset()


def test_registry_invariants():
    # ±15% 면제 타입은 반드시 UP 타입의 부분집합(면제는 UP 스텝에만 의미).
    assert CHANGE_PCT_EXEMPT_TYPES <= BID_UP_TYPES
    # UP과 DOWN은 서로소(한 타입이 두 방향일 수 없음).
    assert BID_UP_TYPES.isdisjoint(BID_DOWN_TYPES)


@pytest.mark.parametrize("pt,expected", [
    ("bid_up", True), ("growth_bid_up", True),
    ("bid_down", False), ("pause", False), ("resume", False),
    ("budget_up", False), ("update_bid", False), ("", False), (None, False),
])
def test_is_bid_up(pt, expected):
    assert is_bid_up(pt) is expected


@pytest.mark.parametrize("pt,expected", [
    ("bid_up", "up"), ("growth_bid_up", "up"),
    ("bid_down", "down"),
    ("pause", None), ("resume", None), ("budget_up", None),
    ("update_bid", None), ("set_user_lock", None), ("", None), (None, None),
])
def test_direction_of(pt, expected):
    assert direction_of(pt) == expected


# ══════════════════════════════════════════════════════════════════
# (B1) 차등 — guardrail_gate가 소비하는 집합이 골든과 동일 + 판정 산출물 동일
# ══════════════════════════════════════════════════════════════════
def test_guardrail_consumes_registry_sets():
    # guardrail_gate가 레지스트리를 별칭 import — 골든 리터럴과 값 동일(중복 정의 아님, 동일 객체).
    assert guardrail_gate._BID_UP_TYPES == _GOLDEN_BID_UP
    assert guardrail_gate._BID_DOWN_TYPES == _GOLDEN_BID_DOWN
    assert guardrail_gate._EXEMPT_FROM_CHANGE_PCT == _GOLDEN_EXEMPT
    assert guardrail_gate._BID_UP_TYPES is BID_UP_TYPES  # 단일 소스(별칭)


_NOW = datetime(2026, 7, 20, 12, 0, 0)


def _ctx(**over):
    base = {
        "current_bid": 200, "roas_corrected": 300.0, "target_roas": 150.0,
        "cost_today": 1000, "daily_budget": 500_000, "unconverted_spend": 0,
        "last_change_at": None, "changes_today_count": 0,
    }
    base.update(over)
    return base


def _bid(proposal_type, target_bid):
    return {"proposal_type": proposal_type, "target_bid": target_bid, "target_lock": None}


def test_guardrail_change_pct_exemption_matches_golden():
    # 200 → 300 = +50%(±15% 초과). growth_bid_up(면제)만 통과, bid_up은 변경폭 차단.
    over = _ctx()
    assert guardrail_gate.check(_bid("growth_bid_up", 300), over, now=_NOW) is None
    reason = guardrail_gate.check(_bid("bid_up", 300), over, now=_NOW)
    assert reason is not None and "변경폭" in reason


def test_guardrail_up_only_bep_applies_to_both_up_types_not_down():
    # 보정ROAS < 목표 → UP은 BEP 미달 차단, DOWN은 up-only 검사 면제(방향 검증만).
    over = _ctx(roas_corrected=100.0, target_roas=150.0)
    for up_type in sorted(_GOLDEN_BID_UP):
        r = guardrail_gate.check(_bid(up_type, 210), over, now=_NOW)
        assert r is not None and "BEP" in r, up_type
    # bid_down(200→190)은 BEP 검사 없음 → 통과.
    assert guardrail_gate.check(_bid("bid_down", 190), over, now=_NOW) is None


def test_guardrail_daily_cap_exempts_down_only():
    # 일일 상한 도달 — DOWN만 면제(_BID_DOWN_TYPES), UP 2종은 차단.
    over = _ctx(changes_today_count=3)
    assert guardrail_gate.check(_bid("bid_down", 190), over, now=_NOW) is None
    for up_type in sorted(_GOLDEN_BID_UP):
        r = guardrail_gate.check(_bid(up_type, 210), over, now=_NOW)
        assert r is not None and "일일 변경" in r, up_type


# ══════════════════════════════════════════════════════════════════
# (B2) 차등 — _ACTION_BY_PROPOSAL_TYPE: 오늘 값 골든 불변 + bid 계열은 레지스트리 파생
# (codex R0 리뷰 P2: 리터럴 유지 시 R1 신규 타입이 가드엔 등록되고 실행 매핑에서 누락되는
#  어긋남 위험 → 파생으로 전환. 파생 후에도 오늘 값은 골든과 동일해야 함 = 행위 불변)
# ══════════════════════════════════════════════════════════════════
def test_action_by_proposal_type_mapping_unchanged():
    assert harness._ACTION_BY_PROPOSAL_TYPE == {
        "negative_keyword": "add_negative_keyword",
        "bid_up": "update_bid",
        "bid_down": "update_bid",
        "growth_bid_up": "update_bid",
        "pause": "set_user_lock",
        "resume": "set_user_lock",
        "budget_up": "update_budget",
        "budget_down": "update_budget",
    }


def test_action_mapping_derives_from_registry():
    """레지스트리의 모든 bid 타입은 실행 매핑에 update_bid로 존재 —
    R1/R2에서 신규 타입을 레지스트리에 등록하면 이 테스트가 매핑 누락을 잡는다."""
    for t in BID_UP_TYPES | BID_DOWN_TYPES:
        assert harness._ACTION_BY_PROPOSAL_TYPE.get(t) == "update_bid"


# ══════════════════════════════════════════════════════════════════
# (B3) 차등 — diary_outcome direction 매핑 불변(bid 계열만 direction_of로 이관)
# ══════════════════════════════════════════════════════════════════
def test_diary_action_to_direction_matches_golden():
    # 리팩터 전 리터럴과 완전히 동일한 5개 키·값(growth_bid_up은 여전히 미매핑=무필터).
    assert diary_outcome._ACTION_TO_DIRECTION == {
        "bid_up": "up", "bid_down": "down", "pause": "pause",
        "update_bid": None, "set_user_lock": None,
    }
    assert "growth_bid_up" not in diary_outcome._ACTION_TO_DIRECTION


# ══════════════════════════════════════════════════════════════════
# (B4) 차등 — _AD_BID_CANARY_PROPOSAL_TYPES 값 불변(rename만)
# ══════════════════════════════════════════════════════════════════
def test_ad_canary_proposal_types_value_unchanged():
    assert auto_operator._AD_BID_CANARY_PROPOSAL_TYPES == frozenset({"bid_down"})


# ══════════════════════════════════════════════════════════════════
# (B5) 차등 — _executed_bid_ups_today 카운터: UP 2종만 카운트, DOWN 제외
# ══════════════════════════════════════════════════════════════════
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


def _seed_executed(db, proposal_type, *, changed_at, after_value="210", dry_run=False,
                   action="update_bid"):
    p = NaverProposal(
        created_at=changed_at, proposal_type=proposal_type, target_type="adgroup",
        target_id="grp-1", campaign_id="cmp-1", status="executed", target_bid=210,
    )
    db.add(p)
    db.flush()
    db.add(NaverChangeLog(
        changed_at=changed_at, entity_type="adgroup", entity_id="grp-1", campaign_id="cmp-1",
        action=action, after_value=after_value, proposal_id=p.id, dry_run=dry_run,
    ))
    db.commit()


def test_executed_bid_ups_today_counts_both_up_types_not_down(db):
    now = datetime(2026, 7, 20, 15, 0, 0)
    today = datetime(2026, 7, 20, 9, 0, 0)
    _seed_executed(db, "bid_up", changed_at=today)           # 카운트 ✓
    _seed_executed(db, "growth_bid_up", changed_at=today)    # 카운트 ✓ (UP)
    _seed_executed(db, "bid_down", changed_at=today)         # 제외 (DOWN)
    assert auto_operator._executed_bid_ups_today(db, "adgroup", "grp-1", now) == 2


def test_executed_bid_ups_today_excludes_dryrun_and_yesterday(db):
    now = datetime(2026, 7, 20, 15, 0, 0)
    today = datetime(2026, 7, 20, 9, 0, 0)
    yesterday = datetime(2026, 7, 19, 9, 0, 0)
    _seed_executed(db, "bid_up", changed_at=today)                    # 카운트 ✓
    _seed_executed(db, "bid_up", changed_at=today, dry_run=True)      # 제외(dry_run)
    _seed_executed(db, "bid_up", changed_at=today, after_value=None)  # 제외(미실쓰기)
    _seed_executed(db, "growth_bid_up", changed_at=yesterday)         # 제외(어제)
    assert auto_operator._executed_bid_ups_today(db, "adgroup", "grp-1", now) == 1
