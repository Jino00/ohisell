# 실측 — 성과등급(band) × 시간대 축 (2026-08-18)

측정 담당(읽기 전용 서브에이전트). 경향성·해석·권고는 **쓰지 않는다** — 숫자와 좌표만.

## 0. 표기 규약

- 측정일시: **2026-08-18 KST 13:50~13:55 부근**(쿼리별 UTC/KST now() 병기, 아래 참조).
- prod 접속: `ssh -o BatchMode=yes sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db" < 로컬.sql` — 전량 SELECT/PRAGMA/CREATE TEMP TABLE(세션 한정, 원본 DB 미변경).
- band 정본: `docs/references/data/63_band_decomposition/band_group_total.csv`(855행, 헤더 포함 854 데이터행) — `python3`으로 파싱해 `CREATE TEMP TABLE band_map`에 INSERT 구문으로 적재(원본 DB에 CSV를 직접 반영하지 않음, 세션 종료 시 소멸).
- **라벨 규약**: 커머스(orders) 유래 숫자는 전부 「**상한 프록시 — 0쪽에서만 결정적**」이다. 광고 귀속 축이 없다(2026-08-18 이전 실측 확정 사실). 아래 M2 전체에 적용.
- **`__backfill__` 배제**: 새로 쓴 모든 집계 SQL에 `adgroup_id <> '__backfill__'` 조건을 명시적으로 다시 걸었다(공용 필터 없음, 이 저장소 전례). 확인 결과 `naver_adgroup_product`·`naver_adgroup_hourly_today` 모두 라이브에 `__backfill__` 행 **0건**(쿼리로 직접 확인, 아래 M2·M3 참조) — 이번 측정 범위에서는 영향 없었으나 조건은 유지했다.
- weekday 표기: SQLite `strftime('%w', date)`(0=일~6=토)를 Python `date.weekday()` 관례(0=월~6=일)로 변환해 사용 — `(strftime('%w',d)+6)%7`. `naver_hourly_pattern_history`(M1)는 이미 이 관례로 저장돼 있음(모델 docstring 확인).

---

## M1. `naver_hourly_pattern_history` 라이브 현황

### 실행 SQL
```sql
SELECT COUNT(*) FROM naver_hourly_pattern_history;                              -- row_count
SELECT COUNT(DISTINCT weekday) FROM naver_hourly_pattern_history;               -- distinct_weekday
SELECT COUNT(DISTINCT hour) FROM naver_hourly_pattern_history;                  -- distinct_hour
SELECT MIN(last_folded_date), MAX(last_folded_date) FROM naver_hourly_pattern_history;
SELECT MIN(sample_days), MAX(sample_days), ROUND(AVG(sample_days),2) FROM naver_hourly_pattern_history;
SELECT COUNT(*) FROM naver_hourly_pattern_history WHERE weekday BETWEEN 0 AND 6 AND hour BETWEEN 0 AND 23;  -- cells_present_of_168
SELECT COUNT(*) FROM naver_hourly_pattern_history WHERE sample_days = 0;
SELECT weekday, hour, clk_sum, cost_sum, sample_days, last_folded_date
FROM naver_hourly_pattern_history ORDER BY weekday, hour;
```
(스키마: `PRAGMA table_info(naver_hourly_pattern_history)` → `id, weekday, hour, clk_sum, cost_sum, sample_days, last_folded_date, updated_at`. **`conv_cnt`·전환 관련 컬럼 자체가 이 테이블에 없다.**)

### 관측 표 (요약)

| metric | value |
|---|---|
| row_count | 168 |
| distinct_weekday | 7 |
| distinct_hour | 24 |
| cells_present_of_168 | 168 (전 칸 채워짐) |
| cells_with_zero_sample_days | 0 |
| min_last_folded_date | 2026-08-11 |
| max_last_folded_date | 2026-08-17 |
| min_sample_days | 4 |
| max_sample_days | 6 |
| avg_sample_days | 5.82 |

**판정: M3의 원료로 쓸 수 없다** — 두 가지 독립적 이유.
1. **전환 컬럼이 아예 없다**(`clk_sum`, `cost_sum`만 존재). CPA(비용/전환건수) 계산이 원리적으로 불성립.
2. **누적 창이 최대 6일**(`last_folded_date` 최솟값 2026-08-11 → 최댓값 2026-08-17, `fold_yesterday`가 매일 전날 1일치를 접는 구조이므로 이 테이블은 08-11(가동 개시로 추정)부터 시작한 지 6일째다). "누적"이라는 이름과 달리 **주 단위에도 못 미친다**.
3. (부가) 코드 추적 결과(`hourly_pacing.hourly_rows` ← `NaverHourlySnapshot`) 이 테이블은 **계정 전체 합산**이고 `adgroup_id`·`campaign_id` grain이 원천적으로 없다 — band 층화가 애초에 불가능한 구조다(집계 코드 `hourly_pattern.py`의 `fold_yesterday`가 `pacing["rows"]`를 시간대별로만 더함, 캠페인/그룹 축 소실).

★**용도 구분(2026-08-18 M5 추가)**: 위 "기각"은 **CPA 원료로서의 기각**이다. `clk_sum`·`cost_sum`(전환 없이 클릭·비용만)은 그대로 유효한 값이고, **비용 분포(페이싱) 용도**(이미 `trigger_watch.py:176`·`ad_report.py:88`가 이렇게 쓰고 있음)엔 **정상 원료**다. 전체 용도의 기각으로 읽지 말 것.

**특이 관측(해석 없이 사실만)**: 168칸 전부에서 **weekday×hour=0시·1시는 예외 없이 `clk_sum=0, cost_sum=0`**이다(7개 요일 × 2시간 = 14칸 전부). 다른 시간대는 0이 아님. 원인 미상 — 추정하지 않는다.

### 전체 168칸 원자료
```
weekday  hour  clk_sum  cost_sum  sample_days  last_folded_date
0  0    0    0       6  2026-08-17
0  1    0    0       6  2026-08-17
0  2    107  134333   6  2026-08-17
0  3    68   79649    6  2026-08-17
0  4    39   47171    6  2026-08-17
0  5    20   25627    6  2026-08-17
0  6    10   13144    5  2026-08-17
0  7    23   23892    5  2026-08-17
0  8    64   77492    6  2026-08-17
0  9    80   100259   6  2026-08-17
0  10   126  159869   6  2026-08-17
0  11   152  201785   6  2026-08-17
0  12   179  249937   6  2026-08-17
0  13   162  217571   6  2026-08-17
0  14   168  234513   6  2026-08-17
0  15   199  269308   6  2026-08-17
0  16   175  244652   5  2026-08-17
0  17   227  291638   6  2026-08-17
0  18   216  294526   6  2026-08-17
0  19   184  244585   6  2026-08-17
0  20   185  241186   6  2026-08-17
0  21   197  267730   6  2026-08-17
0  22   180  238251   5  2026-08-17
0  23   259  341679   6  2026-08-17
1  0    0    0       5  2026-08-11
1  1    0    0       5  2026-08-11
1  2    109  144522   5  2026-08-11
1  3    64   85824    5  2026-08-11
1  4    37   53212    5  2026-08-11
1  5    26   35163    5  2026-08-11
1  6    22   28375    5  2026-08-11
1  7    23   28120    5  2026-08-11
1  8    60   85963    5  2026-08-11
1  9    99   129106   5  2026-08-11
1  10   137  191457   5  2026-08-11
1  11   123  175448   5  2026-08-11
1  12   147  188282   5  2026-08-11
1  13   166  211943   5  2026-08-11
1  14   152  198806   5  2026-08-11
1  15   135  158879   4  2026-08-11
1  16   260  321168   5  2026-08-11
1  17   175  225282   5  2026-08-11
1  18   194  236721   5  2026-08-11
1  19   157  191726   5  2026-08-11
1  20   143  174250   5  2026-08-11
1  21   166  199582   5  2026-08-11
1  22   192  240668   5  2026-08-11
1  23   171  213172   5  2026-08-11
2  0    0    0       6  2026-08-12
2  1    0    0       6  2026-08-12
2  2    94   118314   6  2026-08-12
2  3    37   42042    6  2026-08-12
2  4    33   42765    6  2026-08-12
2  5    32   39519    6  2026-08-12
2  6    19   28984    6  2026-08-12
2  7    28   31608    6  2026-08-12
2  8    45   53437    6  2026-08-12
2  9    102  124907   6  2026-08-12
2  10   122  163916   6  2026-08-12
2  11   137  178193   6  2026-08-12
2  12   190  240881   6  2026-08-12
2  13   230  304569   6  2026-08-12
2  14   185  248108   6  2026-08-12
2  15   221  292765   6  2026-08-12
2  16   257  345777   6  2026-08-12
2  17   253  319656   6  2026-08-12
2  18   159  189067   6  2026-08-12
2  19   156  187701   6  2026-08-12
2  20   233  302009   6  2026-08-12
2  21   161  197082   6  2026-08-12
2  22   198  248588   6  2026-08-12
2  23   205  262196   6  2026-08-12
3  0    0    0       6  2026-08-13
3  1    0    0       6  2026-08-13
3  2    113  131362   6  2026-08-13
3  3    58   72698    6  2026-08-13
3  4    42   46379    6  2026-08-13
3  5    22   31190    6  2026-08-13
3  6    27   29050    6  2026-08-13
3  7    43   49281    6  2026-08-13
3  8    49   56977    6  2026-08-13
3  9    114  134801   6  2026-08-13
3  10   167  205830   6  2026-08-13
3  11   176  226885   6  2026-08-13
3  12   185  220460   6  2026-08-13
3  13   176  206906   6  2026-08-13
3  14   159  189328   6  2026-08-13
3  15   217  241406   6  2026-08-13
3  16   198  252472   6  2026-08-13
3  17   172  213949   6  2026-08-13
3  18   200  242699   6  2026-08-13
3  19   186  229299   6  2026-08-13
3  20   75   93703    6  2026-08-13
3  21   352  431830   6  2026-08-13
3  22   201  253595   6  2026-08-13
3  23   185  237612   6  2026-08-13
4  0    0    0       6  2026-08-14
4  1    0    0       5  2026-08-14
4  2    119  138722   6  2026-08-14
4  3    63   72528    6  2026-08-14
4  4    27   32162    6  2026-08-14
4  5    21   26956    6  2026-08-14
4  6    18   22793    6  2026-08-14
4  7    30   34194    6  2026-08-14
4  8    58   67463    6  2026-08-14
4  9    96   118501   6  2026-08-14
4  10   128  155928   6  2026-08-14
4  11   153  182101   6  2026-08-14
4  12   183  242838   6  2026-08-14
4  13   162  191646   6  2026-08-14
4  14   154  191090   6  2026-08-14
4  15   170  218299   6  2026-08-14
4  16   183  233454   6  2026-08-14
4  17   178  228254   6  2026-08-14
4  18   199  242705   6  2026-08-14
4  19   179  217367   6  2026-08-14
4  20   163  201375   6  2026-08-14
4  21   170  217128   6  2026-08-14
4  22   181  231416   6  2026-08-14
4  23   185  236341   6  2026-08-14
5  0    0    0       6  2026-08-15
5  1    0    0       6  2026-08-15
5  2    117  145557   6  2026-08-15
5  3    61   79155    6  2026-08-15
5  4    41   51222    6  2026-08-15
5  5    25   31090    6  2026-08-15
5  6    20   20828    6  2026-08-15
5  7    34   37701    6  2026-08-15
5  8    58   73122    6  2026-08-15
5  9    77   98635    6  2026-08-15
5  10   106  137770   6  2026-08-15
5  11   120  159390   6  2026-08-15
5  12   150  193433   6  2026-08-15
5  13   164  198448   6  2026-08-15
5  14   159  205312   6  2026-08-15
5  15   179  239352   6  2026-08-15
5  16   186  236539   6  2026-08-15
5  17   175  208379   6  2026-08-15
5  18   185  222519   6  2026-08-15
5  19   159  199444   6  2026-08-15
5  20   162  205466   6  2026-08-15
5  21   168  208802   6  2026-08-15
5  22   167  204747   6  2026-08-15
5  23   167  206111   6  2026-08-15
6  0    0    0       6  2026-08-16
6  1    0    0       6  2026-08-16
6  2    131  154211   6  2026-08-16
6  3    75   85227    6  2026-08-16
6  4    32   39605    6  2026-08-16
6  5    22   28374    6  2026-08-16
6  6    21   27705    6  2026-08-16
6  7    21   27411    6  2026-08-16
6  8    36   45433    6  2026-08-16
6  9    64   83189    6  2026-08-16
6  10   109  132060   6  2026-08-16
6  11   123  155883   6  2026-08-16
6  12   137  176309   6  2026-08-16
6  13   167  218396   6  2026-08-16
6  14   163  204814   6  2026-08-16
6  15   172  213878   6  2026-08-16
6  16   182  227830   6  2026-08-16
6  17   202  269731   6  2026-08-16
6  18   190  234734   6  2026-08-16
6  19   188  242202   6  2026-08-16
6  20   161  203299   6  2026-08-16
6  21   147  176153   6  2026-08-16
6  22   194  256151   6  2026-08-16
6  23   213  263414   6  2026-08-16
```

---

## M2. 커머스 주문 시간대 분포 × 등급 (상한 프록시)

### 사전 확인 — 링크 체인
`orders(channel_id=6, 네이버 스마트스토어).platform_product_id` ↔ `naver_adgroup_product.mall_product_id` (직접 매칭, `naver_product_bep.channel_product_id`와 동일 값 공간이라는 모델 docstring대로).

```sql
SELECT id, name, code, platform FROM channels;
-- 6 | 네이버 스마트스토어 | NAVER | naver

SELECT MIN(order_date), MAX(order_date), COUNT(*) FROM orders;              -- 전체
SELECT channel_id, COUNT(*) FROM orders GROUP BY channel_id;                -- 채널별
SELECT COUNT(DISTINCT o.platform_product_id) AS distinct_naver_products,
       COUNT(DISTINCT CASE WHEN nap.mall_product_id IS NOT NULL THEN o.platform_product_id END) AS matched
FROM orders o JOIN channels c ON c.id=o.channel_id
LEFT JOIN naver_adgroup_product nap ON nap.mall_product_id = o.platform_product_id
WHERE c.platform='naver';
```

| metric | value |
|---|---|
| channel 6 = 네이버 스마트스토어 | orders 12,379행 |
| naver 주문 distinct product 수 | 602 |
| naver_adgroup_product에 매칭된 product 수 | 485 (80.6%) |
| naver 주문 전체 date range | 2026-02-12 12:21:59 ~ 2026-08-18 13:29:24 |
| `naver_adgroup_product` 내 `__backfill__` 행 | **0건**(확인 완료) |

### 창 결정
90일 창으로 관측(현재(KST)−90일 ~ 현재): naver 주문 8,450행(전체 naver 12,379행 중). 실제 관측된 창 = **2026-05-20 13:56:02 ~ 2026-08-18 13:29:24**(약 90일).

주문 status 분포(channel=6, 전체 기간, 필터링 안 함 — 아래 집계는 **status 무관 전량**):
`cancelled 517 · confirmed 27 · delivered 11,451 · exchanged 51 · returned 100 · shipped 233`.

### 실행 SQL (fan-out 안전판)
```sql
-- band_map: CSV 854행을 CREATE TEMP TABLE band_map(adgroup_id,campaign_id,campaign_type,band)로 적재.

CREATE TEMP TABLE order_agg AS
SELECT o.id AS order_id, o.order_date, o.quantity, o.selling_price, o.status,
       o.platform_product_id,
       CAST(strftime('%H', o.order_date) AS INTEGER) AS hr,
       CAST(o.quantity AS REAL) * CAST(o.selling_price AS REAL) AS line_amt
FROM orders o
WHERE o.channel_id = 6
  AND o.order_date >= datetime('now','+9 hours','-90 days');

-- baseline: 밴드 미분리, 주문라인 단위 dedup(자연히 1행=1라인이라 dedup은 "매칭 여부"만 걸림)
CREATE TEMP TABLE order_baseline AS
SELECT DISTINCT oa.order_id, oa.hr, oa.line_amt
FROM order_agg oa
JOIN naver_adgroup_product nap
  ON nap.mall_product_id = oa.platform_product_id
  AND nap.adgroup_id <> '__backfill__';

SELECT hr, COUNT(*) AS order_line_cnt, CAST(ROUND(SUM(line_amt)) AS INTEGER) AS amt_sum
FROM order_baseline GROUP BY hr ORDER BY hr;

-- band별: order_id × band 쌍(밴드가 겹치면 한 주문이 여러 밴드에 나타날 수 있음 — 밴드 간 합산 금지)
CREATE TEMP TABLE order_band_pairs AS
SELECT DISTINCT oa.order_id, oa.hr, oa.line_amt, bm.band
FROM order_agg oa
JOIN naver_adgroup_product nap
  ON nap.mall_product_id = oa.platform_product_id
  AND nap.adgroup_id <> '__backfill__'
JOIN band_map bm ON bm.adgroup_id = nap.adgroup_id;

SELECT band, hr, COUNT(*) AS order_line_cnt, CAST(ROUND(SUM(line_amt)) AS INTEGER) AS amt_sum
FROM order_band_pairs GROUP BY band, hr ORDER BY band, hr;

SELECT band, COUNT(DISTINCT order_id) AS distinct_orders, CAST(ROUND(SUM(line_amt)) AS INTEGER) AS amt_sum_dedup_within_band
FROM order_band_pairs GROUP BY band;
```

### 관측 — window sanity
| metric | value |
|---|---|
| order_agg (90일 창 naver 주문라인) | 8,450 |
| order_agg 창 실측 범위 | 2026-05-20 13:56:02.553 ~ 2026-08-18 13:29:24.098 |
| baseline_matched_order_lines (adgroup 매칭 성공) | 8,219 (97.3%) |
| baseline_total_order_lines_in_window | 8,450 |
| band_pairs_row_count (order×band, 밴드 중복 허용) | 15,647 |
| distinct_orders_in_band_pairs | 8,219 (baseline과 동일 — 매칭된 라인은 전부 ≥1개 밴드에 속함) |

**[상한 프록시] BASELINE — hour × (주문라인 건수, 금액합)** (밴드 미분리, dedup 완료)

```
hour  order_line_cnt  amt_sum
0     236   4,154,080
1     120   2,157,700
2     49    843,200
3     49    801,300
4     50    823,000
5     47    894,390
6     112   1,849,690
7     240   3,845,300
8     339   6,109,500
9     390   6,832,430
10    494   8,238,520
11    470   8,030,540
12    453   7,753,700
13    501   9,909,440
14    540   9,038,300
15    508   34,649,820   ← ★이상치, 아래 원행 참조
16    492   8,520,410
17    445   7,636,050
18    404   7,360,900
19    418   7,545,140
20    514   8,958,340
21    517   8,787,400
22    480   8,171,840
23    351   5,819,200
```

**★hour=15 이상치 원행** — `amt_sum` 34,649,820원은 인접 시간대(~8~9M원)의 약 4배. 원행 확인 SQL:
```sql
SELECT o.id, o.order_date, o.platform_product_id, o.quantity, o.selling_price,
       o.quantity*o.selling_price AS line_amt
FROM orders o
WHERE o.channel_id=6 AND o.order_date >= datetime('now','+9 hours','-90 days')
  AND CAST(strftime('%H',o.order_date) AS INTEGER)=15
ORDER BY line_amt DESC LIMIT 10;
```
| order_id | order_date | platform_product_id | qty | selling_price | line_amt |
|---|---|---|---|---|---|
| 10672 | 2026-07-06 15:26:47.814 | 11287690712 | 36 | 439,200 | 15,811,200 |
| 10812 | 2026-07-09 15:21:37.669 | 11734976635 | 19 | 302,100 | 5,739,900 |
| 13104 | 2026-08-03 15:58:27.740 | 11734976635 | 16 | 234,400 | 3,750,400 |
| 11149 | 2026-07-13 15:09:13.361 | 11734976634 | 5 | 79,500 | 397,500 |

상위 3건 합 25,301,500원(hour=15 전체 34,649,820원의 73.0%) — 소수의 대량구매(qty 36·19·16건) 주문이 hour=15 버킷을 지배. 그 외 시간대엔 이런 규모의 단일 라인이 없다(사실만 기록, 원인·의도 해석 안 함).

**[상한 프록시] BAND × HOUR — (주문라인 건수, 금액합)** (밴드 내 dedup, **밴드 간 합산 금지**)

```
band=band1 (총 distinct_orders=6,873, amt_sum(밴드 내 dedup)=143,400,510)
hour  cnt  amt
0    200  3,448,400
1    95   1,591,500
2    36   572,900
3    43   705,300
4    38   628,000
5    40   664,800
6    89   1,434,550
7    210  3,359,700
8    292  5,282,800
9    332  5,900,010
10   408  6,777,800
11   393  6,745,800
12   395  6,724,700
13   431  7,138,700
14   437  7,207,600
15   412  32,837,500   ← 위 이상치(order 10672 등) 대부분이 band1로 판정된 상품에 걸림
16   402  6,956,700
17   376  6,460,950
18   331  6,052,000
19   356  6,442,500
20   422  7,243,500
21   451  7,662,800
22   395  6,754,700
23   289  4,807,300

band=band2 (총 distinct_orders=2,286, amt_sum=38,067,120)
hour  cnt  amt
0    68   1,160,800
1    32   622,200
2    13   218,600
3    12   175,100
4    17   275,000
5    13   216,400
6    35   556,300
7    73   1,121,200
8    89   1,441,400
9    117  1,917,310
10   134  2,270,500
11   141  2,314,000
12   115  1,902,100
13   134  2,176,700
14   170  2,749,500
15   144  2,473,300
16   139  2,225,410
17   111  1,908,600
18   118  1,973,000
19   117  1,877,100
20   138  2,447,100
21   135  2,291,800
22   129  2,247,300
23   92   1,506,400

band=band3 (총 distinct_orders=6,258, amt_sum=124,656,540)
hour  cnt  amt
0    185  3,213,580
1    88   1,540,000
2    34   598,400
3    33   541,600
4    40   661,400
5    36   708,790
6    93   1,550,890
7    185  2,968,700
8    248  4,180,400
9    298  5,370,730
10   370  6,194,420
11   361  6,089,240
12   350  5,943,700
13   374  7,857,040
14   410  6,629,500
15   404  22,875,920   ← 같은 이상치 영향(order 10812·13104가 band3로도 걸림, 밴드 중복 허용 규약)
16   365  6,301,010
17   339  5,794,100
18   308  5,733,500
19   314  5,448,040
20   392  6,931,140
21   400  6,901,000
22   369  6,293,840
23   262  4,329,600

band=band4_unjudgeable (총 distinct_orders=220, amt_sum=3,910,140) — 시간대별 표는 소표본(일부 hour 0건, 결측 아님)
band=excluded_cost0 (총 distinct_orders=10, amt_sum=159,000) — 극소표본
```

★**밴드 총액 검산 주의**: band1(143,400,510)+band2(38,067,120)+band3(124,656,540)+band4(3,910,140)+excluded_cost0(159,000) = 310,193,310원. 이는 **총 매출이 아니다** — 한 주문이 여러 밴드의 상품/광고그룹에 동시 매칭되면 각 밴드에서 각각 dedup되어 중복 계상된다(`band_pairs_row_count=15,647` vs baseline 매칭 라인 `8,219` — 1.90배 fan-out, 지시대로 밴드 간 합산 안 함). baseline 표(위)가 유일한 "밴드 미분리 총액"이다.

---

## M3. 시간대별 CPA 168칸 — 측정 전용

### 원료 선택 판정
- `naver_hourly_pattern_history`(M1) — **기각**: 전환 컬럼 없음 + 창 4~6일뿐 + band grain 없음(위 M1 참조).
- `naver_adgroup_hourly_today` — **채택**. 이름과 달리(「당일만」) prod에 **삭제 로직이 없어 15일치(2026-08-04~2026-08-18)가 누적돼 있다**(라이브 확인, 아래). `adgroup_id` grain 보유 → band 층화 가능. `conv_cnt`(건수) 보유 → CPA 성립.

```sql
SELECT ad_date, COUNT(*), COUNT(DISTINCT adgroup_id), COUNT(DISTINCT hour)
FROM naver_adgroup_hourly_today GROUP BY ad_date ORDER BY ad_date;
```
| ad_date | n_rows | n_adgroups | n_hours |
|---|---|---|---|
| 2026-08-04 | 3218 | 203 | 23 |
| 2026-08-05 | 1788 | 198 | **15** ← 결측 |
| 2026-08-06 | 3289 | 201 | 23 |
| 2026-08-07 | 3440 | 205 | 23 |
| 2026-08-08 | 3501 | 206 | 23 |
| 2026-08-09 | 3619 | 206 | 23 |
| 2026-08-10 | 3516 | 207 | 23 |
| 2026-08-11 | 3447 | 200 | 23 |
| 2026-08-12 | 3398 | 197 | 23 |
| 2026-08-13 | 3236 | 193 | 23 |
| 2026-08-14 | 3274 | 195 | 23 |
| 2026-08-15 | 3264 | 194 | 23 |
| 2026-08-16 | 3339 | 198 | 23 |
| 2026-08-17 | 3345 | 204 | 23 |
| 2026-08-18(당일, 미완) | 1612 | 189 | 12 |

**결측 관측(해석 없음)**: 2026-08-05는 hour 0,1,2,3,4,6,7,8이 없음(15/24만 존재). 확인 SQL: `SELECT DISTINCT hour FROM naver_adgroup_hourly_today WHERE ad_date='2026-08-05' ORDER BY hour;` → `5,9,10,11,12,13,14,15,16,17,18,19,20,21,22`. 원인(수집 실패 vs 전 그룹 실적 0) 미상 — hh24 API가 "실적 있는 시간대만 반환"하는 사양(코드 docstring, `naver_sa_ad_fetcher.py:499`)이라 결측=수집실패인지 결측=전 그룹 0인지 이 자료만으로는 구분 불가.

**★구조적 결측(모든 15일 공통)**: `naver_adgroup_hourly_today`에는 **hour=23 행이 단 1건도 없다**(전체 47,286행 스캔). 확인:
```sql
SELECT COUNT(*) FROM naver_adgroup_hourly_today WHERE hour=23;   -- 0
SELECT MAX(hour) FROM naver_adgroup_hourly_today;                 -- 22
SELECT GROUP_CONCAT(DISTINCT hour) FROM naver_adgroup_hourly_today; -- 0~22 전부 있고 23만 없음
```
→ 이 테이블 기반 168칸 그리드는 **23시(23:00~24:00 KST) 칸이 구조적으로 결측**이다. `naver_hourly_pattern_history`(M1, 다른 파이프라인 소스)에는 23시 데이터가 존재하므로, 소스 파이프라인 간 수집 범위 차이로 보인다 — 원인은 미상, 추정하지 않는다.

### 창 확정
분석 창 = **2026-08-04 ~ 2026-08-17(14일, 완결일만)**. `2026-08-18`(당일)은 제외 — 아래 별도 「미완 스냅샷」으로 표기. **4주(28일)에 못 미친다**(14일=2주). 이 창으로만 계산했다.

### 실행 SQL
```sql
-- band_map 재사용(위와 동일 CSV 적재)

CREATE TEMP TABLE hourly_win AS
SELECT h.ad_date, h.hour, h.adgroup_id, h.cost, h.conv_cnt,
       CAST((strftime('%w', h.ad_date) + 6) % 7 AS INTEGER) AS py_weekday
FROM naver_adgroup_hourly_today h
WHERE h.adgroup_id <> '__backfill__'
  AND h.ad_date >= '2026-08-04' AND h.ad_date <= '2026-08-17';

-- sanity
SELECT COUNT(*) FROM hourly_win;                                     -- 45,674
SELECT COUNT(DISTINCT ad_date) FROM hourly_win;                      -- 14
SELECT COUNT(*) FROM hourly_win h
  WHERE NOT EXISTS (SELECT 1 FROM band_map bm WHERE bm.adgroup_id=h.adgroup_id);  -- 0 (전량 매칭)

-- baseline(밴드 미분리)
SELECT py_weekday, hour, SUM(cost), SUM(conv_cnt), COUNT(DISTINCT ad_date),
       CASE WHEN SUM(conv_cnt)>0 THEN ROUND(CAST(SUM(cost) AS REAL)/SUM(conv_cnt),0) ELSE NULL END AS cpa
FROM hourly_win GROUP BY py_weekday, hour ORDER BY py_weekday, hour;

-- band 층화(band1/2/3 각각 동일 쿼리에 WHERE bm.band='bandN' 추가)
SELECT bm.band, hw.py_weekday, hw.hour, SUM(hw.cost), SUM(hw.conv_cnt), COUNT(DISTINCT hw.ad_date),
       CASE WHEN SUM(hw.conv_cnt)>0 THEN ROUND(CAST(SUM(hw.cost) AS REAL)/SUM(hw.conv_cnt),0) ELSE NULL END AS cpa
FROM hourly_win hw JOIN band_map bm ON bm.adgroup_id=hw.adgroup_id
WHERE bm.band='band1' GROUP BY hw.py_weekday, hw.hour ORDER BY hw.py_weekday, hw.hour;
-- band2, band3 동일 패턴

-- band4_unjudgeable / excluded_cost0: 168칸 대신 총계만(표본 극소)
SELECT bm.band, COUNT(DISTINCT hw.adgroup_id), SUM(hw.cost), SUM(hw.conv_cnt)
FROM hourly_win hw JOIN band_map bm ON bm.adgroup_id=hw.adgroup_id
WHERE bm.band IN ('band4_unjudgeable','excluded_cost0') GROUP BY bm.band;

-- 당일(2026-08-18) 미완 스냅샷 — 168칸에서 제외, 참고용만
SELECT hour, SUM(cost), SUM(conv_cnt), COUNT(DISTINCT adgroup_id)
FROM naver_adgroup_hourly_today
WHERE ad_date='2026-08-18' AND adgroup_id <> '__backfill__'
GROUP BY hour ORDER BY hour;
```

### ★각주 (지시 원문대로 재확인)
- **전환 "금액" 컬럼이 `naver_adgroup_hourly_today`에 없다**(`_STATS_HH24_FIELDS`가 `impCnt/clkCnt/salesAmt/ccnt/avgRnk`만 요청, `convAmt` 미포함 — `naver_sa_ad_fetcher.py:491,504`). 따라서 **CPA(비용/전환건수)만 성립하고 ROAS·총이익은 원리적으로 불성립**한다.
- `orders.order_date`는 **결제(주문 확정) 시각**이고 `naver_adgroup_hourly_today`의 cost/conv_cnt는 **광고 클릭/전환 집계 시각**이다. 둘 사이 시차는 **미상**(클릭→구매까지 걸리는 시간 분포를 이 저장소는 어디에도 적재하지 않는다). 따라서 **같은 시각 버킷의 비용↔주문을 대응시키는 것은 원리적으로 오염**된다 — M2·M3을 같은 hour로 나란히 놓아도 인과·상관 판정에 쓸 수 없다.
- **마지막 버킷(당일 진행 중) 미완 표시**: 위 168칸 그리드는 2026-08-04~08-17(완결 14일)만 사용했다. **2026-08-18(측정 당일)은 전량 미완으로 별도 표(아래)에 두고 168칸 집계에서 제외했다.**

### 관측 표 — BASELINE 168칸 (weekday 0=월…6=일, 완결 14일 합산, hour=23 없음 → 실질 161칸/168칸)

```
wd hr  cost    conv  n_days  CPA
0  0   45,108   18   2   2,506
0  1   30,048   5    2   6,010
0  2   7,082    0    2   (전환0)
0  3   5,884    0    2   (전환0)
0  4   2,402    1    2   2,402
0  5   8,053    1    2   8,053
0  6   15,641   4    2   3,910
0  7   15,320   9    2   1,702
0  8   30,481   7    2   4,354
0  9   39,826   6    2   6,638
0  10  61,083   17   2   3,593
0  11  45,167   15   2   3,011
0  12  65,079   18   2   3,616
0  13  60,564   18   2   3,365
0  14  89,343   25   2   3,574
0  15  71,739   14   2   5,124
0  16  71,618   13   2   5,509
0  17  62,573   12   2   5,214
0  18  69,792   12   2   5,816
0  19  69,372   14   2   4,955
0  20  80,414   23   2   3,496
0  21  86,918   18   2   4,829
0  22  64,058   14   2   4,576
1  0   44,285   9    2   4,921
1  1   11,230   1    2   11,230
1  2   13,011   1    2   13,011
1  3   11,054   3    2   3,685
1  4   4,421    2    2   2,211
1  5   6,944    1    2   6,944
1  6   15,804   7    2   2,258
1  7   46,290   6    2   7,715
1  8   42,725   7    2   6,104
1  9   45,621   6    2   7,604
1  10  76,501   18   2   4,250
1  11  76,844   17   2   4,520
1  12  58,586   6    2   9,764
1  13  73,910   16   2   4,619
1  14  90,372   22   2   4,108
1  15  80,501   20   2   4,025
1  16  87,443   18   2   4,858
1  17  60,235   12   2   5,020
1  18  74,359   16   2   4,647
1  19  66,186   12   2   5,516
1  20  96,818   29   2   3,339
1  21  51,292   12   2   4,274
1  22  28,246   9    2   3,138
2  0   22,767   7    1   3,252
2  1   5,970    4    1   1,493
2  2   5,797    0    1   (전환0)
2  3   2,641    0    1   (전환0)
2  4   1,089    0    1   (전환0)
2  5   3,019    2    2   1,510
2  6   14,022   3    1   4,674
2  7   19,297   7    1   2,757
2  8   23,889   2    1   11,945
2  9   41,489   13   2   3,191
2  10  65,024   17   2   3,825
2  11  92,324   18   2   5,129
2  12  77,881   18   2   4,327
2  13  102,637  16   2   6,415
2  14  110,887  24   2   4,620
2  15  116,670  22   2   5,303
2  16  92,987   30   2   3,100
2  17  76,894   21   2   3,662
2  18  76,795   20   2   3,840
2  19  49,303   9    2   5,478
2  20  53,319   16   2   3,332
2  21  38,485   3    2   12,828
2  22  32,071   10   2   3,207
3  0   19,166   4    2   4,792
3  1   17,151   2    2   8,576
3  2   13,213   2    2   6,607
3  3   14,560   0    2   (전환0)
3  4   9,469    0    2   (전환0)
3  5   7,986    2    2   3,993
3  6   14,607   6    2   2,435
3  7   35,630   8    2   4,454
3  8   53,935   10   2   5,394
3  9   51,546   10   2   5,155
3  10  64,270   14   2   4,591
3  11  45,873   7    2   6,553
3  12  50,734   7    2   7,248
3  13  76,171   18   2   4,232
3  14  71,533   20   2   3,577
3  15  46,406   11   2   4,219
3  16  71,861   9    2   7,985
3  17  75,360   14   2   5,383
3  18  58,199   15   2   3,880
3  19  53,084   18   2   2,949
3  20  70,930   15   2   4,729
3  21  56,978   7    2   8,140
3  22  55,788   5    2   11,158
4  0   42,108   8    2   5,264
4  1   17,691   1    2   17,691
4  2   10,248   1    2   10,248
4  3   5,828    0    2   (전환0)
4  4   7,486    2    2   3,743
4  5   6,788    0    2   (전환0)
4  6   29,527   5    2   5,905
4  7   36,776   8    2   4,597
4  8   38,056   3    2   12,685
4  9   62,218   17   2   3,660
4  10  72,378   20   2   3,619
4  11  52,146   8    2   6,518
4  12  55,438   5    2   11,088
4  13  74,600   13   2   5,738
4  14  74,183   10   2   7,418
4  15  85,532   15   2   5,702
4  16  66,825   15   2   4,455
4  17  75,334   11   2   6,849
4  18  61,201   13   2   4,708
4  19  50,022   9    2   5,558
4  20  61,947   8    2   7,743
4  21  78,538   15   2   5,236
4  22  64,521   7    2   9,217
5  0   43,608   9    2   4,845
5  1   27,506   7    2   3,929
5  2   22,876   5    2   4,575
5  3   7,367    1    2   7,367
5  4   9,847    3    2   3,282
5  5   4,471    2    2   2,236
5  6   24,559   2    2   12,280
5  7   29,291   6    2   4,882
5  8   29,273   10   2   2,927
5  9   46,421   8    2   5,803
5  10  45,108   12   2   3,759
5  11  53,905   5    2   10,781
5  12  53,263   14   2   3,805
5  13  51,784   10   2   5,178
5  14  76,747   17   2   4,515
5  15  51,572   6    2   8,595
5  16  57,508   10   2   5,751
5  17  53,818   15   2   3,588
5  18  42,712   6    2   7,119
5  19  65,739   14   2   4,696
5  20  62,906   9    2   6,990
5  21  56,935   11   2   5,176
5  22  61,980   14   2   4,427
6  0   42,587   4    2   10,647
6  1   23,338   6    2   3,890
6  2   14,557   3    2   4,852
6  3   2,231    1    2   2,231
6  4   8,939    0    2   (전환0)
6  5   10,150   1    2   10,150
6  6   10,798   3    2   3,599
6  7   23,350   5    2   4,670
6  8   37,540   5    2   7,508
6  9   41,965   8    2   5,246
6  10  51,991   11   2   4,726
6  11  62,507   14   2   4,465
6  12  67,763   16   2   4,235
6  13  58,334   13   2   4,487
6  14  76,942   13   2   5,919
6  15  86,654   14   2   6,190
6  16  61,388   14   2   4,385
6  17  69,293   15   2   4,620
6  18  55,374   7    2   7,911
6  19  46,580   15   2   3,105
6  20  80,668   16   2   5,042
6  21  59,895   11   2   5,445
6  22  68,511   14   2   4,894
```
(단위: cost=원, conv=건수, CPA=원/건 반올림. `(전환0)`=conv_cnt=0, CPA는 null로 둠 — 무한대 아님.)

### 관측 표 — BAND 층화 168칸(각 161칸/168, hour=23 결측 공통)

원자료 전량(각 band 168행 중 hour=23 제외 161행)은 실행 로그에서 재현 가능(위 SQL로 재실행 시 동일 산출). 분량이 커 이 문서엔 **band별 원자료 파일**로 별도 보관한다:
- `docs/references/data/74_band_x_time_brand/raw_m3_band1.txt`
- `docs/references/data/74_band_x_time_brand/raw_m3_band2.txt`
- `docs/references/data/74_band_x_time_brand/raw_m3_band3.txt`

(각 파일 형식: `band wd hour cost_sum conv_sum n_days cpa`, sqlite3 `.mode column` 출력 그대로.)

band4_unjudgeable / excluded_cost0은 표본 극소라 168칸 대신 총계만:

| band | n_adgroups(창 내 활동) | cost_sum | conv_sum |
|---|---|---|---|
| band4_unjudgeable | 10 | 725 | 0 |
| excluded_cost0 | 0 (창 내 활동 없음) | — | — |

### 관측 표 — 당일(2026-08-18) 미완 스냅샷 (168칸 제외, 참고용)

측정 시각 KST 13:52 기준, 오늘 날짜 행은 **hour 0~11까지만 존재**(hour=12 이후는 아직 수집/미완결). 2026-08-18 = 화요일(py_weekday=1)로, 이 시간대들은 위 168칸 그리드의 `wd=1, hour=0~11` 칸과 같은 셀이지만 **집계에 포함하지 않았다**.

```
hour  cost    conv  n_adgroups
0     27,987   7    160
1     11,072   2    143
2     8,919    0    128
3     1,173    0    102
4     2,702    0    105
5     3,892    2    133
6     2,170    1    109
7     4,522    1    139
8     22,330   4    145
9     30,553   3    147
10    26,135   7    148
11    20,928   7    153
```

---

## M4. 두 시간축의 정렬 가능성

### 판정: `orders.order_date`는 **KST naive**(오프셋 없는 KST 벽시계)로 저장돼 있다

코드 근거(라이브 재확인 필요 없는 정본 — `order_delivery.py:222-238`):
```python
def _kst_naive(value: Any) -> datetime | None:
    """ISO8601(오프셋 포함) → **KST 벽시계 naive** datetime. ...
    ★naive KST로 떨어뜨리는 이유: orders.order_date가 이미 그 표현이다(라이브 실측
      `2026-08-03 14:36:12.456000` — 오프셋 없이 KST 벽시계). ...
    """
```
이 docstring 자체가 2026-08-03에 실측 확인된 정본 사실(이 저장소엔 "SQLite `now()`는 UTC" 함정 전례가 있어 이번에도 라이브로 교차검증):

```sql
SELECT datetime('now') AS utc_now, datetime('now','+9 hours') AS kst_now;
-- 실행 시각(2026-08-18): utc_now=2026-08-18 04:50:04, kst_now=2026-08-18 13:50:04
SELECT MAX(order_date) FROM orders WHERE channel_id=6;
-- 2026-08-18 13:29:24.098000
```
`MAX(order_date)`가 실행 시점 KST 벽시계(13:50)에 **21분 앞선 근접값**으로 나온다 — UTC였다면 04:50 근처여야 하므로 최대 9시간의 괴리가 났을 것. **KST 저장 확정.**

### 판정: `naver_adgroup_hourly_today.hour`도 **KST 기준**

```sql
SELECT MAX(ad_date), MIN(hour), MAX(hour) FROM naver_adgroup_hourly_today
WHERE ad_date=(SELECT MAX(ad_date) FROM naver_adgroup_hourly_today);
-- ad_date=2026-08-18, hour 범위 0~11
```
실행 시각 KST 13:52에 오늘 날짜 행이 **hour=11까지만 존재**(완결된 시간만 저장한다는 모델 docstring과 일치 — 12시=아직 진행 중이라 미수록). UTC였다면 실행 시각 UTC 04:52 기준 hour=3~4까지만 있어야 하는데 관측값(0~11)은 그보다 훨씬 크다 → **KST 기준 확정.**

### 결론
두 시간축(`orders.order_date`의 hour, `naver_adgroup_hourly_today.hour`) 모두 **동일 타임존(KST) · 동일 자정 경계**를 쓴다. 타임존 자체의 어긋남은 **없음**(0시간). 단, M3 각주에서 이미 밝힌 대로 **"같은 시간 표기"≠"같은 사건 시각"** — order_date는 결제 확정 시각, hourly_today의 cost/conv는 클릭·전환 집계 시각이라 **사건 간 시차(클릭→구매 리드타임)는 별도 미상**이며 이 저장소 어디에도 그 분포가 적재돼 있지 않다.

---

## M5. `naver_keyword_hourly` — 168칸 CPA 재산출 (범위 확대, 2026-08-18 추가)

경계는 M1~M4와 동일(읽기 전용·`__backfill__` 매번 배제·추정 금지·해석 금지). **M1~M4 본문은 고치지 않았다** — M1의 「기각」 판정도 그대로 두되, 용도 구분 한 줄만 §M1 말미에 덧붙였다(위 참조).

### ★각주(M3와 동일 의무, 재확인)
- **전환 "금액" 컬럼이 `naver_keyword_hourly`에도 없다**(`conv_cnt`만, `PRAGMA table_info` 확인 — M5-1). **CPA만 성립, ROAS·총이익은 M3와 동일하게 불성립.**
- `order_date`(결제 시각) ↔ 이 테이블의 cost/conv_cnt(클릭·전환 집계 시각) 시차는 M4와 동일하게 **미상** — 같은 hour 버킷 대응은 원리적으로 오염.
- **마지막 버킷 미완 표시**: `naver_keyword_hourly`의 MAX(ad_date)=2026-08-17(측정 당일 2026-08-18의 전날)이고, 이 테이블은 "일 1회 D-1 스윕"(모델 docstring) 방식이라 **측정 당일(08-18) 행 자체가 없다** — 즉 이 원료엔 "진행 중인 미완 버킷"이 애초에 존재하지 않는다(전량 완결일). 그럼에도 가장 최신 날짜(08-17)는 스윕된 지 하루밖에 안 됐다는 점만 밝혀둔다(재수정 가능성 여부는 미상).

### M5-1. 구조·창 실측

모델 좌표: `NaverKeywordHourly`, `backend/app/models.py:2464-2499`, 테이블 `naver_keyword_hourly`, grain `(ad_date, entity_id, hour)`.

```sql
PRAGMA table_info(naver_keyword_hourly);
SELECT COUNT(*) FROM naver_keyword_hourly;                          -- row_count
SELECT MIN(ad_date), MAX(ad_date), COUNT(DISTINCT ad_date) FROM naver_keyword_hourly;
SELECT COUNT(DISTINCT entity_id) FROM naver_keyword_hourly;          -- distinct entity(keyword+adgroup 합)
SELECT COUNT(DISTINCT adgroup_id) FROM naver_keyword_hourly;         -- ★함정, 아래 참조
SELECT entity_type, COUNT(*) FROM naver_keyword_hourly GROUP BY entity_type;
SELECT campaign_type, COUNT(*) FROM naver_keyword_hourly GROUP BY campaign_type;
SELECT entity_type, campaign_type, COUNT(*) FROM naver_keyword_hourly GROUP BY entity_type, campaign_type;
SELECT COUNT(*) FROM naver_keyword_hourly WHERE adgroup_id='__backfill__';   -- 0
SELECT COUNT(*) FROM naver_keyword_hourly WHERE entity_id='__backfill__';   -- 0
SELECT hour, COUNT(*) FROM naver_keyword_hourly GROUP BY hour ORDER BY hour;  -- 시간 커버리지
SELECT ad_date, COUNT(*), COUNT(DISTINCT hour) FROM naver_keyword_hourly GROUP BY ad_date ORDER BY ad_date;
```

| metric | value |
|---|---|
| row_count | 317,034 |
| MIN(ad_date) ~ MAX(ad_date) | **2026-07-11 ~ 2026-08-17**(38일 — **4주 요건 충족**) |
| distinct ad_date | 38 |
| entity_type='keyword' 행 | 163,340 (전부 campaign_type=WEB_SITE) |
| entity_type='adgroup' 행 | 153,694 (SHOPPING 125,181 · WEB_SITE 28,513) |
| `__backfill__` 행 | 0건(entity_id·adgroup_id 둘 다) |
| hour 커버리지 | **0~23 전 시간대 존재**(hour=23도 13,185행 — `naver_adgroup_hourly_today`의 「hour=23 전무」가 여기선 재현되지 않음, 아래 확인) |
| 2026-08-05 결측 | 16/24시간만 존재(hour 5,9~22) — `naver_adgroup_hourly_today`의 그날 결측(15/24, 다른 시간대 조합)과 **패턴이 다르다**(둘 다 결측이지만 같은 시간이 빠진 게 아님). 원인 미상. |

★**함정 확인**: `COUNT(DISTINCT adgroup_id)`(스키마상 존재하는 컬럼을 그대로 세면) = 233 — 이건 **entity_type='keyword' 행이 채운 adgroup_id만 잡힌 값**이다. `entity_type='adgroup'` 행은 **`adgroup_id` 컬럼이 전부 빈 문자열**(153,694건 전수 확인, `entity_id`에 실제 그룹ID가 들어 있다). 즉 adgroup 자신을 가리키는 컬럼은 행 종류에 따라 `adgroup_id` 또는 `entity_id` 둘 중 하나다 — 롤업 시 `CASE WHEN entity_type='adgroup' THEN entity_id ELSE adgroup_id END`로 통일해야 한다(아래 M5-2 SQL 참조). 이걸 놓치면 SHOPPING/BRAND_SEARCH 그룹 153,694행 전량이 조인에서 누락된다.

```sql
SELECT COUNT(*) FROM naver_keyword_hourly WHERE entity_type='adgroup' AND adgroup_id='';       -- 153,694 (전수)
SELECT COUNT(*) FROM naver_keyword_hourly WHERE entity_type='keyword' AND adgroup_id='';        -- 0
SELECT hour, COUNT(*) FROM naver_keyword_hourly WHERE entity_type='adgroup' AND hour=23 GROUP BY hour;
  -- adgroup행 중 hour=23: SHOPPING 5,408 · WEB_SITE 1,258 (합 6,666) — 23시도 정상 존재
```

★**이중계상 함정 확인** (adgroup 롤업 방식 결정 근거): WEB_SITE는 hh24 스윕이 **키워드 grain과 adgroup grain을 동시에** 적재한다(`entity_type='adgroup' AND campaign_type='WEB_SITE'` 28,513행이 별도로 존재). 이 adgroup 행이 그 그룹 소속 키워드 행들의 단순 합인지 확인:
```sql
SELECT entity_id, ad_date, hour, cost, conv_cnt FROM naver_keyword_hourly
WHERE entity_type='adgroup' AND entity_id='grp-a001-01-000000070512941' AND ad_date='2026-07-23' AND hour=17;
-- cost=8312, conv_cnt=0
SELECT SUM(cost), SUM(conv_cnt), COUNT(*) FROM naver_keyword_hourly
WHERE entity_type='keyword' AND adgroup_id='grp-a001-01-000000070512941' AND ad_date='2026-07-23' AND hour=17;
-- cost_sum=1231, conv_sum=0, n_keywords=8  ← adgroup행(8,312)의 14.8%에 불과
```
**일치하지 않는다** — 두 grain을 함께 더하면 이중계상이 아니라 **표본이 왜곡된 과다계상**이 된다. 그래서 M5의 모든 168칸 산출은 **`entity_type='adgroup'` 행만** 사용했다(WEB_SITE도 SHOPPING/BRAND_SEARCH와 동일하게 그룹 자체 집계행을 쓴다 — keyword grain은 이번 산출에서 제외).

### M5-2. 168칸 CPA — 실행 SQL

```sql
-- band_map: 기존 CSV 854행 TEMP TABLE 재사용

CREATE TEMP TABLE kw_roll AS
SELECT ad_date, hour, cost, conv_cnt, campaign_type,
       entity_id AS agid,                    -- ★entity_type='adgroup' 전용이므로 entity_id가 곧 adgroup_id
       CAST((strftime('%w', ad_date) + 6) % 7 AS INTEGER) AS py_weekday
FROM naver_keyword_hourly
WHERE entity_type = 'adgroup' AND entity_id <> '__backfill__';

-- BASELINE(밴드 미분리) 168칸 — 전체창 / 4주창 두 판
SELECT py_weekday, hour, SUM(cost), SUM(conv_cnt), COUNT(DISTINCT ad_date),
       CASE WHEN SUM(conv_cnt)>0 THEN ROUND(CAST(SUM(cost) AS REAL)/SUM(conv_cnt),0) ELSE NULL END AS cpa
FROM kw_roll GROUP BY py_weekday, hour ORDER BY py_weekday, hour;                       -- 전체창(38일)

SELECT py_weekday, hour, SUM(cost), SUM(conv_cnt), COUNT(DISTINCT ad_date), ...
FROM kw_roll WHERE ad_date >= '2026-07-21' AND ad_date <= '2026-08-17'
GROUP BY py_weekday, hour ORDER BY py_weekday, hour;                                    -- 4주창(28일)

-- BAND 층화(band1/2/3, 전체창) — band별 동일 패턴
SELECT bm.band, k.py_weekday, k.hour, SUM(k.cost), SUM(k.conv_cnt), COUNT(DISTINCT k.ad_date),
       CASE WHEN SUM(k.conv_cnt)>0 THEN ROUND(CAST(SUM(k.cost) AS REAL)/SUM(k.conv_cnt),0) ELSE NULL END AS cpa
FROM kw_roll k JOIN band_map bm ON bm.adgroup_id = k.agid
WHERE bm.band = 'band1' GROUP BY k.py_weekday, k.hour ORDER BY k.py_weekday, k.hour;

-- 캠페인 유형 층화(SHOPPING/WEB_SITE, 전체창) — 동일 패턴에 campaign_type 필터
SELECT campaign_type, py_weekday, hour, SUM(cost), SUM(conv_cnt), COUNT(DISTINCT ad_date), ...
FROM kw_roll WHERE campaign_type='SHOPPING' GROUP BY py_weekday, hour ORDER BY py_weekday, hour;
-- WEB_SITE 동일
```

### M5-3. 매칭률(band_map 대비)

```sql
SELECT COUNT(DISTINCT agid) FROM kw_roll;                                            -- 338
SELECT COUNT(DISTINCT CASE WHEN agid IN (SELECT adgroup_id FROM band_map) THEN agid END) FROM kw_roll;  -- 338
SELECT SUM(cost) FROM kw_roll;                                                       -- 21,889,671
SELECT SUM(CASE WHEN agid IN (SELECT adgroup_id FROM band_map) THEN cost ELSE 0 END) FROM kw_roll;  -- 21,889,671
```

| metric | value |
|---|---|
| kw_roll distinct adgroup(agid) 수(전체창, entity_type=adgroup만) | 338 (SHOPPING 236 · WEB_SITE 102) |
| band_map에 매칭된 adgroup 수 | 338 (**100.0%**) |
| kw_roll 비용 합계 | 21,889,671원 |
| band_map에 매칭된 비용 | 21,889,671원 (**100.0%**) |
| 매칭 안 된 adgroup(top 20 by cost) | **0건** — 미매칭 목록 쿼리 결과 공행(空行) |

M3(hourly_today 14일 창, 214개 활성 adgroup 전량 매칭)와 마찬가지로 **전량 매칭** — band CSV(391일 창 정본)가 현재 활동 중인 그룹을 빠짐없이 덮고 있다.

### M5-4. 관측 표 — BASELINE 168칸 (요약, 원자료는 `raw_m5_baseline_full.txt`/`raw_m5_baseline_4wk.txt`)

**전체창(2026-07-11~2026-08-17, 38일, n_days=4~6/칸)** — 발췌(hour 0,12,23 × 요일 0/3/6):
```
wd hr  cost     conv  n_days  cpa
0  0   116,262   36   6   3,230
0  12  189,588   37   6   5,124
0  23  160,470   22   6   7,294
3  0    88,848   12   5   7,404
3  12  112,331   16   5   7,021
3  23  168,927   33   5   5,119
6  0   126,121   33   6   3,822
6  12  168,016   31   6   5,420
6  23  176,297   43   6   4,100
```
(168행 전체는 `docs/references/data/74_band_x_time_brand/raw_m5_baseline_full.txt`.)

**4주창(2026-07-21~2026-08-17, 28일, n_days=3~4/칸)** — 같은 발췌 셀:
```
wd hr  cost     conv  n_days  cpa
0  0    83,432   27   4   3,090
0  12  126,465   29   4   4,361
0  23  118,168   18   4   6,565
3  0    54,589   12   4   4,549
3  12   86,608   16   4   5,413
3  23  151,925   33   4   4,604
6  0    88,734   21   4   4,225
6  12  123,892   28   4   4,425
6  23  126,929   36   4   3,526
```
(168행 전체는 `raw_m5_baseline_4wk.txt`. 두 판 모두 hour=23 칸이 **정상 존재** — M3에서 지적한 구조적 결측이 이 원료에는 없다.)

### M5-5. 관측 표 — BAND 층화 168칸 (전체창, 요약)

원자료 전량(각 168행): `raw_m5_band1_full.txt` · `raw_m5_band2_full.txt` · `raw_m5_band3_full.txt`.

밴드 총계(168칸 합산, 검산용 — **밴드 간 합산 자체는 여전히 부정확하지 않다**: band CSV 정본이 adgroup을 밴드 배타적으로 분류하므로 M2 커머스 fan-out과 달리 여기선 한 adgroup=한 band다):

| band | n_agid(전체창) | cost_sum | conv_sum | CPA |
|---|---|---|---|---|
| band1 | 105 | 8,094,685 | 1,411 | 5,737 |
| band2 | 45 | 3,244,392 | 507 | 6,399 |
| band3 | 150 | 10,493,147 | 1,514 | 6,931 |
| band4_unjudgeable | 36 | 57,447 | 6 | 9,575 |
| excluded_cost0 | 2 | 0 | 0 | — |
| **합계** | 338 | **21,889,671** | **3,438** | — |

4주창(28일) 밴드 총계(교차검증용, 168칸 미전개):

| band | n_agid | cost_sum | conv_sum | CPA |
|---|---|---|---|---|
| band1 | 103 | 5,762,568 | 1,260 | 4,573 |
| band2 | 45 | 2,492,050 | 463 | 5,382 |
| band3 | 139 | 8,376,221 | 1,461 | 5,733 |
| band4_unjudgeable | 20 | 28,988 | 6 | 4,831 |
| excluded_cost0 | 1 | 0 | 0 | — |

### M5-6. 관측 표 — 캠페인 유형 층화(SHOPPING / WEB_SITE, 전체창)

원자료 전량: `raw_m5_shopping_full.txt` · `raw_m5_website_full.txt`.

| metric | SHOPPING | WEB_SITE |
|---|---|---|
| 총 비용(38일) | 20,244,111 | 1,645,560 |
| 총 전환건수 | 3,259 | 179 |
| distinct adgroup | 236 | 102 |

SHOPPING이 비용의 92.5%(20,244,111/21,889,671)를 차지 — 이 저장소의 광고비 대부분이 쇼핑 지면에 있다는 것은 이미 다른 실측(D-NAO-187 등)에서도 나온 사실과 같은 방향.

### M5-7. 대조 — `naver_adgroup_hourly_today` vs `naver_keyword_hourly` (겹치는 구간)

★이게 M5 전체 원료 신뢰의 유일한 근거다. 겹치는 구간(2026-08-04~2026-08-17, hour 0~22 — hourly_today엔 23시가 없으므로 공정 비교는 0~22로 제한) 동일 `(adgroup_id, ad_date, hour)`에서 두 테이블의 cost·conv_cnt를 직접 대조.

```sql
CREATE TEMP TABLE hot AS
SELECT adgroup_id, ad_date, hour, cost AS cost_hot, conv_cnt AS conv_hot
FROM naver_adgroup_hourly_today
WHERE adgroup_id <> '__backfill__' AND ad_date >= '2026-08-04' AND ad_date <= '2026-08-17';

CREATE TEMP TABLE kwh AS
SELECT entity_id AS adgroup_id, ad_date, hour, cost AS cost_kwh, conv_cnt AS conv_kwh
FROM naver_keyword_hourly
WHERE entity_type='adgroup' AND entity_id <> '__backfill__'
  AND ad_date >= '2026-08-04' AND ad_date <= '2026-08-17';

CREATE TEMP TABLE joined AS
SELECT h.adgroup_id, h.ad_date, h.hour, h.cost_hot, h.conv_hot, k.cost_kwh, k.conv_kwh
FROM hot h JOIN kwh k ON k.adgroup_id=h.adgroup_id AND k.ad_date=h.ad_date AND k.hour=h.hour;

SELECT COUNT(*) FROM hot;                                                            -- 45,674
SELECT COUNT(*) FROM kwh WHERE hour<=22;                                             -- 45,802
SELECT COUNT(*) FROM joined;                                                         -- 45,674 (=hot 전량 매칭)
SELECT COUNT(*) FROM hot h WHERE NOT EXISTS (SELECT 1 FROM kwh k WHERE k.adgroup_id=h.adgroup_id AND k.ad_date=h.ad_date AND k.hour=h.hour);  -- 0
SELECT COUNT(*) FROM kwh k WHERE k.hour<=22 AND NOT EXISTS (SELECT 1 FROM hot h WHERE h.adgroup_id=k.adgroup_id AND h.ad_date=k.ad_date AND h.hour=k.hour);  -- 128
SELECT COUNT(*) FROM joined WHERE cost_hot=cost_kwh;                                 -- 45,674 (전량)
SELECT COUNT(*) FROM joined WHERE cost_hot<>cost_kwh;                                -- 0
SELECT SUM(cost_hot), SUM(cost_kwh), SUM(ABS(cost_hot-cost_kwh)) FROM joined;         -- 7,589,892 | 7,589,892 | 0
SELECT COUNT(*) FROM joined WHERE conv_hot=conv_kwh;                                 -- 45,674 (전량)
SELECT SUM(conv_hot), SUM(conv_kwh) FROM joined;                                     -- 1,579 | 1,579
```

| metric | value |
|---|---|
| hourly_today 행수(창 내, hour≤22) | 45,674 |
| keyword_hourly(adgroup행, hour≤22) 행수 | 45,802 |
| 조인 성공(양쪽 다 존재) | 45,674 |
| hourly_today에만 있고 keyword_hourly에 없음 | **0건** |
| keyword_hourly에만 있고 hourly_today에 없음 | **128건**(추가 커버리지 — 활동 그룹 집합이 완전히 같지는 않다) |
| cost 완전 일치 행 | **45,674 / 45,674 (100.0%)** |
| cost 불일치 행 | **0건** |
| cost 합계 차이 | **0원**(7,589,892 = 7,589,892) |
| conv_cnt 완전 일치 행 | **45,674 / 45,674 (100.0%)** |
| conv_cnt 합계 차이 | **0건**(1,579 = 1,579) |

**판정: 어긋남 없음.** 겹치는 전체 구간에서 cost·conv_cnt가 **행 단위로 완전히 일치**한다(방향성 있는 편차조차 없음, 절대 0). `naver_keyword_hourly`(entity_type='adgroup')가 `naver_adgroup_hourly_today`의 상위 호환(같은 값 + 더 긴 창 + 23시 포함 + 128행 추가 커버리지)임을 라이브로 확인했다.

### M5-8. `models.py` 전수 확인 — 다른 후보 테이블 존재 여부

```bash
grep -n "^class \|hour: Mapped\|conv_cnt: Mapped\|conv_amt: Mapped" backend/app/models.py
```
`hour:` 컬럼을 가진 클래스는 5개(`NaverHourlySnapshot`·`NaverKeywordHourly`·`NaverAdgroupHourlyToday`·`NaverHourlyPatternHistory`·무관한 `alert_hour` 필드 1건). 이 중 **`conv_cnt`(또는 `conv_amt`) 컬럼을 동시에 가진 것은 `NaverKeywordHourly`와 `NaverAdgroupHourlyToday` 단 둘뿐**이다(`NaverHourlySnapshot`·`NaverHourlyPatternHistory`엔 전환 컬럼 자체가 없음, 위 M1과 동일 결함).

**답: 「168칸×4주+×adgroup grain×전환건수」 4조건을 동시에 만족하는 테이블은 `naver_keyword_hourly` 1개뿐이다.** `naver_adgroup_hourly_today`는 grain·전환건수는 만족하나 창이 15일(완결 14일)로 4주 미달이라 조건 미충족. 그 외 테이블은 없음.

---

## 이 실측이 답하지 못한 것

1. ~~band 층화된 다주(4주+) CPA는 어느 테이블로도 지금 당장 안 나온다.~~ **→ M5에서 해소.** `naver_keyword_hourly`(entity_type='adgroup' 행)로 38일 창(2026-07-11~08-17) 168칸 band1/2/3 CPA를 산출했고, `naver_adgroup_hourly_today`와의 겹치는 구간(14일·hour 0~22) 교차검증에서 **cost·conv_cnt 완전 일치(45,674/45,674행, 불일치 0)**를 확인했다. 상세는 §M5.
2. **2026-08-05 hour 8칸 결측의 원인**(수집 실패 vs 전 그룹 실적 0)은 이 자료만으로 구분 불가.
3. **`naver_adgroup_hourly_today`에 hour=23이 전무한 이유**는 코드 추적으로 확인하지 않았다(이번 과제는 데이터 실측 전용, 코드 원인 규명은 스코프 밖).
4. **M2 커머스 창(90일)과 M3 광고 창(14일)이 길이가 다르다** — 같은 hour 버킷이라도 두 표를 나란히 놓고 뭔가를 나누거나 비율을 계산하면 안 된다(요청받지 않았고, 하지도 않았다).
5. **클릭→구매 리드타임 분포**는 이 저장소 어디에도 적재돼 있지 않다(M4에서 확인) — 같은 hour의 광고비와 주문금액을 대응시키는 어떤 연산도 원리적으로 근거가 없다.
6. **status 필터링(취소·반품 포함 여부)에 따른 M2 민감도**는 계산하지 않았다(지시에 없었음) — 위 status 분포(취소 517·반품 100 등, channel=6 전체)만 참고로 남긴다.
7. band CSV(`band_group_total.csv`, 391일 창, 계정 블렌디드 BEP)와 M3의 14일 창은 **시간 축이 다르다** — band 판정 당시의 활동과 지금 창(08-04~08-17)의 활동이 같은 그룹 집합이라는 보장은 검증하지 않았다(214개 활성 adgroup 전량이 band_map에 매칭됐다는 사실만 확인, band 배정 자체의 최신성은 미검증). **M5도 동일 결 미검증**(38일 창 vs band CSV 391일 창, 338개 adgroup 전량 매칭만 확인).
8. **2026-08-05의 두 결측 패턴이 다른 이유**(M5): `naver_adgroup_hourly_today`는 그날 hour 5,9~22(15칸)만 있고, `naver_keyword_hourly`는 hour 5,9~22(16칸, 미세하게 다름)만 있다 — 정확한 차집합은 계산하지 않았다. 수집 파이프라인이 다른데(hh24 스윕 vs 페이싱류) 결측 패턴이 유사하다는 것 자체가 그날 네이버 API 쪽 이슈였을 가능성을 시사하나, **원인 확인은 스코프 밖**.
9. **`naver_keyword_hourly`의 128행(hourly_today엔 없고 keyword_hourly에만 있는 (adgroup,date,hour))이 왜 생기는지**는 확인하지 않았다 — 활동 그룹 로스터(observation_campaign_ids)가 두 수집 경로에서 완전히 같지 않을 가능성이 있으나 코드 추적은 스코프 밖.
10. **밴드 4주창/전체창 CPA 차이의 방향성**(예: band1 CPA 4주창 4,573원 vs 전체창 5,737원)은 순수 관측값 병기만 했다 — 두 창의 차이가 추세인지 표본 변동인지 **해석하지 않았다**(지시대로).
11. **SHOPPING이 WEB_SITE보다 CPA 계산이 더 안정적인지**(표본 크기: SHOPPING 3,259건 vs WEB_SITE 179건 전환) 등 통계적 신뢰도 판정은 하지 않았다 — 건수만 병기.
