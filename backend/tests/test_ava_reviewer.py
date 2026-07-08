# test_ava_reviewer.py — E1a T3 ava_reviewer(SA2) 단위테스트. invoke는 전부 가짜(주입경계),
# 실 claude 무의존. 강한 스키마 검증: 위반 시 1회 재시도 → 그래도 위반이면 degraded(조작 금지).
from __future__ import annotations

from app.services.naver_ad import ava_reviewer

BRIEFING_2_PROPOSALS = {"pending_proposals": [{"id": 10}, {"id": 20}]}
BRIEFING_EMPTY = {"pending_proposals": []}


def _valid_response(ids, commentary="총평"):
    return {
        "verdicts": [
            {"proposal_id": pid, "verdict": "agree", "reasoning": "근거"} for pid in ids
        ],
        "commentary": commentary,
    }


def _invoke_returning(*json_payloads):
    """호출될 때마다 순서대로 json_payloads를 반환하는 가짜 invoke. 소진되면 마지막 값 반복."""
    calls = {"n": 0}

    def fake(prompt, *, system, schema, model, timeout):
        i = min(calls["n"], len(json_payloads) - 1)
        calls["n"] += 1
        payload = json_payloads[i]
        return {"json": payload, "raw": "raw-text", "usage": {"input_tokens": 1}}

    fake.calls = calls
    return fake


def test_review_returns_ok_with_valid_response():
    invoke = _invoke_returning(_valid_response([10, 20]))

    result = ava_reviewer.review(BRIEFING_2_PROPOSALS, invoke=invoke)

    assert result["status"] == "ok"
    assert result["error"] is None
    assert {v["proposal_id"] for v in result["verdicts"]} == {10, 20}
    assert result["commentary"] == "총평"
    assert invoke.calls["n"] == 1


def test_review_retries_once_on_missing_proposal_id_then_succeeds():
    bad = _valid_response([10])  # 20 누락
    good = _valid_response([10, 20])
    invoke = _invoke_returning(bad, good)

    result = ava_reviewer.review(BRIEFING_2_PROPOSALS, invoke=invoke)

    assert result["status"] == "ok"
    assert invoke.calls["n"] == 2


def test_review_degrades_after_second_failure_without_fabricating():
    bad = _valid_response([10])  # 20 누락, 두 번 다 이 응답
    invoke = _invoke_returning(bad)

    result = ava_reviewer.review(BRIEFING_2_PROPOSALS, invoke=invoke)

    assert result["status"] == "degraded"
    assert result["verdicts"] == [], "조작·환각 보정 금지 — 실패 시 빈 평결이어야 함"
    assert result["commentary"] == ""
    assert result["error"] is not None
    assert invoke.calls["n"] == 2, "1회 재시도까지만(최대 2회 호출), 무한 재시도 금지"


def test_review_rejects_duplicate_proposal_id():
    dup = {"verdicts": [
        {"proposal_id": 10, "verdict": "agree", "reasoning": "a"},
        {"proposal_id": 10, "verdict": "reject", "reasoning": "b"},
        {"proposal_id": 20, "verdict": "agree", "reasoning": "c"},
    ], "commentary": "총평"}
    invoke = _invoke_returning(dup)

    result = ava_reviewer.review(BRIEFING_2_PROPOSALS, invoke=invoke)

    assert result["status"] == "degraded"


def test_review_rejects_extra_unknown_proposal_id():
    extra = _valid_response([10, 20, 999])
    invoke = _invoke_returning(extra)

    result = ava_reviewer.review(BRIEFING_2_PROPOSALS, invoke=invoke)

    assert result["status"] == "degraded"


def test_review_rejects_invalid_verdict_value():
    bad_verdict = {"verdicts": [
        {"proposal_id": 10, "verdict": "definitely_yes", "reasoning": "a"},
        {"proposal_id": 20, "verdict": "agree", "reasoning": "b"},
    ], "commentary": "총평"}
    invoke = _invoke_returning(bad_verdict)

    result = ava_reviewer.review(BRIEFING_2_PROPOSALS, invoke=invoke)

    assert result["status"] == "degraded"


def test_review_rejects_commentary_used_as_per_proposal_verdict():
    """commentary↔proposal 혼동: 제안별 verdict에 'commentary' 값을 쓰는 건 무효(총평 전용 개념)."""
    confused = {"verdicts": [
        {"proposal_id": 10, "verdict": "commentary", "reasoning": "a"},
        {"proposal_id": 20, "verdict": "agree", "reasoning": "b"},
    ], "commentary": "총평"}
    invoke = _invoke_returning(confused)

    result = ava_reviewer.review(BRIEFING_2_PROPOSALS, invoke=invoke)

    assert result["status"] == "degraded"


def test_review_rejects_non_string_commentary():
    confused = {"verdicts": [
        {"proposal_id": 10, "verdict": "agree", "reasoning": "a"},
        {"proposal_id": 20, "verdict": "agree", "reasoning": "b"},
    ], "commentary": ["이건", "리스트", "임"]}
    invoke = _invoke_returning(confused)

    result = ava_reviewer.review(BRIEFING_2_PROPOSALS, invoke=invoke)

    assert result["status"] == "degraded"


def test_review_rejects_verdict_missing_reasoning():
    """codex review 발견(P2): reasoning은 스키마 required 필드라 검증도 강제해야 한다."""
    no_reasoning = {"verdicts": [
        {"proposal_id": 10, "verdict": "agree"},
        {"proposal_id": 20, "verdict": "agree", "reasoning": "b"},
    ], "commentary": "총평"}
    invoke = _invoke_returning(no_reasoning)

    result = ava_reviewer.review(BRIEFING_2_PROPOSALS, invoke=invoke)

    assert result["status"] == "degraded"


def test_review_rejects_bool_proposal_id_masquerading_as_int():
    """codex review 발견(P2): Python bool은 int 서브클래스라 True/False가 1/0으로 통과하면 안 됨."""
    bool_id = {"verdicts": [
        {"proposal_id": True, "verdict": "agree", "reasoning": "a"},
        {"proposal_id": 20, "verdict": "agree", "reasoning": "b"},
    ], "commentary": "총평"}
    invoke = _invoke_returning(bool_id)

    result = ava_reviewer.review({"pending_proposals": [{"id": 1}, {"id": 20}]}, invoke=invoke)

    assert result["status"] == "degraded"


def test_review_rejects_float_proposal_id_masquerading_as_int():
    float_id = {"verdicts": [
        {"proposal_id": 10.0, "verdict": "agree", "reasoning": "a"},
        {"proposal_id": 20, "verdict": "agree", "reasoning": "b"},
    ], "commentary": "총평"}
    invoke = _invoke_returning(float_id)

    result = ava_reviewer.review(BRIEFING_2_PROPOSALS, invoke=invoke)

    assert result["status"] == "degraded"


def test_review_rejects_when_json_is_none():
    """스키마 강제 실패로 claude 응답에서 JSON 추출 자체가 안 된 경우도 스키마 위반과 동일 취급."""
    invoke = _invoke_returning(None)

    result = ava_reviewer.review(BRIEFING_2_PROPOSALS, invoke=invoke)

    assert result["status"] == "degraded"
    assert invoke.calls["n"] == 2


def test_review_with_no_pending_proposals_accepts_empty_verdicts():
    empty_ok = {"verdicts": [], "commentary": "오늘은 검토할 제안이 없습니다."}
    invoke = _invoke_returning(empty_ok)

    result = ava_reviewer.review(BRIEFING_EMPTY, invoke=invoke)

    assert result["status"] == "ok"
    assert result["verdicts"] == []


def test_review_includes_model_prompt_version_and_briefing_hash_for_ledger():
    """T4 expert_ledger.record가 run 원장을 채우려면 review()가 이 3개를 반환해야 한다."""
    invoke = _invoke_returning(_valid_response([10, 20]))

    result = ava_reviewer.review(BRIEFING_2_PROPOSALS, invoke=invoke, model="opus")

    assert result["model"] == "opus"
    assert result["prompt_version"] == ava_reviewer._PROMPT_VERSION
    assert isinstance(result["briefing_hash"], str) and len(result["briefing_hash"]) > 0


def test_review_briefing_hash_is_deterministic_for_same_briefing():
    invoke1 = _invoke_returning(_valid_response([10, 20]))
    invoke2 = _invoke_returning(_valid_response([10, 20]))

    r1 = ava_reviewer.review(BRIEFING_2_PROPOSALS, invoke=invoke1)
    r2 = ava_reviewer.review(BRIEFING_2_PROPOSALS, invoke=invoke2)

    assert r1["briefing_hash"] == r2["briefing_hash"]


def test_review_briefing_hash_differs_for_different_briefing():
    invoke1 = _invoke_returning(_valid_response([10, 20]))
    invoke2 = _invoke_returning(_valid_response([10]))

    r1 = ava_reviewer.review(BRIEFING_2_PROPOSALS, invoke=invoke1)
    r2 = ava_reviewer.review({"pending_proposals": [{"id": 10}]}, invoke=invoke2)

    assert r1["briefing_hash"] != r2["briefing_hash"]


def test_review_degraded_result_also_includes_model_and_briefing_hash():
    bad = _valid_response([10])  # 20 누락 → degraded
    invoke = _invoke_returning(bad)

    result = ava_reviewer.review(BRIEFING_2_PROPOSALS, invoke=invoke, model="opus")

    assert result["status"] == "degraded"
    assert result["model"] == "opus"
    assert isinstance(result["briefing_hash"], str) and len(result["briefing_hash"]) > 0


def test_review_calls_invoke_with_ava_persona_and_schema():
    captured = {}

    def fake(prompt, *, system, schema, model, timeout):
        captured["system"] = system
        captured["schema"] = schema
        captured["model"] = model
        captured["prompt"] = prompt
        return {"json": _valid_response([10, 20]), "raw": "", "usage": {}}

    ava_reviewer.review(BRIEFING_2_PROPOSALS, invoke=fake)

    assert "Ava" in captured["system"]
    assert captured["schema"] is ava_reviewer._RESPONSE_SCHEMA
    assert captured["model"] == "opus"
    assert "10" in captured["prompt"] and "20" in captured["prompt"]
