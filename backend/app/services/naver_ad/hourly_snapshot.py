# hourly_snapshot.py — naver_watchdog_harness 일부 (시간별 스냅샷 SA+저장)
# 역할: 매시간 /stats로 당일 누적 캠페인 지표(cost/clk/imp)를 naver_hourly_snapshot에 적재.
#   빠른 루프(관찰·페이싱, D-NAO-4)의 데이터 기반. 직접 쓰기 금지(관찰만). 7일 롤링 정리.
# 소스: /stats id 단수(datePreset=today) — salesAmt=cost 정합 실증(docs/references/21).
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete, or_
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

    ★같은 (날짜, 시각) 슬롯에 **값이 있는 행이 이미 있으면 적재하지 않고 skip**한다
      (force=True면 교체). 이유는 순서대로:
      ①**페이싱 차분 수학** — `hourly_pacing`은 시간당 증분을 슬롯 간 차분으로 낸다. 시간
        중간에 다시 찍어 슬롯을 교체하면 그 시간 증분은 과대(예: 100분치)·다음 시간은
        과소(20분치)가 된다. 12개 모듈이 그 위에 있다. **이 이유만으로 가드는 정당하다** —
        원천이 얼마나 자주 갱신되든 슬롯의 기준시각이 흔들리는 것 자체가 왜곡이다.
      ②~~재실행의 이득도 작다~~ **이 근거는 실측으로 철회했다.** 2026-08-06 4분 간격 16회
        관측(19:26~20:29)에서 값은 시간 내에 **두 번** 바뀌었다: 19:26~20:00 9회 506,370 →
        20:04~20:25 6회 **398,102**(후퇴, 17시 값과 동일) → 20:29 **543,125**(회복·전진).
        즉 갱신은 시간 단위가 아니고, 가드는 **최대 59분치 새 정보를 버린다**(비용이 0이 아니다).
        그래도 가드를 유지하는 이유는 ①이다 — 슬롯 기준시각이 흔들리면 12개 모듈의 증분이
        틀리는데, 「오늘 광고비」 카드의 신선도는 소비 측에서 다른 수단으로 올릴 수 있다.
        (스냅샷과 별개의 관측 테이블이 필요하며 그건 스키마 변경이므로 별 계약 — 이월.)
      실사례: 2026-08-06 16:46 수동 실행이 16시 슬롯을 실제로 밀었다. 트리거 라우터 정본화로
      수동 실행이 가능해진 이래 이걸 막는 장치가 없었다.

    ★**전부 0인 슬롯은 "있음"으로 세지 않는다.** 02:01에 수동 실행해 전부 0인 2시 슬롯이
      생기면, 종전 규칙이라면 02:05 크론이 skip해 **03시까지 0원이 고정**된다(갱신하려는 행동이
      데이터를 더 낡게 만든다). 0은 증분을 만들지 않으므로 다시 채워도 페이싱 왜곡이 없다.

    ★force는 코드 경로 전용 탈출구다 — **HTTP 트리거로 노출하지 않으며, 현재 프로덕션 호출자는
      없다**(테스트만 쓴다). 부분 적재 슬롯을 사람이 복구할 경로는 아직 없다는 뜻이다.
      부분 적재는 반환 dict의 `partial`·경고 로그로 **관측만** 한다(라이브 빈도 0: 최근 7일
      185슬롯 전부 46행).
    """
    # ★슬롯 판정은 API 호출 **전에** 한다 — skip이면 호출 자체가 낭비다.
    guard_now = kst_now()
    today = kst_today()
    hour = guard_now.hour

    if not force:
        # 값이 있는 행만 센다(위 "전부 0인 슬롯은 있음이 아니다" 참조).
        existing = db.query(NaverHourlySnapshot).filter(
            NaverHourlySnapshot.ad_date == today,
            NaverHourlySnapshot.snapshot_hour == hour,
            or_(NaverHourlySnapshot.cost > 0, NaverHourlySnapshot.imp > 0),
        ).count()
        if existing:
            kept_at = db.query(sqlfunc.min(NaverHourlySnapshot.snapshot_at)).filter(
                NaverHourlySnapshot.ad_date == today,
                NaverHourlySnapshot.snapshot_hour == hour,
            ).scalar()
            reason = (f"{hour:02d}시 슬롯에 값이 이미 있다({existing}행, 최초 기록 "
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

    # snapshot_at은 **관측 시각**(fetch 직후)이고, 슬롯의 hour는 위 가드가 판정한 그 시각이다.
    # 시(hour)를 앞서 고정하는 건 이득이다 — 14:59:40 실행이 15:00:10에 끝나도 14시가 보존된다.
    now = kst_now()

    # ★★날짜는 다르다 — fetch가 **자정을 넘었으면 적재하지 않는다.**
    #   payload의 "today"(date_preset=today)는 **네이버가 응답한 시점의 오늘**이다. 23:05 발화가
    #   밀려 23:59:5x에 실행되면 응답은 다음날치(전 캠페인 0)인데 슬롯은 전날 23시가 된다 →
    #   전날 누적이 마지막 시간에 0으로 붕괴하고, 그 곡선을 `completeness_curve`가 학습하며
    #   `budget_allocator`·`trigger_watch`·`perf_today_harness`가 그 행을 "최신 누적"으로 읽는다.
    #   전부 조용히 틀린다. 시경계는 hour 보존이 이득이지만 **날짜경계는 payload와 슬롯이 서로
    #   다른 날을 가리키므로 쓰지 않는 것이 유일하게 정직하다.** 다음 :05가 재기회다.
    if kst_today() != today:
        reason = (f"fetch 중 날짜가 바뀌었다({today} → {kst_today()}) — 응답은 새 날짜치인데 "
                  f"슬롯은 {today} {hour:02d}시라 적재하지 않는다(다음 정시 수집이 재기회)")
        log.warning("naver_hourly_snapshot 적재 취소: %s", reason)
        db.rollback()
        return {"skipped": True, "reason": reason, "hour": hour,
                "date_rolled": True, "campaigns": 0, "total_cost": 0}

    # ★부분 적재 관측 — `fetch_campaign_stats`는 캠페인 1건씩 조회하며 비-200/빈 응답을 건너뛴다.
    #   즉 "46개 중 20개"는 예외가 아니라 그 함수의 정상 반환 형태다. 금액 축은 소비 측에서
    #   막았지만(`naver_ops`가 캠페인별 당일 최대 누적을 쓴다) 페이싱은 여전히 흔들리므로
    #   **최소한 관측 가능하게** 한다. 자동 복구 경로는 아직 없다(force에 호출자가 없다).
    partial = bool(campaigns) and len(stats) < len(campaigns)
    if partial:
        log.warning("naver_hourly_snapshot 부분 적재: %d/%d 캠페인만 응답(시각 %02d시)",
                    len(stats), len(campaigns), hour)

    # 같은 시각 슬롯 교체 — force=True 경로 외에도, 전부 0인 슬롯을 다시 채울 때 지난다.
    # ★죽은 코드가 아니다: 크론(:05)과 수동 트리거가 겹치면(수동은 FastAPI 요청 스레드라
    #   max_instances=1이 안 막는다) UniqueConstraint(ad_date, campaign_id, snapshot_hour)
    #   위반을 흡수하는 유일한 장치다. 지우면 IntegrityError → EVENT_JOB_ERROR가 난다.
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
    log.info("naver_hourly_snapshot: %d캠페인 (시각 %02d시, 당일누적 cost=%d)%s",
             len(stats), hour, total_cost, " ⚠️부분적재" if partial else "")
    return {"campaigns": len(stats), "hour": hour, "total_cost": total_cost,
            "partial": partial, "campaigns_expected": len(campaigns)}
