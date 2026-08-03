# 트랙: 쿠팡 로켓그로스(RG) 수수료 회계 자동화

> 시작일: 2026-06-08 · 상태: 🟢 Active (완료 처리는 아래 상태 블록 참조)
> 단일 진실 원천. 이 트랙을 무시·변형해서 진행하지 말 것. 변경은 Jino 승인 후 D-N으로 기록.
> 상세 정책·라이브 실증: docs/references/17_coupang_rg_fulfillment_fee_policy.md

> **상태: Completed (2026-08-03)** — Jino 승인.
> 미결이던 size_mismatch_high 1건은 "자동 판가름 대기"가 종료됐다. 실측값 확보(극소형) + 판정 기구
> 정상화(PR #183) 후 결과는 **불일치 확인**: 실측 극소형 vs 청구 함의 대형1, 주문당 4,050원
> (극소형 최소 1,350원의 3.0배). 감사 목록 1위로 상시 노출되며 `measured_vs_billed_mismatch`
> 플래그로 뜬다. 후속(정산서 대조)은 운영 판단이라 chip `task_7b887d9f`로 분리.

## 목표 (한 줄)
오픽스·오하이테크 로켓그로스(RG) 판매에 쿠팡이 실제 청구한 **모든 수수료(판매수수료·입출고비·배송비·보관비·반품/반출비 등)를 옵션 단위로 자동 수집**해 종합조망 순이익에 반영하고, 향후 모든 RG 상품·판매에 자동 적용한다.

## 배경 문제 (왜)
- 종합조망 순이익은 `intelligence._agg_fees`에서 **판매수수료+VAT(`total_fee`)만** 차감 → RG 풀필먼트 비용 100% 누락.
- 실증: 오픽스 RG 옵션 31개 ∩ revenue_fee(수수료 소스) 옵션 = 공집합. RG 매출은 Open API revenue-history에 안 잡힘.
- RG 수수료는 **Open API 어디에도 항목별로 없음**(settlement-histories deduction=Wing 3P 통짜; RG 상품 API=치수만). 윙 판매자센터 **로켓그로스 정산현황**에만 있음(별도 스트림).

## 확정 결정사항 (D-N)
- **D-1 (수집 소스 = 윙 내부 API)**: RG 수수료는 윙 판매자센터 로켓그로스 정산현황 내부 API로 수집한다. 인증=기존 RG 입고(inbound)에서 만든 `CoupangWingCookie`(Fernet 암호화 세션쿠키) 재사용. Open API엔 RG 수수료가 없음을 라이브로 확정(레퍼런스 17 §4·§7).
  - API: `POST /tenants/rfm/v2/settlements/status/api`(정산주기별 리포트), `POST /tenants/rfm/v2/settlements/profit-status/search`(기간 요약), `POST /tenants/rfm/v2/settlements/download-list/api`(종류별 엑셀 생성목록).
- **D-2 (입자도 = 옵션 단위 1순위)**: 가장 디테일·정확하게 — 정산 **종류별 리포트(입출고·배송비/판매수수료 등 8종)**의 주문/SKU 단위 상세를 **vendor_item_id(옵션) 단위로 적재**(D-8 결합축 일치). 주별 총액(status/api)은 검산 기준선. Σ(옵션별)==주별총액 ±0 검증(원칙22).
  - 단 보관비·반품·반출비는 판매가 아닌 재고/이벤트 단위 → 옵션 귀속하되 "발생 기준" 적재.
- **D-3 (광고비 이중계상 방지)**: RG정산의 광고비(d)는 쿠팡 실청구액 → **RG 광고비는 RG정산을 정본**으로, 기존 `ad_costs`의 RG분과 겹치지 않게 출처 정합(겹침 구간 점검).
- **D-4 (모델은 보조)**: 치수·무게 기반 사이즈등급 모델(A)은 1순위 아님 → **사전예측·과오청구 교차검증(D-13 감사의 RG 확장)** 보조용. 실청구 수집(B)이 핵심.
- **D-5 (시스템은 사실만)**: 트랙 전체 D-3 정신 — 수집·검산·표기만. 전략 판단은 Jino.
- **D-6 (reconciliation-first — plan-eng-review D1 + Codex #10, 개정 2026-06-08)**: net_profit을 바로 건드리지 않는다. Phase 1 = RG 정산을 수집해 **별도 대조(reconciliation) 뷰**로 "현재 순이익에서 빠진 RG 비용 = OO원"을 순이익 **옆에** 표시(가시화로 목표 달성). 권위적 net_profit 차감은 회계 규칙(basis·판매수수료·dedup·테스트) 전부 잠근 뒤 Phase 2에서 플립. 사유: 머니코드 가역성·account↔option 불일치 회피. Jino 선택(D3): "A) 대조 라인 먼저". (구 D-6 "계정 단위 즉시 net_profit 차감"은 폐기.)
- **D-7 (대조 뷰 표현 — 개정 2026-06-08)**: RG 비용은 `account_sum` 차감이 아니라 **독립 대조 지표**(예: command-center에 'RG 정산 비용(미반영)' 별도 섹션/카드, account_key별 주별). net_profit 숫자는 Phase 1에서 불변. Phase 2 플립 시 by_option 귀속(엑셀)과 함께 net_profit 반영. (구 D-7 "account_sum 한 줄 즉시 차감"은 D-6 개정으로 대조 뷰로 변경.)
- **D-8 (parser는 Harness에 흡수)**: 별도 parser SA 만들지 않음 — 파싱은 rg_settlement_sync Harness 책임(기존 rg_inbound_sync 패턴 동일). 신규 코드유닛 = Client SA + Harness + 모델.
- **D-9 (RG 판매수수료 + 풀필먼트 둘 다 — Codex #3, 확정 2026-06-08)**: RG 매출은 revenue-history에 없어 현재 total_fee가 RG 판매수수료(B)도 못 잡음. 따라서 수집·대조는 RG **판매수수료(B) + 쿠팡풀필먼트비용(J) 둘 다** 포함(풀필먼트만 아님). "RG 풀필먼트 비용"이라는 명명은 "RG 정산 비용(판매수수료+풀필먼트)"로 정정.
- **D-10 (회계 basis 잠금 — Codex #4·#5, 확정 2026-06-08)**: 날짜 기준 = **매출인식일**(기존 revenue 인식 기준과 정합). 비용 인식 = **발생비용(f, 해당 인식기간 발생분)** — `f-g`(이월 조정 후)·최종지급액 아님. status/api 조회도 매출인식일 기준. (Phase 2 플립 전 재확인 가능.)
- **D-11 (광고비 dedup 규칙 — Codex #6, 확정 2026-06-08)**: 명시 규칙 필요(손짓 금지). RG 광고비 = RG정산(d) 정본. 기존 `ad_costs`의 RG 계정·해당 기간 광고비는 **대조 단계에선 표시만**(이중계상은 net_profit 플립 시점에 차단). 키=account_key+날짜(+가능시 campaign/option), 출처 우선순위·제외/대체 규칙은 S3에서 코드로 못박음.
- **D-12 (머니코드 fixture 테스트 — Codex #8 부분수용, 확정 2026-06-08)**: 프로젝트 라이브 self-verify 컨벤션의 **예외** — RG 정산은 머니코드라 파싱·부호(취소/환급 음수)·집계·dedup·날짜필터에 대해 **fixture 기반 committed 테스트**를 둔다(라이브 self-verify와 병행).
- **D-13 (Wing API 방어성 — Codex #7)**: 미문서 내부 API라 스키마 드리프트 대비 — 방어적 파싱(키 부재·타입 변화 fail-soft) + 응답 형태 변화 감지. ToS/세션 리스크는 inbound(D-1/D-5)에서 이미 수용한 전제 계승.
- **D-15 (S7 광고비 = non-ad 차감, D-11 플립 메커니즘 개정 — 확정 2026-06-09, Codex 교차검증→Jino 결정)**: net_profit 플립은 **RG정산의 광고 제외 비용만 차감**한다. 공식: `net_profit_new = net_profit_pre_rg − (rg_total − rg_ad_settlement)`. 즉 RG 광고비는 **이미 net_profit에 든 광고XLSX 2P를 정본으로 유지**하고, settlement ad_sales는 **차감 안 함**(표시만). 사유(Codex 지적, Claude·Jino 동의): ① add-back/replacement은 rg_total(겹침 basis)과 XLSX 2P(report_date basis)의 **basis 불일치로 부분윈도우·음수환급에서 깨짐**(`rg_total>0` 게이트도 음수행 때문에 오류). ② non-ad 차감은 **이중계상을 원천 차단**(광고는 XLSX 2P로 1회만)하고 basis 함정 자체가 없음. ③ XLSX 2P는 report_date라 net_profit 나머지(광고도 report_date)와 **오히려 더 정합**. **D-11 개정**: "RG 광고비 정본 = settlement ad_sales"(구) → **"정본 = 광고XLSX 2P(이미 net_profit 반영), settlement ad_sales는 표시·검산용"**(신). **부수 결정**: add-back·D1게이트(rg_total>0)·basis매칭 전부 제거. `rg_flip_applied` 불리언 → `rg_flip_status` enum(not_applied_no_data/applied_non_ad)으로 money basis 명시(Codex #6). 단순=정확(머니코드 원칙).
- **D-16 (S7 광고비 = 전액 차감, D-15 개정 — 확정 2026-06-09, /browse 라이브 조사→Jino 결정)**: net_profit 플립은 **RG 정산 총액(rg_total)을 전액 차감**한다(광고 포함). 공식: `net_profit_new = net_profit_pre_rg − rg_total`. **사유(라이브 조사, 원칙22)**: 업로드되는 광고 XLSX(`pa_daily`)의 출처는 **쿠팡 광고센터(advertising.coupang.com `/marketing-reporting/billboard/reports/pa`) = 마켓플레이스(3P/윙) 검색·디스플레이 광고**이고, 판매방식(3P/2P/Retail)은 출력 컬럼이다. **RG 광고비(totalAdSalesDeductionAmount, 윙1 80,754)는 이 PA 보고서에 안 잡히고 RG 정산에만 존재** — prod 전기간·양 테이블(coupang_ad_option_daily·coupang_ad_report) **2P 0행** 실증. 즉 D-15의 전제("RG 광고=XLSX 2P가 정본, 이미 net_profit에 있음")가 **성립 안 함** → RG 광고가 net_profit에서 누락. **전액 차감의 정당성**: ① RG 광고는 정산에만 있는 실비용 → 차감해야 반영 ② prod 2P=0이라 이중계상 없음 ③ rg_total 전부 정산 basis라 **Codex가 지적한 basis 불일치(XLSX report_date↔정산 recognition_date) 자체가 사라짐** — 공식이 D-15보다 더 단순. **D-15 폐기**(non-ad 차감). `rg_flip_status` enum: `applied_full`/`not_applied_no_data`. **잔존 리스크(수용)**: 미래에 광고센터에서 RG상품 검색광고를 돌려 PA 2P>0이 되고 그게 정산 ad_sales와 같은 돈이면 이중계상 가능 → 그때 재검토(현재 2P=0, 별개 광고상품으로 추정). **Jino 원문**: "전액 차감으로 개정".
- **D-14 (S7 net_profit 플립 입자도 = 계정 단위 — 확정 2026-06-09, Jino 결정)**: net_profit 차감은 **계정 단위 RG 총액**(status/api, VAT後 실청구, 전 기간 완비) 기준으로 한다. 옵션 단위(엑셀, VAT前, 현재 prod 8행뿐) 귀속은 net_profit 권위 소스 아님 — 종합조망 대조뷰/드릴다운 **표시용**으로만 유지(엑셀 채워지는 대로). 사유: 옵션 데이터가 희소(S6-auto 미가동)해 옵션 차감은 대부분 옵션을 RG비용 0으로 잘못 표기. 계정 단위는 지금 정확하고 S6-auto에 비의존. **account_sum.net_profit에 'RG 정산 비용' 한 줄 차감 + 명시적 브리지 필드**(rg_settlement_deducted, rg_ad_dedup_addback)로 감사가능하게. by_option net_profit은 옵션 운영지표로 유지(계정 RG조정은 summary 레벨 표기). **모델(A) 과오청구 감사(D-4)는 S7에서 제외 → 별도 S8로 분리**(머니코드 변경 리스크 최소화, Jino 결정).
- **D-17 (S8 감사 = 사이즈 분류 + 이상치 스크리닝, 정확금액 복제 안 함 — 확정 2026-06-09, Jino 승인)**: S8 과오청구 감사는 쿠팡 정확 수수료 계산기를 **복제하지 않는다**(프로모션·저가할인·합포장 재산정·카테고리별 규칙으로 fragile 머니코드, 오탐 다수 → D-4 "모델은 보조용"·D-5 "사실만, 판단은 Jino" 위배). 대신 **① 사이즈 유형 분류(결정적, 공식표 §7) + ② 이상치 플래깅(스크리닝)**. **핵심 신호**: 쿠팡 최종 청구 사이즈=물류센터 입고측정값이라 우리 등록 치수와 다를 수 있음 → 우리 치수→예상 사이즈 유형 vs 실청구 금액 정합성 대조 → 불일치를 **사람 검토용 플래그**(definitive 과오청구 판정 아님). **읽기 전용, net_profit 불변.** 구조: SA1 `classify_size_type`(세변합∪무게→상위채택, fixture 테스트 D-12) + SA2 `expected_fee_floor`(공식 최소금액, "최소치·상한단정 금지" 라벨) + SA3 `detect_fee_anomalies`(per-unit 정규화 위해 rg_order_item 수량 조인; 플래그 missing_dims/below_floor/size_mismatch) → Harness `rg_fee_audit` → 라우터 `GET /api/coupang/ops/rg/fee-audit`(표시 전용). 합포장으로 주별집계÷수량≠깔끔한 단가 → **스크리닝 도구**(확정 계산기 아님). 출처: Wing fee-details 라이브(레퍼런스 17 §7). **Jino 원문**: 구조 도표 제시 후 "그래"(승인).

## 사용자 원문 인용 (왜곡 방지)
- "그래, 이걸 모두 프로그래밍화해서 향후에도 모두 자동적용될 수 있도록 해야한다"
- "open api에서 나오는 정산 부분이랑 너가 찾은 수수료가 매칭이 되는지는 확인을 해야지"
- (입자도) "내가 확인 필요 항목에서 가장 디테일하게 정확한 자동화방법이 뭐야?" → 옵션 단위 실청구 수집으로 답·승인.

## 구조 (승인됨 2026-06-08, D-6/D-7/D-8 반영)
```
[Agent] RG 수수료 회계 (종합조망 순이익에 RG 비용 반영)
 └─[Harness] rg_settlement_sync (정보 유통 허브 + 파싱, 원칙18-6, D-8)
      ├─[SA] CoupangWingRgSettlementClient  ★신규 — 윙 내부 API 래퍼
      │        (status/api[증분 1순위] · profit-status/search · download-list/api[옵션 단계])
      │        HMAC 미상속, 세션쿠키+x-xsrf-token (inbound.py 패턴 계승)
      ├─[SA] (재사용) 쿠키 CRUD/만료감지     기존 rg_inbound_sync 인프라
      └─ 저장: CoupangRgSettlementFee 테이블 ★신규
               증분=(account_key×정산주기×수수료종류) / 옵션단계=+vendor_item_id
 └─ 대조 뷰(D-6/D-7): compute_command_center → 'RG 정산 비용(미반영)' 독립 지표
      (판매수수료+풀필먼트, account_key별, 매출인식일 기준 D-10). net_profit 불변(Phase1).
      Phase2에서 basis·dedup·테스트 잠근 뒤 by_option 귀속+net_profit 플립.
 └─ 자동화: scheduler_service 일일 RG 정산 sync job
 └─ 검산: status/api 비용합(f) == Σ(종류별 리포트) (라이브 정합, 원칙22) + fixture 테스트(D-12)
```

## 핵심 리스크
- **엑셀 비동기 다운로드**: 종류별 리포트는 요청→생성→다운로드 비동기. 폴링·타임아웃·실패 fail-soft 필요.
- **엑셀 컬럼에 vendor_item_id 유무**: 옵션 귀속의 전제. 없고 주문번호만이면 주문→옵션 매핑 단계 추가(S1에서 확인).
- **쿠키 만료**(D-5 기존): inbound와 동일 — 302 감지·🔴 상태.
- **보관비/반품/반출의 옵션 귀속 모호성**: 판매 단위 아님 → 회계 표기 방식 합의 필요.

## 체크리스트 (Sprints) — D-6 reconciliation-first: Phase1 대조뷰(net_profit 불변), Phase2 플립
### Phase 1 — 대조(reconciliation) 뷰 (누락 가시화, net_profit 불변)
- [x] **S1. CoupangWingRgSettlementClient SA** — `status/api`(주별 정산리포트, 매출인식일 기준 D-10) 래퍼. 세션쿠키+xsrf (inbound.py 패턴). 방어적 파싱(D-13). profit-status 보조. ★body 실측(2026-06-09): {startDate, endDate, searchDateType:"SALES"(매출인식일)/"PAYMENT"(정산일)}.
- [x] **S2. CoupangRgSettlementFee 모델 + 마이그레이션** — grain=(account_key×recognition_date_from×recognition_date_to×fee_type). **판매수수료(B)+풀필먼트(J) 둘 다**(D-9). 음수(취소/환급) 허용. alembic g1h2i3j4k5l6.
- [x] **S3. rg_settlement_sync Harness** — status/api 수집·파싱(D-8)·적재 + fail-soft(302→🔴). **fixture 테스트(D-12) 14/14 PASS**: 파싱·부호·집계·dedup·누락필드 방어.
- [x] **S4. 대조 뷰 노출(D-6/D-7)** — compute_command_center에 'RG 정산 비용(미반영)' 독립 지표(account_key별). net_profit **불변**. 광고비는 표시만(D-11). API/프론트. scheduler 등록. codex P2×2 수정(rg_settlement.py 미커밋 + xsrf decrypt). 커밋 e7cb99f.
### Phase 2 — net_profit 플립 (규칙 잠근 뒤 정확 반영)
- [x] **S5. 회계 규칙 최종 잠금 + 엑셀 스키마 실증** — ✅ 완료(2026-06-09, 커밋 2c410c9+6bcff4d, codex 3R pass, fixture 22/22, prod 라이브 reconcile 검증).
  - **D-10 라이브 확정(원칙22)**: totalFulfillmentFeeDeductionAmount=배송비(delivery)뿐 → fee_type 'fulfillment'→'delivery' 리네임(alembic h2i3j4k5l6m7 UPDATE). 풀필먼트 J=배송+입출고+보관(레퍼런스17 §7 검산 일치). status/api 컴포넌트=할인적용가(A−B)+VAT(실청구), 이월 g 별도필드. searchDateType=SALES.
  - **D-11 코드화**: RG정산 ad_sales 정본, 광고비 XLSX sell_type='2P'(RG)분 Phase2 플립 시 제외(rg_ad_spend_to_exclude+_agg_rg_ad_overlap). Phase1 표시만. 현재 prod 2P행 0개(겹침 없음).
  - **reconcile guard(codex 지적1)**: _rg_account_breakdown other=total−라인합, legacy/미지 fee_type 가시화. **net_profit 불변(D-6)**.
  - **★엑셀 실증 완료(레퍼런스17 §8-1)**: 종류별 엑셀(WAREHOUSING_SHIPPING)에 **옵션ID(vendor_item_id) per 주문 존재** → S6 옵션단위 수집 **가능**. 매출인식일·주문ID·SKU·발생비용(A)/할인적용가(A−B) 포함. **검산 완전일치**: Σ옵션 할인적용가(A−B)=요약합계=status/api(VAT前). ★S6 규칙=옵션 cost는 **할인적용가(A−B)** 사용(gross A 아님), VAT 별도 gross-up.
- [x] **S6. 옵션 단위 수집** — ✅ 완료(2026-06-09, 커밋 d637bd6, codex 4R pass, fixture 44/44, prod 라이브 self-verify). **S6-core(파서·모델·수동업로드·검산) 완료, S6-auto(자동 다운로드)는 후속**(download-list/api body 캡처 필요).
  - **모델**: CoupangRgSettlementFee grain에 vendor_item_id 추가(계정 row=''sentinel, 옵션 row=실제ID). alembic i3j4k5l6m7n8(batch_alter, unique 갱신, 기존 row '' backfill—동작 불변). prod 196행 '' backfill 완료.
  - **파서(Harness, D-8)**: 시트명→fee_type(입출고비→warehousing·배송비→delivery 등 8종), **헤더명 기반 동적 컬럼 매핑**(2층 헤더 row7+row8, 시트별 컬럼 위치 다름 대응—입출고 col25·배송 col24). 옵션 cost=**할인적용가(A−B) VAT前**(§8-1). 집계 grain=(옵션ID, 정산주기끝). 검산 Σ상세==요약합계.
  - **ingest**: fee_type 단위 병합(같은 fee_type 여러 시트 합산) + snapshot replace(delete-once, 종료일 fallback) + 검산2(요약최종 vs status/api 계정 row, fee_type+period 합계 기준).
  - **이중계상 가드(codex P1)**: 대조뷰 _agg_rg_settlement_fees에 vendor_item_id="" 필터 → 옵션 row 적재해도 계정 대조뷰·net_profit 불변(D-6).
  - **라우터**: POST /api/coupang/ops/rg/settlement/upload-xlsx(수동, vendor_id 자동매핑+불일치 reject).
  - **codex 4R(원칙19)**: 1R 4건(이중계상 P1·stale·vendor검증·마이그가시성)→수용/해결. 2R 3건(같은fee_type삭제충돌 P1·snapshot빈시트·미등록vendor)→수용. 3R 2건(종료일fallback 데이터손실 P1·vs_status_api false mismatch)→수용. 4R pass(남은 P1/P2 없음).
  - **★prod 라이브 self-verify(원칙22)**: 샘플 엑셀 업로드 8행, **vs_status_api 완전 일치**(warehousing 75,489==status/api·delivery 130,599==status/api, diff 0). **net_profit 불변 517,949→517,949**(D-6). 대조뷰 other=0. 재업로드 idempotent(snapshot replace).
- [x] **S6-auto. 자동 엑셀 다운로드** — ✅ 완료(2026-06-09, 커밋 e9554bc, codex 3R pass). Wing 3단계 비동기 흐름(request-download·download-list 폴링·download/api/v2·S3 GET). 요청별 고유 requestTime(P1), per-account auth 실패 결과 반환(P2-a), 24h poll window for duplicates(P2-b), _mark_red mid-flow(P2-c), 0행 ingest error(P2-d). POST /api/coupang/ops/rg/settlement/auto-download. ★prod self-verify 미완(쿠키 존재 시 실행 필요—수동 업로드 선행).
- [x] **S7. net_profit 플립 (계정 단위, D-14/D-16)** — ✅ 완료(2026-06-09, 커밋 0ec96cf+a58a9e1, codex 2R pass, fixture 12/12, prod 라이브 self-verify). account_sum.net_profit에서 계정 RG 총액(status/api, VAT後) **전액 차감**(D-16). 순수함수 `apply_rg_net_profit_flip` + 5 브리지필드(net_profit_pre_rg·rg_settlement_total·rg_ad_settlement·rg_non_ad_deducted·rg_flip_status enum applied_full/not_applied_no_data). by_option 불변. **★D-15→D-16 전환(/browse 라이브 조사)**: RG 광고비가 광고센터 PA 보고서엔 없고 RG 정산에만 존재(prod 2P 0행 실증) → non-ad 차감(D-15)은 RG 광고 누락 → 전액 차감(D-16). 미래 2P>0 겹침 가드(log.warning). **prod self-verify: net_profit 2,706,189.80→2,045,586.80(전액 660,603 차감, 광고 45,375 포함), 등식·회귀·overlap=0 전부 통과**. **모델 감사는 S8로 분리**. (계획서: docs/PLAN_S7_net_profit_flip.md)
- [x] **S8. 모델(A) 과오청구 감사 (D-4/D-17)** — ✅ 완료(2026-06-09, 커밋 7de358a+00a8525, codex 1R pass[P1 0·P2 2 수용], fixture 32, prod 라이브 self-verify). 사이즈 분류(SA1 공식표 §7 세변합∪무게 상위채택)+최소금액 floor(SA2)+이상치 스크리닝(SA3 배송 주문당·입출고 수량당 정규화) → Harness rg_fee_audit → GET /api/coupang/ops/rg/fee-audit. **읽기 전용·net_profit 불변**(D-17). 정확금액 복제 안 함(fragile 머니코드 회피). codex P2-1(배송 order_count 정규화)·P2-2(날짜 overlap) 수용+회귀테스트. **prod self-verify: 22옵션 15플래그(size_mismatch_high 4=극소형이 배송 3,800~4,050=floor 2.8배·below_floor 2·unit_unknown 9[RG주문 희소])**. 사이즈표 라이브 확보=레퍼런스 17 §7.
- **S6-auto. prod self-verify** — ✅ 완료(2026-06-09). `POST /rg/settlement/auto-download` 라이브: WING1 28/28 완료 9적재·WING2 28/28 완료 10적재, 인증 정상. 옵션단위 vendor_item_id 적재 확인(delivery·warehousing). CATEGORY_TR 0행(시트명 "주문내역, 판매수수료" 미매핑 — sale_fee는 status/api로 이미 수집돼 기능 영향 없음). scheduler `auto_download_rg_settlement_job` 06:15 KST 등록(커밋 db48d04).
- 각 Sprint: self-verify(라이브 prod) + fixture 테스트(D-12, 머니코드) + codex review pass (원칙19).

## 현재 진행 단계
- 2026-06-08: discovery 완료 + 계획서 + **plan-eng-review + Codex 외부검증 통과**. 구현 착수 전.
- 2026-06-09: **S1~S3 완료**. status/api body 실측(searchDateType SALES/PAYMENT). fixture 테스트 14/14 PASS.
- 2026-06-09: **S4 완료**. intelligence.py _agg_rg_settlement_fees()+rg_settlement 독립섹션. scheduler sync_coupang_rg_settlement_job 05:30 KST. 프론트 RgSettlementCard(계정별 대조 카드). codex P2×2 수정. 커밋 e7cb99f. Phase 1(S1~S4) 코드 완료.
- 2026-06-09: **★Phase 1 prod self-verify 완료 (라이브 증거, 원칙22)**. prod(sellc.ohitech.co.kr, PM2 ohisell-backend:8001)에 마이그레이션 g1h2i3j4k5l6 적용 → CoupangRgSettlementFee 테이블 생성. WING1·WING2 sync 각 98행 status=ok. 종합조망 API rg_settlement 섹션 라이브 200, RgSettlementCard 라이브 렌더(WING1 412,156 + WING2 13,295, **순이익 불변=D-6 확정**). 프론트 dist 배포(index-D79z1Lve.js).
  - **★코드 검증 결과 (원칙22 정정)**: status/api 스키마·body가 라이브와 정확히 일치(S0 실측 옳았음). 조사 중 "스키마 틀림" 잠정 단정은 내 직접호출 body 오류였고 정산현황 탭 실제호출 캡처로 정정.
  - **★발견 1 — Wing 쿠키 httpOnly 누락**: 라이브 302 원인=document.cookie엔 httpOnly 세션쿠키(CGSID_PARTNERADMINWEB·JSESSIONID·sxSessionId) 없음. JS·CDP 둘 다 못 읽음 → DevTools "Copy as cURL" 등록이 유일 경로(광고비/RG입고와 동일 parse_curl_cookies). failures.jsonl 기록.
  - **★발견 2 — 종합조망 500 버그(기존)**: overview.py:56 `datetime.now(_KST)` _KST 미정의 NameError(커밋 a2bbd3a부터 존재, 라이브 미검증 방치). `kst_today()`로 수정, codex review PASS, 라이브 200. failures.jsonl 기록.
  - **★발견 3 — S4 모델 미커밋**: models.py(CoupangRgSettlementFee)+__init__.py(export)가 e7cb99f에서 누락돼 로컬 미커밋(prod엔 scp로 반영됨). overview.py 수정과 함께 커밋 예정.
- 2026-06-09: **★S5 완료(코드 잠금 + 엑셀 실증)**. 커밋 2c410c9(코드)+6bcff4d(docs). D-10: fulfillment=배송비 라이브 확정(원칙22), fee_type 리네임(alembic h2i3j4k5l6m7), 발생f basis. D-11: 광고비 dedup 규칙 코드화(2P↔ad_sales). reconcile guard. codex 3R pass, fixture 22/22, prod 마이그레이션+배포+라이브 reconcile OK, net_profit 불변(D-6). **엑셀 실증(§8-1)**: WAREHOUSING_SHIPPING 엑셀에 옵션ID(vendor_item_id) per 주문 존재→S6 가능. Σ옵션 할인적용가(A−B)=요약=status/api 완전 검산. S6 규칙=할인적용가(A−B)+VAT.
- 2026-06-09: **★S6-core 완료(옵션 단위 수집)**. 커밋 d637bd6. 모델 vendor_item_id grain(alembic i3j4k5l6m7n8) + 엑셀 파서(헤더명 동적매핑·2층헤더·시트별 위치 다름) + ingest(fee_type 병합·snapshot replace·종료일 fallback·검산2) + 수동 업로드 라우터 + 이중계상 가드(대조뷰 vendor_item_id='' 필터). codex 4R pass(1R 4건·2R 3건·3R 2건 수용, 4R 클린). fixture 44/44. **prod 라이브 self-verify(원칙22): 업로드 8행, vs_status_api 완전일치(75,489·130,599 diff 0), net_profit 불변 517,949, 대조뷰 other=0**. S6-auto(자동 다운로드)는 download-list/api body 캡처 대기.
- 2026-06-09: **★S7 완료(net_profit 플립, Phase 2 핵심)**. 커밋 0ec96cf(D-15 non-ad)+a58a9e1(D-16 전액 차감). **D-15→D-16 전환의 핵심은 /browse 라이브 조사**: 광고센터(advertising.coupang.com /reports/pa)가 pa_daily XLSX 출처=마켓플레이스(3P/윙) 광고이고, RG 광고비(80,754)는 거기 없고 RG 정산에만 존재(prod 광고 2P 전기간 0행, coupang_ad_option_daily·coupang_ad_report 양쪽). → D-15(광고 제외 차감)는 RG 광고 누락 → **전액 차감 D-16**. codex 2R(1R Low2 수용·2R 3건 수용: UI 부호버그·stale주석·overlap경고). fixture 12/12. **prod 라이브 self-verify(원칙22): net_profit 2,706,189.80→2,045,586.80(rg_total 660,603 전액 차감, 광고 45,375 포함), 등식 pre−total==np·감소액==rg_total·flip_status=applied_full·overlap=0·RG0윈도우 불변 전부 통과**. 마이그레이션 없음(테이블 불변).
- 2026-06-09: **★S6-auto 완료(자동 엑셀 다운로드)**. 커밋 e9554bc. Wing 3단계(request-download→폴링→S3 GET)·고유 requestTime·24h poll window·_mark_red mid-flow·0행 error. codex 3R pass(P1 1건+P2 4건 모두 수용·수정). POST /api/coupang/ops/rg/settlement/auto-download. prod self-verify 미완(쿠키 존재 시).
- 2026-06-09: **★S6-auto prod self-verify + scheduler 등록 완료**. 라이브 `auto-download`: WING1 28/28→9적재·WING2 28/28→10적재, 인증 정상, 옵션단위 vendor_item_id 적재 확인. `auto_download_rg_settlement_job` 06:15 KST scheduler 등록(커밋 db48d04, scheduler_state DB 확인). CATEGORY_TR만 0행(시트명 미매핑, sale_fee는 status/api로 수집중이라 무영향).
- 2026-06-09: **★S8 완료(과오청구 감사, D-17)**. 커밋 7de358a+00a8525. **사이즈표 라이브 확보**(Wing fee-details 로그인, 쿠키 피커 import → 레퍼런스 17 §7): 세변합(cm)+무게(kg) 6등급, 둘 다 충족·하나라도 초과시 상위. 구조: SA1 classify_size_type(순수함수)+SA2 expected_fee_floor(최소금액)+SA3 detect_fee_anomalies(배송 주문당·입출고 수량당 정규화) → Harness rg_fee_audit → GET /rg/fee-audit. **읽기전용·net_profit 불변**. **정확금액 복제 안 함**(D-17 — fragile 머니코드 회피, D-4 모델보조·D-5 사실만 준수). codex 1R(P1 0·P2 2 수용: 배송 order_count 정규화·날짜 overlap)+회귀테스트. fixture 32. **prod self-verify: 22옵션 15플래그(size_mismatch_high 4·below_floor 2·unit_unknown 9), order_count 정규화 라이브 확인**.
- **2026-08-03 — 감사 판정 기구 결함 수정 (PR #183)**: `rg_fee_anomaly.detect_fee_anomalies`가 쿠팡 실측값(`coupang_size_type`)이 있으면 `size_mismatch_high` 판정을 **통째로 스킵**했다("실측값이 과금 기준이므로 추정 불필요"). 그 논리는 "실측 사이즈 = 청구 사이즈"를 전제하는데 라이브가 반증했다 — 실측 극소형인데 청구는 대형1. 실측값의 도착이 불일치를 해소한 게 아니라 **확인해 준 것인데** 코드는 "볼 필요 없음"으로 읽고 신호를 껐고, 그 결과 2026-06-15에 올라온 플래그가 숫자 하나 안 바뀐 채 조용히 사라졌다. 수정: 스킵 제거 + 신호 세기별 이름 분리 — 실측 미확보는 `size_mismatch_high`(약한 신호, 기존 계약 불변), 실측 확보는 `measured_vs_billed_mismatch`(강한 신호, 신설·정렬 최상단). 읽기 전용이라 net_profit 불변(D-17). prod 배포·라이브 확인 완료(`measured_vs_billed_mismatch: 1`, 해당 옵션 flags 재부상, 정렬 1/13).

## 다음 액션
- **트랙 코드 전부 완료(S1~S8 + S6-auto self-verify + scheduler).** 운영 단계 진입.
- **S8 후속(선택)**: ★2026-06-15 라이브 재감사 — size_mismatch_high는 이제 **1건**(이전 4건은 PRODUCT_SIZE_COMPARISON 실측 사이즈 자동수집되며 해소). 남은 1건 = 아이패드미니필름(91313543029, 등록 극소형 세변합 60.5cm vs 배송청구 주문당 4,050원=대형1 정합 3배, size_source=registered_dims 실측 미확보). **Jino 결정(2026-06-15): 자동해제 대기** — 다음 입고 시 실측 사이즈 수집되면 자동 판가름(ⓐ극소형 확정→과오청구 / ⓑ대형1 확정→정당), 코드변경 없음. 프론트 UI(로켓그로스 탭에 감사 뷰) 추가는 미정.
- **CATEGORY_TR 파서**: 시트명 "주문내역, 판매수수료" → sale_fee 매핑 추가하면 S6-auto가 판매수수료도 옵션단위 수집(현재 status/api로 계정단위 수집 중이라 기능 영향 없음, 선택).
- **D-16 잔존 리스크 감시**: 광고센터에서 RG상품 검색광고를 돌려 광고 XLSX에 2P가 생기면(현재 0) `ad_xlsx_rg_overlap>0` log.warning 발화 → RG 광고 이중계상 가능성 재검토.
- **TODOS.md(D4)**: dashboard.py/profit_calculator.py 쿠팡 순이익은 S7 RG 반영 안 됨(command-center와 화면 차이) — 후속.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | clean | Step0 스코프축소(증분 채택 D-6) + 아키텍처 1건(계정단위 RG비용 표현 D-7) + parser 흡수(D-8). 결정 3건 전부 합의. |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | issues_found→fixed | 3 findings, 2/3 fixed (P1 Alembic multi-head ✅, P2 kst_now ✅; P1 날짜변환 S0 증거 기각) |

- **Step 0**: 스코프 축소 — 풀(옵션) → 증분(계정 먼저), Jino 승인(D-6).
- **Architecture**: 1 이슈(계정 단위 RG 비용을 옵션 grained net_profit에 표현) → 해결(D-7, 계정 한 줄).
- **Code Quality**: parser SA 과분해 → Harness 흡수(D-8).
- **Tests**: 검산=항목합(f)==Σ(종류별), 옵션단계 Σ(옵션)==계정총액. 프로젝트 컨벤션=라이브 self-verify(prod, committed test 없음, S5 선례).
- **Performance**: RG 정산 데이터 소량(주별 수 행). N+1 없음. 비동기 엑셀 폴링은 Phase 2로 분리.
- **NOT in scope**: 옵션 단위 귀속(Phase 2 S5~7) / 모델(A) 과오청구 감사(S7 선택) / 오하이테크 RG(WING2)는 동일 구조 확장.
- **What already exists (재사용)**: rg_inbound_sync 쿠키 인프라(CoupangWingCookie·crypto.py·inbound.py·302감지·fail-soft) / intelligence.compute_command_center(합산 지점) / scheduler_service(RG cron).
- **Failure modes**: 쿠키 만료(302→🔴, inbound 검증됨) / 비동기 엑셀 생성 실패(Phase2, 폴링 타임아웃 fail-soft) / 광고비 이중계상(net_profit 플립 시 D-11로 차단) / Wing API 스키마 드리프트(D-13 방어적 파싱).
- **CODEX (outside voice, 원칙19)**: 10건 지적. 수용=#10 reconciliation-first(→D-6/D-7 개정, Jino D3 승인), #3 RG 판매수수료 누락(→D-9), #4/#5 basis 미정(→D-10), #6 dedup 손짓(→D-11), #8 머니코드 테스트(→D-12 부분수용), #7 API 방어(→D-13), #9 엑셀 조기확인(→S5). 미합의=없음.
- **CROSS-MODEL**: Claude 리뷰는 증분(account_sum 한 줄)까지, Codex가 한발 더 — net_profit 자체를 미루는 reconciliation-first. Jino가 Codex 안 채택.
- **VERDICT**: ENG + CODEX CLEARED — Phase 1(S1~S4 대조뷰) 구현 착수 가능.
