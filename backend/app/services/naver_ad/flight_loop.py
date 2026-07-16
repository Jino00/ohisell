# flight_loop.py — flight_loop Harness (X2 T3, D-NAO-34; 완결도 보정 D-NAO-44)
# 역할(Harness): response_curve_builder(T1)와 pacing_controller(T2) SA를 조합해
#   2시간 주기로 캠페인별 최적 입찰배수(α)를 산출한다. 원료 pre-compute → SA 호출 →
#   결과 기록(naver_change_log, dry_run=True). α에 따른 실제 입찰 변경은 dry-run 1주
#   확인 후 Jino 전환 결정(D-NAO-5, PLAN §3 X2 완료기준).
#   SA간 직접 호출 금지(원칙18-6) — SA를 조합하고 원료를 유통하는 게 harness의 본연.
#   D-NAO-44: completeness_curve(T4) SA를 run당 1회 pre-compute해 오늘 보이는 cost의
#   저평가를 보정한 뒤 response_curve_builder에 전달한다(PLAN_naver-ad-pacing-correction.md).
from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import (
    NaverCampaignSettings, NaverChangeLog, NaverEntity, NaverForecastDaily,
    NaverHourlyPatternHistory, NaverHourlySnapshot, NaverLearningState,
)
from app.services.naver_ad import completeness_curve, response_curve_builder, pacing_controller
from app.services.naver_ad.bid_simulator import pooled_rpc
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.diagnosis import correction_factor as compute_correction_factor
from app.utils.kst import kst_now, kst_today

log = logging.getLogger(__name__)

_Q4 = Decimal("0.0001")


def _ours_campaigns(db: Session) -> list[NaverCampaignSettings]:
    return (
        db.query(NaverCampaignSettings)
        .filter(NaverCampaignSettings.optimizer == "ours")
        .all()
    )


def _today_forecast(db: Session, campaign_id: str, today: date) -> dict | None:
    row = (
        db.query(NaverForecastDaily)
        .filter(
            NaverForecastDaily.grain == "campaign",
            NaverForecastDaily.scope_key == campaign_id,
            NaverForecastDaily.target_date == today,
        )
        .first()
    )
    if row is None:
        return None
    return {
        "pred_clk": row.pred_clk,
        "pred_cost": row.pred_cost,
        "pred_conv_amt": row.pred_conv_amt,
    }


def _hourly_weights(db: Session, weekday: int) -> list[dict]:
    rows = (
        db.query(NaverHourlyPatternHistory)
        .filter(NaverHourlyPatternHistory.weekday == weekday)
        .all()
    )
    observed = [r for r in rows if r.sample_days and r.sample_days > 0]
    total = sum(r.cost_sum for r in observed)
    if total <= 0:
        return []
    return [
        {"hour": r.hour, "cost_fraction": Decimal(r.cost_sum) / Decimal(total)}
        for r in observed
    ]


def _today_actuals(db: Session, campaign_id: str, today: date) -> dict:
    row = (
        db.query(NaverHourlySnapshot)
        .filter(
            NaverHourlySnapshot.ad_date == today,
            NaverHourlySnapshot.campaign_id == campaign_id,
        )
        .order_by(NaverHourlySnapshot.snapshot_hour.desc())
        .first()
    )
    if row is None:
        return {"cost": 0, "clk": 0, "imp": 0, "conv_amt": 0}
    return {"cost": row.cost, "clk": row.clk, "imp": row.imp, "conv_amt": 0}


def _campaign_rpc(db: Session, campaign_id: str, today: date, lookback: int = 15) -> Decimal:
    from app.models import NaverAdDaily
    from datetime import timedelta

    date_from = today - timedelta(days=lookback - 1)
    agg = (
        db.query(
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
            sqlfunc.coalesce(
                sqlfunc.sum(NaverAdDaily.conv_direct_amt) + sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0
            ),
        )
        .filter(
            NaverAdDaily.campaign_id == campaign_id,
            NaverAdDaily.ad_date >= date_from,
            NaverAdDaily.ad_date <= today,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .first()
    )
    clk, conv_amt = (agg[0] or 0), (agg[1] or 0)
    if clk <= 0:
        return Decimal("0")
    raw_rpc = Decimal(conv_amt) / Decimal(clk)
    cf_result = compute_correction_factor(db, today)
    cf = cf_result.get("factor", Decimal("1"))
    return (raw_rpc * cf).quantize(_Q4)


def _budget_info(db: Session, campaign_id: str, today: date) -> dict:
    """daily_budget와 remaining을 함께 반환. daily_budget=0은 무제한(Naver 규격)."""
    snapshot = (
        db.query(NaverHourlySnapshot)
        .filter(
            NaverHourlySnapshot.ad_date == today,
            NaverHourlySnapshot.campaign_id == campaign_id,
        )
        .order_by(NaverHourlySnapshot.snapshot_hour.desc())
        .first()
    )
    if snapshot is None or snapshot.daily_budget is None or snapshot.daily_budget <= 0:
        return {"daily_budget": None, "remaining": None}
    remaining = max(0, snapshot.daily_budget - snapshot.cost)
    return {"daily_budget": snapshot.daily_budget, "remaining": remaining}


def _log_flight_decision(
    db: Session,
    *,
    campaign_id: str,
    result: dict,
    dry_run: bool,
    now,
) -> None:
    db.add(NaverChangeLog(
        entity_type="campaign",
        entity_id=campaign_id,
        campaign_id=campaign_id,
        action="flight_pacing",
        proposal_id=None,
        dry_run=dry_run,
        changed_at=now,
        before_value=None,
        after_value=json.dumps(result, default=str),
        rationale=(
            f"α={result.get('alpha', '?')} "
            f"(αB={result.get('alpha_budget', '?')}, αC={result.get('alpha_roas', '?')}) "
            f"binding={result.get('binding_constraint', '?')}"
        ),
    ))


def run_flight_loop(
    db: Session,
    *,
    today: date | None = None,
    current_hour: int | None = None,
    dry_run: bool = True,
) -> dict:
    """2시간 주기 플라이트 루프: 캠페인별 α 산출 + change_log 기록.

    dry_run=True(기본): 결정만 기록, 실제 입찰 변경 없음.
    dry_run=False: α에 따른 입찰 변경 실행(X2 완료기준 달성 후 Jino 전환).
    """
    today = today or kst_today()
    now = kst_now()
    if current_hour is None:
        current_hour = now.hour

    campaigns = _ours_campaigns(db)
    if not campaigns:
        log.info("flight_loop: optimizer='ours' 캠페인 없음 — 스킵")
        return {"campaigns_processed": 0, "decisions": []}

    weekday = today.weekday()
    weights = _hourly_weights(db, weekday)
    decisions = []

    # D-NAO-44: 완결도 곡선 run당 1회 pre-compute(캠페인 루프 밖, 원칙18-6 — harness가
    # 원료를 유통하고 SA는 서로 모른다). /stats 당일누적은 시각별로 체계적 저평가라
    # (완결도 곡선 v2, naver_stat_field_cadence_20260716.md) 보정 없이 raw cost_so_far를
    # 쓰면 예산제약(αB)이 남은예산을 과대평가해 α가 과속 편향된다(PLAN §0).
    curve_by_hour = completeness_curve.build_curve(db)
    projection = completeness_curve.projection_factor(curve_by_hour, current_hour)
    hour_completeness = (
        curve_by_hour.get(current_hour, {}).get("completeness") if projection is not None else None
    )

    for cs in campaigns:
        cid = cs.campaign_id
        try:
            forecast = _today_forecast(db, cid, today)
            if forecast is None:
                decisions.append({"campaign_id": cid, "skipped": "forecast 없음"})
                continue

            actuals = _today_actuals(db, cid, today)
            raw_today_cost = actuals["cost"]
            rpc = _campaign_rpc(db, cid, today)
            budget = _budget_info(db, cid, today)

            target_roas_result = None
            try:
                from app.services.naver_ad import campaign_target_resolver
                target_roas_result = campaign_target_resolver.resolve_target_roas(db, cid)
            except Exception:
                pass

            target_roas = (
                target_roas_result["target_roas"]
                if target_roas_result and target_roas_result.get("target_roas")
                else Decimal("2")
            )

            budget_for_pacing = budget["daily_budget"] if budget["daily_budget"] is not None else 999_999_999

            if projection is None:
                # D-NAO-44 fail-safe: 완결도 표본 부족/오전 자연차단(PLAN §3) — 저평가 입력으로
                # α를 계산하는 것 자체가 버그이므로 원 로직(response_curve/pacing_controller)을
                # 계속하지 않고 중립(α=1.0)으로 고정한다. 원 로직 계속 금지는 §0의 과속 편향을
                # 표본 부족 상태에서 그대로 재현하지 않기 위함.
                pacing = {
                    "alpha": 1.0, "alpha_budget": 1.0, "alpha_roas": 1.0,
                    "binding_constraint": "projection_unavailable",
                }
                curve_meta = {"remaining_fraction": None, "pace_ratio": None, "elasticity": None}
                projected_final_cost = None
            else:
                projected_final_cost = int((Decimal(raw_today_cost) * projection).to_integral_value())

                # 의미 확정(PLAN §3 선행 필수, §7에 동일 내용 기록):
                # response_curve_builder.build_response_curve()의 points[i]["cost"] =
                # cost_so_far + scaled_remaining_cost(α) — 이미 "오늘 하루 전체 예상 총비용"
                # (전일 물량)이다. daily_budget도 전일 물량이라 원래부터 동종 비교였다
                # (P2-2 회귀 test_flight_loop_total_vs_remaining_budget_comparison 참조).
                # 편향의 원인은 그 cost_so_far 항이 raw(저평가)라는 점 — 여기(cost_so_far)만
                # projected_final_cost로 교체해 동종성을 유지한 채 저평가를 보정한다.
                # (ROAS제약 αC는 손대지 않음: 클릭·노출·비용 완결도 곡선이 거의 동일해
                # 비율인 ROAS에는 편향이 상쇄되기 때문 — 참고문서 §3.)
                # 알려진 근사: scaled_remaining_cost(α)도 이미 "잔여 예상비용"을 더하므로
                # projected_final_cost(완결도 기반, 미래분 포함)를 그대로 cost_so_far 자리에
                # 넣으면 잔여분이 개념적으로 일부 중복 반영될 수 있다 — 방향은 항상 α를
                # 낮추는(보수화) 쪽이라 안전 측 편향이며, dry-run 관찰(07-17~)로 캘리브레이션한다.
                actuals_for_curve = dict(actuals)
                actuals_for_curve["cost"] = projected_final_cost

                curve = response_curve_builder.build_response_curve(
                    forecast=forecast,
                    hourly_weights=weights,
                    actuals=actuals_for_curve,
                    current_hour=current_hour,
                    rpc=rpc,
                )
                curve_meta = {
                    "remaining_fraction": curve["remaining_fraction"],
                    "pace_ratio": curve["pace_ratio"],
                    "elasticity": curve["elasticity"],
                }
                pacing = pacing_controller.compute_pacing_alpha(
                    points=curve["points"],
                    remaining_budget=budget_for_pacing,
                    target_roas=target_roas,
                )

            decision = {
                "campaign_id": cid,
                "alpha": pacing["alpha"],
                "alpha_budget": pacing["alpha_budget"],
                "alpha_roas": pacing["alpha_roas"],
                "binding_constraint": pacing["binding_constraint"],
                **curve_meta,
                "actuals_cost": raw_today_cost,
                "daily_budget": budget["daily_budget"],
                "remaining_budget": budget["remaining"],
                "target_roas": float(target_roas),
                "dry_run": dry_run,
                # D-NAO-44 관측 필드 — dry-run 관찰·07-17 이후 대조의 원료(PLAN §3/§6).
                "raw_today_cost": raw_today_cost,
                "completeness": hour_completeness,
                "projection_factor": projection,
                "projected_final_cost": projected_final_cost,
            }
            decisions.append(decision)

            _log_flight_decision(db, campaign_id=cid, result=decision, dry_run=dry_run, now=now)

        except Exception as e:
            log.exception("flight_loop: campaign %s 처리 실패: %s", cid, e)
            decisions.append({"campaign_id": cid, "error": str(e)})

    db.commit()
    log.info("flight_loop: %d캠페인 처리 (dry_run=%s)", len(decisions), dry_run)
    return {"campaigns_processed": len(decisions), "decisions": decisions, "dry_run": dry_run}
