# 세션 인수인계: ohisell-adcost-akamai
> 저장일시: 2026-06-05 24:00
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000`
- 프론트 실행: `cd frontend && npm run dev`
- 프로덕션: https://sellc.ohitech.co.kr (PM2 `ohisell-backend` 포트 8001)
- SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, Prod 경로 `/home/ubuntu/ohisell/`
- 배포: git 없음 → scp + `pm2 reload ohisell-backend`; 프론트 `rsync dist/`
- DB: **SQLite** (local·prod 둘 다). 이번에 WAL+busy_timeout=30s+synchronous=NORMAL 적용(database.py)
- 주요 환경변수: `AD_INGEST_TOKEN`(신규, prod .env), `COOKIE_ENC_KEY`, `NAVER_SA_*`, `META_*`

## 2. 이번 세션 완료 목록 (전부 prod 배포 + 라이브 검증 완료)

### HANDOFF §6 후속 3건 (커밋 4432836)
- ✅ `frontend/src/components/Layout.tsx` — 광고비 수집중단 **전역 빨간 배너**(status==red 또는 stale). 페이지 이동마다+6초후 재확인. CTA `/coupang-ops?adcookie=open`
- ✅ `frontend/src/lib/api.ts` — `getAdCostCookieStatus()` + `AdCostCookieStatus`(age_hours/stale 포함)
- ✅ `frontend/src/pages/CoupangOps.tsx` — `?adcookie=open` 진입 시 쿠키 패널 자동 펼침
- ✅ `backend/app/database.py` — SQLite 동시성 하드닝(WAL/busy_timeout/synchronous). 8스레드 동시쓰기 락0 검증
- ✅ `backend/app/routers/sync.py` — `_run_orders` 6채널 직렬→병렬(채널별 독립 세션)
- ✅ CoupangOps 마운트 sync = **검토→변경불필요**(이미 타깃 sync)

### realtime 주문 sync 치명버그 수정 (커밋 f24262a)
- ✅ `_run_orders`가 `sync_channel_orders`(dict 반환)를 `r.new_orders`(속성)로 접근 → 전 채널 실패였음(기존부터). `r.get("status")=="success"` + `.get()`으로 수정. 라이브: channel_errors 0, **1189건 sync**

### 광고비 Akamai 근본해결 (커밋 b1c8c4f, 704a52e, ebf29a9)
- ✅ prod: `POST /api/coupang/ops/ad-cost/ingest`(토큰 X-Ingest-Token, secrets.compare_digest+검증), `ad_cost_sync.ingest_ad_cost`, `cookie_status` staleness(age_hours/stale 26h), realtime sync에서 `_run_coupang_ad` 제거(prod는 Akamai 403)
- ✅ 로컬: `tools/ad_cost_browser_fetcher.py`(Playwright storage_state), `tools/requirements-local.txt`, `tools/com.ohisell.adcost.plist`, README
- ✅ **launchd `com.ohisell.adcost` 매시 등록·가동중** (Mac). prod green/stale=false, 오늘 광고비 자동갱신중
- ✅ Mac: playwright+chromium 설치, `~/.ohisell_ad_fetcher.json` 설정, `~/.ohisell_ad_state.json` 세션, Jino 로그인 1회 완료

## 3. 확정된 결정사항 (번복 금지)
- **쿠팡 광고비 "잦은 만료"의 진짜 원인 = Akamai 봇매니저** (세션 TTL 아님). ①prod 데이터센터 IP 차단(residential만 통과) ②curl 재생은 1회용(토큰 회전 무효화). 이전 "TTL 수일~1주일/IP화이트리스트"는 **오진**.
- **광고비 fetch는 Jino Mac 실제 브라우저(Playwright)에서만 가능.** prod 직접 fetch 영구 불가 → realtime sync에서 제거됨.
- **세션 유지 = Playwright storage_state** (영속 프로필은 세션쿠키를 종료 시 버림). login이 저장, run이 로드→fetch→회전 state 재저장. 연속 3회 run 성공으로 검증.
- prod ingest는 토큰 인증 필수. 광고비 숫자만 push(쿠키/자격증명은 prod에 안 보냄).
- 활성 트랙(RG 발송관제)과 무관한 독립 작업이었음.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `tools/ad_cost_browser_fetcher.py` | ★광고비 브라우저 페처(login/run, storage_state) |
| `tools/com.ohisell.adcost.plist` | launchd 매시 실행 템플릿 |
| `tools/README_ad_cost_local_fetcher.md` | 셋업·운영 가이드 |
| `tools/requirements-local.txt` | 로컬 의존성(playwright) — prod 불필요 |
| `tools/ad_cost_local_fetcher.py` | curl 방식(1회용 폐기, 진단기록 보존) |
| `backend/app/services/coupang/ad_cost_sync.py` | ingest_ad_cost SA + cookie_status staleness |
| `backend/app/routers/coupang_ops.py` | `/ad-cost/ingest` 토큰 엔드포인트 (936행~) |
| `backend/app/routers/sync.py` | realtime sync(주문 병렬, coupang_ad 제거) |
| `backend/app/database.py` | SQLite WAL/busy_timeout 하드닝 |
| `frontend/src/components/Layout.tsx` | 광고비 중단 전역 배너 |

## 5. 알려진 이슈 / 주의사항
- **광고비 운영**: launchd가 매시 자동 fetch·push. Mac이 켜져 있어야 동작(꺼지면 그 시간 중단, 데이터는 마지막값 유지). headless라 창 안뜸. 로그 `~/.ohisell_ad_fetcher.log`.
- **세션 만료 시(며칠~몇주 뒤)**: run 로그 "세션 만료" + 대시보드 stale 배너 → `cd backend && ./.venv/bin/python3 ../tools/ad_cost_browser_fetcher.py login` 1회 재실행.
- **headless 장기 안정성 미확정**: 4회(login+run3) 헤드리스 성공했으나 며칠 단위 관찰 필요. 막히면 `~/.ohisell_ad_fetcher.json`의 `"headless": false`로 전환(창이 뜸).
- **실패한 접근(반복 금지)**: curl 재생=1회용 / Chrome 디스크쿠키 이식=인증쿠키가 메모리에만 있어 불가(aid/cmgbdgw-sid 디스크 미저장, 광고 로그인은 Chrome Profile 11) / Playwright 영속프로필=세션쿠키 종료시 소실. → **storage_state가 정답.**
- 민감 임시파일(Chrome Safe Storage 키 등)은 세션 중 모두 삭제 완료.
- 쿠팡 Open API는 서버 IP 화이트리스트 → 주문 sync는 prod에서만 정상(로컬 403).

## 6. 다음에 할 작업 (미완료)
- [ ] 광고비 브라우저 페처 **며칠 관찰** — launchd 매시 run이 헤드리스로 계속 성공하는지(`~/.ohisell_ad_fetcher.log` + 대시보드 배너). 끊기면 headless:false 검토.
- [ ] RG 발송관제 트랙 S7 — 요일/휴일별 판매속도 세분화 (데이터 누적 후, 활성 트랙)
- [ ] (선택) ad_cost_local_fetcher.py(curl 폐기본) 정리 시점 결정
- [ ] vendor_ids 확인: Jino curl은 104438581·104997005였음(기존 코드 _DEFAULT_VENDOR_IDS는 104964791 — 불일치, 추후 확인)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-adcost-akamai_20260605.md 읽고 이어서 작업해줘
```
