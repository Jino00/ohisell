# cafe24.py — cafe24 API 클라이언트 (OAuth2 code grant)
# Access Token 2시간 만료, Refresh Token 2주
from __future__ import annotations

import base64
import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Optional
from urllib.parse import urlencode

import requests

from app.clients.base import BaseChannelClient, RawOrder
from app.config import Cafe24AccountConfig

log = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 2

# cafe24 OAuth 스코프 (읽기 전용 — 주문/상품/정산 조회용)
CAFE24_SCOPES = ",".join([
    "mall.read_store",
    "mall.read_product",
    "mall.read_order",
    "mall.read_salesreport",
])


def build_cafe24_oauth_url(mall_id: str, client_id: str, redirect_uri: str) -> str:
    """cafe24 OAuth 인증 URL 생성"""
    base = f"https://{mall_id}.cafe24api.com/api/v2/oauth/authorize"
    params = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": CAFE24_SCOPES,
        "state": "cafe24_oauth",
    })
    return f"{base}?{params}"


def exchange_authorization_code(
    mall_id: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict | None:
    """Authorization code를 access_token + refresh_token으로 교환"""
    url = f"https://{mall_id}.cafe24api.com/api/v2/oauth/token"
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "expires_at": _parse_cafe24_datetime(data.get("expires_at")),
            "refresh_token_expires_at": _parse_cafe24_datetime(data.get("refresh_token_expires_at")),
        }
    except Exception as e:
        log.error("cafe24 토큰 교환 실패: %s", e)
        return None


def _parse_cafe24_datetime(iso_str: str | None) -> datetime | None:
    """cafe24가 반환하는 datetime 문자열을 UTC datetime으로 변환"""
    if not iso_str:
        return None
    try:
        # cafe24 응답에 timezone 없으면 KST(+09:00)로 간주
        if "+" not in iso_str and "Z" not in iso_str:
            iso_str += "+09:00"
        return datetime.fromisoformat(iso_str).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


class Cafe24Client(BaseChannelClient):
    """cafe24 API 클라이언트 (OAuth2 code grant)"""

    def __init__(
        self,
        config: Cafe24AccountConfig,
        access_token: str | None = None,
        refresh_token: str | None = None,
        on_token_refreshed: Optional[Callable[[str, str, datetime | None, datetime | None], None]] = None,
    ):
        self.mall_id = config.mall_id
        self.client_id = config.client_id
        self.client_secret = config.client_secret
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._on_token_refreshed = on_token_refreshed
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
            new_refresh = data.get("refresh_token")
            if new_refresh:
                self._refresh_token = new_refresh

            expires_at = _parse_cafe24_datetime(data.get("expires_at"))
            refresh_expires_at = _parse_cafe24_datetime(data.get("refresh_token_expires_at"))

            # DB 업데이트 콜백 호출
            if self._on_token_refreshed and self._access_token:
                self._on_token_refreshed(
                    self._access_token,
                    self._refresh_token or "",
                    expires_at,
                    refresh_expires_at,
                )

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
            "embed": "items",
        }

        while True:
            result = self._request("GET", path, params)
            if not result:
                break

            orders_data = result.get("orders", [])
            if not orders_data:
                break

            for item in orders_data:
                items_list = item.get("items", [])
                order_amount = item.get("actual_order_amount", {})
                shipping_fee_str = order_amount.get("shipping_fee", "0")
                shipping_fee = Decimal(str(shipping_fee_str).replace(",", ""))

                if items_list:
                    for detail in items_list:
                        price_str = detail.get("product_price", detail.get("actual_price", "0"))
                        # variant_code = 상품+옵션 고유 ID (예: P00000UC000Y)
                        variant = detail.get("variant_code", "")
                        product_no = str(detail.get("product_no", ""))
                        pid = variant if variant else product_no

                        # 상품명 + 옵션명 결합
                        pname = detail.get("product_name", "")
                        option_val = detail.get("option_value", "")
                        if option_val:
                            pname = f"{pname} [{option_val}]"

                        raw = RawOrder(
                            order_number=str(item.get("order_id", "")),
                            platform_product_id=pid,
                            platform_product_name=pname,
                            quantity=int(detail.get("quantity", 1)),
                            selling_price=Decimal(str(price_str).replace(",", "")),
                            shipping_cost=shipping_fee if shipping_fee else None,
                            order_date=item.get("order_date", date_from.isoformat()),
                            status=self._map_status(
                                detail.get("order_status", item.get("order_status", ""))
                            ),
                            raw_data={"order": item, "item": detail},
                        )
                        all_orders.append(raw)
                else:
                    # items 없으면 주문 금액으로 1건 기록
                    payment = order_amount.get("payment_amount", "0")
                    raw = RawOrder(
                        order_number=str(item.get("order_id", "")),
                        platform_product_id="",
                        platform_product_name="",
                        quantity=1,
                        selling_price=Decimal(str(payment).replace(",", "")),
                        shipping_cost=shipping_fee if shipping_fee else None,
                        order_date=item.get("order_date", date_from.isoformat()),
                        status=self._map_status(item.get("order_status", "")),
                        raw_data={"order": item},
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
