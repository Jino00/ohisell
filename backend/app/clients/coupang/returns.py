# returns.py — 쿠팡 반품/취소 도메인 SA (7개). 읽기 4개 구현, 쓰기 3개 stub.
# 명세: docs/references/03_coupang_returns_api_specs.md §1
# 트랙 D-8: vendorId는 path에 박힘 → 계정별 클라이언트로 호출. 호출은 서버 IP에서만(로컬 403).
from __future__ import annotations

import logging
from collections.abc import Iterator

from app.clients.coupang._base import CoupangBaseClient, CoupangReadError

log = logging.getLogger(__name__)

# 반품/교환 API는 상품 API(/marketplace)와 달리 /v6·/v4 + vendorId가 path에 박힘.
_RETURN_V6 = "/v2/providers/openapi/apis/api/v6/vendors"
_RETURN_V4 = "/v2/providers/openapi/apis/api/v4/vendors"


class CoupangReturnClient(CoupangBaseClient):
    """쿠팡 반품/취소 API 단일책임 래퍼. 각 메서드 = 1 엔드포인트(SA). raw data 반환, None=실패.

    조합/저장/차감 로직은 Harness(services/coupang/returns_sync.py)가 담당한다.
    SA는 다른 SA를 모른다(원칙 18-6).
    """

    # ────────────────────────────────────────────────
    # 읽기 (순매출 차감 회계축 — 구현됨)
    # ────────────────────────────────────────────────
    def list_return_requests(
        self,
        *,
        created_at_from: str,
        created_at_to: str,
        status: str | None = None,
        cancel_type: str = "RETURN",
        next_token: str | None = None,
        max_per_page: int = 50,
        order_id: int | str | None = None,
    ) -> dict | None:
        """반품/취소 요청 목록 조회 (일단위 페이징).

        명세 §1-1. created_at_from/to = "yyyy-MM-dd" (최대 31일).
        cancel_type=CANCEL 조회 시 status·order_id는 제외해야 함(쿠팡 제약 — Harness가 보장).
        반환: {code, message, nextToken, data:[...]} 또는 None. nextToken 빈문자열=마지막.
        """
        params: dict = {
            "createdAtFrom": created_at_from,
            "createdAtTo": created_at_to,
            "maxPerPage": max_per_page,
        }
        if cancel_type:
            params["cancelType"] = cancel_type
        if status:
            params["status"] = status
        if next_token:
            params["nextToken"] = next_token
        if order_id is not None:
            params["orderId"] = order_id
        return self._request("GET", f"{_RETURN_V6}/{self.vendor_id}/returnRequests", params)

    def iter_return_requests(
        self,
        *,
        created_at_from: str,
        created_at_to: str,
        cancel_type: str = "RETURN",
        status: str | None = None,
        max_per_page: int = 50,
    ) -> Iterator[dict]:
        """반품/취소 목록을 nextToken 페이징으로 전부 순회 (제너레이터). 각 접수 dict yield.

        31일 윈도우 분할은 Harness 책임. SA는 주어진 구간을 페이징만 한다.
        """
        next_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            resp = self.list_return_requests(
                created_at_from=created_at_from,
                created_at_to=created_at_to,
                status=status,
                cancel_type=cancel_type,
                next_token=next_token,
                max_per_page=max_per_page,
            )
            # codex [P1]: 하드 실패(None=인증/네트워크, 비200=API에러)는 raise로 표면화.
            # 정상 빈 페이지(code 200, data 없음)는 아래에서 자연 종료(stale 위장 방지).
            if resp is None:
                raise CoupangReadError(f"반품목록 읽기 실패(None): {self.vendor_id} {cancel_type}")
            if str(resp.get("code")) not in ("200", "SUCCESS"):
                raise CoupangReadError(
                    f"반품목록 API 에러 code={resp.get('code')} msg={resp.get('message')}"
                )
            for receipt in resp.get("data", []) or []:
                yield receipt
            next_token = resp.get("nextToken") or ""
            if not next_token or next_token in seen_tokens:
                break
            seen_tokens.add(next_token)

    def get_return_request(self, receipt_id: int | str) -> list | None:
        """반품요청 단건 조회 (명세 §1-2). receipt_id=반품접수번호(취소번호 미지원).

        반환: data(배열, 보통 1건) 또는 None.
        """
        resp = self._request(
            "GET", f"{_RETURN_V6}/{self.vendor_id}/returnRequests/{receipt_id}"
        )
        return resp.get("data") if resp else None

    def list_return_withdraw(
        self,
        *,
        date_from: str,
        date_to: str,
        page_index: int = 1,
        size_per_page: int = 100,
    ) -> dict | None:
        """반품철회 이력 기간별 조회 (명세 §1-3). date_from/to="yyyy-MM-dd".

        철회된 반품은 순매출 차감에서 제외해야 함(Harness가 withdrawn 플래그 갱신).
        반환: {code, message, data:[{cancelId, orderId, vendorItemIds[], ...}], nextPageIndex} 또는 None.
        """
        params: dict = {
            "dateFrom": date_from,
            "dateTo": date_to,
            "pageIndex": page_index,
            "sizePerPage": size_per_page,
        }
        return self._request(
            "GET", f"{_RETURN_V4}/{self.vendor_id}/returnWithdrawRequests", params
        )

    def iter_return_withdraw(
        self, *, date_from: str, date_to: str, size_per_page: int = 100
    ) -> Iterator[dict]:
        """반품철회 이력을 pageIndex 페이징으로 전부 순회 (제너레이터). 각 철회 dict yield."""
        page_index = 1
        seen_pages: set[int] = set()
        while True:
            resp = self.list_return_withdraw(
                date_from=date_from,
                date_to=date_to,
                page_index=page_index,
                size_per_page=size_per_page,
            )
            if resp is None:
                raise CoupangReadError(f"반품철회 읽기 실패(None): {self.vendor_id}")
            if str(resp.get("code")) not in ("200", "SUCCESS"):
                raise CoupangReadError(
                    f"반품철회 API 에러 code={resp.get('code')} msg={resp.get('message')}"
                )
            for row in resp.get("data", []) or []:
                yield row
            nxt = resp.get("nextPageIndex")
            # 마지막 페이지면 빈값("") 노출
            if nxt in (None, "", page_index) or page_index in seen_pages:
                break
            seen_pages.add(page_index)
            try:
                page_index = int(nxt)
            except (TypeError, ValueError):
                break

    def get_return_withdraw_by_ids(self, cancel_ids: list[int]) -> list | None:
        """반품철회 이력 접수번호로 조회 (명세 §1-4, POST+body). cancel_ids 최대 50개(number).

        반환: data(배열) 또는 None.
        """
        if not cancel_ids:
            return []
        body = {"cancelIds": [int(c) for c in cancel_ids][:50]}
        resp = self._request(
            "POST", f"{_RETURN_V4}/{self.vendor_id}/returnWithdrawList", body=body
        )
        return resp.get("data") if resp else None

    # ────────────────────────────────────────────────
    # 쓰기 (라이브 스토어 변경 — stub). 별도 쓰기 페이즈에서 dry_run+명시확인 안전장치와 함께 구현.
    # ────────────────────────────────────────────────
    def confirm_receipt(self, *args, **kwargs):
        """반품상품 입고 확인처리 (명세 §1-5, PATCH/PUT). ⚠️쓰기. 미구현.

        PATCH/PUT {_RETURN_V4}/{vendorId}/returnRequests/{receiptId}/receiveConfirmation
        """
        raise NotImplementedError("반품상품 입고확인 — 쓰기 페이즈에서 dry_run 안전장치와 구현")

    def approve_return(self, *args, **kwargs):
        """반품요청 승인 처리 (명세 §1-6, PATCH/PUT). ⚠️쓰기. 미구현.

        PATCH/PUT {_RETURN_V4}/{vendorId}/returnRequests/{receiptId}/approval
        """
        raise NotImplementedError("반품요청 승인 — 쓰기 페이즈에서 명시확인 필수")

    def register_return_invoice(self, *args, **kwargs):
        """회수 송장 등록 (명세 §1-7, POST). ⚠️쓰기. 미구현.

        POST {_RETURN_V4}/{vendorId}/return-exchange-invoices/manual
        """
        raise NotImplementedError("회수 송장 등록 — 쓰기 페이즈에서 구현")
