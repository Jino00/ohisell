# HANDOFF — RG 정산 반쪽 run 오보 수정 (2026-08-03 오전, 워크트리 `cool-driscoll-31712d`)

## §0 가장 먼저 알아야 할 것

- **PR #173 병합 완료 = main `7ffa8a0`.** 원격 main에서 두 핵심 심볼 실재 확인함
  (`last_error_at=case(` / `settleBeforeSuccess && settledFailure`).
- **prod 배포는 안 했다.** main에만 들어갔다. DB 변경 없음 → `--migrate` 불필요.
  ```
  scripts/safe_deploy.sh backend/app/services/coupang/rg_settlement_sync.py \
                         backend/app/routers/coupang_ops.py --restart
  ```
  프론트는 별도 `--frontend`.
- **라이브 합격은 아직 미충족.** 이 변경의 "됐다"는 코드·테스트가 아니라 **배포 후 첫 RG 실패
  회차**에서만 확인된다(§4 참조). 정산 실패 회차가 자주 오지 않으므로 그때 확인할 것.
- 이 세션은 PR #169의 codex [P1]을 Jino가 **별건 분리**한 건이다. 트랙(네이버 SA 광고)과 무관한
  쿠팡 수집 인프라 작업.

## §1 결함과 수정

### 결함
`frontend/src/lib/streamRefresh.ts`의 `runStreamRefresh()`에서 `settleBeforeSuccess` 스트림
(RG 정산 = COUPANG_WING1/2)이 **반쪽 run을 "✅ 완료"로 오보**했다.

RG 한 회차는 (정산주기 × 리포트종류) 여러 엑셀을 올린다 → 첫 엑셀에서 이미 `last_success_at`이
baseline을 벗어난다 → 뒷단이 실패해 요청이 소멸하면 성공 분기(`변한 success && !requested`)가
**먼저** 참이 되어 `{state:"done"}`으로 이탈. `settleBeforeSuccess`가 막으려고 만들어진 바로 그
오보를 스스로 통과시키고 있었다. PR #169가 도입한 게 아니라 리팩터 전
`CommandCenter.runRgRefreshForAccount()`에 같은 순서로 이미 있던 결함.

### 수정 4단계 (codex 교차 리뷰 3라운드 전건 대응)

1. **프론트 판정 순서** — RG 한정 실패 우선. 비RG는 성공 우선 그대로.
2. **codex 1R[P1]** — `rg_mark_heartbeat`에 '살아있는 요청' 가드.
3. **codex 2R[P1]** — 그 가드를 한 UPDATE 안 `CASE`로 원자화.
4. **codex 2R·3R[P2]** — 공용 4스트림 테스트에서 RG 분리 + 주석 7곳 정정.

## §2 판정 근거 — 타임스탬프 추측이 아니라 백엔드 계약

| 경로 | 상태 전이 |
|---|---|
| run 중 upload | `rg_mark_heartbeat`: `last_success_at=now` + **요청이 살아있을 때만** `error=NULL` |
| run 정상종료 | `refresh-complete` → `mark_success(clear_error=True)`: 요청 소멸 + `error=NULL` |
| run 실패종료 | `report_failure`(소멸 사유)·`_reap_exhausted`: `last_error_at=now` + 요청 소멸 |

→ **요청 소멸 시점에 이번 run의 실패 흔적이 살아 있다 ⟺ 그 run은 실패로 끝났다.**

### ★2026-07-17 '성공 우선' 사고의 실제 순서 (추정 아님)
커밋 `7118ef5` 본문: *"rg_mark_heartbeat(성공)와 뒤늦게 도착한 페처의 실패 보고가 138ms 차로
같은 행을 갱신 — **last_error_at > last_success_at**이 됐다"*.
→ 검토됐던 "나중 타임스탬프가 이긴다" 방향이었다면 **그 성공 run을 실패로 뒤집었을 것**이다.
→ 지금 판정은 다르다: lease 계약(2026-07-27) 이후 그 경로는 kind 없는 평범한 실패라 요청이
  살아남아(재시도) 애초에 정착하지 않는다. **3회 모두 그렇게 끝날 때만** 실패로 보고되며,
  정산=돈 데이터에서는 거짓 완료 ≫ 거짓 실패다(재클릭 무해).

## §3 codex가 깬 내 전제 (이 세션의 핵심 교훈)

**"성공 경로가 실패 흔적을 반드시 지운다"는 각 함수가 실행되는 순간에만 참이었고, 최종 상태의
불변식이 아니었다.**

- **1R[P1]**: 페처 업로드의 클라 타임아웃(60s)은 **서버 처리를 취소하지 않는다.** 페처가 실패를
  보고해 run이 끝난 **뒤에** 서버 ingest가 완주하며 heartbeat가 terminal error를 무조건 지운다.
  프론트만으로는 못 닫는다 — heartbeat에 lease도 요청 조건도 없어 늦은 straggler와 정상 업로드를
  구분할 수단이 없다.
- **2R[P1]**: 1R 가드가 `SELECT → 판정 → COMMIT`이라, 그 사이 fetch-error(3회 소진)·
  `_reap_exhausted`가 끼면 **이미 통과한 조건을 근거로 방금 기록된 terminal error를 지운다.**
  1·2회차 실패 흔적이 있어야 ORM이 NULL 변경을 dirty update로 내보내므로 하필 "1·2회차 실패 후
  3회차" 조합에서 정확히 열린다.

**codex 3R 최종 확인**: *"CASE UPDATE 자체는 건전합니다… 새 회귀 3종은 원래 결함을 실제로
잡습니다. atomic 테스트의 `h_view` 지역변수가 강참조이므로 GC 타이밍에는 의존하지 않습니다."*

### ★회귀 작성 중 스스로 한 번 속았다
처음 쓴 경합 회귀가 **수정 전 코드에서도 통과**했다. SQLAlchemy identity map이 **약참조**라,
읽은 행을 변수로 붙잡지 않으면 GC가 가져가고 다음 조회가 최신값을 다시 읽어 창이 우연히 닫힌다.
강참조로 고정한 뒤에야 `assert None == '...11:12:17.980996'`로 실패했다.
→ LESSONS #71. **모든 회귀는 "수정을 되돌리면 실패하는가"로 검증한다.**

## §4 라이브 합격 시나리오 (배포 후 확인 — 미충족)

배포 후 **첫 RG 갱신 실패 회차**에서:
1. 일부 엑셀만 올라간 뒤 실패로 끝나는 회차에서 화면에 **"❌ 실패(사유)" 또는 "🔑 로그인 필요"**
   (이전엔 "✅ 완료")
2. 그 계정의 데이터 재조회가 **일어나지 않는다**(`CommandCenter`의 `outcome.state === "done"` 분기)
3. prod DB `coupang_wing_cookie`에서 `COUPANG_WING_RG`/`_RG2` 행이
   `refresh_requested_at IS NULL AND last_error_at IS NOT NULL`로 남아 있다

## §5 남은 것 / 부채

### 별건 [P1] 2건 — codex 3R 발견, **이 PR이 만든 게 아님**. 별도 세션 진행 중(2026-08-03 11:43 시작)
1. **`status="empty"` 업로드가 성공으로 계산됨** — `ingest_settlement_xlsx`가 인식 가능한 시트가
   0개면 예외 대신 `{"upserted":0,"status":"empty"}`를 정상 반환하는데, 라우터가 결과와 무관하게
   heartbeat를 찍는다. 쿠팡이 시트명을 바꾸면 정산이 통째로 비어도 "완료". (chip `task_4eb8f2ca`)
2. **`refresh-complete`의 lease가 선택 파라미터** — `lease=None`이면 `mark_success`가 현재 요청·
   임대를 확인하지 않고 무조건 지운다. 이전 회차의 늦은 완료 POST가 새 회차 요청을 지우면
   프론트의 "새 실패 없이 요청만 사라짐 = 정상 종료" 분기가 **시작도 안 한 run을 done으로 오보**.
   (chip `task_b7fd6986`)
   - 실측: 배포된 `~/.ohisell/tools/wing_browser_fetcher.py`는 repo와 **바이트 동일**하고
     `body = {"lease": lease} if lease else {}`로 lease를 보낸다. 필수화 시 `lease`가 None이 되는
     페처 내부 경로가 있는지 먼저 확인할 것.

⚠️ **두 세션은 이 PR과 같은 파일**(`rg_settlement_sync.py`·`coupang_ops.py`)을 건드린다. 특히 ①은
`rg_mark_heartbeat` 호출부를 다루는데 그 함수가 방금 바뀌었다. **두 세션은 11:43에 시작했고 main은
11:57에 갱신됐으므로, 구버전 main에서 분기했을 가능성이 높다 — PR 시점에 rebase 확인 필수.**

### 부작용 (현재는 실害 제한적, 향후 위험)
요청 없는 성공(수동 업로드)에서 옛 실패 흔적이 남는다. RG가 전역 신선도 배너의 스트림 목록에
없어 지금은 진단상 혼합 상태(`green + 최신 success + 옛 error`)일 뿐이다. **그러나 PR #171이 방금
넣은 수집 신선도 워치독에 RG를 편입하면 오경고가 된다** — 그때 함께 다뤄야 할 부채.

### 프론트-백엔드 계약 테스트 부재
회귀는 프론트(스냅샷)·백엔드(상태 전이)를 각각 검증한다. 둘을 한 테스트로 잇는 계약 테스트는 없다
(codex 1R[P2] 지적의 잔여분).

## §6 검증 기록

- 백엔드 **4,183 passed** (main `f39203b` rebase 후 재실행. #171 테스트 포함)
- 프론트 **169 passed** · `npm run build` 통과
- codex 교차 리뷰 **3라운드**(PR 경계 의무 + 정산 데이터 완결성)
- 회귀 3건 전부 **수정 전 실패를 먼저 확인**:
  - `× ★RG 반쪽 run … expected { state: 'done' } to deeply equal { state: 'failed' }`
  - `FAILED test_rg_heartbeat_does_not_erase_terminated_failure — assert None == '…10:55:06'`
  - `FAILED test_rg_heartbeat_guard_is_atomic_… — assert None == '…11:12:17'`

## §7 PR 이력 (혼선 주의)

- **#172** — base를 PR #169 브랜치로 잡아 열었다가 **닫음**. #169가 squash 병합되면서 main과
  CONFLICTING이 됐고, force-push가 이 환경의 Bash 권한 규칙에 막혔다(Jino가 AskUserQuestion에서
  허용을 골랐는데도 권한 계층이 별도로 차단).
- **#173** — rebase한 4커밋을 새 브랜치 `fix/rg-partial-failure-misreport-v2`로 일반 push해
  main 기준으로 다시 연 것. **squash 병합 완료 = `7ffa8a0`.**
- 교훈: base 브랜치가 squash 병합되면 그 위에 쌓은 PR은 main과 반드시 충돌한다. **stacked PR은
  base가 병합되는 즉시 rebase가 필요하고, force-push가 막힌 환경에서는 새 브랜치가 유일한 길이다.**

## §8 이 세션에서 만든 기록

- `LESSONS_LEARNED.md` **#70**(정착 판정은 최종 상태의 불변식이어야 한다) · **#71**(회귀가 수정
  전에도 통과하면 테스트를 의심한다)
- `failures.jsonl` 2줄(경합/비동기 상태 판정 · 약참조 identity map으로 인한 가짜 초록)
