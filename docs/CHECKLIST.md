# CHECKLIST.md — 작업 체크리스트 (Sprint 4B-cafe24)
# Sprint 진행 중 Claude가 갱신합니다. ✅완료 🔄진행중 ⏳대기

## Phase 1 — Sub-Agent (순수 함수)
- ✅ SA1 Cafe24StatusMapper — 공식 enum + ^[A-Z]\d{2}$ prefix 폴백
- ✅ SA2 Cafe24PaymentClassifier — market_id/pm/gateway → payment_type
- ✅ SA3 CommissionResolver — 요율표 (kcp_transfer per-order, 미확인 보수 0.0385, 원단위)
- ✅ SA4 ShippingResolver — cafe24 1,900/주문
- ✅ /codex review — 2라운드 합의 PASS (P1-1/P1-2/P2-2 반영, P1-3 근거기각, P1-4 부분기각)

## Phase 2 — DB 마이그레이션
- ✅ models.py: Order.payment_type, Order.commission_amount 추가
- ✅ alembic revision a1c24f0b9d31 (orders 컬럼 2개) — 적용 완료 (head)

## Phase 3 — Harness 배선
- ✅ cafe24.py: _map_status 삭제 → normalize_status + classify + 라인배분
- ✅ base.py: RawOrder.payment_type/commission_amount 추가
- ✅ sync_service.py: 영속화 (create+update)

## Phase 4 — profit_calculator
- ✅ 매출 제외 status 필터 (cancelled/returned/pending) — 전 채널
- ✅ cafe24: commission_amount 합산 + shipping 1,900 (channel_summary는 cafe24만)
- ✅ 비-cafe24 회귀 없음 실측 확인 (네이버5.5%/쿠팡10.8% 정률 유지)
- ✅ Phase 3/4 /codex review — 2라운드 합의 PASS

## Phase 5 — 백필 + 검증
- ✅ 백필 스크립트 (라인별 detail status, 매출포함 라인 배분, 잔여정산) — 212주문/242라인
- ✅ QA before/after: 순이익 3,346,264 → 2,677,623 (구버전 19.5% 과대 교정)
- ✅ failures.jsonl 기록
- ⏳ git commit (Jino 확인 후)
