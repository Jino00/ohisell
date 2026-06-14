# 트랙: Wing 세션 자동화 (Wing Session Automation)

> 생성 2026-06-14. 단일 진실 원천(Layer 1). 결정 발생 즉시 갱신.
> **상태: 4/6 — S0~S3 완료(백엔드 파이프라인 + 종합조망 검산 UI), prod 라이브 검증. 다음=S4 RG정산 자동수집 흡수.**
> 상위 컨텍스트: 정합성 트랙(completed) 자동대조 + RG정산 자동수집이 공통으로 막힌 "Wing 세션 freshness"를 한 번에 해결.

## 1. 목표 (왜 존재하는가)
`wing.coupang.com` 내부 API를 **세션 만료 없이 자동 호출**할 수 있는 공용 인프라(헤드풀 브라우저 페처)를 만든다. 이게 풀리면 두 기능이 동시에 열린다:
- **(A) 매출 자동 대조**: `vendor-summary`(ref 18)로 쿠팡 공식 3P/RG GMV를 당겨 우리 revenue와 드리프트% 자동 감지(정합성 트랙 잔여).
- **(B) RG 정산 자동 수집**: RG 수수료 회계 트랙 S6-auto(현재 수동 cURL 의존)를 자동화.

## 2. 핵심 문제 (왜 어려운가)
- `wing.coupang.com`은 **Cloudflare(`cf_clearance`, IP+UA 바인딩) + Akamai(`_abck`,`bm_*`) + AWS ALB** 다중 봇 방어. 쿠키 단명.
- `requests`/curl 쿠키 재생은 **1회용**(cf_clearance 갱신 불가) → 실제 브라우저가 주기적으로 챌린지를 풀어 세션을 살려둬야 함.
- **이미 검증된 해법**: 광고 페처 `tools/ad_cost_browser_fetcher.py`가 advertising.coupang.com에서 정확히 이 패턴(headful Playwright + storage_state + keycloak SSO 재발급 + launchd poll 데몬 + 버튼 트리거)으로 해결 중. **Wing판으로 복제**가 출발점.

## 3. 확정 결정사항 (번복 금지)
- **D-1**: 광고 페처의 headful Playwright + launchd poll 데몬 패턴을 재사용(재발명 금지). 모바일 UA 필수(ref 18).
- **D-2**: 시스템은 사실·지표·드리프트만 정리(전략 추천 금지) — 종합조망 불변 원칙 계승([[no-ad-strategy-recommendations]]).
- **D-3**: 자동대조는 **닫힌 과거일** 기준으로만 비교(당일은 sync 시차로 부정확, ref 18 실측).
- **D-4 (런타임 경계, 2026-06-14 S0 확정)**: Wing 페처는 광고 페처처럼 **Mac 로컬 헤드풀 상주 프로세스**(`tools/`, residential IP). 백엔드 Harness는 이걸 함수로 직접 호출하지 않고 **push된 데이터를 ingest해서 읽기만** 한다. 도표는 두 런타임(Mac 페처 ↔ 백엔드 ingest/reconcile)을 명시적으로 분리한다.
- **D-5 (vendor_summary 위치 + 별도 데몬, 2026-06-14 S0 확정)**: cf_clearance는 재생 불가이므로 `vendor-summary`/RG XLSX는 **백엔드 `requests` client가 아니라 살아있는 브라우저 세션 안에서 fetch/download**(광고비 `report/SALES` 패턴) → prod push. 백엔드는 ingest+store SA로 수신. **Wing 페처는 별도 launchd 데몬 `com.ohisell.wing`**(별도 storage_state) — 광고(advertising.coupang.com·aid/keycloak)와 Wing(wing.coupang.com·cf_clearance)은 도메인·세션·방어체계가 달라 한 프로세스에 묶으면 취약 세션끼리 상호 파손. RG는 기존 업로드 ingest를 재사용(다운로드만 Mac 페처가 대행, 백엔드 변경 최소).
- **D-6 (★브라우저는 m-wing.coupang.com origin으로 호출, 2026-06-14 S1 라이브 실측)**: 모바일 UA(필수, ref 18)로 로그인하면 Wing이 **`m-wing.coupang.com`(모바일 호스트)로 라우팅**한다. vendor-summary를 절대 호스트 `wing.coupang.com`으로 부르면 브라우저에서 **cross-origin CORS 차단("Failed to fetch")**. 반드시 **`location.origin`(=m-wing) + 경로**로 same-origin 호출해야 200. (ref 18의 cURL은 requests라 호스트 직타가 통했지만 브라우저는 다름.) XSRF-TOKEN 쿠키→`x-xsrf-token` 더블서브밋. **교차검증(원칙22)**: 페처 6/8~6/13 GMV(3P 1,693,230·RG 1,786,500)가 ref 18 수동 cURL과 원 단위 일치.
- **D-7 (reconcile 완전성·권위 의미론, 2026-06-14 S2 codex 대화)**: 드리프트는 **닫힌 윈도우 전 날짜가 적재됐을 때만 권위값**이다. ① 커버리지는 **날짜 그레인**으로 판정(`complete = days_with_data >= expected_days`). 등록유형(NORMAL/RFM)별 결측은 갭으로 보지 않음 — 쿠팡이 '그날 0 판매' 유형의 행을 생략하므로 진짜 0과 모호(codex 합의). ② **집계(`account=None`) 뷰는 절대 권위값 아님(`complete=False` 고정)** — `ours`는 전 계정 매출 합인데 `official`은 vendor-summary 적재된 계정만 합산하므로, 매출은 있는데 official 없는 계정을 날짜 수로 못 잡음(codex P1 round2). 정합 판정은 **계정 지정(COUPANG_WING1/2) 대조로만**. 부분/집계 시 드리프트는 계산은 하되 "참고치"로 명시. ③ ingest account_key는 {WING1,WING2}로 검증(오타가 집계 official 오염하는 사각 차단, codex P2).

- **D-8 (S4 RG정산 자동수집 구조, 2026-06-14 승인)**: RG 정산 엑셀 자동 다운로드는 **살아있는 브라우저 세션**(`wing_browser_fetcher.py`)으로 수행(D-5 동일). 백엔드 requests-client(`auto_download_and_ingest`)는 cf_clearance 재생 불가라 **미사용**(Mac 페처가 대행).
  - 흐름: ① status/api(정산주기 목록) → ② request-download/api → ③ download-list/api 폴링(COMPLETED) → ④ download/api/v2(S3 url) → ⑤ Mac `requests.get(S3, 무인증·24h)` → ⑥ POST 기존 `/api/coupang/ops/rg/settlement/upload-xlsx`(S6-core ingest 재사용, 백엔드 무변경). ①~④는 `location.origin+경로` same-origin fetch(D-6 패턴).
  - **API 양식·응답 라이브 캡처 검증 완료**(2026-06-14, 오픽스 WING1 DevTools 3요청+응답 전수). 코드(`clients/coupang/rg_settlement.py`) body·필드명과 정확 일치 = 기존 "HTTP 500 블로커"는 stale 라벨. 상세 ref17 §8-2.
  - **트리거**: ⓐ 새벽 예약(Mac 켜둠 전제, 기존 데몬 `com.ohisell.wing`) + ⓑ 온디맨드 새로고침 플래그(버튼, vendor-summary 패턴 복제). **VM 미사용** — 클라우드 데이터센터 IP는 Cloudflare cf_clearance(IP 바인딩) 하드차단 위험·검증 불가(원칙22). prod는 항상 켜진 *열람* 서버일 뿐 fetch는 residential Mac만.
  - **범위**: WAREHOUSING_SHIPPING 1종 end-to-end(다운→push→적재→검산) 우선, 나머지 7종 코드명 확보 후 확장.
  - **미검증·최우선 de-risk(원칙22)**: Playwright 페처(판매분석은 m-wing 착지)가 정산 페이지에서 어느 origin에 착지·same-origin fetch 200 나오는지는 구현 시 라이브 실측.

### 사용자 원문 인용 (왜곡 방지)
- "C로 하면 안되는 이유는 뭐야?" → C는 기술 장벽 없음, 단 스프린트 규모·별도 트랙 감이라 정합성 트랙과 분리.
- "너의 제안대로 가자" → 정합성 트랙 B 마감 + 본 트랙 스캐폴딩 승인.
- "그래" (2026-06-14) → S0 구조 검토안(런타임 경계 명시 D-4 + vendor_summary 브라우저측 fetch·별도 데몬 D-5) 승인.
- "내가 보고 싶을때마다 업데이트되게... 주기적으로는 VM에서 돌려서 출근할때 보는게" → "그래, 그렇게 하자" (2026-06-14) → D-8 S4 구조(Mac 예약+온디맨드 버튼, VM 안 씀) 승인.

## 4. 확정 구조 (2026-06-14 S0 승인, 레고 계층 — 런타임 2개 분리)
```
┌─ [Mac 로컬·헤드풀·residential IP]  tools/wing_browser_fetcher.py  (광고페처 복제)
│   launchd: com.ohisell.wing  (별도 데몬·별도 storage_state, D-5)
│   SA: WingBrowserFetcher — cf_clearance 세션 유지 + SSO 재발급(login/run/poll 골격)
│     ├─ 브라우저측 fetch: vendor-summary (ref 18, 모바일 UA)   → prod push
│     └─ 브라우저측 download: RG 정산 XLSX                      → prod push(기존 ingest)
│   (기존 재사용: parse_curl_cookies[inbound.py], coupang_wing_cookie store[Fernet] — 최초 로그인/쿠키 부트스트랩)
│
└─ [백엔드 prod]  backend/app/...
    Harness: revenue_reconcile (매출 대조)
      ├ SA: vendor_summary ingest+store  ← 신규(push 수신·스냅샷 저장)
      ├ SA: compute_command_center (기존, revenue_3p/rg)
      └ → 닫힌일 드리프트% 산출(D-3) → 검산패널 노출
    Harness: rg_settlement_auto = 기존 업로드 ingest 재사용(S6-auto, 백엔드 변경 최소)
```
- 데이터 흐름: WingBrowserFetcher가 세션 유지 → 브라우저측에서 vendor-summary fetch / RG XLSX download → **prod push** → 백엔드 ingest 저장 → revenue_reconcile이 우리 값과 비교 → 검산 패널에 "쿠팡 공식 GMV + 드리프트%" 노출.
- **계층 배치 주의(D-4/D-5)**: vendor_summary는 백엔드 `requests` client가 아님(cf_clearance 재생 불가). 호출은 Mac 페처 브라우저측, 백엔드는 ingest 수신만.

## 5. 체크리스트 (5/6 — S4 완료, CDP 모드 전환 + RG 자동수집 prod 라이브 검증)
- [x] **S0 구조 승인(Jino) + Opus 계획서** — 구조 확정(D-4/D-5), 계획서 `docs/PLAN_wing-session-automation.md`
- [x] **S1 WingBrowserFetcher(헤드풀 세션) — 라이브 검증 완료** (커밋 eae19d9). login/run 동작, vendor-summary 200·3P/RG 파싱이 ref 18과 원단위 일치(D-6). codex review PASS.
- [x] **S2 — 완료·codex PASS·183 테스트 그린·prod 라이브 self-verify 완료** (커밋 e2c2560):
  - 백엔드 ingest+store SA `vendor_summary_sync.py` + 모델 `CoupangVendorSummaryDaily` + alembic `m7n8o9p0q1r2`(head) + `revenue_reconcile.py` Harness(닫힌일 드리프트%, D-7 커버리지·권위, **net_profit 불변·읽기전용**) + 라우터(`/api/coupang/ops/wing/vendor-summary/*` ingest·refresh 3종, `GET /api/overview/revenue-reconcile`).
  - 페처 `wing_browser_fetcher.py` push 배선(`_push`·게이트)·units 캡처·`cmd_poll` 데몬·plist `tools/com.ohisell.wing.plist`. codex PASS(지적0).
  - 테스트 16(reconcile 11 + http 5). codex 대화 3R 합의(P1 2건 수정·P2 2건 근거기각·account_key 검증).
  - **★prod 라이브 self-verify(원칙22, 2026-06-14)**: prod DB 백업→백엔드 6파일 scp→`alembic upgrade head`(l6m7n8o9p0q1→m7n8o9p0q1r2, 테이블 생성)→PM2 재시작(#120). 페처(S1 세션 유효) run→**push 14행(7일×2유형)**→prod ingest. **`GET /revenue-reconcile?from=2026-06-08&to=2026-06-13&account=COUPANG_WING1`: official 3P 1,693,230·RG 1,786,500(=ref18 원단위), ours 3P 1,724,230·RG 1,918,700(=ref18), 드리프트 +1.83%·+7.40%(=ref18 +1.8%·+7.4%), coverage complete=true.** command-center net_profit 불변(2,294,339 반환·읽기전용 확인). 데몬 com.ohisell.wing 설치·로드→request-refresh→**자동 claim→headful fetch→push 사이클 라이브 동작**(last_success 18:28→18:29 진행, status green).
- [x] **S3 검산 패널 UI — 완료·codex 2R PASS·build green·prod 라이브 브라우저 검증** (커밋 d047d84):
  - 프론트 `frontend/src/lib/api.ts`(fetchRevenueReconcile + requestWingVendorSummaryRefresh/getWingVendorSummaryRefreshStatus + 타입) + `frontend/src/pages/CommandCenter.tsx`(회계축 `RevenueDriftCard`: 쿠팡 공식 GMV 3P/RG + 드리프트% 테이블 + D-7 참고치/권위값 라벨 + D-2 임계 색상[<5% 회색·5~10% 주황·≥10% 빨강, 추천 없음] + '판매분석 갱신' 버튼[광고 패턴 복제]).
  - codex 대화 2R: P1 2건 수정(① doFetch 시작 시 `setReconcile(null)`로 이전 계정 드리프트 잔상 제거 ② `selRef`로 갱신완료 후 stale 클로저 인자 회피 — 둘 다 line66 reqSeq 가드와 같은 검산 surface 정합성 원칙). round2 findings none.
  - **★prod 라이브 self-verify(원칙22, 2026-06-14)**: `npm run build`(dist index-Wu_C9ezR.js)→`rsync -az --delete frontend/dist/ → prod nginx`. prod 서빙 해시 일치 확인. browse로 `/command-center` 오픽스(WING1) 선택→**RevenueDriftCard 라이브 렌더**: 권위값 라벨·닫힌 과거일 6/8~6/13·적재 6/6일·3P 우리 1,724,230/공식 1,693,230/+1.83%·RG 1,918,700/1,786,500/+7.40%·합계 3,642,930/3,479,730/+4.69%(=ref18 원단위), 콘솔 에러 0. reconcile API 라이브 official complete=true.
- [x] **S4 RG정산 자동수집(S6-auto) 흡수 — 완료·prod 라이브 self-verify** (커밋 509a075+9037817):
  - [x] S4-P0 **CDP 모드 전환(Akamai 영구 우회)** (커밋 9037817): `_chrome()` 컨텍스트 매니저(cdp_port 설정 시 실제 Chrome `connect_over_cdp`, 미설정 시 레거시 Playwright 하위호환) + `cmd_chrome`(전용 프로필 Chrome 실행) + `_save_state cdp=True` no-op + `_login_wait_loop/_rg_login_wait` cdp 파라미터 전파. **★Akamai Access Denied 문제 근본 해결** — Playwright Chromium 핑거프린트 대신 실제 Chrome 세션 재사용. 설정 `cdp_port=9222·cdp_profile=~/.ohisell_wing_chrome`.
  - [x] S4-P1 페처 RG 다운로드 흐름 이식(status/api 주기열거→request-download→download-list 폴링→download/api/v2→S3 GET→기존 `/rg/settlement/upload-xlsx` push) + `rg` CLI. **★prod 라이브 self-verify(원칙22)**: 정산주기 `A01564720-2026-06-08-2026-06-14` → push upserted=10·입출고비 120,375·배송비 206,075·검산 diff=0.
  - [x] S4-P2 트리거 **코드 완료·191 테스트 그린·codex 3R PASS**: RG 새로고침 플래그 + 라우터 3종 + 데몬 RG 분기 + upload-xlsx 토큰 인증.
  - [x] S4-P3 prod 배포 + self-verify: `upload-xlsx` 무토큰→**401** ✅, `rg-settlement/refresh-status`→**200** ✅. 데몬 복원·일일예약 자동 트리거 확인.
- [ ] 각 Sprint: codex 교차검증 + prod 라이브 self-verify(원칙22)

## 6. 현재 진행 단계
- 2026-06-14 **S4 완료(5/6)** — CDP 모드 전환(Akamai 영구 우회) + RG 정산 자동수집 prod 라이브 self-verify 완료. 커밋 509a075(S4-P1/P2) + 9037817(CDP). 데몬 com.ohisell.wing 복원·일일예약 자동 트리거 동작 확인.
- 2026-06-14 **S4-P2 완료(코드+191테스트+codex 3R PASS)·S4-P1 코드완료/라이브 실측만 보류**:
  - S4-P2: 백엔드 RG 새로고침 플래그(`rg_settlement_sync` 끝 `rg_request/refresh_status/claim_refresh`+`rg_mark_heartbeat`, 상태행 `COUPANG_WING_RG`) + 라우터 `/api/coupang/ops/wing/rg-settlement/{request-refresh,refresh-status,refresh-claim}` + 데몬 `cmd_poll` RG 분기(온디맨드 claim + 새벽 일일예약) + `upload-xlsx` **X-Ingest-Token 인증 추가**(회계 보호, 프론트 무영향). 페처 세션감지=정산 `status/api`(`_rg_session_ok`/`_rg_login_wait`).
  - **codex 대화 3R PASS(원칙19)**: R1 P1 2건(데몬 RG 미소비·dup 오기간 업로드) 수정, R2 P1 1건(account_key 명시) 수정+P2 1건(claim-before-success) 근거수용, R3 클린. 191 테스트 그린(신규 RG 8: 서비스 6+HTTP 2).
  - **남은 것**: P1 라이브 실측(정산 페이지 same-origin 200·다운로드·적재) + prod 배포 + self-verify — 전부 **Akamai-free 로그인 창 필요**(이상적 내일 아침). **코드 미커밋·미배포**.
- 2026-06-14 **S4-P1 코드 작성 완료·라이브 실측 보류(Akamai 일시 차단)**:
  - `tools/wing_browser_fetcher.py`에 RG 정산 다운로드 흐름 추가(컴파일 OK): `_POST_JSON_JS`(VS_FETCH_JS 일반화·공용) + `cmd_rg`/`_do_rg_run` + `_rg_enumerate_group_keys`(status/api 주기열거)·`_rg_download_one`(request→list 폴링→v2, **매칭키=requestTime**)·`_rg_push_xlsx`(S3 GET→기존 `/rg/settlement/upload-xlsx` push). 설정에 `vendor_id`(WING1=A01564720)·`rg_*` 추가. `python wing_browser_fetcher.py rg` 1회 실행.
  - **API 양식·응답 100% 라이브 검증**(ref17 §8-2): 3요청+응답 전부 캡처, 기존 client 코드와 일치(블로커=stale 라벨이었음).
  - ⛔ **라이브 de-risk 미완**: 페처 세션 만료→`login` 재시도 시 **Akamai Access Denied**(xauth.coupang.com keycloak, errors.edgesuite.net). 원인=수분 내 반복 시도로 봇 의심도↑ 일시차단(당일 18:29엔 로그인 성공했었음=방식은 유효). **쿨다운 후 재시도**(이상적: 내일 아침, "출근 시 최신" 시나리오와 합치). failures.jsonl 기록.
  - ⚠️ **헤드풀 충돌**: 데몬(com.ohisell.wing) 실행 중 수동 login 창이 7초 만에 닫힘 → 라이브 로그인 전 `launchctl bootout gui/$(id -u)/com.ohisell.wing`로 데몬 임시 중지 필요. **현재 데몬 bootout 상태(중지됨)** — 다음 클린 로그인 성공 후 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ohisell.wing.plist`로 복원할 것.
  - prod 정상(refresh-status 200·reconcile 데이터 유지). 코드 **미커밋**(라이브 검증 후 커밋·codex).
- 2026-06-14 **S3 완료(4/6)** — 프론트 RevenueDriftCard 코드+codex 2R PASS+prod 라이브 브라우저 검증 통과. 매출 자동 대조가 **종합조망 회계축 UI로 노출**: 사용자가 오픽스/오하이테크 선택 → 쿠팡 공식 GMV(3P/RG)·드리프트%·권위값/참고치 라벨을 한 화면에서 봄. '판매분석 갱신' 버튼이 com.ohisell.wing 데몬을 깨워 즉시 최신화.
- 2026-06-14 S2 완료(3/6) — 백엔드 파이프라인: Wing 페처(헤드풀) → request-refresh → com.ohisell.wing 데몬 claim → headful fetch → prod push → ingest → revenue_reconcile(닫힌일 드리프트%, net_profit 불변).
- 운영 상태: prod 백엔드 #120, 프론트 dist=index-Wu_C9ezR.js(rsync 배포·nginx 서빙), 데몬 com.ohisell.wing 로드(15s 폴, 창은 요청 시만). 페처 설정 `~/.ohisell_wing_fetcher.json`. prod DB 백업=`ohisell.db.backup_wingS2_20260614_092642`.
- **미관측**(S1과 동일): 세션 만료→회복 경로(데몬 수명 중 실측). cf_clearance 단명 → 만료 시 headful 로그인 대기 폴백. UI '갱신' 버튼의 실제 데몬 round-trip(215s 폴링)은 라이브 클릭 미실측(reconcile API·렌더는 라이브 확인).

## 7. 다음 액션
- **S5**: 나머지 7종 sellerReportType 코드명 수집 → `CONFIRMED_SELLER_REPORT_TYPES` 확장 + RG 새로고침 UI 버튼(RevenueDriftCard) 추가.
- **S6**: (선택) git origin push + RG수수료 S8 size_mismatch_high 4건 + RG발송관제 S7.
- CDP 모드 주의: Wing Chrome(`cmd_chrome`)이 켜져 있어야 데몬이 CDP 연결 가능. Mac 재부팅 시 `python3 tools/wing_browser_fetcher.py chrome` 먼저 실행 후 `login`.
- 참고: `tools/wing_browser_fetcher.py`·`tools/com.ohisell.wing.plist`, `~/.ohisell_wing_fetcher.json`(cdp_port/cdp_profile), `backend/app/services/coupang/{vendor_summary_sync,revenue_reconcile,rg_settlement_sync}.py`, `backend/app/routers/coupang_ops.py`.
