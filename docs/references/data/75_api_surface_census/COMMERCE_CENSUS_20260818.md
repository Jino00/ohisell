# 커머스(스마트스토어) API 전수 조사 — 밴드 매트릭스 대조 (2026-08-18 KST)

> 목적: Jino 지시 원문 *"성과로 나눈 등급에 대해서 광고, 스마트스토어에서 API로 받아올 수 있는 모든 데이터를 나누자"*의 「모든 데이터」를 `docs/references/data/73_band_x_all_api/BAND_X_ALL_API_MATRIX_20260818.md`의 커머스 12축(C1~C12)이 실제로 대표하는지, endpoint 단위로 검증한다. 읽기 전용(prod 미접속, git 커밋 없음). 저자: Sonnet(전수성 검증 담당).
> 입력: ①`docs/references/data/71_commerce_surface/commerce_api_surface_inventory.md`(선행 인벤토리, endpoint 116개 최초 집계) ②`73_band_x_all_api/BAND_X_ALL_API_MATRIX_20260818.md` 1-B·1-C(커머스 축 정의) ③본 조사에서 신규 fetch한 24건 개별 .md ④`backend/app/clients/naver.py` 코드 grep(재검증).

---

## 1. 조사 방법·커버리지 자백 (이 절 없이는 이 문서는 무효)

- **1차 출처 인덱스**: `https://apicenter.commerce.naver.com/llms/llms.txt`. `WebFetch` 툴은 이 호스트에서 거부됐다(`Claude Code is unable to fetch from apicenter.commerce.naver.com`) — **대체 경로로 `curl`(Bash)을 썼고 200 OK로 전체 126개 항목을 받았다.** ref 71과 독립적으로 재파싱한 도메인별 개수는 공통소개 6·위키 4·**N배송 4·문의 6·상품 64·인증 1·정산 5·주문 20·커머스솔루션 8·판매자정보 8 = endpoint 116개**로 ref 71의 116과 **정확히 일치**(드리프트 없음, 도메인별 세부 개수까지 동일).
- **개별 endpoint .md 문서**: 이번 세션에서 **신규로 24건**을 개별 fetch했다(N배송 4/4 전량, 판매자정보 신규 6건+ref71의 2건=8/8 전량, 커머스솔루션 신규 7건+ref71의 1건=8/8 전량, 상품 신규 7건). ref 71이 이미 연 **17건**과 합쳐 **누적 41건/116건(35.3%)**을 개별 문서로 확인했다. **75건(64.7%)은 여전히 인덱스 설명문만 근거로 한 [부분확인]이다** — 대부분 상품 도메인(64개 중 10개만 개별 확인, 54개 미확인)에 몰려 있다. **「전수 조사 완료」라고 쓰지 않는다.**
- **도메인별 개별 문서 확인율**(누적, ref71 + 이번 세션):

  | 도메인 | 전체 | 개별 확인 | 비율 | 비고 |
  |---|---|---|---|---|
  | N배송 | 4 | **4** | 100% | 이번 세션 신규 4건 — ref71은 0건(전부 [부분확인]이었음) |
  | 문의 | 6 | 4 | 66.7% | ref71 기존(조회 3계열+템플릿), 쓰기 2건(qnas PUT·pay-merchant answer PUT) 미확인 — 이번 세션 재확인 안 함 |
  | 상품 | 64 | 10 | 15.6% | ref71 기존 3(search·change-status·product-inspections) + 이번 세션 신규 7(channel-products v2·origin-products v2·group-products v2·categories·seller-notices·product-brands·product-models). **54건 미확인 — 최대 공백** |
  | 인증 | 1 | 1 | 100% | ref71 기존 |
  | 정산 | 5 | 5 | 100% | ref71 기존, 5행 전부 |
  | 주문 | 20 | 20건 중 대표 8행 확인(그룹 커버) | 확인 근거 그룹핑 | ref71이 20개를 8개 대표 행(claim류 다건 포함)으로 묶어 개별 확인 — 이번 세션 재확인 안 함, ref71 신뢰 승계 |
  | 커머스솔루션 | 8 | **8** | 100% | 이번 세션 신규 7건 + ref71 기존 1건(transactions) |
  | 판매자정보 | 8 | **8** | 100% | 이번 세션 신규 6건 + ref71 기존 2건(seller/account·channels) |

- **코드 grep**은 `backend/app/clients/naver.py`(1,054줄) 전체를 독립적으로 재검증했다(아래 §4). `backend/app/services/naver.py`는 **존재하지 않는다** — 실제 클라이언트 파일은 `backend/app/clients/naver.py`다(작업 지시문의 경로 가정이 틀렸다, 직접 확인).
- **미확인 사항은 §6에 명시한다.** 이 문서의 결론(§5)은 확인된 41건 + 코드 grep + ref71의 기존 판정에 근거하며, 상품 도메인 54건의 [부분확인] 위에 있는 판정(특히 C10)은 **인덱스 설명문 신뢰 수준**임을 반복해 밝힌다.

---

## 2. P1 — 전수 목록 (도메인별)

전체 도메인별 endpoint 개수(1차 출처 재실측, ref71과 일치):

| 도메인 | 개수 |
|---|---|
| N배송 | 4 |
| 문의 | 6 |
| 상품 | 64 |
| 인증 | 1 |
| 정산 | 5 |
| 주문 | 20 |
| 커머스솔루션 | 8 |
| 판매자정보 | 8 |
| **합** | **116** |

(공통소개 6·위키 4는 endpoint가 아니라 안내 문서 — 116에 불포함, ref71과 동일 처리.)

**신규 확인분 상세(24건, 이번 세션)**:

| 도메인 | endpoint | grain(재확인) | 등급 연결 키 | ref71 원 라벨 | 이번 판정 |
|---|---|---|---|---|---|
| N배송 | GET SKU 조회(v1, Deprecated) | **TS** — `registrationYmdt`/`modificationYmdt`(date-time, KST) | 없음(SKU 단위) | 정적[부분확인] | **정정: TS, 정적 아님** |
| N배송 | GET SKU 조회(v2) | **TS** — 上同 | 없음(SKU 단위) | 정적[부분확인] | **정정: TS, 정적 아님** |
| N배송 | GET SKU 연결상품 조회 | 정적(SKU-상품 매핑 자체엔 시간 필드 없음) | ★**`content.channelProductId`** — 채널상품ID, `naver_adgroup_product.mall_product_id`(models.py:2346 주석: `= naver_product_bep.channel_product_id`)와 **같은 ID 공간으로 판단**(코드 대조, 확정 아님) | 정적[부분확인] | **경로 발견 — 등급 교차 후보(§5)** |
| N배송 | POST SKU 목록조회(paged-list) | **TS** — `fromDate`/`toDate`(yyyy-MM-dd) 조회창 지원 + `content.registrationYmdt`/`modificationYmdt` | 없음(SKU 단위, 응답에 상품 링크 없음 — product-mappings로 별도 조회 필요) | 정적[부분확인] | **정정: TS, 정적 아님** |
| 커머스솔루션 | GET seller-info-by-token | TS(회차 시작/종료일 date-time) | 없음(accountUid만) | 정적[부분확인] | 확인 — 원료없음 유지 |
| 커머스솔루션 | GET subscriptions/{accountUid} | TS(requestDate/startDate/endDate) | 없음(accountUid만) | 정적[부분확인] | 확인 — 원료없음 유지 |
| 커머스솔루션 | POST external-transactions(쓰기) | 쓰기(paymentConfirmDate 입력) | 없음 | 정적[부분확인] | 확인 — 쓰기, 축 아님 |
| 커머스솔루션 | PUT subscriptions/…/reject·unsubscription·approve·unsubscription/approve(쓰기 4건) | 쓰기 | 없음 | 정적[부분확인] | 확인 — 쓰기, 축 아님 |
| 판매자정보 | GET logistics-companies, outbound-locations | 정적 | 없음 | 정적[부분확인] | 확인 — 정적 유지 |
| 판매자정보 | GET addressbooks(단건·목록), this-day-dispatch, POST this-day-dispatch | 정적/쓰기 | 없음 | 정적[부분확인] | 확인 — 정적/쓰기 유지 |
| 상품 | GET/PUT channel-products{No}(v2) | **TS**(`saleStartDate`/`saleEndDate`) — 단 매출 발생 시각이 아니라 **셀러가 입력하는 판매기간 설정값** | **`channelProductNo` 직결** | 정적[부분확인] | 정정: TS 필드 존재하나 변경-피드 grain 아님(입력값), 등급 교차는 이미 C10 경로(상품번호)로 원리상 열려 있음 — 신규 아님 |
| 상품 | GET/PUT origin-products{No}(v2) | 上同 | `originProductNo`(channelProductNo 상위) | 정적[부분확인] | 上同 |
| 상품 | GET group-products{No}(v2) | 정적(release/월 단위 productInfoProvidedNotice.releaseDate만, 연도 없는 월값) | `groupProductNo` | 정적[부분확인] | 확인 — 정적 유지 |
| 상품 | GET categories, product-brands, product-models | 정적 | 없음(카탈로그 코드) | 정적[부분확인] | 확인 — 정적 유지 |
| 상품 | GET seller-notices | **TS**(`displayStartDate`/`displayEndDate`/`importantNoticeStartDate`/`EndDate`, KST) | 간접(별도 PUT apply API로 채널상품에 적용 — 이 GET 자체엔 상품 키 없음) | 정적[부분확인] | 정정: TS 존재하나 공지 도메인(운영 공지)이라 성과 등급과 무관 — 저우선 |

**나머지 92건**(문의 2 쓰기, 상품 54, 주문 20, 정산 5, 인증 1, 판매자정보/N배송/커머스솔루션의 이미 §2 표에 없는 나머지)은 ref71의 기존 판정을 승계했다 — 이번 세션에서 개별 재확인은 안 했다(§1 표에 도메인별 수치로 명시).

---

## 3. P2 — 양방향 대조표

### 3-A. 축 → endpoint (매트릭스 1-B 정의, 재확인)

매트릭스 `73_band_x_all_api/BAND_X_ALL_API_MATRIX_20260818.md` 1-B의 12축과 endpoint 매핑을 원문 그대로 재확인했다(§1-B 144~155행):

| 축 | endpoint(들) | 도메인 배정 |
|---|---|---|
| C1 주문 | product-orders(query·last-changed-statuses·조건형) | 주문 4(조회) |
| C2 주문 대기 | product-orders(조건형, 재사용) | 주문(재사용, 신규 아님) |
| C3 클레임 | claim 조회(사실상 product-orders/query 재사용 + claim 쓰기 16건) | 주문(재사용+쓰기 16) |
| C4 클레임 정산 프로브 | (내부 원장, endpoint 아님 — `naver_claim_settlement_probe`는 settle/case 파생) | — |
| C5 정산 일별 | settle/daily | 정산 1 |
| C6 정산 건별 | settle/case | 정산 1 |
| C7 수수료 분해 | commission-details | 정산 1 |
| C8 부가세 | vat/case, vat/daily | 정산 2 |
| C9 문의 | pay-user/inquiries, contents/qnas(조회 3) | 문의 3(조회) + 쓰기 3 |
| C10 상품 메타 | products/search + 정적 61~62종 | 상품 64(전량) |
| C11 N배송 | SKU 조회(v1/v2)·연결상품·목록조회 | N배송 4(전량) |
| C12 커머스솔루션 | transactions 등 8종 | 커머스솔루션 8(전량) |
| (축 아님) | seller/account·channels 등 | 판매자정보 8 + 인증 1 |

**계수**: 조회 성격 축(C1~C10 실질 endpoint) + C11(4) + C12(8) + 판매자정보·인증(9, 축 아님) + 쓰기 계열(주문 16 + 문의 3 + 상품 정적군 내 쓰기 다수 + 커머스솔루션 쓰기 6) = 매트릭스 1-B 자신의 계수표(144~155행)가 **116 = 축 12 + 축 아님 28**로 이미 자체 정합을 주장한다. 본 조사가 endpoint 단위로 직접 여는 방식으로 재확인한 결과, **이 자체 정합 주장 자체는 성립**하되(모든 116개가 어떤 C-축이나 "축 아님"에 형식적으로는 들어간다), §3-B에서 그 배정의 **질**(라벨이 실제 응답 스키마와 맞는가)에 이견이 나왔다.

### 3-B. endpoint → 축 (핵심 산출물 — 라벨 재검증 결과)

116개 전부가 형식적으로는 C1~C12 또는 "축 아님"에 배정돼 있다(§3-A). **미배정(어떤 축에도 안 들어간) endpoint는 0건**이다 — 매트릭스의 버킷 설계 자체는 촘촘하다. 그러나 이번 조사에서 **버킷 안의 라벨이 실제 문서와 어긋나는 endpoint 4건**을 찾았다(N배송 4/4 전량):

| endpoint | 매트릭스 라벨(C11) | 실제 문서 근거 | 어긋남 종류 |
|---|---|---|---|
| GET SKU 조회(v1) | "물류 축 — 등급 연관 가설 자체가 아직 없음", 암묵적 정적 | `registrationYmdt`/`modificationYmdt` **TS(date-time)** | grain 오분류(정적→TS) |
| GET SKU 조회(v2) | 上同 | 上同 | 上同 |
| GET SKU 연결상품 조회 | 上同, 연결 키 없음 전제 | `content.channelProductId` **응답 필드 존재** — `naver_adgroup_product.mall_product_id`와 동일 ID 공간 가능성(models.py:2346 주석 근거) | **연결 키 부재 전제가 틀렸을 가능성** — 등급 교차 경로 후보 신규 발견 |
| POST SKU 목록조회 | 上同 | `fromDate`/`toDate` 조회창 지원(정적 API가 아니라 기간 필터형 목록) | grain 오분류 |

C10(상품 메타)의 products/search는 이미 매트릭스·ref71·코드 3곳 모두 TS(D-grain)로 일치 — 어긋남 없음. seller-notices(TS 발견)는 C10 버킷 안에 있으나 성과 등급과 연결될 상품 키가 이 GET 자체엔 없어(적용 API가 별도) 실질 영향은 낮다.

---

## 4. P3 — 코드 호출 현황 (재검증)

`backend/app/clients/naver.py`(1,054줄) 전체를 독립 grep했다. **파일 경로 정정**: 작업 지시문이 가정한 `backend/app/services/naver.py`는 존재하지 않는다 — 실제 클라이언트는 `backend/app/clients/naver.py`다.

**ref71이 제시한 좌표를 전부 직접 재확인**했고, **드리프트는 발견되지 않았다**(작업 지시문의 "222·644·765 → 실제 220·636·757" 드리프트 주장은 이번 재확인과 **불일치** — 현재 파일에서 `last-changed-statuses`/`query`가 정확히 222/223, 644/645, 765/766에 있다):

| endpoint | 코드 위치(재확인) | ref71 주장과 일치? |
|---|---|---|
| product-orders/last-changed-statuses (3회 호출부) | 222, 644, 765 | ✅ 일치 |
| product-orders/query (3회 호출부) | 223, 645, 766 | ✅ 일치(ref71은 222/644/765로 병기했으나 실제 query는 +1행) |
| settle/daily | 347 | ✅ 일치 |
| settle/case (2회) | 395, 472 | ✅ 일치 |
| pay-user/inquiries | 521 | ✅ 일치 |
| products/search | 583 | ✅ 일치 |
| seller/account, seller/channels | 618–619 | ✅ 일치 |
| product-orders/confirm, dispatch | 718, 731 | ✅ 일치 |
| product-orders/{id}/delay | 746 | ✅ 일치 |
| claim/cancel/approve·request | 832, 843 | ✅ 일치 |
| claim/return/approve·reject·holdback·holdback/release·request | 854, 862, 878, 889, 909 | ✅ 일치 |
| claim/exchange/collect/approve·dispatch·holdback·holdback/release·reject | 925, 939, 960, 971, 979 | ✅ 일치 |
| origin-products/{id}/change-status | 1003 | ✅ 일치 |
| oauth2/token | 76 | ✅ 일치(ref71 기재 그대로) |

전체 백엔드(backend/app 전수 grep, naver.py 외) 대상 추가 확인: **N배송·커머스솔루션·상품 정적 61~62종·hope-delivery/change**를 부르는 코드는 **0건**(grep으로 재확인, `logistics/products/sellers/me/skus`, `commerce-solutions`, `/v1/categories`, `product-brands`, `contents/qnas`, `standard-group-products` 등 전부 hit 없음). `pay-user/inquiries`(문의)는 `naver_ops.py:836`에서 **온디맨드 라우터로만** 호출되고 스케줄러 등록은 재확인해도 **0건**(`scheduler_service.py`에 naver 문의 관련 job 없음) — ref71과 일치.

`keywordstool`·`/estimate/*`는 **네이버 SA(검색광고) API**이지 커머스 API가 아니다(다른 베이스 URL, `naver_sa_ad_fetcher.py`) — 116개 계수에 안 들어가는 것이 맞다(혼동 주의용으로 명시).

**코드 호출 요약 재확인**: 부르는 endpoint 25개(oauth 포함 시 25, claim 다건 포함)/116개 = **21.6%** — ref71과 동일.

---

## 5. ★매트릭스에 「제대로 안 실린」 endpoint 최종 목록 — 처분 후보 + 등급 교차 가능성

전 116개가 형식적 버킷은 갖고 있으므로(§3-B), 이 절은 「버킷은 있으나 버킷 안 라벨이 실제와 다른」 endpoint에 집중한다.

### 5-1. ★★핵심 발견 — N배송 SKU 4종 (매트릭스 C11) — **등급 교차 가능 후보로 재분류 제안**

| endpoint | 현재 매트릭스 처분(C11) | 재검토 결과 | 처분 후보 |
|---|---|---|---|
| GET SKU 조회(v1/v2) | 정적, 등급 연관 가설 없음 | `registrationYmdt`/`modificationYmdt` **TS grain 확인** | **①등급과 교차 가능(조건부)** — SKU 자체엔 상품 키가 없어 단독으론 안 닿음, 아래 product-mappings와 결합 필요 |
| **GET SKU 연결상품 조회** | 정적, 연결 키 없음 | `content.channelProductId` **응답 필드로 직접 확인** | **①등급과 교차 가능** — `channelProductId` → (models.py:2346 주석 근거로) `naver_adgroup_product.mall_product_id`와 동일 ID 공간 가능성 → `adgroup_id` 경로 존재 후보. **단 이 동일성은 코드 대조로 100% 확정하지 않았다(주석 근거일 뿐, 실측 조인 미실행) — [미상]으로 유보하되 근거를 남긴다** |
| POST SKU 목록조회 | 정적, 미사용 | TS(fromDate/toDate) 확인 | 상동, 목록 진입점으로 상동 경로 |

**근거를 대는 이유(지시 원문 요구사항)**: 매트릭스 §0-B·1-B는 "등급과 안 닿는다"고 쓸 땐 근거를 대라고 명시했다. 이번 발견의 근거는 ①N배송 문서 자체가 응답에 `channelProductId`를 명시(정적 메타가 아니라 상품 연결 스키마) ②`backend/app/models.py:2346` 주석이 `naver_adgroup_product.mall_product_id`를 `naver_product_bep.channel_product_id`와 동일시함 ③`naver.py:582`의 `products/search` 호출이 `channelProductNos`라는 동일 명명 규칙의 파라미터를 이미 쓰고 있어 이 ID 공간이 코드베이스 전체에서 일관됨. **이 셋을 연결하면 N배송 SKU 도메인은 원리적으로 등급 교차가 가능한 후보이고, 현재 매트릭스가 "가설 자체가 아직 없음"이라 쓴 것은 과소평가다.** 단, 실제 조인 실행(라이브 SKU-상품 매핑을 가져와 `naver_adgroup_product`와 조인)은 이번 조사 범위 밖(읽기 전용, 신규 API 호출·prod 접속 없음) — **[미상]으로 남기고 처분은 하지 않는다.**

### 5-2. 상품 도메인 v2 상세(channel-products/origin-products) — 재분류 불필요, 경로는 이미 열려 있었음

`saleStartDate`/`saleEndDate`가 TS 필드로 확인됐으나, 이는 판매기간 **설정값**(변경 이력이 아니라 현재 설정)이라 성과 리포트의 grain과 무관하고, 등급 교차 경로는 이미 `channelProductNo` = `mall_product_id`로 C10이 원리상 열려 있다고 매트릭스가 이미 서술했다(1-B C10 "상품번호 직결(원리상)"). **재분류 불필요 — 기존 판정 유지.**

### 5-3. 커머스솔루션·판매자정보 8+8종 — 매트릭스 판정 확인(변경 없음)

TS 필드는 존재하나(회차·신청일 등) 상품/주문 연결 키가 전혀 없다(accountUid만). 매트릭스의 "연결 키 없음 → 원리 불가"(C12) 및 "축 아님"(판매자정보) 판정은 **이번 개별 문서 확인으로 재확인됐다 — 변경 없음.**

### 5-4. 상품 도메인 나머지 54건 — 미확인, 판정 보류

이번 조사도 상품 도메인 64개 중 54개는 열지 않았다(§1). 확인한 10개(products/search, change-status, product-inspections, channel-products v2, origin-products v2, group-products v2, categories, seller-notices, product-brands, product-models) 범위에서는 "정적 메타, 시간축 없음"이라는 매트릭스 C10 라벨과 **어긋나는 사례가 없었다**(seller-notices의 TS는 상품 키가 없어 예외로 취급). 그러나 나머지 54건(옵션·사이즈·원산지·패션모델·태그·묶음배송/희망일배송그룹·검수·반품택배사 등)은 **미확인이므로 C10 라벨이 전부 옳다고 단언하지 않는다.**

---

## 6. 이번에도 확인 못 한 것

- **상품 도메인 54/64건** — 옵션·사이즈·원산지·패션모델·태그·묶음배송그룹·희망일배송그룹·검수 복원·반품택배사·추천태그·제한태그·상품정보제공고시 등. 개별 문서 미확인, 인덱스 설명문([부분확인])에만 근거.
- **문의 도메인 쓰기 2건**(`PUT contents/qnas/{id}`, `POST/PUT pay-merchant/inquiries/.../answer`) — ref71도 [부분확인], 이번 세션도 재확인 안 함.
- **주문 도메인 20건의 개별 URL 단위 재확인** — ref71이 8개 대표 행으로 그룹핑해 확인한 것을 승계했을 뿐, 20개 URL 각각을 이번 세션에서 다시 열지 않았다.
- **N배송 `channelProductId` = `naver_adgroup_product.mall_product_id` 동일 ID 공간 여부** — 코드 주석(models.py:2346) 근거뿐, 실제 라이브 조인이나 API 응답 대조로 확정하지 않았다. **[미상]**.
- **매트릭스가 자체 언급한 "ref 71 내부 계수 1건 불일치(상품 64 vs 정적 그룹 63)"** — 이번 조사로 부분 해소: 상품 64 = products/search(1) + change-status(1) + **정적 62**(매트릭스 원문의 "61"은 오프바이원). 단 이 62건 자체를 전부 열어 재확인한 것은 아니므로(§6 첫 항목과 동일 공백) "62"도 목록 카운트일 뿐 개별 grain 재확인은 아니다.
- **API데이터솔루션 도메인**(llms.txt 인덱스에 섹션 자체가 없음, ref71이 이미 기록) — 이번 조사도 재확인 못 함, 그대로 [미상] 유지.
- **prod 실측**(이 census는 코드·문서 정적 분석만 — 읽기 전용 경계상 prod DB 조회·API 호출 0건. N배송 조인 후보의 실제 매치율은 prod 없이는 확인 불가).

---

## 7. ★2026-09-05 재측정 (18일 경과, C2조사 세션) — 순수 추가, 위 §1~6 원문 불변

> 목적·경계는 ADS_CENSUS §7과 동일 — 북극성(`ref 82`) §3 L0 「커머스 25/116」 행 갱신. **네이버 API 실호출 0건 · prod 접속 0건 · 코드 0줄.**

### 7-1. 분모(116) 재확인 — 변하지 않았다

09-05 10:3x KST `https://apicenter.commerce.naver.com/llms/llms.txt`를 재`curl`(200 OK, 149줄). 도메인 섹션의 endpoint 링크 수를 재파싱: 공통소개 6·위키 4(합 10, endpoint 아님) + **N배송 4·문의 6·상품 64·인증 1·정산 5·주문 20·커머스솔루션 8·판매자정보 8 = 116** — 08-18과 **정확히 동일**(도메인별 세부 개수까지 일치, 신규·삭제 0건). **API데이터솔루션 섹션은 이번에도 인덱스에 없다**(08-18 §6과 동일 — [미상] 유지, ref71 이래 3번째 확인).

### 7-2. 분자 재확인 — `backend/app/clients/naver.py` 재검사

파일은 여전히 `backend/app/clients/naver.py`(1,232줄, 08-18 대비 +178줄 — 다른 기능 추가로 늘었을 뿐 이 census 대상 endpoint 호출부는 아래처럼 불변). 경로 리터럴을 전수 재grep(`"/v1\|f"/v1"` 패턴)했다.

**결과: 08-18과 완전히 동일한 25개 endpoint, 신규 0건, 감소 0건.** `oauth2/token`·`product-orders`(query·last-changed-statuses·confirm·dispatch·delay)·`claim/*`(11종)·`settle/daily`·`settle/case`·`pay-user/inquiries`·`products/search`·`seller/account`·`seller/channels`·`origin-products/{id}/change-status` — 08-18 §4 표의 좌표(행 번호는 파일이 늘어 이동했으나 endpoint 자체는 불변)와 1:1 대응.

**09-05 실사용 = 25/116(21.6%) — 08-18과 변화 없음.**

이번 세션이 확인한 것 — **D-NAO-212(C10 상품메타, 08-21)가 새 endpoint를 열지 않았다**: `naver_product_meta_ingest.py`가 쓰는 `POST /v1/products/search`는 08-18에 이미 O였던 바로 그 endpoint이고(§4 "products/search 583"), D-NAO-212가 바꾼 것은 **호출 파라미터**(카테고리 필터 있음 → 전건 무필터 순회)뿐이다 — endpoint 신규 개통이 아니다. N배송 SKU 4종(§5-1이 "등급 교차 가능 후보"로 지목)·`shared-budgets`류에 대응하는 커머스 쪽 후보(SharedBudget은 광고 계열, 커머스엔 해당 없음)도 grep 0건 그대로다.

### 7-3. §5-1 N배송 SKU 후보 — 여전히 미착수

08-18 §5-1이 "①등급 교차 가능(조건부)"로 지목한 N배송 SKU 4종(`GET SKU 조회 v1/v2`·`GET SKU 연결상품 조회`·`POST SKU 목록조회`)은 이번 재확인에서도 코드 호출 **0건**(`nsId`·`logistics/products/sellers` 문자열 grep 0건) — `channelProductId` = `naver_adgroup_product.mall_product_id` 동일 ID 공간 가설(§6 세 번째 항목)은 18일 전과 마찬가지로 **[미상]** 그대로, 실제 조인은 시도되지 않았다.

**「전수 조사 완료」라고 쓰지 않는다** — 상품 도메인 54/64건 미확인 등 08-18 §6의 공백은 이번 세션도 그대로 남아 있다.

---

*7절 작성: Sonnet(C2조사, 읽기 전용). 재확인 1차 출처: `https://apicenter.commerce.naver.com/llms/llms.txt`(2026-09-05 curl 재취득, 200 OK, 116개 endpoint 구성 100% 동일). 코드 재확인: `backend/app/clients/naver.py`(1,232줄). 네이버 API 실호출 0건·prod 접속 0건·git 커밋(이 파일 외) 0건·`backend/`·`frontend/` 수정 0줄.*
