# 네이버 커머스(스마트스토어) API 표면 전수 조사 — 1부 전체 인벤토리

조사일 2026-08-18. 1차 출처: https://apicenter.commerce.naver.com/llms/llms.txt (인덱스, 200 OK, 126개 항목:
공통소개 6·위키 4·실제 endpoint 116) + endpoint별 개별 .md 17건 fetch(핵심 도메인 커버, 상품 도메인 정적 메타
44건은 문서를 열지 않고 인덱스 설명문만으로 grain 판정 — 아래 표에 [부분확인] 표기).

전체 도메인별 endpoint 개수(인덱스 실측): N배송4 · 문의6 · 상품64 · 인증1 · 정산5 · 주문20 · 커머스솔루션8 · 판매자정보8 = **116개**.

범례: **grain** = TS(타임스탬프 date-time) / D(일 단위 date) / 정적(시간 필드 없음) / 쓰기(작업성 API, grain 무관).
라벨: [확인됨] = 개별 .md fetch로 응답 스키마 필드 타입까지 확인 / [부분확인] = 인덱스 설명문·엔드포인트명만 확인(개별 md 미fetch) / [미상] = 접근 실패·문서 부재.

## 주문 도메인 (20개 중 우리가 부르는 것 표시)

| 엔드포인트 | 주는 자료 | grain | 조회창 제약 | 소급 가능성 | 코드 호출 | 라벨 |
|---|---|---|---|---|---|---|
| GET .../product-orders/last-changed-statuses | 상태 변경된 productOrderId 피드 | **TS**(date-time, lastChangedFrom/To) | lastChangedTo 생략시 +24h 자동, limitCount 최대 300(초과 요청도 300 캡), moreFrom/moreSequence 커서 페이지네이션 | 임의 과거 lastChangedFrom 지정 가능(창 상한 문서 미기재) | ✅ `backend/app/clients/naver.py:307,750,880` (fetch_orders/fetch_pending_orders/fetch_claims) — **전부 `_sweep_last_changed`(naver.py:231) 경유** | [확인됨] ⚠️**2026-08-19 정정**: 이 행의 ✅는 「호출한다」이지 「커서를 처리한다」가 아니었다. 실제로 세 호출부 전부 `more`를 무시해 **2026-08-18에 23건(356,100원)이 유실**됐다(D-NAO-202). 이 표의 ✅를 「제약을 지킨다」로 읽으면 안 된다 — 그 오독이 결함을 63일 가렸다. |
| POST .../product-orders/query | 주문·상품주문·클레임·배송 풀 상세(paymentDate 등) | **TS**(응답 order.paymentDate — 우리 코드가 실제로 파싱, naver.py:316) | productOrderIds 배열 기반(최대 다건, 상한 문서 미기재) | 식별자 알면 언제든 재조회 가능 | ✅ `naver.py:223,645,766` | [확인됨](필드 구조는 문서상 축약 "하위 구조 생략"이나 우리 코드가 order.paymentDate/po.remainProductAmount 등 실 파싱으로 존재 실증) |
| GET .../product-orders (조건형, rangeType) | 현재상태 스냅샷, rangeType(주문일/결제일/발송일 등)+상태필터 | **TS**(from/to date-time) | to 생략시 +24h, pageSize 1~300, page≥1. 문서: "동기화 용도라면 last-changed-statuses가 더 안전" | 구조상 가능, 창 상한 미기재 | ❌ 미사용(grep 0건) | [확인됨] |
| GET orders/{orderId}/product-order-ids | 주문 내 상품주문ID 목록 | 정적(식별자 나열) | - | - | ❌ 미사용 | [확인됨] |
| POST confirm / dispatch / {id}/delay / {id}/hope-delivery/change | 발주확인·발송·지연·희망일변경 처리(쓰기) | 쓰기 | - | - | ✅confirm,dispatch,delay 사용(naver.py:718,731,746) / ❌hope-delivery/change 미사용 | [확인됨] |
| POST claim/cancel/approve·request | 취소 승인/요청(쓰기) | 쓰기 | - | - | ✅ 둘 다 사용(832,843) | [확인됨] |
| POST claim/return/approve·reject·request·holdback·holdback/release | 반품 처리(쓰기) | 쓰기 | - | - | ✅ 전부 사용(854,862,878,889,909) | [확인됨] |
| POST claim/exchange/collect/approve·dispatch·holdback·holdback/release·reject | 교환 처리(쓰기) | 쓰기 | - | - | ✅ 전부 사용(925,939,960,971,979) | [확인됨] |

## 정산 도메인 (5개 — 전부 일 단위, PAO엔 「촘촘한 자료」 없음)

| 엔드포인트 | 주는 자료 | grain | 조회창 제약 | 소급 가능성 | 코드 호출 | 라벨 |
|---|---|---|---|---|---|---|
| GET settle/case (건별) | 상품주문/배송비/기타비용 건별 정산(payDate 등) | **D**(전 필드 yyyy-MM-dd, 시각 없음) | pageSize≤1000, searchDate 단일일자 또는 orderId/productOrderId 특정 | 특정 식별자면 창 제한 문서 미기재(사실상 자유로 보이나 미확정) | ✅ `naver.py:395,472` | [확인됨] |
| GET settle/daily (일별) | 정산 방법(계좌/충전금)별 일 합계 | **D** | startDate/endDate 필수, pageSize≤1000 | 문서에 조회 가능 기간 상한 명시 없음(D+n 성숙도는 memory 정본: D+12) | ✅ `naver.py:347` | [확인됨] |
| GET settle/commission-details | 수수료를 유형(commissionType 14종)·매출연동채널(sellingInterlockCommissionType 100+종)·결제수단별로 분해 | **D** | pageSize≤1000 | 미기재 | ❌ 미사용 | [확인됨] — 우리 코드는 4개 수수료 키 합산(paymentCommission 등)만 쓰는데, 이 endpoint는 그보다 세분화된 근거 원장이다 |
| GET vat/case (건별 부가세) | 원주문/취소/환급 흐름별 매출·세액 | **D** | startDate/endDate 필수, **조회 가능 기간 = 전월 말일까지**(당월 불가) | 과거로는 열려 있으나 "당월 실시간"은 원리적으로 막힘 | ❌ 미사용 | [확인됨] |
| GET vat/daily (일별 부가세) | 일별 매출·세액 합계 | **D** | 上同, 전월 말일까지 | 上同 | ❌ 미사용 | [확인됨] |

## 문의 도메인 (6개)

| 엔드포인트 | 주는 자료 | grain | 조회창 제약 | 소급 가능성 | 코드 호출 | 라벨 |
|---|---|---|---|---|---|---|
| GET pay-user/inquiries (고객문의=네이버페이) | 문의/답변 본문+**등록·답변 일시**(HH:mm:ss.SSS) | **TS** | startSearchDate~endSearchDate(날짜 필터), size 10~200, page 1~1,000,000 | 날짜범위 임의 지정 가능, 상한 미기재 | ✅ `naver.py:521`, 호출부 `routers/naver_ops.py:836` — **온디맨드뿐, 스케줄러 미등록**(scheduler_service.py grep 0건) | [확인됨] |
| GET contents/qnas (상품문의) | 상품 상세페이지 Q&A, createDate(date-time) | **TS** | fromDate/toDate 필수(date-time), size≤100 | "5~10분 폴링" 권장 문구만, 창 상한 미기재 | ❌ 미사용 | [확인됨] |
| GET contents/qnas/templates | 답변 템플릿 목록 | 정적 | - | - | ❌ 미사용 | [확인됨] |
| PUT contents/qnas/{id} | 상품문의 답변 등록/수정(쓰기) | 쓰기 | - | - | ❌ 미사용 | [부분확인] |
| POST/PUT pay-merchant/inquiries/{id}/answer[/…] | 네이버페이 문의 답변 등록/수정(쓰기) | 쓰기 | - | - | ❌ 미사용 | [부분확인] |

## 상품 도메인 (64개 — 압축. 전부 정적 메타이거나 D-grain, 코드 미사용 다수)

| 그룹 | 개수 | grain | 코드 호출 | 라벨 |
|---|---|---|---|---|
| POST products/search (목록조회, periodType=등록일/판매시작/판매종료/최종수정일) | 1 | **D**(yyyy-MM-dd) | ✅ `naver.py:583` | [확인됨] |
| PUT origin-products/{id}/change-status (판매상태 변경, 쓰기) | 1 | 쓰기 | ✅ `naver.py:1003` | [확인됨] |
| 카테고리·브랜드·제조사·속성·옵션·사이즈·원산지·패션모델·상품정보고시·태그·묶음배송/희망일배송그룹·공지사항·반품택배사·검수 등 정적 메타 조회/등록/수정/삭제 | 61 | 정적(시간 필드 없음, 등록/수정 API는 쓰기) | ❌ 전부 미사용 | [부분확인](인덱스 설명문 기준. product-inspections/channel-products만 개별 fetch로 [확인됨] — 정적 상태 큐, 시간 필드 없음) |

## N배송 (물류 SKU, 4개) — 전부 미사용, 정적 조회

| 엔드포인트 | grain | 코드 호출 | 라벨 |
|---|---|---|---|
| GET SKU 조회(v1/v2)·SKU 연결상품 조회·SKU 목록조회(POST paged-list) | 정적 | ❌ 전부 미사용 | [부분확인] |

## 커머스솔루션 (8개) — PAO 매출과 무관(우리가 네이버에 내는 솔루션 구독료 축)

| 엔드포인트 | 주는 자료 | grain | 코드 호출 | 라벨 |
|---|---|---|---|
| GET commerce-solutions/transactions (비즈월렛 결제내역) | 솔루션 사용료 거래(paymentConfirmDate) | **TS** | ❌ 미사용 | [확인됨] — PAO 총이익 비용 항목 후보이나 매출/광고 축과는 별개 자금흐름 |
| GET subscriptions/{accountUid}(사용상태)·seller-info-by-token 등 | 구독 상태 메타 | 정적 | ❌ 미사용 | [부분확인] |
| PUT subscriptions/…/approve·reject·unsubscription 등(쓰기) | - | 쓰기 | ❌ 미사용 | [부분확인] |

## 판매자정보 (8개)

| 엔드포인트 | grain | 코드 호출 | 라벨 |
|---|---|---|---|
| GET seller/account, seller/channels | 정적 | ✅ `naver.py:618-619` | [확인됨] |
| GET seller/this-day-dispatch, logistics-companies, outbound-locations, addressbooks* | 정적 | ❌ 전부 미사용 | [부분확인] |

## 인증 (1개)

| 엔드포인트 | grain | 코드 호출 | 라벨 |
|---|---|---|
| POST /v1/oauth2/token | 인프라(토큰 5분 유효) | ✅ `naver.py:76` | [확인됨] |

## API데이터솔루션 도메인 — llms.txt 인덱스에 부재

`intro-제약사항.md`(요청량 제한 절)에 "API데이터솔루션을 구독 후 API를 호출하는 판매자"라는 문구만 존재하고,
llms.txt 최상단 소개문에도 "…API데이터솔루션 등 핵심 도메인"으로 언급되지만, 실제 인덱스(126개 항목)에는
이 이름의 섹션이 **없다**. 상품/주문/정산/문의/N배송/커머스솔루션/판매자정보/인증 8개 도메인만 존재.
→ 4부 미상 목록 참조(별도 구독 게이트로 추정되나 미확인).

---

## 코드 호출 요약 카운트

- 부르는 endpoint: **25개**(claim류 다건 포함, oauth 제외 시 24개) — 전부 「주문」+「정산」+「상품 2개」+「판매자정보 2개」+「문의 1개(비스케줄)」.
- 문서 전체 endpoint: **116개**.
- 비율: 약 **21.6%**(25/116).
