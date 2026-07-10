# 세션 인수인계: ohisell 네이버 광고 — X1b(정지재개→입찰) 전체 완료 + 치명적 결함 3건 발견·수정
> 저장일시: 2026-07-10 밤 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것. 그 다음: `docs/PLAN_naver-ad-execution-loop.md` §0→§7.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `Ohiselling/.claude/worktrees/naver-ad-x1b-sprint-a42eb5` — **네이버 트랙 현행 워크트리**(브랜치 `claude/naver-ad-x1b-sprint-a42eb5`)
- prod: `ssh os.ohitech.co.kr`, 백엔드 `/home/ubuntu/ohisell/backend`(pm2 `ohisell-backend`:8001, UTC 서버), DB `backend/ohisell.db`, 프론트 `https://sellc.ohitech.co.kr` ← `/home/ubuntu/ohisell/frontend/dist`
- 로컬 테스트: `cd backend && PYTHONPATH=. "../../admiring-solomon-b4f056/backend/.venv-test/bin/pytest" -q` (⚠️venv python 직접 호출 시 `python3`가 아니라 `python3.11`을 써야 함 — bin/python3 심볼릭 링크가 깨져 있음, 이번 세션 발견)
- codex: 사용한도 소진(2026-07-10 밤부터), **재설정 예정 2026-07-11 00:49 KST** — 그 전까지는 Claude 적대적 리뷰로 대체(원칙19 폴백)
- **전부 로컬 커밋만 완료 — main 미병합·origin 미push.** prod는 main 기준(PR #11까지)이라 이번 세션 변경사항은 prod에 없음.

## 2. 이번 세션 완료 목록 (X1b T1~T5 전체)

D-NAO-16 개방 순서 2·3단계(정지·재개→입찰) 개방. 커밋 순서(전부 `claude/naver-ad-x1b-sprint-a42eb5` 브랜치, 미push):

- ✅ **T1**(`55a07a3`+`c37a864`): 마이그레이션 `c3d4e5f6g7h8`(naver_proposals.target_bid INT·target_lock BOOL) + `naver_sa_writer`에 `get_keyword`·`get_campaign`·`update_keyword_bid`·`set_keyword_lock`·`set_adgroup_lock`·`set_campaign_lock` 신설(ref 27 스펙). codex 4라운드 PASS(useGroupBidAmt 미검증 1건 수정).
- ✅ **T2**(`66594b6`+`e9d7d5f`+`4cb2bca`): `guardrail_gate.py`(순수 판정) — 클램프·±15%(D-NAO-5)·쿨다운(5시간, trigger_watch 재사용값)·일일상한(3건, 신규)·스톱로스(D-NAO-20)·BEP증액금지·일예산불가침(dailyBudget=0=미설정 처리). codex: 방향불일치 2건 + daily_budget=0 1건 수정, 4라운드 PASS.
- ✅ **T3**(`2501cea`+`980d878`+`535b934`+`1f52f16`+`c2702fe`): `proposal_writer`가 target_bid 구조화 저장 + 신규 `account_diagnosis.pause_candidates`(무전환 누적비용≥스톱로스)/`resume_candidates`(우리가 정지시킨 키워드 중 정지직전 ROAS≥현재목표). codex: 부모체인 캐스케이드 미확인·N+1·캠페인별 target_roas 미반영 3건 수정. 4라운드에서 "pause/resume 미배선(T4 몫)" 지적 → 의도적 이관 기록.
- ✅ **T4**(`c540d15`+`6663583`+`00bbfea`): `naver_execution_harness`에 `_execute_update_bid`/`_execute_set_user_lock` + `_build_guardrail_context`(라이브재조회+30일실적+캠페인target_roas+당일소진+쿨다운) + `_detect_external_change`(D-NAO-13 MOP충돌경고). **codex 2라운드에서 치명적 결함 발견**(resume 루프 action 문자열 불일치) → codex 한도 소진 → **Claude 적대적 리뷰로 대체**(2라운드째) → **changed_at UTC/KST 불일치**(또 다른 치명적 결함) 발견·수정.
- ✅ **T5**(`3477e82`): "D+7/14 채점 배선 확인" 임무 수행 중 **세 번째 치명적 결함** 발견 — outcome="executed" 즉시기록이 proposal_scoreboard의 `outcome IS NULL` 필터를 영원히 못 타서 X1a 배포 이후 학습루프가 한 번도 안 돌았음. 수정.

## 3. ⭐발견한 치명적 결함 3건 (전부 이 스프린트에서 수정 완료)

1. **resume 루프 action 문자열 불일치** — `_execute_set_user_lock`이 pause/resume 둘 다 `action="set_user_lock"`으로 기록(update_bid가 bid_up/down/growth를 묶는 것과 동일 관례)하는데 `account_diagnosis.resume_candidates`는 `action.in_(("pause","resume"))`로 조회 — 문자열이 영영 안 맞아 **정지→재개 루프가 설계상 완전히 작동 불능**이었음. Jino "완벽하게 작동" 지시의 핵심 실패 지점. 수정: 방향 판별을 action 문자열이 아니라 `after_value`의 실제 userLock 값으로.
2. **changed_at UTC/KST 불일치** — `NaverChangeLog.changed_at`이 `server_default=func.now()`인데 SQLite에서 UTC 반환(실측 9시간차 확인). `executed_at`은 이미 KST(`now`)로 명시돼 있었는데 `changed_at`은 누락. guardrail_gate 쿨다운(`now-last_change_at`)이 상시 "9시간+ 지남"으로 오판정 → **D-NAO-19 쿨다운 안전장치가 fail-open으로 사실상 무력화**. X1a부터 잠재(과거엔 시간계산 소비자 없어 무해)했으나 X1b guardrail_gate가 처음 소비하며 실결함화. 수정: 전 8개 change_log 생성 지점에 `changed_at=now` 명시.
3. **D+7/14 채점루프가 X1a부터 한 번도 안 돎(가장 근본적)** — 실쓰기 성공 시 `outcome="executed"` 즉시 기록 → `proposal_scoreboard.run_daily()`는 "미검증"을 `outcome IS NULL`로 찾음(Phase 6 원안·기존 테스트 `_change()` 기본값이 이미 그렇게 설계돼 있었음, X1a T3 구현이 그 계약과 어긋났던 것) → **D-NAO-14 학습루프 핵심기능이 X1a 배포 이후 실행된 어떤 제안도 채점한 적이 없었음**. 수정: "실제 성공" 판별을 `outcome`이 아니라 `dry_run=False AND after_value IS NOT NULL`로 전환(같은 세션에서 만든 `_detect_external_change`·guardrail 쿨다운·resume_candidates 3곳도 함께).

**세 결함 모두 원칙22("됐다"는 라이브 증거로만) 정신에 부합 — 실제로 실행해서 배선이 끝까지 연결되는지 검증하는 과정에서 발견됨(단위테스트가 아니라 종단 통합테스트·시간대 실측·리뷰 재검증으로).**

## 4. 확정된 결정사항

- 쿨다운=5시간(trigger_watch.TRIGGER_COOLDOWN_HOURS 재사용, 문서 미확인 정직라벨) / 일일상한=3건(신규, 정직라벨)
- 정지·재개 proposal_type: `pause`(target_lock=True)·`resume`(target_lock=False), 둘 다 실행 액션명은 `set_user_lock` 공유
- resume_candidates 스코프: "BEP 개선"(정지직전 ROAS≥현재목표)만 구현, 계절성회복·CPC하락은 §8 승계 큐(정지 중엔 새 실적 관측 불가라 정직 경계)
- resume_candidates는 pause_candidates와 달리 부모체인(광고그룹·캠페인 on) 확인 안 함 — **의도적 비대칭**(과거 정지 근거 데이터는 부모상태와 무관해 왜곡 경로 없음, account_diagnosis.py docstring에 근거 명시). 재검토 필요시 Jino 논의 대상으로 남김.
- adgroup 단위 입찰·정지는 미구현(fail-closed 가드) — shopping_group_bep 제안(target_type='adgroup')은 실행 안 됨, §8 승계 큐 후보
- "실제 성공 판별" = `dry_run=False AND after_value IS NOT NULL`(outcome 아님) — 이 세션에서 확정된 새 관례, 향후 change_log 소비 코드는 전부 이 기준 따를 것

## 5. 핵심 파일 목록

| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-ad-execution-loop.md` §7 | X 스프린트 진행 위치(유일 진실) — X1b T1~T5 전부 [x] |
| `docs/tracks/active/track_naver-ad-optimization.md` D-NAO-39 | 이번 세션 전체 요약(트랙 단일 진실) |
| `backend/app/services/naver_ad/guardrail_gate.py` | 실행 직전 가드레일 순수 판정 SA(신규) |
| `backend/app/services/naver_ad/naver_execution_harness.py` | 실쓰기 초크포인트 — update_bid·set_user_lock 실행자 신설 |
| `backend/app/services/naver_ad/account_diagnosis.py` | pause_candidates·resume_candidates 신설 |
| `backend/app/services/naver_ad/naver_sa_writer.py` | 입찰·정지재개 쓰기 함수 신설 |
| `backend/alembic/versions/c3d4e5f6g7h8_*.py` | target_bid·target_lock 마이그레이션(⚠️prod 미적용) |

## 6. 알려진 이슈 / 주의사항

- **⚠️prod 미배포** — main 미병합, origin 미push. 배포 전 D-NAO-33/X1a 때와 동일 절차(DB백업→sha256검증→마이그레이션→pm2재시작→프론트) 필요.
- **⚠️카나리 라이브 왕복 완전 미실시**(X1a 때부터 이어진 동일 선결 — Jino 캠페인 지정 대기). 계획서 §3 X1b 완료기준①②③ 전부 미확인 상태 — "X1b 됐다" 아직 금지(원칙22).
- **codex 사용한도**: T4 2라운드부터 소진, 2026-07-11 00:49 KST 재설정 예정. T4 마지막~T5는 Claude 리뷰만 받았음 — codex 재검증 권장.
- `.venv-test/bin/python`/`python3` 심볼릭 링크가 깨져 있어(CommandLineTools 시스템 파이썬으로 잘못 연결) sqlalchemy 등 venv 패키지 import 실패 — **`python3.11`을 직접 호출**해야 함(이번 세션 발견, failures.jsonl 기록 대상).
- proposal_scoreboard의 D+14 채점이 이제 실제로 도는지는 **prod 배포 후 실제 D+14가 지나야 라이브 확인 가능**(가장 빠른 확인 시점 = 다음 주).

## 7. 다음에 할 작업 (미완료)

- [ ] **codex 최종 재검증**(00:49 KST 이후) — T4~T5 diff 전체 재확인 권장
- [ ] **prod 배포**(main 병합→push→DB백업→마이그레이션 `c3d4e5f6g7h8`→pm2재시작→프론트, X1a 절차 재사용)
- [ ] **Jino 카나리 캠페인 지정** → optimizer='ours' 전환 → X1b 완료기준 라이브 왕복(가드레일 차단 실측·입찰변경 1건·정지재개 각 1건 실집행)
- [ ] X2(당일 플라이트 루프: response_curve_builder·pacing_controller·flight_loop) — X1b 완료 후 착수
- [ ] failures.jsonl에 이번 세션 발견 사항 기록: ①venv python3 심볼릭 링크 깨짐 ②changed_at UTC/KST 패턴(향후 서버 타임스탬프 컬럼 설계 시 항상 명시적 KST 값 전달 원칙화 검토)

## 8. 새 세션 시작 프롬프트

아래를 복사해서 새 대화 첫 메시지로 사용:

```
Ohiselling/.claude/worktrees/naver-ad-x1b-sprint-a42eb5/.claude/memory/HANDOFF_ohisell-naver-ad-x1b-complete+critical-bugs-found_20260710.md 읽고 이어서 작업해줘. codex 재검증 → prod 배포부터.
```
