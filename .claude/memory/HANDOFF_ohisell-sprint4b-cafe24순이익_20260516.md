# 세션 인수인계: ohisell Sprint 4B-cafe24 — 자사몰 순이익 정확화
> 저장일시: 2026-05-16
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- Backend 실행: `cd backend && source .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8000`
- Frontend 실행: `cd frontend && npx vite --port 5173` (포트 5173 고정 — CORS)
- URL: 로컬 FE http://localhost:5173 / BE http://localhost:8000(/docs), 프로덕션 https://sellc.ohitech.co.kr (Oracle 168.107.19.222)
- 서버 SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@168.107.19.222`, PM2 ohisell-backend(8001)
- GitHub: https://github.com/Jino00/ohisell (private, main)
- DB: SQLite `backend/ohisell.db` (alembic head = a1c24f0b9d31)
- 환경변수(.env, gitignore): COUPANG_WING1/2_*, COUPANG_RG1/2_*, NAVER_CLIENT_ID/SECRET(작은따옴표 필수), CAFE24_*, DATABASE_URL

## 2. 이번 세션 완료 목록
- ✅ 신규 SA 4개(순수함수, Phase1):
  - `backend/app/services/cafe24_status_mapper.py` — order_status→active|cancelled|returned|exchanged|pending, ^[A-Z]\d{2}$ prefix폴백, REVENUE_EXCLUDED={cancelled,returned,pending}
  - `backend/app/services/payment_classifier.py` — market_id==NCHECKOUT→네이버페이 / gateway kakao·toss / 그외 KCP, payment_method별 코드
  - `backend/app/services/commission_resolver.py` — 공식요율표, PER_ORDER_TYPES={kcp_bank,naverpay_bank,kcp_transfer}, 미확인유형 보수 0.0385, 원단위 반올림
  - `backend/app/services/shipping_resolver.py` — CAFE24=1900/주문
- ✅ `backend/app/models.py` — Order.payment_type(String30), Order.commission_amount(Numeric12,2)
- ✅ `backend/alembic/versions/a1c24f0b9d31_add_cafe24_payment_fields_to_orders.py` (적용 완료)
- ✅ `backend/app/clients/base.py` — RawOrder에 payment_type/commission_amount(기본 None) 추가
- ✅ `backend/app/clients/cafe24.py` — _map_status 삭제, 결제분류+공식수수료+배송비 라인배분(매출포함 라인에만, 잔여=마지막포함라인)
- ✅ `backend/app/services/sync_service.py` — payment_type/commission_amount 영속화(create+update)
- ✅ `backend/app/services/profit_calculator.py` — _line_commission 헬퍼, 3함수 REVENUE_EXCLUDED 필터, cafe24 commission_amount 합산, channel_summary shipping은 cafe24만 차감
- ✅ `backend/scripts/backfill_cafe24_payments.py` — 기존 242라인 재산출(라인별 detail order_status 우선, 매출포함 배분, 잔여정산) → 실행완료 212주문/242라인
- ✅ docs/PLAN.md·CONTEXT.md·CHECKLIST.md 갱신, claude-progress.txt 갱신, failures.jsonl 2건 기록
- ✅ /codex review 2게이트(SA 4개 / 배선+계산) 각 2라운드 대화형 검증 후 PASS

## 3. 확정된 결정사항 (번복 금지)
- 우선순위: 1)자사몰(cafe24) 2)네이버 스마트스토어 3)쿠팡 (쿠팡 광고/로켓배송 매출처리 결정은 보류 중)
- cafe24 배송비 = 한진택배 1,900원/주문 (우리 부담, 고객 무료배송, 주문 건당 고정)
- 네이버페이 신용카드 등급 = **영세 → 1.870%** (VAT 포함)
- 공식요율(출처 검증완료): 네이버페이(VAT포함) 카드1.870%/계좌이체1.650%/보조결제3.740%/휴대폰3.850%/무통장 min(1%,275). KCP·카카오·토스(VAT별도 ×1.1) 카드3.5%, KCP계좌이체 max(1.8%,200)×1.1, KCP가상계좌 300×1.1=330정액
- cafe24 식별: market_id=NCHECKOUT→네이버페이, payment_gateway_name에 kakao/toss, 그외 KCP (실데이터 242건 검증)
- 사용 PG: KCP, 카카오페이, 토스페이, 네이버페이(주문형, 카페24 공식 호스팅사)
- 추정 금지: 모든 요율/enum은 공식문서로 확인함 (네이버페이 help.admin.pay.naver.com, cafe24 developers.cafe24.com)

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| app/services/cafe24_status_mapper.py | SA 상태정규화 + REVENUE_EXCLUDED |
| app/services/payment_classifier.py | SA 결제유형 분류 |
| app/services/commission_resolver.py | SA 공식수수료 산출 |
| app/services/shipping_resolver.py | SA cafe24 1900/주문 |
| app/clients/cafe24.py | 분류+수수료+배송 라인배분, 동기화 |
| app/services/sync_service.py | payment_type/commission_amount 영속화 |
| app/services/profit_calculator.py | 상태필터 + cafe24 commission/shipping |
| scripts/backfill_cafe24_payments.py | 기존주문 재산출 (멱등) |

## 5. 알려진 이슈 / 주의사항
- **미커밋 상태** — Jino가 sprint 커밋 여부 미확정(질문에 "다음 대화"로 응답). 변경 staged/untracked 다수. push 안 함.
- raw_data 10000자 잘림(sync_service MAX_RAW_DATA_SIZE) 미해결 → 복합결제 금액분해 불가, 전체 라인매출 근사(영향 미미). **별도 후속작업**(잘림 제거 + 결제필드 컬럼추출)
- 백필 멱등 — 재실행 안전. 신규 동기화는 cafe24.py 경로가 분류/배분 자동 수행
- profit_calculator 비-cafe24(쿠팡10.8%/네이버5.5%) 정률 로직 불변 — 회귀 없음 실측 확인됨
- 네이버 취소/반품(cancelled53+returned19)도 이제 매출 제외됨(전채널 필터, 의도된 정확화)
- cafe24 순이익: 구버전 3,346,264 → 정확화 2,677,623 (19.5% 과대 교정)
- Python 3.14 로컬/3.10 서버, models.py `from __future__ import annotations` 필수, 전체 KST

## 6. 다음에 할 작업 (미완료)
- [ ] Jino에게 sprint 커밋 여부 확인 → 승인 시 로컬 커밋 (push는 별도 확인)
- [ ] 네이버 스마트스토어 순이익 정확화 (다음 우선순위)
- [ ] 쿠팡 (Wing/RG + 광고/로켓배송 매출처리 보류 결정 재개)
- [ ] (후속) raw_data 10000자 잘림 제거 + cafe24 결제필드 Order 컬럼 추출
- [ ] (보류) 쿠팡 로켓배송 매출 처리방법 결정 (광고전환매출만 vs 전체매출 수동/엑셀)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/memory/HANDOFF_ohisell-sprint4b-cafe24순이익_20260516.md 읽고 이어서 작업해줘
