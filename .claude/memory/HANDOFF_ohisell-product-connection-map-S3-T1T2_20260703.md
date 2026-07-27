# 세션 인수인계: 상품 연관맵 — S3(통합손익) 계획 확정 + T1/T2 구현
> 저장일시: 2026-07-03
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로(이 세션 작업 워크트리): `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/upbeat-lamport-86c720`
- 브랜치: `claude/upbeat-lamport-86c720` (main 기준, S1+S2 이미 반영됨). 커밋 `f4526e4`(T1+T2), **미push**.
- 백엔드 venv: 이 워크트리 전용 `backend/.venv`(python3.11, 이번 세션에 신규 생성 — 메인 레포 venv와 별개). 활성화: `backend/.venv/bin/python`.
- DB(dev): `backend/ohisell.db`

## 2. 이번 세션 완료 목록
- ✅ S1+S2가 이미 main에 머지되어 있음을 확인(PR #1, 이전 세션 HANDOFF는 미머지로 착각한 상태였음 — 실제로는 완료).
- ✅ S3(Harness3 통합 손익 조망) 설계 조사: 리서치 에이전트 2회로 기존 매출/원가/수수료/광고비 엔진 전수 조사(`intelligence.py`·`rocket_intelligence.py`·`profit_calculator.py`).
- ✅ **중요 발견**: 1P(로켓배송) 옵션↔internal_sku 브리지가 두 테이블(`product_channel_mapping` ROCKET행 vs `RocketProductCostMap`)에 각자 존재 — Jino 확인 후 `RocketProductCostMap` 정본 채택(D-7).
- ✅ S3 계획서 v1 작성 → `/plan-eng-review` 실행 → D1~D5 이슈 5건 각각 AskUserQuestion으로 확인.
- ✅ **codex outside-voice(gpt-5.5) 15건** — 계획을 근본 재구성해야 하는 회계 함정 발견(RG VAT前/後 불일치·날짜기준 6종 혼재·부분기간정산 미일할·1P광고 계정레벨 한계·`Order.product_id`≠internal_sku 등). Jino 확인 후 **reconciliation-first**로 전면 재작성(D6).
- ✅ 계획서 v2 완성(`docs/PLAN_product-connection-map-s3.md`) — "SKU 손익표보다 대조원장(보존법칙) 먼저" 구조. GSTACK REVIEW REPORT 포함, Implementation Tasks T1~T7 명시.
- ✅ 트랙 파일에 D-8~D-11 승격 기록(`docs/tracks/active/track_product-connection-map.md`).
- ✅ **T1 구현**: `intelligence.py::_agg_rg_settlement_fees(grain="account"|"option")` 파라미터화. 기존 account 호출부 완전 불변(회귀 4테스트).
- ✅ **T2 구현**: `tests/test_intelligence_characterization.py`(12개) — 재사용할 밑줄함수 5개 의미 고정. `_cost_master`가 internal_sku를 노출하지 않는다는 실동작 발견(T3 설계에 영향).
- ✅ 커밋 `f4526e4`(로컬만, push 안 함).

## 3. 확정된 결정사항 (번복 금지 — 트랙 D-7~D-11)
- **D-7**: 1P 옵션↔internal_sku 브리지 정본 = `RocketProductCostMap`(product_channel_mapping ROCKET행은 조인 미사용, 불일치는 리포트).
- **D-8**: RG 옵션 수수료를 SKU행에 귀속(신규 지표)하되, 원장에서 `Σ(RG옵션귀속)+rg_vat_residual+rg_unmapped == 계정 RG 플립 총액`으로 대조.
- **D-9**: RG 옵션행=VAT前(A−B), 계정행=VAT後 — SKU 귀속 시 gross-up, 잔차는 `rg_vat_residual` 버킷.
- **D-10**: 채널별 날짜 기준(3P주문일/3P수수료인식일/RG paid_at/RG정산기간중첩/1P발주일/1P광고리포트일)을 원장에 명시. 부분기간 RG정산은 `partial_period_settlement` 경고(일할 배분 안 함).
- **D-11**: 엔드포인트 `account` 파라미터 필수(계정 인식 엔진과 정합).
- **엔지니어링 리뷰 D1~D6**: D1(밑줄함수 그대로 import) · D2(coverage 플래그로 공백 표면화) · D3(grain 파라미터로 DRY) · D4(라인헬퍼만 재사용, calculate_product_profit 전체 재사용 안 함) · D5(교차검증=잔차분해, tolerance 아님) · D6(대조원장 우선 재구성).
- **보존 법칙(이 트랙의 핵심 정의)**: 채널·컴포넌트마다 `Σ(SKU행 귀속분) + Σ(잔차 버킷) == 기존 권위 엔진의 계정레벨 소계`가 **정확히** 성립해야 원장 통과. 성립 안 하면 조인/기준 버그.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_product-connection-map.md` | ★단일 진실 원천(트랙), D-1~D-11 |
| `docs/PLAN_product-connection-map-s3.md` | S3 계획서 v2(reconciliation-first) + GSTACK REVIEW REPORT + T1~T7 |
| `backend/app/services/coupang/intelligence.py` | 재사용 대상 밑줄함수 5개 + `_agg_rg_settlement_fees`(T1로 grain 파라미터 추가됨) |
| `backend/tests/test_intelligence_characterization.py` | T2 신규 — 재사용 함수 의미 고정 |
| `backend/tests/test_rg_settlement_sync.py` | T1 신규 테스트 4개 추가된 곳 |
| `backend/app/services/coupang/rocket_intelligence.py` | 1P 엔진(PO grain), `CoupangRocketPurchaseOrderItem`+`RocketProductCostMap` |
| `backend/app/services/profit_calculator.py` | 네이버/cafe24 라인헬퍼(`_line_revenue`·`_line_commission`) — D4로 이것만 재사용 |
| (T3 신규 예정) `backend/app/services/product_pnl.py` | Harness 3a(대조원장)+3b(SKU행) — 아직 미작성 |

## 5. 알려진 이슈 / 주의사항
- 이 워크트리는 `.venv`가 없었음 — python3.11로 새로 만듦(`backend/.venv`). 다른 워크트리로 옮기면 다시 만들어야 함.
- `_cost_master`는 `{cost_price, name}`만 반환, internal_sku 없음 — T3에서 internal_sku 그룹핑은 `product_channel_mapping`을 직접 조회해야 함(계획에 이미 반영).
- codex 15건 중 8번(네이버/cafe24 광고=Meta/네이버SA/GFA가 `profit_calculator`에서 계정레벨로 이미 배분됨)은 계획서 §3 SA-4에 "원장 `account_adjustments`에 포함" 방식으로 반영됨 — T3 구현 시 반드시 이 계정레벨 광고비를 원장에 넣을 것(빠뜨리면 codex #8 재발).
- 원칙22: T3~T7 각 완료 시 dev DB 라이브 self-verify 필수. **불균형(보존법칙 위반) 발견 시 배포 금지.**

## 6. 다음에 할 작업 (미완료)
- [ ] **T3(다음, 최대 태스크)**: `product_pnl.py` 신규 — Harness 3a 대조원장(SA-1 매출·SA-2 원가·SA-3 수수료·SA-4 광고비·SA-5 대조, 잔차 버킷 5종, 보존법칙 검증) → 3b SKU행(원장 균형 후에만). 계획서 §2~3 상세 참고.
- [ ] T4: RG 옵션수수료 VAT gross-up + `rg_vat_residual` 버킷(D-9).
- [ ] T5: 날짜기준 명시 컬럼 + `partial_period_settlement` 플래그 + SA-2 채널별 순수량 원가(D-10).
- [ ] T6: `GET /api/products/pnl-reconciliation`(account 필수) + `net_profit_allocated_only`·`account_adjustment_residual`·`reconciled_net_profit` 분리 필드(D-11).
- [ ] T7: dev DB 라이브 self-verify — 실제 계정·기간 원장 균형, 기존 command-center/rocket-overview 총액과 정확 대조.
- [ ] T3~T7 완료 후 `/codex review`(보존법칙·VAT gross-up·날짜기준 정합 집중).
- [ ] 브랜치 push + PR 생성(Jino 결정 대기, 지금까지 로컬 커밋만).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-product-connection-map-S3-T1T2_20260703.md 읽고 T3부터 이어서 작업해줘
```
