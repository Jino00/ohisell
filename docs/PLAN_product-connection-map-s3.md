# PLAN — 상품 연관맵 S3: 통합 손익 조망 (Unified P&L) — **대조원장 우선(reconciliation-first)**

> 트랙: `docs/tracks/active/track_product-connection-map.md` (D-1~D-7 전제)
> 대상: `/plan-eng-review` (codex outside-voice 15건 흡수 후 재작성 v2, 2026-07-03)

## 0. 핵심 재구성 (codex #15 수용, D6)

**옵션(SKU)별 손익표를 먼저 만들지 않는다. "돈이 사라지지 않음"을 증명하는 대조원장(reconciliation ledger)을 먼저 만들고, 원장이 균형을 이룬 뒤에만 SKU 행을 노출한다.**

이 프로젝트의 RG 수수료 회계 트랙이 이미 쓴 reconciliation-first 선례와 동일(메모리: "Phase1 대조뷰만·net_profit 불변"). 어려운 건 internal_sku 그룹핑이 아니라, 기존 회계 엔진의 **모든 소계를 보존**하면서 SKU로 못 내리는 부분(미매핑·계정조정·VAT·1P광고)을 **1급 잔차 버킷**으로 드러내는 것.

### 보존 법칙 (conservation law — 이 트랙의 정의)
채널·컴포넌트(매출/수수료/광고/원가/조정)마다:
```
Σ(SKU행 귀속분) + Σ(잔차 버킷) == 기존 권위 엔진의 계정레벨 소계
```
정확히 성립해야 원장 통과(tolerance 아님, D5). 성립 안 하면 조인/기준 버그.

## 1. 전제 결정 (D-1~D-7 + 엔지니어링 리뷰 D1~D6)

### 트랙 결정
- **D-7**: 1P 옵션↔internal_sku 브리지 정본 = `RocketProductCostMap`(product_channel_mapping ROCKET행은 조인 미사용, 불일치는 리포트).

### 엔지니어링/외부검증 결정 (2026-07-03)
- **D1 (모듈경계)**: `intelligence.py` 밑줄 함수 그대로 import 재사용. **단 characterization 회귀테스트로 현재 의미론 고정**(codex #13) — 시그니처뿐 아니라 반환 grain·필터 의미를 테스트로 못박는다.
- **D2 (커버리지)**: SKU 행마다 채널별 `fee_coverage`·`ad_spend_coverage` = `"option" | "account_only" | "missing"`. 0원과 '데이터 없음'과 '계정에만 있음'을 구분. 추정 배분 금지.
- **D3 (DRY)**: `_agg_rg_settlement_fees(grain="account"|"option")` 파라미터화. account 기본값 무변경(IRON RULE 회귀테스트).
- **D4 (네이버/cafe24)**: `_line_revenue`·`_line_commission`만 재사용, `Order`를 **`ProductMaster.internal_sku`로 조인·group by**(codex #5 — `product_id`는 `product_master.id`지 internal_sku 아님).
- **D5 (판정)**: 교차검증 = 잔차 분해(보존 법칙 정확 성립), tolerance 아님.
- **D6 (전략)**: 대조원장 우선(이 문서).

### 신규 결정 (codex 흡수 — 트랙 D-8~D-11로 승격 예정, Jino 확인 필요)
- **D-8 (RG 의미 명시, codex #3)**: 현재 command_center는 RG 정산을 옵션 net에서 빼고 summary에서만 차감(플립 D-16). S3의 SKU행은 **RG 옵션 수수료를 행에 귀속**시키되(신규 지표임을 명시), 원장에서 `Σ(RG 옵션 귀속) + RG_vat_residual + RG_unmapped == 계정 RG 플립 총액`으로 대조. 즉 새 지표를 만들되 기존 총액과 화해시킨다.
- **D-9 (VAT 기준, codex #2)**: RG 옵션행은 VAT前(A−B), 계정행은 VAT後. SKU 귀속 시 gross-up 하고, gross-up 잔차는 `rg_vat_residual` 버킷으로 원장에 노출(임의 반올림 은폐 금지).
- **D-10 (날짜 기준, codex #9·#10)**: 각 채널 날짜 기준을 원장에 명시 컬럼으로 기록(3P 주문일/3P수수료 인식일/RG paid_at/RG정산 기간중첩/1P 발주일/1P광고 리포트일). 교차검증은 **각 소스 엔진이 쓰는 그 기준·그 창**으로 대조. RG 정산이 창에 완전 포함되지 않는 부분기간은 `partial_period_settlement` 경고 플래그(일할 안 함, codex #10).
- **D-11 (계정 스코프, codex #12)**: 엔드포인트는 `account` 파라미터 필수. 미지정 시 "전 계정" 계약을 명시하고 **계정별 원장 배열**로 반환(계정 인식 엔진과 정합).

## 2. 데이터 흐름

```
GET /api/products/pnl-reconciliation?from&to&account
        │
        ▼
[Harness 3a] 대조원장 (Reconciliation Ledger)  ← 먼저, 반드시 균형
  채널별·컴포넌트별:
   ├─ authoritative_total  ← compute_command_center / compute_rocket_overview (권위 소계)
   ├─ allocated_to_sku     ← Σ SKU 귀속분
   ├─ 잔차 버킷:
   │    unmapped_3p / unmapped_rg / unmapped_1p / unmapped_naver / unmapped_cafe24
   │    account_adjustments (정산매출조정·non-PA광고·RG플립잔차·판매자배송)  ← codex #1
   │    rg_vat_residual                                                    ← codex #2/#9
   │    naver_cafe24_shipping                                              ← codex #7
   │    account_only_ad (1P 광고 등 옵션귀속 불가)                          ← codex #4
   └─ conservation_check: allocated + Σ잔차 == authoritative  (정확)
        │  (불균형이면 여기서 실패 표면화 — SKU행 신뢰 불가)
        ▼
[Harness 3b] SKU 행 (옵션 손익) — 원장 균형 후에만
  internal_sku별:
   ├─ 채널 분해 (3P/RG/1P/naver/cafe24 각 매출·수수료·광고·원가)
   ├─ net_profit_allocated_only   ← SKU 귀속분만 (계정조정 제외)      ← codex #14
   ├─ account_adjustment_residual ← 이 계정의 미귀속 조정 (참고, 안분 안 함)
   ├─ reconciled_net_profit       ← 계정 단위에서만 의미(= 엔진 총액)
   └─ coverage 플래그 (D2)
```

## 3. SA / Harness (재사용 우선)

### SA-1 매출 (channel별, internal_sku 그룹)
- 3P/RG: `_agg_orders`+`_agg_rg_orders`+`_merge_rg_orders`(vendor_item_id) → `product_channel_mapping`으로 internal_sku 리매핑.
- 1P: `CoupangRocketPurchaseOrderItem` × `RocketProductCostMap`(D-7) → internal_sku. 매출=`line_order_amount`(D-3).
- 네이버/cafe24: `_line_revenue`를 `Order` 라인마다 호출, `ProductMaster.internal_sku` group by(D4·codex #5).

### SA-2 원가 (codex #6 — 채널별 순수량)
- `_cost_master`(internal_sku→cost_price)는 단가 소스. **원가 = 단가 × 채널별 순수량(주문−반품)**을 채널마다 따로 계산(옵션당 1회 아님). 반품 의미가 채널마다 다름을 명시.

### SA-3 수수료
- 3P: `revenue_fee_source.actual_fee_by_order_option` 재사용.
- RG: `_agg_rg_settlement_fees(grain="option")`(D3) + VAT gross-up(D-9), 잔차는 원장 버킷.
- 네이버/cafe24: `_line_commission` group.

### SA-4 광고비
- 3P/RG: `_agg_ads`(옵션) 재사용.
- 1P: 옵션 데이터 없음 → SKU행 `account_only`, 실제 금액은 원장 `account_only_ad`(codex #4).
- 네이버/cafe24: `profit_calculator`가 배분하는 Meta/네이버SA/GFA 광고를 **원장 `account_adjustments`에 포함**(codex #8 — null로 무시하면 재사용 정의 위반). SKU행 귀속은 Phase2(옵션단위 소스 확인 후), S3에선 계정레벨 잔차로만.

### SA-5 대조 (신규 — 이 트랙의 핵심)
- 각 채널·컴포넌트에서 `allocated + Σ잔차 == authoritative` 검증하는 순수함수. 불균형 diff를 구조화 리포트로 반환.

### Harness 3a/3b
- `backend/app/services/product_pnl.py`(top-level — 네이버/cafe24 포함). 3a가 3b의 전제(원장 불균형 시 SKU행 `trustworthy=false`).
- 라우터: `GET /api/products/pnl-reconciliation`(읽기전용, account 파라미터 D-11).

## 4. 검증 (원칙22)
1. **characterization 회귀(codex #13)**: 재사용하는 밑줄 함수 5개의 현재 반환 의미를 fixture로 고정. `_agg_rg_settlement_fees` grain 기본값 account 불변(IRON RULE).
2. **보존 법칙 유닛(D5)**: 채널·컴포넌트마다 allocated+잔차==authoritative 정확 성립. 일부러 미매핑/부분기간/VAT前 케이스 fixture 주입.
3. **라이브 self-verify**: dev DB 사본에서 실제 계정·기간으로 원장 균형 확인, 기존 command-center/rocket-overview 총액과 정확 대조. 불균형이면 배포 금지.
4. codex review(원칙19) — 보존 법칙 성립·VAT gross-up·날짜기준 정합 집중.

## 5. NOT in scope (명시적 이연)
- 프론트 UI(탭1 연관맵 편집·탭2 손익) — S4/S5.
- 오픽스(WING1/RG1) 매핑 결손 보강 — S6.
- 네이버/cafe24 **옵션단위** 광고비 귀속 — Phase2(현재 소스는 계정레벨 배분뿐, codex #8 → S3는 원장 잔차로만).
- 1P 옵션단위 광고비(Billboard) — 별도 Phase2(오하이테크 광고 트랙).
- RG 정산 부분기간 일할 배분 — 하지 않음(경고 플래그만, codex #10).

## 6. What already exists (재사용/재구축 판정)
| 기능 | 기존 자산 | S3 처리 |
|---|---|---|
| 3P/RG 매출·수수료·광고 | `intelligence.py` 밑줄 함수 | 재사용(D1), internal_sku 리매핑만 신규 |
| 1P 매출·원가 | `rocket_intelligence` + `RocketProductCostMap` | 재사용(D-7) |
| 네이버/cafe24 매출·수수료 | `profit_calculator._line_*` | 라인헬퍼만 재사용(D4) |
| 계정레벨 권위 총액 | `compute_command_center`·`compute_rocket_overview` | 대조 기준으로 재사용(재구축 안 함) |
| 대조원장·잔차 버킷 | 없음 | **신규**(이 트랙 핵심) |

## 7. Implementation Tasks
이 리뷰 findings에서 도출. Claude Code 또는 Codex로 실행, 완료 시 체크.

- [ ] **T1 (P1, human: ~1h / CC: ~15min)** — intelligence.py — `_agg_rg_settlement_fees(grain="account"|"option")` 파라미터화
  - Surfaced by: D3 (DRY) + codex #13
  - Files: `backend/app/services/coupang/intelligence.py`
  - Verify: 기존 account 호출부 결과 불변(회귀 테스트) + option grain 신규 유닛
- [ ] **T2 (P1, human: ~2h / CC: ~20min)** — tests — 재사용 밑줄함수 5개 characterization 회귀테스트
  - Surfaced by: D1 + codex #13 (밑줄함수 의미 고정)
  - Files: `backend/tests/test_intelligence_characterization.py`(신규)
  - Verify: `_agg_orders`·`_agg_rg_orders`·`_agg_ads`·`_cost_master`·`_agg_fees` 반환 grain·필터 의미 고정
- [x] **T3 (P1, human: ~1d / CC: ~40min)** — product_pnl.py — Harness 3a 대조원장 + SA-1~5 + 잔차 버킷 ✅ 2026-07-03
  - Surfaced by: D6 + codex #1·#4·#7·#8·#11 (보존법칙·잔차)
  - Files: `backend/app/services/product_pnl.py`(신규, 커밋 d22aa0f)
  - Verify: 보존법칙 유닛 9개(3P·RG·per-vid·net_profit+조정4종·1P·마켓·충돌·전체균형), 전체 486 passed
  - codex review(원칙19): [P1]×3 수용 — ①충돌 vid는 SKU 귀속 안 하고 잔차로(임의선택이 권위
    _cost_master와 어긋남) ②1P vendor=acc["vendor_id"](D-2 회사공유 vendor) 잠금 테스트 ③마켓
    컴포넌트명 product_revenue(권위 revenue=product+shipping 정직 분리). [P2] ignored_1p 이연.
- [ ] **T4 (P1, human: ~3h / CC: ~20min)** — product_pnl.py — RG 옵션수수료 VAT gross-up + rg_vat_residual
  - Surfaced by: D-9 + codex #2·#3 (VAT前/後·RG 의미)
  - Files: `backend/app/services/product_pnl.py`
  - Verify: `Σ(RG옵션귀속)+rg_vat_residual+rg_unmapped == 계정 RG 플립 총액`
- [ ] **T5 (P2, human: ~3h / CC: ~20min)** — product_pnl.py — 날짜기준 명시 + 부분기간 경고 + SA-2 채널별 순수량 원가
  - Surfaced by: D-10 + codex #6·#9·#10
  - Files: `backend/app/services/product_pnl.py`
  - Verify: 채널별 날짜기준 원장 컬럼, `partial_period_settlement` 플래그, 원가=단가×채널별 순수량
- [ ] **T6 (P1, human: ~2h / CC: ~15min)** — routers — `GET /api/products/pnl-reconciliation`(account 필수) + 분리 필드
  - Surfaced by: D-11 + codex #12·#14
  - Files: `backend/app/routers/products.py`, `backend/app/schemas.py`
  - Verify: net_profit_allocated_only·account_adjustment_residual·reconciled_net_profit 분리, account 파라미터 계약
- [ ] **T7 (P1, human: ~2h / CC: ~15min)** — verify — dev DB 라이브 self-verify(원칙22)
  - Surfaced by: 검증 §3
  - Files: (검증 스크립트)
  - Verify: 실제 계정·기간 원장 균형 + 기존 command-center/rocket-overview 총액 정확 대조, 불균형 시 배포 금지

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | issues_found | 15 findings, 15 absorbed into v2 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | clean | 5 issues (D1–D5), all resolved; 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — (UI = S4/S5, out of scope) |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **CODEX:** outside-voice (gpt-5.5) raised 15 gaps the eng review missed. All 15 accepted (11 direct, 3 reinforcing, 1 strategic reframe #15). Plan rewritten v2 as reconciliation-first; D-8~D-11 promoted to track.
- **CROSS-MODEL:** No tension — eng review and codex agree; codex found gaps, not contradictions. #15 (reconciliation-ledger-first) matched the project's own prior RG-fee-accounting precedent.
- **VERDICT:** ENG CLEARED — ready to implement. Reconciliation-first plan v2 with conservation law as the ship gate.

NO UNRESOLVED DECISIONS
