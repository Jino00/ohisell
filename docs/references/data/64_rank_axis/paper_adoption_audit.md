# 논문 채택 13항목 실배선 감사 (2026-08-17 21:0x, 서브에이전트 2기 + 코디네이터 검증)

> 기준 = `docs/references/26_bidding_papers_survey.md` §2 TOP5 + `docs/references/33_gmv_max_under_roas_floor_research_20260719.md` findings [0]~[10].
> 판정 5값: 미구현 / 부분구현 / 구현·미배선 / 배선·정지중 / 가동중.
> ★코디네이터가 직접 재확인한 항목엔 [검증]. 나머지는 서브에이전트 실측(파일:줄 근거 보유).

## 요약표

| # | 기법 | 판정 | 근거 |
|---|---|---|---|
| ① | GRM 응답곡선 + 분석적 min-pacing `min{αB,αC}` | **배선·정지중** [검증] | `pacing_controller.py:88-90` `alpha=min(alpha_b,alpha_r)` 실재 · 크론 `15 */2 * * *` 실행 · **그러나 `flight_loop.py:1-22`가 「이 레인은 관측기다. 입찰을 바꾸지 않는다」(Jino 확정 2026-07-29)** + 쓰기 경로 재생성 차단 테스트 `test_flight_loop.py:490`. 이분법 대신 그리드+선형보간 |
| ③ | EBaReT LP 쌍대 닫힌형 `b=(1+αc·C)/(αb+αc)×v` | **미구현** | `쌍대`·`dual` grep 0건. `PLAN_naver-ad-execution-loop.md` §8 승계 큐에 「X2 성적 보고 후 채택 결정」 |
| ④ | Bayesian AdComB 분포예측 + 정수계획 | **미구현** [검증] | `scipy`·`ortools`·`pulp` **requirements.txt에 없음** — 착수 흔적 0 |
| ⑤ | GAVE 점수 `S=min{(ROAS/BEP)^γ,1}×매출` | **배선·정지중** | `gave_score.py` 공식 그대로 + 호출부 6곳(`expansion_pressure:166`·`expansion_allocator:300`·`auto_operator:2329`·`retro_scorer:124`·`proposal_pipeline:581`·`proposal_writer`) + `NaverCampaignSettings.gamma` 컬럼. **`optimizer='none'` 7/7이 실행 직전 차단 → 실제 입찰 반영 0건** |
| ② / [9] | DHEB 계층 EB 축소추정 | **부분구현** [검증] | `hierarchical_pooling.py` 4계층·`shrink()=(n·raw+K·prior)/(n+K)`, K=10, n↑에 따라 수축 자동 완화 — 그러나 **`pool_all`·`pool_metric` 호출부 0건(죽은 코드)**. RPC만 구버전 중복 구현 `bid_simulator.pooled_rpc`가 담당(가동중, `flight_loop:47`·`exploration:367`·`bid_ceiling_calculator:34`). **CTR·CVR 계층 추정치는 prod에서 계산되지 않음** |
| [0][2] | 두 승수 min 결합 + **둘 다 SGD 갱신** + 슬랙 회계 | **부분구현** | min 결합만 존재(①과 같은 코드). `SGD`·`슬랙`·`slack` 0건. αB·αC는 매 2시간 **처음부터 재계산**되는 순수함수 결과이지, 위반량을 누적하는 상태 변수가 아니다 |
| [1][6] | soft ROAS(7~14일 롤링) + 데이터 하한 | **부분구현** | `auto_operator.py:404-439` `_settlement_roas_status()` = 정착창 D-8~D-2 **7일 롤링**, `cost<=0`이면 `unknown`(fail-closed). 최소표본 게이트 실재: `_MIN_CLICK_FOR_APPROVAL=10`·`MIN_CLICKS_FOR_PROXY=5`·`_INTRADAY_UP_MIN_CONV=2`·`_MIN_CLICK_FOR_EXPLORATION=10`. **논문 수치(30일 15전환·4주)는 미이식 — 자체 상수** |
| [3] | 예산 증액 = (ROAS≥floor) AND (소진 병목) | **가동중** | `budget_pacing.py:390-596` 트리거 = `depletion_ratio ≥ 0.90` **AND** `proxy_roas ≥ target_roas`, 크론 `20 * * * *` → `auto_operator.py:3683`. 라이브 로그 「14:20 reviewed=5 raised=0」. ⚠️단 ①「기간 ROAS」가 아니라 **당일 프록시 ROAS**(스마트스토어 매출 기반, 광고귀속 아님) ②트리거가 「budget-limited OR ≥95%」가 아니라 **소진율≥90% 단일** ③**입찰 상향과 예산 상향의 짝짓기 0건**(BP 레인은 「핫셋·탐색 레인과 독립·맨 뒤」 — `auto_operator.py:2879`) |
| [4] | 증액 후 3~7일 관측 → 한계ROAS<floor면 롤백 | **부분구현** | `budget_pacing.restore_candidates()` + 익일 00:05 크론이 **무조건 base로 원복**. 사람 개입 감지(`unrestored_raise`)는 정교. **그러나 「한계 ROAS(증분가치/증분비용)」 계산 코드 0건** — 롤백 판정이 성과가 아니라 **순수 날짜**(1일 고정 창). `expansion_allocator.marginal_stop`은 스스로 「보수 프록시(정밀 한계ROAS 아님)」 명시(:47-58), 대상도 예산이 아니라 그룹 승격 |
| [5] | 한계ROI 균등화 + 지수법칙 베이지안 곡선 + TS + 25% 클리핑 | **미구현** | `budget_allocator.py` 전체 133줄이 **단순 임계값 비교**뿐. 균등화·`w1·x^w2` 적합·Thompson Sampling·클리핑 전부 0건. 문서가 정직하게 명시: `budget_allocator.py:2-9` 「marginal ROAS 인과추정은 하지 않는다(추정 금지 원칙)」 — **의도적 보류** |
| [7] | UCB 점수(실측비율+√(δln t/N)) + 최소노출 강제 + learning on tails | **부분구현** | `exploration.py:396-444` 대상이 핫셋(정착 clk≥10)의 **여집합**(clk<10) → 「learning on tails」와 **구조적 동형**. 콜드(imp=0)는 소폭 적응 스텝으로 노출 확보(:172-176). **그러나 UCB 스코어식·탐색 보너스 항 0건** — 판정은 스코어링이 아니라 `ladder_judgment()` **규칙기반 상태기계**(:562-699), 설계 원리가 다르다 |
| [8] | n-gram 집계 검색어 마이닝(승격·회수 양방향) | **미구현** [검증] | `ngram`·`n_gram`·`bigram` backend 전체 **0건**. `search_term_judge.py`는 `GROUP BY search_term`(:168·180-184·372·382-385) = **개별 쿼리 grain**. 승격·회수 양방향은 가동 중이나 풀링이 없다 — 논문이 이 기법을 권한 이유(개별 쿼리 전환 0~1건이라 판단 불가)가 정확히 우리 결함 |
| [10] | 168슬롯 계층 풀링(4~8그룹) + 소폭·저빈도 갱신 | **구현·미배선** [검증] | `hourly_pattern.py:69-101` 168칸 **각각 개별** 지수(`cell_avg/day_avg×100`), 신뢰도는 `sample_days/4주` 선형 — **통계적 축소추정 아님**. `:136` 「적용(bidWeight API 반영)은 하지 않는다(D-NAO-3)」. `bidWeight` grep = **`hourly_pattern.py` 3곳뿐**, writer 0건. ★그리고 **대행사가 콘솔에 넣어둔 실설정을 우리가 읽지도 않는다** |

**집계: 가동중 1 · 배선·정지중 2 · 부분구현 5 · 구현·미배선 1 · 미구현 4 = 13.**

## ★가장 큰 괴리 3건

1. **[10] 시간대 가중이 통째로 죽어 있다** — 168칸을 매일 계산해 DB에 쌓지만 실행 반영 0, 게다가 남이 켜 둔 `bidWeight`(연령·성별 실설정 1,343행 중 다수 70/80)를 **읽지도 않아** 실효 입찰가·순위 판정·BEP 판정이 전부 「가중치 100%」 암묵 가정 위에 있다.
2. **[8] n-gram 풀링 부재** — 검색어 승격·회수는 가동 중이나 전부 개별 쿼리 grain. 롱테일 희소성 완화 장치가 0.
3. **[9] 「일반화 완료」라 문서(PLAN §7 X3 T1)에 적힌 코드가 죽은 코드** — `pool_all`이 테스트 18개를 통과하고 호출부 0건.

## ★순서에 대한 판정 (설계의 핵심 입력)

미구현 4건 중 **③④[5]는 전부 정교한 최적화 수학**이다. 그런데 같은 날 ref 64가 실측한 것은
「순위 구간 우열의 시간 불안정(홀드아웃 8/8 미재현, 평시 행만으로도 미재현)」과 「쇼핑의 효율 신호는
스프레드 0.05 = 노이즈」다. 즉 **지금 ③④[5]를 이식하면 불안정한 신호 위에 정교한 제어를 얹는 것**이고,
그것은 `flight_loop`을 관측기로 남긴 논리(*"약한 신호로 정교한 제어를 덮는 구조"*)와 **같은 함정**이다.

반대로 부분·미배선인 **[9]·[8]·[10]은 신호를 두껍게 만드는 축**이고, ref 33 자신이 [6]·[10]에서
*"어떤 접근이든 학습 축적 기간을 먼저 확보 후 제약 활성화가 벤더 표준"*이라고 적었다.
승인돼 있는 **D-NAO-186(적재 3축: `/keywordstool`·`/estimate` 주기 적재 · col7/8/9 180일 백필 ·
`CRITERION` 365일 백필)**이 정확히 그 원료를 켜는 계약이다.

## 논문에 없는데 우리가 하고 있는 것 (버리지 말 것)

- **완결도 곡선 보정**(`completeness_curve.py`, `flight_loop.py:369-481`) — 당일 스냅샷의 시각별 체계적 저평가 보정. 논문 4건 어디에도 없는 자체 축
- **외부 개입 감지·되돌림 판별**(`ad_external_change.py`·`bm_diff.py`, D-NAO-13) — 같은 계정을 대행사·사람이 함께 만지는 환경 방어. 검토 논문에 없음
- **순위 목표형 적응 스텝**(`exploration.adaptive_step`·`ladder_judgment`) — 「눈먼 % 상향」 대신 최저 CPC로 밴드 진입(D-NAO-71 Jino 교정). UCB보다 목표지향적이나 학술 근거 없이 자체 개발
- **BP 레인 미복원 증액 방어**(`unrestored_raise`) — 실전 사고에서 파생된 레이스 방지

## 부수 정정 (이번 감사 중 확인)

- **D-NAO-180/181(쇼핑 제외 읽기·쓰기)은 이미 병합·배포 완료**다 — PR #306·#307, `exclusion_survival.py:154·210`·`naver_execution_harness.py:1295·1315·1383`에 살아 있고 prod와 동일. `MEMORY.md`의 「다음 세션 1순위: 쇼핑 제외」 기록보다 **저장소가 앞서 있었다**([[handoff-lists-must-be-remeasured]] 재확인).
- `PLAN_naver-ad-execution-loop.md` §0-4의 완료 정의(「X2 플라이트 루프 1주 이상 라이브 가동」)는 **달성 불가능한 형태로 남아 있다** — 그 루프가 2026-07-29에 영구 관측기로 확정됐다. 계획서 정정 필요.
