# PAO 운영 정본 — 지금 광고가 어떻게 돌고 있고, 주기적으로 무엇을 봐야 하나

> **정본 — 좌표는 `scripts/check_pao_canon.py`가 검사한다** (계약 `docs/contracts/CONTRACT_pao_ops_canon.md` §8 · S3 산출물).
> **서식 규약 요약 (다음 저자도 지킬 것)**:
> 1. 좌표(백틱)는 5유형만 검사된다 — 파일경로 / «경로::심볼» / «GET|PUT /api/…» / «테이블.컬럼» / 크론 잡 이름. **줄 번호 금지**(커밋 하나에 낡는다 — 심볼로 쓴다). 명령어·예시는 백틱에 넣지 않는다(검사기 오탐 방지).
> 2. **날마다 변하는 실측값은 전부 `<!-- MEASURED -->` … `<!-- /MEASURED -->` 블록 안에** 쓴다 — 검사기는 블록 **밖**의 백틱만 본다.
> 3. 실측값에는 **관측 시각(KST)과 창의 시작·끝 날짜**를 병기한다(라벨만 병기하면 라벨이 틀렸을 때 같은 병이 재발한다 — ref 111 반증 1·2).
> 4. 재현 안 된 주장은 무표기 금지 — `[미상]` + 출처·날짜로만. 📄=문서 주장(재검증 안 됨).
>
> 전면 개정: 2026-08-30 (계약 승인 D-NAO-279 · 재검증 정본 `docs/references/111_paoops_reverify_20260830.md` — 사실 주장 153개 중 재현 52 / **반증 4** / 미상 다수. 옛 판은 git 역사에 있다).

---

## §0. ⚠️ 지금 알아야 할 것 — 결정·조치가 필요한 것부터

### ★0-1. 카나리는 「발화는 하되 아무것도 집행하지 않는」 상태다

08-29 12:53 카나리 1캠페인 점화(D-NAO-275 📄). 그런데 그 캠페인은 **SHOPPING**이고, 자동 발사되는 검색어 제외의 실행 경로는 **파워링크(WEB_SITE) 전용**이다(`backend/app/services/naver_ad/search_term_ss_lane.py::_autofire_exclude` — 쇼핑 후보는 브리핑 diary만). 입찰 레버는 표본·ROAS 게이트에 걸려 있다.

★**검색어 제외 파이프라인은 «둘»이고 섞으면 오판한다** (ref 111이 재현 확인):

| | 파이프라인 | 크론 | proposal_type | 자동 발사 | 실행 이력 |
|---|---|---|---|---|---|
| A | PAO 고유 `search_term_ss_lane` | 08:50 | search_term_exclude | 파워링크만 | 전 기간 **1건**(07-22, 파워링크) |
| B | 레거시 `backend/app/services/naver_ad/proposal_writer.py` | 08:00 | negative_keyword | 없음 | 전 기간 실행 **0건** — 게이트 0겹, SHOPPING 대상은 실행돼도 항상 실패(WEB_SITE 전용 API). 그 안전은 설계가 아니라 우연 |

<!-- MEASURED -->
- 2026-08-30 17:1x KST (ref 111): 오늘 A 레인 산출물 0건(계정 전체) · 오늘 우리 엔진 실쓰기(flight_pacing 제외) 0건 · 제외 원장 총 3,990행, 최신 생성 2026-08-17(13일째 신규 0) · B 파이프라인 pending 11건(08-30 08:00 생성, 6개 그룹에 걸침 — 카나리 스코프 밖).
- PAO 마지막 실집행: 2026-07-30 📄(ref 109) — 이후 31일+ 0건.
<!-- /MEASURED -->

### ★0-2. 결정 대기 (전부 Jino 몫 — 이 문서는 사실까지만)

| | 무엇 | 비고 |
|---|---|---|
| A | **카나리 실집행 경로** — ①A 레인 후보 Confirm(지금은 후보 0) ②카나리를 파워링크로 교체 ③C2 합격기준 개정(=계약 개정) ④「구조상 실집행 0」을 M4 판정으로 기록 ⑤쇼핑 자동 발사 개방(§0-3) — 다섯 다 계약 사안 | 대기 시작 08-30 📄 |
| B | **소유권 분리**(북극성 §8-②) — 재개방·A급 실험·개입 검증 셋을 동시에 막는다 | 08-19부터 📄 |
| C | **prod 배포가 타 트랙 마이그에 막힘** — 죽은 카드 수리(PR #573 머지 📄)가 미배포. `scripts/safe_deploy.sh`가 원가 트랙 마이그 미적용을 이유로 거부. 남의 마이그레이션은 대신 적용하지 않는다 | 📄 |
| D | ADVoost·GFA 취급 / 스마트스토어 대조축 / n-gram 적자어 처분 / A3 회색 토큰 4건 / WEB_SITE 승계 3건 / ref94 하한 음수 후속 | 전부 📄(옛 판 §8, 08-19~) — 상세는 북극성 `docs/references/82_pao_north_star_20260819.md` §8 |

### ★0-3. 쇼핑 검색어 제외 자동 발사 — 결정 A-⑤의 근거 요약 (옛 판 §3-b 압축, 2026-08-30 조사 📄)

- 막힌 명시적 근거가 없다 — 「쇼핑 API 불가」 전제는 D-NAO-180·181로 폐기됐고, 이후 「왜 안 여는가」의 새 근거는 grep으로 안 나온다 📄. 의도적 정지가 아니라 관성.
- **비대칭이 핵심**: 파워링크 검색어는 전환을 구조적으로 못 재서 잘못 잘라도 잃을 게 없지만, **쇼핑은 전환이 찍히므로 잘못 자르면 진짜 매출을 잃는다**. <!-- MEASURED -->실측(2026-08-30 17:1x, 창 대략 07-30~08-29): expkeyword 82,141행·전환 0건 / shopping 328,126행·전환 1,584건(0.483%) — 옛 판 값과 ±4%(창 경계 차이)<!-- /MEASURED -->
- 단점 셋 다 실측 📄: ①14일 창의 오탐 실증(「폴드8와이드필름」 24클릭·창내 전환 0인데 90일 전환 9) ②**되돌릴 손이 없다** — 수동 재개방 API 0건, 자동 재심사는 우리 생성 2칸에만 닿음 ③일일 캡 10건을 파워링크와 공유.

### ★0-4. 켜져 있는 부채

<!-- MEASURED -->
| 부채 | 값 (관측 시각) | 소관 |
|---|---|---|
| 죽은 승인 카드(approved·영구 미실행) | 141건(08-30 08:5x 📄) → 154건(10:17 📄) → **180건**(17:19, ref 111) — 증가 중 | 이 트랙 — 수리는 main에, 배포만 남음(§0-2-C) |
| 제외 원장 신규 | 08-17 이후 0건 (08-30 기준 13일째) | 이 트랙 |
| 지혜 승격→적용 왕복 | prod 0회 📄 | 이 트랙 |
| 학습 재료 만료 | 2026-10-28에 3,863건 이탈 📄(D-NAO-251) | 이 트랙 |
| 쿠팡 계열 크론 last_run_at 08-22 정지 📄 | 7종 | ⚠️PAO 밖 — 쿠팡 트랙 소관, 기록만 |
<!-- /MEASURED -->

---

## §1. ★광고 성과 — 광고가 돈을 벌고 있나 (신설, Jino 질문 *"광고 성과 지표는 안봐?"* 대응)

### 1-1. 성과 원장 (구조 — 안 변하는 것)

- `naver_ad_daily` — grain (ad_date, campaign_id, adgroup_id, keyword_id). `naver_ad_daily.cost`·`naver_ad_daily.conv_direct_cnt` 등 보유, **D-1 확정 적재**(오늘치 없음). ★집계 정본: `adgroup_id <> '__backfill__'`, keyword 필터 걸지 말 것(2배 중복 사고 전력).
- `naver_search_term_daily` — grain에 search_term·source. source='shopping'만 전환이 채워지고 source='expkeyword'(파워링크)는 구조적으로 항상 0.
- 상품 BEP 스냅샷 `naver_product_bep` · 조치 채점 `naver_change_log.outcome_profit`(+gave 전후).

### 1-2. 총이익(목적함수)을 재는 코드와 그 표면

- **`backend/app/services/naver_ad/profit_scorecard.py`** — D-NAO-59 목적함수(총이익 절대액)를 캠페인별로 매일 계산(식: 보정 전환매출 ÷ bep_roas − cost). 크론 `run_naver_profit_scorecard` 08:40. 대상은 `backend/app/services/naver_ad/campaign_roster.py::observation_campaign_ids`(auto_operate 무관).
- ★**단, 이 계산 결과는 diary·Slack으로만 간다 — API·화면 배선 0건**(ref 111 §5-3, grep 확인). 화면들이 보여주는 ROAS·공헌이익은 **별도 코드 경로의 재계산**이다. 두 벌 계산이 갈라지는지는 [미상 — 대조 안 됨, ref 111].

### 1-3. 사람이 보는 곳 (전부 실재 — ref 111 「가장 놀란 것」: 옛 판 13개 절이 이 화면을 한 번도 언급 안 함)

- 화면: `frontend/src/pages/NaverAdPerformance.tsx` — `/naver-ad/performance` 라우팅. ROAS 카드(목표/BEP 대비)·캠페인 추이·공헌이익.
- API 10종: `GET /api/naver/ad/performance/ownership-bands`(전체/PAO 관할/비관할/전환일/모름 — `backend/app/services/naver_ad/perf_ownership_bands.py`, 항등식 identity.ok 검사 내장) · `GET /api/naver/ad/performance/bep-breakdown`(상품별 BEP 구성 되짚기 — `backend/app/services/naver_ad/bep_breakdown.py`) · today/day/compare/campaigns/campaign/{id}/budget/timeline/ownership-campaigns.

### 1-4. 지금 값

<!-- MEASURED -->
관측 2026-08-30 17:17 KST, prod naver_ad_daily, adgroup_id <> '__backfill__' (ref 111 — 옛 판의 「어제 537,105원」·「7일 4,051,618원」은 반증됐다: 각각 08-28 값·8일 합이었다. 그래서 창의 시작·끝을 명시한다):

| 창 (시작~끝, KST) | 비용 | 전환 | 전환매출 | ROAS |
|---|---:|---:|---:|---:|
| 07-30~08-29 (달력일 31일 포함창) — 계정 전체 | 19,923,726원 | 2,225 | 35,377,700원 | 177.5% |
| 07-30~08-29 — 카나리 캠페인만 | 7,724,101원 | — | 11,583,480원 | 150.0% |
| 08-23~08-29 (7일) — 계정 전체 | 3,561,309원 | — | — | — |
| 08-29 (하루) | 443,894원 | — | — | — |
| 참고: 08-28 (하루) | 537,105원 | — | — | — |

- 최근 7일 naver_change_log 776건: flight_pacing(관찰) 516 · 대행사 external 3종 256(키워드 제거 133·입찰 119·상태 4) · optimizer_change 3 · scope 1. **우리 엔진 실쓰기 0건.** (옛 판의 756/238과 총계 불일치 — 관측 시각차 8h인지 오류인지 미분리, ref 111 부분반증)
- 카나리 총이익 기준선: 점화 시점 하한 자 −275,787원 📄(옛 판 §6, 재검증 안 됨) — 계약 금지선 「하한 자 적자면 확대 금지」에 걸려 있는 상태 📄.
<!-- /MEASURED -->

⚠️**§1-4의 ROAS(31일 포함창)를 BEP 171.1%와 그대로 비교하지 말 것 — 창이 다르다.** BEP 171.1%는 **391일 창 계정 블렌디드** 📄(ref 63)다. 창이 달라 「본전 위/아래」 판정은 [미상 — 같은 창으로 다시 재기 전엔 비교 불가]. 상품 단위 BEP는 624그룹 미확보 📄라 그 구간은 블렌디드로 뭉개진다(알려진 구멍).

### 1-5. 성과 판독의 구멍 (있는 것/없는 것만 — 배선 목록은 §12)

①profit_scorecard 화면 미배선(§12-12) ②관할 밴드 × 캠페인유형 교차 없음(§12-13) ③관할 분리를 «총이익 기준»으로도 하는지 [미상] ④상품 단위 «실현» 이익(성과×마진) 도달 여부 [미상] ⑤D-0은 원리적으로 밴드 분리 불가(설계 한계 — D-1 적재).

---

## §2. 엔진의 손 — 무엇이 열려 있나

<!-- MEASURED -->
2026-08-30 17:14 KST (ref 111 정확 재현):

| | 값 |
|---|---|
| 계정 전체 캠페인 | 46 (SHOPPING 31 · WEB_SITE 13 · BRAND_SEARCH 2) |
| optimizer='ours' | 1 — cmp-a001-02-000000008425541 (갤럭시_지문방지_TPU, SHOPPING) |
| auto_operate=1 | 1 (동일 캠페인) |
| 스코프 행 | 1행 — 광고그룹 grp-…70523564 (Z폴드8와이드), enabled=1. 같은 캠페인의 다른 8개 그룹은 스코프 밖 📄(ref 109) |

되돌리기 1줄: `UPDATE naver_campaign_settings SET auto_operate=0 WHERE campaign_id='cmp-a001-02-000000008425541';`
<!-- /MEASURED -->

- ⚠️`auto_operate`를 **켜는 API는 없다** — 직접 DB UPDATE가 유일한 경로(`backend/app/services/naver_ad/ignition_preflight.py::check` 모듈 주석). `optimizer`는 전용 엔드포인트 있음: `PUT /api/naver/ad/campaign-settings/optimizer`.
- 점화 «직전» 검사: `GET /api/naver/ad/campaign-settings/ignition-preflight` — scope_empty(스코프 0행이면 켜는 순간 **전 그룹 개방** — 진리표 기본값)·slots_exhausted 경고. safe_to_ignite는 «경고 없음»이지 «켜도 좋다»가 아니다(코드 원문). ★캠페인 타입×레버 정합(§0-1)은 **안 잡는다** — §12-4.
- 쓰기 유일 어댑터: `backend/app/services/naver_ad/naver_sa_writer.py` — 성공 판정은 응답 코드가 아니라 재조회 실측(fail-closed). 제외 읽기는 `backend/app/services/naver_ad/naver_sa_writer.py::RESTRICT_TYPES` 둘 다(한 타입만 물으면 「없다」는 거짓말을 받는다).

## §3. 무엇이 언제 도는가 (크론)

★**시각의 정본은 prod scheduler_state 행이다.** 아래는 `backend/app/services/scheduler_service.py::_ensure_default_states` seed 값 — 한 번 seed되면 코드는 기존 행을 안 고친다. prod와의 대조는 §14 레시피. 실쓰기 5종의 cron은 prod와 일치 확인됨(ref 111 정확 재현). 총 개수는 세는 방법에 따라 다르다(<!-- MEASURED -->job_name LIKE '%naver%' 기준 36건, 2026-08-30 — 옛 판 「35종」은 반증(약함), ref 111<!-- /MEASURED -->) — **이 문서는 총계를 자로 쓰지 않는다.**

**실쓰기 크론 5종** (광고 계정을 실제로 바꿀 수 있는 것):

| KST | 잡 | 무엇 |
|---|---|---|
| 08:50 | `run_naver_auto_operator_daily` | 일 레인 — `backend/app/services/naver_ad/auto_operator.py::run_daily_lane` |
| 매시 :20 | `run_naver_auto_operator_hourly` | 시간당 레인 — `backend/app/services/naver_ad/auto_operator.py::run_hourly_lane` + 예산 페이싱 |
| 08:55 | `run_naver_probe_settlement` | 탐침 되돌림 |
| 00:05 | `run_naver_budget_pacing_reset` | 예산 원복 |
| 08:05 | `generate_expert_desk` | Ava 평결 + 위임 게이트 자동승인 가능 |

**읽기·판정·학습** (주요): 07:30 `sync_naver_ad_daily` · 07:35 `sync_naver_entity` · 07:50 `run_naver_forecast_engine` · 08:00 `generate_naver_proposals` · 08:10 `run_naver_learning_loops` · 08:25 `verify_search_term_exclusions` · 08:30 `run_naver_retro_scoring` · 08:35 `run_naver_diary_reflection` · 08:40 `run_naver_profit_scorecard` · 08:45 `run_naver_wisdom` · 09:03 `run_naver_probe_learning` · 09:35 `sync_naver_adgroup_targets`(제외 슬롯 라이브 count 적재) · 09:50 `sync_naver_keyword_baseline`(★소급 불가 축) · 매시 :05 `snapshot_naver_ad_hourly` · :47 `run_naver_inday_catchup` · :57 `sweep_naver_today_hourly` · 2시간 :15 `run_naver_flight_loop`(dry_run 고정 — 관찰 전용).

⚠️환경변수 기본값이 서로 반대 📄(옛 판, 미재검증): NAVER_BP_DRY_RUN 미설정=실쓰기 / NAVER_CS_DRY_RUN 미설정=dry-run — [미상].

## §4. 엔진이 스스로 쏘는 것 vs 사람이 눌러야 하는 것 (📄 옛 판 §3 — 검색어 제외 항목만 재검증됨, 나머지 [미상])

- **자동 발사**: 입찰 상향·하향(일/시간당/소재) · 탐색 상향 · rank-step · 예산봉투 태그 증액(≤10만) · 예산 페이싱 증액·원복 · 탐침·되돌림 · 스파이럴 복원 · **파워링크 검색어 제외**(전부 📄 — 개별 미검증).
- **사람 Confirm**: **쇼핑 검색어 제외**(★카나리가 여기 걸림 — 재현됨) · 비태그 예산 증액 · negative_keyword(콘솔) · 신규 캠페인 생성·재구축·예산 상한 인상(영구, D-NAO-5·42) · 위임 스위치(Jino만, D-NAO-25).
- **원리적 불가**: 의미 단위 검색어 제외(판정층까지만 — 계약이 그렇게 정함 📄).

## §5. 가드레일 — 지금 값

코드에서 직접 확인한 것(무표기)과 옛 판 주장(📄)을 구분한다. 정본 모듈: `backend/app/services/naver_ad/guardrail_gate.py` · DB 봉투층 `backend/app/services/naver_ad/guardrail_params.py::SPECS`(폴백은 코드 상수 — fail-to-current).

| 이름 | 값 | 근거 |
|---|---|---|
| 변경폭 클램프 | ±15% (탐색 ±30%) | `backend/app/services/naver_ad/guardrail_gate.py::_MAX_CHANGE_PCT` · `backend/app/services/naver_ad/guardrail_gate.py::_EXPLORATION_MAX_CHANGE_PCT` |
| 쿨다운 | 2시간 | `backend/app/services/naver_ad/guardrail_gate.py::_COOLDOWN_HOURS` (D-NAO-55) |
| 일일 변경 상한 | 3회 | `backend/app/services/naver_ad/guardrail_gate.py::_MAX_DAILY_CHANGES` |
| 입찰 클램프 | 70~100,000원·10원 단위 | `backend/app/services/naver_ad/guardrail_gate.py::_MAX_BID` (이중 방벽 — writer에도 있음) |
| 누적 상승 상한 | 기준가 ×2.0 | `backend/app/services/naver_ad/guardrail_params.py::max_auto_up_multiple` |
| 스톱로스 | 무전환 지출 ≥ 기준가 ×10 📄 | [미상 — 상수 미조회] |
| BEP 이익하한 | 보정ROAS < 목표면 증액 금지 | accel_gate_view가 이 게이트의 차단량을 셈(§8) |
| CPC 급등 하향 배율 | ×2 📄 (PLAN 문서엔 ×1.5 — 코드가 정본 📄) | [미상 — ref 111도 실행 코드 상수 못 찾음] |
| 제외 슬롯 | 그룹당 70칸(파워링크 자동은 60 📄) | `backend/app/services/naver_ad/exclusion_slot_usage.py` |
| SPECS(DB 우선) | ss_min_click 10 / ss_window_days 14 / pl 5·30 / cooldown_hours 2 📄 | 현재값 표면: `GET /api/naver/ad/settings/guardrail-params` |

⚠️옛 판 §5의 판정 임계값 다수(표본 30·창 3h·UP 최소 전환 2 등)는 재검증이 가장 얕았던 절이다(ref 111 자백: 18개 중 1개 시도) — 전부 📄로 강등. 값을 쓰려면 코드를 열어 확인할 것.

---

## §6. 주기 체크리스트 — 모드 A: 정지 국면 (지금)

질문은 둘: 「멈춰 있다는 사실이 보이는가」 「멈춘 동안 부패·손실이 쌓이지 않는가」. 대행사는 정지 중에도 계정을 바꾼다. (🤖=자동 산출, 표면 병기 / 👁=사람이 봐야 함. 주기 근거: 일=D-1 확정 적재+아침 배치 사슬, 주=승격 TTL 14일·주간 잡, 월·시즌=전환 정착 D+7·시즌 준공선. 매분·매시 항목이 없는 이유: 정지 국면엔 그 주기의 신호가 실재하지 않는다.)

### A-일 1회

| # | 무엇 | 왜 (목적 5요소·금지선) | 좌표 | 판정 | 이상 시 |
|---|---|---|---|---|---|
| A1 🤖 | 수집 크론 생존·데이터 나이 | ①의 전제 | `GET /api/scheduler/health` → 전역 헬스 배너. `backend/app/services/scheduler_health.py`(잡 자기보고+data_stale 이중 감시) | STALE/FAILED 0건 | 해당 잡 재실행·원인 조사 |
| A2 👁 | 죽은 승인 카드 잔량 | 원장이 거짓말 금지 — 콘솔이 «실행 가능»으로 오표시 📄 | `naver_proposals.executed_change_log_id` IS NULL ∧ approved (§14 레시피) — 전용 표면 없음(§12-1) | 0건 정상 | 배포 판단(§0-2-C) |
| A3 🤖 | 반성 루프 침묵 — 재료 없음 vs 고장 구분 | ⑤ (20일 침묵 실사고 재발 방지) | `backend/app/services/naver_ad/reflection_health.py` → `GET /api/naver/ad/wisdom-scorecard` 응답 reflection_health → 콘솔 `frontend/src/pages/NaverAdOptimizationConsole.tsx` | 재료 있는 결번만 이상. 08:35 전 조회는 not_due | 스케줄러 로그 조사 |
| A4 🤖 | 우리 제외의 생존(대행사 되돌림) | 학습 오염 금지선 — 되돌림 2회 전례, 1회는 로그 무흔적 | `verify_search_term_exclusions` 08:25 → `backend/app/services/naver_ad/exclusion_survival.py`(delFlag까지 상태 대조) → `GET /api/naver/ad/search-term/exclusion-survival` | 되돌림 0 | 기록·표면화(감시가 조치를 바꾸면 감시가 아니다) |
| A5 🤖 | 제외 슬롯 사용률·미귀속 칸 | 70/70=음의 레버 소멸 | `GET /api/naver/ad/search-term/exclusion-slots` ← 09:35 스윕 적재 | 70/70 무조건 빨강. 미귀속을 0으로 뭉개지 않았는지 | 처분은 Jino(소유권 분리 계열) |
| A6 🤖 | 외부 변경 감지(소재 grain 포함) | 학습 오염 — 값 비교만으론 왕복 되돌림이 무변동으로 보임 | `backend/app/services/naver_ad/ad_external_change.py`(editTm 앵커) · `backend/app/services/naver_ad/bm_diff.py` | 기대값 없음 — 급증만 주시 | 개입 검증은 소유권 분리 전 금지 |

### A-주 1회

| # | 무엇 | 왜 | 좌표 | 판정 |
|---|---|---|---|---|
| A7 👁 | 정지 일수 자체(마지막 실집행 이후 N일) | ④ — D-NAO-226 검토의 원료 | §14 실쓰기 레시피. 전용 카운터 없음(§12-5) | 추세가 늘기만 하면 병목은 §0-2-A·B |
| A8 🤖 | pending 만료 발생 | 「승인 대기가 조용히 죽는」 경로 — promote 300건 만료 전례 📄 | `GET /api/naver/ad/proposals` — 만료 알림 없음(§12-2) | expired 신규 0 |
| A9 🤖 | 지혜 성적표의 «표본 0» 정직성 | ⑤ — 0을 «문제없음»으로 렌더 금지 | `backend/app/services/naver_ad/wisdom_scorecard.py`(has_evidence/evidence_gap) → `GET /api/naver/ad/wisdom-scorecard` | «잴 것 없음»과 «재 봤더니 나쁨»이 구분돼 보이는가 |
| A10 👁 | 학습 재료 만료 시계 | ⑤ — 10-28에 3,863건 이탈 📄 | 표면 없음(§12-8) | 날짜 상기만 — 재개 촉구는 이 문서 소관 아님 |

### A-월·시즌 / 이벤트

- A11 🤖 소급 불가 축 지속 가동(`sync_naver_keyword_baseline` 09:50) — 죽으면 그 구간 영구 결번. A1 배너가 겸함.
- A12 👁 시즌 준공선(아이폰 9월·Z 7월중~8월초, D-NAO-183 불가역 📄) — 재개 전후 비교 창 설계 시 상수.
- A13 [점화 직전] preflight 조회(§2) + 부품 생존(§14 — ⚠️레시피 수정됨) + **캠페인 타입×레버 정합은 사람이 대조**(§12-4).
- A14 [배포 직후] 블루-그린이 pm2 로그 파일을 옮긴다 📄 — 배포 «전» 발화가 활성 로그에서 사라짐(에러 아닌 빈 결과). 정본은 `scheduler_state.last_run_at`.

## §7. 주기 체크리스트 — 모드 B: 가동 국면 (실집행이 재개되면. 모드 A 전 항목 유효)

### B-매시간

| # | 무엇 | 좌표 | 판정 |
|---|---|---|---|
| B1 🤖 | 시간당 레인 발화·킬스위치 실시간 재확인 | `run_naver_auto_operator_hourly` :20 → `backend/app/services/naver_ad/auto_operator.py::run_hourly_lane`(실행 직전 auto_operate 재확인 — 킬스위치 OFF pending은 폐기 안 함 「정지≠폐기」) | OFF 시 즉시 hold |
| B2 🤖 | 손실 방어 발동(RL3 고삐·CD 밸브) | `backend/app/services/naver_ad/auto_operator.py::_intraday_loss_leash`(추정ROAS<BEP→한 등 하향, kill 아닌 leash — D-NAO-59) · 발동 캠페인은 탐색 UP 제외 | diary에 [순위고삐] 구분 기록 |
| B3 🤖 | 예산 페이싱 | `backend/app/services/naver_ad/budget_pacing.py`(소진율≥90% ∧ 프록시 ROAS≥target · fail-closed 8종 · ★프록시는 상한 프록시 — 광고 외 유입 포함) · 00:05 원복 | 증액분 익일 원복 확인 |
| B4 👁 | 무인 발화 확증 | as_of·last_run_at이 크론 슬롯과 소수점까지 일치(§14) — 자동 판정 없음(§12-9) | 임의 시각=수동 |

### B-일 1회

| # | 무엇 | 좌표 | 판정 |
|---|---|---|---|
| B5 🤖 | 승인→실행 관통률 | A2와 같은 SQL — 가동 중 신규 죽은 카드 0이어야. 레인별 스코프 검사 단일화는 `backend/app/services/naver_ad/auto_operator.py::engine_approve` | 신규 0 |
| B6 🤖 | 실집행↔일기 정합 | `backend/app/services/naver_ad/diary.py` ← 08:35 `backend/app/services/naver_ad/reflection_loop.py::run_daily_reflection`(backfill→해석, 단계 격리) | 실쓰기 수 = diary execute 행 수 |
| B7 👁 | 가드레일 차단 사유 분포 | 봉투 현재값 `GET /api/naver/ad/settings/guardrail-params`. ★전례: 차단 1위는 봉투(2.2%)가 아니라 소급채점 stale(55%)이었다(guardrail_params 독스트링) | 봉투 차단 급증 시 상류 데이터부터 의심. 완화는 사람 승인 경로만 |
| B8 🤖 | 대행사 되돌림 0 (M4 합격 관측 항목) | A4·A6 기제 | 카나리 창 «연속» 관측 |
| B9 🤖 | 총이익 스코어카드·소급 채점 | 08:30 `run_naver_retro_scoring` · 08:40 `run_naver_profit_scorecard` · 08:55 정산 · 09:03 학습 | 성적엔 자(尺)의 가정·창 병기 — §9 |
| B10 👁 | 누적 상향 상한(×2.0) 잔여 | §5 — 기준점 리셋은 사람 개입만 | 상한 근접 목록 |

### B-주 1회

- B11 👁 **액셀·브레이크 대칭** — §8.
- B12 🤖 지혜 승격·성적(`run_naver_wisdom` 08:45, TTL 14일/유사 3회 → 독립 판사) — 「지혜→총이익 기여」 양수 ≥1건이 M5 관측 항목.
- B13 🤖 판정면 주입 클램프 — `backend/app/services/naver_ad/wisdom_apply.py::propose_param_changes`: scope='unconditional' ∧ SPECS 화이트리스트 **둘 다**일 때만 제안. 반영 트리거는 콘솔 승인 핸들러(`POST /api/naver/ad/proposals/{proposal_id}/status`)뿐 — D-NAO-249 「승인=반영」, 무승인 자동 반영 0건 확인.
- B14 👁 카나리 성적 일반화 금지(M4 «안 함») — 확대는 새 계약.

## §8. 횡단 — 액셀·브레이크 대칭 검사 (금지선 1번의 집행 지점)

상습 실패 = ROAS 방어 표류(D-NAO-85: ROAS +7%·매출 −52%). 대칭은 **두 축의 다른 질문**이고, 집행 지점 셋이 이미 배선돼 있다:

| 지점 | 무엇을 세나 | 좌표 | 판정 |
|---|---|---|---|
| ① 게이트에서 죽는 액셀 | BEP 증액금지가 하한 자에서 몇 건 막았고 상한이면 몇 건이었나 — 양끝 나란히 | `backend/app/services/naver_ad/accel_gate_view.py` → `GET /api/naver/ad/diagnosis` 응답 accel_gate | 하한-상한 격차가 크면 자의 폭이 액셀을 죽이는 것. unmeasurable은 통과로 안 센다 |
| ② 봉투 변경의 방향 분류 | SPECS 키별 brake/accel 카운트 | `backend/app/services/naver_ad/wisdom_scorecard.py`의 방향 분류 → 콘솔(isBrakeOnlyDrift) | brake만 쌓이면 표류 — 단 실집행 0 국면은 verdict_pending |
| ③ 확장 압력의 실가동 | 액셀의 실물 | `backend/app/services/naver_ad/expansion_pressure.py`(3게이트 fail-closed) → 배분 `backend/app/services/naver_ad/expansion_allocator.py` | expansion_mode=True인데 집행 0 지속이면 액셀 배선 단선 |

축 구분(옛 판 §4 📄): 자동 발사 «경로»는 7:7 표면상 대칭이나, **학습 가능 파라미터(SPECS)는 브레이크 7 : 액셀 0 비대칭** — 「배움이 조이는 쪽으로만 열려 있다」. [미상 — SPECS 방향 분류는 ref 111 미조회.]

## §9. 횡단 — 자(尺) 건전성 (D-NAO-230: M4 합격 관측엔 총이익 항이 없어 자가 부풀어도 통과한다)

| 검사 | 좌표 | 판정 |
|---|---|---|
| 구간 자 생존 [0.827, 점추정] | `backend/app/services/naver_ad/correction_interval.py::CORRECTION_FACTOR_FLOOR`(=0.827, 근거 창 2026-07-25~08-23, ref 95) · `backend/app/services/naver_ad/diagnosis.py::_as_interval` | 하한 1.0 회귀는 결함. factor_floor_applied 확인 |
| 산출 불가 시 퇴화 | source unavailable이면 [1,1](근거 없이 매출 17% 깎기 금지) | unavailable인데 0.827 적용이면 결함 |
| 점추정 창 | `backend/app/services/naver_ad/diagnosis.py::_CORRECTION_LOOKBACK_DAYS`(30일 롤링). ★하한은 고정 창 상수 — 재검 주기 없음(§12-7) | — |
| 실운용 | 카나리·M5 성적 판독은 **「하한으로도 흑자인가」**(북극성 §6 각주) | 자의 가정·창 병기 없는 성적은 판정 재료 아님 |
| 밴드 순환성 | 캠페인유형·상품BEP·절대액 3축을 «발견»으로 소비 금지 | 🧠 자동 검사 없음 — 새 판정 규칙 추가 시 사람이 대조 |

## §10. 금지선 (정책 — 옛 판 §7 유지, 라이브 대조 대상 아님)

- `optimizer` 'ours' 아닌 캠페인 쓰기 절대 금지(D-NAO-13, 코드 하드체크) · 목적함수는 총이익 절대액(D-NAO-59) · **하한 자 적자면 카나리 확대 금지**(D-NAO-230) · 예산 변경 개방 금지 · 개별 캠페인 하드코딩 금지 · 카나리 성적 즉시 일반화 금지 · 홀드아웃 없이 발견을 집행에 넣지 않음(판정면 주입은 A/B 등급만) · 되돌릴 수 없는 액션은 항상 사람 Confirm(D-NAO-5·42), 위임 스위치는 Jino만(D-NAO-25) · 지혜발 파라미터 변경은 승인 카드·SPECS·봉투 안에서만(D-NAO-249), 무승인 자동 반영 금지 · 대행사 칸 반납 금지(소유권 분리 협의 전) · 실험 배치(memo) 캠페인 점화 금지 · 새 게이트·안전장치 발명 금지.
- 소진·폐기: yardstick 금지선 2건은 D-NAO-234·236으로 해제 📄(2026-08-24 배포).

## §11. 문서·코드·라이브가 어긋난 자리 (반복 사고 지점 — ref 111 판정 병기)

| # | 어긋남 | 정본 | ref 111 |
|---|---|---|---|
| 1 | `backend/app/services/naver_ad/naver_execution_harness.py` docstring 「자동 발사 없음」 ↔ `backend/app/services/naver_ad/search_term_ss_lane.py::_autofire_exclude`가 실제 자동 배선(08:50) | 코드(ss_lane) | 재현 |
| 2 | `backend/app/services/naver_ad/auto_operator.py::AD_BID_CANARY_CAMPAIGNS`(카나리 제한) ↔ `backend/app/services/naver_ad/auto_operator.py::AD_BID_ROUTING_ENABLED`=True라 `backend/app/services/naver_ad/auto_operator.py::_ad_bid_canary`가 무조건 True — 카나리 범위 제한은 **지금 작동 안 함** | 스위치 | 재현 |
| 3 | `backend/app/services/naver_ad/adgroup_scope.py`의 in_scope_now·campaign_level_allowed_now — 프로덕션 호출 0건(테스트만), 화면 `backend/app/services/naver_ad/pao_scope_roster.py`는 진리표를 자기 재조합(★ref 109: 「행 없음=전 그룹 ON」을 전 그룹 False로 계산 📄 — §12-10) | ⚠️미확정 — 잠재 결함 감시 | 재현(호출 0건) |
| 4 | `docs/contracts/CONTRACT_ignition_readiness.md` 헤더 「초안—승인 대기」 ↔ 사실상 종결 | 확인줄 | 재현 |
| 5 | 카나리 계약 헤더에 점화 완료 표시 없음 📄 | 확인줄 | 미상 |
| 6 | PLAN CPC ×1.5 ↔ 코드 ×2 📄 | 코드 📄 | 미상(실행 코드 상수 미발견) |
| 7 | §14 부품 생존 레시피가 prod에서 실행 불가였다(**반증 3** — 수정 반영됨) | 이 문서 | 반증→수정 |
| 8 | §14 시간대 경고문 「created_at·changed_at = UTC」 ↔ 라이브는 **쓰는 주체별로 갈림**(change_log 사실상 KST · proposals는 trigger_pacing만 KST) | 코드·라이브 | **신규 반증**(2026-08-31) — §14 개정 반영 |

### §11-B. ref 111 반증 4건 — 처분 표 (계약 D-NAO-280 P1)

★**이 표의 존재 이유**: 반증은 「틀렸다」까지만 적으면 다음 세션이 같은 자리를 다시 밟는다. **무엇으로 고쳤고 지금 어디를 보면 확인되는가**까지가 처분이다.

| # | ref 111의 반증 | 무엇이 틀렸나 | 처분 | 지금 확인할 곳 | 상태 |
|---|---|---|---|---|---|
| 1 | 「어제(08-29) 537,105원」 | 537,105원은 **08-28**의 값. 08-29 실제는 443,894원. 라벨과 값이 하루 어긋남 | §1-4 표를 **창의 시작·끝 날짜를 라벨에 박는** 형식으로 개정하고 08-28을 「참고」 행으로 분리 | 이 문서 §1-4 | **완결** |
| 2 | 「계정 7일 4,051,618원」 | 실제로는 08-22~08-29 **8일** 합. 진짜 7일(08-23~08-29)은 3,561,309원 | 위와 같은 개정으로 흡수 — 원인이 #1과 **같은 축**(하루 밀림)이라 처분도 하나다 | 이 문서 §1-4 | **완결** |
| 3 | §14 부품 생존 레시피 prod 실행 불가 | prod에 scripts/ 디렉터리가 없다 | scp → /tmp → backend에서 **stdin 형식** 실행으로 교체. <!-- MEASURED -->2026-08-31 10:38 KST 실행 검증: stdin·PYTHONPATH 형식 **8/8 절 성공**, 경로 형식은 마지막 절만 No module named app으로 실패<!-- /MEASURED -->. ★옛 판이 🧠(미검증)로 남긴 수정안을 **실행해서 확정**했고, 그 과정에서 **옛 수정안도 부분적으로 틀렸음**(경로 형식이면 여전히 죽음)을 발견 | 이 문서 §14 | **완결** |
| 4 | 「크론 35종」 ↔ 실측 36건 | 「35종」이 어떤 집합을 센 것인지 문서에 정의가 없어 정밀 대조 불가 | **총계를 자로 쓰지 않는다**로 규약을 바꾸고, 세는 방법(job_name LIKE naver)과 관측일을 병기 | 이 문서 §3 | **완결**(반증 자체가 약함 — 카운트 방법론 부재가 본질) |

⇒ 4건 전부 처분 완료. ★**#3이 남긴 일반 교훈**: 반증에 붙은 수정안이 🧠(미검증)로 남아 있으면 그것도 아직 반증이다 — **고쳤다고 적은 것을 실행해 봐야 처분이 닫힌다.**

## §12. 미배선·표면 없음 — 유일 등록부 (단계 × 상태 행렬, **14건** +판정 보류 2)

- **지위**: 이 표가 «지금 결여된 감시·배선»의 **유일 등록부**다(계약 「목적관철 목표」 D-NAO-280 §3 P1). `docs/references/114_issue1_pao_purpose_vs_implementation_20260830.md`(이슈1)·`docs/references/115_issue2_pao_missing_hands_20260830.md`(이슈2)는 **증거 문서**다 — 같은 항목을 다시 세지 않고 이 표의 행 번호를 가리킨다. `docs/references/116_issue3_fold8wide_death_spiral_20260830.md`(이슈3)의 지위는 **「사례 연구(증거)」** — 등록부도 처방도 아니다. 배선·수리는 전부 이 문서 범위 밖 — 목록이 산출물이다. ★값 저장소도 아니다 — 항목의 «크기»(건수·금액·날짜 경과)는 §0-4·§14로 잰다.
- **행렬인 이유**: 평평한 목록은 「배선이 아예 없다」와 「배선은 있는데 값이 비어 있다」를 같은 줄로 보이게 한다 — [[existence-gate-is-not-maturity-gate]]의 분류 편입(이슈2 정정 ②의 제안을 여기서 채택. 새 발명 아님).
- **축 정의**: **단계** = 제어 루프 순서 — 눈(입력·신호) / 자(판정 기준·임계값) / 손(실행 입구) / 표면(사람이 봄) / 되돌림 / 학습(결과→기준). **상태** 5값 — **없음** / **값이 빔**(배선은 있는데 채워지는 값이 빈다) / **꺼짐**(있는데 dormant — 아무도 안 부른다) / **삶** / **잡을 것 없음**(배선이 있어도 현 국면엔 그 신호 자체가 실재하지 않음). 상태 어휘를 임의로 바꾸지 않는다. **삶 = 등록부 퇴장 상태**다 — 항목이 삶에 도달하면 행을 지우고 지운 사유·좌표를 한 줄 남긴다. 잡을 것 없음은 현재 0건 — 정지↔가동 국면 전환 시의 재분류용으로 어휘를 남겨 둔다.
- **「이상 시 손」 열** = 이 항목이 겨냥한 이상이 실제로 확인됐을 때 **오늘 그것을 되돌리거나 고칠 손이 있는가**(있음/없음). 이 열이 떨어져 있던 자리에서 「처분은 정해졌는데 실행할 손이 없다」(이슈2)가 자라났다 — 그래서 열로 복원한다.
- ★**손 층의 전수(기능 31 중 손 빠짐 24)는 이 표에 복제하지 않는다** — 그 전수 표는 ref 115 §2(증거)가 보유하고, 그 수리는 계약 D-NAO-280 P2·P3 체크박스가 산다. 여기 다시 실으면 등록부가 셋이 된다 — 이 표가 막으려는 병 그 자체다.

| # | 무엇 | 단계 | 상태 | 이상 시 손 | 비고 |
|---|---|---|---|---|---|
| 1 | 죽은 승인 카드 카운터 — 콘솔은 오히려 «실행 가능» 오표시 📄 | 표면 | 없음 | **있음** — 콘솔 반려까지만. 실행되게 하는 손은 배포 판단(§0-2-C) | 잔량은 §0-4 · SQL은 §14 |
| 2 | pending 만료(expired) 알림 — promote 300건 무음 만료 전례 📄 | 표면 | 없음 | **없음** — 승격 executor 자체가 없다(«생성» writer 부재 📄 ref 115 §6-7) | |
| 3 | prod↔origin/main 배포 드리프트 상시 감시 — CAS는 배포 순간만 | 눈 | 없음 | **있음** — `scripts/safe_deploy.sh` 재배포. 단 타 트랙 마이그 봉쇄 시 §0-2-C | |
| 4 | 점화 preflight의 캠페인 타입×레버 정합 경고 — §0-1의 구멍을 못 잡음 | 눈 | 없음 | **있음** 🧠 — optimizer 전환으로 정지 가능. auto_operate 쪽은 직접 UPDATE뿐(§2) | 사람 대조는 §6-A13 |
| 5 | ★「마지막 실집행 이후 N일」 카운터 | 표면 | 없음 | **없음** — N이 늘 때의 처치는 §0-2-A·B(Jino 결정)이지 손이 아니다 | 유일 계상† |
| 6 | Slack 발송 생존 — `backend/app/services/naver_ad/slack_notifier.py`는 webhook 미설정 시 무음 no-op이 정상 경로. prod 설정 여부 [미상] | 눈 | 없음 | **있음** — webhook env 재설정(재시작 필요 📄 ref 115 §4) | |
| 7 | 보정계수 하한 0.827의 근거 창(07-25~08-23) 재검 주기 | 자 | 없음 | **없음** — 하한은 배포 전용 코드 상수 📄(ref 115 §4) | |
| 8 | ★학습 재료 만료 시계(10-28 📄) | 학습 | 없음 | **없음** — 시계를 멈추는 길은 실집행 재개(§0-2-A 결정) 또는 창 상수 변경(배포로만 📄) | 유일 계상† |
| 9 | 무인 발화 자동 판정 — 소수점 대조가 수동 SQL | 눈 | 없음 | **있음** — 수동 재판정 가능(§14 크론 슬롯 대조) | |
| 10 | `backend/app/services/naver_ad/pao_scope_roster.py`의 D-NAO-244 진리표 미준수 📄(ref 109) | 표면 | 꺼짐 — 정본 판정 함수는 prod 호출 0건(§11-3), 화면이 진리표를 자기 재조합 | **있음** 🧠 — 발현 조건 억제(스코프 행 유지·재지정). 코드 수리는 범위 밖 | 현 prod엔 스코프 행이 있어 미발현 |
| 11 | `backend/app/routers/naver_ad.py`의 스코프 PUT이 갱신에도 before_value=None 📄 — 되감기 오염 위험 | 되돌림 | 값이 빔 | **없음** — 이전 값이 원장에 없어 되감을 목표값이 없다 | 미발현 |
| 12 | ★profit_scorecard(목적함수 그 자체)의 API·화면 배선 0건 — diary·Slack만(§1-2) | 표면 | 없음 | **있음** 🧠 — 악화 «처치»(입찰·제외)의 콘솔 승인 경로는 있다. 단 악화를 «보는» 표면이 없어 손이 눈 없이 논다 | 유일 계상† |
| 13 | 관할 밴드 × 캠페인유형 교차 표면 — perf 하니스별로 campaign_type 사용이 갈린다(ref 111) | 표면 | 없음 | **없음** — 하니스 간 정합은 코드 수정(배포)으로만 | |
| 14 | ★캡에 밀린 액셀 카운터 — `backend/app/services/naver_ad/proposal_pipeline.py::_apply_gave_priority`가 방어 클래스를 무조건 선순위 배치 | 표면 | 없음 | **없음** — 선순위는 배포 전용 코드 고정(ref 115 §3-C) | 유일 계상† · 신규 편입(아래) |

† **유일 계상 4건 (행 5·8·12·14)** — 학습 재료 만료 · 마지막 실집행 N일 · profit_scorecard 표면 · 밀린 액셀 카운터. ref 114·115에 각각 중복 등재돼 «삼중 계상»이던 것을 이 표로 모았다(계약 P1-①). 두 ref의 해당 자리는 이제 이 행 번호를 가리키는 참조다 — 값·판정을 그쪽에서 갱신하지 않는다.

**13건 → 14건인 이유**: 14번(밀린 액셀 카운터)은 새 사실이 아니라 **ref 114 고리 4-2·C층과 ref 115 §3-C에 이미 적혀 있던 항목의 편입**이다 — 삼중 계상 4건 중 이 하나만 종전 §12에 빠져 있었다. 그 외 항목의 증감 없음(1~13은 종전 번호 그대로 — 다른 절의 §12-N 참조를 깨지 않기 위해 재번호하지 않는다).

**단계 집계**: 눈 4 · 자 1 · **손 0** · 표면 7 · 되돌림 1 · 학습 1. 손이 0인 것은 손 결손이 없다는 뜻이 아니라 **손 결손의 등록처가 계약 P2·P3 체크박스라는 뜻**이다(위 ★). 상태는 없음 12 · 값이 빔 1 · 꺼짐 1 · 삶 0 · 잡을 것 없음 0.

**판정 보류 2건** (배선 유무 자체가 [미상]이라 상태 분류 불가): ⓐ관할 분리를 총이익 기준으로도 하는지 ⓑ상품 단위 실현 이익 도달 여부.

## §13. 스코프 밖이지만 의존하는 것

상품 원가·매핑(product-connection-map 트랙) · 주문 수집(프록시 매출·보정계수 분모) · Wing 매출 정합 · 타 트랙 마이그레이션이 배포를 막는 구조(§0-2-C) · Mac 페처·IP 허용목록 · 쿠팡 계열 크론(§0-4 — 기록만).

## §14. 재는 법 (숫자가 낡으면 여기부터)

prod 조회는 스크립트를 파일로 만들어 `scp` 후 stdin 실행한다(인라인 heredoc은 따옴표가 벗겨져 SQL이 깨진다). 스크립트 첫 줄에 `load_dotenv("/home/ubuntu/ohisell/backend/.env")`. 서버는 UTC.

```bash
ssh -o BatchMode=yes sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && .venv/bin/python - < /tmp/q.py"
```

| 무엇 | 어떻게 |
|---|---|
| 엔진의 손 범위 | `SELECT campaign_id, optimizer, auto_operate FROM naver_campaign_settings` + `SELECT * FROM naver_adgroup_scope` (★naver_campaign_settings는 소수 행짜리 override 테이블 — 전체 캠페인 목록은 naver_entity_snapshot) |
| 크론이 돌았나 | `SELECT job_name, last_run_at, last_status FROM scheduler_state` — ★무인 판정 = `scheduler_state.last_run_at`이 크론 슬롯과 **소수점까지** 일치 |
| 우리 엔진 실쓰기 | `naver_change_log`에서 action NOT IN ('flight_pacing') ∧ dry_run=0 — external_*는 대행사 |
| 죽은 카드 | `naver_proposals`에서 status='approved' ∧ `naver_proposals.executed_change_log_id` IS NULL |
| 돈 | `naver_ad_daily`에서 `adgroup_id <> '__backfill__'` (★keyword 필터 걸지 말 것 — 2배 중복 사고 전력. ★창의 시작·끝 날짜를 라벨에 명시 — ref 111 반증 1·2가 「하루 밀림」 한 원인의 두 증상이었다) |
| 부품 생존 | 로컬 `scripts/ignition_parts_alive.py`를 scp로 prod의 /tmp에 올린 뒤, /home/ubuntu/ohisell/backend 에서 **stdin 형식**으로 실행한다(스크립트는 포트 자동탐지·읽기 전용). ★**경로 형식으로 실행하면 마지막 절이 죽는다** — 파이썬이 sys.path에 올리는 것은 cwd가 아니라 «스크립트가 있는 디렉터리»(/tmp)라서 점화 선행 검사가 No module named app으로 실패한다. stdin 형식(python 하이픈 리다이렉트)이나 PYTHONPATH=. 는 8/8 절 전부 성공. ★repo 루트 4KB ohisell.db는 미끼 — prod에도 있다: 반드시 backend/에서 실행 |
| 좌표 생사 | `scripts/check_pao_canon.py`를 python으로 실행 (repo 루트, S3 산출물) |
| 진행률 | TRACK-CONTRACT 블록 안에서만 `grep -c '^[[:space:]]*-[[:space:]]*\[[xX]\]'` / `grep -c '^[[:space:]]*-[[:space:]]*\['` |

⚠️**시간대 혼재 — 컬럼이 아니라 «쓰는 주체»가 정한다** (2026-08-31 재측정으로 옛 서술 반증. 옛 판은 「created_at·changed_at = UTC」라고 단정했는데 **둘 다 틀렸다**):

- 서버·SQLite `datetime('now')`는 **UTC**다. 그래서 컬럼이 `server_default=func.now()`로 채워지면 UTC가 된다.
- 그런데 이 저장소의 writer 대부분은 **kst_now()를 명시로 넘긴다**. 그러면 같은 컬럼에 KST가 들어간다. 근거 주석: `backend/app/services/naver_ad/improvement_events.py`(change_log writer는 사실상 전부 명시) · `backend/app/services/naver_ad/trigger_watch.py`(proposals 중 명시 스탬프하는 거의 유일한 경로) · `backend/app/services/naver_ad/budget_envelope.py`.
- ★**판별 규칙(기계적)**: 값에 **소수점이 있으면 kst_now() 명시 = KST**, **없으면 server_default = UTC**. SQLite CURRENT_TIMESTAMP엔 소수점이 없기 때문이다. 초 단위로 크론 슬롯과 대조할 때 이 규칙을 먼저 적용할 것.
- `scheduler_state.last_run_at` = **KST**(종전과 같음).

<!-- MEASURED -->
2026-08-31 10:36 KST 관측(prod 읽기 전용, 전건 카운트):
| 컬럼 | KST(소수점 O) | UTC(소수점 X) | UTC 쪽의 정체 |
|---|---:|---:|---|
| change_log의 changed_at | 7,786 | **8** | optimizer_change 3 · auto_operate_change 3 · set_user_lock 2 — 전부 2026-08-10 이전 레거시 |
| proposals의 created_at | 5,043 | 2,127 | KST 쪽은 trigger_pacing 5,039 + trigger_cpc_spike 2 + bid_down 2. **그 외 전 타입이 UTC** |
<!-- /MEASURED -->

⇒ **실무 요약**: change_log는 사실상 KST(레거시 8행만 예외), proposals는 **trigger_pacing만 KST이고 나머지는 UTC**다. 한 컬럼을 날짜로 필터하면 두 시간대가 섞인다. 모르면 「미상」이라 적고 추측하지 말 것.

## §15. 이 문서가 다루지 않는 것 / [미상] 목록

- **다루지 않음**: PAO UI/UX 트랙(성과 소관 분리 화면 — 별도 트랙) · 광고 전략(어느 상품에 얼마는 Jino 몫) · [미배선] 14건의 수리 · 값 자동 채움 대시보드(별도 계약).
- **[미상] 주요** (ref 111 §6 전체 목록이 정본): §4 자동/Confirm 표의 검색어 제외 외 10항목 · §5 판정 임계값 다수 · 환경변수 기본값 반대 주장 · CPC ×2 코드 좌표 · SPECS 브레이크7:액셀0 실측 · 카나리 총이익 기준선 −275,787원 · BEP 171.1%와 현행 ROAS의 같은 창 비교 · change_log 756↔776 불일치의 원인 · D-NAO·PR 번호 전수.
- 이 문서의 실측값은 전부 MEASURED 블록 안 스냅샷이다 — 관측 시각에서 몇 시간만 지나도 낡는다. **좌표가 낡으면 검사기가 잡고, 값이 낡으면 §14가 잡는다.**
