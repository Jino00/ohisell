# bid_step_types.py — UP/DOWN 입찰 스텝 proposal_type 판별의 단일 소스(레지스트리, 스프린트 IU-R R0).
#   역할: "이 proposal_type이 상향인가/하향인가·±15% 변경폭 면제 대상인가·순위 스텝 타입인가"의
#   판별을 한 곳에 중앙화한다. 종전엔 guardrail_gate·naver_execution_harness·auto_operator·
#   diary_outcome에 `("bid_up","growth_bid_up")` 문자열이 독립 산재해, 새 UP 타입을 일부에만
#   넣으면 BEP/스톱로스/일예산 컨텍스트 누락(fail-open 우회)·가드 미인식이 발생했다(PLAN §1-3).
#
#   ★이 모듈은 앱 내 어떤 모듈도 import 하지 않는다(codex P2 — 순환 import 원천 차단). 모든
#   소비자(guardrail_gate·harness·auto_operator·diary_outcome)가 이 최말단 상수 모듈을 단방향
#   import 한다. R0는 행위 불변 리팩터 — 신규 타입을 도입하지 않고 기존 값만 단일 소스로 이관한다.
from __future__ import annotations

# 상향 입찰 스텝 proposal_type(BEP·스톱로스·일예산 up-only 가드·일 1스텝 캡 카운터 트리거).
BID_UP_TYPES: frozenset[str] = frozenset({"bid_up", "growth_bid_up"})

# 하향 입찰 스텝 proposal_type(안전방향 — 노출↓·지출↓). 종전 guardrail_gate._BID_DOWN_TYPES.
BID_DOWN_TYPES: frozenset[str] = frozenset({"bid_down"})

# D-NAO-20-③: ±15% 변경폭 상한(_MAX_CHANGE_PCT)만 면제되는 타입(신규/육성 트랙). BEP·스톱로스·
# 일예산·쿨다운·일일상한은 전량 존치 — 면제되는 것은 변경폭 상한 하나뿐(종전 _EXEMPT_FROM_CHANGE_PCT).
CHANGE_PCT_EXEMPT_TYPES: frozenset[str] = frozenset({"growth_bid_up"})

# 순위(rank) 스텝 타입 — R1/R2에서 서보/estimate 타입(bid_up_servo·bid_up_rank)으로 채운다.
# R0에서는 빈 셋으로 자리만 잡는다(행위 불변 — 아직 어떤 타입도 rank-step이 아님).
RANK_STEP_TYPES: frozenset[str] = frozenset()


def is_bid_up(proposal_type: str | None) -> bool:
    """proposal_type이 상향 입찰 스텝인가(BID_UP_TYPES 멤버십)."""
    return proposal_type in BID_UP_TYPES


def direction_of(proposal_type: str | None) -> str | None:
    """proposal_type의 입찰 방향 — 상향 'up' / 하향 'down' / 그 외(비-bid) None."""
    if proposal_type in BID_UP_TYPES:
        return "up"
    if proposal_type in BID_DOWN_TYPES:
        return "down"
    return None
