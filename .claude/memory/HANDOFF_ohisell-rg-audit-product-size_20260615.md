# 세션 인수인계: ohisell RG 감사 — 쿠팡 실측 사이즈 수집
> 저장일시: 2026-06-15
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 직전 HANDOFF: `HANDOFF_ohisell-wing-session-automation-S5done_20260615.md`

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload` (8000)
- 테스트: `python -m pytest -q` (**191 그린**)
- prod: `sellc.ohitech.co.kr` (ssh Host: sellc.ohitech.co.kr, User=ubuntu). PM2 `ohisell-backend`(online). DB=SQLite. alembic head=**n8o9p0q1r2s3**.
- CDP Chrome: `backend/.venv/bin/python3 tools/wing_browser_fetcher.py chrome` → 전용 프로필(`~/.ohisell_wing_chrome`) Chrome 실행(port 9222)
- 데몬: launchd `com.ohisell.wing`(running). 로그=`~/.ohisell_wing_fetcher.log`
- 페처 설정: `~/.ohisell_wing_fetcher.json` — `rg_report_types: ["WAREHOUSING_SHIPPING", "PRODUCT_SIZE_COMPARISON"]`

## 2. 이번 세션 완료 목록

### S8 RG 수수료 감사 — size_mismatch_high 4건 조사 완료

#### 감사 결과
- `PRODUCT_SIZE_COMPARISON` XLSX(Wing 보고서)를 다운로드해 쿠팡 실측 사이즈 확인
- **결론**: 아이폰 필름 3건은 쿠팡도 "극소형" 측정 → false positive
- 아이패드 미니 필름(91313543029)은 최근 정산주기 미입고 → 보고서에 없음

#### 구현 (커밋 2bdd148)
- `backend/app/models.py`: `CoupangProductSize` 모델 추가
- `backend/alembic/versions/n8o9p0q1r2s3_add_coupang_product_size.py`: 마이그레이션
- `backend/app/services/coupang/rg_product_size_sync.py`: **신규 SA** — XLSX 파서 + DB ingest + `get_coupang_size_type()`
- `backend/app/services/coupang/rg_fee_anomaly.py`:
  - `coupang_size_type` optional 파라미터 추가
  - `size_source` 필드 반환 ("coupang_measured" / "registered_dims")
  - 쿠팡 실측값 있으면 `size_mismatch_high` 스킵 (실측값=과금 기준)
- `backend/app/services/coupang/rg_fee_audit.py`:
  - `_load_coupang_sizes()` 배치 조회 추가
  - `build_fee_audit`에서 쿠팡 사이즈 주입
- `backend/app/routers/coupang_ops.py`:
  - `POST /api/coupang/ops/rg/product-size/upload-xlsx` 엔드포인트 추가
  - `GET /api/coupang/ops/rg/product-size` 엔드포인트 추가
- `tools/wing_browser_fetcher.py`:
  - `RG_PRODUCT_SIZE_UPLOAD_PATH` 상수 추가
  - `_rg_push_xlsx`: PRODUCT_SIZE_COMPARISON → 전용 엔드포인트 분기
  - `~/.ohisell_wing_fetcher.json` rg_report_types에 PRODUCT_SIZE_COMPARISON 추가

#### prod self-verify (원칙 22)
- prod에 alembic upgrade + PM2 restart 완료
- 48개 상품 실측 사이즈 ingest 완료
- fee-audit 결과:
  - 아이폰 필름 3건: `flags=[]` (해제) ✅
  - 아이패드 미니(미입고): `flags=['size_mismatch_high']` (다음 입고 후 자동 해제)

## 3. 확정된 결정사항
- **쿠팡 실측 사이즈가 배송비 과금 기준**: `PRODUCT_SIZE_COMPARISON` 보고서 → DB → anomaly 판단 최우선
- **실측값 있으면 size_mismatch_high 추정 불필요**: 쿠팡이 직접 분류한 값이 있으면 배송비 기반 추정 스킵
- **자동 수집**: 매 정산주기 RG 다운로드 시 WAREHOUSING_SHIPPING + PRODUCT_SIZE_COMPARISON 동시 다운로드

## 4. 핵심 파일 목록

| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/rg_product_size_sync.py` | ★신규 SA: XLSX 파서 + ingest + 조회 |
| `backend/app/services/coupang/rg_fee_anomaly.py` | 이상치 탐지 순수함수 (coupang_size_type 주입 지원) |
| `backend/app/services/coupang/rg_fee_audit.py` | Harness: 배치 로드 + SA 조합 |
| `backend/app/routers/coupang_ops.py` | `/rg/product-size/*` 엔드포인트 |
| `tools/wing_browser_fetcher.py` | PRODUCT_SIZE_COMPARISON 자동 push 포함 |
| `~/.ohisell_wing_fetcher.json` | rg_report_types 설정 |

## 5. 알려진 이슈 / 주의사항

- **아이패드 미니 필름(91313543029)**: 최근 입고 이력 없어 실측 사이즈 없음. 입고 후 다음 정산주기에 자동 해제됨.
- **Mac 재부팅 시**: ① chrome 서브커맨드 → ② 쿠팡 로그인 → ③ login → ④ 데몬 자동 재시작
- **Wing Chrome CDP**: port 9222, 프로필 `~/.ohisell_wing_chrome`
- **prod alembic head**: n8o9p0q1r2s3 (coupang_product_size 테이블 생성 완료)
- **로컬 .env에 AD_INGEST_TOKEN 없음**: 로컬에서 product-size upload-xlsx는 직접 DB 세션으로 ingest해야 함

## 6. 다음에 할 작업 (미완료)

- [ ] RG발송관제 S7 UI (RG 재고관리 탭 UI 개선)
- [ ] 나머지 8종 XLSX 파서 구현 (CATEGORY_TR, STORAGE_FEE 등)
- [ ] track_wing-session-automation.md → completed/ 이동
- [ ] git push (로컬 커밋 origin push)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ohisell-rg-audit-product-size_20260615.md 읽고 이어서 작업해줘
