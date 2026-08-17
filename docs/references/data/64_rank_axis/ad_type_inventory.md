# 네이버 광고 종류 전수 실측 — PAO 설계서 분모 (2026-08-17)

조회 시각: 2026-08-17 KST. repo HEAD `5e14d191`(main, 워킹트리 clean). prod DB `sellc.ohitech.co.kr:/home/ubuntu/ohisell/backend/ohisell.db`(readonly). 실행 SQL 3개는 §5.

## 1. 광고 종류 전수 표

### 층 1 — 검색광고(SA) 캠페인 유형

`naver_ad_daily` 전체 기간(2025-07-22~2026-08-16, 949,511행) 기준 `campaign_type` distinct 3종 + 빈 문자열 잔재 1건.

| campaign_type | 캠페인 수 | 광고그룹 수 | 90일 Σcost (05-19~08-16) | 90일 Σ전환액 | 최신 데이터일 | 온/오프(entity, 캠페인) | 출처 |
|---|---|---|---|---|---|---|---|
| **SHOPPING**(쇼핑검색) | 31 | 367(전체 기간)/482(ref57 라이브) | **61,377,717원** | 155,873,770원 | 2026-08-16 | on 19 · off 12 | `naver_ad_daily` GROUP BY, `naver_entity` |
| **WEB_SITE**(파워링크) | 13 | 489 | **26,099,476원** | 61,505,780원 | 2026-08-16 | on 7 · off 6 | 〃 |
| **BRAND_SEARCH**(브랜드검색) | 2 | 1(ad_daily엔 1그룹만 잡힘; entity엔 9그룹) | **0원**(90일 전량 0 — 44일치만 행 존재, 전부 off) | 0 | 2026-08-16 | on 0 · off 2 | 〃 |
| (빈 문자열, campaign_id `cmp-a001-01-…`) | 1 | 16 | 0원(전량 cost=0) | 0 | **2026-01-14 단발** | — | `naver_ad_daily`(§4 확인 안 됨 항목 참조) |

★공식 스펙(`ncc-heroes-ncc.json`, ref 57 §2) 상 `Campaign.campaignTp`는 5종: `WEB_SITE·SHOPPING·BRAND_SEARCH·PLACE·POWER_CONTENTS`. **우리 계정엔 PLACE·POWER_CONTENTS 캠페인 자체가 없다**(캠페인 0개 — ref57 §2 확인, 이번 조사는 그 문서를 재확인만 했고 라이브 재조회는 안 함).

### 층 2 — 검색광고 밖의 네이버 광고 (`ad_costs`, channel=`네이버 스마트스토어`)

| 종류 | 테이블·source 값 | 90일 Σ비용(05-19~08-16) | 전체기간 Σ비용 | 최신 데이터일 | 성과(imp/clk/전환) 보유? |
|---|---|---|---|---|---|
| **ADVoost 쇼핑**(`PMAX`) | `ad_costs.source='gfa:advoost'` | **4,053,806.44원**(62건, 06-05~08-16) | 4,053,806.44원 | 2026-08-16 | **없음** — `product_id` 전건 NULL(§5 q2), 비용만 |
| **GFA(성과형 디스플레이)** | `ad_costs.source='gfa:da'` | **1,221,400.35원**(73건, 06-05~08-16) | 1,221,400.35원 | 2026-08-16 | **없음** — 동일 |
| 구 수동 CSV(`gfa:쇼핑`) | `ad_costs.source='gfa:쇼핑'` | 3,171,151원(17건, 05-19~06-04만 — 06-05부터 자동 수집으로 대체돼 중단) | 37,421,280원(2026-01-01~06-04, 149건) | 2026-06-04 | 없음 |
| naver_sa:* (품목축 배분, 참고) | `ad_costs.source='naver_sa:*'` 10종 | 검색광고 NCC 비용을 상품 카테고리로 배분한 것 — **campaign_type 축이 아니라 다른 축**(품목). 층1의 SHOPPING+WEB_SITE 합계와 별도 대사됨(트랙 §245 「±13원 이내 일치」). | — | 2026-08-16 | 해당없음(SA 성과 원장은 `naver_ad_daily`가 정본) |

**확정 사실**(트랙 §245~252, ref 62 원문 대조 — 이번 조사는 DB만 재확인): ADVoost·GFA는 `/billing/bizmoney/histories/exhaust`(실차감) 소급으로만 비용을 수집하고, **네이버 SA API의 캠페인 타입 enum에도 ADVoost가 없고 성과형 디스플레이 API는 베타+공식 파트너사 한정**이라 imp/clk/conv를 원리적으로 못 가져온다. `product_id` NULL 전수(§5 q2)가 이를 재확인 — 상품 축도 없다.

## 2. 커버리지 매트릭스 (종류 × 8표면)

| 표면 | SHOPPING(쇼핑검색) | WEB_SITE(파워링크) | BRAND_SEARCH(브랜드검색) | ADVoost 쇼핑(PMAX) | GFA(디스플레이) | PLACE·POWER_CONTENTS |
|---|---|---|---|---|---|---|
| **성과 수집**(imp/clk/cost/전환) | **O** — `naver_ad_daily`(90일 68,143행 전기간, imp 있는 행 19,355/19,868=97.4%) | **O** — `naver_ad_daily`(90일 881,244행 전기간) | **부분** — 행은 있으나(88행/90일) 전부 cost=0(캠페인 off라 노출 자체가 0, 사각은 아님) | **X** — 비용만, imp/clk/conv 없음(§1) | **X** — 비용만(§1) | **해당없음** — 캠페인 0개 |
| **검색어 grain** | **O** — `naver_search_term_daily source='shopping'`(457,400행, 06-16 개통 이후 07-04~08-16) | **O** — `source='expkeyword'`(2,598,758행, 2025-07-23~) | **X** — 별도 검색어 리포트 없음(꺼져 있어 미확인) | **X** | **X** | — |
| **순위 지표**(rank_sum) | **O** — 90일 18,510/19,868행(93.2%)에 rank_sum>0 | **O** — 90일 139,990/140,613행(99.6%) | **X**(cost=0이라 노출 자체 없음) | **X**(리포트 축 없음) | **X** | — |
| **입찰 레버** | **부분** — 그룹 입찰(`update_adgroup_bid`) + 소재 개별입찰(1,761행/238그룹, `naver_adgroup_product`) 둘. 검색어 단위 조준 **불가**(구조적, ref64 §6-1) | **O** — 키워드 단위(`update_keyword_bid`, `naver_entity` 키워드 91,172개) + 그룹/소재도 가능 | **X** — 코드 배선은 있으나(`update_bid` 매핑 자체는 campaign_type 무관) 캠페인이 전부 off·`naver_campaign_settings` 미등록이라 **PAO 실행 대상 밖** | **X** — 입찰 개념 자체가 없음(정액/자동입찰 상품) | **X** | — |
| **제외 레버** | **O**(2026-08-17 D-NAO-180/181로 신규 배선, **prod 배포 확인됨**) — `GET/PUT /ncc/targets`의 `RESTRICT_KEYWORD_TARGET`, 3,880건 읽음·쓰기 왕복 검증 완료(`naver_sa_writer.py` `SHOPPING_ADGROUP_TYPE` 분기, prod grep 5건 확인) | **O** — `EXP_SEARCH`+`KEYWORD_PLUS_RESTRICT` 두 타입(D-NAO-179, PR #303, 원장 105건 편입) | **X** — 스펙상 `restricted-keywords`가 `WEB_SITE 전용` 게이트(ref57 §4)라 브랜드검색은 API 자체가 지원 안 함(확인 안 됨: `/ncc/targets` 경로가 BRAND_SEARCH에도 통하는지는 미시험) | **X** | **X** | — |
| **예산 레버** | **O** — `update_campaign_budget`는 campaign_type 무관(코드상 게이트 없음) | **O** | **O**(코드상 가능하나 캠페인이 off·미등록이라 미사용) | **X**(정액계약이라 일예산 개념 없음, ref58 §12-7) | **X** | — |
| **타겟팅 레버**(bidWeight·시간대·지역·매체·기기) | **X** — 실설정 존재(§13-3: 연령 95그룹·1,140행, 성별 1그룹) **읽기만 라이브 프로브로 확인됐을 뿐 DB 적재·코드 반영 0**(`bidWeight` grep 전수 0건, ref58 §3·§9) | **X** — 실설정 존재(연령 6그룹·72행, 요일시간 4그룹·56행) 동일하게 미반영 | 확인 안 됨(이 조사에서 타겟팅 그룹 스윕 재실행 안 함, ref58은 385그룹=SHOPPING+WEB_SITE만 훑음) | 해당없음 | 해당없음 | — |
| **자동운영 대상**(`optimizer`) | **부분** — `naver_campaign_settings`에 등록된 **6캠페인만**(전체 SHOPPING 31개 중), 전부 `optimizer='none'`(전면 정지 중) | **부분** — 등록 **1캠페인**(전체 13개 중), `optimizer='none'` | **X** — `naver_campaign_settings`에 **0행**(등록 자체가 없음, 이번 조사 신규 확인) | **X** | **X** | — |

## 3. ★우리가 아예 안 보는 종류 / 안 읽는 레버

1. **BRAND_SEARCH(브랜드검색) — `naver_campaign_settings` 등록이 0건이다.** SHOPPING 6·WEB_SITE 1은 등록돼 있어 `optimizer` 상태를 확인할 수 있지만, BRAND_SEARCH 2캠페인은 **PAO 시스템 자체가 그 존재를 모른다**(현재는 둘 다 off·30일 집행 0원이라 실害는 없으나, 켜지는 순간 사각이 된다). 이번 조사에서 새로 확인한 사실(ref57·58은 이 표를 직접 안 냄).
2. **ADVoost(PMAX)·GFA(디스플레이) — 성과 원리적으로 못 봄.** 비용은 자동 수집되나(90일 528만원) imp/clk/전환/상품 연결이 전부 없다. 층1 SHOPPING·WEB_SITE 합계(90일 8,748만원)의 6% 규모.
3. **PLACE(플레이스광고)·POWER_CONTENTS(파워콘텐츠) — 캠페인 자체를 안 산다**(0개, ref57 §2 재확인만). 우리가 안 보는 게 아니라 아예 운영하지 않는 상품.
4. **타겟팅 레버(연령·성별·요일시간·지역·매체·PC/모바일 가중치) — 실설정이 SHOPPING 1,215행·WEB_SITE 128행 있는데 코드가 어디서도 안 읽는다.** `bidWeight` grep 전수 0건(hourly_pattern.py 주석 1곳만 언급, 미적용). 우리 실효 입찰가·순위 서보·BEP 판정 전부 「가중치 100%」를 암묵 가정.
5. **확장소재(ad-extensions) 413개 — 성과를 안 잰다.** `ADEXTENSION`·`ADEXTENSION_CONVERSION` 리포트 타입 미생성.
6. **쇼핑 검색어 리포트의 시간대·지역·매체 3열 — 매일 받으면서 버린다.** `naver_sa_ad_fetcher.py`가 col7/8/9를 「미상, 불필요」로 주석 처리(ref58 §12-1). `naver_search_term_daily` grain에 그 축이 없어 물리적으로 적재가 안 됨.
7. **타겟팅 성과(`CRITERION`·`CRITERION_CONVERSION` 리포트) — 적재 테이블이 아직 없다.** `naver_criterion_daily`류 테이블 0개(현재 `.tables` 전수 확인), C-1(ref58) 제안 상태 그대로 미착수.
8. **자동입찰(`autobidStrategy`) — 읽기만, 쓰기 배선 없음.** 12/12 표본이 꺼져 있는 것만 확인(ref57), 이번 조사는 재확인 안 함.
9. **쇼핑 브랜드형·카탈로그형(채널 존재, 광고 미집행)** — `SHOPPING_BRAND` 채널 PAUSED·`CATALOG` 채널 ELIGIBLE·상품그룹 3건(그중 2026-07-13 신설 1건, 그룹 2개 연결). 우리 시스템은 이 grain을 전혀 모른다.

## 4. 확인 안 됨 목록

- **빈 문자열 `campaign_type` 16행(2026-01-14 단발)의 원인** — `campaign_id=cmp-a001-01-…`(01 prefix는 WEB_SITE 관례와 일치하나) `cost=0`이라 실무 영향은 없어 보이나, 왜 그 하루만 `campaign_type`이 안 채워졌는지는 조사 안 함(로그·배포 이력 대조 필요).
- **`naver_ad_daily` BRAND_SEARCH 캠페인 수가 entity(2개)와 adgroup 수(entity 9개 vs ad_daily 1개)가 안 맞는 이유** — 두 캠페인이 계속 off라 `naver_ad_daily`엔 노출이 있었던 극소수 행(88행/90일, 전부 cost=0)만 잡히고, entity는 구조 스냅샷이라 off 그룹도 전부 담는 차이로 보이나 **원리 확인은 안 함**.
- **BRAND_SEARCH에 `/ncc/targets`(제외 레버)가 실제로 통하는지** — 스펙 문구(`restricted-keywords`가 WEB_SITE 전용)는 확인됐으나, `/ncc/targets`의 `RESTRICT_KEYWORD_TARGET`이 BRAND_SEARCH에도 적용되는지는 **네이버 API 호출 금지 제약상 이번 조사에서 라이브 시험 안 함**. 캠페인이 꺼져 있어 우선순위도 낮음.
- **PLACE·POWER_CONTENTS 관련 지면·과금 요율** — ref57 §0·§8이 이미 「마케팅 사이트 접근 차단으로 확인 못 함」으로 명시, 이번 조사도 재시도 안 함(범위 밖 — 이 조사는 DB·코드 재확인이 목적).
- **`naver_change_log`의 빈 `campaign_type`(`update_guardrail_params` 2건)** — `campaign_id`가 계정 레벨 설정이라 캠페인에 안 걸리는 행으로 보이나 확정 안 함.
- **90일 창의 WEB_SITE 캠페인 수(13) vs 층1 라이브 표(ref57, 8/17 조회 13개 on 8)의 근소한 차이** — on/off 카운트가 조사 시점차로 다를 수 있음(ref57은 8/17 오전, 이 조사는 같은 날 다른 시각), 재조회 안 함.
- **BRAND_SEARCH 캠페인이 향후 켜질 계획이 있는지 / D-NAO 트랙에 재개 결정이 있는지** — 이번 조사는 사실 지도이므로 전략 판단(재개 여부)은 범위 밖.

## 5. 실행한 SQL 파일과 명령

```
ssh -o BatchMode=yes sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db" < q1_layer1_types.sql
ssh -o BatchMode=yes sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db" < q2_layer2_display.sql
ssh -o BatchMode=yes sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db" < q3_coverage.sql
```

파일(전량 로컬 스크래치패드, 전부 SELECT-only, `-readonly` 플래그 사용):
- `q1_layer1_types.sql` — `naver_ad_daily` campaign_type별 캠페인/그룹/90일 비용, `naver_entity` 캠페인·그룹·키워드 status 분포, `naver_campaign_settings` 전 캠페인 목록(PAO 등록 범위 확인)
- `q2_layer2_display.sql` — `channels` 테이블, `ad_costs` source별 집계(전체·90일), `product_id` NULL 여부(ADVoost·GFA 상품 연결 없음 확인)
- `q3_coverage.sql` — rank_sum 커버리지, `naver_search_term_daily` source 분포, `naver_adgroup_product` campaign_type, `naver_search_term_exclusion` campaign_type×source×status, `naver_change_log` action×campaign_type

코드 확인(읽기 전용, `grep`만):
- `backend/app/models.py` — 테이블 정의·docstring 전수(층1·2 근거)
- `backend/app/services/naver_ad/naver_sa_writer.py` — SHOPPING/WEB_SITE 분기(`SHOPPING_ADGROUP_TYPE`), 쇼핑 제외 읽기·쓰기 함수(§2 제외 레버)
- `backend/app/services/naver_ad/naver_execution_harness.py` — `_ACTION_BY_PROPOSAL_TYPE`(입찰·예산·제외 레버 목록)
- `git log --oneline -15`, `git log --oneline --all | grep D-NAO-18[01]` — D-NAO-180/181(쇼핑 제외 API) 배포 확인
- prod SSH grep으로 `naver_sa_writer.py`의 `SHOPPING_ADGROUP_TYPE`(5회)·`RESTRICT_KEYWORD_TARGET`(8회) 존재 확인 — **D-NAO-181 쇼핑 제외 쓰기가 이미 prod에 배포돼 있다**(로컬 main 최신 커밋 `5e14d191`과 prod가 동일 로직)
