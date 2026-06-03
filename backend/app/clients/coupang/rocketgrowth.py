# rocketgrowth.py — 쿠팡 로켓그로스(RG) 도메인 SA (9개). 읽기 5개 구현, 쓰기 2·카테고리 2 stub.
# 명세: docs/references/05_coupang_rocketgrowth_api_specs.md (트랙 P3, D-14).
# 트랙 D-8: vendorItemId는 vendor_id 귀속 → 계정별 클라이언트. 호출은 서버 IP에서만(로컬 403).
# 두 게이트웨이 경로: seller_api(#1·#2, 기존 products와 동일 패밀리) / rg_open_api(#3·#4·#5, 분당 50회).
from __future__ import annotations

import logging
from collections.abc import Iterator

from app.clients.coupang._base import CoupangBaseClient, CoupangReadError

log = logging.getLogger(__name__)

_SELLER_BASE = "/v2/providers/seller_api/apis/api/v1/marketplace"
_RG_BASE = "/v2/providers/rg_open_api/apis/api/v1/vendors"


def _ok(resp: dict | None) -> bool:
    """쿠팡 RG 응답 성공 판정. seller_api는 code='SUCCESS', rg_open_api는 code=200(숫자)."""
    return bool(resp) and str(resp.get("code")) in ("200", "SUCCESS")


class CoupangRocketGrowthClient(CoupangBaseClient):
    """쿠팡 로켓그로스 API 단일책임 래퍼. 각 메서드 = 1 엔드포인트(SA). raw data 반환, None=실패.

    조합/저장/계산(사이즈→CBM, 재고 적재, RG주문 적재)은 Harness(services/coupang/rg_*_sync.py)가
    담당한다. SA는 다른 SA를 모른다(원칙 18-6). 하드 실패는 CoupangReadError로 표면화(원칙22·codex P1).
    """

    # ════════════════════════════════════════════════
    # 읽기 (구현) — 사이즈·재고·RG주문
    # ════════════════════════════════════════════════

    # ── #2 상품 목록 페이징 (RG/하이브리드) — sellerProductId 열거 ──
    def list_rg_products(
        self, *, next_token: str | None = None, max_per_page: int = 100,
        status: str | None = None,
    ) -> dict | None:
        """RG 상품 목록 페이징 조회 (명세 §2). businessTypes=rocketGrowth 고정.

        반환: {code, message, nextToken(빈문자열=마지막), data:[{sellerProductId, ...}]} 또는 None.
        """
        params: dict = {
            "vendorId": self.vendor_id,
            "maxPerPage": max_per_page,
            "businessTypes": "rocketGrowth",
        }
        if next_token:
            params["nextToken"] = next_token
        if status:
            params["status"] = status
        return self._request("GET", f"{_SELLER_BASE}/seller-products", params)

    def iter_rg_products(self, *, max_per_page: int = 100) -> Iterator[dict]:
        """RG 상품 목록을 nextToken 페이징으로 전부 순회 (제너레이터). 각 상품 dict yield.

        하드 실패(None·비성공코드)는 CoupangReadError로 표면화(stale 위장 방지). 빈 data=자연 종료.
        """
        next_token: str | None = None
        seen: set[str] = set()
        while True:
            resp = self.list_rg_products(next_token=next_token, max_per_page=max_per_page)
            if resp is None:
                raise CoupangReadError(f"RG 상품목록 읽기 실패(None): {self.vendor_id}")
            if not _ok(resp):
                raise CoupangReadError(
                    f"RG 상품목록 API 에러 code={resp.get('code')} msg={resp.get('message')}"
                )
            for product in resp.get("data", []) or []:
                yield product
            next_token = resp.get("nextToken") or ""
            if not next_token or next_token in seen:
                break
            seen.add(next_token)

    # ── #1 상품 조회 (RG 스키마 — items[].rocketGrowthItemData.skuInfo 사이즈) ──
    def get_rg_product(self, seller_product_id: int) -> dict | None:
        """RG 상품 단건 조회 (명세 §1). data(상품+items[]) 반환, None=실패.

        items[].rocketGrowthItemData.skuInfo에 width/length/height(mm)·weight/netWeight(g),
        .vendorItemId(옵션ID)·.priceData. 파싱·CBM 계산은 Harness(rg_size_sync) 담당.
        """
        resp = self._request("GET", f"{_SELLER_BASE}/seller-products/{seller_product_id}")
        if not _ok(resp):
            return None
        return resp.get("data")

    # ── #3 로켓창고 재고 summaries (옵션별 주문가능재고·30일판매) ──
    def get_inventory_summaries(
        self, *, vendor_item_id: str | int | None = None, next_token: str | None = None,
    ) -> dict | None:
        """로켓창고 재고 요약 조회 (명세 §3). vendor_item_id 지정 시 단일 SKU(nextToken 무시).

        반환: {code(200), message, data:[{vendorItemId, externalSkuId,
        inventoryDetails.totalOrderableQuantity, salesCountMap.SALES_COUNT_LAST_THIRTY_DAYS}],
        nextToken} 또는 None. ★rg_open_api 분당 50회 제한.
        """
        params: dict = {}
        if vendor_item_id is not None:
            params["vendorItemId"] = vendor_item_id
        if next_token:
            params["nextToken"] = next_token
        return self._request(
            "GET", f"{_RG_BASE}/{self.vendor_id}/rg/inventory/summaries", params or None
        )

    def iter_inventory_summaries(self) -> Iterator[dict]:
        """로켓창고 재고 요약을 nextToken 페이징으로 전부 순회 (제너레이터). 각 재고행 dict yield.

        하드 실패는 CoupangReadError로 표면화. 빈 data=자연 종료(재고 없음=정상).
        """
        next_token: str | None = None
        seen: set[str] = set()
        while True:
            resp = self.get_inventory_summaries(next_token=next_token)
            if resp is None:
                raise CoupangReadError(f"RG 재고 읽기 실패(None): {self.vendor_id}")
            if not _ok(resp):
                raise CoupangReadError(
                    f"RG 재고 API 에러 code={resp.get('code')} msg={resp.get('message')}"
                )
            for row in resp.get("data", []) or []:
                yield row
            next_token = resp.get("nextToken") or ""
            if not next_token or next_token in seen:
                break
            seen.add(next_token)

    # ── #4 RG 주문 목록 (기간 조회, 최대 30일 윈도우) ──
    def list_rg_orders(
        self, *, paid_date_from: str, paid_date_to: str, next_token: str | None = None,
    ) -> dict | None:
        """RG 주문 목록 조회 (명세 §4). paidDateFrom/To = yyyymmdd, ★최대 30일. 분당 50회.

        반환: {code(200), message, data:[{orderId, vendorId, paidAt(ms문자열),
        orderItems:[{vendorItemId, productName, salesQuantity, unitSalesPrice|salesPrice, currency}]}],
        nextToken} 또는 None.
        """
        params: dict = {
            "paidDateFrom": paid_date_from,
            "paidDateTo": paid_date_to,
        }
        if next_token:
            params["nextToken"] = next_token
        return self._request("GET", f"{_RG_BASE}/{self.vendor_id}/rg/orders", params)

    def iter_rg_orders(
        self, *, paid_date_from: str, paid_date_to: str
    ) -> Iterator[dict]:
        """RG 주문을 nextToken 페이징으로 전부 순회 (제너레이터). 각 주문 dict yield.

        하드 실패는 CoupangReadError로 표면화. ≤30일 윈도우 분할은 Harness 책임(명세 §4).
        """
        next_token: str | None = None
        seen: set[str] = set()
        while True:
            resp = self.list_rg_orders(
                paid_date_from=paid_date_from, paid_date_to=paid_date_to, next_token=next_token
            )
            if resp is None:
                raise CoupangReadError(f"RG 주문 읽기 실패(None): {self.vendor_id}")
            if not _ok(resp):
                raise CoupangReadError(
                    f"RG 주문 API 에러 code={resp.get('code')} msg={resp.get('message')}"
                )
            for order in resp.get("data", []) or []:
                yield order
            next_token = resp.get("nextToken") or ""
            if not next_token or next_token in seen:
                break
            seen.add(next_token)

    # ── #5 RG 주문 단건 (orderId) ──
    def get_rg_order(self, order_id: int | str) -> dict | None:
        """RG 주문 단건 조회 (명세 §5). data(객체: orderId·paidAt(ISO)·orderItems[]) 반환, None=실패."""
        resp = self._request("GET", f"{_RG_BASE}/{self.vendor_id}/rg/order/{order_id}")
        if not _ok(resp):
            return None
        return resp.get("data")

    # ════════════════════════════════════════════════
    # 쓰기 (stub — 쓰기 페이즈, product_write.py Harness + dry_run, 트랙 D-1·D-14)
    # ════════════════════════════════════════════════
    def create_rg_product(self, payload: dict) -> dict | None:
        """RG 상품 생성 POST .../seller-products (명세 §6). ⚠️라이브 스토어 등록 — 쓰기 페이즈."""
        raise NotImplementedError("RG 상품 생성은 쓰기 페이즈(product_write.py + dry_run)에서 구현")

    def update_rg_product(self, payload: dict) -> dict | None:
        """RG 상품 수정 PUT .../seller-products (명세 §7). ⚠️라이브 스토어 수정 — 쓰기 페이즈."""
        raise NotImplementedError("RG 상품 수정은 쓰기 페이즈(product_write.py + dry_run)에서 구현")

    # ════════════════════════════════════════════════
    # 카테고리 (stub — P6 category.py 도메인 소관, 트랙 D-14)
    # ════════════════════════════════════════════════
    def get_category_meta(self, display_category_code: int) -> dict | None:
        """카테고리 메타 정보 조회 GET .../meta/category-related-metas/... (명세 §6 #8). P6에서 구현."""
        raise NotImplementedError("카테고리 메타는 P6 category.py 도메인에서 구현")

    def list_rg_categories(self) -> dict | None:
        """RG 운영 카테고리 목록 GET .../meta/display-categories?registrationType=RFM (#9). P6에서 구현."""
        raise NotImplementedError("RG 카테고리 목록은 P6 category.py 도메인에서 구현")
