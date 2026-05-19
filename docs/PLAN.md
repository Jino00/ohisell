# PLAN — 배송비 회계 재설계 (고객결제 vs 판매자부담 분리)

> Sprint: 4B-shipping-accounting
> 작성: 2026-05-19 (Opus 계획)
> 목적: 채널마다 의미가 뒤섞인 `orders.shipping_cost`를 올바른 회계 모델로 통일

## 1. 배경 / Why

- 진단 결과 `orders.shipping_cost` 컬럼이 채널별 의미가 다름 (설계 결함):
  - CAFE24 = `per_order_shipping("CAFE24")` 1,900 고정 (판매자 실비용, 고객 무료배송)
  - NAVER = `deliveryFeeAmount` 선결제분 (고객이 낸 배송비, pass-through)
  - COUPANG = `shippingPrice` (대부분 고객 부담)
- `calculate_daily_trend`/`product_profit`는 전 채널 `o.shipping_cost`를 비용 차감
  → NAVER/쿠팡은 고객이 낸 돈을 판매자 비용처럼 차감 (순이익 과소)
- `calculate_channel_summary`는 CAFE24만 차감 → 4개 화면 순이익 불일치
  (어제 NAVER 기준 92,500원 / 5월 누적 1,510,000원 차이)
- NAVER 무료배송분은 실제 판매자가 한진택배비 부담하는데 데이터 없어 누락

## 2. 확정 모델 (Jino 결정)

- **매출 = 상품매출 + 고객이 낸 배송비** (NAVER 선결제분 / 쿠팡 shippingPrice)
- **비용 += 1,900원 / 배송(주문 1건)**, 전 채널 동일 (한진택배 지급액, 항상 발생)
- **수수료는 상품매출 기준만** — 배송비(고객결제분·1,900)에는 수수료 미부과
- VAT: 표시 매출(상품+배송) 기준 10/110 유지 (가정 — Jino 미정 시 현행 공식 유지,
  한국 부가세상 배송비도 과세 대상이므로 합리적 기본값. 정정 가능)
- 1배송 = 주문번호 1건. 멀티라인 주문은 1,900을 1회만 계상 (NAVER 5월 152행/143주문 검증)

## 3. 설계 — 엔진 단독 변경 (DB 마이그레이션 無, 되돌리기 쉬움)

`o.shipping_cost`는 그대로 두고, `profit_calculator.py`에서 채널별로 해석:

| 채널 | delivery_income (매출 가산) | 판매자 배송비 (비용) |
|------|------------------------------|----------------------|
| NAVER | `o.shipping_cost` (선결제분, 무료=0) | 1,900 / 주문 |
| COUPANG_* (위탁 제외) | `o.shipping_cost` (shippingPrice) | 1,900 / 주문 |
| CAFE24 | 0 (고객 무료배송) | 1,900 / 주문 |
| 기타 | 0 | 1,900 / 주문 |

- 수수료: `_line_commission`에 **상품매출만** 전달 (현행대로 — delivery_income 미포함)
  → NAVER는 API commission_amount라 자동 만족, 정률 채널도 상품매출 기준 유지
- net = (상품매출 + delivery_income) − 원가 − 수수료 − (1,900×배송) − 광고비 − VAT

### 변경 함수 (profit_calculator.py 1파일)
1. 헬퍼 추가: `_delivery_income(ch, o)`, 상수 `HANJIN_PER_SHIPMENT = 1900`
2. `calculate_daily_trend` — 라인 루프: revenue += product+delivery, 기존
   `bucket["shipping"] += o.shipping_cost` 제거 → 주문번호 seen-set으로 1,900/주문 1회
3. `calculate_channel_summary` — CAFE24-only 조건 제거, 전 채널 1,900/주문(seen-set),
   revenue에 delivery_income 가산
4. `calculate_product_profit` — 주문번호 단위 1,900을 라인 매출 비례 배분, delivery 가산
5. `calculate_channel_daily_trend` — daily_trend 래핑이라 자동 반영 (확인만)

## 4. 완료 기준

- 4개 화면(`/trend` `/kpi` `/channel-breakdown` `/trend-by-channel`) NAVER 순이익 일치
- 선결제 주문: 매출에 고객배송비 포함, 비용 1,900/주문, 수수료엔 미포함
- 무료배송 주문: 매출 가산 0, 비용 1,900/주문
- 멀티라인 주문 배송비 1회만 계상 (중복 0)
- CAFE24 회귀 0 (기존 1,900/주문 비용 동일, 매출 불변)
- `/codex review` PASS (금액 로직 — 원칙 19 필수)
- 프로덕션 실측: 어제 NAVER 4개 화면 동일 순이익 확인

## 5. 범위 밖

- DB 스키마 변경 / 재동기화 (엔진 단독 변경으로 회피)
- 무료배송 한진단가 채널별 차등 (전 채널 1,900 확정)
- VAT 배송비 분리 과세 (현행 공식 유지, 추후 정정 가능)
