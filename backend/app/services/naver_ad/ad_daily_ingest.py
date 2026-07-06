# ad_daily_ingest.py — naver_ad_daily_harness (수집 → 적재)
# 역할(Harness): report_collector_sa 출력을 naver_ad_daily에 snapshot 교체 적재(멱등).
#   같은 날짜 재수집 시 해당 날짜 행 전체 삭제 후 재삽입(확정치 교체 + 사라진 행 반영).
# 데이터 유통 허브(원칙18-6): 수집 SA → 영속화. 쓰기는 이 경로만(광고 계정 쓰기 아님, 로컬 DB 적재).
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import NaverAdDaily
from app.services.naver_ad.report_collector import collect_daily_rows
from app.utils.kst import kst_now

log = logging.getLogger(__name__)


def _to_date(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def ingest_ad_daily(
    db: Session,
    date_from: date,
    date_to: date,
    *,
    rows: list[dict] | None = None,
) -> dict:
    """naver_ad_daily 적재 — 날짜 단위 snapshot 교체(멱등).

    rows 미주입 시 collect_daily_rows로 수집. 적재 대상 날짜 행을 먼저 삭제 후 bulk insert.
    반환: {rows, dates, total_cost, total_conv_amt}.
    """
    if rows is None:
        rows = collect_daily_rows(date_from, date_to)

    # 날짜별 snapshot 교체: 적재 대상 날짜 집합의 기존 행 삭제
    dates = sorted({_to_date(r["ad_date"]) for r in rows})
    if dates:
        db.execute(delete(NaverAdDaily).where(NaverAdDaily.ad_date.in_(dates)))

    now = kst_now()
    for r in rows:
        db.add(NaverAdDaily(
            ad_date=_to_date(r["ad_date"]),
            campaign_id=r["campaign_id"],
            campaign_type=r.get("campaign_type", ""),
            adgroup_id=r.get("adgroup_id", ""),
            keyword_id=r.get("keyword_id", ""),
            imp=r["imp"], clk=r["clk"], cost=r["cost"], rank_sum=r["rank_sum"],
            conv_direct_cnt=r["conv_direct_cnt"], conv_indirect_cnt=r["conv_indirect_cnt"],
            conv_direct_amt=r["conv_direct_amt"], conv_indirect_amt=r["conv_indirect_amt"],
            synced_at=now,
        ))
    db.commit()

    total_cost = sum(r["cost"] for r in rows)
    total_conv = sum(r["conv_direct_amt"] + r["conv_indirect_amt"] for r in rows)
    log.info(
        "naver_ad_daily ingest: %d행 (%s~%s) cost합=%d 전환매출합=%d",
        len(rows), date_from, date_to, total_cost, total_conv,
    )
    return {
        "rows": len(rows),
        "dates": [d.isoformat() for d in dates],
        "total_cost": total_cost,
        "total_conv_amt": total_conv,
    }
