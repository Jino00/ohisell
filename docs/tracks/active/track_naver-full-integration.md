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

## 3. 페이즈 체크리스트
읽기·사실 (패널 표시):
- [x] **N1. 정산(일별)** — `/v1/pay-settle/settle/daily` 적재(naver_settlement_daily 테이블·alembic c3d5e7f9a1b2)·스케줄러(05:25)·패널 정산 섹션. prod 라이브 실증: 30일 정산 29,958,779 / 실측수수료 -1,304,731 (2026-06-04). 잔여: 건별정산·수수료상세·부가세 + 이익계산에 실측수수료 반영(차기)
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
- N1 착수: 정산 scope 확인 완료. 일별 정산 적재 + 패널 표시 구현 중.

## 7. 다음 액션
- N1 백엔드(일별 정산 SA·테이블·sync·스케줄러) → 패널 정산 섹션 → prod 배포·라이브 실증.
