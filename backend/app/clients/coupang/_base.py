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

    def _request(self, method: str, path: str, params: dict | None = None) -> dict | None:
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
                resp = requests.request(method, url, headers=headers, timeout=30)
                if resp.status_code in (401, 403):
                    log.error("쿠팡 API 인증 실패 (%d): %s", resp.status_code, path)
                    return None
                if resp.status_code == 429:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning("쿠팡 API rate limit, %ds 후 재시도", delay)
                    time.sleep(delay)
                    continue
                if resp.status_code >= 500 and attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning("쿠팡 API 서버 에러 (%d), %ds 후 재시도", resp.status_code, delay)
                    time.sleep(delay)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.Timeout:
                if attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning("쿠팡 API 타임아웃, %ds 후 재시도", delay)
                    time.sleep(delay)
                    continue
                log.error("쿠팡 API 타임아웃 (최종 실패): %s", path)
                return None
            except requests.exceptions.RequestException as e:
                log.error("쿠팡 API 요청 에러: %s — %s", url, e)
                return None
        return None
