# 세션 인수인계: S5a(Wing Chrome launchd 상주화) 완료 + S5b(스케줄러 워치독) S1~S3
> 저장일시: 2026-06-20 17:10
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 prod: `ssh ubuntu@sellc.ohitech.co.kr` · pm2 `ohisell-backend`(venv `/home/ubuntu/ohisell/backend/.venv/bin/python3` -m uvicorn :8001) · DB `/home/ubuntu/ohisell/backend/ohisell.db`
- 프론트 prod: nginx root `/home/ubuntu/ohisell/frontend/dist`, `https://sellc.ohitech.co.kr/command-center`
- 배포: 백엔드=변경파일 scp → `pm2 restart ohisell-backend`. 프론트=`npm run build`→rsync.
- 로컬 테스트: `cd backend && python3 -m pytest tests/test_scheduler_watchdog.py tests/test_scheduler_listener.py -q` (로컬 Python 3.9 — app 전체 임포트는 Pydantic `X|None`로 실패하나 prod 3.10 정상. 순수함수 테스트는 3.9 OK. 파일 명시 실행.)
- 로컬 Mac 데몬: launchd `com.ohisell.{adcost,wing,wing-chrome,rocket}`. 설치/재배포 = `bash tools/install_local_runtime.sh`(멱등). CDP Chrome 9222 = wing-chrome 잡이 상주 관리(S5a).

## 2. 이번 세션 완료 목록
- ✅ **S5a — CDP Chrome 9222 launchd 상주화 (트랙 revenue-wing-truth D-4) — 완료·main 머지(커밋 ae6e07f+3520c13, FF로 main=3520c13, 미push)**
  - `tools/wing_browser_fetcher.py`: `cmd_chrome_supervise`(Chrome 포그라운드 자식 `proc.wait()` block→launchd가 수명 인식; cmd_chrome은 Popen 즉시리턴이라 KeepAlive 비호환) + `_cdp_alive`(/json/version 프로브) + `_profile_chrome_alive`(SingletonLock PID+cmdline `--user-data-dir` 검증). 기존 Chrome adopt/없으면 stale lock 청소 후 launch. SIGTERM wait10s→SIGKILL.
  - `tools/com.ohisell.wing-chrome.plist`(신규, KeepAlive+RunAtLoad+Throttle10s). poll 데몬(com.ohisell.wing)은 connect_over_cdp attach만 → 독립 공존.
  - `tools/install_local_runtime.sh`: wing-chrome 추가 + bootout 잡소멸 폴링(느린 SIGTERM 레이스 방지) + 잡 PID갱신 리로드 검증(silent stale-deploy 차단).
  - **codex 4R clean PASS**(R1 P2×2·R2 P2×1·R3 P2×2 전부 수용→R4 무지적). **라이브 self-heal 검증(원칙22)**: Chrome SIGKILL→proc.wait rc=-9 즉시감지→launchd 재기동→lock청소→fresh Chrome→CDP 자동복구(세션유지·리스너1). installer 4잡 PID갱신 안정로드.
- ✅ **S5b S1 — scheduler_state 워치독 컬럼(커밋 7d5d846, background가 선행)**: last_status/last_error/last_status_at(nullable) + alembic `s3t4u5v6w7x8`. **prod DB는 이미 upgrade됨**(컬럼 존재), 단 prod **models.py·scheduler_service.py 미배포**(S3 함께 배포 예정).
- ✅ **S5b S2 — staleness_evaluator 순수함수(커밋 bc7677a)**: `backend/app/services/scheduler_watchdog.py`(SA②, I/O0) evaluate_job/evaluate_staleness. 우선순위 disabled>failed(error/missed)>never_succeeded>stale(>1.5×주기)>ok. created_at 입력 추가(첫 주기 유예). 17 단위테스트. codex PASS(P2×2 반영).
- ✅ **S5b S3 — 리스너 배선 + 삼킴잡 정렬(커밋 0d0553f, ⚠️prod 미배포)**: `_job_state_listener`+`add_listener(EXECUTED|ERROR|MISSED)` + `_apply_job_event`(매핑 순수 분리). 삼킴잡 **7종** re-raise 정렬. 인라인 last_run_at 스탬프 13개 제거. 라우터 수동트리거 status 정리. 6 테스트. **codex R2 PASS**(R1 [P1] recalculate_profit 수정 후 clean).
- ✅ failures.jsonl 3건(launchd fire-and-forget·Chrome CDP 콜드스타트 90s·installer 레이스). 트랙/계획서/progress 갱신 커밋.

## 3. 확정된 결정사항
- **S5a는 main 머지 완료**(Jino 승인 "main 머지 + S5b 이어서"). S5b는 별도 브랜치 `feat/scheduler-watchdog`(미머지).
- **워치독 = revenue-wing-truth 트랙의 S5b** = 자체 미니트랙(계획서 `docs/PLAN_scheduler-watchdog.md`, T1~T6, S1~S5). eng-review 완료.
- **삼킴잡은 7종**(계획 6 + codex P1으로 추가된 `recalculate_profit_job`). 모든 20잡이 outer except에서 raise(전수감사 확정).
- **last_run_at = '마지막 성공' 의미** — 리스너가 EXECUTED에만 갱신, error/missed는 보존.
- **워치독 allowlist**(critical, 계획 §3): auto_sync_orders·auto_profit_calc·naver_settlement·naver_case·naver_sa·meta·coupang_products·rg_sizes·rg_inventory·**returns·settlement**·rg_orders·coupons·cs. 제외(fail-soft): rg_inbound·rg_settlement·auto_download·ad_cost·request_refresh·cafe24.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_scheduler-watchdog.md` | ★워치독(S5b) 단일 진실원천. T1·T2 완료, T3 코드완료(prod 미배포), T4/T5 잔여 |
| `docs/tracks/active/track_revenue-wing-truth.md` | 상위 트랙(S2~S5a 완료, S5b=워치독 보류 표시) |
| `backend/app/services/scheduler_watchdog.py` | S2 SA② evaluate_staleness(순수, 5-state) |
| `backend/app/services/scheduler_service.py` | S3 리스너 `_job_state_listener`/`_apply_job_event`+add_listener, 20잡 re-raise 정렬, 스탬프 제거 |
| `backend/app/routers/scheduler.py` | S3 수동트리거 status 정리. **S4에서 GET /api/scheduler/health 추가 예정** |
| `backend/app/models.py:1417` | SchedulerState(+last_status/last_error/last_status_at, S1) |
| `backend/tests/test_scheduler_{watchdog,listener}.py` | S2·S3 테스트 23개 |
| `tools/wing_browser_fetcher.py`·`com.ohisell.wing-chrome.plist`·`install_local_runtime.sh` | S5a (main 머지됨) |

## 5. 알려진 이슈 / 주의사항
- **로컬 main이 origin보다 10커밋 앞섬(미push)**. push는 Jino 요청 시만.
- **prod models.py·scheduler_service.py 미배포** — prod DB엔 S1 컬럼 있으나 prod 코드는 구버전(추가 컬럼 무시·무해). S3 배포 시 models.py+scheduler_service.py+routers/scheduler.py 함께 배포해야 정합.
- 로컬 Python 3.9: app 전체 임포트 테스트는 Pydantic `X|None`로 collection error(prod 3.10 정상). `test_scheduler_listener.py`의 삼킴 reraise 테스트는 `@skipif(<3.10)`. 순수함수 테스트는 3.9 OK.
- 작업트리에 무관 미커밋 파일(다른 트랙 HANDOFF·rocket_supplier_sync.py·track_coupang-* 등) — 건드리지 말 것.
- `git branch`에 `refs/heads/main 2`(broken name) 경고 — iCloud 중복 ref, 무해(미정리).
- Chrome CDP 콜드스타트 ~90s(포트 즉시 LISTEN, /json/version은 완전초기화 후) — 검증 폴 창 90s+.

## 6. 다음에 할 작업 (미완료)
- [ ] **S4 — scheduler_health Harness + `GET /api/scheduler/health`**(계획 T4): scheduler.running 확인 + WATCHDOG_JOBS 중 get_jobs() 미등록 산출 + cron→CronTrigger 2회 발화 diff로 interval 산출 + SA②(evaluate_staleness) 주입 → {healthy:bool, scheduler_running, missing_jobs[], stale[], failed[], never_succeeded[], disabled[], as_of}. A1은 sanitized(error class+짧은 msg만, 전체 traceback DB만). [codex review]
- [ ] **S3+S4 함께 prod 배포 + 라이브검증(원칙22 §6)**: ① 실잡 성공→last_status=ok ② 고의 raise 주입→error+last_error ③ /health failed 노출 ④ scheduler.running=False 모사→healthy=False·missing_jobs ⑤ 머니로직 불변.
- [ ] **S5 — Mac 워치독 폴 모드**(계획 T5): tools 폴 + launchd KeepAlive + 디바운스 + 집계 단일 알림(_notify_mac).
- [ ] (선택) S5b 완료 후 feat/scheduler-watchdog → main 머지, push.
- [ ] (선택) S5a + S5b push to origin(Jino 요청 시).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-wing-chrome-launchd+scheduler-watchdog-S3_20260620.md 읽고 PLAN_scheduler-watchdog S4 이어서 작업해줘
```
