# 세션 인수인계: 예산 통제 계획서 작성 완료(D-NAO-42-f) + A/B D1 상태
> 저장일시: 2026-07-13 06:30 (KST)
> 직전 HANDOFF: `HANDOFF_ohisell-mop-vs-ours-04-canary+budget-scope_20260712.md`(04 카나리 개방·A/B·예산 개방 결정).
> 트랙: `docs/tracks/active/track_naver-ad-optimization.md` **D-NAO-42-f**.
> ★핵심 2가지: ①**예산 통제 계획서 작성 완료 — Jino 승인 대기(코딩 미착수)** ②**A/B 03vs04 D1은 오늘 저녁에 실관찰**(지금은 데이터 없음).

## 0. 다음 세션 진행 방식 (Jino 지시 — 준수)
- 모델 라우팅: 복잡(계획/설계)=**Opus**, 단순(구현)=**Sonnet**. 예산 구현은 Sonnet.
- 끝까지 자동 + 신선도 유지. 이 트랙은 main 기준 워크트리. `git branch --show-current` 확인.
- ⚠️ **워크트리 주의**: 이번 세션 shell은 `naver-ad-execution-canary-9c4b69`였으나 **파일 기록 전부 `naver-ad-execution-loop-6cc75b`**(D-NAO-42 living-memory·comparison log·baseline이 거기 untracked). 다음 세션도 6cc75b에서 작업 권장. **미해결 부채**: 이 트랙 living-memory(트랙파일 D-NAO-42-a~f·PLAN·mop_ui/*)가 여러 워크트리에 untracked만 존재 → 언젠가 커밋·정리 필요(원칙20/21, 트랙파일 line 130 기록).

## 1. 이번 세션 완료
- ✅ **Jino 확정**: 예산 정책 ②"회당 총 증가액 ≤10만"=**라운드 합계**(전 캠페인 증액분 합, 캠페인당 아님). AskUserQuestion "라운드 합계 (권장)". 트랙 D-NAO-42-f 갱신(해석 확인대기→확정).
- ✅ **예산 통제 계획서 작성**: `docs/PLAN_naver-ad-budget-control.md`(Opus). 실제 코드 grounding(파일:라인) 기반.
- ✅ **A/B D1 상태 확인**(원칙22): 04 카나리 라이브 재확인 + D1 데이터 부재 정직 기록(comparison log D1 append).
- ✅ 트랙 D-NAO-42-f "다음"·progress·comparison log·HANDOFF 갱신.

## 2. 예산 계획서 핵심 (승인 검토용)
- **철학**: 우리 MOP = MOP Pro+ 무제한(요금제 인위제한 제거) **단 안전 가드레일 유지**(BEP 이익하한·스톱로스·클램프 = 차별점). 목적함수 D-NAO-1 불변. marginal ROAS 인과추정 없음.
- **★핵심 설계 판단**: `execute()`가 제안 1건씩 실행(라운드 루프 없음) → **"라운드 합계 ≤10만"은 생성 단계 봉투**(1 라운드=1 proposal 생성 런), **per-campaign 캡(+100%·BEP·스톱로스·클램프)은 실행 직전 `guardrail_gate._check_budget`**. 두 층 분리.
- **신설물**: ⓐ`NaverProposal.target_budget` 컬럼(+ 선택 `budget_auto_eligible`) 마이그레이션 ⓑ`naver_sa_writer.update_campaign_budget`(PUT `?fields=budget`, before/after 재조회) ⓒ`guardrail_gate._check_budget` ⓓ`_build_guardrail_context` **campaign 브랜치**(현재 keyword 전용) + `campaign_window_agg` ⓔ`budget_sizer`(목표예산=min(current×2, max(current+증분, pred_cost)), 클램프) ⓕ`OPEN_ACTIONS += update_budget` + `_execute_update_budget` + `budget_down` 배선.
- **⚠️ swagger 미확정=P0 하드블로커**: ref 27이 캠페인 budget PUT을 의도적으로 미상세(스코프밖이었음). 정확한 body(`useDailyBudget` 동반?)·`dailyBudget` 최소값·증분 단위 = **P1 착수 전 swagger 실확인**(추정금지). ref 27:86,89,101 참조.
- **Phase**: P0 swagger확인 → P1 스키마+writer → P2 가드레일+campaign컨텍스트 → P3 사이징+라운드봉투+실행배선 → P4 04 카나리 라이브 왕복(가드레일 실차단 실측). 각 Phase TDD+codex pass.

## 3. Jino 결정 대기 (승인 시 즉답 필요)
1. **계획 승인 여부**(승인해야 P0 착수). 방향 임의변경 금지 — D-NAO-42-f 준수.
2. **§6 열린질문 2건**:
   - (2) `budget_auto_eligible` 컬럼을 **지금** 스키마에 넣을까(권장, 위임 켤 때 라운드봉투 재현 안전) / 위임 착수 시 별도 추가할까.
   - (3) 사이징 **fallback 스텝**: 예측(pred_cost) 없는 캠페인의 기본 증액 스텝(예 +50%?) — 임의 상수라 확정 필요.
3. 위임/자율 발사는 이 계획 밖(반자동 완전작동까지만). 자율 승급=Ava 수리 후(D-NAO-42-e).

## 4. A/B 03 vs 04 관찰 — 다음(오늘 저녁)
- **지금(06:20) 관찰 불가**: 04 제안은 08:00 크론 후 생성, MOP 6245 오늘 집행 시작, 03 종일치는 07-14 07:30 수집. **의미있는 D1=오늘 저녁**(04 제안·집행 발생 + 03 하루 진행 후).
- 실행 방법: 저녁에 이 세션 유지 시 실행하거나, 새 세션 "03 vs 04 D1 관찰 업데이트". 크론 6b2c0462(20:20)는 죽은 세션 종속=발동 불확실.
- 관찰 항목: 04 = naver_proposals(04 budget/bid 제안)·change_log(집행)·naver_ad_daily(04 delta). 03 = SA 자동수집 delta(unit6245_baseline_snapshot.py 재실행) + (Jino 로그인 시) MOP 콘솔 입찰횟수·플라이트·예측. lift% + 메커니즘 대조(단일지표 승패 금지).
- baseline: 03=32,411원/7일(24그룹)·04=7,214원/7일(11그룹). 03≈4.5×04 → lift%로.

## 5. 환경 (직전 HANDOFF에서 불변)
- prod SA API: `ssh sellc.ohitech.co.kr 'cd /home/ubuntu/ohisell; set -a; . backend/.env; set +a; PYTHONPATH=backend backend/.venv/bin/python3 -' < script.py`.
- prod HTTP: 포트 **8001**(`curl localhost:8001/api/naver/ad/...`). pm2 id 0. DB=`backend/ohisell.db`(SQLite, APScheduler `scheduler_state`, Asia/Seoul).
- prod DB 컬럼: proposals=`naver_proposals`, ad_daily 날짜=`ad_date`.
- MOP: 콘솔 mop.co.kr·API be.mopapp.net·advertiserId **756**·`x-session-id`. **로그인은 Jino만**.
- 04 카나리: `cmp-a001-02-000000008514959`, SHOPPING, 하루예산 30,000, optimizer=ours(mode NULL=BEP-ROAS).

## 6. 핵심 파일
| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-ad-budget-control.md` | ★이번 계획서(승인 대상) |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙 마스터, D-NAO-42-f |
| `docs/references/data/mop_ui/mop_vs_ours_03_04_comparison.md` | A/B 비교 로그(D1 append) |
| `backend/app/services/naver_ad/guardrail_gate.py` | 가드레일(:126-132 BEP하한, _check_budget 신설 대상) |
| `backend/app/services/naver_ad/naver_execution_harness.py` | 집행(OPEN_ACTIONS:100, _build_guardrail_context:180 campaign 브랜치 대상) |
| `backend/app/services/naver_ad/naver_sa_writer.py` | 쓰기(update_campaign_budget 신설 대상) |
| `backend/app/services/naver_ad/budget_allocator.py` | 예산 신호(find_budget_expansion_signals) |
| `backend/app/services/naver_ad/proposal_writer.py:236` | _budget_proposal(target_budget 사이징 대상) |

## 7. 새 세션 시작 프롬프트
```
.claude/worktrees/naver-ad-execution-loop-6cc75b/.claude/memory/HANDOFF_ohisell-budget-plan-drafted+ab-d1_20260713.md 읽고 이어서. 복잡=Opus·단순 구현=Sonnet. 우선순위: ①(Jino 승인 시) 예산 계획서 docs/PLAN_naver-ad-budget-control.md P0(swagger 확인)→P1~P4 구현 ②저녁이면 03(MOP)vs04(우리) D1 관찰 업데이트. 끝까지 자동+트랙·progress·HANDOFF 신선도 유지.
```
