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

- [ ] F0a 로컬 사본 캠페인 백필(백테스트 원료)
- [ ] F1 forecast_engine 코어 + 백테스트 + codex review
- [ ] F2 grain 확장 + 배선 ⓐⓑⓒ + 89K 재검증 (+ 트랙 1-a ④⑤ 처리)
- [ ] E1 expert_desk 조언자 모드 + 콘솔 뷰 + codex review
- [ ] F0b prod 백필 + 배포 + 라이브 self-verify (원칙22)
- [ ] E2 부분 게이트 (보류 — 반자동 전환 결정과 동기)

> 매 Phase: 구현 → codex review pass → 트랙/이 문서 §7 즉시 갱신(원칙20 보강 룰).
