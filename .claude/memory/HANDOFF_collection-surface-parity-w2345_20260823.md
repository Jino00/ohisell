# 세션 인수인계: 접속 안정화 — W2~W5 + W1의 나머지 절반(VS 자동 재로그인)
> 저장일시: 2026-08-23 17:55 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★비트랙 세션이다(앵커에 `트랙:` 줄 없음) — 운영/인프라 수리. 「트랙 진행률」 절 없음.
> 체인: `접속-안정화` **n=3** (n=1 S1 · n=2 W1 — 등록부는 이 세션이 신설, 앞 둘은 소급 기록)

---

# ⛔ 다음 세션이 가장 먼저 읽을 것 — 오늘 배운 것 한 줄

**「호출 가능하다」와 「호출하면 답을 준다」는 다르다.** 오늘 라이브 사고 두 건이 전부 이 형태였고,
테스트는 두 번 다 초록이었다. 가드를 쓸 때 **형태가 아니라 행동**을 단언하라.

그리고 이 작업의 결함은 세 번 다 **Jino가 폰에서 버튼을 눌러서** 드러났다 — 테스트도, 적대 리뷰도,
완료 QA도 못 잡았다. 표면까지 가 보지 않으면 모른다.

---

## 1. 프로젝트 위치 및 환경
- **워크트리**: `~/.claude-worktrees/ohiselling/collection-surface-parity` (브랜치 `feat/collection-visibility-w5`, HEAD `64912307` — PR #346·#353·#360 전부 머지 완료)
- 공유 메인: `~/Library/Mobile Documents/.../Ohiselling` — **병행 세션 3개가 살아 있다. 여기서 작업 금지**
- prod: `sellc.ohitech.co.kr` · **앱 포트는 고정이 아니다**(블루-그린 8001↔8011, 다른 세션 배포로 바뀐다). 확인: `ssh sellc.ohitech.co.kr "pm2 list | grep ohisell"`
- prod 관측(읽기 전용): `ssh sellc.ohitech.co.kr "curl -s http://127.0.0.1:<활성포트>/api/coupang/ops/collection-status"` (nginx 경유는 Basic Auth에 막힌다)
- **Mac 페처**: 데몬은 repo가 아니라 `~/.ohisell/tools/`의 설치 사본을 돈다. 배포 `bash tools/install_local_runtime.sh`(CAS 가드)
  - 로그: `~/.ohisell_wing_fetcher.log`(WING1) · `~/.ohisell_wing2_fetcher.log`(WING2)
  - CDP: 9222=오픽스 Wing · 9223=오하이테크 Wing · 9224=오하이테크 광고센터 · 9225=공급자허브
- **`backend/.venv`가 없다.** 테스트는 시스템 `python3`. frontend는 `~/.ohisell-node-modules/...`를 심볼릭 링크(새 워크트리는 직접 걸어야 한다)

## 2. 이번 세션 완료 목록

### W2 — 문구의 저자를 하나로 (PR #346)
- ✅ `frontend/src/lib/streamRefresh.ts` — `outcomeView(spec, outcome)` 신설 = 아이콘·문구·tone의 **유일한 저자**. `describeOutcome`은 조립만
- ✅ `frontend/src/pages/Dashboard.tsx` — 패널이 `RefreshOutcome`을 **통째로 운반**(종전엔 done/failed/timeout 셋으로 접어 `attemptCount·inFlight·kind`를 버렸다) · `isLoginRequired` 2인자 정정 · 하단 고정 처방 제거
- ✅ `frontend/src/lib/bulkRefreshPersistence.ts` — `BulkQueueState`에 `outcome?` 추가(구버전 저장분 호환)
- ✅ `frontend/src/pages/CoupangOps.tsx` — 광고비 갱신의 **자체 폴링·자체 문구 사본 제거** → 공용 모듈 위임(그 사본 탓에 이 화면엔 W3·W4가 하나도 안 왔다)
- ✅ `backend/app/services/coupang/refresh_contract.py` — 6레인 공통 경로라 **처방을 빼고 사실만**(「로그인 필요」 접두는 유지 — 구버전 프론트 폴백이 매칭한다)

### W3 — 215초 고정 → 2단 판정 (PR #346)
- ✅ `PICKUP_TIMEOUT_MS=90000` / `MAX_TRACKING_MS=600000` · `StreamRefreshSpec.pickupTimeoutMs`(레인별)
- ✅ 캐던스 실측 반영: `ohitech_ad`는 poll 60s+쿨다운 60s라 180초, RG 2레인도 180초(VS run flock 점유)

### W4 — 표면까지 가는 회귀 가드 (PR #346)
- ✅ `frontend/src/pages/bulkPanelReachesTheUser.test.tsx` — Dashboard 통째 렌더 + **버튼 클릭**으로 「판정 → 운반 → 렌더」 전 구간
- ✅ `frontend/src/pages/adCostRefreshReachesTheUser.test.tsx` — 광고비 화면 표면 가드(리뷰 P2-2)
- ✅ `tools/tests/test_wing_session_recovery.py` — RG `verify=` 단언(PR #342의 유일 생존 변이)

### W5 — 가시성 인지 (PR #353)
- ✅ 상한을 **«깨어 있던 시간»**으로(`hiddenMs` 차감, T_max·T_pickup 둘 다) · 기본 시계는 `document.visibilitychange` 자가 측정, DOM 없으면 0, `finally`에서 리스너 해제
- ✅ **판정 전 강제 1회 조회** — 마감했다고 단정하지 않고 지금 상태를 한 번 더 본다
- ✅ `ABSOLUTE_CEILING_MULTIPLE=3` — 적대 리뷰 P1-1(무한 루프) 수리
- ✅ `frontend/src/lib/streamRefreshVisibility.dom.test.ts` 신설(jsdom)

### W1의 나머지 절반 — VS 자동 재로그인 (PR #360)
- ✅ `tools/wing_browser_fetcher.py` — `_recover_vs_session`(RG와 같은 3층) · `_vs_recover_and_refetch`(복구 «선언»을 믿지 않고 실제 fetch로 재확증) · `_vs_apply_recovery`(복구 결과를 회차 결과로 반영) · `_do_run` 배선 · `_fetch_vendor_summary`가 `retries<1`을 ValueError로 거절
- ✅ `tools/tests/test_wing_session_recovery.py` 13건 → **30건**(파일 전체 89건)

## 2-1. 완료 QA (별도 Sonnet 기·읽기 전용 · 2026-08-23 17:36 KST) — 판정 원문 그대로

- **작업 목적(정본 원문)**: *"나는 다른건 모르겠고 수집이 폰이나 노트북이든 어디서든 문제없이 모두 잘 되도록 만들고 싶어. 이게 내 목표야."* (Jino 2026-08-23 12:03 KST)
- **합격기준(원문)**: 계약 `docs/contracts/CONTRACT_collection_works_everywhere.md` §5 ①~④(⑤는 N 확정 후 별도 QA)

```
판정(계약 §5 합격기준): 부분달성 — ①부분달성(3회차 라이브: 4레인 당회 갱신 확인·RG 2레인은
  요청소멸·에러0이나 설계상 last_success_at 불변이라 "당회 갱신" 문구 미충족·사람 개입은 0건)
  / ②판정불능(불변, 크로스 디바이스 비교 불가) / ③부분달성(RG 경로 달성 / VS 경로는 3회차에
  자기 메커니즘으로 첫 성공 n=1이나 직전 2회차엔 같은 코드가 TypeError로 실패했고 PR #360
  미병합·WING2 미확인이라 "기본 경로화" 단정 불가) / ④달성(불변, 앞 판정 유지)
  (2026-08-23 17:36 KST)

판정(Jino 지시 원문): 부분달성 — 16:42 "W5까지 하자"는 달성(불변) / 13:08 스코프 지시는
  달성(불변) / 12:03 목표 원문("수집이 어디서든 잘 됨")은 3회차에서 6레인 전부 사람 개입 없이
  요청이 소멸(에러 0)했고 VS 자동 복구까지 처음으로 자력 성공했다 — 방향은 뚜렷이 개선됐으나
  n=1·미병합 PR·WING2 미확인이 남아 "완전히 잘 됨"이라 쓰기엔 이르다 — 부분달성
  (2026-08-23 17:36 KST)

판정(계약 §3 진행률 W0~W5): 달성 — 불변(§3은 W2~W5의 작업 완료를 묻고, 이번 재판정 대상인
  VS 자동 재로그인 배선은 계약 §3의 W1 몫이며 이 계약의 W2~W5 범위 밖이다) (2026-08-23 17:36 KST)
```

★**QA 판정 시점 이후 바뀐 것**: PR #360이 17:50에 머지됐다(QA는 「미병합」을 근거의 하나로 썼다).
그래도 판정을 고쳐 쓰지 않는다 — 판정은 그 시점의 기록이고, n=1·WING2 미확인은 여전히 유효하다.

- **미달·미판정 항목**: ①의 「`last_success_at` 당회 갱신」(RG 2레인 구조적 불가) · ② 크로스 디바이스 · ③ VS n=1·WING2 미확인
- **목적 전환 여부**: 없음(선언 없음). W1-VS는 목표 변경이 아니라 **계약 W1의 미완 이행**이다

### ★QA가 찾아낸 구조적 긴장 (계약 문구 개정 후보 — 처분은 Jino 몫)
합격 ①의 「`last_success_at` **당회 갱신**」을 **RG 2레인은 구조적으로 만족할 수 없다.**
`/refresh-complete`가 「받을 신규 데이터가 없으면 신선도 시계를 안 건드린다」로 설계돼 있기 때문이다
(2026-08-03 D-8 — 이 계약이 만든 것이 아니다). **완벽하게 성공한 회차라도** 정산주기가 없는 날엔
그 문구를 문자 그대로 못 만족한다. 고친다면 ①을 「요청이 소멸하고 새 실패가 없다(=정상 종료),
신규 데이터가 있으면 신선도 갱신」으로 쓰는 것이 실제 설계와 맞다.

## 2-3. 착지 — ✅ 완주 (PR 3건 전부 머지)
- **완료 단계**: 커밋 → push → PR → 적대 리뷰 → 머지 (3회 반복)
- **멈춘 단계**: 없음
- **좌표**:
  | PR | 내용 | 리뷰 | 머지 |
  |---|---|---|---|
  | **#346** | W2·W3·W4 | 1R FAIL(P1 2건) → 2R **PASS**, 변이 7/7 사망 | `e9439895` 14:02 |
  | **#353** | W5 | 1R FAIL(P1-1 무한 루프) → 2R **PASS**, 변이 10/10 사망 | `8cc46582` 17:06 |
  | **#360** | W1-VS | 1R **PASS**(P1 0) + 델타 2R **PASS**(회귀 0), 변이 18종 | `17:50` |
- **리뷰 판정**: 3건 전부 최종 PASS(P1 0)
- **배포**: 백엔드 무중단 재시작 · 프론트 17:06(CAS 조상 확인 후) · Mac 가동본 3회
- ⚠️ **셋 다 `--force` 병합**: CI 3 job 전부 `steps=0`·2~3초(결제 정지, 단 한 스텝도 실행 안 됨). 자백은 `$TMPDIR/safe_merge.log`

## 3. 확정된 결정사항 (번복 금지)
- **문구의 저자는 `outcomeView` 하나다.** 화면이 라벨을 따로 그리든 한 줄로 그리든 여기서 짓는다
- **처방은 레인마다 다르다** — `autoResumeOnLogin`이 참인 레인(`ofix_sales`·`rg_wing1`·`rg_wing2`)만 「자동으로 이어받습니다」. `_revive_lane`은 `wing_browser_fetcher.py`에만 있다(실측)
- **픽업 상한은 레인 데몬 캐던스에서 유도한다** — 전 레인 공통 상한의 단순 상향은 계약 §4 금지선
- **T_max 도달은 실패가 아니라 «추적 종료»다.** 절대 천장(T_max×3)은 liveness 보장이지 상한 상향이 아니다
- **백엔드는 사실만, 처방은 프론트가** — `report_failure`는 6레인 공통 경로라 어느 처방을 써도 절반이 거짓
- **`_fetch_vendor_summary(retries=0)`은 금지**(=`raise None`). 이제 ValueError로 거절한다

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/contracts/CONTRACT_collection_works_everywhere.md` | **계약 정본**(승인됨) — §3 W0~W5 · §5 합격기준 |
| `frontend/src/lib/streamRefresh.ts` | 판정·문구의 단일 원천. `outcomeView`·2단 판정·hiddenMs·절대 천장 |
| `frontend/src/pages/Dashboard.tsx` | 「전체 갱신」 패널 — `bulkStateText`가 `outcomeView`에 위임 |
| `frontend/src/pages/bulkPanelReachesTheUser.test.tsx` | 표면 가드(화면 글자 ↔ outcomeView) |
| `frontend/src/lib/streamRefreshVisibility.dom.test.ts` | W5 가시성·리스너 누수·종료 보장 |
| `tools/wing_browser_fetcher.py` | VS·RG 자동 재로그인. `_recover_vs_session`·`_vs_recover_and_refetch`·`_vs_apply_recovery` |
| `tools/tests/test_wing_session_recovery.py` | 배선 절단·verify 실호출 가드 30건 |
| `.claude/anchors/19c76836-....md` | 이 세션 앵커 — 라이브 3회차 관측·QA 판정 3건·이월 |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **Mac 절전 설정이 최대 위험**: 전원 어댑터에서도 `sleep 1`(유휴 1분에 잠듦). **Mac이 자면 데몬도 멈춰** 폰에서 눌러도 「Mac이 요청을 집지 않았습니다」가 뜬다. 시스템 설정이라 Jino만 바꿀 수 있다(`sudo pmset -c sleep 0`)
- ⚠️ **prod 앱 포트가 고정이 아니다**(블루-그린). 관측 전에 `pm2 list`로 확인할 것 — 이 세션도 8011로 굳게 믿다가 한 번 헛발질했다
- ⚠️ **프론트 배포 전 CAS 조상 관계를 «올바른 방향»으로 확인할 것**: 물어야 할 것은 「**내 커밋이 prod 스탬프의 조상인가**」가 아니라 「prod 스탬프가 **내 HEAD의 조상인가**」다. 이 세션이 방향을 반대로 봐서 「배포 안 됨」을 오보했다(rsync를 멈춘 판단 자체는 옳았다)
- ⚠️ **CI는 결제 정지로 죽어 있다** — 전 job `steps=0`. **빨강은 코드 신호가 아니다.** 로컬 스위트로 판단
- ⚠️ **`tools/tests`는 CI가 안 돈다**(`ci.yml:43`이 `working-directory: backend`)
- ⚠️ backend 전건에서 `test_health_partial_sync.py::test_health_route_actually_returns_partial_sync`·`test_vendor_item_axis.py::test_health_route_actually_returns_conservation` 2건이 실패하는데 **`origin/main` 기준선에서도 동일**(stash 대조로 확인) — 이 작업과 무관
- ★**`isLoginRequired` 2인자는 표면에 안 닿는다**: 위임 이후 `BulkQueueState.login`은 구버전 저장분 폴백에서만 읽힌다. 계약이 지시한 정정이라 유지하되 **표면 변이로는 죽일 수 없음**을 기록한다
- ⚠️ 훅 오탐 1건(계약 §6 계수 대상): `review-surface-mutation.sh`가 **완료 QA 위임문**을 적대 리뷰로 오인해 1회 deny(위임문에 「적대 리뷰는 네 일이 아니다」가 있었다). 재전송으로 통과

## 6. 다음에 할 작업 (미완료)

- **이어지는 작업의 목적(원문)**: *"나는 다른건 모르겠고 수집이 폰이나 노트북이든 어디서든 문제없이 모두 잘 되도록 만들고 싶어. 이게 내 목표야."* (Jino 2026-08-23 12:03 KST)
  → 받는 세션은 이 문장을 **원문 그대로** 앵커 `목표:`로 옮긴다. 발명 금지. `트랙:` 줄은 쓰지 않는다(비트랙).

- [ ] ★★**P2-7 — `_landed_on`은 라이브에서 한 번도 참을 말한 적이 없다** (다음 세션 1순위)
  RG·VS 전 회차가 「자동 로그인 실패(목적지 미착지)」 → 「앱 세션 검사 통과」였다. 즉 복구는 **전적으로 `verify`가 살리고 있고**, URL 판정 기계는 매 복구마다 **SSO 20초 + 폴 25초를 순손실**로 태운다(17:30:52→17:31:32 = 40초). 테스트 4건이 「발동한 적 없는 성질」을 지키고 있다는 뜻이기도 하다. **묻지 말고 진행** — 되돌릴 수 있고 목표 변경이 아니다
- [ ] **M5b·M12b·M12c — `_do_run` 배선 3줄**(헬퍼 반환값을 지역변수에 꽂는 자리)에 가드가 없다. 로직은 전부 잠겼고 이 3줄만 남았는데 `sync_playwright()` 안이라 브라우저 없이는 못 잡는다. 헬퍼 시그니처를 바꿔야 하므로 **새 설계 제안**. ⚠️**라이브 증거는 리팩터 «이전» 코드의 것**(17:31:40) — `df1fb480` 배포 후 **첫 VS 만료 회차에서 폰 패널이 ✅인지** 확인 필요
- [ ] **WING2(오하이테크) 계정의 VS·RG 자동 복구 라이브 확인** — 세 회차 모두 만료 미발생. 자연 발생 대기(억지로 만들지 않는다)
- [ ] **P2-5** `_vs_recover_and_refetch`의 `None`이 「복구 실패」와 「복구 후 로그아웃 재확인」을 뭉갠다 — 결과는 보수적이라 안전하나 그 좁은 창에서 사유 로그가 안 남는다(합격 ③의 「정직하게」가 약해짐)
- [ ] **계약 §5① 문구 개정** — RG 2레인이 구조적으로 만족 불가(§2-1 참조). **Jino 결정 사항**(합격기준 변경이라 §1 승인 지점 ①)
- [ ] **합격 ⑤(N일 기간 유지)** — N 미정(Jino 미결). 계약 제안 14일
- [ ] **Jino 미결 ②**: 목표의 「모두」가 쿠팡 6레인까지인가, 네이버·스마트스토어까지인가(동병 여부 미확인 — 넓히면 조사 슬라이스 선행)
- [x] ~~W2·W3·W4·W5·W1-VS~~ **완료** — PR #346·#353·#360 전부 머지(§2-3)
- [x] ~~Jino가 폰에서 「전체 갱신」~~ **완료** — 3회 눌러 ④ 달성·①③ 부분달성 판정(§2-1)

## 7. 새 세션 시작 프롬프트
```
/session-relay 접속-안정화
```
