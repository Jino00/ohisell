# logistics.py — 쿠팡 물류센터 도메인 SA (8개). 읽기 3개 구현, 쓰기 4개 stub(쓰기 페이즈).
# 명세: docs/references/08_coupang_logistics_api_specs.md (전수 디테일 2026-06-03, D-15).
# 용도: 출고지/반품지 관리(상품 생성·송장 처리 부속). 조망 직접 관련 낮음(D-7) — 온디맨드 조회.
# 호출은 서버 IP에서만(로컬 403, 트랙 D-8).
from __future__ import annotations

import logging
from collections.abc import Iterator

from app.clients.coupang._base import CoupangBaseClient, CoupangReadError

log = logging.getLogger(__name__)

_MP_BASE = "/v2/providers/marketplace_openapi/apis/api/v2/vendor"
_V5_BASE = "/v2/providers/openapi/apis/api/v5/vendors"
_V3_BASE = "/v2/providers/openapi/apis/api/v3/return"

# ── #8 택배사 코드표 (정적 상수, API 아님) ──────────────────────────────────────
# 명세: 08 §8. 송장업로드 시 사용. 오픽스는 한진(HANJIN) 고정.
COURIER_CODES: dict[str, str] = {
    "HANJIN": "한진택배",
    "CJGLS": "CJ대한통운",
    "KGB": "로젠택배",
    "EPOST": "우체국택배",
    "HYUNDAI": "롯데택배",
    "KDEXP": "경동택배",
    "ILYANG": "일양택배",
    "DIRECT": "업체직송(트래킹 없음)",
    "CHUNIL": "천일택배",
    "CVSNET": "GS편의점택배",
    "WIZWA": "위즈와",
}


class CoupangLogisticsClient(CoupangBaseClient):
    """쿠팡 물류센터 API 단일책임 래퍼. 각 메서드 = 1 엔드포인트(SA). raw data 반환, None=실패.

    조합/저장은 Harness가 담당. SA는 온디맨드 조회용 — DB 적재 없음(조망 직접 관련 낮음, D-7).
    하드 실패는 CoupangReadError로 표면화(원칙22·codex P1).
    """

    # ════════════════════════════════════════════════
    # 읽기 구현
    # ════════════════════════════════════════════════

    def list_outbound_places(
        self, *, page_num: int = 1, page_size: int = 50
    ) -> dict | None:
        """#1 출고지 목록 조회. marketplace_openapi 게이트웨이.

        Args:
            page_num: 1-based 페이지 번호(기본 1).
            page_size: 페이지당 건수(기본 50, 최대 50).
        Returns:
            {content:[{outboundShippingPlaceCode, ...}], totalPages, ...} 또는 None(실패).
        """
        path = f"{_MP_BASE}/shipping-place/outbound"
        resp = self._request(
            "GET",
            path,
            params={"pageNum": page_num, "pageSize": page_size},
        )
        if resp is None:
            raise CoupangReadError("출고지 목록 조회 실패(None 반환)")
        return resp.get("data") or resp

    def list_return_places(
        self, *, page_num: int = 1, page_size: int = 50
    ) -> dict | None:
        """#5 반품지 목록 조회. openapi v5 게이트웨이.

        Returns:
            {data:[{vendorId, returnCenterCode, ...}]} 또는 None(실패).
        """
        path = f"{_V5_BASE}/{self.vendor_id}/returnShippingCenters"
        resp = self._request(
            "GET",
            path,
            params={"pageNum": page_num, "pageSize": page_size},
        )
        if resp is None:
            raise CoupangReadError("반품지 목록 조회 실패(None 반환)")
        return resp.get("data") or resp

    def get_return_place(self, *, return_center_codes: list[str]) -> dict | None:
        """#6 반품지 단건(복수) 조회. 센터코드 최대 100개 콤마구분.

        Args:
            return_center_codes: returnCenterCode 목록.
        Returns:
            {data:[{vendorId, returnCenterCode, ...}]} 또는 None(실패).
        """
        if not return_center_codes:
            return None
        path = f"{_V3_BASE}/shipping-places/center-code"
        resp = self._request(
            "GET",
            path,
            params={"returnCenterCodes": ",".join(return_center_codes)},
        )
        if resp is None:
            raise CoupangReadError("반품지 단건 조회 실패(None 반환)")
        return resp.get("data") or resp

    # ════════════════════════════════════════════════
    # 쓰기 stub — 쓰기 페이즈에서 dry_run + 본문스키마 재확인(D-1)
    # ════════════════════════════════════════════════

    def create_outbound_place(self, *, body: dict) -> dict:
        """#2 출고지 생성 — stub(쓰기 페이즈). dry_run 게이트 필수(D-1)."""
        raise NotImplementedError("쓰기 페이즈 — 출고지 생성 미구현(D-1 dry_run 필요)")

    def update_outbound_place(self, *, code: str, body: dict) -> dict:
        """#3 출고지 수정 — stub(쓰기 페이즈)."""
        raise NotImplementedError("쓰기 페이즈 — 출고지 수정 미구현(D-1 dry_run 필요)")

    def create_return_place(self, *, body: dict) -> dict:
        """#4 반품지 생성 — stub(쓰기 페이즈)."""
        raise NotImplementedError("쓰기 페이즈 — 반품지 생성 미구현(D-1 dry_run 필요)")

    def update_return_place(self, *, return_center_code: str, body: dict) -> dict:
        """#7 반품지 수정 — stub(쓰기 페이즈)."""
        raise NotImplementedError("쓰기 페이즈 — 반품지 수정 미구현(D-1 dry_run 필요)")
