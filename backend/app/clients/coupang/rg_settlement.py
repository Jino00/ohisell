# rg_settlement.py — Wing 내부 RG 정산현황 API 세션쿠키 클라이언트 (트랙 RG-Fee-Accounting S1)
# ⚠️ HMAC 베이스(_base) 미상속 — Wing 내부 API는 셀러 로그인 '세션쿠키' 인증(inbound.py 패턴 계승).
# S0 라이브 실측(2026-06-09): POST /tenants/rfm/v2/settlements/status/api
#   body: {"startDate":"2026-06-01T15:00:00.000Z","endDate":"...","searchDateType":"SALES"|"PAYMENT"}
#   searchDateType: "SALES"=매출인식일(D-10), "PAYMENT"=정산일. 날짜=KST 00:00→UTC T15:00:00.000Z.
# D-10: 매출인식일 기준 → searchDateType="SALES". D-13: 방어적 파싱+스키마 드리프트 감지.
from __future__ import annotations

import logging
from typing import Any

import requests

from app.clients.coupang.inbound import WingAuthError, WingReadError, _REDIRECT_CODES, _UA

log = logging.getLogger(__name__)

WING_SETTLEMENT_STATUS_URL = (
    "https://wing.coupang.com/tenants/rfm/v2/settlements/status/api"
)
WING_SETTLEMENT_PROFIT_URL = (
    "https://wing.coupang.com/tenants/rfm/v2/settlements/profit-status/search"
)
WING_SETTLEMENT_DOWNLOAD_URL = (
    "https://wing.coupang.com/tenants/rfm/v2/settlements/download-list/api"
)
WING_REFERER = "https://wing.coupang.com/tenants/rfm/settlements/status-new"
# searchDateType 상수 (S0 실측 확정)
_SEARCH_DATE_SALES = "SALES"     # 매출인식일 — D-10 기본값
_SEARCH_DATE_PAYMENT = "PAYMENT"  # 정산일


class CoupangWingRgSettlementClient:
    """Wing 내부 RG 정산현황 API 단일책임 래퍼. 세션쿠키+xsrf로 정산 데이터 조회. raw dict 반환.

    파싱·적재·검산은 Harness(rg_settlement_sync.py)가 담당(원칙 18-6/D-8).
    인증 실패(302/401/403) → WingAuthError(inbound 패턴 동일). 그 외 실패 → WingReadError.
    D-10: recognitionDateFrom/To = 매출인식일 기준.
    D-13: 방어적 파싱 — 키 부재·타입 변화 fail-soft, 응답 형태 변화 감지.
    """

    def __init__(self, cookie_header: str, xsrf_token: str):
        self.cookie = cookie_header
        self.xsrf = xsrf_token

    def _headers(self) -> dict[str, str]:
        return {
            "x-xsrf-token": self.xsrf,
            "user-agent": _UA,
            "referer": WING_REFERER,
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "cookie": self.cookie,
        }

    def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST 공통 실행. 302→WingAuthError, 4xx/5xx/JSON실패→WingReadError."""
        try:
            resp = requests.post(
                url, json=body, headers=self._headers(),
                timeout=30, allow_redirects=False,
            )
        except requests.exceptions.RequestException as e:
            raise WingReadError(f"Wing RG 정산 API 요청 에러: {e}") from e
        if resp.status_code in _REDIRECT_CODES:
            raise WingAuthError(f"Wing 세션쿠키 만료(리다이렉트 {resp.status_code})")
        if resp.status_code in (401, 403):
            raise WingAuthError(f"Wing 인증 실패(HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise WingReadError(f"Wing RG 정산 API HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError as e:
            raise WingReadError(f"Wing RG 정산 API 응답 JSON 파싱 실패: {e}") from e
        if not isinstance(data, dict):
            raise WingReadError(
                f"Wing RG 정산 API 응답이 dict 아님: {type(data).__name__}(스키마 드리프트 의심)"
            )
        return data

    @staticmethod
    def _kst_date_to_utc_iso(kst_date: str) -> str:
        """KST "YYYY-MM-DD" → UTC ISO "YYYY-MM-DDT15:00:00.000Z" (KST 00:00 = UTC T15:00).

        S0 실측: Wing API는 UTC ISO 형식으로 날짜 경계를 전달받음.
        """
        return f"{kst_date}T15:00:00.000Z"

    def get_settlement_status(
        self,
        *,
        start_date: str,
        end_date: str,
        search_date_type: str = _SEARCH_DATE_SALES,
    ) -> dict[str, Any]:
        """주별 정산리포트 조회(status/api). D-10: 매출인식일 기준(searchDateType="SALES").

        start_date/end_date: KST "YYYY-MM-DD" 형식. 내부에서 UTC ISO 변환.
        반환: 원본 dict. 구조 미확정 — Harness가 방어적으로 파싱(D-13).
        """
        body = {
            "startDate": self._kst_date_to_utc_iso(start_date),
            "endDate": self._kst_date_to_utc_iso(end_date),
            "searchDateType": search_date_type,
        }
        return self._post(WING_SETTLEMENT_STATUS_URL, body)

    def get_profit_status(
        self,
        *,
        start_date: str,
        end_date: str,
        search_date_type: str = _SEARCH_DATE_SALES,
    ) -> dict[str, Any]:
        """기간 정산 요약(profit-status/search). 검산 기준선용(D-2).

        start_date/end_date: KST "YYYY-MM-DD" 형식.
        """
        body = {
            "startDate": self._kst_date_to_utc_iso(start_date),
            "endDate": self._kst_date_to_utc_iso(end_date),
            "searchDateType": search_date_type,
        }
        return self._post(WING_SETTLEMENT_PROFIT_URL, body)

    def get_download_list(
        self,
        *,
        start_date: str,
        end_date: str,
        search_date_type: str = _SEARCH_DATE_SALES,
    ) -> dict[str, Any]:
        """종류별 엑셀 생성목록 조회(download-list/api). Phase 2 S6용.

        start_date/end_date: KST "YYYY-MM-DD" 형식.
        """
        body = {
            "startDate": self._kst_date_to_utc_iso(start_date),
            "endDate": self._kst_date_to_utc_iso(end_date),
            "searchDateType": search_date_type,
        }
        return self._post(WING_SETTLEMENT_DOWNLOAD_URL, body)
