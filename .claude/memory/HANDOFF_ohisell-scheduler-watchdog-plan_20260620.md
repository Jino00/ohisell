# 세션 인수인계: 스케줄러 워치독 — 구조설계 + 계획서 + /plan-eng-review 완료
> 저장일시: 2026-06-20 15:47
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★상태: 코드 0줄. 계획서 확정·eng-review 반영 완료 → 다음은 S1 구현(/model sonnet)

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: FastAPI, `backend/app/`. 실행: prod=pm2 `ohisell-backend`
- prod: `ssh ubuntu@sellc.ohitech.co.kr`, DB `/home/ubuntu/ohisell/backend/ohisell.db`
- 현재 브랜치: `fix/coupang-returns-settlement-kst-regression` (워치독은 아직 새 브랜치 미생성)
- APScheduler 3.10.4(pin)/3.11.2(설치) — `add_listener` 사용 가능 확정
- alembic head = `r2s3t4u5v6w7` (새 마이그레이션 down_revision)

## 2. 이번 세션 완료 목록
- ✅ `docs/PLAN_scheduler-watchdog.md` 생성 — 워치독 계획서(구조·5스프린트·테스트·self-verify·NOT-in-scope·실패모드·병렬화·Implementation Tasks T1~T6 + GSTACK REVIEW REPORT)
- ✅ 구조 라이브 정찰: `scheduler_service.py`(잡 14개+삼킴 패턴), `models.py:1417` SchedulerState, `routers/scheduler.py`(status/trigger/toggle), `_notify_mac`(`tools/ad_cost_browser_fetcher.py:669`)
- ✅ `/plan-eng-review` 실행 + codex(gpt-5.5 high) 아웃사이드보이스 14건 수집·13건 fold·1건 resolve
- ✅ gstack review-log/decision-log/telemetry 기록 (커밋 78751ac 기준)

## 3. 확정된 결정사항 (번복 금지)
- **D-A 워치독 대상 = critical 서버측 잡만**(returns·settlement 포함 14개). fail-soft/Mac·쿠키 의존 6개 제외(거짓알림 방지). [Jino "그래" 승인]
- **D-B SA① = APScheduler `add_listener(EVENT_JOB_EXECUTED|ERROR|MISSED)` 콜백** (데코레이터 아님). cron 경로 전 잡 중앙 포착, 수동-trigger-map 누락 회피. [Layer-1, codex+Claude 합의]
- **D-C 성공 정의 = "raise 없이 리턴"**. 단 예외 삼키는 잡 6종(sync_all_channels/naver_sa/meta/naver_settlement/naver_case_settlement/cafe24) 외부 except를 **re-raise로 정렬**해야 incident class 봉인. [codex #1·#2]
- **D-D scheduler.running=False·잡 미등록을 health 1급 신호로** 노출(main.py:22가 start 실패해도 API 생존). [codex #4]
- **D-E traceback는 DB에만, /health는 sanitized 요약만**(누출 방지·scheduler 라우트 무인증). [codex #12]
- **D-F DB 컬럼 3개 추가**: last_status, last_error(≤2000), last_status_at. last_run_at=마지막 성공 의미 유지.
- **D-G 원칙18 계층 유지하되 경량 구현**(codex #13 "over-abstraction"과 Jino 원칙18 충돌 → 원칙18 우선, 코드는 ceremony 없는 함수). ★유일한 미해결 항목 — Jino가 flat 구조로 override 가능(S1 전).
- **D-H Mac-off 알림 공백은 bounded gap로 명시**, 서버푸시 채널(Telegram/email)은 TODO(T6).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| docs/PLAN_scheduler-watchdog.md | ★단일 진실 원천(계획+리뷰리포트) |
| backend/app/services/scheduler_service.py | 잡 14개·start_scheduler — S3 리스너 배선·삼킴잡 정렬·인라인 스탬핑 제거 |
| backend/app/models.py:1417 | SchedulerState — S1 컬럼 3개 추가 |
| backend/app/routers/scheduler.py | status/trigger/toggle — S4 /health 신설 |
| tools/ad_cost_browser_fetcher.py:669 | _notify_mac + 데몬 — S5 워치독 폴 모드 |
| backend/alembic/versions/ | S1 새 rev (down=r2s3t4u5v6w7) |

## 5. 알려진 이슈 / 주의사항
- 예외 삼키는 잡 정렬(D-C) 시 **머니로직 미접촉** 유지 — except에 re-raise만 추가, 본문 불변.
- 리스너 콜백 자체 예외는 try/except 격리(잡/스케줄러 죽이면 안 됨).
- 수동 trigger 경로는 리스너 미적용(이미 HTTP500 표면화) — 의도된 범위.
- 매 스프린트 후 codex review 필수(원칙19). 라이브 self-verify(원칙22) 9항목 — 특히 prod에 고의 raise 주입→/health failed→Mac 알림까지 실증.
- 부분 성공(degraded) 탐지는 이번 비목표(잡 "죽음"과 별개, TODO).

## 6. 다음에 할 작업 (미완료)
- [ ] (선택) Jino가 D-G(원칙18 계층 vs flat) override 여부 확정
- [ ] `/model sonnet` 전환 + 새 브랜치 생성
- [ ] S1: scheduler_state 컬럼 3개 + 모델 + alembic rev → 로컬→prod upgrade→PRAGMA 확인 → codex
- [ ] S2: staleness_evaluator 순수함수(5-state) + 단위테스트 → codex
- [ ] S3: add_listener 콜백 + 삼킴잡 6종 re-raise 정렬 + 인라인 스탬핑 제거 → codex
- [ ] S4: scheduler_health Harness + GET /api/scheduler/health → codex
- [ ] S5: Mac 워치독 폴 모드 + launchd KeepAlive + 디바운스 + 집계 알림 → codex
- [ ] 라이브 self-verify 1~9 + failures.jsonl + claude-progress.txt + 트랙 D-N 기록

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ohisell-scheduler-watchdog-plan_20260620.md 읽고 이어서 작업해줘 (스케줄러 워치독 S1부터, /model sonnet)
