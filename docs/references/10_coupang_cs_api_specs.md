# 10. 쿠팡 CS(고객문의) API 명세 (6개 — 전수 디테일)

> 수집일: 2026-06-03 · 출처: developers.coupangcorp.com CS 섹션(360005081953), /browse --headed
> 트랙 D-15. 게이트웨이 `https://api-gateway.coupang.com`, HMAC. 모두 openapi 게이트웨이. 조회 기간 최대 7일.

| # | 이름 | 메서드·Path | 용도 |
|:-:|------|------|------|
| 1 | 상품별 고객문의 조회 | `GET /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/onlineInquiries` | 고객Q&A 조회 |
| 2 | 상품별 고객문의 답변 | `POST /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/onlineInquiries/{inquiryId}/replies` | ⚠️쓰기 |
| 3 | 쿠팡 고객센터 문의조회 | `GET /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/callCenterInquiries` | CS이관 문의 조회 |
| 4 | 쿠팡 고객센터 문의답변 | `POST /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/callCenterInquiries/{inquiryId}/replies` | ⚠️쓰기 |
| 5 | 쿠팡 고객센터 문의확인 | `POST /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/callCenterInquiries/{inquiryId}/confirms` | ⚠️쓰기 |
| 6 | 쿠팡 고객센터 문의 단건 조회 | `GET /v2/providers/openapi/apis/api/v5/vendors/callCenterInquiries/{inquiryId}` | 단건 조회 |

## 1. 상품별 고객문의 조회 (#1)
- `GET .../v5/vendors/{vendorId}/onlineInquiries` · Path: vendorId(O)
- Query: `vendorId`(O), `answeredType`(O: ALL/ANSWERED/NOANSWER), `inquiryStartAt`·`inquiryEndAt`(최대 7일), `pageSize`, `pageNum`
- 고객-판매자 Q&A 조회.

## 2. 상품별 고객문의 답변 (#2) — ⚠️쓰기
- `POST .../v4/vendors/{vendorId}/onlineInquiries/{inquiryId}/replies` · Path: inquiryId(O)·vendorId(O) · Body: `content`(O 답변내용)
- inquiryId는 #1로 먼저 확인. 하나의 문의에 답변.

## 3. 쿠팡 고객센터 문의조회 (#3)
- `GET .../v5/vendors/{vendorId}/callCenterInquiries` · Query: vendorId(O), `partnerCounselingStatus`(O 문의상태 NO_ANSWER 등), inquiryStartAt/EndAt(최대7일), pageSize/pageNum
- 쿠팡 고객센터로 접수→업체이관된 문의 조회.

## 4. 쿠팡 고객센터 문의답변 (#4) — ⚠️쓰기
- `POST .../v4/vendors/{vendorId}/callCenterInquiries/{inquiryId}/replies` · Path vendorId(O)·inquiryId(O)
- 상태가 '미답변'(inquiryStatus:progress, partnerTransferStatus:requestAnswer)일 때만 가능. 중복 답변 에러. ⚠️ 24시간 미답변 시 쿠팡 자동 처리→'답변완료' 전환되면 API 답변 불가(WING에서만).

## 5. 쿠팡 고객센터 문의확인 (#5) — ⚠️쓰기
- `POST .../v4/vendors/{vendorId}/callCenterInquiries/{inquiryId}/confirms` · Body: `confirmBy`(O 실사용자ID=WING ID)
- 쿠팡이 상담완료한 업체이관 건(미확인 TRANSFER) 확인 처리. 24시간 경과 시 불가.

## 6. 쿠팡 고객센터 문의 단건 조회 (#6)
- `GET .../v5/vendors/callCenterInquiries/{inquiryId}` · Path: inquiryId(O, 상담번호)
- 단건 조회. ⚠️ 과도 조회 시 자동 차단.

---
## 구현 메모
- cs.py(6 SA): 읽기 #1·#3·#6 + 쓰기 #2·#4·#5. 조망 우선순위 낮음(트랙 D-7) — 운영 보조. 쓰기는 쓰기 페이즈.
