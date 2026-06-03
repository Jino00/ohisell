# 06. 쿠팡 쿠폰/캐시백 API 명세 (21개 — 전수 디테일)

> 수집일: 2026-06-03 · 출처: developers.coupangcorp.com 쿠폰/캐시백 섹션(360005046574), /browse --headed
> 트랙 D-15. 게이트웨이 `https://api-gateway.coupang.com`, HMAC. **3개 게이트웨이 패밀리 혼재**: openapi([도서]캐시백)·marketplace_openapi([다운로드쿠폰])·fms([즉시할인쿠폰]·공통 예산/계약).
> 용도: P5 쿠폰/캐시백(할인 비용). 조망 회계축 = 셀러 부담 할인액 차감. ⚠️ 오픽스(휴대폰 액세서리)는 [도서]캐시백 무관(트랙 D-7 — 자리만). 쓰기 다수(생성/파기) → 쓰기 페이즈 dry_run.

## A. [도서] 상품 캐시백 (3) — openapi 게이트웨이
| # | 이름 | 메서드·Path |
|:-:|------|------|
| 1 | 적용 | `POST /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/products/items/cashback` ⚠️쓰기 |
| 2 | 검색 | `GET /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/products/items/cashback` |
| 3 | 삭제 | `DELETE /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/products/items/cashback` ⚠️쓰기 |
- 오픽스 무관(도서 전용). 자리만 둠(D-7).

## B. (공통) 예산/계약서 (3) — fms 게이트웨이
| # | 이름 | 메서드·Path |
|:-:|------|------|
| 4 | 예산현황 조회 | `GET /v2/providers/fms/apis/api/v1/vendors/{vendorId}/budgets` |
| 5 | 계약서 단건 조회 | `GET /v2/providers/fms/apis/api/v2/vendors/{vendorId}/contract` |
| 6 | 계약서 목록 조회 | `GET /v2/providers/fms/apis/api/v2/vendors/{vendorId}/contract/list` |
- 쿠폰 예산·계약 현황(즉시할인쿠폰 운영 토대).

## C. [다운로드쿠폰] (5) — marketplace_openapi 게이트웨이
| # | 이름 | 메서드·Path |
|:-:|------|------|
| 7 | 생성 | `POST /v2/providers/marketplace_openapi/apis/api/v1/coupons` ⚠️쓰기 |
| 8 | 아이템 생성 | `PUT /v2/providers/marketplace_openapi/apis/api/v1/coupon-items` ⚠️쓰기 |
| 9 | 파기 | `POST /v2/providers/marketplace_openapi/apis/api/v1/coupons/expire` ⚠️쓰기 |
| 10 | 단건 조회(couponId) | `GET /v2/providers/marketplace_openapi/apis/api/v1/coupons/{couponId}` |
| 11 | 요청상태 확인 | `GET /v2/providers/marketplace_openapi/apis/api/v1/coupons/transactionStatus` |
- 고객이 다운로드하는 쿠폰. 생성→아이템 추가→(비동기)요청상태 확인 흐름.

## D. [즉시할인쿠폰] (10) — fms 게이트웨이
| # | 이름 | 메서드·Path |
|:-:|------|------|
| 12 | 생성 | `POST /v2/providers/fms/apis/api/v2/vendors/{vendorId}/coupon` ⚠️쓰기 |
| 13 | 아이템 생성 | `POST /v2/providers/fms/apis/api/v1/vendors/{vendorId}/coupons/{couponId}/items` ⚠️쓰기 |
| 14 | 파기 | `PUT /v2/providers/fms/apis/api/v1/vendors/{vendorId}/coupons/{couponId}` ⚠️쓰기 |
| 15 | 단건 조회(couponId) | `GET /v2/providers/fms/apis/api/v2/vendors/{vendorId}/coupon` |
| 16 | 단건 조회(couponItemId) | `GET /v2/providers/fms/apis/api/v1/vendors/{vendorId}/coupons/{couponId}/items/{couponItemId}` |
| 17 | 단건 조회(vendorItemId) | `GET /v2/providers/fms/apis/api/v1/vendors/{vendorId}/coupons/{couponId}/items/{vendorItemId}` |
| 18 | 목록 조회(status) | `GET /v2/providers/fms/apis/api/v2/vendors/{vendorId}/coupons` |
| 19 | 목록 조회(orderId) | `GET /v2/providers/fms/apis/api/v2/vendors/{vendorId}/{orderId}/coupons` |
| 20 | 아이템 목록 조회(status) | `GET /v2/providers/fms/apis/api/v1/vendors/{vendorId}/coupons/{couponId}/items` |
| 21 | 요청상태 확인 | `GET /v2/providers/fms/apis/api/v1/vendors/{vendorId}/requested/{requestedId}` |
- 즉시할인쿠폰(판매가 직접 할인). #17 vendorItemId 조회 = D-8 결합축(옵션별 할인). #19 orderId 조회 = 주문별 적용 할인.

---
## E. 읽기 응답 스키마 (전수 수집 2026-06-03, /browse 공식 — P5 구현 토대)

> ⚠️ 게이트웨이별 응답 래핑 다름: **fms = `{code, message, httpStatus, data:{success, content, pagination}}`** /
> **marketplace_openapi(다운로드쿠폰) = 직접 반환(code 래핑 없음, settlement-histories 패턴)**.

### #18 즉시할인쿠폰 목록 조회(status) — fms
`GET /v2/providers/fms/apis/api/v2/vendors/{vendorId}/coupons?status=APPLIED&page=1&size=10&sort=desc`
- status(필수): STANDBY/APPLIED/PAUSED/EXPIRED/DETACHED. page 기본1, size, sort(asc/desc).
- `data.content[]`: **contractId**, vendorContractId, **couponId**, **discount**(할인율), **endAt**("2018-04-05 23:59:00"), **maxDiscountPrice**, promotionName, **startAt**, **status**, **type**(RATE 정률/FIXED_WITH_QUANTITY 수량별정액/PRICE 정액), **wowExclusive**(false 전체/true 와우회원).
- `data.pagination`: countPerPage, currentPage, totalPages, totalElements.

### #15 즉시할인쿠폰 단건 조회(couponId) — fms
`GET /v2/providers/fms/apis/api/v2/vendors/{vendorId}/coupon?couponId=91`
- `data.content`(Object): #18 content와 동일 필드(단건).

### #17 즉시할인쿠폰 단건 조회(vendorItemId) — fms ★옵션별 결합(D-8)
`GET /v2/providers/fms/apis/api/v1/vendors/{vendorId}/coupons/{couponId}/items/{vendorItemId}?type=vendorItemId`
- `data.content`(Object): **couponItemId**, **couponId**, **vendorItemId**, startAt, endAt, **status**(STANDBY/APPLIED/PAUSED/EXPIRED).

### #16 즉시할인쿠폰 단건 조회(couponItemId) — fms
`GET .../coupons/{couponId}/items/{couponItemId}?type=couponItemId`
- `data.content`(Object): #17과 동일.

### #20 즉시할인쿠폰 아이템 목록 조회(status) — fms ★옵션 결합
`GET /v2/providers/fms/apis/api/v1/vendors/{vendorId}/coupons/{couponId}/items?status=APPLIED&page=1&size=10`
- ⚠️ page 기본 **0**(0-based, #18은 1-based).
- `data.content[]`: couponItemId, couponId, **vendorItemId**, startAt, endAt, status.

### #19 즉시할인쿠폰 목록 조회(orderId) — fms
`GET /v2/providers/fms/apis/api/v2/vendors/{vendorId}/{orderId}/coupons`
- `data.content[]`: contractId, couponId, discount, endAt, maxDiscountPrice, promotionName, (status/type 등).

### #21 즉시할인쿠폰 요청상태 확인 — fms
`GET /v2/providers/fms/apis/api/v1/vendors/{vendorId}/requested/{requestedId}`
- `data.content`(Object): couponId, requestedId, **status**(REQUESTED/FAIL/DONE), succeeded, total, type(COUPON_PUBLISH/COUPON_EXPIRE/COUPON_ITEM_PUBLISH/COUPON_ITEM_EXPIRE), failed, failedVendorItems[].

### #4 (공통)예산현황 조회 — fms
`GET /v2/providers/fms/apis/api/v1/vendors/{vendorId}/budgets?contractId=-1&targetMonth=2017-08`
- contractId(자유계약 -1), targetMonth(yyyy-MM, 생략 시 현재월).
- `data.content[]`: **contractId**, **targetMonth**, **vendorShareRatio**(분담율%), **totalBudgetAmount**(설정 예산), **usedBudgetAmount**(사용 예산) + pagination.

### #6 (공통)계약서 목록 조회 — fms
`GET /v2/providers/fms/apis/api/v2/vendors/{vendorId}/contract/list`
- `data.content[]`: **contractId**, vendorContractId, sellerId, **sellerShareRatio**, **coupangShareRatio**, gmvRatio, **start**, **end**, **type**(CONTRACT_BASED/NON_CONTRACT_BASED), usedBudget, modifiedAt, modifiedBy + pagination.

### #5 (공통)계약서 단건 조회 — fms
`GET /v2/providers/fms/apis/api/v2/vendors/{vendorId}/contract?contractId=9962`
- `data.content`(Object): #6과 동일(단건).

### #10 다운로드쿠폰 단건 조회(couponId) — marketplace_openapi (직접 반환)
`GET /v2/providers/marketplace_openapi/apis/api/v1/coupons/{couponId}`
- 직접 Object: couponId, title, couponType, **couponStatus**(생성시 STANDBY), publishedDate, **startDate**, **endDate**("YYYY-MM-DD HH:MM:SS"), **appliedOptionCount**(적용 옵션수), **usageAmount**(사용량), **couponPolicies[]**{couponId, title, typeOfDiscount(RATE/PRICE), description, minimumPrice, discount, maximumDiscountPrice(RATE 최대/PRICE는 -1), maximumPerDaily}.

### #11 다운로드쿠폰 요청상태 확인 — marketplace_openapi (직접 반환)
`GET /v2/providers/marketplace_openapi/apis/api/v1/coupons/transactionStatus?requestTransactionId=...`
- 직접 Object: **transactionStatusResponse**{type(COUPON_ITEM_PUBLISH 등), total, succeeded, **status**(FAIL/...), requestedId, couponFailedVendorItemIdResponses[]{vendorItemId, failureReason}, failed, couponId}.

### #2 [도서] 상품 캐시백 검색 — openapi (오픽스 무관, D-7 자리만)
`GET /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/products/items/cashback`

### 다운로드쿠폰 운영 현황 sync 제약 ⚠️
- 다운로드쿠폰(C그룹)은 **목록 조회 API가 없음**(생성/아이템생성/파기/단건조회/요청상태 5개뿐). couponId를 외부에서 알아야 단건 조회 가능 → **자동 sync 불가**. 운영 현황 동기화는 **즉시할인쿠폰(#18 상태별 목록) + 예산/계약(#6·#4) 중심**. 다운로드쿠폰 SA는 구현하되 couponId 주어질 때만 조회.

---
## 구현 메모 (P5)
- coupons.py(21 SA): 읽기 #2·#4·#5·#6·#10·#11·#15~#21 구현 + 쓰기 #1·#3·#7·#8·#9·#12·#13·#14 stub(쓰기 페이즈 dry_run).
- **조망 회계축 연결**: 셀러 부담 할인 비용 = 즉시할인쿠폰 적용액(#17 옵션별·#19 주문별). 단, 정산(P4) revenue-history의 `seller_discount_coupon`/`coupang_discount_coupon`에 이미 실측 차감액이 잡힘(04 §3) → P5는 쿠폰 운영 현황 보조. 실제 할인 비용 차감은 정산이 진실(D-3).
- **DB(P5)**: `coupang_coupon`(couponId 그레인, 즉시+다운로드 통합) · `coupang_coupon_item`(couponItemId/vendorItemId 그레인, D-8 결합축) · `coupang_coupon_budget`(contractId+targetMonth 그레인, 예산+계약 메타).
- ⚠️ 쓰기 본문 스키마는 구현 시 각 article 재확인(추정 금지). 게이트웨이 3종(openapi/marketplace_openapi/fms) 서명은 _base 동일(HMAC).
