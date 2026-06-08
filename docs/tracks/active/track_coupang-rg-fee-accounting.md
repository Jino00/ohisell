# 트랙: 쿠팡 로켓그로스(RG) 수수료 회계 자동화

> 시작일: 2026-06-08 · 상태: 🟢 Active
> 단일 진실 원천. 이 트랙을 무시·변형해서 진행하지 말 것. 변경은 Jino 승인 후 D-N으로 기록.
> 상세 정책·라이브 실증: docs/references/17_coupang_rg_fulfillment_fee_policy.md

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
- [~] **S5. 회계 규칙 최종 잠금 + 엑셀 스키마 실증** — ★코드 잠금 완료(커밋 2c410c9, codex 3R pass, fixture 22/22, prod 라이브 reconcile 검증). 엑셀 실증(vendor_item_id 유무)은 윙 로그인 진행 중.
  - **D-10 라이브 확정(원칙22)**: totalFulfillmentFeeDeductionAmount=배송비(delivery)뿐 → fee_type 'fulfillment'→'delivery' 리네임(alembic h2i3j4k5l6m7 UPDATE). 풀필먼트 J=배송+입출고+보관(레퍼런스17 §7 검산 일치). 발생비용(f) 기준(이월 g 별도필드, 미혼입). searchDateType=SALES.
  - **D-11 코드화**: RG정산 ad_sales 정본, 광고비 XLSX sell_type='2P'(RG)분 Phase2 플립 시 제외(rg_ad_spend_to_exclude+_agg_rg_ad_overlap). Phase1 표시만. 현재 prod 2P행 0개(겹침 없음).
  - **reconcile guard(codex 지적1)**: _rg_account_breakdown other=total−라인합, legacy/미지 fee_type 가시화. **net_profit 불변(D-6)**.
  - **남은 것**: 종류별 엑셀(입출고·배송비 등)에 vendor_item_id 컬럼 유무 확인 → S6 옵션단위 수집 전제. download-list/api body 실측(현재 500) 캡처 필요.
- [ ] **S6. 옵션 단위 수집** — download-list/api + 비동기 엑셀 폴링·파싱 → CoupangRgSettlementFee에 vendor_item_id 추가. Σ(옵션)==계정총액 검산. fixture 테스트.
- [ ] **S7. net_profit 플립 + 광고비 dedup 차단 + 모델(A) 감사** — by_option·account_sum에 RG 비용 반영(대조→권위), ad_costs RG분 제외/대체(D-11), 치수→등급 모델 과오청구 감사(D-4). (선택/후속)
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

## 다음 액션
- **(커밋 대기)** models.py + __init__.py(S4 모델 누락분) + overview.py(_KST 버그픽스) 커밋. 프론트 dist 이미 prod 배포됨.
- **S5**: 회계 규칙 최종 잠금 + 엑셀 스키마 실증(vendor_item_id 유무). ★status/api 응답에 vendor_item_id 없음 확인(주기별 집계뿐) → 옵션단위는 S6 download-list/api 엑셀 필요.

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
