# _base.py — 쿠팡 Open API 공통 베이스 (HMAC-SHA256 서명 + 재시도/타임아웃 _request)
# 모든 쿠팡 SA(channel/products/...)가 상속해 공유한다. (트랙 D-8: 호출은 서버 IP에서만)
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

from app.config import CoupangAccountConfig

log = logging.getLogger(__name__)

API_GATEWAY = "https://api-gateway.coupang.com"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2  # seconds


class CoupangReadError(Exception):
    """쿠팡 읽기 호출이 API 레벨에서 실패(None 반환·비200 코드)했음을 알리는 예외.

    codex [P1]: 실패한 읽기를 '0건 성공'으로 위장하면(iterator가 조용히 break) 데이터가
    stale인데도 스케줄러가 성공 보고한다(원칙 22). 페이징 iterator는 정상 빈 페이지(code 200,
    data 없음)와 이 예외를 구분해, 하드 실패는 이 예외로 표면화하고 Harness가 집계·표면화한다.
    """


class CoupangWriteError(Exception):
    """쿠팡 쓰기 호출(POST/PUT/DELETE)이 실패했음을 알리는 예외 (트랙 D-16, 쓰기 페이즈).

    쓰기는 라이브 스토어 변경이므로 실패를 절대 삼키지 않는다(원칙22). SA가 None·비성공코드를
    받으면 이 예외로 표면화하고, Harness/Router가 호출자(Jino)에게 그대로 보고한다.
    읽기와 달리 '정상 빈 결과'가 없으므로 실패=즉시 예외.
    """


class CoupangWriteValidationError(CoupangWriteError):
    """쓰기 입력값 검증 실패(클라이언트 잘못) — 업스트림 실패와 구분 (codex[P2] W3a).

    apMinSalePrice>=price·필수값 누락·정수 변환 불가 등은 호출자 입력 오류이지 쿠팡 API 실패가
    아니다. 라우터가 이 예외를 400으로, 상위 CoupangWriteError(업스트림 실패)를 502로 매핑한다.
    CoupangWriteError 하위이므로 기존 except 절은 그대로 잡되, 라우터가 먼저 이걸 분기한다.
    """


def check_write_response(
    resp: dict | None,
    context: str,
    success_codes: tuple[str, ...] = ("200", "SUCCESS"),
    *,
    require_code: bool = False,
) -> dict:
    """쿠팡 쓰기 응답 성공 판정 (트랙 D-16, codex[P1] W2·W3a).

    쓰기는 라이브 변경이므로 '전송 실패를 성공으로 위장'하면 안 된다(원칙22). _request는 2xx면
    body를 그대로 반환하는데, 쿠팡은 HTTP 200 + 실패 code/message를 주는 경우가 있다(읽기 SA는
    code를 검사하지만 쓰기는 누락됨). 이 헬퍼로 쓰기 SA가 code를 일관 검사한다.

    - resp is None(4xx/5xx·네트워크) → CoupangWriteError
    - code가 있고 success_codes에 없음 → CoupangWriteError
    - code 없음(키 부재·빈값): require_code=False면 성공 간주(보수적), True면 fail-closed

    Args:
        success_codes: 성공으로 간주할 code 집합. 기본 ("200","SUCCESS"). W3a 자동생성옵션
            (#19~22)은 응답 code가 SUCCESS/PROCESSING/FAILED이고 PROCESSING이 비동기 처리중(정상)
            이므로 ("200","SUCCESS","PROCESSING")으로 호출(명세 02 §4). 이 경우 FAILED만 실패.
        require_code: True면 인식 가능한 성공 code가 없을 때 fail-closed(codex[P1] W3a). W3a 9개는
            명세 02 §4에서 전부 code를 반환하므로 code 없는 2xx=비정상 → 성공 단정 금지(원칙22).
            W1/W2(물류·CS)는 응답 code 형태가 명세 미확인(D-1)이라 기본 False(회귀 방지).
    """
    if resp is None:
        raise CoupangWriteError(f"{context}: 응답 None(4xx/5xx·네트워크)")
    code = str(resp.get("code") or "")  # None·키부재·빈값 모두 "" 처리
    if not code:
        if require_code:
            raise CoupangWriteError(
                f"{context}: 성공 code 부재(2xx이나 인식 가능한 결과코드 없음 — fail-closed)"
            )
        return resp  # 보수적 성공 간주(W1/W2, code 형태 미확인)
    if code not in success_codes:
        raise CoupangWriteError(f"{context}: 실패 code={code} msg={resp.get('message')}")
    return resp


class CoupangBaseClient:
    """쿠팡 계정 1개에 대한 서명·요청 공통 베이스.

    하위 SA(CoupangClient=채널, CoupangProductClient=상품 등)가 상속한다.
    vendorItemId는 vendor_id에 귀속되므로(트랙 D-8) 클라이언트는 계정별로 생성한다.
    """

    def __init__(self, config: CoupangAccountConfig):
        self.vendor_id = config.vendor_id
        self.access_key = config.access_key
        self.secret_key = config.secret_key

    def _generate_hmac(self, method: str, path: str, query_str: str, datetime_str: str) -> str:
        message = datetime_str + method + path + query_str
        signature = hmac_mod.new(
            self.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return (
            "CEA algorithm=HmacSHA256, access-key=" + self.access_key
            + ", signed-date=" + datetime_str
            + ", signature=" + signature
        )

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        body: dict | None = None,
        *,
        retry_transient: bool = True,
    ) -> dict | None:
        """쿠팡 Open API 호출. body는 POST/PUT용 JSON 페이로드(optional).

        쿠팡 HMAC 서명은 datetime+method+path+query만 포함(body 제외)하므로
        body 추가는 기존 GET 동작에 영향 없음(후방호환).

        Args:
            retry_transient: 429/5xx/타임아웃 등 일시 오류 재시도 여부. 기본 True(읽기).
                ⚠️ 쓰기(POST/PUT/DELETE)는 False로 호출(codex[P1] W3a): 타임아웃 시 첫 요청이
                서버에 도달했는지 불명 → 재시도가 중복 실행(자동옵션 생성·정산 등) 위험. 쓰기는
                일시 오류를 즉시 None으로 표면화하고, 호출자가 라이브 상태 확인 후 재시도 결정(원칙22).
        """
        now_utc = datetime.now(timezone.utc)
        datetime_str = now_utc.strftime("%y%m%d") + "T" + now_utc.strftime("%H%M%S") + "Z"

        query_str = urlencode(params) if params else ""
        auth = self._generate_hmac(method, path, query_str, datetime_str)

        headers = {
            "Authorization": auth,
            "Content-Type": "application/json;charset=UTF-8",
        }
        url = f"{API_GATEWAY}{path}"
        if query_str:
            url += f"?{query_str}"

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = requests.request(
                    method, url, headers=headers, json=body, timeout=30
                )
                if resp.status_code in (401, 403):
                    log.error("쿠팡 API 인증 실패 (%d): %s", resp.status_code, path)
                    return None
                if resp.status_code == 429:
                    if not retry_transient:
                        log.error("쿠팡 API rate limit (쓰기 재시도 안 함): %s", path)
                        return None
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning("쿠팡 API rate limit, %ds 후 재시도", delay)
                    time.sleep(delay)
                    continue
                if resp.status_code >= 500 and retry_transient and attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning("쿠팡 API 서버 에러 (%d), %ds 후 재시도", resp.status_code, delay)
                    time.sleep(delay)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.Timeout:
                if retry_transient and attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning("쿠팡 API 타임아웃, %ds 후 재시도", delay)
                    time.sleep(delay)
                    continue
                log.error("쿠팡 API 타임아웃 (최종 실패%s): %s",
                          "·쓰기 재시도 안 함" if not retry_transient else "", path)
                return None
            except requests.exceptions.RequestException as e:
                log.error("쿠팡 API 요청 에러: %s — %s", url, e)
                return None
        return None
