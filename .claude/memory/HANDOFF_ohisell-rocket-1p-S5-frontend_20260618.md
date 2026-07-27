# 세션 인수인계: ohisell-rocket-1p-S5-frontend
> 저장일시: 2026-06-18 
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 실행: 백엔드 `uvicorn app.main:app --reload` (포트 8000) / 프론트 `npm run dev` (포트 5173)
- 관련 URL: prod 백엔드 `PROD_BASE_URL` in `~/.ohisell_rocket_fetcher.json`
- 주요 환경변수: `COUPANG_ROCKET_VENDOR_ID`, `AD_INGEST_TOKEN`(rocket ingest 공유)

## 2. 이번 세션 완료 목록

### S5 프론트 (로켓배송 1P 종합조망 탭)
- ✅ **`frontend/src/lib/api.ts`** — 하단 "네이버 운영 패널" 블록 앞에 약 100줄 추가:
  - 타입 5종: `RocketCostCoverage`, `RocketOverview`, `RocketUnmappedItem`, `RocketMappingItem`, `RocketRefreshStatus`
  - 함수 7종: `fetchRocketOverview`, `fetchRocketCostMapUnmapped`, `fetchRocketCostMap`, `upsertRocketCostMap`, `deleteRocketCostMap`, `requestRocketRefresh`, `getRocketRefreshStatus`

- ✅ **`frontend/src/pages/CommandCenter.tsx`** — 총 ~200줄 추가:
  - `type Axis`에 `"rocket"` 추가
  - 상태 3종: `rocket: RocketOverview | null`, `rocketRefreshing: boolean`, `rocketRefreshMsg: string | null`
  - `doFetch()` 내 fail-soft parallel `fetchRocketOverview(f, t)`
  - `refreshRocketNow()` — Wing 패턴 복제(POST request-refresh → 30s 폴링 last_success_at, 180s timeout)
  - 탭 배열에 `["rocket", "🚀 로켓배송 1P"]` 추가
  - `{axis === "rocket" && <RocketView ... />}` 렌더링 블록
  - `RocketView` 컴포넌트(파일 맨 끝): 4카드(매출/광고/원가/순이익)+커버리지배지+드리프트+원가 매핑 관리 UI(accordion lazy-load)

- ✅ **`backend/app/services/coupang/rocket_supplier_sync.py`** — 파일 끝에 추가:
  - 상수: `_ROCKET_FETCHER_ACCOUNT = "COUPANG_ROCKET_FETCHER"`, `_STALE_HOURS = 26`
  - 함수군: `_state_row`, `_ensure_state_row`, `rocket_fetcher_status`, `request_rocket_refresh`, `rocket_refresh_status`, `claim_rocket_refresh`, `mark_rocket_fetch_success`

- ✅ **`backend/app/routers/coupang_ops.py`** — cost-map DELETE 이후에 4 엔드포인트 추가:
  - `POST /rocket/request-refresh` — UI 갱신 버튼
  - `GET /rocket/refresh-status` — UI 폴링
  - `POST /rocket/refresh-claim` — 페처 소비
  - `POST /rocket/fetch-success` — 페처 완료 알림

- ✅ **`tools/rocket_supplier_fetcher.py`** — cmd_poll 추가 + fetch-success 호출:
  - `_prod_rocket_refresh_status`, `_prod_rocket_claim`, `_prod_rocket_mark_success` 함수
  - `cmd_run()` 시작 시 refresh_requested_at 체크 → claim
  - `_do_run()` 완료 시 `/rocket/fetch-success` POST
  - `cmd_poll(cfg, interval=30)` — KeepAlive poll 루프(30s 주기, last_success_at > 23h 자동 실행)
  - `main()` `poll` 커맨드 라우팅

- ✅ **`tools/com.ohisell.rocket.plist`** — 완전 재작성:
  - 기존: `StartCalendarInterval` 08:00 `run` 1회
  - 변경: `KeepAlive: true` + `RunAtLoad: true` + `poll` 커맨드 (Wing 데몬 패턴)

- ✅ **`docs/tracks/active/track_coupang-rocket-1p.md`** — S5 체크리스트 완료 + 현재 진행 단계 갱신 (5/6)
- ✅ **`claude-progress.txt`** — S5 블록 추가

## 3. 확정된 결정사항

- **S5 갱신 버튼 패턴**: Wing poll 패턴 복제. `CoupangWingCookie` 재사용 대신 `COUPANG_ROCKET_FETCHER` 가상 account_key로 state row 관리
- **커버리지 배지**: `cost_coverage.coverage_pct` < 100% → amber 경고, 100% → emerald (원칙22 투명화)
- **원가 매핑 UI**: lazy-load (`mapLoaded` flag) — 첫 클릭 시만 API 호출
- **poll 데몬**: 30s 주기 체크 + last_success_at > 23h 이면 일일 자동 실행 (기존 08:00 예약형 대체)
- **미push**: S2+S3+S4+S4.5a+S4.5b+S4.5c+S5 전부 로컬만. 6/19 06:42 codex quota 리셋 후 묶음 push

## 4. 핵심 파일 목록

| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-rocket-1p.md` | ★단일 진실 원천 (D-N 결정사항 전체) |
| `frontend/src/pages/CommandCenter.tsx` | 🚀 탭 + RocketView 컴포넌트 |
| `frontend/src/lib/api.ts` | rocket API 함수 7종 + 타입 5종 |
| `backend/app/services/coupang/rocket_intelligence.py` | compute_rocket_overview Harness |
| `backend/app/services/coupang/rocket_supplier_sync.py` | ingest + refresh state 함수군 |
| `backend/app/services/coupang/rocket_cost_map.py` | 원가 매핑 Harness |
| `backend/app/routers/coupang_ops.py` | 1P 전용 엔드포인트 전체 |
| `backend/app/routers/overview.py` | GET /api/overview/rocket-overview |
| `tools/rocket_supplier_fetcher.py` | 헤드풀 CDP 페처 + cmd_poll |
| `tools/com.ohisell.rocket.plist` | launchd KeepAlive poll 데몬 |

## 5. 알려진 이슈 / 주의사항

- **prod 미배포**: S2~S5 전부 로컬. prod 백엔드는 S1 이전 상태(rocket 테이블 없음). 페처를 prod로 향하면 404.
- **launchd 미설치**: plist가 바뀌었으나 `~/Library/LaunchAgents/` 미복사. prod 배포 후 설치 필요.
- **alembic 마이그레이션 2개**: `q1r2s3t4u5v6`(po_items), `r2s3t4u5v6w7`(cost_map) — prod에 `alembic upgrade head` 필요
- **codex 보류**: OpenAI quota 소진. 6/19 06:42 리셋 후 `/codex review` 실행(원칙19 게이트)
- **테스트 314 통과, 빌드 성공** (dist `index-C4uDjVY3.js`)

## 6. 다음에 할 작업 (미완료)

- [ ] **6/19 06:42 이후** `/codex review` — S2+S3+S4+S4.5a+S4.5b+S4.5c+S5 diff 교차검증
- [ ] **pass 시 prod 배포**:
  - `scp` models/routers/services/alembic migrations
  - `alembic upgrade head` (q1r2s3t4u5v6 · r2s3t4u5v6w7)
  - `pm2 restart ohisell-backend`
  - `npm run build` → `rsync dist/ → prod nginx`
- [ ] **launchd poll 데몬 설치**:
  - `cp tools/com.ohisell.rocket.plist ~/Library/LaunchAgents/`
  - `launchctl unload ~/Library/LaunchAgents/com.ohisell.rocket.plist 2>/dev/null`
  - `launchctl load ~/Library/LaunchAgents/com.ohisell.rocket.plist`
- [ ] **prod 라이브 self-verify (원칙22)**:
  - `launchctl kickstart -k gui/$(id -u)/com.ohisell.rocket` → DB 적재 확인
  - `GET /api/overview/rocket-overview` (cost/has_cost/cost_coverage)
  - 브라우저 🚀 탭 UI 렌더 확인
  - 갱신 버튼 클릭 → 데몬 round-trip 확인
- [ ] **`git push`**
- [ ] **(운영) 원가 매핑 채우기**: cost-map/unmapped 미매핑 상품번호 제안 클릭 확정 → coverage_pct 상승

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rocket-1p-S5-frontend_20260618.md 읽고 이어서 작업해줘
```
