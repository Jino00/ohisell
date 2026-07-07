# PLAN — P2-S3 시뮬·제안·발송 (네이버 SA 광고 최적화)

- 작성: 2026-07-07 (Opus 4.8). **개정: 2026-07-07 plan-eng-review 반영(codex 외부목소리 20건 + 자체 7건 + Jino 스코프 결정)**
- 트랙 정본: `docs/tracks/active/track_naver-ad-optimization.md` (D-NAO-1~21, D-S3-a~c)
- 상위 계획서: `docs/PLAN_naver-ad-optimization.md` (§P2-S3, §3.5 자율학습)
- 워크트리(불변): `admiring-solomon-b4f056` (원칙20)
- 구현 모델: Sonnet (Phase 단위), 계획은 Opus
- 이 문서 = 계획서 + 맥락노트 + 체크리스트 3-in-1 (원칙2 태스크 메모리)

---

## 0. 무엇을 / 왜

**무엇**: 매일 08:00 `진단 → 입찰 시뮬 → 제안서 작성 → Slack 발송` 체인을 자동 발화시켜 **읽기전용 제안**(광고 API 쓰기 없음, `naver_proposals` 저장만)을 생성. D-NAO-5 권한 1단계(**관찰 = 제안만 2~4주**) 개시.

**왜**: P2-S2까지는 진단(사실)뿐. S3는 진단을 **행동 제안**으로 번역하되 아직 실행 안 함 — 사람이 2주 제안 품질을 관찰한 뒤 P3 Confirm 실행으로. **관찰 모드의 목적 = 제안 품질 증명**이므로, 학습 루프가 휴면인 무거운 기계(budget_allocator·이상피드)는 S3c로 미뤄 검증 후 붙인다(D-S3-c).

---

## 1. 결정 전제 (2026-07-07 확정)

- **D-S3-a 스프린트 분할**: S3a(백엔드) → S3b(프론트) → **S3c(예산·이상, 연기)**. 단계별 라이브 검증. (Jino 승인)
- **D-S3-b campaign_target_resolver ② 보류**: 캠페인/그룹↔상품 연결 소스 없음 → 계정 기본 `target_roas`로 제안(관찰 모드 충분). 제안에 **target 근거 라벨**(account_default/override) 부착해 약한 근거를 사람이 식별. ②는 P3+. 이름 추정 매칭 금지. (Jino 승인)
- **D-S3-c 관찰 모드 = 얇은 S3 (codex 과빌드 지적 + Jino 승인 "부분 축소")**: S3a/b는 **bid_simulator(D-NAO-19) + proposal_writer + slack**만. **budget_allocator(marginal ROAS)와 경량 이상피드는 S3c로 연기** — 근거: ①marginal ROAS는 집계 데이터로 인과≠상관 분리 불가(사전학습 `intermittent-demand-short-history`) ②이상피드는 원래 "본격 P4" ③입찰+제외 제안만으로 제안 품질 증명 가능. bid_simulator는 D-NAO-19라 유지(핵심). "점진적>혁명적". budget_allocator는 폐기가 아니라 S3c에서 빌드. **이 결정은 D-NAO 스코프 변경 — Jino 승인 후 기록.** (Jino 승인 2026-07-07)
- **불변 상속**: 매출 극대화·이익 안전선(D-NAO-1) / optimizer='ours'만(D-NAO-13) / 학습은 파라미터만·권한은 사람 게이트(D-NAO-14) / CPC=min(경제성상한, estimate 목표순위)(D-NAO-19) / 신규·육성 100% 진입(D-NAO-20) / 진단 보정계수(D-NAO-21).

---

## 2. 맥락노트

### 2.1 이미 존재 (재사용, 신규 생성 금지)
- **DB 테이블 4개 (P0 스키마만, S3가 첫 쓰기)**:
  - `naver_proposals` — `proposal_type`(24)·`target_type`·`target_id`·`campaign_id`·`rationale`(Text)·`expected_effect`(Text)·`status`(pending/approved/rejected/expired)·`slack_ts`·`executed_change_log_id`. **⚠️ `predicted_json` 컬럼 없음**(codex #1) → S3는 예측을 `expected_effect`(사람 읽을 요약)에 저장, 구조화 predicted_json은 P3 `naver_change_log`(이 테이블엔 있음)에서.
  - `naver_change_log` — 쓰기는 P3(S3 미사용). `predicted_json`/`actual_json`/`outcome` 보유.
  - `naver_learning_state` — S3는 **optional 읽기만**(쓰기 주체=P3+ verify).
  - `naver_keyword_candidates` — S3 미사용(P4).
- **`diagnosis.build_diagnosis(db, date_from, date_to)`** (P2-S2) — 보드 7개 + 보정계수 + 계정 BEP/target. **S3 harness가 그대로 재사용**. 각 보드 행이 cost·clk·conv_amt·roas_naver·roas_corrected 보유 → bid_simulator 입력.
- **`campaign_target_resolver`** — `account_default_target_roas`/`resolve_target_roas`. ②는 보류(D-S3-b).
- **`naver_sa_ad_fetcher.py`** — `_headers(path, method)`·`get_campaigns_full()`(**dailyBudget 포함 — codex #3, 예산은 이미 있음**)·`get_adgroups`/`get_keywords`. **estimate 호출은 없음 → S3a 추가**. ⚠️ **자격증명 import-time 캡처**(codex #19, module top-level) → estimate 추가 시 함수 내에서 읽어 이 패턴 심화 금지.
- **`NaverAdDaily`** — `conv_direct_cnt`/`conv_indirect_cnt`(전환건수)·`conv_direct_amt`/`conv_indirect_amt`(매출)·`clk`·`cost`·`rank_sum`. **⚠️ 기기(M/P) 롤업 합산**(device 분리 없음 — codex #7) → estimate는 device 필요.
- **`NaverHourlySnapshot`** — `daily_budget` persist(codex #3). budget_allocator(S3c)가 소비.
- **`NaverCampaignSettings`** — `optimizer`(none/ours/mop 기본 none)·`mode`·`target_roas_override`·`updated_at`.
- **스케줄러 패턴** — `sync_naver_ad_daily`(07:30) 등과 동일 APScheduler 등록.

### 2.2 이식 자산 (쿠팡 `ohi-ad-learning-loop`)
- Bayesian 계층 수축 → `bid_simulator.pooled_rpc`. proposal-template.md → 근거 3소스. 네이버 차이: estimate 실행 전 시뮬·변경 페널티 없음 → ×0.5 폐기(D-NAO-20).

### 2.3 미확인 (착수 시 라이브 프로브 — 추정 금지, §5)
1. **estimate 엔드포인트 실제 스펙**(요청 body/응답, device 파라미터 형식) — S3a 최초 라이브 프로브.
2. **Slack incoming webhook 응답** — 대개 `ok`만 반환, message ts 없음(codex #15) → slack_ts는 best-effort.

---

## 3. 구현 설계 (레고 계층, 원칙18)

### 3.1 아키텍처 (S3a/b — budget_allocator·이상피드 제외, D-S3-c)
```
naver_proposal_harness   [신규 cron: generate_naver_proposals 08:00]
  0. freshness 게이트 ── naver_ad_daily 최신 ad_date·synced_at 확인(codex #12).
  │                      07:30 sync 실패/부분이면 → 생성 스킵 + 알림(원칙22, stale 제안 금지).
  │                      as_of = 마지막 완결 KST 날짜(어제, codex #13).
  └ diagnosis.build_diagnosis(as_of-lookback, as_of)          # 진단 재사용
     → bid_simulator.simulate(diagnosis_rows, target, estimate)  # 진단 행 소비(재조회 X)
     → proposal_writer.build(diagnosis, bid_sims) → persist       # optimizer='ours'만 → naver_proposals
     → slack_notifier.notify(proposals)                           # webhook or no-op
     + 계정 브리프 싱글톤(첫 제안서 S26 질문 — dedup 대상 아님, codex #17)
     + 일일 만료 패스(N일 초과 pending → expired, codex/내 #6)
Router:
  GET  /api/naver/ad/proposals              # 콘솔 제안 카드
  GET  /api/naver/ad/campaign-settings      # optimizer 패널 로드
  PUT  /api/naver/ad/campaign-settings      # optimizer/mode/override + 전환 로깅(codex #16)
```
**원칙18**: SA는 서로 모름, harness가 유통. 광고 API 쓰기 없음(읽기전용 + naver_proposals 저장).

### 3.2 SA 시그니처

**① fetcher estimate 확장** (`naver_sa_ad_fetcher.py`)
```python
def estimate_average_position_bid(device: str, items: list[dict]) -> list[dict]: ...  # 100개/콜
def estimate_performance(device: str, items: list[dict]) -> list[dict]: ...            # {clicks,cost,impressions} — 전환 아님(codex #8)
```
- 자격증명은 **함수 내에서** 읽음(import-time 캡처 심화 금지, codex #19).
- **device**: 저장 데이터가 롤업이라 per-device 가중 불가 → 지배 기기(mobile) 가정, 제안에 가정 라벨(codex #7). 진짜 per-device는 P0 device-split(연기).
- **호출량 캡**: 회당 대상 상한(출혈=비용상위 N·굶는승자=전량[소수]·육성후보=상한) + 100개 배치 + 429 백오프(P0 기실측) + **캡된 건수 로그**(무언 truncation 금지, codex #3-arch). 스펙 프로브 전 커밋 금지.

**② `bid_simulator.py`** (신규 SA)
```python
def pooled_rpc(keyword_row, group_agg, campaign_agg, account_agg) -> Decimal:
    """계층 베이지안 수축: 키워드→그룹→캠페인→계정 클릭당매출(RPC). 상위 aggregate는
    harness가 회당 1회 precompute해 전달(N+1 방지, 성능 #7). 모수 부족(D-NAO-9) 보완."""
def affordable_ceiling(rpc_corrected: Decimal, target_roas: Decimal) -> int:
    """경제성 상한 = 보정 클릭당매출 ÷ target_roas (D-NAO-19-①).
    ※ CVR×객단가 = (전환/클릭)×(매출/전환) = 매출/클릭 = RPC이므로 CVR·AOV 분해 불요
      (codex #6/#8 AOV 소스 문제 해소, 전환건수 의존 제거). conv_amt=0/clk=0 division guard."""
def simulate_bid(keyword_row, target_roas, *, estimate=None, learning_state=None,
                 is_new_or_growth=False) -> dict:
    """최종입찰 = min(경제성 상한, estimate 목표순위 필요입찰). D-NAO-20: 신규/육성 ×0.5 없음.
    반환: {recommended_bid, economic_ceiling, rank_bid, direction(up/down/hold),
           basis, expected_effect_text, capability_flags(estimate_ok 등, codex #11)}.
    expected_effect = estimate 예측클릭 × 우리 RPC(가정·범위 명시, false precision 금지, codex #8)."""
```
- **입력 = harness가 넘긴 진단 보드 행**(재조회 금지 — DRY + 보정계수 분기 회피).
- 변경 게이트(D-NAO-19-②, rationale에만 기록 — 실행 없음): 인상=ROAS≥target×여유+순위여력(avg_rank 2위↓) / 인하=7일추세+30일수준+BEP미달+모수 게이트 / 판정유보=3일무소진·일100원↓ / 쿨다운=change_log 없어 S3 no-op(훅만).

**③ `proposal_writer.py`** (신규 SA)
```python
def build(db, diagnosis, *, bid_sims=None, as_of) -> list[dict]:
    """진단 + 시뮬 → 제안. 각 제안: {proposal_type, target_type, target_id, campaign_id,
    rationale(3소스: 진단사실+시뮬근거+target근거라벨), expected_effect(사람읽을 예측·가정),
    capability_flags, status='pending'}.
    - optimizer='ours'만(D-NAO-13). none/mop 제외.
    - negative_keyword 제안은 '비용/볼륨 후보(전환귀속 없음)' 라벨(검색어행 전환데이터 없음, codex #9).
    - target 근거 라벨(account_default/override, D-S3-b/codex #5).
    - dedup: (proposal_type, target_id, status='pending') 존재 시 skip — 단일 08:00 크론이라
      트랜잭션 내 check-then-insert로 충분(DB 유니크 인덱스는 P3 병렬/재시도 하드닝 TODO, codex #2)."""
def persist(db, proposals) -> list[NaverProposal]:  # INSERT
def account_brief_singleton(db, diagnosis, as_of) -> NaverProposal:
    """계정레벨 일일 브리프(첫 제안서 S26 질문·확장버킷/출혈/굶는승자 요약) — 결정적 싱글톤,
    dedup·optimizer 필터 무관하게 매일 1건 보장(codex #17). proposal_type='account_brief'."""
```

**④ `slack_notifier.py`** (신규 SA)
```python
def notify(proposals, *, webhook_url=None) -> dict:
    """webhook 미설정(env NAVER_SLACK_WEBHOOK_URL 없음) → no-op+로그(D-NAO-21).
    설정 시 요약 블록 발송. incoming webhook은 대개 message ts 미반환 → slack_ts는
    best-effort(없으면 null, 완료기준 아님, codex #15). 타임아웃·재시도."""
```

### 3.3 Harness `proposal_pipeline.py` (신규)
```python
def run_daily(db, *, lookback_days=15) -> dict:
    """08:00 엔트리. ①freshness 게이트(stale/부분→스킵+알림) ②as_of=마지막 완결 KST일
    ③상위 aggregate precompute ④diagnosis→bid_simulator→proposal_writer→slack ⑤계정 브리프
    싱글톤 ⑥만료 패스. 각 단계 예외는 그 단계만 skip + 단계상태 기록(ok/degraded/failed),
    제안에 capability_flags 부착(codex #11). 반환: {generated, skipped, expired, stage_status, errors}."""
```
- cron `generate_naver_proposals` 08:00.

### 3.4 Router (`routers/naver_ad.py` 확장)
- `GET /proposals?status=&date_from=&date_to=`.
- `GET·PUT /campaign-settings` — optimizer/mode/override. **optimizer 전환 시 변경 로깅**(codex #16, 경량 — 누가·언제·전후). 
- **라우터 HTTP 왕복 테스트(TestClient) 필수** — P2-S2 500 사고 재발방지.
- (이상피드 엔드포인트는 S3c로 연기 — D-S3-c)

### 3.5 프론트 (S3b) — `NaverAdReport.tsx` "최적화 콘솔" 탭
- 탭: 리포트 / 진단 보드 / **최적화 콘솔**. 사이드바 항목 안 늘림.
- 2섹션: ①제안 카드(type·target·근거·target라벨·capability플래그·**실행 버튼 disabled** 관찰모드) ②캠페인 optimizer 패널(none/ours/mop PUT·mode·override). (이상피드는 S3c)
- `api.ts` + `fetchNaverAdProposals`/`fetchNaverCampaignSettings`/`putNaverCampaignSettings`. dev 백엔드 8000 고정, CORS 5173.

### 3.6 S3c (연기 — D-NAO 원안 복원, 관찰 검증 후) 
- `budget_allocator.py`: `marginal_roas`(→ **"예산 민감도 신호"로 명명**, 상관≠인과 명시 codex #18)·`allocate`(총상한 불가침·±15% 클램프·BEP미달 증액금지·**보존 단언 sum≤cap**·헤드룸=한계ROAS순 D-NAO-1). daily_budget=`get_campaigns_full()`/hourly_snapshot(staleness 처리).
- 경량 이상피드: hourly_snapshot 소진율 단순규칙 + `GET /anomaly-feed`. (본격 파수꾼은 P4)

---

## 4. 체크리스트 (✅완료 🔄진행 ⏳대기)

### S3a — 백엔드 (bid_simulator + proposal + slack + harness)
- [ ] ⏳ estimate 라이브 프로브 → fetcher 2함수(자격증명 함수내 읽기·device) + 실측 스펙 `docs/references/23_naver_sa_estimate_recon.md`
- [ ] ⏳ `bid_simulator.py` (pooled_rpc·affordable_ceiling[division guard]·simulate_bid[device 가정 라벨·capability]) + 단위테스트
- [ ] ⏳ `proposal_writer.py` (build[optimizer 필터·target 라벨·negative 라벨·dedup]·persist·account_brief_singleton) + 단위테스트
- [ ] ⏳ `slack_notifier.py` (no-op 폴백·slack_ts best-effort·타임아웃·재시도) + 단위테스트
- [ ] ⏳ `proposal_pipeline.py` (freshness 게이트·as_of·precompute·단계상태·만료) + 통합테스트
- [ ] ⏳ cron `generate_naver_proposals` 08:00 등록
- [ ] ⏳ Router 2개(proposals·campaign-settings+전환로깅) + **TestClient HTTP 왕복(500 재발방지)**
- [ ] ⏳ 라이브 검증(원칙22): prod DB 스크래치 사본 → **카나리 1~2 캠페인 optimizer='ours' 세팅(스크립트)** → harness 실행 → 제안 생성·저장 확인 / prod 배포(sha256 scp·pm2) → 08:00 자율 발화 or 수동 → `naver_proposals` 실적재 SQL 확인 / freshness 게이트·slack no-op 확인
- [ ] ⏳ codex review(원칙19) → 트랙·progress 갱신·커밋

### S3b — 프론트
- [ ] ⏳ "최적화 콘솔" 탭 + 2섹션(제안 카드·optimizer 패널)
- [ ] ⏳ `api.ts` 타입 + fetch 3종
- [ ] ⏳ optimizer PUT 반영 라이브 e2e
- [ ] ⏳ `tsc -b --noEmit` + build + prod 배포(rsync)·라이브 렌더 검증
- [ ] ⏳ 트랙·progress 갱신·커밋

### S3c — 예산·이상 (관찰 검증 후 착수)
- [ ] ⏳ `budget_allocator.py`(예산 민감도 신호·보존 단언 test·Δcost=0 guard) + `GET /anomaly-feed` + 콘솔 이상피드 섹션

## 5. 리스크·라이브 프로브 (착수 시 — 원칙22)
1. **estimate 스펙**: 로컬 격리 venv + prod `.env` NAVER_SA_* 읽기전용(`ssh cat`) → 실호출. **prod venv 무접촉**(anyio 크래시루프 교훈).
2. **freshness**: 08:00 생성 전 naver_ad_daily 신선도 필수 확인 — 실패 시 제안 0건이 정상(stale 제안 금지).
3. **Slack**: URL 없으면 no-op 완주 우선 검증. slack_ts 미반환 정상.
4. **관찰 한계**: 학습 루프 1·2는 실행 원료 필요 → 관찰 중 휴면. 카나리 조기 P3 승격 시 시작(§3.5 상위계획).

## 6. 완료 기준 (라이브, 원칙14/22 — 경계 정정 codex #20)
- **S3a**: 카나리 캠페인 'ours' 세팅 상태에서 08:00 체인이 제안 생성·`naver_proposals` 저장(SQL 확인) / freshness 게이트 동작(stale이면 스킵) / 계정 브리프 싱글톤 매일 1건(확장버킷·출혈·굶는승자 + S26) / slack 미연결 no-op 로그 / 테스트·라우터 HTTP 왕복 pass.
- **S3b**: 콘솔 2섹션 브라우저 렌더 + optimizer PUT DB 반영.
- **S3c**: (연기) 예산 제안 보존법칙 test·이상피드 렌더.

## NOT in scope (명시적 연기)
- **budget_allocator + 경량 이상피드** → S3c(D-S3-c, Jino 승인). 관찰 모드 제안 품질 증명 후.
- **campaign_target_resolver ②(상품BEP 연결)** → P3+(소스 확보 시, D-S3-b).
- **device per-기기 split** → P0 device-split 재작업 필요, 현재 롤업+가정 라벨로 진행.
- **naver_proposals DB 유니크 인덱스** → P3 병렬/재시도 하드닝(현 단일 크론엔 앱로직 dedup 충분, codex #2).
- **육성 파이프라인 실집행** → P3+(S3는 육성후보 "제안"만).
- **광고 API 쓰기(입찰/예산/제외 실행)** → P3(execution_harness).

## What already exists (재사용)
- diagnosis harness·진단 SA·campaign_target_resolver·fetcher(_headers/get_campaigns_full[dailyBudget]) 재사용. 테이블 4개 P0 기존. **병렬 구축 없음** — 신규는 bid_simulator·proposal_writer·slack_notifier·harness·cron만.

## Implementation Tasks
plan-eng-review 발견에서 합성. 각 태스크는 특정 발견에서 파생. Sonnet 구현.

- [ ] **T1 (P1, human: ~3h / CC: ~20min)** — fetcher — estimate 라이브 프로브 + 2함수(device·함수내 자격증명·호출량 캡·429 백오프)
  - Surfaced by: Arch-3 / codex#7(device 롤업)·#19(import-time env)
  - Files: `naver_sa_ad_fetcher.py`, `docs/references/23_naver_sa_estimate_recon.md`
  - Verify: 로컬 격리 venv 실호출 200 + 스펙 문서화
- [ ] **T2 (P1, human: ~4h / CC: ~25min)** — bid_simulator — pooled_rpc·affordable_ceiling(RPC÷target)·simulate_bid(division guard·device 라벨·capability flags)
  - Surfaced by: CQ-5(RPC 단순화) / codex#6(AOV 소스)·#8(estimate 전환 없음)
  - Files: `backend/app/services/naver_ad/bid_simulator.py`
  - Verify: 단위테스트(폴백 4단·division·100%진입·게이트)
- [ ] **T3 (P1, human: ~4h / CC: ~25min)** — proposal_writer — optimizer 필터·target/negative 라벨·dedup·account_brief 싱글톤·expected_effect Text
  - Surfaced by: codex#1(predicted_json 부재)·#5·#9·#17
  - Files: `backend/app/services/naver_ad/proposal_writer.py`
  - Verify: 단위테스트(ours 필터·첫제안 싱글톤·dedup)
- [ ] **T4 (P1, human: ~1h / CC: ~10min)** — slack_notifier — no-op 폴백·slack_ts best-effort·타임아웃·재시도
  - Surfaced by: codex#15(incoming webhook ts 미반환)
  - Files: `backend/app/services/naver_ad/slack_notifier.py`
  - Verify: webhook 미설정 no-op + mock 발송
- [ ] **T5 (P1, human: ~3h / CC: ~20min)** — harness — proposal_pipeline(freshness 게이트·as_of KST·aggregate precompute·단계상태·만료)
  - Surfaced by: codex#12(freshness)·#13(타임존) / Arch-2 / Perf-7(N+1)
  - Files: `backend/app/services/naver_ad/proposal_pipeline.py`
  - Verify: 통합테스트(stale 스킵·부분실패 격리)
- [ ] **T6 (P1, human: ~30min / CC: ~5min)** — scheduler — cron generate_naver_proposals 08:00
  - Files: `backend/app/services/scheduler_service.py`
- [ ] **T7 (P1, human: ~2h / CC: ~15min)** — router — proposals + campaign-settings(전환로깅) + TestClient HTTP 왕복
  - Surfaced by: codex#16 / P2-S2 500 재발방지(REGRESSION RULE)
  - Files: `backend/app/routers/naver_ad.py`, `backend/tests/test_naver_ad_proposals_router.py`
- [ ] **T8 (P1, human: ~2h / CC: ~20min)** — verify — 카나리 ours 세팅→08:00 체인 제안 생성·저장·freshness·slack no-op
  - Surfaced by: codex#20(완료기준 경계)
- [ ] **T9 (P2, human: ~4h / CC: ~30min)** — frontend — S3b 최적화 콘솔 탭(제안 카드·optimizer 패널)
  - Files: `frontend/src/pages/NaverAdReport.tsx`, `frontend/src/lib/api.ts`

_S3c(budget_allocator·이상피드)는 별도 스프린트 — 여기 태스크 아님(D-S3-c 연기)._

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | issues_found | 20 findings, 12 folded / 3 converged / 2 partial / 1 escalated |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 15 issues, 0 critical gaps, mode SCOPE_REDUCED |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **CODEX:** outside voice found 12 gaps I missed (no `predicted_json` column; daily_budget source already exists via `get_campaigns_full()`; device rollup vs estimate device requirement; no freshness gate; no timezone/window policy; slack_ts webhook reality; first-proposal singleton). All folded into the plan.
- **CROSS-MODEL:** codex + eng review agreed on 3 (AOV source undefined → resolved by RPC simplification; partial-failure silent proposals; marginal-ROAS causality overclaim). One tension (overbuild/scope) escalated to Jino → resolved: defer budget_allocator + anomaly feed to S3c (D-S3-c).
- **VERDICT:** ENG CLEARED (SCOPE_REDUCED) — ready to implement S3a. Scope reduced per D-S3-c; all codex findings folded or deferred with rationale.

NO UNRESOLVED DECISIONS
