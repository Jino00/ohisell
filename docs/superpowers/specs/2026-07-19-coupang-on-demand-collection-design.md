# 쿠팡 데이터 수집: 순수 On-Demand 전환 + 전역 신선도 배너

- 날짜: 2026-07-19 (KST)
- 브랜치: `claude/sellc-on-demand-data-df77b1`
- 트랙: 활성 트랙(naver-ad-optimization) **외** 별도 스레드 (Jino 승인 하에 진행)
- 상태: 설계 승인 대기

---

## 1. 배경 / 문제

Jino의 요청: "쿠팡 데이터(ohitech 로켓 광고, supplier hub, ofix 매출·광고비)를 스케줄로 가져오지 말고, 내가 sellC에서 버튼 누를 때만 가져오면 더 안정적이지 않을까? 지금은 시도때도없이 창이 뜨고, 로그인이 안 돼서 깨지는 경우도 많다."

### 현재 상태 (실측)
버튼 트리거 모델은 **이미 라이브로 존재**한다. 4개 브라우저 스트림 각각 "갱신" 버튼 → `request-refresh` 플래그 → Mac 상주 데몬이 claim+헤드풀 fetch+prod push. 남은 문제는 **창을 스스로 띄우는 자동 트리거 3개**와, **낡음/실패가 눈에 안 보이는 것**이다.

두 종류의 수집 계층:
- **Open API 계층** (HMAC 키, 브라우저 없음, 창 안 뜸): 05~06시 배치 + rg_orders 2시간 주기. **문제 없음 → 유지.**
- **브라우저/CDP 계층** (Playwright + 실 Chrome, 창 뜸, 세션 깨짐): 본 설계 대상.

### 근본 원인 분리 (설계 전제)
- **창이 저절로 뜸** ← *스케줄*이 원인. 자동 트리거 제거로 해결 가능.
- **로그인 깨짐 / 데이터 깨짐** ← *브라우저 자동화 자체*(Cloudflare/Akamai 쿠키 1회용)가 원인. 버튼으로 **해결 불가** — 다만 "실패를 눈에 보이게" 만들어 대응 속도를 높인다.

---

## 2. 목표 / 비목표

**목표**
1. 브라우저 창은 **오직 버튼 누를 때만** 뜬다 (자동 트리거 0).
2. 안 눌러서 낡거나(stale) 눌렀는데 실패한(login broke) 상태를 **전역 배너로 항상 가시화**한다.

**비목표**
- 로그인 깨짐 자체를 없애는 것 (브라우저 자동화 한계 — 별건).
- Open API 계층 스케줄 변경.
- ~~데몬/`*-chrome` supervisor plist 제거 (버튼 성공률의 핵심 — 유지).~~
  → **2026-07-27 개정으로 폐기됨**(supervisor가 "창이 되살아나는" 범인). 아래 「2026-07-27 개정」 절 참조.
  poll 데몬 4개는 그대로 유지.

---

## 3. 설계

### 3-1. 제거 — 자동 트리거 3개 (subtractive)

| # | 위치 | 제거 대상 |
|---|---|---|
| 1 | `backend/app/services/scheduler_service.py` (`request_ad_cost_refresh` 잡 `:934`, 크론 테이블 `:1160` 부근) | ofix 광고비 자동 갱신 요청 크론 `0 3,10-20 * * *` |
| 2 | `tools/rocket_supplier_fetcher.py` `cmd_poll` (`:729` 부근) | ">23h stale → 자동 fetch" 안전망 |
| 3 | `tools/ohitech_ad_fetcher.py` `cmd_poll` (`:532` 부근) | ">23h stale → 자동 fetch" 안전망 |

제거 후 데몬 `cmd_poll`은 **오직 refresh 플래그(버튼)만** 소비한다. 폴 주기는 그대로 둔다(버튼 반응 지연 방지).

### 3-2. 유지 — 손대지 않음
- 4개 갱신 버튼 + 4개 상주 poll 데몬 (버튼 전달 수단)
- Open API 크론 전부 (창 없음)
- ~~`com.ohisell.wing-chrome`(9222) · `com.ohisell.ohitech-chrome`(9224) supervisor plist — Chrome 세션 유지 = 로그인 재깨짐 방지~~
  → **2026-07-27 개정으로 삭제**(per-fetch 기동·종료로 대체).

### 3-3. 신규 — 집계 상태 엔드포인트 (Sub-Agent, 단일 책임)

**신규** `GET /api/coupang/ops/collection-status`
- 4개 스트림의 상태를 한 번에 반환. 기존 스트림별 refresh-status 로직 재사용(중복 구현 금지).
- 각 스트림 필드: `key`, `label`, `last_success_at`, `last_error_at`, `refresh_requested_at`(in-flight 여부), 파생 `age_hours`, 파생 `state`.
- `state` 판정 (서버에서 계산, 프론트는 표시만):
  - `in_flight` — `refresh_requested_at` 있고 아직 소비 전 (갱신 진행 중)
  - `failed` — `last_error_at > last_success_at` (눌렀는데 로그인/fetch 실패)
  - `critical` — age > 48h
  - `warn` — 24h < age ≤ 48h
  - `fresh` — age ≤ 24h
- 임계 상수: `WARN_HOURS=24`, `CRIT_HOURS=48` (스트림 공통).

**⚠️ KST/UTC 함정 (프로젝트 기지식 [[sqlite-server-default-now-is-utc]])**: `last_success_at` 등이 `server_default=func.now()`면 UTC로 저장됨. age 계산은 반드시 **timezone-aware**로, 저장 tz를 확인해 KST 기준 현재시각과 비교. 이 부분 테스트로 못박는다.

대상 스트림 4개:
| key | label | 근거 엔드포인트(기존) |
|---|---|---|
| `ofix_sales` | ofix 판매분석 | `coupang_ops.py:1739` |
| `ofix_ad` | ofix 광고비 | `coupang_ops.py:1616` |
| `ohitech_ad` | ohitech 로켓 광고 | `coupang_ops.py:1511` |
| `supplier_hub` | 로켓 발주/정산 | `coupang_ops.py:1456` |

### 3-4. 신규 — 전역 신선도 배너 (Harness+표시)

**신규 컴포넌트** `frontend/src/components/CollectionFreshnessBanner.tsx`, 전역 `Layout.tsx`에 마운트.
- `collection-status`를 폴링(60s). 하나라도 `warn|critical|failed`면 상단 배너 표시. 전부 `fresh|in_flight`면 숨김.
- 색: 최악 상태 기준 — `warn`만 있으면 🟡, `critical`/`failed` 하나라도 있으면 🔴.
- 내용: 문제 스트림을 두 종류로 **구분 표기**
  - 낡음: "ofix 광고비 2일 지남", "로켓 발주 1일 지남"
  - 실패: "로켓 갱신 실패 · 로그인 필요"
- 각 항목 클릭 → 종합조망(CommandCenter)의 해당 갱신 버튼으로 점프(anchor/scroll).
- **fail-safe**: `collection-status` 자체가 실패하면 배너를 숨기거나 회색 "상태 확인 불가"로 — 앱 크래시 금지, 에러 배너로 도배 금지.

**버튼 옆 상세 텍스트**: `CommandCenter.tsx` 4버튼 + `Layout.tsx` ofix 광고비 버튼 옆에 "N시간/N일 지남" (같은 status 데이터 재사용).

### 3-5. 데이터 흐름
```
[버튼 press] → request-refresh 플래그(기존)
      → Mac 데몬 claim+fetch+push(기존) → last_success_at bump(기존)
[전역 배너] ← GET collection-status(신규, 60s 폴) ← 4 스트림 last_success/error_at
```

---

## 4. 에러 처리
- 집계 엔드포인트 다운 → 배너 fail-safe(숨김/회색), 콘솔만.
- 스트림 `failed`(로그인 깨짐) → 🔴 "로그인 필요" 명시 (원래 불만이던 "모르고 지나감" 해소 핵심).
- 데몬 poll에서 자동 fetch 제거 후에도 flag 소비 경로는 불변 → 버튼 동작 회귀 없음(테스트로 확인).

---

## 5. 테스트
**백엔드**
- `collection-status`: 픽스처 타임스탬프 → 각 스트림 `state`/`age_hours` 정확(경계 24h/48h 포함).
- `failed` 판정: `last_error_at > last_success_at`일 때 `failed`.
- KST/UTC: UTC 저장값에 대해 age가 KST 기준으로 맞게 나오는지(경계에서 9시간 오차 없음).
- 자동 트리거 제거 회귀: 스케줄러 크론 테이블에 `request_ad_cost_refresh` 부재 / `cmd_poll` staleness 분기 미발동(플래그 없을 때 fetch 0회).

**프론트**
- 배너 임계 로직(24/48/failed) 컴포넌트 테스트.
- 집계 엔드포인트 실패 시 fail-safe(크래시 없음).

---

## 6. 리스크 / 롤백
- **새 리스크 = 잊어버림 → 조용히 낡음.** 과거 사고 이력 있음(광고비 400만원 누락, Wing 5일 먹통). → 전역 배너로 완화. 배너가 유일한 안전장치이므로 배너 가시성/신뢰성이 최우선.
- **롤백**: 제거한 크론/staleness 분기는 git 이력에 남음 → 되돌리면 즉시 이전 자동 모드 복원.
- **배포**: prod 배포는 `scripts/safe_deploy.sh`만 (D-NAO-49, 직접 scp 금지). 데몬 코드(`tools/*.py`) 변경 시 `launchctl kickstart -k` 재시작 필요(stale 구코드 clobber 방지).

---

## 7. 레고 계층 매핑 (원칙 18)
- **Sub-Agent**: `collection-status` 집계기(스트림별 상태 → 통합 상태·단일 책임). 데몬 `cmd_poll`(플래그 소비만).
- **Harness**: 전역 배너(상태 조회 → 임계 판정 → 표시/점프 유통).
- **Agent(메뉴)**: 종합조망(CommandCenter) 수집 UX + 전역 Layout 배너.

---

# 2026-07-27 개정: supervisor 폐기 → per-fetch 크롬 수명

- 결정: Jino 승인 2026-07-27 (KST)
- 상태: 구현 완료(코드), Mac 측 launchd 전환은 별도 수행

## R-1. 무엇을 뒤집었나

§2 **비목표**의 마지막 항목 — *"데몬/`*-chrome` supervisor plist 제거(버튼 성공률의 핵심 — 유지)"* — 을 **폐기한다.**

원 설계는 상주 Chrome을 "세션 보온"으로 보고 남겼다. 그러나 그 supervisor의 `KeepAlive=true`가
**Jino가 창을 닫으면 ~30초 뒤 Chrome이 되살아나는** 불편의 실측 범인으로 확정됐다
(`~/.ohisell_rocket_chrome.log`에서 종료→재기동 반복 확인). 즉 §3-1에서 자동 트리거 3개를 제거하고도
창이 계속 뜨던 잔여 원인이 supervisor 자신이었다.

**개정된 모델**: Chrome이 뜨는 유일한 순간 = **버튼 요청을 claim한 직후 1회(~20초, 세션 만료 시 로그인 대기 포함)**.
poll 데몬이 스스로 Chrome을 띄우고, 작업이 끝나면 자기가 띄운 Chrome만 닫는다.

## R-2. 설계 (per-fetch Chrome 수명)

각 CDP 페처(rocket·wing·ohitech)에 동일한 4요소를 넣었다(공유 모듈 없음 — 설치 스크립트가
페처 .py를 개별 복사하는 런타임 구조라 파일별 자립이 필수).

| 요소 | 내용 |
|---|---|
| `_chrome_argv(cfg)` | 기동 커맨드라인 단일 출처. 수동 `chrome` 커맨드와 per-fetch 기동이 **동일**해야 세션/핑거프린트가 갈리지 않는다. 실제 `/Applications/Google Chrome.app` + `--remote-debugging-port` + 전용 프로필(Playwright 번들 Chromium 금지 — Akamai 차단). |
| `_owned_chrome(cfg, owner)` | 가용 보장 컨텍스트. CDP 살아있으면 **adopt**(닫지 않음) / 프로필만 점유 중이면 거부(중복 launch=프로필 손상) / 없으면 stale Singleton lock 청소 후 launch + CDP 응답 대기(최대 60s). |
| `_ChromeOwner` | **소유권 추적**. `proc=None`이면 남의 창 → 절대 닫지 않는다. `keep_open=True`면 내 창이라도 남긴다. |
| `_close_chrome(proc)` | SIGTERM → 15s 대기 → SIGKILL. |

**창을 남기는 유일한 예외 = 사람이 로그인해야 할 때.** 세션 만료로 판정되면(로그아웃 URL,
로그인 HTML 응답, RG status/api 미응답, SALES 일별맵 아님) `owner.keep_open=True`로 그 창을 남긴다.
창을 먼저 닫아버리면 "재로그인하세요" 알림에 로그인할 창이 없다. 수동 `login`·`capture` 커맨드도 같은 이유로 창을 남긴다.
supervisor와 다른 점: **되살아나지 않는다** — Jino가 닫으면 그것으로 끝이다.

## R-3. 추가 제거 — RG 정산 새벽 일일 예약

`wing_browser_fetcher.cmd_poll`의 `rg_daily_hour`(기본 7시 이후 자동 1회) 분기를 제거했다.
§3-1이 잡지 못한 **마지막 자동 창 트리거**다. RG도 버튼 요청만 소비한다.
- 잃은 것: 일일예약이 버튼요청 claim 실패(§3-1 NOTE codex P2)를 재시도로 덮어주던 효과 → 실패 시 사람이 다시 누른다.
- 대체 안전장치: §3-4 전역 신선도 배너(낡음이 눈에 보임). 설정 키 `rg_daily_hour`는 무시된다(하위호환).

## R-4. 파일 변경

| 파일 | 변경 |
|---|---|
| `tools/rocket_supplier_fetcher.py` | per-fetch 수명 도입(`_cdp_alive`·`_profile_chrome_alive` 신규), `_do_run`/`cmd_login` 배선, `cmd_chrome`을 `_launch_chrome`으로 통일 |
| `tools/wing_browser_fetcher.py` | per-fetch 수명(`_chrome` CDP 분기에 내장), `_do_run`·`_do_rg_run`·`cmd_login` owner 배선, **RG 일일예약 제거**, `chrome-supervise` 폐기 스텁 |
| `tools/ohitech_ad_fetcher.py` | per-fetch 수명, `cmd_run`(응답 판정을 창 닫기 전으로 이동)·`cmd_capture` 배선, `chrome-supervise` 폐기 스텁 |
| `tools/ad_cost_browser_fetcher.py` | **무변경**(원래부터 per-fetch `chromium.launch(headless=False)` — advertising.coupang.com은 번들 Chromium 통과) |
| `tools/com.ohisell.{wing,ohitech}-chrome.plist` | **삭제** (rocket-chrome plist는 Mac에만 존재 → 전환 절차에서 제거) |
| `tools/install_local_runtime.sh` | supervisor 잡 2개를 설치 루프에서 제외 + 구 잡 수동 정리 안내 |

`chrome-supervise` 커맨드는 **지우지 않고 no-op 스텁으로 남겼다**: Mac에 구 plist가 남은 상태에서
스크립트만 갱신되면 usage 에러 → `KeepAlive` 크래시 루프가 된다. 스텁은 Chrome을 띄우지 않고 block만 한다.
잡을 bootout·plist 삭제한 뒤에는 이 스텁도 제거 가능(백로그).

## R-5. 리스크

- **세션 쿠키 소실 가능성**: Chrome을 완전 종료하면 세션 스코프 쿠키(만료 없는 쿠키)는 사라진다.
  supervisor의 "세션 보온" 효과가 없어지므로 재로그인 빈도가 늘 수 있다. 완화는 이미 있음
  (버튼이 띄운 창에서 바로 로그인 → 그대로 fetch 진행). 라이브에서 재로그인 빈도를 관찰할 것.
- **콜드 스타트 지연**: 매 버튼마다 Chrome 기동(수초~수십초, 과거 실측 최대 ~90s) 만큼 UI 대기가 길어진다.
  CDP 대기 상한 60s. 프론트 폴링 윈도우(광고 215s)와 비교해 여유는 있으나 체감 지연은 증가.
- **stale lock 오판**: `_profile_chrome_alive`(SingletonLock PID+cmdline 검증)로 방어. 다른 Chrome이
  같은 프로필을 점유하면 기동을 **거부**(사일런트 손상 대신 명시적 실패 + 알림).

## R-6. codex 교차검증(2026-07-27, 2라운드)에서 추가된 방어장치

리뷰 대화는 R1 P1 4건 전건 수용 → R2에서 2건 잔여 반박 + 신규 1건 수용으로 종결(P2 3건은 근거와 함께
기각/보류). 아래가 그 결과로 코드에 들어간 것들이다.

| 방어 | 무엇을 막나 |
|---|---|
| 설치 스크립트의 **구 supervisor 자동 bootout·plist 삭제·실패 시 exit 1** | 설치 목록에서 빼기만 하면 **이미 로드된 잡은 계속 산다** — 실행 중 프로세스는 .py를 덮어써도 안 바뀌므로 구 코드가 Chrome을 계속 상주시킨다(전환 목적 무효화). 문서 안내가 아니라 구조로 막는다. |
| `_profile_launch_lock(profile)` — `_owned_chrome`과 **수동 `chrome` 커맨드 둘 다** | 페처별 flock은 *작업* 단위라 같은 프로필의 다른 커맨드(login/chrome)·다계정 인스턴스와 배타되지 않는다. 둘 다 "비어 있음"을 보고 Singleton을 지운 뒤 이중 기동 → 프로필·쿠키 DB 손상. |
| `_port_owner_foreign()` — **fail-closed** (설정 `adopt_unverified_chrome`로 해제) | 포트 200만 보고 adopt하면 같은 포트의 무관한 Chrome으로 수집해 **남의 vendor 데이터를 우리 account_key로 적재**한다. 정당한 창은 전부 `_chrome_argv`로 떠서 cmdline에 프로필이 있으므로 '확인 불가'는 남의 Chrome 신호. |
| 소유 판정 = **PID 동일성**(`_profile_owner_pid` vs LISTEN PID) | cmdline 문자열 대조는 원리적으로 불가 — macOS `ps -o command=`는 argv를 공백으로 flatten해 공백 품은 인자 하나(`"https://x/a --user-data-dir=<우리경로>"`)와 진짜 인자 두 개를 구분 못 한다(codex R4). 프로필 안 SingletonLock의 PID는 우리 소유 디렉터리에서 나온 값이라 모호성이 없다. 단 심볼릭링크만 믿지 않는다(codex R5): 크래시 잔재 lock의 PID가 재사용돼 하필 우리 포트를 LISTEN하면 오adopt되므로, **PID 생존 ∧ 그 PID가 우리 프로필로 도는 Chrome임(cmdline)**까지 AND로 확인한다. cmdline은 여기서 보조 조건이라 flatten 모호성만으로는 위조가 성립하지 않는다(우리 lock의 PID까지 차지해야 함). (`_cmdline_has_profile` 경계 검사는 `_profile_chrome_alive`의 PID 재사용 방어로만 잔류 — 그쪽 오판은 '기동 거부'=안전한 방향.) |
| `_LIVE_OWNERS` + SIGTERM/SIGHUP·atexit 회수(재진입 가드 포함) | 파이썬 기본 SIGTERM은 `finally`를 **실행하지 않는다.** 설치 스크립트는 배포마다 poll 데몬을 bootout하므로 fetch 중 재설치하면 데몬만 죽고 Chrome이 남고, 다음 데몬은 그걸 adopt해 닫을 책임을 갖지 않는다 → 버튼-only인데 창 영구 잔류. |
| rocket에도 `chrome-supervise` no-op 스텁 | ★실측: repo엔 없지만 **Jino Mac에 `com.ohisell.rocket-chrome`가 실제로 로드돼 있었다**(2026-07-17 생성, 포트 9225). 새 .py엔 이 서브커맨드가 없어 설치 즉시 usage 에러 → KeepAlive+Throttle 30 → **30초 크래시 루프**. |

**라이브 미검증(원칙 22) — 첫 버튼 클릭 때 로그로 확인할 것**: adopt 게이트는 이제
"프로필 SingletonLock PID == CDP 포트 LISTEN PID"에 의존한다. 검증된 절반: SingletonLock 타깃 PID가
**브라우저 프로세스**(헬퍼 아님)임을 실행 중 Chrome으로 실측(2026-07-27, 기본 프로필 → PID 1040 =
`Google Chrome` 본체). 미검증 절반: 그 브라우저 프로세스가 `--remote-debugging-port`를 LISTEN하는
프로세스와 동일한지(디버깅 포트 Chrome 기동이 이 환경에서 차단돼 실측 불가). Chrome 구조상 DevTools
HTTP 서버는 브라우저 프로세스에 있으므로 성립할 것으로 보지만 **단정하지 않는다.**
어긋날 경우의 방향은 안전하다: adopt **거부** → 수집이 시끄럽게 멈추고(로그·ohitech는 Mac 알림),
`adopt_unverified_chrome:true`로 즉시 완화 가능. 조용한 오적재로는 이어지지 않는다.

**Jino 판단 대기(스코프 밖으로 보류)**: RG/vendor-summary의 `refresh-claim`은 성공 *전에* 요청을
소비하므로 실행 실패 시 버튼 요청이 유실된다(§R-3에서 일일예약이라는 사실상의 재시도가 사라져 노출됨).
제대로 고치려면 claim→lease(ack/release) 또는 실패 보고 엔드포인트가 필요한데 **둘 다 prod 백엔드
계약 변경**이라 이 작업 범위 밖이다(현재 실패 보고 엔드포인트 없음). codex도 "페처 단독으로는
exactly-once 복구 불가"에 동의. 당분간은 실패 시 사람이 버튼을 다시 누르고, 낡음은 전역 신선도 배너가 표면화.
