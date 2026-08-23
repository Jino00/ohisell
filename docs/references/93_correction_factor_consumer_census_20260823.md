# 93 — 보정계수(correction_factor) 소비처 전수·감도 실측 (D-NAO-230 산출물 A)

## §0. 무엇을 왜 셌는가

D-NAO-21 보정계수(네이버 채널 전체 주문매출 ÷ 광고 전환매출, `diagnosis.py:19-47`)가 소비되는 모든 지점에서
**계수가 오르내릴 때 판정이 액셀(확장·상향) 쪽으로 가는지 브레이크(정지·차단·하향) 쪽으로 가는지**를 확정했다.
북극성 §7이 지목한 상습 실패 모드(「ROAS 방어로의 표류」, D-NAO-85 실측: 2026-07-23 ROAS +7%·매출 −52%)의
재발 여부를 이 자(尺) 층에서 검사하는 것이 목적이다. **이 표는 계약 `docs/PLAN_naver-profit-yardstick-review.md`
(D-NAO-230, 승인됨)의 산출물 A다** — 안3(구간 자: 브레이크=상한·액셀=하한) 적용의 선행 조건.

조사 일시: 2026-08-23 KST 12:2x~13:4x경 (서버 UTC 03:2x~04:4x). 방법: 코드 정독(9개 파일 전체 grep 재확인) +
prod DB 읽기전용 실측(`ssh sellc.ohitech.co.kr "sqlite3 -readonly ...ohisell.db"`) + prod
`/api/naver/ad/diagnosis` 읽기전용 조회. **DB/API 쓰기, 코드 수정, 배포, 크론 수동 실행은 하지 않았다.**

라이브 기준값(2026-08-23 실측):
- `correction_factor` = **1.3108**(source=`actual_revenue_ratio`, window 2026-07-25~2026-08-23,
  window_revenue=45,220,860 ÷ window_conv_amt=34,499,980)
- `account_bep_roas` = **1.683290433167481**, `account_target_roas` = **1.9357872842124666**
- 진단 보드 window(diagnosis API 기본값) = 2026-08-09~2026-08-23(15일)

## §1. 전수표

19행 — 9개 파일 중 `account_diagnosis.py`(파일 #2)는 **판정 함수 단위로 10행**(2a~2j)으로 쪼갰다(위임문 지시
"같은 파일 안에서도 정지 후보를 고르는 함수와 확장 후보를 고르는 함수는 방향이 반대"). 열 순서: 좌표 /
무엇을 판정하는가 / 계수 내려가면 방향(+산술 근거) / 안3 끝(端) / 감도.

| # | 좌표 | 무엇을 판정하는가 | 계수↓ 시 방향 (산술 근거) | 안3 끝(端) | 감도(±10%) |
|---|---|---|---|---|---|
| 1 | `diagnosis.py:70-127`(호출 107-109·125) | **판정 없음** — `correction_factor()`(19-47)가 산출한 factor를 account_diagnosis의 각 판정함수에 그대로 주입하는 허브(SA간 직접호출 금지 원칙18). 실제 판정은 전부 2a~2j에 있다 | 해당없음(전달만) | 해당없음 | 해당없음 |
| 2a | `account_diagnosis.py::bleeding_keywords` (110-125, `_corrected_roas`72-76 재사용) | WEB_SITE 키워드 중 `roas_c(=roas_naver×factor) < bep_roas` → 「출혈」 후보(→`proposal_writer.py`가 `bid_down`만 생성, `_BOARD_DIRECTION["bleeding_keywords"]={"down"}`, `proposal_writer.py:101`) | **브레이크 강화** — `roas_c`가 `bep_roas` 미만으로 곱셈 스케일되어 더 쉽게 미달(분자 factor 직접 곱) | **상한** | **실측**: 현재 라이브 600건(원본 749건 중, cost>0 WEB_SITE 키워드-grain). ±10% flip-zone(raw roas∈[1.1674,1.4269)) = **4건**(전체 749건의 0.5%) |
| 2b | `account_diagnosis.py::starving_winners` (128-146) | WEB_SITE 키워드 중 `roas_c ≥ target_roas` ∧ 일평균클릭<1 → 「굶는 승자」(D-NAO-18 육성 파이프라인 입력, `bid_up`만 — `_BOARD_DIRECTION["starving_winners"]={"up"}`) | **액셀 약화** — `roas_c` 하락으로 target_roas 미달 전환 증가 → 통과 건수 감소 | **하한** | **실측**: 현재 135건(clk<15 필터 포함). ±10% flip-zone(raw roas∈[1.3425,1.6409), clk<15) = **2건** |
| 2c | `account_diagnosis.py::expansion_bucket` (149-182) | WEB_SITE 확장('') 버킷 총계의 `roas_corrected`를 **기록만** — 임계 비교 없음(cost_share 등 서술 통계) | **판정 아님(기록값만)** — 표시값이 흔들릴 뿐 게이트 없음 | 해당없음 | 해당없음(구조상 무의미) |
| 2d | `account_diagnosis.py::shopping_group_bep` (185-218) | SHOPPING adgroup 중 `roas_c < bep_roas` → BEP 미달(`bid_down`만, `_BOARD_DIRECTION["shopping_group_bep"]={"down"}`) | **브레이크 강화** (2a와 동일 산식, adgroup grain) | **상한** | **실측**: 현재 65건(원본 156건 중). flip-zone 동일 밴드 = **6건**(전체 156건의 3.8%) |
| 2e | `account_diagnosis.py::vicious_cycle_flags` (290-365) | `declining`(최근7일roas_c < 이전23일roas_c×0.9) ∧ `thinning`(클릭추세하락) ∧ `below_target`(recent_roas_c<target_roas) 3조건 AND → 「악순환」 경보 | **부분** — `declining`은 **계수에 불변**(아래 유도 참조), `below_target`만 계수에 민감. 그러나 **어떤 proposal_writer 액션에도 연결되지 않는다**(grep 0건, `proposal_writer.py`/`auto_operator.py` 미소비) — 화면 경보판일 뿐 | 해당없음(비집행) | **실측(분석적)**: 현재 4건 전부 확인. 3건은 `recent_roas_corrected=0.0`(계수 무관 — 0×factor=0, 항상 below_target 유지). 1건(`recent_roas_corrected=1.7174`)은 raw=1.3103, +10%(factor=1.44188)에서도 roas_c=1.8896<target 1.9358로 **flip 없음**. 현재 4건 중 flip=**0건**. 새로 진입할 캠페인은 [부분실측] — 전 캠페인 스윕 미실행(3조건 재현 비용 높음, §5) |
| 2f | `account_diagnosis.py::resume_candidates` (617-708) | 우리가 정지시킨 WEB_SITE 키워드 중 정지 직전 30일 `roas_c ≥ target_roas` → 재개 후보(`resume`, `proposal_writer.py:714-` `_resume_proposal`) | **액셀 약화** — 재개(=지출 재개=확장 방향) 조건이 더 엄격해짐 | **하한** | **[미실측]**: 현재 라이브 0건. off 키워드 918개 존재(§1 실측)하지만 proposal_id 有+userLock=True+정지직전창 실적有 게이트를 아무도 통과 못함(대부분 수동정지 추정) — 확정하려면 `naver_change_log`에서 `entity_type='keyword', action IN ('set_user_lock','external_status_change'), proposal_id IS NOT NULL`인 최신 잠금행을 JSON 파싱해 대상 좁힌 뒤 재계산 필요(높은 구현비용, §5) |
| 2g | `account_diagnosis.py::shopping_pause_candidates`(lever_broken 경로만, 838-865) | SHOPPING adgroup 전환有·at-floor·**레버끊김**(만성7일창 실측CPC>5×실효입찰)·`roas_c(7일 만성창)<bep_roas` → 터미널 pause(`_stop_loss_proposal`, `proposal_writer.py:660`) | **브레이크 강화** — `roas_c` 하락으로 BEP 미달 조건이 더 쉽게 성립 | **상한** | **실측**: 현재 라이브 3건 **전부 `reason=zero_conv`**(무전환 경로 — cost·conv_amt만 쓰고 factor 무관)이고 `lever_broken=False`. 즉 **오늘은 이 경로(factor 의존)에 걸린 건이 0건** — 감도=**0**(멤버 자체가 없음). zero_conv 경로(2g와 분리)는 factor 불변 |
| 2h | `account_diagnosis.py::floor_wait_units` (888-1054) | 쇼핑 전환有·레버정상·at-floor·`roas_c<bep_roas`(내부 판정 有) 및 키워드 무전환 at-floor를 관찰 목록화 | **판정 아님(기록값만)** — docstring 명시: "★관찰 전용(실행 없음): 이 보드는 어떤 제안·쓰기 경로에도 연결되지 않는다(§0 금지선)" | 해당없음 | 실측(라이브 0건) — 감도 산출 무의미 |
| 2i | `account_diagnosis.py::shopping_group_growth` (1057-1127) | SHOPPING adgroup 중 `roas_c ≥ target_roas_resolver(campaign_id)` → 성장 후보(`bid_up`, `_BOARD_DIRECTION["shopping_group_growth"]={"up"}`) | **액셀 약화** (2b와 동일 방향, adgroup grain, target 임계) | **하한** | **실측(근사)**: 계정 기본 target_roas로 근사 계산 시 87건(라이브 실측 85건, 캠페인별 override 차이로 ±2 오차 — [부분실측], §5). flip-zone(동일 target 밴드) = **12건**(근사치의 13.8%) — 9개 판정 중 상대적으로 가장 민감 |
| 2j | `account_diagnosis.py::shopping_resume_candidates` (1176-1242) | 우리가 정지시킨 SHOPPING adgroup 중 정지 직전 30일 `roas_c ≥ target_roas` → 재개 후보(`resume`) | **액셀 약화** (2f와 동일 방향, adgroup grain) | **하한** | **[미실측]**: 현재 라이브 0건. off adgroup 220개 존재하지만 게이트 통과 0(2f와 동일 사유) — 확정 방법도 2f와 동일 |
| 3 | `naver_execution_harness.py` (import 75, 사용 924·1003·1091 — `_build_guardrail_context`) | `roas_corrected = roas_naver × factor`를 계산해 `guardrail_gate.check()`에 주입 — **오직 `BID_UP_TYPES`(증액) 제안에서만** 채움(adgroup/ad 분기는 `if proposal.proposal_type in BID_UP_TYPES:` 명시 가드, keyword/campaign 분기는 무조건 채우지만 `guardrail_gate._check_bid`/`_check_budget`가 `BID_UP_TYPES`에서만 이 값을 실제 비교에 씀, `guardrail_gate.py:283,338-342,420-435`) → `roas_corrected<target_roas`면 "BEP 미달 증액 금지"로 그 증액 제안 자체를 **차단** | **액셀 약화** — 증액 제안이 통과할 확률이 낮아짐(브레이크 신설이 아니라 액셀 게이트가 더 엄격해짐). down/pause/resume 제안은 이 검사 자체가 면제라 무관 | **하한**(액셀 가드이므로 안3 원칙 그대로) | **[미실측]** — 발동은 특정 proposal이 실제로 생성된 순간에만 조건부이고(제안 없으면 게이트도 없음) 어느 순간에 몇 건의 bid_up/budget_up 제안이 대기 중인지는 실행 로그(`naver_change_log`, action='update_bid'/budget, before/after) 재구성이 필요 — 방법은 §4에 명시 |
| 4 | `expansion_allocator.py:277` (+ 320-336 소비) | 확장모드 캠페인의 쇼핑 adgroup 중 자기표본충분(clk≥`_OWN_SAMPLE_CLK`)이면 `own_ratio = (conv_amt×factor)/cost÷bep_roas`(via `gave_score.compute_gave_score`, revenue 인자에 factor 직접 곱) — `own_ratio<1`이면 **제외**, `own_ratio<EX_MARGINAL_STOP_RATIO`(slope 有)면 `marginal_stop=True` 태깅. 둘 다 확장압력 배분(액셀)을 줄이는 방향 | **액셀 약화** (이중: 제외 게이트 + marginal_stop 게이트 둘 다 factor↓에서 더 쉽게 발동) | **하한**(문서 자체가 "확장 배분") | **[미실측]** — `own_ratio`는 `gave_score.compute_gave_score`(별도 SA, 자체 감마 파라미터 有)를 거치므로 단순 SQL 밴드 재현이 아니라 그 SA 로직 재현이 필요(중간 난이도). 확장모드가 켜진 캠페인 자체가 조건부라 대상 축소는 가능 |
| 5 | `flight_loop.py` (import 49, 사용 249 — `_campaign_rpc`) | `rpc = raw_rpc × factor` → `response_curve_builder.build_response_curve(rpc=rpc)` → `pacing_controller.compute_pacing_alpha`가 `alpha_roas`(일중 페이싱 배율) 산출 | **계산상 액셀 약화 방향**(factor↓→rpc↓→예측ROAS↓→alpha_roas 하향 편향)이나, **모듈 자체가 "관측기"로 확정**(모듈 docstring 1-21행: "★★이 레인은 관측기다. 입찰을 바꾸지 않는다(Jino 확정 2026-07-29)". `dry_run`은 change_log의 **기록 라벨**일 뿐 — False로 불러도 입찰은 바뀌지 않는다, `flight_loop.py:21,312,333-336`) → **실제 판정 아님(기록값만)** | 해당없음(비집행) | 실측 불필요(집행 경로 없음). 승격 시엔 재평가 필요 — 아래 §4 참고 |
| 6 | `proposal_scoreboard.py` (124·129 `_cf_for`, 137 `_gross_profit`, 146 `_profit_verdict`) | 이미 **실행된** 제안(`before`/`after` 실측 창)의 `총이익 = conv_amt×cf/bep − cost`를 전/후 비교해 D-NAO-225 `outcome_profit`(improved/declined/neutral) **사후 채점**. cf는 `after_to` 하루치로 캐싱돼 before·after **양쪽에 동일값** 적용(`proposal_scoreboard.py:287,293`) | **조건부(방향 불확정)** — before/after가 같은 cf를 쓰므로 delta = `cf×(conv_amt_after−conv_amt_before)/bep − (cost_after−cost_before)`. cf가 곱해지는 항은 conv_amt 변화분뿐이라, **Δconv_amt의 부호에 따라 cf↓의 효과가 반대로 갈린다**(Δconv_amt>0인 건은 cf↓시 개선판정이 더 어려워지고, Δconv_amt<0인 건은 cf↓시 악화판정이 완화됨) — 살아있는 액션 게이트가 아니라 **학습/신뢰도 신호**(계약 문서가 명시: 이 값이 M4·M5 판정의 자 중 하나) | 해당없음(안3 프레임 밖 — 사후 채점은 액셀/브레이크 분류 대상이 아님, 단 §4에 미해결 질문으로 남김) | **[미실측]** — Δconv_amt 부호별 건수 분포를 얻으려면 `naver_change_log`에서 `outcome_profit` 채점 대상 전건의 before/after conv_amt를 재구성해야 함(proposal_scoreboard 실행 로그 재현, 중간 난이도) |
| 7 | `bid_simulator.py:85,114` (`rpc_corrected = rpc_raw × correction_factor`) | `economic_ceiling = affordable_ceiling(rpc_corrected, target_roas)`(≈rpc_corrected/target_roas, 10원 단위) → `recommended_bid = min(economic_ceiling, rank_bid)` → `current_bid`와 비교해 `direction`(up/down/hold) 산출 | **양면(액셀 약화 + 브레이크 강화가 하나의 값에서 동시에 발생)** — factor↓→rpc_corrected↓→ceiling↓. ceiling이 rank_bid보다 낮아 상한 역할이면 **액셀 약화**(bid_up이 눌림). ceiling이 현재입찰보다 낮아지면 그 자체로 `direction="down"`이 산출돼 **브레이크 강화**(원래 hold/up이었을 키워드가 down으로 전환). **어느 쪽이 우세한지는 개별 키워드의 current_bid 위치에 달려 있어 단일 방향 결론 불가** | **[미상 — 구조적 충돌]**: 안3 규칙("브레이크=상한·액셀=하한")이 이 함수엔 **한 값으로 동시에 두 역할**을 하므로 그대로 적용 불가. `economic_ceiling`은 구조상 상한(ceiling)이므로 표면적으로는 "액셀 상한"(§1-5 예시 원문)에 가깝고, 계약 문서도 이 파일을 「액셀」로 분류(`docs/PLAN_naver-profit-yardstick-review.md` §1-5 표 7행) — 그러나 이 census가 발견한 **"동일 ceiling이 브레이크 방향(down 전환)도 만든다"는 사실은 그 분류가 불완전함을 보여준다**. 다음 세션이 Jino에게 명시적으로 물어야 할 설계질문(§4) | **[미실측]** — `rpc_corrected`는 4단 계층 베이지안 수축(`pooled_rpc`: 키워드→그룹→캠페인→계정)을 거치므로 단순 SQL 재현 불가. 개별 키워드마다 4단계 집계가 필요해 최고난도 |
| 8 | `exploration.py:332` (`exploration_ceiling`, 320-341 주석) | 탐색(콜드 그룹) UP 경제성 상한 — **의도적으로 `correction_factor` 미적용**, `bid_simulator.affordable_ceiling`에 **raw rpc**(비보정)를 그대로 태움 | **불변(구조적)** — 코드가 factor를 아예 읽지 않으므로 계수가 얼마든 이 판정엔 영향 없음 | **해당없음**(안3 적용 대상 아님 — raw 자체가 이미 "하한보다 보수적인" 선택) | 실측 불필요(0, 구조상 자명) — **단, 주석이 스스로 밝힌 전제가 흔들림**: 주석 원문 "보정계수는 네이버 과소보고를 상향 보정(>1)해 상한을 높인다 ... 상한을 낮게 잡는 쪽이 안전하므로 raw를 쓴다"는 **factor>1을 전제**로 한 논거다. 라이브 factor=1.3108(>1)이라 지금은 논거가 성립하지만, **factor가 1 미만으로 재확정되면(안3 채택으로 하한=1.0 검토 중, §8-8) "raw가 곧 보수값"이라는 전제 자체가 무너진다** — 이 파일의 재판정 필요성은 계약 §1-5 8행이 이미 지적했고 이번 조사가 재확인 |
| 9 | `profit_scorecard.py` (15·33 import, 215 `factor_info=correction_factor(...)`, 242 `correction_factor_source`) | 캠페인별 `총이익 = conv_amt×factor/bep_roas − cost`를 어제/7일평균/6월대비로 계산해 **Slack + diary에 표시만** — API 응답에 쓰기 없음, 어떤 제안도 생성 안 함(모듈 헤더: "관찰 전용, 실쓰기 0") | **판정 아님(표시값 왜곡)** — 자동 게이트는 없지만 **사람(Jino)이 읽는 숫자 자체가 factor에 종속**. 계약 문서(§1-3) 실측 인용: 계정 30일 총이익이 **보정적용 +5,963,568원 ↔ 미적용 −234,545원**로 부호가 갈린다(트랙 확인줄 2026-08-22 [a3343a99]) | 해당없음(안3 프레임 밖 — 표시 전용) — 단 안3 채택 시 이 카드가 「구간 양끝 병기」 표면 후보 1순위(계약 산출물 7번, `docs/PLAN_naver-profit-yardstick-review.md` 산출물 표) | 실측 불필요(이미 실측 쌍 존재, 위 인용) — **단 이 census가 직접 재현한 값이 아니라 계약 문서에 이미 기록된 값을 인용**한 것임을 명시(§5) |

## §2. 액셀·브레이크 대칭 판정

**개소(메커니즘) 수로 세면 액셀약화 8곳(2b·2i·2f·2j·3·4·7상단·5[비집행]) 대 브레이크강화 4곳(2a·2d·2g·7하단)으로
오히려 액셀 쪽이 많아 보인다 — 그러나 지금 살아 있는 후보 볼륨으로 세면 정반대다.**

라이브 실측(2026-08-23, `/api/naver/ad/diagnosis`):
- **브레이크 후보** = bleeding_keywords 600 + shopping_group_bep 65 + shopping_pause_candidates(lever_broken) 0 = **665건**
- **액셀 후보** = starving_winners 135 + shopping_group_growth 85 + resume_candidates 0 + shopping_resume_candidates 0 = **220건**

⇒ **약 3:1로 브레이크 쪽에 압도적으로 기운다.** D-NAO-85(2026-07-23, ROAS +7%·매출 −52%)가 지목한
「액셀·브레이크 비대칭」이 지금도 볼륨 기준으로는 그대로 재현되고 있다 — **다만 방향은 그때와 다르다.**
7월 실측은 "브레이크가 강하고 액셀 압력이 없다"였는데, 지금은 계수가 1.31로 부풀어 있어(§1-3 인용) **브레이크가
오히려 억제되고 있는 상태에서도** 브레이크 후보가 액셀 후보의 3배다 — 계수를 안3대로 재조정해 브레이크에
상한(현행과 유사)·액셀에 하한(더 낮은 값)을 쓰면, **브레이크 볼륨은 거의 안 변하고 액셀 볼륨만 더 줄어** 비대칭이
지금보다 더 벌어질 가능성이 있다. **이것이 계약 §6 금지선 2("자 교정을 단독 배포하지 않는다")가 존재하는
정확한 이유를 이 census가 수치로 확인한 것**이다 — 액셀 재조정(예: target_roas 하향, 확장압력 상수 조정 등,
이 census의 스코프 밖) 없이 안3만 배포하면 D-NAO-85가 그대로 재현된다.

## §3. 안3(구간 자) 적용 명세

| 소비처 | 끝(端) | 근거 |
|---|---|---|
| 2a `bleeding_keywords` | **상한** | 브레이크(정지→bid_down 후보) — 후하게 봐서 안 죽임 |
| 2d `shopping_group_bep` | **상한** | 〃 |
| 2g `shopping_pause_candidates`(lever_broken) | **상한** | 〃 (단, 현재 이 경로 자체가 0건이라 즉시 영향 없음) |
| 2b `starving_winners` | **하한** | 액셀(육성 bid_up) — 보수적으로 |
| 2i `shopping_group_growth` | **하한** | 〃 |
| 2f `resume_candidates` | **하한** | 액셀(재개=지출 재개) |
| 2j `shopping_resume_candidates` | **하한** | 〃 |
| 3 `naver_execution_harness`(bid_up/budget_up 가드) | **하한** | 액셀 가드 — 증액 통과 기준을 보수적으로 |
| 4 `expansion_allocator` | **하한** | 액셀(확장 배분) |
| 7 `bid_simulator` | **[미상 — 설계질문]** | 한 값이 액셀 상한과 브레이크 유발을 동시에 함. 안3의 이분법이 이 파일엔 그대로 안 맞는다 — 다음 세션이 Jino에게 확인할 것(§4) |
| 8 `exploration.py` | **해당없음(현행 유지)** | 이미 raw(=사실상 하한보다 보수적)를 쓰고 있어 안3 적용 대상 아님. 단 factor<1 재확정 시 재판정 필요(§1 8행) |
| 5 `flight_loop` | **해당없음(비집행)** | 관측기 — 배포 시점에 판정 자체가 발동하지 않음. 승격되면 액셀(하한)로 분류될 성격 |
| 6 `proposal_scoreboard` | **해당없음(사후채점)** | 안3은 살아있는 액션 게이트를 위한 규칙 — 학습신호에는 다른 프레임 필요(별도 논의) |
| 9 `profit_scorecard` | **해당없음(표시전용)** | 단, 안3 표면 병기의 1순위 후보(계약 산출물 표 7번) |
| 2c/2e/2h (`expansion_bucket`/`vicious_cycle_flags`/`floor_wait_units`) | **해당없음** | 기록값만·관찰전용 — 실행 경로 없음 |
| 1 `diagnosis.py` | **해당없음** | 전달 허브, 판정 없음 |

## §4. [미상]·[미실측] 목록

- **[미상] bid_simulator.py의 안3 이분류 불가** — §1 행 7·§3 참조. **무엇을 실행하면 확정되는가**: Jino에게
  "economic_ceiling이 상한(액셀 보호)과 하향유발(브레이크) 두 역할을 겸하는데 안3의 어느 끝을 쓸지" 직접
  질문하거나, `simulate_bid`를 방향별로 분리(상한 계산엔 하한 factor, 이미 down으로 확정된 키워드의 재계산엔
  상한 factor를 쓰는 이중 경로)하는 설계안을 다음 세션이 제시.
- **[미상] exploration.py의 raw-rpc 전제 붕괴 조건** — §1 행 8. **무엇을 실행하면 확정되는가**: 안3의 구간
  하한이 1.0 미만으로 확정되는 순간(§8-8, `docs/PLAN_naver-profit-yardstick-review.md`) 이 파일의 "raw가 곧
  보수값" 주석을 재검토하는 태스크를 별도로 연다.
- **[미실측] 2f/2j resume_candidates·shopping_resume_candidates 감도** — 현재 0건인 이유(게이트 미통과)와
  ±10% 감도 둘 다 미확정. **실행하면 확정**: `naver_change_log`에서 `entity_type IN ('keyword','adgroup'),
  action IN ('set_user_lock','external_status_change'), proposal_id IS NOT NULL`인 최신 잠금행을 엔티티별로
  뽑아 `after_value` JSON의 `userLock=True`인 것만 남기고, 정지일 직전 30일 `keyword_window_agg`/
  `_shopping_adgroup_window_agg`를 재계산해 raw roas 분포를 얻는다(위임문에 이미 나온 방법과 동형).
- **[미실측] 3 naver_execution_harness 가드 발동 빈도** — 현재 대기 중인 bid_up/budget_up 제안이 몇 건이고
  그중 몇 건이 이 가드에 걸리는지. **실행하면 확정**: `naver_proposal`(pending 상태) 테이블을
  `proposal_type IN (bid_up, growth_bid_up, budget_up)`으로 필터해 건수 확인 후, 각 건의
  `roas_corrected`/`target_roas`를 재계산.
- **[미실측] 4 expansion_allocator 감도** — `gave_score.compute_gave_score`(감마 파라미터 포함) 재현이
  필요해 단순 SQL로 못 잰다. **실행하면 확정**: 확장모드 활성 캠페인 목록을 `expansion_pressure.judge_campaign_pressure`로
  먼저 좁힌 뒤 `gave_score` 로직을 그대로 호출(읽기전용 함수 호출이라 가능 — DB 쓰기 없음).
- **[미실측] 6 proposal_scoreboard Δconv_amt 부호 분포** — 채점 대상 건의 개선/악화 방향이 계수에 어떻게
  갈리는지. **실행하면 확정**: `naver_change_log`에서 `outcome_profit IS NOT NULL`인 행의 before/after
  conv_amt 재구성.
- **[미실측] 7 bid_simulator 키워드별 ceiling 재계산** — 최고난도(4단 계층 수축). **실행하면 확정**: 대상
  캠페인 하나를 표본으로 `pooled_rpc` 4단 집계를 수동 재현.
- **[부분실측] 2e vicious_cycle_flags 신규 진입** — 현재 4건은 flip 없음을 확인했으나, 3조건(declining ∧
  thinning ∧ below_target)을 만족할 잠재 캠페인 전체를 스윕하지 않았다. **실행하면 확정**: 전 캠페인에
  `_by_campaign` 로직을 SQL로 재현(recent 7일/prior 23일 창).
- **[부분실측] 2i shopping_group_growth 감도** — 계정 기본 target_roas로 근사(87 vs 실측 85, 오차 2건).
  **실행하면 확정**: `campaign_target_resolver.resolve_target_roas`를 캠페인별로 조회해 정확한 밴드 재계산.

## §5. 커버리지 자백

**"전수 조사 완료"라고 쓰지 않는다.** 이 census가 실제로 확정한 것과 확정하지 못한 것을 구분한다.

- **확정(실측)**: 9개 파일 전부의 `correction_factor` 읽기 지점(grep 재확인, 위임문 좌표와 대조해 1건 오차도
  없음 확인) + 각 판정식의 산술적 방향(코드 직독) + 4개 판정함수(2a·2b·2d·2i)의 라이브 감도(±10% flip 건수) +
  proposal_writer.py 연결 여부(어느 보드가 실제 쓰기로 이어지는지, grep 전수) + flight_loop가 비집행임을
  모듈 docstring에서 확인(추정 아님).
- **미확정(구조적 한계)**: ①`bid_simulator`·`expansion_allocator`의 감도는 계층적 계산(4단 베이지안 수축,
  gave_score 감마)이 얽혀 있어 SQL 밴드 재현이 아니라 해당 SA 함수를 직접 호출해야 한다 — 이번 조사는
  방향(산술)만 확정하고 감도는 방법만 적었다. ②`resume_candidates` 계열은 현재 후보가 0건이라 "어떤 조건에서
  통과하는가"의 경계를 실측하지 못했다(대상 자체가 없다). ③`vicious_cycle_flags`·`proposal_scoreboard`는
  다조건/사후비교 구조라 이번 조사에서 전수 스윕을 하지 않고 현재 활성 건만 검사했다.
- **분모의 한계**: 감도표의 "±10%"는 **현재 라이브 board window(15일, 2026-08-09~2026-08-23)** 및 **현재
  라이브 bep_roas/target_roas(계정 기본값)** 기준이다 — 창이 달라지거나(예: 30일) 캠페인별 override가 적용된
  target_roas를 쓰면 flip-zone 건수는 달라진다(2i처럼 근사 오차가 이미 2건 발생). 프로덕션 시각은 조사
  시작(2026-08-23 KST 12:2x)부터 종료(KST 13:4x경)까지 흘렀으므로, 표의 "라이브 600/65/135/85건" 등은 그
  구간 내 어느 순간의 스냅샷이며 이후 자연 갱신(크론)으로 달라질 수 있다.
- **proposal_writer.py는 이 census의 명시 스코프 밖**(9개 파일 목록에 없음)이지만, 각 보드의 「실제 쓰기로
  이어지는가」를 확인하려면 그 파일의 `_BOARD_DIRECTION`·보드별 루프를 읽을 수밖에 없었다 — 코드를 수정하지
  않았고 읽기만 했다.
- **`auto_operator.py`의 board-membership 소비**(예: `shopping_group_bep`를 daily-lane bid_up 게이트 조건으로
  재사용, `auto_operator.py:2108-2137`)는 `correction_factor`를 직접 읽지 않고 이미 계산된 board 결과만
  재사용하므로 이 census의 소비처 9개엔 포함시키지 않았다 — 단, 이 census가 확정한 board 방향(브레이크)을
  그대로 물려받는다는 점만 §5에 기록해 둔다(별도 소비처로 셀지는 다음 세션 판단).
