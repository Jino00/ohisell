# 완료 QA 미달 항목 보강 — ④검색량×성과등급 교차 실측 + ③N1~N8 처분표 (2026-08-18)

> 측정 담당(읽기 전용). 완료 QA(HANDOFF_bleed-valve-fix+band-x-all-api_20260818)가 미달로 지목한 두 합격기준을 채운다. **경향성·해석·권고는 쓰지 않는다** — 숫자·좌표·처분만.

---

## 0. 표기 규약

- 측정일시: **2026-08-18 KST 16:30~16:50 부근**(prod 접속 3회, 아래 SQL 각각의 실행 결과).
- prod 접속: `ssh -o BatchMode=yes sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db" < 로컬.sql` — 전량 SELECT/PRAGMA/CREATE TEMP TABLE(세션 한정, `-readonly` 플래그로 원본 DB 물리적 쓰기 불가). **파일 쓰기(`.once` 등) 사용 안 함** — 모든 출력은 stdout으로 받아 로컬 파일에 리다이렉트(prod 파일시스템 변경 0건).
- band 정본: `docs/references/data/63_band_decomposition/band_group_total.csv`(855행=헤더+854 데이터행, `adgroup_id` 1:1, 중복 0건 확인) — python으로 `CREATE TEMP TABLE band_map`용 INSERT 구문을 기계적으로 생성해 세션 시작 시 적재(원본 DB 미반영, 세션 종료 시 소멸). campaign_type도 이 CSV 컬럼을 그대로 썼다(WEB_SITE 488행 · SHOPPING 366행, python 직접 카운트로 확인).
- **`__backfill__` 배제**: 새로 쓴 모든 집계 SQL에 `adgroup_id != '__backfill__'` 조건을 명시적으로 다시 걸었다(공용 필터 없음, 이 저장소 전례).
- **창 불일치 각주(필독)**: `naver_keyword_volume_daily`는 **2026-08-18 하루치만 존재**(`measured_date` 전 행 동일값, 1,166행). band는 **391일 누적 창**(성숙분 ≤2026-08-09)에서 산출됐다. 이 문서의 모든 교차는 **1일 창 값을 391일 창 라벨에 갖다 붙인 것**이다 — band가 오늘 하루의 검색량 변화를 반영한 게 전혀 아니고, 검색량이 391일간 어떻게 움직였는지도 이 데이터로는 알 수 없다.
- **검색량은 시장 수치, 계정 수치 아님 각주**: `pc_volume`/`mobile_volume`/`total_volume`은 네이버 검색광고 `/keywordstool`이 주는 **월간 시장 검색량**이다. 우리 계정의 노출·클릭과 무관하게 그 키워드를 검색한 전체 사용자 수의 근사치다. 「이 밴드가 검색량 합이 크다」는 **그 밴드 소속 광고그룹이 그런 시장 검색량을 가진 키워드를 등록해 두었다**는 뜻이지, 성과(band 자체가 이미 391일 비용/전환으로 정해진 값)를 검색량이 설명한다는 인과가 전혀 아니다.
- **하한 sentinel 각주**: `is_below_threshold=1`인 행은 네이버가 임계값 미만을 마스킹해 낮은 고정값(10·15·25…)으로 반환한 것으로 관측된다(§2-1 참조) — **하한이지 실측값이 아니다**. 이런 행이 섞인 SUM/median은 진짜 검색량보다 낮게 잡힌다.
- **fan-out 규약(D-NAO-194 그대로)**: 한 키워드(entity 텍스트)가 여러 광고그룹(→여러 밴드)에 걸린다(실측: 1,166개 중 943개(80.9%)가 2개 이상 광고그룹에 걸림). **밴드별 값 = 그 밴드 안에서 dedupe한 합**(같은 밴드에 중복 진입해도 1회만 산입), **밴드 간 합산 금지**(밴드별 합을 더하면 baseline을 초과 — §2-1 하단 실측치로 확인).
- 좌표 규약: SQL 원문은 스크래치패드에 있고 이 문서엔 전문을 인용한다(파일 경로: `/private/tmp/claude-501/.../scratchpad/*.sql`, 세션 로컬이라 재현 시 재작성 필요 — 아래 코드블록이 재현에 필요한 전체 내용이다).

---

## 1. 작업 A — 실행 SQL/스크립트 전문

### 1-1. band_map 임시테이블 적재 (band_group_total.csv → CREATE TEMP TABLE)

생성 방법(python, 기계적 변환 — 854행 INSERT 리터럴은 지면상 생략하고 생성 스크립트만 전문 수록):

```python
import csv
rows = list(csv.DictReader(open('docs/references/data/63_band_decomposition/band_group_total.csv')))
out = ['CREATE TEMP TABLE band_map (adgroup_id TEXT PRIMARY KEY, campaign_type TEXT, band TEXT);']
buf = []
for r in rows:
    aid = r['adgroup_id'].replace("'", "''")
    ct = r['campaign_type'].replace("'", "''")
    b = r['band'].replace("'", "''")
    buf.append(f"('{aid}','{ct}','{b}')")
out.append('INSERT INTO band_map (adgroup_id, campaign_type, band) VALUES')
out.append(',\n'.join(buf) + ';')
out.append('CREATE INDEX ix_band_map_adgroup ON band_map(adgroup_id);')
open('band_map_load.sql','w').write('\n'.join(out))
```

결과: `band_map` 854행, `adgroup_id` PK(중복 0건, `python3 -c` 별도 카운트로 확인), 밴드 분포 `band1=296(SHOPPING 77+WEB_SITE 219) · band2=93 · band3=295 · band4_unjudgeable=143 · excluded_cost0=27`(§0 63_band_decomposition 정본과 일치).

### 1-2. 스키마 확인 (실행 SQL)

```sql
PRAGMA table_info(naver_keyword_volume_daily);
PRAGMA table_info(naver_entity);
PRAGMA table_info(naver_ad_daily);
```

관측: `naver_keyword_volume_daily(id, measured_date, keyword, pc_volume, mobile_volume, total_volume, competition, is_below_threshold, synced_at)` · `naver_entity(id, entity_type, entity_id, parent_id, campaign_id, campaign_type, name, status, bid_amt, monthly_volume, competition, volume_updated_at, synced_at, qi_grade, status_reason, edit_tm, reg_tm)` · `naver_ad_daily(id, ad_date, campaign_id, campaign_type, adgroup_id, keyword_id, imp, clk, cost, rank_sum, conv_direct_cnt, conv_indirect_cnt, conv_direct_amt, conv_indirect_amt, synced_at, cart_*)`.

### 1-3. 기초 카운트 (실행 SQL)

```sql
SELECT COUNT(*) FROM naver_keyword_volume_daily;                                   -- 1166
SELECT COUNT(DISTINCT measured_date) FROM naver_keyword_volume_daily;              -- 1 (2026-08-18 단일값)
SELECT COUNT(DISTINCT keyword) FROM naver_keyword_volume_daily;                    -- 1166 (중복 0)
SELECT COUNT(*) FROM naver_keyword_volume_daily WHERE is_below_threshold = 1;      -- 729
SELECT competition, COUNT(*) FROM naver_keyword_volume_daily GROUP BY competition; -- 낮음224 / 높음794 / 중간148
SELECT COUNT(*) FROM naver_entity WHERE entity_type='keyword';                     -- 91172
SELECT COUNT(DISTINCT name) FROM naver_entity WHERE entity_type='keyword';         -- 43136 (중복명 48036행)
SELECT campaign_type, COUNT(*) FROM naver_entity WHERE entity_type='keyword' GROUP BY campaign_type;  -- WEB_SITE 91172, SHOPPING 0
SELECT MIN(ad_date), MAX(ad_date) FROM naver_ad_daily;                             -- 2025-07-22 ~ 2026-08-17
SELECT campaign_type, COUNT(*) FROM naver_ad_daily
  WHERE adgroup_id != '__backfill__' AND (keyword_id IS NULL OR keyword_id='')
  GROUP BY campaign_type;   -- SHOPPING 66695 / WEB_SITE 58937 (blank keyword_id, 즉 일별 롤업 행)
SELECT campaign_type, COUNT(*) FROM naver_ad_daily
  WHERE adgroup_id != '__backfill__' AND keyword_id != '' AND keyword_id IS NOT NULL
  GROUP BY campaign_type;   -- WEB_SITE 823061 / SHOPPING 0
```

★**구조적 사실(해석 아님, 관측)**: `naver_ad_daily.keyword_id`는 **SHOPPING 캠페인 행에서 100% 빈 문자열**이다(66,695행 전부). `naver_entity(entity_type='keyword')`도 **전 행 WEB_SITE**(SHOPPING 0건). 즉 지시가 명시한 조인 경로(`naver_keyword_volume_daily.keyword → naver_entity.name → entity_id → naver_ad_daily.keyword_id → adgroup_id → band`)는 **SHOPPING 밴드 셀에 원리적으로 절대 닿지 못한다** — SHOPPING은 상품 기반 타겟팅이라 이 계정에 등록된 "keyword" entity 자체가 없다(검색어는 `naver_search_term_daily`가 별도로 담당, 이 조인 경로 밖). 이는 §5의 "지시 배경 사실 검증" 의무에 해당하는 발견이다.

### 1-4. 조인 퍼널 (실행 SQL — kw_entities → kw_ad_activity → kw2band → kw2band_dedup)

```sql
CREATE TEMP TABLE kw_entities AS
SELECT e.entity_id, e.name AS keyword, e.parent_id AS adgroup_id_entity, e.campaign_type AS entity_campaign_type
FROM naver_entity e
WHERE e.entity_type='keyword'
  AND e.name IN (SELECT keyword FROM naver_keyword_volume_daily);
CREATE INDEX ix_kwent_entity ON kw_entities(entity_id);
CREATE INDEX ix_kwent_keyword ON kw_entities(keyword);

CREATE TEMP TABLE kw_ad_activity AS
SELECT DISTINCT k.keyword, a.adgroup_id
FROM kw_entities k
JOIN naver_ad_daily a ON a.keyword_id = k.entity_id AND a.adgroup_id != '__backfill__';

CREATE TEMP TABLE kw2band AS
SELECT DISTINCT ka.keyword, bm.adgroup_id, bm.band, bm.campaign_type
FROM kw_ad_activity ka
JOIN band_map bm ON bm.adgroup_id = ka.adgroup_id;

CREATE TEMP TABLE kw2band_dedup AS
SELECT DISTINCT keyword, band, campaign_type FROM kw2band;   -- ★밴드 내 dedupe(fan-out 규약)

-- 매칭 실패 지점별 카운트
SELECT COUNT(*) FROM naver_keyword_volume_daily v WHERE v.keyword NOT IN (SELECT keyword FROM kw_entities);                                   -- step1 실패
SELECT COUNT(*) FROM naver_keyword_volume_daily v WHERE v.keyword IN (SELECT keyword FROM kw_entities) AND v.keyword NOT IN (SELECT keyword FROM kw_ad_activity); -- step2 실패
SELECT COUNT(DISTINCT ka.keyword) FROM kw_ad_activity ka
  WHERE NOT EXISTS (SELECT 1 FROM band_map bm WHERE bm.adgroup_id = ka.adgroup_id)
  AND ka.keyword NOT IN (SELECT keyword FROM kw2band_dedup);   -- step3 실패(활동은 있으나 그 adgroup이 band_map 854에 없음)
```

### 1-5. 밴드별 집계 (실행 SQL)

```sql
SELECT d.band, d.campaign_type,
  COUNT(*) n_keywords,
  SUM(v.total_volume) sum_total_volume,
  SUM(v.pc_volume) sum_pc_volume,
  SUM(v.mobile_volume) sum_mobile_volume,
  SUM(CASE WHEN v.is_below_threshold=1 THEN 1 ELSE 0 END) n_below_threshold,
  ROUND(100.0*SUM(CASE WHEN v.is_below_threshold=1 THEN 1 ELSE 0 END)/COUNT(*),1) pct_below_threshold
FROM kw2band_dedup d JOIN naver_keyword_volume_daily v ON v.keyword = d.keyword
GROUP BY d.band, d.campaign_type ORDER BY d.band, d.campaign_type;

SELECT d.band, d.campaign_type, v.competition, COUNT(*) n
FROM kw2band_dedup d JOIN naver_keyword_volume_daily v ON v.keyword = d.keyword
GROUP BY d.band, d.campaign_type, v.competition ORDER BY d.band, d.campaign_type, v.competition;

-- 밴드별 원자료 전건(중앙값은 SQLite에 함수가 없어 이 원자료를 로컬 python으로 재계산)
SELECT d.band, d.campaign_type, d.keyword, v.total_volume, v.pc_volume, v.mobile_volume, v.competition, v.is_below_threshold
FROM kw2band_dedup d JOIN naver_keyword_volume_daily v ON v.keyword = d.keyword;
```

중앙값 로컬 재계산(python, `statistics.median`) — 원자료 1,867행(밴드-키워드 셀, dedupe 후) 그대로 사용, 별도 필터·추정 없음.

### 1-6. 30일 비용 대조 (실행 SQL)

```sql
CREATE TEMP TABLE kw_entity_adgroup_activity AS
SELECT DISTINCT k.keyword, k.entity_id, a.adgroup_id
FROM kw_entities k
JOIN naver_ad_daily a ON a.keyword_id = k.entity_id AND a.adgroup_id != '__backfill__';

CREATE TEMP TABLE ad30 AS
SELECT keyword_id, adgroup_id, SUM(cost) cost30, SUM(clk) clk30, SUM(imp) imp30
FROM naver_ad_daily
WHERE ad_date BETWEEN '2026-07-19' AND '2026-08-17' AND adgroup_id != '__backfill__'
GROUP BY keyword_id, adgroup_id;

-- (a) 매칭된 1,166개 키워드에 귀속되는 30일 비용, 밴드별
SELECT bm.band, bm.campaign_type,
  COUNT(DISTINCT kea.keyword) n_keywords,
  SUM(COALESCE(ad30.cost30,0)) sum_cost30,
  SUM(COALESCE(ad30.clk30,0)) sum_clk30
FROM kw_entity_adgroup_activity kea
JOIN band_map bm ON bm.adgroup_id = kea.adgroup_id
LEFT JOIN ad30 ON ad30.keyword_id = kea.entity_id AND ad30.adgroup_id = kea.adgroup_id
GROUP BY bm.band, bm.campaign_type ORDER BY bm.band, bm.campaign_type;

-- (b) 밴드 전체 30일 비용(매칭 여부 무관, 대조 분모)
SELECT bm.band, bm.campaign_type, SUM(a.cost) sum_cost30_all, COUNT(DISTINCT bm.adgroup_id) n_adgroups
FROM naver_ad_daily a JOIN band_map bm ON bm.adgroup_id = a.adgroup_id
WHERE a.ad_date BETWEEN '2026-07-19' AND '2026-08-17' AND a.adgroup_id != '__backfill__'
GROUP BY bm.band, bm.campaign_type ORDER BY bm.band, bm.campaign_type;
```

창: `naver_ad_daily` 최댓값(2026-08-17) 기준 역산 30일 = **2026-07-19 ~ 2026-08-17**.

---

## 2. 작업 A — 관측 표

### 2-1. 매칭 퍼널 (§5와 통합 서술)

| 단계 | 조건 | 통과 키워드 수(distinct) | 탈락 수 |
|---|---|---|---|
| 0 | `naver_keyword_volume_daily` 전체 | 1,166 | — |
| 1 | keyword 텍스트가 `naver_entity(entity_type='keyword')`에 존재 | 1,166 | **0** |
| 2 | 그 entity_id가 `naver_ad_daily.keyword_id`에 활동 행 보유(`__backfill__` 제외) | 1,166 | **0** |
| 3 | 그 adgroup_id가 `band_group_total.csv`(854행)에 존재 | 1,166 | **0** |

**매칭률 = 1,166/1,166 = 100.0%.** 탈락 0건이므로 "안 닿는 것의 검색량·비용" 별도 표는 **해당 없음**(전량 매칭).

★**해석 유보 각주**: 100% 매칭은 「이 조인 경로가 일반적으로 완벽하다」는 뜻이 아니다. `naver_keyword_volume_daily`의 1,166개 키워드가 애초에 **우리 계정에 등록된 키워드(entity)를 시드로 조회된 값**일 가능성이 높다(D-NAO-186 적재 착수 기록과 정합) — 즉 표본이 시장에서 무작위로 뽑힌 게 아니라 "우리가 이미 등록한 키워드"라서 자기 자신과 100% 매칭되는 구조일 수 있다. 이 데이터만으로는 표본 구성 방식을 확정 못 한다 — **미상**.

fan-out: 1,166개 키워드 중 **943개(80.9%)**가 2개 이상 광고그룹에 걸린다. 밴드-키워드 dedupe 셀 수 = **1,867**(=1,166개 키워드가 여러 밴드에 걸쳐 생성한 고유 (키워드,밴드) 쌍). 캠페인 유형은 **전량 WEB_SITE**(§1-3의 구조적 사실 때문).

**밴드 간 합산 금지 실측 근거**: 밴드별 `sum_total_volume`을 그대로 더하면 166,530+103,135+216,670+65,215 = **551,550**인데, baseline(중복 제거 1,166개 키워드의 실제 합)은 **327,830**이다 — **1.68배 인플레이션**. 밴드 합산을 총계로 쓰면 안 된다.

### 2-2. 밴드별 핵심 지표 (SHOPPING/WEB_SITE 층화)

| band | campaign_type | n_keywords(dedupe) | sum_total_volume | median_total_volume | sum_pc_volume | sum_mobile_volume | pc_비중% | mobile_비중% | n_below_threshold | pct_below_threshold |
|---|---|---|---|---|---|---|---|---|---|---|
| band1 | WEB_SITE | 1,007 | 166,530 | 20 | 28,130 | 138,400 | 16.9% | 83.1% | 647 | 64.3% |
| band2 | WEB_SITE | 417 | 103,135 | 30 | 16,855 | 86,280 | 16.3% | 83.7% | 239 | 57.3% |
| band3 | WEB_SITE | 400 | 216,670 | 40.0 | 40,590 | 176,080 | 18.7% | 81.3% | 209 | 52.3% |
| band4_unjudgeable | WEB_SITE | 43 | 65,215 | 40 | 13,190 | 52,025 | 20.2% | 79.8% | 22 | 51.2% |
| **(모든 SHOPPING 셀)** | SHOPPING | **0** | — | — | — | — | — | — | — | — |
| excluded_cost0 | (둘 다) | 0 | — | — | — | — | — | — | — | — |

SHOPPING 행과 `excluded_cost0` 행이 0인 이유는 §1-3의 구조적 사실(entity keyword 표면이 WEB_SITE 전용) 및 이번 1,166개 표본이 `excluded_cost0`(391일 누적비용 0 밴드) 소속 광고그룹의 키워드를 하나도 포함하지 않았기 때문 — **결측이 아니라 이 조인 경로·이 표본 구성의 필연적 결과**.

전체(baseline, fan-out 제거, 1,166개 유니크): sum_total_volume=327,830 · median=25 · mean=281.2(최댓값 50,720에 의한 우편향) · pc_비중 18.7%(61,320) · mobile_비중 81.3%(266,510) · below_threshold 729/1,166=**62.5%**(지시 원문 "약 62%"와 정합, 하한 sentinel 값은 10·15·25·35…로 관측 — §0 각주).

### 2-3. 경쟁도(competition) 분포

| band | 낮음 | 중간 | 높음 | n |
|---|---|---|---|---|
| band1 | 196 (19.5%) | 111 (11.0%) | 700 (69.5%) | 1,007 |
| band2 | 59 (14.1%) | 51 (12.2%) | 307 (73.6%) | 417 |
| band3 | 62 (15.5%) | 64 (16.0%) | 274 (68.5%) | 400 |
| band4_unjudgeable | 8 (18.6%) | 15 (34.9%) | 20 (46.5%) | 43 |
| 전체(1,166, 유니크) | 224 (19.2%) | 148 (12.7%) | 794 (68.1%) | 1,166 |

### 2-4. 30일 비용 대조 (2026-07-19~2026-08-17)

| band | campaign_type | 매칭 1,166개 키워드분 30일비용(원) | 밴드 전체 30일비용(원, 매칭무관) | 매칭분/밴드전체 비율 |
|---|---|---|---|---|
| band1 | WEB_SITE | 1,797,909 | 1,986,661 | 90.5% |
| band2 | WEB_SITE | 1,425,312 | 1,555,012 | 91.7% |
| band3 | WEB_SITE | 828,217 | 1,409,191 | 58.8% |
| band4_unjudgeable | WEB_SITE | 19,033 | 23,328 | 81.6% |
| band1 | SHOPPING | — (조인 경로상 원리적으로 0) | 6,011,114 | — |
| band2 | SHOPPING | — | 2,524,569 | — |
| band3 | SHOPPING | — | 8,038,896 | — |
| band4_unjudgeable | SHOPPING | — | 29,066 | — |
| excluded_cost0 | SHOPPING | — | 0 | — |

★**검색량 대비 비용 비를 SQL로 직접 나누지 않았다** — 단위가 다르다(검색량=월간 시장 조회수, 비용=원). "검색량 대비 비용"은 원 단위/월간회수 단위의 비율로 계산할 수는 있으나(예: band1 WEB_SITE 30일비용 1,797,909원 ÷ sum_total_volume 166,530회 ≈ 10.8원/회) **이 비율이 무엇을 의미하는지는 검색량이 계정 무관 시장수치이므로 해석 불가**(§0 각주) — 숫자만 계산 가능하고 그 이상은 말하지 않는다.

SHOPPING 밴드는 30일 비용이 실재한다(band3가 8,038,896원으로 가장 크다) — 그러나 이 비용을 발생시킨 검색어는 `naver_search_term_daily`(별도 그레인)에 있고 이번 조인 경로(entity keyword)로는 그 검색어들의 시장 검색량을 알 수 없다. band_map의 SHOPPING adgroup 수: band1=70·band2=38·band3=109·band4_unjudgeable=14·excluded_cost0=1(cost=0, n_adgroups=1).

WEB_SITE `__backfill__` 제외 후 distinct adgroup = 870, band_map(854)에 없는 adgroup = **16개**(870−854, 직접 확인) — 매칭된 1,166개 키워드는 이 16개 중 어디에도 걸치지 않았다(§2-1 step3 탈락 0건과 정합).

---

## 3. 작업 B — N1~N8 + `/ncc/targets` 폐기분 처분표

근거: `docs/references/data/75_api_surface_census/ADS_CENSUS_20260818.md` §3-3·§5, `docs/references/data/73_band_x_all_api/BAND_X_ALL_API_MATRIX_20260818.md` 1-A2(개정 4, L145-158). 판정 규칙은 지시 원문 그대로 적용 — grain이 광고그룹(adgroup)에 닿고 원료가 실재하며 기술적으로 수집 가능하면 **개통**, 가능하지만 이번엔 보류할 근거가 있으면 **제외(사유)**, grain이 원리적으로 못 닿거나 쓰기전용이면 **불가(원리)**, 분류 자체가 애매하면 **Jino 결정 필요**.

| 축 | 표면 | grain 확정도 | 처분 | 근거 |
|---|---|---|---|---|
| N1 | `GET /ncc/criterion/{ownerId}` type 7종(SD·AG·GN·AD·RL·RP·DV) | **adgroup 확정**(ownerId 자체가 adgroup_id) | **개통** | 코드 호출 0건(census §4-5, grep 0). 축 #4(연령성별)·#9(관심사)·#10(요일시간)의 직접 원료 — memory가 지목한 "등급을 가장 직접 흔드는 두 축 중 하나(연령성별)가 등급과 안 엮여 있다"의 실체가 이것. **선행조건**: 대상 adgroup 목록(band_map 854개 또는 활성 adgroup 전체, `naver_ad_daily` distinct 870개) 확정 후 ownerId별 GET 반복 + 응답 파싱·저장 테이블 신설. **예상 콜 수**: adgroup당 1회 × 854~870 ≈ **854~870콜**(1회성 스냅샷 기준, type 7종이 한 응답에 같이 오는지 개별 콜인지는 §6 미확인). RL·RP 두 코드는 의미 자체가 swagger에 없어[미상] — AG/GN/SD/DV/AD 5종 개통에는 지장 없음. |
| N2 | MasterReport 5종(item=Qi·Criterion·Adgroup·AdgroupBudget·Keyword) | **adgroup(census 표기, 별도 재검증 안 함)** | **개통** | 코드 사용 0/29(census §4-4 인접). 특히 `item=Qi`는 "91,172개 전부 `qi_grade=4`" 죽은신호의 미탐색 대안 경로(census §5-1) — 현재 경로(`/ncc/keywords`의 `nccQi.qiGrade`)와 독립적이라 대조 가치가 있다. **선행조건**: `POST /master-reports`(item별 fromTime 지정) → 상태 폴링(`GET /master-reports/{id}`) → 다운로드, 5개 item 반복. **예상 콜 수**: item당 생성1+폴링1+다운로드1 ≈ 3콜 × 5 = **약 15콜**(최초 풀스냅샷 1회 기준, 이후 delta는 더 적음). ★단서: 이 문서는 `item=Qi`의 실제 응답 grain(키워드 단위인지 adgroup 단위인지)을 직접 검증하지 않았다 — census 표기를 그대로 인용. |
| N3 | AdExtension군 9종(`/ncc/ad-extensions` CRUD 6 + `ADEXTENSION`/`ADEXTENSION_CONVERSION` 리포트 2) | **adgroup(문서상)**, 단 **실계정 가용성 자체가 미상** | **Jino 결정 필요** | 매트릭스 옛 서술("주요 유형 API 생성 불가")과 이번 census("CRUD·리포트 문서상 존재")가 **상충**(census §5-2, 매트릭스 L138). 원인(권한·상품 미가입 vs 문서 오독)이 실호출 금지로 검증 불가 — 「가능한지 자체가 애매」한 사례라 3값을 억지로 못 채운다. |
| N4 | SharedBudget 8종(`/ncc/shared-budgets` 계열) | **캠페인/adgroup(문서상 확정)** | **개통** | #20(B2 예산)이 27일 창 `daily_budget` 스냅샷으로 근사 중인데, 이 표면은 "캠페인 35/39(89.7%)가 다중 밴드"(매트릭스 §0-B) 구조의 원천인 공유예산 그룹 자체를 직접 준다. **선행조건**: 공유예산 그룹 목록 조회(`GET /ncc/shared-budgets`)로 그룹 수 확정 먼저 — 이번 조사는 그 개수를 세지 않았다[미상]. **예상 콜 수**: 그룹 목록 1콜 + 그룹당 멤버 조회(`/adgroups`·`/campaigns` 변형) N콜 — N은 그룹 수에 의존(미상이라 정확한 콜 수 산정 불가, 목록 조회 이후 추정 가능). |
| N5 | ProductGroup 1종(`GET /ncc/product-groups`) | **[미상]**(census 자신이 문서로 확정 못함) | **Jino 결정 필요** | grain이 adgroup에 닿는지 자체가 census §3-3에서 "미확정(미상)"으로 남음 — 개통·불가 어느 쪽으로도 근거가 없다. |
| N6 | InspectHistory 2종 | **[미상]**, 성과 연관 가설 자체가 미상 | **Jino 결정 필요** | census §3-3: "검수 이력 — 성과와 직접 연관 미상". grain뿐 아니라 "왜 이게 등급과 교차돼야 하는가"라는 가설 자체가 서 있지 않다 — 억지 개통·제외 판정보다 이쪽이 정직하다. |
| N7 | `/stats` breakdown=`pcMblTp`·`dayw`·`regnNo` | **요청 entity(adgroup) 확정** — 이미 가동 중인 #24(hh24)와 동일 콜 구조 | **개통** | 코드에 문자열 자체 0건(census §4-2). #24(hh24 breakdown)와 **완전히 같은 엔드포인트·같은 파라미터 자리**(값만 다름)라 신규 엔드포인트 통합이 필요 없다 — 세 후보 중 가장 낮은 배선 비용. `breakdown`은 "단일값만 지원"(census §2-3)이므로 세 값을 다 받으려면 **동일 콜을 3배**(현재 hh24까지 포함하면 4배) 반복해야 한다. **선행조건**: 없음(기존 `/stats` 호출부에 breakdown 파라미터 변형 추가 + 저장 스키마 확장). **예상 콜 수**: 현재 hh24 스윕 빈도 × 3(신규 breakdown 3종). 정확한 배수는 현재 hh24 스윕의 엔티티당 콜 빈도에 의존 — 이 문서는 그 빈도를 재확인하지 않았다[미상, 기존 코드 확인 필요]. |
| N8 | `/stats` 미요청 필드 13종(ctr·cpc·recentAvgRnk·recentAvgCpc·pcNxAvgRnk·mblNxAvgRnk·crto·ror·cpConv·viewCnt·purchaseCcnt·purchaseConvAmt·purchaseRor) | **요청 entity(기존 호출과 동일)** | **개통** | endpoint가 아니라 **기존 `/stats` 호출의 필드 파라미터 한 줄**(census §4-2: 19종 중 6종만 요청 중, 나머지 13종은 요청 자체를 안 함). **선행조건**: `_STATS_FIELDS` 등 기존 필드 상수에 항목 추가 + 응답 매핑·저장 컬럼 신설. **예상 콜 수**: **0(추가 호출 없음)** — 기존 콜의 쿼리스트링만 확장. 처분 후보 중 유일하게 "API 콜 증가 0" 확정. |
| `/ncc/targets` 폐기분(A5 매체 블랙리스트·A6 PC/모바일 가중치 원료) | 이미 호출 중인 `GET /ncc/targets`(쇼핑 제외 정규 경로, `naver_sa_writer.py:237,310`) 응답에서 `targetTp≠RESTRICT_KEYWORD_TARGET` 행을 필터링 시점에 버림 | **adgroup 확정, 이미 도착한 데이터** | **개통** | N1~N8과 다른 자리(신규 후보 축이 아니라 **기존 축(#13 A5·#14 A6)의 원료**, 매트릭스 L132-133이 이미 "호출가능·미적재"로 강등해 둠) — 지시가 별도로 처분을 요구해 포함. **선행조건**: 코드 변경만(파싱 로직에 `MEDIA_TARGET`·`PC_MOBILE_TARGET` 분기 추가 + 신규 컬럼/테이블). **예상 콜 수**: **0**(정규 운영 경로에 상시 편승, census §4-3) — 처분표 전체에서 개통 비용이 가장 낮다. |

### 3-1. 3값 집계

- **개통**: N1·N2·N4·N7·N8 + `/ncc/targets` 폐기분 = **6건**
- **제외(사유)**: **0건**(이번 8개+1 중 "가능하지만 이번엔 안 한다"는 이유가 성립하는 축이 없었다 — 전부 grain·가설이 확정돼 개통이거나, grain·가설 자체가 서지 않아 Jino 결정 필요로 빠졌다)
- **불가(원리)**: **0건**(N1~N8은 census §3-3이 이미 "등급 교차 가능 후보"로 1차 screening한 목록이라 원리적 불가 사례가 애초에 안 들어옴 — 원리적 불가 사례는 census §3-3의 ②③④⑤ 버킷에 있고 그건 N1~N8 밖)
- **Jino 결정 필요**: N3·N5·N6 = **3건**

---

## 4. 지시 배경 사실 검증 (교훈 #314)

- **"약 1,166행 주장"**: 실측 **1,166행 정확히 일치**(§1-3).
- **"오늘 개통·적재" 주장**: `measured_date` 전 행이 **2026-08-18 단일값**으로 확인, 창=1일 맞음(§0).
- **"하한 sentinel 약 62%"**: 실측 **729/1,166 = 62.53%** — 일치.
- **지시가 전제한 조인 경로 자체가 SHOPPING에 원리적으로 안 닿는다는 것은 지시에 없던 사실**이다(§1-3) — 배경 전제와 어긋나는 게 아니라 지시가 다루지 않은 구조적 제약을 이번 실측이 새로 드러냈다. 1차 출처(코드·prod 스키마)를 따라 SHOPPING 셀을 공란으로 두고 사유를 명시했다.

---

## 5. 이 실측이 답하지 못한 것

- **N1의 정확한 응답 단위**(type 7종이 한 GET에 같이 오는지, type별로 별도 파라미터가 필요한지) — swagger 문서 구조까지는 census가 확인했으나 실제 응답 바디는 실호출 금지라 미확인.
- **N4 SharedBudget의 실제 그룹 수·콜 수** — 목록 조회 자체를 하지 않아 예상 콜 수를 정확히 못 냈다(범위만 제시).
- **N7의 정확한 추가 콜 배수** — 현재 hh24 breakdown 스윕의 엔티티당 빈도를 이 세션에서 재확인하지 않았다(기존 코드 `naver_sa_ad_fetcher.py` 재독해 필요, 범위 밖).
- **밴드별 검색량-비용 비율의 의미** — 계산은 가능하나(§2-4) 시장 검색량과 계정 비용은 인과관계가 없어 그 비율 자체가 무엇을 나타내는지 이 데이터로는 결론 못 낸다.
- **1,166개 표본의 구성 방식**(계정 등록 키워드 시드인지 별도 방식인지) — 100% 매칭률의 원인을 이 문서가 추정하지 않았다(§2-1 각주에 가능성만 기록, 확정 아님).
- **SHOPPING 밴드의 실제 시장 검색량** — 이 조인 경로로는 원리적으로 도달 불가(§1-3). `naver_search_term_daily`(별도 그레인)를 거치는 다른 조인이 필요하나 이번 작업 범위 밖(지시가 명시한 경로만 실행).
- **N3의 실계정 가용성**(권한·상품 가입 여부) — 실호출 금지로 검증 불가, Jino 결정 필요로 남김.
- **N2 `item=Qi`가 죽은신호(qi_grade=4 고정)의 원인을 실제로 규명하는지** — 개통 판정은 grain·가능성 기준이고, 규명 여부는 개통 후에나 알 수 있다.

---

*작성: Sonnet(완료 QA 보강 담당, 읽기 전용). prod SELECT/PRAGMA/CREATE TEMP TABLE만 실행(원본 DB 물리 쓰기 0건, `-readonly` 플래그), 로컬 파일 1개 신규 작성(이 문서), git 커밋 0건, 배포 0건, 네이버 API 실호출 0건.*
