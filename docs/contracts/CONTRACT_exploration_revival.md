# CONTRACT — bleeding 판정이 «표본 부족»인 그룹은 탐색으로 되살린다 (승인 대기 · D-NAO-289)

> **목표이름(제안): 「탐색소생 목표」** — 승인 직후 확정해 트랙·앵커에 복사한다.
> 브랜치 `feat/pao-n89` · 초안 2026-09-05 16:3x KST · 기획 파트너(Fable 5.1) 초안 → Jino 승인 대기
> 발단: Jino 질문(2026-09-05 15:51 KST) → 처방 A/B/C 제시 → Jino *"A안으로 가자"*(16:21 KST)
> 파일 독점(구현 시): `backend/app/services/naver_ad/auto_operator.py` · `backend/tests/test_naver_exploration_revival.py`(신설)
> 배경: 북극성 `docs/references/82_pao_north_star_20260819.md` §5-3 ③ · §6 M4 · §7 · 이 문서 §0

---

## §0. 착수 실측 — 이미 잰 것을 원문으로 (2026-09-05 15:5x~16:4x KST · prod 읽기 전용)

**Jino 질문 원문** — 계약의 출발점:
> **"안된다고 그냥 꺼버리면 그 광고그룹은 영원히 기회를 잃는거잖아. 이런건 어떻게 처리를 하게 되어 있어?"** (2026-09-05 15:51 KST)

**설계가 답하려고 만든 것(코드로 확인)** — 정지 대신 고삐(`_intraday_loss_leash`, `auto_operator.py:1981`) · 바닥이면 무액션 대기(`proposal_writer.py:583, 700-706`) · 핫셋 여집합 전부가 탐색 후보(`exploration.py:453-500`) · 래더 `reactivate`(`exploration.py:772-778`) · 무노출 종료의 자가 치유(`exploration.py:131-135`).

**순환이 닫히지 않는 좌표(한 자리):**
1. 탐색 레인은 후보마다 **가드5** `_exploration_daily_loss_reason`(`auto_operator.py:2787`, 호출 `:3137`)을 묻는다.
2. 그 함수는 `_bleeding_hold_reason`(`auto_operator.py:895`)을 그대로 부르고, 최신 retro `shopping_group_bep` 보드에 그 그룹이 있으면 **제외**한다(`:927-931`). 두 번째 조건으로 `shopping_pause_candidates` 보드도 본다(`:2801-2809`).
3. `shopping_group_bep` 보드의 진입은 **14일 창**(`retro_snapshotter.py:16, 43`)에서 `cost>0 ∧ 보정ROAS<BEP`뿐이다(`account_diagnosis.py:185-222`). **보드 탈출은 「14일 비용 0」 또는 「ROAS≥BEP」 둘뿐**(`account_diagnosis.py:206-213` — `if cost <= 0: continue` 207행 · ROAS 판정 210-213행).
4. 노출이 살아 있는 저볼륨 그룹은 소액 비용이 계속 나고 전환은 0 → ROAS 0 < BEP → **영구 bleeding → 탐색 영구 제외**. DL4 재상향(`auto_operator.py:2391-2431`)은 정착창 ROAS≥목표 또는 장중 전환≥2를 요구하므로 이 그룹을 못 올린다. **어느 레인도 이 그룹을 안 만진다.**

**라이브 숫자(독립 재현분 — 새 수가 필요하면 직접 잰다):**
- bleeding 보드 최신 asof **2026-09-04, 67그룹** — 그중 **바닥입찰(그룹입찰 ≤100원) 38 · 14일 전환 0인 것 49**.
- 보드 체류일수 상위 **59·57·56·56·56·54·54·50·49·48일**, 다수가 `first_seen=2026-07-08`(보드 시작일). 예: `grp…59821034` 54일·14일 비용 9,370원·ROAS 0·그룹입찰 50.
- 최근 14일 탐색 레인 차단 사유 1위 = `daily 손실상태 제외 — ④최신 소급채점에서 bleeding으로 판정됨` **1,761건**(`ops_diary_entries` actor=`explore`, 2026-08-22~09-05). 2~4위는 retro stale 981건.
- 스코프 «안»에서도 같다: 사생활 캠페인이 켜져 있던 **2026-09-01 07:20~13:20**, 핫셋이 아닌 스코프 내 4그룹(`59832147·59832150·70992078·70992116`) **전부 7/7 런 bleeding 차단**. 스코프 안 탐색 실집행 0건(마지막 2026-07-30)은 우연이 아니라 구조다.
- 래칫 지대(정착창 clk≥10 ∧ 전환<7) **20그룹, 전건 스코프 밖** — 이 계약 밖(§3).
- 현재 엔진이 실제로 굴리는 광고그룹 **1개**(`grp…70523564`, 30일 광고비 8.3%). 사생활 5그룹은 `auto_operate=0`(09-01 사람이 끔)이라 무력.
- 터미널 pause는 07-28 이후 제안 0건(08-01~ `shopping_pause_candidates` 유래는 ad `bid_down` pending 8건뿐). 지금 「영원히」의 실체는 정지가 아니라 **가드5 제외**다.

**★스톱로스 상수 재사용 가능성 실측(2026-09-05 16:4x KST, `naver_retro_signal` ⋈ `naver_entity` ⋈ `naver_adgroup_product`, 실효입찰은 `effective_bid._derive` 규칙을 SQL로 근사):**

| 구분 | 수 |
|---|---|
| bleeding 67그룹 중 **실효입찰 ≤70원(진짜 바닥)** | **0** |
| bleeding 67그룹 중 **14일 비용(`cost_asof`) < 실효입찰 × `LOW_CLICK_THRESHOLD`(10)** | **42** |
| 스코프 안 4그룹 중 위 조건 통과 | **1** (`grp…59832147`: 1,117원 < 150×10) |

⇒ **두 가지가 확정됐다.** ①「바닥 그룹」은 그룹입찰(명목 50원)의 착시다 — 실효입찰(소재 bidAmt 150~1,990원) 기준으로 바닥은 **없다**. 처방 A의 「바닥 ∧ 소액」 중 «바닥»은 조건으로 쓸 수 없다. ②「소액」은 새 상수 없이 **기존 스톱로스 상수**(`account_diagnosis.py:25 LOW_CLICK_THRESHOLD=10` · `:818 stop_loss_amount = eff_bid * LOW_CLICK_THRESHOLD`)로 정의된다. 그리고 그 정의는 우연이 아니다 — 스톱로스 보드 자신이 `if cost < stop_loss_amount: continue`(`account_diagnosis.py:819`)로 **그 미만은 «표본 부족·성급 사살 금지»라며 손도 안 댄다.** 가드5는 주석에 *「DL이 읽는 것과 동일 보드/신선도」*(`auto_operator.py:2790`)라 적어 두고, 실제로는 **DL이 손대지 않는 그룹까지 손실 확정으로 읽고 있었다.**

★**SA 직접 호출로 재확인 완료(2026-09-05 16:5x KST, 코디네이터 `935aae94` · prod 읽기 전용)** — 위 표는 SQL 근사였으나 `effective_bid.adgroup_effective_bid()`를 67그룹 전건에 직접 호출한 결과가 **같은 수**다: 실효입찰 ≤70원 **0/67** · `14일 비용 < 실효입찰×10` **42/67** · 스코프 안 4그룹 중 통과 **1건(`grp…59832147`)**. 실효입찰 분포는 **150~2,190원**(근사판의 「150~1,990」은 상단만 달랐고 판정 수는 동일). 스코프 안 4그룹 실측: `59832147` 그룹입찰 50/실효 150(src=ad)·14일비용 1,117 → **표본부족** / `59832150` 1000/460·13,946 → 출혈확정 / `70992078` 50/550·30,949 → 출혈확정 / `70992116` 50/1000·18,222 → 출혈확정. ⇒ **「바닥 입찰」은 그룹입찰(명목)의 착시**임이 SA로 확정됐다 — 코디네이터가 앞서 보고한 「바닥 38그룹」은 그룹입찰 기준이었고 **정정한다**.

---

## §1. 목표 / 이번에 안 하는 것

**목표** — 탐색 레인 가드5가 「bleeding 보드에 있다」 하나로 그룹을 제외하던 것을, **「bleeding ∧ 14일 비용 ≥ 실효입찰×`LOW_CLICK_THRESHOLD`(스톱로스 상당)」일 때만 제외**하도록 바꾼다. 그 미만은 «손실 확정»이 아니라 «표본 부족»이므로 탐색(증거 구매)의 대상이다. 이로써 **바닥에 내려간 그룹이 다시 탐색 후보가 되는 순환이 코드상 닫힌다.** 사람이 안 봐도 닫혀야 한다(Jino 질문의 요지).

**★적용 범위 — 카나리 스코프 안에서만.** 이 변경은 승인 문 `engine_approve`(`auto_operator.py:526`) **앞**에 있으므로 스코프 밖 그룹은 종전과 같이 pending으로 남고 `blocked(스코프 밖)` 일기만 남는다. **스코프 «밖»에서는 실쓰기 행위 변화 0**이어야 하고 그게 §4-C ⓗ의 체크박스다. 바뀌는 것은 일기의 «사유문»뿐이다(「bleeding 제외」→「스코프 밖」).

**이번에 안 하는 것** (인접하지만 범위 밖):
- **`_bleeding_hold_reason` 자체는 한 글자도 안 고친다** — 일 레인 bid_up 조건④(`_check_bid_up_conditions`)가 공유한다. 조건은 가드5 **안에** 얹는다.
- 가드5의 두 번째 조건(`shopping_pause_candidates` 보드 제외)은 **불변** — 그것이 §4-C ⓙ 대칭의 브레이크 쪽 반환점이다.
- retro stale/부재 fail-closed(`auto_operator.py:917-926`) 불변 — 「모름=괜찮음」 금지.
- **스코프 확대**(S23울트라·S23플러스·S24울트라·사생활 S23울트라 4후보) — 별건. 사생활 `auto_operate` 재점화도 별건(Jino 스위치).
- **래칫 지대**(핫셋 clk≥10 vs 표본 하한 전환 7의 경계) — 별건 계약. 이 계약은 clk<10 탐색 후보만 다룬다.
- 표본 하한 게이트(D-NAO-286) 값·대상·면제 — 불변. 탐색은 이미 면제(`guardrail_gate.py:117-119`)라 이 계약이 닿지 않는다.
- 탐색 래더의 봉투(스텝 30%·경제성 상한·쿨다운 2h·무노출 3스텝·유령 홀드·CTR 경보 skip) 값 변경 0.
- `frontend/**` · 콘솔 화면(`pao-uiux` 체인 독점) · 북극성 문서 · 트랙 파일(구현 세션이 확인줄만).
- 예산 변경 개방 0 · 새 자동 쓰기 경로 0 · 새 SA 파일 0.
- **작업 종류**: 리팩터 / 새 헬퍼 모듈 추가 / 테스트 범위 확대 / 보드(`account_diagnosis`) 수정 / retro 창 길이 변경.

---

## §2. 판단기준 — "항상 A, 왜냐하면 ○○"

1. **항상 임계값은 기존 상수를 «같은 객체로» 재사용한다**(`account_diagnosis.LOW_CLICK_THRESHOLD`), 왜냐하면 이 저장소의 상습 실패가 「근거 없는 상수」와 「복제된 상수가 갈라짐」(D-NAO-125·172)이고, 스톱로스 보드가 이미 그 값으로 «표본 부족»을 정의해 두었기 때문이다.
2. **항상 «출혈이 아니라 표본 부족»인 그룹만 연다**, 왜냐하면 목적함수가 총이익 절대액(D-NAO-59)이고 볼륨 0=이익 0이며, 14일 지출이 10클릭치도 안 되는 그룹의 ROAS 0은 «손실 확정»이 아니라 «판정 불가»이기 때문이다.
3. **항상 브레이크는 그대로 두고 액셀의 «오판»만 벗긴다**, 왜냐하면 스톱로스 보드(가드5 두 번째 조건)는 지출이 임계에 닿는 순간 같은 그룹을 다시 잡으므로 — 탐색이 허용되는 구간과 스톱로스가 잡는 구간이 **정확히 상보**라 §7 대칭이 구조로 성립하기 때문이다(§4-C ⓙ가 이를 «수»로 증명한다).
4. **항상 스코프 밖 행위 변화 0을 테스트와 라이브 둘 다로 증명한다**, 왜냐하면 07-30 이후 «죽은 카드 119건»(`engine_approve` docstring)의 전례가 「레인은 열렸는데 실행은 막힘」이 사람 눈에 «실행 가능»으로 보였던 사고이기 때문이다.
5. **항상 사유문이 새 판정을 원문으로 말한다**, 왜냐하면 이 트랙의 라이브 진단이 `ops_diary_entries` 사유문 집계(§0의 1,761건)로만 가능했고, 사유문이 안 바뀌면 «켜졌는지»를 아무도 못 재기 때문이다.
7. **항상 대기 카드 적체를 «부작용»으로 세어 보고한다**, 왜냐하면 이 변경이 스코프 밖에서 만드는 유일한 실물이 그것이고, 「스코프가 좁아 대기하는 카드」와 「승인할 사람이 없어 죽은 카드」는 **화면에서 같은 숫자로 보이기** 때문이다(D-NAO-285 A/B 정리의 B 항목).
6. **항상 0건은 「안 걸렸다」이지 「됐다」가 아니다**, 왜냐하면 현 스코프(TPU 1그룹=핫셋)에서 이 변경의 실쓰기 효과는 **0건이 정상**이기 때문이다 — 그걸 「됐다」로 읽는 순간 교훈 #123의 재발이다.

---

## §3. 금지선

- **표본 하한 게이트(D-NAO-286) 값 재조정 금지** — `min_weekly_conv_campaign`·`min_weekly_conv_target`·`_FLOOR_GATED_TYPES` 불변. `backend/tests/test_naver_guardrail_floor_gate.py` 전건 유지(2026-09-05 QA 실측 43 passed).
- **스코프 확대 금지**(별건) — `naver_adgroup_scope` 행 추가·`auto_operate` 변경을 이 계약으로 하지 않는다.
- **래칫 경계값(핫셋 clk 10 · 표본 하한 전환 7) 손대기 금지**(별건) — `_MIN_CLICK_FOR_APPROVAL`·`_MIN_CLICK_FOR_EXPLORATION`·표본 하한 파라미터 불변.
- **예산 변경 개방 금지**(트랙 불변 금지선).
- **새 상수 신설 금지** — 「작다」·「바닥」·「N일」을 숫자로 발명하지 않는다. 임계는 §2-1의 기존 상수 한 벌뿐이며, 그것으로 안 되면 구현하지 않고 §10으로 돌아온다.
- **가드5 두 번째 조건(스톱로스 보드 제외) 삭제·완화 금지** — 브레이크만 남기는 수정도, 액셀만 여는 수정도 금지(북극성 §7).
- 되돌릴 수 없는 액션(prod 배포)은 §2 승인 지점을 상속 — `scripts/safe_deploy.sh`로만, CAS 거부 시 덮지 않는다. `--force`·`git add -A` 금지.
- 스코프 밖 그룹에 실쓰기 1건이라도 나가면 **즉시 revert**(§4-C ⓗ 위반 = 계약 실패).

---

## §4. 합격기준 — 라이브 증거를 체크박스로, 표면을 지목해

### §4-A. 산출물 (구현)

**S1 — 가드5에 «표본 부족 예외» 한 단** (`auto_operator._exploration_daily_loss_reason`)
- `_bleeding_hold_reason`이 「④최신 소급채점에서 bleeding」을 돌려준 경우에 **한해**, 같은 최신 asof·`shopping_group_bep`·`target_id` 행의 `cost_asof`(모델 `NaverRetroSignal.cost_asof`, `models.py:2973` — 보드가 본 그 14일 비용 그대로)를 읽고, `cost_asof < effective_bid × account_diagnosis.LOW_CLICK_THRESHOLD`이면 **None(탐색 허용)**을 돌려준다. stale/부재/기타 사유는 종전 그대로 제외.
- 실효입찰은 레인이 이미 파생하는 `effective_bid.adgroup_effective_bid(db, adgroup_id, current_group_bid)`(`auto_operator.py:3160`, SA `effective_bid.py:274`)의 `effective_bid`를 쓴다. 현재 호출 순서가 손실검사(`:3137`) → 실효입찰(`:3160`)이므로 **손실검사를 실효입찰 파생 뒤로 옮기거나 실효입찰을 인자로 넘긴다**(값을 두 번 계산하지 않는다 — D-NAO-265 재발 방지).
- 두 번째 조건(`shopping_pause_candidates`)은 **예외 뒤에도 그대로 검사**한다 — 표본 부족이어도 스톱로스 보드에 오른 그룹은 제외.

**S2 — 사유문** — 허용 시 레인은 이후 래더 판정(`start/step_up/reactivate/…`)의 기존 일기를 남긴다. 스코프 밖이면 `engine_approve`가 `자동운영 스코프 밖 광고그룹(…) — D-NAO-244`를 남긴다(종전 문구). 제외가 유지되는 경우 사유문에 **비교값을 원문으로** 싣는다: `daily 손실상태 제외 — ④bleeding ∧ 14일 비용 {cost_asof}원 ≥ 스톱로스 {eff×10}원(D-NAO-289)`.

**S3 — 회귀 고정 테스트** (§4-B) · **S4 — 라이브 계수 SQL**(§5)

### §4-B. 테스트 (`backend/tests/test_naver_exploration_revival.py`)

- [ ] ⓐ bleeding 행 존재 ∧ `cost_asof` < 실효입찰×10 → 가드5가 **None**(허용). 픽스처는 §0 실측 그룹 `grp…59832147`의 구조(그룹입찰 50 · 소재 bidAmt 150 · `cost_asof` 1,117)로 만들고, 실효입찰은 SQL 근사가 아니라 **SA(`effective_bid`) 호출**로 얻는다.
- [ ] ⓑ bleeding 행 존재 ∧ `cost_asof` ≥ 실효입찰×10 → **제외 유지**, 사유문에 두 값이 원문으로 실린다(픽스처: `grp…59832150` 구조, 460×10=4,600 ≤ 17,617).
- [ ] ⓒ retro stale / 부재 → 종전 사유 그대로 제외(fail-closed 불변).
- [ ] ⓓ `cost_asof` < 임계여도 `shopping_pause_candidates`에 있으면 제외(두 번째 조건 생존).
- [ ] ⓔ 임계 상수 정합: 가드5가 읽는 값 `is account_diagnosis.LOW_CLICK_THRESHOLD`(복제 리터럴 금지) — 값을 11로 바꾸면 ⓐ·ⓑ 경계가 같이 움직인다.
- [ ] ⓕ **스코프 밖 행위 변화 0**: ⓐ 조건의 그룹이 스코프 밖이면 레인이 제안을 만들되 `engine_approve`가 False를 돌려 `execute`가 호출되지 않는다(호출부 절단 변이 대상).
- [ ] ⓖ 표본 하한 게이트 전건 통과 · 탐색 관련 기존 테스트(`test_exploration.py`·`test_naver_ad_exploration_bx3.py`·`test_naver_hold_reasons_and_inday_catchup.py`) 전건 통과 · 백엔드 회귀 전건(기준선 2026-09-05 QA 7,852 passed).

### §4-C. 라이브·대칭 증거 (배포 후 · prod 읽기 전용)

- [ ] ⓗ ★**스코프 밖 실쓰기 0건** — 배포 후 7일, `naver_change_log ⋈ naver_proposals`에서 `proposal_type='bid_up_explore' ∧ after_value IS NOT NULL ∧ dry_run=0`인 행의 `adgroup_id`가 **전건 `naver_adgroup_scope(enabled=1)` 안**이다. 1건이라도 밖이면 §3 위반.
- [ ] ⓘ **사유문 이동이 관측된다** — `ops_diary_entries` actor=`explore`에서 24시간 창 기준 `④최신 소급채점에서 bleeding` 건수가 배포 전(§0: 14일 1,761건 ≈ 일 126건)보다 줄고, 같은 그룹들이 `자동운영 스코프 밖` 사유로 옮겨 간다. 예상 규모 🧠 42그룹 — 예상과 다르면 그 수를 그대로 적는다.
- [ ] ⓙ ★**§7 액셀·브레이크 대칭 — «수»로 보고**: 배포 후 7일 창에서 ①탐색 UP 실쓰기 수(`explore_op`) ②브레이크 실쓰기 수(`auto_op_hr` 고삐·CPC급등 down + `auto_op` bid_down + `revert_op`) ③**탐색이 허용됐다가 지출이 임계에 닿아 `shopping_pause_candidates`(가드5 두 번째 조건)로 다시 막힌 그룹 수**. ③이 0이 아니어야 «상보»가 라이브로 증명된 것이고, ③이 0이면 「도달 안 함」으로 적는다(됐다가 아니다). 브레이크 쪽 수가 배포 전 창 대비 **줄지 않았음**을 병기한다.
- [ ] ⓚ **현 스코프의 실쓰기 효과** — TPU 스코프 그룹 1개는 핫셋이라 탐색 대상이 아니고, 사생활은 마스터 OFF다. 따라서 배포 후 스코프 안 탐색 실쓰기는 **0건이 예상**이며, 0건이면 「0건 — 스코프 구조상 정상」으로 적는다. 0이 아닌 값이 나오면 그 경로를 §10에 새로 적는다.

- [ ] ⓜ ★**대기 카드 증가량을 «수»로 보고한다** — 이 변경의 알려진 부작용이다. 스코프 «밖» 그룹은 종전에 가드5에서 막혀 **제안 자체가 안 만들어졌는데**, 열면 제안이 만들어진 뒤 `engine_approve`에서 막혀 **pending으로 적체**된다. 그리고 **`bid_up_explore`에는 만료가 돌지 않는다**(2026-09-05 16:5x KST 실측: 최근 30일 `expired` 목록에 `bid_up_explore` **0건**, 반면 `bid_up` 632·`trigger_pacing` 2,756은 만료가 돈다). 배포 전 기준선은 **전체 pending 1,164건(전건 스코프 밖) · 그중 `bid_up_explore` 728건 / 23그룹**이다. 배포 후 7일 그 두 수를 다시 세어 **증가분을 그대로 적는다.** 증가가 「예상 42그룹분」을 크게 넘으면 그 이유를 §10에 적는다.
  ★**이 부작용을 «허용»으로 판정한 근거**(§2-7): 카드가 쌓이는 것은 「승인할 사람이 없어서」가 아니라 **「스코프가 좁아서」**이고, 스코프가 넓어지는 순간 그 카드들이 곧 후보가 된다. 즉 이 적체는 **버려지는 것이 아니라 대기하는 것**이다. 다만 이 저장소는 「대기카드 899·죽은카드 133」의 전례가 있으므로, 만료 부재는 §10에 부채로 남긴다.

### §4-D. 표면 — 사람이 어디서 보는가

★이번 슬라이스의 사람 표면은 **판정 사유문**이다(`frontend/**`가 안 함) — `ops_diary_entries.rationale`(actor=`explore`, event_type=`blocked`/`observe`/`execute`)과 `run_hourly_lane` 결과의 `held_by_reason` 집계(`auto_operator.py` 레인 말미).
- [ ] ⓛ 배포 후 prod에서 **S2 사유문(비교값 원문)이 최소 1건 기록된 것**을 타임스탬프와 함께 관측한다. 0건이면 그대로 적는다.

**표면파일**: `backend/app/services/naver_ad/auto_operator.py`
**표면: 판정 사유문(운영 일기 `ops_diary_entries` · `held_by_reason`) — 화면 없음(frontend 안 함)**

---

## §5. 실행 — 돌리면 결과가 보이는 명령

```bash
cd /Users/jino/ohisell-pao-n89/backend && python3 -m pytest \
  tests/test_naver_exploration_revival.py tests/test_naver_hold_reasons_and_inday_catchup.py \
  tests/test_naver_ad_exploration_bx3.py tests/test_exploration.py tests/test_naver_guardrail_floor_gate.py -q
```

라이브 계수(§4-C ⓗ·ⓘ, prod 읽기 전용 · 쓰기 0):
```bash
ssh -o BatchMode=yes sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && sqlite3 -header ohisell.db \
 \"SELECT substr(rationale,1,48) r, count(*) n FROM ops_diary_entries WHERE actor='explore' AND event_type='blocked' AND created_at>=datetime('now','-1 day') GROUP BY 1 ORDER BY n DESC LIMIT 8; \
   SELECT count(*) out_of_scope_writes FROM naver_change_log c JOIN naver_proposals p ON p.id=c.proposal_id WHERE p.proposal_type='bid_up_explore' AND c.after_value IS NOT NULL AND c.dry_run=0 AND c.changed_at>=datetime('now','-7 day') AND p.adgroup_id NOT IN (SELECT adgroup_id FROM naver_adgroup_scope WHERE enabled=1);\""
```
(두 번째 수는 **항상 0**이어야 한다 — ⓗ.)

---

## §6. 항목 — 구현 목록 (진입점·배선·렌더까지)

1. `auto_operator.py` — `_exploration_daily_loss_reason`에 «표본 부족 예외» 한 단(S1): bleeding 사유일 때만 `NaverRetroSignal.cost_asof` 1회 조회 + `effective_bid × LOW_CLICK_THRESHOLD` 비교. 시그니처에 실효입찰(또는 `eff` dict) 인자 추가.
2. `auto_operator.py` — `_run_exploration_for_campaign`에서 실효입찰 파생(`:3160`)을 손실검사(`:3137`) **앞**으로 옮기고 그 값을 넘긴다. CTR 경보 skip·트리거 순서는 불변.
3. `auto_operator.py` — 제외 유지 사유문에 비교값 원문(S2). 허용 시 별도 일기는 만들지 않는다(래더 일기가 이어받음 — 소음 원칙 `_record_blocked` docstring).
4. `backend/tests/test_naver_exploration_revival.py` — §4-B ⓐ~ⓕ.
5. 문서: 트랙 `확인:` 1줄 · 이 계약의 체크박스 갱신 · `docs/references/`에 §0·§4-C 실측 1건(번호는 `scripts/next_ids.sh`).
6. 배포: `scripts/safe_deploy.sh backend/app/services/naver_ad/auto_operator.py --restart`(마이그레이션 0).

---

## §7. 예산

- 코드: `auto_operator.py` 1파일 + 테스트 1파일 · 순증 **80줄 이내** 목표. ★직전 계약(D-NAO-288)은 150줄 목표에 실측 315줄이었다 — 이 계약은 함수 1개 안의 분기 1개라 그 실적을 보고도 80으로 둔다. 넘으면 이유를 §11에 적는다.
- 세션: 1세션 · 적대 리뷰 1R + 필요 시 2R.
- prod 쓰기: **배포 1회**(백엔드 1파일 · 마이그레이션 0 · 무중단 재시작). 네이버 광고계정 쓰기는 배포 후 레인이 스코프 안에서만 — 현 스코프 예상 0건(§4-C ⓚ).

---

## §8. 종료 조건

- **적대 리뷰 1회**(PR 경계 · 구현과 다른 기 · 계약 §4·§0 원문 동봉). 변이 의무 셋: ①**사유문 절단**(비교값이 안 실리는 변이) ②**`engine_approve` 호출부 제거**(스코프 밖 실쓰기가 새는 변이 — 마지막 표면) ③`LOW_CLICK_THRESHOLD`를 리터럴 10으로 복제하는 변이(ⓔ가 잡아야 한다). → **P1=0이면 PASS.**
- **완료 QA 1회**(별도 Sonnet · 읽기 전용) — 대조 3: ①이 계약 §4 ②Jino 질문 원문(§0) ③북극성 §6 M4·§7.
- 이유: 크기는 M(1파일·함수 1개)이지만 **폭발 반경이 자동 입찰 실쓰기**라 전역 §1 규칙(돈에 닿으면 리뷰)으로 리뷰를 붙이고, 「영원히 기회를 잃는가」는 코드가 옳은가와 다른 질문이라 완료 QA를 붙인다. 트랙 종결 QA 대상 아님(M4는 닫히지 않는다).

---

## §9. 북극성 대비 검토 (D-NAO-226 · 227 — 4줄)

1. **어느 M인가**: **M4**(L3 재개 — 카나리 `optimizer='ours'`). 그 안에서 §5-3 ③ *"학습 루프의 회전 속도는 결국 L3 재개 폭이 정한다"*의 «폭»이 탐색 레인 쪽에서 0으로 닫혀 있던 것을 연다. M4 합격 관측(실집행 diary + 가드레일 위반 0 + 되돌림 0)에는 **닿지 않는다** — 현 스코프에서 실쓰기 0이 정상이라(§4-C ⓚ) 이 슬라이스로 M4가 전진했다고 쓰지 않는다.
2. **5요소 중 움직이는 것**: **④자동화 운영**(탐색 레인의 자동 소생 경로) · **⑤학습·상시개선**(증거 구매가 다시 가능해짐). ①②③은 0. ④가 «0이 아닌» 두 번째 연속 세션(직전 D-NAO-288).
3. **§7 금지선 검사**: 이 변경은 **액셀을 여는 쪽**이다 — 그래서 §2-3·§4-C ⓙ가 브레이크 상보를 «수»로 재고, §3이 스톱로스 보드 조건 삭제를 금지한다. 「홀드아웃 없이 발견 집행 금지」: 이건 발견의 집행이 아니라 탐색 정책의 오판 수정이며 카나리 스코프 밖 행위 변화 0을 ⓗ로 못 박는다. 「표본이 준 결정을 전수로 굳히지 않는다」: 정확히 그 문장의 집행이다(표본 없는 ROAS 0을 손실로 굳히던 것을 푼다).
4. **관련 절 전부 읽었는가**: §목차부터 훑음(§0~§9+부록 A). 이 세션이 원문으로 읽은 절 — §1(원문, 위임문 인용) · §5-3 ③ · §6 M4·6-b · §7(전문) · §8(①②가 이 계약의 전제 — 소유권 분리 미결이므로 스코프 밖 0을 계약으로 못 박는다). §2·§3·§4 간극3은 표만 읽었다. **구현 위임문에는 §7 원문과 이 계약 §0·§3을 싣는다.**

---

## §10. [미상] — 이 계약이 모르는 것

- ★**부채(미상 아님 — 실측된 결손)**: `bid_up_explore` 제안에 **만료가 돌지 않는다**(30일 `expired` 0건). 다른 유형(`bid_up`·`trigger_pacing`·`search_term_promote`)은 만료가 돈다. 이 계약은 만료를 **신설하지 않는다**(§3 「새 상수 신설 금지」·범위 밖) — 대신 §4-C ⓜ이 증가분을 세어 다음 계약의 재료로 남긴다. 만료 부재의 원인(설계인지 누락인지)은 **확인 안 됨**.

- ~~§0의 「42그룹」은 SQL 근사~~ → **해소**(2026-09-05 16:5x KST): SA 직접 호출 결과 42/67로 동일. 남은 미상은 «그 42가 배포 후 실제로 사유문이 옮겨 가는가»이고 그것은 §4-C ⓘ가 라이브로 판정한다.
- §4-B ⓑ 픽스처의 `cost_asof` 17,617은 **보드의 14일 창** 값이고, 코디네이터가 잰 `-14d~-1d` 창은 13,946이다 — 임계 4,600 대비 판정(출혈확정)은 같으나 **두 창이 다르다**. 구현은 반드시 보드의 `cost_asof`를 쓰고, 테스트 픽스처도 그 값으로 고정한다.
- 가드5의 두 조건 중 `shopping_pause_candidates`가 막은 건수 — 일기에 사유가 «bleeding»으로만 남아 배포 전 분리 불가.
- 이 예외가 열어 준 그룹에 탐색이 **실제로 스텝을 쏠지** — 뒤에 경제성 상한(`exploration_ceiling`)·유령 홀드·CTR 경보·무노출 종료가 있다. §0 실측에서 `첫 탐색이나 경제성 상한 이미 도달(현 1900≥상한 1710)` 48건이 보인다 — 상한이 다음 병목일 수 있으나 이 계약은 재지 않는다.
- 사생활 캠페인 `auto_operate` 재점화 여부·시점(Jino 스위치) — 그 전엔 스코프 안 라이브 효과가 원리적으로 0.
- 09-04 대행사 93건 변경(−62%)이 탐색 기울기 연속성(`continuity_ok`, `auto_operator.py:3200-3206`)을 끊는지 — 코드상 보수 10% 스텝으로 떨어지나 라이브 미관측.
- retro stale 차단 981건(08-29~31)의 원인(크론 실패 여부) — 이 계약 밖, 기록만.
- 터미널 pause가 07-28 이후 0건인 이유(카나리 ad 라우팅인지 다른 게이트인지) — 잠재 «복귀 불가» 경로(`shopping_resume_candidates`는 정지 직전 창 ROAS≥목표를 요구해 zero_conv 정지는 원리상 복귀 불가)의 활성 여부.
- 일기 `created_at`의 시간대(UTC 추정) — §0의 시각은 DB 원문이며 KST 환산하지 않았다.
