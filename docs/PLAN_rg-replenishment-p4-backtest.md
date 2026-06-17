# PLAN — RG 발송관제 P4 백테스트 하니스

> 트랙: `docs/tracks/active/track_coupang-rg-replenishment.md` · D-18 완료 게이트 ②
> 작성 2026-06-19 (Opus) · 상태: 구조 승인됨(Jino "A로 가자"), 구현 대기(Sonnet)
> 이 문서 = 계획서 + 맥락노트 + 체크리스트 통합(원칙4).

## 1. 목표 (한 줄)
파는 옵션(demand-class sparse+active)의 발송추천이 실제로 좋았는지를 **자기평가 아닌 과거 데이터로 증명**한다(원칙3·14). 산출 = 옵션별 **fill-rate(품절 회피율)** + **평균 과잉재고일수** + **유효 윈도우 수**.

## 2. 데이터 제약 & 핵심 설계결정 (정직성, 원칙22)
- **과거 재고 스냅샷 없음**: `rg_inventory.orderable_qty`는 `synced_at` onupdate로 매번 덮어쓰는 현재 스냅샷. 그날 재고를 역재현 불가 → **재고 깊이 시뮬 백테스트는 불가**.
- **판매 시계열은 있음**: `rg_order_item(paid_at, sales_quantity)` → 일별 실수요 재구성 가능.
- **D-결정-A (target-vs-demand 검증)**: 정확 재고 replay 대신 **"과거 시점 D까지 데이터로 산정한 목표재고가, D 이후 보호구간 실제 수요를 덮었나(품절) + 얼마나 남겼나(과잉)"**를 검증. 추천 품질의 진짜 불확실성=수요예측이므로 이게 핵심을 친다. 재고 스냅샷 없이 판매 시계열만으로 성립.
- **D-결정-B (velocity = order_item 시계열만)**: 백테스트 as-of-D 일판매율은 **order_item 시계열에서만** 산정한다. 라이브 엔진의 `sold_30d` 폴백은 **현재 스냅샷이라 시간 역행 불가** → 백테스트에서 사용 금지(과거 충실성). 즉 백테스트는 "깨끗한 주문 데이터 기반 예측"의 적정성을 측정. (라이브가 sold_30d 폴백을 쓰는 옵션은 백테스트 base 부족으로 skip되며, 이는 정직하게 윈도우 0으로 표기.)

## 3. 구조 (원칙18 레고 계층 — 승인됨)
```
[Agent] RG 발송관제 (로켓그로스 탭)
  └─[Harness] replenishment_backtest  ★신규 — walk-forward 검증 (읽기전용, net_profit/머니 무영향)
        ├─[SA] sales_velocity_estimator  기존 S3 — ★as_of=D 파라미터 추가(D까지만 학습; as_of=None이면 현행 동일=등가성 계약)
        ├─[SA] lead_time_estimator       기존 S2 — 리드 평균/p90 (변경 없음, Jino "리드 신뢰")
        └─[순수함수] _score_window         ★신규 — 목표재고 S vs 보호구간 실제수요 A → 품절/과잉 판정
  [라우터] GET /api/coupang/ops/replenishment-backtest?account_key=&train_min_days=&protection_mode=  (검증/UI, 원칙18-7 조회 예외 — read-only지만 다중 SA 가로지르므로 Harness 경유)
```
- 신규 파일: `backend/app/services/coupang/replenishment_backtest.py`(Harness + _score 순수함수).
- 기존파일 수정 1곳: `sales_velocity_estimator.py`(`_compute_context`/공개함수에 `as_of` optional). S2·replenishment_calc·rg_replenishment 무변경.

## 4. _score_window 알고리즘 (검증 로직 — 정확히 못박음)
옵션별, cutoff D별:
1. **학습(as-of-D)**: `_compute_context(db, account_key, as_of=D)` → 구간계수(평일/주말/휴일) + 옵션 일별판매. 옵션 base_rate(D) = `[TRUST_START, D]` order_item 일판매 평균(관측일로 나눔, 0일 포함). 관측 nonzero일 < 1 또는 학습일수 < `train_min_days` → 이 옵션·D skip(이유 기록).
2. **목표재고 S(D)** = 라이브 엔진 정책 그대로 (**A1: `replenishment_calc.compute_target_level` 공유 순수함수 호출** — _calc와 동일 공식, 백테스트가 진짜 프로덕션 정책 검증):
   `safety = max(0, (p90_L − mean_L) × base_rate)`
   `S = target_days × base_rate + safety`  (target_days = D-16 review_period = 7)
3. **보호구간 H**: `protection_mode`로 선택 —
   - `full`(기본): `H = ceil(p90_L) + review_period(7)` ≈ 10일 (표준 (R,L) 보호구간).
   - `lead_only`: `H = ceil(p90_L)` ≈ 3일 (얇은 데이터용, 더 많은 윈도우 확보).
4. **실제수요 A** = `Σ d[t]` for `t ∈ [D+1, D+H]`. **윈도우가 데이터 끝(until)을 넘으면 incomplete → skip**(미래 모름, 은폐 금지). 완전 윈도우만 valid.
5. **판정**:
   - **stockout** ⟺ `A > S` (목표가 실현수요 못 덮음).
   - **overstock_days** = `A < S`일 때 `(S − A) / base_rate` (수요 대비 남은 일수). base_rate>0 보장(2번에서 base_rate=0이면 1번 skip).
6. **옵션 집계**(valid 윈도우들):
   - `fill_rate` = 1 − (#stockout / #valid). (사이클 서비스수준식, 해석 단순.)
   - `mean_overstock_days` = 비품절 윈도우 overstock_days 평균.
   - `valid_windows`, `skipped_windows`(사유별 카운트).
7. **포트폴리오 요약**: 옵션 평균 fill_rate(valid≥1 옵션만), 평균 overstock_days, Σvalid_windows, 커버 옵션 수, skip 옵션 수. **note**: `valid_windows`가 작으면(예 <10) "indicative only(데이터 부족)" 플래그.

## 5. 파라미터 (tunable, 기본값)
| 파라미터 | 기본 | 의미 |
|---|---|---|
| `train_min_days` | 7 | D 이전 최소 학습일수(평일/주말 구분 위해) |
| `protection_mode` | `full` | `full`(p90+R≈10) / `lead_only`(p90≈3) |
| `review_period` | 7 | D-16 cadence, S(D) target_days |
| `account_key` | None | 전체/계정 필터 |
- 얇은 데이터(현 15일) 현실: `full`은 valid 윈도우 ≈0 예상 → `lead_only`로도 돌려 측정. **결과에 valid_windows 항상 표기**(원칙22 은폐 금지). 데이터 누적 시 같은 코드가 자동 결론화.

## 6. S3 `as_of` 리팩터 (등가성 계약 — S5 패턴 계승)
- `_compute_context(db, account_key, *, as_of: date | None = None)`: `until = as_of if as_of else (kst_today() - 1)`. 그 외 로직 불변.
- 공개 `estimate_sales_velocities`/`estimate_sales_velocity`에 `as_of` optional 전달(기본 None).
- **계약**: `as_of=None` 호출 결과 = 리팩터 전과 **정확히 동일**(현행 회귀 0). 테스트로 고정.

## 7. 응답 스키마(라우터)
```
{ generated_at, account_key, params:{train_min_days,protection_mode,review_period},
  summary:{ options_covered, options_skipped, total_valid_windows,
            mean_fill_rate, mean_overstock_days, indicative_only:bool, note },
  options:[ { vendor_item_id, product_name, item_name, base_source,
              valid_windows, skipped_windows:{reason:count},
              fill_rate, mean_overstock_days, stockout_windows } ] }
```

## 8. 엣지/정직성 체크리스트 (적대검증 선제)
- base_rate=0/None → 옵션 skip(품질 보장), `(S−A)/base_rate` /0 없음.
- incomplete window(미래 데이터 부족) → valid에서 제외 + skipped 카운트.
- valid_windows=0 옵션 → fill_rate=None(허위 100% 금지), summary에서 제외.
- as_of=None 등가성 회귀 테스트.
- 휴일/주말 분류는 S3 `_classify_day` 재사용(중복 금지).
- net_profit/머니 무영향(읽기전용) — 라이브 회계 불변 확인.

## 9. 테스트 계획 (fixture oracle, 손계산)
- `_score_window` 단위: 합성 일판매 시계열로 stockout/overstock 손계산 대조(품절 경계 A=S, A>S, A<S; overstock_days 산식; base_rate=0 skip).
- walk-forward: 짧은 합성 시계열로 valid/incomplete/skip 분기 검증.
- as_of 등가성: as_of=None == 현행.
- 전체 스위트 그린 유지(현 249 + 신규).

## 10. 완료 기준 (게이트 ② 충족)
- [ ] `replenishment_backtest.py`(Harness + _score) + S3 as_of 리팩터.
- [ ] 라우터 `GET /replenishment-backtest`.
- [ ] fixture 테스트(oracle 손계산 + as_of 등가성) 그린.
- [ ] Claude 서브 적대검증 GATE PASS(P1 0) — codex 미사용(Jino 지시).
- [ ] prod 라이브 self-verify(원칙22): 실제 9 신호옵션 대상 `lead_only`·`full` 둘 다 호출 → valid_windows·fill_rate·overstock 실수치 + "indicative_only" 정직 표기 확인. net_profit 불변.
- [ ] 트랙 P4 [x] + 결과 섹션 + progress 갱신.

## 11. 체크리스트 (Tasks)
- ⏳ T0. (A1) `replenishment_calc.compute_target_level` 순수함수 추출 + `_calc` 호출 전환(무행동 등가). 백테스트가 동일 공식 검증 보장.
- ⏳ T1. ~~S3 as_of 리팩터~~ **불필요로 판명(구현 중)** — 백테스트는 day-by-day 소진 시뮬 안 함 → 세그먼트 계수 불필요 → base_rate만 필요. **S3 순수함수 `_option_base_rate`(임계 로직 동일)+상수 `TRUST_START`+`demand_classifier._load_daily_series`(날짜별 슬라이스) 재사용**으로 충분. **S3 `_compute_context` 무변경 = 등가성 리스크 0, 더 작은 diff.** sold_30d 배제(D-결정-B)는 `_option_base_rate(..., sold30=None)` 호출로 구현.
- ⏳ T2. `_score_window` 순수함수(compute_target_level 호출) + fixture oracle.
- ⏳ T3. `run_backtest` Harness(배치 로드 1회→cutoff 루프, 원칙18-8).
- ⏳ T4. 라우터 + 응답 스키마.
- ⏳ T5. 적대검증 → prod self-verify → 트랙/progress 갱신 → 커밋.

## GSTACK REVIEW REPORT
| 항목 | 내용 |
|------|------|
| Runs | plan-eng-review 1회 (Opus, codex 미사용=Jino 지시 → 자동결정 진행) |
| Status | PASS (Step 0 통과: <8파일·신규클래스0·과설계 아님) |
| Findings | A1 DRY+정확성(compute_target_level 공유추출 → 채택, T0 신설) · A2 정직성(order_item velocity self-resolving, 문서화) · A3/A4 투명성(윈도우기반 fill-rate + 카운트 노출, overstock=윈도우끝 잔여 근사 명시) · as_of 리팩터 유지(정책 진화 충실) |
| 자동결정 | Jino "너가 제안하는 옵션으로 자동진행" → 위 권장안 전부 채택. A1만 계획 변경(replenishment_calc 무행동 추출 1곳 +). 나머지는 계획 보강. |

VERDICT: PASS — 구현 진행. (CODEX absorbed: N/A, codex 미사용. CROSS-MODEL: N/A.)

NO UNRESOLVED DECISIONS
