# PLAN — S7: RG 정산 비용 net_profit 플립 (계정 단위)

> 트랙: docs/tracks/active/track_coupang-rg-fee-accounting.md (7/8)
> 작성: 2026-06-09 (Opus 계획) · 머니코드 — 코딩 전 plan-eng-review + codex + Jino 승인 필수
> 결정 근거: D-6(reconciliation-first), D-10(basis), D-11(광고비 dedup), D-14(계정 단위 차감)

## 1. 배경 / Why

Phase 1(S1~S6)에서 RG 정산 비용을 수집·검산·대조뷰로 가시화했으나 **net_profit엔 미반영**(D-6).
종합조망 헤드라인 순이익(`compute_command_center.account_sum.net_profit`)은 여전히 RG 풀필먼트·판매수수료를 빠뜨린 과대값이다. S7은 이 비용을 **계정 단위로 순이익에서 실제 차감**해 플립한다(Phase 2).

**코드로 확인한 현재 사실:**
- `intelligence.py:448` 옵션별 `net_profit = revenue − return_deduction − total_fee − ad_spend − cost`. `account_sum.net_profit = Σ(옵션별)` (`:494`).
- RG 옵션은 Open API revenue-history에 없어 `total_fee=0` → RG 판매수수료도 현재 순이익에 0 (D-9).
- `_agg_ads`(`:99`)는 **sell_type 필터 없음** → net_profit의 `ad_spend`에 2P(RG) 광고비가 이미 포함됨 → RG정산 차감 시 이중계상 위험(D-11 dedup 필수).
- RG 정산 비용은 `_agg_rg_settlement_fees`(`:209`)가 **계정 단위(vendor_item_id='', VAT後 실청구)**로 전 기간 집계 완료(prod 196행). 옵션 단위는 희소(8행) → D-14로 계정 단위 채택.

## 2. 확정 모델 (머니코드, D-14 + D-15 ★Codex 교차검증 개정)

차감은 **summary(account_sum) 레벨**에서만. by_option(옵션별 net_profit)은 운영지표로 불변.
계정 RG 조정은 명시적 브리지 필드로 감사가능하게 노출한다.

```
net_profit_pre_rg      = Σ(옵션별 net_profit)               # 기존값(대조 기준선, XLSX 2P 광고비 이미 포함)
rg_ad_settlement       = Σ_account RG정산 ad_sales(광고)     # ★표시만, 차감 안 함(D-15)
rg_non_ad_deducted     = rg_total − rg_ad_settlement         # 판매수수료+풀필먼트+반품 (광고 제외)

net_profit(플립후) = net_profit_pre_rg − rg_non_ad_deducted
```

**★D-15 핵심(Codex 교차검증, Claude·Jino 합의):** RG 광고비는 **이미 net_profit_pre_rg 안에 광고XLSX 2P로 들어있음**(정본 유지). settlement에서는 **광고 제외 비용만** 차감 → 이중계상 원천 차단(광고는 XLSX 2P로 1회만, settlement ad_sales 미차감). **add-back·D1게이트·basis매칭 전부 불필요**. 폐기된 구안(add-back 후 settlement total 차감)은 rg_total(겹침)과 XLSX 2P(report_date)의 basis 불일치로 부분윈도우·음수환급에서 깨졌음.

**비중복 가정(검증 대상, §5):**
- RG정산 `sale_fee` ↔ 순이익 `total_fee`: 비겹침. RG 매출은 revenue-history 부재로 total_fee=0(RG옵션), 3P(윙) total_fee는 RG정산에 없음 → 두 스트림 disjoint.
- RG정산 `return_shipping/return_handling`(회수·반출비) ↔ 순이익 `return_deduction`(반품 매출역산 추정): 다른 개념(회수 처리비 vs 매출 환입) → 비겹침.
- RG 광고비: **non-ad 차감으로 광고는 settlement에서 빠지므로 이중계상 불가**(XLSX 2P 1회). rg_ad_settlement는 표시·검산용(XLSX 2P와 자릿수 대조).

## 3. 구현 단계 (Sub-Agent → Harness → Agent, 최소 변경)

**모두 `compute_command_center`(intelligence.py) 내 변경 + 순수함수 1개 추출. 신규 테이블·마이그레이션 없음.**

- **S7-1. 순수 차감 함수(D-12 테스트 대상)** — `intelligence.py`에 추가:
  ```python
  def apply_rg_net_profit_flip(net_profit_pre_rg, rg_non_ad_deducted) -> Decimal:
      # D-15: 광고 제외 RG 비용만 차감. 광고는 net_profit_pre_rg의 XLSX 2P가 정본(미차감).
      return net_profit_pre_rg - rg_non_ad_deducted
  ```
  DB 없이 fixture 테스트 가능. 부호·0·음수(환급) 케이스 커버. **add-back 인자 없음**(D-15).
- **S7-2. account_sum 플립 적용** — `compute_command_center`(`:486~499` account_sum 직후, rg_settlement 섹션은 이미 `:520`/`:523`에 `rg_total`·`rg_ad_settlement` 계산됨 → 재사용):
  ```python
  # D-15: 광고 제외 RG 비용만 차감(rg_total − rg_ad_settlement). 음수환급·부분윈도우에
  # 견고(basis 매칭 불필요). 광고 dedup은 settlement ad_sales 미차감으로 자동 성립.
  rg_non_ad_deducted = rg_total - rg_ad_settlement
  account_sum["net_profit_pre_rg"]    = account_sum["net_profit"]    # 기존(대조, XLSX 2P 광고비 포함)
  account_sum["rg_settlement_total"]  = rg_total                     # 계정 RG총액(VAT後, 표시)
  account_sum["rg_ad_settlement"]     = rg_ad_settlement             # RG정산 광고(표시·검산, 미차감)
  account_sum["rg_non_ad_deducted"]   = rg_non_ad_deducted          # ★실제 차감액(광고 제외)
  account_sum["net_profit"] = apply_rg_net_profit_flip(
      account_sum["net_profit"], rg_non_ad_deducted)
  account_sum["rg_flip_status"] = "applied_non_ad" if len(rg_fees) > 0 else "not_applied_no_data"
  ```
  ※ `rg_total`/`rg_ad_settlement` 계산(`:520`,`:523`)을 account_sum 블록 위로 이동하거나 플립을 rg_settlement 계산 뒤로 재배치. 순서만 변경.
  ★ **D-15(Codex 교차검증 확정)**: add-back·D1게이트 제거. RG 데이터 없으면 `rg_non_ad_deducted` 자연히 0(rg_total=0) + status=`not_applied_no_data`. **음수 환급주기도 그대로 정확**(rg_total<0이면 순이익에 가산=환급 반영).
- **S7-3. rg_settlement 섹션 note 갱신** — `:529` "미반영(D-6)" → "반영됨(Phase2/S7, 계정 단위 non-ad, D-14/D-15)". summary에 `flip_status` 추가. by_account 대조뷰 유지(드릴다운). 광고는 미반영(표시만)임을 note에 명시.
- **S7-4. 프론트(RgSettlementCard + 순이익 표시)** — "RG 정산 비용(미반영)" → "(반영됨 — 광고 제외)" 문구. 순이익 카드에 브리지 표시: `순이익 = net_profit_pre_rg − rg_non_ad_deducted`. "광고비는 광고리포트(2P)로 이미 반영, 정산 광고는 표시만(D-15)" + "정산주기 기준" 명시. 헤드라인 net_profit이 RG 비용만큼 감소함을 사용자에게 표기.
- **S7-5. 광고 정합 자릿수 검증(코딩 후 prod)** — `rg_ad_settlement`(표시) vs XLSX 2P(`_agg_rg_ad_overlap`) 자릿수 대조. 차감엔 영향 없으나 큰 괴리는 데이터 이슈 신호 → 가시화. (구안의 add-back basis 검증은 D-15로 불필요해짐.)

## 4. 검산 / 테스트

- **fixture(D-12, committed)** — 신규 `test_intelligence_rg_flip.py` (D-15 + Codex 추가 케이스):
  - `apply_rg_net_profit_flip` 순수: `pre_rg − rg_non_ad_deducted` 양수/0/음수.
  - `compute_command_center` 통합(인메모리 DB): RG 계정 row만 → net_profit == `pre_rg − (rg_total − rg_ad_settlement)`.
  - **[Codex t1] 부분윈도우 견고**: 주간 정산주기에 1일 윈도우 → non-ad 차감은 광고 basis 함정 없음(구안과 달리 안전). 정산주기 통째 차감은 D2대로 동작(고정).
  - **[Codex t2] rg_total==0 + ad_sales>0 + 음수 other**: non-ad 차감 정확(광고 미차감이라 과대표시 없음).
  - **[Codex t3] rg_total<0 환급주기**: 순이익에 가산(환급 반영) — 정확.
  - **[Codex t4] ad_sales==0 + XLSX 2P 존재**: 광고는 XLSX 2P로 net_profit에 그대로(차감/되돌림 둘 다 없음) — 실광고비 보존.
  - **[Codex t5] 정산정렬 윈도우에서 ad_sales≠XLSX 2P**: non-ad 차감은 영향 없음(광고 미차감) — basis 차이 무해 증명.
  - **[D3] 브리지 검산**: `net_profit_pre_rg − rg_non_ad_deducted == net_profit`, summary 필드(pre_rg·total·ad_settlement·non_ad·status) 존재.
  - **[회귀 가드 CRITICAL] RG 데이터 0이면 net_profit 불변**(IRON RULE — 기존 동작 보존).
  - **비중복 가정**: total_fee(3P)·return_deduction이 RG 차감과 독립임을 합성 데이터로 확인.

**커버리지 다이어그램:**
```
CODE PATHS                                          상태
[+] intelligence.apply_rg_net_profit_flip()         순수함수(인자 2개)
  └── 양수 / 0 / 음수(환급) non-ad                   [GAP→테스트]
[+] compute_command_center() account_sum 플립(D-15)
  ├── RG>0 정상 non-ad 차감                          [GAP→테스트]
  ├── [t1] 부분윈도우 견고                           [GAP→테스트]
  ├── [t2] rg_total=0 + ad>0 + other<0              [GAP→테스트] ★과대표시 가드
  ├── [t3] rg_total<0 환급                           [GAP→테스트]
  ├── [t4] ad_sales=0 + XLSX 2P 보존                 [GAP→테스트]
  ├── [t5] 정렬윈도우 ad_sales≠2P 무해               [GAP→테스트]
  ├── [D3] 브리지 5필드 + 등식                        [GAP→테스트]
  └── [회귀] RG 데이터 0 → 불변                       [GAP→CRITICAL]
COVERAGE 목표: 9/9 (신규 코드 100%, 머니코드 D-12)
```
- **prod 라이브 self-verify(원칙22)** — 배포 후:
  - 플립 전후 net_profit 차이 == `rg_settlement_deducted − rg_ad_dedup_addback` (브리지 일치).
  - `rg_settlement_deducted` == 대조뷰 `rg_total`(기존 검증값)와 동일.
  - 광고비: `rg_ad_dedup_addback` 크기가 RG정산 ad_sales와 동일 자릿수인지(basis 정합 신호).
  - 헤드라인 net_profit이 RG비용만큼 정확히 감소(과대 → 정상).
- **codex review** — 플립 diff 독립 검토(원칙19, pass 필요).

## 5. 리스크 / 미해결 (eng-review + Codex 교차검증 반영)

- **D-15 광고 basis 리스크 = 제거됨**: non-ad 차감이라 광고는 settlement에서 안 빠지고 XLSX 2P(report_date)로만 1회 반영 → add-back/replacement의 basis 불일치 함정 자체가 사라짐. (폐기된 구안의 최대 약점 해소.)
- **D2 정산주기 통째 차감(확정·명시)**: 광고 제외 RG 비용(판매수수료·풀필먼트·반품)은 정산주기 단위 저장 → 윈도우가 주기를 부분만 걸쳐도 **주기 전액 차감**(Phase1과 동일 동작, 회귀 없음). **월/주 경계 정렬 조회 권장**. 비례배분 안 함(RG가 주기 회계). UI note·계획 명시. (Codex #4: 비정렬 윈도우에선 "진짜 순이익"이 아니라 하이브리드 → UI 문구에서 "정산주기 기준" 명시.)
- **D3 summary≠옵션합(확정·표현)**: 계정 단위 차감이라 `Σ(by_option) ≠ account_sum.net_profit`(설계상, D-14). `net_profit_pre_rg`·`rg_settlement_total`·`rg_ad_settlement`·`rg_non_ad_deducted`·`rg_flip_status` **브리지 필드를 summary에 노출** → 라이브 검산·감사가능. by_option은 운영지표로 불변. 옵션 가짜행 주입 안 함. 프론트 문구 "계정 RG조정=summary 레벨" 명시(S7-4).
- **rg_flip_status enum(Codex #6)**: 불리언 대신 `not_applied_no_data`/`applied_non_ad`로 money basis 명시.
- **other(미매핑 fee_type)**: `_rg_account_breakdown` other가 0 아니면 non-ad 차감(rg_total−ad_sales)에 포함됨(보수적·정확). 정상=0(S6 검증). other에 광고가 섞일 일은 없음(ad_sales 별도 분리).
- **가역성**: `rg_flip_status`·`net_profit_pre_rg` 보존으로 롤백/대조 용이.
- **광고 정합 검산(S7-5)**: `rg_ad_settlement`(표시) vs XLSX 2P 자릿수 대조 — 큰 괴리는 데이터 이슈 신호(차감엔 영향 없지만 가시화).

### NOT in scope (eng-review)
- **D4 크로스채널 대시보드 RG 반영**: `dashboard.py`/`profit_calculator.py`의 쿠팡 순이익은 S7에서 RG 미반영 유지 → command-center와 화면 간 차이 발생. profit_calculator 구조 재조사 필요해 머니코드 리스크 → **TODO 분리**(아래). 두 화면은 이미 방법론 차이(옵션단위 실측수수료 vs 정산) 있음.
- **모델(A) 과오청구 감사(D-4)**: S8로 분리(D-14).
- **옵션 단위 net_profit 귀속**: D-14로 계정 단위 채택, 옵션 데이터 희소(S6-auto 미가동).

### What already exists (재사용)
- `_agg_rg_settlement_fees`(`:209`)·`rg_total`(`:520`): 계정 RG총액(VAT後) — 차감액 그대로 재사용.
- `_agg_rg_ad_overlap`(`:301`)·`rg_ad_xlsx_overlap`(`:524`): 2P add-back 금액 — 그대로 재사용(D1 게이트만 추가).
- `rg_settlement` 대조 섹션(`:525`): note 문구만 갱신, 구조 유지.
- **신규**: 순수함수 `apply_rg_net_profit_flip` 1개 + account_sum 6줄 + 프론트 문구. 신규 테이블/마이그레이션 없음.

### Failure modes
| 코드패스 | 실패 시나리오 | 테스트 | 에러처리 | 사용자 가시성 |
|---|---|---|---|---|
| non-ad 차감 | RG정산 stale → 미차감 | ✅ 회귀 가드 | rg_total=0 → 차감 0, status=not_applied_no_data | flip_status 노출 |
| 음수 환급주기 | rg_total<0 부호 오류 | ✅ fixture(t3) | 부호 그대로 가산 | 환급 반영(정확) |
| 정산주기 통째 | 부분윈도우 과다차감 | ✅ fixture(t1) | 설계상 수용(광고 basis 함정 없음) | UI note "정산주기 기준" |
| summary≠옵션합 | 프론트 재계산 불일치 | ✅ fixture(D3) | 5개 브리지 필드 | pre_rg로 검증가능 |
| RG 데이터 0 | 회귀(net_profit 변동) | ✅ 회귀 가드(CRITICAL) | 플립 no-op | 불변 |

## 6. 완료 기준

- [ ] 순수함수(non-ad) + account_sum 플립(5 브리지필드+status) + note/프론트 갱신
- [ ] fixture 테스트 9/9(머니코드, Codex t1~t5 + 회귀 가드 CRITICAL) PASS
- [ ] codex review pass(구현 diff, 원칙19)
- [ ] prod 배포 + 라이브 self-verify(브리지 등식·net_profit 정확 감소·광고 자릿수, 원칙22)
- [ ] 트랙/progress/MEMORY 갱신, failures.jsonl(이슈 시)

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | issues_found | 6 findings + simpler approach; non-ad 차감 채택(D-15) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | clean | 5 issues (D1~D4 + 테스트), 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **CODEX:** 핵심 발견 — 구안(add-back 후 settlement total 차감)은 rg_total(겹침 basis) vs XLSX 2P(report_date basis) 불일치로 부분윈도우·음수환급에서 깨짐. `rg_total>0` 게이트도 음수 환급행 때문에 오류. 더 단순·안전한 **non-ad 차감**(`pre_rg − (rg_total − rg_ad_settlement)`) 제안 → 채택(D-15). enum status·테스트 5종 추가 수용.
- **CROSS-MODEL:** Eng-review는 add-back 게이트(D1)까지 → Codex가 한발 더: add-back 자체를 제거하는 non-ad 차감. Claude 동의(머니코드 단순=정확), D-11이 잠근 결정이라 Jino에 텐션 위임 → **Jino가 B(non-ad 전환, D-11 개정) 채택**. 합의 도달, 미해결 0.
- **UNRESOLVED:** 0 (D1~D5 전부 결정, D-14/D-15 트랙 기록).
- **VERDICT:** ENG + CODEX CLEARED — D-15(non-ad 차감)로 계획 확정, 구현 착수 가능. 머니코드라 구현 후 fixture 9/9 + codex review + prod self-verify 필수.
