# 세션 인수인계: cmd_login RG 세션 프로브 잔여 호출부 정리
> 저장일시: 2026-08-03 20:10 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> **트랙외 작업**(쿠팡 Wing 페처 유지보수). 주력 트랙 `네이버 SA 광고 최적화`는 이 세션에서 착수하지 않았다.

## 1. 프로젝트 위치 및 환경

- 로컬 경로: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 이 세션 워크트리: `.claude/worktrees/vigorous-wing-ca8cf8` (브랜치 `claude/vigorous-wing-ca8cf8`, HEAD `6ef4196`)
- 테스트: `/opt/homebrew/bin/python3 -m pytest -q` (backend/ 에서). **`backend/.venv`는 깨져 있다**(`.venv.broken-py314`) — 시스템 python3.14 사용
- 페처 런타임: `~/.ohisell/venv/bin/python` + `~/.ohisell/tools/*.py` (iCloud 밖 로컬 사본)
- 페처 로그: `~/.ohisell_wing_fetcher.log`
- 페처 설정: `~/.ohisell_wing_fetcher.json`(WING1/오픽스) · `~/.ohisell_wing2_fetcher.json`(WING2/오하이테크) — **Mac 로컬에만 존재, repo에 없음**
- prod: `https://sellc.ohitech.co.kr`
- 주요 환경변수: `OHISELL_WING_CONFIG`, `OHISELL_LOCAL_HOME`

## 2. 이번 세션 완료 목록

**PR [#188](https://github.com/Jino00/ohisell/pull/188) — 병합 완료(main `6ef4196`, 커밋 `aaf4fc5`) · 배포 완료 · OK 경로 라이브 확인**

- ✅ `tools/wing_browser_fetcher.py` — `cmd_login`의 VS→RG 연결 프로브를 보수적 bool(`_rg_session_ok`) → **3값 판정**(`_rg_session_verdict_confirmed`)으로 교체
  - `AUTH`(로그아웃 확증)일 때만 로그인 대기·`rc=5`
  - `UNKNOWN`이면 **로그인을 요구하지 않고 `rc=0`**, 단 "확인됨"이라고 **주장도 하지 않고** WARNING을 남긴다
  - 프로브 `goto` 예외도 `UNKNOWN`으로 접되, **이미 AUTH를 확증한 뒤** 로그인 대기 중 터진 예외는 판정을 지우지 않는다(`if rg_verdict != _PROBE_AUTH:`)
- ✅ `tools/wing_browser_fetcher.py` — 사문 `_rg_session_ok_confirmed`(호출부 0) **삭제**
- ✅ `tools/wing_browser_fetcher.py` — `_rg_session_ok` docstring 정정: 유일한 호출부는 `_rg_login_wait`이고 **거기서만** 보수적인 게 맞다(그 루프에서 `False`는 '만료 선언'이 아니라 '아직 확증 못 했으니 더 기다린다')
- ✅ `backend/tests/test_wing_login_rg_probe.py` — 기존 테스트를 3값 계약으로 이전 + **신규 5건**
- ✅ `.claude/memory/LESSONS_LEARNED.md` — **교훈 #98** 추가
- ✅ 배포: `tools/install_local_runtime.sh --files-only` → CAS `ancestor` 통과, 매니페스트 기록
- ✅ 데몬 재기동: `com.ohisell.wing` / `wing2`

## 3. 확정된 결정사항

- **UNKNOWN은 로그인을 요구하지 않는다.** 근거(비용 비대칭): 오보는 사람에게 헛로그인을 시키고 멀쩡한 세션에 `rc=5`를 남기지만, 놓친 로그아웃은 이어지는 `rg`가 재시도 가능한 실패로 회수한다.
- **원래 마스킹 구멍(VS 성공이 RG 만료를 가림)은 그대로 막혀 있다.** 2026-07-27 실측된 로그아웃 형태(정산 goto → xauth 리다이렉트)는 **오리진 이탈 = AUTH 확증**이라 UNKNOWN으로 오지 않는다.
- **`rc=5`(RC_RG_LOGIN_REQUIRED)는 AUTH일 때만.** 계약 변경 — 종전엔 프로브 예외도 `rc=5`였다(`test_probe_exception_is_not_fatal_to_vs_result` → `test_probe_exception_is_unknown_not_logout`으로 대체).
- **`_rg_login_wait`은 보수적 bool을 계속 쓴다.** 그 루프는 "로그인이 됐는가"를 폴링하므로 UNKNOWN에 True를 주면 로그인 안 된 세션에 마커를 저장하고 빠져나온다.
- **PR #188 병합은 Jino 승인**("다른 작업에 영향없이 병합하자", 16:57). 배포·라이브 확인도 Jino 승인(20:0x 이전 "배포하고 라이브 확인해줘").

## 4. 핵심 파일 목록

| 파일 | 역할 |
|---|---|
| `tools/wing_browser_fetcher.py` | Wing 페처 본체. `cmd_login`(≈794행)·`_rg_session_probe`(≈1871행)·`_rg_session_verdict_confirmed`·`_rg_session_ok`·`_do_rg_run` |
| `backend/tests/test_wing_login_rg_probe.py` | cmd_login RG 프로브 계약 고정(3값) |
| `backend/tests/test_wing_rg_session_probe_verdict.py` | 프로브 자체의 3값 판정(엔드포인트·리다이렉트·오리진 이탈) |
| `backend/tests/test_rg_gap_driven_fetcher.py` | `_do_rg_run` 층2 루프(UNKNOWN으로 중단 안 함) |
| `tools/install_local_runtime.sh` | Mac 페처 배포. **CAS 가드 있음** — 다른 세션 배포본을 덮지 않는다 |
| `~/.ohisell/tools/.deploy_manifest.json` | "지금 도는 게 누구 코드냐"의 단일 답 |
| `.claude/memory/LESSONS_LEARNED.md` | 교훈 #98 |

## 5. 알려진 이슈 / 주의사항

### ★인계 문서 오류 2건 — 정정함 (이전 HANDOFF가 틀렸다)
1. **"PR #175 미병합"은 틀렸다** — `gh pr view 175` = **MERGED**. 그리고 #175는 `wing_browser_fetcher.py`를 애초에 건드리지 않는다(`coupang_auth.py` 계열).
2. **"`setup_fetcher_autologin.sh` 미실행"도 근거가 약하다** — Keychain에 서비스 `ohisell-coupang-ad` 항목이 **2건 존재**한다. 다만 어느 계정인지까지는 확인하지 못했다(`security dump-keychain`으로 acct 추출 실패).

### 배포본은 내 코드가 아니라 "내 코드 + 다른 세션 코드"다
17:03:16에 다른 세션(워크트리 `strange-nash-317ccc`, 브랜치 `claude/wing2-resident-chrome`, 커밋 `96379d8` = PR #191 = main `c48a578`)이 같은 파일을 재배포했다. **CAS 판정 `ancestor`라 덮은 게 아니라 얹은 것**이고, 20:05 실측으로 내 수정 4개 표식이 전부 온전함을 확인했다:

| 표식 | 배포본 개수 |
|---|---|
| `로그아웃 확증이 아니므로 로그인을 요구하지` | 1 ✅ |
| `rg_verdict = _PROBE_OK` | 2 ✅ |
| `판정을 지우지` | 1 ✅ |
| `_rg_session_ok_confirmed`(삭제됐어야) | **0** ✅ |

→ **배포본이 repo HEAD와 diff가 나도 놀라지 말 것.** 내 워크트리 HEAD(`6ef4196`)가 origin/main보다 뒤쳐진 것이지 되돌려진 게 아니다. 판단은 `.deploy_manifest.json` + 표식 grep으로 한다.

### 변경된 분기는 수동 `login`으로만 도달한다
데몬(`cmd_poll`)은 `cmd_login(..., rg_probe=False)`로 부른다(≈1255행, 회귀 가드 테스트 `test_poll_daemon_calls_login_with_rg_probe_false`). 즉 이번 수정은 **`python3 ~/.ohisell/tools/wing_browser_fetcher.py login`을 사람이 돌릴 때만** 실행된다.

### 병렬 세션 주의
오늘 이 저장소에서 최소 4개 세션이 동시에 돌았다. **작업 전 `git fetch` + `origin/main` 확인 필수.** 이번 세션에서도 로컬 main이 5커밋 뒤쳐져 있었고, 그 사이 병행 세션이 LESSONS 번호를 재정렬(`#85~94` 선점 → 내 것을 `#98`로)했다.

### codex 면제 기록의 출처 주의
다른 세션 HANDOFF(`HANDOFF_rg-layer2-session-misclassification_20260803.md` §9)에 *"쿼터 소진으로 Jino가 이 건 한정 면제 — 오후 작업도 동일 면제 유지"*라고 적혀 있다. **그건 그 세션의 기록이고, PR #188에 대해 Jino가 직접 면제를 준 적은 없다.**

## 6. 다음에 할 작업 (미완료)

### 이 작업의 부채 2건
- [ ] **codex 교차 리뷰** — 쿼터 소진(`try again at Aug 9th, 2026 4:16 PM`, 실제 실행해서 확인). 대상 = PR #188 diff. 부채로 볼지 면제로 볼지 Jino 확인 필요
- [ ] **UNKNOWN 분기 라이브 증거** — 미확보. 재현 조건이 구조적으로 안 나온다(프로브를 안정 엔드포인트 `download-list`로 옮긴 뒤 UNKNOWN 자체가 드물게 설계됨 + `cmd_login`은 수동 실행 경로). Jino가 `login`을 쓰다 마주치면 로그에 `RG(정산) 세션 판정 불가 — 로그아웃 확증이 아니므로 로그인을 요구하지 않는다` WARNING이 뜬다. **그게 보이는 순간이 라이브 증거다**

### ★다음 세션 최우선 — 주력 트랙(별도 세션에서)
`docs/tracks/active/track_naver-ad-optimization.md` · **한 세션 = 한 트랙**이므로 새 세션에서 시작할 것.
- [ ] **D-NAO-124 순위 서보** — 4위=가중 선호로 완화, 하향 바닥 없음(D-NAO-120 개정). **설계 확정·미구현, 트랙 파일에 "다음 세션 최우선"으로 명시**
- [ ] D-NAO-106~109 학습 배관 수리 — 워크트리 `learning-loop-repair`에 **커밋 2건 미배포**, 코딩 중단 상태
- [ ] 순서 엄수: 학습밴드 충돌 해소 → flight_loop 관측성 수정 → dry_run 해제

### 그 외 활성 트랙 잔여 (참고)
- [ ] 쿠팡 프로모션 손익 Phase1 — supplier 세션 만료로 정찰 미완
- [ ] 상품 연관맵 — 코드 100%, **Jino의 오픽스 매핑 데이터 입력**만 남음
- [ ] 오하이테크 광고비 S1 페처 확장 / 로켓 1P S5 프론트

## 7. 새 세션 시작 프롬프트

아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_cmdlogin-probe-residuals_20260803.md 읽고 이어서 작업해줘
```

네이버 SA 트랙을 시작할 거라면 대신 이쪽:

```
docs/PLAN_naver-ad-execution-loop.md §0 읽고 §7 체크리스트로 현재 위치 확인한 뒤,
docs/tracks/active/track_naver-ad-optimization.md의 D-NAO-124(순위 서보) 구현 이어서 해줘
```
