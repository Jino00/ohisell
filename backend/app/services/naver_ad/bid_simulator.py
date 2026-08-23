# bid_simulator.py — bid_simulator SA (P2-S3 T2, D-NAO-19)
# 역할(SA): 계층 베이지안 수축으로 클릭당매출(RPC)을 풀링하고, 경제성 상한과 estimate
#   목표순위 입찰가 중 낮은 쪽을 추천 입찰가로 산출. 순수 함수 — 광고 API 쓰기 없음(D-3).
#   SA간 직접 호출 금지(원칙18) — harness(proposal_pipeline, T5)가 진단 보드·estimate를
#   precompute해 넘겨준다(재조회 금지, N+1 방지).
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from app.services.naver_ad import correction_interval
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
    correction_factor_low: Decimal | None = None,
    correction_factor_high: Decimal | None = None,
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

    # ★D-NAO-230 안3 «구간 자» — 이 함수는 **한 값이 두 역할**을 한다(census 93 §1 7행 발견):
    # economic_ceiling은 bid_up을 누르는 «상향 상한»이면서, 동시에 current_bid 아래로 내려가면
    # 그 자체로 direction="down"을 **만들어내는** «브레이크»다. 그래서 「브레이크=상한·액셀=하한」
    # 이분법을 한 값에 그대로 못 씌운다.
    # ⇒ ★★D-NAO-231(Jino 결정 2026-08-23)의 규칙을 그대로 적용한다:
    #   **«어느 방향인가»(선정)는 상한으로 정하고, «얼마나»(실쓰기 크기)만 하한으로 누른다.**
    #   - direction: 상한 기준 — 오늘과 **동일한 판정**이다(액셀 판정 불변 = 금지선 2 이행).
    #   - up 일 때 크기: 하한 상한선(rec_low) — 덜 올린다. rec_low가 현재 입찰 이하면
    #     구간이 현재 입찰을 **가로지르는** 것이고, 그때는 **방향을 뒤집지 않고**(D-NAO-236)
    #     크기만 최소 한 틱으로 눌러 두 층을 동시에 만족시킨다. 아래 ★D-NAO-236 주석 참조.
    #     ⚠️2026-08-24 이전 판은 여기서 hold로 뒤집었다(`basis="interval_floor_blocks_up"`) —
    #       그게 하한 인하 시 액셀 24%를 조용히 없앤 자리다.
    #   - down 일 때 크기: 상한 상한선(rec_high) — 덜 내린다(브레이크는 상한, 안3 원문).
    # 두 끝을 명시하지 않으면 받은 단일 계수에서 **유도**한다.
    # ★★D-NAO-234: 유도식은 `correction_interval.interval_ends`가 정본이다 — 예전엔 같은 식이
    # 여기와 `diagnosis._as_interval` **두 곳**에 복사돼 있었고, 그 상태로 하한 상수를 한쪽만
    # 바꾸면 이 경로는 옛 하한으로 계속 돌면서 테스트는 양쪽 다 초록이 된다.
    # ⚠️단 이 폴백은 **근거가 안 실려 온 경로**다 — 그래서 D-NAO-234의 0.827이 아니라
    # `NO_CORRECTION`(=1.0)을 기준점으로 쓴다. 0.827은 ref 95 창의 실측이고, 그 근거를 아는 것은
    # `diagnosis.correction_factor` 하나뿐이다. 근거 없이 여기서 17% 깎으면 금지선 5 위반이다.
    # ⇒ **라이브 경로 3곳(proposal_pipeline ×2 · auto_operator 서보)은 두 끝을 다 명시해 넘긴다**
    #   (그 배선은 소스 grep이 아니라 spy 테스트가 지킨다 — ref 94에서 배선 절단 변이가 생존했다).
    _derived_low, _derived_high = correction_interval.interval_ends(
        correction_factor, correction_interval.NO_CORRECTION,
    )
    cf_low = _derived_low if correction_factor_low is None else correction_factor_low
    cf_high = _derived_high if correction_factor_high is None else correction_factor_high

    rank_bid = estimate.get("rank_bid")

    def _resolve(cf: Decimal) -> tuple[int, int, str]:
        """한쪽 끝에서의 (경제성 상한, 추천입찰, 근거)."""
        ceiling = affordable_ceiling((rpc_raw * cf).quantize(_Q4), target_roas)
        if rank_bid is not None:
            return ceiling, min(ceiling, rank_bid), (
                "economic_ceiling" if ceiling <= rank_bid else "rank_target"
            )
        # estimate 미조회 — 순위 상한 없이 경제성 상한만 적용
        return ceiling, ceiling, "economic_ceiling_only"

    ceiling_low, rec_low, basis_low = _resolve(cf_low)
    ceiling_high, rec_high, basis_high = _resolve(cf_high)

    current_bid = keyword_row.get("bid_amt")

    def _direction(rec: int) -> str:
        if current_bid is None or rec == current_bid:
            return "hold"
        return "up" if rec > current_bid else "down"

    dir_low, dir_high = _direction(rec_low), _direction(rec_high)

    # ★★D-NAO-236 (Jino 결정 2026-08-24) — «게이트»는 상한이 정하고 **어떤 층도 뒤집지 않는다.**
    #
    # 무엇이 바뀌었나: 이전 판은 ②(크기 층)가 `direction = "hold"`로 ①(게이트 층)을 **뒤집었다**
    # (`basis="interval_floor_blocks_up"`). 그 결과 D-NAO-234가 하한을 1.0→0.827로 내리자
    # **액셀 제안의 24%가 조용히 사라졌다** — n=43 prod 실측(ref 95 §9-2, 창 08-09~23·후보 884건):
    #     액셀 up 296건 → 225건 (−71건) · 증액 총액 +244,730원 → +169,610원 (−30.7%)
    #     브레이크 down 531건 → 531건 (0) · 감액 총액 −282,870원 → −282,870원 (**완전 불변**)
    # 브레이크가 1원도 안 움직인 이유는 아래 `down` 분기가 `rec_high`/`ceiling_high`, 즉 **상한만**
    # 쓰기 때문이다 — 하한은 브레이크 경로에 아예 들어가지 않는다. 그래서 하한 인하의 효과는
    # 산술적으로 **100% 액셀 억제 쪽**이었고, 이는 계약 §4 금지선 2(액셀·브레이크 대칭)와
    # 정면으로 부딪혔다. 이 트랙의 상습 실패 모드가 정확히 그 모양이다(D-NAO-85: ROAS +7%·매출 −52%).
    #
    # ★진단은 「값이 틀렸다」가 아니라 «층 배정이 어긋났다»였다: D-NAO-234 ⓐ가 세운 정의는
    #   선정=상한 / **게이트(통과·차단)=상한** / 크기(얼마나)=하한 인데, 이 자리는 파일 위치가
    #   `bid_simulator`(크기 층)라는 이유로 층C에 배정돼 있었다. 그러나 **묻는 질문이 곧 층**이고
    #   이 코드가 묻던 것은 「올리나 마나」 — 즉 게이트다. 층을 «파일 위치»가 아니라 «질문»으로
    #   배정한다.
    #
    # 두 층을 동시에 만족시키는 법: 게이트(상한)가 「올려도 된다」 했으므로 **up을 유지**하고,
    # 크기(하한)가 「많이는 아니다」 했으므로 **가능한 최소 인상**(한 틱)만 한다. 상한 추천값을
    # 넘지 않도록 캡을 씌운다 — 즉 `min(rec_high, current + 10원)`.
    # ⚠️이 분기의 추천값은 **하한의 경제성 상한(ceiling_low)을 넘을 수 있다.** 그건 은폐할 사실이
    #   아니라 이 결정의 내용이다: 하한 경제성으로는 정당화되지 않지만 상한 게이트가 통과시킨
    #   구간이고, 구간이 현재 입찰을 **가로지른다**는 뜻이다. `economic_ceiling`은 보수 끝
    #   (`ceiling_low`)을 그대로 실어 그 사실이 사후에 재구성되게 둔다(`direction_low`도 함께).
    #
    # ①게이트 층 — 방향은 상한이 정한다. 아래 어느 분기도 이 값을 뒤집지 않는다.
    direction = dir_high
    if direction == "up":
        # ②크기 층 — «얼마나»만 하한으로 누른다. 방향은 안 건드린다.
        if rec_low > current_bid:
            recommended_bid, economic_ceiling, basis = rec_low, ceiling_low, basis_low
        else:
            # 하한이 현재 입찰을 못 넘는다 = 구간이 현재 입찰을 가로지른다.
            # 예전엔 여기서 hold로 뒤집었다(액셀 소멸). 이제는 **최소 한 틱**만 올린다.
            recommended_bid = min(rec_high, current_bid + _BID_INCREMENT)
            economic_ceiling = ceiling_low  # 보수 끝을 그대로 보고한다(추천값이 이를 넘을 수 있다)
            basis = "interval_floor_min_step"
    elif direction == "down":
        recommended_bid, economic_ceiling, basis = rec_high, ceiling_high, basis_high
    else:
        recommended_bid, economic_ceiling, basis = rec_high, ceiling_high, basis_high

    rpc_corrected = (rpc_raw * cf_low).quantize(_Q4)  # 예상매출 표기는 보수 끝(하한)으로

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
        # D-NAO-230: 구간 양끝을 그대로 노출한다 — 「하한/상한 중 무엇이 쓰였나」를 사후에
        # 재구성할 수 있어야 §5-6 비대칭 검사와 적대 리뷰가 가능하다.
        "economic_ceiling_low": ceiling_low,
        "economic_ceiling_high": ceiling_high,
        "direction_low": dir_low,
        "direction_high": dir_high,
        "rank_bid": rank_bid,
        "direction": direction,
        "basis": basis,
        "current_bid": current_bid,  # 라이브[P1] DOA 수정 — proposal_writer 스텝 클램프 원료(direction 판정에 이미 쓰던 값의 노출)
        "expected_effect_text": expected_effect_text,
        "capability_flags": capability_flags,
    }
