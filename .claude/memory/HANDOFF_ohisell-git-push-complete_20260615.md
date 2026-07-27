# 세션 인수인계: ohisell git push 완료
> 저장일시: 2026-06-15
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 직전 HANDOFF: `HANDOFF_ohisell-rg-audit-product-size_20260615.md`

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload` (8000)
- 테스트: `python -m pytest -q` (**191 그린**)
- prod: `sellc.ohitech.co.kr` (ssh Host: sellc.ohitech.co.kr, User=ubuntu). PM2 `ohisell-backend`(online). DB=SQLite. alembic head=**n8o9p0q1r2s3**.
- CDP Chrome: `backend/.venv/bin/python3 tools/wing_browser_fetcher.py chrome` → 전용 프로필(`~/.ohisell_wing_chrome`) Chrome 실행(port 9222)
- 데몬: launchd `com.ohisell.wing`(running). 로그=`~/.ohisell_wing_fetcher.log`

## 2. 이번 세션 완료 목록
- ✅ `git push origin main` — 커밋 `2bdd148`(쿠팡 실측 사이즈 수집) origin에 push 완료
- ✅ `.claude/memory/MEMORY.md` — HANDOFF_ohisell-rg-audit-product-size_20260615 인덱스 추가

## 3. 확정된 결정사항
- **쿠팡 실측 사이즈(PRODUCT_SIZE_COMPARISON)가 배송비 과금 기준** → DB 저장 → anomaly 판단 최우선
- **실측값 있으면 size_mismatch_high 스킵** — 쿠팡이 분류한 값 있으면 배송비 추정 불필요
- **자동 수집**: RG 다운로드 시 WAREHOUSING_SHIPPING + PRODUCT_SIZE_COMPARISON 동시 다운로드
- **Wing 세션 자동화 트랙 6/6 완료 — 종료**

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/rg_product_size_sync.py` | 신규 SA: XLSX 파서 + ingest + 조회 |
| `backend/app/services/coupang/rg_fee_anomaly.py` | 이상치 탐지 (coupang_size_type 주입 지원) |
| `backend/app/services/coupang/rg_fee_audit.py` | Harness: 배치 로드 + SA 조합 |
| `tools/wing_browser_fetcher.py` | PRODUCT_SIZE_COMPARISON 자동 push 포함 |
| `~/.ohisell_wing_fetcher.json` | rg_report_types: WAREHOUSING_SHIPPING + PRODUCT_SIZE_COMPARISON |

## 5. 알려진 이슈 / 주의사항
- **아이패드 미니 필름(91313543029)**: 최근 입고 없어 실측 사이즈 미확보. 입고 후 다음 정산주기에 size_mismatch_high 자동 해제.
- **Wing Chrome CDP**: port 9222, 프로필 `~/.ohisell_wing_chrome`. Mac 재부팅 시 ① chrome 서브커맨드 → ② 쿠팡 로그인 → ③ login → ④ 데몬 자동 재시작.
- **prod alembic head**: n8o9p0q1r2s3 (coupang_product_size 테이블 생성 완료)
- **로컬 .env에 AD_INGEST_TOKEN 없음**: 로컬에서 product-size upload-xlsx는 직접 DB 세션으로 ingest해야 함

## 6. 다음에 할 작업 (미완료)
- [ ] RG발송관제 S7 UI (RG 재고관리 탭 UI 개선)
- [ ] 나머지 8종 XLSX 파서 구현 (CATEGORY_TR, STORAGE_FEE, INVENTORY_COMPENSATION, BARCODE_LABELING_FEE, CRETURN_PICKUP_RESTOCKING, VRETURN_HANDLING, VRETURN_SHIPPING 등)
- [ ] track_wing-session-automation.md → completed/ 이동

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ohisell-git-push-complete_20260615.md 읽고 이어서 작업해줘
