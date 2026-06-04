# products.py — 쿠팡 상품 도메인 SA (22개).
# 읽기 5개 + W3a 단순쓰기 9개(body 없음) + W3b 복잡쓰기 4개(body 있음) + #13 삭제 영구 차단.
# 명세: docs/references/02_coupang_product_api_specs.md (§4 = W3a, §5 = W3b, 2026-06-04).
# 트랙 D-8: vendorItemId는 vendor_id 귀속 → 계정별 클라이언트로 호출. 호출은 서버 IP에서만.
from __future__ import annotations

import logging
from collections.abc import Iterator

from app.clients.coupang._base import (
    CoupangBaseClient,
    CoupangWriteValidationError,
    check_write_response,
)

log = logging.getLogger(__name__)

_SELLER_BASE = "/v2/providers/seller_api/apis/api/v1/marketplace"

# 자동생성옵션(#19~22)은 PROCESSING(비동기 처리중)도 정상 — 명세 02 §4. FAILED만 실패.
_AUTO_OPT_SUCCESS = ("200", "SUCCESS", "PROCESSING")


# ── W3a 쓰기 경로 빌더 (단일 정의 — SA 실행 + Harness dry-run 미리보기 공유, 트랙 D-16) ──
# 전 9개 body 없음(path/query). 옵션ID·가격·수량은 path segment(전부 정수 — Harness _require_int로 보장).
def item_quantity_path(vendor_item_id: int, quantity: int) -> str:
    """#14 재고수량 변경 경로."""
    return f"{_SELLER_BASE}/vendor-items/{vendor_item_id}/quantities/{quantity}"


def item_price_path(vendor_item_id: int, price: int) -> str:
    """#15 판매가격 변경 경로."""
    return f"{_SELLER_BASE}/vendor-items/{vendor_item_id}/prices/{price}"


def item_price_query(
    *,
    force_sale_price_update: bool = False,
    ap_min_sale_price: int | None = None,
    ap_active: bool | None = None,
) -> dict:
    """#15 가격변경 query string 조립 (SA 실행 + Harness preview 공유 — codex[P2] W3a).

    no-body API라 preview가 정확한 path+query를 보이려면 SA·Harness가 같은 query를 써야 한다.
    bool은 'true'/'false' 문자열로 직렬화(쿠팡 예시 일치). 빈 dict면 호출자가 params=None 처리.
    """
    params: dict = {}
    if force_sale_price_update:
        params["forceSalePriceUpdate"] = "true"
    if ap_min_sale_price is not None:
        params["apMinSalePrice"] = ap_min_sale_price
    if ap_active is not None:
        params["apActive"] = "true" if ap_active else "false"
    return params


def item_base_price_path(vendor_item_id: int, original_price: int) -> str:
    """#16 할인율 기준가격 변경 경로."""
    return f"{_SELLER_BASE}/vendor-items/{vendor_item_id}/original-prices/{original_price}"


def item_resume_path(vendor_item_id: int) -> str:
    """#17 판매 재개 경로."""
    return f"{_SELLER_BASE}/vendor-items/{vendor_item_id}/sales/resume"


def item_stop_path(vendor_item_id: int) -> str:
    """#18 판매 중지 경로."""
    return f"{_SELLER_BASE}/vendor-items/{vendor_item_id}/sales/stop"


def auto_option_item_optin_path(vendor_item_id: int) -> str:
    """#19 자동생성옵션 활성화(옵션 단위) 경로."""
    return f"{_SELLER_BASE}/vendor-items/{vendor_item_id}/auto-generated/opt-in"


def auto_option_seller_optin_path() -> str:
    """#20 자동생성옵션 활성화(전체 단위) 경로. vendorId 없음(HMAC키로 셀러 식별)."""
    return f"{_SELLER_BASE}/seller/auto-generated/opt-in"


def auto_option_item_optout_path(vendor_item_id: int) -> str:
    """#21 자동생성옵션 비활성화(옵션 단위) 경로."""
    return f"{_SELLER_BASE}/vendor-items/{vendor_item_id}/auto-generated/opt-out"


def auto_option_seller_optout_path() -> str:
    """#22 자동생성옵션 비활성화(전체 단위) 경로. vendorId 없음."""
    return f"{_SELLER_BASE}/seller/auto-generated/opt-out"


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

    # ════════════════════════════════════════════════
    # W3b 복잡 쓰기 구현 (트랙 D-16) — ⚠️ 라이브 실행자(live executor).
    # ⚠️ 직접 호출 금지. 반드시 Harness(services/coupang/product_write.py)를 경유할 것.
    # #9·#11·#12 = body 있음(POST/PUT JSON). #10 = no body(path param). #13 = 영구 차단.
    # 전부 retry_transient=False(쓰기 재시도=중복실행 위험, D-16 공통 규칙). require_code=True.
    # 명세: docs/references/02_coupang_product_api_specs.md §5(W3b 2026-06-04 수집).
    # ════════════════════════════════════════════════

    def create_product(self, *, body: dict) -> dict:
        """#9 상품 생성 (POST, body 있음). APPROVE_PRODUCT. 명세 02 §5.

        body: 상품 전체 JSON dict (vendorId·sellerProductName·items[] 등 필수포함).
        반환: { code: SUCCESS/ERROR, data: sellerProductId }.
        Raises: CoupangWriteError(실패 — None·비성공 code).
        """
        resp = self._request(
            "POST", f"{_SELLER_BASE}/seller-products", body=body, retry_transient=False
        )
        return check_write_response(resp, "상품생성", require_code=True)

    def request_approval(self, *, seller_product_id: int) -> dict:
        """#10 상품 승인 요청 (PUT, body 없음). APPROVE_PRODUCT. 명세 02 §5.

        '임시저장' 상태에서만 요청 가능. requested=true로 생성하면 자동 처리되어 불필요.
        반환: { code: SUCCESS/ERROR, data: sellerProductId_string }.
        """
        spid = self._vid(seller_product_id)
        resp = self._request(
            "PUT",
            f"{_SELLER_BASE}/seller-products/{spid}/approvals",
            retry_transient=False,
        )
        return check_write_response(resp, f"상품승인요청(spid={spid})", require_code=True)

    def update_product(self, *, body: dict) -> dict:
        """#11 상품 수정 (승인필요, PUT, body 있음). UPDATE_PRODUCT. 명세 02 §5.

        body: 상품 전체 JSON dict (sellerProductId + sellerProductItemId per item 포함).
        승인완료 상품의 판매가·재고·판매상태는 이 API가 아니라 단순쓰기 SA(#14~18) 사용.
        """
        resp = self._request(
            "PUT", f"{_SELLER_BASE}/seller-products", body=body, retry_transient=False
        )
        return check_write_response(resp, "상품수정(승인필요)", require_code=True)

    def update_product_partial(self, *, seller_product_id: int, body: dict) -> dict:
        """#12 상품 수정 (승인불필요, PUT, body 있음). UPDATE_PRODUCT_PARTIAL. 명세 02 §5.

        배송/반품지 정보만 승인 없이 빠르게 수정. '임시저장·승인대기중'은 수정 불가.
        body: sellerProductId + 변경할 배송/반품 필드만(부분 전송 가능).
        """
        spid = self._vid(seller_product_id)
        resp = self._request(
            "PUT",
            f"{_SELLER_BASE}/seller-products/{spid}/partial",
            body=body,
            retry_transient=False,
        )
        return check_write_response(resp, f"상품수정(승인불필요,spid={spid})", require_code=True)

    def delete_product(self, *args, **kwargs) -> None:  # type: ignore[override]
        """#13 상품 삭제 — ⛔ 이 시스템에서 영구 차단(시스템 정책).

        실수로 라이브 상품을 삭제하는 사고를 원천 차단한다. Wing에서 직접 수행할 것.
        """
        raise CoupangWriteValidationError(
            "상품 삭제는 이 시스템에서 허용하지 않습니다. Wing(coupang.com)에서 직접 수행하세요."
        )

    # ════════════════════════════════════════════════
    # W3a 단순 쓰기 구현 (트랙 D-16) — ⚠️ 라이브 실행자(live executor).
    # ⚠️ 직접 호출 금지. 반드시 Harness(services/coupang/product_write.py)를 경유할 것.
    #    아래 메서드는 dry_run/confirm을 모른다(원칙18-1 단일책임) — 호출 즉시 라이브 변경한다.
    #    dry_run 게이트·confirm 토큰·범위/단위 검증은 Harness가 담당(트랙 §5 확정 아키텍처).
    # 전 9개 request body 없음(명세 02 §4) — path/query만 구성. 성공판정=check_write_response.
    # path segment(vid/가격/수량)는 _vid로 정수 강제(직접호출 이중방어, cs.py 패턴 일관).
    # 범위·10원단위·삭제여부 등은 쿠팡이 검증(추정 강제 안 함, D-1).
    # ════════════════════════════════════════════════

    @staticmethod
    def _vid(value: object) -> int:
        """path segment용 정수 강제(직접호출 이중방어 — Harness가 1차 검증, 원칙18-1).

        path에 None/문자열이 들어가 경로가 깨지는 것 방지. 범위·단위 검증은 쿠팡(D-1).
        """
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as e:
            raise CoupangWriteValidationError(
                f"상품 아이템 쓰기: 정수 변환 불가 값({value!r})"
            ) from e

    def update_item_quantity(self, *, vendor_item_id: int, quantity: int) -> dict:
        """#14 아이템 재고수량 변경 (PUT, body 없음). 명세 02 §4.

        Raises: CoupangWriteError(실패 — None·비성공 code·code 부재).
        """
        vid, qty = self._vid(vendor_item_id), self._vid(quantity)
        resp = self._request("PUT", item_quantity_path(vid, qty), retry_transient=False)
        return check_write_response(resp, f"재고변경(vii={vid})", require_code=True)

    def update_item_price(
        self,
        *,
        vendor_item_id: int,
        price: int,
        force_sale_price_update: bool = False,
        ap_min_sale_price: int | None = None,
        ap_active: bool | None = None,
    ) -> dict:
        """#15 아이템 판매가격 변경 (PUT, body 없음). 명세 02 §4.

        query: forceSalePriceUpdate(변경비율 -50%~+100% 제한 해제),
        apMinSalePrice/apActive(자동가격조정, 함께 전달·apMinSalePrice<price).
        query 조립은 item_price_query(Harness preview 공유). 10원단위 등은 쿠팡이 검증(D-1).
        """
        vid, prc = self._vid(vendor_item_id), self._vid(price)
        ap_min = self._vid(ap_min_sale_price) if ap_min_sale_price is not None else None
        params = item_price_query(
            force_sale_price_update=force_sale_price_update,
            ap_min_sale_price=ap_min,
            ap_active=ap_active,
        )
        resp = self._request(
            "PUT", item_price_path(vid, prc), params=params or None, retry_transient=False
        )
        return check_write_response(resp, f"가격변경(vii={vid})", require_code=True)

    def update_item_base_price(self, *, vendor_item_id: int, original_price: int) -> dict:
        """#16 아이템 할인율 기준가격 변경 (PUT, body 없음). 명세 02 §4."""
        vid, op = self._vid(vendor_item_id), self._vid(original_price)
        resp = self._request("PUT", item_base_price_path(vid, op), retry_transient=False)
        return check_write_response(resp, f"할인율기준가 변경(vii={vid})", require_code=True)

    def resume_item_sale(self, *, vendor_item_id: int) -> dict:
        """#17 아이템 판매 재개 (PUT, body 없음). 명세 02 §4."""
        vid = self._vid(vendor_item_id)
        resp = self._request("PUT", item_resume_path(vid), retry_transient=False)
        return check_write_response(resp, f"판매재개(vii={vid})", require_code=True)

    def stop_item_sale(self, *, vendor_item_id: int) -> dict:
        """#18 아이템 판매 중지 (PUT, body 없음). 명세 02 §4."""
        vid = self._vid(vendor_item_id)
        resp = self._request("PUT", item_stop_path(vid), retry_transient=False)
        return check_write_response(resp, f"판매중지(vii={vid})", require_code=True)

    def enable_auto_option_item(self, *, vendor_item_id: int) -> dict:
        """#19 자동생성옵션 활성화(옵션 단위) (POST, body 없음). PROCESSING 정상. 명세 02 §4."""
        vid = self._vid(vendor_item_id)
        resp = self._request("POST", auto_option_item_optin_path(vid), retry_transient=False)
        return check_write_response(
            resp, f"자동옵션 활성화(vii={vid})",
            success_codes=_AUTO_OPT_SUCCESS, require_code=True,
        )

    def enable_auto_option_all(self) -> dict:
        """#20 자동생성옵션 활성화(전체 단위) (POST, body 없음). 셀러 전체. PROCESSING 정상."""
        resp = self._request("POST", auto_option_seller_optin_path(), retry_transient=False)
        return check_write_response(
            resp, "자동옵션 활성화(전체)",
            success_codes=_AUTO_OPT_SUCCESS, require_code=True,
        )

    def disable_auto_option_item(self, *, vendor_item_id: int) -> dict:
        """#21 자동생성옵션 비활성화(옵션 단위) (POST, body 없음). PROCESSING 정상. 명세 02 §4."""
        vid = self._vid(vendor_item_id)
        resp = self._request("POST", auto_option_item_optout_path(vid), retry_transient=False)
        return check_write_response(
            resp, f"자동옵션 비활성화(vii={vid})",
            success_codes=_AUTO_OPT_SUCCESS, require_code=True,
        )

    def disable_auto_option_all(self) -> dict:
        """#22 자동생성옵션 비활성화(전체 단위) (POST, body 없음). 셀러 전체. PROCESSING 정상."""
        resp = self._request("POST", auto_option_seller_optout_path(), retry_transient=False)
        return check_write_response(
            resp, "자동옵션 비활성화(전체)",
            success_codes=_AUTO_OPT_SUCCESS, require_code=True,
        )
