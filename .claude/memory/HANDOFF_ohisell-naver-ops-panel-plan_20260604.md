# 세션 인수인계: ohisell 스마트스토어 운영 패널 (계획 단계)
> 저장일시: 2026-06-04
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 다음 작업 = **네이버 스마트스토어 운영 패널** 구현 (쿠팡 운영 패널과 동일 형식, 단순화). 구조 합의 완료, 코딩 직전.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (로컬 venv=Python 3.14)
- 프론트: `cd frontend && npm run dev` / 빌드 `npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`, **포트=8001**, 서버 Python **3.10**, DB=SQLite `backend/ohisell.db`, **git 없음 → 배포=scp**
- ⚠️ scp 전송: `COPYFILE_DISABLE=1 tar --exclude='._*' --exclude='*__pycache__*'`
- 배포 패턴: tar → scp /tmp → 서버 `tar -xzf` → `pm2 restart ohisell-backend` → curl 라이브 검증

## 2. 이번 세션 완료 목록 (쿠팡 운영 패널 — 전부 prod 배포·라이브 실증·커밋 완료)
- ✅ `backend/app/routers/coupang_ops.py`: GET `/api/coupang/ops/sales-summary` (회사별·기간별·채널타입별 매출/이익 집계) + GET `/products/items`
- ✅ `frontend/src/pages/CoupangOps.tsx`: 신규 페이지 (사이드바 🔧 쿠팡 운영, App.tsx `/coupang-ops` 라우트)
- ✅ 기간: 오늘(days=0)/어제/7일/15일/30일. 회사 탭(전체/오픽스/오하이테크). 채널 필터(Wing/로켓그로스/로켓배송)
- ✅ 요약 카드: 총매출·수수료·원가·광고비·배송비·이익(파란/빨강 테두리)·이익률 + 광고전환매출·RoAS
- ✅ 상품별 테이블: 상품명+옵션명 한줄, 총매출·광고비·광고전환매출·RoAS·**이익·이익률** 컬럼, 정렬(↑↓)·엑셀식 값 필터(⊟)
- ✅ 🔄 동기화 버튼 (POST /api/scheduler/trigger/auto_sync_orders → 3초후 재조회)
- ✅ Wing 주문 상태 수집 확대: FINAL_DELIVERY+DELIVERING+DEPARTURE+INSTRUCT+ACCEPT (`backend/app/clients/coupang/channel.py`, seen set 중복방지)
- ✅ RG 주문 누락 수정: account_key WING코드 저장 대응 + paid_at KST(UTC보정 제거) + channel_type 강제 로켓그로스
- ✅ 이익 공식 확정: **매출 − 수수료 − 원가 − 광고비 − 배송비**
  - 수수료: orders.order_number ↔ coupang_revenue_fee.order_id 직접 매칭(실거래), 미매칭은 기본율 7.8%
  - 원가: product_master.cost_price (product_channel_mapping coupang·is_active, D-12 경로)
  - 배송비: Wing 1,900원/건, RG는 0
- ✅ 오늘 광고비: days=0이면 coupang_ad_option_daily 최신 report_date 자동 사용 + 응답 ad_ref_date + 판매/광고 섹션 분리 표시
- ✅ 전 백엔드 KST 통일: `backend/app/utils/kst.py` (kst_now/kst_today) 신설, 12개 파일 datetime.now()/date.today() 교체
- ✅ git push origin main 완료. 최신 커밋 **b2cc588**

## 3. 확정된 결정사항 (번복 금지)
- **이익 공식 = 매출 − 수수료 − 원가 − 광고비 − 배송비** (빼야 할 것 전부 차감)
- **수수료는 건별 실거래 대조** (하나로 묶지 말 것 — Jino 지적). 쿠팡=정산 order_id 매칭, 미매칭만 기본율
- **쿠팡 기본 수수료율 7.8%** (정산 실측 최빈값: 7.8%=174건 vs 10.5%=6건). saleAgentCommission은 항상 0이라 사용 불가(D-13)
- **모든 시간 KST 통일** (app/utils/kst.py)
- **쿠팡 ordersheets API 1~2시간 lag** — 오늘 실시간은 Wing 포털과 차이 발생, 불가피. UI에 안내 표시함

## 4. ★ 다음 작업 = 네이버 스마트스토어 운영 패널 (구조 합의 완료)
### 네이버 데이터 현황 (서버 확인 완료)
- 네이버 주문: 4,975건, **오늘까지 실시간 동기화됨** (order_date 2026-06-04 13:22까지)
- Naver SA 광고비: ad_costs 테이블 342건, 총 12,254,274원, 최신 2026-06-02 (source LIKE 'naver_sa%', 자동 일별 적재)
- **PG수수료**: orders.commission_amount 필드에 이미 저장됨 (건당 592원 등) — 별도 매칭 불필요
- **배송비**: orders.shipping_cost 필드 (네이버 deliveryFeeAmount)
- ad_costs 컬럼: id, channel_id, product_id, ad_date, ad_spend, ad_revenue, source, created_at

### 쿠팡 대비 빠지는 것 (네이버는 단순)
- ❌ 회사 탭 (네이버=주식회사 오하이 단일)
- ❌ 채널 필터 (Wing/RG/로켓배송 → NAVER 단일)
- ❌ Wing 1,900원 배송비 구분 → orders.shipping_cost 그대로 사용
- ❌ RG 별도 테이블 처리
- ❌ 정산 order_id 매칭 → commission_amount가 이미 주문에 있음

### 네이버 패널 구성 (합의됨)
- 기간 선택 (오늘/어제/7일/15일/30일) + 🔄 동기화 버튼
- 요약 카드: 총매출 · PG수수료 · 원가 · 광고비 · 배송비 · 이익 · 이익률
- 상품별 테이블: 상품명 · 총매출 · 광고비 · 이익 · 이익률 (정렬·필터)
- 이익 = 매출 − PG수수료(commission_amount) − 원가 − 광고비 − 배송비(shipping_cost)
- 사이드바 새 메뉴 (예: 🛒 스마트스토어), App.tsx 라우트 추가

### 구현 방식 제안
- 백엔드: 신규 라우터 `backend/app/routers/naver_ops.py` GET `/api/naver/ops/sales-summary` (coupang_ops.py 패턴 복제·단순화)
- 광고 매칭: ad_costs는 product_id 기준 → orders.product_id 또는 상품명 매칭 경로 확인 필요 (구현 전 데이터 점검)
- 프론트: `frontend/src/pages/NaverOps.tsx` (CoupangOps.tsx 복제·단순화)

## 5. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/routers/coupang_ops.py` | 쿠팡 운영 패널 백엔드 (sales-summary 집계 — 네이버 복제 원본) |
| `frontend/src/pages/CoupangOps.tsx` | 쿠팡 운영 패널 프론트 (네이버 복제 원본) |
| `frontend/src/lib/api.ts` | API 클라이언트 + SalesSummary/SalesProductRow 타입 |
| `frontend/src/App.tsx` | 라우트 (`/coupang-ops` 등록됨, 네이버 추가 위치) |
| `frontend/src/components/Layout.tsx` | 사이드바 NAV_ITEMS (메뉴 추가 위치) |
| `backend/app/utils/kst.py` | KST 전역 유틸 (kst_now/kst_today) |
| `backend/app/services/scheduler_service.py` | auto_sync_orders 잡 (네이버 포함 전체 채널 동기화) |
| `docs/tracks/active/track_coupang-full-integration.md` | 쿠팡 메가 트랙 (단일 진실원천) |

## 6. 다음에 할 작업 (미완료)
- [ ] 네이버 광고비-주문 매칭 경로 데이터 점검 (ad_costs.product_id ↔ orders.product_id 또는 상품명)
- [ ] `backend/app/routers/naver_ops.py` 신규 (GET /api/naver/ops/sales-summary)
- [ ] `frontend/src/pages/NaverOps.tsx` 신규 (CoupangOps 단순화 복제)
- [ ] 사이드바 메뉴 + App.tsx 라우트 추가
- [ ] tsc/vite 빌드 → prod 배포 → 라이브 실증 (네이버 매출/이익 Wing 포털 대조)
- [ ] git 커밋

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-naver-ops-panel-plan_20260604.md 읽고 이어서 작업해줘
```
