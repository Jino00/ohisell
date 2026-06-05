# 쿠팡 광고비 로컬 페처 (Akamai 우회)

## 왜 필요한가
`advertising.coupang.com`은 **Akamai 봇매니저가 데이터센터(prod) IP를 차단**(403)한다.
실측: 동일 쿠키로 prod=403, Jino Mac(residential IP)=201. 따라서 광고비 fetch는
**Jino Mac에서만** 가능하다. 이 페처가 Mac에서 fetch한 뒤 숫자만 prod로 push한다.

```
Jino Mac (residential IP)                          prod (sellc.ohitech.co.kr)
  ad_cost_local_fetcher.py
   ├─ 쿠키파일 로드 → POST report/cost (201)
   ├─ Set-Cookie 회전 → 쿠키파일 갱신(롤링)   ──push 숫자──▶  POST /ad-cost/ingest
   └─ (launchd 매시 실행)                                      └─ CoupangAdCostDaily upsert
                                                                  status=green/last_success
```

## 1회 설정

### (1) prod에 ingest 토큰 설정
prod `.env`에 추가 후 백엔드 reload:
```
AD_INGEST_TOKEN=<랜덤 토큰>
```

### (2) Mac 설정 파일 `~/.ohisell_ad_fetcher.json`
```json
{
  "prod_base_url": "https://sellc.ohitech.co.kr",
  "ingest_token": "<위와 동일한 토큰>",
  "vendor_ids": [104438581, 104997005],
  "cookie_file": "/Users/jino/.ohisell_ad_cookie.txt"
}
```

### (3) 쿠키 import (브라우저 cURL 1회 붙여넣기)
1. advertising.coupang.com 광고 대시보드 → DevTools Network → `cost` 요청 우클릭 → Copy as cURL
2. 텍스트 파일로 저장(예: `~/curl.txt`) 후:
```
python3 tools/ad_cost_local_fetcher.py import ~/curl.txt
```

### (4) 수동 1회 실행해서 확인
```
python3 tools/ad_cost_local_fetcher.py
# 로그: ~/.ohisell_ad_fetcher.log  →  "성공: ... push ..." 확인
```

### (5) launchd 등록 (매시 자동)
`com.ohisell.adcost.plist`의 `__PYTHON__`/`__SCRIPT__`/`__HOME__`을 실제 경로로 치환 후:
```
cp tools/com.ohisell.adcost.plist ~/Library/LaunchAgents/com.ohisell.adcost.plist
launchctl load ~/Library/LaunchAgents/com.ohisell.adcost.plist
```

## 운영 메모
- **롤링 갱신**: 매 성공 시 회전 쿠키를 저장 → Mac이 켜져 있고 ~1.9h(`bm_sv`) 안에 다시
  돌면 재붙여넣기 불필요. Mac이 수시간 꺼지면 쿠키 만료 → (3) 재import.
- **배너**: prod 대시보드는 마지막 push가 26h 초과면(`stale`) 전역 빨간 배너로 알림.
- **보안**: 쿠키파일/설정은 0600, git 미추적. 토큰은 prod .env와 Mac 설정에만 둔다.
- **Akamai 장기 안정성**: curl 재생만으로 `_abck`가 장기간 유효할지는 미확정(JS 센서 없음).
  끊기면 (3) 재import. 잦으면 브라우저(Playwright) 기반으로 격상 검토.
