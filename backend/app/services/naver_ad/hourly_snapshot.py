# hourly_snapshot.py — naver_watchdog_harness 일부 (시간별 스냅샷 SA+저장)
# 역할: 매시간 /stats로 당일 누적 캠페인 지표(cost/clk/imp)를 naver_hourly_snapshot에 적재.
#   빠른 루프(관찰·페이싱, D-NAO-4)의 데이터 기반. 직접 쓰기 금지(관찰만). 7일 롤링 정리.
# 소스: /stats id 단수(datePreset=today) — salesAmt=cost 정합 실증(docs/references/21).
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy import func as sqlfunc
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
                    stats: list[dict] | None = None, force: bool = False) -> dict:
    """당일 캠페인 누적 스냅샷을 (오늘, 캠페인, 현재시각) grain으로 적재.

    campaigns/stats는 테스트 주입용(원칙18-8). 미주입 시 fetcher 조회.
    _RETAIN_DAYS 초과 행 삭제.

    ★같은 (날짜, 시각) 슬롯이 이미 있으면 **적재하지 않고 skip**한다(force=True면 교체).
      왜냐하면 `hourly_pacing`은 시간당 증분을 **슬롯 간 차분**으로 낸다 — 시간 중간에
      다시 찍어 슬롯을 교체하면 그 시간 증분은 과대(예: 100분치), 다음 시간은 과소(20분치)가
      된다. 12개 모듈이 그 위에 있다. 2026-08-06 16:46 수동 실행이 실제로 슬롯을 밀었고
      (값이 우연히 같아 숫자 피해는 없었다 — NAVER /stats 당일치가 시간 단위로만 갱신되기
      때문이다), 트리거 라우터 정본화로 수동 실행이 가능해진 이래 이걸 막는 장치가 없었다.
      **재실행은 새 정보를 주지 않으면서 페이싱만 왜곡하므로, 조용히 덮는 게 아니라 사유를
      돌려준다.** force는 코드 경로 전용 탈출구다(예: :05 수집이 일부 캠페인만 담고 끝난
      경우) — HTTP 트리거로는 노출하지 않는다.
    """
    # ★슬롯 판정은 API 호출 **전에** 한다 — skip이면 호출 자체가 낭비고, 판정에 쓴 시각과
    #   적재에 쓴 시각이 갈라지면 가드가 검사한 슬롯과 다른 슬롯에 쓸 수 있다.
    guard_now = kst_now()
    today = kst_today()
    hour = guard_now.hour

    if not force:
        existing = db.query(NaverHourlySnapshot).filter(
            NaverHourlySnapshot.ad_date == today,
            NaverHourlySnapshot.snapshot_hour == hour,
        ).count()
        if existing:
            kept_at = db.query(sqlfunc.min(NaverHourlySnapshot.snapshot_at)).filter(
                NaverHourlySnapshot.ad_date == today,
                NaverHourlySnapshot.snapshot_hour == hour,
            ).scalar()
            reason = (f"{hour:02d}시 슬롯이 이미 있다({existing}행, 최초 기록 "
                      f"{kept_at.strftime('%H:%M:%S') if kept_at else '?'}) — "
                      f"다시 찍으면 시간별 소진 증분이 왜곡되므로 건너뛴다")
            log.info("naver_hourly_snapshot skip: %s", reason)
            return {"skipped": True, "reason": reason, "hour": hour,
                    "existing_rows": existing,
                    "kept_at": kept_at.isoformat() if kept_at else None,
                    "campaigns": 0, "total_cost": 0}

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

    # snapshot_at은 **관측 시각**(fetch 직후)이고, 슬롯(ad_date·hour)은 위 가드가 판정한
    # 그 슬롯이다 — 둘을 따로 두는 이유는 fetch가 시 경계를 넘어도 검사한 슬롯에 쓰기 위함이다.
    now = kst_now()

    # 같은 시각 슬롯 교체 — force=True 경로에서만 도달한다(가드가 없던 시절의 기본 동작)
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
            avg_rank=s.get("avg_rank"),  # D-NAO-46②: fetch_campaign_stats avg_rank(없으면 None)
        ))

    # 7일 롤링 정리
    cutoff = today - timedelta(days=_RETAIN_DAYS)
    db.execute(delete(NaverHourlySnapshot).where(NaverHourlySnapshot.ad_date < cutoff))
    db.commit()

    total_cost = sum(s["cost"] for s in stats)
    log.info("naver_hourly_snapshot: %d캠페인 (시각 %02d시, 당일누적 cost=%d)",
             len(stats), hour, total_cost)
    return {"campaigns": len(stats), "hour": hour, "total_cost": total_cost}
