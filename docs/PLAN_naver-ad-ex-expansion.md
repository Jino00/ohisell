# PLAN — EX 확장 압력 스프린트 (D-NAO-85 + ref39 확장 스코프)

- 작성: 2026-07-23, 워크트리 `ex-expansion-sprint-d-nao-85-707081`
- 설계: Fable / 구현: Opus(핵심)·Sonnet(단순) / 각 Phase codex review 통과 후 다음 진행(원칙 19)

## §0. 방향 고정 (이 스프린트를 건드리는 모든 세션 필독)

**목적함수(D-NAO-59)**: 총이익 절대액 최대화. ROAS 최대화 아님. 한계 ROAS ≥ BEP 구간에서는 볼륨 확장.
**이 스프린트가 놓는 것**: 실행층의 "액셀" — 캠페인 보정 ROAS가 BEP를 크게 웃돌면 한계 ROAS가 BEP에
수렴할 때까지 볼륨을 밀어 올리는 **상설 확장 압력 메커니즘**(D-NAO-85). 발단 = Jino 질책 원문:
"RoAS 7% 올리면서 매출이 52%가 줄어들었어… 매출을 극대화하자는 전제는 어떻게 없어져버린거야?"

**금지선(변경 시 Jino 승인 필요)**:
- Q2 OG(자율 개선 루프)는 보류 — **재제안 금지**(Jino가 먼저 꺼낼 때만).
- KX(D-NAO-88)·P5(브레이크 대칭화)는 스코프 밖(EX 뒤 별도 스프린트).
- DOWN/브레이크 계열(스톱로스·손실고삐·쿨다운·`below` 거부권)은 이 스프린트에서 건드리지 않는다.
- 기존 가드레일(BEP 하한·스텝 클램프 15%/30%·쿨다운 2h·일일 상한·budget +100%/회·라운드 캡 10만)은
  전부 존속 — EX는 가드레일 **안에서** 미는 압력이지 가드레일 해제가 아니다.
- 실쓰기는 auto_operate=True 캠페인만. 대행사 캠페인 = 관찰 전용 유지.

## §1. 5갈래 스코프 (ref39 반영 확정)

| # | 갈래 | 근원 | 규모 |
|---|------|------|------|
| 1 | P7 일일 이익 스코어카드 | ref39 P7 (우선순위 1) | 반나절 |
| 2 | EX 본체 (캠페인 압력판정 → 그룹 배분 → 정지) | D-NAO-85 | 핵심 |
| 3 | P4 밴드 동적화 (2.5 상단 → 한계ROAS≥BEP 동적 경계) | ref39 P4 재심 지정 | EX에 흡수 |
| 4 | P3 UP게이트 프라이어 폴백 (unknown→캠페인 프라이어) | ref39 P3 | 중간 |
| 5 | D-NAO-87 예산 봉투 (max(30일 일평균×1.5, 5만)) | ref39 P6 → D-NAO-87 확정 | 중간 |

## §2. 코드 실측 근거 (2026-07-23 세션 실측 — "있으니 된다" 금지 원칙 이행)

- **GAVE 배선**: 1차(retro_scorer.py:124→NaverRetroSignal.gave_score_d3/d7, 08:30 크론) 라이브 확인.
  2차(flight_loop α) 미배선 확인 — 참조 0건, α=min(αB 예산, αC 목표ROAS) 순수 이분법. 제3 배선
  발견: proposal_pipeline._apply_gave_priority(:739)가 성장 제안을 GAVE 내림차순 재정렬(라이브).
  → EX는 `gave_score.compute_gave_score`의 `roas_ratio`(=ROAS/BEP)를 캠페인 갭 판정에 재사용.
- **UP 게이트 3상**: `auto_operator._settlement_roas_status` = ok/below/unknown 3상 이미 존재
  (:303). P3 폴백은 unknown에만 적용, below(명시적 미달)는 거부권 유지.
- **예산 경로**: `naver_sa_writer.update_campaign_budget`(:464, 검증 재조회 포함) +
  `guardrail_gate._check_budget`(+100%캡·스톱로스·BEP) + harness `budget_up→update_budget`(:160)
  전부 존재. 현재 budget_up은 Jino 콘솔 Confirm 전용 — D-NAO-87이 auto_operate 한정 자동화 개방.
- **밴드 정적 상한 위치**: `exploration.py` `_EXPLORATION_BAND_LOW=2.5`(과열 경계, 진입 금지) —
  P4의 대상. `rank_servo`는 데드밴드(0.3)만 있고 밴드 상한 없음.
- **한계 반응 재료**: `bid_rank_curve.py` bid_rank_slope(30일 관측, 오염 필터, `load_response_priors`)
  — EX 정지 조건과 P4 동적 경계의 공통 재료.
- **수축 재료**: `hierarchical_pooling.shrink(n, raw, prior, k=10)` — P3 폴백·그룹 배분 판정 공용.

## §3. 아키텍처 (Agent/Harness/SA — 원칙 18)

```
[EX 확장 압력 레인]  (일 1회, 08:00 제안 생성 시각에 편입)
  Harness: proposal_pipeline.run_daily (기존) ← EX 단계 추가
    ├── SA expansion_pressure.py (신규, 순수)
    │     캠페인 단위 압력 판정: 정착창(D-8~D-2) 캠페인 보정ROAS vs BEP
    │     → roas_ratio ≥ EX_PRESSURE_RATIO ∧ 표본 게이트 → 확장 모드 + 여력 산출
    ├── SA expansion_allocator.py (신규, 순수)
    │     그룹 단위 배분: ①밴드내+순위여유 ②고수요·밴드밖 ③증거창(VF) 순 랭킹
    │     그룹 자기 표본(clk≥10)=자기 ROAS, 미달=캠페인 프라이어(shrink)
    │     정지: bid_rank_slope 기반 한계 ROAS ≤ BEP 접근 그룹 제외
    │     → 캠페인당 EX_DAILY_GROUP_CAP개 bid_up 제안(rationale [EX확장] 태그)
    └── (기존) proposal_writer.persist → 08:50 일 레인 심사·집행

[P3 프라이어 폴백]  auto_operator._check_bid_up_conditions 조건②③ 확장
[P4 밴드 동적화]   exploration.py 과열 경계에 한계ROAS 분기 추가
[예산 봉투]        SA budget_envelope.py (신규, 순수) + 일 레인 budget_up 자동 심사 추가
[P7 스코어카드]    SA profit_scorecard.py (신규, 순수) + 크론 08:40 브리핑(관찰 전용)
```

SA간 직접 호출 금지 유지 — expansion_pressure 출력을 harness(proposal_pipeline)가
expansion_allocator 입력으로 전달. auto_operator는 rationale 태그·approval_source로만 인지.

## §4. 갈래별 상세 설계

### 4-1. P7 일일 이익 스코어카드 (첫 작업, 관찰 전용·실쓰기 0)

- **정의**: 캠페인별 일일 총이익 절대액 = `보정 conv_amt ÷ bep_roas − cost`.
  (bep_roas = campaign_target_resolver의 캠페인 BEP — sp/contribution이므로 conv_amt/bep_roas가
  곧 공헌이익 절대액. 보정 = diagnosis.correction_factor, source≠actual_revenue_ratio면
  무보정 값에 "무보정" 병기 — 스코어카드는 관찰이라 fail-open, 단 표기는 정직하게.)
- **표면**: ①어제 일일 이익(캠페인별+합계) ②7일 이동평균 ③6월 일평균 baseline 대비 %.
  auto_operate 캠페인 + ours 전체(03/04/17프로/P_Test). 광고비만이 아니라 이익이 주인공.
- **전달**: Slack(slack_notifier.notify_text) + diary observe(action="profit_scorecard") —
  기존 브리핑 관례 그대로. 크론 `run_naver_profit_scorecard` 08:40 (retro 08:30 뒤, 일 레인 08:50 앞).
- **파일**: `profit_scorecard.py`(순수 SA) + scheduler job + 테스트.

### 4-2. EX 본체

**압력 판정 (캠페인 단위, expansion_pressure.py)**
- 입력: 캠페인 정착창(D-8~D-2) 집계(clk·cost·conv_amt), 보정계수, bep_roas, 당일 예산 소진.
- 게이트(전부 충족 시 확장 모드):
  - ①표본: 정착창 캠페인 clk ≥ EX_MIN_CAMPAIGN_CLK(=30) ∧ cost > 0 — 캠페인 질량 근거.
  - ②보정: correction_factor source == actual_revenue_ratio (fail-closed, 기존 관례).
  - ③갭: roas_ratio = 보정ROAS ÷ bep_roas ≥ EX_PRESSURE_RATIO(=1.25 초기값).
    (근거: target_roas=BEP×1.15(표준)이므로 1.25 = 목표 초과 + 여유. 03 실측 2.57/1.55=1.66은
    깊은 확장 구간. 캘리브레이션 상수 — 라이브 관측 후 조정.)
- 출력: {campaign_id, expansion_mode, roas_ratio, headroom_note} — 판정만, 실행 없음.

**배분 (그룹 단위, expansion_allocator.py)**
- 후보 = 확장 모드 캠페인의 활성(on) 그룹/키워드.
- 랭킹(우선순위 3층, D-NAO-85 확정 순서):
  1. 밴드 내 + 순위 여유: weighted_rank ∈ (2.5, 4.0] — 2.5 방향 이동 여지.
  2. 고수요·밴드 밖: 7일 imp ≥ EX_HIGH_DEMAND_IMP(=100, VF 가시 임계 재사용) ∧ rank > 4.0.
  3. 증거창(VF) 그룹: visibility.py 증거창 활성 그룹 순.
- 그룹 판정: 자기 clk ≥ 10 → 자기 정착창 ROAS ≥ bep면 채택. 표본 미달 → 캠페인 프라이어로
  채택(수축 철학 — 자기 증거로 반박(below)되지 않는 한 캠페인의 여유를 상속).
- **정지 조건(D-NAO-59 꼭짓점)**: bid_rank_slope 프라이어가 있는 그룹은
  한계 ROAS 근사 = 그룹 RPC×ΔCTR수익 대신 **보수 근사**로 "현재 CPC 상승률 대비 클릭 증가율"
  (slope 기반)이 BEP 유지선을 하회하면 제외. slope 프라이어 없으면 정지 판정 불가 →
  배제하지 않되(관측이 목적) 스텝은 기존 래더 클램프에 맡김.
- 출력: 캠페인당 상위 EX_DAILY_GROUP_CAP(=5, vitality 관례 미러) 그룹 bid_up 제안 소재
  (target_bid = 기존 스텝 규칙 재사용: +15% 클램프 내, 10원 단위).
- 집행: proposal_writer.persist로 통상 bid_up 제안 생성(rationale "[EX확장] …" 태그) →
  08:50 일 레인 4조건 심사(P3 폴백 적용) → harness → guardrail_gate 전량 통과 필수.
  **신규 실쓰기 경로 0** — 기존 실행 파이프라인 재사용이 안전의 핵심.

### 4-3. P4 밴드 동적화 (EX에 흡수)

- 현행: `exploration.py` rank ≤ 2.5 = 과열밴드 진입 금지(전 그룹 공통 정적 천장).
- 변경: rank ≤ 2.5 그룹이라도 **①자기 표본 충분(clk≥10) ∧ ②정착창 보정ROAS ≥ bep_roas ×
  EX_DEEP_RATIO(=1.25) ∧ ③bid_rank_slope 프라이어 존재**면 상향 지속 허용(동적 경계).
  셋 중 하나라도 미충족 → 현행 정적 밴드 유지(fail-closed — 증거 없이 과열 진입 금지).
- DOWN 방향·탐침 프라이어 경계(2.5)는 불변.

### 4-4. P3 UP게이트 프라이어 폴백

- 위치: `auto_operator._check_bid_up_conditions` 조건②(clk≥10)·조건③(_settlement_roas_ok).
- 변경: 조건②·③이 **표본 부족(unknown)** 으로 hold될 때, 해당 제안이 [EX확장] 태그이고
  캠페인이 확장 모드(압력 판정 통과)면 → 캠페인 프라이어(캠페인 정착창 보정ROAS ≥ target)로
  대체 통과. `below`(명시적 미달)는 폴백 불가(거부권 유지 — DOWN 비대칭은 올바른 보수성).
- 일반(비EX) bid_up은 현행 유지 — 폴백은 확장 모드에서만(계통적 브레이크 해소를 EX 문맥에 한정).

### 4-5. D-NAO-87 예산 봉투

- SA `budget_envelope.py`(순수): auto_operate 캠페인별
  **봉투 = max(과거 30일 일평균 지출 × 1.5, 50,000원)** (일평균 = NaverAdDaily 30일 합÷관측일수,
  sentinel 제외).
- 현재 daily_budget < 봉투 → budget_up 제안(target = min(봉투, 현재×2) — guardrail +100%캡 정합,
  봉투까지 여러 날에 걸쳐 램프). daily_budget=0(uncapped)·봉투 ≤ 현재 → 제안 없음(관찰만).
  **자동 감액 없음** — 봉투는 천장 개방 레버지 조임 레버가 아니다(감액은 기존 Confirm 경로 유지).
- 심사: 일 레인에 budget_up 자동 심사 추가 — auto_operate ∧ [예산봉투] 태그 ∧ guardrail
  _check_budget 통과 시 자동 승인·집행(APPROVAL_SOURCE_DAILY). 재산정 = 매일(30일 롤링).
  비auto_operate 캠페인 budget_up은 현행 Confirm 전용 불변.
- 라운드 캡(회당 총 증가 10만) 존속 — 봉투 램프도 이 캡 안에서.

## §5. Phase 분할·모델 라우팅·검증

| Phase | 내용 | 모델 | 검증 |
|-------|------|------|------|
| 1 | P7 profit_scorecard SA+크론+테스트 | Sonnet | pytest + codex review |
| 2 | expansion_pressure + expansion_allocator SA(순수)+테스트 | Opus | pytest + codex review |
| 3 | 레인 배선(proposal_pipeline EX 단계·P3 폴백·일 레인 budget_up 심사) | Opus | pytest + codex review |
| 4 | P4 밴드 동적화(exploration) + budget_envelope SA | Opus | pytest + codex review |
| 5 | 전체 회귀 + 배포(safe_deploy) + 라이브 합격 | Sonnet(관측) | 원칙22 라이브 증거 |

**라이브 합격 시나리오(착수 전 못박음, 원칙 22)**:
1. 배포 후 첫 08:00 크론에서 EX 판정 로그·diary 기록 발생(확장 모드 캠페인 ≥ 0건이라도 판정 자체 기록).
2. 03 캠페인(gap 1.66 실측)이 확장 모드로 판정되고 [EX확장] 제안 생성 → 08:50 일 레인 심사 통과
   여부와 사유가 diary에 남는다.
3. 예산 봉투: 03(일지출 ~7천)의 budget_up 제안 생성 + 자동 집행 + change_log 기록 + 네이버
   재조회 dailyBudget 반영 확인.
4. P7 스코어카드 Slack 브리핑 1회 실수신.
5. 가드레일 전량 생존(테스트 회귀 0 + 라이브 스텝 클램프·쿨다운 로그 확인).

## §6. 4일 재판정(착수 ③) 결과 — 2026-07-23 prod 실측 (read-only)

**정정**: "17프로"는 별도 캠페인이 아니라 **캠페인 03 소속 애드그룹** `grp-a001-02-000000059879629`
(상품 12382833885 단일 매핑). 03=`cmp-a001-02-000000008492582`, 04=`cmp-a001-02-000000008514959`,
둘 다 auto_operate=1. prod 실DB=`/home/ubuntu/ohisell/backend/ohisell.db`(ad_data.db는 0바이트 방치).

| 대상 | 6월 일평균 | 07-19~22 일평균 | 4일 ROAS | BEP(median) | 갭 비율(ROAS/BEP) |
|---|---|---|---:|---:|---:|
| 03 | 노출1,524·클릭8.5·비16,512·매출39,563 (ROAS 2.40) | 노출1,126·클릭6.0·비11,832·매출35,050 | 2.9622 | 1.5555 | **1.90** |
| 04 | 노출1,352·클릭4.3·비5,927·매출10,070 (ROAS 1.70) | 노출912·클릭4.75·비6,317·매출15,900 | 2.5169 | ~1.50 | **~1.67** |
| 17프로 | (그룹) 클릭0 지속 | 07-22 첫 클릭1·전환1(13,900원)·순위 3.8 | n=1 | 1.5484(구가격) | 판정 불가 |

- **03**: 회복은 사실이나 다일 추세가 아니라 07-22 단발(순위 5.3→4.3 급개선+17프로 개방) 견인.
  광고비 9,370→4,108→8,590→25,261. → EX 압력 판정 실증 표본(갭 1.90 ≥ 1.25).
- **04**: 갭은 있으나(1.67) 07-22 CTR 0.07%(노출 1,461·클릭 1)로 밴드 내 클릭 가뭄 **심화** —
  압력이 있어도 클릭이 안 붙는 케이스. 배분 랭킹 1층(밴드내+순위여유)이 걸러야 함(CTR 경보
  그룹은 래더 skip 기존 규칙 존속).
- **⚠️17프로 BEP stale**: `_unit_prices` 120일 median이 구가격(18,900) 지배 — 신가격(13,900) 기준
  BEP는 더 높다. 주문 누적으로 자가 치유되는 소스 지연 — 코드 개입 없음, EX 판정 시 감안만.
  (스코어카드·EX 판정 공히 이 소스를 쓰므로 17프로 그룹 확장은 보수적으로 나온다 — 올바른 방향.)
- **보정계수**: conv_delay 학습값이 day_7~21만 존재(day_0~6 없음) — 정착창(D-8~D-2) 판정 관례
  유지가 옳음을 재확인. EX 압력 판정도 정착창 + `diagnosis.correction_factor` 경로 재사용.
- **예산 봉투 실계산(D-NAO-87)**: 03=30일 일평균 29,229→봉투 50,000(현행 50,000, **변화 없음**) /
  04=일평균 8,115→봉투 50,000(바닥값, 현행 30,000 → **+20,000 확대**, +67%<+100% 캡 1스텝).
- **캘리브레이션 확정**: EX_PRESSURE_RATIO=1.25 유지(03=1.90·04=1.67 모두 초과, 여유 있음).
  EX_MIN_CAMPAIGN_CLK=30 유지(정착창 7일 기준 03≈42·04≈33 통과 — 표본 게이트가 현실 데이터와
  정합함을 확인).
