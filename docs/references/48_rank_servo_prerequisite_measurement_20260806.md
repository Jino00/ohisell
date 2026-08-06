# ref 48 — 순위 서보(D-NAO-124) 착수 전 선행조건 실측

> 측정일시: 2026-08-06 20:5x KST · prod DB(`sellc.ohitech.co.kr:/home/ubuntu/ohisell/backend/ohisell.db`)
> 목적: D-NAO-124(순위 서보 — "4위는 기준점, 이익이 방향타")를 **착수할 수 있는지**와
> **어디서 틀릴 수 있는지**를 숫자로 못 박는다. 설계·구현은 다음 세션.
> ★이 문서는 측정만 한다. 결론을 설계로 확장하지 않았다.

## 0. 한 줄 요약

착수 가능하다. **단 관문은 "곡선 적립"이 아니라 «전환 표본의 희소성»이다** — 순위→이익 곡선을
개별 광고그룹 단위로는 세울 수 없다(사용 가능 그룹 121개, 그룹당 순위구간별 2~4 관측).
풀링 단위 상향 또는 베이지안 축소가 설계의 핵심 제약이다.

## 1. 선행조건 4단계 판정

D-NAO-124가 명시한 선행조건: ①관측 개통(D-NAO-122) ②순위→가격 사상(D-NAO-123)
③순위별 이익 곡선 적립 ④서보 가동.

| 조건 | 상태 | 실측 근거 |
|---|---|---|
| ① 관측 개통 | ✅ | `naver_adgroup_hourly_today` **28,766행 / 9일**(2026-07-29~08-06) |
| ② 순위→가격 사상 | ✅ | D-NAO-123 완료(트랙 기록) |
| ③ 곡선 적립 | ✅ **단 종류가 다르다** | `naver_learning_state.bid_rank_slope` **56행**(updated_at 2026-08-05) — 이건 **입찰→순위**다. D-NAO-124가 쓰려는 것은 **순위→이익**이다(§3) |
| ④ 서보 가동 | ❌ | 미구현 = 다음 세션 작업 |

### ★트랙 기록 정정 — D-NAO-112 반응곡선 수정은 **prod에 배포돼 있다**

트랙 D-NAO-112는 커밋 `466bbc5`(반응곡선 원료 분리)를 **"prod 미배포"** 로 기록하고 있다.
실측 결과 prod의 `backend/app/services/naver_ad/bid_rank_curve.py`에 `CURVE_SAMPLE_TYPES`가
**5회**, `bid_step_types.py`에 **1회** 존재한다 = 배포됨. 그리고 `bid_rank_slope`가 56행 적립돼 있다
(원료 필터가 고쳐지지 않았다면 0행이어야 한다).

→ **"rank_servo 영구 콜드스타트" 우려는 해소된 상태다.** 이 정정이 없으면 다음 세션이 이미
해결된 문제를 다시 고치려 든다.

## 2. 학습 메트릭 적립 현황 (`naver_learning_state`)

| metric | 행 | 갱신 범위 |
|---|---|---|
| `hour_weight` | 168 | 2026-07-30 ~ 08-05 |
| **`bid_rank_slope`** | **56** | 2026-08-05 |
| `conv_delay` | 21 | 2026-08-05 |
| `gave_score_d7` / `gave_score_d3` | 6 / 6 | 2026-08-05 |
| `proposal_accuracy` | 4 | 2026-07-19 ~ 08-05 |
| `launch_target_rank` | 3 | 2026-07-29 |
| `prediction_accuracy` | 1 | 2026-08-04 |
| `estimate_bias` | 1 | 2026-08-05 |

⚠️**대부분의 metric이 08-05에서 멈춰 있다**(측정 시점 08-06 21시). 학습 루프가 08-06에 돌지
않았거나 갱신을 남기지 않았을 수 있다 — 착수 전 확인 항목(이 문서는 원인 규명을 하지 않았다).

★`launch_target_rank`가 이미 존재한다(3행) = **목표 순위를 학습 메트릭으로 써서 `rank_servo`가
무개조 소비하는 배관이 이미 있다**. D-NAO-124의 새 결정기가 붙을 자연스러운 이음매 후보다
(D-NAO-112 노트의 "bid_rank_slope에 써서 rank_servo가 load_response_priors로 무개조 소비"와 같은 패턴).

## 3. 순위 → 이익 원료 (최근 14일, `naver_ad_daily` 광고그룹 grain)

전체: **24,047행 / 437그룹 / 14일**, 순위 산출 가능(`imp>0 AND rank_sum>0`) **23,394행**,
전환 있는 행 **848행(3.5%)**.

### 3-1. 순위 구간별 집계 — ★인과가 아니다

`rank_bucket = ROUND(rank_sum / imp)`

| 순위 | 표본(그룹×일) | 광고비 | 전환매출 | 차액(매출−광고비) | ROAS |
|---|---|---|---|---|---|
| 1 | 8,368 | 1,042,447 | 1,933,220 | +890,773 | 1.85 |
| 2 | 5,864 | 2,773,358 | 3,467,900 | +694,542 | 1.25 |
| 3 | 3,961 | 2,656,022 | 4,060,570 | +1,404,548 | 1.53 |
| **4** | 2,048 | 2,392,698 | 4,104,840 | **+1,712,142** | 1.72 |
| 5 | 869 | 769,157 | 1,291,500 | +522,343 | 1.68 |
| 6 | 653 | 468,436 | 936,600 | +468,164 | 2.00 |
| 7 | 475 | 204,328 | 370,400 | +166,072 | 1.81 |
| 8 | 351 | 109,592 | 286,200 | +176,608 | 2.61 |
| 9~12 | 461 | 112,251 | 207,700 | +95,449 | 1.85 |

r=4가 절대 차액 최대이고 Jino의 "4등에 가장 큰 가산점"과 방향이 같다. **그러나 이 표를 곡선으로
쓰면 안 된다**:
- **순위는 개입이 아니라 결과다.** 순위가 다른 그룹은 애초에 다른 상품·다른 경쟁 환경이다
  (트랙 D-S3-c가 이미 경고: *"marginal ROAS는 집계 데이터로 인과≠상관 분리 불가"*).
- 차액은 **전환매출 − 광고비**이고 총이익이 아니다. 공헌이익률(원가·수수료·물류비)을 곱해야 한다.
- 전환은 D+1~7 정착 중이다(`conv_delay` 곡선 필요).

### 3-2. 인과 원료(그룹 내 변동) — ★진짜 관문

| 지표 | 값 |
|---|---|
| 순위 관측 있는 그룹 | **436** |
| 여러 순위 구간을 경험한 그룹(`COUNT(DISTINCT rank_bucket) ≥ 2`) | **428** |
| 그중 전환일 2일 이상 = **사용 가능** | **121** |

전환 있는 그룹×일의 순위 분포: r1=95 · r2=112 · r3=145 · r4=111 · r5=63 · r6=49 · r7=21 ·
r8=16 · r9=6 · r10~20=6 (합 628 행 — 순위 1~4에 **74%** 집중)

→ **그룹 내 순위 변동은 풍부하지만(428/436), 그 변동에 전환이 붙은 표본은 그룹당 2~4개다.**
개별 그룹 곡선은 불가능하다. 설계는 이 제약을 정면으로 다뤄야 한다(풀링 단위 상향 / 계층
베이즈 축소 / 순위 1~4 밖은 사전분포에 의존 등 — 선택은 다음 세션 설계).

## 4. 재현 쿼리

```bash
# 선행조건 ①: 당일 그룹 시간별 적재
ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && sqlite3 ohisell.db \
 \"SELECT COUNT(*), COUNT(DISTINCT ad_date), MIN(ad_date), MAX(ad_date) FROM naver_adgroup_hourly_today;\""

# 선행조건 ③: 학습 메트릭 적립
ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && sqlite3 -header ohisell.db \
 \"SELECT metric, COUNT(*) n, MIN(date(updated_at)) frm, MAX(date(updated_at)) too \
   FROM naver_learning_state GROUP BY metric ORDER BY n DESC;\""

# §3-1 순위 구간별 집계
ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && sqlite3 -header ohisell.db \
 \"SELECT CAST(ROUND(rank_sum*1.0/imp) AS INT) rb, COUNT(*) n, SUM(cost) cost, \
     SUM(conv_direct_amt+conv_indirect_amt) conv \
   FROM naver_ad_daily WHERE ad_date >= date('now','+9 hours','-14 day') \
     AND adgroup_id<>'' AND imp>0 AND rank_sum>0 GROUP BY 1 ORDER BY 1;\""

# §3-2 그룹 내 변동(사용 가능 표본)
ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && sqlite3 -header ohisell.db \
 \"WITH g AS (SELECT adgroup_id, CAST(ROUND(rank_sum*1.0/imp) AS INT) rb, \
       conv_direct_amt+conv_indirect_amt conv FROM naver_ad_daily \
       WHERE ad_date >= date('now','+9 hours','-14 day') AND adgroup_id<>'' AND imp>0 AND rank_sum>0) \
   SELECT COUNT(*) groups_, SUM(CASE WHEN nrb>=2 THEN 1 ELSE 0 END) multi_rank, \
     SUM(CASE WHEN nrb>=2 AND conv_days>=2 THEN 1 ELSE 0 END) usable \
   FROM (SELECT adgroup_id, COUNT(DISTINCT rb) nrb, SUM(CASE WHEN conv>0 THEN 1 ELSE 0 END) conv_days \
         FROM g GROUP BY adgroup_id);\""

# prod 배포 상태(D-NAO-112 정정 근거)
ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell && grep -c CURVE_SAMPLE_TYPES \
 backend/app/services/naver_ad/bid_rank_curve.py backend/app/services/naver_ad/bid_step_types.py"
```

## 5. 다음 세션 착수 시 확인할 것 (이 문서가 답하지 않은 것)

- [ ] **학습 메트릭이 08-05에서 멈춘 이유** — 08-06 루프가 안 돌았나, 갱신을 안 남겼나
- [ ] 기존 `rank_servo`가 목표 순위를 **어디서 읽는가** — `launch_target_rank`가 그 경로인지 코드로 확인
      (맞다면 새 결정기는 그 메트릭에 쓰기만 하면 되고 서보는 무개조)
- [ ] 공헌이익률 원천 — 그룹 grain에 붙일 수 있는가(BEP 테이블 `naver_product_bep`은 상품 grain)
- [ ] `conv_delay` 곡선의 알려진 이상(day 8~18이 전부 0.5714 = 4/7, D-NAO-112 기록)이
      순위별 이익 추정에 영향을 주는가
- [ ] 페이지 경계 비선형성(D-NAO-74 백로그) — 순위 3↔4에서 노출이 급변한다는 대행사 증언.
      §3-1에서 r4의 차액이 가장 큰 것과 관련 있을 수 있으나 **이 문서는 확인하지 않았다**
