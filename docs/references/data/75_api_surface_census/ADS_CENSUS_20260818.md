# 네이버 검색광고(SA) API 표면 전수 census × 성과등급 매트릭스 대조 (2026-08-18 KST)

> **목적**: `docs/references/data/73_band_x_all_api/BAND_X_ALL_API_MATRIX_20260818.md`(개정 3)의 광고(SA) 24축은 ref 63·64·65·58·57·66에 **이미 있던 값을 옮겨 조립**한 것이지 API 표면에서 연역한 것이 아니다(그 문서 자신이 "이 문서는 새 분석이 아니다"라고 밝힘). 이 문서는 **API 표면 쪽에서 출발**해 24축이 그걸 얼마나 덮는지 대조한다. 읽기 전용 — 네이버 API 실호출 0건, prod 접속 0건, git 커밋 0건.

---

## 1. 조사 방법·커버리지 자백 (이 절 없으면 산출물 무효)

### 무엇을 열었나
- **`https://naver.github.io/searchad-apidoc/`는 AngularJS SPA라 WebFetch(HTML→markdown 변환)로는 빈 셸(`searchad-apidoc`이라는 제목 한 줄)만 나온다** — 실측 확인(2회 시도, 루트와 `#/API Reference` 해시 둘 다 빈 결과). 정적 HTML fetch로는 이 사이트를 못 연다는 것 자체가 하나의 발견이다.
- 그래서 **1차 출처를 이 사이트가 실제로 로드하는 원본 데이터로 내려갔다**: `app/config.js`(gh-pages 브랜치)에 이 문서 사이트가 렌더링에 쓰는 **9개 공식 Swagger(OpenAPI 2.0) JSON 경로**가 명시돼 있다. 전부 `curl`로 200 OK·전문 취득(파일 크기 3.8KB~287KB, 총 9개):
  `ncc-heroes-ncc.json`(핵심 NCC API) · `ncc-heroes-tool.json`(외부연동 도구) · `ncc-heroes-billing.json`(빌링) · `atower.json`(계정 구조) · `ncc-report.json`(StatReport·Stat) · `master-report.json`(MasterReport) · `ncc-keywordstool.json`(연관검색어) · `estimate.json`(입찰 추정) · `ncc-inspect-history.json`(검수 이력).
  **이 JSON 자체가 네이버가 문서 사이트를 렌더링하는 데 쓰는 원본이므로 1차 출처다** — 스크린샷·요약이 아니라 사이트가 신뢰하는 것과 동일한 파일이다.
  원본 URL: `https://naver.github.io/searchad-apidoc/assets/json/<파일명>` — 로컬 사본은 스크래치패드(`swagger/*.json`, 이 세션 한정, repo 밖).
- **StatReport `reportTp`(13종) · MasterReport `item`(29종) · `/stats` `fields`(19종) · `/ncc/criterion` `type`(7종) · `/ncc/targets` `targetTp`(12종) · `/stats` `breakdown`(4종)는 전부 이 Swagger 정의(`definitions`/`parameters`의 `enum`)에서 그대로 뽑았다** — 추정·기억 재구성 0건.

### 무엇을 못 열었나 (미확인으로 명시)
- **"Guides" 절(각 리포트 파일의 컬럼 스키마 설명 prose, 예: SHOPPINGKEYWORD_DETAIL의 col1~col9가 각각 무엇인지)는 못 열었다.** Swagger는 리포트가 "무엇을 반환하는가"(reportTp enum, job 상태)만 정의하고 **다운로드되는 TSV 파일의 컬럼 스키마 자체는 정의하지 않는다**(파일 다운로드 API라 응답 스키마가 없다). `config.js`의 `menus: ['guides','reference','samples',...]`에서 `reference`만 위 Swagger로 매핑되고, `guides`·`samples`의 실제 URL을 gh-pages 저장소 트리 전체(재귀 검색)에서 못 찾았다 — `app.js`(74KB, Angular 번들)에도 가이드 프로즈 문자열이 없다(`grep -c guides app.js` = 1건, 콘텐츠 아님). **즉 이 두 메뉴의 콘텐츠는 정적 파일로 이 저장소에 없고, 별도의 동적 소스(네이버 내부)에서 온다고 추정되나 그 접근 경로를 못 찾았다 — 확인 안 됨.**
- 결론: **리포트 컬럼 스키마(예: SHOPPINGKEYWORD_DETAIL col7/8/9=시간대/지역/매체)는 공식 "Guides" 문서로 재확인하지 못했다.** 이 값은 매트릭스·코드 주석(`naver_sa_ad_fetcher.py:1111`)이 실제 다운로드 파일을 뜯어본 **실측 근거**이지, 이번 조사가 공식 문서로 검증한 것이 아니다 — 이 문서에서는 "코드 실측 근거, 공식 가이드 미대조"로 표시한다.
- **"전수 조사 완료"라고 말할 수 있는 범위는 Swagger 9개 파일의 구조적 표면(endpoint·enum·definition)뿐이다.** 사이트의 서술형 가이드 절, 그리고 스웨거에 안 실린 부가 규칙(예: 요청 한도, 필드별 조합 제약의 자연어 설명)은 이번 조사 범위 밖 — 미확인.
- **네이버 커머스(스마트스토어) API는 이번 P1에서 재조사하지 않았다.** 지시 원문 P1이 "네이버 검색광고 API 표면"으로 한정했고, 커머스 12축(1-B)은 매트릭스 자신이 이미 ref 71(별도 라이브 프로브, 116 endpoint 계수)로 커버했다고 밝혀 이 census와 중복 조사가 아니다. P2 대조도 광고 24축(1-A)만 대상으로 한다.
- **swagger가 문서 UI에 "숨기는" 태그가 있다**(`config.js` `tags.exclude`: accesscontrol, apiapikey, apicustomerauth, apihistory, apikeys, apilicenses, customer, customerauth, customerlinks, history, managercandidates, nccmanagedkeyword 등). 이 목록에 해당하는 controller들은 **UI에는 안 보이지만 swagger JSON 자체에는 있을 수도 없을 수도 있다** — 이번 census는 JSON `paths`에 실제로 있는 것만 셌으므로 이 제외선과 무관하게 완전하다(제외는 "문서 UI 표시 여부"이지 "JSON에 존재 여부"가 아니다). 다만 JSON에도 없는 완전 비공개 내부 API의 존재 가능성은 원리적으로 배제 못 한다 — 미확인.

---

## 2. P1 — 공식 Swagger 9종 전수 목록

### 2-1. Endpoint (canonical method+path 기준)

| 항목 | 값 |
|---|---|
| Swagger 파일 수 | 9 |
| **raw 문서 행수**(파라미터 조합별로 별도 행인 것 포함, 원문 그대로 셈) | 126 |
| **canonical endpoint**(method+base path, `{?query}` 접미 제거 후 중복 제거) | **110** |

리소스 태그별 분포(29개 태그, canonical 기준):

| 태그 | 파일 | 개수 | 태그 | 파일 | 개수 |
|---|---|---|---|---|---|
| Ad | ncc-heroes-ncc | 6 | Label | ncc-heroes-ncc | 2 |
| AdAccounts | atower | 2 | LabelRef | ncc-heroes-ncc | 1 |
| AdExtension | ncc-heroes-ncc | 6 | ManagedKeyword | ncc-heroes-ncc | 1 |
| AdKeyword | ncc-heroes-ncc | 7 | ManagerAccounts | atower | 2 |
| Adgroup | ncc-heroes-ncc | 9 | MasterReport | master-report | 5 |
| AnalyticsController | ncc-heroes-tool | 3 | ProductGroup | ncc-heroes-ncc | 1 |
| Bizmoney | ncc-heroes-billing | 4 | RelKwdStat | ncc-keywordstool | 1 |
| BrandNewContract | ncc-heroes-ncc | 1 | SharedBudget | ncc-heroes-ncc | 8 |
| BusinessChannel | ncc-heroes-ncc | 8 | Stat | ncc-report | 1 |
| Campaign | ncc-heroes-ncc | 7 | StatReport | ncc-report | 5 |
| Criterion | ncc-heroes-ncc | 4 | Target | ncc-heroes-ncc | 2 |
| Estimate | estimate | 10 | TimeContract | ncc-heroes-ncc | 1 |
| InspectHistory | ncc-inspect-history | 2 | 서류관리 | ncc-heroes-tool | 3 |
| IpExclusion | ncc-heroes-tool | 7 | 세금계산서위임조회 | ncc-heroes-tool | 1 |

전체 목록(110행, 사용 여부는 §4 P3와 통합해 O 표시)은 §3-2 표에 태그순으로 있다.

### 2-2. 리포트 종류 전수

**StatReport `reportTp`(13종)** — `POST /api/stat-reports`로 생성, `GET /api/stat-reports`로 조회, `downloadUrl`로 TSV 취득:
`AD` · `AD_DETAIL` · `AD_CONVERSION` · `AD_CONVERSION_DETAIL` · `ADEXTENSION` · `ADEXTENSION_CONVERSION` · `EXPKEYWORD` · `SHOPPINGKEYWORD_DETAIL` · `SHOPPINGKEYWORD_CONVERSION_DETAIL` · `SHOPPINGBRANDPRODUCT` · `SHOPPINGBRANDPRODUCT_CONVERSION` · `CRITERION` · `CRITERION_CONVERSION`

**MasterReport `item`(29종)** — `POST /master-reports`로 생성(엔티티 스냅샷·`fromTime` 이후 변경분 delta):
`Campaign` · `CampaignBudget` · `BusinessChannel` · `Adgroup` · `AdgroupBudget` · `Keyword` · `Ad` · `AdExtension` · `Qi` · `Label` · `LabelRef` · `Media` · `Biz` · `SeasonalEvent` · `ShoppingProduct` · `ContentsAd` · `PlaceAd` · `CatalogAd` · `AdQi` · `ProductGroup` · `ProductGroupRel` · `BrandAd` · `BrandThumbnailAd` · `BrandBannerAd` · `Criterion` · `SharedBudget` · `Asset` · `AdAssetLink` · `RsaAd` · `HospitalAd`

**리포트 종류 합계 = 13 + 29 = 42종.**

### 2-3. `/stats` fields 전수 — 19종 (요청 가능 지표)

`impCnt` · `clkCnt` · `salesAmt` · `ctr` · `cpc` · `avgRnk` · `ccnt` · `recentAvgRnk` · `recentAvgCpc` · `pcNxAvgRnk` · `mblNxAvgRnk` · `crto` · `convAmt` · `ror` · `cpConv` · `viewCnt` · `purchaseCcnt` · `purchaseConvAmt` · `purchaseRor`

`breakdown`(단일값만 지원) 4종: `pcMblTp`(PC/모바일) · `dayw`(요일) · `hh24`(시간대) · `regnNo`(지역코드).
`datePreset` 7종: `today`·`yesterday`·`last7days`·`last30days`·`lastweek`·`lastmonth`·`lastquarter`. `timeIncrement`: `1`(일별) / `allDays`(합계).

### 2-4. 타겟팅 축 전수

**`/ncc/criterion/{ownerId}` `type`(7종)** — 광고그룹 단위 타겟팅 딕셔너리 코드:
`SD`(요일/시간, Schedule) · `AG`(연령, Age) · `GN`(성별, Gender) · `AD`(관심사) · `RL` · `RP` · `DV`(디바이스). ※RL·RP는 swagger에 코드값만 있고 설명 문자열이 없다 — 의미 미확인.

**`/ncc/targets` `targetTp`(12종)** — 광고그룹/캠페인 단위 타겟팅 실체(제외·가중치 포함):
`TIME_WEEKLY_TARGET` · `REGIONAL_TARGET` · `MEDIA_TARGET` · `PC_MOBILE_TARGET` · `RESTRICT_KEYWORD_TARGET` · `NON_SEARCH_KEYWORD_TARGET` · `GENDER_TARGET` · `AGE_TARGET` · `PERIOD_TARGET` · `AD_TAG` · `GENDER_WEIGHT_TARGET` · `PLACE_ADGROUP_TAG`.

**`PUT /ncc/criterion/{ownerId}/bidWeight`** — `bidWeight`(50~500) 설정 전용 별도 엔드포인트. GET으로 현재값을 읽는 경로는 `/ncc/criterion/{ownerId}`(type별 코드-가중치 쌍)로 추정되나, 매트릭스가 인용한 "1,271행 bidWeight" 실측은 코드에 이 문자열이 전혀 없어(§3 확인) **`/ncc/targets` 응답 안의 target JSON blob에서 나왔을 가능성이 있으나 어느 필드인지 이번 조사로 특정 못함 — 미확인**(스캐폴드 프로브가 휘발성 scratchpad에만 있어 재현 불가, 매트릭스 §자체 기술과 일치).

**`GET /keywordstool` 응답 필드(`RelKwdStat`, 9종)**: `relKeyword` · `monthlyPcQcCnt` · `monthlyMobileQcCnt` · `monthlyAvePcClkCnt` · `monthlyAveMobileClkCnt` · `monthlyAvePcCtr` · `monthlyAveMobileCtr` · `plAvgDepth` · `compIdx`.

---

## 3. P2 — 매트릭스 24축 ↔ API 표면 대조 (양방향)

### 3-1. 정방향 — 축 #1~#24가 어느 API 표면에서 오는가

grain 판정 기준: 성과등급은 **광고그룹(adgroup) 단위**다(`band_group_total.csv`, `adgroup_id` 기준, ref 63 §1-5). "닿는다"는 그 API 응답이 adgroup_id(또는 그 하위 keyword/ad_id를 경유해 adgroup_id로 복원 가능)를 키로 갖는 경우로 판정한다.

| # | 축 | API 표면 출처 | grain | 비고 |
|---|---|---|---|---|
| 1 | 캠페인 유형 | `GET /ncc/campaigns`(`campaignTp`) | 캠페인 | 등급표 자체 층화 원천 |
| 2 | S1 순위(avg_rank) | `GET /stats` field=`avgRnk` | 요청 entity(adgroup 가능) | 닿음 |
| 3 | S2 시간대·지역·매체(검색어) | StatReport `SHOPPINGKEYWORD_DETAIL`(col7/8/9, **가이드 미대조**) | 검색어(→adgroup 복원 가능) | 리포트 컬럼 스키마는 §1에서 미확인 처리 |
| 4 | S3 연령·성별 | `GET /ncc/criterion/{ownerId}` type=AG,GN **또는** StatReport `CRITERION`/`CRITERION_CONVERSION` | adgroup(ownerId) | 두 경로 존재 — 매트릭스는 전자만 언급, 후자(리포트 경로)는 §3-3 참조 |
| 5 | S4 제외 여부·시점 | WEB_SITE: `GET/POST/DELETE /ncc/adgroups/{id}/restricted-keywords` · SHOPPING: `GET/PUT /ncc/targets`(targetTp=RESTRICT_KEYWORD_TARGET) | adgroup | 코드 대조로 확정(§4) |
| 6 | S5 상품별 BEP | 파생(축#7의 비용·전환액 위에서 계산) | — | API 표면 자체가 아니라 계산값 |
| 7 | S6 절대액(비용·이익) | `GET /stats` 또는 StatReport `AD`/`AD_DETAIL`(salesAmt/convAmt/ccnt) | adgroup/campaign | 닿음 |
| 8 | 달력 요인 | 파생(축#7의 시계열을 요일·공휴일 캘린더와 대조) | — | API 표면 아님 |
| 9 | A1 관심사 | `GET /ncc/criterion/{ownerId}` type=AD **또는** StatReport `CRITERION` | adgroup | 닿음(호출만 안 함) |
| 10 | A2 요일·시간(CRITERION SD) | `GET /ncc/criterion/{ownerId}` type=SD | adgroup | 닿음(호출만 안 함) — `/stats breakdown=dayw`는 **별개 경로**(§3-3) |
| 11 | A3 검색어 텍스트 속성 | StatReport `EXPKEYWORD`/`SHOPPINGKEYWORD_DETAIL` 원장의 키워드 문자열 | 검색어(→adgroup) | 닿음 |
| 12 | A4 타겟팅 실설정(bidWeight) | `PUT .../bidWeight`(쓰기) / GET 경로 미상(§2-4) | adgroup | 원료 자체 grain은 닿으나 정확한 GET 표면 미확정 |
| 13 | A5 매체 블랙리스트 | `GET /ncc/targets` targetTp=MEDIA_TARGET | adgroup | ★같은 GET 콜에 이미 실려 옴, 코드가 버림(§4) |
| 14 | A6 PC/모바일 가중치 | `GET /ncc/targets` targetTp=PC_MOBILE_TARGET | adgroup | ★같은 GET 콜에 이미 실려 옴, 코드가 버림(§4). `/stats breakdown=pcMblTp`(실적 자체의 기기별 분해)는 **별개 경로**(§3-3) |
| 15 | A7 소재 개별입찰·잠금 | `GET /ncc/ads`(`bidAmt`/`userLock`) · `GET /ncc/keywords`(`bidAmt`) | ad/keyword(→adgroup) | 닿음, 이미 호출 중 |
| 16 | A8 확장검색·자동입찰 변이 | `GET /ncc/adgroups`(`useExpSearch`·`expSearchBudgetRatio` 등 Adgroup 정의 필드) | adgroup | 닿음, 이미 호출 중(전수 스윕만 안 함) |
| 17 | A9 검색량·경쟁도 | `GET /keywordstool` | 키워드(계정 무관 사전) | grain이 adgroup에 원리적으로 안 닿음(사전 조회이지 계정 실적 아님) |
| 18 | A10 계절성 | 파생(축#7 시계열의 월·분기 집계) | — | API 표면 아님 |
| 19 | B1 확장소재 성과 | `GET/POST/PUT/DELETE /ncc/ad-extensions` + StatReport `ADEXTENSION`/`ADEXTENSION_CONVERSION` | adgroup(ownerId) | ⚠️매트릭스는 "주요 유형 API 생성 불가"라 적었으나 CRUD 엔드포인트 자체는 문서상 **존재**(§3-3 불일치 기록) |
| 20 | B2 예산·daily_budget | `GET/PUT /ncc/campaigns`·`/ncc/adgroups`(`dailyBudget`) | 캠페인/adgroup | 닿음, 이미 호출 중 |
| 21 | B3 시장가 사다리 | `POST /estimate/*`(average-position-bid, performance-bulk 등 10종) | 키워드 | 그룹 아님, 키워드 레벨 |
| 22 | B4 입찰 변경 이력 | 자체 관측(우리 쓰기 로그 `naver_change_log`) + `POST /ncc/inspect-history`(검수 이력, 다른 개념) | — | 네이버가 "변경 이력"을 직접 주는 공식 API 없음(`MasterReport`의 `fromTime` delta가 가장 가까운 근사, §3-3) |
| 23 | ADVoost·GFA | 없음(SA 계열 밖) | — | 원리적 암흑, 확정 |
| 24 | 시간대(hh24) × 등급 | `GET /stats` breakdown=`hh24` | 요청 entity(adgroup) | 닿음, 이미 호출 중 |

### 3-2. 전체 canonical endpoint 110개 원장 (태그순, `O`=코드에서 실사용 확인)

| 태그 | 파일 | 메서드 | 경로 | 사용 |
|---|---|---|---|---|
| Ad | ncc-heroes-ncc | DELETE | /api/ncc/ads/{adId} | |
| Ad | ncc-heroes-ncc | GET | /api/ncc/ads | O |
| Ad | ncc-heroes-ncc | GET | /api/ncc/ads/{adId} | |
| Ad | ncc-heroes-ncc | POST | /api/ncc/ads | |
| Ad | ncc-heroes-ncc | PUT | /api/ncc/ads | |
| Ad | ncc-heroes-ncc | PUT | /api/ncc/ads/{adId} | O |
| AdAccounts | atower | GET | /api/ad-accounts | |
| AdAccounts | atower | GET | /api/ad-accounts/{adAccountNo}/members | |
| AdExtension | ncc-heroes-ncc | DELETE | /api/ncc/ad-extensions/{adExtensionId} | |
| AdExtension | ncc-heroes-ncc | GET | /api/ncc/ad-extensions | |
| AdExtension | ncc-heroes-ncc | GET | /api/ncc/ad-extensions/{adExtensionId} | |
| AdExtension | ncc-heroes-ncc | POST | /api/ncc/ad-extensions | |
| AdExtension | ncc-heroes-ncc | PUT | /api/ncc/ad-extensions | |
| AdExtension | ncc-heroes-ncc | PUT | /api/ncc/ad-extensions/{adExtensionId} | |
| AdKeyword | ncc-heroes-ncc | DELETE | /api/ncc/keywords | |
| AdKeyword | ncc-heroes-ncc | DELETE | /api/ncc/keywords/{nccKeywordId} | |
| AdKeyword | ncc-heroes-ncc | GET | /api/ncc/keywords | O |
| AdKeyword | ncc-heroes-ncc | GET | /api/ncc/keywords/{nccKeywordId} | |
| AdKeyword | ncc-heroes-ncc | POST | /api/ncc/keywords | |
| AdKeyword | ncc-heroes-ncc | PUT | /api/ncc/keywords | |
| AdKeyword | ncc-heroes-ncc | PUT | /api/ncc/keywords/{nccKeywordId} | O |
| Adgroup | ncc-heroes-ncc | DELETE | /api/ncc/adgroups/{adgroupId} | |
| Adgroup | ncc-heroes-ncc | DELETE | /api/ncc/adgroups/{adgroupId}/restricted-keywords | O |
| Adgroup | ncc-heroes-ncc | GET | /api/ncc/adgroups | O |
| Adgroup | ncc-heroes-ncc | GET | /api/ncc/adgroups/shared-budgets/{sharedBudgetId} | |
| Adgroup | ncc-heroes-ncc | GET | /api/ncc/adgroups/{adgroupId} | |
| Adgroup | ncc-heroes-ncc | GET | /api/ncc/adgroups/{adgroupId}/restricted-keywords | |
| Adgroup | ncc-heroes-ncc | POST | /api/ncc/adgroups | |
| Adgroup | ncc-heroes-ncc | POST | /api/ncc/adgroups/{adgroupId}/restricted-keywords | O |
| Adgroup | ncc-heroes-ncc | PUT | /api/ncc/adgroups/{adgroupId} | O |
| AnalyticsController | ncc-heroes-tool | GET | /api/tool/analyticses | |
| AnalyticsController | ncc-heroes-tool | POST | /api/tool/analyticses | |
| AnalyticsController | ncc-heroes-tool | PUT | /api/tool/analyticses | |
| Bizmoney | ncc-heroes-billing | GET | /api/billing/bizmoney | |
| Bizmoney | ncc-heroes-billing | GET | /api/billing/bizmoney/histories/charge | |
| Bizmoney | ncc-heroes-billing | GET | /api/billing/bizmoney/histories/exhaust | O |
| Bizmoney | ncc-heroes-billing | GET | /api/billing/bizmoney/histories/period | |
| BrandNewContract | ncc-heroes-ncc | GET | /api/ncc/brand-new/contracts | |
| BusinessChannel | ncc-heroes-ncc | DELETE | /api/ncc/channels | |
| BusinessChannel | ncc-heroes-ncc | DELETE | /api/ncc/channels/{businessChannelId} | |
| BusinessChannel | ncc-heroes-ncc | GET | /api/ncc/channels | |
| BusinessChannel | ncc-heroes-ncc | GET | /api/ncc/channels/{businessChannelId} | |
| BusinessChannel | ncc-heroes-ncc | GET | /api/ncc/purchasable-place-channels | |
| BusinessChannel | ncc-heroes-ncc | POST | /api/ncc/channels | |
| BusinessChannel | ncc-heroes-ncc | PUT | /api/ncc/channels/{businessChannelId} | |
| BusinessChannel | ncc-heroes-ncc | PUT | /api/ncc/channels/{businessChannelId}/inspect | |
| Campaign | ncc-heroes-ncc | DELETE | /api/ncc/campaigns | |
| Campaign | ncc-heroes-ncc | DELETE | /api/ncc/campaigns/{campaignId} | |
| Campaign | ncc-heroes-ncc | GET | /api/ncc/campaigns | O |
| Campaign | ncc-heroes-ncc | GET | /api/ncc/campaigns/shared-budgets/{sharedBudgetId} | |
| Campaign | ncc-heroes-ncc | GET | /api/ncc/campaigns/{campaignId} | |
| Campaign | ncc-heroes-ncc | POST | /api/ncc/campaigns | |
| Campaign | ncc-heroes-ncc | PUT | /api/ncc/campaigns/{campaignId} | O |
| Criterion | ncc-heroes-ncc | GET | /api/ncc/criterion-dictionary/{type} | |
| Criterion | ncc-heroes-ncc | GET | /api/ncc/criterion/{ownerId} | |
| Criterion | ncc-heroes-ncc | PUT | /api/ncc/criterion/{ownerId}/bidWeight | |
| Criterion | ncc-heroes-ncc | PUT | /api/ncc/criterion/{ownerId}/{type} | |
| Estimate | estimate | POST | /estimate/average-position-bid/{type} | O |
| Estimate | estimate | POST | /estimate/exposure-minimum-bid/{type} | |
| Estimate | estimate | POST | /estimate/median-bid/{type} | |
| Estimate | estimate | POST | /estimate/performance-bulk | O |
| Estimate | estimate | POST | /estimate/performance/{type} | |
| Estimate | estimate | POST | /npc-estimate/average-position-bid/{type} | |
| Estimate | estimate | POST | /npc-estimate/exposure-minimum-bid/{type} | |
| Estimate | estimate | POST | /npc-estimate/performance | |
| Estimate | estimate | POST | /npla-estimate/average-position-bid/{type} | |
| Estimate | estimate | POST | /npla-estimate/exposure-minimum-bid/{type} | |
| InspectHistory | ncc-inspect-history | GET | /api/ncc/inspect-history/{id} | |
| InspectHistory | ncc-inspect-history | POST | /api/ncc/inspect-history | |
| IpExclusion | ncc-heroes-tool | DELETE | /api/tool/ip-exclusions | |
| IpExclusion | ncc-heroes-tool | DELETE | /api/tool/ip-exclusions/{id} | |
| IpExclusion | ncc-heroes-tool | GET | /api/tool/client-ip | |
| IpExclusion | ncc-heroes-tool | GET | /api/tool/ip-exclusion-histories | |
| IpExclusion | ncc-heroes-tool | GET | /api/tool/ip-exclusions | |
| IpExclusion | ncc-heroes-tool | POST | /api/tool/ip-exclusions | |
| IpExclusion | ncc-heroes-tool | PUT | /api/tool/ip-exclusions | |
| Label | ncc-heroes-ncc | GET | /api/ncc/labels | |
| Label | ncc-heroes-ncc | PUT | /api/ncc/labels | |
| LabelRef | ncc-heroes-ncc | PUT | /api/ncc/label-refs | |
| ManagedKeyword | ncc-heroes-ncc | GET | /api/ncc/managedKeyword | |
| ManagerAccounts | atower | GET | /api/manager-accounts | |
| ManagerAccounts | atower | GET | /api/manager-accounts/{managerAccountNo}/child-ad-accounts | |
| MasterReport | master-report | DELETE | /master-reports | |
| MasterReport | master-report | DELETE | /master-reports/{id} | |
| MasterReport | master-report | GET | /master-reports | |
| MasterReport | master-report | GET | /master-reports/{id} | |
| MasterReport | master-report | POST | /master-reports | |
| ProductGroup | ncc-heroes-ncc | GET | /api/ncc/product-groups | |
| RelKwdStat | ncc-keywordstool | GET | /keywordstool | O |
| SharedBudget | ncc-heroes-ncc | DELETE | /api/ncc/shared-budgets | |
| SharedBudget | ncc-heroes-ncc | GET | /api/ncc/shared-budgets | |
| SharedBudget | ncc-heroes-ncc | GET | /api/ncc/shared-budgets/{sharedBudgetId} | |
| SharedBudget | ncc-heroes-ncc | POST | /api/ncc/shared-budgets | |
| SharedBudget | ncc-heroes-ncc | PUT | /api/ncc/shared-budgets | |
| SharedBudget | ncc-heroes-ncc | PUT | /api/ncc/shared-budgets/adgroups | |
| SharedBudget | ncc-heroes-ncc | PUT | /api/ncc/shared-budgets/campaigns | |
| SharedBudget | ncc-heroes-ncc | PUT | /api/ncc/shared-budgets/{sharedBudgetId} | |
| Stat | ncc-report | GET | /api/stats | O |
| StatReport | ncc-report | DELETE | /api/stat-reports | |
| StatReport | ncc-report | DELETE | /api/stat-reports/{reportJobId} | |
| StatReport | ncc-report | GET | /api/stat-reports | O |
| StatReport | ncc-report | GET | /api/stat-reports/{reportJobId} | |
| StatReport | ncc-report | POST | /api/stat-reports | O |
| Target | ncc-heroes-ncc | GET | /api/ncc/targets | O |
| Target | ncc-heroes-ncc | PUT | /api/ncc/targets/{targetId} | O |
| TimeContract | ncc-heroes-ncc | GET | /api/ncc/time-contracts | |
| 서류관리 | ncc-heroes-tool | GET | /api/tool/document | |
| 서류관리 | ncc-heroes-tool | POST | /api/tool/document | |
| 서류관리 | ncc-heroes-tool | POST | /api/tool/file/upload | |
| 세금계산서위임조회 | ncc-heroes-tool | GET | /api/tool/bill-recipient-histories/latest | |

**합계: 110 canonical, 사용 19, 미사용 91.**

### 3-3. 역방향 — 표면 → 축 (핵심: 어디에도 안 들어간 것)

**미배정 91개 endpoint를 태그(리소스) 단위로 묶어 처분 후보를 매긴다** (①등급교차 가능 ②grain 불닿음 ③쓰기전용 ④정적/설정 ⑤이미 다른 축에 포함):

| 리소스(태그) | 미배정 endpoint 수 | 처분 후보 | 근거 |
|---|---|---|---|
| **Criterion**(`/ncc/criterion`) | 4(GET 1·PUT 3) | **①등급교차 가능** | GET은 adgroup(ownerId) grain, type=AG/GN/AD/SD 전부. 축 #4·#9·#10의 **직접 원료**인데 코드가 호출 안 함(§4). PUT 3개는 ③쓰기전용 |
| **MasterReport**(`/master-reports`) | 5 | **①등급교차 가능(강함, 미상 포함)** | `item=Qi`(품질지수)·`Criterion`·`Adgroup`·`AdgroupBudget`·`Keyword`는 adgroup grain 스냅샷/delta. 특히 `Qi`는 memory의 "91,172개 전부 `qi_grade=4`" 죽은신호(§5-1 별도 절)의 **대안 취득 경로 후보** — 현재 qi_grade는 `/ncc/keywords`의 `nccQi.qiGrade`(§4)에서만 오는데, MasterReport의 독립 경로로 같은 값을 재조회해 대조한 적이 없다(미상) |
| **AdExtension**(`/ncc/ad-extensions`) | 6(GET 3·POST 1·PUT 2) + StatReport `ADEXTENSION`/`ADEXTENSION_CONVERSION` | **①등급교차 가능** — 단 매트릭스와 상충 | 매트릭스 B1은 "주요 유형 API 생성 불가"라 적었으나, 문서상 CRUD 엔드포인트와 리포트 타입 자체는 **존재한다**. 이 불일치는 §5-2에 별도 기록(어느 쪽이 맞는지는 이번 조사로 확정 못함 — 실호출 금지라 재현 불가) |
| **SharedBudget**(`/ncc/shared-budgets`) | 8 | **①등급교차 가능(정밀화)** | 축 #20(B2 예산)이 `entity_snapshot.daily_budget`(27일 창)로 대신하고 있는데, 이 엔드포인트군이 공유예산 그룹 구조 자체(멤버 캠페인/adgroup 목록)를 직접 준다 — 원료 정밀화 후보 |
| **ProductGroup**(`/ncc/product-groups`) | 1 | ①등급교차 가능(약함) | 상품군 단위, adgroup 경유 가능성 — grain 확정 못함(미상) |
| **InspectHistory**(`/ncc/inspect-history`) | 2 | ①등급교차 가능(약함)/④ | 심사 이력(소재·확장소재 검수) — 성과와 직접 연관 미상 |
| **AdAccounts·ManagerAccounts**(atower) | 4 | **②grain 불닿음** | 계정/서브계정 구조 — 이 계정 하나만 쓰는 구조에선 층 자체가 없음 |
| **BusinessChannel**(`/ncc/channels`) | 8 | **④정적/설정** | 사이트 채널 등록 정보, 시계열·성과 없음 |
| **IpExclusion** | 7 | **④정적/설정** | 계정 단위 IP 제한 도구, 성과 축 아님 |
| **Label·LabelRef** | 3 | **④정적/설정** | 태깅 조직화, 성과 아님 |
| **ManagedKeyword** | 1 | **④정적/설정** | 관리 금지어 사전(컴플라이언스) |
| **TimeContract** | 1 | 미상 | 문서상 설명 빈약(예약 광고 계약 추정) — grain·용도 확인 안 됨 |
| **AnalyticsController**(`/tool/analyticses`) | 3 | 미상 | swagger에 파라미터 스키마가 비어 있어(`$ref` 없음) 용도 확정 못함 |
| **서류관리·세금계산서·Bizmoney(exhaust 외 3개)** | 7 | **④정적/설정** | 행정·회계 인프라, 성과 축 아님 |
| **Ad·AdKeyword·Adgroup·Campaign 나머지**(GET-by-id·CRUD 변형) | 22 | **⑤이미 다른 축에 포함** | list 버전이 이미 호출 중이라 by-id 단건 조회는 같은 데이터의 부분집합. POST/DELETE는 ③쓰기전용 |
| **Estimate 나머지 8종** | 8 | **⑤이미 다른 축에 포함**(#21과 동일 도메인) | median-bid·exposure-minimum-bid·performance(단건)·NPC/NPLA 변형 — B3(시장가 사다리)와 같은 축, 세부 산식만 다름 |

★**등급 교차 가능 후보로 새로 지목된 것 = Criterion GET(1) · MasterReport(5, 특히 Qi) · AdExtension군(9) · SharedBudget(8) · ProductGroup(1) · InspectHistory(2) = 26개 endpoint.** 이 중 Criterion·AdExtension·MasterReport의 grain(adgroup)은 문서상 확정, SharedBudget·ProductGroup·InspectHistory는 grain이 adgroup에 닿는지 이번 조사로 미확정(미상).

---

## 4. P3 — 코드 실사용 현황 (전수 grep, `backend/app/services/naver_sa_ad_fetcher.py` + `backend/app/services/naver_ad/*.py`)

### 4-1. 실제로 호출하는 endpoint (19개, §3-2에 O 표시)

`GET/PUT /ncc/campaigns[/{id}]` · `GET/PUT /ncc/adgroups[/{id}]` · `GET/POST/DELETE /ncc/adgroups/{id}/restricted-keywords` · `GET/PUT /ncc/keywords[/{id}]` · `GET/PUT /ncc/ads[/{id}]` · `GET/PUT /ncc/targets[/{id}]` · `GET /stats` · `GET/POST /stat-reports` · `GET /billing/bizmoney/histories/exhaust` · `POST /estimate/average-position-bid/{type}` · `POST /estimate/performance-bulk` · `GET /keywordstool`.

전부 `backend/app/services/naver_sa_ad_fetcher.py`(`BASE_URL = "https://api.searchad.naver.com"`)를 유일한 진입점으로 경유 — `grep -rl "api.searchad.naver.com" backend/`가 이 파일 1개만 반환, 다른 어떤 서비스 파일도 URL을 직접 하드코딩하지 않는다(전부 `fetcher.BASE_URL`/`fetcher._get` 경유).

### 4-2. `/stats` fields — 19종 중 실사용 6종

요청 필드는 코드 전체에서 3벌뿐(`naver_sa_ad_fetcher.py:432,491,1290`):
- `_STATS_FIELDS`(일별 집계) = `impCnt,clkCnt,salesAmt,ccnt,convAmt,avgRnk`
- `_STATS_HH24_FIELDS`(시간대) = `impCnt,clkCnt,salesAmt,ccnt,avgRnk`(convAmt 제외 — hh24엔 금액 필드가 없다는 문서 제약, 회계 불변으로 코드에 명시)
- `_BACKFILL_FIELDS` = `impCnt,clkCnt,salesAmt,ccnt,convAmt`

**미요청 13종**(한 번도 fields 배열에 등장 안 함): `ctr` · `cpc` · `recentAvgRnk` · `recentAvgCpc` · `pcNxAvgRnk` · `mblNxAvgRnk` · `crto` · `ror` · `cpConv` · `viewCnt` · `purchaseCcnt` · `purchaseConvAmt` · `purchaseRor`.

**요청 필드 자체는 요청→저장 손실이 없다** — 6개 필드는 모두 응답 매핑 코드(`imp/clk/cost/conv_cnt/conv_amt/avg_rank`)에서 그대로 저장된다(라인 479-487, 534-544 확인). 즉 "요청하나 미저장"은 **필드 레벨에서는 0건** — 손실은 "요청 자체를 안 함"(13종) 쪽에 있다.

**`breakdown` 4종 중 실사용 1종**(`hh24`만). `pcMblTp`(기기별)·`dayw`(요일별)·`regnNo`(지역별)는 코드에 문자열 자체가 없다 — 0건 호출. ★이 3개는 **축 #3·#10·#14가 언급하는 리포트/설정 경로와 다른, `/stats` 자체의 단일콜 breakdown**이라 매트릭스에 명시적으로 반영되지 않은 별도 표면이다(§5-3).

### 4-3. `/ncc/targets` — ★요청은 하는데 저장(파싱)하지 않는 필드의 실증 사례

`naver_ad/naver_sa_writer.py:237`, `:310`의 두 함수(`get_shopping_exclusions`, `_shopping_restrict_target`)는:

```python
resp = fetcher._get("/ncc/targets", {"ownerId": adgroup_id})   # type 필터 없음 — 그 그룹의 전체 target 응답
for target in resp.json():
    if target.get("targetTp") != _RESTRICT_KEYWORD_TARGET_TP:  # RESTRICT_KEYWORD_TARGET 외 전부 버림
        continue
    ...
```

`ownerId`만 지정하고 `types` 파라미터로 필터링하지 않으므로, **응답에는 그 adgroup의 `MEDIA_TARGET`·`PC_MOBILE_TARGET`·`GENDER_TARGET`·`AGE_TARGET`·`GENDER_WEIGHT_TARGET`·`TIME_WEEKLY_TARGET`·`REGIONAL_TARGET` 등 targetTp≠RESTRICT_KEYWORD_TARGET인 행도 이미 포함돼 온다.** 코드는 `targetTp`를 비교해 그 자리에서 버린다 — 파싱도, 저장도 안 한다. 이 함수는 쇼핑 제외 검색어 관리(D-NAO-180/181) 경로에서 **주기적으로 호출되고 있으므로**, 추가 API 콜 없이 A5(매체 블랙리스트)·A6(PC/모바일 가중치) 원료가 매번 도착했다가 버려지는 중이다.
→ 이것이 매트릭스가 §F1에서 관측한 "A5는 385그룹 GET까지 이미 했다(08-16~17)"의 **코드 레벨 원인**과 정확히 같은 모양이며, 이번 조사로 **더 구체화**됐다: 일회성 프로브가 아니라 **정규 운영 경로(쇼핑 제외 쓰기 흐름)에 상시 편승 가능**하다는 뜻이다.

### 4-4. StatReport `reportTp` — 13종 중 실사용 5종

코드가 실제로 생성/조회하는 reportTp: `AD`(list_ad_reports·ensure_reports_built) · `AD_CONVERSION` · `EXPKEYWORD` · `SHOPPINGKEYWORD_DETAIL` · `SHOPPINGKEYWORD_CONVERSION_DETAIL`.

**미사용 8종**: `AD_DETAIL` · `AD_CONVERSION_DETAIL` · `ADEXTENSION` · `ADEXTENSION_CONVERSION` · `SHOPPINGBRANDPRODUCT` · `SHOPPINGBRANDPRODUCT_CONVERSION` · `CRITERION` · `CRITERION_CONVERSION`.

★`CRITERION`/`CRITERION_CONVERSION`은 축 #4·#9·#10(연령·성별·관심사·요일시간)의 **벌크 파일 경로**다 — 현재 매트릭스가 서술하는 "엔티티별 GET 전수 스윕"(177그룹 라이브 프로브, 휘발성)보다 리포트 1건 생성으로 **전 그룹을 한 번에** 받을 수 있는 대안일 가능성이 있다(문서상 존재 확인만, 실제 응답 스키마·성공 여부는 미확인 — 실호출 금지).

### 4-5. `/ncc/criterion` — 완전 미호출

`grep -rn "criterion" backend/app/services/` 0건(대소문자 무관, `naver_sa_ad_fetcher.py`·`naver_ad/*.py`·`models.py` 전부). `bidWeight` 문자열도 0건. 매트릭스가 "라이브 프로브(08-16, 177그룹)는 휘발성 scratchpad에만"이라 적은 것과 정합 — **커밋된 코드에는 이 표면을 만지는 경로가 전혀 없다.**

### 4-6. "저장하나 미독" — 이번 조사로 확정 못한 것

매트릭스 F1이 이미 이 패턴의 거울상 사례(`NaverAdgroupHourlyToday` writer는 있고 reader가 0)를 지목했다. 이번 조사에서 같은 문자열을 재확인하니 `models.py` 외에 `load_window.py`·`today_hourly_sweep.py`·`probe_revert.py` 3개 서비스 파일이 이 모델명을 참조한다 — **08-17/08-18 시점 매트릭스의 "reader 0" 서술이 지금도 유효한지, 아니면 이후 배선됐는지는 이번 조사(문자열 참조 확인까지만)로는 못 가른다**(읽기/쓰기 방향 구분에는 각 파일 전문 대조가 필요, 시간 예산상 미실행). "저장하나 미독" 전수 감사(100+ 서비스 파일 × models.py 컬럼 교차)는 이번 census의 범위를 넘는다 — **미확인으로 유보.**
`naver_adgroup_product`(A7 원료 테이블)에 `bid_weight`/`bidWeight` 컬럼은 `models.py`에 없음(grep 0건) — 매트릭스의 "구조사실만" 판정과 정합(그 데이터가 애초에 DB에 없다).

---

## 5. 매트릭스에 안 실린 API 표면 — 최종 목록 + 처분 후보 + 등급 교차 가능성

### 5-1. Qi(품질지수) — MasterReport의 미탐색 대안 경로 [미상]

현재 `qi_grade`(1~7)는 오직 `GET /ncc/keywords` 응답의 `nccQi.qiGrade`에서만 온다(`naver_sa_ad_fetcher.py:772`, `entity_sync.py:237`) — 기존 일일 동기화에 "무상 편승"한 것으로 코드 주석에 명시돼 있다. **`MasterReport item=Qi`는 완전히 다른, 한 번도 호출된 적 없는 경로**(§4-5 인접, grep 0건)다. memory(`품질지수 죽은신호`)가 지목한 "91,172개 전부 `qi_grade=4`" 현상이 취득 경로 자체의 결함인지 값 자체가 정말 그런지는, 독립 경로(MasterReport)로 재조회해 대조한 적이 이번 조사로도 확인 안 됨 — **등급 교차 가능성: ①(강함, adgroup 경유 가능), 원인 규명 여부는 미상.**

### 5-2. AdExtension CRUD·리포트 존재 — 매트릭스 B1 서술과 상충

매트릭스 1-B의 B1(확장소재 성과)은 "주요 유형 API 생성 불가"라고 적었다. 이번 Swagger 조사로는 `/ncc/ad-extensions`(CRUD 6개)와 `reportTp=ADEXTENSION`/`ADEXTENSION_CONVERSION`(성과 리포트 2종) 자체가 문서상 **존재**함만 확인했다. 두 서술 중 어느 쪽이 실제 계정 상황(가입 상품·권한)에 맞는지는 **이번 조사(문서 대조, 실호출 금지)로는 결론 못 낸다** — 상충 사실만 기록.

### 5-3. `/stats` breakdown=`pcMblTp`/`dayw`/`regnNo` — 매트릭스가 다루지 않은 별도 표면

축 #3(S2)은 SHOPPINGKEYWORD_DETAIL **리포트 파일**의 시간대·지역·매체 컬럼을, 축 #14(A6)는 `/ncc/targets`의 **타겟팅 설정값**(PC_MOBILE_TARGET 가중치)을 다룬다. 그러나 `/stats` 엔드포인트 자체가 **entity(adgroup 포함) 단위로 기기·요일·지역별 실적을 단일 GET 호출로 분해**해 주는 `breakdown` 파라미터를 갖고 있고(hh24와 동열의 옵션), 이건 리포트 생성도 타겟팅 설정 조회도 아닌 **세 번째 경로**다. 코드는 `hh24`만 쓰고 나머지 3개는 문자열조차 없다. **등급 교차 가능성: ①(강함)** — adgroup grain 직접 조회이므로 배선 난이도가 축 #24(hh24)와 동일한 클래스로 보인다(단, 실측·시행은 이번 조사 범위 밖).

### 5-4. SharedBudget 8종 — B2(예산)의 정밀화 후보

현재 축 #20(B2)은 `naver_entity_snapshot.daily_budget`(27일 창, 스냅샷 부산물)로 예산을 근사한다. `/ncc/shared-budgets` 계열(공유예산 그룹 자체의 멤버·설정 조회)은 한 번도 호출되지 않았고, 매트릭스 §0-B가 지적한 "39캠페인 중 35개(89.7%)가 다중 밴드"라는 공유예산 구조 문제 자체를 직접 조회할 수 있는 원천일 가능성이 있다. **등급 교차 가능성: ①(정밀화 목적, grain은 캠페인/adgroup 확인됨).**

### 5-5. 최종 미배정 요약

| 처분 | endpoint 수 | 대표 |
|---|---|---|
| **①등급 교차 가능** | 26 | Criterion GET(1)·MasterReport(5)·AdExtension군(9)·SharedBudget(8)·ProductGroup(1)·InspectHistory(2) |
| ②grain 불닿음(계정 단위) | 4 | AdAccounts·ManagerAccounts |
| ③쓰기전용(그 자체로 성과축 아님) | ~30 | 각 리소스의 POST/PUT/DELETE 변형 |
| ④정적/설정 | ~24 | BusinessChannel·IpExclusion·Label·ManagedKeyword·서류관리·세금계산서·Bizmoney 잔여 |
| ⑤이미 다른 축에 포함 | ~22 | Ad/AdKeyword/Adgroup/Campaign의 by-id 단건, Estimate 잔여 8종 |
| 미상(용도 자체 불명) | 3 | TimeContract·AnalyticsController·`/ncc/criterion` type RL·RP 코드 의미 |

(구간 표기 `~`는 태그 경계에서 쓰기/조회가 섞인 항목의 재배분 여지 — 정확한 1개 단위 배정표는 §3-3 표로 대체.)
`/stats` 필드 13종(§4-2)·breakdown 3종(§5-3)은 endpoint 자체가 아니라 **기존 사용 endpoint의 미사용 파라미터**라 이 표(endpoint 카운트)에는 안 잡히지만 §5-3에 별도로 다뤘다.

---

## 6. 이번에도 확인 못한 것

- "Guides" 절(리포트별 컬럼 스키마 prose) — 정적 fetch로 원리적으로 못 열었다(§1). SHOPPINGKEYWORD_DETAIL col7/8/9=시간대/지역/매체는 **코드 실측 근거**이지 이번 조사가 공식 문서로 재확인한 게 아니다.
- `/ncc/criterion` type `RL`·`RP`의 의미 — swagger enum에 코드값만 있고 설명 문자열 없음.
- `bidWeight`(매트릭스 "1,271행")의 정확한 **GET 경로** — `/ncc/criterion` 응답 필드인지 `/ncc/targets` target blob 내부 필드인지 이번 조사로 특정 못함(원 프로브가 휘발성이라 재현 불가).
- AdExtension "주요 유형 API 생성 불가"(매트릭스 B1) vs 이번 조사의 "CRUD 엔드포인트 존재" — 상충의 실제 원인(권한/상품 미가입 vs 문서 오독) 미상.
- MasterReport `item=Qi`가 qi_grade 죽은신호의 원인을 밝히는 대안 경로가 되는지 — 미상(실호출 금지로 검증 불가).
- SharedBudget·ProductGroup·InspectHistory의 정확한 grain(adgroup 도달 여부) — 문서 정의만으로는 응답 스키마의 키 필드가 안 보여 미확정.
- `naver_adgroup_product` "활성 연동만 반영" 가정의 코드 대조(매트릭스가 이미 미상으로 남긴 것) — 이번 조사도 재확인 안 함(범위 밖).
- "저장하나 미독" 컬럼의 전수 감사(§4-6) — `NaverAdgroupHourlyToday`류 1건만 문자열 참조 확인, 나머지 DB 스키마 전체 교차는 미실행.
- 커머스(스마트스토어) API 표면의 이번 재조사 — 지시 P1 범위가 SA로 한정되어 있고 ref 71이 이미 담당, 이번 문서는 손대지 않음.

---

*작성: Sonnet(전수성 검증 담당, 읽기 전용). 1차 출처: `https://naver.github.io/searchad-apidoc/assets/json/{ncc-heroes-ncc,ncc-heroes-tool,ncc-heroes-billing,atower,ncc-report,master-report,ncc-keywordstool,estimate,ncc-inspect-history}.json`(2026-08-18 curl 취득, 전부 200 OK). 로컬 사본은 세션 스크래치패드(`swagger/*.json`), repo에는 반영 안 함. 네이버 API 실호출 0건·prod 접속 0건·git 커밋 0건·파일 수정 0건(신규 산출물 1개만 작성).*
