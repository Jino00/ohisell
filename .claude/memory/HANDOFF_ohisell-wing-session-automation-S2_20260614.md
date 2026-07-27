# 세션 인수인계: ohisell Wing 세션 자동화 트랙 S2 (완료)
> 저장일시: 2026-06-14 18:32 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 직전 HANDOFF: `HANDOFF_ohisell-wing-session-automation-S1_20260614.md`(S0 구조확정 + S1 페처 라이브검증). 본 파일이 그 다음(S2 백엔드 ingest+reconcile+데몬 — 코드+prod 라이브 self-verify 완료).

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`(8000). 테스트: `cd backend && python -m pytest -q`(**183 그린**). playwright는 `backend/.venv/bin/python3`에 설치됨.
- 프론트: `cd frontend && npm run dev`(5173) / `npm run build`
- **prod = `sellc.ohitech.co.kr`**(ssh config 별칭 등록됨, User=ubuntu, 경로 `~/ohisell`, git 아님 — scp/rsync 배포). 백엔드 PM2 `ohisell-backend`(:8001, 현재 restart #120). DB=SQLite `~/ohisell/backend/ohisell.db`. **alembic head=`m7n8o9p0q1r2`(prod 적용 완료)**. prod DB 백업=`~/ohisell/backend/ohisell.db.backup_wingS2_20260614_092642`.
- 종합조망 API: `GET /api/overview/command-center?from&to&account=COUPANG_WING1|COUPANG_WING2`. WING1=오픽스(vendor A01564720, 광고·RG·매출분석 데이터 전용)·WING2=오하이테크(상품만, 매출≈0).
- **신규 reconcile API(S2)**: `GET /api/overview/revenue-reconcile?from&to&account=COUPANG_WING1`. 응답=닫힌일 드리프트%(아래 §5 JSON 형태).
- **Wing 페처(S2 push 가동)**: `backend/.venv/bin/python3 tools/wing_browser_fetcher.py login|<run>|poll`. 세션 state=`~/.ohisell_wing_state.json`, 설정=`~/.ohisell_wing_fetcher.json`(account_key/prod_base_url/ingest_token=광고 설정 재사용+account_key 추가), 로그=`~/.ohisell_wing_fetcher.log`. **데몬 `com.ohisell.wing` launchd 설치·로드됨**(plist=`~/Library/LaunchAgents/com.ohisell.wing.plist`, 15s 폴·창은 요청 시만).
- 광고 페처(복제 원본·상주): `tools/ad_cost_browser_fetcher.py poll`, launchd `com.ohisell.adcost`.

## 2. 이번 세션 완료 목록
- ✅ **백엔드 모델+마이그레이션**: `backend/app/models.py`에 `CoupangVendorSummaryDaily`(grain: summary_date×account_key×registration_type[NORMAL=3P/RFM=RG], gmv·units_sold·last_refresh) + alembic `backend/alembic/versions/m7n8o9p0q1r2_add_coupang_vendor_summary_daily.py`(prod head 적용).
- ✅ **ingest+store SA**: `backend/app/services/coupang/vendor_summary_sync.py` — `ingest_vendor_summary`(snapshot upsert)·`get_vendor_summary_totals`(3P/RG 합계+days_with_data)·heartbeat/refresh(account_key="COUPANG_WING_VS").
- ✅ **reconcile Harness**: `backend/app/services/coupang/revenue_reconcile.py` — `reconcile_revenue(db,dfrom,dto,account)`. compute_command_center(우리 revenue_3p/rg) vs get_vendor_summary_totals(쿠팡 GMV), 닫힌일 드리프트%=(우리−쿠팡)/쿠팡. **읽기전용(net_profit 불변)**.
- ✅ **라우터**: `backend/app/routers/coupang_ops.py`에 `/wing/vendor-summary/ingest`(토큰)·`request-refresh`·`refresh-status`·`refresh-claim`(토큰) + `_VS_ACCOUNTS` account_key 검증. `backend/app/routers/overview.py`에 `GET /revenue-reconcile`.
- ✅ **페처 push 배선**: `tools/wing_browser_fetcher.py`에 `_push`·`_push_configured` 게이트·`_summarize` units 캡처·`cmd_poll` 데몬 + plist `tools/com.ohisell.wing.plist`.
- ✅ **테스트 16**: `backend/tests/test_vendor_summary_reconcile.py`(11) + `backend/tests/test_vendor_summary_http.py`(5, TestClient 격리DB). 전체 183 그린.
- ✅ **Codex 대화 3R 합의**(원칙19): P1 2건 수정(부분 적재 커버리지·집계 권위 D-7), P2 2건 근거기각(request-refresh 무토큰=광고 parity, select-insert idempotency=flock 직렬화), account_key 검증 추가. round3 Findings none.
- ✅ **prod 배포·라이브 self-verify**(원칙22): DB 백업→6파일 scp→`alembic upgrade head`→PM2 재시작#120→페처 run push 14행→reconcile 6/8~6/13 **ref18 원단위 재현**(아래 §3)→데몬 설치→request-refresh 자동 사이클 동작 확인.
- ✅ 커밋 `e2c2560`(코드)+`7f5225f`(기록). 트랙 3/6·progress·auto-memory(MEMORY.md) 갱신. **git origin push 안 함**(지시 대기).

## 3. 확정된 결정사항 (번복 금지)
- **D-7 (reconcile 완전성·권위, S2 codex 대화)**: ① 드리프트는 **닫힌 윈도우 전 날짜 적재 시만 권위**(`complete = days_with_data >= expected_days`, 날짜 그레인). 등록유형(NORMAL/RFM)별 결측은 갭 아님(쿠팡이 0판매 유형 행 생략 → 진짜 0과 모호). ② **집계(`account=None`) 뷰는 절대 권위 아님(`complete=False` 고정)** — ours=전계정 매출, official=적재계정만. 정합 판정은 **계정 지정(WING1/2)으로만**. ③ ingest account_key는 {WING1,WING2} 검증(오타가 집계 official 오염 차단).
- **reconcile는 읽기전용** — net_profit 등 종합조망 값 불변(테스트+prod 입증, command-center net_profit=2,294,339 그대로).
- D-4/D-5/D-6 계승: Mac 페처 push만·백엔드 ingest만 / vendor-summary는 브라우저측 fetch·별도 데몬 com.ohisell.wing / 모바일 UA로 m-wing.coupang.com origin same-origin 호출(절대호스트는 CORS).
- **운영주의**: 페처/데몬 코드 변경 후 `launchctl kickstart -k gui/$(id -u)/com.ohisell.wing` = 배포(미재시작=stale).
- ingest 토큰 = `AD_INGEST_TOKEN`(광고와 공용, prod `~/ohisell/backend/.env`에 존재).

### prod 라이브 검증 수치 (ref18 원단위 재현)
| | 우리(command-center) | 쿠팡 공식 GMV | 드리프트 |
|---|---:|---:|---:|
| 3P | 1,724,230 | 1,693,230 | +1.83% |
| RG | 1,918,700 | 1,786,500 | +7.40% |
- coverage complete=true(6/6일), 윈도우 6/8~6/13(account=COUPANG_WING1).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/vendor_summary_sync.py` | ★ingest+store SA(snapshot upsert·3P/RG 합계·heartbeat/refresh) |
| `backend/app/services/coupang/revenue_reconcile.py` | ★reconcile Harness(닫힌일 드리프트%·D-7·읽기전용) |
| `backend/app/models.py` | `CoupangVendorSummaryDaily` 모델 |
| `backend/alembic/versions/m7n8o9p0q1r2_*.py` | 테이블 마이그레이션(prod head) |
| `backend/app/routers/coupang_ops.py` | vendor-summary ingest·refresh 라우터(`_VS_ACCOUNTS`·`_require_ingest_token`) |
| `backend/app/routers/overview.py` | `GET /revenue-reconcile` |
| `tools/wing_browser_fetcher.py` | Mac 페처(login/run/poll·`_push`·units) |
| `tools/com.ohisell.wing.plist` | launchd 데몬 정의(설치됨) |
| `backend/tests/test_vendor_summary_reconcile.py` / `_http.py` | fixture 11 + HTTP 5 |
| `docs/tracks/active/track_wing-session-automation.md` | ★단일 진실 원천(D-1~D-7, 체크리스트 3/6) |
| `docs/references/18_*vendor_summary.md` | vendor-summary 스펙·ref값 |

## 5. 알려진 이슈 / 주의사항
- **미관측(S1과 동일)**: Wing 세션 만료→회복 경로. cf_clearance 단명 → 만료 시 `cmd_poll`/`_do_run`이 headful 로그인 대기 폴백(광고 패턴). 데몬 수명 중 실측 필요(추정 금지).
- **헤드풀 창 충돌**: 페처 재실행 시 이전 playwright chromium 잔여가 남으면 새 창이 곧 닫힘. 재실행 전 `pkill -f "ms-playwright/chromium"`.
- **잔차 해석**(드리프트는 정상): 3P +1.8%=S6 후 잔여 stale 취소(D-5), RG +7.4%=gross-vs-net(우리 RG gross, 취소 미차감, D-11). 신규 버그 아님.
- **rg_flip_status=not_applied_no_data**(prod 6/8~6/13 WING1): 해당 윈도우 RG 정산 fee 데이터 없음 — S2와 무관(RG 수수료 트랙 소관).
- **git origin push 미실행** — 로컬 커밋만(e2c2560+7f5225f). push는 Jino 지시 시.
- reconcile 응답 JSON 형태(라이브):
  `{period{from,to,closed_through,account}, has_closed_days, has_official, coverage{expected_days,days_with_data,complete}, official{gmv_3p,gmv_rg,gmv_total,days_with_data,last_refresh}, ours{revenue_3p,revenue_rg,revenue_total}, drift{abs_3p,abs_rg,abs_total,pct_3p,pct_rg,pct_total}, note}` (Decimal=문자열, pct는 official 0이면 null).

## 6. 다음에 할 작업 (미완료 — S3)
- [ ] **S3 검산 패널 UI**: 종합조망(`frontend/src/components/.../CommandCenter.tsx` ReconciliationCard 확장)에 "쿠팡 공식 GMV(3P/RG) + 드리프트%" 컬럼 추가. `GET /api/overview/revenue-reconcile` 호출(api.ts 타입 추가).
- [ ] **'판매분석 갱신' 버튼**: → `POST /api/coupang/ops/wing/vendor-summary/request-refresh`(백엔드 준비됨, 광고 갱신 버튼 패턴 복제). 클릭 후 refresh-status 폴링.
- [ ] **표시 규칙(D-2/D-7)**: 드리프트 임계 초과 시각 강조(사실 표시만, 추천 금지). `complete=false`(부분/집계) 시 "참고치" 라벨. 권위 판정은 계정 지정 뷰에서만.
- [ ] S3 완료 후 `/qa`(브라우저 실동작) + codex review + prod nginx dist 배포(rsync) + 라이브 self-verify.
- [ ] (이후) S4 RG정산 자동수집(S6-auto) 흡수.
- (선택·기존 트랙) git origin push, RG수수료 S8 size_mismatch 4건, RG발송관제 S7 요일·휴일 세분화.
- 모델: S3은 프론트(React/TS) → **Sonnet 권장**.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ohisell-wing-session-automation-S2_20260614.md 읽고 이어서 작업해줘
