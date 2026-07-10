# expert_llm.py — E1a claude CLI subprocess 린 어댑터(C1).
# 출처: AI_office `backend/app/utils/claude_cli.py`의 `call_claude_cli`를 린 포팅한 것.
# AI_office 원본은 cost_guard/chokepoint/usage_db 등 그 레포 전용 계측·과금 게이팅과
# 강결합돼 있어 그대로 못 쓴다(D-NAO-30) — 이 파일은 그 계측 레이어 없이 subprocess 호출
# +재시도+JSON 파싱만 포팅한다(cost_guard 미포함, PLAN §8 명시).
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess

log = logging.getLogger(__name__)


def _get_claude_path() -> str:
    path = shutil.which("claude")
    if not path:
        raise RuntimeError("claude CLI를 찾을 수 없음(PATH에 설치·로그인 필요)")
    return path


def _get_clean_env() -> dict[str, str]:
    """ANTHROPIC_API_KEY 제거 — OAuth(Max plan) 세션 인증 사용(D-NAO-30)."""
    return {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}


def _invoke_claude(
    prompt: str, *, system: str, schema: dict | None, model: str, timeout: int,
) -> dict:
    """claude CLI(`--print --output-format json`) subprocess 호출. 재시도: timeout→×1.5→×2(3회).

    프롬프트는 argv가 아니라 stdin으로 전달(ARG_MAX 회피, AI_office I-10 전례).
    Returns: {"text": str, "json": dict|None, "raw": str, "usage": dict}
    """
    claude_path = _get_claude_path()
    env = _get_clean_env()

    full_prompt = prompt
    if schema:
        full_prompt = (
            f"{prompt}\n\n"
            f"반드시 다음 JSON 스키마에 맞게 JSON만 응답하세요(다른 텍스트 없이):\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )

    cmd = [claude_path, "--print", "--output-format", "json", "--model", model, "--system-prompt", system]
    timeouts = (timeout, int(timeout * 1.5), timeout * 2)

    last_timeout_err: subprocess.TimeoutExpired | None = None
    for attempt, t in enumerate(timeouts, 1):
        try:
            proc = subprocess.run(cmd, input=full_prompt, capture_output=True, text=True, timeout=t, cwd="/tmp", env=env)
        except subprocess.TimeoutExpired as exc:
            last_timeout_err = exc
            log.warning("expert_llm: claude CLI 타임아웃(%d초, %d/%d회 시도)", t, attempt, len(timeouts))
            continue

        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI 오류(코드 {proc.returncode}): {(proc.stdout + proc.stderr).strip()[:200]}"
            )

        raw = proc.stdout.strip()
        try:
            resp = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"claude CLI JSON 봉투 파싱 실패: {raw[:200]}") from exc
        text: str = resp.get("result", "")
        usage: dict = resp.get("usage", {})

        parsed_json: dict | None = None
        if schema and text:
            block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            candidates = [block_match.group(1)] if block_match else [text.strip()]
            # 2026-07-10 prod 실측: opus가 ```json 펜스를 열고 닫지 않아 위 정규식이 미매치
            # → 첫 '{'~마지막 '}' substring을 마지막 후보로 시도(펜스 유실·앞뒤 설명문 방어).
            brace_start, brace_end = text.find("{"), text.rfind("}")
            if brace_start != -1 and brace_end > brace_start:
                candidates.append(text[brace_start : brace_end + 1])
            for candidate in candidates:
                try:
                    parsed_json = json.loads(candidate)
                    break
                except json.JSONDecodeError:
                    parsed_json = None

        return {"text": text, "json": parsed_json, "raw": raw, "usage": usage}

    raise RuntimeError(f"expert_llm: claude CLI 타임아웃 — {len(timeouts)}회 시도 후 실패(최대 {timeouts[-1]}초)") from last_timeout_err
