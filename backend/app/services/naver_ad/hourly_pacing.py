# hourly_pacing.py — hourly_pacing_sa (단일 책임: 시간대별 소진 산출)
# 역할(SA): naver_hourly_snapshot(당일 누적)을 시간대별 증분으로 변환해 rows 반환.
#   스냅샷 cost/clk/imp는 당일 누적 → 캠페인별로 정렬 후 이전 기록시각과 차분해 시간당 순증분 산출.
# 빠른 루프(D-NAO-4) 페이싱 뷰. 전환 데이터 없음(스냅샷은 cost/clk/imp만) → ROAS 없음.
from __future__ import annotations

from datetime import date

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverHourlySnapshot


def _latest_ad_date(db: Session, on_or_before: date | None) -> date | None:
    q = db.query(sqlfunc.max(NaverHourlySnapshot.ad_date))
    if on_or_before is not None:
        q = q.filter(NaverHourlySnapshot.ad_date <= on_or_before)
    return q.scalar()


def hourly_rows(
    db: Session,
    *,
    ad_date: date | None = None,
    on_or_before: date | None = None,
    campaign_filter: str | None = None,
) -> dict:
    """특정일 시간대별 순증분 소진(cost/clk/imp) rows 반환.

    ad_date 미지정 시 on_or_before 이하 최신 스냅샷 날짜 사용(없으면 전체 최신).
    누적→증분: 캠페인별 시각 오름차순 정렬 후 직전 기록과 차분(첫 기록은 그 값). 시간대별 합산.
    반환: {ad_date, rows:[{hour, cost, clk, imp}...](0~23 오름차순), total_cost}.
    """
    target = ad_date or _latest_ad_date(db, on_or_before)
    if target is None:
        return {"ad_date": None, "rows": [], "total_cost": 0}

    q = db.query(
        NaverHourlySnapshot.campaign_id,
        NaverHourlySnapshot.snapshot_hour,
        NaverHourlySnapshot.cost,
        NaverHourlySnapshot.clk,
        NaverHourlySnapshot.imp,
    ).filter(NaverHourlySnapshot.ad_date == target)
    if campaign_filter:
        q = q.filter(NaverHourlySnapshot.campaign_id == campaign_filter)

    # 캠페인별 (hour → 누적값) 수집
    per_campaign: dict[str, list[tuple[int, int, int, int]]] = {}
    for cid, hour, cost, clk, imp in q.all():
        per_campaign.setdefault(cid, []).append((int(hour), int(cost or 0), int(clk or 0), int(imp or 0)))

    # 시간대별 증분 합산
    by_hour: dict[int, dict[str, int]] = {}
    for records in per_campaign.values():
        records.sort(key=lambda x: x[0])  # 시각 오름차순
        prev = (0, 0, 0)  # (cost, clk, imp) 누적 직전값
        for hour, cost, clk, imp in records:
            # 누적 감소(리셋/재적재 이상치)는 0으로 클램프
            inc_cost = max(0, cost - prev[0])
            inc_clk = max(0, clk - prev[1])
            inc_imp = max(0, imp - prev[2])
            slot = by_hour.setdefault(hour, {"cost": 0, "clk": 0, "imp": 0})
            slot["cost"] += inc_cost
            slot["clk"] += inc_clk
            slot["imp"] += inc_imp
            prev = (cost, clk, imp)

    rows = [{"hour": h, **by_hour[h]} for h in sorted(by_hour)]
    total_cost = sum(r["cost"] for r in rows)
    return {"ad_date": target.isoformat(), "rows": rows, "total_cost": total_cost}
