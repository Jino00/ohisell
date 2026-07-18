# PLAN — 클릭 탐침 루프: 환경 조건별 최적 순위 학습 (D-NAO-58)

> 이 시스템을 건드리는 모든 세션은 §0을 먼저 읽으세요. 트랙 `docs/tracks/active/track_naver-ad-optimization.md` D-NAO-58 항목이 결정의 단일 진실 원천입니다.

## §0 방향 고정 (변형 금지 — 변경은 Jino 승인 후 D-N 기록)

### Jino 문제의식 (원문 요지, 2026-07-18)
- "클릭이 너무 안 일어나는 것도 기회손실 — 어떤 기준으로 클릭 안 나면 한 등씩 올려서 클릭 나는 순위로 높이고, 성과 비교해 손실 크면 내리는 판단을 하게 할 수 있어?"
- "바로 전환/장바구니 경유 전환의 고객 패턴을 조사할 수 있잖아."
- "환경(주말/주중·월초말·계절)에 따라 클릭 나는 등수가 다를 수 있다."

### 핵심 개념
고정 밴드(순위 2.5~4)는 **평균적** 이익 최적일 뿐이다. 실제 최적 순위는 **환경별 함수**다. 그러나 저빈도 캠페인은 관찰만으로는 환경별 학습이 불가능(셀당 표본 0). → **능동 탐침**: 클릭이 병리적으로 0인 상태에서 순위를 한 등씩 올려 "이 환경에서 클릭이 살아나는 순위"를 실험으로 알아내고, D-NAO-54 지혜 시스템으로 승격한다. 밴드 철학과 충돌이 아니라 **밴드의 사각지대(밴드 안인데 클릭 0인 병리 상태) 예외 처리.**

### 확정 결정 5개 (트랙 D-58-1~5)
1. **성공 판정 = 선행지표(장바구니 포함)**: score = 즉시구매 + 장바구니 × (상품별 장바구니→구매 전환율). 장바구니는 100% 구매가 아니라 관심 신호이므로 상품별 실측 전환율로 가중.
2. **범위 = ours 전체** (04 아이폰_지문방지 + P_Test 아이패드 파워링크). 파워링크는 클릭0 키워드가 많아 탐침 대상 풍부.
3. **CPC 리스크 = 실시간 관측 + 2단계 되돌림** (차단 아님):
   - 실시간 안전판: 비용 급등 ∧ 즉시판매 0 = 출혈 → 즉시 원위치.
   - D+1 최종 판정: 장바구니 경유 전환(~1일 지연) 합산 후 탐침 이익 여부 확정 → 지혜 승격/기각.
   - 밴드 상한(2.5) 돌파 허용, **BEP 하한·스톱로스는 불변**(안전 바닥).
4. **표본 = 계층적 풀링** (용량 아닌 학습 신뢰도 문제): 거친 축(주말/주중) 먼저 → 표본 충분한 셀만 세분화. `hierarchical_pooling` 재사용.
5. **세분화 주체 = 자동 + 투명보고**: 규칙이 표본 임계 감지 → LLM 판사가 하위 셀 순위패턴 유의성 판정(차이 없으면 안 쪼갬) → 자동 세분화. 근거를 일기·볼트에 기록, Jino 검토·오버라이드(P4 경로) 가능.

### 금지선
- 밴드 상한 돌파는 허용하나 **BEP 하한·스톱로스·킬스위치는 절대 불변**.
- 탐침도 기존 실행 게이트(D-NAO-5·가드레일·킬스위치) 전부 통과 — 탐침 전용 우회 경로 금지.
- 03(MOP) 불가침. 지혜→실행 직접 쓰기 금지(제안만).
- 세분화(학습 구조)는 자동이나 **실행(돈)은 기존 게이트 그대로** — 탐침이 자동 집행되는 건 auto_operate 캠페인 한정.
- 모델 라우팅: 구현=Opus 서브에이전트, 리뷰=Opus(★Fable 금지·5라운드 이내). codex 소급 리뷰 후보(07-23).

## 구조 (승인됨 2026-07-18)

```
클릭 탐침 Agent (기존 시간당 밴드 레인 auto_operator.run_hourly_lane의 예외 확장)
├── trigger_sa        노출有(imp≥임계) ∧ 클릭 N시간 연속 0 ∧ BEP 여유 → "노출-클릭 실패" 감지
├── probe_step_sa     현재 순위 → 한 등 위 +1스텝(기존 _clamp_step ±15%·쿨다운2h·BEP하한 재사용)
│                     환경 스냅샷은 P1 diary가 이미 기록
├── signal_sa (신규)  선행지표 = 즉시구매 + 장바구니×상품전환율 (AD_CONVERSION add_to_cart 수집 확장)
├── revert_sa         2단계: ①실시간 출혈 안전판 ②D+1 최종 이익 판정
└── [P3 지혜 승격]     "환경 E에서 상품 P는 N등에서 클릭 살아남" 계층적 풀링→3회 반복→승격
                      다음 같은 환경 오면 탐침 없이 그 순위 목표
```

## Phase 계획 (각 Phase: 구현→테스트→독립리뷰(Opus,5R)→PR→safe_deploy→라이브 검증)

### CD1 선행지표 데이터층 (signal 재료)
- AD_CONVERSION 수집 확장: 현재 `add_to_cart` 행을 버림(fetch_conversion_daily) → **매출엔 안 섞되** 장바구니를 별도 컬럼/테이블로 수집(직접/간접 분리 유지). BEP·ROAS 회계는 불변(구매만).
- 상품별 장바구니→구매 전환율 산출 SA(주문 데이터 실측, _PRICE_WINDOW_DAYS 창).
- 완료 기준: 04·파워링크 장바구니 실데이터 적재 + 전환율 산출 실측.

### CD2 탐침 트리거·실행층
- trigger_sa: "노출≥임계 ∧ 최근 N시간 클릭 0 ∧ BEP 여유" 판정(임계값은 계획 시 추천→트랙 확정). 기존 hh24 곡선 재사용.
- probe_step_sa: run_hourly_lane에 탐침 분기 — 밴드 안인데 트리거 참이면 한 등 상향 제안(기존 가드레일·쿨다운·BEP하한 통과, approval_source=probe 태그). auto_operate 캠페인만 자동 집행.
- 완료 기준: 트리거 발동·probe 집행이 diary에 probe 태그로 기록됨 실측.

### CD3 되돌림·성과 판정층 (D-58-8~10 확정, 2026-07-18)
- **상태 추적 = change_log 파생(마이그레이션 없음)**: standing probe = `NaverProposal(approval_source='probe_op', executed_change_log_id NOT NULL)` 중 그 유닛의 가장 최근 성공 `update_bid` change_log가 그 probe인 것(이후 다른 변경 있으면 = 이미 해소, 제외). 최근 7일 창만 probe로 취급. 되돌림 값 = 그 change_log `before_value["bidAmt"]`.
- **신규 SA 2개(원칙18 단일책임)**:
  - `probe_signal.py` — `probe_signal_score(db, grain, target, campaign, date_from, date_to)`: 순수 계산. immediate=conv_direct_cnt+conv_indirect_cnt, carts=cart_direct_cnt+cart_indirect_cnt, cart_rate=cart_conversion_rate(상품→캠페인→global 폴백), adjusted_score=immediate+carts×cart_rate, + cost/clk/conv_amt/roas_corrected 반환.
  - `probe_revert.py` — revert_sa: `_standing_probes()`·`run_bleed_valve()`(Stage1)·`run_settlement()`(Stage2)·`_execute_revert()`·`_write_probe_outcome()`.
- **Stage 1 실시간 출혈 밸브**(run_hourly_lane 말미, lazy import — 매시 :20): 당일 standing probe에 hh24 곡선으로 (a)완료시간대 누적비용/now.hour > 정착창 시간당평균(총비용/(7×24))×`_PROBE_BLEED_COST_MULTIPLE(=3)` AND (b)그날 conv_direct_cnt=0(행 부재도 0). 둘 다 → 즉시 되돌림. intraday conv 측정 한계로 사실상 "시간당 3× 비용급등 시 보수적 회수"(되돌림은 완전 가역, 쿨다운 2h 후 재탐침 가능).
- **Stage 2 D+1 정산 판정**(신규 크론 08:55, 일 레인·해석 뒤): age≥1 standing probe에 signal_sa. ①clk=0→되돌림 ②clk>0∧roas≥target→유지(CD4 지혜 후보) ③clk>0∧roas<target∧adjusted<1.0→되돌림 ④clk>0∧roas<target∧adjusted≥1.0→defer(D+2 재판정) ⑤근거부족→defer, age≥3이면 안전 default 되돌림. 결과를 probe execute diary outcome_json["probe"]에 기입.
- **되돌림 집행 초크포인트 경유(우회 금지)**: bid_down 제안(target_bid=before_value, `APPROVAL_SOURCE_REVERT="revert_op"`) → execute()(guardrail·킬스위치·change_log 전량 통과). diary ACTOR_PROBE 재사용, harness 킬스위치 튜플에 revert_op 추가.
- 완료 기준: 탐침 왕복 1회(상향→관측→유지 or 되돌림) 실측(자연 발동 대기, 원칙22).

### CD4 환경별 학습·세분화층 (D-58-11~14 확정, 2026-07-19 — Claude 자동진행, Jino "너의 추천옵션으로 자동진행" 위임)
- 계층적 풀링: 환경 셀(거친 축 시작) × 순위 → 클릭/선행지표 집계. hierarchical_pooling 재사용.
- 세분화 판사: 표본 임계 도달 셀을 다음 축으로 쪼갤 유의성 LLM 판정(P3 판사 재사용). 근거 일기·볼트 기록.
- 지혜 승격 연결: "환경 E → 상품 P 최적 탐침 순위 N" 3회 반복 승격. 다음 같은 환경 진입 시 탐침 생략하고 그 순위 목표.
- 완료 기준: 백필 데이터로 첫 셀 집계·세분화 판정 1회 실측.

#### 확정 설계 (D-58-11~14)
- **D-58-11 스코프 = 지식층만(실행경로 wiring은 CD5 이월).** CD4는 "환경 셀 × 순위 → 클릭 곡선"을 **백필로 학습**하고 사람에게 보이는 지혜로 승격하는 데까지. CD2 탐침이 이 학습을 소비해 **목표 순위를 잡거나 탐침을 생략**하는 실행경로 변경은 **CD5**로 미룬다. 이유(원칙22): 탐침이 아직 자연 발동 0건 → 실행경로 변경은 라이브 검증 불가 → 검증 못 하는 주장 회피. CD4는 **advisory read 함수**(`learned_probe_rank`)만 노출하고 CD2 소비는 안 함.
- **D-58-12 마이그레이션 0 (LESSONS #14 준수).** 학습 상태를 **새 테이블로 저장하지 않는다** — 집계는 `NaverKeywordHourly`(per-hour·avg_rank, 365일 보존)에서 **매번 재계산**(순수 파생). "3회 반복 승격"은 **창 안 ≥3 서로 다른 날 일관 신호**라는 **계산 조건**으로 구현(카운터 영속 없음). 세분화 판정 근거·승격 결과는 **`ops_diary_entries` observe 행**(append-only 로그, 스키마 불변)에 기록해 볼트 열람. `OpsWisdomEntry`는 승격 가시화용으로만 idempotent 기입(선택).
- **D-58-13 환경 축 = day_class 시작(주말/주중/공휴일), 세분화 후보축 = iphone_window·season.** 월초/중/말 전용 헬퍼는 **신설 안 함**(기존 diary env 필드 재사용, `_day_class`·`_season_of`·`_iphone_offset_days`·`_iphone_window` 순수 재사용). 어느 축을 더 쪼갤지는 **세분화 판사(LLM)가 데이터 유의성으로 결정** — 사전 하드코딩 금지(리스크 로그 준수). 순위 밴드 = [1,2)/[2,2.5)/[2.5,3)/[3,4)/[4,∞) (CD2 임계 2.5 정합).
- **D-58-14 구조 = 3 SA + 1 Harness + 1 크론(마이그 0).**
  - **SA1 `probe_cell_aggregate.py`**(순수): `NaverKeywordHourly` 창 → env_cell(=day_class[×iphone_window×season 세분 시]) × rank_band → Σimp/clk/cost·CTR. 희소 셀은 `hierarchical_pooling.shrink`로 거친 env prior 축소추정(EB, **첫 프로덕션 소비자**). 선행지표(cart/conv)는 `NaverAdDaily`에서 env_cell 단위(순위 무관)로 보강. 쓰기 0.
  - **SA2 `probe_cell_segmenter.py`**(LLM 판사 재사용): 표본 임계(Σimp≥`_MIN_CELL_IMP`) 셀에 대해 `expert_llm._invoke_claude`(model=opus·schema)로 "다음 축 세분이 클릭곡선을 유의하게 바꾸는가" 판정 + 신호 명확 시 셀별 최적 rank_band 선정. fail-open(LLM 실패→세분 안 함). 쓰기 0(판정 반환).
  - **Harness `probe_learning_loop.py`**(wisdom_loop 패턴): aggregate→segment-judge→승격 계산→observe 일기 1행 기록(오늘 학습 요약). 스테이지 격리·fail-soft.
  - **크론 `run_naver_probe_learning`** 매일 09:05(정산 08:55 뒤·재계산이라 catch-up 무해). 스케줄러 4곳 등록.
  - **advisory 함수 `learned_probe_rank(db, *, env_cell, now)`** → 승격 조건 충족 셀의 최적 rank_band or None(CD5가 소비 예정, 이번엔 미배선·테스트만).

### CD5 실행경로 wiring (CD4에서 분리 — D-58-11, 미착수)
CD4가 지식층까지 완성. CD5 = 그 지식을 탐침 트리거가 실제로 소비하는 층(원래 CD4 스코프였으나 탐침 자연발동 0건이라 라이브 검증 불가 → 분리).
- **learned_probe_rank 소비**: CD2 `_probe_trigger`(현재 무조건 한 등 상향)가 그 유닛의 env_cell에 승격된 최적 순위밴드가 있으면 → 목표 순위로 상향(or 이미 그 밴드면 탐침 생략). guardrail 전량 통과 유지(우회 금지).
- **이익 가중 승격**: CD4 optimal_band는 순수 CTR argmax라 최상위 순위로 쏠림(이익 스팟밴드 2.5~4와 배치, P3-3). CD5에서 cell_leading_indicator(cart/conv)·roas를 승격 판정에 결합해 "이익 최적 순위"로 교정.
- **완료 기준(원칙22)**: 탐침이 학습된 순위를 실제 목표로 삼는 왕복 1회 실측 — CD2/CD3 탐침 자연발동이 선결(현재 대기 중).

## 리스크·결정 로그
- 탐침이 클릭만 늘리고 전환 0이면 비싼 클릭만 삼 → 되돌림 게이트가 생명선(실시간 안전판이 1차 방어).
- 장바구니→구매 전환율이 낮은 상품은 선행지표 가중이 자동으로 낮아짐(설계상 자정).
- 표본 세분화 순서(어느 축 먼저 쪼갤지)는 데이터 유의성이 결정 — 사전 하드코딩 금지.
- 파워링크는 소재-상품 연결 구조가 쇼핑과 달라 signal grain 확인 필요(CD1에서 실측).

## 미결(계획 시 추천안 제시 → 트랙 확정)
- 트리거 임계: 클릭 0 지속 시간(예: 3시간?), 최소 노출(예: imp 30?).
- 실시간 안전판 손실 상한(예: 탐침 후 시간당 비용이 정착창 평균의 N배 ∧ 판매 0).
- ~~거친 환경축 초기 정의(주말/주중 + 월초/중/말 정도로 시작?).~~ → **해소(D-58-13)**: day_class 시작, 세분 후보축=iphone_window·season, 월초/중/말 헬퍼 신설 안 함(세분화 판사가 데이터로 결정).
