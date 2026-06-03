# 07. 쿠팡 배송/환불 API 명세 (12개 — 전수 디테일)

> 수집일: 2026-06-03 · 출처: developers.coupangcorp.com 배송/환불 섹션(360005081913), /browse --headed
> 트랙 D-15. 게이트웨이 `https://api-gateway.coupang.com`, HMAC(openapi 게이트웨이). 발주서(ordersheet)=쿠팡의 주문 단위. ★발주서 목록 조회는 **현재 사용중**(sync_service 주문 동기화).
> ⚠️ 쓰기(상태변경) 메서드 일부 미확정 — 구현 시 본문/메서드 재확인(추정 금지).

| # | 이름 | 메서드·Path | 용도 |
|:-:|------|------|------|
| 1 | 발주서 목록 조회(분단위 전체) | `GET /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/ordersheets` | 주문 수집(분단위) ★사용중 |
| 2 | 발주서 목록 조회(일단위 페이징) | `GET /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/ordersheets` | 주문 수집(일단위 페이징) |
| 3 | 발주서 단건 조회(shipmentBoxId) | `GET /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/ordersheets/{shipmentBoxId}` | 단건 조회 |
| 4 | 발주서 단건 조회(orderId) | `GET /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/{orderId}/ordersheets` | orderId로 단건 |
| 5 | 배송상태 변경 히스토리 조회 | `GET /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/ordersheets/{shipmentBoxId}/history` | 상태이력 |
| 6 | 상품준비중 처리 | `PUT /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/ordersheets/acknowledgement` | ⚠️쓰기 결제완료→상품준비중 |
| 7 | 송장업로드 처리 | `POST /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/orders/invoices` | ⚠️쓰기 운송장 등록 |
| 8 | 이미출고 처리 | `PUT /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/returnRequests/{receiptId}/completedShipment` | ⚠️쓰기 출고중지요청에도 발송한 경우 |
| 9 | 송장업데이트 처리 | `POST /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/orders/updateInvoices` | ⚠️쓰기 운송장 수정 |
| 10 | 주문 상품 취소 처리 | `POST /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/orders/{orderId}/cancel` | ⚠️쓰기 주문 취소 |
| 11 | 출고중지완료 처리 | `PUT /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/returnRequests/{receiptId}/stoppedShipment` | ⚠️쓰기 출고중지 완료 |
| 12 | 장기미배송 배송완료 처리 | `POST /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/completeLongTermUndelivery` | ⚠️쓰기 |

## 1·2. 발주서 목록 조회 (#1 분단위·#2 일단위) — ★주문 수집 (사용중)
- `GET .../v5/vendors/{vendorId}/ordersheets`. 분단위(createdAtFrom/To 분단위 전체)와 일단위 페이징 2변형. 핵심 Query: 기간(createdAt)·`status`(주문상태)·페이징. 발주서=쿠팡 주문 단위, items[]에 vendorItemId·수량·가격(D-8 결합축, Order 테이블 적재 원천).
- **이미 sync_service 주문 동기화가 사용**(catalog "현재 사용중"). 상세 응답 스키마는 기존 코드 참조.

## 3·4. 발주서 단건 조회 (#3 shipmentBoxId·#4 orderId)
- `GET .../ordersheets/{shipmentBoxId}` 또는 `GET .../{orderId}/ordersheets`. 단건 발주서. ⚠️ 상품준비중 처리 후 배송지 변경 확인용(공식 권고).

## 5. 배송상태 변경 히스토리 (#5)
- `GET .../ordersheets/{shipmentBoxId}/history`. 주문 상태 변경 이력.

## 6. 상품준비중 처리 (#6) — ⚠️쓰기
- `PUT .../v4/vendors/{vendorId}/ordersheets/acknowledgement`. 결제완료→상품준비중. Body: `shipmentBoxIds`(배열, ≤50개·타임아웃 방지). 취소건 포함 시 Partial Error. 환불진행중 주문 변경 불가.

## 7·9. 송장 업로드/업데이트 (#7 POST invoices·#9 POST updateInvoices) — ⚠️쓰기
- 등록 `POST .../v4/vendors/{vendorId}/orders/invoices` · 수정 `POST .../v4/vendors/{vendorId}/orders/updateInvoices`. Body: 운송장(택배사코드[08 §8]·invoiceNumber·shipmentBox). 출고 처리.

## 8·11. 출고중지 관련 (#8 completedShipment·#11 stoppedShipment) — ⚠️쓰기
- 둘 다 `.../v4/vendors/{vendorId}/returnRequests/{receiptId}/...`. 고객 출고중지요청(RELEASE_STOP_UNCHECKED) 대응. #8=이미 발송함(왕복 반품비 판매자 귀책), #11=출고중지 완료.

## 10. 주문 상품 취소 처리 (#10) — ⚠️쓰기
- `POST .../v5/vendors/{vendorId}/orders/{orderId}/cancel`. 주문 취소.

## 12. 장기미배송 배송완료 처리 (#12) — ⚠️쓰기
- `POST .../v4/vendors/{vendorId}/completeLongTermUndelivery`.

---
## 구현 메모
- orders.py(12 SA): 읽기 #1~#5(발주서 조회·히스토리, #1 사용중) + 쓰기 #6~#12(송장·취소·출고중지·상태변경, 쓰기 페이즈 dry_run). 회계축은 발주서(주문)가 핵심 — 이미 Order 적재 중.
- ⚠️ 쓰기 6종 메서드/본문은 구현 시 각 article 재확인(상태변경 PUT/POST 혼재). 발주서 조회 응답 스키마는 기존 sync_service 코드가 권위.
