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
