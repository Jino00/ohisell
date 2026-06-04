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

## 3. 페이즈 체크리스트
읽기·사실 (패널 표시):
- [x] **N1. 정산(일별)** — `/v1/pay-settle/settle/daily` 적재(naver_settlement_daily 테이블·alembic c3d5e7f9a1b2)·스케줄러(05:25)·패널 정산 섹션. prod 라이브 실증: 30일 정산 29,958,779 / 실측수수료 -1,304,731 (2026-06-04).
- [x] **N1·D-6. 이익 정밀화(건별정산 실측 수수료)** — `/v1/pay-settle/settle/case` 적재(naver_settlement_case 테이블·alembic d4f6a8c0e2b3)·스케줄러(05:30)·sales-summary 하이브리드 매칭. **prod 라이브 실증(2026-06-04)**: 건별정산 3257건 적재, sales-summary 30일 실측 1208라인/예상 597라인(67% 실측), 7일 실측66/예상434(최근 미정산 폴백 정확). codex review P1×2 데이터로 검증(productOrderId중복 0, 부분정산 0)·P2-2 청크 수정 합의. 부호 라이브 프로브 확인(수수료 음수→반전).
- [ ] N1 잔여(선택): 수수료 상세(commission-details, 타입별 분해)·부가세(vat, 세무용) — 이익 정밀화엔 불필요, 분석/세무 필요 시
- [ ] N2. 통계(데이터솔루션) — 판매성과·상품성과·재구매·검색키워드·배송통계
- [ ] N3. 문의(CS) — 고객/상품 문의 조회·답변 (쿠팡 P6 대응)
- [ ] N4. 상품 조회 — 상품목록·재고·가격·카탈로그·카테고리
- [ ] N5. 판매자/물류/SKU — 계정·창고·주소록·SKU
쓰기·운영 (dry_run+confirm):
- [ ] N6. 발주/발송 처리
- [ ] N7. 클레임 (취소/반품/교환)
- [ ] N8. 상품 쓰기 (등록/수정/재고/가격)

## 4. 공식 API 그룹 (v2.79.0, 2026-05-26 기준 — 출처 apicenter.commerce.naver.com)
인증1 · API데이터솔루션(통계)5 · N배송(SKU)1 · 문의3 · 상품21 · 정산2 · 주문5 · 커머스솔루션4(N/A) · 판매자정보4

## 5. 현재 사용 중이던 것 (트랙 이전, 이번 세션 ad-hoc 포함)
- 주문 조회: last-changed-statuses + product-orders/query (NaverClient.fetch_orders)
- 운영 패널(NaverOps): 매출/이익/이익률 + GFA 광고비 업로드 + 검색광고 전환매출·RoAS(SA API 별도)

## 6. 현재 진행 단계
- **N1 완료**(일별정산 + 이익 정밀화 건별정산). 전부 prod 배포·라이브 실증·codex 합의 완료(2026-06-04).
- 다음 = N2 통계(데이터솔루션) 또는 N3 문의. N1 잔여(수수료상세·부가세)는 선택(이익엔 불필요).

## 7. 다음 액션
- N2 통계(데이터솔루션 5종): 판매성과·상품성과·재구매·검색키워드·배송통계. GET 엔드포인트 apicenter 문서 확인 후 착수.
- 또는 N3 문의(CS, 쿠팡 P6 대응) → N4 상품조회 → N5 판매자/물류.
- ※ 미배포 git 변경 있음(이익 정밀화 코드는 prod엔 scp 배포됨, 로컬 git은 uncommitted). 커밋은 Jino 지시 시.
