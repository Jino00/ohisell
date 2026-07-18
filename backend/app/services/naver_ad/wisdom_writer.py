# wisdom_writer.py — writer_sa (D-NAO-54 P3 승격층, docs/PLAN_naver-ad-diary-wisdom.md §P3)
# 역할: promoted 후보 중 아직 지혜(ops_wisdom_entries) 미생성분을 1:1로 지혜 엔트리로 만들고,
#   Jino 보고를 정보성 NaverProposal(wisdom_promoted)로 1건 낸다. ★금지선(D-NAO-54): 지혜는
#   실행 경로에 직접 쓰지 않는다 — proposal_type은 informational 목록(INFORMATIONAL_PROPOSAL_TYPES)
#   에만 등록돼 있고 실행 개방 액션(_WRITE_EXECUTORS/OPEN_ACTIONS)에는 절대 매핑하지 않는다.
#   멱등: source_candidate_id(unique)로 기존 엔트리가 있으면 skip. 쓰기 = wisdom_entries +
#   naver_proposals(정보성)만.
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import NaverProposal, OpsWisdomCandidate, OpsWisdomEntry
from app.services.naver_ad.proposal_writer import _WISDOM_PROMOTED
from app.utils.kst import kst_now

log = logging.getLogger(__name__)


def _report_rationale(principle: str, rationale: str) -> str:
    """콘솔 카드에 보일 보고문 — 판단원칙 + 판사 근거."""
    return f"[지혜 승격] {principle}\n근거: {rationale}"


def write_wisdom(db: Session, *, now: datetime | None = None) -> dict:
    """promoted 후보 → 지혜 엔트리 + 정보성 제안(멱등). 매일 wisdom_loop이 호출.

    행별 try/except + 유닛 증분 커밋. 이미 엔트리가 있는 후보는 건너뛴다(source_candidate_id
    unique가 DB 차원 최종 방어).
    """
    now = now or kst_now()
    promoted = (
        db.query(OpsWisdomCandidate)
        .filter(OpsWisdomCandidate.status == "promoted")
        .order_by(OpsWisdomCandidate.id)
        .all()
    )
    existing = {
        row[0]
        for row in db.query(OpsWisdomEntry.source_candidate_id).all()
    }
    totals = {"promoted_candidates": len(promoted), "entries_created": 0, "skipped_existing": 0, "errors": 0}
    for cand in promoted:
        if cand.id in existing:
            totals["skipped_existing"] += 1
            continue
        try:
            verdict = json.loads(cand.judge_verdict_json) if cand.judge_verdict_json else {}
            principle = (verdict.get("principle") or "").strip() or (cand.observation or "")
            rationale = (verdict.get("rationale") or "").strip()

            entry = OpsWisdomEntry(
                wisdom_text=principle, source_candidate_id=cand.id,
                judge_rationale=rationale, status="active", promoted_at=now,
            )
            db.add(entry)
            # Jino 보고 = 정보성 제안 1건(실행 대상 아님). target은 계정 레벨.
            db.add(NaverProposal(
                proposal_type=_WISDOM_PROMOTED, target_type="account", target_id="",
                campaign_id=cand.campaign_id or "",
                rationale=_report_rationale(principle, rationale),
                expected_effect="정보성 — 실행 대상 아님(콘솔 열람 전용).", status="pending",
            ))
            db.commit()
            totals["entries_created"] += 1
        except Exception as e:  # noqa: BLE001 — 한 건 실패가 나머지를 못 죽인다
            db.rollback()
            totals["errors"] += 1
            log.exception("wisdom_writer: 지혜 엔트리 생성 실패(cand_id=%s): %s", cand.id, e)
    return totals
