# 세션 인수인계: ohisell Wing 세션 자동화 트랙 S4 완료
> 저장일시: 2026-06-14 23:30 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 직전 HANDOFF: `HANDOFF_ohisell-wing-session-automation-S4_20260614.md`(S4 코드 완료·라이브 보류). 본 파일=S4 완전 완료(CDP 전환 + 라이브 self-verify + prod 배포 + 데몬 복원).

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`(8000). 테스트 `python -m pytest -q`(**191 그린**).
- 프론트: `cd frontend && npm run build` → `rsync -az --delete frontend/dist/ ubuntu@sellc.ohitech.co.kr:~/ohisell/frontend/dist/`
- **prod = `sellc.ohitech.co.kr`**(ssh Host: `sellc.ohitech.co.kr`, User=ubuntu). PM2 `ohisell-backend`(id=0, :8001). DB=SQLite. alembic head=`m7n8o9p0q1r2`(S2, 이번 세션 마이그레이션 없음).
- **CDP Chrome**: `backend/.venv/bin/python3 tools/wing_browser_fetcher.py chrome` → 전용 프로필(`~/.ohisell_wing_chrome`) Chrome 실행(port 9222). 로그인 후 `login` 커맨드.
- 페처/데몬: `backend/.venv/bin/python3 tools/wing_browser_fetcher.py {chrome|login|rg|poll}`. launchd `com.ohisell.wing`(복원 완료, pid 32396, running). 로그=`~/.ohisell_wing_fetcher.log`.
- 설정=`~/.ohisell_wing_fetcher.json`(cdp_port=9222·cdp_profile·account_key·vendor_id·ingest_token·prod_base_url·rg_*).

## 2. 이번 세션 완료 목록

### S4 CDP 모드 전환 (커밋 9037817)
- ✅ `tools/wing_browser_fetcher.py`: `_chrome()` 컨텍스트 매니저 추가 — `cdp_port` 설정 시 `p.chromium.connect_over_cdp(f"http://localhost:{port}")` (실제 Chrome 연결), 미설정 시 기존 `launch(headless=False)` 레거시 하위호환.
- ✅ `cmd_chrome` 추가: 전용 프로필(`--user-data-dir=~/.ohisell_wing_chrome`) + `--remote-debugging-port=9222`로 Chrome 실행. `chrome` 서브커맨드로 노출.
- ✅ `_save_state(cdp=True)` no-op(Chrome이 세션 보관). `_login_wait_loop`/`_rg_login_wait` cdp 파라미터 전파.
- ✅ `cmd_login`/`_do_run`/`_do_rg_run` 전부 `_chrome()` 컨텍스트 매니저로 일원화.
- ✅ `~/.ohisell_wing_fetcher.json`에 `cdp_port=9222`, `cdp_profile=~/.ohisell_wing_chrome` 추가.
- ✅ **라이브 self-verify**: `chrome` → Chrome 전용 프로필 열림 → 쿠팡 로그인 → `login` 8초 만에 감지(Akamai 차단 없음) → vendor-summary 14일분 prod push 성공.

### S4-P1 RG 정산 자동수집 라이브 실측 (커밋 509a075 포함)
- ✅ `python3 tools/wing_browser_fetcher.py rg` → 정산주기 `A01564720-2026-06-08-2026-06-14` → WAREHOUSING_SHIPPING 다운로드 → prod push → **upserted=10·입출고비 120,375·배송비 206,075·검산 diff=0**.

### S4-P3 prod 배포 (이번 세션)
- ✅ `backend/app/services/coupang/rg_settlement_sync.py` scp → prod.
- ✅ `backend/app/routers/coupang_ops.py` scp → prod.
- ✅ PM2 restart → ohisell-backend online.
- ✅ **self-verify**: `upload-xlsx` 무토큰 → **401** ✅, `rg-settlement/refresh-status` → **200** ✅.

### 데몬 복원
- ✅ `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ohisell.wing.plist` → state=running, pid=32396.
- ✅ 복원 즉시 **RG 일일예약 자동 트리거** 동작 확인(23:21 로그).

### 트랙/진행 파일 갱신 (커밋 0d3c286)
- ✅ `docs/tracks/active/track_wing-session-automation.md` — 체크리스트 5/6, S4 완료 기록, D-8 CDP 결정 반영.
- ✅ `claude-progress.txt` 갱신.

## 3. 확정된 결정사항

- **D-8 (S4) + CDP 추가**: Wing 자동화는 실제 Chrome CDP 연결 방식(`cmd_chrome` + `cdp_port=9222`)으로 운영. Playwright Chromium(핑거프린트 탐지 대상) 사용 안 함. Mac 재부팅 시 `chrome` 서브커맨드 먼저 실행 필수.
- **upload-xlsx는 X-Ingest-Token 필수**: prod 회계(net_profit 소스) 보호. 무토큰 → 401.
- **RG 범위**: 현재 WAREHOUSING_SHIPPING 1종. 나머지 7종(판매수수료·보관비·반품회수/재입고·반출비·반출배송·재고손실보상·부가서비스비) 코드명 미수집.
- **데몬 트리거**: 07시 새벽 일일예약 + 온디맨드 버튼(UI 미구현).

## 4. 핵심 파일 목록

| 파일 | 역할 |
|------|------|
| `tools/wing_browser_fetcher.py` | ★CDP 모드·`_chrome()`·`cmd_chrome`·`cmd_rg`·`cmd_poll` RG 분기 |
| `~/.ohisell_wing_fetcher.json` | 설정(cdp_port=9222·cdp_profile·ingest_token 등) |
| `~/.ohisell_wing_chrome/` | CDP용 Chrome 전용 프로필(쿠팡 세션 보관) |
| `~/Library/LaunchAgents/com.ohisell.wing.plist` | launchd 데몬 설정 |
| `~/.ohisell_wing_fetcher.log` | 데몬/페처 로그 |
| `backend/app/services/coupang/rg_settlement_sync.py` | RG 정산 파서+ingest+새로고침 플래그 |
| `backend/app/routers/coupang_ops.py` | `/wing/rg-settlement/*` 라우터(upload-xlsx 토큰 인증 포함) |
| `docs/tracks/active/track_wing-session-automation.md` | 단일 진실 원천(D-1~D-8, 체크리스트 5/6) |

## 5. 알려진 이슈 / 주의사항

- **Mac 재부팅 시 순서**: ① `python3 tools/wing_browser_fetcher.py chrome` (CDP Chrome 실행) → ② 쿠팡 로그인 → ③ `python3 tools/wing_browser_fetcher.py login` (세션 감지) → ④ 데몬은 launchd가 자동 재시작(부팅 시 자동 로드). Chrome이 켜져 있어야 데몬이 CDP 연결 가능.
- **Wing Chrome 종료 시**: 데몬이 CDP 연결 실패 → 로그에 에러. 재실행 필요(`chrome` 커맨드).
- **미관측(원칙22)**: ① 세션 만료→CDP 모드 회복 경로(Chrome 탭 살아있는데 쿠팡 세션 만료 시) ② 데몬 새벽 07시 일일예약 실제 round-trip(복원 직후 23:21 트리거만 확인) ③ UI '판매분석 갱신' 버튼 실제 클릭 round-trip.
- **RG 나머지 7종 코드명**: `CONFIRMED_SELLER_REPORT_TYPES`에 WAREHOUSING_SHIPPING만 있음. 드롭다운에서 캡처 필요.
- **git origin push 미완**: 로컬 커밋 4개(509a075·9037817·0d3c286 + 이전) 미push.

## 6. 다음에 할 작업 (S5)

- [ ] **나머지 7종 sellerReportType 코드명 수집** — Wing 정산 드롭다운에서 DevTools로 캡처 → `RG_REPORT_TYPES_DEFAULT` / `CONFIRMED_SELLER_REPORT_TYPES` 확장.
- [ ] **RG 새로고침 UI 버튼** — `RevenueDriftCard`에 RG 정산 갱신 버튼 추가(vendor-summary 갱신 버튼 패턴 복제, `/wing/rg-settlement/request-refresh`).
- [ ] (선택) git origin push.
- [ ] (선택) RG수수료 S8 size_mismatch_high 4건 감사.
- [ ] (선택) RG발송관제 S7 UI.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ohisell-wing-session-automation-S4done_20260614.md 읽고 이어서 작업해줘
