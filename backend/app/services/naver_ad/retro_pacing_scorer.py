# retro_pacing_scorer.py — retro_pacing_scorer SA (D-NAO-45, PLAN_naver-ad-retro-scoring §4-C3)
# 역할: trigger_pacing 정보성 제안(저속/과속 경보)을 그날 캠페인 sentinel 최종 소진과
#   대조해 채점한다. 진단 보드와 달리 페이싱은 "그날 하루"가 곧 사후창이라(다음날이면
#   최종치가 확정) D+3/D+7 지평이 필요 없다 — sentinel 최종치가 나오는 대로 즉시 채점.
#   rationale 텍스트를 정규식으로 파싱(ref 31 스크립트 패턴 고정) — trigger_watch가 구조화
#   필드를 직접 쓰도록 바뀌면(후속, PLAN §2 OUT) 이 파싱은 대체 대상.
#   읽기 + 본인 테이블 쓰기만(원칙18-1) — 외부 API 호출 없음.
from __future__ import annotations

import re
from datetime import date

from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverProposal, NaverRetroPacingScore
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.trigger_watch import PROPOSAL_TYPE_PACING
from app.utils.kst import kst_now

# ref 31 §2 정규식 패턴 고정 — trigger_watch._pacing_proposal의 실 rationale 형식과 일치
# (예: "[trigger_watch] 페이싱 이탈 저속(예산 소진 지연) — 2026-07-16 12시 기준 소진
#   5000원/50000원(실제페이스=0.1, 기대페이스=0.2(...), 배수=0.5).").
_PATTERN = re.compile(
    r"(저속|과속).*?(\d{4}-\d{2}-\d{2}) (\d+)시 기준 소진 (\d+)원/(\d+)원.*?배수=([0-9.]+)"
)


def score_alerts(db: Session, upto: date) -> dict:
    """미채점 trigger_pacing 제안(naver_retro_pacing_score에 proposal_id 없음) 중
    경보일 <= upto를 파싱→sentinel 최종치 대조→채점.

    unparsed(정규식 미매치)는 재시도 무한루프 방지를 위해 verdict='unparsed'로 즉시 기록.
    진짜 채점 불가(sentinel 최종치 아직 없음, 당일 미확정)는 행을 만들지 않고 skip
    (다음 날 재시도 — 최종치가 늦게 올 수 있음). 경보일이 upto보다 미래인 것도 마찬가지로
    skip(파싱은 필요해 미리 하되, upto 밖이면 아직 채점하지 않음).
    """
    scored_ids = {row[0] for row in db.query(NaverRetroPacingScore.proposal_id).all()}
    q = db.query(NaverProposal).filter(NaverProposal.proposal_type == PROPOSAL_TYPE_PACING)
    if scored_ids:
        q = q.filter(~NaverProposal.id.in_(scored_ids))
    candidates = q.all()

    scored = 0
    unparsed = 0
    skipped = 0
    for p in candidates:
        m = _PATTERN.search(p.rationale or "")
        if not m:
            db.add(NaverRetroPacingScore(
                proposal_id=p.id, alert_date=None, campaign_id=p.campaign_id,
                kind=None, alert_hour=None, spent=None, budget=None, multiple=None,
                final_cost=None, final_ratio=None, verdict="unparsed", scored_at=kst_now(),
            ))
            unparsed += 1
            continue

        kind_label, dstr, hour, spent, budget, mult = m.groups()
        alert_date = date.fromisoformat(dstr)
        if alert_date > upto:
            skipped += 1
            continue

        final_cost = _sentinel_final(db, p.campaign_id, alert_date)
        if final_cost is None:
            skipped += 1
            continue

        budget_i = int(budget)
        final_ratio = final_cost / budget_i if budget_i else 0.0
        verdict = _bucket(kind_label, final_ratio)
        db.add(NaverRetroPacingScore(
            proposal_id=p.id, alert_date=alert_date, campaign_id=p.campaign_id,
            kind=kind_label, alert_hour=int(hour), spent=int(spent), budget=budget_i,
            multiple=float(mult), final_cost=final_cost, final_ratio=final_ratio,
            verdict=verdict, scored_at=kst_now(),
        ))
        scored += 1

    db.commit()
    return {"scored": scored, "unparsed": unparsed, "skipped": skipped}


def _sentinel_final(db: Session, campaign_id: str, ad_date: date) -> int | None:
    row = db.query(NaverAdDaily.cost).filter(
        NaverAdDaily.campaign_id == campaign_id, NaverAdDaily.ad_date == ad_date,
        NaverAdDaily.adgroup_id == BACKFILL_SENTINEL_ADGROUP,
    ).first()
    return int(row[0]) if row is not None else None


def _bucket(kind: str, final_ratio: float) -> str:
    """ref 31 §2 버킷 고정: 저속 = 최종/예산 ≥0.9 false_alarm / 0.5~0.9 partial / <0.5 correct.
    과속 = ≥1.1 correct / 0.9~1.1 partial / <0.9 false_alarm."""
    if kind == "저속":
        if final_ratio >= 0.9:
            return "false_alarm"
        if final_ratio >= 0.5:
            return "partial"
        return "correct"
    # 과속
    if final_ratio >= 1.1:
        return "correct"
    if final_ratio >= 0.9:
        return "partial"
    return "false_alarm"
