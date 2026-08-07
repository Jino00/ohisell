# 세션 인수인계: ohisell 네이버 광고 — X0 선결 완료 + expert_ledger 멱등 버그 수정 (트랙 "실종" 해프닝 포함)
> 저장일시: 2026-07-10 오후
> 새 대화 시작 시 이 파일을 먼저 읽을 것. 그 다음 필독: `docs/PLAN_naver-ad-execution-loop.md` §0(방향 고정) → §7(체크리스트).

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `Ohiselling/.claude/worktrees/admiring-solomon-b4f056` (**네이버 트랙은 반드시 이 워크트리에서** — 세션 시작 시 `git branch --show-current` 확인 습관)
- 현재 브랜치: `claude/missing-track-recovery-c90197` (이 세션 커밋 2개: `eb81bc0` 멱등 수정, `c4445b9` D-NAO-37)
- prod: `ssh os.ohitech.co.kr`, 백엔드 `/home/ubuntu/ohisell/backend`(pm2 `ohisell-backend`, venv=`.venv`), DB `/home/ubuntu/ohisell/backend/ohisell.db`
- 로컬 테스트: `backend/`에서 `PYTHONPATH=. .venv-test/bin/pytest`(주의: venv가 `.venv-test`, `python -m pytest` 안 됨)
- ⚠️ 이 워크트리는 iCloud라 `git log` 류가 타임아웃 잦음 — 파일 직접 읽기 우선

## 2. 이번 세션 완료 목록
- ✅ **트랙 "실종" 원인 규명**: 데이터 손실 아님 — 루트 폴더가 `feat/ohitech-ad-cost` 브랜치를 켜놔서 안 보였던 것. 네이버 트랙 최신본은 이 워크트리에 온전. (재발 시: 삭제 의심 전에 브랜치부터 확인)
- ✅ **D-NAO-35**: 모델 배분 재확정 — 설계·계획=fable(**2026-07-12까지 한시**), 구현=Sonnet, 7/12 이후 fable 미가용 시 Opus 복귀. (기존 D-NAO-34의 불명확한 "fable 승계" 구절은 Jino 지시로 삭제 후 이 결정으로 대체)
- ✅ **D-NAO-36**: ref 25 대조 검증 — G4 순위유지는 X 스코프 밖 명기, G4~G9+논문③④를 계획서 **§8 승계 큐** 신설 등재
- ✅ **X0-1 완료**: Ava 크론 401은 별도 세션이 수리(+ 09:23 opus 미닫힘 JSON 펜스 폴백 배포). 검증 중 **멱등 버그 발견**: `expert_ledger.record()` dedup이 status를 안 봐서 degraded run이 당일 성공 재시도의 평결 43건을 조용히 삼킴(3회 라이브 실행으로 실증). **수정**: dedup에 `status=='ok'` 필터 — Sonnet 서브에이전트 TDD(`test_record_does_not_dedup_against_degraded_run_same_day_same_hash`), 904 passed 회귀 0, codex review PASS(대화 2라운드 합의), prod 배포(sha256 검증+DB백업 `ohisell.backup-20260710-dedupfix.db`)+pm2 재시작 → **run id=2 status=ok·평결 44행(agree42/partial1/commentary1) 라이브 확인**. failures.jsonl 기록 완료.
- ✅ **X0-2 연기 확정**: Jino "카나리 캠페인은 프로그램 완성되면 정하자" — 코딩은 카나리 없이 진행, 실집행 라이브 검증 단계만 카나리 지정 후
- ✅ **X0-3 완료 = D-NAO-37**: 정보성 pending 경량화 정책 확정(하단 3-①②③). 구현은 X1a T6
- ✅ 계획서 §7 갱신(X0-1·X0-3 [x]) + 트랙 D-NAO-35/36/37 + progress 갱신

## 3. 확정된 결정사항 (번복 금지)
- **D-NAO-34** 구조 유지: X0→X1a(쓰기+제외키워드+반자동)→X1b(정지재개→입찰+가드레일)→X2(GRM식 당일 플라이트)→X3(DHEB+GAVE). 개방 순서·가드레일 임의 변경 금지, 예산 개방은 스코프 밖
- **D-NAO-35** 모델 배분: 설계=fable(~7/12)·구현=Sonnet. 7/12 후 Opus 복귀
- **D-NAO-36** G4 순위유지 후속(§8 승계 큐: G4·G5·G6·G7·G8·G9·논문③④)
- **D-NAO-37** 경량화: ①차등 TTL(trigger_*·account_brief=D+1, anomaly*=D+3, 실행형=14일) ②브리핑 접기(정보성=집계 블록 1개, expected_ids 제외, Ava는 실행형 전건+총평) ③백로그 ~180건 소급 일괄 expired(prod 백업 후, 행 보존)
- codex 합의 연기 2건(계획서에 명기됨): `/expert-reviews` run-status 조인→X1a T4에서 / (as_of,briefing_hash,status=ok) 부분 유니크 인덱스→X2 전 재검토

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-ad-execution-loop.md` | X 스프린트 계획서 — §0 방향고정·§7 체크리스트(진행 위치)·§8 승계 큐 |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙 단일 진실 원천(D-NAO-1~37) |
| `docs/references/25_mop_pro_gap_analysis.md` / `26_bidding_papers_survey.md` | 갭 G1~G9 / 논문 TOP5 |
| `backend/app/services/naver_ad/expert_ledger.py` | 이번 수정 파일(record dedup) |
| `backend/app/services/naver_ad/expert_briefing_builder.py` | T6에서 접기 구현할 곳(_MAX_PROPOSALS_CHARS=20000) |
| `backend/app/services/naver_ad/proposal_pipeline.py` | T6 TTL 확장할 곳(`_expire_stale_pending`, `_PROPOSAL_EXPIRY_DAYS=14`) |
| `backend/app/services/naver_ad/naver_execution_harness.py` | 정보성 유형 목록·OPEN_ACTIONS(T3에서 실쓰기 연결) |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **X1a T1의 swagger 파일 `ncc-heroes-ncc.json`을 이 워크트리에서 못 찾음**(중단 직전 탐색 — backend .venv 잡음만 나옴). MOP 리뷰 세션(recursing-engelbart-6bb9d5 워크트리)에서 수집했다고 기록돼 있으니 **다른 워크트리·스크래치 탐색 필요**. 정 없으면 네이버 공식 GitHub(naver/searchad-apidoc)에서 재확보
- ⚠️ **첫 실쓰기(제외키워드 왕복 실측)는 Jino 확인 후에만** — 카나리 미지정 상태. 저위험 캠페인(예: 예산 1만원 `벌크`) 1개 지정받고 진행
- 내일(07-11) 08:05 크론이 크론 경로 ok run을 자연 재확인해줄 것(확인 권장). 단 D-NAO-37 구현 전이라 브리핑 절삭은 여전히 발생(오늘 146건)
- Ava LLM 호출 1차 타임아웃 120초가 빠듯(실측 ~116초, 재시도 사다리가 흡수) — T6 즈음 상향 검토 가능(선택)
- prod DB 백업 2개 존재: `ohisell.backup-20260710-expertfix.db`(별도 세션)·`-dedupfix.db`(이 세션)

## 6. 다음에 할 작업 (미완료)
- [ ] **X1a T1(진행 중이던 것)**: swagger 파일 찾기 → `docs/references/27_naver_sa_write_api_recon.md` 스펙 문서화(제외키워드 추가/삭제·keyword bidAmt PUT·userLock PUT) → 왕복 실측은 Jino 캠페인 지정 후
- [ ] X1a T2 `naver_sa_writer` SA (Sonnet TDD) → T3 execution_harness 제외키워드 개방 → T4 콘솔 승인 버튼(+run-status 조인) → T5 위임 스위치 → T6 경량화 구현(D-NAO-37)
- [ ] 이후 X1b → X2 → X3 (계획서 §3)
- [ ] (병행 대기) E1b Ava 연동은 AI_office 별도 세션 / 카나리 지정은 프로그램 완성 후 Jino

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

Ohiselling/.claude/worktrees/admiring-solomon-b4f056/.claude/memory/HANDOFF_ohisell-naver-ad-X0-done+dedupfix_20260710.md 읽고 이어서 작업해줘. 네이버 광고 실행 루프(X) 스프린트, X1a T1(쓰기 API 스펙 문서화)부터.
