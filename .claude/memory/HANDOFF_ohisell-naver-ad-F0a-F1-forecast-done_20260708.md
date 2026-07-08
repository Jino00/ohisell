# 세션 인수인계: ohisell 네이버 광고 — 예측·전문가 스프린트 F0a+F1 완료 (sonnet 구현)
> 저장일시: 2026-07-08 (오후, sonnet 구현 세션)
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로(작업 워크트리, 원칙20 불변): `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/admiring-solomon-b4f056`
- 브랜치: `claude/admiring-solomon-b4f056` — **⚠️작업 전 반드시 `git branch --show-current`로 확인**(워크트리 뒤바뀜 사고가 2번 있었음). 세션 시작 시 다른 워크트리(`fervent-heyrovsky-7de930`)에서 시작했다가 `EnterWorktree`로 이 경로로 전환함 — 새 세션도 처음엔 다른 워크트리에 배치될 수 있으니 경로부터 확인할 것.
- origin과 동기: **미push** (로컬 커밋 3개: `a566ad8`→`a1deb28`→`178a2fa`→`42aaf03`, 이전 HEAD `6b30f8f`에서 4커밋 진행). push는 사용자 지시 필요.
- 테스트: `backend/.venv-test/bin/python3.11 -m pytest -q` (스크래치 격리 venv, prod 공유 venv 절대 무접촉)
- 백엔드 .env: 이 워크트리엔 원래 없었으나 **이번 세션에 메인 워크트리(Ohiselling 루트)에서 복사해 넣음**(`backend/.env`, gitignored·미커밋) — NAVER_SA_ACCESS_LICENSE 등 크리덴셜 포함, F0a/F1 백테스트에 실제 네이버 API 호출이 필요해서 복사함. 다음 세션도 이 파일이 남아있으면 재사용 가능(워크트리 삭제 시 사라짐).
- prod: sellc.ohitech.co.kr — SSH 별칭 `sellc.ohitech.co.kr`(user ubuntu), DB `/home/ubuntu/ohisell/backend/ohisell.db`. prod alembic head는 여전히 `v6w7x8y9z0a1`(F0/F1 마이그레이션 포함 다수 미배포).
- 스크래치 prod DB 사본: `/private/tmp/claude-501/.../scratchpad/ohisell_prod_f0a.db`(세션 종료 시 사라지는 임시 디렉토리 — 다음 세션에서 필요하면 prod에서 재scp 필요, 아래 §5 참조).

## 2. 이번 세션 완료 목록
- ✅ **F0a: 캠페인 grain 180일 백필 완료** (`docs/PLAN_naver-ad-forecast-expert.md` §7, 커밋 `a566ad8`은 문서만·실제 백필은 스크래치 DB에서 실행, 코드 변경 없음): prod DB scp 읽기전용 사본 확보 → additive 마이그레이션 2개(`w7x8y9z0a1b2`·`x8y9z0a1b2c3`) 적용(alembic.ini를 임시로 스크래치 경로에 포인팅 후 즉시 git checkout 원복 — dotenv override=False라 이 트릭이 alembic 전용으로 필요) → `campaign_backfill.backfill_campaign_daily`로 실제 네이버 `/stats` API 180일 백필: **43캠페인·7,740행·2026-01-09~07-07·cost합 80,335,231원**. 완료기준 2개 충족(①기존 리포트 수치 백필 전/후 byte-identical ②겹치는 10일 재백필로 멱등 확인). pytest 738 passed.
- ✅ **F1: forecast_engine 코어 구현 완료** (커밋 `a1deb28`): 신규 테이블 2개(`naver_forecast_model`·`naver_forecast_daily`, migration `y9z0a1b2c3d4`) + SA 3개(`forecast_gate.py` 활동일게이트+쿨다운·`forecast_model_builder.py` 추세모델·`forecast_scorer.py` MAPE채점+자동강등) + Harness `forecast_engine.py`(3단계 격리: 재백필→모델생성→채점) + 스케줄러 크론 07:50 등록(`scheduler_service.py`). 테스트 21개 신규.
- ✅ **모델 설계 변경 — 백테스트 실증(정직 경계, 중요)**: 계획서 원안 "요일계절성×추세"를 F0a 180일 실데이터로 43캠페인×165일 워크포워드 백테스트한 결과 **요일 계절성 적용이 항상 나이브 베이스라인(어제=오늘)보다 나빴음**(계절성 포함 window=1조차 0.63>나이브 0.61) — 4주 이력만으로 추정한 요일지수 추정오차가 실신호보다 커서 순노이즈로 작용(캠페인 단위는 요일패턴보다 일별 자기상관이 훨씬 강함). **계절성 제거 + 짧은 창(3일) 지수감쇠(decay=0.6)로 전환**해 나이브 대비 **clk MAPE -5.1%·cost MAPE -3.0% 개선** 확인(완료기준① 충족). 전체 29개 on캠페인 일일 모델 재생성 **0.078초**(완료기준② 충족). 요일 패턴은 데이터 축적 후 F2/v2 재검토 대상으로 문서화.
- ✅ **codex review 완료** (원칙19, 커밋 `178a2fa`): 2건 지적 중 [P1] "backfill fetcher가 timeIncrement 미지정→집계 응답 우려"는 **반박·기각**(F0a 라이브 백필이 정확히 43캠페인×180일=7,740행을 반환 — 집계됐다면 ~43행이어야 함, 이 함수는 P2-S1에서 이미 실측 문서화된 코드라 새 버그 아님). 후속 대화 라운드는 **codex 사용한도 소진**(OpenAI "12:39 재시도" 안내)으로 진행 불가 → 트랙 폴백 규칙대로 이 반박은 Claude 자체 판단으로 기록. [P2] "campaign_backfill 삭제범위가 응답 날짜만 커버→무활동일 stale sentinel 잔존"은 **동의·즉시수정**: forecast_engine이 이 함수를 매일 재실행하며 위험이 실사용 경로로 노출됐기 때문. 삭제조건을 요청 전체 `[date_from,date_to]`로 확장, fix전 상태로 되돌려 회귀 재현 확인 후 재적용(원칙14), 이 SA의 첫 단위테스트 3개 신규 추가.
- ✅ **문서 갱신 커밋**: `a566ad8`(F0a 완료 기록) `42aaf03`(F1 완료+모델재설계+codex review 기록) — 트랙·계획서·claude-progress.txt 전부 갱신.
- 최종 테스트: **762 passed**(신규 24개: F1 SA/Harness 21개 + campaign_backfill 회귀 3개, 회귀 0).

## 3. 확정된 결정사항
- **F1 모델 v1 = 무계절성 단기추세(3일 지수감쇠 0.6)** — 계획서 원안(요일계절성)에서 백테스트 실증 근거로 변경. 요일 계절성은 F2/v2 재검토 대상(데이터 축적 후).
- codex P1(timeIncrement) 지적 **기각** — 근거: F0a 라이브 실측(요청 범위와 정확히 1:1 매칭하는 행수), 해당 코드는 이번 세션이 작성한 것이 아니라 P2-S1에서 이미 실측 문서화됨(`docs/references/22`).
- codex P2(stale sentinel) 지적 **수용·수정 완료**.
- 방향 임의 변경 금지 — F2 착수는 Jino 승인 후 D-N 기록 원칙 그대로.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-ad-forecast-expert.md` | ★계획서(F0~F2, E1~E2), §7 체크리스트에 F0a/F1 완료 상세 기록됨 |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙 마스터(D-NAO-1~25), F0a/F1 완료 bullet + "다음 액션" 갱신됨 |
| `backend/app/services/naver_ad/forecast_gate.py` | 신규 — 활동일 게이트(14일 80%)+강등 쿨다운 |
| `backend/app/services/naver_ad/forecast_model_builder.py` | 신규 — 추세 지수감쇠 모델(모듈 docstring에 백테스트 근거 상세) |
| `backend/app/services/naver_ad/forecast_scorer.py` | 신규 — MAPE 채점+자동강등 |
| `backend/app/services/naver_ad/forecast_engine.py` | 신규 Harness — 3단계 조합, 스케줄러가 호출 |
| `backend/app/services/naver_ad/campaign_backfill.py` | 이번 세션 버그수정(삭제범위 확장) — F0에서 이미 존재하던 SA |
| `backend/alembic/versions/y9z0a1b2c3d4_*.py` | F1 신규 테이블 2개 마이그레이션 |
| `backend/app/services/scheduler_service.py` | `run_naver_forecast_engine_job` 07:50 등록 |
| `backend/tests/test_naver_forecast_*.py` (4개) | F1 신규 테스트 21개 |
| `backend/tests/test_naver_campaign_backfill.py` | 신규 — campaign_backfill 첫 테스트 3개(fix 회귀 포함) |

## 5. 알려진 이슈 / 주의사항
- **작업 전 브랜치 확인 필수**(`git branch --show-current` == `claude/admiring-solomon-b4f056`) — 워크트리 뒤바뀜 사고 이력 2회.
- **미push** — 로컬 커밋 4개 쌓여있음(`6b30f8f`..`42aaf03`). push는 Jino 지시 필요.
- prod에는 F0/F1 마이그레이션·코드 전체가 **미배포**(head `v6w7x8y9z0a1`). F0b(prod 백필+배포) 단계에서 일괄 배포 필요.
- **스크래치 prod DB 사본은 세션 종료와 함께 사라지는 임시 디렉토리에 있었음** — 다음 세션에서 F2 백테스트/검증이 필요하면 `scp sellc.ohitech.co.kr:/home/ubuntu/ohisell/backend/ohisell.db`로 재확보 필요(방법: F0a 세션 절차 그대로 — SSH 별칭 sellc.ohitech.co.kr, additive 마이그레이션 적용은 alembic.ini 임시 포인팅 트릭 사용).
- codex 사용한도가 이번 세션 중 소진됨(OpenAI, "12:39 재시도" 메시지) — 다음 codex 호출 시 여전히 막혀있을 수 있음, 재시도 또는 시간 경과 확인 필요.
- E1(전문가 에이전트) 착수 전 Jino 준비물: 백엔드 `.env`에 `ANTHROPIC_API_KEY`. F2는 불필요.
- 이연 항목(트랙 1-a ④⑤): trigger_watch 당일 가드·anomaly_feed 절대액 floor — F2 배선 ⓒ(trigger_watch 예측곡선 배선) 시 함께 처리 예정.

## 6. 다음에 할 작업 (미완료)
- [ ] **⚠️F2 착수 전 Opus 전환 권장** — 그룹(990)·키워드(~90K) grain 확장 + 3개 기존 모듈(proposal_pipeline·budget_allocator·trigger_watch) 배선은 CLAUDE.md 기준 "3개 이상 파일 구조변경+신규 Harness 성격"에 해당(이번 세션 마지막에 Jino에게 확인 메시지 보냈으나 세션이 archive로 끝나 답변 못 받음 — 새 세션에서 먼저 확인).
- [ ] F2: grain 확장(무제한 모델 수) + 배선 ⓐ(proposal_pipeline 예측축) ⓑ(budget_allocator 사전신호) ⓒ(trigger_watch 예측곡선 페이싱) + 89K 재검증 + 트랙 1-a ④⑤ 처리
- [ ] E1: expert_desk 조언자 모드(ANTHROPIC_API_KEY 수령 후)
- [ ] F0b: prod 백필 + 듀얼모드~예측 코드 일괄 배포 + 라이브 self-verify
- [ ] (Jino 결정, 병행) 관찰모드 개시 — 카나리 캠페인 optimizer='ours'
- [ ] (미push) 로컬 커밋 4개 origin push 여부 — Jino 결정 대기

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-naver-ad-F0a-F1-forecast-done_20260708.md 읽고, admiring-solomon-b4f056 워크트리에서 브랜치 확인 후 F2(grain 확장+배선 3곳) 착수 전 Opus 전환 여부부터 확인해줘
```
