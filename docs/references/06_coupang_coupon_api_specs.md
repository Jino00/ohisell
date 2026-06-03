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
## 구현 메모 (P5)
- coupons.py(21 SA): 읽기 #2·#4·#5·#6·#10·#11·#15~#21 + 쓰기 #1·#3·#7·#8·#9·#12·#13·#14(쓰기 페이즈 dry_run).
- **조망 회계축 연결**: 셀러 부담 할인 비용 = 즉시할인쿠폰 적용액(#17 옵션별·#19 주문별). 단, 정산(P4) revenue-history의 `seller_discount_coupon`/`coupang_discount_coupon`에 이미 실측 차감액이 잡힘(04 §3) → P5는 쿠폰 운영 현황 보조. 실제 할인 비용 차감은 정산이 진실(D-3).
- ⚠️ 쓰기 본문 스키마는 구현 시 각 article 재확인(추정 금지). 게이트웨이 3종(openapi/marketplace_openapi/fms) 서명은 _base 동일(HMAC).
