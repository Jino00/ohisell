# anomaly_feed.py — anomaly_feed SA (경량, 듀얼모드 스프린트 Phase 3, D-NAO-22-④ 앞부분)
# 역할(SA): naver_ad_daily만 읽어 ①부분적재(freshness) ②캠페인별 소진 급변 이상을 가볍게
#   산출한다. 판정만 하고 액션 없음(D-3, 사실 정리) — 콘솔 노출 + Slack으로 사람이 확인.
#   S3a codex 지적(연기분) 해소: 기존 freshness 게이트(proposal_pipeline._freshness_gate)는
#   "as_of 행이 1건이라도 있는지"만 보고 부분적재(07:30 sync가 절반만 돌고 죽은 경우)는
#   못 잡았다 — 이 SA가 "최근 평균 대비 오늘 행수가 너무 적다"는 상대 비교로 그 틈을 메운다.
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

_Q4 = Decimal("0.0001")

# 부분적재 판정 — as_of 행수가 최근 baseline 평균의 이 비율 미만이면 "부분적재 의심"(경량
# 휴리스틱, 실측 통계적 검정 아님 — 사람이 확인할 신호일 뿐 자동 액션 근거 아님이라 엄격한
# 통계적 근거보다 운영상 상식적 여유폭을 채택. 조정 필요 시 Jino 재검토).
FRESHNESS_BASELINE_LOOKBACK_DAYS = 7
FRESHNESS_MIN_RATIO = Decimal("0.5")

# 소진 급변 — 전일 대비 캠페인별 cost 비율. 마찬가지로 통계적 임계값이 아니라 "사람이 한 번
# 볼 만큼 크게 움직였다"는 상식적 배수(2배/절반) — 자동 실행 근거 아님, 콘솔·Slack 노출용.
SPEND_SPIKE_RATIO = Decimal("2")
SPEND_DROP_RATIO = Decimal("0.5")


def _real_daily_cost_by_campaign(db: Session, ad_date: date) -> dict[str, int]:
    """특정일 캠페인별 실단위(비-backfill) cost 합계."""
    rows = (
        db.query(NaverAdDaily.campaign_id, sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0))
        .filter(
            NaverAdDaily.ad_date == ad_date,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.campaign_id)
        .all()
    )
    return {cid: int(cost) for cid, cost in rows}


def freshness_partial_load(
    db: Session, as_of: date, *, lookback_days: int = FRESHNESS_BASELINE_LOOKBACK_DAYS,
) -> dict:
    """as_of 행수를 [as_of-lookback_days, as_of-1] 창의 일평균 행수와 비교.

    baseline_avg=0(창 안에 실데이터가 아직 없음, 파이프라인 초기)이면 비교 불가 —
    partial=False·reason 명시(부정확 신호를 만들지 않음, 추정 금지).
    반환: {as_of, as_of_count, baseline_avg, ratio, partial, reason}.
    """
    window_from = as_of - timedelta(days=lookback_days)
    window_to = as_of - timedelta(days=1)

    as_of_count = int(
        db.query(sqlfunc.count(NaverAdDaily.id))
        .filter(NaverAdDaily.ad_date == as_of, NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP)
        .scalar() or 0
    )
    daily_counts = dict(
        db.query(NaverAdDaily.ad_date, sqlfunc.count(NaverAdDaily.id))
        .filter(
            NaverAdDaily.ad_date >= window_from, NaverAdDaily.ad_date <= window_to,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.ad_date).all()
    )
    days_with_data = len(daily_counts)
    if days_with_data == 0:
        return {
            "as_of": as_of.isoformat(), "as_of_count": as_of_count, "baseline_avg": None,
            "ratio": None, "partial": False, "reason": "baseline 창에 실데이터 없음(비교 불가)",
        }

    baseline_avg = Decimal(sum(daily_counts.values())) / Decimal(days_with_data)
    if baseline_avg == 0:
        return {
            "as_of": as_of.isoformat(), "as_of_count": as_of_count, "baseline_avg": 0.0,
            "ratio": None, "partial": False, "reason": "baseline 평균이 0(비교 불가)",
        }

    ratio = (Decimal(as_of_count) / baseline_avg).quantize(_Q4)
    partial = ratio < FRESHNESS_MIN_RATIO
    return {
        "as_of": as_of.isoformat(), "as_of_count": as_of_count,
        "baseline_avg": float(baseline_avg.quantize(_Q4)), "ratio": float(ratio),
        "partial": partial,
        "reason": (
            f"최근 {days_with_data}일 평균({baseline_avg.quantize(_Q4)}행) 대비 "
            f"{ratio*100}%로 부분적재 의심" if partial else "정상"
        ),
    }


def spend_anomalies(db: Session, as_of: date) -> list[dict]:
    """[as_of] vs [as_of-1일] 캠페인별 cost 급증/급감 — prior=0(신규 집행 시작)은 비율
    미정의라 제외. as_of가 완전히 0으로 떨어진 경우(캠페인 사실상 중단)도 놓치지 않도록
    today_cost/prior_cost 양쪽 캠페인ID 합집합을 순회한다(codex 지적 — today_cost만
    순회하면 today=0인 행 자체가 없어 완전 중단이 안 잡혔다).

    반환: [{campaign_id, as_of, prior_date, cost_today, cost_prior, ratio, kind}],
    |ratio-1| 내림차순. kind: "spike"(SPEND_SPIKE_RATIO 이상) / "drop"(SPEND_DROP_RATIO 이하).
    날짜 라벨은 "오늘/어제"가 아니라 실제 ISO 날짜로 반환한다 — as_of는 harness에 따라
    항상 "오늘"이 아닐 수 있어(run_daily은 확정치 어제를 씀) "오늘"이라 쓰면 오해의 소지가
    있다(codex 지적).
    """
    prior_date = as_of - timedelta(days=1)
    today_cost = _real_daily_cost_by_campaign(db, as_of)
    prior_cost = _real_daily_cost_by_campaign(db, prior_date)

    out = []
    for cid in set(today_cost) | set(prior_cost):
        cost_today = today_cost.get(cid, 0)
        cost_prior = prior_cost.get(cid, 0)
        if cost_prior <= 0:
            continue
        ratio = (Decimal(cost_today) / Decimal(cost_prior)).quantize(_Q4)
        if ratio >= SPEND_SPIKE_RATIO:
            kind = "spike"
        elif ratio <= SPEND_DROP_RATIO:
            kind = "drop"
        else:
            continue
        out.append({
            "campaign_id": cid, "as_of": as_of.isoformat(), "prior_date": prior_date.isoformat(),
            "cost_today": cost_today, "cost_prior": cost_prior, "ratio": float(ratio), "kind": kind,
        })
    out.sort(key=lambda x: abs(x["ratio"] - 1), reverse=True)
    return out
