# 세션 인수인계: ohisell-coupang-adreport
> 저장일시: 2026-05-19
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && uvicorn app.main:app --reload`
- 프론트엔드 실행: `cd frontend && npm run dev`
- 프로덕션 URL: https://sellc.ohitech.co.kr
- 서버 SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`
- 서버 경로: `/home/ubuntu/ohisell/`
- 주요 환경변수: COUPANG_WING1_VENDOR_ID, COUPANG_WING2_VENDOR_ID, NAVER_SA_API_KEY, META_ACCESS_TOKEN

## 2. 이번 세션 완료 목록

- ✅ `backend/app/routers/ad_costs.py`: `_detect_xlsx_format()` 헤더 자동 감지 추가 — adGroup/keyword 포맷 모두 처리
- ✅ `backend/app/routers/ad_costs.py`: `upload_coupang_ad_xlsx()` 수정 — 업로드 한 번으로 `ad_costs` + `coupang_ad_report` 동시 upsert
- ✅ `backend/app/models.py`: `CoupangAdReport` 모델 추가 (report_date, sell_type, vendor_id, impressions, clicks, ad_spend, orders, sales_qty, conversion_revenue)
- ✅ `backend/alembic/versions/79c5bf56a7eb_add_coupang_ad_report_table.py`: DB 마이그레이션 생성 및 프로덕션 적용
- ✅ `backend/app/routers/coupang_report.py`: 신규 생성 — `GET /api/ads/coupang/report` (기간별 sell_type 집계, CTR/CVR/ROAS 계산)
- ✅ `backend/app/main.py`: coupang_report 라우터 등록
- ✅ `frontend/src/pages/AdReport.tsx`: 신규 생성 — 기간 선택 + 판매방식별 성과 표 + 전체합계 행. 별도 XLSX 업로드 버튼 제거
- ✅ `frontend/src/lib/api.ts`: CoupangAdReportRow, CoupangAdReportResponse 타입 + fetchCoupangAdReport() 추가
- ✅ `frontend/src/App.tsx`: `/ad-report` 라우트 추가
- ✅ `frontend/src/components/Layout.tsx`: 사이드바에 "광고 리포트" 메뉴 추가
- ✅ 프로덕션 배포 완료 (frontend dist + backend rsync + alembic upgrade + pm2 restart)
- ✅ git commit: `9832aea` (ad_costs + AdReport.tsx 통합), `20d00c4` (progress 갱신)

## 3. 확정된 결정사항

- **XLSX 업로드 단일 경로**: 설정 페이지의 쿠팡 광고비 업로드(`POST /api/ad-costs/coupang/upload`) 하나로 통합. AdReport 페이지에 별도 업로드 버튼 없음
- **광고 리포트 데이터 소스**: `coupang_ad_report` 테이블 (pa_daily_keyword XLSX에서 자동 파싱)
- **포맷 자동 감지**: `_detect_xlsx_format(headers)` — keyword/adGroup 포맷 모두 처리, 헤더 키워드 매칭
- **sell_type 컬럼**: 쿠팡 XLSX C열 값 그대로 사용 (3P=윙, 2P=로켓그로스, Retail=로켓배송)

## 4. 핵심 파일 목록

| 파일 | 역할 |
|------|------|
| `backend/app/routers/ad_costs.py` | 쿠팡 XLSX 업로드 → ad_costs + coupang_ad_report 동시 저장 |
| `backend/app/routers/coupang_report.py` | GET /api/ads/coupang/report — 기간별 집계 API |
| `backend/app/models.py` | CoupangAdReport 모델 |
| `frontend/src/pages/AdReport.tsx` | 광고 리포트 페이지 UI |
| `frontend/src/lib/api.ts` | fetchCoupangAdReport 함수 + 타입 |

## 5. 알려진 이슈 / 주의사항

- **coupang_ad_report 테이블이 현재 비어있음**: 기존 업로드한 XLSX는 이전 코드로 처리돼 ad_costs만 저장됨. 광고 리포트에 데이터가 나오려면 XLSX를 다시 업로드해야 함.
- **업로드 대상 파일**: 쿠팡 광고 관리 → 리포트 → `pa_daily_keyword_{vendor_id}_...xlsx` 형식
- **coupang_report.py 중복 주의**: `/api/ads/coupang/upload` 엔드포인트가 `ad_costs.py`에도 있고, `coupang_report.py`에 별도 upload 엔드포인트가 있음 (현재는 ad_costs.py 것만 사용, coupang_report.py upload는 미사용 상태)

## 6. 다음에 할 작업 (미완료)

- [ ] 쿠팡 광고 XLSX 과거분 다시 업로드 → 광고 리포트 데이터 채우기
- [ ] coupang_report.py의 미사용 upload 엔드포인트 정리 (제거 or 유지 결정)
- [ ] 로켓배송 실제 매출 데이터 입력 (Settings 페이지 활용)
- [ ] nginx IP 화이트리스트 또는 Basic Auth (보안 — Jino 결정 시 즉시 적용 가능)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-adreport_20260519.md 읽고 이어서 작업해줘
```
