# PLAN — 스프린트 IU: 장중 상향 개방 + 순위 제한 폐지 (D-NAO-66)

> 작성 2026-07-20 18:30 KST (Fable 구조). 구현=Opus·GATE 적대 리뷰 필수(행위 변경, 돈 경로).
> 근거: D-NAO-66(트랙, Jino 원문 인용 포함) + ref 34(대행사 사이클 실증) + D-NAO-59(총이익 극대화).

## §0 방향 고정 (변경 금지)

- **순위는 목표가 아니라 결과.** 상향/하향의 유일한 지배 게이트 = **target ROAS(BEP×공격성) 유지 여부**. Jino 원문: *"우리의 목표는 3등이 아니고 이익을 낼 수 있는 일정한 RoAS를 설정하고 최대한 매출을 많이 올리는 것"*.
- 사이클(대행사 실증과 동일, 단 자동): 올려보고 → 누적 ROAS ≥ target이면 또 올리고 → 깨지면 한 스텝 내리고(기존 고삐) → 회복하면 재상향. 오버슛 비용은 스텝 1개 분량으로 자연 캡.
- **불변 가드(폐지 아님)**: BEP 하한·target ROAS 게이트·일예산 불가침·킬스위치·쿨다운 2h(하루 ~8스텝 상한의 실체)·표본 최소치(imp/전환 tally)·CPC 급등 DOWN·RL3 loss 고삐 DOWN·DL 일일리셋·비대칭 기억(UP=관성, DOWN=일일리셋).
- **폐지 대상 = 순위 기반 제한만**: 과열밴드 DOWN(<2.5)·UP의 밴드하단(>4) 전제·learned band 천장(ROAS-driven UP 경로에서). 스팟 밴드·learned band는 **탐침(CD) 프라이어·관찰 지표로 강등**(하드 캡 금지).
- 스코프: 그룹입찰 자동 레인(auto_operate 캠페인). 소재입찰(ad)은 B 카나리 Confirm-only 유지 — IU가 ad 자동발사를 열지 않는다. 예산 변경은 L2 스코프 밖.

## §1 현재 구조의 문제 (실코드 기준)

`auto_operator._judge_hourly`:
1. UP이 `weighted_rank > 4`(밴드하단이탈)일 때만 검사됨 → 밴드 안(2.5~4)의 건강한 유닛은 **ROAS가 아무리 높아도 장중 상향 불가**(04 07-20 실사례: 17시 전환 2·추정 ROAS 8.3, target 1.8 — 상향 0회).
2. `weighted_rank < 2.5` = 무조건 DOWN(과열밴드) → **1~2등이 ROAS ≥ target이어도 강제 하향** = D-NAO-66 위반.
3. UP의 ROAS 근거가 정착창(D-8~D-2)뿐 — 장중 신호(RL2 ccnt tally) 미사용(RL3가 DOWN에만 사용).

## §2 페이즈

- **IU1 (Opus)**: 장중 tally UP 신호 + UP 분기 재구성.
  - 신규 게이트 `_intraday_up_ok`(순수): 오늘 hh24 곡선 누적 — ①직접전환 tally ≥ N(추천 2, 상수)  ②`estimated_intraday_roas ≥ target_roas × 여유계수`(추천 1.2 — 추정치 과신 방지) ③곡선 소진 표본 게이트(기존 `_MIN_HOURLY_SAMPLE_IMP` 재사용). price 미산출(원가 미확인 상품)은 발사 불가(fail-closed, RL3와 동일).
  - `_judge_hourly` UP 재구성: **순위 전제 제거** — `(_intraday_up_ok) OR (정착창 ROAS ≥ target)`이면 UP 검토. 페이싱 조건은 "예산 여력"(일예산 잔여 확인)으로 재정의 — 저속일 때만 올리는 게 아니라 **예산이 남아 있으면** 올린다(소진 임박이면 UP 불가 = 기존 가드레일 정합).
  - DOWN 우선순위 불변(CPC 급등·loss 고삐가 UP보다 먼저 = bleeding day UP 금지 유지).
- **IU2 (Opus)**: 순위 제한 철거.
  - 과열밴드 DOWN(<2.5) 삭제 — 상단 순위의 이상 지출은 CPC 급등 DOWN + loss 고삐가 담당(전환 없는 고지출 = est ROAS 0 < target → 고삐 발동 확인).
  - DL4 재시작 경로·growth 스윕의 learned band 천장: **ROAS-driven UP에서는 미참조**로 변경(탐침 CD2/CD5의 프라이어 용도는 유지 — `_learned_optimal_skip`은 탐침 전용임을 주석·테스트로 못 박기).
  - 일 레인 성장 스윕(shopping_group_bep/growth)의 순위 상한 참조가 있으면 동일 원칙 적용.
- **IU3 (Sonnet)**: 사유문 정합(순위 언급 → ROAS 근거로)·diary/retro 매핑 확인·계획서 체크.

## §3 검증 (원칙22 — 착수 전 못 박음)

1. pytest 전체 회귀 0 + 차등 테스트: ①밴드 안(rank 3) + tally 충족 → UP 생성(종전 hold와 차등) ②rank 2 + ROAS ≥ target → hold(종전 DOWN과 차등) ③rank 2 + est ROAS < BEP → 고삐 DOWN(안전망 생존) ④tally 미달/price 없음/예산 소진 → UP 불가 ⑤쿨다운 2h로 재상향 차단.
2. GATE: Opus 적대 리뷰(공격 각도: 추정 ROAS 과신 → 과상향 폭주 경로·쿨다운 우회·일예산 잔여 계산 구멍·과열 DOWN 삭제 후 상단 무전환 지출 시나리오에서 고삐 도달 보장·ad 자동발사 누출·비카나리 소재-미연결 유닛 hold 유지).
3. 배포(safe_deploy) 후 라이브: 다음 :20 레인 정상 완주 + 04류 유닛에서 UP 판정/생성 실측(조건 충족 시) + 상단 유닛 강제 DOWN 소멸 확인. 자연 발동 상설 관측.

## §4 체크리스트

- [x] IU1 구현 + 테스트 (`_intraday_up_ok` tally≥2·est≥target×1.2·imp≥30·price fail-closed / UP=(intraday OR settle) 순위 무관 / `_is_pacing_slow`→`_budget_headroom_ok`)
- [x] IU2 구현 + 테스트 (과열밴드 DOWN 삭제·general-UP learned band 천장 제거 — `_learned_optimal_skip`은 탐침 프라이어 전용 존치. 성장 스윕엔 순위 상한 없음 실측 확인 — bid_simulator rank@2는 D-NAO-19 효율캡이라 불변)
- [x] GATE PASS (Opus 적대 리뷰 — P1 0. 방어 3중 실증: 쿨다운 2h·일일캡 3(=일 최대 +52%)·BEP는 30일 정산 기준 + 서킷브레이커·일예산. P2 2건 권고)
- [x] GATE P2 반영 (P2-A-1 **정산 거부권**: 정착창 명시적 target 미달이면 intraday 근거 UP 금지 / P2-A-2 **장중-단독 UP 일 1스텝 캡**: 정산 판정불가 유닛은 +15%/일, `_executed_bid_ups_today` / P2-B **keyword S3 완전성 게이트**: 키워드 UP도 BEP·일예산 원료 fail-closed. 에이전트 중단분(테스트 1건)은 오케스트레이터가 마무리 — **2433 passed·회귀 0**)
- [x] IU3 정합 (사유문 "재시작 대기(ROAS 미달)"·diary 갱신. retro 스냅샷 매핑 확인은 배포 전 확인 항목으로 이월)
- [ ] 배포(safe_deploy auto_operator.py+naver_execution_harness.py) + 라이브: 다음 :20 레인 완주·04류 UP 판정 실측·상단 강제 DOWN 소멸 — **다음 세션**
- [ ] 자연 발동 관측(장중 UP 실집행·정산 거부권/1스텝 캡 발동·상단 유지 실측) — 상설
