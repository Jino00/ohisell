# 세션 인수인계: ohisell 네이버 광고 — X1a T1·T2·T3 완료 (쓰기 어댑터 + 실쓰기 개방)
> 저장일시: 2026-07-10 15:48 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것. 그 다음 필독: `docs/PLAN_naver-ad-execution-loop.md` §0(방향 고정) → §7(체크리스트).

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `Ohiselling/.claude/worktrees/gracious-heyrovsky-98a1bb` — **이 세션부터 네이버 트랙의 현행 워크트리**(브랜치 `claude/naver-ad-execution-loop-13db01`). 구 admiring-solomon 워크트리의 X0 커밋(`eb81bc0`~`7284c2f`, 브랜치 `claude/missing-track-recovery-c90197`)은 이 브랜치에 **fast-forward 병합 완료** — 더 이상 그쪽에 고유 커밋 없음.
- prod: `ssh os.ohitech.co.kr`, 백엔드 `/home/ubuntu/ohisell/backend`(pm2 `ohisell-backend`, venv=`.venv`), DB `/home/ubuntu/ohisell/backend/ohisell.db`
- 로컬 테스트: 이 워크트리엔 venv 없음 — **인접 워크트리 venv 재사용**: `cd backend && PYTHONPATH=. "../../admiring-solomon-b4f056/backend/.venv-test/bin/pytest" -q` (Python 3.9.6. 주의: 그 venv의 `bin/python`은 깨져 있고 `bin/pytest`·`bin/python3.11`만 정상)
- 미push 커밋 9개(`4fa3fe2`~`56b39de`+X0분). push는 Jino 지시 시에만.

## 2. 이번 세션 완료 목록
- ✅ **선행 정리**: 이 워크트리 브랜치에 X0 커밋이 없던 것을 발견 → `claude/missing-track-recovery-c90197`을 fast-forward 병합(작업 유실 0 확인)
- ✅ **X1a T1 (커밋 `4fa3fe2`)**: swagger `ncc-heroes-ncc.json` 원본이 전 워크트리·스크래치에서 유실 → `naver/searchad-apidoc` **gh-pages** `assets/json/`에서 재확보, `docs/references/data/`에 커밋(재유실 차단). `docs/references/27_naver_sa_write_api_recon.md` 작성(제외키워드 POST/GET/DELETE·keyword bidAmt PUT·userLock 3계층 PUT + 왕복 실측 시나리오 §6). **prod 라이브 읽기 실측 3건**(restricted-keywords GET 200 `[]`·adgroup·keyword — 쓰기 0건): `/api` prefix 제거 확인, 기존 `_headers` 서명 유효, 우리 adgroup=WEB_SITE. swagger 유실 교훈 failures.jsonl 기록.
- ✅ **X1a T2 (커밋 `10cd1cb`+`02982a7`)**: `backend/app/services/naver_ad/naver_sa_writer.py` 신규 — 제외키워드 쓰기 유일 어댑터(add/delete/get). 성공 판정=재조회 실측만, 쓰기 무재시도(비멱등), WEB_SITE 사전검증, 중복(기존·요청내) 차단, 복수행 모호성 fail-closed, created_ids는 after에서 파생. **codex 3라운드**: R1 4건+R2 1건 전부 동의·수정 → R3 PASS. 테스트 20개.
- ✅ **X1a T3 (커밋 `59c5bc2`+`2e5b808`)**: `naver_execution_harness` 실쓰기 연결 — `OPEN_ACTIONS={'add_negative_keyword'}` 첫 개방 + `_WRITE_EXECUTORS` 디스패치(이중 방벽). `naver_proposals.adgroup_id` 컬럼 추가(마이그레이션 `a1b2c3d4e5f6`, 스크래치 SQLite로 up/down 라이브 검증) + proposal_writer가 exclusion 행의 adgroup_id 저장(`NaverProposal(**p)` 자동 통과라 파이프라인 코드 변경 0). 실행 시맨틱: **원자적 클레임**(조건부 UPDATE→'executing', rowcount!=1→AlreadyExecutedError)→writer→성공 시 change_log(before/after 실측 JSON+created_ids)+approved 복원 / 실패·사전가드 결함 전부 change_log 기록+'failed' 종결(재승인만 재시도 경로). 사전 가드 2중: **target_type='search_term' 필수**(bid_proposal 격상 경로의 nkw-ID 오등록 차단, Sonnet 발견)·adgroup_id 필수. **codex 2사이클**: R1 3건(클레임우선·가드기록·dedup키 adgroup_id)+R2 1건(클레임 원자화) 전부 동의·수정 → R3 PASS. 테스트 총 935 passed.
- ✅ §7 체크리스트 3항목 즉시 갱신(T1·T2·T3), review-log/timeline 기록
- ✅ 백로그 칩 발행: alembic 신규 DB 체인 결함(`e8f0d3882c59`에서 oauth_tokens 부재) — **Jino가 별도 세션(task_14179cb0)으로 시작함, 진행 중**

## 3. 확정된 결정사항 (번복 금지)
- D-NAO-34 구조·개방 순서 유지(변경 없음). D-NAO-35 모델 배분 실증: **fable 메인(설계·리뷰·codex 평결) + Sonnet 서브에이전트(model:'sonnet'로 Agent 디스패치, TDD 구현) 구조로 T2·T3 완주** — Jino가 이 구조 확인함.
- T2 스코프 결정(fable): writer는 제외키워드 3함수만 — userLock·bidAmt 함수는 X1b 개방 시점에 추가(라이브 검증 불가능한 코드 미리 안 쌓음).
- T3 설계 결정(fable): adgroup_id는 **제안 생성 시 저장**(ref 27 §8-1 전자) — 실행 시 재해석은 모호성·드리프트로 기각. 성공 시 status='approved' 복원('executed' 신규 상태 도입 안 함 — 실행 마커는 executed_change_log_id 유지). 'executing' 잔존=크래시 흔적, 자동 복구 금지(사람 조사).
- 수용 잔여(무해 판정): `_guard_failure` 동시 중복 감사행 가능(API 쓰기 없음) / 실패 change_log의 before_value=None(writer 예외가 스냅샷 미탑재 — 필요 시 X1b에서 WriteError에 before 탑재 검토).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-ad-execution-loop.md` | §0 방향고정·§7 체크리스트(T1~T3 [x]) — 진행 위치의 유일 진실 |
| `docs/references/27_naver_sa_write_api_recon.md` | 쓰기 API 스펙(T2·T3의 근거, §6=왕복 실측 시나리오) |
| `docs/references/data/ncc-heroes-ncc.json` | 네이버 공식 swagger 보존본 |
| `backend/app/services/naver_ad/naver_sa_writer.py` | 쓰기 유일 어댑터(T2, 테스트 20) |
| `backend/app/services/naver_ad/naver_execution_harness.py` | 실쓰기 개방·클레임·기록(T3) |
| `backend/alembic/versions/a1b2c3d4e5f6_add_naver_proposal_adgroup_id.py` | adgroup_id 마이그레이션(**prod 미적용**) |
| `backend/app/services/naver_ad/proposal_writer.py` | adgroup_id 저장+dedup 키 확장 |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **prod 배포 대기물**: 마이그레이션 `a1b2c3d4e5f6` + 코드 5파일. 배포 절차는 기존 관례(DB 백업→rsync→alembic upgrade→pm2 재시작, AppleDouble 제거). T4까지 묶어 배포해도 됨 — 콘솔 없인 실행 경로가 어차피 없음.
- ⚠️ **실쓰기는 코드상 개방됐지만 실제로는 아직 불가능**: approved 전환 수단(T4 콘솔 버튼·라우터)이 없고 카나리 미지정(X0-2 연기). 왕복 실측(ref 27 §6)은 Jino가 저위험 캠페인 지정 후에만.
- ⚠️ bid_proposal 격상 경로의 negative_keyword(target_type='keyword')는 실행 시 MissingExecutionTargetError로 fail-closed — X1b에서 이 유형의 올바른 액션(키워드 정지 등) 재설계 필요.
- fable 한도 77%+ 사용(7/12 20:00 재설정) — T4 이후 설계 판단은 아껴 쓸 것. 리서치·구현은 Sonnet.
- alembic oauth_tokens 백로그는 별도 세션 진행 중 — 이 트랙에서 중복 수정 금지.

## 6. 다음에 할 작업 (미완료)
- [ ] **X1a T4: 콘솔 승인 버튼 활성화** — 제안 카드 "승인"(disabled 해제)→status='approved'→실행 API(신규 라우터, Confirm 후 호출). +codex 연기 항목: `/expert-reviews` 라우터에 run status=ok 조인(이 라우터를 만질 때 함께). 프론트(React)+백엔드 라우터 — 구현 Sonnet, 설계 판단만 fable.
- [ ] X1a T5: E2 위임 스위치(expert_delegated_types, Jino 전용 UI)
- [ ] X1a T6: 정보성 pending 경량화 구현(D-NAO-37: 차등 TTL·브리핑 접기·백로그 일괄 expired)
- [ ] X1a 완료기준① 라이브 왕복(카나리 지정 후) → 이후 X1b(정지재개→입찰+가드레일)
- [ ] prod 배포(마이그레이션 포함 — T4와 묶어서 권장)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

Ohiselling/.claude/worktrees/gracious-heyrovsky-98a1bb/.claude/memory/HANDOFF_ohisell-naver-ad-X1a-T1T2T3-done_20260710.md 읽고 이어서 작업해줘. 네이버 광고 실행 루프(X) 스프린트, X1a T4(콘솔 승인 버튼)부터.
