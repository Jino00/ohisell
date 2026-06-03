# category.py — 쿠팡 카테고리 도메인 SA (6개) + RG 카테고리 2 stub.
# 명세: docs/references/09_coupang_category_api_specs.md (전수 디테일 2026-06-03, D-15).
# 용도: 상품 생성 메타·D-13 카테고리율 2차 교차·RG 카테고리(P3 D-14 stub #8·#9 본구현).
# ⚠️ 카테고리 판매수수료율은 이 API에 없음(09 §구현메모) — 정적 상수 참고(constants/coupang_fee_rates.py).
# 호출은 서버 IP에서만(로컬 403, 트랙 D-8).
from __future__ import annotations

import logging

from app.clients.coupang._base import CoupangBaseClient, CoupangReadError

log = logging.getLogger(__name__)

_SELLER_BASE = "/v2/providers/seller_api/apis/api/v1/marketplace"
_OPENAPI_BASE = "/v2/providers/openapi/apis/api/v1"
# RG 카테고리(#8·#9)는 seller_api 게이트웨이 — rg_open_api 아님(references/05 §27 확인).
# #8 = category-related-metas/{code}, #9 = display-categories?registrationType=RFM.
# registrationType=RFM 파라미터만 일반 카테고리와 다름(path 자체는 동일).


class CoupangCategoryClient(CoupangBaseClient):
    """쿠팡 카테고리 API 단일책임 래퍼. 각 메서드 = 1 엔드포인트(SA). raw data 반환, None=실패.

    DB 적재 없음(온디맨드 조회 — 트리는 변동 적어 호출측이 캐싱).
    RG 카테고리(#8·#9)는 P3 D-14 stub을 여기서 본구현.
    """

    # ════════════════════════════════════════════════
    # 일반 카테고리 (seller_api 게이트웨이)
    # ════════════════════════════════════════════════

    def list_categories(self) -> list | None:
        """#1 카테고리 목록(전체 트리) 조회.

        Returns:
            [{displayCategoryCode, name, status, child:[...재귀]}] 또는 None(실패).
        """
        path = f"{_SELLER_BASE}/meta/display-categories"
        resp = self._request("GET", path)
        if resp is None:
            raise CoupangReadError("카테고리 목록 조회 실패(None 반환)")
        data = resp.get("data")
        if data is None and str(resp.get("code", "")) not in ("SUCCESS", "200"):
            raise CoupangReadError(f"카테고리 목록 API 오류: {resp.get('message')}")
        return data

    def get_category(self, display_category_code: str | int) -> dict | None:
        """#2 카테고리 단건 조회 (1 Depth 하위 포함). code=0이면 최상위 1depth.

        Returns:
            {displayCategoryCode, name, status, child} 또는 None(실패).
        """
        path = f"{_SELLER_BASE}/meta/display-categories/{display_category_code}"
        resp = self._request("GET", path)
        if resp is None:
            raise CoupangReadError(f"카테고리 단건 조회 실패({display_category_code})")
        return resp.get("data")

    def check_category_status(self, display_category_code: str | int) -> bool | None:
        """#3 카테고리 유효성 검사 (leaf·사용가능 여부).

        Returns:
            True(사용가능) / False(불가) / None(실패).
        """
        path = f"{_SELLER_BASE}/meta/display-categories/{display_category_code}/status"
        resp = self._request("GET", path)
        if resp is None:
            raise CoupangReadError(f"카테고리 유효성 검사 실패({display_category_code})")
        return resp.get("data")

    def get_category_meta(self, display_category_code: str | int) -> list | None:
        """#4 카테고리 메타정보 조회 (★상품생성 필수 — 고시정보·구매옵션·구비서류·인증).

        Returns:
            [{isAllowSingleItem, attributes[], notices[], requiredDocuments[], certifications[]}]
            또는 None(실패).
        """
        path = (
            f"{_SELLER_BASE}/meta/category-related-metas"
            f"/display-category-codes/{display_category_code}"
        )
        resp = self._request("GET", path)
        if resp is None:
            raise CoupangReadError(f"카테고리 메타정보 조회 실패({display_category_code})")
        return resp.get("data")

    def predict_category(
        self,
        *,
        product_name: str,
        product_description: str | None = None,
        brand: str | None = None,
        attributes: dict | None = None,
    ) -> dict | None:
        """#5 카테고리 추천 (상품명 → 추천 카테고리). openapi 게이트웨이.

        Returns:
            {autoCategorizationPredictionResultType, predictedCategoryId, predictedCategoryName, ...}
            또는 None(실패).
        """
        path = f"{_OPENAPI_BASE}/categorization/predict"
        body: dict = {"productName": product_name}
        if product_description:
            body["productDescription"] = product_description
        if brand:
            body["brand"] = brand
        if attributes:
            body["attributes"] = attributes
        resp = self._request("POST", path, body=body)
        if resp is None:
            raise CoupangReadError("카테고리 추천 실패(None 반환)")
        return resp.get("data") or resp

    def check_auto_category_agreed(self, vendor_id: str | None = None) -> bool | None:
        """#6 카테고리 자동매칭 동의 확인.

        Args:
            vendor_id: 업체코드(기본값: self.vendor_id).
        Returns:
            True/False 또는 None(실패).
        """
        vid = vendor_id or self.vendor_id
        path = f"{_SELLER_BASE}/vendors/{vid}/check-auto-category-agreed"
        resp = self._request("GET", path)
        if resp is None:
            raise CoupangReadError("카테고리 자동매칭 동의 확인 실패(None 반환)")
        return resp.get("data")

    # ════════════════════════════════════════════════
    # RG 카테고리 — rg_open_api 게이트웨이 (P3 D-14 stub #8·#9 본구현)
    # ════════════════════════════════════════════════

    def get_rg_category_meta(self, display_category_code: str | int) -> dict | None:
        """#8 RG 카테고리 메타 조회 (P3 D-14 stub 본구현).

        seller_api 게이트웨이 — category-related-metas와 동일 path.
        references/05 §27: #8·#9는 seller_api(rg_open_api 아님).
        Returns:
            카테고리 메타 dict 또는 None(실패).
        """
        path = (
            f"{_SELLER_BASE}/meta/category-related-metas"
            f"/display-category-codes/{display_category_code}"
        )
        resp = self._request("GET", path)
        if resp is None:
            raise CoupangReadError(f"RG 카테고리 메타 조회 실패({display_category_code})")
        return resp.get("data")

    def list_rg_categories(
        self, *, display_category_code: str | None = None
    ) -> list | None:
        """#9 RG 카테고리 목록 조회 (P3 D-14 stub 본구현). registrationType=RFM 고정.

        seller_api 게이트웨이, registrationType=RFM 파라미터로 RG 구분.
        references/05 §27·references/09 #1 공유 path.
        Args:
            display_category_code: 상위 카테고리 코드(None=최상위).
        Returns:
            카테고리 목록 또는 None(실패).
        """
        path = f"{_SELLER_BASE}/meta/display-categories"
        params: dict = {"registrationType": "RFM"}
        if display_category_code:
            params["displayCategoryCode"] = display_category_code
        resp = self._request("GET", path, params=params)
        if resp is None:
            raise CoupangReadError("RG 카테고리 목록 조회 실패(None 반환)")
        return resp.get("data")
