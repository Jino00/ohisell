# 17. 쿠팡 로켓그로스(RG) 풀필먼트 수수료 정책 + 자동적용 설계 토대

> 조사일: 2026-06-08 · 목적: RG 판매의 모든 수수료를 우리가 등록 속성(치수·무게·카테고리)으로 자동 계산·자동 대조하는 시스템 토대.
> 트랙: track_coupang-rg-replenishment 후속(회계 정확화). D-2 회계축·D-3(사실 정리).
> ⚠️ 금액은 수시 변동. 공식 진실원천 = 쿠팡 윙 판매자센터 로켓그로스 요금 페이지.

## 8. ★S5 status/api 필드 라이브 확정 (2026-06-09, 원칙22 — WING1 50필드 응답 캡처)
- **`totalFulfillmentFeeDeductionAmount` = 배송비(delivery)뿐**, "풀필먼트 합계"가 아님. 14개 리포트 전수 확인 +
  06-01~07 리포트가 §7 검산과 정확 일치: 배송 130,599(=ful) + 입출고 75,489(=warehousing) + 보관 168(=storage) = J 206,256.
  → **풀필먼트 J = delivery + warehousing + storage** (세 값 독립, 합산해도 이중계상 아님).
- **발생비용(f) basis 확정**: 이월(g)은 별도필드(`totalCarryOverSettlementDeductionAmount`,
  `pastDeductedCfsFeeDetails`, `totalPastCfsDeductionAmount`)로 분리 — 우리가 적재하는 7개 컴포넌트엔 미혼입.
  즉 적재값은 인식기간 gross 발생분(D-10 충족, f−g·최종지급액 아님).
- **status/api에 vendor_item_id 없음 재확인**: 50필드 전부 정산주기별 집계(옵션단위 귀속 불가) → 옵션단위는 S6 종류별 엑셀 필요.
- **profit-status/search, download-list/api는 status/api와 body 스키마 다름**(같은 body로 호출 시 HTTP 500).
  → 추정 금지(원칙22): 실제 요청을 브라우저 DevTools에서 캡처해야 함. **★2026-06-14 캡처·검증 완료 → §8-2 참조(블로커 해소).**
- **코드 반영(커밋 2c410c9)**: fee_type 'fulfillment'→'delivery' 리네임(alembic h2i3j4k5l6m7 UPDATE, stale 방지).
  D-11 광고비 dedup 규칙 코드화(sell_type='2P'=RG, RG정산 ad_sales 정본 → Phase2 플립 시 제외). codex 3R pass.

### 8-1. ★S5 종류별 엑셀 실증 (2026-06-09, 오픽스 WAREHOUSING_SHIPPING 엑셀 — S6 전제 확정)
- 파일: `A01564720-WAREHOUSING_SHIPPING-ko-*.xlsx`(윙 로켓그로스 정산현황 → 상세보기 → 입출고·배송비 엑셀).
- **2층 구조**: ① 상단 요약(정산주기 종료일·합계·세액·최종비용) ② 하단 **주문/SKU 단위 상세**(헤더 row7, 26컬럼).
- **★vendor_item_id 있음(S6 가능 확정)**: 상세 컬럼 = 정산유형·정산주기·세금계산서발행월·발생일(배송완료일)·**매출인식일**·거래유형·주문ID·배송ID·주문일·등록상품ID·**옵션ID(=vendor_item_id)**·SKU ID·등록상품명·옵션명·단품판매가·카테고리·개별포장사이즈·물류센터·판매수량·단품기준구매수량·옵션유형·입출고비(VAT별도: 발생비용A·할인가B·할인적용가A−B)·비고.
- **시트=비용종류별**(입출고비/배송비 2시트). 오픽스 1주(정산주기 06-07): 입출고 62행·배송 62행, 각 고유 옵션ID 4개.
- **★검산 완전 일치(원칙22)**: Σ옵션 **할인적용가(A−B)** = 요약 「합계」 = status/api 컴포넌트(VAT前). 입출고: Σ(A−B)=68,625 = 요약 입출고비합계 68,625; +세액 6,864 = 최종 75,489 = **status/api totalWarehousingFeeDeductionAmount(75,489)**. 배송도 동일 구조.
- **★S6 회계 규칙(중요)**: 옵션 귀속 cost = **할인적용가(A−B)** 사용(발생비용 A=gross 할인前은 status/api와 불일치 100,650≠68,625). VAT는 요약 세액으로 별도 gross-up. fee_type별 엑셀 분리 다운로드(8종).
- **다운로드 흐름**: 정산현황 행 「엑셀 다운로드 요청」(비동기 생성) → 우측상단 「정산관리 엑셀 다운로드 목록」에서 받음(download-list/api). 파일명=`{vendor_id}-{REPORT_TYPE}-ko-{uuid}.xlsx`. REPORT_TYPE 예: WAREHOUSING_SHIPPING(입출고·배송비).

### 8-2. ★S6-auto 다운로드 API 라이브 캡처·검증 완료 (2026-06-14, 원칙22 — 오픽스 WING1 DevTools 3요청+응답 전수 캡처)
3요청 전부 **현재 코드(`clients/coupang/rg_settlement.py`) body·응답 필드명과 정확히 일치** → 기존 "HTTP 500 블로커"는 stale 라벨이었고 코드가 실제로는 맞았음(라이브 확정). host=`wing.coupang.com`(데스크톱), UA=iPhone, same-origin, `x-xsrf-token`=XSRF-TOKEN 쿠키 더블서브밋.

1. **POST `/tenants/rfm/v2/settlements/request-download/api`** (엑셀 생성요청)
   - body: `{"sellerReportType":"WAREHOUSING_SHIPPING","requestTime":"<unix_ms 문자열, 내가 정함>","settlementGroupKeys":["A01564720-2026-06-08-2026-06-14"],"locale":"ko"}`
   - settlementGroupKey 형식 = `{vendorId}-{from}-{to}`(YYYY-MM-DD, 주 단위). 응답: `{requestId, vendorId, ...}`.
2. **POST `/tenants/rfm/v2/settlements/download-list/api`** (생성목록 폴링)
   - body: `{"requestTimeFrom":"<unix_ms>","requestTimeTo":"<unix_ms>"}` (UI는 ~2h 창; 우리 코드는 더 넓게 OK).
   - 응답=list: `[{vendorId, requestTime, requestedSettlementGroupKeys(null 가능), downloadStatus("PENDING"|"COMPLETED"), requestId, sellerReportType, recognitionDateFrom/To(null 가능)}]`. **완료판정=downloadStatus=="COMPLETED"**, 매칭=requestId, v2 호출키=requestTime.
3. **POST `/tenants/rfm/v2/settlements/download/api/v2`** (S3 주소 취득)
   - body: `{"requestTime":"<완료항목 requestTime>","locale":"ko"}`. 응답: `{"url":"<S3 presigned>","vendorId":"A01564720"}`.
   - S3 url: `rfm-common-prod.s3.ap-northeast-2.amazonaws.com/settlements/seller-report-download-v2/{vendorId}-{REPORT_TYPE}-ko-{uuid}.xlsx`. **`X-Amz-SignedHeaders=host`+`X-Amz-Expires=86400` → 인증헤더 없이 평범한 GET, 24h 유효**(어느 IP에서나 다운 가능 → prod도 직접 다운 가능, 단 만료 race 회피하려면 Mac이 받아 push 권장).
- **미검증(원칙22)**: 위는 Jino 데스크톱 Chrome(cf_clearance, wing.coupang.com)에서 확인. **우리 Playwright 페처**(판매분석은 m-wing 착지)에서 정산 페이지가 어느 origin에 착지하는지·same-origin fetch 200 여부는 S4 구현 시 라이브 실측 대상. `location.origin+경로`로 호출하면 호스트 자동 대응.
- **8종 sellerReportType 중 2종 확정**(WAREHOUSING_SHIPPING·CATEGORY_TR), 나머지 6종 코드명 미수집(드롭다운: 판매수수료·입출고/배송비·보관비·반품 회수/재입고·반출비·반출 배송 서비스비·재고 손실 보상·부가서비스비).

## 0. 핵심 문제 (왜 이 작업이 필요한가)
- 우리 종합조망 순이익은 **판매수수료 + 판매수수료 VAT(`total_fee`)만** 차감(intelligence.py `_agg_fees`).
- 오픽스 RG 90개 판매의 수수료(입출고비·배송비·보관비·반품비·RG서비스이용료)는 **전혀 안 들어감**.
- 실증: 오픽스 RG 옵션 31개 ∩ revenue_fee 옵션 4개 = **공집합**. RG 매출은 revenue-history(수수료 소스)에 안 잡힘.

## 1. RG 수수료 체계 (9종)
| # | 수수료 | 부과 방식 |
|---|---|---|
| 1 | 판매수수료 | 카테고리율 4~10.9%(Wing 동일), 최종판매가 기준 |
| 2 | 입출고비 | 사이즈 6단계별 정액 |
| 3 | 배송비(풀필먼트) | 사이즈 6단계별 정액 |
| 4 | 보관비 | 보관일수 구간별, 무료기간 후 |
| 5 | 반품 회수비 | 건당, 월 20건 무료 후 |
| 6 | 반품 재입고비 | 수량당, 월 20개 무료 후 |
| 7 | 반출비 | 수량당 300원, 월 20개 무료 후 |
| 8 | RG 서비스이용료 | 월 정산 차감(doc 04 line 41) |
| 9 | 부가서비스 | 반출배송·바코드 등(선택) |

## 2. 금액표 (2025.1.6 개편 / 프로모션 2027.1.31까지) — 출처: percenty·windly 블로그
### 입출고비·배송비 (사이즈 6단계)
| 사이즈 | 입출고비(정상) | 배송비(정상) | 입출고(프로모션) | 배송(프로모션) |
|---|---|---|---|---|
| 극소형 | 1,650 | 2,200 | 600~ | 1,350~ |
| 소형 | 1,750 | 2,400 | 650~ | 1,550~ |
| 중형 | 2,000 | 3,000 | 1,250~ | 2,100~ |
| 대형1 | 2,400 | 4,000 | 1,375~ | 2,200~ |
| 대형2 | 2,400 | 5,500 | 1,375~ | 4,100~ |
| 특대형 | 2,400 | 9,500 | 1,375~ | 5,600~ |
⚠️ 프로모션 금액 "~"는 카테고리·판매가 구간에 따라 변동. 정확표는 윙.

### 보관비 (보관일수 구간, 프로모션: 악세/의류/신발 45일 무료)
| 구간 | 0–30일 | 31–60일 | 61–90일 | 91–120일 | 121–180일 | 181일+ |
|---|---|---|---|---|---|---|
| 금액 | 1,000 | 2,000 | 2,500 | 2,500 | 3,500 | 5,000 |

### 반품·반출 (월 20건/개 무료 후)
- 반품 회수비: 675~ (정상 극소형 2,200~특대형 9,500)
- 반품 재입고비: 300~ (정상 600~)
- 반출비: 300원

## 3. 사이즈 등급 판정 (★미확정 키스톤)
- **부피무게 = (가로×세로×높이)/6000** (sellerking 가이드).
- RG 절대 한도: 포장단위 **30kg 이하, 세변합 250cm 이하**.
- ★**6단계 각각의 정확한 cm/kg 경계값은 공개 안 됨** — 윙 판매자센터에서만 확인. ← 다음 단계에서 확보.

## 4. ★어떤 API로도 RG 수수료는 항목별로 안 나온다 (직접 프로브 확정 2026-06-08)
- **Open API 지급내역(settlement-histories)**: `deductionAmount`를 통짜 lump sum으로만 줌. RG 입출고/배송/보관 분해 필드 없음. 오픽스 2026-03 실측: totalSale 264,000 − serviceFee 30,493 → 지급 163,455 − **deductionAmount 122,601** − 전주채권 40,854 = finalAmount 0. (deduction이 매출 46%, 단순수수료 아님 — 광고비+RG비용+이월 추정)
- **Open API revenue-history**: Wing 3P 판매수수료만. RG 매출 자체가 없음.
- **RG 상품 API(seller-products/{id})**: `rocketGrowthItemData.skuInfo`에 치수/무게만. **사이즈 등급도 수수료도 안 줌**. `rocketGrowthAdditionalInformation={"rfmInboundName":""}`.
- **결론**: RG 수수료 자동화 길은 둘 — (A) 우리가 치수→등급→금액표 모델 계산, (B) 윙 판매자센터 스크랩(입고 쿠키 인프라 재사용). 항목별 Open API는 없음.

## 5. 우리 데이터 가용성 (오픽스 RG 31옵션, prod 실측)
- 치수(width/length/height) 30/31, 무게 30/31 보유 → **(A)모델 입출고비·배송비 즉시 가능**.
- category_id 0/31 (전부 NULL) → 판매수수료율 산출엔 카테고리 보강 필요(또는 Wing동일상품 매핑).
- coupang_product_item에 width_mm·length_mm·height_mm·weight_g·net_weight_g·cbm 컬럼 이미 존재(rg_size_sync 적재).

## 7. ★윙 판매자센터 실증 (2026-06-08, 오픽스 로그인 — 원칙22 라이브 증거)
- **RG 수수료는 Open API 지급내역(Wing 3P)과 완전 별도의 정산 스트림**이다. 윙 좌측메뉴 정산 > **로켓그로스 정산현황**(`/tenants/rfm/settlements`)에 따로 있음. Open API settlement-histories의 deduction_amount(청바지 건)는 Wing 3P이고 RG와 무관.
- **로켓그로스 정산현황 구성**: 홈(수익현황) / 정산현황(정산리포트) / 광고비 내역 / 밀크런 내역.
- **수익현황 홈** (`/tenants/rfm/settlements/home`) — 오픽스 최근7일(06-01~07) 라이브:
  매출 1,199,900 − 비용 647,852(광고비 338,648 + 풀필먼트 206,254[배송비118,725·입출고비68,625·보관비154·VAT18,750] + 판매수수료 102,950) = 이익 552,048.
  - 내부 API: **`POST /tenants/rfm/v2/settlements/profit-status/search`** (기간 요약).
- **정산현황 정산리포트** (`/tenants/rfm/settlements/status-new`) — 정산주기(주별)별 항목 분해. 오픽스 2026-06-01~07 리포트(라이브, 검산 일치):
  - 판매액(a) 1,250,600 − 취소액(b) 50,700 = 매출(A) 1,199,900
  - 판매수수료(B) 102,950 (=8.58%) · 상계(C: 쿠폰) 0 · 판매기준매출액(D=A−B−C) 1,096,950
  - 지급액(H)=D×70% 767,865
  - 추가상계(I)= 밀크런(c) + 광고비(d) + 정산차감(e)
  - 쿠팡풀필먼트비용(J): 전체비용(f)=입출고비+배송비+보관비+반품회수비+반품재입고비+반출비+반출배송서비스비+바코드부가서비스비; 미납/기납부/이월(g) 조정 → 이번정산비용(f−g). 06-01~07: 입출고비 75,489 + 배송비 130,599 + 보관비 168 = J 206,256
  - 재고손실보상(K) · **최종지급액 = H − I − J + K** = 561,609 ✓(산식 검산 일치)
  - 내부 API: **`POST /tenants/rfm/v2/settlements/status/api`** (정산주기별 리포트, ~2.3KB JSON). 기준일 토글: 정산일/매출인식일, 7일/30일, 날짜범위.
  - ★각 리포트 **상세보기** 안에 **수수료 종류별 리포트 8종**(엑셀): 판매수수료 / **입출고·배송비** / 보관비 / 반품 회수·재입고 / 반출비 / 반출배송서비스비 / 재고손실보상 / 부가서비스비. ← **주문/SKU 단위 상세의 출처**(옵션 귀속 가능성, build 1단계에서 컬럼 확인).
  - 엑셀 생성·다운로드 센터 API: **`POST /tenants/rfm/v2/settlements/download-list/api`**(생성파일 목록 폴링). 비동기 생성(요청→생성완료→다운로드).
- **★Open API ↔ 내가 찾은 수수료 매칭 결론(Jino 질문 답)**: 윙 RG 정산의 비용 분류(입출고비·배송비·보관비·반품회수비·반품재입고비·반출비·바코드·판매수수료) = §1·§2에서 공개출처로 찾은 항목과 **1:1 일치**. Open API(Wing 3P)엔 이 RG 비용이 **전혀 없음** → 현재 종합조망은 오픽스 RG 비용 100% 누락. 자동화는 §7 내부 API(쿠키 인증, 입고 인프라 재사용)로 실제 청구액을 직접 수집하는 게 모델(A)보다 정확.
- **설계 함의**: 사이즈 등급표 역산(모델 A) 없이도, RG 정산 내부 API가 **쿠팡이 실제 청구한 항목별 금액**을 줌 → (B)직접 수집이 1순위. 모델(A)은 사전예측·과오청구 교차검증용 보조.
- **광고비 주의**: RG 광고비(d)는 이 RG 정산 안에도 있음(추가상계 I). 우리 ad_costs와 **이중계상 주의** — 회계 합산 시 출처 정합 필요.

## 6. 다음 단계 (이 세션 진행 중)
- [ ] 윙 판매자센터 RG 요금 페이지 → 사이즈 6단계 cm/kg 경계표 확보(§3 키스톤).
- [ ] 윙 정산 상세(2026-03) → `deductionAmount` 분해(광고비/RG비용/이월) 확인.
- [ ] **검증(Jino 지시)**: Open API 정산값(serviceFee·deductionAmount) ↔ §2 금액표로 모델 계산한 RG 수수료가 **매칭되는지** 대조. 맞으면 (A)모델 신뢰 확보.
- [ ] 설계: SA(RG 수수료 계산) → Harness(회계 합산) → 종합조망 net_profit 반영 + 스케줄러 자동화.

## 7. ★사이즈 유형 분류표 (S8 키스톤 — 라이브 확보 2026-06-09, Wing fee-details + marketplace)
출처: `https://wing.coupang.com/tenants/rfm/settlements/fee-details#warehousing-and-fulfillment-fee`(로그인) + marketplace.coupang.com/rocket-growth. **공식 표 원문 캡처.**

**측정 기준 = 개별 포장 상품의 가로+세로+높이(세변의 합, cm) AND 무게(kg).**

| 사이즈 유형 | 세변의 합(cm) | 무게(kg) | 입출고비 최소 | 배송비 최소 |
|---|---|---|---|---|
| 극소형 | ~80 이하 | ~2 이하 | 600원~ | 1,350원~ |
| 소형 | 80 초과 ~100 이하 | 2 초과 ~5 이하 | 650원~ | 1,550원~ |
| 중형 | 100 초과 ~120 이하 | 5 초과 ~10 이하 | 1,250원~ | 2,100원~ |
| 대형1 | 120 초과 ~140 이하 | 10 초과 ~15 이하 | 1,375원~ | 2,200원~ |
| 대형2 | 140 초과 ~160 이하 | 15 초과 ~20 이하 | 1,375원~ | 4,100원~ |
| 특대형 | 160 초과 ~250 이하 | 20 초과 ~30 이하 | 1,375원~ | 5,600원~ |

- **분류 규칙(핵심)**: 세변의 합과 무게를 **모두 충족**해야 해당 유형. **둘 중 하나라도 초과하면 상위 유형**으로 분류(= 두 기준 각각 분류 후 더 큰 등급 채택). 예: 세변 극소형 + 무게 소형 → **소형**.
- 특대형(세변 250cm/무게 30kg) 초과 = 입고 불가.
- **최종 사이즈 = 상품 최초 입고 시 물류센터 측정값 기준**(우리 등록 치수 ≠ 청구 사이즈일 수 있음 → **이게 과오청구 감사의 핵심 신호**).

### 배송비/입출고비 부과 규칙 (라이브 확보)
- **입출고비**: 수량 당 부과(주문 수량 당).
- **배송비**: 주문 상품 당 1회. 단 합포장 규칙:
  - **합포장 가능(개당 극소형~대형1)**: 배송비 1회 부과.
  - **합포장 불가(개당 대형2~특대형)**: 판매 수량 당 부과. 서로 다른 옵션 혼합 판매 시도 수량 당.
  - 합포장 시 **판매 총수량으로 사이즈 재산정**, 재산정 사이즈+전체 판매가(개당가×수량) 기준 비용 결정.
- **판매가 기준**: 판매자 할인쿠폰 적용 **최종 소비자 판매가**. (저가 전용 할인 시 개당 판매가 기준.)
- **저가 상품 전용 할인**: 14,000원 미만 일부 1차 카테고리(영유아 필수품·영유아 기타용품·뷰티·음료·신선식품·하우스홀드·건강/식품·퍼스널케어·애완용품·스낵/커피/차) 입출고·배송비 할인(~2027.01.31).
- **VAT 별도**(반출 배송 서비스만 VAT 포함). 반품 시 입출고/배송 비용 전액 환불(프로모션).
- 정확한 카테고리×사이즈×판매가 원 단위 금액표는 fee-details 페이지에서 **카테고리 선택 시 동적**으로 표시(API: `GET /tenants/rfm/api/cms/categories` 1.3MB). 카테고리(1차): 패션의류잡화·뷰티·출산/유아동·식품·주방용품·생활용품·가구/홈데코·가전/디지털·스포츠/레져·도서·문구/오피스·음반/DVD·완구/취미·반려/애완용품·로켓프레시 등.

### S8 설계 함의 (중요)
- 쿠팡 정확 금액표 **완전 복제 = 잘못된 설계**(프로모션·저가할인·합포장 재산정·카테고리별 규칙으로 fragile 머니코드, 오탐 다수). D-4 "모델은 보조용"·D-5 "사실만, 판단은 Jino"에 위배.
- **올바른 S8 = 사이즈 유형 분류(결정적) + 이상치 플래깅(스크리닝)**. 우리 등록 치수→예상 사이즈 유형 vs 실청구 금액 정합성 대조 → 불일치 항목을 **사람 검토용으로 플래그**(definitive 과오청구 판정 아님).
