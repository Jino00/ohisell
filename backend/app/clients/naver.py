# naver.py — 네이버 커머스 API 클라이언트 (OAuth2 + bcrypt 서명)
# IP 사전등록 필수, Access Token 별도 구현 필요
from __future__ import annotations

import hashlib
import logging
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

import bcrypt
import requests

from app.clients.base import BaseChannelClient, RawOrder
from app.config import NaverAccountConfig

log = logging.getLogger(__name__)

NAVER_API_BASE = "https://api.commerce.naver.com/external"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2


class NaverClient(BaseChannelClient):
    """네이버 커머스 API 클라이언트 (OAuth2 + bcrypt 전자서명)"""

    def __init__(self, config: NaverAccountConfig, access_token: str | None = None):
        self.client_id = config.client_id
        self.client_secret = config.client_secret
        self._access_token = access_token

    def _generate_signature(self, client_id: str, client_secret: str, timestamp: int) -> str:
        """네이버 커머스 API bcrypt 전자서명 생성"""
        password = f"{client_id}_{timestamp}"
        hashed = bcrypt.hashpw(password.encode("utf-8"), client_secret.encode("utf-8"))
        return hashed.decode("utf-8")

    def _get_access_token(self) -> str | None:
        """OAuth2 Access Token 발급"""
        timestamp = int(time.time() * 1000)
        signature = self._generate_signature(self.client_id, self.client_secret, timestamp)

        try:
            resp = requests.post(
                f"{NAVER_API_BASE}/v1/oauth2/token",
                data={
                    "client_id": self.client_id,
                    "timestamp": timestamp,
                    "client_secret_sign": signature,
                    "grant_type": "client_credentials",
                    "type": "SELF",
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data.get("access_token")
            return self._access_token
        except Exception as e:
            log.error("네이버 토큰 발급 실패: %s", e)
            return None

    def _request(self, method: str, path: str, params: dict | None = None) -> dict | None:
        if not self._access_token:
            self._get_access_token()
        if not self._access_token:
            return None

        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        url = f"{NAVER_API_BASE}{path}"

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = requests.request(method, url, headers=headers, params=params, timeout=30)
                if resp.status_code in (401, 403):
                    if attempt == 0:
                        self._get_access_token()
                        continue
                    log.error("네이버 API 인증 실패: %s", path)
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
                log.error("네이버 API 에러: %s — %s", url, e)
                return None
        return None

    def test_connection(self) -> dict:
        token = self._get_access_token()
        if token:
            return {"status": "ok", "message": "네이버 API 연결 성공"}
        return {"status": "error", "message": "네이버 API 인증 실패"}

    def fetch_orders(self, date_from: date, date_to: date) -> list[RawOrder]:
        """네이버 주문 조회 (lastChangedFrom ~ lastChangedTo)"""
        path = "/v1/pay-order/seller/product-orders/last-changed-statuses"
        all_orders: list[RawOrder] = []

        params = {
            "lastChangedFrom": f"{date_from.isoformat()}T00:00:00.000+09:00",
            "lastChangedTo": f"{date_to.isoformat()}T23:59:59.999+09:00",
        }

        result = self._request("GET", path, params)
        if not result:
            return []

        for item in result.get("data", {}).get("lastChangeStatuses", []):
            product_order_id = item.get("productOrderId", "")
            # 상세 조회 필요 시 별도 호출
            raw = RawOrder(
                order_number=str(item.get("orderId", product_order_id)),
                platform_product_id=str(item.get("productId", "")),
                platform_product_name=item.get("productName", ""),
                quantity=int(item.get("quantity", 1)),
                selling_price=Decimal(str(item.get("totalPaymentAmount", 0))),
                shipping_cost=None,
                order_date=item.get("lastChangedDate", date_from.isoformat()),
                status=self._map_status(item.get("lastChangedType", "")),
                raw_data=item,
            )
            all_orders.append(raw)

        log.info("네이버 주문 %d건 수집 (%s ~ %s)", len(all_orders), date_from, date_to)
        return all_orders

    @staticmethod
    def _map_status(naver_status: str) -> str:
        mapping = {
            "PAYED": "confirmed",
            "DELIVERING": "shipped",
            "DELIVERED": "delivered",
            "PURCHASE_DECIDED": "delivered",
            "EXCHANGED": "returned",
            "CANCELED": "cancelled",
            "RETURNED": "returned",
        }
        return mapping.get(naver_status, naver_status.lower())
