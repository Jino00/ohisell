# 18. 쿠팡 Wing 판매분석 vendor-summary API (자동 대조 소스)

> 작성 2026-06-14. 읽기전용 라이브 프로브로 확인(원칙22). 정합성 트랙 자동대조 후보 → "Wing 세션 자동화" 트랙으로 이관.

## 목적
종합조망의 우리 계산 매출(revenue_3p/revenue_rg)을 **쿠팡 공식 판매분석 GMV**와 자동 대조(드리프트 감지)하기 위한 backing API. 광고는 이미 report/SALES로 0.02% 자동 일치하므로, 자동대조의 미해결 축은 "매출"이었음.

## 엔드포인트
```
POST https://wing.coupang.com/tenants/rfm-ss/api/business-insight/vendor-summary
```
- 화면: Wing → 비즈니스 인사이트 → 판매분석 (`/tenants/business-insight/sales-analysis`)
- 인증: Wing 세션쿠키 + `x-xsrf-token` 헤더. **모바일 UA 필수**(iPhone Safari UA). `cf_clearance`(Cloudflare, IP+UA 바인딩) 포함 → 쿠키 발급 IP에서 재생해야 통과(타 IP 재생 시 거부 가능).
- 인프라: `coupang_wing_cookie` 테이블(Fernet 암호화)·`parse_curl_cookies`(inbound.py) 재사용. RG 정산(rg_settlement.py)과 동일 표면.

## 요청 body (실측)
```json
{"startDate":"2026-06-14","endDate":"2026-06-14","registrationTypes":["NORMAL","RFM"],"searchIds":[]}
```
- 날짜는 `YYYY-MM-DD`. `registrationTypes`: **NORMAL=3P 마켓플레이스, RFM=로켓그로스(RG)**. searchIds=[] 전체.

## 응답 형태 (실측 2026-06-14, 오픽스 A01564720)
```json
{
  "saleSummaryByDate": [
    {"date":"2026-06-14","registrationType":"NORMAL","unitsSold":14.0,"gmv":223030.0},
    {"date":"2026-06-14","registrationType":"RFM","unitsSold":5.0,"gmv":84500.0}
  ],
  "conversionSummaryByDate": [ {"registrationType":"NORMAL","orders":13.0,...}, {"registrationType":"RFM","orders":5.0,...} ],
  "trafficSummaryByDate": [ ... uniqueVisitor, pageViews (구독 없으면 0) ],
  "summaryMetrics": {"totalGmv":307530.0, "totalUnitsSold":19.0, "totalOrders":18.0, "...Variance":...},
  "lastRefreshTimestamp": "2026-06-14 16:09"
}
```
- **핵심**: `saleSummaryByDate[].gmv`를 registrationType별로 합산 → 3P GMV / RG GMV. `summaryMetrics.totalGmv`=전체. **우리 revenue_3p/revenue_rg와 1:1 매핑.**
- 준실시간(`lastRefreshTimestamp`). 트래픽/검색량은 구독 필요(403/0).

## 라이브 검증 결과 (원칙22 — 우리 파이프라인 역검증)
오픽스(WING1) **닫힌 윈도우 6/8~6/13** 1:1 대조:

| | 우리(command-center) | 쿠팡 공식 GMV | 차이 | 오차% |
|---|---:|---:|---:|---:|
| 3P | 1,724,230 | 1,693,230 | +31,000 | **+1.8%** |
| RG | 1,918,700 | 1,786,500 | +132,200 | **+7.4%** |
| 합계 | 3,642,930 | 3,479,730 | +163,200 | +4.7% |

- **신규 버그 없음**. 오차는 전부 트랙 문서화 잔차: 3P +1.8%=S6 reconcile 후 잔여 stale 취소(D-5, 1.4~3.6%) / RG +7.4%=D-11 gross-vs-net(우리 RG gross, 취소 미차감). → **우리 매출이 쿠팡 공식과 설명 가능한 오차 내 일치함을 라이브 입증.**
- 당일(6/14, 진행중) 비교는 sync 시차로 부정확(우리 RG=0 vs 쿠팡 84,500) — **닫힌 과거일로만 대조할 것.**

## 자동화 차단점 (→ Wing 세션 자동화 트랙)
- body·응답·인증패턴 전부 확보. 유일 난점 = **Wing 세션 freshness**(쿠키 단명, cf_clearance는 requests로 갱신 불가 → headful 브라우저가 주기적으로 Cloudflare 챌린지 해소 필요). 광고 페처(ad_cost_browser_fetcher.py)의 headful Playwright 패턴을 wing.coupang.com용으로 복제하면 해결. RG 정산 자동수집과 공용 인프라.
