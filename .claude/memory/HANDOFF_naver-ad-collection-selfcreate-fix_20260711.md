# 세션 인수인계: 네이버 일별 수집 자체생성 복구
> 저장일시: 2026-07-11
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로(워크트리): `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/competent-nobel-e2b7e8`
- 브랜치: `claude/ohisell-naver-ad-handoff-50ba46` (main 병합 완료, main=`205cffa`)
- prod 서버: `ssh sellc.ohitech.co.kr` → `~/ohisell/backend` (배포=rsync 파일복사, git 아님)
- prod 실행: pm2 `ohisell-backend` (uvicorn :8001), 외부 HTTPS `https://sellc.ohitech.co.kr/api/...`
- prod에서 코드 실행/검증: `cd ~/ohisell/backend && .venv/bin/python3 ...` (app import 시 `PYTHONPATH=/home/ubuntu/ohisell/backend` 필요). **표준 방법** — pip 설치·앱 별도 기동만 금지.
- 주요 환경변수: `NAVER_SA_*`(backend/.env, HMAC-SHA256 키)

## 2. 이번 세션 완료 목록
- ✅ `backend/app/services/naver_sa_ad_fetcher.py` — 자체생성 로직 신설: `create_stat_report(제네릭 POST)`, `ensure_reports_built`(생성·BUILT폴링·dedup·터미널ERROR/NONE재생성·개별skip·타임아웃), `_json_list`(빈바디→[]), 헬퍼 `_report_status_by_kst_date`/`_poll_until_built`/`_ensure_one_report_built`/`_stat_dt_to_kst_date`. `create_expkeyword_report`는 래퍼로. fetch_* 4곳(AD 2·AD_CONVERSION 2·search_term 1)에 조회 전 `ensure_reports_built` 배선.
- ✅ `backend/tests/test_naver_report_self_create.py` — 신규 18 테스트(create/ensure/dedup/pending재사용/터미널재생성/timeout/생성실패/자격증명없음/빈바디 회귀/배선). 전체 932 passed·회귀 0.
- ✅ `docs/PLAN_naver-report-self-create.md` — 계획서(근본원인·설계·TDD·백필·검증).
- ✅ `docs/tracks/active/track_naver-ad-optimization.md` — D-NAO-41 기록.
- ✅ `claude-progress.txt` — 현재 상태 갱신.
- ✅ codex review — GATE PASS(P1 0), P2 1건(터미널 잡 영구skip) 수정·회귀테스트.
- ✅ prod 배포 — PR #14(`ce69ff3`)·#15(문서) main 병합, 단일파일 rsync sha256 일치(`185ec5c2...`), pm2 재시작 정상.
- ✅ 라이브 백필 — 크론 잡 3개 `.venv/bin/python3`로 실행(3일 창 07-08~07-10). **실측(원칙22)**: `naver_ad_daily` 07-10 43→**1669행**, `search_term` 07-10 **7289행 등장**, 07-08/09 갱신.
- ✅ failures.jsonl 해결 기록.

## 3. 확정된 결정사항
- **D-NAO-41**: 네이버 수집은 자족(self-create) 구조 — 외부(MOP/네이버 정기보고서)가 만들어준 보고서에 기생하지 않고 우리가 직접 POST 생성·폴링·다운로드. AD·AD_CONVERSION·SHOPPINGKEYWORD_DETAIL·EXPKEYWORD 4종 전부 POST 자체생성 가능(라이브 실증). X 스프린트와 독립한 별개 결함.
- **근본원인 확정**: `/stat-reports`가 계정에 보고서 0개면 HTTP 200+빈 바디(0바이트)→`resp.json()` 크래시. 우리 수집기가 AD/CONV/SHOPPING을 조회만 하고 생성 안 했음(EXPKEYWORD만 생성). 2026-07-10 MOP 유닛 종료로 기생 소스 끊김.
- **X 스프린트 상태 불변**: X2+X3 배포 완료, 카나리 지정은 Jino 보류(이번에도 재확인). 카나리는 프로그램 성숙 후.
- **세션 기록 방침(Jino 지시 2026-07-11)**: 전역 자동 archive 기능 사용 금지. 인계는 프로젝트 로컬 파일(`claude-progress.txt`·`docs/tracks/`·`.claude/memory/`·`failures.jsonl`)로만.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/naver_sa_ad_fetcher.py` | 자체생성 로직(핵심 수정) |
| `backend/tests/test_naver_report_self_create.py` | 18 테스트(신규) |
| `docs/PLAN_naver-report-self-create.md` | 계획서(근본원인·설계·TDD) |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙 마스터(D-NAO-41) |
| `backend/app/services/scheduler_service.py` | 크론 잡 3개(sync_naver_sa_ad_costs/ad_daily/search_term), 07:00/07:30/07:40 |
| `docs/PLAN_naver-ad-execution-loop.md` | X 스프린트 방향(§0)·체크리스트(§7) |

## 5. 알려진 이슈 / 주의사항
- **크론 자연 경로 미확인**: 백필은 잡 함수 수동 실행(= 크론과 동일 코드). 스케줄이 스스로 발화하는 자연 통과는 내일(2026-07-12) 07:00~07:40에 확정 — 원칙22.
- prod 백업 파일 `naver_sa_ad_fetcher.py.bak-20260711-selfcreate` 존재(문제 시 롤백용).
- 진단 중 네이버에 07-10 보고서 생성함 — 광고 무접촉·자동 만료. 정리 불필요.
- EXPKEYWORD는 백필 시 0행(파워링크 확장검색 데이터 없음, 정상).
- prod DB=SQLite `~/ohisell/backend/ohisell.db`(읽기 검증은 sqlite3로).

## 6. 다음에 할 작업 (미완료)
- [ ] **2026-07-12 아침 크론 자연 재확인**: 07:00/07:30/07:40 실행 후 `naver_ad_daily` 07-11 등장·scheduler last_status=ok 확인(원칙22).
- [ ] 카나리 지정(Jino 판단, 보류 중) → 지정 시 X1a/X1b/X2 라이브 왕복 검증.
- [ ] X 스프린트 재개 시 `docs/PLAN_naver-ad-execution-loop.md` §0→§7 필독.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_naver-ad-collection-selfcreate-fix_20260711.md 읽고 이어서 작업해줘
