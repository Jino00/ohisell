# coupang.py — 쿠팡 Open API 클라이언트 (HMAC-SHA256 서명)
# ohi-ad-intelligence/coupang_api_client.py 기반, 멀티 계정 + 재시도 개선
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlencode

import requests

from app.clients.base import BaseChannelClient, RawOrder
from app.config import CoupangAccountConfig

log = logging.getLogger(__name__)

API_GATEWAY = "https://api-gateway.coupang.com"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2  # seconds


class CoupangClient(BaseChannelClient):
    """쿠팡 Wing/로켓그로스 API 클라이언트 (HMAC-SHA256)"""

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

    def test_connection(self) -> dict:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        path = "/v2/providers/openapi/apis/api/v1/revenue-history"
        params = {
            "vendorId": self.vendor_id,
            "recognitionDateFrom": yesterday,
            "recognitionDateTo": yesterday,
            "token": "",
            "maxPerPage": 1,
        }
        result = self._request("GET", path, params)
        if result and result.get("code") == 200:
            return {"status": "ok", "vendor_id": self.vendor_id}
        return {"status": "error", "vendor_id": self.vendor_id, "message": str(result)}

    def fetch_orders(self, date_from: date, date_to: date) -> list[RawOrder]:
        path = f"/v2/providers/openapi/apis/api/v4/vendors/{self.vendor_id}/ordersheets"
        all_orders: list[RawOrder] = []

        current = date_from
        while current <= date_to:
            day_str = current.strftime("%Y-%m-%d")
            next_token = ""

            while True:
                params: dict = {
                    "createdAtFrom": day_str,
                    "createdAtTo": day_str,
                    "status": "FINAL_DELIVERY",
                }
                if next_token:
                    params["nextToken"] = next_token

                result = self._request("GET", path, params)
                if not result:
                    break

                code = result.get("code")
                if str(code) != "200":
                    log.error("발주서 조회 실패 (날짜: %s): code=%s", day_str, code)
                    break

                for item in result.get("data", []):
                    raw = RawOrder(
                        order_number=str(item.get("orderId", "")),
                        platform_product_id=str(item.get("vendorItemId", "")),
                        platform_product_name=item.get("vendorItemName", ""),
                        quantity=int(item.get("shippingCount", 1)),
                        selling_price=Decimal(str(item.get("orderPrice", 0))),
                        shipping_cost=None,  # 쿠팡 발주서에 배송비 별도 필드 없음
                        order_date=item.get("paidAt", day_str),
                        status=self._map_status(item.get("status", "")),
                        raw_data=item,
                    )
                    all_orders.append(raw)

                if result.get("hasNext"):
                    next_token = result.get("nextToken", "")
                else:
                    break
                time.sleep(0.5)

            current += timedelta(days=1)

        log.info("쿠팡 주문 %d건 수집 (%s ~ %s)", len(all_orders), date_from, date_to)
        return all_orders

    @staticmethod
    def _map_status(coupang_status: str) -> str:
        mapping = {
            "ACCEPT": "confirmed",
            "INSTRUCT": "confirmed",
            "DEPARTURE": "shipped",
            "DELIVERING": "shipped",
            "FINAL_DELIVERY": "delivered",
            "CANCEL": "cancelled",
            "RETURN": "returned",
        }
        return mapping.get(coupang_status, coupang_status.lower())
