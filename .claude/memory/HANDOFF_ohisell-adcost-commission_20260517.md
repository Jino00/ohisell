# 세션 인수인계: ohisell 광고비 파이프라인 + 수수료 백필
> 저장일시: 2026-05-17 03:00
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: `backend/` (FastAPI, Python 3.10 서버 / 3.14 로컬)
- 프론트엔드: `frontend/` (React + Vite + TypeScript + Tailwind CSS v4)
- 실행: `cd backend && uvicorn app.main:app --reload --port 8000`
- 프로덕션: https://sellc.ohitech.co.kr
- 서버: Oracle Cloud 168.107.19.222, PM2 프로세스명 `ohisell-backend`
- SSH 키: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/Study/AI/Oracle Cloud/SSH Key/ssh-key-2026-02-28.key`
- DB: `/home/ubuntu/ohisell/backend/ohisell.db` (SQLite, alembic head: a1c24f0b9d31)
- 주요 환경변수: CAFE24_MALL_ID, CAFE24_CLIENT_ID, CAFE24_CLIENT_SECRET, CAFE24_REDIRECT_URI, FRONTEND_URL, NAVER_SA_ACCESS_LICENSE, NAVER_SA_SECRET_KEY, NAVER_SA_CUSTOMER_ID, META_ACCESS_TOKEN, META_AD_ACCOUNT_ID

## 2. 이번 세션 완료 목록

### 광고비 파이프라인 구축
- ✅ `backend/app/routers/ad_costs.py` — SA sync(`POST /api/ad-costs/naver-sa/sync`), Meta sync(`POST /api/ad-costs/meta/sync`) 엔드포인트 추가. `_upsert_ad_cost()`, `_extract_naver_sa_keyword()`, `_extract_meta_keyword()` 헬퍼 함수 추가
- ✅ `backend/app/services/scheduler_service.py` — `sync_naver_sa_ad_costs_job()`, `sync_meta_ad_costs_job()` 작업 추가 (매일 07:00 KST). `_ensure_default_states()`에 두 작업 등록
- ✅ 서버에 `facebook-business` pip 패키지 설치 완료

### OAuth 콜백 URL 수정
- ✅ `backend/app/routers/oauth.py` — `CAFE24_REDIRECT_URI` 환경변수 지원 추가. nginx 프록시 환경에서 `request.base_url`이 localhost로 잡히는 문제 해결
- ✅ 서버 `.env`에 `CAFE24_REDIRECT_URI=https://sellc.ohitech.co.kr/api/oauth/cafe24/callback`, `FRONTEND_URL=https://sellc.ohitech.co.kr` 추가

### 수수료 백필 완료
- ✅ NAVER: 3,720/3,721건 commission_amount 채움 (합계 2,907,635원) — 03-14~05-17 전체 재동기화
- ✅ CAFE24: 243/243건 commission_amount 채움 (합계 106,115원) — OAuth 재인증 후 03-26~04-15 재동기화
- ✅ CAFE24 OAuth 재인증 완료 (토큰 만료: 2026-05-31)

### 광고비 과거 데이터 소급 적재
- ✅ GFA (ADVoost 쇼핑): 45일치 / 5,845,556원 (04-02~05-16) — CSV 수동 업로드
- ✅ Naver SA: 169건 / 5,211,890원 (04-03~05-16) — API 소급
- ✅ Meta: 72건 / 6,597,220원 (04-02~05-16) — API 소급

## 3. 확정된 결정사항

### ad_costs source 형식 (profit_calculator와 일치)
- GFA: `gfa:쇼핑` (NAVER channel)
- Naver SA: `naver_sa:{키워드}` 또는 `naver_sa:기타` (NAVER channel)
- Meta: `meta:{키워드}` 또는 `meta:기타` (CAFE24 channel)

### 키워드 목록 (profit_calculator, ad_costs.py, scheduler_service.py 모두 동일)
- Naver SA: `["지문방지","강화유리","종이질감","사생활","갤럭시탭","아이패드","아이폰","갤럭시","셀카봉","뮤패드","케이스"]`
- Meta: `["지문방지필름","골프필름","버디필름","강화유리","셀카봉","문캅스","일미리케이스"]`

### 스케줄러 일정
- 06:00 — 전체 채널 주문 동기화 (최근 7일)
- 06:30 — 이익률 재계산
- 07:00 — Naver SA + Meta 광고비 어제치 자동 적재 (신규)
- 30분마다 — cafe24 토큰 갱신

### GFA 업로드 워크플로우 (자동화 불가)
매일: cafe24 GFA 콘솔 → 보고서 → CSV 다운로드 → ohisell 설정 페이지 CSV 업로드 → 대시보드 즉시 반영

## 4. 핵심 파일 목록

| 파일 | 역할 |
|------|------|
| `backend/app/routers/ad_costs.py` | GFA CSV 업로드, SA/Meta sync 엔드포인트 |
| `backend/app/routers/oauth.py` | cafe24 OAuth (CAFE24_REDIRECT_URI 환경변수 지원) |
| `backend/app/services/scheduler_service.py` | APScheduler 작업 (SA/Meta 07:00 자동 적재 포함) |
| `backend/app/services/naver_sa_ad_fetcher.py` | Naver SA API 광고비 수집 SA |
| `backend/app/services/meta_ad_fetcher.py` | Meta Marketing API 광고비 수집 SA |
| `backend/app/services/profit_calculator.py` | 순이익 계산 엔진 (GFA/SA/Meta ad_costs 반영) |
| `backend/app/clients/cafe24.py` | cafe24 동기화 (payment_type + commission_amount 계산) |
| `backend/app/clients/naver.py` | Naver 커머스 API (commission_amount 계산) |
| `frontend/src/pages/Settings.tsx` | GFA CSV 업로드 UI |
| `claude-progress.txt` | Sprint 진행 현황 |

## 5. 알려진 이슈 / 주의사항

### 미완료 항목
- **NAVER 1건 commission_amount NULL** — before_deposit 상태, 사실상 무의미
- **SA/Meta 과거 데이터 (01-01~04-01)** — 소급 적재 안 됨. 필요 시 수동 실행:
  ```
  POST /api/ad-costs/naver-sa/sync?date_from=2026-01-01&date_to=2026-04-01
  POST /api/ad-costs/meta/sync?date_from=2026-01-01&date_to=2026-04-01
  ```
- **GFA 과거 데이터 (01-01~04-01)** — CSV 업로드 필요

### 서버 관련
- port 8001은 외부 방화벽 차단 → nginx 경유(https://sellc.ohitech.co.kr)만 접근 가능
- nginx proxy_read_timeout 60초 제한 → 60초 넘는 API 요청(장기간 동기화)은 직접 SSH 실행
- 서버 venv: `/home/ubuntu/ohisell/backend/.venv/bin/activate`
- ad_data.db: 서버에 없음 (0바이트) — ohi-ad-intelligence 구 시스템 legacy

### cafe24
- OAuth Refresh Token 만료: 2026-05-31 (약 2주 후) → 재인증 필요
- cafe24 API raw_data 10000자 잘림 (로우 데이터 보존 이슈, 미수정)

### 대시보드 광고비 반영 확인법
GFA 업로드 후 대시보드에서 날짜를 04-02 이후로 설정하면 광고비(ad_spend) 숫자 변화 확인 가능. 기본 30일 범위에서는 04-17 이전 날짜가 안 보임

## 6. 다음에 할 작업 (미완료)

- [ ] GFA CSV 매일 업로드 (01-01~04-01 과거 데이터 소급)
- [ ] SA/Meta 01-01~04-01 과거 데이터 소급 적재
- [ ] cafe24 OAuth 토큰 만료 전(05-31) 재인증 알림 or 자동화
- [ ] Naver 스마트스토어 수수료 정확화 (현재 API 제공 필드 그대로 사용)
- [ ] 쿠팡 수수료/배송비 정확화 (Sprint backlog)
- [ ] raw_data 10000자 잘림 제거 (cafe24)
- [ ] 사용자 인증 추가 (프로덕션 API 인증 없음 — 보안 취약)
- [ ] 엑셀 리포트 다운로드

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/memory/HANDOFF_ohisell-adcost-commission_20260517.md 읽고 이어서 작업해줘
```
