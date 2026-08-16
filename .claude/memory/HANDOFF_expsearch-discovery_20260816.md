# 세션 인수인계: D-NAO-178 합격 확정 + ★`EXP_SEARCH` 제외 타입 발견 (프로브 미실행)

> 저장일시: 2026-08-16 23:2x KST (세션 실작업은 8/13~8/14)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 트랙: **PAO(파오)** — `docs/tracks/active/track_naver-ad-optimization.md`
> 앞 세션 인계: `HANDOFF_d1st-shipped_20260813.md` (D-NAO-178 구현·배포 상세는 그쪽)

## 1. 한 줄

D-NAO-178의 **라이브 합격기준이 실제로 관측됐고**(4371의 `d1`≠`d1_st`), 그 뒤 「쇼핑은 API로 못 자른다」를 Jino가 되물어서 파고들다 **`EXP_SEARCH`라는 두 번째 제외 타입**을 찾았다. **Jino 승인까지 받고 쓰기 프로브는 실행 안 한 채 인계한다.**

## 2. ★★새 세션이 할 일

### ① 쇼핑 `EXP_SEARCH` 쓰기 프로브 — **Jino 승인 완료 · 미실행**

Jino 승인 원문: *"진행하고 파워링크 EXP_SEARCH 전맹은 별건으로 고차자"*(2026-08-14 10:00).

**왜 하나**: 되면 후보 50건(순손실 **−1,009,853원/30일**)이 콘솔 수동 → **API 자동**으로 바뀌고, Jino 캡처 병목이 사라진다. S7 「레버 개방」의 쇼핑 분기가 열린다.

**설계(SS0와 동일한 가역 왕복)**:
- 대상: 쇼핑 광고그룹 1개(예: `grp-a001-02-000000047005364` 버디필름 — 두 타입 모두 0건 확인됨)
- 검색어: `제외프로브임시9999` 같은 **실재하지 않는 문자열**(실제 노출 영향 0)
- 순서: `POST /ncc/adgroups/{ag}/restricted-keywords` body `[{"keyword":…, "type":"EXP_SEARCH"}]`
  → GET 재조회(`?type=EXP_SEARCH`) → DELETE → GET 재조회로 **원복 확인**
- `try/finally`로 원복 보장. 400이 나오면 **그것도 답이다**(쓰기 미발생 — SS0의 `KEYWORD_PLUS_RESTRICT` 400과 같은 모양).
- writer의 POST 관례를 그대로: `requests.post(fetcher.BASE_URL + path, headers=fetcher._headers(path, method="POST"), json=body, timeout=30)`.
  ★`naver_sa_writer.add_restricted_keywords`는 `_RESTRICT_TYPE`(=`KEYWORD_PLUS_RESTRICT`) **하드코딩**이라 그대로는 못 쓴다.

**⚠️Jino가 승인한 것의 범위**: 이 프로브는 **D-NAO-13 금지선**(`optimizer='ours'` 아닌 캠페인에 쓰기 금지)의 예외로 진행하는 것이다 — 현재 7개 캠페인 전부 `optimizer='none'`. Jino가 그 사정을 듣고 승인했다. 프로브 결과는 change_log에 남지 않으므로(직접 호출) **트랙·HANDOFF에 반드시 기록**할 것.

### ② 파워링크 `EXP_SEARCH` 전맹 수리 — 별건(Jino 지시)

`naver_sa_writer.get_restricted_keywords()`가 `{"type": _RESTRICT_TYPE}` **한 값 고정**이라 `EXP_SEARCH` 제외를 **한 건도 못 본다.** 라이브 실측으로 파워링크 3개 그룹에서만 **14건**이 시야 밖에 있었다.

**영향 3곳**(착수 전 전수 확인할 것 — 아래는 코드 읽기 기반 추정):
1. `detect_new_exclusions` — 콘솔에서 사람이 자른 `EXP_SEARCH` 제외를 장부에 못 넣는다(「골프」 편입과 같은 종류의 누락).
2. `verify_search_term_exclusions`(생존감시) — `EXP_SEARCH`로 등록된 제외가 지워져도 모른다.
3. `add_restricted_keywords`의 before/after 재조회 — 같은 키워드가 `EXP_SEARCH`로 이미 있으면 중복 가드가 못 잡는다.

**규모가 3파일+이면 앵커를 쓸 것**(전역 §1). 읽기 확장이라 승인은 불필요.

## 3. D-NAO-178 합격기준 — 최종 관측 (2026-08-16 23:1x 실측)

| # | 판정 | 관측 |
|---|---|---|
| ① 4371 정합 | **✅달성** | `d1`={cost 43084…} **불변** + `d1_st`={window 2026-08-12, status **stopped**, cost_total **0**, required_sources [shopping], by_source.shopping.present **true**, matched_terms 0}. **d1(43,084) ≠ d1_st(0)** — 오귀속 제거가 값으로 증명됐고 8/13 예행값과 정확히 일치 |
| ② 거짓 0 부재 | **✅달성** | `d1_st` 기입 **2건**(425·4371) 전수 — 필요 source가 `present:false`인데 `stopped`인 행 **0건**. 8/13엔 분모가 0이라 vacuous였는데 이제 아니다 |
| ③ 오염 정지 | **부분달성** | wisdom 후보 27 `last_seen_at 2026-08-13 08:45` **불변**(good 1/bad 0) — 8/14 08:45 harvest가 실제로 돌았는데(`wisdom: ok` 로그) 후보를 하나도 안 건드렸다 = skip 라이브 작동. 다만 `skipped_search_term_grain` 카운터 자체는 `_stage`가 'ok'만 로깅해 **로그로 확인 불가** |
| ④ 기존 불변 | **✅달성** | 8/13 QA에서 baseline 4,156행·8,311키 diff 0건 |
| ⑤ 해석문 통과 | **판정불능** | **최신 해석문이 2026-08-13 08:35 KST에서 멈춰 있다** — 8/14·8/15·8/16 3일째 새 행이 없다. §5 참조 |

**종합: 부분달성(①②④ 달성 / ③ 부분 / ⑤ 판정불능 / 미달 0건).** 8/13 QA 대비 ①②가 판정불능 → 달성으로 올라갔다.

## 4. 이 세션이 발견한 것 (전부 라이브 실측)

### ★A. `EXP_SEARCH` — 두 번째 제외 타입이 있다

공식 스펙(`naver/searchad-apidoc` gh-pages `assets/json/ncc-heroes-ncc.json`, 오늘 다운로드):

```
AdgroupRestrictKwd.type: enum = ['KEYWORD_PLUS_RESTRICT', 'EXP_SEARCH']
  "Type of the keyword. default value is 'KEYWORD_PLUS_RESTRICT'"
```

SS0 프로브(2026-07-21)는 `KEYWORD_PLUS_RESTRICT` **하나만** 시험했고, 400 에러 메시지도 유형을 특정해 거절한다(*"does not support **keyword plus** impression restricted keywords"*). SS0 문서 자신이 그 한계를 미결로 남겼는데(*"다른 restrict type 지원 가능성은 프로브가 배제하지 못한다"*), 그 enum이 이제 확인됐다.

**읽기 실측 결과**:

| 그룹 | `KEYWORD_PLUS_RESTRICT` | `EXP_SEARCH` |
|---|---:|---:|
| 쇼핑 / 버디필름(콘솔 43건 있음) | 200 / **0건** | 200 / **0건** |
| 쇼핑 / Z폴드8와이드 | 200 / **0건** | 200 / **0건** |
| 파워링크 `…31186688` | 1건 | **6건** |
| 파워링크 `…32245671` | 0건 | **2건** |
| 파워링크 `…37423354` | 0건 | **6건** |

- **쇼핑은 두 타입 다 0건** → [[shopping-exclusion-invisible-to-api]] 유지. 타입 문제가 아니었다.
- **파워링크엔 `EXP_SEARCH` 제외가 실재**(등록일 2025-04~05, 예: `'아이폰커버'` `'강화유리필름가격'` `'지문방지필름게임'` `'Z폴드6정품보호필름'`) → §2-②의 전맹.
- **★확장검색 제외 «수단»은 API에 있다** — 앞서 「파워링크 확장검색어는 전환을 몰라 판정 못 한다」고 했는데, **자를 수단은 있었다.** 판정 축만 만들면 자동화 가능한 구조다.

### ★B. 파워링크 검색어별 전환 불가 — 공식 1차 출처로 확정 (커밋 `f886f69e`)

`reportTp` 13종이 성과·전환 **6쌍 + 고아 1**인데 짝이 없는 유일한 리포트가 `EXPKEYWORD`다. 실시간 `/stats`도 우회로가 아니다(`id`는 엔티티 ID 전용·`breakdown`에 검색어 축 없음). 상세는 **ref 36 §7**(신설)·`docs/PLAN_naver-ad-searchterm-ss.md` §0.5.

**★그런데 이게 어제 내 프레이밍을 뒤집었다**: 「`powerlink_undecidable` 519만원이 판정 불가로 새고 있다」는 **틀렸다.** 파워링크 확장검색 버킷은 30일 비용 6,259,486원 → 전환 20,014,620원 = **ROAS 3.20**으로 계정에서 **가장 잘 버는 구간**이다(파워링크 전체 2.72 · BEP 1.711). 자르면 손해다.

### ★C. 후보 50건의 «진짜» 금액 = 순손실 −1,009,853원

「광고비 223만원」이 아니라 **공헌이익(매출÷BEP) − 광고비 = −1,009,853원 / 30일**이다(후보들이 전환 130건·매출 208만원을 내고 있다). 파레토가 극단적:

| 그룹 | 건 | 순손실 | 누적 |
|---|---:|---:|---:|
| 01.갤럭시_지문방지_TPU / **Z폴드8와이드** | 15 | **−604,633** | 60% |
| 09.기타상품 / 01.버디필름 | 5 | −84,789 | 68% |
| 01.갤럭시_지문방지_TPU / Z폴드8울트라 | 5 | −77,636 | 76% |

- **Z폴드8 3종 적자가 −578,822원 → −722,007원으로 확대**됐다(미결 목록의 「8/16 재측정」 항목 — 방향은 이미 나왔다).
- 전환 0건 10건(247,068원)은 매출 소실 없이 끊을 수 있다.
- 실행 목록 산출물: 스크래치패드 `검색어제외_실행목록_20260814.md`(그룹별 + 콘솔 붙여넣기용 검색어 목록). ⚠️**세션 스크래치는 휘발된다 — 필요하면 재생성**(API 1회 호출 + 집계).
- ★**후보 50건 전부 `whitelisted: true`다.** 화이트리스트 토큰이 우리 상품명(「폴드」「필름」)에서 나와 사실상 전건에 걸린다. **자동 발사에서는 차단 사유지만 사람 리스트에서는 표시만 한다**(코드 주석: *"01 지문방지 계열이 정확히 그 모양이다"*). 즉 이 50건은 설계상 사람만 판단할 수 있는 것들이다 — 자동화해도 이 가드는 남는다.

### ★D. wisdom 후보 10 — 이미 `rejected`로 봉인된 거짓 판정

```
후보 10  cmp-a001-01-…10236310|exclude_search_term|weekday|summer|normal
         status=rejected · good 0 / bad 1 · source_entry_ids=[425]
```

diary 425의 **캠페인 grain `d1`**(cost 7,696·roas 0.0)로 「검색어 제외는 bad」를 배우고 판사가 영구 기각했다. 그런데 그 조치의 진짜 성적은 `d1_st` = **`stopped`(성공)**다. 후보 27(good 1, 과대평가)과 **정반대 방향의 같은 결함**이다.

**문제**: `rejected`는 터미널이라 S8에서 재채점해도 안 살아난다. 27을 `hidden`으로 두기로 한 이유가 「S8 재채점 여지 보존」이었는데 **10은 그 문이 이미 닫혔다.** → **S8 설계에 「이미 봉인된 거짓 판정의 처분」 항목 신설 필요**(현재 계약에 없다).

## 5. ⚠️ 알아야 할 것

- **★해석문이 2026-08-13 08:35 이후 3일째 안 만들어진다.** 8/14 로그는 `naver diary_reflection: {'outcome_backfill': 'ok', 'daily_reflection': 'ok'}`로 정상인데 `ops_diary_entries`에 새 `observe/daily_reflection` 행이 없다. **가설**: 전날 집행 이벤트 0건 → `build_reflection`이 `{"skipped":"no_entries"}` 반환, `_stage`가 그걸 'ok'로 기록. **정상 동작일 수 있으나 확인 안 됨** — 합격기준 ⑤가 이것 때문에 판정불능이다. 다음 세션이 `build_reflection` 반환값을 로그나 읽기 실행으로 확인할 것.
- **자격증명 로드 함정(교훈 #266 재발)**: prod에서 진단 스크립트를 돌릴 때 `dotenv`를 **fetcher import 전에** 로드해야 한다. 모듈이 import 시점에 `os.getenv`로 상수를 굳히기 때문. 안 하면 `X-API-KEY`가 빈 값으로 나가 **403**이 뜨고, 그걸 「네이버 차단」으로 오진하기 쉽다(이번 세션에서 실제로 한 번 겪었다).
  ```python
  from dotenv import load_dotenv
  load_dotenv("/home/ubuntu/ohisell/.env")
  from app.services import naver_sa_ad_fetcher as fetcher   # ← 반드시 이 순서
  ```
- **fetcher import 경로**: `from app.services import naver_sa_ad_fetcher`(`app.services.naver_ad`가 아니다).
- **prod DB**: `/home/ubuntu/ohisell/backend/ohisell.db`, 읽기는 `sqlite3.connect("file:…?mode=ro", uri=True)`. **인라인 heredoc은 따옴표가 벗겨진다 — `scp` 후 실행.**
- **prod는 Basic Auth**: `curl -u "$(cat ~/.ohisell_prod_auth)"`. 무인증은 401. **무중단 배포가 풀렸다**(8/13 실증, 0초).
- **API 라우터 prefix는 `/api/naver/ad/...`**(`/api/naver-ad/...` 아님 — 이번 세션에서 404로 한 번 헛짚었다).
- `test_vendor_item_axis.py::test_health_route_actually_returns_conservation` 1건은 **main에서도 매일 실패**하는 기존 실패(별건).
- **GitHub Actions가 결제 정지로 job을 시작조차 못 한다** — CI 빨강은 코드 신호가 아니다.
- **Mac 로컬 시각이 대만(UTC+8)**이다. `safe_deploy`/`safe_merge` 로그 시각은 KST보다 1시간 이르다.
- ⚠️**공유 메인 폴더에 다른 세션의 미커밋 변경이 있다** — `claude-progress.txt`(04 자동운영 감사 D+15 항목)·`docs/references/data/ab_03_vs_04_daily.jsonl`·`HANDOFF_harness-anthropic-alignment_20260814.md`. **그래서 이 세션은 `claude-progress.txt`를 갱신하지 않았다**(남의 변경과 섞이므로). 이 인계 파일이 그 몫을 대신한다.

## 6. Jino 대기

- **콘솔 캡처(S5)**: `01.갤럭시_지문방지_TPU / Z폴드8와이드`가 **순손실의 60%**(15건·−604,633원)로 최우선. 다음 `S26울트라`·`Z폴드8울트라`. 안내서 `docs/HOWTO_console-exclusion-export.md`.
  ★단 §2-①의 프로브가 성공하면 **캡처 자체가 불필요해질 수 있다** — 프로브를 먼저 돌리는 게 순서상 이득.
- **후보 50건을 실제로 자를지·얼마나 자를지**는 Jino 판단(전환 있는 40건은 자르면 매출도 사라진다).
- 기존 유지: Mac IP 대만 원복 · `node_modules` iCloud 밖 이전 · P4 괴리 감시 임계값 · 네이버 대행사 평가 후속 3건.

## 7. 남은 일 / 이월

- **wisdom 후보 27 → `hidden`** (D-NAO-178 범위 5건 중 마지막, **마감 8/27** TTL 숙성). 순서 제약은 이미 충족.
- **S8에 「후보 10 처분」 신설**(§4-D).
- **S6** 8/17 첫 성적표 판정(「골프」) — 사전 매출 0원이라 `margin_lost`가 구조적으로 0 클램프(D-NAO-175 ⑤).
- **S7** 레버 개방 — ★**내용이 정의된 적이 없다**(문서에 `| 레버 개방 안건 | S7 |` 한 줄이 전부). 착수하려면 「어느 채널의 어느 레버를 어떤 조건에서」부터 써야 한다. 실행 루프 §7 체크리스트상 X1a~X3 구현은 **전부 `[x]`**이고, 미완은 `X0-2 카나리 지정(Jino)` 하나에 전부 걸려 있다(Jino 7/10: *"카나리 캠페인은 프로그램 완성되면 정하자"*).
- **이스케이프 없는 LIKE 잔여 2곳**: `routers/orders.py:47-48` · `services/product_connection_map.py:117-118` — 둘 다 검색창 입력이라 결과를 넓힐 뿐 판정을 뒤집지 않는다(P2, 교훈 #291).
- **일기 action 표기 분열이 실물 확인됨**: 425=`exclude_search_term` vs 4371=`search_term_exclude`. 승률이 두 갈래로 쌓인다.
- **품질지수 죽은 신호** — `naver_entity` 91,172개 전부 `qi_grade=4`. 공식 1차 문서 대조 필요(추정 금지).
- 생존감시 `breached`에 `source`·`console_excluded_at` 없음 · 콘솔 「유형(일치)」 축 미반영 · 그룹당 70건 상한 PAO 설계 미반영 · `ss_lane._upsert_exclusion` cycle 규칙 두 벌 · PR#289 P2 7건 · `safe_deploy` 백업 폴더명이 Mac 로컬 시간.

## 8. 상태·환경

- prod: `sellc.ohitech.co.kr` · pm2 **`ohisell-backend-8011`** · 백엔드 커밋 `1371139`(D-NAO-178) · alembic head `cs1exat2when3`.
- **PAO는 완전 정지**: 7개 캠페인 전부 `optimizer='none'` · `auto_operate=0`(8/14 실측).
- 로컬 main = `f886f69e` + 이 인계 커밋. 병행 세션이 활발하다 — 착수 전 `git fetch && git log --oneline -10`.
- 테스트: `cd backend && python3 -m pytest -q`(5,511 passed / 1 known fail) · `cd frontend && npm test`(★`npx vitest run` 직접 호출 금지).
- 변이 원복은 `cp`로. **`git checkout --` 금지.** 배포 락 충돌 시 `--steal-lock` 쓰지 말고 대기.
- 번호는 `scripts/next_ids.sh`(이 세션은 교훈 #290·#291 수령, D-NAO는 178 유지 — 새 결정이 아니라 그 구현이므로).

## 9. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_expsearch-discovery_20260816.md 읽고 이어서 작업해줘
```
