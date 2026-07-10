# test_expert_llm.py — E1a T3 expert_llm._invoke_claude 린 어댑터 단위테스트
# 실 claude CLI 무의존(subprocess.run을 monkeypatch) — 실제 왕복 스모크는 CLI 설치처에서 별도.
from __future__ import annotations

import json
import subprocess

import pytest

from app.services.naver_ad import expert_llm


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _ok_stdout(result_text: str, usage: dict | None = None) -> str:
    return json.dumps({"result": result_text, "usage": usage or {"input_tokens": 10, "output_tokens": 5}})


def test_invoke_claude_happy_path_returns_text_json_raw_usage(monkeypatch):
    monkeypatch.setattr(expert_llm.shutil, "which", lambda name: "/usr/local/bin/claude")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeCompletedProcess(stdout=_ok_stdout('{"verdicts": [], "commentary": "ok"}'))

    monkeypatch.setattr(expert_llm.subprocess, "run", fake_run)

    result = expert_llm._invoke_claude("본문 프롬프트", system="너는 Ava", schema={"type": "object"}, model="opus", timeout=30)

    assert result["json"] == {"verdicts": [], "commentary": "ok"}
    assert result["usage"] == {"input_tokens": 10, "output_tokens": 5}
    assert "result" in result["raw"]


def test_invoke_claude_builds_expected_cli_command(monkeypatch):
    monkeypatch.setattr(expert_llm.shutil, "which", lambda name: "/usr/local/bin/claude")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeCompletedProcess(stdout=_ok_stdout("{}"))

    monkeypatch.setattr(expert_llm.subprocess, "run", fake_run)

    expert_llm._invoke_claude("프롬프트", system="시스템", schema=None, model="opus", timeout=30)

    cmd = captured["cmd"]
    assert cmd[0] == "/usr/local/bin/claude"
    assert "--print" in cmd and "--output-format" in cmd and "json" in cmd
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "opus"
    assert "--system-prompt" in cmd and cmd[cmd.index("--system-prompt") + 1] == "시스템"
    assert captured["kwargs"]["cwd"] == "/tmp"
    assert captured["kwargs"]["input"] == "프롬프트"  # 프롬프트는 argv 아니라 stdin(ARG_MAX 회피)


def test_invoke_claude_strips_anthropic_api_key_from_env(monkeypatch):
    monkeypatch.setattr(expert_llm.shutil, "which", lambda name: "/usr/local/bin/claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-passed")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return _FakeCompletedProcess(stdout=_ok_stdout("{}"))

    monkeypatch.setattr(expert_llm.subprocess, "run", fake_run)

    expert_llm._invoke_claude("p", system="s", schema=None, model="opus", timeout=30)

    assert "ANTHROPIC_API_KEY" not in captured["env"]


def test_invoke_claude_extracts_json_from_fenced_code_block(monkeypatch):
    monkeypatch.setattr(expert_llm.shutil, "which", lambda name: "/usr/local/bin/claude")

    def fake_run(cmd, **kwargs):
        text = '설명입니다.\n```json\n{"verdicts": [{"proposal_id": 1}], "commentary": "총평"}\n```\n'
        return _FakeCompletedProcess(stdout=_ok_stdout(text))

    monkeypatch.setattr(expert_llm.subprocess, "run", fake_run)

    result = expert_llm._invoke_claude("p", system="s", schema={"type": "object"}, model="opus", timeout=30)

    assert result["json"] == {"verdicts": [{"proposal_id": 1}], "commentary": "총평"}


def test_invoke_claude_extracts_json_from_unclosed_fenced_block(monkeypatch):
    """prod 실측 회귀(2026-07-10): opus가 ```json만 열고 닫는 펜스를 생략 → 기존 정규식 미매치
    → text.strip()이 "```json"으로 시작해 json.loads 실패 → 스키마 위반 2회 → run degraded."""
    monkeypatch.setattr(expert_llm.shutil, "which", lambda name: "/usr/local/bin/claude")

    def fake_run(cmd, **kwargs):
        text = '```json\n{"verdicts": [{"proposal_id": 110}], "commentary": "총평"}'
        return _FakeCompletedProcess(stdout=_ok_stdout(text))

    monkeypatch.setattr(expert_llm.subprocess, "run", fake_run)

    result = expert_llm._invoke_claude("p", system="s", schema={"type": "object"}, model="opus", timeout=30)

    assert result["json"] == {"verdicts": [{"proposal_id": 110}], "commentary": "총평"}


def test_invoke_claude_extracts_json_with_leading_prose_no_fence(monkeypatch):
    """펜스 없이 앞뒤 설명문 사이에 JSON 객체만 있는 경우 — 첫 '{'~마지막 '}' 폴백으로 추출."""
    monkeypatch.setattr(expert_llm.shutil, "which", lambda name: "/usr/local/bin/claude")

    def fake_run(cmd, **kwargs):
        text = '검토 결과입니다.\n{"verdicts": [], "commentary": "ok"}\n이상입니다.'
        return _FakeCompletedProcess(stdout=_ok_stdout(text))

    monkeypatch.setattr(expert_llm.subprocess, "run", fake_run)

    result = expert_llm._invoke_claude("p", system="s", schema={"type": "object"}, model="opus", timeout=30)

    assert result["json"] == {"verdicts": [], "commentary": "ok"}


def test_invoke_claude_json_is_none_when_unparseable(monkeypatch):
    monkeypatch.setattr(expert_llm.shutil, "which", lambda name: "/usr/local/bin/claude")

    def fake_run(cmd, **kwargs):
        return _FakeCompletedProcess(stdout=_ok_stdout("이건 JSON이 아닙니다"))

    monkeypatch.setattr(expert_llm.subprocess, "run", fake_run)

    result = expert_llm._invoke_claude("p", system="s", schema={"type": "object"}, model="opus", timeout=30)

    assert result["json"] is None
    assert result["text"] == "이건 JSON이 아닙니다"


def test_invoke_claude_retries_on_timeout_then_succeeds(monkeypatch):
    monkeypatch.setattr(expert_llm.shutil, "which", lambda name: "/usr/local/bin/claude")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(kwargs["timeout"])
        if len(calls) < 3:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])
        return _FakeCompletedProcess(stdout=_ok_stdout("{}"))

    monkeypatch.setattr(expert_llm.subprocess, "run", fake_run)

    result = expert_llm._invoke_claude("p", system="s", schema={"type": "object"}, model="opus", timeout=10)

    assert calls == [10, 15, 20]  # timeout → ×1.5 → ×2
    assert result["json"] == {}


def test_invoke_claude_raises_after_all_retries_timeout(monkeypatch):
    monkeypatch.setattr(expert_llm.shutil, "which", lambda name: "/usr/local/bin/claude")

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

    monkeypatch.setattr(expert_llm.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="타임아웃"):
        expert_llm._invoke_claude("p", system="s", schema=None, model="opus", timeout=10)


def test_invoke_claude_raises_runtime_error_on_malformed_json_stdout(monkeypatch):
    """codex review 발견(P1): returncode=0인데 stdout이 JSON이 아니면 RuntimeError로 통일(raw JSONDecodeError 유출 금지)."""
    monkeypatch.setattr(expert_llm.shutil, "which", lambda name: "/usr/local/bin/claude")

    def fake_run(cmd, **kwargs):
        return _FakeCompletedProcess(stdout="이건 JSON 봉투가 아닌 배너 텍스트입니다")

    monkeypatch.setattr(expert_llm.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="파싱 실패"):
        expert_llm._invoke_claude("p", system="s", schema=None, model="opus", timeout=10)


def test_invoke_claude_raises_on_nonzero_returncode(monkeypatch):
    monkeypatch.setattr(expert_llm.shutil, "which", lambda name: "/usr/local/bin/claude")

    def fake_run(cmd, **kwargs):
        return _FakeCompletedProcess(returncode=1, stdout="", stderr="에러 발생")

    monkeypatch.setattr(expert_llm.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="에러 발생"):
        expert_llm._invoke_claude("p", system="s", schema=None, model="opus", timeout=10)


def test_invoke_claude_raises_when_cli_not_installed(monkeypatch):
    monkeypatch.setattr(expert_llm.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError, match="claude CLI"):
        expert_llm._invoke_claude("p", system="s", schema=None, model="opus", timeout=10)


def test_invoke_claude_embeds_schema_in_prompt_when_provided(monkeypatch):
    monkeypatch.setattr(expert_llm.shutil, "which", lambda name: "/usr/local/bin/claude")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["input"] = kwargs["input"]
        return _FakeCompletedProcess(stdout=_ok_stdout("{}"))

    monkeypatch.setattr(expert_llm.subprocess, "run", fake_run)

    expert_llm._invoke_claude("본문", system="s", schema={"type": "object", "required": ["verdicts"]}, model="opus", timeout=30)

    assert "본문" in captured["input"]
    assert "verdicts" in captured["input"]  # 스키마가 프롬프트에 병기됨
