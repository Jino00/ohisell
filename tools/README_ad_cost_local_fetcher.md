# 쿠팡 광고비 로컬 페처 (Akamai 우회, 브라우저 방식)

## 왜 이렇게 하나 (실측 근거)
`advertising.coupang.com`은 **Akamai 봇매니저**로 보호된다.
- prod(데이터센터 IP) = **403 차단**. Jino Mac(residential IP) = 통과.
- curl/requests로 쿠키 재생 = **1회용**: 첫 요청 성공 직후 세션 토큰이 회전·무효화되어
  두 번째 호출부터 Keycloak 로그인으로 튕김 → 자동화 불가.
- **실제 브라우저(Playwright 영속 프로필)** 만 토큰 회전을 자동 유지하고 Akamai를 통과 →
  한 번 로그인하면 며칠~몇 주 무인 동작.

```
Jino Mac (실제 브라우저, residential IP)             prod (sellc.ohitech.co.kr)
  ad_cost_browser_fetcher.py  (launchd 매시)
   ├─ 영속 프로필로 대시보드 열기(세션 유지)
   ├─ page.evaluate fetch report/cost (201)  ──push 숫자──▶  POST /ad-cost/ingest
   └─ 로그아웃 감지 시 'login' 재실행 안내                      └─ CoupangAdCostDaily upsert
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
  "state_file": "/Users/jino/.ohisell_ad_state.json",
  "headless": true
}
```
세션은 `state_file`(Playwright storage_state, 세션쿠키 포함)에 저장된다(0600). 영속
프로필을 쓰지 않는 이유: Playwright 영속 프로필은 세션쿠키를 컨텍스트 종료 시 버리기 때문.

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
세션이 끊기면(며칠~몇 주 뒤) run 로그에 "세션 만료 — login 재실행" + 대시보드 배너 →
(4)만 다시 실행하면 됨.

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
- **세션 유지**: 실제 브라우저 프로필이 로그인을 며칠~몇 주 유지. 로그아웃되면 로그에
  "세션 만료 — login 재실행" → (4)만 다시.
- **배너**: prod 대시보드는 마지막 push가 26h 초과면 전역 빨간 배너로 알림.
- **headless**: 기본 true. Akamai가 막으면 설정에서 `"headless": false`로(창이 뜸).
- **Mac 상주 필요**: Mac이 꺼져 있으면 그 시간 동안 push 안 됨(데이터는 마지막 값 유지).

## 참고: curl 방식(ad_cost_local_fetcher.py)은 1회용으로 폐기
같은 디렉터리의 `ad_cost_local_fetcher.py`는 curl 재생 방식이라 1회만 동작한다(위 근거).
브라우저 방식(`ad_cost_browser_fetcher.py`)을 사용한다. curl 버전은 진단 기록용으로 보존.
