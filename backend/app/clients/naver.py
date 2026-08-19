# naver.py — 네이버 커머스 API 클라이언트 (OAuth2 + bcrypt 서명)
# IP 사전등록 필수, Access Token 별도 구현 필요
from __future__ import annotations

import base64
import hashlib
import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal

import bcrypt
import requests

from app.clients.base import BaseChannelClient, RawOrder
from app.config import NaverAccountConfig

log = logging.getLogger(__name__)


def naver_line_revenue(po: dict) -> Decimal:
    """네이버 상품주문 1건의 **우리가 받는 상품 매출**. 수집·백필이 같은 함수를 쓴다.

    ★공식(2026-08-04, 정산 원장 전수 대조로 확정):
        remainProductAmount − remainSellerBurdenDiscountAmount
      성숙 2개 창에서 **2,505건 100.00%** 정산 일치. 종전 `totalPaymentAmount`는 99.24%.

    왜 이 두 필드인가 — 종전 공식이 틀리는 방식이 두 가지였다:
      ① **부분취소**: 2개 중 1개가 취소돼도 `totalPaymentAmount`는 원 주문 금액 그대로다.
         `remain*`만 갱신된다 → 취소된 수량만큼 매출이 과대계상된다.
      ② **플랫폼 부담 할인**: `totalPaymentAmount`는 **고객이 낸 돈**이다. 네이버가 부담하는
         쿠폰·할인은 고객이 덜 내지만 그 차액을 네이버가 우리에게 지급하므로, 그만큼
         매출이 과소계상된다(실측 4,900원짜리 다수). 우리가 실제로 못 받는 것은
         **판매자 부담 할인**뿐이라, 그것만 뺀다.

    즉 "고객이 낸 돈"이 아니라 "우리가 받는 돈"을 매출로 삼는다 — 정산이 그 축이다.
    """
    remain = Decimal(str(po.get("remainProductAmount") or 0))
    seller_burden = Decimal(str(po.get("remainSellerBurdenDiscountAmount") or 0))
    if remain > 0:
        return remain - seller_burden
    # remain 계열이 없는 응답(구 스키마·부분 응답)은 종전 값으로 폴백한다 — 0으로 떨구면
    # 매출이 통째로 사라져 훨씬 위험하다.
    return Decimal(str(po.get("totalPaymentAmount") or 0))

NAVER_API_BASE = "https://api.commerce.naver.com/external"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2


class NaverClient(BaseChannelClient):
    """네이버 커머스 API 클라이언트 (OAuth2 + bcrypt 전자서명)"""

    # 일별 정산 조회 구간 상한(일, 시작·종료일 포함). 라이브 실측 2026-08-03:
    # 32일 구간 OK / 33일 구간부터 400. 공식 문서에 제한 명시가 없어 실측이 유일 근거다.
    SETTLE_DAILY_MAX_SPAN_DAYS = 32

    # 변경상태 조회(last-changed-statuses) 경로 — 세 호출부가 공유한다.
    LAST_CHANGED_PATH = "/v1/pay-order/seller/product-orders/last-changed-statuses"
    # 하루치 스윕의 페이지 상한(무한 루프 차단용 안전판). 라이브 실측 2026-08-19: 1페이지 300건이라
    # 50페이지 = 하루 15,000건이고, 우리 하루 최대 변경은 336건이다 — 정상 운영에선 절대 안 닿는다.
    LAST_CHANGED_MAX_PAGES = 50

    def __init__(self, config: NaverAccountConfig, access_token: str | None = None):
        self.client_id = config.client_id
        self.client_secret = config.client_secret
        self._access_token = access_token
        # 마지막 fetch_orders가 «전건»을 받았는지. sync_service가 읽는 기존 계약
        # (`getattr(client, "last_fetch_complete", False)`)과 같은 이름·의미다.
        self.last_fetch_complete = True
        # 미완주한 날짜들(YYYY-MM-DD). 로그·sync_log 표면화용.
        self.last_sweep_incomplete_days: list[str] = []

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

    def _sweep_last_changed(self, day: date) -> tuple[list[dict], bool]:
        """하루치 변경상태를 `more` 커서 끝까지 훑는다. 반환 `(항목들, 완주여부)`.

        ★존재 이유(2026-08-19 실사고, D-NAO-202): 이 API는 한 번에 **300건까지만** 주고 나머지는
          `data.more`(`moreFrom`·`moreSequence`)로 알려준다. 종전 세 호출부(fetch_orders·
          fetch_pending_orders·fetch_claims)는 전부 `data.lastChangeStatuses`만 읽고 `more`를
          **무시**해, 하루 변경이 300건을 넘는 날에는 그 시점 이후가 조용히 사라졌다.
          라이브 실측: 2026-08-18은 변경 336건인데 1페이지 300건에서 잘려 **20:30 이후 23건**이
          유실됐다(상품매출 356,100원, 그날 스마트스토어 결제 20~22시가 DB에 0건). 그 시각의
          `sync_log`는 20:45·21:45·22:45·23:45 전부 `success`였다 — 실패 신호가 어디에도 없었다.
          최근 45일(07-06~08-19) 중 절단일은 이 하루뿐이지만, 재발 조건은 「하루 변경 300건 초과」
          하나뿐이고 9월 단말 출시철이 그 조건에 가장 가깝다.

        ★페이지 넘기는 법(라이브 실측이 유일 근거 — 공식 문서에 예시가 없다):
          다음 요청 = 같은 `lastChangedTo` + `lastChangedFrom := more.moreFrom` + `moreSequence`.
          이 왕복으로 2026-08-18이 300 → 336건으로 완결되는 것을 확인했다.

        ★완주여부를 왜 돌려주나: 「못 받았다」와 「없다」는 같은 숫자로 보인다(교훈 #123). 호출부가
          이 값을 보고 처분을 정해야 한다 — 조용히 삼키면 이 결함이 그대로 재발한다. 첫 페이지
          실패도 미완주로 친다(그날 치를 통째로 못 본 것이므로).
        """
        base = {
            "lastChangedFrom": f"{day.isoformat()}T00:00:00.000+09:00",
            "lastChangedTo": f"{day.isoformat()}T23:59:59.999+09:00",
        }
        items: list[dict] = []
        params = dict(base)
        seen_cursors: set[str] = set()

        for page in range(self.LAST_CHANGED_MAX_PAGES):
            result = self._request("GET", self.LAST_CHANGED_PATH, params)
            if not result:
                log.error(
                    "[naver] 변경상태 조회 실패 — %s %d페이지째, 누적 %d건에서 중단(미완주).",
                    day, page + 1, len(items),
                )
                return items, False
            data = result.get("data") or {}
            items.extend(data.get("lastChangeStatuses") or [])

            more = data.get("more") or {}
            if not more:
                return items, True   # more 없음 = 이 날짜 완주
            # ★more는 있는데 커서 키가 비면 «완주»가 아니라 «못 이어받음»이다(적대 리뷰 P2).
            #   API가 「더 있다」고 말한 것만은 확실하므로 fail-closed로 올린다. 라이브 실측
            #   (2026-08-18)에서 둘 다 문자열로 왔지만, 스키마 변화를 완주로 오독하면 이 결함이
            #   그대로 재발한다 — 이 함수의 존재 이유가 「못 받았다」를 「없다」로 안 읽는 것이다.
            cursor = str(more.get("moreSequence") or "")
            more_from = str(more.get("moreFrom") or "")
            if not cursor or not more_from:
                log.error(
                    "[naver] 변경상태 more 커서 결측 — %s more=%r, 누적 %d건에서 중단(미완주).",
                    day, more, len(items),
                )
                return items, False

            # 커서가 안 움직이면 같은 페이지를 무한히 받는다 — 진행 없음은 미완주로 끊는다.
            if cursor in seen_cursors:
                log.error(
                    "[naver] 변경상태 커서 정체 — %s moreSequence=%s 반복, 누적 %d건에서 중단(미완주).",
                    day, cursor, len(items),
                )
                return items, False
            seen_cursors.add(cursor)

            params = dict(base)
            params["lastChangedFrom"] = more_from
            params["moreSequence"] = cursor
            time.sleep(0.1)

        log.error(
            "[naver] 변경상태 페이지 상한 %d 도달 — %s 누적 %d건에서 중단(미완주).",
            self.LAST_CHANGED_MAX_PAGES, day, len(items),
        )
        return items, False

    def fetch_orders(self, date_from: date, date_to: date) -> list[RawOrder]:
        """네이버 주문 조회 (24시간 제한 → 하루 단위 분할 → 상세 조회)

        ★1단계는 `_sweep_last_changed`로 **`more` 커서 끝까지** 훑는다(D-NAO-202) — 그 전엔
          1페이지(300건)만 읽어 바쁜 날의 저녁 주문이 통째로 유실됐다. 사고 상세는 헬퍼 docstring.
        ★부분 스윕은 «적재하되 표면화»한다: 받은 것은 그대로 넣고(버리면 멀쩡한 주문까지 잃는다),
          `last_fetch_complete=False`와 `last_sweep_incomplete_days`로 호출부에 알린다. 예외를
          던지지 않는 이유 — 30일 창의 하루가 실패했다고 나머지 29일 적재까지 무산시키면 부분
          유실이 전면 정지로 커진다. 대신 sync_service가 이 플래그를 sync_log에 적어 «success인데
          조용히 덜 들어옴»(이 결함의 본체)을 다시 만들지 않는다.
        """
        detail_path = "/v1/pay-order/seller/product-orders/query"
        all_orders: list[RawOrder] = []
        seen_po_ids: set[str] = set()       # 1단계: 수집한 productOrderId (배치 중복 방지)
        emitted_po_ids: set[str] = set()    # 2단계: RawOrder로 내보낸 productOrderId (방어적 중복 방지)
        po_id_batch: list[str] = []
        self.last_fetch_complete = True
        self.last_sweep_incomplete_days = []

        # 1단계: 하루씩 last-changed-statuses로 productOrderId 수집 (페이지 전건)
        current = date_from
        while current <= date_to:
            items, complete = self._sweep_last_changed(current)
            if not complete:
                self.last_fetch_complete = False
                self.last_sweep_incomplete_days.append(current.isoformat())
            for item in items:
                po_id = item.get("productOrderId", "")
                if po_id and po_id not in seen_po_ids:
                    seen_po_ids.add(po_id)
                    po_id_batch.append(po_id)
            current += timedelta(days=1)
            time.sleep(0.2)

        if not self.last_fetch_complete:
            log.error(
                "[naver] 주문 스윕 미완주 %d일(%s) — 이 구간 주문이 덜 수집됐다.",
                len(self.last_sweep_incomplete_days),
                ",".join(self.last_sweep_incomplete_days),
            )

        if not po_id_batch:
            log.info("네이버 주문 0건 (%s ~ %s)", date_from, date_to)
            return []

        # 2단계: productOrderId 배치로 상세 조회 (최대 300건씩)
        # ★청크 실패도 «부분 수집»이다(적대 리뷰 P1, 2026-08-19): 1단계 스윕이 완주해도 여기서
        #   조용히 넘어가면 청크당 최대 300건이 사라지는데 `last_fetch_complete`는 True로 남아
        #   「전건 받았다」고 거짓 주장한다 — 8/18 사고와 **같은 모양이고 폭은 더 크다**.
        #   1단계 미완주와 같은 표면으로 올려서 sync_log까지 도달시킨다.
        chunk_size = 300
        for i in range(0, len(po_id_batch), chunk_size):
            chunk = po_id_batch[i:i + chunk_size]
            detail_result = self._request_post(detail_path, {"productOrderIds": chunk})
            if not detail_result:
                self.last_fetch_complete = False
                self.last_sweep_incomplete_days.append(
                    f"detail-chunk[{i}:{i + len(chunk)}]"
                )
                log.error(
                    "[naver] 주문 상세조회 실패 — 청크 %d~%d(%d건)이 통째로 빠졌다(부분 수집).",
                    i, i + len(chunk), len(chunk),
                )
                continue

            for entry in detail_result.get("data", []):
                po = entry.get("productOrder", {})
                order_info = entry.get("order", {})
                # 값이 None일 때 "None" 문자열이 그레인 키에 섞이지 않도록 `or` 폴백 사용.
                product_order_id = str(po.get("productOrderId") or "")
                order_id = str(order_info.get("orderId") or product_order_id or "")
                product_id = str(po.get("productId") or "")
                # ★제주·도서산간 추가배송비 포함(2026-08-04): 정산 DELIVERY 원장 행은
                #   `deliveryFeeAmount + sectionDeliveryFee`와 일치한다(성숙 2개 창 전수 대조).
                #   종전엔 deliveryFeeAmount만 읽어, 무료배송(deliveryFeeAmount=0)인데 제주라
                #   sectionDeliveryFee만 붙는 건의 배송수입이 통째로 빠졌다(05월 이후 29건).
                #   금액은 상수로 박지 않고 스마트스토어가 주는 값을 그대로 쓴다(Jino 지시) —
                #   스토어 설정이 바뀌면 코드 수정 없이 따라간다.
                shipping_fee = Decimal(str(po.get("deliveryFeeAmount", 0))) + Decimal(
                    str(po.get("sectionDeliveryFee", 0) or 0)
                )

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
                # ★부분취소 보정(2026-08-04, 정산 전수 대조로 확정): 주문 API의 수수료 필드는
                #   부분취소가 나도 **원 주문 기준으로 남는다**(remain 대응 필드가 없다).
                #   그대로 두면 매출만 remain으로 줄고 수수료는 안 줄어 수수료율이 2배로 보인다.
                #   → 남은 금액 비율로 축소한다. 절사(ROUND_DOWN)인 것은 네이버 실측 관례와
                #   같다(배송비 수수료 1,652건 전수에서 절사 99.5% vs 반올림 92.1%).
                #   라이브 검증: 부분취소 2건 모두 정산과 원 단위 일치(1,477→738 / 1,185→592).
                if commission is not None:
                    _total_prod = Decimal(str(po.get("totalProductAmount") or 0))
                    _remain_prod = Decimal(str(po.get("remainProductAmount") or 0))
                    if _total_prod > 0 and _remain_prod != _total_prod:
                        commission = (commission * _remain_prod / _total_prod).quantize(
                            Decimal("1"), rounding=ROUND_DOWN
                        )

                raw = RawOrder(
                    order_number=order_id,
                    platform_product_id=product_id,
                    platform_order_line_id=product_order_id,
                    platform_product_name=po.get("productName", ""),
                    quantity=int(po.get("quantity", 1)),
                    selling_price=naver_line_revenue(po),
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

    def fetch_case_settlement_by_order(self, order_id: str) -> list[dict]:
        """주문번호 단건의 건별 정산 행 **전량** 조회(유형 필터 없음).

        fetch_case_settlement()와 같은 엔드포인트지만 **조회 축이 다르다**:
          - fetch_case_settlement: searchDate(결제일) 순회 + settleDecisionType='SETTLED' 고정
            → 회계 적재용(확정분만).
          - 이 메서드: orderId 하나만 지정
            → "이 주문이 어떤 productOrderType·settleType으로 잡히는가"를 관측하기 위한
              것(N배송 반품 회수비 프로브).

        ★★orderId는 periodType·searchDate와 **상호 배타**다(2026-08-03 라이브 실측, raw HTTP):
            orderId + periodType  → 400 "periodType 값은 orderId, productOrderId 값과 같이
                                    입력될 수 없습니다"
            orderId + searchDate  → 400 (같은 문구)
            orderId 단독(+page/size) → 200
          settleDecisionType은 periodType=SETTLE_CASEBYCASE_PAY_DATE일 때만 의미를 갖는데
          그 periodType 자체를 못 주므로 **단건 조회에서는 쓸 수 없다**. 대신 그 주문의 정산
          행이 유형 구분 없이 전부 돌아온다 — 유형 축은 응답의 settleType으로 관측한다
          (settleDecisionType은 응답 스키마에 아예 없다, 공식 스펙 확인).
          07-31 첫 배포본이 이 세 개를 함께 보내 라이브에서 400으로 전손했다.

        ★조용한 실패 금지: _request가 None(인증·네트워크·400)이면 빈 결과로 삼키지 않고
          RuntimeError로 올린다(fetch_case_settlement와 같은 관례) — 프로브가 "아직 정산 안
          뜸"과 "조회가 실패함"을 절대 혼동하면 안 되기 때문이다. 혼동하면 08-06·08-09에
          표본이 익어도 실패를 '0건'으로 기록하고 알림 없이 지나간다.
        """
        path = "/v1/pay-settle/settle/case"
        results: list[dict] = []
        page = 1
        while True:
            params = {
                "orderId": str(order_id),
                "pageNumber": page,
                "pageSize": 1000,
            }
            data = self._request("GET", path, params)
            if data is None:
                raise RuntimeError(
                    f"건별 정산 단건 조회 실패(orderId={order_id}, page={page}) — "
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
                    "settle_expect_amount": Decimal(str(e.get("settleExpectAmount") or 0)),
                    "pay_date": e.get("payDate"),
                    "settle_expect_date": e.get("settleExpectDate"),
                    "settle_complete_date": e.get("settleCompleteDate"),
                })
            if len(elements) < 1000:
                break
            page += 1
            time.sleep(0.2)
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
        ★`more` 커서 전건 스윕(D-NAO-202) — 종전엔 1페이지(300건)만 읽어, 바쁜 날의 저녁 주문이
          **발주확인 대기 목록에서 통째로 빠졌다**(미출고로 이어지는 경로다). 헬퍼 docstring 참조.
          미완주는 목록을 비우지 않고 부분 반환 + log.error — 이 화면은 «그 순간의 처리 대상»이라
          통째로 죽이는 쪽이 더 나쁘고, 다음 호출이 재기회다.
        반환: {"awaiting_place": [...], "awaiting_dispatch": [...]} (각 항목 dict).
        """
        detail_path = "/v1/pay-order/seller/product-orders/query"
        today = datetime.now(timezone(timedelta(hours=9))).date()  # KST 기준 (서버 UTC 보정)
        dfrom = today - timedelta(days=days - 1)

        seen: set[str] = set()
        po_ids: list[str] = []
        incomplete: list[str] = []
        current = dfrom
        while current <= today:
            items, complete = self._sweep_last_changed(current)
            if not complete:
                incomplete.append(current.isoformat())
            for item in items:
                poid = item.get("productOrderId", "")
                if poid and poid not in seen:
                    seen.add(poid)
                    po_ids.append(poid)
            current += timedelta(days=1)
            time.sleep(0.2)
        if incomplete:
            log.error(
                "[naver] 발주/발송 대기 스윕 미완주 %d일(%s) — 목록이 실제보다 적다.",
                len(incomplete), ",".join(incomplete),
            )

        awaiting_place: list[dict] = []
        awaiting_dispatch: list[dict] = []
        emitted: set[str] = set()
        for i in range(0, len(po_ids), 300):
            chunk = po_ids[i:i + 300]
            detail = self._request_post(detail_path, {"productOrderIds": chunk})
            if not detail:
                # ★상세조회 실패도 미완주다(2R 리뷰 P1) — 여기서 조용히 넘어가면 목록이
                #   비어 나가면서 `incomplete_days`는 «완주»라고 말한다. 발주확인 대기는
                #   미출고로 이어지는 화면이라 거짓 「처리할 게 없다」의 대가가 크다.
                incomplete.append(f"detail-chunk[{i}:{i + len(chunk)}]")
                log.error(
                    "[naver] 발주/발송 대기 상세조회 실패 — 청크 %d~%d(%d건) 누락.",
                    i, i + len(chunk), len(chunk),
                )
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
        # 미완주를 반환값에도 싣는다(적대 리뷰 P2): 로그만 남기면 화면은 200 + 짧은 목록만
        # 보고 「처리할 게 없다」로 읽는다. 빈 리스트면 호출부 동작 불변.
        return {"awaiting_place": awaiting_place, "awaiting_dispatch": awaiting_dispatch,
                "incomplete_days": incomplete}

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
        ★`more` 커서 전건 스윕(D-NAO-202) — 종전엔 1페이지(300건)만 읽었다. 클레임은 하루 변경의
          일부라 절단에 더 잘 걸린다(취소·반품이 저녁에 몰리면 그대로 안 보인다). 헬퍼 docstring 참조.
          미완주는 부분 반환 + log.error(발주 대기와 같은 사유).
        반환: {"claims": [ {product_order_id, claim_type, claim_status, ...}, ... ]}
        """
        detail_path = "/v1/pay-order/seller/product-orders/query"
        today = datetime.now(timezone(timedelta(hours=9))).date()
        dfrom = today - timedelta(days=days - 1)

        by_poid: dict[str, dict] = {}
        incomplete: list[str] = []
        current = dfrom
        while current <= today:
            items, complete = self._sweep_last_changed(current)
            if not complete:
                incomplete.append(current.isoformat())
            for item in items:
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
        if incomplete:
            log.error(
                "[naver] 클레임 스윕 미완주 %d일(%s) — 클레임 목록이 실제보다 적다.",
                len(incomplete), ",".join(incomplete),
            )

        # 상세 보강 (상품명·주문자·수량)
        poids = list(by_poid.keys())
        for i in range(0, len(poids), 300):
            chunk = poids[i:i + 300]
            detail = self._request_post(detail_path, {"productOrderIds": chunk})
            if not detail:
                # ★상세 보강 실패도 미완주다(2R 리뷰 P1). 클레임은 여기서 넘어가면 행은
                #   남고 상품명·주문자만 공란이 되어 «불완전»이 «정상»처럼 보인다.
                incomplete.append(f"detail-chunk[{i}:{i + len(chunk)}]")
                log.error(
                    "[naver] 클레임 상세 보강 실패 — 청크 %d~%d(%d건) 공란.",
                    i, i + len(chunk), len(chunk),
                )
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
        return {"claims": claims, "incomplete_days": incomplete}

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
        """네이버 productOrderStatus → 내부 상태.

        ★내부 상태는 매출 인식을 가른다 — cafe24_status_mapper.REVENUE_EXCLUDED
          ({cancelled, returned, pending})에 걸리면 매출·수수료·원가가 통째로 빠진다.
          그래서 이 표는 "이 주문의 돈이 우리 것인가"를 결정하는 회계 계약이다.

        ★2026-08-03 정정 2건(14일 전수 대사에서 발견):
          ① EXCHANGED → 종전 "returned"였다. **교환은 환불이 아니다** — 상품만 바꿔 보내고
             돈은 그대로 우리 것이다. 정산이 이를 확정한다: 교환 라인 33건이
             settle_type=NORMAL_SETTLE_ORIGINAL로 **정산 완료**됐고(settle_complete_date 존재)
             차감 행(NORMAL_SETTLE_AFTER_CANCEL)은 3개월이 지나도 오지 않았다.
             그런데 우리는 매출에서 뺐다 — 라이브 49건 826,500원 과소계상.
             → 별도 상태 "exchanged"로 분리(REVENUE_EXCLUDED에 넣지 않아 매출 유지).
          ② CANCELED_BY_NOPAYMENT → 매핑이 없어 폴백 .lower()로 통과했고, 그 값은
             REVENUE_EXCLUDED에 없어 **결제조차 안 된 주문이 매출로 잡혔다**
             (라이브 10건 163,000원 과대계상). → "cancelled"로 명시 매핑.

        ★폴백(.lower())은 유지하되 경고를 남긴다 — 모르는 상태가 조용히 매출로 새는 것이
          ②의 실체였다. 새 상태가 로그에 뜨면 이 표에 명시적으로 추가할 것.
        """
        mapping = {
            # 매출 인식
            "PAYED": "confirmed",            # 결제완료
            "DELIVERING": "shipped",         # 배송중
            "DELIVERED": "delivered",        # 배송완료
            "PURCHASE_DECIDED": "delivered",  # 구매확정
            "EXCHANGED": "exchanged",        # 교환완료 — 돈은 우리 것(매출 유지)
            # 매출 제외 (REVENUE_EXCLUDED)
            "CANCELED": "cancelled",
            "CANCELED_BY_NOPAYMENT": "cancelled",  # 미입금 취소 = 결제 자체가 없었다
            "RETURNED": "returned",
        }
        mapped = mapping.get(naver_status)
        if mapped is None:
            log.warning(
                "네이버 미지의 주문상태 '%s' — 매핑표에 없어 소문자 폴백. 매출 인식이 "
                "의도와 다를 수 있으니 _map_status에 명시 추가할 것", naver_status,
            )
            return naver_status.lower()
        return mapped
