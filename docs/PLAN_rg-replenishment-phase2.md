# PLAN — RG 발송관제 Phase 2 (예측 고도화 + in-transit + newsvendor)

> 작성: 2026-06-17 (Opus) · 트랙: `docs/tracks/active/track_coupang-rg-replenishment.md` (확정 결정 D-10~D-13)
> 맥락노트·체크리스트 = 트랙 파일. 이 문서는 스프린트 단위 실행 계획서(SDD).
> 흐름: 구조 승인(완료) → 이 계획서 → /plan-eng-review(선택) → Sonnet 스프린트 구현 → codex(원칙19) → prod self-verify(원칙22).

## 목표 (What/Why — 원칙 P-1, How는 스프린트에서)
간헐 수요 통계예측(SBA/TSB) + 발송중(in-transit) 유효재고 반영 + newsvendor 분위수 목표재고로
"855옵션 98.6% insufficient_data"와 "중복 발송"을 해소한다. 결과 = 로켓그로스 탭 단일 조망:
`현재고 | 발송중 | 일판매 | 소진예상일 | 권장 발송일·수량 | 보관리스크`.

## eng-review 반영 결정 (2026-06-17 /plan-eng-review)
- **R1 (D-10 수단 개정)**: statsforecast(numba+pandas+scipy, 콜드스타트 위험) 채택 보류 → **Croston/SBA/TSB 직접 구현(~60줄, 무거운 dep 0)**. method는 D-10 그대로(SBA/TSB), 수단만 변경. 정확성 oracle = **Syntetos-Boylan 논문 검산값 fixture**(statsforecast 레퍼런스 안 씀 — X8). 근거: 작은 prod 서버 콜드스타트 회피·fixture 전수테스트 예정·newsvendor 분위수는 직접 모델링 필요(R2/X2).
- **R2 (newsvendor 산출법 명세)**: 점예측→분포는 **경험적 부트스트랩**(보호기간 L+R 동안 일수요를 관측에서 리샘플링→99% 분위수). 정규분포 가정 금지(간헐=비정규).
- **R3 (in-transit fail-soft 정정)**: stale 시 발송중=0은 "재고 보수적"이 아니라 "품절회피 우선"(D-12 부합). **freshness-gate**: 입고데이터 <2일이면 차감, stale면 차감 스킵 + UI "발송중 오래됨" 배지.
- **R4 (테스트)**: S8·S9·S11도 fixture 단위테스트 필수(S10만 있었음). S9는 Syntetos-Boylan 검산값으로 고정.
- **R5 (라우팅)**: forecaster의 base_rate 덮어쓰기는 Harness `_velocity_for` 어댑터에서(원칙18-6, SA 직접호출 금지).

## eng-review 아웃사이드 보이스 반영 (2026-06-17, Claude 서브에이전트 — codex 사용한도 소진)
- **X1 (예측 ROI 정직화, ★전략·Jino 확인 요)**: SBA/TSB는 "판매신호 0" 옵션을 못 살린다(Croston도 nonzero 이벤트 필요). 98.6% insufficient의 진짜 원인을 먼저 진단 — **855옵션을 zero-signal/sparse/active로 버킷팅**(S8a 선행). 예측은 **sparse-but-nonzero 집합만** 살림. zero-signal은 설계상 insufficient 유지(시간·커버리지 문제이지 예측문제 아님). **단기 데이터에선 Phase 2 즉효 범위가 제한적임을 명시**(데이터 누적 시 확대).
- **X2 (분위수 산출법 개정, R2 대체)**: 11일 데이터로 99% **부트스트랩=사실상 최대값**(꼬리 없음). → **모수적 음이항분포(NBD) 리드타임 수요**로 변경(SBA 평균+추정 분산, 작은 표본에서도 꼬리 모델링 — 간헐재고 표준). 표본 극소 옵션은 분산 하한 적용.
- **X4 (시퀀스 재배치, ★전략·Jino 확인 요)**: **in-transit를 첫 스프린트로** 앞당김(데이터 이미 존재·검증가능·중복발송 즉시 방지). 예측 타워(분류·예측·newsvendor)는 그 뒤. 리스크/가치 우선 순서. → 새 순서: **S8 in-transit → S9 진단/분류 → S10 예측 → S11 newsvendor → S12 백테스트**.
- **X5 (freshness-gate 기준 정정, R3 대체)**: 게이트는 inbound 행 나이가 아니라 **마지막 성공 fetch 시각(sync 건강도)** 기준. 입고이벤트는 본래 희소(반년 6건)라 행 나이로 stale 판정하면 정상도 stale로 오판. fetch 성공 <2일이면 in-transit 신뢰.
- **X6 (in-transit 종료 의미 추가)**: `발송중=Σ(입고생성−판매개시)`는 취소·분실·부분입고에 phantom 잔존 → 만성 과소발송(silent 품절). **수정**: receivedQty/stowedQty(S1 적재됨) 반영 + 입고취소 status 제외 + **max(리드 p90+버퍼) 초과 미적치 입고는 만료**(분실/취소로 간주, in-transit에서 제거).
- **X7 (D-9↔D-12 정합, ★Jino 확인 요)**: D-9(목표 7일치)와 D-12(분위수)가 충돌. 정합안 = **검토주기 R=7일**(D-9 의도='주 1회 보충 cadence' 계승) + 목표 = **(리드 L + 7일) 수요의 99% 분위수**(D-12 안전수준). 즉 D-9는 cadence, D-12는 safety로 역할 분리. target_days→review_period_days=7 리네임.
- **X8 (검증 경로 정리)**: statsforecast를 "검산 레퍼런스"로 쓰지 않음(dep로 거부한 걸 oracle로 쓰면 관례 불일치). fixture oracle = **Syntetos-Boylan 논문 검산값만**. R1/S12에서 statsforecast 레퍼런스 표현 삭제.

## 불변 제약 (트랙 결정)
- D-10: 리드타임은 SBA/TSB 대상 아님(수요만). 리드타임 = 실측 평균/p90 유지(S2 무변경). **수단=직접 구현(R1).**
- D-12: 목표재고 = 수요분포 서비스수준 분위수. **시작 99%**. 상품별 자동(전부 100%↑). 단일 % 금지.
- D-13: 유효재고 = 현재고 + 발송중. 발송중 = Σ(입고생성−판매개시). 판매개시 예정 = 도착예정일 + (도착→판매개시)갭.
- D-3/D-4: 결정론적 계산값(예측+정책)이지 전략추천 아님. 발송 실행 결정은 Jino.
- 원칙18: SA 단일책임 / Harness가 정보유통 허브 / 라우터는 Harness만 호출.
- 등가성 계약(S5): 배치 주입 == 단일 SA 직접호출. 신규 SA도 이 계약에 편입.

## 아키텍처 (승인됨 — 트랙 "구조 확정" 참조)
신규 SA 3개: `demand_classifier`(P0) · `sba_forecaster`(P1) · `in_transit_estimator`(P3).
기존: S2 무변경 · S3 요일계수 유지 · S4 유효재고+newsvendor 개선 · S5 Harness 라우팅 추가.
수집: `wing_browser_fetcher.py` rfm-inbound 추가 → `rg_inbound_sync`(S1) → `coupang_rg_inbound`.

---

## 스프린트 분해 (점진 — 원칙5, sprint=1~2일)
> ★실행 순서(X4 재배치): **① in-transit(아래 "★X4" 블록) → ② S8 진단/분류 → ③ S9 예측 → ④ S10 newsvendor → ⑤ S12 백테스트.** in-transit이 가장 확실·검증가능·고가치라 먼저. 예측 타워는 데이터 누적과 함께.
> ★S8 선행 진단(X1): 855옵션을 **zero-signal / sparse-but-nonzero / active**로 버킷팅 → 예측이 살릴 수 있는 모집단(sparse) 크기를 먼저 정직하게 측정. zero-signal은 설계상 insufficient 유지.

### S8 (P0) — demand_classifier SA (+ X1 버킷 진단)
- **What**: 옵션별 일판매 시계열 → ADI(평균 발생간격)·CV²(수량 변동계수) → smooth/erratic/intermittent/lumpy 4분면(컷 1.32/0.49). 표본 부족은 `unknown` + 폴백 라벨.
- **신규**: `backend/app/services/coupang/demand_classifier.py`. 읽기전용, 새 테이블 없음. `classify_demand(db, account_key)`(전체) + `classify_demand_one(db, vii, account_key)`(단일, 원칙18-8). 데이터원 = `coupang_rg_order_item`(TRUST_START 이후).
- **검증 엔드포인트**: `GET /api/coupang/ops/demand-class`(조회 read-only, SA 직접 = 원칙18-7 예외).
- **완료기준**: prod 라이브 855옵션 4분면 분포 출력 + 표본부족 unknown 정직 표기. codex pass.

### S9 (P1) — sba_forecaster SA (직접 구현, R1)
- **What**: 일판매 시계열 + S8 라벨(라우팅) → 일수요 평균 d̄ + 분산(X2 NBD 모수용). intermittent/erratic=SBA, 단종의심=TSB, smooth=SES, unknown=현 sold_30d/30 폴백.
- **신규**: `backend/app/services/coupang/sba_forecaster.py`. **Croston/SBA/TSB/SES 직접 구현(~60줄, 무거운 dep 0 — R1)**. `forecast_demand(db, account_key, *, classes=None)` + `forecast_demand_one(...)`. classes는 S8 출력 optional 주입(원칙18-8). 데이터 부족 옵션 None(정직).
- **Harness 라우팅(R5)**: velocity의 base_rate를 forecaster 출력으로 덮어씀은 **Harness `_velocity_for` 어댑터에서**(요일계수는 S3 유지, SA 직접호출 금지). 등가성 계약 유지.
- **테스트(R4)**: fixture 필수 — Croston/SBA/TSB를 **Syntetos-Boylan 논문 검산값**으로 고정 + 전부 0→None + 단일 수요 처리.
- **성능**: 855옵션 on-demand 산술이라 <2s 예상(직접 구현). S9에서 실측, >3s면 스케줄 precompute 전환.
- **완료기준**: prod에서 sparse-but-nonzero 집합이 예측치 획득(X1 버킷 전/후) + NBD 모수 산출 + 855옵션 응답시간 실측. codex pass.

### S10 (P2) — newsvendor 목표재고 (replenishment_calc 개선)
- **What**: 목표재고 = **모수적 NBD 분위수(X2)** — 보호기간(리드 L + **검토주기 R=7일**, X7) 동안 수요를 음이항분포(평균=SBA, 분산=추정+하한)로 모델링한 분포의 서비스수준 분위수. 서비스수준 기본 99%. 기존 `target_days×rate+(p90−mean)×rate` 대체. 발송일 역산(p90 차감)은 유지. **정규·부트스트랩 가정 금지(11일 표본엔 꼬리 없음).**
- **D-9 정합(X7)**: `target_days`(7)→`review_period_days`(7)로 역할 변경 — D-9는 cadence(R), D-12는 safety(분위수).
- **수정**: `replenishment_calc.py`(목표레벨 NBD 분위수 함수, `SERVICE_LEVEL=0.99`·`REVIEW_PERIOD_DAYS=7` 상수), forecaster 분포(평균·분산) 주입 시그니처(_UNSET 패턴).
- **머니/재고 민감** → fixture 단위테스트 필수(R4: 서비스수준↑→목표↑ 단조성, 안정상품≈110%·lumpy 200%↑ 자동, 분산하한, L+R 경계).
- **완료기준**: prod 사본에서 옵션별 목표재고% 분포(전부 100%↑·수요형태별 차등) 수동 대조. codex pass.

### (★X4: 이 스프린트를 첫 번째로) in_transit_estimator SA + 페처 배선 + 유효재고
- **What**: `coupang_rg_inbound` → 옵션별 발송중 수량 + 판매개시 예정(도착예정일+갭). calc 유효재고=현재고+발송중(도착 시점 투영에 반영). **먼저 하는 이유(X4)**: 데이터 이미 적재·Wing 화면으로 검증가능·중복발송 즉시 방지(예측보다 확실한 가치).
- **발송중 수량(X6 종료의미)**: `Σ(입고생성 − 판매개시)`에서 receivedQty/stowedQty 반영 + 입고취소 status 제외 + **리드 p90+버퍼 초과 미적치 입고는 만료(분실/취소 간주)** → phantom 잔존(만성 과소발송·silent 품절) 방지.
- **신규**: `backend/app/services/coupang/in_transit_estimator.py`(읽기전용). `estimate_in_transit(db, account_key)` + 단일.
- **수정**: `tools/wing_browser_fetcher.py`(rfm-inbound 호출+push, 검증된 페처에 추가) · `rg_replenishment.py`(in-transit 주입) · `replenishment_calc.py`(유효재고 반영) · 데몬/스케줄 정기수집.
- **freshness-gate(X5)**: **마지막 성공 fetch 시각** 기준(inbound 행 나이 아님 — 입고이벤트는 본래 희소). fetch <2일이면 차감, stale면 차감 스킵(과발송 편향=D-12 품절회피 우선) + UI "발송중 데이터 오래됨" 배지.
- **테스트(R4)**: fixture — 발송중 정확성(필름100·버디0 실측 대조) + 만료/취소/부분입고 분기(X6) + fetch-time freshness-gate(X5).
- **완료기준**: prod 라이브 발송중 수량이 Wing 입고관리 화면과 일치(필름 100·버디필름 0 등) + 중복 발송 해소(발송중 있는 옵션 권장수량 차감). codex pass.

### S12 (P4) — 백테스트 루프
- **What**: 과거 데이터로 fill-rate·품절일수·과잉재고 측정 → 서비스수준/방법 검증 및 "최적 숫자"(D-12) 탐색.
- **신규**: `backend/app/services/coupang/replenishment_backtest.py` 또는 tools 스크립트. 읽기전용.
- **완료기준**: 99% 서비스수준의 fill-rate·평균재고 리포트 + 대안 서비스수준 비교표.

### S13 (P5, 선택) — LightGBM 글로벌+분위수
- 데이터 충분·ROI 검증 후. 이번 Phase 범위 밖(트랙 P5).

## UI (S9·S11 후)
로켓그로스 탭 발송관제 섹션 컬럼 추가: `발송중`·`소진예상일`·(수요분류 배지). `frontend/src/pages/CoupangOps.tsx` + `api.ts` 타입 확장.

## 리스크 / 정직성 (원칙22)
- 예측 직접 구현(R1): Croston/SBA/TSB는 fixture로 Syntetos-Boylan 검산값 고정 후에만 신뢰. statsforecast 무거운 dep 회피.
- 표본 부족 옵션은 강제 예측 금지 → unknown/None 정직 표기(D-4).
- newsvendor 부트스트랩 분위수는 머니/재고 직결 → fixture 테스트 + prod 사본 self-verify 후 라이브.
- in-transit 페처는 세션쿠키 의존(D-5) → 만료 시 freshness-gate(R3, 차감 스킵 + stale 배지).

## What already exists (재사용 — 재구축 금지)
- `rg_inbound_sync.py`(S1) — rfm-inbound → `coupang_rg_inbound` 쓰기 이미 구현. S11은 **페처 호출 + 읽기 SA만** 신규(쓰기 재사용).
- `rg_size_classifier.py`·`rg_fee_anomaly.py` — 분류/스코어 SA 패턴 선례. demand_classifier 동일 패턴.
- S5 배치 주입 등가성 어댑터(`_velocity_for`/`_lead_for`) — 신규 SA가 여기 편입, 신규 오케스트레이션 0.
- `lead_time_estimator.py`(S2) — 발송→판매개시 리드타임. 변경 없이 그대로.

## NOT in scope (의도적 보류)
- **P5 LightGBM**(S13) — 855옵션·짧은 이력은 데이터 부족, ROI 미검증. 백테스트 후 재평가.
- **비용기반 newsvendor**(Cu/Co) — 실제 품절·보관·반품비 데이터 누적 후 전환(D-12). 시작은 서비스수준 99%.
- **Wing inventory-health-dashboard API**(OOS/OVERSTOCK) — 발송관제에 불필요(현재고+발송중으로 충분).
- **쿠키 자동수확 자동화**(D-5) — 만료 주기 측정 후 잦으면 별도. 현재 freshness-gate로 방어.

## 다음 액션
- ✅ `/plan-eng-review` 완료(2026-06-17) — R1~R5 + 아웃사이드 보이스 X1~X8 반영.
- **★Jino 확인 요(트랙/전략 변경)**: X1(예측 ROI 정직화)·X4(in-transit 우선 재배치)·X7(D-9→검토주기 R=7 재정의). 이의 없으면 트랙 D-N으로 승격.
- `/model sonnet` 전환 후 **in-transit 스프린트부터** 구현 → 각 스프린트 codex(원칙19) → prod self-verify → 트랙·progress 갱신.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_open | 5 review + 8 outside-voice, all folded into plan |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **CODEX:** codex 사용한도 소진(Jun 19 리셋) → Claude 서브에이전트로 아웃사이드 보이스 대체(X1~X8).
- **CROSS-MODEL:** 검토 5건(R1~R5)+아웃사이드 8건(X1~X8) 모두 계획서 반영. 충돌 없음(아웃사이드가 검토를 보강·심화).
- **VERDICT:** ENG REVIEW 완료 — 계획 구현 준비됨. 단, 3건(X1·X4·X7)이 트랙 결정/전략을 건드려 Jino 확인 대기.

**UNRESOLVED DECISIONS:**
- X1 — 예측 ROI 정직화(zero-signal은 예측으로 못 살림): 진단 버킷팅 선행 동의 여부
- X4 — in-transit를 첫 스프린트로 재배치: 시퀀스 변경 동의 여부
- X7 — D-9(7일치)를 검토주기 R=7로 재정의(D-12 분위수와 역할 분리): 동의 여부
