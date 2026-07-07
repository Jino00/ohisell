# 세션 인수인계: 네이버 광고 트랙 — 듀얼모드 스프린트 Phase 2·3 완료(growth_sweeper + budget_allocator/anomaly_feed)
> 저장일시: 2026-07-08
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- **작업 워크트리(불변, 원칙20)**: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/admiring-solomon-b4f056` (브랜치 `claude/admiring-solomon-b4f056`, 미push)
- 실행 명령어: 백엔드 `cd backend && uvicorn app.main:app --reload --port 8000`(스크래치 격리 venv — prod/공유 venv 절대 무접촉). 이번 세션 pytest는 `/tmp/naver_s3b_venv/bin/python -m pytest`(python3.14 venv, 이전 세션이 만든 스크래치 venv 재사용 — `backend/.venv-test`도 존재하나 이번엔 안 씀) — `cd backend`에서 실행해야 `app` 모듈 임포트됨(워크트리 루트에서 실행하면 `ModuleNotFoundError: No module named 'app'`).
- 프론트: 이번 세션은 프론트 무변경(백엔드 전용, Phase 2·3 둘 다).
- prod: `ssh sellc.ohitech.co.kr`, pm2 `ohisell-backend`(8001) — 이번 세션은 **prod 무배포**(스크래치 DB 카나리만).

## 2. 이번 세션 완료 목록
- ✅ **듀얼모드 스프린트 Phase 2(growth_sweeper) 완료**(커밋 `85a1135`):
  - `backend/app/services/naver_ad/growth_sweeper.py`(SA, 신규) — 전 활성 WEB_SITE 키워드(naver_entity status=on)를 로컬로 전수 스윕(광고 API 無, `bid_simulator.pooled_rpc`/`affordable_ceiling` 재사용)해 "현재입찰 << 경제성상한" 갭 내림차순 후보 산출. 공개 상수 `WEB_SITE`, `ESTIMATE_BUDGET=200`, `STOP_LOSS_CLICK_MULTIPLE=LOW_CLICK_THRESHOLD`, `GROWTH_PROPOSAL_CAP=50`.
  - `backend/app/services/naver_ad/proposal_pipeline.py`에 `compute_growth_sims()` 추가(갭 상위 200개만 estimate) + `_precompute_aggregates()`에 `campaign_type` 필터 옵션 추가 + `_make_target_roas_resolver`/`_fill_predicted_clicks` 공용 헬퍼로 리팩터(compute_bid_sims와 중복 제거).
  - `backend/app/services/naver_ad/proposal_writer.py`에 `growth_bid_up` 제안유형(방향 up만) + D-NAO-20 스톱로스 rationale 부착.
  - **codex review 2라운드, 2건 발견·즉시수정(둘 다 fix-전후 차등테스트로 회귀 재현 확인 후 재적용)**: ①`_precompute_aggregates()`가 전 캠페인유형(SHOPPING 등)을 계정 Bayesian prior에 섞어 클릭0인 WEB_SITE 신규 키워드가 근거 없는 제안을 받을 수 있음 → WEB_SITE 스코프 집계 분리 ②non-ours 캠페인이 estimate 예산을 선점해 ours 캠페인 후보가 밀려남 → ours 필터를 예산 슬라이스보다 먼저 적용.
  - 테스트 17개 신규(`test_naver_growth_sweeper.py` 9개 + pipeline/writer 확장 8개).
- ✅ **듀얼모드 스프린트 Phase 3(budget_allocator + anomaly_feed) 완료**(커밋 `c39f7fd`):
  - `backend/app/services/naver_ad/budget_allocator.py`(SA, 신규) — 오늘(`kst_today()`, hourly_snapshot 실시간) 캠페인별 최신 스냅샷에서 `cost≥daily_budget`(실측 비교)인 예산 소진 캠페인을 찾고, `compute_growth_sims()["all_candidates"]`(estimate 재조회 없이 재사용)를 결합해 "소진 && 이익보장 잔존볼륨 존재"인 것만 `budget_up` 신호로 채택. marginal ROAS 인과추정은 하지 않음(D-S3-c 연기 사유 유지).
  - `backend/app/services/naver_ad/anomaly_feed.py`(SA, 신규, 경량) — `freshness_partial_load`(최근 7일 baseline 대비 as_of 행수 비교, S3a codex 연기분 해소) + `spend_anomalies`(전일 대비 급증≥2배/급감≤0.5배).
  - `proposal_writer.py`에 `budget_up`(Confirm 게이트, ours만)/`anomaly`/`anomaly_freshness`(정보성, 전 캠페인 대상) 제안유형 추가.
  - **codex review, 2건 발견·즉시수정(fix-전후 차등테스트)**: ①`spend_anomalies`가 `today_cost.items()`만 순회해 캠페인이 완전히 중단(오늘 행 자체 없음)된 경우를 놓침 → `today_cost`/`prior_cost` 합집합 순회로 수정 ②rationale이 "오늘/어제"를 하드코딩(run_daily의 as_of는 확정치 어제라 오해 소지) → 실제 ISO 날짜(`as_of`/`prior_date`) 반환·표기로 수정.
  - 테스트 15개 신규(`test_naver_budget_allocator.py` 6개 + `test_naver_anomaly_feed.py` 9개) + pipeline/writer 확장 12개.
- ✅ **라이브 검증(원칙22, 두 Phase 공통)**: prod DB 스크래치 사본은 아직 미확보(89K 실 엔티티 데이터 부재) — 3,000건 합성 WEB_SITE 키워드 스크래치 DB 카나리로 대체. Phase 2: 로컬 스윕 0.05s, estimate API 콜 정확히 예산(200) 이내 1회 완주, `GROWTH_PROPOSAL_CAP` 준수, 재실행 dedup 정상. Phase 3: 위 카나리에 오늘자 예산소진 스냅샷(daily_budget=500,000원 전액 소진)+전일대비 10배 급증 캠페인 추가 → `budget_up` 1건(성장후보 1,318건 재사용, gap 합계 282,780원, estimate 재조회 없음) + `anomaly`(급증) 1건 정상 생성, 재실행 시 3개 신규 유형 전부 dedup 확인.
- ✅ 전체 pytest 스위트 **626 → 643(Phase2) → 666(Phase3) passed**. `test_account_brief_singleton_created_once_per_day` 1건은 KST/UTC 자정 경계 타이밍 이슈로 pre-existing 무관 flaky(git stash로 base commit에서도 재현 확인, 이번 세션 스코프 밖이라 미수정).
- ✅ 문서 갱신 완료: `docs/PLAN_naver-ad-S3b-dual-mode.md` §7(Phase 2·3 상세 완료 기록), `docs/tracks/active/track_naver-ad-optimization.md`(체크리스트+다음액션+진행단계), `docs/TRACKS.md`(진행률 요약), `claude-progress.txt`(최상단 블록), 두 개의 자동 메모리 항목(`naver-ad-dual-mode-sprint.md`, `MEMORY.md`).
- ✅ **커밋 3개**(전부 브랜치 `claude/admiring-solomon-b4f056`, **미push**):
  - `dd1bc52` "docs: S3b Phase 1 HANDOFF 저장" — 이전 세션이 남긴 미커밋 문서 정리.
  - `85a1135` "feat: Phase 2 growth_sweeper".
  - `c39f7fd` "feat: Phase 3 budget_allocator + anomaly_feed".

## 3. 확정된 결정사항 (번복 금지)
- **비교 기준 = MOP Pro**, **D-NAO-22/23 6-Phase 구조**(트랙 파일 정본) — 변경 없음, 이번 세션도 그대로 따름.
- **growth_sweeper처럼 특정 campaign_type(WEB_SITE)에 스코프된 SA는 공유 집계 헬퍼를 그대로 재사용하면 안 됨** — 다른 campaign_type 데이터가 Bayesian prior에 섞여 근거 없는 결과가 나올 수 있음(`_precompute_aggregates(campaign_type=...)` 패턴으로 항상 스코프 명시).
- **API 예산으로 후보를 자르는 로직은 반드시 최종 대상 필터(optimizer='ours' 등)를 먼저 적용한 뒤 자를 것** — 순서를 반대로 하면 무관 후보가 예산을 선점한다.
- **두 날짜 집합을 비교하는 로직은 두 집합의 합집합을 순회할 것** — 한쪽만 순회하면 "완전히 사라진" 케이스(값이 아예 없어짐, 예: 캠페인 완전 중단)를 놓친다.
- **사람이 읽는 rationale 텍스트에 "오늘/어제" 같은 상대적 시점 표현 금지** — harness가 호출하는 실제 날짜(run_daily의 as_of는 확정치 어제)와 어긋나 오해를 부를 수 있음, 항상 실제 ISO 날짜 표기.
- **budget_allocator/anomaly_feed 모두 marginal ROAS 인과추정을 하지 않는다**(D-S3-c 연기 사유 유지) — 예산 증액이 실제로 얼마나 더 벌게 해줄지 예측하지 않고, "이미 확인된 이익보장 볼륨이 예산 캡에 막혀 있다"는 사실 관측만 제공. 실행은 D-NAO-5 영구 Confirm 게이트.
- **anomaly_feed는 진단 성격이라 optimizer 무관 전 캠페인 대상**(diagnosis 보드와 동일 취급, D-NAO-13 예외) — 반면 `budget_up`은 실행 후보(Confirm 게이트)라 optimizer='ours'만.
- **다음 Phase 순서 불변**: Phase 4(trigger_watch) → Phase 5(execution_harness골격+change_log) → Phase 6(learning_loops). 방향 임의 변경 금지.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/naver_ad/growth_sweeper.py` | ★Phase2 — 전 활성 WEB_SITE 키워드 로컬 스윕 SA |
| `backend/app/services/naver_ad/budget_allocator.py` | ★Phase3 — 예산소진+잔존볼륨 결합 신호 SA |
| `backend/app/services/naver_ad/anomaly_feed.py` | ★Phase3 — 경량 freshness/소진급변 SA |
| `backend/app/services/naver_ad/proposal_pipeline.py` | Harness — `compute_growth_sims`/`compute_budget_signals`/`compute_anomalies` 추가, `run_daily`에 4개 신규 stage(`growth_sweeper`/`budget_allocator`/`anomaly_feed` stage_status) |
| `backend/app/services/naver_ad/proposal_writer.py` | `growth_bid_up`/`budget_up`/`anomaly`/`anomaly_freshness` 빌더 + `build()` 시그니처 확장 |
| `docs/PLAN_naver-ad-S3b-dual-mode.md` | ★방향 고정 문서 — §7 체크리스트에서 Phase 진행상황 추적(Phase1~3 완료 표시됨) |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙 정본(D-NAO-1~23) |

## 5. 알려진 이슈 / 주의사항
- **89K 실규모·prod 데이터 라이브 검증은 여전히 미실시**(Phase 1·2·3 공통 한계) — prod DB 스크래치 사본(scp)을 확보하면 growth_sweeper/budget_allocator가 실제 규모(~89,274 키워드)에서 API 예산·소요시간을 다시 확인할 가치가 있음. 지금까지는 3,000건 합성 데이터로만 검증(선형 확장 가정 — 0.05s×30≈1.5s 예상이지만 실측 아님).
- **`test_account_brief_singleton_created_once_per_day`는 pre-existing flaky**(KST/UTC 자정 경계, `account_brief_singleton`이 `date.today()`를 쓰는데 SQLite `func.now()`가 UTC 저장이라 자정 근처에 실행하면 어긋남) — git stash로 base commit(Phase1 이전)에서도 재현 확인. 다음 세션에서 고칠 거면 `date.today()` → `kst_today()`로 교체가 유력한 수정안(미검증, 이번 세션 스코프 아님).
- **`test_naver_proposal_pipeline.py`/`test_naver_proposal_writer.py`는 이제 상당히 커짐**(Phase 2·3 테스트가 계속 같은 파일에 추가됨) — Phase 4부터는 신규 SA별 전용 테스트 파일(`test_naver_trigger_watch.py` 등)을 만들고, harness 연동 테스트만 기존 pipeline 파일에 추가하는 방식 유지 권장.
- 입찰가 70~100,000원·10원 단위, 자동집행 영구 사람 게이트(D-NAO-5) 등 기존 가드레일 전부 그대로 유효.
- `proposal_pipeline.run_daily()`의 `budget_allocator`/`anomaly_feed` 스테이지는 `diag`(BEP 진단) 실패 여부와 무관하게 항상 실행되지만, **그 결과가 실제 제안으로 저장되는 건 `diag.get("boards")`가 있을 때뿐**(proposal_writer.build 호출 자체가 그 조건 안에 있음) — BEP 데이터가 없어 diagnosis가 완전히 실패하면 anomaly_feed가 계산한 신호도 이번 회차엔 버려짐(다음 회차에 diag가 성공하면 재계산돼 문제없음, 그러나 알아둘 특성).

## 6. 다음에 할 작업 (미완료)
- [ ] **Phase 4 — trigger_watch**(조건발동 즉시 제안, D-NAO-3-②, 계획서 §4-Phase4): 매시 :05 `snapshot_naver_ad_hourly` 직후 실행하는 신규 Harness — 소진 이상(페이스 대비)·CPC 급등·순위 이탈(rank_target 이탈) 감지 → 해당 항목만 즉시 제안 생성+Slack. 정시 스케줄이 아니라 조건발동. **쿨다운**(동일 대상 재발동 최소 간격, MOP "빈번 변경 비권장" 이식) — 임계값은 백필/실측 분포에서 도출(자의적 상수 금지, 근거 없으면 "문서에서 확인 안 됨" 명시 후 Jino 질문). 완료기준: 과거 hourly 스냅샷 리플레이로 발동 시뮬 — 발동 건수·오탐 육안 검수, 쿨다운 동작 테스트.
- [ ] Phase 5 — execution_harness 골격 + change_log(기본 OFF)
- [ ] Phase 6 — learning_loops(estimate 보정·전환성숙·시간대 즉시+제안 성적표 인프라)
- [ ] Phase 6 완료 후 → 직전 HANDOFF(S3a) 승계 큐(관찰모드 개시·15일 베이스라인 재대조·push 결정·트랙파일 귀속 정리·campaign_target_resolver②·S26 질문)
- 매 Phase 공통: 전체 pytest 통과 유지 + codex review(fix-전후 차등테스트로 회귀 재현 검증하는 패턴 이번 세션에 확립됨, 계속 사용) + 라이브 검증(원칙22, 가능하면 prod DB 스크래치 사본으로 전환) + 트랙 갱신.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

`.claude/memory/HANDOFF_ohisell-naver-ad-S3b-phase3-budget-anomaly_20260708.md` 읽고, admiring-solomon-b4f056 워크트리에서 트랙 → `docs/PLAN_naver-ad-S3b-dual-mode.md` 순으로 읽은 뒤 듀얼모드 스프린트 Phase 4(trigger_watch)부터 구현해줘.
