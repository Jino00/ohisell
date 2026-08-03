# 세션 인수인계: 수집 배너 버튼 수정 + 신선도 워치독 + 페처 세션 자가 복구

> 저장일시: 2026-08-03 12:45 KST · main `f39203b` 기준(+PR #175, 이후 8/3 오후 라이브 검증
> 거쳐 병합 완료 = main `e55e7e2`, 갱신 시각 아래 §2 참조)
> 앞 HANDOFF: `HANDOFF_ohisell-promo-pnl-layer+rg-selfheal-complete_20260728.md`
> 시작점: Jino "SellC에 나오는 3가지 경고가 항상 나온다. 어떻게 해결할 수 있어?"

## 1. 프로젝트 위치 및 환경
- 로컬: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (main 고정, 작업은 워크트리)
- prod: `sellc.ohitech.co.kr` (ssh BatchMode 가능, 배포=`scripts/safe_deploy.sh`만)
- **★codex 쿼터 소진 — 리셋 `2026-08-09 16:16`.** 오늘 PR #169·#171 리뷰로 남은 분을 다 썼다.
  그때까지 **Jino 승인 대체 경로**(신선 컨텍스트 적대적 Claude/Opus 리뷰어 1기, 07-18 승인)를 쓴다.
  실제로 PR #175에서 그 경로가 **codex가 못 잡았을 P1 2건을 잡았다** — 대체재가 아니라 유효한 수단이다.
- 활성 워크트리: `.claude/worktrees/collection-watchdog`(#171, 병합됨) · `.claude/worktrees/fetcher-auto-relogin`(#175, **병합됨, main `e55e7e2`**)

## 2. 이번 세션 완료 목록

### ✅ PR #169 — 배너 '지금 갱신'을 진짜 버튼으로 (병합·배포 완료, main `caf2350`)
Jino 보고 **"버튼을 눌러도 아무 일이 일어나지 않는다"**의 정체: 작동하는 갱신 버튼 5개는 전부
커맨드센터 안에 있고, **실제로 눈에 띄어 누르게 되는 배너의 버튼 2개는 `<Link>`**였다. 특히
신선도 배너는 라벨이 `지금 갱신 →`인데 하는 일은 페이지 이동뿐 — 갱신도 실패 사유도 안 보였다.
- `frontend/src/lib/streamRefresh.ts` 신설 = 갱신 폴링 판정의 단일 구현. CommandCenter에 3벌
  복제돼 있던 루프를 흡수(LESSONS #55 사본 금지). 라이브 사고로 얻은 규칙 4개 보존.
- 로그인 필요 시 **계정명 병기**. 파이프라인 경고 배너는 라벨만 `쿠팡 운영 열기 →`로 정정.
- 부수 정정: 로켓 무응답 안내가 CDP 9223(오하이테크 Wing)을 가리켰으나 **9225**(공급자허브)다.
- codex 1R: P1 1 + P2 3. P2 3건 수용, **P1(RG 부분실패 오탐)은 Jino 결정으로 별건 분리**
  → 별도 세션 진행 중(chip `task_41881732`).
- 라이브: 배너 클릭 → `requested_at 10:08:38` → 데몬 `claimed_at 10:08:44` → 수집 → 배너 소거.

### ✅ PR #171 — 수집 신선도 워치독 (병합·배포 완료, main `f39203b`)
07-27 재설계로 페처 4종의 자동 실행이 **전부 제거**돼(순수 버튼-only) 수집의 유일한 트리거가
사람의 클릭이 됐는데, 그 클릭을 유도하는 장치가 없었다.
- **설계(Jino 승인)**: "평소엔 알림만, 데이터 사라지기 직전엔 자동으로". 평소 3일↑ 낡으면 Slack
  (**창 안 뜸**), 영구 소실 임박(21일)한 스트림만 자동 갱신 요청.
- ★**자동 구조 대상이 `supplier_hub` 하나뿐인 근거**(라이브 로그로 확정, 추정 아님):
  ofix_ad 30일·ofix_sales 90일·발주/정산 90일 창은 6일 공백 후 **스스로 복구됐다**.
  **로켓 판매분석만 요청 창이 7일**이라 그보다 오래된 구멍은 영영 안 메워진다.
- ★**1차 방어**: `sales_days` 7→30(다른 스트림과 대칭). 이 비대칭이 07-28 51일 결손의 기전.
  **라이브 확인**: `판매분석 수집: 30일 요청 → 29일 수집`(11:58).
- ★쿨다운용 저장소 없음 — 잡이 **하루 1회**(09:20) 도는 것이 곧 쿨다운. 시간당이면 알림이
  배너처럼 상시화돼 다시 무시된다(오늘 배너가 당한 실패를 알림에서 반복하지 않는다).
- ★Mac이 자도 됨 — prod DB에 플래그만 세우고 Mac이 깨어나면 집어간다.
- codex 1R: **P1 3 + P2 2, 전건 수용**(아래 §3 참조).
- 라이브: 11:19 실행 시 `notify: ['supplier_hub']` + **Slack 실제 발송**(진짜 고장 탐지).

### ✅ PR #175 — 페처 세션 자가 복구 (병합 완료, main `e55e7e2`)
페처 4종 중 **오픽스 광고 하나만** 자동 재로그인을 갖고 있었다. 나머지는 세션이 풀리면 rc=3으로
멈춰 사람을 불렀다 — **그날 하루에만 Jino가 3번 로그인**(09:31·10:07·11:11).
- `tools/coupang_auth.py` 신설 = 오픽스 구현의 일반화. 3층: ①SSO 재발급(비번 없음) →
  ②Keychain 자동입력 → ③macOS 알림.
- `tools/install_local_runtime.sh`에 공용 모듈 복사를 **페처 루프보다 앞**에 추가(없으면 import
  실패 크래시 루프 — LESSONS #54 기전).
- 적대적 리뷰 1R: **P1 2 + P2 3 수용**, 추가로 구조 위험 1건 자발 수정(§3).
- **★8/3 오후 라이브 검증에서 2층(Keychain)이 처음으로 실제 발동했고 그 자리에서 죽었다.**
  원인 3개를 실측으로 규명해 고쳤다(아래) + 적대적 리뷰가 추가로 P1 2건을 잡았다(LESSONS #85~88).
  최종 라이브 합격 후 병합.

#### 라이브가 드러낸 결함 3건 (8/3 오후, `.env` 아닌 라이브 실측)
1. **진입 클라이언트 오류** — 오하이테크(A01029796)는 쿠팡 **로켓배송(1P) 공급자** 계정인데,
   오픽스에서 그대로 가져온 `_cap_client=WING` 진입을 썼다. WING으로 들어가면 광고센터
   '역할 선택' 화면(마켓플레이스·로켓배송·대행사 카드 3장)에 멈추고 입력칸이 0개다 —
   supplier-hub realm 세션이 살아 있는데도 WING 진입은 튕겼다(13:52 실측). 올바른 체인(document
   요청 추적으로 실측): `/user/login?_cap_client=SUPPLIERHUB&_cap_market=KR` →
   `/login_sxauth?client=SUPPLIERHUB&market=KR` →
   `xauth.coupang.com/auth/realms/seller?client_id=supplier-hub&redirect_uri=advertising.coupang.com/keycloak_callback`
   → `/keycloak_callback` → 대시보드(비번 없이 관통). ★`SUPPLIER`(오타 아닌 실제 다른 값)도
   `_cap_market` 누락도 역할 선택 화면으로 되돌아간다.
2. **폼 셀렉터가 id 의존** — supplier-hub 테마 keycloak 폼에는 id가 하나도 없다:
   `input[name=username]` / `input[name=password]`, 제출은 id 없는 `button[type=submit]`.
   오픽스에서 가져온 `#username`/`#kc-login`은 전부 빗나가 13:34:00에 `wait_for_selector`
   15초 타임아웃으로 죽었다.
3. **권위값 검사가 다음 층을 망가뜨렸다** — `ensure_session`이 ①실패 후 부르는 `verify`(앱 세션
   검사)는 '대시보드에 실제로 들어가지는지'라 본질적으로 goto한다. 그 이동이 ①이 띄워둔
   keycloak 폼을 화면에서 치웠고, ②는 입력칸 없는 화면에서 15초 기다리다 죽었다(13:58:00).
   → ②는 로그인 진입으로 **재진입한 뒤** 채우도록 수정.

#### 적대적 리뷰(codex 쿼터 소진 대체 경로, 신선 컨텍스트 Opus 1기)가 잡은 P1 2건 — 전건 수용
- **거짓 OK(수정이 만든 신규 회귀)**: `_goto_reset`이 goto 실패를 삼켜 `page.url`이 stale로 남으면,
  직전 verify가 남긴 대시보드 URL 때문에 **아무 데도 안 가고 비번도 안 넣고 OK**가 됐다.
  `ensure_session`은 `res==OK`면 단락평가로 verify를 호출조차 안 해 ③알림까지 건너뛴다 →
  사람은 아무 신호도 못 받는다.
- **비밀번호가 회원가입 폼에 들어갈 수 있었다**: keycloak 가입 폼에도 `name=password`가 있고,
  playwright의 `page.fill`/`page.click`은 strict가 아니라 **첫 매치**를 쓴다. 제출만
  `form:has(...)`로 가둔 것은 장식이었고 fill은 스코프가 아예 없었다. → 로그인 폼을 action
  (`login-actions/authenticate`, keycloak 계약)으로 **하나로 특정**해 입력·제출을 전부 그 안에서
  수행. 후보가 2개 이상이면 채택하지 않고 시끄럽게 실패.

#### 부수 실측 (중요)
- **`KEYCLOAK_IDENTITY`는 세션 쿠키**다 — Chrome 프로필의 Cookies DB에 없다(영속: `KEYCLOAK_SESSION`
  12h, `aid` 1h / 비영속: `AUTH_SESSION_ID`, `CAP_AUTH_SESSION`). 페처는 run마다 Chrome을 닫으므로
  **①은 사실상 항상 실패하고 ②(비번 로그인)가 상시 경로**가 된다. "오하이테크 세션 수명
  ≈2시간"의 정체는 시간이 아니라 **Chrome 재기동**이었다.
- **CDP 웹소켓 물림**: 장시간 떠 있던 Chrome(9224, 4시간)에서 HTTP `/json/version`은 64ms로
  응답하는데 `connect_over_cdp`의 **WS 핸드셰이크만 무한 대기**했다. 페처가 여기서 180초를 태워
  rc=1로 끝나 자가 복구 로직은 손도 못 댔다. 해결=그 Chrome 재기동(SIGTERM).
- prod 일시 502(13:12) — 병행 세션 배포 중 재시작으로 추정. 판매분석·프로모션 push 실패했으나
  13:07 회차가 이미 성공해 데이터 유실 없음.

#### 라이브 합격 증거 (수정본, 2회 연속)
```
14:22:12 세션 만료 감지 — 자가 복구 시도(SSO → Keychain 순)
14:23:02 SSO로 복구 안 됨 — Keychain 자동 로그인 시도
14:23:18 자동 로그인 성공 — 목적지 착지 / 세션 자가 복구 성공 — 수집 계속
14:23:19 성공: A01029796 Retail 29일 push → rc=0
prod refresh-status: last_success_at 14:23:19, status green
```
사람 개입 0. 같은 경로로 14:04:59에도 성공. 테스트 28 passed, 변이 5건 전부 잡힘 확인.

## 3. 확정된 결정사항 / 리뷰가 잡은 것

**Jino 결정**
- 재발 방지 방식 = **"평소엔 알림만, 데이터 사라지기 직전엔 자동으로"**(2번). 자동 수집 전면
  부활(3번)도 알림만(1번)도 아니다. 근거: 창이 마구 뜨는 것은 07-27에 이미 문제였고, 영구
  소실은 되돌릴 수 없으니 거기만 예외.
- PR #169의 codex P1(RG 부분실패 오탐) = **별건 분리**. 기존 코드 이식분이고 2026-07-17 RG
  138ms 사고와 트레이드오프가 있어 자체 계약 필요.

**codex/적대적 리뷰가 잡은 내 결함 (전부 "됐다"고 말할 뻔한 것들)**
1. **`tsc --noEmit`이 이 레포에선 무검사** — `frontend/tsconfig.json`이 `{"files": [], "references": [...]}`라
   0개 파일을 검사하고 **항상 exit 0**. 커밋에 "tsc 클린"이라 적었다가 배포 단계에서 발각.
   → 프론트 타입 검증은 반드시 `npm run build`(=`tsc -b && vite build`). LESSONS #68.
2. **워치독 구조 임계가 손으로 고친 config에 기대고 있었다** — `RESCUE_STALE_DAYS=21`이
   `sales_days=30` 전제인데 그 30을 **Mac 로컬 config에서 손으로** 고쳐놨고 코드 기본값은 7이었다.
   초기화되면 구조해도 앞 14일이 영구 소실되고 그 성공이 신선도를 리셋해 워치독은 조용해진다.
   → 가정을 **코드**에 박고(기본값 7→30), 테스트가 페처 소스를 직접 읽어 커플링을 강제.
3. **`in_flight` 영구 침묵** — 요청 플래그는 아무도 claim 안 하면 안 사라진다. Mac이 꺼져 있으면
   영원히 in_flight → 무조건 skip하면 "자동 구조 건 다음날부터 영구 무음". → `requested_at` 노출 +
   24h↑ pending은 `stuck`으로 계속 알림.
4. **구조 실패인데 Slack은 "걸었습니다"라고 거짓 보고** — 문구를 plan으로 만들었다. 게다가 정상
   반환해 APScheduler·health가 초록. → 실제 결과로 문구 생성 + 알림 후 raise.
5. **복구 판정이 출발 URL을 착지로 인정** — 로켓 `SSO_LOGIN_URL=origin+"/"`인데 `_is_landed`도
   origin으로 시작하면 참. 아무 데도 안 가고 OK가 되고 ②③이 통째로 건너뛰어진다.
   ★**내 라이브 시험이 통과한 건 우연**(그 회차가 13초 걸려 경합을 안 밟음) — 행복 경로를
   증명했지 판정의 건전성을 증명하지 못했다. → 판정자 협소화 + 안정화 3000ms +
   `_assert_predicate_sound` 런타임 가드.
6. **playwright `page.fill` 실패 시 call log에 평문 비밀번호**가 예외 메시지로 붙는다 → `str(e)`를
   찍으면 0644 로그 파일에 남는다. 길이 절단(`[:100]`)으로 **우연히** 막히던 것을 구조화.
7. **config 키 이름이 오픽스와 동일**(`ad_login_id`) → 복사 시 오픽스 계정이 오하이테크 프로필에
   로그인하고 vendor_id는 리터럴로 push → **남의 광고비가 우리 vendor로 조용히 적재**.
   → `ohitech_ad_login_id`로 분리.

**자발 수정**: supplier SSO의 `returnUrl`이 **오리진 루트**라 판정자를 `/dashboard`로 좁힌 탓에
정상 복구를 실패로 오판할 여지가 생겼다 → URL 판정을 복구 판정에서 분리하고 **앱 자신의 세션
검사(`verify`)를 권위값**으로 삼았다.

**★변이 테스트가 내 회귀의 무력함을 두 번 잡았다** — 처음엔 소스 문자열만 검사해 판정자를
`return True`로 바꿔도 통과했고, 고친 뒤에도 "로그인 페이지가 아님"만 보는 약화를 못 잡았다.
착지는 부재가 아니라 **존재의 증명**이어야 한다(`about:blank`·오리진 루트 거부 단언 추가).

## 4. 핵심 파일 목록

| 파일 | 역할 |
|---|---|
| `frontend/src/lib/streamRefresh.ts` | 갱신 폴링 판정 단일 구현(배너·커맨드센터 공용) |
| `backend/app/services/coupang/collection_watchdog.py` | 신선도 워치독(알림 + 위급 시 자동 갱신) |
| `backend/app/services/coupang/collection_status.py` | 4스트림 신선도 집계(+`requested_at` 노출) |
| `tools/coupang_auth.py` | 세션 자가 복구 공용(①SSO ②Keychain ③알림) — **미병합** |
| `tools/setup_fetcher_autologin.sh` | ②층 활성화(계정별 1회 실행) — **불필요해짐**(ohitech Keychain 등록이 이미 완료돼 있었음, §6 참조) |
| `tools/install_local_runtime.sh` | Mac 데몬 로컬 사본 설치(공용 모듈 복사 포함) |

## 5. 알려진 이슈 / 주의사항

- **[정정] PR #175는 병합 완료·라이브 합격 증거 확보됨(8/3 오후, main `e55e7e2`)**. 14:22~14:23
  세션 만료 → SSO 실패 → Keychain 자동 로그인 성공 → 수집 계속, 2회 연속(14:04:59도 동일 경로
  성공). §2의 "라이브가 드러낸 결함 3건"을 실측·수정한 뒤의 결과다.
- **[정정] "2층(Keychain) 설정은 완비, 라이브 검증만 남았다"는 오전 기록이었고, 오후 자연
  발동에서 §2의 결함 3개가 드러났다** — 발동 자체는 됐으나 진입 클라이언트 오류·id 셀렉터
  부재·verify의 navigation 부작용으로 죽었다. 수정 후 재발동해 합격.
- **[정정] "오하이테크 세션 수명이 짧다(≈2시간)"는 오독이었다.** `KEYCLOAK_IDENTITY`는
  세션 쿠키(브라우저 프로세스에 묶임)라 페처가 run마다 Chrome을 닫으면 항상 사라진다.
  Jino가 겪은 "≈2시간마다 끊김"의 정체는 시간 경과가 아니라 **Chrome 재기동 빈도**였다.
  "keycloak 12h"는 여전히 오픽스 WING 맥락 수치이고 supplier-hub 적용 여부는 확인 안 됨(별건).
- **Slack 도착 여부 미확인** — 워치독이 11:19·11:32에 `sent: True`로 발송했으나 Jino 확인 전.
  안 왔다면 웹훅 채널(`NAVER_SLACK_WEBHOOK_URL` 폴백 중, `COUPANG_SLACK_WEBHOOK_URL` 미설정) 점검.
- **비활성 스케줄러 잡 3종**(`sync_coupang_ad_cost`·`sync_coupang_rg_settlement`·
  `auto_download_rg_settlement`, 07-27 비활성) — WING1/WING2 쿠키가 red(6월부터)라 그냥 켜면
  실패만 쌓인다. 그리고 `scheduler_health`에서 **제외**돼 있어 꺼진 사실 자체가 경보 안 된다.
- **페처 요청의 간헐 403 20%**(2,437/11,970, 16~18시 KST 집중) — 원인 미규명. 폴링은 대체로 뚫려
  수집을 막고 있진 않다.
- 레포 루트 미커밋 파일(`docs/TRACKS.md`·`track_naver-ad-optimization.md`·`tools/ohitech_billboard_recon.py`
  등)은 **병행 세션 소관 — 건드리지 말 것**.

## 6. 다음에 할 작업

**PR #175 병합 완료(main `e55e7e2`)에 따른 남은 미결:**
1. `KEYCLOAK_IDENTITY` 세션 쿠키 문제 → 매 갱신마다 비번 로그인 경로를 탄다. Chrome 상주
   유지 또는 오픽스식 `storage_state` 파일 보존이 대안(별건).
2. ohitech `DASH_URL`이 아직 `_cap_client=WING` — 데이터 fetch는 정상이나 verify가 이 URL로
   재진입하므로 false negative 여지(방향은 안전=사람 호출 쪽으로 치우침).
3. 로켓 공급자허브의 ②(Keychain) 재진입 수정은 라이브 미검증(구조상 ①의 이동을 그대로
   반복하므로 동치로 판단했으나 별도 확인은 안 함).
4. codex 쿼터 리셋 `2026-08-09 16:16` — 리셋 후 소급 교차 리뷰 정상화.
5. `tools/setup_fetcher_autologin.sh` 실행 항목은 **불필요해짐** — Keychain 등록(ohitech)은
   이미 돼 있었고 그게 8/3 오후 ②층 성공의 전제였다.

**이전부터 이어지는 미결:**
6. **Phase B — 오하이테크 광고비 옵션 귀속**. `coupang_ad_option_daily`에 A01029796 **0행**
   (오픽스 3P만 6,177행). ★"배선하면 됨"이 아니라 **가부 미확정**이다 —
   `tools/ohitech_billboard_recon.py`(07-11 작성, 결론 없이 방치)가 "1P가 옵션 granularity
   Billboard를 주는가"를 검증하려던 S0 정찰이다. 세션 만료로 실행이 막혔던 적 있음
   (`[S0][세션만료]`). 자동 복구가 붙었으니 재시도 가능. 붙으면 프로모션 순이익이 N/A→숫자.
7. **Phase C — 08-20 판매분석 무료체험 종료 대응**. 유료 전환은 **Jino 판단**, 모델은 판단
   재료(끊기면 뭐가 죽는지·대체 경로·D-CPP-5 배선 실효)를 만든다.
8. RG 부분실패 오탐(별건 세션 진행 중, chip `task_41881732`)
9. 수집 자동성 잔여 — 비활성 크론 3종 처리 방침, 간헐 403 규명

## 7. 새 세션 시작 프롬프트

`.claude/memory/HANDOFF_collection-buttons+watchdog+auto-relogin_20260803.md` 읽고 이어서 작업해줘
