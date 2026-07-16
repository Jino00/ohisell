# 32. 네이버 SA 키워드 시간별 수집·avgRnk·qi recon (hh24 breakdown 실측)

> 실측일: 2026-07-16 밤~07-17 (prod sellc, CUSTOMER_ID=1313769, 총 ~49콜 전부 읽기 GET·쓰기 0)
> 목적: D-NAO-46 "키워드 grain 시간별 수집 + 순위(avgRnk) + 품질지수(qi)" 타당성 확정
> 후속 계획서: `docs/PLAN_naver-ad-keyword-hourly-accrual.md`

## 1. 판정 요약

| 질문 | 판정 | 근거 |
|---|---|---|
| avgRnk를 시간별로 확보 가능한가 | **가능(확정)** | `/stats` fields에 avgRnk 수용, datePreset=today가 당일 중간 누적 순위 실시간 반환. 리포트 rank_sum/imp와 정합(§3) |
| 키워드 grain 시간별 수집 가능한가 | **가능 — 단 매시간 폴링이 아니라 일 1회 hh24 스윕으로** | `breakdown=hh24`+`timeIncrement=allDays`가 1콜에 24시간대 곡선 반환(§4). 매시간 폴링(41,664콜/일) 대비 1/24 비용 |
| 품질지수(1~7) 확보 가능한가 | **가능(확정, 무상)** | `/ncc/keywords` 응답에 `nccQi.qiGrade` 포함(41/41 키워드). entity_sync가 이미 매일 열거 — 추가 콜 0 (§5) |
| 그룹 1콜로 키워드별 행 분해 | **불가(확정)** | breakdown=keyword/nccKeywordId/adgroup 전부 **무언 무시**(200+집계 1행, 400 아님) — 오용 시 무언 데이터 손실 주의 |

## 2. 계정 규모·활성 스코프 (라이브)

- 캠페인 45개: WEB_SITE 13(ELIGIBLE 9)·SHOPPING 30(ELIGIBLE 23)·BRAND_SEARCH 2(전부 PAUSED).
- 등록 키워드 ~90,150(WEB_SITE) — 전량 수집 불가(~4.5시간/회).
- **활성 스코프(2026-07-15 리포트 기준): 노출>0 WEB_SITE 키워드 1,452 + SHOPPING 그룹 284 = 1,736유닛**. imp≥10은 421, clk>0은 226.

## 3. avgRnk 실측 (교차검증)

`GET /stats?id=<nkw-…>&fields=[…,"avgRnk"]` — 키워드 id 단수로 HTTP 200, avgRnk 반환.

| 키워드 | /stats avgRnk(07-15) | 리포트 rank_sum/imp | datePreset=today |
|---|---|---|---|
| …686967 | 2.0 (imp 548) | 1.96 | avgRnk 2.0 (당일 부분 누적 imp 126) |
| …706788 | 2.3 (imp 528) | 2.32 | 1.9 (imp 102) |
| …898420 | 4.2 (imp 167) | 4.18 | 3.9 (imp 134) |

- 실적 0 키워드는 avgRnk=0 반환(0=무의미, 순위는 1부터 → 저장 시 NULL 처리).
- `ids` 복수는 키워드 grain에서도 **400 code 11001** — id 단수=1콜/유닛 확정(캠페인 grain의 기존 실측과 동일).
- 응답시간: 142~360ms(중앙값 ~200ms).

## 4. ★핵심 발견: `breakdown=hh24` + `timeIncrement=allDays`

```
GET /stats?id=nkw-…&fields=["impCnt","clkCnt","salesAmt","avgRnk"]
    &datePreset=today&timeIncrement=allDays&breakdown=hh24
→ 200, data[0] = { id, impCnt, clkCnt, salesAmt, avgRnk,
    breakdowns: [ {"name":"00시~01시","avgRnk":1.8,"impCnt":22,"clkCnt":0,"salesAmt":0}, … ] }
```

- **키워드·애드그룹·(추정)캠페인 id 전부 동작.** breakdowns 엔트리 키 = `[avgRnk, clkCnt, impCnt, name, salesAmt]`.
- breakdowns에는 **실적 있는 시간대만** 옴(당일이면 경과 시간대만). `name`은 한글 라벨("00시~01시")뿐 — 숫자 필드 없음, 파싱은 name 정규식.
- **과거 날짜: 단일일 `timeRange`로 동작하되 보존 = 최근 7일 하드리밋**(초과 시 400 code 11004 "상세 데이터는 최근 7일 이내"). 놓친 날은 7일 내 소급 가능, 그 후 영구 소실.
- **다일 범위 불가 조합**: 다일 timeRange+allDays=전기간 합산 1행(날짜 구분 소실), timeIncrement=1(일별)+breakdown=hh24는 **breakdowns 빈 배열(무언 무효화)** — 날짜×시간대 매트릭스는 날짜당 1콜로 분리 필수.
- breakdown 파라미터는 조건 안 맞으면 **에러 없이 무시**됨(전략적 주의: 응답에 breakdowns 없으면 실패로 간주해야 함).

## 5. qi (품질지수 1~7)

- 스웨거(`docs/references/data/ncc-heroes-ncc.json`): line 4484 `nccQi{qiGrade}` = AdKeyword(파워링크 키워드), line 3937 = Ad(쇼핑 소재) 객체.
- 라이브: `/ncc/keywords` 샘플 adgroup 41키워드 **전부 `nccQi={'qiGrade':4}`**(값 4·5 관측). `expectedClickScore`는 0/41(이 계정 미채움).
- 현 `get_keywords()`(naver_sa_ad_fetcher.py:491)는 nccQi 미추출 — 필드 매핑만 추가하면 entity_sync 기존 일일 열거에 무상으로 얹힘.
- 쇼핑 qi는 소재(`/ncc/ads`) grain(스웨거만 확인, 라이브 미실측).

## 6. 수집 전략 부하 비교

| 전략 | 콜/일 | 직렬 소요 | 비고 |
|---|---|---|---|
| 매시간 전 활성 키워드 폴링 | 41,664 | ~5.2분/시 | 기각 — hh24가 동일 데이터를 1/24 비용에 |
| **★ 일 1회 D-1 hh24 스윕(활성 1,736)** | **1,736** | **~5.8분/회** | 채택 — 전 활성 유닛의 24시간대 imp/clk/cost/avgRnk 완전 곡선 |
| 핫셋(clk>0 226) 매시간 hh24 + 전량 일1회 | ~7,160 | ~45초/시 | 시간당 실입찰 루프 개방 시(게이트 뒤) 후보 |
| 현행 캠페인 grain hourly | ~1,080 | ~8초/시 | 유지(+avgRnk 필드만 추가) |

## 7. 미확인 잔여

- 7일 경계의 정확한 의미(오늘 포함 여부·자정 기준) — 스윕을 D-1로 돌리는 한 무관, 캐치업은 보수적으로 D-6까지만.
- 레이트리밋 절대치(문서 미명시, 연속 ~49콜 무429. fetcher에 429 백오프 기존 보유).
- breakdown 유효 enum 전체(hh24만 확증), 캠페인 id+avgRnk의 datePreset=today 조합(미실측 — 구현은 필드 부재 허용으로 방어).
