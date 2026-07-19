# PLAN — 하루짜리 순위 고삐 + 시간당 총이익 제어 + CD5 (스프린트 RL, D-NAO-59/60)

> 이 시스템을 건드리는 모든 세션은 §0을 먼저 읽으세요. 트랙 `docs/tracks/active/track_naver-ad-optimization.md` D-NAO-59/60 항목이 결정의 단일 진실 원천입니다.
> 배경: CD1~CD4 완료(클릭 탐침 지혜층). 이 스프린트 = 그 지혜를 실행에 연결(CD5) + 시간당 레인을 "총이익 제어"로 승격 + 스톱로스를 순위 고삐로 교체.

## §0 방향 고정 (변형 금지 — 변경은 Jino 승인 후 D-N 기록)

### 최종 목적 (D-NAO-59, Jino 2026-07-19 원문)
> "무조건 이익스팟 순위에 있어서 매출 증가가 없는 것보다, ROAS는 떨어지지만 매출이 늘어서 총 이익이 늘어나는 경우 구간도 분명히 있거든. 우리의 최종목표는 이거야. 이게 우리가 만든 MOP 프로그램의 최종 목적이고 목표야."

**우리판 MOP의 최종 목적 = 총 이익(절대액) 최대화** (ROAS 최대화 아님).
- 평균 ROAS가 떨어져도 **한계(marginal) ROAS ≥ BEP** 구간에서는 순위를 올려 볼륨 확장 → 매출↑·총이익↑ 동시.
- **한계 ROAS = BEP = 총 이익 꼭짓점 = 운영 목표점.** 효율 최고 순위(고ROAS·저볼륨)에 앉아 이익을 남겨두지 않는다.
- 안전선 = 평균 ROAS ≥ BEP (D-NAO-1의 수학적 정밀화, 같은 꼭짓점).

### 이 스프린트가 바꾸는 것 (D-NAO-59 §25, "재설계 아님 = 확장")
목적함수(D-NAO-1)·실행 엔진(harness·가드레일·시간당/일 레인·응답곡선·CD1~4·시간당 수집)은 **재사용**. 바꾸는 것은 4가지:
1. **ccnt 수집 추가** — hh24에 시간당 전환건수를 넣어 장중 총이익을 추정.
2. **시간당 레인에 누적 추정 ROAS 신호 투입** — 지금 "순위·CPC·페이싱만·ROAS 판단 없음"(D-NAO-4) → **완화**: 장중 추정 ROAS로 총이익 제어.
3. **스톱로스(하드 정지) → 순위 고삐(rank leash) 교체** — 장중 loss면 순위를 쭉 하향(정지 대신). 볼륨 0 = 이익 0이므로 총이익 극대화엔 kill보다 leash가 우월.
4. **CD5** — learned_probe_rank 소비(탐침이 학습된 순위를 목표) + 이익 가중 승격.

### 순위 고삐(Rank Leash)의 핵심: 비대칭 기억
- **아래(하향) = 하루 리셋**: 장중 loss(누적 비용 유의미 ∧ 장중 추정 ROAS < BEP)면 순위를 한 등씩 하향. 판단 원료는 **오늘 누적치만**(cost_today·ccnt_today) — 자정에 리셋되어 매일 새 기회. 하향은 완전 가역(쿨다운 후 재상승 가능)·안전 방향.
- **위(상향) = 누적**: 성과 좋으면(정착창 ROAS ok) 상향하고 그 이득을 유지(관성). 다음날 자동으로 낮추지 않음. BEP가 자동 천장(BEP 미달 증액은 guardrail이 영구 차단).
- 하드 정지는 고삐의 **최종 단계**로만: 고삐가 입찰 하한(70원)까지 내려도 무전환 출혈이 계속되면 그때 pause(터미널). 첫 대응이 아니라 마지막 수단.

### 금지선
- **BEP 하한·킬스위치·일예산 불가침은 절대 불변.** 고삐 하향도 guardrail_gate 전량 통과(우회 경로 금지).
- 시간당 추정 ROAS는 **신호(방향 증거)이지 하드 게이트가 아님** — 진짜 판정은 D+1 정산(실 conv_amt). 장중 신호는 노이즈·전환지연으로 과소추정되므로 **보수적으로만** 하향 발동.
- 03(MOP) 불가침. 예산 변경 개방은 스코프 밖. 위임 스위치는 Jino만.
- 쿨다운 2h **유지**(D-NAO-60-쿨다운): D-NAO-55의 진동 근거(07-17 실사례) + 급성 출혈은 CD3 Stage1 밸브(×3 즉시회수)가 별도 처리. 2주 소급채점 후 재검토.
- 모델 라우팅: 설계=Opus, 구현=Sonnet, 리뷰=Opus(★Fable 금지·5R 이내). codex 소급 07-23.

## 구조 (Agent / Harness / SA — 원칙18)

```
시간당 밴드 레인 Agent (auto_operator.run_hourly_lane, 매시 :20)  ← 기존, 총이익 제어로 승격
├── [기존] _hot_set_candidates / _check_spend_circuit_breaker
├── [기존] _judge_hourly (순위<2.5 DOWN · CPC급등 DOWN · rank>4 UP)
│     └── + [RL3 신규 분기] 장중 loss 고삐 DOWN (추정ROAS<BEP ∧ 비용 유의미)
├── SA intraday_roas.py (신규, 순수)   ← 시간당 총이익 신호
│     ├── adgroup_unit_price(db, adgroup_id) → 매출가중 평균 판매가·공헌이익 (NaverProductBep×NaverAdgroupProduct 재사용)
│     └── estimated_intraday_roas(curve, unit_price) → (Σccnt×price)/Σcost  (전환지연·다상품 오차 경계 명시)
├── [기존] _probe_trigger (CD2)  ← + [RL5/CD5] learned_probe_rank 소비
├── [기존 CD3] probe_revert.run_bleed_valve (Stage1 급성 출혈)
└── [기존] naver_execution_harness.execute (단일 초크포인트·guardrail)

일 레인 Agent (auto_operator.run_daily_lane, 08:50)  ← 기존
└── pause 생성기 교체:
      account_diagnosis.pause_candidates / shopping_pause_candidates
      → [RL4] 스톱로스 도달 시 pause 대신 bid_down(고삐) 생성. 입찰 하한 도달 시에만 pause(터미널).

CD4 학습 Agent (probe_learning_loop.run_probe_learning, 09:03)  ← 기존
└── [RL5] _optimal_band 승격을 CTR argmax → 이익 가중(cart/conv·roas 결합)으로 교정

시간당 수집 (keyword_hourly_sweep, D-1)  ← [RL1] conv_cnt 추가
데이터층 (NaverKeywordHourly + hh24 fetch)  ← [RL1] ccnt 컬럼·필드
```

## Phase 계획 (각 Phase: 구현(Sonnet,TDD)→독립리뷰(Opus,5R)→PR→safe_deploy→라이브 검증→트랙/계획서 §7 갱신→HANDOFF)

### RL1 — 시간당 전환 데이터층 (ccnt 수집)
- `naver_sa_ad_fetcher._STATS_HH24_FIELDS` += `ccnt`; `fetch_entity_hh24` 반환에 `conv_cnt` 추가(파싱).
- `NaverKeywordHourly.conv_cnt` 컬럼 (additive 마이그, server_default '0', LESSONS #14 준수).
- `keyword_hourly_sweep`가 conv_cnt 저장.
- 회계 불변: conv_cnt는 **건수만**(금액 아님) — 매출/BEP 회계 절대 미접촉(CD1 원칙 계승).
- 완료 기준(원칙22): prod 재수집으로 conv_cnt 실적재 실측(04 시간당 전환건수 등장).

### RL2 — 시간당 추정 ROAS 신호 SA (`intraday_roas.py` 신규, 순수)
- `adgroup_unit_price(db, adgroup_id)` → 그 광고그룹 매핑 상품(NaverAdgroupProduct)의 NaverProductBep `selling_price`·`contribution_margin`을 최근 주문매출로 가중평균(campaign_target_resolver `_revenue_weighted_avg` 패턴 재사용). 매핑 없으면 None.
- `estimated_intraday_roas(curve, unit_price)` → `(Σccnt × unit_price) / Σcost`. cost=0 or price None이면 None. **보정계수 불필요** — ccnt×실판매가는 네이버 convAmt(2.6× 과대, D-NAO-7)가 아니라 실 주문가 기반이라 더 정직한 매출 신호.
- 정직 경계(docstring): ①다상품 광고그룹은 가중평균 오차(단일상품=정확, 파워링크 대부분 단일) ②전환지연(간접전환 ~1일)으로 장중 과소추정 → 보수적 하향에만 사용 ③ccnt 귀속(직+간접 여부)은 RL2 라이브에서 D+1 정산 대조로 캘리브레이션.
- 완료 기준: 04 광고그룹 unit_price 실산출 + 당일 곡선 추정 ROAS 실측.

### RL3 — 순위 고삐 판정 (장중 loss DOWN + 관성 + 자정 리셋)
- `_judge_hourly`에 **장중 loss 고삐 DOWN 분기 신규**(D-NAO-4 완화, D-NAO-60-2 기록):
  - 조건: 오늘 누적 비용이 유의미(`cost_today ≥ current_bid × K`, K=기존 스톱로스 배수 재사용 개념의 절반 등 보수적) ∧ estimated_intraday_roas < bep_roas(명백히 하회, 노이즈 여유) → bid_down 한 등(고삐).
  - 우선순위: 과열밴드 DOWN·CPC급등 DOWN 뒤, UP 앞 — bleeding day엔 UP 금지(장중 loss가 정착창 UP을 게이트).
  - **오늘 누적치만 사용**(자정 리셋). 하향은 안전 방향·완전 가역.
- 관성(상향 누적): 기존 UP 경로가 이미 이득 유지(다음날 자동 하강 없음). 문서로 비대칭 기억 명시.
- guardrail 불변(BEP 하한·쿨다운 2h·일일상한). 고삐는 새 bid_down 소스일 뿐.
- 완료 기준: 장중 loss 고삐 발동→하향 실측(자연 발동 대기 가능, 원칙22).

### RL4 — 스톱로스 → 순위 고삐 교체
- `account_diagnosis.pause_candidates` / `shopping_pause_candidates`: 스톱로스 조건(무전환 ∧ 누적비용≥bid×배수) 충족 시 **pause 대신 bid_down(고삐)** 제안 생성.
- 입찰이 이미 하한(70원 근처)이라 더 못 내리면 → 그때만 pause(터미널 최종 단계).
- 무전환 무한출혈 방지 = 터미널 pause + BEP guardrail로 보존.
- 정직 경계: 07-19 08:50 `01.아이폰16e` pause가 실 발동한 경로 — 새 고삐 경로를 반드시 라이브 검증.
- 완료 기준: 스톱로스 조건 유닛이 pause 아닌 bid_down 고삐로 처리됨 실측.

### RL5 — CD5 (learned_probe_rank 소비 + 이익 가중 승격)
- `_probe_trigger`가 그 유닛 env_cell의 `learned_probe_rank` 조회 → 승격된 최적 밴드 있으면 목표 순위로(or 이미 그 밴드면 탐침 생략). guardrail 전량 통과 유지.
- 이익 가중 승격: `probe_cell_aggregate._optimal_band`를 순수 CTR argmax → cell_leading_indicator(cart/conv)·roas 결합으로 교정(이익 스팟밴드 2.5~4 정합, P3-3 해소).
- 완료 기준(원칙22): 탐침이 학습 순위를 실제 목표로 삼는 왕복 1회 실측(CD2/CD3 탐침 자연발동이 선결).

## 리스크·결정 로그
- 장중 추정 ROAS 과소추정(전환지연) → 조급한 하향 위험. 완화: 비용 유의미 floor + 명백한 BEP 하회만 발동 + D+1 정산이 진짜 판정 + 자정 리셋(하루 손실 상계).
- 다상품 광고그룹 판매가 오차 → 매출가중 평균 + 단일상품 우선(파워링크 대부분 단일).
- 고삐 하향이 순위 데이터 재측정 전 연타 위험 → 쿨다운 2h가 방어(변경 후 1~2h 반영). 급성 출혈은 CD3 Stage1 밸브.
- 스톱로스 교체가 무전환 유닛을 너무 오래 살려둘 위험 → 터미널 pause + 입찰 하한 도달 시 자연 starvation + BEP guardrail.
- ccnt 귀속 미검증 → RL2 라이브에서 D+1 정산 대조 캘리브레이션(원칙22).

## §7 체크리스트 (현재 위치)
- [x] RL0 통합 설계·D-NAO-60 기록 (이 문서·트랙) — 완료(2026-07-19)
- [x] **RL1 시간당 전환 데이터층(ccnt)** — 완료·배포·라이브 검증(2026-07-19). commit `c225386`·마이그 `b48c2f3bc0a3` prod 적용. 2153 passed(+4). ★라이브: 전환 있던 3 애드그룹 hh24 conv_cnt 실값 흐름(21h=1·11h=1·15h=2). **캘리브레이션 항목**: 824088 hh24 conv_cnt=2 vs naver_ad_daily 일별=1 — ccnt 직/간접 귀속 차이 추정 → RL2에서 D+1 정산 대조로 확정.
- [ ] RL2 시간당 추정 ROAS 신호 SA — **다음**
- [ ] RL3 순위 고삐 판정
- [ ] RL4 스톱로스→고삐 교체
- [ ] RL5 CD5 소비+이익 가중 승격
- [ ] 쿨다운 2h 유지 결정 기록 (D-NAO-60)
- [ ] PR #60(D-NAO-59 docs) 병합
