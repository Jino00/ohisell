# logistics.py — 쿠팡 물류센터 도메인 SA (8개). 읽기 3개 구현, 쓰기 4개 stub(쓰기 페이즈).
# 명세: docs/references/08_coupang_logistics_api_specs.md (전수 디테일 2026-06-03, D-15).
# 용도: 출고지/반품지 관리(상품 생성·송장 처리 부속). 조망 직접 관련 낮음(D-7) — 온디맨드 조회.
# 호출은 서버 IP에서만(로컬 403, 트랙 D-8).
from __future__ import annotations

import logging
from collections.abc import Iterator
from urllib.parse import quote

from app.clients.coupang._base import (
    CoupangBaseClient,
    CoupangReadError,
    CoupangWriteError,
    check_write_response,
)

log = logging.getLogger(__name__)

_MP_BASE = "/v2/providers/marketplace_openapi/apis/api/v2/vendor"
_V5_BASE = "/v2/providers/openapi/apis/api/v5/vendors"
_V3_BASE = "/v2/providers/openapi/apis/api/v3/return"


# ── 쓰기 경로 빌더 (단일 정의 — SA 실행 + Harness dry-run 미리보기 공유, 트랙 D-16) ──
def outbound_create_path(vendor_id: str) -> str:
    """#2 출고지 생성 경로."""
    return f"{_V5_BASE}/{vendor_id}/outboundShippingCenters"


def outbound_update_path(vendor_id: str, code: str) -> str:
    """#3 출고지 수정 경로(outboundShippingPlaceCode). codex[P2]: path segment quote."""
    return f"{_V5_BASE}/{vendor_id}/outboundShippingCenters/{quote(str(code), safe='')}"


def return_create_path(vendor_id: str) -> str:
    """#4 반품지 생성 경로."""
    return f"{_V5_BASE}/{vendor_id}/returnShippingCenters"


def return_update_path(vendor_id: str, return_center_code: str) -> str:
    """#7 반품지 수정 경로(returnCenterCode). codex[P2]: path segment quote."""
    return f"{_V5_BASE}/{vendor_id}/returnShippingCenters/{quote(str(return_center_code), safe='')}"

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
    # 쓰기 구현 (트랙 D-16, W1) — ⚠️ 라이브 실행자(live executor).
    # ⚠️ 직접 호출 금지. 반드시 Harness(services/coupang/logistics_ops.py)를 경유할 것.
    #    아래 메서드는 dry_run/confirm을 모른다(원칙18-1 단일책임) — 호출 즉시 라이브 변경한다.
    #    dry_run 게이트·confirm 토큰은 Harness(_write_guard)가 담당(트랙 §5 확정 아키텍처).
    #    codex[P1] 절충: 게이트는 Harness에 두고(전 도메인 SA 패턴 일관), SA는 경고로 방어.
    # SA는 완성된 body를 받아 vendorId 강제 후 POST/PUT. 실패는 CoupangWriteError로 표면화(원칙22).
    # 본문 필드 검증은 쿠팡이 수행(추정으로 필드 강제 안 함 — D-1). 명세 08 §2·3·4·7.
    # ════════════════════════════════════════════════

    def _inject_vendor(self, body: dict) -> dict:
        """body의 vendorId를 자기 계정으로 강제(경로가 진실). 원본 불변(복사본 반환).

        codex[P1]: setdefault면 호출자가 다른 vendorId를 넣었을 때 경로(account.vendor_id)와
        본문이 불일치 → 계정 혼선·dry-run 미리보기 신뢰 붕괴. 무조건 덮어쓴다(D-8: vendorItemId는
        계정 단일소유이므로 본문 vendorId는 항상 자기 계정이어야 함).
        """
        payload = dict(body)
        payload["vendorId"] = self.vendor_id
        return payload

    def create_outbound_place(self, *, body: dict) -> dict:
        """#2 출고지 생성 (POST). vendorId 자동 주입. 명세 08 §2.

        Args:
            body: {userId(O), shippingPlaceName(O 최대50자·중복불가), usable, 주소/택배정보, ...}.
        Returns:
            쿠팡 응답 dict.
        Raises:
            CoupangWriteError: 생성 실패(None 반환 — 4xx/5xx·네트워크).
        """
        payload = self._inject_vendor(body)
        resp = self._request(
            "POST", outbound_create_path(self.vendor_id), body=payload, retry_transient=False
        )
        return check_write_response(resp, "출고지 생성")

    def update_outbound_place(self, *, code: str, body: dict) -> dict:
        """#3 출고지 수정 (PUT). null 필드는 기존값 유지. 명세 08 §3.

        Args:
            code: outboundShippingPlaceCode.
            body: 수정 필드(null이면 기존값 유지).
        Raises:
            CoupangWriteError: 수정 실패.
        """
        payload = self._inject_vendor(body)
        resp = self._request(
            "PUT", outbound_update_path(self.vendor_id, code), body=payload, retry_transient=False
        )
        return check_write_response(resp, f"출고지 수정(code={code})")

    def create_return_place(self, *, body: dict) -> dict:
        """#4 반품지 생성 (POST). vendorId 자동 주입. 명세 08 §4.

        Args:
            body: {userId(O), shippingPlaceName(O), goodsflowInfoOpenApiDto(O 택배사정보), ...}.
        Raises:
            CoupangWriteError: 생성 실패.
        """
        payload = self._inject_vendor(body)
        resp = self._request(
            "POST", return_create_path(self.vendor_id), body=payload, retry_transient=False
        )
        return check_write_response(resp, "반품지 생성")

    def update_return_place(self, *, return_center_code: str, body: dict) -> dict:
        """#7 반품지 수정 (PUT). null 필드는 기존값 유지. 명세 08 §7.

        Args:
            return_center_code: returnCenterCode.
            body: {userId(O), shippingPlaceName(null이면 유지), usable, ...}.
        Raises:
            CoupangWriteError: 수정 실패.
        """
        payload = self._inject_vendor(body)
        payload["returnCenterCode"] = return_center_code  # 경로가 진실(codex[P1])
        resp = self._request(
            "PUT", return_update_path(self.vendor_id, return_center_code),
            body=payload, retry_transient=False,
        )
        return check_write_response(resp, f"반품지 수정(code={return_center_code})")
