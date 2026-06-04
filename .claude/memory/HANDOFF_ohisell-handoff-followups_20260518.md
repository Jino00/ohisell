# 세션 인수인계: ohisell HANDOFF §6 후속작업 4건 일괄 처리
> 저장일시: 2026-05-18 11:37
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행(로컬): `cd backend && .venv/bin/uvicorn app.main:app --port 8000` (시스템 python3=3.9, 반드시 .venv 사용. --reload 없으면 코드변경 시 수동 재시작)
- 프론트 실행(로컬): `cd frontend && npm run dev` (:5173)
- 프로덕션: https://sellc.ohitech.co.kr
- 프로덕션 서버: Oracle Cloud `ubuntu@168.107.19.222`, SSH 키 `~/.ssh/oracle_vm.pem` (chmod 600). SSH config 없음 — `-i ~/.ssh/oracle_vm.pem` 명시 필수
- 서버 경로: `/home/ubuntu/ohisell/{backend,frontend}`. 백엔드 PM2 `ohisell-backend`(id 0), **포트 8001**, venv `/home/ubuntu/ohisell/backend/.venv`(python3.10 — f-string 중첩따옴표 미지원, heredoc은 `<< "PYEOF"`로). 프론트 nginx root `/home/ubuntu/ohisell/frontend/dist`. nginx `/api/`→localhost:8001. 서버 TZ=UTC
- 배포: backend rsync(--exclude .git/__pycache__/.venv/*.db/.env/backups) + frontend dist rsync + (DB 변경 시)`alembic upgrade head` + `pm2 restart ohisell-backend`
- DB: SQLite `/home/ubuntu/ohisell/backend/ohisell.db`. alembic head **c7d2e1f3a4b5** (이번 세션 DB 변경 없음)
- 주요 환경변수: CAFE24_CLIENT_ID/SECRET/REDIRECT_URI, NAVER_CLIENT_ID/SECRET, NAVER_SA_*, META_*, COUPANG_WING1/2_VENDOR_ID, FRONTEND_URL

## 2. 이번 세션 완료 목록
- ✅ **#1 스케줄러 타임존 근본수정**: `backend/app/services/scheduler_service.py:280` — `CronTrigger.from_crontab(state.cron_expression, timezone="Asia/Seoul")` 명시(기존 timezone 미지정→`get_localzone()`=서버 UTC 폴백 버그). +6/-1줄. codex review PASS(P1/P2 없음). git commit `eb4c915` + push origin/main. 프로덕션 rsync+pm2 restart 배포. 검증: scheduler API 5개 잡 모두 next_run_time `+09:00`, 광고비 잡 07:00 KST 정상화(이전 16:00 KST). failures.jsonl 기록.
- ✅ **#2 cafe24 OAuth 복구**: 자동 refresh 400 invalid_grant 반복 진단. 원인=refresh token 회전 + 스케줄러/API경로 동시 refresh 경합으로 토큰 체인 파손. 유효 refresh_token으로 `Cafe24Client._refresh_access_token()`→`on_token_refreshed` 콜백 DB 영구저장으로 복구. refresh 만료 2026-06-01로 연장. cafe24 store API HTTP 200 검증((주)오하이테크/theohi11). **브라우저 재인증 불필요**. 코드 변경 없음(런타임 복구). failures.jsonl 기록. 근본수정 spawn task 등록.
- ✅ **#3 nginx 보안**: Jino 결정 — 혼자 쓰는 내부 도구라 Basic Auth/IP 화이트리스트 **미적용**, 현행 유지. 코드/서버 변경 없음. claude-progress.txt에 잔여 리스크 문서화.
- ✅ **#4 네이버 스마트스토어 동기화 테스트**: 프로덕션(등록 IP) test_connection OK, fetch_orders 7일분 770건(수수료 포함), `sync_channel_orders` 엔드투엔드 success(신규 36+갱신 734, 에러 0, DB 3736→3772). 코드 변경 없음(검증만).
- ✅ `claude-progress.txt` 4개 작업 모두 갱신(진행률 97%)

## 3. 확정된 결정사항
- **nginx 보안 미적용 (Jino 명시 결정, 번복 금지)**: "혼자 쓰는 거니까 비밀번호나 보안 없이 운영". sellc.ohitech.co.kr 공개 접근은 인지된 리스크. 필요 시 Basic Auth 즉시 적용 가능 상태이나 사용자 요청 전까지 건드리지 않음.
- **스케줄러 timezone 수정 방식 확정**: trigger 객체를 미리 만들어 add_job에 넘기면 BackgroundScheduler 기본 timezone이 적용 안 됨 → from_crontab에 timezone 명시가 정답(codex 합의). 다른 from_crontab 호출처 없음(grep 전수확인).
- **cafe24 복구는 refresh 경로로 충분**: HANDOFF가 가정한 브라우저 재인증은 불필요했음. refresh token 자체는 유효, 체인 파손이 문제였음.
- 작업 흐름: codex review 1라운드 원칙(P1/P2 없으면 종료), 미커밋 워킹트리는 `codex exec -s read-only`에 diff 인라인으로 검토. failure-memory는 `AI Program/.claude/skills/failure-memory/failures.jsonl` 단일 통합.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| backend/app/services/scheduler_service.py | APScheduler 잡 등록(이번 세션 280행 timezone 수정). cafe24_proactive_refresh_job 동시성 이슈 보유 |
| backend/app/clients/cafe24.py | Cafe24Client._refresh_access_token (refresh token 회전), _parse_cafe24_datetime(KST→UTC-naive) |
| backend/app/routers/oauth.py | cafe24 OAuth 플로우. /status는 저장 만료값만 봐 실파손 미탐지(개선 대상) |
| backend/app/clients/naver.py | 네이버 커머스 API(OAuth2+bcrypt 서명). fetch_orders 정상 검증됨 |
| backend/app/services/sync_service.py | sync_channel_orders — 네이버 엔드투엔드 정상 |
| claude-progress.txt | 세션 간 진행상황(진행률 97%, 4건 모두 기록 완료) |
| .claude/memory/MEMORY.md | HANDOFF 인덱스 |

## 5. 알려진 이슈 / 주의사항
- **cafe24 refresh token 동시성 경합(미해결, spawn task 등록됨)**: cafe24는 refresh마다 refresh_token 회전. `_cafe24_refresh_lock`은 스케줄러 잡 자신만 보호, sync_service/ad_costs 등 API 호출 경로의 Cafe24Client 자동 refresh는 미보호 → 동시 refresh 시 한쪽이 토큰 소비, 다른쪽 400 invalid_grant → 체인 파손 → 자사몰 끊김. 2026-05-18 실제 발생, 수동 복구함. **재발 가능** — 근본수정 필요(모든 refresh 단일 락 직렬화 + 락 내 DB 토큰 재조회). 추가: `/api/oauth/cafe24/status`가 저장 만료값만 봐 "connected" 오보고 → 실검증 반영도 spawn task에 포함.
- 서버 python3.10: heredoc Python은 `<< "PYEOF"`, f-string에 같은 따옴표 중첩 금지(3.12+ 기능).
- 서버 TZ=UTC. DB datetime은 UTC-naive 저장(cafe24 KST→UTC 변환), status/scheduler 비교도 UTC-naive로 일관.
- 위탁(로켓배송)은 주문 API 동기화 대상 아님 — manual_revenue 수동입력만.
- 스케줄러 타임존 수정 후 cafe24_token_refresh(`*/30 * * * *`)는 timezone 무관(every 30min), 영향 없음.
- failure-memory DB: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/.claude/skills/failure-memory/failures.jsonl`.

## 6. 다음에 할 작업 (미완료)
- [ ] **cafe24 refresh token 동시성 직렬화 근본수정** (spawn task 등록됨, 대기 중) — 모든 refresh 단일 락 + 락 내 DB 재조회, oauth /status 실검증. cafe24 client+scheduler+oauth로 범위 한정, profit 엔진/동기화 로직 미변경, codex review 필수
- [ ] 쿠팡 광고 XLSX 과거분 업로드
- [ ] 로켓배송 실제 매출 데이터 입력 (Settings 페이지)
- [ ] Sprint 5 후보: 알림(Telegram/Slack), 재고관리, 엑셀리포트, 사용자 인증, 쿠팡 revenue-history 정산 자동연동

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/memory/HANDOFF_ohisell-handoff-followups_20260518.md 읽고 이어서 작업해줘
```
