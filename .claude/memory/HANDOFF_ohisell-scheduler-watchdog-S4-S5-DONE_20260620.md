# 세션 인수인계: 스케줄러 워치독(S5b) S4+S5 완료 — 미니트랙 5/5 전부 완료
> 저장일시: 2026-06-20 20:26
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 prod: `ssh ubuntu@sellc.ohitech.co.kr` · pm2 `ohisell-backend`(venv `/home/ubuntu/ohisell/backend/.venv/bin/python3` -m uvicorn :8001) · DB `/home/ubuntu/ohisell/backend/ohisell.db`
- 프론트 prod: nginx `/home/ubuntu/ohisell/frontend/dist`, `https://sellc.ohitech.co.kr`
- 배포: 백엔드=변경파일 scp → `pm2 restart ohisell-backend`. 프론트=`npm run build`→rsync.
- 로컬 테스트: `cd backend && python3 -m pytest tests/test_scheduler_watchdog.py tests/test_scheduler_listener.py tests/test_scheduler_health.py tests/test_scheduler_watchdog_poll.py -q` (로컬 Python 3.9 — 순수함수 테스트 OK, app 전체 임포트는 prod 3.10 필요. 43 passed/1 skipped).
- 로컬 Mac 데몬: launchd `com.ohisell.{adcost,wing,wing-chrome,rocket,scheduler-watchdog}`. **워치독 데몬 라이브 가동 중**(PID 75949).
- 브랜치: **`feat/scheduler-watchdog`**(S1~S5 전부 커밋, 미머지·미push).

## 2. 이번 세션 완료 목록
- ✅ **S4 — scheduler_health Harness + GET /api/scheduler/health (커밋 ced07f7, codex PASS)**
  - `backend/app/services/scheduler_health.py`(읽기전용 Harness, SA 허브, 머니로직 미접촉):
    · `build_health`(순수): missing_jobs(DB결손 OR running·enabled인데 미등록)·5버킷(failed/stale/never_succeeded/disabled)·healthy(disabled 제외, 그외 비정상이면 False).
    · `compute_interval_seconds`(순수): cron→CronTrigger 2발화 diff(일86400/2시간7200/30분1800/불량0). KST무DST·균등주기 가정(주석).
    · `compute_scheduler_health`(I/O 경계): SchedulerState(WATCHDOG_JOBS 14종) 로드+scheduler.get_jobs() → build_health.
    · `_sanitize_error`: 예외 마지막 1줄≤200자만(전체 traceback DB만 — 누출 방지).
  - `backend/app/routers/scheduler.py`: `GET /api/scheduler/health`(항상 200, healthy:bool). `backend/app/schemas.py`: SchedulerHealthOut/SchedulerJobVerdictOut(response_model이 last_error 누출 차단).
  - 테스트 `tests/test_scheduler_health.py` 14개.
- ✅ **S3+S4 prod 배포 + 라이브검증(원칙22 §6 전부 PASS, 커밋 d24a0b0)**
  - 6파일 scp(models/schemas/scheduler_service/scheduler_watchdog/scheduler_health/routers.scheduler)+pm2 restart(online). 백업 `/home/ubuntu/ohisell_bak/watchdog_20260620_102611`(롤백용).
  - §6.1 컬럼존재 ✓ / §6.2 실리스너+실DB executed→ok ✓ / §6.3 고의 raise(boom)→error+last_error·last_run_at(성공)보존 + 워치드잡 DB error주입→`/health` failed·error_summary 마지막1줄만(traceback 미누출)→원복 ✓ / §6.4·6.7 **라이브 첫 `/health`가 returns/settlement stale ~16.9일 즉시 포착=6/4 침묵사고 회귀봉인 라이브 입증** ✓ / §6.5 scheduler.running=False 모사→healthy=False ✓ / §6.8 머니불변(revenue-reconcile·/status 200) ✓ / §6.9 sanitized ✓.
- ✅ **S5 — Mac 폴 데몬 + launchd + 집계 알림 (커밋 7ba0e02, codex PASS) + 라이브검증(§6.6)**
  - `tools/scheduler_watchdog_poll.py`(독립 경량 데몬, 원칙8/18 — ad_cost 페처와 분리, requests만): prod /health 30분 폴 → 비정상 시 `_notify_mac` 집계 단일 알림(osascript). 6h 디바운스·해소 prune(재발 즉시 재알림)·기동 알림(1h 디바운스)·연속 net실패(3h)→'prod 도달불가' 1회 알림·데몬 불사. `_problem_keys`/`_summarize`/`_maybe_notify` 순수분리. once/poll 모드.
  - `tools/com.ohisell.scheduler-watchdog.plist`(KeepAlive+RunAtLoad+Throttle30s). `tools/install_local_runtime.sh`: 워치독 설치 블록을 기존 loop **뒤에** 별도 추가(loop 미접촉=main의 wing-chrome loop수정과 머지 안전).
  - 테스트 `tests/test_scheduler_watchdog_poll.py` 6개. **라이브**: launchd 데몬(PID 75949) 기동→prod 폴→집계 알림+기동 알림 발화, once 디바운스 확인.
  - ★수동 설치(풀 installer 미실행)로 S5a wing-chrome 보호.
- ✅ failures.jsonl 1건: 셀프테스트 TZ misfire(prod UTC vs scheduler KST → datetime.now()가 9h과거 misfire). 라이브코드 정상·테스트 하니스 버그(원칙22 교훈).

## 3. 확정된 결정사항
- **워치독 = revenue-wing-truth 트랙의 S5b = 자체 미니트랙**(계획서 `docs/PLAN_scheduler-watchdog.md`, T1~T6). **T1~T5 전부 완료(5/5)**. T6(서버측 푸시 알림, Mac-off 공백 메움)=TODO 미착수.
- **WATCHDOG_JOBS 14종 allowlist**(critical-only): auto_sync_orders·auto_profit_calc·naver_settlement·naver_case·naver_sa·meta·coupang_products·rg_sizes·rg_inventory·returns·settlement·rg_orders·coupons·cs. 제외(fail-soft): rg_inbound·rg_settlement·auto_download·ad_cost·request_refresh·cafe24.
- **last_run_at='마지막 성공' 의미** — 리스너 EXECUTED에만 갱신, error/missed 보존.
- **워치독 폴러는 ad_cost 페처와 별도 파일/별도 launchd**(원칙8/18 단일책임 — 폴러는 가벼운 requests, 페처는 무거운 playwright).
- **머니로직 불변** — 워치독은 scheduler_state 테이블만 write.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_scheduler-watchdog.md` | ★워치독 단일 진실원천. T1~T5 완료, §6 라이브검증 결과 기록, T6 TODO |
| `backend/app/services/scheduler_watchdog.py` | SA② evaluate_staleness(순수, 5-state) |
| `backend/app/services/scheduler_service.py` | SA① 리스너 `_job_state_listener`/`_apply_job_event`+add_listener, 20잡 re-raise 정렬 |
| `backend/app/services/scheduler_health.py` | Harness build_health/compute_interval_seconds/compute_scheduler_health + WATCHDOG_JOBS |
| `backend/app/routers/scheduler.py` | GET /api/scheduler/health |
| `backend/app/models.py:1417` | SchedulerState(+last_status/last_error/last_status_at) |
| `tools/scheduler_watchdog_poll.py` | Mac 폴 데몬(집계 알림·디바운스) |
| `tools/com.ohisell.scheduler-watchdog.plist` | launchd KeepAlive |
| `backend/tests/test_scheduler_{watchdog,listener,health,watchdog_poll}.py` | 테스트 43개 |

## 5. 알려진 이슈 / 주의사항
- 🔴 **워치독이 즉시 잡은 실제 문제(별도 조사 필요, 워치독 범위 밖)**: `sync_coupang_returns`·`sync_coupang_settlement`가 **17일째 성공 0**(stale ~16.9일, 6/3~6/4 이후). 고친 줄 알던 6/4 _KST NameError 사고가 미해소 의심. naver/meta/cs `never_succeeded`는 리스너 미배포 기간 영향이라 **오늘 5~7시 cron부터 실제 ok/error/stale로 수렴 예정** → 내일 아침 `curl /api/scheduler/health` 재확인하면 진짜 상태 드러남.
- **브랜치 분기**: `feat/scheduler-watchdog`는 main의 S5a(wing-chrome+ad ALL coverage) 미포함. install_local_runtime.sh는 branch=loop 미수정+워치독 블록 추가, main=loop 수정 → **자동 병합 예상(충돌 회피 설계)**. 머지는 Jino 승인 후.
- **로컬 main이 origin보다 앞섬(미push)**. push는 Jino 요청 시만.
- 로컬 Python 3.9: app 전체 임포트 테스트는 Pydantic `X|None`로 실패(prod 3.10 정상). 순수함수 테스트는 3.9 OK.
- 작업트리에 무관 미커밋 파일(다른 트랙 HANDOFF·rocket_supplier_sync.py·track_coupang-* 등) — 건드리지 말 것.
- prod 롤백 필요 시: `/home/ubuntu/ohisell_bak/watchdog_20260620_102611`의 4파일 복원+pm2 restart.

## 6. 다음에 할 작업 (미완료)
- [ ] (선택) **`feat/scheduler-watchdog` → `main` 머지 + push** (Jino 요청 시). 머지 후 install_local_runtime.sh 1회 실행하면 wing-chrome+워치독 모두 정합 설치.
- [ ] (별도 작업) **returns/settlement 17일 침묵 조사** — _KST NameError 미해소 여부 확인, 재발 방지. 워치독이 이제 36h 내 알림.
- [ ] (선택) **PLAN T6 — 서버측 푸시 알림 채널**(Telegram/email) — Mac off/sleep 시 알림 공백(bounded gap) 메움. 현재는 Mac 폴만.
- [ ] (관찰) 내일 아침 cron 후 `/api/scheduler/health` 재확인 — never_succeeded 잡들이 실제 상태로 수렴했는지.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-scheduler-watchdog-S4-S5-DONE_20260620.md 읽고 이어서 작업해줘
```
