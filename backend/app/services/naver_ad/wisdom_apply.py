# wisdom_apply.py — apply_harness의 두 소비 SA (D-NAO-54 P4 소비층,
#   docs/PLAN_naver-ad-diary-wisdom.md §P4)
# 역할:
#   ① param_proposal_sa(propose_param_changes) — 지혜(judge의 param_suggestion 포함)를
#      **결정 전용** NaverProposal(proposal_type=param_change)로 낸다. ★D-NAO-248 §4-B(B1)
#      이후: 승인해도 harness.execute는 여전히 부르지 않는다(param_change는 실행 매핑이 없다 —
#      _ACTION_BY_PROPOSAL_TYPE에 없음) — 대신 콘솔 승인 핸들러(`POST /proposals/{id}/status`)가
#      `guardrail_params.apply_params()`를 직접 불러 반영한다(D-NAO-54의 "지혜→실행 직접 쓰기
#      금지"는 광고 API 실쓰기 얘기였고, 봉투 파라미터 KV 반영은 그 금지선 밖이다 — D-NAO-249
#      확정). 이 함수는 여전히 실행 payload(target_bid/lock/budget)를 담지 않는다(그건 지금도
#      금지). 멱등 = OpsWisdomEntry.param_proposal_id 전용 추적(rationale 텍스트 매칭 안 함)
#      — 같은 지혜로 1회만 생성.
#      ★B7 코드 클램프(fail-closed) — 판사의 param_suggestion이 ①scope=='unconditional'
#      ②param이 guardrail_params.SPECS 화이트리스트 안 **둘 다**일 때만 제안을 낸다. 조건부
#      지혜(scope='conditional' 또는 부재)나 미매핑 param은 제안을 만들지 않는다 — 전역 상수
#      3종에 조건부 지혜를 반영하면 「주말 지혜가 평일까지 막는」 사고가 난다. 판사가 뭐라
#      하든 이 코드가 최종 판정이고, 판정은 «격상» 방향으로는 절대 흔들리지 않는다.
#   ② briefing_sa(active_wisdom_prefix) — 전문가 데스크 브리핑에 활성 지혜를 "참고(지시 아님)"
#      섹션으로 주입할 문자열을 만든다(지혜 0건이면 None → 브리핑 현행 출력 불변). 조건부
#      지혜는 여기로 계속 흘러간다(B7이 막는 것은 param_change 제안 생성뿐, 브리핑 주입은
#      건드리지 않는다 — 배선 신설 0).
# SA간 직접 호출 금지(원칙18): wisdom_loop(apply 단계)·expert_briefing_builder(하니스 성격)가
#   이 함수들을 호출한다. 여기서 다른 SA를 호출하지 않는다(guardrail_params는 SA가 아니라
#   §3층 봉투 파라미터 서비스 — read-only 화이트리스트 참조만 하고 apply_params는 여기서
#   호출하지 않는다, 그건 승인 핸들러 몫).
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import NaverProposal, OpsWisdomCandidate, OpsWisdomEntry
from app.services.naver_ad import guardrail_params, wisdom_judge
from app.services.naver_ad.proposal_writer import PARAM_CHANGE
from app.utils.kst import kst_now

# ★D-NAO-248 §3 — wisdom_judge를 top-level import한다. 순환 우려를 미리 확인했다: wisdom_judge
#   (및 그 의존 guardrail_params·expert_llm)는 wisdom_apply를 어디서도 import하지 않는다
#   (wisdom_apply를 import하는 쪽은 wisdom_loop·expert_briefing_builder·wisdom_scorecard —
#   wisdom_judge와는 무관한 소비자다). 순환이 없으므로 지연 import 불필요.

log = logging.getLogger(__name__)

_PREFIX_LIMIT = 10  # 브리핑에 주입할 활성 지혜 최신 N건

# 승인=결정 기록임을 카드에 못 박는 고정 문안(콘솔 Confirm 드리프트 방지 — 프론트도 파생값으로 분기).
# ★D-NAO-248 §4-B(B1) 이후: 「자동 적용 없음」은 여전히 참이다 — 승인 자체가 «사람의 행위»이고
#   반영값도 사람이 정하므로 트리거·값 둘 다 사람이 확정한다(D-NAO-249). 다만 예전 문구
#   ("적용은 Jino가 콘솔/설정에서 수동")는 「승인해도 아무 일도 안 난다"로 오독될 수 있어
#   갱신한다 — 지금은 승인이 곧 반영이다(값을 함께 승인할 때).
_PARAM_EXPECTED_EFFECT = (
    "파라미터 변경 제안 — 승인 시 사람이 입력한 값이 즉시 반영된다(D-NAO-248 §4-B). "
    "자동 적용 없음 — 트리거는 콘솔에서의 사람 승인 행위이고, 반영될 값의 크기도 사람이 "
    "확정한다. 허용 범위(봉투) 밖 값은 거부된다."
)

# ── B7 코드 클램프 판정 라벨 ──────────────────────────────────────────────
GATE_UNCONDITIONAL_MAPPED = "unconditional_mapped"  # scope=unconditional ∧ param∈SPECS → 제안 생성
GATE_CONDITIONAL = "conditional_fallback"            # scope!=unconditional(부재 포함) → 격상 안 함
GATE_UNMAPPED = "unmapped_param"                     # param이 SPECS 밖(또는 없음) → 격상 안 함
GATE_NO_SUGGESTION = "no_suggestion"                 # param_suggestion 자체가 없음(대부분 정상)


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


def _classify_param_suggestion(suggestion: dict) -> str:
    """B7 코드 클램프(fail-closed) — 판사가 뭐라 채웠든 **이 코드가 최종 판정**한다.

    반환은 GATE_* 넷 중 하나(NO_SUGGESTION은 호출부가 suggestion=None일 때 따로 매긴다,
    여기는 이미 suggestion이 dict임을 전제).
    ①scope가 정확히 "unconditional"이 아니면(부재·오타·"conditional" 전부) → CONDITIONAL로
      떨어진다 — «격상»(제안 생성) 방향으로는 절대 흔들리지 않는다(fail-closed).
    ②param이 guardrail_params.SPECS 화이트리스트 밖(또는 없음)이면 → UNMAPPED.
    ③둘 다 통과해야만 UNCONDITIONAL_MAPPED(= propose_param_changes가 제안을 낸다).
    """
    scope = (suggestion.get("scope") or "").strip()
    if scope != "unconditional":
        return GATE_CONDITIONAL
    param = (suggestion.get("param") or "").strip()
    if param not in guardrail_params.SPECS:
        return GATE_UNMAPPED
    return GATE_UNCONDITIONAL_MAPPED


def gate_summary(db: Session) -> dict:
    """B7-6 카운터 표면화 — 판사의 param_suggestion이 코드 클램프에서 어떻게 갈렸는지.

    ★read-time 재현이다(wisdom_scorecard._candidate_status와 같은 관례) — 제안 생성 여부·
    오늘 실행 여부와 무관하게 **현재 저장된 지혜(OpsWisdomEntry) 전체**를 매번 다시 분류한다.
    카운트가 0이어도 키는 항상 낸다(교훈 #318: 카운터가 있어야 침묵을 본다) — 미매핑·조건부
    폴백이 «조용히 0건»인지 «세는 코드가 죽어서 0건」인지 이 함수가 없으면 구분이 안 된다.
    """
    pairs = (
        db.query(OpsWisdomEntry, OpsWisdomCandidate)
        .join(OpsWisdomCandidate, OpsWisdomEntry.source_candidate_id == OpsWisdomCandidate.id)
        .all()
    )
    counts = {
        GATE_UNCONDITIONAL_MAPPED: 0, GATE_CONDITIONAL: 0,
        GATE_UNMAPPED: 0, GATE_NO_SUGGESTION: 0,
    }
    for _entry, cand in pairs:
        suggestion = _param_suggestion_of(cand)
        if suggestion is None:
            counts[GATE_NO_SUGGESTION] += 1
            continue
        counts[_classify_param_suggestion(suggestion)] += 1
    return counts


def _sibling_control_summary(sibling_view: dict | None) -> str:
    """★D-NAO-248 §3 — 판사가 본 조건 대조군 재료를 «판정 없이» 사람에게 병기한다.

    판사는 sibling_buckets.condition_controls를 보고 scope를 판정하는데, 최종 승인자(사람)는
    그것 없이 승인한다 — 최종 판정자가 중간 판정자보다 적은 증거로 결정하는 역전을 막는다.
    문턱·합격 판정 어휘(예: "충분"·"합격"·"권장")는 절대 쓰지 않는다 — 판단은 사람 몫이다.
    값이 없으면(0건) 침묵하지 않고 「없음(0건)」으로 명시한다.
    """
    view = sibling_view or {}
    cc = view.get("condition_controls") or []
    ot = view.get("other_campaign_types") or []
    excl = view.get("excluded_from_controls") or {}

    if cc:
        items = []
        for r in cc[:3]:
            differs = ",".join(r.get("differs_in") or []) or "(없음)"
            wr = r.get("win_rate")
            wr_s = str(wr) if wr is not None else "N/A"
            items.append(f"differs_in={differs} n={r.get('n')} WR={wr_s}")
        listing = "; ".join(items)
        if len(cc) > 3:
            listing += f"; 외 {len(cc) - 3}건"
        cc_part = f"조건 대조군(판사가 본 재료 — 판정 아님): {len(cc)}건 [{listing}]"
    else:
        cc_part = "조건 대조군: 없음(0건)"

    exp = excl.get("experiment_batch", 0)
    legacy = excl.get("legacy_grain", 0)
    unknown = excl.get("unknown_boundary", 0)
    # ★D-NAO-248 §2(P2-1) — 후보 자신이 규칙 0에 걸려 대조군을 가질 수 없을 때, 대조군
    #   자격이 있었을 형제(어느 버킷에도 안 잡히고 그냥 버려지던 형제)를 여기서 센다. 값이
    #   0이어도 표기를 지우지 않는다(침묵 금지 — 「없음」과 「0건」은 다르다).
    not_eligible = excl.get("candidate_not_eligible", 0)
    excluded_total = exp + legacy + unknown + not_eligible

    return (
        f"{cc_part} / 비교 불가 유형 {len(ot)}건 / "
        f"대조군 제외 {excluded_total}건(실험배치 {exp}·레거시 {legacy}·경계미상 {unknown}·"
        f"후보 자체가 대조군 자격 없음 {not_eligible})"
    )


def _param_rationale(
    entry: OpsWisdomEntry, cand: OpsWisdomCandidate, suggestion: dict, sibling_view: dict | None
) -> str:
    """콘솔 카드에 보일 근거 — 지혜 원칙 + param_suggestion 내용 + 후보 승률/표본 근거 +
    조건 대조군 요약(D-NAO-248 §3, 판정 없이 재료만)."""
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
        + f", 캠페인={cand.campaign_id or '(계정)'}, 액션={cand.action or '(미지정)'}.\n"
        + _sibling_control_summary(sibling_view)
    )


def propose_param_changes(db: Session, *, now: datetime | None = None) -> dict:
    """활성 지혜(judge param_suggestion 보유·아직 param_change 제안 미생성) → 결정 전용
    NaverProposal(param_change) 생성(멱등). 매일 wisdom_loop의 apply 단계가 호출.

    ★결정 전용: 실행 payload(target_bid/target_lock/target_budget)는 전부 None으로 둔다 —
    실행 불가 형태 유지(param_change는 harness 실행 매핑에 없다). 멱등은 entry.param_proposal_id
    전용 추적. 행별 try/except + 유닛 증분 커밋(한 건 실패가 나머지를 못 죽인다).

    ★B7 코드 클램프(fail-closed) — scope=='unconditional' ∧ param∈guardrail_params.SPECS 인
    것만 제안을 낸다(`_classify_param_suggestion`). 조건부(scope 부재·"conditional") 또는
    미매핑(param이 화이트리스트 밖) 후보는 제안을 만들지 않는다 — 카운트만 하고 넘어간다
    (조용히 버려지는 게 아니다, gate_summary()가 read-time으로 다시 세어 표면화한다). 이
    지혜들은 여전히 active_wisdom_prefix()를 통해 브리핑에는 실린다(B7이 막는 것은 param_change
    제안 «생성»뿐).
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
        "skipped_no_suggestion": 0, "skipped_conditional": 0, "skipped_unmapped_param": 0,
        "errors": 0,
    }
    for entry, cand in pairs:
        suggestion = _param_suggestion_of(cand)
        if suggestion is None:
            totals["skipped_no_suggestion"] += 1
            continue
        gate = _classify_param_suggestion(suggestion)
        if gate == GATE_CONDITIONAL:
            totals["skipped_conditional"] += 1
            continue
        if gate == GATE_UNMAPPED:
            totals["skipped_unmapped_param"] += 1
            continue
        param_key = (suggestion.get("param") or "").strip()
        # ★D-NAO-248 §3 — 판사가 본 조건 대조군 재료를 카드 근거에도 병기한다(판정 없이).
        sibling_view = wisdom_judge._sibling_buckets(db, cand)
        try:
            proposal = NaverProposal(
                proposal_type=PARAM_CHANGE,
                # ★B7-5: SPECS 키를 rationale 자유텍스트가 아니라 target_type/target_id에
                #   구조적으로 싣는다 — 승인 핸들러가 텍스트 파싱을 하지 않게 한다.
                target_type=guardrail_params.TARGET_TYPE, target_id=param_key,
                campaign_id=cand.campaign_id or "",
                rationale=_param_rationale(entry, cand, suggestion, sibling_view),
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
