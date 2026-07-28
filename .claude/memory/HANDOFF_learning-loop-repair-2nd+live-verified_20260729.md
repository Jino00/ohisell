# 세션 인수인계: 학습 배관 수리 2차 + 라이브 검증 (2026-07-29 새벽)

> 저장일시: 2026-07-29 05:30 KST
> 세션 모델: Opus. 트랙: `docs/tracks/active/track_naver-ad-optimization.md` (D-NAO-111)
> 전 세션: `.claude/memory/HANDOFF_learning-loop-repair+z8-takeover_20260728.md` (D-NAO-106~110)

---

## 0. 가장 먼저 알아야 할 것 (놓치면 안 되는 것)

1. **PR #161이 아직 미병합이다.** 워크트리 `.claude/worktrees/learning-loop-repair`, 브랜치 `claude/learning-loop-repair`, HEAD `9b6df55`(main 병합 포함). 병합 판단은 Jino 몫.
2. **prod 배포는 2회 완료됐지만, 미배포 항목이 2개 남아 있다** — `scheduler_service.py`(CAS 거부, 다른 세션 소유 추정)와 `dd73a59`(conv_delay 곡선 퇴화 미해소로 의도적 보류). "고쳤다"가 "전부 배포됐다"를 뜻하지 않는다.
3. **`scheduler_service.py`의 CAS 거부는 원인 미파악 상태로 남아 있다.** prod의 현재 내용이 우리 브랜치·main·로컬 어느 브랜치 역사에도 없다 — 다른 세션이 배포했는데 아직 PR을 안 낸 것으로 추정된다. 다음 세션이 그 세션의 정체와 변경 내용을 파악해야 한다.
4. **적대적 리뷰는 Jino 지시로 이번 세션은 2회로 캡됐다** ("적대적리뷰는 2번만 해"). 1건은 중단(대상이 466bbc5), 1건은 NEEDS-CHANGES 판정 후 지적 6건 전량 반영 완료. 중단된 리뷰의 몫은 메인 루프가 직접 코드를 재확인해 대체했다(§4 참조).
5. **아침 관측 3건은 별도 에이전트가 관측 중이며 이 HANDOFF 작성 시점엔 결과가 없다** — 07:45 매핑 적재 / 08:10 `bid_rank_slope` 첫 적립 / 08:50 갤럭시 68그룹 손실 브레이크. 다음 세션이 결과를 확인해야 한다.

---

## 1. 이번 세션에서 실제로 한 일

### 워크트리·PR
- `.claude/worktrees/learning-loop-repair`, 브랜치 `claude/learning-loop-repair`, HEAD `9b6df55`(main 병합 포함).
- **PR #161** 생성됨(미병합) — 제목: "학습 배관 수리 2차: flight_loop 무성 실패·탐침 하한 학습화 (D-NAO-111)".

### 커밋 5건 (전 세션의 `466bbc5`·`dd73a59` 위에 쌓임)
| 커밋 | 내용 |
|---|---|
| `c58f430` | flight_loop 무성 실패 관측성 + 대상 정의 불일치 해소 |
| `223f138` | 탐침 하한을 학습밴드가 이기게(하드코딩 2.5 충돌 해소) |
| `e2ecd29` | 정착 보정 주석의 표적성 단정 철회(동작 무변경) |
| `72b0628` | forecast_pending ≠ forecast_missing |
| `09f7943` | 적대적 리뷰 지적 6건 반영 |
| `9b6df55` | main 병합(문서 충돌 2건은 main 채택) |

### prod 배포 완료(`scripts/safe_deploy.sh`, CAS 통과)
- **2026-07-28 23:48** — `flight_loop.py` + `auto_operator.py` (pm2 재시작 276)
- **2026-07-29 05:28** — `bid_rank_curve.py` + `bid_step_types.py` (pm2 재시작 277)

### 미배포 2건
- ❌ **`scheduler_service.py`** — CAS 거부. prod 내용(blob `642619658baf…`)이 우리 브랜치·main·로컬 어느 브랜치에도 없다 = **다른 세션이 배포하고 아직 PR을 안 냈다.** 우리 변경은 로그 한 줄뿐이라 분리했다. 그 세션의 변경 정체 파악은 미완 — 다음 세션 과제.
- ❌ **`dd73a59`(conversion_maturity.py·bid_ceiling_calculator.py)** — 의도적 보류 유지(§3 곡선 퇴화 기전 참조).

### 테스트
- 3,994 passed (신규 회귀 13건).

### 적대적 리뷰
Jino "적대적리뷰는 2번만 해"로 캡. 2건 띄웠고 1건은 중단됨(466bbc5 대상), 1건은 완료 후 **NEEDS-CHANGES** 판정 → 지적 6건 전량 반영(`09f7943`). 중단된 466bbc5 리뷰 몫은 메인 루프에서 직접 방어코드를 확인해 대체했다(§4 근거 참조).

---

## 2. ★라이브 검증 결과 (원칙22 — 전부 실제 확인됨)

- **인수 후 첫 시간당 레인(07-28 23:20)**: 갤럭시 캠페인에서 `update_bid` 9건 나갔으나 **실제 입찰 변경 0건** — 9건 전부 `[실행 불가] 가드레일 차단`(8건 일예산 상한, 1건 BEP 미달). 코드상 `[실행 불가]`는 "사전 가드 거부, writer를 부르지도 않았다"(`naver_execution_harness.py:208`).
- **00:05 BP 원복 첫 실전 성공**: `run_naver_budget_pacing_reset` 사상 첫 실행. 03 캠페인 `dailyBudget 52,700 → 50,000`, `dry_run=0`(실집행), 사유 "[예산페이싱복원] 익일 원복 — 2026-07-28 21:20 BP 증액분을 base 50000원으로 복귀".
- **00:15/02:15/04:15 flight_loop 신버전**: 로그 원문 `flight_loop: 6캠페인 처리 — 결정 0, 스킵 6 {'forecast_pending': 5, 'campaign_off': 1}, 오류 0 (dry_run=True)` — **정정된 예측과 정확히 일치**. 진단 행(`flight_pacing_silent`) **0건**(pending을 병리로 안 셈).
- **auto_operator 신버전**: 05:20까지 매시간 `ok`, 예외 없음.
- **아직 확인 안 됨(별도 에이전트가 관측 중)**: 07:45 매핑 적재 / 08:10 `bid_rank_slope` 첫 적립 / 08:50 일 레인의 갤럭시 68그룹 손실 브레이크 + 신형식 경보.

---

## 3. ★새로 표면화된 구조적 사실 (다음 세션이 알아야 할 것)

- **학습밴드 스코프 불일치 — ★해소·배포 완료(2026-07-29 06:38 KST)**: 09:03 학습 잡 `run_probe_learning(db)`은 `campaign_id`를 안 넘겨 **계정 전체** 집계를 승격·일기에 기록하는데, 실제 게이트는 **캠페인별**로 재계산한다. 오늘 계정 전체는 `1.0-2.0`이 승격돼 있지만 **ours 6개 중 그 밴드를 소비하는 캠페인은 0개**였다. → `probe_learning_loop.gate_bands()`로 기록 정정(캠페인별 실제 적용값 라벨링) + `_account_band_fallback_ok`로 **BEP 확인되는 유닛에만** 계정 밴드 폴백 허용. Jino 원문 *"2.0~2.5로 올린다고 해도 bep 보다 손실이면 알아서 입찰가를 내릴거 아니야?"* → 사전 상한(`affordable_ceiling`)+사후 고삐(`_intraday_loss_leash`) 이중 방어 확인 후 *"그러자"*로 승인. 브랜치 `claude/learned-band-scope` 커밋 `530a88b`, PR #162, 4,001 passed, prod 배포 완료(`auto_operator.py`·`probe_learning_loop.py`, pm2 재시작 278). 트랙 D-NAO-117.
- **conv_delay 곡선 퇴화 기전 확정**: `conversion_maturity.compute_curve:109`가 코호트별 비율의 **단순 평균**이라, 성숙 코호트 7개 중 4개가 비율 1.0·3개가 0.0이면 평균이 정확히 4/7=0.5714로 고정된다. day 8~18 11칸 동일값의 정체가 이것이고, `dd73a59`의 최댓값 1.75배가 바로 이 구간에서 나온다. → 배포 보류 유지. 선결: 퇴화 해소 → 배수 분포 재측정 → Jino 배포 판단.
- **인수 유지 조건 ① 자동 해결 예정**: `shopping_ad_product_sync`(매일 07:45)가 `optimizer='ours'` 캠페인만 훑는데 갤럭시 캠페인은 `campaign_type=SHOPPING`이고 22:36에 ours가 됐다 → 07:45에 68그룹 매핑 자동 적재 예상, 그것이 08:50 일 레인보다 앞선다. 원가·BEP(6,090 / 2.0365)는 이미 등록돼 있어 **사람 입력 불필요**.
- **`466bbc5` 배포 근거(리뷰 대체 자체 확인)**: `_fit_slope`의 유효쌍 정의가 `개선폭>0 ∧ Δbid>0` 한쪽 사분면이라 slope는 **구조적으로 양수**, 유효쌍 3개 미만이면 `None`(콜드스타트 강제), 추정량은 평균이 아닌 **중앙값**. NULL이면 `load_response_priors`가 안 읽어 종전 폴백 유지. slope "존재"만으로 열리는 유일한 게이트(확장 deep 예외)는 **4조건 AND**라 나머지 3개(`clk≥10`·`보정ROAS/BEP ≥ 1.25`·marginal_stop 아님)가 독립적으로 살아 있다.

---

## 4. ★이 세션에서 Claude가 틀렸다가 정정한 것 (반복 방지 — 반드시 읽을 것)

| # | 틀린 주장 | 실제 |
|---|---|---|
| 1 | "00:15에 decided=1이 나온다" | **틀림.** 예측 생성 배치는 07:50이고 flight_loop은 `*:15` 2시간 주기라 **00·02·04·06시 4회는 구조적으로 배치보다 앞선다.** 그대로 뒀으면 "정상일 때 0행"이라던 진단 행이 건강한 시스템에서도 매일 4개 쌓여 경보가 배경소음이 됐을 것 → `forecast_pending`/`forecast_missing` 분리(`72b0628`). |
| 2 | `forecast_ready`를 데이터 프록시(예측 행 존재)로 판정 | **리뷰가 반증.** `forecast_model_builder`는 게이트가 active가 아니면 예측 행을 **아예 안 쓴다** → ours 전원 강등된 날엔 배치가 정상 완주해도 0행 → 프록시가 "정상"으로 읽어 **하루 종일 침묵.** 그게 이 작업이 고치려던 무성 실패의 총체판이었다 → 판정 술어를 `scheduler_state.last_run_at`(사실)로 교체(`09f7943`). |
| 3 | "학습밴드 충돌 해소로 CD5가 되살아난다" | **아니다.** floor==band_high라 탐침 발동과 CD5 생략이 정확한 여집합이 되어 CD5는 여전히 발동하지 않는다. 달성된 것은 "탐침이 학습 최적점에서 멈춘다"뿐. 코드로 되살리려면 floor를 낮춰야 하는데 그러면 최적점을 **넘어서** 올라가므로 방향이 틀렸다. |
| 4 | "하한이 느슨해지지 않는다" | 값 이야기지 노출 이야기가 아니다. 하한이 내려가면 **탐침 발동 집합은 엄격히 커진다**(입찰 상향 제안 증가 = 라이브 광고비 방향). |
| 5 | 전 세션의 "dry_run 해제는 위험하다" | **틀렸다.** `flight_loop`엔 입찰을 바꾸는 코드가 **아예 없다**(writer import 0건, 호출자는 스케줄러 1개·기본값). docstring이 약속한 실행 경로는 구현된 적이 없다. **해제 대상이 없다 — 위험해서가 아니라 연결된 게 없어서.** |
| 6 | "`bid_rank_slope` 테이블이 prod에 없다"(정찰 보고) | **오탐.** 전용 테이블이 아니라 `naver_learning_state`의 metric 행이다. 마이그레이션 불필요. |
| 7 | 커밋 1건에 3개 변경을 섞어 메시지와 내용이 어긋남 | 즉시 reset 후 3개로 분리. |

---

## 5. ★기록 사고 복원 (main 커밋 `056afcf`)

`0e92e38`(07-28 19:51)이 Jino 지시 *"codex는 건너뛰어줘"*(19:49)를 **D-NAO-108**로 기록했는데, 3시간 뒤 다른 세션의 `1a6a260`이 같은 번호를 갤럭시 인수에 재사용하면서 **그 결정이 트랙에서 소실**됐다. 트랙은 Jino가 면제한 codex 부채 5건을 "08-02에 일괄 재실행"하라고 계속 지시하고 있었다. → **D-NAO-110**으로 복원(원 결정 시각 19:49 유지) + 244·598행 정정. 교훈은 LESSONS #61.

### LESSONS 추가 (main 커밋 `837be34`)
- **#61** D-NAO 채번은 트랙 파일이 아니라 `git log`를 봐야 한다 — 병행 세션의 선점이 앞선 결정을 지운다.
- **#62** 스위치의 존재를 기능의 존재로 착각하지 말 것("dry_run 해제가 위험하다"가 틀렸던 이유).

---

## 6. 다음 세션 할 일 (우선순위)

1. **아침 관측 3건 결과 확인**(별도 에이전트 관측 중): 07:45 매핑 적재 / 08:10 `bid_rank_slope` 첫 적립 / 08:50 갤럭시 68그룹 손실 브레이크 + 신형식 경보.
2. **PR #161 병합 판단**(Jino).
3. **`scheduler_service.py` CAS 거부 해소** — prod 버전을 배포한 세션 찾기, 로그 한 줄 변경 반영.
4. **Jino 판단 대기 2건** (학습밴드 스코프 불일치는 2026-07-29 06:38 D-NAO-117로 해소·배포 완료 — 목록에서 제거):
   - ① `dd73a59` 배포(곡선 퇴화 해소 후, §3).
   - ② `flight_loop` 실행 경로를 만들 것인가(dry_run 해제의 진짜 질문 — §4-#5).
5. **D-NAO-109 수명주기 국면 판정 구조(SA1~SA3)는 여전히 ★미승인** — 착수 전 Jino 승인 필요. 학습 예산 = "오늘과 같게"(Jino 22:53).

---

## 7. 핵심 파일

| 파일 | 역할 |
|---|---|
| `docs/tracks/active/track_naver-ad-optimization.md` | D-NAO-111 갱신(이번 세션 배포·라이브 검증·리뷰 반영 추가) |
| `.claude/worktrees/learning-loop-repair` | 이번 세션 작업 워크트리, 브랜치 `claude/learning-loop-repair` |
| `backend/app/services/naver_ad/flight_loop.py` | 관측성 수정(c58f430)·prod 배포 완료 |
| `backend/app/services/naver_ad/auto_operator.py` | 탐침 하한 학습화(223f138)·prod 배포 완료 |
| `backend/app/services/naver_ad/bid_rank_curve.py` | prod 배포 완료(05:28) |
| `backend/app/services/naver_ad/bid_step_types.py` | prod 배포 완료(05:28) |
| `backend/app/services/naver_ad/conversion_maturity.py` | 정착 보정 주석 철회(e2ecd29) — dd73a59는 미배포 |
| `backend/app/services/naver_ad/bid_ceiling_calculator.py` | dd73a59 소속, 미배포(곡선 퇴화 해소 대기) |
| `backend/app/services/naver_ad/scheduler_service.py` | CAS 거부로 미배포 — 다음 세션 조사 대상 |

---

## 8. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_learning-loop-repair-2nd+live-verified_20260729.md 읽고 이어서 작업해줘.
★먼저 §0(놓치면 안 되는 것)과 §4(내가 틀렸다 정정한 7건)을 읽을 것.

【현재 상태】
· PR #161 미병합 (브랜치 claude/learning-loop-repair, HEAD 9b6df55).
· prod 배포 2회 완료(23:48 flight_loop+auto_operator, 05:28 bid_rank_curve+bid_step_types).
· 미배포 2건: scheduler_service.py(CAS 거부, 원인 미파악) / dd73a59(conv_delay 곡선 퇴화 미해소로 보류).
· 라이브 검증 3건 전부 성공(BP 원복 실전·flight_loop 신버전 정확 스킵·auto_operator 무예외).

【순서】
1. 아침 관측 3건 결과 확인 (07:45 매핑 적재 / 08:10 bid_rank_slope 첫 적립 / 08:50 갤럭시 손실 브레이크)
2. scheduler_service.py CAS 거부 원인 파악 — prod 배포한 세션 특정
3. PR #161 병합 여부는 Jino 판단
4. Jino 판단 대기 3건(학습밴드 스코프·dd73a59 배포·flight_loop 실행 경로) 확인
```
