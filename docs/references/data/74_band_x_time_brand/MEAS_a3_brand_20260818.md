# 실측 — 성과등급(band) × A3(검색어 텍스트 속성: 브랜드어·토큰수·길이) (2026-08-18)

측정 담당(읽기 전용 서브에이전트). **경향성·인과 해석·권고는 쓰지 않는다** — 숫자와 목록만.
같은 폴더의 `MEAS_time_axis_20260818.md`(M축, 시간대)는 병행 세션 산출물 — 서로 다른 파일, 내용 간섭 없음.

## 0. 표기 규약

- 측정일시: 2026-08-18 KST 13:40경 SQL 실행(prod), 이후 로컬 python3 집계.
- prod 접속: `ssh -o BatchMode=yes sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db" < 로컬.sql` — 전량 SELECT.
- **정규화 규약**(사전 토큰·검색어 공통): `re.sub(r"[^0-9A-Za-z가-힣]", "", s).casefold()` — 공백·기호 전부 제거 후 casefold. 최소 길이 2자 미만은 사전에 등재하지 않는다.
  - 실측: 이번 사전 구성 후보(모델 코드 41개 raw, series root 8개, 자사 브랜드 2개) 중 **길이<2로 탈락한 항목은 0건**(전부 이미 2자 이상). 정규화 규약이 실제로 걸러낸 것은 길이가 아니라 **`match_confidence` 필터**였다(§B1 참조) — 28개 코드가 `unresolved/UNKNOWN/generic/구형/범위표기` 패턴으로 제외됨.
- `__backfill__` 배제: 이번에 새로 짠 집계 SQL(`q0_sanity.sql`·`q1_agg.sql`)마다 `adgroup_id != '__backfill__'` 조건을 명시적으로 다시 걸었다. 실측 결과 `naver_search_term_daily`에는 `__backfill__` 행 자체가 **0건**(아래 §0-1 참조, 이 테이블 한정 사실 — 다른 테이블에 일반화 금지).
- 창: **band_group_total.csv 창(2025-07-22~2026-08-16, 391일 파일, 데이터 390일분+헤더)** ∩ **naver_search_term_daily 창(2025-07-23~2026-08-17)** = **2025-07-23~2026-08-16(391일)** 을 B2~B4 전 구간에 적용.
- 층화: `campaign_type`(WEB_SITE=파워링크/SHOPPING=쇼핑)을 전 구간 의무 층화. `source`(expkeyword/shopping) ↔ `campaign_type` 대응은 §0-2에서 직접 검증(1:1 확인).

### 0-1. 원료 sanity (재현: `q0_sanity.sql`)

```sql
SELECT COUNT(*) FROM naver_search_term_daily;
SELECT COUNT(DISTINCT adgroup_id) FROM naver_search_term_daily;
SELECT MIN(ad_date), MAX(ad_date) FROM naver_search_term_daily;
SELECT COUNT(*) FROM naver_search_term_daily WHERE adgroup_id='__backfill__';
SELECT COUNT(*) FROM naver_search_term_daily WHERE search_term='' OR search_term IS NULL;
SELECT source, COUNT(*) FROM naver_search_term_daily GROUP BY source;
SELECT COUNT(*) FROM naver_search_term_daily WHERE ad_date BETWEEN '2025-07-23' AND '2026-08-16' AND adgroup_id != '__backfill__';
```

| 항목 | 값 |
|---|---|
| total_rows | 3,069,343 |
| distinct_adgroup | 729 |
| min_date / max_date | 2025-07-23 / 2026-08-17 |
| `__backfill__` 행 | 0 |
| blank search_term | 0 |
| source=expkeyword | 2,601,883 |
| source=shopping | 467,460 |
| 창 내 행수(2025-07-23~2026-08-16, backfill 배제) | 3,056,158 |

### 0-2. source ↔ campaign_type 대응 검증 (band_group_total 조인된 것만)

재현: `analyze.py` 내 `src_ctype_check` 누산 → `out_src_ctype_check.csv`.

| source | campaign_type | n_combo |
|---|---|---|
| expkeyword | WEB_SITE | 949,729 |
| shopping | SHOPPING | 262,031 |

**교차(예: expkeyword×SHOPPING, shopping×WEB_SITE)는 0건** — band_group_total에 조인된 범위 내에서 `source`는 `campaign_type`의 완전한 프록시임을 확인. 조인 안 된 행(39건, expkeyword)은 §B4-1 참조.

---

## B1. 브랜드어 후보의 데이터 유도

### 방법(재현 스크립트: `build_dict.py` → `merge_dict.py`)

후보 원천 3갈래를 각각 실측했다.

**(a) 단말 브랜드·시리즈·모델명**
1. `backend/app/data/device_launch_dates_kr.json` — `galaxy_s/galaxy_z_fold/galaxy_z_flip/galaxy_a/galaxy_tab/ipad` 6개 series 배열의 `model` 문자열. 전부 접두사가 series별로 통일됨(galaxy_s/a/z_fold/z_flip → "갤럭시", galaxy_tab → "갤럭시탭", ipad → "아이패드") — **직접 대조로 확인**.
2. `backend/app/data/iphone_launch_dates.json` — **확인 결과 모델명 없음**, `launch_dates` 배열(날짜 3개)뿐. `assert "model" not in json.dumps(...)`로 스크립트 내 재확인(통과).
3. `docs/references/data/63_band_decomposition/adgroup_model_map.csv`(855행, 우리가 실제 광고하는 모델) — `parsed_model`(series:code 형식, 77종) · `matched_json_model`(48종) · 원문 `adgroup_name`/`campaign_name`.

**(b) 자사 브랜드어**
- `docs/references/data/63_band_decomposition/adgroup_model_map.csv`에 `campaign_name='○ 00. 자사키워드'`(adgroup_id 2개: `grp-a001-01-000000031116306`=핸드폰필름, `grp-a001-01-000000043935093`=골프필름) 캠페인이 존재 — 캠페인명 자체가 "자사 브랜드 검색어를 잡는 캠페인"임을 시사하나, **adgroup_name엔 브랜드 리터럴이 없다**("핸드폰필름"은 일반명사).
- 그래서 그 2개 adgroup의 **실제 검색어 텍스트**를 prod에서 직접 조회(재현: `q_selfbrand_explore.sql`) → 최다클릭 검색어가 "오하이"(clk=757, cost=83,368원, n=363행), "오하이필름"(clk=427), "OHI"(clk=10, n=329행) 등으로 **리터럴 문자열 자체에서 자사 브랜드어 확인**. 출처는 캠페인명(가설)이 아니라 **검색어 리터럴**(확정 증거).

**(c) 경쟁사·타사 브랜드어**
- `adgroup_model_map.csv`의 39개 캠페인명 전수를 확인했으나 경쟁사 브랜드를 타깃하는 캠페인(컨퀘스팅)은 **0건**.
- **판정: 우리 데이터에서 경쟁사·타사 브랜드어를 유도할 수 있는 원천이 없다.** 라벨(경쟁사 여부 플래그)도, 컨퀘스팅 캠페인도, 문서화된 경쟁사 목록도 없음. 데이터에서 "이 검색어가 경쟁사 브랜드다"라고 판정할 근거가 전무하므로 사전에 넣지 않는다.
- 부수 발견(경쟁사는 아니지만 「우리 브랜드가 아닌 제조사 브랜드」): `● 13. 아이뮤즈_뮤패드` 캠페인 → "아이뮤즈"(iMuse, 태블릿 제조사)/"뮤패드"(MuPad, 그 제품군). naver_search_term_daily에서 "아이뮤즈K11"·"뮤패드K11" 등 실검색어로 교차확인(재현: `q_soda_muse.sql`). 이건 (a) 범주("우리가 실제 광고하는 단말 브랜드")로 분류 — 우리가 그 브랜드 액세서리를 파는 대상 단말이지 경쟁사가 아니다.

### 사전 구성 규칙(기계적, 판단 없음)

| 카테고리 | 추출 규칙 | 근거 |
|---|---|---|
| `brand_root`(제조사/제품군 대표어) | `parsed_model`의 series 접두사(신뢰도 무관 — series 판정 자체는 유지) → 매핑 테이블(galaxy_*→갤럭시/갤럭시탭, ipad→아이패드, iphone→아이폰, note→노트, mupad→뮤패드) + `_SS_WHITELIST_TOKENS`(브랜드성 3개만: 아이폰·아이패드·맥세이프, 기능어 3개는 제외) + campaign_name 리터럴(아이뮤즈) | 다중 소스 교차 |
| `self_brand` | naver_search_term_daily 실검색어 리터럴(자사키워드 캠페인) | 위 (b) |
| `model_code`(모델 코드) | ①`parsed_model`에서 `match_confidence ∈ {exact,fuzzy}`인 code만(unresolved/UNKNOWN/generic/구형/범위표기 제외) ②`adgroup_name`/`campaign_name`에서 정규식 `(폴드\|플립)\d+(와이드\|울트라\|SE)?`·`트라이폴드` 구조 추출 ③`소다케이스_갤럭시`/`소다케이스_아이폰` 캠페인의 `adgroup_name`에서 `_맥`/`_일`(케이스 부착방식 태그) 접미 제거 후 리터럴 채택 | 기계적 패턴, 판단 없음 |

### 결과

- **확정 토큰(고유): 97개** — `self_brand` 2 · `brand_root` 8 · `model_code` 87.
- 정규화 후 **중복(같은 토큰을 여러 출처가 만든 경우): 9건**(갤럭시=4계열 중복, 15/16/17/s22~s26=parsed_model↔소다케이스 adgroup_name 중복) — 병합해 출처를 병기.
- **모호성 위험(ambiguity_risk) 태깅**(기계적: 순수 숫자=high, 2자 이하=high, 영문1+숫자=medium, 그 외=low):
  | risk | 개수 |
  |---|---|
  | low | 67 |
  | medium(알파벳1+숫자, 예 s8) | 19 |
  | high(순수 숫자) | 7 |
  | high(2자 이하) | 4 |
- `parsed_model` 코드 41종(raw) 중 **28종이 `match_confidence≠exact/fuzzy` 또는 unresolved류 패턴으로 탈락**(A73, 노트 계열 6종 — 단 노트20/20울트라는 소다케이스 adgroup_name 경로로 별도 재확보, 나머지 iPad UNKNOWN_* 8종, S23FE/S25FE, budi 등).

브랜드 대표어(brand_root) 8종 + 자사(self_brand) 2종 전문(전체 목록·매치 비용은 §B5):

| token | category | 출처 요약 |
|---|---|---|
| 갤럭시 | brand_root | adgroup_model_map.csv:parsed_model(series galaxy_s/a/z_fold/z_flip 319행) ∪ device_launch_dates_kr.json:model 접두사 |
| 갤럭시탭 | brand_root | adgroup_model_map.csv:parsed_model(series galaxy_tab) ∪ device_launch_dates_kr.json:model |
| 아이폰 | brand_root | search_term_judge.py:_SS_WHITELIST_TOKENS ∪ adgroup_model_map.csv:parsed_model(series iphone) |
| 아이패드 | brand_root | search_term_judge.py:_SS_WHITELIST_TOKENS ∪ adgroup_model_map.csv:parsed_model(series ipad) ∪ device_launch_dates_kr.json |
| 맥세이프 | brand_root | search_term_judge.py:_SS_WHITELIST_TOKENS ∪ adgroup_model_map.csv:campaign_name('맥세이프카드케이스') |
| 노트 | brand_root | adgroup_model_map.csv:parsed_model(series note, 23행, 신뢰도 무관) |
| 뮤패드 | brand_root | adgroup_model_map.csv:parsed_model(series mupad) ∪ adgroup_name ∪ naver_search_term_daily 실검색어 |
| 아이뮤즈 | brand_root | adgroup_model_map.csv:campaign_name('● 13. 아이뮤즈_뮤패드') ∪ naver_search_term_daily 실검색어 |
| 오하이 | self_brand | naver_search_term_daily(prod):search_term, adgroup_id=grp-a001-01-000000031116306, clk=757 cost=83,368원 n=363행 |
| OHI | self_brand | naver_search_term_daily(prod):search_term, 같은 adgroup, clk=10 n=329행 + 'OHI필름' clk=6 n=61행 |

기능어로서 **브랜드어에서 제외한 것**(참고, `_SS_WHITELIST_TOKENS`의 나머지 3개): 강화유리·지문방지·보호필름 — 제조사/제품 고유명이 아니라 상품 카테고리·기능 서술어.

---

## B2. 사전 적용 — 검색어 분류

### 방법 (재현: `brand_seg.py`, trie 기반 최장일치, 원본은 `analyze.py`)

- 정규화된 검색어 텍스트에 대해 §B1 사전(97개 토큰) trie로 좌→우 스캔, 각 위치에서 가장 긴 매치를 소비, 매치 없으면 1글자 스킵(잔여) — semantic.py와 동일한 최장일치 방법.
- "브랜드어 포함" = 세그멘테이션 결과 유닛이 1개 이상. 저위험(low-risk, 67토큰)만 쓴 버전도 별도 산출(고위험 토큰의 영향 격리).
- 원료: prod 집계 쿼리(`q1_agg.sql`)로 (adgroup_id, search_term, source) 그레인 합계를 창 내 전량 다운로드(1,211,799 combo행, 창 내 daily-grain 3,056,158행과 대응) 후 로컬 처리.

```sql
SELECT adgroup_id, search_term, source,
       SUM(clk) clk, SUM(cost) cost, SUM(imp) imp,
       SUM(conv_purchase_cnt) conv_cnt, SUM(conv_purchase_amt) conv_amt,
       COUNT(*) n_rows
FROM naver_search_term_daily
WHERE ad_date BETWEEN '2025-07-23' AND '2026-08-16' AND adgroup_id != '__backfill__'
GROUP BY adgroup_id, search_term, source;
```

### 결과 — 브랜드어 포함 여부 (`out_b2_brand_match.csv`, 전체 97토큰)

| source | is_brand | n_combo | n_daily_rows | clk | cost |
|---|---|--:|--:|--:|--:|
| expkeyword(파워링크) | True | 698,888 | 2,028,143 | 63,733 | 47,945,333 |
| expkeyword(파워링크) | False | 250,880 | 570,615 | 13,175 | 8,094,777 |
| shopping | True | 180,292 | 319,011 | 13,533 | 19,075,215 |
| shopping | False | 81,739 | 138,389 | 2,377 | 3,291,390 |

### 저위험(low-risk 67토큰)만 쓴 버전 (`out_b2_brand_match_lowrisk.csv`)

| source | is_brand_lowrisk | n_combo | clk | cost |
|---|---|--:|--:|--:|
| expkeyword | True | 619,474 | 57,911 | 43,765,177 |
| expkeyword | False | 330,294 | 18,997 | 12,274,933 |
| shopping | True | 161,889 | 12,627 | 17,658,138 |
| shopping | False | 100,142 | 3,283 | 4,708,467 |

### 자사 브랜드어(오하이/OHI)만 (`out_b2_selfbrand_match.csv`)

| source | is_selfbrand | n_combo | clk | cost |
|---|---|--:|--:|--:|
| expkeyword | True | 8,619 | 3,609 | 1,086,960 |
| expkeyword | False | 941,149 | 73,299 | 54,953,150 |
| shopping | True | 4,260 | 516 | 627,907 |
| shopping | False | 257,771 | 15,394 | 21,738,698 |

### 매치 커버리지(사전이 인식한 검색어 글자 비율, `out_cov_hist_brand.csv`)

| bucket% | expkeyword n_combo | shopping n_combo |
|--:|--:|--:|
| 0(무매치) | 250,880 | 81,739 |
| 10 | 5,952 | 497 |
| 20 | 108,218 | 27,247 |
| 30 | 167,732 | 37,368 |
| 40 | 162,666 | 41,218 |
| 50 | 122,187 | 37,025 |
| 60 | 73,479 | 21,319 |
| 70 | 28,436 | 7,710 |
| 80 | 24,706 | 6,657 |
| 90 | 2,898 | 404 |
| 100(완전매치) | 2,614 | 847 |

브랜드 전용 사전(97개, 대부분 기기 모델명)이라 **완전매치(100%)는 드물다** — 검색어 대부분이 브랜드/모델 토큰 외에 기능어("필름"·"케이스"·"부착법" 등)를 포함하기 때문. 이는 §B3 광의 의미사전(464개, 기능어 포함)과의 커버리지 대조로 교차 확인됨(아래).

---

## B3. 토큰 수 · 길이 축

### 방법

- **글자 수**: 정규화 텍스트 길이, 6구간 버킷(1-3/4-6/7-10/11-15/16-20/21+).
- **의미 단위 개수**: D-NAO-191의 `semantic.py`(사전=상품명 has_cost 토큰 ∪ 그룹명 토큰 ∪ `_SS_WHITELIST_TOKENS` 전체 6개, 최장일치)를 **원본 그대로 재현**해 사전 크기부터 재검증 — **464개**(`funnel_out4_bep.csv` has_cost=1행 546개 상품명, `all_shopping_group_counts.csv` 116개 그룹명에서 토큰화) → 문서가 주장한 "464개"와 **일치 확인**. 단, 원본 스크립트는 `source='shopping' AND conv_purchase_cnt==0`인 부분집합에만 적용했던 반면, 이번 B3는 **전체 창 전체 source**에 적용(원 스크립트 목적 대비 확장 — 명시).

### 글자 수 분포 (`out_b3_len_bucket.csv`)

| source | len_bucket | n_combo | cost |
|---|---|--:|--:|
| expkeyword | 7-10 | 367,489 | 28,346,714 |
| expkeyword | 11-15 | 412,030 | 18,665,372 |
| expkeyword | 4-6 | 49,087 | 6,321,996 |
| expkeyword | 16-20 | 102,760 | 2,275,422 |
| expkeyword | 1-3 | 1,145 | 295,070 |
| expkeyword | 21+ | 17,257 | 135,536 |
| shopping | 7-10 | 111,844 | 11,174,213 |
| shopping | 11-15 | 103,452 | 7,788,832 |
| shopping | 4-6 | 20,070 | 2,579,358 |
| shopping | 16-20 | 25,287 | 654,558 |
| shopping | 1-3 | 1,378 | 169,644 |
| shopping | 21+ | 0 | 0 |

(shopping 소스엔 21자 이상 검색어 조합이 0건 관측 — 파워링크만 21+ 17,257건.)

### 의미 단위 개수 분포 (`out_b3_unit_bucket.csv`, 464개 의미사전 기준)

| source | unit_bucket | n_combo | cost |
|---|---|--:|--:|
| expkeyword | 0(전량 잔여) | 39,662 | 474,534 |
| expkeyword | 1 | 258,802 | 6,044,190 |
| expkeyword | 2 | 374,998 | 32,754,774 |
| expkeyword | 3 | 208,496 | 14,599,511 |
| expkeyword | 4 | 58,043 | 1,985,962 |
| expkeyword | 5+ | 9,767 | 181,139 |
| shopping | 0 | 15,659 | 306,855 |
| shopping | 1 | 58,000 | 2,144,377 |
| shopping | 2 | 104,803 | 12,699,457 |
| shopping | 3 | 58,298 | 5,542,694 |
| shopping | 4 | 21,542 | 1,445,669 |
| shopping | 5+ | 3,729 | 227,553 |

공백 토큰(97.4%가 1개, D-NAO-191 기 실측)과 달리 **의미 단위 개수는 2~3개에 몰린다**(expkeyword: 2단위 374,998건이 최빈, cost도 최대 32.75M원 — 관측치, 해석 없음).

### 의미사전 커버리지 (`out_cov_hist_semantic.csv`, 464개 사전 기준)

| bucket% | expkeyword n_combo | shopping n_combo |
|--:|--:|--:|
| 0 | 39,662 | 15,659 |
| 80 | 193,151 | 55,611 |
| 100 | 118,674 | 67,340 |

(전체 11버킷은 CSV 원본 참조. 464개 광의 사전은 97개 브랜드 전용 사전보다 100%매치 비중이 훨씬 높음 — expkeyword 100%버킷 118,674 vs 브랜드전용 2,614.)

---

## B4. 등급 교차 — A3 × band

### 조인·매치율

- 키: `naver_search_term_daily.adgroup_id` ↔ `band_group_total.csv.adgroup_id`.
- **매치율(창 내 combo 기준)**: expkeyword 949,729/949,768 = **99.996%**(미매치 39건, 전부 cost=0) · shopping 262,031/262,031 = **100.000%**(미매치 0건). (`out_join_match_rate.csv`)
- **캠페인 유형 층화**: §0-2에서 source=campaign_type 1:1 확인됨 — band 조인 결과의 campaign_type은 band_group_total 자체 값(WEB_SITE/SHOPPING) 사용.

### B4-1. band × campaign_type × 브랜드어 포함 (`out_b4_band_x_brand.csv`, 전체 표는 CSV)

| campaign_type | band | is_brand | n_combo | clk | cost | conv_cnt | conv_amt |
|---|---|---|--:|--:|--:|--:|--:|
| WEB_SITE | band1 | True | 375,111 | 35,843 | 26,200,398 | 0 | 0 |
| WEB_SITE | band1 | False | 150,577 | 6,536 | 3,904,113 | 0 | 0 |
| WEB_SITE | band2 | True | 103,129 | 11,946 | 10,909,959 | 0 | 0 |
| WEB_SITE | band2 | False | 31,056 | 2,187 | 1,286,595 | 0 | 0 |
| WEB_SITE | band3 | True | 199,789 | 15,015 | 10,392,460 | 0 | 0 |
| WEB_SITE | band3 | False | 62,429 | 4,315 | 2,840,287 | 0 | 0 |
| WEB_SITE | band4_unjudgeable | True | 20,776 | 929 | 442,516 | 0 | 0 |
| WEB_SITE | band4_unjudgeable | False | 6,802 | 137 | 63,782 | 0 | 0 |
| SHOPPING | band1 | True | 64,289 | 4,740 | 6,947,069 | 796 | 13,472,980 |
| SHOPPING | band1 | False | 34,909 | 1,391 | 1,812,269 | 181 | 2,832,400 |
| SHOPPING | band2 | True | 35,228 | 1,888 | 3,030,723 | 323 | 5,112,320 |
| SHOPPING | band2 | False | 15,629 | 332 | 590,091 | 38 | 565,000 |
| SHOPPING | band3 | True | 79,554 | 6,873 | 9,070,986 | 713 | 11,775,590 |
| SHOPPING | band3 | False | 30,958 | 651 | 886,871 | 42 | 683,600 |
| SHOPPING | band4_unjudgeable | True | 1,219 | 32 | 26,437 | 0 | 0 |
| SHOPPING | band4_unjudgeable | False | 243 | 3 | 2,159 | 0 | 0 |

(WEB_SITE는 파워링크라 전환 귀속 불가 확정 사실 — conv_cnt/amt 항상 0, D-NAO 기 확정.)

### B4-2. band × campaign_type × 글자 수 버킷, B4-3. band × campaign_type × 의미단위 버킷

전체 표는 `out_b4_band_x_len.csv` · `out_b4_band_x_unit.csv`(각 CSV 다운로드). WEB_SITE·band1 예시:

| len_bucket | n_combo | cost |
|---|--:|--:|
| 7-10 | 187,236 | 16,167,447 |
| 11-15 | 191,306 | 8,712,540 |
| 4-6 | 22,556 | 3,013,946 |
| 16-20 | 40,552 | 1,109,610 |
| 21+ | 6,896 | 90,290 |
| 1-3 | 141 | 10,278 |

---

## B5. Jino 확인용 사전 목록

### 확정 목록 — `brand_dict_confirmed.csv`(97행, 비용 큰 순)

컬럼: `token,raw,category,ambiguity_risk,matched_n_combo,matched_clk,matched_cost,matched_conv_cnt,matched_conv_amt,sources,notes`.

상위 15행(비용순):

| token | category | risk | matched_cost | matched_clk |
|---|---|---|--:|--:|
| 갤럭시 | brand_root | low | 23,309,133 | 24,300 |
| 아이폰 | brand_root | low | 11,730,522 | 11,648 |
| 폴드7 | model_code | low | 4,271,231 | 5,844 |
| 갤럭시탭 | brand_root | low | 3,997,475 | 5,364 |
| 폴드8 | model_code | low | 3,386,793 | 2,942 |
| 플립7 | model_code | low | 2,934,546 | 3,551 |
| s25 | model_code | medium | 2,573,858 | 3,194 |
| s23울트라 | model_code | low | 2,353,723 | 1,951 |
| 맥세이프 | brand_root | low | 2,097,664 | 3,772 |
| s25울트라 | model_code | low | 2,026,397 | 2,226 |
| 아이패드 | brand_root | low | 2,008,523 | 2,878 |
| s26 | model_code | medium | 1,943,983 | 1,698 |
| s10 | model_code | medium | 1,899,128 | 2,446 |
| s23 | model_code | medium | 1,841,673 | 1,840 |
| 17 | model_code | high(순수숫자) | 1,807,928 | 2,018 |

전체는 CSV 참조. 자사 브랜드(오하이=1,677,835원/ohi=37,402원), 뮤패드(161,117원), 아이뮤즈(88,463원) 등 하위 항목도 CSV에 전부 있음.

### Jino 확인 대기(회색) — `brand_dict_pending_jino.csv`(4행, 비용 큰 순)

| token | 검색어 서브스트링 매치(참고용, 사전 매치 아님) | 왜 못 가르는가 |
|---|---|---|
| 버디 | n_combo=2,409, clk=710, cost=398,874원 | `galaxy_a:budi_unresolved`(LG U+ 갤럭시 버디4/5)의 series는 확인되나 `match_confidence=unresolved`. 동시에 골프필름 adgroup(자사키워드 캠페인)의 검색어 "버디필름"·"오하이버디필름"은 골프 용어(버디=birdie) 문맥과 공존 — 같은 표층형이 두 개념에 걸쳐 실측됨. 사전에 넣으면 골프 검색어를 폰 브랜드로 오분류. |
| 일미리케이스 | n_combo=107, clk=89, cost=17,779원 | 자사몰(○ 01. 자사몰) adgroup_name이나 "일미리"=1mm 두께 서술어로 보여 브랜드어인지 규격 서술어인지 데이터만으로 판정 불가. |
| A73 | n_combo=72, clk=4, cost=2,847원 | device_launch_dates_kr.json 자체가 출처 상충으로 `launch_kr=null`(status=unreleased). adgroup_model_map.csv 2행 존재하나 `match_confidence=unresolved`. |
| 소다케이스 | n_combo=51, clk=6, cost=2,372원 | 자사 제품 캠페인명(소다케이스_갤럭시/아이폰)엔 있으나 실검색어 244건 중 대다수가 "오하이"와 결합된 형태로만 나타남(예: 오하이하이브리드소다케이스). 독립 자사 브랜드어인지 제품 스타일/재질 서술어인지 데이터만으로 못 가른다. |

("substring_n_combo" 등은 §B2 trie 매치가 아니라 원문 검색어에 해당 정규화 문자열이 부분 포함된 combo행을 직접 센 것 — 사전에 없는 토큰이라 trie로는 집계 안 됨, 참고용 스캔.)

---

## 답하지 못한 것

1. **경쟁사·타사 브랜드어(B1-c)**: 우리 데이터에 라벨·컨퀘스팅 캠페인·경쟁사 목록이 전무해 유도 자체가 불가능 — "미상"이 아니라 "원천 없음"으로 확정.
2. **모델 코드(model_code) 87개의 오검출율**: high/medium 위험 태깅(26/87 = 30%)까지는 기계적으로 표시했으나, **실제 오검출건수(예: "17"이 iPhone 17이 아니라 다른 숫자 문맥으로 매치된 건수)는 세지 않았다** — 개별 검색어 문맥 판정은 이 실측 범위 밖(추정 등재 금지 원칙상 임의 재분류 불가).
3. **iPad 서브모델(air4/5/mini6/mini7/pro3-5 등) 정밀 매치**: `matched_json_model`에 "A|B" 이중 후보(예 `ipad:air6|ipad:air7`)로 남은 10~13건은 어느 세대인지 원천 데이터가 확정하지 못해 사전에서 두 후보 모두 등재했다(코드가 명확히 구분 안 된 상태로 넘어옴) — 재확인은 이 실측 범위 밖.
4. **의미단위 사전(464개)의 WEB_SITE 적합성**: 원 스크립트(semantic.py)는 `source='shopping'`에만 검증된 사전이다. 이번 B3에서 WEB_SITE(파워링크)에도 동일 사전을 적용했으나, 상품명(has_cost) 토큰이 파워링크 검색어를 얼마나 잘 커버하는지는 **별도 검증하지 않았다**(§B3 커버리지 히스토그램의 raw 숫자만 제공, 적합성 판정 없음).
5. **band4_unjudgeable·excluded_cost0 그룹의 A3 축 의미**: band 자체가 "판정불가/비용0"이라 A3와 교차해도 해석 여지가 없다 — 표에는 넣었으나(§B4-1) 별도 언급 안 함.
6. **정규화 규약의 부수 손실**: 검색어 원문의 따옴표·해시(`#`)·공백 등 기호가 이번 정규화로 전부 제거됐다 — 몇 글자가 제거됐는지 전체 총량은 세지 않았다(개별 예시만 §0-1/B2 서두에서 관측: `'"스마트폰무점착보호필름"'`, `'#스마트폰강화필름'`).

---

## ★부록 Z — 코디네이터 사후 실측 정정 (2026-08-18 14:1x KST, 읽기 전용)

이 문서의 창 표기(「2025-07-23 ~ 2026-08-16」)는 **두 source를 합친 값**이라, A3 축이
무엇을 판정할 수 있는지를 **과대 표시한다.** source별로 다시 재면 성질이 갈린다.

```sql
-- prod 읽기 전용. ★__backfill__ 센티널 배제(공용 필터가 없어 매번 다시 적는다)
SELECT source,
       COUNT(*)                                             rows_all,
       SUM(CASE WHEN conv_purchase_cnt>0 THEN 1 ELSE 0 END) rows_conv_pos,
       SUM(conv_purchase_cnt)  conv_cnt_sum,
       SUM(conv_purchase_amt)  conv_amt_sum,
       SUM(conv_direct_cnt)    conv_direct_sum,
       SUM(cost)               cost_sum,
       MIN(ad_date) d_min, MAX(ad_date) d_max
FROM naver_search_term_daily
WHERE adgroup_id <> '__backfill__'
GROUP BY source;
```

| source | 행수 | 전환>0 행 | 전환건수 합 | 전환액 합 | 직접전환 합 | 비용 | 창 | 일수 |
|---|---|---|---|---|---|---|---|---|
| `expkeyword`(→WEB_SITE) | 2,601,883 | **0** | **0** | **0** | **0** | 56,198,010 | 2025-07-23~2026-08-17 | **391일** |
| `shopping`(→SHOPPING) | 467,460 | 2,011 | 2,154 | 35,368,890 | 1,825 | 22,901,783 | **2026-07-04**~2026-08-17 | **45일** |

행수 합 3,069,343은 ref 73 #11의 값과 일치한다 — **총계는 맞고 분해가 없었다.**

### 이 정정이 바꾸는 것 3가지

1. ★**「391일을 온전히 덮는 유일한 미교차 축」(ref 73 #11)은 `expkeyword`에만 참이다.**
   그런데 그쪽은 **전환 컬럼 4종이 전부 0**이라 «브랜드어가 성과에 어떤가»를 **원리적으로
   판정할 수 없다.** 본문 B4 표의 WEB_SITE 행이 전부 `conv_cnt=0 · conv_amt=0`인 것은
   집계 실수가 아니라 **원장이 그렇게 생긴 것**이다.
2. **전환이 있는 쪽(`shopping`)의 창은 45일**이다. 등급(band)은 **391일 창**에서 산출됐으므로
   (`data/63_band_decomposition/`), shopping의 A3 교차는 «391일로 매긴 등급» × «45일치 검색어
   성과»다. **두 창이 8.7배 어긋나 있다** — 본문 B4의 SHOPPING 전환 수치를 인용할 때 이
   비대칭을 빼면 안 된다.
3. 따라서 A3 축의 현재 결론력은 본문이 시사하는 것보다 **좁다**: 비용·클릭 분포는 391일로
   말할 수 있고, **성과(전환)는 shopping 45일로만** 말할 수 있다.

### 원인 `[미상]` — 추정하지 않는다

`expkeyword` 전환 0이 **①수집기가 그 필드를 안 채우는 것**인지 **②API가 파워링크 검색어
grain에 전환을 안 주는 것**인지 이번에 **재지 않았다.** ★단 「진짜로 전환이 0이었다」는
가능성은 낮다 — 같은 WEB_SITE 그룹들이 등급 산출 원자료(`band_group_total.csv`)에서는
`conv_amt`를 갖고 있다(예: `grp-a001-01-000000031116306` WEB_SITE `conv_amt=8,264,340`).
**그레인이 다른 두 원장이 서로 다른 말을 하는 상태**이고, 어느 쪽이 맞는지는 수집 경로를
읽어야 안다. 다음 세션 몫으로 남긴다(광고 원장 4통의 그레인 불일치 부류 —
`ad-ledger-four-buckets`).
