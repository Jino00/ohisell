# rank_servo.py — 쇼핑검색 폐루프 순위 서보 SA (스프린트 IU-R R1, D-NAO-67 원리③).
# 역할(SA): 관측 가중순위(weighted_rank)를 "한 단 위"로 올리기 위한 입찰 스텝 크기를
#   산정하는 순수 함수. 순위는 목표가 아니라 결과이고(§0), 이 함수는 ROAS 게이트가 이미
#   UP을 승인한 뒤 "관측 순위 한 단 위로" 래칫하는 스텝의 크기만 낸다. ROAS 게이트 통과
#   여부는 harness가 _judge_hourly 확장 verdict로 판정해 서보 호출 자체를 게이팅한다 —
#   이 함수는 순위/입찰/표본/상한이라는 **구조화 값만** 받고 문자열 rationale에 의존하지
#   않는다(codex 지적1).
#
#   부수효과 0(DB·API 접근 없음 — bid_simulator·guardrail_gate와 동일 규율). SA간 직접 호출
#   금지(원칙18-6): rank_servo는 intraday_roas·_judge_hourly·economic_ceiling 산식을 모른다 —
#   harness(auto_operator.run_hourly_lane)가 economic_ceiling·weighted_rank·imp_sum·
#   response_prior를 precompute해 optional 입력(원칙18-8)으로 넘긴다.
#
#   무영속 래칫(§난제2): 서보 상태(목표·직전 스텝·정지)를 저장하지 않는다 — 매 실행 현재
#   관측순위로 목표=현재순위−1을 재계산한다. "정지(rest)"는 목표 도달이 아니라 ROAS 게이트
#   미통과·예산 소진·순위 무반응·가드 차단이 발생한 지점이다(§0 래칫 의미론).
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_DOWN

# §실측1(추천 0.3 잠정) — 쇼검 avg_rank 노이즈 관망대. 목표(현재순위−1)에 근접한 진동
# 구간에서는 스텝을 내지 않는다(수렴이 아니라 데드밴드 관망, codex 지적3·4). 실측 필요.
_SERVO_DEADBAND = Decimal("0.3")
# §실측2(추천 0.15 잠정) — 콜드스타트(response_prior 없음) 기본 스텝 비율. 반응곡선(R3)이
# 없으면 보수 기본 스텝으로 시작해 폐루프가 반복 보정한다(무근거 큰 점프 금지). 실측 필요.
_SERVO_DEFAULT_STEP_PCT = Decimal("0.15")
# §실측3(추천 0.50 잠정) — 서보 절대 스텝 캡. ±15%를 면제해도 노이즈 1건이 입찰을 폭주
# 시키는 것을 절대치로 차단한다(현재가 대비 최대 +50%). 실측/튜닝 필요.
_SERVO_MAX_STEP_PCT = Decimal("0.50")

# 표본 게이트 — auto_operator._MIN_HOURLY_SAMPLE_IMP과 동일 값(imp 합 < 30이면 판정 보류).
# 순환 import 회피(rank_servo는 최말단 순수 모듈)를 위해 값만 이 모듈에 복제한다 — harness가
# imp_sum을 넘기고 서보는 자체적으로도 fail-closed hold(이중 방어, codex 지적5).
_MIN_HOURLY_SAMPLE_IMP = 30

# 네이버 SA 유효 입찰가 규격(bid_simulator._MIN_BID/_MAX_BID/_BID_INCREMENT와 동일 — 라이브
# 검증 2026-07-07: 70~100,000원·10원 단위만 유효, 그 밖은 estimate/등록 모두 거부).
_MIN_BID = 70
_MAX_BID = 100_000
_BID_INCREMENT = 10


def _hold(reason: str, target_rank: int = 0) -> dict:
    """스텝 없음(관찰) — target_bid=None. unresponsive는 무영속 재구성(R3 harness) 몫이라
    이 순수 함수에선 항상 False(계약 형태만 유지, PLAN §2 R1 출력 스키마)."""
    return {"target_bid": None, "target_rank": target_rank, "step_reason": reason, "unresponsive": False}


def decide_servo_step(
    weighted_rank,
    current_bid: int,
    *,
    imp_sum: int,
    economic_ceiling: int,
    response_prior=None,
) -> dict:
    """관측 가중순위를 한 단 위로 래칫하는 입찰 스텝을 산정(순수).

    입력(전부 구조화 값 — codex 지적1):
      weighted_rank: imp-가중 3시간 창 평균 순위(Decimal|float|None). None=순위 전부 null.
      current_bid:   현재 입찰가(원, int).
      imp_sum:       최근 3시간 창 노출 합(표본 게이트).
      economic_ceiling: 경제성 상한(원, int) — harness가 pooled_rpc→affordable_ceiling으로
                     precompute. rpc≤0(수익 여력 없음)이면 0(입찰 근거 없음).
      response_prior: R3 반응곡선 기울기(원/rank 개선 1.0, 양수)|None. 없으면 콜드스타트 폴백.

    출력: {"target_bid": int|None, "target_rank": int, "step_reason": str, "unresponsive": bool}.
      target_bid=None이면 hold(관찰) — fail-closed·최상단·데드밴드·근거부족·무효 스텝.

    로직(PLAN §2 R1):
      ①fail-closed hold(codex 지적5): weighted_rank None / imp_sum<30 / economic_ceiling≤0.
      ②최상단 가드(codex P1-4): weighted_rank ≤ 1+deadband → converged hold(더 올릴 순위 없음).
      ③목표 순위 = max(1, ceil(weighted_rank)−1)(codex 지적2 — floor 아님).
      ④데드밴드 관망(codex 지적3·4): |weighted_rank − target_rank| ≤ deadband → hold(진동 차단).
      ⑤스텝 크기: response_prior 있으면 기울기×개선폭으로 필요 증분 근접, 없으면 콜드스타트
        보수 기본 스텝(current×default_pct 증분).
      ⑥반올림 순서 못박음(codex 엣지): 산정 → 서보 캡·경제성 상한·_MAX_BID 클램프 → 10원
        내림 → current 초과·≥70원이어야 유효(아니면 None hold).
    """
    # ① fail-closed hold — 근거 없음.
    if weighted_rank is None:
        return _hold("weighted_rank None(순위 전부 null) — 근거 없음(fail-closed)")
    if imp_sum < _MIN_HOURLY_SAMPLE_IMP:
        return _hold(f"imp_sum={imp_sum}<{_MIN_HOURLY_SAMPLE_IMP}(표본 부족) — fail-closed")
    if economic_ceiling <= 0:
        return _hold("economic_ceiling≤0(수익 여력 없음/심층 콜드) — fail-closed")

    wr = Decimal(str(weighted_rank))

    # ② 최상단 가드 — 1위권은 더 올릴 순위가 없다(converged hold).
    if wr <= Decimal(1) + _SERVO_DEADBAND:
        return _hold(
            f"최상단 converged(weighted_rank={float(wr):.2f}≤1+{float(_SERVO_DEADBAND)}) — 더 올릴 순위 없음",
            target_rank=1,
        )

    # ③ 목표 순위 = ceil(현재)−1(한 단 위), 최소 1.
    ceil_rank = int(wr.to_integral_value(rounding=ROUND_CEILING))
    target_rank = max(1, ceil_rank - 1)

    # ④ 데드밴드 관망 — 목표에 근접(진동 구간)하면 스텝 안 냄(수렴 아님, 매 실행 재계산 래칫).
    delta = wr - Decimal(target_rank)
    if abs(delta) <= _SERVO_DEADBAND:
        return _hold(
            f"데드밴드 관망(|{float(wr):.2f}−{target_rank}|={float(abs(delta)):.2f}≤{float(_SERVO_DEADBAND)}) — 진동 차단",
            target_rank=target_rank,
        )

    # ⑤ 스텝 크기(원).
    if response_prior is not None and Decimal(str(response_prior)) > 0:
        # "한 단 위(rank 개선 delta)"에 필요한 입찰 증분 ≈ 기울기(원/rank) × 개선폭.
        prior = Decimal(str(response_prior))
        increment = prior * delta  # delta>deadband>0(위에서 보장)
        raw_target = Decimal(current_bid) + increment
        step_basis = f"prior 기울기 {float(prior):.1f}원/rank × 개선폭 {float(delta):.2f}"
    else:
        # 콜드스타트 — 보수 기본 스텝(current×default_pct 증분). 폐루프가 반복 보정.
        raw_target = Decimal(current_bid) * (Decimal(1) + _SERVO_DEFAULT_STEP_PCT)
        step_basis = f"콜드스타트 기본 스텝 +{float(_SERVO_DEFAULT_STEP_PCT):.0%}"

    # ⑥ 클램프 순서: 서보 절대 캡·경제성 상한·_MAX_BID → 10원 내림 → 유효성.
    servo_cap = Decimal(current_bid) * (Decimal(1) + _SERVO_MAX_STEP_PCT)
    clamped = min(raw_target, servo_cap, Decimal(economic_ceiling), Decimal(_MAX_BID))
    stepped = int((clamped // _BID_INCREMENT) * _BID_INCREMENT)  # UP=10원 내림
    if stepped <= current_bid or stepped < _MIN_BID:
        # 상한(경제성/캡)이 현재가 이하로 눌러 스텝이 사라졌거나 최소입찰가 미만 — 근거 없음.
        return _hold(
            f"유효 스텝 없음(산정={step_basis} → 캡·상한 클램프 후 {stepped}원 ≤ 현재 {current_bid}원 or <70원)",
            target_rank=target_rank,
        )
    return {
        "target_bid": stepped,
        "target_rank": target_rank,
        "step_reason": (
            f"목표순위 {target_rank}(관측 {float(wr):.2f}) — {step_basis} → {stepped}원"
            f"(현재 {current_bid}원·경제성상한 {economic_ceiling}원·서보캡 +{float(_SERVO_MAX_STEP_PCT):.0%})"
        ),
        "unresponsive": False,
    }
