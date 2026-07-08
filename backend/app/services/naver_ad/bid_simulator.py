# bid_simulator.py — bid_simulator SA (P2-S3 T2, D-NAO-19)
# 역할(SA): 계층 베이지안 수축으로 클릭당매출(RPC)을 풀링하고, 경제성 상한과 estimate
#   목표순위 입찰가 중 낮은 쪽을 추천 입찰가로 산출. 순수 함수 — 광고 API 쓰기 없음(D-3).
#   SA간 직접 호출 금지(원칙18) — harness(proposal_pipeline, T5)가 진단 보드·estimate를
#   precompute해 넘겨준다(재조회 금지, N+1 방지).
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from app.services.naver_ad.account_diagnosis import LOW_CLICK_THRESHOLD

_Q4 = Decimal("0.0001")
_SHRINK_K = Decimal(str(LOW_CLICK_THRESHOLD))  # 10 — D-NAO-9 저클릭 게이트와 동일 상수(계층 수축 강도)

# 라이브검증(2026-07-07, T8, 원칙22): /estimate/performance-bulk가 "invalid collections size"로
# 거부한 입찰가를 역추적해 확정 — 네이버 SA 입찰가는 70~100,000원, **10원 단위만 유효**
# (71/73/1024/1025 등 10 배수가 아니면 400, 69원 이하는 range 오류). affordable_ceiling이
# 계산한 임의 정수를 그대로 추천하면 estimate/실제 입찰 등록 모두에서 거부당한다.
_MIN_BID = 70
_MAX_BID = 100_000
_BID_INCREMENT = 10


def _level_rpc(clk: int, conv_amt: int, prior: Decimal) -> Decimal:
    """관측 RPC(conv_amt/clk)를 상위 prior로 수축(경험적 베이즈, pseudo-count=_SHRINK_K).

    clk이 _SHRINK_K보다 훨씬 작으면 prior 비중이 커지고, 훨씬 크면 관측값(raw)에 수렴한다.
    clk=0이면 raw는 정의되지 않지만 가중치도 0이라 결과에 영향 없음(0으로 안전 대체).
    """
    raw = (Decimal(conv_amt) / Decimal(clk)) if clk else Decimal("0")
    n = Decimal(clk)
    return (n * raw + _SHRINK_K * prior) / (n + _SHRINK_K)


def pooled_rpc(keyword_row: dict, group_agg: dict, campaign_agg: dict, account_agg: dict) -> Decimal:
    """계층 베이지안 수축: 키워드→그룹→캠페인→계정 클릭당매출(RPC), 원 단위(**보정 전 raw**).

    각 인자는 {"clk": int, "conv_amt": int}(그 레벨 raw 합계 — harness가 회당 1회 precompute해
    전달, 재조회 금지). 계정 레벨은 무조건 최상위 prior라 그 자신의 prior가 없음 — 계정
    전체에 클릭이 없으면(사실상 불가능하지만) 0으로 폴백, affordable_ceiling의 division
    guard가 이어서 처리한다.
    """
    account_clk = account_agg.get("clk", 0)
    account_rpc = (
        Decimal(account_agg.get("conv_amt", 0)) / Decimal(account_clk)
    ) if account_clk else Decimal("0")
    campaign_rpc = _level_rpc(campaign_agg.get("clk", 0), campaign_agg.get("conv_amt", 0), account_rpc)
    group_rpc = _level_rpc(group_agg.get("clk", 0), group_agg.get("conv_amt", 0), campaign_rpc)
    keyword_rpc = _level_rpc(keyword_row.get("clk", 0), keyword_row.get("conv_amt", 0), group_rpc)
    return keyword_rpc.quantize(_Q4)


def affordable_ceiling(rpc_corrected: Decimal, target_roas: Decimal) -> int:
    """경제성 상한(원) = 보정 클릭당매출 ÷ target_roas.

    CVR×객단가 = (전환/클릭)×(매출/전환) = 매출/클릭 = RPC이므로 전환건수·AOV를 따로 분해할
    필요가 없다(계획서 §3.2 확정, codex #6/#8의 AOV 소스 불명 문제를 이 단순화로 해소).
    target_roas<=0은 호출측 설정 오류(ValueError). rpc_corrected<=0(매출 실적 없음)은 division
    guard — 0원 반환(입찰 근거 없음 = 인상 불가 신호, 예외 아님).

    결과는 실측 확정 유효 입찰가 규격(70~100,000원, 10원 단위)으로 내림 반올림한다 — 그렇지
    않으면 estimate 호출과 실제 입찰 등록 양쪽에서 거부당한다(라이브검증, docs/references/23).
    내림 결과가 최소입찰가(70원) 미만이면 그 입찰가로는 프로필상 이미 수익성이 없다는 뜻이라
    0을 반환(70원으로 올림 강제하지 않음 — 보수적 상한 원칙 유지).
    """
    if target_roas <= 0:
        raise ValueError(f"target_roas는 0보다 커야 함: {target_roas}")
    if rpc_corrected <= 0:
        return 0
    ceiling = rpc_corrected / target_roas
    ceiling_int = int(ceiling.to_integral_value(rounding=ROUND_DOWN))
    rounded = (ceiling_int // _BID_INCREMENT) * _BID_INCREMENT  # 10원 단위 내림
    if rounded < _MIN_BID:
        return 0
    return min(rounded, _MAX_BID)


def simulate_bid(
    keyword_row: dict,
    target_roas: Decimal,
    *,
    group_agg: dict,
    campaign_agg: dict,
    account_agg: dict,
    correction_factor: Decimal = Decimal("1"),
    estimate: dict | None = None,
    learning_state: dict | None = None,
    is_new_or_growth: bool = False,
) -> dict:
    """최종 추천입찰 = min(경제성 상한, estimate 목표순위 필요입찰).

    keyword_row: {"clk": int, "conv_amt": int, "bid_amt": int|None(현재 입찰가, entity_sync 소스)}.
    estimate: harness가 fetcher.estimate_average_position_bid(+estimate_performance)로 미리
      조회해 넘기는 묶음(부분 실패 시 필드별 None — capability_flags로 표시, 전체 실패로
      취급하지 않음, codex #11):
      {"rank_bid": int|None, "rank_position": int|None, "device": str|None,
       "predicted_clicks": int|None}. None 전체를 넘기면 estimate 완전 미조회로 처리.
    learning_state: {"estimate_bias": {"factor": Decimal, "confidence": Decimal}} (Phase 6
      학습루프2, D-NAO-14) — factor=실측/예측 클릭 비율(estimate_calibrator 산출). estimate의
      predicted_clicks에 곱해 expected_effect_text의 "예상매출" 표시만 보정한다 —
      recommended_bid/economic_ceiling 계산에는 관여하지 않는다(학습은 제안 "품질"만 높이고
      권한을 넓히지 않는다는 D-NAO-14 경계 — 입찰 자체는 여전히 RPC/target_roas 산식만 따름).
      None이거나 factor<=0이면 미보정(원본 predicted_clicks 그대로).
    is_new_or_growth: D-NAO-20(신규/육성 100% 진입, 쿠팡의 ×0.5 변경페널티는 네이버엔 적용 안 함)
      라벨만 부착 — 계산 분기 없음, 표기 목적.

    반환: {recommended_bid, economic_ceiling, rank_bid, direction(up/down/hold), basis,
           expected_effect_text, capability_flags}.
    변경 게이트(D-NAO-19-②: ROAS/추세/모수 기반 인상·인하·판정유보 조건)는 여기서 실행하지
    않는다 — rationale 텍스트 근거로만 proposal_writer(T3)가 기록(실행 없음, 계획서 명시).
    """
    estimate = estimate or {}
    rpc_raw = pooled_rpc(keyword_row, group_agg, campaign_agg, account_agg)
    rpc_corrected = (rpc_raw * correction_factor).quantize(_Q4)
    economic_ceiling = affordable_ceiling(rpc_corrected, target_roas)

    rank_bid = estimate.get("rank_bid")
    if rank_bid is not None:
        recommended_bid = min(economic_ceiling, rank_bid)
        basis = "economic_ceiling" if economic_ceiling <= rank_bid else "rank_target"
    else:
        recommended_bid = economic_ceiling
        basis = "economic_ceiling_only"  # estimate 미조회 — 순위 상한 없이 경제성 상한만 적용

    current_bid = keyword_row.get("bid_amt")
    if current_bid is None or recommended_bid == current_bid:
        direction = "hold"
    else:
        direction = "up" if recommended_bid > current_bid else "down"

    device = estimate.get("device")
    device_note = f"기기가정={device}(지배기기)" if device else "기기가정 없음(estimate 미조회)"
    predicted_clicks = estimate.get("predicted_clicks")
    if predicted_clicks is not None:
        bias = (learning_state or {}).get("estimate_bias") or {}
        bias_factor = bias.get("factor")
        calibrated = (
            (Decimal(predicted_clicks) * bias_factor).quantize(_Q4)
            if bias_factor is not None and bias_factor > 0 else Decimal(predicted_clicks)
        )
        predicted_revenue = (calibrated * rpc_corrected).quantize(_Q4)
        bias_note = f", 학습보정계수={bias_factor}(estimate_calibrator)" if bias_factor is not None and bias_factor > 0 else ""
        expected_effect_text = (
            f"추정클릭 {predicted_clicks}{bias_note} × 보정RPC {rpc_corrected}원 ≈ 예상매출 {predicted_revenue}원"
            f"(estimate는 클릭/비용만 반환·전환 아님 — 가정 기반 범위, false precision 아님) {device_note}"
        )
    else:
        expected_effect_text = (
            f"경제성 상한 {economic_ceiling}원 기준(성과 estimate 미조회 — 클릭 예측 불가) {device_note}"
        )

    capability_flags = {
        "estimate_ok": rank_bid is not None,
        "performance_estimate_ok": predicted_clicks is not None,
        "is_new_or_growth": is_new_or_growth,
        "keyword_sample_thin": keyword_row.get("clk", 0) < LOW_CLICK_THRESHOLD,
    }

    return {
        "recommended_bid": recommended_bid,
        "economic_ceiling": economic_ceiling,
        "rank_bid": rank_bid,
        "direction": direction,
        "basis": basis,
        "expected_effect_text": expected_effect_text,
        "capability_flags": capability_flags,
    }
