# slack_notifier.py — slack_notifier SA (P2-S3 T4, D-NAO-21 / notify_text=BM Phase5, D-NAO-79)
# 역할(SA): 생성된 제안 요약을 Slack incoming webhook으로 발송. 순수 함수 — 광고 API 무관,
#   naver_proposals 쓰기도 안 함(harness가 slack_ts를 저장할지는 harness 소관).
#   webhook 미설정 시 no-op(D-NAO-21, URL 미제공 상태가 정상 운영 경로).
#   notify_text()는 임의 텍스트(BM 예외 브리핑 등)를 같은 발송 로직(_post_with_retries)으로
#   보낸다 — notify()의 proposals 전용 포맷과 발송 인프라(재시도·no-op 관례)를 공유한다.
from __future__ import annotations

import logging
import os
import time

import requests

log = logging.getLogger(__name__)

_WEBHOOK_ENV = "NAVER_SLACK_WEBHOOK_URL"
_TIMEOUT_SECONDS = 10
_MAX_RETRIES = 3


def _build_summary(proposals: list[dict]) -> str:
    """제안 타입별 건수 요약(상세는 콘솔에서 확인 — Slack은 발생 알림 역할)."""
    by_type: dict[str, int] = {}
    for p in proposals:
        by_type[p["proposal_type"]] = by_type.get(p["proposal_type"], 0) + 1
    lines = [f"네이버 SA 제안 {len(proposals)}건 생성"]
    for t in sorted(by_type):
        lines.append(f"- {t}: {by_type[t]}건")
    return "\n".join(lines)


def _extract_slack_ts(resp: requests.Response) -> str | None:
    """incoming webhook은 대개 본문 'ok'만 반환하고 message ts를 주지 않음(실측 문서화,
    docs/references/23 §2 slack 부분 미해당이지만 동일 성격 — codex #15). JSON이 아니거나
    ts 필드가 없으면 None(정상, 완료기준 아님)."""
    try:
        data = resp.json()
    except ValueError:
        return None
    return data.get("ts") if isinstance(data, dict) else None


def _post_with_retries(url: str, text: str) -> dict:
    """공통 발송 로직(재시도·타임아웃) — notify()·notify_text()가 공유한다.
    반환: {"sent": bool, "reason": str|None, "slack_ts": str|None}."""
    last_err: str | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.post(url, json={"text": text}, timeout=_TIMEOUT_SECONDS)
        except requests.RequestException as e:
            last_err = str(e)
        else:
            if resp.status_code == 200:
                return {"sent": True, "reason": None, "slack_ts": _extract_slack_ts(resp)}
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        if attempt < _MAX_RETRIES - 1:
            time.sleep(2 ** attempt)
    return {"sent": False, "reason": last_err, "slack_ts": None}


def notify(proposals: list[dict], *, webhook_url: str | None = None) -> dict:
    """제안 요약을 Slack에 발송. webhook_url 미지정 시 env(NAVER_SLACK_WEBHOOK_URL)를 읽고,
    그마저 없으면 no-op + 로그(D-NAO-21 — 미연결이 정상 상태, 예외 아님).

    반환: {"sent": bool, "reason": str|None, "slack_ts": str|None, "proposal_count": int}.
    slack_ts는 best-effort — 없어도 실패로 취급하지 않는다(codex #15).
    """
    url = webhook_url or os.getenv(_WEBHOOK_ENV)
    if not url:
        log.info("Naver 제안 Slack 미연결(%s 없음) — no-op, %d건 스킵", _WEBHOOK_ENV, len(proposals))
        return {"sent": False, "reason": "no_webhook", "slack_ts": None, "proposal_count": len(proposals)}

    if not proposals:
        return {"sent": False, "reason": "no_proposals", "slack_ts": None, "proposal_count": 0}

    text = _build_summary(proposals)
    result = _post_with_retries(url, text)
    result["proposal_count"] = len(proposals)
    if not result["sent"]:
        log.warning("Naver 제안 Slack 발송 실패(%d회 재시도): %s", _MAX_RETRIES, result["reason"])
    return result


def notify_text(text: str, *, webhook_url: str | None = None, log_label: str = "메시지") -> dict:
    """임의 텍스트(제안 요약이 아닌 메시지)를 Slack에 발송(BM 예외 브리핑 재사용, D-NAO-79).

    webhook_url 미지정 시 env(NAVER_SLACK_WEBHOOK_URL), 그마저 없으면 no-op(D-NAO-21과 동일
    관례 — 미연결이 정상 상태). 호출부가 발송 여부(예: 예외 0건은 생략)를 이미 판단했다는
    전제 — 이 함수는 "주어진 텍스트를 보낸다"만 책임진다(원칙18-1).
    반환: {"sent": bool, "reason": str|None, "slack_ts": str|None}.
    """
    url = webhook_url or os.getenv(_WEBHOOK_ENV)
    if not url:
        log.info("%s Slack 미연결(%s 없음) — no-op", log_label, _WEBHOOK_ENV)
        return {"sent": False, "reason": "no_webhook", "slack_ts": None}
    result = _post_with_retries(url, text)
    if not result["sent"]:
        log.warning("%s Slack 발송 실패(%d회 재시도): %s", log_label, _MAX_RETRIES, result["reason"])
    return result
