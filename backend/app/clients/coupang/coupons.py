# coupons.py — 쿠팡 쿠폰/캐시백 도메인 SA (21개). 읽기 13개 구현, 쓰기 8개 stub(쓰기 페이즈 dry_run).
# 명세: docs/references/06_coupang_coupon_api_specs.md §E (응답 스키마 전수 수집 2026-06-03, P5).
# 트랙 D-8: vendorItemId는 vendor_id 귀속 → 계정별 클라이언트. 호출은 서버 IP에서만(로컬 403).
# 게이트웨이 3종(서명은 _base HMAC 동일):
#   - fms: 예산·계약·즉시할인쿠폰 → 응답 {code, message, data:{success, content, pagination}}
#   - marketplace_openapi: 다운로드쿠폰 → 직접 반환(code 래핑 없음, settlement-histories 패턴)
#   - openapi: [도서]캐시백(오픽스 무관, D-7 자리만)
from __future__ import annotations

import logging
from collections.abc import Iterator

from app.clients.coupang._base import CoupangBaseClient, CoupangReadError

log = logging.getLogger(__name__)

_FMS_BASE = "/v2/providers/fms/apis/api"
_MP_BASE = "/v2/providers/marketplace_openapi/apis/api/v1"
_OPENAPI_BASE = "/v2/providers/openapi/apis/api/v4"


def _fms_ok(resp: dict | None) -> bool:
    """fms 응답 성공 판정. {code:200, data:{success:true, content, pagination}}.

    codex[P1]: code만 보면 {code:200, data:{success:false}}를 성공으로 위장 →
    빈 content로 iterator 조용히 종료 = stale 생존(원칙22). data.success가 명시적
    False면 실패로 본다(success 필드 부재 시엔 통과 — 호환).
    """
    if not resp or str(resp.get("code")) not in ("200", "SUCCESS"):
        return False
    data = resp.get("data")
    if isinstance(data, dict) and data.get("success") is False:
        return False
    return True


class CoupangCouponClient(CoupangBaseClient):
    """쿠팡 쿠폰/캐시백 API 단일책임 래퍼. 각 메서드 = 1 엔드포인트(SA). raw data 반환, None=실패.

    조합/저장(쿠폰 목록·아이템·예산 적재)은 Harness(services/coupang/coupon_sync.py)가 담당한다.
    SA는 다른 SA를 모른다(원칙 18-6). 하드 실패는 CoupangReadError로 표면화(원칙22·codex P1).
    """

    # ════════════════════════════════════════════════
    # B. (공통) 예산/계약서 — fms (읽기 구현)
    # ════════════════════════════════════════════════

    # ── #4 예산현황 조회 ──
    def get_budgets(
        self, *, contract_id: str | int | None = None, target_month: str | None = None
    ) -> dict | None:
        """예산현황 조회 (명세 §E #4). contractId(자유계약 -1), targetMonth(yyyy-MM, 생략=현재월).

        반환: {code, data:{success, content:[{contractId, targetMonth, vendorShareRatio,
        totalBudgetAmount, usedBudgetAmount}], pagination}} 또는 None.
        """
        params: dict = {"vendorId": self.vendor_id}
        if contract_id is not None:
            params["contractId"] = contract_id
        if target_month:
            params["targetMonth"] = target_month
        return self._request("GET", f"{_FMS_BASE}/v1/vendors/{self.vendor_id}/budgets", params)

    # ── #5 계약서 단건 조회 ──
    def get_contract(self, *, contract_id: str | int) -> dict | None:
        """계약서 단건 조회 (명세 §E #5). 반환: data.content(Object) 또는 None."""
        params = {"vendorId": self.vendor_id, "contractId": contract_id}
        return self._request("GET", f"{_FMS_BASE}/v2/vendors/{self.vendor_id}/contract", params)

    # ── #6 계약서 목록 조회 ──
    def get_contract_list(self) -> dict | None:
        """계약서 목록 조회 (명세 §E #6). 반환: data.content[]{contractId, sellerShareRatio,
        coupangShareRatio, start, end, type, ...} 또는 None.
        """
        params = {"vendorId": self.vendor_id}
        return self._request("GET", f"{_FMS_BASE}/v2/vendors/{self.vendor_id}/contract/list", params)

    # ════════════════════════════════════════════════
    # D. [즉시할인쿠폰] — fms (읽기 구현)
    # ════════════════════════════════════════════════

    # ── #18 목록 조회(status) ──
    def get_instant_coupons(
        self, *, status: str, page: int = 1, size: int = 100, sort: str = "desc"
    ) -> dict | None:
        """즉시할인쿠폰 목록 조회 (명세 §E #18). status 필수(STANDBY/APPLIED/PAUSED/EXPIRED/DETACHED).

        page 1-based(기본1). 반환: data.content[]{couponId, status, type, discount,
        maxDiscountPrice, startAt, endAt, promotionName, contractId, wowExclusive} + pagination.
        """
        params = {
            "vendorId": self.vendor_id,
            "status": status,
            "page": page,
            "size": size,
            "sort": sort,
        }
        return self._request(
            "GET", f"{_FMS_BASE}/v2/vendors/{self.vendor_id}/coupons", params
        )

    def iter_instant_coupons(self, *, status: str, size: int = 100) -> Iterator[dict]:
        """즉시할인쿠폰 목록을 page 페이징으로 전부 순회(제너레이터). 각 쿠폰 dict yield.

        하드 실패는 CoupangReadError로 표면화(stale 위장 금지, 원칙22). page 1-based.
        """
        page = 1
        while True:
            resp = self.get_instant_coupons(status=status, page=page, size=size)
            if resp is None:
                raise CoupangReadError(
                    f"즉시할인쿠폰 목록 읽기 실패(None): {self.vendor_id} status={status}"
                )
            if not _fms_ok(resp):
                raise CoupangReadError(
                    f"즉시할인쿠폰 목록 API 에러 code={resp.get('code')} "
                    f"msg={resp.get('message')}"
                )
            data = resp.get("data") or {}
            content = data.get("content") or []
            for coupon in content:
                yield coupon
            pg = data.get("pagination") or {}
            cur = int(pg.get("currentPage") or page)
            total_pages = int(pg.get("totalPages") or 0)
            if not content or cur >= total_pages or total_pages <= 0:
                break
            page = cur + 1

    # ── #15 단건 조회(couponId) ──
    def get_instant_coupon(self, *, coupon_id: str | int) -> dict | None:
        """즉시할인쿠폰 단건 조회 (명세 §E #15). 반환: data.content(Object) 또는 None."""
        params = {"vendorId": self.vendor_id, "couponId": coupon_id}
        return self._request("GET", f"{_FMS_BASE}/v2/vendors/{self.vendor_id}/coupon", params)

    # ── #20 아이템 목록 조회(status) — 옵션 결합(D-8) ──
    def get_instant_coupon_items(
        self, *, coupon_id: str | int, status: str, page: int = 0, size: int = 100,
        sort: str = "desc",
    ) -> dict | None:
        """즉시할인쿠폰 아이템 목록 조회 (명세 §E #20). ★page 기본 0(0-based, #18과 다름).

        반환: data.content[]{couponItemId, couponId, vendorItemId, startAt, endAt, status}.
        """
        params = {
            "status": status,
            "page": page,
            "size": size,
            "sort": sort,
        }
        return self._request(
            "GET",
            f"{_FMS_BASE}/v1/vendors/{self.vendor_id}/coupons/{coupon_id}/items",
            params,
        )

    def iter_instant_coupon_items(
        self, *, coupon_id: str | int, status: str, size: int = 100
    ) -> Iterator[dict]:
        """즉시할인쿠폰 아이템 목록을 page 페이징으로 순회(제너레이터). ★page 0-based.

        하드 실패는 CoupangReadError로 표면화(원칙22).
        """
        page = 0
        while True:
            resp = self.get_instant_coupon_items(
                coupon_id=coupon_id, status=status, page=page, size=size
            )
            if resp is None:
                raise CoupangReadError(
                    f"쿠폰아이템 목록 읽기 실패(None): coupon={coupon_id} status={status}"
                )
            if not _fms_ok(resp):
                raise CoupangReadError(
                    f"쿠폰아이템 목록 API 에러 code={resp.get('code')} "
                    f"msg={resp.get('message')}"
                )
            data = resp.get("data") or {}
            content = data.get("content") or []
            for item in content:
                yield item
            pg = data.get("pagination") or {}
            total_pages = int(pg.get("totalPages") or 0)
            # page는 0-based — currentPage 의미가 응답마다 다를 수 있어 길이/총페이지로 종료 판정.
            if not content or total_pages <= 0 or (page + 1) >= total_pages:
                break
            page += 1

    # ── #16 단건 조회(couponItemId) ──
    def get_instant_coupon_item_by_item_id(
        self, *, coupon_id: str | int, coupon_item_id: str | int
    ) -> dict | None:
        """쿠폰아이템 단건 조회(couponItemId) (명세 §E #16). 반환: data.content(Object) 또는 None."""
        params = {"type": "couponItemId"}
        return self._request(
            "GET",
            f"{_FMS_BASE}/v1/vendors/{self.vendor_id}/coupons/{coupon_id}/items/{coupon_item_id}",
            params,
        )

    # ── #17 단건 조회(vendorItemId) — 옵션별 결합(D-8) ──
    def get_instant_coupon_item_by_vendor_item(
        self, *, coupon_id: str | int, vendor_item_id: str | int
    ) -> dict | None:
        """쿠폰아이템 단건 조회(vendorItemId) (명세 §E #17). 옵션ID별 쿠폰 적용 여부.

        반환: data.content{couponItemId, couponId, vendorItemId, startAt, endAt, status} 또는 None.
        """
        params = {"type": "vendorItemId"}
        return self._request(
            "GET",
            f"{_FMS_BASE}/v1/vendors/{self.vendor_id}/coupons/{coupon_id}/items/{vendor_item_id}",
            params,
        )

    # ── #19 목록 조회(orderId) — 주문별 적용 쿠폰 ──
    def get_instant_coupons_by_order(self, *, order_id: str | int) -> dict | None:
        """주문별 적용 쿠폰 목록 조회 (명세 §E #19). 반환: data.content[]{couponId, discount, ...}."""
        return self._request(
            "GET", f"{_FMS_BASE}/v2/vendors/{self.vendor_id}/{order_id}/coupons", None
        )

    # ── #21 요청상태 확인 ──
    def get_instant_coupon_request_status(self, *, requested_id: str | int) -> dict | None:
        """즉시할인쿠폰 요청상태 확인 (명세 §E #21). 비동기 쓰기 결과 폴링용.

        반환: data.content{couponId, requestedId, status(REQUESTED/FAIL/DONE), succeeded,
        total, type, failed, failedVendorItems[]} 또는 None.
        """
        return self._request(
            "GET", f"{_FMS_BASE}/v1/vendors/{self.vendor_id}/requested/{requested_id}", None
        )

    # ════════════════════════════════════════════════
    # C. [다운로드쿠폰] — marketplace_openapi (읽기 구현, 직접 반환)
    # ════════════════════════════════════════════════
    # ⚠️ 목록 조회 API 없음 → couponId를 알아야 단건 조회 가능(명세 §E 제약). 자동 sync 불가.

    # ── #10 단건 조회(couponId) ──
    def get_download_coupon(self, *, coupon_id: str | int) -> dict | None:
        """다운로드쿠폰 단건 조회 (명세 §E #10). ★직접 Object 반환(code 래핑 없음).

        반환: {couponId, title, couponType, couponStatus, startDate, endDate, appliedOptionCount,
        usageAmount, couponPolicies[]{...}} 또는 None(실패).
        """
        return self._request("GET", f"{_MP_BASE}/coupons/{coupon_id}", None)

    # ── #11 요청상태 확인 ──
    def get_download_coupon_status(self, *, request_transaction_id: str) -> dict | None:
        """다운로드쿠폰 요청상태 확인 (명세 §E #11). ★직접 Object 반환.

        반환: {transactionStatusResponse{type, total, succeeded, status, requestedId,
        couponFailedVendorItemIdResponses[], failed, couponId}} 또는 None.
        """
        params = {"requestTransactionId": request_transaction_id}
        return self._request("GET", f"{_MP_BASE}/coupons/transactionStatus", params)

    # ════════════════════════════════════════════════
    # A. [도서] 상품 캐시백 — openapi (오픽스 무관, D-7 자리만 — 읽기만 구현)
    # ════════════════════════════════════════════════

    # ── #2 캐시백 검색 ──
    def search_book_cashback(self) -> dict | None:
        """[도서] 상품 캐시백 검색 (명세 §E #2). 오픽스(휴대폰 액세서리) 무관 — 호환용 구현."""
        return self._request(
            "GET",
            f"{_OPENAPI_BASE}/vendors/{self.vendor_id}/products/items/cashback",
            None,
        )

    # ════════════════════════════════════════════════
    # 쓰기 8개 — stub (쓰기 페이즈에서 product_write.py 패턴 + dry_run 구현, D-1)
    # ════════════════════════════════════════════════
    # ⚠️ 쓰기는 라이브 스토어 변경 → 쓰기 페이즈에서 dry_run 기본 + 명시 확인 게이트(트랙 §5).
    #   본문 스키마는 구현 시점 각 article 재확인(추정 금지). 여기서는 시그니처만 예약.

    def apply_book_cashback(self, **kwargs):  # #1 [도서]캐시백 적용 POST
        raise NotImplementedError("쓰기 페이즈(dry_run) — coupons #1 도서캐시백 적용")

    def delete_book_cashback(self, **kwargs):  # #3 [도서]캐시백 삭제 DELETE
        raise NotImplementedError("쓰기 페이즈(dry_run) — coupons #3 도서캐시백 삭제")

    def create_download_coupon(self, **kwargs):  # #7 다운로드쿠폰 생성 POST
        raise NotImplementedError("쓰기 페이즈(dry_run) — coupons #7 다운로드쿠폰 생성")

    def create_download_coupon_items(self, **kwargs):  # #8 다운로드쿠폰 아이템 생성 PUT
        raise NotImplementedError("쓰기 페이즈(dry_run) — coupons #8 다운로드쿠폰 아이템 생성")

    def expire_download_coupon(self, **kwargs):  # #9 다운로드쿠폰 파기 POST
        raise NotImplementedError("쓰기 페이즈(dry_run) — coupons #9 다운로드쿠폰 파기")

    def create_instant_coupon(self, **kwargs):  # #12 즉시할인쿠폰 생성 POST
        raise NotImplementedError("쓰기 페이즈(dry_run) — coupons #12 즉시할인쿠폰 생성")

    def create_instant_coupon_items(self, **kwargs):  # #13 즉시할인쿠폰 아이템 생성 POST
        raise NotImplementedError("쓰기 페이즈(dry_run) — coupons #13 즉시할인쿠폰 아이템 생성")

    def expire_instant_coupon(self, **kwargs):  # #14 즉시할인쿠폰 파기 PUT
        raise NotImplementedError("쓰기 페이즈(dry_run) — coupons #14 즉시할인쿠폰 파기")
