# products.py — 쿠팡 상품 도메인 SA (22개). 읽기 5개 구현, 쓰기/미수집 17개 stub.
# 명세: docs/references/02_coupang_product_api_specs.md
# 트랙 D-8: vendorItemId는 vendor_id 귀속 → 계정별 클라이언트로 호출. 호출은 서버 IP에서만.
from __future__ import annotations

import logging
from collections.abc import Iterator

from app.clients.coupang._base import CoupangBaseClient

log = logging.getLogger(__name__)

_SELLER_BASE = "/v2/providers/seller_api/apis/api/v1/marketplace"


class CoupangProductClient(CoupangBaseClient):
    """쿠팡 상품 API 단일책임 래퍼. 각 메서드 = 1 엔드포인트(SA). raw data 반환, None=실패.

    조합/저장/안전장치는 Harness(services/coupang/product_sync.py 등)가 담당한다.
    SA는 다른 SA를 모른다(원칙 18-6).
    """

    # ────────────────────────────────────────────────
    # 읽기 (결합축 — 구현됨)
    # ────────────────────────────────────────────────
    def list_products(
        self,
        *,
        status: str | None = None,
        next_token: str | None = None,
        max_per_page: int = 100,
        seller_product_id: int | None = None,
        seller_product_name: str | None = None,
        created_at: str | None = None,
    ) -> dict | None:
        """상품 목록 페이징 조회 (GET_PRODUCTS_BY_QUERY).

        반환: {code, message, nextToken, data:[...]} 또는 None. nextToken 빈문자열=마지막.
        """
        params: dict = {"vendorId": self.vendor_id, "maxPerPage": max_per_page}
        if status:
            params["status"] = status
        if next_token:
            params["nextToken"] = next_token
        if seller_product_id is not None:
            params["sellerProductId"] = seller_product_id
        if seller_product_name:
            params["sellerProductName"] = seller_product_name
        if created_at:
            params["createdAt"] = created_at
        return self._request("GET", f"{_SELLER_BASE}/seller-products", params)

    def iter_products(
        self, *, status: str | None = None, max_per_page: int = 100
    ) -> Iterator[dict]:
        """상품 목록을 nextToken 페이징으로 전부 순회 (제너레이터). 각 상품 dict yield."""
        next_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            resp = self.list_products(
                status=status, next_token=next_token, max_per_page=max_per_page
            )
            if not resp or str(resp.get("code")) not in ("200", "SUCCESS"):
                break
            for product in resp.get("data", []) or []:
                yield product
            next_token = resp.get("nextToken") or ""
            if not next_token or next_token in seen_tokens:
                break
            seen_tokens.add(next_token)

    def get_product(self, seller_product_id: int) -> dict | None:
        """상품 조회 (GET_PRODUCT_BY_PRODUCT_ID). data(상품+items[]) 반환.

        items[]에 vendorItemId(옵션ID)·salePrice·supplyPrice(원가)·maximumBuyCount(재고)·
        saleAgentCommission(수수료)·externalVendorSku 포함.
        """
        resp = self._request("GET", f"{_SELLER_BASE}/seller-products/{seller_product_id}")
        return resp.get("data") if resp else None

    def get_product_partial(self, seller_product_id: int) -> dict | None:
        """상품 조회 (승인불필요, GET_PARTIAL_PRODUCT_BY_PRODUCT_ID). 승인 전 최신 데이터 포함."""
        resp = self._request(
            "GET", f"{_SELLER_BASE}/seller-products/{seller_product_id}/partial"
        )
        return resp.get("data") if resp else None

    def get_item_inventory(self, vendor_item_id: int | str) -> dict | None:
        """상품 아이템별 수량/가격/상태 조회 (GET_PRODUCT_QUANTITY_PRICE_STATUS).

        반환: {sellerItemId, amountInStock, salePrice, onSale} 또는 None. 옵션ID 단위 실시간.
        """
        resp = self._request(
            "GET", f"{_SELLER_BASE}/vendor-items/{vendor_item_id}/inventories"
        )
        return resp.get("data") if resp else None

    def get_products_by_external_sku(self, external_vendor_sku_code: str) -> list | None:
        """상품 요약 정보 조회 (GET_PRODUCT_BY_EXTERNAL_SKU). 우리 SKU → 상품 N개 요약."""
        resp = self._request(
            "GET",
            f"{_SELLER_BASE}/seller-products/external-vendor-sku-codes/{external_vendor_sku_code}",
        )
        return resp.get("data") if resp else None

    # ────────────────────────────────────────────────
    # 읽기 (명세 미수집 — stub). 해당 페이즈에서 reference 02 §3 방식으로 수집 후 구현.
    # ────────────────────────────────────────────────
    def list_products_interval(self, *args, **kwargs):
        """상품 목록 구간 조회 (article 360033645054). 미구현."""
        raise NotImplementedError("상품 목록 구간 조회 — 명세수집 후 구현 (article 360033645054)")

    def get_registration_status(self, *args, **kwargs):
        """상품 등록 현황 조회 (article 4404525347353). 미구현."""
        raise NotImplementedError("상품 등록 현황 조회 — 명세수집 후 구현 (article 4404525347353)")

    def get_status_history(self, *args, **kwargs):
        """상품 상태변경이력 조회 (article 360034156213). 미구현."""
        raise NotImplementedError("상품 상태변경이력 조회 — 명세수집 후 구현 (article 360034156213)")

    # ────────────────────────────────────────────────
    # 쓰기 (라이브 스토어 변경 — stub). 별도 쓰기 페이즈에서 dry_run+명시확인 안전장치와 함께 구현.
    # ────────────────────────────────────────────────
    def create_product(self, *args, **kwargs):
        """상품 생성 (article 360033877853). ⚠️쓰기. 미구현."""
        raise NotImplementedError("상품 생성 — 쓰기 페이즈에서 dry_run 안전장치와 구현 (article 360033877853)")

    def request_approval(self, *args, **kwargs):
        """상품 승인 요청 (article 360033644894). ⚠️쓰기. 미구현."""
        raise NotImplementedError("상품 승인 요청 — 쓰기 페이즈 (article 360033644894)")

    def update_product(self, *args, **kwargs):
        """상품 수정 (승인필요, article 360034156073). ⚠️쓰기. 미구현."""
        raise NotImplementedError("상품 수정(승인필요) — 쓰기 페이즈 (article 360034156073)")

    def update_product_partial(self, *args, **kwargs):
        """상품 수정 (승인불필요, article 360042169352). ⚠️쓰기. 미구현."""
        raise NotImplementedError("상품 수정(승인불필요) — 쓰기 페이즈 (article 360042169352)")

    def delete_product(self, *args, **kwargs):
        """상품 삭제 (article 360033644954). ⚠️쓰기. 미구현."""
        raise NotImplementedError("상품 삭제 — 쓰기 페이즈에서 명시확인 필수 (article 360033644954)")

    def update_item_quantity(self, *args, **kwargs):
        """상품 아이템별 수량 변경 (article 360034156253). ⚠️쓰기. 미구현."""
        raise NotImplementedError("아이템 수량 변경 — 쓰기 페이즈 (article 360034156253)")

    def update_item_price(self, *args, **kwargs):
        """상품 아이템별 가격 변경 (article 360034156273). ⚠️쓰기. 미구현."""
        raise NotImplementedError("아이템 가격 변경 — 쓰기 페이즈 (article 360034156273)")

    def update_item_base_price(self, *args, **kwargs):
        """상품 아이템별 할인율 기준가격 변경 (article 360034156333). ⚠️쓰기. 미구현."""
        raise NotImplementedError("아이템 할인율기준가 변경 — 쓰기 페이즈 (article 360034156333)")

    def resume_item_sale(self, *args, **kwargs):
        """상품 아이템별 판매 재개 (article 360033645154). ⚠️쓰기. 미구현."""
        raise NotImplementedError("아이템 판매 재개 — 쓰기 페이즈 (article 360033645154)")

    def stop_item_sale(self, *args, **kwargs):
        """상품 아이템별 판매 중지 (article 360034156313). ⚠️쓰기. 미구현."""
        raise NotImplementedError("아이템 판매 중지 — 쓰기 페이즈 (article 360034156313)")

    def enable_auto_option_item(self, *args, **kwargs):
        """자동생성옵션 활성화 (옵션 단위, article 27244057869209). ⚠️쓰기. 미구현."""
        raise NotImplementedError("자동생성옵션 활성화(옵션) — 쓰기 페이즈 (article 27244057869209)")

    def enable_auto_option_all(self, *args, **kwargs):
        """자동생성옵션 활성화 (전체 단위, article 27244235299609). ⚠️쓰기. 미구현."""
        raise NotImplementedError("자동생성옵션 활성화(전체) — 쓰기 페이즈 (article 27244235299609)")

    def disable_auto_option_item(self, *args, **kwargs):
        """자동생성옵션 비활성화 (옵션 단위, article 27244841785497). ⚠️쓰기. 미구현."""
        raise NotImplementedError("자동생성옵션 비활성화(옵션) — 쓰기 페이즈 (article 27244841785497)")

    def disable_auto_option_all(self, *args, **kwargs):
        """자동생성옵션 비활성화 (전체 단위, article 27246230561177). ⚠️쓰기. 미구현."""
        raise NotImplementedError("자동생성옵션 비활성화(전체) — 쓰기 페이즈 (article 27246230561177)")
