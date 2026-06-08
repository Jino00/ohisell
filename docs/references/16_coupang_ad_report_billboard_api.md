# 16. 쿠팡 광고 보고서(Billboard) API — 옵션×일별 광고비 자동화

> 역설계 출처: 2026-06-08 advertising.coupang.com 네트워크 캡처(`tools/ad_endpoint_capture.py` interactive).
> 목적: Jino가 수동 다운로드하던 옵션×일별 광고비 XLSX(`{vendor}_pa_daily_keyword_*.xlsx`)를 페처가 자동 재현.

## 결론: 데이터 소스 위계 (혼동 금지)
| 데이터 | 엔드포인트 | grain | 일별? | 비고 |
|--------|-----------|-------|------|------|
| 오늘 running 누적 | `cmg-api/report/cost` | vendor | ❌(누적) | 헤더 "오늘 광고비" |
| 과거 확정 | `cmg-api/report/SALES` | vendor | ✅ | 현 폴백 |
| 광고그룹 일별 | `cmg-api/report/id/{adGroupId}` (`type:group_sales`) | adGroup | ✅ | 옵션 분해 X(자동타기팅=한 그룹에 다수 옵션) |
| 화면 상품표 | `cmg-api/tableMetric` (`tableType:product_sales`) | 상품(타깃) | ❌(기간합계) | 일별 아님 |
| **옵션×일별** | **Billboard 보고서 XLSX** (아래) | **옵션ID** | ✅ | **유일한 옵션×일별 소스. 기존 파서 사용** |

→ 옵션별 광고비를 일별로 주는 JSON 화면 API는 없음. Billboard 보고서 XLSX가 유일.

## 계정
- 오픽스: vendorId `A01564720`, advertiserId `444899`, loginId `ofixohi`(김진오). 캠페인 9개.
- 호스트 `advertising.coupang.com`, GraphQL 엔드포인트 `POST /marketing-reporting/v2/graphql`.
- Akamai 데이터센터 IP 차단 → **Jino Mac 브라우저 세션(residential)** 으로만 호출 가능.

## 자동화 흐름 (4단계)
### 1) 캠페인 목록 (query)
```
POST /marketing-reporting/v2/graphql
[{"operationName":"GetCampaignListInBillboard",
  "variables":{"startDate":20260601,"endDate":20260607,"reportType":"pa"},
  "query":"query GetCampaignListInBillboard($startDate: Int!, $endDate: Int!, $reportType: ReportType!) {\n  getCampaignList(\n    startDate: $startDate\n    endDate: $endDate\n    reportType: $reportType\n  ) {\n    id\n    name\n    __typename\n  }\n}\n"}]
```
응답: `[{"data":{"getCampaignList":[{"id":"104438581","name":"AI스마트광고",...}, ...9개]}}]`
※ 날짜는 **YYYYMMDD 정수**(epoch 아님).

### 2) 보고서 생성 요청 (mutation)
```
[{"variables":{"reportType":"pa","startDate":20260601,"endDate":20260607,
   "dateGroup":"daily","granularity":"keyword","excludeIfNoClickCount":true,
   "campaignIds":["104498511","104438581",...9개]},
  "query":"mutation ($startDate: Int!, $endDate: Int!, $campaignIds: [ID], $reportType: ReportType!, $dateGroup: DateGroup!, $granularity: Granularity, $excludeIfNoClickCount: Boolean) {\n  requestReport(\n    data: {startDate: $startDate, endDate: $endDate, campaignIds: $campaignIds, reportType: $reportType, dateGroup: $dateGroup, granularity: $granularity, excludeIfNoClickCount: $excludeIfNoClickCount}\n  ) {\n    ...ReportRequest\n    __typename\n  }\n}\n\nfragment ReportRequest on ReportRequest {\n  id\n  requestDate\n  startDate\n  endDate\n  reportType\n  dateGroup\n  granularity\n  excludeIfNoClickCount\n  campaignName\n  campaignCount\n  status\n  isLargeReport\n  schedule {\n    scheduleType\n    title\n    __typename\n  }\n  __typename\n}\n"}]
```
응답: `[{"data":{"requestReport":{"id":"14003681","status":"inprogress",...}}}]` → **report id** 확보.
- `granularity:"keyword"` = 옵션ID 컬럼([8]광고집행 옵션ID,[10]전환매출발생 옵션ID) 포함 포맷 = 기존 파서가 먹는 그것.
- `excludeIfNoClickCount:true` = 클릭 0행 제외(수동 다운로드와 동일).

### 3) 완료 폴링 (query) — status가 completed 될 때까지
```
[{"variables":{"reportType":"pa","page":1,"pageSize":10,"duration":90,"onlyScheduledReport":false},
  "query":"query ($reportType: ReportType!, $page: Int!, $pageSize: Int!, $duration: Int!, $onlyScheduledReport: Boolean) {\n  reportList(\n    data: {reportType: $reportType, page: $page, pageSize: $pageSize, duration: $duration, onlyScheduledReport: $onlyScheduledReport}\n  ) {\n    ...ReportList\n    __typename\n  }\n}\n\nfragment ReportList on ReportList {\n  page\n  pageSize\n  total\n  duration\n  onlyScheduledReport\n  reports {\n    id\n    requestDate\n    startDate\n    endDate\n    reportType\n    dateGroup\n    granularity\n    excludeIfNoClickCount\n    campaignName\n    campaignCount\n    status\n    isLargeReport\n    schedule { title scheduleType createDay requestDate expireAt __typename }\n    __typename\n  }\n  __typename\n}\n"}]
```
응답 `reports[]`에서 우리 id의 `status`가 `"completed"`(생성 중=`"inprogress"`)면 다운로드 가능.

### 4) 다운로드 (GET, 인증 쿠키)
```
GET /marketing-reporting/v2/api/excel-report?id=14003681
→ content-type application/vnd.openxmlformats-officedocument.spreadsheetml.sheet (xlsx bytes)
```
파일명 헤더: `A01564720_pa_daily_keyword_20260601_20260607.xlsx` (44열, 925행/주 — 기존 `_detect_xlsx_format` keyword 포맷).

## 자동화 정책 (D 결정, 2026-06-08 Jino 승인)
- D-1 범위 = 오픽스 9개 캠페인 전부(report/SALES와 동일 vendor). 오하이테크 별도계정은 범위 밖.
- D-2 주기 = 하루 1회(아침). 최근 7일(start=today-7, end=yesterday) daily/keyword 일괄. 매시 호출 X(생성·폴링 비용).
- D-3 수동 업로드(`/api/ad-costs/coupang/upload`)는 폴백으로 유지.
- D-4 백엔드는 기존 파서 재사용(추출) + 토큰 인증 ingest 신설. CoupangAdOptionDaily 테이블 그대로(마이그레이션 불필요).

## 페처 호출 메모
- 모두 same-origin `page.evaluate(fetch, credentials:'include')`. GraphQL POST는 `[{...}]` 배열 형태로 전송.
- 다운로드는 fetch→arrayBuffer→base64→python decode(blob 다운로드 이벤트 대신 직접 fetch가 안정적).
