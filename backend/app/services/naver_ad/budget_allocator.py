# budget_allocator.py — budget_allocator SA (듀얼모드 스프린트 Phase 3, D-NAO-22-③)
# 역할(SA): "일예산 캡 소진 && 동일 캠페인 내 이익보장 잔존 볼륨 존재"라는 손익 경계 신호만으로
#   예산 증액 후보를 산출한다. D-S3-c 연기 사유였던 marginal ROAS 인과추정(예산을 늘리면
#   실제로 얼마나 더 벌지 예측하는 것)은 여전히 하지 않는다(추정 금지 원칙) — "이미 존재가
#   확인된 이익보장 키워드가 예산 캡에 막혀 있다"는 사실 관측만 제공, 예산 증액 실행은
#   D-NAO-5에 따라 영구 Confirm 게이트(캠페인 재구축·예산 상한 인상과 동급).
# SA간 직접 호출 금지(원칙18) — hourly_snapshot(오늘 실시간)과 growth_sweeper(어제 확정,
#   이미 harness가 계산해 넘긴 결과)만 읽는 순수 함수. 광고 API 호출 없음.
from __future__ import annotations

from datetime import date

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverHourlySnapshot


def _latest_hourly_rows(db: Session, ad_date: date) -> list[NaverHourlySnapshot]:
    """캠페인별 당일(ad_date) 최신 시각(snapshot_hour) 스냅샷 1행 — 누적치라 최신=당일 누적."""
    sub = (
        db.query(
            NaverHourlySnapshot.campaign_id,
            sqlfunc.max(NaverHourlySnapshot.snapshot_hour).label("latest_hour"),
        )
        .filter(NaverHourlySnapshot.ad_date == ad_date)
        .group_by(NaverHourlySnapshot.campaign_id)
        .subquery()
    )
    return (
        db.query(NaverHourlySnapshot)
        .join(
            sub,
            (NaverHourlySnapshot.campaign_id == sub.c.campaign_id)
            & (NaverHourlySnapshot.snapshot_hour == sub.c.latest_hour),
        )
        .filter(NaverHourlySnapshot.ad_date == ad_date)
        .all()
    )


def find_budget_exhausted_campaigns(db: Session, ad_date: date) -> list[dict]:
    """daily_budget이 설정된 캠페인 중 당일 누적 cost가 daily_budget에 도달한 캠페인.

    "소진"은 추정이 아니라 실측 비교(누적 지출 ≥ 네이버가 보고한 일예산) — 네이버는 예산
    도달 시 실시간 노출을 멈추므로(플랫폼 자체 동작) 이 비교 자체가 곧 캡 바인딩의 직접
    증거다. 반환: [{campaign_id, campaign_type, daily_budget, cost, hour}], cost 내림차순.
    daily_budget이 None(미설정/미동기화)인 캠페인은 비교 불가라 제외.
    """
    rows = _latest_hourly_rows(db, ad_date)
    out = [
        {
            "campaign_id": r.campaign_id, "campaign_type": r.campaign_type,
            "daily_budget": r.daily_budget, "cost": r.cost, "hour": r.snapshot_hour,
        }
        for r in rows
        if r.daily_budget is not None and r.daily_budget > 0 and r.cost >= r.daily_budget
    ]
    out.sort(key=lambda x: x["cost"], reverse=True)
    return out


def find_budget_expansion_signals(
    db: Session, ad_date: date, *, growth_candidates: list[dict],
) -> list[dict]:
    """예산 캡 소진 캠페인 중, growth_sweeper가 이미 찾아낸(별도 추정 없음) 이익보장
    잔존 볼륨이 그 캠페인 안에도 있는 것만 증액 신호로 채택.

    growth_candidates: growth_sweeper.find_growth_candidates()의 전체(estimate 적용 전)
    반환값 — harness가 재사용해 넘김(재조회 금지, N+1 방지). 각 원소의 "gap"(경제성상한-
    현재입찰)은 그 자체로 이익보장 여력의 실측 신호이지 예측이 아니다.
    반환: [{...find_budget_exhausted_campaigns 필드, growth_candidate_count, total_gap}],
    total_gap 내림차순.
    """
    exhausted = find_budget_exhausted_campaigns(db, ad_date)
    if not exhausted:
        return []

    by_campaign: dict[str, list[dict]] = {}
    for c in growth_candidates:
        by_campaign.setdefault(c["campaign_id"], []).append(c)

    signals = []
    for e in exhausted:
        candidates = by_campaign.get(e["campaign_id"])
        if not candidates:
            continue
        signals.append({
            **e,
            "growth_candidate_count": len(candidates),
            "total_gap": sum(c["gap"] for c in candidates),
        })
    signals.sort(key=lambda s: s["total_gap"], reverse=True)
    return signals
