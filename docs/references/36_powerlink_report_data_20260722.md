# 36. 파워링크(WEB_SITE) 대상 네이버 SA API 데이터 전수 실측

- 실측일: 2026-07-22 (라이브 VM sellc, CUSTOMER_ID=1313769, 원칙22 — read-only, 광고 엔티티 쓰기 0건)
- 방법: prod에서 `naver_sa_ad_fetcher` 기존 헬퍼로 직접 API 호출(대용량 리포트 9종 생성+다운로드,
  `/stats` 필드 브루트포스, `/ncc/keywords`·`/ncc/adgroups`·`/ncc/ad-extensions` 원본 응답 검사,
  과거 날짜 리포트 생성 시도로 보존 한도 확인). 대표 캠페인: `cmp-a001-01-000000010236310`
  ([P_Test] 아이패드 파워링크, 그룹 19개·키워드 다수). 전 계정 WEB_SITE 캠페인 13개(그룹 523개)를
  필터링 기준으로 사용.
- ⚠️ 모든 대용량 리포트 생성은 기존 수집기(`ensure_reports_built`/`create_stat_report`)와 동일한
  정규 메커니즘(POST `/stat-reports` + GET 다운로드) — 광고 엔티티(캠페인/그룹/키워드/소재) 쓰기는
  전혀 수행하지 않음.
- 컬럼 의미가 실측만으로 확정 안 되는 경우 **"미상"**으로 표기(추정 배제 원칙).

## 0. 최우선 발견 — EXPKEYWORD(파워링크 확장검색어) 수집이 처음부터 전량 실패 중

- `naver_search_term_daily` 실측(2026-07-22 라이브 DB 쿼리): **`source='expkeyword'` 행 = 0건**
  (전체 150,503행이 전부 `source='shopping'`, 2026-07-04~07-20). 테이블 생성(P2-S1, ~07-07) 이후
  현재까지 파워링크 확장검색어 성과가 단 한 번도 적재된 적이 없음.
- 원인(재현 확인): `docs/references/22` §2가 "EXPKEYWORD는 실다운로드 검증 전이라 재확인 권장"이라고
  남긴 가정 — **"SHOPPINGKEYWORD_DETAIL과 동일 16열 레이아웃"**이 실제로는 틀렸다.
  - SHOPPINGKEYWORD_DETAIL 실다운로드(2026-07-19): **16열**, 전체 14,716행.
  - EXPKEYWORD 실다운로드(같은 날짜): **12열**(예외 없이 6,788행 전부 12열 — 소재ID·비즈채널ID
    컬럼이 아예 없는 더 짧은 레이아웃).
  - `naver_sa_ad_fetcher.fetch_search_term_daily()`는 두 리포트 타입에 동일한 `ST_COL_*` 상수
    (16열 기준, `ST_COL_RANK_SUM=14`)를 재사용 → 가드 `if len(cols) <= ST_COL_RANK_SUM: continue`가
    `12 <= 14`로 항상 참이 되어 **EXPKEYWORD 행이 전량 스킵**된다.
  - 실측 확인: `fetch_search_term_daily("EXPKEYWORD", 2026-07-19, 2026-07-19)` 호출 결과 **`0`행**
    (원본 TSV엔 6,788행 존재).
- EXPKEYWORD 실제 컬럼 구조(교차검증으로 재구성, 아래 §1.7 참조): imp/clk/cost 합계가 같은 날
  WEB_SITE AD 리포트 합계 대비 96.7%/97.3%/98.8% 일치 — col8=imp, col9=clk, col10=cost로 확정.
  col7·col11은 여전히 미상.
- **영향 범위**: `search_term_ss_lane.py`(SS3/SS4)는 "소스(shopping/expkeyword) 무관 전건 대상"으로
  설계돼 있으나, expkeyword 쪽은 데이터가 한 번도 들어온 적이 없어 파워링크의 확장검색어
  제외·승격 판단이 구조적으로 전맹(全盲) 상태다. 이 세션은 read-only 스코프라 **수정하지 않음** —
  발견 사실만 기록.

## 1. 대용량 리포트 9종 컬럼 실측 (2026-07-19 KST, 파워링크 행 기준 샘플)

공통: 헤더 없는 TSV, 날짜는 YYYYMMDD, 첫 컬럼=일자·둘째=고객ID(상수 1313769). 파워링크는
`cmp-a001-01-…` 접두, 캠페인 유형 WEB_SITE. "col7류 미상값"(8753/27758/33421/122875/…)은 모든
리포트에 반복 등장하는 정체불명 숫자 컬럼 — `docs/references/21`이 이미 "지표 아님, 불필요"로
표기했던 것과 동일 위치/값역이며, 이번 실측도 동일 결론(매체/지면 ID로 추정되나 확정 불가).

### 1.1 AD (14열) — CONFIRMED (기존 ref21과 일치)

grain: 일자×캠페인×그룹×키워드×소재×기기. WEB_SITE 행에서 keyword_id는 항상 `nkw-…`(쇼핑만 `-`).

| idx | 값 예시 | 의미 |
|---|---|---|
| 0 | `20260719` | 일자 |
| 2 | `cmp-a001-01-000000010206612` | 캠페인 ID |
| 3 | `grp-a001-01-000000060530499` | 그룹 ID |
| 4 | `nkw-a001-01-000007818236552` | 키워드 ID |
| 5 | `nad-a001-01-000000461534078` | 소재 ID |
| 6 | `bsn-a001-00-000000001021037` | 비즈채널 ID |
| 7 | `8753` | 미상(지표 아님) |
| 8 | `M` | 기기(M/P) |
| 9 | `2` | 노출수 |
| 10 | `0` | 클릭수 |
| 11 | `0` | 비용(원, VAT 별도) |
| 12 | `2` | 노출순위 합(avg_rank = col12/col9) |
| 13 | `0` | 미상(AD 리포트엔 전환 없음, 전량 0 관측) |

- 파워링크 전용 필터(col2 in WEB_SITE) 시 5,015행 중 2,102행.

### 1.2 AD_DETAIL (16열) — 신규 실측(코드 미사용)

AD(14열) + 2컬럼 삽입(위치 idx7~8, `03`/`02`류) — bizChannel(idx6) 다음, 미상 col(idx9,
값역은 AD의 col7과 동일한 8753류) 앞.

```
['20260719','1313769','cmp-a001-01-000000010206612','grp-a001-01-000000060617379',
 'nkw-a001-01-000007825091114','nad-a001-01-000000462886403','bsn-a001-00-000000001021037',
 '03','02','8753','M','1','0','0','2','0']
```

| idx | 값 예시 | 의미 |
|---|---|---|
| 0~6 | (AD와 동일) | 일자~비즈채널 |
| **7** | `03` | 미상 — AD 대비 추가된 상세 코드 A(관측값: `03`·`19`·`09`·`21` 등) |
| **8** | `02`/`99`/`03`/`09` | 미상 — 추가 상세 코드 B (매체/노출영역 세부 구분으로 추정, 확정불가) |
| 9 | `8753` | 미상(AD의 col7과 동일 값역) |
| 10 | `M` | 기기 |
| 11~14 | imp/clk/cost/rank_sum | AD의 col9~12와 동일 의미(대조 위치 일치) |
| 15 | `0` | 미상(전량 0) |

- **AD 대비 grain 차이**: AD는 총 14,716개 파워링크+쇼핑 계정 전체에서 5,015행인데 AD_DETAIL은
  같은 날 34,180행(WEB_SITE만 13,590행) — AD의 (일자×캠페인×그룹×키워드×소재×기기) grain을
  idx7·8의 세부 코드로 한 단계 더 쪼갠 것으로 추정(노출 지면/매체 세부). 합계는 AD와 근사(별도
  전수 대조는 미실시 — 이번 스코프에서 확정 불필요 판단).

### 1.3 AD_CONVERSION (13열) — CONFIRMED (기존 ref21과 일치)

grain: AD와 동일 + 직접/간접·액션 구분.

```
['20260719','1313769','cmp-a001-01-000000006006664','grp-a001-01-000000031116306',
 'nkw-a001-01-000005009913563','nad-a001-01-000000264097307','bsn-a001-00-000000001021037',
 '8753','M','1','purchase','1','17800']
```
idx7=미상(8753류)·8=기기·**9=직접(1)/간접(2)**·**10=액션(purchase/add_to_cart)**·**11=전환수**·
**12=전환매출액**. 파워링크(WEB_SITE) 행 93행 중 25행.

### 1.4 AD_CONVERSION_DETAIL (15열) — 신규 실측(코드 미사용)

AD_CONVERSION(13열)에 AD_DETAIL과 동일한 2컬럼(idx7~8, 상세코드 A·B) 삽입 — 이하 컬럼은 전부
+2 밀림.

```
['20260719','1313769','cmp-a001-01-000000010217616','grp-a001-01-000000060604039',
 'nkw-a001-01-000007823031966','nad-a001-01-000000462316150','bsn-a001-00-000000001021037',
 '19','03','8753','M','1','purchase','1','18900']
```
idx7·8=미상 상세코드(AD_DETAIL과 동일 패턴, 값 `09`/`19`/`21` 등)·9=미상(8753류)·10=기기·
**11=직접/간접**·**12=액션**·**13=전환수**·**14=전환매출액**. WEB_SITE 26행 관측(전체 97행 중).
D-NAO-57류 상품 귀속 세분화 목적으로 보이나, 확장 코드(idx7·8)의 정확한 의미는 미상.

### 1.5 ADEXTENSION (15열) — 신규 실측(코드 미사용)

grain: AD + 확장소재(extension) ID. `/ncc/ad-extensions?ids=` 실측으로 확장소재 엔티티 확인 가능
(예: `ext-a001-01-000000372377058` → `type:"SHOPPING_WEB"`, 스마트스토어 링크형 확장소재).

```
['20260719','1313769','cmp-a001-01-000000010206612','grp-a001-01-000000060612844',
 'nkw-a001-01-000007825144070','nad-a001-01-000000462886154','ext-a001-01-000000372377058',
 'bsn-a001-00-000000005675466','8753','M','2','0','0','4','0']
```
idx6=**확장소재 ID(ext-…)**(AD의 idx6 자리에 삽입, 이하 밀림)·idx7=비즈채널·idx8=미상(8753류)·
idx9=기기·idx10~13=imp/clk/cost/rank_sum·idx14=미상(0). WEB_SITE 2,660행(전체 5,937행 중).

### 1.6 ADEXTENSION_CONVERSION (14열) — 신규 실측(코드 미사용)

AD_CONVERSION(13열)에 확장소재 ID(idx6) 삽입.

```
['20260719','1313769','cmp-a001-01-000000010236263','grp-a001-01-000000067675781',
 'nkw-a001-01-000008207898408','nad-a001-01-000000528873498','ext-a001-01-000000433333388',
 'bsn-a001-00-000000005675466','8753','M','1','add_to_cart','1','19800']
```
idx6=확장소재 ID·idx7=비즈채널·idx8=미상·idx9=기기·**idx10=직접/간접**·**idx11=액션**·
**idx12=전환수**·**idx13=전환매출액**. 계정 전체 6행 중 **6행 전부 WEB_SITE**(파워링크 확장소재
전환은 희소하지만 관측 즉시 잡힘).

### 1.7 EXPKEYWORD (12열) — 신규 실측, ★기존 문서 가정(16열) 반증

**§0 참조 — 이 세션의 최우선 발견.** 소재 ID·비즈채널 ID 컬럼이 아예 없는 더 짧은 레이아웃.

```
['20260719','1313769','cmp-a001-01-000000010236263','grp-a001-01-000000060823768',
 'Z플립7여행촬영','8753','M','1','1','1','237','0']
```

| idx | 값 예시 | 의미 | 근거 |
|---|---|---|---|
| 0 | `20260719` | 일자 | — |
| 2 | `cmp-…` | 캠페인 ID | — |
| 3 | `grp-…` | 그룹 ID | — |
| 4 | `Z플립7여행촬영` | **검색어 텍스트**(등록 키워드 아님) | — |
| 5 | `8753` | 미상(AD 리포트 col7과 동일 값역) | — |
| 6 | `M` | 기기(M/P) | — |
| **7** | `0`/`1`/`2` | 미상 — 소값 플래그류(합계가 imp/clk 어느 쪽과도 안 맞아 rank_sum 아님) | WEB_SITE 전체 합계 5,200 (imp 27,958의 18.6%, 아무 지표와도 정합 안됨) |
| **8** | `1`~`23` | **노출수(imp)** | WEB_SITE 전체 합계 27,958 vs 같은 날 AD 리포트 WEB_SITE 노출합계 28,922 (**96.7% 일치**) |
| **9** | `0`~`2` | **클릭수(clk)** | 합계 143 vs AD 147 (**97.3% 일치**) |
| **10** | `0`~`2795` | **비용(cost)** | 합계 154,313 vs AD 156,118 (**98.8% 일치**) |
| 11 | `0` | 미상(전 표본 관측값 0) | — |

- 교차검증 방식: `docs/references/22`가 SHOPPINGKEYWORD_DETAIL에 썼던 것과 동일하게, 특정
  adgroup(`grp-a001-01-000000060823768`, 셀카봉 계열 확장검색어 다수) EXPKEYWORD 행 합계를 같은
  adgroup의 AD 리포트(등록키워드 전체) 합계와 대조. 100% 일치는 아니나(확장검색이 등록키워드의
  부분집합 성격이라 자연스러운 근사), imp/clk/cost 3개 모두 96~99% 범위로 일관되게 수렴해
  col8/9/10 = imp/clk/cost로 결론.
- rank_sum에 해당하는 컬럼은 이 12열 레이아웃에서 **찾지 못함**(idx7·11 둘 다 정합 안 됨) —
  EXPKEYWORD에는 순위 정보가 아예 없을 가능성.
- WEB_SITE 전체 6,788행(=전체 관측치와 동일, 즉 이 리포트는 파워링크 전용 — SHOPPING/BRAND_SEARCH
  행이 아예 없음. 확장검색어 리포트는 WEB_SITE에서만 의미가 있다는 §0.5 기존 확정과 정합).

### 1.8 CRITERION (7열) — 신규 실측(코드 미사용), 파워링크 매칭 방법 특이

grain: 일자×(그룹~기준코드)×기기. **캠페인 ID 컬럼이 없음** — `그룹ID~코드` 복합키만 존재.

```
['20260719','1313769','grp-a001-01-000000060480724~AG5054','P','1','1','980']
```
idx2=`그룹ID~기준코드`(예: `AG5054`)·idx3=기기(M/P)·idx4=노출수·idx5=클릭수·idx6=비용.

- WEB_SITE 매칭은 캠페인ID가 아니라 **그룹ID 목록(523개) 사전 조회 후 `~` 앞부분 대조**로만
  가능(캠페인 필터 직접 불가). 이렇게 필터링 시 6,399행 중 3,528행이 WEB_SITE.
- 기준코드(`~` 뒷부분) 접두 분포(계정 전체, 07-19): `AG`(3,805) / `GNM`(653) / `AGXXXX`(527) /
  `GNF`(517) / `GNU`(512) / `AD`(370) / `SDSUN`(15).
- **패턴 추정(비공식, 확정 불가)**: `AG####`는 연령대 구간 코드로 보임(관측값 예: `AG1924`,
  `AG2529`, `AG3034`, `AG3539`, `AG4044`, `AG4549` — 각각 19~24/25~29/30~34/35~39/40~44/45~49세
  구간과 순서·간격이 일치), `AGXXXX`는 연령 미상. `GNM`/`GNF`/`GNU`는 값 패턴상 성별
  남/여/미상(Gender Male/Female/Unknown)으로 추정. `AD0099`·`SDSUN0003`은 정체 불명(소수
  관측, 매체 또는 광고유형 코드로 추정하나 근거 부족 — 이 4개는 공식 문서 확인 없이는
  "미상"으로만 남긴다).
- 공식 Naver 검색광고 API 문서(naver/searchad-apidoc)에서 CRITERION 리포트의 컬럼/코드 정의를
  찾으려 시도했으나, 공개 저장소에는 샘플 코드만 있고 리포트 컬럼 스펙 문서는 확인하지 못함
  (문서에서 확인 안 됨 — 위 추정은 데이터 패턴 근거일 뿐).

### 1.9 CRITERION_CONVERSION (8열) — 신규 실측(코드 미사용)

CRITERION(7열) + 액션/전환수/매출.

```
['20260719','1313769','grp-a001-01-000000060826108~AG4549','M','1','purchase','1','35600']
```
idx2=그룹~코드·idx3=기기·idx4=미상(항상 `1`, 전환행이라 자기 자신 카운트로 추정)·**idx5=액션**·
**idx6=전환수**·**idx7=전환매출액**. WEB_SITE 76행(전체 208행 중, 그룹ID 목록 대조로 필터).

## 2. `/stats` API 필드 가용성 실측

- 테스트 대상: WEB_SITE 키워드 1건(`nkw-a001-01-000008376710174`, 실적 있는 키워드), 그룹·캠페인
  단위도 교차 확인.

### 2.1 유효 필드(200) vs 무효 필드(400)

| 필드 | 결과 | 비고 |
|---|---|---|
| `impCnt` | ✅ | 기존 사용 중 |
| `clkCnt` | ✅ | 기존 사용 중 |
| `salesAmt` | ✅ | 비용(원), 기존 사용 중 |
| `ccnt` | ✅ | 전환수, 기존 사용 중 |
| `convAmt` | ✅ | 전환매출, 기존 사용 중 |
| `avgRnk` | ✅ | 평균순위, 기존 사용 중 |
| **`ctr`** | ✅ **미사용** | 클릭률(%) — 실측값 `11.43` 등, 직접 계산 가능하나 API가 자체 제공 |
| **`cpc`** | ✅ **미사용** | 평균 CPC(원) — `cost/clk`와 동일 값 추정, API 자체 제공 |
| **`crto`** | ✅ **미사용** | 전환율(%) — 실측값 `100`(4클릭 4전환), `50`, `14.29` 등 — `ccnt/clkCnt*100`으로 추정 |
| **`viewCnt`** | ✅ **미사용** | 전 표본에서 항상 `0` — 동영상/디스플레이 계열 지표로 추정, 파워링크 텍스트 광고엔 무의미할 가능성 |
| `mtCnt`,`dvcCnt`,`recAvgRnk`,`avgRnk_cnts`,`ccnt_1/7/30`,`clkCnts`,`impCnts`,`avgCpc`,`avgCtr`,`avgCvr`,`cnvAmt` | ❌ 400 | `{"code":11001,"message":"잘못된 파라미터 형식입니다."}` — 존재하지 않는 필드명 |

- **10개 필드(impCnt/clkCnt/salesAmt/ccnt/convAmt/avgRnk/ctr/cpc/crto/viewCnt) 동시 요청도 정상
  200** — 필드 개수 제한 없음(적어도 10개까지 확인).
- ctr/cpc/crto는 impCnt/clkCnt/salesAmt/ccnt에서 산출 가능한 파생값으로 보이나, API가 직접
  내려주므로 소비측 계산 로직 중복 제거에 쓸 수 있음(파생 공식 자체는 미검증 — 반올림/소수점
  처리가 API와 우리 계산이 다를 수 있어 완전 대체 전엔 대조 필요).

### 2.2 breakdown 가용성

- **`breakdown=hh24`**: ✅ 정상 작동(기존 `fetch_entity_hh24`가 사용 중). 응답에 `breakdowns:[{name:"00시~01시",...}]` 배열.
- **7일 초과 과거일자 요청 시**: `400 {"code":11004,"message":"상세 데이터는 최근 7일 이내 기간에서만 사용 가능합니다."}` — 정확한 경계 메시지로 재확인(기존 트랙 기록과 일치).
- **기기(PC/MO) breakdown 후보 전부 실패(무언 무시)**: `dvc`/`pcMobileType`/`device`/`dvcCtg`/
  `mobileYn`/`media`/`pcMobile` 전부 **HTTP 200이지만 `breakdowns` 키 자체가 없음**(top-level
  집계만 반환 — 조건이 안 맞으면 조용히 무시하는 기존 관측 패턴과 동일). **기기별 세부는
  `/stats`로는 불가 — 대용량 리포트(AD/AD_DETAIL 등)의 device 컬럼(M/P)으로만 확보 가능**(실측
  확인됨, §1.1/1.2).

### 2.3 다건 id 동시 조회

- 콤마 구분 다건 id(`id=kw1,kw2`) 요청 시 **HTTP 200이지만 응답이 kw1 단건 조회 결과와 동일**
  (kw2 반영 흔적 없음) — 기존 코드 주석("ids 복수는 빈 응답")과는 다른 관측(빈 응답이 아니라
  첫 id만 반영하는 것으로 보임). **결론은 동일: 다건 조회는 신뢰 불가, id 단수 반복 호출 유지가
  안전.**

### 2.4 기타

- adgroup 단위 `/stats`도 keyword/campaign과 동일하게 정상 동작(모든 유효 필드 사용 가능).
- `timeIncrement=1`(일별 breakdown, 캠페인 단위) 기존 백필 경로 정상 재확인.

## 3. 엔티티 API 부가 필드 실측

### 3.1 `/ncc/keywords` (키워드) — 저장 중인 것 vs 저장 안 하는 것

| 필드 | 저장(`naver_entity`) 여부 |
|---|---|
| `nccKeywordId`,`keyword`,`nccAdgroupId`,`nccCampaignId` | ✅ (entity_id/name/parent_id/campaign_id) |
| `bidAmt` | ✅ (`bid_amt`) |
| `status`,`userLock` | ✅ (병합해 `status` on/off/deleted로 정규화) |
| `nccQi.qiGrade`(품질지수 1~7) | ✅ (`qi_grade`, D-NAO-46②) |
| **`useGroupBidAmt`** | ❌ 미저장 — 키워드가 그룹 입찰가를 따르는지(true) 개별 입찰가를 쓰는지(false) 구분값. 쇼핑 소재는 `naver_adgroup_product.use_group_bid_amt`로 저장 중(B1)이나, **파워링크 키워드 자체는 이 필드를 안 씀** |
| `inspectStatus`(심사상태 APPROVED 등) | ❌ 미저장 |
| `statusReason` | ❌ 미저장(status만) |
| `delFlag` | ❌ 미저장(status로 흡수 추정) |
| `regTm`/`editTm` | ❌ 미저장 |
| `attr`(항상 `{}` 관측) | ❌ 미저장(내용 없어 무의미해 보임) |

### 3.2 `/ncc/adgroups` (그룹) — 저장 중인 것 vs 저장 안 하는 것

| 필드 | 저장 여부 |
|---|---|
| `nccAdgroupId`,`nccCampaignId`,`name`,`status`,`userLock`,`bidAmt` | ✅ |
| **`useExpSearch`**(확장검색 on/off, 관측 `true`) | ❌ 미저장 — §0의 EXPKEYWORD 버그와 맞물려, 그룹별로 확장검색이 켜져 있는지조차 우리 시스템엔 없음 |
| **`expSearchBudgetRatio`**(확장검색 예산비중, 관측 `100`) | ❌ 미저장 |
| **`aiAdsOptIn`**(AI 광고 참여 여부, 관측 `true`) | ❌ 미저장 |
| `contentsNetworkBidAmt`/`useCntsNetworkBidAmt`/`mobileNetworkBidWeight`/`pcNetworkBidWeight`/`contentsNetworkBidWeight` | ❌ 미저장(콘텐츠 매체 입찰 조정 — 파워링크가 콘텐츠 네트워크에도 별도 입찰가/가중치를 쓸 수 있음을 시사) |
| `adRollingType`(`PERFORMANCE` 관측) | ❌ 미저장 |
| `systemBiddingType`(`NONE` 관측) | ❌ 미저장(자동입찰 시스템 사용 여부로 추정) |
| `targetSummary`(`{pcMobile:"all", media:"partially"}`) | ❌ 미저장 |
| `dailyBudget`/`useDailyBudget`/`budgetLock`/`sharedBudgetLock` | ❌ 미저장(그룹별 예산 — 캠페인 레벨 `daily_budget`만 `naver_campaign_settings`류에서 다룸) |
| `mobileChannelId`/`pcChannelId`/`mobileChannelKey`/`pcChannelKey` | ❌ 미저장(스마트스토어 URL) |

### 3.3 `/ncc/ads` (소재) — 기존 코드가 상세히 다룸(B1)

- `adAttr`(bidAmt/useGroupBidAmt), `userLock`, `mallProductId` 등은 이미 파싱 로직 존재
  (`get_ads`, D-NAO-65 B1) — 다만 **파워링크(WEB_SITE) 소재는 `mallProductId`가 없어 이 함수의
  필터(`if not mall_pid: continue`)에 전량 걸려 스킵**됨(§0과 별개로, 원래 쇼핑 상품 소재
  전용 함수). 파워링크 소재의 `headline`/`description`/`final URL` 등 텍스트 필드는 어디서도
  수집하지 않음(광고 문구 자체는 우리 시스템 밖).
- `inspectStatus`(심사상태), `preNccAdId`(개정 이력) 등도 미수집.

### 3.4 `/ncc/ad-extensions` (확장소재) — 완전 미사용 엔드포인트

- `ids=` 또는 `ownerId=`(그룹ID) 파라미터로 조회 가능(`nccAdgroupId`는 400 — 파라미터명 다름).
- `type`(예: `SHOPPING_WEB`), `adExtension.view`(링크 URL), `schedule`(노출 스케줄), `inspectStatus`
  등을 제공하나 **우리 시스템 어디서도 호출하지 않음**(ADEXTENSION 리포트도 §1.5처럼 미사용).

## 4. 보존 한도 실측

| 리포트/API | 보존 한도(실측) | 근거 |
|---|---|---|
| AD, AD_CONVERSION | 롤링 ~365일(기존 확정) | 이번 세션 재검증 안 함(기존 트랙 기록 신뢰) |
| SHOPPINGKEYWORD_DETAIL | 16일(자동 BUILT, 기존 확정) | 이번 세션 재검증 안 함 |
| `/stats` hh24(시간대별) breakdown | **정확히 7일** | `400 code:11004 "상세 데이터는 최근 7일 이내 기간에서만 사용 가능합니다"` (재확인) |
| AD_DETAIL / ADEXTENSION / ADEXTENSION_CONVERSION / EXPKEYWORD / CRITERION / CRITERION_CONVERSION | **롤링 ~365일로 추정(AD와 동일 경계)** | -30일·-100일 생성 REGIST 성공, -400일 전부 실패(`code:10004 "선택하신 조건에 지표가 확인되지 않습니다"`). CRITERION 대표로 정밀 이분 테스트: **-350일 성공 / -370일 실패** → 경계가 350~370일 사이, AD/AD_CONVERSION의 기존 확정 365일과 정합 |
| AD_CONVERSION_DETAIL | 불명(추가 확인 필요) | -30일 성공했으나 **-100일에서도 실패** — 다른 6종과 다른 패턴. 오류 메시지가 "지표 없음"류(경계 특정 메시지 아님)라, 100일 전 그 날짜에 우연히 상세코드가 붙는 전환이 0건이었을 가능성과 실제 더 짧은 보존 한도 가능성을 구분 못함 — **미확정, 재확인 필요** |
| EXPKEYWORD 자동생성 여부 | **자동생성 안 됨**(기존 확정 재검증) | `list_report_jobs("EXPKEYWORD")`가 우리가 직접 POST한 것 외 과거 잡을 전혀 안 가지고 있음 — self-create 필수 |
| AD_DETAIL 등 7종의 자동생성 여부 | **자동생성 신뢰 불가 — 전부 self-create 필요** | 테스트 전 `list_report_jobs`가 7종 전부 0건(우리가 만든 것 외엔 없음). "Naver가 정기 생성해준다"는 가정에 의존하면 안 됨(2026-07-11 사고와 동일 원리) |

## 5. 우리 수집 현황 대조 — 수집 중 vs 미사용 갭

| 데이터 소스 | 현재 수집 상태 | 저장 테이블 |
|---|---|---|
| AD (imp/clk/cost/rank_sum, 키워드 grain) | ✅ 수집 중 | `naver_ad_daily` |
| AD_CONVERSION (직접/간접 전환) | ✅ 수집 중 | `naver_ad_daily` |
| SHOPPINGKEYWORD_DETAIL (쇼핑 검색어) | ✅ 수집 중 | `naver_search_term_daily`(source=shopping) |
| SHOPPINGKEYWORD_CONVERSION_DETAIL (쇼핑 검색어 전환) | ✅ 수집 중 | `naver_search_term_daily`(source=shopping, UPDATE 병합) |
| **EXPKEYWORD (파워링크 확장검색어)** | ❌ **코드는 있으나 파싱 버그로 0행 적재(§0)** | `naver_search_term_daily`(source=expkeyword) — **테이블 생성 이후 단 1행도 없음** |
| `/stats` hh24 breakdown (키워드/그룹 시간대별) | ✅ 수집 중(일 1회 D-1 스윕, 영구보존) | `naver_keyword_hourly` |
| `/ncc/keywords` qiGrade(품질지수) | ✅ 수집 중 | `naver_entity.qi_grade` |
| `/ncc/keywords` bidAmt | ✅ 수집 중 | `naver_entity.bid_amt` |
| keywordstool 월검색량/경쟁도 | ✅ 수집 중(저클릭 키워드만 선택적) | `naver_entity.monthly_volume/competition` |
| AD_DETAIL(idx7·8 상세코드) | ❌ 미수집 | — |
| AD_CONVERSION_DETAIL(상세코드) | ❌ 미수집 | — |
| ADEXTENSION / ADEXTENSION_CONVERSION(확장소재 성과·전환) | ❌ 미수집 | — |
| CRITERION / CRITERION_CONVERSION(성별·연령대 추정 브레이크다운) | ❌ 미수집 | — |
| `/stats` ctr/cpc/crto/viewCnt | ❌ 미수집(계산 가능하지만 API 자체 제공값 미사용) | — |
| `/stats` 기기(PC/MO) breakdown | 해당 API로는 불가 확인(§2.2) — 리포트 device 컬럼(M/P)은 롤업 합산 후 버려짐 | `naver_ad_daily`가 기기 분리 없이 합산 저장(P0 설계, P1 예정이었으나 미착수) |
| `/ncc/keywords` useGroupBidAmt(파워링크) | ❌ 미수집(쇼핑 소재만 B1로 수집 중) | — |
| `/ncc/adgroups` useExpSearch/expSearchBudgetRatio/aiAdsOptIn/콘텐츠매체 입찰 | ❌ 미수집 | — |
| `/ncc/ads`(파워링크 소재 헤드라인/설명/URL) | ❌ 미수집 | — |
| `/ncc/ad-extensions`(확장소재 엔티티: 유형·URL·스케줄) | ❌ 미수집(엔드포인트 자체 미호출) | — |

## 6. 파워링크 운영에 쓸 만한 미수집 데이터 시사점 (사실만, 전략 추천 없음)

- **EXPKEYWORD 버그(§0)는 사실관계로서, 파워링크의 "등록되지 않은 검색어" 발굴·제외 시스템이
  설계는 됐으나 실데이터가 한 번도 들어온 적이 없다는 것을 의미한다.** SS3/SS4 판단 로직이
  "shopping/expkeyword 무관 전건 대상"으로 짜여 있음에도, expkeyword 쪽은 입력이 항상 빈 배열이었다.
- **`useGroupBidAmt`가 파워링크 키워드에도 존재하지만(쇼핑 소재만 수집 중) 저장하지 않는다** —
  키워드 개별입찰이 그룹입찰을 오버라이드하는지 여부를 판별할 근거가 파워링크 쪽엔 없다.
- **AD_DETAIL/ADEXTENSION 등은 AD 리포트보다 더 세분화된 grain(idx7·8 상세코드)을 제공하지만
  그 코드의 의미가 미상이라 활용 여부를 판단할 근거 자체가 없다.**
- **`/stats`의 ctr/cpc/crto는 API가 직접 계산해 주므로, 소비 코드가 직접 나눗셈을 하는 대신 이
  값을 쓸 수 있는 여지가 있다**(단, 반올림 방식 대조 없이는 완전 대체 불가).
- **기기(PC/MO)별 세부 성과는 `/stats`로는 얻을 수 없고, 대용량 리포트의 device 컬럼을 쓸 때만
  가능**하다 — 현재는 그 컬럼조차 합산 후 버려진다.
- **CRITERION(성별·연령대로 추정)은 공식 문서로 확정되지 않았으나, 값 패턴상 인구통계 축이
  존재하는 것으로 보인다** — 활용하려면 코드 의미를 공식 문서나 네이버 고객센터로 먼저
  확정해야 한다(현재는 "미상" 상태로 데이터만 존재).

## 7. ★공식 스펙 대조 (2026-08-14 추가) — 「파워링크 검색어별 전환 불가」 확정

> 이 절은 §0·SS0(`docs/PLAN_naver-ad-searchterm-ss.md` §0.5)이 **실측만으로** 내렸던 결론
> (「`AD_CONVERSION_DETAIL`의 검색어 컬럼이 항상 `-`」)에 **1차 출처 근거**를 붙인다.
> 전역 CLAUDE.md §3: 규범형 지식은 공식 1차 출처를 직접 fetch해 대조한다.

**출처**: 네이버 공식 API 문서 저장소 `naver/searchad-apidoc`, `gh-pages` 브랜치,
`assets/json/ncc-report.json`(Report API swagger). 2026-08-14 직접 다운로드.
문서 사이트(`naver.github.io/searchad-apidoc`)는 Angular SPA라 HTML fetch로는 본문이 안 잡힌다 —
사이트가 읽는 원본 swagger 목록은 `app/config.js`의 `swaggerJson` 배열에 있다(9개 스펙).

### `reportTp` 전수 13종 — 성과·전환이 «쌍»인데 EXPKEYWORD만 고아다

`definitions.StatReportJob.properties.reportTp` / `ReportJobResponse.properties.reportTp` (동일):

| 성과 리포트 | 전환 짝 |
|---|---|
| `AD` | `AD_CONVERSION` |
| `AD_DETAIL` | `AD_CONVERSION_DETAIL` |
| `ADEXTENSION` | `ADEXTENSION_CONVERSION` |
| `SHOPPINGKEYWORD_DETAIL` | `SHOPPINGKEYWORD_CONVERSION_DETAIL` |
| `SHOPPINGBRANDPRODUCT` | `SHOPPINGBRANDPRODUCT_CONVERSION` |
| `CRITERION` | `CRITERION_CONVERSION` |
| **`EXPKEYWORD`** (파워링크 확장검색어) | **없음** |

**6쌍 + 고아 1 = 13종.** 짝이 없는 유일한 리포트가 `EXPKEYWORD`다. 쇼핑 검색어에는
`SHOPPINGKEYWORD_CONVERSION_DETAIL`이 있으나 파워링크 확장검색어에는 대응물이 **아예 정의되어
있지 않다.** 즉 §0·SS0의 실측 결론은 옳고, 리포트 목록 자체가 그것을 확인한다.

### 실시간 `/stats` API에도 우회로가 없다

- `id` 파라미터 설명 원문: `"Entity Id (campaign id, Ad group id, Ad keyword id, Ad id, Criterion id)"`
  — **검색어가 목록에 없다.** 전환 필드(`convAmt`·`purchaseConvAmt`·`ror`·`purchaseRor`)는 존재하나
  전부 이 엔티티 ID 단위다.
- `breakdown` enum: `pcMblTp`(기기)·`dayw`(요일)·`hh24`(시간)·`regnNo`(지역) — **검색어 축 없음.**

→ 대용량 리포트·실시간 조회 **두 경로 모두** 확장검색 검색어별 전환을 제공하지 않는다.

### 스펙이 말하지 않는 것 (추정 금지 — 여기서 멈춘다)

- **「왜 없는지」는 스펙 어디에도 없다.** `reportTp` 설명은 `"Type of ad performance Report"`가 전부다.
- 쇼핑 검색어도 ID 없는 텍스트인데 거기엔 전환 리포트가 있다 — 따라서 「ID가 없어서 기술적으로
  불가」는 **스펙으로 뒷받침되지 않는다.** 제품 결정으로 보이나 근거가 없으므로 단정하지 않는다.
  향후 네이버가 추가할 가능성도 배제할 수 없다(이 절의 재확인 시점은 스펙 갱신 시).
- §6의 `CRITERION` 의미(성별·연령대 추정)는 **여전히 미확정** — 이 스펙에도 컬럼 설명이 없다.

### 실무 함의 (2026-08-14 라이브 실측 병기)

파워링크 확장검색 버킷은 30일 비용 6,259,486원 / 전환 20,014,620원 = **ROAS 3.20**으로 계정에서
가장 수익성 높은 구간이다(파워링크 전체 2.72, BEP 1.711). 검색어별 전환을 모르는 것이 현재
손해로 이어지고 있지 않다. 이 구간이 악화되면 전환 대신 **클릭당 비용 이상치·버킷 평균 대비
이탈**로 판정하는 축을 별도 설계해야 한다(현재 우선순위 낮음).

## 부록 — 조사에 사용한 스크립트/명령 요약

모든 스크립트는 `_investigate_step{1..10}.py`로 prod `backend/`에 임시 배치 후 실행·즉시 삭제.
로컬 사본: 본 세션 스크래치패드(`step1_campaign_entities.py` ~ `step10_adextension_entity.py`).
호출 API: `/ncc/campaigns`, `/ncc/adgroups`, `/ncc/keywords`, `/ncc/ads`, `/ncc/ad-extensions`,
`/stat-reports`(GET/POST), `/report-download`, `/stats`. 쓰기 API(`/ncc/*` POST/PUT/DELETE)는
호출하지 않음.
