# 세션 인수인계: D-NAO-58 CD4 완료 + D-NAO-59 최종 목적 확정 + "순위 고삐" 설계 방향
> 저장일시: 2026-07-19 09:33 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것 (그다음 트랙 `docs/tracks/active/track_naver-ad-optimization.md` D-NAO-1/59 필독)

## 1. 프로젝트 위치 및 환경
- 워크트리: `.claude/worktrees/d-nao-58-click-probe-continue-979ca3` / 브랜치 동명(#58 병합됨)
- prod: `sellc.ohitech.co.kr:/home/ubuntu/ohisell` — 백엔드 pm2 `ohisell-backend`(포트 8001), read-only 검증 = `cd backend && .venv/bin/python3 ...`
- 배포: **`scripts/safe_deploy.sh <파일...> [--restart]` 만**(CAS 가드, 직접 scp 금지 — [[naver-ad-safe-deploy-cas]])
- 테스트: `cd backend && python3 -m pytest -q` (binary는 `python3`)
- main == prod 정합(2026-07-19 08:32 PR #55~58 병합 완료)

## 2. 이번 세션 완료 목록
- ✅ **D-NAO-58 CD4(환경별 학습·세분화층) 전체 완료** — 구현(Sonnet TDD)·Opus 적대적 리뷰 R1 GATE PASS·safe_deploy 배포·**라이브 완전 합격**. 커밋 `da69acd`(코드)+`9d76724`(docs/HANDOFF)+`c343b41`(LESSONS #15).
  - 신규: `backend/app/services/naver_ad/probe_cell_aggregate.py`·`probe_cell_segmenter.py`·`probe_learning_loop.py` + 테스트 3 + scheduler 크론 `run_naver_probe_learning` 09:03. 마이그 0.
  - 리뷰 반영: P2-1(세분 근거를 observe **rationale**에 기록 — vault는 rationale만 렌더)·P3-1(N+1 재집계 제거)·P3-2(docstring)·P3-3("클릭 최다·이익가중 미반영·CD5" 표기).
  - **★09:03 크론 자연 발동 완전 라이브 합격 실측**: last_run 09:03:11 ok + observe 일기 1행("3셀 집계·weekday→1.0-2.0 승격") + **실 LLM 세분판정**("하위셀 1개뿐이라 세분 근거 없음, keep" — 정확). CD4 코드경로 아닌 실동작 검증됨.
- ✅ **PR #55(CD1)·#56(CD2)·#57(CD3)·#58(CD4) 순서대로 병합** → main tip `37ae63a`, main==prod.
- ✅ **MOP/P_Test/04 운영 실측**(prod read-only): 03=MOP(아이폰 강화유리)·04=우리(아이폰 지문방지)·P_Test=우리(아이패드 파워링크). **★핵심 발견**: 우리 프로그램 자동 실행 스코프=입찰·정지·재개뿐, **키워드 변경 안 함**·P_Test는 auto_operate 07-18 켜졌으나 실변경 0건(hot-set clk≥10 미달). 04는 활발히 운영(07-17~19 입찰 조정 + 07-19 08:50 무전환 스톱로스로 `01.아이폰16e` 광고그룹 자동 정지). **재개는 자동 아님**(auto_operator 일 레인 `_DAILY_LANE_PROPOSAL_TYPES=(bid_up,bid_down,pause)` — resume 없음, 제안만·콘솔 승인).
- ✅ **★시간당 전환 데이터 라이브 실측(중대)**: `/stats?breakdown=hh24`에 `ccnt` 넣으면 **시간당 전환 건수 옴**(04 adgroup 07-18 14시 ccnt=1). **`convAmt`(금액)는 시간당 안 옴, 일별만** → 매출 추정=ccnt×판매가. 우리 `_STATS_HH24_FIELDS`가 지금 `imp/clk/cost/avgRnk`만 요청(ccnt 미포함).
- ✅ **D-NAO-59 최종 목적 확정 + 트랙/메모리 저장** — PR #60(docs, main 병합 대기).

## 3. 확정된 결정사항
- **★D-NAO-59 (Jino 2026-07-19) = 우리판 MOP의 최종 목적**: **총 이익(절대액) 최대화**(ROAS 최대화 아님). 평균 ROAS 떨어져도 **한계(marginal) ROAS ≥ BEP 구간에선 순위 올려 볼륨 확장** → 매출↑·총이익↑ 동시. **한계 ROAS = BEP = 총 이익 꼭짓점 = 운영 목표점.** 효율 최고 순위(고ROAS·저볼륨)에 앉아 이익 남겨두지 않음. 안전선=평균 ROAS ≥ BEP. D-NAO-1의 정밀화(수학적 동일 꼭짓점). 원문: "무조건 이익스팟 순위에 있어서 매출 증가가 없는것보다 Roas는 떨어지지만 매출이 늘어서 총 이익이 늘어나는 경우, 구간도 분명히 있거든. 우리의 최종목표는 이거야. 이게 우리가 만든 MOP프로그램의 최종 목적이고 목표야."
- **CD1~CD4 완료** — 클릭 탐침 루프 지식층까지 완성. CD5(실행경로 wiring) 미착수.
- **재설계 아님 = 확장** — 목적함수는 원래 D-NAO-1. 실행 엔진(harness·가드레일·시간당/일 레인·응답곡선·CD1~4·시간당 수집)은 재사용. 바꾸는 건 ①ccnt 수집 추가 ②시간당 레인에 누적ROAS 신호 투입 ③스톱로스→순위 고삐 교체.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_naver-ad-optimization.md` | ★목표(D-NAO-59)·확정결정 전체·CD1~CD4 기록 (먼저 읽기) |
| `docs/PLAN_naver-ad-click-discovery.md` | CD1~CD5 계획서(§CD5=실행경로 wiring) |
| `backend/app/services/naver_ad/auto_operator.py` | 시간당 밴드 레인(:20)+일 레인(08:50)+CD2 탐침. `_DAILY_LANE_PROPOSAL_TYPES`·`_probe_trigger` |
| `backend/app/services/naver_ad/probe_*.py`(cell_aggregate/segmenter/learning_loop/signal/revert) | CD1~CD4 SA/harness |
| `backend/app/services/naver_sa_ad_fetcher.py` | `_STATS_HH24_FIELDS`(여기 ccnt 추가 예정)·`fetch_entity_hh24` |
| `backend/app/services/naver_ad/guardrail_gate.py` | BEP천장·쿨다운(2h)·일일상한·스톱로스 |
| `backend/app/services/naver_ad/naver_execution_harness.py` | 입찰 집행 단일 초크포인트 |

## 5. 알려진 이슈 / 주의사항
- **시간당 전환은 건수(ccnt)만·금액(convAmt)은 일별** → 매출은 ccnt×판매가 추정. 단일상품 광고그룹은 거의 정확, 다상품은 오차.
- **시간당 전환 희소**(하루 0~1건 흔함) → 시간별 단발 판단 금지, **누적 tally**로만. 최근 시간대 전환지연 완결도 보정.
- **쿨다운 2h(D-NAO-55)가 "매시간 CPC 변경"의 실제 상한** — 진짜 시간당 원하면 1h로 줄일지 트레이드오프 결정 필요(학습 노이즈·순위 안정성).
- **원칙22**: "된다" 주장 전 라이브 실측. prod 검증은 앱 venv(`backend/.venv/bin/python3`).
- 스코프 밖 칩: `scheduler_health` 테스트 순서 오염(사전 존재, task_d66dbb52 — 사용자가 별도 세션 시작함).
- 모델 라우팅: 이 트랙 **Fable 금지·설계=Opus·구현=Sonnet·리뷰=Opus(≤5R)** ([[model-routing-fable-opus-sonnet]]).

## 6. 다음에 할 작업 (미완료)
- [ ] **통합 설계(Opus)**: "하루짜리 순위 고삐 + 시간당 총이익 제어 + CD5" 한 그림으로. Agent/Harness/SA 도표 → Jino 승인 → 계획서.
  - **하루짜리 순위 고삐**: 장중 loss(누적 비용↑ ∧ 누적 ccnt 저조/추정ROAS<BEP)면 순위 쭉 하향(하드 정지 pause 대체) · 자정 목표 밴드 리셋(매일 새 기회) · 성과 좋으면 상향 후 관성 유지(다음날 안 낮춤, BEP 자동 천장). 비대칭: 아래=하루 리셋·위=누적.
  - **①ccnt 수집 추가**(컬럼+마이그+`_STATS_HH24_FIELDS`+sweep 확장) **②시간당 레인에 누적 추정 ROAS 신호 투입**(지금 "순위·CPC만·ROAS 판단 없음"→ D-NAO-4 규칙 완화) **③스톱로스→순위 고삐 교체** **④CD5**(learned_probe_rank 소비+이익가중 승격).
  - 쿨다운 조정 여부 결정.
- [ ] CD2/CD3 탐침 자연 발동 관측(원칙22, 저빈도 정상).
- [ ] codex 소급 리뷰 07-23(CD1~CD4 커밋).
- [ ] PR #60 병합(D-NAO-59 docs).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

`.claude/worktrees/d-nao-58-click-probe-continue-979ca3/.claude/memory/HANDOFF_ohisell-D-NAO-59-total-profit-objective+rank-leash-next_20260719.md 읽고 이어서 작업해줘. 다음은 "하루짜리 순위 고삐 + 시간당 총이익 제어 + CD5" 통합 설계(Opus)부터.`
