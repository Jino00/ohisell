# diagnosis.py — diagnosis Harness (P2-S2)
# 역할: account_diagnosis_sa(보드 6개) + campaign_target_resolver(계정 BEP/목표ROAS) +
#   actual_revenue_sa(D-NAO-21 보정계수)를 조합해 GET /diagnosis 응답 하나로 정리.
#   SA간 직접 호출 금지 원칙(원칙18) — 이 Harness가 유일한 정보 유통 허브.
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.naver_ad import account_diagnosis as diag
from app.services.naver_ad import campaign_target_resolver, metrics_aggregator
from app.services.naver_ad.actual_revenue import naver_order_revenue

_CORRECTION_LOOKBACK_DAYS = 30  # D-NAO-21: 계정 보정계수 산출 창(30일 고정)


def _correction_factor(db: Session, date_to: date) -> dict:
    """D-NAO-21 보정계수 = (실단위 데이터가 실제로 존재하는 창의) 실주문매출 ÷ 네이버 convAmt.

    네이버 convAmt(직+간접)가 실주문 대비 과대(~2.6배 실증) → 진단 판정 시 곱해 보정.
    창은 최대 30일이지만, naver_ad_daily 실단위(P0) 수집이 아직 30일 안 쌓였으면(파이프라인
    가동 초기) 실제 데이터가 있는 구간으로 양쪽(매출·convAmt)을 똑같이 좁힌다 — 그렇지 않으면
    "매출은 30일치, convAmt는 3일치"처럼 창이 어긋나 계수가 왜곡된다(원칙22, 라이브 검증 중 발견).
    naver convAmt=0이면(실단위 데이터 자체가 없음) 계수 산출 불가(1.0 폴백, no-op 보정 + 사유 명시).
    """
    earliest_real = diag.earliest_real_data_date(db, date_to, _CORRECTION_LOOKBACK_DAYS)
    if earliest_real is None:
        return {"factor": Decimal("1"), "source": "unavailable", "window_revenue": 0, "window_conv_amt": 0}

    date_from = earliest_real
    revenue = naver_order_revenue(db, date_from, date_to)["revenue"]
    totals = metrics_aggregator.aggregate(db, date_from, date_to, grain="date")["totals"]
    naver_conv_amt = totals["conv_amt"]
    if not naver_conv_amt:
        return {"factor": Decimal("1"), "source": "unavailable", "window_revenue": revenue, "window_conv_amt": 0}
    factor = (Decimal(revenue) / Decimal(naver_conv_amt)).quantize(Decimal("0.0001"))
    return {
        "factor": factor, "source": "actual_revenue_ratio",
        "window_from": date_from.isoformat(), "window_to": date_to.isoformat(),
        "window_revenue": revenue, "window_conv_amt": naver_conv_amt,
    }


def _target_roas_resolver(db: Session, account_target_roas: Decimal):
    """campaign_id → Decimal target_roas 리졸버(override > 계정기본값) — 회차 내 캐싱.

    resume_candidates 전용(codex[P2], X1b T3) — proposal_pipeline._make_target_roas_resolver와
    동일 원리(계정 단일 target_roas만 쓰면 캠페인 override가 실제 판정에 반영되지 않는 재발
    버그 패턴, 2026-07-07 라이브검증 이력)를 diagnosis Harness 쪽에서도 적용한다.
    """
    cache: dict[str, Decimal] = {}

    def _resolve(campaign_id: str) -> Decimal:
        if campaign_id not in cache:
            resolved = campaign_target_resolver.resolve_target_roas(db, campaign_id)
            cache[campaign_id] = (
                resolved["target_roas"] if resolved["target_roas"] is not None else account_target_roas
            )
        return cache[campaign_id]

    return _resolve


def build_diagnosis(db: Session, date_from: date, date_to: date) -> dict:
    """진단 보드 6개 + 보정계수 + 계정 BEP/목표ROAS를 조립. 읽기 전용(D-3, 제안 없음)."""
    correction = _correction_factor(db, date_to)
    factor = correction["factor"]

    bep_roas = campaign_target_resolver.account_default_bep_roas(db)
    target_roas = campaign_target_resolver.account_default_target_roas(db)

    if bep_roas is None or target_roas is None:
        return {
            "window": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
            "correction_factor": {**correction, "factor": float(factor)},
            "account_bep_roas": float(bep_roas) if bep_roas is not None else None,
            "account_target_roas": float(target_roas) if target_roas is not None else None,
            "error": "계정 BEP/목표ROAS 산출 불가 — naver_product_bep에 has_cost=True 상품 없음",
            "boards": None,
        }

    boards = {
        "bleeding_keywords": diag.bleeding_keywords(db, date_from, date_to, bep_roas, factor),
        "starving_winners": diag.starving_winners(db, date_from, date_to, target_roas, factor),
        "expansion_bucket": diag.expansion_bucket(db, date_from, date_to, factor),
        "shopping_group_bep": diag.shopping_group_bep(db, date_from, date_to, bep_roas, factor),
        "exclusion_candidates": diag.exclusion_candidates(db, date_from, date_to),
        "keyword_triage": diag.keyword_triage(db, as_of=date_to),
        "vicious_cycle": diag.vicious_cycle_flags(db, date_to, target_roas, factor),
        "pause_candidates": diag.pause_candidates(db, date_from, date_to),
        "resume_candidates": diag.resume_candidates(
            db, date_to, _target_roas_resolver(db, target_roas), factor,
        ),
    }

    return {
        "window": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        "correction_factor": {**correction, "factor": float(factor)},
        "account_bep_roas": float(bep_roas),
        "account_target_roas": float(target_roas),
        "boards": boards,
    }
