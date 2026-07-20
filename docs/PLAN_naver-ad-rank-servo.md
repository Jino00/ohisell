# PLAN — 스프린트 IU-R: 순위 서보 (제어 언어를 입찰%에서 순위로, D-NAO-67 원리③)

> 작성 2026-07-20 (Fable 구조→Opus 설계). 개정 2026-07-20 (codex consult 지적 → Fable 실코드 검증 판정 반영).
> D-NAO-67 원리③ / D-NAO-66 ROAS 단일 지배 / D-NAO-68 실행 손 완비.
> 근거: `docs/STRATEGY_naver-ad-v2.md` §1-③ + 트랙 D-NAO-66/67/68·D-NAO-19/20(estimate 직행·±15% 면제 선례)·D-NAO-46②(`NaverKeywordHourly` 시간당 순위 영구 축적)·D-NAO-5(±15% 안전핀).
> Jino 원문: *"1스텝을 15%가 아니고 순위로"*(D-NAO-67) · *"실제 실행하는 손까지 모두 만들어야해"*(D-NAO-68). 구현=Opus·GATE 적대 리뷰 필수(행위 변경, 돈 경로).
> `PLAN_naver-ad-intraday-up.md`(스프린트 IU)를 잇는다 — IU가 순위 제한을 폐지했다면, IU-R은 스텝의 단위를 순위로 전환한다.

---

## §0 방향 고정 (변경 금지)

- **스텝 언어 전환**: 상향 1스텝의 의미를 `현재가×(1+0.15)`(±15%)에서 **"관측 순위 한 단 위로"**로 바꾼다. 순위는 여전히 목표가 아니라 결과 — 서보는 **target ROAS(BEP×공격성) 게이트를 통과할 때만** 작동하고, "한 순위 위"는 그 게이트가 열린 뒤의 *스텝 크기 산정 방식*일 뿐이다(D-NAO-66 지배 게이트 불변).
- **래칫(ratchet) 의미론**(codex 지적 4 반영): 서보는 "특정 목표 순위에 도달해 수렴"하는 기계가 아니라, **ROAS 게이트·예산·가드가 멈출 때까지 한 단씩 계속 상향**하는 래칫이다. 매 실행 목표=현재 관측순위 한 단 위로 재계산. "정지(rest)"는 목표 도달이 아니라 **ROAS 게이트 미통과 or 예산 소진 or 순위 무반응 or 가드 차단**이 발생한 지점이다("직전 목표까지 수렴" 언어 금지).
- **그레인별 두 기계, 하나의 이음매**:
  - **파워링크(WEB_SITE=키워드 grain)**: estimate API로 목표 순위 입찰 직행 — 목표 = `ceil(현재 관측순위) − 1`(codex 지적 2: floor 아님, 현재 4.9의 한 단 위=4위). 경제성 상한 캡. ±15% 면제(D-NAO-20 선례).
  - **쇼핑검색(SHOPPING=광고그룹 grain)**: 순위별 필요입찰 API 부재 → **폐루프 서보**: 스텝 → 다음 시간 실측 avg_rank 확인 → 한 단씩 래칫(ROAS 게이트·쿨다운 리듬 안). **BRAND_SEARCH는 초기 스코프 제외**(codex 지적 6: 쇼검과 반응이 같다는 근거 없음 — 실측 후 확대). **제외 유닛(BRAND_SEARCH) fallback = 기존 ±15% `_clamp_step` UP 유지**(codex P1-3: IU가 연 UP을 회귀시키지 않는다 — 서보만 미적용, UP 자체는 종전대로).
- **최상단 상향 방지**(codex P1-4): `weighted_rank ≤ 1 + _SERVO_DEADBAND`이면 쇼검·파워링크 공통 **converged hold**(이미 1위권 — 더 올릴 순위 없음). 파워링크는 목표가 1 미만이 되는 경우 estimate position 1 요청 자체를 하지 않고 hold.
- **불변 가드(폐지 절대 금지 — 서보가 얹히는 안전 지반)**: ROAS 단일 지배·BEP 하한·스톱로스·일예산 불가침·킬스위치(3중 방어)·쿨다운 2h(`_COOLDOWN_HOURS`)·일일 변경 상한 3(`_MAX_DAILY_CHANGES`)·장중-단독 UP 일 1스텝 캡(`_INTRADAY_ONLY_UP_DAILY_CAP`)·정산 거부권(`_settlement_roas_status`=below veto)·10원 단위·70~100,000·소재입찰(B) 카나리 비침범·CPC 급등/loss 고삐 DOWN 우선.
- **±15% 면제의 정확한 경계**: 면제되는 것은 **`_MAX_CHANGE_PCT` 변경폭 상한 하나뿐**. BEP 하한·스톱로스·일예산·쿨다운·일일상한은 전량 존치. 대체 상한 = 경제성 상한 + 서보 절대 스텝 캡 + 예산 여력 사전체크. "±15% 면제 = 무제한"이 아니다.
- **스코프**: 시간당 레인(`run_hourly_lane`) **UP 경로**만. DOWN(CPC 급등·loss 고삐)은 기존 `_clamp_step` ±15% 유지. 소재입찰(ad) 자동발사·예산 변경(L2)·검색어 레이어(SS)는 스코프 밖.
- **실집행까지가 스코프(D-NAO-68)**: 서보는 제안/dry-run에서 멈추지 않는다 — 폐루프는 실제 입찰 변경 없이 다음 시간 rank 피드백을 측정할 수 없다. 각 Phase의 완료 = 스텝 산정 → 승인 → `naver_execution_harness.execute(dry_run=False)` 실쓰기 + **다운스트림 정합**까지(§2 시퀀싱).

---

## §0.5 실행 손 현황표 (D-NAO-68) — 손은 이미 있다

IU-R이 쓰는 입찰 손은 전부 구현돼 있다. 서보는 새 writer가 필요 없고 기존 `update_bid` 초크포인트를 재사용한다(신규 배선은 "무엇을 얼마로"의 산정부).

| 레버 | 실쓰기 손(`naver_sa_writer`) | 실행 하니스 | 상태 | IU-R |
|---|---|---|---|---|
| 키워드 입찰 | `update_keyword_bid` | `_execute_update_bid` | ✅ | R2 실집행 |
| 광고그룹 입찰 | `update_adgroup_bid` | `_execute_update_bid` | ✅ | R1 실집행 |
| 소재 입찰 | `update_ad_bid` | `_execute_update_bid`(ad) | ✅(Confirm-only) | 카나리 2단계까지 서보 UP 제외 |
| 정지·재개 | `set_keyword_lock` | `_execute_set_user_lock` | ✅ | 스코프 밖 |
| 예산 | `update_campaign_budget` | `_execute_update_budget` | ✅(영구 Confirm) | 스코프 밖(L2) |
| 제외키워드 | `add_restricted_keywords` | `_execute_add_negative_keyword` | ✅ | 스코프 밖(SS) |
| 키워드 등록·그룹 생성·소재 생성 | ❌ 없음 | ❌ 없음 | 미구현 | **SS·L3·소재 스코프** — D-NAO-68에 따라 각 스프린트가 실쓰기 손+하니스 배선까지 완주(제안 전용 금지) |

- **IU-R 결론**: R1/R2는 없는 손이 없다 — 서보 스텝은 기존 `update_bid`로 실집행(`run_hourly_lane`이 승인 후 `execute(dry_run=False)` 인라인 호출·D-NAO-52 라이브 실증). IU-R이 여는 것은 손이 아니라 스텝 산정 방식(±15%→순위).
- 3중 방벽(OPEN_ACTIONS+`_WRITE_EXECUTORS`+dry_run=False)·D-NAO-16 개방 순서·생성류 영구 Confirm(D-NAO-5)은 불변.

## §0.6 자동 실행 vs Confirm 경계 (원칙25)

- **그룹/키워드 입찰 = 자동 실집행**(되돌릴 수 있음 — 다음 스텝/DOWN 고삐로 원위치). 서보 폐루프 성립의 유일 조건(사람 왕복이 끼면 다음 시간 피드백 측정 불가).
- **Confirm 유지**: 소재입찰(ad, 자동발사 0)·생성류(영구 Confirm).
- **사고 시(원칙25/23)**: 게이트를 새로 세우지 않는다 — DOWN 고삐+쿨다운+일일상한이 자연 회수, 필요 시 서보 캡·데드밴드 조정. 자동 전제 3종: 사후 가시성=change_log·diary / 정정 경로=DOWN 고삐 / 근거 보존=rationale에 목표순위·서보 근거.

---

## §1 현재 구조의 문제 (실코드 기준)

`auto_operator.py` · `bid_simulator.py` · `guardrail_gate.py` · `naver_execution_harness.py`:

1. **스텝이 통짜 ±15%**: `_clamp_step(current_bid, direction)`은 UP=`current×1.15`·DOWN=`current×0.85` 고정. 순위 개념 없음. `run_hourly_lane` UP도 이 함수만 씀(1320행). 한 순위 올리는 입찰폭이 15%보다 크면 여러 쿨다운(2h×N)에 걸려 장중에 순위가 못 붙음.
2. **±15% 이중 강제**: `_clamp_step`(생성)+`guardrail_gate._check_bid`(실행 직전, `change_pct > _MAX_CHANGE_PCT`). 현재 면제 = `_EXEMPT_FROM_CHANGE_PCT = {"growth_bid_up"}` 하나. 운영 키워드 일반 `bid_up`은 estimate 순위 입찰이라도 ±15%로 잘림(D-NAO-20-③).
3. **★UP 타입 판별이 다파일 하드코딩(치명적 — R0로 해결)**: `("bid_up","growth_bid_up")` 문자열이 여러 곳에 독립 산재 —
   - `guardrail_gate._BID_UP_TYPES = frozenset({"bid_up","growth_bid_up"})`(BEP·스톱로스·일예산 up-only 검사 트리거) / `_EXEMPT_FROM_CHANGE_PCT`(±15% 면제).
   - `naver_execution_harness._build_guardrail_context` 391행 `proposal.proposal_type in ("bid_up","growth_bid_up")`(BEP/스톱로스/일예산 컨텍스트 채움) · 692행 실행 직전 완전성 게이트.
   - `auto_operator._executed_bid_ups_today` 826행 `proposal_type.in_(("bid_up","growth_bid_up"))`(일 1스텝 캡 카운터).
   - `diary_outcome._ACTION_TO_DIRECTION = {"bid_up":"up", ...}`(소급채점 방향).
   - ad 카나리 방향 게이트(`_AD_BID_CANARY_DIRECTIONS`) 및 하니스 최종 경계 재검증.
   → 새 UP 타입을 **일부 set에만** 넣으면 BEP/스톱로스/일예산 컨텍스트 누락(fail-open 우회) or 가드 미인식. 반드시 단일 소스화 선행(R0).
4. **목표 순위 고정 2위**: 파워링크 estimate 직행은 *일 레인*에만 있고 `_TARGET_RANK_POSITION=2` 고정. "현재−1단" 동적 목표 아님. 시간당 레인엔 estimate 호출 자체가 없음(R2 신규 배선 필요).
5. **✅정정(codex 승 — 사실 오류였음)**: 유닛별 시간당 실측 순위는 **영구 축적되고 있다**. `NaverKeywordHourly`(models.py 1620·`keyword_hourly_sweep.py`)가 WEB_SITE=키워드·SHOPPING/BRAND_SEARCH=애드그룹 grain의 hh24 avg_rank를 **일 1회 D-1 스윕·365일 롤링**으로 적립(D-NAO-46②). 초판 계획서의 "어디에도 영속 안 됨" 주장은 오류. →
   - **R3는 신규 테이블 없이 `change_log × NaverKeywordHourly` 조인 집계로 시작**(마이그레이션 0). 충분통계 전용 테이블은 조인 성능/필요가 실증된 뒤 후속으로만 강등.
   - R3 원료 시계 = **일 단위**(NaverKeywordHourly는 D-1 스윕). 반응 곡선 학습은 08:10 크론(`learning_loops`)에 적합하고, "첫 스텝 정교화"는 **익일 반영**이다(시간당 개선처럼 읽히는 문구 금지).
6. **서보 입력 순위는 이미 계산돼 있다**: `_judge_hourly`가 `_weighted_recent(curve, now.hour)`로 imp-가중 3시간 창 `weighted_rank`를 산출 — 서보 입력으로 흘려보냄(재조회 불요).

---

## §2 페이즈

> 원칙: 레지스트리(R0) 선행 → 어려운 핵심(쇼검 서보 R1) → 파워링크(R2) → 반응곡선(R3). **R4 폐지** — 다운스트림 정합을 각 Phase 완료 조건에 흡수(다운스트림 깨진 채 라이브 금지). 각 Phase 완료 = 실쓰기 라이브 완주(D-NAO-68).

### R0 (Opus) — UP 타입 레지스트리 단일 소스화 [선행·행위 불변 리팩터]

- **신규 독립 상수 모듈** `backend/app/services/naver_ad/bid_step_types.py`(codex P2: **guardrail_gate 승격안 삭제** — 순환 import 회피. auto_operator·harness·diary_outcome·guardrail가 모두 이 모듈을 단방향 import하도록 최말단 상수 전용 모듈로 고정). UP 타입 판별·방향·±15% 면제·rank-step 여부를 한 곳에 중앙화:
  - `BID_UP_TYPES` / `BID_DOWN_TYPES` / `CHANGE_PCT_EXEMPT_TYPES` / `RANK_STEP_TYPES`(신규 서보/랭크 타입) + 헬퍼 `is_bid_up(pt)` / `direction_of(pt)`.
- §1-3의 산재 참조를 **전부 이 소스 import로 교체**: guardrail_gate(`_BID_UP_TYPES`/`_EXEMPT_FROM_CHANGE_PCT`), 하니스 391·692, auto_operator 826, diary_outcome `_ACTION_TO_DIRECTION`, ad 카나리 방향 게이트.
- **행위 불변**: R0는 신규 타입을 **아직 도입하지 않는다** — 기존 값만 단일 소스로 이관. **차등 테스트로 "동일 입력에 대한 guardrail 판정·`_ACTION_BY_PROPOSAL_TYPE` action 매핑·direction·일일 카운터 결과가 리팩터 전/후 동일"함을 확인**(codex P2 — "바이트 동일"의 구체화: 판정 산출물 동일).
- **그 위에서 신규 타입 vs context-flag 재결정 — 판정: 신규 proposal_type 채택**(`bid_up_servo`·`bid_up_rank`). 근거:
  - 필요한 구별 = ±15% 면제 + rank-step 의미(rationale·관측쌍). 그 외 UP 의미(BEP·스톱로스·예산·쿨다운·방향)는 `bid_up`과 동일.
  - **context-flag 기각**: 면제는 *per-proposal*(이 bid_up이 서보 산출인가)이라 가드가 실행 시점에 알아야 하는데, 하니스는 공유 초크포인트라 값을 DB의 proposal에서 다시 읽는다. NaverProposal에 자유 컬럼이 없어 flag를 담으려면 (a)마이그레이션 or (b)approval_source 오염(승인자≠스텝의미, 스멜). 게다가 flag는 `_EXEMPT_FROM_CHANGE_PCT` 단일소스 규율을 깨는 **두 번째 면제 경로**를 만든다.
  - **신규 타입 채택 근거**: proposal_type은 기존 컬럼이라 마이그레이션 0. R0 레지스트리가 가드측 위험(치명적)을 무력화 — 신규 타입을 `BID_UP_TYPES`·`CHANGE_PCT_EXEMPT_TYPES`·`RANK_STEP_TYPES`에 한 번 등록하면 모든 가드가 인식. 남는 위험 = 가드 밖 소비자(scoreboard/diary/retro/dedup/expiry/dashboard)인데, 이는 R1/R2 완료 조건에 명시 흡수(§시퀀싱).

### R1 (Opus) — 쇼핑검색 폐루프 순위 서보 [핵심·마이그레이션 없음]

**신규 SA `rank_servo`(순수 함수, `rank_servo.py`)**
- 시그니처(원칙18-8): `decide_servo_step(weighted_rank, current_bid, *, imp_sum, economic_ceiling, response_prior=None) -> dict`.
  - 입력은 **구조화 값만**(codex 지적 1: 문자열 rationale 의존 금지). ROAS 게이트 통과 여부는 harness가 `_judge_hourly` 확장 verdict로 판정해 **서보 호출 자체를 게이팅**(서보는 UP이 이미 승인된 뒤 스텝 크기만 산정). `economic_ceiling`은 harness가 아래 산식으로 precompute해 주입.
- 출력: `{"target_bid": int|None, "target_rank": int, "step_reason": str, "unresponsive": bool}`.
- 로직:
  - **목표 순위** = `max(1, ceil(weighted_rank) − 1)`(codex 지적 2). **최상단 가드**(codex P1-4): `weighted_rank ≤ 1 + _SERVO_DEADBAND`이면 converged hold(`target_bid=None`) — 1위권은 더 올릴 순위 없음.
  - **fail-closed hold**(codex 지적 5): `weighted_rank is None`(rank 전부 null) or `imp_sum < _MIN_HOURLY_SAMPLE_IMP(30)` or `economic_ceiling ≤ 0`(원료 부재/심층 콜드)이면 `target_bid=None`(근거 없음).
  - **스텝 크기**: `response_prior`(R3 산출·없으면 None) 있으면 "한 단 위"에 필요한 입찰 증분을 프라이어 기울기로 근접. 없으면(콜드스타트) 보수 기본 스텝 `current×_SERVO_DEFAULT_STEP_PCT`(추천 0.15에서 시작, 폐루프가 반복 보정, **실측 필요**).
  - **서보 절대 스텝 캡**: `target_bid ≤ current×_SERVO_MAX_STEP_PCT`(추천 0.50, **실측/튜닝**). ±15% 면제해도 노이즈 1건 폭주 차단.
  - **반올림 순서 못박음**(codex 엣지): ① 원 target_bid 산정 → ② 서보 캡·**경제성 상한(economic_ceiling)**·`_MAX_BID` 클램프 → ③ **UP=10원 내림** → ④ `current`보다 크고 ≥70원이어야 유효(아니면 None).

**쇼검 경제성 상한 산식(codex P1-1 — harness precompute, `bid_simulator` 재사용)**
- adgroup grain ceiling = **`bid_simulator.affordable_ceiling(pooled_RPC × correction_factor, target_roas)`**(D-NAO-19 동형). `pooled_RPC = bid_simulator.pooled_rpc(keyword_row, group_agg, campaign_agg, account_agg)` — 여기서 `keyword_row`={clk, conv_amt}는 정착창 adgroup 실적(`_settlement_agg`), 상위 prior는 `proposal_pipeline._precompute_aggregates`의 {campaign, account}. **SHOPPING은 그룹 하위 키워드 grain 부재 → `group_agg = campaign_agg`로 근사**(compute_bid_sims의 기존 shopping 처리와 동일). 즉 이미 있는 계층 베이지안 수축(`hierarchical_pooling`/`pooled_rpc`)을 그대로 adgroup에 적용.
- **원료 부재/`clk=0` 심층 콜드 → `economic_ceiling=0` → 서보 fail-closed hold**(`affordable_ceiling`이 rpc≤0에서 0 반환·70원 미만도 0 — 입찰 근거 없음). 이 caps가 ±15% 면제를 대체하는 상한(순위가 아무리 낮아도 수익 여력 밖은 안 산다).
- **서보 상태 = 무영속 재구성**(래칫): 목표·직전 스텝·정지를 저장하지 않고 매 실행 재계산 — 목표=현재순위−1, 오늘 스텝 수=`_executed_bid_ups_today` 패턴(신규 타입 포함). `probe_learning_loop` 무영속 관례와 동형. **DB 스키마 변경 없음.**
- **수렴 대신 데드밴드 관망**(codex 지적 3·4): 목표가 매 실행 현재순위−1로 재계산되므로 순위가 목표에 근접(`|weighted_rank − target_rank| ≤ _SERVO_DEADBAND`, 추천 0.3 **실측**)하면 그 실행은 스텝 안 냄(진동 차단). 순위가 더 좋아지면 다음 실행이 다음 단으로 래칫(ROAS 게이트가 여는 한).

**harness 확장 `run_hourly_lane`(이음매 신설)**
- `_judge_hourly`를 **구조화 verdict 반환으로 확장**(codex 지적 1): `{"direction","reason", "settle_status","intraday_ok","target_roas","est_roas","budget_ok","weighted_rank","imp_sum"}`. 서보/estimate 라우팅이 문자열 파싱 없이 소비.
- 종전 `step_bid = _clamp_step(step_base, direction)`(1320행)를 **그레인·방향 라우팅**으로 교체:
  - UP ∧ adgroup(SHOPPING) → `rank_servo.decide_servo_step(...)`(harness가 weighted_rank·imp_sum·current_bid·economic_ceiling·최근3h clk pace·response_prior precompute 주입). `target_bid None`이면 hold(관찰).
  - UP ∧ adgroup(BRAND_SEARCH) → **기존 `_clamp_step`(±15%) UP 유지**(codex P1-3 — 서보 미적용이지 UP 회귀 아님).
  - UP ∧ keyword(WEB_SITE) → R2에서 estimate 직행 절체. R1 단계 keyword UP은 종전 `_clamp_step` 유지(회귀 0).
  - DOWN → 기존 `_clamp_step`(±15%).
- **쿨다운/일일캡 prefilter 공용 helper 추출**(codex 엣지): 서보 진입 전 사전필터를 harness에서 재구현하지 말고 guardrail 로직과 공용 helper로(중복 금지).
- SA간 직접 호출 금지(원칙18-6): `rank_servo`는 `intraday_roas`·`_judge_hourly`를 모름 — harness가 출력을 optional 입력으로 전달.

**±15% 면제 배선(R0 레지스트리 위)**
- `bid_up_servo`(쇼검) 도입 — R0 레지스트리 `BID_UP_TYPES`·`CHANGE_PCT_EXEMPT_TYPES`·`RANK_STEP_TYPES`에 등록. `_ACTION_BY_PROPOSAL_TYPE["bid_up_servo"]="update_bid"`. 면제는 `_MAX_CHANGE_PCT`만 — BEP·스톱로스·예산·쿨다운·일일상한은 `BID_UP_TYPES` 경로로 전량 존치.
- **스톱로스 완화 방지 못박음**(codex 엣지): 기존 `stop_loss = target_bid × STOP_LOSS_CLICK_MULTIPLE`은 큰 스텝에서 target_bid가 커져 실질 완화된다. **rank-step 타입(`RANK_STEP_TYPES`)의 스톱로스는 스텝 전 `current_bid` 기준으로 산정**(더 보수적) — guardrail_gate가 rank-step에서 base를 current로 스위치.
- **예산 여력 사전체크 신설(codex P1-2 — 신규 API 없음, hh24 곡선 재사용)**: 큰 스텝은 기존 `_budget_headroom_ok`의 `cost_today ≥ daily_budget` 사후 가드만으론 부족 → "**잔여 예산 대비 예상 추가 지출**"을 서보 스텝에 사전 체크. 원료 = **이미 조회한 hh24 곡선의 최근 3시간 실측 `clk` pace**(신규 API 없음). 근사: `잔여시간 예상 클릭(최근 3h clk pace × 잔여 활동시간) × target_bid ≤ (daily_budget − cost_today) × 안전계수`. 곡선 부재/pace 산출 불가면 **§0 표본 게이트(`_MIN_HOURLY_SAMPLE_IMP`)가 이미 hold**하므로 자연 fail-closed(이중 방어) — daily_budget=0(uncapped)이면 통과. **★R1이 전량 hold되지 않음**을 차등 테스트로 못박음(정상 pace·예산 여유 유닛은 서보 스텝 생성).

**harness 라우팅 precompute(원칙18-6 허브)**: `run_hourly_lane`이 서보 진입 유닛에 대해 `economic_ceiling`(pooled_rpc→affordable_ceiling, 위 산식)·최근 3h clk pace·weighted_rank·imp_sum·response_prior를 precompute해 `rank_servo`에 optional 입력으로 주입. agg({campaign,account})는 캠페인 순회 밖에서 1회 precompute(N+1 방지).

**실집행 완주(D-NAO-68)** + **다운스트림 정합(R1 완료 조건, R4 흡수)**: `bid_up_servo`를 scoreboard 채점 버킷·diary actor/rationale·retro 매핑·`_executed_bid_ups_today`·real_write_blocker/delegation_gate·제안 만료/dedup·대시보드 시간정규화까지 인식하도록 정합. **★`_DAILY_LANE_PROPOSAL_TYPES`에는 넣지 않는다**(codex P2 — 새 타입은 시간당 레인 inline 전용: 즉시 승인→즉시 execute이므로 일 레인의 pending 재처리·중복 승인·stale sweep 경로에 태우면 안 됨). **깨진 채 라이브 금지.**

**소재 카나리 상호작용(맥세이프=쇼핑)**: `_AD_BID_CANARY_DIRECTIONS={"bid_down"}`이라 ad-라우팅 유닛 UP은 이미 hold("ad UP은 카나리 2단계"). 서보 UP도 동일 게이트에 걸려 그룹입찰 유닛만 서보(카나리 2단계까지). 누출 0 실증(§3).

### R2 (Opus) — 파워링크 estimate 직행 (동적 목표, `bid_simulator` 재사용)

- 시간당 레인 UP·keyword 경로에 `estimate_average_position_bid("MOBILE", [{key:kw_id, position:target}])` 신규 배선. 목표 = `clamp(ceil(weighted_rank)−1, 1, 4)`(estimate position 1~4 제약).
- `bid_simulator.simulate_bid`의 `min(economic_ceiling, rank_bid)` 재사용. **원료 precompute 비용을 R2 스코프에 명시**(codex 시퀀싱): simulate_bid는 keyword/group/campaign/account agg + correction_factor가 필요한데 `run_hourly_lane`엔 없음 → `proposal_pipeline._precompute_aggregates` 패턴을 시간당 레인용으로 도입(스텝 대상 키워드에 한정, N+1 방지).
- `bid_up_rank`(파워링크) 도입 — R0 레지스트리 등록·±15% 면제. 대체 가드 = 경제성 상한(estimate>상한이면 상한까지만, D-NAO-19).
- **estimate 실패·이상 → fail-closed hold**(codex 엣지, 못박음): estimate 호출 실패·`rank_bid` 누락·0·비10원·범위밖·`≤ current`면 그 유닛은 **hold**(일 레인은 economic-ceiling으로 계속 진행하지만, 시간당 서보는 순위 근거가 없으면 스텝 금지 — 명시적 차이).
- **TOCTOU 방어 = fail-closed 중단으로 고정**(codex P1-5): estimate 산정 시점 bid ≠ 실행 시점 bid일 수 있음 → `_execute_update_bid`의 라이브 재조회에서 bid 변동 감지 시 **중단(hold)만**. **하니스에서 재산정 금지** — write chokepoint에 산정/네트워크(estimate 재호출)를 얹지 않는다(초크포인트 순수성 유지). 재산정은 다음 시간당 레인 몫(다음 :20에 fresh current로 재진입).
- **estimate 예산**(§난제 4): 실제 스텝 유닛에만(ROAS 게이트 ∧ 쿨다운 아님 ∧ 예산 여력 ∧ 데드밴드 밖) + 회당 캡 + 런 캐시. 호출량 canary **실측**.
- **실집행 완주 + 다운스트림 정합**(R1과 동일 조건 흡수): `update_keyword_bid` 실쓰기 + `bid_up_rank` 다운스트림 정합.

### R3 (Opus/Sonnet) — 반응 곡선 학습 `bid_rank_curve` [조인 기반·마이그레이션 0]

- **신규 SA `bid_rank_curve`(순수 함수)** — **신규 테이블 없이** `NaverChangeLog`(update_bid before/after+changed_at) × `NaverKeywordHourly`(다음 시간대 avg_rank) **조인 집계**로 유닛별 (입찰변경→순위변화) 관측쌍 산출·곡선(기울기) 적합. 충분통계 전용 테이블은 조인 성능/필요 실증 후로 강등(마이그레이션 0으로 시작).
- **조인 버킷 결정론 규칙(codex P2)**: 변경 `changed_at`=시각 h대(레인 :20) 기준 — **`rank_before` = 버킷 `h−1`**(변경 전 완결 버킷·부분 h대 배제), **`rank_after` = 버킷 `h+1`**(변경 후 첫 완결 버킷 — h대는 :20 변경이 섞인 부분버킷이라 제외). 쿨다운 2h와 정합(다음 스텝은 최소 h+2라 h+1은 이 스텝의 순수 결과). 두 버킷 중 하나라도 avg_rank NULL/부재면 그 쌍 제외.
- **인과 오염 방지**(codex 엣지): 같은 시간창(h−1~h+1)에 **다른 변경(외부/MOP/소재/예산소진) 감지 시 그 관측쌍 제외**(`external_*` change_log·소진 완료 시각 대조). **실패 writer 호출(실반영 불확실)은 제외가 아니라 불확실 플래그**로 표시(관측쌍 자체는 살리되 신뢰 하향).
- 적립·학습 주체 = `learning_loops.run_all`에 스테이지 추가(08:10, D-1 기준). 결과(유닛별 기울기)를 서보 `response_prior`로 전달(harness read) — **첫 스텝 정교화는 익일 반영**(NaverKeywordHourly D-1 스윕, §1-5 정정).
- 콜드스타트: 관측쌍 < 최소치면 `response_prior=None` → 서보 R1 보수 기본 스텝 폴백(무근거 큰 점프 금지).
- **response_prior 기울기 단위 정규화(codex P2)**: **"원 / rank 개선 1.0"**(순위 1단 개선에 필요한 입찰 증분, 원 단위) — 양수 규약(rank는 작을수록 좋으므로 부호 혼선 방지: 개선폭=`rank_before − rank_after`, 기울기=`Δbid / Δrank개선`, Δrank개선>0인 쌍만 기울기 적합에 사용). 서보는 `한 단 위 필요 증분 ≈ 기울기 × 1.0`.
- **저장처(codex P2 scope 확장 규칙)**: 곡선 기울기(유닛별 스칼라)는 `NaverLearningState`에 저장 — **유닛 grain은 `scope="entity", scope_key="adgroup:<id>"` / `"keyword:<id>"`** 규약 신설(기존 campaign/keyword_type/global scope 옆에 추가), `metric="bid_rank_slope"`, `current_value`=기울기, `sample_n`=관측쌍 수, `confidence`=적합도. **모델 docstring(scope 예시 목록)·`_upsert_learning_state` 테스트를 동반 갱신**(새 scope 값 추가를 명문화). 단일 Numeric로 충분(관측쌍 원료는 조인으로 매일 재산출 → 충분통계 영속 불요). ★초판의 "NaverLearningState에 못 담음→신규 테이블" 판단 철회.
- 피드백 루프(원칙18-9): 서보 실스텝 → `NaverKeywordHourly` 다음 시간 순위 적립 → 조인 집계로 곡선 갱신 → 익일 서보 첫 스텝 정교화.

### 시퀀싱(codex 수용)

- **R4 폐지** → 다운스트림 정합(scoreboard/diary/retro/일일카운터/dedup/expiry/대시보드/real_write_blocker/delegation_gate)을 R1·R2 각 완료 조건에 흡수. 다운스트림 깨진 채 라이브 금지.
- R2 simulate_bid 재사용 원료(agg + correction_factor) precompute 비용을 R2 스코프에 명시(위).

---

## §난제별 설계 (착수 전 못 박음)

1. **avg_rank 노이즈·오실레이션**: imp-가중 3시간 창(`_weighted_recent`) + 표본 게이트 `_MIN_HOURLY_SAMPLE_IMP=30`(미만 fail-closed hold) + 데드밴드 관망 + 쿨다운 2h(관측 주기) + 서보 절대 스텝 캡. rank 전부 null도 fail-closed(codex 지적 5).
2. **서보 상태 저장**: **저장 안 함 — 무영속 래칫 재구성**(현재 hh24 순위 + change_log). R1 스키마 변경 0. R3 반응곡선 원료도 기존 `NaverKeywordHourly` 조인이라 **마이그레이션 0**(§1-5 정정).
3. **쿨다운·일 1스텝 캡 vs 래칫**: 쿨다운 2h = 충돌이 아니라 서보의 관측 시계. 래칫은 이 리듬 안. 일 1스텝 캡(`_INTRADAY_ONLY_UP_DAILY_CAP`)은 정산 미확인(unknown) 유닛에만 — 검증된 이익("ok") 유닛은 일일상한 3 안에서 반복 상향(=하루 최대 3단). "ROAS 게이트 통과하는 한 반복"의 게이트 = 검증된 target ROAS. **캡·쿨다운 상향 금지**(기존 상수 상속).
4. **estimate API 예산(시간당)**: 실제 스텝 유닛에만 + 회당 캡 + 런 캐시. 호출량·비용 **실측 필요**.
5. **콜드스타트 첫 스텝**: 파워링크=estimate가 곧 모델. 쇼검=반응곡선 없으면 보수 기본 스텝으로 시작해 폐루프 반복 보정. 무근거 큰 점프 금지.
6. **순위 미이동 시 판정**(codex 검증 추가): 스텝 후 다음 시간 순위가 안 오르면 — ①ROAS 게이트·예산 유지 시 다음 쿨다운에 다음 스텝(래칫 계속, 일일상한 3 내) ②**N스텝(추천 3, 실측) 연속 무반응이면 `unresponsive` hold**(경쟁 천장/수요 문제 — 위치 아님, 과입찰 방지) ③무반응 관측쌍은 **곡선 학습에 유지**(평탄 기울기 = 유효 신호, 제외 아님).
   - **재구성 쿼리(codex P2)**: 무영속이므로 change_log에서 매 실행 재계산 — 해당 유닛의 **최근 연속 rank-step change_log(action=update_bid ∧ dry_run=False ∧ after_value 존재 ∧ proposal_type ∈ RANK_STEP_TYPES) N개**를 `changed_at` 역순으로 뽑고, 각각의 §R3 조인 버킷 규칙으로 개선폭(`rank_before − rank_after`)을 계산. **연속 N개 모두 개선폭 ≤ 0**이면 `unresponsive`. **실패/불확실 플래그/인과 오염 행은 연속성 카운트에서 제외**(개선폭 판정 불가 = 연속을 끊지도 세지도 않음, 건너뜀).

---

## §3 검증 (원칙22 — 라이브 합격 시나리오, 착수 전 못 박음)

1. **R0**: 리팩터 전/후 **동일 입력에 대한 guardrail 판정·action 매핑·direction·일일 카운터 결과가 동일함을 차등 테스트로 확인**(guardrail/harness 391·692/auto_operator 826/diary_outcome/ad 카나리 전 참조처). pytest 전체 회귀 0.
2. **단위/차등(R1~R3)**:
   - 쇼검 서보 UP: 관측 4.9위·ROAS 게이트 통과·economic_ceiling>0·예산 pace 여유 → 목표 4위(ceil−1)·서보 스텝(±15% 초과·캡 내). rank 전부 null/imp<30/`economic_ceiling≤0`(clk=0 콜드) → fail-closed hold.
   - **경제성 상한**: pooled_rpc(정착창 adgroup clk/conv_amt + campaign/account prior)→affordable_ceiling이 rank 스텝을 캡(순위 근거로도 상한 초과 불가).
   - **예산 pace(전량 hold 아님 실증)**: 정상 3h clk pace·예산 여유 유닛 → 서보 스텝 생성(codex P1-2). pace로 잔여예산 초과 예상 → hold. daily_budget=0(uncapped) → 통과.
   - **최상단**: 관측 1.2위(≤1+deadband) → converged hold(파워링크는 position 1 요청 안 함).
   - 데드밴드: 목표 근처 노이즈 진동 → 재스텝 없음. 래칫: 순위 개선되면 다음 실행 다음 단.
   - ±15% 면제 경계: `bid_up_servo`/`bid_up_rank`가 change_pct 초과 통과, **동시에** BEP 미달·스톱로스(current 기준)·예산 여력·쿨다운·일일상한 3은 여전히 차단.
   - 파워링크: 목표=ceil(현재)−1(고정2 아님)·`min(상한, rank_bid)`·estimate 실패/이상 → fail-closed hold·TOCTOU=**중단 고정**(재산정 안 함).
   - R3: 관측쌍 부족→prior None→폴백. 인과 오염 시간창→제외. 실패 writer→불확실 플래그. 무반응→평탄 기울기 유지.
   - 정산 거부권/일 1스텝 캡/킬스위치/쿨다운이 서보 경로에서 생존.
3. **GATE(Opus 적대 리뷰)** — 공격 각도: ①노이즈 1건 폭주 점프(캡·데드밴드·표본 게이트) ②±15% 면제가 BEP/스톱로스/예산까지 조용히 면제 안 하는지 ③스톱로스 current-기준 실작동(target_bid 완화 방지) ④예산 여력 사전체크로 잔여예산 초과 지출 차단 ⑤estimate 1~4 clamp·fail-closed·TOCTOU ⑥ad 카나리 UP 누출 0 ⑦무영속 래칫 목표 오판(현재순위 stale) ⑧R0 레지스트리 누락 참조처(새 타입이 어느 set에도 안 들어간 소비자) ⑨R3 관측쌍 귀속 오염 ⑩real_write_blocker/delegation_gate/dedup/expiry/대시보드가 새 타입 미인식 여부.
4. **실쓰기 라이브 합격(원칙22·D-NAO-68 — dry-run 통과 ≠ 합격, Phase별)**:
   - **R1**: 배포 후 다음 :20 레인 완주 + 그룹입찰 쇼검 유닛 서보 UP→승인→`execute(dry_run=False)` **실 `update_adgroup_bid` 성공**(change_log dry_run=0·after_value bidAmt·라이브 API 응답) → **다음 시간 `NaverKeywordHourly`/hh24 avg_rank 목표 방향 이동** 실측(bid→rank 인과 지연이 쿨다운 2h 안에 관측되는지 = 서보 핵심 가정, 실측 전 단정 금지) → 다음 :20 데드밴드 관망/래칫. **폐루프 한 바퀴 라이브 완주** 필요. 다운스트림(scoreboard/diary/retro) 정상 인식 확인.
   - **R2**: keyword UP estimate 목표순위 입찰→`update_keyword_bid` 실쓰기 + **±15% 초과 스텝 1회 반영**(after_value 변경폭>15%·guardrail 면제 실작동) + 다음 시간 순위 도달.
   - **R3**: 서보 실스텝이 조인 집계로 관측쌍화·다음 서보 첫 스텝 프라이어 근접(익일) 라이브 관측.
   - **★±15% 초과 실쓰기 사전 봉투 못박음**(codex 엣지): 대상 유닛 선정 기준(그룹입찰 쇼검·ROAS ok·데드밴드 밖)·최대 금액(`_SERVO_MAX_STEP_PCT`)·잔여예산 하한·실패 시 롤백 절차를 배포 전 명문화.
   - 공통: 격리/dry-run 통과≠라이브 합격(원칙22) — change_log after_value·라이브 순위로 확인, stale 로그 금지. ad 카나리 UP 누출 0 실측.

---

## §4 체크리스트

- [x] R0: `bid_step_types` 독립 상수 모듈 + 전 참조처 교체(전수 감사로 계획 외 harness 438·카나리 소비처 6곳 추가 발견·이관, 카나리 상수 `_AD_BID_CANARY_PROPOSAL_TYPES` rename) + 차등 테스트 31개 + **2465 passed·회귀 0**
- [x] R0 GATE PASS(codex review P1 0·P2 1 — `_ACTION_BY_PROPOSAL_TYPE` 리터럴 잔존 → 레지스트리 파생으로 수용 반영 + 누락 감지 테스트 추가)
- [ ] R1: `rank_servo` SA(ceil−1·최상단 가드·데드밴드·서보 캡·경제성 상한[pooled_rpc→affordable_ceiling]·current-기준 스톱로스·예산 pace 사전체크·무영속 래칫) + `_judge_hourly` 구조화 verdict + `run_hourly_lane` 그레인 라우팅(BRAND_SEARCH=±15% fallback) + 공용 prefilter helper + `bid_up_servo` 레지스트리 등록(`_DAILY_LANE_PROPOSAL_TYPES` 제외) + **`execute(dry_run=False)` 실집행** + **다운스트림 정합(scoreboard/diary/retro/dedup/expiry/대시보드/real_write_blocker/delegation_gate)** + 단위/차등 테스트
- [ ] R1 GATE(Opus 적대) PASS
- [ ] R2: 시간당 estimate 배선(ceil−1·1~4 clamp) + agg precompute + `bid_up_rank` 등록 + estimate fail-closed/TOCTOU/예산캡 + `update_keyword_bid` 실집행 + 다운스트림 정합 + 테스트
- [ ] R2 GATE PASS
- [ ] R3: `bid_rank_curve` SA(조인 기반·마이그레이션 0·h−1/h+1 버킷 규칙·기울기 원/rank 단위) + `NaverLearningState`(scope="entity" 규약·bid_rank_slope·docstring/테스트 갱신) + `learning_loops.run_all` 스테이지 + 인과 오염 제외·불확실 플래그·무반응 평탄기울기 + 콜드스타트 폴백 + 익일 반영 명시 + 테스트
- [ ] R3 GATE PASS
- [ ] 배포 + 라이브(실쓰기 합격, §3-4): R0 배포 회귀 0 → R1 폐루프 한 바퀴 라이브 완주(실 `update_adgroup_bid`→다음 시간 순위 이동→데드밴드/래칫)·R2 ±15% 초과 1스텝 실쓰기·ad 카나리 UP 누출 0·사전 봉투 준수 — **다음 세션**
- [ ] 자연 발동 상설 관측(서보 래칫·데드밴드·반응 곡선 적립·무반응 hold) — 상설

---

## §실측 필요 목록 (단정 금지 — "문서에서 확인 안 됨" 플래그)

1. **`_SERVO_DEADBAND`(rank)** — 쇼검 avg_rank 노이즈 std 실측(추천 0.3 잠정).
2. **`_SERVO_DEFAULT_STEP_PCT`(콜드스타트)** — 추천 0.15 잠정.
3. **`_SERVO_MAX_STEP_PCT`(서보 절대 캡)** — 추천 0.50 잠정, 한 순위 이동 실제 최대 입찰폭 실측.
4. **bid→rank 인과 지연** — 입찰 변경이 다음 시간(쿨다운 2h) 순위에 반영되는지. 서보 핵심 가정 — 라이브 실증 전 단정 금지.
5. **시간당 레인 estimate 호출량/비용** — 실제 스텝 유닛 수 × 매시. canary 실측 후 `_HOURLY_ESTIMATE_BUDGET` 확정.
6. **한 순위 이동 실제 스텝 수(곡선 기울기)** — 유닛별 상이, R3 관측 전 미상.
7. **`estimate_average_position_bid` 유효성** — 목표 순위 1~4에 유효 bid 반환(70~100,000·10원 정합).
8. **서보 대상 유닛의 ad-라우팅 비율** — `effective_bid.adgroup_effective_bid` source='ad'는 카나리 2단계까지 서보 UP 제외 → 실제 적용 유닛 수 실측.
9. **무반응 hold 임계 N스텝** — 추천 3 잠정(경쟁 천장 판정), 실측.
10. **BRAND_SEARCH 서보 적합성** — 초기 제외(±15% fallback), 쇼검 실적 확인 후 확대 판정.
11. **예산 pace 안전계수** — 잔여예산 사전체크의 마진(추천 보수값), 최근 3h clk pace의 잔여시간 외삽 정확도 실측.

---

## §codex 왕복 결과 (원칙19 — 2026-07-20, 3라운드, 미합의 0)

R1(치명 5·허점 6·단순화 3·엣지 10·시퀀싱 4·검증 4) → 전 수용 개정 → R2(P1 5·P2 7) → 전 수용 개정 → **R3 PASS 선언**("설계 승인 관점에서 진행 가능").

**R3 잔여 P2 5건 = 구현 단계 의무 반영 목록** (구현자는 이 목록을 체크리스트로 상속):
1. `_AD_BID_CANARY_DIRECTIONS={"bid_down"}`은 이름은 direction이나 값은 proposal_type — R0에서 `direction_of(pt)` 도입 시 값을 `{"down"}`(진짜 direction)으로 바꾸거나 이름을 `_AD_BID_CANARY_PROPOSAL_TYPES`로 정정(애매하게 두면 새 타입에서 다시 새는 지점).
2. R2 TOCTOU mismatch의 DB 상태 = **failed(stale 사유 기록)로 종결**(pending 잔류 시 자동 재시도 루프 위험 — `_guard_failure` 관례 동형). 재산정은 다음 시간당 레인의 새 제안 몫.
3. 예산 pace 안전계수는 **보수 방향 < 1**로 못박고 이름도 `_BUDGET_HEADROOM_SAFETY_RATIO`(<1)로(여유계수 1.2류 오독 방지).
4. "핫셋 밖 직접 호출 시 clk=0 → hold"를 rank_servo/harness 테스트로 고정(`pooled_rpc`는 하위 clk=0이어도 상위 prior로 양수 ceiling을 만들 수 있음 — 핫셋 게이트가 실무 방어이나 테스트로 봉인).
5. R3 실패 writer 관측쌍의 "불확실" 처리는 저장 플래그가 아니라 **적합 함수 내부 가중 규칙**으로 명시(감쇠 배수 or slope 포함+confidence 하향 중 택1을 구현 시 결정·주석).
