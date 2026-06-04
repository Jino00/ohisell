# 세션 인수인계: ohisell Sprint 4B 로켓배송 수동 매출 입력 (완료)
> 저장일시: 2026-05-17 17:00
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000`
- 프론트 실행: `cd frontend && npm run dev`
- 프로덕션: https://sellc.ohitech.co.kr (Oracle Cloud 168.107.19.222, PM2 `ohisell-backend`)
- 서버 배포: `rsync -av --exclude='.git' --exclude='__pycache__' --exclude='.venv' backend/ ubuntu@168.107.19.222:/home/ubuntu/ohisell/backend/`
- DB: SQLite, alembic head: `3b94b7c55a1f`
- 주요 환경변수: CAFE24_*, NAVER_SA_*, META_*, COUPANG_WING1/2_VENDOR_ID

## 2. 이번 세션 완료 목록
- ✅ `backend/app/models.py`: ManualRevenue 클래스 추가 (channel_id FK, revenue_date, gross_revenue, memo, UniqueConstraint)
- ✅ `backend/alembic/versions/3b94b7c55a1f_add_manual_revenue_table.py`: 마이그레이션 생성 + upgrade head 적용
- ✅ `backend/app/services/manual_revenue_service.py` 신규 생성: SA-1(upsert), SA-2(list), SA-3(delete), SA-4(get_daily)
- ✅ `backend/app/services/profit_calculator.py`: 수동매출 매출-only 병합 (net_profit 제외), channel_summary 수동매출 채널 별도 행 추가, 수동매출 채널 ad_spend AdCost 실제 합산 (Codex P2 수정)
- ✅ `backend/app/routers/manual_revenue.py` 신규 생성: POST/GET/DELETE
- ✅ `backend/app/main.py`: manual_revenue 라우터 등록
- ✅ `backend/app/schemas.py`: ChannelSummaryRow.net_profit/profit_rate → Optional[str]
- ✅ `frontend/src/lib/api.ts`: ChannelBreakdown.net_profit/profit_rate → number | null
- ✅ `frontend/src/pages/Settings.tsx`: 로켓배송 수동 매출 입력 섹션 (폼 + 내역 표, KST 로컬 날짜 기본값)
- ✅ `frontend/src/pages/Dashboard.tsx`: 이익률 차트 null 채널 제외 (회색 처리)
- ✅ codex review 2라운드 PASS
- ✅ git commit 27ffc1a, 34d65f9
- ✅ 네이버 IP 등록 완료 (Oracle IP 168.107.19.222) — 이미 이전 세션에서 완료됨 확인

## 3. 확정된 결정사항
- **D-1**: 로켓배송 수동매출 — 가짜 Order 금지, 전용 `manual_revenue` 테이블
- **D-2**: Settlement 테이블 재활용 안 함
- **D-3**: 입력 필드는 "전체 매출액" 1개뿐 (광고비는 기존 AdCost 자동 적재)
- **D-4**: VAT 미차감, 입력값 그대로 / 순이익 null (계산 불가)
- **D-5**: (channel_id, revenue_date) 유니크 → 재입력 시 멱등 덮어쓰기
- **D-6 (신규)**: 수동매출 전용 채널의 ad_spend = AdCost 테이블 실제 합산 (Codex 지적 반영)
- **D-7 (신규)**: KST 로컬 날짜로 폼 기본값 설정 (`new Date()` 로컬 포맷)

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/models.py` | ManualRevenue 클래스 (head: 3b94b7c55a1f) |
| `backend/app/services/manual_revenue_service.py` | SA-1~4 (upsert/list/delete/daily집계) |
| `backend/app/services/profit_calculator.py` | 수동매출 병합 로직 (trend + channel_summary) |
| `backend/app/routers/manual_revenue.py` | POST/GET/DELETE API |
| `frontend/src/pages/Settings.tsx` | 로켓배송 매출 입력 폼 + 내역 표 |
| `frontend/src/pages/Dashboard.tsx` | 이익률 차트 null 처리 |
| `frontend/src/lib/api.ts` | ChannelBreakdown nullable 타입 |
| `claude-progress.txt` | 전체 진행 현황 (갱신 완료) |
| `docs/CHECKLIST.md` | Sprint 체크리스트 (전항목 ✅) |

## 5. 알려진 이슈 / 주의사항
- 서버 배포 아직 미완료 — 로컬만 적용된 상태. 배포 시 alembic upgrade head 필요
  - 서버 배포 명령: rsync 후 `pm2 restart ohisell-backend` + `alembic upgrade head`
- cafe24 OAuth Refresh Token 만료: **2026-05-31** (재인증 필요)
- models.py에 `from __future__ import annotations` 필수 (Python 3.14 + SQLAlchemy)
- 로컬 실행 시 `.venv/bin/uvicorn` 사용 (시스템 python3는 3.9라 `|` 타입 미지원)
- 스케줄러 시작 시 `scheduler_state` UNIQUE 충돌 로그 출력되지만 정상 동작

## 6. 다음에 할 작업 (미완료)
- [ ] **서버 배포**: rsync + alembic upgrade head + PM2 재시작
- [ ] **네이버 스마트스토어 API 동기화 테스트** (IP 등록 완료 상태)
- [ ] 로켓배송 실제 매출 데이터 입력 (Settings 페이지에서 직접)
- [ ] 쿠팡 광고 XLSX 과거분 업로드
- [ ] nginx IP 화이트리스트 또는 Basic Auth (보안)
- [ ] cafe24 OAuth 재인증 (만료: 2026-05-31 전)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/memory/HANDOFF_ohisell-sprint4b-rocket-manual_20260517.md 읽고 이어서 작업해줘
```
