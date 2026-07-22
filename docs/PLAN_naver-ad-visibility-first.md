# PLAN — 가시성 우선 스프린트 VF (D-NAO-83)

> 작성 2026-07-22 22:00 KST · 설계=Fable · 근거 전량 = `docs/references/38_visibility_first_analysis_20260722.md`(prod 실측)
> Jino 승인 원문: "가격을 낮추던 광고를 하던 누가 봐야 구매를 하던 클릭을 하던 할거 아니야. **노출이 기본이지**"

## §0. 방향 고정 (이 스프린트 동안 불변)

1. **유령 지면(순위>5) 스텝 금지** — "보이는 자리를 사거나, 아예 안 사거나". 학습가치 0 지면에서 소폭 상향 반복 금지.
2. **증거 구매 창** — 고수요·표본미달 그룹 한정, 밴드(≤4) 진입까지 허용. 예산 주 5만원/그룹·클릭 15개 확보 시 종료 → 통상 판정 복귀.
3. **콜드 상한 개혁** — 창 활성 그룹의 상한을 캠페인 90일 실측 RPC 기반 대체 산식으로 해방(순환논리 해소). 근거=ref 38 §2 전환단가 순위 무관 평평(8.1~9.4천원).
4. 스코프 밖: 밴드(2.5~4)·핫셋·vitality·PX·SS·예산 레버 불변. 대행사 캠페인은 auto_operate=False라 원천 무관. CTR 경보 skip·손실보드 skip은 창 활성과 무관하게 **선행 유지**(뮤패드 7그룹 보호).
5. 관측 전용 보조(VT3b 최소형): 유령∧창 비활성(경제성 사유) 그룹은 일 레인 브리핑에 관측 라인만 — 실쓰기 없음.

## §1. 구조 (Agent-Harness-SA)

```
hourly lane (auto_operator._run_exploration_for_campaign)   ← 기존 Harness, 배선만 추가
  ├── ctr_alert skip / loss-board skip                       (기존, 선행 유지)
  ├── visibility.py  ★신규 SA (순수 판단 — DB 읽기만)
  │     ├── classify_visibility(rank)  → in_band | visible | ghost | unknown
  │     ├── evidence_window(db, adgroup, campaign, today)
  │     │     활성 조건: 고수요(7일 노출≥100) ∧ 표본미달(정산창 clk<10)
  │     │               ∧ 7일 지출<50,000 ∧ 7일 클릭<15
  │     └── evidence_ceiling(db, campaign, bep_roas, current_bid)
  │           = min( affordable_ceiling(campaign_rpc_90d, bep_roas), current_bid×2.0 )
  │           campaign_rpc_90d 불가 시 → 기존 exploration_ceiling 폴백
  ├── exploration.ladder_judgment                            ← 수정: ghost_hold 신설
  │     rank>5 ∧ 창 비활성 → "ghost_hold" (스텝 없음)
  │     창 활성 → 기존 판정 그대로 (상한만 evidence_ceiling)
  └── exploration.adaptive_step / harness / writer           (불변 — 상한값은 마커로 흐름)
```

## §2. 파라미터 (전부 ref 38 실측 앵커, 추천안 확정)

| 상수 | 값 | 근거 |
|---|---|---|
| `_GHOST_RANK` | 5.0 | ref38 §1: 5위 밖 CTR 1/3~1/8 붕괴 (스텝 금지선) |
| `_VISIBILITY_BAND_TOP` | 4.0 | 가시 임계 = 기존 밴드 상단 재사용 |
| `_EVIDENCE_BUDGET_7D` | 50,000원/그룹 | ref38 §3: 15프로 실측 시세 주 3.4만 + 여유 (3~5만 상단) |
| `_EVIDENCE_CLICK_TARGET` | 15 | ref38 §5-2: 클릭 10~15 확보 시 종료 (상단) |
| `_EVIDENCE_MIN_DEMAND_IMP_7D` | 100 | 고수요 판별 — 유령 순위에서도 노출 존재(17프로 7일 217 통과), 무수요 그룹 배제 |
| `_CAMPAIGN_RPC_WINDOW_D` | 90 | ref38 §1·2 분석 창과 동일 |

- 기대 손실 상한: 창당 최악 5만원 × 클릭 15 → 기대 전환 ~1.7건(캠페인 CVR)·기대 전환단가 8~9천 = 15프로 복제, 도박 아님(ref38 §2 평평성).
- 창 상태는 **무테이블 파생**(naver_ad_daily 7일 집계) — change_log=쿨다운 저장소와 같은 하우스 스타일. 새 DB 스키마 없음 → 마이그레이션 없음.

## §3. Phase

- **VF1**: `backend/app/services/naver_ad/visibility.py` 신규 SA + 단위 테스트. 순수 판단·DB 읽기만·다른 SA 미임포트(exploration 순환 금지 — 상수는 visibility가 소유, exploration이 임포트).
- **VF2**: `exploration.py` — `ladder_judgment`에 `ghost_hold` verdict 신설(rank>_GHOST_RANK ∧ evidence_active=False, 기존 verdict 우선순위 유지: no-prior/클릭 발생/과열 판정보다 뒤·out-of-band 분기보다 앞), `exploration_ceiling`에 evidence 경로 추가(활성 시 evidence_ceiling, 불가 시 기존 폴백). 시그니처는 optional 파라미터(원칙18-8).
- **VF3**: `auto_operator._run_exploration_for_campaign` 배선 — 후보별 evidence_window 산출→judgment/ceiling 전달, 결과 카운터 `explored_ghost_hold` 추가, 일 레인 브리핑에 유령∧경제성 차단 그룹 관측 라인(VT3b 최소형, diary만·Slack은 기존 경보 채널 규칙 따름).
- **GATE**: 전체 pytest 회귀 0 + prod 데이터 시뮬(17프로: 창 활성·상한 해방·스텝 산출 확인 / 뮤패드: CTR skip 선행 유지 / 158개 플로어 그룹: VT4 동작 불변).
- **codex review** (원칙19) → 수정 → AGREE.
- **배포**: safe_deploy.sh + 재시작 + 라이브 검증(다음 시간당 레인 :20 크론 실행 로그·change_log·ghost_hold 카운터).

## §4. 완료 기준 (라이브 합격 시나리오 — 착수 전 고정, 원칙22)

1. pytest 전체 green·회귀 0.
2. prod 시뮬레이션: 17프로 그룹이 evidence 창 활성으로 판정되고 상한이 기존 경제성 상한(2,290 부근)을 넘어 산출된다.
3. 유령∧창 비활성 시나리오에서 스텝이 발생하지 않고 `ghost_hold`로 기록된다.
4. 배포 후 실제 시간당 레인 1회에서 예외 없이 완주하고 카운터가 로그에 나타난다.
5. 기존 보호 불변: CTR 경보 그룹 skip·가드레일(30%·3/3·쿨다운·50원 하한) 전부 기존 테스트 green.

## §5. 구현 라우팅

- VF1~VF3 구현 = **Opus** (새 SA + 다파일 배선 = 중요 코딩). 테스트 실행·수정 루프 = 에이전트 내에서 완결.
- 배포·git 기계 작업 = Sonnet. 판정 종합 = Fable.

## §6. 진행 기록 (2026-07-22 밤 — 완료·배포·라이브 합격)

- **VF1~VF3 구현 완료**: `backend/app/services/naver_ad/visibility.py` 신규(classify_visibility·evidence_window·evidence_ceiling, 전부 §2 상수 소유·순수 판단·DB 읽기만) + `exploration.py`(`ladder_judgment`에 `ghost_hold` verdict 신설·`exploration_ceiling`에 evidence 경로 추가, optional 파라미터) + `auto_operator._run_exploration_for_campaign` 배선(evidence_window 산출→judgment/ceiling 전달·`explored_ghost_hold` 카운터·일 레인 유령 관측 브리핑 diary 라인).
- **GATE**: pytest 전체 green(3008→3013 passed)·회귀 0. prod 시뮬 3종 확인(17프로 창 활성·상한 해방 / 뮤패드 CTR skip 선행 유지 / 158개 플로어 그룹 VT4 동작 불변).
- **codex review(원칙19)**: 1R P1 1건 — 첫 사이클(그룹 최초 판정 시점)에 유령 스텝이 누수되는 경계 케이스 지적 → 수용·수정(첫 사이클도 `ghost_hold` 경로에 포함) → **2R AGREE-ALL**(신규 지적 0).
- **배포**: safe_deploy.sh 22:23 KST(commit `0fdca1b`, visibility.py+exploration.py+auto_operator.py)·재시작·health 200.
- **라이브 검증 — §4 완료 기준 5개 항목 대조(23:20 KST 첫 신코드 시간당 레인)**:
  1. pytest 전체 green·회귀 0 — **충족**(3013 passed).
  2. 17프로 그룹 evidence 창 활성 판정 + 상한이 기존 경제성 상한(2,290 부근)을 넘어 산출 — **충족**(시뮬 검증: 상한 2,290→3,010 해방).
  3. 유령∧창 비활성 시나리오에서 스텝 미발생·`ghost_hold` 기록 — **충족**(라이브 실포착 2건: grp-…59830547 순위5.30·grp-…44743919 순위9.73, diary id 605·610).
  4. 배포 후 실제 시간당 레인 1회 예외 없이 완주 + 카운터가 로그에 나타남 — **부분 충족**: 레인 완주·예외 0은 확인. **카운터 로그 표시는 기존 갭으로 미확인** — `main.py` 로깅 설정 부재로 시간당 레인 완료 INFO 라인이 root logger(WARNING 이상만)에 걸려 전 기간(VF 이전부터) 0회 출력. diary 기록(605·610)으로 `ghost_hold` 발동 자체는 재구성·확인함(로그 카운터가 아니라 diary가 실질 증거). 백로그 chip 발행(task_9f4ea74c) — 코드 수정은 이번 스코프 밖.
  5. 기존 보호 불변(CTR 경보 skip·가드레일 30%·3/3·쿨다운·50원 하한) — **충족**(라이브에서 실쓰기 2건 동반 가드레일 차단 3건 확인, 회귀 테스트 green).
- **17프로 결말(같은 밤 관측)**: 21시대 클릭1·전환1(D-NAO-84 N배송 첫 주문과 동일 건) 발생 → 장중 순위 3.8~4.1 밴드 진입 → 래더가 `stop_observe`로 전이(클릭 발생=증거 도착·통상 판정 인계, 설계 의도 그대로). 22:20 시점 "capped"였다가 23:20 "stop_observe"로 보인 것은 결함이 아니라 **클릭 데이터 집계 지연**(기지 cadence) 때문 — 표적 시뮬로 레인 단계별 재구성해 확인(교훈: LESSONS_LEARNED 참조).
- **잔여 백로그**: 시간당 레인 INFO 로깅 설정(카운터 가시화) / `stop_observe`·step-capped 분기 diary 미기록(침묵 분기 관측 갭) — 둘 다 관찰성 개선, 판정 로직 변경 아님.
