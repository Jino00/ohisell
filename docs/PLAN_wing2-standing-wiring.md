# PLAN — WING2(오하이테크) 상시 배선 수리

> 작성: 2026-07-27 15:18 KST (Fable 설계). Jino 승인: "WING2 상시 배선 수리도 이어서 진행해" (15:17).
> 근거 조사: 07-27 WING2 RG 누락 조사(이 세션) — 원인 = "수집 주체 부재 + 갱신 플래그에 계정 차원 없음 + green-while-dead 크론".
> **선행 조건: claim lease 계약(PLAN_coupang-claim-retry-lease.md) 병합 후 그 main 위에서 작업** — 같은 함수(rg/vs claim·상태 함수군)를 건드리므로 병렬 금지.

## §0 방향 고정

- WING2는 오늘(07-27) 로그인·수동 백필로 데이터는 복구됨. 이 스프린트의 목적은 **재발 방지 = 상시 수집 구조**: 버튼/데몬 경로에 WING2가 1급 시민으로 편입되는 것.
- 버튼-only 원칙 유지(상시 supervisor 부활 금지). WING2 데몬도 WING1과 동일한 poll(가벼운 GET) + per-fetch 크롬 수명.

## ★현행화 (2026-07-27 19:08 재실측)

**P1·P2·P3 = 병행 세션이 이미 구현·배포·라이브 가동 중 — 재구현 금지.** (증거: `_RG_STATE_ACCOUNT_BY_ACCOUNT`/`_VS_ACCOUNT_BY_ACCOUNT` 매핑, prod 상태행 4개 분리 실재·green, CommandCenter 계정별 병렬 폴링, `com.ohisell.wing2` 데몬 가동. 핵심 커밋 `d546243` 등, PR #106 병합 시 통합됨.)
**이 세션의 잔여 = P4·P5만.** P4는 현재 07-27 13:54 수동 스텁 마커 파일 하나로 버티는 중(코드 미수정 — 재발 시한폭탄), P5는 코드·prod 설정 둘 다 미수리(크론 2개 여전히 enabled=1).

## 1. 수리 항목 (조사 ④B 확정 + 이후 발견 1건)

### P1. 갱신 플래그 계정 차원 도입 (구조 결함 — 핵심)
- `rg_settlement_sync.py`: `_RG_STATE_ACCOUNT` 단일 상수 → 계정 파라미터화. 상태행 `COUPANG_WING_RG`(WING1, 기존값=기본값으로 하위호환) / `COUPANG_WING_RG2`(WING2). `rg_request_refresh`/`rg_refresh_status`/`rg_claim_refresh`/`rg_mark_heartbeat`/`rg_mark_fetch_error` + lease 함수들(선행 병합분)에 `account_key` 추가.
- `vendor_summary_sync.py`: `_VS_ACCOUNT`도 동일 구조(두 데몬이 같은 VS 플래그를 경합하는 동일 결함) — 단 VS 데이터 행 자체는 이미 계정별이므로 **상태행만** 분리.
- `coupang_ops.py` 해당 엔드포인트: `account_key` 쿼리 파라미터(생략 시 WING1 = 현행 동작 완전 호환).
- `tools/wing_browser_fetcher.py` `_prod_rg_refresh_status`/`_prod_rg_claim`(+VS 대응부): `params={"account_key": cfg["account_key"] 기반 상태키}` — 각 인스턴스가 자기 플래그만 소비.
- lease 컬럼(claimed_at·attempt_count)은 상태행 단위이므로 계정 분리와 자연 합성됨 — 테스트로 확인.

### P2. 프론트 버튼 계정 차원
- `frontend/src/lib/api.ts` + 해당 화면: RG 정산·판매분석 갱신 버튼이 **두 계정에 각각 요청**(1클릭=계정별 요청 2건 순차 발행이 기본. 계정별 버튼 분리가 UI상 더 자연스러우면 그쪽 — 구현 시 화면 구조 보고 판단, 상태 표시는 계정별로 구분되게).

### P3. wing2 poll 데몬 plist
- `tools/com.ohisell.wing.plist` 복제 → `tools/com.ohisell.wing2.plist`: Label `com.ohisell.wing2`, `EnvironmentVariables`에 `OHISELL_WING_CONFIG=~/.ohisell_wing2_fetcher.json`·`OHISELL_WING_LOG`·`OHISELL_WING_LOCK` 3종(페처 33행 규약), 로그 경로 분리. **lock 분리 필수**(미분리 시 두 인스턴스가 fetch lock 경합 — 페처 57행 경고).
- CDP 포트 충돌 없음 확인됨(WING2=9223, WING1=9222, ohitech-ad=9224).
- 설치·bootstrap은 배포 롤아웃 단계에서(코드 리뷰 대상은 plist 파일 자체).

### P4. CDP login state 마커 버그 (07-27 실사고 — 이 세션이 백필 중 직접 밟음)
- `wing_browser_fetcher.py`: CDP 모드 login이 "세션 저장 완료" 로그를 남기지만 `_save_state`가 no-op이라 `state_file`이 안 생김 → `rg`/`run`의 존재 게이트(735·1252행)가 fail-fast. WING1은 CDP 전환 이전 구식 파일로 우연히 통과 중.
- 수리: CDP 모드 login 성공 시 마커 state 파일(빈 storage_state)을 생성하거나, 게이트가 CDP 모드에서는 파일 대신 프로필 세션을 검사하도록. 로그 문구도 실체와 일치시킬 것.
- (임시 조치로 `~/.ohisell_wing2_state.json` 수동 마커를 이미 만들어 둠 — 수리 후에도 무해해야 함.)

### P5. green-while-dead 크론 수리
- `scheduler_service.py` `sync_coupang_rg_settlement_job`(988행 부근): SA가 fail-soft로 돌려주는 `{"status":"auth_error"}` dict를 로그만 찍고 삼킴 → 잡이 50일간 'ok'. **result status≠ok면 잡 실패로 기록**하도록 수정(다른 잡에 같은 패턴 있으면 관찰만, 수정은 스코프 밖).
- prod `scheduler_state`의 `sync_coupang_rg_settlement`·`auto_download_rg_settlement`(죽은 서버측 쿠키 경로, 양 계정 red) → **비활성화**(00:10 광고비 잡 비활성 전례 따름). 배포 단계에서 실행.

## 2. 완료 기준

- 전체 pytest 통과 + 신규: 계정 차원 상태 분리·양 데몬 무경합·하위호환(파라미터 생략=WING1) 테스트.
- codex review pass.
- 라이브 합격(배포·롤아웃 후): ①WING1/WING2 데몬 동시 가동 상태에서 각 계정 버튼 → 각자 수집·상대 플래그 무접촉 ②WING2 신선도 배너 재등장 없음 ③크론 2개 비활성 확인.

## 3. 롤아웃 체크리스트 (병합 후)

- [ ] safe_deploy: backend 변경분 + 프론트
- [ ] `~/.ohisell/tools/` 페처 사본 갱신 + 기존 데몬 kickstart
- [ ] `com.ohisell.wing2.plist` 설치·bootstrap
- [ ] WING1/WING2 config `vs_days:7` 제거(창 확대 자가치유 활성화 — vendor-summary 창 수리 배포와 동시)
- [ ] prod 크론 2개 비활성 + 라이브 합격 시나리오 실행
