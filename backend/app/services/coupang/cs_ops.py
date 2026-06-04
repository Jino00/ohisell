# cs_ops.py — 쿠팡 CS 쓰기 Harness (트랙 D-16, W2).
# SA 조합: CoupangCsClient 쓰기 3개(#2 상품문의답변·#4 CS이관답변·#5 CS이관확인).
# 핵심: dry_run 게이트는 여기서만(_write_guard). SA는 dry_run 모름(원칙18-1).
# ⚠️ 라이브 실행 시 실제 고객/문의에 답변·확인이 전송된다(D-16: 인위적 테스트 금지, 실사용 시 Jino 실행).
from __future__ import annotations

import logging

from app.clients.coupang._base import CoupangWriteError
from app.clients.coupang.cs import (
    CoupangCsClient,
    cc_confirm_path,
    cc_reply_path,
    coerce_answer_id,
    online_reply_path,
)
from app.config import CoupangAccountConfig
from app.services.coupang._write_guard import guarded_write

log = logging.getLogger(__name__)


def _client(account: CoupangAccountConfig) -> CoupangCsClient:
    return CoupangCsClient(account)


def _require(value: str, field: str) -> str:
    """필수 문자열 빈값 방어(dry-run preview에서도 적용 — codex[P2] W2).

    dry_run은 SA를 호출하지 않으므로 SA._require가 안 돌아간다. preview가 '라이브 가능 본문'이려면
    Harness 진입부에서 dry/live 공통 검증해야 한다. SA._require는 직접호출 대비 이중 방어로 유지.
    """
    v = (value or "").strip()
    if not v:
        raise CoupangWriteError(f"CS 쓰기 필수 필드 누락: {field}")
    return v


def reply_online_inquiry(
    account: CoupangAccountConfig,
    inquiry_id: str,
    content: str,
    reply_by: str,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#2 상품별 고객문의 답변. dry_run 기본(라이브 미전송)."""
    _require(content, "content")
    _require(reply_by, "reply_by")
    client = _client(account)
    payload = {"content": content, "vendorId": account.vendor_id, "replyBy": reply_by}
    return guarded_write(
        operation=f"상품 고객문의 답변(inquiry={inquiry_id})",
        method="POST",
        path=online_reply_path(account.vendor_id, inquiry_id),
        payload=payload,
        sa_call=lambda: client.reply_online_inquiry(
            inquiry_id=inquiry_id, content=content, reply_by=reply_by
        ),
        dry_run=dry_run,
        confirm=confirm,
    )


def reply_call_center_inquiry(
    account: CoupangAccountConfig,
    inquiry_id: str,
    content: str,
    reply_by: str,
    parent_answer_id: int | str,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#4 쿠팡 고객센터 문의 답변. dry_run 기본. ⚠️ 24시간 미답변 시 API 답변 불가."""
    _require(content, "content")
    _require(reply_by, "reply_by")
    answer_id = coerce_answer_id(parent_answer_id)  # preview=live 일치(codex[P2])
    client = _client(account)
    payload = {
        "vendorId": account.vendor_id,
        "inquiryId": str(inquiry_id),
        "content": content,
        "replyBy": reply_by,
        "parentAnswerId": answer_id,
    }
    return guarded_write(
        operation=f"CS이관 문의 답변(inquiry={inquiry_id})",
        method="POST",
        path=cc_reply_path(account.vendor_id, inquiry_id),
        payload=payload,
        sa_call=lambda: client.reply_call_center_inquiry(
            inquiry_id=inquiry_id,
            content=content,
            reply_by=reply_by,
            parent_answer_id=answer_id,
        ),
        dry_run=dry_run,
        confirm=confirm,
    )


def confirm_call_center_inquiry(
    account: CoupangAccountConfig,
    inquiry_id: str,
    confirm_by: str,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#5 쿠팡 고객센터 문의 확인. dry_run 기본. ⚠️ 24시간 경과 시 불가."""
    _require(confirm_by, "confirm_by")
    client = _client(account)
    payload = {"confirmBy": confirm_by}
    return guarded_write(
        operation=f"CS이관 문의 확인(inquiry={inquiry_id})",
        method="POST",
        path=cc_confirm_path(account.vendor_id, inquiry_id),
        payload=payload,
        sa_call=lambda: client.confirm_call_center_inquiry(
            inquiry_id=inquiry_id, confirm_by=confirm_by
        ),
        dry_run=dry_run,
        confirm=confirm,
    )
