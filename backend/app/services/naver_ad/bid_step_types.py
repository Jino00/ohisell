# bid_step_types.py — UP/DOWN 입찰 스텝 proposal_type 판별의 단일 소스(레지스트리, 스프린트 IU-R R0).
#   역할: "이 proposal_type이 상향인가/하향인가·±15% 변경폭 면제 대상인가·순위 스텝 타입인가"의
#   판별을 한 곳에 중앙화한다. 종전엔 guardrail_gate·naver_execution_harness·auto_operator·
#   diary_outcome에 `("bid_up","growth_bid_up")` 문자열이 독립 산재해, 새 UP 타입을 일부에만
#   넣으면 BEP/스톱로스/일예산 컨텍스트 누락(fail-open 우회)·가드 미인식이 발생했다(PLAN §1-3).
#
#   ★이 모듈은 앱 내 어떤 모듈도 import 하지 않는다(codex P2 — 순환 import 원천 차단. stdlib re만
#   사용). 모든 소비자(guardrail_gate·harness·auto_operator·diary_outcome)가 이 최말단 상수
#   모듈을 단방향 import 한다. R0는 행위 불변 리팩터 — 신규 타입을 도입하지 않고 기존 값만 단일
#   소스로 이관한다. R2는 파워링크 estimate 직행 타입 `bid_up_rank`를 3셋에 추가한다.
from __future__ import annotations

import re

# 상향 입찰 스텝 proposal_type(BEP·스톱로스·일예산 up-only 가드·일 1스텝 캡 카운터 트리거).
# ★IU-R R1(D-NAO-67 원리③): 쇼핑검색 폐루프 순위 서보 타입 `bid_up_servo` 추가 — UP 의미
#   (BEP·스톱로스·예산·쿨다운·일일상한·방향)는 bid_up과 완전히 동일하고, 다른 점은 ±15% 변경폭
#   면제(CHANGE_PCT_EXEMPT_TYPES)와 rank-step 의미(RANK_STEP_TYPES)뿐이다. 이 한 곳 등록으로
#   모든 가드(guardrail_gate up-only 검사·harness _build_guardrail_context up 브랜치·완전성
#   게이트·_ACTION_BY_PROPOSAL_TYPE update_bid 매핑·auto_operator 일 1스텝 캡 카운터)가 서보를
#   bid_up과 동형으로 인식한다(R0 레지스트리의 목적 — 부분 등록 시 fail-open 우회 차단, PLAN §1-3).
# ★IU-R R2(D-NAO-67 원리③): 파워링크(WEB_SITE=키워드) estimate 직행 타입 `bid_up_rank` 추가 —
#   목표 순위(현재−1)로 필요입찰(estimate)을 직행 산정하는 시간당 레인 inline UP. UP 의미는
#   bid_up과 동일하고, ±15% 면제·rank-step 의미(스톱로스 current 기준·신선도·TOCTOU)만 다르다.
# ★B-X BX2(D-NAO-70·71): 저볼륨 그룹 탐색 UP 타입 `bid_up_explore` 추가 — 핫셋 게이트(정착
#   클릭≥10) 밖 "죽는 캠페인" 그룹에 클릭 표본을 사는 능동 탐색 스텝(+30% 고정). UP 의미
#   (BEP·스톱로스·예산·쿨다운·방향)는 bid_up과 동일하나 두 가지가 다르다: ①±15% 변경폭
#   대신 **30% 상한**(EXPLORATION_STEP_TYPES, guardrail_gate._EXPLORATION_MAX_CHANGE_PCT — 완전
#   면제 아님, 30% 초과는 여전히 차단) ②표본-기반 BEP 완전성 게이트 면제(콜드 그룹은 정착 표본이
#   없어 BEP를 못 재므로 — 대체 가격 브레이크 = product_bep 연동 경제성 상한, exploration_ceiling).
#   rank-step 아님(estimate/서보 스텝 산정이 아니라 고정 +30% 래더 — base_bid 마커·신선도·TOCTOU
#   기계 미적용). explore_op 자동 실쓰기 승인원(exploration.APPROVAL_SOURCE_EXPLORE)에서만 발사.
# ★CS(콜드 스타트): 콜드 소재 첫 입찰 타입 `bid_up_cold` 추가. UP 의미(BEP·스톱로스·예산·
#   쿨다운·일일상한·방향)는 bid_up과 동일하고 ±15% 변경폭만 면제된다(CHANGE_PCT_EXEMPT_TYPES).
#   ★왜 면제인가: 이 타입의 존재 이유가 "50~300원에 방치된 신규 소재를 실제 시장가(수천 원)로
#   **한 번에** 올리는 것"이다. ±15%(또는 30%) 상한을 걸면 시장가까지 수십 일이 걸려(30% 상한
#   기준 300→1,400원에 6스텝·12시간 쿨다운) 레인 자체가 무의미해진다 — 그 느린 눈먼 래더가
#   바로 CS가 대체하려는 종전 동작이다(exploration.adaptive_step).
#   대체 브레이크(변경폭 상한을 대신하는 것들 — 면제가 무방비를 뜻하지 않음):
#     ① 경제성 상한: 첫 입찰 = min(이익상한, 목표순위 시장가) — 상한 초과 자체가 불가(SA3).
#     ② 소재당 **첫 1회만** 발사(cold_start_bid_lane이 change_log로 재발동 차단).
#     ③ BEP·스톱로스·일예산·쿨다운·일일상한 가드레일 전량 존치(면제는 변경폭 하나뿐).
#   rank-step 아님(estimate 순위 서보 산정이 아니라 시장가 직행 — base_bid TOCTOU 기계 미적용).
BID_UP_TYPES: frozenset[str] = frozenset(
    {"bid_up", "growth_bid_up", "bid_up_servo", "bid_up_rank", "bid_up_explore", "bid_up_cold"}
)

# 하향 입찰 스텝 proposal_type(안전방향 — 노출↓·지출↓). 종전 guardrail_gate._BID_DOWN_TYPES.
BID_DOWN_TYPES: frozenset[str] = frozenset({"bid_down"})

# D-NAO-20-③: ±15% 변경폭 상한(_MAX_CHANGE_PCT)만 면제되는 타입(신규/육성 트랙). BEP·스톱로스·
# 일예산·쿨다운·일일상한은 전량 존치 — 면제되는 것은 변경폭 상한 하나뿐(종전 _EXEMPT_FROM_CHANGE_PCT).
# ★IU-R R1: `bid_up_servo`는 "한 순위 위"에 필요한 입찰폭이 15%보다 클 수 있어(PLAN §1-1) 변경폭
#   면제 대상이다. 대체 상한 = 경제성 상한 + 서보 절대 스텝 캡 + 예산 pace 사전체크(harness/서보 측).
# ★IU-R R2: `bid_up_rank`도 "목표 순위 한 단 위"의 estimate 필요입찰이 15%보다 클 수 있어(PLAN
#   §1-1·D-NAO-20) 변경폭 면제. 대체 상한 = 경제성 상한(estimate>상한이면 상한까지) + 예산 pace.
# ★CS: `bid_up_cold`도 변경폭 완전 면제(위 BID_UP_TYPES 주석의 ①②③이 대체 브레이크).
CHANGE_PCT_EXEMPT_TYPES: frozenset[str] = frozenset(
    {"growth_bid_up", "bid_up_servo", "bid_up_rank", "bid_up_cold"}
)

# B-X BX2(D-NAO-70·71): 탐색 스텝 타입 — ±15% 대신 30% 변경폭 상한이 적용되는 UP 타입.
# guardrail_gate가 이 집합을 보고 _MAX_CHANGE_PCT(0.15) 대신 _EXPLORATION_MAX_CHANGE_PCT(0.30)로
# 변경폭을 검증한다(완전 면제 CHANGE_PCT_EXEMPT_TYPES와 **다름** — 30% 초과는 여전히 차단).
# ★CHANGE_PCT_EXEMPT_TYPES·RANK_STEP_TYPES와 서로소여야 한다(면제도 rank-step도 아님, 아래 invariant 테스트).
EXPLORATION_STEP_TYPES: frozenset[str] = frozenset({"bid_up_explore"})

# CS(콜드 스타트): 콜드 첫 입찰 스텝 타입 — **위임 경로 영구 제외** 대상(delegation_gate가 이
# 셋을 차집합으로 뺀다). rank-step·explore와 동일 철학이며, CS는 그 둘보다 이유가 더 강하다:
# 이 타입은 ±15% 변경폭이 **완전 면제**라(CHANGE_PCT_EXEMPT_TYPES) 유일한 상한이 레인이 산정한
# min(이익상한, 목표순위 시장가)이다. 위임(Ava agree 자동승인) 경로로 새면 그 산정 없이
# 무제한 상향이 되고, 킬스위치 화이트리스트(cold_op 전용)도 우회한다
# (approval_source='delegation'은 미등록). 영구 제외로 봉쇄한다.
COLD_START_STEP_TYPES: frozenset[str] = frozenset({"bid_up_cold"})

# CS 승인원 상수(String(12) 제약 — 7자). ★여기 두는 이유(적대적 리뷰 P3-12): harness가 킬스위치
# 확인·소재 UP 경계·면제 판정마다 이 값을 읽는데, 이 상수를 cold_start_bid_lane에 두면
# harness → cold_start_bid_lane → market_bid_probe → naver_sa_ad_fetcher(requests·자격증명) 체인을
# 매번 지연 import 하게 된다. 그 체인 어디가 깨지면 CS뿐 아니라 **approval_source가 있는 모든
# 제안 집행**이 죽는다. 승인원 상수는 의존 없는 최말단 모듈(이 파일)에 둔다.
# cold_start_bid_lane은 이 값을 재수출만 한다(호출부 호환).
APPROVAL_SOURCE_COLD = "cold_op"

# 순위(rank) 스텝 타입 — R1 쇼검 서보 `bid_up_servo` + R2 파워링크 estimate 직행 `bid_up_rank`.
# rank-step 타입은 (a) 스톱로스 base를 target_bid가 아니라 **스텝 전 current_bid**로 스위치하고
# (guardrail_gate._check_bid — 큰 스텝에서 target_bid가 커져 스톱로스가 실질 완화되는 것 방지),
# (b) harness 신선도 게이트(_RANK_STEP_MAX_AGE_MINUTES) + TOCTOU 방어(제안 시점 base_bid ≠ 실행
# 시점 라이브 bid면 fail-closed 중단) 대상이다(PLAN §2 R1·R2, codex 엣지·P1-5).
RANK_STEP_TYPES: frozenset[str] = frozenset({"bid_up_servo", "bid_up_rank"})

# 반응곡선(bid_rank_curve) 학습 원료 타입 — "Δ입찰 → Δ순위" 관측쌍으로 삼을 실집행 스텝.
# ★왜 RANK_STEP_TYPES를 재사용하면 안 되는가(2026-07-28 실측 사고): bid_rank_curve.
#   build_observations가 원료 필터로 RANK_STEP_TYPES를 쓰고 있었는데, 그 셋의 의미는 "스톱로스
#   base를 current_bid로 스위치 + harness 신선도/TOCTOU 기계 적용 대상"이지 "곡선 학습 표본"이
#   아니다. 서로 다른 세 의미가 한 셋에 얹혀 있었고, 그 결과 prod에서
#     · `bid_up_servo`·`bid_up_rank` = 역사상 실집행 **0건**(원료로 인정되지만 존재하지 않음)
#     · 실제 219건 실집행된 `bid_up_explore` + 평시 주력 `bid_up`·`growth_bid_up` = **원료에서 제외**
#   가 되어 NaverLearningState(metric="bid_rank_slope")가 **0행**이었다. 원료가 없으니
#   rank_servo는 항상 콜드스타트 상수(_COLD_START_SLOPE)로 폴백하고, EX의 marginal_stop 판정과
#   D-NAO-89 deep 예외(③bid_rank_slope 프라이어 존재)도 프라이어 부재로 함께 휴면했다.
#   → 곡선 원료라는 **세 번째 의미를 이 셋으로 분리**한다. RANK_STEP_TYPES는 가드레일/TOCTOU
#     의미 그대로 두므로 탐색·콜드에 신선도·base_bid TOCTOU 기계가 딸려 들어가지 않는다
#     (그 기계는 estimate 기반 순위 스텝 전용 — 위 BID_UP_TYPES 주석의 "rank-step 아님" 계약 보존).
# 곡선이 재는 것은 경매의 물리량(입찰을 올리면 순위가 얼마나 오르는가)이고, **스텝의 동기(서보·
# 탐색·육성·콜드)는 그 물리량과 무관**하다. 따라서 성공 실집행된 상향 스텝은 전부 표본이다.
# BID_UP_TYPES 별칭인 이유: 앞으로 새 UP 타입이 생기면 곡선 원료에도 자동 편입되는 것이 옳은
# 기본값이다(이번 사고가 정확히 "새 타입을 일부 셋에만 등록"해서 생긴 부분등록 누락이다).
# ★하향(bid_down)은 아직 제외: 부호가 반대인 사분면(Δbid<0 ∧ 순위 악화)이라 _fit_slope의 유효쌍
#   정의(개선폭>0 ∧ Δbid>0)를 함께 넓혀야 하고, 인하 시 순위 반응이 상향과 대칭이라는 근거가
#   아직 없다(경매 히스테리시스). 표본을 2배로 늘릴 후보이나 별도 검증 후 편입한다.
CURVE_SAMPLE_TYPES: frozenset[str] = BID_UP_TYPES


# ── rank-step TOCTOU 방어용 base_bid 마커(PLAN §2 R2 point6, codex P1-5) ──
# 제안 시점 current_bid(스텝 산정 기준가)를 expected_effect 텍스트에 기계판독 마커로 실어 보낸다.
# 신규 마이그레이션 금지(NaverProposal에 자유 int 컬럼 없음) → 기존 Text 컬럼(expected_effect)에
# 결정적 suffix로 인코딩. harness._execute_update_bid이 RANK_STEP_TYPES 실행 직전 이 base와
# 라이브 재조회 bid(_build_guardrail_context의 current_bid)를 대조해 변동 시 fail-closed 중단한다.
_BASE_BID_TAG = "servo_base_bid"
_BASE_BID_RE = re.compile(r"\[\[servo_base_bid=(\d+)\]\]")
# suffix 계약(encode가 항상 끝에 붙임) — 디코드는 끝 anchor로만 인정(codex R2 P2: 본문 오염
# 마커가 첫 매치로 잡히는 것 방지).
_BASE_BID_SUFFIX_RE = re.compile(r"\[\[servo_base_bid=(\d+)\]\]\s*$")


def encode_base_bid(expected_effect: str | None, base_bid: int) -> str:
    """expected_effect 끝에 제안 시점 base_bid 마커를 붙여 반환(TOCTOU 원료 영속)."""
    return f"{expected_effect or ''}\n[[{_BASE_BID_TAG}={int(base_bid)}]]"


def decode_base_bid(expected_effect: str | None) -> int | None:
    """expected_effect에서 base_bid 마커를 추출 — **엄격 모드**(codex R2 P1/P2):
    ① 마커는 정확히 1개여야 하고 ② suffix(끝) 위치여야 유효. 그 외(부재/중복/본문 오염
    위치)는 전부 None — 소비자(harness TOCTOU)가 None을 fail-closed로 처리한다."""
    if not expected_effect:
        return None
    if len(_BASE_BID_RE.findall(expected_effect)) != 1:
        return None
    m = _BASE_BID_SUFFIX_RE.search(expected_effect)
    return int(m.group(1)) if m else None


def strip_base_bid_marker(expected_effect: str | None) -> str | None:
    """표시용 — expected_effect에서 기계판독 마커(base_bid·explore_ceiling) 제거(콘솔/브리핑 사람
    노출 방지, GATE R2 P2-1). 저장값은 건드리지 않는다(TOCTOU·상한 원료 보존) — 표시 직전 레이어만."""
    if not expected_effect:
        return expected_effect
    return _CEILING_RE.sub("", _BASE_BID_RE.sub("", expected_effect)).rstrip()


# ── B-X BX3(D-NAO-70·71): 탐색 스텝 경제성 상한(ceiling) 마커(PLAN §3 BX2 GATE 이월 P2①) ──
# 레인 계산을 불신하고 harness 쓰기-경계에서 재검증하기 위해, run_hourly_lane이 산정한 경제성
# 상한(exploration.exploration_ceiling)을 expected_effect에 기계판독 마커로 실어 보낸다. harness
# _execute_update_bid이 EXPLORATION_STEP_TYPES 실행 직전 이 상한을 디코드해 target_bid>상한이면
# fail-closed 차단한다(D-NAO-71로 유일 가격 브레이크 = 이중 게이트). base_bid 마커와 동형(신규
# 마이그레이션 금지 — 기존 Text 컬럼 재사용, 끝 anchor suffix·단일 매치 엄격 디코드).
# ★탐색 스텝(codex P1)은 ceiling 마커 + base_bid 마커(TOCTOU) 둘 다 단다 — ceiling을 먼저 붙이고
#   base_bid를 끝(suffix)에 붙인다(base_bid decode의 strict-suffix 계약 보존). 그래서 ceiling decode는
#   suffix가 아니라 **정확히 1개 발생**(distinct tag)으로 판정한다(둘이 섞여도 각자 정확히 추출).
_CEILING_TAG = "explore_ceiling"
_CEILING_RE = re.compile(r"\[\[explore_ceiling=(\d+)\]\]")


def encode_exploration_ceiling(expected_effect: str | None, ceiling: int) -> str:
    """expected_effect 끝에 경제성 상한 마커를 붙여 반환(harness 쓰기-경계 재검증 원료 영속)."""
    return f"{expected_effect or ''}\n[[{_CEILING_TAG}={int(ceiling)}]]"


def decode_exploration_ceiling(expected_effect: str | None) -> int | None:
    """expected_effect에서 ceiling 마커를 추출 — **정확히 1개 발생**이어야 유효(distinct tag이라
    위치 무관·base_bid 마커가 뒤에 붙어도 정확 추출). 부재/중복(오염)은 None — 소비자(harness 탐색
    상한 게이트)가 None을 fail-closed로 처리한다."""
    if not expected_effect:
        return None
    matches = _CEILING_RE.findall(expected_effect)
    if len(matches) != 1:
        return None
    return int(matches[0])


# ── CS: 콜드 첫 입찰 상한(ceiling) 마커 ──────────────────────────────────────
# 존재 이유(적대적 리뷰 P1-3): `bid_up_cold`는 ±15% **완전 면제**라 변경 폭을 제한하는 가드가
# 하나도 없다. 유일한 가격 브레이크 = 레인이 산정한 min(이익상한, 목표순위 시장가). 그러므로
# 탐색(explore_ceiling)과 **동일한 규약**으로 그 상한을 expected_effect에 실어 보내고, harness
# 쓰기-경계에서 재검증한다 — 레인 계산을 불신하는 이 코드베이스의 확립된 규약을 CS만 어기면
# 안 된다. 마커 부재/중복(오염) = fail-closed(경제 근거 없이 콜드 상향 금지).
# 태그를 explore와 분리하는 이유: 한 제안이 두 성격을 동시에 갖는 일이 없어야 하고, 태그가
# 같으면 explore 게이트와 CS 게이트가 서로의 마커를 소비해 교차 통과가 생긴다.
_COLD_CEILING_TAG = "cold_ceiling"
_COLD_CEILING_RE = re.compile(r"\[\[cold_ceiling=(\d+)\]\]")


def encode_cold_ceiling(expected_effect: str | None, ceiling: int) -> str:
    """expected_effect 끝에 콜드 첫 입찰 상한 마커를 붙여 반환(쓰기-경계 재검증 원료 영속)."""
    return f"{expected_effect or ''}\n[[{_COLD_CEILING_TAG}={int(ceiling)}]]"


def decode_cold_ceiling(expected_effect: str | None) -> int | None:
    """expected_effect에서 콜드 상한 마커를 추출 — **정확히 1개 발생**이어야 유효.
    부재/중복(오염)은 None → 소비자(harness CS 상한 게이트)가 fail-closed 처리."""
    if not expected_effect:
        return None
    matches = _COLD_CEILING_RE.findall(expected_effect)
    if len(matches) != 1:
        return None
    return int(matches[0])


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
