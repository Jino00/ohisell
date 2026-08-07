# 세션 인수인계: 스케줄러 미발동 근본수리(codex 4R PASS) + MOP 3축 D1 관찰
> 저장일시: 2026-07-13 15:30 (KST)
> 새 대화 시작 시 이 파일 먼저. 트랙: `docs/tracks/active/track_naver-ad-optimization.md`.
> 직전: `HANDOFF_ohisell-mop-observation+ipad-powerlink_20260713.md`(MOP 관찰 아키텍처 구축).
> ★이번 세션 성과: ①**MOP 3축 D1 관찰 실측**(전부 아직 미개입=정상) ②**04 "제안 0건" 근본원인 규명**(크론 미발동)+수동 복구 ③**스케줄러 재시작 catch-up 결함 근본수리**(codex 4라운드 대화형 PASS, PR-ready·미배포).

## 1. 환경 (직전과 동일)
- prod: `ssh sellc.ohitech.co.kr`, 백엔드 포트 8001(pm2 `ohisell-backend` id 0), DB `/home/ubuntu/ohisell/backend/ohisell.db`. non-git(file-copy 배포). SA API: `cd backend; set -a; . .env; set +a; PYTHONPATH=. .venv/bin/python3 …`.
- **코드 작업 워크트리(이번 세션)**: `naver-ad-critical-bugs-27ff24`, 브랜치 `claude/naver-ad-execution-loop-dbe167`(main 기준). ★리빙메모리(HANDOFF·comparison log·mop_ui 도구)는 여전히 `naver-ad-execution-loop-6cc75b`. 워크트리 스프롤 유지 중.
- MOP 감지기(VM crontab): `*/10 run_mop_activation.sh`(03 쇼핑 유닛6245) + `15 * * * * run_mop_keyword.sh`(아이패드 파워링크 유닛5752). 로그 `backend/mop_activation.log`·`mop_keyword.log`. ls 시각은 UTC 표기 주의(+9=KST).

## 2. MOP 3축 D1 관찰 실측 (2026-07-13 13:40 KST, 원칙22 라이브)
- **03(MOP 유닛6245, 아이폰_강화유리)**: bidYn=N 유지, 24 애드그룹 bidAmt·on/off·status **변화 0**(10:17 baseline 이후). MOP 이력게이트로 입찰 아직 미개시. 관찰 트리거=bidYn N→Y.
- **아이패드 파워링크(MOP 유닛5752)**: 키워드 2056개 CPC·on/off **변화 0**. **집행시작 07-14**라 오늘은 검수/플래닝일 = 정상. 감지기 무인 가동 중. → **오늘 학습 반영할 MOP 조정 데이터 아직 없음(내일부터)**.
- **04(우리 프로그램 카나리, 아이폰_지문방지 cmp-…008514959)**: optimizer=ours(07-12 21:22~), mode=None(BEP-ROAS). 성과 07-13 비용2193·클릭2·노출179. **실행형 제안 0건** → 아래 규명.
- **결론**: 03·04 양군 **모두 오늘 실질 입찰 개입 0** → 진짜 D1 입찰 대조 아직 불가. 로그 append: `docs/references/data/mop_ui/mop_vs_ours_03_04_comparison.md`(13:40 KST 섹션).

## 3. ★04 "제안 0건"=정상 + 내 근본원인 오독 정정 (원칙22 실패사례)
- **04 제안 0건 = 옵티마이저 정당 절제(문제 아님)**: read-only 프로브(build)가 bid_down 6건을 내 "크론만 돌면 6건 받음"으로 단정한 건 **격리 아티팩트** — 프로브 7일 윈도우 vs `run_daily` 기본 `lookback_days=15`. 15일 집계에선 적자 애드그룹 economic_ceiling 1720>현재입찰→direction=**up**인데 shopping_group_bep 보드는 손실축소용 **down만 허용** → 정당 무제안. **실제 크론(run_daily)은 04 bid_down 0, anomaly만.** 로직 정상.
- **★내가 틀린 것(원칙22 실패, 정정)**: "07-13 `account_brief` 0건 → 08:00 크론 미발동/미완주"라 단정했으나 **거짓**. `date(created_at)`가 **UTC 기준 집계**라 08:00 KST 실행분(=07-12 23:00 UTC, **account_brief id=502**)이 "07-12"로 잡힌 걸 "07-13=0"으로 오독. **실측: 오늘 아침배치 4잡 전부 정시 발동·완주** — `generate_naver_proposals.last_run_at=08:00:09 ok`, account_brief id=502 created_at 08:00:09 KST. **SchedulerState.last_run_at을 먼저 봤으면 즉시 알았을 오류.** 사고·미스파이어·미발동 전부 없었음.
- **연쇄 오류**: 없는 사고 근거로 불필요한 "복구"(`generate_naver_proposals_job` 수동 실행) → 중복 account_brief **id=544**(13:55 KST) 생성. account_brief_singleton이 date.today()=서버 UTC로 dedup해 id=502(07-12 UTC)를 못 잡음(=별개 실버그, UTC자정 경계). id=544는 무해·자연만료.

## 4. 스케줄러 미스파이어 하드닝 (사고 아님·codex 4R PASS·배포됨) + _parse_qc 수정
브랜치 `claude/naver-ad-execution-loop-dbe167`, 4커밋(222a375→ec07f6d→538723f→265f669), 2파일 +175줄. **codex GATE PASS(4R). PR #18. prod 배포 완료(Jino "유지" 결정, 재시작서 올바르게 no-op 확인).**
> ★프레이밍 정정: 이 수정은 §3의 (없던) "오늘 사고"를 고친 게 아니라 **실재하지만 오늘은 발생 안 한 취약점에 대한 하드닝**이다. 취약점 메커니즘은 codex가 독립 확인(APScheduler 기본 grace 1초+in-memory jobstore→재시작이 크론시각 걸치면 catch-up 없이 드롭). 백엔드 49회/일 재시작이라 리스크 유효. `_parse_qc`는 §5 참조(진짜 독립 버그).
- **수리 A — `_parse_qc`**(`naver_sa_ad_fetcher.py`): 네이버 keywordstool가 monthlyPcQcCnt를 int/str 혼합 반환 → int일 때 `.strip()` 크래시(`sync_naver_keyword_volume_job` 일요일 09:00). int/float/None/str 방어. 단위 10케이스 PASS.
- **수리 B — 스케줄러**(`scheduler_service.py`): job_defaults(misfire_grace_time=3600·coalesce)로 executor 지연 방어 + **명시 catch-up**(`_catch_up_morning_batch`). codex 대화 4라운드:
  - R1 [P1]: misfire_grace_time은 in-memory jobstore라 **재시작을 못 잡음**(next_run을 now 이후로 계산→놓친 발화 미인식). → 동의, last_run_at 기반 명시 catch-up 추가.
  - R2 [P1]: 놓친 잡 동시 발화 시 **순서 의존 파괴**(expert가 proposals 전에 돌면 pending 0→성공스킵→영구스킵). → cron 순서 **순차 체인**(상류 성공해야 하류, 실패 시 중단), 데몬 스레드·`_apply_job_event` 재사용 수동 상태기록.
  - R3 [P1-a]: proposals 잡이 freshness stale·writer 실패를 삼켜 정상반환→체인 오인 ok. → `_run_proposals_catchup_verified`가 run_daily 직접 호출·stage_status(freshness+proposal_writer) 검증, 미완주면 예외.
  - R3 [P1-b](정상 08:05 expert 크론이 catch-up과 경합): **기각(근거 인정됨)** — expert_desk_job docstring(2026-07-09, Jino승인)이 "유실 아니라 지연, pending은 다음날 재포함"으로 이미 수용한 설계.
  - R4: **P1-a 해소·P1-b 기각 인정, 신규 P1 없음 → PASS.**
- **범위 결정(Jino)**: catch-up=**네이버 아침배치 4잡만**(forecast07:50·proposals08:00·expert08:05·learning08:10), lookback 12h. 쿠팡 등 제외(blast radius). 완전 견고화(영속 jobstore)는 후속.
- 검증: py_compile OK·prod venv import OK(4함수 정의·job_defaults 적용)·`job.modify` 시각대 정합(drift 0)·판정 경계 6/6·체인 순서/중단 3케이스 PASS.

## 5. 다음 작업 (미완료)
- [x] ~~수리 B/A prod 배포 + PR~~ **완료** — 2파일 sha256 일치 배포·pm2 재시작(안정, 재시작서 catch-up 올바르게 no-op)·PR #18(Jino "유지" 결정).
- [x] ~~백엔드 49회/일 재시작 규명~~ **조사 완료 = 만성 아님**: 현재 업타임 44분·재시작 정지(안정). 49회는 아침 버스트(08:53~09:40경, 이후 4h+ 안정). 트레이스백은 전부 `rocket_refresh_status` AttributeError지만 요청 레벨 500(uvicorn 미종료)이지 재시작 원인 아님("Shutting down"=graceful=외부 배포들 정황). **rocket 500 루프는 stale 코드 구프로세스 문제였고 제 15:16 재시작(재import)으로 해소** — 현재 `/rocket/refresh-status`=200. (별건 스폰 태스크 수정이 재시작으로 반영됨.)
- [x] ~~account_brief UTC dedup 버그~~ **완료·배포됨**: `date.today()`→`kst_today()` KST일 경계. codex PASS·격리 2케이스 PASS. prod 배포·검증(budget코드 공존, sha 일치).
- [x] ~~브랜치 재조정~~ **완료(Jino "병합·재조정·배포" 지시)**: ①**PR #19**로 budget-control→main 병합(rocket #17·date-drift #16과 무충돌 자동병합, naver 294 test PASS) ②PR #18을 새 main 위로 rebase(충돌 0, account_brief가 budget proposal_writer와 자동 공존, 317 test PASS)→**PR #20**(재조정판, codex PASS)로 main 병합, PR #18 대체 닫기 ③proposal_writer만 prod 재배포(diff=account_brief만 확인, budget clobber 없음). **최종: main==prod 3파일 전부 일치.**
- [ ] **07-14~ MOP 아이패드 유닛5752 가동 관찰**: `cat backend/mop_keyword.log`(CPC·추가/제외) — bidYn N→Y 포착 → **키워드 최적화 로직에 학습 반영(D-NAO 핵심 목적)**.
- [ ] **03/04 D1 저녁~내일 재관찰**: 03 bidYn N→Y·04 성과 축적 후 진짜 대조. comparison.md append.
- [ ] **(Jino 게이트) 예산 P4**: 04 소진 캠페인 필요→증액 제안→콘솔승인→실 PUT(직전 HANDOFF §6).
- [ ] (후속) Ava 공백 수리(prod 크론 401)·영속 jobstore 완전 견고화.

## 5b. ★원칙22 교훈 (이번 세션 실패)
"account_brief 0건→크론 미발동"을 `SchedulerState.last_run_at` 확인 없이 단정 → UTC/KST 날짜 그룹핑 착시였음(실제 크론 정상). 연쇄로 없는 사고에 복구·수정·문서화. **교훈: 잡 실행 여부는 항상 last_run_at(권위 소스)부터 확인. `date(created_at)`는 UTC라 KST 일자 판정에 직접 쓰지 말 것.** failures.jsonl 기록.

## 6. 새 세션 시작 프롬프트
```
.claude/worktrees/naver-ad-execution-loop-6cc75b/.claude/memory/HANDOFF_ohisell-scheduler-catchup-fix+D1-observation_20260713.md 읽고 이어서. 복잡=Opus·단순=Sonnet. **완료·배포·main병합**: 스케줄러/parse_qc 하드닝(사고 아닌 하드닝—§5b 오독 교훈) + account_brief UTC dedup 수정 + budget-control(PR #19)·재조정(PR #20) main 병합 → **main==prod 정합**. 49재시작=만성 아님(조사완료). 우선: ①07-14 MOP 아이패드5752 가동 관찰(mop_keyword.log CPC변화)→우리 로직 학습반영 ②03/04 D1 재대조 ③예산 P4(Jino콘솔 왕복). 원칙22: 잡 실행여부는 last_run_at부터, date(created_at)는 UTC, prod file-copy 前 sha drift 확인.
```
