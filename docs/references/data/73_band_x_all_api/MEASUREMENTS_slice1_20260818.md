# 슬라이스 1 실측 원자료 (D-NAO-194, 2026-08-18 KST) — 조립·해석 전의 날것

> 이 파일은 **실측 산출물을 원문 그대로 모은 것**이다. 조립·경향·연관 해석은 여기 없다(그건 Fable 몫, 앵커 「위임 규약」).
> 실측 3주체: ①커머스 원료 실측(Sonnet) ②광고 미교차 10축 실측(Sonnet) ③조인 매칭률(코디네이터 인라인 — ①이 API 오류로 중도 사망해 재실행).
> 전부 **읽기 전용**: prod `sqlite3 -readonly`, 네이버 API 호출 0, prod 쓰기 0.

---

## A. 커머스 API 축별 적재 현황 (실측 ①, 원문)

| 축 | 우리 코드가 부르는가(파일:줄) | 적재 테이블 | prod 행수·기간 | 상품/광고그룹 연결 키 |
|---|---|---|---|---|
| **주문**(last-changed-statuses + query, 조합) | ✅ `backend/app/clients/naver.py:220`(`fetch_orders`) → `backend/app/services/sync_service.py:289` 호출 | **`orders`**(channel_id=6=NAVER) | **12,366행** (2026-02-12~2026-08-18), 최근30일 3,051행 / 최근14일 1,525행 | `orders.platform_product_id`(네이버 상품번호, 예 `9645476434`) 존재 — 링크 가능 |
| **주문 대기**(fetch_pending_orders, `naver.py:636`) | ✅ `routers/naver_ops.py:980` | **없음(온디맨드)** — 라우터 docstring "DB 저장 없이 라이브 반환" | — | — |
| **클레임**(fetch_claims, `naver.py:757`) | ✅ `routers/naver_ops.py:1101` | **없음(온디맨드)** — 라우터 docstring(`get_claims`, naver_ops.py:1096) "DB 저장 없이 라이브 반환" 명시 | — | — |
| **클레임 정산 프로브**(fetch_case_settlement_by_order, `naver.py:446`) | ✅ `naver_claim_settlement_probe.py:212`, 크론 `run_naver_nbaesong_return_probe` 06:02 KST | **`naver_claim_settlement_probe`**(append-only 관측 로그) | **1,069행** (observed_date 2026-08-03~2026-08-18) | `product_order_id`만(상품번호 컬럼 없음) — 상품 직접 연결 불가, order_number 경유해야 함 |
| **정산 — settle/daily**(`naver.py:327`) | ✅ `routers/naver_ops.py:686`, 크론 `sync_naver_settlement` 05:25 KST | **`naver_settlement_daily`**(UNIQUE settle_expect_date, 계정 전체 일별 합계) | **55행** (2026-05-06~2026-08-18) | **없음** — 상품 컬럼 자체가 스키마에 없음(계정 단위 그레인). 등급 교차 불가 |
| **정산 — settle/case**(`naver.py:387`) | ✅ `routers/naver_ops.py:805`, 크론 `sync_naver_case_settlement` 05:30 KST | **`naver_settlement_case`**(UNIQUE product_order_id) | **11,730행** (pay_date 2026-04-21~2026-08-17), 최근30일 3,442행 | `product_id` 컬럼 있음 — **9,003/11,730행(76.8%)에 채워짐**, 나머지(DELIVERY류)는 NULL. 형식이 `orders.platform_product_id`와 일치 |
| **정산 — settle/commission-details** | ❌ 미사용(ref71 §정산 도메인 표) | 없음 | — | — |
| **부가세**(vat/case, vat/daily) | ❌ 미사용(ref71) | 없음 | — | — |
| **문의 — pay-user/inquiries**(`naver.py:510`) | ✅ `routers/naver_ops.py:836` | **없음(온디맨드)** — docstring "DB 저장 없이 라이브 API 직접 반환" 명시, `scheduler_service.py` grep 0건 | — | — |
| **문의 — contents/qnas** | ❌ 미사용(ref71) | 없음 | — | — |
| **상품 메타**(products/search, `naver.py:564`) | ✅ `routers/naver_ops.py:881` | **없음(온디맨드)** | — | — |
| **비즈월렛**(commerce-solutions/transactions) | ❌ 미사용(ref71) | 없음 | — | — |
| **판매자정보**(seller/account, seller/channels, `naver.py:612`) | ✅ `routers/naver_ops.py:862` | **없음(온디맨드)** | — | — |
| **쓰기 계열**(confirm/dispatch/delay, claim cancel/return/exchange 승인·거부) | ✅ 다수(naver.py 700~980대) | 해당 없음(작업성 API) | — | — |

### 실측 ①의 「확인 못 한 것」(원문)
- `naver_settlement_case.product_id` → `orders.platform_product_id` → `NaverAdgroupProduct.mall_product_id` 실제 JOIN 미실행 → **아래 C에서 실행함**
- `naver_claim_settlement_probe`가 `order_number`로 상품까지 닿는지 미실행 → **C에서 실행함**
- ref71이 인용한 `naver.py` 줄번호(222·644·765)와 현재 코드(220·636·757) 불일치 — 원인 미상
- API데이터솔루션 도메인(ref71이 「인덱스 부재, 별도 구독 게이트로 추정」) — 존재 자체가 미상
- `vat/*`·`commission-details`·`contents/qnas`·N배송(SKU)·상품 정적메타 61종 — ref71의 「미사용」 결론을 코드 재확인 없이 인용(크론 부재만 교차확인)

---

## B. 광고 「원료 있음·미교차」 10축 재검증 (실측 ②, 원문)

| # | 축 | 원료의 실제 위치 | prod 행수·기간 커버리지 | adgroup_id 집계 가능? | 추가 작업 | 지금 교차 가능? |
|---|---|---|---|---|---|---|
| 5 | S4 제외여부·시점 | `naver_search_term_exclusion`(adgroup_id 有, blank 0) | **3,990행**(excluded 3,986/void 4), `excluded_at` 2026-07-22~08-17, `console_excluded_at` 2024-08-14~2026-08-11(4건 NULL). campaign_type: SHOPPING 3,880·WEB_SITE 106·미매칭 4 | 예(100% 충전) | 제외 이전 `naver_search_term_daily` 성과를 adgroup 단위로 합산해 「제외 전」 밴드 재계산 스크립트 신설 | **조건부** — SHOPPING 사실상 전량, WEB_SITE는 723건 중 106건(14.7%)만. 제외 46%(1,821/3,990)는 `console_excluded_at`이 검색어 성과 테이블 시작일(2025-07-23)보다 앞서 「제외 전」 데이터가 원리적으로 없음 |
| 9 | A1 관심사(AD) | **없음**(DB) — `criterion`/`target` 계열 테이블 0개 | 0행. 라이브 프로브(2026-08-16, 177그룹, CRITERION 5,325행/1일치) 원본은 세션 scratchpad(휘발성)에만 | 원리상 가능(ownerId=adgroup_id) — 저장 0이라 판단 불가 | `naver_criterion_daily` 신설 + 일별 수집 + 1년 소급 백필 | **불가** |
| 10 | A2 요일·시간(CRITERION SD) | **없음**(DB) | 0행. SD는 **설정된 4그룹만** 리포트 행 존재 | 표본 4그룹뿐 — 밴드 교차 자체가 무의미한 크기 | A1과 동일 | **불가** |
| 11 | A3 검색어 텍스트 속성 | `naver_search_term_daily.search_term`(String(300)) | **3,069,343행**, 2025-07-23~2026-08-17, distinct adgroup 729, blank 0 | 예(100% 충전) | 브랜드어·토큰수 판정 로직(사전 매칭) 신설 | **조건부** — 텍스트 원료 완비, 판정 로직만 있으면 즉시 |
| 13 | A5 매체 블랙리스트 | **없음**(DB) | 0행. 385그룹(SHOPPING 195·WEB_SITE 190) GET 완료했으나 응답 본문 미보존 | 원리상 가능 — 저장 자료 없음 | 신규 테이블 + 재수집 | **불가** |
| 15 | A7 소재 개별입찰·잠금 | `naver_adgroup_product`(ad_bid_amt·use_group_bid_amt·ad_user_lock) | **1,761행/238그룹**, 전 행 충전. `use_group_bid_amt=0` 588행/221그룹·`ad_user_lock=1` 317행. `synced_at` 2026-08-09~08-18(★누적 upsert, 역사적 시계열 아님 — 「현재 상태」만) | 예(직결) | 없음 — 집계 쿼리만 | **가능** — 단 밴드는 391일 누적인데 원료는 현재 단면이라 횡단면 대조로 한정 |
| 18 | A10 계절성(월·분기) | `naver_ad_daily.ad_date`(기존 grain 재사용) | **951,201행**, 2025-07-22~2026-08-17, distinct adgroup 871 | 예 | 월/분기 파생 group-by + band join | **가능** — 단 1년=1주기뿐이라 「패턴 확정」 원리적 불가, 「경향 서술」까지 |
| 20 | B2 예산·daily_budget | `naver_entity_snapshot.daily_budget`(entity_type='campaign') | **1,282행**, 100% 충전, 2026-07-22~2026-08-18(**27일**, 391일 창의 약 7%) | 캠페인 grain → band CSV의 `campaign_id`로 조인(1:N 복제) | 조인 스크립트 | **조건부** — 창의 7%만 덮어 「기술」용으로만 |
| 21 | B3 시장가 사다리 | `naver_bid_estimate_daily`(adgroup_id 有) | **6,590행, 단 4일(2026-07-27~07-30)**, distinct adgroup 95(SHOPPING 238그룹의 40%). 08-18 기준 **19일째 갱신 정지** | 예 | 없음 | **불가(사실상)** — ref59 §B-1(line 463)이 이미 「4일치로는 판정 불가·기술조차 불가」로 처분 |
| 22 | B4 입찰 변경 이력 | `naver_change_log`(`update_bid`·`external_bid_change`) + `naver_entity_snapshot` bid_amt 차분 | `update_bid` 425행(2026-07-17~07-30) · `external_bid_change` 443행(2026-07-22~08-14). entity_type: ad 287·adgroup 150·keyword 431 | adgroup 150행 직접, ad·keyword 718행은 `naver_entity` 부모 링크 롤업 필요 | 롤업 조인 스크립트 | **조건부** — 인과 오염 축이라 ref59가 「기술용만」으로 용도 제한(line 150) |

### ★실측 ②가 찾은 「문서 주장 ↔ 실측 어긋남」 3건 (원문)
1. **#5 S4** — ref68은 "원장 편입은 42/3,880건뿐(콘솔 캡처분)"이라 적었으나 prod 실측(08-18)은 **SHOPPING 3,880/3,880 사실상 전량이 이미 원장에 있다**. `created_at` 히스토그램상 08-17 하루에 3,837건이 `console_import`로 신규 생성. ref68은 그 시점 스냅샷이라 인계 당시엔 맞았으나 **지금 다시 세면 완전히 다른 숫자**. (WEB_SITE는 반대로 723건 중 106건만 — ref68은 SHOPPING/WEB_SITE 구분을 안 했다.)
2. **#21 B3** — ref68은 "커버리지 미확정"이라 적었으나, 같은 세션의 앞선 문서 **ref59 §B-1(line 463)이 이미 커버리지를 측정**(4일치)하고 그 자리에서 "B3 축을 뺀다 — 판정 불가(기술조차 불가)"라고 **결론까지 냈다**. "미확정"이 아니라 **"확정됐고 이미 탈락 처분된 축"** — ref68이 ref59의 결론을 누락했다. prod 재확인도 4일에서 전혀 안 늘었다(19일째 정체).
3. **#9/#10/#13 (A1·A2·A5)** — ref68 §2부의 "원료 있음·미교차" 표기가 "DB에 저장돼 있다"는 오독을 부른다. 실측하면 **세 축 모두 DB 테이블이 0개**다. "원료"는 "API를 다시 부르면 나온다"는 뜻이지 "지금 저장돼 있다"가 아니다. (§1부 인벤토리엔 "없음"이라 정확히 적혀 있어 문서 내부 모순은 아니나, §2부만 보면 다른 신호를 준다.)

### 실측 ②의 「확인 못 한 것」(원문)
- A1/A2/A5의 385그룹 전수 라이브 프로브 원본 응답이 다른 세션 `/private/tmp` 잔여물에 더 있는지 — 전수 탐색 안 함
- `naver_search_term_exclusion`의 WEB_SITE 723건이 **지금도** 723건인지 — API 재호출이 금지선이라 원문 인용만
- `naver_change_log.external_bid_change` 443건이 `bm_diff.py` 산출과 동일한지 — 코드 대조 범위 밖
- `criterion_real_20260817.json`이 A1/A2/A5의 「성과」인지 「타겟팅 설정」(A4 귀속)인지 — 스키마상 후자로 보이나 원 코드 미대조. AD(관심사)·MEDIA_TARGET 키가 없어 A1/A5 원본으로는 못 씀

---

## C. 조인 매칭률 — 커머스가 성과등급에 실제로 닿는가 (실측 ③, SQL 전문 병기)

성과등급은 **광고그룹 단위**다. 커머스 자료가 등급과 교차되려면 `상품번호 → naver_adgroup_product.mall_product_id → adgroup_id` 경로가 실제로 이어져야 한다.

### C-1. 모집단 상한
```sql
SELECT (SELECT COUNT(DISTINCT adgroup_id) FROM naver_adgroup_product) map_adgroups,
       (SELECT COUNT(DISTINCT mall_product_id) FROM naver_adgroup_product) map_products,
       (SELECT COUNT(DISTINCT adgroup_id) FROM naver_ad_daily WHERE adgroup_id<>'') ad_adgroups_all,
       (SELECT COUNT(DISTINCT adgroup_id) FROM naver_ad_daily
         WHERE adgroup_id<>'' AND ad_date >= date('now','+9 hours','-90 day')) ad_adgroups_90d;
```
| map_adgroups | map_products | ad_adgroups_all | ad_adgroups_90d |
|---|---|---|---|
| 238 | 702 | 871 | 495 |

90일 활성 광고그룹 495개 중 매핑 보유 **228개(46.1%)**.

### C-2. ★캠페인 유형별 매핑 보유율 + 비용 점유 (90일)
```sql
WITH a AS (
  SELECT adgroup_id, campaign_type, SUM(cost) cost FROM naver_ad_daily
  WHERE adgroup_id<>'' AND ad_date >= date('now','+9 hours','-90 day')
  GROUP BY adgroup_id, campaign_type
), m AS (SELECT DISTINCT adgroup_id FROM naver_adgroup_product)
SELECT a.campaign_type, COUNT(*) groups,
       SUM(CASE WHEN m.adgroup_id IS NOT NULL THEN 1 ELSE 0 END) mapped_groups,
       ROUND(100.0*SUM(CASE WHEN m.adgroup_id IS NOT NULL THEN 1 ELSE 0 END)/COUNT(*),1) grp_pct,
       ROUND(SUM(a.cost),0) cost_all,
       ROUND(SUM(CASE WHEN m.adgroup_id IS NOT NULL THEN a.cost ELSE 0 END),0) cost_mapped,
       ROUND(100.0*SUM(CASE WHEN m.adgroup_id IS NOT NULL THEN a.cost ELSE 0 END)/NULLIF(SUM(a.cost),0),1) cost_pct
FROM a LEFT JOIN m ON m.adgroup_id=a.adgroup_id GROUP BY a.campaign_type ORDER BY cost_all DESC;
```
| campaign_type | groups | mapped_groups | grp_pct | cost_all | cost_mapped | cost_pct |
|---|---|---|---|---|---|---|
| SHOPPING | 239 | 228 | **95.4** | 62,353,448 | 37,442,453 | **60.0** |
| WEB_SITE | 257 | **0** | **0.0** | 26,242,745 | 0 | **0.0** |
| BRAND_SEARCH | 1 | 0 | 0.0 | 0 | 0 | (n/a) |

### C-3. 정산 건별 × 매핑
```sql
SELECT COUNT(*) rows_all,
       SUM(CASE WHEN s.product_id IS NOT NULL AND s.product_id<>'' THEN 1 ELSE 0 END) rows_with_pid,
       SUM(CASE WHEN m.mall_product_id IS NOT NULL THEN 1 ELSE 0 END) rows_matched,
       COUNT(DISTINCT CASE WHEN m.mall_product_id IS NOT NULL THEN s.product_id END) pids_matched,
       ROUND(SUM(CASE WHEN m.mall_product_id IS NOT NULL THEN s.settle_expect_amount ELSE 0 END),0) amt_matched,
       ROUND(SUM(s.settle_expect_amount),0) amt_all
FROM naver_settlement_case s
LEFT JOIN (SELECT DISTINCT mall_product_id FROM naver_adgroup_product) m
       ON m.mall_product_id = s.product_id;
```
| rows_all | rows_with_pid | rows_matched | pids_matched | amt_matched | amt_all |
|---|---|---|---|---|---|
| 11,730 | 9,003 | **8,740** | 410 | 139,177,309 | 150,674,832 |

행 기준 74.5%(pid 보유분 대비 97.1%) · **금액 기준 92.4%**.

### C-4. 주문 × 매핑 (90일)
```sql
SELECT COUNT(*) rows_all,
       SUM(CASE WHEN m.mall_product_id IS NOT NULL THEN 1 ELSE 0 END) rows_matched,
       COUNT(DISTINCT CASE WHEN m.mall_product_id IS NOT NULL THEN o.platform_product_id END) pids_matched,
       ROUND(SUM(CASE WHEN m.mall_product_id IS NOT NULL THEN o.selling_price ELSE 0 END),0) amt_matched,
       ROUND(SUM(o.selling_price),0) amt_all
FROM orders o
LEFT JOIN (SELECT DISTINCT mall_product_id FROM naver_adgroup_product) m
       ON m.mall_product_id = o.platform_product_id
WHERE o.channel_id=6 AND o.order_date >= datetime('now','+9 hours','-90 day');
```
| rows_all | rows_matched | pids_matched | amt_matched | amt_all |
|---|---|---|---|---|
| 8,445 | **8,213** | 381 | 135,639,210 | 139,334,610 |

행 **97.3%** · 금액 **97.3%**.

### C-5. 클레임 정산 프로브 → orders → 상품
```sql
SELECT COUNT(*) rows_all,
       SUM(CASE WHEN o.order_number IS NOT NULL THEN 1 ELSE 0 END) joined_orders,
       SUM(CASE WHEN m.mall_product_id IS NOT NULL THEN 1 ELSE 0 END) reached_mapping
FROM naver_claim_settlement_probe p
LEFT JOIN orders o ON o.order_number = p.order_number AND o.channel_id=6
LEFT JOIN (SELECT DISTINCT mall_product_id FROM naver_adgroup_product) m
       ON m.mall_product_id = o.platform_product_id;
```
| rows_all | joined_orders | reached_mapping |
|---|---|---|
| 1,220 | 1,220 | 1,220 |

⚠️ **행 팽창 주의**: 원 테이블은 **1,069행**인데 조인 결과가 1,220이다. `orders`의 `order_number`가 유일하지 않기 때문이다(12,366행 / distinct 11,711). 또한 이 프로브의 **distinct `product_order_id`는 94개뿐**이다(같은 건을 매일 재관측하는 append-only 로그). 「100% 도달」은 팽창된 행 집합 위의 비율이므로 **건수 지표로 그대로 쓰면 안 된다.**

---

## D. 등급(밴드) 정의 — 교차의 기준축 (정본 좌표만)
`docs/references/data/68_band_x_api_matrix/performance_band_x_api_inventory_20260817.md` §0.
BEP RoAS = 1.711(계정 블렌디드) · BEP+20% = 2.0532. band1/2/3/4 + cost0. 성숙분 ≤2026-08-09 기준, SHOPPING/WEB_SITE 층화.
밴드별 그룹 목록 원자료: `docs/references/data/63_band_decomposition/band_group_total.csv`(`adgroup_id` 컬럼 보유, 391일 창).

---

## E. ★정정 — C-2의 비용 점유율이 틀렸다 (2026-08-18 12:5x, 코디네이터 자기정정)

**무엇이 틀렸나**: §C-2의 90일 비용 집계가 `__backfill__` **센티널 행을 제외하지 않았다**. 이 센티널은 실제 광고그룹이 아니라 캠페인 백필용 표식이고(`campaign_backfill.BACKFILL_SENTINEL_ADGROUP`, `probe_revert` 등 다른 판정 경로는 전부 명시적으로 배제한다), 90일 창에서 혼자 **31,550,746원 / 2,062행**을 차지한다. 그것이 「미매핑 SHOPPING」 쪽에 통째로 실려 매핑 비용 점유율을 끌어내렸다.

**정정된 값** (센티널 제외, 90일, 실행 SQL 아래):

| campaign_type | groups | mapped_groups | cost_all | cost_mapped | **cost_pct** |
|---|---|---|---|---|---|
| SHOPPING | 238 | 228 | 38,728,684 | 37,442,453 | **96.7** (초판 60.0 — 틀림) |
| WEB_SITE | 256 | 0 | 18,316,763 | 0 | 0.0 |

```sql
WITH a AS (
  SELECT adgroup_id, campaign_type, SUM(cost) cost FROM naver_ad_daily
  WHERE adgroup_id<>'' AND adgroup_id<>'__backfill__'
    AND ad_date >= date('now','+9 hours','-90 day')
  GROUP BY adgroup_id, campaign_type
), m AS (SELECT DISTINCT adgroup_id FROM naver_adgroup_product)
SELECT a.campaign_type, COUNT(*) groups,
       SUM(CASE WHEN m.adgroup_id IS NOT NULL THEN 1 ELSE 0 END) mapped_groups,
       ROUND(SUM(a.cost),0) cost_all,
       ROUND(SUM(CASE WHEN m.adgroup_id IS NOT NULL THEN a.cost ELSE 0 END),0) cost_mapped,
       ROUND(100.0*SUM(CASE WHEN m.adgroup_id IS NOT NULL THEN a.cost ELSE 0 END)/NULLIF(SUM(a.cost),0),1) cost_pct
FROM a LEFT JOIN m ON m.adgroup_id=a.adgroup_id GROUP BY a.campaign_type ORDER BY cost_all DESC;
```

**파급**: 매트릭스 §2부 **F5(「미매핑 SHOPPING 11그룹이 SHOPPING 비용의 40%」)는 이 오류 위에 서 있다.** 교차 계산이 독립적으로 같은 것을 짚었다 — 미매핑 11그룹의 90일 비용 24.9M원 중 **94.8%(23.6M원)가 센티널 1행**이고, 나머지 10개 실그룹은 **전부 `status ∈ {off, deleted}`**(활성 0). 즉 「고비용 그룹이 매핑에서 빠져 있다」가 아니라 **「센티널과 꺼진 그룹이 미매핑으로 잡혔다」**가 사실이다. 커머스 교차의 금액 대표성은 60%가 아니라 **쇼핑 비용의 96.7%**이고, 전체 SA 비용 기준으로는 37,442,453 / (38,728,684+18,316,763) = **65.6%**다.

**교훈(발생 지점)**: 이 저장소는 `__backfill__` 센티널을 **판정 경로마다 개별적으로** 배제한다 — 공용 필터가 없다. 그래서 새로 쓰는 집계 SQL은 매번 그 배제를 다시 적어야 하고, 잊으면 조용히 틀린다(에러가 나지 않는다). 실측 ①·②는 기존 좌표를 따라가서 안 걸렸고, **코디네이터가 새로 쓴 SQL만 걸렸다.**

## F. fan-out — 상품 1개가 여러 광고그룹에 걸린다 (교차 계산이 발견, 미해결)

`naver_adgroup_product`의 상품당 광고그룹 수 분포(전 702상품):

| 광고그룹 수 | 상품 수 |
|---|---|
| 1 | 122 |
| 2 | 268 |
| 3 | 216 |
| 4 | 51 |
| 5 | 22 |
| 6 | 20 |
| 7 | 3 |

```sql
SELECT n_groups, COUNT(*) products FROM (
  SELECT mall_product_id, COUNT(DISTINCT adgroup_id) n_groups
  FROM naver_adgroup_product GROUP BY mall_product_id) GROUP BY n_groups ORDER BY n_groups;
```

**2개 이상에 걸린 상품 = 580 / 702 (82.6%)**. 서로 다른 밴드의 그룹에 같은 상품이 걸리므로, 밴드별 커머스 금액을 그냥 합산하면 **원래 매칭 총액을 3.2~3.24배 부풀린다**(교차 계산 실측).

⚠️ **미해결 불일치**: 교차 계산 산출물은 다중 그룹 상품을 **390개(55.6%)**로 적었고 위 쿼리는 **580개(82.6%)**다. 정의가 다른 지점(SHOPPING 한정 여부·정산 매칭분 한정 여부 등)을 **아직 특정하지 못했다 — 둘 중 무엇이 맞는지 확정 전에는 어느 쪽도 인용하지 않는다.**

**배분 방법은 미결**: 이 저장소엔 선례가 있다 — `today_proxy_revenue.build()`가 `product_campaign_share.campaigns_per_product`로 **균등 분할**하고, 그 분모는 「요청 범위와 무관하게 그 상품을 매핑한 **모든** 캠페인 수」여야 한다고 docstring에 명시돼 있다(범위가 좁으면 과대 계상). 다만 그 선례는 **캠페인** grain이고 여기 필요한 것은 **광고그룹** grain이다. 어느 배분을 쓸지는 이 문서에서 정하지 않는다.
