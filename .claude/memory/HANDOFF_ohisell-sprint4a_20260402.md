# 세션 인수인계: ohisell Sprint 4A (API 연동 + 배포)
> 저장일시: 2026-04-02 18:00
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 실행 명령어: `bash scripts/init.sh` (frontend + backend 동시 시작)
- Backend: `http://localhost:8000` (FastAPI, Swagger: `/docs`)
- Frontend: `http://localhost:5173` (React + Vite)
- **프로덕션: https://sellc.ohitech.co.kr** (Oracle Cloud 168.107.19.222)
- GitHub: https://github.com/Jino00/ohisell (private)
- Python: 3.14 (로컬), 3.10 (서버), venv: `backend/.venv/`
- 서버 SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@168.107.19.222`
- 서버 PM2: `ohisell-backend` (port 8001, uvicorn)
- 서버 nginx: sellc.ohitech.co.kr → localhost:8001 (HTTPS certbot)
- 주요 환경변수: `DATABASE_URL`, `COUPANG_WING1_*`, `COUPANG_WING2_*`, `COUPANG_RG1_*`, `COUPANG_RG2_*`, `NAVER_*`, `CAFE24_*`, `AD_DATA_DB_PATH`, `FRONTEND_URL`, `BACKEND_BASE_URL`
- ohi-ad-intelligence: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/ohi-ad-intelligence/` (로컬), `/home/ubuntu/ad_data.db` (서버, 0바이트 — 실제 파일 전송 필요)

## 2. 이번 세션 완료 목록

### API 키 입력 (전체 완료)
- ✅ `backend/.env` — 네이버 API 키 입력 (client_id: ttWc5ACKCdKQ4A1Oukkuw)
- ✅ `backend/.env` — 쿠팡 오픽스 API 키 입력 (vendor_id: A01564720, access_key 수정: 36자 UUID)
- ✅ `backend/.env` — 쿠팡 오하이테크 API 키 입력 (vendor_id: A01029796)
- ✅ `backend/.env` — 쿠팡 RG1/RG2 = Wing1/Wing2와 동일 키 (같은 API, 배송방식으로 구분)
- ✅ `backend/.env` — cafe24 키 입력 (meta-ads-dashboard DB에서 가져옴: theohi11)

### cafe24 OAuth 엔드포인트 구현 (6개 파일)
- ✅ `backend/app/models.py` — OAuthToken에 `refresh_token_expires_at` 컬럼 추가
- ✅ `backend/app/clients/cafe24.py` — `build_cafe24_oauth_url()`, `exchange_authorization_code()`, `_parse_cafe24_datetime()` 추가, `on_token_refreshed` 콜백 파라미터 추가
- ✅ `backend/app/schemas.py` — `OAuthAuthUrl`, `OAuthStatus` Pydantic 모델 추가
- ✅ `backend/app/routers/oauth.py` — **신규** 4개 엔드포인트 (auth-url, callback, status, disconnect)
- ✅ `backend/app/main.py` — oauth 라우터 등록
- ✅ `backend/alembic/versions/e8f0d3882c59_add_refresh_token_expires_at_to_oauth_.py` — 마이그레이션

### cafe24 실제 OAuth 인증
- ✅ cafe24 개발자센터에 redirect_uri 등록: `https://sellc.ohitech.co.kr/api/oauth/cafe24/callback`
- ✅ 실제 OAuth 인증 완료 → access_token 발급 (만료: 2026-04-02T01:36:34 UTC)
- ✅ 토큰 ohisell DB에 저장 (channel_id=7)

### F-403: cafe24 토큰 자동갱신
- ✅ `backend/app/services/sync_service.py` — `_get_client_for_channel()`에 `on_token_refreshed` 클로저 연결, 토큰 갱신 시 DB 자동 업데이트
- ✅ `backend/app/services/scheduler_service.py` — `cafe24_proactive_refresh_job()` 추가 (30분마다 실행, threading.Lock으로 동시 실행 방지, refresh token 만료 3일 전 경고)

### F-404: Oracle Cloud 배포
- ✅ 코드 rsync 전송 (backend + frontend)
- ✅ Python 3.10 venv 생성 + requirements 설치
- ✅ DB 초기화 (create_all + alembic stamp head + seed)
- ✅ nginx 설정 (sellc.ohitech.co.kr → localhost:8001)
- ✅ certbot HTTPS 인증서 발급 (만료: 2026-06-30)
- ✅ PM2 ohisell-backend 등록 (port 8001, --proxy-headers)
- ✅ Frontend npm build → dist/ 서빙
- ✅ https://sellc.ohitech.co.kr/api/channels → 정상 응답 확인

### GitHub
- ✅ `gh repo create Jino00/ohisell --private` → push 완료

### DNS
- ✅ Gabia: sellc.ohitech.co.kr → 168.107.19.222 (A 레코드)

## 3. 확정된 결정사항
- **쿠팡 Wing/RG**: 같은 API 키 사용, 별도 vendor_id로 4개 채널 분리 (이미 구현됨)
- **cafe24 redirect_uri**: `https://sellc.ohitech.co.kr/api/oauth/cafe24/callback` (HTTPS만 지원)
- **서버 포트**: ohisell = 8001 (기존 coupang-dashboard가 8000 사용)
- **Sprint 4 분리**: 4A(데이터 레이어) → 4B(네이버 + 인프라 보강) — CEO/Eng 리뷰 반영
- **cafe24 토큰 갱신**: 30분 간격 proactive refresh + 동시성 Lock
- **서버 ad_data.db**: 0바이트 파일만 존재, 실제 305MB 파일 전송 필요

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/routers/oauth.py` | cafe24 OAuth 4개 엔드포인트 (신규) |
| `backend/app/clients/cafe24.py` | OAuth 토큰 교환 + 갱신 콜백 |
| `backend/app/services/sync_service.py` | 동기화 오케스트레이션 + cafe24 콜백 연결 |
| `backend/app/services/scheduler_service.py` | APScheduler + cafe24 proactive refresh |
| `backend/app/services/profit_calculator.py` | 순이익 계산 (광고비 bleeding 버그 미수정) |
| `backend/app/models.py` | OAuthToken.refresh_token_expires_at 추가 |
| `backend/.env` | 전체 API 키 입력 완료 |
| `docs/PLAN.md` | Sprint 4A 계획서 (리뷰 반영) |
| `/home/ubuntu/ohisell/ecosystem.config.js` | 서버 PM2 설정 |

## 5. 알려진 이슈 / 주의사항
- **쿠팡 API 403**: IP 168.107.19.222를 두 계정에 등록했으나, 아직 반영 안 됨 (등록 직후 테스트 시 403). 시간이 지나면 반영될 것으로 예상
- **쿠팡 오픽스 Access Key**: 원래 스크린샷에서 한 글자 잘림 → `f3b90aa3-6baf-4b0e-b9c2-9087600356cd`로 수정 완료 (36자 UUID)
- **서버 ad_data.db 0바이트**: `/home/ubuntu/ad_data.db`가 빈 파일. 실제 305MB 파일 전송 필요 (rsync 또는 scp)
- **서버 코드 동기화**: 로컬에서 수정한 sync_service.py, scheduler_service.py가 아직 서버에 미반영. 서버 재배포 필요
- **cafe24 access_token 만료**: 2026-04-02T01:36:34 UTC (이미 만료). refresh_token은 4/15까지 유효. 서버에서 proactive refresh 동작하면 자동 갱신됨
- **profit_calculator 광고비 bleeding**: Eng 리뷰에서 발견 — `calculate_channel_summary()`의 proportional allocation이 채널 간 광고비를 잘못 배분. option_id 직접 매칭으로 수정 필요
- **보안**: 프로덕션 API에 인증 없음. 배포 상태에서 누구든 접근 가능. Sprint 4B에서 nginx IP 화이트리스트 추가 예정
- **Python 3.14 vs 3.10**: 로컬은 3.14, 서버는 3.10. `from __future__ import annotations` 덕에 호환되지만 주의
- **cafe24 OAuth state**: 현재 하드코딩 `"cafe24_oauth"` → CSRF 토큰으로 변경 권장

## 6. 다음에 할 작업 (미완료)

### Sprint 4A 잔여
- [ ] 쿠팡 API 서버에서 재테스트 (IP 반영 확인)
- [ ] 쿠팡 API 응답 샘플링 → Wing/RG 배송방식 필드 확인
- [ ] profit_calculator 광고비 채널간 bleeding 버그 수정
- [ ] 프론트엔드 설정 페이지 (F-405: 채널 연동 상태, OAuth 버튼, 동기화 트리거)
- [ ] 서버에 수정된 코드 재배포 (rsync + pm2 restart)
- [ ] 서버에 ad_data.db 실제 파일 전송 (305MB)
- [ ] 첫 동기화 결과 플랫폼 대시보드와 수동 비교

### Sprint 4B
- [ ] 네이버 IP 등록 (Oracle IP 168.107.19.222) → API 테스트
- [ ] nginx IP 화이트리스트 또는 Basic Auth
- [ ] OAuth state CSRF 토큰화
- [ ] cafe24 주문 API 실제 호출 검증

### Sprint 5 후보
- [ ] 알림 시스템 (Telegram/Slack)
- [ ] 재고 관리 페이지
- [ ] 엑셀 리포트 다운로드
- [ ] 사용자 인증 (로그인/세션)
- [ ] 쿠팡 revenue-history 정산 자동 연동

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/memory/HANDOFF_ohisell-sprint4a_20260402.md 읽고 이어서 작업해줘
