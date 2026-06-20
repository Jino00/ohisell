# PLAN — 스케줄러 워치독 / 잡 실패 탐지·알림

> 작성: 2026-06-20 (Opus) · 상태: /plan-eng-review 반영 완료(codex 14건 + 자체 findings)
> 관련: 트랙 `revenue-wing-truth` D-10 (returns/settlement 16일 침묵 사고가 발단)
> 원칙: 22(라이브 검증) · 머니로직 불변 · 19(codex 게이트) · 18(레고 계층)

---

## 1. 목표 (What / Why)

**Why**: 2026-06-20, `sync_coupang_returns_job`·`sync_coupang_settlement_job`가 `_KST`
NameError로 6/4~6/20(16일) 매일 죽었으나 **아무 알림 없이** `last_run_at`만 stale로
남아, 라이브 DB를 직접 파보고서야 발견. 스케줄 잡이 조용히 죽는 사각지대를 없앤다.

**What**: 서버측 필수 스케줄 잡이 (a) 마지막 실행이 **에러/미스파이어**였거나,
(b) 기대 주기(cron×1.5)를 넘겨 **성공하지 못했거나**, (c) **스케줄러 자체가 안 돌거나
잡이 미등록**이면 — 탐지하고 Jino에게 알린다.

**비목표(이번 범위 아님)**: 종합조망 프론트 카드 가시화(후보 #3 UI). 잡 자동 복구/재시도.
**부분 성공(degraded) 탐지**(예: sync_all_channels가 일부 채널만 실패) — 잡이 "죽는" 것과
다른 문제, 별도 TODO. fail-soft/Mac·쿠키 의존 잡 감시.

---

## 2. 코드에서 확인한 전제 (라이브, 추측 없음)

- `SchedulerState`(`backend/app/models.py:1417`): id, job_name, cron_expression,
  is_enabled, last_run_at, next_run_at(미사용), created_at. **last_status/last_error 없음.**
- **APScheduler 3.x 확정**(`requirements.txt` apscheduler==3.10.4, 설치 3.11.2) →
  `scheduler.add_listener(cb, EVENT_JOB_EXECUTED|EVENT_JOB_ERROR|EVENT_JOB_MISSED)` 사용 가능.
  `scheduler_service.py:217` 주석이 이미 `EVENT_JOB_ERROR`를 언급(저자 인지).
- ★**성공의 정의 함정**(codex #1·#2): 일부 잡은 내부 예외를 삼킨다.
  - `sync_all_channels_job:54,72` 외부 except가 **re-raise 안 함** → 전체 실패도 삼킴.
  - naver_sa/meta/naver settlement 2종/cafe24도 외부 except가 로그만 → 리스너가 'ok' 오판.
  - 쿠팡 잡들은 이미 `raise`(line 221 등) → 리스너가 정상 포착.
  → **삼키는 잡들의 외부 except를 쿠팡 패턴처럼 re-raise로 정렬**해야 incident class 봉인.
- 수동 트리거(`routers/scheduler.py:45`) job_map은 **쿠팡/core만**(naver·ad 누락). 데코레이터로
  라우터를 감싸도 누락 잡엔 무효(codex #3) → **리스너(cron 경로 전체 포착)가 데코레이터보다 우월.**
- `main.py:22` `start_scheduler()` 실패해도 API 생존 → **scheduler.running=false·잡 미등록을
  health가 1급 신호로 노출**해야(codex #4). DB staleness만으론 36h 지연.
- 알림: `_notify_mac()`(`tools/ad_cost_browser_fetcher.py:669`) osascript, **Jino Mac 전용**.
  prod=Linux → osascript 불가. Mac 페처가 health 폴 후 `_notify_mac`. 단 `cmd_poll`은 현재
  ad-cost 전용·기본은 one-shot(codex #5) → **워치독 폴 모드 신규 배선 필요.**
- 헬스 기존: `GET /api/scheduler/status`(`routers/scheduler.py:18`). 별도 `/health` 신설.
- alembic head = `r2s3t4u5v6w7`. 새 revision의 down_revision으로 사용.

---

## 3. 구조 (Agent / Harness / Sub-Agent — 원칙18 계층 유지, 구현은 경량 함수)

```
[데이터 토대 — prod backend]
  SA① job_state_listener  ← APScheduler add_listener 콜백(데코레이터 아님). 단일 책임: 기록
        EVENT_JOB_EXECUTED → last_run_at=now, last_status='ok',     last_error=NULL, last_status_at=now
        EVENT_JOB_ERROR    → last_status='error', last_error=traceback[:2000], last_status_at=now (last_run_at 불변)
        EVENT_JOB_MISSED   → last_status='missed', last_status_at=now
        · 자체 새 SessionLocal · cron 경로 전 잡 자동 포착(수동 trigger는 기존 HTTP500 유지)
        · 머니로직 미접촉
  (보강) 삼키는 잡 외부 except를 re-raise로 정렬(sync_all_channels/naver_sa/meta/
        naver_settlement/naver_case_settlement/cafe24) → EVENT_JOB_ERROR로 표면화

  SA② staleness_evaluator (순수함수, I/O 없음)  ← 판정만
        입력: [{job_name, is_enabled, expected_interval_sec, last_run_at, last_status, last_status_at}], now
        출력: [{job_name, state, age_sec, reason}]
        state 규칙(우선순위):
          is_enabled=False                         → 'disabled' (노이즈 제외)
          last_status in ('error','missed')        → 'failed'
          last_run_at is None & job age>interval    → 'never_succeeded'
          now-last_run_at > interval*1.5            → 'stale'
          else                                      → 'ok'

[Harness — prod backend]
  H scheduler_health (읽기 전용)  ← SA 정보 유통 허브
        · scheduler.running 확인 / WATCHDOG_JOBS 중 get_jobs() 미등록 목록 산출(codex #4)
        · 대상 잡 SchedulerState 로드 · cron→CronTrigger 2회 발화 diff로 interval(신규의존0)
        · SA②에 주입 → {healthy:bool, scheduler_running, missing_jobs[], stale[], failed[],
          never_succeeded[], disabled[], as_of}

[Agent — 표면]
  A1 GET /api/scheduler/health
        · HTTP 200 + body(healthy:bool 포함). 에러는 **sanitized 요약**(error class+짧은 msg)만
          노출, 전체 traceback은 DB에만(codex #12 누출 방지).
  A2 Mac 페처 워치독 폴 (tools/ad_cost_browser_fetcher.py)
        · 신규 폴 모드 + launchd KeepAlive + 기동 알림 + 디바운스 상태파일 +
          연속 HTTP 실패 처리 + **집계 단일 알림**(N개 stale 한 번에) (codex #5·#6)
        · ⚠️ Mac off/sleep 시 알림 공백(bounded gap) — 라이브 한계로 명시(서버푸시 채널=TODO)
```

### 워치독 대상 allowlist (critical 서버측 — Jino 승인 critical-only)
포함: auto_sync_orders, auto_profit_calc, sync_naver_settlement,
sync_naver_case_settlement, sync_naver_sa_ad_costs, sync_meta_ad_costs,
sync_coupang_products, sync_coupang_rg_sizes, sync_coupang_rg_inventory,
**sync_coupang_returns, sync_coupang_settlement**, sync_coupang_rg_orders,
sync_coupang_coupons, sync_coupang_cs

제외(fail-soft/Mac·쿠키 의존): sync_coupang_rg_inbound, sync_coupang_rg_settlement,
auto_download_rg_settlement, sync_coupang_ad_cost, request_ad_cost_refresh, cafe24_token_refresh

---

## 4. 스프린트 분해 (Sub-Agent 먼저 → Harness → Agent)

- **S1 — DB 마이그레이션**: `scheduler_state`에 `last_status`(String, nullable),
  `last_error`(Text, nullable), `last_status_at`(DateTime, nullable) 추가. 모델 갱신.
  alembic rev down=`r2s3t4u5v6w7`. 로컬 upgrade → prod upgrade → PRAGMA 확인. [codex review]
- **S2 — SA② staleness_evaluator + 단위테스트**: 5-state 규칙 + fixture(경계값·None·disabled·
  failed 우선순위). 의존 0. [codex review]
- **S3 — SA① 리스너 배선 + 삼킴잡 정렬 + DRY 정리**:
  add_listener(EXECUTED|ERROR|MISSED) 콜백 + 삼키는 잡 6종 re-raise 정렬 +
  **14× 인라인 last_run_at 스탬핑 제거**(리스너가 중앙화). [codex review]
- **S4 — H + A1**: scheduler_health Harness(running·missing_jobs 포함) +
  `GET /api/scheduler/health`(sanitized·healthy bool). [codex review]
- **S5 — A2 Mac 워치독 폴**: 폴 모드 + launchd KeepAlive + 기동 알림 + 디바운스 상태파일 +
  연속 실패 처리 + 집계 알림. [codex review]

각 스프린트 완료 시 claude-progress.txt + 이 PLAN 체크리스트 갱신.

---

## 5. 테스트 계획 (codex #14 — 약한 검증 보강)

순수/단위(SA②):
- [ ] interval×1.5 경계(직전 ok / 직후 stale)
- [ ] last_status='error' → failed (stale보다 우선)
- [ ] last_status='missed' → failed
- [ ] last_run_at=None & job age>interval → never_succeeded
- [ ] is_enabled=False → disabled (stale 아님)

통합(SA①·H·A1):
- [ ] [→통합] 잡이 raise → 리스너가 last_status='error'+last_error 기록 (incident class 재현)
- [ ] [→통합] **삼키는 잡(sync_all_channels) 전체 실패 → re-raise 정렬 후 error 기록**(거짓 ok 봉인)
- [ ] H: scheduler.running=False → healthy=False
- [ ] H: WATCHDOG_JOBS 중 미등록 잡 → missing_jobs에 노출
- [ ] A1: last_error에 traceback 있어도 응답은 sanitized 요약만(누출 방지)
- [ ] cron interval 산출: 일배치→~86400, 2시간배치→~7200

Mac(S5):
- [ ] 폴 HTTP 실패(연속) → 데몬 죽지 않고 재시도, 기동 시 startup 알림
- [ ] 디바운스: 같은 stale 잡 N시간 내 1회만 알림 / 다수 stale → 단일 집계 알림

---

## 6. 완료 기준 / Self-Verification (원칙 22 — 라이브 증거)

1. prod `scheduler_state`에 last_status/last_error/last_status_at 컬럼 존재(PRAGMA).
2. 실제 잡 1회 성공 → prod DB row last_status='ok'.
3. **고의 raise 주입** → last_status='error'+last_error → `/api/scheduler/health`가 failed로 노출.
4. last_run_at 과거 셋 테스트 row → health stale.
5. **scheduler.running=False 모사** → health healthy=False·missing_jobs.
6. Mac 페처 prod 가리킴 → failed/stale 발생 시 `_notify_mac` 실제 발화(집계 1회).
7. 회귀 봉인: returns/settlement가 또 죽으면 cron×1.5(36h) 내 health가 잡음(3·4로 입증).
8. 머니로직 불변: net_profit/매출 수치 변화 0.
9. A1 응답에 전체 traceback 미포함(sanitized) 확인.

---

## 7. NOT in scope (명시적 보류)
- 종합조망 프론트 카드(후보#3 UI) — 별도 스프린트.
- **부분 성공(degraded) 탐지** — 잡이 "죽는 것"과 별개, TODO.
- **서버측 푸시 알림 채널**(Telegram/email) — Mac-off 공백 메움. 현재는 Mac 폴만(승인 범위). TODO.
- 잡 자동 복구/재시도, 알림 escalation.
- 수동 trigger 경로 리스너 적용(이미 HTTP500로 표면화됨).

## 8. What already exists (재사용)
- `GET /api/scheduler/status` — 잡 목록·next_run (health는 별도 신설, status 유지).
- **APScheduler add_listener** — 내장. 데코레이터 자작 대신 사용(Layer-1).
- `_notify_mac()` — 알림 재사용.
- Mac 페처 데몬 루프 — 워치독 폴 모드 추가(현재 ad-cost 전용).
- 쿠팡 잡의 dict-error→raise 패턴 — 삼킴잡 정렬 시 그대로 차용.

## 9. Failure modes (신규 코드패스별)
- 리스너 콜백 자체 예외 → try/except로 격리(콜백 실패가 잡/스케줄러를 죽이면 안 됨).
- 리스너 DB write 충돌 → 자체 짧은 세션·commit 후 close.
- health interval 산출 시 cron 파싱 실패 → 해당 잡 skip+log, 나머지 정상.
- Mac 폴 네트워크 단절 → 데몬 생존·재시도, 연속 실패 시 1회 알림.
- Mac off → 알림 공백(bounded, 명시).

## 10. 병렬화
- Lane A: S1(마이그레이션) → S3(리스너, models 의존) [순차, 같은 backend 코어]
- Lane B: S2(순수 evaluator) [독립, 즉시 착수 가능]
- S4는 S2+S3 후. S5는 S4 후. → B는 A와 병렬 가능, 이후 직렬.

## 11. Implementation Tasks
- [x] **T1 (P1)** — models/alembic — scheduler_state에 last_status/last_error/last_status_at 추가 ✅ (커밋 7d5d846)
  - Verify: ✅ 로컬 upgrade/downgrade 왕복 + PRAGMA, ✅ prod upgrade(rev s3t4u5v6w7x8)+PRAGMA 3컬럼, ✅ prod API 200 무회귀, codex GATE PASS
- [x] **T2 (P1)** — services — staleness_evaluator 순수함수 + 5-state 단위테스트 ✅ (커밋 bc7677a)
  - `app/services/scheduler_watchdog.py`(evaluate_job/evaluate_staleness, I/O 0) + 17 테스트. created_at 입력 추가(never_succeeded 첫 주기 유예). codex PASS(P2×2 우선순위·경계 테스트 반영).
- [x] **T3 (P1)** — 코드+테스트+codex 완료(0d0553f), ✅**prod 배포+라이브검증 완료(2026-06-20)** — §6.2 ok·§6.3 error 라이브 실증.
  - `_job_state_listener`+`add_listener`(EXECUTED|ERROR|MISSED) + `_apply_job_event`(순수 mutation 분리) + 삼킴잡 **7종** re-raise 정렬(계획의 6 + ★codex P1로 추가된 `recalculate_profit_job`) + 인라인 스탬프 13개 제거 + 라우터 수동트리거 status 정리.
  - 테스트 `test_scheduler_listener.py`(6: 매핑·last_run_at 보존·절단·폴백 + 삼킴 reraise[prod3.10/로컬3.9 skip]). 로컬 22 passed/1 skipped.
  - **codex R2 PASS**: R1 [P1](recalculate_profit 스탬프 제거했으나 except 미정렬→거짓 ok) 수용·수정 → R2 clean. ★교훈: 계획의 삼킴잡 목록(6)이 recalculate_profit 누락 → 실제 except 전수감사로 20잡 전부 raise 확인.
  - **Verify(PENDING, 원칙22 §6)**: prod 배포 후 ① 실잡 성공→last_status=ok ② 고의 raise→error+last_error ③ /health failed(S4 필요). 미배포 상태(prod models.py도 함께 배포).
- [~] **T4 (P1)** — 코드+테스트+codex PASS(커밋 ced07f7), ⚠️prod 배포+라이브검증 PENDING(S3와 함께)
  - `app/services/scheduler_health.py`: build_health(순수)·compute_interval_seconds(순수, cron 2발화 diff)·compute_scheduler_health(I/O 경계). WATCHDOG_JOBS 14종. `_sanitize_error`(예외 마지막 1줄≤200자, 전체 traceback DB만).
  - `GET /api/scheduler/health`(항상 200, healthy:bool) + SchedulerHealthOut/SchedulerJobVerdictOut(response_model이 last_error 차단).
  - 테스트 14개(interval 일배치86400/2시간7200/30분1800/불량0, sanitize 3, build_health: ok·failed+요약·stale·미등록·DB결손·정지·disabled무해·never_succeeded). 로컬 37 passed/1 skipped.
  - **codex S4 PASS**(P1 0). P2 2건: #1 sanitize=PLAN 설계(class+짧은msg) 유지·기각, #2 불규칙cron=현 allowlist(균등주기·KST무DST) 무관·가정 주석 보강.
  - ✅**prod 배포+라이브검증 완료(2026-06-20)** — 아래 §6 결과 참조.
- [x] **T5 (P2)** — tools — Mac 워치독 폴 데몬 + launchd KeepAlive + 디바운스 + 집계 알림 ✅ (커밋 7ba0e02)
  - `tools/scheduler_watchdog_poll.py`(독립 경량 데몬, ad_cost 페처와 분리) + `com.ohisell.scheduler-watchdog.plist`(KeepAlive) + install_local_runtime.sh 별도 블록(loop 미접촉=main wing-chrome 머지 안전). 6 테스트.
  - codex S5 PASS(P1 0). P2 2건 수용: #1 _load_cfg 오버라이드 독립화(prod_url 의존 버그), #2 _notify_mac 백슬래시/개행 하드닝.
  - ✅**라이브(원칙22 §6.6)**: launchd 데몬(PID 75949) 기동→prod /health 폴→집계 단일 알림+기동 알림 발화. 6h 디바운스 확인.
- [ ] **T6 (P3, TODO)** — 서버측 푸시 알림 채널(Mac-off 공백)

## 12. 체크리스트
- [x] S1 마이그레이션(로컬→prod PRAGMA) — codex pass ✅ 커밋 7d5d846, prod rev s3t4u5v6w7x8
- [x] S2 evaluator + 단위테스트 — codex pass ✅ (bc7677a, 17 테스트)
- [x] S3 리스너 + 삼킴잡 정렬 + DRY — 코드+codex R2 PASS(0d0553f), ✅prod 배포+라이브검증 완료(2026-06-20)
- [x] S4 Harness + /health — 코드+codex PASS(ced07f7), ✅prod 배포+라이브검증 완료(2026-06-20)
- [x] S5 Mac 워치독 폴 — 코드+codex PASS(7ba0e02), ✅launchd 데몬 라이브 가동·집계 알림 발화
- [x] 라이브 self-verify 1~9 통과 — ✅ §6.1~6.9 prod 실증(아래)
- [~] failures.jsonl(✅TZ misfire 기록) / claude-progress.txt(✅) / 트랙 D-N 기록

### 라이브 검증 결과 (2026-06-20, S3+S4 prod 배포 후 — 원칙22)
- 배포: 6파일 scp(models/schemas/scheduler_service/scheduler_watchdog/scheduler_health/routers.scheduler)+pm2 restart. 백업 `/home/ubuntu/ohisell_bak/watchdog_20260620_102611`.
- §6.1 ✅ prod `scheduler_state`에 last_status/last_error/last_status_at 존재.
- §6.2 ✅ 실 리스너+실 prod DB 셀프테스트: EVENT_JOB_EXECUTED → last_status=ok, last_run_at 세팅.
- §6.3 ✅ 고의 raise(boom-12345) → last_status=error, last_error 기록, last_run_at(마지막 성공) 보존.
  ✅ 워치드 잡(sync_coupang_cs) DB error 주입 → 라이브 `/health` `failed` 버킷 노출, error_summary=마지막 1줄만(traceback 미누출), 검증 후 원복(NULL).
- §6.4/6.7 ✅ **라이브 `/health`가 첫 호출에서 sync_coupang_returns·sync_coupang_settlement를 stale(~16.9일)로 포착** — 이게 워치독을 만든 6/4 침묵 사고 그 자체. 회귀 봉인 라이브 입증.
- §6.5 ✅ scheduler.running=False 모사(FakeStopped) → healthy=False·scheduler_running=False.
- §6.8 ✅ 머니 불변: `/api/overview/revenue-reconcile` 200·정상 GMV, `/api/scheduler/status` 200(기존 무회귀). 워치독은 scheduler_state만 write.
- §6.9 ✅ sanitized — 응답에 'Traceback'/'File' 없음, response_model에 last_error 필드 부재.
- ★실측 부산물(워치독 범위 밖, Jino 보고용): returns/settlement가 17일째 성공 0(고친 줄 알았던 사고가 미해소 가능성), naver_settlement/case/sa·meta·cs는 never_succeeded(단 listener 미배포 기간 영향 — 오늘 5~7시 cron부터 실제 ok/error/stale로 수렴 예정).

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | issues_found | 14 findings (outside voice, plan-stage) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_open→folded | 16 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | (backend-only, n/a) |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **CODEX:** outside voice (gpt-5.5, high effort) raised 14 gaps; 13 folded into plan (success-contract, scheduler-down/missing-job detection, last_status_at, null/disabled states, EVENT_JOB_MISSED, traceback sanitization, Mac transport hardening, test matrix). 1 (over-abstraction) resolved by keeping 원칙18 layering with lightweight implementation.
- **CROSS-MODEL:** Claude+Codex agree the central risk is "function returned ≠ job succeeded" → resolved via APScheduler listener + re-raise alignment of exception-swallowing jobs. Both agree listener > decorator. Only divergence: codex wants to drop SA/Harness ceremony; user's 원칙18 mandates it → layering kept, code lightweight.
- **VERDICT:** ENG CLEARED (findings folded) — ready to implement S1. Backend-only; no design/CEO review required.

**UNRESOLVED DECISIONS:**
- #13 architecture-ceremony: proceeding with 원칙18 layered-but-lightweight per user's standing rule; user may override to flat structure before S1 if desired.
