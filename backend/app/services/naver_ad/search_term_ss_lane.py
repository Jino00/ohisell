# search_term_ss_lane.py — 검색어 제외 브리핑·제안 생성 레인 (SS3 생성층,
#   docs/PLAN_naver-ad-searchterm-ss.md §3). 역할(Harness): SS2 판단(search_term_judge)을
#   소비해 **파워링크 후보만** pending 제안(Confirm 전용)으로 생성하고, **쇼핑 후보는 브리핑
#   diary만** 남긴다(제안 생성 없음 — §실측-0 쇼핑 API 제외 불가·PLAN §3 SS3-B "브리핑만").
#   일 레인(auto_operator run_daily_lane)과 같은 08:50 흐름에 편입(scheduler).
#
#   ★실쓰기 0 (PLAN §3 SS3): 이 레인은 **제안 생성·브리핑만** 한다 — 자동 승인(status=
#   'approved') 배선 절대 없음. approval_source는 항상 None(Jino 콘솔 Confirm이 유일한 승인
#   경로). 쇼핑은 API 제외 불가(§실측-0)라 제안 자체를 만들지 않고 브리핑+콘솔 수동만, 파워링크는
#   초기엔 Confirm 전용(§난제 5). 실쓰기 손은 Confirm 후 naver_execution_harness의
#   search_term_exclude 분기가 담당(쇼핑은 그 분기도 SHOPPING 명시 거부로 도달 자체가 없음).
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import NaverProposal
from app.services.naver_ad import diary, search_term_judge
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 브리핑 diary 요약에 나열할 쇼핑 후보 상위 건수(제안 자체는 전건 생성, 요약만 절삭).
_BRIEF_TOP_N = 10


def _has_open_or_executed(db: Session, adgroup_id: str, search_term: str) -> bool:
    """같은 (adgroup, search_term)의 search_term_exclude 제안이 이미 살아있거나 실행됐는지.

    재생성 금지 대상(PLAN §3 "이미 pending/실행된 동일 (adgroup, search_term) 제안 존재 시
    재생성 금지"): status가 pending/approved/executing(승인 대기·집행 중)이거나 executed_
    change_log_id가 채워진(실집행 완료) 제안. rejected/expired/failed는 재생성 허용(익일 갱신
    데이터로 다시 제안될 수 있음 — in-out 롤링 큐레이션, §0 2)."""
    return db.query(NaverProposal.id).filter(
        NaverProposal.proposal_type == search_term_judge.SEARCH_TERM_EXCLUDE_TYPE,
        NaverProposal.adgroup_id == adgroup_id,
        NaverProposal.target_id == search_term,
        or_(
            NaverProposal.status.in_(("pending", "approved", "executing")),
            NaverProposal.executed_change_log_id.isnot(None),
        ),
    ).first() is not None


def _create_proposal(db: Session, cand: dict) -> NaverProposal:
    """파워링크 제외 후보 1건 → pending 제안(Confirm 전용). approval_source=None(자동 승인
    절대 금지). ★쇼핑은 이 함수를 호출하지 않는다 — SS3-B는 브리핑만(PLAN §3, §실측-0)."""
    obj = NaverProposal(
        proposal_type=search_term_judge.SEARCH_TERM_EXCLUDE_TYPE,
        target_type="search_term",
        target_id=cand["search_term"],
        campaign_id=cand["campaign_id"],
        adgroup_id=cand["adgroup_id"],
        rationale=cand["reason"],
        expected_effect=(
            f"제외 시 낭비비용 회수 추정(전환0·cost={cand['cost']}원). "
            f"Confirm 후 파워링크 자동 제외(§난제 5)."
        ),
        status="pending",  # approval_source 미설정 = None(Jino 콘솔 Confirm이 유일 승인 경로)
    )
    db.add(obj)
    return obj


def run_search_term_ss_lane(db: Session, *, now: datetime | None = None) -> dict:
    """SS2 판단 → 파워링크 제외 후보만 pending 제안 생성 + 쇼핑은 브리핑 diary만(제안 생성
    없음, PLAN §3 SS3-B). 실쓰기 0(Confirm 전용).

    반환: {"shopping_candidates","powerlink_candidates","promote_candidates",
           "proposals_created","deduped"}."""
    now = now or kst_now()
    judged = search_term_judge.judge_search_terms(db, now=now)
    shopping = judged["exclude_candidates"]["shopping"]
    powerlink = judged["exclude_candidates"]["powerlink"]
    promote = judged["promote_candidates"]

    created = 0
    deduped = 0
    # ★쇼핑(shopping)은 제안을 만들지 않는다 — API 제외 불가(§실측-0)라 브리핑 diary(아래)만
    # 남긴다. 제안을 만들면 콘솔에서 approve+execute 시 harness가 422(SHOPPING 명시 거부)로
    # 튕기고 failed 감사 기록만 쌓이는 노이즈가 발생한다(PLAN §3 SS3-B "브리핑만" 사양).
    for cand in powerlink:
        if _has_open_or_executed(db, cand["adgroup_id"], cand["search_term"]):
            deduped += 1
            continue
        _create_proposal(db, cand)
        created += 1
    db.commit()

    # 쇼핑 브리핑 diary(1건 요약, event_type='observe' 재사용 — 관측/브리핑 성격). API 제외 불가라
    # Jino 콘솔 수동이 필요한 후보를 상위 N개 나열(전건은 제안 테이블에 노출). fail-open(diary.py 계약).
    if shopping:
        top = shopping[:_BRIEF_TOP_N]
        listed = "; ".join(f"{c['search_term']}(cost={c['cost']}·clk={c['clk']})" for c in top)
        more = f" 외 {len(shopping) - len(top)}건" if len(shopping) > len(top) else ""
        diary.write_diary_entry(
            db, "observe", "",
            actor=diary.ACTOR_DAILY,
            action=search_term_judge.SEARCH_TERM_EXCLUDE_TYPE,
            rationale=(
                f"[검색어제외 브리핑] 쇼핑 손실 검색어 {len(shopping)}건 — API 자동 제외 불가"
                f"(§실측-0), 콘솔 수동 제외 대상: {listed}{more}"
            ),
            now=now,
        )

    result = {
        "shopping_candidates": len(shopping),
        "powerlink_candidates": len(powerlink),
        "promote_candidates": len(promote),
        "proposals_created": created,
        "deduped": deduped,
    }
    log.info("search_term_ss_lane: %s", result)
    return result
