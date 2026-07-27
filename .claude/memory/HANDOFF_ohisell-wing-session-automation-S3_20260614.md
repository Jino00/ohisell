# 세션 인수인계: ohisell Wing 세션 자동화 트랙 S3 (완료)
> 저장일시: 2026-06-14 20:04 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 직전 HANDOFF: `HANDOFF_ohisell-wing-session-automation-S2_20260614.md`(S0 구조확정 + S1 페처 라이브검증 + S2 백엔드 ingest+reconcile+데몬). 본 파일이 그 다음(S3 검산 패널 UI — 코드+codex 2R PASS+prod 라이브 브라우저 검증 완료).

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`(8000). 테스트: `cd backend && python -m pytest -q`(183 그린).
- 프론트: `cd frontend && npm run dev`(5173) / `npm run build`(tsc -b && vite build).
- **prod = `sellc.ohitech.co.kr`**(ssh config 별칭, User=ubuntu, 경로 `~/ohisell`, git 아님 — scp/rsync 배포). 백엔드 PM2 `ohisell-backend`(:8001, restart #120). DB=SQLite `~/ohisell/backend/ohisell.db`. alembic head=`m7n8o9p0q1r2`(S2 적용).
- **프론트 배포(이번 세션 실행함)**: `npm run build`(frontend/) → `rsync -az --delete frontend/dist/ sellc.ohitech.co.kr:/home/ubuntu/ohisell/frontend/dist/`. nginx가 그 dist 서빙. 현재 prod dist=`index-Wu_C9ezR.js`(서빙 해시 일치 확인).
- 종합조망 API: `GET /api/overview/command-center?from&to&account=COUPANG_WING1|COUPANG_WING2`. 신규(S2) reconcile: `GET /api/overview/revenue-reconcile?from&to&account=COUPANG_WING1`. account 생략=집계(참고치).
- 페처/데몬: `backend/.venv/bin/python3 tools/wing_browser_fetcher.py login|<run>|poll`, launchd `com.ohisell.wing`. (이번 세션은 페처 안 건드림 — 프론트만.)
- ingest 토큰=`AD_INGEST_TOKEN`(prod `~/ohisell/backend/.env`).

## 2. 이번 세션 완료 목록 (S3)
- ✅ **`frontend/src/lib/api.ts`**: `RevenueReconcile` 타입 + `fetchRevenueReconcile(from,to,account)`(account "ALL"→파라미터 생략) + `WingVendorSummaryRefreshStatus` 타입 + `requestWingVendorSummaryRefresh()`(POST `/api/coupang/ops/wing/vendor-summary/request-refresh`) + `getWingVendorSummaryRefreshStatus()`(GET `.../refresh-status`).
- ✅ **`frontend/src/pages/CommandCenter.tsx`**: 회계축(AccountView)에 신규 `RevenueDriftCard`(ReconciliationCard 바로 아래). 표시: 쿠팡 공식 GMV(3P/RG)·우리 매출·차이·드리프트% 테이블 + **D-7 참고치/권위값 배지**(집계 or `coverage.complete=false`→참고치[amber], 계정지정+complete→권위값[emerald]) + **D-2 임계 색상**(`pctColor`: |드리프트|<5% 회색·5~10% 주황·≥10% 빨강 — **추천 없음, 사실 크기 강조만**) + **'판매분석 갱신' 버튼**(`refreshSalesAnalysisNow` — 광고비 버튼 패턴 복제, baseline last_success_at→request-refresh→3s 폴링×215s→완료 시 reconcile 리로드). 상태별 분기: `!has_closed_days`→note, `has_official=false`→"갱신 버튼으로 가져오세요", 정상→테이블.
- ✅ **doFetch 확장**: command-center + revenue-reconcile를 같은 `reqSeq` seq로 병렬 fetch. reconcile은 fail-soft(실패해도 본체 안 막음). 시작 시 `setReconcile(null)`(잔상 제거) + `selRef.current` 갱신.
- ✅ **codex 대화 2R PASS**(원칙19): P1 2건 수정 — ① `doFetch` 시작 시 `setReconcile(null)`(command-center가 먼저 resolve되면 이전 계정 드리프트 카드 잔상 → 검산 surface 정확성 버그) ② `refreshSalesAnalysisNow` 완료 시 `selRef.current`로 현재 선택 재조회(클릭 시점 from/to/account 클로저 stale 회피). round2 findings none. 잔여 note(selRef=로드된 선택, 미적용 폼 입력 아님)는 올바른 동작이라 non-blocking 합의.
- ✅ **build green**(tsc -b && vite build).
- ✅ **prod 배포 + 라이브 self-verify**(원칙22): 위 §3 수치.
- ✅ 커밋 `d047d84`(코드 2파일)+`00a7597`(트랙/TRACKS/progress). 트랙 4/6·MEMORY.md 갱신. **git origin push 안 함**(지시 대기).

## 3. 확정된 결정사항 (번복 금지)
- S3는 **새 D 결정 없음** — 기존 D-2(사실·지표만, 추천 없음)·D-7(참고치/권위값 의미론)을 UI로 구현만 함. D-1~D-7 전부 트랙 파일 §3 참조.
- **reconcile/카드는 읽기전용** — net_profit 등 종합조망 값 불변(S2 입증 계승, 카드는 reconcile API만 호출).
- **임계 색상 = 사실 크기 강조이지 추천 아님**(D-2). 알려진 잔차(3P stale 취소 D-5, RG gross-vs-net D-11)는 카드 하단에 "계산 오류 아님" 명시.
- **'판매분석 갱신' 버튼 = 광고비 버튼과 동일 메커니즘**(request-refresh 플래그 → Mac 데몬 com.ohisell.wing이 소비). 백엔드 라우터 S2에서 준비됨.

### prod 라이브 검증 수치 (browse 실렌더, 오픽스/WING1)
| | 우리 매출 | 쿠팡 공식 GMV | 차이 | 드리프트% |
|---|---:|---:|---:|---:|
| 3P | 1,724,230 | 1,693,230 | 31,000 | +1.83% (회색) |
| RG | 1,918,700 | 1,786,500 | 132,200 | +7.40% (주황) |
| 합계 | 3,642,930 | 3,479,730 | 163,200 | +4.69% (회색) |
- 권위값 배지, 닫힌 과거일 6/8~6/13, 적재 6/6일, complete=true. = ref18 원단위 재현. 콘솔 에러 0.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `frontend/src/pages/CommandCenter.tsx` | ★`RevenueDriftCard`(S3) + `doFetch` reconcile 병렬·`selRef`·`refreshSalesAnalysisNow` |
| `frontend/src/lib/api.ts` | ★`fetchRevenueReconcile` + Wing refresh 2종 + 타입(S3) |
| `backend/app/routers/overview.py` | `GET /revenue-reconcile`(S2) |
| `backend/app/routers/coupang_ops.py` | `/wing/vendor-summary/{ingest,request-refresh,refresh-status,refresh-claim}`(S2) |
| `backend/app/services/coupang/{vendor_summary_sync,revenue_reconcile}.py` | ingest SA + reconcile Harness(S2) |
| `tools/wing_browser_fetcher.py` / `tools/com.ohisell.wing.plist` | Mac 페처·데몬(S1/S2) |
| `docs/tracks/active/track_wing-session-automation.md` | ★단일 진실 원천(D-1~D-7, 체크리스트 4/6) |
| `docs/references/18_*vendor_summary.md` | vendor-summary 스펙·ref18 값 |

## 5. 알려진 이슈 / 주의사항
- **미실측(원칙22 정직)**: UI '판매분석 갱신' 버튼의 실제 215초 데몬 round-trip 클릭은 라이브로 안 눌러봄. reconcile API·카드 렌더는 라이브 확인. 버튼 로직은 광고비 버튼과 동일 패턴 + codex 검증 통과.
- **미관측(S1/S2 계승)**: Wing 세션 만료→회복 경로(cf_clearance 단명, 데몬 수명 중 실측 필요).
- **계정 기본값=전체(ALL)**: 종합조망 첫 진입은 ALL → 카드가 "참고치"로 뜸(정상, D-7). 권위값 보려면 오픽스/오하이테크 선택.
- **헤드풀 창 충돌**: 페처 재실행 시 잔여 chromium 있으면 `pkill -f "ms-playwright/chromium"`.
- **git origin push 미실행** — 로컬 커밋만(d047d84+00a7597, 그 외 e2c2560·7f5225f S2도 미push 가능성). push는 Jino 지시 시.
- reconcile 응답 JSON: `{period{from,to,closed_through,account?}, has_closed_days, has_official, coverage{expected_days,days_with_data,complete}, official{gmv_3p,gmv_rg,gmv_total(숫자),days_with_data,last_refresh(ISO|null)}, ours{revenue_3p,revenue_rg,revenue_total(문자열)}, drift{abs_*(문자열),pct_*(문자열|null)}, note}`.

## 6. 다음에 할 작업 (미완료 — S4)
- [ ] **S4 RG정산 자동수집(S6-auto) 흡수**: RG 수수료 회계 트랙 S6-auto(현재 수동 다운로드)를 Wing 페처 브라우저측 download → prod push(기존 RG 업로드 ingest 재사용, D-5, 백엔드 변경 최소). RG 수수료 트랙은 코드 8/8 완료·운영 단계 → 자동 다운로드 배선만 남음. RG XLSX download-list/api body 캡처 필요(RG수수료 트랙 S6-auto HANDOFF 참조).
- [ ] (선택) UI '판매분석 갱신' 버튼 라이브 클릭 round-trip 실측(데몬 깨우기→215s 폴링→last_success_at 상승→reconcile 리로드 시각 확인).
- [ ] S4 완료 후: codex review + prod 라이브 self-verify(원칙22).
- (선택·기존 트랙) git origin push, RG수수료 S8 size_mismatch_high 4건 Jino 검토, RG발송관제 S7 요일·휴일 세분화.
- 모델: S4는 페처(Python)+ingest 배선 → 단순하면 Sonnet, 흐름 새로 짜면 Opus 권장.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ohisell-wing-session-automation-S3_20260614.md 읽고 이어서 작업해줘
