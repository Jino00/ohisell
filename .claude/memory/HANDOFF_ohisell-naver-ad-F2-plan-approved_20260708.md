# 세션 인수인계: ohisell 네이버 광고 — F2(grain 확장+배선3곳) 계획 승인·기록 (opus 계획 세션)
> 저장일시: 2026-07-08 (밤, opus 계획 세션 → sonnet 전환 후 종료)
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로(작업 워크트리, 원칙20 불변): `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/admiring-solomon-b4f056`
- 브랜치: `claude/admiring-solomon-b4f056` — **⚠️작업 전 반드시 `git branch --show-current`로 확인**(워크트리 뒤바뀜 사고 이력 2회).
- ⚠️**이번 세션은 다른 워크트리(`vigorous-goldberg-d67686`)에서 시작됐음** — 세션 cwd는 그 워크트리였지만, 모든 파일 편집은 Edit 도구에 절대경로(`admiring-solomon-b4f056/...`)를 지정해 실제로는 올바른 작업 워크트리 파일이 수정됨(git status로 확인 완료). 새 세션도 처음엔 엉뚱한 워크트리에 배치될 수 있으니 **경로부터 확인하고, 도구 호출 시 반드시 admiring-solomon-b4f056 절대경로 사용**할 것.
- origin과 동기: **미push** (로컬 커밋 4개, 이전 HANDOFF와 동일 — 이번 세션은 코드 변경 없이 문서만 편집). 커밋 `6b30f8f`..`42aaf03`.
- 이번 세션 변경 파일(미커밋): `docs/tracks/active/track_naver-ad-optimization.md`(D-NAO-26 추가) · `docs/PLAN_naver-ad-forecast-expert.md`(§7 F2 세분화) — **다음 세션에서 F2a 구현과 함께 커밋하거나, 문서만 먼저 커밋할지 Jino 확인 후 진행**.
- 테스트: `backend/.venv-test/bin/python3.11 -m pytest -q` (스크래치 격리 venv)
- 백엔드 .env: 이전 세션에 메인 워크트리에서 복사해 넣은 것이 이 워크트리에 남아있음(NAVER_SA_ACCESS_LICENSE 등, gitignored·미커밋) — 재사용 가능.
- prod: sellc.ohitech.co.kr, alembic head는 여전히 `v6w7x8y9z0a1`(F0/F1 마이그레이션 미배포, 변동 없음).
- codex 사용한도: 직전 세션(F1)에서 소진됨(OpenAI "12:39 재시도" 안내) — 이번 세션엔 codex 호출 안 함(계획만). **F2a codex review 시점에 재시도 필요할 수 있음, 먼저 가용 여부 확인**.

## 2. 이번 세션 완료 목록
- ✅ **HANDOFF 정독 + 브랜치 확인**: `.claude/memory/HANDOFF_ohisell-naver-ad-F0a-F1-forecast-done_20260708.md` 읽고 `admiring-solomon-b4f056` 브랜치가 `claude/admiring-solomon-b4f056`(HEAD `42aaf03`)임을 실측 확인 — HANDOFF 기록과 정확히 일치.
- ✅ **F2 코드 조사(구현 없이 계획만, Opus 세션)**: 병렬 Explore 에이전트 2개로 배선 대상 3곳 정독.
  - `forecast_model_builder.py`·`forecast_gate.py`·`forecast_engine.py`·`forecast_scorer.py`(F1 코어 4파일, grain="campaign" 하드코딩 확인) 직접 정독.
  - `models.py`의 `NaverForecastModel`/`NaverForecastDaily` 스키마 확인 — **grain 컬럼이 이미 account/campaign/adgroup/keyword를 지원(String(16)), scope_key String(60)** → F2 grain 확장에 **스키마 변경 불필요**(중요 발견).
  - Explore 에이전트 A: `proposal_pipeline.py`+`proposal_writer.py`+`NaverProposal` 모델 — 배선ⓐ 삽입 지점 확정(`compute_forecast_evidence` 신규 함수 → `proposal_writer.build(forecast_data=None)` optional kwarg, rationale/expected_effect에 병기, 원칙18-8 패턴 그대로).
  - Explore 에이전트 B: `trigger_watch.py`(페이싱 계산 정확한 라인 84-122) + `hourly_pattern.py`(시간대 지수 `compute_bid_weight_recommendations`) — 배선ⓒ 삽입 지점 확정 + 1-a④(당일 가드 미구현 확인)⑤(절대액 floor 미구현 확인) 위치 확정.
  - `budget_allocator.py` 직접 정독 — 배선ⓑ(`find_pre_exhaustion_signals` 신규 함수, `find_budget_exhausted_campaigns` 패턴 재사용) 삽입 지점 확정.
  - `campaign_backfill.py` 재확인 — sentinel(`BACKFILL_SENTINEL_ADGROUP`) vs P0 실단위 행 구분 로직, grain별 소스 라우팅 설계 근거.
- ✅ **Jino 승인 획득(AskUserQuestion)**: ①F2 규모 분할 = **F2a(grain확장)→F2b(배선3곳+1-a④⑤)** 순차, 각각 codex review 게이트 ②구현 모델 = **Sonnet 전환**(CLAUDE.md 원칙12 기본 흐름대로 — F2는 "복잡한 구조변경"이나 계획을 Opus로 이미 확정했으므로 구현은 Sonnet).
- ✅ **D-NAO-26 트랙 기록**: `docs/tracks/active/track_naver-ad-optimization.md`에 F2 착수 승인 + 분할구조 + 배선ⓐⓑⓒ 각각의 불변 경계(입찰산식 D-NAO-19 무변경/marginal ROAS 인과추정 아님/당일가드+절대액floor) 상세 기록.
- ✅ **계획서 §7 세분화**: `docs/PLAN_naver-ad-forecast-expert.md` F2 체크리스트를 F2a/F2b/89K재검증 3단으로 쪼개고 각 파트의 정확한 파일·함수명 기록.
- ✅ **트랙 "다음 액션" 포인터 갱신**: F2a(forecast_source.py)부터 시작하도록 명시.
- 코드 변경 **0줄**(계획 세션) — 다음 세션이 F2a 구현부터 시작.

## 3. 확정된 결정사항 (D-NAO-26, 이번 세션 신규)
- **F2 분할 구현**: F2a(grain 확장) 먼저 완료·codex review·커밋 → F2b(배선ⓐⓑⓒ+1-a④⑤) → 89K 재검증. 번복 없이 이 순서 유지.
- **스키마 변경 없음** — NaverForecastModel/Daily의 grain 컬럼이 이미 4-grain 지원.
- **grain별 데이터 소스(정직 경계)**: campaign=sentinel 행(`__backfill__`, 180일 백필), adgroup=실 P0 행 adgroup_id별 일합산(sentinel 제외), keyword=실 P0 행 keyword_id별(WEB_SITE만, NaverEntity 동기화 규칙 재사용). adgroup/keyword는 이력이 7/04 개시라 게이트가 자연히 대부분 fallback 처리 — 이건 버그가 아니라 설계대로(육성 진행에 따라 창발).
- **신규 SA `forecast_source.py`**: grain→시계열 소싱을 한 파일에 집중(원칙18-6, 3개 SA가 각자 라우팅 중복 금지).
- **배선 3곳 전부 "근거 축 추가"만, 실행 산식 무변경**: ⓐ proposal rationale 병기, ⓑ budget_allocator 정보성 사전경보(marginal ROAS 인과추정 여전히 안 함, D-S3-c 연기 사유 유지), ⓒ trigger_watch 페이싱 기대곡선 정밀화(예측 없으면 선형 폴백).
- **구현 모델 = Sonnet**(계획은 Opus로 이미 확정 완료).
- 방향 임의 변경 금지 — D-NAO-26이 이 승인의 기록.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-ad-forecast-expert.md` | ★계획서, §7에 F2a/F2b/89K 3단 체크리스트 |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙 마스터, **D-NAO-26 신규**(F2 승인+분할+불변경계) |
| `backend/app/services/naver_ad/forecast_gate.py` | F1 기존 — F2a에서 grain 파라미터화 대상(현재 `grain != "campaign": raise` 하드코딩, line 29-30) |
| `backend/app/services/naver_ad/forecast_model_builder.py` | F1 기존 — F2a에서 `_daily_series()`(line 37-47)를 신규 `forecast_source.py` 호출로 교체 대상 |
| `backend/app/services/naver_ad/forecast_scorer.py` | F1 기존 — F2a에서 grain 파라미터화 대상 |
| `backend/app/services/naver_ad/forecast_engine.py` | F1 기존 Harness — F2a에서 `_active_campaign_ids()`(line 26-30)를 adgroup·keyword까지 확장 |
| `backend/app/services/naver_ad/forecast_source.py` | **F2a 신규 파일**(아직 없음) — grain→시계열 소싱 단일화 SA |
| `backend/app/services/naver_ad/proposal_pipeline.py` | F2b ⓐ — `run_daily()`(line 410), 신규 `compute_forecast_evidence()` 추가 대상(~line 474 부근) |
| `backend/app/services/naver_ad/proposal_writer.py` | F2b ⓐ — `build()`(line 217-319), `forecast_data` optional kwarg 추가 대상 |
| `backend/app/services/naver_ad/budget_allocator.py` | F2b ⓑ — 신규 `find_pre_exhaustion_signals()` 추가 대상(기존 `find_budget_exhausted_campaigns` line 42-60 패턴 재사용) |
| `backend/app/services/naver_ad/trigger_watch.py` | F2b ⓒ — `find_pacing_anomalies()`(line 84-122, 특히 103-108 페이싱 산식) 교체 대상, 1-a④ 당일가드도 여기 |
| `backend/app/services/naver_ad/hourly_pattern.py` | F2b ⓒ — `compute_bid_weight_recommendations()`(line 69-101) 시간대 지수, 페이싱곡선 원료 |
| `backend/app/services/naver_ad/anomaly_feed.py` | F2b 1-a⑤ — 절대액 floor 추가 대상 |
| `backend/app/models.py` | `NaverForecastModel`(line 1776-1800)·`NaverForecastDaily`(line 1803-1832) — **변경 불필요**(grain 이미 4종 지원) |

## 5. 알려진 이슈 / 주의사항
- **작업 전 브랜치 확인 필수**(`git branch --show-current` == `claude/admiring-solomon-b4f056`) — 이번 세션도 다른 워크트리에서 시작해 확인 후 절대경로로 작업.
- **미push** — 로컬 커밋 4개 그대로(F1까지). 이번 세션 문서 변경 2개(트랙+계획서)는 **미커밋** — 다음 세션 시작 시 F2a 구현과 함께 커밋할지, 문서만 먼저 커밋할지 Jino 확인.
- codex 사용한도가 F1 세션에서 소진됨(재시도 "12:39" 안내는 시각 표시일 뿐 날짜 아님 — 다음 세션에서 실제 가용 여부 재확인 필요, 여전히 막혀있으면 원칙19 폴백 규칙대로 Claude 자체 판단 기록 후 진행).
- E1(전문가 에이전트) 착수 전 Jino 준비물: 백엔드 `.env`에 `ANTHROPIC_API_KEY`. F2는 불필요.
- adgroup/keyword grain은 이력 부족으로 게이트 통과 모델이 F2a 완료 직후 거의 0개일 것 — **이건 실패가 아니라 설계대로**(정직 경계, 다음 세션에서 "왜 모델이 안 생기지" 하고 당황하지 말 것).

## 6. 다음에 할 작업 (미완료)
- [ ] **F2a 구현부터 시작(Sonnet)**: 신규 `forecast_source.py`(순수 reader SA, `daily_series(db, grain, scope_key, from, to)` + `active_days(...)`) → `forecast_gate`/`forecast_model_builder`/`forecast_scorer`의 `grain=="campaign"` 하드코딩 제거·파라미터화 → `forecast_engine._active_campaign_ids()`를 NaverEntity(status='on') adgroup·keyword까지 확장. TDD(fix전/후 차등) → codex review pass → 커밋 → 트랙/계획서 §7 즉시 갱신(원칙20 보강룰).
- [ ] F2b: 배선ⓐ(proposal_pipeline `compute_forecast_evidence`)ⓑ(budget_allocator `find_pre_exhaustion_signals`)ⓒ(trigger_watch 예측곡선+당일가드) + anomaly_feed 절대액 floor(1-a⑤). 각각 fix전/후 차등 테스트 → codex review → 커밋.
- [ ] 89K 재검증(F2b 후): prod DB 스크래치 사본 재확보 → forecast 마이그레이션 적용 → 전 grain 엔진+배선3곳 실규모 실행.
- [ ] E1: expert_desk 조언자 모드(ANTHROPIC_API_KEY 수령 후).
- [ ] F0b: prod 백필 + 듀얼모드~예측 코드 일괄 배포 + 라이브 self-verify.
- [ ] (Jino 결정, 병행) 관찰모드 개시 — 카나리 캠페인 optimizer='ours'.
- [ ] (미push) 로컬 커밋 4개 + 이번 세션 문서변경 origin push 여부 — Jino 결정 대기.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-naver-ad-F2-plan-approved_20260708.md 읽고, admiring-solomon-b4f056 워크트리에서 브랜치 확인 후 F2a(forecast_source.py 신규+grain 파라미터화) 구현 시작해줘 (Sonnet)
```
