# 커머스 축 × 성과등급 교차 (D-NAO-194 슬라이스 2, 2026-08-18 KST)

> 담당: C6 정산 건별×등급(3부 순위1) · C1 주문×등급(3부 순위2) · +α F5 후속(미매핑 11그룹 정체). 해석·권고 없음 — 숫자·SQL·제약만.
> 측정 시각: 2026-08-18 12:53 KST 전후(각 쿼리 실행 시점, 셀별 병기). **읽기 전용** — prod 쓰기·배포·커밋·네이버 API 호출 0건.
> 등급 정본: `docs/references/data/63_band_decomposition/band_group_total.csv`(854행, adgroup_id 유니크, 391일 창). 커머스 값은 전부 **상한 프록시**(ref 72 §1) — 광고 귀속 매출이 아니라 그 상한.

---

## 0. 공통 방법 — 어떻게 조인했는가 (재현 필수 정보)

**prod 쓰기 금지 제약 때문에 band CSV를 prod에 올리지 않았다.** 대신 prod에서 원자료 3종을 `sqlite3 -readonly`로 SELECT해 로컬 CSV로 내려받고, 로컬에 임시 sqlite db(`local.db`)를 만들어 band CSV와 함께 `.import`한 뒤 로컬에서 조인했다. prod 쪽은 SELECT만 실행(쓰기 0), band CSV는 이미 존재하던 repo 파일을 그대로 복사만 했다(생성 안 함).

```bash
SP=<scratchpad>
ssh -o BatchMode=yes sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db" < extract_settlement.sql > settlement_case.csv
ssh -o BatchMode=yes sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db" < extract_map.sql        > adgroup_product.csv
ssh -o BatchMode=yes sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db" < extract_orders90.sql   > orders_90d.csv
ssh -o BatchMode=yes sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db" < extract_orders_full.sql > orders_full.csv
cp docs/references/data/63_band_decomposition/band_group_total.csv "$SP/band_group_total.csv"
sqlite3 "$SP/local.db" <<'EOF'
.mode csv
.import band_group_total.csv band_group_total
.import adgroup_product.csv  adgroup_product
.import settlement_case.csv  settlement_case
.import orders_90d.csv       orders_90d
.import orders_full.csv      orders_full
EOF
```

prod SELECT 4종의 전문(추출 조건):

```sql
-- extract_settlement.sql
SELECT product_order_id, product_id, product_order_type, settle_type,
       pay_settle_amount, total_pay_commission, selling_interlock_commission,
       settle_expect_amount, pay_date
FROM naver_settlement_case
WHERE pay_date >= '2026-04-21' AND pay_date <= '2026-08-17';

-- extract_map.sql
SELECT adgroup_id, campaign_id, mall_product_id FROM naver_adgroup_product;

-- extract_orders90.sql
SELECT id, order_number, platform_product_id, selling_price, order_date, status
FROM orders
WHERE channel_id=6 AND order_date >= datetime('now','+9 hours','-90 day');

-- extract_orders_full.sql
SELECT id, order_number, platform_product_id, selling_price, order_date, status
FROM orders
WHERE channel_id=6 AND order_date >= '2026-02-12';
```

측정 시점(2026-08-18 12:5x KST) 행수: `settlement_case.csv` 11,730행 · `adgroup_product.csv` 1,761행 · `orders_90d.csv` 8,452행 · `orders_full.csv` 12,374행 · `band_group_total.csv` 854행(변경 없음, repo 파일).
⚠️ `MEASUREMENTS_slice1_20260818.md`(실측 A·C, 측정 시각 더 이름)의 orders 행수(8,445·12,366)와 **소폭 다르다** — 시간 경과로 신규 주문이 몇 건 더 들어온 정상 드리프트다(재현 시 이 문서 실행 시각 기준 숫자가 다시 바뀔 수 있음).

**연결 경로**: `product_id`/`platform_product_id` → `naver_adgroup_product.mall_product_id` → `adgroup_id` → `band_group_total.band`(campaign_type='SHOPPING' 한정, WEB_SITE는 매핑 0%로 실측됨 — MEASUREMENTS §C-2).

### ⚠️ 새로 발견한 제약 — «다중 밴드 팬아웃» (이 문서 작성 중 실측, 사전 문서에 없던 사실)

`naver_adgroup_product`는 `UNIQUE(adgroup_id, mall_product_id)`이지 `mall_product_id` 단독 유니크가 아니다 — **한 상품이 여러 adgroup_id에 동시에 걸려 있고, 그 adgroup들이 서로 다른 밴드에 속하는 경우가 있다.**

```sql
WITH map_band AS (
  SELECT ap.mall_product_id, b.band
  FROM adgroup_product ap
  JOIN band_group_total b ON b.adgroup_id = ap.adgroup_id AND b.campaign_type='SHOPPING'
)
SELECT COUNT(DISTINCT mall_product_id) total_mapped_products,
       SUM(CASE WHEN nbands>1 THEN 1 ELSE 0 END) multi_band_products
FROM (SELECT mall_product_id, COUNT(DISTINCT band) nbands FROM map_band GROUP BY mall_product_id);
```
| total_mapped_products | multi_band_products |
|---|---|
| 702 | **390 (55.6%)** |

SHOPPING 매핑 상품 702개 중 390개(55.6%)가 2개 이상의 밴드에 걸쳐 있다(대부분 band3+band1 조합, 일부 3~4개 밴드). **이 문서의 band × 커머스 rollup은 그대로 조인하면 밴드마다 같은 정산액·주문액이 중복 계상된다** — «상한 프록시»(광고 귀속 아님)라는 원래 제약 위에 얹히는 **추가 제약**이다. 아래 각 표는 ①밴드 fan-out 그대로의 값(raw) ②fan-out 전 baseline(상품/건 단위 유니크 매칭 총액) 둘 다 병기해 인플레이션 배율을 드러낸다. **밴드 간 합산은 baseline을 초과한다 — 밴드별 값을 그대로 더해 「총 광고 귀속 매출」로 쓰면 안 된다.**

---

## 1. C6 — 정산 건별(`naver_settlement_case`) × 등급

**창**: pay_date 2026-04-21 ~ 2026-08-17 (약 4개월) · **SHOPPING 한정** · **상한 프록시**(그 상품의 그날 전체 판매채널 성과이지 이 광고그룹 귀속이 아니다) · **다중밴드 fan-out 있음**(위 §0).

### baseline (fan-out 전, 상품 매칭 유니크 건수)
```sql
SELECT COUNT(*) rows_matched_product_level,
       COUNT(DISTINCT s.product_order_id) distinct_cases,
       ROUND(SUM(s.settle_expect_amount),0) amt_matched_baseline
FROM settlement_case s
JOIN (SELECT DISTINCT mall_product_id FROM adgroup_product) m
  ON m.mall_product_id = s.product_id;
```
| rows_matched_product_level | distinct_cases | amt_matched_baseline(원) |
|---|---|---|
| 8,740 | 8,740 | 139,177,309 |

(MEASUREMENTS §C-3의 8,740행/139,177,309원과 일치 — 동일 조건 재실행 확인.)

### 밴드별 rollup (raw, fan-out 포함)
```sql
SELECT b.band,
       COUNT(*) n_case_rows,
       COUNT(DISTINCT s.product_order_id) n_distinct_cases,
       ROUND(SUM(s.settle_expect_amount),0) settle_expect_sum,
       ROUND(SUM(s.pay_settle_amount),0) pay_settle_sum,
       ROUND(SUM(s.total_pay_commission),0) commission_sum,
       ROUND(SUM(s.selling_interlock_commission),0) interlock_commission_sum,
       ROUND(100.0*SUM(s.total_pay_commission)/NULLIF(SUM(s.pay_settle_amount),0),2) effective_commission_rate_pct
FROM settlement_case s
JOIN adgroup_product ap ON ap.mall_product_id = s.product_id
JOIN band_group_total b ON b.adgroup_id = ap.adgroup_id AND b.campaign_type='SHOPPING'
GROUP BY b.band
ORDER BY settle_expect_sum DESC;
```

| band | 건수(n_case_rows, fan-out 포함) | 유니크 건(n_distinct_cases) | settle_expect_amount 합(원) | pay_settle_amount 합(원) | total_pay_commission 합(원) | 실효수수료율(%) | selling_interlock_commission 합(원) |
|---|---|---|---|---|---|---|---|
| band3 | 12,817 | 6,644 | 203,378,308 | 212,761,010 | −5,795,076 | −2.72 | −3,587,626 |
| band1 | 12,235 | 7,448 | 194,067,512 | 202,785,710 | −5,523,623 | −2.72 | −3,194,575 |
| band2 | 3,203 | 2,563 | 49,838,382 | 52,115,300 | −1,419,298 | −2.72 | −857,620 |
| band4_unjudgeable | 226 | 226 | 3,573,466 | 3,742,600 | −101,959 | −2.72 | −67,175 |
| excluded_cost0 | 15 | 15 | 229,303 | 238,500 | −6,494 | −2.72 | −2,703 |

**합계(밴드 합, fan-out 포함)**: 건수 28,496 · settle_expect_amount 451,086,971원. **baseline(139,177,309원) 대비 3.241배** — fan-out 인플레이션.

- 실효수수료율(=total_pay_commission/pay_settle_amount)이 5개 밴드 전부 **−2.72%로 동일**하다 — DB에 commission이 pay_settle_amount에 대한 고정 비율(음수=차감)로 저장되는 구조로 보이며, 밴드 간 차이가 없다(구조적 관측, 원인 코드 대조는 이번 범위 밖).
- 부호: `total_pay_commission`·`selling_interlock_commission`은 DB 원값이 음수(차감 항목)다. 부호를 바꾸지 않고 그대로 실었다.

---

## 2. C1 — 주문(`orders`, channel_id=6) × 등급

**창**: 90일과 전체(2026-02-12~) **두 표로 분리**(C6과 이중계상 금지 — 정산=확정, 주문=발생) · **SHOPPING 한정** · **상한 프록시** · **다중밴드 fan-out 있음**(§0).

### 2-A. 창 = 최근 90일

baseline:
```sql
SELECT COUNT(*) rows_matched, ROUND(SUM(o.selling_price),0) amt_matched
FROM orders_90d o
JOIN (SELECT DISTINCT mall_product_id FROM adgroup_product) m
  ON m.mall_product_id = o.platform_product_id;
```
| rows_matched | amt_matched(원) |
|---|---|
| 8,220 | 135,747,410 |

밴드별 rollup:
```sql
SELECT b.band,
       COUNT(*) n_order_rows,
       COUNT(DISTINCT o.id) n_distinct_orders,
       COUNT(DISTINCT o.platform_product_id) n_distinct_products,
       ROUND(SUM(o.selling_price),0) selling_price_sum
FROM orders_90d o
JOIN adgroup_product ap ON ap.mall_product_id = o.platform_product_id
JOIN band_group_total b ON b.adgroup_id = ap.adgroup_id AND b.campaign_type='SHOPPING'
GROUP BY b.band
ORDER BY selling_price_sum DESC;
```

| band | 주문 행수(fan-out 포함) | 유니크 주문(n_distinct_orders) | 상품 수 | selling_price 합(원) |
|---|---|---|---|---|
| band3 | 11,844 | 6,259 | 289 | 193,695,580 |
| band1 | 11,405 | 6,873 | 313 | 187,254,310 |
| band2 | 2,898 | 2,287 | 94 | 46,414,640 |
| band4_unjudgeable | 220 | 220 | 20 | 3,628,540 |
| excluded_cost0 | 10 | 10 | 3 | 159,000 |

**합계(밴드 합)**: 행수 26,377 · selling_price 합 431,152,070원. **baseline(135,747,410원) 대비 3.176배**.

### 2-B. 창 = 전체(2026-02-12 ~ 측정일)

baseline:
```sql
SELECT COUNT(*) rows_matched, ROUND(SUM(o.selling_price),0) amt_matched
FROM orders_full o
JOIN (SELECT DISTINCT mall_product_id FROM adgroup_product) m
  ON m.mall_product_id = o.platform_product_id;
```
| rows_matched | amt_matched(원) |
|---|---|
| 11,994 | 202,559,110 |

밴드별 rollup(쿼리는 2-A와 동일, 테이블만 `orders_full`):

| band | 주문 행수(fan-out 포함) | 유니크 주문(n_distinct_orders) | 상품 수 | selling_price 합(원) |
|---|---|---|---|---|
| band3 | 17,712 | 9,170 | 388 | 295,832,290 |
| band1 | 16,494 | 10,133 | 372 | 277,276,020 |
| band2 | 4,467 | 3,575 | 103 | 73,442,970 |
| band4_unjudgeable | 318 | 318 | 21 | 5,325,760 |
| excluded_cost0 | 19 | 19 | 6 | 305,100 |

**합계(밴드 합)**: 행수 39,010 · selling_price 합 652,182,140원. **baseline(202,559,110원) 대비 3.220배**.

⚠️ 전체창(2026-02-12~)은 등급 창(391일, ref 63 기준일 역산 시 대략 2025-08 시작)의 **약 절반 이하**다(MEASUREMENTS F6 승계) — 등급이 보는 기간 전체를 못 덮는다.

---

## 3. +α — 미매핑 SHOPPING 11그룹의 정체 (F5 후속)

MEASUREMENTS §C-2 쿼리(`GROUP BY adgroup_id, campaign_type`, 90일)를 그대로 재실행해 `WHERE m.adgroup_id IS NULL AND a.campaign_type='SHOPPING'`으로 파생:

```sql
WITH a AS (
  SELECT adgroup_id, campaign_type, SUM(cost) cost FROM naver_ad_daily
  WHERE adgroup_id<>'' AND ad_date >= date('now','+9 hours','-90 day')
  GROUP BY adgroup_id, campaign_type
), m AS (SELECT DISTINCT adgroup_id FROM naver_adgroup_product)
SELECT a.adgroup_id, a.campaign_type, a.cost
FROM a LEFT JOIN m ON m.adgroup_id=a.adgroup_id
WHERE m.adgroup_id IS NULL AND a.campaign_type='SHOPPING'
ORDER BY a.cost DESC;
```

11행 확인(원래 매트릭스의 239−228=11과 일치). 90일 cost 합 = 24,910,995원(원 매트릭스 F5의 숫자와 일치).

| adgroup_id | 90일 cost(원) | 90일 imp | 90일 clk | 90일 conv | 391일 band(있으면) | 391일 cost(band CSV) | naver_entity 이름 | status | reg_tm |
|---|---|---|---|---|---|---|---|---|---|
| `__backfill__` | **23,624,764** | — | — | — | (밴드 대상 아님 — 실 adgroup 아님) | — | (엔티티 아님) | — | — |
| grp-a001-02-000000052959049 | 624,684 | 44,910 | 583 | 14 | band3 | 727,237 | "07. 맥세이프카드지갑" | off | 2025-07-28 |
| grp-a001-02-000000070109616 | 295,745 | 15,228 | 310 | 3 | band3 | 295,745 | "맥세이프_MO" | off | 2026-07-13 |
| grp-a001-02-000000069089452 | 100,708 | 9,823 | 129 | 1 | band3 | 100,708 | "맥세이프카드지갑" | **deleted** | — |
| grp-a001-02-000000069089475 | 58,424 | 4,364 | 87 | 3 | band3 | 58,424 | "멕세이프카드지갑" | **deleted** | — |
| grp-a001-02-000000069087677 | 58,111 | 7,016 | 78 | 0 | band3 | 58,111 | "기존상품명" | **deleted** | — |
| grp-a001-02-000000070108611 | 55,087 | 2,665 | 46 | 0 | band3 | 55,087 | "맥세이프_PC" | off | 2026-07-13 |
| grp-a001-02-000000048079095 | 42,919 | 2,797 | 27 | 1 | band3 | 302,522 | "01. 아이폰16e" | off | 2025-02-21 |
| grp-a001-02-000000054617822 | 25,892 | 3,234 | 17 | 7 | band2 | 6,268,292 | "01. 강화유리" | **on** | 2025-09-11 |
| grp-a001-02-000000069089465 | 24,661 | 2,665 | 31 | 0 | band4_unjudgeable | 24,661 | "맥세이프지갑" | **deleted** | — |
| grp-a001-02-000000054617868 | 0 | 74 | 0 | 0 | band3 | 1,764,560 | "02. 사생활" | **on** | 2025-09-11 |

재현 SQL(band·이름 조회):
```sql
-- band: docs/references/data/63_band_decomposition/band_group_total.csv에서 adgroup_id로 grep
-- 이름·status: prod
SELECT entity_id, entity_type, campaign_id, campaign_type, name, status, reg_tm, edit_tm
FROM naver_entity
WHERE entity_id IN ('grp-a001-02-000000052959049', ...);
```

### 관찰만 (확정 판정 아님)

- **90일 cost의 94.8%(23,624,764원/24,910,995원)는 `__backfill__` 1행이다.** 이건 「매핑 누락」이 아니라 **구조적으로 매핑 대상이 아닌 행**이다 — `backend/app/services/naver_ad/campaign_backfill.py:21`의 `BACKFILL_SENTINEL_ADGROUP`으로, 코드베이스 전역(9개 파일 이상)에서 "campaign-grain(/stats 소스) 집계를 adgroup 실단위와 안 섞이게 구분하는 센티널"로 문서화돼 있다. 애초에 실 adgroup_id가 아니므로 `naver_adgroup_product`에 나올 수 없다. **F5의 "40% 농도"는 사실상 이 1행이 대부분을 설명한다** — 나머지 10개 실그룹의 합은 1,286,231원(90일 cost 24,910,995 − 23,624,764)으로, 매핑된 228그룹 몫(37,442,453원)의 3.4%에 불과하다.
- **실그룹 10개는 전부 `status ∈ {off, deleted}`다 — `on`은 0개.** `naver_adgroup_product`가 (추정컨대) 현재 활성 상품-그룹 연동만 반영하는 표면이라면, 최근에 껐거나 지운 그룹이 90일 성과 이력(naver_ad_daily)에는 남아 있으면서 현재 매핑 테이블에는 안 잡히는 모양과 일치한다 — **이 문서는 그 인과를 코드로 확인하지 않았으므로 「일치하는 모양」까지만 적는다.**
- 이름이 "맥세이프카드지갑"/"맥세이프지갑"/"멕세이프카드지갑"/"기존상품명"(cmp-a001-02-000000010769985, deleted 4건)처럼 **유사·중복 이름으로 흩어져 있다** — 그룹 정리/재편성 과정에서 남은 잔재로 보이나, 신규 램프업인지 정리 잔재인지는 **미상**이다(reg_tm이 deleted 4건은 NULL이라 생성 시점을 못 봄).
- 두 그룹(`054617822` "01. 강화유리", `054617868` "02. 사생활")은 **status='on'인데 90일 cost가 거의 0(각 25,892원·0원)이지만 391일 cost는 6,268,292원·1,764,560원**으로 크다 — 활성 그룹이 최근 90일만 조용한 경우다. 신규 램프업이 **아니다**(391일 창에 이미 큰 이력이 있음). 최근 정지·예산 소진·기타 사유는 **미상**.
- 종합: 11그룹 정체는 **①구조적 비대상(센티널) 1건 + ②비활성(off/deleted) 그룹 8건 + ③활성인데 최근 조용한 그룹 2건**으로 나뉜다. 「매핑 누락」(버그성)이라 부를 수 있는 것은 이 중 없다 — 전부 상태·구조로 설명된다. 다만 이 결론은 `naver_adgroup_product`가 "현재 활성 연동만 반영"이라는 가정 위에 있고, **그 가정 자체는 코드 대조로 확정하지 않았다** — 미상으로 남긴다.

---

*작성: Sonnet(교차 계산 담당), 2026-08-18. 해석·권고 없음. 산출물은 전부 위 SQL의 재실행으로 재현 가능(로컬 조인, prod는 SELECT만).*
