# 세션 인수인계: 네이버 운영패널 이익 회계 정확화 (D-8) — 배송비 회계 + 공급가 VAT 통일
> 저장일시: 2026-06-04
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 진행 = 네이버 커머스 API 전 기능 트랙(track_naver-full-integration.md). N1 완료 + D-7(분할라인) + **D-8(운영패널 이익 회계 정확화)** 전부 prod 배포. 다음 = N2 통계.

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (로컬 venv=Python 3.14)
- 프론트: `cd frontend && npm run build` (Vite, dist 배포)
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`, **포트 8001**, 서버 Python 3.10, DB=SQLite `backend/ohisell.db`, **git 없음 → 배포=scp**
- ⚠️ scp: `COPYFILE_DISABLE=1 tar --exclude='._*' --exclude='*__pycache__*'`. 백엔드 1파일은 직접 scp 가능.
- ★ 프론트 dist 배포: `/home/ubuntu/ohisell/frontend/dist/` → 서버서 `rm -rf assets index.html && tar -xzf`
- ★ 서버 alembic = `.venv/bin/alembic upgrade head`. 서비스 재시작 = `pm2 restart ohisell-backend`
- ★ standalone 프로브 스크립트는 맨 위 `import app.database  # noqa`로 load_dotenv 발동(아니면 get_naver_config None)
- 환경변수(서버 backend/.env): NAVER_CLIENT_ID/SECRET(커머스API key="NAVER"), NAVER_SA_*(검색광고 별개)

## 2. 이번 세션 완료 목록 (전부 prod 배포·라이브 실증·codex 합의)
- ✅ **네이버 운영패널 이익 회계 정확화** `backend/app/routers/naver_ops.py` (sales-summary만):
  - 고객배송비(deliveryFeeAmount=shipping_cost) 비용 차감 → **매출 가산**으로 수정
  - 한진 물류비 신설: `packageNumber` distinct 배송건 × 1900 (NULLIF+COALESCE+json_extract, 매출제외주문 빼고 기간필터)
  - **공급가(VAT 제외) 통일**: 순이익 = (상품매출 + 고객배송비 − 수수료 − 원가 − 한진물류비) ÷ 1.1 − 광고비
  - 상수 `_VAT_DIVISOR=1.1`, `_HANJIN_PER_SHIPMENT=1900` 추가. summary 신규 필드(revenue_vat_incl/product_revenue/delivery_revenue/logistics/shipment_count/supply_basis)
  - by_product = 상품손익 공급가 (상품매출−수수료−원가)÷1.1, shipping 키 제거
- ✅ **프론트** `frontend/src/pages/NaverOps.tsx` + `src/lib/api.ts`: "배송비" 카드 → "물류비(한진)"(sub 배송건수). 총매출 카드에 VAT포함액 병기. 타입 갱신(shipping 제거, 신규 필드). 테이블 주석 = 공급가 기준 공식.
- ✅ **VAT 상태 조사**(라이브 검색): 네이버 광고비=공급가(세금계산서 별도), 네이버 수수료=VAT포함("부가세 포함 수수료율"). 매출=VAT포함(소비자가). [근거: 광고선전비 분개 예시 / 비즈넵 세나 / 2025.6 수수료 개편 안내]
- ✅ **codex review**: P1 0건(VAT 이중적용 없음·공급가 환산·배송비 매출가산·by_product 제외 전부 정확). P2 packageNumber 빈문자열→NULLIF 합의·적용.
- ✅ **prod 라이브**: 30일 매출(공급가)33,078,918(VAT포함36,386,810)/배송매출2,634,090/한진2,938,090(1701건)/이익6,683,743·20.21%. 검산 전부 통과. 수정전 6,153,918·18.6% → +53만 정확.

## 3. 확정된 결정사항 (번복 금지)
- **D-8**(트랙): 네이버 운영패널 이익 회계 = 배송비 회계 + 공급가 통일. **범위 = naver_ops.py만**. 메인 profit_calculator·쿠팡·cafe24는 **건드리지 않음**(Jino 명시).
- **VAT 공급가 통일이 정석**: 부가세는 통과항목(매출VAT−매입VAT 상쇄). VAT 포함 항목은 ÷1.1, 광고비만 공급가라 그대로.
- **VAT 상태**: 매출·원가·한진물류비·수수료=VAT포함(÷1.1) / 네이버 광고비=공급가(그대로).
- **한진 물류비 = packageNumber 배송건×1900** (메인 엔진 profit_calculator와 동일 기준).
- 고객배송비는 매출(과세), 수수료는 상품매출 기준만(배송비엔 미부과).
- 표시: 전 금액 공급가 기준(매출−비용=이익 일관) + 총매출에 VAT포함 병기. 상품별은 상품손익만(배송/물류/광고는 요약).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| docs/tracks/active/track_naver-full-integration.md | ★트랙 단일진실원천(D-8 포함) |
| backend/app/routers/naver_ops.py | sales-summary(공급가 통일 회계) + 정산 sync |
| backend/app/services/profit_calculator.py | ★메인 엔진(대시보드/정산/주문). 이번에 미변경 — 참고만(배송회계 정석·VAT=rev×10/110 근사) |
| frontend/src/pages/NaverOps.tsx | 패널(물류비 카드·VAT포함 병기) |
| frontend/src/lib/api.ts | NaverSalesSummary 타입 |
| docs/references/13_naver_settlement_and_order_fee_fields.md | 정산·수수료·주문 API 필드 전수조사 |

## 5. 알려진 이슈 / 주의사항
- ★ **로컬 git uncommitted** — N1정밀화+D-7+D-8 전부 prod에 scp 배포돼 라이브 동작 중이나 로컬 git 미커밋. 커밋·push는 Jino 지시 시.
- ★ **메인 대시보드(전 채널) 동일 정확화 미적용** — Jino가 "네이버만" 지시. 메인 profit_calculator는 여전히 vat=rev×10/110(매입공제 무시 근사) + 네이버 외 채널.
- ★ 메인까지 하려면 **채널별 VAT 미확인** 항목 먼저 확인(추정 금지): 쿠팡 판매수수료 VAT, 쿠팡 광고비(XLSX) VAT, cafe24 PG수수료 VAT, 메타 광고비 VAT.
- 네이버 커머스 API는 서버 IP 화이트리스트 → 검증은 prod curl. 로컬 DB는 과거(3~4월)분만.
- 면세 매출 가정: 오하이 상품 전부 과세로 ÷1.1. 면세 섞이면 네이버 vat/daily API(과세/면세 분리)로 검증 가능(N1 잔여).

## 6. 다음에 할 작업 (미완료)
- [ ] N2 통계(데이터솔루션 5종): 판매성과·상품성과·재구매·검색키워드·배송통계. apicenter 문서 확인 후 착수.
- [ ] (또는) N3 문의(CS) → N4 상품조회 → N5 판매자/물류 → N6~8 쓰기(dry_run+confirm).
- [ ] (선택) 메인 대시보드+쿠팡/cafe24 회계 정확화 — 채널별 VAT 확인 후 별도.
- [ ] (선택) git 커밋·push.
- 공식문서: https://apicenter.commerce.naver.com/docs/commerce-api/current (WebFetch 차단 → /browse, 사이드바 그룹 클릭해 펼침)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-naver-D8-profit-accounting_20260604.md 읽고 이어서 작업해줘
```
