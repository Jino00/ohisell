# 세션 인수인계: ohisell 쿠팡 데이터 완성 + 전체 채널 현황 파악
> 저장일시: 2026-05-17 15:00
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
- 주요 환경변수: CAFE24_MALL_ID, CAFE24_CLIENT_ID, CAFE24_CLIENT_SECRET, CAFE24_REDIRECT_URI, FRONTEND_URL, NAVER_SA_ACCESS_LICENSE, NAVER_SA_SECRET_KEY, NAVER_SA_CUSTOMER_ID, META_ACCESS_TOKEN, META_AD_ACCOUNT_ID, COUPANG_WING1_VENDOR_ID, COUPANG_WING2_VENDOR_ID

## 2. 이번 세션 완료 목록

### GFA 업로드 개선
- ✅ `backend/app/routers/ad_costs.py` — `GET /api/ad-costs/gfa/status` 엔드포인트 추가 (현황 조회)
- ✅ GFA 업로드 후 BackgroundTasks로 이익 자동 재계산 연결
- ✅ `frontend/src/pages/Settings.tsx` — GFA 드래그앤드롭 존 + 상태 표시 UI 추가

### 쿠팡 광고비 XLSX 업로드
- ✅ `backend/app/routers/ad_costs.py` — `POST /api/ad-costs/coupang/upload` 엔드포인트 추가
  - vendor_id를 파일명 regex `r"(A\d+)_"`로 추출
  - C열 판매방식: `3P→WING`, `2P→RG`, `Retail→ROCKET` 채널 자동 분류
  - `_SELL_TYPE_TO_CHANNEL_SUFFIX`, `_build_vendor_channel_map()` 헬퍼 추가
- ✅ `GET /api/ad-costs/coupang/status` 엔드포인트 추가 (채널별 광고비 현황)
- ✅ `frontend/src/pages/Settings.tsx` — 쿠팡 광고비 업로드 UI (오렌지 테마 드래그앤드롭)

### sync_service 버그 수정
- ✅ `backend/app/services/sync_service.py` — cafe24 API 중복 주문 충돌 수정
  - `seen: set[tuple[str, str]]` 중복 방지
  - `db.flush()` 즉시 반영
  - except 블록에 `db.rollback()` 추가 (sync_log stuck 버그 수정)

### 채널명 변경
- ✅ 쿠팡 Wing 계정1 → `쿠팡_오픽스` (vendor A01564720)
- ✅ 쿠팡 Wing 계정2 → `쿠팡_오하이테크` (vendor A01029796)

### 데이터 소급 적재
- ✅ cafe24 (자사몰) 2026-01-01 ~ 현재 동기화 완료 (1,173건)
- ✅ Meta 광고비 Jan~Apr 소급 (166건 / 9,537,784원)
- ✅ 쿠팡_오픽스 + 오하이테크 Jan-Feb 소급 동기화 완료
- ✅ GitHub push 완료 (origin/main 최신)

## 3. 확정된 결정사항

### 채널 구조 (최종)
| 채널명 | 코드 | 판매 API | 광고비 |
|--------|------|---------|--------|
| 쿠팡_오픽스 | COUPANG_WING1 | HMAC API (Wing만) | XLSX 수동 업로드 |
| 쿠팡_오하이테크 | COUPANG_WING2 | HMAC API (Wing만) | XLSX 수동 업로드 |
| 쿠팡 로켓그로스 계정1 | COUPANG_RG1 | HMAC API | XLSX 수동 업로드 |
| 쿠팡 로켓그로스 계정2 | COUPANG_RG2 | HMAC API | XLSX 수동 업로드 |
| 쿠팡 로켓배송 | COUPANG_ROCKET | 엑셀 업로드 | XLSX 수동 업로드 |
| 네이버 스마트스토어 | NAVER | OAuth2+bcrypt | 자동 (API) |
| 자사몰 (cafe24) | CAFE24 | OAuth2 | GFA CSV 수동 업로드 |

### 쿠팡 광고비 XLSX 파일명 규칙
- 파일명에 vendor_id 포함 필수: `A01564720_xxx.xlsx` (오픽스), `A01029796_xxx.xlsx` (오하이테크)
- C열: 판매방식 (3P=Wing, 2P=RG, Retail=로켓배송)
- L열: 광고비 금액

### 로켓배송은 오하이테크 단독 운영
- 오하이테크 XLSX 하나로 Wing + 로켓배송 광고비 동시 적재됨

## 4. 현재 데이터 현황

### 판매 데이터 (2026-05-17 기준)
| 채널 | 주문 수 | 기간 | 비고 |
|------|--------|------|------|
| 쿠팡_오픽스 | 5건 | 1/15~4/22 | Wing만 |
| 쿠팡_오하이테크 | 196건 | 1/3~5/15 | Wing만 |
| 쿠팡 로켓그로스 계정1/2 | 0건 | — | 실제 주문 있는지 확인 필요 |
| 쿠팡 로켓배송 | 0건 | — | 엑셀 업로드 필요 |
| 네이버 스마트스토어 | 3,721건 | 2/12~5/17 | 정상 |
| 자사몰 (cafe24) | 1,173건 | 1/1~5/17 | 정상 |

### 광고비 데이터 (2026-05-17 기준)
| 소스 | 기간 | 비고 |
|------|------|------|
| GFA (쇼핑) | 1/1~5/16 | 정상 (130일치) |
| Naver SA | 4/3~5/16 | 정상 (자동) |
| Meta | 1/1~5/16 | 정상 |
| 쿠팡_오픽스 Wing | 5/10~5/16 | 5일만 → 과거 XLSX 필요 |
| 쿠팡 로켓배송 | 5/16 | 1일만 → 과거 XLSX 필요 |
| 쿠팡_오하이테크 | 0 | XLSX 업로드 전혀 안 됨 |

## 5. 핵심 파일 목록

| 파일 | 역할 |
|------|------|
| `backend/app/routers/ad_costs.py` | GFA/쿠팡 업로드, SA/Meta sync 엔드포인트 |
| `backend/app/routers/oauth.py` | cafe24 OAuth (CAFE24_REDIRECT_URI 환경변수 지원) |
| `backend/app/services/scheduler_service.py` | APScheduler (SA/Meta 07:00 자동 적재) |
| `backend/app/services/sync_service.py` | 채널 동기화 (중복 방지 + 락 버그 수정 완료) |
| `backend/app/services/profit_calculator.py` | 순이익 계산 엔진 |
| `frontend/src/pages/Settings.tsx` | GFA + 쿠팡 광고비 업로드 UI |

## 6. 알려진 이슈 / 주의사항

- **cafe24 OAuth Refresh Token 만료: 2026-05-31** — 약 2주 후 재인증 필요
- **로켓그로스 0건** — API 연동은 되어 있으나 실제 주문 없는지 셀러센터 확인 필요
- **쿠팡 광고비 XLSX 파일명에 vendor_id 필수** — 없으면 채널 매핑 실패
- **서버에 git 없음** — rsync로 배포. 로컬에서 `rsync -av --exclude='.git' --exclude='__pycache__' --exclude='.venv' backend/ ubuntu@168.107.19.222:/home/ubuntu/ohisell/backend/`
- **nginx proxy_read_timeout 60초** — 장기간 동기화는 SSH 직접 실행
- **스케줄러 자동 실행** — 06:00 주문동기화, 06:30 이익재계산, 07:00 SA/Meta 광고비

## 7. 다음에 할 작업 (미완료)

- [ ] 쿠팡 광고 XLSX 과거분 업로드 (오픽스 1~5월, 오하이테크 1~5월) — 쿠팡 광고센터에서 다운로드 후 설정 페이지 업로드
- [ ] 로켓배송 판매 데이터 엑셀 업로드 (실제 데이터 있으면)
- [ ] 로켓그로스 실제 주문 여부 셀러센터 확인
- [ ] cafe24 OAuth 재인증 (2026-05-31 만료 전)
- [ ] 네이버 스마트스토어 2/12 이전 (1/1~2/11) 데이터 소급 가능 여부 확인
- [ ] 사용자 인증 추가 (프로덕션 API 인증 없음)
- [ ] 엑셀 리포트 다운로드

## 8. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/memory/HANDOFF_ohisell-coupang-data_20260517.md 읽고 이어서 작업해줘
```
