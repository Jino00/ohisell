# _coupang_write_http.py — 쿠팡 쓰기 라우터 공통 HTTP 예외 핸들러 (트랙 D-16).
# p6_meta(W1·W2)·coupang_ops(W3)·향후 coupon 쓰기 라우터가 공유한다.
# 쓰기 Harness가 던지는 도메인 예외를 일관된 HTTP 상태로 변환한다.
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

log = logging.getLogger(__name__)


def handle_write(fn: Callable[[], Any]) -> Any:
    """쓰기 Harness 호출의 공통 예외 처리(가드 거부=403, 쓰기 실패=502).

    가드 거부=403, 입력 검증 오류=400(클라이언트 잘못), 업스트림 쓰기 실패=502.
    CoupangLiveWriteRejected·CoupangWriteError 메시지는 우리가 만든 안전한 문구라 그대로 노출.
    CoupangWriteValidationError(하위)는 상위 CoupangWriteError보다 먼저 잡아 400으로(codex[P2] W3a).
    그 외 예외(built-in PermissionError 포함)는 원문(URL·vendor path·OS 메시지 포함 가능)을
    응답에 누수하지 않고 서버 로그에만 남긴다(codex[P2] W1 R1·R2).
    """
    from app.clients.coupang._base import CoupangWriteError, CoupangWriteValidationError
    from app.services.coupang._write_guard import CoupangLiveWriteRejected

    try:
        return fn()
    except CoupangLiveWriteRejected as e:
        raise HTTPException(status_code=403, detail=str(e))
    except CoupangWriteValidationError as e:  # 상위 CoupangWriteError보다 먼저(MRO 아님 — 순서로 분기)
        raise HTTPException(status_code=400, detail=str(e))
    except CoupangWriteError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        log.exception("쿠팡 쓰기 처리 중 예기치 못한 오류")
        raise HTTPException(
            status_code=502, detail="쿠팡 쓰기 처리 중 오류(상세는 서버 로그 확인)"
        )
