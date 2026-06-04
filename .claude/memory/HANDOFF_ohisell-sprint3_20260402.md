# 세션 인수인계: ohisell Sprint 3 완료
> 저장일시: 2026-04-02 07:30
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 실행 명령어: `bash scripts/init.sh` (frontend + backend 동시 시작)
- Backend: `http://localhost:8000` (FastAPI, Swagger: `/docs`)
- Frontend: `http://localhost:5173` (React + Vite)
- Python: 3.14, venv: `backend/.venv/`
- 주요 환경변수: `DATABASE_URL`, `COUPANG_WING1_*`, `COUPANG_WING2_*`, `COUPANG_RG1_*`, `COUPANG_RG2_*`, `NAVER_*`, `CAFE24_*`, `AD_DATA_DB_PATH`
- ohi-ad-intelligence 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/ohi-ad-intelligence/`

## 2. 이번 세션 완료 목록

### QA (Sprint 2 검증)
- ✅ 5개 페이지 브라우저 테스트 (Dashboard, Orders, Products, Inventory, Settlements)
- ✅ `frontend/src/pages/Products.tsx` — React key prop 경고 수정 (Fragment key)

### Sprint 3 Backend (11개 파일)
- ✅ `backend/requirements.txt` — apscheduler==3.10.4 추가
- ✅ `backend/app/models.py` — SchedulerState 모델 추가, ProfitReport에 period_type+UniqueConstraint, Settlement에 5개 컬럼 추가
- ✅ `backend/app/schemas.py` — 10개 Pydantic 모델 추가 (TrendPoint, DashboardKPI, ChannelSummaryRow, ProductProfitRow, SettlementOut 등)
- ✅ `backend/app/services/profit_calculator.py` — 순이익 계산 엔진 (광고비 포함, ad_data.db cross-DB Python 레벨 매칭)
- ✅ `backend/app/routers/dashboard.py` — 4개 엔드포인트: /trend, /kpi, /channel-breakdown, /product-ranking
- ✅ `backend/app/routers/settlements.py` — 정산 CRUD + 엑셀 업로드 (openpyxl)
- ✅ `backend/app/services/scheduler_service.py` — APScheduler BackgroundScheduler (06:00 동기화, 06:30 이익 재계산)
- ✅ `backend/app/routers/scheduler.py` — 상태/트리거/토글 API
- ✅ `backend/app/main.py` — lifespan 컨텍스트 매니저 + 3개 라우터 등록 (dashboard, settlements, scheduler)
- ✅ `backend/app/routers/orders.py` — /summary 리팩터 (profit_calculator로 위임)
- ✅ `backend/alembic/versions/1b60d8e50617_sprint3_dashboard_scheduler_settlements.py` — 마이그레이션

### Sprint 3 Frontend (6개 파일)
- ✅ `frontend/package.json` — recharts, date-fns 추가
- ✅ `frontend/src/lib/api.ts` — 8개 새 TypeScript 인터페이스 추가
- ✅ `frontend/src/pages/Dashboard.tsx` — KPI 카드 4개 + Recharts 차트 (매출 추이 ComposedChart, 채널별 PieChart/BarChart, 상품 순위 테이블)
- ✅ `frontend/src/pages/Settlements.tsx` — 정산 업로드 + 요약 카드 + 필터 + 테이블 + 페이지네이션
- ✅ `frontend/src/components/SchedulerStatus.tsx` — 사이드바 자동 동기화 상태 표시기 (초록 도트 + 다음 실행 시간)
- ✅ `frontend/src/components/Layout.tsx` — SchedulerStatus 추가

### 기타
- ✅ `claude-progress.txt` 갱신 (75% 진행률)
- ✅ Sprint 3 QA: 5개 페이지 모두 정상 렌더링 확인, API 0 에러

## 3. 확정된 결정사항
- **차트 라이브러리**: Recharts (React 네이티브, TS 지원)
- **스케줄러**: APScheduler 3.10.4 (단일 인스턴스, Redis 불필요)
- **대시보드 집계**: Hybrid (실시간 쿼리 + ProfitReport 캐시)
- **정산 데이터**: 엑셀 업로드 (Sprint 3) → API 연동 (Sprint 4)
- **광고비 연동**: ad_data.db에서 option_id로 cross-DB Python 레벨 조인
- **스케줄러 기본 작업**: 매일 06:00 KST 전체 동기화, 06:30 이익 재계산
- **API 응답 Decimal→string**: 프론트에서 parseNumbers로 변환
- **순이익 공식**: 매출 - 원가 - 수수료 - 광고비 - 배송비 - VAT(10/110)
- **로켓배송(위탁)**: 매출만 표시, 이익 계산 제외

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/profit_calculator.py` | 순이익 계산 엔진 (핵심 비즈니스 로직) |
| `backend/app/routers/dashboard.py` | 대시보드 API 4개 |
| `backend/app/routers/settlements.py` | 정산 CRUD + 엑셀 업로드 |
| `backend/app/services/scheduler_service.py` | APScheduler 자동 동기화 |
| `backend/app/routers/scheduler.py` | 스케줄러 상태/트리거 API |
| `backend/app/models.py` | 전체 DB 모델 (12개 테이블) |
| `backend/app/schemas.py` | 전체 Pydantic 스키마 |
| `backend/app/main.py` | FastAPI 엔트리포인트 + lifespan |
| `frontend/src/pages/Dashboard.tsx` | 대시보드 UI (Recharts) |
| `frontend/src/pages/Settlements.tsx` | 정산 관리 UI |
| `frontend/src/components/SchedulerStatus.tsx` | 자동 동기화 표시기 |
| `frontend/src/lib/api.ts` | API 클라이언트 + 타입 |
| `claude-progress.txt` | 세션 간 인계 (현재 75%) |

## 5. 알려진 이슈 / 주의사항
- **Python 3.14 호환**: SQLAlchemy 2.0.48 필수, `from __future__ import annotations` 필수
- **API Key 미입력**: .env의 모든 API 키가 빈 상태. 실제 키 입력 후 동기화 테스트 필요
- **쿠팡 API Key**: 6개월(180일)마다 갱신 필요
- **네이버 API**: IP 사전등록 필수
- **cafe24 API**: Access Token 2시간 만료, OAuth 인증 플로우 별도 설정 필요
- **ad_data.db**: 305MB, iCloud 경로, `?mode=ro`로 읽기 전용
- **Dashboard parseNumbers**: API가 Decimal을 string으로 반환 → 프론트에서 Number() 변환
- **스케줄러**: 서버 시작 시 자동 활성화 (lifespan). 개발 중에도 06:00/06:30에 작업 실행됨
- **browse 도구**: 백엔드 CWD 기준으로 경로 해석됨 (스크린샷 경로 주의)

## 6. 다음에 할 작업 (미완료)
- [ ] .env에 실제 쿠팡 API 키 입력 후 동기화 테스트
- [ ] 네이버/cafe24 OAuth 인증 설정 (IP 등록, 앱 등록)
- [ ] Sprint 4 계획 (/autoplan)
- [ ] Sprint 4 후보: 쿠팡 revenue-history → 정산 자동 연동
- [ ] Sprint 4 후보: 알림 시스템 (이상 매출, 동기화 실패)
- [ ] Sprint 4 후보: 재고 관리 페이지 구현
- [ ] Sprint 4 후보: 엑셀 리포트 다운로드
- [ ] Sprint 4 후보: 사용자 인증 (로그인/세션)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/memory/HANDOFF_ohisell-sprint3_20260402.md 읽고 이어서 작업해줘
