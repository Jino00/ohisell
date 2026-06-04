# naver.py — 네이버 커머스 API 클라이언트 (OAuth2 + bcrypt 서명)
# IP 사전등록 필수, Access Token 별도 구현 필요
from __future__ import annotations

import base64
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
        """네이버 커머스 API bcrypt 전자서명 생성 (bcrypt → base64 인코딩)"""
        password = f"{client_id}_{timestamp}"
        hashed = bcrypt.hashpw(password.encode("utf-8"), client_secret.encode("utf-8"))
        return base64.b64encode(hashed).decode("utf-8")

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

    def _request_post(self, path: str, body: dict) -> dict | None:
        """POST JSON 요청"""
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
                resp = requests.post(url, headers=headers, json=body, timeout=30)
                if resp.status_code in (401, 403):
                    if attempt == 0:
                        self._get_access_token()
                        headers["Authorization"] = f"Bearer {self._access_token}"
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
        """네이버 주문 조회 (24시간 제한 → 하루 단위 분할 → 상세 조회)"""
        status_path = "/v1/pay-order/seller/product-orders/last-changed-statuses"
        detail_path = "/v1/pay-order/seller/product-orders/query"
        all_orders: list[RawOrder] = []
        seen_po_ids: set[str] = set()
        po_id_batch: list[str] = []

        # 1단계: 하루씩 last-changed-statuses로 productOrderId 수집
        current = date_from
        while current <= date_to:
            params = {
                "lastChangedFrom": f"{current.isoformat()}T00:00:00.000+09:00",
                "lastChangedTo": f"{current.isoformat()}T23:59:59.999+09:00",
            }
            result = self._request("GET", status_path, params)
            if result:
                for item in result.get("data", {}).get("lastChangeStatuses", []):
                    po_id = item.get("productOrderId", "")
                    if po_id and po_id not in seen_po_ids:
                        seen_po_ids.add(po_id)
                        po_id_batch.append(po_id)
            current += timedelta(days=1)
            time.sleep(0.2)

        if not po_id_batch:
            log.info("네이버 주문 0건 (%s ~ %s)", date_from, date_to)
            return []

        # 2단계: productOrderId 배치로 상세 조회 (최대 300건씩)
        chunk_size = 300
        for i in range(0, len(po_id_batch), chunk_size):
            chunk = po_id_batch[i:i + chunk_size]
            detail_result = self._request_post(detail_path, {"productOrderIds": chunk})
            if not detail_result:
                continue

            for entry in detail_result.get("data", []):
                po = entry.get("productOrder", {})
                order_info = entry.get("order", {})
                order_id = str(order_info.get("orderId", po.get("productOrderId", "")))
                product_id = str(po.get("productId", ""))
                shipping_fee = Decimal(str(po.get("deliveryFeeAmount", 0)))

                # 동일 주문+상품 중복 방지
                detail_key = f"{order_id}_{product_id}"
                if detail_key in seen_po_ids:
                    continue
                seen_po_ids.add(detail_key)

                # 네이버 API가 제공하는 실제 수수료 합산 (필드 부재 vs 명시적 0 구분)
                _COMM_KEYS = (
                    "paymentCommission",
                    "saleCommission",
                    "knowledgeShoppingSellingInterlockCommission",
                    "channelCommission",
                )
                commission: Decimal | None = (
                    Decimal(str(sum(po.get(k, 0) for k in _COMM_KEYS)))
                    if any(k in po for k in _COMM_KEYS)
                    else None
                )

                raw = RawOrder(
                    order_number=order_id,
                    platform_product_id=product_id,
                    platform_product_name=po.get("productName", ""),
                    quantity=int(po.get("quantity", 1)),
                    selling_price=Decimal(str(po.get("totalPaymentAmount", 0))),
                    shipping_cost=shipping_fee if shipping_fee else None,
                    order_date=order_info.get("paymentDate", date_from.isoformat()),
                    status=self._map_status(po.get("productOrderStatus", "")),
                    commission_amount=commission,
                    raw_data=entry,
                )
                all_orders.append(raw)
            time.sleep(0.3)

        log.info("네이버 주문 %d건 수집 (%s ~ %s)", len(all_orders), date_from, date_to)
        return all_orders

    def fetch_daily_settlement(self, date_from: date, date_to: date) -> list[dict]:
        """일별 정산 내역 조회 (/v1/pay-settle/settle/daily). 트랙 N1.

        Returns 정규화 dict 목록 (정산예정일·정산금액·수수료(음수)·혜택·지급보류 등).
        네이버 응답 amount는 부호 그대로 보존(수수료/혜택은 음수).
        """
        path = "/v1/pay-settle/settle/daily"
        results: list[dict] = []
        page = 1
        while True:
            params = {
                "startDate": date_from.isoformat(),
                "endDate": date_to.isoformat(),
                "pageNumber": page,
                "pageSize": 1000,
            }
            data = self._request("GET", path, params)
            if not data:
                break
            elements = data.get("elements", []) if isinstance(data, dict) else []
            for e in elements:
                results.append({
                    "settle_basis_start": e.get("settleBasisStartDate"),
                    "settle_basis_end": e.get("settleBasisEndDate"),
                    "settle_expect_date": e.get("settleExpectDate"),
                    "settle_complete_date": e.get("settleCompleteDate"),
                    "settle_amount": Decimal(str(e.get("settleAmount") or 0)),
                    "pay_settle_amount": Decimal(str(e.get("paySettleAmount") or 0)),
                    "commission_amount": Decimal(str(e.get("commissionSettleAmount") or 0)),
                    "benefit_amount": Decimal(str(e.get("benefitSettleAmount") or 0)),
                    "payholdback_amount": Decimal(str(e.get("payHoldbackAmount") or 0)),
                    "settle_method": e.get("settleMethodType"),
                })
            # 페이지 크기 미만이면 종료 (네이버는 page 응답에 총건수 메타가 그룹마다 달라 방어적 처리)
            if len(elements) < 1000:
                break
            page += 1
            time.sleep(0.2)
        log.info("네이버 일별 정산 %d건 수집 (%s ~ %s)", len(results), date_from, date_to)
        return results

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
