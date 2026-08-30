# ref 112 — PAO의 사전 판단 게이트는 총이익을 읽지 않는다 (전수 조사, 2026-08-30 KST)

> **발단**: Jino 원문(2026-08-30 18:3x KST) — *"우리가 만든 pao에서 광고성과와 지표를 참고해서 운영하지 않냐는거야"*
> **성격**: 조사 문서 — 코드·prod 무변경. 처분(트랙 결정·설계)은 Jino 몫이다.
> **방법**: 읽기 전용 코드 조사 3벌(본 조사기 1 + 하위 감사 2) + 세션 리드의 직접 grep. prod 접속·쓰기 0건.
> **표기**: 무표기 = 이 조사에서 코드를 직접 열어 확인 · `📄` 문서·주석 주장 · `🧠` 추론.

---

## §1. 한 줄 답

**PAO는 광고 성과 지표를 아주 많이 읽는다. 그런데 «할까 말까»를 가르는 사전 판단 게이트가 읽는 것은 전부 ROAS·BEP 비율·클릭수·전환건수이고, 원화 총이익을 읽는 사전 판단 게이트는 0개다.**

총이익은 계산된다 — 정확하게, 잘 짜인 코드로. 다만 **그 값을 소비하는 곳이 계산한 자기 자신뿐**이다.

---

## §2. 결정적 증거 넷

### 2-1. `outcome_profit`의 소비자가 자기 자신뿐이다

`NaverChangeLog.outcome_profit`(총이익 개선/악화 판정)을 **읽는** 코드는 저장소 전체에서 **`wisdom_scorecard.py`와 `proposal_scoreboard.py` 두 파일뿐**이다(grep 6곳 전부 이 두 파일 안). `auto_operator` · `guardrail_gate` · `search_term_judge` · `proposal_pipeline` · `gave_score` — **실행·생성 판단 어디에도 이 값을 읽는 코드가 없다.**

### 2-2. 엔진 본체가 총이익 모듈을 import조차 안 한다

`backend/app/services/naver_ad/auto_operator.py`는 **4,016줄**인데 `profit`이라는 문자열이 **0회** 등장하고, `profit_scorecard`를 **import하지 않는다**.

⇒ 매일 **08:40** 크론(`run_naver_profit_scorecard`)이 총이익을 계산하는데, **08:50** 실집행 레인(`run_naver_auto_operator_daily`)은 그 값을 **모른 채** 집행한다.

### 2-3. 판단 게이트 파일 전건 `profit` 0 — 대신 `roas` 175

세션 리드 직접 측정(2026-08-30 18:4x KST):

| 파일 | `profit` | `roas` |
|---|---|---|
| `auto_operator.py` (판정 본체) | **0** | 81 |
| `expansion_pressure.py` | **0** | 31 |
| `budget_pacing.py` | **0** | 20 |
| `bid_ceiling_calculator.py` | **0** | 16 |
| `guardrail_gate.py` | **0** | 14 |
| `expansion_allocator.py` | **0** | 7 |
| `search_term_judge.py` | **0** | 5 |
| `growth_sweeper.py` | **0** | 4 |
| `rank_servo.py` | **0** | 1 |
| `search_term_ss_lane.py` · `delegation_gate.py` | **0** | 0 |

`profit`이 등장하는 13개 파일은 **전부 채점·성적표·집계·표시** 계열이다: `profit_scorecard` · `wisdom_scorecard` · `proposal_scoreboard` · `retro_rollup` · `search_term_scorecard` · `accel_gate_view`(관측 전용) · `pao_scope_roster`(화면) · `diary_reflection` · `campaign_roster` · `exclusion_return_score` · `search_term_exclusion_list` · `intraday_roas` · `cold_start_bid_decider`.

⚠️ **이 표는 «낱말」을 센 것이다.** 다른 이름으로 이익을 계산할 가능성은 §2-4와 §3의 지점별 조사가 따로 배제했다.

### 2-4. 「이름은 이익, 자는 비율」이 두 군데

| 좌표 | 이름이 말하는 것 | 실제 계산 |
|---|---|---|
| `exclusion_return_score.py::STATUS_PROFITABLE` | 「이익이 남았다」 | 주석 원문: *"창 RoAS ≥ BEP — 복귀가 옳았다"* |
| `gave_score.py` (`retro_scorer.py` 주석이 **「총이익 기여도」**라 부름) | 총이익 기여 | `penalty(ROAS/BEP) × **revenue**` — **비용을 빼지 않는다** |

이 둘이 「총이익으로 판단하고 있다」는 착시의 실제 출처로 보인다 🧠.

---

## §3. 판단 지점별 — 무엇을 읽는가

조사 3벌의 합본. **원화 총이익을 명시 계산해 그 크기로 판단을 가르는 사전 게이트는 없다.**

### 3-1. 실행 게이트 (사전 판단)

| 판단 지점 | 무엇을 정하나 | 읽는 지표 | 창 | 총이익? | 방향 |
|---|---|---|---|---|---|
| `auto_operator.py::_settlement_roas_status` | 일 레인 bid_up 조건③ | 보정 ROAS `(conv_amt/cost)×factor` vs `target_roas` | D-8~D-2 정착창 | ✗ | 브레이크(veto) |
| `auto_operator.py::_bleeding_hold_reason` | 조건④ bleeding | `NaverRetroSignal.board`(bep_roas 기반) | D-1 스냅샷 | ✗ | 브레이크 |
| `auto_operator.py::_check_bid_up_conditions` | bid_up 4조건 종합 | ①스텝클램프 ②클릭≥10 ③보정ROAS ④bleeding | 조건별 | ✗ | 혼합 |
| `auto_operator.py::_deep_expansion_ok` | 심층 확장 진입 | `roas_ratio`(ROAS/BEP) **≥ 1.25** | 정착창 | ✗(gave_score의 `score`가 아니라 `roas_ratio`만 씀) | 액셀 |
| `auto_operator.py::_intraday_up_ok` | 장중 UP | `estimated_intraday_roas` ≥ `target_roas`×여유 | 당일 | ✗ | 액셀 |
| `auto_operator.py::_intraday_loss_leash` | 장중 순위 하향(RL3) | `est_intraday_roas < bep_roas` ∧ 당일소진≥하루평균 | 당일 vs 정착창 | ✗ | 브레이크 |
| `auto_operator.py::_check_spend_circuit_breaker` | 시간당 레인 전체 hold | `today_cost` vs 직전7일평균×3 | 당일 vs 7일 | ✗ | 브레이크 |
| `auto_operator.py::_servo_economic_ceiling` | 서보 입찰 상한(원) | `rpc×보정계수`, `target_roas` | 정착창 | ✗(단가 상한) | 브레이크 |
| `guardrail_gate.py::_check_bid` (BEP 미달 증액금지) | 실행 직전 승인 | `roas_corrected` vs `target_roas` | 미상 | ✗ | 브레이크 |
| `guardrail_gate.py::_check_bid` (스톱로스) | 실행 직전 승인 | **무전환 지출** 원화 vs `target_bid×배수` | 미상 | ✗(지출이지 손실 아님) | 브레이크 |
| `guardrail_gate.py::_check_budget` | 예산 증액 승인 | `roas_corrected` vs `target_roas` | 미상 | ✗ | 브레이크 |
| `budget_pacing.py::evaluate` | 예산 페이싱 증액 | 소진율 · `proxy_roas` vs `target_roas` | 당일 | ✗ | 액셀 조건 |
| `expansion_pressure.py::judge_campaign_pressure` | 확장모드 on/off | `corrected_roas/bep_roas` ≥ **1.25** | 정착창 | ✗ | 액셀 |
| `expansion_allocator.py::_classify_tier` | 그룹 채택/제외 | `weighted_rank` + `own_ratio`(ROAS/BEP) vs 1.0·1.25·1.1 | 정착창+7일 | ✗ | 액셀+브레이크 |
| `growth_sweeper.py::find_growth_candidates` | 성장 후보·갭 | `affordable_ceiling(rpc, target_roas)` | 클릭 게이트 | △ 간접(수식 유도) | 액셀 |
| `bid_ceiling_calculator.py::ceiling_from` | 콜드 CPC 상한 | `affordable_ceiling(rpc, bep_roas)` | 90일 | △ 간접 | 브레이크 |
| `exclusion_grade.py::classify` | 제외 등급 | `revenue/cost` vs `bep_roas` | 전 기간 | ✗ | 판정 |
| `search_term_judge.py::judge_search_terms`(쇼핑) | 제외 후보 | `clk`≥min · `conv_purchase_cnt`==0 · `cost`≥min_cost | 14일 | ✗ | 브레이크 |
| `search_term_judge.py::_judge_powerlink` | 파워링크 제외 | `clk`·`cost` + 그룹 30일 `(conv_amt/cost) < target_roas` | 30일 | ✗ | 브레이크 |
| `rank_servo.py::decide_servo_step` | 순위 스텝 크기 | `weighted_rank` · `imp_sum≥30` · economic_ceiling 클램프 | 3시간 | ✗ | 액셀+상한 |
| `cold_start_bid_decider.py::decide_cold_start_bid` | 콜드 첫 입찰 | `ceiling_cpc` vs `ladder_min`, `min(ceiling, market)` | — | ✗ | 액셀+브레이크 |
| `launch_rank_floor.py::floor_for` | 출시창 입찰 하한 | `days_since_launch` vs 21일, `target_rank` | 21일 | ✗ | 액셀(하한이 상한을 이김) |
| `probe_learning_loop.py::learned_probe_rank` | 순위밴드 승격 캡 | `conv_cnt` argmax / `ctr_shrunk` argmax — **`cost`는 집계하되 판정 미사용** | 최소 3일·imp100 | ✗ | 브레이크 |
| `guardrail_params.py::SPECS` | 안전봉투 값 | 시간·횟수·배수·클릭수·일수 — **원화 항목 0개** | — | ✗ | 브레이크 |
| `proposal_writer.py`(`bid_simulator.simulate_bid`) | `target_bid` 산정 | `affordable_ceiling = rpc_corrected / target_roas` | — | ✗ | 양방향(동일 산식) |
| `delegation_gate.py::_eligible` | 자동승인 자격 | proposal_type·optimizer·예산봉투 (경제 판단 아님) | — | 해당없음 | 권한 게이트 |

### 3-2. 총이익을 «계산»하는 곳 — 전부 사후·관찰

| 좌표 | 산식 | 언제 | 소비자 |
|---|---|---|---|
| `proposal_scoreboard.py::_gross_profit` (D-NAO-225) | `(conv_amt×cf/bep) − cost` | D+14 사후 채점 | **자기 자신 + `wisdom_scorecard`뿐** |
| `profit_scorecard.py::run_profit_scorecard` | 동일 산식 | 08:40 크론 | **diary + Slack뿐** (주석이 「관찰 전용」 명시) |
| `pao_scope_roster.py::_profit` | 동일 산식 | 화면 조회 시 | 대시보드 표시 |
| `wisdom_scorecard.py::_profit_amounts` | 동일 산식 재구현 | 리포트 | 화면 |

★ **구현 품질은 좋다** — 「BEP 해석 불가면 숫자를 지어내지 않는다」를 지킨다. **문제는 계산이 없는 게 아니라 그 결과가 게이트로 한 걸음도 안 넘어가는 것**이다.

### 3-3. 학습 루프의 입구와 출구가 끊겨 있다

`wisdom_apply.py::propose_param_changes`(지혜 → `guardrail_params.SPECS` 변경 제안)가 **`outcome_profit`·`gross_profit`을 참조하지 않는다**(grep 확인). 읽는 것은 `wisdom_judge`의 `good_count/bad_count`다.

⇒ **사후에 잰 총이익이 사전 임계값(`target_roas`·SPECS)을 조정하는 배선이 없다.**

### 3-4. dormant — 계산은 되는데 배선이 없는 것 둘

- **`flight_loop.py::run_flight_loop`** — `dry_run` 분기가 코드에 **0건**이다. `dry_run=False`로 불러도 값이 로그 컬럼에 박힐 뿐 제어흐름이 안 바뀐다. `naver_execution_harness`·`naver_sa_writer` import **0건**, `db.add()`는 `NaverChangeLog` 2곳뿐. ⇒ **문서의 「관측기로 강등」 주장 📄이 코드로 확인됐다.**
- **`pacing_controller.py::compute_pacing_alpha`** — 예산·ROAS 제약 배수 α를 계산해 `NaverChangeLog`에 **기록만** 하고, 실제 입찰에 곱해지는 배선이 없다.

---

## §4. 액셀 : 브레이크 — **확정 못 함**

조사 3벌이 서로 다른 숫자를 냈다: **7 : 5** / **4~5 : 10~12** / **3 : 6**. 파일 집합이 달라 합산해도 확정치가 아니다. **방향만 일치한다**(브레이크 우세).

★ 그런데 **개수 논쟁보다 중요한 것이 하나 나왔다**:

> **`proposal_pipeline.py::_apply_gave_priority`가 방어(브레이크) 클래스를 무조건 선순위로 배치한다.** 같은 회차 제안이 캡에 물리면 **브레이크가 먼저 나가고 액셀이 밀린다.**

개수뿐 아니라 **실행 순서에까지** 브레이크 우대가 구조로 박혀 있다.

---

## §5. 이게 D-NAO-59와 어떻게 어긋나는가

트랙 궁극 목표(Jino 2026-07-19 원문):
> *"무조건 이익스팟 순위에 있어서 매출 증가가 없는것보다 Roas는 떨어지지만 매출이 늘어서 총 이익이 늘어나는 경우, 구간도 분명히 있거든. 우리의 최종목표는 이거야."*

**비율 게이트는 그 구간을 원리적으로 못 잡는다** — 임계값 미달이면 **볼륨이 얼마나 크든 hold**이기 때문이다. 실제로 확장 게이트 둘(`_deep_expansion_ok` · `expansion_pressure`)이 `roas_ratio ≥ **1.25**`를 요구하는데 **BEP는 1.0**이다.

⇒ **ROAS 1.0~1.25 = 「이익은 나는데 확장하지 않는」 띠**가 비어 있다. 🧠 다만 1.25의 의도가 「표본 부족 방어」일 수 있어, 이 해석을 확정하려면 그 상수의 근거를 따로 봐야 한다.

★**북극성 §7이 적은 「액셀·브레이크가 대칭인가」보다 한 층 아래에 이 문제가 있다** — 개수를 맞춰도 **둘 다 ROAS를 자로 쓰면 최적화 대상은 여전히 ROAS**다.

---

## §6. 북극성 ①요소가 코드에 없다

`grep -rln "성과등급\|perf_grade\|performance_grade"` → `backend/` 전체 **0건**.

북극성 §1의 ①요소는 「API 지표 ↔ **4등급 성과등급**의 개연성」인데, **그 4등급이 연결될 코드 필드 자체가 존재하지 않는다.** (`qi_grade`는 품질지수 1~7이고 별개 · `exclusion_grade`는 제외 임대 유형 UNDERPERFORM/BROAD/MISCUT/IRRELEVANT/UNVERIFIED로 별개.) 북극성 문서 자신이 이 4등급을 L1 지식층(연구 단계) 산물로 서술한다 — **운영 판단에 연결되기 전 단계라는 것이 코드로 확인된다.**

---

## §7. 확인 못 한 것 (커버리지 자백)

- `response_curve_builder.py` — `pacing_controller`가 소비하는 곡선의 산출 로직. `pacing_controller` 자체가 dormant라 우선순위를 낮췄다.
- `proposal_writer.py`(85KB) 전 제안 유형의 세부 스텝 — `bid_simulator.affordable_ceiling` 하나를 공유하는 구조라 총이익 계산이 숨어 있을 가능성은 낮다 🧠(파일 내 `profit`·「이익」 문자열 미발견).
- `wisdom_judge`가 `good/bad`를 무엇으로 가르는지 — `wisdom_apply`가 `outcome_profit`을 안 읽는다는 것만 확인.
- `expert_desk`/전문가 평결(`NaverExpertReview.verdict`)의 판정 근거.
- **라이브 대조 0건** — 이 조사는 코드만 봤다. 엔진은 07-30 이후 대부분 `optimizer='none'`·`auto_operate=0`이라 실집행 0건 📄이므로, 「지금 안 돈다」와 「무엇을 읽도록 짜여 있나」는 별개다. **이 문서는 후자만 답한다.**
- 확장 게이트 문턱 **1.25의 근거** — 상수의 도입 경위를 안 봤다. §5의 해석이 여기에 걸려 있다.

---

## §8. 처분 (미정 — Jino 몫)

이 문서는 **사실까지만** 적는다. 아래는 후보이고 결정하지 않았다:
- 트랙 결정(D-NAO)으로 등재할 것인가
- 정본 `docs/PAO_OPS.md` §8(액셀·브레이크 대칭)을 「자가 목적함수인가」가 먼저 오도록 고칠 것인가
- 사전 판단이 총이익을 읽게 하는 설계로 갈 것인가 — **그건 새 계약이다**(북극성 §8-③: 계약 1장 + Jino 승인)

**다음 가용 번호**: D-NAO-280 · 교훈 #380
