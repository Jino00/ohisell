# 세션 인수인계: 수집 불안정 근본 수리 S1
> 저장일시: 2026-08-22 15:58 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★비트랙 세션이다(앵커에 `트랙:` 줄 없음) — 운영/인프라 수리. 「트랙 진행률」 절 없음.

## 1. 프로젝트 위치 및 환경
- 워크트리: `/Users/jino/.claude-worktrees/ohiselling/collection-stability-s1` (브랜치 `worktree-collection-stability-s1`, **PR #325 병합 완료·미푸시 0**)
- 메인 체크아웃: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- prod: `sellc.ohitech.co.kr` · 앱 경로 `/home/ubuntu/ohisell` · **앱 포트 8011**(pm2 `ohisell-backend-8011`)
  - ★nginx를 거치면 Basic Auth에 막힌다 → 관측은 **원격 루프백**으로: `ssh sellc.ohitech.co.kr "curl -s http://127.0.0.1:8011/api/coupang/ops/collection-status"`
  - prod DB = **SQLite** (`backend/ohisell.db`). alembic은 `alembic.ini`에 URL이 하드코딩돼 `DATABASE_URL`을 무시한다(교훈 #341)
- Mac 페처: 데몬은 repo가 아니라 **`~/.ohisell/tools/`의 설치 사본**을 돈다. 배포는 `tools/install_local_runtime.sh`(CAS 가드)
  - 데몬 5종: `com.ohisell.{wing,wing2,rocket,adcost,ohitech-ad}` + `scheduler-watchdog`
  - CDP 포트: **9222=오픽스 Wing · 9223=오하이테크 Wing · 9224=오하이테크 광고센터 · 9225=오하이테크 공급자허브**
- 배포: `scripts/safe_deploy.sh [파일...] [--migrate] [--restart]` / `--frontend` · 병합 `scripts/safe_merge.sh <PR> [--force]`
- 프론트 `node_modules`는 심볼릭 링크 필요: `ln -s /Users/jino/.ohisell-node-modules/ohiselling-frontend/node_modules frontend/node_modules`

## 2. 이번 세션 완료 목록

- ✅ **진단** — Jino *"왜 될때있고 안될때 있고 이렇게 불안정하지?"* → 로그·코드 실측으로 원인 특정. Fable(기획 파트너)이 6레인 전수 조사 후 설계
- ✅ **계약 1장 승인** `docs/contracts/CONTRACT_collection_stability_s1.md` (Jino 승인 + 2개 선택: 자동 재수집 «켬» / S2 사전 프로브 «아직 안 연다»)
- ✅ **S1-0** `940101f6` — Mac 가동본의 prod Basic Auth 배선을 repo로 역이식(페처 5개). `rocket`은 양쪽이 서로 앞서 선택 병합
- ✅ **W1~W5 본체** `6661d926`
  - `backend/app/models.py` — `CoupangWingCookie.last_error_kind` (String 32, nullable)
  - `backend/alembic/versions/cs1kind0a1b2_*.py` — 신규 마이그
  - `refresh_contract.py` — `KIND_NO_RESPONSE` 신설 · `report_failure`가 kind 기록 · `_settle_values`가 kind 클리어 · `status_fields`가 kind 노출(**6레인 공유 → 한 줄이 전 레인 전파**)
  - `collection_status.py` — **RG 2큐 편입(4→6)** · `needs_login` 상태(in_flight보다 우선) · 스트림별 임계(`_STREAM_THRESHOLDS`, RG 9일/16일)
  - `collection_watchdog.py` — `needs_login` 알림 + RG 계정 등재(★`rescue_streams`엔 **안** 넣음 = 자동 창 금지선)
  - `tools/wing_browser_fetcher.py` — fail-fast(180→0) · `keep_open`이면 **탭도** 유지 · 로그인 워치(30초, 기존 탭만) · 자동 재개 · `_notify_mac` · `_prune_stale_tabs`
  - `tools/{ohitech_ad,rocket_supplier}_fetcher.py` — 같은 탭 유지 수리
  - `frontend/src/lib/streamRefresh.ts` — 타임아웃 3분할 · `isLoginRequired`가 kind 우선 · 「자동으로 이어받습니다」
  - `frontend/src/components/collectionFreshnessBanner.ts` — `needs_login` 상주 배너
- ✅ **적대 리뷰 1R = FAIL(P1 4건) → 수리 `a0b71fc8` → 2R = PASS(P1 0)**
- ✅ **변이 재주입 14/14 사망**(1R 생존 7종 포함) · 무테스트 표면 3곳 봉합 · `tools/tests/test_wing_login_watch.py` 신설 22건
- ✅ **배포** — 백엔드(마이그→코드→무중단 재시작, 다운타임 0초) · 프론트 · Mac 페처 5데몬
- ✅ **라이브 검증** — 증거 4종 `docs/contracts/evidence_collection_stability_s1/`
- ✅ **PR #325 병합**(`30e214bf`) — CI는 결제 정지로 전 job이 steps=0/2초 실패라 `--force`(자백 `$TMPDIR/safe_merge.log`)
- ✅ **증거 캡처 스크립트** `scripts/capture_collection_evidence.sh` (읽기 전용, QA가 그대로 돌릴 수 있다)

## 2-1. 완료 QA (별도 Sonnet 기, 읽기 전용 · 1차 판정불능 → 배포 후 재판정 1회)

- **작업 목적(정본 원문)**: *"근본적으로 이런 수집 불안정을 해결해줘. fable이 해결해줘"* (Jino 2026-08-22 12:59 KST)
- **대조 대상 2개** — 계약 §5 합격기준 / Jino 지시 원문

### 판정(계약 §5 합격기준): **부분달성** (2026-08-22 15:39 KST)
> 7항목 중 6항목(①②③④⑥⑦) 배포 후 라이브 증거로 달성 — 1차 QA가 판정불능이라 적었던 항목이 전부 실제로 관측됐다. 유일하게 ⑤가 「전부 215초 내」를 `supplier_hub` 1레인(331초)에서 명백히 위반. ⑤의 다른 조건 「응답 없음 0건」은 별개로 충족. ②는 API·순수변환 경로까지만 확인이고 React 렌더 화면은 미촬영(감점 사유로 명시하되 달성 판단).

| # | 합격기준 | 관측 | 판정 |
|---|---|---|---|
| ① | 로그아웃 상태 전체갱신 1회 → RG 2레인 60초 내 정착·창 계정당 1회·「Mac이 켜져 있는지」 0건 | 15:14:49 트리거 → **10초**(RG2) · **56초**(RG, 중간 `net::ERR_ABORTED` 1회 백오프). CDP 9222/9223 각 xauth 탭 1개(추가 창 없음) | **달성** |
| ② | 버튼 없이 재진입 시 로그인필요 상주 배너(RG 포함) | 15:16:36 스냅샷: `rg_wing1`·`rg_wing2` = `needs_login`/`kind=login_required`. 배너 빌더는 순수함수+유닛테스트 | **달성**(렌더 화면 미촬영) |
| ③ | `curl localhost:9222/json/list` page ≥1 (출력 파일 보존) | 9222·9223 각 1개. **before: 둘 다 0개** | **달성** |
| ④ | 로그인만 하면 버튼 재클릭 없이 배너 해제 + 자동 재수집 완주 | 오하이테크 15:19:41→15:20:25 · 오픽스 15:24:29→15:25:14. 정산 대사 `diff 0.0 match True`. prod `last_error_kind` 두 계정 NULL | **달성** |
| ⑤ | 정상 상태 6레인 215초 내 정착·응답없음 0건 | (a) T0=15:27:20 → 전 레인 정착 15:32:54 = **331~334초**. `supplier_hub`만 초과(나머지 82~138초 또는 즉시) (b) 6계정 `last_error_kind` 전부 빈칸 = 오류·no_response 0 | **미달**((a) 위반, (b) 충족) |
| ⑥ | `in_flight` 문구 분기 | 유닛테스트 존재·통과. 계약이 「실사례 없으면 모의 허용」 명시 | **달성**(모의 기준) |
| ⑦ | `diff tools/*.py ~/.ohisell/tools/*.py` 6개 차이 0 | 6개 전부 차이없음. **before: 3개 차이** | **달성** |

### 판정(Jino 지시 원문): **부분달성** (2026-08-22 15:39 KST)
> 사용자가 호소한 「로그인 상태의 오판·불안정한 이름 붙이기」는 라이브로 확인된 근본 수리다(before 창 3회·9분 → after 56초 fail-fast, Jino가 «로그인만» 하고 두 계정 완주). 적대 리뷰 P1 4건도 정확히 「불안정」의 다른 얼굴이었고 배포 전에 잡혔다. 그러나 지시가 "근본적으로"·"이런 수집 불안정"이라 포괄적으로 말한 이상, ⑤에서 **새로 드러난 부하 시 SLA 초과**가 남아 있어 완전한 「해결」이라 부르기 이르다 — 종합 부분달성.

### 미달·미판정 항목 (다음 세션 슬라이스의 기본 후보)
- **⑤ `supplier_hub` 331초** — 계약 §1이 「이번에 안 함」으로 미뤄둔 **S3(레인 격리) 개계약 트리거와 같은 축**. 문턱(「주 1회 이상」) 해당 여부는 누적 관측 필요
- **QA가 확인 못 한 것**: ② 실제 렌더 배너 화면 · ① 「Mac이 켜져 있는지」 0건의 실제 화면 문구(코드 분기로 간접 확인) · ⑤(b) 331초 구간에 `describeOutcome`이 실제로 in_flight 분기를 탔는지 · ⑥의 실사례 승격 여부 · 병행 세션 로그 오염분 전수 대조
- **합격기준을 낮추지 않았다** — ⑤를 미달인 채 닫았다

### 목적 전환 여부
없음. `🔁 목적 전환` 선언 0건. 「안 함」·금지선 위반 관측 0건(QA 확인).

## 3. 확정된 결정사항 (번복 금지)

- **자동 재수집 = 켬** (Jino 결정) — 로그인 회복 감지 시 소멸된 요청을 자동 재요청. 상한 `_MAX_AUTO_REVIVES=3`
- **S2 사전 프로브 = 아직 열지 않는다** (Jino 결정) — 2026-07-27 「순수 버튼-only」 예외라 별도 승인 필요
- **적대 리뷰 P2-8 기각** — 자동 재요청이 RG 쿨다운 면제를 발동하는 건 「지금 이어받아라」라는 의도 그대로이고 상한 3회가 최악을 묶는다
- **`ad_cost_browser_fetcher.py`는 W3 대상 아님** — CDP가 아니라 Playwright launch 구조. W2·W5는 이미 갖춤. 「4개 사본 전부」는 **CDP 3종에 대해** 충족
- **로그인 워치는 wing/wing2에만** — ohitech·rocket은 탭 유지까지만
- **kind 클리어는 `clear_error`와 무관하게 항상** — prod 성공 경로 6곳이 전부 기본값 호출이라 조건부면 4레인에 배너가 영구 고착된다

## 4. 핵심 파일 목록

| 파일 | 역할 |
|------|------|
| `docs/contracts/CONTRACT_collection_stability_s1.md` | 계약 정본(목표·판단기준·금지선·합격기준·예산) |
| `docs/contracts/evidence_collection_stability_s1/` | 라이브 증거 4종(before-deploy → after-deploy → after-trigger-logged-out → after-login-and-full-refresh) |
| `scripts/capture_collection_evidence.sh` | 증거 캡처(읽기 전용). `scripts/capture_collection_evidence.sh <라벨>` |
| `backend/app/services/coupang/refresh_contract.py` | lease 계약 + `last_error_kind`. `status_fields()`를 6레인이 공유 |
| `backend/app/services/coupang/collection_status.py` | 6스트림 집계 · `needs_login` 판정 · 스트림별 임계 |
| `tools/wing_browser_fetcher.py` | wing/wing2 데몬. fail-fast · 탭 유지 · 로그인 워치 · 자동 재개 |
| `tools/tests/test_wing_login_watch.py` | W2/W3 가드 22건 (⚠️ CI 밖) |
| `frontend/src/lib/streamRefresh.ts` | 갱신 판정 단일 구현 + 타임아웃 3분할 |

## 5. 알려진 이슈 / 주의사항

- ⚠️ **CI는 결제 정지로 죽어 있다** — 전 job이 `steps=0`·2초 실패. **빨강은 코드 신호가 아니다.** 최근 8개 run 전부 동일(이미 병합된 #324·#322 포함). 로컬 스위트로 판단할 것
- ⚠️ **`tools/tests`는 CI가 안 돈다** — `.github/workflows/ci.yml:43`이 `working-directory: backend`. P1-2·3·4 가드가 전부 그 밖에 있다
- ⚠️ **사전 실패 테스트 2건**(base `dc7d185b`에서도 실패, 이 작업과 무관): `test_health_partial_sync.py::test_health_route_actually_returns_partial_sync` · `test_vendor_item_axis.py::test_health_route_actually_returns_conservation`
- ⚠️ **`COUPANG_WING1`·`WING2` 쿠키 행이 여전히 `red`**(Wing 세션쿠키 만료) — 이 계약 대상(RG 2레인)과 다른 경로지만 「쿠팡 수집이 불안정하다」는 체감에 계속 기여할 수 있는 **별도 표면**(QA 지적)
- ★**로그인 워치가 락 경합 시 침묵한다** — 내가 쓴 `if not _wacq: pass`. 병행 세션이 오픽스 락을 잡은 3.5분이 로그에 흔적을 안 남겨 원인 규명에 프로세스 추적이 필요했다. 한 줄 로그를 넣을 것
- ★**병행 세션의 env 절반 걸기** (2026-08-22 15:20:50 실관측): `OHISELL_WING_CONFIG`만 wing2로 걸고 `OHISELL_WING_LOG`·`OHISELL_WING_LOCK`은 기본값 → **오픽스의 로그에 쓰고 오픽스의 락을 잡는다**. `~/.ohisell_wing_fetcher.log`에 `account=COUPANG_WING2`·`CDP 9223` 줄이 섞여 있다(로그 읽을 때 감안). 세 env는 **한 벌로** 걸 것
- ★**워크트리는 자기 서브에이전트로부터 지켜주지 않는다** — 리뷰어가 변이 주입 중일 때 `git add -A`가 그 변이를 삼켰다(`2e87c400` → 되돌림 `af518754`). 리뷰 중엔 파일 명시 또는 리뷰 완료 대기
- ★**변이 스크립트의 `git checkout --`은 미커밋 수정을 지운다** — 수정을 **먼저 커밋**하고 변이를 돌릴 것(한 번 당했다)
- `net::ERR_ABORTED` 1회(15:15:08, 오픽스 RG 진입) — 남겨 둔 탭 재사용과 관련 있을 수 있다. 일시 실패로 정상 처리됐으나 재발 빈도 관측 필요
- `cmd_login(wait_secs=0)`의 첫 프로브는 정착 대기 0초 — Cloudflare 챌린지 중이면 헛칠 수 있다(2R 지적). 상한 3회가 최악을 묶지만 상한은 프로세스 수명 기준이라 launchd 재기동마다 리셋
- 로컬 main이 origin/main보다 **3커밋 앞섬** — 「PAO 논의 34」 세션의 docs 커밋. **내 것 아님**

## 6. 다음에 할 작업 (미완료)

- **이어지는 작업의 목적(원문)**: *"근본적으로 이런 수집 불안정을 해결해줘. fable이 해결해줘"* (Jino 2026-08-22 12:59 KST)
- **남은 슬라이스**: ⑤(부하 시 SLA) 누적 관측 → 조건 충족 시 S3 개계약 / 가시성 구멍 2건 수리

### ✅ alembic 병합 마이그 중복 — **해소됨** (PR #326, 2026-08-22 16:02)

두 세션이 같은 두 헤드(`cs1kind0a1b2` + `imp1ledger47a`)를 서로 모른 채 **각자** 접었다.
둘 다 살아 있으면 `alembic upgrade head`가 「Multiple head revisions」로 죽어 **모든 DB 배포가 막힌다**.

**해소 방식 — theirs(`mrg48s1heads`)를 정본으로.** prod의 `alembic_version`이 이미 그걸 가리키므로,
내 것을 정본으로 삼으려면 **DB 쓰기**가 필요했다(되돌리기 어려운 축은 회피). 둘은 같은 두 부모를
접는 no-op merge revision이라 의미가 동일하고 **스키마 변화 0**.

- 소유 세션(`a9a2121b…`, 워크트리 `~/.claude-worktrees/ohiselling/import-ledger`)이 **이미 종료**돼
  조율 불가 → 그 커밋 `2b8eaf96`(마이그 파일 1개·34줄·미푸시)을 **원본 그대로** main으로 옮겼다
  (revision id가 prod 버전 행과 정확히 맞아야 해 손대면 안 된다).
- 조율 시도 기록: 15:53 `쿠팡 손익정합3`에 SendMessage → **소유자가 아니라는 답신**
  (`git branch -a --contains 2b8eaf96` = `worktree-import-ledger` 하나). 방향엔 동의하되
  「결정은 소유 세션이 해야 한다」. 그 세션 종료를 확인하고 진행.
- **최종 상태(16:03 실측)**: main head = `mrg48s1heads` · prod `alembic_version` = `mrg48s1heads`
  · prod `alembic heads` = `mrg48s1heads` **단일** → **main·prod 완전 일치, 배포 정상**
- 내가 prod에 잠깐 올렸던 `mrg2heads0822`는 제거함(백업 `/tmp/mrg2heads0822.bak`)

★**교훈**: 두 세션이 동시에 마이그를 만들면 «충돌 해소»조차 충돌한다. 병합 마이그를 만들기 전에
`git branch -a --contains <상대 head>`와 **prod `alembic_version`**을 먼저 보라 — prod가 이미
가리키는 revision이 있으면 그쪽이 정본이다(DB 쓰기를 피하는 쪽).
- [ ] **⑤ 누적 관측** — `supplier_hub` 215초 초과가 반복되는지. 주 1회 이상이면 S3(레인 격리) 개계약. 관측은 `scripts/capture_collection_evidence.sh`로
- [ ] **로그인 워치 락 경합 로그 한 줄** — `wing_browser_fetcher.py`의 `if not _wacq: pass`에 30초마다는 시끄러우니 간헐 로그
- [ ] **env 세 벌 가드** — `OHISELL_WING_CONFIG`가 기본값과 다른데 `LOG`/`LOCK`이 기본값이면 기동 시 경고
- [ ] **`tools/tests`를 CI에 편입** — `.github/workflows/ci.yml`의 `working-directory` 구조 손보기(CI 결제 복구 후에나 의미 있음)
- [ ] **`COUPANG_WING1`/`WING2` 쿠키 행 red** — 별도 표면. 이 계약 밖이지만 사용자 체감에 기여
- [x] ~~PR #325 생성·병합~~ 완료 — `30e214bf` (2026-08-22 15:50, `--force`·CI 결제 정지 자백 기록)
- [x] ~~배포(prod·프론트·Mac 페처)~~ 완료 — 다운타임 0초
- [x] ~~`mark_success(clear_error=False)` kind 미클리어(1차 QA 이월)~~ 해소·라이브 재확인

## 7. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_collection-stability-s1_20260822.md 읽고 이어서 작업해줘
```
