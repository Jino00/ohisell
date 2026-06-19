# PLAN — 판매유형별 쿠팡 총비용을 운영 패널 이익에 반영
> 작성 2026-06-19 (Opus) · plan-eng-review 반영 2026-06-19 · 트랙 D-18 · 상태: 계획(구현 전)

## 1. 목표 (한 줄)
쿠팡 운영 패널(`sales-summary`)의 "수수료"를 flat 7.8%가 아니라 **판매유형별 쿠팡 총비용**으로 재정의해, 이익 = 매출 − 원가 − 쿠팡수수료 − 광고 − 물류비를 정확히 보이게 한다.

## 2. 배경 / 왜 (맥락노트)
- 현재 `sales-summary`는 `fee_rate_map`이 빈 dict라 정산 안 된 전 옵션에 **flat 7.8%(3P 판매수수료, VAT 제외)** 적용. 2P는 쿠팡이 매출의 ~19.5%+(판매수수료+입출고+배송+보관+RG광고)를 가져가는데 판매수수료만 "수수료"로 표시·풀필먼트 일부는 "물류비"에 분산·보관/RG광고 누락 → 2P 이익 과대평가.
- 판매유형별 비용 모델은 D-17(BEP)에서 설계·승인됐으나 **코드 미구현**.
- ★**2P 전액(rg_total)은 종합조망 net_profit에 이미 구현**(`intelligence._agg_rg_settlement_fees`+`apply_rg_net_profit_flip`, D-16). 이 작업은 **그 권위 분해를 재사용**해 운영 패널을 수렴시킨다(새 평행 합산 금지 — plan-eng-review #2/#8).

## 3. 확정 결정 (트랙 D-18, 번복 금지)
- **이익 = 매출 − 원가 − 쿠팡수수료 − 마켓플레이스광고 − 물류비(한진 3P)**
- **쿠팡수수료(3P)** = 판매수수료 + VAT
- **쿠팡수수료(2P)** = 판매수수료+VAT + 풀필먼트(입출고·배송·보관) + RG광고
- **1P** = 범위 밖(주문 기반 패널 미포함, RocketView 별도)
- **한진(3P)** = 별도 물류비 차감(쿠팡 수수료 아님)
- **미정산 2P 보관료·RG광고** = 추정하되 `basis=estimate` 표기(원칙22)

## 4. 라이브 실증으로 잠근 머니룰 (2026-06-19 prod)
- **VAT**: `coupang_revenue_fee.service_fee_ratio`는 VAT 제외율 → 실청구 = 율 × 1.1 (7.8%→8.58%, 10.5%→11.55%; 옵션별 상이). 정산 매칭 시 `service_fee + service_fee_vat` 직접. **VAT는 판매수수료에만** 곱한다 — 풀필먼트·보관·RG광고 정산액은 이미 실청구라 ×1.1 금지(plan-eng-review #C3).
- **2P 비용 구성**: `coupang_rg_settlement_fee.fee_type` = `sale_fee`·`warehousing`·`delivery`·`storage`·`ad_sales`·`return_handling`/`return_shipping`. 정산된 2P는 옵션 단위 합산 가능.
- **2P 판매수수료는 RG정산 `sale_fee`에서만** 취한다. `coupang_revenue_fee`(3P 경로)와 겹치면 이중계상 → 2P는 revenue_fee 경로 배제(plan-eng-review #3).

## 5. 아키텍처 (plan-eng-review 반영 — 얇은 Harness, 권위 재사용)
```
Agent  ── sales-summary 라우터 + 프론트 "쿠팡 비용/물류비/이익" 카드
  └─ Harness  coupang_seller_cost  (판매유형별 쿠팡 총비용 유통 허브 · 원칙18-6/18-7)
        in : 옵션별 {vid, ch_type, revenue, qty, order_ids} + 배치주입(아래)
        out: 옵션별 {seller_cost_total, components{commission,fulfillment,storage,rg_ad}, basis}
        │
        ├─ 3P(Wing): SA commission_vat_resolver
        │     실측 service_fee+vat ▸ 옵션 service_fee_ratio×1.1 ▸ 기본 7.8%×1.1
        └─ 2P(로켓그로스): 기존 권위 분해 재사용 (신규 평행합산 금지)
              정산분  = intelligence 권위 분해(_agg_rg_settlement_fees 계열)에서
                        sale_fee+warehousing+delivery+storage+ad_sales (옵션 그레인)
              미정산분 = commission(sale_fee 율×1.1) + 풀필먼트(_rg_fulfillment_per_unit 입출고+배송)
                        + 보관(★account-level 추정 또는 제외+coverage, per-unit 금지)
                        + RG광고(ad_sales/GMV 닫힌윈도우 비율 × 매출, 미정산만, _agg_rg_ad_overlap 제로체크)
```
- **재사용(원칙18-4)**: 2P 정산 분해 = `intelligence._agg_rg_settlement_fees`/`_rg_account_breakdown`에서 옵션 그레인 분해를 **공유 리더로 추출**해 패널·net_profit이 같은 소스를 읽는다(단일 진실). 3P=`coupang_revenue_fee` 인라인 로직 SA화 + VAT. 풀필먼트 단가=기존 `_rg_fulfillment_per_unit`.
- **배치주입(원칙18-8, N+1 방지 — plan-eng-review #P1)**: settlement/revenue_fee 행을 요청당 1회 group_by 로드해 Harness가 옵션별로 주입. 옵션당 쿼리 금지.
- SA는 순수함수, 다른 SA 출력을 optional 파라미터로 수신.

## 6. Sprint 분해
- **S1 — commission_vat_resolver SA**: 3P/2P 판매수수료+VAT 캐스케이드(실측 service_fee+vat ▸ 옵션 service_fee_ratio×1.1 ▸ 기본 7.8%×1.1). 2P 판매수수료는 RG정산 sale_fee 소스. 순수함수+fixture(3경로 + **옵션이 양 테이블에 있을 때 이중계상 0** 검산).
- **S2 — 2P 비용 권위 재사용 + 미정산 추정**: `_agg_rg_settlement_fees` 분해를 옵션 그레인 공유 리더로 추출. 미정산: 풀필먼트(입출고+배송 per-unit), 보관(account-level/제외+coverage), RG광고(정의된 비율·미정산만·overlap 제로체크). basis 표기. fixture(정산/미정산/보관 account-level).
- **S3 — Harness + 라우터 병행출력(가역성, plan-eng-review #7)**: Harness 분기·합산·basis. `sales-summary`가 **`fee_legacy`(현 7.8%)와 `fee_new`(신모델)를 둘 다 반환** + 구성. 패널/이익은 아직 legacy 사용. shipping 변경 없음. 기존 fixture 회귀 통과.
- **S4 — prod 병행검증 후 컷오버**: 1개 닫힌 기간 prod에서 fee_new vs fee_legacy + 종합조망 대조(아래). 이상 없으면 패널을 fee_new로 전환 + `shipping`에서 풀필먼트 제거(한진만) + 이익 재계산. legacy 경로/`fee_rate_map` 제거.
- **S5 — 프론트**: "수수료"→"쿠팡 비용"(구성 툴팁 commission/fulfillment/storage/rg_ad·basis 배지), "물류비(한진)" 분리 카드, 이익·이익률 재계산.

## 7. 완료 기준 (self-verification)
- fixture: 3P·2P 각 정산/추정 경로 머니테스트(VAT 한 번·이중계상 0·보관 account-level) 통과.
- **정합 = 계정 단위·닫힌 과거 윈도우·정산인식일 기준만**(plan-eng-review #1/#5, D-7): Σ 패널 2P 정산비용(계정) == `_agg_rg_settlement_fees` rg_total(계정). **옵션 단위 수렴은 주장하지 않음**(per-option은 best-effort 배분, basis=estimate). 오늘(order-date·미정산)은 수렴 대상 아님.
- prod 라이브: 2P 옵션 "쿠팡 비용"이 구성요소로 표시·basis 정확, 3P는 판매수수료+VAT(옵션 실측율), 한진 물류비 분리. 신규버그0.
- codex review pass(원칙19, 구현 후).

## 8. NOT in scope (명시 — plan-eng-review)
- 1P 패널 편입(RocketView 소관). 종합조망 net_profit 로직 변경(D-16 그대로). BEP RoAS 풀구현(D-17 별건). **미정산 2P 보관료의 옵션 단위 정밀 배분**(방법론상 부정확 → account-level/coverage로 대체). order-date↔정산인식일 윈도우 통합(닫힌 기간 계정 대조로 한정).

## 9. What already exists (재사용 대상)
- `intelligence._agg_rg_settlement_fees`·`_rg_account_breakdown`·`apply_rg_net_profit_flip` — 2P 정산 분해/전액차감(D-16). **공유 리더로 추출해 재사용**.
- `coupang_ops.actual_fee_by_vid`(3P 실측 service_fee+vat 인라인), `_rg_fulfillment_per_unit`(2P 입출고+배송 단가), `_agg_rg_ad_overlap`(RG광고 제로체크).

## 10. 리스크 / 착수 전 라이브 확인
- **엔진 드리프트(P1)**: 새 평행합산 금지, 권위 분해 재사용 + 계정 단위 닫힌윈도우 대조 테스트.
- **이중계상(P1)**: ① shipping→fee 풀필먼트 이동 시 shipping에서 정확히 1회 제거(병행출력으로 검증). ② 2P 판매수수료 sale_fee 단일 소스. ③ RG광고 정산분(이미 net_profit 차감)과 패널 동시 표시 시 계정 대조로 검증.
- **보관료 추정(P1)**: per-unit-per-option 금지(재고보유시간 비례). account-level 추정 또는 제외+coverage.
- **VAT(P2)**: 판매수수료에만 ×1.1, 정산 실청구액엔 금지.
- 보관 account-level 추정 단가 출처 = S2 착수 전 라이브 확인.

## 11. 체크리스트
- [x] S1 commission_vat_resolver + VAT + 이중계상 fixture (10/10 pass, 2026-06-19)
- [x] S2 권위 분해 공유 리더 추출 + 미정산 추정(보관 account-level·RG광고 비율) + fixture (10/10 pass, 2026-06-19)
- [x] S3 Harness + sales-summary 병행출력(fee_legacy/fee_new) + 배치주입 (2026-06-19)
- [x] S4 prod 병행검증(계정 닫힌윈도우 대조) → 컷오버 + shipping 풀필먼트 제거 + legacy 삭제 (2026-06-19)
- [ ] S5 프론트 쿠팡비용/물류비 카드 + basis 배지
- [ ] codex review(원칙19)

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | deferred | 구현 후 게이트(원칙19) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_folded | 8 findings (4 P1·3 P2·1 P3), 전부 plan 반영 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | S5 프론트 시 선택 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **OUTSIDE VOICE (Claude subagent)**: 8 findings — ①엔진 비수렴(order-date vs recognition_date, P1) ②권위 분해 재사용 안 하면 드리프트(P1) ③2P 판매수수료 양테이블 이중계상(P1) ④보관 per-unit 추정 방법론 오류(P1) ⑤계정 vs 옵션 그레인 → 옵션단위 대조 불가(P2) ⑥RG광고 분모 미정의·정산분 이중계상(P2) ⑦S3 일괄 삭제 무롤백(P2) ⑧3-SA 과설계(P3). **전부 수용·plan 반영**(사용자 결정 위임).
- **CROSS-MODEL:** 리뷰(내부)와 outside voice 모두 "권위 분해 재사용 + 계정단위 정합"으로 일치. 텐션 없음.
- **VERDICT:** ENG REVIEW 반영 완료 — 강화된 plan으로 구현 진행 가능(권위 재사용·얇은 Harness·병행출력 가역성·보관 account-level·VAT 1회). 구현은 /model sonnet S1부터, 이후 codex review.

NO UNRESOLVED DECISIONS
