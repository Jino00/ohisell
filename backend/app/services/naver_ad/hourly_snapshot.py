# hourly_snapshot.py — naver_watchdog_harness 일부 (시간별 스냅샷 SA+저장)
# 역할: 매시간 /stats로 당일 누적 캠페인 지표(cost/clk/imp)를 naver_hourly_snapshot에 적재.
#   빠른 루프(관찰·페이싱, D-NAO-4)의 데이터 기반. 직접 쓰기 금지(관찰만). 7일 롤링 정리.
# 소스: /stats id 단수(datePreset=today) — salesAmt=cost 정합 실증(docs/references/21).
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import NaverHourlySnapshot
from app.services.naver_sa_ad_fetcher import fetch_campaign_stats, get_campaigns_full
from app.utils.kst import kst_now, kst_today

log = logging.getLogger(__name__)

# D-NAO-46(2026-07-16 Jino): 시간별 원시 시계열 = 학습 데이터 영구 축적 방침 — 기존 7일
# 롤링을 365일로 연장. 규모 무해(캠페인 ~25개×24h ≈ 600행/일 ≈ 22만행/년). 완결도 곡선
# (D-NAO-44)·시간당 관제 루프(D-NAO-46 설계 예정)의 표본도 이만큼 깊어진다.
_RETAIN_DAYS = 365


def snapshot_hourly(db: Session, *, campaigns: list[dict] | None = None,
                    stats: list[dict] | None = None) -> dict:
    """당일 캠페인 누적 스냅샷을 (오늘, 캠페인, 현재시각) grain으로 upsert.

    campaigns/stats는 테스트 주입용(원칙18-8). 미주입 시 fetcher 조회.
    같은 (날짜, 캠페인, 시각) 재실행 시 교체(멱등). _RETAIN_DAYS 초과 행 삭제.
    """
    if campaigns is None:
        try:
            campaigns = get_campaigns_full()
        except Exception as e:
            log.warning("campaigns 조회 실패: %s", e)
            campaigns = []
    budget_by_id = {c["campaign_id"]: c.get("daily_budget") for c in campaigns}
    type_by_id = {c["campaign_id"]: c.get("campaign_type", "") for c in campaigns}

    if stats is None:
        ids = [c["campaign_id"] for c in campaigns]
        stats = fetch_campaign_stats(ids, date_preset="today") if ids else []

    now = kst_now()
    today = kst_today()
    hour = now.hour

    # 같은 시각 슬롯 교체(멱등)
    existing_ids = [s["campaign_id"] for s in stats]
    if existing_ids:
        db.execute(delete(NaverHourlySnapshot).where(
            NaverHourlySnapshot.ad_date == today,
            NaverHourlySnapshot.snapshot_hour == hour,
            NaverHourlySnapshot.campaign_id.in_(existing_ids),
        ))
    for s in stats:
        db.add(NaverHourlySnapshot(
            snapshot_at=now,
            ad_date=today,
            snapshot_hour=hour,
            campaign_id=s["campaign_id"],
            campaign_type=type_by_id.get(s["campaign_id"], ""),
            cost=s["cost"], clk=s["clk"], imp=s["imp"],
            daily_budget=budget_by_id.get(s["campaign_id"]),
        ))

    # 7일 롤링 정리
    cutoff = today - timedelta(days=_RETAIN_DAYS)
    db.execute(delete(NaverHourlySnapshot).where(NaverHourlySnapshot.ad_date < cutoff))
    db.commit()

    total_cost = sum(s["cost"] for s in stats)
    log.info("naver_hourly_snapshot: %d캠페인 (시각 %02d시, 당일누적 cost=%d)",
             len(stats), hour, total_cost)
    return {"campaigns": len(stats), "hour": hour, "total_cost": total_cost}
