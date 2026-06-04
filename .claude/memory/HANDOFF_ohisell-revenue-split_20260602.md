# 세션 인수인계: ohisell-revenue-split
> 저장일시: 2026-06-02
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (venv: `backend/.venv/bin/python3`)
- 프론트엔드 실행: `cd frontend && npm run dev`
- 프론트 빌드: `cd frontend && npm run build`
- 프로덕션 URL: https://sellc.ohitech.co.kr
- 서버 SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`
- 프로덕션 경로: `/home/ubuntu/ohisell/`
- pm2 프로세스: `ohisell-backend` (id 0)
- 검증용 DB 복사본: `/tmp/ohisell_verify.db` (scp로 받음, 재사용 가능 — 단 SQLAlchemy 연결 후 일부 케이스에서 truncate 사례 있었음, 필요 시 재다운로드)
- 주요 환경변수: `COUPANG_ACCESS_KEY`, `COUPANG_SECRET_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `CAFE24_CLIENT_ID`, `CAFE24_CLIENT_SECRET` (값 제외, 이름만)

## 2. 이번 세션 완료 목록

### Sprint 4B-revenue-split (2026-05-20, commit 433d99a) — 제품매출/배송비매출 분리
- ✅ `backend/app/schemas.py`: TrendPoint / GroupedSummaryRow / GroupedTrendPoint / ProductProfitRow에 `product_revenue`, `shipping_revenue` 필드 추가 (str, 기본값 "0"). SettlementOut에 `product_amount` 파생 필드 추가.
- ✅ `backend/app/services/profit_calculator.py` 6함수 분리 누적:
  - `calculate_daily_trend`: bucket에 product_revenue/shipping_revenue 누적, 결과 dict에 포함. 수동매출(로켓배송)은 분리 불가라 product_revenue로 일괄.
  - `calculate_channel_summary`: by_channel에 동일. 수동매출 행도 분리 처리.
  - `calculate_product_profit`: 메인 루프에서 product_revenue 누적, `_alloc_to_lines`로 배송수입을 revenue + shipping_revenue + vat 동시 배분 (불변식 보장).
  - `calculate_channel_daily_trend`: daily_trend 래핑이라 propagation만.
  - `group_summary_by_company`, `group_trend_by_company`: 그룹 블록에 누적 + 출력.
  - `_agg_block`, `_finalize`에 두 필드 추가.
- ✅ `backend/app/routers/dashboard.py`: weekly/monthly 그룹핑(`/trend` 엔드포인트)에 두 필드 propagation.
- ✅ `backend/app/routers/settlements.py` `_settlement_to_out`: `product_amount = total_amount - shipping_fee`, 음수 가드(0으로 클램프).
- ✅ `frontend/src/lib/api.ts`: TrendItem, GroupedSummaryRow, GroupedTrendPoint, ProductRanking, SettlementItem에 옵셔널 필드 추가.
- ✅ `frontend/src/pages/Dashboard.tsx`:
  - 기간 요약표: [채널 | 제품매출 | 배송비매출 | 총매출 | 광고비 | RoAS | 순이익 | 이익률] 8컬럼
  - 상품별 랭킹 테이블: [순위 | 상품명 | 제품매출 | 배송비매출 | 총매출 | 원가 | 수수료 | 광고비 | 순이익 | 이익률] 10컬럼
  - KPI 카드/4차트는 미변경 (사용자 결정대로)
- ✅ `frontend/src/pages/Settlements.tsx`: [정산일 | 채널 | 제품정산 | 배송정산 | 정산액 | 수수료 | 순정산 | 삭제] 8컬럼
- ✅ `frontend/src/pages/Orders.tsx`:
  - 채널별 분기: cafe24=0 (판매자 비용이라 매출 아님), NAVER=라인별 shipping_cost, 쿠팡=order_number 단위 dedup(first-wins)
  - IIFE로 seenCoupangBox Set 추적
- ✅ codex review 3라운드 (원칙 19 대화형):
  - R1 P2: cafe24의 shipping_cost를 그대로 표시 → 채널별 판정 추가로 수정
  - R2 P2: 쿠팡 멀티라인 박스 라인 복사 double-count → order_number dedup으로 수정
  - R3 P2: 1 orderId → N shipmentBoxId 분할 케이스 → 실데이터 0건 확인 후 Jino 결정으로 현행 유지 (안전한 underestimate)
- ✅ 로컬 검증: 6함수 64건 모두 불변식(`product + shipping == revenue`) 통과. NAVER 2026-05-18: 899,710 + 92,500 = 992,210 ✓. cafe24 배송수입 0 ✓.
- ✅ 프로덕션 배포: backend 4파일 + frontend dist rsync + pm2 restart. ohisell-backend online.
- ✅ 프로덕션 검증: `/api/dashboard/channel-breakdown?date_from=2026-05-18&date_to=2026-05-18` 7행 모두 불변식 통과. 쿠팡 로켓그로스·윙에서 5,000원 배송수입 신규 발견.

### 대시보드 기본 기간 어제 1일치 변경 (2026-05-19, 별도 commit)
- ✅ `frontend/src/pages/Dashboard.tsx` `getDefaultDateRange()` `quickRange(30)` → `quickRange(1)`
- ✅ 빌드 + 프로덕션 dist rsync 완료

### 이전 세션 마무리 (2026-05-19)
- ✅ Sprint 4B-vat-fix (commit 60674fa) — calculate_channel_summary/product_profit에 VAT 차감 추가, 4화면 일치
- ✅ failures.jsonl 기록 2건 (배송비 컬럼 채널별 의미 상이, VAT 미차감 버그)

## 3. 확정된 결정사항 (Jino, 번복 금지)

### 매출 분리 정책
- **제품매출** = selling_price × quantity (라인 단위 상품 매출)
- **배송비매출** = 고객이 결제한 배송비 (NAVER deliveryFeeAmount / 쿠팡 shippingPrice)
- cafe24와 기타 채널은 고객 무료배송 → 배송비매출 = 0
- 수동매출(로켓배송)은 분리 불가 → 전액 product_revenue로 표시
- 불변식: `product_revenue + shipping_revenue == revenue` (모든 함수 출력)
- API 응답에 기존 `revenue` 필드 유지(=합) → KPI 카드/차트 등 미변경 화면 호환성 보장

### 이익률 분모
- 카드 이익률 = net / 전체매출(로켓 포함) — 현행 유지
- 테이블 전체 행 이익률 = net / 측정가능매출(로켓 제외) — 현행 유지
- 두 지표 의미 다름. 같은 화면 두 표시값이 다르더라도 by-design.

### VAT 기준
- 표시매출(제품+배송) 전체에 10/110 적용 — 현행 유지

### 배송비 회계 (이전 sprint 확정)
- 한진택배 1,900원 / 물리배송 1건, 전 채널 동일
- 배송단위 = NAVER packageNumber / 쿠팡 shipmentBoxId / cafe24 order_number

### Orders 페이지 쿠팡 dedup 정책
- order_number 단위 first-wins dedup (백엔드 _shipment_key와 다름)
- 1 orderId → N shipmentBoxId 분할 케이스(이론적으로 가능, 실데이터 0건)는 미지원 — 발생 시 first-box만 표시되어 안전한 underestimate
- 향후 발생 빈도 증가 시 백엔드 OrderOut에 shipment_key 노출 검토

## 4. 핵심 파일 목록

| 파일 | 역할 |
|------|------|
| `backend/app/services/profit_calculator.py` | 이익 계산 엔진 (6함수 + 그룹2함수). 이번 sprint 핵심 변경 |
| `backend/app/schemas.py` | TrendPoint/GroupedSummaryRow/GroupedTrendPoint/ProductProfitRow/SettlementOut 응답 스키마 |
| `backend/app/routers/dashboard.py` | /kpi, /trend, /channel-breakdown, /trend-by-channel, /product-ranking. weekly/monthly grouping propagation |
| `backend/app/routers/settlements.py` | _settlement_to_out에 product_amount 파생 |
| `backend/app/clients/naver.py:182,208` | shipping_cost=deliveryFeeAmount, selling_price=totalPaymentAmount (상품only 검증됨) |
| `backend/app/clients/coupang.py:139-148` | shipping_cost=shippingPrice (박스값 라인복사), raw_data=shipment(shipmentBoxId 포함) |
| `backend/app/clients/cafe24.py:278` | ship_per_order=1900(판매자 비용, 매출 아님). 엔진에선 미사용(_delivery_income이 0 반환) |
| `frontend/src/pages/Dashboard.tsx` | 기간요약표·상품랭킹 분리 컬럼 + KPI 카드·차트(미변경) |
| `frontend/src/pages/Settlements.tsx` | 정산표 [제품정산\|배송정산\|정산액] 컬럼 분리 |
| `frontend/src/pages/Orders.tsx` | 채널별 배송비매출 표시 (cafe24=0, NAVER=라인별, 쿠팡=order_number dedup) |
| `frontend/src/lib/api.ts` | TS 타입 정의 (옵셔널 필드로 추가, 기본값 "0"/0) |
| `claude-progress.txt` | sprint 진행 상황 |

## 5. 알려진 이슈 / 주의사항

### 절대 잊지 말 것
- **DB 마이그레이션 없음**: 이번 sprint는 모두 엔진/스키마/UI 변경만. alembic 적용할 것 없음.
- **백엔드 product_revenue 출현 횟수 검증법**: 프로덕션에서 `grep -c product_revenue /home/ubuntu/ohisell/backend/app/services/profit_calculator.py` → 23 확인되면 배포 성공.
- **쿠팡 광고비 누락 (별도 이슈, 미해결)**: `calculate_daily_trend`는 NAVER+cafe24 광고비만 집계. 쿠팡 로켓배송 + 윙1 광고비가 KPI 카드 ad_spend에 안 잡힘. 채널 요약표는 ad_costs 테이블에서 로켓배송 ad_spend 표시하지만 윙1은 주문 0이라 누락. 결과: 전사 순이익이 쿠팡 광고비(평일 ~55만 원) 만큼 과대 표시. 이전 세션에서 보고했고 Jino 인지 상태. 별도 sprint로 처리 필요 (위탁 채널 광고비를 전사 순이익에서 차감하는 정책 결정 + 구현).

### 쿠팡 dedup 한정사항
- Orders.tsx의 쿠팡 배송비매출 dedup은 `order_number` 단위. 백엔드 `_shipment_key`(=`raw.shipmentBoxId`)와 다름.
- 실데이터 2026-04 이후 1 orderId → N shipmentBoxId 케이스 0건.
- 페이지네이션 경계에서 dedup이 끊길 수 있음(같은 박스 라인이 페이지 1·2 분산 시 양쪽 첫 라인에 둘 다 표시). 현재 페이지 단위 IIFE라 이런 케이스 처리 안 함.

### Codex review 1 orderId/N shipmentBoxId 미해결 사항
- 백엔드 OrderOut에 shipment_key 노출하면 정확히 해결 가능. 변경 범위 큼.
- 향후 멀티박스 발생 빈도 모니터링 필요.

### Settlement product_amount 파생 계산
- `product_amount = total_amount - shipping_fee` (음수 가드 0 클램프)
- 엑셀 업로드 시 total_amount가 shipping_fee를 이미 제외한 값인지 포함한 값인지에 따라 의미 달라짐. 현행은 "포함" 가정.

## 6. 다음에 할 작업 (미완료)

### 우선순위 높음 (회계 정확도)
- [ ] **쿠팡 광고비 누락 해결 (별도 sprint)**:
  1. `calculate_daily_trend`에 쿠팡 채널 ad_costs 합산 추가 (consignment + marketplace 모두)
  2. `calculate_channel_summary`에서 by_channel에 없지만 ad_costs 있는 모든 채널(현재는 manual만)을 별도 행으로 추가하도록 확장
  3. 또는 전사 순이익 계산 시 위탁 채널 ad_spend도 차감하도록 정책 결정 (Jino 결정 필요)
  4. 예상 영향: 2026-05-18 표시 순이익 138,003 → 약 -416,918 (실제 적자 노출)

### 우선순위 중간
- [ ] 로켓배송 실제 매출 데이터 입력 (Settings 페이지 활용, manual_revenue)
- [ ] 쿠팡 광고 XLSX 과거분 업로드 (시계열 데이터 보강)

### 우선순위 낮음
- [ ] nginx IP 화이트리스트 또는 Basic Auth (Jino 결정으로 미적용 상태)
- [ ] Sprint 5 후보: 알림 시스템, 재고 관리, 엑셀 리포트 다운로드, 사용자 인증

### codex review 3라운드 R3 미합의 사항 (조건부)
- [ ] 1 orderId → N shipmentBoxId 발생 빈도 증가 시 백엔드 OrderOut에 shipment_key 필드 추가 검토

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-revenue-split_20260602.md 읽고 이어서 작업해줘
```
