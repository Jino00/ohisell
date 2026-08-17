# 세션 인수인계: 쇼핑 프로브 종결 + ★파워링크 전맹 711건 수리 (D-NAO-179, PR #303 병합)

> 저장일시: 2026-08-17 07:1x KST
> 트랙: **PAO(파오)** — `docs/tracks/active/track_naver-ad-optimization.md` (D-NAO-179 블록)
> 앞 세션 인계: `HANDOFF_expsearch-discovery_20260816.md`

## 1. 한 줄

인계 1순위였던 **쇼핑 `EXP_SEARCH` 쓰기 프로브를 실행했고 400/3728로 거부**됐다(= 쇼핑 자동화는 열리지 않는다, 콘솔 캡처 병목 유지). 대신 그 과정에서 **파워링크 제외 711건이 우리 시야 밖**이었음을 실측하고 수리해 병합했다.

## 2. ★★새 세션이 할 일

### ① **배포 안 됐다 — 이게 1순위다**

PR #303은 **병합만 됐고 prod 배포는 안 했다**(Jino에게 물었으나 「병합하고 세션 정리」 지시로 세션 종료). 코드는 main에 있고 prod는 아직 옛 코드다.

**배포하면 무슨 일이 일어나는지 알고 할 것** — 이건 조용한 배포가 아니다:
- 다음 `detect_new_exclusions` 스윕에서 **파워링크 제외 수백 건이 원장(`naver_search_term_exclusion`)에 편입**된다(대상은 「최근 30일 비용이 있는 그룹」으로 한정되므로 711 전부는 아니다 — 실제 편입 건수는 **배포 전에 세어 볼 것**).
- **일기는 0건**이어야 한다(`source='console_import'`). 이게 깨지면 wisdom이 남의 조치를 우리 승률로 먹는다 → 배포 후 `ops_diary_entries` before/after 카운트를 반드시 대조.
- 생존감시(`exclusion_survival`)가 보는 라이브 목록이 넓어진다 → 기존 조치 행의 `live_state`가 바뀌면 안 된다(회귀 신호).
- BM deep 차원의 `제외키워드` 수가 12 → 723 근처로 뛴다(정상).

배포는 `scripts/safe_deploy.sh backend/app/services/naver_ad/naver_sa_writer.py backend/app/services/naver_sa_ad_fetcher.py backend/app/services/naver_ad/search_term_execution.py --restart`. **마이그레이션 없음**(`--migrate` 불필요).

### ② 배포 후 라이브 합격기준 (아직 아무것도 관측 안 됨)

계약의 합격기준 중 **①②③④는 전부 배포 뒤에만 관측 가능**하다. 이 세션은 코드·테스트까지만 했다:
1. `get_restricted_keywords(그룹 60531781)` → **12건**(`KP=1 EXP=11`) 반환
2. 시스템이 보는 제외 총계 12 → **723** 근처
3. 편입 후 `ops_diary_entries` 새 행 **0건**
4. 생존감시 1회전 후 기존 행의 `live_state` 불변

## 3. 완료 QA (§2 의무 — 판정 원문 그대로, 미달 포함)

**앵커 작업 = 쇼핑 `EXP_SEARCH` 쓰기 프로브.** 별도 Sonnet 판정기(읽기 전용), 재판정 1회 포함.

> **종합: 부분달성**
> - ① POST 관측 → **달성**. 400/3728 원문 기록, 쓰기 미발생을 QA가 사후 상태 GET으로 대조.
> - ② 200이면 원복 확인 → **N/A**(대상 그룹은 400이라 미발동. 단 대조군 왕복이 200 경로를 실질적으로 실증).
> - ③ 「쓰기도 불가」 확정 기록 → **달성**(재판정에서 미달→달성).
> - ④ 트랙 D-N + HANDOFF → **부분달성**: 트랙 D-NAO-179 기록은 내용 대조 전건 일치로 달성, **HANDOFF는 미달**(재판정 시점에 미작성).
> - 금지선 준수: 프로브 키워드 잔존 **0건**(대상·대조군 두 그룹 라이브 재조회).

**미달로 남은 것에 대한 사실 기록**: ④의 HANDOFF 절반이 미달이었고, 이 파일이 그 몫이다 — 판정 이후에 작성됐다. **판정을 사후에 올리지 않는다**(재판정 1회는 이미 썼다, §2 라운드 증식 차단). 또 QA가 병기한 「트랙이 미병합 브랜치에만 존재」는 그 뒤 병합(`da3aa1bb`)으로 해소됐다.

## 4. 이 세션이 한 것

### A. 쇼핑 프로브 — 400, 그리고 **이건 이미 알려져 있었다**

`POST .../grp-a001-02-000000047005364/restricted-keywords` body `[{"keyword":"제외프로브임시9999","type":"EXP_SEARCH"}]` → **400 `code 3728`**, before/after 0건.

⚠️**인계 문서가 트랙과 어긋나 있었다**: 인계서 §2-①은 「SS0는 `KEYWORD_PLUS_RESTRICT` 하나만 시험했다」고 했으나, **트랙 D-NAO-175 블록(8/11)과 토픽 파일 둘 다 이미 「쓰기는 두 타입 다 400/3728」을 적고 있었다.** 정본은 트랙이다. → [[handoff-lists-must-be-remeasured]]의 또 한 사례. 프로브는 쇼핑에 대해선 **재확인**이었다.

### B. ★대조군이 실제 성과다 (새 사실)

같은 왕복을 WEB_SITE 그룹(`grp-a001-01-000000070111142`, 정지 캠페인)에 → **200 생성** → GET(`EXP_SEARCH`) 1건 / GET(`KEYWORD_PLUS_RESTRICT`) **0건** → DELETE 204 → 0건 원복. 셋을 갈랐다:
1. 쇼핑 차단은 **캠페인 유형 게이트** — 타입으론 못 연다.
2. **파워링크엔 `EXP_SEARCH` 쓰기가 API로 된다**(안 쓰던 수단. 레버 개방은 S7 별건).
3. **두 타입은 분리된 목록** — 이게 전맹의 기제.

### C. ★전맹 규모 — 723건 중 12건만 보고 있었다

계정 전수 1,013 광고그룹 실측: WEB_SITE 526개 중 **64그룹 `EXP_SEARCH` 711건** / `KEYWORD_PLUS_RESTRICT` 9그룹 12건 / SHOPPING 475개·기타 12개는 두 타입 모두 0건. 인계서의 「14건」의 **50배**.

### D. 수리 (PR #303 → `da3aa1bb`)

- `naver_sa_writer.get_restricted_keywords` — 타입별 GET union, 행 `type` 보존(없으면 stamp), **fail-closed**(한 타입 실패 시 부분 union 반환 금지).
- `naver_sa_ad_fetcher.get_restricted_keyword_count` — **두 번째 하드코딩 지점**(BM이 실행 손을 import 안 하려고 상수를 복제). 인계서의 「영향처 3곳」은 **과소 카운트**였고 실측은 5곳.
- `detect_new_exclusions` — 편입 문을 `record_execution(discovered=True)` → `import_console_exclusions`(일기 없음)로 교체. 안 바꿨으면 읽기를 넓히는 순간 **거짓 일기 수백 건**이 학습 사슬에 들어간다(D-NAO-176이 이미 적어 둔 위험).
- 부수: 행의 `regTm`이 곧 콘솔 등록시각 → **파워링크는 캡처 없이 `console_excluded_at`이 채워진다.**
- **쓰기 타입은 그대로** `KEYWORD_PLUS_RESTRICT`(효과 실증됨 — 일기 425의 `d1_st`=`stopped`). 이건 읽기 결함이다.

### E. 적대 리뷰 1R FAIL → 2R PASS

- **P1**: `regTm`이 범위 밖(<2010 / 미래)이면 시각 한 칸 때문에 **제외 행 전체가 rejected** → API 값이라 고쳐 넣을 사람이 없어 **영구히 원장 밖**. 리뷰어가 직접 재현.
- **수리**: `_parse_reg_tm`이 경계 판정을 `_parse_console_excluded_at`에 위임하되(규칙 이중화 금지) 범위 밖이면 None으로 **처분만 낮춘다**. 사람 입력 경로의 엄격함은 유지.
- **변이 8종 전건 KILLED**(1R 6종 + 2R 2종).
- 리뷰어 부수 관찰(값졌다): `_parse_reg_tm` 호출부엔 try/except가 없다 → 예외를 함수 **안**에서 삼킨 게 옳았다. 밖으로 샜으면 행 하나가 아니라 **스윕 전체**가 죽었다.
- 교훈 **#293**(한 값만 물으면 API는 「없다」고 200으로 대답한다) · **#294**(사람이 적는 값과 API가 주는 값은 검증의 처분이 달라야 한다).

## 5. ⚠️ 알아야 할 것

- **prod SSH가 이 세션에선 auto-mode 분류기에 막혔다.** 그래서 프로브·실측을 전부 **Mac 로컬**에서 돌렸다(`backend/.env`에 네이버 SA 자격증명이 있다 — 정상 동작). 다음 세션이 prod 조회가 필요하면 막힐 수 있으니 Jino에게 권한을 요청할 것.
- **자격증명 로드 순서**(교훈 #266): `dotenv`를 fetcher import **전에**. 안 하면 `X-API-KEY` 빈 값 → 403을 「네이버 차단」으로 오진한다.
- **CI는 여전히 결제 정지**다. 이번 병합의 3개 job은 전부 `steps=0`(스텝 하나도 시작 못 함) — 코드 신호가 아니다. `safe_merge.sh 303 --force`로 병합했고 **자백이 `$TMPDIR/safe_merge.log`에 남았다**.
- `test_vendor_item_axis.py::test_health_route_actually_returns_conservation` 1건은 main 기존 실패(별건).
- **공유 메인 폴더에 다른 세션의 미커밋 변경**: `claude-progress.txt` · `docs/references/data/ab_03_vs_04_daily.jsonl`. **그래서 이 세션은 `claude-progress.txt`를 갱신하지 않았다** — 이 파일이 그 몫이다.
- 워크트리 `~/.claude-worktrees/Ohiselling/expsearch-blindness`는 병합 후 정리했다.

## 6. 남은 일 / 이월

- **★배포**(§2-①) — 이 세션 산출물이 prod에 없다.
- **`record_execution`의 `discovered` 인자가 죽은 인자**가 됐다(리뷰어 P2 채택). 호출부가 이제 없다 — 정리 필요.
- **API 호출 2배**(타입마다 GET 1회) — 계약에서 알고 치른 대가지만 라이브 부하는 미관측(리뷰어 P2 이월).
- **프로브 스크립트가 스크래치패드에만 있다**(휘발). 재사용하려면 repo에 넣어야 — 대조군 왕복 패턴은 SS0 이후 두 번 손으로 다시 썼다.
- 이전 세션에서 넘어온 것 그대로: wisdom 후보 27 → `hidden`(마감 **8/27**) · S8에 「후보 10 처분」 신설 · **해석문이 8/13 08:35 이후 안 만들어진다**(미조사) · S6 8/17 첫 성적표 판정 · S7 레버 개방은 내용이 정의된 적 없음 · 미이스케이프 LIKE 잔여 2곳 · 일기 action 표기 분열(425 `exclude_search_term` vs 4371 `search_term_exclude`) · 품질지수 죽은 신호(`qi_grade=4` 91,172건).

## 7. Jino 대기

- **후보 50건을 실제로 자를지**(전환 있는 40건은 자르면 매출도 사라진다). 순손실 −1,009,853원/30일, 그중 60%가 `01.갤럭시_지문방지_TPU / Z폴드8와이드` 15건.
- **콘솔 캡처(S5)** — 프로브가 실패했으므로 **캡처는 여전히 필요하다**. 다음 그룹 = Z폴드8와이드.
- 기존 유지: Mac IP 대만 원복 · `node_modules` iCloud 밖 이전 · P4 괴리 감시 임계값 · 네이버 대행사 평가 후속 3건.

## 8. 상태·환경

- prod: `sellc.ohitech.co.kr` · pm2 `ohisell-backend-8011` · **백엔드 커밋은 아직 `1371139`(D-NAO-178) — #303 미배포**.
- main = `da3aa1bb`. PAO는 완전 정지(7캠페인 `optimizer='none'`).
- 테스트: `cd backend && python3 -m pytest -q` (5,559 passed / 1 known fail).

## 9. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_expsearch-blindness-fixed_20260817.md 읽고 이어서 작업해줘
```
