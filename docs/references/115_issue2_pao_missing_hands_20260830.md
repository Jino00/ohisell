# 이슈2 — PAO: 기능은 있는데 «손»이 없어 못 쓰는 자리 (전수, 2026-08-30 KST)

> **발단**: Jino 원문(2026-08-30 19:41 KST) — *"이게 업데이트 되게 되면 운영하는데 손이 없어서 실제 운영이 안되는 경우가 없는거야? 있다면 모두 찾아줘. **너는 항상 기능은 만들어놓고 손을 안만들어서 실제 사용이 안돼는 경우가 대부분이야.**"*
> **이름**: **「이슈2」**(잠정 — Jino 확정 대기). **이슈1**(ref 114)과 질문이 다르다: 이슈1 = 「목적 대비 무엇이 **없나**」 · 이슈2 = 「**있는 것을 왜 못 쓰나**」.
> **성격**: 조사 문서 — 코드·prod 무변경. 처분은 Jino 몫.
> **방법**: 읽기 전용 전수 조사(Opus) — `backend/app/services/naver_ad/` · `backend/app/routers/` · `frontend/src/` grep + prod 읽기 조회. 기능 **31개**를 네 손으로 훑음.
> **표기**: 무표기 = 코드를 직접 열거나 실행해 확인 · `📄` 문서·주석 주장 · `🧠` 추론.

---

## §1. 한 줄 답

**기능 31개 중 손이 빠진 자리 24개. 그중 «지금 당장 운영을 막는 것» 7개.** 그 7개는 전부 **「엔진이 만든 값·판정을 사람이 만질 입구가 없어 DB를 직접 UPDATE하거나 배포해야만 굴러가는」** 모양이다.

★**가장 나쁜 하나: 켜는 손도 끄는 손도 없는 `auto_operate`.** prod에서 지금 켜져 있는 캠페인 1개를 **API로 끌 수 없다.**

### 「손」의 네 종류 (판정 축)

| 손 | 무엇 | 없으면 |
|---|---|---|
| **A 사람의 입구** | API 엔드포인트 + 화면 버튼/폼 | DB 직접 UPDATE = 사실상 못 씀 |
| **B 시스템 배선** | 호출부·크론 등록 | 계산은 되는데 아무도 안 읽음 |
| **C 보는 표면** | 화면·알림(**diary·Slack은 로그로 분류**) | 값이 있어도 판단에 못 씀 |
| **D 되돌리는 손** | 취소·복구·재개방 입구 | 못 되돌림 ⇒ **무서워서 못 켬** |

★판정 기준은 「함수가 있는가」가 아니라 **「오늘 실제로 끝까지 실행할 수 있는가」**다.

<!-- MEASURED -->
### 라이브 실측 (2026-08-30 KST 관측) — 판정 근거

| 관측 | 값 |
|---|---|
| `optimizer='ours' ∧ auto_operate=1` | **1캠페인**(`cmp-a001-02-000000008425541`) |
| 그 캠페인의 스코프 행 | **1행** (해당 캠페인 adgroup 스냅샷 **2,720**개 중) |
| 광고 API 마지막 실쓰기 | **2026-07-29 10:59** `update_bid` — 이후 **32일 0건** |
| 승인됐는데 미실행(죽은 카드) | **133건** — 전건 사유 「자동운영 스코프 밖 광고그룹」 |
| `flight_pacing` change_log 14일 | **996행 전건 `dry_run=1`** |
| 제외 원장 `probation`(재개방) 행 | **0건** — 재개방이 한 번도 발화한 적 없음 |
| `search_term_promote` 제안 | pending **280** + expired **320** (executor 없음) |
| 대행사 `external_bid_change` 14일 | **297건** (+ `external_keyword_removed` 133건) |
<!-- /MEASURED -->

---

## §2. 전수 표 (기능 31)

`A`=사람 입구 · `B`=시스템 배선 · `C`=보는 표면 · `D`=되돌리는 손. **굵게 = 손 없음.**

| # | 기능 | A | B | C | D | 좌표 | 지금 어떻게 하나 |
|---|---|---|---|---|---|---|---|
| 1 | **엔진 점화·킬스위치 `auto_operate`** | **없음** | 있음 | 배지 | **없음** | 쓰기 API 0건 · 표시만 `frontend/src/pages/NaverAdScope.tsx` | **직접 SQL UPDATE** |
| 2 | 관리주체 `optimizer` | 있음 | 있음 | 화면 | 있음 | `PUT /api/naver/ad/campaign-settings/optimizer` | 콘솔 |
| 3 | 스코프 지정·해제 | 있음 | 있음 | 화면 | 있음 | `PUT`/`DELETE /api/naver/ad/scope/adgroup` | 콘솔 — **그룹 1개씩만** |
| 4 | **점화 선행검사** | GET만 | 있음 | **없음** | n/a | `ignition_preflight.py::check` | **아무도 안 봄**(프론트 `ignition` 문자열 0건) |
| 5 | 가드레일 SPECS 7키 | 있음 | 있음 | 화면 | 있음 | `PUT /api/naver/ad/settings/guardrail-params` | 콘솔 |
| 6 | **SPECS 밖 상수 438개** | **없음** | 있음 | **없음** | **없음** | §4 | **배포로만** |
| 7 | **`_PARAMS_FROM_DB` 전역 원복** | **없음** | 있음 | 설명문만 | **없음** | `guardrail_params.py::_PARAMS_FROM_DB` | **배포로만** |
| 8 | 제안 승인·거부·실행 | 있음 | 있음 | 화면 | 반려만 | `POST /api/naver/ad/proposals/{id}/status` | 콘솔 |
| 9 | 입찰 상향·하향 | 있음 | 있음 | 화면 | 역제안만 | `naver_sa_writer.py::update_keyword_bid` | 콘솔 (32일째 0건) |
| 10 | 예산 증액·원복 | 있음 | 있음 | 화면 | 크론 | `run_naver_budget_pacing_reset` 00:05 | 자동(라이브) |
| 11 | 정지·재개 | 있음 | 있음 | 화면 | 있음 | `naver_sa_writer.py::set_keyword_lock` | 콘솔 |
| 12 | **검색어 제외 «실행»** | **없음** | 있음 | 화면 | **없음** | `search_term_ss_lane.py::_autofire_exclude` | 자동만 — **사람이 지금 자르는 입구 0** |
| 13 | 제외 «원장 기록» | 있음 | 있음 | 화면 | API만 | `POST /api/naver/ad/search-term/executions` | 네이버 콘솔에서 자르고 여기 기록 |
| 14 | **원장 무효화(void)** | **없음** | 있음 | 화면 | n/a | `DELETE /api/naver/ad/search-term/executions/{id}` | **API는 있는데 버튼 0** |
| 15 | **제외 재개방(복귀)** | **없음** | 게이트 닫힘 | **없음** | n/a | `search_term_ss_lane.py::_run_reexamination` · `_PROBATION_DAYS`=14 | **아무도 못 함** — `probation` 라이브 0행 |
| 16 | **제외 일괄 편입** | **없음** | 있음 | n/a | n/a | `POST /api/naver/ad/search-term/executions/import` | **API만** — curl로만 |
| 17 | 제외 자동 발견 | 있음 | 있음 | 화면 | n/a | `POST /api/naver/ad/search-term/executions/detect` | 콘솔 버튼 |
| 18 | **키워드 승격** | **없음** | **executor 0** | 「자동 만료」 | n/a | `proposal_writer.py::INFORMATIONAL_PROPOSAL_TYPES` | **Jino가 네이버 콘솔에서 수동 등록** |
| 19 | **순위 서보** | **없음** | 있음 | **없음** | **없음** | `rank_servo.py::decide_servo_step` | 자동만 · 파라미터 배포로만 |
| 20 | **콜드스타트 첫 입찰** | **없음** | 라이브 | 로그만 | **없음** | `cold_start_bid_lane.py::run_cold_start_lane` | **on/off = env + 재시작** |
| 21 | **시장가 사다리 수집** | **없음** | 있음 | **없음** | n/a | `market_bid_probe.py::collect_daily` | 자동만 |
| 22 | **탐침 실행** | **없음** | 있음 | 일부 | 자동 | `probe_revert.py::run_settlement` | 자동만 — **사람이 회수하는 입구 0** |
| 23 | **확장 압력 on/off** | **없음** | 있음 | **없음** | **없음** | `expansion_pressure.py::EX_PRESSURE_RATIO` | **배포로만** |
| 24 | **카나리 지정** | **없음** | 있음 | **없음** | **없음** | `auto_operator.py::AD_BID_CANARY_CAMPAIGNS` — **캠페인 id 하드코딩** | **배포로만** |
| 25 | **소재 레인 킬스위치** | **없음** | 있음 | **없음** | **없음** | `auto_operator.py::AD_BID_ROUTING_ENABLED` | **배포로만** |
| 26 | **총이익 스코어카드** | n/a | 크론 08:40 | **일기+Slack만** | n/a | `profit_scorecard.py::run_profit_scorecard` | 화면·API 0 |
| 27 | 총이익(스코프 화면) | n/a | 있음 | 화면 | n/a | `pao_scope_roster.py::_profit` → `NaverAdScope.tsx` | 화면에 있음 (26과 **다른 산출 경로**) |
| 28 | **페이싱 α** | **없음** | **소비처 0** | **없음** | n/a | `flight_loop.py::run_flight_loop` | 로그만 |
| 29 | **탄성 ε** | **없음** | **호출부 0** | **없음** | n/a | `response_curve_builder.py::fit_elasticity` | 항상 `DEFAULT_ELASTICITY`=0.5 |
| 30 | 대행사 되돌림 감지 | n/a | 있음 | 화면 | **대응 손 없음** | `ad_external_change.py::run` | 감지·표시까지 |
| 31 | 지혜 승격·적용 | 있음 | 있음 | 화면 | 항목별 | `wisdom_apply.py::propose_param_changes` | 콘솔 |

---

## §3. 심각도 순 — 「없으면 무슨 일이 벌어지는가」

### 3-A. 켤 수는 있는데 «끌 수 없는» 자리 (최우선)

1. ★**`auto_operate` — 켜는 손도 끄는 손도 없다.** 지금 1캠페인이 켜져 있는데 사고가 나면 API·화면 어디로도 못 끈다. `optimizer='none'`으로 «우회 정지»는 되지만 그건 다른 스위치이고 `auto_operate`는 남는다. 되돌리려면 SSH + SQL. **이 하나가 「무서워서 못 켠다」의 구조적 원인이다.**
2. **소재 입찰 레인 킬스위치가 배포로만.** 주석은 *"False면 즉시 복귀"*라는데 그 「즉시」가 배포 한 사이클.
3. **콜드스타트 실집행 on/off가 env + 프로세스 재시작.** 현재 prod `NAVER_CS_DRY_RUN=0`(라이브).
4. **제외 재개방 입구 0 — 「14일 대기」가 아니라 「영영 안 열림」.** 자동 재심사(`_PROBATION_DAYS`=14)가 유일 경로인데 `auto_operate` 게이트 뒤라 라이브 `probation` **0행**. 원장 3,986행 전부 `excluded`.

### 3-B. 지금 당장 운영을 막는 것

5. ★**스코프 «일괄» 지정 입구가 없다.** 그룹 1개씩 토글만 있어 **2,720 그룹을 열려면 클릭 2,720번**. 실제 스코프 행은 **1행**. ⇒ **죽은 카드 133건의 실질 원인이 이것이다**(사유가 「스코프 밖」인데 스코프를 넣을 손이 없다).
6. **키워드 승격에 executor가 아예 없다.** 600건(pending 280 + 만료 320)이 정보성으로 분류돼 배지만 달고 사라진다. **근본 원인은 알림이 아니라 `naver_sa_writer`에 «생성» 쓰기 함수가 없는 것**(§6-7).
7. **점화 선행검사 렌더 0건.** 「켜기 전에 무엇이 열리는지 물어보라」고 만든 창구를 아무도 안 연다 — 켜는 버튼 자체가 없으니 당연한 귀결(1번과 같은 뿌리).

### 3-C. 조용히 새는 것

8. **페이싱 α가 계산만 되고 아무도 안 읽는다** — 14일 996행 전건 `dry_run=1`. `flight_loop`은 `naver_sa_writer`를 import조차 안 함.
9. **탄성 ε가 영원히 0.5** — 실측 넣을 호출부 0.
10. **총이익 스코어카드가 Slack·일기에만** (단 총이익 «수치» 자체는 스코프 화면에 있음 — 산출 경로가 둘).
11. **원장 무효화 버튼 없음** — API는 사유 필수·감사 보존까지 설계됐는데 화면 호출 0.
12. **캡에 밀린 액셀 카운터 없음** — `proposal_pipeline.py::_apply_gave_priority`가 방어 클래스 무조건 선순위.
13. **「마지막 실집행 이후 N일」 카운터 없음** — 커맨드 센터가 창 기준 「N회」만 보여줘 **32일 침묵이 「이번 주 0회」로만** 읽힌다.
14. **학습 재료 만료 시계 없음** — `wisdom_candidates.py::_HARVEST_LOOKBACK_DAYS`=90 + 마지막 실집행 07-29 ⇒ 🧠 2026-10-27경 창 밖.
15. **대행사 되돌림에 대응 손 없음** — `feed_reapply`는 이름과 달리 **재적용기가 아니라 «피드 재적용 vs 실조작» 분류기**. 14일 297건이 관측만 되고 있다.

---

## §4. 배포로만 바뀌는 상수 — **438개**

모듈 레벨 숫자·리터럴 상수 **445개**를 AST로 전수. 사람이 바꿀 수 있는 것은 `guardrail_params.py::SPECS` **7개뿐**이고 **그 7개가 전부 브레이크**다(액셀 0).

| 상수 | 값 | 좌표 | 왜 중요한가 |
|---|---|---|---|
| `EX_PRESSURE_RATIO` | 1.25 | `expansion_pressure.py` **+** `expansion_allocator.py` | **2파일 사본** |
| `_SETTLEMENT_WINDOW_START_DAYS`/`_END_DAYS` | 8/2 | `auto_operator.py`·`expansion_allocator.py`·`expansion_pressure.py`·`visibility.py` | ★**4파일 사본** — 한 곳만 고치면 조용히 갈라진다 |
| `EX_MIN_CAMPAIGN_CLK` | 30 | `expansion_pressure.py` | 표본 문턱 |
| `DEFAULT_ELASTICITY` | 0.5 | `response_curve_builder.py` | 곡선의 핵심 가정 |
| `AD_BID_CANARY_CAMPAIGNS` | frozenset 1개 | `auto_operator.py` | 카나리가 **id 하드코딩** |
| `AD_BID_ROUTING_ENABLED` | True | `auto_operator.py` | 레인 킬스위치 |
| `_PARAMS_FROM_DB` | True | `guardrail_params.py` | 봉투 전역 원복 |
| `_MAX_CHANGE_PCT`/`_EXPLORATION_MAX_CHANGE_PCT` | 0.15/0.30 | `guardrail_gate.py` | **SPECS에서 일부러 뺀 값**(생성기 7곳이 각자 재현) |
| `EXCLUSION_SLOT_CAP` | 70 | `exclusion_slot_usage.py` | 제외 칸 상한 |
| `_PROBATION_DAYS` | 14 | `search_term_ss_lane.py` | 재개방 관찰창 |
| `_HARVEST_LOOKBACK_DAYS` | 90 | `wisdom_candidates.py` | 만료 시계의 뿌리 |
| `CORRECTION_FACTOR_FLOOR` | 0.827 | `correction_interval.py` | 총이익 보정계수 |
| `_SERVO_DEADBAND`/`_SERVO_DEFAULT_STEP_PCT`/`_SERVO_MAX_STEP_PCT` | 0.3/0.15/0.50 | `rank_servo.py` | 서보 전량 |
| `ALPHA_MIN`/`ALPHA_MAX` | 0.5/1.5 | `pacing_controller.py` | α 클램프 |
| `EX_DAILY_GROUP_CAP`/`EX_HIGH_DEMAND_IMP`/`EX_MARGINAL_STOP_RATIO` | 5/100/1.1 | `expansion_allocator.py` | 확장 봉투 |

**env 스위치는 3개뿐**(재시작 필요): `NAVER_CS_DRY_RUN`(prod=0, 라이브) · `NAVER_BP_DRY_RUN`(미설정=라이브) · `NAVER_SLACK_WEBHOOK_URL`(설정됨).

**이슈1이 해결된 뒤 필요해질 손 — 전건 없음**: 정착창 길이를 바꾸는 손 **없음**(4파일 배포) · 탄성 ε를 실측으로 갈아끼우는 손 **없음**(호출부 0) · 총이익을 게이트에 넣었을 때 사람이 근거를 확인·승인하는 입구 **없음**(`profit_scorecard`가 API·화면에 안 닿으므로).

---

## §5. ★이전 진술의 정정 2건

| # | 이전 진술 | 판정 |
|---|---|---|
| 1 | 「`profit_scorecard`가 API·화면 어디에도 안 닿는다」 | **부정확** — 그 모듈의 일일 스코어카드는 맞으나 **총이익 «수치» 자체는 스코프 화면에 있다**(`pao_scope_roster.py::_profit` → `NaverAdScope.tsx`). 산출 경로가 둘이다. |
| 2 | 「죽은 카드 수리(`bd8e7572`)가 **prod 미배포**」 📄 | ★**반증** — prod에 있다(`auto_operator.py` Aug 30 02:40). `real_write_blocker`가 133건 전건에 사유를 반환하고, **신규 발생도 08-30 02시(UTC)에 멈췄다.** 인계 문서의 주장을 실측 없이 반복한 것이 오류였다. |

★**②는 이 세션이 반복해 밟은 병의 또 한 사례다** — 「인계 목록은 실측 전엔 못 믿는다」.

---

## §6. 새로 찾은 것 (사전 시드에 없던 것)

1. **점화 선행검사가 화면에 렌더 0건** — 프론트 전체 `ignition` 문자열 **0건**.
2. **원장 무효화(void) 버튼 없음** — API는 완비.
3. **제외 일괄 편입 API가 화면에 없음** — `POST …/executions/import`(200건 상한) 프론트 호출 0.
4. **프론트가 호출하지 않는 GET 엔드포인트 5개** — preflight · exclusion-slots · exclusions · bm/agency-ops · bm/snapshot·benchmark. (제외 칸 소진은 전역 헬스 배너로 우회 노출됨.)
5. **정착창 상수가 4파일 사본, `EX_PRESSURE_RATIO`가 2파일 사본.**
6. **`feed_reapply`는 재적용기가 아니라 분류기** — 대행사 되돌림 «대응» 손은 이름과 달리 없다.
7. ★**소재·광고그룹·키워드 «생성» writer 함수가 아예 없다.** `naver_sa_writer`의 쓰기는 **9종뿐**(제외 add/remove · 입찰 3 · 락 3 · 예산). **승격이 실행 불가인 근본 이유가 이것이다.**
8. ★**스코프 «일괄» 지정 입구 없음** — 2,720 그룹을 클릭 2,720번. **죽은 카드 133건의 실질 원인.**
9. **`optimizer='ours'`로 바꿔도 원본 MOP를 우리가 못 끈다**(별도 SaaS) — 두 옵티마이저 충돌을 막는 손이 우리 쪽엔 없고, 충돌은 `external_bid_change` 감지로 **사후에만** 드러난다(14일 297건).

---

## §7. 확인 못 한 것

- **프론트 prod 배포 상태** — 백엔드 `bd8e7572`가 prod에 있는 것은 확인했으나 `frontend/dist`의 `.deploy-stamp` 대조는 안 했다. 「콘솔이 사유를 제대로 보여준다」는 **백엔드 응답 기준** 판정이다.
- **왜 실집행이 07-29에 멈췄는지의 근본 원인** — 133건은 「스코프 밖」으로 설명되지만 07-29~08-28 침묵이 같은 원인인지는 안 팠다(스코프 밖 표본은 08-29부터).
- **Slack이 실제로 도착하는지** — webhook 설정만 확인, 전송 성공 여부 미조회.
- **`rank_servo`·`market_bid_probe`·`exploration` 레인의 라이브 산출 건수** — 배선·크론 등록만 확인.
- **`auto_operate`를 켠 주체·시각** — `auto_operate_change` action 상수는 models.py에 있으나 라이브 14일 집계에 해당 행 없음. 직접 UPDATE라 흔적이 안 남은 것인지 창 밖인지 미분리.
- **다른 트랙(1P·쿠팡)의 손** — 이 조사는 PAO(네이버 SA) 한정.

---

## §8. 처분 (미정 — Jino 몫)

- **이슈1(ref 114)과의 관계**: 이슈1 = 「목적 대비 무엇이 없나」(배선·표면 층) · **이슈2 = 「있는 것을 왜 못 쓰나」(손 층)**. 이슈1을 다 고쳐도 **이슈2가 남으면 여전히 운영이 안 된다.**
- **가장 싼 것부터 하면**: ①`auto_operate` 쓰기 API + 화면 버튼 ②스코프 일괄 지정 ③점화 preflight 렌더 — 셋 다 기존 배선에 «입구»만 붙이는 일이다 🧠.
- **가장 큰 것**: 승격 executor(=`naver_sa_writer`에 «생성» 함수 신설) · 배포 전용 상수 438개 중 운영 판단이 걸린 것들의 SPECS 편입.
- 전부 **새 계약** 사안이다(북극성 §8-③: 계약 1장 + Jino 승인).

**다음 가용 번호**: D-NAO-280 · 교훈 #380
