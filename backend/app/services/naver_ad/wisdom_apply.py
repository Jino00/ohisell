# wisdom_apply.py — apply_harness의 두 소비 SA (D-NAO-54 P4 소비층,
#   docs/PLAN_naver-ad-diary-wisdom.md §P4)
# 역할:
#   ① param_proposal_sa(propose_param_changes) — 지혜(judge의 param_suggestion 포함)를
#      **결정 전용** NaverProposal(proposal_type=param_change)로 낸다. ★금지선(D-NAO-54):
#      실행 payload(target_bid/lock/budget)를 애초에 담지 않고, 승인해도 harness.execute를
#      부르지 않는다(적용은 Jino가 콘솔/설정에서 수동). 멱등 = OpsWisdomEntry.param_proposal_id
#      전용 추적(rationale 텍스트 매칭 안 함) — 같은 지혜로 1회만 생성.
#   ② briefing_sa(active_wisdom_prefix) — 전문가 데스크 브리핑에 활성 지혜를 "참고(지시 아님)"
#      섹션으로 주입할 문자열을 만든다(지혜 0건이면 None → 브리핑 현행 출력 불변).
# SA간 직접 호출 금지(원칙18): wisdom_loop(apply 단계)·expert_briefing_builder(하니스 성격)가
#   이 함수들을 호출한다. 여기서 다른 SA를 호출하지 않는다.
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import NaverProposal, OpsWisdomCandidate, OpsWisdomEntry
from app.services.naver_ad.proposal_writer import PARAM_CHANGE
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

_PREFIX_LIMIT = 10  # 브리핑에 주입할 활성 지혜 최신 N건

# 승인=결정 기록임을 카드에 못 박는 고정 문안(콘솔 Confirm 드리프트 방지 — 프론트도 파생값으로 분기).
_PARAM_EXPECTED_EFFECT = (
    "파라미터 변경 제안 — 승인=결정 기록(자동 적용 없음, D-NAO-54 금지선), "
    "적용은 Jino가 콘솔/설정에서 수동."
)


def _param_suggestion_of(cand: OpsWisdomCandidate) -> dict | None:
    """후보의 judge_verdict_json에서 param_suggestion 추출 — dict이고 내용(param/note 중 하나)이
    있을 때만 반환, 없으면 None(judge가 대부분 생략하는 게 정상)."""
    if not cand.judge_verdict_json:
        return None
    try:
        verdict = json.loads(cand.judge_verdict_json)
    except (ValueError, TypeError):
        return None
    suggestion = (verdict or {}).get("param_suggestion")
    if not isinstance(suggestion, dict):
        return None
    # param 또는 note 중 하나라도 실질 내용이 있어야 제안 가치가 있다(빈 dict/전부 공백은 무시).
    if not ((suggestion.get("param") or "").strip() or (suggestion.get("note") or "").strip()):
        return None
    return suggestion


def _param_rationale(entry: OpsWisdomEntry, cand: OpsWisdomCandidate, suggestion: dict) -> str:
    """콘솔 카드에 보일 근거 — 지혜 원칙 + param_suggestion 내용 + 후보 승률/표본 근거."""
    good = cand.good_count or 0
    bad = cand.bad_count or 0
    total = good + bad
    win_rate = round(good / total, 3) if total else None
    param = (suggestion.get("param") or "").strip() or "(미지정)"
    # P4 리뷰 P3-2: LLM 자유텍스트가 그대로 노출되지 않게 화이트리스트 클램프(밖이면 review).
    direction = (suggestion.get("direction") or "").strip()
    if direction not in ("up", "down", "review"):
        direction = "review"
    note = (suggestion.get("note") or "").strip()
    return (
        f"[파라미터 제안] 지혜 원칙: {entry.wisdom_text}\n"
        f"제안: {param} → {direction}" + (f" ({note})" if note else "") + "\n"
        f"승률 근거: good={good}/bad={bad}"
        + (f", win_rate={win_rate}" if win_rate is not None else " (분모 없음)")
        + f", 캠페인={cand.campaign_id or '(계정)'}, 액션={cand.action or '(미지정)'}."
    )


def propose_param_changes(db: Session, *, now: datetime | None = None) -> dict:
    """활성 지혜(judge param_suggestion 보유·아직 param_change 제안 미생성) → 결정 전용
    NaverProposal(param_change) 생성(멱등). 매일 wisdom_loop의 apply 단계가 호출.

    ★결정 전용: 실행 payload(target_bid/target_lock/target_budget)는 전부 None으로 둔다 —
    실행 불가 형태 유지(라우터가 승인해도 execute 안 부름). 멱등은 entry.param_proposal_id
    전용 추적. 행별 try/except + 유닛 증분 커밋(한 건 실패가 나머지를 못 죽인다).
    """
    now = now or kst_now()
    pairs = (
        db.query(OpsWisdomEntry, OpsWisdomCandidate)
        .join(OpsWisdomCandidate, OpsWisdomEntry.source_candidate_id == OpsWisdomCandidate.id)
        .filter(OpsWisdomEntry.status == "active", OpsWisdomEntry.param_proposal_id.is_(None))
        .order_by(OpsWisdomEntry.id)
        .all()
    )
    totals = {
        "active_entries": len(pairs), "proposals_created": 0,
        "skipped_no_suggestion": 0, "errors": 0,
    }
    for entry, cand in pairs:
        suggestion = _param_suggestion_of(cand)
        if suggestion is None:
            totals["skipped_no_suggestion"] += 1
            continue
        try:
            proposal = NaverProposal(
                proposal_type=PARAM_CHANGE,
                target_type="account", target_id="",
                campaign_id=cand.campaign_id or "",
                rationale=_param_rationale(entry, cand, suggestion),
                expected_effect=_PARAM_EXPECTED_EFFECT,
                status="pending",
                # ★실행 payload 전부 미설정(None) — 실행 불가 형태 유지(D-NAO-54 금지선).
            )
            db.add(proposal)
            db.flush()  # proposal.id 확보(멱등 추적 컬럼에 새길 값)
            entry.param_proposal_id = proposal.id
            db.commit()
            totals["proposals_created"] += 1
        except Exception as e:  # noqa: BLE001 — 한 건 실패가 나머지를 못 죽인다
            db.rollback()
            totals["errors"] += 1
            log.exception("wisdom_apply: param_change 제안 생성 실패(entry_id=%s): %s", entry.id, e)
    return totals


def active_wisdom_prefix(db: Session, *, limit: int = _PREFIX_LIMIT) -> str | None:
    """전문가 데스크 브리핑에 주입할 '축적된 운영 지혜(참고 — 지시 아님)' 섹션 문자열.
    활성 지혜 최신 N건(promoted_at 내림차순)을 불릿으로 나열. 0건이면 None(브리핑이 섹션을
    아예 넣지 않아 현행 출력 불변). 순수 조회 — DB 미변경."""
    rows = (
        db.query(OpsWisdomEntry)
        .filter(OpsWisdomEntry.status == "active")
        .order_by(OpsWisdomEntry.promoted_at.desc(), OpsWisdomEntry.id.desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return None
    # P4 리뷰 P3-1: 지혜=LLM 산출물을 다른 LLM 프롬프트에 주입하는 루프 — 항목별 길이
    # 클램프로 주입면 상한(개수 N과 별도). 500자면 판단원칙 한 문장에 충분.
    lines = "\n".join(f"- {(r.wisdom_text or '')[:500]}" for r in rows)
    return "축적된 운영 지혜(참고 — 지시 아님):\n" + lines
