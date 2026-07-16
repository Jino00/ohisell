# PLAN — 상설 소급 채점(retro scoring) 일일 성적표 (D-NAO-45, 2026-07-16)

> 승인: Jino "그래, 1번을 진행하자" (2026-07-16 밤, ref 31 §4-1 채택).
> 설계=Fable(이 문서), 구현=Sonnet, 게이트=codex(원칙19), 배포=Jino.
> 브랜치 `claude/spot-backtest-cadence-pacing-2e3bfa`(main 기준, D-NAO-44 커밋 위).

## 0. 왜 (근거 = ref 31 일회성 실측)
- 실행이 거의 0인 관찰 모드에서 기존 D+7/14 채점(proposal_scoreboard)은 굶는다(실행된 제안만 채점).
  소급 채점은 **실행 안 된 신호도** 매일 채점 → 관찰 기간 전체가 학습 기간이 된다.
- 효과: ①위임 개방의 숫자 근거(유형별 정밀도 추세) ②규칙 품질 회귀 감지 ③임계값 실측 튜닝
  ④데이터 지평 복리(매일 +1 as-of일). 일회성 실측치: down/pause 61~88%·up 29~60%·저속경보 98.7% 진짜.
- **정직 경계**: 방향 정확도 계기판이지 인과 성과 아님(그건 카나리). 알려진 한계 2건 문서화 —
  ⓐ entity 상태(입찰가·on/off)는 이력이 없어 스냅샷 시점 현재값 사용 ⓑ product BEP/target도 현재값.

## 1. 사전 감사 결과 (R0 — 완료, 2026-07-16 실측)
- `diagnosis.build_diagnosis(db, date_from, date_to)` 서브트리는 **전부 명시 날짜 관통**(correction_factor,
  각 보드, keyword_triage(as_of=), vicious_cycle, resume류 포함) — **run_daily 수술 불필요.**
- `kst_today()` 하드콜은 전부 제안 생성·만료 경로(proposal_pipeline 예산 신호 helpers=override 파라미터
  있음, run_daily:498, proposal_writer:647 만료 패스)라 이번 스코프 밖.
- 시간 기록은 반드시 `kst_now()` 명시([[sqlite-server-default-now-is-utc]] — server_default는 UTC).

## 2. 스코프
- **IN**: 신규 테이블 2 + 마이그레이션 1 + SA 3 + Harness 1 + 크론 1(08:30 KST) + 백필 + 조회 API 1 + 테스트.
- **OUT**: 콘솔 UI(후속), overpace 트리거 튜닝(ref 31 §4 별건), 최소 윈도우 가드(별건), 캠페인 6개월
  장기 리플레이(별건), trigger_watch가 구조화 필드를 직접 쓰도록 바꾸는 것(후속 — v1은 rationale 파싱).

## 3. 스키마 (마이그레이션 `f6g7h8i9j0k1`, down_revision=`e5f6g7h8i9j0`)
### naver_retro_signal — as-of×보드×타깃 1행
- id PK / created_at(kst_now 명시) / **asof_date Date idx** / board String(32) / direction String(8) down·up·pause
- grain String(8) keyword·adgroup / target_id String(50) / campaign_id String(50) idx
- 스냅샷 시점 고정 렌즈(채점 재현성): cf_asof Float / bep_asof Float / target_asof Float
- cost_asof Int / roas_c_asof Float nullable
- D+3 채점: verdict_d3 String(10) nullable(correct/gray/wrong/no_spend) / scored_d3_at DateTime nullable /
  cost_post3 Int / conv_post3 Int / roas_c_post3 Float nullable / bleed_post3 Int
- D+7 채점: verdict_d7 / scored_d7_at / cost_post7 / conv_post7 / roas_c_post7 / bleed_post7
- UniqueConstraint(asof_date, board, target_id)
### naver_retro_pacing_score — trigger_pacing 경보 1건당 1행
- id PK / proposal_id Int UNIQUE(naver_proposals.id) / alert_date Date idx / campaign_id / kind String(8) 저속·과속
- alert_hour Int / spent Int / budget Int / multiple Float (rationale 파싱값)
- final_cost Int / final_ratio Float / verdict String(24) (버킷: ref 31 §2와 동일) / scored_at DateTime

## 4. SA 3개 (원칙18 — 단일 책임, 서로 모름)
### C1 `retro_snapshotter.py` — snapshot_signals(db, asof: date) -> dict
- diag = build_diagnosis(db, asof-14일, asof). boards None(진단 에러)이면 {"snapshotted":0,"skipped":"...","error":...}.
- 대상 보드 6종(ref 31과 동일): bleeding_keywords·starving_winners(keyword) /
  shopping_group_bep·shopping_group_growth·shopping_pause_candidates(adgroup) / pause_candidates(keyword).
  방향: bep류=down, growth·starving=up, pause류=pause. resume류는 제외(정지 중=사후 관측 불가, 정직 경계).
- 행 생성 시 cf/bep/target as-of 값 고정 저장. **idempotent**: (asof,board,target) 기존재 시 skip(재실행 안전).
- 읽기+본인 테이블 쓰기만. 외부 API 호출 없음.
### C2 `retro_scorer.py` — score_due(db, today: date) -> dict
- d3: `asof_date <= today-4 AND verdict_d3 IS NULL` 전부(밀린 것 포함 catch-up) → 사후창 asof+1..asof+3.
- d7: `asof_date <= today-8 AND verdict_d7 IS NULL` → 사후창 asof+1..asof+7.
- 사후 집계: naver_ad_daily 상세행(≠BACKFILL_SENTINEL_ADGROUP)을 grain 컬럼으로 SUM(cost·conv_direct+indirect).
- 판정(ref 31 §1-a 규약 고정, 행에 저장된 as-of 렌즈 사용):
  - cost_post==0 → no_spend
  - down: roas_c<bep_asof→correct / <target_asof→gray / else wrong
  - pause: conv_post==0→correct / roas_c<bep_asof→gray / else wrong
  - up: roas_c≥target_asof→correct / ≥bep_asof→gray / else wrong
  - bleed = round(cost_post − conv_post×cf_asof/bep_asof) (bep_asof=계정 순수 손익분기)
- 같은 행 이중 채점 금지(verdict NULL 조건이 가드). 반환 {"scored_d3":n,"scored_d7":n}.
### C3 `retro_pacing_scorer.py` — score_alerts(db, upto: date) -> dict
- 미채점 trigger_pacing 제안(naver_retro_pacing_score에 proposal_id 없음) 중 경보일 ≤ upto.
- rationale 정규식 파싱(ref 31 스크립트 패턴 고정): `(저속|과속)...(YYYY-MM-DD) (H)시 기준 소진 (S)원/(B)원...배수=(M)`.
  파싱 실패 → verdict='unparsed'로 기록(재시도 무한루프 방지). 진짜 채점 불가(sentinel 최종치 없음)는
  **행을 만들지 않고 skip**(다음 날 재시도 — 최종치가 늦게 올 수 있음).
- sentinel 최종치: naver_ad_daily WHERE adgroup_id==BACKFILL_SENTINEL_ADGROUP AND campaign_id, ad_date.
- 버킷(ref 31 §2 고정): 저속 = 최종/예산 ≥0.9 false_alarm / 0.5~0.9 partial / <0.5 correct.
  과속 = ≥1.1 correct / 0.9~1.1 partial / <0.9 false_alarm.

## 5. Harness + 크론 + API
### C4 `retro_scoring_loop.py` — run_daily_retro(db, today=None) -> dict
- today = today or kst_today() (as-of 관통: 파라미터 명시 가능 — D-NAO-44 codex R1 버그 클래스 회피).
- 순서: ①snapshot_signals(today-1) ②score_due(today) ③score_alerts(today-1). 각 단계 독립 try/except
  (run_daily의 stage_status 패턴 준용) → {"stage_status":{...}, 카운트}.
- `backfill(db, date_from, date_to, today=None)`: as-of 순회 스냅샷+채점(idempotent라 안전).
  배포 후 1회 07-08~어제 백필 실행(Jino 배포 단계에서).
### 크론: scheduler_service defaults에 `naver_retro_scoring` 08:30 KST 추가
- 아침배치 뒤(07:30 수집→07:50 forecast→08:00 proposals→08:05/08:10) 순서 보장. catch-up 목록 포함 여부는
  기존 `_catch_up_morning_batch` 규약 따름(비정형 아님 → 포함 가능하면 포함).
### API: GET `/api/naver/ad/retro-scorecard?days=28`
- 보드별 rollup(d3/d7 각각): n, correct, gray, wrong, no_spend, precision_spenders(=correct/(correct+gray+wrong)),
  bleed_sum(원, down·pause correct분). pacing rollup: kind×verdict 카운트. 단순 read-only 집계.

## 6. 테스트 (TDD, superpowers)
- C1: 행 생성·렌즈 고정값 저장 / idempotent 재실행 / boards None graceful / **as-of 누출 회귀**(asof 이후
  날짜 데이터를 넣고 스냅샷 결과에 영향 없어야 — D-NAO-44 codex R1 클래스).
- C2: 판정 매트릭스(각 direction×correct/gray/wrong/no_spend) / d3·d7 창 경계 / 이중 채점 방지 /
  밀린 as-of catch-up / bleed 계산.
- C3: 정규식 파싱(실 rationale 형식 fixture) / 버킷 경계 / unparsed 기록 / 최종치 없음 skip 후 재시도.
- C4: 단계 오케스트레이션·독립 격리 / backfill idempotent.
- 전체 naver 스위트 회귀 0 (로컬 homebrew python3, bcrypt 라우터 collection 에러는 기존 이슈).

## 7. 완료 기준
1. 테스트 전부 green + as-of 누출 회귀 테스트 존재.
2. codex GATE PASS(원칙19 대화형, 최대 3라운드).
3. (Jino 게이트) prod 배포: 마이그레이션 + 파일 copy + pm2 재시작 → 백필 1회(07-08~) →
   다음 날 08:30 크론 자연 발화로 신규 as-of 1일 추가 확인(원칙22 라이브 — 그 전까지 "됐다" 금지).
4. 문서 신선도: 이 문서 §8 + progress + 트랙(D-NAO-45).

## 8. 진행 기록 (구현자가 갱신)
- [x] C1 snapshotter + 마이그레이션 + 테스트 (2026-07-16, Sonnet) — `retro_snapshotter.py` +
  `f6g7h8i9j0k1_add_naver_retro_scoring_tables.py`(테이블 2개) + `test_naver_retro_snapshotter.py`
  5 tests green(행 생성·렌즈 고정값/6보드 방향매핑/idempotent/boards None graceful/as-of 누출 회귀).
- [x] C2 scorer + 테스트 (2026-07-16, Sonnet) — `retro_scorer.py` + `test_naver_retro_scorer.py`
  17 tests green(판정 매트릭스 9종·no_spend·d3/d7 창 경계·사후창 7일 경계·이중채점 방지·
  밀린 as-of catch-up·bleed 계산·sentinel 이중계산 회귀).
- [x] C3 pacing scorer + 테스트 (2026-07-16, Sonnet) — `retro_pacing_scorer.py` +
  `test_naver_retro_pacing_scorer.py` 14 tests green(정규식 파싱·버킷 경계 8종·unparsed
  기록+재시도 방지·최종치 없음 skip 후 재시도·upto 필터·idempotent).
- [x] C4 harness + 크론 + API + 백필 + 테스트 (2026-07-16, Sonnet) — `retro_scoring_loop.py`
  (`run_daily_retro`/`backfill`) + `test_naver_retro_scoring_loop.py` 8 tests green(단계
  오케스트레이션·partial-failure 격리 3종·backfill 순회+idempotent+단일일 격리). 크론
  `run_naver_retro_scoring`(30 8 * * *) scheduler_service defaults+start_scheduler
  매핑+_CATCHUP_ORDER 등록. API `GET /retro-scorecard?days=28` naver_ad.py에 추가(라우터
  테스트 `test_naver_retro_scorecard_router.py` 작성 — 로컬 bcrypt collection 이슈로 실행
  확인은 직접 함수 호출로 대체 검증).
- [ ] codex GATE PASS (라운드/지적/반영: ___)
- [ ] 배포 + 백필 + 라이브 확인 (Jino)
