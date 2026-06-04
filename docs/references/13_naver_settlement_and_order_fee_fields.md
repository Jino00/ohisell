# 13. 네이버 커머스 API — 정산·수수료·이익 정밀화 데이터 맵

> 출처: apicenter.commerce.naver.com/docs/commerce-api/current (v2.79.0, 2026-05-26)
> 조사일: 2026-06-04 (트랙 N1 잔여 "이익 정밀화" 착수 전 전수 조사)
> 목적: 패널 이익계산의 PG수수료를 주문시점 추정 → 정산 실측으로 정밀화. 어떤 API/필드가
>       실측 수수료를 주는지 한 번에 확정 (Jino 지시: "볼 때마다 새 발견" 방지 — 전수 조사).

## 핵심 결론 (먼저)
- **주문 단위 실측 수수료의 정답 = 건별 정산 `GET /v1/pay-settle/settle/case`** (productOrderId 그레인).
- 주문 API(product-orders/query)는 이미 주문시점 **예상** 수수료 4종 + `expectedSettlementAmount`(정산 예정 금액)를 줌 → 이게 현재 우리 `commission_amount`의 출처(추정).
- 정밀화 = "주문시점 예상" → "정산 실측"으로 교체. 단 **시점 함정**: 건별 정산은 정산 진행된 건만 → 최근 미정산 주문은 실측 없음.

---

## A. 주문 상세 (product-orders/query) — "상품 주문 정보 구조체" (현재 사용 중)
경로: `POST /v1/pay-order/seller/product-orders/query` → entry.productOrder
수수료/정산 관련 필드:
| 필드 | 의미 | 현재 사용 |
|------|------|-----------|
| paymentCommission | 결제 수수료 | ✅ 합산 |
| saleCommission | (구)판매 수수료 | ✅ 합산 |
| channelCommission | 채널 수수료 | ✅ 합산 |
| knowledgeShoppingSellingInterlockCommission | 지식쇼핑 매출연동 수수료 | ✅ 합산 |
| **expectedSettlementAmount** | **정산 예정 금액(주문시점 예상)** | ❌ 미사용 ← 활용 후보 |
| commissionRatingType | 수수료 과금 구분(결제/판매/채널) | ❌ |
| commissionPrePayStatus | 수수료 선결제 상태 | ❌ |
- 한계: 무이자할부/유입/솔루션사용료 수수료는 명시 필드 없음. 사후 취소·우대수수료 환급·차액정산 미반영.
- 즉 주문 API 수수료 = **주문 시점 예상값**. 실측 아님.

## B. 건별 정산 `GET /v1/pay-settle/settle/case` ★실측 주문단위★
요청: pageNumber/pageSize(≤1000) 필수. 선택: searchDate, orderId, productOrderId, periodType, settleDecisionType, settleType.
- **periodType**: SETTLE_CASEBYCASE_{SETTLE_SCHEDULE_DATE(정산예정일)|SETTLE_BASIS_DATE(정산기준일)|SETTLE_COMPLETE_DATE(정산완료일)|PAY_DATE(결제일)|TAXRETURN_BASIS_DATE(세금신고)}
- **settleDecisionType**(PAY_DATE일 때): SETTLED(정산확정)/UNSETTLED(미확정)/BEFORE_CANCEL(정산전취소)
응답 elements[] 필드:
| 필드 | 의미 |
|------|------|
| orderId / productOrderId | 주문번호 / 상품주문번호(배송비·기타비용 번호 포함) |
| productOrderType | PROD_ORDER(상품주문)/DELIVERY(배송비)/EXTRAFEE(기타)/REFUND 등 23종 |
| settleType | NORMAL_SETTLE_ORIGINAL/...AFTER_CANCEL/...BEFORE_CANCEL/QUICK_*/QUANTITY_CANCEL_*/PURCHASE_CONFIRM |
| productId / productName / purchaserName | 상품번호/상품명/구매자명 |
| settleBasisDate/settleExpectDate/settleCompleteDate/payDate | 정산기준/예정/완료/결제일 |
| **paySettleAmount** | 결제 정산 금액(=정산 기준 금액) |
| **totalPayCommissionAmount** | 총 네이버페이 관리 수수료 금액 |
| **sellingInterlockCommissionAmount** | 매출 연동 수수료 |
| **freeInstallmentCommissionAmount** | 판매자 부담 무이자 할부 수수료 |
| benefitSettleAmount | 혜택 정산 금액 |
| **settleExpectAmount** | 정산 예정 금액(실측) |
| merchantId/merchantName/contractNo | 가맹점/계약 |
- **실측 판매자 부담 수수료 ≈ totalPayCommissionAmount + sellingInterlockCommissionAmount + freeInstallmentCommissionAmount** (부호는 라이브 프로브로 확인 필요 — 일별정산은 수수료/혜택 음수)

## C. 수수료 상세 `GET /v1/pay-settle/settle/commission-details`
- 그레인: productOrderId × **commissionType**(행 분해). 같은 요청 파라미터(periodType 등).
- elements[] 필드: orderNo, productOrderId, productOrderType, productId, productName, settleType,
  settleBasisDate/ExpectDate/CompleteDate, taxReturnDate, commissionBasisAmount(수수료기준금액),
  **commissionType**, sellingInterlockCommissionType, payMeansType, **commissionAmount**(수수료금액), maximumSellingInterlockCommissionAmount
- commissionType 14종: SALE_COMMISSION/PAY_COMMISSION/CHNL_COMMISSION/ISTLM_COMMISSION(무이자할부)/
  PUBLISHING/INFLOW(유입)/SERVICE(솔루션사용료)/CONTRACT/PACKAGE/PARTNER/PLATFORM(판매수수료)/VERTICAL/PURCHASER/PRICE_COMPARISON
- sellingInterlockCommissionType: NAVER_SHOPPING/TOPTOP/EASY_BOOKING/ONEPLUS/SEARCH_ETC/LENS/SHOPPING_SEARCH
- 용도: "왜 이 수수료인가" 타입별 분해. 이익 총액엔 B로 충분, C는 분석/검증용.

## D. 일별 정산 `GET /v1/pay-settle/settle/daily` (구현 완료 — N1)
- 그레인: 정산예정일별 그룹 합계. **주문 단위 매칭 불가**.
- 응답 전체 필드(우리가 저장 안 한 것 多, 단 총합 settleAmount는 저장):
  settleBasisStartDate/EndDate, settleExpectDate, settleCompleteDate,
  settleAmount, paySettleAmount, commissionSettleAmount, benefitSettleAmount,
  deductionRestoreSettleAmount(기타공제환급), payHoldbackAmount(지급보류),
  minusChargeAmount, differenceSettleAmount(차액), returnCareSettleAmount(반품안심케어),
  normalSettleAmount, quickSettleAmount(빠른정산), preferentialCommissionAmount(우대수수료환급),
  settlementLimitAmount, settleMethodType(ACCOUNT/...), bankType, depositorName, accountNo
- 현재 저장: settle/pay/commission/benefit/payholdback + method. 합계는 정확하나 분해 항목 일부 누락.

## E. 부가세 — 이익계산 무관(세무 신고용)
- 일별 `GET /v1/pay-settle/vat/daily`: settleBasisDate, totalSalesAmount, taxationSalesAmount(과세),
  taxExemptionSalesAmount(면세), creditCardAmount, cashInComeDeductionAmount, cashOutGoingEvidenceAmount,
  cashExclusionIssuanceAmount, otherAmount
- 건별 `GET /v1/pay-settle/vat/case`: 건별 부가세.
- 이익 정밀화엔 미사용. N1 잔여 "부가세 연동"은 세무 관점 별도 기능.

---

## ★ 라이브 프로브 실측 (2026-06-04 서버, 원칙 22)
요청: `GET settle/case` periodType=SETTLE_CASEBYCASE_PAY_DATE, settleDecisionType=SETTLED, searchDate=2026-05-15 → 75건 SETTLED.
실제 응답 1건(PROD_ORDER):
```
orderId=2026051565521021 productOrderId=2026051585957391 productOrderType=PROD_ORDER
settleType=NORMAL_SETTLE_ORIGINAL paySettleAmount=15900
totalPayCommissionAmount=-433 sellingInterlockCommissionAmount=-159 freeInstallmentCommissionAmount=0
benefitSettleAmount=0 settleExpectAmount=15308 payDate=2026-05-15 settleExpectDate=2026-05-18
```
**확정 사실:**
1. 수수료 필드는 **음수**(totalPayCommission/sellingInterlock). → 실측 fee(양수) = `-(totalPayCommission + sellingInterlock + freeInstallment)`.
2. 검산: settleExpectAmount(15308) = paySettleAmount(15900) + totalPayCommission(-433) + sellingInterlock(-159) + freeInstallment(0) + benefit(0). ✓
3. **DELIVERY 행 별도**: 같은 orderId에 배송비 productOrderId 행 (paySettleAmount=2500, totalPayCommission=-68). → 상품 이익 매칭엔 **productOrderType=PROD_ORDER만** 사용.
4. searchDate 단일일 + periodType=PAY_DATE → 결제일 그레인. 우리 orders.order_date(결제일)와 매칭 정합.
5. 전체 키: benefitSettleAmount, contractNo, freeInstallmentCommissionAmount, merchantId/Name, orderId, payDate, paySettleAmount, productId, productName, productOrderId, productOrderType, purchaserName, sellingInterlockCommissionAmount, settleBasisDate, settleCompleteDate, settleExpectAmount, settleExpectDate, settleType, totalPayCommissionAmount.

## 이익 정밀화 설계 함의
1. **데이터 소스 = B(건별 정산, settle/case)**. periodType=PAY_DATE(결제일)로 조회하면 우리 주문의
   order_date(=paymentDate)와 동일 그레인 → orderId+productId 매칭 자연스러움.
2. **매칭 키**: 현재 orders는 order_number(=orderId)+platform_product_id(=productId)만 저장,
   productOrderId 미저장. 건별 정산을 orderId+productId로 집계해 매칭(같은 주문에 동일 productId 1개면 1:1).
   또는 fetch_orders에서 productOrderId를 별도 컬럼 저장(더 정밀).
3. **시점 함정**: 건별 정산은 정산 진행 건만. 최근 미정산 주문은 실측 없음 → 폴백 정책 필요(D-N으로 확정 예정).
4. **부호**: 라이브 프로브로 수수료 부호 확인 후 이익 공식 적용(원칙 22).
