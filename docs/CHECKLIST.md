# CHECKLIST — Sprint 4B-rocket-manual-revenue
# ✅완료 🔄진행중 ⏳대기

## Phase 1 — DB / Model
- ⏳ models.py에 `ManualRevenue` 클래스 추가
- ⏳ alembic 마이그레이션 생성 (down_revision = a1c24f0b9d31)
- ⏳ alembic upgrade head 적용 (로컬)

## Phase 2 — SA (services)
- ⏳ manual_revenue_service.py 생성
- ⏳ SA-1 upsert_manual_revenue (멱등)
- ⏳ SA-2 list_manual_revenue
- ⏳ SA-3 delete_manual_revenue
- ⏳ SA-4 get_daily_manual_revenue (일별 집계)

## Phase 3 — Harness 통합
- ⏳ calculate_daily_trend 에 SA-4 매출-only 병합
- ⏳ calculate_channel_summary 에 로켓배송 행 매출 반영 (순이익 "—")

## Phase 4 — Router
- ⏳ routers/manual_revenue.py (POST/GET/DELETE)
- ⏳ main.py 라우터 등록

## Phase 5 — Frontend
- ⏳ Settings.tsx "로켓배송 매출 입력" 섹션 (폼)
- ⏳ 입력 내역 표 + 수정/삭제

## Phase 6 — 검증
- ⏳ 입력 → 대시보드 매출 반영 실측
- ⏳ /codex review PASS
- ⏳ git commit

## 완료 기준
- ⏳ 재입력 시 덮어쓰기 동작
- ⏳ 로켓배송 매출 대시보드 표시 + 순이익 "—"
- ⏳ 광고비 기존 AdCost 그대로 (중복/누락 없음)
