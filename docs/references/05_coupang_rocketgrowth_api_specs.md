# 05. 쿠팡 로켓그로스(RocketGrowth) API 명세 (P3 도메인)

> 수집일: 2026-06-03 · 출처: developers.coupangcorp.com 로켓그로스 API 섹션(sections/35157469062553), /browse --headed 직접 확인(Cloudflare 우회)
> 목적: 트랙 P3 로켓그로스 도메인 — ① 상품조회=사이즈(보관비 원가 토대, D-5) ② 로켓창고 재고(재고관리) ③ RG 주문(향후 RG 매출). clients/coupang/rocketgrowth.py(9 SA) 구현 명세.
> ⚠️ 모든 path는 게이트웨이 `https://api-gateway.coupang.com` 기준. 인증 = HMAC-SHA256 (기존 `_base._request` 재사용).
> ⚠️ 트랙 D-8: 호출은 **서버 IP에서만**(로컬 403, IP 화이트리스트). 실데이터 검증은 ssh oracle_vm.
> ⚠️ **현재 RG 실데이터 0**(트랙 D-4: 로켓그로스 2P 판매 0) — 라이브 검증은 상품/재고 응답(0건 정상) 확인까지, 주문은 RG 활성화 시점에.
> 결합축: 모든 SA가 **vendorItemId**(옵션ID) 반환 → 기존 광고⨝상품⨝주문⨝반품⨝수수료 결합축(D-8)과 동일. RG 사이즈·재고가 같은 축으로 결합 엔진(intelligence.py)에 합류.

---

## 0. 9개 엔드포인트 요약 (전수)

| # | SA | 메서드·Path | 용도 | P3 우선 |
|:-:|------|------|------|:-:|
| 1 | 상품 조회 (RG/하이브리드) | `GET /v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{sellerProductId}` | ★사이즈(보관비 원가)·vendorItemId | 읽기★ |
| 2 | 상품 목록 페이징 (RG) | `GET /v2/providers/seller_api/apis/api/v1/marketplace/seller-products?businessTypes=rocketGrowth` | sellerProductId 열거 → #1 입력 | 읽기★ |
| 3 | 로켓창고 재고 | `GET /v2/providers/rg_open_api/apis/api/v1/vendors/{vendorId}/rg/inventory/summaries` | ★옵션별 주문가능재고·30일판매 | 읽기★ |
| 4 | RG 주문 목록(쿼리) | `GET /v2/providers/rg_open_api/apis/api/v1/vendors/{vendorId}/rg/orders` | 기간 RG 주문 목록(향후 매출) | 읽기 |
| 5 | RG 주문 단건 | `GET /v2/providers/rg_open_api/apis/api/v1/vendors/{vendorId}/rg/order/{orderId}` | orderId 단건 조회 | 읽기 |
| 6 | 상품 생성 (RG) | `POST /v2/providers/seller_api/apis/api/v1/marketplace/seller-products` | 상품 등록 ⚠️쓰기 | stub(쓰기 페이즈) |
| 7 | 상품 수정 (RG) | `PUT /v2/providers/seller_api/apis/api/v1/marketplace/seller-products` | 상품 수정 ⚠️쓰기 | stub(쓰기 페이즈) |
| 8 | 카테고리 메타 정보 조회 | `GET /v2/providers/seller_api/apis/api/v1/marketplace/meta/category-related-metas/display-category-codes/{displayCategoryCode}` | 카테고리 등록속성 | stub(P6/category.py 공유) |
| 9 | 카테고리 목록 조회 (RG) | `GET /v2/providers/seller_api/apis/api/v1/marketplace/meta/display-categories?registrationType=RFM` | RG 운영 카테고리 | stub(P6/category.py 공유) |

- **속도제한**: rg_open_api 계열(#3·#4·#5)은 **분당 50회 이하** 명시. `_base._request`가 429 재시도 처리하나, Harness에서 호출 빈도 조절 권장.
- **#1·#2·#6·#7·#8·#9는 seller_api 게이트웨이**(기존 products.py와 동일 path 패밀리), **#3·#4·#5는 rg_open_api 게이트웨이**(신규 path 패밀리).
- #8·#9는 카테고리 도메인(P6 category.py)과 **path가 겹침**(RG는 registrationType=RFM 파라미터만 다름). P3에서는 stub로만 두고 P6에서 본 구현.

---

## 1. 상품 조회 (RG/하이브리드) — ★사이즈·옵션ID (#1)

- **Path**: `GET /v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{sellerProductId}`
- **Path 파라미터**: `sellerProductId`(O, Number) — 등록상품ID(상품 생성 결과값)
- Request body: 없음
- **유의(공식 원문)**: 로켓그로스 또는 판매자배송/로켓그로스 동시운영 상품에 대해서만 **신규 response 스키마**(아래 `rocketGrowthItemData`)로 응답. 순수 판매자배송 상품은 기존 상품조회 스키마로 응답. **End point는 기존 WING 상품조회와 동일**.

### 응답 핵심 구조 (조망에 쓰는 것만)
```
data
├── sellerProductId, sellerProductName, displayProductName, displayCategoryCode
├── vendorId, brand, brandId, generalProductName, statusName
└── items[]  (업체상품옵션목록)
    ├── itemName, offerCondition, ...
    ├── rocketGrowthItemData   ← ★RG 옵션 (없으면 마켓플레이스 전용 옵션)
    │   ├── sellerProductItemId (Number, 벤더아이템 옵션ID)
    │   ├── vendorItemId        (Number, ★옵션ID = D-8 결합축. 임시저장이면 null, 승인완료 시 표시)
    │   ├── itemId              (Number)
    │   ├── externalVendorSku   (String, 판매자SKU)
    │   ├── barcode, modelNo
    │   ├── skuInfo             ← ★사이즈/중량 (보관비 원가 토대, D-5)
    │   │   ├── width   (Number, mm)
    │   │   ├── length  (Number, mm)
    │   │   ├── height  (Number, mm)
    │   │   ├── weight  (Number, g)   기본 중량
    │   │   ├── netWeight (Number, g) 순중량
    │   │   ├── fragile (Boolean)     취급주의
    │   │   ├── quantityPerBox (Number) RG는 항상 1
    │   │   ├── distributionPeriod (Number) 유통기간(일)
    │   │   └── expiredAtManaged, producedAtManaged, manufacturedAtManaged, hazardous, heatSensitive, year, season, ... (대부분 null)
    │   └── priceData { originalPrice (Number), salePrice (Number) }
    └── marketplaceItemData    ← 판매자배송 옵션(RG 전용 상품은 null). 구조: sellerProductItemId·vendorItemId·externalVendorSku·maximumBuyCount·priceData
```

### Response Example (공식, 사이즈 실값)
```json
{
  "code": "SUCCESS", "message": "",
  "data": {
    "sellerProductId": 30100306501,
    "sellerProductName": "test openapi rg only",
    "displayCategoryCode": 69515,
    "vendorId": "A00013264",
    "items": [
      {
        "offerCondition": "NEW", "itemName": "s",
        "rocketGrowthItemData": {
          "sellerProductItemId": 30100598574,
          "vendorItemId": 72300263641,
          "itemId": 4300564093,
          "externalVendorSku": "DUMMY_EXT_SKU_2",
          "modelNo": "DUMMY_MODEL_NO_2",
          "skuInfo": {
            "fragile": false, "height": 141, "length": 365, "width": 341,
            "weight": 1042, "netWeight": 937,
            "quantityPerBox": 1, "distributionPeriod": 400, "expiredAtManaged": true
          },
          "priceData": { "originalPrice": 0, "salePrice": 900000000 },
          "barcode": "DEFG1231456811"
        },
        "marketplaceItemData": null,
        "outboundShippingTime": 24
      }
    ]
  }
}
```

- **조망 활용**: skuInfo(부피·중량) → 보관비 원가 정확화(D-5). vendorItemId로 기존 결합엔진 합류.
- ⚠️ `code`는 예시상 `"SUCCESS"` 문자열(상품 도메인 계열). #3~#5는 `200` 숫자 — SA마다 code 타입 다름(방어 필요).

---

## 2. 상품 목록 페이징 조회 (RG/하이브리드) — sellerProductId 열거 (#2)

- **Path**: `GET /v2/providers/seller_api/apis/api/v1/marketplace/seller-products`
- **Query 파라미터**:
  | Name | Req | Type | 설명 |
  |------|:-:|------|------|
  | `vendorId` | O | Number | 판매자 ID (Axxxx) |
  | `nextToken` | | Number | 페이지 키. 첫 호출 시 미입력 또는 1 |
  | `maxPerPage` | | Number | 페이지당 건수. 기본 10, **최대 100** |
  | `sellerProductId` | | Number | 등록상품ID 필터 |
  | `sellerProductName` | | String | 등록상품명(20자 이하) |
  | `status` | | String | IN_REVIEW/SAVED/APPROVING/APPROVED/PARTIAL_APPROVED/DENIED/DELETED |
  | `manufacture` | | String | 제조사 |
  | `createdAt` | | String | "yyyy-MM-dd" |
  | `businessTypes` | O* | String | **`"rocketGrowth"` 지정 시 RG 상품 또는 RG/마켓플레이스 Hybrid 목록**. 미지정 시 기존(마켓플레이스) 형식 |
- **응답**: `{code, message, nextToken(없으면 빈문자열), data:[{sellerProductId, sellerProductName, displayCategoryCode, ...}]}`
- **기존 WING product_sync와 같은 path** — 차이는 `businessTypes=rocketGrowth` 파라미터뿐. RG 상품 열거 → 각 sellerProductId를 #1에 넣어 사이즈/옵션 획득.

---

## 3. 로켓창고 재고 API — ★옵션별 실재고 (#3)

- **Path**: `GET /v2/providers/rg_open_api/apis/api/v1/vendors/{vendorId}/rg/inventory/summaries`
- **Path 파라미터**: `vendorId`(O, String, Axxxx)
- **Query 파라미터**:
  | Name | Req | Type | 설명 |
  |------|:-:|------|------|
  | `vendorItemId` | | String | 단일 SKU 재고 조회. 지정 시 nextToken 무시 |
  | `nextToken` | | String | 페이징 토큰(이전 응답값). vendorId+nextToken이면 토큰 이후부터 반환 |
- **속도제한**: ★**분당 50회 이하** (초과 시 429)
- **응답**:
```json
{ "code": 200, "message": "SUCCESS",
  "data": [
    { "vendorId": "A00123456", "vendorItemId": 70000000000, "externalSkuId": 10012345,
      "inventoryDetails": { "totalOrderableQuantity": 10 },
      "salesCountMap": { "SALES_COUNT_LAST_THIRTY_DAYS": 15 } }
  ],
  "nextToken": "2" }
```
- 필드: `data[].vendorItemId`(★결합축), `externalSkuId`, `inventoryDetails.totalOrderableQuantity`(주문가능 총수량), `salesCountMap.SALES_COUNT_LAST_THIRTY_DAYS`(최근30일 판매수량)
- **조망 활용**: 옵션별 로켓창고 실재고 → 재고관리(InventoryPage 연동). vendorItemId 직결.
- ⚠️ `code`가 예시상 `200`(숫자)·`message`="SUCCESS". 빈 `data:[]`는 정상(재고 없음).

---

## 4. RG 주문 목록(쿼리) — 기간 조회 (#4)

- **Path**: `GET /v2/providers/rg_open_api/apis/api/v1/vendors/{vendorId}/rg/orders`
- **Path 파라미터**: `vendorId`(O, String)
- **Query 파라미터**:
  | Name | Req | Type | 설명 |
  |------|:-:|------|------|
  | `paidDateFrom` | O | String | yyyymmdd (예 20240709) |
  | `paidDateTo` | O | String | yyyymmdd. ★**최대 30일** 윈도우 |
  | `nextToken` | | String | 페이징. 미반환 시 종료 |
- Order API는 **[출고일] 이후 주문**만 지원. **분당 50회** 제한.
- **응답**:
```json
{ "code": 200, "message": "SUCCESS",
  "data": [
    { "vendorId": "A00123456", "orderId": 70000000000, "paidAt": "1746093162000",
      "orderItems": [
        { "vendorItemId": 0, "productName": "...", "salesQuantity": 1, "unitSalesPrice": 27800, "currency": "KRW" }
      ] }
  ],
  "nextToken": "17189443970001139254740404666368" }
```
- 필드: `data[].{orderId, vendorId, paidAt(★ms epoch 문자열), orderItems[]}`. orderItems[].{vendorItemId(★), productName, salesQuantity, 단가, currency}
- ⚠️ **단가 필드 이름 불일치**: 필드표=`salesPrice`, 예시=`unitSalesPrice`. **둘 다 방어**(`unitSalesPrice or salesPrice`).
- ⚠️ `paidAt`는 목록 API에선 **ms epoch 문자열**(예 "1746093162000"), 단건 API(#5)에선 **ISO 문자열**("2024-06-02T17:13:27Z") — 다름.

---

## 5. RG 주문 단건 — orderId 조회 (#5)

- **Path**: `GET /v2/providers/rg_open_api/apis/api/v1/vendors/{vendorId}/rg/order/{orderId}`
- **Path 파라미터**: `vendorId`(O, String), `orderId`(O, Number) — 발주서 목록 조회로 획득
- **응답** (data가 ★객체 — 목록 API는 배열):
```json
{ "code": 200, "message": "SUCCESS",
  "data": {
    "vendorId": "A00123456", "orderId": 70000000000, "paidAt": "2024-06-02T17:13:27Z",
    "orderItems": [ { "vendorItemId": 0, "productName": "...", "salesQuantity": 1, "salesPrice": 15000, "currency": "KRW" } ]
  } }
```
- 단건은 `salesPrice` 사용·`paidAt` ISO. 에러: 400(주문번호 오류·타 판매자 주문 조회 불가).

---

## 6. 쓰기·카테고리 (P3 stub — 본 구현은 쓰기 페이즈/P6)

| # | SA | 메서드·Path | 비고 |
|:-:|------|------|------|
| 6 | 상품 생성(RG) | `POST .../marketplace/seller-products` | 라이브 등록 ⚠️ dry_run 안전장치(D-1). 본문 스키마는 #1 응답과 동형(items[].rocketGrowthItemData.skuInfo 필수). 쓰기 페이즈. |
| 7 | 상품 수정(RG) | `PUT .../marketplace/seller-products` | 라이브 수정 ⚠️. 쓰기 페이즈. |
| 8 | 카테고리 메타 | `GET .../marketplace/meta/category-related-metas/display-category-codes/{displayCategoryCode}` | 등록 가능 옵션/인증 메타. category.py(P6)와 공유. |
| 9 | 카테고리 목록(RG) | `GET .../marketplace/meta/display-categories?registrationType=RFM&locale=ko` | RG 운영 카테고리. `registrationType=RFM`이 RG 구분. category.py(P6)와 공유. |

- RFM = RocketGrowth Fulfillment(쿠팡 풀필먼트) 등록 타입.

---

## 6.5 보관비 원가 — 사이즈 등급 공식 (D-14, 공개 size-guide 확인 2026-06-03)

> 출처: `wing.coupang.com/tenants/rfm/settlements/size-guide`(공개) + `marketplace.coupang.com/rocketgrowth-fee-after-zerocostpromotion`(공개). 보관료 **단가**는 Wing 로그인 뒤(미확보 — 아래 ⚠️).

### 사이즈 6등급 경계 (✅ 확보 — 공개)
- **사이즈 = 개별포장 상품의 (가로+세로+높이) 3변 합(cm) AND 무게(kg)로 결정.**
- 등급(2025 개편): XS · S · M · L1 · L2 · XL (04 문서의 극소/소/중/대형1/대형2/특대형과 동일)

| Size | 3변 합 (W+L+H, cm) | 무게 (kg) |
|------|------|------|
| XS | ~80 이하 | ~2 이하 |
| S | 80 초과 ~ 100 | 2 초과 ~ 5 |
| M | 100 초과 ~ 120 | 5 초과 ~ 10 |
| L1 | 120 초과 ~ 140 | 10 초과 ~ 15 |
| L2 | 140 초과 ~ 160 | 15 초과 ~ 20 |
| XL | 160 초과 ~ 250 | 20 초과 ~ 30 |

- **규칙(공식 원문)**: 3변 합·무게 **둘 다** 기준을 충족해야 해당 등급. 하나라도 초과하면 **더 큰 사이즈로 분류**(둘 중 max 등급). 예) 3변합 XS인데 무게 S → S로 분류.
- 최종 사이즈는 **FC 입고 시 실측**으로 확정. XL 초과 입고 불가.
- **API 사이즈와 단위 주의**: 상품조회 skuInfo는 **mm·g** (#1: width/length/height=mm, weight=g). 등급표는 **cm·kg**. 변환 필요: cm=mm/10, kg=g/1000. 3변합(cm) = (width+length+height)/10.

### ✅ 보관비 단가 = CBM(부피) 기준 (확보 2026-06-03, Wing 로그인 fee-details#storage-fee)
> ★핵심: **보관비는 사이즈 6등급(XS~XL)이 아니라 CBM(부피) 기준.** 사이즈 등급은 입출고/배송비용. 보관비는 부피만으로 결정 → skuInfo로 직접 계산 가능.

- **공식 원문**: "매 입고시 30일 무료, 이후 CBM당 부과. 상품 1개의 부피에 따라 보관일별 비용 부과."
- **CBM** = 가로(m) × 세로(m) × 높이(m). (1CBM = 셔츠 약 250개 부피). API skuInfo는 mm → **CBM = width_mm × length_mm × height_mm / 1,000,000,000** (10⁹). 공식 예시: 0.26m×0.32m×0.05m = 0.004CBM.
- **1 CBM당 일 보관비 (VAT 별도, 보관 기간 누적별)**:

  | 보관 기간 | 1CBM당 일 단가 |
  |------|------|
  | 1~30일 | 1,000원 |
  | 31~60일 | 2,000원 (31~45·46~60 동일) |
  | 61~120일 | 2,500원 |
  | 121~180일 | 3,500원 |
  | 181일 이상 | 5,000원 |

- **무료 프로모션 (~2027.01.31)**: 그 외 모든 상품 **30일 무료** / 의류·신발·악세서리 **45일 무료** (해당 기간 단가 0원). 2025-03-31까지 입고분은 60일 무료.
- **개당 일 보관비 = CBM × 위 단가**. 공식 예시 검증: 0.004CBM × 2,000원 = 8원/일(46~60일), × 2,500 = 10원(61~120), × 3,500 = 14원(121~180), × 5,000 = 20원(181일+). ✅ 역산 일치.
- **계산식**: `보관비(옵션,기간) = CBM × Σ(기간 구간별 단가 × 무료초과 보관일수)`. 누진(가산) 구조 — 오래 보관할수록 단가 상승.
- **⚠️ 계산 한계(사실, D-3)**: 실제 청구 보관비는 "옵션별 무료기간 초과 보관일수 × CBM × 단가". 로켓창고 재고 API(#3)는 `totalOrderableQuantity`·`sold30d`만 주고 **입고일/보관경과일은 안 줌**. → 정확한 실청구 보관비는 **정산(settlement, P4)에 나타남**(현재 RG 정산 희소). API 사이즈로 산출 가능한 것 = **"옵션 단위 부피(CBM) + 보관일 가정 시나리오별 보관비 모델값"**(예: 현재고 × CBM × 31~60일 단가 = 월 보관비 추정). 실청구 대사는 정산 데이터로.
- **입출고/배송비용**(별건): 사이즈 6등급 + **카테고리·판매가 의존** → Wing 계산기(category 선택)로만 정확. 공개 시작가만: XS 600/1350 · S 650/1550 · M 1250/2100 · L1 1375/2200 · L2 1375/4100 · XL 1375/5600(원~). 정확 모델은 별도(P3 범위 밖 — 보관비가 D-14 초점).

### ⚠️ 입고일/보관경과일 — 공식 API 없음, Wing 내부 API에만 있음 (재확인 2026-06-03)
- **공식 Open API(HMAC)엔 입고 엔드포인트 없음** — RG 9개·물류센터 8개 전부 확인. 로켓창고 재고(#3)는 `totalOrderableQuantity`+`sold30d`만, 입고일/보관경과일 없음.
- **Wing 내부 API(비공식)엔 있음**: `GET https://wing.coupang.com/tenants/rfm-inbound/data/inbound/search?pagingSize&pageIndex`(세션쿠키 인증). 네트워크 캡처 실확인 — 응답에 `skuDetails[].plannedSku.{vendorItemId, skuId, vendorInventoryId, requestedQty, weight, barcode}` · `receivedQty/stowedQty` · **`shipmentStatusHistory`(CREATED→PO_CREATED→SHIPMENT_CREATED→INIT_COMPLETED, updatedAt ms 타임스탬프)** · `parcelConfig.totalVolume`(CBM m³) · `invoiceNumber`·택배사. 즉 입고 생성/완료 일시·CBM이 여기 있음.
- ⚠️ **그러나 비공식·미문서화·세션쿠키 기반**(광고비 XLSX·스크래핑 성격). 예고 없이 변경 가능. **Jino 결정: 공식 API만 사용**(안정성·유지보수). 이 내부 API는 **쓰지 않음** — 다음 세션이 재조사하지 말 것.
- **결론**: 순이익 보관비 = **정산(settlement, P4) 실측이 정답**(실제 청구액). P3가 적재하는 CBM은 ① 미래 토대 ② 모델 추정값(별도 정보 지표, 순이익에 안 섞음). 입고일 필요시 향후 Wing 입고 XLSX 수동업로드(D-4 방식)가 별도 옵션이나 현재 범위 밖.

## 7. P3 구현 메모 (다음 단계)
- **clients/coupang/rocketgrowth.py** (9 SA): 읽기 5(#1~#5) 구현 + 쓰기 2(#6·#7)·카테고리 2(#8·#9) stub. `CoupangBaseClient` 상속, raw 반환, 하드실패 `CoupangReadError` 표면화(settlement.py 패턴).
  - #1·#2는 seller_api path(기존 products.py와 동일 게이트웨이) / #3·#4·#5는 rg_open_api path(신규).
  - 페이징: #2 nextToken(Number)·#3 nextToken(String)·#4 nextToken(String). settlement.iter_revenue_history 토큰 순회 패턴 재사용.
- **WING≡RG 계정 동일**(D-8 제약3): A01564720=WING1=RG1, A01029796=WING2=RG2. RG 동기화는 WING1·WING2 크레덴셜 2계정 순회로 커버.
- **DB**: 사이즈는 기존 `coupang_product_item`에 컬럼 추가(width/length/height/weight/netWeight) 또는 신규 테이블 검토. 재고는 신규 테이블(옵션별 totalOrderableQuantity) 또는 기존 재고 테이블 연동. RG주문은 신규 테이블 또는 기존 Order에 channel 구분. ← P3 설계에서 확정.
- **Harness**: inventory_sync.py(로켓창고 재고) + 사이즈는 product_sync 확장 또는 신규. 결합엔진(intelligence.py)에 사이즈→보관비, 재고→재고축 합류는 D-N 결정 후.
- ⚠️ **RG 실데이터 0**: 라이브 검증은 "API가 정상 응답(빈 목록 포함)" 확인까지. 실제 사이즈/재고 행 적재는 RG 상품이 있어야 함 — 서버 프로브로 실응답(빈/실데이터) 먼저 확인 필요(원칙22).
