# PLAN — 스프린트 A″: 저볼륨 콜드 그룹 순위 탐색 (핫셋 게이트 밖 탐침 확장)

> ⛔ **2026-07-21 실측으로 보류(B 의존) — 그룹-입찰 레버로는 후보 0개.** SHOPPING auto_operate 콜드(정착 클릭≤3)·노출>0 그룹 18개가 **전부 source='ad'(소재-레벨 실효입찰)**, source='group'은 0개. 클릭밴드×레버 교차표:
> | 클릭밴드 | group | ad | (콜드 롱테일=Jino 탐색 목표=전량 소재-레벨) |
> |---|---|---|---|
> | 콜드≤3 | **0** | **18** | 그룹입찰 UP 무효 |
> | warm4-9 | 1 | 4 | |
> | hot≥10 | 2 | 2 | (source=group=활성 관리 그룹=이미 클릭 있음) |
> **결론**: 콜드 그룹 순위 탐색의 유일 레버=소재입찰=B 스프린트. 아래 탐색 로직(트리거·후보·D+1 되돌림·안전)은 유효 → **레버를 그룹입찰→소재입찰(B)로 이식해 재활용**. B(소재-레벨 UP 제어) 선결 후 재개. Jino 결정 대기(옵션1=B 확장 우선).


> 작성 2026-07-21 (Fable 설계). 근거: D-NAO-58 CD2/CD5 클릭 탐침 · D-NAO-4 두 루프 분리 · D-NAO-66 ROAS 단일 지배 · D-NAO-68 실행 손 완비.
> Jino 결정 흐름(2026-07-21 대화): "클릭 안 나오면 순위 올려야 하는데 왜 안 보이나" → 진단(핫셋 게이트 primary 배제) → A′(핫셋 게이트 완화, 안전 영향 넓음) vs A″(탐침 전용 저볼륨 경로) → **"A″로 설계 착수"**.
> 구현=Sonnet·GATE 적대 리뷰 필수(행위 변경·돈 경로)·codex 왕복(원칙19).

---

## §0 문제 (실측으로 확정)

Jino 관찰: auto_operate 캠페인들이 노출은 나오는데 클릭이 저조한데도 순위 올리는 행동이 안 보인다.

**진단(2026-07-21 라이브 실측)**: "순위 올려 클릭 탐색"은 **클릭탐침(CD2/CD5)**의 일이지만, 탐침은 **핫셋에 든 그룹만** 평가한다. 핫셋(`_hot_set_candidates`, auto_operator.py:648)은 **정착창(D−8~D−2, 7일) 클릭 ≥ `_MIN_CLICK_FOR_APPROVAL`(=10)**을 요구한다(line 703). 저볼륨 그룹은 여기서 먼저 걸러져 탐침 루프에 **아예 안 들어온다**.

정착창 그룹별 클릭 실측(4개 auto_operate 캠페인):
| 캠페인 | 핫셋 통과(≥10) | 롱테일(탈락) |
|---|---|---|
| 파워링크 10236310 | **0개**(최대 6) | 전 그룹 |
| 쇼핑 8492582 | 2개(28·10) | 7·5·4·3… |
| 쇼핑 8514959 | 1개(24) | 5·4·4·3… |
| 맥세이프 10769985 | 3개(308·46·14) | — |

**핵심**: 저볼륨 롱테일 그룹(정착창 클릭<10)은 핫셋 게이트에서 배제되어 탐침 대상이 못 된다. 이들이 "노출은 있는데 클릭 없는" 순위 사각지대다.

---

## §0.5 방향 고정 (변경 금지)

- **핫셋 게이트는 그대로 둔다**(A′ 기각 이유): `_MIN_CLICK_FOR_APPROVAL=10`은 **탐침만이 아니라 레인의 모든 money-action(서보·CPC 급등 DOWN·손실 고삐 DOWN·밴드 조정)을 저표본 그룹으로부터 보호**하는 게이트다. 낮추면 전 액션이 저표본 그룹에 노출된다(안전 영향 과대). → **탐침 전용 별도 후보 경로(A″)**로 롱테일에 도달하되, money-action의 핫셋 게이트는 불변.
- **탐색은 느린 루프(일 단위)다**(D-NAO-4 정합): 콜드 그룹 탐색은 "이 그룹을 며칠에 걸쳐 한 번 올려보고 클릭 살아나나 본다"는 **슬로우 루프 학습**이다. 장중 2h 창(빠른 루프)은 저볼륨 그룹에 표본이 얇아 부적합 → **정착창(7일) 증거로 판정**. 한 그룹당 **하루 1회** 탐색(dedup).
- **되돌릴 수 있는 작은 실험**: 한 단(one-notch) 상향 · BEP 하한 불가침 · **D+1 CD3 되돌림 자동**(approval_source=probe_op 재사용) · 출혈 밸브 자동 · 쿨다운 2h · 일일 변경 상한 3 · 런당 탐색 캡. "탐색=무제한 상향"이 아니다.
- **레버 작동 그룹만**(source='group'): source='ad'(소재-레벨 실효입찰) 그룹은 그룹입찰을 올려도 순위 무효 → 탐색 대상에서 사전 제외(레인 line 1562-1572 가드와 이중). 소재-레벨 콜드 그룹은 **B 스프린트(소재입찰 자동 제어) 몫**(A″ 스코프 밖).
- **초기 스코프 = SHOPPING adgroup grain만**. 파워링크(WEB_SITE)는 제외: 그 문제는 순위(3~4위)가 아니라 **만성 0.1% CTR = 소재/키워드 관련성**이라, 순위를 올려도 클릭이 안 붙는다(조건 rank≥2.5의 반대 병리). BRAND_SEARCH도 제외(서보 선례). 실측 후 확대 판정.
- **money-action 불가침**: 탐색 후보는 **탐침 UP만** 받는다 — 서보·CPC DOWN·손실 고삐·밴드 조정은 절대 안 탄다(핫셋 게이트가 지키는 그 액션들을 저표본 그룹에 흘리지 않는다).

---

## §1 구조 (원칙18 — 재사용 최대, 신규 최소)

```
run_hourly_lane (기존)
 ├─ [기존] _hot_set_candidates (정착 클릭≥10) → money-action(서보·DOWN·밴드) + 빠른-루프 탐침(CD2/CD5)
 └─ [신규] _exploration_candidates (정착 클릭<10·established-serving·source=group)  ← A″
       └─ 탐침-only 경로 (money-action 금지)
             ├─ _exploration_trigger (정착창 증거: imp≥T·clk<10·avg_rank≥2.5)   ← 신규 SA(순수함수)
             ├─ [재사용] _learned_optimal_skip (과열 상단 억제)
             ├─ [재사용] effective_bid source=group 가드 (line 1562-1572)
             ├─ [재사용] 한 단 상향 스텝 (_clamp_step UP ±15% — 보수)
             ├─ [재사용] naver_execution_harness.execute (BEP 하한·쿨다운·일일상한·킬스위치)
             ├─ [신규] 하루 1회 dedup 가드 (오늘 이 그룹 탐색 change_log 있으면 skip)
             └─ approval_source=probe_op → [재사용] CD3 D+1 되돌림 + 출혈 밸브 자동
```

**재사용(신규 아님)**: probe_op 기계(CD3 되돌림·출혈밸브)·`_learned_optimal_skip`·`effective_bid`·`_clamp_step`·guardrail_gate·execute·`_settlement_agg`.
**신규(최소)**: ①`_exploration_candidates` (후보 선정) ②`_exploration_trigger` (정착창 판정 순수함수) ③하루 1회 dedup ④`result["explored"]` 카운터 ⑤상수 3개.

### Sub-Agent 시그니처 (원칙18-8)
- `_exploration_candidates(db, campaign_id, window_from, window_to) -> list[(target_type, target_id)]`: SHOPPING adgroup·status on·부모 체인 활성·**정착 clk < _MIN_CLICK_FOR_APPROVAL**·**정착 imp ≥ _EXPLORATION_MIN_SETTLE_IMP**·**source='group'**. `_hot_set_candidates`와 상호배타(핫셋 통과분은 제외 — 이미 fast-loop 처리).
- `_exploration_trigger(settle_agg, curve, now) -> (bool, reason)`: 정착창 `clk < _EXPLORATION_MAX_SETTLE_CLK`(콜드) ∧ 정착 `imp ≥ _EXPLORATION_MIN_SETTLE_IMP`(established-serving) ∧ 정착 `avg_rank ≥ _HOURLY_RANK_DOWN_THRESHOLD`(2.5, 올릴 여지) ∧ 오늘 dedup 미발동. curve는 라이브 현재가·killswitch 재확인용(판정은 정착창).

---

## §2 페이즈

### AP1 (Sonnet) — 탐색 후보 + 정착창 트리거 [순수함수·행위추가 없음, 테스트 선]
- `_exploration_candidates`·`_exploration_trigger` 신규 SA + 상수 3개. 단위 테스트로 게이트별 통과/탈락 고정(핫셋 상호배타·source=ad 제외·정착 imp 미달 제외·rank<2.5 제외·콜드 아님 제외).
- **레인 미배선**(행위 불변) — SA만. `run_hourly_lane` 반환에 `explored` 키 0으로 추가.

### AP2 (Sonnet) — 레인 배선 + 하루 1회 dedup + 탐침-only 경로 [행위 변경·GATE 대상]
- `run_hourly_lane`에 핫셋 루프 **뒤** 탐색 루프 추가: 후보 → 라이브 현재가 재조회 → `_exploration_trigger` → `_learned_optimal_skip` → effective_bid source=group 가드 → 하루 1회 dedup → 한 단 UP 제안(approval_source=probe_op·rationale `[클릭탐침·저볼륨탐색]`) → execute.
- **money-action 격리 못박음**: 탐색 후보는 `_judge_hourly`(서보·DOWN·밴드)를 **호출하지 않는다** — 탐침 UP 분기로 직행. 차단 시 diary(blocked·ACTOR_PROBE).
- 런당 탐색 캡(`_EXPLORATION_RUN_CAP`)으로 비용 상한. dedup = 오늘 KST 이 그룹 probe_op update_bid change_log 존재 여부.
- 다운스트림 정합: `explored` 카운터·diary·CD3 되돌림(자동)·scoreboard(probe_op 기존 매핑 재사용).

### AP3 (Sonnet) — 관측·튜닝 훅
- 탐색 발동/되돌림/전환 성사 로그. 상수 3종 실측 캘리브레이션(§실측).

---

## §난제 (착수 전 못박음)

1. **콜드 그룹에 돈 쓰는 리스크**: established-serving 게이트(정착 imp≥T)로 "노출은 충분히 되는데 클릭 안 나오는" 그룹만(=순위 병리 가설 성립). 진짜 죽은 그룹(노출도 미미)은 제외. + 한 단·D+1 되돌림·BEP 하한·런당 캡·일일상한으로 다중 상한.
2. **fast-loop 탐침과 중복 발동**: 핫셋 통과 그룹은 탐색 후보에서 상호배타 제외 → 한 그룹이 CD2/CD5(fast)와 A″(slow) 양쪽에 안 뜬다.
3. **dedup 없으면 매시 재탐색**: 정착창은 장중 불변 → 하루 1회 dedup 필수(오늘 probe_op change_log 존재 시 skip). 되돌림 후 재탐색은 익일부터.
4. **source='ad' 헛발**: 후보 선정에서 source='group' 사전 필터 + 레인 line 1568 가드 이중. ad-레버 콜드 그룹은 B 스프린트 몫(명시).
5. **CD3 되돌림 귀속**: approval_source=probe_op 재사용 → `_standing_probes`가 자동 회수. 단 A″ 탐침도 CD3 `run_settlement` D+1 정산 대상이 됨을 테스트로 고정(전환 없으면 되돌림).

---

## §실측 필요 (단정 금지)

1. **`_EXPLORATION_MIN_SETTLE_IMP`** — established-serving 문턱(정착 7일 노출). 저볼륨 쇼핑 그룹 노출 분포 실측 후 확정(잠정 ~300).
2. **`_EXPLORATION_MAX_SETTLE_CLK` = 3 (Jino 확정 2026-07-21 "진짜 콜드까지 적극 탐색")** — 정착 클릭 0~3인 진짜 콜드 그룹까지 탐색 대상. 4~9(established지만 약함) 구간은 포함 안 함(그 구간은 fast-loop 탐침이 노출 집중 시 잡거나, 다음 상향에서 자연 편입).
3. **`_EXPLORATION_RUN_CAP`** — 런당 탐색 수(비용 상한). 잠정 소수(3~5).
4. **탐색 스텝 크기** — 한 단=±15% `_clamp_step` UP vs 서보(rank 기반). 초기 보수 = `_clamp_step`.
5. **WEB_SITE 확대 적합성** — 파워링크 CTR 병리에 순위 탐색이 듣는지(초기 제외, 쇼핑 실적 후 판정).

---

## §검증 (원칙22 — 라이브 합격 시나리오)

1. **단위/차등**: 핫셋 통과 그룹은 탐색 후보 제외 / source=ad 제외 / 정착 imp<T 제외 / rank<2.5 제외 / 콜드 아님 제외 / dedup 재발동 안 함 / money-action(서보·DOWN) 탐색 경로에서 미호출.
2. **GATE(적대)**: ①콜드 그룹 폭주 상향(런캡·일일상한·D+1 되돌림) ②money-action 누출(탐색 후보가 서보/DOWN 받나) ③dedup 우회(매시 재탐색) ④source=ad 헛발 ⑤CD3 되돌림 귀속 ⑥BEP 하한 생존.
3. **codex 왕복**(원칙19).
4. **라이브 합격(실쓰기)**: 배포 후 저볼륨 콜드 그룹에 탐색 UP 실집행(change_log dry_run=0·probe_op·저볼륨탐색 태그) → 다음날 클릭 살아나나 관측 → 안 살면 CD3 D+1 되돌림 실측 = **탐색 한 사이클 완주**.

---

## §체크리스트

- [ ] AP1: `_exploration_candidates`·`_exploration_trigger` SA + 상수 + 단위 테스트 (행위 불변)
- [ ] AP2: 레인 배선 + dedup + 탐침-only 격리 + 다운스트림 정합 (GATE 대상)
- [ ] AP2 GATE PASS (적대 리뷰 P1 0) + codex 왕복 PASS
- [ ] AP3: 관측·상수 실측 캘리브레이션
- [ ] 배포 + 라이브 합격(탐색 한 사이클: UP 실집행→클릭 관측→D+1 되돌림)
