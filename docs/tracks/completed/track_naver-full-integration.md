# 트랙: 네이버 스마트스토어 커머스 API 전 기능 연결 + 패널 표시
> 시작: 2026-06-04 · 상태: Active · 단계: N1 정산 진행 중
> 단일 진실 원천. 쿠팡 트랙(완료)과 동일 아키텍처·원칙 계승.

## 1. 목표 (한 줄)
네이버 커머스 API(9개 그룹, ~200 엔드포인트)를 읽기→쓰기 순으로 연결해 스마트스토어 패널에서 본다.

## 2. 확정 결정사항 (D-N, 번복 금지)
### D-1. 범위 = 커머스 API 전 기능, 단 N/A·무권한 제외
- 커머스솔루션 그룹(비즈월렛·자체결제·솔루션사용)은 솔루션 개발사용 → 자가판매자(type=SELF) **N/A 제외**.
- 실제 호출 가능 여부는 앱 부여 scope에 의존. 페이즈 착수 전 라이브 프로브로 권한 확인(원칙 22).
### D-2. 읽기·사실 먼저 → 쓰기 나중 (Jino: 범위·순서 "선호 없음" → Claude 권고 채택)
- 읽기 페이즈 N1~N5 = 패널 표시(이 시스템 본령, D-3 사실주의). 쓰기 N6~N8 = 쿠팡처럼 dry_run+confirm 이중확인.
### D-3. 시스템은 사실/지표 정리만 — 전략 추천 안 함 (쿠팡 트랙 D-3 계승).
### D-4. 아키텍처 = clients/naver(SA, 그룹별) → services/naver(Harness) → routers/naver_* → 패널 탭 (쿠팡과 동일).
### D-5. 정산(pay-settle) scope 부여 확인 ✅ (2026-06-04 서버 라이브 프로브)
- `GET /v1/pay-settle/settle/daily` 실데이터 반환: settleAmount/paySettleAmount/commissionSettleAmount/benefitSettleAmount/payHoldbackAmount 등.
- 현재 네이버 PG수수료는 orders.commission_amount(추정). 정산 API가 실측 수수료·정산금액 → 이익 정밀화 다리.
### D-6. 이익 정밀화 = 건별 정산 실측 수수료 + 하이브리드 폴백 (2026-06-04 전수조사 후 확정)
- 출처: [docs/references/13_naver_settlement_and_order_fee_fields.md](../../references/13_naver_settlement_and_order_fee_fields.md) (정산·수수료·주문 API 필드 전수 조사).
- **데이터 소스 = 건별 정산 `GET /v1/pay-settle/settle/case`** (productOrderId 그레인, 실측). 일별 정산은 정산예정일 합계라 주문 매칭 불가 → 이익 교체엔 부적합.
- **periodType=PAY_DATE(결제일)** 로 조회 → 우리 주문 order_date(=결제일)와 동일 그레인. orderId+productId로 집계해 orders(order_number, platform_product_id) 매칭.
- 실측 판매자부담 수수료 ≈ totalPayCommissionAmount + sellingInterlockCommissionAmount + freeInstallmentCommissionAmount (★부호는 라이브 프로브로 확인 — 일별정산은 음수).
- **하이브리드 폴백(Jino 확정)**: 정산된 주문=실측 수수료, 미정산 최근 주문=현재 주문API 예상 수수료(commission_amount) 유지. 패널에 "실측 N건/예상 M건" 투명 표시(D-3 사실주의). 이익 왜곡 방지.
- 현재 commission_amount는 "추정"이 아니라 주문 API 주문시점 **예상** 수수료(payment+sale+channel+knowledgeShoppingSellingInterlock 합산). 정밀화 = 사후변동(취소·우대수수료환급·차액·무이자할부·유입수수료) 반영.
### D-7. 주문 그레인 productOrderId 세분화 = 분할 라인 매출 누락 버그 수정 (2026-06-04, codex 합의)
- **버그**: `naver.py fetch_orders`가 같은 (order_id, product_id)의 분할 productOrderId 2번째를 dedup-skip → 그 라인 수량·매출 누락. Order 그레인/`uq_order_item`이 (channel,order,product)라 1행만 저장(이중 봉쇄). 라이브 표본 1.4%(23/1634).
- **비대칭 왜곡**: 수수료(settle/case)는 (order,product) group_by sum으로 전액 반영되는데 매출은 일부만 → 실효 수수료율 과대·순이익 과소. D-6 이익 정밀화와 정면충돌이라 수정.
- **수정(Option B, Jino 승인)**: Order에 `platform_order_line_id` 추가 → `uq_order_item` 4컬럼화(네이버=productOrderId, 쿠팡/cafe24=""이라 동작 불변). `naver.py`는 productOrderId 단위 emit, `sync_service` dedup/lookup/insert에 line_id 포함. 기존 naver_ops `group_by(order,product) sum`이 그대로 합산 + status 필터가 라인 단위로 작동(부분취소 mixed-status 정확).
- **마이그레이션**: alembic `f0a1b2c3d4e5` — 컬럼 추가 + raw_data의 productOrderId로 기존 네이버 행 line_id 백필(dev 1293/1293 100%) + uq 4컬럼 교체(batch). 백필은 best-effort.
- **호환 브리지(codex P1 합의)**: 백필이 못 채운 레거시 line_id="" 행이 재동기화 시 이중 적재되지 않도록 sync_service가 승격(promote). 쿠팡/cafe24는 line_id=""이라 미해당.
- **codex 검증**: P1(중복적재 위험) 핵심 수용→브리지+테스트 추가, PG/JSONB-dict 전제는 기각(raw_data는 TEXT·prod sqlite). P2(settlement 테이블 누락) 기각(d4f6a8c0e2b3가 생성, 컨텍스트 부재). P2(str(None) 오염) 수용→`or ""` 적용. 회귀 테스트 3개 통과(backend/tests/test_naver_order_split.py).
- **✅ prod 배포·라이브 실증(2026-06-04, os.ohitech.co.kr:8001 pm2 ohisell-backend)**: DB 백업→코드5파일 scp→alembic f0a1b2c3d4e5 적용(백필 naver 4989/4990, 비네이버 영향 0)→pm2 restart→채널6 재동기화(신규20/갱신940). **분할 보존 그룹 18개(수정 전 0)**, (order,product,line_id) 중복 0, 빈 line_id 잔존 1행(7일창 밖 historical). sales-summary 200(매출 33,433,810/이익 6,386,379/19.10%). ⚠️ 로컬 git은 uncommitted(커밋은 Jino 지시 시).
### D-8. 네이버 운영 패널 이익 회계 정확화 = 배송비 회계 + 공급가(VAT 제외) 통일 (2026-06-04, Jino 지시·codex 합의)
- **범위 한정**: 네이버 운영 패널 `naver_ops.py` sales-summary **만**. 메인 엔진 `profit_calculator`·쿠팡·cafe24는 **건드리지 않음**(Jino 명시). 메인은 향후 별도(전 채널 VAT 확인 필요).
- **버그였던 것**: 기존 패널 공식 `매출 − 수수료 − 원가 − 광고 − 배송비`에서 배송비=고객배송비(deliveryFeeAmount)를 **비용으로 차감**(부호 반대, 이건 매출). 한진 물류비 누락. VAT 미반영.
- **수정**: ① 고객배송비 → 매출 가산. ② 한진 물류비 신설 = packageNumber distinct 배송건 × 1900(메인 엔진과 동일 기준, NULLIF+COALESCE). ③ **공급가 통일**: 순이익 = (상품매출 + 고객배송비 − 수수료 − 원가 − 한진물류비) ÷ 1.1 − 광고비.
- **VAT 상태 확정(라이브 검색·Jino 확인)**: 매출=VAT포함(소비자가), 원가·한진물류비=VAT포함(Jino), 수수료=VAT포함(네이버 공식 "부가세 포함 수수료율"), **광고비=공급가(VAT 제외, 네이버 광고는 세금계산서 별도)**. → VAT포함 항목만 ÷1.1, 광고비 그대로.
- **표시**: 전 금액 공급가 기준(매출−비용=이익 일관), 총매출에 VAT포함액 병기. 상품별=순수 상품손익(상품매출−수수료−원가)÷1.1, 배송·물류·광고는 요약만.
- **codex review**: [P1] 0건. 회계모델 정확성 전부 확인(VAT 이중적용 없음·공급가 환산·배송비 매출가산·by_product 제외). [P2] packageNumber 빈문자열 fallback → NULLIF 추가 합의.
- **✅ prod 라이브(2026-06-04)**: 30일 매출(공급가) 33,078,918(VAT포함 36,386,810)/배송매출 2,634,090/한진 2,938,090(1701건)/**이익 6,683,743·20.21%**. 검산 전부 통과. 수정 전(틀린) 6,153,918·18.6% → +53만 정확. ⚠️ 로컬 git uncommitted.
- ※ 메타: 자사몰/쿠팡 동일 정확화는 채널별 수수료·광고비 VAT 미확인(쿠팡 판매수수료, 쿠팡/메타 광고비, cafe24 PG) → 추정 금지, 확인 후 별도 진행.

### D-9. N6 발주/발송 처리 = 쓰기 3종(전부 POST) + 라이브 미발송 조회 + dry_run 기본 (2026-06-04, Jino 승인·codex 합의)
- **★스펙 정정**: 발주확인/발송/발송지연 전부 **POST** (HANDOFF엔 PUT으로 오기 → API센터 실측으로 정정). 추측 금지 원칙으로 Jino가 API센터 스크린샷 제공, 전수 기록 = [docs/references/14_naver_order_write_apis.md].
  - 발주확인 `POST .../confirm` {productOrderIds[]} (최대30) · 발송 `POST .../dispatch` {dispatchProductOrders[]} (최대30) · 발송지연 `POST .../{productOrderId}/delay` (단건, poid는 path).
- **대기 목록 = 라이브 조회(Jino 선택 "2")**: `fetch_pending_orders` = last-changed(최근 days) → 상세 → PAYED 건 분류. **prod raw_data 실측(원칙22)**: PAYED+placeOrderStatus=NOT_YET=발주확인 대기, PAYED+OK=발송 대기. 발송 폼 기본값=expected_delivery_company/expected_delivery_method/shippingDueDate.
- **안전(D-2 계승)**: dry_run=true 기본 → 네이버 미호출, would_send만 반환. 실제 실행은 dry_run=false 별도 버튼. confirm/dispatch 최대30 검증, DELIVERY는 택배사+송장 필수, 날짜 ISO8601(+09:00) 정규화/검증.
- **아키텍처(D-4)**: clients/naver.py(SA: confirm_orders/dispatch_orders/delay_order/fetch_pending_orders, 쓰기는 4xx 본문 surface하는 _request_write) → routers/naver_ops.py(Harness: 검증+dry_run 분기) → NaverOps.tsx 📦 섹션(대기목록→선택→dry_run 모달→실행).
- **codex review**: P1 0건(바디키·메서드·최대30·dry_run 안전 확인). P2 4건 전부 수용·수정: ①delay would_send를 path+body 분리 ②날짜 ISO8601 정규화/검증 ③쓰기 실패 구조화 detail(naver_status/error/data) ④발송 UI에 deliveryMethod 드롭다운+expected_delivery_method 시드(비택배 지원).
- **✅ prod dry_run 라이브 실증(2026-06-04)**: 발주확인 대기 14건 조회, confirm/dispatch/delay dry_run would_send 정확, date-only 정규화·검증 400 동작.
- **✅✅ 실제 쓰기 end-to-end 실증(2026-06-04)**: 발주확인 1건(POID 2026060470576381) dry_run=false 실행 → 네이버 successProductOrderInfos 반환·fail 0. 재조회로 NOT_YET→OK(발주확인대기→발송대기) 이동 확인. ※발송처리는 가짜 송장 실주문 처리 위험으로 실검증 제외(dry_run만). ⚠️ 로컬 git uncommitted.

### D-10. N7 클레임 = 전부(12개 쓰기) 구현, 취소 1순위, 3파동 순차 (2026-06-04, Jino 지시)
- **범위**: 취소/반품/교환 전체. Jino "전부 하자" + "취소가 가장 많다" → 취소 우선.
- **3파동(원칙 5, 각 파동 codex+배포)**: ①취소(취소요청승인·취소요청 2) → ②반품(요청·승인·보류·보류해제·거부 5) → ③교환(수거완료·재배송·보류·보류해제·거부 5).
- 공통 응답: 주문-클레임 처리 반환 구조체 {timestamp, traceId, data}. + 클레임 현황 조회(읽기)로 처리 대상 표시.
- N6 동일 원칙: dry_run+confirm, 스펙은 API센터 실측만(추측 금지), 스펙 누적 → docs/references/14(또는 신규).
- **✅ wave2 반품 완료(2026-06-04, prod 라이브 dry_run + codex 통과)**: 5종 전부 POST·단건 path. 스펙=docs/references/14 N7 wave2 (API센터 실측).
  - approve(body없음) · reject{rejectReturnReason 자유텍스트} · holdback{holdbackClassType enum14 + holdbackReturnDetailReason + extraReturnFeeAmount?} · holdback/release(body없음) · request{returnReason enum11 + collectDeliveryMethod enum11 + collectDeliveryCompany? + collectTrackingNumber? + returnQuantity?(생략=전체)}.
  - 아키텍처: naver.py(approve_return/reject_return/holdback_return/release_return_holdback/request_return) → naver_ops.py(/claims/return/{approve,reject,holdback,holdback/release,request}, dry_run 기본, enum/길이/수량 검증) → api.ts(naver*Return 5함수 + 3 enum 상수) → NaverOps.tsx(⚖️ RETURN_REQUEST행 승인/거부/보류 버튼 + 직접반품요청·보류해제 헤더 모달).
  - **codex review: P1 0 / P2 0** (반품 path/body·no-body null·camelCase·필수검증·선택생략 전부 확인).
  - **prod dry_run 라이브 실증**: 5종 would_send 정확, 검증400 6종(빈사유/잘못enum×3/음수비용/빈poid) 동작. **라이브 RETURN_REQUEST 5건 존재** → 실제 승인/거부는 실고객·실환불이라 Jino 건별 결정(미실행, dry_run까지만).
- **✅ wave3 교환 완료(2026-06-04, prod 라이브 dry_run + codex 통과)**: 5종 전부 POST·단건 path, 경로 `claim/exchange/*`. 스펙=docs/references/14 N7 wave3 (API센터 실측).
  - collect/approve(body없음) · dispatch{reDeliveryMethod?·reDeliveryCompany?·reDeliveryTrackingNumber? 전부 선택} · holdback{holdbackClassType enum14(반품과 동일) + holdbackExchangeDetailReason + extraExchangeFeeAmount?} · holdback/release(body없음) · reject{rejectExchangeReason 자유텍스트}.
  - 아키텍처: naver.py(approve_exchange_collect/dispatch_exchange/holdback_exchange/release_exchange_holdback/reject_exchange) → naver_ops.py(/claims/exchange/* 5엔드포인트, _VALID_HOLDBACK_CLASS_TYPES·_VALID_COLLECT_DELIVERY_METHODS 재사용) → api.ts(naver*Exchange 5함수) → NaverOps.tsx(⚖️ EXCHANGE_REQUEST행 수거완료/거부/보류 + COLLECT_DONE행 재배송 + 교환보류해제 헤더 모달).
  - **codex review: P1 0 / P2 2 → 합의 수정**: dispatch DELIVERY 시 택배사+송장 XOR 강제 제거(스펙상 전 필드 선택, 추측금지·네이버가 권위) + 모달 "택배 시 필수" 라벨 → "(선택)".
  - **prod dry_run 라이브 실증**: 5종 would_send 정확(부분입력 허용 확인), 검증400 5종 OK. 라이브 EXCHANGE_REQUEST 3·COLLECT_DONE 1 존재 → 실쓰기 Jino 결정(미실행).
  - ★N7 클레임 전체(취소+반품+교환 12쓰기) 완료.

### D-11. N8 상품 쓰기 = **판매 상태 변경(change-status) 하나만** (옵션재고·가격·수정·등록 전부 제외) (2026-06-05, Jino 지시)
- **범위 축소 2단계 확정**:
  1. Jino "그럼 수정과 쓰기는 활성화 시키지 말자 위험할 수도 있겠다" → "맞아": 상품 **수정(채널/원상품 수정)·등록 제외**(라이브 상품 손상/오등록 위험).
  2. prod 상품구조 실측 후 Jino "그래": **옵션 재고 변경(option-stock)도 제외** → **판매 상태 변경만** 구현.
- ✅ **구현: 판매 상태 변경** — `PUT /v1/products/origin-products/:originProductNo/change-status`
  - 품절(`OUTOFSTOCK`, 재고0) / 재입고·재판매(`SALE`+stockQuantity) / 판매중지(`SUSPENSION`). 패널은 이 3개만 노출(DELETE 등 시스템상태 노출 금지).
  - ★ **가격(salePrice)을 전혀 안 받음 → 가격 손실 위험 0**. 원상품(전체) 단위 재고/상태.
- ❌ **옵션 재고 변경(option-stock) 제외 — prod 실측 근거(원칙 22)**: 오하이는 **원상품 단위 재고**(각 origin_product_no에 stock 1개, 변종은 group_product_no로 묶인 별도 원상품). 옵션별 재고 미사용. option-stock은 salePrice 필수(가격 위험)+복잡 → 불필요·위험으로 제외. (표본 1,202개 확인)
- ❌ 가격·할인 UI / 상품 수정 / 상품 등록 / 멀티변경 / 삭제 — 전부 제외(위험). 향후 필요 시 Jino 재승인 후 별도 D-N.
- **원칙 계승**: dry_run=true 기본(실쓰기는 Jino 건별), 스펙은 API센터 실측만(추측 금지). 스펙=[docs/references/15_naver_product_write_apis.md] N8-2.
- **전이 규칙 검증 반영**: 품절→판매중 전환 시 stockQuantity 필수, SALE→OUTOFSTOCK은 재고0 자동, 재고0이면 OUTOFSTOCK 유지.

## 3. 페이즈 체크리스트
읽기·사실 (패널 표시):
- [x] **N1. 정산(일별)** — `/v1/pay-settle/settle/daily` 적재(naver_settlement_daily 테이블·alembic c3d5e7f9a1b2)·스케줄러(05:25)·패널 정산 섹션. prod 라이브 실증: 30일 정산 29,958,779 / 실측수수료 -1,304,731 (2026-06-04).
- [x] **N1·D-6. 이익 정밀화(건별정산 실측 수수료)** — `/v1/pay-settle/settle/case` 적재(naver_settlement_case 테이블·alembic d4f6a8c0e2b3)·스케줄러(05:30)·sales-summary 하이브리드 매칭. **prod 라이브 실증(2026-06-04)**: 건별정산 3257건 적재, sales-summary 30일 실측 1208라인/예상 597라인(67% 실측), 7일 실측66/예상434(최근 미정산 폴백 정확). codex review P1×2 데이터로 검증(productOrderId중복 0, 부분정산 0)·P2-2 청크 수정 합의. 부호 라이브 프로브 확인(수수료 음수→반전).
- [ ] N1 잔여(선택): 수수료 상세(commission-details, 타입별 분해)·부가세(vat, 세무용) — 이익 정밀화엔 불필요, 분석/세무 필요 시
- [x] **N2. 통계(데이터솔루션) — SKIP**: 전체 그룹이 [브랜드스토어 전용]. prod 라이브 프로브 403 확인(2026-06-04). 스마트스토어에서 사용 불가.
- [x] **N3. 문의(CS)** — `GET /v1/pay-user/inquiries` 조회. 백엔드 `/api/naver/ops/inquiries`, 프론트 💬섹션(카드3개+테이블). prod 라이브 실증(2026-06-04).
- [x] **N4. 상품 조회** — `POST /v1/products/search` 조회. 백엔드 `/api/naver/ops/products`, 프론트 🛍️섹션(재고카드+테이블). prod 라이브 실증: 판매중 692개(2026-06-04).
- [x] **N5. 판매자정보** — `GET /v1/seller/account` + `/v1/seller/channels` 조회. 백엔드 `/api/naver/ops/seller`, 프론트 🏪섹션. prod 라이브 실증: theohi/등급02/채널 오하이 Ohi(2026-06-04).
쓰기·운영 (dry_run+confirm):
- [x] **N6. 발주/발송 처리** — 발주확인/발송/발송지연 3종(전부 POST) + 라이브 미발송 조회. dry_run+confirm 이중확인. **prod dry_run 라이브 실증(2026-06-04)**: 발주확인 대기 14건 조회, confirm/dispatch/delay dry_run would_send 정확, 검증 400 동작. ★실제 쓰기(dry_run=false)는 미실행 — Jino와 함께 1건. 스펙=[docs/references/14_naver_order_write_apis.md](../../references/14_naver_order_write_apis.md). 상세 D-9.
- [x] **N7. 클레임 (취소/반품/교환) — 전체 완료**(3파동 전부 prod 라이브). 클레임 조회·취소(승인/직접요청 2)·반품(승인/거부/보류/보류해제/직접요청 5)·교환(수거완료/재배송/보류/보류해제/거부 5). **prod 실증(2026-06-04)**: GET /claims 119건. 각 파동 dry_run would_send 정확·검증400 OK·codex 통과(wave3 P2 2건 합의 수정). 실쓰기는 라이브 대기건(RETURN_REQUEST 5·EXCHANGE_REQUEST 3 등) 존재하나 실고객·실환불이라 Jino 건별 결정(미실행). 스펙 전체=docs/references/14.
- [x] **N8. 상품 쓰기 — 판매 상태 변경(change-status)만 — 완료** (옵션재고·가격·수정·등록 전부 제외, D-11). 품절/재입고(SALE+수량)/판매중지 3상태. 가격 안 건드림(위험0). dry_run+confirm. 오하이=원상품 단위 재고(옵션 미사용, prod 실측). 스펙=[docs/references/15_naver_product_write_apis.md](../../references/15_naver_product_write_apis.md) N8-2. **prod 배포·dry_run 라이브 실증(2026-06-05, 실상품 13504079747 7케이스 전부 통과)·codex 2차 pass**. 실쓰기(dry_run=false)는 실상품 영향이라 Jino 결정(미실행).

## 4. 공식 API 그룹 (v2.79.0, 2026-05-26 기준 — 출처 apicenter.commerce.naver.com)
인증1 · API데이터솔루션(통계)5 · N배송(SKU)1 · 문의3 · 상품21 · 정산2 · 주문5 · 커머스솔루션4(N/A) · 판매자정보4

## 5. 현재 사용 중이던 것 (트랙 이전, 이번 세션 ad-hoc 포함)
- 주문 조회: last-changed-statuses + product-orders/query (NaverClient.fetch_orders)
- 운영 패널(NaverOps): 매출/이익/이익률 + GFA 광고비 업로드 + 검색광고 전환매출·RoAS(SA API 별도)

## 6. 현재 진행 단계
- **N1~N8 완료**(N2 skip). ★트랙 사실상 완료 — 읽기 N1·N3·N4·N5 + 쓰기 N6(발주/발송)·N7(클레임 12)·N8(상품 판매상태).
- **N8 완료**(2026-06-05): 판매상태 변경(change-status)만, prod 배포·dry_run 라이브 실증(7케이스)·codex 2차 pass. 가격 안 건드림(위험0). 옵션재고/가격/수정/등록은 D-11로 제외.
- ★ 미실행(실고객·실데이터, Jino 건별 결정): 라이브 클레임 대기건(RETURN_REQUEST 5·EXCHANGE_REQUEST 3·COLLECT_DONE 1 등) + N8 판매상태 실쓰기(dry_run=false).

## 7. 다음 액션
- **(선택) 실 클레임/실 판매상태 처리**: Jino가 건별 결정 시 dry_run=false 실행.
- **(선택) 트랙 완료 처리**: 남은 읽기 잔여(N1 commission-details/vat — 이익정밀화엔 불필요) 외 핵심 전부 완료 → completed/로 이동 검토.
- **git 커밋**: N8(이번 세션)은 prod 배포 완료·git 미커밋. Jino 지시 시 커밋. (N1~N7은 main f74ead7까지 커밋 완료)
