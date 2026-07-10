# 내부 구현 현황 매트릭스 — 네이버 SA 광고 최적화 (fable 3자 대조용 원료)

> 작성: 2026-07-10 (읽기전용 조사, 코드 수정 없음). 워크트리: `suspicious-shaw-b5315f` (브랜치 `claude/naver-ad-execution-loop-6cc75b`).
> 방법: ref 24/25/26 정독 + PLAN_naver-ad-execution-loop.md·PLAN_naver-ad-forecast-expert.md·track_naver-ad-optimization.md 정독 + 코드 spot-check(파일 존재·함수 시그니처·크론 등록·라우터 엔드포인트 실측). 코드 근거 없이 "구현됨" 판정 없음(원칙22).
> 상태값 6종만 사용: `✅prod가동` / `✅코드완료`(라이브 미검증) / `🔶부분` / `⏳계획`(§7에 있음) / `📋승계큐`(§8) / `❌없음`.
> **주의**: X0-2(카나리 캠페인 지정)가 Jino 지시로 연기됨("프로그램 완성되면 정하자") — 이 때문에 실쓰기 경로 다수가 "코드완료"에서 멈춰 있고 "라이브 왕복 검증"은 구조적으로 전부 미실시 상태다. 이건 결함이 아니라 의도된 순서(§0-4 완료 정의 참조).

---

## A. MOP 기능 인벤토리 대비 우리 구현 (ref 24 기준)

| MOP 기능 | MOP 스펙 요약 | 우리 구현 상태 | 근거 |
|---|---|---|---|
| ① 데이터 수집 | 네이버 API 매일 수집(D-1 확정) + 당일 페이싱 | ✅prod가동 | `naver_ad_daily`=07:30 크론, `naver_hourly_snapshot`=매시 :05, `naver_entity`=07:35, `naver_search_term_daily`=07:40 (`backend/app/services/scheduler_service.py` L858-863 `_ensure_default_states`) |
| ② 러닝 엔진(일일 ML 예측모델, 14일 게이트) | 매일 재생성, grain=유닛/캠페인급(ref24 §3: 모델40개 vs 키워드30,250) | ✅prod가동(구조) — 데이터 성숙 진행중 | `forecast_engine.py` 크론 07:50(L865) → `forecast_gate.py`(활동일 게이트)+`forecast_model_builder.py`(3일 지수감쇠)+`forecast_scorer.py`(MAPE 자동강등), `forecast_source.py`가 campaign/adgroup/keyword 3-grain 소싱. D-NAO-29 실측(2026-07-08): prod 30,916 스코프 전부 `fallback`(prod 실단위 이력 짧아서 — 설계대로, 코드 결함 아님) |
| ③ 플래닝 엔진(예측→입찰가 변환) | 시간대×입찰가 ~350 경우의수 이산열거(ref25 외부자료) | 🔶부분 | 예측치는 `proposal_pipeline.compute_forecast_evidence`가 rationale에 병기(D-NAO-28 배선ⓐ)할 뿐 — 실제 "입찰가로 변환"은 `bid_simulator.py`의 경제성상한 산식(D-NAO-19)이 예측과 무관하게 별도 담당. 시간대 차원 계획 자체 없음(X2 미착수) |
| ④ 플라이트(시간대 버킷 자동집행, 하루5회+) | Basic<5회, Pro 5회+시간단위 | ❌없음 | `flight_loop.py`/`response_curve_builder.py`/`pacing_controller.py` 파일 자체 부재(확인: `ls backend/app/services/naver_ad/` 목록에 없음). 현재는 일 1회 제안(08:00)뿐, 자동집행 루프 없음 |
| 목표 설정(방향 택1, 수치 불가) | 클릭/전환/ROAS/다중 극대화 방향만, target ROAS 수치 지정 불가 | ✅코드완료(우위) | `bep_calculator.py` `target_roas = bep_roas × 공격성배수`(D-NAO-2) — 정밀 수치 타겟 |
| 고급설정 6종(Avg CPC/CPA·Top Impression·Max CPC·Rank/Budget Boosting·CPC Rebooting) | Pro 6종 | ❌없음 | 해당 명칭 기능 코드 전무(grep 무결과, 추정 아님) |
| 순위유지(Rank Target, Max CPC 상한 포기) | 검색5~20분/쇼핑2시간 순위추종 | 📋승계큐(G4) | 코드 부재 확인. `docs/PLAN_naver-ad-execution-loop.md` §8 목록 |
| 예산 민감도 한계효용 곡선 | 시각화 | 📋승계큐(G8) | `budget_allocator.find_pre_exhaustion_signals`가 신호는 내지만(D-NAO-28) 곡선 형태 시각화 없음. §8 |
| 경쟁 심화도(DEA 0~1 지수) | 자료포락분석 | ❌없음 | 해당 코드 없음(DEA/포락 검색 무결과) |
| Budget Opt 크로스미디어(네이버+카카오+구글+메타) | 제안만, 총예산 재분배 | 📋승계큐(G9) | 네이버 단일 매체만. §8(전략상 후순위 명시) |
| 기여도 분석(non-last-click, 6개월 lookback) | — | 📋승계큐(G7) | `actual_revenue.py`는 last-click 실주문 대조만. §8 |
| 이상감지(URL/UTM/소재 설정오류 자동모니터+제외) | 매일 9:30 알림 | ❌없음(스펙 자체가 다름) | 우리 `anomaly_feed.py`는 급증/급감·freshness 탐지이지 URL/UTM 설정오류 탐지가 아님 — 동일 기능명이나 검출 대상 상이 |
| (참고) 성과 급변 이상감지 — MOP엔 없는 우리 기능 | — | ✅prod가동 | `anomaly_feed.py`(급증2배/급감0.5배+절대액floor)+`trigger_watch.py`(페이싱이탈+CPC급등), 크론 매시 :07 |
| 대용량 Raw 데이터(CSV, 90/60일) | Pro 전용 | ❌없음 | 콘솔 export 기능 코드 없음 |
| 매체별 최적화 지원(네이버·카카오·구글·메타·크리테오 일부) | — | 🔶부분 | 네이버만. 캠페인별 관리주체(optimizer='ours'/'MOP'/'없음', D-NAO-13)는 있으나 매체 확장 자체는 없음(쿠팡은 별도 트랙) |
| 연동 게이트(14일 80%+최근3일+일1건전환) | warm-up 조건 | ✅코드완료 | `forecast_gate.py` 활동일 비율 게이트(우리 버전 임계값) |
| warm-up 폴백(네이버 추천입찰가 자동운영) | Pro 전용 | 🔶부분 | `forecast_gate`가 `fallback` 상태를 표시·`forecast_model_builder`가 풀링 기대치로 대체하지만, "네이버 추천입찰가로 자동 운영"은 안 함(자동집행 자체가 아직 없어 실효 낮음 — MOP과 폴백 메커니즘이 다름) |

행 수: **17행**.

---

## B. 갭 G1~G9 현재 판정 (ref 25 대비 오늘자 업데이트)

> ref 25는 X1a 착수 **직전**(2026-07-10 이른 시각)에 작성됐고, 오늘(같은 날) X1a T1~T6 + prod 배포가 전부 끝났다. 따라서 "당시"와 "오늘"이 같은 날이지만 실질적 진전이 있다.

| # | 갭 | 당시 판정(ref25) | 오늘 판정 | 남은 것 (1줄) |
|---|---|---|---|---|
| G1 | 입찰 집행 자체 | 쓰기 코드 0줄, 승인게이트 골격만 | 🔶 제외키워드(add_negative_keyword) 쓰기 어댑터+harness+콘솔 승인/실행 API+E2 위임스위치까지 **코드 완료**, prod 배포 완료 | 카나리 지정 후 라이브 왕복 검증(§7 완료기준①) 1건만 남음 + 입찰(bid_up/down)·정지재개는 X1b 전혀 미착수 |
| G2 | 시간대 차원 플래닝 | 없음(bid_simulator 일단위 1회) | 변화 없음 | X2 T1 `response_curve_builder` 미착수(파일 부재 확인) |
| G3 | 당일 반영 루프 | 다음날 08:00 제안뿐 | 변화 없음 | X2 T3 `flight_loop` 미착수 |
| G4 | 순위 관측·유지 루프 | estimate 하루1회 제안시점만 | 변화 없음 | X 스코프 밖으로 확정(D-NAO-36), §8 승계 큐 |
| G5 | 소재(ad) grain | 없음 | 변화 없음 | §8 승계 큐, 미착수 |
| G6 | 캠페인 생성 보조 | 없음 | 변화 없음 | §8 승계 큐, 미착수 |
| G7 | 기여도 분석 | 없음 | 변화 없음 | §8 승계 큐, 전략상 후순위 |
| G8 | 예산 민감도 곡선 | 신호만 있음(곡선 없음) | 변화 없음 | §8 승계 큐, 곡선화만 남음 |
| G9 | 크로스미디어 | 네이버만 | 변화 없음 | §8 승계 큐, 전략상 후순위 |

행 수: **9행**. G1만 오늘 실질 진전(설계·코드 100%, 라이브 검증만 잔여). G2~G9은 ref25 작성 시점과 동일.

---

## C. 논문 TOP5 채택 항목 구현 현황 (ref 26)

| 순위 | 기법 | 배정 Phase | 현재 상태 | 근거 |
|---|---|---|---|---|
| ① | GRM 응답곡선 + 이분법(min-pacing) 컨트롤러 | X2 T1(`response_curve_builder`)+T2(`pacing_controller`) | ⏳계획(미착수) | 두 파일 모두 `backend/app/services/naver_ad/` 목록에 부재 확인 |
| ② | DHEB 계층 EB 축소추정 | X3 T1 | ⏳계획(미착수) | `bid_simulator.pooled_rpc`(키워드→그룹→캠페인→계정 3단 풀링)가 **이미 존재**(전신, D-NAO-19 이식) — 하지만 이건 X3가 계획하는 "전 지표(CTR/CVR/RPC)로 일반화"가 아니라 RPC(매출/클릭) 한 지표만의 계층 풀링. X3 확장은 미착수 |
| ③ | EBaReT LP 쌍대 최적입찰 코어 | §8 승계 큐(X 완료 후) | 📋승계큐 | 코드 없음. X2 성적 보고 후 채택 결정 예정(계획서 §8) |
| ④ | Bayesian AdComB 분포예측+정수계획 | §8 승계 큐(X 완료 후) | 📋승계큐 | 코드 없음. 동일 |
| ⑤ | GAVE 페널티 점수 S=min{(ROAS/BEP)^γ,1}×매출 | X3 T2 | ⏳계획(미착수) | `proposal_scoreboard.py`(제안 성적표)가 outcome(improved/declined/neutral) 판정은 하지만 GAVE 식의 γ 다이얼·점수 공식은 미구현. 파일에 `γ`/`gamma`/`min(` 페널티 식 검색 무결과 |

행 수: **5행**. TOP5 전체 미착수 — X2·X3가 아직 시작 안 됐으므로 당연한 결과(§7 체크리스트와 일치).

---

## D. 우리만 있는 것 (MOP에 없거나 약한 것) — 코드 근거 필수

| 항목 | 상태 | MOP 비교 | 코드/크론 근거 |
|---|---|---|---|
| forecast_engine(예측 모델층) | ✅prod가동(구조) | **MOP도 있음**(차이점: MOP는 유닛/캠페인급 40개 고정 grain, 우리는 계정→캠페인→그룹→키워드 사다리 전체에서 게이트 통과 엔티티 전부 개별 모델=개수 무제한 창발, D-NAO-24) | 크론 07:50, `forecast_engine.py`+`forecast_gate/model_builder/scorer/source` 4-SA |
| Ava 전문가 데스크(E1a 검토+E2 위임) | ✅prod가동 | MOP엔 LLM 검토 에이전트 없음(순수 규칙/모델 기반) — **우리만의 기능** | 크론 08:05(`generate_expert_desk`), `expert_desk.py`+`ava_reviewer.py`(model=opus, L63 `_REVIEW_MODEL`)+`expert_llm.py`+`expert_briefing_builder.py`+`expert_ledger.py`. 라이브 확인(X0-1, 2026-07-10 14:14 KST): run id=2 status=ok, 평결 44행 |
| BEP-ROAS 손익 통합(수치 타겟) | ✅코드완료 | MOP은 방향 택1만, 수치 지정 불가(ref24 §10-2, 우리 명시 우위) | `bep_calculator.py`, `naver_product_bep` 테이블, `GET /api/naver/ad/bep` |
| change_log 전건 기록 + D+7/14 채점 학습루프 | ✅prod가동 | MOP은 조정 이력·before/after 미노출(성과결과만, ref24 §10-1) — **우리만** | `naver_change_log` 모델(before/after 컬럼), `proposal_scoreboard.py`(outcome 판정), 크론 08:10(`run_naver_learning_loops`) |
| 진단보드 7종 | ✅prod가동 | 🔶MOP도 있음(차이점: MOP=예산민감도·DEA경쟁심화도, 우리=출혈/굶는승자/확장버킷/쇼핑BEP/제외후보/3단분류/악순환 — 손익경계 기반 설계) | `account_diagnosis.py`(7함수: bleeding_keywords·starving_winners·expansion_bucket·shopping_group_bep·exclusion_candidates·keyword_triage·vicious_cycle_flags), `GET /api/naver/ad/diagnosis` |
| 검색어→제외키워드 제안(확장버킷 승격 포함) | ✅prod가동 | MOP 쇼핑 제외키워드는 Pro 최대 70개 **수동 등록 한도**일 뿐, 자동 진단·승격 제안 여부는 "문서에서 확인 안 됨"(ref24 §12) | `account_diagnosis.exclusion_candidates`, `growth_sweeper.py`(확장버킷 검색어 승격, D-NAO-18③) |
| 예측 성적표(scorer 자동강등, 공개) | ✅prod가동 | MOP scorer/모델 강등은 내부·비공개(ref25 §4 "검증루프" 행) — **우리는 공개**(콘솔 노출) | `forecast_scorer.py`(MAPE 채점→자동강등), `GET /api/naver/ad/expert-scorecard` |
| 듀얼모드(수익성방어+볼륨성장) | ✅prod가동 | **MOP도 있음**(SA 이지모드 "균형운영" vs "성장운영" 2택, ref25 §1) — 동일 프레임, 차이는 우리가 3단 공격성×4캠페인모드로 더 세분화(D-NAO-2) | `NaverAdOptimizationConsole.tsx` 다이얼, `campaign_target_resolver.py` |
| E2 위임 스위치(전문가 합의+가드레일 통과 시 부분 자동승인) | ✅코드완료(라이브 미검증) | MOP엔 LLM 합의 기반 위임 개념 자체가 없음 — **우리만** | `delegation_gate.py`(run_gate), `naver_account_settings.expert_delegated_types`, `GET/PUT /api/naver/ad/settings/expert-delegation` |

행 수: **9행**.

---

## E. 미해결·미진 항목 요약 (2026-07-10 기준, 정직하게)

### 카나리 미지정 → 라이브 미검증 (코드는 완료, §7 기준 "완료"는 아님)
- **X1a 완료기준①**: 카나리 캠페인에서 제외키워드 1건 실집행 → 네이버 API 재조회 반영 확인. X0-2(카나리 지정)가 Jino 지시로 연기됨("프로그램 완성되면 정하자") — 구조적으로 지금 불가능.
- **E2 위임 스위치 자동실행**: 코드·테스트 완료(TDD+codex 3라운드 PASS), 실제 자동승인 라이브 동작은 카나리 이후.
- **T6 정보성 pending 경량화 완료 확인**: 다음 크론(2026-07-11 08:00/08:05, 즉 이 조사 시점 기준 "내일")에서 절삭 로그 0건·Ava 평결=실행형 전건을 확인해야 함 — 아직 미확인(prod 배포는 2026-07-10 저녁 완료).

### X1b 정지·재개 → 입찰 개방 (§7 전부 미체크)
- 정지·재개(userLock) 개방 미착수.
- 입찰(bid_up/bid_down/growth_bid_up) 개방 미착수 — `naver_sa_writer.py`에 userLock·bidAmt 함수 자체가 없음(현재는 제외키워드 3함수뿐, spot-check 확인).
- 가드레일(±15%·쿨다운·일일 변경건수 상한·스톱로스·BEP 미달 증액금지·클램프) "전부 코드로 실효화"는 미착수 — 일부(스톱로스 절대액 D-NAO-20, ±15% 원칙)는 산식에 존재하나 "전부 실효화"라는 완료기준 자체가 미달성.

### X2 당일 플라이트 루프 (§7 전부 미체크)
- `response_curve_builder`·`pacing_controller`·`flight_loop` 3개 SA/Harness 전부 파일 부재 확인.

### X3 두뇌 고도화 (§7 전부 미체크)
- DHEB 계층 풀링 일반화(전 지표), GAVE 페널티 점수+γ 다이얼 — 둘 다 미착수.

### §8 승계 큐 전체 목록 (X 완료 후 검토 예정, 현재 전부 미착수)
1. G4 순위유지 루프
2. G6 캠페인 생성 보조
3. G5 소재(ad) grain
4. G8 예산 민감도 곡선(시각화)
5. G7 기여도 분석(non-last-click)
6. G9 크로스미디어(쿠팡 결합 교차 검토 포함)
7. 논문③ EBaReT LP 쌍대 코어
8. 논문④ Bayesian AdComB 분포예측+정수계획
9. 백엔드 API 인증 도입 여부(T5 codex 지적 — 위임 스위치 등 제어면 엔드포인트가 현재 무인증, 폭발반경은 유계로 수용 중이나 자동실행 범위가 넓어지기 전 Jino 결정 필요)

### 그 외
- **E1b(Ava 실 연동)**: AI_office가 별도 레포/프로젝트라 이 워크트리에서 작업 불가. wisdom pull + observe push + 실 claude 어댑터 스모크 전부 미착수.
- **expert_ledger.record 멱등 레이스**: codex 연기 항목 — select-before-insert라 동시 쓰기 시 이론상 중복 가능(현재 단일 스케줄러 프로세스라 도달 불가). X2에서 크론이 늘어나기 전 유니크 인덱스 재검토 예정.

---

## 보고 (조사자 노트)

- **파일 경로**: `/private/tmp/claude-501/-Users-jino-Library-Mobile-Documents-com-apple-CloudDocs-1Personal-AI-Program-Ohiselling--claude-worktrees-suspicious-shaw-b5315f/a8d3640b-c260-4771-84ec-f48a0ead7f8c/scratchpad/internal_inventory.md`
- **표별 행 수**: A=17행, B=9행(G1~G9), C=5행(TOP5), D=9행.
- **자신 없는 판정(⚠️ 재확인 권고)**:
  1. **A표 "이상감지(URL/UTM 설정오류)" = ❌없음** 판정 — 재확인 완료(`grep '^def '` 결과 `anomaly_feed.py`에는 `freshness_partial_load`·`spend_anomalies` 2함수뿐, URL/UTM/소재 오류 관련 코드 전무). 확신도 상향.
  2. **B표 G1 "🔶" 판정** — X1a는 제외키워드(add_negative_keyword) 하나만 개방했고 입찰·정지재개는 전혀 안 열렸다. "쓰기 코드 0줄"에서 "1개 액션 타입 코드완료+배포"로 바뀐 것은 명확하지만, G1 전체(입찰 포함)로 보면 여전히 초기 단계라 🔶 표기가 과대평가처럼 보일 수 있음 — 갭 크기(★★★)는 그대로 두되 "진전은 있으나 미미"로 해석 권고.
  3. **D표 "듀얼모드=MOP도 있음"** — ref25 §1의 "SA 이지모드 균형운영/성장운영 2택"이 D-NAO-22 듀얼모드와 정확히 동일 개념인지는 원문(MOP UI 실측)까지 재확인하지 못했고 ref25 기록에만 의존함.
  4. **C표 ②(DHEB) 상태** — `pooled_rpc`가 이미 RPC 한 지표에 대해 계층 풀링을 하고 있어 "완전 미착수"로 보기엔 애매한 경계선이다. X3의 계획 범위(CTR/CVR/RPC 전 지표 일반화)와 현재 구현(RPC만)을 구분해 ⏳계획으로 판정했으나, 이걸 "🔶부분"으로 봐야 한다는 반론도 가능.
  5. **prod 배포 시점 확인** — X1a prod 배포가 "2026-07-10 저녁"에 완료됐다는 것은 §7 체크리스트 텍스트 기록에 의존했고, 이 워크트리(읽기전용) 자체에서 prod 서버 상태를 직접 재확인하지는 않았다(코드 수정 금지 지시에 따라 SSH 등 라이브 접근 안 함).
