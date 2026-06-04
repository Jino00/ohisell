# 세션 인수인계: ohisell 대시보드 회사별 그룹핑 (배포 완료)
> 저장일시: 2026-05-18 10:23
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행(로컬): `cd backend && .venv/bin/uvicorn app.main:app --port 8000` (시스템 python3=3.9, 반드시 .venv 사용)
- 프론트 실행(로컬): `cd frontend && npm run dev` (보통 :5173)
- 프로덕션: https://sellc.ohitech.co.kr
- 프로덕션 서버: Oracle Cloud `ubuntu@168.107.19.222`, SSH 키 `~/.ssh/oracle_vm.pem` (chmod 600). SSH config 없음 — `-i ~/.ssh/oracle_vm.pem` 명시 필수
- 서버 경로: `/home/ubuntu/ohisell/{backend,frontend}`. 백엔드 PM2 `ohisell-backend` (id 0), **포트 8001**, venv `/home/ubuntu/ohisell/backend/.venv` (python3.10). 프론트 nginx root `/home/ubuntu/ohisell/frontend/dist`. nginx `/api/`→localhost:8001
- 배포: `rsync -az --exclude='.git' --exclude='__pycache__' --exclude='.venv' --exclude='*.db' --exclude='.env' --exclude='backups' -e "ssh -i ~/.ssh/oracle_vm.pem" backend/ ubuntu@168.107.19.222:/home/ubuntu/ohisell/backend/` + frontend dist rsync + `alembic upgrade head` + `pm2 restart ohisell-backend`
- DB: SQLite `/home/ubuntu/ohisell/backend/ohisell.db`. alembic head: **c7d2e1f3a4b5** (로컬/프로덕션 동일 적용 완료)
- 주요 환경변수: CAFE24_*, NAVER_SA_*, META_*, COUPANG_WING1/2_VENDOR_ID

## 2. 이번 세션 완료 목록
- ✅ (이전 작업) 대시보드 기간별 분석: calculate_channel_daily_trend SA, /trend-by-channel, 빠른기간 버튼, 요약표, 추이 4그래프 — 커밋 789c828, 배포 완료
- ✅ 배포 중 발견·해결: 프로덕션 manual_revenue 마이그레이션 미적용(3b94b7c55a1f) → 서버 alembic upgrade로 해결 (failures.jsonl 기록)
- ✅ cafe24 어제 광고비 0원 원인규명: 버그 아님 — Meta 동기화 스케줄러가 cron `0 7 * * *`인데 서버 UTC라 16:00 KST 실행(의도 07:00 KST). `POST /api/ad-costs/meta/sync?date_from=&date_to=`로 2026-05-17분(114,642원) 수동 동기화. (스케줄러 타임존 근본수정은 별도 spawn task로 등록함)
- ✅ **회사별 그룹핑 신규 기능 (커밋 ee4244e, 6b89a83, 배포 완료)**:
  - `backend/app/models.py`: Channel.company 컬럼
  - `backend/alembic/versions/c7d2e1f3a4b5_add_company_to_channels.py`: batch_alter_table add company + 7채널 시드 UPDATE(code 기준), downgrade drop_column (로컬 up/down/re-up 검증)
  - `backend/app/services/profit_calculator.py`: _classify_channel, get_channel_company_map, _agg_block/_add_net/_finalize, group_summary_by_company, group_trend_by_company (기존 엔진 calculate_* 미변경)
  - `backend/app/schemas.py`: GroupedSummaryRow/GroupedTrendPoint 추가, ChannelSummaryRow/ChannelTrendPoint 삭제(미사용)
  - `backend/app/routers/dashboard.py`: /channel-breakdown·/trend-by-channel 그룹 응답
  - `backend/app/seed.py`: CHANNELS에 company 7개 + seed_channels 기존행 backfill
  - `frontend/src/lib/api.ts`: GroupedSummaryRow/GroupedTrendPoint
  - `frontend/src/pages/Dashboard.tsx`: 계층 요약표(total/company/leaf), buildChannelChartData group 키, 4그래프/Pie/Bar 그룹 적용, Pie 라벨깨짐→짧은이름+Legend
  - `docs/CHECKLIST.md`: Sprint 4B-company-grouping 체크리스트
  - 정기백업: 서버 `/home/ubuntu/ohisell/backend/backups/daily_backup.sh` + crontab `30 18 * * *`(UTC=03:30KST), 14일 보관. 변경 전 스냅샷 `backups/ohisell_pre_company_20260518_100645.db`
  - codex 2라운드 PASS

## 3. 확정된 결정사항
- **회사 매핑** (channels.company, code 기준 — 번복 금지):
  - 개인회사 오픽스 ← COUPANG_WING1, COUPANG_RG1
  - 주식회사 오하이테크 ← COUPANG_WING2, COUPANG_RG2, COUPANG_ROCKET, CAFE24
  - 주식회사 오하이 ← NAVER
- **그룹 구조**: 전체 → 회사 소계 → leaf. leaf = "{회사} · {세그}"; 세그 = 쿠팡 로켓그로스·윙(WING+RG marketplace) / 쿠팡 로켓배송(ROCKET consignment) / 자사몰(cafe24) / 네이버 스마트스토어
- **net 의미론 (중요, 그룹핑前부터의 제품 정의 복원)**: 집계(회사·전체)의 net_profit = 측정가능 자식들의 net 합(숫자), profit_rate = net/측정가능매출. net None인 자식(로켓배송)은 net/측정매출 미반영하되 매출·광고비·order_count는 계속 합산. 측정가능 매출이 0일 때만(순수 로켓배송 leaf) net/rate = "—"(None). 프론트 전체선도 동일 정의(net!=null 포인트만 합산) — 일관.
- **엔진 미변경 원칙**: profit_calculator의 calculate_daily_trend/calculate_channel_summary/calculate_channel_daily_trend 등 기존 산출 로직 불변. 그룹핑은 신규 순수함수+라우터에서만. (회귀 위험 0)
- **codex 속도 정책**: review 기본 reasoning=medium(보안/마이그레이션/금액은 프롬프트로 집중 지시 또는 high), 1라운드 원칙(P1/P2 없으면 종료), 백그라운드 실행으로 벽시계 단축. 합의 안되면 Jino 위임.
- 작업 흐름: 대화로 구조확정 → Jino 승인 → 계획서/체크리스트 → Phase 구현 → codex → 배포. AskUserQuestion 옵션나열 지양, 대화형 한 질문씩(Jino 선호).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| backend/app/services/profit_calculator.py | 이익 엔진(미변경) + 회사 그룹핑 순수함수(하단) |
| backend/app/routers/dashboard.py | /trend, /kpi, /channel-breakdown(그룹), /trend-by-channel(그룹), /product-ranking |
| backend/app/schemas.py | GroupedSummaryRow/GroupedTrendPoint, TrendPoint, DashboardKPI, ProductProfitRow 등 |
| backend/app/seed.py | 7채널 시드 + company + 기존행 backfill |
| backend/app/models.py | Channel.company 등 모델 |
| backend/alembic/versions/c7d2e1f3a4b5_*.py | company 컬럼 마이그레이션(batch, 롤백가능) |
| frontend/src/pages/Dashboard.tsx | 대시보드 전체 UI(계층표/4그래프/Pie/Bar/빠른기간) |
| frontend/src/lib/api.ts | API 타입 |
| docs/CHECKLIST.md | 현재 sprint 체크리스트 |
| claude-progress.txt | 세션 간 진행상황 (매 sprint 갱신) |

## 5. 알려진 이슈 / 주의사항
- **스케줄러 타임존 버그(미해결, spawn task 등록됨)**: scheduler_service.py 잡들이 cron `0 7 * * *`인데 서버 UTC라 07:00 UTC=16:00 KST 실행(의도 07:00 KST). sync_meta_ad_costs, sync_naver_sa_ad_costs 모두 해당 → 매일 오전엔 전날 광고비 비어보이다 16:00 KST 채워짐. 근본수정: APScheduler timezone="Asia/Seoul" (별도 작업).
- Meta/네이버SA 광고비 수동 동기화: `POST /api/ad-costs/meta/sync?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`, `POST /api/ad-costs/naver-sa/sync`. 멱등(upsert).
- cafe24 OAuth Refresh Token 만료: **2026-05-31** (재인증 필요 — 별도 작업, 미완료).
- nginx IP 화이트리스트/Basic Auth 보안 미적용 (TODO).
- 위탁(로켓배송)은 주문 API 동기화 대상 아님 — manual_revenue 수동입력만. consignment 채널은 orders 0건이 정상.
- Pie 범례에 "쿠팡 로켓그로스·윙"이 회사 2개분으로 중복 표시될 수 있음(색 다름, 매출비중 작음) — 의도된 트레이드오프, 표/Bar는 회사 prefix 전체표기로 구분됨.
- 로컬 uvicorn은 --reload 없이 띄웠으므로 백엔드 코드 변경 시 수동 재시작 필요.
- failure-memory DB: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/.claude/skills/failure-memory/failures.jsonl` (에러 해결 시 반드시 기록).

## 6. 다음에 할 작업 (미완료)
- [ ] 스케줄러 UTC/KST 타임존 근본수정 (APScheduler timezone=Asia/Seoul) — spawn task 등록됨, codex review 필수
- [ ] cafe24 OAuth 재인증 (만료 2026-05-31 임박)
- [ ] nginx IP 화이트리스트 또는 Basic Auth (보안)
- [ ] 네이버 스마트스토어 API 실제 동기화 테스트
- [ ] (옵션) 요약표 leaf 아래 개별 계정(WING1/RG1 분리) 펼치기 — 이번엔 회사·판매유형까지만 구현, 사용자 요청 시 추가
- [ ] Sprint 5 후보: 알림(Telegram/Slack), 재고관리, 엑셀리포트, 사용자 인증, 쿠팡 revenue-history 정산 자동연동

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/memory/HANDOFF_ohisell-company-grouping_20260518.md 읽고 이어서 작업해줘
```
