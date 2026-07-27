# 세션 인수인계: ohisell Wing 세션 자동화 트랙 S0+S1
> 저장일시: 2026-06-14 17:41 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 직전 HANDOFF: `HANDOFF_ohisell-revenue-ad-reconciliation-DONE_20260614.md`(정합성 트랙 마감·Wing 트랙 개설). 본 파일이 그 다음(Wing 트랙 S0 구조확정 + S1 페처 라이브검증).

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`(8000). 테스트: `cd backend && python -m pytest -q`(backend/에서, 167 그린). **playwright는 `backend/.venv/bin/python3`에 설치됨**.
- 프론트: `cd frontend && npm run dev`(5173) / `npm run build`
- **prod = `sellc.ohitech.co.kr`**(ssh User=ubuntu, 경로 `~/ohisell`, git 아님 — scp/rsync 배포). 백엔드 PM2 `ohisell-backend`(8001), DB=SQLite `~/ohisell/backend/ohisell.db`. alembic head=`l6m7n8o9p0q1`.
- 종합조망 API: `GET /api/overview/command-center?from&to&account=COUPANG_WING1|COUPANG_WING2`. 계정: WING1=오픽스(vendor A01564720, 광고·RG·매출분석 데이터 전용)·WING2=오하이테크(A01029796, 상품만).
- **Wing 페처(S1 신규)**: `backend/.venv/bin/python3 tools/wing_browser_fetcher.py login|<run>`. 세션 state=`~/.ohisell_wing_state.json`(저장됨), 로그=`~/.ohisell_wing_fetcher.log`, 설정=`~/.ohisell_wing_fetcher.json`(S1은 없어도 동작). **헤드풀 창이 떠서 Jino가 wing.coupang.com 로그인 필요**(모바일 UA라 m-wing으로 라우팅됨).
- 광고 페처(복제 원본·상주): `tools/ad_cost_browser_fetcher.py poll`, launchd `com.ohisell.adcost`. plist=`~/Library/LaunchAgents/com.ohisell.adcost.plist`.

## 2. 이번 세션 완료 목록
- ✅ **S0 구조 확정·승인**(Jino "그래"). 트랙 §3에 D-4/D-5 추가, §4 확정 구조 교체. 계획서 `docs/PLAN_wing-session-automation.md` 신설(S1~S4 What/Why/완료기준).
  - D-4 런타임 2분리: Wing 페처=Mac 로컬 헤드풀 프로세스(push만), 백엔드=ingest만.
  - D-5: vendor-summary/RG는 백엔드 requests 아니라 **브라우저측 fetch**(cf_clearance 재생불가), **별도 데몬 com.ohisell.wing**(광고 aid/keycloak ↔ Wing cf_clearance 세션 격리).
- ✅ **S1 WingBrowserFetcher 작성·라이브 검증**(커밋 eae19d9). `tools/wing_browser_fetcher.py`(광고 페처 복제, 모바일 iPhone UA, storage_state, login/run, flock, 닫힌과거일 윈도우 D-3).
  - login→state 저장→첫 파싱, run 재로드→vendor-summary 200→3P/RG GMV 파싱 모두 라이브 성공.
  - **★D-6 라이브 발견**(아래 §3). 자체검증: payload/파싱/인증감지 단위테스트 PASS. codex review PASS(지적0).
  - **교차검증(원칙22)**: 페처 6/8~6/13 GMV = ref18 수동 cURL과 **원 단위 일치**(3P 1,693,230·RG 1,786,500).
- ✅ 트랙 체크리스트 2/6, progress, MEMORY(auto), failures.jsonl(3178행, m-wing CORS 교훈) 갱신. 커밋 eae19d9+ec60c81 push 완료(origin/main).

## 3. 확정된 결정사항 (번복 금지)
- **D-4 런타임 경계**: Wing 페처는 Mac 헤드풀 프로세스, 백엔드는 push 데이터 ingest만(직접 호출 금지).
- **D-5 vendor-summary/RG = 브라우저측 fetch + 별도 데몬 com.ohisell.wing**(광고 데몬과 분리).
- **★D-6 (S1 라이브 실측)**: 모바일 UA(필수, ref18)로 로그인하면 Wing이 **`m-wing.coupang.com`(모바일 호스트)로 라우팅**. vendor-summary를 절대호스트 `wing.coupang.com`으로 부르면 브라우저 **cross-origin CORS 차단(Failed to fetch)**. 반드시 **`location.origin`(=m-wing) + 경로**로 same-origin 호출해야 200. (ref18 cURL은 requests라 호스트 직타 통했으나 브라우저는 다름.) XSRF-TOKEN 쿠키→`x-xsrf-token` 더블서브밋.
- **자동대조는 닫힌 과거일만**(D-3, 당일은 sync 시차로 부정확).
- **시스템은 사실/지표/드리프트만**(D-2, 전략 추천 금지).
- **운영주의**: 페처/데몬 코드 변경 후 `launchctl kickstart -k`로 재시작=배포(미재시작=stale, 광고 페처 교훈).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `tools/wing_browser_fetcher.py` | ★S1 결과물. Wing vendor-summary 헤드풀 페처(모바일 UA, m-wing origin fetch, storage_state) |
| `tools/ad_cost_browser_fetcher.py` | 복제 원본. push(_push)·poll 데몬(cmd_poll)·SSO재발급·launchd plist 패턴 — S2 push/데몬 참고 |
| `docs/tracks/active/track_wing-session-automation.md` | ★단일 진실 원천(D-1~D-6, 체크리스트 2/6, §7 다음액션) |
| `docs/PLAN_wing-session-automation.md` | S1~S4 계획서(What/Why/완료기준) |
| `docs/references/18_coupang_wing_business_insight_vendor_summary.md` | vendor-summary API 스펙·응답형태·ref값 |
| `backend/app/services/coupang/ad_cost_sync.py` | ingest 패턴(S2 vendor-summary ingest 참고) |
| `backend/app/services/coupang/intelligence.py` | command-center revenue_3p/revenue_rg(S2 reconcile 대조 대상) |
| `backend/app/clients/coupang/inbound.py` | `_UA`(모바일 iPhone), `parse_curl_cookies`, WingAuthError |
| `~/.ohisell_wing_state.json` | S1에서 저장된 Wing 세션(0600). run이 재로드. 만료 시 재login 필요 |

## 5. 알려진 이슈 / 주의사항
- **세션 만료→회복 경로 미관측**: S1은 fresh 로그인 상태라 만료를 강제 못함. 현재 `_do_run` 폴백=만료 시 대시보드 재진입 1회→실패 시 수동 로그인 대기(headful). Wing이 광고처럼 keycloak 무재로그인 재발급을 지원하는지는 **S2 데몬 수명 중 라이브 실측**(추정 금지). cf_clearance 단명이라 자주 만료될 수 있음.
- **헤드풀 창 충돌**: 페처 재실행 시 이전 playwright chromium 잔여가 남으면 새 창이 7초 만에 닫힘. 재실행 전 `pkill -f "ms-playwright/chromium"`로 정리. (이번 세션 1회 발생.)
- **로그인은 Jino 수동 필요**: vendor-summary는 셀러 세션쿠키 인증. 헤드풀 창에서 Jino가 wing.coupang.com 로그인해야 함(모바일 형태로 보임=정상). 자동 감지(vendor-summary 200)되면 창 자동 종료.
- WING1 광고 전용·WING2 광고 0. 매출분석(vendor-summary)도 로그인한 계정 기준(WING1=오픽스).
- gstack 업그레이드 알림 떴음(1.56→1.58) — 작업과 무관해 건너뜀.

## 6. 다음에 할 작업 (미완료 — S2)
- [ ] **S2-a 백엔드 vendor-summary ingest+store SA**: push 수신 라우터+스냅샷 저장 모델(alembic, head l6m7n8o9p0q1 위에). 날짜별 3P(NORMAL)/RG(RFM) GMV.
- [ ] **S2-b revenue_reconcile Harness**: 우리 revenue_3p/revenue_rg(intelligence.command-center) vs 쿠팡 GMV 닫힌일 드리프트%=(우리−쿠팡)/쿠팡. ref18 수동값(6/8~6/13 3P+1.8%·RG+7.4%)과 자동산출 일치 self-verify. **net_profit 등 기존 값 불변(읽기전용 추가)**.
- [ ] **S2-c 페처 prod push 배선 + launchd 데몬**: `tools/wing_browser_fetcher.py`에 _push(광고 패턴) + cmd_poll 추가, plist `com.ohisell.wing` 등록(광고 plist 복제). 머니/비율 fixture 테스트 + codex pass + prod 라이브 self-verify.
- [ ] S3 검산 패널 UI 컬럼("쿠팡 공식 GMV + 드리프트%"). S4 RG정산 자동수집(S6-auto) 흡수.
- (선택·기존 트랙) RG수수료 S8 size_mismatch_high 4건 / RG발송관제 S7 요일·휴일 세분화.
- 모델: S2는 백엔드 통합(스키마+ingest+harness) → Sonnet 권장.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ohisell-wing-session-automation-S1_20260614.md 읽고 이어서 작업해줘
