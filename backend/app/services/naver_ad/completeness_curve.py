# completeness_curve.py — completeness_curve SA (D-NAO-44)
# 역할: /stats 당일누적의 시각별 완결도 곡선을 실측 이력(naver_hourly_snapshot의 시각별
#   누적cost 대 naver_ad_daily 확정치 비율)으로 산출한다. flight_loop(harness)가 이 곡선으로
#   오늘 보이는 cost를 예상최종치로 보정해 페이싱 판단(αB)에 쓴다(PLAN_naver-ad-pacing-correction §2/§3).
#   순수 읽기 SA — 쓰기·외부 API 호출 없음(원칙18-1).
#   근거: docs/references/data/mop_ui/naver_stat_field_cadence_20260716.md (완결도 곡선 v2).
#   ⚠️ 집계 규약(치명): naver_ad_daily에 sentinel(BACKFILL_SENTINEL_ADGROUP, 캠페인 grain)과
#   상세행이 공존한다. 확정치는 반드시 sentinel 행만 사용할 것 — 전행 SUM은 정확히 2배
#   (이중계산, failures.jsonl 2026-07-16).
from __future__ import annotations

import statistics
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverHourlySnapshot
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_today


def build_curve(
    db: Session, *, today: date | None = None,
    lookback_days: int = 14, min_daily_cost: int = 50_000,
) -> dict[int, dict]:
    """캠페인-일별 (시각별 누적cost / 확정 최종cost) 비율의 시각별 median.

    today: as-of 기준일 — 리플레이/백테스트에서 호출자(flight_loop)의 루프 날짜를 전달
      (codex[P2]: 벽시계 kst_today()만 쓰면 시뮬레이션 날짜 이후 확정치가 표본에 누출됨).
      미지정 시 kst_today().
    finals: naver_ad_daily WHERE adgroup_id == BACKFILL_SENTINEL_ADGROUP
      AND ad_date < today (기준일 당일은 미확정이라 제외) AND ad_date >= today-lookback_days.
      final_cost >= min_daily_cost인 캠페인-일만 포함(저볼륨 노이즈 배제).
    samples: naver_hourly_snapshot의 같은 (campaign_id, ad_date) 조합, 시각별 누적cost.
    비율은 캠페인-일 단위로 구한 뒤 시각별 median을 취한다(합산비 아님 — 캠페인 동등 가중,
    큰 캠페인이 곡선을 지배하지 않게 함).
    hourly_snapshot 보존기간 7일이라 lookback_days는 상한일 뿐(있는 만큼만 반영).

    반환: {hour: {"completeness": Decimal, "n": int}}. 표본 없는 시각은 키 자체가 없음.
    """
    today = today or kst_today()
    date_from = today - timedelta(days=lookback_days)

    finals_rows = (
        db.query(NaverAdDaily.campaign_id, NaverAdDaily.ad_date, NaverAdDaily.cost)
        .filter(
            NaverAdDaily.adgroup_id == BACKFILL_SENTINEL_ADGROUP,
            NaverAdDaily.ad_date < today,
            NaverAdDaily.ad_date >= date_from,
        )
        .all()
    )
    finals: dict[tuple[str, object], int] = {}
    for campaign_id, ad_date, cost in finals_rows:
        if cost and cost >= min_daily_cost:
            finals[(campaign_id, ad_date)] = cost

    if not finals:
        return {}

    campaign_ids = {cid for cid, _ in finals}
    snapshot_rows = (
        db.query(
            NaverHourlySnapshot.campaign_id, NaverHourlySnapshot.ad_date,
            NaverHourlySnapshot.snapshot_hour, NaverHourlySnapshot.cost,
        )
        .filter(
            NaverHourlySnapshot.campaign_id.in_(campaign_ids),
            NaverHourlySnapshot.ad_date < today,
            NaverHourlySnapshot.ad_date >= date_from,
        )
        .all()
    )

    ratios_by_hour: dict[int, list[Decimal]] = {}
    for campaign_id, ad_date, hour, cost in snapshot_rows:
        final_cost = finals.get((campaign_id, ad_date))
        if final_cost is None:
            continue
        ratio = Decimal(cost) / Decimal(final_cost)
        ratios_by_hour.setdefault(hour, []).append(ratio)

    curve: dict[int, dict] = {}
    for hour, ratios in ratios_by_hour.items():
        curve[hour] = {"completeness": statistics.median(ratios), "n": len(ratios)}
    return curve


def projection_factor(
    curve: dict[int, dict], hour: int, *,
    min_completeness: Decimal = Decimal("0.10"), min_samples: int = 5,
) -> Decimal | None:
    """시각 hour 완결도로부터 예상최종치 보정계수(= 1/completeness) 산출.

    예상최종 = 보이는값(오늘 누적cost) × projection_factor(hour).
    fail-safe(None) 조건: hour 표본 없음 / n < min_samples / completeness < min_completeness
    (오전 0~9시대는 completeness가 자연히 낮아 자동 차단됨).
    """
    entry = curve.get(hour)
    if entry is None:
        return None
    if entry["n"] < min_samples:
        return None
    completeness = entry["completeness"]
    if completeness < min_completeness:
        return None
    return Decimal(1) / completeness
