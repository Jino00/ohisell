# ref 90 — 채점기 목적함수 어긋남: 성패를 찍는 자가 «효율»을 재고 있다 (D-NAO-222)

> 작성 2026-08-21 23:5x KST · 세션 `6b9376b0`(체인 「PAO 논의 31」) · **읽기 전용 조사** — 코드 변경 0건 · prod 쓰기 0건
> 관련: D-NAO-59(목적함수 확정) · D-NAO-85(2026-07-23 실사고) · D-NAO-183(교락) · 북극성 ref 82 §6·§7 · 트랙 `docs/tracks/active/track_naver-ad-optimization.md`

---

## §0. 한 줄 결론 · 그리고 커버리지 자백

**PAO의 확정 목적함수는 「총이익 절대액」(D-NAO-59)인데, 이 엔진에서 «성패·좋고나쁨»을 실제로 찍는 지점의 다수가 «효율 비율»(ROAS·RPC·CPC 배수)을 재고 있다.** 그래서 **절대액이 줄어도 「개선」으로 기록되는 경로가 실재하고, 라이브 원장에 이미 4건 찍혀 있다.**

**이 문서는 「전수 조사 완료」가 아니다.** 판정 지점 스윕은 `backend/app/services/naver_ad/` 한 디렉터리를 대상으로 했고, **열지 못한 파일이 40개 이상**이다(§8-B에 전량 나열). §4의 개수는 «훑은 범위 안의 개수»이지 저장소 전체의 분모가 아니다.

**이 문서는 사실까지만 적는다** — 처분(고칠지·언제·어느 마일스톤에 넣을지)은 지정하지 않는다. 그건 Jino 몫이다(북극성 §8이 항목 ④에 쓴 것과 같은 규율).

---

## §1. 발단

Jino 2026-08-21 23:26 원문:

> *"대행사에게는 우리가 결정하면 통보하면 되는거여서 문제가 전혀 없어. **우리가 대행사보다 잘 운영할 수 있는 준비가 되었냐가 중요하지**"*

앞선 대화에서 「M4를 열려면 결정 ①(`optimizer` 해제 범위)·②(소유권 분리)를 풀어야 한다」까지 나왔고, Jino가 ②의 절반(대행사 통보)을 해소하면서 **병목을 「스위치」에서 「역량 판정」으로 옮긴 것**이 이 조사의 출발점이다.

「우리가 더 나은가」에 답하려면 **그것을 재는 자**가 있어야 한다. 그 자를 열어본 결과가 이 문서다.

---

## §2. 1차 발견 — `proposal_scoreboard`는 RPC(수익/클릭)를 잰다

### 2-1. 판정식 (코드 좌표)

`backend/app/services/naver_ad/proposal_scoreboard.py`

| 좌표 | 내용 |
|---|---|
| `:59` | `rpc = (Decimal(conv_amt) / Decimal(clk)) if clk > 0 else None` |
| `:97` | `ratio = (after["rpc"] / before["rpc"]).quantize(_Q4)` |
| `:98-103` | `ratio >= IMPROVED_RATIO → "improved"` / `<= DECLINED_RATIO → "declined"` / 그 사이 `"neutral"` |
| `:33` | `IMPROVED_RATIO = Decimal("1.1")` |
| `:34` | `DECLINED_RATIO = Decimal("0.9")` |
| `:92` (상수 `:25`, `account_diagnosis`에서 import) | 모수게이트 `LOW_CLICK_THRESHOLD = 10` — 전·후 창 양쪽 `clk>=10` 미달이면 `outcome=None`(판정 보류) |

**목적함수와의 차이 (한 문장):** 판정식의 분모가 `clk`이므로 **클릭이 줄면 RPC는 오른다** — 즉 이 채점기는 「클릭·매출이 함께 줄어도 매출이 덜 줄었으면 개선」이라고 찍는데, D-NAO-59가 요구하는 것은 정확히 그 반대 방향(**ROAS가 떨어져도 매출 절대액이 늘어 총이익이 느는 구간을 잡는 것**)이다.

### 2-2. 라이브 증거 — `outcome='improved'` 전건 (prod, 2026-08-21 조회)

`naver_change_log` 전건. `actual_json`은 원장 원문 그대로이고, RPC·증감률은 그 값으로 **수기 재계산**한 것이다.

| id | 날짜 | action | clk 전→후 | 매출 전→후 | RPC 전→후 | ratio | 판정 |
|---|---|---|---|---|---|---|---|
| 221 | 07-21 | `update_bid` | 90 → 59 (**−34.4%**) | 178,900 → 138,200 (**−40,700 / −22.7%**) | 1,987.78 → 2,342.37 | ×1.178 | improved |
| 222 | 07-21 | `update_bid` | 90 → 59 (**−34.4%**) | 178,900 → 138,200 (**−40,700 / −22.7%**) | 1,987.78 → 2,342.37 | ×1.178 | improved |
| 761 | 07-27 | `update_bid` | 89 → 28 (**−68.5%**) | 174,900 → 90,500 (**−84,400 / −48.3%**) | 1,965.17 → 3,232.14 | ×1.645 | improved |
| 942 | 07-29 | `set_user_lock` | 55 → 11 (**−80.0%**) | 59,400 → 50,400 (**−9,000 / −15.2%**) | 1,080.00 → 4,581.82 | ×4.242 | improved |

### ★ **「개선인데 매출 감소」 = 4건 / 4건 (100%)**

`improved` 판정을 받은 전건이 매출 감소다. 매출이 늘어서 improved가 된 사례는 **0건**이다.

**id 761이 D-NAO-85의 축소판이다** — 클릭 −68.5%, 매출 **−48.3%**. 2026-07-23 사고가 ROAS +7% / 매출 −52%였다. **이 채점기는 그 사고를 「개선」으로 기록한다.**

⚠️ **[미상] 1건**: id 221과 222는 `before`/`after`가 완전히 동일하다(같은 날 `entity_type='ad'`). 같은 광고그룹의 서로 다른 소재 2건인지, 중복 기입인지 이 조사로는 못 갈랐다. **그래서 위 4건의 매출 감소액을 합산하지 않았다** — 합산하면 중복 계상 위험이 있다.

⚠️ **[미상] 2건째**: `outcome='success'` 2행(id 974·975, 07-29)은 `actual_json`이 **NULL**이고, `backend/app/services/naver_ad/`·`routers/naver_ad.py` 어디에서도 `outcome`에 `"success"`를 쓰는 코드를 찾지 못했다. `models.py:2567` 주석이 정의하는 값 집합(`improved/declined/neutral/executed`)에도 없다. **출처 미상의 고아 값**이다.

### 2-3. 이 값을 누가 소비하나 (파급 범위 — 과장하지 않기 위해 실측)

`naver_change_log.outcome`을 **읽는** 지점 전수:

| 좌표 | 용도 | 성격 |
|---|---|---|
| `routers/naver_ad.py:550·583·1338` | API 응답에 그대로 실어 화면 표시 | 표시 |
| `routers/naver_ad.py:1150·1289` · `ad_external_change.py:119` | `outcome=='failed'` 행을 **걸러내는** 필터 | failed만 사용 |
| `bid_rank_curve.py:151` | `failed` 행을 관측쌍에서 제외 | failed만 사용 |
| `proposal_scoreboard.py:153-160` | 액션별 improved 비율 롤업 | 자기 롤업 |

그리고 그 롤업의 출력은 `scheduler_service.py:629`(`run_naver_learning_loops`)에서 **로그로만** 남는다.

⇒ **파급은 「표시 + 자기 롤업」에 그친다.** improved/declined/neutral 값이 입찰·예산·정지 같은 실제 조작으로 흘러가는 경로는 이 조사에서 **찾지 못했다.** 즉 **지금 당장 돈을 잘못 쓰게 만드는 결함은 아니다.** 문제는 다른 데 있다 — **「우리가 더 나은가」를 판정할 유일한 계기판이 이것**이라는 점이다.

*(내가 조사 중 「이 값이 지혜 승격으로 흘러간다」고 가설을 세웠으나 **틀렸다** — `wisdom_candidates`는 `ops_diary_entry.outcome_json`이라는 **다른 테이블**을 읽는다. 아래 §3이 그 별개 경로다.)*

---

## §3. 2차 발견 — 지혜 «승격»의 게이트도 효율이다

`backend/app/services/naver_ad/wisdom_candidates.py:70 `_outcome_direction()`:

```python
roas_c = window.get("roas_c")
good = roas_c is not None and roas_c >= float(target)
return "good" if good else "bad"
```

**`good`의 정의가 「보정ROAS ≥ 캠페인 목표ROAS」다.** 매출·이익 절대액은 판정식에 없다.

이 `good`/`bad` tally가 그대로 패턴 후보의 승률이 되고(`_observation()`, `:91`), 그 후보가 지혜 승격 심사로 올라간다.

**북극성 M5의 합격기준은 「지혜→총이익 기여」 양수 ≥1건**이다. 즉 **승격 게이트는 ROAS로 거르는데, 그 층의 성공 정의는 총이익이다.** 두 자가 다른 것을 재고 있다.

---

## §4. 전수 스윕 — 훑은 범위 안에서 «효율» 19 / «절대액» 4 / «혼합» 4

`backend/app/services/naver_ad/`의 판정 지점을 별도 기(읽기 전용)로 스윕한 결과. **각 행은 코드를 실제로 열어 확인한 것이다.**

### 4-1. 효율(비율)로 성패를 가르는 지점 — 19

| 좌표 | 함수 | 판정식 | 무엇을 좌우하나 |
|---|---|---|---|
| `retro_scorer.py:47` | `_judge` | down: `roas_c<bep_asof→correct` / up: `roas_c>=target_asof→correct` | 진단 제안 방향의 **사후 정확도 채점** → GAVE 롤업 학습치 |
| `auto_operator.py:406` | `_settlement_roas_status` | `roas_corrected < target_roas → below` | **bid_up 승인의 핵심 거부권** |
| `auto_operator.py:1696` | `_judge_hourly` | CPC급등(배수) · loss leash(ROAS<BEP) · UP(ROAS≥target) 조합 | 시간당 입찰 상향/하향/보류 |
| `auto_operator.py:1425` | `_intraday_loss_leash` | `est_roas < bep_roas` | 장중 순위 고삐(하향) 발동 |
| `auto_operator.py:1130` | `_check_spend_circuit_breaker` | `today_cost > prior_avg×3` | 시간당 레인 전체 hold |
| `budget_pacing.py:390` | `evaluate` | `depletion_ratio>=trigger` → `proxy_roas<target_roas→거부` | **예산 증액 승인 여부** |
| `expansion_pressure.py:112` | `judge_campaign_pressure` | `보정ROAS/BEP >= 1.25 → expansion_mode` | **캠페인 확장(볼륨 배분) 허용 여부** |
| `account_diagnosis.py:110` | `bleeding_keywords` | `roas_c < bep_roas` | 출혈 키워드 목록(하향 대상) |
| `account_diagnosis.py:128` | `starving_winners` | `roas_c>=target_roas & avg_daily_clk<1` | 굶는 승자 육성 후보 |
| `account_diagnosis.py:185` | `shopping_group_bep` | `roas_c < bep_roas` | 쇼핑그룹 BEP 미달 목록 |
| `account_diagnosis.py:290` | `vicious_cycle_flags` | `recent_roas<prior_roas×0.9 & clk↓ & roas<target` | 악순환 캠페인 플래그 |
| `account_diagnosis.py:617` | `resume_candidates` | `roas_at_pause(보정) >= target_roas` | 정지 키워드 재개 후보 |
| `account_diagnosis.py:1057` | `shopping_group_growth` | `roas_c >= target_roas` | 쇼핑그룹 성장 후보 |
| `search_term_judge.py:452` | `_pl_group_net_loss` | `conv_amt/gcost < target_roas` | 파워링크 그룹 자동 제외 게이트④ |
| `search_term_scorecard.py:107` | `_verdict` | `after_cost/before_cost <= 0.10 → stopped` | 제외 조치가 걸렸는지 판정 |
| `group_state_badge.py:61` | `judge` | `roas<bep→hold` / `roas>=target→expanding` | 그룹 상태 배지(UI) |
| `creative_scorecard.py:75·155` | `build` | `roas - bep_roas >= 0 → "above"` | 소재별 스코어카드 |
| `anomaly_feed.py:105` | `spend_anomalies` | `cost_today/cost_prior >= SPIKE` | 소진 이상 탐지(브리핑) |
| `vitality_signal.py:89·107` | `_signal_s1`·`_signal_s2` | 노출 누적하락률≥40% · 순위 악화+밴드 밖 | 스파이럴 경보·소생 대상 |

### 4-2. 절대액으로 가르는 지점 — 4 (★설계로 방어한 사례)

| 좌표 | 판정식 | 비고 |
|---|---|---|
| `search_term_judge.py:148` | `pconv==0 & clk>=10 & cost>=min_cost(공헌이익)` | 코드가 **"ROAS 최대화 아님"** 을 명시 |
| `account_diagnosis.py:549` | `conv_amt==0 & cost >= bid_amt×10` (스톱로스 절대액) | WEB_SITE 자동 정지 |
| `diary_outcome.py:194` | `cost_total==0 → stopped, else leaking` | 주석: "성공 지표가 비용 정지" |
| `probe_revert.py:174` | `ad_conv>0 or proxy_revenue>0 → positive` | 프로브 되돌림 근거 |

### 4-3. 혼합 — 4

| 좌표 | 판정식 | 비고 |
|---|---|---|
| **`gave_score.py:21`** | **`score = min((ROAS/BEP)^γ, 1) × revenue`** | ★**올바른 모양이 이미 코드에 있다** — 비율 페널티 × **절대 매출**. 북극성 §5-⑤ 기준 **배선·정지중** |
| `account_diagnosis.py:711` | zero_conv=`cost>=eff_bid×10`(절대액) / lever_broken=`roas_c<bep & CPC>k×bid`(효율) | 쇼핑그룹 터미널 pause |
| `probe_revert.py:307` | `hourly_rate>avg×3`(효율) & `전환==0`(절대액) | 실시간 프로브 되돌림 |
| `growth_sweeper.py:78` | `ceiling=affordable_ceiling(rpc,target_roas)`(효율) → `gap=ceiling-bid`(절대액) | WEB_SITE 성장 후보 |

### 4-4. ★ 이 표에서 읽어야 할 것

1. **누락이 아니라 «편중»이다.** `search_term_scorecard.py`는 주석에서 *"우리가 만들지 않은 매출 증가분을 회수액에 넣지 않는다"* 며 **D-NAO-59를 직접 겨냥해** 설계돼 있다. 즉 이 저장소는 목적함수를 아는 자리와 모르는 자리가 섞여 있다. 일괄 과실로 서술하면 틀린다.
2. **★올바른 목적함수가 이미 코드에 있다 — 그런데 그게 정지 중이다.** `gave_score.py`의 `min((ROAS/BEP)^γ,1)×revenue`가 정확히 「효율은 페널티로, 크기는 절대액으로」의 모양이다. 북극성이 이걸 **배선·정지중**으로 분류해 뒀고, 실제로 성패를 찍고 있는 19개는 그 옆에서 효율만 본다.
3. **「절대액이 줄어도 통과」가 실증 가능한 지점 4곳**(스윕 판정):
   - `retro_scorer.py:47` — 하향의 사후 채점이 `roas_c<bep_asof`뿐. **매출을 얼마나 깎았는지가 채점식에 없다.** D-NAO-85 같은 사건도 「correct」로 기록된다.
   - `auto_operator.py:1696` CPC급등 DOWN 분기 — `today_cpc>baseline×2`만 보고 하향. 그 시간대 매출이 늘고 있어도 크기를 참조하지 않는다.
   - `auto_operator.py:1425` — `est_roas<bep_roas`면 무조건 하향 대상. **매출 절대 규모가 발동 여부에 반영되지 않는다.**
   - `account_diagnosis.py:110·185` — 배제 기준이 비율뿐이라 매출 규모가 큰 키워드·그룹도 동일하게 「출혈」로 오른다(정렬만 비용순).

---

## §5. 곁가지 실측 — 액셀은 막혀 있고 브레이크만 작동했다

§4-4의 편중이 라이브에서 어떻게 나타났는지의 증거. `update_bid` **425건**(dry_run=0) 중 **194건(45.6%)이 미집행**이고, 차단 주체는 대부분 **네이버가 아니라 우리다**:

```
A. 우리 가드레일이 막음    174건 (89.7%)
B. 네이버 API/쓰기 실패      20건 (10.3%)
```

가드레일 174건의 사유:

| 사유 | n | 성격 |
|---|---:|---|
| **BEP 미달 증액 금지** (보정ROAS 0.0 < 목표 1.822) | **61** | ★**액셀 차단** |
| 기타 가드레일 | 52 | 미분류 |
| 일일 변경 건수 상한 3/3 도달 | 43 | 속도 제한 |
| 스톱로스 도달 | 10 | 브레이크 |
| 일예산 상한 불가침 | 8 | 브레이크 |

**「BEP 미달 증액 금지 — 보정ROAS 0.0」 61건이 핵심이다.** 보정ROAS 0.0 = 창 안 전환 0. 즉 **전환 신호가 아직 없는 구간에서는 증액이 원리적으로 불가능**하다. 그런데 D-NAO-59가 잡으라는 «ROAS는 떨어지지만 매출이 늘어 총이익이 느는 구간»은 **정확히 그 미탐색 구간에 있다.**

북극성 §7이 이 구조를 미리 적어 뒀다:

> *"자동화는 브레이크(차단·정지)가 액셀(확장)보다 만들기 쉬워서 방치하면 반드시 ROAS 방어로 기운다"*

⚠️ 이 §5는 **2026-07-17~07-30 창의 관측**이다(우리 집행이 있었던 유일한 창). 그 뒤 가드레일 파라미터가 바뀌었을 수 있고(`update_guardrail_params` 2건, 08-10·08-11), **현재 값으로 재현하지 않았다.**

---

## §6. 「우리 vs 대행사」 직접 비교는 지금 원리적으로 불가능하다

Jino 질문에 숫자로 답하려면 «우리 운영 창»과 «대행사 운영 창»의 성과를 비교해야 한다. **그 비교는 지금 데이터로 성립하지 않는다.**

- 우리 집행 창 = **2026-07-17 ~ 07-30**(마지막 `update_bid` 07-30 10:20, 이후 22일간 우리 실집행 0건)
- 그 창이 **휴가시즌(D-NAO-183 확정: 고정 7/20~8/15) 시작**과 겹친다
- 그 창이 **Z폴드8 출시(7월 중순)** 와도 겹친다
- D-NAO-183 확정: **단말 출시가 계절과 준-공선** — 분석 창 안 출시 4회 중 「계절 없는 출시」는 S26(2026-03-11) 1건뿐 ⇒ **단독 귀속 금지**

⇒ 전후 비교 숫자는 **만들 수는 있지만 인용해서는 안 되는 숫자**다. **그래서 이 조사는 그 숫자를 생산하지 않았다.**

**이것 자체가 §2·§3의 발견을 되받는다**: 채점기가 없으면 「우리가 더 낫다」를 창 비교로 대신할 수밖에 없는데, 창 비교는 교락으로 막혀 있다. **단위 조치별 채점만이 남은 경로**이고 — 그 채점기가 지금 효율을 재고 있다.

---

## §7. 마일스톤에 갖는 함의 (사실까지만 — 처분은 지정하지 않는다)

- 북극성 §6의 순서는 **M0·M1(알기) → M2·M3(판단·학습 준비) → M4(손 재개) → M5(루프 상시화)** 이고 **M4만 직렬 게이트**다.
- **M3 = 「지혜 성적표 + 확정 지식 주입 배선」**, 합격 = 「승격 지혜 ≥1건에 성적 행 + 항등식 일치」.
- **사실 1**: 그 성적표가 원료로 삼을 판정값 두 축(§2 `naver_change_log.outcome` · §3 `_outcome_direction`)이 **둘 다 효율 기반**이다.
- **사실 2**: **M5의 합격기준은 「지혜→총이익 기여」 양수 ≥1건**이다 — 승격 게이트(ROAS)와 성공 정의(총이익)가 **다른 것을 잰다.**
- **사실 3**: 올바른 모양(`gave_score`, 비율 페널티×절대 매출)은 **이미 코드에 있고 정지 중**이다. 새로 발명할 필요가 있는지 없는지는 이 조사의 범위 밖이다.
- **사실 4**: 이 어긋남은 **현재 어느 마일스톤(M0~M6)에도 항목으로 잡혀 있지 않다.** 북극성 §8의 Jino 결정 9건에도, [미상] 2건에도 없다.

**처분 선택지(열거만 — 이 문서는 고르지 않는다)**: ⓐM3 계약에 흡수 · ⓑ별도 슬롯 신설 · ⓒM2 잔여에 편입 · ⓓ부채로 기록만 하고 미루기.

---

## §8. [미상] · 못 본 곳

### 8-A. [미상] 3건
1. `naver_change_log` id 221·222의 동일 값 — 다른 소재 2건인지 중복 기입인지 못 갈랐다(§2-2).
2. `outcome='success'` 2행의 출처 — 쓰는 코드를 못 찾았고 `models.py:2567` 정의 집합에도 없다.
3. `improved` 4건이 **모수게이트(clk≥10)를 통과한 표본**이라 전체 조치의 대표값인지 알 수 없다. 채점된 것은 150건(improved 4 / neutral 57 / declined 89)이고 나머지는 `outcome=NULL`(판정 보류)이다.

### 8-B. 못 본 곳 (스윕이 열지 못한 파일 — 정직한 나열)
- `auto_operator.py` 3,713줄 중 **약 900줄만** 확인. 미확인: `_hot_set_candidates`·`_probe_trigger`·`_learned_optimal_skip`·`_deep_expansion_ok`·`_run_exploration_for_campaign`·`_fire_vitality_revive`·`_bp_fire`·`run_daily_lane`·`run_hourly_lane` 등
- 일부만 확인: `exploration.py`(702줄)·`flight_loop.py`(561줄)·`bid_rank_curve.py`(363줄) — `adaptive_step`·`_finalize_step`·`exploration_ceiling`·`prioritize_candidates`·`_fit_slope` 미확인
- `account_diagnosis.py`의 `keyword_triage`(254)·`floor_wait_units`(888)·`shopping_resume_candidates`(1176)·`shopping_lever_resume_candidates`(1309) — 판정식 미확인
- 시그니처만 grep: `ctr_alert.py`·`ctr_alert_state.py`·`ctr_alert_briefing.py`
- **전혀 열지 않음**: `rank_servo.py`·`bid_ceiling_calculator.py`·`bid_simulator.py`·`response_curve_builder.py`·`market_bid_probe.py`·`cold_start_bid_decider.py`·`cold_start_bid_lane.py`·`hourly_pattern.py`·`launch_rank_floor.py`·`expansion_allocator.py`·`guardrail_gate.py`·`guardrail_params.py`·`budget_envelope.py`·`probe_learning_loop.py`·`probe_signal.py`·`probe_cell_aggregate.py`·`hierarchical_pooling.py`·`intraday_roas.py`·`effective_bid.py`·`bep_calculator.py`·`bep_breakdown.py`·`product_commission.py`·`expert_desk.py`·`expert_briefing_builder.py`·`expert_llm.py`·`ava_reviewer.py`·`diary_reflection.py`·`wisdom_apply.py`·`wisdom_retention.py`·`wisdom_writer.py`·`wisdom_loop.py`·`campaign_roas_lines.py`·`dashboard_overview.py`·`visibility.py`·`bm_*.py`(6종)·`hourly_snapshot.py`·`keyword_hourly_sweep.py`·`today_hourly_sweep.py`·`search_term_px_briefing.py`·`search_term_ss_lane.py`·`event_impact_scorer.py`·`improvement_events.py`·`today_proxy_revenue.py`·`cart_conversion_rate.py`·`product_campaign_share.py`·`perf_*.py`·`vault_export.py`
- **디렉터리 밖 전체 미조사**: `backend/app/routers/`·`backend/app/services/`(naver_ad 외)·프론트엔드

⇒ **§4의 「19 / 4 / 4」는 훑은 범위 안의 개수다.** 저장소 전체 분모로 인용하면 안 된다.

### 8-C. 이 문서가 하지 않은 것
- 채점기 **수정 제안·설계** — 범위 밖(앵커 「안 함」)
- §5 가드레일 분포의 **현재 파라미터 재현** — 07-30 창 관측이다
- 「우리 vs 대행사」 **숫자 생산** — §6의 이유로 의도적 미생산

---

## §9. 증거 재현 명령

```bash
# ① improved 전건 (prod, 읽기 전용)
ssh sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db \
  \"SELECT id,outcome,action,date(changed_at),actual_json FROM naver_change_log \
    WHERE outcome IN ('improved','success') ORDER BY changed_at;\""

# ② update_bid 194건의 차단 주체
ssh sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db \
  \"SELECT CASE WHEN rationale LIKE '%[실행 불가] 가드레일 차단%' THEN 'A.가드레일' \
    WHEN rationale LIKE '%[실행 실패]%' THEN 'B.API' ELSE 'C.기타' END k, COUNT(*) \
    FROM naver_change_log WHERE dry_run=0 AND action='update_bid' AND outcome='failed' GROUP BY k;\""

# ③ 판정식 좌표 (로컬)
sed -n '55,105p' backend/app/services/naver_ad/proposal_scoreboard.py
sed -n '70,90p'  backend/app/services/naver_ad/wisdom_candidates.py
sed -n '15,30p'  backend/app/services/naver_ad/gave_score.py
```
