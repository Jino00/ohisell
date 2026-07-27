# 세션 인수인계: ohisell-rg-fee-accounting-S4
> 저장일시: 2026-06-09 (S4 완료)
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`
- 테스트 실행: `cd backend && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_rg_settlement_sync.py -v`
- 주요 환경변수: `DATABASE_URL`, `SECRET_KEY`

## 2. 이번 세션 완료 목록
- ✅ **S4: 대조(reconciliation) 뷰 노출 (D-6/D-7)** — 커밋 `e7cb99f`
  - `backend/app/services/coupang/intelligence.py`: `_agg_rg_settlement_fees()` 함수 추가 + `compute_command_center()` 반환값에 `rg_settlement` 독립 섹션 추가 (net_profit 불변, Phase 1)
  - `backend/app/services/scheduler_service.py`: `sync_coupang_rg_settlement_job` 함수 추가 + 기본 cron `05:30 KST` 등록
  - `frontend/src/lib/api.ts`: `RgSettlementByAccount` 인터페이스 + `OverviewResponse.rg_settlement?` 타입 추가
  - `frontend/src/pages/CommandCenter.tsx`: `RgSettlementCard` 컴포넌트 추가 (주황색 대조 카드, 데이터 없으면 amber 경고)
- ✅ **codex review P2×2 수정** (동일 커밋)
  - `backend/app/clients/coupang/rg_settlement.py`: 미커밋(untracked) → git add로 포함
  - `backend/app/services/coupang/rg_settlement_sync.py:180`: `xsrf_token` 복호화 누락 수정 — `row.xsrf_token or ""` → `decrypt_secret(row.xsrf_token) if row.xsrf_token else ""` (inbound_sync.py:233 패턴 일치)
  - codex P2-3(NaverOps 기간필터) = 이번 diff 외 기존 이슈 → 기각
- ✅ **트랙 파일 4/7 갱신** — `docs/tracks/active/track_coupang-rg-fee-accounting.md` S4 체크
- ✅ **claude-progress.txt 갱신** — 커밋 `633c7e2`
- ✅ **테스트 14/14 PASS** (rg_settlement_sync 기존 fixture 테스트 불변)
- ✅ **프론트엔드 빌드 성공** — `dist/assets/index-D79z1Lve.js`

## 3. 확정된 결정사항
- **D-6 (reconciliation-first)**: Phase 1 = net_profit 불변, 'RG 정산 비용(미반영)' 독립 대조 지표만 표시
- **D-7**: compute_command_center에 account_key별 독립 섹션/카드로 표시
- **xsrf_token**: Fernet 암호화 저장 → `decrypt_secret()` 필수 (inbound_sync.py:176 저장, :233 로드 패턴)
- **Alembic down_revision**: `b2d4f6082ace` (현재 헤드, 기존 `f2a4c6e8b0d1`는 틀림)
- **S0 실측 확정**: `_kst_date_to_utc_iso("YYYY-MM-DD") → "YYYY-MM-DDT15:00:00.000Z"`
- **scheduler cron**: `sync_coupang_rg_settlement` = `30 5 * * *` (05:30 KST)

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/clients/coupang/rg_settlement.py` | S1: Wing 정산 API 클라이언트 SA |
| `backend/app/models.py` | S2: CoupangRgSettlementFee 모델 (grain=account_key×date_from×date_to×fee_type) |
| `backend/alembic/versions/g1h2i3j4k5l6_add_coupang_rg_settlement_fee.py` | S2: DB 마이그레이션 (down_revision=b2d4f6082ace) |
| `backend/app/services/coupang/rg_settlement_sync.py` | S3: Harness (수집·파싱·upsert) + xsrf decrypt 수정 |
| `backend/app/services/coupang/intelligence.py` | S4: _agg_rg_settlement_fees() + compute_command_center rg_settlement 섹션 |
| `backend/app/services/scheduler_service.py` | S4: sync_coupang_rg_settlement_job + 05:30 KST 등록 |
| `frontend/src/lib/api.ts` | S4: RgSettlementByAccount + OverviewResponse.rg_settlement? 타입 |
| `frontend/src/pages/CommandCenter.tsx` | S4: RgSettlementCard 컴포넌트 (주황색 대조 카드) |
| `backend/tests/test_rg_settlement_sync.py` | D-12: fixture 테스트 14개 |
| `docs/tracks/active/track_coupang-rg-fee-accounting.md` | 트랙 마스터 플랜 (4/7 완료) |

## 5. 알려진 이슈 / 주의사항
- **prod 마이그레이션 미실시**: `alembic upgrade head` 아직 prod에 적용 안 됨 → self-verify 전 필수
- **prod self-verify 미실시**: `sync_rg_settlement()` 수동 호출 + 종합조망 RG 카드 표시 확인 미완료
- **Wing 쿠키**: COUPANG_WING1, COUPANG_WING2 둘 다 유효한 쿠키 DB 필요
- **NaverOps 기간필터 버그(기존)**: 이번 diff 외 기존 이슈 — 별도 세션에서 처리 필요
- **프론트 dist 미배포**: `frontend/dist/assets/index-D79z1Lve.js` — prod rsync 아직 안 함
- **S4 데이터 유무**: prod에 `coupang_rg_settlement_fee` 테이블이 비어있으면 RgSettlementCard는 "데이터 없음(sync 필요)" amber 표시

## 6. 다음에 할 작업 (미완료)
- [ ] **prod self-verify** (최우선):
  ```bash
  # prod SSH 후
  cd backend && source .venv/bin/activate
  alembic upgrade head  # coupang_rg_settlement_fee 테이블 생성
  
  # Python 콘솔
  from app.database import SessionLocal
  from app.services.coupang.rg_settlement_sync import sync_rg_settlement
  db = SessionLocal()
  print(sync_rg_settlement(db, "COUPANG_WING1"))
  print(sync_rg_settlement(db, "COUPANG_WING2"))
  # DB에 행 생성 확인 후
  # 프론트 rsync + 종합조망 → 💰 회계 탭 RG 카드 확인
  ```
- [ ] **S5**: 회계 규칙 최종 잠금 + 엑셀 스키마 실증
  - basis(D-10)·dedup(D-11) 코드 확정
  - 종류별 리포트 엑셀 컬럼(vendor_item_id 유무) 확인 (윙 로그인)
  - S6 전제 (Codex #9: 조기 확인)
- [ ] **S6**: download-list/api + 비동기 엑셀 폴링·파싱 → vendor_item_id 추가
- [ ] **S7**: net_profit 플립 + 광고비 dedup 차단 + 모델(A) 감사

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rg-fee-accounting-S4_20260609.md 읽고 이어서 작업해줘
```
