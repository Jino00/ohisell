# forecast_model_builder.py — forecast_model_builder SA (예측·전문가 스프린트 F1, D-NAO-24)
# 역할(SA 단일 책임): 캠페인 grain(F1 스코프) 일별 clk/cost/conv_amt를 최근 추세 지수감쇠
#   이동평균(모델 v1)으로 예측한다.
# **모델 설계 변경 이력(백테스트 실증, 정직 경계)**: 계획서 원안은 "요일 계절성×추세"였으나
#   실제 43캠페인×151일 워크포워드 백테스트에서 요일 계절성 적용이 나이브 베이스라인(어제
#   값을 그대로 오늘 예측으로 씀)보다 항상 더 나빴다(clk MAPE 0.63 vs 나이브 0.61,
#   window=1+계절성조차 순수나이브보다 열위 0.63>0.61) — 캠페인 단위 일별 성과는 요일
#   패턴보다 일별 자기상관(어제→오늘 연속성)이 훨씬 강하고, 4주 이력만으로 추정한 요일
#   지수는 추정오차가 신호보다 커서 순노이즈로 작용했다(추정 금지 원칙 위반 소지 — 신뢰
#   못 할 패턴을 강제 적용하지 않는다). 계절성 제거 + 짧은 창(3일) 지수감쇠(0.6)로
#   전환하자 나이브 대비 clk +4.2%·cost +1.6% 개선을 확인(스크래치 스윕 스크립트로 검증,
#   재현 가능). 요일 패턴은 데이터가 더 쌓이면(수개월~) F2/v2에서 재도입 검토 대상.
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverForecastDaily, NaverForecastModel
from app.services.naver_ad import forecast_gate
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

HISTORY_LOOKBACK_DAYS = 14  # 조회 창(TREND_WINDOW_DAYS보다 넉넉히 — 결측일 대비 여유)
TREND_WINDOW_DAYS = 3  # 최근 추세 이동평균 창(백테스트 실측 최적, 위 docstring 참조)
TREND_DECAY = Decimal("0.6")  # 지수감쇠율(백테스트 스윕: 0.6~0.7 구간에서 최적, 큰 차이 없어 0.6 채택)
_METRICS = ("clk", "cost", "conv_amt")
_Q0 = Decimal("1")
_Q2 = Decimal("0.01")


def _daily_series(db: Session, campaign_id: str, date_from: date, date_to: date) -> dict[date, dict]:
    """campaign_backfill sentinel 행에서 {date: {clk,cost,conv_amt}} 시계열을 읽는다."""
    rows = db.query(NaverAdDaily).filter(
        NaverAdDaily.campaign_id == campaign_id,
        NaverAdDaily.adgroup_id == BACKFILL_SENTINEL_ADGROUP,
        NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
    ).all()
    return {
        r.ad_date: {"clk": r.clk, "cost": r.cost, "conv_amt": r.conv_direct_amt + r.conv_indirect_amt}
        for r in rows
    }


def _trend_level(series: dict[date, dict], metric: str) -> Decimal:
    """최근 TREND_WINDOW_DAYS일(가용한 만큼)의 지수감쇠 가중평균 — 가장 최근일에 가장 큰 가중치."""
    dates = sorted(series.keys(), reverse=True)[:TREND_WINDOW_DAYS]
    if not dates:
        return Decimal(0)
    weights = [TREND_DECAY ** i for i in range(len(dates))]
    total_w = sum(weights)
    level = sum(w * Decimal(series[d][metric]) for w, d in zip(weights, dates))
    return level / total_w if total_w else Decimal(0)


def _round_int(v: Decimal) -> int:
    return int(v.quantize(_Q0, rounding=ROUND_HALF_UP))


def build_and_forecast(db: Session, campaign_id: str, *, today: date) -> dict:
    """게이트 평가 → (active면) 예측 생성 → NaverForecastModel/NaverForecastDaily upsert.

    같은 (campaign_id, today) 재실행 시 교체(멱등) — 크론 재실행/백테스트 재현 안전.
    """
    gate = forecast_gate.evaluate(db, grain="campaign", scope_key=campaign_id, today=today)

    model_row = db.query(NaverForecastModel).filter(
        NaverForecastModel.grain == "campaign", NaverForecastModel.scope_key == campaign_id,
    ).first()
    if model_row is None:
        model_row = NaverForecastModel(grain="campaign", scope_key=campaign_id)
        db.add(model_row)
    model_row.gate_status = gate["gate_status"]
    model_row.sample_days = gate["active_days"]

    if gate["gate_status"] != "active":
        db.commit()
        return {
            "campaign_id": campaign_id, "gate_status": gate["gate_status"],
            "forecast_created": False, "reason": gate["reason"],
        }

    date_to = today - timedelta(days=1)
    date_from = date_to - timedelta(days=HISTORY_LOOKBACK_DAYS - 1)
    series = _daily_series(db, campaign_id, date_from, date_to)
    if not series:
        model_row.gate_status = "fallback"
        db.commit()
        return {
            "campaign_id": campaign_id, "gate_status": "fallback",
            "forecast_created": False, "reason": "게이트는 active이나 조회 구간에 시계열 없음(경합 데이터)",
        }

    levels = {m: _trend_level(series, m) for m in _METRICS}

    pred_clk = max(0, _round_int(levels["clk"]))
    pred_cost = max(0, _round_int(levels["cost"]))
    pred_conv_amt = max(0, _round_int(levels["conv_amt"]))
    pred_cpc = (Decimal(pred_cost) / Decimal(pred_clk)).quantize(_Q2, rounding=ROUND_HALF_UP) if pred_clk > 0 else None

    forecast_row = db.query(NaverForecastDaily).filter(
        NaverForecastDaily.target_date == today, NaverForecastDaily.grain == "campaign",
        NaverForecastDaily.scope_key == campaign_id,
    ).first()
    if forecast_row is None:
        forecast_row = NaverForecastDaily(target_date=today, grain="campaign", scope_key=campaign_id)
        db.add(forecast_row)
    forecast_row.pred_clk = pred_clk
    forecast_row.pred_cost = pred_cost
    forecast_row.pred_cpc = pred_cpc
    forecast_row.pred_conv_amt = pred_conv_amt

    model_row.params_json = json.dumps({
        "trend_level": {m: float(levels[m]) for m in _METRICS},
        "trend_window_days": TREND_WINDOW_DAYS, "trend_decay": float(TREND_DECAY),
        "series_days": len(series),
    }, ensure_ascii=False)
    model_row.trained_at = kst_now()

    db.commit()
    return {
        "campaign_id": campaign_id, "gate_status": "active", "forecast_created": True,
        "pred_clk": pred_clk, "pred_cost": pred_cost, "pred_conv_amt": pred_conv_amt,
    }


def run_daily(db: Session, campaign_ids: list[str], *, today: date) -> dict:
    """전 캠페인(호출자가 목록 전달 — F1은 naver_entity 조회를 harness가 담당) 순회 실행."""
    results = [build_and_forecast(db, cid, today=today) for cid in campaign_ids]
    forecasted = sum(1 for r in results if r["forecast_created"])
    log.info("forecast_model_builder: %d개 캠페인 중 %d개 예측 생성(%s)", len(campaign_ids), forecasted, today)
    return {"campaigns": len(campaign_ids), "forecasted": forecasted, "results": results}
