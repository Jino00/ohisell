# hourly_pattern.py — hourly_pattern Harness (듀얼모드 스프린트 Phase 6, D-NAO-14 학습루프5)
# 역할: naver_hourly_snapshot(7일 롤링, hourly_snapshot.py _RETAIN_DAYS)이 매일 지워지기 전에
#   전날 시간대별 순증분(hourly_pacing SA 재사용)을 요일×시간(168칸) 누적 테이블에 무기한
#   합산한다. 이 파일은 이 기능의 harness다(원칙18-6·트리거감시 trigger_watch.py와 동일
#   전례 — hourly_pacing을 직접 호출해도 됨, SA간 직접 호출 금지는 다른 "SA"끼리에 적용되고
#   harness는 SA를 조합하는 게 본연의 역할). 전환 데이터는 시간대 grain에 없어(hourly_snapshot
#   은 cost/clk/imp만) 이 학습은 "클릭 분포"를 성과 신호로 쓴다 — 전환 기반 추천은 아니다
#   (정직 경계, 추정 금지).
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import NaverHourlyPatternHistory, NaverLearningState
from app.services.naver_ad import hourly_pacing
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

_Q4 = Decimal("0.0001")
METRIC = "hour_weight"
# 신뢰도 목표 — 같은 요일이 이 정도 관측되면(4주=한 달) 충분히 안정적이라고 봄(주차 누적
# 취지, D-NAO-14 "168칸이 주차 누적으로 정교화"). 통계적 유의성 검정 상수는 아님.
TARGET_SAMPLE_WEEKS = 4


def fold_yesterday(db: Session, *, today: date | None = None) -> dict:
    """어제(today-1)의 24시간 순증분을 (weekday, hour)별로 무기한 누적 테이블에 합산.

    같은 날짜를 두 번 접지 않도록 last_folded_date로 멱등 처리 — 재실행해도 중복 합산 없음.
    naver_hourly_snapshot(hourly_pacing 소스)이 7일 뒤 삭제되므로 이 fold는 그 전에 반드시
    1회 실행돼야 데이터가 유실되지 않는다(스케줄러가 매일 실행 보장).
    반환: {"target_date": iso, "weekday": int, "hours_folded": int, "already_folded": bool}.
    """
    today = today or kst_today()
    target_date = today - timedelta(days=1)
    weekday = target_date.weekday()

    pacing = hourly_pacing.hourly_rows(db, ad_date=target_date)
    if not pacing["rows"]:
        return {"target_date": target_date.isoformat(), "weekday": weekday, "hours_folded": 0, "already_folded": False}

    hours_folded = 0
    for r in pacing["rows"]:
        row = db.query(NaverHourlyPatternHistory).filter(
            NaverHourlyPatternHistory.weekday == weekday, NaverHourlyPatternHistory.hour == r["hour"],
        ).first()
        if row is None:
            row = NaverHourlyPatternHistory(weekday=weekday, hour=r["hour"], clk_sum=0, cost_sum=0, sample_days=0)
            db.add(row)
        if row.last_folded_date == target_date:
            continue  # 이미 이 날짜를 이 셀에 반영함(재실행 멱등)
        row.clk_sum += r["clk"]
        row.cost_sum += r["cost"]
        row.sample_days += 1
        row.last_folded_date = target_date
        hours_folded += 1

    db.commit()
    return {
        "target_date": target_date.isoformat(), "weekday": weekday,
        "hours_folded": hours_folded, "already_folded": hours_folded == 0,
    }


def compute_bid_weight_recommendations(db: Session, *, weekday: int | None = None) -> list[dict]:
    """요일(들)별 24시간 클릭분포 지수(index=이 시간대 평균클릭/그 요일 24시간 평균클릭×100) —
    100=평균, >100=평균보다 클릭이 몰리는 시간대(성과 신호는 전환이 아니라 클릭량, 정직 경계).

    반환: [{"weekday","hour","index","sample_days"}]. 그 요일에 관측 자체가 없는 셀은 제외
    (0으로 채우면 "관측했는데 0"과 "관측 안 함"이 구분 안 됨 — 추정 금지).
    """
    q = db.query(NaverHourlyPatternHistory)
    if weekday is not None:
        q = q.filter(NaverHourlyPatternHistory.weekday == weekday)
    rows = q.all()
    if not rows:
        return []

    by_weekday: dict[int, list[NaverHourlyPatternHistory]] = {}
    for r in rows:
        by_weekday.setdefault(r.weekday, []).append(r)

    out: list[dict] = []
    for wd, cells in by_weekday.items():
        avg_clks = [Decimal(c.clk_sum) / Decimal(c.sample_days) for c in cells if c.sample_days > 0]
        if not avg_clks:
            continue
        day_avg = sum(avg_clks) / Decimal(len(avg_clks))
        if day_avg <= 0:
            continue
        for c in cells:
            if c.sample_days <= 0:
                continue
            cell_avg = Decimal(c.clk_sum) / Decimal(c.sample_days)
            index = ((cell_avg / day_avg) * 100).quantize(_Q4)
            out.append({"weekday": wd, "hour": c.hour, "index": float(index), "sample_days": c.sample_days})
    return out


def expected_cost_fraction(db: Session, *, weekday: int, hour: int) -> Decimal | None:
    """weekday의 0~hour(포함) 누적 cost_sum ÷ 전체 24시간 cost_sum(관측된 셀만) — 지출분포
    곡선에서 현재 시각까지 기대되는 누적 소진 비율(F2b ⓒ, trigger_watch 페이싱 예측곡선의 원료).

    관측 없으면(그 요일 데이터 자체가 없거나 관측된 셀의 총합이 0) None — 추정 금지, 호출자가
    선형모델(elapsed/24h)로 폴백한다.
    """
    rows = db.query(NaverHourlyPatternHistory).filter(NaverHourlyPatternHistory.weekday == weekday).all()
    observed = [r for r in rows if r.sample_days > 0]
    total = sum(r.cost_sum for r in observed)
    if total <= 0:
        return None
    cumulative = sum(r.cost_sum for r in observed if r.hour <= hour)
    return (Decimal(cumulative) / Decimal(total)).quantize(_Q4)


def _upsert_learning_state(db: Session, *, scope_key: str, value: Decimal, sample_n: int, confidence: Decimal) -> None:
    row = db.query(NaverLearningState).filter(
        NaverLearningState.scope == "global", NaverLearningState.scope_key == scope_key,
        NaverLearningState.metric == METRIC,
    ).first()
    if row is None:
        row = NaverLearningState(scope="global", scope_key=scope_key, metric=METRIC)
        db.add(row)
    row.current_value = value
    row.sample_n = sample_n
    row.confidence = confidence


def run_daily(db: Session, *, today: date | None = None) -> dict:
    """매일 실행 — ①어제 데이터 fold ②전체 168칸(관측된 것만) 지수 재계산해
    NaverLearningState(scope=global, scope_key=f"{weekday}_{hour}", metric=hour_weight)에 기록.
    적용(bidWeight API 반영)은 하지 않는다(D-NAO-3 "적용은 Confirm" — 추천안 산출까지만).
    """
    fold_result = fold_yesterday(db, today=today)
    recs = compute_bid_weight_recommendations(db)
    for rec in recs:
        confidence = min(Decimal(1), Decimal(rec["sample_days"]) / Decimal(TARGET_SAMPLE_WEEKS))
        _upsert_learning_state(
            db, scope_key=f"{rec['weekday']}_{rec['hour']}", value=Decimal(str(rec["index"])),
            sample_n=rec["sample_days"], confidence=confidence.quantize(_Q4),
        )
    db.commit()
    log.info("hourly_pattern: fold=%s, %d개 셀 지수 갱신", fold_result, len(recs))
    return {**fold_result, "cells_updated": len(recs)}
