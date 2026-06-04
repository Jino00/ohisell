# 세션 인수인계: 네이버 N4(상품조회) + N5(판매자정보) 완료 — N6 발주/발송 설계 대기
> 저장일시: 2026-06-04
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 진행 = 네이버 커머스 API 전 기능 트랙(track_naver-full-integration.md). N1~N5 완료(N2 skip). 다음 = N6 발주/발송 처리(쓰기).

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload`
- 프론트: `cd frontend && npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`, 포트 8001, Python 3.10, DB=SQLite
- ⚠️ scp 배포: `COPYFILE_DISABLE=1 tar --exclude='._*' --exclude='*__pycache__*'`
- ★ 프론트 dist 배포: `/home/ubuntu/ohisell/frontend/dist/` → 서버서 `rm -rf assets index.html && tar -xzf`
- 환경변수: NAVER_CLIENT_ID/SECRET(커머스API key="NAVER"), NAVER_SA_*(검색광고 별개)

## 2. 이번 세션 완료 목록

### N2 SKIP (브랜드스토어 전용 확인)
- ✅ 데이터솔루션 API 전체 그룹 `[브랜드스토어 전용]` — prod 라이브 403 프로브 확인. 스마트스토어 사용 불가.

### N3 고객 문의 (이전 세션에서 완료, 이번 세션 prod 라이브 확인)
- ✅ `backend/app/clients/naver.py` — `fetch_inquiries()` 추가
- ✅ `backend/app/routers/naver_ops.py` — `GET /api/naver/ops/inquiries` 엔드포인트
- ✅ `frontend/src/lib/api.ts` — `NaverInquiryRow`, `NaverInquiries`, `fetchNaverInquiries`
- ✅ `frontend/src/pages/NaverOps.tsx` — 💬 고객 문의 섹션 (카드 3개 + 테이블, 미답변 `bg-red-50`)

### N4 상품 조회 (이번 세션 신규 완료 + prod 배포)
- ✅ `backend/app/clients/naver.py` — `search_products()` 추가 (`POST /v1/products/search`, 상태필터/페이징)
- ✅ `backend/app/routers/naver_ops.py` — `GET /api/naver/ops/products?status=SALE&page=1&size=500` 추가
- ✅ `frontend/src/lib/api.ts` — `NaverChannelProduct`, `NaverProductItem`, `NaverProductList`, `fetchNaverProducts` 추가
- ✅ `frontend/src/pages/NaverOps.tsx` — 🛍️ 상품 목록 섹션 (상태버튼 + 카드3개 + 테이블)
- ✅ **prod 라이브 실증**: 판매중 692개, total_pages 231

### N5 판매자 정보 (이번 세션 신규 완료 + prod 배포)
- ✅ `backend/app/clients/naver.py` — `fetch_seller_info()` 추가 (`GET /v1/seller/account` + `/v1/seller/channels`)
- ✅ `backend/app/routers/naver_ops.py` — `GET /api/naver/ops/seller` 추가
- ✅ `frontend/src/lib/api.ts` — `NaverSellerChannel`, `NaverSellerInfo`, `fetchNaverSellerInfo` 추가
- ✅ `frontend/src/pages/NaverOps.tsx` — 🏪 판매자 정보 섹션 (계정ID/등급/채널 카드+채널 상세)
- ✅ **prod 라이브 실증**: theohi / 등급 02 / 채널 500140084 STOREFARM 오하이 Ohi

### 트랙 파일 갱신
- ✅ `docs/tracks/active/track_naver-full-integration.md` — N2~N5 완료 표시, 현재 진행 단계·다음 액션 갱신

## 3. 확정된 결정사항 (번복 금지)
- **N2 브랜드스토어 전용 확정**: 데이터솔루션 5종 전부 prod 403. 스마트스토어 사용 불가 → 영구 skip.
- **N3~N5 읽기 전용**: DB 저장 없이 라이브 직접 반환 방식. 실시간 데이터라 캐싱/적재 불필요.
- **N6~N8 쓰기 = dry_run+confirm 이중확인 필수**: 실제 주문 상태 변경이므로 쿠팡 쓰기와 동일 구조.
- **메인 profit_calculator·쿠팡·cafe24 = 미변경**. 네이버 naver_ops.py만 범위.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_naver-full-integration.md` | ★트랙 단일진실원천 |
| `backend/app/clients/naver.py` | NaverClient (fetch_orders, fetch_inquiries, search_products, fetch_seller_info) |
| `backend/app/routers/naver_ops.py` | 운영패널 엔드포인트 전체 (sales-summary, settlement, inquiries, products, seller) |
| `frontend/src/pages/NaverOps.tsx` | 운영패널 UI 전체 (매출·정산·광고·문의·상품·판매자) |
| `frontend/src/lib/api.ts` | 타입 + fetch 함수 전체 |

## 5. 알려진 이슈 / 주의사항
- ★ **로컬 git uncommitted** — N1~N5 전부 prod scp 배포됨. 커밋은 Jino 지시 시.
- ★ **N6 발주/발송 = 쓰기 API** → Opus 전환 권장 (원칙). 실제 주문 상태 변경됨.
- 네이버 커머스 API = 서버 IP 화이트리스트 → 검증은 prod curl만. 로컬 직접 호출 불가.
- 상품 조회 `POST /v1/products/search` — size 최대 500, page 기반. 전체 조회 시 231페이지.
- `_request(method, ...)` 가 PUT도 지원 → N6 발주/발송 별도 메서드 불필요(재활용 가능).

## 6. 다음에 할 작업 (미완료)

### N6 발주/발송 처리 (다음 작업)
설계 합의 완료:
| 작업 | Method | 경로 |
|------|--------|------|
| 발주확인 | PUT | `/v1/pay-order/seller/product-orders/confirm` |
| 발송처리 | PUT | `/v1/pay-order/seller/product-orders/dispatch` |
| 발송지연 고지 | PUT | `/v1/pay-order/seller/product-orders/delay` |

엔드포인트 설계:
- `POST /api/naver/ops/orders/confirm?dry_run=true/false`
- `POST /api/naver/ops/orders/dispatch?dry_run=true/false`  (body: product_order_ids, delivery_method, carrier_code, tracking_number)
- `POST /api/naver/ops/orders/delay?dry_run=true/false`

프론트: 📦 발주/발송 섹션 — 발주확인 대기 주문 카드 + 발송처리 폼 + dry_run 결과 모달

- [ ] N6 백엔드: naver.py 메서드 3개 + naver_ops.py 엔드포인트
- [ ] N6 프론트: api.ts 타입 + NaverOps.tsx 섹션
- [ ] N6 prod 배포 + dry_run 라이브 검증
- [ ] N7 클레임(취소/반품/교환)
- [ ] N8 상품 쓰기(등록/수정/재고/가격)
- [ ] (선택) git 커밋·push

## 7. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_ohisell-naver-N4N5-done_20260604.md 읽고 이어서 작업해줘
```
