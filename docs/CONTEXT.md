# CONTEXT — 기술 결정 맥락 (Sprint 4B-rocket-manual-revenue)

## D-1. 가짜 Order를 만들지 않는다
- 대시보드 매출/이익은 전부 `Order` 테이블에서 집계됨 (`calculate_daily_trend`)
- 로켓배송 매출을 합성 Order로 넣으면 → 수수료/VAT 공식이 자동 적용되어 잘못된 순이익 산출
- 확정 규칙 "로켓배송 = 매출만 표시, 이익 계산 제외"와 정면 충돌
- → **전용 `manual_revenue` 테이블 + 집계 단계 병합**으로 결정

## D-2. Settlement 테이블 재활용 안 함
- Settlement는 "채널 정산금/수수료(기간 단위)" 용도 — 일별 매출 표시와 의미 불일치
- 게다가 대시보드 집계(Order 기반)에 안 잡힘 → 재활용해도 화면에 안 보임

## D-3. 입력 필드는 "전체 매출" 1개뿐
- 광고비는 이미 쿠팡 광고 XLSX 업로드 → AdCost 테이블로 자동 적재 중
- 광고 전환 매출 등은 분석용일 뿐 순이익에 미반영 → 보관 불필요 (단순성 우선)
- Jino 확인: "광고는 따로 업로드하니 전체 매출액만 받으면 됨"

## D-4. VAT 미차감
- 로켓배송은 순이익 계산을 안 하므로 VAT 차감 의미 없음
- 위탁이라 부가세 인식 주체가 셀러가 아닐 수 있음 → 입력값 그대로 표시가 가장 단순·일관

## D-5. 멱등 upsert
- 같은 날짜를 다시 입력하면 덮어쓰기 (Unique(channel_id, revenue_date))
- 광고 XLSX 업로드와 동일한 멱등 패턴 유지 (재입력 안전)

## 기술 스택 / 환경
- alembic head: `a1c24f0b9d31` → 신규 마이그레이션 down_revision으로 사용
- models.py: `from __future__ import annotations` 필수 (Python 3.14 + SQLAlchemy 2.0.48)
- 채널: COUPANG_ROCKET (seed.py에 시드됨)
