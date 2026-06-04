# 세션 인수인계: 네이버 스마트스토어 — 운영패널 완성 + 풀통합 트랙 N1 정산
> 저장일시: 2026-06-04
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 진행 = **네이버 커머스 API 전 기능 연결 메가 트랙** (track_naver-full-integration.md). N1 정산 완료, 다음 = N1 잔여(수수료상세→이익반영) 또는 N2 통계.

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (로컬 venv=Python 3.14)
- 프론트: `cd frontend && npm run build` (Vite, dist 배포)
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`, **포트 8001**, 서버 Python 3.10, DB=SQLite `backend/ohisell.db`, **git 없음 → 배포=scp**
- ⚠️ scp: `COPYFILE_DISABLE=1 tar --exclude='._*' --exclude='*__pycache__*'` → /tmp → 서버 `tar -xzf` → `pm2 restart ohisell-backend` → curl 검증
- ★ **서버 alembic = `.venv/bin/alembic upgrade head`** (`python3 -m alembic` 안 됨)
- 환경변수(서버 backend/.env): NAVER_CLIENT_ID/SECRET(커머스API, config key="NAVER"), NAVER_SA_ACCESS_LICENSE/SECRET_KEY/CUSTOMER_ID(검색광고 API 별개)

## 2. 이번 세션 완료 목록 (전부 prod 배포·라이브 실증·git push)
- ✅ **네이버 운영 패널 신규** `backend/app/routers/naver_ops.py` + `frontend/src/pages/NaverOps.tsx` (7120e9b): GET /api/naver/ops/sales-summary, 매출·PG수수료·원가·광고비·배송비·이익·이익률, 상품별표(정렬·필터), /naver-ops 라우트, 🛒스마트스토어 메뉴
- ✅ **GFA(디스플레이) 광고비** (d3e5f6f·38400a0·cecd1d5): ad_costs.py upload 파서 `총비용` 우선(동영상 누락방지), 패널에 신선도배지(2일+빨강)+CSV업로드버튼. 밀린 5/19~6/3 등록(source=gfa:쇼핑, 채널 NAVER)
- ✅ **대시보드 하위 접이식 서브메뉴** `frontend/src/components/Layout.tsx` (07c8d98): 대시보드▾→쿠팡운영·스마트스토어, 종합조망 최상위 유지
- ✅ **검색광고 전환매출·RoAS** (0fde6a3): naver_sa_ad_fetcher.py fetch_daily_conversion_revenue(AD_CONVERSION 보고서, purchase만·직접+간접), source=naver_sa:conv(ad_spend=0/ad_revenue=전환매출), 패널 요약카드. RoAS 분모=전환데이터 있는 날짜로 정렬(저평가 방지). 7일1.92/30일1.83
- ✅ **모바일 반응형** (bd373f2): Layout 햄버거 드로어(<md), main p-4 md:p-6, Dashboard KPI 4→2열·차트 2→1열. 390px 누수 0
- ✅ **N1 정산** (f8989af): NaverClient.fetch_daily_settlement(/v1/pay-settle/settle/daily), naver_settlement_daily 테이블(alembic c3d5e7f9a1b2 prod 적용), POST/GET /api/naver/ops/settlement, 스케줄러 sync_naver_settlement(05:25), 패널 💰정산 섹션. 라이브 30일: 정산 29,958,779/수수료 -1,304,731/혜택 -59,800

## 3. 확정된 결정사항 (번복 금지)
- **네이버 풀통합 = 새 활성 트랙** (docs/tracks/active/track_naver-full-integration.md). 쿠팡 트랙은 완료 처리(TRACKS.md Completed, 파일은 active/ 유지)
- **읽기·사실 먼저(N1~5) → 쓰기(N6~8) 나중** (쓰기는 쿠팡처럼 dry_run+confirm). 커머스솔루션 그룹은 N/A(자가판매 type=SELF)
- **D-3 사실주의**: 시스템은 사실/지표만, 전략추천 없음. 추정배분 금지(상품별 광고비 불가 → 계정합계만)
- **정산 scope 부여 확인됨**(서버 프로브). 정산은 settle_expect_date 그레인, 수수료/혜택은 음수 보존
- **이익 공식(매출 패널)**: 매출 − PG수수료(commission_amount) − 원가 − 광고비 − 배송비. 광고비 합계=검색(SA)+디스플레이(GFA)
- **GFA**: API 없음→CSV 수동업로드. `총비용` 컬럼(동영상 포함). 디스플레이는 전환추적 없어 RoAS는 검색광고만
- **검색광고 전환매출**: AD_CONVERSION 보고서, purchase만·직접+간접, 네이버 전환보고서 ~15일만 보관

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| docs/tracks/active/track_naver-full-integration.md | ★네이버 메가 트랙 단일진실원천(N1~N8) |
| docs/TRACKS.md | 트랙 인덱스(네이버 active, 쿠팡 completed) |
| backend/app/clients/naver.py | 커머스API 클라(OAuth2 bcrypt, fetch_orders, fetch_daily_settlement) |
| backend/app/routers/naver_ops.py | 패널 백엔드(sales-summary + settlement sync/get + _upsert_settlement) |
| backend/app/services/naver_sa_ad_fetcher.py | 검색광고 API(spend + AD_CONVERSION 전환매출) |
| backend/app/routers/ad_costs.py | GFA CSV업로드(총비용), naver-sa/sync(광고비+전환) |
| backend/app/services/scheduler_service.py | 잡: sync_naver_sa_ad_costs(07:00), sync_naver_settlement(05:25) |
| backend/app/models.py | NaverSettlementDaily 등 |
| frontend/src/pages/NaverOps.tsx | 패널(매출·RoAS·GFA배지·💰정산섹션) |
| frontend/src/components/Layout.tsx | 사이드바(접이식+모바일드로어) |
| frontend/src/lib/api.ts | API 클라+타입(NaverSettlement 등) |

## 5. 알려진 이슈 / 주의사항
- 네이버 커머스 API는 **서버 IP 화이트리스트** → 정산 sync는 서버에서만(로컬 호출 권한오류 가능)
- 상품별 광고지표 불가: ad_costs.product_id=NULL, 광고가 키워드단위. 계정합계 RoAS만(추정배분 안 함)
- 로컬 DB는 네이버 주문 4월까지만(prod가 최신). 검증은 prod curl로
- 쿠팡 잔여: W4·W5 codex 교차검증 미실행(별도, 라이브엔 영향 없음 — dry_run 기본)
- 패널 매출의 PG수수료는 아직 주문 commission_amount(추정). N1 잔여에서 정산 실측수수료로 교체 예정

## 6. 다음에 할 작업 (미완료)
- [ ] N1 잔여: 건별정산(/settle/case)·수수료상세(/settle/commission-details)·부가세(/vat) 연동
- [ ] 패널 매출 이익계산의 PG수수료를 **정산 실측수수료**로 교체(시점 차이 주의 — 정산예정일 vs 주문일)
- [ ] N2 통계(데이터솔루션): 판매성과·상품성과·재구매·검색키워드·배송통계 (GET 엔드포인트는 apicenter 문서 참조)
- [ ] N3 문의(CS) → N4 상품조회 → N5 판매자/물류/SKU
- [ ] N6 발송 → N7 클레임 → N8 상품쓰기 (dry_run+confirm, 쿠팡 패턴)
- 공식문서: https://apicenter.commerce.naver.com/docs/commerce-api/current (브라우저 렌더링, WebFetch 차단 → /browse 사용, 사이드바 [aria-expanded=false] 클릭해 펼침)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-naver-fullintegration-N1_20260604.md 읽고 이어서 작업해줘
```
