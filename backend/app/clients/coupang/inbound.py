# inbound.py — Wing 내부 입고 API 세션쿠키 클라이언트 (트랙 RG-Replenishment D-1, S1)
# ⚠️ HMAC 베이스(_base) 미상속 — Wing 내부 API는 셀러 로그인 '세션쿠키' 인증(공식 Open API와 별개).
# S0 실측(2026-06-05): GET wing.coupang.com/tenants/rfm-inbound/data/inbound/search, 전체쿠키셋
#   + x-xsrf-token 헤더 + 모바일 UA → 프로덕션(KR IP) HTTP 200/68KB. 302=쿠키 만료 신호.
from __future__ import annotations

import logging
import re
from collections.abc import Iterator

import requests

log = logging.getLogger(__name__)

WING_INBOUND_URL = "https://wing.coupang.com/tenants/rfm-inbound/data/inbound/search"
WING_REFERER = "https://wing.coupang.com/tenants/rfm-inbound/inbound/list"
# S0 실측: 모바일 Safari UA로 200 통과
_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
_REDIRECT_CODES = (301, 302, 303, 307, 308)


class WingAuthError(Exception):
    """Wing 세션쿠키 인증 실패(302 로그인 리다이렉트·401·403). = 쿠키 만료 신호.

    호출자(SA)가 잡아 cookie row status=red + fail-soft(마지막 입고 이력 유지) 처리(트랙 D-5).
    """


class WingReadError(Exception):
    """Wing 입고 API 읽기 실패(네트워크·5xx·JSON 파싱 실패). fail-soft 대상(인증 실패와 구분)."""


def parse_curl_cookies(curl: str, *, require_xsrf: bool = True) -> tuple[str, str]:
    """브라우저 'Copy as cURL'(또는 쿠키 문자열) → (cookie_header, xsrf_token).

    -H 'cookie: ...'/-b '...'에서 쿠키 추출. x-xsrf-token 헤더 우선, 없으면 쿠키의 XSRF-TOKEN
    값 사용(S0 실측: 둘이 동일). 쿠키만 통째 붙여넣은 경우도 허용. 추출 실패 시 ValueError.
    require_xsrf=False: xsrf 없어도 ("", "") 대신 (cookie, "") 반환 — 광고 API용.
    """
    if not curl or not curl.strip():
        raise ValueError("빈 입력 — cURL 또는 쿠키 문자열을 붙여넣어 주세요")
    text = curl.strip()

    # 1) -H 'cookie: ...' / -H "cookie: ..." (대소문자 무시)
    m = re.search(r"-H\s+(['\"])cookie:\s*(.*?)\1", text, re.IGNORECASE | re.DOTALL)
    if not m:
        # 2) -b '...' / --cookie '...'
        m = re.search(r"(?:-b|--cookie)\s+(['\"])(.*?)\1", text, re.IGNORECASE | re.DOTALL)
    if m:
        cookie = m.group(2).strip()
    elif "XSRF-TOKEN=" in text and "curl " not in text.lower():
        # 3) 쿠키 문자열만 통째 붙여넣은 경우
        cookie = text
    else:
        raise ValueError("쿠키를 찾지 못했습니다 — cURL의 cookie 헤더가 포함됐는지 확인하세요")

    if not cookie:
        raise ValueError("쿠키 값이 비어 있습니다")

    # x-xsrf-token: 헤더 우선
    mx = re.search(r"-H\s+(['\"])x-xsrf-token:\s*(.*?)\1", text, re.IGNORECASE | re.DOTALL)
    xsrf = mx.group(2).strip() if mx else ""
    if not xsrf:
        mc = re.search(r"XSRF-TOKEN=([^;]+)", cookie)
        xsrf = mc.group(1).strip() if mc else ""
    if not xsrf and require_xsrf:
        raise ValueError("x-xsrf-token을 찾지 못했습니다(헤더·XSRF-TOKEN 쿠키 모두 없음)")
    return cookie, xsrf


class CoupangInboundClient:
    """Wing 내부 입고 API 단일책임 래퍼. 세션쿠키+xsrf로 inbound/search 호출. raw dict 반환.

    파싱·리드타임 계산·적재는 Harness(services/coupang/rg_inbound_sync.py)가 담당(원칙 18-6).
    인증 실패(302/401)는 WingAuthError, 그 외 실패는 WingReadError로 표면화(원칙22: stale 위장 금지).
    """

    def __init__(self, cookie_header: str, xsrf_token: str):
        self.cookie = cookie_header
        self.xsrf = xsrf_token

    def _headers(self) -> dict:
        return {
            "x-xsrf-token": self.xsrf,
            "user-agent": _UA,
            "referer": WING_REFERER,
            "accept": "application/json, text/plain, */*",
            "cookie": self.cookie,
        }

    def search_inbound(self, *, paging_size: int = 50, page_index: int = 0) -> dict:
        """입고 목록 1페이지 조회 (GET, body 없음). 반환: {content:[...], pagination:{...}} dict.

        ⚠️ allow_redirects=False — 302를 따라가면 로그인 HTML(200)을 받아 만료를 놓침.
        302/401/403 → WingAuthError(만료). 5xx·네트워크·JSON실패 → WingReadError.
        """
        params = {"pagingSize": paging_size, "pageIndex": page_index}
        try:
            resp = requests.get(
                WING_INBOUND_URL, params=params, headers=self._headers(),
                timeout=30, allow_redirects=False,
            )
        except requests.exceptions.RequestException as e:
            raise WingReadError(f"Wing 입고 API 요청 에러: {e}") from e
        if resp.status_code in _REDIRECT_CODES:
            raise WingAuthError(f"Wing 세션쿠키 만료(리다이렉트 {resp.status_code})")
        if resp.status_code in (401, 403):
            raise WingAuthError(f"Wing 인증 실패(HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise WingReadError(f"Wing 입고 API HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as e:
            raise WingReadError(f"Wing 입고 API 응답 JSON 파싱 실패: {e}") from e
        if not isinstance(data, dict):
            raise WingReadError(f"Wing 입고 API 응답이 dict 아님: {type(data).__name__}")
        return data

    def iter_inbound(self, *, paging_size: int = 50, max_pages: int = 50) -> Iterator[dict]:
        """입고건(content[])을 페이징으로 순회 (제너레이터). 각 입고 dict yield.

        content 길이 < paging_size면 마지막 페이지로 종료. max_pages는 폭주 방지 안전장치.
        ★스키마 변동 방어(codex S1): content 키 누락·non-list는 WingReadError로 표면화(빈
        '0건 성공'으로 위장 금지, 원칙22). 실제 입고 0건(content=[])은 정상. max_pages 도달 시
        종료조건 미달=무한루프/스키마 의심 → WingReadError. 인증 실패는 그대로 전파.
        """
        page = 0
        while True:
            data = self.search_inbound(paging_size=paging_size, page_index=page)
            if "content" not in data:
                raise WingReadError("Wing 입고 응답에 content 키 없음(스키마 변동 의심)")
            content = data.get("content")
            if content is None:
                content = []  # 명시적 null = 입고 0건으로 간주(키는 존재)
            if not isinstance(content, list):
                raise WingReadError(
                    f"Wing 입고 content가 list 아님: {type(content).__name__}(스키마 변동 의심)"
                )
            for row in content:
                yield row
            if len(content) < paging_size:
                return
            page += 1
            if page >= max_pages:
                raise WingReadError(
                    f"Wing 입고 페이징 max_pages({max_pages}) 초과 — 종료조건 미달(스키마/무한루프 의심)"
                )
