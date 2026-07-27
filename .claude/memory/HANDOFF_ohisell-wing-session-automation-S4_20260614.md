# 세션 인수인계: ohisell Wing 세션 자동화 트랙 S4 (코드 완료·라이브 실측 보류)
> 저장일시: 2026-06-14 22:40 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 직전 HANDOFF: `HANDOFF_ohisell-wing-session-automation-S3_20260614.md`(S0~S3 검산 패널 UI). 본 파일=그 다음 S4(RG정산 자동수집): API 라이브 검증 + P1/P2 코드 + codex 3R PASS. **라이브 실측만 Akamai 차단으로 보류**.

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`(8000). 테스트 `python -m pytest -q`(**191 그린**).
- 프론트: `cd frontend && npm run build`(이번 세션 프론트 미변경).
- **prod = `sellc.ohitech.co.kr`**(ssh 별칭, User=ubuntu, `~/ohisell`, scp/rsync 배포). 백엔드 PM2 `ohisell-backend`(:8001). DB=SQLite. alembic head=`m7n8o9p0q1r2`(S2, 이번 세션 마이그레이션 없음).
- 페처/데몬: `backend/.venv/bin/python3 tools/wing_browser_fetcher.py {login|rg|poll}`, launchd `com.ohisell.wing`. plist=`~/Library/LaunchAgents/com.ohisell.wing.plist`. 설정=`~/.ohisell_wing_fetcher.json`(account_key=COUPANG_WING1·vendor_id=A01564720·ingest_token·prod_base_url + rg_* 키 추가됨). 로그=`~/.ohisell_wing_fetcher.log`.
- ingest 토큰=`AD_INGEST_TOKEN`(prod `~/ohisell/backend/.env`).
- ⚠️ **데몬 com.ohisell.wing 현재 중지(bootout) 상태** — 라이브 로그인 충돌 회피용. 다음 클린 로그인 성공 후 복원: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ohisell.wing.plist`.

## 2. 이번 세션 완료 목록 (S4 — 커밋 `509a075`, 미push)
- ✅ **RG 다운로드 API 3종 라이브 캡처·검증**(Jino DevTools, 오픽스 WING1) → `docs/references/17_*.md` §8-2 기록. **기존 client 코드 body·응답 필드명과 정확히 일치** = "HTTP 500 블로커"는 stale 라벨이었음. 흐름: request-download/api(requestTime 내가 정함)→download-list/api(`{requestTimeFrom,To}`, 항목 requestTime==내값·downloadStatus COMPLETED)→download/api/v2(`{requestTime,locale}`→`{url:S3,vendorId}`). S3=`X-Amz-SignedHeaders=host`+24h→무인증 GET.
- ✅ **S4-P1 `tools/wing_browser_fetcher.py`**: RG 다운로드 흐름 + `rg` CLI. `_POST_JSON_JS`(VS_FETCH_JS 일반화·공용), `_rg_enumerate_group_keys`(status/api 주기열거), `_rg_download_one`(요청→`_rg_find_completed`[requestTime 정확매칭만]→v2, **dup이면 스킵**), `_rg_push_xlsx`(S3 GET→`params={account_key}` + 토큰으로 upload-xlsx push), `_rg_session_ok`/`_rg_login_wait`(정산 status/api 기반 세션감지), `_do_rg_run`, `cmd_rg`.
- ✅ **S4-P2 백엔드**: `rg_settlement_sync.py` 끝 `rg_request_refresh`/`rg_refresh_status`/`rg_claim_refresh`/`rg_mark_heartbeat`(상태행 `COUPANG_WING_RG`, vendor-summary 미러) + `coupang_ops.py` 라우터 `/api/coupang/ops/wing/rg-settlement/{request-refresh,refresh-status,refresh-claim}` + **upload-xlsx `X-Ingest-Token` 인증 추가**(회계 보호) + ingest 성공 후 `rg_mark_heartbeat`.
- ✅ **S4-P2 데몬**: `cmd_poll`에 RG 분기(`_prod_rg_refresh_status`/`_prod_rg_claim`) — 온디맨드 claim + 새벽 일일예약(`rg_daily_hour` 기본7, `rg_min_interval_s` 기본3600, `rg_done_date` 중복방지).
- ✅ **테스트**: `test_rg_settlement_sync.py`(+6 RG 플래그) + `test_vendor_summary_http.py`(+2 HTTP: upload 401·RG refresh flow). **전체 191 그린**.
- ✅ **codex 대화 3R PASS(원칙19)**: R1 P1 2건(데몬 RG 미소비·dup 오기간 업로드)→수정, R2 P1 1건(account_key 명시)→수정+P2 1건(claim-before-success)→근거수용, R3 클린.
- ✅ 설정에 `vendor_id`·`rg_*` 추가(`~/.ohisell_wing_fetcher.json`). failures.jsonl에 Akamai 차단 lesson 기록.

## 3. 확정된 결정사항 (D-8, 트랙 §3)
- RG 자동 다운로드는 **살아있는 브라우저 세션**(페처)으로만(D-5). 백엔드 requests-client(`auto_download_and_ingest`)는 cf_clearance 막혀 미사용.
- 트리거 = **Mac 온디맨드 버튼 + 새벽 일일예약**, **VM 미사용**(데이터센터 IP=Cloudflare 차단). prod는 열람 전용.
- dup(duplicateRequest)이면 **스킵**(download-list가 기간 식별 불가 → 오업로드 방지). 일일 캐던스 >24h라 dup 거의 없음.
- upload-xlsx는 **토큰 필수**(prod 회계=net_profit 소스 보호). 수동 curl도 토큰 포함. 프론트 미사용.
- 범위: **WAREHOUSING_SHIPPING 1종** end-to-end 우선. 나머지 7종 sellerReportType 코드명 미수집(드롭다운: 판매수수료·입출고/배송비·보관비·반품 회수/재입고·반출비·반출 배송 서비스비·재고 손실 보상·부가서비스비).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `tools/wing_browser_fetcher.py` | ★S4 RG 다운로드 흐름·`rg` CLI·`cmd_poll` RG 분기·`_rg_session_ok` |
| `backend/app/services/coupang/rg_settlement_sync.py` | ★끝부분 RG 새로고침 플래그/heartbeat(S4-P2) + 기존 파서/ingest/auto_download |
| `backend/app/routers/coupang_ops.py` | ★`/wing/rg-settlement/*` 3종 + upload-xlsx 토큰 인증 |
| `backend/tests/{test_rg_settlement_sync,test_vendor_summary_http}.py` | ★신규 RG 테스트 8개 |
| `docs/references/17_coupang_rg_fulfillment_fee_policy.md` | ★§8-2 다운로드 API 라이브 캡처 명세 |
| `docs/tracks/active/track_wing-session-automation.md` | ★단일 진실 원천(D-1~D-8, 체크리스트, 다음 액션) |

## 5. 알려진 이슈 / 주의사항
- ⛔ **Akamai 일시 차단(라이브 블로커)**: 페처 로그인 시 `xauth.coupang.com` keycloak에서 **Access Denied**(errors.edgesuite.net). 오늘 반복 시도(login 5회+)로 차단 1시간+ 지속. **더 두드리지 말 것** — 식으면 풀림(내일 아침 확실). 18:29엔 로그인 성공했었음 = 방식은 유효.
- **헤드풀 창 7~18초 자기종료**: 데몬(com.ohisell.wing)이 떠 있으면 수동 login 창과 충돌. 라이브 로그인 전 **데몬 bootout 필수**(현재 이미 중지됨).
- **미검증(원칙22)**: ① 우리 Playwright 페처가 정산 페이지에서 어느 origin 착지·same-origin status/api 200 나오는지(`location.origin`라 자동대응하나 cf_clearance 정산호스트 커버 여부 미실측) ② 전체 다운로드 체인(request→list→v2→S3→push→적재) ③ 데몬 새벽 round-trip. **전부 Akamai-free 창에서 실측**.
- **codex P2 수용분**: 온디맨드 claim이 실행 성공 전에 이뤄져 실패 시 버튼요청 유실(vendor-summary와 동일·일일예약이 재시도 커버·RG UI 버튼 아직 없음). UI 버튼 추가 시 재검토(코드 주석).
- 코드 **미push**(로컬 커밋 509a075만).

## 6. 다음에 할 작업 (미완료 — Akamai-free 창에서, 이상적 내일 아침)
- [ ] **S4-P1 라이브 de-risk**: ① `pkill -f ms-playwright/chromium` ② 데몬 중지 확인 ③ `backend/.venv/bin/python3 tools/wing_browser_fetcher.py login`(창에서 로그인, Akamai 풀려 성공) ④ `… rg` → 정산 same-origin 200·다운로드·prod push·옵션 적재 검증(WAREHOUSING_SHIPPING). 실패 시 로그/origin 진단(추정 금지).
- [ ] **S4-P3 prod 배포 + self-verify(원칙22)**: 백엔드 변경(`rg_settlement_sync.py`·`coupang_ops.py`) scp + PM2 restart. **⚠️ upload-xlsx 토큰 인증이 break change** — 기존 수동 업로드 스크립트 있으면 토큰 추가 필요(프론트는 무영향). prod `/wing/rg-settlement/refresh-status` 200·upload 무토큰 401·페처 rg push→적재 라이브 확인.
- [ ] 데몬 복원: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ohisell.wing.plist` + 새벽 일일예약 round-trip 실측.
- [ ] (선택) 나머지 7종 sellerReportType 코드명 수집 → `CONFIRMED_SELLER_REPORT_TYPES` 확장. RG 새로고침 UI 버튼(RevenueDriftCard) 추가 시 codex P2 재검토.
- [ ] (선택·기존) git origin push, RG수수료 S8 size_mismatch_high 4건, RG발송관제 S7.
- 모델: 라이브 디버깅 가능성 → Opus 권장.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ohisell-wing-session-automation-S4_20260614.md 읽고 이어서 작업해줘
