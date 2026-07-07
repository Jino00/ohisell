# campaign_backfill.py — naver_campaign_backfill_harness (과거 데이터 소급 적재, D-NAO-17)
# 역할: /stats(timeRange)로 캠페인 단위 일별 성과를 최대 730일 소급 수집해 naver_ad_daily에
#   campaign-grain(adgroup_id=keyword_id='') 행으로 적재. stat-reports(16일 보관)보다 훨씬
#   긴 창이지만 그룹/키워드 세부는 없음 — 용도는 계정/캠페인 레벨 패턴 학습(D-NAO-17: 악순환
#   임계값·다기간 비교·168칸 시간대. 키워드 단위 인과 학습은 아님, 정직 경계 문서화 필수).
# 실측 한도(docs/references/22): 최근 730일 이내만 조회 가능, 호출당 daily breakdown 92일 한도.
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import NaverAdDaily
from app.services.naver_sa_ad_fetcher import fetch_campaign_daily_backfill, get_campaigns_full
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

BACKFILL_SENTINEL_ADGROUP = "__backfill__"  # naver_ad_daily 실단위(P0) 행과 겹치지 않게 구분


def backfill_campaign_daily(
    db: Session, date_from: date, date_to: date, *,
    campaigns: list[dict] | None = None,
) -> dict:
    """전 캠페인의 /stats 소급 데이터를 naver_ad_daily에 적재(campaign grain, sentinel adgroup).

    campaign_type은 유지하되 adgroup_id/keyword_id는 BACKFILL_SENTINEL_ADGROUP 고정 —
    P0 실단위 행(AD/AD_CONVERSION 소스)과 물리적으로 구분해 이중집계를 방지한다.
    같은 (campaign, 날짜) 재실행 시 교체(멱등).
    """
    if campaigns is None:
        campaigns = get_campaigns_full()

    all_rows: list[dict] = []
    for c in campaigns:
        rows = fetch_campaign_daily_backfill(c["campaign_id"], date_from, date_to)
        for r in rows:
            r["campaign_type"] = c.get("campaign_type", "")
        all_rows.extend(rows)

    dates = sorted({date.fromisoformat(r["date"]) for r in all_rows})
    campaign_ids = sorted({r["campaign_id"] for r in all_rows})
    if dates and campaign_ids:
        db.execute(delete(NaverAdDaily).where(
            NaverAdDaily.ad_date.in_(dates),
            NaverAdDaily.campaign_id.in_(campaign_ids),
            NaverAdDaily.adgroup_id == BACKFILL_SENTINEL_ADGROUP,
        ))

    now = kst_now()
    for r in all_rows:
        db.add(NaverAdDaily(
            ad_date=date.fromisoformat(r["date"]), campaign_id=r["campaign_id"],
            campaign_type=r.get("campaign_type", ""),
            adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="",
            imp=r["imp"], clk=r["clk"], cost=r["cost"], rank_sum=0,
            # /stats는 직접/간접을 분리하지 않음 — 합계를 conv_indirect_*에 몰아 저장
            # (direct=0 고정, 합산 총액은 정확·구성비만 미상. P0 실단위 행과 sentinel로 구분되어 있어
            # 리포트 집계 시 이중가산 없음. 컬럼 재활용에 불과, "간접전환"이란 의미는 아님).
            conv_direct_cnt=0, conv_indirect_cnt=r["conv_cnt"],
            conv_direct_amt=0, conv_indirect_amt=r["conv_amt"],
            synced_at=now,
        ))
    db.commit()

    total_cost = sum(r["cost"] for r in all_rows)
    log.info("campaign_backfill: %d캠페인 %d행 (%s~%s, %d~%d일) cost합=%d",
              len(campaigns), len(all_rows), date_from, date_to,
              (date_to - date_from).days, len(dates), total_cost)
    return {
        "campaigns": len(campaigns), "rows": len(all_rows),
        "date_range": [date_from.isoformat(), date_to.isoformat()],
        "dates_covered": len(dates), "total_cost": total_cost,
    }
