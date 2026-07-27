# PLAN — 오하이테크(1P 로켓배송) 광고비 페처 자동 재로그인

_작성 2026-07-16 KST · 브랜치 `claude/popups-ad-reading-bugs-0af55a` · Opus 설계_

## 배경 / 문제 (라이브 실측)
- 오하이테크 광고비는 상주 Google Chrome(포트 **9224**)에 CDP attach해 `advertising.coupang.com` report/SALES를 읽음(`tools/ohitech_ad_fetcher.py`).
- 그 Chrome의 쿠팡 세션이 **2026-07-15 23:54 이전부터 로그아웃** → 매시간 auto-run이 `rc=1`(세션 만료)로 실패, 수집 10시간+ 중단, macOS 알림 매시간 스팸.
- ohitech 페처는 **자동 재로그인이 없음**(감지 후 알림만). 3P adcost 페처(`ad_cost_browser_fetcher.py`)엔 이미 Keychain 자동 로그인 존재.
- 로그인 리다이렉트 = `xauth.coupang.com/auth/realms/seller/...` **keycloak** → 3P 페처가 처리하는 폼과 **동일**(`#username`/`#password`/`#kc-login`, 성공=`/marketing/dashboard` 착지). ← 라이브 확인됨.

## 목표
세션 만료 시 ohitech 페처가 Keychain 자격증명으로 **자동 재로그인**하고, 실패할 때만(2FA 등) 알림 — 그리고 그 알림도 **쿨다운**으로 세션당 1회.

## 설계 (adcost `_try_auto_login` 이식, attach 모델에 맞게 단순화)
- **신규** `_try_auto_login(page, cfg)` in `ohitech_ad_fetcher.py`: `#username`(=오하이테크 아이디) fill → `#password`(Keychain) fill → `#kc-login` click → URL 폴링으로 `/marketing/dashboard` 착지 대기. 2FA면 `_otp_input_visible`로 구분 후 사람 호출.
- **신규** `_keychain_get(account)` + 서비스명 `ohisell-ohitech-ad` (adcost의 `ohisell-coupang-ad`와 분리 — 다른 계정 A01029796).
- **config**: `ohitech_login_id`(평문 아이디, 비번 아님) 추가.
- **cmd_run 배선**: `_is_logged_out(page.url)` True → `_notify_mac` 대신 먼저 `_try_auto_login` 시도 → 성공하면 그대로 SALES fetch 계속, 실패해야만 알림(쿨다운 적용).
- **알림 쿨다운**: 마지막 알림 시각을 상태파일에 기록, N시간(예: 6h) 내 재알림 억제. 세션 만료 매시간 스팸 방지.
- **storage_state 불필요**: 상주 Chrome 프로필이 쿠키 보존 → adcost의 `_save_state` 불요.

## 인간 전제 (Claude 불가 — Jino 직접)
- **P1 (지금)**: 9224 Chrome에서 수동 로그인 1회 → 즉시 수집 복구 + 스팸 중단. (창은 이미 로그인 페이지로 띄워둠.)
- **P2**: `security add-generic-password -U -s ohisell-ohitech-ad -a <오하이테크아이디> -w` 로 자격증명 저장. 이후 config `ohitech_login_id`에 같은 아이디 기입.

## 완료 기준 / 검증
- 세션 만료 상태에서 페처 run → 자동 로그인 → 대시보드 착지 → SALES fetch 성공(rc=0). 로그로 확인(원칙 22 라이브).
- 자격증명 오류/2FA 시 무한루프 없이 알림 1회(쿨다운) 후 rc=1.
- codex review PASS(원칙 19 — 인증 로직이라 필수). 비밀번호가 로그/커밋에 안 남는지 확인.
- prod 배포는 `~/.ohisell/` 런타임 동기화 + `launchctl kickstart -k gui/$(id -u)/com.ohisell.ohitech-ad`.

## 스코프 밖
- 3P adcost 페처 로직 변경(별개, 이미 자동로그인 있음).
- 네이버 SA 트랙(무관 — 이건 전용 브랜치 별건 작업).
- 프로드 서버 `sellc.ohitech.co.kr` 간헐 네트워크 타임아웃(별개 인프라 이슈, 로그에 관찰됨).
