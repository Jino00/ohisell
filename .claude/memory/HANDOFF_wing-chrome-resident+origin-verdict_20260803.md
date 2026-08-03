# HANDOFF — WING2 Chrome 상주화 요청 → 로그아웃 오분류 규명·수정 (2026-08-03 저녁)

워크트리 `strange-nash-317ccc` / 브랜치 `claude/wing2-resident-chrome` / base `6ef4196`(PR #188 병합 후)

## Jino 시작점

> "WING2(오하이테크) 페처의 Chrome을 WING1처럼 **상주** 형태로 바꿔주세요. 지금은 회차마다
> 새로 띄우고 닫아서 Akamai 봇 센서가 예열될 틈이 없고, 그 결과 API 호출이 404로 막힙니다."

라이브 대조를 첨부해 주셨다 — 같은 시각·같은 코드인데 WING1(adopt)=성공 4건,
WING2(매 회차 신규)=15:22·15:25·15:27 3연속 rc=1.

## 결론: 상주화는 옳았고, 진단은 Akamai가 아니었다

**404의 정체 = 로그아웃.** 허가받아 9223 Chrome을 닫고 페처와 **동일한 argv**로 재현(15:41):

```
page.url : https://xauth.coupang.com/auth/realms/seller/protocol/openid-connect/auth?...
title    : Sign in to seller
body     : coupang | 판매자 로그인 | 아이디 찾기 비밀번호 찾기 ...
```

로그인 페이지 오리진으로 나간 same-origin POST라 `xauth.coupang.com/tenants/rfm/...`는
실제로 없는 경로 → 404. 본문의 `/NgBm_rkKf/…`는 **쿠팡 404 페이지에 얹힌** Akamai 스크립트다.
봇 차단이면 403/429이지 404가 아니다. 4분간 11회 재탐색해도 회복 없음(예열 대기는 대증요법조차 못 된다).

**왜 Chrome을 닫으면 로그아웃되나** — 쿠키 실측, 두 계정 공통:

| 쿠키 | WING1 | WING2 |
|---|---|---|
| `JSESSIONID` (wing) | 세션쿠키 — Chrome 종료 시 소멸 | 세션쿠키 — 종료 시 소멸 |
| `KEYCLOAK_IDENTITY`/`_SESSION` (xauth) | 없음 | 없음 |

무언 재발급 수단이 없으니 **Chrome 종료 = 로그인 소멸**이 구조다. 즉 로그인 횟수를 정하는 건
세션 수명이 아니라 **Chrome 수명**이다.

**★WING1이 멀쩡해 보인 건 상주 덕분이 아니다.** 오늘 WING1 로그 실측 — fresh launch 4회차
**전부 로그아웃**이었고 Jino가 그때마다 창에서 수동 로그인했다:

```
12:43:20 기동 → 12:43:27 로그인 안내 → 12:44:13 성공 (46초 = 타이핑)
12:55:35 기동 → 12:55:43 로그인 안내 → 12:56:05 성공 (22초)
13:06:20 기동 → 13:06:29 로그인 안내 → 13:07:03 성공 (34초)
```

그 회차들이 `로그인 대기 위해 Chrome 창 유지`로 끝나 창이 안 닫혔고 — **9222의 2시간 26분짜리
"상주" Chrome은 설계가 아니라 그 login-wait의 잔재였다.** 상주용 launchd 잡은 없다
(`com.ohisell.wing-chrome`은 07-27 `0797f19`에서 폐기).

## 진짜 결함 — 로그아웃이 로그인 안내로 이어지지 않았다

WING2는 같은 로그아웃인데 로그인 창이 **안 떴다.** 원인은 `_rg_off_origin`:

```python
if url and not url.startswith("about:") and _RG_ORIGIN_HOST not in url:   # "wing.coupang.com"
```

Keycloak 로그인 URL은 돌아갈 주소를 쿼리에 싣는다 —
`...auth?...&redirect_uri=https%3A%2F%2F**wing.coupang.com**%2F...`.
부분문자열 검사가 거기 걸려 **로그인 페이지 위에 서서 "오리진 유지"라고 답했다.**
→ 로그아웃이 AUTH로 확증되지 못하고 404 → UNKNOWN → "업스트림 장애(로그아웃 아님)"
→ 로그인 창이 안 뜸 → **아무도 로그인하지 못한 채 영구 침묵.**
`wing2-rg-settlement-restored_20260727`의 RG 50일 침묵과 형태가 같다.
함수 docstring은 이 로그아웃 형태를 정확히 기술해 놓고, 그걸 볼 수 없는 검사를 썼다.
기존 회귀 테스트(`test_off_origin_is_auth_evidence`)는 쿼리 **없는** URL을 써서 통과시켰다.

## 한 일 (커밋 `96379d8`)

1. **`_rg_off_origin` → `urlsplit().hostname` 정확 일치.** 접미사 위장
   (`wing.coupang.com.evil.example`)도 함께 막힌다.
2. **`chrome_resident` 설정** — 내가 띄운 창을 작업 후 닫지 않는다(`_ChromeOwner.resident`).
   다음 회차가 adopt해 세션이 이어진다.
   - `keep_open`과 **별도 플래그**인 이유: `_do_rg_run`이 로그인 성공 후 `keep_open=False`로
     되돌리므로, 얹었다면 그 한 줄이 상주를 조용히 해제했을 것이다.
   - **SIGTERM 회수에서도 제외** — 배포마다 데몬을 bootout 하는데 거기서 닫으면
     **재배포가 곧 로그아웃**이 된다.
   - ★07-27에 폐기한 상주 supervisor(launchd KeepAlive)는 **되살리지 않았다.** 폐기 사유가
     "사람이 창을 닫으면 10~30초 뒤 되살아난다"였기 때문. 창이 새로 뜨는 순간은 예나 지금이나
     '갱신 버튼 직후' 하나뿐이고, 달라진 건 **닫지 않는다**는 것뿐이다. 별도 잡이 아니라
     소유권 규칙 안에 두어 프로필 기동을 두 주체가 다투지도 않는다.
3. 회귀 테스트 9건(전부 수정 전 코드에서 실패함을 `git stash` 대조로 확인). 4420 passed.
4. 배포: `install_local_runtime.sh --files-only`(CAS 통과). 설정 `chrome_resident:true`를
   **WING1·WING2 둘 다** 적용(`~/.ohisell_wing{,2}_fetcher.json`, `.bak.<시각>` 백업 있음).
   데몬 2종 kickstart.

## 라이브 증거

| 합격기준 | 결과 |
|---|---|
| ③ 로그아웃을 로그인 필요로 판정 | ✅ 17:03:53 `RG 진입 프로브 auth — 로그인 필요(오리진 이탈 — url=https://xauth.coupang.com/...)` — 구코드는 같은 상태에서 `unknown 비200(404)` |
| ② Chrome 강제 종료 → 자동 기동·유지 | ✅ 9223 전멸(CDP 000) → 17:04:33 `Chrome 기동(PID 14208, CDP 9223) — 상주(작업 후 닫지 않음)` → 17:04:40 `상주 모드 — Chrome(PID 14208) 유지` → 페처 종료 후에도 14208 생존 |
| ① adopt + push 성공 | ⏳ **미완 — WING2 로그인 대기 중**(아래) |
| 회귀 테스트 | ✅ 신규 9건 수정 전 실패 확인 / 4420 passed |

## ★미결

1. **WING2 로그인이 필요하다.** 재현하느라 9223을 닫아서 오하이테크 세션이 끊겼다(허가된 재현).
   Chrome(PID 14208)이 로그인 페이지를 띄운 채 떠 있으니 거기서 로그인하면 된다.
   로그인 후 `RG 정산 갱신` 버튼 1회 → `기존 Chrome(CDP 9223) 감지 — adopt` + `push 성공 N`이
   찍히면 합격기준 ①이 닫힌다. **그 전까진 "고쳤다"고 말할 수 없다.**
2. **codex PR 경계 리뷰 미실행** — 쿼터 소진(리셋 2026-08-09 16:16). 부채로 남긴다.
3. 상주 모드의 장기 거동 미검증 — 세션이 며칠 유지되는지는 관측이 쌓여야 안다. 로그인 요구
   빈도가 눈에 띄게 줄지 않으면 세션 자체에 별도 만료가 있다는 신호다.
4. WING1도 상주로 켰다 — 오픽스 창이 계속 떠 있는 게 거슬리면 `~/.ohisell_wing_fetcher.json`의
   `chrome_resident`를 `false`로 되돌리면 즉시 옛 동작이다(코드 변경 불요).
