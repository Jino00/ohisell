# 세션 인수인계: ohisell-rg-fee-accounting-S3
> 저장일시: 2026-06-09 00:00
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`
- 테스트 실행: `cd backend && source .venv/bin/activate && PYTHONPATH=. python -m pytest tests/test_rg_settlement_sync.py -v`
- 주요 환경변수: `DATABASE_URL`, `SECRET_KEY`

## 2. 이번 세션 완료 목록
- ✅ **S1: CoupangWingRgSettlementClient SA** (`backend/app/clients/coupang/rg_settlement.py` 신규)
  - Wing 내부 API 래퍼. HMAC 미상속, 세션쿠키+x-xsrf-token.
  - 메서드: `get_settlement_status()`, `get_profit_status()`, `get_download_list()`
  - `_kst_date_to_utc_iso("YYYY-MM-DD") → "YYYY-MM-DDT15:00:00.000Z"` (S0 실측 확정)
  - S0 실측(2026-06-09): POST body = `{"startDate":"...T15:00:00.000Z","endDate":"...","searchDateType":"SALES"}`
- ✅ **S2: CoupangRgSettlementFee 모델 + 마이그레이션** (`backend/app/models.py` 수정, `backend/alembic/versions/g1h2i3j4k5l6_add_coupang_rg_settlement_fee.py` 신규)
  - grain = (account_key × recognition_date_from × recognition_date_to × fee_type)
  - 유니크 제약: `uq_coupang_rg_settlement_fee`
  - down_revision = `b2d4f6082ace` (codex review 후 수정, 현재 실제 헤드)
- ✅ **S3: rg_settlement_sync Harness** (`backend/app/services/coupang/rg_settlement_sync.py` 신규)
  - `_FEE_FIELD_MAP` 7종: sale_fee, fulfillment, storage, warehousing, return_shipping, return_handling, ad_sales
  - `_parse_status_response()`: dedup + 분할정산 합산(70%+30%)
  - `sync_rg_settlement()`: fail-soft (WingAuthError→red, WingReadError→return dict)
  - 기본 날짜: `kst_now().date()` (UTC 서버 오차 방지)
- ✅ **fixture 테스트** (`backend/tests/test_rg_settlement_sync.py` 신규) — 14/14 PASS
- ✅ **codex review 완료** — P1 Alembic 수정(down_revision 정정), P2 kst_now 적용, P1 날짜변환 S0 근거로 기각
- ✅ **커밋**: `c8d7d0c` — `fix(rg-fee): codex review 수정 — Alembic 헤드 정정 + KST 기본날짜`

## 3. 확정된 결정사항
- **D-6 (reconciliation-first)**: Phase 1 = net_profit 불변, 'RG 정산 비용(미반영)' 독립 대조 지표만 표시
- **D-7**: compute_command_center에 account_key별 독립 섹션/카드로 표시
- **D-8**: parser는 Harness에 흡수 (별도 SA 없음)
- **D-9**: sale_fee(B) + fulfillment(J) 둘 다 수집
- **D-10**: 날짜 basis = 매출인식일, `searchDateType="SALES"`
- **D-12**: fixture 테스트(머니코드 예외 — 라이브 self-verify 대신)
- **S0 실측 확정**: `_kst_date_to_utc_iso("YYYY-MM-DD") → "YYYY-MM-DDT15:00:00.000Z"` (브라우저 동일 포맷)
- **Alembic down_revision**: `b2d4f6082ace` (현재 헤드, 기존 `f2a4c6e8b0d1`는 틀림)

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/clients/coupang/rg_settlement.py` | S1: Wing 정산 API 클라이언트 SA |
| `backend/app/models.py` | S2: CoupangRgSettlementFee 모델 추가 |
| `backend/alembic/versions/g1h2i3j4k5l6_add_coupang_rg_settlement_fee.py` | S2: DB 마이그레이션 |
| `backend/app/services/coupang/rg_settlement_sync.py` | S3: Harness (수집·파싱·upsert) |
| `backend/tests/test_rg_settlement_sync.py` | D-12: fixture 테스트 14개 |
| `docs/tracks/active/track_coupang-rg-fee-accounting.md` | 트랙 마스터 플랜 (단일 진실 원천) |
| `docs/references/17_coupang_rg_fulfillment_fee_policy.md` | RG 수수료 정책 레퍼런스 |

## 5. 알려진 이슈 / 주의사항
- **prod 마이그레이션 미실행**: `alembic upgrade head` 아직 prod에 적용 안 됨 → S4 전에 SSH 접속해서 실행 필요
- **prod self-verify 미실시**: `sync_rg_settlement()` 수동 호출로 실제 DB 데이터 확인 필요
- **Wing 쿠키**: COUPANG_WING1, COUPANG_WING2 둘 다 유효한 쿠키가 DB에 있어야 sync 작동
- **S4 착수 전**: TRACKS.md 확인, 활성 트랙 = `track_coupang-rg-fee-accounting.md` (3/7)

## 6. 다음에 할 작업 (미완료)
- [ ] **prod self-verify**: SSH → `alembic upgrade head` → `sync_rg_settlement("COUPANG_WING1")` 수동 호출 → DB 확인
- [ ] **S4**: `compute_command_center`에 'RG 정산 비용(미반영)' 독립 지표 추가
  - account_key별 주별 RG 비용 합산
  - net_profit 불변 (Phase 1, D-6)
  - API 엔드포인트 추가
  - 프론트엔드 카드/섹션 추가
  - scheduler에 일일 sync job 등록
- [ ] **S5~S7**: Phase 2 (규칙 잠금 → 엑셀 스키마 실증 → net_profit 플립)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rg-fee-accounting-S3_20260609.md 읽고 이어서 작업해줘
```
