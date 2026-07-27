# 세션 인수인계: ohisell-realtime-sync
> 저장일시: 2026-06-05 21:30
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000`
- 프론트엔드 실행: `cd frontend && npm run dev`
- 프로덕션: https://sellc.ohitech.co.kr
- SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`
- Prod 경로: `/home/ubuntu/ohisell/`, PM2: `ohisell-backend` (포트 8001)
- 배포: git 없음 → scp + `pm2 reload ohisell-backend`
- 주요 환경변수: `NAVER_SA_ACCESS_LICENSE`, `NAVER_SA_SECRET_KEY`, `NAVER_SA_CUSTOMER_ID`, `FERNET_KEY`

## 2. 이번 세션 완료 목록

- ✅ **`backend/app/routers/sync.py`** — `POST /api/sync/realtime` 엔드포인트 추가. ThreadPoolExecutor(4)로 4개 병렬 fail-soft: `_run_orders`(전채널 주문), `_run_coupang_ad`(광고비), `_run_naver_sa`(SA 광고비+전환매출), `_run_meta`(Meta 광고비). 각 태스크 독립 SessionLocal 세션 사용.
- ✅ **`backend/app/services/naver_sa_ad_fetcher.py`** — `statDt` UTC→KST 변환 버그 수정. `T15:00Z` 이상이면 `+timedelta(days=1)`. `list_ad_reports`와 `_list_reports_by_type` 양쪽 모두 수정.
- ✅ **`backend/app/clients/coupang/inbound.py`** — `parse_curl_cookies`에 `require_xsrf: bool = True` 파라미터 추가. advertising.coupang.com은 xsrf 불필요(`require_xsrf=False`).
- ✅ **`frontend/src/lib/api.ts`** — `syncRealtime()` 함수 추가 (`POST /api/sync/realtime`).
- ✅ **`frontend/src/pages/Dashboard.tsx`** — 마운트 시 `syncAndRefresh()` 자동 호출 + 헤더 `🔄 새로고침` 버튼.
- ✅ **`frontend/src/pages/AdReport.tsx`** — 마운트 시 `syncAndLoad()` 자동 호출 + 헤더 `🔄 새로고침` 버튼.
- ✅ **`frontend/src/pages/CommandCenter.tsx`** — 마운트 시 `syncAndLoad()` 자동 호출 + 헤더 `🔄 새로고침` 버튼.
- ✅ **`frontend/src/pages/Orders.tsx`** — 마운트 시 `syncRealtime()` 자동 호출 (기존 채널별 sync 버튼 유지).
- ✅ **`frontend/src/pages/NaverOps.tsx`** — 마운트 시 `syncRealtime()` 후 `load()` 순서 실행.
- ✅ **쿠팡 광고 쿠키 갱신** — advertising.coupang.com cURL → `POST /api/coupang/ops/ad-cost/cookie`로 저장. vendor 104438581: 오늘 57,060원 수집 확인.
- ✅ **prod 배포 완료** — 커밋 `8a9eddf`. scp + pm2 reload + frontend dist 배포.

## 3. 확정된 결정사항

- **D-realtime**: 접속/새로고침 시 `POST /api/sync/realtime` 1회 호출. 4개 소스(주문·쿠팡광고·네이버SA·Meta) 병렬 fail-soft. 각 소스 오류는 해당 항목만 `error` 키로 표시, 나머지 계속.
- **쿠팡 광고 쿠키**: advertising.coupang.com은 AWSALB 스티키쿠키 필수. Wing 내부 API 쿠키와 별개로 관리. 만료 주기 수일~1주일. 설정 페이지 `📣 광고쿠키` 버튼에서 수동 갱신.
- **Naver SA statDt**: UTC 기준 `T15:00Z` 이상이면 KST 날짜 +1일. `list_ad_reports`와 `_list_reports_by_type` 양쪽 동일 로직 적용.
- **활성 트랙**: 쿠팡 RG 발송관제 S6 완료(6/7). S7=요일/휴일 세분화(데이터 누적 대기). 이번 세션 작업은 트랙 외 독립 작업.

## 4. 핵심 파일 목록

| 파일 | 역할 |
|------|------|
| `backend/app/routers/sync.py` | `/api/sync/realtime` 엔드포인트 |
| `backend/app/services/naver_sa_ad_fetcher.py` | 네이버 SA 광고비 수집 (UTC→KST 버그 수정) |
| `backend/app/services/coupang/ad_cost_sync.py` | 쿠팡 광고비 sync + 쿠키 관리 |
| `backend/app/clients/coupang/inbound.py` | `parse_curl_cookies` (require_xsrf 파라미터) |
| `frontend/src/lib/api.ts` | `syncRealtime()` 함수 |
| `frontend/src/pages/Dashboard.tsx` | 마운트 sync + 새로고침 버튼 |
| `frontend/src/pages/AdReport.tsx` | 마운트 sync + 새로고침 버튼 |
| `frontend/src/pages/CommandCenter.tsx` | 마운트 sync + 새로고침 버튼 |
| `docs/tracks/active/track_coupang-rg-replenishment.md` | RG 발송관제 트랙 마스터 플랜 |

## 5. 알려진 이슈 / 주의사항

- **쿠팡 광고 쿠키 만료 주기**: 수일~1주일. 만료 시 `coupang_ad: {error: "auth_expired: 403"}` 표시. 설정 페이지에서 새 cURL 붙여넣기 필요.
- **쿠팡 주문 sync**: Open API는 서버 IP 화이트리스트 — 로컬에서는 403. `channel_errors: 6` 정상(로컬 테스트 시). Prod에서는 정상 동작.
- **네이버 SA 보고서 타이밍**: 보고서가 BUILT 상태가 되기까지 시간 소요. 스케줄러 07:00 KST 실행 시 전날 보고서가 아직 없을 수 있음. 수동 sync로 해결.
- **realtime sync 응답 시간**: 4개 병렬이지만 채널 수 많으면 10~20초 소요. 프론트에서 `syncing` 스피너 표시 중.
- **RG 발송관제 S7**: 요일/휴일 세분화는 데이터 1~2주 누적 후 시작. 현재 대기 중.

## 6. 다음에 할 작업 (미완료)

- [ ] 쿠팡 광고 쿠키 자동 갱신 방안 검토 (현재 수동, 만료 주기 짧음)
- [ ] RG 발송관제 S7 — 요일/휴일별 판매속도 세분화 (데이터 누적 후)
- [ ] CoupangOps 페이지에도 마운트 시 realtime sync 추가 여부 검토
- [ ] `POST /api/sync/realtime` 응답 시간 최적화 (현재 직렬 채널 루프 → 채널별 병렬 검토)

## 7. 새 세션 시작 프롬프트

아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-realtime-sync_20260605.md 읽고 이어서 작업해줘
```
