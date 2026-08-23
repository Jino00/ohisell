# expansion_pressure.py — 확장 압력 판정 순수 SA (스프린트 EX, D-NAO-85 §4-2 "압력 판정")
# 역할(SA·순수·DB 읽기만): 캠페인 단위로 "지금 이 캠페인이 볼륨을 더 밀어야 하는가"(확장 압력)를
#   판정한다. 목적함수 D-NAO-59 = 총이익 절대액 최대화 — 캠페인 보정ROAS가 BEP를 크게 웃도는
#   구간(한계 ROAS ≥ BEP)에서는 볼륨 확장이 이익을 키운다. 이 SA는 **판정만** 하고 실행/배분은
#   하지 않는다(배분=expansion_allocator, 배선=Phase 3 harness). 광고 API 호출 0.
#
#   판정 게이트(전부 충족 시 expansion_mode=True — §4-2):
#     ① 표본: 정착창(D-8~D-2) 캠페인 집계 clk ≥ EX_MIN_CAMPAIGN_CLK ∧ cost > 0(캠페인 질량).
#     ② 보정: diagnosis.correction_factor(D-1) source == 'actual_revenue_ratio'(fail-closed —
#        보정계수 unavailable이면 무보정 추정으로 확장하지 않는다, auto_operator._settlement_roas_status
#        의 fail-closed 관례와 동일 철학).
#     ③ 갭: roas_ratio = 보정ROAS ÷ bep_roas ≥ EX_PRESSURE_RATIO.
#   fail-closed 기조: 판정 불가(BEP 미해석·표본 미달·보정 부재·갭 미달)는 전부 expansion_mode=False.
#
#   ★import 정책: models + 최말단 순수 SA(gave_score·campaign_target_resolver — models만 의존)
#     + diagnosis(correction_factor 공개 함수)만 import한다. auto_operator는 import하지 않는다 —
#     Phase 3에서 harness(auto_operator/proposal_pipeline)가 이 모듈을 import(레인 배선)하므로
#     역방향 import는 순환을 만든다(exploration의 auto_operator 비import 철학과 동일). 정착창 상수는
#     로컬 복제하고 출처를 명시하며(아래), test_naver_expansion.py가 auto_operator 원본과의 정합을
#     별도로 고정한다(exploration._MIN_CLICK_FOR_EXPLORATION 정합 테스트 관례 미러).
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverCampaignSettings, NaverProductBep
from app.services.naver_ad import campaign_target_resolver, diagnosis, gave_score
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

# ── 정착창 상수(출처: auto_operator._SETTLEMENT_WINDOW_START_DAYS/_END_DAYS = 8/2, §3) ──
# 정착창 [오늘-8, 오늘-2] — naver_ad_daily 확정치(as_of=D-1)에서 전환귀속(~1일 정착)까지 감안한
# 짧은 창. auto_operator를 import하면 Phase 3 배선에서 순환이 생기므로 값만 로컬 복제한다.
# test_naver_expansion.py가 auto_operator._settlement_window와 값 정합을 고정해 조용한 divergence를
# 차단한다(exploration 정합 테스트 관례 미러).
_SETTLEMENT_WINDOW_START_DAYS = 8  # 출처: auto_operator._SETTLEMENT_WINDOW_START_DAYS
_SETTLEMENT_WINDOW_END_DAYS = 2    # 출처: auto_operator._SETTLEMENT_WINDOW_END_DAYS

# rationale 접두(D-NAO-85) — EX 확장 배분이 만든 bid_up 제안의 태그. 일 레인 P3 프라이어 폴백
# (auto_operator._check_bid_up_conditions)과 P4 배선이 이 접두로 EX 제안만 식별한다(비EX 회귀 0).
# 단일 진실 소스(proposal_pipeline 생성·auto_operator 판정이 공유 import).
EX_RATIONALE_PREFIX = "[EX확장]"

# ── 판정 상수(PLAN §4-2·§6 캘리브레이션 확정) ──
# 표본 게이트 — 정착창 7일 캠페인 클릭 하한(캠페인 질량 근거). §6 실측: 03≈42·04≈33 통과.
EX_MIN_CAMPAIGN_CLK = 30
# 갭 게이트 — 보정ROAS/BEP 하한. target_roas=BEP×1.15(표준)이므로 1.25 = 목표 초과 + 여유.
# §6 실측: 03=1.90·04=1.67 모두 초과(여유 있음). 캘리브레이션 상수 — 라이브 관측 후 조정.
EX_PRESSURE_RATIO = Decimal("1.25")


def _settlement_window(today: date) -> tuple[date, date]:
    """정착창 [오늘-8, 오늘-2] — auto_operator._settlement_window 로컬 복제(정합은 테스트 고정)."""
    return (
        today - timedelta(days=_SETTLEMENT_WINDOW_START_DAYS),
        today - timedelta(days=_SETTLEMENT_WINDOW_END_DAYS),
    )


def _campaign_settlement_agg(db: Session, campaign_id: str, date_from: date, date_to: date) -> dict:
    """정착창 내 캠페인 grain (clk, cost, conv_amt) 집계 — auto_operator._settlement_agg의
    집계 규칙을 그대로 복제한다(conv_amt = conv_direct_amt + conv_indirect_amt, backfill
    sentinel 행 제외 = 2배 집계 함정 방지). 캠페인 단위이므로 campaign_id로만 필터한다."""
    clk, cost, direct, indirect = (
        db.query(
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
        )
        .filter(
            NaverAdDaily.ad_date >= date_from,
            NaverAdDaily.ad_date <= date_to,
            NaverAdDaily.campaign_id == campaign_id,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .one()
    )
    return {"clk": int(clk), "cost": int(cost), "conv_amt": int(direct) + int(indirect)}


def _resolve_campaign_bep_roas(db: Session, campaign_id: str) -> Decimal | None:
    """캠페인 BEP ROAS **원값** 폴백 사다리(exploration._resolve_exploration_bep_roas의 캠페인
    대칭 — 그룹 grain 제외): ① 캠페인 매핑 상품 BEP → ② 계정 기본 BEP. 전부 부재면 None.
    ★target_roas가 아니라 BEP 원값이다 — target=BEP×공격성 배수라 갭 판정의 분모로 쓰면 안 된다
    (§4-2 "target_roas가 아니라 BEP 원값"). has_cost=True 상품만 대상(원가 미확인 추정 금지).
    account_default_bep_roas는 D-NAO-2 다이얼(공격성)이 적용되지 않는 순수 손익분기선이다."""
    bep = campaign_target_resolver.weighted_product_value_for_campaign(
        db, campaign_id, NaverProductBep.bep_roas,
    )
    if bep is not None and bep > 0:
        return bep
    bep = campaign_target_resolver.account_default_bep_roas(db)
    if bep is not None and bep > 0:
        return bep
    return None


def _gamma_for(db: Session, campaign_id: str) -> Decimal:
    """캠페인 공격성 다이얼 γ(naver_campaign_settings.gamma) — proposal_pipeline._gave_gamma_for
    (D-NAO-72)와 동일 해석 규칙(없거나 NULL이면 gave_score.DEFAULT_GAMMA)을 로컬 미러한다.
    ★γ는 갭 판정(roas_ratio)에는 영향을 주지 않는다(roas_ratio=ROAS/BEP는 γ 무관) — compute_gave_score
    재사용의 파라미터 정합을 위해서만 해석한다(중복 공식 금지·원칙18-4)."""
    row = db.query(NaverCampaignSettings.gamma).filter(
        NaverCampaignSettings.campaign_id == campaign_id,
    ).first()
    return row[0] if row is not None and row[0] is not None else gave_score.DEFAULT_GAMMA


def judge_campaign_pressure(db: Session, campaign_id: str, *, today: date) -> dict:
    """캠페인 확장 압력 판정(순수·판정만, PLAN §4-2). 반환 dict:
      {campaign_id, expansion_mode: bool, roas_ratio: Decimal|None, corrected_roas: Decimal|None,
       bep_roas: Decimal|None, window_clk: int, reason: str}.

    게이트 순서(전부 fail-closed): BEP 해석 → ①표본(clk≥EX_MIN_CAMPAIGN_CLK ∧ cost>0) →
    ②보정계수(source=='actual_revenue_ratio') → ③갭(roas_ratio≥EX_PRESSURE_RATIO). 어느
    게이트든 미충족 시 expansion_mode=False + 사유 반환(실행 없음).

    roas_ratio/corrected_roas는 gave_score.compute_gave_score를 재사용해 산출한다(중복 공식 금지):
    revenue=보정 conv_amt(= conv_amt × 보정계수), cost=정착창 cost, bep_roas=캠페인 BEP 원값.
    compute_gave_score의 roas_ratio(= ROAS/BEP) 필드가 곧 갭 비율이다(gave_score 헤더 개념 동일)."""
    window_from, window_to = _settlement_window(today)
    agg = _campaign_settlement_agg(db, campaign_id, window_from, window_to)
    bep_roas = _resolve_campaign_bep_roas(db, campaign_id)

    base = {
        "campaign_id": campaign_id,
        "expansion_mode": False,
        "roas_ratio": None,
        "corrected_roas": None,
        "bep_roas": bep_roas,
        "window_clk": agg["clk"],
    }

    # BEP 미해석 — 갭의 분모 없음(fail-closed).
    if bep_roas is None:
        return {**base, "reason": "BEP ROAS 해석 불가(상품/계정 BEP 부재) — 확장 보류(fail-closed)"}

    # ① 표본 게이트 — 캠페인 질량 근거.
    if agg["clk"] < EX_MIN_CAMPAIGN_CLK or agg["cost"] <= 0:
        return {
            **base,
            "reason": (
                f"표본 미달(정착창 clk {agg['clk']}<{EX_MIN_CAMPAIGN_CLK} 또는 cost {agg['cost']}≤0) — "
                "확장 보류(질량 부족)"
            ),
        }

    # ② 보정계수 게이트 — 무보정 추정으로 확장 금지(fail-closed).
    factor_info = diagnosis.correction_factor(db, today - timedelta(days=1))
    if factor_info.get("source") != "actual_revenue_ratio":
        return {
            **base,
            "reason": (
                f"보정계수 unavailable(source={factor_info.get('source')!r}) — 실주문 매출 부재, "
                "확장 보류(fail-closed)"
            ),
        }
    # ★★D-NAO-234 ⓐ(계약 §5-Q4): 이 갭 게이트는 «확장할까 말까»(통과/차단)이지 «얼마나
    # 확장하나»(크기)가 아니다 ⇒ **상한**. 하한을 쓰면 하한이 내려갈수록 EX_PRESSURE_RATIO
    # 통과가 어려워져 **신규 확장 후보 자체가 줄어든다** — 액셀이 주는 방향이라
    # 하한 인하와 같은 배포에 이 재배정이 없으면 계약 §4 금지선 2 위반이다(ref 94 §6).
    factor = Decimal(str(factor_info["factor_high"]))

    # ③ 갭 게이트 — gave_score 재사용으로 roas_ratio(=보정ROAS/BEP) 산출(중복 공식 금지).
    gamma = _gamma_for(db, campaign_id)
    corrected_revenue = Decimal(agg["conv_amt"]) * factor
    scored = gave_score.compute_gave_score(
        revenue=corrected_revenue, cost=agg["cost"], bep_roas=bep_roas, gamma=gamma,
    )
    roas_ratio = scored["roas_ratio"]
    corrected_roas = scored["roas"]
    base = {**base, "roas_ratio": roas_ratio, "corrected_roas": corrected_roas}

    if roas_ratio is None or roas_ratio < EX_PRESSURE_RATIO:
        return {
            **base,
            "reason": (
                f"갭 부족(보정ROAS/BEP {roas_ratio} < {EX_PRESSURE_RATIO}) — 확장 보류"
            ),
        }

    return {
        **base,
        "expansion_mode": True,
        "reason": (
            f"확장 모드 — 갭 {roas_ratio}≥{EX_PRESSURE_RATIO}·표본 clk {agg['clk']}≥{EX_MIN_CAMPAIGN_CLK}"
            f"(보정ROAS {corrected_roas}·BEP {bep_roas})"
        ),
    }
