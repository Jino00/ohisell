# 27. 네이버 SA 쓰기 API 정찰 — 제외키워드 · bidAmt · userLock (X1a T1)

> 작성: 2026-07-10 (실행 루프 X 스프린트 X1a T1). 근거: `docs/PLAN_naver-ad-execution-loop.md` §3-X1a.
> 이 문서는 `naver_sa_writer` SA(T2)와 execution_harness 실쓰기 연결(T3)의 유일한 스펙 근거다.
> **추정 금지 원칙 준수**: 아래 모든 내용은 ①네이버 공식 swagger ②prod 라이브 읽기 실측 중 하나로 뒷받침되며, 확인 안 된 것은 §7에 정직하게 분리했다.

## 0. 출처와 검증 상태

| 항목 | 상태 |
|------|------|
| swagger 스펙 | ✅ 공식 — `naver/searchad-apidoc` GitHub **gh-pages** 브랜치 `assets/json/ncc-heroes-ncc.json` (swagger 2.0, paths 64개). 로컬 보존: `docs/references/data/ncc-heroes-ncc.json` (285KB) |
| 경로 형태·인증·읽기 응답 | ✅ **prod 라이브 실측**(2026-07-10, 읽기 전용 GET만): restricted-keywords GET 200 · adgroup GET 200 · keyword GET 200 |
| 쓰기 왕복(추가→재조회→삭제→재조회) | ⏳ **미실측** — X0-2 카나리 연기(Jino: "프로그램 완성되면 정하자")에 따라 실쓰기 검증 단계에서 수행. §6 시나리오 준비 완료 |

**원본 유실 경위**: MOP 리뷰 세션에서 수집했다는 `ncc-heroes-ncc.json`이 모든 워크트리·스크래치에서 발견되지 않아(mdfind·find 전수 탐색) HANDOFF 폴백 지침대로 공식 GitHub에서 재확보했다. 이번에는 repo 안(`docs/references/data/`)에 커밋해 재유실을 차단한다.

## 1. 공통 — 베이스 URL · 인증 · 경로 규칙

- **BASE_URL**: `https://api.searchad.naver.com` (기존 `naver_sa_ad_fetcher.BASE_URL` 그대로)
- **경로 규칙**: swagger의 path는 `/api/ncc/...`로 적혀 있으나 **실제 호출은 `/api` prefix를 제거**한 `/ncc/...`다.
  - 근거 ①: swagger UI 설정(`gh-pages app/config.js`)의 `uriReplace: {'^\/api': ''}`
  - 근거 ②: 라이브 실측 — `GET /ncc/adgroups/{id}/restricted-keywords` 200 (prefix 제거 형태로 성공, 기존 코드의 `/ncc/campaigns`도 동일)
- **인증**: 기존 `naver_sa_ad_fetcher._headers(path, method)` HMAC-SHA256 서명 그대로 재사용.
  - ⚠️ **서명 문자열의 method는 실제 HTTP 메서드와 반드시 일치**(P2-S1 실측: POST에 'GET' 서명 → 403 signature invalid). writer의 POST/PUT/DELETE는 각각 해당 메서드로 서명할 것.
  - 서명 대상은 **path만**(쿼리스트링 제외) — 기존 GET들이 `params=`를 서명 밖에 두고 성공해 온 것으로 실증됨. `fields=`·`ids=` 쿼리도 동일하게 서명 밖.
- **응답 시각 필드**: `regTm`/`editTm`은 RFC 3339 (UTC) — 해석 시 KST 환산 주의(전역 시간 원칙).

## 2. ① 제외키워드 (X1a T3 개방 대상 — `add_negative_keyword`)

swagger 명칭: **restricted-keywords** (negative search terms). **광고그룹 단위** 리소스다(캠페인 아님).

### 2-1. 추가 — `POST /ncc/adgroups/{adgroupId}/restricted-keywords`
- body: **`AdgroupRestrictKwd` 배열** (단건도 배열로 감싼다)
- 생성 시 필요한 필드(나머지는 `#hidden-create`):
  ```json
  [{"keyword": "제외할검색어", "type": "KEYWORD_PLUS_RESTRICT"}]
  ```
- `type` enum: `KEYWORD_PLUS_RESTRICT`(기본값) | `EXP_SEARCH`. 콘솔의 "노출 제한 검색어"에 해당하는 기본값 사용.
- 응답 200/201: 생성된 `AdgroupRestrictKwd`(배열) — **`nccAdgroupRestrictKwdId`가 여기서 발급**된다. 삭제에 필수이므로 writer는 응답에서 이 ID를 반드시 캡처해 change_log에 저장할 것.
- ⚠️ swagger 설명: "This feature is only available for adgroups of **website campaign types**" — WEB_SITE(파워링크) 전용. 라이브 실측으로 우리 adgroup `adgroupType=WEB_SITE` 확인(§5). SHOPPING 등 다른 유형 캠페인에는 시도 자체를 차단할 것(T2 가드).

### 2-2. 조회 — `GET /ncc/adgroups/{adgroupId}/restricted-keywords?type=KEYWORD_PLUS_RESTRICT`
- 응답 200: `AdgroupRestrictKwd` 배열. **라이브 실측 완료**: 200 + `[]` (현재 등록된 제외키워드 0건).
- writer의 실행 전/후 재조회(검증)용 — before 스냅샷·after 반영 확인 모두 이 GET.

### 2-3. 삭제 — `DELETE /ncc/adgroups/{adgroupId}/restricted-keywords?ids={id1},{id2}`
- `ids` = `nccAdgroupRestrictKwdId` 목록(쿼리 파라미터, required).
- 응답 **204 No Content** (200 아님 — writer의 성공 판정 주의).

### 2-4. `AdgroupRestrictKwd` 모델 (swagger definitions)
| 필드 | 타입 | 비고 |
|------|------|------|
| `nccAdgroupRestrictKwdId` | string | 생성 응답에서 발급, 삭제 시 필수 |
| `nccAdgroupId` | string | 소속 광고그룹 |
| `keyword` | string | 제외 검색어 |
| `type` | enum | `KEYWORD_PLUS_RESTRICT`(기본) / `EXP_SEARCH` |
| `regTm` | string | RFC 3339 |
| `resultStatus` | ResultStatus | 등록 결과 정보(응답 전용) |

## 3. ② 키워드 입찰가 — `PUT /ncc/keywords/...?fields=bidAmt` (X1b 개방 대상 — `update_bid`)

### 3-1. 단건 — `PUT /ncc/keywords/{nccKeywordId}?fields=bidAmt`
- body: `KeywordRequest`. swagger 명시: **`fields=bidAmt`일 때 `bidAmt`와 `useGroupBidAmt` 둘 다 필수**.
  ```json
  {"nccKeywordId": "nkw-...", "bidAmt": 500, "useGroupBidAmt": false}
  ```
- `bidAmt` 범위: **70 ~ 100,000** (swagger 명시 — 기존 estimate 실측 "70~100,000원 10원 단위" 규격과 일치. 10원 단위 제약은 swagger에 없고 estimate API 실측에서 확인된 것 — bid_simulator 클램프 유지).
- 응답 200: 갱신된 `AdKeyword` — `bidAmt`·`editTm` 포함(after 실측값 원료).

### 3-2. 벌크 — `PUT /ncc/keywords?fields=bidAmt`
- body: `KeywordRequest` **배열, 최대 200개**(swagger 명시). 각 원소에 `nccKeywordId` 필수(`#required-update-items`).
- X1b에서 회당 변경 건수 상한 가드레일과 함께 사용 검토(초기에는 단건 권장 — 부분 실패 격리 단순).

### 3-3. `fields` enum (keyword PUT 공통)
`userLock` | `bidAmt` | `links` | `inspect` — writer는 bidAmt·userLock만 사용(links/inspect는 스코프 밖).

## 4. ③ 정지·재개(userLock) — 3계층 PUT (X1b 개방 대상)

`userLock: true = 중지(PAUSED), false = 노출(ENABLED)` — **의미 반전 주의**(lock이 "중지"다).

| 계층 | 엔드포인트 | body 필수 | fields enum |
|------|-----------|----------|-------------|
| 키워드 | `PUT /ncc/keywords/{nccKeywordId}?fields=userLock` | `{"nccKeywordId", "userLock"}` | userLock/bidAmt/links/inspect |
| 키워드 벌크 | `PUT /ncc/keywords?fields=userLock` (최대 200) | 배열 | 〃 |
| 광고그룹 | `PUT /ncc/adgroups/{adgroupId}?fields=userLock` | `AdgroupRequest`(`nccAdgroupId`, `userLock`) | bidAmt/userLock/budget/networkBidWeight/target* |
| 캠페인 | `PUT /ncc/campaigns/{campaignId}?fields=userLock` | `CampaignRequest`(`nccCampaignId`, `customerId`, `userLock`) | **userLock/budget/period** |

- ⚠️ 캠페인 PUT은 **`fields` 생략 시 캠페인 전체가 교체**된다(swagger: "fields 파라미터가 없을 경우 캠페인 전체가 변경됩니다") — writer는 **항상 `fields`를 명시**할 것(부분 수정 강제). adgroup/keyword PUT도 동일 원칙 적용.
- ⚠️ 캠페인 `fields` enum에 `budget`이 있으나 **예산 변경은 X 스프린트 스코프 밖**(D-NAO-34 금지선) — writer에 budget 관련 함수를 만들지 않는다.
- `CampaignRequest.customerId`는 "생성/수정 시 필수 입력값"으로 명시 — 캠페인 userLock PUT 시 포함(`NAVER_SA_CUSTOMER_ID`).

## 5. 라이브 읽기 실측 결과 (2026-07-10, prod에서 읽기 전용 GET만)

기존 `_headers` 서명으로 전부 200 — 인증·경로 규칙이 쓰기 대상 리소스에도 유효함을 실증:

```
GET /ncc/adgroups/grp-a001-01-000000031185769/restricted-keywords?type=KEYWORD_PLUS_RESTRICT
→ 200, []   (제외키워드 0건 — 왕복 실측 시 깨끗한 초기상태)

GET /ncc/adgroups/grp-a001-01-000000031185769
→ 200, {"userLock": false, "bidAmt": 500, "useDailyBudget": false, "dailyBudget": 0,
        "adgroupType": "WEB_SITE", ...}   (WEB_SITE — restricted-keywords 지원 유형)

GET /ncc/keywords/nkw-a001-01-000005009913563
→ 200, {"nccAdgroupId": "grp-a001-01-000000031116306", "keyword": "오하이", "bidAmt": 190,
        "useGroupBidAmt": false, "userLock": false, "status": "ELIGIBLE", "inspectStatus": "APPROVED"}
```

재조회(검증) GET 매핑 — writer의 before/after 실측값 원료:

| 쓰기 | 재조회 |
|------|--------|
| restricted-keywords POST/DELETE | `GET /ncc/adgroups/{id}/restricted-keywords?type=KEYWORD_PLUS_RESTRICT` |
| keyword bidAmt/userLock PUT | `GET /ncc/keywords/{nccKeywordId}` |
| adgroup userLock PUT | `GET /ncc/adgroups/{adgroupId}` |
| campaign userLock PUT | `GET /ncc/campaigns/{campaignId}` |

## 6. 왕복 실측 시나리오 (카나리 지정 후 실행 — 현재 대기)

Jino가 저위험 캠페인(예: 예산 1만원 `벌크`) 1개를 지정하면 아래를 1회 수행(원칙 22 라이브 증거):

1. `GET restricted-keywords` → before 스냅샷(개수 N)
2. `POST` 무해한 테스트 검색어 1건(예: 실검색 유입이 없을 문자열) → 응답에서 `nccAdgroupRestrictKwdId` 캡처
3. `GET` 재조회 → N+1 및 해당 keyword 존재 확인
4. `DELETE ?ids=` → 204 확인
5. `GET` 재조회 → N 복원 확인
6. 오류 응답 형식(존재하지 않는 id 삭제 시도 등 1건)도 이때 함께 채집 → §7 해소

## 7. 문서에서 확인 안 됨 / 실측 대기 (정직 라벨)

- **쓰기 왕복 실증**: §6 전체 — 카나리 대기. **이것이 끝나기 전 T3(실쓰기 개방) 완료 선언 금지**.
- **오류 응답 body 형식**: swagger에 4xx 응답 스키마 없음(코드만: 401/403/404). 공식 에러코드 표는 `naver/searchad-apidoc`의 `NaverSA_API_Error_Code_MAP.md` 참조 가능 — writer 구현 시 status code 기반 fail-closed로 설계하고, body 파싱은 왕복 실측에서 실물 확인 후.
- **제외키워드 광고그룹당 최대 개수**: swagger에 없음. 문서에서 확인 안 됨 — 대량 등록 전 확인 필요(현재 스코프는 건 단위라 즉시 문제 아님).
- **벌크 PUT의 부분 실패 시맨틱**: 200이어도 원소별 실패가 섞이는지(`resultStatus`?) 문서로 불명 — 벌크 사용 전 실측 필수. X1a~X1b 초기는 단건만 사용 권장.
- **rate limit 수치**: 문서에 없음. 기존 `_RETRY_STATUS`(429 지수 백오프) 방어 재사용.

## 8. T2(`naver_sa_writer`)·T3 배선에 주는 시사점

1. **negative_keyword 제안에 adgroup_id가 없다** — 현재 `proposal_writer._negative_keyword_from_exclusion`은 `target_type="search_term", target_id=검색어, campaign_id`만 저장. 그러나 API는 **adgroupId가 필수**. 원천(`exclusion_candidates` 보드 쿼리)은 `NaverSearchTermDaily.adgroup_id`를 이미 SELECT하므로: **T3에서 제안 생성 시 adgroup_id를 제안에 포함**(payload 확장)하거나, 실행 시점에 search_term+campaign으로 `naver_search_term_daily`에서 재해석(resolve). 전자가 단순·결정적 — T2/T3 설계 시 결정(구현 세션 몫, 여기 기록만).
2. writer의 모든 함수는 (실행 전 재조회값, 쓰기 응답, 실행 후 재조회값)을 반환 — §5 재조회 매핑 표 사용. 계획서 T2 계약("실행 전 실측값, 실행 후 재조회값 반환") 그대로.
3. 쓰기 서명은 method 정확 일치(§1) — POST/PUT/DELETE 각각. `_headers`는 이미 method 파라미터를 받는다(재사용, 신규 서명 코드 불필요).
4. 성공 판정: POST=200/201(+body), DELETE=**204**(body 없음), PUT=200(+갱신 body). 코드별 분기 필요.
5. `fields` 항상 명시(§4 전체 교체 함정) + budget 필드는 어떤 경로로도 건드리지 않음(스코프 밖).
