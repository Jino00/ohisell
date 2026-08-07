# 세션 인수인계: stale-closure-sync-selection (issue #235)
> 저장일시: 2026-08-07 13:40 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로(이번 세션 워크트리): `Ohiselling/.claude/worktrees/cool-driscoll-31712d`
  (브랜치 `claude/pensive-gauss-af7cdf` — **main에 병합 완료**, 워크트리 정리 가능)
- repo 루트: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 프론트 실행: `cd frontend && npm run dev` · 테스트 `npm run test` · 타입 `npx tsc -b`
  · lint `npx eslint . --max-warnings 54` · 빌드 `npm run build`
- prod: https://sellc.ohitech.co.kr (백엔드 `/api/*` 동일 호스트)
- 배포: `scripts/safe_deploy.sh` (프론트는 `--frontend`) · 병합: `scripts/safe_merge.sh <PR>`
- 환경변수: 이번 세션에서 손대지 않음(`backend/.env`)

## 2. 이번 세션 완료 목록

**issue #235 → PR #239 병합(`ebc17d4`), 이슈 CLOSED.** 트랙 작업 아님(단발 버그 수정).

- ✅ `frontend/src/pages/CommandCenter.tsx` — `syncAndLoad`가 `await syncRealtime()` 뒤
  **마운트 클로저** `from/to/account`로 재조회하던 것을 `selRef.current`로 교체(258행 부근).
  같은 파일 159·184·215·240행은 08-06에 이미 이 패턴이었고 **이 한 곳만 누락**돼 있었다.
- ✅ `frontend/src/pages/AdReport.tsx` — `reqSeq`/`selRef` 가드가 **아예 없었다**.
  `doFetch(f, t)` 코어로 분리해 둘 다 신설(응답 역순 도착 문제도 같이 닫힘) +
  `syncAndLoad`가 `selRef.current`로 재조회.
- ✅ `frontend/src/pages/staleSyncSelection.test.tsx` (신규) — 회귀 테스트 3건.
  **수정 전 코드에서 3건 전부 실패**하는 것을 stash로 확인함(통과만으론 가드 생존을 모름).
- ✅ `frontend/vite.config.ts` — `include`에 `src/**/*.test.tsx` 추가. jsdom은 파일 상단
  `// @vitest-environment jsdom` 도크블록으로 개별 전환(전역 jsdom은 느리기만 하다).
- ✅ `frontend/package.json`/`package-lock.json` — devDep 신설: `jsdom`,
  `@testing-library/react`, `@testing-library/dom`. **이 repo 첫 컴포넌트 테스트 인프라.**
- ✅ lint 래칫 유지 수선(아래 §5 참조) — `Date.now()` 렌더 밖으로, `ReportRow` 호이스트.
- ✅ 적대 리뷰 P1 자체 검출·수정(커밋 `6f38aa1`) — 빠른선택 `range()`가 from·to를
  같은 호출 시점에 쌍으로 계산.
- ✅ `.claude/memory/LESSONS_LEARNED.md` 교훈 **#166·#167** (main 커밋 `a2f0ce0`).
- ✅ failure-memory `failures.jsonl` 1줄 기록.

## 3. 확정된 결정사항

- **D-1(이 세션)**: 지연 콜백(동기화·폴링 완료 후)은 **완료 시점의 현재 선택**으로 재조회한다.
  `selRef.current`(CommandCenter·AdReport) 또는 `loadRef.current`(NaverOps·Dashboard).
  클로저 캡처 금지.
- **D-2**: `selRef`는 **마지막으로 「조회」된(적용된) 선택**을 담는다 — 화면에 그려진 데이터와
  같은 기준이어야 하기 때문. 인풋만 바꾸고 조회 안 한 값으로 데이터를 갈아치우지 않는다.
- **D-3**: `reqSeq`는 **응답 역순 도착만** 막는다. 이 버그(나중에 발행된 요청이 옛 인자를 듦)는
  seq가 더 커서 가드가 오히려 그것을 이기게 해 준다 — **다른 실패 모드다.**
- **D-4**: AdReport 빠른선택 버튼은 종전대로 **state만 바꾸고 조회하지 않는다**(「조회」가
  적용 시점). 즉시조회로 바꾸는 것은 동작 변경이라 범위 밖.
- **D-5**: 회귀 테스트는 **수정 전 코드에서 실패하는 것을 확인한 뒤에만** 가드로 인정한다.

## 4. 핵심 파일 목록

| 파일 | 역할 |
|---|---|
| `frontend/src/pages/CommandCenter.tsx` | 종합조망. `doFetch`/`selRef`/`reqSeq` 원본 패턴(159·184·215·240·258행) |
| `frontend/src/pages/AdReport.tsx` | 쿠팡 광고 리포트. 이번에 가드 신설 |
| `frontend/src/pages/staleSyncSelection.test.tsx` | 이 계열 회귀 가드. jsdom 컴포넌트 테스트의 **유일한 선례** |
| `frontend/vite.config.ts` | `.test.tsx` include + node/jsdom 이중 환경 |
| `frontend/src/pages/NaverOps.tsx` (190~196행) | 같은 계열 선례(`loadRef`, PR #213·#214). 주석에 배경 있음 |
| `frontend/src/pages/Dashboard.tsx` (376~390행) | 같은 계열 선례(`fetchAllRef`, 08-05) |
| `backend/app/routers/sync.py:190` | `POST /api/sync/realtime` — 전 채널 주문+네이버 SA+Meta 동기 |
| `.github/workflows/ci.yml:72` | lint 래칫 `--max-warnings 54` |

## 5. 알려진 이슈 / 주의사항

- ★**`/api/sync/realtime`은 약 30초짜리 동기 엔드포인트다** — prod 실측 curl 3회
  **32.1 / 29.9 / 32.9초**, 브라우저 5회 27.4~31.5초 (2026-08-07 13:0x KST, http 200).
  마운트 시 무조건 돌므로 **모든 페이지가 30초짜리 "대기 창"을 연다.** 이 창 안에서 발사되는
  지연 콜백은 전부 stale closure 후보다.
- ★**잔여 스윕 완료 — 이 두 페이지가 마지막이었다.** `syncRealtime` 사용처 전수:
  `Dashboard.tsx`=`fetchAllRef` 기적용 · `NaverOps.tsx`=`loadRef` 기적용 ·
  `Orders.tsx`=`syncRealtime().catch(()=>{})` fire-and-forget이라 재조회 없음 → 구멍 없음.
- ★**lint 래칫 함정(교훈 #166)**: stale closure를 고치자 react-hooks 컴파일러가 AdReport를
  **분석하게 되면서** 원래 있던 `purity` error 2건 + `static-components` warning 1건이 처음
  드러났다(baseline은 error 0). "내가 만든 에러"가 아니라 "내가 드러낸 에러"다. 셋 다 고쳐
  **error 0 / warning 54**로 되돌렸다 — 상한은 올리지도 내리지도 않았다.
- ★**브라우저 자동화 함정**: `a.click(); b.click()`을 한 JS 태스크에 넣으면 React가 그 사이
  flush를 안 해서 **없는 stale closure 버그가 보인다**(내가 실제로 한 번 오진했다).
  실제 사용자 클릭은 이벤트마다 flush된다 → `applyAccount`/`applyQuick`의 클로저는 정상.
- **codex 교차 리뷰는 이번 건에 한해 Jino가 스킵을 결정했다** (2026-08-07 14:34 KST
  "이번에 codex 소급리뷰는 스킵하자"). 되살리지 말 것 — 이월이 아니라 **종결**이다.
  경위: 3렌즈 전부 usage limit(`try again at Aug 9th, 2026 4:16 PM`)으로 EXIT:1,
  게이트 INCONCLUSIVE → Opus 1기 적대 리뷰로 대체(P1 1건 검출·수정 `6f38aa1`).
  **미완주 렌즈를 "findings 없음"으로 세지 않았다**(교훈 #123).
- ✅**prod 배포 완료** (2026-08-07 14:30 KST, 스탬프 `d468745` = main tip).
  ★가는 길에 CAS가 **두 번** 거부했고 **두 번의 성격이 달랐다** — 이게 이번 건의 교훈이다:
  ①`aa9c425`(`claude/cost-unknown`) — 병합해 보니 `git diff HEAD^1 HEAD`가 **완전히 비었다**.
    그 브랜치 내용은 이미 전부 main에 있었다(내용 무손실). **CAS는 커밋 신원을 보는데 나는
    거기서 "내용이 사라진다"를 추론했다 — 대조하지 않고.** 잘못된 근거로 옳은 행동을 했다.
  ②`a8c1412`(`claude/rocket-1p-pnl-onscreen`, PR #240) — 이건 **진짜로 달랐다**
    (`Rocket1PRevenue.tsx` +172행 등 main에 없음). 덮었으면 실제로 지워졌다.
  → **CAS 거부 시 절차: 먼저 `git diff <내HEAD> <prod스탬프> -- frontend/src`로 내용을 대조하라.**
    비어 있으면 신원 문제일 뿐이고, 비어 있지 않으면 진짜 clobber다.
- ⚠️(다른 세션 이월, 손대지 않음) prod `backend/.env` 38행에 따옴표 없는 iCloud 경로 →
  `. .env` 시 파싱 에러.

## 6. 다음에 할 작업 (미완료)

- [x] ~~prod 프론트 배포~~ — 2026-08-07 14:30 완료. PR #240 병합(14:28) 직후 merge→build→deploy.
      **라이브 합격 증거(prod, performance timing)**:
      · CommandCenter — sync 30.4s, 클릭 `from=2026-07-09` → 완료 직후 재조회도 `from=2026-07-09`
      · AdReport — sync 31.7s, 조회 `date_from=2026-01-01` → 완료 직후 재조회도 `2026-01-01`
- [x] ~~codex 소급 리뷰~~ — **Jino 스킵 결정 (2026-08-07 14:34)**. 다시 열지 말 것.
- [ ] (선택) 워크트리 `cool-driscoll-31712d` 정리 — 브랜치는 병합 완료.

**→ 이 인계에 남은 필수 작업은 없다.** 참고용 기록으로만 읽으면 된다.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_stale-closure-sync-selection_20260807.md 읽고 이어서 작업해줘
```
