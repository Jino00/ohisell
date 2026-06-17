# 20. 쿠팡 로켓배송(1P) supplier.coupang.com 정찰(S1) 실측 보고

> 트랙: `track_coupang-rocket-1p.md` S1(D-8) · 정찰일: 2026-06-17 · 계정: 주식회사 오하이테크 (vendorId `A01029796`)
> 방식: 헤드풀 Chrome(CDP 9223) + 원시 CDP 웹소켓 Network 도청 + DOM(Runtime.evaluate) 추출.
> 도구: `tools/rocket_supplier_recon.py`(원본) → 라이브 보강(원시 CDP `suppress_origin` + base64/HTML 본문 + DOM 스크레이프).
> **전부 라이브 실측(추측 0). 원칙22 준수.**

---

## 0. 결론 한 줄
**3단계(발주·납품·정산) 데이터를 supplier.coupang.com에서 전부 긁을 수 있다.** 단,
**발주+납품은 깨끗한 JSON API 1개**로 끝나고(`/po-web/app/purchase-order/list`),
**정산은 폼-GET SSR HTML**이라 DOM/HTML 파싱이 필요하다(JSON API 아님).

---

## 1. 인증·봇 방어 (D-1 검증)
- **쿠키 세션 인증** (브라우저 로그인). Wing과 동일.
- **Akamai 봇 방어 존재**: `POST /C_A8aP/4MMhvw/.../EFt` (202, sensor data) 반복 관측 → Wing의 `cf_clearance`류와 동급.
  → **헤드풀 CDP 페처 패턴 재사용 필수**(D-1 맞음). requests 직타는 Akamai 차단 위험. `wing_browser_fetcher.py` 패턴 복제 권장.
- 호스트: 전부 `supplier.coupang.com` 단일 오리진(별도 API 서브도메인 없음).

---

## 2. 단계별 데이터 소스 (★핵심)

| 단계 | 화면 | 엔드포인트 | 방식 | 금액 필드 |
|------|------|-----------|------|----------|
| **① 발주(PO)** | 발주현황 목록 | `GET /po-web/app/purchase-order/list` | **JSON API** | `sumOfOrderQty` / `sumOfOrderAmount` |
| **② 납품(입고)** | (동일 목록) | `GET /po-web/app/purchase-order/list` | **JSON API** | `sumOfReceivingQty` / `sumOfReceivingAmount` |
| **③ 정산(매입확정·지급)** | 매입 정산 | `GET /scm/settlement/general/purchase/account` | **SSR HTML(폼-GET)** | 공급가액 / 부가가치세 / 지급예정금액 |
| (참고) 발주 상세 | 발주 상세 | `GET /scm/purchase/order/get/{seq}` | **SSR HTML** | SKU 단위(DOM 파싱 필요, 본 정찰 미추출) |

→ **①발주 + ②납품이 같은 list API 한 방에 다 들어있다.** D-2 3단계 중 ①②는 JSON 1개로 해결.
→ **발주↔납품 드리프트(D-5)** = `sumOfOrderAmount` vs `sumOfReceivingAmount` (같은 row 내 비교, 즉시 계산).

---

## 3. ① + ② 발주/납품 list API 실측

**요청**: `GET https://supplier.coupang.com/po-web/app/purchase-order/list`
**쿼리 파라미터**(실측):
```
page=1
searchDateType=WAREHOUSING_PLAN_DATE   # 입고예정일 기준 (다른 값: 발주일 등 추정 — S2에서 확인)
searchStartDate=2026-06-17
searchEndDate=2026-07-17
centerCode=                            # 입고센터 필터(빈값=전체)
purchaseOrderIdArray=
vendorPaymentInfoSeq=
purchaseOrderStatus=                   # 발주상태 필터
purchaseOrderType=
skuIdArray=
crossdock=
transportType=
```

**응답 envelope**: `{"success":true,"message":null,"body":{"body":[ {PO}, ... ]}}`
(주의: `body.body` 이중 중첩. 페이지네이션 메타는 상위 `body`에 더 있을 수 있음 — S2 확인.)

**PO 1건 전체 필드(실측 — vendorId A01029796):**
```json
{
  "purchaseType": "REORDER", "purchaseTypeDescription": "리오더",
  "purchaseOrderSeq": 134433322,                          // ★PK
  "mdId": "selee227", "mdName": "Lia (이승현)",
  "vendorId": "A01029796", "vendorName": "주식회사 오하이테크",
  "firstSkuName": "오하이 풀커버 강화유리 액정보호필름2개 ... 갤럭시S24 울트라",
  "createdAt": "2026-06-17T06:34:32.000+00:00",           // 발주 생성일(UTC)
  "purchaseOrderStatus": "RP", "purchaseOrderStatusDescription": "거래처확인요청",
  "expectedDeliveryDate": "2026-06-23T15:00:00.000+00:00",// 입고예정일
  "bookingStatus": null, "bookingStatusDescription": "미예약",
  "centerCode": "SEL1", "centerName": "서울",              // 입고센터(예: KKW3/경기광주3)
  "receivingStartedAt": null, "receivingFinishedAt": null,// 입고 추적 타임스탬프
  "orderType": "DIRECT", "orderTypeDescription": "일반",
  "purchaseOrderType": "NORMAL", "purchaseOrderTypeDescription": "일반",
  "virtual": false,
  "transportType": "SHIPMENT", "transportTypeDescription": "쉽먼트",
  "sumOfVendorConfirmedQty": 2,  "sumOfVendorConfirmedAmount": 21480,  // 거래처 확인
  "sumOfReceivingQty": 0,        "sumOfReceivingAmount": 0,            // ★② 납품(입고)
  "sumOfOrderQty": 2,            "sumOfOrderAmount": 21480,            // ★① 발주(D-3 매출)
  "skuCount": 1,
  "regType": "VERTICAL_INSTOCK_OOS_RECOVERY",
  "crossDockCode": null, "crossDockName": null, "coreV2CrossDock": false, "freshCrossDock": false,
  "bookingSave": false, "shipmentBookingV2Save": false,
  "milkrunSeqList": [], "milkrunSaveTarget": false, "milkrunSave": false,
  "vendorPaymentList": [],
  "hasShipmentBooking": false, "parcelShipment": false, "truckShipment": false, "shipmentBooked": false
}
```

**금액 3종 구분(실측):**
- `sumOfOrderAmount` = 발주 금액 (쿠팡이 발주한 금액) → **D-3 매출 기준**
- `sumOfVendorConfirmedAmount` = 거래처(우리)가 확인한 금액
- `sumOfReceivingAmount` = 실제 입고된 금액 → **D-2 ② 납품 단계**
- 예시 PO 134433322: 발주 21,480 = 확인 21,480, 입고 0 (status RP=거래처확인요청, 아직 미입고)
- 단가: 21,480 / 2개 = 10,740/개. **VAT 포함 여부 S2에서 확정 필요**(정산 공급가액과 대조).

---

## 4. ③ 정산(매입 정산) 실측 — SSR HTML

**요청**: `GET https://supplier.coupang.com/scm/settlement/general/purchase/account`
**쿼리 파라미터**(폼-GET, 실측):
```
page=1
size=10
billIssueType=DIRECT                   # 계산서 발행 유형
startDate=2026-03-01
endDate=2026-06-17
paymentPurchaseSearchType=
vendorPaymentInfoSeq=
paymentSearchType=COMPLETE             # 정산상태(완료)
```
→ **응답은 JSON 아님, 전체 HTML 페이지**(데이터 테이블 SSR 렌더). XHR 미발생.
→ `Network.getResponseBody`는 최상위 navigation 문서라 **빈 본문** 반환 →
   **`Runtime.evaluate`로 DOM 테이블 추출**해야 함(본 정찰에서 검증된 방법).

**정산 테이블 컬럼(실측, grain=계산서번호):**
| # | 컬럼 | 예시(계산서 30025494) |
|---|------|------|
| 1 | 계산서번호 | 30025494 |
| 2 | 거래처명 | 주식회사 오하이테크 |
| 3 | 작성일자 | 2026-06-16 |
| 4 | 지급일자 | 2026-08-14 |
| 5 | 과세유형 | 과세 |
| 6 | 발주유형 | 일반 |
| 7 | 정산유형 | 입고 |
| 8 | 발행유형 | 역발행 |
| 9 | **공급가액** | 510,819 |
| 10 | **부가가치세** | 51,081 |
| 11 | **지급예정금액** | 561,900 |
| 12 | 세금계산서 확정일 | - (또는 2026-06-16) |
| 13 | 1차 지급일 | - |
| 14 | 1차 지급액 | 0 |
| 15 | 2차 지급액 | 561900 |
| 16 | (링크) | 발주현황 / 입고상세내역 / 전송성공 |

**관측치(3~6월 10건 일부):**
```
30025494 작성2026-06-16 지급2026-08-14 공급510,819 +VAT51,081 = 지급예정561,900
30015106 작성2026-06-15 지급2026-08-14 공급204,437 +VAT20,443 = 지급예정224,880
30015105 작성2026-06-15 지급2026-08-12 공급635,441 +VAT63,544 = 지급예정698,985
30003353 작성2026-06-14 지급2026-08-12 공급1,101,800 +VAT110,180 = 지급예정1,211,980
29991328 작성2026-06-13 지급2026-08-07 공급1,105,546 +VAT110,554 = 지급예정1,216,100
```
- **지급예정금액 = 공급가액 + 부가가치세** (검산 일치).
- 정산 grain = **계산서(invoice)** 단위(발주 PO 단위 아님). 1 계산서 = 여러 입고 묶음 가능(`입고상세내역` 링크로 PO 매핑).
- 지급일자가 작성일 +약 2개월 후(여신). **세금계산서 확정일**이 정산 확정 시점.
- 원본 증거: `docs/references/data/20_rocket_1p_settlement_dom_sample.json`

---

## 5. 보조 API (필터 enum·메뉴)
- `GET /menus` — 좌측 메뉴 + `searchCondition`(기본 조회조건) + `vendorName`/`userName`.
- `GET /po-web/app/settlement/sidebar` — 정산 사이드바 카운트.
- `GET /po-web/app/purchase-order/status` · `/type` · `/transport-type` · `/is-cross-dock` — 발주 필터 enum 목록.
- `GET /po-web/app/center/purchasable/list` — 발주 가능 센터 목록.
- `GET /po-web/app/config-info` — `{supplierHubServer, xdockPickingEnabled}`.
- `GET /po-web/app/vendor/calculator-required`, `/xdock-picking/work-history/has-new`, `/banner`, `/alert/news` — 부가.

---

## 6. S2 설계로 넘길 결정·미확인 사항
**확정(실측):**
- 발주/납품 = `/po-web/app/purchase-order/list` JSON 1개. 발주↔납품 드리프트 row 내 계산.
- 정산 = `/scm/settlement/general/purchase/account` SSR → DOM/HTML 파싱(계산서 grain).
- 인증=쿠키, Akamai 방어 → 헤드풀 CDP 페처(D-1).

### 6-1. S2 사전 라이브 확인 — 6건 중 5건 해결 (2026-06-17, 전부 page-context fetch, 추측 0)
**★수집 방법 확정**: XHR 캡처 대신 **브라우저 page-context `fetch(path,{credentials:"include"})`**로 전체 JSON 수신
(세션 쿠키 자동, same-origin, 8000자 잘림 없음). `tools/rocket_supplier_recon.py`에 헬퍼화 가능. 정산 SSR만 DOM.

1. **[해결·코드값 확정] `searchDateType` 가능값 = 2종**: `WAREHOUSING_PLAN_DATE`(입고예정일) · **`PURCHASE_ORDER_DATE`(발주일)**.
   → **D-3 매출(발주 시점 인식)은 `searchDateType=PURCHASE_ORDER_DATE` 기준 조회**.
   ★발주일 enum 코드값 라이브 캡처 확정(2026-06-17, S2 진입 시): Ant Select 드롭다운에서 발주일 선택→검색 1회의
     XHR URL에서 `searchDateType=PURCHASE_ORDER_DATE` 실측(fetch 아닌 **XMLHttpRequest** 사용 — 캡처는 XHR.open 후킹). 추측 0(원칙22).
2. **[해결] 페이지네이션**: 응답 outer `body`에 `currentPage·lastPageNumber·totalRecordSize·pageSize`.
   예: 2026-04-01~07-17 입고예정일 윈도우 = 620건/13페이지. → **`page=1..lastPageNumber` 루프 수집**.
3. **[해결] 발주/입고 금액 = VAT 포함(gross)**. 계산서별 Σ`sumOfReceivingAmount` = 정산 **지급예정금액**(gross) 일치(4/5 정확):
   `30025494→561,900` `30015106→224,880` `30015105→698,985` `29991328→1,216,100` (=각 공급가액+VAT).
   → 종합조망 매출/순이익은 **gross 발주금액**(sumOfOrderAmount). 정산 공급가액은 net(VAT 별도).
4. **[해결] 계산서↔PO 매핑 = list 안에 내장**: PO 필드 **`vendorPaymentList:[{vendorPaymentInfoSeq, documentStatus}]`**.
   `vendorPaymentInfoSeq` = **계산서번호**(정산 화면 grain·`vendorPaymentInfoSeq` 파라미터와 동일).
   관계: **1 계산서 ↔ N PO**(묶음 정산) **AND 1 PO ↔ N 계산서**(부분 정산 가능, list라서). 발주↔정산 드리프트(D-5) 이 키로 연결.
   ⚠ 부분정산 사례 30003353: 단일 PO 134001752 입고 2,375,980 vs 계산서 gross 1,211,980 → PO가 복수 계산서로 분할. S2 reconcile 시 vendorPaymentList 다중성·documentStatus 처리 주의.
5. **[미해결·선택] SKU 단위 금액**: list는 `skuCount`·`firstSkuName`만(PO 집계). SKU 그레인 필요 시 발주 상세
   `/scm/purchase/order/get/{seq}`(SSR HTML) DOM 파싱 필요. **머니수학(D-3/D-4)은 PO grain으로 충분 → S2 기본범위 제외**, SKU 분석 요구 시 후속.
6. **[해결] 정산/발주 list `size` 고정**: `size=200` 줘도 `pageSize=50` 유지(무시) → 페이지 루프 필수.

### 6-2. 보너스 관측
- list 응답에 `bulkQuery`(원시 SQL `eoupang.purchase_order`)·`piiFields`(mdId/mdName)·`queryValues` 노출(수집엔 불필요, 무시).
- 발주일시(KST) = `createdAt`(UTC)+9h 검증 일치(예: 15:34:32 KST = 06:34:32Z).

---

## 7. 재현 방법 (self-verify)
```bash
# 1) 정찰 Chrome 실행 (CDP 9223)
backend/.venv/bin/python3 tools/rocket_supplier_recon.py chrome
#    → supplier.coupang.com 로그인 후 발주/정산 화면 이동

# 2) 원시 CDP 도청 (Network 도메인 직결, suppress_origin 필수)
#    JSON API(발주/납품) 캡처: ~/.ohisell_supplier_recon.jsonl
# 3) 정산 SSR은 DOM 추출:
#    CDP Runtime.evaluate → document.querySelectorAll('table') 파싱
```
- ★교훈: Playwright `connect_over_cdp`는 **기존(자기가 안 띄운) 페이지의 response 이벤트를 못 받음**
  → 원시 CDP 웹소켓 `Network.responseReceived` 직접 도청으로 우회.
- ★교훈: Chrome 디버깅 소켓은 Origin 헤더 붙은 ws 연결을 **403 거부**
  → `websocket.create_connection(url, suppress_origin=True)`.
- ★교훈: 최상위 navigation 문서는 `Network.getResponseBody` 빈 본문 → SSR은 `Runtime.evaluate(DOM)`로.
