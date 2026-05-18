# CHECKLIST — Sprint 4B-company-grouping (대시보드 회사별 그룹핑)
# ✅완료 🔄진행중 ⏳대기

## 목표
기간 요약표 + 채널별 추이 4그래프를 회사 → 채널유형 leaf로 그룹핑.
엔진(profit_calculator 핵심) 미변경, 그룹핑은 신규 순수함수 + Harness만.

## 회사 매핑 (확정)
- 개인회사 오픽스 ← WING1(1), RG1(3)
- 주식회사 오하이테크 ← WING2(2), RG2(4), ROCKET(5), CAFE24(7)
- 주식회사 오하이 ← NAVER(6)

## leaf 그룹 (요약표 행 / 차트 라인)
- 개인회사 오픽스 · 쿠팡 로켓그로스·윙   (WING1+RG1, 순이익 O)
- 주식회사 오하이테크 · 쿠팡 로켓그로스·윙 (WING2+RG2, 순이익 O)
- 주식회사 오하이테크 · 쿠팡 로켓배송      (ROCKET, 순이익 —)
- 주식회사 오하이테크 · 자사몰(cafe24)     (순이익 O)
- 주식회사 오하이 · 네이버 스마트스토어    (순이익 O)
- 전체 (프론트 파생)

## Phase 1 — DB
- ✅ models.py Channel.company 컬럼
- ✅ alembic batch 마이그레이션 (add company, nullable, 롤백 가능)
- ✅ 마이그레이션 내 7채널 시드 UPDATE (전부 회사 지정)

## Phase 2 — 그룹핑 SA (순수함수)
- ✅ _get_channel_company_map(db) → {id: (company, leaf_label, has_profit)}
- ✅ group_summary_by_company(rows, map) → 계층(total/company/leaf) 합산
- ✅ group_trend_by_company(points, map) → leaf별 일자 추이

## Phase 3 — 스키마/Harness/라우터
- ✅ schemas: GroupedSummaryRow / GroupedTrendPoint
- ✅ /channel-breakdown → 그룹 응답
- ✅ /trend-by-channel → leaf 그룹 포인트

## Phase 4 — 프론트
- ✅ Dashboard.tsx 요약표 계층 렌더(전체/회사소계/leaf 들여쓰기)
- ✅ 4그래프 series = leaf 그룹 + 전체 (buildChannelChartData 재사용)
- ✅ api.ts 타입

## Phase 5 — 검증
- ✅ alembic upgrade/downgrade 로컬 검증
- ✅ tsc/build, 브라우저 (Pie 라벨 깨짐 수정) 시각 검증
- ✅ codex 통합 (2라운드 PASS) 1회 (medium, 마이그레이션만 high, 백그라운드)

## Phase 6 — 배포
- ✅ 프로덕션 .backup 스냅샷(필수) → backend rsync → alembic upgrade → pm2 restart
- ✅ 정기 백업 cron (.backup 매일, N일 보관)
- ✅ 프로덕션 검증 + claude-progress.txt 갱신

## 완료 기준
- 요약표/4그래프가 회사>leaf 구조, 합산 정확
- 로켓배송 leaf 순이익 "—" 유지
- 마이그레이션 롤백 가능, 백업 존재
- codex PASS
