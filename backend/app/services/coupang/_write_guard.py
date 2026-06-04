# _write_guard.py — 쿠팡 쓰기 공통 안전장치 (트랙 D-16, 쓰기 페이즈 횡단 모듈).
# 모든 쓰기 Harness(logistics_ops·cs_ops·product_write·coupon_write)가 재사용한다.
# 핵심: dry_run=True 기본 + 명시 confirm 토큰 없으면 라이브 SA 호출 거부(트랙 §5 횡단원칙).
# 원칙22: 쓰기는 라이브 변경 → 실패 삼키지 않고 표면화. dry_run은 "보낼 본문"만 미리보기.
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

# 라이브 실행을 위한 명시 확인 토큰. dry_run=False만으로는 부족 — 이 토큰까지 일치해야 실행.
# (실수로 쿼리 하나 바꿔 라이브 변경되는 것 방지. 호출자가 의도를 두 번 표명.)
WRITE_CONFIRM_TOKEN = "CONFIRM_LIVE_WRITE"


class CoupangLiveWriteRejected(Exception):
    """confirm 토큰 미일치로 가드가 라이브 쓰기를 거부했음을 알리는 전용 예외 (codex[P2] R2).

    built-in PermissionError를 쓰면 라우터가 OS/파일 PermissionError까지 '안전한 거부'로
    오인해 시스템 메시지를 응답에 누수할 수 있다. 가드 거부는 이 전용 예외로만 표현한다.
    """


def guarded_write(
    *,
    operation: str,
    method: str,
    path: str,
    payload: dict[str, Any],
    sa_call: Callable[[], Any],
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict[str, Any]:
    """쓰기 1건을 안전장치와 함께 실행한다.

    Args:
        operation: 사람이 읽는 작업명(예: "출고지 생성").
        method: HTTP 메서드(로그·미리보기용, "POST"/"PUT"/"DELETE").
        path: 호출 경로(미리보기용 — 실제 호출은 sa_call이 수행).
        payload: 전송할 본문(미리보기 + 감사 로그용).
        sa_call: 실제 SA를 호출하는 무인자 콜러블(라이브 실행 시에만 호출).
        dry_run: True(기본)면 SA 호출 없이 미리보기만 반환.
        confirm: 라이브 실행 확인 토큰. dry_run=False일 때 WRITE_CONFIRM_TOKEN과 일치해야 함.

    Returns:
        dry_run: {dry_run:True, operation, method, path, payload}
        executed: {dry_run:False, operation, executed:True, result:<SA 반환>}

    Raises:
        CoupangLiveWriteRejected: dry_run=False인데 confirm 토큰이 없거나 불일치(라이브 보호).
        CoupangWriteError: SA 호출이 실패(SA가 표면화한 것을 그대로 전파).
    """
    if dry_run:
        log.info("[write_guard] DRY-RUN %s — %s %s (라이브 미변경)", operation, method, path)
        return {
            "dry_run": True,
            "operation": operation,
            "method": method,
            "path": path,
            "payload": payload,
            "note": "dry_run=true (기본). 라이브 실행하려면 dry_run=false & confirm 토큰 필요.",
        }

    # 라이브 실행 경로 — 명시 confirm 토큰 검증 (이중 확인)
    # 토큰은 보안용이 아니라 '실수로 라이브 변경' 방지용 이중확인(D-16). 운영 보안은 서버 IP
    # 화이트리스트가 담당. codex[P2]: 단 거부 응답에 토큰 문자열을 노출하지 않는다.
    if confirm != WRITE_CONFIRM_TOKEN:
        log.warning("[write_guard] 라이브 쓰기 거부(%s): confirm 토큰 불일치", operation)
        raise CoupangLiveWriteRejected(
            f"라이브 쓰기 거부: '{operation}' 실행하려면 dry_run=false + 올바른 confirm 토큰 필요 "
            f"(현재 토큰 없음/불일치). 의도 재확인 후 토큰을 명시할 것."
        )

    log.warning("[write_guard] ⚠️LIVE %s — %s %s (라이브 스토어 변경 실행)", operation, method, path)
    result = sa_call()  # SA가 실패 시 CoupangWriteError 표면화
    return {
        "dry_run": False,
        "operation": operation,
        "executed": True,
        "result": result,
    }
