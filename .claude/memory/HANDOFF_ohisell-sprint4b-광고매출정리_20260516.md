# 세션 인수인계: ohisell Sprint 4B — 광고/매출 데이터 업로드 정리
> 저장일시: 2026-05-16 (날짜 변동: 세션 중 4/2→5/16)
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 로컬 실행:
  - Backend: `cd backend && source .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8000` (PID 변동, /tmp/ohisell_backend.log)
  - Frontend: `cd frontend && npx vite --port 5173` (포트 5173 고정 — CORS가 5173만 허용)
- 로컬 URL: Frontend http://localhost:5173, Backend http://localhost:8000 (Swagger /docs)
- 프로덕션: https://sellc.ohitech.co.kr (Oracle Cloud 168.107.19.222)
- 서버 SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@168.107.19.222`
- 서버 PM2: `ohisell-backend` (port 8001, --proxy-headers), nginx → localhost:8001
- GitHub: https://github.com/Jino00/ohisell (private), main 브랜치
- DB: SQLite `backend/ohisell.db` (로컬=서버 동기화됨, 894 상품/2610 매핑/1595 주문)
- 주요 환경변수(.env, gitignore됨): `COUPANG_WING1/2_*`, `COUPANG_RG1/2_*`, `NAVER_CLIENT_ID/SECRET`, `CAFE24_*`, `DATABASE_URL`
  - ⚠️ `NAVER_CLIENT_SECRET`은 작은따옴표로 감싸야 함 (`'$2a$04$...'`) — `$` 변수치환 방지

## 2. 이번 세션 완료 목록
- ✅ `backend/app/services/profit_calculator.py` — 광고비 채널간 bleeding 버그 수정 (비례배분→option_id 직접매핑), product_profit 중복할당 수정
- ✅ `backend/app/clients/coupang.py` — orderItems 배열 내부 파싱 (vendorItemId/orderPrice), shipmentType 필터링은 sync_service에서
- ✅ `backend/app/services/sync_service.py` — `_is_coupang_order_for_channel()` Wing(THIRD_PARTY)/RG 구분
- ✅ `backend/app/clients/naver.py` — bcrypt 서명에 base64 인코딩 추가(인증 버그 해결), last-changed-statuses 하루단위 분할(24h제한), product-orders/query 상세조회 추가, `_request_post` 추가, 중복방지
- ✅ `backend/app/clients/cafe24.py` — `embed=items` 추가, variant_code를 platform_product_id로 사용, 상품명+옵션명 결합
- ✅ `backend/app/routers/channels.py` — `GET /api/channels/connection-status` 추가
- ✅ `backend/app/routers/products.py` — `POST /api/products/upload-by-name` (상품명 기반 통합 1시트 업로드), `GET /api/products/mapping-template` 통합 1시트 구조로 변경
- ✅ `frontend/src/pages/Settings.tsx` — 신규 (채널 연동상태 + OAuth버튼 + 동기화)
- ✅ `frontend/src/App.tsx`, `Layout.tsx` — /settings 라우트+메뉴 추가
- ✅ `frontend/src/lib/api.ts` — `API_BASE = import.meta.env.DEV ? "http://localhost:8000" : ""`
- ✅ `frontend/src/pages/Products.tsx` — "매핑 템플릿"/"원가 매핑 업로드" 버튼 추가
- ✅ `frontend/src/pages/Orders.tsx` — 총비용 NaN 버그 수정 (`Number()` 변환 후 합산)
- ✅ 실데이터 동기화 완료: 네이버 다수 + cafe24 81건 + 쿠팡 일부
- ✅ 원가 매핑 업로드 완료: 894 상품 / 2610 채널매핑 / 주문 1531건 자동링크 (96%)
- ✅ 네이버 검색광고 API 인증 검증 성공 (Customer ID 1313769, 캠페인 42개 조회됨)
- ✅ 쿠팡 광고 엑셀 분석 완료 (`~/Downloads/2620260513_A01029796_custom_report.xlsx`)

## 3. 확정된 결정사항
- **계정별 운영 분리**: 오픽스(A01564720)=Wing1+RG1 (API), 오하이테크(A01029796)=로켓배송만 (광고 엑셀)
- **Wing2/RG2 채널은 미사용** (오하이테크는 로켓배송 전용)
- 같은 상품 원가는 전 채널 동일
- 원가 매핑 엑셀 구조: 가로형(상품1행+채널별ID 가로나열) → 변환스크립트로 세로형 변환 후 `upload-by-name` 업로드
- cafe24 상품ID = `variant_code` (상품+옵션 고유), 상품명에 `[옵션값]` 결합
- 네이버 검색광고 자격증명 (검색광고만 가능, GFA는 파트너 전용):
  - Access License: `01000000000a10c43ccbd0878147f8682922e0ae6561daddc995f9288cbcb9b54c99715ecf`
  - Secret Key: `AQAAAAAKEMQ8y9CHgUf4aCki4K5lBNGlOV5T8CvPFHP/WzHknA==`
  - Customer ID: `1313769`
  - API: HMAC-SHA256 서명(`{ts}.{method}.{uri}` → base64), 헤더 X-API-KEY/X-Customer/X-Signature/X-Timestamp, BASE https://api.searchad.naver.com
- 네이버 검색광고 연동은 **보류** (먼저 매출/광고 업로드 기능 마무리 후)

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/clients/coupang.py` | 쿠팡 HMAC API (orderItems 파싱) |
| `backend/app/clients/naver.py` | 네이버 OAuth+bcrypt+base64, 2단계 주문조회 |
| `backend/app/clients/cafe24.py` | cafe24 OAuth, variant_code |
| `backend/app/services/sync_service.py` | 동기화 오케스트레이션, Wing/RG 필터 |
| `backend/app/services/profit_calculator.py` | 순이익 계산 (광고비 option_id 매칭) |
| `backend/app/services/ad_cost_reader.py` | ad_data.db 조회 (외부 의존, 곧 교체 예정) |
| `backend/app/routers/products.py` | 상품 CRUD + upload-by-name + 템플릿 |
| `frontend/src/pages/Settings.tsx` | 채널 연동상태 UI |
| `frontend/src/pages/Products.tsx` | 원가 매핑 업로드 |

## 5. 알려진 이슈 / 주의사항
- **로컬 frontend는 반드시 포트 5173** (백엔드 CORS가 localhost:5173만 허용 — main.py `allow_origins`)
- 다른 프로젝트(coupang, Meta)가 5173 선점하는 경우 있음 → 해당 vite kill 후 ohisell을 5173에 띄울 것
- `ad_data.db`: 로컬 없음, 서버 0바이트. **외부 ohi-ad-intelligence 의존 → 폐기 방향**. 쿠팡 광고는 엑셀 업로드로 전환 예정
- 쿠팡 로켓배송: 매출 API 없음. 광고관리 화면이 유일 매출원
- 미매핑 주문 약 64건 (네이버 구형기종 35 + Wing 청바지 2(무시) + 기타). `~/Desktop/ohisell_미매핑_추가입력.xlsx` 생성됨
- 광고 엑셀(`2620260513_A01029796_custom_report.xlsx`): 70컬럼, 11686행, 13일치(4/30~5/12). 핵심 컬럼: [0]날짜 [3]캠페인 [17]광고집행옵션ID [24]광고비 [49]총전환매출14일. 14일 광고비 합계 8,415,241원, 옵션ID 275개중 223개(81%) 매핑테이블 존재
- profit_calculator는 아직 ad_data.db(`get_ad_db`) 의존 — 광고 엑셀 업로드 테이블로 전환 필요
- 프로덕션 API 인증 없음 (보안 취약, Sprint 4B 예정)

## 6. 다음에 할 작업 (미완료)

### 🔴 즉시 결정 필요 — 로켓배송 매출 처리 방법 (사용자 답변 대기 중)
광고 엑셀에는 "광고 전환 매출"만 있고 "전체매출"(비광고 포함)은 없음. 광고관리 화면 캡처상 전체매출≈광고전환매출의 2배.
- **A.** 광고 전환 매출만 사용 (자동, 비광고매출 ~50% 누락)
- **B.** 광고관리 화면 "전체매출"을 일별 수동입력 (정확)
- **C.** 광고관리 화면에서 전체매출 엑셀 별도 다운로드 가능한지 확인
→ **이 결정 나와야 광고 엑셀 업로드 기능 설계 확정 가능**

### 광고 엑셀 업로드 기능 (설계 제안 완료, 승인 대기)
제안 구조:
```
[Agent] 쿠팡 광고비 관리
  ├─[Harness] 광고비 엑셀 업로드
  │  ├─[SA] CoupangAdExcelParser (70컬럼 파싱)
  │  ├─[SA] VendorIdResolver (파일명 vendor_id→채널)
  │  └─[SA] AdSpendStorage (ad_spend_daily upsert)
  └─[Harness] 광고비 조회 (profit_calculator가 ad_data.db→이 테이블로 전환)
```
신규 테이블 `ad_spend_daily(date, channel_id, platform_product_id, campaign_name, ad_spend, impressions, clicks, conv_revenue_14d, raw_data, UNIQUE(date,channel_id,platform_product_id,campaign_name))`
- DB 스키마 변경 + 새 Agent/Harness → **Opus 전환 권장 작업**

### Sprint 4B 잔여
- [ ] 네이버 검색광고 API 연동 (자격증명 확보됨, 보류 중)
- [ ] nginx IP 화이트리스트 또는 Basic Auth (보안)
- [ ] OAuth state CSRF 토큰화
- [ ] 미매핑 64건 처리 (네이버 구형기종 엑셀 추가입력)
- [ ] 서버 DB와 로컬 DB 동기화 정책 정리

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/memory/HANDOFF_ohisell-sprint4b-광고매출정리_20260516.md 읽고 이어서 작업해줘
