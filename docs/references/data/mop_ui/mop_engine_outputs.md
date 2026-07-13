# MOP 엔진 산출물 지도 — "MOP가 각 단계에서 만드는 자료" (A안 관찰)

> 목적: Jino 질문 "예측·플라이트 등을 통해 MOP가 어떤 자료를 만들어내는지 알 수 있지 않아?"에 대한 라이브 캡처.
> 계정: 오하이_구민정 advertiserId=**756**(네이버 SA). 인증 헤더 `x-session-id`. 날짜 2026-07-12(D+1).
> 이 지도가 있으면 다음 세션부터 각 엔드포인트를 replay만 하면 MOP 궤적을 시계열로 쌓을 수 있다.

## 1. 엔진 단계별 엔드포인트 (SA 대시보드 상세 패널이 호출 — 라이브 캡처)

베이스: `https://be.mopapp.net/v1/dashboard/sa/`, 공통 파라미터 `?advertiserId=756`

| 단계 | 엔드포인트 | 만드는 자료(추정 역할) |
|---|---|---|
| 컬렉션 Collection | `collection/status` | 매체 연동·수집 상태 |
| | `collection/items` | 수집 항목(캠페인/그룹/키워드 수) |
| | `collection/performances` | 수집된 성과 원천 데이터 |
| 예측 Projection | `projection/predictions` | **ML 예측**(키워드/소재별 예상 성과) — 대시보드상 ML 모델 40개 |
| | `projection/planning` | **입찰 계획**(러닝→플래닝: 키워드 목표입찰가 산출) |
| 플라이트 Flight | `flight/bids` | **입찰 집행 이력**(24h 입찰 횟수·현재/계획) |
| | `flight/rank-maintenance` | 순위 유지 상태(무료 제공 기능) |
| 이상감지 Abnormal | `abnormal/performances` `abnormal/urls` `abnormal/utms` | 성과 이상·URL 오류·UTM 오류 감지 |
| 종합 | `report` | SA 리포트 요약 |

- 계정 요약: `GET /v1/dashboard/overview/756` → 매체(NAVER OK)·엔진상태(sa/saShopping/dva 각각 collection/prejection/flight = OK).
- ⚠️ **shopping(SPA) 전용 엔진 엔드포인트는 미캡처**(위는 SA=검색광고). SPA 상세 패널을 열면 `/dashboard/saShopping/...` 계열이 나올 것으로 예상(다음 세션 캡처 대상).

### 1-b. 엔진 실데이터 (2026-07-12 라이브 replay, SA)
| 엔진 | 실측 값 |
|---|---|
| collection/items | optimizations 0 · **campaigns 8 · adgroups 215 · keywords 30,810** |
| collection/status | NAVER useY OK · GA4/AIRBRIDGE N |
| projection/predictions | **models: totalCount 40**(lastUpdated 2026.07.10 14:01) · predictions: OK(2026.07.06 08:24) |
| projection/planning | optimizations totalCount **0**·runCount 0(lastUpdated 07-12 15:18) · keywords totalCount 30,810·**targetTotalCount 0·bidsCount 0** |
| flight/bids | bidsCount [0,0,0,0,0,0]·bidsHours [] (currentTime 07-12 15:48) |
| flight/rank-maintenance | keywordCount 0·rankTotalCount 0·monitoring 24h 전부 0 |
| report | MANDATORY_PARAM_ERROR (날짜 파라미터 필요) |

**결론(원칙22)**: 예측 엔진(ML 40개)은 활성 유닛 없어도 상시 생성. **플래닝·플라이트는 활성 유닛 0 → 산출물 전부 0**(계획 실행은 되나 타겟 0). "MOP가 입찰을 계획·집행하는 실물 궤적"은 유닛 1개가 살아있어야 관찰 가능.

### 1-c. 안전 캡처 방법 (크래시 회피 — 중요)
- ⚠️ **동기 XHR(`open(...,false)`)로 엔진 엔드포인트를 치면 브라우저가 크래시**한다(flight/bids가 ~30s 지연→메인스레드 블록→browse 서버 강제 재시작→헤드리스 복귀·로그아웃). 이번 세션에서 4회 재현.
- ✅ **비동기 fetch로 kickoff 후 window 전역에 저장→수 초 뒤 별도 eval로 read** 하면 크래시 0. 스크립트: `/private/tmp/mop_async_kick.js`(패턴 보존). 앞으로 모든 MOP API 캡처는 이 방식.

## 2. 최적화 유닛(=MOP가 만든 자동입찰 산출물) — 라이브 캡처

목록: `GET /v1/optimizations/sa/shopping?advertiserId=756&pageSize=10&pageIndex=1&orderBy=STATUS&sorting=DESC`
상세: `GET /v1/optimizations/sa/shopping/{optimizationId}?advertiserId=756` (4099=15KB)
신규 등록가능 애드그룹: `GET /v1/optimizations/sa/shopping/new-adgroup?advertiserId=756`

현재 상태: totalCount=2, **currentAdgroupsCount=0 / max 30, currentItemsCount=0 / max 1**(활성 유닛 0). 둘 다 종료(END):

| optimizationId | 이름 | optimizationGoal | operationMode | dailyBudget | adgroups | 기간 | boostingRate |
|---|---|---|---|---|---|---|---|
| 4099 | 250617_ROAS최적화 | **ROAS** | **EXPERT** | 193,940 | 44 | 2025.06.24~2026.06.17 | 1.0 |
| 1119 | 전환_240424_쇼검_통합 테스트 | **CONVERSION** | EXPERT | 40,200 | 23 | 2024.04.26~2024.06.18 | 1.0 |

유닛 4099 상세 필드: optimizationGoal=ROAS, kpis=[CLICKS -1, REVENUE -1], adgroupIds=44개(accountId 1313769·campaignId·adgroupId 전체 나열), dailyBudget, addTopCpcYn, boostingRate, minImps, cpcReboot, exclusiveMaxCpc/Sprint/Turbo/Clustering(Pro 플래그) 등.

## 3. 핵심 관찰 (원칙22)

1. **과거 유닛은 EXPERT 모드 + 명시적 숫자목표(ROAS/CONVERSION)** — 이지모드(균형/성장, 숫자목표 없음)와 별개로 **우리 BEP-ROAS 방식에 더 가까운 목표지향 모드가 MOP에 실재**한다. (직전 관찰이 "MOP는 숫자목표 없음"이라 한 건 이지모드만 본 것 — EXPERT 모드는 ROAS/CONVERSION 목표를 명시함.)
2. **플래닝/플라이트는 활성 유닛이 있어야 산출** — 현재 currentItemsCount=0이라 대시보드상 입찰계획 완료비율 0·키워드 조정 0·오늘 입찰 0. 즉 이 두 엔진의 실제 산출물을 보려면 유닛이 하나 살아있어야 한다.
3. **예측(ML)은 유닛 무관하게 생성** — ML 모델 40개(대시보드), 계정 수집 데이터로 상시 러닝.

## 4. 미완(다음 세션)
- [x] ~~projection/predictions·planning, flight/bids body 실데이터~~ → §1-b 완료(비동기 캡처).
- [x] ~~SPA(saShopping) 전용 엔진 엔드포인트 캡처~~ → **§5 완료(2026-07-12, D+2)**. `/dashboard/saShopping/*` 경로는 **존재하지 않음(전부 500)**. SPA 데이터는 `/v1/optimizations/sa/shopping/*`·`/v1/report/opt/*`에 있음.
- [x] ~~종료 유닛 4099 최적화 리포트~~ → **§5 완료**. report/opt 엔드포인트 확보했으나 **보존기간(~2개월)이 4099 활동기(2025-06~2026-04)에 못 미쳐 전 지표 0/`-`**(이력 소멸).
- [ ] gstack 헤디드 크래시 = 동기 XHR 원인 규명됨(§1-c). 비동기로 회피 가능하나, 로그아웃 빈발 자체는 별도 원인(세션 TTL/SVG클릭 네비)으로 잔존 — 계속 점검.

## 5. SPA(쇼핑) 엔진 엔드포인트 — 확정(2026-07-12, D+2 라이브 캡처)

> ★"`/dashboard/saShopping/*`"는 오답(전부 500=INTERNAL_SERVER_ERROR, 존재 안 함). SPA 엔진 산출물은 아래 두 계열에 실재.
> 광고주 컨텍스트 전환은 UI 헥사곤 클릭 대신 **`sessionStorage.setItem('advertiserId','756')`** 로 확정 전환(발견).

### 5-a. SPA 최적화 후보/성과 (위저드 이지모드가 호출) — ★우리 캠페인에 가장 직접적
- `GET /v1/optimizations/sa/shopping/adgroups?advertiserId=756&mediaType=NAVER&startDate=<오늘>&endDate=99991231&saShoppingType=SHOPPING`
- 반환: 캠페인→애드그룹 트리, 각 그룹 플래그 **`active` / `predicted`(ML예측모델 有無) / `inBidding`(활성 최적화 편입) / `dailyBudget`**.
- 실측(756): **25캠페인·450애드그룹 중 predicted=150·inBidding=0**. → MOP는 실적 이력 있는 그룹엔 예측모델을 만들지만(150개), **어떤 그룹도 현재 자동입찰 중이 아님(0)**.
- **00.아이폰_17(9793536)**: campaign·3그룹 전부 `active:true`, 그러나 **`predicted:false`·`inBidding:false`·dailyBudget:null**. → 클릭·전환 이력 0이라 **MOP가 ML 예측모델조차 안 만듦** = 최적화 재료 부재의 근본 확인.

### 5-b. 최적화 리포트(report/opt) — MOP가 뽑는 성과 리포트
- 가용기간: `GET /v1/report/available-period/756?reportType=REPORT_OPT` → **minDate 2026-05-13 ~ maxDate 2026-07-11**(약 2개월만 보존).
- 유닛목록: `GET /v1/report/opt/756/optimizations` → SPA/NAVER에 1119(2024)·4099(2025-06-24~2026-06-17), 둘 다 `bidYn:N`.
- 요약: `POST /v1/report/opt/756/summary?startDate=&endDate=` body `{"optimizationIds":[4099]}`(또는 `{}`=전체).
- 일별표: `POST /v1/report/opt/756/table?startDate=&endDate=&reportType=DAILY&deviceType=false` body 동일.
- ★실측: 4099·전체 모두 보존창(05-13~07-11) **전 지표 `-`/0**(일별표 06-17부터 매일 0). **4099의 실제 ROAS 최적화 궤적은 보존기간 밖이라 복구 불가**.

### 5-c. 유닛 4099 설정(=MOP EXPERT 산출물 스냅샷) — `spa_opt_4099_detail_20260712.json`(15KB)
- optimizationGoal **ROAS** · operationMode **EXPERT** · dailyBudget 193,940 · **adgroupCount 132**(D+1의 "44"는 화면표기값, 실제 adgroupIds=132) · boostingRate 1.0.
- kpis: CLICKS·REVENUE 둘 다 **kpiValue = -1** → ★**목표 "타입"은 ROAS지만 하드 숫자값은 미설정(-1=auto)**. D+1의 "EXPERT=명시적 숫자목표"는 부분 정정: EXPERT는 목표 **방향**(ROAS/CONVERSION)을 고르되 반드시 숫자 타겟을 넣는 건 아님. 우리 BEP-ROAS(하한 숫자 불변)와는 여전히 다름.
- Pro 플래그 전부 N(exclusiveMaxCpc/Sprint/Turbo/Clustering=N, cpcReboot=false) — Basic이라 고급옵션 미사용.

### 5-d. 노출을 미는 메커니즘 (Jino 질문: "노출시키기 위한 작업이 있나")
- **목표입찰 / 순위유지**(라우트 `/target-bidding`): `GET /v1/rank-maintenance/shopping/ad?advertiserId=756&orderBy=BID_YN&sorting=DESC&pageIndex&pageSize` → 현재 `totalCount:0, ads:[]`(등록 0).
- 화면: **"목표입찰 소재 수 0/1"**(Basic 한도 1). 컬럼 = 소재·매체·소재ID·캠페인·애드그룹·**입찰기준 키워드**·**목표 지표**. 소재 1개에 목표(예: 목표순위)를 걸면 MOP가 자동입찰로 그 목표를 맞춤.
- ★**지출이력 불필요** = 0-이력 신규 캠페인도 지금 바로 노출을 밀 수 있는 유일한 Basic 레버. (`입찰최적화` 유닛은 7일평균 예산·predicted 모델이 있어야 해서 신규엔 막힘.)
- 판단 신호: MOP는 후보목록의 `predicted`(ML모델 유무)·`inBidding` 플래그로 캠페인 최적화 자격을 표시. 우리 캠페인=둘 다 false=수집/대기.
- ⚠️ 실제 등록(소재+목표순위 저장)은 **돈·수동**이라 Jino만.

#### 목표입찰 신규생성 폼 전체 구조 (2026-07-12 모달 라이브 캡처) — ★우리 시스템과 가장 유사한 MOP 메커니즘
- 입력 순서: **소재 ID(수동입력, 검색/자동완성 없음)** → 매체(네이버) → 소재 → 캠페인/애드그룹 자동표시.
- **지난 2주 키워드별 광고 실적** 테이블 표시: Keyword·Impression·Click·Cost·Conversion·Revenue·**Avg.Rank**·CPC·CPA·**ROAS(%)**.
- **입찰 기준 키워드 선택**(그 소재의 키워드 중 1개).
- **입찰 목표 설정 = Avg Rank / ROAS / CPA** (3택) ← ★**소재/키워드 단위 수치목표**(Basic에서도 가능. D+1 "Basic=목표 없음"을 결정적으로 반증).
- **입찰가 상세 설정**: 현재 입찰가 · **최대 입찰가**(상한) · **입찰가 변동폭**(스텝). → MOP가 변동폭 스텝으로 최대입찰가 한도 내에서 목표(순위/ROAS/CPA)를 향해 자동 조정.
- **구조 비교(벤치마크 핵심)**: 목표(Avg Rank/ROAS/CPA) + 상한(최대입찰가) + 스텝(변동폭) = **우리 naver_ad의 BEP-ROAS 다이얼 + 가드레일(±상한·스텝)과 사실상 동형**. 차이: MOP=소재/키워드 레벨·목표 다양(순위도), 우리=캠페인/키워드·BEP-ROAS 하한 중심.
- ✅ **소재 ID = SA API로 확보 가능(UI 로그인 불필요)**: `GET /ncc/ads?nccAdgroupId=<grp>` → nccAdId(`nad-a001-02-...`). MOP 폼은 이 **full nccAdId**를 그대로 받음. 소재 detail: `GET /v1/ads/{nccAdId}?mediaType=NAVER&advertiserId=756`(productTitle·adgroup·campaign·**bidAmount**), 키워드실적: `GET /v1/rank-maintenance/shopping/ad/keywordStats?adId={nccAdId}&loginMemberId=<mid>&advertiserId=756`.
- ★★ **적용 블로커 재확정(2026-07-12 실측)**: hero 소재 `nad-...739856`(01.강화유리) 입력 → MOP가 정상 인식(productTitle 로드, 현재입찰가 **1,390원**). **그러나 `keywordStats=[]`(2주 키워드 실적 없음)** → "입찰 기준 키워드 선택" 불가 → **저장 버튼 disabled = 등록 불가**. 즉 목표입찰도 **2주 키워드 실적이 있어야** 등록됨. 07-11 시작한 신규 캠페인은 미충족.
- ★★★ **결론: MOP의 두 노출 레버 모두 history-gated** — ①입찰최적화(성장모드)=7일평균 예산+predicted 모델 필요, ②목표입찰=2주 키워드 실적 필요. **day-1 0-이력 캠페인엔 MOP가 지금 할 수 있는 게 없음.** 레버를 열려면 ~2주 이력 축적이 선행.
