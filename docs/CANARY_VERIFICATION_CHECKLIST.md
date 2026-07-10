# 카나리 검증 체크리스트 — MOP 기능 대비 우리 구현 시뮬레이션

> 목적: MOP 4단 엔진의 각 기능이 우리 시스템에서 **실제로 작동하는지** 카나리 캠페인 2~3개에서 검증.
> 시점: Jino 카나리 캠페인 지정 + optimizer='ours' 전환 후.
> 원칙: "작동한다"는 라이브 증거로만(원칙 22). 격리 테스트 통과 ≠ 라이브 검증.

---

## 검증 대상 범위

MOP 4단 엔진 | 우리 대응 모듈 | 구현 상태
---|---|---
**1단 수집(Collection)** | entity_sync · ad_daily_ingest · search_term_ingest · hourly_snapshot · keyword_volume_sync | ✅ prod 가동 중
**2단 러닝(Learning)** | forecast_engine · estimate_calibrator · conversion_maturity · hourly_pattern · proposal_scoreboard | ✅ prod 가동 중
**3단 플래닝(Planning)** | proposal_pipeline · bid_simulator · growth_sweeper · proposal_writer · expert_desk(Ava) | ✅ prod 가동 중
**4단 플라이트(Flight)** | naver_execution_harness · naver_sa_writer · guardrail_gate · delegation_gate | ✅ 코드 완료·prod 배포(X1b). 카나리 라이브 왕복 미실시

**미구현(X2/X3 대기)**:
- 당일 플라이트 루프(response_curve_builder · pacing_controller · flight_loop) = X2
- 계층 풀링(DHEB) · GAVE 페널티 점수 = X3
- 순위유지 = §8 승계 큐(G4)

---

## 1단: 수집 (MOP Collection)

MOP은 Connection Status · Performance Data collected · Anomaly Detection 3개를 대시보드에 표시.

### ✅ 이미 라이브 검증 완료된 항목

| # | MOP 기능 | 우리 대응 | 검증 증거 |
|---|---------|----------|----------|
| C1 | 매체 데이터 수집 상태 | `sync_naver_ad_daily` (07:30 KST) | prod 크론 매일 실행, dashboard-overview "ingest" 스테이지 표시 |
| C2 | 캠페인/그룹/키워드 인벤토리 | `sync_naver_entity` (07:35 KST) | 90,150+ 엔티티 실측 수집 확인 |
| C3 | 시간대별 스냅샷 | `snapshot_naver_ad_hourly` (매시 :05) | hourly_snapshot 행 prod 적재 확인 |
| C4 | 검색어 성과 | `sync_naver_search_term` (07:40 KST) | SHOPPINGKEYWORD_DETAIL + EXPKEYWORD 교차 수집 |
| C5 | 키워드 월검색량 | `sync_naver_keyword_volume` (일요일 09:00) | keywordstool API 연동 확인 |

### 🔲 카나리에서 추가 검증할 항목

| # | 검증 시나리오 | 판정 기준 | 확인 방법 |
|---|-------------|----------|----------|
| C6 | **외부 상태 변경 감지(D-NAO-40)** | MOP/사람이 카나리 캠페인의 키워드 status를 바꾸면 → entity_sync가 `external_status_change` change_log 행 생성 | 카나리 키워드 1개를 네이버 UI에서 수동 OFF → 07:35 크론 후 change_log 조회 |
| C7 | **이상 감지: 비용 급등** | 어제 대비 비정상 소진 캠페인 감지 | `anomaly_feed.spend_anomalies()` 결과에 카나리 캠페인 포함 여부 확인(자연 발생 또는 의도적 예산 증액 후) |
| C8 | **이상 감지: 데이터 공백** | 수집 누락 시 freshness 경고 | `anomaly_feed.freshness_partial_load()` — 크론 실패 시뮬레이션 불필요, 자연 관찰로 충분 |

---

## 2단: 러닝 (MOP Learning/Projection)

MOP은 ML 모델 생성 · 일일 예측 · Bid Planning 진행률을 표시.

### ✅ 이미 라이브 검증 완료된 항목

| # | MOP 기능 | 우리 대응 | 검증 증거 |
|---|---------|----------|----------|
| L1 | ML 모델 수·생성 상태 | `forecast_engine` (07:50 KST) | 캠페인 grain 3일 지수감쇠 모델, 매일 학습·예측 (나이브 대비 clk MAPE -5.1%) |
| L2 | 예측 정확도 공개 | `forecast_scorer` — MAPE 공개(성적표) | **MOP 미제공** — 우리 우위. 실측 기록 있음 |
| L3 | 견적 API 편향 보정 | `estimate_calibrator` | learning_state 테이블에 편향 계수 누적 |
| L4 | 전환 성숙도 곡선 | `conversion_maturity` | D+1~14 전환 숙성 비율 학습 |
| L5 | 시간대 패턴 학습 | `hourly_pattern` | 요일×시간대 비용 분포 학습, 가중치 권고 |
| L6 | 제안 성적표(D+7/14) | `proposal_scoreboard` | **X1b T5에서 배선 결함 수정 완료** — 실채점은 D+7 후 확인 |

### 🔲 카나리에서 추가 검증할 항목

| # | 검증 시나리오 | 판정 기준 | 확인 방법 |
|---|-------------|----------|----------|
| L7 | **예측→제안 근거 연결** | 제안 rationale에 예측 근거가 병기되는지 | 카나리 캠페인의 pending 제안에 forecast evidence suffix 확인 |
| L8 | **D+7 채점 실제 작동** | X1b T5 수정 후 첫 실채점 | 카나리 제안 실행 7일 후 `proposal_scoreboard` 결과 조회 — `outcome` 값이 채워지는지 |
| L9 | **대시보드 엔진 카드** | 4단 파이프라인 상태가 콘솔에 정확히 표시 | `GET /dashboard-overview` — 5개 스테이지 전부 status 확인 |

---

## 3단: 플래닝 (MOP Planning)

MOP은 제안 생성 · 입찰 변경 키워드 수 · 계획 수립률을 표시.

### ✅ 이미 라이브 검증 완료된 항목

| # | MOP 기능 | 우리 대응 | 검증 증거 |
|---|---------|----------|----------|
| P1 | 진단 보드 7종 | `account_diagnosis` (출혈·굶는승자·악순환·쇼핑BEP·제외후보·정지후보·재개후보) | prod 실데이터 검증, 콘솔 시각화 |
| P2 | BEP-ROAS 자동 산출 | `bep_calculator` + `campaign_target_resolver` | 수수료 7.8% + 물류비표 → 자동 손익분기 (MOP 미제공) |
| P3 | 입찰 시뮬레이션 | `bid_simulator` (pooled_rpc + affordable_ceiling) | 견적 API × 보정계수 → 경제성 상한 |
| P4 | 볼륨 확장 탐색 | `growth_sweeper` | BEP 미달 키워드의 성장 잠재력 발굴 |
| P5 | 제안 자동 생성 | `proposal_pipeline` (08:00 KST) | 7종 제안 일일 생성 (prod 261건 pending 확인) |
| P6 | Slack 알림 | `slack_notifier` | 제안 생성 후 Slack 발송 |
| P7 | 전문가(Ava) 검토 | `expert_desk` (08:05 KST) | LLM 독립 평결 (agree/disagree/partial) |
| P8 | 보정 계수(전환 보정) | `correction_factor` | 네이버 전환 ↔ 실주문 괴리 자동 보정 |

### 🔲 카나리에서 추가 검증할 항목

| # | 검증 시나리오 | 판정 기준 | 확인 방법 |
|---|-------------|----------|----------|
| P9 | **정지 제안 생성** | 스톱로스 도달 키워드에 pause 제안이 생기는지 | 카나리 캠페인 중 무전환+고비용 키워드 존재 시 → pending에 `proposal_type='pause'` 확인 |
| P10 | **재개 제안 생성** | 정지 사유 해소 키워드에 resume 제안이 생기는지 | P9에서 정지 실행 후 BEP 개선(또는 시간경과) → resume 제안 생성 확인 |
| P11 | **optimizer 필터** | ours 아닌 캠페인에는 실행형 제안 미생성 | 비카나리 캠페인의 bid_up/pause 등 실행형 제안이 0건인지 확인 |
| P12 | **차등 TTL(D-NAO-37)** | 정보성 D+1, 이상감지 D+3, 실행형 14일 | 크론 후 expired 처리된 행의 유형·시점 확인 |
| P13 | **Ava 브리핑 접기** | 정보성은 집계만, 실행형은 전건 | `expert_desk` 브리핑 내용에서 정보성 유형이 개별이 아닌 집계 블록인지 확인 |

---

## 4단: 플라이트 (MOP Flight — 핵심 검증 구간)

MOP은 Bidding(시간 그룹 · Next bidding) · Maintenance(순위유지) 표시.
**우리 X1b = 정지·재개 + 입찰 개방 + 가드레일. X2(당일 루프) 미구현.**

### 🔲 카나리에서 검증할 항목 (전부 라이브 왕복 필수)

| # | 검증 시나리오 | 판정 기준 | 확인 방법 |
|---|-------------|----------|----------|
| **F1** | **제외키워드 실행** | 콘솔 승인→실행→네이버 반영 | 카나리 그룹에서 제안 승인 → `POST /proposals/{id}/execute` → 네이버 API 재조회로 제외키워드 존재 확인 |
| **F2** | **입찰 변경 실행** | bid_up 제안 승인→실행→네이버 입찰가 변경 | 카나리 키워드 bid_up 제안 → 실행 → `get_keyword()` 재조회로 bidAmt 변경 확인 + `useGroupBidAmt=false` 확인 |
| **F3** | **키워드 정지 실행** | pause 제안→실행→네이버 userLock=true | 카나리 키워드 pause 제안 → 실행 → `get_keyword()` 재조회 `userLock=true` 확인 |
| **F4** | **키워드 재개 실행** | resume 제안→실행→네이버 userLock=false | F3 정지 후 resume 제안 생성·실행 → `get_keyword()` 재조회 `userLock=false` 확인 |
| **F5** | **가드레일: ±15% 제한** | 현재 700원→900원(+28%) 제안이 차단됨 | bid_up 제안의 target_bid가 현재의 115% 초과 시 → guardrail_gate "±15% 초과" 사유로 차단 → 콘솔에 사유 표시 |
| **F6** | **가드레일: 쿨다운** | 변경 후 5시간 내 재변경 시도 차단 | F2 실행 직후 같은 키워드에 대한 다른 제안 실행 시도 → 쿨다운 차단 확인 |
| **F7** | **가드레일: 일일 상한** | 동일 키워드 하루 3건 초과 시 차단 | (시뮬레이션 난이도 높음 — 쿨다운 5h × 3건 = 15h 필요. 단위테스트로 보완, 라이브는 자연 관찰) |
| **F8** | **가드레일: 스톱로스 증액 금지** | 무전환 고비용 키워드의 bid_up 차단 | 무전환 키워드에 대한 bid_up이 guardrail에서 BEP미달 사유로 차단되는지 |
| **F9** | **가드레일: 10원 단위 · 70~100,000원 클램프** | 범위 밖 입찰가 차단 | target_bid=50(70미만)이나 110,000(초과) 제안이 차단되는지 (단위테스트 기확인, 라이브는 정상 범위 제안으로 통과 확인) |
| **F10** | **MOP 충돌 감지(D-NAO-13)** | 외부 변경 후 실행 시 경고 부착 | C6(외부 상태 변경) 후 해당 키워드에 실행 시도 → rationale에 경고 메시지 포함 확인 |
| **F11** | **change_log 전건 기록** | 모든 실행에 before/after 실측값 기록 | F1~F4 각 실행 후 `naver_change_log` 행에 before_value·after_value·dry_run=False 확인 |
| **F12** | **미개방 액션 차단** | budget_up 등 미개방 액션 실행 불가 | (미개방 액션의 제안은 생성 자체가 ours 필터에 걸려 희소 — 발생 시 409 확인) |
| **F13** | **콘솔 승인 없이 실행 불가** | pending 상태에서 직접 execute 시도 → 차단 | `POST /proposals/{id}/execute` (status=pending) → 400/409 |
| **F14** | **E2 위임 자동승인** | 위임 ON 유형 + Ava agree → 자동 실행 | 위임 스위치 ON(Jino만) → 08:05 Ava run → delegation_gate → 자동 approved → harness 실행 → change_log 확인 |
| **F15** | **위임 OFF 유형 차단** | 위임 OFF 유형은 Ava agree여도 사람 대기 | negative_keyword만 위임 ON, bid_up은 OFF → bid_up agree 제안이 pending 잔류 확인 |

---

## 5단: 우리만의 기능 (MOP에 없는 것)

| # | 기능 | 구현 상태 | 카나리 검증 |
|---|------|----------|-----------|
| U1 | **BEP-ROAS 수치 타겟**(마진 기반 자동 산출) | ✅ prod | 카나리 캠페인의 target_roas가 BEP 기반으로 산출되는지 확인 |
| U2 | **변경 이력 투명성**(전건 before/after) | ✅ prod (X1b) | F11과 동일 |
| U3 | **LLM 전문가(Ava)** | ✅ prod | Ava 평결이 카나리 제안에 대해 합리적인지(agree/disagree 근거) 확인 |
| U4 | **예측 성적표 공개**(MAPE) | ✅ prod | forecast_scorer 결과가 콘솔에서 조회 가능한지 |
| U5 | **손익 통합**(실주문 대조 ROAS) | ✅ prod | 카나리 캠페인의 보정 ROAS가 correction_factor 적용 후 합리적인지 |
| U6 | **콘솔 엔진 카드** | ✅ prod | dashboard-overview 5단 스테이지 정상 표시 |

---

## 검증 순서 (권장)

### Phase A — 수집·러닝 확인 (카나리 지정 직후, 수동 개입 불필요)
1. C6: 외부 상태 변경 감지 테스트(네이버 UI에서 카나리 키워드 1개 수동 OFF → 07:35 크론 확인)
2. L7~L9: 대시보드·예측 연결 확인
3. P9~P13: 제안 생성 정상 여부

### Phase B — 반자동 실행 왕복 (핵심 — Jino 입회)
4. F13: 승인 없이 실행 불가 확인
5. F1: 제외키워드 1건 승인→실행→재조회
6. F2: 입찰 변경 1건 승인→실행→재조회 + F5(±15% 가드레일)
7. F3: 정지 1건 실행→재조회
8. F4: 재개 1건 실행→재조회 (F3 키워드)
9. F11: 전건 change_log 확인
10. F6: 쿨다운 차단 확인
11. F10: MOP 충돌 감지 확인

### Phase C — 위임 자동실행 (Jino 위임 스위치 ON 후)
12. F14: 위임 ON → Ava agree → 자동 실행 확인
13. F15: 위임 OFF 유형 차단 확인

### Phase D — D+7 학습루프 (7일 후)
14. L8: proposal_scoreboard 채점 확인

---

## 판정 기준

- **PASS**: 라이브에서 기대 동작 확인, 증거(API 응답·DB 행·로그) 캡처
- **FAIL**: 기대와 다른 동작 → 즉시 원인 분석·수정·재검증
- **N/A**: 해당 시나리오가 자연 발생하지 않아 검증 불가 → 정직하게 "미검증" 기록, 억지 시뮬레이션 금지

## MOP 대비 현재 갭 정직 기록

| MOP 기능 | 우리 상태 | 갭 해소 시점 |
|---------|----------|------------|
| 시간단위 소재 조정(Pro 매시간) | ❌ 미구현 | X2 flight_loop |
| 당일 플라이트 루프 | ❌ 미구현 | X2 pacing_controller |
| 응답곡선(예산 민감도) | ❌ 미구현 | X2 response_curve_builder → G8 부산물 |
| 순위유지 진동 알고리즘 | ❌ 미구현 | §8 승계 큐 G4 |
| CPC Reboot(탐색 재학습) | ❌ 미보유 | §8 검토 대상 |
| 캠페인 자동 생성 | ❌ 미구현 (MOP도 중단+브랜드스토어 한정) | §8 승계 큐 G6 |
| DEA 경쟁 심화도 | ❌ 미구현 (진단보드와 목적 중복) | §8 승계 큐 후순위 |
| 기여도(Attribution) | ❌ 미구현 (전제 조건 부재) | §8 승계 큐 G7 후순위 |

---

> 이 체크리스트는 카나리 지정 시 §7과 함께 사용한다.
> 검증 완료 후 각 항목에 날짜·증거를 기록하고 "X1b 완료" 선언의 근거로 삼는다.
