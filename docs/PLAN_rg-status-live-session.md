# PLAN — 층1: RG status/api 수집을 Mac 상주 브라우저 경로로 이관

> 2026-07-17 22:40 KST, 설계=Fable. 상위 계획: docs/PLAN_pipeline-freshness-3layer.md §4의 승격.
> 목적: WING1/WING2 정적 쿠키(반드시 만료 → 26일 침묵 사고의 뿌리)를 계정 row 수집 경로에서 제거.

## §0. 핵심 발견 (설계를 작게 만드는 사실)

**Mac wing 페처(`tools/wing_browser_fetcher.py`)는 이미 status/api 전체 JSON을 매일 받고 있다.**
`_rg_enumerate_group_keys()`(672행)가 엑셀 다운로드용 group key 열거를 위해 `RG_STATUS_PATH`
(`/tenants/rfm/v2/settlements/status/api`)를 상주 브라우저 same-origin으로 POST하고, 응답에서
group key만 뽑고 **수수료 필드 전부(settlementStatusReportDetail)를 버린다.**
→ 층1 = "버리던 JSON을 prod로 push" + "서버에 ingest 엔드포인트" 뿐이다. 새 인증·새 세션·새 크롤링 없음.

기존 인프라 재사용 목록:
- 인증: `X-Ingest-Token` 헤더 (`_rg_push_xlsx` 767행 ↔ 서버 `_require_ingest_token` 패턴 그대로)
- 파서: 서버 `_parse_status_response()` (설치분 dedupe 규칙 포함 — 재구현 금지, 그대로 호출)
- upsert: `sync_rg_settlement`의 upsert 루프를 `_upsert_account_rows(db, rows)`로 추출해 공용화
- 다계정: D-7 (`OHISELL_WING_CONFIG` env + `~/.ohisell_wing2_fetcher.json` + wing2 Chrome 프로필 — 이미 존재)

## §1. 변경 명세

### 서버 (repo backend)
1. `rg_settlement_sync.py`:
   - `_upsert_account_rows(db, rows) -> int` 추출 (sync_rg_settlement 내 루프 이동, 동작 불변).
   - `ingest_status_payload(db, account_key, raw: dict) -> dict` 신설 =
     `_parse_status_response(raw, account_key)` → `_upsert_account_rows` → commit.
     반환 {"synced": N, "account_key", "status": "ok"} / 파싱 실패 시 WingReadError 전파(라우터가 422).
   - 쿠키 상태행 조작 없음 (mark_red/mark_last_success는 쿠키 경로 전용 — 이 경로는 쿠키 무관).
2. `routers/coupang_ops.py`: `POST /api/coupang/wing/rg-settlement/ingest-status?account_key=...`
   - `X-Ingest-Token` 필수, account_key ∈ RG_ACCOUNTS 검증, body=raw JSON(dict).
   - upload_rg_settlement_xlsx(1828행)와 같은 방어 수위. 프론트 호출 없음.
3. 서버 크론 `sync_coupang_rg_settlement`(05:30)은 **유지**(쿠키 있으면 동작하는 무해한 폴백).
   층1 라이브 합격 후 별도 결정: WATCHDOG_COOKIES에서 WING1/2 제거 여부(data_stale이 전담하므로
   쿠키 경보는 영구 노이즈가 됨 — 합격 확인 후 제거 권장). ★제거 전까지는 쿠키 경보가 계속 뜨는 게 정상.

### Mac (tools/wing_browser_fetcher.py — repo 사본 수정 후 ~/.ohisell/tools/로 복사·재시작)
4. `_rg_fetch_status_raw(page, cfg)` 신설: RG_STATUS_PATH POST 1회, raw dict 반환.
   윈도우는 `rg_status_days`(신설 cfg, 기본 35 — 월경계 분할 주기+여유. 백필 시 90으로 1회 오버라이드).
5. `_rg_push_status(cfg, raw) -> int` 신설: `{prod}/api/coupang/wing/rg-settlement/ingest-status`
   POST (params={"account_key"}, headers=X-Ingest-Token). 실패=log+계속 (엑셀 흐름 fail-soft).
6. `cmd_rg`: raw 1회 fetch → push → 같은 raw로 group key 열거(기존 `_rg_enumerate_group_keys`를
   raw 재사용형으로 분리 — status/api 이중 호출 금지).
7. WING2 인스턴스 분리 결함 수정: `LOCK_PATH`(고정)·state 경로가 인스턴스 간 공유되면 상호 배제/오염
   → env/config로 분리 가능하게 (`OHISELL_WING_LOCK` env, cfg `state_path`). 기존 인스턴스 기본값 불변.

### 운영 (Jino 1회 개입 — WING2만)
8. WING1: **개입 0.** 세션 살아있음(07-17 push 실측) → 배포 후 다음 cmd_rg 실행에서 자동 개시.
   `rg_status_days=90` 1회 실행으로 06-22~ 계정 row 백필 → **서버 쿠키 재등록 자체가 불필요해짐.**
9. WING2: wing2 프로필 로그인 1회(`OHISELL_WING_CONFIG=~/.ohisell_wing2_fetcher.json ... login`)
   + launchd `com.ohisell.wing2` 등록. (로그인은 자격증명이라 Jino 직접.)

## §2. 라이브 합격 기준 (원칙22)

- [ ] Mac cmd_rg 1회 실행 → prod `coupang_rg_settlement_fee`에 WING1 계정 row(recognition_date_to
      최근 주) 실물 생성 + `/api/scheduler/health` data_stale에서 WING1 소멸.
- [ ] 90d 백필 실행 → 06-22 이후 결손 주기 전부 채워짐 + 옵션 row from과 계정 row from 일치 재검증.
- [ ] 6월 합계 재검산: 옵션 row 귀속이 보정값(2,083,450)과 정합 유지.
- [ ] WING2: 로그인 후 동일 확인 (그 전까지 WING2 data_stale 경보 유지가 **정상**).

## §3. 순서 제약

- 층2/층3 스프린트(병렬 에이전트 3개)가 rg_settlement_sync.py·coupang_ops.py를 먼저 만진다 →
  **층1 구현은 층3 완료 후 착수** (같은 파일 충돌 방지).
