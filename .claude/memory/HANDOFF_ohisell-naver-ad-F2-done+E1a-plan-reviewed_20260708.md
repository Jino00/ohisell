# 세션 인수인계: ohisell 네이버 광고 — F2 전체 완료 + E1a 계획·리뷰 완료 (전문가 데스크 = Ava)
> 저장일시: 2026-07-08 15:40 (Opus 계획·리뷰 세션)
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로(작업 워크트리, 원칙20 불변): `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/admiring-solomon-b4f056`
- 브랜치: `claude/admiring-solomon-b4f056` — **⚠️작업 전 반드시 `git branch --show-current`로 확인**(워크트리 뒤바뀜 이력). 이번 세션 shell cwd는 `silly-dirac-a46f92`였으나 모든 편집은 admiring-solomon 절대경로로 수행됨.
- 테스트: `backend/.venv-test/bin/python3.11 -m pytest -q` (스크래치 격리 venv). 현재 **808 passed**.
- 프론트 tsc: `npx tsc -b --noEmit` (단독 `--noEmit`은 no-op).
- prod: sellc.ohitech.co.kr — SSH 별칭 `sellc.ohitech.co.kr`(user ubuntu), DB `/home/ubuntu/ohisell/backend/ohisell.db`(SQLite). prod alembic head `v6w7x8y9z0a1`(F0/F1/F2 마이그레이션 미배포).
- **prod DB 스크래치 사본 절차**(89K 재검증에서 사용, 세션 종료 시 사라짐): `scp sellc.ohitech.co.kr:/home/ubuntu/ohisell/backend/ohisell.db <scratch>/ohisell_prod_XX.db` → alembic.ini `sqlalchemy.url`을 임시로 그 사본 절대경로에 포인팅 → `.venv-test/bin/python3.11 -m alembic upgrade head` → **즉시 `git checkout backend/alembic.ini` 원복** → 검증 후 사본 삭제. (dotenv override=False라 alembic만 이 트릭 필요.)
- backend/.env: 이전 세션이 메인 워크트리에서 복사(NAVER 크리덴셜, gitignored·미커밋) — 재사용 가능.
- origin 동기: **미push** (로컬 19 커밋, `667ad8f`..`49eb230`). push 여부 Jino 결정 대기.
- codex CLI: 이 세션에서 사용 가능(`codex exec`, ready). claude CLI(-p)는 **로컬 미설치**(npm 설치본 native 바이너리 없음, PATH에 없음) — E1a 구현엔 무관(가짜 reviewer), E1b/실 어댑터 시점에 설치처 필요.

## 2. 이번 세션 완료 목록
- ✅ **F2a 구현+codex review** (D-NAO-27): 신규 `backend/app/services/naver_ad/forecast_source.py`(reader SA, grain별 시계열 소싱 단일화) + forecast_gate/model_builder/scorer의 campaign 하드코딩 제거·grain 파라미터화 + forecast_engine이 adgroup·keyword까지 순회. codex P1 2건(부모-자식 status 캐스케이드 누락=실버그·즉시수정 / 빈 scope_key+keyword 중복합산 방어)+P2 1건 반영. 커밋 `f91bfc7`+`ba46a17`. pytest 762→779.
- ✅ **F2b 구현+codex review** (D-NAO-28): ⓐ `proposal_pipeline.compute_forecast_evidence`→`proposal_writer.build(forecast_data=)` 예측 병기(입찰산식 불변) ⓑ `budget_allocator.find_pre_exhaustion_signals`(신규 `budget_pre_exhaustion` 정보성 제안) ⓒ `trigger_watch.find_pacing_anomalies` 예측곡선(신규 `hourly_pattern.expected_cost_fraction`)+1-a④당일가드+1-a⑤`anomaly_feed` 절대액floor(1000원). codex P2 4건 전부 반영(pred_cost==budget 경계·곡선 분단위 보간·hourly_pattern N+1 캐시·자정 레이스 today 파라미터). 커밋 `235b2f3`~`c519541`. pytest 779→808.
- ✅ **89K 재검증** (D-NAO-29): prod DB scp 사본에 마이그레이션 3개 적용 → forecast_engine+배선3곳 실규모 실행. **크래시 0건**. 부모체인 캐스케이드 필터 실측(on 90,364개→부모까지 on인 30,916개만: campaign29·adgroup451·keyword30,436). N+1 우려 실측 = 30,916 스코프 **46.7초**(크론 예산 내) → **인덱스 불필요 결론**. 전 grain fallback(prod 이력 4일뿐, 설계대로). 사본 즉시 삭제. **F2 트랙 완결**.
- ✅ **E1 설계·계획·리뷰** (D-NAO-30/31/32, 코드 0줄): 아래 3·4·6 참조. `docs/PLAN_naver-ad-forecast-expert.md` §6·§8 확정 + GSTACK REVIEW REPORT. 트랙 파일 D-NAO-30~32 기록.
- 커밋: `717710b`~`49eb230`(E1 문서 6개). 전부 admiring-solomon 로컬.

## 3. 확정된 결정사항 (번복 금지)
- **D-NAO-30/31 LLM 호출**: Anthropic API 아니라 **`claude -p`**(ANTHROPIC_API_KEY 불필요). 레퍼런스 = **AI_office `backend/app/utils/claude_cli.py`**(프로덕션 검증). 확정 호출식: `[which("claude"), "--print", "--output-format", "json", "--model", "opus", "--system-prompt", persona]` + **프롬프트 stdin**(ARG_MAX 회피) + `cwd=/tmp` + `env`에서 ANTHROPIC_API_KEY 제거(OAuth Max plan). 구조화출력=`--json-schema` 플래그 **없음**, 스키마를 프롬프트에 붙이고 응답 `["result"]`에서 regex 추출. **배치 1콜/일**(오늘 제안 전체→평결 배열), **모델=Opus**.
- **D-NAO-32 전문가 = AI_office 기존 직원 Ava(`ohi_ads_media`)**: 신규 에이전트 안 만듦. Ava 역할에 "쿠팡·네이버 매체 운영... 주간 성과 리뷰" **이미 포함**(factory.py:245-251). **분리 아키텍처**: 검토(동기 08:05)는 ohisell claude -p / 지혜(일기→성찰→wisdom)는 Ava 인지 in AI_office. AI_office 실측: `POST /v1/cognition/observe`(agent_id·text·source_type="external_result") 존재, **야간 비동기라 동기 평결 불가** → 검토는 ohisell에서. E1b가 wisdom pull(브리핑 주입)+observe push. AI_office는 `https://os.ohitech.co.kr`:8000.
- **검증 채점(관찰모드)**: 실행 의존 채점 → E2 연기. 지금은 "검증 가능한 예측"만 D+7/14 실측 대조(부분 성적표, 정직 라벨).
- **E1a/E1b 분리**: E1a=ohisell 자족(AI_office·실 claude 무의존, 가짜 reviewer TDD) → E1b=Ava 연동(AI_office쪽 별도 세션 작업 필요).
- **F2 전부 완료**(코드+89K 실규모). 인덱스 추가 불필요 종결.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-ad-forecast-expert.md` | ★계획서 — §6 미확정 해소, **§8 = E1a 상세 task 분해(T1~T9)**, 말미 GSTACK REVIEW REPORT |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙 마스터 — D-NAO-27~32 기록, "다음 액션" 포인터=E1a T1 |
| `/Users/jino/.../AI_office/backend/app/utils/claude_cli.py` | ★claude -p 린 포팅 레퍼런스(다른 레포) |
| `/Users/jino/.../AI_office/backend/app/agents/factory.py:245-251` | Ava(`ohi_ads_media`) 정의·역할 |
| `backend/app/services/naver_ad/learning_loops.py` | 하네스 단계격리 패턴(expert_desk가 따를 형) |
| `backend/app/services/naver_ad/forecast_source.py` 등 F2 산출물 | F2 완료 코드 |
| `backend/app/models.py` (NaverProposal ~1590, NaverLearningState ~1634) | E1a가 붙을 모델 |
| `backend/app/services/scheduler_service.py` | 크론 등록부(08:05 추가 지점) |
| `frontend/src/pages/NaverAdOptimizationConsole.tsx` | 콘솔(평결 배지+Ava 패널 추가 지점) |

## 5. 알려진 이슈 / 주의사항
- **작업 전 브랜치 확인 필수**(admiring-solomon-b4f056). 이번 세션 shell cwd가 silly-dirac였음 — 절대경로 사용.
- **미push 19 커밋**. push는 Jino 결정.
- **E1a 열린 결정 1건(되돌릴 수 있음)**: codex 아웃사이드 보이스가 "prod 이력 4일뿐이니 LLM 검토를 F0b(prod 백필) 후로 연기하라" 주장 → 나는 auto-proceed로 **진행 결정**(검토+총평 유지, 성적표는 정직 라벨로 약화). 근거: Jino 의도(D-NAO-25 논의 파트너)+E1b 지혜 episodic 축적. **Jino가 연기를 원하면 재조정 가능**. PLAN §8 말미 UNRESOLVED DECISIONS 참조.
- claude CLI 로컬 미설치 — E1a는 가짜 reviewer라 무관하나 T3 실 어댑터는 설치처에서만 스모크.
- codex 사용한도: 이 세션 정상 사용. 다음 세션 재확인.

## 6. 다음에 할 작업 (미완료)
- [ ] **E1a T1부터 구현(Sonnet)** — PLAN §8 순서. T1 마이그레이션(2테이블: `naver_expert_review_run` 원장 + `naver_expert_review` verdict child, codex 반영) + 모델(verdict enum에 `insufficient_evidence` 포함, checkable_prediction 선택, verify_date·as_of index, verdict/outcome/source 상수) → T2 briefing_builder → T3 expert_llm 어댑터+ava_reviewer(주입경계·강한 스키마검증) → T4 expert_ledger(record+grade_due_predictions·자문경계 C3) → T5 expert_desk 하네스(빈제안 skip A2) → T6 라우터 → T7 크론08:05 → T8 프론트 → T9 prod사본 e2e+codex. 각 T: TDD→codex→커밋→트랙/§8 갱신.
- [ ] E1b: ava_client(wisdom pull+observe push) + 실 claude 어댑터 스모크. AI_office쪽(Ava wisdom/SOUL read 엔드포인트·인증토큰·CORS) 별도 세션 필요.
- [ ] F0b: prod 캠페인 백필 + F0~F2 마이그레이션 prod 배포 + 라이브 self-verify.
- [ ] E2: 부분 게이트(반자동 전환 결정 후).
- [ ] (Jino 결정) 미push 19커밋 origin push / 관찰모드 카나리 개시.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-naver-ad-F2-done+E1a-plan-reviewed_20260708.md 읽고, admiring-solomon-b4f056 워크트리에서 브랜치 확인 후 E1a T1(마이그레이션 2테이블 + NaverExpertReview 모델)부터 구현 시작해줘 (Sonnet, PLAN §8 순서)
```
