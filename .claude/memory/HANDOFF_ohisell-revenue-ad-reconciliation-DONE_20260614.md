# 세션 인수인계: ohisell 정합성 트랙 마감 + Wing 세션 자동화 트랙 개설
> 저장일시: 2026-06-14 16:30 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 직전 HANDOFF: `HANDOFF_ohisell-revenue-ad-reconciliation-S5_20260614.md`(7/7 시점). 본 파일이 그 다음(트랙 마감).

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`(8000). 테스트: `cd backend && python -m pytest -q`(반드시 backend/에서, 167 그린)
- 프론트: `cd frontend && npm run dev`(5173) / `npm run build`
- **prod = `sellc.ohitech.co.kr`**(ssh, User=ubuntu). 경로 `~/ohisell`(git 아님 — scp/rsync 배포). 백엔드 PM2 `ohisell-backend`(8001), DB=SQLite `~/ohisell/backend/ohisell.db`. alembic head=`l6m7n8o9p0q1`.
- 종합조망 API: `GET /api/overview/command-center?from&to&account=COUPANG_WING1|COUPANG_WING2`(생략=전체). 응답 구조: top=`{period,account,ad,product,rg_settlement}`, 매출/순이익=`d['account']['summary']`(revenue_3p/revenue_rg/net_profit/net_profit_pre_nonpa/ad_nonpa_deducted/net_profit_pre_rg), 광고=`d['ad']['summary']`(ad_confirmed_pa/total/nonpa/ad_spend/ad_basis).
- 계정: COUPANG_WING1=오픽스(vendor A01564720, 광고·RG 데이터 전용)·COUPANG_WING2=오하이테크(A01029796, 상품만).
- **★광고비 페처 = launchd 상주 poll 데몬** `com.ohisell.adcost`(PID 691 가동중). `tools/ad_cost_browser_fetcher.py poll`. 대시보드 '광고비 갱신' 버튼이 유일 fetch 트리거. **코드 변경 후 반드시 `launchctl kickstart -k gui/$(id -u)/com.ohisell.adcost`로 재시작=배포.** 로그: `~/.ohisell_ad_fetcher.log`.

## 2. 이번 세션 완료 목록
- ✅ **Task 1 — 옵션 보고서 윈도우 7→30 + _do_run 재정렬** (커밋 d9b57fc·bd7df39, push 완료)
  - `tools/ad_cost_browser_fetcher.py`: `_option_window` option_days 기본 7→30(net_profit PA 광고비=CoupangAdOptionDaily 소스가 7일이라 8~30일차 PA 누락→과대였음, 30일로 비-PA와 정렬). `_fetch_option_report` poll_timeout 150→300s. **★`_do_run` 재정렬**: 메인(report/cost)+SALES push를 무거운 옵션 보고서 fetch 전에 수행(페처 단일경로 버튼 UI 215s 윈도우 블록 방지).
  - codex 2R pass: [P2-1] 옵션 fetch를 data 파싱성공+main_rc==0 게이트(SALES는 파싱성공만, 과거백필 보존) / [P2-2] SALES fetch+push try/except로 best-effort 보존(재정렬로 push가 컨텍스트 안 이동해 생긴 새 실패모드 차단). R2 신규 0.
  - 데몬 재배포(kickstart) → **라이브 검증**: 옵션 30일 보고서 1579행·30일(05-15~06-13) 실적재, 재정렬 로그순서(메인15:55:38→SALES15:55:39→옵션15:55:48) 확인, 미커버 PA구간 5/16~6/5 ad_spend 317,532 차감 실증.
- ✅ **stale 데몬 비-PA erasure 발견·복구**(보너스): prod `all_day_cost>day_cost` 0일 → stale 데몬(S5 미재시작, 구 코드 ALL_DELIVERED 미전송)이 ingest clobber(`ad_cost_sync.py:274` all_cost None→all=day)로 6/9~6/13 비-PA 갭 덮어씀. 데몬 재시작+풀트리거로 비-PA 65,677 복원(감사체인 `pre_nonpa 1,939,487−65,677=pre_rg 1,873,810=net_profit` 정확). failures.jsonl 기록.
- ✅ **Task 2 — 자동대조 읽기전용 프로브 + 매출 정합 라이브 입증**
  - `vendor-summary`(Wing 내부, `POST /tenants/rfm-ss/api/business-insight/vendor-summary`) 프로브 성공: 쿠팡 공식 GMV를 3P(NORMAL)/RG(RFM)로 분리 제공. body=`{startDate,endDate,registrationTypes:[NORMAL,RFM],searchIds:[]}`. **ref 18 신설**.
  - **닫힌 윈도우 6/8~6/13 라이브 1:1 대조**: 3P 우리 1,724,230 vs 쿠팡 1,693,230(+1.8%, S6 잔여 stale D-5), RG 우리 1,918,700 vs 쿠팡 1,786,500(+7.4%, D-11 gross-vs-net). **신규버그 0 — 우리 매출이 쿠팡 공식과 문서화 오차 내 일치함을 처음 라이브 입증**(트랙 원래 목표 달성).
- ✅ **Task 3 — 정합성 트랙 completed/ 마감 + 신규 트랙 스캐폴딩**(커밋 7ebf5e7, push 완료)
  - `git mv` 정합성 트랙 active→completed/. TRACKS.md 갱신(Completed로 이동).
  - **신규 트랙 `docs/tracks/active/track_wing-session-automation.md`** 생성(⚠️구조 설계 스캐폴딩만, 코딩 전). progress·MEMORY(auto) 갱신.
- ✅ git push 6커밋 origin/main (b15c6b1..7ebf5e7).

## 3. 확정된 결정사항
- **정합성 트랙 완료(B 결정)**: 매출을 쿠팡과 맞추는 원래 목표는 vendor-summary 프로브로 라이브 1:1 입증돼 **달성·증명**. 트랙 completed/ 마감.
- **자동대조(드리프트 감시)는 별도 트랙(C)**: Wing 세션 freshness(cf_clearance=Cloudflare IP+UA 바인딩·단명, requests로 갱신 불가→headful 브라우저 필요)가 필요. 광고 페처(`ad_cost_browser_fetcher.py`) 헤드풀 패턴을 wing.coupang.com용으로 복제하면 해결. vendor-summary 자동대조 + RG정산 S6-auto 공용 인프라. **Jino 원문**: "C는 가능, 별도 트랙으로 제대로", "너의 제안대로 가자".
- **운영 주의**: 페처/상주데몬 코드 변경 후 반드시 `launchctl kickstart -k`로 재시작(미재시작=stale 코드, 본 세션 비-PA erasure 근본 원인).
- 자동대조 비교는 **닫힌 과거일**만(당일은 sync 시차로 부정확, ref 18).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `tools/ad_cost_browser_fetcher.py` | 광고비 헤드풀 페처(launchd 데몬). 옵션30일·재정렬·main_rc 게이트. Wing 트랙 S1의 복제 원본 |
| `backend/app/services/coupang/ad_cost_sync.py` | `ingest_ad_cost_days(all_cost)`(L274 None→all=day clobber 주의)·`get_ad_cost_totals` |
| `backend/app/services/coupang/intelligence.py` | command-center. net_profit PA=CoupangAdOptionDaily, 비-PA 계정게이트(L~730) |
| `backend/app/clients/coupang/rg_settlement.py` | Wing 내부 API 호출(cookie+xsrf), 모바일 UA. Wing 트랙 참고 |
| `backend/app/clients/coupang/inbound.py` | `parse_curl_cookies`(cURL→cookie/xsrf 추출). Wing 트랙 재사용 |
| `coupang_wing_cookie`(models.py:676) | Fernet 암호화 Wing 쿠키 저장. 라우터 `coupang_ops.py:983`(cURL 붙여넣기 저장) |
| `docs/references/18_coupang_wing_business_insight_vendor_summary.md` | vendor-summary API 스펙+프로브 검증(신설) |
| `docs/tracks/active/track_wing-session-automation.md` | ★다음 작업 트랙(구조 스캐폴딩) |
| `docs/tracks/completed/track_coupang-revenue-ad-reconciliation.md` | 마감된 정합성 트랙(이력) |

## 5. 알려진 이슈 / 주의사항
- **WING1 Wing 쿠키 만료**: prod `coupang_wing_cookie` WING1 last_success=6/10(4일 경과, 만료). 프로브는 Jino가 이번 세션에 준 fresh cURL로 했음(임시파일 삭제됨). Wing 트랙 작업 시 fresh 쿠키 또는 헤드풀 세션 필요.
- **광고는 오픽스(WING1) 전용**. WING2(오하이테크) 광고 0. 비-PA 계정게이트가 WING2 차단.
- prod **git 아님** — scp/rsync 배포. 광고 페처는 Mac 로컬(launchd). Mac off 시 광고비 stale(30일 윈도우라 복구 여유).
- RG정산 S6-auto는 기존 트랙(track_coupang-rg-fee-accounting, 8/8 운영)의 잔여 — Wing 세션 자동화 트랙이 흡수 설계.

## 6. 다음에 할 작업 (미완료)
- [ ] **Wing 세션 자동화 트랙 S0**: `track_wing-session-automation.md` §4 제안 구조 검토 → "이 구조로 진행할까요?" 승인 → /model opus 계획서.
- [ ] S1 WingBrowserFetcher(헤드풀 세션 유지) — 광고 페처 복제, wing.coupang.com SSO/cf_clearance 흐름 라이브 확인.
- [ ] S2 vendor_summary SA + 매출 대조 Harness(닫힌일 드리프트%). S3 검산패널 UI 컬럼. S4 RG정산 자동수집 흡수.
- (선택·기존 트랙) RG수수료 S8 size_mismatch_high 4건 Jino 검토 / RG발송관제 S7 요일·휴일 세분화(데이터 누적 대기).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ohisell-revenue-ad-reconciliation-DONE_20260614.md 읽고 이어서 작업해줘
