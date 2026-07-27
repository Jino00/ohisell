# cold_start_bid_decider.py — SA3 cold_start_bid_decider (CS 스프린트, 단일 책임: 첫 입찰 결정)
# 역할(SA·순수함수): 이익 상한(SA1)과 시장가 사다리(SA2)를 받아 콜드 소재의 **첫 입찰가**를 정한다.
#   DB·API 접근 없음. SA1/SA2를 직접 호출하지 않는다(원칙18-6 — Harness가 값을 유통).
#
# 결정 규칙:
#   첫 입찰 = min(ceiling_cpc, 목표순위 시장가).  목표순위 기본 = **모바일 3위**
#     (이익극대 스팟밴드 2.5~4의 중앙 — 메모리 naver-ad-profit-spot-bands).
#   목표순위 시장가 > ceiling  → ceiling에서 시작(D-NAO-91: BEP 우선, 순위는 시장이 주는 대로 수용).
#   시세가 floor/무의미      → 제안 보류(no-op). 근거 없이 임의값을 넣지 않는 것이 이 스프린트의 존재 이유.
#   ceiling < 사다리 최저가  → **경제성 없음 판정 + 경보**(제안 없음).
#
# ★최소노출입찰가를 게이트로 쓰지 않는 이유(라이브 실측 2026-07-27 — 지시 설계와 다른 점):
#   지시는 "최소노출가보다 낮으면 노출 안 되므로 ceiling < 최소노출가면 경제성 없음 경보"였다.
#   그러나 prod 실측에서 최소노출가는 순위 사다리의 하한이 아니었다 —
#     nad-…558730: pos1=3,420 pos2=3,100 pos3=2,810 pos4=2,630 인데 expmin=3,270 (pos2~4보다 높다)
#     nad-…558731: pos1=3,210 … pos4=2,280 인데 expmin=2,560
#   최소노출가가 진짜 하한이라면 그 아래 순위들은 애초에 존재할 수 없다. 두 엔드포인트는 서로 다른
#   추정 모델이며, 실제로 해당 소재들은 지금 그 순위대에 노출 중이다. 이 값을 하드 게이트로 쓰면
#   현재 정상 노출 중인 소재까지 전량 "노출 불가"로 오판한다.
#   → 경제성 판정 기준을 **사다리 최저가(= 가장 싼 유효 순위 4위)**로 바꾼다. 최소노출가는
#     참고 신호로 결과에 실어 보내되 판정에는 쓰지 않는다.
from __future__ import annotations

from decimal import Decimal

# 목표 순위 기본값 — 이익극대 스팟밴드(2.5~4)의 중앙. 1·2위는 밴드보다 비싸고(볼륨 2.4배·이익 1/3),
# 4위는 밴드 하단. 3위가 밴드 안에서 가장 방어적인 진입점이다.
DEFAULT_TARGET_POSITION = 3
DEFAULT_DEVICE = "MOBILE"
_BID_INCREMENT = 10
# ★리뷰 P2-9/P2-8: 저신뢰 상한 할인 계수.
#   SA1이 `confident=False`(계정 층 폴백)를 라벨로 돌려주는데 종전엔 그 라벨이 **어떤 판정도
#   하지 않았다** — "표본 빈약이면 공격적 입찰 금지"라는 명세와 구현이 어긋나 있었다.
#   계정 층 RPC는 판매가가 서로 다른 여러 상품의 혼합이라 "판매가가 소거된다"는 상한 유도가
#   그 층에서는 엄밀히 성립하지 않는다(싼 상품이 비싼 상품과 섞이면 상한이 과대 산출 = 과지출
#   방향). 그래서 그 층 상한에는 보수 계수를 곱한다. 0.7 = 임의 상수가 아니라 "혼합 층 상한은
#   자기 층 상한보다 낙관적일 수 있으니 3할 깎는다"는 명시적 보수 선택 — 근거가 생기면(층별
#   과대 편향 실측) 이 값을 교체할 것.
LOW_CONFIDENCE_CEILING_FACTOR = Decimal("0.7")
# 쇼핑 유효 입찰 하한(원) — exploration._EXPLORATION_MIN_BID_SHOPPING과 동일 출처.
_MIN_BID_SHOPPING = 50

# 판정값(decision) — 호출부/브리핑이 문자열로 분기한다.
DECISION_PROPOSE = "propose"          # 첫 입찰 제안 있음
DECISION_HOLD_NO_MARKET = "hold_no_market"      # 시세 없음/floor → 보류
DECISION_HOLD_NO_CEILING = "hold_no_ceiling"    # 경제 상한 못 세움 → 보류
DECISION_NOT_VIABLE = "not_viable"    # 경제성 없음(상한 < 최저 시장가) → 경보
DECISION_HOLD_NO_CHANGE = "hold_no_change"      # 산출값이 현재 입찰과 같거나 낮음 → 보류


def _round_down(bid: Decimal | int) -> int:
    """10원 단위 내림(네이버 유효 입찰 규격 — 10 배수가 아니면 API가 400)."""
    return int(int(bid) // _BID_INCREMENT * _BID_INCREMENT)


def decide_cold_start_bid(
    *,
    ceiling: dict | None = None,
    market: dict | None = None,
    current_bid: int = 0,
    target_position: int = DEFAULT_TARGET_POSITION,
    device: str = DEFAULT_DEVICE,
) -> dict:
    """콜드 소재의 첫 입찰 결정(순수).

    ceiling: SA1 bid_ceiling_calculator.compute_ceiling 반환(optional 파라미터 — 원칙18-8).
             {"ceiling_cpc", "rpc", "rpc_source", "sample_clk", "confident", "bep_roas", ...}
    market:  SA2 market_bid_probe.load_today_ladder/probe_market_bids 반환(optional).
             {"ladder": {position: bid}, "exposure_min": int|None, "is_floor": bool}
    current_bid: 소재의 현재 실효 입찰(원). 0/미상이면 0.

    반환 dict:
      decision, target_bid(int|None), reason(한국어),
      ceiling_cpc, market_bid(목표순위 시장가), ladder_min(사다리 최저가),
      exposure_min(참고), rpc_source, sample_clk, confident, target_position, device
    """
    ceiling = ceiling or {}
    market = market or {}
    ladder = {int(k): int(v) for k, v in (market.get("ladder") or {}).items()}
    raw_ceiling = int(ceiling.get("ceiling_cpc") or 0)
    exposure_min = market.get("exposure_min")

    # 저신뢰(계정 층 폴백) 상한 할인 — 리뷰 P2-9/P2-8. 라벨이 판정에 실제로 쓰이게 한다.
    confident = bool(ceiling.get("confident"))
    if raw_ceiling > 0 and not confident:
        ceiling_cpc = _round_down(Decimal(raw_ceiling) * LOW_CONFIDENCE_CEILING_FACTOR)
    else:
        ceiling_cpc = raw_ceiling

    out = {
        "decision": None, "target_bid": None, "reason": "",
        "ceiling_cpc": ceiling_cpc,
        "raw_ceiling_cpc": raw_ceiling,
        "market_bid": ladder.get(target_position),
        "ladder_min": min(ladder.values()) if ladder else None,
        "ladder": ladder,
        "exposure_min": exposure_min,
        "rpc_source": ceiling.get("rpc_source", "none"),
        "sample_clk": ceiling.get("sample_clk", 0),
        "confident": bool(ceiling.get("confident")),
        "target_position": target_position,
        "device": device,
        "current_bid": current_bid,
    }

    # ① 시세 없음/무의미 — 근거 없는 임의값 금지(no-op).
    if market.get("is_floor") or not ladder:
        out["decision"] = DECISION_HOLD_NO_MARKET
        out["reason"] = (
            "시세 무의미(floor/미관측) — 시장가 근거 없어 첫 입찰 보류. "
            f"floor_reason={market.get('floor_reason') or '사다리 없음'}"
        )
        return out

    # ② 경제 상한 없음 — BEP/RPC 근거 부재.
    if ceiling_cpc <= 0:
        out["decision"] = DECISION_HOLD_NO_CEILING
        out["reason"] = f"경제 상한 없음 — {ceiling.get('reason') or 'ceiling_cpc=0'}"
        return out

    ladder_min = out["ladder_min"]

    # ③ 경제성 없음 — 상한이 **가장 싼 유효 순위**보다도 낮다.
    #    = 이 상품은 현재 시장에서 이익을 내며 사다리 안(1~4위)에 노출될 수 없다는 뜻.
    #    ★중요한 신호이므로 조용히 보류하지 않고 별도 판정값으로 올린다.
    if ceiling_cpc < ladder_min:
        # ★f-string 안에 같은 따옴표 f-string/첨자를 중첩하지 말 것(PEP 701 = Python 3.12+,
        #   **prod는 3.10**). 조각을 미리 만들어 쓴다 — tests/test_prod_python_syntax_compat.py 참조.
        _rpc = ceiling.get("rpc")
        rpc_note = f"{_rpc:.0f}" if _rpc else "미상"
        out["decision"] = DECISION_NOT_VIABLE
        out["reason"] = (
            f"경제성 없음 — 이익 상한 {ceiling_cpc:,}원 < 사다리 최저가(4위권) {ladder_min:,}원. "
            f"현 RPC({rpc_note}원·출처 {out['rpc_source']}·"
            f"clk {out['sample_clk']})·BEP {ceiling.get('bep_roas')} 로는 1~4위 어디에도 "
            "이익 내며 노출 불가."
        )
        return out

    # ④ 정상 — min(상한, 목표순위 시장가). 목표순위 시세가 없으면 폴백.
    #    ★리뷰 P2-10: 폴백은 **더 싼 쪽(하위 순위 = 큰 숫자)으로만** 허용한다. 종전엔 절대거리
    #    최근접이라 3위가 없으면 2위(더 비싼 자리) 가격을 "3위 시세"로 지불할 수 있었다.
    #    사다리 부분 실패(fetch_position_ladder가 순위별 배치 실패를 건너뜀)는 정상 동작이므로
    #    이 상황은 실제로 발생한다. 더 싼 순위가 하나도 없으면 근거 부족으로 보류한다.
    market_bid = ladder.get(target_position)
    if market_bid is None:
        cheaper = [p for p in ladder if p > target_position]
        if not cheaper:
            out["decision"] = DECISION_HOLD_NO_MARKET
            out["reason"] = (
                f"목표 {target_position}위 시세 부재 ∧ 그보다 하위(더 싼) 순위 시세도 없음"
                f"(관측 순위={sorted(ladder)}) — 더 비싼 자리 가격을 목표가로 쓰지 않는다(보류)."
            )
            return out
        nearest = min(cheaper)
        market_bid = ladder[nearest]
        out["market_bid"] = market_bid
        out["reason"] = f"목표 {target_position}위 시세 부재 — 하위 최근접 {nearest}위 시세 사용. "

    target = _round_down(min(ceiling_cpc, market_bid))
    if target < _MIN_BID_SHOPPING:
        out["decision"] = DECISION_NOT_VIABLE
        out["reason"] += (
            f"산출 입찰 {target}원이 쇼핑 유효 하한({_MIN_BID_SHOPPING}원) 미만 — 제안 불가."
        )
        return out

    # ⑤ 현재 입찰보다 높지 않으면 이 레인이 할 일이 없다(콜드 첫 입찰은 상향 목적).
    if current_bid and target <= current_bid:
        out["decision"] = DECISION_HOLD_NO_CHANGE
        out["target_bid"] = None
        out["reason"] += (
            f"산출 입찰 {target:,}원이 현재 {current_bid:,}원 이하 — 상향 없음(보류). "
            f"상한 {ceiling_cpc:,}·시장가({target_position}위) {market_bid:,}"
        )
        return out

    bound = "상한" if ceiling_cpc <= market_bid else f"시장가({target_position}위)"
    # ★f-string 안에 **같은 따옴표** f-string을 중첩하지 말 것 — PEP 701(Python 3.12+) 문법인데
    #   **prod는 Python 3.10**이다(로컬 3.14에서는 통과해 테스트가 못 잡았고, 배포 후 이 줄에서
    #   SyntaxError로 모듈 import가 통째로 죽었다). 조각을 미리 만들어 붙인다.
    conf_note = "" if confident else (
        f"·표본 빈약 → 상한 {raw_ceiling:,}에 {LOW_CONFIDENCE_CEILING_FACTOR} 할인 적용"
    )
    expmin_note = f", 참고 최소노출가 {exposure_min:,}원" if exposure_min else ""
    out["decision"] = DECISION_PROPOSE
    out["target_bid"] = target
    out["reason"] += (
        f"콜드 첫 입찰 {target:,}원 = min(이익상한 {ceiling_cpc:,}, "
        f"{target_position}위 시장가 {market_bid:,}) — {bound}이 결정. "
        f"RPC 출처 {out['rpc_source']}(clk {out['sample_clk']}{conf_note})"
        + expmin_note
    )
    return out
