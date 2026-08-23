# 세션 인수인계: 접속 안정화 — 라이브 전건 정상 확인 후 체인을 닫다

> 저장일시: 2026-08-23 22:05 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★비트랙·비계약 세션이다(앵커 없음 — 코드 0줄·쓰기 0건) — 「완료 QA」·「트랙 진행률」 절 없음.
> 체인: `접속-안정화` **n=5** (n=1 S1 · n=2 W1 · n=3 W2~W5+VS · n=4 상한 확정·계약 종결)
> ★★**이 체인은 여기서 닫힌다** — Jino 2026-08-23 21:58 원문: *"이 트랙의 업무는 모두 끝난거 같다. close해도 되지?"*

---

# ⛔ 다음 세션이 가장 먼저 알 것

**이 체인(`접속-안정화`)은 닫혔다. 이어서 팔 슬라이스가 없다.**
계약 `docs/contracts/CONTRACT_collection_works_everywhere.md`는 **n=4에서 이미 종결**됐고(Jino 20:04),
n=5는 「정말 닫아도 되나」를 라이브로 재고 닫은 세션이다. 아래 §6은 **할 일 목록이 아니라 부채 목록**이다.
다시 열려면 **Jino가 먼저 꺼내야 한다**(전역 §7 — 다른 스코프의 작업은 내가 가져오지 않는다).

그리고 ⚠️ **한 가지는 아직 장전돼 있다**: **AC 전원에서도 `sleep 1`** — 자리 뜨고 ~11분이면 Mac이 잠들고,
잠들면 데몬 5기가 통째로 멈춰 수집이 죽는다. `caffeinate -s` 상주 한 줄이면 사라진다(§6-1). **안 했다.**

---

## 1. 프로젝트 위치 및 환경
- **공유 메인**: `~/Library/Mobile Documents/.../Ohiselling` — 병행 세션 상시 3~4기. **여기서 코드 작업 금지**
  - 이 세션 착수 시 로컬 `main`이 `origin/main`보다 **164커밋 뒤처져** 있었다. 등록부·HANDOFF를 여기서 읽으면 낡은 것을 본다 → `git show origin/main:<경로>`로 대조
- prod: `sellc.ohitech.co.kr` · **앱 포트 고정 아님**(블루-그린 8001↔8011). 이 세션 시점 **8011**, pid `3413610`
  - 관측(읽기 전용): `ssh sellc.ohitech.co.kr "curl -s http://127.0.0.1:8011/api/coupang/ops/collection-status"` (nginx 경유는 Basic Auth에 막힌다)
- **Mac 페처**: 데몬은 repo가 아니라 `~/.ohisell/tools/` 설치 사본을 돈다. 배포 `bash tools/install_local_runtime.sh`(CAS)
  - 로그: `~/.ohisell_wing_fetcher.log`(WING1) · `~/.ohisell_wing2_fetcher.log`(WING2) · `~/.ohisell_ad_fetcher.log`(광고비) · `~/.ohisell_rocket_fetcher.log` · `~/.ohisell_watchdog.log`

## 2. 이번 세션 완료 목록

**코드 변경 0건 · 커밋(코드) 0건 · 배포 0건 · prod 쓰기 0건.** 산출물은 **라이브 실측과 종결 판단**이다.

### 2-A. 체인 생존 판정 — 로컬과 `origin/main`이 갈렸고, 로컬이 맞았다
- 등록부 `접속-안정화.jsonl` n=4: **로컬 `end_kst`=`2026-08-23 21:40`(닫힘) · `origin/main`은 `null`**(미푸시 상태로 남아 있었다)
- 착지 머지 `e65ece2d`가 `origin/main`에 실재함을 `git branch -r --contains`로 확인 ⇒ **n=4는 종결**로 판정, n=5로 append
- ★단 `83d145dd`(n=4의 「착지 절」 커밋)는 **`origin/feat/collection-mac-liveness`에만 있고 `main`엔 없다**(§5 참조)
- 훅 `[체인] ⛔` 주입도 `pao-논의` n=41 · `sellc-원가-메뉴` n=2 둘뿐 — 접속-안정화를 살아 있다고 말하지 않았다(일치)

### 2-B. ★라이브 전건 실측 — 「지금 깨진 것」은 없다 (2026-08-23 21:55~21:57 KST)
- ✅ **Mac은 오늘 한 번도 안 잤다** — `pmset -g log`의 마지막 실제 `Sleep`은 **2026-08-21 17:54**. 08-23 수면 이벤트 **0건**
- ✅ **데몬 5기 전부 생존** — `com.ohisell.wing`(85773)·`wing2`(85793)·`adcost`(85747)·`ohitech-ad`(85839)·`rocket`(85819), 전부 **17:46 기동·4h11m 가동**(+`scheduler-watchdog`·`vaultpull`)
- ✅ **prod 6레인 전부 `fresh`** — 4레인 `last_success_at` 17:30~17:33 / RG 2레인 08-22 15:2x(RG는 **버튼 전용·간격 3600s**라 정상)
- ✅ **17:32 이후 수집 0건은 «정상»이다** — 4레인 모두 *"갱신 요청(버튼)만 체크·실행"* 폴러다. **누르지 않으면 안 돈다.** 「몇 시간째 안 돌았다」를 고장으로 오독하지 말 것
- ✅ **자동 재로그인은 오늘 전건 성공** — WING2 RG 17:30:45→17:31:25 등, n=4가 센 7회차와 동일. 사람 개입 0

### 2-C. ★실측 중 새로 드러난 것 2건 (이 체인 소관 아님 — 기록만)
- ⚠️ **prod 스케줄러 잡 7종이 오늘 하루 종일 30분 주기로 실패** — `~/.ohisell_watchdog.log`
  - 05:55·11:56·18:16 → `sync_coupang_rg_sizes`·`sync_coupang_rg_inventory`·`sync_coupang_returns`·`sync_coupang_settlement`
  - 06:25·12:26·18:46 → `sync_coupang_rg_orders`·`sync_coupang_coupons`·`sync_coupang_cs`
  - **18:46 이후 22:0x까지 신규 알림 0건**(워치독은 실패 시에만 로그한다 — 「멎었다」인지 「해소됐다」인지 이 로그만으론 못 가른다)
  - **n=1~n=4 인계 어디에도 이 건은 한 줄도 없다.** → **소관: 쿠팡 손익정합 / prod 스케줄러** (이 체인이 가져오지 않는다)
- ⚠️ **20:07~20:09 데몬 3기 동시 `sellc.ohitech.co.kr:443 Read timed out`** — wing1·wing2·ad_fetcher가 같은 2분 창에서 동시에. prod 측 순간 지연으로 보이나 **원인 미규명**. 오늘 이 시간대만. → **소관: 같은 곳**

## 2-3. 착지
- **완료 단계**: 커밋 → push → PR → 리뷰(기록물 예외로 생략) → 머지 — **완주**
- **멈춘 단계**: 없음
- **재개 명령**: 해당 없음
- **좌표**: 커밋 `71b95cad` → PR **#384** → 머지 `9c9d3527` (2026-08-23 22:2x KST)
- **리뷰 판정**: ⚠️ **리뷰 생략: 기록물만** — `.claude/memory/HANDOFF_collection-track-closed_20260823.md` · `.claude/memory/chains/접속-안정화.jsonl` (코드 0파일)
- **착지 전제 검사**(22:1x 실측): L1 — 살아 있는 체인 `pao-논의` n=41 · `sellc-원가-메뉴` n=2 · `근거자료` n=2, **전부 내 대상 아님**(내 브랜치는 이번 세션이 `origin/main`에서 딴 것, 커밋 전건 내 소산) → 진행 / L2 경로 지정 커밋 / L3 내 브랜치는 내 워크트리에만 / L4 머지 전 갱신 / L5 로컬 `main`이 공유 메인 폴더에 잡힘 → **「main에 세워둔다」 생략**

## 3. 확정된 결정사항 (번복 금지)

- ★**체인 `접속-안정화`는 닫혔다** — Jino 21:58 원문: *"이 트랙의 업무는 모두 끝난거 같다. close해도 되지?"*. 다시 열려면 Jino가 먼저 꺼내야 한다
- ★**수집은 MacBook이 깨어 있을 때만 가능하다**(n=4 확정, 불변) — 뚜껑 열림 필수. 내장 디스플레이 1개(MacBookPro18,4)라 클램셸 불성립. `caffeinate`·`pmset -c sleep 0` 둘 다 뚜껑 닫힘 수면을 못 막는다
- ★**「어디서든」은 «Jino가 어디 계시든»이지 «Mac이 어떤 상태든»이 아니다**(n=4)
- ★**버튼 전용 폴러의 «오래 안 돎»은 고장이 아니다** — 4레인은 요청이 있을 때만 fetch한다. `last_success_at`이 몇 시간 전이어도 `state: fresh`가 정상일 수 있다(n=5 실측)
- **`sudo pmset -b disablesleep 1`은 권하지 않는다**(n=4) — 가방 속 과열·배터리 위험
- **미승인 계약 초안은 파일로 남기지 않는다**(n=3·n=4 두 번의 폐기 근거)

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/contracts/CONTRACT_collection_works_everywhere.md` | 계약 정본 — **n=4에서 종결됨** |
| `.claude/memory/chains/접속-안정화.jsonl` | 체인 등록부 — n=5로 닫힘 |
| `.claude/memory/HANDOFF_mac-awake-is-the-hard-limit_20260823.md` | n=4 인계 — 상한 확정·부채 목록의 정본 |
| `tools/wing_browser_fetcher.py` | Wing 데몬. `_landed_on`(2643) · 폴 루프 15초(`cmd_poll`) |
| `tools/coupang_auth.py` | 페처 4종 공용 세션 자가 복구(SSO → Keychain → 사람 호출) |
| `backend/app/routers/coupang_ops.py` | `:1958 collection-status` · `:2447 refresh-status`(UI·데몬 **공용**) |
| `~/.ohisell_watchdog.log` | prod 잡 실패 알림 — §2-C의 근거 |

## 5. 알려진 이슈 / 주의사항

- ⚠️ ★**AC에서도 `sleep 1`** — 자리 뜨고 ~11분이면 잠든다. **지금도 살아 있는 결함**이다. 지금 안 자는 유일한 이유는 `PreventUserIdleSystemSleep` assertion(오늘은 coreaudiod가 들고 있었다)뿐 — 설정이 안전한 게 아니다. 해결책 §6-1, **안 했다**
- ⚠️ ★**n=4의 착지 좌표가 `main`에 없다 — 실측 확인됨**. `origin/main`의 n=4 HANDOFF `## 2-3. 착지`는 **`(Step 6에서 확정)` 자리표시자 그대로**이고(좌표·멈춘 단계·리뷰 판정 3줄), 실제 값을 채운 커밋 `83d145dd`는 **`origin/feat/collection-mac-liveness`에만** 있다(머지 안 됨)
  - 확인: `git show origin/main:.claude/memory/HANDOFF_mac-awake-is-the-hard-limit_20260823.md | sed -n '95,101p'`
  - **이게 정확히 archive-session Step 6이 경고한 「순서 공백 3호」의 재발이다** — 착지 절은 Step 2에 쓰이는데 값은 Step 6에서 생기고, 되돌아가 채운 커밋이 **머지 밖에 남았다**
  - 남은 좌표(이 세션이 대신 머지하지 않았다 — n=4 소관): 커밋 `92e052f2` → PR **#382** → 머지 `e65ece2d`. 살리려면 `gh pr create --head feat/collection-mac-liveness` 한 번이면 된다
- ⚠️ **prod 앱 포트 고정 아님**(블루-그린). 관측 전 `pm2 list` 확인
- ⚠️ **CI는 결제 정지로 죽어 있다** — 전 job `steps=0`. **빨강은 코드 신호가 아니다.** 로컬 스위트로 판단
- ⚠️ **공유 메인 폴더 로컬 `main`이 164커밋 뒤처짐**(이 세션 착수 시). 등록부 생존 판정은 **로컬·`origin/main` 양쪽**을 봐야 한다 — 이번엔 **로컬이 더 새것**이었다(n=4가 자기 행을 닫았는데 미푸시)
- ⚠️ `tools/tests`는 CI가 안 돈다(`ci.yml:43`이 `working-directory: backend`)

## 6. 다음에 할 작업 (미완료)

★**할 일이 아니라 부채 목록이다.** 이 체인은 닫혔다(§3). 새로 열려면 **Jino가 먼저 꺼내야 한다**(전역 §7).

- **닫힌 작업의 목적(원문, 보존용)**: *"나는 다른건 모르겠고 수집이 폰이나 노트북이든 어디서든 문제없이 모두 잘 되도록 만들고 싶어. 이게 내 목표야."* (Jino 2026-08-23 12:03 KST)
- **남은 슬라이스**: **없음**(체인 종결)

### 6-1. ★가장 값싼 실물 개선 — 여전히 안 함 (Jino가 다시 열면 30분)
- [ ] **`caffeinate -s` 상주로 유휴 수면 제거** — 뚜껑 **열린** 구간의 「11분 뒤 잠듦」이 사라진다. sudo 불필요·파일 1개·되돌리기 1명령. 계약 불필요한 소형 작업. **Jino가 이 작업을 다시 열었을 때만 — 그때는 묻지 말고 진행 가능**(되돌릴 수 있고 목표 변경 아님)
- 대안(하드웨어): **외부 모니터 + 전원** → 클램셸 성립 → 뚜껑 닫아도 수집. **Jino 결정 사항**

### 6-2. 남은 부채 («후보»이지 «할 일»이 아니다 — n=4에서 승계, n=5에서 변동 없음)
- [ ] **P2-7 `_landed_on`** — 라이브 7회차 전건 거짓, 회차당 38~40초 순손실. 테스트 5건이 `is False` 방향만 단언
- [ ] **M5b·M12b·M12c** — `_do_run` 배선 3줄에 가드 없음. `sync_playwright()` 안이라 브라우저 없이 못 잡는다 → 새 설계 필요
- [ ] **WING2 VS 자동 복구 라이브 확인** — RG는 확증됨(n=4 §2-B), VS만 남음. 자연 발생 대기
- [ ] **P2-5** `_vs_recover_and_refetch`의 `None`이 「복구 실패」와 「복구 후 로그아웃 재확인」을 뭉갠다
- [ ] **계약 §5① 문구 개정** — RG 2레인이 「`last_success_at` 당회 갱신」을 **구조적으로** 만족 불가. **Jino 결정**(합격기준 변경 = 전역 §1 승인 지점 ①)
- [ ] **합격 ⑤(N일 유지)** — N 미정(계약 제안 14일). **Jino 결정**
- [ ] **「모두」의 범위** — 쿠팡 6레인까지인가, 네이버·스마트스토어까지인가. **Jino 결정**
- [ ] **Mac 생존 가시화(하트비트+화면 4분기)** — n=4에서 설계까지 갔다 의도적으로 접었다. 되살리려면 값이 왜 다시 생겼는지부터

### 6-3. ↗️ 스코프 밖 — 기록만 (이 체인이 가져오지 않는다)
- [ ] **prod 스케줄러 잡 7종 반복 실패**(§2-C) → **소관: 쿠팡 손익정합 / prod 스케줄러**
- [ ] **20:07~20:09 prod HTTPS 동시 타임아웃**(§2-C) → **소관: 같은 곳**

## 7. 새 세션 시작 프롬프트

★**이 체인은 닫혔다 — 이어받을 것이 없다.** 아래는 부채를 다시 열 때만 쓴다.

```
/session-relay 접속-안정화
```
