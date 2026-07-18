# HANDOFF — D-NAO-58 클릭 탐침 루프 CD4(환경별 학습·세분화층) 구현·리뷰·배포·라이브 백필 검증

- 날짜: 2026-07-19 07:xx KST
- 워크트리: `d-nao-58-click-probe-continue-979ca3` / 브랜치: 동명(CD3 tip `32c8f4c` 위 ff-stack)
- HEAD: `da69acd`(CD4 전체 1커밋) · prod 배포 완료(safe_deploy CAS, commit da69acd) · **PR #58**(base main)

## 한 줄 요약
D-NAO-58 CD4 = "환경 셀 × 순위 밴드 → 클릭 곡선"을 **백필로 학습**하는 지식층. 구현(Sonnet TDD)·Opus 독립 적대적 리뷰 R1 GATE PASS·prod 배포·**prod 백필 라이브 검증(첫 셀 집계+세분화 판정 1회 실측)**까지 완료. **CD1~CD4 완료.** 단 실행경로 wiring(learned_probe_rank 소비)은 **CD5로 분리 이월**(탐침 자연발동 0건이라 라이브 검증 불가 — 원칙22). 실 LLM verdict + observe 일기 write는 **09:03 deterministic 크론** 대기(자연 발동 시 관측).

## 확정 설계 (D-58-11~14, Claude 자동진행 — Jino "너의 추천옵션으로 자동진행" 위임)
- **D-58-11 스코프 = 지식층만**. 실행경로 변경(탐침이 학습된 목표순위 소비 or 탐침 생략)은 **CD5 이월**. 이유: 탐침 자연발동 0건 → 실행경로 변경은 라이브 검증 불가 → 검증 못 하는 주장 회피(원칙22). CD4는 advisory read 함수 `learned_probe_rank`만 노출(CD2 소비 안 함).
- **D-58-12 마이그레이션 0(LESSONS #14)**. 학습 상태를 새 테이블에 저장 안 함 — 집계는 `NaverKeywordHourly`(per-hour·avg_rank·365일 보존)에서 매번 재계산(순수 파생). "3회 반복 승격"=창 내 ≥3 서로 다른 날 일관신호(카운터 영속 없음). 판정 근거·승격 결과=`ops_diary_entries` observe 행(append-only, 스키마 불변).
- **D-58-13 환경축 = day_class 시작**(휴일>주말>평일), 세분 후보축 = iphone_window·season. 월초/중/말 헬퍼 신설 안 함(기존 diary env 순수 헬퍼 `_KR_HOLIDAYS`/`_season_of`/`_iphone_offset_days`/`_iphone_window` 재사용). 어느 축을 쪼갤지는 세분화 판사(LLM)가 데이터 유의성으로 결정(사전 하드코딩 금지). 순위밴드=[1,2)/[2,2.5)/[2.5,3)/[3,4)/[4,∞).
- **D-58-14 구조 = 3 SA + 1 Harness + 1 크론(마이그 0)**. (아래 구현 참조)

## 구현 (구현=Sonnet TDD, 커밋 da69acd)
- **신규 `probe_cell_aggregate.py`**(순수 SA·쓰기0): `env_cell_of_date`·`rank_band_of`·`aggregate_cells`(env_cell×rank_band→imp/clk/CTR, `hierarchical_pooling.shrink` EB 축소 — **첫 프로덕션 소비자**)·`cell_leading_indicator`(cart/conv 원시카운트·**회계 불변**, CD4 미호출·CD5 대기).
- **신규 `probe_cell_segmenter.py`**: `judge_cell_segmentation`(표본 임계 imp≥100·days≥3 셀에 대해 iphone_window·season 세분 유의성 LLM 판정, `expert_llm._invoke_claude` 재사용·model=opus·fail-open keep, invoke 주입가능). 승격과 **병렬 advisory**(D-58-11).
- **신규 `probe_learning_loop.py`**: `learned_probe_rank`(CD5 조회 API — 단일셀 재집계)·`_is_promotable`(승격 조건 단일소스)·`run_probe_learning`(aggregate→segment→promote→observe 일기 스테이지 격리 harness, wisdom_loop 패턴). 유일 쓰기=observe 일기 1행(하루 1회 idempotent, `write_diary_entry` fail-open).
- **`scheduler_service.py`**: `run_naver_probe_learning` 크론 `3 9 * * *`(정산 08:55 뒤·vault 09:05 앞 → observe 요약이 당일 볼트 포함) 4곳 등록(job func·defaults·_CATCHUP_ORDER·두 job-map).
- 상수: `_MIN_CELL_IMP=100`·`_MIN_BAND_IMP=30`·`_MIN_CELL_DAYS=3`·`_CTR_SIGNAL_MIN=0.01`·`_WINDOW_DAYS=30`.
- 테스트: `test_probe_cell_aggregate.py`(15)·`test_probe_cell_segmenter.py`(7)·`test_probe_learning_loop.py`(11, P2-1·P3-3 회귀테스트 포함). 전체 **2149 passed 회귀0**.

## 독립 적대적 리뷰 (Opus R1 GATE PASS, Fable 미사용·1R 수렴)
P1 0건. 불변식 전부 통과(마이그0·회계불변·격리↔라이브 LLM 계약 `res.get("json")`이 `_invoke_claude` 실반환 `{text,json,raw,usage}`와 정합·승격 시맨틱·fail-open·멱등성·스케줄러 4곳). 지적 전건 반영(원칙19 대화형):
- **P2-1(동의·수정)**: 세분 판정 근거(LLM verdict/axis/rationale)가 diary 미기록 → **observe `rationale`에 기록**(★vault_export는 observe를 `e.rationale`만 렌더[vault_export.py:182], `after_value` 안 보임 → rationale에 넣어야 실제 볼트에 보임. after_value에도 기계판독용 JSON 병기). 승격밴드에 "이익가중 미반영·CD5" 캐비어트 포함.
- **P3-1(동의·수정)**: `_promote_cells`가 셀마다 `learned_probe_rank`→`aggregate_cells` 재실행(N+1 전량 재집계) → **계산된 aggregate 스냅샷 직독**(`_is_promotable` 단일소스, 보고 cells와 동일 스냅샷 일관성 보너스). `learned_probe_rank`는 CD5 단일셀 조회 API로 유지.
- **P3-2(부분동의)**: segmenter docstring "산술 결과 그대로 승격 안 함" 과장 → 로직 결합은 기각(승격을 flaky LLM에 의존시키면 결정론·견고성 훼손·계획 D-58-11=병렬 advisory), **docstring만 정정**.
- **P3-3(동의·라벨 정직화)**: `optimal_band`=순수 CTR argmax ≈ 항상 최상위 순위(이익 스팟밴드 2.5~4와 배치) → 로직은 CD5(이익가중)지만 볼트 표기에 "클릭 최다·이익가중 미반영(CD5)" 캐비어트 즉시 부착.

## 라이브 검증 (원칙22 — prod 백필 읽기전용·쓰기0)
- **safe_deploy CAS**: 4파일(3 신규 + scheduler CAS 통과) = **병행 clobber 없음**. pm2 재시작 healthy.
- **새 크론 등록 확인**: `run_naver_probe_learning | 3 9 * * * | is_enabled=True | last_run=None`(09:03 자연 발동 예정).
- **첫 셀 집계 실측**(prod `.venv/bin/python3`, 30일 백필 2026-06-20~07-19): 3셀 —
  - `weekday`: imp 514,944·clk 2,227·days 4·**optimal_band=1.0-2.0**(밴드별 ctr_shrunk 1.0-2.0=0.0104 … 4.0+=0.0024, 단조감소).
  - `weekend`: imp 99,162·days 2·optimal 3.0-4.0(미eligible).
  - `holiday`: imp 82,753·days 1·optimal 3.0-4.0(미eligible).
- **세분화 판정 1회 실측**(stub invoke — 실 LLM 비용/지연 회피, real 데이터로 eligibility+sub_cells 조립 검증): judged=1(weekday 유일 eligible)·skipped=2(weekend/holiday days<3 게이트 정확 작동).
- **선행지표**: weekday{immediate 644,carts 195}·weekend{368,154}·holiday{43,44}.
- **P3-3 실증**: weekday optimal=1.0-2.0(순수 CTR argmax=최상위 순위) — 이익 스팟밴드([[naver-ad-profit-spot-bands]] 2.5~4)와 배치 → 내가 단 "이익가중 미반영·CD5" 표기가 실데이터로 정당화됨.
- **★미충족(원칙22)**: 실 LLM verdict + observe 일기 write = 0건. CD4는 conditional trigger가 아니라 **deterministic 09:03 크론**이라 발동은 확실 — 다음 관측 = `ops_diary_entries WHERE event_type='observe' AND action='probe_learning'` 행 등장(09:03 이후) + 그 rationale에 실 LLM 세분 판정 근거 포함 여부. **그 전까지 "실 LLM 세분판정·observe 기입 작동한다" 아직 금지.**

## 다음 세션이 할 일
1. **09:03 크론 자연 발동 관측**(오늘 07-19 09:03 KST 이후): observe 일기 행 + 실 LLM verdict rationale 확인 → 완전 라이브 합격 기록. (이 세션이 09:03까지 살아있으면 직접 확인 후 HANDOFF 갱신 예정.)
2. **CD2/CD3 탐침 자연 발동 관측**(공통 미충족, 원칙22): clk0∧imp≥30∧rank≥2.5 유닛이 :20 크론에 걸리면 탐침 발동 → CD3 Stage1/2 되돌림/유지 → outcome_json["probe"]. 발동 0건은 정상(저빈도), 강제 금지.
3. **CD5 착수**(실행경로 wiring, 계획서 §CD5): `learned_probe_rank`를 CD2 `_probe_trigger`가 소비(목표순위 설정 or 탐침 생략) + 이익 가중 승격(cell_leading_indicator/roas 결합해 순수 CTR argmax의 최상위 쏠림 교정). 탐침 자연발동 선결.
4. codex 소급 리뷰 07-23(CD1~CD4 전 커밋).
5. **PR 병합 대기**: #55(CD1)·#56(CD2)·#57(CD3)·#58(CD4) 스택. #58 diff에 CD1~CD3 조상 함께 표시(main 병합 시 해소).

## 참조
- 계획서: `docs/PLAN_naver-ad-click-discovery.md`(§CD4 D-58-11~14·§CD5)
- 트랙: `docs/tracks/active/track_naver-ad-optimization.md` D-58-11~14 + CD4 완료 기록
- 직전 HANDOFF: `HANDOFF_ohisell-D-NAO-58-CD3-live_20260719.md`
- 교훈: [[naver-ad-safe-deploy-cas]](배포는 safe_deploy.sh만) · [[model-routing-fable-opus-sonnet]](이 트랙 Fable 금지·리뷰=Opus·구현=Sonnet) · [[naver-ad-profit-spot-bands]](이익 스팟밴드 2.5~4 — CD4 optimal_band와 배치, CD5 교정) · LESSONS #14(append-only 로그/데이터 파생 상태 = 마이그 0)
