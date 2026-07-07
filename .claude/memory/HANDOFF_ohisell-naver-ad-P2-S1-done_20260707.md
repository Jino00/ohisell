# 세션 인수인계: 네이버 SA 광고 트랙 — P2-S1(데이터 기반) 구현 완료·prod 배포·라이브 검증
> 저장일시: 2026-07-07 17:20 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것. **이전 HANDOFF(P2-design-done, 순수 설계 세션)를 대체함.**

## 1. 프로젝트 위치 및 환경
- **작업 워크트리(불변)**: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/admiring-solomon-b4f056` (브랜치 `claude/admiring-solomon-b4f056`). 이 트랙은 반드시 여기서만 작업(원칙20).
- prod VM: `ssh sellc.ohitech.co.kr` → `~/ohisell/backend`(포트 8001, pm2 `ohisell-backend`) + `~/ohisell/frontend/dist`(nginx). 배포=scp/rsync(git 비관리).
- 로컬 테스트: 프로젝트에 `.venv`가 없음 — 이번 세션은 스크래치 디렉터리에 격리 venv를 만들어 `pip install -r requirements.txt`로 테스트 실행(prod venv는 절대 건드리지 않음, 과거 사고 재발방지).
- 네이버 SA API 키: `backend/.env` `NAVER_SA_*`(prod에만 존재, 로컬 워크트리엔 `.env.example`만 있음). 라이브 API를 직접 두드릴 땐 `ssh sellc.ohitech.co.kr "grep NAVER_SA_ ~/.../.env"`로 값만 읽어와(read-only) 로컬 스크래치에서 실행.
- ⚠️ prod venv `.venv` 절대 pip install/uninstall 금지(anyio 삭제 크래시루프 사고 이력, failures.jsonl).

## 2. 이번 세션 완료 목록
- ✅ **신규 마이그레이션** `backend/alembic/versions/v6w7x8y9z0a1_add_naver_entity_search_term_tables.py` (down_revision=u5v6w7x8y9z0): `naver_entity`(campaign/adgroup/keyword 인벤토리), `naver_search_term_daily`(검색어 단위 성과) 2테이블. `backend/app/models.py`에 `NaverEntity`·`NaverSearchTermDaily` SQLAlchemy 모델 추가.
- ✅ **fetcher 확장** `backend/app/services/naver_sa_ad_fetcher.py`: `get_adgroups`·`get_keywords`·`create_expkeyword_report`·`get_report_job`·`list_report_jobs`·`fetch_search_term_daily`·`fetch_campaign_daily_backfill`·`fetch_keyword_volumes` 신규 함수. `get_campaigns_full`에 `status` 필드 추가(버그 수정).
- ✅ **서비스 모듈 5개 신규**(`backend/app/services/naver_ad/`):
  - `entity_sync.py` — `collect_entities`(캠페인→그룹→WEB_SITE만 키워드 순회)+`sync_entities`(upsert, keywordstool 보강필드 보존, 사라진 엔티티는 status='deleted'로 표시·물리삭제 안 함).
  - `search_term_ingest.py` — `ingest_search_term_daily`(shopping/expkeyword 소스별 snapshot 교체)+`request_missing_expkeyword_reports`(비어있는 날짜만 POST 생성, 자기치유).
  - `campaign_backfill.py` — `backfill_campaign_daily`(/stats 730일/92일청크, `naver_ad_daily`에 `adgroup_id='__backfill__'` sentinel로 적재해 P0 실단위 행과 구분).
  - `campaign_target_resolver.py` — `resolve_target_roas`(override→계정기본값 매출가중 2단만 구현, 아래 §5 참조).
  - `keyword_volume_sync.py` — `sync_keyword_volumes`(30일 클릭<10 키워드만 keywordstool 대상).
- ✅ **cron 3개 등록**(`backend/app/services/scheduler_service.py`): `sync_naver_entity`(07:35)·`sync_naver_search_term`(07:40)·`sync_naver_keyword_volume`(일요일 09:00).
- ✅ **테스트** `backend/tests/test_naver_ad_p2s1.py` 9개 신규, 전체 539 pass(로컬 격리 venv).
- ✅ **prod 배포**: 백업(`~/ohisell_bak/ohisell_20260707_165445.db`, `backend_naver_p2s1_20260707_165445`) → `alembic upgrade head`(v6w7x8y9z0a1) → scp 9파일(sha256 전수검증) → pm2 restart → 수동 1회 실행으로 라이브 검증.
- ✅ **버그 발견·즉시 수정**: `_headers(path)`가 HMAC 서명 문자열에 `.GET.`을 하드코딩 → POST(EXPKEYWORD 생성)가 항상 403 invalid-signature. `method` 파라미터 추가(기본 GET, POST 콜만 명시)로 수정. 재배포 후 EXPKEYWORD 생성 200 확인. failures.jsonl에 기록.
- ✅ **문서**: `docs/references/22_naver_sa_p2s1_recon.md` 신규(라이브 정찰+실행 결과 전체), 트랙 파일·`claude-progress.txt` 갱신.
- ✅ **커밋 2개**(브랜치 `claude/admiring-solomon-b4f056`, **미push**): `75ee582`(P2-S1 구현) → `d9402fd`(fetcher 버그수정+라이브검증 기록).

## 3. 확정된 결정사항 (번복 금지 — 상세는 트랙 파일이 정본)
- **실측 정정(중요)**: 트랙의 "파워링크 등록 키워드 4,936개"는 틀렸음 — 최근 16일 노출 이력이 있는 키워드 수였을 뿐, **등록 전체는 90,150개**(18배). D-NAO-18 죽은키워드 위생의 실제 스케일이 예상보다 훨씬 큼. naver_entity의 keyword 행은 **WEB_SITE(파워링크)만** 동기화(SHOPPING은 33건뿐이라 그룹 단위 진단으로 충분, AD 리포트에서도 keyword_id='-'로만 집계됨).
- **D-NAO-17 백필 한도 확정**: `/stats`는 최근 **730일**까지, 호출당 daily breakdown은 **92일** 한도(둘 다 에러 메시지로 명문화된 값, 추측 아님) — 90일 청크로 분할. 캠페인 grain만 가능(그룹/키워드 세부 불가), 직접/간접 전환 분리도 불가.
- **SHOPPINGKEYWORD_DETAIL 컬럼 확정**(16열): imp=col11·clk=col12·cost=col13(±1원)·rank_sum=col14(prod naver_ad_daily 동일 adgroup·날짜 합계 대조로 실증). col7·8·9·15는 의미 미확정 — 저장 안 함.
- **EXPKEYWORD는 자동 생성 안 됨** — POST `/stat-reports {"reportTp":"EXPKEYWORD","statDt":"YYYYMMDD"}`로 생성 후 비동기 BUILT 대기. `search_term_ingest`가 없는 날짜만 생성 요청하고 다음 크론이 자기치유로 수집(폴링 없음).
- **naver_entity는 upsert 방식**(전체 delete 후 재삽입 아님) — keywordstool로 채운 `monthly_volume`/`competition`이 재동기화 때마다 날아가는 걸 방지. 사라진 엔티티는 `status='deleted'`로만 표시(물리 삭제 금지 — 이력 보존, search_term_daily 등에서 참조 가능성).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_naver-ad-optimization.md` | **정본** — D-NAO-1~21, P2 체크리스트(S1 완료 표시) |
| `docs/references/22_naver_sa_p2s1_recon.md` | 이번 세션 라이브 정찰+실행 결과 전체(신규) |
| `backend/app/models.py` (NaverEntity·NaverSearchTermDaily) | 신규 테이블 모델 |
| `backend/alembic/versions/v6w7x8y9z0a1_...py` | 신규 마이그레이션 |
| `backend/app/services/naver_ad/entity_sync.py` 외 4개 | 이번 세션 신규 서비스 모듈 |
| `backend/app/services/naver_sa_ad_fetcher.py` | HMAC 인증(POST 버그 수정 포함)·신규 fetch 함수 8개 |
| `backend/app/services/scheduler_service.py` | cron 3개 신규 등록 |
| `backend/tests/test_naver_ad_p2s1.py` | 이번 세션 신규 테스트 |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **`campaign_target_resolver`의 "②쇼핑 상품BEP 연결" 단계 미구현**: 캠페인/그룹→상품(channel_product_id) 연결 데이터 소스가 아직 없음. 이름 기반 추정 매칭은 금전 판단 근거로 약해 시도 안 함(추정 금지 원칙). 현재는 ①override→③계정기본값(매출가중) 2단만 동작. **S2(진단 엔진) 착수 전 재검토 필요** — S2가 이 단계를 실제로 필요로 하는지부터 판단.
- EXPKEYWORD는 이번 세션엔 생성(REGIST) 요청만 확인, 실제 BUILT까지의 소요시간·다운로드 후 컬럼 레이아웃이 SHOPPINGKEYWORD_DETAIL과 동일한지는 **미검증**(같은 포맷으로 가정하고 파서 재사용 중) — S2 착수 전 1회 다운로드 확인 권장.
- 라이브 조사 중 "계정 전체 403"처럼 보인 순간이 있었는데 실제 원인은 진단스크립트가 `app.database`를 임포트 안 해 `load_dotenv()`가 안 돌아 자격증명이 빈 문자열이었던 것뿐(실제 API 차단 아님) — 향후 유사 진단 스크립트 작성 시 `import app.database`를 맨 위에 넣을 것.
- `naver_entity` 동기화는 순차 HTTP 호출(~1,030개 endpoint: 캠페인 43+그룹 990+WEB_SITE키워드 그룹당 1콜)이라 **8~9분 소요** — 크론(07:35)이 자동 처리하니 문제 없지만, 수동 재실행 시 오래 걸림을 감안할 것.
- 로컬 워크트리에 `.venv` 없음 — 테스트 돌릴 땐 스크래치 디렉터리에 임시 venv 생성 후 `pip install -r requirements.txt`(+ pytest httpx 추가 설치, requirements.txt엔 없음).

## 6. 다음에 할 작업 (미완료)
- [ ] **P2-S2 진단 엔진 구현 착수** (/model sonnet): `account_diagnosis_sa`(쿠팡 판정 로직 이식 — 출혈/승자·굶는승자/확장버킷/제외후보/키워드 3단분류 D-NAO-18/악순환·학습불능 감지) + `GET /naver-ad/diagnosis` + 콘솔 진단 보드 UI. 완료기준(라이브): 실측 베이스라인 재현(확장버킷42%·출혈30개·굶는승자4개·쇼핑16그룹 미달이 보드에 잡히는지 대조).
- [ ] 착수 전: EXPKEYWORD 실제 다운로드 1회 확인(컬럼 레이아웃 검증) 권장.
- [ ] `campaign_target_resolver` "②쇼핑 상품BEP 연결" 재검토(§5 참조) — S2에 필요한지 먼저 판단.
- [ ] (선택) 판매가 커버리지 개선: 미주문 196상품 BEP 위해 네이버 상품 API 가격 동기화 검토 → actionable BEP 500+.
- [ ] 트랙/계획서 파일 정리(메인 워크트리에도 흔적 있었던 사고, 이미 이 브랜치로 이전됨 — Jino 확인만 필요).
- [ ] 브랜치 push 여부(Jino 결정) — 이번 세션 커밋 2개(`75ee582`, `d9402fd`) 포함 미push 상태.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용 (`/model sonnet` — 설계는 끝났고 구현 이어가는 것):

`.claude/memory/HANDOFF_ohisell-naver-ad-P2-S1-done_20260707.md` 읽고, admiring-solomon-b4f056 워크트리에서 네이버 광고 트랙 P2-S2(진단 엔진) 구현 시작해줘.
