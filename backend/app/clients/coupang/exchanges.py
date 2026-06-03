# exchanges.py — 쿠팡 교환 도메인 SA (4개). 읽기 1개(목록조회) 구현, 쓰기 3개 stub.
# 명세: docs/references/03_coupang_returns_api_specs.md §2
# 교환은 순매출 차감 없음(상품 교체, 환불 아님). 운영 가시성용.
from __future__ import annotations

import logging
from collections.abc import Iterator

from app.clients.coupang._base import CoupangBaseClient

log = logging.getLogger(__name__)

_EXCHANGE_V4 = "/v2/providers/openapi/apis/api/v4/vendors"


class CoupangExchangeClient(CoupangBaseClient):
    """쿠팡 교환 API 단일책임 래퍼. 각 메서드 = 1 엔드포인트(SA). raw data 반환, None=실패.

    조합/저장은 Harness(services/coupang/returns_sync.py)가 담당한다. SA는 다른 SA를 모른다.
    """

    # ────────────────────────────────────────────────
    # 읽기 (운영 기록 — 구현됨)
    # ────────────────────────────────────────────────
    def list_exchange_requests(
        self,
        *,
        created_at_from: str,
        created_at_to: str,
        status: str | None = None,
        next_token: str | None = None,
        max_per_page: int = 50,
        order_id: int | str | None = None,
    ) -> dict | None:
        """교환요청 목록조회 (명세 §2-1). created_at_from/to="yyyy-MM-ddTHH:mm:ss".

        status 미지정 시 전체. status: RECEIPT/PROGRESS/SUCCESS/REJECT/CANCEL.
        반환: {code, message, data:[...], nextToken} 또는 None.
        """
        params: dict = {
            "createdAtFrom": created_at_from,
            "createdAtTo": created_at_to,
            "maxPerPage": max_per_page,
        }
        if status:
            params["status"] = status
        if next_token:
            params["nextToken"] = next_token
        if order_id is not None:
            params["orderId"] = order_id
        return self._request(
            "GET", f"{_EXCHANGE_V4}/{self.vendor_id}/exchangeRequests", params
        )

    def iter_exchange_requests(
        self,
        *,
        created_at_from: str,
        created_at_to: str,
        status: str | None = None,
        max_per_page: int = 50,
    ) -> Iterator[dict]:
        """교환요청 목록을 nextToken 페이징으로 전부 순회 (제너레이터). 각 교환 dict yield."""
        next_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            resp = self.list_exchange_requests(
                created_at_from=created_at_from,
                created_at_to=created_at_to,
                status=status,
                next_token=next_token,
                max_per_page=max_per_page,
            )
            if not resp or str(resp.get("code")) not in ("200", "SUCCESS"):
                break
            data = resp.get("data")
            # 교환 응답 data는 단건이면 Object, 목록이면 Array일 수 있어 정규화
            if isinstance(data, dict):
                yield data
            else:
                for ex in data or []:
                    yield ex
            next_token = resp.get("nextToken") or ""
            if not next_token or next_token in seen_tokens:
                break
            seen_tokens.add(next_token)

    # ────────────────────────────────────────────────
    # 쓰기 (라이브 스토어 변경 — stub). 별도 쓰기 페이즈에서 dry_run+명시확인 안전장치와 함께 구현.
    # ────────────────────────────────────────────────
    def confirm_exchange_receipt(self, *args, **kwargs):
        """교환요청상품 입고 확인처리 (명세 §2-2, PATCH/PUT). ⚠️쓰기. 미구현.

        PATCH/PUT {_EXCHANGE_V4}/{vendorId}/exchangeRequests/{exchangeId}/receiveConfirmation
        """
        raise NotImplementedError("교환 입고확인 — 쓰기 페이즈에서 dry_run 안전장치와 구현")

    def reject_exchange(self, *args, **kwargs):
        """교환요청 거부 처리 (명세 §2-3, PATCH/PUT). ⚠️쓰기. 미구현.

        PATCH/PUT {_EXCHANGE_V4}/{vendorId}/exchangeRequests/{exchangeId}/rejection
        """
        raise NotImplementedError("교환요청 거부 — 쓰기 페이즈에서 명시확인 필수")

    def upload_exchange_invoice(self, *args, **kwargs):
        """교환상품 송장 업로드 처리 (명세 §2-4, POST). ⚠️쓰기. 미구현.

        POST {_EXCHANGE_V4}/{vendorId}/exchangeRequests/{exchangeId}/invoices
        """
        raise NotImplementedError("교환 송장 업로드 — 쓰기 페이즈에서 구현")
