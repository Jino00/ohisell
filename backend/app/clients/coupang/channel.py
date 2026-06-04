# channel.py — 쿠팡 채널 어댑터 (BaseChannelClient 구현: 주문 동기화·연결테스트)
# 기존 clients/coupang.py 동작을 100% 보존. sync_service가 이 CoupangClient를 사용한다.
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.clients.base import BaseChannelClient, RawOrder
from app.clients.coupang._base import CoupangBaseClient

log = logging.getLogger(__name__)


class CoupangClient(CoupangBaseClient, BaseChannelClient):
    """쿠팡 Wing/로켓그로스 채널 어댑터 (HMAC-SHA256).

    주문 수집(ordersheets) + 연결테스트(revenue-history). 정산/매출 동기화 경로에서 사용.
    상품 도메인은 CoupangProductClient(products.py)가 담당한다.
    """

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

    # 조회 대상 주문 상태 — DELIVERING 포함으로 당일 배송중 주문도 수집
    _FETCH_STATUSES = ("FINAL_DELIVERY", "DELIVERING", "DEPARTURE", "INSTRUCT")

    def fetch_orders(self, date_from: date, date_to: date) -> list[RawOrder]:
        path = f"/v2/providers/openapi/apis/api/v4/vendors/{self.vendor_id}/ordersheets"
        all_orders: list[RawOrder] = []
        seen: set[tuple[str, str]] = set()  # (order_id, vendorItemId) 중복 방지

        current = date_from
        while current <= date_to:
            day_str = current.strftime("%Y-%m-%d")

            for fetch_status in self._FETCH_STATUSES:
                next_token = ""
                while True:
                    params: dict = {
                        "createdAtFrom": day_str,
                        "createdAtTo": day_str,
                        "status": fetch_status,
                    }
                    if next_token:
                        params["nextToken"] = next_token

                    result = self._request("GET", path, params)
                    if not result:
                        break

                    code = result.get("code")
                    if str(code) != "200":
                        log.error("발주서 조회 실패 (날짜: %s, status: %s): code=%s", day_str, fetch_status, code)
                        break

                    for shipment in result.get("data", []):
                        order_id = str(shipment.get("orderId", ""))
                        paid_at = shipment.get("paidAt", day_str)
                        status = self._map_status(shipment.get("status", ""))
                        shipping_price = Decimal(str(shipment.get("shippingPrice", 0)))

                        for oi in shipment.get("orderItems", []):
                            vid = str(oi.get("vendorItemId", ""))
                            key = (order_id, vid)
                            if key in seen:
                                continue
                            seen.add(key)
                            raw = RawOrder(
                                order_number=order_id,
                                platform_product_id=vid,
                                platform_product_name=oi.get("vendorItemName", ""),
                                quantity=int(oi.get("shippingCount", 1)),
                                selling_price=Decimal(str(oi.get("orderPrice", 0))),
                                shipping_cost=shipping_price if shipping_price else None,
                                order_date=paid_at,
                                status=status,
                                raw_data=shipment,
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
