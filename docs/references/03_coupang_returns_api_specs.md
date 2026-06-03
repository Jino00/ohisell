# 03. 쿠팡 반품/교환 API 정밀 명세 (P2 — 순매출 차감 회계축)

> 수집일: 2026-06-03 · 출처: developers.coupangcorp.com (공식 포털, /browse --headed 직접 확인, Cloudflare 우회)
> 범위: 반품 API 7개 + 교환 API 4개 = 11개. 회계 정확화(순매출 = 매출 − 반품/취소)에 직접 닿는 **읽기 5개**를 정밀 수집, 쓰기 6개는 path만(쓰기 페이즈 stub).
> ⚠️ 모든 path는 게이트웨이 `https://api-gateway.coupang.com` 기준. 인증 = HMAC-SHA256 (기존 `_base._request` 재사용).
> ⚠️ 반품/교환 API는 **`/v6` 또는 `/v4`** 경로 (상품 API의 `/marketplace`와 다름). vendorId가 path에 박힘.

---

## 0. 조망 결합 (왜 P2인가)

```
반품/취소 목록 조회 → returnItems[].vendorItemId(옵션ID) + cancelCount + receiptType(RETURN/CANCEL)
        │ (vendorItemId 직결)
        ▼
coupang_product_item.vendor_item_id  ⨝  orders.platform_product_id
        │
        ▼
순매출 = 주문매출 − (반품/취소 수량 × 단가)   ← D-3 사실 정리 (전략판단 아님)
```
- **결합키 = vendorItemId** (광고·상품·주문과 동일 축). 반품도 옵션ID 단위로 접수됨 ("vendorItemId 단위로 반품이 접수됩니다" — 공식 문구).
- **반품철회(returnWithdraw)**: 접수됐다가 철회된 반품은 **차감하면 안 됨**. 철회 이력으로 제외 처리 → 차감 정확도. (cancelId/vendorItemIds 단위)

---

## 1. 반품 API (7개)

### 1-1. 반품/취소 요청 목록 조회 ★ [READ — 핵심]
- **Path**: `GET /v2/providers/openapi/apis/api/v6/vendors/{vendorId}/returnRequests`
- **Query**:
  | 이름 | 필수 | 타입 | 설명 |
  |------|:---:|------|------|
  | searchType | O | String | `timeFrame` 설정 시 분단위 조회. 미설정 시 일단위 |
  | createdAtFrom | O | String | 검색 시작일 `yyyy-MM-dd` (timeFrame 시 `yyyy-MM-ddTHH:mm`) |
  | createdAtTo | O | String | 검색 종료일 (동일 형식) |
  | status | | String | RU(출고중지요청)·UC(반품접수)·CC(반품완료)·PR(쿠팡확인요청). cancelType=CANCEL 시 미지원 |
  | cancelType | | String | RETURN(반품, default)·CANCEL(취소). CANCEL 조회 시 status·orderId 제외해야 함 |
  | nextToken | | String | 페이징 토큰. timeFrame 시 미지원 |
  | maxPerPage | | Number | default 50. timeFrame 시 미지원 |
  | orderId | | Number | status 제외하고 조회 시 포함 필요. timeFrame 시 미지원 |
- **제약**: 최대 **31일** 조회. 길면 타임아웃 → 짧게 권장. 출고중지요청은 RU·UC 상태로 조회됨.
- **결제완료 단계 취소** 조회: `cancelType=CANCEL` + status·orderId 제외.
- **응답 data[]** (핵심 필드):
  | 필드 | 타입 | 의미 |
  |------|------|------|
  | **receiptId** | Number | 취소(반품)접수번호 (단건조회·철회조회 키) |
  | **orderId** | Number | 주문번호 |
  | paymentId | Number | 결제번호 |
  | **receiptType** | String | **RETURN / CANCEL** (반품인지 취소인지) |
  | receiptStatus | String | RELEASE_STOP_UNCHECKED·RETURNS_UNCHECKED·VENDOR_WAREHOUSE_CONFIRM·REQUEST_COUPANG_CHECK·RETURNS_COMPLETED |
  | createdAt | String | 접수시간 `yyyy-MM-ddThh:mm:ss` |
  | modifiedAt | String | 최종 변경시간 |
  | cancelReasonCategory1/2 | String | 반품 사유 카테고리 |
  | cancelReason | String | 취소사유 상세 |
  | **cancelCountSum** | Number | 총 취소수량 |
  | returnDeliveryId | Number | 반품배송번호 |
  | faultByType | String | 귀책: COUPANG·VENDOR·CUSTOMER·WMS·GENERAL |
  | preRefund | Boolean | 선환불 여부 |
  | completeConfirmType | String | VENDOR_CONFIRM·UNDEFINED·CS_CONFIRM·CS_LOSS_CONFIRM |
  | enclosePrice | Object | 동봉배송비 {currencyCode, units, nanos} |
  | **returnItems[]** | Array | 반품 아이템 목록 (아래) |
- **응답 data[].returnItems[]** (★회계 결합축):
  | 필드 | 타입 | 의미 |
  |------|------|------|
  | **vendorItemId** | Number | **옵션아이디 — 결합키**. "vendorItemId 단위로 반품 접수" |
  | vendorItemName | String | 옵션명 |
  | **cancelCount** | Number | 취소 수량 (부분반품 가능 → 반드시 확인) |
  | purchaseCount | Number | 원 주문 수량 |
  | shipmentBoxId | Number | 원 배송번호 |
  | sellerProductId | Number | 업체등록상품번호 |
  | sellerProductName | String | 업체등록상품명 |
  | vendorItemPackageId | Number | 딜번호 |
  | releaseStatus | String | Y(출고됨)·N(미출고)·S(출고중지됨) |

### 1-2. 반품요청 단건 조회 [READ]
- **Path**: `GET /v2/providers/openapi/apis/api/v6/vendors/{vendorId}/returnRequests/{receiptId}`
- **Path param**: receiptId (Number, **반품접수번호만** — 취소번호 미지원)
- **응답**: 1-1과 동일 구조의 단건 (data가 배열 1건)

### 1-3. 반품철회 이력 기간별 조회 ★ [READ — 차감 정확도]
- **Path**: `GET /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/returnWithdrawRequests`
- **Query**:
  | 이름 | 필수 | 타입 | 설명 |
  |------|:---:|------|------|
  | dateFrom | O | String | 조회 시작일 `yyyy-MM-dd` |
  | dateTo | O | String | 조회 종료일 `yyyy-MM-dd` |
  | pageIndex | | Number | default 1. 마지막 페이지 시 빈값 |
  | sizePerPage | | Number | default 10, 최대 100 |
- **응답 data[]**: cancelId(Number, 반품접수번호=receiptId), orderId, vendorId, refundDeliveryDuty(COM업체·CUS고객·COU쿠팡), createdAt(철회시각), **vendorItemIds[]**(철회된 옵션ID 목록), nextPageIndex
- **용도**: 철회된 반품은 순매출 차감에서 **제외**. (receiptId/vendorItemId로 매칭 제외)

### 1-4. 반품철회 이력 접수번호로 조회 [READ — POST]
- **Path**: `POST /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/returnWithdrawList`
- **Body**: `{ "cancelIds": [87033689] }` (한 번에 최대 50개, number 타입)
- **응답 data[]**: cancelId, orderId, vendorId, refundDeliveryDuty, createdAt, vendorItemIds[]
- ⚠️ **POST + body** → `_base._request`에 optional body 지원 추가 필요(아래 §3).

### 1-5. 반품상품 입고 확인처리 [WRITE — stub]
- **Path**: `PATCH/PUT /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/returnRequests/{receiptId}/receiveConfirmation`

### 1-6. 반품요청 승인 처리 [WRITE — stub]
- **Path**: `PATCH/PUT /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/returnRequests/{receiptId}/approval`

### 1-7. 회수 송장 등록 [WRITE — stub]
- **Path**: `POST /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/return-exchange-invoices/manual`

---

## 2. 교환 API (4개) — 회계 영향 작음(참고), 읽기 1개만 구현

### 2-1. 교환요청 목록조회 [READ]
- **Path**: `GET /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/exchangeRequests`
- **Query**:
  | 이름 | 필수 | 타입 | 설명 |
  |------|:---:|------|------|
  | createdAtFrom | O | String | `yyyy-MM-ddTHH:mm:ss` |
  | createdAtTo | O | String | `yyyy-MM-ddTHH:mm:ss` |
  | status | | String | RECEIPT·PROGRESS·SUCCESS·REJECT·CANCEL (미지정 시 전체) |
  | orderId | | Number | 주문번호 |
  | nextToken | | String | 페이징 토큰 |
  | maxPerPage | | Number | default 10 |
- **응답 data[]**: exchangeId, orderId, vendorId, orderDeliveryStatusCode(ACCEPT·INSTRUCT·DEPARTURE·DELIVERING·FINAL_DELIVERY·NONE_TRACKING), exchangeStatus(RECEIPT·PROGRESS·SUCCESS·REJECT·CANCEL), referType, (exchangeItems[]에 vendorItemId 포함 — 운영용)

### 2-2. 교환요청상품 입고 확인처리 [WRITE — stub]
- **Path**: `PATCH/PUT /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/exchangeRequests/{exchangeId}/receiveConfirmation`

### 2-3. 교환요청 거부 처리 [WRITE — stub]
- **Path**: `PATCH/PUT /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/exchangeRequests/{exchangeId}/rejection`

### 2-4. 교환상품 송장 업로드 처리 [WRITE — stub]
- **Path**: `POST /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/exchangeRequests/{exchangeId}/invoices`

---

## 2.5 ★라이브 실측 보정 (2026-06-03, 서버 프로브 — 문서와 실제 차이)

문서(이름+섹션 레벨)와 라이브 동작이 달라, 서버 IP 프로브로 확정한 제약(추정 아닌 실측):

| 엔드포인트 | 문서 | **라이브 실측** | 대응 |
|------|------|------|------|
| 반품목록 RETURN | status optional | **status 필수** — 없으면 `400 "OrderId can't be null, if doesn't pass the parameter status"` | status별 순회(RU/UC/CC/PR) |
| 반품목록 CANCEL | — | status 없이 OK (31일도 OK) | 그대로 |
| 교환목록 | 기간 명시 없음 | **최대 7일** — `400 "createdAtTo - createdAtFrom should less then 7day"` | 7일 윈도우 |
| 반품철회 | 기간 명시 없음 | **최대 7일** — `400 "The maximum view duration is 7 days"` | 7일 윈도우 |
| 전반 | — | 호출 과다 시 **429** | 호출 간 0.3s 지연 + _base 429 재시도 |

- **RETURN status 필수**가 핵심: 전 상태를 덮으려면 RU·UC·CC·PR 4개 status로 각각 조회해 합집합. CANCEL은 status 미사용.
- 반품목록(returnRequests)은 31일 OK(CANCEL 31일 검증). 철회·교환만 7일.

## 3. 구현 메모 (P2 코드 설계 토대)

- **_base 확장**: 현재 `_request(method, path, params)`는 body 미지원. `returnWithdrawList`(POST+body) 위해 **optional `body: dict | None` 추가** (HMAC 서명은 datetime+method+path+query만 — body는 서명 제외, 기존 GET 동작 무변경). 후방호환.
- **읽기 구현 우선순위 (회계축)**: 1-1(목록) → 1-3(철회 기간별) → 1-2(단건) → 1-4(철회 접수번호 POST) → 2-1(교환 목록). 쓰기 6개는 stub.
- **계정 순회**: 트랙 D-8 — vendorId 2개(WING1=A01564720, WING2=A01029796) 각각 순회. RG는 같은 셀러계정이라 중복 불필요.
- **차감 로직(Harness)**: receiptType별로 (RETURN/CANCEL) returnItems[].vendorItemId×cancelCount 집계 → 철회분(returnWithdraw vendorItemIds) 제외 → 순매출 차감 사실 제공. 전략판단 없음(D-3).
- **31일 제약**: 목록 조회는 윈도우를 31일 이하로 끊어 순회.
- **status 코드표(반품)**: RELEASE_STOP_UNCHECKED·RETURNS_UNCHECKED·VENDOR_WAREHOUSE_CONFIRM·REQUEST_COUPANG_CHECK·RETURNS_COMPLETED.
- ⚠️ 라이브 호출은 **서버 IP에서만**(D-8, 로컬 403). 검증/실sync는 ssh oracle_vm.
