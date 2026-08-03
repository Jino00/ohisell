# naver.py — 네이버 커머스 API 클라이언트 (OAuth2 + bcrypt 서명)
# IP 사전등록 필수, Access Token 별도 구현 필요
from __future__ import annotations

import base64
import hashlib
import logging
import time
from datetime import date, datetime, timedelta, timezone
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

    # 일별 정산 조회 구간 상한(일, 시작·종료일 포함). 라이브 실측 2026-08-03:
    # 32일 구간 OK / 33일 구간부터 400. 공식 문서에 제한 명시가 없어 실측이 유일 근거다.
    SETTLE_DAILY_MAX_SPAN_DAYS = 32

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

    def _request_write(self, method: str, path: str, body: dict | None = None) -> dict:
        """쓰기(발주/발송) 전용 요청. 4xx 비즈니스 에러 본문을 그대로 surface.

        body=None이면 본문 없이 전송(requests가 json=None일 때 body 생략) — '본문 없음'
        스펙(예: 취소 승인)을 정확히 따른다.

        읽기용 _request/_request_post는 실패를 None으로 삼키지만, 쓰기는 네이버가
        주는 실패 사유(예: 이미 발송됨·발주확인 안 됨)를 사용자에게 보여줘야 하므로
        4xx에서는 재시도/삼키지 않고 {ok, status, data, error}로 반환한다.
        5xx·네트워크 오류만 재시도.
        반환: {"ok": bool, "status": int, "data": dict|None, "error": str|None}
        """
        if not self._access_token:
            self._get_access_token()
        if not self._access_token:
            return {"ok": False, "status": 401, "data": None, "error": "네이버 토큰 발급 실패"}

        url = f"{NAVER_API_BASE}{path}"
        for attempt in range(MAX_RETRIES + 1):
            headers = {
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            }
            try:
                resp = requests.request(method, url, headers=headers, json=body, timeout=30)
                if resp.status_code == 401 and attempt == 0:
                    self._get_access_token()  # 토큰 만료 1회 갱신 후 재시도
                    continue
                if resp.status_code >= 500 and attempt < MAX_RETRIES:
                    time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                    continue
                try:
                    payload = resp.json()
                except ValueError:
                    payload = {"raw": resp.text}
                if 200 <= resp.status_code < 300:
                    return {"ok": True, "status": resp.status_code, "data": payload, "error": None}
                # 4xx 비즈니스 에러 — 네이버 메시지 surface (재시도/삼키지 않음)
                msg = ""
                if isinstance(payload, dict):
                    msg = payload.get("message") or payload.get("invalidInputs") or str(payload)
                return {"ok": False, "status": resp.status_code, "data": payload, "error": str(msg)}
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                    continue
                log.error("네이버 쓰기 API 에러: %s — %s", url, e)
                return {"ok": False, "status": 0, "data": None, "error": str(e)}
        return {"ok": False, "status": 0, "data": None, "error": "재시도 초과"}

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
        seen_po_ids: set[str] = set()       # 1단계: 수집한 productOrderId (배치 중복 방지)
        emitted_po_ids: set[str] = set()    # 2단계: RawOrder로 내보낸 productOrderId (방어적 중복 방지)
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
                # 값이 None일 때 "None" 문자열이 그레인 키에 섞이지 않도록 `or` 폴백 사용.
                product_order_id = str(po.get("productOrderId") or "")
                order_id = str(order_info.get("orderId") or product_order_id or "")
                product_id = str(po.get("productId") or "")
                shipping_fee = Decimal(str(po.get("deliveryFeeAmount", 0)))

                # productOrderId 단위로 1행씩 내보낸다. 같은 (주문, 상품)이 여러 productOrderId로
                # 분할돼도(부분취소/부분배송) 각각 보존 → 수량·매출 누락 방지(트랙 N1·D-6).
                # 1단계서 이미 productOrderId를 dedup하나, 상세 응답 중복에 대비해 방어적으로 한 번 더.
                if product_order_id and product_order_id in emitted_po_ids:
                    continue
                emitted_po_ids.add(product_order_id)

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
                    platform_order_line_id=product_order_id,
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

        ★조회 구간 상한 = **32일**(라이브 실측 2026-08-03: `days=31`(32일 구간) OK /
          `days=32`(33일 구간)부터 400. 공식 문서엔 제한이 명시돼 있지 않아 실측이 유일 근거).
          호출부가 넘기면 여기서 즉시 죽인다 — 400을 받아 조용히 0건을 돌려주면 "성공 0건"으로
          기록돼 **아무도 모르는 채 정산 데이터가 계속 비는** 사고가 된다(라이브 실사고:
          `days=34`가 배선돼 있어 이 잡이 계속 400 → daily 테이블이 07-27에서 멈춰 있었다).
        ★요청 실패(_request가 None)도 예외로 표면화한다. 종전엔 `break`로 삼켜 빈 리스트를
          돌려줬고, 잡은 그걸 정상으로 보고 last_status='ok'를 남겼다(green-while-stale).
        """
        span = (date_to - date_from).days + 1
        if span > self.SETTLE_DAILY_MAX_SPAN_DAYS:
            raise ValueError(
                f"일별 정산 조회 구간 {span}일 > 상한 {self.SETTLE_DAILY_MAX_SPAN_DAYS}일 "
                f"({date_from}~{date_to}) — 네이버가 400을 낸다. 호출부 창을 줄일 것."
            )
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
            if data is None:
                raise RuntimeError(
                    f"일별 정산 조회 실패({date_from}~{date_to}, page={page}) — "
                    "빈 결과로 삼키지 않고 실패로 표면화한다."
                )
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

    def fetch_case_settlement(self, date_from: date, date_to: date) -> list[dict]:
        """건별 정산 내역 조회 (/v1/pay-settle/settle/case). 트랙 N1·D-6.

        결제일(PAY_DATE) 기준 정산 확정(SETTLED) 건을 productOrderId 단위로 수집.
        searchDate는 단일 날짜 → 결제일 하루씩 순회. 응답 금액 부호는 그대로 보존
        (수수료는 음수 — 2026-06-04 라이브 프로브 확인).
        매칭 키: order_id + product_id (PROD_ORDER만 상품 수수료, DELIVERY는 배송비).
        """
        path = "/v1/pay-settle/settle/case"
        results: list[dict] = []
        current = date_from
        while current <= date_to:
            page = 1
            while True:
                params = {
                    "periodType": "SETTLE_CASEBYCASE_PAY_DATE",
                    "settleDecisionType": "SETTLED",
                    "searchDate": current.isoformat(),
                    "pageNumber": page,
                    "pageSize": 1000,
                }
                data = self._request("GET", path, params)
                if data is None:
                    # ★조용한 유실 금지: 하루가 통째로 빠져도 종전엔 break로 삼켜
                    #   "성공"으로 기록됐다. 실패로 올려 다음 실행이 재시도하게 한다(upsert라 안전).
                    raise RuntimeError(
                        f"건별 정산 조회 실패(결제일 {current}, page={page}) — "
                        "빈 결과로 삼키지 않고 실패로 표면화한다."
                    )
                if not data:
                    break
                elements = data.get("elements", []) if isinstance(data, dict) else []
                for e in elements:
                    results.append({
                        "product_order_id": str(e.get("productOrderId") or ""),
                        "order_id": str(e.get("orderId") or ""),
                        "product_id": str(e.get("productId")) if e.get("productId") else None,
                        "product_order_type": e.get("productOrderType") or "",
                        "settle_type": e.get("settleType"),
                        "product_name": e.get("productName"),
                        "pay_settle_amount": Decimal(str(e.get("paySettleAmount") or 0)),
                        "total_pay_commission": Decimal(str(e.get("totalPayCommissionAmount") or 0)),
                        "selling_interlock_commission": Decimal(str(e.get("sellingInterlockCommissionAmount") or 0)),
                        "free_installment_commission": Decimal(str(e.get("freeInstallmentCommissionAmount") or 0)),
                        "benefit_amount": Decimal(str(e.get("benefitSettleAmount") or 0)),
                        "settle_expect_amount": Decimal(str(e.get("settleExpectAmount") or 0)),
                        "pay_date": e.get("payDate"),
                        "settle_expect_date": e.get("settleExpectDate"),
                        "settle_complete_date": e.get("settleCompleteDate"),
                    })
                if len(elements) < 1000:
                    break
                page += 1
                time.sleep(0.2)
            current += timedelta(days=1)
            time.sleep(0.15)
        log.info("네이버 건별 정산 %d건 수집 (결제일 %s ~ %s)", len(results), date_from, date_to)
        return results

    def fetch_inquiries(
        self,
        date_from: date,
        date_to: date,
        answered: bool | None = None,
    ) -> list[dict]:
        """고객 문의 목록 조회 (/v1/pay-user/inquiries). 트랙 N3.

        startSearchDate ~ endSearchDate 범위, 페이지네이션 자동 처리(size=200).
        answered=None이면 전체, True이면 답변 완료, False이면 미답변.
        """
        path = "/v1/pay-user/inquiries"
        results: list[dict] = []
        page = 1
        params: dict = {
            "startSearchDate": date_from.isoformat(),
            "endSearchDate": date_to.isoformat(),
            "size": 200,
            "page": page,
        }
        if answered is not None:
            params["answered"] = str(answered).lower()

        while True:
            params["page"] = page
            data = self._request("GET", path, params)
            if not data:
                break
            content = data.get("content", []) if isinstance(data, dict) else []
            for item in content:
                results.append({
                    "inquiry_no": item.get("inquiryNo"),
                    "category": item.get("category") or "",
                    "title": item.get("title") or "",
                    "inquiry_content": item.get("inquiryContent") or "",
                    "inquiry_date": item.get("inquiryRegistrationDateTime") or "",
                    "answered": bool(item.get("answered")),
                    "answer_content": item.get("answerContent") or "",
                    "answer_date": item.get("answerRegistrationDateTime") or "",
                    "order_id": item.get("orderId") or "",
                    "product_no": item.get("productNo") or "",
                    "product_name": item.get("productName") or "",
                    "product_order_option": item.get("productOrderOption") or "",
                    "customer_name": item.get("customerName") or "",
                    "customer_id": item.get("customerId") or "",
                })
            total_pages = data.get("totalPages", 1) if isinstance(data, dict) else 1
            if page >= total_pages:
                break
            page += 1
            time.sleep(0.15)
        log.info("네이버 고객 문의 %d건 수집 (%s ~ %s)", len(results), date_from, date_to)
        return results

    def search_products(
        self,
        status_types: list[str] | None = None,
        keyword_type: str | None = None,
        channel_product_nos: list[int] | None = None,
        page: int = 1,
        size: int = 500,
    ) -> dict:
        """상품 목록 조회 (POST /v1/products/search). 트랙 N4.

        status_types: SALE, OUTOFSTOCK, SUSPENSION 등. None이면 전체.
        반환: {totalElements, totalPages, page, contents}
        """
        body: dict = {"page": page, "size": size}
        if status_types:
            body["productStatusTypes"] = status_types
        if keyword_type and channel_product_nos:
            body["searchKeywordType"] = keyword_type
            body["channelProductNos"] = channel_product_nos
        data = self._request_post("/v1/products/search", body) or {}
        return {
            "total_elements": data.get("totalElements", 0),
            "total_pages": data.get("totalPages", 1),
            "page": data.get("page", page),
            "contents": [
                {
                    "origin_product_no": item.get("originProductNo"),
                    "group_product_no": item.get("groupProductNo"),
                    "channel_products": [
                        {
                            "channel_product_no": cp.get("channelProductNo"),
                            "name": cp.get("name") or "",
                            "status_type": cp.get("statusType") or "",
                            "sale_price": cp.get("salePrice"),
                            "discounted_price": cp.get("discountedPrice"),
                            "stock_quantity": cp.get("stockQuantity"),
                            "category": cp.get("wholeCategoryName") or "",
                            "image_url": (cp.get("representativeImage") or {}).get("url") or "",
                            "reg_date": cp.get("regDate") or "",
                            "modified_date": cp.get("modifiedDate") or "",
                        }
                        for cp in (item.get("channelProducts") or [])
                    ],
                }
                for item in (data.get("contents") or [])
            ],
        }

    def fetch_seller_info(self) -> dict:
        """판매자 계정·채널 정보 조회 (N5). 트랙 N5.

        계정: GET /v1/seller/account → accountId, grade
        채널: GET /v1/seller/channels → channelNo, name, url
        """
        account = self._request("GET", "/v1/seller/account") or {}
        channels = self._request("GET", "/v1/seller/channels") or []
        return {
            "account_id": account.get("accountId") or "",
            "account_uid": account.get("accountUid") or "",
            "grade": account.get("grade") or "",
            "channels": [
                {
                    "channel_no": c.get("channelNo"),
                    "channel_type": c.get("channelType") or "",
                    "name": c.get("name") or "",
                    "url": c.get("url") or "",
                    "talktalk_id": c.get("talkTalkAccountId") or "",
                }
                for c in (channels if isinstance(channels, list) else [])
            ],
        }

    def fetch_pending_orders(self, days: int = 14) -> dict:
        """발주확인 대기·발송 대기 주문 라이브 조회 (트랙 N6, 라이브 조회 방식).

        last-changed-statuses(최근 days일) → 상세 조회 → PAYED 건을 placeOrderStatus로 분류.
        (prod raw_data 실측: PAYED+NOT_YET=발주확인 대기, PAYED+OK=발송 대기 — 원칙 22 라이브 증거)
        ※ 변경상태 기반이라 days 창 밖 미발송 건은 누락 가능 → 창은 넉넉히(기본 14일).
        반환: {"awaiting_place": [...], "awaiting_dispatch": [...]} (각 항목 dict).
        """
        status_path = "/v1/pay-order/seller/product-orders/last-changed-statuses"
        detail_path = "/v1/pay-order/seller/product-orders/query"
        today = datetime.now(timezone(timedelta(hours=9))).date()  # KST 기준 (서버 UTC 보정)
        dfrom = today - timedelta(days=days - 1)

        seen: set[str] = set()
        po_ids: list[str] = []
        current = dfrom
        while current <= today:
            params = {
                "lastChangedFrom": f"{current.isoformat()}T00:00:00.000+09:00",
                "lastChangedTo": f"{current.isoformat()}T23:59:59.999+09:00",
            }
            result = self._request("GET", status_path, params)
            if result:
                for item in result.get("data", {}).get("lastChangeStatuses", []):
                    poid = item.get("productOrderId", "")
                    if poid and poid not in seen:
                        seen.add(poid)
                        po_ids.append(poid)
            current += timedelta(days=1)
            time.sleep(0.2)

        awaiting_place: list[dict] = []
        awaiting_dispatch: list[dict] = []
        emitted: set[str] = set()
        for i in range(0, len(po_ids), 300):
            chunk = po_ids[i:i + 300]
            detail = self._request_post(detail_path, {"productOrderIds": chunk})
            if not detail:
                continue
            for entry in detail.get("data", []):
                po = entry.get("productOrder", {})
                order_info = entry.get("order", {})
                poid = str(po.get("productOrderId") or "")
                if not poid or poid in emitted:
                    continue
                emitted.add(poid)
                if po.get("productOrderStatus") != "PAYED":
                    continue  # 결제완료(미발송)만 대상 — 배송중/취소/구매확정 제외
                row = {
                    "product_order_id": poid,
                    "order_id": str(order_info.get("orderId") or ""),
                    "product_name": po.get("productName") or "",
                    "quantity": int(po.get("quantity", 1)),
                    "orderer_name": order_info.get("ordererName") or "",
                    "receiver_name": (po.get("shippingAddress") or {}).get("name") or "",
                    "shipping_due_date": po.get("shippingDueDate") or "",
                    "expected_delivery_company": po.get("expectedDeliveryCompany") or "",
                    "expected_delivery_method": po.get("expectedDeliveryMethod") or "",
                    "package_number": po.get("packageNumber") or "",
                    "shipping_memo": po.get("shippingMemo") or "",
                    "place_order_status": po.get("placeOrderStatus") or "",
                    "order_date": order_info.get("orderDate") or "",
                }
                if po.get("placeOrderStatus") == "OK":
                    awaiting_dispatch.append(row)
                else:  # NOT_YET 등 — 발주확인 대기
                    awaiting_place.append(row)
            time.sleep(0.3)

        log.info(
            "네이버 미발송 주문 조회: 발주확인대기 %d / 발송대기 %d (%s ~ %s)",
            len(awaiting_place), len(awaiting_dispatch), dfrom, today,
        )
        return {"awaiting_place": awaiting_place, "awaiting_dispatch": awaiting_dispatch}

    def confirm_orders(self, product_order_ids: list[str]) -> dict:
        """발주 확인 처리 (POST .../confirm). 최대 30건. 트랙 N6.

        반환: _request_write 결과 {ok, status, data, error}.
        """
        return self._request_write(
            "POST",
            "/v1/pay-order/seller/product-orders/confirm",
            {"productOrderIds": product_order_ids},
        )

    def dispatch_orders(self, items: list[dict]) -> dict:
        """발송 처리 (POST .../dispatch). 최대 30건. 트랙 N6.

        items: 이미 네이버 형식으로 구성된 dict 목록
          [{productOrderId, deliveryMethod, deliveryCompanyCode, trackingNumber, dispatchDate}]
        반환: _request_write 결과.
        """
        return self._request_write(
            "POST",
            "/v1/pay-order/seller/product-orders/dispatch",
            {"dispatchProductOrders": items},
        )

    def delay_order(
        self,
        product_order_id: str,
        dispatch_due_date: str,
        delayed_dispatch_reason: str,
        dispatch_delayed_detailed_reason: str,
    ) -> dict:
        """발송 지연 처리 (POST .../{productOrderId}/delay). 단건. 트랙 N6.

        반환: _request_write 결과.
        """
        path = f"/v1/pay-order/seller/product-orders/{product_order_id}/delay"
        return self._request_write(
            "POST",
            path,
            {
                "dispatchDueDate": dispatch_due_date,
                "delayedDispatchReason": delayed_dispatch_reason,
                "dispatchDelayedDetailedReason": dispatch_delayed_detailed_reason,
            },
        )

    def fetch_claims(self, days: int = 14) -> dict:
        """클레임(취소/반품/교환) 요청 건 라이브 조회 (트랙 N7). 읽기.

        last-changed-statuses(최근 days)에서 claimStatus 있는 건 수집 → 상세로 상품명·주문자 보강.
        같은 productOrderId가 여러 번 변경되면 최신(lastChangedDate) 유지.
        claimStatus/claimType은 네이버 값 그대로 전달(추측 금지) — 처리대상 판별은 라우터/프론트.
        반환: {"claims": [ {product_order_id, claim_type, claim_status, ...}, ... ]}
        """
        status_path = "/v1/pay-order/seller/product-orders/last-changed-statuses"
        detail_path = "/v1/pay-order/seller/product-orders/query"
        today = datetime.now(timezone(timedelta(hours=9))).date()
        dfrom = today - timedelta(days=days - 1)

        by_poid: dict[str, dict] = {}
        current = dfrom
        while current <= today:
            params = {
                "lastChangedFrom": f"{current.isoformat()}T00:00:00.000+09:00",
                "lastChangedTo": f"{current.isoformat()}T23:59:59.999+09:00",
            }
            result = self._request("GET", status_path, params)
            if result:
                for item in result.get("data", {}).get("lastChangeStatuses", []):
                    poid = item.get("productOrderId", "")
                    cstatus = item.get("claimStatus")
                    if not poid or not cstatus:
                        continue  # 클레임 없는 일반 변경 제외
                    # 최신 변경만 유지 (lastChangedDate 비교, 문자열 ISO는 사전식=시간순)
                    prev = by_poid.get(poid)
                    if prev and (item.get("lastChangedDate") or "") < (prev.get("_lcd") or ""):
                        continue
                    by_poid[poid] = {
                        "product_order_id": poid,
                        "order_id": item.get("orderId") or "",
                        "claim_type": item.get("claimType") or "",
                        "claim_status": cstatus,
                        "last_changed_type": item.get("lastChangedType") or "",
                        "product_order_status": item.get("productOrderStatus") or "",
                        "_lcd": item.get("lastChangedDate") or "",
                    }
            current += timedelta(days=1)
            time.sleep(0.2)

        # 상세 보강 (상품명·주문자·수량)
        poids = list(by_poid.keys())
        for i in range(0, len(poids), 300):
            chunk = poids[i:i + 300]
            detail = self._request_post(detail_path, {"productOrderIds": chunk})
            if not detail:
                continue
            for entry in detail.get("data", []):
                po = entry.get("productOrder", {})
                order_info = entry.get("order", {})
                poid = str(po.get("productOrderId") or "")
                row = by_poid.get(poid)
                if not row:
                    continue
                row["product_name"] = po.get("productName") or ""
                row["quantity"] = int(po.get("quantity", 1))
                row["orderer_name"] = order_info.get("ordererName") or ""
            time.sleep(0.3)

        claims = []
        for row in by_poid.values():
            row.pop("_lcd", None)
            row.setdefault("product_name", "")
            row.setdefault("quantity", 0)
            row.setdefault("orderer_name", "")
            claims.append(row)
        log.info("네이버 클레임 %d건 조회 (%s ~ %s)", len(claims), dfrom, today)
        return {"claims": claims}

    # ── N7 클레임 — 취소 (wave 1) ──────────────────────────────
    def approve_cancel(self, product_order_id: str) -> dict:
        """취소 요청 승인 (POST .../{poid}/claim/cancel/approve). 단건, body 없음. 트랙 N7."""
        path = f"/v1/pay-order/seller/product-orders/{product_order_id}/claim/cancel/approve"
        return self._request_write("POST", path, None)  # 스펙상 본문 없음 (codex P1)

    def request_cancel(
        self,
        product_order_id: str,
        cancel_reason: str,
        cancel_detailed_reason: str = "",
        cancel_quantity: int | None = None,
    ) -> dict:
        """취소 요청 (판매자 직접, POST .../{poid}/claim/cancel/request). 단건. 트랙 N7."""
        path = f"/v1/pay-order/seller/product-orders/{product_order_id}/claim/cancel/request"
        body: dict = {"cancelReason": cancel_reason}
        if cancel_detailed_reason:
            body["cancelDetailedReason"] = cancel_detailed_reason
        if cancel_quantity is not None:
            body["cancelQuantity"] = cancel_quantity
        return self._request_write("POST", path, body)

    # ── N7 클레임 — 반품 (wave 2) ──────────────────────────────
    def approve_return(self, product_order_id: str) -> dict:
        """반품 승인 (POST .../{poid}/claim/return/approve). 단건, body 없음. 트랙 N7 wave2."""
        path = f"/v1/pay-order/seller/product-orders/{product_order_id}/claim/return/approve"
        return self._request_write("POST", path, None)  # 스펙상 본문 없음

    def reject_return(self, product_order_id: str, reject_return_reason: str) -> dict:
        """반품 거부(철회) (POST .../{poid}/claim/return/reject). 단건. 트랙 N7 wave2.

        reject_return_reason: 자유 텍스트 사유(enum 아님), 필수.
        """
        path = f"/v1/pay-order/seller/product-orders/{product_order_id}/claim/return/reject"
        return self._request_write("POST", path, {"rejectReturnReason": reject_return_reason})

    def holdback_return(
        self,
        product_order_id: str,
        holdback_class_type: str,
        holdback_return_detail_reason: str,
        extra_return_fee_amount: int | None = None,
    ) -> dict:
        """반품 보류 (POST .../{poid}/claim/return/holdback). 단건. 트랙 N7 wave2.

        holdback_class_type: 보류 유형 enum(RETURN_DELIVERYFEE 등), 필수.
        holdback_return_detail_reason: 보류 상세 사유(자유 텍스트), 필수.
        extra_return_fee_amount: 기타 반품 비용(선택).
        """
        path = f"/v1/pay-order/seller/product-orders/{product_order_id}/claim/return/holdback"
        body: dict = {
            "holdbackClassType": holdback_class_type,
            "holdbackReturnDetailReason": holdback_return_detail_reason,
        }
        if extra_return_fee_amount is not None:
            body["extraReturnFeeAmount"] = extra_return_fee_amount
        return self._request_write("POST", path, body)

    def release_return_holdback(self, product_order_id: str) -> dict:
        """반품 보류 해제 (POST .../{poid}/claim/return/holdback/release). 단건, body 없음. 트랙 N7 wave2."""
        path = f"/v1/pay-order/seller/product-orders/{product_order_id}/claim/return/holdback/release"
        return self._request_write("POST", path, None)  # 스펙상 본문 없음

    def request_return(
        self,
        product_order_id: str,
        return_reason: str,
        collect_delivery_method: str,
        collect_delivery_company: str = "",
        collect_tracking_number: str = "",
        return_quantity: int | None = None,
    ) -> dict:
        """반품 요청 (판매자 직접, POST .../{poid}/claim/return/request). 단건. 트랙 N7 wave2.

        return_reason: 클레임 요청 사유 enum(INTENT_CHANGED 등), 필수.
        collect_delivery_method: 수거 배송 방법 enum(DELIVERY 등), 필수.
        collect_delivery_company: 수거 택배사 코드(선택).
        collect_tracking_number: 수거 송장 번호(선택).
        return_quantity: 반품 수량(미입력 시 전체 수량 반품).
        """
        path = f"/v1/pay-order/seller/product-orders/{product_order_id}/claim/return/request"
        body: dict = {
            "returnReason": return_reason,
            "collectDeliveryMethod": collect_delivery_method,
        }
        if collect_delivery_company:
            body["collectDeliveryCompany"] = collect_delivery_company
        if collect_tracking_number:
            body["collectTrackingNumber"] = collect_tracking_number
        if return_quantity is not None:
            body["returnQuantity"] = return_quantity
        return self._request_write("POST", path, body)

    # ── N7 클레임 — 교환 (wave 3) ──────────────────────────────
    def approve_exchange_collect(self, product_order_id: str) -> dict:
        """교환 수거완료 (POST .../{poid}/claim/exchange/collect/approve). 단건, body 없음. 트랙 N7 wave3."""
        path = f"/v1/pay-order/seller/product-orders/{product_order_id}/claim/exchange/collect/approve"
        return self._request_write("POST", path, None)  # 스펙상 본문 없음

    def dispatch_exchange(
        self,
        product_order_id: str,
        re_delivery_method: str = "",
        re_delivery_company: str = "",
        re_delivery_tracking_number: str = "",
    ) -> dict:
        """교환 재배송 (POST .../{poid}/claim/exchange/dispatch). 단건. 트랙 N7 wave3.

        body 필드는 전부 선택(API센터 실측 — BODY는 required지만 개별 필드는 optional).
        """
        path = f"/v1/pay-order/seller/product-orders/{product_order_id}/claim/exchange/dispatch"
        body: dict = {}
        if re_delivery_method:
            body["reDeliveryMethod"] = re_delivery_method
        if re_delivery_company:
            body["reDeliveryCompany"] = re_delivery_company
        if re_delivery_tracking_number:
            body["reDeliveryTrackingNumber"] = re_delivery_tracking_number
        return self._request_write("POST", path, body)

    def holdback_exchange(
        self,
        product_order_id: str,
        holdback_class_type: str,
        holdback_exchange_detail_reason: str,
        extra_exchange_fee_amount: int | None = None,
    ) -> dict:
        """교환 보류 (POST .../{poid}/claim/exchange/holdback). 단건. 트랙 N7 wave3.

        holdback_class_type: 반품 보류와 동일 enum. 필드명만 Exchange (detail/extra).
        """
        path = f"/v1/pay-order/seller/product-orders/{product_order_id}/claim/exchange/holdback"
        body: dict = {
            "holdbackClassType": holdback_class_type,
            "holdbackExchangeDetailReason": holdback_exchange_detail_reason,
        }
        if extra_exchange_fee_amount is not None:
            body["extraExchangeFeeAmount"] = extra_exchange_fee_amount
        return self._request_write("POST", path, body)

    def release_exchange_holdback(self, product_order_id: str) -> dict:
        """교환 보류 해제 (POST .../{poid}/claim/exchange/holdback/release). 단건, body 없음. 트랙 N7 wave3."""
        path = f"/v1/pay-order/seller/product-orders/{product_order_id}/claim/exchange/holdback/release"
        return self._request_write("POST", path, None)  # 스펙상 본문 없음

    def reject_exchange(self, product_order_id: str, reject_exchange_reason: str) -> dict:
        """교환 거부(철회) (POST .../{poid}/claim/exchange/reject). 단건. 트랙 N7 wave3.

        reject_exchange_reason: 자유 텍스트 사유(enum 아님), 필수.
        """
        path = f"/v1/pay-order/seller/product-orders/{product_order_id}/claim/exchange/reject"
        return self._request_write("POST", path, {"rejectExchangeReason": reject_exchange_reason})

    # ── N8 상품 — 판매 상태 변경 (트랙 D-11) ──────────────────────
    def change_product_status(
        self,
        origin_product_no: int,
        status_type: str,
        stock_quantity: int | None = None,
        sale_start_date: str = "",
        sale_end_date: str = "",
    ) -> dict:
        """원상품 판매 상태 변경 (PUT /v1/products/origin-products/{originProductNo}/change-status). 트랙 N8.

        ★ 메서드는 PUT(주문 쓰기와 달리). 가격(salePrice) 안 받음 → 가격 손실 위험 0.
        status_type: SALE(판매중)/OUTOFSTOCK(품절)/SUSPENSION(판매중지). 필수.
        stock_quantity: 변경 재고 수량(<=99999999). 품절·중지→판매중(SALE) 전환 시 필수.
        sale_start_date/sale_end_date: ISO8601(yyyy-MM-dd'T'HH:mm[:ss][.SSS]XXX), 선택.
        반환: _request_write 결과 {ok, status, data, error}.
        """
        # 방어 심화(codex P2): 위험 상태(DELETE 등) 직접 호출 차단 — 라우터 우회 시 안전장치
        if status_type not in {"SALE", "OUTOFSTOCK", "SUSPENSION"}:
            return {"ok": False, "status": 400, "data": None,
                    "error": f"허용되지 않은 판매상태 '{status_type}' (SALE/OUTOFSTOCK/SUSPENSION만 가능)"}
        path = f"/v1/products/origin-products/{origin_product_no}/change-status"
        body: dict = {"statusType": status_type}
        if stock_quantity is not None:
            body["stockQuantity"] = stock_quantity
        if sale_start_date:
            body["saleStartDate"] = sale_start_date
        if sale_end_date:
            body["saleEndDate"] = sale_end_date
        return self._request_write("PUT", path, body)

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
