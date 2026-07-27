# 세션 인수인계: claim lease 계약 + WING2 완전 복구 + 45일 자가치유 완결

> 저장일시: 2026-07-27 21:27 KST · repo 루트(main, 워크트리 아님) · main==prod(`1a01a93` 계열)
> 앞 HANDOFF: `HANDOFF_ohisell-ondemand-button-only-complete_20260727.md` — 그 "다음에 할 작업" 4건을 이 세션이 전부 완결.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (main 고정, 작업은 워크트리)
- prod: `sellc.ohitech.co.kr` (ssh BatchMode 가능, DB=`/home/ubuntu/ohisell/backend/ohisell.db`, 배포=`scripts/safe_deploy.sh`만)
- Mac 페처 런타임: `~/.ohisell/tools/`(사본, 갱신=`tools/install_local_runtime.sh`) + launchd 데몬 6개(adcost/wing/wing2/rocket/ohitech-ad/scheduler-watchdog)
- 테스트: 루트에서 `python3 -m pytest`(homebrew 3.14 — `backend/.venv`는 깨져 있음 `.venv.broken-py314`)

## 2. 이번 세션 완료 목록 (PR 5건 전부 병합·배포·라이브 검증)
- ✅ **claim lease 재시도 계약(PR #106)**: `backend/app/services/coupang/refresh_contract.py` 신설 — claim이 플래그를 보존(임대)하고 ①성공 ②3회 소진 ③login_required에서만 요청 소멸. TTL 20분(실측 최장 11.4분×1.75). 5개 스트림 배선+페처 4종 실패보고 첫 배선(기존엔 실패 보고 호출 0건이었음). alembic `c4e6a8b0d2f4`(claimed_at·attempt_count). codex 5R 22건 중 21수용/1기각. 계획서=`docs/PLAN_coupang-claim-retry-lease.md`(§0=Jino 확정: 버튼 1회=재시도 3회 포함·login_required 제외).
- ✅ **WING2(오하이테크) 완전 복구**: 원인="세션 사망"이 아니라 **수집 주체 부재**(로그인 미완+플래그 계정 차원 없음+green-while-dead 크론). 로그인 개통(세션은 크롬 프로필 보관)→RG 정산 49일 백필(계정 98행=14주기·옵션 엑셀 — 공백 7주기 중 2주기 실데이터·5주기 진짜 0원 해명, PRODUCT_SIZE는 최신덮어쓰기 위험으로 의도적 제외)→prod 24주기=WING1과 동일→신선도 배너 소멸→판매분석 재개통(장기 이력 push 완료).
- ✅ **WING2 상시 배선 P4·P5(PR #114)**: P4=CDP login이 state 마커를 실제로 안 만들던 버그(`_save_state` no-op) 수정+마커 실생성 라이브 실증 / P5=RG 크론 2개가 auth_error를 삼키고 50일간 'ok' 내던 것 → status≠ok raise + prod toggle API로 2개 잡 비활성(`sync_coupang_rg_settlement`·`auto_download_rg_settlement`). **P1~P3(계정 분리·버튼·wing2 데몬)은 병행 세션이 기구현 — 재실측으로 확인, 재구현 안 함.** 계획서=`docs/PLAN_wing2-standing-wiring.md`.
- ✅ **vendor-summary 45일 자가치유(PR #108)**: 고정 7일 창→45일 롤링·7일 청크(라이브 실증된 폭만 사용)·날짜 합집합 병합. `vs_days` config 키 제거(WING1/WING2 — config가 코드 기본값을 누르는 구조). **라이브 최종 실증: WING1 06-20~07-09 구멍 40행 완전 충전.**
- ✅ **음수 GMV 400 완화(PR #115)**: 환불>판매일(gmv 음수)은 정당 — 절대값 상한(100억/100만)만 방어. **병행 세션이 같은 버그를 동시 수정·prod 직접 배포 → safe_deploy CAS가 감지·차단 → 두 설계 합류·이력 정합화**(PR #113은 superseded close). 음수 2행(-16,620/-12,900) prod 수록 실증.
- ✅ chrome-supervise 스텁 제거(PR #107) — plist 3계층 소멸 재확인 후.
- ✅ 3P GMV=0 chip 종결: 결함 아님 — **오픽스 3P 필름 라인 06-26경 RG 이관(Jino 확정 의도)**. 메모리 `ofix-3p-moved-to-rg` 기록.
- ✅ 기록 정리: iCloud 중복 41건 검증 후 삭제, HANDOFF 60건·계획서 5건·TODOS·LESSONS #41·42 main 커밋·push(`1a01a93`). failure memory 3건 기록.

## 3. 확정된 결정사항
- **버튼 1회의 의도 = 재시도 3회까지 포함, login_required 실패는 재시도 제외**(Jino 21:45 "그 해석대로 진행해") — lease 계약 §0 금지선.
- **오픽스 3P → RG 이관은 의도된 변화**(Jino "맞아, 3P는 RG로 이관한 거야") — 3P GMV≈0을 결함으로 재조사하지 말 것.
- codex 기각 1건 유지: "모든 성공 경로에 lease 전달"은 머니데이터 ingest 경로 침범이라 후속 보류(Fable 권고=보류, Jino 이견 없음).
- RG 서버측 쿠키 경로 크론 2개는 비활성 유지(00:10 광고비 잡 전례).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/refresh_contract.py` | lease 계약 공용 SA(5스트림 공유) |
| `docs/PLAN_coupang-claim-retry-lease.md` | lease 스프린트 단일 진실(§0 금지선) |
| `docs/PLAN_wing2-standing-wiring.md` | WING2 배선 계획(★현행화 블록=P1~P3 기완료 증거) |
| `tools/wing_browser_fetcher.py` | wing 페처(45일 청크·CDP 마커·계정별 claim) |
| `backend/app/services/coupang/vendor_summary_sync.py` / `rg_settlement_sync.py` | 계정 차원 상태·lease 배선 |
| `~/.ohisell_wing2_fetcher.json` + `com.ohisell.wing2` | WING2 인스턴스(env 3종·lock 분리) |

## 5. 알려진 이슈 / 주의사항
- **서브에이전트 스톨 패턴**: codex 백그라운드 대기 중 스톨/조기 종료 반복 → SendMessage 재개+재개 지시에 "상태 실측 먼저·timeout 부여"(failure memory 기록됨).
- **병행 세션 중복 작업 2건 발생**(3P chip 중복 조사·음수 GMV 동시 수정) — 착수 전 열린 PR·최근 커밋·chip 상태 확인 습관(LESSONS #41). task_41a3cb55 chip은 PR #115로 기해결 — 그 세션이 살아있으면 닫을 것.
- 커밋 안 한 잔여 5개는 의도적: DB 런타임 3개(`backend/ohisell.db-shm/-wal/.bak_pre_s1`)+타 세션 미검토 스크립트 2개(`tools/ohitech_billboard_recon.py`·`test_ohitech_poll_backoff.py`).
- 테스트는 실 `.env` 있는 루트에서 돌려야 완결(LESSONS #42) — 워크트리 통과만 믿지 말 것.
- prod DB 백업들: `ohisell.db.bak-lease-202607271852`(서버)·`/tmp/ohisell.db.bak-rgrate-20260727-004416`.

## 6. 다음에 할 작업 (미완료)
- [ ] chip task_2a26d430: WING2 RG 데스크톱 호스트 세션 체크 갭(`_rg_session_ok`) — 별도 세션 진행 중일 수 있음, 착수 전 확인.
- [ ] lease "성공 경로 전달" 후속 판단(codex 기각 1건 — 현재 보류가 기본값).
- [ ] RG 쪽 창 자가치유(vendor-summary와 동일 구조 결함: `rg_days`/`rg_status_days` 고정 롤링 + `rg_max_periods=1` 병목 — dup 판정 근거 확인 선행).
- [ ] 04-27~05-03 오래된 RG 옵션 공백 2주기(WING1/WING2 공통·이번 장애 밖).
- [ ] 첫 무인 사이클 관찰: lease 재시도(실패 시 자동 재claim)·크론 비활성 후 아침 관문에 이상 없는지.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

`.claude/memory/HANDOFF_ohisell-lease-wing2-selfheal-complete_20260727.md` 읽고 이어서 작업해줘
