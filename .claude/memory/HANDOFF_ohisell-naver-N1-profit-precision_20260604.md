# 세션 인수인계: 네이버 N1 이익 정밀화 — 건별정산 실측 수수료 (완료)
> 저장일시: 2026-06-04
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 진행 = 네이버 커머스 API 전 기능 트랙(track_naver-full-integration.md). **N1 완전 완료**(일별정산+건별정산 이익정밀화). 다음 = N2 통계.

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (로컬 venv=Python 3.14)
- 프론트: `cd frontend && npm run build` (Vite, dist 배포)
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`, **포트 8001**, 서버 Python 3.10, DB=SQLite `backend/ohisell.db`, **git 없음 → 배포=scp**
- ⚠️ scp: `COPYFILE_DISABLE=1 tar --exclude='._*' --exclude='*__pycache__*'` → /tmp → 서버 `tar -xzf` → `pm2 restart ohisell-backend` → curl 검증
- ★ **서버 alembic = `.venv/bin/alembic upgrade head`** (`python3 -m alembic` 안 됨)
- ★ **프론트 dist 배포 위치 = `/home/ubuntu/ohisell/frontend/dist/`** (nginx static). dist 통째 tar → 서버서 `rm -rf assets index.html && tar -xzf`
- 환경변수(서버 backend/.env): NAVER_CLIENT_ID/SECRET(커머스API key="NAVER"), NAVER_SA_* (검색광고 별개)
- ★ standalone 프로브 스크립트는 맨 위 `import app.database  # noqa`로 load_dotenv 발동 필요(아니면 get_naver_config None)

## 2. 이번 세션 완료 목록 (전부 prod 배포·라이브 실증)
- ✅ **전수 조사** `docs/references/13_naver_settlement_and_order_fee_fields.md`: 네이버 정산 5종(건별정산·수수료상세·일별정산·건별부가세·일별부가세) + 주문 API(product-orders/query) 수수료 필드 전부. + 라이브 프로브 실측(수수료 음수 등).
- ✅ **건별정산 클라이언트** `backend/app/clients/naver.py`: `fetch_case_settlement(date_from, date_to)` — `GET /v1/pay-settle/settle/case`, periodType=PAY_DATE(결제일)·settleDecisionType=SETTLED, searchDate 하루씩 순회+페이지네이션. 수수료 음수 부호 보존.
- ✅ **테이블** `backend/app/models.py`: `NaverSettlementCase`(productOrderId UNIQUE 그레인, order_id/product_id/pay_date 인덱스, 수수료 음수 보존). alembic `d4f6a8c0e2b3` (prod 적용 완료).
- ✅ **라우터** `backend/app/routers/naver_ops.py`: `POST /api/naver/ops/settlement/case/sync` + `_upsert_case_settlement`. ★sales-summary 대수술: 주문집계를 (order_number, platform_product_id) 라인 단위로 변경 → 건별정산 (order_id, product_id) 매칭(청크 800) → 라인별 실측/예상 폴백 → 상품별 재집계. summary에 `fee_settled_lines`/`fee_est_lines`, by_product에 `fee_actual`.
- ✅ **스케줄러** `backend/app/services/scheduler_service.py`: `sync_naver_case_settlement_job`(05:30, 결제일 45일), default state 등록 + 분기.
- ✅ **프론트** `frontend/src/pages/NaverOps.tsx` + `src/lib/api.ts`: "수수료" 카드에 "실측 N·예상 M건" 배지 + 하이브리드 주석. 타입에 fee_settled_lines/fee_est_lines/fee_actual.
- ✅ **codex review**: P1×2 라이브 데이터로 검증·기각, P2-2 IN청크 수정 합의.
- ✅ **라이브 실증**: 건별정산 3257건 적재. sales-summary 30일 실측1208/예상597라인(67%실측), 7일 실측66/예상434(최근 미정산 폴백 정확).

## 3. 확정된 결정사항 (번복 금지)
- **D-6**(트랙): 이익 정밀화 = 건별정산 실측 수수료 + 하이브리드 폴백. 정산완료=실측, 미정산 최근 주문=주문API 예상 유지. 패널에 실측/예상 건수 투명 표시(D-3 사실주의).
- **데이터 소스 = 건별정산 settle/case**(productOrderId 그레인). 일별정산은 정산예정일 합계라 주문 매칭 불가(이익 교체엔 부적합, 정산 섹션 표시용).
- **수수료 부호 음수**(라이브 프로브 확정): 실측 fee(양수) = `-(totalPayCommission + sellingInterlock + freeInstallment)`. PROD_ORDER 행만(DELIVERY는 배송비 별도).
- **매칭 키 = (order_id, product_id)**. periodType=PAY_DATE라 주문 order_date(결제일)와 그레인 일치.
- 이익 공식(매출 패널): 매출 − 수수료 − 원가 − 광고비 − 배송비. 수수료=하이브리드(실측/예상).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| docs/tracks/active/track_naver-full-integration.md | ★네이버 메가 트랙 단일진실원천(D-6 포함) |
| docs/references/13_naver_settlement_and_order_fee_fields.md | 정산·수수료·주문 API 필드 전수조사 + 라이브 프로브 |
| backend/app/clients/naver.py | fetch_case_settlement(건별정산) + fetch_daily_settlement + fetch_orders |
| backend/app/routers/naver_ops.py | sales-summary 하이브리드 매칭 + settlement/case/sync + settlement(일별) |
| backend/app/models.py | NaverSettlementCase, NaverSettlementDaily |
| backend/app/services/scheduler_service.py | 잡: sync_naver_case_settlement(05:30), sync_naver_settlement(05:25) |
| frontend/src/pages/NaverOps.tsx | 패널(수수료 실측/예상 배지) |
| frontend/src/lib/api.ts | NaverSalesSummary 타입 |

## 5. 알려진 이슈 / 주의사항
- ★ **로컬 git uncommitted** — 이익 정밀화 코드는 prod에 scp 배포돼 라이브 동작 중이나 로컬 git 미커밋. 커밋·push는 Jino 지시 시.
- ★ **fetch_orders 분할 productOrderId 이슈**(spawn_task 분리): 같은 (주문,상품)의 2번째 productOrderId를 `detail_key` 중복방지로 버림(라이브 1.4%) → 매출 과소집계 가능. 정밀화 범위 밖, 별도 검토.
- 네이버 커머스 API는 서버 IP 화이트리스트 → sync는 서버에서만(로컬 호출 권한오류 가능). 검증은 prod curl.
- 부분정산(같은 order,product 분할 중 일부만 정산) 시 실측이 미정산분 가림 → 라이브 표본 0건이라 현재 무해, 주석 처리됨.
- N1 잔여(수수료상세 commission-details·부가세 vat)는 선택 — 이익 정밀화엔 불필요, 세무/분석 필요 시.

## 6. 다음에 할 작업 (미완료)
- [ ] N2 통계(데이터솔루션 5종): 판매성과·상품성과·재구매·검색키워드·배송통계. GET 엔드포인트 apicenter 문서 확인 후 착수.
- [ ] (또는) N3 문의(CS, 쿠팡 P6 대응) → N4 상품조회 → N5 판매자/물류/SKU.
- [ ] N6 발송 → N7 클레임 → N8 상품쓰기 (dry_run+confirm, 쿠팡 패턴).
- [ ] (선택) git 커밋·push, fetch_orders 분할 productOrderId 검토(spawn_task).
- 공식문서: https://apicenter.commerce.naver.com/docs/commerce-api/current (WebFetch 차단 → /browse, 사이드바 그룹 클릭해 펼침)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-naver-N1-profit-precision_20260604.md 읽고 이어서 작업해줘
```
