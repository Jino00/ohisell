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
    EXPLORATION_STEP_TYPES,
    RANK_STEP_TYPES,
    direction_of,
    is_bid_up,
)

# 리팩터 전(pre-R0) 하드코딩 리터럴 — R0 차등 테스트가 "동일 입력 → 동일 결과"를 증명한
# 원본 두 타입(bid_up·growth_bid_up). R0의 B-루프(BEP·일일캡)는 이 두 타입 위에서 pre-R0
# 행위가 보존됨을 계속 못박는다. ★IU-R R1은 여기에 `bid_up_servo`를 **의도적으로** 추가한다
# (행위 변경 = 신규 쇼검 서보 타입) — 멤버십/매핑 테스트는 아래 R1 기대값으로 갱신한다.
_GOLDEN_BID_UP = frozenset({"bid_up", "growth_bid_up"})
_GOLDEN_BID_DOWN = frozenset({"bid_down"})
_GOLDEN_EXEMPT = frozenset({"growth_bid_up"})

# IU-R R2 기대 상태 — R1 bid_up_servo(쇼검 서보) + R2 bid_up_rank(파워링크 estimate 직행)가
# UP·±15%면제·rank-step 3셋에 등록됨.
# ★BX2(D-NAO-70·71): 탐색 UP 타입 bid_up_explore가 **UP에는 추가**되나 ±15%면제·rank-step에는
#   추가되지 않는다(30% 상한 = EXPLORATION_STEP_TYPES 별도 셋, 완전 면제도 rank-step도 아님).
# ★CS(콜드 스타트): `bid_up_cold`가 UP + ±15%완전면제 두 셋에 추가된다(rank-step·탐색 셋에는
#   미추가 — 시장가 직행이라 rank 서보도 30% 래더도 아니다).
_BX2_BID_UP = _GOLDEN_BID_UP | {"bid_up_servo", "bid_up_rank", "bid_up_explore", "bid_up_cold"}
_R2_EXEMPT = _GOLDEN_EXEMPT | {"bid_up_servo", "bid_up_rank", "bid_up_cold"}
_R2_RANK_STEP = frozenset({"bid_up_servo", "bid_up_rank"})
_BX2_EXPLORATION = frozenset({"bid_up_explore"})


# ══════════════════════════════════════════════════════════════════
# (A) 레지스트리 자체 단위 테스트
# ══════════════════════════════════════════════════════════════════
def test_registry_membership_sets():
    # IU-R R2: bid_up_servo·bid_up_rank가 UP·±15%면제 셋에 추가됨(DOWN은 불변).
    # BX2: bid_up_explore가 UP에 추가(±15%면제엔 미추가 — 30% 별도 상한).
    assert BID_UP_TYPES == _BX2_BID_UP
    assert BID_DOWN_TYPES == _GOLDEN_BID_DOWN
    assert CHANGE_PCT_EXEMPT_TYPES == _R2_EXEMPT


def test_rank_step_types_filled_with_servo():
    # IU-R R2: rank-step 타입 = 쇼검 서보 bid_up_servo + 파워링크 estimate 직행 bid_up_rank.
    assert RANK_STEP_TYPES == _R2_RANK_STEP
    # rank-step은 반드시 UP 타입의 부분집합(rank 스텝은 상향 스텝의 하위 의미).
    assert RANK_STEP_TYPES <= BID_UP_TYPES


def test_exploration_step_types_registered_bx2():
    # BX2(D-NAO-70·71): 탐색 스텝 타입 = bid_up_explore. UP의 부분집합이지만 ±15%완전면제도
    # rank-step도 아님(30% 상한 별도 셋 — guardrail_gate가 이 셋을 보고 0.30 상한 적용).
    assert EXPLORATION_STEP_TYPES == _BX2_EXPLORATION
    assert EXPLORATION_STEP_TYPES <= BID_UP_TYPES
    assert EXPLORATION_STEP_TYPES.isdisjoint(CHANGE_PCT_EXEMPT_TYPES)  # 완전 면제 아님
    assert EXPLORATION_STEP_TYPES.isdisjoint(RANK_STEP_TYPES)  # rank-step 아님(고정 30% 래더)


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
    # guardrail_gate가 레지스트리를 별칭 import — R2 값과 동일(중복 정의 아님, 동일 객체).
    assert guardrail_gate._BID_UP_TYPES == _BX2_BID_UP
    assert guardrail_gate._BID_DOWN_TYPES == _GOLDEN_BID_DOWN
    assert guardrail_gate._EXEMPT_FROM_CHANGE_PCT == _R2_EXEMPT
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
# (B1-R1) IU-R R1 서보 타입(bid_up_servo) 가드 차등 — ±15% 면제하되 나머지 가드 존치
# ══════════════════════════════════════════════════════════════════
def test_guardrail_servo_change_pct_exempt_like_growth():
    # bid_up_servo도 ±15% 변경폭 면제(한 순위 위 입찰폭이 15% 초과 가능). 200→300=+50% 통과.
    over = _ctx()
    assert guardrail_gate.check(_bid("bid_up_servo", 300), over, now=_NOW) is None
    # 대조: bid_up(비면제)은 변경폭 차단.
    r = guardrail_gate.check(_bid("bid_up", 300), over, now=_NOW)
    assert r is not None and "변경폭" in r


def test_guardrail_servo_stop_loss_uses_current_base_stricter():
    # current=1000, target=1450(+45%, 면제), unconverted=12000.
    # rank-step(bid_up_servo): 스톱로스 base=current 1000×10=10000 → 12000≥10000 → 차단.
    # 대조 growth_bid_up(비 rank-step): base=target 1450×10=14500 → 12000<14500 → 스톱로스 통과.
    over = _ctx(current_bid=1000, unconverted_spend=12000, roas_corrected=300.0, target_roas=150.0)
    r_servo = guardrail_gate.check(_bid("bid_up_servo", 1450), over, now=_NOW)
    assert r_servo is not None and "스톱로스" in r_servo and "current_bid" in r_servo
    assert guardrail_gate.check(_bid("growth_bid_up", 1450), over, now=_NOW) is None


def test_guardrail_servo_still_blocks_bep_budget_cooldown_cap():
    # ±15%는 면제해도 BEP·일예산·쿨다운·일일캡은 서보에도 전량 존치(PLAN §0 불변 가드).
    r_bep = guardrail_gate.check(
        _bid("bid_up_servo", 1300), _ctx(current_bid=1000, roas_corrected=100.0, target_roas=150.0), now=_NOW,
    )
    assert r_bep is not None and "BEP" in r_bep
    r_bud = guardrail_gate.check(
        _bid("bid_up_servo", 1300), _ctx(current_bid=1000, cost_today=500_000, daily_budget=500_000), now=_NOW,
    )
    assert r_bud is not None and "일예산" in r_bud
    r_cd = guardrail_gate.check(
        _bid("bid_up_servo", 1300), _ctx(current_bid=1000, last_change_at=_NOW - timedelta(hours=1)), now=_NOW,
    )
    assert r_cd is not None and "쿨다운" in r_cd
    r_cap = guardrail_gate.check(
        _bid("bid_up_servo", 1300), _ctx(current_bid=1000, changes_today_count=3), now=_NOW,
    )
    assert r_cap is not None and "일일 변경" in r_cap


# ══════════════════════════════════════════════════════════════════
# (B2) 차등 — _ACTION_BY_PROPOSAL_TYPE: bid 계열은 레지스트리 파생(R0 P2). IU-R R1에서
# bid_up_servo를 레지스트리에 등록하면 실행 매핑도 자동으로 update_bid로 파생돼야 한다
# (가드는 UP으로 인식하는데 실행 매핑 누락으로 ActionNotExecutableError가 나는 어긋남 차단).
# ══════════════════════════════════════════════════════════════════
def test_action_by_proposal_type_mapping_derived_with_servo():
    assert harness._ACTION_BY_PROPOSAL_TYPE == {
        "negative_keyword": "add_negative_keyword",
        "bid_up": "update_bid",
        "bid_down": "update_bid",
        "growth_bid_up": "update_bid",
        "bid_up_servo": "update_bid",  # IU-R R1: 레지스트리 파생으로 자동 매핑
        "bid_up_rank": "update_bid",   # IU-R R2: 레지스트리 파생으로 자동 매핑
        "bid_up_explore": "update_bid",  # BX2(D-NAO-70): 탐색 UP도 레지스트리 파생 자동 매핑
        "bid_up_cold": "update_bid",  # CS: 콜드 첫 입찰도 레지스트리 파생 자동 매핑
        "pause": "set_user_lock",
        "resume": "set_user_lock",
        "budget_up": "update_budget",
        "budget_down": "update_budget",
        "search_term_exclude": "exclude_search_term",  # SS3: 검색어 제외(Confirm 전용)
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
