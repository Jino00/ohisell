# ref 49 — 스마트스토어 수수료: 예상=실측 오차 0원 + 정산 커버리지 재측정

> 측정일시: 2026-08-07 06:3x KST · prod DB(`sellc.ohitech.co.kr:/home/ubuntu/ohisell/backend/ohisell.db`)
> 계기: Jino 질문 원문 — **"수수료 추정치는 나중에 실측으로 바뀌면 자동으로 맞아지는거지?"**
> ★이 문서는 측정만 한다. 결론을 설계로 확장하지 않았다.

## 0. 한 줄 요약

수수료는 조회 시점마다 실측 우선으로 다시 계산되므로 별도 재계산 배치가 필요 없다. 그리고
**예상(주문 API) 수수료와 실측(정산) 수수료는 최근 60일 4,958라인 전체에서 오차 0원**이다.
화면의 「실측 N / 예상 M」 표시는 **신뢰도 라벨이지 오차 경고가 아니다** — 실측 비율이 낮은
최근 구간도 수수료 금액 자체는 이미 맞다.

## 1. 수수료는 조회 시점에 매번 다시 붙는다 (저장 안 함)

`backend/app/routers/naver_ops.py`의 `sales_summary`는 `(order_id, product_id)` →
`naver_settlement_case`(PROD_ORDER) 실측 맵을 **요청마다** 만들고, 있으면 실측값을, 없으면
`orders.commission_amount`(주문 API 예상값)를 쓴다. 값을 DB에 저장(캐시)하지 않으므로,
정산이 나중에 들어온 뒤 같은 기간을 다시 조회하면 **자동으로 실측 숫자가 나온다.** 별도
재계산 배치·크론이 필요 없는 구조다.

## 2. 정산 실측 커버리지 곡선 (최근 30일 주문, 취소·반품 제외)

| 나이 | 라인 | 실측 | 커버리지 |
|---|---|---|---|
| D+0~3 | 463 | 89 | 19.2% |
| D+4~7 | 385 | 190 | 49.4% |
| D+8~11 | 362 | 348 | 96.1% |
| D+12~14 | 221 | 220 | 99.5% |
| D+15~20 | 441 | 439 | 99.5% |
| D+21~ | 710 | 710 | 100.0% |

★2026-08-03 측정(직전 기록된 곡선)과 **두 곳이 다르다**:
- 초기 구간이 0%가 아니다 — D+0~3에 이미 19.2%가 붙는다(종전 기록은 D+0~2가 0%였다).
- D+12가 정확히 100%는 아니다 — 99.5%다.

**완전 폐쇄는 D+21.** 대조창 권장값(`오늘−14일` 이전)은 이번 측정으로도 그대로 유효하다
(D+12~14가 이미 99.5%).

## 3. ★예상 수수료 = 실측 수수료, 오차 0원 (최근 60일 4,958라인)

- 예상 합계: **3,624,618원**
- 실측 합계: **3,624,618원**
- 차이: **0원**
- 값이 다른 라인 수: **0건**

→ 「실측 N / 예상 M」은 **신뢰도 라벨이지 오차 경고가 아니다.** 실측 비율이 낮은 최근 구간
(직전 측정 기준 7일 24.3%)도 수수료 **금액 자체는 이미 맞다.** 정산이 들어와도 숫자는
안 움직인다.

(2026-08-03에 503라인 표본으로 같은 결론이 이미 있었고, 이번은 10배 표본(4,958라인)으로
재확인된 것이다.)

## 4. 재현 쿼리

```bash
# ② 커버리지 곡선
ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && sqlite3 -header ohisell.db \"
WITH o AS (
  SELECT DISTINCT o.order_number, o.platform_product_id,
    CAST(julianday('now','+9 hours') - julianday(o.order_date) AS INT) age
  FROM orders o WHERE o.channel_id=6
    AND o.order_date >= datetime('now','+9 hours','-30 day')
    AND o.status NOT IN ('cancelled','returned','pending'))
SELECT CASE WHEN age<=3 THEN 'D+0~3' WHEN age<=7 THEN 'D+4~7' WHEN age<=11 THEN 'D+8~11'
            WHEN age<=14 THEN 'D+12~14' WHEN age<=20 THEN 'D+15~20' ELSE 'D+21~' END bucket,
  COUNT(*) lines_,
  SUM(CASE WHEN EXISTS(SELECT 1 FROM naver_settlement_case c
      WHERE c.order_id=o.order_number AND c.product_id=o.platform_product_id
        AND c.product_order_type='PROD_ORDER') THEN 1 ELSE 0 END) settled
FROM o GROUP BY 1 ORDER BY MIN(age);\""

# ③ 예상 vs 실측
ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && sqlite3 -header ohisell.db \"
WITH o AS (
  SELECT o.order_number oid, o.platform_product_id pid, SUM(o.commission_amount) est
  FROM orders o WHERE o.channel_id=6
    AND o.order_date >= datetime('now','+9 hours','-60 day')
    AND o.status NOT IN ('cancelled','returned','pending') GROUP BY 1,2),
a AS (
  SELECT c.order_id oid, c.product_id pid,
    -SUM(c.total_pay_commission + c.selling_interlock_commission + c.free_installment_commission) act
  FROM naver_settlement_case c WHERE c.product_order_type='PROD_ORDER' AND c.product_id IS NOT NULL
  GROUP BY 1,2)
SELECT COUNT(*) lines_, ROUND(SUM(o.est)) est_sum, ROUND(SUM(a.act)) act_sum,
       ROUND(SUM(a.act) - SUM(o.est)) diff,
       SUM(CASE WHEN ABS(a.act - o.est) >= 1 THEN 1 ELSE 0 END) lines_differ
FROM o JOIN a ON a.oid=o.oid AND a.pid=o.pid;\""
```
