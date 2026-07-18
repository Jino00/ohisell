# HANDOFF — D-NAO-58 클릭 탐침 루프 CD3(되돌림·성과 판정층) 구현·리뷰·배포·라이브 코드경로 검증

- 날짜: 2026-07-19 00:20 KST
- 워크트리: `recursing-engelbart-6bb9d5` / 브랜치: `claude/d-nao-58-click-probe-cd3`(CD2 tip `b55994d` 위 stack)
- HEAD: `9ba0ce7`(CD3 구현) → docs 커밋 별도. prod 배포 완료(safe_deploy, commit 9ba0ce7) · **PR: 생성 예정**

## 한 줄 요약
D-NAO-58 CD3 = CD2 탐침(밴드 사각지대 능동 상향)을 **유지/되돌림 판정**하는 층. 2단계 되돌림(①실시간 출혈 밸브 ②D+1 정산 판정) + 선행지표 signal_sa. 구현·Opus 독립 적대적 리뷰 R1 GATE PASS·prod 배포·라이브 코드경로 검증까지 완료. **CD1~CD3 완료. 단 실제 탐침 왕복(발동→되돌림/유지)은 CD2 탐침이 아직 자연 발동 안 해서 0건 — 원칙22상 "탐침 왕복 작동" 아직 금지, 모니터링 중.**

## 확정 설계 (D-58-8~10, Claude 자동진행 — Jino "너의 추천옵션으로 자동진행" 위임)
- **되돌림 값의 출처 = 그 probe의 성공 change_log `before_value["bidAmt"]`**(harness가 실쓰기 성공 시 기록).
- **상태 추적 = change_log에서 파생(마이그레이션 불필요)**. "standing probe" = `NaverProposal(approval_source='probe_op', executed_change_log_id NOT NULL)` 중 그 유닛의 **가장 최근 성공 update_bid change_log**가 바로 그 probe인 것. 이후 다른 변경(밴드 레인 down/되돌림)이 있으면 = 이미 해소 → 제외. 최근 7일 창만 probe로 취급.
- **D-58-8 Stage 1 실시간 출혈 밸브**(run_hourly_lane 말미, lazy import — 매시 :20): 당일 standing probe에 hh24 곡선으로 (a)완료시간대 누적비용/now.hour > 정착창 시간당평균(총비용/(7×24))×`_PROBE_BLEED_COST_MULTIPLE(=3)` AND (b)그날 conv_direct_cnt=0(행 부재도 0=충족, 보수적). 둘 다 → before_value로 즉시 되돌림. intraday conv 측정 한계로 사실상 "시간당 3× 비용급등 시 보수적 회수"(되돌림은 완전 가역, 쿨다운 2h 후 재탐침 가능, 최종 판정은 Stage 2).
- **D-58-9 Stage 2 D+1 정산 판정**(신규 크론 08:55, 일 레인·해석 뒤): age≥1 standing probe에 signal_sa. score=즉시구매(conv_direct_cnt+conv_indirect_cnt) + 장바구니(cart_*_cnt)×상품전환율(cart_conversion_rate 상품→캠페인→global 폴백). 판정: ①clk=0→되돌림("상향해도 클릭 안 살아남=순위 병목 아님") ②clk>0∧roas_corrected≥target_roas→**유지**(CD4 지혜 후보) ③clk>0∧roas<target∧adjusted_score<1.0→되돌림("클릭 살았으나 전환 부족") ④clk>0∧roas<target∧adjusted_score≥1.0→defer(장바구니 지연, D+2 재판정) ⑤근거부족→defer, age≥3이면 안전 default 되돌림. 결과를 probe execute diary outcome_json["probe"]에 기입(P2 diary_outcome 관례).
- **D-58-10 되돌림 집행 = 초크포인트 경유(우회 금지)**: bid_down 제안(target_bid=before_value, `APPROVAL_SOURCE_REVERT="revert_op"`) → execute()(guardrail·킬스위치·change_log 전량 통과). diary ACTOR_PROBE 재사용(같은 주체)·매핑 추가. harness 킬스위치 **양 튜플**(`_claim_executing`·`execute()` 진입)에 revert_op 추가(probe_op와 동일 2중 harness 방어 parity + probe_revert pre-check = 3중). 킬스위치 OFF 시 되돌림도 hold(Jino 수동통제, 일관성).

## 구현 (구현=Opus 서브에이전트, TDD)
- **신규 `backend/app/services/naver_ad/probe_signal.py`**: `probe_signal_score(...)` 순수 계산 SA. immediate/carts/adjusted_score/cost/clk/conv_amt/roas_corrected 반환. 쓰기 0.
- **신규 `backend/app/services/naver_ad/probe_revert.py`**: `_standing_probes`·`run_bleed_valve`(Stage1)·`run_settlement`(Stage2)·`_execute_revert`·`_write_probe_outcome`·`_resolve_cart_rate`·`_conv_direct_today`.
- **`auto_operator.py`**: `APPROVAL_SOURCE_REVERT="revert_op"` + `run_hourly_lane` 말미 Stage1 훅(lazy import·fail-soft, `result["bleed"]`).
- **`diary.py`**: `_APPROVAL_SOURCE_TO_ACTOR["revert_op"]=ACTOR_PROBE`.
- **`naver_execution_harness.py`**: 킬스위치 양 튜플에 `APPROVAL_SOURCE_REVERT`(2줄).
- **`scheduler_service.py`**: `run_naver_probe_settlement_job` + 크론 "55 8 * * *" + catch-up 순서(일 레인 뒤) + job 맵 2곳.
- 테스트: `test_probe_signal.py`(7) + `test_naver_probe_revert.py`(21 — 실제 guardrail 2개 포함).

## 독립 적대적 리뷰 (Opus R1 GATE PASS, Fable 미사용·5R 이내)
- **P1 0건·P2 0건**. 리뷰어가 실제 guardrail 적대적 테스트를 직접 작성·실행해 money-path 실증:
  1. **되돌림은 입찰을 절대 못 올린다**: 외부가 라이브가를 before_value 아래로 낮췄으면 bid_down이 인상 방향이 되어 `guardrail_gate._check_bid` 방향 불일치 차단 → execute 예외 → `_execute_revert` False → guard_failure(outcome=failed·after_value=None) → probe standing 유지·재시도.
  2. **쿨다운 시 안전 defer**: 같은 날 탐침 직후(2h 내) 출혈 밸브 되돌림은 실 guardrail 쿨다운이 차단 → 쓰기 없음 → probe standing 유지, 다음 틱/Stage 2가 처리.
  3 킬스위치 3중(pre-check+harness 2튜플)·4 Stage1(당일)/Stage2(age≥1) disjoint=이중되돌림 없음(성공 되돌림이 새 change_log→probe 자동 superseded)·5 회계 불변(signal_sa 쓰기0·cart는 판정지표에만·roas는 conv_amt만·cf는 보수적).
- **권고 반영**: 리뷰어가 "revert-exec 테스트가 guardrail을 stub해 money-safety를 상시 검증 못 함" 지적 → **실제 guardrail 영구 테스트 2개 추가**(`test_real_guardrail_blocks_revert_that_would_raise_bid`·`test_real_guardrail_cooldown_blocks_same_day_bleed_revert`). 전체 2114→**2116 passed 회귀0**.
- **P3 3건(무해, 이월)**: ①중첩탐침 부분 되돌림(같은 유닛 2회 탐침 시 되돌림이 이전 탐침 레벨 복원 = 밴드 상단 1스텝 잔존, 밴드 레인 down/소진서킷이 보정, 드뭄) ②target_roas 미해석 시 이익 탐침도 보수적 되돌림(안전방향, 계정 기본값 있으면 비발생) ③외부 인상(external_bid_change)은 supersede 안 함 → 매일 되돌림 재시도하나 guardrail 방향/변경폭 차단(무해, 오히려 외부 상향을 우리 구가로 낮추지 않음).

## 라이브 검증 (원칙22 — prod 실측)
- **safe_deploy CAS**: 6파일 전부 통과(2 신규 + 4 CAS "prod=내 역사 속 구버전") = **병행 세션 clobber 없음**(이번엔 CAS 충돌 0). pm2 재시작 healthy(Application startup complete·Uvicorn 8001·크래시루프 없음·probe 에러 0·scheduler/health 200).
- **새 크론 등록 확인**: `run_naver_probe_settlement` next_run 2026-07-19T08:55:00+09:00 enabled=True(scheduler status API + 스케줄 튜플·job맵 소스 확인).
- **read-only 실검증(쓰기 0)**: prod `.venv/bin/python3`로 `APPROVAL_SOURCE_REVERT=revert_op`·`actor_from_approval_source("revert_op")=probe` 로드 확인 + `_standing_probes(db, now)` prod 실행 **에러 없이 0건 반환**(자연 발동 전 정상) + `run_settlement`/`run_bleed_valve` end-to-end 실행 **쓰기0·에러0**(standing 0 → 되돌림 없음).
- **★미충족(원칙22)**: 실제 탐침 왕복(발동→되돌림 or 유지) = 0건. CD2 탐침 자체가 자연 발동 조건(clk0∧imp≥30∧rank≥2.5 유닛이 :20 크론에) 미충족으로 아직 0건. **"탐침 왕복 작동한다" 아직 금지. 자연 발동 대기** — `ops_diary_entries.outcome_json`에 `probe` 키(result: kept/reverted/deferred) 등장 or `naver_proposals WHERE approval_source='revert_op'` 행 관측 시 합격.

## 다음 세션이 할 일
1. **PR 생성**(CD3). ⚠️ CD3 브랜치는 CD1(#55)·CD2(#56) 미병합 PR + 병행 세션 409bd8의 D-NAO-54 병합을 조상으로 포함 → PR diff에 함께 보임(CD2 HANDOFF와 동일 상태). CD1/CD2가 main 병합되면 merge-base가 중복 해소.
2. **자연 발동 관측**(CD2·CD3 공통 완료 조건, 원칙22): prod에서 탐침이 실제 발동(diary actor=probe execute 행) → Stage1/Stage2가 그 outcome_json["probe"]에 result 기입하거나 revert_op 되돌림 집행하는지. 발동 안 나면 정상(저빈도) — 강제 금지.
3. **CD4 착수**(환경별 학습·세분화층): 계층적 풀링(hierarchical_pooling 재사용, 환경셀×순위→클릭/선행지표 집계) + 세분화 판사(표본 임계→LLM 유의성, P3 판사 재사용) + 지혜 승격("환경 E→상품 P 최적 탐침 순위 N" 3회 반복 → 다음 같은 환경 진입 시 탐침 생략하고 그 순위 목표). 계획서 `docs/PLAN_naver-ad-click-discovery.md` §CD4.
4. codex 소급 리뷰 07-23(CD1·CD2·CD3 전 커밋).

## 참조
- 계획서: `docs/PLAN_naver-ad-click-discovery.md`(§0·CD1~CD4·§CD3 D-58-8~10)
- 트랙: `docs/tracks/active/track_naver-ad-optimization.md` D-58-1~10 + CD1/CD2/CD3 완료 기록
- 직전 HANDOFF: `HANDOFF_ohisell-D-NAO-58-CD2-live_20260718.md`(워크트리 `d-nao-58-click-probe-cd2-33da23`)
- 교훈: [[naver-ad-safe-deploy-cas]](배포는 safe_deploy.sh만) · [[model-routing-fable-opus-sonnet]](이 트랙 Fable 금지·구현/리뷰=Opus) · [[sqlite-server-default-now-is-utc]](change_log.changed_at=KST(harness now 주입)·created_at=UTC 구분)
