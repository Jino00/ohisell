# 세션 인수인계: ohisell-rg-fee-accounting-S4verify
> 저장일시: 2026-06-09
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬 실행: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`
- **prod 서버: `sellc.ohitech.co.kr`** (SSH config에 추가함, User=ubuntu, Oracle Cloud 키). 경로 `~/ohisell`. PM2 관리: `ohisell-backend`(포트 8001).
  - prod 재시작: `ssh sellc.ohitech.co.kr "pm2 restart ohisell-backend"`
  - prod 마이그레이션: `ssh sellc.ohitech.co.kr "cd ~/ohisell/backend && source .venv/bin/activate && alembic upgrade head"`
- prod URL: `https://sellc.ohitech.co.kr` (종합조망 = `/command-center`)
- 프론트 배포: `cd frontend && npm run build && rsync -avz --delete dist/ sellc.ohitech.co.kr:~/ohisell/frontend/dist/`
- 주요 환경변수: `DATABASE_URL`, `SECRET_KEY`

## 2. 이번 세션 완료 목록
- ✅ **Phase 1 prod self-verify 완료 (라이브 증거, 원칙22)** — 커밋 `e88e07d`
  - prod 마이그레이션 `g1h2i3j4k5l6` 적용 → `coupang_rg_settlement_fee` 테이블 생성
  - Wing 쿠키 등록(WING1 오픽스 + WING2 오하이테크) → `sync_rg_settlement` 각 **98행, status=ok**
  - 종합조망 API `/api/overview/command-center` `rg_settlement` 섹션 라이브 200
  - 프론트 `RgSettlementCard` 라이브 렌더(WING1 412,156 + WING2 13,295, **순이익 불변=D-6 확정**)
  - 프론트 dist 배포(`index-D79z1Lve.js`)
- ✅ **버그픽스 커밋 `e88e07d`** (3파일)
  - `backend/app/routers/overview.py:56`: `datetime.now(_KST)` → `kst_today()` (★기존 NameError 버그, 커밋 a2bbd3a부터, 종합조망 500 원인). codex review PASS.
  - `backend/app/models.py`: `CoupangRgSettlementFee` 모델 (e7cb99f에서 누락된 S4 모델, 로컬 미커밋분)
  - `backend/app/clients/coupang/__init__.py`: `CoupangWingRgSettlementClient` export 추가
- ✅ failures.jsonl 2건 기록 (httpOnly 쿠키 / overview _KST)
- ✅ 트랙 파일 + claude-progress.txt 갱신

## 3. 확정된 결정사항
- **★코드 정정(원칙22)**: `status/api` 스키마·body는 라이브와 **정확히 일치**(S0 실측 옳았음). body=`{startDate, endDate, searchDateType:"SALES"|"PAYMENT"}` (UTC ISO `T15:00:00.000Z`). 응답=`settlementStatusReports[].settlementStatusReportDetail.{totalTakeRateAmountWithVat, totalFulfillmentFeeDeductionAmount, totalStorageFeeDeductionAmount, totalWarehousingFeeDeductionAmount...}`. (조사 중 "스키마 틀림" 잠정단정은 내 직접호출 body 오류였음 — 정정됨)
- **★Wing 쿠키 = Copy as cURL만**: `document.cookie`엔 httpOnly 세션쿠키(`CGSID_PARTNERADMINWEB`·`JSESSIONID`·`sxSessionId`) 없음 → 302. JS·CDP(getAllCookies/getCookies 모두 allowlist deny) 못 읽음. DevTools Network에서 실제 요청 우클릭→Copy as cURL이 유일 경로. 등록 API = `POST /api/coupang/ops/inbound/cookie` body `{account_key, curl}` (광고비/RG입고와 공유 `parse_curl_cookies`).
- **★S6 전제 확정**: `status/api` 응답엔 **vendor_item_id 없음**(정산주기별 집계뿐). 옵션단위 귀속은 S6 `download-list/api` 엑셀이 유일 경로.
- D-6 reconciliation-first: Phase 1 = 대조뷰만, net_profit 불변 (라이브 확인됨).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/clients/coupang/rg_settlement.py` | S1 Wing 정산 API 클라이언트 (status/api·profit-status·download-list). body·스키마 라이브 검증 완료 |
| `backend/app/models.py` | `CoupangRgSettlementFee` 모델 (커밋 e88e07d) |
| `backend/app/services/coupang/rg_settlement_sync.py` | S3 Harness (수집·파싱·upsert). `_FEE_FIELD_MAP` 필드매핑 |
| `backend/app/services/coupang/intelligence.py` | S4 `_agg_rg_settlement_fees()` + command_center `rg_settlement` 섹션 |
| `backend/app/routers/overview.py` | 종합조망 API. `_KST` 버그 수정됨(e88e07d) |
| `frontend/src/pages/CommandCenter.tsx` | `RgSettlementCard` (회계 탭, 라이브 렌더 확인) |
| `docs/tracks/active/track_coupang-rg-fee-accounting.md` | 트랙 마스터(4/7, Phase1 self-verify 완료 기록됨) |

## 5. 알려진 이슈 / 주의사항
- **Wing 쿠키 만료**: 세션쿠키라 주기적 만료. sync가 302 받으면 status=red → Copy as cURL 재등록 필요. (이번에 등록한 쿠키도 며칠 후 만료 가능)
- **prod 배포 = scp/rsync 수동**: PM2라 백엔드 파일 변경 시 `scp` 후 `pm2 restart ohisell-backend` 필요. 프론트는 `npm run build` + `rsync dist/` + 브라우저 캐시버스트(`?_cb=...`).
- **브라우저 캐시**: 프론트 새 번들 배포 후 index.html 캐시 때문에 구 번들 로드될 수 있음 → URL에 `?_cb=타임스탬프` 붙여 강제 새로고침.
- **종합조망 날짜 파라미터**: `?from=YYYY-MM-DD&to=YYYY-MM-DD` (date_from/date_to 아님).
- **NaverOps 기간필터 버그(기존)**: 이번 diff 외 별도 이슈, 미해결.

## 6. 다음에 할 작업 (미완료) — S5
- [ ] **S5: 회계 규칙 최종 잠금 + 엑셀 스키마 실증**
  - **basis(D-10)·dedup(D-11) 코드 확정** — 매출인식일 기준, RG 광고비 출처 정합 규칙 코드화
  - **★종류별 리포트 엑셀에 vendor_item_id 유무 확인** — Wing `download-list/api` 또는 정산현황 "엑셀 다운로드"로 실제 엑셀 생성·다운로드해 컬럼 확인. (S6 옵션단위 수집 가능 여부 결정)
    - Wing 정산현황 페이지: `https://wing.coupang.com/tenants/rfm/settlements/status-new` (정산현황 탭에 "엑셀 다운로드" 버튼)
    - GStack 브라우저로 wing 로그인 후 엑셀 생성·다운로드 흐름 캡처 권장
- [ ] **S6**: `download-list/api` + 비동기 엑셀 폴링·파싱 → `CoupangRgSettlementFee`에 vendor_item_id 추가
- [ ] **S7**: net_profit 플립 + 광고비 dedup 차단(D-11) + 모델(A) 감사

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rg-fee-accounting-S4verify_20260609.md 읽고 이어서 작업해줘 (S5 진행)
```
