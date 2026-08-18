# ref 77 — `/ncc/targets` 전수 실측: MEDIA_TARGET media id ≡ col9 매체 코드 동일성 판정

> D-NAO-197 ③(매체 블랙리스트 A5/A6) 착수 첫 단계. 읽기 전용 GET만 수행, 새 API 호출 없음.
> 원자료: `docs/references/data/77_targets_surface/`. 재현 명령·독립 재집계 대조표는 §1·부록 참조.

## §0. 한 줄 판정

**`MEDIA_TARGET`의 media id와 `naver_search_term_dim_daily`(dim_type='m', col9)의 매체 코드는 같은 코드 공간이다.**

근거 강도는 둘로 나뉜다:
- 집합 대조(교집합 17/합집합 68)는 **정황 증거**다 — 겹치는 숫자가 많다는 것만으로 동일 공간이라 단정할 수 없다.
- **인과 검정(§2-2)이 결정적이다**: 블랙리스트로 등록된 (그룹, 매체) 쌍 1,864건 중 실제 송출 실적이 잡힌 것은 1건뿐이고, 그 1건도 등록 시각과 송출일이 같은 날이라 등록 전 노출로 해소된다. 같은 그룹집합의 비블랙 쌍은 197,835행이 정상 송출됐다. 코드 공간이 다르다면(우연히 자릿수만 겹치는 별개 값이라면) 블랙 쌍도 기저율로 송출됐어야 하는데 사실상 0이다 — 이건 우연으로 설명되지 않는다.

## §1. 무엇을 어떻게 쟀나

- **프로브 대상**: `/ncc/targets?ownerId={adgroup_id}` GET, 그룹 단위. 두 배치로 나눠 시도 537건.
  - `targets_raw.jsonl` — `naver_search_term_dim_daily`(dim_type='m')에 **전체 172일 창(2026-02-19~2026-08-18) 동안 한 번이라도 등장한 SHOPPING 그룹 307개** 전건.
  - `targets_other.jsonl` — 그 307개를 제외하고, **최근 30일 `naver_ad_daily`에 등장한 그 밖의 그룹 230개**(대부분 WEB_SITE·일부 BRAND_SEARCH).
- **호출부**: `app.services.naver_sa_ad_fetcher._get("/ncc/targets", {"ownerId": g})` — 정규 운영 경로(`naver_sa_writer.get_shopping_exclusions`)가 상시 호출하는 것과 같은 함수. 요청 간 0.12초 슬립.
- **실행 시각**: 2026-08-19 (KST), 로그 `probe_targets.log` 타임스탬프 없음(상대 순서만 기록) — 원자료 파일 mtime 08:05~08:10 (KST 오전).
- **재현 명령**: `python3 docs/references/data/77_targets_surface/probe_targets.py <scratch_dir> [limit]` (`.env` 자격증명 필요, 쓰기 없음).
- **DB 조회 방법**: 로컬 `.sql` 파일 작성 후
  `ssh -o BatchMode=yes sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db" < 파일.sql`
  (인라인 heredoc은 따옴표가 벗겨지는 문제가 있어 쓰지 않았다 — `ssh-heredoc-strips-quotes-use-scp` 교훈).

## §2. 동일성 판정

### 2-1. 집합 대조 (정황)

| | 값 |
|---|---|
| B = SHOPPING 그룹의 블랙 media id 합집합 | **48종** |
| M = col9(dim_type='m') distinct dim_value | **37종** |
| B ∩ M | **17종** |
| B \ M (블랙엔 있는데 col9엔 없음) | 31종 |
| M \ B (col9엔 있는데 블랙엔 없음) | 20종 |

B\M(31)은 "블랙으로 걸어놨지만 애초에 그 매체로 송출 자체가 안 잡힌" 값(예: 계정 전체에서 항상 0인 매체)일 수 있고, M\B(20)는 "블랙으로 안 걸었으니 정상 송출된" 값이다 — **둘 다 동일성을 부정하는 증거가 아니라 오히려 정합적**이다(블랙 걸면 안 뜨고, 안 걸면 뜬다).

★자릿수도 창(전체 172일 vs 그룹 307개 전수)을 병기: B는 5자리 7종·6자리 41종(전부 5~6자리). M은 **4자리 1종(`8753`)·5자리 6종·6자리 30종** — `models.py:3197` docstring의 "6자리" 서술과 어긋난다(§5-ⓐ).

### 2-2. 인과 검정 (결정적)

SHOPPING 그룹의 블랙 (adgroup, media) 쌍을 전부 나열해 `naver_search_term_dim_daily`(dim_type='m', 창 전체 172일)와 조인했다.

| 쌍 집합 | 쌍 개수 | dim 테이블에서 실제 매칭된 행 | imp | clk | cost |
|---|---|---|---|---|---|
| **블랙 쌍**(1,864건) | 1,864 | **1행** | 1 | 0 | 0원 |
| 같은 그룹집합의 **비블랙 쌍** | (계산 안 함, 조합 전체) | 197,835행 | 3,182,005 | 29,846 | 41,101,626원 |

블랙 쌍 1,864건 중 dim 테이블에 걸린 것은 1건, 그것도 노출 1·클릭 0·비용 0. 반면 같은 그룹들의 비블랙 쌍은 정상 규모로 송출됐다(19.8만 행, 4,110만원). **코드 공간이 달랐다면 "우연히 같은 값"인 블랙 쌍도 비블랙 쌍과 같은 기저율로 나타났어야 하는데, 사실상 완전히 억제되어 있다** — 이게 동일성의 결정적 근거다.

### 2-3. 유일한 반례의 해소

블랙 쌍인데 송출이 잡힌 유일한 사례:

- 쌍: `grp-a001-02-000000070451363` × media `335738`
- 송출일(dim 테이블 ad_date): **2026-07-21**, imp 1·clk 0·cost 0
- 그 그룹 `MEDIA_TARGET`의 `regTm`: 2026-07-20T08:15:29.000Z, **`editTm`: 2026-07-21T11:51:52.000Z**(블랙 등재/수정 시각)

송출일과 등재 수정일이 **같은 날**이다. dim 테이블의 `ad_date`는 일 단위 grain이라 시:분 정보가 없어 "그날 몇 시에 노출이 잡혔는지"까지는 확정 못 하지만, 등재 시각이 그날 오전~낮(UTC 11:51 = KST 20:51)이라는 점에서 **"당일 등재 이전 노출"로 보는 것이 정합적**이다. ⚠️ 시:분 단위까지 완전히 확정하려면 노출 발생 시각이 필요한데 그건 이 리포트 grain에 없다 — **이 부분은 날짜 단위 정합이지 시각 단위 증명은 아니다**. 반증(=코드 공간이 다르다는 증거)으로 볼 근거는 없다.

## §3. `/ncc/targets` 표면의 실제 모습

ref 75(`docs/references/data/75_api_surface_census/ADS_CENSUS_20260818.md` §4-3)는 Swagger 정의를 근거로 이렇게 서술했다:

> "응답에는 그 adgroup의 `MEDIA_TARGET`·`PC_MOBILE_TARGET`·`GENDER_TARGET`·`AGE_TARGET`·`GENDER_WEIGHT_TARGET`·`TIME_WEEKLY_TARGET`·`REGIONAL_TARGET` 등 targetTp≠RESTRICT_KEYWORD_TARGET인 행도 이미 포함돼 온다."

이번 **실측(533그룹 성공 응답 전수)은 이 서술과 어긋난다.**

| targetTp | 실측 건수(533그룹 성공 응답 기준) |
|---|---|
| MEDIA_TARGET | 533 |
| PC_MOBILE_TARGET | 533 |
| RESTRICT_KEYWORD_TARGET | 311 |
| NON_SEARCH_KEYWORD_TARGET | 311 |
| GENDER_TARGET | **0** |
| AGE_TARGET | **0** |
| GENDER_WEIGHT_TARGET | **0** |
| TIME_WEEKLY_TARGET | **0** |
| REGIONAL_TARGET | **0** |
| PERIOD_TARGET / AD_TAG / PLACE_ADGROUP_TAG | (Swagger enum엔 있으나 이번 실측 대상 밖 — 아래 참조) |

Swagger `targetTp` enum은 **12종**(ref 75 §2)인데, 이번에 533그룹 전수에서 실재로 관측된 것은 **4종뿐**이다. ref 75의 서술은 Swagger 구조 추론이었고 이번이 첫 실측이다 — **"같은 GET 콜에 이미 실려 온다"는 A5(MEDIA_TARGET)·A6(PC_MOBILE_TARGET)에 한해서만 사실**이고, GENDER/AGE/TIME_WEEKLY/REGIONAL 등은 이 계정의 그룹 어디에도 설정 자체가 없는 것으로 보인다(설정이 없으면 응답에도 안 실린다 — API 자체가 안 주는 게 아니라 **이 계정이 그 타입을 쓴 적이 없다**는 뜻일 수 있다. 이 구분은 [미상] — 별도 계정으로 대조해야 가른다).

## §4. A5·A6 원료 평가

**A5(매체 블랙리스트)**: 실물 있음. SHOPPING 48종(그룹당 0~46개, 아래 분포), 비-SHOPPING 9종(그룹당 0~7개). 그룹마다 다른 값을 갖는 진짜 축 — 등급 교차에 쓸 수 있다.

| 그룹당 블랙 개수 | SHOPPING(303그룹) | 비-SHOPPING(230그룹) |
|---|---|---|
| 0 | 11 | 15 |
| 2 | 47 | 5 |
| 4 | 50 | 1 |
| 5 | 173 | 0 |
| 6 | 4 | 162 |
| 7 | 1 | 47 |
| 10 | 3 | 0 |
| 46 | 14 | 0 |

**A6(PC/모바일 가중치, `PC_MOBILE_TARGET`)**: 실물은 있으나 **축으로서 가치가 사실상 없다**.

| (pc, mobile) | SHOPPING(303) | 비-SHOPPING(230) | 합계(533) |
|---|---|---|---|
| (True, True) | 301 | 224 | **525 (98.5%)** |
| (True, False) | 1 | 3 | 4 |
| (False, True) | 1 | 3 | 4 |

533그룹 중 525그룹(98.5%)이 "PC·모바일 둘 다 노출"이라는 단일값이다. 등급을 가를 변이가 4+4=8그룹밖에 없어 통계적으로 무의미하다 — **A6는 원료가 균일해서 교차분석 가치가 없다**는 것을 이번에 숫자로 확정했다(이전 매트릭스는 "구조사실만"로 남겨뒀던 것을 이번 실측이 구체화).

## §5. 정정 목록

| # | 항목 | 기존 서술 | 이번 실측 | 정정 |
|---|---|---|---|---|
| ⓐ | `backend/app/models.py:3197`(`NaverSearchTermDimDaily` docstring) | "매체 id는 **6자리**로 MEDIA_TARGET 블랙리스트의 media id와 자릿수가 같으나 동일성은 미확인" | col9(M) 37종 중 4자리 1종(`8753`)·5자리 6종·6자리 30종 — **6자리로만 구성되지 않음**. B(48종)는 5자리 7·6자리 41(4자리 없음). | "6자리"는 사실과 다르다(4~6자리 혼재). **동일성은 이제 확인됨**(§2). 코드는 이 작업 범위 밖이라 수정하지 않음 — docstring 정정은 별도 작업. |
| ⓑ | ref 75 §4-3 (`ADS_CENSUS_20260818.md:298`) | "응답에는 …GENDER_TARGET·AGE_TARGET·GENDER_WEIGHT_TARGET·TIME_WEEKLY_TARGET·REGIONAL_TARGET 등 … 행도 이미 포함돼 온다" | 533그룹 전수에서 위 5종은 **0건** | Swagger 구조 추론과 실측이 갈렸다(§3). MEDIA_TARGET·PC_MOBILE_TARGET은 서술대로 맞다. |
| ⓒ | ref 73 #12(`BAND_X_ALL_API_MATRIX_20260818.md:132,516`) — A4 `bidWeight` GET 표면 [미상](criterion 응답 vs targets blob) | "원 프로브 휘발로 재현 불가" | `targets_raw.jsonl`+`targets_other.jsonl`(533건 전수) 텍스트에 `bidWeight` 문자열 **0건**(grep 확인) | [미상]의 범위가 좁혀진다 — **`/ncc/targets`는 아니다**. 남은 후보는 `/ncc/criterion`뿐(ref 73 N1이 이미 "코드 호출 0건"으로 확정한 그 표면). |

## §6. [미상]으로 남긴 것

- **404 4그룹의 정체는 확인됐다**(더 이상 미상 아님, 기록 목적으로 남김): `naver_entity`(정본 로컬 원장)에서 `entity_id`로 조회하면 4건 전부 `status='deleted'`, 같은 `campaign_id`(`cmp-a001-02-000000010769985`) 하위 그룹이다. API가 삭제된 그룹에 404(권한 없음 코드 1018)를 주는 것으로 보인다 — **대행사 소유 여부는 이걸로 안 갈린다**(삭제=소유권과 별개 축).
- **`type` 1 vs 2의 의미**: SHOPPING 303건 중 type=2가 299·type=1이 4건, 비-SHOPPING 230건은 전건 type=2. Swagger 파라미터 설명에 이 값의 의미가 없어(로컬 캐시 문서 기준) **[미상]**. 이번 작업 범위에서 Naver API 새 호출·외부 문서 조회를 하지 않기로 했으므로 추정하지 않는다.
- **지역(col8)·매체(col9) 코드 자체의 "뜻"**: D-NAO-198이 이미 "안 함"으로 명시한 범위이고 이번 작업도 그 경계를 유지한다 — 코드 공간이 같다는 것과 코드가 무슨 매체를 가리키는지는 별개 질문이다. **[미상]**.
- **비-SHOPPING 블랙 9종이 SHOPPING 48종과 왜 다른가**: 7종은 공유(118495·118496·335738·612593·612594·805759·805760), 2종(`122876`·`335739`)은 비-SHOPPING에만 있다. 그룹당 개수 분포도 확연히 다르다(SHOPPING은 5개가 최빈값, 비-SHOPPING은 6~7개가 대부분). 캠페인 유형별로 운영자가 별도 정책을 쓴다는 정황은 있으나, **"왜"에 대한 1차 근거(운영 로그·변경 이력)는 이번 자료에 없다 — [미상]**.

## §7. 이 판정이 무엇을 열었나

동일성이 확정됐으므로, **이미 172일치 적재된 col9 성과(`naver_search_term_dim_daily`, 990,932행 중 dim_type='m' 204,773행)와 현재 블랙리스트(A5)를 소급 교차**할 수 있다 — "차단한 매체가 실제로 어떤 성과였는지"(과거 실적 유무·비용 규모)를 코드 공간 불일치 리스크 없이 조회할 수 있다는 뜻이다. 이 작업 자체는 §2-2에서 이미 최소 형태로 수행했다(블랙 쌍 대 비블랙 쌍 대조). 더 깊은 활용(운영 반영 등)은 이 계약의 범위 밖이다 — 전략·운영 권고는 Jino 몫.

## 커버리지 자백

**"전수 조사 완료"라고 쓰지 않는다.** 실제로 못 본 것:

- **SHOPPING 쪽**: 프로브 대상 307그룹은 "dim 테이블(172일 창)에 한 번이라도 등장한 적 있는 그룹"이다. 현재 `naver_entity`에서 `campaign_type='SHOPPING' AND status='on'`인 그룹은 **327개**인데, 그중 **36개는 이번 307그룹에 없다**(= 한 번도 col9 실적이 안 잡혔거나 172일 창 밖에서 생성된 신규 그룹) — **이 36개는 이번 프로브에 없다.**
- **비-SHOPPING 쪽이 훨씬 크게 빈다**: `campaign_type IN ('WEB_SITE','BRAND_SEARCH') AND status='on'`인 그룹은 **468개**인데, "최근 30일 `naver_ad_daily` 활동" 기준으로 뽑은 230그룹 중 실제 그 468개 안에 든 것은 **219개뿐**이고, **249개(53%)는 이번 프로브에 없다.** 즉 비-SHOPPING 쪽은 "최근 30일 지출이 없던(또는 그레인이 다른 경로로 잡히는) 그룹"이 절반 넘게 안 보였다.
- **삭제/일시정지 그룹**: `naver_entity`에서 `status≠'on'`인 adgroup은 전체 1,017건 중 222건(1,017−795) — 이번 프로브는 대부분 `status='on'` 위주였고(예외: 307그룹 중 16건은 on이 아님, 그중 4건이 404로 확인됨) 나머지 비-on 그룹은 조직적으로 훑지 않았다.
- **동일성 판정 자체는 위 미프로브 그룹과 무관하게 유효하다**(§2-2는 실제 송출 실적이 있는 창 전체 데이터로 검정했다). 다만 A5/A6 원료의 "그룹별 값 분포" 통계(§4)는 이번에 프로브한 533그룹 기준이지 전체 그룹(1,017 또는 795) 기준이 아니다.

## 부록 — 독립 재집계 대조표

아래 항목은 위임 배경에 제시된 「내 값」과 **별도로 새로 작성한 스크립트**(`my_recount.py`, 원 `analyze_targets.py`를 참고하지 않고 처음부터 작성)로 재계산한 값을 대조한 것이다. prod SQL도 별도로 작성한 쿼리(`my_pairs.sql`, 원 `q_pairs.sql`을 재사용하지 않고 `targets_raw.jsonl`에서 블랙 쌍을 새로 파싱해 INSERT문을 새로 생성)로 재실행했다.

| # | 항목 | 배경의 값 | 독립 재집계 값 | 일치 |
|---|---|---|---|---|
| 1 | SHOPPING 프로브 결과 | 200 OK 303 · 404 4 | 200 OK 303 · 404 4 (동일 4개 adgroup_id) | 일치 |
| 1b | 비-SHOPPING 프로브 결과 | 230건 전건 200 | 230/230 200 | 일치 |
| 2 | 실재 targetTp | MEDIA_TARGET·PC_MOBILE_TARGET·RESTRICT_KEYWORD_TARGET·NON_SEARCH_KEYWORD_TARGET 4종만, GENDER/AGE/GENDER_WEIGHT/TIME_WEEKLY/REGIONAL 533그룹 전수 0건 | 동일(합산 카운트: MEDIA_TARGET 533·PC_MOBILE_TARGET 533·RESTRICT_KEYWORD_TARGET 311·NON_SEARCH_KEYWORD_TARGET 311, 5종 전부 0) | 일치 |
| 3 | 블랙 media id 합집합 \|B\| (SHOPPING) | 48 | 48 | 일치 |
| 3 | col9 코드 \|M\| | 37 | 37 | 일치 |
| 3 | 교집합 | 17 | 17 | 일치 |
| 3 | B\M / M\B | 31 / 20 | 31 / 20 | 일치 |
| 4 | 블랙 쌍 개수 | 1,864건 | 1,864건(새로 파싱해도 동일) | 일치 |
| 4 | 블랙 쌍 중 dim 매칭 | 1건(imp 1·clk 0·cost 0) | 1건(imp 1·clk 0·cost 0) | 일치 |
| 4 | 같은 그룹집합 비블랙 쌍 | 197,835행 / imp 3,182,005 / clk 29,846 / cost 41,101,626원 | 197,835행 / imp 3,182,005 / clk 29,846 / cost 41,101,626원 | 일치 |
| 5 | 유일 반례 해소 | grp-…070451363 × 335738, 송출일 2026-07-21, editTm 2026-07-21T11:51:52.000Z | 동일(regTm 2026-07-20T08:15:29.000Z도 추가 확인) | 일치 |
| 6 | 자릿수 | B: 5자리7·6자리41 / M: 4자리1(8753)·5자리6·6자리30 | 동일 | 일치 |
| 7 | A6 (pc,mobile) | SHOPPING (T,T)301·(T,F)1·(F,T)1 / 비-SHOPPING (T,T)224·(T,F)3·(F,T)3 / 합계 525/533=98.5% | 동일 | 일치 |
| 8 | MEDIA_TARGET.target 필드(SHOPPING) | type 2:299·1:4, search "naver":299·[]:4, contents 비어있지 않음 1, white 전건 null, mediaGroup 전건 빈값 | 동일 | 일치 |
| 8 | MEDIA_TARGET.target 필드(비-SHOPPING) — 배경엔 미집계 | (미집계) | **type 전건 2(230/230)** · search "naver" 220·[]10 · contents 비어있지 않음 17(값은 전부 `["naver"]`) · white 전건 null · mediaGroup 전건 빈값 | 신규(대조 대상 없음) |
| 9 | 그룹당 블랙 개수 분포 | SHOPPING {0:11,2:47,4:50,5:173,6:4,7:1,10:3,46:14} / 비-SHOPPING {0:15,2:5,4:1,6:162,7:47} | 동일 | 일치 |
| 9 | 비-SHOPPING 블랙 합집합 9종 | "335739 포함" | 9종 = {118495,118496,122876,335738,335739,612593,612594,805759,805760}, SHOPPING과 겹치지 않는 것은 **122876·335739 2종** | 일치(배경이 예시로 든 335739 확인, 122876도 신규로 특정) |
| 10 | dim 테이블 창 | 204,773행·172일(2026-02-19~2026-08-18)·307그룹 / 전체 990,932행 | 동일 | 일치 |
| 신규 | ref 73 #12 bidWeight | (배경에 없음, 위임 지시 §5-ⓒ로 확인 요청) | targets_raw.jsonl+targets_other.jsonl 그레프 결과 `bidWeight` 문자열 **0건** | — |

**전건 일치.** 어긋난 항목 없음(항목 8-비-SHOPPING과 신규 bidWeight 항목은 배경에 없던 것을 이번에 새로 계산한 것이라 "대조 불가"이지 "불일치"가 아니다).

## 재현 좌표

- 원자료: `docs/references/data/77_targets_surface/`(`targets_raw.jsonl`·`targets_other.jsonl`·`col9_media.csv`·`blacklist_by_adgroup.json`·`q_pairs.sql`·`probe_targets.py`, 총 652KB)
- 독립 재집계 스크립트(스크래치, repo 밖): `my_recount.py`·`my_pairs.sql`(§2-2 SQL 재현용, prod에서 재실행 가능하나 임시 테이블만 만들고 쓰기 없음)
- DB 조회 원칙: `ssh -o BatchMode=yes sellc.ohitech.co.kr "sqlite3 -readonly ...` — 인라인 heredoc 금지
