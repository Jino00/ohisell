# 쿠팡 광고비 로컬 페처 (Akamai 우회, 브라우저 방식)

## 왜 이렇게 하나 (실측 근거)
`advertising.coupang.com`은 **Akamai 봇매니저**로 보호된다.
- prod(데이터센터 IP) = **403 차단**. Jino Mac(residential IP) = 통과.
- curl/requests로 쿠키 재생 = **1회용**: 첫 요청 성공 직후 세션 토큰이 회전·무효화되어
  두 번째 호출부터 Keycloak 로그인으로 튕김 → 자동화 불가.
- **실제 브라우저(Playwright)** 만 토큰 회전을 자동 유지하고 Akamai를 통과한다.

### 세션 만료 구조 (2026-06-06 실측 확정)
쿠키 만료 시각을 직접 측정한 결과 — 광고 API 인증 쿠키 `aid`는 **발급 시각 + 정확히
1시간 절대 만료**(fetch를 자주 해도 회전 안 됨). 그래서 매시 run은 거의 항상 aid 만료
상태로 시작한다. 반면 SSO 원천인 `KEYCLOAK_SESSION`(xauth.coupang.com)은 **12시간**
살아있고, **SSO 재발급을 할 때마다 다시 12h로 갱신**된다.

→ **해법: 매 run이 keycloak 세션으로 aid를 자동 재발급한다.** WING 로그인 시작 URL로
진입하면 keycloak authorize(Akamai JS 챌린지 ~16초)를 거쳐 비번 없이 callback → aid
재발급. 이 SSO 경로는 **headful(창)에서만 통과**한다(headless는 xauth Akamai가
Access Denied로 차단). 매시 run이 keycloak을 12h로 갱신하므로, Mac이 켜져 매시 도는 한
**재로그인은 사실상 불필요**(keycloak이 만료될 틈이 없음).

```
Jino Mac (실제 브라우저=headful, residential IP)       prod (sellc.ohitech.co.kr)
  ad_cost_browser_fetcher.py  (launchd 매시, headful)
   ├─ storage_state 로드 → 대시보드 fetch 시도
   ├─ aid 만료(1h)면 → WING 로그인 URL → keycloak SSO 재발급(~16s) → 새 aid+keycloak 12h
   ├─ page.evaluate fetch report/cost (201)  ──push 숫자──▶  POST /ad-cost/ingest
   └─ keycloak도 만료 시만 'login' 재실행 안내                 └─ CoupangAdCostDaily upsert
                                                                  status=green/last_success
```

## 1회 설정

### (1) prod에 ingest 토큰
prod `.env`에 `AD_INGEST_TOKEN=<토큰>` 추가 후 백엔드 reload. (이미 설정됨)

### (2) Mac 설정 파일 `~/.ohisell_ad_fetcher.json`
```json
{
  "prod_base_url": "https://sellc.ohitech.co.kr",
  "ingest_token": "<prod와 동일 토큰>",
  "vendor_ids": [104438581, 104997005],
  "state_file": "/Users/jino/.ohisell_ad_state.json"
}
```
세션은 `state_file`(Playwright storage_state, 세션쿠키 포함)에 저장된다(0600). 영속
프로필을 쓰지 않는 이유: Playwright 영속 프로필은 세션쿠키를 컨텍스트 종료 시 버리기 때문.
`"headless"` 키는 무시된다 — run은 SSO 재발급을 위해 항상 headful이다.

### (3) 의존성 설치 (Mac)
```
cd backend && ./.venv/bin/pip install -r ../tools/requirements-local.txt
./.venv/bin/python -m playwright install chromium
```

### (4) 1회 로그인 (창이 뜸 → 직접 로그인, Enter 불필요)
```
cd backend && ./.venv/bin/python ../tools/ad_cost_browser_fetcher.py login
# 뜬 브라우저에서 advertising.coupang.com 로그인 → 대시보드 보이면 자동 감지·저장(창 닫지 말 것)
```
이후 매시 run이 keycloak 세션을 12h로 갱신하므로 재로그인은 거의 불필요하다. keycloak도
만료된 경우(Mac이 12h 이상 꺼져 있었던 등)만 run 로그에 "세션 만료 — keycloak 세션도
만료. login 재실행" + 대시보드 배너가 뜨고, 그때 (4)만 다시 실행하면 된다.

### (5) 수동 1회 실행 확인
```
./.venv/bin/python ../tools/ad_cost_browser_fetcher.py
# 로그 ~/.ohisell_ad_fetcher.log 에 "성공: ... push ..." 확인
```

### (6) launchd 등록 (매시 자동)
`com.ohisell.adcost.plist`의 `__PYTHON__`(backend/.venv/bin/python3),
`__SCRIPT__`(tools/ad_cost_browser_fetcher.py 절대경로), `__HOME__`을 치환 후:
```
cp tools/com.ohisell.adcost.plist ~/Library/LaunchAgents/com.ohisell.adcost.plist
launchctl load ~/Library/LaunchAgents/com.ohisell.adcost.plist
```

## 운영 메모
- **세션 유지**: 매시 run의 SSO 재발급이 keycloak을 12h로 갱신 → Mac이 켜져 매시 도는 한
  재로그인 불필요. keycloak까지 만료된 경우만 "세션 만료 — keycloak 세션도 만료" → (4)만 다시.
- **창이 매시 뜬다**: SSO 재발급이 headful 필수라 run마다 Chrome 창이 ~20초 떴다 닫힌다
  (aid 살아있어 재발급 불필요한 경우는 더 짧음). 거슬리면 launchd 주기를 늘릴 것
  (광고비는 일 누적값이라 2~3시간 간격도 충분).
- **배너**: prod 대시보드는 status=red 또는 마지막 push 26h 초과(stale)면 전역 빨간 배너로 알림.
- **Mac 상주 필요**: Mac이 꺼져 있으면 그 시간 동안 push 안 됨(데이터는 마지막 값 유지).

## 참고: curl 방식(ad_cost_local_fetcher.py)은 1회용으로 폐기
같은 디렉터리의 `ad_cost_local_fetcher.py`는 curl 재생 방식이라 1회만 동작한다(위 근거).
브라우저 방식(`ad_cost_browser_fetcher.py`)을 사용한다. curl 버전은 진단 기록용으로 보존.
