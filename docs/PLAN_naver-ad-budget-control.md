# PLAN — 네이버 SA 예산 통제 개방 (우리 MOP = MOP Pro+ 무제한, 단 이익하한 유지)

> 트랙: `docs/tracks/active/track_naver-ad-optimization.md` **D-NAO-42-f**(= D-NAO-34 예산 금지선 개정).
> 설계: Opus (2026-07-13). 구현: Sonnet (승인 후). 각 Phase TDD + codex review(원칙19).
> 이 계획서는 **아직 승인 전이다 — 코딩 착수 금지.** Jino 승인 후 Phase 1부터 시작한다.

---

## §0 방향 고정 (읽고 시작 — 임의 변경 금지)

- **무엇을 여는가**: 네이버 캠페인 **일예산(dailyBudget) 변경**을 우리 실행 루프의 개방 액션에 추가한다. D-NAO-16 개방 순서(제외키워드→정지·재개→입찰→**예산**)의 마지막 4단계. 지금까지 예산은 "영구 스코프 밖"(D-NAO-34)이었고, D-NAO-42-f가 이를 **개방**으로 개정했다.
- **무엇을 열지 않는가**: 캠페인 생성·재구축(여전히 영구 Confirm, D-NAO-5), 예산 외 신규 액션. 입찰·정지재개는 이미 열림(X1b).
- **철학**: "우리 MOP = MOP Pro+ **무제한**"의 실체 = MOP 요금제의 **인위적 제한 제거**(유닛1·애드그룹30·전문가모드 잠금·예산=7일평균 강제). **무제한 ≠ 무분별** — 우리 **안전 가드레일(BEP 이익하한 D-NAO-1·스톱로스·클램프)은 제한이 아니라 차별점**이므로 예산 증액에도 그대로 확장한다.
- **목적함수 불변(D-NAO-1)**: 한계 ROAS ≥ BEP×공격성을 지키는 한 매출 최대. 예산 증액은 "이익 보장 잔존 볼륨이 예산 캡에 막혀 있다"는 손익 경계 신호에서만 나온다 — marginal ROAS 인과추정은 **여전히 하지 않는다**(D-S3-c 연기 사유 유지, 추정 금지).
- **게이트 불변**: 자동 발사 여부는 이 계획이 새로 만드는 게 아니라 **기존 위임 스위치(D-NAO-5/25, Jino만)**를 그대로 탄다. 현재 반자동(위임 미설정)이므로 예산 제안도 pending → Jino 콘솔 승인. 자율 승급은 Ava 수리 후(D-NAO-42-e).

---

## §1 배경 — 현재 코드 실측 (grounding, 추정 아님)

이 계획은 아래 **실제 코드**를 읽고 작성했다(파일:라인 근거).

| 구성요소 | 현재 상태 | 근거 |
|---|---|---|
| `NaverProposal` 구조화 컬럼 | `target_bid`(int)·`target_lock`(bool) 있음. **`target_budget` 없음** | `models.py:1622-1623` |
| `budget_up` 제안 생성 | `_budget_proposal`이 생성하나 **목표 예산값 없음**(rationale 텍스트만) | `proposal_writer.py:236-259` |
| 예산 신호 산출 | `budget_allocator.find_budget_expansion_signals`(소진+이익보장 잔존, `total_gap`) | `budget_allocator.py:67-98` |
| 실행 매핑 | `budget_up → update_budget` 매핑은 있음 | `naver_execution_harness.py:93` |
| 개방 여부 | `update_budget` ∉ `OPEN_ACTIONS`, ∉ `_WRITE_EXECUTORS` → 실행 불가(`real_write_blocker` "액션 미개방") | `naver_execution_harness.py:100,514-518,552-553` |
| 쓰기 어댑터 | `update_keyword_bid`/`set_*_lock` 있음. **`update_campaign_budget` 없음** | `naver_sa_writer.py` |
| 가드레일 컨텍스트 | `_build_guardrail_context`는 **keyword 대상만**(`target_type!='keyword'`이면 전부 None) | `naver_execution_harness.py:200-201` |
| 가드레일 판정 | `guardrail_gate.check`는 bid/lock만. **budget 경로 없음** | `guardrail_gate.py:59-69` |
| BEP 이익하한 | `_check_bid` 안에서 `_BID_UP_TYPES`만(보정ROAS<목표 시 차단) | `guardrail_gate.py:126-132` |
| 캠페인 예산 쓰기 API | `PUT /ncc/campaigns/{id}?fields=budget`(`CampaignRequest`: nccCampaignId, customerId, useDailyBudget, dailyBudget) **존재하나 ref 27이 의도적으로 미상세**(스코프 밖이었음) | `docs/references/27_...md:86,89,101` |

**⚠️ 추정 금지 항목**: 예산 PUT의 정확한 요청 바디(`useDailyBudget` 동반 필요 여부·periodicBudget 등)·`dailyBudget` **최소값·증분 단위**는 ref 27에 확정되지 않았다. **Phase 1 착수 시 swagger(ncc-campaign 정의) 실확인 후 코딩** — bidAmt/userLock 때 했던 것과 동일 규율. 확인 전 상수 하드코딩 금지.

---

## §2 확정 정책 (Jino, D-NAO-42-f + 2026-07-13 확정)

| # | 규칙 | 적용 지점 |
|---|---|---|
| ① | 예산 **증액은 Jino 승인** 원칙 | 반자동 게이트(기존 status='pending' → 콘솔 승인) |
| ② | **회당 총 증가액 ≤ 100,000원 자율** — **라운드 합계**(Jino 2026-07-13 "라운드 합계"). 한 라운드에서 전 캠페인 증액분의 **합**이 10만원 이내면 자율(위임 시 자동), 초과분만 승인 대기 | **생성 단계 라운드 봉투**(§5-E) |
| ③ | **회당 변경폭 상한 = 캠페인당 +100%**(한 번에 최대 2배) | 생성 시 사이징 + `_check_budget` 재검증(§5-C/D) |
| ④ | **BEP 이익하한을 예산 증액에도 확장** — 보정ROAS<목표면 증액 금지(현재는 입찰만) | `_check_budget` BEP 게이트(§5-C) |
| ⑤ | **스톱로스 대칭** — 무전환 출혈 캠페인은 증액 금지(감액 방향은 자유) | `_check_budget` 스톱로스(§5-C) |
| ⑥ | **감액은 자유 자율** | `budget_down` 경로(§5-F, 별도 라운드 캡 없음) |

**두 캡 동시 작동**: ⓐ**라운드 절대 캡**(전 캠페인 증액 합 ≤10만 자율) + ⓑ**캠페인당 비율 캡**(+100%). 서로 독립 — ③이 한 캠페인의 폭을, ②가 라운드 전체의 총량을 막는다.

---

## §3 아키텍처 (원칙18 — Agent / Harness / Sub-Agent)

```
Agent: 예산 최적화 (네이버 SA 예산 통제)
 └ Harness: budget_execution  (= naver_execution_harness 확장 + proposal_pipeline 라운드 봉투)
      ├ SA budget_allocator          [기존] 소진+이익보장 잔존 신호 산출 (find_budget_expansion_signals)
      ├ SA budget_sizer              [신규 or _budget_proposal 확장] 신호 → 목표예산(target_budget) 사이징
      │                                (+100% 클램프 + pred_cost 기준, 인과추정 없음)
      ├ SA proposal_writer           [기존 확장] target_budget 구조화 저장 + 라운드 봉투 플래그
      ├ SA guardrail_gate._check_budget [신규] 실행 직전 순수 판정
      │                                (클램프·+100%·BEP하한·스톱로스·쿨다운·방향)
      ├ SA naver_sa_writer.update_campaign_budget [신규] PUT dailyBudget + 전후 재조회(fail-closed)
      └ SA campaign_target_resolver  [기존] target_roas 조회 (BEP×공격성)
```

**정보 유통(원칙18-6)**: harness가 `budget_allocator` 출력 → `budget_sizer` 입력 → `proposal_writer` 저장, 그리고 실행 시 `_build_guardrail_context`(campaign 브랜치 신규) → `_check_budget`. SA끼리 직접 호출 없음.

---

## §5 설계 상세

### A. 스키마 — `NaverProposal.target_budget` (additive)

- 신규 컬럼 `target_budget: int | None`(원). `target_bid`와 완전 병렬(실행자는 이 컬럼만 읽음, rationale 파싱 금지).
- 마이그레이션 1개(additive, nullable) — DB 스키마 변경이므로 **D-NAO-42-f 승인 범위 안에서만**(별도 위험 없음, X1b `target_bid` 추가와 동일 선례 `c3d4e5f6g7h8`).
- 라운드 봉투 분류를 저장할 필드가 필요하면(§E) `budget_auto_eligible: bool | None`도 같은 마이그레이션에 포함(설계 결정 §6-2 참조).

### B. 쓰기 어댑터 — `naver_sa_writer.update_campaign_budget(ncc_campaign_id, daily_budget)`

**✅ P0 swagger+라이브 확정(2026-07-13, `docs/references/data/ncc-heroes-ncc.json` + 라이브 04 GET)**:
- 엔드포인트: `PUT /ncc/campaigns/{id}?fields=budget`(fields enum=`userLock|budget|period`, swagger 경로 `/api/ncc/...`이나 fetcher BASE_URL/서명은 `set_campaign_lock`와 동일 규율).
- body: `{"nccCampaignId": id, "customerId": int(fetcher.CUSTOMER_ID), "useDailyBudget": True, "dailyBudget": daily_budget}` — `CampaignRequest`(customerId·nccCampaignId #required-update). **`useDailyBudget=True` 필수**(false면 dailyBudget 무시, swagger 명시).
- 패턴은 `update_keyword_bid` 동일: 사전검증 → before 재조회(`get_campaign`) → PUT → 2xx 아니면 `WriteError` → **after 재조회로만 성공 판정**(fail-closed): after `dailyBudget == daily_budget` **및** `useDailyBudget is True`(bidAmt 때 `useGroupBidAmt` 확인과 동형).
- **★공유예산 fail-closed**: before의 `sharedBudgetId`가 None이 아니면 → `WriteValidationError`(공유예산 캠페인은 per-campaign dailyBudget 무효, swagger `sharedDailyBudget` 별도). 라이브 04는 `sharedBudgetId=None`(안전).
- **min/증분 = swagger에 없음(정직 라벨)**: `dailyBudget`은 integer·default 0, minimum/multipleOf 미정의. → 사전검증은 `daily_budget > 0` 정수만, 나머지는 **네이버 API 거부 + after 재조회 exact-match**(fail-closed)에 위임(ref 27 미지수 처리 규율과 동일). 정확 min·증분은 **P4 라이브 왕복에서 실측**. sizer가 100원 단위로 반올림(§G, 침묵 반올림 방어).
- `customerId` 포함(라이브 04=1313769, `set_campaign_lock`:440과 동일).

### C. 가드레일 — `guardrail_gate._check_budget(proposal, context, proposal_type)`

`check()`에 `_BUDGET_UP_TYPES={"budget_up"}` / `_BUDGET_DOWN_TYPES={"budget_down"}` 분기 추가. 판정 순서(증액):
1. `target_budget` 존재 + 클램프(최소값~상한, 증분 단위 — swagger 확정값).
2. `current_budget` 미확보 → fail-closed 차단.
3. **방향 일치**: budget_up인데 `target_budget ≤ current_budget` → 차단(bid 방향검증과 동형, codex[P2] 선례).
4. **캠페인당 +100% 캡**(③): `(target-current)/current > 1.0` → 차단.
5. **스톱로스**(⑤): campaign `unconverted_spend ≥ 캠페인 스톱로스 상한` → 차단(증액만).
6. **BEP 이익하한**(④): `roas_corrected < target_roas` → 차단(증액만) — 현 `:126-132` 로직을 budget_up에도 적용.
7. 쿨다운·일일 변경 상한(전 유형 공통, 기존 `_check_cooldown_and_cap` 재사용).

감액(budget_down)은 방향 검증 + 클램프만(스톱로스/BEP/+100% 면제 — 감액은 자유 ⑥).

### D. 캠페인 단위 가드레일 컨텍스트 — `_build_guardrail_context` campaign 브랜치

현재 keyword 전용(`:200`). budget_up은 `target_type='campaign'` → 신규 분기:
- `current_bid` 대신 `current_budget` = `naver_sa_writer.get_campaign(campaign_id).dailyBudget`(라이브 재조회).
- `roas_corrected`/`unconverted_spend` = **캠페인 단위 집계**(신규 `account_diagnosis.campaign_window_agg` 또는 기존 집계 재사용, 30일 창 as_of=D-1) × 보정계수(D-NAO-21).
- `target_roas` = `campaign_target_resolver.resolve_target_roas`(이미 캠페인 단위).
- `cost_today`/`daily_budget` = `NaverHourlySnapshot` 당일 최신(이미 캠페인 단위, budget_allocator와 동일 소스).
- `last_change_at`/`changes_today_count` = change_log `entity_type='campaign'`.
- 컨텍스트 dict에 `current_budget` 키 추가(기존 `current_bid`와 공존).

### E. 라운드 봉투 — "회당 총 증가액 ≤100,000 (라운드 합계)"

**핵심 설계**: `execute()`는 제안 1건씩 실행하므로 "라운드 합계"는 실행 단계가 아니라 **생성 단계**에서 강제한다. 1 라운드 = **1 proposal 생성 런**(proposal_pipeline.run_daily 08:00, 또는 트리거 런).

절차(`proposal_pipeline`에서 budget_up 제안 생성 직후):
1. budget_up 제안을 우선순위(`total_gap` 내림차순)로 정렬.
2. 각 제안의 증가액 Δ = `target_budget - current_budget` 누적.
3. 누적 ΣΔ ≤ 100,000 → `budget_auto_eligible=True`(위임 시 자동 발사 대상).
4. ΣΔ가 10만을 넘기는 제안부터 → `budget_auto_eligible=False`(**초과분 = 위임 있어도 반드시 Jino 승인**, ②"초과분만 승인 대기").
5. **현재 반자동**: 전부 `status='pending'` → Jino 콘솔 승인. `budget_auto_eligible`는 **위임 켜질 때 소비되는 분류 메타**(오늘은 게이트 아님, 미래 자동 경로용).

> ⚠️ 라운드 봉투는 **자율(위임) 승급 후에만 실효**. 오늘 반자동에서는 Jino가 승인하는 모든 증액이 유효(①"증액은 Jino 승인 원칙" — 사람 승인은 항상 허용, 10만은 "무승인 자율 한도"). 그래서 라운드 캡은 실행 차단이 아니라 **자동승인 봉투**에 있다. 이 구분이 정책 ①②의 정확한 실체다.

### F. OPEN_ACTIONS / 실행자

- `OPEN_ACTIONS += "update_budget"`(코드 배포로만, D-NAO-16 4단계).
- `_WRITE_EXECUTORS["update_budget"] = _execute_update_budget`(신규) — `_execute_update_bid`와 동형: 구조검증(target_type='campaign'·target_budget 존재) → `_build_guardrail_context`(campaign) → `guardrail_gate.check` → `_claim_executing` → `update_campaign_budget` → change_log 전건(before/after) + MOP 충돌 감지(`_detect_external_change` field='dailyBudget') → `verify_date`.
- `budget_down` 제안유형·생성기(감액)도 필요 → `_ACTION_BY_PROPOSAL_TYPE["budget_down"]="update_budget"` 추가, 방향은 `target_budget<current`로 guardrail이 구분.
- `real_write_blocker`에 update_budget 분기(구조 판정만, UI executable 노출).

### G. 사이징 규칙 (budget_sizer) — 목표 예산값 산정

marginal ROAS 인과추정 없이(추정 금지) 목표 예산을 정한다:
- **목표 예산 = clamp_to_increment( min( current×2, max(current+1증분, pred_cost_uncapped) ) )**
  - `pred_cost_uncapped` = forecast_engine 캠페인 grain `pred_cost`(추세 지수감쇠, 비인과 — D-NAO-26/28). 예측 있으면 "추세가 말하는 미제한 지출"까지 완화.
  - 예측 없으면(fallback) 보수적 고정 스텝 **+20%(`current×1.2`, Jino 확정 2026-07-13)**.
  - `min(current×2, …)` = 캠페인당 +100% 캡(③) 반영.
- 감액(budget_down)은 별도 신호(지속 저소진·BEP 미달)에서 — Phase 3(선택).

---

## §6 핵심 설계 결정 & 열린 질문 (Jino 확인 후 확정)

1. **라운드 봉투 위치 = 생성 단계**(§E). 근거: `execute()`가 per-proposal이라 실행 단계엔 "라운드" 개념이 없음. 라운드 캡은 자동승인 봉투에 사는 게 정책 ①②와 정합. **→ 채택(확정).**
2. **`budget_auto_eligible` 컬럼 신설** — **✅ Jino 확정 "넣어"(2026-07-13)**. 마이그레이션 A에 `target_budget`과 함께 포함. 위임 켜질 때 라운드 봉투 분류 재현.
3. **사이징 fallback 스텝** — **✅ Jino 확정 "+20%"(2026-07-13)**. 예측(pred_cost) 없는 캠페인은 `current×1.2`(캠페인당 +100% 캡 안, 보수적). §5-G의 "+50%" 예시는 **+20%로 확정**.
4. **자율 실효 시점**: 라운드 봉투·budget_auto_eligible이 실제로 자동 발사하려면 **위임 스위치(budget_up 유형)** 를 Jino가 켜야 함 + Ava 수리 선결(D-NAO-42-e). 이 계획은 **capability까지만**(반자동 완전 작동), 자율 발사는 별도. **→ 확인 불요, 원칙 재확인.**
5. **budget clamp 최소·증분**: swagger 확정 필요(추정 금지) — Phase 1 첫 작업. **→ 확인 불요(코드에서 실측).**

---

## §7 Phase 분할 (각 Phase: TDD → codex review pass → 커밋 → 트랙/계획서 §9 즉시 갱신)

- **P0 (착수 전)**: swagger로 캠페인 budget PUT 바디·min·증분 실확인(§1 추정금지 해소). 결과를 이 §5-B에 확정 기록.
- **P1 스키마+쓰기**: 마이그레이션(`target_budget`[+`budget_auto_eligible`]) + `naver_sa_writer.update_campaign_budget`(before/after 재조회 계약). fix전/후 차등 테스트.
- **P2 가드레일**: `guardrail_gate._check_budget`(클램프·방향·+100%·스톱로스·BEP·쿨다운) + `_build_guardrail_context` campaign 브랜치 + `campaign_window_agg`(필요 시). 순수 SA 단위 테스트.
- **P3 생성+실행 배선**: `_budget_proposal`에 target_budget 사이징 + 라운드 봉투(proposal_pipeline) + `_execute_update_budget` + `OPEN_ACTIONS`/`_WRITE_EXECUTORS`/`real_write_blocker`/`_ACTION_BY_PROPOSAL_TYPE(budget_down)`. end-to-end 테스트(dry-run→가드 차단 실측→봉투 분류).
- **P4 라이브 검증(원칙22)**: 04 카나리(cmp-…008514959, optimizer='ours')에서 왕복 — 예산 증액 제안 생성 → Jino 콘솔 승인 → 실 PUT → 재조회 반영 → change_log. 가드레일 실차단(±100% 초과·BEP 미달) 라이브 실측. **격리 통과 ≠ 라이브 합격**(원칙22).

각 Phase codex review **pass** 필요(원칙19). fail/needs-changes 시 대화형 검증(원칙19 codex↔Claude). PR은 P4까지 완료 + Jino 최종 승인 후.

---

## §8 리스크 & 정직 경계

- **자율 오해 방지**: "무제한 예산"은 위임 켜지고 Ava 수리된 이후에만 자동. 지금 만드는 건 반자동 완전 작동 + 자율 봉투 분류까지. 라이브 자동 증액은 이 계획 밖.
- **캠페인 단위 스톱로스/BEP**: 키워드 집계는 있으나 캠페인 집계 헬퍼는 신규 — 기존 keyword_window_agg와 계산 일관성 유지(보정계수 동일 산식).
- **MOP 충돌**: budget도 `_detect_external_change`로 우리 마지막 기록 vs 라이브 dailyBudget 비교 경고(D-NAO-13). MOP가 켜져 있으면 예산도 외부 변경될 수 있음.
- **swagger 미확정 = 하드 블로커**: P0 확인 전 P1 코딩 금지.
- **04 카나리 예산 소액(30,000원)**: +100%=60,000원, 라운드 10만 안. 라이브 왕복에 적합(소액 안전).

---

## §9 체크리스트

- [ ] Jino 계획 승인 (§6 열린질문 2·3 확정)
- [ ] P0 swagger 캠페인 budget PUT 실확인 → §5-B 확정
- [ ] P1 스키마 마이그레이션 + update_campaign_budget + 테스트 + codex pass
- [ ] P2 _check_budget + campaign 컨텍스트 + 테스트 + codex pass
- [ ] P3 사이징 + 라운드 봉투 + _execute_update_budget + 배선 + 테스트 + codex pass
- [ ] P4 04 카나리 라이브 왕복 + 가드레일 실차단 실측(원칙22)
- [ ] 트랙 D-NAO-42-f 하위에 구현 완료 D-N 기록 + PR(Jino 승인 후)

## §10 완료 기준

1. 04 카나리에서 예산 증액 제안이 생성되고, Jino 콘솔 승인 시 실 PUT이 네이버에 반영(재조회 실측)되며 change_log에 전건 기록된다.
2. 가드레일이 실제로 차단한다(라이브): +100% 초과 증액·BEP 미달 캠페인 증액·클램프 위반이 각각 차단되고 사유가 남는다.
3. 라운드 봉투가 한 라운드 증액 합 10만 경계에서 auto_eligible을 정확히 분류한다(단위 테스트 + 실런 로그).
4. 감액은 가드레일 최소검증만으로 자유 통과한다.
5. 전 과정 optimizer='ours' 하드체크·반자동 승인 게이트를 우회하지 않는다(D-NAO-5/13 불변).
