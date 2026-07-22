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
- 데몬/`*-chrome` supervisor plist 제거 (버튼 성공률의 핵심 — 유지).

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
- `com.ohisell.wing-chrome`(9222) · `com.ohisell.ohitech-chrome`(9224) supervisor plist — Chrome 세션 유지 = 로그인 재깨짐 방지

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
