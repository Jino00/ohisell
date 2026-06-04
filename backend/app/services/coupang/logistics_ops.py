# logistics_ops.py — 쿠팡 물류 쓰기 Harness (트랙 D-16, W1).
# SA 조합: CoupangLogisticsClient 쓰기 4개(#2 출고지생성·#3 출고지수정·#4 반품지생성·#7 반품지수정).
# 핵심: dry_run 게이트는 여기서만(_write_guard). SA는 dry_run을 모름(단일 책임, 원칙18-6).
# 소비자: p6_meta 라우터 POST/PUT. 검증: 안전 항목 라이브 1건(D-16).
from __future__ import annotations

import logging
from typing import Any

from app.clients.coupang.logistics import (
    CoupangLogisticsClient,
    outbound_create_path,
    outbound_update_path,
    return_create_path,
    return_update_path,
)
from app.config import CoupangAccountConfig
from app.services.coupang._write_guard import guarded_write

log = logging.getLogger(__name__)


def _client(account: CoupangAccountConfig) -> CoupangLogisticsClient:
    return CoupangLogisticsClient(account)


def _preview_payload(account: CoupangAccountConfig, body: dict, **extra: Any) -> dict:
    """dry-run 미리보기용 payload(vendorId 강제 — SA._inject_vendor와 동일 결과, codex[P1])."""
    payload = dict(body)
    payload["vendorId"] = account.vendor_id  # 경로가 진실 — 강제 덮어쓰기
    payload.update(extra)
    return payload


def create_outbound_place(
    account: CoupangAccountConfig,
    body: dict,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#2 출고지 생성. dry_run 기본(라이브 미변경)."""
    client = _client(account)
    return guarded_write(
        operation="출고지 생성",
        method="POST",
        path=outbound_create_path(account.vendor_id),
        payload=_preview_payload(account, body),
        sa_call=lambda: client.create_outbound_place(body=body),
        dry_run=dry_run,
        confirm=confirm,
    )


def update_outbound_place(
    account: CoupangAccountConfig,
    code: str,
    body: dict,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#3 출고지 수정. dry_run 기본."""
    client = _client(account)
    return guarded_write(
        operation=f"출고지 수정({code})",
        method="PUT",
        path=outbound_update_path(account.vendor_id, code),
        payload=_preview_payload(account, body),
        sa_call=lambda: client.update_outbound_place(code=code, body=body),
        dry_run=dry_run,
        confirm=confirm,
    )


def create_return_place(
    account: CoupangAccountConfig,
    body: dict,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#4 반품지 생성. dry_run 기본."""
    client = _client(account)
    return guarded_write(
        operation="반품지 생성",
        method="POST",
        path=return_create_path(account.vendor_id),
        payload=_preview_payload(account, body),
        sa_call=lambda: client.create_return_place(body=body),
        dry_run=dry_run,
        confirm=confirm,
    )


def update_return_place(
    account: CoupangAccountConfig,
    return_center_code: str,
    body: dict,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> dict:
    """#7 반품지 수정. dry_run 기본."""
    client = _client(account)
    return guarded_write(
        operation=f"반품지 수정({return_center_code})",
        method="PUT",
        path=return_update_path(account.vendor_id, return_center_code),
        payload=_preview_payload(account, body, returnCenterCode=return_center_code),
        sa_call=lambda: client.update_return_place(
            return_center_code=return_center_code, body=body
        ),
        dry_run=dry_run,
        confirm=confirm,
    )
