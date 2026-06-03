# 04. 쿠팡 수수료 전체 지도 + API 실측 비교 (회계 진짜 순이익)

> 조사일: 2026-06-03 · 출처: 쿠팡 공식(cloud.mkt.coupang.com·sell.coupang.com·developers.coupangcorp.com), /browse 직접 확인
> 목적: 오하이가 쿠팡 윙(3P)·로켓그로스(2P)에 **실제로 지불하는 모든 수수료**를 꿰뚫고, 우리 API가 찍어주는 실측값과 비교.
> 트랙 D-2 회계축(① 진짜 순이익) · D-3(사실 정리만, 전략 추천 없음). 페이즈 P4(정산) 연계.
> ⚠️ 공식 수수료율/금액은 수시 변동. 카테고리·계정별 최신값은 **API 실측이 진실 원천**(아래 §3).

---

## 1. 윙(3P, 판매자배송) — 우리가 내는 수수료

| 수수료 | 공식 내용 | 부과 기준 |
|------|------|------|
| **판매수수료** | 카테고리별 **4 ~ 10.8%** (오하이 휴대폰액세서리 대부분 7.8%) | 할인 적용된 **최종 판매가** 기준 |
| 결제수수료 | **별도 없음** — 카테고리 판매수수료에 포함 | (유료배송 배송비엔 약 3.3% 결제수수료) |
| **유료배송 배송비 수수료** | 배송비에 수수료 부과 (기본배송비·도서산간 각각) | 배송비 |
| **판매자 서비스이용료** | 월 매출 100만원↑ 셀러: 월 1회 **55,000원**(VAT 포함) | 월 정산 시 차감 |
| 쿠런티(couranteeFee) | 쿠런티 이용 시 % 부과 | 옵션 |
| 스토어 이용료(할인) | 셀러 스토어 이용료/할인 | 옵션 |
- 공식표 원문 저장: 본 조사 시 `cloud.mkt.coupang.com/Fee-Table` 전수 캡처(가전디지털 7.8%·패션 10.5%·주방 10.8%·반려 10.8% 등 대분류~소분류).
- **상품 등록 후 카테고리 변경 불가** → 잘못된 카테고리는 수수료율도 잘못 정산됨(신규 등록 필요).

## 2. 로켓그로스(2P, 쿠팡풀필먼트) — 우리가 내는 수수료 (현재 판매 0, 대비용)

2025-01-06 개편. 구성 4갈래:

| 수수료 | 공식 내용 |
|------|------|
| **판매수수료** | 윙과 **동일** 카테고리율(4~10.9%) |
| **입출고비 (a)** + **배송비 (b)** | **사이즈 6단계**별 (판매가·카테고리도 영향): |
| | XS/극소형 입출고 600원~ / 배송 1,350원~ |
| | S/소형 650원~ / 1,550원~ |
| | M/중형 1,250원~ / 2,100원~ |
| | 대형1 1,375원~ / 2,200원~ · 대형2 1,375원~ / 4,100원~ · 특대형 1,375원~ / 5,600원~ |
| **보관비** | 매 입고 30일 무료(의류/신발/악세서리 45일), 이후 부과 |
| **반품 회수비** | 상품당, 매달 20건 무료 후 부과 |
| **반품 재입고비** | 수량당, 매달 20개 무료 후 |
| **반출비** | 수량당 (건당 약 300원), 매달 20개 무료 후 |
| 부가서비스 | 반출배송·바코드 부착·바코드 오류수정 |
- **사이즈 = 가로+세로+높이+무게**로 결정(단일상품 합 250cm·30kg 이내). → **D-5(RG 상품 API가 width/length/height/weight 제공)로 사이즈 등급 산출 → fulfillment 수수료 모델 가능**.
- RG 정산: "최종 판매가 − 판매수수료 − 추가비용(광고비 등) − RG 서비스이용비" 후 지급.

## 3. ★ API 실측 — 우리 API가 찍어주는 실제 수수료 (비교의 진실 원천)

### 3-1. 매출내역 조회 (revenue-history) — **옵션ID별 실제 수수료** [사용중·필드 미저장]
- Path: `GET /v2/providers/openapi/apis/api/v1/revenue-history` (recognitionDateFrom/To, **최대 ~7일 범위**, token 페이징)
- 핵심 필드(옵션 단위, vendorItemId 포함 → 광고·상품·주문·반품 축과 조인):
  | 필드 | 의미 |
  |------|------|
  | **serviceFeeRatio** | **실제 적용 판매수수료율(%)** ← 공식표와 직접 비교 |
  | serviceFee / serviceFeeVat | 판매수수료 금액/VAT |
  | saleAmount | 매출금액(판매액 − 쿠팡지원할인) |
  | **settlementAmount** | 정산금액 = 매출 − (서비스이용료+VAT) |
  | deliveryFee{amount,fee,feeVat,feeRatio,baseFee,remoteFee} | 배송비·배송비 수수료 상세 |
  | couranteeFee / storeFeeDiscount | 쿠런티·스토어 이용료(할인) |
  | 쿠팡지원할인·판매자할인쿠폰·다운로드쿠폰 | 할인 비용 |

### 3-2. 지급내역 조회 (settlement-histories) — **실제 통장 지급액** [미사용]
- Path: `GET /v2/providers/marketplace_openapi/apis/api/v1/settlement-histories`
- 월/주/추가/최종 정산 단위 집계:
  | 필드 | 의미 |
  |------|------|
  | salesAmount | 판매액+배송료−(취소+할인쿠폰) |
  | serviceFee | 판매수수료(+우대수수료 환급) |
  | settlementTargetAmount | 정산대상액 = 총판매액 − 판매수수료 |
  | settlementAmount | 지급액(주 70%/월 100%) |
  | **sellerServiceFee** | 판매자 서비스이용료(월 55,000원) |
  | deductionAmount | 정산차감 |
  | **finalAmount** | **최종지급액** = 지급액+보류해제−(전담택배비+서비스이용료+정산차감+전주채권+쿠런티+할인쿠폰+스토어이용료할인) |

### 3-3. 상품 조회 API — 등록 수수료율 [이미 저장중]
- `coupang_product_item.sale_agent_commission` = 상품 조회 API의 saleAgentCommission(옵션별 판매수수료%). 사전 등록값.

## 4. ★ 라이브 실측 비교 (2026-06-03, 서버 매출내역 — 원칙22 실증)

오하이 윙 실제 판매수수료율(API serviceFeeRatio) ↔ 공식표:

| 옵션ID | 매출 | API 실측율 | 수수료 | 정산 | 공식표 대조 |
|--------|------|:---:|------|------|------|
| 94156365576 | 12,900 | **7.8%** | 1,006 | 11,793 | 가전디지털 7.8% ✅ |
| 94365168294 | 179,000 | **10.5%** | 18,795 | 158,325 | 패션잡화 10.5% ✅ |
| 91716150288 | 15,010 | **6.4%** | 961 | 13,953 | (카테고리별) |
| 87286712272 | 18,900 | **7.8%** | 1,474 | 17,279 | 7.8% ✅ |
- **결론: 공식표 ↔ API 실측 일치.** API가 옵션별 실제율을 그대로 제공 → 비교/검증 가능 확인.

## 5. 비교 가능성 정리 (Jino 질문 답)

| 수수료 | 비교 방법 | 현재 가능? |
|------|------|:---:|
| 윙 판매수수료 | 공식 카테고리율 ↔ revenue-history `serviceFeeRatio`(실측) | ✅ 지금(윙 판매 있음) |
| 윙 배송비 수수료 | revenue-history `deliveryFee.fee/feeRatio` | ✅ |
| 윙 서비스이용료(월55k) | settlement-histories `sellerServiceFee`/`finalAmount` | ✅ (지급내역 연결 필요) |
| 할인쿠폰/쿠런티 | revenue-history 할인·courantee 필드 | ✅ |
| RG 판매수수료 | 공식(윙동일) ↔ (RG 매출 발생 시 정산) | ⏳ RG 판매 0 |
| RG 입출고/배송/보관/반품비 | 공식 사이즈표 + RG 상품 API 사이즈(D-5)로 **모델** ↔ 지급내역 실측 | ⏳ RG 판매 0(모델만) |

## 6. ★ 라이브 응답 실구조 확정 (2026-06-03 서버 프로브 — P4 구현 토대, 원칙22)

### 6-1. revenue-history 실응답 (서버 덤프 확정)
- **호출**: `GET /v2/providers/openapi/apis/api/v1/revenue-history` + params `{vendorId, recognitionDateFrom, recognitionDateTo, token, maxPerPage}`.
- **★`token` 파라미터 필수** — 누락 시 400(Bad Request). §4의 "30일은 400"은 token 누락 오진이었음. token="" 있으면 11일 구간도 code=200 확인. 페이징: `token=""` 시작 → 응답 `nextToken`/`hasNext`.
- **★인식일(recognitionDate) 기준** — saleDate(판매일)보다 한참 뒤. 최근 구간(어제~한달)은 0건, **2026-04 이전 인식 구간부터 데이터**. settlement-histories가 알려주는 `revenueRecognitionDateFrom/To`가 데이터 있는 구간.
- **응답 구조 = 주문(거래) 단위 + `items[]` 옵션 중첩** (옵션 그레인 필드는 `items[]` 안):
  - 최상위(거래): `orderId`, `saleType`(SALE/REFUND…), `saleDate`, `recognitionDate`, `settlementDate`, `finalSettlementDate`, `deliveryFee{amount,fee,feeVat,feeRatio,settlementAmount,baseAmount,baseFee,baseFeeVat,remoteAmount,remoteFee,remoteFeeVat}`
  - **`items[]`(옵션)**: `taxType`, `productId`(=sellerProductId), `productName`, **`vendorItemId`**, `vendorItemName`, `salePrice`, `quantity`, `coupangDiscountCoupon`, `discountCouponPolicyAgreement`, **`saleAmount`**, `sellerDiscountCoupon`, `downloadableCoupon`, **`serviceFee`**, `serviceFeeVat`, **`serviceFeeRatio`** ← D-10/D-11 핵심, **`settlementAmount`**, `couranteeFeeRatio`, `couranteeFee`, `couranteeFeeVat`, `storeFeeDiscountVat`, `storeFeeDiscount`, `externalSellerSkuCode`
  - 실측 1건: orderId=17100183465800, saleType=REFUND, items[0].vendorItemId=94365168294, serviceFeeRatio=**10.5**, serviceFee=18795, saleAmount=179000, settlementAmount=158325 (§4 패션 10.5%와 일치).
  - **REFUND 행은 환불** — deliveryFee/금액이 음수일 수 있음(사실 그대로 적재, D-3).

### 6-2. settlement-histories 실응답 (서버 덤프 확정)
- **호출**: `GET /v2/providers/marketplace_openapi/apis/api/v1/settlement-histories` + params `{vendorId, revenueRecognitionYearMonth:"YYYY-MM", maxPerPage}`.
- **★응답은 JSON 배열을 직접 반환** (code/data 래핑 없음 — SA에서 `isinstance(r, list)` 처리 필수). revenue-history와 다름.
- **인식 월(YYYY-MM) 단위 조회** — 2026-06/05는 빈 배열(미정산), **2026-04부터 데이터**. 정산은 주간(WEEKLY) 단위로 여러 행.
- **행 필드(정산 단위)**: `settlementType`(WEEKLY…), `settlementDate`, `revenueRecognitionYearMonth`, `revenueRecognitionDateFrom`, `revenueRecognitionDateTo`, `totalSale`, `serviceFee`, `settlementTargetAmount`, `settlementAmount`, `lastAmount`, `pendingReleasedAmount`, `sellerDiscountCoupon`, `downloadableCoupon`, `dedicatedDeliveryAmount`, **`sellerServiceFee`**(월 55k 위치), `couranteeFee`, `couranteeCustomerReward`, `deductionAmount`, `debtOfLastWeek`, `finalAmount`, `status`, `storeFeeDiscount`, (+`bankAccountHolder`/`bankName`/`bankAccount` = **PII, 저장 안 함**).
  - 실측 1건(2026-04): WEEKLY, settlementDate=2026-05-19, 인식 04-20~04-26, totalSale=182000, serviceFee=20774, settlementAmount=112858, deductionAmount=70274, finalAmount=0, status=DONE.

### 6-3. 아직 미확정 (P4 외 / 후속)
- [ ] RG fulfillment 사이즈 등급 ↔ 입출고/배송비 정확 매핑표(공식 상세 페이지, 카테고리·판매가 변수) — P3 로켓그로스에서.
- [ ] revenue-history 최대 조회기간 상한(11일 OK 확인, 그 이상 미검증) — Harness는 보수적 7일 윈도우.
