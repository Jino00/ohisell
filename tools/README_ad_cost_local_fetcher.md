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

→ **해법: aid 만료 시 keycloak 세션으로 aid를 자동 재발급한다.** WING 로그인 시작 URL로
진입하면 keycloak authorize(Akamai JS 챌린지 ~16초)를 거쳐 비번 없이 callback → aid
재발급. 이 SSO 경로는 **headful(창)에서만 통과**한다(headless는 xauth Akamai가
Access Denied로 차단).

### 운영 모델: "버튼 트리거" (2026-06-06 전환)
매시 자동 fetch는 headful 창이 매시 뜨고(SSO 필수) Mac을 밤새 켜야 keycloak 12h가 유지되는
부담이 있었다. 그래서 **"대시보드 버튼을 누를 때만 갱신"**으로 바꿨다. Mac에는 가벼운
**poll 데몬**이 상주하며(15s마다 prod에 갱신 요청만 확인 — 창 안 뜸), 버튼을 누른 경우에만
headful fetch(창 ~20초)를 한다.

```
[쿠팡 운영페이지 '📣 광고비 갱신' 버튼]            prod (sellc.ohitech.co.kr)
        │ 클릭                                       POST /ad-cost/request-refresh
        ▼                                            → refresh_requested_at = now
  (Mac poll 데몬, 15s마다 GET refresh-status)        ◀──── 플래그 확인(창 안 뜸)
        │ requested=true 감지
        ▼  flock 획득 → POST refresh-claim(원자적 소비)
  headful 브라우저: fetch report/cost (201)
   ├─ aid 만료면 → WING 로그인 URL → keycloak SSO 재발급(~16s)
   ├─ keycloak도 만료면 → 같은 창에서 로그인 대기(아침 첫 클릭이 로그인 겸함)
   └─ push 숫자 ──────────────────────────────▶  POST /ad-cost/ingest
                                                  → coupang_ad_cost_daily upsert
        대시보드는 last_success_at 갱신을 폴링(최대 215s)해 "오늘 광고비" 리로드
```

평소엔 창이 전혀 안 뜬다. 버튼을 누른 그 한 번만 ~20초 창이 떴다 닫힌다.
일별 광고비는 쿠팡 운영페이지 헤더 "오늘 광고비"에 표시된다(coupang_ad_cost_daily).

## 1회 설정

### (1) prod에 ingest 토큰
prod `.env`에 `AD_INGEST_TOKEN=<토큰>` 추가 후 백엔드 reload. (이미 설정됨)

### (2) Mac 설정 파일 `~/.ohisell_ad_fetcher.json`
```json
{
  "prod_base_url": "https://sellc.ohitech.co.kr",
  "ingest_token": "<prod와 동일 토큰>",
  "vendor_ids": [104438581, 104997005],
  "ad_vendor_code": "A01564720",
  "sales_days": 7,
  "state_file": "/Users/jino/.ohisell_ad_state.json"
}
```
- `ad_vendor_code`(**옵션 보고서 필수**): 광고 보고서 vendor 코드(오픽스=`A01564720`). 미설정 시
  옵션×일별 보고서 적재를 **건너뜀**(잘못된 vendor 귀속 방지, fail-closed). `vendor_ids`(광고노드 숫자ID)와 다름.
- `sales_days`: report/SALES·옵션 보고서가 받는 최근 일수(기본 7, 어제까지).

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
버튼 트리거 방식에서는 보통 **아침 첫 클릭이 로그인을 겸한다**(세션 만료 시 버튼이 띄운
창에서 로그인하면 그대로 fetch 진행). 별도 login은 최초 1회나 문제 발생 시에만.

### (5) 수동 1회 실행 확인 (선택)
```
./.venv/bin/python ../tools/ad_cost_browser_fetcher.py        # run: 1회 fetch·push
./.venv/bin/python ../tools/ad_cost_browser_fetcher.py poll   # poll: 상주 데몬(데몬 동작 확인용)
# 로그 ~/.ohisell_ad_fetcher.log 에 "성공: ... push ..." 확인
```

### (6) launchd 등록 (상주 poll 데몬)
`com.ohisell.adcost.plist`의 `__PYTHON__`(backend/.venv/bin/python3),
`__SCRIPT__`(tools/ad_cost_browser_fetcher.py 절대경로), `__HOME__`을 치환 후:
```
launchctl unload ~/Library/LaunchAgents/com.ohisell.adcost.plist 2>/dev/null  # 기존 매시 데몬 내림
cp tools/com.ohisell.adcost.plist ~/Library/LaunchAgents/com.ohisell.adcost.plist
launchctl load ~/Library/LaunchAgents/com.ohisell.adcost.plist
launchctl list | grep ohisell   # 가동 확인
```
plist는 `poll` 인자 + `KeepAlive`(죽으면 자동 재시작)로 상주 데몬을 띄운다.

## 운영 메모
- **버튼 트리거**: 쿠팡 운영페이지 "📣 광고비 갱신" 버튼을 누를 때만 fetch한다. 평소 데몬은
  15s마다 prod에 갱신 요청만 확인(창 안 뜸). 버튼 누른 그 한 번만 ~20초 창이 떴다 닫힌다.
- **아침 첫 클릭 = 로그인**: Mac을 밤새 꺼서 keycloak(12h)이 만료됐으면, 아침 첫 버튼 클릭이
  띄운 창에서 로그인하면 된다(최대 180s 대기). 이후 클릭은 창만 잠깐 뜨고 자동.
- **세션 갱신**: fetch 1회가 keycloak을 12h로 다시 갱신 → 낮 동안 추가 클릭은 로그인 불필요.
- **배너**: prod 대시보드는 status=red 또는 마지막 push 26h 초과(stale)면 전역 빨간 배너로 알림.
- **Mac 상주 필요**: Mac이 꺼져 있으면 버튼을 눌러도 데몬이 못 받는다(데이터는 마지막 값 유지).
- **데몬 로그**: `~/.ohisell_ad_fetcher.log`. launchd stdout/err: `~/.ohisell_ad_fetcher.launchd.log`.

## 참고: curl 방식(ad_cost_local_fetcher.py)은 1회용으로 폐기
같은 디렉터리의 `ad_cost_local_fetcher.py`는 curl 재생 방식이라 1회만 동작한다(위 근거).
브라우저 방식(`ad_cost_browser_fetcher.py`)을 사용한다. curl 버전은 진단 기록용으로 보존.
