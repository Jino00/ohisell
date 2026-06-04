# 세션 인수인계: ohisell Sprint 2 API 연동
> 저장일시: 2026-04-01 23:45
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 실행 명령어: `bash scripts/init.sh` (frontend + backend 동시 시작)
- Backend: `http://localhost:8000` (FastAPI, Swagger: `/docs`)
- Frontend: `http://localhost:5173` (React + Vite)
- Python: 3.14, venv: `backend/.venv/`
- 주요 환경변수: `DATABASE_URL`, `COUPANG_WING1_*`, `COUPANG_WING2_*`, `COUPANG_RG1_*`, `COUPANG_RG2_*`, `NAVER_*`, `CAFE24_*`, `AD_DATA_DB_PATH` (backend/.env)
- ohi-ad-intelligence 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/ohi-ad-intelligence/`

## 2. 이번 세션 완료 목록

### autoplan 리뷰 (CEO + Design + Eng)
- ✅ Sprint 2 계획서 작성 + 3단계 리뷰 파이프라인 실행
- ✅ 순이익 공식 수정: 배송비 추가 (shipping_cost nullable — 상품별로 포함/미포함 다름)
- ✅ 네이버/cafe24를 Sprint 2에 포함 (분리하지 않음)
- ✅ Design review: Orders 페이지 상세 UX 스펙 (empty states, sync 동작, 컬럼 순서)
- ✅ Eng review: OAuthToken 모델, 3컬럼 unique, 테스트 계획

### Backend 구현 (15개 파일)
- ✅ `backend/app/config.py` — 쿠팡/네이버/cafe24 멀티 계정 Config dataclass + 팩토리
- ✅ `backend/app/clients/base.py` — BaseChannelClient 추상 인터페이스 + RawOrder dataclass
- ✅ `backend/app/clients/coupang.py` — HMAC-SHA256, 멀티 계정, 3회 재시도, TZ 사이드이펙트 제거
- ✅ `backend/app/clients/naver.py` — OAuth2 + bcrypt 전자서명, 토큰 자동 갱신
- ✅ `backend/app/clients/cafe24.py` — OAuth2 code grant, refresh token 갱신
- ✅ `backend/app/models.py` — Order 업데이트 (product_master FK nullable, 3컬럼 unique, shipping_cost nullable), SyncLog, OAuthToken 추가
- ✅ `backend/app/database.py` — ad_data.db read-only engine (sqlite ?mode=ro)
- ✅ `backend/app/services/sync_service.py` — 동기화 오케스트레이션, concurrency guard, auto-linking, raw_data 10KB 제한
- ✅ `backend/app/services/ad_cost_reader.py` — 일별/상품별 광고비 조회
- ✅ `backend/app/routers/orders.py` — 주문 조회 (필터+페이지네이션) + 순이익 요약 API
- ✅ `backend/app/routers/sync.py` — 단일/전체 채널 동기화 + 상태 조회
- ✅ `backend/app/routers/ad_costs.py` — 일별/상품별 광고비 조회
- ✅ `backend/app/main.py` — 신규 라우터 등록 (orders, sync, ad_costs)
- ✅ `backend/app/schemas.py` — OrderOut, SyncResult, SyncStatusOut, ProfitSummary 등 추가
- ✅ `backend/requirements.txt` — requests, bcrypt 추가

### Frontend 구현 (2개 파일)
- ✅ `frontend/src/pages/Orders.tsx` — 전체 구현 (순이익 카드 3개, Sync 드롭다운+채널별 last synced, 필터바, 테이블, skeleton 로딩, empty states 3종, 페이지네이션)
- ✅ `frontend/src/lib/api.ts` — OrderItem, OrderListResponse, SyncResult, SyncStatus, ProfitSummary 타입 추가

### 기타
- ✅ `CLAUDE.md` — gstack 스킬 라우팅 규칙 추가
- ✅ `backend/.env` — 전체 채널 API 키 플레이스홀더 추가
- ✅ DB migration — orders 재생성, sync_log + oauth_tokens 신규 생성
- ✅ ad_data.db 검증: 405개 option_id, 240K rows, 2025-07-24~2026-03-21

## 3. 확정된 결정사항
- **순이익 공식**: 매출 - 원가 - 수수료 - 광고비 - 배송비 - VAT(10/110)
- **배송비**: 상품별로 포함/미포함 다름 → shipping_cost nullable
- **7개 채널 전체 Sprint 2에서 연동** (분리하지 않음)
- **쿠팡 데이터 소스**: ordersheets = 주문 수집, revenue-history = 정산용 (Sprint 3)
- **로켓배송(위탁)**: Sprint 2에서 이익 계산 제외 (매출만 표시, 위탁 구조 다름)
- **Unique constraint**: (channel_id, order_number, platform_product_id) 3컬럼
- **Timezone**: 전체 시스템 KST 전제
- **수동 동기화**: Sprint 2는 Sync Now 버튼, 자동 스케줄러는 Sprint 3

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/config.py` | 멀티 플랫폼 API 설정 관리 |
| `backend/app/clients/coupang.py` | 쿠팡 HMAC API 클라이언트 |
| `backend/app/clients/naver.py` | 네이버 OAuth2+bcrypt 클라이언트 |
| `backend/app/clients/cafe24.py` | cafe24 OAuth2 클라이언트 |
| `backend/app/models.py` | 전체 DB 모델 (12개 테이블) |
| `backend/app/services/sync_service.py` | 동기화 오케스트레이션 |
| `backend/app/services/ad_cost_reader.py` | ad_data.db 광고비 조회 |
| `backend/app/routers/orders.py` | 주문 조회 + 순이익 요약 API |
| `backend/app/routers/sync.py` | 동기화 트리거 API |
| `backend/app/routers/ad_costs.py` | 광고비 조회 API |
| `frontend/src/pages/Orders.tsx` | 주문 관리 페이지 (전체 구현) |
| `frontend/src/lib/api.ts` | API 클라이언트 + 타입 |
| `claude-progress.txt` | 세션 간 인계 파일 |
| `.claude/plans/logical-sniffing-pinwheel.md` | Sprint 2 계획서 (autoplan 리뷰 완료) |

## 5. 알려진 이슈 / 주의사항
- **Python 3.14 호환**: SQLAlchemy 2.0.48 필수, `from __future__ import annotations` 필수
- **API Key 미입력**: .env의 모든 API 키가 빈 상태. 실제 키 입력 후 동기화 테스트 필요
- **쿠팡 API Key**: 6개월(180일)마다 갱신 필요
- **네이버 API**: IP 사전등록 필수, bcrypt 서명 구현은 완료
- **cafe24 API**: Access Token 2시간 만료, Refresh Token 2주. OAuth 인증 플로우는 별도 설정 필요
- **ad_data.db**: 305MB, iCloud 경로. `?mode=ro`로 읽기 전용 열기
- **Product.orders 관계 제거**: 레거시 Product 모델에서 orders 관계 제거됨
- **autoplan 계획서**: `.claude/plans/logical-sniffing-pinwheel.md`에 전체 리뷰 결과 포함

## 6. 다음에 할 작업 (미완료)
- [ ] .env에 실제 쿠팡 API 키 입력 후 동기화 테스트
- [ ] 네이버/cafe24 OAuth 인증 설정 (IP 등록, 앱 등록)
- [ ] /qa 실행 (브라우저 실동작 테스트)
- [ ] Sprint 3: 대시보드 (일별/주별/월별 매출 추이 차트)
- [ ] Sprint 3: 상품별/채널별 이익률 자동 계산
- [ ] Sprint 3: 자동 동기화 스케줄러 (cron)
- [ ] Sprint 3: 정산 관리 페이지

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/memory/HANDOFF_ohisell-sprint2_20260401.md 읽고 이어서 작업해줘
