# 세션 인수인계: ohisell 프로젝트 초기 세팅 + 상품 원가표
> 저장일시: 2026-04-01 22:37
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 실행 명령어: `bash scripts/init.sh` (frontend + backend 동시 시작)
- Backend: `http://localhost:8000` (FastAPI, Swagger: `/docs`)
- Frontend: `http://localhost:5173` (React + Vite)
- Python: 3.14, venv: `backend/.venv/`
- 주요 환경변수: `DATABASE_URL` (backend/.env)
- ohi-ad-intelligence 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/ohi-ad-intelligence/`

## 2. 이번 세션 완료 목록

### Sprint 0 — 개발 환경 세팅
- ✅ `_template/` → `Ohiselling/` 복사 + 플레이스홀더 치환 (ohisell, 2026-04-01)
- ✅ `CLAUDE.md`, `FEATURES.json`, `claude-progress.txt`, `docs/` 초기화
- ✅ `backend/` FastAPI + SQLAlchemy 2.0.48 + Alembic
- ✅ `frontend/` React + Vite + TypeScript + Tailwind CSS v4 + React Router
- ✅ `scripts/init.sh` 개발 서버 시작 스크립트
- ✅ `.gitignore` 설정
- ✅ git init + 초기 커밋

### Sprint 1 — 상품 원가표 + 채널 매핑
- ✅ `backend/app/models.py` — 9개 테이블 (Channel 확장, ProductMaster, ProductChannelMapping, AdCost, ProfitReport 신규)
- ✅ `backend/app/seed.py` — 7개 채널 시드 (Wing x2, 로켓그로스 x2, 로켓배송, 네이버, cafe24)
- ✅ `backend/app/schemas.py` — Pydantic 스키마 (신규)
- ✅ `backend/app/routers/products.py` — 상품 CRUD + 매핑 + 엑셀 업로드/다운로드 API (신규)
- ✅ `backend/app/routers/channels.py` — 채널 목록 API (신규)
- ✅ `backend/app/main.py` — 라우터 등록
- ✅ `frontend/src/pages/Products.tsx` — 상품 원가표 관리 UI (테이블 + 확장형 매핑)
- ✅ `frontend/src/components/ProductForm.tsx` — 상품 등록/수정 모달 (신규)
- ✅ `frontend/src/components/MappingForm.tsx` — 채널 매핑 추가 모달 (신규)
- ✅ `frontend/src/lib/api.ts` — API 클라이언트 + 타입 정의

## 3. 확정된 결정사항
- **채널 구조**: 쿠팡 Wing 3P x2, 로켓그로스 2P x2, 로켓배송 1P x1, 네이버 1, cafe24 1 = **총 7개 계정**
- **데이터 소스**: 쿠팡 4계정(API HMAC), 네이버(API OAuth2+bcrypt), cafe24(API OAuth2), 로켓배송(엑셀/ohi-ad-intelligence DB)
- **이익률 공식**: 순이익 = 총매출 - 원가 - 채널수수료 - 광고비 - 부가세(10/110)
- **상품 매핑**: product_master(통합SKU) → product_channel_mapping(채널별 식별자) 구조
- **쿠팡 API**: ohi-ad-intelligence의 `coupang_api_client.py` 재사용 (HMAC 인증 검증 완료)
- **광고비**: ohi-ad-intelligence의 ad_data.db 읽기 전용 연동

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/models.py` | 전체 DB 모델 (9개 테이블) |
| `backend/app/schemas.py` | Pydantic 요청/응답 스키마 |
| `backend/app/routers/products.py` | 상품 CRUD + 매핑 + 엑셀 API |
| `backend/app/routers/channels.py` | 채널 목록 API |
| `backend/app/seed.py` | 7개 채널 시드 데이터 |
| `backend/app/main.py` | FastAPI 앱 + 라우터 등록 |
| `frontend/src/pages/Products.tsx` | 상품 원가표 관리 UI |
| `frontend/src/components/ProductForm.tsx` | 상품 등록/수정 폼 |
| `frontend/src/components/MappingForm.tsx` | 채널 매핑 폼 |
| `frontend/src/lib/api.ts` | API 클라이언트 + 타입 |
| `claude-progress.txt` | 세션 간 인계 파일 |
| `.claude/plans/bright-dazzling-fern.md` | Sprint 1~3 종합 계획서 |

## 5. 알려진 이슈 / 주의사항
- **Python 3.14 호환**: SQLAlchemy 2.0.48 필수, models.py에 `from __future__ import annotations` 필수
- **API Key 보안**: 모든 API Key는 backend/.env에 저장, .gitignore에 포함됨
- **쿠팡 API Key**: 6개월(180일)마다 갱신 필요
- **네이버 API**: IP 사전등록 필수, Access Token 별도 구현 필요 (bcrypt 서명)
- **cafe24 API**: Access Token 2시간 만료, Refresh Token 2주
- **ohi-ad-intelligence**: ad_data.db가 319GB — 읽기 전용으로만 연동할 것

## 6. 다음에 할 작업 (Sprint 2: API 연동)
- [ ] 쿠팡 API 연동 — coupang_api_client.py 복사 + 4계정 주문/매출 수집
- [ ] 네이버 커머스 API — OAuth 2.0 + bcrypt 인증 구현 + 주문/정산 수집
- [ ] cafe24 API — OAuth 2.0 인증 구현 + 주문 수집
- [ ] 로켓배송 — 엑셀 업로드 or ohi-ad-intelligence DB 광고비 연동
- [ ] ad_costs 테이블에 광고비 데이터 통합

Sprint 3 (대시보드 + 이익률 분석):
- [ ] 일별/주별/월별 매출 추이 차트
- [ ] 상품별/채널별 이익률 계산 및 표시
- [ ] 정산 관리

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/memory/HANDOFF_ohisell-init_20260401.md 읽고 이어서 작업해줘
