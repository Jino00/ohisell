# 14. 네이버 커머스 API — 발주/발송 처리(쓰기) 스펙 (트랙 N6)

> 출처: apicenter.commerce.naver.com → 커머스API → 주문 → 발주/발송 처리 (v2.79.0, 2026-06-04 Jino 스크린샷 실측)
> ★ 전부 **POST** (HANDOFF에 PUT으로 적혔던 것은 오류 — 스펙 확인으로 정정). 추측 금지(CLAUDE.md) 원칙에 따라 실측 스펙만 기록.
> 응답 공통 형식: `{ timestamp: string<date-time>, traceId: string(필수), data: object(성공/실패 상품주문 처리 내역) }`

## N6-1. 발주 확인 처리 (confirm)
```
POST /v1/pay-order/seller/product-orders/confirm
```
- 설명: 단수/복수 상품주문 발주 확인. **요청 가능 상품주문번호 최대 30개**.
- Body (REQUIRED): 상품 주문 번호 배열
  - `productOrderIds` : `string[]`  — 상품주문번호 배열

## N6-2. 발송 처리 (dispatch)
```
POST /v1/pay-order/seller/product-orders/dispatch
```
- 설명: 단수/복수 상품주문 발송 처리. **최대 30개**.
- Body (REQUIRED): `dispatchProductOrders` (object 배열). 각 항목:
  - `productOrderId`      : string        — 상품주문번호 (예 2022040521691281)
  - `deliveryMethod`      : string        — 배송방법 코드 (아래 표)
  - `deliveryCompanyCode` : string        — 택배사 코드 (아래 표, deliveryMethod=DELIVERY 계열일 때 필요)
  - `trackingNumber`      : string        — 송장번호 (택배일 때 필요)
  - `dispatchDate`        : string<date-time> — 발송일 (ISO8601, 예 2022-04-05T12:17:35+09:00)

### deliveryMethod 코드
| 코드 | 설명 |
|------|------|
| DELIVERY | 택배, 등기, 소포 |
| GDFW_ISSUE_SVC | 굿스플로 송장 출력 |
| VISIT_RECEIPT | 방문 수령 |
| DIRECT_DELIVERY | 직접 전달 |
| QUICK_SVC | 퀵서비스 |
| NOTHING | 배송 없음 |
| RETURN_DESIGNATED | 지정 반품 택배 |

### deliveryCompanyCode 주요 코드 (전체 100+ — 국내 주요만 발췌)
| 코드 | 택배사 |
|------|--------|
| CJGLS | CJ대한통운 |
| HANJIN | 한진택배 |
| HYUNDAI | 롯데택배 |
| KGB | 로젠택배 |
| EPOST | 우체국택배 |
| KDEXP | 경동택배 |
| CVSNET | GSPostbox택배 |
| CUPARCEL | CU편의점택배 |
| DAESIN | 대신택배 |
| KUNYOUNG | 건영택배 |
| GSFRESH | GSFresh |
| NONGHYUP | 농협택배 |
| EMS | EMS |
| DHL | DHL |
| FEDEX | FEDEX |
| UPS | UPS |
- ※ 전체 목록은 API 문서 deliveryCompanyCode 표 참조(국제택배 다수 포함). 드롭다운엔 국내 주요만 노출, 나머지는 직접 입력 허용 검토.

## N6-3. 발송 지연 처리 (delay)
```
POST /v1/pay-order/seller/product-orders/:productOrderId/delay
```
- 설명: **특정 상품주문 1건** 발송 지연 처리 (confirm/dispatch와 달리 단건, productOrderId는 path param).
- Path param: `productOrderId` (string, REQUIRED)
- Body (REQUIRED):
  - `dispatchDueDate`               : string<date-time> — 발송 기한 (예 2022-06-05T12:17:35+09:00)
  - `delayedDispatchReason`         : string            — 발송 지연 사유 코드 (아래 표)
  - `dispatchDelayedDetailedReason` : string            — 발송 지연 상세 사유 (예 "상품 준비중입니다.")

### delayedDispatchReason 코드
| 코드 | 설명 |
|------|------|
| PRODUCT_PREPARE | 상품 준비 중 |
| CUSTOMER_REQUEST | 고객 요청 |
| CUSTOM_BUILD | 주문 제작 |
| RESERVED_DISPATCH | 예약 발송 |
| OVERSEA_DELIVERY | 해외 배송 |
| ETC | 기타 |

---

# N7. 클레임 (취소/반품/교환) 쓰기 스펙 (트랙 D-10)
> 출처: API센터 → 커머스API → 주문 → 취소/반품/교환 (2026-06-04 Jino 스크린샷 실측). 전부 POST.
> 공통 응답 = 주문-클레임 처리 반환 구조체: `{timestamp, traceId(필수), data:object}` (발주확인 confirm 응답은 이 구조체 아님 — 별도).

## N7 wave 1 — 취소 (Cancel)
### 취소 요청 승인 (구매자 취소요청 승인)
```
POST /v1/pay-order/seller/product-orders/{productOrderId}/claim/cancel/approve
```
- Path: `productOrderId` (string, 필수). **Body 없음.**

### 취소 요청 (판매자 직접 취소)
```
POST /v1/pay-order/seller/product-orders/{productOrderId}/claim/cancel/request
```
- Path: `productOrderId` (string, 필수)
- Body (필수):
  - `cancelReason` (string, **필수**) — 클레임 요청 사유 코드 (아래)
  - `cancelDetailedReason` (string) — 취소 상세 사유, 500자 제한
  - `cancelQuantity` (integer) — 취소 수량 (미입력 시 전체수량 취소)

#### cancelReason 코드
| 코드 | 설명 |
|------|------|
| INTENT_CHANGED | 구매 의사 취소 |
| COLOR_AND_SIZE | 색상 및 사이즈 변경 |
| WRONG_ORDER | 다른 상품 잘못 주문 |
| PRODUCT_UNSATISFIED | 서비스 불만족 |
| DELAYED_DELIVERY | 배송 지연 |
| SOLD_OUT | 상품 품절 |
| INCORRECT_INFO | 상품 정보 상이 |

## N7 클레임 목록 읽기 — 변경 상품 주문 정보 구조체 (last-changed-statuses 응답)
- 출처: API센터 주문조회 "변경 상품 주문 정보 구조체" 실측. last-changed-statuses 각 항목 필드:
  - `orderId`, `productOrderId`, `lastChangedType`, `paymentDate`, `lastChangedDate`, `productOrderStatus`, **`claimType`**, **`claimStatus`**, `receiverAddressChanged`, `giftReceivingStatus`.
- `lastChangedType` 코드: PAY_WAITING, PAYED, EXCHANGE_OPTION, DELIVERY_ADDRESS_CHANGED, GIFT_RECEIVED, **CLAIM_REJECTED(클레임 철회)**, DISPATCHED, **CLAIM_REQUESTED(클레임 요청)**, **COLLECT_DONE(수거 완료)**, **CLAIM_COMPLETED(클레임 완료)**, … (이하 PURCHASE_DECIDED 등).
- → 클레임 감지 = `claimStatus` 비어있지 않음. claimType은 CANCEL/RETURN/EXCHANGE.
- **claimStatus enum(실측)**: CANCEL_REQUEST(취소요청)·CANCELING·CANCEL_DONE·CANCEL_REJECT(취소철회) / RETURN_REQUEST(반품요청)·EXCHANGE_REQUEST(교환요청)·COLLECTING(수거중)·COLLECT_DONE(수거완료)·EXCHANGE_REDELIVERING(교환재배송중)·RETURN_DONE·EXCHANGE_DONE·RETURN_REJECT·EXCHANGE_REJECT / PURCHASE_DECISION_HOLDBACK·_REQUEST·_HOLDBACK_RELEASE / ADMIN_CANCELING·ADMIN_CANCEL_DONE·ADMIN_CANCEL_REJECT.
- **처리 대상 매핑**: 취소 승인=CANCEL_REQUEST / 반품 승인=RETURN_REQUEST / 교환=EXCHANGE_REQUEST·COLLECT_DONE.
- productOrderStatus enum: PAYMENT_WAITING·PAYED·DELIVERING·DELIVERED·PURCHASE_DECIDED·EXCHANGED·CANCELED·RETURNED·CANCELED_BY_NOPAYMENT.

## N7 wave 2 — 반품 (Return)
> 출처: API센터 실측 (2026-06-04). 전부 POST. productOrderId는 path.

### 반품 거부(철회) (reject)
- `POST /v1/pay-order/seller/product-orders/:productOrderId/claim/return/reject`
- 설명: 1건의 상품 주문에 대한 반품 요청을 거부(철회).
- Path: `productOrderId` (string, REQUIRED) — 상품 주문 번호. Example: 2022040521691951
- Body (application/json, REQUIRED):
  - `rejectReturnReason` (string, REQUIRED) — 반품 거부(철회) **자유 텍스트 사유** (enum 아님). Example: "고객님께서 통화로 교환을 원하셨습니다."
- 응답(200): {timestamp, traceId, data} (성공/실패 상품 주문 처리 내역).

### 반품 보류 (holdback)
- `POST /v1/pay-order/seller/product-orders/:productOrderId/claim/return/holdback`
- 설명: 1건의 상품 주문에 대한 반품을 보류.
- Path: `productOrderId` (string, REQUIRED).
- Body (application/json, REQUIRED):
  - `holdbackClassType` (string, REQUIRED) — 보류 유형 enum (250바이트 내외):
    - RETURN_DELIVERYFEE(반품 배송비 청구) · EXTRAFEEE(추가 비용 청구, ★원문 철자 E 3개) · RETURN_DELIVERYFEE_AND_EXTRAFEEE(반품 배송비 + 추가 비용 청구) · RETURN_PRODUCT_NOT_DELIVERED(반품 상품 미입고) · ETC(기타 사유)
    - EXCHANGE_DELIVERYFEE(교환 배송비 청구) · EXCHANGE_EXTRAFEE(추가 교환 비용 청구) · EXCHANGE_PRODUCT_READY(교환 상품 준비 중) · EXCHANGE_PRODUCT_NOT_DELIVERED(교환 상품 미입고) · EXCHANGE_HOLDBACK(교환 구매 확정 보류)
    - SELLER_CONFIRM_NEED(판매자 확인 필요) · PURCHASER_CONFIRM_NEED(구매자 확인 필요) · SELLER_REMIT(판매자 직접 송금) · ETC2(기타)
  - `holdbackReturnDetailReason` (string, REQUIRED) — 보류 상세 사유 (자유 텍스트). Example: 미입고
  - `extraReturnFeeAmount` (number, optional) — 기타 반품 비용. Example: 0
- 응답(200): {timestamp, traceId, data}.

### 반품 보류 해제 (holdback/release)
- `POST /v1/pay-order/seller/product-orders/:productOrderId/claim/return/holdback/release`
- 설명: 1건의 상품 주문에 대한 반품 보류를 해제.
- Path: `productOrderId` (string, REQUIRED).
- Body: **없음** (취소 승인과 동일 — body 없이 POST).
- 응답(200): {timestamp, traceId, data}.

### 반품 승인 (approve)
- `POST /v1/pay-order/seller/product-orders/:productOrderId/claim/return/approve`
- 설명: 1건의 상품 주문에 대한 반품 요청을 승인.
- Path: `productOrderId` (string, REQUIRED).
- Body: **없음** (body 없이 POST).
- 응답(200): {timestamp, traceId, data}.

### 반품 요청 (request, 판매자 직접 반품 접수)
- `POST /v1/pay-order/seller/product-orders/:productOrderId/claim/return/request`
- 설명: 1건의 상품 주문에 대해 반품 요청.
- Path: `productOrderId` (string, REQUIRED).
- Body (application/json, REQUIRED):
  - `returnReason` (string, REQUIRED) — 클레임 요청 사유 enum (requestReturnClaimReason, 250바이트 내외):
    - INTENT_CHANGED(구매 의사 취소) · COLOR_AND_SIZE(색상 및 사이즈 변경) · WRONG_ORDER(다른 상품 잘못 주문) · PRODUCT_UNSATISFIED(서비스 불만족) · DELAYED_DELIVERY(배송 지연) · SOLD_OUT(상품 품절) · DROPPED_DELIVERY(배송 누락) · BROKEN(상품 파손) · INCORRECT_INFO(상품 정보 상이) · WRONG_DELIVERY(오배송) · WRONG_OPTION(색상 등 다른 상품 잘못 배송)
  - `collectDeliveryMethod` (string, REQUIRED) — 수거 배송 방법 코드 enum (deliveryMethod):
    - DELIVERY(택배·등기·소포) · GDFW_ISSUE_SVC(굿스플로 송장 출력) · VISIT_RECEIPT(방문 수령) · DIRECT_DELIVERY(직접 전달) · QUICK_SVC(퀵서비스) · NOTHING(배송 없음) · RETURN_DESIGNATED(지정 반품 택배) · RETURN_DELIVERY(일반 반품 택배) · RETURN_INDIVIDUAL(직접 반송) · RETURN_MERCHANT(판매자 직접 수거, 장보기 전용) · UNKNOWN(알 수 없음, 예외용). Example: DELIVERY
  - `collectDeliveryCompany` (string, optional — REQUIRED 배지 없음) — 수거 택배사 코드 (deliveryCompanyCode enum, CJGLS·HYUNDAI(롯데택배)·HANJIN(한진)·KGB(로젠)·EPOST(우체국) 등 100+, N6-2 발송의 deliveryCompanyCode 표와 동일 코드셋).
  - `collectTrackingNumber` (string, optional) — 수거 송장 번호. Example: D2485799470
  - `returnQuantity` (integer, optional) — 반품 수량. **미입력 시 전체 수량 반품**.
- 응답(200): {timestamp, traceId, data}.

## N7 wave 3 — 교환 (Exchange)
> 출처: API센터 실측 (2026-06-04). 전부 POST. productOrderId는 path. ★경로가 `claim/exchange/...`.

### 교환 수거완료 (collect/approve)
- `POST /v1/pay-order/seller/product-orders/:productOrderId/claim/exchange/collect/approve`
- 설명: 1건의 상품 주문에 대한 교환을 수거 완료 처리.
- Path: `productOrderId` (string, REQUIRED).
- Body: **없음** (body 없이 POST).
- 응답(200): {timestamp, traceId, data}.

### 교환 재배송 (dispatch)
- `POST /v1/pay-order/seller/product-orders/:productOrderId/claim/exchange/dispatch`
- 설명: 1건의 상품 주문 교환 승인 건을 재배송 처리.
- Path: `productOrderId` (string, REQUIRED).
- Body (application/json, BODY 자체는 REQUIRED, 개별 필드는 REQUIRED 배지 없음 = 선택. ReDeliveryExchange):
  - `reDeliveryMethod` (string, optional) — 배송 방법 코드 (deliveryMethod enum, collectDeliveryMethod와 동일 셋: DELIVERY·GDFW_ISSUE_SVC·VISIT_RECEIPT·DIRECT_DELIVERY·QUICK_SVC·NOTHING·RETURN_DESIGNATED·RETURN_DELIVERY·RETURN_INDIVIDUAL·RETURN_MERCHANT·UNKNOWN). Example: DELIVERY
  - `reDeliveryCompany` (string, optional) — 택배사 코드 (deliveryCompanyCode enum, N6-2와 동일).
  - `reDeliveryTrackingNumber` (string, optional) — 재배송 송장 번호. Example: 1111111115
- 응답(200): {timestamp, traceId, data}.

### 교환 보류 (holdback)
- `POST /v1/pay-order/seller/product-orders/:productOrderId/claim/exchange/holdback`
- 설명: 1건의 상품 주문에 대한 교환을 보류.
- Path: `productOrderId` (string, REQUIRED).
- Body (application/json, REQUIRED):
  - `holdbackClassType` (string, REQUIRED) — 보류 유형 enum (반품 보류와 **동일 14종**: RETURN_DELIVERYFEE·EXTRAFEEE·RETURN_DELIVERYFEE_AND_EXTRAFEEE·RETURN_PRODUCT_NOT_DELIVERED·ETC·EXCHANGE_DELIVERYFEE·EXCHANGE_EXTRAFEE·EXCHANGE_PRODUCT_READY·EXCHANGE_PRODUCT_NOT_DELIVERED·EXCHANGE_HOLDBACK·SELLER_CONFIRM_NEED·PURCHASER_CONFIRM_NEED·SELLER_REMIT·ETC2).
  - `holdbackExchangeDetailReason` (string, REQUIRED) — 보류 상세 사유 (★필드명이 Return과 다름: Exchange). Example: 미입고 상태
  - `extraExchangeFeeAmount` (number, optional) — 기타 교환 비용. Example: 0
- 응답(200): {timestamp, traceId, data}.

### 교환 보류 해제 (holdback/release)
- `POST /v1/pay-order/seller/product-orders/:productOrderId/claim/exchange/holdback/release`
- 설명: 1건의 상품 주문에 대한 교환 보류를 해제.
- Path: `productOrderId` (string, REQUIRED).
- Body: **없음** (body 없이 POST).
- 응답(200): {timestamp, traceId, data}.

### 교환 거부(철회) (reject)
- `POST /v1/pay-order/seller/product-orders/:productOrderId/claim/exchange/reject`
- 설명: 1건의 상품 주문에 대한 교환 요청을 거부(철회).
- Path: `productOrderId` (string, REQUIRED).
- Body (application/json, REQUIRED):
  - `rejectExchangeReason` (string, REQUIRED) — 교환 거부(철회) **자유 텍스트 사유**. Example: 착용한 상품은 교환할 수 없습니다.
- 응답(200): {timestamp, traceId, data}.

## N7 wave 3 교환 요약 (구현 대상 5종)
| 기능 | endpoint (`.../claim/exchange/...`) | body |
|------|------|------|
| 수거완료 | `/collect/approve` | 없음 |
| 재배송 | `/dispatch` | reDeliveryMethod?·reDeliveryCompany?·reDeliveryTrackingNumber? (전부 선택) |
| 보류 | `/holdback` | holdbackClassType(enum14)+holdbackExchangeDetailReason+extraExchangeFeeAmount? |
| 보류 해제 | `/holdback/release` | 없음 |
| 거부(철회) | `/reject` | rejectExchangeReason(자유텍스트) |

## 구현 메모
- 3종 모두 실제 주문 상태 변경 → dry_run+confirm 이중확인 필수(D-2, 쿠팡 쓰기 계승).
- confirm/dispatch는 배치(최대 30) → 30 초과 시 분할 또는 거부.
- delay는 단건 path 방식 → 라우터에서 1건씩 호출.
- 네이버 커머스 API는 서버 IP 화이트리스트 → 검증은 prod에서만 가능. dry_run은 API 미호출(보낼 body만 구성·반환).
- NaverClient._request_post는 POST JSON 지원하나 path param 치환·단건 delay용 메서드 별도 필요.
