# PLAN — 네이버 SA 예측 엔진 + 전문가 데스크 (D-NAO-24/25)

> 작성: 2026-07-08 (fable 설계 세션, Jino 구조 승인 "진행하자")
> 트랙: `docs/tracks/active/track_naver-ad-optimization.md` (필독 — D-NAO-24/25가 이 계획의 근거)
> 선행 상태: 듀얼모드 6-Phase 완료(테스트 738 pass) + prod 89K 실데이터 재검증 완료. 브랜치 `claude/admiring-solomon-b4f056`.

---

## §1 목표 (What & Why)

**① 예측 엔진 (D-NAO-24)** — MOP 러닝 엔진 아키텍처 이식: 매일 재생성되는 예측 모델층.
- Why: 지금 시스템은 진단(과거)+estimate(현재)뿐, "내일" 축이 없음. Jino 지시 = 육성으로 클릭을 키운다는 전제 하에 예측층을 **지금** 구축("패배주의를 버리자").
- 실측 근거: MOP도 Keywords 30,250 대비 ML모델 40개 — 모델 grain은 유닛/캠페인급, 키워드 산출은 플래닝 변환(ref 24 §3). 즉 캠페인 grain부터 즉시 가동 가능.
- **모델 개수 무제한(D-NAO-24 보강)**: 유일한 문지기는 데이터 충분성 게이트. grain 사다리(계정→캠페인→그룹→키워드) 전 층에서 게이트 통과 엔티티 전부 개별 모델. 개수는 창발.

**② 전문가 데스크 (D-NAO-25)** — 24시간 상주 광고 전문가 에이전트(LLM).
- Why: 시스템의 진단·제안·성적표를 읽고 논평·반박·개선제안, Jino와 논의 창구. 원칙19 codex 토론 패턴의 운영 상설화.
- (b) 부분 게이트 지향: 반자동 단계에서 Jino가 유형 단위 위임 스위치를 켠 것만 전문가 합의+가드레일로 자동 승인. 위임 스위치 자체는 영구 Jino 게이트(D-NAO-5 불변).
- "37년 경력"의 실체 = 페르소나가 아니라 축적 구조: 판단 전건 기록→D+7/14 검증→적중률 성적표.

## §2 구조 (승인된 도표)

```
                        Jino (다이얼 + 위임 스위치 + 최종 게이트)
                          ↕ 논의
                 [전문가 데스크: briefing_builder → expert_reviewer → expert_ledger]
                          ↕ 읽기·논평
 수집(07:30~:40) → 예측엔진(07:50: forecast_gate → model_builder → scorer)
                          ↓ naver_forecast_daily
 진단(보드7) → 플래닝(08:00 proposal_pipeline, 예측 결합) → 전문가 검토(08:05)
                          ↓
 플라이트(execution_harness 승인 게이트) → 학습루프(08:10) + 예측 채점(익일)
```

| Agent | Harness | SA (단일 책임) |
|---|---|---|
| 예측 엔진 | `forecast_engine` (크론 07:50) | `forecast_gate`(게이트 판정·승격/강등) / `forecast_model_builder`(일일 모델 재생성+예측) / `forecast_scorer`(예측vs실측 채점·자동 강등) |
| 전문가 데스크 | `expert_desk` (08:05 + 트리거 이벤트) | `expert_briefing_builder`(결정적 브리핑 조립, LLM 아님) / `expert_reviewer`(LLM 평결: 동의/부분동의/기각+근거) / `expert_ledger`(판단 기록→검증→성적표) |

기존 배선 강화 3곳(F2): ⓐ proposal_pipeline에 "내일 예측" 근거 축 ⓑ budget_allocator 사전 소진 예측 신호 ⓒ trigger_watch 페이싱을 선형 경과시간 → 예측 시간대 곡선(hourly_pattern×일예측) 대비로 격상.

## §3 데이터 · 스키마 (additive만, 원칙: 기존 컬럼 무변경)

- `naver_forecast_model` — 모델 원장: grain(account/campaign/adgroup/keyword), scope_key, gate_status(active/fallback/demoted), params_json, trained_at, 정확도 롤업(최근 MAPE).
- `naver_forecast_daily` — 예측치: target_date, grain, scope_key, 예측(clk/cost/cpc/conv_amt보정), 실측 백필, 오차. (예측→실측 백필이 forecast_scorer의 원료)
- `naver_expert_review` — 전문가 원장: proposal_id(nullable — 제안 무관 개선제안도 있음), verdict, reasoning, improvement_suggestions, created_at, verify_date, outcome.
- 위임 스위치: 계정 설정(기존 naver_campaign_settings 패턴의 계정 레벨) — `expert_delegated_types` JSON. E2에서.

## §4 Phase 상세

### F0 — 캠페인 grain 백필 (예측 학습 데이터 확보) — 선행 필수
- D-NAO-17 실측 활용: `/stats` 730일·92일 청크·**캠페인 grain만** 가능. `campaign_backfill` SA 이미 존재(sentinel, 이중계상 필터도 P2-S2에서 선제 수정됨).
- F0a: prod DB 사본에 로컬 백필 90~180일 → **F1 백테스트 원료**. F0b: 배포 시 prod 실행.
- 완료기준: 사본에서 백필 N일 적재 + 기존 리포트/진단 수치 불변(이중계상 회귀 테스트).

### F1 — forecast_engine 코어 (캠페인 grain 즉시 가동)
- 모델 v1 = **요일 계절성 + 최근 추세 가중이동평균** (경량·순수 파이썬·해석 가능). 화려한 모델 금지 — scorer 성적표가 v2 교체 근거를 만들 때까지.
- forecast_gate v1 임계값(초기 상수, 성적표로 튜닝): 최근 14일 중 활동일 ≥80% 등 — MOP 게이트의 우리 번안. 게이트 통과 수를 콘솔 KPI로.
- forecast_scorer: 익일 실측 백필→모델별 MAPE→**최근 성적 불량 모델 자동 폴백 강등**(원칙14 셀프체크 내장).
- 크론 07:50 등록. 신규 테이블 2개 마이그레이션.
- **완료기준(확인 방법)**: ①prod 사본 실데이터 walk-forward 백테스트 — 43캠페인 × 90일+ 이력으로 예측 MAPE 산출·나이브 베이스라인(어제=내일) 대비 우위 확인 ②전체 모델 일일 재생성 소요 실측 ③단위테스트+codex review pass.

### F2 — grain 확장 + 기존 배선 3곳
- 그룹(990)·키워드(~90K) 게이트 승격 경로 개통. 정직 경계: 키워드 grain 이력은 7/04 개시라 백필 불가 — 게이트가 자연히 잡아주고, 육성 진행만큼 자동 확대(D-NAO-24 취지 그대로).
- 배선 ⓐⓑⓒ 각각 fix 전/후 차등 테스트(듀얼모드 스프린트 검증 패턴 재사용).
- **완료기준**: 89K 스케일 성능(사본 재검증 패턴) + 배선 3곳 차등 테스트 + ⓒ는 리플레이 시뮬로 오탐률 개선을 수치로 확인(Phase 4 때 200캠페인 리플레이 하네스 재사용).

### E1 — expert_desk 조언자 모드 (D-NAO-30/31 반영)
- briefing_builder: 진단·오늘 pending 제안 **전체**·트리거·성적표·예측vs실측 → 구조화 브리핑(결정적, LLM 아님).
- expert_reviewer: 페르소나(system-prompt)+브리핑 → **claude -p 배치 1콜** → 평결 배열 JSON(제안별 {동의/부분동의/기각+근거} + 시스템 전체 하루 총평 1건). 스키마는 프롬프트로 강제·응답에서 추출. 시스템과 불일치 시 근거 필수(원칙19). **LLM 호출은 주입가능 경계**로 격리(로컬 claude 없이도 하네스·ledger·콘솔 전부 TDD, 실제 어댑터만 CLI 설치처에서 실측). 모델=Opus.
- expert_ledger: naver_expert_review 기록 + NaverLearningState 성적표 upsert + 콘솔 "전문가 데스크" 뷰(제안 카드 평결 배지 + 성적표).
- **완료기준**: prod 사본 실제안으로 (가짜 reviewer) e2e + ledger 기록 + 콘솔 렌더 + 평결 스키마 위반 0. 실제 claude -p 어댑터는 CLI 설치·인증된 호스트에서 별도 실측(1콜 왕복 + 평결 배열 파싱 성공).

### E2 — 부분 게이트 배선 (보류 게이트: 반자동 전환과 동기)
- `expert_delegated_types` 위임 스위치(Jino 전용 UI) + execution_harness `OPEN_ACTIONS` 연동.
- **착수 조건**: 관찰모드 성적 확인 후 Jino가 반자동 전환을 결정할 때. 그 전엔 설계만 수용(코드 골격 E1에 포함하되 스위치 항상 OFF).

## §5 기존 흐름과의 관계 · 리스크

- **승계 큐(관찰모드 개시 등)와 직교** — 이 스프린트가 관찰모드 결정을 대체하지 않음. 오히려 E1이 되면 제안마다 전문가 의견이 붙어 관찰모드 판단 재료가 풍부해짐.
- 이연 항목 연동: 트랙 1-a ④(trigger_watch 당일 가드)·⑤(anomaly 절대액 floor)는 F2-ⓒ 배선 시 함께 처리.
- 리스크: ①모델 과신 — scorer 강등 + 예측은 "근거 축 추가"일 뿐 입찰 산식(D-NAO-19)은 불변 ②전문가 환각 — 브리핑 밖 수치 인용 금지 프롬프트 + 스키마 강제 + 성적표 공개 ③백필 이중계상 — 기존 필터 회귀 테스트로 방어.

## §6 미확정 (착수 시 실측/결정 — 추정 금지)

1. ~~LLM 호출 경로: 백엔드에서 Anthropic API 직호출~~ **확정(D-NAO-30/31, 2026-07-08)**: `claude -p` 서브프로세스 호출, **ANTHROPIC_API_KEY 불필요**. 레퍼런스=AI_office `backend/app/utils/claude_cli.py`(프로덕션 검증). 확정 호출식: `[claude, "--print", "--output-format", "json", "--model", "opus", "--system-prompt", persona]` + **프롬프트 stdin**(ARG_MAX 회피) + `cwd=/tmp` + 인증은 `env`에서 `ANTHROPIC_API_KEY` 제거(OAuth Max plan). 구조화출력=스키마를 프롬프트에 붙이고 응답 `["result"]`에서 regex로 JSON 추출(`--json-schema` 플래그 없음). **배치**: 오늘 제안 전체를 1콜에 담아 평결 배열 수신(하루 1~2콜). **모델=최고(Opus)**. 남은 실측: 배포 호스트(sellc)에 claude 설치·로그인·PATH, 정확한 `--model` 문자열.
2. 게이트 임계값 초기 상수 — F1 백테스트로 실측 후 확정.
3. ~~전문가 페르소나 이름·톤~~ **확정(D-NAO-32)**: 신규 페르소나 불요 — 전문가 = **AI_office 기존 직원 Ava(`ohi_ads_media`)**. 역할에 네이버 매체 운영 이미 포함(factory.py:245-251). 페르소나=Ava SOUL(AI_office에서 pull). 아키텍처=분리(검토는 ohisell claude -p / 지혜는 Ava 인지 in AI_office, `observe` push + wisdom pull). E1a(ohisell 자족)→E1b(Ava 연동).
4. 브리핑 토큰 예산 — E1 착수 시 실측.

## §7 체크리스트

- [x] **F0a 로컬 사본 캠페인 백필(백테스트 원료) — 완료 2026-07-08 (admiring-solomon-b4f056)**: prod DB scp 읽기전용 사본(스크래치, prod 원본 무접촉) → additive 마이그레이션 2개 적용(alembic.ini 임시 포인팅→git checkout으로 즉시 원복, 이 워크트리엔 dotenv override=False라 alembic만 별도 처리 필요했음) → `campaign_backfill.backfill_campaign_daily` 실제 네이버 `/stats` API로 180일 백필(43캠페인, 2026-01-09~07-07, 7,740행, cost합 80,335,231원 — SHOPPING 29캠페인/53.2M, WEB_SITE 12캠페인/27.2M, BRAND_SEARCH 2캠페인/0원). **완료기준 둘 다 충족**: ①실단위 기존 리포트 수치(07-04~07-07, `metrics_aggregator.aggregate`) 백필 전/후 byte-identical(sentinel 필터 정상 작동, 이중계상 없음) ②겹치는 10일 구간 재백필로 멱등 교체 확인(재실행 후에도 총 7,740행 유지, 중복 없음). 전체 pytest 738 passed(회귀 0, 코드 변경 없이 기존 SA만 실행). 이 워크트리에 backend/.env 없어 메인 워크트리에서 복사(NAVER 크리덴셜, gitignored·미커밋).
- [x] **F1 forecast_engine 코어 + 백테스트 + codex review — 완료 2026-07-08 (admiring-solomon-b4f056, sonnet 구현)**: 신규 테이블 2개(`naver_forecast_model`·`naver_forecast_daily`, migration `y9z0a1b2c3d4`) + SA 3개(`forecast_gate` 활동일게이트+쿨다운·`forecast_model_builder` 추세모델·`forecast_scorer` MAPE채점+자동강등) + Harness `forecast_engine`(3단계 격리) + 크론 07:50 등록. 테스트 21개 신규(759 passed).
  - **모델 설계 변경(백테스트 실증, 정직 경계)**: 계획서 원안 "요일계절성×추세"를 F0a 180일 실백필 데이터(43캠페인×165일 워크포워드)로 검증한 결과 **요일 계절성 적용이 항상 나이브 베이스라인(어제=오늘)보다 나빴음**(계절성 포함 window=1조차 순수나이브보다 열위: clk MAPE 0.63>0.61) — 4주 이력만으로 추정한 요일지수의 추정오차가 실제 신호보다 커서 순노이즈로 작용(캠페인 단위는 요일패턴보다 일별 자기상관이 훨씬 강함). **계절성 제거 + 짧은 창(3일) 지수감쇠(decay=0.6)로 전환**해 나이브 대비 clk MAPE -5.1%·cost MAPE -3.0% 개선 확인(완료기준① 충족). 요일 패턴은 데이터 축적 후 F2/v2 재검토 대상.
  - **완료기준② 실측**: 29개 on캠페인 전체 모델 일일 재생성 0.078초(크론 07:50 여유 충분).
  - **codex review(원칙19)**: 1건[P1] "backfill fetcher가 timeIncrement 미지정→집계 응답 우려" — **반박·기각**: 이 함수는 F1 이전 P2-S1 스프린트에서 이미 실측 문서화됨(`docs/references/22`, 92일 청크 한도 자체가 daily breakdown 전제)+F0a 라이브 백필이 정확히 43캠페인×180일=7,740행(요청 범위와 1:1, 집계됐다면 ~43행이어야 함)을 반환해 일별 그레인이 실제로 정상 작동함을 실측으로 반증. 후속 대화 라운드는 codex 사용한도 소진(OpenAI 12:39 재시도 안내)으로 진행 불가 — 트랙 폴백 규칙대로 이 반박은 Claude 자체 판단으로 기록(재개 시 재확인 가능). 1건[P2] "campaign_backfill 삭제범위가 응답 날짜만 커버→무활동일 stale sentinel 잔존" — **동의·즉시수정**: forecast_engine이 이 함수를 매일 재실행하며 위험이 실사용 경로로 노출됐기 때문. 삭제조건을 요청 전체 [date_from,date_to]로 확장, fix전 상태로 되돌려 회귀 재현 확인 후 재적용(원칙14), 이 SA의 첫 단위테스트 3개 신규 추가. 최종 762 passed.
  - 코드: 브랜치 `claude/admiring-solomon-b4f056`, 커밋 `a1deb28`(F1 코어)+`178a2fa`(codex fix). **다음 = F2 grain 확장 + 배선 3곳.**
- [ ] **F2 grain 확장 + 배선 ⓐⓑⓒ + 89K 재검증 (+ 트랙 1-a ④⑤)** — 착수 승인 D-NAO-26(2026-07-08, Jino "F2a→F2b 분할"+"Sonnet 전환"). **스키마 변경 없음**(grain 컬럼 기존 존재).
  - [x] **F2a grain 확장** — 완료(2026-07-08, Sonnet, TDD). 신규 `forecast_source.py`(순수 reader SA, grain→시계열 소싱 단일화) → forecast_gate/model_builder/scorer `grain=="campaign"` 하드코딩 제거·파라미터화 → forecast_engine이 NaverEntity(status='on') 활성 adgroup·keyword까지 순회. 소스: campaign=sentinel(`__backfill__`), adgroup=실P0 adgroup별 일합산(sentinel 제외), keyword=실P0 keyword별(WEB_SITE만). **codex review(D-NAO-27)**: P1 2건·P2 1건 — 부모-자식 status 캐스케이드 누락(즉시수정, entity_sync.py 실측으로 실버그 확인)·빈 scope_key 방어+keyword 중복합산 누락(즉시수정)·NaverAdDaily adgroup_id/keyword_id 인덱스 부재로 인한 N+1 스케일 위험(스키마 변경이라 F2a 범위 밖, 89K 재검증에서 실측 후 처리로 의도적 이연). 코드: 커밋 `f91bfc7`(F2a 코어)+`ba46a17`(codex fix). pytest 762→779 passed. **다음 = F2b.**
  - [x] **F2b 배선 3곳 + 1-a④⑤** — 완료(2026-07-08, Sonnet, TDD). ⓐ `proposal_pipeline.compute_forecast_evidence`→`proposal_writer.build(forecast_data=...)` — bid_up/bid_down/negative_keyword/growth_bid_up rationale에 예측치 병기만(입찰산식 D-NAO-19 불변, 커밋 `d9c6f01`). ⓑ `budget_allocator.find_pre_exhaustion_signals`(pred_cost>budget 미소진 사전경보, 신규 `budget_pre_exhaustion` 정보성 제안, 커밋 `235b2f3`). ⓒ `trigger_watch.find_pacing_anomalies` 예측곡선 페이싱(hourly_pattern 신규 `expected_cost_fraction`×오늘 pred_cost, 예측/패턴 없으면 선형 폴백) + 1-a④ 당일 가드(`ad_date != today` → 빈 목록) + 1-a⑤ `anomaly_feed` 절대액 floor(SPEND_ANOMALY_MIN_ABS=1000원, 양쪽 다 미만이면 스킵, 커밋 `95b591c`+`320d79a`). **codex review(GATE PASS, P1 0건·P2 4건)**: 전부 즉시 반영(커밋 `c519541`) — pred_cost==budget 경계(`<`→`<=`), 곡선이 정각에 그 시간 몫을 전부 반영해 선형(분단위)보다 정밀도 낮던 문제(hour-1~hour 사이 분단위 보간), hourly_pattern 조회 N+1(hour별 캐시), find_pacing_anomalies 내부 kst_today() 재계산이 호출자와 자정 경계에서 어긋날 이론상 레이스(명시적 today 파라미터로 run_hourly가 한 번만 계산해 전달). pytest 779→808 passed. **다음 = 89K 재검증.**
  - [x] **89K 재검증** — 완료(2026-07-08, D-NAO-29). prod DB scp 읽기전용 사본(`sellc.ohitech.co.kr:/home/ubuntu/ohisell/backend/ohisell.db`) → additive 마이그레이션 3개(`w7x8y9z0a1b2`/`x8y9z0a1b2c3`/`y9z0a1b2c3d4`, alembic.ini 임시 포인팅 트릭 후 즉시 git checkout 원복) 적용 → forecast_engine+배선3곳 실규모 실행, **크래시 0건**. **부모 체인 캐스케이드 필터(F2a codex fix) 실측 검증**: NaverEntity status='on' 90,364개 중 `_active_scopes`가 부모 전부 on인 30,916개만 통과(campaign 29·adgroup 451·keyword 30,436) — 나머지는 부모가 off인 고아 엔티티, 필터가 실제로 동작함을 확인. **N+1 스케일 실측(F2a codex 이연 항목 해소)**: `forecast_engine.run_daily` 30,916 스코프 46.7초 — 일일 크론(07:50) 예산 내 여유, **인덱스 추가 불필요로 결론**(이연 항목 종결, 스키마 변경 없이 진행). model_builder: 30,916개 전부 `fallback`(정직 경계 — prod는 P0 실단위 이력 7/04 개시 4일뿐이라 14일 게이트 미달, 캠페인 grain sentinel 백필도 F0a가 별도 스크래치 DB에서만 실행돼 prod 실DB엔 부재 — 설계대로, F0b가 이 백필을 prod에 배포하는 별도 작업). budget_allocator/trigger_watch/compute_forecast_evidence 배선 3곳 전부 예측 없음 상태에서 정상 no-op(오탐 0건). 스크래치 사본은 검증 직후 즉시 삭제(prod 미변경).
- [ ] **E1a expert_desk (ohisell 자족) + 콘솔 + codex review** — 상세 §8. Ava 페르소나로 08:05 배치 검토(Opus), 평결 저장·콘솔, 검증가능예측 D+7/14 로컬 성적표. AI_office·실 claude 무의존(주입경계 TDD).
  - [x] **T1 마이그레이션+모델** — 완료(2026-07-09, sonnet, TDD). 신규 마이그레이션 `z0a1b2c3d4e5`(2테이블: `naver_expert_review_run` 원장 + `naver_expert_review` verdict child, run_id/proposal_id FK) + 모델 `NaverExpertReviewRun`/`NaverExpertReview` + 모듈 상수 4종(verdict/outcome/source/run_status, C2). 모델 테스트 7개(run↔verdict FK 링크·재실행=새run 이력보존·commentary NULL proposal_id·reject에도 checkable_prediction 허용·insufficient_evidence 정직평결·컬럼폭 회귀가드). **codex review**: [P1] `verdict String(20)`이 `insufficient_evidence`(21자)를 못 담는 실버그 발견 → `String(24)`로 즉시수정+회귀테스트 추가. P2 2건(status/source 기본값 스타일, created_at nullable 생략)은 기존 `naver_forecast_*` 마이그레이션과 동일 컨벤션이라 근거 명시 기각(원칙19). 실sqlite로 upgrade/downgrade/재upgrade 라운드트립 별도 검증(원칙22, pytest는 create_all이라 alembic 자체는 안 거침). pytest 808→815. 커밋 `c807971`. **다음 = T2 `expert_briefing_builder`**(§8 병렬화 순서상 T2/T3/T4 병렬 가능하나 순차 진행).
- [ ] **E1b Ava 연동** — ava_client(wisdom pull 브리핑 주입 + observe push) + 실 claude 어댑터 스모크. AI_office쪽(Ava 지혜/SOUL read 엔드포인트·인증·CORS, 별도 세션) 준비 후.
- [ ] F0b prod 백필 + 배포 + 라이브 self-verify (원칙22)
- [ ] E2 부분 게이트 (보류 — 반자동 전환 결정과 동기)

> 매 Phase: 구현 → codex review pass → 트랙/이 문서 §7 즉시 갱신(원칙20 보강 룰).

## §8 E1a 상세 구현 계획 (task 분해, D-NAO-30/31/32)

**목표(E1a)**: ohisell 자족 — Ava 페르소나로 매일 08:05 오늘 pending 제안 전체를 claude -p 배치 1콜(Opus)로 검토해 평결을 저장·콘솔 노출 + 반대 시 붙인 "검증 가능한 예측"을 D+7/14에 실측 채점하는 로컬 성적표. **AI_office·실 claude 무의존으로 전부 TDD**(reviewer LLM 호출은 주입가능 경계). 실 claude 어댑터는 CLI 설치처에서 별도 스모크.

**정직 경계**: E1a 페르소나·지혜=로컬 상수/로컬 성적표(Ava 실 SOUL·지혜는 E1b pull). 실행 의존 채점은 E2 연기(관찰모드 미실행). 브리핑 밖 수치 인용 금지 프롬프트 + 스키마 강제(원칙19 환각 방어) + no silent cap(절삭 시 로깅).

### 데이터 (additive 마이그레이션 1개, 2테이블 — codex 아웃사이드 보이스 반영)
- **`naver_expert_review_run`** (배치 run 원장, run당 1행): id, `as_of`(Date, **index**), `model`, `prompt_version`, `briefing_hash`, `raw`(Text — claude 원응답), `usage_json`, `status`(ok/degraded/skipped/failed), `created_at`. → run_id로 verdict를 묶어 provenance·프롬프트버전·재실행 이력 보존(codex: 단일테이블 혼재·history 소실 방지). 재실행=새 run(덮어쓰기 아님).
- **`naver_expert_review`** (평결 child): id, `run_id`(FK run), `as_of`(Date), `proposal_id`(Int nullable FK naver_proposals.id — null=하루 총평, run의 child라 NULL-dedup 문제 없음), `verdict`(str20: agree/partial/reject/**insufficient_evidence**/commentary — codex: 관찰모드 "판단 불가" 정직 평결 추가), `confidence`(Numeric nullable), `reasoning`(Text), `checkable_prediction`(Text nullable — **기각 시에도 선택**, codex: 억지 예측 방지), `pred_target_type`/`pred_target_id`(str nullable), `pred_metric`/`pred_direction`(nullable), `verify_date`(Date nullable=as_of+7/+14, **index**), `outcome`(str12 nullable: correct/wrong/unverifiable/pending), `source`(str: local/ava). verdict/outcome/source는 모듈 상수(C2).
- 성적표: 기존 `NaverLearningState` 재사용(scope="expert", metric="prediction_accuracy") — 채점된 예측만 롤업. **콘솔 노출은 정직 라벨**(codex): sample_n 표시, 임계 미달 시 "표본 축적 중(참고용)" — 정확도%를 competence 신호로 헤드라인 금지.

### SA (단일 책임)
- **SA1 `expert_briefing_builder.build(db, as_of) -> dict`** [결정적, LLM 아님]: 오늘 pending NaverProposal 전체 + 진단보드 요약 + forecast 예측vs실측 최근 롤업 + 최근 trigger 이벤트 + 현재 로컬 성적표 요약 조립(+E1b: Ava 지혜). 페르소나는 여기 안 넣음. 토큰가드: 상한 초과 시 오래된 컨텍스트부터 절삭+로깅. 결정적(같은 입력→같은 브리핑).
- **SA2 `ava_reviewer.review(briefing, *, invoke=_invoke_claude, model="opus") -> dict`**: 브리핑→스키마 지시(제안별 verdict 배열[agree/partial/reject/insufficient_evidence] + 총평; checkable_prediction은 선택)→`invoke`(주입경계: 기본=실 claude, 테스트=가짜). 반환 {verdicts[], commentary, raw, usage}. **강한 스키마 검증**(codex): 누락/중복/여분 proposal_id·무효 verdict·commentary↔proposal 혼동 전부 거부, 위반 시 1회 재시도 후 degraded 기록(조작·환각 보정 금지). 페르소나(system)=E1a 로컬 Ava 상수(charter 충실 stub) / E1b 실 Ava SOUL pull.
- **어댑터 `_invoke_claude(prompt, system, schema, model, timeout)`** [claude_cli.py 린 포팅, cost_guard 미포함]: `[which("claude"), "--print", "--output-format", "json", "--model", model, "--system-prompt", system]` + 프롬프트 stdin + `env`에서 ANTHROPIC_API_KEY 제거 + cwd=/tmp + 재시도(t→×1.5→×2). stdout→`json["result"]`→regex로 JSON 추출. 신규 `app/services/naver_ad/expert_llm.py`(C1: 파일 상단에 출처 주석 — AI_office `backend/app/utils/claude_cli.py`의 린 포팅).
- **SA3 `expert_ledger`**: `record(db, as_of, review)` 평결 저장(멱등; 총평 행은 A1 delete-then-insert dedup)[+E1b observe push] / `grade_due_predictions(db, today)` verify_date<=today & pending 행을 pred_target 엔티티 실성과(naver_ad_daily/forecast_scorer 재사용)와 대조→outcome 설정 + 성적표 upsert. **⚠️C3 자문 경계 불변식**: 이 SA(및 E1a 전체)는 `naver_expert_review`에만 쓰고 `NaverProposal.status`·실행상태는 **절대 건드리지 않는다**(D-3 관찰모드, 전문가는 자문일 뿐) — 경계 테스트로 강제. **정직 노트**: prod 이력 ~4일이라 초기 다수 예측이 `unverifiable`로 채점됨(설계대로, 데이터 축적되며 성숙).

### Harness `expert_desk.run_daily(db, *, today=None)` [learning_loops식 단계격리]
stage1 grade_due_predictions → stage2 briefing_builder.build(as_of=kst_today()) → **stage2.5 빈 제안 가드(A2): pending 0건이면 stage3 skip(claude 미호출, stage_status=skipped)** → stage3 ava_reviewer.review(주입경계) → stage4 expert_ledger.record. 각 단계 독립 try/except, stage_status/errors.

### 배선
- 크론: scheduler_service `generate_expert_desk`(`5 8 * * *`, 08:00 proposal 직후).
- 라우터: `GET /api/naver/ad/expert-reviews?as_of=&proposal_id=` + `/proposals` 응답에 verdict 요약 조인(배지용).
- 프론트(NaverAdOptimizationConsole.tsx): 제안 카드 평결 배지(✓/⚠/✗) + "Ava의 검토" 패널(총평 + 성적표).

### 완료기준(E1a)
prod 사본 실제안으로 (가짜 reviewer) e2e[브리핑→평결배열→ledger→라우터/콘솔, 스키마위반 0] + grade_due_predictions 채점 단위테스트(예측→outcome→성적표) + 전 SA/harness TDD + codex review pass. 실 claude 어댑터는 CLI 설치·인증 호스트에서 1콜 왕복 스모크(별도, E1a 코드 무의존).

### Task 순서 (각 T: TDD RED→GREEN → codex review → 커밋 → 트랙/§7 즉시 갱신)
- **T1** 마이그레이션(2테이블: `naver_expert_review_run` + `naver_expert_review`) + 모델(verdict/outcome/source 상수, `verify_date`·`as_of` index) + 모델 테스트(run↔verdict FK, 재실행=새 run 이력보존)
- **T2** `expert_briefing_builder` + 테스트(**결정성**·**토큰캡 절삭+로깅**; forecast 롤업·trigger 조회 recent-N 바운드)
- **T3** `expert_llm._invoke_claude` 린 어댑터(출처 주석) + `ava_reviewer`(주입경계) + 테스트(가짜 invoke·**스키마위반→1회재시도→degraded 조작금지**)
- **T4** `expert_ledger`(record + grade_due_predictions) + 테스트(**총평 dedup A1**·record 멱등·**grade 4-outcome correct/wrong/unverifiable/pending유지+멱등**·성적표 upsert·**자문경계 C3: NaverProposal 무변경**)
- **T5** `expert_desk` 하네스 + 테스트(단계격리·**빈제안 skip A2**)
- **T6** 라우터 GET /expert-reviews + /proposals 조인 + 테스트
- **T7** 크론 08:05 등록
- **T8** 프론트 배지 + Ava 패널(tsc/build)
- **T9** prod 사본 e2e(가짜 reviewer) + codex review + 트랙/§7 갱신
- (T10 E1b, 별도) ava_client pull/push + 실 어댑터 스모크 + **[→EVAL] reviewer 프롬프트 품질 eval**(실 claude 붙는 시점) — AI_office쪽 준비 후

### plan-eng-review 반영 (2026-07-08, auto-proceed 권장안)
A1(총평 dedup→run테이블로 해소)·C3(자문경계 불변식, P1)·A2(빈제안 skip)·C1/C2(어댑터 출처주석·enum상수)·P1(verify_date index)·테스트 갭(4-outcome·스키마위반·결정성·토큰캡·경계) 전부 위 데이터/SA/하네스/Task에 접힘. **NOT in scope**: E1b(Ava 연동)·E2(위임 게이트)·실 claude eval — 순서상 뒤. **What already exists(재사용)**: 하네스 패턴=learning_loops / claude 어댑터=AI_office claude_cli.py / 성적표=NaverLearningState / 예측채점 패턴=forecast_scorer / 제안=NaverProposal+proposal_pipeline / 크론=scheduler_service / 콘솔=NaverAdOptimizationConsole. **병렬화**: T1→T2·T3·T4(모델 확정 후 3 SA 병렬)→T5(조합)→T6·T8(라우터/프론트 병렬)→T7→T9. **Failure modes**: claude 타임아웃(재시도+degraded)·스키마위반(재시도→미저장, 조작금지)·빈브리핑(skip)·grade 실측결측(unverifiable, 침묵 아님).

### codex 아웃사이드 보이스 반영 (2026-07-08)
**즉시 반영**: ①`insufficient_evidence` 평결(관찰모드 "판단 불가" 정직) ②checkable_prediction 기각 시에도 선택(억지 예측 방지) ③2테이블(run 원장 + verdict child) — provenance·프롬프트버전·재실행 이력 ④강한 스키마 검증(누락/중복/여분 proposal_id·verdict 무효 거부) ⑤성적표 정직 라벨(sample_n, "표본 축적 중") ⑥라우터 조인=**as_of 최근 완료 run**의 평결(최신 의미 명시) ⑦데이터 경계 노트=브리핑은 내 광고지표만(계정 자격증명 無), Max OAuth로 내 Claude 세션에 감(이 세션과 동일 경계) ⑧모델 정확 id·CLI-크론 신뢰성(세션만료/PATH/인터랙티브 프롬프트)은 **E1b 실 claude 붙는 시점 헬스체크로 실측**(E1a는 가짜라 무관). **부분 기각(근거 명시)**: codex "prod 4일이라 LLM 검토 전체를 F0b 후로 연기" → **성적표 과신 우려는 수용(위 ⑤로 약화)**, 그러나 **검토+총평 자체는 유지** — 근거: (a) Jino 명시 의도 D-NAO-25 "전문가와 논의하며 운영"=정량 성적표가 아니라 정성 논의 파트너가 1차 가치, (b) E1b 지혜 축적의 episodic 재료를 지금부터 쌓아야 실행 시작 시점에 이력 존재. 성적표는 데이터 축적되며 성숙(정직 라벨로 허위신호 차단). **이 부분 기각은 Jino 확인 대상**(아래 대화 참조).

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | issues_found | 다수(대부분 접힘) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_open | 10 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **CODEX:** run-table·insufficient_evidence·optional-prediction·honest-scorecard·강한 스키마검증 접힘. LLM 검토 전면 연기는 기각(Jino 의도 + E1b 지혜 episodic 재료 축적 근거).
- **CROSS-MODEL:** Claude(A1/A2/C1-C3/P1)와 codex는 A1(총평 dedup)·정직 게이트에서 합치. codex가 run-table·insufficient_evidence·성적표 과신 우려를 추가로 발굴 → 반영.
- **VERDICT:** ENG CLEARED (auto-proceed per Jino) — E1a 구현 착수 가능(§8 T1~T9, Sonnet). 예약된 되돌릴 수 있는 스코프 결정 1건.

**UNRESOLVED DECISIONS:**
- E1a LLM 검토 스코프: 검토+총평+약화된 성적표로 **진행**(Jino "auto-proceed" 자동결정). codex는 실행/prod 이력 축적(F0b) 전까지 LLM 검토 연기를 주장 — **되돌릴 수 있음**, 지금 연기하려면 알려주세요.
