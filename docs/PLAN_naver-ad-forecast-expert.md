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

### E1 — expert_desk 조언자 모드
- briefing_builder: 진단·제안배치·트리거·성적표·예측vs실측 → 구조화 브리핑(토큰 예산 상한 명시).
- expert_reviewer: 페르소나+브리핑 → 제안별 구조화 평결 JSON(스키마 강제). 시스템과 의견 불일치 시 근거 필수(원칙19 형식).
- expert_ledger + 콘솔 "전문가 데스크" 뷰(제안 카드에 평결 병기 + 전문가 성적표).
- **완료기준**: prod 사본 실제안으로 평결 생성 e2e + ledger 기록 + 콘솔 렌더 + 평결 스키마 위반 0.

### E2 — 부분 게이트 배선 (보류 게이트: 반자동 전환과 동기)
- `expert_delegated_types` 위임 스위치(Jino 전용 UI) + execution_harness `OPEN_ACTIONS` 연동.
- **착수 조건**: 관찰모드 성적 확인 후 Jino가 반자동 전환을 결정할 때. 그 전엔 설계만 수용(코드 골격 E1에 포함하되 스위치 항상 OFF).

## §5 기존 흐름과의 관계 · 리스크

- **승계 큐(관찰모드 개시 등)와 직교** — 이 스프린트가 관찰모드 결정을 대체하지 않음. 오히려 E1이 되면 제안마다 전문가 의견이 붙어 관찰모드 판단 재료가 풍부해짐.
- 이연 항목 연동: 트랙 1-a ④(trigger_watch 당일 가드)·⑤(anomaly 절대액 floor)는 F2-ⓒ 배선 시 함께 처리.
- 리스크: ①모델 과신 — scorer 강등 + 예측은 "근거 축 추가"일 뿐 입찰 산식(D-NAO-19)은 불변 ②전문가 환각 — 브리핑 밖 수치 인용 금지 프롬프트 + 스키마 강제 + 성적표 공개 ③백필 이중계상 — 기존 필터 회귀 테스트로 방어.

## §6 미확정 (착수 시 실측/결정 — 추정 금지)

1. **LLM 호출 경로**: 백엔드에서 Anthropic API 직호출 권장 — `ANTHROPIC_API_KEY` 백엔드 .env 필요(Jino 제공). 모델·호출당 비용은 착수 시 실측 후 일일 비용 상한 설정.
2. 게이트 임계값 초기 상수 — F1 백테스트로 실측 후 확정.
3. 전문가 페르소나 이름·톤 — Jino 결정(재미 요소).
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
  - [ ] **89K 재검증**(F2b 후): prod DB 스크래치 사본 재확보→forecast 마이그레이션 적용→전 grain 엔진+배선 3곳 실규모 실행(crash/perf/오탐률, Phase4 리플레이 패턴). F2a codex review에서 이연된 NaverAdDaily adgroup_id/keyword_id 인덱스 부재(N+1 스케일 위험)도 이 단계에서 실측 후 필요 시 처리.
- [ ] E1 expert_desk 조언자 모드 + 콘솔 뷰 + codex review
- [ ] F0b prod 백필 + 배포 + 라이브 self-verify (원칙22)
- [ ] E2 부분 게이트 (보류 — 반자동 전환 결정과 동기)

> 매 Phase: 구현 → codex review pass → 트랙/이 문서 §7 즉시 갱신(원칙20 보강 룰).
