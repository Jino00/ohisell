# 22. P2-S1 데이터 기반 — 라이브 정찰(엔티티 규모·검색어 컬럼·백필 한도)

- 실측일: 2026-07-07 (라이브 VM sellc, CUSTOMER_ID=1313769, 원칙22)
- 방법: 로컬 샌드박스에서 prod `.env`의 NAVER_SA_* 자격증명을 읽기전용 SSH(`cat`)로 확인 후, 로컬
  격리 venv에서 `requests`로 실제 API를 직접 호출(추측 0, prod venv는 건드리지 않음). 스펙은
  `naver/searchad-apidoc`(gh-pages) JSON을 GitHub API로 다운로드해 병행 확인.
- 트랙: `docs/tracks/active/track_naver-ad-optimization.md` / 계획서 §P2-S1

## 1. 엔티티 인벤토리 규모 — 트랙 추정치(4,936) 정정

`/ncc/campaigns` → `/ncc/adgroups?nccCampaignId=` → `/ncc/keywords?nccAdgroupId=` 전수 순회 실측:

| campaignTp | 캠페인 | 그룹 | 키워드 |
|---|---|---|---|
| WEB_SITE(파워링크) | 12 | 513 | **90,150** |
| SHOPPING | 29 | 468 | 33 |
| BRAND_SEARCH | 2 | 9 | 196 |

- **트랙 파일의 "파워링크 등록 키워드 4,936개"는 등록 전체가 아니라 최근 16일 AD 리포트에
  노출이 찍힌 키워드 수였다** (등록 전체는 그 18배인 90,150개). 이 격차 자체가 D-NAO-18
  죽은키워드 위생의 실제 스케일을 보여준다(등록만 되고 한 번도 안 쓰인 키워드가 다수).
- SHOPPING은 개별 키워드가 33건뿐 — AD 리포트에서도 keyword_id='-'(그룹 단위)로만 집계됨을
  재확인. **naver_entity의 keyword 행은 WEB_SITE 소속만 동기화**(SHOPPING/BRAND_SEARCH는
  campaign·adgroup 행만).
- `/ncc/adgroups`, `/ncc/keywords` 응답은 문서화된 JSON 스키마(Adgroup, AdKeyword)와 정확히
  일치 — master-reports(POST 생성+폴링, 컬럼 미문서화 TSV) 대신 이 경로를 채택.

## 2. SHOPPINGKEYWORD_DETAIL 컬럼 실측 (16열, 헤더 없음 TSV)

자동 생성(BUILT, 16일 보관 — AD/AD_CONVERSION과 동일). EXPKEYWORD는 **자동 생성 안 됨**
(POST `/stat-reports {"reportTp":"EXPKEYWORD","statDt":"YYYYMMDD"}` 필요, 확인: `statDt=null`은
400 `잘못된 파라미터 형식`, 유효 날짜 전달 시 200+REGIST 정상). 두 리포트는 동일 컬럼 레이아웃으로
가정하고 같은 파서 재사용(EXPKEYWORD는 실제 다운로드 검증 전이라 P2-S2 착수 전 1회 재확인 권장).

| idx | 의미 | 확정 근거 |
|---|---|---|
| 0 | 일자 YYYYMMDD | AD와 동일 |
| 1 | 고객ID | 상수 |
| 2 | 캠페인 ID | — |
| 3 | 광고그룹 ID | — |
| 4 | **검색어 텍스트**(등록 키워드ID 아님) | 예: "판매갤럭시S25울트라1+1" |
| 5 | 소재 ID | — |
| 6 | 비즈채널 ID | — |
| 7 | 미상("03" 고정 다수) | 불필요 |
| 8 | 미상("02"/"09"/"99") | 불필요 |
| 9 | 미상(큰 정수, 상품/카탈로그 식별 추정) | 불필요, 미검증 |
| 10 | 기기 M/P | — |
| **11** | **노출수(imp)** | prod naver_ad_daily 동일(adgroup,날짜) 합계 대조 — **정확 일치**(663=663) |
| **12** | **클릭수(clk)** | 동일 대조 — **정확 일치**(19=19) |
| **13** | **비용(cost)** | 동일 대조 — 33,243 vs 33,244 (**1원 오차**, 반올림 추정) |
| **14** | **노출순위 합(rank_sum)** | avg_rank=col14/col11, 미검증이나 값 범위(3.59) 합리적 |
| 15 | 미상(0 다수) | 전환수 추정이나 미검증 — 저장하지 않음 |

- 교차검증 표본: 2026-07-05, adgroup `grp-a001-02-000000054537529`, 535개 검색어 행 합산.
- 컬럼 7·8·9·15는 저장하지 않음(D-NAO-18 확장버킷 승격에 불필요, 의미 미확정).

## 3. 백필 한도 (D-NAO-17 미확인 해소)

`/stats?id=<campaignId>&timeRange={"since":...,"until":...}` 실측(2022년 개설 캠페인로 bisection):

- **조회 가능 최대 소급 = 오늘로부터 730일 이내.** 730일 초과 요청 시 400
  `code:11004 "데이터는 최근 730일 이내 기간에서만 조회할 수 있습니다."`
- **1회 호출당 daily breakdown(timeIncrement=1) 최대 92일.** 92일 초과 시 400
  `code:11004 "데이터는 92일 이내 기간에서만 사용 가능합니다."` → 90일 청크로 분할 호출(730/90≈9콜/캠페인).
- 조회 grain은 **캠페인 단위만**(그룹/키워드 세부 불가) — stat-reports(16일)보다 창은 훨씬
  길지만 세밀도는 낮음. naver_ad_daily에는 `adgroup_id='__backfill__'` sentinel로 별도 적재해
  P0 실단위 행과 물리적으로 구분(이중집계 방지).
- 직접/간접 전환 분리 불가(`/stats`는 합산 `ccnt`/`convAmt`만 제공) → conv_indirect_* 컬럼에
  합계를 저장(direct=0 고정, 의미상 "간접"이 아니라 컬럼 재활용임을 코드 주석에 명시).
- **정직 경계(원칙22 재확인, D-NAO-17 본문과 동일)**: 백필 데이터는 '성과'만 있고 '행동' 기록이
  없어 인과 학습(개입→결과)엔 못 쓰고 패턴(계절성·임계값 분포) 학습에만 쓴다.

## 4. keywordstool 실측

- `/keywordstool?hintKeywords=a,b,c,d,e&showDetail=1` — hintKeywords는 **최대 5개/호출**
  (문서 확정, `ncc-keywordstool.json`). 응답엔 요청하지 않은 연관 확장 키워드도 섞여 나오므로
  `relKeyword`가 요청 키워드와 정확히 일치하는 것만 채택.
  `monthlyPcQcCnt`/`monthlyMobileQcCnt`는 문자열, 10 미만이면 `"<10"` — 5로 치환(sentinel).
- 전체 파워링크 저클릭 후보(수천 건) × 5개/콜은 호출량이 크므로 `keyword_volume_sync`는
  30일 누적 클릭 10 미만 키워드만 대상으로 스코프 한정(1회 실행 `limit` 파라미터로 추가 제한).

## 4.5 prod 라이브 실행 결과 (2026-07-07, 원칙22)

- 백업(`~/ohisell_bak/ohisell_20260707_165445.db`, `backend_naver_p2s1_20260707_165445`) →
  migration(`v6w7x8y9z0a1`, additive) → 코드 배포(sha256 전수 검증) → pm2 재시작 → 수동 1회 실행.
- **naver_entity 실측**: campaign 43(off 14·on 29, PAUSED 반영) / adgroup 990(off 216·on 774) /
  **keyword 90,150**(off 876·on 89,274) — §1의 사전 실측과 정확히 일치.
- **naver_search_term_daily**: shopping 39,153행(2026-07-04~06, 3일치) 적재 확인.
- **버그 발견·즉시 수정(원칙22)**: `create_expkeyword_report`(POST)가 기존 `_headers(path)`를
  그대로 재사용 → 이 함수가 서명 문자열에 `.GET.`을 **하드코딩**하고 있어 POST인데 GET으로
  서명 → `403 invalid-signature`. 최초 라이브 실행 시 발견(2건 요청 실패 로그). `_headers`에
  `method` 파라미터 추가(`_get`는 그대로 GET 유지, POST 콜만 `method="POST"` 전달)로 수정 후
  재배포·재검증 — EXPKEYWORD 생성 200 REGIST 확인. (참고: 이 조사 중 "계정 전체 403"처럼
  보인 순간이 있었으나 원인은 별개 — 진단 스크립트가 `app.database`를 임포트하지 않아
  `load_dotenv()`가 안 돌아 자격증명이 빈 문자열이었을 뿐, 실제 계정 차단은 아니었음.)
- cron 3개 정상 등록 확인(`scheduler_state` 조회): `sync_naver_entity`(07:35)·
  `sync_naver_search_term`(07:40)·`sync_naver_keyword_volume`(일요일 09:00).
- EXPKEYWORD는 생성 요청만 확인(REGIST) — BUILT까지의 실제 소요시간은 이번 세션에서
  미관측(비동기 생성, 다음 크론들이 자기치유 방식으로 수집). 다음 세션에서 실제 다운로드까지
  1회 확인 권장.

## 5. 설계 반영 (구현 완료, P2-S1)

- `naver_entity`(campaign/adgroup/keyword, WEB_SITE keyword-only) — upsert 방식(전체
  delete 아님), keywordstool 보강 필드는 재동기화에도 보존, 사라진 엔티티는 `status='deleted'`로
  표시(물리 삭제 안 함 — 이력 보존).
  ⚠️ **미해결(다음 액션)**: 캠페인/그룹→상품(channel_product_id) 연결 데이터 소스가 아직
  없어 `campaign_target_resolver`의 "②쇼핑 상품BEP 연결" 단계는 미구현(①override→③계정
  기본값만 동작). S2 착수 전 재검토 필요 — 이름 기반 추정 매칭은 금전 판단에 근거가 약해
  시도하지 않음(추정 금지 원칙).
- `naver_search_term_daily`(shopping/expkeyword 소스 구분, snapshot 교체).
- `campaign_backfill`(730일/90일청크, sentinel grain).
- `campaign_target_resolver`(override→account_default 매출가중, 상품BEP 연결 단계는 위 참조).
- `keyword_volume_sync`(30일 클릭<10 키워드만 대상).
- cron 3개 등록: `sync_naver_entity`(07:35)·`sync_naver_search_term`(07:40)·
  `sync_naver_keyword_volume`(주1회 일요일 09:00). 캠페인 백필은 1회성이라 cron 미등록
  (스크립트 수동 실행).
