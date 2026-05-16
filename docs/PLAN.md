# PLAN.md — Sprint 4B-cafe24 계획서 (자사몰 순이익 정확화)

## Sprint 정보
- Sprint ID: sprint-4b-cafe24
- 작성일: 2026-05-16
- 목표: 자사몰(cafe24) 순이익을 실제와 일치하도록 정확화
- 우선순위: 1) 자사몰 → 2) 네이버 스마트스토어 → 3) 쿠팡 (Jino 지시)

## 왜 (Why)
cafe24 242건 매출 4,076,600원이 순이익에 부정확하게 반영됨:
- 취소/반품 13건이 매출에 포함 (profit_calculator status 필터 없음)
- 자사몰 결제 수수료 0% 처리 (실제 PG 수수료 미반영, 순이익 과대)
- 배송비 미반영 (실제 한진택배 1,900원/건 우리 부담)
- cafe24 상태코드 미정규화 (C40/R40/E00 등 raw 저장)

## 무엇 (What) — 완료 기준 (Sprint Contract)
1. cafe24 순이익 = 매출(취소/반품 제외) − 원가 − PG수수료 − 배송비(1,900/건) − VAT
2. PG 수수료가 결제수단/PG사별로 정확히 산출됨 (공식 요율 기반, 추정 없음)
3. 취소/반품/입금전 주문이 매출·순이익에서 제외됨
4. 기존 242건이 백필로 정확히 재계산됨 (재동기화 불필요 — 식별자가 raw_data 앞부분이라 잘림 무관)
5. 각 Phase codex review pass + QA before/after 순이익 검증

## 확정 사업 규칙 (Jino 승인 완료)
- 배송비: cafe24 주문 건당 1,900원 (한진택배, 우리 부담, 고객 무료배송)
- 네이버페이 신용카드 등급: 영세 → 1.870% (VAT 포함)
- 수수료 × 1.1 (VAT 포함): KCP/카카오/토스 = VAT 별도라 ×1.1, 네이버페이 = VAT 이미 포함
- 사용 PG: KCP, 카카오페이, 토스페이, 네이버페이

## 공식 수수료표 (출처: 각 PG 공식 문서 / help.admin.pay.naver.com)
| PG / 결제수단 | 요율 | 적용 |
|--------------|------|------|
| KCP 신용카드 | 3.5% (VAT별도) | rev × 0.035 × 1.1 |
| KCP 계좌이체(tcash) | 1.8% 최저 200원 (VAT별도) | max(rev×0.018,200) × 1.1 |
| KCP 무통장/가상(cash/icash) | 건별 300원 (VAT별도) | 330 (주문당, 라인 비례배분) |
| KCP 에스크로/현금영수증 | 0 | 0 |
| 카카오페이 (card/prepaid) | 3.5% (VAT별도) | rev × 0.035 × 1.1 |
| 토스페이 (card/prepaid) | 3.5% (VAT별도) | rev × 0.035 × 1.1 |
| 네이버페이 신용카드(card) | 영세 1.870% (VAT포함) | rev × 0.0187 |
| 네이버페이 계좌이체(tcash) | 1.650% (VAT포함) | rev × 0.0165 |
| 네이버페이 무통장/가상(cash/icash) | 1% 최대 275원 (VAT포함) | min(rev×0.01,275) (주문당) |
| 네이버페이 보조결제(prepaid) | 3.740% (VAT포함) | rev × 0.0374 |
| 네이버페이 휴대폰(cell) | 3.850% (VAT포함) | rev × 0.0385 |

## 식별 로직 (cafe24 raw_data, 242건 데이터 검증 완료)
```
if order.market_id == "NCHECKOUT":  → 네이버페이 (payment_method별 요율)
elif gateway 카카오:                 → 카카오페이 3.5%×1.1
elif gateway toss:                   → 토스페이 3.5%×1.1
else (kcp/기타):                     → KCP (payment_method별 요율)
```
- cafe24 official payment_method: cash=무통장 card=신용카드 tcash=계좌이체 icash=가상계좌 cell=휴대폰 prepaid=선불금 point=적립금 coupon=쿠폰 cod=후불 etc=기타
- cafe24 official order_status: N*=정상(N00=입금전) C*=취소 R*=반품 E*=교환

## 아키텍처 (레고 구조 — Jino 승인 완료)
```
[Agent] 자사몰(cafe24) 순이익 정확화
 ├─[Harness] 주문 동기화 정확화 (sync_service)
 │  ├─[SA] Cafe24StatusMapper      — order_status → active|cancelled|returned|exchanged|pending
 │  └─[SA] Cafe24PaymentClassifier — order dict → payment_type 코드
 └─[Harness] 순이익 계산 정확화 (profit_calculator)
    ├─[SA] CommissionResolver — payment_type + 금액 → 수수료액 (요율표)
    ├─[SA] ShippingResolver   — cafe24 → 주문당 1,900원
    └─ profit_calculator      — 취소/반품 제외 + 위 SA 결과 반영
```
SA는 순수 함수(다른 SA 모름). Harness가 데이터 유통. SA 시그니처 optional 입력 허용.

## DB 스키마 변경 (Alembic)
orders 테이블 추가:
- payment_type VARCHAR(30) NULL — 분류 결과 (naverpay_card, kcp_card, kakaopay ...)
- commission_amount NUMERIC(12,2) NULL — 동기화 시 산출 PG 수수료
- status 값 정규화: active/cancelled/returned/exchanged/pending

## 순이익 계산 변경
- cafe24: commission = Σ order.commission_amount (rate 무시), shipping = 1,900/주문
- 비-cafe24: 기존 channel.commission_rate 로직 유지 (회귀 방지)
- 매출 제외 status: cancelled, returned, pending
- 교환(exchanged): 매출 유지

## 미해결 위험 / 근사
- raw_data 10000자 잘림 → 복합결제(card+point ~15건) 금액 분해 불가 → 전체 라인매출 근사(영향 미미). 향후 sync 잘림 제거 권장(별도 작업)
- 매출 제외 경계: 기본 C*/R20+/N00 제외. 필요시 Jino 확인

## 진행 순서 (SDD)
SA 3개 → 마이그레이션 → sync 배선 → profit_calculator → 백필 → 각 단계 /codex review → QA
