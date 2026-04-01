# cafe24.py — cafe24 API 클라이언트 (OAuth2 code grant)
# Access Token 2시간 만료, Refresh Token 2주
from __future__ import annotations

import base64
import logging
import time
from datetime import date, timedelta
from decimal import Decimal

import requests

from app.clients.base import BaseChannelClient, RawOrder
from app.config import Cafe24AccountConfig

log = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 2


class Cafe24Client(BaseChannelClient):
    """cafe24 API 클라이언트 (OAuth2 code grant)"""

    def __init__(self, config: Cafe24AccountConfig, access_token: str | None = None, refresh_token: str | None = None):
        self.mall_id = config.mall_id
        self.client_id = config.client_id
        self.client_secret = config.client_secret
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._api_base = f"https://{self.mall_id}.cafe24api.com/api/v2"

    def _refresh_access_token(self) -> str | None:
        """Refresh Token으로 Access Token 갱신"""
        if not self._refresh_token:
            log.error("cafe24 refresh token 없음")
            return None

        auth = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        try:
            resp = requests.post(
                f"https://{self.mall_id}.cafe24api.com/api/v2/oauth/token",
                headers={
                    "Authorization": f"Basic {auth}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token", self._refresh_token)
            return self._access_token
        except Exception as e:
            log.error("cafe24 토큰 갱신 실패: %s", e)
            return None

    def _request(self, method: str, path: str, params: dict | None = None) -> dict | None:
        if not self._access_token:
            return None

        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "X-Cafe24-Api-Version": "2024-06-01",
        }
        url = f"{self._api_base}{path}"

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = requests.request(method, url, headers=headers, params=params, timeout=30)
                if resp.status_code == 401:
                    if attempt == 0:
                        self._refresh_access_token()
                        headers["Authorization"] = f"Bearer {self._access_token}"
                        continue
                    log.error("cafe24 API 인증 실패: %s", path)
                    return None
                if resp.status_code >= 500 and attempt < MAX_RETRIES:
                    time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                    continue
                log.error("cafe24 API 에러: %s — %s", url, e)
                return None
        return None

    def test_connection(self) -> dict:
        if not self._access_token:
            return {"status": "error", "message": "cafe24 access token이 없습니다. OAuth 인증을 먼저 수행하세요."}
        result = self._request("GET", "/admin/store")
        if result:
            return {"status": "ok", "message": "cafe24 API 연결 성공"}
        return {"status": "error", "message": "cafe24 API 연결 실패"}

    def fetch_orders(self, date_from: date, date_to: date) -> list[RawOrder]:
        """cafe24 주문 조회"""
        path = "/admin/orders"
        all_orders: list[RawOrder] = []

        params = {
            "start_date": date_from.isoformat(),
            "end_date": date_to.isoformat(),
            "limit": 100,
            "offset": 0,
        }

        while True:
            result = self._request("GET", path, params)
            if not result:
                break

            orders_data = result.get("orders", [])
            if not orders_data:
                break

            for item in orders_data:
                for detail in item.get("items", [{}]):
                    raw = RawOrder(
                        order_number=str(item.get("order_id", "")),
                        platform_product_id=str(detail.get("product_no", "")),
                        platform_product_name=detail.get("product_name", ""),
                        quantity=int(detail.get("quantity", 1)),
                        selling_price=Decimal(str(detail.get("product_price", 0))),
                        shipping_cost=Decimal(str(item.get("shipping_fee", 0))) or None,
                        order_date=item.get("order_date", date_from.isoformat()),
                        status=self._map_status(item.get("order_status", "")),
                        raw_data={"order": item, "item": detail},
                    )
                    all_orders.append(raw)

            if len(orders_data) < 100:
                break
            params["offset"] += 100
            time.sleep(0.3)

        log.info("cafe24 주문 %d건 수집 (%s ~ %s)", len(all_orders), date_from, date_to)
        return all_orders

    @staticmethod
    def _map_status(cafe24_status: str) -> str:
        mapping = {
            "N00": "confirmed",
            "N10": "confirmed",
            "N20": "shipped",
            "N30": "delivered",
            "N40": "delivered",
            "C00": "cancelled",
            "C10": "cancelled",
            "R00": "returned",
        }
        return mapping.get(cafe24_status, cafe24_status.lower())
