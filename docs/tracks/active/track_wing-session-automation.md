# 트랙: Wing 세션 자동화 (Wing Session Automation)

> 생성 2026-06-14. 단일 진실 원천(Layer 1). 결정 발생 즉시 갱신.
> ⚠️ **상태: 구조 설계 스캐폴딩(코딩 전). 다음 세션에서 구조 승인 → Opus 계획 → 구현.**
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

### 사용자 원문 인용 (왜곡 방지)
- "C로 하면 안되는 이유는 뭐야?" → C는 기술 장벽 없음, 단 스프린트 규모·별도 트랙 감이라 정합성 트랙과 분리.
- "너의 제안대로 가자" → 정합성 트랙 B 마감 + 본 트랙 스캐폴딩 승인.
- "그래" (2026-06-14) → S0 구조 검토안(런타임 경계 명시 D-4 + vendor_summary 브라우저측 fetch·별도 데몬 D-5) 승인.

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

## 5. 체크리스트 (2.5/6 — S2 코드 완료·prod 라이브 검증만 남음)
- [x] **S0 구조 승인(Jino) + Opus 계획서** — 구조 확정(D-4/D-5), 계획서 `docs/PLAN_wing-session-automation.md`
- [x] **S1 WingBrowserFetcher(헤드풀 세션) — 라이브 검증 완료** (커밋 eae19d9). login/run 동작, vendor-summary 200·3P/RG 파싱이 ref 18과 원단위 일치(D-6). codex review PASS.
- [~] **S2 — 코드 완료·codex PASS·183 테스트 그린, prod 라이브 self-verify만 남음(Jino 손 필요)**:
  - [x] 백엔드 ingest+store SA `vendor_summary_sync.py`(snapshot upsert·3P/RG 합계·heartbeat/refresh) + 모델 `CoupangVendorSummaryDaily` + alembic `m7n8o9p0q1r2`(head).
  - [x] `revenue_reconcile.py` Harness(닫힌일 드리프트%, 커버리지·권위 D-7, **net_profit 불변·읽기전용**). 라우터: ingest(토큰)·request-refresh·refresh-status·refresh-claim(토큰) `/api/coupang/ops/wing/vendor-summary/*` + 조회 `GET /api/overview/revenue-reconcile`.
  - [x] 페처 `wing_browser_fetcher.py` prod push 배선(`_push`·`_push_configured` 게이트)·units 캡처·`cmd_poll` 데몬·plist `tools/com.ohisell.wing.plist`. codex PASS(지적0).
  - [x] 테스트: `test_vendor_summary_reconcile.py`(11, 머니/드리프트/커버리지) + `test_vendor_summary_http.py`(5, HTTP 라운드트립). codex 대화 3R 합의(P1 2건 수정·P2 2건 근거기각·account_key 검증 추가).
  - [ ] **prod 라이브 self-verify(원칙22 게이트)**: 백엔드 prod 배포(alembic upgrade+PM2 재시작) → 페처 설정(~/.ohisell_wing_fetcher.json) → 헤드풀 Wing 로그인(Jino) → 데몬 등록(com.ohisell.wing) → 갱신 트리거→push→reconcile가 ref18 드리프트(3P+1.8%·RG+7.4%) 재현.
- [ ] S3 검산 패널 UI에 "쿠팡 공식 GMV + 드리프트%" 컬럼 (request-refresh 버튼은 백엔드 준비됨)
- [ ] S4 RG정산 자동수집(S6-auto) 흡수
- [ ] 각 Sprint: codex 교차검증 + prod 라이브 self-verify(원칙22)

## 6. 현재 진행 단계
- 2026-06-14 S2: **코드 전부 완료·codex PASS·183 테스트 그린(로컬).** 백엔드(모델+alembic+ingest SA+reconcile Harness+라우터) + 페처(push+poll 데몬+plist). reconcile은 읽기전용(net_profit 불변, 테스트로 입증). HTTP 라운드트립(TestClient 격리DB)으로 토큰401·account_key400·ingest저장·reconcile read-back(official GMV·complete·Decimal직렬화)·refresh트리거/claim 검증.
- **남은 게이트 = prod 라이브 self-verify(원칙22, Jino 손 필요)**: ① 백엔드를 prod(sellc.ohitech.co.kr)에 배포(rsync/scp + `alembic upgrade head` + PM2 `ohisell-backend` 재시작) ② Mac 페처 설정 `~/.ohisell_wing_fetcher.json`에 `{account_key:"COUPANG_WING1", prod_base_url:"https://sellc.ohitech.co.kr", ingest_token:<AD_INGEST_TOKEN>}` ③ 헤드풀 Wing 로그인(`wing_browser_fetcher.py login`) ④ 데몬 설치(`cp tools/com.ohisell.wing.plist ~/Library/LaunchAgents/` → load) ⑤ request-refresh→push→`GET /api/overview/revenue-reconcile?account=COUPANG_WING1`이 ref18 드리프트 재현 확인.
- **미관측**(S1과 동일): 세션 만료→회복 경로(데몬 수명 중 실측). `_do_run`/`cmd_poll`은 만료 시 헤드풀 로그인 대기 폴백(광고 패턴 차용).

## 7. 다음 액션
- **prod 배포 + 라이브 self-verify**(위 §6 게이트 ①~⑤) → 확인되면 S2 완료(3/6) 마킹. 코드는 아직 미커밋(스테이징됨) — 배포 시 커밋.
- 이후 S3: 종합조망 검산 패널에 "쿠팡 공식 GMV(3P/RG)+드리프트%" 컬럼 + '판매분석 갱신' 버튼(→ POST request-refresh, 백엔드 이미 준비). 임계 초과 시각 강조(사실 표시만, D-2).
- 참고: ref 18(vendor-summary), `tools/wing_browser_fetcher.py`(페처·push·poll), `tools/com.ohisell.wing.plist`(데몬), `backend/app/services/coupang/{vendor_summary_sync,revenue_reconcile}.py`, `backend/app/routers/{coupang_ops,overview}.py`.
