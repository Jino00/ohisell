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


# 스킵 사유 코드 — 로그·요약·change_log에서 같은 이름을 쓴다(문자열 중복 방지).
SKIP_CAMPAIGN_OFF = "campaign_off"
SKIP_FORECAST_MISSING = "forecast_missing"


def _ours_campaigns(db: Session) -> list[tuple[NaverCampaignSettings, str | None]]:
    """optimizer='ours' 설정 행을 그 캠페인의 `NaverEntity.status`와 함께 반환.

    ★대상 정의 불일치(2026-07-28 규명): `forecast_engine._active_scopes`는 status='on'인
    엔티티만 예측 대상으로 삼는데(forecast_engine.py:40-46) 여기서는 status를 보지 않았다.
    그래서 정지된 캠페인이 optimizer='ours'로 남아 있으면 예측이 영원히 생기지 않고,
    이 루프는 2시간마다 그것을 "forecast 없음"으로 조용히 넘겼다.
    prod 실측(2026-07-28 23:0x): optimizer='ours' 6개 중 `cmp-a001-02-000000010769985`가
    entity status='off' 상태로 이 경로에 걸려 있었다.

    빼지 않고 status를 함께 읽어오는 이유: 쿼리에서 제외하면 그 캠페인이 리포트에서
    통째로 사라져 관측성이 오히려 나빠진다. 루프 안에서 `campaign_off`로 **이름 붙여**
    스킵한다 — 무성 실패를 유성 실패로 바꾸는 것이 이 수정의 목적이다.
    """
    rows = (
        db.query(NaverCampaignSettings, NaverEntity.status)
        .outerjoin(
            NaverEntity,
            (NaverEntity.entity_id == NaverCampaignSettings.campaign_id)
            & (NaverEntity.entity_type == "campaign"),
        )
        .filter(NaverCampaignSettings.optimizer == "ours")
        .all()
    )
    return [(cs, status) for cs, status in rows]


def _forecast_gate_hint(db: Session, campaign_id: str) -> str | None:
    """예측이 없을 때 '왜 없는지'의 단서 — 모델 원장의 gate_status.

    active/fallback/demoted 중 무엇인지가 대응을 가른다(강등이면 쿨다운 대기, fallback이면
    이력 부족). 조회 실패는 진단 부가정보이므로 삼켜서 본 루프를 막지 않는다.
    """
    try:
        from app.models import NaverForecastModel

        row = (
            db.query(NaverForecastModel.gate_status)
            .filter(
                NaverForecastModel.grain == "campaign",
                NaverForecastModel.scope_key == campaign_id,
            )
            .first()
        )
        return row[0] if row else None
    except Exception:  # noqa: BLE001 — 진단 부가정보. 실패해도 루프는 계속.
        return None


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
        return {"cost": 0, "clk": 0, "imp": 0, "conv_amt": 0, "snapshot_hour": None}
    return {
        "cost": row.cost, "clk": row.clk, "imp": row.imp, "conv_amt": 0,
        # codex[P2] R2: 완결도 factor는 이 스냅샷이 찍힌 시각 기준이어야 한다 —
        # current_hour 스냅샷이 아직 안 쓰였으면 최신 행은 이전 시각분이고, 거기에
        # 더 늦은 시각의(더 높은 완결도=더 낮은) factor를 곱하면 저투영된다.
        "snapshot_hour": row.snapshot_hour,
    }


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


def _log_flight_silence(db: Session, *, summary: dict, now) -> None:
    """한 run에서 **결정이 하나도 안 나온 경우에만** 남기는 진단 행(무성 실패 → 유성 실패).

    캠페인별 스킵을 매번 change_log에 적지 않는 이유: 6캠페인 × 12run = 하루 72행이 쌓여
    "시스템이 오늘 한 일"을 읽는 화면들을 오염시킨다. 대신 병리 상태(전원 무결정)일 때만
    run당 1행 — 정상일 때 0행, 고장났을 때 하루 최대 12행으로 자기 제한된다.
    `entity_type='system'`이라 캠페인 단위 조회에 섞이지 않고, action도 실행 액션 집합
    (`naver_execution_harness.EXECUTION_ACTIONS`)에 없어 성과 화면 집계에 들어가지 않는다.
    """
    db.add(NaverChangeLog(
        entity_type="system",
        entity_id="flight_loop",
        campaign_id=None,
        action="flight_pacing_silent",
        proposal_id=None,
        dry_run=True,  # 진단 기록 — 아무것도 바꾸지 않았다.
        changed_at=now,
        before_value=None,
        after_value=json.dumps(summary, default=str),
        rationale=(
            f"{summary.get('campaigns_processed', 0)}개 캠페인 전원 무결정 — "
            f"스킵 {summary.get('skipped', 0)}({summary.get('skip_breakdown', {})}) "
            f"오류 {summary.get('errors', 0)}"
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
        return {
            "campaigns_processed": 0, "decided": 0, "skipped": 0,
            "skip_breakdown": {}, "errors": 0, "decisions": [], "dry_run": dry_run,
        }

    weekday = today.weekday()
    weights = _hourly_weights(db, weekday)
    decisions = []
    skip_breakdown: dict[str, int] = {}

    # D-NAO-44: 완결도 곡선 run당 1회 pre-compute(캠페인 루프 밖, 원칙18-6 — harness가
    # 원료를 유통하고 SA는 서로 모른다). /stats 당일누적은 시각별로 체계적 저평가라
    # (완결도 곡선 v2, naver_stat_field_cadence_20260716.md) 보정 없이 raw cost_so_far를
    # 쓰면 예산제약(αB)이 남은예산을 과대평가해 α가 과속 편향된다(PLAN §0).
    curve_by_hour = completeness_curve.build_curve(db, today=today)

    for cs, entity_status in campaigns:
        cid = cs.campaign_id
        try:
            # 정지된 캠페인은 예측 자체가 생성되지 않는다(_ours_campaigns docstring 참조).
            # "forecast 없음"으로 뭉뚱그리면 대응이 갈리지 않으므로 사유를 분리한다.
            if entity_status is not None and entity_status != "on":
                skip_breakdown[SKIP_CAMPAIGN_OFF] = skip_breakdown.get(SKIP_CAMPAIGN_OFF, 0) + 1
                decisions.append({
                    "campaign_id": cid,
                    "skipped": SKIP_CAMPAIGN_OFF,
                    "skip_detail": f"NaverEntity.status={entity_status}",
                })
                continue

            forecast = _today_forecast(db, cid, today)
            if forecast is None:
                gate = _forecast_gate_hint(db, cid)
                skip_breakdown[SKIP_FORECAST_MISSING] = (
                    skip_breakdown.get(SKIP_FORECAST_MISSING, 0) + 1
                )
                decisions.append({
                    "campaign_id": cid,
                    "skipped": SKIP_FORECAST_MISSING,
                    "skip_detail": f"gate_status={gate}" if gate else "gate_status=미등록",
                })
                continue

            actuals = _today_actuals(db, cid, today)
            raw_today_cost = actuals["cost"]
            # codex[P2] R2: factor는 캠페인별 최신 스냅샷의 시각 기준(불일치 시 저투영 —
            # 예: 10시 run인데 최신 스냅샷이 9시분이면 9시 factor를 써야 함). 오늘
            # 스냅샷이 아예 없으면 current_hour 폴백(cost=0이라 수치 영향 없음).
            snapshot_hour = (
                actuals["snapshot_hour"] if actuals["snapshot_hour"] is not None else current_hour
            )
            projection = completeness_curve.projection_factor(curve_by_hour, snapshot_hour)
            hour_completeness = (
                curve_by_hour.get(snapshot_hour, {}).get("completeness")
                if projection is not None else None
            )
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
                # 편향의 원인은 그 cost_so_far 항이 raw(저평가)라는 점 — so-far 항을
                # projected로 교체해 동종성을 유지한 채 저평가를 보정한다.
                # codex[P2] ROAS 오염 방지: cost만 투영하면 points의 ROAS(=revenue/cost)
                # 분모만 부풀어 αC가 체계적으로 깎인다(예산 보정이 ROAS 제약을 오발동).
                # 클릭·노출·비용의 완결도 곡선이 거의 동일하므로(참고문서 §3) clk·conv_amt도
                # 같은 factor로 투영해 so-far ROAS 비율을 보존한다. (전환 완결도는 어트리뷰션
                # 지연으로 더 낮지만 conv_amt는 rpc=0일 때만 쓰이는 revenue 폴백 — 같은
                # factor 적용이 ratio 보존 관점에서 안전.)
                # 알려진 근사: scaled_remaining_*(α)도 이미 "잔여 예상물량"을 더하므로
                # projected(완결도 기반, 미래분 포함)를 그대로 so-far 자리에 넣으면 잔여분이
                # 개념적으로 일부 중복 반영될 수 있다 — cost·clk 양쪽에 같은 방향이라 ROAS는
                # 중립, 예산(αB)은 항상 α를 낮추는(보수화) 안전 측 편향이며, dry-run
                # 관찰(07-17~)로 캘리브레이션한다.
                actuals_for_curve = dict(actuals)
                actuals_for_curve["cost"] = projected_final_cost
                actuals_for_curve["clk"] = int(
                    (Decimal(actuals["clk"]) * projection).to_integral_value()
                )
                actuals_for_curve["conv_amt"] = int(
                    (Decimal(actuals.get("conv_amt", 0)) * projection).to_integral_value()
                )

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
                "snapshot_hour": actuals["snapshot_hour"],
                "completeness": hour_completeness,
                "projection_factor": projection,
                "projected_final_cost": projected_final_cost,
            }
            decisions.append(decision)

            _log_flight_decision(db, campaign_id=cid, result=decision, dry_run=dry_run, now=now)

        except Exception as e:
            log.exception("flight_loop: campaign %s 처리 실패: %s", cid, e)
            decisions.append({"campaign_id": cid, "error": str(e)})

    decided = sum(1 for d in decisions if "alpha" in d)
    errors = sum(1 for d in decisions if "error" in d)
    skipped = sum(skip_breakdown.values())
    summary = {
        "campaigns_processed": len(decisions),
        "decided": decided,
        "skipped": skipped,
        "skip_breakdown": skip_breakdown,
        "errors": errors,
        "dry_run": dry_run,
    }

    # ★"N캠페인 처리"만 찍으면 전원 스킵도 정상 완주로 보인다(2026-07-25~28 실사고:
    # 크론은 매 2시간 ok였고 예외 로그도 0건인데 결정은 4일간 하나도 없었다).
    # 결정 0 = 이 루프의 존재 이유가 사라진 상태이므로 로그 레벨을 올리고 진단 행을 남긴다.
    if decided == 0:
        log.warning(
            "flight_loop: %d캠페인 전원 무결정 — 스킵 %d %s, 오류 %d (dry_run=%s)",
            len(decisions), skipped, skip_breakdown, errors, dry_run,
        )
        _log_flight_silence(db, summary=summary, now=now)
    else:
        log.info(
            "flight_loop: %d캠페인 처리 — 결정 %d, 스킵 %d %s, 오류 %d (dry_run=%s)",
            len(decisions), decided, skipped, skip_breakdown, errors, dry_run,
        )

    db.commit()
    return {**summary, "decisions": decisions}
