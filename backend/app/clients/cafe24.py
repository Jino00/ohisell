# cafe24.py — cafe24 API 클라이언트 (OAuth2 code grant)
# Access Token 2시간 만료, Refresh Token 2주
from __future__ import annotations

import base64
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Optional, Tuple
from urllib.parse import urlencode

import requests

from app.clients.base import BaseChannelClient, RawOrder

# 단일 프로세스(PM2 1 워커 + APScheduler 동일 프로세스) 전제.
# 멀티 워커 전환 시 Redis 분산 락으로 교체 필요.
_CAFE24_REFRESH_LOCK = threading.Lock()
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED, normalize_status
from app.services.commission_resolver import PER_ORDER_TYPES, resolve as resolve_commission
from app.services.payment_classifier import classify as classify_payment
from app.services.shipping_resolver import per_order_shipping
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
        token_reader: Optional[Callable[[], Tuple[Optional[str], Optional[str], Optional[datetime]]]] = None,
    ):
        self.mall_id = config.mall_id
        self.client_id = config.client_id
        self.client_secret = config.client_secret
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._on_token_refreshed = on_token_refreshed
        self._token_reader = token_reader  # () -> (refresh_token, access_token, expires_at)
        self._api_base = f"https://{self.mall_id}.cafe24api.com/api/v2"

    def _refresh_access_token(self) -> str | None:
        """Refresh Token으로 Access Token 갱신 — 전역 락으로 직렬화.

        cafe24는 refresh 시 refresh_token을 회전시킨다. 락 없이 여러 경로가
        동시에 refresh를 시도하면 한쪽이 이미 소비된 RT로 400 invalid_grant를
        받아 토큰 체인이 파손된다. 따라서:
        1) 전역 락(_CAFE24_REFRESH_LOCK) 획득 후 DB에서 최신 토큰을 재조회.
        2) 대기 중 다른 스레드가 이미 갱신했고 access token이 유효하면 네트워크 skip.
        3) 그 외 DB 최신 RT로 네트워크 refresh → on_token_refreshed로 저장.
        """
        with _CAFE24_REFRESH_LOCK:
            # 락 획득 후 DB 최신 토큰 재조회 (다른 스레드가 이미 회전했을 수 있음)
            if self._token_reader:
                try:
                    fresh_rt, fresh_access, fresh_expires_at = self._token_reader()
                    if fresh_rt:
                        self._refresh_token = fresh_rt
                    now = datetime.now()
                    if fresh_access and fresh_expires_at and fresh_expires_at > now + timedelta(minutes=1):
                        # 이미 갱신된 유효 토큰 — 네트워크 호출 불필요
                        self._access_token = fresh_access
                        log.info("cafe24 토큰 이미 갱신됨 (락 대기 중 타 스레드 완료), 스킵")
                        return self._access_token
                except Exception as e:
                    log.warning("cafe24 token_reader 실패 (무시하고 refresh 진행): %s", e)

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

    def verify_connection(self) -> dict:
        """access token 유효성 확인 — refresh 부작용 없음. /status 전용."""
        if not self._access_token:
            return {"status": "no_token"}
        try:
            resp = requests.get(
                f"{self._api_base}/admin/store",
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                    "X-Cafe24-Api-Version": "2024-06-01",
                },
                timeout=5,
            )
            if resp.status_code == 200:
                return {"status": "ok"}
            if resp.status_code == 401:
                return {"status": "token_invalid"}
            return {"status": "error", "http_status": resp.status_code}
        except Exception as e:
            return {"status": "error", "message": str(e)}

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

            ship_per_order = per_order_shipping("CAFE24")  # 한진택배 1,900원/주문

            for item in orders_data:
                items_list = item.get("items", [])
                order_amount = item.get("actual_order_amount", {})

                # 결제유형 분류 (주문 단위) — market_id/payment_method/gateway 조합
                payment_type = classify_payment(item)

                if items_list:
                    # 라인별 매출/상태 산출 (수수료·배송비는 '매출 포함' 라인에만 배분)
                    details: list[tuple[dict, int, Decimal, str]] = []
                    for detail in items_list:
                        ps = detail.get("product_price", detail.get("actual_price", "0"))
                        qty = int(detail.get("quantity", 1))
                        rev = Decimal(str(ps).replace(",", "")) * qty
                        st = normalize_status(
                            detail.get("order_status", item.get("order_status", ""))
                        )
                        details.append((detail, qty, rev, st))

                    incl = [i for i, d in enumerate(details) if d[3] not in REVENUE_EXCLUDED]
                    incl_total = sum(details[i][2] for i in incl) or Decimal("0")

                    # 출고(배송비)는 포함 라인이 있을 때만 발생. PER_ORDER 수수료는 포함총액 기준.
                    ship_total = ship_per_order if incl else Decimal("0")
                    order_fee = (
                        resolve_commission(payment_type, incl_total)
                        if (payment_type in PER_ORDER_TYPES and incl)
                        else None
                    )

                    def _alloc(total: Decimal) -> dict[int, Decimal]:
                        # 포함 라인에 매출 비례 배분, 잔여는 마지막 포함 라인 (합=total 보장)
                        out: dict[int, Decimal] = {}
                        acc = Decimal("0")
                        for k, i in enumerate(incl):
                            if k == len(incl) - 1:
                                out[i] = total - acc
                            else:
                                v = (
                                    (total * details[i][2] / incl_total).quantize(Decimal("1"))
                                    if incl_total
                                    else Decimal("0")
                                )
                                out[i] = v
                                acc += v
                        return out

                    ship_alloc = _alloc(ship_total) if incl else {}
                    fee_alloc = _alloc(order_fee) if (order_fee is not None) else None

                    for idx, (detail, qty, line_rev, st) in enumerate(details):
                        variant = detail.get("variant_code", "")
                        product_no = str(detail.get("product_no", ""))
                        pid = variant if variant else product_no

                        pname = detail.get("product_name", "")
                        option_val = detail.get("option_value", "")
                        if option_val:
                            pname = f"{pname} [{option_val}]"

                        if st in REVENUE_EXCLUDED:
                            line_commission = Decimal("0")
                            line_ship = Decimal("0")
                        elif fee_alloc is not None:
                            line_commission = fee_alloc[idx]
                            line_ship = ship_alloc[idx]
                        else:
                            line_commission = resolve_commission(payment_type, line_rev)
                            line_ship = ship_alloc[idx]

                        raw = RawOrder(
                            order_number=str(item.get("order_id", "")),
                            platform_product_id=pid,
                            platform_product_name=pname,
                            quantity=qty,
                            selling_price=Decimal(str(
                                detail.get("product_price", detail.get("actual_price", "0"))
                            ).replace(",", "")),
                            shipping_cost=line_ship,
                            order_date=item.get("order_date", date_from.isoformat()),
                            status=st,
                            raw_data={"order": item, "item": detail},
                            payment_type=payment_type,
                            commission_amount=line_commission,
                        )
                        all_orders.append(raw)
                else:
                    # items 없으면 주문 금액으로 1건 기록
                    payment = order_amount.get("payment_amount", "0")
                    sell = Decimal(str(payment).replace(",", ""))
                    raw = RawOrder(
                        order_number=str(item.get("order_id", "")),
                        platform_product_id="",
                        platform_product_name="",
                        quantity=1,
                        selling_price=sell,
                        shipping_cost=ship_per_order,
                        order_date=item.get("order_date", date_from.isoformat()),
                        status=normalize_status(item.get("order_status", "")),
                        raw_data={"order": item},
                        payment_type=payment_type,
                        commission_amount=resolve_commission(payment_type, sell),
                    )
                    all_orders.append(raw)

            if len(orders_data) < 100:
                break
            params["offset"] += 100
            time.sleep(0.3)

        log.info("cafe24 주문 %d건 수집 (%s ~ %s)", len(all_orders), date_from, date_to)
        return all_orders
