# 02. 쿠팡 상품 API 정밀 명세 (P1 — 읽기 결합축)

> 수집일: 2026-06-02 · 출처: developers.coupangcorp.com (공식 포털, /browse --headed 직접 확인)
> 범위: 상품 API 22개 중 **종합 조망 결합축(옵션ID↔상품·가격·재고·원가)** 읽기 5개를 정밀 수집.
> 나머지 17개(쓰기/관리)는 §3 URL 목록만. 페이즈 진행 시 같은 방식으로 정밀 수집.
> ⚠️ 모든 path는 게이트웨이 `https://api-gateway.coupang.com` 기준. 인증 = HMAC-SHA256 (기존 `_request` 재사용).

---

## 1. 결합 체인 (조망 엔진의 옵션ID 매핑 경로)

```
상품 목록 페이징 조회   → sellerProductId 수집 (상품 단위)
        │
        ▼
상품 조회 (by sellerProductId) → items[].vendorItemId(옵션ID) + salePrice + supplyPrice(원가) + maximumBuyCount(재고) + saleAgentCommission(수수료)
        │
        ▼
상품 아이템별 수량/가격/상태 조회 (by vendorItemId) → 실시간 amountInStock / salePrice / onSale
```
- 광고 XLSX의 옵션ID(vendorItemId)와 조인되는 키 = **vendorItemId**.
- vendorItemId는 "상품 목록"에는 없고 "상품 조회"의 items[]에서만 나옴 (승인완료 상품만 값 존재, 임시저장은 null).

---

## 2. 정밀 명세 (읽기 5개)

### 2-1. 상품 목록 페이징 조회 — `GET_PRODUCTS_BY_QUERY`
- **Path**: `GET /v2/providers/seller_api/apis/api/v1/marketplace/seller-products`
- **Query**:
  | 이름 | 필수 | 타입 | 설명 |
  |------|:---:|------|------|
  | vendorId | O | String | 판매자 ID (예: A00012345) |
  | nextToken | | Number | 다음 페이지 키. 첫 호출 미입력 또는 1 |
  | maxPerPage | | Number | 기본 10, 최대 100 |
  | sellerProductId | | Number | 등록상품ID 필터 |
  | sellerProductName | | String | 등록상품명 검색 (20자 이하) |
  | status | | String | IN_REVIEW/SAVED/APPROVING/APPROVED/PARTIAL_APPROVED/DENIED/DELETED |
  | manufacture | | String | 제조사 |
  | createdAt | | String | "yyyy-MM-dd" (해당일 00:00~23:59 조회) |
  | violationTypes / violationTypeAndOr | | String | NO_VA_V2/MOTA_V2/ATTR, AND/OR |
- **응답**: `{ code, message, nextToken, data: [...] }` (nextToken 빈문자열이면 마지막 페이지)
- **data[] 필드**: sellerProductId, sellerProductName, displayCategoryCode, categoryId, productId, vendorId, mdId, mdName, saleStartedAt, saleEndedAt, brand, statusName, createdAt
- ⚠️ **vendorItemId 없음** → 상품 조회로 내려가야 함.

### 2-2. 상품 조회 — `GET_PRODUCT_BY_PRODUCT_ID`
- **Path**: `GET /v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{sellerProductId}`
- **Path param**: sellerProductId (O, Number)
- **응답**: `{ code, message, data: { ...상품, items: [...] } }`
- **data(상품) 핵심**: sellerProductId, sellerProductName, displayProductName(노출명), brand, brandId, categoryId, productId, statusName, deliveryChargeType, deliveryCharge, returnCharge, manufacture
- **data.items[] 핵심 (조망 결합·회계축)**:
  | 필드 | 타입 | 의미 |
  |------|------|------|
  | **vendorItemId** | Number | **옵션ID (광고 XLSX 조인키)**. 승인완료 시에만 값, 임시저장 null |
  | sellerProductItemId | Number | 업체상품옵션아이디 |
  | itemName | String | 옵션명 |
  | **salePrice** | Number | 판매가격 |
  | originalPrice | Number | 할인율기준가 |
  | **supplyPrice** | Number | 공급가(원가성) — 응답 예시에 존재 (표 미기재, 예: 1111873) |
  | **maximumBuyCount** | Number | 판매가능수량(재고) |
  | **saleAgentCommission** | Number | 판매수수료(%) — 응답 예시에 존재 (예: 9) |
  | externalVendorSku | String | 판매자상품코드(우리 SKU). 발주서 응답에도 포함 |
  | barcode / modelNo | String | 바코드/모델번호 |
  | offerCondition | String | NEW/REFURBISHED/USED_* |
- ※ 응답이 매우 큼(images/notices/attributes/contents 포함). 조망용으로는 위 핵심 필드만 추출 저장.

### 2-3. 상품 조회 (승인불필요) — `GET_PARTIAL_PRODUCT_BY_PRODUCT_ID`
- **Path**: `GET /v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{sellerProductId}/partial`
- 2-2와 동일 구조이나 **승인 전(최신) 데이터 포함** 조회. 수정 전문 확보용. 조망 동기화는 2-2 사용(승인완료 기준).

### 2-4. 상품 아이템별 수량/가격/상태 조회 — `GET_PRODUCT_QUANTITY_PRICE_STATUS`
- **Path**: `GET /v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{vendorItemId}/inventories`
- **Path param**: vendorItemId (O, Number, 옵션ID)
- **응답**: `{ code, message, data: { sellerItemId, amountInStock, salePrice, onSale(bool) } }`
- 용도: 옵션ID 단위 **실시간** 재고/판매가/판매상태. 상품 조회의 items[]보다 가볍게 단건 최신화.

### 2-5. 상품 요약 정보 조회 — `GET_PRODUCT_BY_EXTERNAL_SKU`
- **Path**: `GET /v2/providers/seller_api/apis/api/v1/marketplace/seller-products/external-vendor-sku-codes/{externalVendorSkuCode}`
- **Path param**: externalVendorSkuCode (O, String, 우리 SKU)
- **응답**: `{ code, message, data: [ {sellerProductId, sellerProductName, categoryId, productId, brand, statusName, saleStartedAt, saleEndedAt, createdAt} ] }` (SKU 일치 상품 N개)
- 용도: 우리 SKU → sellerProductId 역매핑. 상품 등록 1분 후 호출.

---

## 3. 상품 API 22개 article URL (페이즈별 정밀 수집용 인덱스)

읽기(정밀 수집 완료 ✅) / 쓰기·관리(URL만, just-in-time):

| # | 이름 | article | 구분 |
|---|------|---------|:---:|
| 1 | 상품 목록 페이징 조회 | 360033645034 | ✅읽기 |
| 2 | 상품 조회 | 360033644994 | ✅읽기 |
| 3 | 상품 조회 (승인불필요) | 360042701211 | ✅읽기 |
| 4 | 상품 아이템별 수량/가격/상태 조회 | 360033645114 | ✅읽기 |
| 5 | 상품 요약 정보 조회 | 360033645094 | ✅읽기 |
| 6 | 상품 목록 구간 조회 | 360033645054 | 읽기(미수집) |
| 7 | 상품 등록 현황 조회 | 4404525347353 | 읽기(미수집) |
| 8 | 상품 상태변경이력 조회 | 360034156213 | 읽기(미수집) |
| 9 | 상품 생성 | 360033877853 | ⚠️쓰기 |
| 10 | 상품 승인 요청 | 360033644894 | ⚠️쓰기 |
| 11 | 상품 수정 (승인필요) | 360034156073 | ⚠️쓰기 |
| 12 | 상품 수정 (승인불필요) | 360042169352 | ⚠️쓰기 |
| 13 | 상품 삭제 | 360033644954 | ⚠️쓰기 |
| 14 | 상품 아이템별 수량 변경 | 360034156253 | ⚠️쓰기 |
| 15 | 상품 아이템별 가격 변경 | 360034156273 | ⚠️쓰기 |
| 16 | 상품 아이템별 할인율 기준가격 변경 | 360034156333 | ⚠️쓰기 |
| 17 | 상품 아이템별 판매 재개 | 360033645154 | ⚠️쓰기 |
| 18 | 상품 아이템별 판매 중지 | 360034156313 | ⚠️쓰기 |
| 19 | 자동생성옵션 활성화 (옵션 단위) | 27244057869209 | ⚠️쓰기 |
| 20 | 자동생성옵션 활성화 (전체 단위) | 27244235299609 | ⚠️쓰기 |
| 21 | 자동생성옵션 비활성화 (옵션 단위) | 27244841785497 | ⚠️쓰기 |
| 22 | 자동생성옵션 비활성화 (전체 단위) | 27246230561177 | ⚠️쓰기 |

- URL 패턴: `https://developers.coupangcorp.com/hc/ko/articles/{article}`
- 쓰기 7개(생성·수정·삭제·수량/가격/할인가 변경·판매중지/재개)는 P1 패키지에 **stub 슬롯**만 두고, 실제 구현은 별도 쓰기 페이즈에서 dry_run 안전장치와 함께.

---

## 4. W3a 단순 쓰기 9개 본문 스키마 (쓰기 페이즈, /browse 재수집 2026-06-04, D-1 추정금지)

> 수집일: 2026-06-04 · 출처: developers.coupangcorp.com (headed /browse 직접 확인)
> ★**전 9개 request body 없음(`not require body`)** — 모든 변수는 path segment 또는 query string.
> → W1/W2(body POST)와 SA 시그니처가 다름. guarded_write의 payload는 "변경 파라미터 미리보기"로 사용.
> 게이트웨이: 전부 `seller_api` (`/v2/providers/seller_api/apis/api/v1/marketplace`). 옵션ID(vendorItemId) 발급(승인완료) 후 사용 가능.

| # | 메서드 | HTTP | Path (마켓플레이스 base 이후) | params | body | 응답 code | API명 |
|---|--------|:---:|------|------|:---:|------|------|
| 14 | `update_item_quantity` | PUT | `/vendor-items/{vendorItemId}/quantities/{quantity}` | path: vendorItemId, quantity | 없음 | SUCCESS/ERROR | UPDATE_PRODUCT_QUANTITY_BY_ITEM |
| 15 | `update_item_price` | PUT | `/vendor-items/{vendorItemId}/prices/{price}` | path: vendorItemId, price(10원단위); query: forceSalePriceUpdate(bool), apMinSalePrice(<price), apActive(bool) | 없음 | SUCCESS/ERROR | UPDATE_PRODUCT_PRICE_BY_ITEM |
| 16 | `update_item_base_price` | PUT | `/vendor-items/{vendorItemId}/original-prices/{originalPrice}` | path: vendorItemId, originalPrice(0~,10원단위) | 없음 | SUCCESS/ERROR | UPDATE_PRODUCT_PRICE_INCL_DISCOUNT |
| 17 | `resume_item_sale` | PUT | `/vendor-items/{vendorItemId}/sales/resume` | path: vendorItemId | 없음 | SUCCESS/ERROR | RESUME_PRODUCT_SALES_BY_ITEM |
| 18 | `stop_item_sale` | PUT | `/vendor-items/{vendorItemId}/sales/stop` | path: vendorItemId | 없음 | SUCCESS/ERROR | STOP_PRODUCT_SALES_BY_ITEM |
| 19 | `enable_auto_option_item` | POST | `/vendor-items/{vendorItemId}/auto-generated/opt-in` | path: vendorItemId | 없음 | **SUCCESS/PROCESSING/FAILED** | UPDATE_PRODUCT_UP_BUNDLING_OPT_IN |
| 20 | `enable_auto_option_all` | POST | `/seller/auto-generated/opt-in` | 없음(셀러 단위, HMAC키로 식별) | 없음 | **SUCCESS/PROCESSING/FAILED** | UPDATE_SELLER_UP_BUNDLING_OPT_IN |
| 21 | `disable_auto_option_item` | POST | `/vendor-items/{vendorItemId}/auto-generated/opt-out` | path: vendorItemId | 없음 | **SUCCESS/PROCESSING/FAILED** | UPDATE_PRODUCT_UP_BUNDLING_OPT_OUT |
| 22 | `disable_auto_option_all` | POST | `/seller/auto-generated/opt-out` | 없음(셀러 단위) | 없음 | **SUCCESS/PROCESSING/FAILED** | UPDATE_SELLER_UP_BUNDLING_OPT_OUT |

### 핵심 설계 함의 (구현 반영)
- **body 없음** → SA는 path/query만 구성. `check_write_response`는 응답 code 검사로 성공판정(2xx면 _request가 body 반환).
- **자동옵션 4개(#19~22)는 code=PROCESSING이 정상**(비동기 처리중). `check_write_response(success_codes=(...,"PROCESSING"))`로 PROCESSING을 실패 오판하지 않게 확장. 나머지 5개는 SUCCESS/ERROR.
- **#15 가격변경 query**: `forceSalePriceUpdate=true`면 변경비율 제한(기존가 -50%~+100%) 해제. `apMinSalePrice`/`apActive`는 자동가격조정(반드시 함께 전달, apMinSalePrice<price). bool은 `"true"/"false"` 문자열로 직렬화(쿠팡 예시 일치).
- **#15 제약**: 자동생성옵션 가격은 직접 수정 불가(기준 판매자옵션 가격으로 조정). 10원 단위, 1원 단위 불가 → 쿠팡이 검증(추정 강제 안 함, D-1). 우리는 정수·양수 기본 검증만.
- **#20·#22 전체단위**: path에 vendorId조차 없음. HMAC access-key로 셀러 식별. → SA 시그니처에 vendor_item_id 없음.
- 공통: 옵션ID 삭제/미발급(임시저장 null)이면 400. 모니터링 판매중지 상품은 #17 재개 불가(쿠팡 CS 경유).

---

## 5. W3b 복잡 쓰기 5개 본문 스키마 (쓰기 페이즈, /browse 재수집 2026-06-04, D-1 추정금지)

> 수집일: 2026-06-04 · 출처: developers.coupangcorp.com (headed /browse 직접 확인)
> #9·#11·#12 = **body 있음(JSON)**. #10·#13 = no body.
> ⛔ **#13(삭제)는 시스템 정책으로 영구 차단** — SA·Harness·Router 3계층 모두 거부(Wing 직접 수행).
> 게이트웨이: 전부 `seller_api` (`/v2/providers/seller_api/apis/api/v1/marketplace`).

| # | 메서드 | HTTP | Path (마켓플레이스 base 이후) | body | 응답 code | API명 |
|---|--------|:---:|------|:---:|------|------|
| 9 | `create_product` | POST | `/seller-products` | **있음** | SUCCESS/ERROR | CREATE_PRODUCT |
| 10 | `request_approval` | PUT | `/seller-products/{sellerProductId}/approvals` | 없음 | SUCCESS/ERROR | APPROVE_PRODUCT |
| 11 | `update_product` | PUT | `/seller-products` | **있음** | SUCCESS/ERROR | UPDATE_PRODUCT |
| 12 | `update_product_partial` | PUT | `/seller-products/{sellerProductId}/partial` | **있음(부분)** | SUCCESS/ERROR | UPDATE_PRODUCT_PARTIAL |
| 13 | ~~`delete_product`~~ | DELETE | `/seller-products/{sellerProductId}` | 없음 | ⛔차단 | DELETE_PRODUCT |

### #9 상품 생성 (CREATE_PRODUCT) — body 필수 키

| 필드 | 필수 | 타입 | 설명 |
|------|:---:|------|------|
| sellerProductName | O | String | 등록상품명 (max 100자, 발주서용) |
| vendorId | O | String | 판매자ID (Wing 확인) |
| saleStartedAt | O | String | 판매시작일시 "yyyy-MM-dd'T'HH:mm:ss" |
| saleEndedAt | O | String | 판매종료일시 (2099년까지 가능) |
| deliveryMethod | O | String | SEQUENCIAL/COLD_FRESH/MAKE_ORDER/AGENT_BUY/VENDOR_DIRECT |
| deliveryCompanyCode | O | String | 택배사 코드 |
| deliveryChargeType | O | String | FREE/NOT_FREE/CHARGE_RECEIVED/CONDITIONAL_FREE |
| deliveryCharge | O | Number | 기본배송비 |
| freeShipOverAmount | O | Number | 조건부 무료배송 기준금액 (무료=0) |
| deliveryChargeOnReturn | O | Number | 초도반품배송비 |
| remoteAreaDeliverable | O | String | Y/N |
| unionDeliveryType | O | String | UNION_DELIVERY/NOT_UNION_DELIVERY |
| returnCenterCode | O | String | 반품지 센터코드 |
| returnChargeName | O | String | 반품지명 |
| companyContactNumber | O | String | 반품지 연락처 |
| returnZipCode | O | String | 반품지 우편번호 |
| returnAddress | O | String | 반품지 주소 |
| returnAddressDetail | O | String | 반품지 주소 상세 |
| returnCharge | O | Number | 반품배송비 |
| outboundShippingPlaceCode | O | Number | 출고지 주소코드 (묶음배송 필수) |
| vendorUserId | O | String | Wing 로그인 ID |
| requested | O | Boolean | 자동승인요청 여부 (true=즉시 승인요청) |
| items | O | List | 옵션 목록 (최대 200개) |
| displayCategoryCode | | Number | 노출카테고리코드 (없으면 자동매칭) |

**items[] 필수 필드**: itemName, originalPrice(할인율기준가), salePrice(판매가), maximumBuyCount(재고,max 99999), maximumBuyForPerson, maximumBuyForPersonPeriod, outboundShippingTimeDay, unitCount(단일=1), adultOnly(EVERYONE/ADULT_ONLY), taxType(TAX/FREE), parallelImported, overseasPurchased, pccNeeded, images(REPRESENTATION 필수), attributes(1개 이상), contents

**응답**: `{ code: "SUCCESS/ERROR", message, data: { code: "SUCCESS/ERROR", data: sellerProductId(Long) } }`
- ⚠️ HTTP 200 + code=ERROR 가능(속성 오류) → require_code=True로 fail-closed
- ⚠️ 승인 후 자동 승인반려 가능 → 생성 후 상태 확인 권장

### #10 상품 승인 요청 (APPROVE_PRODUCT) — no body

- '임시저장' 상태에서만 가능. `requested=true`로 생성하면 자동이므로 별도 불필요.
- 응답: `{ code: "SUCCESS/ERROR", message, data: sellerProductId_string }`

### #11 상품 수정 승인필요 (UPDATE_PRODUCT) — body 필수 키

- #9 create와 동일 body 구조 + **`sellerProductId`(필수, 상단)** + items[]에 `sellerProductItemId` 포함(수정) / 미포함(추가) / 제거(삭제)
- `vendorId`, `items` 필수. 승인완료 후 반영(immediately 아님).
- ⚠️ 승인완료 상품의 판매가·재고·판매상태·할인율기준가는 이 API가 아니라 #14~18 단순쓰기 사용.

### #12 상품 수정 승인불필요 (UPDATE_PRODUCT_PARTIAL) — 부분 body

- Path: `/seller-products/{sellerProductId}/partial` + body에도 `sellerProductId` 필수.
- 수정 가능 필드(전부 선택): deliveryMethod, deliveryCompanyCode, deliveryChargeType, deliveryCharge, freeShipOverAmount, deliveryChargeOnReturn, remoteAreaDeliverable, unionDeliveryType, returnCenterCode, returnChargeName, companyContactNumber, returnZipCode, returnAddress, returnAddressDetail, returnCharge, outboundShippingPlaceCode, outboundShippingTimeDay, sameDayShipping, pccNeeded, extraInfoMessage
- '임시저장·승인대기중' 상품은 수정 불가(쿠팡 400).
- 응답: SUCCESS/ERROR

### #13 상품 삭제 — ⛔ 시스템 정책으로 영구 차단

- **이 시스템(ohisell)에서는 상품 삭제 불가.** SA·Harness·Router 3계층 모두 `CoupangWriteValidationError` / HTTP 403.
- 삭제 조건(쿠팡 원칙): 승인대기 아니고 모든 옵션 판매중지 상태여야 가능 → 실수로 라이브 상품 삭제 방지를 위해 Wing에서만 허용.

### 핵심 설계 함의

- **body 있는 쓰기(#9·#11·#12)**: body dict는 Harness가 필수키 검증 후 SA에 전달. SA는 `_request(..., body=body)`로 JSON 전송.
- **fail-closed**: 전부 `require_code=True` (명세가 code 반환 보장).
- **retry 금지**: `retry_transient=False` (쓰기 재시도=중복실행 위험, D-16 공통).
- **삭제 차단 영구화**: D-16 확정 결정. 시스템 정책 변경 없이는 복원 불가.
