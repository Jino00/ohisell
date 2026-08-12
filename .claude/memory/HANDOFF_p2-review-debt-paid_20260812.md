# 세션 인수인계: P2 적대 리뷰 부채 상환 — 세 곳이 「모르는 것」을 「아는 것」으로 세고 있었다

> 저장일시: 2026-08-12 14:5x KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 트랙: **PAO(파오)** — `docs/tracks/active/track_naver-ad-optimization.md` (D-NAO-174)
> 계약: `docs/PLAN_search-term-exclusion-list.md`
> 앞 세션: `HANDOFF_search-term-exclusion-P1P2-live_20260811.md`

## 1. 한 줄
직전 세션이 남긴 **P2 적대 리뷰 부채**를 갚았다. **FAIL — P1 3건**이 나왔고 전건 수리·배포·라이브 합격. 셋 다 같은 모양이었다: **모르는 것을 아는 것으로 센다.**

## 2. ⚠️ 새 세션이 가장 먼저 할 일
1. **8/13 오늘, 사슬 2단계가 닫힌다.** `ops_diary_entries` id=4371의 `outcome_json`에 `d1`이 생겼는지 확인하라. 안 생겼으면 08:35 잡을 의심하라 — **배선 자체는 이미 검증했다**(§4-③).
2. **8/17 첫 성적표 판정.** 그 전까지 「골프」가 `pending`인 것은 정상이다.
3. `git fetch` — 병행 세션이 매우 활발하다(이 세션 중 PR #288이 병합됐고, 그게 내 발견 하나를 이미 구조로 막아 놨다).
4. 이 인계 목록도 **실측 전엔 믿지 마라** — 직전 인계의 「prod 디스크 86.3%」는 실제로 88%였다.

## 3. 이 세션이 만든 것
| 커밋/PR | 내용 |
|---|---|
| PR **#289** (`bebb0ed`) | P1 3건 수리 + 회귀 테스트 6건 + 교훈 #270~#273 + D-NAO-174 |
| `f9f3119` (main 직접) | 2R 참고 채택 — P1-1 트립와이어를 예외 대신 **스파이**로 |

prod: 백엔드 `bebb0ed` (pm2 `ohisell-backend-8001`, pid 4160712) · 프론트 `index-D6QU6SaI.js` (스탬프 `bebb0ed`)

## 4. P1 3건 — 무엇이 틀렸었나

### ① 광고그룹 유형 조회 실패가 fail-open (`exclusion_survival.py`)
`get_adgroup_type`은 조회 실패도 `None`으로 돌려준다. 그 docstring이 *«모름»이지 «WEB_SITE 아님»이 아니다*라고 **직접 적어 뒀는데**, 호출부 조건이 `is not None and != WEB_SITE`라 **None을 「대조 가능」쪽에 넣었다.**

그러면 쇼핑 그룹인데 유형 조회만 500이 나도 → `restricted-keywords` 호출 → 그 API는 쇼핑에서 200/0건 → `missing`. **`fcf7b33`이 막으려던 바로 그 거짓 「우리 조치가 사라졌다」가 API 500 한 번으로 되살아난다.**

같은 파일 12줄 아래는 반대로 한다(`restricted-keywords` 실패 → `unknown`, 주석: *"모르는 것을 모른다고 표시하는 쪽이 fail-closed다"*). **두 조회 실패가 정반대 규율을 쓰고 있었다.**

### ② BEP 없으면 회수액이 «비용 절감 전액» (`search_term_scorecard.py`)
`margin_lost`를 0으로 두고 전액을 이익이라 신고했다. 같은 데이터에서 **BEP 유무만 다른데 +21,000원 / −119,000원으로 부호가 갈린다.**

층별 규율이 갈려 있었다 — 리스트 생성기는 `bep_unknown`으로 후보에서 빼고, 같은 파일 `_campaign_window`는 `profit = None if not bep`인데, **검색어 축만 fail-open**이었다.

★**라이브에서 실제로 터지고 있었다**: 배포 전 성적표는 `profit_recovered_judged: 3,052원`이었는데 배포 후 **`0` + `profit_unknown_count: 1`**. 유일한 판정 행(id=1 `아이패드종이필름`)이 BEP 없는 행이라, **그 3,052원은 이 결함이 prod에서 만들어낸 숫자**였다.

### ③ 화면이 «못 보는 몫»을 안 그린다 (`NaverAdExclusionList.tsx`)
백엔드 저자는 주석에 적어 뒀다 — *"조용히 묻히면 «감시되고 있다»는 착시가 생기므로 건수를 따로 낸다"*. **그런데 프론트가 `unverifiable`도 `unverifiable_note`도 한 번도 읽지 않았다**(배포된 번들 grep 0건으로 잡았다).

```
prod 11:45  API  → {monitored: 2, alive: 1, unverifiable: 1, healthy: true}
            화면 → "우리가 건 제외 1건 모두 걸려 있음"
```
감시 2건 중 1건이 미지인데 「모두」다. 그 1건이 하필 이 스프린트의 **유일한 실집행(「골프」)**이고, 쇼핑이라 API로 영영 되읽을 수 없다.

## 5. ★이 세션이 «측정»한 것

### ① 사슬 2·3단계는 배선돼 있다 — 8/13을 기다리지 않고 확인했다
prod **읽기전용 시뮬**(commit 없이 rollback)로 4371을 태워 봤다:
- `age >= 2` 조건이라 **d1은 8/13에 채워지는 게 맞다**(인계 정확).
- 데이터가 있는 창(08-11)을 넣으니 `d1 = {cost: 42971, clk: 36, conv: 122000, roas_c: 3.6463}` → `direction = good` → 시그니처 **`cmp-a001-02-000000008902804|search_term_exclude|weekday|summer|normal`**로 후보 생성 성립. **사슬 완주 가능.**
- 아침 수집(07:50~08:10)이 **전날**을 채우고 08:35 `diary_outcome`이 읽는 순서라 타이밍도 맞는다.

⚠️ 단 **d1은 캠페인 grain**이다(`_grain_and_target`이 `search_term`을 campaign으로 폴백). 캠페인 일 42,971원 vs 「골프」 약 1,126원/일 → **신호대잡음비 약 2.6%**. wisdom 시그니처가 캠페인 잡음을 학습할 위험이 있다. 검색어 grain 결과를 사슬에 넣는 것은 별건 설계.

### ② 프론트 테스트 65개가 «초록으로» 사라져 있었다
`npx vitest run`이 **266 passed**(인계 기준 331). 원인은 코드가 아니라 **iCloud Drive의 `node_modules` dataless eviction 487파일** — `@testing-library/dom/dist/event-map.js`가 빈 모듈로 로드돼 jsdom 5파일이 import 단계에서 죽었고, **합계 줄은 초록이었다.**

진단 순서가 핵심이었다: lock↔installed 버전은 4개 전부 일치했고, **`head`가 빈 출력인데 `wc -c`는 크기를 내놓는 모순**이 결정적 단서였다(메타데이터 있고 내용 없음 = dataless).

★**병행 세션이 같은 날 같은 사고를 독립적으로 만나 구조로 막았다**(D-CPP-44, PR #288): `frontend/scripts/test-census.mjs`가 「실패 0건」이 아니라 **「디스크의 테스트 파일 전부가 결과를 냈는가」**로 판정하고, `heal-node-modules.mjs`가 dataless를 실체화한다. **이제 `npm test`가 그 래퍼다 — `npx vitest run` 직접 호출은 가드를 우회한다.**

### ③ 오진 1건 (기록해 둔다)
`cost=0`을 보고 「사슬이 끊긴다」고 한 번 잘못 보고했다. 08-12 행이 아직 없는 상태에서 08-13을 시뮬한 **인공물**이었다. 데이터가 있는 창으로 다시 재서 정정했다 — 시뮬의 «오늘»만 옮기고 «데이터»는 안 옮기면 그 차이가 결과로 나온다.

## 6. 적대 리뷰 (PR 경계 의무 이행)
- **1R FAIL — P1 2건**(위 ①②). 변이 8개 중 3개 SURVIVED가 그대로 지적이 됐다. 1차 시도는 **600초 무진행으로 멈춰** INCONCLUSIVE 처리하고 범위를 좁혀 재실행했다(교훈 #123 — 미완주는 「발견 0건」이 아니다).
- ③은 리뷰어가 아니라 내가 라이브 API↔번들 grep 대조로 찾았다.
- **2R PASS — 잔여 0건.** 변이 5/5 KILLED. 리뷰어가 지적 하나를 스스로 보강했다(내가 준 MA 변이가 약해서 «수정 전 파일 통째 복원» MA2를 추가로 넣었다).
- **P2 트리아지**: 트립와이어→스파이 **채택**(`f9f3119`) · `profit_unknown_count` optional 주석 **기각**(`unverifiable`과 동일 관례) · 나머지 7건 **이월**(§7).

## 7. 남은 일 / 이월
1. **8/13 사슬 2단계 확인** · **8/17 첫 성적표 판정** → 그 결과로 09 나머지 6건 결정
2. **d1이 캠페인 grain이라 신호대잡음비 낮음** — 검색어 grain을 사슬에 넣는 설계는 별건
3. **리뷰어 P2 7건**: `POST /search-term/executions` 입력 검증 없음 · **원장 DELETE 라우트 부재**(잘못 들어온 행이 영구히 배너·성적표에 남는다) · `detect_new_exclusions` 그룹당 API 2회 무상한 · `camp_of` 미매핑 시 `campaign_id=""`가 원장·diary에 들어가 **wisdom 시그니처 오염** · `margin_lost` 음수 가능 · `build_scorecard` N+1(20~50건이면 200쿼리) · `record_execution` docstring 반환값 불일치
4. **콘솔 제외 42건이 원장 밖** — 쇼핑은 자동 발견 불가라 수동 입력 경로 필요
5. prod 디스크 **88%**(인계 86.3%보다 악화) · `.pm2/logs` 로테이션 없음
6. 앞 세션 이월 유지: `update_keyword_bid`의 `useGroupBidAmt:False` 상시 전송 · `[LEVER_MISMATCH]` 상시 표면 없음 · 01 갤럭시_지문방지_TPU 미조치 · 03 일예산 원복 · 대행사 통보

## 8. 상태·환경
- prod: `sellc.ohitech.co.kr` · pm2 **`ohisell-backend-8001`** · 프론트 `index-D6QU6SaI.js`
- ⚠️**Mac IP가 nginx 허용목록 밖**이라 무중단 배포의 vhost 검증이 계속 막힌다 → 이번에도 `--restart-legacy`(다운타임 약 50초). 브라우저로 prod 화면 직접 열람도 불가(403) — **번들 grep + 서버 로컬 curl이 대체 증거다.**
- ⚠️**GitHub Actions가 결제 정지**로 리포 전체에서 안 돈다(*"job was not started because recent account payments have failed"*). CI 빨강은 코드 신호가 아니다 → PR #289는 `safe_merge.sh --force`로 자백 병합.
- 테스트: `cd backend && python3 -m pytest -q`(**5,417 passed**, 약 3분) · `cd frontend && **npm test**`(27파일 354건, 인구조사 «전부 실행됨 ✓» 확인할 것 — `npx vitest run` 직접 호출 금지)
- 원격 조회 관용구: `ssh -o BatchMode=yes sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && .venv/bin/python -" < 스크립트.py`
  (파일을 scp 해서 절대경로로 실행하면 `app` 모듈을 못 찾는다 — stdin 방식이어야 cwd가 backend가 된다.)
- ★원격 스크립트는 `load_dotenv("/home/ubuntu/ohisell/backend/.env")`를 첫 줄에.
- ★**변이 주입 원복에 `git checkout --`를 쓰지 마라** — 커밋 안 한 수정이 같이 죽는다(이 세션에서 실제로 P1 수정 3건을 잃고 재작업했다). `cp <파일> /tmp/x.orig` → `cp /tmp/x.orig <파일>`.

## 9. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_p2-review-debt-paid_20260812.md 읽고 이어서 작업해줘
```
