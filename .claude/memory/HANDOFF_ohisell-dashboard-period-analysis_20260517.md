# 세션 인수인계: ohisell 대시보드 기간별 분석 기능 (설계 완료, 구현 대기)
> 저장일시: 2026-05-17 17:28
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000`
- 프론트 실행: `cd frontend && npm run dev`
- 프로덕션: https://sellc.ohitech.co.kr (Oracle Cloud 168.107.19.222, PM2 `ohisell-backend`)
- 서버 배포: `rsync -av --exclude='.git' --exclude='__pycache__' --exclude='.venv' backend/ ubuntu@168.107.19.222:/home/ubuntu/ohisell/backend/`
- DB: SQLite, alembic head: `3b94b7c55a1f` (변경 없음)
- 주요 환경변수: CAFE24_*, NAVER_SA_*, META_*, COUPANG_WING1/2_VENDOR_ID

## 2. 이번 세션 완료 목록
- ✅ 이전 HANDOFF 읽어 현황 파악 (Sprint 4B-rocket-manual-revenue 완료 상태 확인)
- ✅ 대시보드 기간별 분석 기능 요구사항 정의 (사용자와 대화)
- ✅ 기존 코드 전체 파악 (dashboard.py, Dashboard.tsx, profit_calculator.py 분석)
- ✅ 아키텍처 설계 확정 (Agent/Harness/SA 구조 + 확정사항 3개)
- ⏳ 구현 미착수 (이번 세션은 설계만 완료)

## 3. 확정된 결정사항
- **D-1**: RoAS = 총매출 ÷ 광고비 × 100 (%). 광고 기여 매출 데이터 없으므로 전체 매출 기준.
- **D-2**: 기간 기준 = 어제 종료, 오늘 제외. "어제"=어제 하루, 7/14/30일 = 어제부터 과거 N일.
- **D-3**: 추이 그래프 = 지표별 4개 (매출/광고비/RoAS/이익), 각 그래프에 전체+채널별 라인.
- **D-4**: profit_calculator 엔진 리팩토링 없음. 신규 SA는 기존 `calculate_daily_trend(channel_id=...)` 채널별 호출로 조합. 엔진 회귀 위험 0.
- **D-5**: DB 변경/마이그레이션 없음.
- **D-6**: RoAS·'전체'행은 프론트 파생 계산.
- **D-7**: 로켓배송(위탁/수동매출) 채널 → 요약표·추이에서 매출·광고비·RoAS만 표시, 이익은 "—".
- **D-8**: "어제" 단일일 선택 시 추이 그래프는 포인트 1개 (표 중심, 그래프는 점으로 표시).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/profit_calculator.py` | `calculate_channel_daily_trend` 신규 SA 추가 예정 |
| `backend/app/routers/dashboard.py` | `GET /trend-by-channel` 엔드포인트 추가 예정 |
| `backend/app/schemas.py` | `ChannelTrendPoint` 응답 스키마 추가 예정 |
| `frontend/src/lib/api.ts` | 신규 타입 추가 예정 |
| `frontend/src/pages/Dashboard.tsx` | 빠른기간 버튼 + 요약표 + 추이 4그래프 추가 예정 |

## 5. 아키텍처 상세 (구현 가이드)

### 신규 SA: `calculate_channel_daily_trend`
```
위치: backend/app/services/profit_calculator.py 하단 추가
입력: db, ad_db, date_from, date_to
처리: 채널 목록 조회 → 각 채널별로 calculate_daily_trend(channel_id=...) 호출 → 합산
출력: list[dict] — {channel_id, channel_name, date, revenue, ad_spend, net_profit}
      로켓배송(위탁/수동) 채널 → net_profit=None
```

### 신규 라우터: `GET /api/dashboard/trend-by-channel`
```
위치: backend/app/routers/dashboard.py
쿼리파라미터: date_from, date_to (기존 동일)
응답: list[ChannelTrendPoint]
```

### 신규 스키마: `ChannelTrendPoint`
```python
class ChannelTrendPoint(BaseModel):
    channel_id: int
    channel_name: str
    date: str
    revenue: str  # Decimal as str
    ad_spend: str
    net_profit: Optional[str]  # null for 수동매출/위탁 채널
```

### 프론트 변경 (Dashboard.tsx)
1. 빠른기간 버튼 컴포넌트 추가 (어제/7일/14일/30일) → setDateFrom/setDateTo 적용
2. 요약표 컴포넌트: channels 데이터(기존)로 전체행 + 채널별행, RoAS=revenue/ad_spend*100
3. 채널별 추이 4개 차트: /trend-by-channel 새 fetch, recharts LineChart, 채널별 Line + 전체 Line

## 6. 알려진 이슈 / 주의사항
- profit_calculator.py 엔진은 Sprint 4B에서 cafe24 19.5% 과대 교정 + 수동매출 병합 완료된 상태. 건드리지 말 것.
- calculate_daily_trend에서 로켓배송 채널(channel_id 필터) 호출 시: orders 없음, manual_lookup 있음 → revenue 보이지만 net_profit=0 (수동매출 빼서 0이 됨). 추이 SA에서 이 채널은 net_profit=None으로 override 필요.
- ad_spend 0인 채널/날짜 → RoAS 프론트에서 null 처리 ("—" 표시).
- cafe24 OAuth Refresh Token 만료: **2026-05-31** (재인증 필요 — 별도 작업).
- 로컬 실행 시 `.venv/bin/uvicorn` 사용 (시스템 python3는 3.9).
- 서버 배포 (이전 sprint 작업) 아직 미완료 상태.

## 7. 다음에 할 작업 (미완료)
- [ ] **[메인] profit_calculator.py에 calculate_channel_daily_trend SA 추가**
- [ ] **[메인] dashboard.py에 /trend-by-channel 라우터 추가**
- [ ] **[메인] schemas.py에 ChannelTrendPoint 추가**
- [ ] **[메인] api.ts 타입 추가**
- [ ] **[메인] Dashboard.tsx: 빠른기간 버튼, 요약표, 추이 4그래프 구현**
- [ ] /codex review (구현 완료 후)
- [ ] 서버 배포 (별도 태스크)
- [ ] cafe24 OAuth 재인증 (만료: 2026-05-31)

## 8. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/memory/HANDOFF_ohisell-dashboard-period-analysis_20260517.md 읽고 이어서 작업해줘
```
