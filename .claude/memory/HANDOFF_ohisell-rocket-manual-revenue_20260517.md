# 세션 인수인계: ohisell 로켓배송 매출 수동 입력 (계획 완료, 구현 대기)
> 저장일시: 2026-05-17 16:00
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && uvicorn app.main:app --reload --port 8000`
- 프론트 실행: `cd frontend && npm run dev`
- 프로덕션: https://sellc.ohitech.co.kr (Oracle Cloud 168.107.19.222, PM2 `ohisell-backend`)
- 서버 배포: `rsync -av --exclude='.git' --exclude='__pycache__' --exclude='.venv' backend/ ubuntu@168.107.19.222:/home/ubuntu/ohisell/backend/`
- DB: SQLite, alembic head: `a1c24f0b9d31`
- 주요 환경변수: CAFE24_*, NAVER_SA_*, META_*, COUPANG_WING1/2_VENDOR_ID (값 제외)

## 2. 이번 세션 완료 목록
- ✅ 로켓배송 데이터 한계 분석: API 주문 0건(위탁), 광고센터 엑셀 다운 불가 → 수동 입력 확정
- ✅ 입력 방식 결정: 이미지 OCR ❌ → **숫자 직접 입력 폼** ✅ (필요 데이터가 "전체 매출액" 1개뿐이라)
- ✅ 아키텍처 설계 완료 (Agent/Harness/SA, 가짜 Order 금지)
- ✅ `docs/PLAN.md` 작성 — 스펙·아키텍처·6 Phase 구현 계획
- ✅ `docs/CONTEXT.md` 작성 — 결정 맥락 D-1~D-5
- ✅ `docs/CHECKLIST.md` 작성 — Phase별 진행 체크리스트
- ✅ 코드베이스 조사 완료: 모든 매출/이익은 `Order` 테이블 집계(`calculate_daily_trend`), 광고비는 `AdCost` 테이블, Settlement는 재활용 부적합

## 3. 확정된 결정사항 (번복 금지)
- **D-1**: 가짜 Order 안 만듦. 전용 `manual_revenue` 테이블 + profit_calculator 집계 단계에서 매출-only 병합
- **D-2**: Settlement 테이블 재활용 안 함 (의미 불일치 + 대시보드 미반영)
- **D-3**: 입력 필드는 "전체 매출액" 1개뿐 (광고비는 이미 쿠팡 XLSX→AdCost 자동 적재 중)
- **D-4**: VAT 미차감, 입력값 그대로 표시 (로켓배송은 순이익 계산 제외 = 매출만 표시)
- **D-5**: (channel_id, revenue_date) 유니크 → 같은 날 재입력 시 멱등 덮어쓰기
- 쿠팡_오하이테크 광고 XLSX(`A01029796_..._Retail`)는 코드 수정 없이 그대로 업로드하면 COUPANG_ROCKET으로 분류됨 (검증 완료, 미업로드 상태)

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN.md` / `CONTEXT.md` / `CHECKLIST.md` | 이번 Sprint 계획·맥락·체크 (먼저 읽을 것) |
| `backend/app/models.py` | `ManualRevenue` 클래스 추가 예정 (head a1c24f0b9d31) |
| `backend/app/services/profit_calculator.py` | `calculate_daily_trend`/`calculate_channel_summary` — Order 집계, 여기에 매출-only 병합 |
| `backend/app/routers/ad_costs.py` | 쿠팡 광고 XLSX 업로드 (Retail→ROCKET 매핑, 수정 불필요) |
| `backend/app/routers/manual_revenue.py` | 신규 생성 예정 (POST/GET/DELETE) |
| `backend/app/services/manual_revenue_service.py` | 신규 생성 예정 (SA-1~4) |
| `frontend/src/pages/Settings.tsx` | "로켓배송 매출 입력" 섹션 추가 예정 |

## 5. 알려진 이슈 / 주의사항
- models.py에 `from __future__ import annotations` 필수 (Python 3.14 + SQLAlchemy 2.0.48)
- 신규 alembic 마이그레이션 down_revision = `a1c24f0b9d31`
- 매출/이익 집계는 전부 Order 기반 → 로켓배송은 Order 0건이라 SA-4 병합 없으면 대시보드에 안 보임
- cafe24 OAuth Refresh Token 만료: **2026-05-31** (별건, 재인증 필요)
- 현재 Opus 모델 상태 — 구현은 Sonnet으로 전환 후 진행

## 6. 다음에 할 작업 (미완료)
- [ ] **`/model sonnet` 전환 후 구현 시작**
- [ ] Phase 1: models.py `ManualRevenue` + alembic 마이그레이션 + upgrade
- [ ] Phase 2: `manual_revenue_service.py` SA-1(upsert)/2(list)/3(delete)/4(daily 집계)
- [ ] Phase 3: profit_calculator `calculate_daily_trend`+`calculate_channel_summary` 매출-only 병합 (순이익 "—")
- [ ] Phase 4: `routers/manual_revenue.py` (POST/GET/DELETE) + main.py 등록
- [ ] Phase 5: Settings.tsx 입력 폼 + 내역 표(수정/삭제)
- [ ] Phase 6: 입력→대시보드 반영 실측 + `/codex review` PASS + git commit
- [ ] (후속) 쿠팡_오하이테크 광고 XLSX 과거분 업로드, 로켓배송 매출 실제 입력

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/memory/HANDOFF_ohisell-rocket-manual-revenue_20260517.md 읽고 이어서 작업해줘
```
