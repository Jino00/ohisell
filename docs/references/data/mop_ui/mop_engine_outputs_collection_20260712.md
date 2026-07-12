# MOP 엔진 산출물 전량 수집 — 예측모델·입찰계획·플라이트·키워드 (2026-07-12)

> Jino 요청: "MOP에서 생성되는 예측모델·입찰계획·플라이트·키워드 등 정보 모두 수집".
> 계정 advertiserId=756(오하이_구민정), API `be.mopapp.net`, 인증 `x-session-id`. 전부 라이브 실측(원칙22).
> ★현재 상태 요지: **예측모델은 상시 생성(40개)·예측대상 애드그룹 다수(SA 142·SPA 150), 그러나 입찰계획·플라이트는 활성 최적화 유닛 0이라 산출물 0.** MOP는 유닛이 살아있어야 계획·집행 궤적을 만든다.

## 1. 수집(Collection) 엔진 — MOP가 잡고 있는 원천
`GET /v1/dashboard/sa/collection/items?advertiserId=756`
```json
{"optimizations":0,"campaigns":8,"adgroups":215,"keywords":30810}
```
- **키워드 30,810개**(검색광고), 캠페인 8·애드그룹 215. (숫자는 SA 대시보드 집계 기준; 후보 트리(§5)는 512 애드그룹으로 더 넓게 잡힘 — 집계 범위 차이.)
- `collection/status`: NAVER useY OK · GA4/AIRBRIDGE 미연동.

## 2. 예측(Projection) 엔진 — ★예측모델·입찰계획
`GET /v1/dashboard/sa/projection/predictions?advertiserId=756`
```json
{"models":{"lastUpdated":"2026.07.10 14:01:01","status":"OK","totalCount":40},
 "predictions":{"lastUpdated":"2026.07.06 08:24:05","status":"OK"}}
```
- **예측모델(ML) 40개**(2026-07-10 갱신), 예측(predictions) OK(07-06 갱신). → 활성 최적화 유닛이 없어도 **상시 생성**.

`GET /v1/dashboard/sa/projection/planning?advertiserId=756` (입찰계획)
```json
{"optimizations":{"lastUpdated":"2026.07.12 15:18:53","status":"OK","totalCount":0,"runCount":0},
 "keywords":{"totalCount":30810,"targetTotalCount":0,"bidsCount":0,"keyManagementCount":0}}
```
- **입찰계획 = 0**(targetTotalCount 0·bidsCount 0). 활성 최적화 유닛 0이라 목표입찰가 산출·키워드 조정 없음. 계획 엔진은 돌지만(status OK) 산출 대상이 0.

## 3. 플라이트(Flight) 엔진 — 입찰 집행
`GET /v1/dashboard/sa/flight/bids?advertiserId=756`
```json
{"bidsCount":[0,0,0,0,0,0],"bidsHours":[],"currentTime":"2026.07.12 17:54:41"}
```
`GET /v1/dashboard/sa/flight/rank-maintenance?advertiserId=756`
```json
{"keywordCount":0,"rankTotalCount":0,"rankAchivementCount":0,"reachMaxCpc":[],
 "monitoring":{"hours":[18,19,...,17],"values":[0,0,...,0]}}
```
- **플라이트 = 0**(24h 입찰 집행 0·순위유지 0). 활성 유닛 0이라 집행할 계획이 없음.

## 4. 이상감지(Abnormal) 엔진
- `abnormal/performances`=[] · `urls`·`utms` 데이터 없음. 감지된 이상 0.

## 5. 예측 대상(후보) — 어느 애드그룹에 예측모델이 붙어 있나 (predicted 플래그)
후보 트리 = `GET /v1/optimizations/sa{,/shopping}/adgroups?advertiserId=756&mediaType=NAVER&startDate=<오늘>&endDate=99991231[&saShoppingType=SHOPPING]`. 원천 저장:
- **SA(검색광고)**: `sa_candidate_adgroups_20260712.json`(90KB) — **11캠페인·512애드그룹·predicted 142·inBidding 0**. predicted 애드그룹 예: 아이폰 파워링크/아이폰15프로_사생활보호, 아이폰17_사생활보호 등(제품별 파워링크 그룹).
- **SPA(쇼핑)**: `spa_candidate_adgroups_20260712.json`(87KB) — **25캠페인·450애드그룹·predicted 150·inBidding 0**. 우리 00.아이폰_17 3그룹은 predicted:false(이력0).
- 플래그 의미: `predicted`=이 애드그룹에 ML 예측모델 있음 / `inBidding`=활성 최적화에 편입돼 자동입찰 중. **inBidding은 SA·SPA 통틀어 0**(현재 자동입찰 중인 유닛 없음).

## 6. 최적화 유닛(=계획·플라이트를 켜는 스위치) — 현재 전부 종료 + ★왜 없는지
`GET /v1/optimizations/sa/shopping?advertiserId=756...` (currentItemsCount 0 / maxItemsCount **1**=Basic 동시 1개):
| 유닛 | goal·mode | status | errorStatus | 생성일 | 입찰기간 | adgroups |
|---|---|---|---|---|---|---|
| 4099 | ROAS·EXPERT | **END** | **null** | 2025.06.23 | 2025-06-24~**2026-06-17** | 44 |
| 1119 | CONVERSION·EXPERT | END | null | 2024.04.25 | 2024-04-26~2024-06-18 | 23 |
- SA(`/v1/optimizations/sa`)=totalCount 0.
- **★활성 유닛이 없는 이유(실측)**: 둘 다 `status=END`·`errorStatus=null` = **오류가 아니라 정상 종료**. 마지막 유닛 **4099가 설정된 입찰 종료일 2026-06-17에 도달해 종료**(약 1년 가동). 그 후 **새 유닛 미생성** → 0/1. 고장이 아니라 "기간 만료 후 새로 안 켠 상태"(우리가 MOP를 운영→관찰/벤치마크로 전환한 흐름과 맞물림). 새로 켜려면 유닛 생성(돈) 필요하고, 우리 테스트 캠페인은 이력·예산 부족으로 생성 막힘.
- ⚠️참고: D-NAO-41의 "2026-07-10 MOP 유닛 종료" 서술은 날짜 부정확 가능 — 실제 유닛 종료=**2026-06-17**(4099). 07-10은 외부 생성 보고서 의존이 끊긴 별개 시점. (수집 자족화로 이미 해소.)

## 7. 결론 (원칙22)
- **상시 돌아가는 것**: 수집(키워드 30,810)·예측모델(40개, 예측대상 SA 142+SPA 150 애드그룹).
- **0인 것(활성 유닛 없어서)**: 입찰계획(target 0)·플라이트(bids 0)·순위유지(0).
- 즉 MOP는 **예측까지는 상시 준비**하지만, **입찰계획·집행(플라이트)은 최적화 유닛이 살아있을 때만** 산출. 지금은 SA·SPA 유닛 전부 종료라 그 두 단계가 비어 있음.
- 우리 00.아이폰_17은 predicted:false라 **예측모델 단계에도 아직 안 들어감**(이력 2주 축적 필요).

## 부록: 재수집 방법
- 요약 엔진: `/v1/dashboard/sa/{collection/items,collection/status,projection/predictions,projection/planning,flight/bids,flight/rank-maintenance,abnormal/performances}` + `/v1/dashboard/overview/756`.
- 후보 트리(predicted 플래그): §5 엔드포인트.
- 유닛: `/v1/optimizations/sa`·`/v1/optimizations/sa/shopping`.
- ⚠️ 반드시 비동기 fetch(동기 XHR=크래시). 대용량(90KB) fetch는 3~6s 폴링 후 read.
- ⚠️ 키워드 30,810·모델 40개의 **per-키워드/per-모델 예측값**은 Basic 대시보드에 리스트로 노출 안 됨(요약 카운트 + 애드그룹 predicted 플래그까지가 Basic 한계). per-키워드 예측은 최적화 생성 위저드의 키워드 단계(유닛 생성 시)에서만 노출.

## 8. 일별 엔진 스냅샷 추적 (Jino 요청: 매일 누적)
> 소재 스냅샷(`soljae_snapshot.py`)=SA API로 자동. **엔진 스냅샷은 MOP 세션 필요**(Jino 로그인 시 `mop_engine_snapshot.js`를 `$B eval`로 캡처 후 아래 append).
> 추적 지표: 예측모델 수·predicted 애드그룹(SA/SPA)·입찰계획 target·플라이트 bids·활성 유닛 수.

| 날짜 | 예측모델 | SA predicted | SPA predicted | 입찰계획 target | 플라이트 bids | 활성유닛(SA/SPA) | 00.아이폰_17 predicted |
|---|---|---|---|---|---|---|---|
| 2026-07-12 | 40 | 142/512 | 150/450 | 0 | 0 | 0/0 | false |
