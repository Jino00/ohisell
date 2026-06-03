# brand.py — 쿠팡 브랜드 도메인 SA (3개). 전부 읽기 구현.
# 명세: docs/references/11_coupang_brand_api_specs.md (전수 디테일 2026-06-03, D-15).
# 용도: 상품 생성(쓰기 페이즈)의 브랜드·UID 필수속성 조회(2026-05 강화). 조망 직접 관련 낮음(D-7).
# 호출은 서버 IP에서만(로컬 403, 트랙 D-8).
from __future__ import annotations

import logging

from app.clients.coupang._base import CoupangBaseClient, CoupangReadError

log = logging.getLogger(__name__)

_SELLER_BASE = "/v2/providers/seller_api/apis/api/v1/marketplace"


class CoupangBrandClient(CoupangBaseClient):
    """쿠팡 브랜드 API 단일책임 래퍼. 각 메서드 = 1 엔드포인트(SA). raw data 반환, None=실패.

    DB 적재 없음(온디맨드 조회). 상품 생성 쓰기 페이즈에서 brandId 조회 용도.
    """

    def search_brands(self, *, brand_name: str) -> dict | None:
        """#1 브랜드 검색 (brandName으로 검색). POST.

        Returns:
            {page, countPerPage, totalCount, items:[{brandId, brandName, brandLogoUrl,
             isUIDRequired, allowedUIDTypes}]} 또는 None(실패).
        """
        path = f"{_SELLER_BASE}/brands/search"
        resp = self._request("POST", path, body={"brandName": brand_name})
        if resp is None:
            raise CoupangReadError(f"브랜드 검색 실패({brand_name!r})")
        data = resp.get("data")
        if data is None and str(resp.get("code", "")) not in ("SUCCESS", "200"):
            raise CoupangReadError(f"브랜드 검색 API 오류: {resp.get('message')}")
        return data

    def list_enrolled_brands(self) -> list | None:
        """#2 등록 브랜드 목록 조회. GET.

        Returns:
            [{brandId, brandName}] 또는 None(실패).
        """
        path = f"{_SELLER_BASE}/brands/enrolled"
        resp = self._request("GET", path)
        if resp is None:
            raise CoupangReadError("등록 브랜드 목록 조회 실패(None 반환)")
        return resp.get("data") or resp

    def get_brand(self, *, brand_id: str) -> dict | None:
        """#3 ID 기반 브랜드 단건 조회. GET /brands/{brandId}.

        Args:
            brand_id: 브랜드 ID (예: 'KR-5').
        Returns:
            {brandId, brandName, brandLogoUrl, isUIDRequired, allowedUIDTypes} 또는 None(실패).
        """
        path = f"{_SELLER_BASE}/brands/{brand_id}"
        resp = self._request("GET", path)
        if resp is None:
            raise CoupangReadError(f"브랜드 단건 조회 실패({brand_id!r})")
        data = resp.get("data")
        if data is None and str(resp.get("code", "")) not in ("SUCCESS", "200"):
            raise CoupangReadError(f"브랜드 조회 API 오류: {resp.get('message')}")
        return data
