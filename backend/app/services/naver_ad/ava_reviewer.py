# ava_reviewer.py — E1a SA2(단일 책임): 브리핑 → 전문가(Ava) 평결. LLM 호출은 invoke
# 주입경계(기본=expert_llm._invoke_claude 실 claude, 테스트=가짜) — PLAN §8.
# 강한 스키마 검증(codex 아웃사이드 보이스 반영): 누락/중복/여분 proposal_id·무효 verdict·
# commentary↔proposal 혼동을 전부 거부하고, 위반 시 1회 재시도 후 degraded로 기록한다.
# 조작·환각 보정은 절대 하지 않는다 — 검증 실패 응답을 "고쳐서" 통과시키지 않는다.
from __future__ import annotations

import json
import logging

from app.models import NAVER_EXPERT_VERDICTS
from app.services.naver_ad.expert_llm import _invoke_claude

log = logging.getLogger(__name__)

# E1a 로컬 Ava 페르소나 스텁(charter 충실). AI_office factory.py:245-251에 정의된 실제 역할
# 문구를 그대로 반영 — 실 Ava SOUL pull은 E1b(별도 세션)에서.
_AVA_PERSONA_STUB = (
    "당신은 오하이테크의 광고 담당 직원 Ava입니다. 쿠팡·네이버 매체 운영, 메타 광고 기획·집행, "
    "ROAS·CPC·CTR·CVR 분석, 주간 성과 리뷰를 담당합니다. 지금은 네이버 SA 광고 최적화 시스템이 "
    "오늘 만든 제안들을 검토하는 자리입니다. 아래 브리핑(오늘 pending 제안·진단보드 요약·예측 "
    "롤업·최근 트리거·성적표) 밖의 수치나 사실을 인용하거나 지어내지 마세요 — 브리핑에 없는 "
    "정보는 모른다고 답하세요."
)

_PER_PROPOSAL_VERDICTS = tuple(v for v in NAVER_EXPERT_VERDICTS if v != "commentary")

# 배치 1콜(하루 전체 제안) 기준 시작값 — 정확한 값은 E1b 실 어댑터 스모크에서 재확인
# (D-NAO-30 미확정 항목, 추정 금지 원칙에 따라 여기선 보수적 초깃값만 둔다).
_REVIEW_TIMEOUT_S = 120
_REVIEW_MODEL = "opus"
_MAX_ATTEMPTS = 2  # 1회 시도 + 스키마 위반 시 1회 재시도(PLAN §8)

_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["verdicts", "commentary"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["proposal_id", "verdict", "reasoning"],
                "properties": {
                    "proposal_id": {"type": "integer"},
                    "verdict": {"type": "string", "enum": list(_PER_PROPOSAL_VERDICTS)},
                    "confidence": {"type": ["number", "null"]},
                    "reasoning": {"type": "string"},
                    "checkable_prediction": {"type": ["string", "null"]},
                    "pred_target_type": {"type": ["string", "null"]},
                    "pred_target_id": {"type": ["string", "null"]},
                    "pred_metric": {"type": ["string", "null"]},
                    "pred_direction": {"type": ["string", "null"]},
                },
            },
        },
        "commentary": {"type": "string"},
    },
}


def review(briefing: dict, *, invoke=_invoke_claude, model: str = _REVIEW_MODEL) -> dict:
    """브리핑을 검토해 평결을 생성. 반환: {verdicts, commentary, raw, usage, status, error}."""
    expected_ids = [p["id"] for p in briefing.get("pending_proposals", [])]
    prompt = _build_prompt(briefing, expected_ids)

    result: dict = {}
    error: str | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        result = invoke(prompt, system=_AVA_PERSONA_STUB, schema=_RESPONSE_SCHEMA, model=model, timeout=_REVIEW_TIMEOUT_S)
        parsed = result.get("json")
        error = _validate(parsed, expected_ids)
        if error is None:
            return {
                "verdicts": parsed["verdicts"],
                "commentary": parsed["commentary"],
                "raw": result.get("raw", ""),
                "usage": result.get("usage", {}),
                "status": "ok",
                "error": None,
            }
        log.warning("ava_reviewer: 스키마 위반(%d/%d회 시도) — %s", attempt, _MAX_ATTEMPTS, error)

    # 재시도까지 실패 — 조작·환각 보정 금지, 빈 평결로 degraded 기록만.
    return {
        "verdicts": [],
        "commentary": "",
        "raw": result.get("raw", ""),
        "usage": result.get("usage", {}),
        "status": "degraded",
        "error": error,
    }


def _build_prompt(briefing: dict, expected_ids: list[int]) -> str:
    return (
        "아래는 오늘의 네이버 SA 광고 최적화 브리핑입니다(JSON). 이 안의 정보만 근거로 사용하세요.\n\n"
        f"{json.dumps(briefing, ensure_ascii=False, default=str)}\n\n"
        f"검토해야 할 제안 id 목록: {expected_ids} — 정확히 이 id들 전부에 대해 각각 하나씩 "
        "verdict를 남기세요. 빠뜨리거나 여기 없는 id를 만들어내지 마세요.\n"
        "각 제안에 대해 agree(동의)/partial(부분동의)/reject(기각)/insufficient_evidence(판단불가, "
        "데이터부족) 중 하나를 verdict로 고르고 reasoning을 남기세요. reject해도 검증 가능한 예측"
        "(checkable_prediction)을 선택적으로 남길 수 있습니다(억지로 만들지 마세요). 마지막으로 "
        "오늘 전체에 대한 총평(commentary)을 한 문단으로 남기세요."
    )


def _validate(parsed, expected_ids: list[int]) -> str | None:
    """강한 스키마 검증. 통과하면 None, 위반이면 사유 문자열(조작 없이 그대로 degraded 사유로 씀)."""
    if not isinstance(parsed, dict):
        return "응답이 JSON 객체가 아님(추출 실패 포함)"

    verdicts = parsed.get("verdicts")
    commentary = parsed.get("commentary")
    if not isinstance(verdicts, list):
        return "verdicts가 배열이 아님"
    if not isinstance(commentary, str):
        return "commentary가 문자열이 아님(commentary↔proposal 혼동 방지)"

    seen_ids: list = []
    for v in verdicts:
        if not isinstance(v, dict):
            return "verdicts 항목이 객체가 아님"
        pid = v.get("proposal_id")
        if not isinstance(pid, int) or isinstance(pid, bool):  # bool은 int 서브클래스라 별도 배제
            return f"proposal_id가 정수가 아님: {pid!r}"
        if pid not in expected_ids:
            return f"알 수 없는 proposal_id: {pid!r}"
        seen_ids.append(pid)
        verdict_value = v.get("verdict")
        if verdict_value not in _PER_PROPOSAL_VERDICTS:
            return f"무효한 verdict: {verdict_value!r}"
        if not isinstance(v.get("reasoning"), str) or not v.get("reasoning"):
            return f"reasoning 누락 또는 빈 문자열(proposal_id={pid})"

    if len(seen_ids) != len(set(seen_ids)):
        return "중복 proposal_id"
    if set(seen_ids) != set(expected_ids):
        missing = set(expected_ids) - set(seen_ids)
        return f"proposal_id 누락: {missing}"

    return None
